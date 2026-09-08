"""python -m harness.capture_smoke -- run Control with the CaptureBit loaded
so a phone running the mm-tuneshroom capture client can join and stream
labelled telemetry.

    python -m harness.capture_smoke --capture-dir /data/captures

Traces land under <capture-dir>/<session-id>/. Tap the CAPTURE_NODE
registration node.

Interim state (Task 7 finishes this): the device transport here is an
in-process O2LiteTransport started on a FakeO2Lite double, not a real
o2lite connection -- there is no live device path through this module yet.

Nothing measured here is a hop count or a latency figure: this is a direct
in-process transport to Control with Arco nowhere in the path.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from bits.capture.capture_bit import CAPTURE_NODE, CaptureBit
from capture.store import CaptureStore, new_session_id
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport

CAPTURE_DIR = "./captures"
TICK = 1.0 / 44.0
BIT_NAME = "capture"


def build(capture_dir=CAPTURE_DIR, session_id: str | None = None,
          clock=time.monotonic):
    """Construct engine + store + server + agent WITHOUT running a tick loop.

    Returns (game_server, server, agent, store). `server` is an
    O2LiteTransport started on an in-process FakeO2Lite double (see the
    module docstring). `session_id` and `clock` are pure test seams; the
    defaults keep main()'s production path unchanged.
    """
    store = CaptureStore(root=Path(capture_dir),
                         session_id=session_id or new_session_id(),
                         bit={"name": BIT_NAME, "version": CaptureBit.version},
                         clock=clock)
    gs = GameServer({BIT_NAME: lambda: CaptureBit(store=store)})
    o2lite = FakeO2Lite()
    o2lite.set_services("actl")
    server = O2LiteTransport()
    server.start(o2lite)
    agent = DeviceLinkAgent(gs, server, clock=clock)
    return gs, server, agent, store


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Serve DeviceLink with the CaptureBit loaded.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="Abort the Bit after this long instead of serving "
                         "until Ctrl-C.")
    ap.add_argument("--capture-dir", default=CAPTURE_DIR,
                    help="Root for trace files. Default ./captures "
                         "(gitignored).")
    args = ap.parse_args()

    gs, server, agent, store = build(args.capture_dir)
    print(f"Traces -> {store.session_dir}   (node: {CAPTURE_NODE})")
    gs.load_bit(BIT_NAME)
    gs.run()
    started = time.monotonic()
    try:
        while True:
            agent.poll()
            gs.tick(TICK)
            if args.seconds is not None and \
                    time.monotonic() - started >= args.seconds:
                break
            time.sleep(TICK)
    except KeyboardInterrupt:
        pass
    finally:
        gs.abort()
        server.stop()
        print(f"captures: {store.counts()}  failures: {store.failures}")


if __name__ == "__main__":
    main()
