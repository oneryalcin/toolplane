# Security Policy

toolplane stores OAuth tokens encrypted at rest and resolves secret
references from your OS keyring, so we treat security reports seriously.

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/oneryalcin/toolplane/security/advisories/new)
("Report a vulnerability"). Do not open a public issue for anything that
could expose users' credentials or sandbox escapes before a fix exists.

You can expect an acknowledgment within a few days. Reports that include a
reproduction get fixed fastest — that is how every security finding in this
project has been handled so far.

## Supported versions

Only the latest release on PyPI receives security fixes.

## Scope notes

- Credential handling is delegated to [fastmcp](https://gofastmcp.com)'s
  storage machinery (Fernet-encrypted store, key in the OS keyring);
  toolplane contains no token-handling code and rolls no cryptography of
  its own. Vulnerabilities in that machinery should also be reported
  upstream.
- The `local_unsafe` backend and `--unsafe` serve flag are explicitly out
  of scope: they are documented as trusted-local-development escape
  hatches with no sandbox promise.
- Sandbox-escape reports against the default monty backend are in scope
  and are the highest-priority class.
