# Room Concept & Load Sequence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Terrarium a `Room` concept (RoomType, per-Bit declared support, a `ROOM` role class that binds a device as a Room's rendering backend) and an orchestrated boot sequence (Arco -> Room resolution -> Room binding -> Bit load) that replaces today's manual "hand-start Arco, then run a harness script" workflow.

**Architecture:** Extends the existing `control/` engine additively — a new `RoleClass.ROOM` reuses `RegistrationState`/`DevicePool` exactly as player roles do; a new Control-global `RoomBindingRegistry` persists which device is bound per `RoomType` across Bit load/unload cycles and process restarts; a new `control/boot.py` ties Arco process lifecycle + Room resolution + Bit gating into one sequence. No renderer is built here — `RoomBridge` is a backend-agnostic bridge (Protocol-typed sinks, fakes only), the same pattern `control/audio.py`'s `AudioBridge` already uses to stay pyarco-free.

**Tech Stack:** Python (existing stdlib only for new modules — `dataclasses`, `enum`, `subprocess`, `signal`, `json`), pytest, no new third-party dependencies.

## Global Constraints

- Every new module stays importable and testable with **no real Arco/O2/pyarco/luxaeterna** present — the offline suite must stay green (`docs/MM_TERRARIUM.md`: "the whole test suite still runs fully offline").
- Room resolution **fails hard, never downgrades**, when a target `RoomType`'s recipe isn't satisfiable.
- Arco has no message-based shutdown; termination is **SIGTERM to the subprocess**, never an in-protocol quit message.
- Room registration is **unlisted and admin-window-gated**, not credential-authenticated — matches this repo's existing "trusted LAN, no authentication" model (Console, DeviceLink).
- Room binding persistence is **just the bound device ID per RoomType**, nothing broader — in-memory for the process lifetime, plus a small on-disk record for restart recall.
- Follow existing repo conventions throughout: dataclasses for data, `Protocol` + fake test-doubles for backend-agnostic seams (mirrors `control/audio.py`), lazy `pyarco`/subprocess-sensitive imports inside methods, not at module level.

---

## File Structure

**New files:**
- `control/rooms.py` — `RoomType`, `RoomRecipe`, `ROOM_RECIPES`, `ROOM_NODE_IDS`, `resolve_room_type()`, `Room`, `room_role()`.
- `control/room_binding.py` — `RoomBindingRegistry` (bind/release/arm/disarm, disk persistence).
- `control/room_bridge.py` — `RoomBridge`, `RoomLightSink`/`RoomAudioSink` Protocols, fake sinks.
- `control/boot_config.py` — `BootConfig`.
- `control/arco_process.py` — `ArcoProcess`, `ArcoReadyTimeout`, `FakePopen`.
- `control/boot.py` — `boot()`, `wait_for_room_binding()`, `shutdown()`, `BootFailure`, `RoomBindingTimeout`.
- `tests/test_rooms.py`, `tests/test_room_binding.py`, `tests/test_room_bridge.py`, `tests/test_boot_config.py`, `tests/test_arco_process.py`, `tests/test_boot.py`.

**Modified files:**
- `control/roles.py` — add `RoleClass.ROOM`.
- `control/bit.py` — add `Bit.room_types`.
- `control/engine.py` — `GameServer` gains `room_binding`/`room` attributes and Room-aware `join()` branching.
- `console/protocol.py` — add `ArmRoomCommand`/`ReleaseRoomCommand`/`parse_admin_command`.
- `console/agent.py` — dispatch the two new admin commands, and filter `RoleClass.ROOM` out of `snapshot()`/`_devices_view()`/`on_registration_change()` so the Room never surfaces on any Console view (design spec section 7).
- `tests/test_roles.py`, `tests/test_bit.py`, `tests/test_engine.py`, `tests/test_console_agent.py` — new test cases alongside existing ones.

---

### Task 1: Room data model — `control/rooms.py`

**Files:**
- Create: `control/rooms.py`
- Test: `tests/test_rooms.py`

**Interfaces:**
- Produces: `RoomType` (Enum: `TEST`, `DEMO`), `RoomResolutionError(Exception)`, `RoomRecipe` (dataclass: `requires_array_backend: bool`), `ROOM_RECIPES: dict[RoomType, RoomRecipe]`, `ROOM_NODE_IDS: dict[RoomType, str]`, `resolve_room_type(target: RoomType, *, array_backend_configured: bool) -> RoomType`, `Room` (dataclass: `room_type: RoomType`, `bound_dev: str | None = None`), `room_role(room_type: RoomType, *, ugen_manifest: dict | None = None, light_manifest: dict | None = None) -> tuple[str, Role, str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rooms.py
import pytest

from control.roles import Role, RoleClass
from control.rooms import (
    ROOM_NODE_IDS,
    Room,
    RoomResolutionError,
    RoomType,
    resolve_room_type,
    room_role,
)


def test_resolve_room_type_test_needs_no_array_backend():
    assert resolve_room_type(
        RoomType.TEST, array_backend_configured=False) == RoomType.TEST


def test_resolve_room_type_demo_succeeds_with_array_backend():
    assert resolve_room_type(
        RoomType.DEMO, array_backend_configured=True) == RoomType.DEMO


def test_resolve_room_type_demo_fails_without_array_backend():
    with pytest.raises(RoomResolutionError):
        resolve_room_type(RoomType.DEMO, array_backend_configured=False)


def test_room_role_builds_capacity_one_room_class_role():
    name, role, node = room_role(RoomType.TEST)
    assert role.role_class == RoleClass.ROOM
    assert role.capacity == 1
    assert role.scored is False
    assert node == ROOM_NODE_IDS[RoomType.TEST]
    assert name == "room_test"


def test_room_role_carries_declared_manifests():
    _, role, _ = room_role(
        RoomType.DEMO,
        light_manifest={"instruments": [{"instrument": "aurora", "target": "primary"}]},
        ugen_manifest={"instruments": [{"instrument": "flsyn"}]})
    assert role.light_manifest["instruments"][0]["instrument"] == "aurora"
    assert role.ugen_manifest["instruments"][0]["instrument"] == "flsyn"


def test_room_defaults_to_unbound():
    room = Room(room_type=RoomType.TEST)
    assert room.bound_dev is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rooms.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.rooms'`

- [ ] **Step 3: Write the implementation**

```python
# control/rooms.py
"""Room: the physical (or simulated) LED/mic/speaker hardware a Terrarium
installation offers. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
sections 3-4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from control.roles import Role, RoleClass


class RoomType(Enum):
    TEST = auto()
    DEMO = auto()


class RoomResolutionError(Exception):
    """Raised when a target RoomType's recipe isn't satisfiable. Resolution
    never downgrades to a lesser type -- see design spec section 3."""


@dataclass(frozen=True)
class RoomRecipe:
    requires_array_backend: bool


ROOM_RECIPES: dict[RoomType, RoomRecipe] = {
    RoomType.TEST: RoomRecipe(requires_array_backend=False),
    RoomType.DEMO: RoomRecipe(requires_array_backend=True),
}

# Canonical, Control-owned Registration Node id per RoomType. Every Bit that
# declares support for a RoomType binds its ROOM-class role to this node (via
# room_role() below), so any compatible Bit can serve the same Room backend
# without re-declaring a fresh node id. Never surfaced in the Console or any
# app UI -- see design spec section 7.
ROOM_NODE_IDS: dict[RoomType, str] = {
    RoomType.TEST: "ROOM_TEST_NODE",
    RoomType.DEMO: "ROOM_DEMO_NODE",
}


def resolve_room_type(target: RoomType, *,
                      array_backend_configured: bool) -> RoomType:
    """Check target's recipe against what this installation has configured.
    Returns target on success. Raises RoomResolutionError -- never downgrades
    -- on failure."""
    recipe = ROOM_RECIPES[target]
    if recipe.requires_array_backend and not array_backend_configured:
        raise RoomResolutionError(
            f"{target.name} requires an array backend, none configured")
    return target


@dataclass
class Room:
    """Resolved once at boot. bound_dev is set once a device (physical,
    simulated, or reconnected-from-a-prior-run) is attached as this Room's
    rendering backend -- see control/room_binding.py and control/boot.py."""
    room_type: RoomType
    bound_dev: str | None = None


def room_role(room_type: RoomType, *, ugen_manifest: dict | None = None,
             light_manifest: dict | None = None) -> tuple[str, Role, str]:
    """Build a ROOM-class Role for room_type plus its canonical node id, so a
    Bit can merge them into its own RoleTable.roles / node_map. The role name
    is deterministic per RoomType so two Bits supporting the same RoomType
    declare identical role names -- see design spec section 3."""
    name = f"room_{room_type.name.lower()}"
    role = Role(
        name=name,
        role_class=RoleClass.ROOM,
        capacity=1,
        scored=False,
        ugen_manifest=ugen_manifest or {},
        light_manifest=light_manifest or {},
    )
    return name, role, ROOM_NODE_IDS[room_type]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rooms.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add control/rooms.py tests/test_rooms.py
git commit -m "feat(terrarium): add RoomType, Room, and room_role data model"
```

---

### Task 2: `RoleClass.ROOM` — `control/roles.py`

**Files:**
- Modify: `control/roles.py:7-10`
- Test: `tests/test_roles.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `RoleClass.ROOM` (used by Task 1's `room_role()` and Task 6's `GameServer`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_roles.py`:

```python
def test_role_class_room_is_distinct_from_player_classes():
    assert RoleClass.ROOM not in (RoleClass.UNIQUE, RoleClass.SHARED, RoleClass.JAM)
    room = Role(name="room_test", role_class=RoleClass.ROOM, capacity=1,
                scored=False)
    assert room.role_class == RoleClass.ROOM
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_roles.py::test_role_class_room_is_distinct_from_player_classes -v`
Expected: FAIL with `AttributeError: ROOM`

- [ ] **Step 3: Write the implementation**

In `control/roles.py`, change:

```python
class RoleClass(Enum):
    UNIQUE = auto()   # exclusive to one player (or capacity K)
    SHARED = auto()   # unbounded; every registrant gets the same effect
    JAM = auto()      # unbounded; full interaction but excluded from scoring
```

to:

```python
class RoleClass(Enum):
    UNIQUE = auto()   # exclusive to one player (or capacity K)
    SHARED = auto()   # unbounded; every registrant gets the same effect
    JAM = auto()      # unbounded; full interaction but excluded from scoring
    ROOM = auto()     # capacity 1; binds the Room's rendering backend, not a
                       # player -- see control/rooms.py:room_role and
                       # docs/superpowers/specs/2026-08-10-room-concept-and-
                       # load-sequence-design.md section 3.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_roles.py -v`
Expected: PASS (all tests, including the new one)

- [ ] **Step 5: Commit**

```bash
git add control/roles.py tests/test_roles.py
git commit -m "feat(terrarium): add RoleClass.ROOM"
```

---

### Task 3: `Bit.room_types` — `control/bit.py`

**Files:**
- Modify: `control/bit.py:18` (after the `version` class attribute)
- Test: `tests/test_bit.py`

**Interfaces:**
- Consumes: `control.rooms.RoomType` (Task 1).
- Produces: `Bit.room_types: set[RoomType]`, class-attribute default `{RoomType.TEST}` — read by Task 10's `boot()` as `bit_cls.room_types` before instantiation.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bit.py` (check the existing file first for its current fixture Bit subclass pattern, then add):

```python
from control.rooms import RoomType


def test_bit_defaults_to_test_room_support():
    class MinimalBit(Bit):
        @property
        def role_table(self):
            from control.roles import RoleTable
            return RoleTable(roles={}, node_map={})

    assert MinimalBit.room_types == {RoomType.TEST}
```

(Match whatever import alias `Bit` already uses at the top of `tests/test_bit.py` -- do not add a second `from control.bit import Bit` if one already exists.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bit.py::test_bit_defaults_to_test_room_support -v`
Expected: FAIL with `AttributeError: type object 'MinimalBit' has no attribute 'room_types'`

- [ ] **Step 3: Write the implementation**

In `control/bit.py`, add the import and the class attribute:

```python
from abc import ABC, abstractmethod

from control.roles import RoleTable
from control.rooms import RoomType


class Bit(ABC):
    ...
    # Bit identity for provenance stamping (light-manifest v2 bit_version).
    # The bit *name* is the registry key GameServer loaded it under -- not
    # an attribute here, so there is nothing for an author to keep in sync.
    version: str = ""

    # Which RoomTypes this Bit can run in. Every Bit supports at least
    # RoomType.TEST (the universal baseline); a Bit declares more by
    # overriding this class attribute. Read off the class (not an instance)
    # by control/boot.py's Bit-gating check, before the Bit is constructed.
    # Treat as override-only -- do not mutate this set in place, since it is
    # shared across every instance of a Bit that doesn't override it.
    room_types: set[RoomType] = {RoomType.TEST}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bit.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add control/bit.py tests/test_bit.py
git commit -m "feat(terrarium): add Bit.room_types, defaulting to TEST support"
```

---

### Task 4: `RoomBindingRegistry` (in-memory) — `control/room_binding.py`

**Files:**
- Create: `control/room_binding.py`
- Test: `tests/test_room_binding.py`

**Interfaces:**
- Consumes: `control.rooms.RoomType` (Task 1).
- Produces: `RoomBindingRegistry` with `bound_device(room_type) -> str | None`, `bind(room_type, dev) -> None`, `release(room_type) -> None`, `arm(room_type, window_seconds) -> None`, `disarm(room_type) -> None`, `is_armed(room_type) -> bool` — consumed by Task 6 (`GameServer`), Task 10/11 (`boot.py`), Task 12 (`console/agent.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_room_binding.py
from control.room_binding import RoomBindingRegistry
from control.rooms import RoomType


def make_clock():
    now = [0.0]

    def clock():
        return now[0]

    def advance(seconds):
        now[0] += seconds

    return clock, advance


def test_bind_and_bound_device_round_trip():
    registry = RoomBindingRegistry()
    assert registry.bound_device(RoomType.TEST) is None
    registry.bind(RoomType.TEST, "ie7")
    assert registry.bound_device(RoomType.TEST) == "ie7"


def test_release_clears_binding():
    registry = RoomBindingRegistry()
    registry.bind(RoomType.TEST, "ie7")
    registry.release(RoomType.TEST)
    assert registry.bound_device(RoomType.TEST) is None


def test_bindings_are_independent_per_room_type():
    registry = RoomBindingRegistry()
    registry.bind(RoomType.TEST, "ie7")
    registry.bind(RoomType.DEMO, "array-1")
    assert registry.bound_device(RoomType.TEST) == "ie7"
    assert registry.bound_device(RoomType.DEMO) == "array-1"


def test_arm_opens_a_window_that_expires():
    clock, advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    assert registry.is_armed(RoomType.TEST) is False
    registry.arm(RoomType.TEST, window_seconds=10.0)
    assert registry.is_armed(RoomType.TEST) is True
    advance(10.1)
    assert registry.is_armed(RoomType.TEST) is False


def test_disarm_closes_the_window_immediately():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm(RoomType.TEST, window_seconds=10.0)
    registry.disarm(RoomType.TEST)
    assert registry.is_armed(RoomType.TEST) is False


def test_bind_disarms_the_window():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm(RoomType.TEST, window_seconds=10.0)
    registry.bind(RoomType.TEST, "ie7")
    assert registry.is_armed(RoomType.TEST) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_room_binding.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.room_binding'`

- [ ] **Step 3: Write the implementation**

```python
# control/room_binding.py
"""RoomBindingRegistry: Control-global record of which device is bound as
each RoomType's Room rendering backend. Survives Bit load/unload cycles the
same way DevicePool does. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 4.
"""

from __future__ import annotations

import json
import os
import time

from control.rooms import RoomType


class RoomBindingRegistry:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._bound: dict[RoomType, str] = {}
        self._armed_until: dict[RoomType, float] = {}

    def bound_device(self, room_type: RoomType) -> str | None:
        return self._bound.get(room_type)

    def bind(self, room_type: RoomType, dev: str) -> None:
        self._bound[room_type] = dev
        self._armed_until.pop(room_type, None)

    def release(self, room_type: RoomType) -> None:
        self._bound.pop(room_type, None)
        self._armed_until.pop(room_type, None)

    def arm(self, room_type: RoomType, window_seconds: float) -> None:
        """Open a registration window for window_seconds. Only a join
        against the Room node while armed may bind a device -- see design
        spec section 4."""
        self._armed_until[room_type] = self._clock() + window_seconds

    def disarm(self, room_type: RoomType) -> None:
        self._armed_until.pop(room_type, None)

    def is_armed(self, room_type: RoomType) -> bool:
        deadline = self._armed_until.get(room_type)
        return deadline is not None and self._clock() < deadline

    def save(self, path: str) -> None:
        """Persist just the bound device IDs -- not armed-window state,
        which never survives a restart. See design spec section 4."""
        data = {room_type.name: dev for room_type, dev in self._bound.items()}
        with open(path, "w") as f:
            json.dump(data, f)

    def load(self, path: str) -> None:
        """Replace in-memory bindings with whatever's on disk. A missing
        file is a no-op (fresh installation, nothing recorded yet)."""
        if not os.path.isfile(path):
            return
        with open(path) as f:
            data = json.load(f)
        self._bound = {RoomType[name]: dev for name, dev in data.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_room_binding.py -v`
Expected: PASS (6 tests; the save/load tests are added in Task 5)

- [ ] **Step 5: Commit**

```bash
git add control/room_binding.py tests/test_room_binding.py
git commit -m "feat(terrarium): add RoomBindingRegistry with an admin-armed window"
```

---

### Task 5: Disk persistence — extend `control/room_binding.py`

**Files:**
- Modify: `control/room_binding.py` (already has `save`/`load` from Task 4 — this task is the test coverage confirming restart-recall behavior)
- Test: `tests/test_room_binding.py`

**Interfaces:**
- Consumes: `RoomBindingRegistry.save(path)`/`.load(path)` (already implemented in Task 4's Step 3).
- Produces: nothing new — this task is the regression test for the persistence contract described in the design spec ("On a Terrarium process restart... boot attempts to reconnect...").

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_room_binding.py`:

```python
def test_save_then_load_restores_bindings_into_a_fresh_registry(tmp_path):
    path = str(tmp_path / "room_binding.json")
    original = RoomBindingRegistry()
    original.bind(RoomType.TEST, "ie7")
    original.bind(RoomType.DEMO, "array-1")
    original.save(path)

    restored = RoomBindingRegistry()
    restored.load(path)
    assert restored.bound_device(RoomType.TEST) == "ie7"
    assert restored.bound_device(RoomType.DEMO) == "array-1"


def test_load_missing_file_is_a_noop(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    registry = RoomBindingRegistry()
    registry.load(path)  # must not raise
    assert registry.bound_device(RoomType.TEST) is None


def test_save_does_not_persist_armed_state(tmp_path):
    path = str(tmp_path / "room_binding.json")
    original = RoomBindingRegistry()
    original.arm(RoomType.TEST, window_seconds=10.0)
    original.save(path)

    restored = RoomBindingRegistry()
    restored.load(path)
    assert restored.is_armed(RoomType.TEST) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Since `save`/`load` were already implemented in Task 4 to keep that task's file coherent, these should already pass. Run them anyway to confirm before moving on:

Run: `python -m pytest tests/test_room_binding.py -k "save or load" -v`
Expected: PASS — if any fail, the `save`/`load` implementation from Task 4 has a bug; fix it in `control/room_binding.py` before proceeding.

- [ ] **Step 3: N/A — implementation already present from Task 4**

- [ ] **Step 4: Run full test file to verify everything passes together**

Run: `python -m pytest tests/test_room_binding.py -v`
Expected: PASS (9 tests total)

- [ ] **Step 5: Commit**

```bash
git add tests/test_room_binding.py
git commit -m "test(terrarium): cover RoomBindingRegistry disk persistence"
```

---

### Task 6: `GameServer` Room-aware `join()` — `control/engine.py`

**Files:**
- Modify: `control/engine.py:1-20` (imports), `:32-56` (`__init__`), `:93-107` (`join`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `control.rooms.Room`/`RoomType`/`room_role` (Task 1), `control.roles.RoleClass` (Task 2), `control.room_binding.RoomBindingRegistry` (Task 4).
- Produces: `GameServer(bit_registry, room_binding=None)`, `GameServer.room: Room | None` (settable by the caller before `load_bit`), `GameServer.room_binding: RoomBindingRegistry | None` — both consumed by Task 10 (`boot.py`) and Task 12 (`console/agent.py`). `join()` now returns `JoinResult(granted=False, reason="no such node")` for an unarmed Room node, and for a granted `ROOM`-class role returns a `JoinResult` with `config=None` (never composes a player blob).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_engine.py`:

```python
from control.room_binding import RoomBindingRegistry
from control.roles import Role, RoleClass, RoleTable
from control.rooms import Room, RoomType, room_role


class RoomCapableBit(TestBit):
    room_types = {RoomType.TEST}

    @property
    def role_table(self) -> RoleTable:
        table = super().role_table
        name, role, node = room_role(RoomType.TEST)
        table.roles[name] = role
        table.node_map[node] = [name]
        return table


def test_room_node_join_denied_while_unarmed():
    server = GameServer({"RoomCapableBit": RoomCapableBit},
                        room_binding=RoomBindingRegistry())
    server.room = Room(room_type=RoomType.TEST)
    server.load_bit("RoomCapableBit")
    result = server.join("ie9", "ROOM_TEST_NODE")
    assert result.granted is False
    assert result.reason == "no such node"


def test_room_node_join_binds_device_once_armed():
    binding = RoomBindingRegistry()
    server = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding)
    server.room = Room(room_type=RoomType.TEST)
    server.load_bit("RoomCapableBit")
    binding.arm(RoomType.TEST, window_seconds=10.0)

    result = server.join("ie9", "ROOM_TEST_NODE")

    assert result.granted is True
    assert result.role_class == RoleClass.ROOM
    assert result.config is None
    assert server.room.bound_dev == "ie9"
    assert binding.bound_device(RoomType.TEST) == "ie9"


def test_room_join_does_not_disturb_player_joins():
    binding = RoomBindingRegistry()
    server = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding)
    server.room = Room(room_type=RoomType.TEST)
    server.load_bit("RoomCapableBit")

    result = server.join("ie1", "TEST_PLAYER_NODE")

    assert result.granted is True
    assert result.role_class == RoleClass.SHARED
    assert result.config is not None    # normal player composition, unchanged


def test_join_without_room_configured_ignores_room_gating():
    # A GameServer with no room_binding/room set (the pre-Room-concept
    # construction path) must keep working exactly as before.
    server = GameServer({"TestBit": TestBit})
    server.load_bit("TestBit")
    result = server.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine.py -k room -v`
Expected: FAIL — `GameServer.__init__() got an unexpected keyword argument 'room_binding'` (or `AttributeError: 'GameServer' object has no attribute 'room'`)

- [ ] **Step 3: Write the implementation**

In `control/engine.py`, add an import:

```python
from control.roles import RoleClass
```

Change `__init__` (currently lines 32-56) by adding two new attributes after `self.devices = DevicePool()`:

```python
    def __init__(self, bit_registry: dict, room_binding=None):
        self.bit_registry = bit_registry
        self.state = State.IDLE
        self.devices = DevicePool()
        # Control-global Room state (see control/rooms.py, control/
        # room_binding.py). Both may be None for a GameServer that predates
        # the Room concept -- join() below treats that exactly as "no Room
        # node exists," leaving normal player joins untouched.
        self.room_binding = room_binding
        self.room = None
        self.bit: Bit | None = None
```

(leave the rest of `__init__` unchanged.)

Replace `join()` (currently lines 93-107):

```python
    def join(self, dev: str, node: str) -> JoinResult:
        if self.state not in (State.SETUP, State.RUNNING):
            return JoinResult(granted=False,
                               reason="no Bit accepting registrations")
        if self._is_room_node(node) and not self._room_armed():
            return JoinResult(granted=False, reason="no such node")
        result = self.registration.join(dev, node, self.state)
        if result.granted and result.role_class == RoleClass.ROOM:
            self._bind_room(dev)
            return result
        if result.granted:
            # Compose from the registration's role-table snapshot -- Bits
            # build role_table per property access, so a fresh call could
            # return different Role objects than the ones counts track.
            role = self.registration.role_table.roles[result.role]
            result.config = compose_role_config(
                self.bit_name, self.bit.version, role)
            self._notify("on_registration_change")
            self._notify("on_devices_change")
        return result

    def _is_room_node(self, node: str) -> bool:
        for role_name in self.registration.role_table.node_map.get(node, ()):
            role = self.registration.role_table.roles.get(role_name)
            if role is not None and role.role_class == RoleClass.ROOM:
                return True
        return False

    def _room_armed(self) -> bool:
        if self.room_binding is None or self.room is None:
            return False
        return self.room_binding.is_armed(self.room.room_type)

    def _bind_room(self, dev: str) -> None:
        if self.room_binding is not None and self.room is not None:
            self.room_binding.bind(self.room.room_type, dev)
        if self.room is not None:
            self.room.bound_dev = dev
        self._notify("on_devices_change")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS (all tests, including the pre-existing ones — confirms the Room gate is fully backward compatible)

- [ ] **Step 5: Commit**

```bash
git add control/engine.py tests/test_engine.py
git commit -m "feat(terrarium): route ROOM-class joins to Room binding, gated by an armed window"
```

---

### Task 7: `RoomBridge` — `control/room_bridge.py`

**Files:**
- Create: `control/room_bridge.py`
- Test: `tests/test_room_bridge.py`

**Interfaces:**
- Consumes: nothing (deliberately backend-agnostic — no imports from `control.rooms`/`control.engine`).
- Produces: `RoomLightSink`/`RoomAudioSink` (Protocols), `FakeRoomLightSink`, `FakeRoomAudioSink`, `RoomBridge` with `bind(dev, light=None, audio=None)`, `feed_midi(status, d1, d2)`, `release()`, `shutdown()`, `dev: str | None` — consumed by Task 10/11 (`boot.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_room_bridge.py
from control.room_bridge import FakeRoomAudioSink, FakeRoomLightSink, RoomBridge


def test_unbound_bridge_feed_midi_is_a_noop():
    bridge = RoomBridge()
    bridge.feed_midi(0xB0, 74, 64)   # must not raise


def test_bind_sets_dev():
    bridge = RoomBridge()
    bridge.bind("ie7")
    assert bridge.dev == "ie7"


def test_feed_midi_forwards_to_both_sinks():
    light, audio = FakeRoomLightSink(), FakeRoomAudioSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light, audio=audio)

    bridge.feed_midi(0xB0, 74, 64)

    assert light.fed == [(0xB0, 74, 64)]
    assert audio.fed == [(0xB0, 74, 64)]


def test_feed_midi_with_only_light_sink_skips_audio():
    light = FakeRoomLightSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light)

    bridge.feed_midi(0x90, 60, 100)

    assert light.fed == [(0x90, 60, 100)]


def test_release_clears_light_and_unbinds():
    light = FakeRoomLightSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light)

    bridge.release()

    assert light.cleared is True
    assert bridge.dev is None
    bridge.feed_midi(0xB0, 74, 64)   # unbound again: must not raise or re-feed
    assert light.fed == []


def test_shutdown_calls_audio_shutdown_then_releases():
    light, audio = FakeRoomLightSink(), FakeRoomAudioSink()
    bridge = RoomBridge()
    bridge.bind("ie7", light=light, audio=audio)

    bridge.shutdown()

    assert audio.shut is True
    assert light.cleared is True
    assert bridge.dev is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_room_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.room_bridge'`

- [ ] **Step 3: Write the implementation**

```python
# control/room_bridge.py
"""RoomBridge: fans a Room's MIDI stream out to whatever light/audio sinks
are currently bound -- the Room-scoped sibling of harness/device_bridge.py
and control/audio.py's AudioBridge. Backend-agnostic by construction: it
never imports luxaeterna or pyarco, so the offline suite stays green. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 6.
"""

from __future__ import annotations

from typing import Protocol


class RoomLightSink(Protocol):
    def feed_midi(self, status: int, d1: int, d2: int) -> None: ...
    def clear(self) -> None: ...


class RoomAudioSink(Protocol):
    def feed_midi(self, status: int, d1: int, d2: int) -> None: ...
    def shutdown(self) -> None: ...


class FakeRoomLightSink:
    """In-process test double, sibling of control/audio.py's FakeVoice."""

    def __init__(self) -> None:
        self.fed: list[tuple[int, int, int]] = []
        self.cleared = False

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        self.fed.append((status, d1, d2))

    def clear(self) -> None:
        self.cleared = True


class FakeRoomAudioSink:
    def __init__(self) -> None:
        self.fed: list[tuple[int, int, int]] = []
        self.shut = False

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        self.fed.append((status, d1, d2))

    def shutdown(self) -> None:
        self.shut = True


class RoomBridge:
    """Owns whichever light/audio sinks are currently bound to the Room and
    forwards the same MIDI bytes to both, mirroring harness/led_smoke.py's
    feed_shared() -- light and sound reading the same stream is the point."""

    def __init__(self) -> None:
        self.dev: str | None = None
        self._light: RoomLightSink | None = None
        self._audio: RoomAudioSink | None = None

    def bind(self, dev: str, light: RoomLightSink | None = None,
             audio: RoomAudioSink | None = None) -> None:
        self.dev = dev
        self._light = light
        self._audio = audio

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        if self._light is not None:
            self._light.feed_midi(status, d1, d2)
        if self._audio is not None:
            self._audio.feed_midi(status, d1, d2)

    def release(self) -> None:
        if self._light is not None:
            self._light.clear()
        self.dev = None
        self._light = None
        self._audio = None

    def shutdown(self) -> None:
        if self._audio is not None:
            self._audio.shutdown()
        self.release()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_room_bridge.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add control/room_bridge.py tests/test_room_bridge.py
git commit -m "feat(terrarium): add backend-agnostic RoomBridge"
```

---

### Task 8: `BootConfig` — `control/boot_config.py`

**Files:**
- Create: `control/boot_config.py`
- Test: `tests/test_boot_config.py`

**Interfaces:**
- Consumes: `control.rooms.RoomType` (Task 1).
- Produces: `BootConfig` (dataclass: `room_type`, `bit_name`, `arco_soundfont=None`, `array_backend=None`, `arco_ready_timeout=15.0`, `room_setup_timeout=30.0`, property `array_backend_configured`) — consumed by Task 10 (`boot.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_boot_config.py
from control.boot_config import BootConfig
from control.rooms import RoomType


def test_array_backend_configured_false_when_none():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    assert config.array_backend_configured is False


def test_array_backend_configured_true_for_simulator():
    config = BootConfig(room_type=RoomType.DEMO, bit_name="TestBit",
                        array_backend="simulator")
    assert config.array_backend_configured is True


def test_array_backend_configured_true_for_real_host():
    config = BootConfig(room_type=RoomType.DEMO, bit_name="TestBit",
                        array_backend="10.44.0.50")
    assert config.array_backend_configured is True


def test_default_timeouts():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    assert config.arco_ready_timeout == 15.0
    assert config.room_setup_timeout == 30.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_boot_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.boot_config'`

- [ ] **Step 3: Write the implementation**

```python
# control/boot_config.py
"""Boot-time configuration for control.boot's load sequence. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 5.
"""

from __future__ import annotations

from dataclasses import dataclass

from control.rooms import RoomType


@dataclass
class BootConfig:
    room_type: RoomType
    bit_name: str
    arco_soundfont: str | None = None
    # None = no array backend configured; "simulator" = Terrarium spawns
    # one (Spec 2's job); any other string = a real ArtNet/WLED host.
    array_backend: str | None = None
    arco_ready_timeout: float = 15.0
    room_setup_timeout: float = 30.0

    @property
    def array_backend_configured(self) -> bool:
        return self.array_backend is not None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_boot_config.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add control/boot_config.py tests/test_boot_config.py
git commit -m "feat(terrarium): add BootConfig for the load sequence"
```

---

### Task 9: `ArcoProcess` — `control/arco_process.py`

**Files:**
- Create: `control/arco_process.py`
- Test: `tests/test_arco_process.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ArcoReadyTimeout(Exception)`, `ArcoProcess(command, *, popen=subprocess.Popen, probe=_default_probe, clock=time.monotonic, sleep=time.sleep)` with `start()`, `wait_ready(timeout)`, `shutdown()`; `FakePopen` test double — consumed by Task 10 (`boot.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_arco_process.py
import signal

import pytest

from control.arco_process import ArcoProcess, ArcoReadyTimeout, FakePopen


def make_clock():
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    return clock, sleep


def test_start_launches_the_configured_command():
    popen = FakePopen()
    process = ArcoProcess(["arco-server", "--flag"], popen=popen)
    process.start()
    assert popen.commands == [["arco-server", "--flag"]]


def test_wait_ready_returns_once_probe_succeeds():
    clock, sleep = make_clock()
    calls = []

    def probe():
        calls.append(1)
        return len(calls) >= 3   # ready on the third check

    process = ArcoProcess(["arco-server"], popen=FakePopen(), probe=probe,
                          clock=clock, sleep=sleep)
    process.start()
    process.wait_ready(timeout=5.0)   # must not raise
    assert len(calls) == 3


def test_wait_ready_raises_when_probe_never_succeeds():
    clock, sleep = make_clock()
    process = ArcoProcess(["arco-server"], popen=FakePopen(),
                          probe=lambda: False, clock=clock, sleep=sleep)
    process.start()
    with pytest.raises(ArcoReadyTimeout):
        process.wait_ready(timeout=1.0)


def test_shutdown_sends_sigterm_and_waits():
    popen = FakePopen()
    process = ArcoProcess(["arco-server"], popen=popen)
    process.start()

    process.shutdown()

    assert popen.signals == [signal.SIGTERM]
    assert popen.waited is True


def test_shutdown_before_start_is_a_noop():
    process = ArcoProcess(["arco-server"], popen=FakePopen())
    process.shutdown()   # must not raise


def test_shutdown_twice_only_signals_once():
    popen = FakePopen()
    process = ArcoProcess(["arco-server"], popen=popen)
    process.start()
    process.shutdown()
    process.shutdown()
    assert popen.signals == [signal.SIGTERM]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_arco_process.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.arco_process'`

- [ ] **Step 3: Write the implementation**

```python
# control/arco_process.py
"""ArcoProcess: spawns and owns the Arco server subprocess for the
Terrarium load sequence. Real pyarco imports stay lazy (inside
_default_probe, never at module level) so the offline suite runs with
neither Arco nor pyarco present. Arco has no message-based quit
(arco/doc/server.md: the only documented shutdown is a console keypress),
so shutdown() sends SIGTERM, mirroring harness/led_smoke.py's own
_sigterm_as_keyboard_interrupt handling of itself. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 5.
"""

from __future__ import annotations

import signal
import subprocess
import time


class ArcoReadyTimeout(Exception):
    """Raised when Arco doesn't report ready within the configured timeout."""


def _default_probe() -> bool:
    """Real readiness probe: lazy pyarco import, mirroring
    harness/arco_synth.py's ArcoSynthPool.start(). A bare connect attempt --
    callers needing the full ensemble use ArcoSynthPool afterward, once this
    has already confirmed a server is listening."""
    from pyarco.arco_engine import arco  # noqa: PLC0415 (lazy by design)
    try:
        arco.initialize()
        return True
    except TimeoutError:
        return False


class FakePopen:
    """In-process test double for subprocess.Popen, sibling of
    control/audio.py's FakeVoice/FakePool."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.signals: list[int] = []
        self.waited = False

    def __call__(self, command: list[str]):
        self.commands.append(command)
        return self

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def wait(self) -> None:
        self.waited = True


class ArcoProcess:
    def __init__(self, command: list[str], *, popen=subprocess.Popen,
                 probe=_default_probe, clock=time.monotonic,
                 sleep=time.sleep) -> None:
        self._command = command
        self._popen = popen
        self._probe = probe
        self._clock = clock
        self._sleep = sleep
        self._process = None

    def start(self) -> None:
        self._process = self._popen(self._command)

    def wait_ready(self, timeout: float) -> None:
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if self._probe():
                return
            self._sleep(0.2)
        raise ArcoReadyTimeout(
            f"Arco did not report ready within {timeout}s")

    def shutdown(self) -> None:
        if self._process is None:
            return
        self._process.send_signal(signal.SIGTERM)
        self._process.wait()
        self._process = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_arco_process.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add control/arco_process.py tests/test_arco_process.py
git commit -m "feat(terrarium): add ArcoProcess (spawn, wait_ready, SIGTERM shutdown)"
```

---

### Task 10: `boot()` happy path and failure modes — `control/boot.py`

**Files:**
- Create: `control/boot.py`
- Test: `tests/test_boot.py`

**Interfaces:**
- Consumes: `control.arco_process.ArcoProcess` (Task 9), `control.boot_config.BootConfig` (Task 8), `control.engine.GameServer`/`BitLoadError` (Task 6), `control.room_binding.RoomBindingRegistry` (Task 4/5), `control.room_bridge.RoomBridge` (Task 7), `control.rooms.Room`/`RoomResolutionError`/`resolve_room_type` (Task 1).
- Produces: `BootFailure(Exception)`, `boot(config, bit_registry, *, arco_command, room_binding, arco_process_cls=ArcoProcess, simulator_factory=None, known_device_connected=lambda dev: False, tick=None) -> tuple[GameServer, RoomBridge, ArcoProcess]` — consumed by Task 11 (extends this same file) and any future harness entrypoint (Spec 2).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_boot.py
from control.arco_process import FakePopen
from control.boot import BootFailure, boot
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.rooms import RoomType
from control.state import State
from tests.test_engine import RoomCapableBit


def make_registry():
    return {"RoomCapableBit": RoomCapableBit}


def test_boot_happy_path_via_simulator_factory():
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        arco_process_cls=lambda cmd: _ready_arco(cmd),
        simulator_factory=lambda: "sim-room-dev")

    assert gs.state == State.SETUP
    assert gs.room.room_type == RoomType.TEST
    assert gs.room.bound_dev == "sim-room-dev"
    assert room_bridge.dev == "sim-room-dev"


def test_boot_happy_path_via_recorded_device_reconnect():
    binding = RoomBindingRegistry()
    binding.bind(RoomType.TEST, "ie7")
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")

    gs, room_bridge, arco = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=binding, arco_process_cls=_ready_arco,
        known_device_connected=lambda dev: dev == "ie7")

    assert gs.room.bound_dev == "ie7"
    assert room_bridge.dev == "ie7"


def test_boot_fails_when_room_type_unresolvable():
    config = BootConfig(room_type=RoomType.DEMO, bit_name="RoomCapableBit")
    with pytest.raises(BootFailure, match="requires an array backend"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco)


def test_boot_fails_when_arco_never_ready():
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit",
                        arco_ready_timeout=0.5)
    with pytest.raises(BootFailure, match="Arco failed to start"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_never_ready_arco,
             simulator_factory=lambda: "sim-room-dev")


def test_boot_fails_for_unknown_bit_name():
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")
    with pytest.raises(BootFailure, match="unknown Bit"):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=lambda: "sim-room-dev")


def test_boot_fails_when_bit_does_not_support_resolved_room_type():
    class DemoOnlyBit(RoomCapableBit):
        room_types = {RoomType.DEMO}

    registry = {"DemoOnlyBit": DemoOnlyBit}
    config = BootConfig(room_type=RoomType.TEST, bit_name="DemoOnlyBit")
    with pytest.raises(BootFailure, match="does not support TEST"):
        boot(config, registry, arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
             simulator_factory=lambda: "sim-room-dev")


def test_boot_shuts_down_arco_on_any_failure_after_start():
    fake_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="NoSuchBit")
    with pytest.raises(BootFailure):
        boot(config, make_registry(), arco_command=["arco-server"],
             room_binding=RoomBindingRegistry(),
             arco_process_cls=lambda cmd: _ready_arco(cmd, popen=fake_popen),
             simulator_factory=lambda: "sim-room-dev")
    assert fake_popen.signals   # Arco was told to stop, not orphaned


def _ready_arco(command, popen=None):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=popen or FakePopen(), probe=lambda: True)


def _never_ready_arco(command):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=FakePopen(), probe=lambda: False)
```

Add `import pytest` at the top of `tests/test_boot.py` alongside the other imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_boot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.boot'`

- [ ] **Step 3: Write the implementation**

```python
# control/boot.py
"""Orchestrates the Terrarium load sequence: config -> Arco -> Room
resolution -> Room binding -> Bit load. See
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
section 5. Backend-agnostic: simulator creation is an injected factory --
Spec 2 (the Terrarium Visualization Simulator) supplies a real one; a real
hardware harness supplies known_device_connected against actual DevicePool
state.
"""

from __future__ import annotations

import time

from control.arco_process import ArcoProcess
from control.boot_config import BootConfig
from control.engine import BitLoadError, GameServer
from control.room_binding import RoomBindingRegistry
from control.room_bridge import RoomBridge
from control.rooms import Room, RoomResolutionError, resolve_room_type


class BootFailure(Exception):
    """Wraps any load-sequence failure. No partial/silent-downgrade success
    -- every failure tears down whatever was already started."""


def boot(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, arco_process_cls=ArcoProcess,
         simulator_factory=None, known_device_connected=lambda dev: False,
         tick=None):
    """Run the full load sequence. Returns (game_server, room_bridge,
    arco_process) once the Bit is loaded and either the Room is already
    bound (fast path) or a fresh tap has bound it (see wait_for_room_binding
    in this module, added by the next task). Raises BootFailure on any
    stage failure."""
    try:
        room_type = resolve_room_type(
            config.room_type,
            array_backend_configured=config.array_backend_configured)
    except RoomResolutionError as exc:
        raise BootFailure(str(exc)) from exc
    room = Room(room_type=room_type)

    arco = arco_process_cls(arco_command)
    try:
        arco.start()
        arco.wait_ready(config.arco_ready_timeout)
    except Exception as exc:
        raise BootFailure(f"Arco failed to start: {exc}") from exc

    _bind_room_fast_path(room, room_binding, simulator_factory,
                         known_device_connected)

    bit_cls = bit_registry.get(config.bit_name)
    if bit_cls is None:
        arco.shutdown()
        raise BootFailure(f"unknown Bit {config.bit_name!r}")
    if room.room_type not in bit_cls.room_types:
        arco.shutdown()
        raise BootFailure(
            f"Bit {config.bit_name!r} does not support {room.room_type.name}")

    gs = GameServer(bit_registry, room_binding=room_binding)
    gs.room = room
    try:
        gs.load_bit(config.bit_name)
    except BitLoadError as exc:
        arco.shutdown()
        raise BootFailure(f"Bit load failed: {exc}") from exc

    room_bridge = RoomBridge()
    if room.bound_dev is not None:
        room_bridge.bind(room.bound_dev)

    return gs, room_bridge, arco


def _bind_room_fast_path(room: Room, room_binding: RoomBindingRegistry,
                         simulator_factory, known_device_connected) -> None:
    """Attempt the no-tap-needed path: a Terrarium-spawned simulator, or a
    reconnect to a previously recorded physical device. Leaves the Room
    unbound (room.bound_dev stays None) if neither applies -- the next
    task's wait_for_room_binding is what holds for a fresh admin-armed tap,
    not this function's job."""
    if simulator_factory is not None:
        dev = simulator_factory()
        room.bound_dev = dev
        room_binding.bind(room.room_type, dev)
        return
    recorded = room_binding.bound_device(room.room_type)
    if recorded is not None and known_device_connected(recorded):
        room.bound_dev = recorded
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_boot.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add control/boot.py tests/test_boot.py
git commit -m "feat(terrarium): add boot() orchestrating Arco start, Room resolution, and Bit gating"
```

---

### Task 11: `wait_for_room_binding()` and `shutdown()` — extend `control/boot.py`

**Files:**
- Modify: `control/boot.py` (add `RoomBindingTimeout`, `wait_for_room_binding`, `shutdown`; wire `wait_for_room_binding` into `boot()` when the fast path doesn't bind)
- Test: `tests/test_boot.py`

**Interfaces:**
- Consumes: `GameServer.room`/`.tick()` (Task 6), `RoomBindingRegistry.arm`/`.disarm` (Task 4).
- Produces: `RoomBindingTimeout(Exception)`, `wait_for_room_binding(gs, room_binding, timeout, *, tick, clock=time.monotonic, sleep=time.sleep) -> None`, `shutdown(gs, room_bridge, arco) -> None` — `shutdown` is the entry point any future harness/CLI calls; `wait_for_room_binding` is now called automatically by `boot()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_boot.py`:

```python
from control.boot import RoomBindingTimeout, shutdown, wait_for_room_binding


def test_wait_for_room_binding_returns_immediately_if_already_bound():
    gs, room_binding = _setup_loaded_room_bit()
    gs.room.bound_dev = "ie7"
    calls = []
    wait_for_room_binding(gs, room_binding, timeout=5.0,
                          tick=lambda: calls.append(1))
    assert calls == []


def test_wait_for_room_binding_arms_and_detects_a_late_join():
    gs, room_binding = _setup_loaded_room_bit()
    ticks = [0]

    def tick():
        ticks[0] += 1
        if ticks[0] == 3:
            gs.join("ie9", "ROOM_TEST_NODE")

    clock, sleep = _fake_clock()
    wait_for_room_binding(gs, room_binding, timeout=5.0, tick=tick,
                          clock=clock, sleep=sleep)

    assert gs.room.bound_dev == "ie9"
    assert room_binding.is_armed(gs.room.room_type) is False   # disarmed on success


def test_wait_for_room_binding_times_out_and_disarms():
    gs, room_binding = _setup_loaded_room_bit()
    clock, sleep = _fake_clock()

    with pytest.raises(RoomBindingTimeout):
        wait_for_room_binding(gs, room_binding, timeout=1.0, tick=lambda: None,
                              clock=clock, sleep=sleep)

    assert room_binding.is_armed(gs.room.room_type) is False


def test_shutdown_aborts_a_running_bit_then_tears_down():
    gs, room_binding = _setup_loaded_room_bit()
    gs.run()
    room_bridge = RoomBridge()
    room_bridge.bind("ie7")
    fake_popen = FakePopen()
    arco = ArcoProcess(["arco-server"], popen=fake_popen)
    arco.start()

    shutdown(gs, room_bridge, arco)

    assert gs.state == State.IDLE
    assert room_bridge.dev is None
    assert fake_popen.signals


def test_shutdown_on_already_idle_server_does_not_raise():
    gs = GameServer({"RoomCapableBit": RoomCapableBit})
    room_bridge = RoomBridge()
    fake_popen = FakePopen()
    arco = ArcoProcess(["arco-server"], popen=fake_popen)
    arco.start()
    shutdown(gs, room_bridge, arco)   # must not raise
    assert fake_popen.signals


def _setup_loaded_room_bit():
    room_binding = RoomBindingRegistry()
    gs = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=room_binding)
    gs.room = Room(room_type=RoomType.TEST)
    gs.load_bit("RoomCapableBit")
    return gs, room_binding


def _fake_clock():
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    return clock, sleep
```

Add the corresponding imports at the top of `tests/test_boot.py`:

```python
from control.engine import GameServer
from control.room_bridge import RoomBridge
from control.rooms import Room
from control.arco_process import ArcoProcess
```

(Combine with the imports already added in Task 10 — de-duplicate rather than repeating an import line.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_boot.py -k "wait_for_room_binding or shutdown" -v`
Expected: FAIL with `ImportError: cannot import name 'wait_for_room_binding' from 'control.boot'`

- [ ] **Step 3: Write the implementation**

In `control/boot.py`, add after the existing `boot()` function:

```python
class RoomBindingTimeout(Exception):
    """Raised when no device joins as the Room within the configured
    setup window."""


def wait_for_room_binding(gs: GameServer, room_binding: RoomBindingRegistry,
                          timeout: float, *, tick, clock=time.monotonic,
                          sleep=time.sleep) -> None:
    """Hold until the Room is bound (a fresh admin-armed tap grants the
    ROOM-class role) or timeout elapses. `tick` is called once per
    iteration -- driving whatever transport/tick loop might deliver that
    join -- so this function has no transport opinion of its own. Mirrors
    harness/devicelink_smoke.py's _wait_in_setup poll-loop shape."""
    if gs.room.bound_dev is not None:
        return
    room_binding.arm(gs.room.room_type, timeout)
    deadline = clock() + timeout
    while clock() < deadline:
        tick()
        if gs.room.bound_dev is not None:
            room_binding.disarm(gs.room.room_type)
            return
        sleep(0.05)
    room_binding.disarm(gs.room.room_type)
    raise RoomBindingTimeout(
        f"no device joined as {gs.room.room_type.name} Room within {timeout}s")


def shutdown(gs: GameServer, room_bridge: RoomBridge, arco: ArcoProcess) -> None:
    """Tear down in the order design spec section 5 step 9 requires: the
    running Bit first (mirroring AudioBridge.shutdown()'s "free everything
    before the pool goes away"), then the Room bridge, then Arco last since
    everything else may still want to address it during teardown."""
    from control.state import State
    if gs.state != State.IDLE:
        gs.abort()
    room_bridge.shutdown()
    arco.shutdown()
```

Add the matching imports at the top of `control/boot.py`:

```python
from control.room_bridge import RoomBridge
```

(`ArcoProcess` is already imported; `GameServer` and `RoomBindingRegistry` are already imported from Task 10.)

Now wire `wait_for_room_binding` into `boot()` — replace the `room_bridge = RoomBridge()` block at the end of `boot()` (from Task 10) with:

```python
    if room.bound_dev is None:
        try:
            wait_for_room_binding(
                gs, room_binding, config.room_setup_timeout,
                tick=tick or (lambda: gs.tick(0.05)))
        except RoomBindingTimeout as exc:
            gs.abort()
            arco.shutdown()
            raise BootFailure(str(exc)) from exc

    room_bridge = RoomBridge()
    if room.bound_dev is not None:
        room_bridge.bind(room.bound_dev)

    return gs, room_bridge, arco
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_boot.py -v`
Expected: PASS (12 tests total across both tasks)

- [ ] **Step 5: Run the full test suite to confirm nothing regressed**

Run: `python -m pytest tests -v`
Expected: PASS (every test, old and new)

- [ ] **Step 6: Commit**

```bash
git add control/boot.py tests/test_boot.py
git commit -m "feat(terrarium): hold for an admin-armed Room tap during boot, add shutdown()"
```

---

### Task 12: Admin "Connect LED Device as Room" commands — `console/protocol.py`, `console/agent.py`

**Files:**
- Modify: `console/protocol.py:1-24`
- Modify: `console/agent.py:1-51`
- Test: `tests/test_console_protocol.py`, `tests/test_console_agent.py`

**Interfaces:**
- Consumes: `control.rooms.RoomType` (Task 1), `control.roles.RoleClass` (Task 2), `GameServer.room`/`.room_binding` (Task 6).
- Produces: `console.protocol.ArmRoomCommand`, `console.protocol.ReleaseRoomCommand`, `console.protocol.parse_admin_command(msg) -> ArmRoomCommand | ReleaseRoomCommand` — consumed by `console/agent.py`'s `_handle_admin_command`, which is the functional hook the design spec's admin "Connect LED Device as Room" Console action drives (UI polish is out of scope — see spec §2).

**Important:** the design spec's section 7 requires the Room's Registration Node/role to be "never surfaced in the Console, any app, or QR/NFC generation." Because Task 6 merges the `ROOM`-class role directly into the Bit's normal `role_table` (the same table `ConsoleAgent.snapshot()`/`on_registration_change()`/`_devices_view()` already iterate unconditionally), those existing methods would otherwise leak the Room's role name and live binding status. This task also patches `console/agent.py`'s three view-building methods to filter `RoleClass.ROOM` out — see Step 3b below.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_console_protocol.py` (check its existing imports/style first, then add):

```python
import pytest

from console.protocol import ArmRoomCommand, ReleaseRoomCommand, parse_admin_command


def test_parse_admin_command_arm_room_with_default_window():
    command = parse_admin_command({"command": "arm_room", "room_type": "TEST"})
    assert command == ArmRoomCommand(room_type="TEST", window_seconds=30.0)


def test_parse_admin_command_arm_room_with_explicit_window():
    command = parse_admin_command(
        {"command": "arm_room", "room_type": "DEMO", "window_seconds": 45.0})
    assert command == ArmRoomCommand(room_type="DEMO", window_seconds=45.0)


def test_parse_admin_command_release_room():
    command = parse_admin_command({"command": "release_room", "room_type": "TEST"})
    assert command == ReleaseRoomCommand(room_type="TEST")


def test_parse_admin_command_rejects_missing_room_type():
    with pytest.raises(ValueError):
        parse_admin_command({"command": "arm_room"})


def test_parse_admin_command_rejects_unrecognized_command():
    with pytest.raises(ValueError):
        parse_admin_command({"command": "not_a_real_command"})
```

Add to `tests/test_console_agent.py` (check its existing fixture/transport style first, then add — these follow the same `ConsoleAgent` + fake server pattern the existing `load_bit`/`run`/`abort` tests already use):

```python
from control.room_binding import RoomBindingRegistry
from control.rooms import Room, RoomType


def test_arm_room_arms_the_configured_room_binding():
    server = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    server.room = Room(room_type=RoomType.TEST)
    transport = FakeConsoleTransport()   # match the existing fixture name/shape
    agent = ConsoleAgent(server, transport)

    error = agent._handle_command({"command": "arm_room", "room_type": "TEST"})

    assert error is None
    assert server.room_binding.is_armed(RoomType.TEST) is True


def test_release_room_clears_the_binding():
    binding = RoomBindingRegistry()
    binding.bind(RoomType.TEST, "ie7")
    server = GameServer({"TestBit": TestBit}, room_binding=binding)
    server.room = Room(room_type=RoomType.TEST)
    transport = FakeConsoleTransport()
    agent = ConsoleAgent(server, transport)

    error = agent._handle_command({"command": "release_room", "room_type": "TEST"})

    assert error is None
    assert binding.bound_device(RoomType.TEST) is None


def test_arm_room_errors_when_no_room_configured():
    server = GameServer({"TestBit": TestBit})   # no room_binding, no room
    transport = FakeConsoleTransport()
    agent = ConsoleAgent(server, transport)

    error = agent._handle_command({"command": "arm_room", "room_type": "TEST"})

    assert error is not None
    assert error["event"] == "error"


def test_arm_room_errors_for_mismatched_room_type():
    server = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    server.room = Room(room_type=RoomType.TEST)
    transport = FakeConsoleTransport()
    agent = ConsoleAgent(server, transport)

    error = agent._handle_command({"command": "arm_room", "room_type": "DEMO"})

    assert error is not None


def test_snapshot_never_lists_the_room_role():
    server = GameServer({"RoomCapableBit": RoomCapableBit},
                        room_binding=RoomBindingRegistry())
    server.room = Room(room_type=RoomType.TEST)
    server.load_bit("RoomCapableBit")
    transport = FakeConsoleTransport()
    agent = ConsoleAgent(server, transport)

    snapshot = agent.snapshot()

    role_names = {r["role"] for r in snapshot["roles"]}
    assert "room_test" not in role_names
    registration_names = {r["role"] for r in snapshot["registration"]}
    assert "room_test" not in registration_names
    # the ordinary player/jam roles from TestBit's own role_table are
    # untouched by the filter
    assert "player" in role_names and "jammer" in role_names


def test_devices_view_hides_the_room_assignment():
    binding = RoomBindingRegistry()
    server = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding)
    server.room = Room(room_type=RoomType.TEST)
    server.load_bit("RoomCapableBit")
    server.hello("ie9", "Shroom Nine", "1")
    binding.arm(RoomType.TEST, window_seconds=10.0)
    server.join("ie9", "ROOM_TEST_NODE")
    transport = FakeConsoleTransport()
    agent = ConsoleAgent(server, transport)

    devices = agent._devices_view()

    ie9 = next(d for d in devices if d["dev"] == "ie9")
    assert ie9["role"] is None    # device is listed, but not as "room_test"
```

(`FakeConsoleTransport`/whatever the existing fixture is actually called — inspect `tests/test_console_agent.py`'s current tests for `load_bit`/`run`/`abort` and reuse the exact same server/transport double already defined there; do not invent a new one.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_console_protocol.py tests/test_console_agent.py -k "room" -v`
Expected: FAIL with `ImportError: cannot import name 'ArmRoomCommand' from 'console.protocol'`

- [ ] **Step 3: Write the implementation**

In `console/protocol.py`, add after the existing re-exports:

```python
from dataclasses import dataclass

from uplink.protocol import (  # re-exported: single source of truth
    AbortCommand,
    LoadBitCommand,
    RunCommand,
    bit_completed_event,
    error_event,
    parse_command,
    registration_changed_event,
    state_changed_event,
)

__all__ = [
    "AbortCommand", "LoadBitCommand", "RunCommand", "parse_command",
    "bit_completed_event", "error_event", "registration_changed_event",
    "state_changed_event", "role_view", "device_view", "snapshot_event",
    "devices_changed_event", "bit_status_event", "log_event",
    "ArmRoomCommand", "ReleaseRoomCommand", "parse_admin_command",
]


@dataclass
class ArmRoomCommand:
    room_type: str
    window_seconds: float = 30.0


@dataclass
class ReleaseRoomCommand:
    room_type: str


def parse_admin_command(msg: dict):
    """Console-only admin commands -- never sent by the uplink's remote
    broker. Kept separate from uplink.protocol.parse_command: Room
    registration is a local, trusted-operator action (design spec section
    7), not something a remote fairyring peer should ever request."""
    command = msg.get("command")
    if command == "arm_room":
        room_type = msg.get("room_type")
        if not isinstance(room_type, str):
            raise ValueError("arm_room requires a string 'room_type'")
        window = msg.get("window_seconds", 30.0)
        return ArmRoomCommand(room_type=room_type, window_seconds=float(window))
    if command == "release_room":
        room_type = msg.get("room_type")
        if not isinstance(room_type, str):
            raise ValueError("release_room requires a string 'room_type'")
        return ReleaseRoomCommand(room_type=room_type)
    raise ValueError(f"unrecognized admin command: {command!r}")
```

(Insert the new dataclasses/function after the existing `role_view`/`device_view`/etc. builders, keeping the file's existing content intact — only the `from dataclasses import dataclass` import and the `__all__` list change from what's already there.)

In `console/agent.py`, add an import:

```python
from control.rooms import RoomType
```

Change `_handle_command` (currently lines 35-51) by adding the admin-command check at the top:

```python
    def _handle_command(self, msg: dict) -> dict | None:
        name = msg.get("command")
        if name in ("arm_room", "release_room"):
            return self._handle_admin_command(msg)
        try:
            command = protocol.parse_command(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable console message: %s", exc)
            return None
        try:
            if isinstance(command, protocol.LoadBitCommand):
                self.game_server.load_bit(command.name)
            elif isinstance(command, protocol.RunCommand):
                self.game_server.run()
            elif isinstance(command, protocol.AbortCommand):
                self.game_server.abort()
        except (InvalidTransition, BitLoadError) as exc:
            return protocol.error_event(name, str(exc))
        return None

    def _handle_admin_command(self, msg: dict) -> dict | None:
        name = msg.get("command")
        try:
            command = protocol.parse_admin_command(msg)
        except ValueError as exc:
            return protocol.error_event(name, str(exc))
        try:
            room_type = RoomType[command.room_type]
        except KeyError:
            return protocol.error_event(
                name, f"unknown room_type {command.room_type!r}")
        gs = self.game_server
        if gs.room_binding is None or gs.room is None or gs.room.room_type != room_type:
            return protocol.error_event(
                name, f"no {command.room_type} Room configured")
        if isinstance(command, protocol.ArmRoomCommand):
            gs.room_binding.arm(room_type, command.window_seconds)
        elif isinstance(command, protocol.ReleaseRoomCommand):
            gs.room_binding.release(room_type)
        return None
```

- [ ] **Step 3b: Filter `RoleClass.ROOM` out of the Console's existing views**

Still in `console/agent.py`, add the import:

```python
from control.roles import RoleClass
```

Replace `snapshot()`:

```python
    def snapshot(self) -> dict:
        gs = self.game_server
        loaded_bit = None
        roles: list = []
        registration: list = []
        if gs.registration is not None:
            loaded_bit = self._loaded_bit_name()
            roles = [protocol.role_view(r)
                     for r in gs.registration.role_table.roles.values()
                     if r.role_class != RoleClass.ROOM]
            registration = protocol.registration_changed_event(
                self._non_room_counts())["roles"]
        return protocol.snapshot_event(
            state=gs.state.name,
            installed_bits=list(gs.bit_registry.keys()),
            loaded_bit=loaded_bit,
            roles=roles,
            registration=registration,
            devices=self._devices_view(),
            bit_status=self._current_status(),
        )

    def _non_room_counts(self):
        """RegistrationState.counts() has no role_class in its tuples, so
        the ROOM-class filter has to cross-reference role_table.roles by
        name. Never surface the Room's occupancy on any Console view --
        design spec section 7."""
        gs = self.game_server
        room_names = {r.name for r in gs.registration.role_table.roles.values()
                     if r.role_class == RoleClass.ROOM}
        return [c for c in gs.registration.counts() if c[0] not in room_names]
```

Replace `_devices_view()`:

```python
    def _devices_view(self) -> list:
        gs = self.game_server
        assignments = gs.registration.assignments if gs.registration else {}
        out = []
        for info in gs.devices.all():
            assigned = assignments.get(info.dev)
            role_name = None
            if assigned is not None and assigned[2] != RoleClass.ROOM:
                role_name = assigned[1]
            out.append(protocol.device_view(info, role_name))
        return out
```

(the device that's bound as the Room still appears in the list — it said hello like any other device — it just never shows `room_test` as its role.)

Replace `on_registration_change()`:

```python
    def on_registration_change(self) -> None:
        self.server.broadcast(
            protocol.registration_changed_event(self._non_room_counts()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_console_protocol.py tests/test_console_agent.py -v`
Expected: PASS (all tests, including the pre-existing ones — confirms the additive dispatch change is backward compatible)

- [ ] **Step 5: Run the full test suite one final time**

Run: `python -m pytest tests -v`
Expected: PASS (every test in the repo)

- [ ] **Step 6: Commit**

```bash
git add console/protocol.py console/agent.py tests/test_console_protocol.py tests/test_console_agent.py
git commit -m "feat(terrarium): add admin arm_room/release_room console commands"
```

---

## Post-Implementation Notes

- This plan deliberately builds **no renderer, no CLI entrypoint, and no simulator**. `boot()` and `shutdown()` are the seam a future harness script (Spec 2's job) drives with a real `simulator_factory`, real `known_device_connected` (backed by `DevicePool`), and a real `arco_command`.
- `RoomBridge`'s light/audio sinks are never populated with anything but `None` by this plan's `boot()` — Spec 2 supplies concrete `RoomLightSink`/`RoomAudioSink` implementations (simulator-backed, then later hardware-backed) and wires them into `room_bridge.bind(dev, light=..., audio=...)`.
- `RoomBindingRegistry.save()`/`.load()` exist and are tested, but nothing in this plan calls them from `boot()` — wiring an actual on-disk path (e.g. `~/.terrarium/room_binding.json`) into the boot sequence is a one-line addition left for whichever harness script (Spec 2) first needs a real persistent path, so it can pick a location appropriate to where it runs.
- **`Bit.room_manifest` naming resolution.** The design spec (section 3) describes a Bit-authored `room_manifest`. This plan implements that requirement by having a Bit merge a `room_role()`-built `Role` (Task 1) directly into its *existing* `role_table` — reusing `Role.light_manifest`/`ugen_manifest` verbatim rather than inventing a separate, parallel `room_manifest` attribute or a second validation module. This is a faithful, arguably more literal reading of "authored the same way `Role.light_manifest`/`ugen_manifest` are today" (spec section 2), and it is why no `control/room_config.py` module appears in this plan — `control/role_config.py`'s existing `validate_role_declarations()` already validates every role in `role_table.roles`, ROOM-class ones included, for free. If a future need arises for Room-scoped declarations that don't fit the `Role` shape, that is a new, separate design question, not an oversight here.
- **Cue routing to `RoomBridge` is intentionally left as harness glue, not new engine code.** The design spec (section 6) describes cues reaching `RoomBridge` "through a room-scoped sibling of the existing `on_light_cue` sink." This plan does not add that sibling sink: `GameServer.on_light_cue(dev, status, data1, data2)` is already dev-generic, so a Bit's verb handler targeting the Room just needs to know `gs.room.bound_dev` and address its cue tuple there, exactly as it would for any player device (see `bits/test_bit.py`'s `tilt` handler for the existing pattern). Whichever harness/transport eventually owns `gs.on_light_cue` (Spec 2's job, since it requires a real dev-to-bridge mapping) needs one `if dev == gs.room.bound_dev: room_bridge.feed_midi(status, d1, d2)` branch alongside its normal per-device routing. Building a dedicated sink now, with no real consumer to prove its shape, would be speculative — flagging it here so it isn't silently missing when Spec 2 starts.
