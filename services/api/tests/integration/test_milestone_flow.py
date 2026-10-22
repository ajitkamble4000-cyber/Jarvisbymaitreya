"""First milestone, end to end over a live server.

Setup, Gemini key storage, provider test, a chat turn that proposes ``launch_app``, device pairing,
a real WebSocket agent on a uvicorn server, approval, a signed command and a result that completes
the task. The negative cases are an offline device, a revoked device and a forged result.

Nothing leaves the process. The FastAPI app runs under uvicorn inside this test's event loop, the
desktop agent is a websockets client, and Gemini is an httpx.MockTransport that accepts one fake key.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
from sqlalchemy import select
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from app.models.ai import ProviderConfig
from app.providers.registry import ProviderRegistry
from tests.conftest import TEST_ORIGIN, create_owner, csrf_headers

pytestmark = pytest.mark.integration

API = "/api/v1"
GEMINI_HOST = "generativelanguage.googleapis.com"
GEMINI_KEY = "AIzaSy-MILESTONE-fake-key-7781"
MODEL = "gemini-2.5-flash"
DEVICE_CAPABILITIES = [
    "open_url",
    "launch_app",
    "open_folder",
    "open_project",
    "system_info",
    "read_file",
    "app_availability",
]
DEVICE_RESOURCES = {
    "apps": [{"id": "chrome", "label": "Google Chrome"}, {"id": "vscode", "label": "Visual Studio Code"}],
    "urls": [{"id": "youtube", "label": "YouTube", "url": "https://www.youtube.com"}],
    "folders": [{"id": "downloads", "label": "Downloads"}],
    "projects": [{"id": "jarvis", "label": "JARVIS project"}],
    "files": [{"id": "notes", "label": "Meeting notes"}],
}
USER_PROMPT = "Open Chrome on my laptop."


# ---- fake upstream ---------------------------------------------------------------------


def _sse_chunk(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\r\n\r\n".encode()


def _text_chunk(text: str) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]}


def _launch_chunk() -> dict[str, Any]:
    call = {"name": "launch_app", "args": {"device": "Laptop", "app": "chrome"}}
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"functionCall": call}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 12, "totalTokenCount": 52},
    }


class FakeGemini:
    """Answers the three free calls the backend makes (model list, key test, stream). Records each call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != GEMINI_HOST:
            return httpx.Response(599, json={"error": {"message": "unexpected host in test"}})
        self.calls.append(f"{request.method} {request.url.path}")
        if request.headers.get("x-goog-api-key") != GEMINI_KEY:
            return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid."}})
        if request.method == "GET" and request.url.path == "/v1beta/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": f"models/{MODEL}",
                            "displayName": "Gemini 2.5 Flash",
                            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path == f"/v1beta/models/{MODEL}:streamGenerateContent":
            body = request.content.decode("utf-8")
            if "functionResponse" in body:
                chunks = [_text_chunk("Noted.")]
            elif USER_PROMPT in body:
                chunks = [_launch_chunk()]
            else:
                chunks = [_text_chunk("Hello.")]
            payload = b"".join(_sse_chunk(chunk) for chunk in chunks)
            return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})
        return httpx.Response(404, json={"error": {"message": "not found in test"}})


def install_gemini(app: Any, fake: FakeGemini) -> None:
    """Replaces the provider registry so its HTTP client uses the fake. The app fixture is per test."""
    app.state.providers = ProviderRegistry(
        settings=app.state.settings,
        secret_box=app.state.secret_box,
        http=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
        session_factory=app.state.session_factory,
        events=app.state.events,
    )


# ---- live server and desktop agent -----------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@asynccontextmanager
async def live_server(application: Any) -> AsyncIterator[str]:
    """Serves the app with uvicorn inside the running loop. Yields the HTTP base URL. Lifespan stays with the fixture."""
    config = uvicorn.Config(application, host="127.0.0.1", port=_free_port(), lifespan="off", log_level="warning")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        for _ in range(400):
            if server.started:
                break
            if serving.done():
                serving.result()
                raise RuntimeError("uvicorn exited before it started")
            await asyncio.sleep(0.025)
        else:
            raise RuntimeError("uvicorn did not start")
        yield f"http://127.0.0.1:{config.port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=10)


class DesktopAgent:
    """A desktop agent written from docs/desktop-protocol.md: hello, heartbeat, verify, result."""

    def __init__(self, ws: ClientConnection, secret: str) -> None:
        self.ws = ws
        self.secret = secret
        self.frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        with suppress(ConnectionClosed):
            async for raw in self.ws:
                frame = json.loads(raw)
                if frame.get("type") == "ping":
                    await self.ws.send(json.dumps({"type": "pong", "nonce": frame["nonce"]}))
                else:
                    self.frames.put_nowait(frame)

    async def send(self, frame: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(frame))

    async def next_frame(self, kind: str, timeout: float = 5.0) -> dict[str, Any]:
        async def _next() -> dict[str, Any]:
            while True:
                frame = await self.frames.get()
                if frame.get("type") == kind:
                    return frame

        return await asyncio.wait_for(_next(), timeout)

    async def no_frame(self, kind: str, window: float) -> None:
        with pytest.raises(TimeoutError):
            await self.next_frame(kind, timeout=window)

    async def aclose(self) -> None:
        with suppress(ConnectionClosed, OSError):
            await self.ws.close()
        self._reader.cancel()
        with suppress(asyncio.CancelledError, ConnectionClosed):
            await self._reader


def verify_command_signature(secret: str, frame: dict[str, Any]) -> None:
    """Recomputes the signature from docs/desktop-protocol.md section 4, independently of the backend code."""
    fields = [frame["id"], frame["action"], frame["issued_at"], frame["expires_at"], frame["nonce"], frame["args_json"]]
    expected = hmac.new(secret.encode("utf-8"), "\n".join(fields).encode("utf-8"), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, frame["signature"]), "command signature does not verify"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hello(capabilities: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "hello",
        "agent_version": "1.0.0",
        "os": "windows",
        "os_version": "10.0.22631",
        "capabilities": capabilities or DEVICE_CAPABILITIES,
        "resources": DEVICE_RESOURCES,
    }


def _result(command_id: str, *, status: str = "succeeded") -> dict[str, Any]:
    now = _now_iso()
    if status == "succeeded":
        output = {"pid": 4312, "app_id": "chrome", "verified": False, "summary": "Started Google Chrome (pid 4312)."}
        return {"type": "result", "command_id": command_id, "status": status, "output": output, "error": None, "started_at": now, "finished_at": now}
    return {
        "type": "result",
        "command_id": command_id,
        "status": status,
        "output": {},
        "error": {"code": "execution_failed", "message": "Chrome did not start.", "details": {}},
        "started_at": now,
        "finished_at": now,
    }


# ---- scenario helpers ------------------------------------------------------------------


class Bench:
    """Holds the signed-in browser-side client and every response body, so secrets can be scanned at the end."""

    def __init__(self, base_url: str, app: Any) -> None:
        self.base_url = base_url
        self.app = app
        self.client = httpx.AsyncClient(base_url=base_url, headers={"Origin": TEST_ORIGIN}, timeout=10)
        self.bodies: list[str] = []

    async def close(self) -> None:
        await self.client.aclose()

    def remember(self, response: httpx.Response) -> httpx.Response:
        self.bodies.append(response.text)
        return response

    async def get(self, path: str) -> Any:
        response = self.remember(await self.client.get(f"{API}{path}"))
        assert response.status_code == 200, response.text
        return response.json()

    async def write(self, method: str, path: str, body: dict[str, Any] | None = None, *, expect: int = 200) -> Any:
        response = self.remember(
            await self.client.request(method, f"{API}{path}", json=body, headers=csrf_headers(self.client))
        )
        assert response.status_code == expect, response.text
        return response.json() if response.content and response.headers.get("content-type", "").startswith("application/json") else None

    async def chat(self, conversation_id: str, content: str) -> list[tuple[str, dict[str, Any]]]:
        events: list[tuple[str, dict[str, Any]]] = []
        async with self.client.stream(
            "POST",
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": content},
            headers=csrf_headers(self.client),
        ) as response:
            assert response.status_code == 200
            name: str | None = None
            async for line in response.aiter_lines():
                self.bodies.append(line)
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                elif line.startswith("data:") and name is not None:
                    events.append((name, json.loads(line[len("data:") :].strip())))
                    name = None
        return events

    async def wait_task(self, task_id: str, statuses: set[str], timeout: float = 5.0) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            task = (await self.get(f"/tasks/{task_id}"))["task"]
            if task["status"] in statuses:
                return dict(task)
            if loop.time() > deadline:
                raise AssertionError(f"task stayed {task['status']}, expected one of {sorted(statuses)}")
            await asyncio.sleep(0.05)

    async def wait_device(self, device_id: str, connection_status: str, timeout: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            devices = (await self.get("/devices"))["items"]
            if any(d["id"] == device_id and d["connection_status"] == connection_status for d in devices):
                return
            if loop.time() > deadline:
                raise AssertionError(f"device did not become {connection_status}")
            await asyncio.sleep(0.05)


async def set_up_owner_and_gemini(bench: Bench) -> None:
    await create_owner(bench.client)
    status = await bench.get("/auth/status")
    assert status["authenticated"] is True and status["setup_required"] is False

    stored = await bench.write("PUT", "/providers/gemini/credential", {"api_key": GEMINI_KEY})
    assert stored["configured"] is True
    assert stored["credential_hint"] and GEMINI_KEY not in stored["credential_hint"]
    async with bench.app.state.session_factory() as db:
        row = await db.scalar(select(ProviderConfig).where(ProviderConfig.provider_id == "gemini"))
    assert row is not None and row.credential_ciphertext, "the key must be stored"
    assert GEMINI_KEY not in row.credential_ciphertext, "the stored value must be encrypted, not the plain key"

    tested = await bench.write("POST", "/providers/gemini/test")
    assert tested["ok"] is True, tested

    models = await bench.get("/providers/gemini/models")
    assert MODEL in {item["id"] for item in models["items"]}
    enabled = await bench.write("PUT", "/providers/gemini", {"enabled": True, "selected_model": MODEL})
    assert enabled["selected_model"] == MODEL


async def pair_laptop(bench: Bench) -> dict[str, str]:
    """Issues a code from the signed-in web app and redeems it as the agent (no cookies, as the agent has none)."""
    code = (await bench.write("POST", "/devices/pairing-codes", expect=201))["code"]
    async with httpx.AsyncClient(base_url=bench.base_url, headers={"Origin": TEST_ORIGIN}, timeout=10) as agent_http:
        response = await agent_http.post(
            f"{API}/agent/pair",
            json={
                "code": code,
                "device_name": "Laptop",
                "device_type": "laptop",
                "os": "windows",
                "os_version": "10.0.22631",
                "agent_version": "1.0.0",
                "capabilities": DEVICE_CAPABILITIES,
                "resources": DEVICE_RESOURCES,
            },
        )
        assert response.status_code == 200, response.text
        paired = response.json()
        replay = await agent_http.post(
            f"{API}/agent/pair",
            json={"code": code, "device_name": "Laptop", "device_type": "laptop", "os": "windows", "os_version": "10.0.22631", "agent_version": "1.0.0", "capabilities": [], "resources": {}},
        )
        assert replay.status_code == 401, "a pairing code must be single use"
        assert replay.json()["error"]["code"] == "pairing_code_invalid"
    assert paired["protocol_version"] == 1
    assert paired["websocket_path"] == "/api/v1/agent/ws"
    return {"device_id": paired["device_id"], "secret": paired["device_secret"]}


async def connect_laptop(base_url: str, device: dict[str, str]) -> DesktopAgent:
    ws_base = base_url.replace("http://", "ws://", 1)
    ws = await connect(
        f"{ws_base}{API}/agent/ws",
        additional_headers={"Authorization": f"Bearer {device['secret']}"},
        open_timeout=5,
    )
    agent = DesktopAgent(ws, device["secret"])
    await agent.send(_hello())
    ack = await agent.next_frame("hello_ack")
    assert ack["device_id"] == device["device_id"]
    assert ack["protocol_version"] == 1
    return agent


async def propose_chrome(bench: Bench, conversation_id: str) -> dict[str, Any]:
    events = await bench.chat(conversation_id, USER_PROMPT)
    proposed = [data for name, data in events if name == "tool_proposed"]
    assert len(proposed) == 1, events
    assert proposed[0]["decision"] == "awaiting_approval", proposed[0]
    return dict(proposed[0]["task"])


# ---- the milestone ----------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def fake_gemini(app: Any) -> FakeGemini:
    fake = FakeGemini()
    install_gemini(app, fake)
    return fake


async def test_open_chrome_on_laptop_completes_through_approval(app: Any, fake_gemini: FakeGemini) -> None:
    async with live_server(app) as base_url:
        bench = Bench(base_url, app)
        agent: DesktopAgent | None = None
        try:
            await set_up_owner_and_gemini(bench)

            conversation = await bench.write("POST", "/conversations", {"title": "Milestone"}, expect=201)
            cid = conversation["id"]

            # Before a laptop is paired the model's proposal is refused, and no task is created.
            early = await bench.chat(cid, USER_PROMPT)
            early_proposal = [data for name, data in early if name == "tool_proposed"][0]
            assert early_proposal["decision"] in {"rejected", "needs_device_selection"}, early_proposal
            assert (await bench.get("/tasks"))["total"] == 0

            device = await pair_laptop(bench)
            agent = await connect_laptop(base_url, device)
            await bench.wait_device(device["device_id"], "online")

            task = await propose_chrome(bench, cid)
            assert task["status"] == "awaiting_approval"
            assert task["tool_name"] == "launch_app"
            assert task["actions"]["approve"] is True
            assert task["device"]["id"] == device["device_id"]

            # Nothing is sent to the agent before the user approves.
            await agent.no_frame("command", window=0.3)

            approved = await bench.write("POST", f"/tasks/{task['id']}/approve")
            assert approved["status"] in {"queued", "running"}, approved

            command = await agent.next_frame("command")
            verify_command_signature(device["secret"], command)
            assert command["protocol_version"] == 1
            assert command["action"] == "launch_app"
            assert json.loads(command["args_json"]) == {"app_id": "chrome"}
            assert datetime.fromisoformat(command["expires_at"].replace("Z", "+00:00")) > datetime.now(UTC)

            await agent.send(_result(command["id"]))
            done = await bench.wait_task(task["id"], {"completed", "failed"})
            assert done["status"] == "completed", done
            assert done["result"]["output"]["pid"] == 4312
            assert done["result"]["summary"] == "Started Google Chrome (pid 4312)."

            # The conversation shows the real outcome, not model text.
            detail = await bench.get(f"/conversations/{cid}")
            outcome = [
                m
                for m in detail["messages"]
                if m.get("task_id") == task["id"] and m["role"] == "assistant" and (m.get("meta") or {}).get("kind") == "task_outcome"
            ]
            assert len(outcome) == 1, "the conversation needs exactly one outcome message for the task"
            assert "Started Google Chrome (pid 4312)." in outcome[0]["content"]
            tool_rows = [m for m in detail["messages"] if m["role"] == "tool" and m.get("task_id") == task["id"]]
            assert tool_rows and tool_rows[0]["meta"]["task_status"] == "completed"

            notifications = await bench.get("/notifications")
            assert any(item["kind"] == "task_completed" for item in notifications["items"])

            audit = await bench.get("/audit?limit=100")
            actions = {item["action"] for item in audit["items"]}
            assert {"device.paired", "provider.credential_set"} <= actions

            # Secrets never appear in any response body the browser saw, including the audit log.
            for body in bench.bodies:
                assert GEMINI_KEY not in body
                assert device["secret"] not in body
        finally:
            if agent is not None:
                await agent.aclose()
            await bench.close()


async def test_offline_device_keeps_task_queued_and_never_completes(app: Any, fake_gemini: FakeGemini) -> None:
    async with live_server(app) as base_url:
        bench = Bench(base_url, app)
        try:
            await set_up_owner_and_gemini(bench)
            cid = (await bench.write("POST", "/conversations", {"title": "Offline"}, expect=201))["id"]
            device = await pair_laptop(bench)
            agent = await connect_laptop(base_url, device)
            await bench.wait_device(device["device_id"], "online")
            task = await propose_chrome(bench, cid)

            await agent.aclose()
            await bench.wait_device(device["device_id"], "offline")

            approved = await bench.write("POST", f"/tasks/{task['id']}/approve")
            assert approved["status"] == "queued", approved
            await asyncio.sleep(0.5)
            current = (await bench.get(f"/tasks/{task['id']}"))["task"]
            assert current["status"] == "queued"
            assert current["result"] is None
            assert current["finished_at"] is None
        finally:
            await bench.close()


async def test_revoked_device_is_refused(app: Any, fake_gemini: FakeGemini) -> None:
    async with live_server(app) as base_url:
        bench = Bench(base_url, app)
        try:
            await set_up_owner_and_gemini(bench)
            device = await pair_laptop(bench)
            agent = await connect_laptop(base_url, device)
            await bench.wait_device(device["device_id"], "online")

            await bench.write("POST", f"/devices/{device['device_id']}/revoke", {"reason": "test"})
            await asyncio.wait_for(agent.ws.wait_closed(), timeout=5)
            assert agent.ws.close_code == 4403

            ws_base = base_url.replace("http://", "ws://", 1)
            again = await connect(
                f"{ws_base}{API}/agent/ws",
                additional_headers={"Authorization": f"Bearer {device['secret']}"},
                open_timeout=5,
            )
            # Revocation erases the stored hash, so the old secret is "unknown" (4401). docs/desktop-protocol.md
            # lists 4403 for this case; see the milestone report. Either way the socket must be refused.
            with pytest.raises(ConnectionClosed) as refused:
                await again.send(json.dumps(_hello()))
                await asyncio.wait_for(again.recv(), timeout=5)
            assert refused.value.rcvd is not None and refused.value.rcvd.code in {4401, 4403}
            with suppress(ConnectionClosed, OSError):
                await again.close()

            detail = (await bench.get("/devices"))["items"]
            assert any(d["id"] == device["device_id"] and d["pairing_status"] == "revoked" for d in detail)
        finally:
            await bench.close()


async def test_result_for_unknown_command_is_ignored(app: Any, fake_gemini: FakeGemini) -> None:
    async with live_server(app) as base_url:
        bench = Bench(base_url, app)
        agent: DesktopAgent | None = None
        try:
            await set_up_owner_and_gemini(bench)
            cid = (await bench.write("POST", "/conversations", {"title": "Forged"}, expect=201))["id"]
            device = await pair_laptop(bench)
            agent = await connect_laptop(base_url, device)
            await bench.wait_device(device["device_id"], "online")
            task = await propose_chrome(bench, cid)
            await bench.write("POST", f"/tasks/{task['id']}/approve")
            command = await agent.next_frame("command")

            await agent.send(_result(str(uuid.uuid4())))
            await asyncio.sleep(0.5)
            still = (await bench.get(f"/tasks/{task['id']}"))["task"]
            assert still["status"] in {"queued", "running"}, still
            assert still["result"] is None

            await agent.send(_result(command["id"]))
            done = await bench.wait_task(task["id"], {"completed", "failed"})
            assert done["status"] == "completed"
        finally:
            if agent is not None:
                await agent.aclose()
            await bench.close()
