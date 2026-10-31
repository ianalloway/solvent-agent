# Releasing solvent-agent

Every release is a PR that bumps the version and closes out the CHANGELOG,
followed by a tag + GitHub Release that a maintainer cuts by hand. Publishing
to PyPI is done by `.github/workflows/publish.yml` (trusted publisher / OIDC)
when a GitHub Release is published. Agents open the release PR only; they never
tag, publish, or upload.

## 1. Release PR (`release/X.Y.Z`)

- [ ] `solvent/__init__.py` → `__version__ = "X.Y.Z"` (the only version source; `pyproject.toml` reads it).
- [ ] CHANGELOG: move `[Unreleased]` entries under `## [X.Y.Z] - YYYY-MM-DD`, using the template below.
- [ ] CHANGELOG link refs: add `[X.Y.Z]: …/compare/vPREV...vX.Y.Z` and point `[Unreleased]` at `vX.Y.Z...HEAD`.
- [ ] List every PR merged since the last tag: `git log vPREV..origin/main --oneline`.
- [ ] Patch (Z) = fixes only, no new env vars or behaviour flags. Minor (Y) = features. Major = breaking.
- [ ] `pip install -e ".[dev,all]" -c constraints.txt && python -m pytest -q && ruff check .` green.
- [ ] `python -m build && pip install dist/*.whl && solvent version && solvent doctor` in a clean venv.
- [ ] CI green on the PR; squash-merge.

## 2. Tag + publish (maintainer)

```bash
git checkout main && git pull --ff-only
# set the real date in CHANGELOG first if it still says YYYY-MM-DD
git tag -a vX.Y.Z -m "solvent-agent X.Y.Z — <one-line summary>"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <(sed -n '/^## \[X.Y.Z\]/,/^## \[/p' CHANGELOG.md | sed '$d')
```

Publishing the Release triggers the PyPI workflow. Watch it finish.

## 3. After PyPI shows X.Y.Z

- [ ] `pip install -U solvent-agent==X.Y.Z && solvent doctor` from a clean venv.
- [ ] HF Space: bump `hf-space/requirements.txt` floor only if the Space needs new APIs; otherwise trigger a factory rebuild so it picks up the latest release. Confirm the runtime log shows the new version.
- [ ] Close / update any tracking issues referenced in the CHANGELOG.

## CHANGELOG template

```markdown
## [X.Y.Z] - YYYY-MM-DD

One or two sentences: what kind of release this is and whether operators must change anything.

### Security
- …

### Added
- … (#PR)

### Changed
- … (#PR)

### Fixed
- … (#PR)

### Removed
- …
```

Omit empty sections.
