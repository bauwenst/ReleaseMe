# ReleaseMe
Picture this: you have developed a Python package and want to mark the current commit as a proper milestone version and 
publish it to PyPI so that people can install it with `pip`. How do you approach this?

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
To enable `ReleaseMe` in your repo, you need to first:
1. Go to https://pypi.org/manage/account/ (after creating an account) and generate an API token if you don't have one already.
2. Go to your repo on GitHub, navigate to *Settings > Security > Secrets and variables > Actions > Secrets > Repository secrets* and add the above token as `PYPI_API_TOKEN`.
3. Go to https://pypi.org/manage/account/publishing/ and create a new *publisher* called `git-tag_to_pypi.yml`. This gives permission for GitHub Actions to upload on your behalf.
4. Make sure the `[project] name = ...` in your `pyproject.toml` matches that of the PyPI *publisher*.

That's all there is to it.

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

### Retroactivity
If you have been tracking versions in your `pyproject.toml` file but you never actually released these as public versions, 
you can let ReleaseMe detect those versions retroactively to publish them. Run instead
```shell
releaseme --retro
```
and the tool will find all version bumps that happened in the TOML through time. This way, your old unofficial versions
can be made into installable versions even if they were not originally uploaded to PyPI.

## Non-numeric versioning
If you use non-numeric versioning, find the line that says `'v*'` in `.github/workflows/git-tag_to_pypi.yml` and change it to just `'*'`.