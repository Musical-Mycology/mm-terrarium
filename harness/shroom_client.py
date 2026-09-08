"""Devicelink participation for a Shroom-shaped device -- real or Testshroom.

What is a Testshroom: the harness's own instrument type, used in testing.
Every simulated device this repo spawns (harness/o2_shroom.py, the smoke
drivers, run_stack's browser canvases) is a Testshroom: a browser-canvas
instrument with a 12 px GRB surface and the standard gesture verbs. It is
deliberately NOT defined as "a simulated Tuneshroom": its shape happens to
match today's Tuneshroom wire, but it is decoupled from what real Tuneshroom
hardware becomes -- the Testshroom's job is to exercise Control's seams, not
to track a hardware design. This class is the shared protocol client both
kinds of device use.

Socket-free by design: ``handle()`` takes a decoded JSON message and returns the
address it handled, or ``""`` if it dropped the frame. That keeps the whole
protocol surface testable on a laptop, and it matches the engine's rule that a
malformed frame is "drop this frame", never an error.

The process that runs this client is harness/o2_shroom.py.

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
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Callable

from control.timed_queue import TimedQueue
from devicelink import protocol

logger = logging.getLogger(__name__)

# 12 pixels x GRB, per protocol.leds_event: the Testshroom's own declared
# surface shape. It matches today's Tuneshroom wire but is not defined AS that
# wire -- if the hardware changes shape, the Testshroom does not have to
# follow. (Hardware note: the real parts are SK6812 RGBW and the white die is
# currently unreachable over this wire; see the plan's Task B7, a pending
# decision rather than a bug to fix here.)
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
                 on_play: Callable[[str, str], None] | None = None,
                 expected_channels: int = LED_CHANNELS,
                 instrument: str | None = None) -> None:
        self.dev = dev
        self.node = node
        self.leds = leds
        # The carried instrument this client declares on hello(), read by
        # devicelink/agent.py's _on_hello at args[3]. None (the default)
        # keeps hello() at its old 1-arg shape, byte-identical for every
        # existing caller; an undeclared device resolves to
        # "defaultshroom" on the agent side. See harness/o2_shroom.py's
        # --instrument for the CLI-threaded case.
        self.instrument = instrument
        self.on_role = on_role
        # /<dev>/play sink, called (name, params) per PlayCue. Optional so
        # every existing caller is unchanged; a raising sink is logged and
        # never propagates -- a broken speaker must not kill the session.
        self.on_play = on_play
        # Frame width this client will accept, in channels. Defaults to the
        # Testshroom's 12 px x GRB shape, so every existing caller is
        # unchanged. The Room simulator passes its RoomProfile.channel_count
        # instead: a Room is not a Testshroom and does not have 36 channels.
        # See control/room_profile.py.
        self.expected_channels = expected_channels
        self.config: dict | None = None
        self.released = False
        self.last_deny: tuple[str, str] | None = None
        self.last_error: tuple[str, str] | None = None
        self.last_play: tuple[str, str] | None = None
        # Latest informational room snapshot (see protocol.room_event).
        # Stored, not acted on: this client has no node tiles to draw, but
        # handling the kind keeps the o2lite wire quiet and the data
        # inspectable.
        self.last_room: dict | None = None
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
        """/game/hello. When this client declares an instrument, the
        shape grows to "ssss" [dev, "", "", instrument] -- name and
        protoversion are left blank (this client has no use for them),
        just enough arity to land the instrument at args[3] where
        devicelink/agent.py's _on_hello reads it. Undeclared clients keep
        the original "s" [dev] shape byte-identical, so nothing changes
        for a caller that never opted in."""
        if self.instrument is None:
            return self._up("hello", "s", [self.dev])
        return self._up("hello", "ssss", [self.dev, "", "", self.instrument])

    def canvas(self, url: str) -> dict:
        """Report the URL of this device's own browser canvas, sent once
        right after hello. Devices with no canvas simply never send it."""
        return self._up("canvas", "ss", [self.dev, url])

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
        if kind == "play":
            return self._on_play(env)
        if kind == "room":
            if not env.args or not isinstance(env.args[0], dict):
                logger.debug("dropping /room with a non-dict payload")
                return ""
            self.last_room = env.args[0]
            return env.address
        return ""

    def _on_play(self, env) -> str:
        name = env.args[0] if env.args else ""
        params = env.args[1] if len(env.args) > 1 else ""
        if not isinstance(name, str) or not isinstance(params, str):
            logger.debug("dropping /play with non-string arguments")
            return ""
        self.last_play = (name, params)
        if self.on_play is not None:
            try:
                self.on_play(name, params)
            except Exception:
                logger.exception("on_play sink raised; sample dropped")
        return env.address

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

    def reset_for_lobby(self) -> None:
        """Return this client to its pre-join state so a --persist
        o2_shroom can re-enter the hello+join lobby after a release,
        without reconstructing the client (the WebSim backend and its
        browser tab must survive rounds). Owns exactly which fields a
        round clears, so harness/o2_shroom.py's loop never reaches into
        internals. Cumulative diagnostics (clamped, latency samples) are
        deliberately kept: the exit report spans the whole process."""
        self.config = None
        self.released = False
        self.last_deny = None
        self.last_error = None

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
