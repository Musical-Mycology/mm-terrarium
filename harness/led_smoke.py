"""python -m harness.led_smoke — drive TestBit through the in-process stack and
watch it on the Web LED simulator.

Requires luxaeterna[websim] installed editable (see requirements-dev.txt):
    python -m pip install -e "/Users/chris/projects/luxaeterna[websim]"

By default the demo runs TestBit's natural ~2 s lifecycle then exits. To watch it
in a browser, keep it up longer:
    python -m harness.led_smoke --hold          # serve until Ctrl-C
    python -m harness.led_smoke --seconds 15    # sweep ~15 s, then complete + fade
    python -m harness.led_smoke --host 0.0.0.0 --port 9000

To hear it too, start the Arco server first (it is a curses app, so it needs a
real terminal):
    cd /Users/chris/projects/arco/apps/pytest && ./server
then:
    PYTHONPATH=/Users/chris/projects/arco python -m harness.led_smoke --audio --hold
"""

from __future__ import annotations

import argparse
import os
import time

from bits.test.test_bit import RUN_DURATION_SECONDS, TestBit
from control.audio import AudioBridge
from control.breath import BREATH_CC, breath_cc
from control.engine import GameServer
from control.state import State
from harness.device_bridge import DeviceBridge
from harness.signals import sigterm_as_keyboard_interrupt
from luxaeterna.backends.websim import WebSimBackend
from luxaeterna.output import OutputLoop
from luxaeterna.synth.capability import shroom_capability
from luxaeterna.universe import Universe

HOST, PORT = "127.0.0.1", 8770


def build(run_duration: float, host: str = HOST, port: int = PORT,
          serve: bool = True, clock=time.monotonic, pool=None):
    """Construct the demo pipeline WITHOUT starting the loop.

    Returns ``(loop, session, gs, audio)``. ``audio`` is None unless a
    SynthPool is passed, so the demo stays byte-identical without --audio.
    ``run_duration`` is threaded into TestBit via a factory so the Bit's
    RUNNING window is caller-controlled (``float('inf')`` = never completes).
    ``serve=False`` gives a record-only backend (no websockets, no port) for
    headless tests."""
    gs = GameServer({"test_bit": lambda: TestBit(run_duration=run_duration)})
    cap = shroom_capability()
    bridge = DeviceBridge(capability=cap, clock=clock)
    gs.on_release = bridge.on_release
    gs.load_bit("test_bit")
    result = gs.join("sim-dev", "TEST_PLAYER_NODE")
    session = bridge.on_grant(result)
    audio = None
    if pool is not None:
        # The audio declaration is read off the Role, not off the composed
        # /ie<N>/role blob: audio never ships to the device (boundary rule 1).
        audio = AudioBridge(pool, clock=clock)
        audio.on_grant("sim-dev", gs.bit.role_table.roles[result.role])
    uni = Universe()
    backend = WebSimBackend(capability=cap, host=host, port=port, serve=serve)
    loop = OutputLoop(uni, backend, on_frame=session.render_into, always_send=True)
    return loop, session, gs, audio


def _run_duration(args) -> float:
    if args.hold:
        return float("inf")
    return RUN_DURATION_SECONDS if args.seconds is None else args.seconds


def feed_shared(session, audio, dev, pairs):
    """The one place light and sound are fed. Both consumers read the SAME
    bytes in the same tick, which is the property this whole demo exists to
    establish. main() and the regression test call this exact function, so a
    future edit that splits the feeding into two paths breaks the test."""
    for status, d1, d2 in pairs:
        session.feed_midi(status, d1, d2)
        if audio is not None:
            audio.feed_midi(dev, status, d1, d2)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Watch (and hear) TestBit render on the Web LED simulator.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="Keep the Bit RUNNING/sweeping this long before it "
                         "completes + fades (default: TestBit's natural ~2 s).")
    ap.add_argument("--hold", action="store_true",
                    help="Serve until Ctrl-C (never auto-complete).")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--audio", action="store_true",
                    help="Also play sound through a running Arco server. Off "
                         "by default, so this demo needs no Arco to watch.")
    ap.add_argument("--soundfont", default=os.environ.get("MM_SOUNDFONT"),
                    help="SoundFont for the Flsyn ugen (default: "
                         "$MM_SOUNDFONT, else the venue soundfont).")
    ap.add_argument("--program", type=int, default=None,
                    help="Override the General MIDI program TestBit declares.")
    args = ap.parse_args()
    sigterm_as_keyboard_interrupt()

    pool = None
    if args.audio:
        from harness.arco_synth import DEFAULT_SOUNDFONT, ArcoSynthPool
        pool = ArcoSynthPool(soundfont=args.soundfont or DEFAULT_SOUNDFONT)
        pool.start()                     # blocks until the Arco server answers

    try:
        loop, session, gs, audio = build(_run_duration(args), args.host, args.port,
                                         pool=pool)
        if audio is not None and args.program is not None:
            audio.feed_midi("sim-dev", 0xC0, args.program, 0)
        loop.start()
        print(f"Watch the Shroom at http://{args.host}:{args.port}/  (Ctrl-C to stop)")

        gs.run()
    except BaseException:
        # build() can raise (e.g. AudioBridge.on_grant on an unknown welcome
        # instrument) after pool.start() already opened the Arco connection
        # and allocated a Flsyn ugen. Nothing else owns that connection yet,
        # so it must be freed here or it is orphaned server-side.
        if pool is not None:
            pool.shutdown()
        raise
    started = time.monotonic()
    try:
        while session.state != "running":
            time.sleep(0.02)
        if audio is not None:
            audio.start_drone("sim-dev")     # FluidSynth is silent without a note
        cc, step = 0, 2
        while gs.state == State.RUNNING:
            breath = breath_cc(time.monotonic() - started)
            feed_shared(session, audio, "sim-dev",
                        ((0xB0, 74, cc), (0xB0, BREATH_CC, breath)))
            if audio is not None:
                audio.tick()
            cc += step
            if cc >= 127 or cc <= 0:         # ping-pong (no wrap discontinuity)
                cc = max(0, min(127, cc))
                step = -step
            gs.tick(0.15)                    # advances TestBit toward complete
            time.sleep(0.15)
        if audio is not None:
            audio.on_release("sim-dev")
        time.sleep(1.2)                      # let the closing fade + idle play
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()
        if audio is not None:
            audio.shutdown()                 # frees the ugen id space


if __name__ == "__main__":
    main()
