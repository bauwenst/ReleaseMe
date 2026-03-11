#!/usr/bin/env python3


def _main():
    import argparse
    import os
    import re
    import sys
    import shutil
    import tomllib
    import subprocess
    from pathlib import Path
    from typing import Optional
    from dataclasses import dataclass

    print()  # Newline

    # Define arguments. They are only parsed after we print the current version.
    @dataclass
    class Args:
        version: str
        no_v: bool
        no_zeroes: bool
        backfill: bool
        runtime_variable_path: Optional[Path]
        runtime_variable_name: Optional[str]

    parser = argparse.ArgumentParser(description="ReleaseMe: Tool for pushing version(s) of a Python package to PyPI.")
    parser.add_argument("version", type=str, help="New version number.", default=None, nargs="?")
    parser.add_argument("--no_v", action="store_true", help="If this flag is given, numeric versions will not automatically be prefixed with 'v'.")
    parser.add_argument("--no_zeroes", action="store_true", help="If this flag is given, zeroes on the left of numbers in numeric versions will be stripped.")
    parser.add_argument("--backfill", action="store_true", help="If this flag is given, the tool will find the latest release, and then look into its past for commits that bumped the TOML's version, to tag them with version tags retroactively, as if the version bump had been done with ReleaseMe. PyPI will still order these retroactive versions correctly, despite showing today's date as the publishing date.")
    parser.add_argument("--runtime_variable_path", type=Path, help="Path to the file where the version is defined in a variable.")
    parser.add_argument("--runtime_variable_name", type=str, help="Name of the variable whose value should be set to the current version.", default="__version__")
    args: Args = parser.parse_args()  # You could do this later, but then --help is delayed until after some prints. We run as much as we can without arguments and then abort if the arguments are wrong.

    # Define version formatting based on args.
    class Version:
        def __init__(self, raw: str):
            self._raw = raw.strip()

        def is_numeric(self) -> bool:
            return re.match(r"^v?[0-9.]+$", self._raw) is not None and not re.search(r"\.\.", self._raw)

        def was_prefixed(self) -> bool:
            return self.is_numeric() and self._raw.startswith("v")

        def _split(self) -> list[str]:
            return self._raw.removeprefix("v").split(".") if self.is_numeric() else [self._raw]

        def to_numeric_tuple(self) -> tuple[int,...]:
            if not self.is_numeric():
                raise ValueError("Version must be numeric.")
            return tuple(int(p) for p in self._split())

        def to_original(self) -> str:
            return self._raw

        def to_formatted(self) -> str:
            if not self.is_numeric():
                return self._raw
            else:
                return "v"*(not args.no_v) + ".".join(str(part) for part in (self._split() if not args.no_zeroes else self.to_numeric_tuple()))

        def __eq__(self, other: "Version") -> bool:
            if self.is_numeric() != other.is_numeric():  # comparing a numeric with a non-numeric
                return False

            if self.is_numeric():
                return self.to_numeric_tuple() == other.to_numeric_tuple()
            else:
                return self.to_original() == other.to_original()

        def __hash__(self) -> int:
            if self.is_numeric():
                return hash(self.to_numeric_tuple())
            else:
                return hash(self.to_original())

        def __lt__(self, other: "Version") -> bool:
            if not self.is_numeric() or not other.is_numeric():
                raise ValueError("Versions must have numeric values to be ordered.")
            return self.to_numeric_tuple() < other.to_numeric_tuple()

    # Sanity check: are we even in a Python package tracked by Git?
    def exit():
        print()
        sys.exit(1)

    PATH_GIT  = Path(".git")
    PATH_TOML = Path("pyproject.toml")
    if not PATH_GIT.exists() or not PATH_TOML.exists():
        print("❌ This does not look like a Python project root (missing .git folder and/or pyproject.toml file).")
        exit()

    # Inspect the package for its name and version.
    # - The TOML definitely exists. Question is whether it is correctly formed.
    def parse_toml() -> dict:
        try:
            with open(PATH_TOML, "rb") as handle:
                return tomllib.load(handle)
        except:
            print("❌ Cannot parse TOML.")
            exit()
            raise  # Will never be reached, but needed to keep the type checker happy.

    def get_toml_name() -> str:
        try:
            return parse_toml()["project"]["name"]
        except:
            print("❌ Missing project name in TOML.")
            exit()
            raise  # See parse_toml()

    def get_toml_version() -> Optional[Version]:
        toml = parse_toml()
        try:
            return Version(toml["project"]["version"])
        except:
            try:
                if "version" in toml["project"]["dynamic"]:
                    return None
                else:
                    raise
            except:
                print("❌ Missing version in TOML.")
                exit()

    DISTRIBUTION_NAME = get_toml_name()
    print(f"✅ Identified distribution: {DISTRIBUTION_NAME}")

    # - And even with a project name, can we find the source code?
    def get_package_path() -> Path:
        with open(PATH_TOML, "rb") as handle:
            try:  # This is most specific and hence has precedent.
                package = Path(tomllib.load(handle)["tool.hatch.build.targets.wheel"]["packages"][0])
            except:
                # If there is a ./src/, it is always investigated.
                parent_of_package = Path("./src/")
                if not parent_of_package.is_dir():
                    parent_of_package = parent_of_package.parent

                # Now, if there is a folder here with the same name as the distribution, that has to be it.
                _, subfolders, _ = next(os.walk(parent_of_package))
                subfolders = [f for f in subfolders if not f.startswith(".") and not f.startswith("_") and not f.endswith(".egg-info")]

                if DISTRIBUTION_NAME in subfolders:
                    package = parent_of_package / DISTRIBUTION_NAME
                # Or, if there is only one subfolder, that's likely it.
                elif len(subfolders) == 1:
                    package = parent_of_package / subfolders[0]
                else:
                    print("❌ Could not find package name.")
                    exit()

        # Verify that this folder contains an __init__.py as a sanity check that it is actually a Python module.
        if not (package / "__init__.py").is_file():
            print(f"❌ Missing __init__.py in supposed package root {package.as_posix()}!")
            exit()

        return package

    def get_package_name() -> str:
        return get_package_path().name

    PACKAGE_NAME = get_package_name()
    print(f"✅ Identified package: {PACKAGE_NAME}")

    # - So we have a Git repo that is a Python package with proper TOML. Make the ReleaseMe workflow.
    def run(*tokens: str, extra_environment_variables: dict[str,str]=None, silence_output: bool=False):  # check=True means non-zero return codes raise an error.
        subprocess.run(tokens, check=True, env=None if not extra_environment_variables else os.environ | extra_environment_variables,
                       stdout=subprocess.DEVNULL if silence_output else None, stderr=subprocess.DEVNULL if silence_output else None)

    def run_with_output(*tokens: str, silence_errors: bool=False) -> str:
        return subprocess.check_output(tokens, text=True, stderr=subprocess.DEVNULL if silence_errors else None).strip()

    def user_says_yes(question: str, default_no: bool=True) -> bool:
        if default_no:  # For all inputs except explicitly yes, return False.
            return input(question + " (y/[n]) ").lower() == "y"
        else:  # For all inputs except literally no, return True.
            return input(question + " ([y]/n) ").lower() != "n"

    WORKFLOW_VERSION_LATEST = "2.1"  # This can change
    WORKFLOW_NAME           = "git-tag_to_pypi.yml"  # This cannot
    PATH_WORKFLOW = Path(".github/workflows/") / WORKFLOW_NAME

    def get_workflow_version() -> str:
        if not PATH_WORKFLOW.is_file():
            return "0"
        with open(PATH_WORKFLOW, "r", encoding="utf-8") as handle:
            first_line = handle.readline().strip()
            if not first_line.startswith("# version: "):
                return "1"
            else:
                return first_line.removeprefix("# version: ")

    if get_workflow_version() != WORKFLOW_VERSION_LATEST:
        workflow_created = not PATH_WORKFLOW.is_file()
        print(f"⚠️ GitHub Actions workflow {'does not exist yet' if workflow_created else 'is outdated'}.")

        # git diff --cached only diffs what has been added already with git add. Exit code is 1 if anything is found.
        try:
            run("git", "diff", "--cached", "--quiet")
        except:
            print("❌ Found staged changes. Please commit them before continuing.")
            exit()

        if not user_says_yes(f"   Please confirm that you want ReleaseMe to push a new commit to fix this.", default_no=False):
            print(f"❌ User abort.")
            exit()

        # Copy from the package into the cwd. (Note that the workflow does not have to be edited since the build process sends the distribution name to PyPI and this name is then compared to the publishers linked to your API token.)
        PATH_WORKFLOW.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(__file__).parent / WORKFLOW_NAME, PATH_WORKFLOW)

        # Commit
        run("git", "add", PATH_WORKFLOW.as_posix())
        run("git", "commit", "-m", f"ReleaseMe: {'Created' if workflow_created else 'Updated'} GitHub Actions workflow for PyPI publishing.")
        run("git", "push", silence_output=True)

    # - Can we find the old and new tags?
    def get_last_version_tag() -> Optional[str]:
        """Note: this does NOT use the TOML. It looks for a Git tag because we want to know which commits have been done."""
        try:
            return run_with_output("git", "describe", "--tags", "--abbrev=0", silence_errors=True)  # stderr is rerouted because otherwise you will get a "fatal: ..." message for the first version.
        except subprocess.CalledProcessError:
            return None

    # - Is there a precedent, either as a Git tag or in the TOML?
    toml_version = get_toml_version()  # This is what is used for (1) enforcing that the new tag is at least as large (if it is numeric) and (2) enforcing a 'v' prefix.
    print(f"✅ Identified TOML version: {toml_version.to_original()}")

    do_backfill = args.backfill
    if not do_backfill and args.version is None:  # This will only be an issue if later we can't find a previous release.
        # parser.error("You need to specify a new version.")
        pass
    elif do_backfill and args.version is not None:
        parser.error("In backfill mode, specifying a version is useless.")

    # Summarise the commits since the last tag.
    def generate_release_notes(from_ref: Optional[str], to_ref: Optional[str]) -> str:
        """
        :param from_ref: EXCLUSIVE lower bound.
        """
        if not from_ref and not to_ref:
            range_spec = "--all"
        elif not from_ref:
            range_spec = to_ref
        else:
            to_ref = to_ref or ""
            range_spec = f"{from_ref}..{to_ref}"

        sep = "<<END>>"
        log = run_with_output("git", "log", range_spec, f"--pretty=format:%B{sep}")
        if not log:
            return ""

        commit_titles = [s.strip().split("\n")[0] for s in log.split(sep)]
        commit_titles.reverse()
        return "".join("- " + title + "\n"
                       for title in commit_titles if title)

    def quote(s: str) -> str:
        return "\n".join("   | " + line for line in [""] + s.strip().split("\n") + [""])

    def find_toml_releases(backwards: bool) -> list[Version]:
        """
        Finds all Git tags which match the pyproject.toml, and then:
            - If backwards is true: looks at all prior versions of the TOML, checks for a consistent order, and then
              offers to publish the ones that were not published as a release (i.e. the ones that weren't tagged).
            - If backwards is false: looks at all versions of the TOML since the latest release and does the same thing.

        :return: list of all past releases.
        """
        ordered_commits_all = [c for c in run_with_output("git", "log", "--format=%H").split("\n") if c]
        ordered_commits_all.reverse()

        # Get existing tags (this includes tags that are not version changes)
        c2t: dict[str,Version] = {run_with_output("git", "rev-list", "-1", t): Version(t)
                                  for t in [t for t in run_with_output("git", "tag", "-l").split("\n") if t]}  # Commits to tags

        # Get commits with version changes (this includes ReleaseMe tags)
        pattern = re.compile(r"commit ([a-f0-9]+)")
        subpattern = re.compile(r"[a-f0-9]+")

        current_commit = None
        c2v: dict[str,Version] = dict()  # Commits to TOML versions
        for thing in pattern.split(run_with_output("git", "log", "-p", "--", "pyproject.toml")):
            if subpattern.match(thing):
                current_commit = thing
                continue
            else:
                pattern = re.compile(r'''\n\+version\s*=\s*"(.+?)"''')  # regex pattern for
                match = pattern.search(thing)
                if match:
                    assert current_commit is not None
                    c2v[current_commit] = Version(match.group(1))

        ordered_commits_versioned = [c for c in ordered_commits_all if c in c2v]
        ordered_commits_releases  = [c for c in ordered_commits_all if c in c2t and c in c2v and c2t[c] == c2v[c]]
        set_of_releases   = set(c2v[c] for c in ordered_commits_releases)  # Not the same as the intersection of tags and versions (and also, having a release version doesn't make you a release necessarily, but then we would have to check PyPI).

        last_release_index = ordered_commits_versioned.index(ordered_commits_releases[-1]) if ordered_commits_releases else None
        if last_release_index is None:  # Retroactive releases require at least one release.
            if backwards:
                print(f"❌ No latest release found to look back from, so no backfilling needed!")
                return []
            print("⚠️ No latest release found. Looking for version updates from start to present.")
        else:
            print(f"✅ Latest release was {c2v[ordered_commits_versioned[last_release_index]].to_original()}. Looking {'back' if backwards else 'ahead'} from that.")
        ordered_commits_versioned = ordered_commits_versioned[:last_release_index] if backwards else ordered_commits_versioned[last_release_index or 0:]

        # Forget all TOML updates which are:
        #   1. an alias of official or unofficial version names OR
        #   2. already tagged (but incorrectly, i.e. different from the TOML) OR
        #   3. numerically not in between their surrounding releases OR
        #   4. out of order within the unofficial versions.
        commits_to_ignore = []
        versions_to_add = set()  # This is only a temporary set to track which version names have been used already. The actual updates require knowing ranges of commits, not just a version name.

        current_upper_index = 0 if backwards else len(ordered_commits_releases) - 1 if ordered_commits_releases else 0
        predecessor_commit = None
        for candidate_commit in ordered_commits_versioned:
            candidate_version = c2v[candidate_commit]

            if candidate_commit in ordered_commits_releases:  # This is an actual release.
                current_upper_index += 1
                predecessor_commit = candidate_commit
                continue

            # Test 1: Aliasing an existing release.
            if candidate_version in set_of_releases:
                commits_to_ignore.append(candidate_commit)
                continue
            elif candidate_version in versions_to_add:
                commits_to_ignore.append(candidate_commit)
                continue

            # Test 2: Already tagged.
            if candidate_commit in c2t:
                assert c2v[candidate_commit] != c2t[candidate_commit]
                commits_to_ignore.append(candidate_commit)
                continue

            # Test 3a: Lower than the previous release.
            if current_upper_index > 0:
                lower_version = c2v[ordered_commits_releases[current_upper_index - 1]]
                if candidate_version < lower_version:  # TODO: Don't do this check if the version is not numeric.
                    commits_to_ignore.append(candidate_commit)
                    continue

            # Test 3b: Higher than the next release.
            if current_upper_index < len(ordered_commits_releases):
                upper_version = c2v[ordered_commits_releases[current_upper_index]]
                if upper_version < candidate_version:
                    commits_to_ignore.append(candidate_commit)
                    continue

            # Test 4: Lower than the preceding version (official or unofficial release).
            if predecessor_commit is not None:
                if candidate_version < c2v[predecessor_commit]:
                    commits_to_ignore.append(candidate_commit)
                    continue

            predecessor_commit = candidate_commit
            versions_to_add.add(candidate_version)

        ### Debug prints:
        # print("TOML versions that will be released:", sorted(versions_to_add))
        # print("TOML versions ignored for various reasons:", sorted(map(c2v.get, commits_to_ignore)))
        # print("Tags ignored due to lack of matching TOML version:", sorted(set(c2t.values()) - versions_to_add - set_of_releases))
        ###
        ordered_commits_versioned = [c for c in ordered_commits_versioned if c not in commits_to_ignore]

        # Now that we know all commits with a valid version change, pair them up in commit ranges, but only keep the ranges that end in a non-existing release.
        update_ranges: list[tuple[str,Version,str,Version]] = []

        if backwards or last_release_index is None:
            ordered_commits_versioned = [""] + ordered_commits_versioned

        for start_commit, end_commit in zip(ordered_commits_versioned[:-1], ordered_commits_versioned[1:]):
            start_version = c2v[start_commit] if start_commit else None
            end_version   = c2v[end_commit]
            if end_version not in set_of_releases:
                update_ranges.append((start_commit, start_version, end_commit, end_version))

        # If any ranges are found, these should be released.
        if update_ranges:
            print("⚠️ Found unofficial version updates retroactively:")
            print(quote('\n'.join([f"{start.to_formatted() if start else '(start)'} -> {end.to_formatted()}" for _, start, _, end in update_ranges])))
            new_versions = [end for _, start, _, end in update_ranges]

            if backwards or user_says_yes("   Would you like to release these separately first?", default_no=last_release_index is not None):
                if user_says_yes("   Would you like to check their release notes?", default_no=False):
                    for start_commit, _, end_commit, version in update_ranges:
                        notes = generate_release_notes(start_commit, end_commit)
                        print(f"✅ Generated release notes for {version.to_formatted()}:")
                        print(quote(notes))

                if user_says_yes(f"⚠️ Please confirm that you want to release the following version(s):\n    📦 Package: {PACKAGE_NAME}\n    ⏳ Version(s): {', '.join(v.to_formatted() for v in new_versions)}\n    🌐 PyPI: {DISTRIBUTION_NAME}\n", default_no=True):
                    for start_commit, _, end_commit, version in update_ranges:
                        version_name = version.to_formatted()
                        notes = generate_release_notes(start_commit, end_commit)  # yeah yeah double work boohoo CPU

                        # You can successfully push older releases to PyPI, but there's a catch.
                        #   - Yes, GitHub's CI/CD is able to run on an existing, older commit.
                        #   - BUT, at that older commit, the workflow has to exit already IF you want to use 'git push'.
                        #     Otherwise, you will need to manually trigger the current version of the workflow using 'gh workflow' and this requires a dependency.
                        try:
                            run("git", "cat-file", "-e", f"{end_commit}:{PATH_WORKFLOW.as_posix()}", silence_output=True)
                            workflow_exists_at_end_commit = True
                        except:
                            workflow_exists_at_end_commit = False
                            try:
                                run("gh", "--version", silence_output=True)
                            except:
                                print("❌ You need GitHub CLI (the 'gh' command) to be able to release commits that existed before the workflow YAML.")
                                print("   See https://cli.github.com/ for instructions.")
                                exit()
                            try:
                                run("gh", "auth", "status", silence_output=True)
                            except:
                                print("❌ You still need to authenticate yourself to GitHub CLI using 'gh auth login'.")
                                exit()

                        # Within Git, the below "committer date" works to pretend the tag was there at the time of the commit.
                        #   - PyPI registers the time of release rather than the (fake) time of the tag, but interestingly,
                        #     it does not order releases chronologically. So the order is as you'd desire despite the date being "wrong".
                        #     Either it's ordering along Git chronology or simply along version name sorting order.
                        committer_date = run_with_output("git", "show", "--format=%aD", end_commit).split("\n")[0].strip()  # You can normally use a shell pipe like "git show blablabla | head -1" but the subprocess package doesn't use a shell.
                        run("git", "tag", "-a", version_name, "-m", f"Release {version_name}\n\n{notes}", end_commit, extra_environment_variables={"GIT_COMMITTER_DATE": committer_date})  # https://stackoverflow.com/a/21741848
                        run("git", "push", "origin", version_name, silence_output=True)
                        if not workflow_exists_at_end_commit:  # This means the git push wasn't enough to run it yet.
                            run("gh", "workflow", "run", WORKFLOW_NAME, "-f", f"tag={version_name}")
                        print(f"✅ Tagged and pushed version {version_name} retroactively with release notes.")

                    return [update_ranges[-1][-1]]  # NOTE: In the case that you are in backwards mode, you don't really care about the releases anyway. Just that there exists at least 1 now is enough.

        return [c2v[c] for c in ordered_commits_releases]

    releases = find_toml_releases(do_backfill)
    latest_release_tag = releases[-1] if releases else None

    # Now comes everything after retroactive versioning.
    def end():
        print()

    if not do_backfill:
        # First generate release notes because potentially there are no changes to even give a name.
        notes = generate_release_notes(latest_release_tag.to_original() if latest_release_tag else None, None)
        if not notes:
            if latest_release_tag is None:  # => There is no commit text since the start of the repo.
                print(f"❌ This repository has no commits yet!")
            else:
                print(f"❌ No new commits were made since the last release ({latest_release_tag.to_original()})!")
            exit()

        # Format new version name.
        version_for_format = latest_release_tag or toml_version  # Very rarely, the TOML version is None.
        if args.version is None and (version_for_format is None or not version_for_format.is_numeric()):
            print("❌ No new version name was provided, and could not deduce one automatically.")
            exit()

        if version_for_format is not None:
            # Impute new version if necessary
            if args.version is None:  # We know the version to format must at least be numeric then.
                version_for_format_tuplified = version_for_format.to_numeric_tuple()
                new_tag = Version("v" * (version_for_format.was_prefixed() and not args.no_v) + ".".join(map(str, version_for_format_tuplified[:-1] + (version_for_format_tuplified[-1] + 1,))))
            else:
                new_tag = Version(args.version.strip())

            # Ensure consistent "v" prefix.
            if version_for_format.is_numeric() and new_tag.is_numeric():
                if args.no_v and args.version is not None and new_tag.was_prefixed():
                    print(f"❌ Requested new version name '{args.version.strip()}' starts with 'v' yet the option --no_v was provided.")
                    exit()

                if args.no_v and version_for_format.was_prefixed():
                    print(f"⚠️ Requested new version name to not have a 'v' prefix, yet a name was found with it ({version_for_format.to_original()}). Maybe this is undesired.")
                elif not args.no_v and not version_for_format.was_prefixed():
                    print(f"⚠️ Requested new version name to have a 'v' prefix, yet a name was found without it ({version_for_format.to_original()}). Maybe this is undesired.")

            if args.version is None:
                print(f"⚠️ No new version name was provided, so it was assumed to be {new_tag.to_formatted()}.")
        else:  # If no information is known about versioning policies before this run, we assume the user wants a 'v' prefix for numeric versions.
            new_tag = Version(args.version.strip())

        # Check that new version is higher than most recent version.
        if latest_release_tag and latest_release_tag.is_numeric() and new_tag.is_numeric() and new_tag < latest_release_tag:
            print(f"❌ Cannot use new release name {new_tag.to_formatted()} since it is lower than (or equal to) the current release {latest_release_tag.to_original()}!")
            exit()

        # Now print release notes (after prints about the version name).
        print(f"✅ Generated release notes since {latest_release_tag.to_original() if latest_release_tag else 'initial commit'}:")
        print(quote(notes))

        # Update all mentions of the version in the project files.
        def update_pyproject(version_name: str):
            content = PATH_TOML.read_text()
            new_content = re.sub(r"""version\s*=\s*["'][0-9a-zA-Z.\-+]+["']""", f'version = "{version_name}"', content)
            PATH_TOML.write_text(new_content)
            print(f"✅ Updated pyproject.toml to version {version_name}")

        PATH_VARIABLE = args.runtime_variable_path or get_package_path() / "__init__.py"
        def update_variable(version_name: str):
            if not PATH_VARIABLE.exists():
                print(f"⚠️ {PATH_VARIABLE.name} not found; skipping {args.runtime_variable_name} update")
                return
            content = PATH_VARIABLE.read_text()
            new_content = re.sub(re.escape(args.runtime_variable_name) + r"""\s*=\s*["'][0-9a-zA-Z.\-+]+["']""",
                                 f'{args.runtime_variable_name} = "{version_name}"', content)
            PATH_VARIABLE.write_text(new_content)
            print(f"✅ Updated {PATH_VARIABLE.name} to version {version_name}")

        # Save changes with Git.
        def git_commit_tag_push(version_name: str, notes: str):
            try:
                print("="*50)
                run("git", "add", "pyproject.toml", PATH_VARIABLE.as_posix())  #, stderr=subprocess.STDOUT)
                run("git", "commit", "-m", f"🔖 Release {version_name}\n\n{notes}")
                run("git", "tag", "-a", version_name, "-m", f"Release {version_name}\n\n{notes}")
                run("git", "push",                         silence_output=True)
                run("git", "push", "origin", version_name, silence_output=True)
                print("="*50)
            except:
                print(f"❌ Failed to save to Git.")
                raise
            print(f"✅ Committed, tagged, and pushed version {version_name} with release notes.")

        new_tag_formatted = new_tag.to_formatted()
        if not user_says_yes(f"⚠️ Please confirm that you want to release the above details as follows:\n    📦 Package: {PACKAGE_NAME}\n    ⏳ Version: {new_tag_formatted}\n    🌐 PyPI: {DISTRIBUTION_NAME}\n", default_no=True):
            print(f"❌ User abort.")
            exit()

        update_pyproject(new_tag_formatted)
        update_variable(new_tag_formatted)
        git_commit_tag_push(new_tag_formatted, notes)
        end()
    else:
        if not latest_release_tag:
            print("   Rerun the tool without --backfill if you wanted to publish historical TOML updates as releases.")
            exit()
        else:
            print("✅ Rerun the tool without --backfill to release commits made since the last release.")
            end()


if __name__ == "__main__":  # Run from command line.
    _main()
