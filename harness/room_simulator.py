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

from harness.shroom_client import ShroomClient, pump_tick
from harness.signals import sigterm_as_keyboard_interrupt


class WebSimLeds:
    """Adapts ShroomClient's leds.show(bytes)/leds.clear() to
    WebSimBackend's send(frame).

    `channels` is the frame width this surface expects. It is a parameter
    rather than the LED_CHANNELS constant because a Room is not a Tuneshroom:
    the Room's width comes from its RoomProfile (60 px x 3 = 180), while a
    player device is still 12 px x GRB = 36.
    """

    def __init__(self, backend, channels: int) -> None:
        self._backend = backend
        self._channels = channels

    def show(self, frame: bytes) -> None:
        self._backend.send(frame)

    def clear(self) -> None:
        self._backend.send(bytes(self._channels))


def build(dev: str, sim_host: str = "127.0.0.1", sim_port: int = 0,
          serve: bool = True, room_type: str = "TEST"):
    """Construct the client + backend WITHOUT opening a socket or serving.

    Returns ``(client, backend)``. ``serve=False`` gives a record-only
    backend (no websockets, no port) for headless tests, matching
    ``led_smoke.py``'s ``build()``/``main()`` split -- the caller is
    responsible for ``backend.open()``/``.close()`` and the real
    devicelink websocket loop.

    The surface is the ROOM's, not a Tuneshroom's: this process renders a
    Room, and borrowing shroom_capability() here is what made a Room a 12-LED
    ring and stem. See control/room_profile.py.
    """
    from luxaeterna.backends.websim import WebSimBackend

    from control.room_profile import room_profile
    from control.rooms import RoomType
    from harness.room_surface import to_capability

    profile = room_profile(RoomType[room_type])
    backend = WebSimBackend(capability=to_capability(profile),
                             host=sim_host, port=sim_port, serve=serve,
                             label=dev)
    client = ShroomClient(dev, node="", leds=WebSimLeds(backend,
                                                       profile.channel_count),
                          expected_channels=profile.channel_count)
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
    parser.add_argument("--room-type", default="TEST",
                        help="Which RoomType's surface to render. Resolved "
                             "through control/room_profile.py, so the "
                             "simulator and Control agree on the shape by "
                             "construction rather than by convention.")
    parser.add_argument("--control-horizon", type=float, default=None,
                        help="The horizon Control was run with, used only to "
                             "report absolute frame latency on exit. Same "
                             "flag and same meaning as harness/o2_shroom.py's.")
    parser.add_argument("--samples-out", default=None,
                        help="Write raw per-frame lateness samples here as "
                             "JSON, for python -m harness.sync_bench.")
    args = parser.parse_args()

    sigterm_as_keyboard_interrupt()

    client, backend = build(args.dev, args.sim_host, args.sim_port,
                            room_type=args.room_type)
    backend.open()
    print(f"Watch the Room at http://{args.sim_host}:{backend.port}/", flush=True)

    async def run() -> None:
        async with websockets.connect(args.server) as ws:
            await ws.send(json.dumps(client.hello()))

            async def pump_down() -> None:
                async for raw in ws:
                    client.handle(json.loads(raw))

            await asyncio.gather(pump_down(), pump_tick(client))

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        # Same exit report as harness/o2_shroom.py. This path carries NO Arco
        # hop -- Control and this process talk over a local websocket -- so
        # what it measures is Control's own render-to-emit latency plus a
        # loopback socket, NOT the o2lite cue path. Useful as a floor and for
        # checking the instrumentation; never quote it as a cue latency.
        from harness.o2_shroom import _report_latency
        print(f"frames displayed late: {client.clamped}")
        _report_latency(client, args.control_horizon, args.samples_out)
        backend.close()


if __name__ == "__main__":
    main()
