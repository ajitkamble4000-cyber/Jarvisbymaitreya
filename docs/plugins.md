# Plugins

A plugin adds server tools to JARVIS. It is a Python module inside this repository. It is listed in a static allowlist and switched on with the `ENABLED_PLUGINS` setting. Plugins are the extension point for new read-only or server-side capabilities. Integrations that need OAuth are a separate mechanism (see `docs/phase2/integrations.md`).

## Trust model: read this first

**Plugins run in-process with full privileges.** A plugin module is ordinary Python loaded into the API process. It can read the database, use the shared HTTP client, read any setting and call any function the process can call. Nothing sandboxes it.

So only reviewed code belongs in `services/api/app/plugins/`, and a module enters the system only through a code review that adds it to the allowlist. Do not load code from user input, from the environment, from a database row, or from a downloaded package. The loader imports nothing that is not in `PLUGIN_MODULES`.

A plugin's tools are still subject to the normal server policy in `TaskEngine`. The model only proposes a call. The engine validates the arguments against the tool's `args_model`, checks that the tool is enabled, and records the result as a task. A plugin cannot skip those checks. It can only be wrong inside its own handler, so keep handlers small and read them.

## The contract

Each plugin module defines these names:

| Name | Type | Meaning |
| --- | --- | --- |
| `PLUGIN_NAME` | `str` | Must equal the allowlist key. |
| `PLUGIN_VERSION` | `str` | Non-empty. Logged at startup. |
| `tool_specs()` | `() -> list[ToolSpec]` | Each spec has `target=ExecutionTarget.SERVER`, `approval_required=False`, and a `category` from the lower-case Explore list: `productivity`, `communication`, `development`, `files`, `devices`, `scheduling`, `research`, `media`, `automation`. |
| `server_handlers()` | `() -> dict[str, ServerHandler]` | Keyed by tool name. Every spec needs one handler, and every handler needs one spec. |

A handler has the signature `async def handler(db: AsyncSession, user_id: UUID, args: BaseModel) -> dict`. The engine passes the validated `args_model` instance. The dict must include a `summary` string, which becomes the task summary. Return only data the user may see. Never return credentials or raw provider bodies.

`server_handlers()` must return the **same function objects** on every call. Define handlers at module level. The registry compares identity, so a closure created per call would fail when the registry is built a second time in the same process.

## Writing a plugin

1. Create `services/api/app/plugins/<name>.py` with the four names above. Look at `clock.py` for a minimal read-only example that reads the caller's settings, and `calculator.py` for a pure function with strict input rules.
2. Validate every argument with a pydantic model that subclasses `app.tools.args.ToolArgs` (it forbids unknown keys). Use `Field(min_length=..., max_length=...)` and field validators for anything the model may not send.
3. Raise `app.core.errors.AppError` subclasses with stable `code` values for expected failures, such as `ValidationAppError(..., code="division_by_zero")`. The engine records these as a failed task. Any other exception stops the call.
4. Scope every user-owned query by `user_id`. Add a test that one user's data never appears in another user's result.
5. Add the module to the allowlist (step below), then add unit tests and an integration test that runs the tool through `TaskEngine.propose`.

## The allowlist step

Open `services/api/app/plugins/loader.py` and add the key and module path to `PLUGIN_MODULES`:

```python
PLUGIN_MODULES: Final[dict[str, str]] = {
    "clock": "app.plugins.clock",
    "calculator": "app.plugins.calculator",
    "my_plugin": "app.plugins.my_plugin",
}
```

Then add `my_plugin` to `ENABLED_PLUGINS` (for example `ENABLED_PLUGINS='["clock","my_plugin"]'`). The default is `["clock", "briefing", "calculator"]`.

## What the loader checks at startup

`load_plugin_tools` raises `ConfigurationError` and the app does not start when any of these fail:

- A name is not in `PLUGIN_MODULES`. The error lists the valid names and imports nothing.
- A name is listed twice.
- The module cannot be imported, or its `PLUGIN_NAME` differs from its key, or `PLUGIN_VERSION` is empty.
- A spec's target is not `server`, or its category is not on the Explore list.
- Two specs in the same plugin, or two plugins, use the same tool name.
- A tool name is already a built-in tool.
- A spec has no handler, or a handler has no spec.
- A handler name is already registered by something else.

Validation for every enabled plugin finishes before any handler is registered. A failing plugin therefore leaves no partial registration behind. On success the loader logs one info line per plugin with its name and version only.

## Shipped plugins

| Plugin | Tool | Arguments | Notes |
| --- | --- | --- | --- |
| `clock` | `current_time` | `timezone` (IANA name, optional) | Defaults to the user's settings timezone, then UTC. Unknown zones are refused. |
| `calculator` | `calculate` | `expression` (1 to 200 characters) | Parsed with `ast`. Only numbers, `+ - * / // % **`, unary `+`/`-` and parentheses. Never `eval`. |
| `briefing` | `daily_briefing` | `include_calendar` (boolean, default `true`) | Plain-text summary of the caller's tasks, notifications, next schedules and, when connected, the next 24 hours of Google Calendar. See below. |

The calculator's limits are part of its contract. Every intermediate and final value must be finite and at most `1e18` in absolute value. Exponents may be at most `1000` in absolute value. Division, floor division and modulo by zero are errors. A power that would be complex is an error.

### The briefing plugin

`daily_briefing` is read-only, risk `low`, and needs no approval. It returns `{summary, sections}`. The summary is at most 2000 characters and is built from fixed templates, so it is deterministic text and not a model-written summary. Every query filters by the caller's user id. The calendar part reads the server's Google settings and the caller's stored credential, and it reports an error as `Calendar: unavailable (<code>)` rather than as an empty calendar. A schedule can run the tool with `tool_name` `daily_briefing`. Details, JSON shapes and error codes are in `docs/phase3/briefing.md`.

## Where to look

- `services/api/app/tools/base.py`: `ToolSpec`, `ExecutionTarget`, `RiskLevel`.
- `services/api/app/tools/server_handlers.py`: `register_server_handler`.
- `services/api/app/services/task_engine.py`: `run_server_tool` and `_run_server_proposal`.
- `services/api/tests/unit/test_plugin_loader.py`, `test_calculator.py` and `tests/integration/test_plugins.py`: the reference tests.
