# Ambient CLI

Ambient CLI support is for local development and controlled code-mode
experiments. Toolplane does not parse every binary on startup. It resolves a CLI
through `cli-to-py` only when agent code first calls it.

=== "Agent code"

    ```python
    status = await git.status(short=True).text()
    files = await git.diff(name_only=True, _=["HEAD~1", "HEAD"]).lines()
    return {"status": status, "files": files}
    ```

=== "Explicit CLI root"

    ```python
    version = await cli("docker-compose").version().text()
    return version
    ```

=== "Host policy"

    ```python
    from toolplane import Toolplane

    runtime = Toolplane(ambient_cli=False)
    ```

The `cli` root is the escape hatch for names that are not valid Python
identifiers, such as `docker-compose`.

!!! warning "Policy belongs to the host"

    Ambient CLI access is convenient for trusted local workflows. Hosts should
    disable or constrain it when running untrusted code.

## Deterministic Smoke

```bash
uv run --no-config --no-project --with-editable . python examples/ambient_cli_git.py
```

Expected output includes a successful execution result with Git status and
recent changed files.
