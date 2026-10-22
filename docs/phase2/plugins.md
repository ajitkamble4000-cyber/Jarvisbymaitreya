# Phase 2: plugin framework and two in-repo plugins (contract for the web and verification agents)

Scope: a static, reviewed plugin allowlist, loaded at startup and switched on with `ENABLED_PLUGINS`. Two plugins ship: `clock` (tool `current_time`) and `calculator` (tool `calculate`). Both are read-only server tools with risk `low` and no approval. The authoring guide and the trust model are in `docs/plugins.md`. This document does not edit `docs/api.md`.

## Environment

| Variable | Purpose |
| --- | --- |
| `ENABLED_PLUGINS` | JSON list of allowlisted plugin names. Default `["clock", "briefing", "calculator"]`. Example: `ENABLED_PLUGINS='["clock","calculator"]'`. An unknown name stops the API at startup with `ConfigurationError`, and the message lists the valid names. An empty list `[]` enables no plugin. |

Plugin code runs in-process with full privileges and is not sandboxed. Only enable plugins that have been reviewed.

## Endpoints

No new routes. The plugin tools appear in the existing registry routes, which both need a session cookie.

- `GET /api/v1/tools` lists every registry entry. Plugin entries have the same shape as built-in tools. A disabled plugin's tools are absent.
- `GET /api/v1/explore` lists the same tools as capability items. See the category note below.

Example `GET /api/v1/tools` item for `calculate`:

```json
{
  "name": "calculate",
  "title": "Calculate",
  "description": "Evaluate an arithmetic expression of at most 200 characters. Allowed: numbers, + - * / // % **, unary + and -, and parentheses. ...",
  "category": "productivity",
  "risk_level": "low",
  "target": "server",
  "requires_device": false,
  "approval_required": false,
  "status": "working",
  "example_commands": ["What is 17 times 23?", "Calculate (2 + 3) ** 2 / 5."],
  "notes": "Provided by the calculator plugin. Expressions are parsed, never executed as code.",
  "parameters": {
    "additionalProperties": false,
    "properties": {"expression": {"maxLength": 200, "minLength": 1, "title": "Expression", "type": "string"}},
    "required": ["expression"],
    "type": "object"
  }
}
```

## Running a plugin tool

Server tools are proposed through the normal task path, `TaskEngine.propose` with `source` set to `chat`, `schedule`, `manual` or `api`. Plugin tools need no approval, so the task is created already finished:

- Completed: `status: "completed"`, `result: {"summary": "...", "output": {...}}`.
- Failed: `status: "failed"`, `error: {"code": "<stable code>", "message": "...", "details": {}}`. The task still exists.
- Refused: no task is created. The call raises a refusal with code `invalid_arguments` (bad or unsafe arguments, or an unknown tool name).

## Tool: `current_time` (plugin `clock`)

Arguments:

```json
{ "timezone": "Europe/London" }
```

`timezone` is optional. It is an IANA name. A value that is blank, too long, unknown, or path-like is refused with `invalid_arguments`. When it is omitted, the caller's own settings timezone is used (`SettingsDoc.timezone`, set with `PATCH /settings`), and UTC when that is unset.

Output in `result.output`:

```json
{
  "summary": "It is 2026-10-20 14:05:00 in Asia/Tokyo (UTC+09:00).",
  "timezone": "Asia/Tokyo",
  "local_time": "2026-10-20T14:05:00+09:00",
  "utc_offset": "+09:00"
}
```

`utc_offset` is `±HH:MM`. Failure code: `invalid_timezone` (only if a stored settings timezone is somehow invalid). The settings timezone is validated when it is saved, so this is rare.

## Tool: `calculate` (plugin `calculator`)

Arguments:

```json
{ "expression": "(2 + 3) * 4 - 7 // 2" }
```

`expression` is 1 to 200 characters. Allowed: numeric literals (int or float, with `_` separators allowed), `+ - * / // % **`, unary `+` and `-`, and parentheses. Anything else is refused at proposal time with `invalid_arguments`, and no task is created. Refused constructs include names, calls, attributes, subscripts, strings, booleans, comparisons, `and`/`or`/`not`, conditionals, lambdas, comprehensions, bitwise operators and complex literals. Control characters are refused too.

Output in `result.output`:

```json
{
  "summary": "(2 + 3) * 4 - 7 // 2 = 17",
  "expression": "(2 + 3) * 4 - 7 // 2",
  "result": 17
}
```

`result` is a JSON integer or number. Division gives a float, for example `10 / 4` gives `2.5`.

Limits. Every intermediate and final value must be finite and at most `1e18` in absolute value. An exponent may be at most `1000` in absolute value. These limits are checked during evaluation, so a failed task is recorded.

Error codes in a failed task:

| Code | Cause |
| --- | --- |
| `division_by_zero` | `/`, `//`, `%` or `0 ** negative` with a zero divisor. |
| `exponent_too_large` | An exponent with absolute value above 1000, such as `2 ** 1001`. |
| `result_too_large` | A value above `1e18`, a non-finite value (for example `1e400`), or a power whose result would exceed the cap, such as `2 ** 60`. |
| `invalid_expression` | A power that would be a complex number, such as `(-8) ** 0.5`. |

## Error codes (summary)

| Code | Where | Meaning |
| --- | --- | --- |
| `invalid_arguments` | Refusal (no task) | Arguments fail the tool's model. Field names only are reported. |
| `invalid_timezone` | Failed task | Stored settings timezone is invalid. Not expected in normal use. |
| `division_by_zero`, `exponent_too_large`, `result_too_large`, `invalid_expression` | Failed task | Calculator evaluation limits, listed above. |

## Startup behaviour

- Each enabled plugin is validated before any handler is registered. A failure stops startup. Nothing is partly registered.
- On success the log has one info line per plugin, `Loaded plugin <name> version <version>.`, with no other plugin detail.
- The same plugin can be loaded again in the same process (for example when a second app is built). Registration is idempotent for the same function objects.

## Notes for the web agent

- Plugin tools appear in `/tools` and `/explore` like any other server tool. Use `name`, `title`, `description`, `example_commands` and `parameters`.
- The `category` of both plugin tools is `productivity`, which is the lower-case Explore category. See the limitation below before you filter on it.

## Notes for the verification agent

- Enable both plugins with `ENABLED_PLUGINS='["clock","calculator"]'` and confirm both tools appear in `GET /api/v1/tools`.
- Enable only `["clock"]` and confirm `calculate` is absent from `GET /api/v1/tools`.
- Set an invalid name such as `ENABLED_PLUGINS='["clock","shell"]'` and confirm the process refuses to start, with a message that lists the valid names.
- Propose `calculate` with `"__import__('os')"`: expect a refusal and no new task. Propose `"1 / 0"`: expect a failed task with code `division_by_zero`.

## Honest limits

- **Plugins are not sandboxed.** They run in the API process with full privileges. Only reviewed code belongs in `app/plugins/`.
- **Explore category label.** `app/services/explore_service.py` maps tool categories through `_TOOL_CATEGORY_MAP`, which does not contain the lower-case Explore names. Plugin tools therefore appear under `Automation` in `/explore`, not `Productivity`. The integration tools share this behaviour. The fix belongs to the explore service owner (see the report).
- **Handler registry is process-wide.** A handler stays in the process-wide registry after its plugin is disabled in a later build. The engine still refuses such a call, because it only dispatches tools that have a registered spec.
- **Clock time is the server's clock.** It is read with `utc_now()` on each call. It does not check any device clock.
- **Calculator precision.** Results are Python `int` and `float`, so floats carry binary rounding. The calculator does not do exact decimal arithmetic.
- **Limits are chosen, not measured.** The `1e18` and `1000` caps bound evaluation cost. They are not tuned for any workload.
