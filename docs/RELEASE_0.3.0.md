# Release 0.3.0 — publish steps (parent)

Do **not** twine-upload from this agent. After the feature PR is squash-merged to `main`:

1. Confirm `solvent/__init__.py` is `__version__ = "0.3.0"` and CHANGELOG has `[0.3.0]`.
2. From a clean `main`:

```bash
git checkout main && git pull
git tag -a v0.3.0 -m "solvent-agent 0.3.0 — Nano sim + live verify-only settle rail"
git push origin v0.3.0
```

3. Publish a GitHub Release for `v0.3.0` (or use the tag UI). That triggers
   `.github/workflows/publish.yml` (trusted publisher / OIDC → PyPI).

4. After PyPI shows 0.3.0, bump the HF Space pin if needed:

```bash
# in hf-space/requirements.txt
solvent-agent>=0.3.0
```

   and re-upload the Space files (`hf-space/`).

5. Smoke: `pip install -U solvent-agent==0.3.0 && solvent doctor`.
