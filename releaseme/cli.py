#!/usr/bin/env python3


def _main():
    import argparse
    import re
    import sys
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
    def get_package_name() -> str:
        with open(PATH_TOML, "rb") as handle:
            return tomllib.load(handle)["project"]["name"]

    def get_last_version_tag() -> Optional[str]:
        try:
            return subprocess.check_output(["git", "describe", "--tags", "--abbrev=0"], text=True).strip()
        except subprocess.CalledProcessError:
            return None

    def is_numeric_version_tag(version: str) -> bool:
        return re.match(r"^v?[0-9.]+$", version) is not None

    def is_version_lower(v1: str, v2: str):
        return tuple(int(p) for p in v1.removeprefix("v").split(".")) < tuple(int(p) for p in v2.removeprefix("v").split("."))

    PACKAGE_NAME = get_package_name()  # The TOML definitely exists. Question is whether it is correctly formed.
    if input(f"✅ Identified package '{PACKAGE_NAME}'.\n⚠️ Are you sure this is the package you want to release? ([y]/n) ").lower() == "n":
        print(f"❌ User abort.")
        sys.exit(1)

    CURRENT_VERSION = get_last_version_tag()
    if CURRENT_VERSION is not None:
        print(f"✅ Identified current version '{CURRENT_VERSION}'.")

    args = parser.parse_args()
    NEW_VERSION = args.version.strip()
    if CURRENT_VERSION is not None:
        if is_numeric_version_tag(CURRENT_VERSION) and is_numeric_version_tag(NEW_VERSION):  # These checks are immune to a 'v' prefix.
            if is_version_lower(NEW_VERSION, CURRENT_VERSION):  # Idem.
                print(f"❌ Cannot use new version {NEW_VERSION} since it is lower than the current version.")
                sys.exit(1)
            if CURRENT_VERSION.startswith("v") and not NEW_VERSION.startswith("v"):
                NEW_VERSION = "v" + NEW_VERSION
    else:
        if is_numeric_version_tag(NEW_VERSION) and not NEW_VERSION.startswith("v"):
            NEW_VERSION = "v" + NEW_VERSION

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
            print(f"❌ No changes were made since the last version ({CURRENT_VERSION})!")
            sys.exit(1)

        commit_titles = [s.strip().split("\n")[0] for s in log.split(sep)]
        return "".join("- " + title + "\n"
                       for title in commit_titles if title)

    notes = generate_release_notes(CURRENT_VERSION)
    print(f"📝 Generated release notes since {CURRENT_VERSION or 'initial commit'}:\n")
    print(notes, "\n")

    # Update all mentions of the version in the project files.
    def update_pyproject(version: str):
        content = PATH_TOML.read_text()
        new_content = re.sub(r"""version\s*=\s*["'][0-9a-zA-Z.\-+]+["']""", f'version = "{version}"', content)
        PATH_TOML.write_text(new_content)
        print(f"✅ Updated pyproject.toml to version {version}")

    PATH_VARIABLE = args.runtime_variable_path or Path(("src/" if Path("src/").is_dir() else "") + f"{PACKAGE_NAME}/__init__.py")
    def update_variable(version: str):
        if not PATH_VARIABLE.exists():
            print(f"⚠️ {PATH_VARIABLE.name} not found; skipping {args.runtime_variable_name} update")
            return
        content = PATH_VARIABLE.read_text()
        new_content = re.sub(re.escape(args.runtime_variable_name) + r"""\s*=\s*["'][0-9a-zA-Z.\-+]+["']""",
                             f'{args.runtime_variable_name} = "{version}"', content)
        PATH_VARIABLE.write_text(new_content)
        print(f"✅ Updated {PATH_VARIABLE.name} to version {version}")

    update_pyproject(NEW_VERSION)
    update_variable(NEW_VERSION)

    # Save changes with Git.
    def git_commit_tag_push(version: str, notes: str):
        subprocess.run(["git", "add", "pyproject.toml", PATH_VARIABLE.as_posix()], check=True)
        subprocess.run(["git", "commit", "-m", f"🔖 Release {version}\n\n{notes}"], check=True)
        subprocess.run(["git", "tag", "-a", f"{version}", "-m", f"Release {version}\n\n{notes}"], check=True)
        subprocess.run(["git", "push"], check=True)
        subprocess.run(["git", "push", "origin", f"{version}"], check=True)
        print(f"✅ Committed, tagged, and pushed version {version} with release notes.")

    git_commit_tag_push(NEW_VERSION, notes)


if __name__ == "__main__":  # Run from command line.
    _main()
