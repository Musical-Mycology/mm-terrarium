"""In-process full-stack regression: TestBit -> GameServer grant -> composed
light-manifest-v2 blob -> luxaeterna session -> OutputLoop -> WebSimBackend
recorder. Deterministic (fake clock, hand-driven ticks, no threads, no browser).
Asserts welcome -> lit-without-a-note -> cc-driven hue glide + breathe -> fade."""

from __future__ import annotations

import pytest

pytest.importorskip("luxaeterna.backends.websim")

from bits.test_bit import TestBit
from control.engine import GameServer
from control.state import State
from harness.device_bridge import DeviceBridge
from luxaeterna.backends.websim import WebSimBackend
from luxaeterna.output import OutputLoop
from luxaeterna.synth.capability import shroom_capability
from luxaeterna.universe import Universe


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
    #     ~1.4 s it has become red-dominant. Brightness varies across the window
    #     (the breathe). max(frame) == the breathe level (hsv value is always 1.0).
    session.feed_midi(0xB0, 74, 0)                     # target hue 0 (red)
    loop._loop_once()
    mid = backend.frames[-1]
    assert max(mid[0::3]) > max(mid[1::3])             # still green-dominant -> glided, not snapped
    maxes = []
    for _ in range(60):
        loop._loop_once()
        maxes.append(max(backend.frames[-1]))
    settled = backend.frames[-1]
    assert max(settled[1::3]) > max(settled[0::3])     # now red-dominant -> cc glided the hue
    assert max(maxes) - min(maxes) > 0.02              # brightness breathes over the window
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
