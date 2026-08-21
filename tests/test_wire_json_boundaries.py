"""The two boundaries with real non-Python consumers, tested end to end.

test_console_snapshot_survives_an_infinite_bit_status is the regression
test for the 2026-08-19 live defect: a Console connected to a healthy stack
rendered every panel empty because TestBit.status()'s run_duration is
float("inf") under --hold and the snapshot therefore failed JSON.parse.
"""

import json

import pytest

from console.agent import ConsoleAgent
from control.engine import GameServer
from bits.test.test_bit import TestBit


def strict_loads(text: str):
    def reject(token):
        raise ValueError(f"non-JSON token {token!r}")
    return json.loads(text, parse_constant=reject)


class _FakeServer:
    """The socket half ConsoleAgent talks to, reduced to what it calls."""

    def __init__(self):
        self.sent = []

    def drain_new_clients(self):
        return []

    def drain_inbound(self):
        return []

    def send(self, client, msg):
        self.sent.append(msg)

    def broadcast(self, msg):
        self.sent.append(msg)


def test_console_snapshot_survives_an_infinite_bit_status():
    """The exact live failure: an unbounded run_duration must not make the
    whole snapshot unparseable."""
    gs = GameServer({"TestBit": lambda: TestBit(run_duration=float("inf"))})
    gs.load_bit("TestBit")
    agent = ConsoleAgent(gs, _FakeServer())
    snapshot = agent.snapshot()

    assert snapshot["bit_status"]["run_duration"] == float("inf")   # source is unchanged

    # Serialise it exactly as ConsoleServer does, and parse it strictly.
    from control.wire_json import dumps
    text = dumps(snapshot)
    assert "Infinity" not in text
    assert strict_loads(text)["bit_status"]["run_duration"] is None


def test_console_server_send_uses_the_guarded_serialiser():
    """Pins the call site itself, not just the helper: a future edit that
    reverts console/server.py to json.dumps fails here."""
    import console.server as server_mod
    src = (server_mod.__file__)
    text = open(src).read()
    assert "json.dumps(" not in text, "console/server.py must use wire_json.dumps"


def test_devicelink_payload_survives_a_non_finite_value():
    """The device wire has real non-Python consumers (phones parsing with
    JSON.parse, Dart clients with jsonDecode), so it gets the same guard."""
    import devicelink.server as server_mod
    text = open(server_mod.__file__).read()
    assert "json.dumps(" not in text, "devicelink/server.py must use wire_json.dumps"

    from control.wire_json import dumps
    msg = {"timestamp": float("inf"), "address": "/ie1/leds",
           "typespec": "b", "args": [1, 2, 3]}
    out = dumps(msg)
    assert "Infinity" not in out
    assert strict_loads(out)["timestamp"] is None


@pytest.mark.parametrize("module_name", [
    "uplink.transport", "devicelink.o2_transport", "capture.store",
])
def test_remaining_outbound_sites_use_the_guarded_serialiser(module_name):
    import importlib
    mod = importlib.import_module(module_name)
    text = open(mod.__file__).read()
    assert "json.dumps(" not in text, f"{module_name} must use wire_json.dumps"
