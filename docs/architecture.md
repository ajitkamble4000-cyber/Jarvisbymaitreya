# JARVIS AI — Architecture and module contracts

## Components

| Component | Path | Stack | Role |
|---|---|---|---|
| Backend API | `services/api` | Python 3.13, FastAPI, SQLAlchemy 2 (async), PostgreSQL 16 | System of record, AI orchestration, task engine, scheduler, device hub |
| Web app | `apps/web` | React 19, TypeScript, Vite, Tailwind CSS 4, React Router 7, TanStack Query | Command centre UI |
| Windows desktop agent | `apps/desktop` | Electron 44, TypeScript | Executes approved, typed actions on a paired PC |
| iPad companion | `apps/ipad-companion` | Design only | Platform plan, see its README |
| Shared types | `packages/shared-types` | TypeScript | Mirrors API models used by web and desktop |

Data flow for "Jarvis, open Chrome on my laptop":

1. Web app `POST /api/v1/conversations/{id}/messages` (SSE response).
2. `services/chat` sends the message and the tool catalogue to the selected provider.
3. The model returns a *tool call* (`launch_app`, `{"device": "laptop", "app": "chrome"}`). It is only a proposal.
4. `services/task_engine` validates arguments with the pydantic model, resolves the device by name, checks connectivity, capability, permission and approval policy, and creates a `Task`.
5. If approval is required the task is `awaiting_approval`. The UI shows Approve/Deny. Approval moves it to `queued`.
6. The hub signs a `command` with the device secret and sends it over the agent WebSocket.
7. The agent verifies signature, expiry and replay, checks its local allowlist, launches Chrome, and returns a `result`.
8. The task engine records the result, moves the task to `completed` or `failed`, writes a task event and notification, and publishes `task.updated` over SSE. The chat message is then updated from the real result.

The model never sees credentials and never receives a shell. Only `DEVICE_ACTIONS` in `app/devices/protocol.py` can reach an agent.

## Backend module ownership (contracts)

All process-wide services are constructed in `app/main.py:create_app` and stored on `app.state`. Constructor signatures are fixed:

```python
# app/devices/hub.py
class DeviceHub:
    def __init__(self, *, settings: Settings, session_factory: async_sessionmaker, secret_box: SecretBox, events: EventBus) -> None
    def bind(self, handler: DeviceEventHandler) -> None          # called once by create_app
    async def start(self) -> None                                 # heartbeat monitor
    async def stop(self) -> None
    def is_online(self, device_id: UUID) -> bool
    async def send_command(self, device_id: UUID, message: CommandMessage) -> bool
    async def send_cancel(self, device_id: UUID, message: CancelMessage) -> bool
    async def ping(self, device_id: UUID, timeout_seconds: float = 5.0) -> int | None   # latency ms or None
    async def disconnect(self, device_id: UUID, reason: str) -> None                    # used on revocation
    async def handle_connection(self, websocket: WebSocket) -> None                     # mounted by routes/devices.py

class DeviceEventHandler(Protocol):   # implemented by TaskEngine
    async def on_agent_result(self, device_id: UUID, message: AgentResult) -> None
    async def on_agent_progress(self, device_id: UUID, message: AgentProgress) -> None
    async def on_device_connected(self, device_id: UUID) -> None
    async def on_device_disconnected(self, device_id: UUID) -> None

# app/services/task_engine.py
class TaskEngine:
    def __init__(self, *, settings, session_factory, secret_box, events, tools: ToolRegistry, devices: DeviceHub) -> None
    async def start(self) -> None           # timeouts / queue expiry loop
    async def stop(self) -> None
    # DeviceEventHandler methods (see above)
    async def propose(self, db: AsyncSession, *, user_id: UUID, tool_name: str, arguments: dict, source: str,
                      conversation_id: UUID | None = None, device_id: UUID | None = None,
                      title: str | None = None, description: str = "",
                      idempotency_key: str | None = None) -> Task      # validate + policy + create; caller commits.
                      # SERVER tools with approval_required=False run immediately and the task is created as completed.
    async def approve(self, user_id: UUID, task_id: UUID) -> Task
    async def deny(self, user_id: UUID, task_id: UUID, reason: str | None) -> Task
    async def cancel(self, user_id: UUID, task_id: UUID) -> Task
    async def retry(self, user_id: UUID, task_id: UUID) -> Task
    async def run_server_tool(self, db, *, user_id, tool_name, arguments) -> dict   # SERVER-target tools, read-only or memory writes

# app/scheduler/worker.py
class Scheduler:
    def __init__(self, *, settings, session_factory, tasks: TaskEngine, events: EventBus) -> None
    async def start(self) -> None
    async def stop(self) -> None

# app/providers/registry.py
class ProviderRegistry:
    def __init__(self, *, settings, secret_box, http: httpx.AsyncClient, session_factory, events) -> None
    def catalog(self) -> list[type[AIProvider]]
    async def build(self, db: AsyncSession, user_id: UUID, provider_id: str) -> tuple[AIProvider, ProviderConfig]   # raises ProviderError(NOT_CONFIGURED)

# app/tools/registry.py
def build_tool_registry() -> ToolRegistry      # all ToolSpec entries from app/tools/catalog.py

# app/services/settings_service.py
async def load_settings(db: AsyncSession, user_id: UUID) -> SettingsDoc      # app/schemas/settings.py, defaults filled

# app/services/memory_service.py
async def prompt_memories(db: AsyncSession, user_id: UUID, limit: int = 20) -> list[Memory]   # empty when memory disabled

# app/services/chat.py
class ChatService:
    def __init__(self, *, settings, session_factory, providers: ProviderRegistry, tasks: TaskEngine, events: EventBus, tools: ToolRegistry)
    async def stream_reply(...) -> AsyncIterator[ServerSentEvent]   # used by routes/conversations.py
```

Already implemented and shared (do not change signatures without updating this file):

* `app/core/config.py` `Settings`, `app/core/errors.py` (`AppError` family), `app/core/time.py`
* `app/database/session.py` `get_db`, `session_scope(factory)` for workers
* `app/api/deps.py` `CurrentUser`, `DbSession`, `SettingsDep`, `CSRF_HEADER`
* `app/security/*` (passwords, tokens, `SecretBox`, signing, `RateLimiter`)
* `app/services/events.py` `EventBus.publish(user_id, type, data)` / `subscribe(user_id)`
* `app/services/audit.py` `record_audit(db, action=..., user_id=..., ...)`
* `app/services/notifications.py` `notify(db, events, user_id=..., kind=..., severity=..., title=..., body=...)`
* `app/services/usage.py` `record_usage(db, user_id=..., provider_id=..., model=..., operation=..., success=..., latency_ms=..., error_code=..., usage=...)`
* `app/providers/base.py` `AIProvider` interface and DTOs
* `app/tools/base.py` `ToolSpec`, `ToolRegistry`, `RiskLevel`, `ExecutionTarget`
* `app/devices/protocol.py` wire models, `build_command`, `build_cancel`, `DEVICE_ACTIONS`
* ORM models in `app/models/*`

## Policy rules (enforced by TaskEngine, not by the model)

1. Tool name must exist in the registry. Arguments must validate against `ToolSpec.args_model`. Extra fields are rejected.
2. Device tools need a target device: explicit `device_id`, or a unique name match. Several matches return `needs_device_selection` and no task. No match returns `device_not_found`.
3. The device must be `paired`, not revoked. For dispatch it must also be online. If it is offline the task is `queued` with `waiting_for_device` and expires after `TASK_QUEUE_TTL_SECONDS`. It is never marked as executed.
4. The device must report the tool's `required_capability`, and its permission mode must not be `deny`.
5. Approval is required when `ToolSpec.approval_required` is true or the device mode is `ask`. Mode `allow` is honoured only for LOW-risk tools. Capability keys are the device action names (`open_url`, `launch_app`, `open_folder`, `open_project`, `system_info`, `read_file`, `app_availability`). `ToolSpec.required_capability` equals the device action.
5a. Emergency stop: `automation.emergency_stopped` (user settings, read-only through PATCH) makes `propose` refuse with `emergency_stop_active`. `POST /tasks/emergency-stop` with `{confirm: "STOP"}` sets it and cancels queued, awaiting and running tasks. `POST /tasks/emergency-resume` clears it. The scheduler skips firings while it is set.
6. Resource arguments (`app_id`, `url`, `folder_id`, `project_id`, `file_id`) must match an entry in the device's reported `resources`. `url` must match an approved URL exactly. Agents re-check locally.
7. Approvals expire after `TASK_APPROVAL_TTL_SECONDS`. Expired approvals become `cancelled` with `error.code = approval_expired`.
8. A task's status moves only by the transitions in `TaskEngine._TRANSITIONS`. Every transition writes a `TaskEvent`.
9. Success is recorded only from an agent `result` with `status = succeeded`. Text shown to the user is generated from that result, not from model output.

## Frontend ownership

`apps/web/src/`:

* `services/api.ts` — typed fetch client (credentials included, `X-CSRF-Token` from the `jarvis_csrf` cookie, error envelope parsing, SSE helper).
* `types/api.ts` — response types mirroring `docs/api.md`.
* `layouts/AppShell.tsx`, `routes.tsx` — navigation and route table.
* `stores/` — Zustand-free small stores built on `useSyncExternalStore` for voice state and UI preferences.
* `pages/*` — one file per navigation item.
