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
import time

from control.wire_json import dumps as _json_dumps

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
    return Blob(_json_dumps(value).encode("utf-8"))

# The complete services string. set_services REPLACES rather than appends
# (o2litepy o2lite.py:707), and pyarco has already claimed "actl"
# (pyarco/arco_engine.py:98), so Control writes both or silently breaks
# Arco's control replies.
SERVICES = "actl,game"

# Every /game/* verb the agent routes. Registered as full-path handlers so
# o2lite dispatches straight into the drain queue.
GAME_VERBS = ("hello", "join", "tilt", "tap", "shake", "capture",
              "telemetry", "canvas")


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


# The nonce verify_service_ownership round-trips. Fixed rather than random:
# the check is one request and one response with no concurrency, and a
# constant keeps the test deterministic.
_OWNERSHIP_NONCE = 0x5643484B          # "VCHK"

# How often the ownership check pumps o2lite while waiting for its own
# message to come back.
_OWNERSHIP_POLL_INTERVAL = 0.005


def verify_service_ownership(o2lite, service: str, *, timeout: float = 2.0,
                             resend_interval: float | None = None,
                             clock=time.monotonic, sleep=time.sleep) -> bool:
    """Does `service` actually route back to THIS o2lite connection?

    o2lite's /_o2/*/sv is fire-and-forget. O2 refuses a second claimant
    with "not from service provider" (o2/src/bridge.cpp:231-237) and logs
    it on the HUB, never telling the client. A client that lost that race
    clock-syncs and is indistinguishable from a healthy one, while nothing
    addressed to it ever arrives -- it is delivered to whoever won.

    Boundary rule 4 is what makes this measurable: o2lite send() has no
    local short circuit, so a message addressed to our own service leaves
    for the hub and comes back only if the hub really routes that service
    to us. Rule 4 also asks that Control never message itself; this is a
    deliberate, documented exception -- ONE message, before the tick loop
    starts, as a startup assertion rather than a steady-state path.

    Sent over TCP (send_cmd), because a dropped UDP datagram would be
    indistinguishable from a lost service. Returns a bool and raises
    nothing: each caller decides what a failed check means. `clock` and
    `sleep` are injected so a test can exhaust the timeout without
    spending real time, the same way control/boot.py's
    wait_for_room_binding already does.

    `resend_interval`, if set, resends the same fixed-nonce svcheck every
    time that many seconds elapse without a reply. This is safe because
    the nonce is constant: a late reply to an early send is
    indistinguishable from a reply to a fresh one, and for this check
    that is exactly correct -- either way the hub just proved it routes
    `service` back to us. Resending is what lets the probe outwait a hub
    that is merely blocked (a cold audio-device open, an undrained pty)
    rather than misdiagnosing it as a second claimant after one silent
    2s window.
    """
    received = []

    def _on_check(address, typespec, info) -> None:
        received.append(o2lite.get_int32())

    o2lite.method_new(f"/{service}/_svcheck", "i", True, _on_check, None)
    o2lite.send_cmd(f"/{service}/_svcheck", 0, "i", _OWNERSHIP_NONCE)

    deadline = clock() + timeout
    next_resend = (clock() + resend_interval
                   if resend_interval is not None else None)
    while True:
        o2lite.poll()
        if _OWNERSHIP_NONCE in received:
            return True
        if clock() >= deadline:
            return False
        if next_resend is not None and clock() >= next_resend:
            o2lite.send_cmd(f"/{service}/_svcheck", 0, "i", _OWNERSHIP_NONCE)
            next_resend += resend_interval
        sleep(_OWNERSHIP_POLL_INTERVAL)


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
        # Services the hub has REFUSED to register to this connection. A
        # message addressed to one of these never routes back, exactly as
        # O2 behaves when a second claimant loses the race
        # (o2/src/bridge.cpp:231-237). Boundary rule 5: the double has to
        # encode the strictness, not only the shape -- a fake that looped
        # every send back would hide precisely the bug this models.
        self.refused_services: set[str] = set()

    def time_get(self) -> float:
        return self._now

    def set_time(self, now: float) -> None:
        self._now = now

    def set_services(self, services: str) -> None:
        self.services = services

    def refuse(self, service: str) -> None:
        """Model the hub refusing this connection's claim on `service`."""
        self.refused_services.add(service)

    def _owns(self, address: str) -> bool:
        """Does the hub route `address` back to this connection?"""
        service = address.lstrip("!/").split("/")[0]
        claimed = [name for name in self.services.split(",") if name]
        return service in claimed and service not in self.refused_services

    def method_new(self, path, typespec, full, handler, info) -> None:
        self.handlers[path] = handler

    def send(self, addr, timestamp, *args) -> None:
        typespec = args[0] if len(args) > 1 else ""
        rest = tuple(args[1:])
        self.sent.append((addr, timestamp, typespec, rest))
        # The hub has no local short circuit either (boundary rule 4): a
        # message addressed to a service THIS connection owns goes out and
        # comes back around to our own handler. That round trip is what
        # verify_service_ownership reads, so the fake has to reproduce it.
        if self._owns(addr):
            self._queue.append((addr, typespec, rest, timestamp))

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

    def start(self, o2lite, *, ownership_timeout: float = 10.0,
              clock=time.monotonic, sleep=time.sleep) -> None:
        """Adopt an already-connected o2lite object and claim `game` on it.

        Raises RuntimeError if the clock is not synced: time_get() returns
        -1 before sync, and a cue scheduled against -1 is meaningless.

        Also raises RuntimeError if the hub does not route `game` back
        here. set_services is fire-and-forget and O2 refuses a second
        claimant silently, so without this check an orphaned Terrarium
        holding `game` would make every device unreachable with no error
        anywhere. See verify_service_ownership on why the round trip is a
        deliberate, one-shot exception to boundary rule 4.
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
        if not verify_service_ownership(o2lite, "game",
                                        timeout=ownership_timeout,
                                        resend_interval=2.0,
                                        clock=clock, sleep=sleep):
            self._o2 = None
            raise RuntimeError(
                "the `game` service did not route back to this connection "
                f"within {ownership_timeout:.0f}s. Most likely the hub is "
                "blocked and cannot answer yet: a cold audio-device open "
                "blocks Arco for seconds after idle, and an undrained pty "
                "freezes it entirely (see the SETUP-hold drain rule in "
                "harness/terrarium_boot.py). The rarer cause is a genuine "
                "second claimant already offering `game` -- O2 refuses "
                "silently (o2/src/bridge.cpp:231-237) and logs only on the "
                "hub. Check o2debug.log: a frozen hub shows no recent "
                "lines at all; a conflict shows this connection's own "
                "`sv` being refused.")

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
