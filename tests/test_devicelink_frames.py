"""DeviceLinkAgent frame streaming: render on tick, emit only on change."""

import pytest

pytest.importorskip("luxaeterna")

import devicelink.agent as devicelink_agent
from bits.test_bit import TestBit
from control.breath import BREATH_CC
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

# Fine-grained schedule for the release/closing-fade tests below: those need
# more than one render_into() call to elapse across the ~0.6s sys:closing
# GainSignature (luxaeterna's synth/status.py: _sig_closing), so the fade
# frames are actually observable across several poll()s rather than
# collapsing into a single one the way CLOCK_SCHEDULE's 2.0s steps do.
FINE_CLOCK_SCHEDULE = [i * 0.02 for i in range(5000)]


def _make_rig_running(clk):
    """Like _make_rig(), but parameterized on the clock and driven with
    poll() until the session actually reaches RUNNING before returning.
    CLOCK_SCHEDULE's 2.0s steps collapse the welcome/LOADING signature into
    a single render_into() call; a fine clock like FINE_CLOCK_SCHEDULE needs
    several poll()s to get there, so this can't just call poll() once like
    _make_rig() does."""
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("test_bit")
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    agent.poll()
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    for _ in range(200):
        if agent.bridges["ie1"].session.state == "running":
            break
        agent.poll()
    else:
        pytest.fail("session never reached RUNNING")
    gs.run()
    server.sent.clear()
    return gs, server, agent


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


def test_released_device_plays_the_closing_fade_then_stops():
    """The closing fade (LightSession.clear() -> CLOSING, driven by
    devicelink/agent.py's _on_release + _render_frames) must actually be
    rendered and sent over the wire before the device is released -- not
    silently skipped, which is what this test used to (wrongly) assert by
    checking for zero frames.

    bridge.on_release() only enqueues a ClearEvent; draining that queue and
    playing the fade out both happen inside render_into(), which only runs
    from _render_frames() on a later poll(). So the bridge/universe/last-
    frame entries must survive on_release and keep being rendered until the
    session's own state says CLOSING is done -- see the docs/superpowers
    design spec (2026-07-27-devicelink-tuneshroom-simulator-design.md) sec 6
    step 7."""
    clk = iter(FINE_CLOCK_SCHEDULE).__next__
    gs, server, agent = _make_rig_running(clk)

    gs.abort()
    assert "ie1" in agent.bridges, (
        "the bridge must still be present right after on_release -- it is "
        "popped only once the closing fade itself has finished rendering, "
        "not up front")
    assert server.addressed("/ie1/release") == [], (
        "/ie1/release must not fire until the fade is done")

    for _ in range(200):
        agent.poll()
        if "ie1" not in agent.bridges:
            break
    else:
        pytest.fail("released device's closing fade never finished")

    fade_frames = server.addressed("/ie1/leds")
    assert fade_frames, (
        "the closing fade must actually render and emit at least one "
        "/ie1/leds frame before the device is released")
    assert "ie1" not in agent.bridges
    assert server.addressed("/ie1/release"), (
        "/ie1/release must be sent once the fade has finished")

    server.sent.clear()
    for _ in range(3):
        agent.poll()
    assert server.addressed("/ie1/leds") == [], (
        "no further frames once the device is fully released")


def test_stuck_closing_session_is_still_released(joined):
    """A session that enqueues the clear but whose .state never reports
    leaving CLOSING (a misbehaving bit, or a signature that never
    completes) must not wedge the device, nor get rendered forever -- the
    bounded guard (_MAX_CLOSING_FRAMES in devicelink/agent.py) must still
    drop it and send /ie1/release."""
    gs, server, agent = joined

    class StuckSession:
        state = "closing"

        def clear(self) -> None:
            pass

        def render_into(self, universe) -> None:
            pass

    agent.bridges["ie1"].session = StuckSession()
    gs.abort()
    assert "ie1" in agent.bridges

    limit = devicelink_agent._MAX_CLOSING_FRAMES
    for _ in range(limit + 5):
        agent.poll()
        if "ie1" not in agent.bridges:
            break
    else:
        pytest.fail("a session stuck in CLOSING was never released")

    assert "ie1" not in agent.bridges
    assert server.addressed("/ie1/release")


def test_rejoin_mid_fade_does_not_destroy_the_new_session():
    """Regression for the _on_join staleness bug: if the same dev rejoins
    while its previous session is still in its closing fade (self._closing
    still has an entry for it, left over from the release that started the
    fade), _on_join must clear that entry when it installs the new bridge.
    Otherwise the very next poll()'s _check_closing_done sees the *new*
    session (state "loading", not CLOSING), wrongly concludes the old fade
    is done, and _finish_release() tears down the brand-new bridge/universe
    -- sending a spurious /ie1/release for a device the engine still
    believes is legitimately joined, and stranding it silently (no
    exception, no log) until it fully re-hellos."""
    clk = iter(FINE_CLOCK_SCHEDULE).__next__
    gs, server, agent = _make_rig_running(clk)

    gs.abort()   # ie1 starts fading; self._closing["ie1"] is now set
    assert "ie1" in agent._closing, (
        "setup precondition: the old session must still be mid-fade")

    gs.load_bit("test_bit")
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    assert "ie1" in agent.bridges, (
        "the freshly rejoined device's bridge must survive the next poll "
        "-- it must not be torn down by the previous session's now-stale "
        "closing-fade bookkeeping")
    assert server.addressed("/ie1/release") == [], (
        "no spurious /ie1/release for the new session")

    fed = []
    session = agent.bridges["ie1"].session
    real_feed_midi = session.feed_midi
    session.feed_midi = lambda *a: (fed.append(a), real_feed_midi(*a))[-1]

    gs.on_light_cue("ie1", 0xB0, 74, 64)
    assert fed, (
        "a light cue to the rejoined device must actually reach its new "
        "session's feed_midi, not silently no-op because the bridge was "
        "torn down by stale closing bookkeeping")


def test_a_raising_session_does_not_break_poll(joined):
    gs, server, agent = joined

    class Boom:
        state = "running"

        def render_into(self, universe):
            raise RuntimeError("boom")

    agent.bridges["ie1"].session = Boom()
    agent.poll()          # must not raise


def _make_timed_rig(now, horizon):
    """A joined device on a SETTABLE clock, with GameServer and
    DeviceLinkAgent sharing that clock and one horizon.

    `now` is a one-element list the test mutates; both the engine and the
    agent read it. Unlike _make_rig's clock iterator this can be moved
    backwards and forwards freely, which cue-timing tests need.

    Driven to RUNNING before returning, for the same reason
    _make_rig_running does it: a session still playing its welcome
    signature does not render a cue's effect, and a frozen clock never
    finishes that signature. Returns (gs, server, agent) matching this
    file's convention; the test reads now[0] afterwards for its own base
    time, since warm-up advanced it by an amount the test should not
    hardcode.
    """
    clk = lambda: now[0]
    gs = GameServer({"test_bit": TestBit}, cue_horizon=horizon, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, horizon=horizon, clock=clk)
    gs.load_bit("test_bit")
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    agent.poll()
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    for _ in range(200):
        if agent.bridges["ie1"].session.state == "running":
            break
        now[0] += 0.1
        agent.poll()
    else:
        pytest.fail("session never reached RUNNING")
    gs.run()
    server.sent.clear()
    agent._pending_at.clear()
    return gs, server, agent


HORIZON = 0.060


def _leds(server, dev="ie1"):
    return [m for d, m in server.sent if m["address"] == f"/{dev}/leds"]


def test_device_frame_carries_the_cues_own_time_not_the_render_clock():
    """A gesture's light must land at the time Control computed for THAT
    gesture. Stamping clock()+horizon here while the cue already carried
    origin+horizon charges the same constant twice, and the light would land
    at gesture + 2*horizon."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]
    at = (base - 0.005) + HORIZON          # 5 ms of delivery on the way up

    # Deviation from the brief: aurora's hue is behind a Smooth() one-pole
    # glide (luxaeterna's synth/presets.py), which only moves with real dt.
    # Rendering at the exact same `now` as the rig's last warm-up frame gives
    # dt ~= 0, so the fed hue would not visibly move any byte of the 36-
    # channel frame -- unrelated to the cue-timing behavior under test. Move
    # the clock first so the glide has room to actually change the frame;
    # `at` stays anchored to the original `base`, so it stays well in the
    # past of the advanced `now` and the gesture-immediate-feed path (which
    # this test is not itself about) still applies.
    now[0] = base + 0.5
    gs.on_light_cue("ie1", 0xB0, 74, 100, at)
    agent.poll()

    leds = _leds(server)
    assert leds, "the cue should have changed the rendered frame"
    assert leds[-1]["timestamp"] == pytest.approx(at)


def test_frame_with_no_cue_behind_it_carries_the_render_clock():
    """A breath-only frame is a STREAM frame: its origin genuinely is
    Control's tick, so clock()+horizon is the right stamp for it."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)

    now[0] += 1.0                          # the breath moves, no cue at all
    agent.poll()

    leds = _leds(server)
    assert leds
    assert leds[-1]["timestamp"] == pytest.approx(now[0] + HORIZON)


def test_earliest_pending_time_wins_for_one_frame():
    """One frame carries every cue applied that tick, so it must not be late
    for the soonest deadline among them."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]

    # Deviation from the brief: with HORIZON=0.060, both these `when`s
    # (base+0.20, base+0.10) push feed_at = when - horizon to base+0.14 and
    # base+0.04 -- both still in the future of `now` (still == base) at the
    # moment on_light_cue runs, so both are queued in _light_cues rather
    # than fed immediately. The clock has to move past the later feed_at
    # (base+0.14) before a poll()'s _drain_light_cues releases either one,
    # let alone both together in the same tick the way this test needs to
    # exercise "earliest wins".
    gs.on_light_cue("ie1", 0xB0, 74, 100, base + 0.20)
    gs.on_light_cue("ie1", 0xB0, 74, 110, base + 0.10)
    now[0] = base + 0.15
    agent.poll()

    leds = _leds(server)
    assert leds
    assert leds[-1]["timestamp"] == pytest.approx(base + 0.10)


def test_a_cue_that_changes_no_frame_leaves_no_stale_time_behind():
    """A cue can feed a session without changing the rendered frame. If the
    pending time survived, a stale `at` would attach to some LATER frame and
    manufacture a spurious clamp on the device, corrupting the one counter
    the horizon measurement depends on."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]

    # cc:7 has no lane in TestBit's player light_manifest, so the session
    # accepts it and renders nothing differently.
    gs.on_light_cue("ie1", 0xB0, 7, 100, base + 0.01)
    agent.poll()
    server.sent.clear()

    now[0] = base + 5.0                    # much later: the breath has moved
    agent.poll()

    leds = _leds(server)
    assert leds, "the breath should have changed the frame"
    assert leds[-1]["timestamp"] == pytest.approx(now[0] + HORIZON)


def test_a_far_future_cue_is_held_before_it_reaches_the_session():
    """A Bit-declared cue further out than one horizon must not leak its
    state into whatever breath frame renders in between. Held until
    at - horizon, and NOT counted as a clamp."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]
    fed = []
    agent.bridges["ie1"].session.feed_midi = lambda s, a, b: fed.append((s, a, b))

    gs.on_light_cue("ie1", 0xB0, 74, 100, base + 0.50)   # feed at base+0.44
    agent.poll()
    assert fed == []
    assert agent._light_cues.clamped == 0

    now[0] = base + 0.45
    agent.poll()
    # Deviation from the brief: this poll() also advances the breath (see
    # control/breath.py) far enough to change its quantized cc value, and
    # _feed_breath runs before _drain_light_cues -- so the overridden
    # feed_midi legitimately also records a (0xB0, BREATH_CC, ...) call
    # ahead of the light cue's own. That is real, unrelated behavior (the
    # breath must keep moving regardless of a queued light cue); filter it
    # out rather than asserting an exact list that depends on incidental
    # breath timing.
    light_feeds = [f for f in fed if f[1] != BREATH_CC]
    assert light_feeds == [(0xB0, 74, 100)]
    assert agent._light_cues.clamped == 0


def test_a_gesture_cue_is_fed_immediately_and_counts_no_clamp():
    """A gesture cue's feed time (at - horizon) IS the gesture time, always
    past by the time Control sees it. Queueing it would count a clamp on
    every single gesture and destroy the counter's meaning, so it is applied
    directly instead."""
    now = [1000.0]
    gs, server, agent = _make_timed_rig(now, HORIZON)
    base = now[0]
    fed = []
    agent.bridges["ie1"].session.feed_midi = lambda s, a, b: fed.append((s, a, b))

    gs.on_light_cue("ie1", 0xB0, 74, 100, (base - 0.005) + HORIZON)
    assert fed == [(0xB0, 74, 100)]
    assert agent._light_cues.clamped == 0
    assert agent._light_cues.pending() == 0
