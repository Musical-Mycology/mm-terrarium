"""The o2lite-backed device transport: Control's `game` service on the Arco
hub.

Satisfies the same small interface DeviceLinkServer does (drain_new_clients
/ drain_inbound / send / bind_dev / drop_dev), so DeviceLinkAgent is
unchanged by the swap. See docs/superpowers/specs/
2026-08-12-control-o2lite-and-timed-cues-design.md section 5.1.

o2litepy is NEVER imported at module level here. The caller passes an
already-initialized o2lite object into start(), which is how
harness/terrarium_boot.py hands over the connection pyarco owns. That keeps
this module importable, and the offline suite green, with no o2litepy on
the path.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# o2litepy refuses a service name longer than this (o2lite.py:697), and a
# dev id is the device's own service name.
MAX_DEV_LEN = 31


class Blob:
    """Duck-types o2litepy's O2blob.

    o2litepy's _add_blob reads only `.size` and `.data`, so this needs no
    o2litepy import at all -- which is what keeps this module importable,
    and the offline suite green, with no o2litepy on the path.
    """

    __slots__ = ("size", "data")

    def __init__(self, raw: bytes) -> None:
        self.size = len(raw)
        self.data = bytearray(raw)


def to_o2_arg(type_char: str, value):
    """Convert one JSON-envelope argument into what o2litepy's send expects.

    'b' is the one that matters. Passing a Python list (36 LED ints) or a
    dict (the role config) straight through raises AttributeError inside
    _add_blob, which reads x.size and x.data. A list of ints is raw bytes;
    anything else is UTF-8 JSON, which is what the device decodes on the
    other side. Every other type char passes through untouched.
    """
    if type_char != "b":
        return value
    if isinstance(value, (bytes, bytearray)):
        return Blob(bytes(value))
    if isinstance(value, list) and all(isinstance(v, int) for v in value):
        return Blob(bytes(v & 0xFF for v in value))
    return Blob(json.dumps(value).encode("utf-8"))

# The complete services string. set_services REPLACES rather than appends
# (o2litepy o2lite.py:707), and pyarco has already claimed "actl"
# (pyarco/arco_engine.py:98), so Control writes both or silently breaks
# Arco's control replies.
SERVICES = "actl,game"

# Every /game/* verb the agent routes. Registered as full-path handlers so
# o2lite dispatches straight into the drain queue.
GAME_VERBS = ("hello", "join", "tilt", "tap", "shake", "capture", "telemetry")


# Inbound arguments are PULLED off the o2lite object one at a time, in
# typespec order (o2litepy o2lite-api.md, "Values are returned sequentially
# from the message"). There is no prebuilt args list.
_GETTERS = {"s": "get_string", "i": "get_int32", "f": "get_float",
            "d": "get_double", "h": "get_int64", "t": "get_time",
            "b": "get_blob", "B": "get_bool"}


def pull_args(o2lite, typespec: str) -> list:
    """Read one message's arguments off `o2lite`, in typespec order."""
    args = []
    for type_char in typespec:
        getter = _GETTERS.get(type_char)
        if getter is None:
            raise ValueError(f"unsupported O2 type {type_char!r}")
        value = getattr(o2lite, getter)()
        if type_char == "b":
            value = from_o2_arg(value)
        args.append(value)
    return args


def from_o2_arg(blob):
    """Decode an inbound blob back into a list of ints or a JSON value.

    o2litepy's get_blob returns an O2blob with .size and .data (its own doc
    says "as bytes", but the code returns the object -- trust the code).
    LED frames are raw bytes; everything else was written as UTF-8 JSON by
    to_o2_arg above.
    """
    raw = bytes(getattr(blob, "data", blob))[:getattr(blob, "size", None)]
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return list(raw)


class FakeO2Lite:
    """In-process double, sibling of control/audio.py's FakePool. Records
    what was sent and lets a test deliver inbound messages with no hub.

    It reproduces two real o2litepy behaviors that are easy to get wrong:
    a handler takes exactly THREE parameters, and it receives the address
    with its leading '/' already stripped (O2lite_handler.__init__ does
    `self.address = address[1:]`, and _msg_dispatch compares against the
    stripped form).
    """

    def __init__(self, now: float = 100.0) -> None:
        self._now = now
        self.services = ""
        self.sent: list[tuple[str, float, str, tuple]] = []
        self.handlers: dict[str, object] = {}
        self.msg_timestamp = 0.0
        self._pull: list = []
        # Messages deliver() has queued but poll() has not yet dispatched --
        # see poll()'s docstring for why this queue exists at all.
        self._queue: list[tuple[str, str, tuple, float]] = []

    def time_get(self) -> float:
        return self._now

    def set_time(self, now: float) -> None:
        self._now = now

    def set_services(self, services: str) -> None:
        self.services = services

    def method_new(self, path, typespec, full, handler, info) -> None:
        self.handlers[path] = handler

    def send(self, addr, timestamp, *args) -> None:
        typespec = args[0] if len(args) > 1 else ""
        self.sent.append((addr, timestamp, typespec, tuple(args[1:])))

    def send_cmd(self, addr, timestamp, *args) -> None:
        self.send(addr, timestamp, *args)

    def poll(self) -> None:
        """Dispatch every message deliver() has queued since the last poll.

        This is the one behavior real o2litepy is strict about and the old
        fake was not: o2lite only calls a registered handler from inside
        poll(). A caller that never pumps o2lite never sees a message, no
        matter how many were delivered to the hub -- which is exactly the
        live bug this fake used to hide by dispatching from deliver()
        directly.
        """
        queue, self._queue = self._queue, []
        for address, typespec, args, timestamp in queue:
            handler = self.handlers.get(address)
            if handler is None:
                continue
            self.msg_timestamp = timestamp
            self._pull = list(args)
            handler(address[1:], typespec, None)   # leading '/' stripped

    def deliver(self, address: str, typespec: str, args: tuple,
                timestamp: float = 0.0) -> None:
        """Simulate an inbound message arriving at the hub. Queues only --
        poll() is what actually dispatches to a registered handler, matching
        real o2litepy."""
        self._queue.append((address, typespec, tuple(args), timestamp))

    # --- the pull-style getters ------------------------------------------

    def _next(self):
        return self._pull.pop(0)

    def get_string(self):
        return self._next()

    def get_int32(self):
        return self._next()

    def get_int64(self):
        return self._next()

    def get_float(self):
        return self._next()

    def get_double(self):
        return self._next()

    def get_time(self):
        return self._next()

    def get_bool(self):
        return self._next()

    def get_blob(self):
        raw = self._next()
        return Blob(bytes(raw) if isinstance(raw, (bytes, bytearray, list))
                    else raw)


class O2LiteTransport:
    def __init__(self, services: str = SERVICES) -> None:
        self._services = services
        self._o2 = None
        self._inbound: list[tuple[object, dict]] = []
        self._devs: dict[str, object] = {}

    def start(self, o2lite) -> None:
        """Adopt an already-connected o2lite object and claim `game` on it.

        Raises RuntimeError if the clock is not synced: time_get() returns
        -1 before sync, and a cue scheduled against -1 is meaningless.
        """
        now = o2lite.time_get()
        if now < 0:
            raise RuntimeError(
                "o2lite clock is not synchronized (time_get() < 0); "
                "Arco must be clock master before Control offers `game`")
        self._o2 = o2lite
        o2lite.set_services(self._services)
        for verb in GAME_VERBS:
            # typespec None means "match any": a verb's shape is the Bit's
            # business, and GameServer.data already validates it.
            o2lite.method_new(f"/game/{verb}", None, True,
                              self._on_message, None)

    def _on_message(self, address, typespec, info) -> None:
        """o2lite handler.

        THREE parameters, not four: o2litepy calls
        `h.handler(address, types, h.info)` and hands over no args list.
        Arguments are pulled off the o2lite object in typespec order.

        `address` arrives with its leading '/' already stripped, because
        O2lite_handler.__init__ strips it from the registered path and
        _msg_dispatch compares the stripped forms. Re-prefix it so the
        envelope the agent sees is identical to the websocket transport's.
        """
        try:
            args = pull_args(self._o2, typespec or "")
        except Exception:
            logger.exception("dropping /%s: unreadable arguments", address)
            return
        self._inbound.append((None, {
            "timestamp": getattr(self._o2, "msg_timestamp", 0.0),
            "address": f"/{address}",
            "typespec": typespec or "",
            "args": args}))

    # --- the transport interface ------------------------------------------

    def drain_new_clients(self) -> list:
        """No connections to accept: a device is anonymous until it sends
        /game/hello. agent.py:150 already tolerates an empty list."""
        return []

    def drain_inbound(self) -> list:
        """Pump o2lite, then return everything that arrived.

        o2litepy only dispatches inbound messages to registered handlers
        from inside o2lite.poll() -- nothing else in Control's tick loop
        pumps it, so this is the one place that must. Guarded for
        self._o2 is None (nothing to pump before start()), and a raising
        poll() is swallowed rather than let escape into the engine tick --
        boundary rule 2, same as send() below.
        """
        if self._o2 is not None:
            try:
                self._o2.poll()
            except Exception:
                logger.exception("o2lite poll failed")
        drained, self._inbound = self._inbound, []
        return drained

    def bind_dev(self, dev: str, client) -> None:
        if not dev or len(dev) > MAX_DEV_LEN:
            raise ValueError(
                f"dev id {dev!r} is not a valid O2 service name "
                f"(1..{MAX_DEV_LEN} characters)")
        self._devs[dev] = client

    def drop_dev(self, dev: str) -> None:
        self._devs.pop(dev, None)

    def send(self, dev: str, msg: dict) -> None:
        """Send one outbound envelope to `dev`'s own service.

        Unknown dev is a silent no-op, matching DeviceLinkServer: a cue for
        a device that has gone away must never raise into the engine tick.
        """
        if dev not in self._devs or self._o2 is None:
            return
        typespec = msg.get("typespec", "")
        raw_args = msg.get("args", [])
        args = [to_o2_arg(t, v) for t, v in zip(typespec, raw_args)]
        try:
            self._o2.send(msg["address"], msg.get("timestamp", 0.0),
                          typespec, *args)
        except Exception:
            logger.exception("o2lite send to %s failed", dev)

    def stop(self) -> None:
        self._o2 = None
        self._devs.clear()
        self._inbound.clear()
