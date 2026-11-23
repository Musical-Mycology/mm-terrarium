"""UplinkAgent over the real WebSocketTransport against a scripted localhost
websockets server (final fix wave item 12): handshake headers, frame sizes,
resync order, confirmation of every completion, session_replaced back-off,
and a 401 refusal."""
import http
import json
import logging
import threading
import time

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from bits.test.test_bit import TestBit
from control.engine import GameServer
from control.wire_json import dumps
from tests.fakes import FakeClock
from uplink.journal import Journal
from uplink.link import UplinkAgent
from uplink.protocol import UplinkIdentity
from uplink.transport import WebSocketTransport, frame_size

SECRET = "e2e-secret-" + "ab" * 16
IDENTITY = UplinkIdentity("mm", "main-stage", SECRET)
DEADLINE_SECONDS = 5.0


def _stored(cid, seq):
    return {"event": "bit_completed", "result": None,
            "bit": {"name": "old", "version": "0"}, "players": [],
            "completion_id": cid, "completed_at": "2026-11-22T09:00:00Z",
            "boot_id": "earlier-boot", "seq": seq}


class ScriptedBroker:
    """Records headers and frames; confirms each bit_completed; once
    `replace` is set, sends session_replaced and closes."""

    def __init__(self):
        self.headers = []
        self.frames = []
        self.problems = []
        self.replace = threading.Event()
        self.connections = 0

    def handler(self, connection):
        self.connections += 1
        self.headers.append({name: connection.request.headers.get(name)
                             for name in ("X-Fairyring-Tenant",
                                          "X-Fairyring-Terrarium",
                                          "X-Fairyring-Secret")})
        while True:
            if self.replace.is_set():
                connection.send(json.dumps({"command": "session_replaced"}))
                connection.close()
                return
            try:
                raw = connection.recv(timeout=0.01)
            except TimeoutError:
                continue
            except ConnectionClosed:
                return
            if not isinstance(raw, str):
                self.problems.append("binary frame")
                continue
            frame = json.loads(raw)
            size = len(raw.encode("utf-8"))
            if size != frame_size(frame):
                self.problems.append(f"size {size} != frame_size "
                                     f"{frame_size(frame)} for {frame}")
            if size > 32000:
                self.problems.append(f"frame of {size} bytes")
            self.frames.append(frame)
            if frame.get("event") == "bit_completed":
                connection.send(json.dumps({
                    "command": "completions_stored",
                    "completion_ids": [frame["completion_id"]]}))


def _serve(handler, **kwargs):
    server = serve(handler, "localhost", 0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"ws://localhost:{server.socket.getsockname()[1]}"


def _pump_until(agent, condition):
    end = time.monotonic() + DEADLINE_SECONDS
    while time.monotonic() < end:
        agent.maintain_connection()
        agent.poll()
        if condition():
            return True
        time.sleep(0.005)
    return False


def test_agent_over_the_real_transport(tmp_path):
    broker = ScriptedBroker()
    server, thread, uri = _serve(broker.handler)
    journal = Journal(str(tmp_path / "uplink_journal.jsonl"))
    journal.append(_stored("c-old-1", 3))
    journal.append(_stored("c-old-2", 4))
    clock = FakeClock()
    gs = GameServer(bit_registry={"test_bit": TestBit})
    transport = WebSocketTransport(uri, headers=IDENTITY.headers())
    agent = UplinkAgent(gs, transport, identity=IDENTITY, journal=journal,
                        time_source=clock, boot_id="boot-e2e")
    try:
        assert _pump_until(agent, lambda: journal.entries() == [])

        # A live completion is journalled, sent and confirmed too.
        gs.load_bit("test_bit")
        gs.run()
        gs.tick(3.0)
        assert len(journal.entries()) == 1
        assert _pump_until(agent, lambda: journal.entries() == [])

        broker.replace.set()
        assert _pump_until(agent, lambda: not transport.connected)
        replaced_at = clock.now
        assert agent._next_attempt_at - replaced_at == 60.0
        clock.now = replaced_at + 59.9
        agent.maintain_connection()
        assert transport.connected is False
        assert broker.connections == 1
    finally:
        transport.close()
        server.shutdown()
        thread.join(timeout=2)

    assert broker.problems == []
    assert broker.headers == [{"X-Fairyring-Tenant": "mm",
                               "X-Fairyring-Terrarium": "main-stage",
                               "X-Fairyring-Secret": SECRET}]
    events = [f["event"] for f in broker.frames]
    applied = events.index("admin_devices_applied")
    assert events[:applied + 1] == ["state_changed", "admin_devices_applied"]
    assert broker.frames[applied + 1:applied + 3] == [
        _stored("c-old-1", 3), _stored("c-old-2", 4)]
    live = [f for f in broker.frames[applied + 3:]
            if f["event"] == "bit_completed"]
    assert len(live) == 1 and live[0]["boot_id"] == "boot-e2e"
    assert "heartbeat" not in events
    assert all(SECRET not in dumps(f) for f in broker.frames)


@pytest.fixture
def websockets_at_info():
    """What harness/terrarium_boot._build_uplink does at boot: at DEBUG the
    websockets library logs the handshake headers, secret included."""
    ws_logger = logging.getLogger("websockets")
    old = ws_logger.level
    ws_logger.setLevel(logging.INFO)
    yield
    ws_logger.setLevel(old)


def test_a_401_refusal_over_the_real_transport(tmp_path, caplog,
                                               websockets_at_info):
    def refuse(connection, request):
        return connection.respond(http.HTTPStatus.UNAUTHORIZED, "refused\n")

    def handler(connection):            # never reached
        raise AssertionError("handshake should have been refused")

    server, thread, uri = _serve(handler, process_request=refuse)
    path = tmp_path / "uplink_journal.jsonl"
    journal = Journal(str(path))
    journal.append(_stored("c-old-1", 3))
    before = path.read_bytes()
    transport = WebSocketTransport(uri, headers=IDENTITY.headers())
    agent = UplinkAgent(GameServer(bit_registry={"test_bit": TestBit}),
                        transport, identity=IDENTITY, journal=journal,
                        time_source=FakeClock(), boot_id="boot-e2e")
    try:
        with caplog.at_level(logging.DEBUG):
            agent.maintain_connection()          # must not raise
            agent.poll()
    finally:
        transport.close()
        server.shutdown()
        thread.join(timeout=2)

    assert transport.connected is False
    assert path.read_bytes() == before
    warnings = [r.getMessage() for r in caplog.records
                if r.name == "uplink.link" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "401" in warnings[0]
    assert "check [uplink] tenant_slug and secret, and [terrarium] name" in \
        warnings[0]
    assert SECRET not in caplog.text
