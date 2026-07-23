"""In-process full-stack regression: TestBit -> GameServer grant -> composed
light-manifest-v2 blob -> luxaeterna session -> OutputLoop -> WebSimBackend
recorder. Deterministic (fake clock, hand-driven ticks, no threads, no browser).
Asserts welcome -> dark-when-running -> note lights + hue routing -> fade."""

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

    # (b) dark before any note
    loop._loop_once()
    assert max(backend.frames[-1]) == 0

    # (c) cc:74=0 -> hue red; note-on -> lit + red-dominant (GRB: byte1 red, byte0 green)
    session.feed_midi(0xB0, 74, 0)
    session.feed_midi(0x90, 60, 100)
    loop._loop_once()
    frame = backend.frames[-1]
    assert max(frame) > 0
    assert max(frame[1::3]) > max(frame[0::3])
    lit = max(frame)

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
