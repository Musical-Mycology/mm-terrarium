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
from control.state import State
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


class RaisingSendServer(FakeServer):
    """A FakeServer whose send() always raises -- exercises the boundary
    guarantee that a transport failure notifying one device can never
    strand another device's bridge or wedge the engine in UNLOADING."""

    def send(self, client, msg):
        raise RuntimeError("transport exploded")


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


# A fake clock with coarse-enough steps that the ~0.6s sys:closing
# GainSignature (luxaeterna's synth/status.py) finishes within a handful of
# render_into() calls -- see tests/test_devicelink_frames.py for the
# detailed fade-then-release test; this file's release tests only need to
# know it eventually finishes, not observe the fade frames themselves.
_CLOSING_CLOCK_SCHEDULE = [i * 0.1 for i in range(5000)]

# Released devices now stay in DeviceLinkAgent.bridges until their closing
# fade finishes (devicelink/agent.py _on_release/_render_frames), so
# release-path tests must drive poll() forward instead of asserting
# immediately after gs.abort(). Bounded so a regression here fails fast
# instead of hanging.
_CLOSING_POLL_LIMIT = 200


def _drain_releases(agent, devs, limit=_CLOSING_POLL_LIMIT):
    """Poll until every dev in `devs` is gone from agent.bridges (its
    closing fade -- or the stuck-session guard -- has finished), or fail."""
    remaining = set(devs)
    for _ in range(limit):
        agent.poll()
        remaining &= set(agent.bridges)
        if not remaining:
            return
    pytest.fail(f"release never finished for {remaining}")


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


def test_release_sends_release_and_clears_the_bridge():
    """gs.abort() starts the release path (DeviceLinkAgent._on_release),
    which now plays the device's closing fade before dropping it -- see
    tests/test_devicelink_frames.py for the detailed fade-then-release test.
    A fake clock (rather than the `rig` fixture's real one) is needed so the
    fade actually finishes within a bounded number of poll()s here."""
    clk = iter(_CLOSING_CLOCK_SCHEDULE).__next__
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    gs.abort()
    _drain_releases(agent, ["ie1"])
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


def test_a_raising_transport_does_not_strand_any_device_on_release():
    """A release-notify send failure for one device must not leave any
    device's bridge stranded, nor wedge the engine outside IDLE -- the
    boundary-rule-2 guarantee that both DeviceLinkAgent._on_release and
    GameServer._unload's release loop now provide independently. This holds
    all the way through the closing fade too: every /<dev>/leds and the
    eventual /<dev>/release send can fail (RaisingSendServer always raises)
    without preventing the fade from finishing or the bridge from being
    dropped -- _finish_release's map cleanup does not depend on the send
    succeeding, see devicelink/agent.py."""
    clk = iter(_CLOSING_CLOCK_SCHEDULE).__next__
    gs = GameServer({"test_bit": TestBit})
    server = RaisingSendServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("test_bit")

    _hello(server, agent, client="c1", dev="ie1")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    _hello(server, agent, client="c2", dev="ie2")
    server.deliver("c2", "/game/join", "ss", ["ie2", "TEST_JAM_NODE"])
    agent.poll()

    assert set(agent.bridges) == {"ie1", "ie2"}

    gs.run()
    gs.abort()          # must not raise, must not wedge

    assert gs.state == State.IDLE
    _drain_releases(agent, ["ie1", "ie2"])   # must not raise, must not wedge
    assert agent.bridges == {}


def test_failing_on_grant_sends_error_not_role_and_omits_the_bridge(rig, monkeypatch):
    gs, server, agent = rig
    gs.load_bit("test_bit")

    class ExplodingBridge:
        def __init__(self, capability=None, clock=None):
            pass

        def on_grant(self, result):
            raise RuntimeError("boom")

    monkeypatch.setattr("devicelink.agent.DeviceBridge", ExplodingBridge)
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    assert server.addressed("/ie1/role") == []
    assert server.addressed("/ie1/error")[0]["args"] == \
        ["role", "could not build light session"]
    assert "ie1" not in agent.bridges
