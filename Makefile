.PHONY: help test examples docs docs-serve ci build clean publish-check publish

UV ?= uv --no-config
PYTEST ?= pytest
SITE_DIR ?= /tmp/toolplane-site
PORT ?= 8000
PYPI_CHECK_URL ?= https://pypi.org/simple/

help:
	@printf "toolplane commands\n"
	@printf "  make test        Run the pytest suite\n"
	@printf "  make examples    Run deterministic example smokes\n"
	@printf "  make docs        Build MkDocs in strict mode\n"
	@printf "  make docs-serve  Serve docs locally on PORT=%s\n" "$(PORT)"
	@printf "  make ci          Run tests and strict docs build\n"
	@printf "  make build       Build package artifacts\n"
	@printf "  make publish-check  Dry-run publish built artifacts\n"
	@printf "  make publish     Publish built artifacts to PyPI\n"
	@printf "  make clean       Remove local generated artifacts\n"

test:
	$(UV) run --no-project --with-editable ".[dev]" python -m $(PYTEST)

examples:
	$(UV) run --no-project --with-editable . python examples/ambient_cli_git.py
	$(UV) run --no-project --with-editable . python examples/fastmcp_in_process.py
	$(UV) run --no-project --with-editable . python examples/mcp_stdio_config.py
	$(UV) run --no-project --with-editable . python examples/from_config.py

docs:
	$(UV) run --no-project --with-editable ".[docs]" mkdocs build --strict --site-dir $(SITE_DIR)

docs-serve:
	$(UV) run --no-project --with-editable ".[docs]" mkdocs serve -a 127.0.0.1:$(PORT)

ci: test examples docs

build:
	$(UV) build

publish-check: build
	@if [ -n "$$PYPI_TOKEN" ]; then \
		UV_PUBLISH_TOKEN="$$PYPI_TOKEN" $(UV) publish --dry-run --check-url $(PYPI_CHECK_URL) dist/*; \
	else \
		$(UV) publish --dry-run --check-url $(PYPI_CHECK_URL) dist/*; \
	fi

publish: build
	@test -n "$(PYPI_TOKEN)" || (printf '%s\n' 'PYPI_TOKEN is required' >&2; exit 1)
	@UV_PUBLISH_TOKEN="$(PYPI_TOKEN)" $(UV) publish --check-url $(PYPI_CHECK_URL) dist/*

clean:
	rm -rf .pytest_cache build dist site *.egg-info
