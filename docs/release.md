# Release Procedure

## Normal release

```bash
# 1. Add release notes to the [Unreleased] section in CHANGELOG.md
#    (replace the placeholder "No unreleased changes yet.")

# 2. Dry-run to verify
python scripts/release.py X.Y.Z --dry-run

# 3. Run the release
python scripts/release.py X.Y.Z
```

`scripts/release.py` does in order:
1. Validates semantic version format (`\d+\.\d+\.\d+`)
2. Updates `version = "X.Y.Z"` in `pyproject.toml`
3. Updates `__version__ = "X.Y.Z"` in `src/wmw/__init__.py`
4. Cuts `CHANGELOG.md`: moves `[Unreleased]` content to `[X.Y.Z] - YYYY-MM-DD`, resets `[Unreleased]` to placeholder
5. Runs `pytest tests/ -v`
6. Runs `python -m build` (produces `dist/wmw-X.Y.Z.tar.gz` and `.whl`)
7. Runs `twine check dist/*`
8. `git add -u` → `git commit -m "Release vX.Y.Z"` → `git tag vX.Y.Z`
9. `git push origin main` → `git push origin vX.Y.Z`

## Skip steps
```bash
python scripts/release.py X.Y.Z --skip-tests
python scripts/release.py X.Y.Z --skip-build
python scripts/release.py X.Y.Z --skip-git
```

## First release on a new machine (all files untracked)
The release script uses `git add -u` which only stages tracked files. For an initial
commit where nothing is tracked yet, stage manually first:

```bash
git add .gitignore CHANGELOG.md pyproject.toml scripts/ src/ tests/
git commit -m "Release vX.Y.Z"
git tag vX.Y.Z
git push origin main && git push origin vX.Y.Z
```

## PyPI upload (not automated by the script)
```bash
twine upload dist/wmw-X.Y.Z*
```

## Required tools
```bash
pip install ".[dev,release]"   # pytest, build, twine
```

## Version locations
- `pyproject.toml` → `version = "X.Y.Z"`
- `src/wmw/__init__.py` → `__version__ = "X.Y.Z"` (hardcoded; overridden at runtime by importlib.metadata)
- `CHANGELOG.md` → `## [X.Y.Z] - YYYY-MM-DD`

All three are updated atomically by `scripts/release.py`.
