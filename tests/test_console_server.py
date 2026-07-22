import json
import time

from urllib.request import urlopen

from websockets.sync.client import connect as ws_connect

from bits.test_bit import TestBit
from console.agent import ConsoleAgent
from console.server import ConsoleServer
from control.engine import GameServer


def test_get_root_serves_index_html():
    server = ConsoleServer(port=0)
    server.start()
    try:
        body = urlopen(f"http://127.0.0.1:{server.port}/").read().decode()
        assert "Terrarium Console" in body
        assert "new WebSocket" in body
    finally:
        server.stop()


def test_client_gets_snapshot_and_command_round_trips():
    gs = GameServer({"TestBit": TestBit})
    server = ConsoleServer(port=0)
    agent = ConsoleAgent(gs, server)
    server.start()
    try:
        with ws_connect(f"ws://127.0.0.1:{server.port}/ws") as ws:
            # _recv_event drives agent.poll() until the event arrives; the
            # first poll drains the new client and sends its snapshot.
            snap = _recv_event(ws, agent, "snapshot")
            assert snap["state"] == "IDLE"
            assert snap["installed_bits"] == ["TestBit"]

            ws.send(json.dumps({"command": "load_bit", "name": "TestBit"}))
            state = _recv_event(ws, agent, "state_changed")
            assert state["state"] in ("LOADING", "LOADED", "SETUP")
    finally:
        server.stop()


def _recv_event(ws, agent, event_name, timeout=2.0):
    """Interleave agent.poll() (tick thread work) with client recv until the
    named event arrives."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        agent.poll()
        try:
            raw = ws.recv(timeout=0.05)
        except TimeoutError:
            continue
        msg = json.loads(raw)
        if msg.get("event") == event_name:
            return msg
    raise AssertionError(f"did not receive {event_name!r} in time")
