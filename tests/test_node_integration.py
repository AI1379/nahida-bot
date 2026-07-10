"""End-to-end integration test: Python Node Client <-> Gateway WebSocket.

This test stands up a real uvicorn server with the node WebSocket endpoint and
services wired in, then drives real ``NodeClient`` instances and raw
``websockets`` frames against it. It validates the full lifecycle:

  issue node token -> connect -> register -> invoke capability -> receive
  event -> submit input -> disconnect.

It exercises the same code paths the Rust/Tauri Desktop node will use, so it
serves as the contract reference for cross-language validation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
import uvicorn
import websockets
from fastapi import FastAPI

from nahida_bot.gateway.node_protocol.routes import router as node_ws_router
from nahida_bot.gateway.node_protocol.schemas import build_event
from nahida_bot.gateway.services.node_auth import NodeAuthService
from nahida_bot.gateway.services.node_invoker import NodeInvoker
from nahida_bot.gateway.services.node_registry import NodeRegistry
from nahida_bot.node.capabilities import CapabilityRegistry
from nahida_bot.node.client import NodeClient


class _RecordingInputSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def submit(self, *, node_id: str, session_id: str, text: str) -> None:
        self.calls.append((node_id, session_id, text))


@pytest.fixture
def gateway_app() -> FastAPI:
    app = FastAPI()
    app.include_router(node_ws_router)
    app.state.node_registry = NodeRegistry(heartbeat_interval_ms=60000)
    app.state.node_auth = NodeAuthService()
    app.state.node_input_sink = _RecordingInputSink()
    app.state.node_invoker = NodeInvoker(
        app.state.node_registry,
        input_sink=app.state.node_input_sink,
    )
    return app


@pytest.fixture
async def server_url(gateway_app: FastAPI) -> str:
    """Start uvicorn on an ephemeral port and return the base ws:// URL.

    Runs the server as a task on the same event loop as the tests so that
    cross-loop future wakeups (invoker <-> node dispatcher) work correctly.
    """

    config = uvicorn.Config(
        gateway_app, host="127.0.0.1", port=0, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())

    # Wait for bind and discover the actual port.
    port: int | None = None
    for _ in range(100):
        if server.started:
            port = _bound_port(server)
            if port is not None:
                break
        await asyncio.sleep(0.05)
    assert port is not None, "uvicorn failed to bind"

    yield f"ws://127.0.0.1:{port}"

    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await serve_task


def _bound_port(server: uvicorn.Server) -> int | None:
    """Discover the actual port uvicorn bound (it uses port=0 -> ephemeral)."""
    for server_obj in getattr(server, "servers", []) or []:
        for sock in server_obj.sockets:
            with contextlib.suppress(Exception):
                return sock.getsockname()[1]
    return None


@pytest.fixture
def node_token(gateway_app: FastAPI) -> str:
    full_token, _ = gateway_app.state.node_auth.issue_node_token(node_id="test-node")
    return full_token


# -- Frame-level tests (raw websockets client) ----------------------------


@pytest.mark.asyncio
async def test_register_round_trip_raw(server_url: str, node_token: str) -> None:
    async with websockets.connect(
        f"{server_url}/api/nodes/ws?token={node_token}"
    ) as ws:
        request = {
            "version": "1.0",
            "kind": "request",
            "id": "req_reg_1",
            "method": "node.register",
            "payload": {
                "node_id": "test-node",
                "display_name": "Test Node",
                "node_type": "desktop",
                "capabilities": [
                    {"name": "desktop.live2d.set_expression", "risk": "low"}
                ],
                "metadata": {"platform": "test"},
            },
        }
        await ws.send(json.dumps(request))
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        envelope = json.loads(raw)

        assert envelope["kind"] == "response"
        assert envelope["id"] == "req_reg_1"
        assert envelope["ok"] is True
        assert envelope["payload"]["accepted"] is True
        assert "session_id" in envelope["payload"]


@pytest.mark.asyncio
async def test_register_rejects_token_node_id_mismatch(
    server_url: str, node_token: str, gateway_app: FastAPI
) -> None:
    async with websockets.connect(
        f"{server_url}/api/nodes/ws?token={node_token}"
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "version": "1.0",
                    "kind": "request",
                    "id": "req_reg_mismatch",
                    "method": "node.register",
                    "payload": {
                        "node_id": "other-node",
                        "display_name": "Wrong Node",
                    },
                }
            )
        )
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        envelope = json.loads(raw)

        assert envelope["kind"] == "response"
        assert envelope["id"] == "req_reg_mismatch"
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "register_rejected"
        assert gateway_app.state.node_registry.get_online_session("other-node") is None
        assert gateway_app.state.node_registry.get_online_session("test-node") is None


@pytest.mark.asyncio
async def test_auth_rejected_without_token(server_url: str) -> None:
    with pytest.raises(Exception):
        async with websockets.connect(f"{server_url}/api/nodes/ws"):
            pass


@pytest.mark.asyncio
async def test_unregistered_request_is_rejected(
    server_url: str, node_token: str
) -> None:
    async with websockets.connect(
        f"{server_url}/api/nodes/ws?token={node_token}"
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "version": "1.0",
                    "kind": "request",
                    "id": "req_input_before_register",
                    "method": "node.input.submit",
                    "payload": {
                        "session_id": "milky:private:10001",
                        "text": "hello",
                    },
                }
            )
        )
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        envelope = json.loads(raw)

        assert envelope["kind"] == "response"
        assert envelope["id"] == "req_input_before_register"
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "not_registered"


@pytest.mark.asyncio
async def test_unregistered_node_rejected_for_invoke(
    server_url: str, node_token: str, gateway_app: FastAPI
) -> None:
    """Before registering, a node cannot process capability.invoke requests,
    but more importantly the invoker won't find an online node."""
    # No node registered yet -> invoke fails fast on the gateway side.
    result = await gateway_app.state.node_invoker.invoke(
        capability="anything", arguments={}
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "capability_not_found"


@pytest.mark.asyncio
async def test_gateway_replies_to_node_heartbeat_ping(
    server_url: str, node_token: str
) -> None:
    async with websockets.connect(
        f"{server_url}/api/nodes/ws?token={node_token}"
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "version": "1.0",
                    "kind": "request",
                    "id": "req_reg_heartbeat",
                    "method": "node.register",
                    "payload": {
                        "node_id": "test-node",
                        "capabilities": [],
                    },
                }
            )
        )
        await ws.recv()  # consume register response

        await ws.send(
            json.dumps(
                {
                    "version": "1.0",
                    "kind": "heartbeat",
                    "payload": {"type": "ping", "ts": 12345},
                }
            )
        )
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        envelope = json.loads(raw)

        assert envelope["kind"] == "heartbeat"
        assert envelope["payload"]["type"] == "pong"
        assert envelope["payload"]["echo_ts"] == 12345


@pytest.mark.asyncio
async def test_registered_node_input_reaches_configured_sink(
    server_url: str, node_token: str, gateway_app: FastAPI
) -> None:
    async with websockets.connect(
        f"{server_url}/api/nodes/ws?token={node_token}"
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "version": "1.0",
                    "kind": "request",
                    "id": "req_reg_input",
                    "method": "node.register",
                    "payload": {"node_id": "test-node", "capabilities": []},
                }
            )
        )
        await ws.recv()
        await ws.send(
            json.dumps(
                {
                    "version": "1.0",
                    "kind": "request",
                    "id": "req_input_1",
                    "method": "node.input.submit",
                    "payload": {
                        "session_id": "test:private:c1",
                        "text": "hello from node",
                    },
                }
            )
        )

        response = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
        assert response["ok"] is True
        assert response["payload"] == {"accepted": True}
        assert gateway_app.state.node_input_sink.calls == [
            ("test-node", "test:private:c1", "hello from node")
        ]


@pytest.mark.asyncio
async def test_duplicate_registration_notifies_and_closes_old_socket(
    server_url: str, node_token: str
) -> None:
    async with websockets.connect(
        f"{server_url}/api/nodes/ws?token={node_token}"
    ) as old_ws:
        await old_ws.send(
            json.dumps(
                {
                    "version": "1.0",
                    "kind": "request",
                    "id": "req_reg_old",
                    "method": "node.register",
                    "payload": {"node_id": "test-node", "capabilities": []},
                }
            )
        )
        await old_ws.recv()

        async with websockets.connect(
            f"{server_url}/api/nodes/ws?token={node_token}"
        ) as new_ws:
            await new_ws.send(
                json.dumps(
                    {
                        "version": "1.0",
                        "kind": "request",
                        "id": "req_reg_new",
                        "method": "node.register",
                        "payload": {
                            "node_id": "test-node",
                            "capabilities": [],
                        },
                    }
                )
            )
            await new_ws.recv()

            duplicate = json.loads(await asyncio.wait_for(old_ws.recv(), timeout=3.0))
            assert duplicate["kind"] == "event"
            assert duplicate["event"] == "node.duplicate_connection"
            with pytest.raises(websockets.ConnectionClosed):
                await asyncio.wait_for(old_ws.recv(), timeout=3.0)


@pytest.mark.asyncio
async def test_capability_invoke_full_round_trip(
    server_url: str, node_token: str, gateway_app: FastAPI
) -> None:
    """Gateway invokes a capability on the node; node replies; gateway gets it."""
    async with websockets.connect(
        f"{server_url}/api/nodes/ws?token={node_token}"
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "version": "1.0",
                    "kind": "request",
                    "id": "req_reg_2",
                    "method": "node.register",
                    "payload": {
                        "node_id": "test-node",
                        "capabilities": [{"name": "test.echo"}],
                    },
                }
            )
        )
        await ws.recv()  # consume register response
        await asyncio.sleep(0.05)  # let the registry settle

        invoke_task = asyncio.ensure_future(
            gateway_app.state.node_invoker.invoke(
                capability="test.echo", arguments={"msg": "hello"}
            )
        )

        invoke_request = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
        assert invoke_request["method"] == "capability.invoke"
        assert invoke_request["payload"]["capability"] == "test.echo"
        assert invoke_request["payload"]["arguments"]["msg"] == "hello"

        await ws.send(
            json.dumps(
                {
                    "version": "1.0",
                    "kind": "response",
                    "id": invoke_request["id"],
                    "ok": True,
                    "payload": {"echoed": "hello"},
                }
            )
        )

        result = await asyncio.wait_for(invoke_task, timeout=3.0)
        assert result.ok is True
        assert result.payload == {"echoed": "hello"}


# -- NodeClient SDK lifecycle (the cross-language contract reference) -----


@pytest.mark.asyncio
async def test_node_client_lifecycle(
    server_url: str, node_token: str, gateway_app: FastAPI
) -> None:
    """The real NodeClient SDK connects, registers and serves invocations."""
    caps = CapabilityRegistry()

    async def echo_handler(args):
        return {"echo": args.get("msg")}

    caps.register("test.echo", echo_handler)

    client = NodeClient(
        url=f"{server_url}/api/nodes/ws",
        token=node_token,
        node_id="test-node",
        node_type="worker",
        display_name="Test Worker",
        capabilities=caps,
        heartbeat_interval_ms=60000,
    )
    client_task = asyncio.create_task(client.start())

    try:
        await asyncio.wait_for(client._registered.wait(), timeout=5.0)
        assert client.registered

        result = await asyncio.wait_for(
            gateway_app.state.node_invoker.invoke(
                capability="test.echo", arguments={"msg": "ping"}
            ),
            timeout=5.0,
        )
        assert result.ok is True
        assert result.payload == {"echo": "ping"}
    finally:
        client.stop()
        try:
            await asyncio.wait_for(client_task, timeout=3.0)
        except TimeoutError:
            client_task.cancel()


@pytest.mark.asyncio
async def test_node_client_stops_after_duplicate_connection_event() -> None:
    class _Socket:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    client = NodeClient(
        url="ws://127.0.0.1/api/nodes/ws",
        token="token",
        node_id="test-node",
    )
    socket = _Socket()
    client._ws = socket

    await client._dispatch_event(build_event("node.duplicate_connection"))

    assert client._stopping.is_set()
    assert socket.closed is True
