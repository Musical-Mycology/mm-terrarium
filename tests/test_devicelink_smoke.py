"""End-to-end: a real websocket client drives registration, receives its
config blob, tilts, and watches the LEDs change."""

import json
import time

import pytest

# harness.devicelink_smoke pulls in devicelink.agent -> harness.device_bridge,
# which needs the sibling luxaeterna checkout. Guard it the same way
# tests/test_devicelink_agent.py, tests/test_device_bridge.py, and
# tests/test_led_smoke.py do, so the core suite still collects without it
# (requirements-dev.txt states that contract).
pytest.importorskip("luxaeterna")

from websockets.sync.client import connect

from harness.devicelink_smoke import build


@pytest.fixture
def rig():
    gs, server, agent = build(host="127.0.0.1", port=0, run_duration=60.0)
    yield gs, server, agent
    server.stop()


def _send(client, address, typespec, args):
    client.send(json.dumps({"timestamp": 0.0, "address": address,
                            "typespec": typespec, "args": args}))


def _collect(client, agent, gs, ticks=12):
    """Interleave engine ticks with reads so the sync client sees output."""
    out = []
    for _ in range(ticks):
        agent.poll()
        gs.tick(0.02)
        try:
            while True:
                out.append(json.loads(client.recv(timeout=0.05)))
        except TimeoutError:
            pass
    return out


def test_tilt_drives_a_visible_hue_change(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
        _send(client, "/game/hello", "sss", ["ie1", "sim", "1"])
        _send(client, "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
        msgs = _collect(client, agent, gs)

        roles = [m for m in msgs if m["address"] == "/ie1/role"]
        assert len(roles) == 1
        assert roles[0]["args"][0]["role"] == "player"

        gs.run()
        frames = []
        for cc in (0, 30, 60, 90, 120):
            _send(client, "/game/tilt", "sf", ["ie1", float(cc)])
            frames += [tuple(m["args"][0])
                       for m in _collect(client, agent, gs)
                       if m["address"] == "/ie1/leds"]

    assert len(set(frames)) > 1, "tilting should change the rendered frame"


def test_denied_join_reports_the_engine_reason(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
        _send(client, "/game/hello", "sss", ["ie9", "sim", "1"])
        _send(client, "/game/join", "ss", ["ie9", "NOPE"])
        msgs = _collect(client, agent, gs)
    denies = [m for m in msgs if m["address"] == "/ie9/deny"]
    assert denies[0]["args"][0] == "no such node"
