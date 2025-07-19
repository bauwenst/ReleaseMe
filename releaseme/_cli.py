#!/usr/bin/env python3


UNSAFE = False


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

    print()  # Newline

    # Define arguments. They are only parsed after we print the current version.
    parser = argparse.ArgumentParser(description="Push a new tagged version of a Python package.")
    parser.add_argument("version", type=str, help="New version number.", default=None, nargs="?")
    parser.add_argument("--retro", action="store_true", help="If this flag is given, the tool will instead look for commits that bumped the TOML's version before the last release, and tag them with version tags retroactively, as if the version bump had been done with ReleaseMe.")
    parser.add_argument("--runtime_variable_path", type=Path, help="Path to the file where the version is defined in a variable.")
    parser.add_argument("--runtime_variable_name", type=str, help="Name of the variable whose value should be set to the current version.", default="__version__")

    # Sanity check: are we even in a Python package tracked by Git?
    PATH_GIT  = Path(".git")
    PATH_TOML = Path("pyproject.toml")
    if not PATH_GIT.exists() or not PATH_TOML.exists():
        print("❌ This does not look like a Python project root (missing .git folder and/or pyproject.toml file).")
        sys.exit(1)

    # Inspect the package for its name and version.
    # - The TOML definitely exists. Question is whether it is correctly formed.
    def parse_toml() -> dict:
        try:
            with open(PATH_TOML, "rb") as handle:
                return tomllib.load(handle)
        except:
            print("❌ Cannot parse TOML.")
            sys.exit(1)

    def get_toml_name() -> str:
        try:
            return parse_toml()["project"]["name"]
        except:
            print("❌ Missing project name in TOML.")
            sys.exit(1)

    def get_toml_version() -> Optional[str]:
        toml = parse_toml()
        try:
            return toml["project"]["version"]
        except:
            try:
                if "version" in toml["project"]["dynamic"]:
                    return None
                else:
                    raise
            except:
                print("❌ Missing version in TOML.")
                sys.exit(1)

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
                    sys.exit(1)

        # Verify that this folder contains an __init__.py as a sanity check that it is actually a Python module.
        if not (package / "__init__.py").is_file():
            print(f"❌ Missing __init__.py in supposed package root {package.as_posix()}!")
            sys.exit(1)

        return package

    def get_package_name() -> str:
        return get_package_path().name

    PACKAGE_NAME = get_package_name()
    print(f"✅ Identified package: {PACKAGE_NAME}")

    # - So we have a Git repo that is a Python package with proper TOML. Make the ReleaseMe workflow.
    WORKFLOW_NAME = "git-tag_to_pypi.yml"
    PATH_WORKFLOW = Path(".github/workflows/") / WORKFLOW_NAME
    if not PATH_WORKFLOW.is_file():
        print("⚠️ GitHub workflow does not exist yet.")

        # git diff --cached only diffs what has been added already with git add. Exit code is 1 if anything is found.
        try:
            subprocess.run(["git", "diff", "--cached", "--quiet"], check=True)
        except:
            print("❌ Found staged changes. Please commit them before continuing.")
            sys.exit(1)

        if input(f"  Please confirm that you want to commit this workflow now. ([y]/n) ").lower() == "n":
            print(f"❌ User abort.")
            sys.exit(1)

        # Copy from the package into the cwd. (Note that the workflow does not have to be edited since the build process sends the distribution name to PyPI and this name is then compared to the publishers linked to your API token.)
        PATH_WORKFLOW.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(__file__).parent / WORKFLOW_NAME, PATH_WORKFLOW)

        # Commit
        subprocess.run(["git", "add", PATH_WORKFLOW.as_posix()], check=True)
        subprocess.run(["git", "commit", "-m", "ReleaseMe GitHub Actions workflow for PyPI publishing."], check=True)

    # - Can we find the old and new tags?
    def get_last_version_tag() -> Optional[str]:
        """Note: this does NOT use the TOML. It looks for a Git tag because we want to know which commits have been done."""
        try:
            return subprocess.check_output(["git", "describe", "--tags", "--abbrev=0"], text=True, stderr=subprocess.DEVNULL).strip()  # stderr is rerouted because otherwise you will get a "fatal: ..." message for the first version.
        except subprocess.CalledProcessError:
            return None

    def is_numeric_version_tag(version: str) -> bool:
        return re.match(r"^v?[0-9.]+$", version) is not None and not re.search(r"\.\.", version)

    def is_version_lower(v1: str, v2: str):
        return tuple(int(p) for p in v1.removeprefix("v").split(".")) <= tuple(int(p) for p in v2.removeprefix("v").split("."))

    # - Is there a precedent, either as a Git tag or in the TOML?
    toml_version = get_toml_version()  # This is what is used for (1) enforcing that the new tag is at least as large (if it is numeric) and (2) enforcing a 'v' prefix.
    print(f"✅ Identified TOML version: {toml_version}")

    args = parser.parse_args()
    retro = args.retro
    if not retro and args.version is None:
        parser.error("You need to specify a new version.")
    elif retro and args.version is not None:
        parser.error("In retroactive mode, specifying a version is useless.")

    # Summarise the commits since the last tag.
    def generate_release_notes(from_tag: str, to_tag: str) -> str:
        """:param from_tag: Exclusive lower bound."""
        if not from_tag and not to_tag:
            range_spec = "--all"
        elif not from_tag:
            range_spec = to_tag
        else:
            to_tag = to_tag or ""
            range_spec = f"{from_tag}..{to_tag}"

        sep = "<<END>>"
        log = subprocess.check_output(["git", "log", range_spec, f"--pretty=format:%B{sep}"], text=True).strip()
        if not log:
            return ""

        commit_titles = [s.strip().split("\n")[0] for s in log.split(sep)]
        commit_titles.reverse()
        return "".join("- " + title + "\n"
                       for title in commit_titles if title)

    def quote(s: str) -> str:
        return "\n".join("   | " + line for line in [""] + s.strip().split("\n") + [""])

    ###########################################################################################################
    # The below is handled once all retroactive stuff has been handled.

    def retroactive_tagging() -> Optional[str]:
        """
        Finds the latest release, and then:
            - If retro is true: looks at all prior versions of the TOML, checks for a consistent order, and then
              offers to publish the ones that were not published as a release (i.e. the ones that weren't tagged).
            - If retro is false: looks at all versions of the TOML since the latest release and does the same thing.

        Returns the version name of the latest release, which may be one that is published by this function itself.
        """
        ordered_commits_all = [c for c in subprocess.check_output(["git", "log", "--format=%H"], text=True).split("\n") if c]
        ordered_commits_all.reverse()

        # Get existing tags (this includes tags that are not version changes)
        c2t = {subprocess.check_output(["git", "rev-list", "-1", t], text=True).strip(): t
               for t in [t for t in subprocess.check_output(["git", "tag", "-l"], text=True).split("\n") if t]}  # Commits to tags

        # Get commits with version changes (this includes ReleaseMe tags)
        pattern = re.compile(r"commit ([a-f0-9]+)")
        subpattern = re.compile(r"[a-f0-9]+")

        current_commit = None
        c2v = dict()  # Commits to versions
        for thing in pattern.split(subprocess.check_output(["git", "log", "-p", "--", "pyproject.toml"], text=True)):
            if subpattern.match(thing):
                current_commit = thing
                continue
            else:
                pattern = re.compile(r'''\n\+version\s*=\s*"(.+?)"''')
                match = pattern.search(thing)
                if match:
                    assert current_commit is not None
                    c2v[current_commit] = match.group(1)

        ordered_commits_versioned = [c for c in ordered_commits_all if c in c2v]
        ordered_commits_releases  = [c for c in ordered_commits_all if c in c2t and c in c2v and c2t[c] == c2v[c]]
        set_of_releases   = set(c2v[c] for c in ordered_commits_releases)  # Not the same as the intersection of tags and versions, and also, having a release version doesn't make you a release necessarily.

        last_release_index = ordered_commits_versioned.index(ordered_commits_releases[-1]) if ordered_commits_releases else None
        if last_release_index is None:  # Retroactive releases require at least one release.
            if retro:
                return ""
            print("⚠️ No latest release found. Looking for version updates from start to present.")
        else:
            print(f"✅ Latest release was {c2v[ordered_commits_versioned[last_release_index]]}. Looking {'back' if retro else 'ahead'} from that.")
        ordered_commits_versioned = ordered_commits_versioned[:last_release_index] if retro else ordered_commits_versioned[last_release_index or 0:]

        # Forget all TOML updates which are:
        #   1. an alias of official or unofficial version names OR
        #   2. already tagged (but incorrectly, i.e. different from the TOML) OR
        #   3. numerically not in between their surrounding releases OR
        #   4. out of order within the unofficial versions.
        commits_to_ignore = []
        versions_to_add = set()  # This is only a temporary set to track which version names have been used already. The actual updates require knowing ranges of commits, not just a version name.

        current_upper_index = 0 if retro else len(ordered_commits_releases)-1 if ordered_commits_releases else 0
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
                if is_version_lower(candidate_version, lower_version):  # TODO: Don't do this check if the version is not numeric.
                    commits_to_ignore.append(candidate_commit)
                    continue

            # Test 3b: Higher than the next release.
            if current_upper_index < len(ordered_commits_releases):
                upper_version = c2v[ordered_commits_releases[current_upper_index]]
                if is_version_lower(upper_version, candidate_version):
                    commits_to_ignore.append(candidate_commit)
                    continue

            # Test 4: Lower than the preceding version (official or unofficial release).
            if predecessor_commit is not None:
                if is_version_lower(candidate_version, c2v[predecessor_commit]):
                    commits_to_ignore.append(candidate_commit)
                    continue

            predecessor_commit = candidate_commit
            versions_to_add.add(candidate_version)

        ordered_commits_versioned = [c for c in ordered_commits_versioned if c not in commits_to_ignore]

        # Debug prints:
        # print("TOML versions that will be released:", sorted(versions_to_add))
        # print("TOML versions ignored for various reasons:", sorted(map(c2v.get, commits_to_ignore)))
        # print("Tags ignored due to lack of matching TOML version:", sorted(set(c2t.values()) - versions_to_add - set_of_releases))

        # Now that we know all commits with a valid version change, pair them up in commit ranges, but only keep the ranges that end in a non-existing release.
        update_ranges = []

        if retro or last_release_index is None:
            ordered_commits_versioned = [""] + ordered_commits_versioned

        for start_commit, end_commit in zip(ordered_commits_versioned[:-1], ordered_commits_versioned[1:]):
            start_version = c2v[start_commit] if start_commit else ""
            end_version   = c2v[end_commit]
            if is_numeric_version_tag(end_version) and not end_version.startswith("v"):
                end_version = "v" + end_version

            if end_version not in set_of_releases:
                update_ranges.append((start_commit, start_version, end_commit, end_version))

        # If any ranges are found, these should be released.
        if update_ranges:
            print("⚠️ Found unofficial version updates retroactively:")
            print(quote('\n'.join([f"{start} -> {end}" for _, start, _, end in update_ranges])))
            new_versions = [end for _, start, _, end in update_ranges]

            if input("   Would you like to check their generated release notes first? ([y]/n) ").lower() != "n":
                for start_commit, _, end_commit, version in update_ranges:
                    notes = generate_release_notes(start_commit, end_commit)
                    print(f"✅ Generated release notes for {version}:")
                    print(quote(notes))

            if input(f"⚠️ Please confirm that you want to release the following version(s):\n    📦 Package: {PACKAGE_NAME}\n    ⏳ Version(s): {', '.join(new_versions)}\n    🌐 PyPI: {DISTRIBUTION_NAME}\n([y]/n) ").lower() != "n":
                for start_commit, _, end_commit, version in update_ranges:
                    notes = generate_release_notes(start_commit, end_commit)  # yeah yeah double work boohoo CPU
                    if UNSAFE:
                        # TODO: In commit 2ed1e1c12, I changed the TOML to v1.1.9 manually on June 16/17. That means you should run the following
                        #       commands a couple days afterwards:
                        #           git checkout 2ed1e1c
                        #           GIT_COMMITTER_DATE="$(git show --format=%aD | head -1)" git tag -a "v1.1.9" -m "Testing version injection."
                        #           git push origin v1.1.9
                        #  On GitHub, go to that commit. Check what CI/CD does. Then check what happens on PyPI. Is 1.9 above 1.10?
                        subprocess.run(['''GIT_COMMITTER_DATE="$(git show --format=%aD | head -1)"''', "git", "tag", "-a", f"{version}", "-m", f"Release {version}\n\n{notes}"], check=True)  # https://stackoverflow.com/a/21741848
                        subprocess.run(["git", "push", "origin", f"{version}"], check=True)
                    print(f"✅ Tagged and pushed version {version} retroactively with release notes.")

                return update_ranges[-1][-1]

        return c2v[ordered_commits_releases[-1]] if ordered_commits_releases else ""

    latest_release_tag = retroactive_tagging()
    if not retro:
        new_tag = args.version.strip()

        # Format new version.
        version_for_format = latest_release_tag or toml_version  # Very rarely, the TOML version is None.
        if version_for_format is not None:
            if is_numeric_version_tag(version_for_format) and is_numeric_version_tag(new_tag):  # These checks are immune to a 'v' prefix.
                if version_for_format.startswith("v") and not new_tag.startswith("v"):
                    new_tag = "v" + new_tag
                elif not version_for_format.startswith("v") and new_tag.startswith("v"):
                    print(f"⚠️ New version ({new_tag}) starts with 'v' unlike an existing version name ({version_for_format}). Maybe this is undesired.")
        else:  # If no information is known about versioning policies before this run, we assume the user wants a 'v' prefix for numeric versions.
            if is_numeric_version_tag(new_tag) and not new_tag.startswith("v"):
                new_tag = "v" + new_tag

        # Check that new version is higher than most recent version.
        if latest_release_tag and is_numeric_version_tag(latest_release_tag) and is_numeric_version_tag(new_tag) and is_version_lower(new_tag, latest_release_tag):
            print(f"❌ Cannot use new release name {new_tag} since it is lower than (or equal to) the current release {latest_release_tag}!")
            sys.exit(1)

        # We are finally ready to generate release notes.
        notes = generate_release_notes(latest_release_tag, None)
        if not notes:
            print(f"❌ No changes were made since the last release ({latest_release_tag})!")
            sys.exit(1)

        print(f"✅ Generated release notes since {latest_release_tag or 'initial commit'}:")
        print(quote(notes))

        # Update all mentions of the version in the project files.
        def update_pyproject(version: str):
            content = PATH_TOML.read_text()
            new_content = re.sub(r"""version\s*=\s*["'][0-9a-zA-Z.\-+]+["']""", f'version = "{version}"', content)
            PATH_TOML.write_text(new_content)
            print(f"✅ Updated pyproject.toml to version {version}")

        PATH_VARIABLE = args.runtime_variable_path or get_package_path() / "__init__.py"
        def update_variable(version: str):
            if not PATH_VARIABLE.exists():
                print(f"⚠️ {PATH_VARIABLE.name} not found; skipping {args.runtime_variable_name} update")
                return
            content = PATH_VARIABLE.read_text()
            new_content = re.sub(re.escape(args.runtime_variable_name) + r"""\s*=\s*["'][0-9a-zA-Z.\-+]+["']""",
                                 f'{args.runtime_variable_name} = "{version}"', content)
            PATH_VARIABLE.write_text(new_content)
            print(f"✅ Updated {PATH_VARIABLE.name} to version {version}")

        if input(f"⚠️ Please confirm that you want to release the above details as follows:\n    📦 Package: {PACKAGE_NAME}\n    ⏳ Version: {new_tag}\n    🌐 PyPI: {DISTRIBUTION_NAME}\n([y]/n) ").lower() == "n":
            print(f"❌ User abort.")
            sys.exit(1)

        update_pyproject(new_tag)
        update_variable(new_tag)

        # Save changes with Git.
        def git_commit_tag_push(version: str, notes: str):
            try:  # TODO: I wonder if you can pretty-print these calls (e.g. with an indent). Using quote(subprocess.check_output(text=True)) does not work at all, probably because these calls are TQDM-esque. I wonder if they are written to stderr, which you can reroute to stdout.
                print("="*50)
                subprocess.run(["git", "add", "pyproject.toml", PATH_VARIABLE.as_posix()], check=True)  #, stderr=subprocess.STDOUT)
                subprocess.run(["git", "commit", "-m", f"🔖 Release {version}\n\n{notes}"], check=True)
                subprocess.run(["git", "tag", "-a", f"{version}", "-m", f"Release {version}\n\n{notes}"], check=True)
                subprocess.run(["git", "push"], check=True)
                subprocess.run(["git", "push", "origin", f"{version}"], check=True)
                print("="*50)
            except:
                print(f"❌ Failed to save to Git.")
                raise
            print(f"✅ Committed, tagged, and pushed version {version} with release notes.")

        git_commit_tag_push(new_tag, notes)
    else:
        print("✅ Rerun the tool without --retro to release commits made since the last release.")


if __name__ == "__main__":  # Run from command line.
    _main()
