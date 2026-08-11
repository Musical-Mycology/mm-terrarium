"""python -m harness.room_simulator -- the TEST-room simulator subprocess.

An ordinary devicelink client, indistinguishable from a real Tuneshroom:
reuses harness/shroom_client.py's ShroomClient unmodified for the wire
protocol, and renders into luxaeterna's WebSimBackend (a browser canvas)
instead of real GPIO LEDs. Sends only `/game/hello` -- never `/game/join` --
because control/boot.py's boot() (Spec 1) has already recorded this dev as
the bound Room before this process is spawned; there is no Registration
Node to tap. See
docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md
section 3-4.

Audio never touches this process: Control drives Arco directly for the
Room's declared instrument (see devicelink/agent.py), the same way it
already does for player roles. This process is a pure LED display.

Usage (normally spawned by harness/terrarium_boot.py, not run by hand):
    python3 -m harness.room_simulator --dev sim-room \
        --server ws://127.0.0.1:8771/ws --sim-host 127.0.0.1 --sim-port 8770
"""

from __future__ import annotations

from harness.shroom_client import LED_CHANNELS, ShroomClient


class WebSimLeds:
    """Adapts ShroomClient's leds.show(bytes)/leds.clear() to
    WebSimBackend's send(frame). Frame byte counts already match: both
    trace to the same shroom_capability() (12 pixels x 3 bytes = 36),
    matching devicelink's own 36-int /<dev>/leds wire shape."""

    def __init__(self, backend) -> None:
        self._backend = backend

    def show(self, frame: bytes) -> None:
        self._backend.send(frame)

    def clear(self) -> None:
        self._backend.send(bytes(LED_CHANNELS))


def build(dev: str, sim_host: str = "127.0.0.1", sim_port: int = 0,
          serve: bool = True):
    """Construct the client + backend WITHOUT opening a socket or serving.

    Returns ``(client, backend)``. ``serve=False`` gives a record-only
    backend (no websockets, no port) for headless tests, matching
    ``led_smoke.py``'s ``build()``/``main()`` split -- the caller is
    responsible for ``backend.open()``/``.close()`` and the real
    devicelink websocket loop."""
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import shroom_capability

    backend = WebSimBackend(capability=shroom_capability(),
                             host=sim_host, port=sim_port, serve=serve)
    client = ShroomClient(dev, node="", leds=WebSimLeds(backend))
    return client, backend


def main() -> None:
    import argparse
    import asyncio
    import json

    import websockets

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", required=True,
                        help="Dev id Terrarium assigned this Room instance.")
    parser.add_argument("--server", required=True,
                        help="Control's devicelink URL, e.g. ws://host:port/ws")
    parser.add_argument("--sim-host", default="127.0.0.1")
    parser.add_argument("--sim-port", type=int, default=0)
    args = parser.parse_args()

    client, backend = build(args.dev, args.sim_host, args.sim_port)
    backend.open()
    print(f"Watch the Room at http://{args.sim_host}:{backend.port}/")

    async def run() -> None:
        async with websockets.connect(args.server) as ws:
            await ws.send(json.dumps(client.hello()))
            async for raw in ws:
                client.handle(json.loads(raw))

    try:
        asyncio.run(run())
    finally:
        backend.close()


if __name__ == "__main__":
    main()
