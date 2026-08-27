# Terrarium Lifecycle and Config-Defined Rooms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Terrarium boots roomless and bitless; rooms come from a versioned `terrarium.toml` config (the `RoomType` enum is deleted); `load_room`/`unload_room` become operator-driven operations with a room-scoped teardown stack, an owned-pid stale sweep, Console/uplink surface, and provenance stamping.

**Architecture:** A new `TerrariumState` machine (`NO_ROOM → ROOM_LOADING → ROOM_READY → ROOM_UNLOADING`) in `control/terrarium.py` sits above the untouched Bit state machine. `control/terrarium_config.py` parses `terrarium.toml` (pure stdlib `tomllib`, located errors, same style as `control/bit_config.py`). Rooms are string names carrying a `RoomProfile` + node id; `Room` carries its own profile so the engine stops calling a global profile registry. `control/boot.py`'s body becomes `Terrarium.load_room`.

**Tech Stack:** Python 3.11+ stdlib only in `control/` (tomllib, dataclasses, hashlib). Tests: pytest, fully offline. Front-end: ES modules under `console/static/`, node `vm` tests under `tests/js/`.

**Spec:** `docs/superpowers/specs/2026-08-26-terrarium-lifecycle-and-config-rooms-design.md`

## Global Constraints

- Suite baseline **1362 passed, 1 skipped**; every task ends green. Run via `.venv/bin/python -m pytest tests -q` (fresh worktree: `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first; never bare `python3`).
- `control/` keeps **module-level imports stdlib + control/ only** (no luxaeterna/pyarco/o2litepy) — pinned by an existing test.
- Every outbound JSON payload goes through `control/wire_json.dumps()`, never bare `json.dumps`; never validate wire output with bare `json.loads`.
- Test doubles must never be more permissive than the real thing (boundary rule 5).
- Teardown invariant: anything registered later is torn down earlier; register on the stack in the same statement that spawns.
- Front-end standing rule: a high-frequency wire event must never rebuild a DOM subtree whose declaration has not changed.
- The Bit lifecycle state machine, cue path (`cue_horizon`, `TimedQueue`, `_dispatch_cues`), and both device transports are out of scope — do not touch their semantics.
- Never `git stash` bare in this worktree; use WIP commits.

---

### Task 1: `control/terrarium_config.py` + shipped `terrarium.toml`

**Files:**
- Create: `control/terrarium_config.py`
- Create: `terrarium.toml` (repo root)
- Test: `tests/test_terrarium_config.py`

**Interfaces:**
- Consumes: `control.room_profile.RoomProfile/RoomFixture/RoomBlock/RoomZone` (existing dataclasses, unchanged), `control.room_profile.ROOM_PROFILES` (golden comparison only, deleted in Task 3).
- Produces: `TerrariumConfig` (fields `schema: int`, `name: str`, `bit_paths: tuple[str, ...]`, `rooms: dict[str, RoomSpec]`, `version: str`), `RoomSpec` (fields `name: str`, `description: str`, `backends: tuple[str, ...]`, `node_id: str`, `profile: RoomProfile`, `arco_ready_timeout: float`, `arco_settle_seconds: float`), `load_terrarium_config(path) -> TerrariumConfig`, `parse_terrarium_config(text: str, source: str) -> TerrariumConfig`, `validate_rooms(config: TerrariumConfig, *, array_backend_configured: bool) -> dict[str, str | None]` (room name → None when loadable, else a reason string), `TerrariumConfigError(Exception)` with `.source`/`.key` located messages.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_terrarium_config.py
import pytest

from control.room_profile import ROOM_PROFILES
from control.rooms import RoomType
from control.terrarium_config import (
    TerrariumConfigError, load_terrarium_config, parse_terrarium_config,
    validate_rooms,
)

MINIMAL = """
schema = 1
[terrarium]
name = "t"
[rooms.ONE]
backends = ["devicelink"]
[[rooms.ONE.fixtures]]
name = "main"
color_order = "GRB"
[[rooms.ONE.fixtures.blocks]]
name = "b1"
start = 0
count = 10
[[rooms.ONE.fixtures.zones]]
name = "all"
start = 0
count = 10
"""


def test_minimal_config_parses():
    cfg = parse_terrarium_config(MINIMAL, source="inline")
    assert cfg.schema == 1
    assert cfg.name == "t"
    assert cfg.bit_paths == ("bits",)          # default
    spec = cfg.rooms["ONE"]
    assert spec.name == "ONE"
    assert spec.node_id == "ROOM_ONE_NODE"     # default shape
    assert spec.backends == ("devicelink",)
    assert spec.profile.pixel_count == 10
    assert spec.profile.fixtures[0].zones[0].name == "all"


def test_version_is_schema_plus_content_hash():
    a = parse_terrarium_config(MINIMAL, source="inline")
    b = parse_terrarium_config(MINIMAL + "\n# comment\n", source="inline")
    assert a.version.startswith("1-") and len(a.version) == 2 + 12
    assert a.version != b.version              # content-addressed


def test_unknown_backend_is_a_located_error():
    bad = MINIMAL.replace('backends = ["devicelink"]',
                          'backends = ["hologram"]')
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(bad, source="inline")
    assert "rooms.ONE" in str(exc.value) and "hologram" in str(exc.value)


def test_profile_validation_errors_are_located():
    # zone overruns the 10 px fixture -> RoomProfile's own ValueError,
    # wrapped with the room's config location.
    bad = MINIMAL.replace('count = 10\n"""'[:10], 'count = 10')  # no-op guard
    bad = MINIMAL.replace(
        '[[rooms.ONE.fixtures.zones]]\nname = "all"\nstart = 0\ncount = 10',
        '[[rooms.ONE.fixtures.zones]]\nname = "all"\nstart = 0\ncount = 99')
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(bad, source="inline")
    assert "rooms.ONE" in str(exc.value)


def test_shipped_config_matches_code_profiles_golden():
    cfg = load_terrarium_config("terrarium.toml")
    assert set(cfg.rooms) == {"TEST", "DEMO"}
    assert cfg.rooms["TEST"].profile == ROOM_PROFILES[RoomType.TEST]
    assert cfg.rooms["DEMO"].profile == ROOM_PROFILES[RoomType.DEMO]
    assert cfg.rooms["TEST"].backends == ("devicelink",)
    assert cfg.rooms["DEMO"].backends == ("devicelink", "array")
    assert cfg.rooms["TEST"].node_id == "ROOM_TEST_NODE"
    assert cfg.rooms["DEMO"].node_id == "ROOM_DEMO_NODE"


def test_validate_rooms_reports_per_room():
    cfg = load_terrarium_config("terrarium.toml")
    status = validate_rooms(cfg, array_backend_configured=False)
    assert status["TEST"] is None
    assert "array" in status["DEMO"]           # reason names the missing backend
    status = validate_rooms(cfg, array_backend_configured=True)
    assert status["DEMO"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -v`
Expected: FAIL with `ModuleNotFoundError: control.terrarium_config`

- [ ] **Step 3: Implement `control/terrarium_config.py`**

```python
"""Terrarium config: the valid-room set and bit search path, as data.
Schema v1. Pure stdlib (tomllib); located errors in the same style as
control/bit_config.py. See docs/superpowers/specs/
2026-08-26-terrarium-lifecycle-and-config-rooms-design.md section 2.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass

from control.room_profile import (RoomBlock, RoomFixture, RoomProfile,
                                  RoomZone)

KNOWN_BACKENDS = frozenset({"devicelink", "array"})


class TerrariumConfigError(Exception):
    def __init__(self, *, source: str, key: str, message: str) -> None:
        self.source = source
        self.key = key
        super().__init__(f"{source}: [{key}] {message}")


@dataclass(frozen=True)
class RoomSpec:
    name: str
    description: str
    backends: tuple[str, ...]
    node_id: str
    profile: RoomProfile
    arco_ready_timeout: float = 15.0
    arco_settle_seconds: float = 0.0


@dataclass(frozen=True)
class TerrariumConfig:
    schema: int
    name: str
    bit_paths: tuple[str, ...]
    rooms: dict[str, RoomSpec]
    version: str          # f"{schema}-{sha256(text)[:12]}", content-addressed


def load_terrarium_config(path: str) -> TerrariumConfig:
    with open(path, encoding="utf-8") as f:
        return parse_terrarium_config(f.read(), source=path)


def parse_terrarium_config(text: str, source: str) -> TerrariumConfig:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TerrariumConfigError(source=source, key="-",
                                   message=f"not valid TOML: {exc}") from exc
    schema = raw.get("schema")
    if schema != 1:
        raise TerrariumConfigError(source=source, key="schema",
                                   message=f"expected 1, got {schema!r}")
    terr = raw.get("terrarium", {})
    name = terr.get("name")
    if not isinstance(name, str) or not name:
        raise TerrariumConfigError(source=source, key="terrarium.name",
                                   message="required non-empty string")
    bit_paths = tuple(terr.get("bit_paths", ["bits"]))
    rooms_raw = raw.get("rooms")
    if not isinstance(rooms_raw, dict) or not rooms_raw:
        raise TerrariumConfigError(source=source, key="rooms",
                                   message="at least one [rooms.<NAME>] required")
    rooms: dict[str, RoomSpec] = {}
    for rname, rraw in rooms_raw.items():
        rooms[rname] = _parse_room(rname, rraw, source=source)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return TerrariumConfig(schema=schema, name=name, bit_paths=bit_paths,
                           rooms=rooms, version=f"{schema}-{digest}")


def _parse_room(rname: str, rraw: dict, *, source: str) -> RoomSpec:
    key = f"rooms.{rname}"
    backends = tuple(rraw.get("backends", []))
    unknown = [b for b in backends if b not in KNOWN_BACKENDS]
    if unknown:
        raise TerrariumConfigError(
            source=source, key=key,
            message=f"unknown backends {unknown}; known: {sorted(KNOWN_BACKENDS)}")
    fixtures = []
    for fraw in rraw.get("fixtures", []):
        blocks = tuple(RoomBlock(b["name"], b["start"], b["count"])
                       for b in fraw.get("blocks", []))
        zones = tuple(RoomZone(z["name"], z["start"], z["count"])
                      for z in fraw.get("zones", []))
        fixtures.append(RoomFixture(name=fraw["name"],
                                    color_order=fraw["color_order"],
                                    blocks=blocks, zones=zones))
    try:
        profile = RoomProfile(surface_id=f"room_{rname.lower()}",
                              fixtures=tuple(fixtures))
    except (ValueError, KeyError, TypeError) as exc:
        raise TerrariumConfigError(source=source, key=key,
                                   message=str(exc)) from exc
    arco = rraw.get("arco", {})
    return RoomSpec(
        name=rname,
        description=rraw.get("description", ""),
        backends=backends,
        node_id=rraw.get("node_id", f"ROOM_{rname}_NODE"),
        profile=profile,
        arco_ready_timeout=float(arco.get("ready_timeout", 15.0)),
        arco_settle_seconds=float(arco.get("settle_seconds", 0.0)),
    )


def validate_rooms(config: TerrariumConfig, *,
                   array_backend_configured: bool) -> dict[str, str | None]:
    """Per-room loadability, boot-time. None = loadable; else the reason.
    The room actually being loaded fails hard on its reason
    (control/terrarium.py); the rest of the set is advisory, surfaced on
    the Console rooms panel and CLI listings. No silent downgrade."""
    out: dict[str, str | None] = {}
    for name, spec in config.rooms.items():
        if "array" in spec.backends and not array_backend_configured:
            out[name] = f"{name} requires an array backend, none configured"
        else:
            out[name] = None
    return out
```

- [ ] **Step 4: Write `terrarium.toml`** (repo root; values transcribed from `control/room_profile.py`'s `ROOM_PROFILES` — TEST: `main` 60 px block `main`, zones left/center/right 20 px each, plus `accent` 30 px block `accent`, zones low/high 15 px each; DEMO: `array`, blocks m1..m6 at 144 px each, zones left/center/right at 288 px each; all `color_order = "GRB"`)

```toml
schema = 1

[terrarium]
name = "dev-box"
bit_paths = ["bits"]

[rooms.TEST]
description = "Simulated dev room: main + accent strips"
backends = ["devicelink"]

  [[rooms.TEST.fixtures]]
  name = "main"
  color_order = "GRB"
    [[rooms.TEST.fixtures.blocks]]
    name = "main"
    start = 0
    count = 60
    [[rooms.TEST.fixtures.zones]]
    name = "left"
    start = 0
    count = 20
    [[rooms.TEST.fixtures.zones]]
    name = "center"
    start = 20
    count = 20
    [[rooms.TEST.fixtures.zones]]
    name = "right"
    start = 40
    count = 20

  [[rooms.TEST.fixtures]]
  name = "accent"
  color_order = "GRB"
    [[rooms.TEST.fixtures.blocks]]
    name = "accent"
    start = 0
    count = 30
    [[rooms.TEST.fixtures.zones]]
    name = "low"
    start = 0
    count = 15
    [[rooms.TEST.fixtures.zones]]
    name = "high"
    start = 15
    count = 15

[rooms.DEMO]
description = "Real-scale 6 m / 864 px Terrarium array, simulated backend"
backends = ["devicelink", "array"]

  [[rooms.DEMO.fixtures]]
  name = "array"
  color_order = "GRB"
    [[rooms.DEMO.fixtures.blocks]]
    name = "m1"
    start = 0
    count = 144
    [[rooms.DEMO.fixtures.blocks]]
    name = "m2"
    start = 144
    count = 144
    [[rooms.DEMO.fixtures.blocks]]
    name = "m3"
    start = 288
    count = 144
    [[rooms.DEMO.fixtures.blocks]]
    name = "m4"
    start = 432
    count = 144
    [[rooms.DEMO.fixtures.blocks]]
    name = "m5"
    start = 576
    count = 144
    [[rooms.DEMO.fixtures.blocks]]
    name = "m6"
    start = 720
    count = 144
    [[rooms.DEMO.fixtures.zones]]
    name = "left"
    start = 0
    count = 288
    [[rooms.DEMO.fixtures.zones]]
    name = "center"
    start = 288
    count = 288
    [[rooms.DEMO.fixtures.zones]]
    name = "right"
    start = 576
    count = 288
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -v` then the full suite `.venv/bin/python -m pytest tests -q`
Expected: new tests PASS; suite stays at baseline + new.

- [ ] **Step 6: Commit**

```bash
git add control/terrarium_config.py terrarium.toml tests/test_terrarium_config.py
git commit -m "feat(terrarium): schema-v1 terrarium.toml config with golden parity to code profiles"
```

---

### Task 2: `Room` carries its own profile and node id

Decouples the engine from the global profile registry so Task 3 can delete it. Behavior-preserving.

**Files:**
- Modify: `control/rooms.py` (the `Room` dataclass, `room_role`)
- Modify: `control/engine.py:301-315` (`_canonical_room_dev`), `control/engine.py:336-360` (`_resolve_target`), `control/engine.py:217-237` (`_is_room_node`/`_room_armed`/`_bind_room` — unchanged logic, but see below)
- Modify: `control/boot.py` (construct `Room` with profile), `console/agent.py:172-193` (`_current_room`), `devicelink/agent.py:193-203` (`_setup_room` profile lookup)
- Test: `tests/test_rooms.py` (existing file; extend)

**Interfaces:**
- Produces: `Room(room_type: RoomType, profile: RoomProfile, node_id: str, bound: dict[str, str])` — `profile` and `node_id` are new required-with-defaults fields (`profile: RoomProfile | None = None`, `node_id: str = ""`, so existing constructions keep working until Task 3 tightens them); `Room.fully_bound()` takes no argument when `self.profile` is set (keep the one-arg form working: `def fully_bound(self, profile=None)` uses `profile or self.profile`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_rooms.py
from control.room_profile import ROOM_PROFILES
from control.rooms import Room, RoomType


def test_room_carries_profile_and_node_id():
    room = Room(room_type=RoomType.TEST,
                profile=ROOM_PROFILES[RoomType.TEST],
                node_id="ROOM_TEST_NODE")
    assert room.profile.pixel_count == 90
    assert not room.fully_bound()
    room.bound["main"] = "d1"
    room.bound["accent"] = "d2"
    assert room.fully_bound()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rooms.py -v -k carries`
Expected: FAIL with `TypeError` (unexpected keyword `profile`)

- [ ] **Step 3: Implement**

In `control/rooms.py`, extend `Room`:

```python
@dataclass
class Room:
    room_type: RoomType
    profile: "RoomProfile | None" = None
    node_id: str = ""
    bound: dict[str, str] = field(default_factory=dict)

    def fully_bound(self, profile=None) -> bool:
        p = profile if profile is not None else self.profile
        return all(f.name in self.bound for f in p.fixtures)
```

Then replace every engine-side `room_profile(self.room.room_type)` call with `self.room.profile`, guarded on None falling back to the old lookup during this task only:

- `control/engine.py` `_canonical_room_dev` and `_resolve_target`: `profile = self.room.profile or room_profile(self.room.room_type)`.
- `console/agent.py` `_current_room`: same fallback shape.
- `devicelink/agent.py` `_setup_room` (line ~199): `self._room_profile = room.profile or room_profile(room.room_type)`.
- `control/boot.py`: construct `Room(room_type=room_type, profile=<the room_profile(room_type) result or None on NotImplementedError>, node_id=ROOM_NODE_IDS[room_type])` at line 82, keeping the existing `NotImplementedError` tolerance.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green (this task is pure plumbing; any failure means a missed call site — grep `room_profile(` under `control/ console/ devicelink/`).

- [ ] **Step 5: Commit**

```bash
git add control/rooms.py control/engine.py control/boot.py console/agent.py devicelink/agent.py tests/test_rooms.py
git commit -m "refactor(rooms): Room carries its own profile and node id"
```

---

### Task 3: Delete the `RoomType` enum — rooms are config names

The big mechanical migration. Everything green at the end of THIS task, not partway; expect to touch ~15 files and a wide swath of tests. Work top-down from `control/rooms.py`.

**Files:**
- Modify: `control/rooms.py` (delete `RoomType`, `RoomRecipe`, `ROOM_RECIPES`, `ROOM_NODE_IDS`, `resolve_room_type`; `Room.name: str` replaces `room_type`), `control/room_profile.py` (delete `ROOM_PROFILES` and `room_profile()`; keep the dataclasses), `control/room_binding.py` (str keys), `control/boot_config.py` (`room_name: str` replaces `room_type: RoomType`), `control/bit.py` (`room_types: set[str] = {"TEST"}`, new `room_manifests()` hook), `control/engine.py` (ROOM-role synthesis in `load_bit`), `control/boot.py`, `console/agent.py`, `devicelink/agent.py`, `bits/test/test_bit.py`, `bits/metronome/metronome_bit.py`, `harness/terrarium_boot.py`, `harness/run_stack.py`, `harness/room_simulator.py`, `harness/o2_shroom.py`
- Test: update every test importing `RoomType` (grep `RoomType` under `tests/`), plus new tests below.

**Interfaces:**
- Produces:
  - `control/rooms.py`: `Room(name: str, profile: RoomProfile, node_id: str, bound: dict[str, str] = ...)`; `room_role_name(room_name: str) -> str` (returns `f"room_{room_name.lower()}"` — unchanged output for TEST/DEMO, so existing role names and blobs are byte-identical); `room_role(room: Room, *, ugen_manifest: dict | None = None, light_manifest: dict | None = None) -> tuple[str, Role, str]` (capacity from `len(room.profile.fixtures)`, node id from `room.node_id`); `non_room_counts` unchanged.
  - `control/room_binding.py`: every `room_type: RoomType` parameter becomes `room_name: str`; `save()`/`load()` keys are the same strings they already serialize (`room_type.name` today), so **the on-disk format is unchanged** — assert that in a test.
  - `control/bit.py`: `room_types: set[str] = {"TEST"}`; new instance hook `def room_manifests(self) -> tuple[dict, dict]: return ({}, {})` returning `(light_manifest, ugen_manifest)` for the Room role.
  - `control/engine.py` `load_bit`: after `role_table = bit.role_table`, when `self.room is not None` and `bit.room_manifests()` returns any non-empty manifest, synthesize and merge the ROOM role (code below). Bits stop merging Room roles themselves — this is what frees a Bit from knowing fixture counts, which are config data now.
- Consumes: `TerrariumConfig`/`RoomSpec` from Task 1 (harness resolves `--room NAME` → `RoomSpec` → `Room(name, spec.profile, spec.node_id)`).

- [ ] **Step 1: Write the failing tests for the new seams**

```python
# tests/test_engine_room_role_synthesis.py
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.rooms import Room, room_role_name
from bits.test.test_bit import TestBit


def make_room(name="TEST"):
    profile = RoomProfile(surface_id="room_x", fixtures=(
        RoomFixture(name="main", color_order="GRB",
                    blocks=(RoomBlock("main", 0, 10),),
                    zones=(RoomZone("all", 0, 10),)),))
    return Room(name=name, profile=profile, node_id=f"ROOM_{name}_NODE")


def test_load_bit_synthesizes_room_role_from_active_room():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = make_room()
    gs.load_bit("TestBit")
    rname = room_role_name("TEST")
    role = gs.registration.role_table.roles[rname]
    assert role.capacity == 1          # the room above has ONE fixture
    assert role.light_manifest         # TestBit's room_manifests light half
    assert gs.registration.role_table.node_map["ROOM_TEST_NODE"] == [rname]


def test_load_bit_without_room_declares_no_room_role():
    gs = GameServer({"TestBit": TestBit})
    gs.load_bit("TestBit")
    assert room_role_name("TEST") not in gs.registration.role_table.roles
```

```python
# append to tests/test_room_binding.py
def test_binding_file_format_is_unchanged_by_string_keys(tmp_path):
    from control.room_binding import RoomBindingRegistry
    reg = RoomBindingRegistry()
    reg.bind("TEST", "main", "d1")
    path = str(tmp_path / "b.json")
    reg.save(path)
    import json
    assert json.load(open(path)) == {"TEST": {"main": "d1"}}
    fresh = RoomBindingRegistry()
    fresh.load(path)
    assert fresh.bound_device("TEST", "main") == "d1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_room_role_synthesis.py -v`
Expected: FAIL (`Room` has no `name` kwarg; TestBit still merges its own room roles).

- [ ] **Step 3: Migrate, file by file, in this order**

1. `control/rooms.py`: delete `RoomType`, `RoomRecipe`, `ROOM_RECIPES`, `ROOM_NODE_IDS`, `resolve_room_type` (keep `RoomResolutionError` — `control/terrarium.py` reuses it in Task 4). `Room` becomes `name: str; profile: RoomProfile; node_id: str; bound: dict = field(default_factory=dict)`. `room_role_name(room_name: str)`. `room_role(room: Room, *, ...)` reading capacity/node off the room (no more local `room_profile` import — the circular-import note in its docstring dies with the registry).
2. `control/room_profile.py`: delete `ROOM_PROFILES` and `room_profile()`; module keeps only the dataclasses and `_MAX_PROFILE_PIXELS`.
3. `control/room_binding.py`: `RoomType` → `str` throughout; `save()` drops `.name` (keys already are the strings); `load()` drops `RoomType[name]`.
4. `control/bit.py`: `room_types: set[str] = {"TEST"}`; add

```python
def room_manifests(self) -> tuple[dict, dict]:
    """(light_manifest, ugen_manifest) for the active Room's synthesized
    ROOM-class role. Empty dicts (the default) mean this Bit declares no
    Room instruments and no ROOM role is merged. The Bit no longer builds
    the Role itself: capacity (fixture count) and the node id are config
    data the engine holds, not something a Bit can know."""
    return ({}, {})
```

5. `control/engine.py` `load_bit`, inside the existing try block after `role_table = bit.role_table`:

```python
if self.room is not None:
    light_m, ugen_m = bit.room_manifests()
    if light_m or ugen_m:
        from control.rooms import room_role
        rname, role, node = room_role(self.room, ugen_manifest=ugen_m,
                                      light_manifest=light_m)
        role_table.roles[rname] = role
        role_table.node_map[node] = [rname]
```

(Module-top import, not function-scoped — no circularity remains.) Also `_room_armed`/`_bind_room`/`_canonical_room_dev`/`_resolve_target`: `self.room.room_type` → `self.room.name`; drop the Task 2 `or room_profile(...)` fallbacks (registry is gone); delete the `from control.room_profile import room_profile` import.
6. `bits/test/test_bit.py`: `room_types = {"TEST", "DEMO"}`; delete the `room_entries` merge block from `role_table` (roles/node_map keep only player/jammer); move the `room_light`/`room_ugen` dicts into `def room_manifests(self): return (room_light, room_ugen)`. `bits/metronome/metronome_bit.py`: `room_types = {"DEMO"}`, same extraction of its Room manifests into `room_manifests()`.
7. `control/boot_config.py`: `room_type: RoomType` → `room_name: str`; drop the `RoomType` import.
8. `control/boot.py`: signature gains `room_spec: RoomSpec` (from Task 1) replacing the resolve step: `room = Room(name=room_spec.name, profile=room_spec.profile, node_id=room_spec.node_id)`; the recipe check becomes `if "array" in room_spec.backends and not config.array_backend_configured: raise BootFailure(...)`. The `bit_cls.room_types` gate compares `room.name not in bit_cls.room_types`. Every `room_binding.*(room.room_type, ...)` → `room.name`. The `NotImplementedError` tolerance paths die (a `RoomSpec` always has a profile).
9. `console/agent.py`: drop `RoomType` import; `_handle_admin_command` validates `command.room_type` (wire field name kept for protocol stability) against `gs.room.name`; `_current_room` uses `gs.room.profile` and `room_role_name(gs.room.name)`.
10. `devicelink/agent.py`: `room_role_name(room.name)`, `room.profile`.
11. `harness/terrarium_boot.py` / `harness/run_stack.py` / `harness/room_simulator.py` / `harness/o2_shroom.py`: the `--room-type` flag keeps its name FOR THIS TASK (renamed in Task 7); its value stops being `RoomType[name]` and becomes the string, resolved through `load_terrarium_config("terrarium.toml").rooms[name]` (with a located error listing valid names on a miss). `run_stack` passes the string through unchanged.
12. Tests: `grep -rl RoomType tests/` and migrate each — most are `RoomType.TEST` → `"TEST"` plus `Room(...)` constructions gaining `name=`/`profile=`/`node_id=`. Rewrite Task 1's golden test to compare against **inline expected literals** (pixel counts, zone tuples) instead of `ROOM_PROFILES`, since the registry no longer exists; the shipped `terrarium.toml` is now the single source of truth.

- [ ] **Step 4: Run the full suite until green**

Run: `.venv/bin/python -m pytest tests -q` and `grep -rn "RoomType\|room_profile(" --include="*.py" control/ console/ devicelink/ harness/ bits/ | grep -v "RoomProfile"`
Expected: suite green; grep returns nothing.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(rooms)!: delete RoomType enum; rooms are terrarium.toml names, engine synthesizes the ROOM role"
```

---

### Task 4: `control/terrarium.py` — the Terrarium state machine, `load_room`/`unload_room`

**Files:**
- Create: `control/terrarium.py`
- Modify: `control/boot.py` (its body moves; keep `wait_for_room_binding`, `_bind_room_fast_path`, `_canonical_room_dev`, `_abort_if_running`, `RoomBindingTimeout`, `BootFailure` — `Terrarium` imports them; delete the `boot()` function itself once `harness/terrarium_boot.py` is off it in Task 7 — for THIS task, reimplement `boot()` as a thin wrapper over `Terrarium` so the harness keeps working unchanged)
- Test: `tests/test_terrarium.py`

**Interfaces:**
- Produces:

```python
class TerrariumState(Enum):
    NO_ROOM; ROOM_LOADING; ROOM_READY; ROOM_UNLOADING

class RoomLoadError(Exception): ...   # every load failure, post-unwind

class Terrarium:
    def __init__(self, config: TerrariumConfig, game_server: GameServer,
                 room_binding: RoomBindingRegistry, *,
                 boot_config: BootConfig, arco_command: list,
                 arco_process_cls=ArcoProcess, simulator_factory=None,
                 known_device_connected=lambda dev: False, tick=None,
                 sweep=None, ownership_probe=None,
                 binding_store_path: str | None = None) -> None
    state: TerrariumState          # starts NO_ROOM
    room: Room | None              # active room, None outside ROOM_READY
    room_stack: TeardownStack | None
    def load_room(self, name: str) -> str | None      # None=loaded, else refusal reason
    def unload_room(self, force: bool = False) -> str | None
    def add_observer(self, observer) -> None          # on_terrarium_state_change(old,new),
                                                      # on_room_load_progress(stage: str)
```

- `load_room` sequence (each stage emits `on_room_load_progress`): refuse unless NO_ROOM → validate name/backends via `validate_rooms` (fail-hard on this room's reason) → `sweep()` (Task 5's callable; None skips) → `ownership_probe()` (None skips; a truthy return = foreign claimant, refuse with its message) → fresh `TeardownStack` → spawn Arco (`arco_process_cls`, `spec.arco_ready_timeout`) → `Room(name, spec.profile, spec.node_id)`; `gs.room = room`; `binding_store_path` and `room_binding.load(path)` before the fast path → `_bind_room_fast_path` → `RoomBridge` bind + push → state ROOM_READY. Any exception: `room_stack.close()`, `gs.room = None`, state NO_ROOM, return the reason (never raise to the Console path — mirror `GameServer.fire_trigger`'s never-raises contract).
- `unload_room`: refuse unless ROOM_READY; refuse if `gs.state is not State.IDLE` unless `force` (then `gs.abort()` first); `room_binding.save(path)` when a store path is set; `room_stack.close()`; **clear `gs.devices`** (spec section 6: every device's clock died with the hub) via a new `DevicePool.clear()` method (add it, one line, plus a test in `tests/test_device_pool.py`); `gs.room = None`; state NO_ROOM.
- State changes notify observers AND `game_server._notify("on_terrarium_state_change", old, new)` is NOT used — the Terrarium has its own observer list (the engine stays ignorant of the level above it); `ConsoleAgent` registers with both (Task 6).

- [ ] **Step 1: Write the failing tests** (drive with fakes: `arco_process_cls` = a `FakeArco` recording start/shutdown order; `simulator_factory` registering a fake teardown step; `sweep`/`ownership_probe` as recording lambdas)

```python
# tests/test_terrarium.py  (representative cases -- write all of these)
def test_boots_in_no_room_and_refuses_load_bit_gating():   # gating via Task 6's agent; here: state only
def test_load_room_happy_path_reaches_room_ready_and_sets_gs_room():
def test_load_room_refused_outside_no_room():
def test_load_unknown_room_name_is_a_located_refusal():    # reason lists valid names
def test_load_room_missing_backend_fails_before_spawning_anything():
def test_ownership_probe_conflict_refuses_and_spawns_nothing():
def test_mid_load_failure_unwinds_room_stack_and_returns_to_no_room():
    # FakeArco.wait_ready raises; assert FakeArco.shutdown called, state NO_ROOM,
    # gs.room is None, and a SECOND load_room succeeds with a fresh stack
def test_unload_room_requires_bit_idle_unless_force():
def test_unload_room_closes_stack_saves_bindings_and_clears_device_pool():
def test_progress_stages_are_observed_in_order():
    # ["validating", "sweeping", "spawning arco", "binding fixtures", "room ready"]
def test_second_ctrl_c_style_failure_in_one_step_does_not_abandon_the_rest():
    # a room-stack step raising BaseException still lets later-pushed steps run
    # (TeardownStack already guarantees this; assert through Terrarium anyway)
```

- [ ] **Step 2: Run to verify failures** — `ModuleNotFoundError: control.terrarium`.

- [ ] **Step 3: Implement `control/terrarium.py`** per the interface block above. `boot()` in `control/boot.py` becomes:

```python
def boot(config, bit_registry, *, arco_command, room_binding, room_spec,
         terrarium_config, **kwargs):
    """Compat wrapper: one-shot load_room + load_bit, returning the old
    4-tuple. Deleted in Task 7 when harness/terrarium_boot.py drives
    Terrarium directly."""
```

constructing a `Terrarium`, calling `load_room(room_spec.name)` (raising `BootFailure(reason)` on refusal), then `gs.load_bit(...)` exactly as before, returning `(gs, terrarium.room_bridge, terrarium.arco, terrarium.room_stack)`.

- [ ] **Step 4: Full suite green.** Run: `.venv/bin/python -m pytest tests -q`

- [ ] **Step 5: Commit**

```bash
git add control/terrarium.py control/boot.py control/device_pool.py tests/test_terrarium.py tests/test_device_pool.py
git commit -m "feat(terrarium): TerrariumState machine with load_room/unload_room and room-scoped teardown"
```

---

### Task 5: `control/run_record.py` — owned-pid records and the stale sweep

**Files:**
- Create: `control/run_record.py`
- Modify: `control/terrarium.py` (default `sweep=` wiring), `control/arco_process.py` + `control/simulator_process.py` (record on spawn — via a `record=` callable parameter defaulting to None, threaded from `Terrarium`; do NOT import run_record inside them)
- Test: `tests/test_run_record.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class SpawnRecord:
    pid: int; spawn_time: float; role: str        # role: "arco"|"simulator:<fixture>"

class RunRecorder:
    def __init__(self, path: str) -> None          # runs/<run-id>/procs.jsonl
    def record(self, pid: int, role: str, *, spawn_time: float) -> None   # append one JSON line
    @staticmethod
    def load_all(runs_dir: str) -> list[SpawnRecord]   # every procs.jsonl under runs_dir

def sweep_stale(runs_dir: str, *, stop=stop_process,
                process_spawn_time=_default_spawn_time,
                is_alive=_default_is_alive) -> list[SpawnRecord]
```

- `sweep_stale` rules (spec section 5, each its own test): only pids present in records; skip a pid that is not alive; skip a pid whose current process spawn time differs from the recorded one beyond 2 s (pid reuse — `process_spawn_time(pid)` reads `psutil`-free via `os.kill(pid, 0)` liveness plus, on darwin/linux, `subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)])` in the default helper; the injected fakes are what tests use); kill via the existing bounded `control/process.py:stop_process`; consume (delete) each record file whose pids are all handled. **Never** matches by name.
- The fake process table used in tests refuses to "kill" a pid it never spawned (boundary rule 5).

- [ ] **Step 1: Failing tests** — cover: record/append/load round-trip through `wire_json.dumps`; sweep kills recorded+alive+time-matching pid; skips dead pid; skips reused pid; never touches an unrecorded pid (fake raises if asked); record files consumed after a clean sweep.
- [ ] **Step 2: Verify failures.**
- [ ] **Step 3: Implement**, then wire: `Terrarium.__init__` builds `RunRecorder(os.path.join(runs_dir, run_id, "procs.jsonl"))` when given `runs_dir` (new optional param, default None = no recording, tests unaffected), passes `record=` into `arco_process_cls(...)`/`simulator_factory`, and defaults `sweep=lambda: sweep_stale(runs_dir)` when `runs_dir` is set.
- [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `feat(terrarium): owned-pid run records and load-time stale sweep`

---

### Task 6: Console + uplink surface — rooms panel data, commands, gating

**Files:**
- Modify: `uplink/protocol.py` (add `LoadRoomCommand(name: str)`, `UnloadRoomCommand(force: bool = False)` to `parse_command`; events `room_loaded_event(name)`, `room_unloaded_event(name)`, `room_load_failed_event(name, reason)`, `room_load_progress_event(stage)`; `state_changed_event` gains `terrarium_state: str | None = None` keyword), `console/protocol.py` (re-export; `snapshot_event` gains `terrarium_state`, `rooms` — the valid-room list `[{"name","description","status","active"}]`), `console/agent.py` (`__init__` gains `terrarium=None`; handle the two commands; register as a Terrarium observer; snapshot fields), `uplink/link.py` (resync carries terrarium_state + active room; relay the three room events)
- Test: `tests/test_console_agent.py`, `tests/test_uplink_protocol.py` (extend)

**Interfaces:**
- Consumes: `Terrarium` from Task 4 (`state`, `room`, `config`, `load_room(name) -> str | None`, `unload_room(force) -> str | None`, `add_observer`), `validate_rooms` from Task 1.
- Produces: wire events named above; `ConsoleAgent.on_terrarium_state_change(old, new)` broadcasting `state_changed_event(gs.state.name, gs.bit_name, terrarium_state=new.name)` plus `room_loaded`/`room_unloaded`; `on_room_load_progress(stage)` broadcasting `room_load_progress_event(stage)`.

- [ ] **Step 1: Failing tests** — parse round-trip for both commands (including malformed refusals); a `load_room` command on an agent with `terrarium=None` returns `error_event`; happy-path command drives `terrarium.load_room` and a refusal reason comes back as `error_event` (never an exception); snapshot carries `terrarium_state` and the rooms list with per-room `status` from `validate_rooms`; `load_bit` while `terrarium.state is not ROOM_READY` returns `error_event("load_bit", "no room loaded")` **from the agent** (agent-level gate in `_handle_command`, checked before `resolve_config`, so the engine's own `InvalidTransition` machinery is untouched); byte-shape of every new event pinned through `wire_json.dumps` raw-text assertions (no bare `json.loads`).
- [ ] **Step 2: Verify failures.**
- [ ] **Step 3: Implement.** Command placement: `load_room`/`unload_room` go in `uplink/protocol.py`'s shared `parse_command` (spec section 8 gives the uplink both). `UplinkAgent` maps them to `terrarium.load_room`/`unload_room` the same way `LoadBitCommand` maps today, refusals as `error` events.
- [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `feat(console,uplink): room load/unload commands, rooms snapshot, terrarium-state gating`

---

### Task 7: Harness — `terrarium_boot` drives `Terrarium`; roomless idle; markers; `run_stack` forwarding

**Files:**
- Modify: `harness/terrarium_boot.py` (argparse at ~line 675: add `--config PATH` default `terrarium.toml`, rename `--room-type` → `--room NAME` with no `choices=` — valid names come from the config, unknown name = located error listing them; `build()` constructs a `Terrarium` and calls `load_room` instead of `boot()`; a launch with no `--room` and a console port enters a NO_ROOM wait loop — same drain-Arco's-pty discipline is moot since Arco isn't up, but the loop must still `agent.poll()` the transport and console at ~20 Hz until `terrarium.state is ROOM_READY`, then fall into the existing `_wait_for_load`/`_serve_rounds` machinery; `_serve_rounds` returns to the NO_ROOM wait when the room is unloaded mid-serve), `control/boot.py` (delete the compat `boot()` wrapper; keep the helpers `Terrarium` imports or fold them into `control/terrarium.py` and delete `boot.py` entirely — prefer the fold+delete: one home), `harness/markers.py`, `harness/run_stack.py` (forward `--config`/`--room`; gate on the new marker)
- Test: `tests/test_markers.py`, `tests/test_terrarium_boot.py` (existing files; extend), `tests/test_run_stack.py`

**Interfaces:**
- Produces (markers, appended to `READY_MARKERS`):

```python
CONTROL_ROOM_LOADED = "room loaded:"      # printed once per successful load_room, "room loaded: TEST"
CONTROL_ROOM_UNLOADED = "room unloaded:"  # printed once per unload_room
```

- Progress stages print as `room loading: <stage>` lines (not markers — variable count, like `BROWSE_URL`'s reasoning).
- `run_stack` waits on `CONTROL_ROOM_LOADED` where it currently gates on boot completion, and its `--room`/`--config` flags forward verbatim into the `terrarium_boot` child command.

- [ ] **Step 1: Failing tests** — markers pinned to their emit sites (`tests/test_markers.py`'s existing source-scan pattern); `build()` with a `FakeArco`/fake factory reaches ROOM_READY and returns the same tuple shape its 17 unpack sites expect (extend the tuple, don't reorder: `(game_server, devicelink_server, devicelink_agent, arco_process, teardown, terrarium)` — grep every unpack site and add the sixth element); `--room BOGUS` exits with a message listing TEST and DEMO; no `--room` + console port waits in NO_ROOM (drive with a scripted console `load_room` command through the fake console server, assert the loop proceeds).
- [ ] **Step 2: Verify failures.**
- [ ] **Step 3: Implement.** Keep the teardown split honest: the devicelink server/console/uplink go on the Terrarium-scoped stack `main()` owns; everything `load_room` spawns goes on `terrarium.room_stack`. `main()`'s final unwind closes the room stack via `terrarium.unload_room(force=True)` (when ROOM_READY) then the process stack.
- [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `feat(harness): terrarium_boot drives Terrarium; roomless idle; room markers; run_stack forwarding`

---

### Task 8: Provenance stamping

**Files:**
- Modify: `control/role_config.py` (`compose_role_config(bit_name, bit_version, role, *, room_name: str | None = None, terrarium_config_version: str | None = None)` — stamped into the blob as `room_name`/`terrarium_config_version` keys when given, absent otherwise so pre-room blobs are byte-identical), `control/engine.py` (`GameServer` gains `self.provenance: dict = {}`; `Terrarium.load_room` sets `gs.provenance = {"room_name": name, "terrarium_config_version": config.version}` and `unload_room` clears it; `join()` passes it into `compose_role_config`; `TriggerFired` in `control/triggers.py` gains `room_name: str | None = None`, populated in `fire_trigger` from `self.provenance.get("room_name")`), `console/agent.py` + `uplink/link.py` (`bit_completed_event` gains the same two optional keys), `capture/store.py` (trace header gains them when the engine's provenance is non-empty — threaded via the existing store construction path in `bits/capture/capture_bit.py`)
- Test: `tests/test_role_config.py`, `tests/test_triggers.py`, `tests/test_capture_store.py` (extend)

- [ ] **Step 1: Failing tests** — blob carries both keys when provenance set and NEITHER key when not (byte-identical to today's composition, pinned on raw `wire_json.dumps` text); `TriggerFired.room_name` populated on a fire with a room and None without; capture trace JSON carries them.
- [ ] **Step 2: Verify failures.**  
- [ ] **Step 3: Implement.**  
- [ ] **Step 4: Full suite green.**  
- [ ] **Step 5: Commit** — `feat(provenance): room name and config version stamped into blobs, trigger records, results, traces`

---

### Task 9: Bit search path from config

**Files:**
- Modify: `control/bit_registry.py` (`BitRegistry.scan(roots: Iterable[Path] | None = None)` — today it scans the single `_DEFAULT_ROOT`; it takes an ordered iterable of roots, default `(_DEFAULT_ROOT,)`; a duplicate package name across roots is a located `PackageError` for the LATER root, first-root-wins; a nonexistent configured root is a `PackageError`, not a crash), `harness/terrarium_boot.py` + `harness/run_stack.py` (resolve roots from `TerrariumConfig.bit_paths`, relative paths anchored at the config file's own directory)
- Test: `tests/test_bit_registry.py` (extend)

- [ ] **Step 1: Failing tests** — two tmp roots each with a `bit.toml` package both discovered; duplicate name = later-root located error + first wins; missing root = located error, other roots still scanned; relative `bit_paths` anchored at the config file's directory (not the CWD).
- [ ] **Step 2: Verify failures.** **Step 3: Implement.** **Step 4: Suite green.**
- [ ] **Step 5: Commit** — `feat(bits): bit search roots come from terrarium.toml bit_paths`

---

### Task 10: Console front-end — rooms panel and gating

**Files:**
- Create: `console/static/rooms.js`
- Modify: `console/static/shell.js` (import + `initRooms`), `console/static/bit.js` (disable Load/Run/Abort outside `terrarium_state === "ROOM_READY"`), `console/static/index.html` (rooms panel container `<section id="roomsPanel">`), `console/server.py` only if the asset allowlist enumerates files (check `console/server.py`'s asset map and add `rooms.js`)
- Test: `tests/js/rooms_panel.test.js`, extend `tests/js/full_stack.test.js` (loads every module together)

**Interfaces:**
- Consumes: wire events from Task 6 (`snapshot.rooms`, `snapshot.terrarium_state`, `room_loaded`, `room_unloaded`, `room_load_failed`, `room_load_progress`, `state_changed.terrarium_state`), `wire.on/send/confirmTap`.
- Produces: `export function init()` (registers wire handlers), `export function renderRooms(rooms, terrariumState)`.

- [ ] **Step 1: Failing node tests** — one card per configured room keyed by name; a `room_load_progress` updates only the active card's status line, card list children preserved across it (identity check, same discipline as `triggers.js`); Load button sends `{command:"load_room", name}` and is disabled while a room is active; Unload uses `confirmTap` and survives a progress event without losing its armed state; an unloadable room (`status` non-null) renders the reason and a disabled Load; bit.js controls disabled outside ROOM_READY.
- [ ] **Step 2: Verify failures** (`node --test tests/js/rooms_panel.test.js`).
- [ ] **Step 3: Implement `rooms.js`** following `triggers.js`'s card-update-in-place pattern and `surface.js`'s `confirmTap` usage; wire dispatch additions in the module itself via `wire.on(...)` (shell only calls `init`).
- [ ] **Step 4: Run JS + Python suites green** (`tests/test_room_panel_behavior.py`-style wrapper if the repo pattern requires one — mirror how existing `tests/js/*.test.js` files are invoked from pytest and add `rooms_panel` to it).
- [ ] **Step 5: Commit** — `feat(console): rooms panel with load/unload and ROOM_READY gating`

---

### Task 11: CI cycle smoke, deep-dive doc, spec status

**Files:**
- Modify: `harness/run_stack.py` (`--ci` gains nothing new; instead add `tests/test_terrarium_cycle.py` — an OFFLINE full-cycle test driving `Terrarium` + `GameServer` + `ConsoleAgent` with fakes through: boot NO_ROOM → console `load_room TEST` → `load_bit TestBit` → run → complete → `unload_room` → `load_room DEMO` → assert fresh FakeArco instance (new object identity = new pid analog) → `unload_room` → clean end, DevicePool empty, both stacks closed)
- Modify: `docs/MM_TERRARIUM.md` (new Landed-subsystems section for this slice; move the superseded `RoomType`/`boot()` descriptions to past tense where the doc's own conventions do that; update the `RoomBindingRegistry.save()/load()` "not yet wired" deferred entry to closed)
- Modify: `docs/superpowers/specs/2026-08-26-terrarium-lifecycle-and-config-rooms-design.md` (Status section: implemented, live-verify checklist pending)

- [ ] **Step 1: Write the failing cycle test**, run it, watch it fail only if any wiring gap exists (it may pass immediately — that is the point of the task: an integration pin).
- [ ] **Step 2: Run the FULL suite one final time**: `.venv/bin/python -m pytest tests -q` — record the new baseline count for the doc.
- [ ] **Step 3: Update both docs** (deep-dive section includes the new baseline; the live-verify checklist from spec section 11 is copied into the spec's Status as unchecked boxes).
- [ ] **Step 4: Commit** — `docs(terrarium): lifecycle slice landed; cycle smoke pins the round trip`

---

## Live verification (human/operator, after merge — not a plan task)

**RUN ON: MYCOLOGICAL** (or whichever dev Mac has the arco checkout): spec section 11's six-step checklist, starting with `.venv/bin/python -m harness.run_stack --console-port 0 --devices 0` for the roomless boot and Console-driven `load_room` cycle.

## Self-review notes

- Spec coverage: section 1-3 → Tasks 2-4, 7; section 2 config → Task 1; section 4 stacks → Task 4/7; section 5 sweep → Task 5; section 6 devices → Task 4 (pool clear) + binding save/load in Task 4; section 7 harness/CLI → Task 7; section 8 console/uplink → Tasks 6, 10; section 9 non-goals → Global Constraints; section 10 provenance → Task 8; section 11 testing → per-task tests + Task 11 cycle pin; `bit_paths` (section 2/12 boundary) → Task 9.
- Type consistency: `Room(name, profile, node_id, bound)` introduced in Task 3 and consumed in Tasks 4-8; `load_room/unload_room -> str | None` refusal-reason convention consistent across Terrarium (4), ConsoleAgent (6), harness (7).
- Deliberate deviation from spec prose: the spec's `start_terrarium()` function is realized as `harness/terrarium_boot.py`'s existing `build()`/`main()` owning the Terrarium-scoped stack, rather than a new `control/`-level function — the harness already owns transport/console construction and a second constructor would duplicate its 17-site tuple contract.
