# Release Checklist

Run local checks:

```bash
make ci
make publish-check
```

Optional live examples:

```bash
uv run --no-config --no-project --with-editable . python examples/context7_remote.py
uv run --no-config --no-project --with-editable . python examples/scoped_namespaces_context7.py
```

Publish:

```bash
PYPI_TOKEN=... make publish
```

## Notes

- `make publish-check` builds the source distribution and wheel, then runs
  `uv publish --dry-run` against PyPI.
- PyPI does not allow replacing an existing version. If `publish-check` reports
  a remote file mismatch, bump `__version__` in `src/toolplane/__init__.py`
  before publishing.
- `PYPI_TOKEN` is required for the real publish target and is passed to uv as
  `UV_PUBLISH_TOKEN`.
