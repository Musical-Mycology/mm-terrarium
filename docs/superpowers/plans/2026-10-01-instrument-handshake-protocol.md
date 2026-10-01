# Instrument Handshake Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/game/join` and the lobby double-tap with Hello, Handshake,
Received Handshake, Validated, then exactly one role per device at RUNNING
(scored if validated, else jam, with a Solo fallback), delivered over TCP;
fold in the review's cleanups; ship contract v3, docs, diagrams, and the
paired mm-tuneshroom change.

**Architecture:** `RegistrationState` gains a `validated` reservation map and
`materialize()`; `GameServer.handshake()` replaces `join()`; `run()`
materializes and pushes each grant through a new `on_grant` sink that
`DeviceLinkAgent` turns into a bridge plus a TCP `/role`. The verb table
grows a per-row down transport that `O2LiteTransport.send` routes on.

**Tech Stack:** Python 3 stdlib (`control/`, `devicelink/`, `harness/`),
pytest, o2litepy (live only), Dart/Flutter (mm-tuneshroom), d2 and the
in-repo `tools/seqrender` (diagrams).

**Spec:** `docs/superpowers/specs/2026-10-01-instrument-handshake-protocol-design.md`
(read it before any task; section numbers below refer to it).

## Global Constraints

- Always run Python as `.venv/bin/python` (a fresh worktree needs
  `ln -s "$HOME/projects/mm-terrarium/.venv" .venv` first). Never `python3`.
- JS tests: `node --test tests/js/*.test.js`.
- All outbound JSON through `control/wire_json.dumps()`.
- Boundary rules 1-5 in `docs/MM_TERRARIUM.md` hold; rule 5: `FakeO2Lite`
  must never be more permissive than o2litepy.
- No luxaeterna, pyarco or o2litepy import at module level under `control/`.
- `CONTRACT_VERSION` becomes `3`. `HELLO_INTERVAL_S` stays `5.0`; reap
  timeout stays `15.0`.
- Down transport: `tcp` for `role`, `deny`, `release`, `room`, `error`,
  `handshake`, `validated`; `udp-ok` for `leds`, `play`.
- New wire: `/<dev>/handshake s round_id`; `/game/handshake sss dev
  round_id node`; `/<dev>/validated ss round_id role`. `/game/join` answered
  with `/<dev>/error ["join", "retired in contract v3: use /game/handshake"]`.
- Deny reasons, verbatim: `not connected`, `registration closed`,
  `scored full`, `no such node`, plus the `satisfies()` reason.
- No em dashes in any doc or comment written for this work.
- Commit after every task; never commit to `main`; branch
  `claude/instrument-handshake-protocol-833d7c` (this worktree).
- mm-tuneshroom work happens on its own branch in its own worktree
  (Task 13), never on its `main`.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `devicelink/contract.py` | modify | verb rows v3, down transport per row |
| `devicelink/protocol.py` | modify | `handshake_event`, `validated_event`, `parse_handshake_args` |
| `devicelink/o2_transport.py` | modify | route by row transport; `FakeO2Lite.channels`; drop `drain_new_clients` |
| `control/registration.py` | modify | `validated`, `validate`, `materialize`, idempotent `assign`, cap |
| `control/jam_role.py` | create | Bit jam role or synthesized `solo:<instrument>` role |
| `control/start_condition.py` | modify | the single start decider |
| `control/lobby.py` | modify | drop `decide_start`, `DoubleTapDetector`, free `lobby_state` |
| `control/engine.py` | modify | `round_id`, `handshake`, `run` materialize, `on_grant`, walk-ups, solo dispatch |
| `control/bit_config.py` | modify | `[lobby] max_scored` |
| `control/role_config.py` | modify | drop the flat `config["instrument"]` write |
| `devicelink/agent.py` | modify | handshake invites, `_on_handshake`, grant sink, first-contact room, reap cleanup |
| `devicelink/lobby_runtime.py` | modify | handshake send sink, ceremony survives stop, no tap path |
| `harness/o2_shroom.py`, `harness/shroom_client.py` | modify | handshake ack, no join path |
| `harness/markers.py`, `harness/run_stack.py`, `harness/terrarium_boot.py` | modify | new markers, timer start via `request_start` |
| `bits/test/` | modify | `player` UNIQUE capacity 1 |
| `bits/solotest/` | create | SoloTestBit (no jam role) |
| `contract_kit/*`, `tools/export_contract.py`, `contract_kit/recordings/` | modify | v3 scenarios |
| `tests/helpers_admit.py` | create | `admit()` / `admit_running()` test helpers |
| `docs/diagrams/*`, `docs/MM_TERRARIUM.md`, `docs/device-contract-guide.md`, `docs/control-gameserver-design.md` | modify | docs |
| mm-tuneshroom `lib/host/device_session.dart`, `lib/link/envelope.dart`, `lib/sim/*`, `test/contract/` | modify | paired client change |

---

### Task 1: Spike: does Arco relay a TCP send to an o2lite client over TCP?

Throwaway. Output is a recorded answer, not kept code. Gates the transport
choice (spec 3.3, 11).

**Files:**
- Create (scratch only, never committed): `$SCRATCH/tcp_relay_probe.py`
  where `$SCRATCH` is the session scratchpad directory.
- Modify: `docs/superpowers/specs/2026-10-01-instrument-handshake-protocol-design.md`
  (one Status-line sentence recording the result).

- [ ] **Step 1: Read how o2litepy sends.** In the sibling arco checkout
  (`harness/arco_paths.py` resolves it; do not guess the path), read
  `o2litepy/src/o2litepy/o2lite.py`: `send`, `send_cmd`, and what flag
  `send_cmd` sets in the message. Read `o2/src/bridge.cpp` /
  `o2/src/o2lite*.c` in the sibling `o2` checkout for how the hub forwards
  a message to an o2lite client: does it honour the TCP flag (forward on
  the client's TCP socket) or always use UDP? Write down file:line.

- [ ] **Step 2: Live probe.** Boot `./terrarium.sh --room TEST --detach`
  (see `docs/MM_TERRARIUM.md` *Running it*; use `set -m` and keep the PID).
  Write `$SCRATCH/tcp_relay_probe.py` that, with `harness.arco_paths.ensure_o2litepy()`
  on `sys.path`, opens two o2lite connections in two processes: `probeb`
  registers service `probeb` and a handler on `/probeb/x` that prints
  `GOT <n>`; `probea` waits for clock sync, then `send_cmd("/probeb/x", 0, "i", n)`
  for n in 1..200 at 100 Hz while you run `sudo -n true 2>/dev/null; echo`
  (no packet loss is expected on loopback, so the signal is the code path
  in step 1, not loss counts). Also run with `send` for comparison.

- [ ] **Step 3: Record and stop.** Kill the stack (`kill -TERM $PID`), run
  `./terrarium.sh --clean`. Add one sentence to the spec Status line:
  `Task 1 (2026-10-01): hub forwards tcp-flagged messages to o2lite clients over <TCP|UDP> (<file:line>).`
  If the answer is UDP, STOP and report BLOCKED: the plan's TCP tasks must
  switch to the spec's recorded fallback (section 11) before continuing.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-10-01-instrument-handshake-protocol-design.md
git commit -m "docs(spec): record the TCP relay probe result"
```

---

### Task 2: Contract table v3, protocol builders, transport routing

**Files:**
- Modify: `devicelink/contract.py`
- Modify: `devicelink/protocol.py`
- Modify: `devicelink/o2_transport.py` (`O2LiteTransport.send`, `FakeO2Lite`, `drain_new_clients`)
- Test: `tests/test_devicelink_contract.py`, `tests/test_devicelink_protocol.py`, `tests/test_link.py`, `tests/test_fakes.py`

**Interfaces:**
- Produces: `protocol.handshake_event(dev: str, round_id: str) -> dict`;
  `protocol.validated_event(dev: str, round_id: str, role: str) -> dict`;
  `protocol.parse_handshake_args(args: list) -> tuple[str, str, str]`
  (dev, round_id, node; raises `ValueError`);
  `contract.down_transport(verb: str) -> str` (`"tcp"` or `"udp-ok"`);
  `FakeO2Lite.channels: list[str]` parallel to `.sent` (`"tcp"`/`"udp"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_devicelink_contract.py (append)
from devicelink import contract

def test_v3_up_verbs():
    assert "handshake" in contract.GAME_VERBS
    assert "join" not in contract.GAME_VERBS

def test_handshake_up_row():
    row = contract.row_for("up", "handshake")
    assert row.typespecs == ("sss",)
    assert row.args == ("dev", "round_id", "node")
    assert row.transport == "tcp" and row.pre_role

def test_new_down_rows():
    assert contract.row_for("down", "handshake").typespecs == ("s",)
    assert contract.row_for("down", "validated").typespecs == ("ss",)

def test_down_transport_split():
    tcp = {"role", "deny", "release", "room", "error", "handshake", "validated"}
    for row in contract.VERB_TABLE:
        if row.direction != "down":
            continue
        want = "tcp" if row.verb in tcp else "udp-ok"
        assert contract.down_transport(row.verb) == want, row.verb
```

```python
# tests/test_devicelink_protocol.py (append)
import pytest
from devicelink import protocol

def test_handshake_event_shape():
    msg = protocol.handshake_event("ie1", "TestBit-1-abc123")
    assert msg["address"] == "/ie1/handshake"
    assert msg["typespec"] == "s" and msg["args"] == ["TestBit-1-abc123"]

def test_validated_event_shape():
    msg = protocol.validated_event("ie1", "r1", "player")
    assert msg["address"] == "/ie1/validated"
    assert msg["typespec"] == "ss" and msg["args"] == ["r1", "player"]

def test_parse_handshake_args():
    assert protocol.parse_handshake_args(["ie1", "r1", ""]) == ("ie1", "r1", "")
    with pytest.raises(ValueError):
        protocol.parse_handshake_args(["ie1", "r1"])
    with pytest.raises(ValueError):
        protocol.parse_handshake_args(["ie1", 3, ""])
```

```python
# tests/test_link.py (append) -- routing by row transport
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport
from devicelink import protocol

def _transport():
    fake = FakeO2Lite()
    t = O2LiteTransport()
    t.start(fake)            # use the same start() call the file's other tests use
    t.bind_dev("ie1", None)
    return fake, t

def test_role_goes_tcp_leds_goes_udp():
    fake, t = _transport()
    t.send("ie1", protocol.role_event("ie1", {"role": "p"}))
    t.send("ie1", protocol.leds_event("ie1", [0] * 36))
    chans = dict(zip([s[0] for s in fake.sent], fake.channels))
    assert chans["/ie1/role"] == "tcp"
    assert chans["/ie1/leds"] == "udp"
```

Match `_transport()` to the existing start/clock fixture pattern in
`tests/test_link.py` (read the top of that file first; it already builds a
started `O2LiteTransport` over `FakeO2Lite`).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_devicelink_contract.py tests/test_devicelink_protocol.py tests/test_link.py -q`
Expected: FAIL (`handshake` missing, `channels` missing, no `down_transport`).

- [ ] **Step 3: Implement**

In `devicelink/contract.py`: replace the module docstring paragraph about
"Down-row transport is udp-ok for every row" with: "Down-row transport is
per row (spec 2026-10-01-instrument-handshake-protocol section 3.3):
control verbs go tcp through o2litepy's send_cmd, leds and play stay
udp-ok." Remove the `join` row. Add after `start`:

```python
    VerbRow("handshake", "up", ("sss",), ("dev", "round_id", "node"),
            "tcp", True,
            "Received Handshake: sent when the user accepts. round_id "
            "echoes the latest /<dev>/handshake; node '' asks for the "
            "Bit's default scored role, else a Registration Node id. A "
            "Room node binds an armed fixture and ignores round_id."),
```

Change the `tap` row note to: `"peak_g is 0 for a touch tap (Rev 1); count
is the device's own pairing (Rev 1 sends 1). Stamped at onset. Gameplay
only: Control no longer reads taps as a lobby handshake."` and set its
`pre_role` to `False`.

Set the down rows' `transport` to `"tcp"` for `role`, `deny`, `release`,
`error`, `room`; update notes: role `"Sent once per round at RUNNING (or at
a RUNNING walk-up's first hello)."`, deny `"Reply to a refused
/game/handshake; reason and hint are both set."`, room `"Sent on first
contact and on every state or registration change; hardware may ignore
it."`. Add:

```python
    VerbRow("handshake", "down", ("s",), ("round_id",), "tcp", True,
            "Invite for this round: sent on first hello in SETUP and every "
            "invite cycle until validated, FULL, or SETUP ends."),
    VerbRow("validated", "down", ("ss",), ("round_id", "role"), "tcp", True,
            "The handshake was accepted and a scored slot is reserved; the "
            "role itself arrives at RUNNING."),
```

and at module end:

```python
def down_transport(verb: str) -> str:
    return row_for("down", verb).transport
```

In `devicelink/protocol.py` after `deny_event`:

```python
def handshake_event(dev: str, round_id: str) -> dict:
    return _event(f"/{dev}/handshake", "s", [round_id])


def validated_event(dev: str, round_id: str, role: str) -> dict:
    return _event(f"/{dev}/validated", "ss", [round_id, role])


def parse_handshake_args(args: list) -> tuple[str, str, str]:
    """(dev, round_id, node) from a /game/handshake; ValueError when the
    shape is wrong (contract rule 6: malformed is dropped)."""
    if len(args) != 3 or not all(isinstance(a, str) for a in args):
        raise ValueError(f"handshake wants 3 strings, got {args!r}")
    return args[0], args[1], args[2]
```

In `devicelink/o2_transport.py`:
- `FakeO2Lite.__init__`: add `self.channels: list[str] = []` with a comment
  that it parallels `sent` and records which o2litepy call carried each
  message (boundary rule 5: a test must be able to fail on a UDP `/role`).
- `FakeO2Lite.send`: append `"udp"` to `self.channels` (keep the existing
  body). `send_cmd`: do the same work but append `"tcp"`; implement it by
  factoring the body into `_record(addr, timestamp, args, channel)` used
  by both.
- `O2LiteTransport.send`: replace the final `self._o2.send(...)` with:

```python
        verb = msg["address"].rsplit("/", 1)[-1]
        try:
            tcp = down_transport(verb) == "tcp"
        except KeyError:
            tcp = False
        sender = self._o2.send_cmd if tcp else self._o2.send
        try:
            sender(msg["address"], msg.get("timestamp", 0.0), typespec, *args)
        except Exception:
            logger.exception("o2lite send to %s failed", dev)
```

  importing `down_transport` from `devicelink.contract`.
- Delete `drain_new_clients` and its stale comment; delete its call site in
  `devicelink/agent.py` `poll()` and any test that asserts it.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_devicelink_contract.py tests/test_devicelink_protocol.py tests/test_link.py tests/test_fakes.py -q`
Expected: PASS. Then run the full suite once and note (do not fix) failures
that come from `join` disappearing from `GAME_VERBS`; those are fixed in
Tasks 6-8. Record the failing test list in the task report.

- [ ] **Step 5: Commit**

```bash
git add devicelink/ tests/test_devicelink_contract.py tests/test_devicelink_protocol.py tests/test_link.py tests/test_fakes.py
git commit -m "feat(devicelink): contract v3 verb rows and per-row down transport"
```

---

### Task 3: RegistrationState: validate, materialize, idempotent assign, cap

**Files:**
- Modify: `control/registration.py`
- Test: `tests/test_registration.py` (create if absent; else append)

**Interfaces:**
- Produces:
  - `RegistrationState(role_table, max_scored: int | None = None)`
  - `.validated: dict[str, tuple[str, str]]` (dev -> (node, role_name), insertion order)
  - `.validate(dev: str, node: str) -> JoinResult`
  - `.scored_cap() -> int | None`
  - `.assign(dev: str, node: str, role: Role) -> bool` (False when already held: no-op)
  - `.materialize(jam_devs: list[str], jam_for: Callable[[str], Role]) -> list[tuple[str, Role]]` (new assignments in order: validated first, then jam)
  - `.release(dev) -> bool` covers validated and assigned
  - `join()` removed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_registration.py
from control.registration import RegistrationState
from control.roles import Role, RoleClass, RoleTable


def _table(cap=2):
    player = Role("player", RoleClass.UNIQUE, cap, True)
    jam = Role("jammer", RoleClass.JAM, None, False)
    return RoleTable(roles={"player": player, "jammer": jam},
                     node_map={"P": ["player"], "J": ["jammer"]})


def test_validate_reserves_until_capacity():
    reg = RegistrationState(_table(cap=1))
    assert reg.validate("a", "P").granted
    r = reg.validate("b", "P")
    assert not r.granted and r.reason == "scored full"
    assert r.hint
    assert list(reg.validated) == ["a"]
    assert ("player", 1, 1) in reg.counts()


def test_validate_is_idempotent():
    reg = RegistrationState(_table())
    reg.validate("a", "P")
    again = reg.validate("a", "P")
    assert again.granted and again.role == "player"
    assert ("player", 1, 2) in reg.counts()


def test_validate_skips_unscored_and_unknown_nodes():
    reg = RegistrationState(_table())
    assert reg.validate("a", "J").reason == "no such node"
    assert reg.validate("a", "NOPE").reason == "no such node"


def test_max_scored_lowers_cap():
    reg = RegistrationState(_table(cap=5), max_scored=1)
    assert reg.scored_cap() == 1
    reg.validate("a", "P")
    assert reg.validate("b", "P").reason == "scored full"


def test_unbounded_scored_cap_is_none_without_max():
    shared = Role("player", RoleClass.SHARED, None, True)
    reg = RegistrationState(RoleTable(roles={"player": shared},
                                      node_map={"P": ["player"]}))
    assert reg.scored_cap() is None


def test_materialize_orders_scored_then_jam():
    t = _table()
    reg = RegistrationState(t)
    reg.validate("b", "P")
    reg.validate("a", "P")
    out = reg.materialize(["c", "d"], lambda dev: t.roles["jammer"])
    assert [(d, r.name) for d, r in out] == [
        ("b", "player"), ("a", "player"), ("c", "jammer"), ("d", "jammer")]
    assert reg.validated == {}
    assert reg.assignments["c"][1] == "jammer"
    assert ("player", 2, 2) in reg.counts()
    assert ("jammer", 2, None) in reg.counts()


def test_materialize_adds_synthesized_role_to_table():
    t = _table()
    reg = RegistrationState(t)
    solo = Role("solo:tuneshroom", RoleClass.JAM, None, False)
    reg.materialize(["c"], lambda dev: solo)
    assert "solo:tuneshroom" in reg.role_table.roles
    assert reg.assignments["c"] == ("", "solo:tuneshroom", RoleClass.JAM)


def test_assign_is_idempotent():
    t = _table()
    reg = RegistrationState(t)
    assert reg.assign("c", "J", t.roles["jammer"]) is True
    assert reg.assign("c", "J", t.roles["jammer"]) is False
    assert ("jammer", 1, None) in reg.counts()


def test_release_frees_validated_slot():
    reg = RegistrationState(_table(cap=1))
    reg.validate("a", "P")
    assert reg.release("a") is True
    assert reg.validate("b", "P").granted
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_registration.py -q`
Expected: FAIL (`validate` not defined).

- [ ] **Step 3: Implement** (replace `join` and `_assign`; keep `JoinResult`,
  `release_all`, `counts`, `granted`; update the `/ie<N>/role` comment on
  `config` to `/<dev>/role`)

```python
_SCORED_FULL_HINT = ("the Bit's scored slots are taken; you will get a jam "
                     "role at start")


class RegistrationState:
    """Created when a Bit loads, discarded when it unloads. In SETUP a
    handshake only RESERVES a scored slot (`validated`); run() turns the
    reservations, plus a jam role for everyone else, into `assignments`
    (spec 2026-10-01-instrument-handshake-protocol section 3.6)."""

    def __init__(self, role_table: RoleTable, max_scored: int | None = None):
        self.role_table = role_table
        self.max_scored = max_scored
        self.assignments: dict[str, tuple[str, str, RoleClass]] = {}
        self.validated: dict[str, tuple[str, str]] = {}
        self._counts: dict[str, int] = {name: 0 for name in role_table.roles}

    def scored_cap(self) -> int | None:
        total = 0
        for role in self.role_table.roles.values():
            if not role.scored or role.role_class is RoleClass.ROOM:
                continue
            if role.capacity is None:
                return self.max_scored
            total += role.capacity
        if self.max_scored is not None:
            return min(total, self.max_scored)
        return total

    def _deny(self, reason: str, hint: str) -> JoinResult:
        return JoinResult(granted=False, reason=reason, hint=hint)

    def _granted(self, role: Role) -> JoinResult:
        return JoinResult(granted=True, role=role.name,
                          role_class=role.role_class, scored=role.scored,
                          breath=role.breath)

    def validate(self, dev: str, node: str) -> JoinResult:
        held = self.validated.get(dev)
        if held is not None:
            return self._granted(self.role_table.roles[held[1]])
        candidates = self.role_table.node_map.get(node)
        if not candidates:
            return self._deny("no such node",
                              f"this Bit declares no node {node!r}")
        cap = self.scored_cap()
        if cap is not None and len(self.validated) >= cap:
            return self._deny("scored full", _SCORED_FULL_HINT)
        saw_scored = False
        for role_name in candidates:
            role = self.role_table.roles[role_name]
            if not role.scored or role.role_class is RoleClass.ROOM:
                continue
            saw_scored = True
            if role.capacity is not None and \
                    self._counts[role_name] >= role.capacity:
                continue
            self.validated[dev] = (node, role_name)
            self._counts[role_name] += 1
            return self._granted(role)
        if saw_scored:
            return self._deny("scored full", _SCORED_FULL_HINT)
        return self._deny("no such node",
                          f"node {node!r} grants no scored role")

    def assign(self, dev: str, node: str, role: Role) -> bool:
        current = self.assignments.get(dev)
        if current is not None and current[1] == role.name:
            return False
        self.release(dev)
        if role.name not in self.role_table.roles:
            self.role_table.roles[role.name] = role
        self._counts.setdefault(role.name, 0)
        self.assignments[dev] = (node, role.name, role.role_class)
        self._counts[role.name] += 1
        return True

    def materialize(self, jam_devs, jam_for) -> list:
        out = []
        reserved, self.validated = self.validated, {}
        for dev, (node, role_name) in reserved.items():
            role = self.role_table.roles[role_name]
            # The reservation already counted this slot; move it, do not
            # count it twice.
            self.assignments[dev] = (node, role_name, role.role_class)
            out.append((dev, role))
        for dev in jam_devs:
            if dev in self.assignments:
                continue
            role = jam_for(dev)
            if self.assign(dev, "", role):
                out.append((dev, role))
        return out

    def release(self, dev: str) -> bool:
        held = self.validated.pop(dev, None)
        if held is not None:
            self._counts[held[1]] -= 1
            return True
        prev = self.assignments.pop(dev, None)
        if prev is None:
            return False
        _, role_name, _ = prev
        self._counts[role_name] -= 1
        return True
```

Also `release_all()` must clear validated reservations silently and
return ONLY devices that held an assignment (a validated device never had
a role, so it must get no `/release` and no `drop_dev`):

```python
    def release_all(self) -> list[str]:
        for dev in list(self.validated):
            self.release(dev)
        devs = list(self.assignments)
        for dev in devs:
            self.release(dev)
        return devs
```

Add to the tests:

```python
def test_release_all_returns_only_assigned():
    t = _table()
    reg = RegistrationState(t)
    reg.validate("a", "P")
    reg.assign("c", "J", t.roles["jammer"])
    assert reg.release_all() == ["c"]
    assert reg.validated == {} and ("player", 0, 2) in reg.counts()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_registration.py -q`
Expected: PASS. (The engine still calls `registration.join`; that breaks
until Task 6. Do not run the full suite here.)

- [ ] **Step 5: Commit**

```bash
git add control/registration.py tests/test_registration.py
git commit -m "feat(control): validated reservations and materialize in RegistrationState"
```

---

### Task 4: Jam role resolver and synthesized Solo role

**Files:**
- Create: `control/jam_role.py`
- Test: `tests/test_jam_role.py`

**Interfaces:**
- Consumes: `control.roles.Role`, `RoleClass`, `control.instrument.Instrument` (`.name`, `.light_manifest`, `.solo: SoloConfig | None` with `.light_manifest`, `.bindings`).
- Produces:
  - `SOLO_PREFIX = "solo:"`
  - `solo_role(instrument) -> Role`
  - `bit_jam_role(role_table) -> Role | None`
  - `is_solo_role(name: str) -> bool`
  - `solo_event(verb: str, args: list) -> str` (`"double_tap"` for a tap with count >= 2, else the verb)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jam_role.py
from control.instrument import DEFAULTSHROOM, TUNESHROOM
from control.jam_role import (SOLO_PREFIX, bit_jam_role, is_solo_role,
                              solo_event, solo_role)
from control.roles import Role, RoleClass, RoleTable


def test_solo_role_from_solo_table():
    role = solo_role(TUNESHROOM)
    assert role.name == SOLO_PREFIX + TUNESHROOM.name
    assert role.role_class is RoleClass.JAM and role.scored is False
    assert role.capacity is None
    assert role.light_manifest == TUNESHROOM.solo.light_manifest
    assert set(role.uses) == {"tap", "shake"}


def test_solo_role_falls_back_to_ambient():
    assert DEFAULTSHROOM.solo is None
    role = solo_role(DEFAULTSHROOM)
    assert role.light_manifest == DEFAULTSHROOM.light_manifest
    assert role.uses == []


def test_solo_role_manifest_is_a_copy():
    role = solo_role(TUNESHROOM)
    role.light_manifest["x"] = 1
    assert "x" not in TUNESHROOM.solo.light_manifest


def test_bit_jam_role():
    jam = Role("jammer", RoleClass.JAM, None, False)
    t = RoleTable(roles={"p": Role("p", RoleClass.UNIQUE, 1, True),
                         "jammer": jam}, node_map={})
    assert bit_jam_role(t) is jam
    assert bit_jam_role(RoleTable(roles={}, node_map={})) is None


def test_solo_event():
    assert solo_event("tap", ["d", 1.0, 50.0, 2]) == "double_tap"
    assert solo_event("tap", ["d", 1.0, 50.0, 1]) == "tap"
    assert solo_event("shake", ["d", 1.0, 1.0, 1.0]) == "shake"
    assert is_solo_role("solo:tuneshroom") and not is_solo_role("jammer")
```

If `TUNESHROOM.solo` is `None` in code (it is the Python literal; check
`control/instrument.py` around the `solo=SoloConfig(` line), the first test
documents that it must carry the same `[solo]` as `instruments/tuneshroom.toml`
(`tests/test_catalog.py` already pins them equal).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_jam_role.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""The jam role a device gets at RUNNING when it did not validate (spec
2026-10-01-instrument-handshake-protocol section 3.7): the Bit's own
JAM-class role, else a role synthesized per carried instrument from its
[solo] table, so an undeclared jam follows the instrument's Solo
behaviour. Pure stdlib."""
from __future__ import annotations

from copy import deepcopy

from control.roles import Role, RoleClass

SOLO_PREFIX = "solo:"
_EVENT_VERB = {"tap": "tap", "double_tap": "tap", "shake": "shake"}


def bit_jam_role(role_table) -> Role | None:
    for role in role_table.roles.values():
        if role.role_class is RoleClass.JAM:
            return role
    return None


def solo_role(instrument) -> Role:
    solo = instrument.solo
    if solo is not None:
        light = deepcopy(solo.light_manifest)
        uses = sorted({_EVENT_VERB[e] for e in solo.bindings
                       if e in _EVENT_VERB})
    else:
        light = deepcopy(instrument.light_manifest)
        uses = []
    return Role(name=SOLO_PREFIX + instrument.name,
                role_class=RoleClass.JAM, capacity=None, scored=False,
                uses=uses, light_manifest=light)


def is_solo_role(name: str) -> bool:
    return name.startswith(SOLO_PREFIX)


def solo_event(verb: str, args: list) -> str:
    if verb == "tap" and len(args) > 3:
        try:
            if int(args[3]) >= 2:
                return "double_tap"
        except (TypeError, ValueError):
            pass
    return verb
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_jam_role.py tests/test_catalog.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add control/jam_role.py tests/test_jam_role.py
git commit -m "feat(control): jam role resolver with a synthesized Solo fallback"
```

---

### Task 5: One start decider

**Files:**
- Modify: `control/start_condition.py`, `control/lobby.py`
- Test: `tests/test_start_condition.py`, `tests/test_lobby.py`, `tests/test_engine_start.py`

**Interfaces:**
- Produces in `control/start_condition.py`:
  - `StartDecision(accepted: bool, reason: str | None, feedback: str)` (moved from `control/lobby.py`, re-exported there for one release is NOT wanted: update importers instead)
  - `decide_start(*, bit_loaded, in_setup, when, expected_key, key, admin, scored, min_scored) -> StartDecision` (moved verbatim, behaviour unchanged)
  - `timer_decision(cond, *, scored, elapsed, setup_seconds) -> str | None` (renamed `start_decision`; `"start"`, `"abort"`, `"start-on-timeout"` semantics unchanged)
  - Feedback constants `FEEDBACK_*` stay in `control/lobby.py` (imported by `start_condition`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_start_condition.py (append)
def test_single_module_owns_both_deciders():
    import control.lobby as lobby
    import control.start_condition as sc
    assert hasattr(sc, "decide_start") and hasattr(sc, "timer_decision")
    assert not hasattr(lobby, "decide_start")
    assert not hasattr(sc, "start_decision")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_start_condition.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement.** Move `StartDecision` and `decide_start` from
  `control/lobby.py` into `control/start_condition.py` (import
  `FEEDBACK_*` from `control.lobby`; `control/lobby.py` must not import
  `start_condition`, to avoid a cycle). Rename `start_decision` to
  `timer_decision`. Update every importer:
  `grep -rn "decide_start\|start_decision\|StartDecision" --include=*.py .`
  (excluding `.venv`): `control/engine.py`, `harness/terrarium_boot.py`,
  tests. Update docstrings that cite the old names.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_start_condition.py tests/test_lobby.py tests/test_engine_start.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add control/ harness/terrarium_boot.py tests/
git commit -m "refactor(control): one start-decider module"
```

---

### Task 6: Engine: round id, handshake, materialize at run, grants, walk-ups, Solo dispatch

**Files:**
- Modify: `control/engine.py`, `control/bit_config.py`, `control/role_config.py`, `control/lobby.py`
- Create: `tests/helpers_admit.py`
- Test: `tests/test_engine_handshake.py` (create)

**Interfaces:**
- Consumes: Task 3 `RegistrationState` API; Task 4 `bit_jam_role`, `solo_role`, `is_solo_role`, `solo_event`; Task 5 `decide_start`.
- Produces on `GameServer`:
  - `.round_id: str | None` (set in `load_bit`, cleared in `_unload`)
  - `.handshake(dev: str, round_id: str, node: str) -> JoinResult` (`config` is None; `role` set on accept)
  - `.default_scored_node() -> str | None` (moved from `DeviceLinkAgent._default_scored_node`)
  - `.on_grant: Callable[[str, JoinResult], None] | None` sink, called once per new non-ROOM assignment with a composed `config`
  - `.notify_devices_changed() -> None` (public)
  - `.lobby_state()` unchanged signature; `control/lobby.py`'s free `lobby_state` becomes `_lobby_state_of(counts, role_table, cap)` used only by it, now FULL when `len(validated) >= scored_cap()`.
  - `join()` removed.
  - `request_start(..., source="timer")` accepted like any source.
- Produces `BitConfig.lobby.max_scored: int | None`.
- Produces `tests/helpers_admit.py`: `admit(gs, dev, node="", instrument=None) -> JoinResult` (hello if not pooled, then `handshake(dev, gs.round_id, node)`); `admit_running(gs, dev, node="", instrument=None) -> JoinResult` (admit, then `gs.request_start(None, "terrarium", "test")` if SETUP; returns admit's result).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_engine_handshake.py
import pytest

from control.engine import GameServer
from control.bit import Bit
from control.roles import Role, RoleClass, RoleTable
from control.state import State


class _Bit(Bit):
    def __init__(self, config=None, jam=True, cap=1):
        super().__init__(config)
        self._jam, self._cap = jam, cap
        self.joined = []

    @property
    def role_table(self):
        roles = {"player": Role("player", RoleClass.UNIQUE, self._cap, True)}
        nodes = {"P": ["player"]}
        if self._jam:
            roles["jammer"] = Role("jammer", RoleClass.JAM, None, False)
            nodes["J"] = ["jammer"]
        return RoleTable(roles=roles, node_map=nodes)

    def on_join(self, dev, role_name):
        self.joined.append((dev, role_name))


class _NoJamBit(_Bit):
    def __init__(self, config=None):
        super().__init__(config, jam=False)


def _gs(bit=_Bit):
    gs = GameServer({"B": bit}, clock=lambda: 100.0)
    gs.load_bit("B")
    grants = []
    gs.on_grant = lambda dev, result: grants.append((dev, result))
    return gs, grants


def test_round_id_minted_and_cleared():
    gs, _ = _gs()
    rid = gs.round_id
    assert rid and rid.startswith("B-")
    gs.abort()
    assert gs.round_id is None


def test_handshake_requires_hello():
    gs, _ = _gs()
    r = gs.handshake("a", gs.round_id, "")
    assert not r.granted and r.reason == "not connected"


def test_handshake_validates_default_node_without_role_send():
    gs, grants = _gs()
    gs.hello("a", "", "", None)
    r = gs.handshake("a", gs.round_id, "")
    assert r.granted and r.role == "player"
    assert grants == [] and gs.bit.joined == []
    assert "a" in gs.registration.validated


def test_stale_round_dropped():
    gs, _ = _gs()
    gs.hello("a", "", "", None)
    r = gs.handshake("a", "old-round", "")
    assert not r.granted and r.reason is None   # dropped silently
    assert gs.registration.validated == {}


def test_over_cap_then_jam_at_run():
    gs, grants = _gs()
    for d in ("a", "b", "c"):
        gs.hello(d, "", "", None)
    assert gs.handshake("a", gs.round_id, "").granted
    over = gs.handshake("b", gs.round_id, "")
    assert over.reason == "scored full" and over.hint
    gs.request_start(None, "terrarium", "test")
    assert gs.state is State.RUNNING
    assert [(d, r.role, r.scored) for d, r in grants] == [
        ("a", "player", True), ("b", "jammer", False), ("c", "jammer", False)]
    assert all(r.config is not None for _, r in grants)
    assert gs.bit.joined == [("a", "player"), ("b", "jammer"), ("c", "jammer")]


def test_handshake_in_running_denied():
    gs, _ = _gs()
    gs.hello("a", "", "", None)
    gs.request_start(None, "terrarium", "test")
    gs.hello("z", "", "", None)
    r = gs.handshake("z", gs.round_id, "")
    assert r.reason == "registration closed"


def test_running_walk_up_gets_jam_once():
    gs, grants = _gs()
    gs.request_start(None, "terrarium", "test")
    gs.hello("w", "", "", None)
    gs.hello("w", "", "", None)
    assert [(d, r.role) for d, r in grants] == [("w", "jammer")]


def test_no_jam_role_gets_solo():
    gs, grants = _gs(_NoJamBit)
    gs.hello("c", "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    (dev, r), = grants
    assert r.role == "solo:tuneshroom" and r.scored is False
    assert r.config["class"] == "jam"


def test_reap_frees_validated_slot():
    t = [100.0]
    gs = GameServer({"B": _Bit}, clock=lambda: t[0])
    gs.load_bit("B")
    gs.hello("a", "", "", None)
    gs.handshake("a", gs.round_id, "")
    t[0] += 20
    gs.hello("b", "", "", None)
    gs.reap_stale(15.0)
    assert gs.handshake("b", gs.round_id, "").granted


def test_default_instrument_is_defaultshroom():
    gs, grants = _gs()
    gs.hello("c", "", "", None)
    gs.request_start(None, "terrarium", "test")
    assert grants[0][1].config["instrument"]["name"] == "defaultshroom"


def test_lobby_full_follows_validated():
    gs, _ = _gs()
    gs.hello("a", "", "", None)
    assert gs.lobby_state() == "WAITING"
    gs.handshake("a", gs.round_id, "")
    assert gs.lobby_state() == "FULL"
```

Add one Solo dispatch test (gesture from a solo-role dev fires the bound
instrument function through `fire_function`):

```python
def test_solo_binding_fires_instrument_function():
    gs, _ = _gs(_NoJamBit)
    fired = []
    gs.add_observer(type("O", (), {"on_function_fired":
                    lambda self, rec: fired.append(rec)})())
    gs.hello("c", "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    assert gs.data("c", "tap", ["c", 1.0, 50.0, 1]) is None
    assert fired and fired[-1].name == "play_aurora"
    assert gs.data("c", "tilt", ["c", 10.0]) is None     # unbound: dropped
```

Check the observer hook and record field names against
`control/engine.py` (`grep -n on_function_fired control/engine.py`) and
`FunctionFired`'s fields before relying on `.name`; adjust the test, not
the engine, if the field is spelled differently.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_engine_handshake.py -q`
Expected: FAIL (`handshake` missing).

- [ ] **Step 3: Implement**

1. `control/bit_config.py`: add `max_scored: int | None = None` to the
   parsed `[lobby]` (and to `control/lobby.py`'s `LobbyConfig`, which is
   what `lobby_config()` returns); validate positive int, else
   `BitConfigError` (match the file's existing error type). Remove
   `double_tap_window_s` from `LobbyConfig` and the parser (warn-as-unknown
   if a manifest still sets it, using the file's existing unknown-key path).
2. `load_bit`: build `RegistrationState(role_table, max_scored=...)` from
   `lobby_config().max_scored` of the new bit (read off `bit.config` the
   same way `lobby_config()` does); if `max_scored` exceeds a bounded
   `scored_cap()` computed without it, raise `ValueError` inside the try
   (becomes `BitLoadError`). Mint
   `self.round_id = f"{name}-{self._round_counter}-{secrets.token_hex(3)}"`
   (`self._round_counter` initialised to 0 in `__init__`, incremented per
   load). Clear `self.round_id = None` in `_unload`.
3. Move `_default_scored_node` from `devicelink/agent.py` to
   `GameServer.default_scored_node()` verbatim (adjust `gs.` to `self.`).
4. Add `handshake`:

```python
    def handshake(self, dev: str, round_id: str, node: str) -> JoinResult:
        """Received Handshake (spec section 3.4). Never raises. A stale
        round_id returns a non-granted result with reason None: dropped,
        not denied."""
        if self.registration is None:
            return JoinResult(granted=False, reason="registration closed",
                              hint="no Bit loaded")
        if node and self._is_room_node(node):
            if not self._room_armed():
                return JoinResult(granted=False, reason="no such node",
                                  hint="no Room fixture is armed")
            result = self.registration.join_room(dev, node)
            if result.granted:
                self._bind_room(dev)
            return result
        if self.devices.get(dev) is None:
            return JoinResult(granted=False, reason="not connected",
                              hint="send /game/hello first")
        if self.bit is None or self.state is not State.SETUP:
            hint = ("scored slots open only in SETUP; you will get a jam "
                    "role at start" if self.state is State.RUNNING
                    else "no Bit loaded")
            return JoinResult(granted=False, reason="registration closed",
                              hint=hint)
        if round_id != self.round_id:
            logger.info("handshake: stale round %r from %s", round_id, dev)
            return JoinResult(granted=False)
        target = node or self.default_scored_node()
        if target is None:
            return JoinResult(granted=False, reason="no such node",
                              hint="this Bit declares no scored node")
        result = self.registration.validate(dev, target)
        if not result.granted:
            return result
        role = self.registration.role_table.roles[result.role]
        if role.requires is not None:
            req = self._slot_requirements.get(role.requires)
            carried = getattr(self.devices.get(dev), "carried", None) \
                or DEFAULTSHROOM
            reason = satisfies(carried, req) if req is not None else None
            if reason is not None:
                self.registration.release(dev)
                return JoinResult(granted=False, reason=reason,
                                  hint=f"this role needs slot {role.requires!r}")
        self._notify("on_registration_change")
        return result
```

   Room nodes: add `RegistrationState.join_room(dev, node) -> JoinResult`
   in `control/registration.py` that walks `node_map[node]` for the ROOM
   role with capacity (today's `join` logic restricted to `RoleClass.ROOM`)
   and calls `assign`. Add a Task 3-style unit test for it in
   `tests/test_registration.py`.
5. Grants. Add `self.on_grant = None` in `__init__` (beside `on_release`)
   and:

```python
    def _jam_for(self, dev: str):
        role = bit_jam_role(self.registration.role_table)
        carried = getattr(self.devices.get(dev), "carried", None) \
            or DEFAULTSHROOM
        if role is not None and role.requires is not None:
            req = self._slot_requirements.get(role.requires)
            if req is not None and satisfies(carried, req) is not None:
                logger.info("jam role %s refuses %s; using solo", role.name, dev)
                role = None
        return role if role is not None else solo_role(carried)

    def _grant(self, dev: str, role) -> None:
        carried = getattr(self.devices.get(dev), "carried", None) \
            or DEFAULTSHROOM
        result = JoinResult(granted=True, role=role.name,
                            role_class=role.role_class, scored=role.scored,
                            breath=role.breath)
        if role.requires is not None:
            result.slot = role.requires
            result.instrument = carried.name
        result.config = compose_role_config(
            self.bit_name, self.bit.version, role,
            room_name=self.provenance.get("room_name"),
            terrarium_config_version=self.provenance.get(
                "terrarium_config_version"),
            slot=result.slot, instrument=result.instrument,
            event_triggers=carried.event_triggers, carried=carried)
        try:
            self.bit.on_join(dev, role.name)
        except Exception:
            logger.exception("Bit.on_join failed; continuing")
        if self.on_grant is not None:
            try:
                self.on_grant(dev, result)
            except Exception:
                logger.exception("on_grant raised for %s; continuing", dev)

    def _jam_candidates(self) -> list[str]:
        room_devs = set(self.room.bound.values()) if self.room else set()
        return [info.dev for info in self.devices.all()
                if info.dev not in room_devs
                and info.dev not in self.registration.assignments
                and not self.is_admin(info.dev)]
```

   `run()`:

```python
    def run(self) -> None:
        if self.state != State.SETUP:
            raise InvalidTransition(
                f"run requires SETUP, current state is {self.state}")
        self._run_elapsed = 0.0
        self._set_state(State.RUNNING)
        granted = self.registration.materialize(self._jam_candidates(),
                                                self._jam_for)
        for dev, role in granted:
            self._grant(dev, role)
        if granted:
            self._notify("on_registration_change")
            self._notify("on_devices_change")
        self.bit.on_run_start()
```

   (State is set to RUNNING first so the agent's `on_state_change` has
   torn the lobby down and the bridges built in `on_grant` render Bit
   light, not lobby overrides.)

   `hello()` walk-up, appended after `self.devices.hello(...)`:

```python
        if self.state is State.RUNNING and self.registration is not None \
                and dev in self._jam_candidates():
            role = self._jam_for(dev)
            if self.registration.assign(dev, "", role):
                self._grant(dev, role)
                self._notify("on_registration_change")
```

   `hello()` must also only fire `on_devices_change` when the device is
   new or its carried instrument changed (cleanup 8): compare
   `self.devices.get(dev)` before and after; keep firing for a new dev.
6. Solo dispatch in `data()`: after the `dev not in assignments` check:

```python
        role_name = self.registration.assignments[dev][1]
        if is_solo_role(role_name):
            return self._solo_gesture(dev, verb, args, gesture_time)
```

```python
    def _solo_gesture(self, dev, verb, args, gesture_time) -> None:
        carried = getattr(self.devices.get(dev), "carried", None) \
            or DEFAULTSHROOM
        bindings = carried.solo.bindings if carried.solo else {}
        fn = bindings.get(solo_event(verb, args))
        if fn is None:
            return None
        at = self._origin(gesture_time) + self._horizon
        self.fire_function(fn, fired_by=FIRED_BY_GESTURE_VERB, dev=dev, at=at)
        return None
```

7. Replace the `join()` method entirely (delete it). The ROOM path lives
   in `handshake`.
8. `lobby_state()`: FULL iff `scored_cap()` is not None and
   `len(registration.validated) >= scored_cap()`; delete the free
   `lobby_state` in `control/lobby.py` (cleanup 10: one copy).
9. `notify_devices_changed()`: `self._notify("on_devices_change")`.
9a. `reap_stale`: a dev that is only VALIDATED must free its slot too.
    Replace the `dev in self.registration.assignments` test with
    `held = dev in self.registration.assignments`, `reserved = dev in self.registration.validated`;
    call `self.registration.release(dev)` when either; call `on_release`
    and `_clear_stream_trigger_state` only when `held`; set
    `released_any = True` when either (so the lobby leaves FULL).
10. `request_start`: unchanged except imports from `control.start_condition`.
    `scored_count` must count validated devices in SETUP; since
    `validate` increments `_counts`, `counts()` already does. Add a test
    asserting `players` start fires on validation count.
11. `control/role_config.py`: delete the flat `config["instrument"] = ...`
    assignment that the `carried` view always overwrites (find it with
    `grep -n '"instrument"' control/role_config.py`).
12. Replace every remaining `TUNESHROOM` fallback for a carried instrument
    in `control/engine.py` with `DEFAULTSHROOM` (cleanup 5).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_engine_handshake.py tests/test_registration.py tests/test_jam_role.py tests/test_start_condition.py -q`
Expected: PASS.

- [ ] **Step 5: Write `tests/helpers_admit.py`**

```python
"""Test helpers for the contract v3 entry path (spec 2026-10-01). Most
older tests only need "this device holds role X"; these give it the
real way: hello, handshake, and (for gameplay) start."""
from control.lobby import TERRARIUM_ADMIN
from control.state import State


def admit(gs, dev, node="", instrument=None):
    if gs.devices.get(dev) is None:
        gs.hello(dev, "", "", instrument)
    return gs.handshake(dev, gs.round_id, node)


def admit_running(gs, dev, node="", instrument=None):
    result = admit(gs, dev, node, instrument)
    if gs.state is State.SETUP:
        gs.request_start(None, TERRARIUM_ADMIN, "test")
    return result
```

- [ ] **Step 6: Commit**

```bash
git add control/ tests/test_engine_handshake.py tests/test_registration.py tests/helpers_admit.py
git commit -m "feat(control): handshake validation, materialize at run, jam and solo grants"
```

---

### Task 7: Migrate the existing engine-level tests off `join`

Mechanical but large; a separate task so a reviewer can check behaviour
was preserved, not just made green.

**Files:**
- Modify: every file from `grep -rlE "(gs|server|game_server|engine|registration|self\.gs)\.join\(" tests/` (at plan time: `test_engine.py`, `test_engine_data.py`, `test_console_agent.py`, `test_engine_requirements.py`, `test_start_condition.py`, `test_rev1_bit.py`, `test_engine_functions.py`, `test_engine_on_join.py`, `test_contract_bit.py`, `test_capture_bit.py`, `test_terrarium_cycle.py`, `test_minigame_bit.py`, `test_metronome_bit_engine.py`, `test_metronome_bit_declarations.py`, `test_device_bridge.py`, `test_test_bit.py`, `test_engine_start.py`)
- Not here: `test_link.py`, `test_devicelink_*`, `test_contract_*`, `test_shroom_client.py`, `test_terrarium_boot.py` (Tasks 8-11).

**Interfaces:**
- Consumes: `tests/helpers_admit.py` (`admit`, `admit_running`).

- [ ] **Step 1: Inventory.** Run the full suite; list every failure in
  the files above with its cause. For each call site decide by intent:
  - "device holds a role so a gesture works" → `admit_running(gs, dev, node)`.
  - "join in SETUP is granted/denied" → `admit(gs, dev, node)` and assert
    on validation (`granted`, `reason`), remembering no role or `on_join`
    exists until start.
  - "role switch by re-tapping another node" → delete the test (no
    longer a feature; spec 3.3 removes `/game/join`), note it in the report.
  - "scored refused in RUNNING, jam granted" → rewrite as: RUNNING
    handshake denied `registration closed`; walk-up gets the jam role.
  - `test_engine_on_join.py` → `on_join` fires at `request_start`, in
    validation order, scored then jam.

- [ ] **Step 2: Migrate file by file**, running each file after editing:
  `.venv/bin/python -m pytest tests/<file> -q`. Never weaken an assertion
  to pass; if a behaviour changed by design, assert the new behaviour and
  cite the spec section in a one-line comment.

- [ ] **Step 3: Run those files together**

Run: `.venv/bin/python -m pytest tests/test_engine*.py tests/test_*_bit*.py tests/test_start_condition.py tests/test_console_agent.py tests/test_terrarium_cycle.py tests/test_device_bridge.py tests/test_registration.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: migrate engine-level tests to the handshake entry path"
```

---

### Task 8: DeviceLinkAgent and LobbyRuntime: handshake invites, grants, cleanups

**Files:**
- Modify: `devicelink/agent.py`, `devicelink/lobby_runtime.py`, `control/lobby.py`
- Test: `tests/test_devicelink_agent.py`, `tests/test_lobby_agent.py`, `tests/test_lobby_runtime.py`, `tests/test_lobby_frames.py`, `tests/test_devicelink_frames.py`, `tests/test_lobby.py`

**Interfaces:**
- Consumes: `GameServer.handshake`, `.on_grant`, `.round_id`, `.notify_devices_changed`, `.default_scored_node`; `protocol.handshake_event`, `validated_event`, `parse_handshake_args`.
- Produces: `LobbySinks.send_handshake: Callable[[str], None]` replacing `request_join`; `LobbyRuntime.stop()` keeps queued ceremony thunks; `DeviceLinkAgent._on_grant(dev, result)`.

- [ ] **Step 1: Write the failing tests** (agent over `FakeO2Lite`, reuse
  the construction helper at the top of `tests/test_devicelink_agent.py`;
  read it first and use the same fixture names)

```python
def test_invite_sends_handshake_over_tcp(agent_with_room):
    agent, fake, gs = agent_with_room          # SETUP, lobby enabled
    deliver_hello(fake, "ie1")
    agent.poll()
    sent = [(s[0], s[3], c) for s, c in zip(fake.sent, fake.channels)
            if s[0] == "/ie1/handshake"]
    assert sent and sent[0][1] == (gs.round_id,) and sent[0][2] == "tcp"


def test_ack_validates_then_role_at_start(agent_with_room):
    agent, fake, gs = agent_with_room
    deliver_hello(fake, "ie1"); agent.poll()
    fake.deliver("/game/handshake", "sss", ("ie1", gs.round_id, ""))
    agent.poll()
    assert addrs(fake, "/ie1/validated") and not addrs(fake, "/ie1/role")
    gs.request_start(None, "terrarium", "test"); agent.poll()
    assert addrs(fake, "/ie1/role")


def test_join_is_retired(agent_with_room):
    agent, fake, gs = agent_with_room
    deliver_hello(fake, "ie1"); agent.poll()
    fake.deliver("/game/join", "ss", ("ie1", "TEST_PLAYER_NODE"))
    agent.poll()
    err = [s[3] for s in fake.sent if s[0] == "/ie1/error"]
    assert ("join", "retired in contract v3: use /game/handshake") in err


def test_tap_is_never_a_handshake(agent_with_room):
    agent, fake, gs = agent_with_room
    deliver_hello(fake, "ie1"); agent.poll()
    fake.deliver("/game/tap", "sffi", ("ie1", 1.0, 50.0, 2)); agent.poll()
    assert "ie1" not in gs.registration.validated


def test_room_only_on_first_contact(agent_with_room):
    agent, fake, gs = agent_with_room
    deliver_hello(fake, "ie1"); agent.poll()
    n = len(addrs(fake, "/ie1/room"))
    deliver_hello(fake, "ie1"); agent.poll()
    assert len(addrs(fake, "/ie1/room")) == n


def test_reaped_unjoined_device_leaves_no_transport_state(agent_with_room):
    agent, fake, gs = agent_with_room
    deliver_hello(fake, "ie1"); agent.poll()
    fake.set_time(fake.time_get() + 30); agent.poll()
    assert "ie1" not in agent.transport._devs
    assert "ie1" not in agent.canvas_urls()
```

`deliver_hello`, `addrs` and `agent_with_room` are the names to create in
the test module if the file has no equivalents (it has a Room-loaded
agent helper used by the lobby tests in `tests/test_lobby_agent.py`;
reuse that rather than writing a second one).

```python
# tests/test_lobby_runtime.py (append)
def test_ceremony_survives_stop(runtime_and_sinks, clock):
    rt, sinks = runtime_and_sinks
    rt.on_scored_join("ie1")
    rt.stop()
    clock.advance(2.0)
    rt.tick()
    assert sinks.plays == [("ie1", "chime", sinks.plays[0][2])]
```

Use the existing sink-recording fixture in `tests/test_lobby_runtime.py`
(read it; rename `plays` above to whatever list it records `send_play`
into).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_lobby_runtime.py -q`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

`devicelink/lobby_runtime.py`:
- `LobbySinks`: replace `request_join` with `send_handshake: Callable[[str], None]`.
- Remove `_taps`, `DoubleTapDetector` import, `observe_tap`, and the
  `self._taps.*` calls in `stop()`/`forget()`.
- `consider_invite`: after the white-flash scheduling, call
  `self._s.send_handshake(dev)` (every invite cycle, including the first).
- `stop()`: do NOT replace `self._queue`; keep draining it in `tick()`
  after stop (tick already runs due thunks before the `_running` check).
  Invites already stop because `_invites.clear()` runs and
  `consider_invite` is not called once the agent drops the runtime; so
  the agent must keep ticking a stopped runtime until its queue is empty:
  add `def draining(self) -> bool: return not self._running and len(self._queue) > 0`
  (use the `TimedQueue` length/peek API, check `control/timed_queue.py`).

`control/lobby.py`: delete `DoubleTapDetector` and the
`double_tap_window_s` field (Task 6 removed the parser side).

`devicelink/agent.py`:
- `_handle`: `elif verb == "handshake": self._on_handshake(client, dev, env.args)`;
  `elif verb == "join": self._send(dev, protocol.error_event(dev, "join", "retired in contract v3: use /game/handshake"))`.
- `_on_handshake`:

```python
    def _on_handshake(self, client, dev: str, args: list) -> None:
        try:
            _dev, round_id, node = protocol.parse_handshake_args(args)
        except ValueError as exc:
            logger.warning("dropping malformed handshake from %s: %s", dev, exc)
            return
        self.transport.bind_dev(dev, client)
        result = self.game_server.handshake(dev, round_id, node)
        if result.granted and result.role_class == RoleClass.ROOM:
            self._drop_player_bridge(dev)
            if self._lobby is not None:
                self._lobby.forget(dev)
            return
        if result.granted:
            self._send(dev, protocol.validated_event(
                dev, self.game_server.round_id, result.role))
            return
        if result.reason is None:
            return        # stale round: dropped, logged by the engine
        self._send(dev, protocol.deny_event(dev, result.reason, result.hint))
        self._notify_join_denied(dev, node, result.reason)
```

- Delete `_on_join`, `_handshake_join`, `_default_scored_node` (moved to
  the engine), and the lobby tap interception block in `_on_verb`; update
  `_on_verb`'s docstring (only `start` bypasses `data()` now).
- `_on_grant(dev, result)`: the body of the old `_on_join` from
  `bridge = DeviceBridge(...)` through `self._send(dev, protocol.role_event(...))`,
  verbatim. Wire it in `__init__` beside the existing
  `game_server.on_release = self._on_release` assignment:
  `game_server.on_grant = self._on_grant`.
- `_on_hello`: send `/room` only when the dev was not in the pool before
  this hello: capture `new = self.game_server.devices.get(dev) is None`
  before calling `game_server.hello`.
- `_lobby_sinks`: `send_handshake=lambda dev: self._send(dev, protocol.handshake_event(dev, gs.round_id))`
  (guard `gs.round_id is None` → no send).
- `_exit_lobby`: keep a stopped runtime in `self._draining_lobby` while
  `draining()`; `poll()` ticks it until empty, then drops it.
- `on_registration_change`: the ceremony diff now reads
  `gs.registration.validated` (new validated devs since last time) instead
  of scored assignments; keep `self._lobby_known` as the validated set,
  and seed it in `_enter_lobby` from `gs.registration.validated` (not
  `assignments`).
  Remove the second `lobby.forget` call in the deleted `_on_join` (only
  this site forgets now, cleanup 10).
- `_on_canvas`: `self.game_server.notify_devices_changed()` instead of
  `_notify`.
- Reap cleanup (cleanup 6): in `poll()`, after `reap_stale` returns the
  reaped list, for each dev with no bridge and not closing:
  `self.transport.drop_dev(dev)`, `self._canvas_urls.pop(dev, None)`,
  `if self._lobby: self._lobby.forget(dev)`.
- Docstrings: replace `/ie<N>/` with `/<dev>/` across the file.

- [ ] **Step 4: Migrate the remaining devicelink tests.** Fix every
  failure in `tests/test_devicelink_agent.py`, `tests/test_lobby_agent.py`,
  `tests/test_lobby_frames.py`, `tests/test_devicelink_frames.py`,
  `tests/test_lobby.py` using the Task 7 rules (a device gets a role only
  after start; the double-tap tests become handshake-ack tests).

Run: `.venv/bin/python -m pytest tests/test_devicelink_*.py tests/test_lobby*.py tests/test_link.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add devicelink/ control/lobby.py tests/
git commit -m "feat(devicelink): handshake invites, validated acks, grants at RUNNING"
```

---

### Task 9: Harness: Testshroom handshake, markers, timer start via request_start

**Files:**
- Modify: `harness/o2_shroom.py`, `harness/shroom_client.py`, `harness/markers.py`, `harness/run_stack.py`, `harness/terrarium_boot.py`
- Test: `tests/test_shroom_client.py`, `tests/test_markers.py`, `tests/test_terrarium_boot.py`, `tests/test_run_stack.py` (whichever exists for run_stack; `ls tests | grep run_stack`)

**Interfaces:**
- Produces: `ShroomClient.round_id: str | None` (latest `/handshake`), `ShroomClient.validated_role: str | None`; markers `HANDSHAKE_VALIDATED` (`"HANDSHAKE VALIDATED:"`) and `ROLE_GRANTED` (`"ROLE GRANTED:"`, printed as `ROLE GRANTED: <dev> scored|jam <role>`); o2_shroom flag `--handshake-delay SECONDS` (default 0.0).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_shroom_client.py (append)
def test_client_tracks_handshake_and_validated(client_factory):
    c = client_factory("ie1")
    c.on_message("/ie1/handshake", "s", ["r1"])
    assert c.round_id == "r1"
    c.on_message("/ie1/validated", "ss", ["r1", "player"])
    assert c.validated_role == "player"
```

(Use the dispatch entry point `ShroomClient` already exposes for inbound
messages; read `harness/shroom_client.py` and match it.)

```python
# tests/test_markers.py (append)
from harness import markers

def test_v3_markers_have_remedies_or_are_ready():
    assert markers.HANDSHAKE_VALIDATED in markers.READY_MARKERS or \
        markers.HANDSHAKE_VALIDATED in markers.INFO_MARKERS
    assert markers.ROLE_GRANTED
```

(Match the marker module's real collection names; read it first.)

```python
# tests/test_terrarium_boot.py (append)
def test_timer_start_goes_through_request_start(...):
    # Drive _wait_in_setup to an "immediate" deadline with a fake gs that
    # records request_start calls; assert one call with source="timer"
    # and that gs.run is never called directly.
```

Write that last test fully against the existing `_wait_in_setup` test
fixtures in `tests/test_terrarium_boot.py` (they already fake `gs` and
the clock); the assertion is: `fake_gs.start_calls == [(None, "terrarium", "timer")]`
and `fake_gs.run_calls == 0`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_shroom_client.py tests/test_markers.py tests/test_terrarium_boot.py -q`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

- `harness/shroom_client.py`: register handlers for `/<dev>/handshake`
  (store `round_id`) and `/<dev>/validated` (store `validated_role`);
  on `/role` print `ROLE GRANTED: <dev> <"scored" if config["scored"] else "jam"> <config["role"]>`
  (flush) and on `/validated` print `HANDSHAKE VALIDATED: <dev> <role>`.
  A `/deny` no longer ends a round: it is informational (the device will
  get jam at start).
- `harness/o2_shroom.py`:
  - Remove `invite_seen`, `HANDSHAKE_RETAP_S`, `invite_flag`,
    `last_handshake_tap`, `send_join`, `--node`'s join use, `--join-retry`
    (and its `--persist` coupling), `explicit_join`, `next_join`,
    `joins_sent`, `join_stall_hint`.
  - `--handshake`: when `client.round_id` is set, no role and not yet
    acked for that round id, wait `--handshake-delay` then
    `o2lite.send_cmd("/game/handshake", 0, "sss", args.dev, client.round_id, args.node or "")`
    once per round id. Without `--handshake` the device only hellos and
    must end up jam.
  - `lobby_round_over`: a deny is never terminal; one-shot mode ends on
    `/release` only.
  - Keep `_gestures_ready` gating on a received role.
- `harness/markers.py`: add the two markers; the deny marker
  `DEVICE_JOIN_DENIED` stays printable but leaves `FAILURE_MARKERS`
  (a deny is expected in the over-cap case).
- `harness/run_stack.py`: the per-device stage `role granted after`
  becomes waiting for `ROLE GRANTED:` (after start); `--handshake-devices N`
  passes `--handshake` to the first N devices; add `--expect-scored K`
  (default: unset) which, under `--ci`, fails the run unless exactly K
  `ROLE GRANTED: ... scored` lines and `devices - K` `... jam` lines
  appear. Drop `--join-retry 2.0` from device args.
- `harness/terrarium_boot.py`: in `_wait_in_setup`'s `"start"` decision
  path and the two `gs.run()` call sites (around the
  `if gs.state is State.SETUP: gs.run()` blocks), call
  `gs.request_start(None, TERRARIUM_ADMIN, "timer")` instead. Update the
  docstrings that say `gs.run()`. Rename the import to `timer_decision`
  (Task 5).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/ -q -x --ignore=tests/test_contract_scenarios.py --ignore=tests/test_contract_recorder.py --ignore=tests/test_export_contract.py`
Expected: PASS (contract kit is Task 11).

- [ ] **Step 5: Commit**

```bash
git add harness/ tests/
git commit -m "feat(harness): Testshroom handshake ack, v3 markers, timer start via request_start"
```

---

### Task 10: TestBit capacity, SoloTestBit, live smoke

**Files:**
- Modify: `bits/test/` (the Bit class and `bit.toml`)
- Create: `bits/solotest/bit.toml`, `bits/solotest/__init__.py`, `bits/solotest/bit.py`
- Test: `tests/test_test_bit.py`, `tests/test_solotest_bit.py` (create)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_solotest_bit.py
from control.bit_registry import BitRegistry
from control.roles import RoleClass
from tests.helpers_admit import admit, admit_running
from control.engine import GameServer


def _gs():
    reg = BitRegistry.scan(["bits"])          # match test_bit_registry.py's call shape
    gs = GameServer({"SoloTestBit": reg["SoloTestBit"].bit_class},
                    clock=lambda: 100.0)
    gs.load_bit("SoloTestBit")
    return gs


def test_declares_no_jam_role():
    gs = _gs()
    classes = {r.role_class for r in gs.registration.role_table.roles.values()}
    assert RoleClass.JAM not in classes


def test_second_device_gets_solo():
    gs = _gs()
    grants = []
    gs.on_grant = lambda d, r: grants.append((d, r.role))
    admit(gs, "a"); gs.hello("b", "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    assert grants == [("a", "player"), ("b", "solo:tuneshroom")]
```

Adjust the registry access to the real `BitRegistry` API used in
`tests/test_bit_registry.py`.

```python
# tests/test_test_bit.py (append)
def test_player_is_unique_capacity_one():
    from bits.test.bit import TestBit      # match the real module path
    role = TestBit().role_table.roles["player"]
    assert role.role_class.name == "UNIQUE" and role.capacity == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_solotest_bit.py tests/test_test_bit.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**
- TestBit: `player` → `RoleClass.UNIQUE`, `capacity=1`; keep everything
  else. Update `docs/MM_TERRARIUM.md`'s TestBit line in Task 12, not here.
- SoloTestBit: copy TestBit's `bit.toml` shape (name `SoloTestBit`,
  `[console] hidden = true`, `room_types` TEST, `[start] when = "immediate"`,
  `requires_terrarium_api = 1`); one scored `UNIQUE` capacity-1 `player`
  role on `SOLOTEST_PLAYER_NODE` with an aurora light manifest; no jam
  role; done after 20 s of RUNNING (`update(dt)` returns True) so a `--ci`
  run completes on its own.

- [ ] **Step 4: Run unit tests**

Run: `.venv/bin/python -m pytest tests/test_solotest_bit.py tests/test_test_bit.py tests/test_bit_packages.py tests/test_bit_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Live smoke** (needs the sibling Arco build; see
  `docs/MM_TERRARIUM.md` *Running it*). Run each, from the worktree root:

```bash
./smoke-test.sh --bit TestBit --devices 3 --handshake-devices 2 --expect-scored 1 --ci
./smoke-test.sh --bit SoloTestBit --devices 3 --handshake-devices 2 --expect-scored 1 --ci
./smoke-test.sh --profile profiles/dev-metronome.toml --ci
```

Expected: exit 0 each; the first two show one `ROLE GRANTED: ... scored`,
two `... jam` (SoloTestBit's say `solo:testshroom`), and one deny
`scored full` before start. If Arco cannot start on this host, report
BLOCKED with the failing stage and log tail; do not skip silently.

- [ ] **Step 6: Commit**

```bash
git add bits/ tests/
git commit -m "feat(bits): TestBit capacity 1 and SoloTestBit for the solo jam path"
```

---

### Task 11: Contract kit v3

**Files:**
- Modify: `contract_kit/contract_bit.py`, `contract_kit/recorder.py`, `contract_kit/scenarios.py`, `tools/export_contract.py`, `tools/record_scenarios.py` (if it lists scenarios), `contract_kit/recordings/*`
- Create: `contract_kit/solo_contract_bit.py`
- Test: `tests/test_contract_bit.py`, `tests/test_contract_recorder.py`, `tests/test_contract_scenarios.py`, `tests/test_export_contract.py`

**Interfaces:**
- Produces: `Recorder(..., handshake: dict | None = None, bit: str = "ContractBit")`
  replacing `join_node`; `Recorder.accept(t_ms, node="")` (device input:
  sends `/game/handshake` with the latest round id it has seen);
  `Recorder.start(t_ms)` (operator start via `request_start(None, TERRARIUM_ADMIN, "recorder")`);
  `Recorder.expect_handshake_out(t_ms)`; `CONTRACT_VERSION = 3`;
  `ALL_SCENARIOS` updated.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export_contract.py (append)
def test_contract_version_is_3():
    from tools.export_contract import CONTRACT_VERSION
    assert CONTRACT_VERSION == 3

# tests/test_contract_scenarios.py (append)
def test_v3_scenario_set():
    from contract_kit.scenarios import ALL_SCENARIOS
    names = {f.__name__ for f in ALL_SCENARIOS}
    assert {"handshake_validate_then_role", "handshake_over_cap_deny",
            "handshake_stale_round", "late_hello_gets_jam",
            "jam_solo_fallback", "room_node_handshake_binds",
            "join_retired_error"} <= names
    assert not names & {"explicit_join_role", "lobby_tap_join"}
```

Adjust `ALL_SCENARIOS`'s element type if it is a dict or list of names
(read `contract_kit/scenarios.py`'s tail).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_export_contract.py tests/test_contract_scenarios.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**
- `ContractBit`: `player` becomes `UNIQUE` capacity 1 (still scored,
  still `requires="rev1"`); add `jammer` (`JAM`, `uses=["tap"]`, a dim
  light manifest) on `CONTRACT_JAM_NODE`. `SoloContractBit`
  (`contract_kit/solo_contract_bit.py`): the same player role, no jam role.
- `Recorder`: replace `join_node` with `handshake` (`{"node": str,
  "ack_after_ms": int}` or `None`). On `link_up` it sends hello only; when
  `handshake` is set it schedules `accept()` at `ack_after_ms` after the
  first `/handshake` it receives. `export` writes the scenario `device`
  field as `{"handshake": ...}`. Remove `_send_join`, `join_now`,
  `expect_join`. Add `accept`, `start`, `expect_handshake_out`
  (asserts the device sent `/game/handshake` at t).
- `scenarios.py`: update the module docstring (v3, the count from
  `len(ALL_SCENARIOS)`); rewrite per spec section 6:
  - `boot_hello_heartbeat`: no `/room` after the first; quiet list
    `["/game/handshake", "/game/tap", *POST_ROLE_GESTURES]`.
  - `handshake_validate_then_role`: link up, `/handshake` seen, accept at
    300 ms, `/validated` seen, start at 1000 ms, `/role` seen, frame after
    `SIGNATURE_SETTLED_MS` past start.
  - `handshake_over_cap_deny`: a second recorded device (or a
    pre-validated phantom dev via `control_send_now`) fills the slot; the
    device accepts, gets `/deny ["scored full", <hint>]`, then `/role`
    with `class: "jam"` at start.
  - `handshake_stale_round`: the device sends `/game/handshake` with
    round id `"stale"`: nothing comes back; then a correct accept validates.
  - `late_hello_gets_jam`: start first (with no devices), then link up:
    `/role` jam within one tick of the first hello.
  - `jam_solo_fallback`: `bit="SoloContractBit"`, device never accepts,
    start: `/role` with `role` `solo:tuneshroom_rev1` (or the carried
    instrument the recorder declares) and `class` `jam`.
  - `room_node_handshake_binds`: `with_room=True`, arm a fixture, accept
    with the Room node: no `/validated`, no `/role`, fixture frames on
    `/leds`.
  - `join_retired_error`: the device sends `/game/join`: `/error ["join",
    "retired in contract v3: use /game/handshake"]`.
  - `deny_stays_hellod`: accept with `NO_SUCH_NODE`: deny, still hello'd.
  - `link_loss_rejoin`: after link up again: hello, `/handshake`, accept,
    `/validated` (round still SETUP in this scenario).
  - `gestures_after_role`, `timed_frames_hold_last`, `release_keeps_display`,
    `play_known_and_unknown`, `error_no_state_change`, `malformed_dropped`,
    `link_loss_keeps_display`: reach the role through accept + start
    instead of join; intent unchanged.
- `tools/export_contract.py`: `CONTRACT_VERSION = 3`; `step_schema`
  documents `device.handshake` and the `accept` input step; notes list
  the TCP down transports.
- Re-record: `.venv/bin/python -m tools.record_scenarios`; delete the
  two retired recordings.
- `docs/device-contract-guide.md`: fix the header (v3, scenario count),
  rewrite section 5's device rules and section 6 rule 8 around the
  handshake, add the spec section 8 firmware checklist and the wire table
  with the transport column (spec 3.3).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_contract_*.py tests/test_export_contract.py -q` then the full suite `.venv/bin/python -m pytest tests -q` and `node --test tests/js/*.test.js`.
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add contract_kit/ tools/ docs/device-contract-guide.md tests/
git commit -m "feat(contract): contract v3 handshake scenarios and guide"
```

---

### Task 12: Docs and diagrams

**Files:**
- Modify: `docs/diagrams/player-flow.seq`, `docs/diagrams/manifest.json`, `docs/MM_TERRARIUM.md`, `docs/control-gameserver-design.md`
- Create: `docs/diagrams/device-lifecycle.d2`, `docs/diagrams/role-assignment.seq`
- Test: `tests/test_diagrams.py`, `tests/test_render_diagrams.py`

- [ ] **Step 1: Read the pipeline.** `docs/superpowers/specs/2026-08-17-ascii-diagram-pipeline-design.md`,
  `tools/render_diagrams.py --help`, and the existing `player-flow.seq`
  and `lifecycle.d2` for syntax. Note how `manifest.json` maps a source to
  a marker name (`diagram:player-flow`).

- [ ] **Step 2: Write the sources.**
  - `player-flow.seq`: title `Player flow, hello to role (contract v3, 2026-10-01)`;
    lanes Tuneshroom, Arco, Control; messages in order: `/game/hello` (up,
    relayed), `/ie1/room` (first contact only), `/ie1/handshake round`
    (TCP) with the white invite flash `/ie1/leds`, note `user double-taps
    (accept)`, `/game/handshake dev round node`, `/ie1/validated round role`,
    green ceremony `/ie1/leds` and `/ie1/play chime key=<midi>`, note
    `start (timer, operator, admin, /game/start)`, `/ie1/role scored` (TCP),
    gameplay `/game/tap`, `/ie1/leds at = origin + cue_horizon`, `/ie1/release`.
    Notes: `over cap: /ie1/deny "scored full" then jam role at start`,
    `stale round: dropped`.
  - `device-lifecycle.d2`: nodes CONNECTED, INVITED, VALIDATED, SCORED,
    JAM; edges per spec 3.1 including `hello in RUNNING` CONNECTED to JAM,
    `reaped` VALIDATED to CONNECTED (label `slot frees`), `deny` INVITED
    to INVITED, `round ends (fade, release)` SCORED and JAM to CONNECTED.
  - `role-assignment.seq`: lanes Control, Bit, Agent, Device; `run()`,
    `materialize`, per scored dev `on_join`, `on_grant`, `/role scored`;
    per other dev `jam role (Bit JAM or solo:<instrument>)`, `on_join`,
    `/role jam`; `on_run_start`.
  - `manifest.json`: add both new entries following the existing shape.

- [ ] **Step 3: Render and inject.** Run `.venv/bin/python -m tools.render_diagrams`
  (check `--help` for the inject flag). Add the two new marker pairs in
  `docs/MM_TERRARIUM.md`: `device-lifecycle` and `role-assignment` under
  *Lobby and join handshake*; `player-flow` stays where it is.

- [ ] **Step 4: Prose.** In `docs/MM_TERRARIUM.md` rewrite:
  - *Lobby and join handshake* → "Lobby and the handshake": spec 3.1-3.7
    in the doc's terse style (states, verbs, cap, Room nodes, materialize,
    jam and Solo, ceremony survives a fast start, round id).
  - *Message vocabulary*: up/down lists per contract v3; add "down rows
    carry a transport: tcp for role, deny, release, room, error,
    handshake, validated; udp for leds, play".
  - *Join, release and overrides* → "Grants, release and overrides":
    grants happen at RUNNING through `on_grant`; no role switch.
  - *Start conditions and profiles*: one decider module; the timer start
    goes through `request_start(source="timer")` and gets the accept flash.
  - *The device contract*: `CONTRACT_VERSION` 3 and what 3 added.
  - *`bits/`*: TestBit `player` UNIQUE capacity 1; SoloTestBit.
  - *`harness/`*: o2_shroom `--handshake`/`--handshake-delay`, no
    `--join-retry`; `run_stack --expect-scored`; new markers.
  - *Relationships*, mm-devshroom: replace the join-resend paragraph with
    the v3 checklist pointer and the fact that `origin/main` firmware
    joins once per link and will get jam until updated.
  - the intro diagram caption `Player flow, hello to complete (lobby
    handshake, 2026-09-11)` is regenerated from the source title.
  `docs/control-gameserver-design.md`: one "Superseded (2026-10-01)" note
  at the top of its registration section linking the new spec.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_diagrams.py tests/test_render_diagrams.py -q`
Expected: PASS. Grep the edited docs for em dashes: `grep -n "—" docs/MM_TERRARIUM.md docs/device-contract-guide.md` must print nothing new.

- [ ] **Step 6: Commit**

```bash
git add docs/
git commit -m "docs: handshake protocol flow, diagrams and deep-dive (contract v3)"
```

---

### Task 13: mm-tuneshroom paired change

Runs in mm-tuneshroom, on its own branch and worktree. Never touch its
`main`. Depends on Task 11's export.

**Files (mm-tuneshroom):**
- Modify: `lib/host/device_session.dart`, `lib/link/envelope.dart`, `lib/host/session_snapshot.dart`, `lib/sim/sim_app.dart`, `lib/sim/node_panel.dart`, `test/contract/` (re-export), its contract replay test, the session unit tests under `test/`
- Modify: its deep-dive (find it: `docs/MM_TUNESHROOM.md` in-repo, else `mm-documents/services/MM_TUNESHROOM.md`; look before assuming)

**Interfaces:**
- Produces in Dart: `DeviceSession.acceptHandshake({String? node})`;
  `SessionSnapshot.handshakePending` (bool), `.validatedRole` (String?);
  `joinNode` and `join()` removed.

- [ ] **Step 1: Set up.** `git -C ~/projects/mm-tuneshroom fetch origin`,
  then `git -C ~/projects/mm-tuneshroom worktree add ../mm-tuneshroom-handshake -b claude/instrument-handshake-v3 origin/main`
  (check `git worktree list` first; reuse if it exists). Read its
  `CLAUDE.md`/README for its test commands (`flutter test`; do not guess
  flags).

- [ ] **Step 2: Re-export the contract.** From this worktree:
  `.venv/bin/python -m tools.export_contract ../mm-tuneshroom-handshake/test/contract`
  (check the tool's `--help` for the exact out-dir argument). Run its
  contract replay test; expect FAIL on the new scenarios.

- [ ] **Step 3: Write failing session tests** (Dart, in the session test
  file that already covers `join`):

```dart
test('handshake then accept sends /game/handshake with the round id', () {
  final s = DeviceSession(dev: 'd1');
  s.linkUp('');
  s.takeEffects();
  s.onMessage(Envelope.fromJson({'address': '/d1/handshake', 'typespec': 's', 'args': ['r1'], 'timestamp': 0.0}));
  expect(s.snapshot.handshakePending, isTrue);
  s.acceptHandshake();
  final sent = s.takeEffects().whereType<SendEffect>().map((e) => e.envelope).toList();
  expect(sent.single.address, '/game/handshake');
  expect(sent.single.args, ['d1', 'r1', '']);
});

test('linkUp sends hello only, never /game/join', () {
  final s = DeviceSession(dev: 'd1');
  s.linkUp('');
  final addrs = s.takeEffects().whereType<SendEffect>().map((e) => e.envelope.address);
  expect(addrs, ['/game/hello']);
});

test('validated clears the prompt', () {
  final s = DeviceSession(dev: 'd1');
  s.linkUp('');
  s.onMessage(Envelope.fromJson({'address': '/d1/handshake', 'typespec': 's', 'args': ['r1'], 'timestamp': 0.0}));
  s.onMessage(Envelope.fromJson({'address': '/d1/validated', 'typespec': 'ss', 'args': ['r1', 'player'], 'timestamp': 0.0}));
  expect(s.snapshot.handshakePending, isFalse);
  expect(s.snapshot.validatedRole, 'player');
});
```

Match `Envelope.fromJson`'s real key names and `SendEffect`'s field name
by reading `lib/link/envelope.dart` and `lib/host/session_effect.dart`.

- [ ] **Step 4: Implement.**
  - `DeviceSession`: remove `joinNode` (constructor param and field) and
    `join()`; `linkUp` sends hello only. Add `_roundId`, `_validatedRole`;
    `onMessage` handles `handshake` (store round id, unless a role is
    held) and `validated` (store role, clear `_roundId`); `deny` clears
    `_roundId`. `acceptHandshake({String? node})` sends
    `Envelope.game('handshake', 'sss', [dev, _roundId, node ?? ''])` when
    `_roundId != null`. A non-rev1 double tap (`g.count >= 2`) while
    `_roundId != null` and no role also accepts. Rev 1 profile: two
    count-1 taps within 1500 ms while pending accept (the board pairs
    taps itself under v3). `linkDown` clears `_roundId` and `_validatedRole`.
  - Snapshot: add `handshakePending` and `validatedRole`.
  - `lib/link/envelope.dart`: anything that enumerates device verbs gains
    `handshake`/`validated` (mirror `devicelink/protocol.py`).
  - Simulator: an **Accept** button enabled while
    `snapshot.handshakePending`; `node_panel.dart` calls
    `acceptHandshake(node: node)` instead of `join(node)` (this also
    covers Room fixture binding); update its header comment.
  - Contract replay runner: replace `device.join_node` handling with
    `device.handshake` (schedule `acceptHandshake(node)` at
    `ack_after_ms` after the first `/handshake`), and the `accept` input
    step; remove the `join` input step.
  - `bits/GlowBit`: read its role table; if it relies on a SETUP role or
    `/game/join`, adapt to scored-or-jam; otherwise note "unchanged" in
    the PR body.

- [ ] **Step 5: Run its tests.** `flutter analyze` and `flutter test`
  (per its README). Expected: PASS, including the v3 replay.

- [ ] **Step 6: Deep-dive.** Update the session and simulator sections of
  its deep-dive for v3 (follow the `mm-deepdive-sync` skill's flow for
  where and how it is pushed).

- [ ] **Step 7: Commit (in the mm-tuneshroom worktree)**

```bash
git -C ../mm-tuneshroom-handshake add -A
git -C ../mm-tuneshroom-handshake commit -m "feat(session): contract v3 handshake (accept, validated), retire join"
```

---

### Task 14: Whole-system verification and closeout prep

**Files:** none new (fixes only, if verification finds a defect).

- [ ] **Step 1: Full offline suites.** `.venv/bin/python -m pytest tests -q`
  and `node --test tests/js/*.test.js` in this worktree; `flutter test`
  in the mm-tuneshroom worktree. All PASS.

- [ ] **Step 2: Live checks** (Task 10's three commands again, plus
  Rev1Bit: `./smoke-test.sh --bit Rev1Bit --devices 2 --handshake-devices 1 --ci --seconds 30`;
  MinigameBit and CaptureBit: `--list-bits` shows them without errors).

- [ ] **Step 3: Browser check.** `./terrarium.sh --room TEST`, load TestBit
  from the Console, open the guest page with the mm-tuneshroom web build
  (`run_stack --web-build` per `docs/MM_TERRARIUM.md`), press Accept,
  start from the Console, confirm the scored role arrives; a second tab
  that never accepts gets jam.

- [ ] **Step 4: mm-devshroom issue draft.** Write the spec section 8
  checklist as `docs/upstream/2026-10-01-devshroom-handshake-v3-issue.md`
  in this repo (title plus body, ready to paste). Do NOT file it: filing
  on GitHub is the operator's call; surface the draft in the final report.

- [ ] **Step 5: Re-check `main`.** `git fetch origin && git log --oneline HEAD..origin/main`;
  if `main` moved, merge it and re-run Step 1 before any PR.

- [ ] **Step 6: Commit**

```bash
git add docs/upstream/2026-10-01-devshroom-handshake-v3-issue.md
git commit -m "docs(upstream): mm-devshroom handshake v3 issue draft"
```
