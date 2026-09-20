"""The scenario recorder: drives a real GameServer + DeviceLinkAgent over
O2LiteTransport(FakeO2Lite), scripts the device's own sends directly (this
process IS the device, from the hub's point of view), and captures every
message Control sends back into an EXPORT FORMAT v1 scenario dict
(docs/superpowers/specs/2026-09-16-device-contract-kit-design.md sections
4.2-4.3, 5.4).

Determinism is the property that matters: a scenario's committed JSON is
re-recorded by a regression test and any diff fails it. So nothing here
reads a wall clock, a commit hash, a random number, or the iteration order
of a set, and every recorded time is an integer millisecond offset from the
scenario's own start. See "Determinism" below for the full list.

Nothing in contract_kit/ imports from tests/: the package has to work from
a plain CLI (tools/record_scenarios.py) with no pytest on the path.
"""
from __future__ import annotations

import re
from pathlib import Path

from control.catalog import load_catalog
from control.engine import GameServer
from control.rooms import Room
from control.terrarium_config import load_terrarium_config
from devicelink.agent import DeviceLinkAgent
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport, from_o2_arg

from contract_kit.contract_bit import ContractBit

REPO_ROOT = Path(__file__).resolve().parents[1]

# The scripted device's real dev id. Replaced by "$DEV" everywhere in the
# recorded output, so no scenario file ever names it.
DEV = "ct1"

# The instrument the scripted device declares at hello. Not "testshroom":
# that fixture advertises no gesture.hold/gesture.swing and ContractBit's
# requires="rev1" gate would refuse it.
INSTRUMENT = "tuneshroom_rev1"

# The room a with_room recorder loads, and the sentinel devs its two
# fixtures bind to. Those devs are never this recorder's own device, so
# nothing a fixture renders reaches the wire this rig inspects.
ROOM_NAME = "TEST"
ROOM_NODE_ID = "ROOM_TEST_NODE"
ROOM_FIXTURE_DEVS = {"main": "sim-main", "accent": "sim-accent"}

# The render/tick rate every other rig in this repo ticks at
# (harness/shroom_client.py's _TICK_INTERVAL, tests/test_lobby_agent.py's
# _poll). Ticking, rather than jumping straight to a target time, is what
# gives two light cues due a few hundred ms apart their own agent.poll()
# each: TimedQueue.due(now) returns every payload whose time has passed in
# ONE call, so a coarse jump would render only the last of them.
TICK_MS = round(1000.0 / 44.0)

# The device's hello heartbeat interval (spec section 4.3, rule 1).
HELLO_INTERVAL_MS = 5000

# The replay tolerance every recorded expectation carries: in-process
# replay runs on a fake clock and allows a frame one render tick of slack
# (spec section 4.3, "Timing").
DEFAULT_WITHIN_MS = 50

_KEY_RE = re.compile(r"key=\d+")


def _normalize_key(value):
    """The lobby's join chime carries "key=<midi note>", which counts joins
    rather than describing the wire (devicelink/lobby_runtime.py). A device
    must not be asked to reproduce the number, so it becomes "$KEY"."""
    if isinstance(value, str):
        return _KEY_RE.sub("key=$KEY", value)
    return value


class Recorder:
    """One scenario's rig. Construct, script the device, then finish().

    Determinism, and what each source of drift is neutralized with:

    - Wall clock. Never read. The fake o2lite's own clock is Control's
      clock here too; it starts at exactly 0.0 and only advance_to() moves
      it, so the agent's `_breath_origin`, the lobby's origin and every
      TimedQueue deadline are fixed.
    - Commit hash. Not stamped at all (the spec puts `_provenance` only in
      the export's contract.json; a hash in every recording would dirty all
      of them on every re-record).
    - Float formatting of times. Every recorded `t` is an integer
      millisecond, and every `at` is `round(seconds * 1000)`, an int. No
      float ever reaches the JSON as a time.
    - Absolute O2 times. The clock starts at exactly 0.0, so an absolute
      O2 time IS an offset from the scenario's start.
    - Dev ids and the lobby's chime key. Rewritten to "$DEV" and "$KEY".
    - Set iteration order. Scenarios are single-device by design (spec
      section 5.4): DeviceLinkAgent's one set-ordered send loop
      (`set(self._overrides) | self._override_only` in _render_frames)
      can only ever emit for one dev here, and the Room fixtures' own
      sends go to `sim-main`/`sim-accent` and are filtered out of the
      capture entirely.
    - Join-order-dependent ids. The only one Control produces is the chime
      key, covered above.
    - The idle breath start time. Control's per-tick breath feed is
      anchored on DeviceLinkAgent's `_breath_origin`, which is the fake
      clock's 0.0 here; ContractBit's player role also sets `breath=False`
      and maps no cc:11 lane, so the feed never reaches the frame anyway.
      The rendered surface still changes every tick, because the role's
      aurora instrument animates on its own; that animation is a pure
      function of the same fake clock, so it re-records identically.
    """

    def __init__(self, *, name: str, summary: str, profiles=("rev1",),
                 join_node: str | None = None, with_room: bool = False,
                 dev: str = DEV) -> None:
        self.name = name
        self.summary = summary
        self.profiles = list(profiles)
        self.join_node = join_node
        self.dev = dev
        # Hand-authored steps (inputs and expectations). Control's own
        # captured sends live in self._sent and are merged in finish().
        self.steps: list[dict] = []
        self._sent: list[tuple[int, str, float, str, list]] = []
        self._now_ms = 0
        self._linked_up = False
        self._next_hello_ms: int | None = None

        self._fake = FakeO2Lite(now=0.0)
        # O2LiteTransport.start refuses a connection that has not seen
        # pyarco announce its own service first, and REPLACES the string
        # with "actl,game".
        self._fake.set_services("actl")
        self._transport = O2LiteTransport()
        self._transport.start(self._fake)
        # Wrapped AFTER start() returns, so the two _svcheck probes
        # verify_service_ownership sends during start() are never captured.
        # The wrapper filters anything not addressed to our own dev anyway,
        # which covers a later probe (a reconnect re-checks ownership) and
        # every Room fixture's own frames.
        self._orig_send = self._fake.send
        self._fake.send = self._wrapped_send

        catalog = load_catalog(REPO_ROOT / "instruments").published
        self._gs = GameServer({"ContractBit": ContractBit},
                              clock=self._fake.time_get,
                              carried_instruments=catalog)
        if with_room:
            profile = load_terrarium_config(
                str(REPO_ROOT / "terrarium.toml")).rooms[ROOM_NAME].profile
            self._gs.room = Room(name=ROOM_NAME, profile=profile,
                                 node_id=ROOM_NODE_ID)
            for fixture, fixture_dev in ROOM_FIXTURE_DEVS.items():
                self._gs.room.bound[fixture] = fixture_dev
        self._agent = DeviceLinkAgent(self._gs, self._transport,
                                      clock=self._fake.time_get)
        self._gs.load_bit("ContractBit")

    # --- capture -----------------------------------------------------------

    def _wrapped_send(self, addr: str, timestamp: float, *raw_args) -> None:
        """Every outbound o2lite message passes through here.

        Wrapping the FAKE's send rather than O2LiteTransport.send is what
        lets this see Blob-wrapped arguments exactly as the wire carries
        them, which is why from_o2_arg is the right decoder for them.
        """
        self._orig_send(addr, timestamp, *raw_args)
        if not addr.startswith(f"/{self.dev}/"):
            return                      # a _svcheck probe, or a Room fixture
        typespec = raw_args[0] if raw_args else ""
        values = [from_o2_arg(v) if t == "b" else v
                  for t, v in zip(typespec, raw_args[1:])]
        self._sent.append((self._now_ms, addr, timestamp, typespec, values))

    # --- clock / link ------------------------------------------------------

    def _set_now(self, now_ms: int) -> None:
        """One clock for the whole rig: the fake o2lite's time_get is what
        both GameServer and DeviceLinkAgent were built with."""
        self._now_ms = now_ms
        self._fake.set_time(now_ms / 1000.0)

    def advance_to(self, target_ms: int) -> None:
        """Tick the rig forward to `target_ms`, one render tick at a time,
        sending the device's hello heartbeat whenever one comes due."""
        while self._now_ms < target_ms:
            step_ms = min(TICK_MS, target_ms - self._now_ms)
            if (self._linked_up and self._next_hello_ms is not None
                    and self._next_hello_ms < self._now_ms + step_ms):
                # Land exactly on the heartbeat rather than stepping past
                # it, so a hello is stamped at 5000 and not at 5014.
                step_ms = self._next_hello_ms - self._now_ms
            self._set_now(self._now_ms + step_ms)
            self._agent.poll()
            if (self._linked_up and self._next_hello_ms is not None
                    and self._now_ms >= self._next_hello_ms):
                self._send_hello(self._now_ms)
                self._next_hello_ms += HELLO_INTERVAL_MS

    def link_up(self, t_ms: int = 0) -> None:
        """The device's link comes up at `t_ms`: it hellos immediately, and
        every HELLO_INTERVAL_MS after that until link_down. A recorder
        built with a join_node also sends that join right away."""
        self.advance_to(t_ms)
        self.steps.append({"t": t_ms, "link": "up"})
        self._linked_up = True
        self._send_hello(t_ms)
        self._next_hello_ms = t_ms + HELLO_INTERVAL_MS
        if self.join_node is not None:
            self._send_join(t_ms, self.join_node)

    def link_down(self, t_ms: int) -> None:
        """The device's link drops at `t_ms`: the heartbeat stops."""
        self.advance_to(t_ms)
        self.steps.append({"t": t_ms, "link": "down"})
        self._linked_up = False
        self._next_hello_ms = None

    def _send_hello(self, t_ms: int) -> None:
        self._fake.deliver("/game/hello", "ssss",
                           (self.dev, "contract-kit", "1", INSTRUMENT),
                           timestamp=t_ms / 1000.0)
        self._agent.poll()

    def _send_join(self, t_ms: int, node: str) -> None:
        self._fake.deliver("/game/join", "ss", (self.dev, node),
                           timestamp=t_ms / 1000.0)
        self._agent.poll()

    def join_now(self, t_ms: int, node: str) -> None:
        """An explicit join sent LATER than link_up, for a scenario whose
        device hellos with join_node=None and only decides to join partway
        through.

        Sends the join through the same path every other scripted device
        message takes, and records the device's own expect_out for it, so
        finish() reports exactly what this rig did. `device.join_node`
        stays None on purpose: a replaying device must not join at link-up
        just because it joins later.

        Records the expectation itself, so a caller does NOT also call
        expect_join for the same join.
        """
        self.advance_to(t_ms)
        self._send_join(t_ms, node)
        self.expect_join(t_ms, node)

    # --- gestures ----------------------------------------------------------

    def _gesture(self, onset_t: int, kind: str, typespec: str, wire_args: tuple,
                 step_args: list, detail: dict) -> None:
        """One classified gesture: the input step, the scripted send, and
        the device's own expect_out, all stamped at the gesture's onset
        (spec section 4.3, rule 5)."""
        self.advance_to(onset_t)
        self.steps.append({"t": onset_t, "gesture": {
            "kind": kind, "onset_t": onset_t, **detail}})
        self._fake.deliver(f"/game/{kind}", typespec, wire_args,
                           timestamp=onset_t / 1000.0)
        self._agent.poll()
        self.steps.append({"t": onset_t, "expect_out": {
            "address": f"/game/{kind}", "typespec": typespec,
            "args": step_args, "stamp_t": onset_t,
            "within_ms": DEFAULT_WITHIN_MS}})

    def tap(self, onset_t: int, duration_ms: float) -> None:
        """A touch tap: peak_g is 0 on Rev 1, count is always 1."""
        self._gesture(onset_t, "tap", "sffi",
                      (self.dev, 0.0, float(duration_ms), 1),
                      ["$DEV", 0.0, float(duration_ms), 1],
                      {"duration_ms": float(duration_ms)})

    def hold(self, onset_t: int, held_s: float) -> None:
        """A touch held past the hold window, stamped at touch-down."""
        self._gesture(onset_t, "hold", "sfi",
                      (self.dev, float(held_s), 1),
                      ["$DEV", float(held_s), 1],
                      {"held_s": float(held_s)})

    def swing(self, onset_t: int, signed_g: float) -> None:
        """A swing, stamped at onset. Negative means left."""
        self._gesture(onset_t, "swing", "sfi",
                      (self.dev, float(signed_g), 1),
                      ["$DEV", float(signed_g), 1],
                      {"signed_g": float(signed_g)})

    # --- expectations ------------------------------------------------------

    def expect_hello(self, t_ms: int) -> None:
        """The device must hello at `t_ms`. Everything but the dev id is a
        `*` placeholder: a device's own name, protoversion and instrument
        are its business, not the contract's."""
        self.steps.append({"t": t_ms, "expect_out": {
            "address": "/game/hello", "typespec": "ssss",
            "args": ["$DEV", "*", "*", "*"], "stamp_t": None,
            "within_ms": DEFAULT_WITHIN_MS}})

    def expect_join(self, t_ms: int, node: str) -> None:
        """The device must join `node` at `t_ms`."""
        self.steps.append({"t": t_ms, "expect_out": {
            "address": "/game/join", "typespec": "ss",
            "args": ["$DEV", node], "stamp_t": None,
            "within_ms": DEFAULT_WITHIN_MS}})

    def expect_quiet(self, t_ms: int, addresses: list[str], for_ms: int) -> None:
        """None of `addresses` may be sent from `t_ms` for `for_ms`."""
        self.steps.append({"t": t_ms, "expect_quiet": {
            "addresses": list(addresses), "for_ms": for_ms}})

    def expect_frame(self, t_ms: int) -> None:
        """The pixels showing at `t_ms`: the last /leds frame Control had
        actually sent by then. Raises if there is none, rather than
        recording an expectation of nothing."""
        frames = [values[0] for (t, addr, _ts, _typespec, values) in self._sent
                  if addr == f"/{self.dev}/leds" and t <= t_ms]
        if not frames:
            raise AssertionError(
                f"no /leds frame sent to {self.dev} by t={t_ms}ms")
        self.steps.append({"t": t_ms, "expect_frame": {"grb": frames[-1]}})

    def expect_play(self, t_ms: int, name: str,
                    within_ms: int = DEFAULT_WITHIN_MS) -> None:
        """Sample `name` must play. Recorded at the time Control really
        sent it, not at `t_ms`, which is only the deadline searched up to."""
        matches = [(t, values)
                   for (t, addr, _ts, _typespec, values) in self._sent
                   if addr == f"/{self.dev}/play" and t <= t_ms
                   and values[0] == name]
        if not matches:
            raise AssertionError(
                f"no /play {name!r} sent to {self.dev} by t={t_ms}ms")
        t, values = matches[-1]
        self.steps.append({"t": t, "expect_play": {
            "name": values[0], "params": _normalize_key(values[1]),
            "within_ms": within_ms}})

    # --- lifecycle / hand-authored input -----------------------------------

    def unload_bit(self) -> None:
        """Unload the Bit, which releases every joined device."""
        self._gs.abort()
        self._agent.poll()

    def control_send_now(self, address: str, typespec: str, args: list,
                         at: int | None = None) -> None:
        """A hand-authored control_sends step (the malformed-input
        scenario) -- appended directly rather than captured, since it is
        never actually sent through this rig's own O2 connection."""
        self.steps.append({"t": self._now_ms, "control_sends": {
            "address": address, "typespec": typespec, "args": list(args),
            "at": at}})

    # --- output ------------------------------------------------------------

    def finish(self) -> dict:
        """The EXPORT FORMAT v1 scenario dict, steps sorted by `t`."""
        control_steps = []
        for (t, addr, timestamp, typespec, values) in self._sent:
            control_steps.append({"t": t, "control_sends": {
                "address": addr.replace(f"/{self.dev}/", "/$DEV/"),
                "typespec": typespec,
                "args": [_normalize_key(v) for v in values],
                # timestamp 0.0 means "no declared presentation time"
                # (devicelink/protocol.py's leds_event), not "at t=0".
                "at": None if not timestamp else round(timestamp * 1000)}})
        # Stable: sorted() is stable, so Control's captured sends keep their
        # real order among themselves within one millisecond, and the
        # hand-authored steps keep theirs.
        all_steps = sorted(self.steps + control_steps, key=lambda s: s["t"])
        return {
            "name": self.name,
            "summary": self.summary,
            "profiles": self.profiles,
            "device": {"join_node": self.join_node},
            "steps": all_steps,
        }
