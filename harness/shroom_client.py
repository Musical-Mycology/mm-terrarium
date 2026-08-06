"""The Radxa Tuneshroom's devicelink participation.

Socket-free by design: ``handle()`` takes a decoded JSON message and returns the
address it handled, or ``""`` if it dropped the frame. That keeps the whole
protocol surface testable on a laptop, and it matches the engine's rule that a
malformed frame is "drop this frame", never an error.

The transport half lives in ``main()`` and is deliberately thin, because it is
the part that gets replaced when o2lite lands. The envelopes here mirror o2ws
field-for-field already, so that swap is mechanical.

The wire, from devicelink/protocol.py and devicelink/agent.py:

    up    /game/hello    s    [dev]
    up    /game/join     ss   [dev, node]
    up    /game/<verb>   sf   [dev, value]
    down  /<dev>/role    b    [config]
    down  /<dev>/deny    ss   [reason, hint]
    down  /<dev>/leds    b    [[36 ints]]
    down  /<dev>/release ""   []
    down  /<dev>/error   ss   [context, message]

Usage on the Radxa:
    python3 -m harness.shroom_client --server ws://10.44.0.10:8081 \
        --dev ie1 --node node-a
"""

from __future__ import annotations

import logging
from typing import Callable

from devicelink import protocol

logger = logging.getLogger(__name__)

# 12 pixels x GRB, per protocol.leds_event. The parts are SK6812 RGBW and the
# white die is currently unreachable over this wire; see the plan's Task B7,
# which is a pending decision rather than a bug to fix here.
LED_CHANNELS = 36


class ShroomClient:
    """Tracks one device's devicelink session and drives its LEDs."""

    def __init__(self, dev: str, node: str, leds=None,
                 on_role: Callable[[dict], None] | None = None) -> None:
        self.dev = dev
        self.node = node
        self.leds = leds
        self.on_role = on_role
        self.config: dict | None = None
        self.released = False
        self.last_deny: tuple[str, str] | None = None
        self.last_error: tuple[str, str] | None = None

    # --- outbound ---

    def _up(self, verb: str, typespec: str, args: list) -> dict:
        return protocol.encode(
            protocol.Envelope(timestamp=0.0, address=f"/game/{verb}",
                              typespec=typespec, args=args))

    def hello(self) -> dict:
        return self._up("hello", "s", [self.dev])

    def join(self) -> dict:
        self.released = False
        return self._up("join", "ss", [self.dev, self.node])

    def tilt(self, value: float) -> dict:
        return self._up("tilt", "sf", [self.dev, float(value)])

    # --- inbound ---

    def handle(self, msg) -> str:
        """Process one inbound message. Returns its address, or "" if dropped."""
        try:
            env = protocol.decode(msg)
        except (ValueError, AttributeError, TypeError):
            logger.debug("dropping malformed envelope")
            return ""

        prefix = f"/{self.dev}/"
        if not env.address.startswith(prefix):
            return ""
        kind = env.address[len(prefix):]

        if kind == "role":
            return self._on_role(env)
        if kind == "leds":
            return self._on_leds(env)
        if kind == "release":
            return self._on_release(env)
        if kind == "deny":
            self.last_deny = (env.args[0], env.args[1])
            return env.address
        if kind == "error":
            self.last_error = (env.args[0], env.args[1])
            return env.address
        return ""

    def _on_role(self, env) -> str:
        if not env.args or not isinstance(env.args[0], dict):
            logger.debug("dropping /role with a non-dict payload")
            return ""
        self.config = env.args[0]
        if self.on_role is not None:
            self.on_role(self.config)
        return env.address

    def _on_leds(self, env) -> str:
        if not env.args or not isinstance(env.args[0], list):
            logger.debug("dropping /leds with a non-list payload")
            return ""
        channels = env.args[0]
        if len(channels) != LED_CHANNELS:
            logger.debug("dropping /leds with %d channels", len(channels))
            return ""
        if self.leds is not None:
            self.leds.show(bytes(int(v) & 0xFF for v in channels))
        return env.address

    def _on_release(self, env) -> str:
        self.released = True
        if self.leds is not None:
            self.leds.clear()
        return env.address


def main() -> None:
    """Connect to a DeviceLinkServer and run the sensor-up / LED-down loop."""
    import argparse
    import asyncio
    import json

    import websockets

    from harness.lis3dh_probe import open_sensor, read_tilt
    from harness.shroom_leds import ShroomLEDs

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="ws://host:port")
    parser.add_argument("--dev", default="ie1")
    parser.add_argument("--node", default="node-a")
    parser.add_argument("--sensor-hz", type=float, default=20.0)
    args = parser.parse_args()

    client = ShroomClient(args.dev, args.node, leds=ShroomLEDs())
    sensor = open_sensor()
    interval = 1.0 / args.sensor_hz

    async def run() -> None:
        async with websockets.connect(args.server) as ws:
            await ws.send(json.dumps(client.hello()))
            await ws.send(json.dumps(client.join()))

            async def pump_down() -> None:
                async for raw in ws:
                    client.handle(json.loads(raw))

            async def pump_up() -> None:
                while not client.released:
                    x, _, _ = read_tilt(sensor)
                    await ws.send(json.dumps(client.tilt(x / 9.81)))
                    await asyncio.sleep(interval)

            await asyncio.gather(pump_down(), pump_up())

    asyncio.run(run())


if __name__ == "__main__":
    main()
