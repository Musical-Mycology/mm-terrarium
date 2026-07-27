"""DeviceLinkAgent frame streaming: render on tick, emit only on change."""

import pytest

pytest.importorskip("luxaeterna")

from bits.test_bit import TestBit
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import FakeServer


@pytest.fixture
def joined():
    # Real wall-clock time (the DeviceLinkAgent/DeviceBridge default) can't
    # advance the welcome signature or the LOADING->RUNNING transition
    # within a synchronous poll() loop -- TestBit's welcome duration is a
    # real 1.5s and this fixture's whole test body runs in under a
    # millisecond. Same fake-clock idiom as tests/test_device_bridge.py,
    # for the same reason: the plan's own deviation note says nothing in
    # this slice measures timing, so a fast virtual clock is fine here.
    clk = iter([i * 2.0 for i in range(1000)]).__next__
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("test_bit")
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    agent.poll()
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    server.sent.clear()
    return gs, server, agent


def test_emits_a_36_channel_frame(joined):
    gs, server, agent = joined
    for _ in range(5):
        agent.poll()
    frames = server.addressed("/ie1/leds")
    assert frames, "expected at least one LED frame"
    assert len(frames[0]["args"][0]) == 36
    assert all(0 <= v <= 255 for v in frames[0]["args"][0])


def test_unchanged_frame_is_sent_once(joined):
    """aurora breathes continuously, so a real session never renders the same
    frame twice. Substitute a constant renderer to test emit-on-change."""
    gs, server, agent = joined

    class ConstantSession:
        state = "running"

        def render_into(self, universe):
            universe.set_range(0, bytes([7] * 36))

    agent.bridges["ie1"].session = ConstantSession()
    for _ in range(4):
        agent.poll()
    frames = server.addressed("/ie1/leds")
    assert len(frames) == 1, "a constant frame must be sent once, not per tick"
    assert frames[0]["args"][0] == [7] * 36


def test_cue_changes_the_frame(joined):
    gs, server, agent = joined
    agent.poll()
    server.sent.clear()
    for cc in (0, 40, 80, 120):
        gs.on_light_cue("ie1", 0xB0, 74, cc)
        for _ in range(3):
            agent.poll()
    frames = [tuple(m["args"][0]) for m in server.addressed("/ie1/leds")]
    assert len(set(frames)) > 1, "hue sweep should produce distinct frames"


def test_released_device_stops_emitting(joined):
    gs, server, agent = joined
    gs.abort()
    server.sent.clear()
    for _ in range(3):
        agent.poll()
    assert server.addressed("/ie1/leds") == []


def test_a_raising_session_does_not_break_poll(joined):
    gs, server, agent = joined

    class Boom:
        state = "running"

        def render_into(self, universe):
            raise RuntimeError("boom")

    agent.bridges["ie1"].session = Boom()
    agent.poll()          # must not raise
