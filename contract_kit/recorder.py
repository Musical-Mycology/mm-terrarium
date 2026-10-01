"""The scenario recorder: drives a real GameServer + DeviceLinkAgent over
O2LiteTransport(FakeO2Lite), scripts the device's own sends directly (this
process IS the device, from the hub's point of view), and captures every
message Control sends back into an EXPORT FORMAT v1 scenario dict
(docs/superpowers/specs/2026-09-16-device-contract-kit-design.md sections
4.2-4.3, 5.4).

The rig runs at the PRODUCTION cue horizon (`BootConfig.cue_horizon`,
currently 0.060 s), read from that dataclass's own field default rather
than restated here. The horizon is what turns a cue's origin into the
presentation time `at` that every recorded `/$DEV/leds` step carries, so
recording at the library default of 0.0 would have published presentation
times no installation ever produces.

Determinism is the property that matters: a scenario's committed JSON is
re-recorded by a regression test and any diff fails it. So nothing here
reads a wall clock, a commit hash, a random number, or the iteration order
of a set, and every recorded time is an integer millisecond offset from the
scenario's own start. See "Determinism" below for the full list.

Nothing in contract_kit/ imports from tests/: the package has to work from
a plain CLI (tools/record_scenarios.py) with no pytest on the path.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from control.boot_config import BootConfig
from control.catalog import load_catalog
from control.engine import GameServer
from control.lobby import TERRARIUM_ADMIN
from control.room_binding import RoomBindingRegistry
from control.rooms import Room
from control.terrarium_config import load_terrarium_config
from devicelink.agent import DeviceLinkAgent
from devicelink.contract import HELLO_INTERVAL_S
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport, from_o2_arg

from contract_kit.contract_bit import ContractBit
from contract_kit.solo_contract_bit import SoloContractBit

REPO_ROOT = Path(__file__).resolve().parents[1]


def _production_cue_horizon() -> float:
    """BootConfig.cue_horizon's shipped default, read off the dataclass.

    BootConfig has two required fields (room_name, bit_name) so it cannot
    simply be constructed here, and hard-coding 0.060 would let the rig
    and production drift apart silently.
    """
    for field in dataclasses.fields(BootConfig):
        if field.name == "cue_horizon":
            return float(field.default)
    raise RuntimeError("BootConfig no longer declares cue_horizon")


# The presentation lead every recorded `at` is computed with. Production
# wires this into GameServer(cue_horizon=) and DeviceLinkAgent(horizon=)
# from BootConfig (harness/terrarium_boot.py); so does this rig.
CUE_HORIZON_S = _production_cue_horizon()

# The scripted device's real dev id. Replaced by "$DEV" everywhere in the
# recorded output, so no scenario file ever names it.
DEV = "ct1"

# A second, uncaptured device for scenarios that need one to fill the
# scored slot first (handshake_over_cap_deny). Nothing addressed to it is
# captured, and its id never appears in any recorded blob.
RIVAL_DEV = "ct2"

# How long arm_fixture opens a Room's registration window for.
ARM_WINDOW_S = 10.0

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

# The device's hello heartbeat interval (spec section 4.3, rule 1),
# derived from devicelink/contract.py's own HELLO_INTERVAL_S so this rig
# and tools/export_contract.py's published lifecycle.hello_interval_s can
# never drift apart.
HELLO_INTERVAL_MS = round(HELLO_INTERVAL_S * 1000)

# The replay tolerance every recorded expect_out/expect_play carries:
# roughly two render ticks (TICK_MS * 2 = 46 ms, rounded up to a plain
# number), not the one render tick of slack expect_frame gets (spec
# section 4.3, "Timing").
DEFAULT_WITHIN_MS = 50

# The only down verb devicelink/protocol.py stamps with a presentation
# time; every other builder leaves the envelope timestamp at 0.0. Reading
# the verb rather than testing the timestamp is what keeps a real
# presentation time of 0 distinct from "no declared time".
PRESENTATION_TIME_VERBS = frozenset({"leds"})

# The Bits a recorder can load, by name (Recorder(bit=...)).
BITS = {"ContractBit": ContractBit, "SoloContractBit": SoloContractBit}

# What a recorded round id is rewritten to. GameServer.load_bit mints it
# with a random token (spec 2026-10-01 section 3.2), so the real string can
# never be committed; a device echoes whatever /$DEV/handshake last carried.
ROUND_PLACEHOLDER = "$ROUND"
# What a round id from an EARLIER Bit load is rewritten to, so an echo of a
# superseded round never records as a correct "$ROUND" echo. No committed
# recording reloads a Bit, so this never appears in the export today.
PREV_ROUND_PLACEHOLDER = "$ROUND_PREV"

_KEY_RE = re.compile(r"key=\d+")


def _normalize_key(value: object) -> object:
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
    - Dev ids, the lobby's chime key and the round id. Rewritten to
      "$DEV", "$KEY" and "$ROUND" (the round id carries a random token).
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
      The role's aurora declares an explicit `level`, which opts out of
      that preset's own looping breathe, so the surface is steady for
      steady inputs: frames go out only while the instrument's glide
      settles into a cue's new value, and then stop.
    - The ownership probe's retry loop. Its `clock` and `sleep` are the
      fake clock and a fake-advancing sleep, so even a routing failure
      would spend no wall-clock time.
    """

    def __init__(self, *, name: str, summary: str,
                 profiles: tuple[str, ...] = ("rev1",),
                 handshake: dict | None = None, with_room: bool = False,
                 dev: str = DEV, bit: str = "ContractBit") -> None:
        """`handshake` is the scripted device's accept policy, exported as
        the scenario's `device.handshake`: None means it never accepts on
        its own; {"node": str, "ack_after_ms": int} means that on every
        link-up it answers the FIRST /$DEV/handshake it receives with
        /game/handshake [dev, round_id, node], ack_after_ms later."""
        if handshake is not None and set(handshake) != {"node",
                                                          "ack_after_ms"}:
            raise ValueError(
                f"handshake must be None or {{node, ack_after_ms}}, got "
                f"{handshake!r}")
        self.name = name
        self.summary = summary
        self.profiles = list(profiles)
        self.handshake = dict(handshake) if handshake is not None else None
        self.dev = dev
        # The presentation lead every recorded `at` was computed with.
        # Public so Task 8's export can publish it alongside the scenarios.
        self.cue_horizon = CUE_HORIZON_S
        # Hand-authored steps (inputs and expectations). Control's own
        # captured sends live in self._sent and are merged in finish().
        self.steps: list[dict] = []
        self._sent: list[tuple[int, str, float, str, list]] = []
        # Every device message this rig has actually scripted, as
        # (t_ms, "/game/<verb>", detail). The expect_* helpers check
        # themselves against this rather than trusting the caller's t.
        self._scripted: list[tuple[int, str, object]] = []
        self._now_ms = 0
        self._linked_up = False
        self._next_hello_ms: int | None = None
        # The latest round id a /$DEV/handshake carried while the link was
        # up (what a real device would hold), every round id this rig has
        # seen (for the $ROUND rewrite), and the device.handshake policy's
        # pending auto-accept.
        self._round_seen: str | None = None
        self._round_ids: set[str] = set()
        self._awaiting_invite = False
        self._accept_due_ms: int | None = None

        self._fake = FakeO2Lite(now=0.0)
        # O2LiteTransport.start refuses a connection that has not seen
        # pyarco announce its own service first, and REPLACES the string
        # with "actl,game".
        self._fake.set_services("actl")
        self._transport = O2LiteTransport()
        # The ownership probe's own wait loop defaults to time.monotonic
        # and time.sleep. It succeeds on its first iteration against this
        # fake, but the module's "no wall clock, no sleeps" promise has to
        # hold on the failure path too, so both are supplied.
        self._transport.start(self._fake, clock=self._fake.time_get,
                              sleep=self._fake_sleep)
        # The probe's sleep may have moved the fake clock; the scenario
        # timeline starts at exactly 0.0 either way.
        self._fake.set_time(0.0)
        # Wrapped AFTER start() returns, so the two _svcheck probes
        # verify_service_ownership sends during start() are never captured.
        # The wrapper filters anything not addressed to our own dev anyway,
        # which covers a later probe (a reconnect re-checks ownership) and
        # every Room fixture's own frames.
        # Both channels: FakeO2Lite.send_cmd (tcp) no longer calls send, so
        # a send-only wrapper would miss every tcp-routed down message.
        self._orig_send = self._fake.send
        self._orig_send_cmd = self._fake.send_cmd
        self._fake.send = (
            lambda addr, ts, *a: self._wrapped_send(
                self._orig_send, addr, ts, *a))
        self._fake.send_cmd = (
            lambda addr, ts, *a: self._wrapped_send(
                self._orig_send_cmd, addr, ts, *a))

        catalog = load_catalog(REPO_ROOT / "instruments").published
        # A Room needs a binding registry for arm_fixture to open a
        # registration window on, driven by the same fake clock.
        self._room_binding = (RoomBindingRegistry(clock=self._fake.time_get)
                              if with_room else None)
        self._gs = GameServer(dict(BITS),
                              cue_horizon=self.cue_horizon,
                              clock=self._fake.time_get,
                              carried_instruments=catalog,
                              room_binding=self._room_binding)
        if with_room:
            profile = load_terrarium_config(
                str(REPO_ROOT / "terrarium.toml")).rooms[ROOM_NAME].profile
            self._gs.room = Room(name=ROOM_NAME, profile=profile,
                                 node_id=ROOM_NODE_ID)
            for fixture, fixture_dev in ROOM_FIXTURE_DEVS.items():
                self._gs.room.bound[fixture] = fixture_dev
        self._agent = DeviceLinkAgent(self._gs, self._transport,
                                      horizon=self.cue_horizon,
                                      clock=self._fake.time_get)
        self._gs.load_bit(bit)
        self._round_ids.add(self._gs.round_id)

    def _fake_sleep(self, seconds: float) -> None:
        """Advance the fake clock instead of the wall clock, so the
        ownership probe's retry loop terminates without a real sleep."""
        self._fake.set_time(self._fake.time_get() + seconds)

    # --- capture -----------------------------------------------------------

    def _wrapped_send(self, orig, addr: str, timestamp: float,
                      *raw_args) -> None:
        """Every outbound o2lite message, udp or tcp, passes through here;
        `orig` is the fake's own send or send_cmd, called through.

        Wrapping the FAKE's send rather than O2LiteTransport.send is what
        lets this see Blob-wrapped arguments exactly as the wire carries
        them, which is why from_o2_arg is the right decoder for them.
        """
        orig(addr, timestamp, *raw_args)
        if not addr.startswith(f"/{self.dev}/"):
            return                      # a _svcheck probe, or a Room fixture
        typespec = raw_args[0] if raw_args else ""
        values = [from_o2_arg(v) if t == "b" else v
                  for t, v in zip(typespec, raw_args[1:])]
        if addr == f"/{self.dev}/handshake":
            self._round_ids.add(values[0])
        # Round ids are labelled NOW, against the round that is current at
        # send time, not at finish(): a later reload must not turn this
        # round's id into "$ROUND_PREV" or an old one into "$ROUND".
        self._sent.append((self._now_ms, addr, timestamp, typespec,
                           [self._round_label(v) for v in values]))
        if addr == f"/{self.dev}/handshake" and self._linked_up:
            # What a real device does on an invite: hold the round id.
            self._round_seen = values[0]
            if self._awaiting_invite and self.handshake is not None:
                self._awaiting_invite = False
                self._accept_due_ms = (self._now_ms
                                       + int(self.handshake["ack_after_ms"]))

    # --- clock / link ------------------------------------------------------

    def _set_now(self, now_ms: int) -> None:
        """One clock for the whole rig: the fake o2lite's time_get is what
        both GameServer and DeviceLinkAgent were built with."""
        self._now_ms = now_ms
        self._fake.set_time(now_ms / 1000.0)

    def advance_to(self, target_ms: int) -> None:
        """Tick the rig forward to `target_ms`, one render tick at a time,
        sending the device's hello heartbeat whenever one comes due.

        A target already in the past is a scenario authored out of order,
        which would silently record steps that never happened in that
        sequence, so it raises rather than no-opping.
        """
        if target_ms < self._now_ms:
            raise ValueError(
                f"cannot advance to t={target_ms}ms: the scenario is "
                f"already at t={self._now_ms}ms (steps run forward on one "
                f"timeline)")
        while self._now_ms < target_ms:
            step_ms = min(TICK_MS, target_ms - self._now_ms)
            for due in (self._next_hello_ms, self._accept_due_ms):
                if (self._linked_up and due is not None
                        and due < self._now_ms + step_ms):
                    # Land exactly on a device send rather than stepping
                    # past it, so a hello is stamped at 5000 and not 5014.
                    step_ms = min(step_ms, due - self._now_ms)
            self._set_now(self._now_ms + step_ms)
            self._agent.poll()
            self._run_due_device_sends()

    def _run_due_device_sends(self) -> None:
        """The scripted device's own timed sends that are due now: the
        hello heartbeat, then the device.handshake policy's auto-accept."""
        if not self._linked_up:
            return
        if (self._next_hello_ms is not None
                and self._now_ms >= self._next_hello_ms):
            self._send_hello(self._now_ms)
            self._next_hello_ms += HELLO_INTERVAL_MS
        if (self._accept_due_ms is not None
                and self._now_ms >= self._accept_due_ms):
            self._accept_due_ms = None
            self._send_handshake(self._now_ms, self._round_seen,
                                 self.handshake["node"])

    def link_up(self, t_ms: int = 0) -> None:
        """The device's link comes up at `t_ms`: it hellos immediately, and
        every HELLO_INTERVAL_MS after that until link_down. A recorder
        built with a `handshake` policy then accepts the first
        /$DEV/handshake this link-up receives, ack_after_ms after it."""
        self.advance_to(t_ms)
        self.steps.append({"t": t_ms, "link": "up"})
        self._linked_up = True
        self._round_seen = None
        self._awaiting_invite = self.handshake is not None
        self._accept_due_ms = None
        self._next_hello_ms = t_ms + HELLO_INTERVAL_MS
        self._send_hello(t_ms)
        # An invite answered with ack_after_ms 0 is due right now.
        self._run_due_device_sends()

    def link_down(self, t_ms: int) -> None:
        """The device's link drops at `t_ms`: the heartbeat stops, and the
        device forgets its round id and any pending accept (rule 7)."""
        self.advance_to(t_ms)
        self.steps.append({"t": t_ms, "link": "down"})
        self._linked_up = False
        self._next_hello_ms = None
        self._round_seen = None
        self._awaiting_invite = False
        self._accept_due_ms = None

    def _send_hello(self, t_ms: int) -> None:
        self._scripted.append((t_ms, "/game/hello", INSTRUMENT))
        self._fake.deliver("/game/hello", "ssss",
                           (self.dev, "contract-kit", "1", INSTRUMENT),
                           timestamp=t_ms / 1000.0)
        self._agent.poll()

    def _send_handshake(self, t_ms: int, round_id: str | None,
                        node: str) -> None:
        if round_id is None:
            raise AssertionError(
                f"the device has no round id to accept with at t={t_ms}ms: "
                f"no /$DEV/handshake has reached it since its link came up")
        self._scripted.append((t_ms, "/game/handshake",
                               (self._round_label(round_id), node)))
        self._fake.deliver("/game/handshake", "sss",
                           (self.dev, round_id, node),
                           timestamp=t_ms / 1000.0)
        self._agent.poll()

    def legacy_join(self, t_ms: int, node: str) -> None:
        """A contract v2 device's /game/join, which a v3 Control answers
        with /$DEV/error and otherwise ignores. Recorded as NO step at all:
        a v3 device never sends it (firmware checklist item 8), so there is
        neither an input a runner could deliver nor an expect_out a
        compliant device could pass. Only Control's captured answer lands
        in the scenario, as an ordinary control_sends step."""
        self.advance_to(t_ms)
        self._fake.deliver("/game/join", "ss", (self.dev, node),
                           timestamp=t_ms / 1000.0)
        self._agent.poll()

    def _scripted_at(self, t_ms: int, address: str) -> list:
        return [detail for (t, addr, detail) in self._scripted
                if t == t_ms and addr == address]

    def _no_send_scripted(self, t_ms: int, address: str) -> str:
        """The AssertionError text for an expectation the rig never
        actually scripted, naming what it did script instead."""
        scripted = sorted({(t, addr) for (t, addr, _d) in self._scripted})
        return (f"no {address} scripted at t={t_ms}ms; this rig scripted "
                f"{scripted}")

    def accept(self, t_ms: int, node: str = "",
               round_id: str | None = None) -> None:
        """The person accepts at `t_ms`: the device sends /game/handshake
        [dev, round_id, node], where round_id is the latest one a
        /$DEV/handshake carried to it (or the literal `round_id` given,
        for a scenario that pins a stale echo).

        Records an `accept` INPUT step (step_schema.kinds.accept) before
        the device's own expect_out, the same way a gesture does: the
        accept is a decision the person makes, so a replaying runner has
        to be told when to make it and must never infer it from the
        expect_out. An accept the device.handshake policy makes on its own
        is not an input step; see expect_handshake_out for that one.

        Records the expectation itself, so a caller does NOT also call
        expect_handshake_out for the same accept.
        """
        self.advance_to(t_ms)
        detail: dict = {"node": node}
        if round_id is not None:
            # Labelled like every other round id, so a real minted id
            # never reaches the output ("stale" stays literal).
            detail["round_id"] = self._round_label(round_id)
        self.steps.append({"t": t_ms, "accept": detail})
        self._send_handshake(t_ms, round_id if round_id is not None
                             else self._round_seen, node)
        self.expect_handshake_out(t_ms)

    def rival_accept(self, t_ms: int) -> None:
        """A second device (RIVAL_DEV) hellos and accepts the current round
        at `t_ms`, taking a scored slot. It is never captured and records
        no step: to the scripted device it is simply someone else in the
        room, visible only through Control's answers (a full lobby)."""
        self.advance_to(t_ms)
        self._fake.deliver("/game/hello", "ssss",
                           (RIVAL_DEV, "contract-kit-rival", "1", INSTRUMENT),
                           timestamp=t_ms / 1000.0)
        self._agent.poll()
        self._fake.deliver("/game/handshake", "sss",
                           (RIVAL_DEV, self._gs.round_id, ""),
                           timestamp=t_ms / 1000.0)
        self._agent.poll()

    def arm_fixture(self, t_ms: int, fixture: str = "main") -> None:
        """The operator arms `fixture` of the loaded Room at `t_ms`, so the
        next Room-node handshake binds it (spec 2026-10-01 section 3.5).
        Operator input, not device input: records no step."""
        if self._room_binding is None:
            raise AssertionError("arm_fixture needs a Recorder(with_room=True)")
        self.advance_to(t_ms)
        self._room_binding.arm(ROOM_NAME, fixture, ARM_WINDOW_S)
        self._agent.poll()

    def start(self, t_ms: int) -> None:
        """The operator starts the round at `t_ms`, through the single
        start authority as the Terrarium's own admin identity (spec
        2026-10-01 section 5.4). Records no step: it is not a device
        input, and everything a device sees of it (every /$DEV/role) is
        captured as control_sends."""
        self.advance_to(t_ms)
        reason = self._gs.request_start(None, TERRARIUM_ADMIN, "recorder")
        if reason is not None:
            raise AssertionError(f"start refused at t={t_ms}ms: {reason}")
        self._agent.poll()

    # --- gestures ----------------------------------------------------------

    def _gesture(self, onset_t: int, kind: str, typespec: str, wire_args: tuple,
                 step_args: list, detail: dict) -> None:
        """One classified gesture: the input step, the scripted send, and
        the device's own expect_out, all stamped at the gesture's onset
        (spec section 4.3, rule 5)."""
        self.advance_to(onset_t)
        self.steps.append({"t": onset_t, "gesture": {
            "kind": kind, "onset_t": onset_t, **detail}})
        self._scripted.append((onset_t, f"/game/{kind}", detail))
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
        are its business, not the contract's.

        Raises unless this rig really did script a hello at `t_ms`, so a
        recorded expectation can never be a time the caller guessed.
        """
        if not self._scripted_at(t_ms, "/game/hello"):
            raise AssertionError(self._no_send_scripted(t_ms, "/game/hello"))
        self.steps.append({"t": t_ms, "expect_out": {
            "address": "/game/hello", "typespec": "ssss",
            "args": ["$DEV", "*", "*", "*"], "stamp_t": None,
            "within_ms": DEFAULT_WITHIN_MS}})

    def expect_handshake_out(self, t_ms: int) -> None:
        """The device must send /game/handshake at `t_ms`. Its round id is
        recorded as "$ROUND" when it was the CURRENT round's id at send
        time, as "$ROUND_PREV" when it was an earlier load's, and literally
        when Control never minted it (e.g. "stale").

        Raises unless this rig really did script a handshake at `t_ms`.
        """
        sent = self._scripted_at(t_ms, "/game/handshake")
        if not sent:
            raise AssertionError(
                self._no_send_scripted(t_ms, "/game/handshake"))
        echoed, node = sent[-1]
        self.steps.append({"t": t_ms, "expect_out": {
            "address": "/game/handshake", "typespec": "sss",
            "args": ["$DEV", echoed, node], "stamp_t": None,
            "within_ms": DEFAULT_WITHIN_MS}})

    def expect_quiet(self, t_ms: int, addresses: list[str], for_ms: int) -> None:
        """None of `addresses` may be sent from `t_ms` for `for_ms`.

        Raises if the rig itself scripted one of them inside that window:
        an expect_quiet a scenario's own gestures contradict would be
        asking a device to do the impossible.
        """
        clashes = [(t, addr) for (t, addr, _d) in self._scripted
                   if addr in addresses and t_ms <= t < t_ms + for_ms]
        if clashes:
            raise AssertionError(
                f"expect_quiet({t_ms}ms, for {for_ms}ms) contradicts this "
                f"rig's own scripted sends {sorted(clashes)}")
        self.steps.append({"t": t_ms, "expect_quiet": {
            "addresses": list(addresses), "for_ms": for_ms}})

    def _presentation_ms(self, addr: str, timestamp: float) -> int | None:
        """The message's presentation time in scenario milliseconds, or
        None for a verb that carries none."""
        if addr.rsplit("/", 1)[-1] not in PRESENTATION_TIME_VERBS:
            return None
        return round(timestamp * 1000)

    def _frame_showing_at(self, t_ms: int) -> list:
        """The pixels showing at `t_ms`: the frame with the NEWEST
        presentation time at or before `t_ms` (spec section 4.3, rule 3).

        Read off this recorder's own finished steps rather than off the
        capture log, so the answer is exactly what a device replaying the
        committed file would compute, and so hand-authored frames
        (`control_send_now`) count too. Control's own stream never puts two
        frames on one presentation time, so a scenario that wants to pin
        "when several are due, only the newest shows" has to author the
        pair; see contract_kit/scenarios.py's timed_frames_hold_last.

        Selection is on `at`, not on send order: send time and presentation
        time differ by the cue horizon, and an authored pair may deliberately
        arrive newest-first. Frames flagged `malformed` are skipped, because
        a frame the device must drop can never be the one showing. Raises if
        no frame is showing yet, rather than returning an answer of nothing.

        Shared by expect_frame (t_ms is the check's own time) and
        expect_frame_held (t_ms is a time BEFORE a link-down window, not
        the check's own time inside it -- see that method).
        """
        showing = None
        newest_at: int | None = None
        for step in self.finish()["steps"]:
            msg = step.get("control_sends")
            if msg is None or msg["address"] != "/$DEV/leds":
                continue
            if msg.get("malformed"):
                continue
            # `or 0`: every REAL /leds send always carries an int `at`
            # (PRESENTATION_TIME_VERBS), so this only ever fires for a
            # hand-authored control_send_now("/$DEV/leds", ..., at=None),
            # which control_send_now itself allows.
            at = msg["at"] or 0
            if at > t_ms:
                continue
            # `>=` and not `>`: two frames on the same presentation time are
            # resolved by arrival, last one wins.
            if newest_at is None or at >= newest_at:
                newest_at, showing = at, msg["args"][0]
        if showing is None:
            raise AssertionError(
                f"no /leds frame is showing on {self.dev} at t={t_ms}ms")
        return showing

    def expect_frame(self, t_ms: int) -> None:
        """The pixels showing at `t_ms` (see _frame_showing_at). Raises if
        no frame is showing yet, rather than recording an expectation of
        nothing.
        """
        self.steps.append(
            {"t": t_ms, "expect_frame": {"grb": self._frame_showing_at(t_ms)}})

    def expect_frame_held(self, t_ms: int, since_t_ms: int) -> None:
        """A HAND-AUTHORED expect_frame for a moment inside a link-down
        window: the device is asserted to still be showing whatever had
        reached it BEFORE the outage began, at `since_t_ms`, not whatever
        expect_frame's own "newest control_sends with at <= t_ms" search
        would find by digging all the way up to `t_ms` itself.

        That distinction matters only here: while the link is down,
        Control goes on ticking and can go on sending `/$DEV/leds` into a
        link the device cannot hear (this export's
        step_schema.link_down_delivery rule), and if `t_ms` fell late
        enough for one of THOSE sends to have an `at` at or before it,
        plain expect_frame would report Control's own unheard traffic
        instead of the frame the device is actually still showing. This
        recorder can only ever observe what Control sent, never a device's
        own screen, so the true answer -- "still whatever it had at
        since_t_ms" -- has to be told to it directly rather than
        re-derived from `t_ms`. See contract_kit/scenarios.py's
        link_loss_keeps_display and this export's replay_notes.

        Raises under the same condition expect_frame does if nothing had
        reached the device by `since_t_ms`.
        """
        self.steps.append({"t": t_ms, "expect_frame": {
            "grb": self._frame_showing_at(since_t_ms)}})

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
            "name": values[0], "params": self._normalize(values[1]),
            "within_ms": within_ms}})

    # --- lifecycle / hand-authored input -----------------------------------

    def control_round_id(self) -> str | None:
        """The live round id, for a test that needs the real string."""
        return self._gs.round_id

    def load_bit(self, t_ms: int, bit: str = "ContractBit") -> None:
        """Load `bit` at `t_ms` (after unload_bit), minting a NEW round id;
        the previous round's id is from then on recorded as "$ROUND_PREV".
        Operator input: records no step."""
        self.advance_to(t_ms)
        self._gs.load_bit(bit)
        self._round_ids.add(self._gs.round_id)
        self._agent.poll()

    def unload_bit(self) -> None:
        """Unload the Bit, which releases every joined device."""
        self._gs.abort()
        self._agent.poll()

    def control_send_now(self, address: str, typespec: str, args: list,
                         at: int | None = None,
                         malformed: bool = False) -> None:
        """A hand-authored control_sends step (the malformed-input
        scenario) -- appended directly rather than captured, since it is
        never actually sent through this rig's own O2 connection.

        `malformed=True` adds `"malformed": true` to the step. The contract
        kit validates every recorded message against devicelink/contract.py,
        and a deliberately broken input has to be told apart from a
        recording that has drifted: a flagged step is required NOT to
        validate, an unflagged one is required to validate. Steps that are
        not flagged carry no such key at all, so ordinary recordings keep
        the step shape the spec publishes.
        """
        step = {"address": address, "typespec": typespec, "args": list(args),
                "at": at}
        if malformed:
            step["malformed"] = True
        self.steps.append({"t": self._now_ms, "control_sends": step})

    # --- output ------------------------------------------------------------

    def _round_label(self, value: object) -> object:
        """A round id as recorded: "$ROUND" for the round current right
        now, "$ROUND_PREV" for one from an earlier Bit load, anything else
        unchanged."""
        if not isinstance(value, str):
            return value
        if value == self._gs.round_id:
            return ROUND_PLACEHOLDER
        if value in self._round_ids:
            return PREV_ROUND_PLACEHOLDER
        return value

    def _normalize(self, value: object) -> object:
        """A captured argument with its chime key placeheld ("key=$KEY").
        Round ids were already labelled at capture (_round_label)."""
        return _normalize_key(value)

    def finish(self) -> dict:
        """The EXPORT FORMAT v1 scenario dict, steps sorted by `t`."""
        control_steps = []
        for (t, addr, timestamp, typespec, values) in self._sent:
            control_steps.append({"t": t, "control_sends": {
                "address": addr.replace(f"/{self.dev}/", "/$DEV/"),
                "typespec": typespec,
                "args": [self._normalize(v) for v in values],
                "at": self._presentation_ms(addr, timestamp)}})
        # Stable: sorted() is stable, so Control's captured sends keep their
        # real order among themselves within one millisecond, and the
        # hand-authored steps keep theirs.
        all_steps = sorted(self.steps + control_steps, key=lambda s: s["t"])
        return {
            "name": self.name,
            "summary": self.summary,
            "profiles": self.profiles,
            "device": {"handshake": (dict(self.handshake)
                                     if self.handshake is not None
                                     else None)},
            "steps": all_steps,
        }
