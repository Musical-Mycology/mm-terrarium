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
    down  /<dev>/room    b    [blob]   (informational; ignored here)

The gesture and play rows are implemented by the Flutter simulator today;
this client sends tilt and tap and ignores /<dev>/play. Design Rule 2 requires
both clients to send byte-identical messages, so the shapes are recorded
here before this client grows into them.

Usage on the Radxa:
    python3 -m harness.shroom_client --server ws://10.44.0.10:8081 \
        --dev ie1 --node node-a
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Callable

from control.timed_queue import TimedQueue
from devicelink import protocol

logger = logging.getLogger(__name__)

# 12 pixels x GRB, per protocol.leds_event. The parts are SK6812 RGBW and the
# white die is currently unreachable over this wire; see the plan's Task B7,
# which is a pending decision rather than a bug to fix here.
LED_CHANNELS = 36

# Bound on frames buffered in _pending between ticks. Under normal operation
# (tick() driven at the render rate -- see _TICK_INTERVAL) this never comes
# close: _pending is fully drained every tick. It exists only to stop
# unbounded growth if a caller drives handle() without ever calling tick(),
# mirroring devicelink/agent.py's _MAX_CLOSING_FRAMES (also 200, ~1s at
# 44Hz) as the bound on an analogous unrendered backlog.
_MAX_PENDING_FRAMES = 200

# The engine's own render/tick rate (see harness/terrarium_boot.py's
# `gs.tick(1.0 / 44.0)` and harness/devicelink_smoke.py's TICK). Frames are
# rendered at this rate, so ticking a client faster than this buys nothing;
# ticking much slower would blur "held until its time" into "held until
# roughly its time".
_TICK_INTERVAL = 1.0 / 44.0


class ShroomClient:
    """Tracks one device's devicelink session and drives its LEDs."""

    def __init__(self, dev: str, node: str, leds=None,
                 on_role: Callable[[dict], None] | None = None,
                 expected_channels: int = LED_CHANNELS) -> None:
        self.dev = dev
        self.node = node
        self.leds = leds
        self.on_role = on_role
        # Frame width this client will accept, in channels. Defaults to the
        # 12 px x GRB Tuneshroom wire, so every existing caller is unchanged.
        # The Room simulator passes its RoomProfile.channel_count instead: a
        # Room is not a Tuneshroom and does not have 36 channels. See
        # control/room_profile.py.
        self.expected_channels = expected_channels
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
        # `when` against a real "now". Bounded: if a caller drives handle()
        # without ever calling tick() (see _MAX_PENDING_FRAMES above), this
        # deque drops the oldest frame per new arrival rather than growing
        # without bound.
        self._pending: deque[tuple[float | None, bytes]] = deque(
            maxlen=_MAX_PENDING_FRAMES)

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

    def tap(self, peak_g: float = 1.0, duration_ms: float = 50.0,
            count: int = 1) -> dict:
        """The documented tap row. Defaults are the simulator's honest
        placeholders for values a mouse cannot measure; count comes from
        the caller's own detection."""
        return self._up("tap", "sffi",
                        [self.dev, float(peak_g), float(duration_ms),
                         int(count)])

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
        if len(channels) != self.expected_channels:
            logger.debug("dropping /leds with %d channels, expected %d",
                         len(channels), self.expected_channels)
            return ""
        frame = bytes(int(v) & 0xFF for v in channels)
        # timestamp 0.0 means "no declared time"; None is what TimedQueue
        # reads as that, and it must NOT count as a clamp. Buffered rather
        # than pushed here -- see _pending's docstring in __init__.
        when = env.timestamp if env.timestamp else None
        if len(self._pending) == self._pending.maxlen:
            logger.debug("pending-frame backlog at %d; dropping oldest "
                         "frame -- is tick() being called?",
                         self._pending.maxlen)
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

    @property
    def lateness(self) -> list[float]:
        """Signed (tick time - declared time), one entry per frame that
        carried a declared time. Negative means the frame arrived with room
        to spare.

        The magnitude behind `clamped`: that counter says the horizon is
        wrong, this says by how much. On the o2lite path both ends read the
        same O2 clock, so these are directly comparable and are what
        BootConfig.cue_horizon is measured from -- see
        docs/superpowers/specs/2026-08-14-cue-horizon-measurement-design.md.
        Bounded by TimedQueue; a long run keeps the most recent samples.
        """
        return list(self._frames.lateness)

    def _on_release(self, env) -> str:
        self.released = True
        if self.leds is not None:
            self.leds.clear()
        return env.address


async def pump_tick(client, interval: float = _TICK_INTERVAL) -> None:
    """Drive ``client.tick()`` at the render rate until the client releases.

    Shared by every asyncio-based devicelink client loop (this module's own
    ``main()`` and ``harness/room_simulator.py``); ``harness/o2_shroom.py``
    is deliberately NOT one of them -- its loop is synchronous o2lite
    polling, a different shape, not a copy of this one.

    Has to run concurrently with a client's inbound pump, not just once per
    inbound frame: a frame timed for the future needs ticks to keep landing
    while the inbound pump sits blocked waiting on the next message.

    Uses time.monotonic(), matching DeviceLinkAgent's default clock
    (devicelink/agent.py: clock=time.monotonic). Whether that agrees with
    the sender's own `now` depends on who is driving `client`: true by
    construction when the caller is a locally-spawned subprocess (e.g.
    harness/room_simulator.py, always spawned by harness/terrarium_boot.py
    on Control's own machine), NOT true for a real over-network device
    (e.g. this module's own Radxa deployment) -- two machines' monotonic()
    clocks share no epoch. That mismatch is real and unresolved here on
    purpose; the design spec's o2lite clock is what actually fixes it.
    This helper just keeps local/simulated runs working in the meantime.
    """
    while not client.released:
        client.tick(time.monotonic())
        await asyncio.sleep(interval)


def main() -> None:
    """Connect to a DeviceLinkServer and run the sensor-up / LED-down loop."""
    import argparse
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

            await asyncio.gather(pump_down(), pump_up(), pump_tick(client))

    asyncio.run(run())


if __name__ == "__main__":
    main()
