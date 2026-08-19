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
    python3 -m harness.room_simulator --dev sim-room-main --fixture main \
        --server ws://127.0.0.1:8771/ws --sim-host 127.0.0.1 --sim-port 8770
"""

from __future__ import annotations

from harness.shroom_client import ShroomClient, pump_tick
from harness.signals import sigterm_as_keyboard_interrupt

# Fixed identification palette, assigned to blocks in declaration order
# (red, orange, yellow, green, blue, violet). RGB triples; laid out per the
# fixture's color_order when painted. Repeats past six blocks.
BLOCK_PALETTE: tuple[tuple[int, int, int], ...] = (
    (255, 0, 0), (255, 128, 0), (255, 255, 0),
    (0, 255, 0), (0, 0, 255), (148, 0, 211),
)


def identify_blocks_frame(profile, fixture_name: str) -> bytes:
    """One static frame painting each of the fixture's blocks a distinct
    solid color, so a human can visually confirm the physical build-out
    mapping on the canvas. Harness-only: the one consumer of block
    boundaries this slice (blocks are otherwise declarative -- see
    control/room_profile.py's RoomBlock)."""
    fixture = next(f for f in profile.fixtures if f.name == fixture_name)
    order = fixture.color_order.upper()
    frame = bytearray(fixture.pixel_count * 3)
    for i, block in enumerate(fixture.blocks):
        rgb = dict(zip("RGB", BLOCK_PALETTE[i % len(BLOCK_PALETTE)]))
        px = bytes(rgb[ch] for ch in order)
        frame[block.start * 3:(block.start + block.count) * 3] = \
            px * block.count
    return bytes(frame)


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
          serve: bool = True, room_type: str = "TEST", fixture: str = "main"):
    """Construct the client + backend WITHOUT opening a socket or serving.

    Returns ``(client, backend)``. Renders exactly one fixture's own
    surface, with LOCAL zone names -- this process IS one physical strip,
    not the whole Room. See harness/room_surface.py's to_fixture_capability
    and design spec section 7.
    """
    from luxaeterna.backends.websim import WebSimBackend

    from control.room_profile import room_profile
    from control.rooms import RoomType
    from harness.room_surface import to_fixture_capability

    profile = room_profile(RoomType[room_type])
    cap = to_fixture_capability(profile, fixture)
    backend = WebSimBackend(capability=cap, host=sim_host, port=sim_port,
                             serve=serve, label=dev)
    client = ShroomClient(dev, node="", leds=WebSimLeds(backend,
                                                       cap.pixel_count * 3),
                          expected_channels=cap.pixel_count * 3)
    return client, backend


def main() -> None:
    import argparse
    import asyncio
    import json

    import websockets

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", required=True,
                        help="Dev id Terrarium assigned this Room instance.")
    parser.add_argument("--fixture", required=True,
                        help="Which of the Room's declared fixtures this "
                             "process renders (see control/room_profile.py).")
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
    parser.add_argument("--identify-blocks", action="store_true",
                        help="Debug: skip Control entirely; paint each of "
                             "this fixture's declared blocks a distinct "
                             "solid color and hold until Ctrl-C, so the "
                             "physical build-out mapping can be confirmed "
                             "visually. See the 2026-08-19 spec section 4.")
    args = parser.parse_args()

    sigterm_as_keyboard_interrupt()

    client, backend = build(args.dev, args.sim_host, args.sim_port,
                            room_type=args.room_type, fixture=args.fixture)
    backend.open()
    print(f"Watch the Room at http://{args.sim_host}:{backend.port}/", flush=True)

    if args.identify_blocks:
        import time

        from control.room_profile import room_profile
        from control.rooms import RoomType

        profile = room_profile(RoomType[args.room_type])
        backend.send(identify_blocks_frame(profile, args.fixture))
        print(f"identify-blocks: {args.fixture} painted; Ctrl-C to exit",
              flush=True)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            backend.close()
        return

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
