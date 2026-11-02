# Security report for 1.1.0-rc.1

## Checks performed

- Reviewed authentication, CSRF, CORS, cookie defaults, ownership filters, provider-secret storage, device command validation, desktop allowlists, plugin registration and release secret handling.
- Ran production `npm audit` for web and desktop: zero known vulnerabilities.
- Ran `pip-audit` after upgrading local pip to 26.2.2: zero known vulnerabilities among resolvable installed packages.
- Exercised owner creation, authenticated settings access and nginx proxying against the local Docker stack.
- Confirmed `.env` is ignored and no new credential was added to source.
- Kept the managed proxy CA as a BuildKit secret used only during dependency installation.

## Security properties retained

- Device actions are validated server-side, require the configured approval policy, are signed for a paired agent, and are checked against the desktop agent's local allowlist.
- Settings and data routes use per-user ownership filters. Provider credentials are encrypted at rest and are excluded from account export.
- MCP and generated skills are absent, so the candidate does not silently execute unknown external tools.
- Emotional assessments are ephemeral and disabled independently. They are not stored in conversation metadata or memory.

## Open risks and limitations

| Severity | Item | Required action |
|---|---|---|
| High | Android stream has no cancellation handle or Stop action. | Disconnect on coroutine cancellation and reject stale stream updates. |
| High | In-process plugins run with API process privileges. | Add process/container isolation and resource limits before accepting untrusted plugins. |
| High | Flagship automation and generated-skill controls do not exist. | Implement permission manifests, sandboxing, approvals, static checks and rollback before activation. |
| Medium | Windows artifact is unsigned; Android uses only the standard debug signature. | Configure protected production signing identities in CI and verify signatures on native platforms. |
| Medium | Live OAuth/provider behavior was not tested. | Use dedicated test tenants, minimal scopes and credential rotation. |
| Medium | Host backup tools were unavailable. | Test matching PostgreSQL client/server major versions and encrypted recovery drills. |
| Low | Dependency scans are point-in-time. | Keep Dependabot/CI scans and review transitive changes on every release. |

No penetration test, SAST product scan or live external-service assessment was performed. The repository should not claim those checks.
