# ReleaseMe
Picture this: you have developed a Python package and want to mark the current commit as a proper milestone version and 
publish it to PyPI so that people can install it with `pip`, but it only exists on GitHub right now. How do you approach this?

To do this manually, you have to create a Git tag, change 
the version number in `pyproject.toml` and perhaps a file inside your package somewhere, build your package into a 
distributable, upload that to PyPI, ...

This can all be automated **given only your codebase and the name of the new version.**

## Installation
```shell
pip install cli_release-me
```

## Usage
### One-time preparation
#### Account
If this is your first time publishing any package to PyPI, you'll need to create a PyPI account and connect it to GitHub.
Then, go to https://pypi.org/manage/account/ and generate an API token if you don't have one already.

#### Repository
To enable `ReleaseMe` in your repo, follow these three steps:
1. Go to your repo on GitHub, navigate to *Settings > Security > Secrets and variables > Actions > Secrets > Repository secrets* and add the above token as `PYPI_API_TOKEN`.
2. Go to https://pypi.org/manage/account/publishing/ and create a new publisher. You will be asked for 4 fields:
    - Your GitHub username and the name of the GitHub repo.
    - The workflow name, which is always `git-tag_to_pypi.yml`.
    - The project name, which is the string people will put after `pip install` to get your package.
3. Make sure the `[project] name = ...` in your `pyproject.toml` matches that project name on PyPI.

That's all there is to it. PyPI can now verify that when your package is uploaded, it is done by _one specific_ GitHub Action 
from _the specific repo_ of the _the specific user_ you submitted.

_Note:_ the project name is not necessarily the package name. E.g., to be able to `import sklearn` you have to `pip install scikit-learn` rather than `pip install sklearn`.

_Note:_ the project will only appear on PyPI and on your profile after you have released your first version.

_Note:_ if you don't configure your `PYPI_API_TOKEN`, you will receive a `NoKeyringError: No recommended backend was available`.

### Execution
Open your shell in your repo, then run:
```shell
releaseme 1.0.0
```
where you replace `1.0.0` with the version name you want.
(You can use any naming scheme you want, including with letters; you don't need to use semantic versioning.)

### Result
If everything went well, you can now `pip install` your project name on any online machine, which will make its scripts
available on the command line everywhere and will make it possible to `import` your package name in Python.

### Further releases
#### Trusted publisher
Once PyPI has created a project for your package, visit `https://pypi.org/manage/project/{YOUR_PROJECT}/settings/publishing/`
and again create a publisher like above. You can now delete the publisher at https://pypi.org/manage/account/publishing/
because you can only have three unassigned publishers associated with your account.

#### Backfilling
In case you have already released at least one version of your package to PyPI, you may still want to release earlier 
versions of your package corresponding to manual version changes in your `pyproject.toml` file. You can "backfill" 
these earlier versions with ReleaseMe by running
```shell
releaseme --backfill
```
so that the tool will find all version bumps that happened in the TOML through time before your latest release, and 
still release them for users who want to install older unofficial releases.

_Note:_ You do not need this option if you have not released anything yet, even if you tracked unofficial versions in 
`pyproject.toml`. ReleaseMe will detect that this is your first time and propose to release all those versions separately.

_Note:_ For all TOML versions where ReleaseMe's `.yml` did not exist in your project, you will be asked to install 
GitHub's `gh` tool.

## Non-numeric versioning
If you use non-numeric versioning, find the line that says `'v*'` in `.github/workflows/git-tag_to_pypi.yml` and change it to just `'*'`.