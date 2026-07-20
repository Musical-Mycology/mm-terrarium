# Terrarium Uplink Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Control+GameServer (`control/engine.py`'s `GameServer`) remotely drivable and observable over a persistent outbound connection, without making it depend on that connection existing, being up, or ever being used.

**Architecture:** A new `uplink/` package adds a `Transport` abstraction (a `FakeTransport` for tests, a real `WebSocketTransport`) and an `UplinkAgent` that registers as a `GameServer` observer and translates between the wire protocol (`uplink/protocol.py`) and direct `GameServer` calls. `GameServer` itself gains only a small, optional hook surface (`on_state_change`, `on_registration_change`, `abort()`) — it never imports from `uplink/` and stays fully functional with no uplink attached, exactly as it is today.

**Tech Stack:** Python 3.10+, pytest, `websockets` (new dependency, sync client/server API).

## Global Constraints

- Python 3.10+ union-type syntax (`X | None`) throughout, matching the existing codebase.
- Run tests with `python -m pytest tests -v`. All new tests must be hermetic: `FakeTransport`-based tests touch no socket; the one exception is `WebSocketTransport`'s own test suite, which binds a real server to `localhost` on an OS-assigned port (`0`) — no external network, no real fairyring.
- New runtime dependency `websockets>=13` goes in `requirements.txt` (currently empty); `requirements-dev.txt` gets a `-r requirements.txt` line so `python -m pip install -r requirements-dev.txt` (the README's documented install command) still installs everything needed.
- Commit message prefixes follow existing history exactly: `feat:`, `test:`, `fix:`, `docs:` (see `git log --oneline`).
- One-directional dependency: `uplink/` may import from `control/` and `bits/`; `control/` and `bits/` must never import from `uplink/`. `GameServer`/`Bit`/`RegistrationState` stay transport-agnostic.
- `GameServer`'s existing single-callback-attribute convention (`self.on_release = None`, called directly) is the pattern for new hooks too — not a multi-subscriber list.
- `join`/`tick` traffic never crosses the uplink wire protocol (design spec §2/§4) — the protocol module must not grow a join/tick message type.
- Canonical design: `docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md`. Where this plan makes a call the spec left implicit, that's called out inline (see Task 4 and Task 6 notes).

---

### Task 1: Wire protocol schemas

**Files:**
- Create: `uplink/__init__.py`
- Create: `uplink/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Produces: `LoadBitCommand(name: str)`, `RunCommand()`, `AbortCommand()` — dataclasses. `parse_command(msg: dict) -> LoadBitCommand | RunCommand | AbortCommand` (raises `ValueError` on unrecognized/malformed input). `state_changed_event(state_name: str) -> dict`. `registration_changed_event(counts: list[tuple[str, int, int | None]]) -> dict`. `bit_completed_event(result: dict) -> dict`. `error_event(command: str, message: str) -> dict`.
- Consumes: nothing (first task, no dependency on the rest of the repo).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_protocol.py
import pytest

from uplink.protocol import (
    AbortCommand,
    LoadBitCommand,
    RunCommand,
    bit_completed_event,
    error_event,
    parse_command,
    registration_changed_event,
    state_changed_event,
)


def test_parse_load_bit_command():
    cmd = parse_command({"command": "load_bit", "name": "test_bit"})
    assert cmd == LoadBitCommand(name="test_bit")


def test_parse_run_command():
    assert parse_command({"command": "run"}) == RunCommand()


def test_parse_abort_command():
    assert parse_command({"command": "abort"}) == AbortCommand()


def test_parse_load_bit_missing_name_raises():
    with pytest.raises(ValueError, match="requires a string 'name'"):
        parse_command({"command": "load_bit"})


def test_parse_unknown_command_raises():
    with pytest.raises(ValueError, match="unrecognized command"):
        parse_command({"command": "self_destruct"})


def test_state_changed_event_shape():
    assert state_changed_event("RUNNING") == {
        "event": "state_changed", "state": "RUNNING",
    }


def test_registration_changed_event_shape():
    counts = [("player", 2, None), ("conductor", 1, 1)]
    assert registration_changed_event(counts) == {
        "event": "registration_changed",
        "roles": [
            {"role": "player", "count": 2, "capacity": None},
            {"role": "conductor", "count": 1, "capacity": 1},
        ],
    }


def test_bit_completed_event_shape():
    assert bit_completed_event({"score": 42}) == {
        "event": "bit_completed", "result": {"score": 42},
    }


def test_error_event_shape():
    assert error_event("run", "requires SETUP") == {
        "event": "error", "command": "run", "message": "requires SETUP",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_protocol.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'uplink'`

- [ ] **Step 3: Write the implementation**

```python
# uplink/__init__.py
"""Terrarium uplink: makes the Control+GameServer remotely drivable and
observable over a persistent outbound connection, without making it depend
on that connection. See
docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md.
"""
```

```python
# uplink/protocol.py
"""Wire message schemas for the Terrarium uplink -- the JSON-serializable
contract between UplinkAgent and a future fairyring broker. See design spec
section 4.
"""

from dataclasses import dataclass


# --- Down: fairyring -> Terrarium, one dataclass per command ---------------

@dataclass
class LoadBitCommand:
    name: str


@dataclass
class RunCommand:
    pass


@dataclass
class AbortCommand:
    pass


def parse_command(msg: dict):
    """Parse an inbound down-message dict into a command object.

    Raises ValueError for an unrecognized or malformed command.
    """
    command = msg.get("command")
    if command == "load_bit":
        name = msg.get("name")
        if not isinstance(name, str):
            raise ValueError("load_bit requires a string 'name'")
        return LoadBitCommand(name=name)
    if command == "run":
        return RunCommand()
    if command == "abort":
        return AbortCommand()
    raise ValueError(f"unrecognized command: {command!r}")


# --- Up: Terrarium -> fairyring, plain dict builders ------------------------
# (terminal messages -- only ever produced here, never parsed back on this
# side, so a builder function is enough; no dataclass round-trip needed.)

def state_changed_event(state_name: str) -> dict:
    return {"event": "state_changed", "state": state_name}


def registration_changed_event(counts: list[tuple[str, int, int | None]]) -> dict:
    return {
        "event": "registration_changed",
        "roles": [
            {"role": name, "count": count, "capacity": capacity}
            for name, count, capacity in counts
        ],
    }


def bit_completed_event(result: dict) -> dict:
    return {"event": "bit_completed", "result": result}


def error_event(command: str, message: str) -> dict:
    return {"event": "error", "command": command, "message": message}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_protocol.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add uplink/__init__.py uplink/protocol.py tests/test_protocol.py
git commit -m "feat: add uplink wire protocol schemas"
```

---

### Task 2: `RegistrationState.counts()` public accessor

**Files:**
- Modify: `control/registration.py`
- Test: `tests/test_registration.py`

**Interfaces:**
- Consumes: existing `RegistrationState` (`control/registration.py`), `Role`/`RoleTable` (`control/roles.py`).
- Produces: `RegistrationState.counts() -> list[tuple[str, int, int | None]]` — one `(role_name, live_count, capacity)` tuple per role in the Bit's role table, `capacity` is `None` for unlimited roles.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_registration.py`:

```python
def test_counts_reflects_live_registrations_and_capacity():
    reg = RegistrationState(make_table())
    reg.join("ie1", "NODE_PLAYER", State.SETUP)
    reg.join("ie2", "NODE_CONDUCTOR", State.SETUP)

    counts = {name: (count, capacity) for name, count, capacity in reg.counts()}

    assert counts["player"] == (1, None)
    assert counts["conductor"] == (1, 1)
    assert counts["jammer"] == (0, None)
    assert counts["understudy"] == (0, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_registration.py::test_counts_reflects_live_registrations_and_capacity -v`
Expected: FAIL with `AttributeError: 'RegistrationState' object has no attribute 'counts'`

- [ ] **Step 3: Write the implementation**

In `control/registration.py`, add this method to `RegistrationState`, immediately after `release_all`:

```python
    def counts(self) -> list[tuple[str, int, int | None]]:
        """Live per-role (name, count, capacity) snapshot -- the public view
        of _counts for callers outside Control (e.g. the uplink) that need
        registration fill-state without reaching into a private attribute.
        """
        return [
            (role.name, self._counts[role.name], role.capacity)
            for role in self.role_table.roles.values()
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_registration.py -v`
Expected: all passed (including the new test)

- [ ] **Step 5: Commit**

```bash
git add control/registration.py tests/test_registration.py
git commit -m "feat: add RegistrationState.counts() public accessor"
```

---

### Task 3: `Bit.result()` hook

**Files:**
- Modify: `control/bit.py`
- Test: `tests/test_bit.py`

**Interfaces:**
- Consumes: existing `Bit` ABC (`control/bit.py`).
- Produces: `Bit.result() -> dict | None` — default `None`; a subclass overrides it to report a completion payload.

- [ ] **Step 1: Write the failing tests**

Modify `tests/test_bit.py`'s `test_default_hooks_are_no_ops_and_never_complete` to also assert `result()`, and add a new test for an overriding subclass:

```python
def test_default_hooks_are_no_ops_and_never_complete():
    bit = MinimalBit()
    assert bit.on_setup_enter() is None
    assert bit.on_run_start() is None
    assert bit.update(0.1) is False
    assert bit.on_complete() is None
    assert bit.result() is None
    assert bit.on_unload() is None
    assert bit.verb_handlers() == {}


def test_result_can_be_overridden_to_report_a_payload():
    class ScoringBit(MinimalBit):
        def result(self):
            return {"score": 42}

    assert ScoringBit().result() == {"score": 42}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bit.py -v`
Expected: FAIL with `AttributeError: 'MinimalBit' object has no attribute 'result'`

- [ ] **Step 3: Write the implementation**

In `control/bit.py`, add this method to `Bit`, immediately after `on_complete`:

```python
    def result(self) -> dict | None:
        """Optional completion payload (e.g. score/outcome) for the uplink
        to relay upstream once this Bit finishes. Default: nothing to
        report.
        """
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bit.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add control/bit.py tests/test_bit.py
git commit -m "feat: add optional Bit.result() completion-payload hook"
```

---

### Task 4: `GameServer` observer hooks + `abort()`

**Files:**
- Modify: `control/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `State` (`control/state.py`), existing `GameServer`, `RegistrationState.counts()` (Task 2).
- Produces: `GameServer.on_state_change: Callable[[State, State], None] | None` (attribute, default `None`), `GameServer.on_registration_change: Callable[[], None] | None` (attribute, default `None`), `GameServer.abort() -> None` (raises `InvalidTransition` if called while `IDLE`).

**Design note (resolves spec §8 open question 1):** the spec flagged uncertainty about `abort()` being called when `registration` is `None`. Tracing `load_bit()`: it's fully synchronous, and `self.bit`/`self.registration` are always assigned together, before any external caller can observe a non-`IDLE` state. So every reachable non-`IDLE` state already has both set — no guard needed. `abort()` reuses the same best-effort completion path as natural completion (factored into `_run_on_complete`), so `on_complete()`/`on_unload()` both still run on an early exit, per the spec.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine.py`:

```python
def test_on_state_change_fires_for_every_transition():
    server = make_server()
    transitions = []
    server.on_state_change = lambda old, new: transitions.append((old, new))

    server.load_bit("test_bit")
    server.run()
    server.tick(1.0)
    server.tick(1.5)  # crosses TestBit's 2.0s completion threshold

    assert transitions == [
        (State.IDLE, State.LOADING),
        (State.LOADING, State.LOADED),
        (State.LOADED, State.SETUP),
        (State.SETUP, State.RUNNING),
        (State.RUNNING, State.COMPLETING),
        (State.COMPLETING, State.UNLOADING),
        (State.UNLOADING, State.IDLE),
    ]


def test_on_state_change_fires_on_failed_load_bit():
    server = make_server()
    transitions = []
    server.on_state_change = lambda old, new: transitions.append((old, new))

    with pytest.raises(BitLoadError):
        server.load_bit("no_such_bit")

    assert transitions == [
        (State.IDLE, State.LOADING),
        (State.LOADING, State.IDLE),
    ]


def test_on_registration_change_fires_only_on_granted_join():
    server = make_server()
    server.load_bit("test_bit")
    calls = []
    server.on_registration_change = lambda: calls.append(server.registration.counts())

    denied = server.join("ie1", "NO_SUCH_NODE")
    assert denied.granted is False
    assert calls == []

    granted = server.join("ie1", "TEST_PLAYER_NODE")
    assert granted.granted is True
    assert len(calls) == 1
    counts = {name: count for name, count, _capacity in calls[0]}
    assert counts["player"] == 1


def test_abort_requires_active_bit():
    server = make_server()
    with pytest.raises(InvalidTransition):
        server.abort()


def test_abort_from_setup_unloads_and_releases_devices():
    server = make_server()
    released = []
    server.on_release = released.append
    server.hello("ie1", "Tuneshroom 1", "1.0")
    server.load_bit("test_bit")
    server.join("ie1", "TEST_PLAYER_NODE")

    server.abort()

    assert server.state == State.IDLE
    assert server.bit is None
    assert released == ["ie1"]


def test_abort_runs_on_complete_before_unloading():
    server = make_server()
    server.load_bit("test_bit")
    server.run()
    bit = server.bit

    server.abort()

    assert bit._completed is True
    assert server.state == State.IDLE


def test_abort_survives_on_complete_exception():
    server = make_server()
    server.load_bit("exploding_complete_bit")

    server.abort()  # must not raise

    assert server.state == State.IDLE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL — `AttributeError: 'GameServer' object has no attribute 'on_state_change'` (and similar for `on_registration_change`/`abort`)

- [ ] **Step 3: Write the implementation**

Replace the full contents of `control/engine.py`:

```python
"""GameServer: the Control+GameServer's lifecycle orchestrator. Owns the
state machine described in design spec section 3. O2-agnostic by design --
callers (a future O2lite transport layer) drive it through hello/load_bit/
run/join/tick and observe device releases via on_release. Also observable
by the Terrarium uplink
(docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md) via
on_state_change/on_registration_change, and remotely abortable via
abort() -- GameServer stays agnostic to who's watching or calling either.
"""

import logging

from control.bit import Bit
from control.device_pool import DevicePool
from control.registration import JoinResult, RegistrationState
from control.state import State

logger = logging.getLogger(__name__)


class InvalidTransition(Exception):
    """Raised when a trigger is called from a state that doesn't allow it."""


class BitLoadError(Exception):
    """Raised when load_bit fails to construct the named Bit."""


class GameServer:
    def __init__(self, bit_registry: dict):
        self.bit_registry = bit_registry
        self.state = State.IDLE
        self.devices = DevicePool()
        self.bit: Bit | None = None
        self.registration: RegistrationState | None = None
        # Set by a transport layer: called once per device released during
        # UNLOADING, so it can send that device's /ie<N>/release message.
        self.on_release = None
        # Set by UplinkAgent: called with (old_state, new_state) on every
        # state transition.
        self.on_state_change = None
        # Set by UplinkAgent: called with no arguments after a join grants a
        # role. Callers read self.registration.counts() for the snapshot.
        self.on_registration_change = None

    def hello(self, dev: str, name: str, protoversion: str) -> None:
        self.devices.hello(dev, name, protoversion)

    def load_bit(self, name: str) -> None:
        if self.state != State.IDLE:
            raise InvalidTransition(
                f"load_bit requires IDLE, current state is {self.state}")
        self._set_state(State.LOADING)
        try:
            bit_cls = self.bit_registry[name]
            bit = bit_cls()
        except Exception as exc:
            self._set_state(State.IDLE)
            raise BitLoadError(f"failed to load Bit {name!r}: {exc}") from exc
        self.bit = bit
        self.registration = RegistrationState(bit.role_table)
        self._set_state(State.LOADED)
        self._enter_setup()

    def _enter_setup(self) -> None:
        self._set_state(State.SETUP)
        self.bit.on_setup_enter()

    def run(self) -> None:
        if self.state != State.SETUP:
            raise InvalidTransition(
                f"run requires SETUP, current state is {self.state}")
        self._set_state(State.RUNNING)
        self.bit.on_run_start()

    def join(self, dev: str, node: str) -> JoinResult:
        if self.state not in (State.SETUP, State.RUNNING):
            return JoinResult(granted=False,
                               reason="no Bit accepting registrations")
        result = self.registration.join(dev, node, self.state)
        if result.granted and self.on_registration_change:
            self.on_registration_change()
        return result

    def tick(self, dt: float) -> None:
        if self.state != State.RUNNING:
            return
        if self.bit.update(dt):
            self._complete()

    def abort(self) -> None:
        """Force an early end to the current Bit from any non-IDLE state.
        Runs the same best-effort on_complete/on_unload cleanup as a normal
        completion, then unloads. Safe from LOADING/LOADED/SETUP/RUNNING/
        COMPLETING/UNLOADING -- load_bit() is fully synchronous, so
        self.bit and self.registration are always set together by the time
        any external caller can observe a non-IDLE state.
        """
        if self.state == State.IDLE:
            raise InvalidTransition("abort requires an active Bit")
        self._run_on_complete()
        self._unload()

    def _complete(self) -> None:
        self._set_state(State.COMPLETING)
        self._run_on_complete()
        self._unload()

    def _run_on_complete(self) -> None:
        try:
            self.bit.on_complete()
        except Exception:
            logger.exception("Bit.on_complete raised; unloading anyway")

    def _unload(self) -> None:
        self._set_state(State.UNLOADING)
        released = self.registration.release_all()
        if self.on_release:
            for dev in released:
                self.on_release(dev)
        try:
            self.bit.on_unload()
        except Exception:
            logger.exception("Bit.on_unload raised; returning to IDLE anyway")
        self.bit = None
        self.registration = None
        self._set_state(State.IDLE)

    def _set_state(self, new_state: State) -> None:
        old_state = self.state
        self.state = new_state
        if self.on_state_change:
            self.on_state_change(old_state, new_state)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_engine.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add control/engine.py tests/test_engine.py
git commit -m "feat: add GameServer observer hooks and abort()"
```

---

### Task 5: `Transport` protocol + `FakeTransport`

**Files:**
- Create: `uplink/transport.py`
- Test: `tests/test_transport.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Transport` (a `typing.Protocol`: `connected: bool`, `connect() -> None`, `send(msg: dict) -> None`, `receive() -> dict | None`). `FakeTransport()` implementing it, plus test-only helpers `disconnect() -> None`, `push_incoming(msg: dict) -> None`, and public state `sent: list[dict]`, `connect_count: int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_transport.py
from uplink.transport import FakeTransport


def test_fake_transport_starts_disconnected():
    t = FakeTransport()
    assert t.connected is False


def test_connect_sets_connected_and_counts_calls():
    t = FakeTransport()
    t.connect()
    assert t.connected is True
    assert t.connect_count == 1


def test_receive_returns_none_when_empty():
    t = FakeTransport()
    assert t.receive() is None


def test_push_incoming_then_receive_fifo_order():
    t = FakeTransport()
    t.push_incoming({"command": "run"})
    t.push_incoming({"command": "abort"})
    assert t.receive() == {"command": "run"}
    assert t.receive() == {"command": "abort"}
    assert t.receive() is None


def test_send_records_sent_messages():
    t = FakeTransport()
    t.send({"event": "state_changed", "state": "RUNNING"})
    assert t.sent == [{"event": "state_changed", "state": "RUNNING"}]


def test_disconnect_clears_connected_flag():
    t = FakeTransport()
    t.connect()
    t.disconnect()
    assert t.connected is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_transport.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'uplink.transport'`

- [ ] **Step 3: Write the implementation**

```python
# uplink/transport.py
"""Transport implementations for the uplink -- the only code that touches
an actual socket. See design spec section 3.
"""

from collections import deque
from typing import Protocol


class Transport(Protocol):
    """What UplinkAgent needs from a connection to fairyring. Non-blocking:
    receive() returns None immediately when there's nothing waiting.
    """

    connected: bool

    def connect(self) -> None:
        """Establish (or re-establish) the connection."""

    def send(self, msg: dict) -> None:
        """Send one message. Callers are expected to check `connected`
        first -- behavior when disconnected is implementation-defined."""

    def receive(self) -> dict | None:
        """Return the next queued inbound message, or None if none
        waiting."""


class FakeTransport:
    """In-process test double. No socket, no server process -- tests push
    inbound messages via `push_incoming` and inspect `sent`.
    """

    def __init__(self):
        self.connected = False
        self.sent: list[dict] = []
        self._incoming: deque[dict] = deque()
        self.connect_count = 0

    def connect(self) -> None:
        self.connected = True
        self.connect_count += 1

    def disconnect(self) -> None:
        """Test helper: simulate the link dropping."""
        self.connected = False

    def send(self, msg: dict) -> None:
        self.sent.append(msg)

    def receive(self) -> dict | None:
        if not self._incoming:
            return None
        return self._incoming.popleft()

    def push_incoming(self, msg: dict) -> None:
        """Test helper: queue a message as if it arrived from fairyring."""
        self._incoming.append(msg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_transport.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add uplink/transport.py tests/test_transport.py
git commit -m "feat: add Transport protocol and FakeTransport test double"
```

---

### Task 6: `UplinkAgent` — command dispatch + event emission

**Files:**
- Create: `uplink/link.py`
- Test: `tests/test_link.py`

**Interfaces:**
- Consumes: `GameServer` incl. `on_state_change`/`on_registration_change`/`abort()` (Task 4), `RegistrationState.counts()` (Task 2), `Bit.result()` (Task 3), `uplink.protocol` (Task 1), `Transport`/`FakeTransport` (Task 5).
- Produces: `UplinkAgent(game_server: GameServer, transport)`, `.poll() -> None` (drains and handles queued inbound commands; no-op while disconnected).

**Design note (fills in spec §4's "once, when entering COMPLETING"):** `Bit.on_complete()` — where a Bit computes its score — runs *after* the `COMPLETING` state-change fires but *before* `UNLOADING` fires (see Task 4's `_complete`/`_unload`). Emitting `bit_completed` on `COMPLETING` would report a result computed before scoring happens. This task instead emits it on the `UNLOADING` transition, where `on_complete()` has already run and `game_server.bit` is still set (it's cleared later in the same `_unload()` call) — the earliest point where `bit.result()` is both meaningful and available. This also means `abort()` (Task 4) gets `bit_completed` reporting for free, matching the spec's "best-effort scoring/cleanup on early exit."

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_link.py
from bits.test_bit import TestBit
from control.engine import GameServer
from uplink.link import UplinkAgent
from uplink.transport import FakeTransport

REGISTRY = {"test_bit": TestBit}


def make_agent():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport)
    transport.connect()
    return agent, server, transport


def test_construction_registers_as_game_server_observer():
    server = GameServer(bit_registry=REGISTRY)
    UplinkAgent(server, FakeTransport())
    assert server.on_state_change is not None
    assert server.on_registration_change is not None


def test_poll_does_nothing_when_disconnected():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport)
    transport.push_incoming({"command": "run"})

    agent.poll()  # never connected

    assert server.state.name == "IDLE"


def test_load_bit_command_drives_game_server():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "load_bit", "name": "test_bit"})

    agent.poll()

    assert server.state.name == "SETUP"


def test_run_command_drives_game_server():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    transport.push_incoming({"command": "run"})

    agent.poll()

    assert server.state.name == "RUNNING"


def test_abort_command_drives_game_server():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    transport.push_incoming({"command": "abort"})

    agent.poll()

    assert server.state.name == "IDLE"


def test_invalid_command_sends_error_event_without_raising():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "run"})  # requires SETUP; server is IDLE

    agent.poll()  # must not raise

    errors = [m for m in transport.sent if m["event"] == "error"]
    assert len(errors) == 1
    assert errors[0]["command"] == "run"


def test_unparseable_message_is_dropped_not_raised():
    agent, server, transport = make_agent()
    transport.push_incoming({"command": "self_destruct"})

    agent.poll()  # must not raise

    assert server.state.name == "IDLE"
    assert transport.sent == []


def test_state_changes_are_sent_as_events():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")

    events = [m["state"] for m in transport.sent if m["event"] == "state_changed"]
    assert events == ["LOADING", "LOADED", "SETUP"]


def test_registration_changes_are_sent_as_events():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    transport.sent.clear()

    server.join("ie1", "TEST_PLAYER_NODE")

    reg_events = [m for m in transport.sent if m["event"] == "registration_changed"]
    assert len(reg_events) == 1
    roles = {r["role"]: r["count"] for r in reg_events[0]["roles"]}
    assert roles["player"] == 1


def test_bit_completed_sent_at_unload_when_result_present():
    class ScoringBit(TestBit):
        def result(self):
            return {"score": 99}

    server = GameServer(bit_registry={"scoring_bit": ScoringBit})
    transport = FakeTransport()
    UplinkAgent(server, transport)
    transport.connect()

    server.load_bit("scoring_bit")
    server.run()
    server.tick(3.0)  # crosses TestBit's default 2.0s completion threshold

    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert completed == [{"event": "bit_completed", "result": {"score": 99}}]


def test_no_bit_completed_event_when_result_is_none():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    server.run()
    server.tick(3.0)

    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []


def test_events_not_sent_while_disconnected():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    UplinkAgent(server, transport)
    # never connected

    server.load_bit("test_bit")

    assert transport.sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_link.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'uplink.link'`

- [ ] **Step 3: Write the implementation**

```python
# uplink/link.py
"""UplinkAgent: translates between the wire protocol and GameServer calls.
See design spec sections 3-5.
"""

import logging

from control.engine import BitLoadError, GameServer, InvalidTransition
from control.state import State
from uplink import protocol

logger = logging.getLogger(__name__)


class UplinkAgent:
    def __init__(self, game_server: GameServer, transport):
        self.game_server = game_server
        self.transport = transport
        game_server.on_state_change = self._on_state_change
        game_server.on_registration_change = self._on_registration_change

    def poll(self) -> None:
        """Drain and handle any inbound commands. Call once per tick-loop
        iteration, alongside GameServer.tick() -- independent of it."""
        if not self.transport.connected:
            return
        while True:
            msg = self.transport.receive()
            if msg is None:
                return
            self._handle_message(msg)

    def _handle_message(self, msg: dict) -> None:
        try:
            command = protocol.parse_command(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable uplink message: %s", exc)
            return
        self._dispatch(msg.get("command"), command)

    def _dispatch(self, command_name: str, command) -> None:
        try:
            if isinstance(command, protocol.LoadBitCommand):
                self.game_server.load_bit(command.name)
            elif isinstance(command, protocol.RunCommand):
                self.game_server.run()
            elif isinstance(command, protocol.AbortCommand):
                self.game_server.abort()
        except (InvalidTransition, BitLoadError) as exc:
            self._send(protocol.error_event(command_name, str(exc)))

    def _on_state_change(self, old_state: State, new_state: State) -> None:
        self._send(protocol.state_changed_event(new_state.name))
        if new_state == State.UNLOADING:
            self._send_bit_completed()

    def _send_bit_completed(self) -> None:
        bit = self.game_server.bit
        if bit is None:
            return
        result = bit.result()
        if result is not None:
            self._send(protocol.bit_completed_event(result))

    def _on_registration_change(self) -> None:
        counts = self.game_server.registration.counts()
        self._send(protocol.registration_changed_event(counts))

    def _send(self, msg: dict) -> None:
        if self.transport.connected:
            self.transport.send(msg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_link.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add uplink/link.py tests/test_link.py
git commit -m "feat: add UplinkAgent command dispatch and event emission"
```

---

### Task 7: `UplinkAgent` — reconnect with backoff + resync

**Files:**
- Modify: `uplink/link.py`
- Test: `tests/test_link.py`

**Interfaces:**
- Consumes: `UplinkAgent` (Task 6).
- Produces: `UplinkAgent.__init__` gains a keyword-only `time_source: Callable[[], float] = time.monotonic` parameter. `UplinkAgent.maintain_connection() -> None` — attempts to (re)connect on an exponential-backoff schedule; on successful (re)connect, sends a full resync (current state, plus current registration snapshot if a Bit is loaded).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_link.py`:

```python
class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FlakyTransport(FakeTransport):
    def __init__(self, fail_times: int):
        super().__init__()
        self._fail_times = fail_times

    def connect(self) -> None:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise ConnectionError("no route")
        super().connect()


def test_maintain_connection_connects_immediately_when_disconnected():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock())

    agent.maintain_connection()

    assert transport.connected is True
    assert transport.connect_count == 1


def test_maintain_connection_is_a_noop_when_already_connected():
    agent, server, transport = make_agent()  # helper already connects once
    agent.maintain_connection()
    assert transport.connect_count == 1


def test_reconnect_sends_resync_snapshot():
    agent, server, transport = make_agent()
    server.load_bit("test_bit")
    server.join("ie1", "TEST_PLAYER_NODE")
    transport.disconnect()
    transport.sent.clear()

    agent.maintain_connection()

    assert transport.sent[0] == {"event": "state_changed", "state": "SETUP"}
    reg_event = transport.sent[1]
    assert reg_event["event"] == "registration_changed"
    roles = {r["role"]: r["count"] for r in reg_event["roles"]}
    assert roles["player"] == 1


def test_resync_omits_registration_snapshot_when_no_bit_loaded():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, time_source=FakeClock())

    agent.maintain_connection()

    assert transport.sent == [{"event": "state_changed", "state": "IDLE"}]


def test_failed_connect_backs_off_before_retrying():
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = FlakyTransport(fail_times=1)
    agent = UplinkAgent(server, transport, time_source=clock)

    agent.maintain_connection()  # fails, schedules retry at t=1.0
    assert transport.connected is False

    clock.advance(0.5)
    agent.maintain_connection()  # too soon (0.5s < 1.0s backoff)
    assert transport.connected is False

    clock.advance(0.6)  # total 1.1s elapsed -- past the 1.0s backoff
    agent.maintain_connection()
    assert transport.connected is True


def test_backoff_doubles_on_repeated_failures():
    clock = FakeClock()
    server = GameServer(bit_registry=REGISTRY)
    transport = FlakyTransport(fail_times=2)
    agent = UplinkAgent(server, transport, time_source=clock)

    agent.maintain_connection()  # fail 1, next attempt scheduled at t=1.0
    clock.advance(1.0)
    agent.maintain_connection()  # fail 2, next attempt scheduled at t=3.0
    assert transport.connected is False

    clock.advance(1.9)  # t=2.9, still short of 3.0
    agent.maintain_connection()
    assert transport.connected is False

    clock.advance(0.2)  # t=3.1
    agent.maintain_connection()
    assert transport.connected is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_link.py -v`
Expected: FAIL — `TypeError: UplinkAgent.__init__() got an unexpected keyword argument 'time_source'`

- [ ] **Step 3: Write the implementation**

In `uplink/link.py`, add `import time` to the top-level imports, then replace `UplinkAgent.__init__` and add the new methods:

```python
import logging
import time

from control.engine import BitLoadError, GameServer, InvalidTransition
from control.state import State
from uplink import protocol

logger = logging.getLogger(__name__)


class UplinkAgent:
    INITIAL_BACKOFF_SECONDS = 1.0
    MAX_BACKOFF_SECONDS = 30.0

    def __init__(self, game_server: GameServer, transport, *,
                 time_source=time.monotonic):
        self.game_server = game_server
        self.transport = transport
        self._time_source = time_source
        self._next_attempt_at = 0.0
        self._backoff = self.INITIAL_BACKOFF_SECONDS
        game_server.on_state_change = self._on_state_change
        game_server.on_registration_change = self._on_registration_change

    def maintain_connection(self) -> None:
        """Call once per tick-loop iteration, alongside poll(). Attempts to
        (re)connect on a backoff schedule; never blocks or raises to the
        caller if an attempt fails."""
        if self.transport.connected:
            return
        now = self._time_source()
        if now < self._next_attempt_at:
            return
        try:
            self.transport.connect()
        except Exception:
            logger.warning("uplink connect failed; retrying in %.1fs",
                            self._backoff)
            self._next_attempt_at = now + self._backoff
            self._backoff = min(self._backoff * 2, self.MAX_BACKOFF_SECONDS)
            return
        self._backoff = self.INITIAL_BACKOFF_SECONDS
        self._next_attempt_at = 0.0
        self._send_resync()

    def _send_resync(self) -> None:
        self._send(protocol.state_changed_event(self.game_server.state.name))
        if self.game_server.registration is not None:
            counts = self.game_server.registration.counts()
            self._send(protocol.registration_changed_event(counts))
```

The rest of the class (`poll`, `_handle_message`, `_dispatch`, `_on_state_change`, `_send_bit_completed`, `_on_registration_change`, `_send`) is unchanged from Task 6.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_link.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add uplink/link.py tests/test_link.py
git commit -m "feat: add UplinkAgent reconnect-with-backoff and resync"
```

---

### Task 8: `WebSocketTransport` (real socket) + dependency wiring

**Files:**
- Modify: `uplink/transport.py`
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`
- Test: `tests/test_websocket_transport.py`

**Interfaces:**
- Consumes: the `Transport` shape defined in Task 5 (structural/duck-typed — no formal inheritance needed since it's a `Protocol`).
- Produces: `WebSocketTransport(uri: str)` with `.connected: bool`, `.connect() -> None` (raises `OSError` if nothing is listening), `.send(msg: dict) -> None`, `.receive() -> dict | None`.

- [ ] **Step 1: Add the dependency**

Write `requirements.txt`:

```
# Runtime dependencies for the Control+GameServer / Terrarium uplink.
websockets>=13
```

Modify `requirements-dev.txt` to pull in the runtime deps too:

```
# Development dependencies for the Control+GameServer test suite.
#
# Install into a venv:
#     python -m pip install -r requirements-dev.txt
#
# Run the offline test suite:
#     python -m pytest tests -v
-r requirements.txt
pytest>=8.0
```

Install it into the dev environment:

```bash
python -m pip install -r requirements-dev.txt
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_websocket_transport.py
import json
import threading

import pytest
from websockets.sync.server import serve

from uplink.transport import WebSocketTransport


@pytest.fixture
def echo_server():
    """A local websocket server bound to an OS-assigned localhost port.
    Records every message it receives and echoes it straight back.
    """
    received = []

    def handler(connection):
        for raw in connection:
            received.append(json.loads(raw))
            connection.send(raw)

    server = serve(handler, "localhost", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    try:
        yield f"ws://localhost:{port}", received
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_connect_sets_connected(echo_server):
    uri, _received = echo_server
    transport = WebSocketTransport(uri)

    transport.connect()

    assert transport.connected is True


def test_send_reaches_the_server(echo_server):
    uri, received = echo_server
    transport = WebSocketTransport(uri)
    transport.connect()

    transport.send({"command": "run"})

    assert transport.receive() == {"command": "run"}  # echoed back
    assert received == [{"command": "run"}]


def test_receive_returns_none_when_nothing_waiting(echo_server):
    uri, _received = echo_server
    transport = WebSocketTransport(uri)
    transport.connect()

    assert transport.receive() is None


def test_receive_returns_none_when_never_connected():
    transport = WebSocketTransport("ws://localhost:1")
    assert transport.receive() is None


def test_connect_raises_when_no_server_listening():
    transport = WebSocketTransport("ws://localhost:1")  # nothing bound there
    with pytest.raises(OSError):
        transport.connect()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_websocket_transport.py -v`
Expected: FAIL — `ImportError: cannot import name 'WebSocketTransport' from 'uplink.transport'`

- [ ] **Step 4: Write the implementation**

Append to `uplink/transport.py` (add `import json` and `from websockets.exceptions import ConnectionClosed` and `from websockets.sync.client import connect as ws_connect` to the top of the file, alongside the existing imports):

```python
import json
from collections import deque
from typing import Protocol

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect
```

Then append this class at the end of `uplink/transport.py`:

```python
class WebSocketTransport:
    """Persistent outbound websocket connection, Terrarium-initiated (venue
    boxes are NAT'd -- this always dials out, never listens). Synchronous
    API to match this codebase's plain-tick-loop style (see
    control/engine.py); receive() uses a zero-timeout recv so it never
    blocks the caller's loop.
    """

    def __init__(self, uri: str):
        self.uri = uri
        self.connected = False
        self._ws = None

    def connect(self) -> None:
        self._ws = ws_connect(self.uri)
        self.connected = True

    def send(self, msg: dict) -> None:
        if not self.connected:
            raise RuntimeError("send() called while disconnected")
        try:
            self._ws.send(json.dumps(msg))
        except ConnectionClosed:
            self.connected = False
            raise

    def receive(self) -> dict | None:
        if not self.connected:
            return None
        try:
            raw = self._ws.recv(timeout=0)
        except TimeoutError:
            return None
        except ConnectionClosed:
            self.connected = False
            return None
        return json.loads(raw)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_websocket_transport.py -v`
Expected: all passed

Then run the full suite to confirm nothing else broke:

Run: `python -m pytest tests -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add uplink/transport.py requirements.txt requirements-dev.txt tests/test_websocket_transport.py
git commit -m "feat: add WebSocketTransport and websockets dependency"
```

---

### Task 9: README update

**Files:**
- Modify: `README.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update the "Planned layout" tree**

In `README.md`, find:

```
control/     Control+GameServer package (Python, on pyarco)
bits/        Bit plugin modules (role tables, graph-builders, cues, scoring)
arcoserver/  Arco server build config for the Terrarium (dspmanifest.txt, prefs)
www/         deployed web root (simulator build ships here from mm-tuneshroom)
deploy/      venue provisioning and installation networking
docs/        repo docs; specs under docs/superpowers/specs/
```

Replace with:

```
control/     Control+GameServer package (Python, on pyarco)
bits/        Bit plugin modules (role tables, graph-builders, cues, scoring)
uplink/      Remote command/telemetry link to a future mm-fairyring broker
arcoserver/  Arco server build config for the Terrarium (dspmanifest.txt, prefs)
www/         deployed web root (simulator build ships here from mm-tuneshroom)
deploy/      venue provisioning and installation networking
docs/        repo docs; specs under docs/superpowers/specs/
```

- [ ] **Step 2: Update the landed-slice paragraph**

Find:

```
`control/` and `bits/` now hold the first implementation slice: the
Control+GameServer lifecycle engine (state machine, role/registration data
model) and `TestBit`, a durable reference fixture. It runs entirely offline
with no O2/Arco/pyarco dependency yet — see
`docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md`
for scope and rationale. Run the test suite with:
```

Replace with:

```
`control/` and `bits/` hold the first implementation slice: the
Control+GameServer lifecycle engine (state machine, role/registration data
model) and `TestBit`, a durable reference fixture. `uplink/` adds a
`GameServer` observer (`UplinkAgent`) that makes that engine remotely
drivable over a persistent outbound websocket, tested against a fake
in-process transport plus a real local socket — see
`docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md`
and `docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md` for
scope and rationale. Both run entirely offline in tests, with no O2/Arco/
pyarco/fairyring dependency. Run the test suite with:
```

- [ ] **Step 3: Update the mm-fairyring relationship bullet**

Find:

```
- **mm-fairyring** (planned): cloud broker for RenQuest integration; the
  Terrarium's uplink module talks outbound to it, never in the real-time
  loop.
```

Replace with:

```
- **mm-fairyring** (planned): cloud broker for RenQuest integration. This
  repo's `uplink/` module (the Terrarium-side half) is implemented and
  talks outbound over a websocket, never in the real-time loop; the
  broker itself doesn't exist yet.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: point README at the landed Terrarium uplink slice"
```

---

## Self-Review Notes

- **Spec coverage:** §3 architecture → Tasks 4–6. §4 wire protocol (down/up/resync) → Tasks 1, 6, 7. §5 connection handling (backoff, drop-untouched) → Task 7 (backoff) and Task 4 (`GameServer` never depends on the link, verified by every engine test running with no transport attached at all). §6 testing (`FakeTransport` primary, no real server needed for logic) → Tasks 1–7; the one real-socket test is scoped narrowly to Task 8, matching the spec's "the only part that touches an actual socket." §7 rationale bullets → each is reflected in a task's implementation choice (observer hook in Task 4, websocket transport in Task 8, lifecycle+registration-only events in Task 6, disconnect-untouched in Task 4/6, `FakeTransport`-only in Task 5, `Bit.result()` as a new hook in Task 3). §8 open questions: #1 resolved in Task 4's design note; #2–#4 remain genuinely open (backoff constants are implementation choices made in Task 7; auth/identity and multi-Bit are out of scope, unaffected by this plan).
- **Placeholder scan:** none found — every step has runnable code and exact commands.
- **Type consistency:** `RegistrationState.counts() -> list[tuple[str, int, int | None]]` (Task 2) matches its consumption in `protocol.registration_changed_event` (Task 1) and in `UplinkAgent._on_registration_change`/`_send_resync` (Tasks 6–7). `Bit.result() -> dict | None` (Task 3) matches `UplinkAgent._send_bit_completed` (Task 6). `GameServer.on_state_change`/`on_registration_change` attribute names (Task 4) match exactly what `UplinkAgent.__init__` assigns (Task 6).
