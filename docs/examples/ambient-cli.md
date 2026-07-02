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

=== "Default backend (monty)"

    ```python
    # Flat form: no `cli` root or `.text()` helpers; results are raw dicts.
    status = await git("status", short=True)
    version = await cli_run("docker-compose", "version")
    return {"status": status["stdout"].strip(), "ok": version["ok"]}
    ```

=== "Host policy"

    ```python
    from toolplane import Toolplane

    runtime = Toolplane(ambient_cli=False)
    ```

The `cli` root is the escape hatch for names that are not valid Python
identifiers, such as `docker-compose`. The `git.status(...)` and `cli(...)`
object forms need the `local_unsafe` or `pyodide-deno` backends; on the
default `monty` backend each allowed binary is a flat async function and
`cli_run` is the non-identifier escape hatch. Allowlist policy is enforced
host-side on every call in all forms.

!!! warning "Policy belongs to the host"

    Ambient CLI access is convenient for trusted local workflows. Hosts should
    disable or constrain it when running untrusted code.

## Deterministic Smoke

```bash
uv run --no-config --no-project --with-editable . python examples/ambient_cli_git.py
```

Expected output includes a successful execution result with Git status and
recent changed files.
