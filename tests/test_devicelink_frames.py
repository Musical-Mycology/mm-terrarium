"""DeviceLinkAgent frame streaming: render on tick, emit only on change."""

import pytest

pytest.importorskip("luxaeterna")

from bits.test_bit import TestBit
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import FakeServer


# Real wall-clock time (the DeviceLinkAgent/DeviceBridge default) can't
# advance the welcome signature or the LOADING->RUNNING transition within a
# synchronous poll() loop -- TestBit's welcome duration is a real 1.5s and a
# test body runs in under a millisecond. Same fake-clock idiom as
# tests/test_device_bridge.py, for the same reason: the plan's own deviation
# note says nothing in this slice measures timing, so a fast virtual clock is
# fine here. Kept as an explicit shared schedule (not a fixture-private
# instance) so two independently constructed rigs can be driven off exactly
# the same clock values -- see test_cue_changes_the_frame, which needs that
# to make a fair differential comparison.
CLOCK_SCHEDULE = [i * 2.0 for i in range(1000)]


def _make_rig():
    """A freshly joined device on its own iterator over CLOCK_SCHEDULE. Two
    calls to this function see identical clock reads call-for-call, since
    render_into() is the only thing that consumes the clock and each poll()
    triggers exactly one render_into() per bridge."""
    clk = iter(CLOCK_SCHEDULE).__next__
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


@pytest.fixture
def joined():
    return _make_rig()


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


def _run_hue_sweep(gs, server, agent, apply_cues: bool) -> None:
    """The tick sequence test_cue_changes_the_frame drives either way: one
    settle poll, then 4 x 3 polls. apply_cues controls only whether
    gs.on_light_cue is actually called each outer iteration -- the number
    and timing of render_into (and hence clock) calls is identical either
    way, which is what makes the two runs comparable."""
    agent.poll()
    server.sent.clear()
    for cc in (0, 40, 80, 120):
        if apply_cues:
            gs.on_light_cue("ie1", 0xB0, 74, cc)
        for _ in range(3):
            agent.poll()


def test_cue_changes_the_frame():
    """aurora breathes continuously on its own clock (luxaeterna's
    _AURORA_BREATHE in synth/presets.py), independent of any MIDI input, so
    with this fixture's fast fake clock a plain "the frames aren't all
    identical" assertion is satisfied by breathing alone -- it would still
    pass with on_light_cue/feed_midi/the whole cue path completely broken.

    Isolate the cue path's actual contribution differentially instead: build
    two rigs off the *same* explicit CLOCK_SCHEDULE, drive one with no cues
    and one with the cc:74 sweep, and require the two frame sequences to
    diverge. Breathing is identical in both runs (same clock schedule, same
    number of render_into calls in each), so any divergence between the two
    sequences must come from the cue path."""
    gs1, server1, agent1 = _make_rig()
    _run_hue_sweep(gs1, server1, agent1, apply_cues=False)
    frames_no_cue = [tuple(m["args"][0]) for m in server1.addressed("/ie1/leds")]

    gs2, server2, agent2 = _make_rig()
    _run_hue_sweep(gs2, server2, agent2, apply_cues=True)
    frames_with_cue = [tuple(m["args"][0]) for m in server2.addressed("/ie1/leds")]

    assert frames_with_cue != frames_no_cue, (
        "the cc:74 sweep must change the rendered frames beyond what "
        "aurora's own breathing already produces")


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
