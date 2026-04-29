.PHONY: help test docs docs-serve ci build clean

UV ?= uv
PYTEST ?= pytest
SITE_DIR ?= /tmp/toolplane-site
PORT ?= 8000

help:
	@printf "toolplane commands\n"
	@printf "  make test        Run the pytest suite\n"
	@printf "  make docs        Build MkDocs in strict mode\n"
	@printf "  make docs-serve  Serve docs locally on PORT=%s\n" "$(PORT)"
	@printf "  make ci          Run tests and strict docs build\n"
	@printf "  make build       Build package artifacts\n"
	@printf "  make clean       Remove local generated artifacts\n"

test:
	$(UV) run --no-project --with-editable . --with pytest python -m $(PYTEST)

docs:
	$(UV) run --no-project --with-editable ".[docs]" mkdocs build --strict --site-dir $(SITE_DIR)

docs-serve:
	$(UV) run --no-project --with-editable ".[docs]" mkdocs serve -a 127.0.0.1:$(PORT)

ci: test docs

build:
	$(UV) build

clean:
	rm -rf .pytest_cache build dist site *.egg-info
