"""python -m harness.led_smoke — drive TestBit through the in-process stack and
watch it on the Web LED simulator.

Requires luxaeterna[websim] installed editable (see requirements-dev.txt):
    python -m pip install -e "/Users/chris/projects/luxaeterna[websim]"

By default the demo runs TestBit's natural ~2 s lifecycle then exits. To watch it
in a browser, keep it up longer:
    python -m harness.led_smoke --hold          # serve until Ctrl-C
    python -m harness.led_smoke --seconds 15    # sweep ~15 s, then complete + fade
    python -m harness.led_smoke --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

import argparse
import time

from bits.test_bit import RUN_DURATION_SECONDS, TestBit
from control.engine import GameServer
from control.state import State
from harness.device_bridge import DeviceBridge
from luxaeterna.backends.websim import WebSimBackend
from luxaeterna.output import OutputLoop
from luxaeterna.synth.capability import shroom_capability
from luxaeterna.universe import Universe

HOST, PORT = "127.0.0.1", 8770


def build(run_duration: float, host: str = HOST, port: int = PORT,
          serve: bool = True, clock=time.monotonic):
    """Construct the demo pipeline WITHOUT starting the loop.

    Returns ``(loop, session, gs)``. ``run_duration`` is threaded into TestBit
    via a factory so the Bit's RUNNING window is caller-controlled
    (``float('inf')`` = never completes). ``serve=False`` gives a record-only
    backend (no websockets, no port) for headless tests."""
    gs = GameServer({"test_bit": lambda: TestBit(run_duration=run_duration)})
    cap = shroom_capability()
    bridge = DeviceBridge(capability=cap, clock=clock)
    gs.on_release = bridge.on_release
    gs.load_bit("test_bit")
    session = bridge.on_grant(gs.join("sim-dev", "TEST_PLAYER_NODE"))
    uni = Universe()
    backend = WebSimBackend(capability=cap, host=host, port=port, serve=serve)
    loop = OutputLoop(uni, backend, on_frame=session.render_into, always_send=True)
    return loop, session, gs


def _run_duration(args) -> float:
    if args.hold:
        return float("inf")
    return RUN_DURATION_SECONDS if args.seconds is None else args.seconds


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Watch TestBit render on the Web LED simulator.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="Keep the Bit RUNNING/sweeping this long before it "
                         "completes + fades (default: TestBit's natural ~2 s).")
    ap.add_argument("--hold", action="store_true",
                    help="Serve until Ctrl-C (never auto-complete).")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    loop, session, gs = build(_run_duration(args), args.host, args.port)
    loop.start()
    print(f"Watch the Shroom at http://{args.host}:{args.port}/  (Ctrl-C to stop)")

    gs.run()
    try:
        while session.state != "running":
            time.sleep(0.02)
        cc, step = 0, 2
        while gs.state == State.RUNNING:
            session.feed_midi(0xB0, 74, cc)          # cc:74 -> hue; aurora glides between steps
            cc += step
            if cc >= 127 or cc <= 0:                 # ping-pong (no wrap discontinuity)
                cc = max(0, min(127, cc))
                step = -step
            gs.tick(0.15)                            # advances TestBit toward complete
            time.sleep(0.15)
        time.sleep(1.2)                              # let the closing fade + idle play
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()


if __name__ == "__main__":
    main()
