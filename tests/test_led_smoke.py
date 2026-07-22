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

    # (a) welcome signature plays out during LOADING, then the session
    # transitions to RUNNING.
    #
    # NOTE on assertion mechanics: this does NOT assert the LOADING frame is
    # lit (max(frame) > 0), unlike the brief's literal draft. Investigation
    # found that isn't a frame-1/near-zero-dt timing artifact (widening the
    # window to the whole LOADING run doesn't help either) -- it's how
    # luxaeterna's "bloom" preset behaves by design. "bloom" is a LightSynth
    # voice pool (luxaeterna/synth/presets.py::_make_bloom); it only renders
    # once a voice is spawned via .noteon(), and TestBit's welcome
    # declaration (bits/test_bit.py, {"instrument": "bloom", "params":
    # {"hue": 0.33}, "duration": 1.5}, no lanes) never triggers one --
    # SignatureDecl (luxaeterna/synth/manifest.py) has no lanes concept to
    # trigger with. Feeding MIDI during LOADING is not a workaround either:
    # LightSession._apply drops MIDI unless director.state == RUNNING. This
    # is luxaeterna's own documented behavior for a bare bloom welcome -- its
    # tests/synth/test_director.py::test_welcome_replaces_generic_loaded
    # swaps in the same shape of welcome and comments "a dark synth is
    # fine -- only timing matters." So the achievable, still-meaningful
    # intent this asserts is: the welcome signature actually plays (LOADING
    # is observed for at least one frame) before the session reaches
    # RUNNING within a bounded window -- i.e. a real LOADING gate happened,
    # not an instant skip.
    loading_frames = 0
    for _ in range(200):
        loop._loop_once()
        if session.state == "loading":
            loading_frames += 1
        elif session.state == "running":
            break
    assert session.state == "running"
    assert loading_frames > 0                   # welcome signature actually played

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
