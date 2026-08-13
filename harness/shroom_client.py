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
    up    /game/tilt     sf   [dev, gamma]
    up    /game/tap      sffi [dev, peak_g, duration_ms, count]
    up    /game/shake    sfff [dev, peak_g, duration_ms, sweep_deg]
    down  /<dev>/role    b    [config]
    down  /<dev>/deny    ss   [reason, hint]
    down  /<dev>/leds    b    [[36 ints]]
    down  /<dev>/play    ss   [name, params]
    down  /<dev>/release ""   []
    down  /<dev>/error   ss   [context, message]

The gesture and play rows are implemented by the Flutter simulator today;
this client sends tilt only and ignores /<dev>/play. Design Rule 2 requires
both clients to send byte-identical messages, so the shapes are recorded
here before this client grows into them.

Usage on the Radxa:
    python3 -m harness.shroom_client --server ws://10.44.0.10:8081 \
        --dev ie1 --node node-a
"""

from __future__ import annotations

import logging
from typing import Callable

from control.timed_queue import TimedQueue
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
        # Frames wait here until their declared display time. Control
        # renders; this client only decides WHEN to light up.
        self._frames = TimedQueue()
        # Frames handled between ticks, not yet pushed into _frames. This
        # client is deliberately clock-free (see tick() below), so handle()
        # cannot itself judge whether a frame's declared time has already
        # passed -- the only trustworthy "now" is the one a tick() call
        # supplies, and pushing eagerly with a stale or absent reading would
        # misjudge lateness. Buffering here and pushing at the next tick
        # keeps handle() clock-free while still comparing each frame's
        # `when` against a real "now".
        self._pending: list[tuple[float | None, bytes]] = []

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
        frame = bytes(int(v) & 0xFF for v in channels)
        # timestamp 0.0 means "no declared time"; None is what TimedQueue
        # reads as that, and it must NOT count as a clamp. Buffered rather
        # than pushed here -- see _pending's docstring in __init__.
        when = env.timestamp if env.timestamp else None
        self._pending.append((when, frame))
        return env.address

    def tick(self, now: float) -> None:
        """Light up any frame whose time has arrived. Driven by the client's
        own loop; on a synced device `now` is o2lite.time_get().

        Frames buffered by _on_leds since the last tick are pushed into the
        TimedQueue first, against this call's `now` -- the only real clock
        reading this socket-free client ever gets -- so a frame whose
        declared time has already passed is correctly counted as clamped.
        """
        for when, frame in self._pending:
            self._frames.push(when, frame, now=now)
        self._pending.clear()
        for frame in self._frames.due(now):
            if self.leds is not None:
                self.leds.show(frame)

    @property
    def clamped(self) -> int:
        return self._frames.clamped

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
