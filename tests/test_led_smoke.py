"""In-process full-stack regression: TestBit -> GameServer grant -> composed
light-manifest-v2 blob -> luxaeterna session -> OutputLoop -> WebSimBackend
recorder. Deterministic (fake clock, hand-driven ticks, no threads, no browser).
Asserts welcome -> lit-without-a-note -> cc-driven hue glide + breathe -> fade."""

from __future__ import annotations

import pytest

pytest.importorskip("luxaeterna.backends.websim")

from bits.test.test_bit import TestBit
from control.engine import GameServer
from control.state import State
from harness.device_bridge import DeviceBridge
from luxaeterna.backends.websim import WebSimBackend
from luxaeterna.output import OutputLoop
from luxaeterna.synth.capability import shroom_capability
from luxaeterna.universe import Universe


def backend_frame(loop):
    """The most recent frame the loop's backend recorded."""
    return loop.backend.frames[-1]


def test_full_inprocess_stack_lights_and_fades():
    gs = GameServer({"test_bit": TestBit})
    clk = iter([i * (1 / 44) for i in range(3000)]).__next__
    bridge = DeviceBridge(capability=shroom_capability(), clock=clk)
    gs.on_release = bridge.on_release

    gs.load_bit("test_bit")
    res = gs.join("dev1", "TEST_PLAYER_NODE")
    assert res.granted
    session = bridge.on_grant(res)

    uni = Universe()
    backend = WebSimBackend(capability=shroom_capability(), serve=False)
    loop = OutputLoop(uni, backend, on_frame=session.render_into, always_send=True)
    backend.open()

    # (a) The welcome signature plays out during LOADING and is LIT the whole
    # time (glow is a field-rate gesture that renders without a note), then the
    # session transitions to RUNNING within a bounded window.
    loading_lit = False
    for _ in range(200):
        loop._loop_once()
        if session.state == "loading":
            if max(backend.frames[-1]) > 0:
                loading_lit = True
        elif session.state == "running":
            break
    assert session.state == "running"
    assert loading_lit                           # welcome actually lit the surface

    # (b) aurora renders LIT during RUNNING with NO note-on — a field-rate gesture,
    #     unlike the old note-triggered bloom (dark until a note). Its authored
    #     hue 0.33 is green (GRB byte order: byte0=green, byte1=red).
    loop._loop_once()
    frame = backend.frames[-1]
    assert max(frame) > 0                              # lit without any note fed
    assert max(frame[0::3]) > max(frame[1::3])         # green-dominant (hue 0.33)

    # (c) cc:74 drives the hue and it GLIDES (Smooth), not a snap. Drive toward red
    #     (cc 0); one frame later it is still green-dominant (mid-glide), and after
    #     ~1.4 s it has become red-dominant. Declaring `level` opted aurora out of
    #     its own private breathing clock, so the breath no longer happens on its
    #     own -- Control now owns it and must drive cc:11 itself, the same way the
    #     real demo's breath_cc will (Task 7/8). Ping-pong cc:11 across the window
    #     so brightness genuinely moves; max(frame) tracks the driven level (hsv
    #     value is always 1.0).
    session.feed_midi(0xB0, 74, 0)                     # target hue 0 (red)
    loop._loop_once()
    mid = backend.frames[-1]
    assert max(mid[0::3]) > max(mid[1::3])             # still green-dominant -> glided, not snapped
    maxes = []
    breath, breath_step = 70, 4                        # ping-pong roughly 70..127
    for _ in range(60):
        session.feed_midi(0xB0, 11, breath)
        loop._loop_once()
        maxes.append(max(backend.frames[-1]))
        breath += breath_step
        if breath >= 127 or breath <= 70:
            breath = max(70, min(127, breath))
            breath_step = -breath_step
    settled = backend.frames[-1]
    assert max(settled[1::3]) > max(settled[0::3])     # now red-dominant -> cc glided the hue
    assert max(maxes) - min(maxes) > 0.02              # breath still reaches the surface, now from cc:11
    lit = max(maxes)                                    # a lit running frame for the fade check

    # (d) complete the Bit -> unload -> on_release -> session.clear() -> fade
    gs.run()
    gs.tick(2.1)                              # elapsed >= RUN_DURATION -> complete
    assert gs.state == State.IDLE

    closing_maxes = []
    for _ in range(30):
        loop._loop_once()
        closing_maxes.append(max(backend.frames[-1]))
    assert session.state in ("closing", "idle")
    assert min(closing_maxes) < lit          # a real fade dip occurred


def test_one_cc_stream_reaches_both_the_light_and_the_audio():
    """The property this whole slice exists to establish. If someone later
    splits the stream into two timelines, this fails loudly."""
    from control.audio import FakePool
    from harness.led_smoke import build, feed_shared

    pool = FakePool()
    # A fake clock, like the test above it: build() threads it into both the
    # light bridge and the audio bridge, so the 1.5 s welcome plays out in
    # hand-driven ticks rather than real seconds.
    clk = iter([i * (1 / 44) for i in range(3000)]).__next__
    loop, session, gs, audio = build(run_duration=float("inf"), serve=False,
                                     clock=clk, pool=pool)
    loop.backend.open()          # build() does not open it; loop.start() would
    gs.run()
    for _ in range(300):                         # let the welcome play out
        loop._loop_once()
        if session.state == "running":
            break
    assert session.state == "running"

    feed_shared(session, audio, "sim-dev", ((0xB0, 74, 100), (0xB0, 11, 120)))
    loop._loop_once()                            # drain the light queue

    drone_voice = pool.acquired[0]
    assert ("cc", 74, 100) in drone_voice.sent   # audio saw both controllers
    assert ("cc", 11, 120) in drone_voice.sent
    assert max(backend_frame(loop)) > 0          # light is still rendering them
