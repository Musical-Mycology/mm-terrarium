"""DeviceLinkAgent: inbound dispatch and the registration path, against an
in-process fake server (no sockets -- see test_devicelink_server.py)."""

import pytest

# devicelink.agent imports harness.device_bridge, which needs the sibling
# luxaeterna checkout. Guard it the same way tests/test_device_bridge.py and
# tests/test_led_smoke.py do, so the core suite still collects without it
# (requirements-dev.txt states that contract).
pytest.importorskip("luxaeterna")

from bits.test_bit import TestBit
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent


class FakeServer:
    """Same tick-thread API as DeviceLinkServer, no sockets."""

    def __init__(self):
        self.new_clients = []
        self.inbound = []
        self.sent = []          # (client, msg)
        self.broadcasts = []

    def drain_new_clients(self):
        out, self.new_clients = self.new_clients, []
        return out

    def drain_inbound(self):
        out, self.inbound = self.inbound, []
        return out

    def send(self, client, msg):
        self.sent.append((client, msg))

    def broadcast(self, msg):
        self.broadcasts.append(msg)

    # --- test helpers ---
    def arrive(self, client):
        self.new_clients.append(client)

    def deliver(self, client, address, typespec="", args=None):
        self.inbound.append((client, {"address": address,
                                      "typespec": typespec,
                                      "args": args or []}))

    def addressed(self, address):
        return [m for _, m in self.sent if m["address"] == address]


@pytest.fixture
def rig():
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    return gs, server, agent


def _hello(server, agent, client="c1", dev="ie1"):
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", [dev, "sim", "1"])
    agent.poll()


def test_hello_registers_the_device_in_the_pool(rig):
    gs, server, agent = rig
    _hello(server, agent)
    assert [d.dev for d in gs.devices.all()] == ["ie1"]


def test_granted_join_sends_role_blob_byte_identical(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    roles = server.addressed("/ie1/role")
    assert len(roles) == 1
    blob = roles[0]["args"][0]
    assert blob["role"] == "player"
    assert blob["light_manifest"]["bit_name"] == "test_bit"
    assert blob["light_manifest"]["instruments"][0]["instrument"] == "aurora"


def test_granted_join_builds_a_light_session(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert agent.bridges["ie1"].session is not None


def test_denied_join_sends_deny_with_engine_reason(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "NO_SUCH_NODE"])
    agent.poll()

    denies = server.addressed("/ie1/deny")
    assert denies[0]["args"][0] == "no such node"
    assert "ie1" not in agent.bridges


def test_join_with_no_bit_loaded_is_denied(rig):
    gs, server, agent = rig
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert server.addressed("/ie1/deny")[0]["args"][0] == \
        "no Bit accepting registrations"


def test_verb_refusal_becomes_an_error_event(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_JAM_NODE"])
    agent.poll()
    server.deliver("c1", "/game/wiggle", "s", ["ie1"])
    agent.poll()
    assert server.addressed("/ie1/error")[0]["args"] == \
        ["wiggle", "unknown verb 'wiggle'"]


def test_malformed_envelope_is_dropped_silently(rig):
    gs, server, agent = rig
    server.arrive("c1")
    server.inbound.append(("c1", {"nonsense": True}))
    agent.poll()          # must not raise
    assert server.sent == []


def test_release_sends_release_and_clears_the_bridge(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    gs.abort()
    assert server.addressed("/ie1/release")
    assert "ie1" not in agent.bridges


def test_light_cue_reaches_the_devices_session(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    session = agent.bridges["ie1"].session
    gs.on_light_cue("ie1", 0xB0, 74, 100)     # must not raise
    assert session is agent.bridges["ie1"].session
