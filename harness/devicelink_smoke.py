"""python -m harness.devicelink_smoke -- run Control with a live DeviceLink
so a browser Tuneshroom simulator can register and render.

    python -m harness.devicelink_smoke --hold
    python -m harness.devicelink_smoke --seconds 30 --host 0.0.0.0

Load a Bit from the Terrarium Console (or let this driver load TestBit),
then point the simulator at ws://<host>:<port>/ws.

Trust model: default bind is 127.0.0.1. --host 0.0.0.0 exposes the device
port to the LAN and is an explicit opt-in, never a default.
"""

from __future__ import annotations

import argparse
import time

from bits.test_bit import RUN_DURATION_SECONDS, TestBit
from control.engine import GameServer
from control.state import State
from devicelink.agent import DeviceLinkAgent
from devicelink.server import DeviceLinkServer

HOST, PORT = "127.0.0.1", 8771
TICK = 1.0 / 44.0


def build(host: str = HOST, port: int = PORT,
          run_duration: float = RUN_DURATION_SECONDS):
    """Construct engine + server + agent WITHOUT running a tick loop.

    Returns (game_server, server, agent). The server is already started and
    bound; pass port=0 for an ephemeral port in tests."""
    gs = GameServer({"test_bit": lambda: TestBit(run_duration=run_duration)})
    server = DeviceLinkServer(host=host, port=port)
    server.start()
    agent = DeviceLinkAgent(gs, server)
    return gs, server, agent


def _run_duration(args) -> float:
    if args.hold:
        return float("inf")
    return RUN_DURATION_SECONDS if args.seconds is None else args.seconds


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Serve DeviceLink for the Tuneshroom simulator.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="How long the Bit stays RUNNING before completing.")
    ap.add_argument("--hold", action="store_true",
                    help="Never auto-complete; serve until Ctrl-C.")
    ap.add_argument("--host", default=HOST,
                    help="Bind address. 0.0.0.0 exposes the device port to "
                         "the LAN -- explicit opt-in, no auth exists.")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    gs, server, agent = build(args.host, args.port, _run_duration(args))
    print(f"DeviceLink listening on ws://{args.host}:{server.port}/ws "
          f"(Ctrl-C to stop)")
    gs.load_bit("test_bit")
    gs.run()
    try:
        while True:
            agent.poll()
            gs.tick(TICK)
            if gs.state == State.IDLE and not args.hold:
                break
            time.sleep(TICK)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
