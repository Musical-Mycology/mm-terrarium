"""python -m harness.capture_smoke -- run Control with the CaptureBit loaded
and a live DeviceLink, so a phone running the mm-tuneshroom capture client
can join and stream labelled telemetry.

    python -m harness.capture_smoke --host 0.0.0.0
    python -m harness.capture_smoke --capture-dir /data/captures

Traces land under <capture-dir>/<session-id>/. Point the capture client at
ws://<host>:<port>/ws and tap the CAPTURE_NODE registration node.

Trust model: default bind is 127.0.0.1, so a real phone needs --host 0.0.0.0.
That is an explicit opt-in and no auth exists -- unchanged from devicelink/
and console/, but now an actual handheld device is on the network.

Nothing measured here is a hop count or a latency figure: this is a direct
websocket to Control with Arco nowhere in the path.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from bits.capture_bit import CAPTURE_NODE, CaptureBit
from capture.store import CaptureStore, new_session_id
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent
from devicelink.server import DeviceLinkServer

HOST, PORT = "127.0.0.1", 8771
CAPTURE_DIR = "./captures"
TICK = 1.0 / 44.0
BIT_NAME = "capture"


def build(host: str = HOST, port: int = PORT,
          capture_dir=CAPTURE_DIR, session_id: str | None = None,
          clock=time.monotonic):
    """Construct engine + store + server + agent WITHOUT running a tick loop.

    Returns (game_server, server, agent, store). The server is already bound;
    pass port=0 for an ephemeral port in tests. `session_id` and `clock` are
    pure test seams; the defaults keep main()'s production path unchanged.
    """
    store = CaptureStore(root=Path(capture_dir),
                         session_id=session_id or new_session_id(),
                         bit={"name": BIT_NAME, "version": CaptureBit.version},
                         clock=clock)
    gs = GameServer({BIT_NAME: lambda: CaptureBit(store=store)})
    server = DeviceLinkServer(host=host, port=port)
    server.start()
    agent = DeviceLinkAgent(gs, server, clock=clock)
    return gs, server, agent, store


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Serve DeviceLink with the CaptureBit loaded.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="Abort the Bit after this long instead of serving "
                         "until Ctrl-C.")
    ap.add_argument("--host", default=HOST,
                    help="Bind address. 0.0.0.0 exposes the device port to "
                         "the LAN -- explicit opt-in, no auth exists.")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--capture-dir", default=CAPTURE_DIR,
                    help="Root for trace files. Default ./captures "
                         "(gitignored).")
    args = ap.parse_args()

    gs, server, agent, store = build(args.host, args.port, args.capture_dir)
    print(f"DeviceLink listening on ws://{args.host}:{server.port}/ws")
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
