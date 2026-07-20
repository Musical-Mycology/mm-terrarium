# Control+GameServer First Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Control+GameServer's Bit-launching engine — the pure-Python lifecycle state machine (IDLE→LOADING→LOADED→SETUP→RUNNING→COMPLETING→UNLOADING→IDLE), role/registration data model, and a durable `TestBit` fixture — fully testable offline, with zero dependency on O2, pyarco, or a live Arco server.

**Architecture:** Seven small modules under `control/` (state, roles, bit interface, device pool, registration, engine) plus `bits/test_bit.py`. The `GameServer` engine class in `control/engine.py` is the orchestrator: it owns the state machine and is driven entirely through a plain Python API (`hello`, `load_bit`, `run`, `join`, `tick`). It knows nothing about O2 — a future transport-layer slice will call these methods from real O2lite message handlers.

**Tech Stack:** Python 3.10+ (stdlib only for runtime code), pytest for tests.

## Global Constraints

- Python 3.10+ (uses `X | None` union syntax and `dict[str, X]` generics — both need 3.10+; local dev machines run 3.14, so this floor is conservative, not aspirational).
- No runtime dependencies beyond the standard library. This slice has zero O2/pyarco dependency, per the design spec's explicit scope decision (docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md §7).
- Test-only dependency: `pytest>=8.0`, listed in `requirements-dev.txt` (matches pyarco's naming/invocation convention).
- Run the whole suite from the repo root: `python -m pytest tests -v` (no `sys.path` hacks needed — `control/` and `bits/` are already top-level packages, and `python -m pytest` puts the repo root on `sys.path`).
- `TestBit` (bits/test_bit.py) is a durable fixture, not throwaway scaffolding — it stays in the repo as the engine's regression bed for future slices.

---

## File Structure

```
control/
  __init__.py
  state.py          Task 1 — State enum
  roles.py           Task 2 — RoleClass, Role, RoleTable
  bit.py              Task 3 — Bit abstract base class
  device_pool.py       Task 4 — DevicePool, DeviceInfo
  registration.py       Task 5 — RegistrationState, JoinResult
  engine.py              Task 7 — GameServer (the orchestrator)
bits/
  __init__.py
  test_bit.py         Task 6 — TestBit (scored + jam roles)
tests/
  __init__.py
  test_state.py
  test_roles.py
  test_bit.py
  test_device_pool.py
  test_registration.py
  test_test_bit.py
  test_engine.py
requirements-dev.txt   Task 1
README.md              Task 8 — updated to reflect landed code
```

---

### Task 1: Repo scaffolding + `State` enum

**Files:**
- Create: `control/__init__.py`
- Create: `control/state.py`
- Create: `tests/__init__.py`
- Create: `tests/test_state.py`
- Create: `requirements-dev.txt`

**Interfaces:**
- Produces: `control.state.State` — an `Enum` with members `IDLE`, `LOADING`, `LOADED`, `SETUP`, `RUNNING`, `COMPLETING`, `UNLOADING`.

- [ ] **Step 1: Create empty package files and requirements-dev.txt**

`control/__init__.py`:
```python
```

`tests/__init__.py`:
```python
```

`requirements-dev.txt`:
```
# Development dependencies for the Control+GameServer test suite.
#
# Install into a venv:
#     python -m pip install -r requirements-dev.txt
#
# Run the offline test suite:
#     python -m pytest tests -v
pytest>=8.0
```

- [ ] **Step 2: Write the failing test**

`tests/test_state.py`:
```python
from control.state import State


def test_all_lifecycle_states_present():
    names = {s.name for s in State}
    assert names == {
        "IDLE", "LOADING", "LOADED", "SETUP",
        "RUNNING", "COMPLETING", "UNLOADING",
    }


def test_states_are_distinct_values():
    assert len({s.value for s in State}) == len(State)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.state'`

- [ ] **Step 4: Write minimal implementation**

`control/state.py`:
```python
"""Lifecycle states for the Control+GameServer's Bit-launching engine.

See docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md
section 3 for the full state diagram and transition rules.
"""

from enum import Enum, auto


class State(Enum):
    IDLE = auto()
    LOADING = auto()
    LOADED = auto()
    SETUP = auto()
    RUNNING = auto()
    COMPLETING = auto()
    UNLOADING = auto()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_state.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add control/__init__.py control/state.py tests/__init__.py tests/test_state.py requirements-dev.txt
git commit -m "feat: add State enum and test scaffolding"
```

---

### Task 2: `roles.py` — `RoleClass`, `Role`, `RoleTable`

**Files:**
- Create: `control/roles.py`
- Create: `tests/test_roles.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `control.roles.RoleClass` — `Enum` with `UNIQUE`, `SHARED`, `JAM`.
  - `control.roles.Role` — dataclass: `name: str`, `role_class: RoleClass`, `capacity: int | None`, `scored: bool`, `ugen_manifest: list` (defaults to `[]`).
  - `control.roles.RoleTable` — dataclass: `roles: dict[str, Role]`, `node_map: dict[str, list[str]]`.

- [ ] **Step 1: Write the failing test**

`tests/test_roles.py`:
```python
from control.roles import Role, RoleClass, RoleTable


def make_two_role_table():
    player = Role(name="player", role_class=RoleClass.SHARED,
                  capacity=None, scored=True)
    jammer = Role(name="jammer", role_class=RoleClass.JAM,
                  capacity=None, scored=False)
    return RoleTable(
        roles={"player": player, "jammer": jammer},
        node_map={"A": ["player"], "B": ["jammer"]},
    )


def test_role_defaults_to_empty_ugen_manifest():
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True)
    assert role.ugen_manifest == []


def test_role_table_holds_roles_and_node_fallback_lists():
    table = make_two_role_table()
    assert table.roles["player"].scored is True
    assert table.roles["jammer"].scored is False
    assert table.node_map["A"] == ["player"]
    assert table.node_map["B"] == ["jammer"]


def test_unique_role_has_integer_capacity():
    conductor = Role(name="conductor", role_class=RoleClass.UNIQUE,
                      capacity=1, scored=True)
    assert conductor.capacity == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_roles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.roles'`

- [ ] **Step 3: Write minimal implementation**

`control/roles.py`:
```python
"""Static role declarations a Bit provides. See design spec section 4."""

from dataclasses import dataclass, field
from enum import Enum, auto


class RoleClass(Enum):
    UNIQUE = auto()   # exclusive to one player (or capacity K)
    SHARED = auto()   # unbounded; every registrant gets the same effect
    JAM = auto()      # unbounded; full interaction but excluded from scoring


@dataclass
class Role:
    name: str
    role_class: RoleClass
    capacity: int | None  # None = unlimited (shared/jam); positive int for unique
    scored: bool
    # Placeholder for this role's per-player graph declaration (future
    # per-role graph-builder work). Unused in this slice; present so the
    # schema doesn't change later.
    ugen_manifest: list = field(default_factory=list)


@dataclass
class RoleTable:
    roles: dict[str, Role]
    node_map: dict[str, list[str]]  # node id -> ordered role-name fallback list
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_roles.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add control/roles.py tests/test_roles.py
git commit -m "feat: add Role/RoleTable data model"
```

---

### Task 3: `bit.py` — `Bit` abstract base class

**Files:**
- Create: `control/bit.py`
- Create: `tests/test_bit.py`

**Interfaces:**
- Consumes: `control.roles.RoleTable` (Task 2).
- Produces: `control.bit.Bit` — ABC with abstract property `role_table -> RoleTable`, and no-op default methods `on_setup_enter() -> None`, `on_run_start() -> None`, `update(dt: float) -> bool` (returns `False`), `on_complete() -> None`, `on_unload() -> None`, `verb_handlers() -> dict` (returns `{}`).

- [ ] **Step 1: Write the failing test**

`tests/test_bit.py`:
```python
import pytest

from control.bit import Bit
from control.roles import RoleTable


class MinimalBit(Bit):
    @property
    def role_table(self) -> RoleTable:
        return RoleTable(roles={}, node_map={})


def test_cannot_instantiate_bit_without_role_table():
    with pytest.raises(TypeError):
        Bit()  # abstract: role_table has no implementation


def test_default_hooks_are_no_ops_and_never_complete():
    bit = MinimalBit()
    assert bit.on_setup_enter() is None
    assert bit.on_run_start() is None
    assert bit.update(0.1) is False
    assert bit.on_complete() is None
    assert bit.on_unload() is None
    assert bit.verb_handlers() == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.bit'`

- [ ] **Step 3: Write minimal implementation**

`control/bit.py`:
```python
"""Base interface every Bit implements. See design spec section 4."""

from abc import ABC, abstractmethod

from control.roles import RoleTable


class Bit(ABC):
    """A loadable game/experience module for the Control+GameServer.

    Subclasses must provide `role_table`. All lifecycle hooks below are
    no-ops by default; a Bit overrides only the ones it needs.
    """

    @property
    @abstractmethod
    def role_table(self) -> RoleTable:
        """This Bit's static role declarations (control.roles.RoleTable)."""

    def on_setup_enter(self) -> None:
        """Called once when Control enters SETUP for this Bit."""

    def on_run_start(self) -> None:
        """Called once when Control enters RUNNING for this Bit."""

    def update(self, dt: float) -> bool:
        """Called once per tick while RUNNING.

        Return True to signal this Bit is finished; Control transitions to
        COMPLETING on the next tick. Default: never completes on its own.
        """
        return False

    def on_complete(self) -> None:
        """Called once when Control enters COMPLETING (scoring, closing actions)."""

    def on_unload(self) -> None:
        """Called once when Control enters UNLOADING, after devices are released."""

    def verb_handlers(self) -> dict:
        """Extra /game/* verb handlers this Bit adds, beyond the fixed
        lifecycle verbs Control always handles. Empty by default.
        """
        return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bit.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add control/bit.py tests/test_bit.py
git commit -m "feat: add Bit abstract base class"
```

---

### Task 4: `device_pool.py` — `DevicePool`, `DeviceInfo`

**Files:**
- Create: `control/device_pool.py`
- Create: `tests/test_device_pool.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `control.device_pool.DeviceInfo` — dataclass: `dev: str`, `name: str`, `protoversion: str`.
  - `control.device_pool.DevicePool` — `hello(dev, name, protoversion) -> DeviceInfo`, `known(dev) -> bool`, `get(dev) -> DeviceInfo | None`, `__len__() -> int`.

- [ ] **Step 1: Write the failing test**

`tests/test_device_pool.py`:
```python
from control.device_pool import DevicePool


def test_hello_registers_a_device():
    pool = DevicePool()
    info = pool.hello("ie3", "Tuneshroom 3", "1.0")
    assert pool.known("ie3") is True
    assert pool.get("ie3") is info
    assert info.name == "Tuneshroom 3"
    assert len(pool) == 1


def test_unknown_device_is_not_known():
    pool = DevicePool()
    assert pool.known("ie9") is False
    assert pool.get("ie9") is None


def test_repeated_hello_from_same_device_updates_in_place():
    pool = DevicePool()
    pool.hello("ie3", "Tuneshroom 3", "1.0")
    pool.hello("ie3", "Tuneshroom 3", "1.1")
    assert len(pool) == 1
    assert pool.get("ie3").protoversion == "1.1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_device_pool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.device_pool'`

- [ ] **Step 3: Write minimal implementation**

`control/device_pool.py`:
```python
"""Tracks known devices across Bit lifecycles. See design spec section 4."""

from dataclasses import dataclass


@dataclass
class DeviceInfo:
    dev: str
    name: str
    protoversion: str


class DevicePool:
    """dev -> DeviceInfo, populated by /game/hello. Global to Control, not
    reset when a Bit unloads -- a released device stays in the joinable pool.
    """

    def __init__(self):
        self._devices: dict[str, DeviceInfo] = {}

    def hello(self, dev: str, name: str, protoversion: str) -> DeviceInfo:
        info = DeviceInfo(dev=dev, name=name, protoversion=protoversion)
        self._devices[dev] = info
        return info

    def known(self, dev: str) -> bool:
        return dev in self._devices

    def get(self, dev: str) -> DeviceInfo | None:
        return self._devices.get(dev)

    def __len__(self) -> int:
        return len(self._devices)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_device_pool.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add control/device_pool.py tests/test_device_pool.py
git commit -m "feat: add DevicePool"
```

---

### Task 5: `registration.py` — `RegistrationState`, `JoinResult`

This is the core rule from brainstorming: scored roles are denied once RUNNING, jam roles stay open, and a re-tap to a different node is a role switch.

**Files:**
- Create: `control/registration.py`
- Create: `tests/test_registration.py`

**Interfaces:**
- Consumes: `control.roles.RoleClass`, `control.roles.RoleTable` (Task 2); `control.state.State` (Task 1).
- Produces:
  - `control.registration.JoinResult` — dataclass: `granted: bool`, `role: str | None`, `role_class: RoleClass | None`, `scored: bool | None`, `reason: str | None`, `hint: str | None` (all but `granted` default `None`).
  - `control.registration.RegistrationState(role_table: RoleTable)` — `join(dev: str, node: str, state: State) -> JoinResult`, `release(dev: str) -> bool`, `release_all() -> list[str]`, plus public attributes `assignments: dict[str, tuple[str, str, RoleClass]]` (`dev -> (node, role_name, role_class)`).

- [ ] **Step 1: Write the failing test**

`tests/test_registration.py`:
```python
from control.registration import RegistrationState
from control.roles import Role, RoleClass, RoleTable
from control.state import State


def make_table():
    player = Role(name="player", role_class=RoleClass.SHARED,
                  capacity=None, scored=True)
    jammer = Role(name="jammer", role_class=RoleClass.JAM,
                  capacity=None, scored=False)
    conductor = Role(name="conductor", role_class=RoleClass.UNIQUE,
                      capacity=1, scored=True)
    return RoleTable(
        roles={"player": player, "jammer": jammer, "conductor": conductor},
        node_map={
            "NODE_PLAYER": ["player"],
            "NODE_JAM": ["jammer"],
            "NODE_CONDUCTOR": ["conductor"],
        },
    )


def test_join_unknown_node_is_denied():
    reg = RegistrationState(make_table())
    result = reg.join("ie1", "NODE_MISSING", State.SETUP)
    assert result.granted is False
    assert result.reason == "no such node"


def test_join_grants_shared_scored_role_in_setup():
    reg = RegistrationState(make_table())
    result = reg.join("ie1", "NODE_PLAYER", State.SETUP)
    assert result.granted is True
    assert result.role == "player"
    assert result.scored is True


def test_scored_role_denied_once_running_but_jam_still_allowed():
    reg = RegistrationState(make_table())
    scored_result = reg.join("ie1", "NODE_PLAYER", State.RUNNING)
    jam_result = reg.join("ie2", "NODE_JAM", State.RUNNING)
    assert scored_result.granted is False
    assert scored_result.reason == "registration closed for scored roles"
    assert jam_result.granted is True
    assert jam_result.role == "jammer"


def test_unique_role_denied_once_capacity_reached():
    reg = RegistrationState(make_table())
    first = reg.join("ie1", "NODE_CONDUCTOR", State.SETUP)
    second = reg.join("ie2", "NODE_CONDUCTOR", State.SETUP)
    assert first.granted is True
    assert second.granted is False
    assert second.reason == "conductor at capacity"


def test_retapping_a_different_node_switches_role():
    reg = RegistrationState(make_table())
    reg.join("ie1", "NODE_PLAYER", State.SETUP)
    switch = reg.join("ie1", "NODE_JAM", State.SETUP)
    assert switch.granted is True
    assert switch.role == "jammer"
    assert reg.assignments["ie1"][1] == "jammer"
    assert reg._counts["player"] == 0  # released when ie1 switched away


def test_release_all_clears_assignments_and_counts():
    reg = RegistrationState(make_table())
    reg.join("ie1", "NODE_PLAYER", State.SETUP)
    reg.join("ie2", "NODE_JAM", State.SETUP)
    released = reg.release_all()
    assert set(released) == {"ie1", "ie2"}
    assert reg.assignments == {}
    assert reg._counts["player"] == 0
    assert reg._counts["jammer"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_registration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.registration'`

- [ ] **Step 3: Write minimal implementation**

`control/registration.py`:
```python
"""Per-Bit runtime registration state: who holds what role. See design
spec section 4 and the join-resolution rules in section 3 (SETUP vs RUNNING).
"""

from dataclasses import dataclass

from control.roles import Role, RoleClass, RoleTable
from control.state import State


@dataclass
class JoinResult:
    granted: bool
    role: str | None = None
    role_class: RoleClass | None = None
    scored: bool | None = None
    reason: str | None = None
    hint: str | None = None


class RegistrationState:
    """Created when a Bit loads, discarded when it unloads."""

    def __init__(self, role_table: RoleTable):
        self.role_table = role_table
        self.assignments: dict[str, tuple[str, str, RoleClass]] = {}
        self._counts: dict[str, int] = {name: 0 for name in role_table.roles}

    def join(self, dev: str, node: str, state: State) -> JoinResult:
        candidates = self.role_table.node_map.get(node)
        if not candidates:
            return JoinResult(granted=False, reason="no such node")

        last_full_role = None
        for role_name in candidates:
            role = self.role_table.roles[role_name]
            if role.scored and state == State.RUNNING:
                continue  # scored roles closed once running; try the next fallback
            if role.capacity is not None and self._counts[role_name] >= role.capacity:
                last_full_role = role_name
                continue
            self._assign(dev, node, role)
            return JoinResult(granted=True, role=role.name,
                               role_class=role.role_class, scored=role.scored)

        if last_full_role is not None:
            return JoinResult(granted=False, reason=f"{last_full_role} at capacity")
        # Every candidate was scored and we're RUNNING -- capacity was never
        # the blocker.
        return JoinResult(granted=False,
                           reason="registration closed for scored roles")

    def _assign(self, dev: str, node: str, role: Role) -> None:
        self.release(dev)  # re-tapping a different node is a role switch
        self.assignments[dev] = (node, role.name, role.role_class)
        self._counts[role.name] += 1

    def release(self, dev: str) -> bool:
        prev = self.assignments.pop(dev, None)
        if prev is None:
            return False
        _, role_name, _ = prev
        self._counts[role_name] -= 1
        return True

    def release_all(self) -> list[str]:
        devs = list(self.assignments)
        for dev in devs:
            self.release(dev)
        return devs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_registration.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add control/registration.py tests/test_registration.py
git commit -m "feat: add RegistrationState with scored/jam join rules"
```

---

### Task 6: `bits/test_bit.py` — `TestBit`

**Files:**
- Create: `bits/__init__.py`
- Create: `bits/test_bit.py`
- Create: `tests/test_test_bit.py`

**Interfaces:**
- Consumes: `control.bit.Bit` (Task 3), `control.roles.Role`/`RoleClass`/`RoleTable` (Task 2).
- Produces: `bits.test_bit.TestBit(run_duration: float = 2.0)` — implements `Bit`, with `role_table` containing a scored `shared` role (`"player"`, node `"TEST_PLAYER_NODE"`) and an unscored `jam` role (`"jammer"`, node `"TEST_JAM_NODE"`); `update(dt)` returns `True` once accumulated elapsed time (since `on_run_start()`) reaches `run_duration`.

- [ ] **Step 1: Write the failing test**

`bits/__init__.py`:
```python
```

`tests/test_test_bit.py`:
```python
from bits.test_bit import TestBit
from control.roles import RoleClass


def test_role_table_has_one_scored_and_one_jam_role():
    bit = TestBit()
    table = bit.role_table
    assert table.roles["player"].scored is True
    assert table.roles["player"].role_class == RoleClass.SHARED
    assert table.roles["jammer"].scored is False
    assert table.roles["jammer"].role_class == RoleClass.JAM


def test_role_ugen_manifests_are_present_but_empty_placeholders():
    bit = TestBit()
    table = bit.role_table
    assert table.roles["player"].ugen_manifest == []
    assert table.roles["jammer"].ugen_manifest == []


def test_node_map_grants_each_role_from_its_own_node():
    bit = TestBit()
    table = bit.role_table
    assert table.node_map["TEST_PLAYER_NODE"] == ["player"]
    assert table.node_map["TEST_JAM_NODE"] == ["jammer"]


def test_lifecycle_hooks_flip_flags():
    bit = TestBit()
    bit.on_setup_enter()
    bit.on_run_start()
    bit.on_complete()
    bit.on_unload()
    assert bit._setup_entered is True
    assert bit._run_started is True
    assert bit._completed is True
    assert bit._unloaded is True


def test_update_completes_after_run_duration_elapses():
    bit = TestBit(run_duration=1.0)
    bit.on_run_start()
    assert bit.update(0.4) is False
    assert bit.update(0.4) is False
    assert bit.update(0.4) is True  # 1.2s elapsed >= 1.0s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_test_bit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bits.test_bit'`

- [ ] **Step 3: Write minimal implementation**

`bits/test_bit.py`:
```python
"""TestBit: the durable reference/test fixture for the Control+GameServer
lifecycle. Exercises both a scored and a jam role. Not throwaway -- this
stays in the repo as the engine's regression fixture. See design spec
section 4.
"""

from control.bit import Bit
from control.roles import Role, RoleClass, RoleTable

RUN_DURATION_SECONDS = 2.0


class TestBit(Bit):
    def __init__(self, run_duration: float = RUN_DURATION_SECONDS):
        self._run_duration = run_duration
        self._elapsed = 0.0
        self._setup_entered = False
        self._run_started = False
        self._completed = False
        self._unloaded = False

    @property
    def role_table(self) -> RoleTable:
        player = Role(name="player", role_class=RoleClass.SHARED,
                      capacity=None, scored=True)
        jammer = Role(name="jammer", role_class=RoleClass.JAM,
                      capacity=None, scored=False)
        return RoleTable(
            roles={"player": player, "jammer": jammer},
            node_map={"TEST_PLAYER_NODE": ["player"],
                      "TEST_JAM_NODE": ["jammer"]},
        )

    def on_setup_enter(self) -> None:
        self._setup_entered = True

    def on_run_start(self) -> None:
        self._run_started = True
        self._elapsed = 0.0

    def update(self, dt: float) -> bool:
        self._elapsed += dt
        return self._elapsed >= self._run_duration

    def on_complete(self) -> None:
        self._completed = True

    def on_unload(self) -> None:
        self._unloaded = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_test_bit.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add bits/__init__.py bits/test_bit.py tests/test_test_bit.py
git commit -m "feat: add TestBit reference/test fixture"
```

---

### Task 7: `engine.py` — `GameServer`

The orchestrator: ties `State`, `DevicePool`, `RegistrationState`, and a loaded `Bit` together behind the plain Python API (`hello`, `load_bit`, `run`, `join`, `tick`) that a future O2 transport layer will call into.

**Files:**
- Create: `control/engine.py`
- Create: `tests/test_engine.py`

**Interfaces:**
- Consumes: `control.state.State` (Task 1); `control.bit.Bit` (Task 3); `control.device_pool.DevicePool` (Task 4); `control.registration.RegistrationState`/`JoinResult` (Task 5); `bits.test_bit.TestBit` (Task 6, test-only).
- Produces:
  - `control.engine.InvalidTransition(Exception)`, `control.engine.BitLoadError(Exception)`.
  - `control.engine.GameServer(bit_registry: dict[str, type[Bit]])` — attributes `state: State`, `devices: DevicePool`, `bit: Bit | None`, `registration: RegistrationState | None`, `on_release: Callable[[str], None] | None` (settable by a future transport layer). Methods: `hello(dev, name, protoversion) -> None`, `load_bit(name: str) -> None`, `run() -> None`, `join(dev, node) -> JoinResult`, `tick(dt: float) -> None`.

- [ ] **Step 1: Write the failing test**

`tests/test_engine.py`:
```python
import pytest

from bits.test_bit import TestBit
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.state import State


class ExplodingCompleteBit(TestBit):
    def on_complete(self) -> None:
        raise RuntimeError("boom")


class ExplodingUnloadBit(TestBit):
    def on_unload(self) -> None:
        raise RuntimeError("boom")


REGISTRY = {
    "test_bit": TestBit,
    "exploding_complete_bit": ExplodingCompleteBit,
    "exploding_unload_bit": ExplodingUnloadBit,
}


def make_server() -> GameServer:
    return GameServer(bit_registry=REGISTRY)


def test_load_bit_moves_idle_to_setup():
    server = make_server()
    server.load_bit("test_bit")
    assert server.state == State.SETUP
    assert isinstance(server.bit, TestBit)


def test_load_bit_requires_idle():
    server = make_server()
    server.load_bit("test_bit")
    with pytest.raises(InvalidTransition):
        server.load_bit("test_bit")


def test_load_bit_unknown_name_raises_and_stays_idle():
    server = make_server()
    with pytest.raises(BitLoadError):
        server.load_bit("no_such_bit")
    assert server.state == State.IDLE
    assert server.bit is None


def test_run_requires_setup():
    server = make_server()
    with pytest.raises(InvalidTransition):
        server.run()


def test_join_denied_when_no_bit_loaded():
    server = make_server()
    result = server.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted is False
    assert result.reason == "no Bit accepting registrations"


def test_full_lifecycle_reaches_idle_and_releases_devices():
    server = make_server()
    released = []
    server.on_release = released.append

    server.hello("ie1", "Tuneshroom 1", "1.0")
    server.load_bit("test_bit")
    assert server.state == State.SETUP

    join_result = server.join("ie1", "TEST_PLAYER_NODE")
    assert join_result.granted is True

    server.run()
    assert server.state == State.RUNNING

    server.tick(1.0)
    assert server.state == State.RUNNING  # 1.0s < TestBit's 2.0s default
    server.tick(1.5)  # 2.5s elapsed total -- crosses the completion threshold

    assert server.state == State.IDLE
    assert released == ["ie1"]
    assert server.bit is None
    assert server.devices.known("ie1") is True  # pool survives unload


def test_scored_join_denied_once_running_jam_still_allowed():
    server = make_server()
    server.load_bit("test_bit")
    server.run()
    scored = server.join("ie1", "TEST_PLAYER_NODE")
    jam = server.join("ie2", "TEST_JAM_NODE")
    assert scored.granted is False
    assert jam.granted is True


def test_on_complete_exception_still_reaches_idle():
    server = make_server()
    server.load_bit("exploding_complete_bit")
    server.run()
    server.tick(3.0)
    assert server.state == State.IDLE


def test_on_unload_exception_still_reaches_idle():
    server = make_server()
    server.load_bit("exploding_unload_bit")
    server.run()
    server.tick(3.0)
    assert server.state == State.IDLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.engine'`

- [ ] **Step 3: Write minimal implementation**

`control/engine.py`:
```python
"""GameServer: the Control+GameServer's lifecycle orchestrator. Owns the
state machine described in design spec section 3. O2-agnostic by design --
callers (a future O2lite transport layer) drive it through hello/load_bit/
run/join/tick and observe device releases via on_release.
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

    def hello(self, dev: str, name: str, protoversion: str) -> None:
        self.devices.hello(dev, name, protoversion)

    def load_bit(self, name: str) -> None:
        if self.state != State.IDLE:
            raise InvalidTransition(
                f"load_bit requires IDLE, current state is {self.state}")
        self.state = State.LOADING
        try:
            bit_cls = self.bit_registry[name]
            bit = bit_cls()
        except Exception as exc:
            self.state = State.IDLE
            raise BitLoadError(f"failed to load Bit {name!r}: {exc}") from exc
        self.bit = bit
        self.registration = RegistrationState(bit.role_table)
        self.state = State.LOADED
        self._enter_setup()

    def _enter_setup(self) -> None:
        self.state = State.SETUP
        self.bit.on_setup_enter()

    def run(self) -> None:
        if self.state != State.SETUP:
            raise InvalidTransition(
                f"run requires SETUP, current state is {self.state}")
        self.state = State.RUNNING
        self.bit.on_run_start()

    def join(self, dev: str, node: str) -> JoinResult:
        if self.state not in (State.SETUP, State.RUNNING):
            return JoinResult(granted=False,
                               reason="no Bit accepting registrations")
        return self.registration.join(dev, node, self.state)

    def tick(self, dt: float) -> None:
        if self.state != State.RUNNING:
            return
        if self.bit.update(dt):
            self._complete()

    def _complete(self) -> None:
        self.state = State.COMPLETING
        try:
            self.bit.on_complete()
        except Exception:
            logger.exception("Bit.on_complete raised; unloading anyway")
        self._unload()

    def _unload(self) -> None:
        self.state = State.UNLOADING
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
        self.state = State.IDLE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -v`
Expected: PASS (all tests across every module, ~28 passed)

- [ ] **Step 6: Commit**

```bash
git add control/engine.py tests/test_engine.py
git commit -m "feat: add GameServer lifecycle orchestrator"
```

---

### Task 8: Update README to reflect landed code

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing (docs-only task).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Update the "Planned layout" section**

In `README.md`, replace the closing paragraph under "## Planned layout":

Old:
```markdown
No implementation yet: this repo starts at the design stage. First code lands
via the spec in `docs/superpowers/specs/`.
```

New:
```markdown
`control/` and `bits/` now hold the first implementation slice: the
Control+GameServer lifecycle engine (state machine, role/registration data
model) and `TestBit`, a durable reference fixture. It runs entirely offline
with no O2/Arco/pyarco dependency yet — see
`docs/superpowers/specs/2026-07-20-control-gameserver-first-slice-design.md`
for scope and rationale. Run the test suite with:

```
python -m pip install -r requirements-dev.txt
python -m pytest tests -v
```
```

- [ ] **Step 2: Verify the file renders sensibly**

Run: `cat README.md`
Expected: the new paragraph and fenced code block appear under "## Planned layout", no broken Markdown (matching triple-backtick fences).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: point README at the landed Control+GameServer slice"
```

---

## Self-Review Notes

- **Spec coverage:** §3 (state machine) → Tasks 1, 7. §4 (DevicePool, RoleTable, RegistrationState, Bit interface, TestBit, tick loop) → Tasks 2–7 (tick loop itself is `GameServer.tick`, Task 7; the O2 poll wrapper around it is out of scope per the transport-scope decision). §5 (error handling) → Task 7 (`InvalidTransition`, `BitLoadError`, forced-unload-on-exception tests). §6 (testing) → every task's test file; full-suite run in Task 7 Step 5. §7/§8 (decisions recap, open questions) → recorded in this plan's Global Constraints and not re-litigated.
- **Placeholder scan:** no TBD/TODO; the one placeholder (`ugen_manifest`) is explicitly named as such in both the spec and the code comment, with a test asserting it's present-but-empty rather than silently omitted.
- **Type consistency:** `JoinResult`, `RoleClass`, `RoleTable`, `State`, `Bit`, `DevicePool`, `RegistrationState` are defined once (Tasks 1–5) and referenced identically by name in every later task; `GameServer`'s constructor signature (`bit_registry: dict[str, type[Bit]]`) matches how Task 7's tests call it (`GameServer(bit_registry=REGISTRY)`).
