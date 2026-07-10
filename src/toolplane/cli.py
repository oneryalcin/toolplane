"""Command-line entrypoint for Toolplane."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import ToolplaneConfig, load_toolplane_config
from .config_edit import ConfigEditError, write_cli_allow_config
from .credentials import CredentialStorageError
from .doctor import doctor_exit_code, format_doctor_checks, run_doctor_checks
from .errors import ToolplaneError, UnsafeFacadeConfigError
from .execution import ExecutionResult
from .mcp_facade import serve_mcp_facade
from .mcp_lifecycle import (
    McpLoginError,
    McpStatusError,
    check_mcp_status,
    format_mcp_list,
    format_mcp_login,
    format_mcp_status,
    login_mcp_server,
    render_mcp_add_snippet,
    write_mcp_add_config,
)
from .policy import EffectivePolicy, ensure_safe_facade_policy, format_effective_policy

_INIT_TEMPLATE = """\
# Toolplane project configuration.
# Safe defaults: sandboxed monty backend, CLI disabled.

[toolplane]
default_backend = "monty" # monty | pyodide-deno | local_unsafe (dev only)

[cli]
mode = "disabled" # disabled | allowlist | ambient (dev only)
# Allow CLI binaries with: toolplane cli allow git gh rg

# Add MCP servers with: toolplane mcp add <name> --url <url>
# [mcp.servers.context7]
# url = "https://mcp.context7.com/mcp"

# Inspect: toolplane config check / toolplane doctor / toolplane mcp list
# Serve:   toolplane serve mcp
"""


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(_normalize_repeated_arg_values(raw_argv))
    match (args.command, _subcommand(args)):
        case ("serve", "mcp"):
            return _cmd_serve_mcp(args)
        case ("init", None):
            return _cmd_init(args)
        case ("config", "check"):
            return _cmd_config_check(args)
        case ("doctor", None):
            return _cmd_doctor(args)
        case ("run", None):
            return _cmd_run(args)
        case ("cli", "allow"):
            return _cmd_cli_allow(args)
        case ("cli", "deny"):
            return _cmd_cli_deny(args)
        case ("cli", "list"):
            return _cmd_cli_list(args)
        case ("mcp", "list"):
            return _cmd_mcp_list(args)
        case ("mcp", "add"):
            return _cmd_mcp_add(args)
        case ("mcp", "remove"):
            return _cmd_mcp_remove(args)
        case ("mcp", "login"):
            return _cmd_mcp_login(args)
        case ("mcp", "status"):
            return _cmd_mcp_status(args)
        case ("mcp", "import"):
            return _cmd_mcp_import(args)
        case ("secret", "set"):
            return _cmd_secret_set(args)
        case ("secret", "rm"):
            return _cmd_secret_rm(args)
        case ("secret", "list"):
            return _cmd_secret_list(args)
        case _:
            parser.print_help()
            return 2


def _subcommand(args: argparse.Namespace) -> str | None:
    for attribute in (
        "serve_command",
        "config_command",
        "cli_command",
        "mcp_command",
        "secret_command",
    ):
        value = getattr(args, attribute, None)
        if value is not None:
            return value
    return None


def _load_config(config_path: str) -> ToolplaneConfig | None:
    """Load and validate a config, or print the error and return None."""
    try:
        return load_toolplane_config(config_path)
    except (OSError, ValueError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return None


def _cmd_serve_mcp(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    try:
        policy = EffectivePolicy.from_config(config, allow_unsafe=args.unsafe)
        ensure_safe_facade_policy(policy)
        print(format_effective_policy(policy), file=sys.stderr)
        asyncio.run(
            serve_mcp_facade(
                config,
                transport=args.transport,
                host=args.host,
                port=args.port,
                allow_unsafe=args.unsafe,
                hybrid=args.hybrid,
            )
        )
    except UnsafeFacadeConfigError as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    except CredentialStorageError as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if path.exists() and not args.force:
        print(
            f"toolplane: {path} already exists; use --force to overwrite",
            file=sys.stderr,
        )
        return 2
    try:
        path.write_text(_INIT_TEMPLATE, encoding="utf-8")
    except OSError as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {path}")
    return 0


def _cmd_config_check(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    print(_format_config_summary(args.config, config), end="")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    checks = run_doctor_checks(config)
    print(format_doctor_checks(checks), end="")
    return doctor_exit_code(checks)


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        code = Path(args.script).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2

    async def execute() -> ExecutionResult:
        from .runtime import Toolplane

        runtime = await Toolplane.from_config(args.config)
        return await runtime.execute(code)

    try:
        result = asyncio.run(execute())
    except (ToolplaneError, OSError, ValueError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.error is not None:
        print(
            f"toolplane: {result.error.type}: {result.error.message}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result.value, indent=2, default=str))
    return 0


def _cmd_cli_allow(args: argparse.Namespace) -> int:
    try:
        path, binaries = write_cli_allow_config(args.config, tuple(args.binaries))
    except (ConfigEditError, OSError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    print(f"Allowed CLI binaries in {path}: {', '.join(binaries)} (mode=allowlist)")
    return 0


def _cmd_cli_deny(args: argparse.Namespace) -> int:
    from .config_edit import write_cli_deny_config

    try:
        path, remaining = write_cli_deny_config(args.config, tuple(args.binaries))
    except (ConfigEditError, OSError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    removed = ", ".join(dict.fromkeys(args.binaries))
    if remaining:
        print(f"Denied {removed} in {path}; still allowed: {', '.join(remaining)}")
    else:
        print(
            f"Denied {removed} in {path}; the allowlist is now empty, so "
            "cli mode is set to disabled — re-enable with: "
            "toolplane cli allow <binary>"
        )
    return 0


def _cmd_cli_list(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    mode = config.cli.mode
    if mode == "allowlist":
        allow = ", ".join(config.cli.allow) or "(empty — no binaries callable)"
        print(f"cli: allowlist [{allow}]")
    else:
        print(f"cli: {mode}")
    return 0


def _cmd_mcp_list(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    print(format_mcp_list(config), end="")
    return 0


def _cmd_mcp_remove(args: argparse.Namespace) -> int:
    from .mcp_lifecycle import write_mcp_remove_config

    try:
        path, removed = write_mcp_remove_config(args.config, args.name)
    except (ConfigEditError, OSError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    print(f"Removed MCP server {args.name!r} from {path}")
    url = removed.get("url")
    if url:
        try:
            from .credentials import has_stored_oauth_tokens

            if asyncio.run(has_stored_oauth_tokens(str(url))):
                print(
                    "note: stored OAuth tokens for this server remain "
                    "encrypted in ~/.toolplane/oauth — re-adding it keeps "
                    "the login; deleting that directory revokes ALL locally "
                    "stored logins, not just this one"
                )
        except Exception:
            # the token note is best-effort; removal already succeeded
            pass
    return 0


def _cmd_mcp_add(args: argparse.Namespace) -> int:
    try:
        if args.print:
            snippet = render_mcp_add_snippet(
                args.name,
                url=args.url,
                command=args.command_value,
                args=tuple(args.args or ()),
                auth=args.auth,
            )
            print(snippet, end="")
        else:
            path = write_mcp_add_config(
                args.config,
                args.name,
                url=args.url,
                command=args.command_value,
                args=tuple(args.args or ()),
                auth=args.auth,
                force=args.force,
            )
            print(f"Added MCP server {args.name!r} to {path}")
    except (ConfigEditError, OSError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_mcp_login(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    if args.name in config.mcp.servers:
        print(
            f"Logging in to {args.name!r}; a browser window may open "
            "to complete authentication...",
            file=sys.stderr,
        )
    try:
        status = asyncio.run(
            login_mcp_server(
                config,
                args.name,
                timeout_seconds=args.timeout,
            )
        )
    except (
        McpLoginError,
        McpStatusError,
        CredentialStorageError,
        OSError,
        ValueError,
    ) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    message = format_mcp_login(status, timeout_seconds=args.timeout)
    if status.state == "ok":
        print(message, end="")
        return 0
    print(f"toolplane: {message}", end="", file=sys.stderr)
    return 2


def _cmd_mcp_import(args: argparse.Namespace) -> int:
    from .mcp_import import format_import_report, import_mcp_servers

    try:
        report = import_mcp_servers(
            args.config,
            args.source,
            dry_run=args.dry_run,
            force=args.force,
            plaintext=args.plaintext,
            verbatim=args.verbatim,
        )
    except (ConfigEditError, CredentialStorageError, OSError) as exc:
        # ConfigEditError covers McpImportError and malformed-TOML parse
        # errors from the target config (reviewer finding on #97)
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    if not report.imported and not report.skipped:
        print(f"No MCP servers found to import from {args.source}.")
        return 0
    print(format_import_report(report), end="")
    return 0


def _cmd_secret_set(args: argparse.Namespace) -> int:
    from .credentials import CredentialStorageError, secret_set

    # never on argv: process lists leak; stdin pipe or hidden prompt only
    if sys.stdin.isatty():
        import getpass

        value = getpass.getpass(f"Value for secret {args.name!r}: ")
    else:
        # strip surrounding whitespace: CRLF pipes left a trailing \r that
        # silently corrupted the credential (reviewer finding on #95);
        # internal newlines survive for multi-line secrets like PEM keys
        value = sys.stdin.read().strip()
    if not value.strip():
        print("toolplane: refusing to store an empty secret", file=sys.stderr)
        return 2
    try:
        secret_set(args.name, value)
    except CredentialStorageError as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    print(
        f"Stored secret {args.name!r} in the OS keyring; reference it in "
        f"toolplane.toml as keyring://{args.name}"
    )
    return 0


def _cmd_secret_rm(args: argparse.Namespace) -> int:
    from .credentials import CredentialStorageError, secret_delete

    try:
        secret_delete(args.name)
    except CredentialStorageError as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    print(f"Deleted secret {args.name!r}")
    return 0


def _cmd_secret_list(args: argparse.Namespace) -> int:
    from .credentials import secret_list

    names = secret_list()
    if not names:
        print("No secrets stored. Add one with: toolplane secret set <name>")
        return 0
    for name in names:
        print(name)
    return 0


def _cmd_mcp_status(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    try:
        statuses = asyncio.run(
            check_mcp_status(
                config,
                names=tuple(args.names or ()),
                timeout_seconds=args.timeout,
            )
        )
    except (McpStatusError, OSError, ValueError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    print(format_mcp_status(statuses), end="")
    return 0


def _format_config_summary(config_path: str, config: ToolplaneConfig) -> str:
    cli = config.cli.mode
    if cli == "allowlist":
        cli += f" [{', '.join(config.cli.allow)}]"
    servers = ", ".join(sorted(config.mcp.servers)) or "none"
    lines = [
        f"config: {config_path}",
        f"backend: {config.toolplane.default_backend}",
        f"cli: {cli}",
        f"mcp servers: {servers}",
    ]
    if config.toolplane.default_backend == "local_unsafe" or config.cli.mode == "ambient":
        lines.append("note: serve mcp will require --unsafe with this config")
    lines.append("ok")
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    from . import __version__

    parser = argparse.ArgumentParser(prog="toolplane")
    parser.add_argument(
        "--version",
        action="version",
        version=f"toolplane {__version__}",
    )
    subcommands = parser.add_subparsers(dest="command")

    serve = subcommands.add_parser("serve", help="Serve Toolplane surfaces")
    serve_subcommands = serve.add_subparsers(dest="serve_command")

    mcp = serve_subcommands.add_parser("mcp", help="Serve the Toolplane MCP facade")
    mcp.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )
    mcp.add_argument(
        "--transport",
        choices=("stdio", "http", "sse", "streamable-http"),
        default="stdio",
        help="FastMCP transport to serve",
    )
    mcp.add_argument("--host", help="Host for HTTP-based transports")
    mcp.add_argument("--port", type=int, help="Port for HTTP-based transports")
    mcp.add_argument(
        "--hybrid",
        action="store_true",
        help=(
            "Also re-export every capability as an ordinary MCP tool "
            "alongside the meta-tools (#114; best on deferred-loading "
            "clients like Claude Code)"
        ),
    )
    mcp.add_argument(
        "--unsafe",
        action="store_true",
        help=(
            "Allow local_unsafe backend or ambient CLI policy for trusted local "
            "development"
        ),
    )

    init = subcommands.add_parser("init", help="Write a starter toolplane.toml")
    init.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to write the Toolplane TOML config file",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config file",
    )

    config_root = subcommands.add_parser("config", help="Inspect Toolplane config")
    config_subcommands = config_root.add_subparsers(dest="config_command")
    config_check = config_subcommands.add_parser(
        "check",
        help="Validate a config file and print a summary without network calls",
    )
    config_check.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    doctor = subcommands.add_parser(
        "doctor",
        help="Check local prerequisites for a configured runtime",
    )
    doctor.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    run_parser = subcommands.add_parser(
        "run",
        help="Execute a Python snippet file against the configured runtime",
    )
    run_parser.add_argument("script", help="Path to a Python snippet file")
    run_parser.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    cli_root = subcommands.add_parser("cli", help="Manage CLI policy")
    cli_subcommands = cli_root.add_subparsers(dest="cli_command")
    cli_allow = cli_subcommands.add_parser(
        "allow",
        help="Allow CLI binaries by switching the config to allowlist mode",
    )
    cli_allow.add_argument(
        "binaries",
        nargs="+",
        help="CLI binary name(s) to add to the allowlist",
    )
    cli_allow.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    cli_deny = cli_subcommands.add_parser(
        "deny",
        help="Remove CLI binaries from the allowlist",
    )
    cli_deny.add_argument(
        "binaries",
        nargs="+",
        help="CLI binary name(s) to remove from the allowlist",
    )
    cli_deny.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    cli_list = cli_subcommands.add_parser(
        "list",
        help="Show the CLI policy mode and allowlist",
    )
    cli_list.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    mcp_root = subcommands.add_parser("mcp", help="Manage MCP server snippets")
    mcp_subcommands = mcp_root.add_subparsers(dest="mcp_command")

    mcp_list = mcp_subcommands.add_parser(
        "list",
        help="List configured MCP servers without connecting",
    )
    mcp_list.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    mcp_add = mcp_subcommands.add_parser(
        "add",
        help="Add an MCP server to a Toolplane TOML config",
    )
    mcp_add.add_argument("name", help="MCP server name")
    mcp_add.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )
    mcp_add.add_argument(
        "--print",
        action="store_true",
        help="Print the TOML snippet instead of editing the config file",
    )
    mcp_add.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing MCP server with the same name",
    )
    source = mcp_add.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Remote MCP endpoint URL")
    source.add_argument(
        "--command",
        dest="command_value",
        help="Local stdio MCP server command",
    )
    mcp_add.add_argument(
        "--arg",
        dest="args",
        action="append",
        help="Argument for --command; repeat for multiple arguments",
    )
    mcp_add.add_argument(
        "--auth",
        choices=("oauth",),
        help="Authentication mode for remote URL snippets",
    )

    mcp_remove = mcp_subcommands.add_parser(
        "remove",
        help="Remove an MCP server from a Toolplane TOML config",
    )
    mcp_remove.add_argument("name", help="MCP server name to remove")
    mcp_remove.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    mcp_login = mcp_subcommands.add_parser(
        "login",
        help="Prime an MCP server interactively (may open a browser)",
    )
    mcp_login.add_argument("name", help="MCP server name to log in")
    mcp_login.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )
    mcp_login.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Login timeout in seconds, including the browser flow",
    )

    mcp_status = mcp_subcommands.add_parser(
        "status",
        help="Check configured MCP servers without authenticating",
    )
    mcp_status.add_argument(
        "names",
        nargs="*",
        help="Optional MCP server name(s) to check",
    )
    mcp_status.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )
    mcp_status.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-server status timeout in seconds",
    )

    mcp_import = mcp_subcommands.add_parser(
        "import",
        help="Import MCP servers from another client's config",
    )
    mcp_import.add_argument(
        "--from",
        dest="source",
        required=True,
        choices=("claude", "codex"),
        help="Client to import from (claude: ~/.claude.json + ./.mcp.json; "
        "codex: ~/.codex/config.toml)",
    )
    mcp_import.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )
    mcp_import.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be imported without writing anything",
    )
    mcp_import.add_argument(
        "--force",
        action="store_true",
        help="Replace already-configured servers with the same name",
    )
    mcp_import.add_argument(
        "--plaintext",
        action="store_true",
        help="Copy secret-looking values literally instead of writing "
        "env:// or keyring:// references",
    )
    mcp_import.add_argument(
        "--verbatim",
        action="store_true",
        help="Keep mcp-remote/fastmcp-remote wrapper entries as-is instead "
        "of rewriting them to direct url entries",
    )

    secret_root = subcommands.add_parser(
        "secret",
        help="Manage secrets in the OS keyring (referenced as keyring://<name>)",
    )
    secret_subcommands = secret_root.add_subparsers(dest="secret_command")
    secret_set = secret_subcommands.add_parser(
        "set",
        help="Store a secret (value read from stdin or an interactive prompt)",
    )
    secret_set.add_argument("name", help="Secret name")
    secret_rm = secret_subcommands.add_parser(
        "rm", help="Delete a stored secret"
    )
    secret_rm.add_argument("name", help="Secret name")
    secret_subcommands.add_parser(
        "list", help="List stored secret names (never values)"
    )

    return parser


def _normalize_repeated_arg_values(argv: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--arg" and index + 1 < len(argv):
            normalized.append(f"--arg={argv[index + 1]}")
            index += 2
            continue
        normalized.append(item)
        index += 1
    return normalized


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
