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

    print()  # Newline

    # Define arguments. They are only parsed after we print the current version.
    parser = argparse.ArgumentParser(description="Push a new tagged version of a Python package.")
    parser.add_argument("version", type=str, help="New version number.")
    parser.add_argument("--runtime_variable_path", type=Path, help="Path to the file where the version is defined in a variable.")
    parser.add_argument("--runtime_variable_name", type=str, help="Name of the variable whose value should be set to the current version.", default="__version__")

    # Sanity check: are we even in a Python package tracked by Git?
    PATH_GIT  = Path(".git")
    PATH_TOML = Path("pyproject.toml")
    if not PATH_GIT.exists() or not PATH_TOML.exists():
        print("❌ This does not look like a Python project root.")
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

        if input(f"⚠️ Please confirm that you want to commit this workflow now. ([y]/n) ").lower() == "n":
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
    ADMIN_VERSION = get_toml_version()  # Only use for this is that IF it is numeric, it will (1) enforce that the new tag is at least as large and (2) enforce a 'v' prefix.
    OLD_TAG = get_last_version_tag()
    OLD_VERSION = OLD_TAG or ADMIN_VERSION  # Can be None!
    if OLD_VERSION is not None:
        if OLD_VERSION == OLD_TAG:
            print(f"✅ Identified official version: {OLD_TAG}")
            if ADMIN_VERSION != OLD_TAG:
                print(f"⚠️ Version found in TOML ({ADMIN_VERSION}) differs from Git tag ({OLD_TAG}). Only the Git tag will be considered.")
        else:  # There is a version given in the TOML, but it was just never used to create a Git tag.
            print(f"✅ Identified unofficial version: {ADMIN_VERSION}")

    args = parser.parse_args()
    NEW_TAG = args.version.strip()
    if OLD_VERSION is not None:
        if is_numeric_version_tag(OLD_VERSION) and is_numeric_version_tag(NEW_TAG):  # These checks are immune to a 'v' prefix.
            if is_version_lower(NEW_TAG, OLD_VERSION):  # Idem.
                print(f"❌ Cannot use new version {NEW_TAG} since it is lower than (or equal to) the current version {OLD_VERSION}!")
                sys.exit(1)
            if OLD_VERSION.startswith("v") and not NEW_TAG.startswith("v"):
                NEW_TAG = "v" + NEW_TAG
            elif not OLD_VERSION.startswith("v") and NEW_TAG.startswith("v"):
                print(f"⚠️ New version ({NEW_TAG}) starts with 'v' unlike the previous version ({OLD_VERSION}). Maybe this is undesired.")
    else:  # If no information is known about versioning policies before this run, we assume the user wants a 'v' prefix for numeric versions.
        if is_numeric_version_tag(NEW_TAG) and not NEW_TAG.startswith("v"):
            NEW_TAG = "v" + NEW_TAG

    # Summarise the commits since the last tag.
    def generate_release_notes(from_tag: Optional[str]):
        if not from_tag:
            print("⚠️ No previous tag found, listing all commits")
            range_spec = "--all"
        else:
            range_spec = f"{from_tag}..HEAD"

        sep = "<<END>>"
        log = subprocess.check_output(["git", "log", range_spec, f"--pretty=format:%B{sep}"], text=True).strip()
        if not log:
            print(f"❌ No changes were made since the last version ({OLD_TAG})!")
            sys.exit(1)

        commit_titles = [s.strip().split("\n")[0] for s in log.split(sep)]
        return "".join("- " + title + "\n"
                       for title in commit_titles if title)

    def quote(s: str) -> str:
        return "\n".join("   | " + line for line in [""] + s.strip().split("\n") + [""])

    notes = generate_release_notes(OLD_TAG)
    print(f"✅ Generated release notes since {OLD_TAG or 'initial commit'}:")
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

    if input(f"⚠️ Please confirm that you want to release the above details as follows:\n    📦 Package: {PACKAGE_NAME}\n    ⏳ Version: {NEW_TAG}\n    🌐 PyPI: {DISTRIBUTION_NAME}\n([y]/n) ").lower() == "n":
        print(f"❌ User abort.")
        sys.exit(1)

    update_pyproject(NEW_TAG)
    update_variable(NEW_TAG)

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

    git_commit_tag_push(NEW_TAG, notes)


if __name__ == "__main__":  # Run from command line.
    _main()
