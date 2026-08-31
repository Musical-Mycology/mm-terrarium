# Instruments and Fixtures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Instrument a first-class entity (manifests + capabilities + functions + accepted triggers), unify it with `RoomFixture` as "a Fixture IS an Instrument a room loads", declare room instrument sets in `terrarium.toml`, instantiate a standard Tuneshroom instrument per joined carrier device, and resolve Bit-declared capability-contract requirements at `load_bit`/`join`.

**Architecture:** New pure module `control/instrument.py` (entity, vocabulary, contract matching, `TUNESHROOM`). `RoomFixture` gains an `instrument` field; `terrarium.toml` gains `[instruments.*]` tables referenced per fixture. `GameServer.load_bit` resolves room slots (implicit from `room_manifests()`, explicit from a new `Bit.instrument_requirements()`); `GameServer.join` gates role grants on a new `Role.requires` slot against the device's carried instrument. `DeviceLinkAgent` renders fixtures' ambient instrument manifests when no Bit is loaded. `fire_trigger` gates cue kinds on `accepted_triggers`. Console views source cards from the Instrument.

**Tech Stack:** Python 3 stdlib only in `control/` (no luxaeterna/pyarco imports — pinned by existing tests); pytest offline suite; node-based JS tests for `console/static/`.

**Spec:** `docs/superpowers/specs/2026-08-27-instruments-and-fixtures-design.md` — read it first; it carries the binding brainstorm decisions and the reasoning behind every choice below.

## Global Constraints

- Offline suite green at every task boundary: `.venv/bin/python -m pytest tests -q` (baseline 1442 passed, 1 skipped). Never invoke bare `python`/`python3` — the venv symlink is the interpreter.
- `control/` may not import luxaeterna or pyarco (module-level import ban is test-pinned).
- `GameServer.fire_trigger`, `Terrarium.load_room/unload_room` never raise — refusals are reason strings. `load_bit` failures are `BitLoadError` with located messages.
- Console one-list-instruments-discriminated-by-`kind` property must survive (test-pinned).
- No em dashes in any authored doc text; use `--`.
- Commit after every task (conventional commits, e.g. `feat(instrument): ...`).

---

### Task 1: `control/instrument.py` — the entity, vocabulary, matching, and TUNESHROOM

**Files:**
- Create: `control/instrument.py`
- Test: `tests/test_instrument.py`

**Interfaces:**
- Produces: `Instrument(name, description="", capabilities=frozenset(), functions=(), accepted_triggers=(), light_manifest={}, ugen_manifest={})` (frozen dataclass; dict fields via `field(default_factory=dict)`, compared but excluded from `__hash__` by `eq=True, frozen=True` with `unsafe_hash` avoided — set `@dataclass(frozen=True)` and do NOT rely on hashing instances; tests must not require hashability of Instrument itself).
- Produces: `CAPABILITY_VOCABULARY: frozenset[str]` = `{"light.pixels", "light.surface", "audio.flsyn", "audio.samples", "gesture.tap", "gesture.tilt"}`.
- Produces: `CUE_KINDS: tuple[str, ...]` = `("midi", "play", "solid", "mute")`.
- Produces: `InstrumentRequirement(slot: str, capabilities: frozenset[str], min_pixels: int = 0, optional: bool = False)` (frozen).
- Produces: `CarriedInstrument(instrument: Instrument, dev: str)` (frozen).
- Produces: `satisfies(instrument, requirement, *, pixel_count=None) -> str | None` (None = satisfied, else reason string).
- Produces: `validate_instrument(instrument) -> None` raising `InstrumentError(ValueError)` on unknown capability tag or unknown accepted-trigger kind.
- Produces: `TUNESHROOM: Instrument` (name `"tuneshroom"`, capabilities `{"light.pixels", "audio.samples", "gesture.tap", "gesture.tilt"}`, functions `("tap", "tilt")`, accepted_triggers `("midi", "play", "solid", "mute")`, empty manifests).

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_instrument.py"""
import pytest
from control.instrument import (
    CAPABILITY_VOCABULARY, CUE_KINDS, CarriedInstrument, Instrument,
    InstrumentError, InstrumentRequirement, TUNESHROOM, satisfies,
    validate_instrument,
)


def test_tuneshroom_is_the_standard_carrier_instrument():
    assert TUNESHROOM.name == "tuneshroom"
    assert "gesture.tap" in TUNESHROOM.capabilities
    assert TUNESHROOM.accepted_triggers == ("midi", "play", "solid", "mute")
    assert TUNESHROOM.light_manifest == {}
    validate_instrument(TUNESHROOM)  # the shipped standard always validates


def test_unknown_capability_tag_is_a_located_error():
    inst = Instrument(name="bogus", capabilities=frozenset({"light.warp"}))
    with pytest.raises(InstrumentError, match="light.warp"):
        validate_instrument(inst)


def test_unknown_accepted_trigger_kind_is_an_error():
    inst = Instrument(name="bogus", accepted_triggers=("laser",))
    with pytest.raises(InstrumentError, match="laser"):
        validate_instrument(inst)


def test_satisfies_capability_superset():
    req = InstrumentRequirement(slot="player",
                                capabilities=frozenset({"gesture.tap"}))
    assert satisfies(TUNESHROOM, req) is None


def test_satisfies_names_the_missing_capability():
    req = InstrumentRequirement(slot="room",
                                capabilities=frozenset({"light.surface"}))
    reason = satisfies(TUNESHROOM, req)
    assert reason is not None and "light.surface" in reason


def test_satisfies_min_pixels_checks_supplied_pixel_count():
    req = InstrumentRequirement(slot="room",
                                capabilities=frozenset(), min_pixels=100)
    assert satisfies(TUNESHROOM, req, pixel_count=864) is None
    reason = satisfies(TUNESHROOM, req, pixel_count=60)
    assert reason is not None and "100" in reason


def test_satisfies_min_pixels_without_pixel_count_refuses():
    req = InstrumentRequirement(slot="room",
                                capabilities=frozenset(), min_pixels=1)
    assert satisfies(TUNESHROOM, req) is not None


def test_carried_instrument_pairs_instrument_and_dev():
    c = CarriedInstrument(instrument=TUNESHROOM, dev="ie1")
    assert c.dev == "ie1" and c.instrument is TUNESHROOM


def test_vocabulary_and_cue_kinds_are_the_documented_sets():
    assert CAPABILITY_VOCABULARY == frozenset({
        "light.pixels", "light.surface", "audio.flsyn", "audio.samples",
        "gesture.tap", "gesture.tilt"})
    assert CUE_KINDS == ("midi", "play", "solid", "mute")
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_instrument.py -q`; expect ModuleNotFoundError.

- [ ] **Step 3: Implement `control/instrument.py`**

```python
"""Instruments: first-class entities a room loads (as fixtures) or a device
carries. Pure stdlib -- no luxaeterna, no pyarco (control/ discipline).

Spec: docs/superpowers/specs/2026-08-27-instruments-and-fixtures-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CAPABILITY_VOCABULARY: frozenset[str] = frozenset({
    "light.pixels",    # addressable pixels of any shape
    "light.surface",   # a linear multi-zone surface (Room-style array)
    "audio.flsyn",     # an Arco FluidSynth voice reachable
    "audio.samples",   # local sample playback
    "gesture.tap",
    "gesture.tilt",
})

CUE_KINDS: tuple[str, ...] = ("midi", "play", "solid", "mute")


class InstrumentError(ValueError):
    pass


@dataclass(frozen=True)
class Instrument:
    name: str
    description: str = ""
    capabilities: frozenset[str] = frozenset()
    functions: tuple[str, ...] = ()
    accepted_triggers: tuple[str, ...] = ()
    light_manifest: dict = field(default_factory=dict)
    ugen_manifest: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InstrumentRequirement:
    slot: str
    capabilities: frozenset[str]
    min_pixels: int = 0
    optional: bool = False


@dataclass(frozen=True)
class CarriedInstrument:
    instrument: Instrument
    dev: str


def validate_instrument(instrument: Instrument) -> None:
    unknown = set(instrument.capabilities) - CAPABILITY_VOCABULARY
    if unknown:
        raise InstrumentError(
            f"instrument {instrument.name!r}: unknown capability tag(s) "
            f"{sorted(unknown)}; known: {sorted(CAPABILITY_VOCABULARY)}")
    bad = [k for k in instrument.accepted_triggers if k not in CUE_KINDS]
    if bad:
        raise InstrumentError(
            f"instrument {instrument.name!r}: unknown accepted trigger "
            f"kind(s) {bad}; known: {list(CUE_KINDS)}")


def satisfies(instrument: Instrument, requirement: InstrumentRequirement,
              *, pixel_count: int | None = None) -> str | None:
    """None when the instrument satisfies the contract, else the reason.

    Matching is on contracts, never names (spec section 2)."""
    missing = requirement.capabilities - instrument.capabilities
    if missing:
        return (f"instrument {instrument.name!r} lacks capability "
                f"{sorted(missing)} required by slot {requirement.slot!r}")
    if requirement.min_pixels:
        if pixel_count is None:
            return (f"slot {requirement.slot!r} requires min_pixels="
                    f"{requirement.min_pixels} but no pixel count is known")
        if pixel_count < requirement.min_pixels:
            return (f"slot {requirement.slot!r} requires at least "
                    f"{requirement.min_pixels} pixels; {instrument.name!r} "
                    f"surface has {pixel_count}")
    return None


TUNESHROOM = Instrument(
    name="tuneshroom",
    description="Handheld 12-LED Tuneshroom (8-ring + 4-stem)",
    capabilities=frozenset({"light.pixels", "audio.samples",
                            "gesture.tap", "gesture.tilt"}),
    functions=("tap", "tilt"),
    accepted_triggers=("midi", "play", "solid", "mute"),
)
```

Note: `Instrument` has dict fields, so instances are unhashable under `frozen=True` default hashing rules? No — `frozen=True, eq=True` generates `__hash__` that would call `hash()` on dict fields and raise at hash time. Do not put Instruments in sets/dict keys anywhere in this plan; if a later task needs identity, key by `instrument.name`.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_instrument.py -q`; expect all pass.
- [ ] **Step 5: Commit** — `git add control/instrument.py tests/test_instrument.py && git commit -m "feat(instrument): first-class Instrument entity, capability contracts, TUNESHROOM"`

---

### Task 2: shared manifest validation callable for instruments

**Files:**
- Modify: `control/role_config.py` (expose light-manifest validation with a caller-supplied location prefix; `validate_ugen_manifest` already takes one — check its actual signature and mirror it)
- Modify: `control/instrument.py` (add `validate_instrument_manifests`)
- Test: `tests/test_instrument.py` (extend)

**Interfaces:**
- Consumes: Task 1's `Instrument`, `InstrumentError`.
- Produces: `control/role_config.validate_light_manifest(manifest: dict, where: str) -> None` — the existing per-role `_validate_light_manifest` logic refactored so a non-role caller can pass its own `where` prefix (e.g. `"instrument 'venue_array'"`). The role path keeps its exact current error text (existing tests pin it — adapt the refactor so role errors are byte-identical).
- Produces: `control/instrument.validate_instrument_manifests(instrument) -> None` calling both validators, wrapping failures in `InstrumentError`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_instrument.py`):

```python
from control.instrument import validate_instrument_manifests


def test_instrument_ambient_light_manifest_is_validated():
    inst = Instrument(name="arr", light_manifest={
        "instruments": [{"target": "primary"}]})  # missing "instrument"
    with pytest.raises(InstrumentError, match="arr"):
        validate_instrument_manifests(inst)


def test_instrument_ambient_ugen_manifest_is_validated():
    inst = Instrument(name="arr", ugen_manifest={
        "instruments": [{"program": 89}]})  # missing "instrument"
    with pytest.raises(InstrumentError, match="arr"):
        validate_instrument_manifests(inst)


def test_empty_ambient_manifests_validate():
    validate_instrument_manifests(TUNESHROOM)
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** In `role_config.py`, extract the body of `_validate_light_manifest` into `validate_light_manifest(manifest, where)` where `where` is the prefix used in messages; keep `_validate_light_manifest(role)` as a thin wrapper passing the current role-shaped prefix so all existing error strings are unchanged. In `instrument.py` (function-level import of `control.role_config` is fine and avoids cycles if any; prefer module-level if no cycle):

```python
def validate_instrument_manifests(instrument: Instrument) -> None:
    from control import role_config
    where = f"instrument {instrument.name!r}"
    try:
        if instrument.light_manifest:
            role_config.validate_light_manifest(
                instrument.light_manifest, where)
        if instrument.ugen_manifest:
            role_config.validate_ugen_manifest(
                instrument.ugen_manifest, where)
    except Exception as exc:
        raise InstrumentError(str(exc)) from exc
```

Adapt to `validate_ugen_manifest`'s real signature (read it first; if it does not take a `where`, give it the same optional-prefix treatment with role-path text unchanged).

- [ ] **Step 4: Run full suite** — `.venv/bin/python -m pytest tests -q`; expect baseline + new tests green (role_config error-text pins must not move).
- [ ] **Step 5: Commit** — `git commit -am "feat(instrument): shared manifest validation with instrument-shaped locations"`

---

### Task 3: `RoomFixture.instrument`

**Files:**
- Modify: `control/room_profile.py`
- Modify: every `RoomFixture(...)` construction site (`grep -rn "RoomFixture(" control tests harness` and fix all)
- Test: `tests/test_room_profile.py` (extend)

**Interfaces:**
- Consumes: Task 1's `Instrument`, `validate_instrument`; Task 2's `validate_instrument_manifests`.
- Produces: `RoomFixture(name, color_order, blocks, zones, instrument: Instrument)` — new REQUIRED field (no default: a fixture without an instrument is no longer representable, per spec section 3).
- Produces: `RoomProfile.__post_init__` additionally runs `validate_instrument` + `validate_instrument_manifests` per fixture, rewrapping `InstrumentError` as the profile's existing `ValueError` style with the fixture name in the message.

- [ ] **Step 1: Write failing tests** (extend `tests/test_room_profile.py`; also add a shared test helper if the suite builds fixtures in many places):

```python
from control.instrument import Instrument

GENERIC = Instrument(
    name="generic_surface",
    capabilities=frozenset({"light.surface", "audio.flsyn"}),
    accepted_triggers=("midi", "play", "solid", "mute"))


def test_fixture_carries_its_instrument():
    profile = make_profile()  # existing helper, updated to pass GENERIC
    assert profile.fixtures[0].instrument.name == "generic_surface"


def test_bad_fixture_instrument_fails_profile_construction():
    bad = Instrument(name="x", capabilities=frozenset({"nope.tag"}))
    with pytest.raises(ValueError, match="nope.tag"):
        make_profile(instrument=bad)
```

- [ ] **Step 2: Run; expect failures** (TypeError missing argument across the suite is expected at this point — that is the construction-site sweep signal).
- [ ] **Step 3: Implement.** Add the field; in `RoomProfile.__post_init__` after existing geometry checks:

```python
from control.instrument import (InstrumentError, validate_instrument,
                                validate_instrument_manifests)
...
for fixture in self.fixtures:
    try:
        validate_instrument(fixture.instrument)
        validate_instrument_manifests(fixture.instrument)
    except InstrumentError as exc:
        raise ValueError(
            f"fixture {fixture.name!r}: {exc}") from exc
```

Sweep every construction site: `control/terrarium_config.py` (temporarily pass a module-level `_PLACEHOLDER` generic instrument — Task 4 replaces it with real parsing), tests, and any harness builders. Define ONE shared test instrument in `tests/` (e.g. `tests/instrument_fixtures.py` exporting `GENERIC_SURFACE`) rather than N copies.

- [ ] **Step 4: Run full suite** — green.
- [ ] **Step 5: Commit** — `git commit -am "feat(room): a Fixture carries its Instrument (RoomFixture.instrument)"`

---

### Task 4: `terrarium.toml` instrument tables

**Files:**
- Modify: `control/terrarium_config.py`
- Modify: `terrarium.toml`
- Test: `tests/test_terrarium_config.py` (extend)

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `TerrariumConfig.instruments: dict[str, Instrument]` (new field); `[instruments.<name>]` tables parsed (keys: `description`, `capabilities` list, `functions` list, `accepted_triggers` list, optional `[instruments.<name>.ambient.light]` / `[...ambient.ugen]` tables becoming `light_manifest`/`ugen_manifest`); each fixture table's new required `instrument = "<name>"` key resolved to the parsed `Instrument` value. Missing key, unknown reference, unknown capability tag, malformed ambient manifest: each a located `TerrariumConfigError`. Remove Task 3's `_PLACEHOLDER`.

- [ ] **Step 1: Write failing tests** (extend, following the file's existing text-fixture style):

```python
def test_instruments_parse_and_resolve_onto_fixtures():
    cfg = parse_terrarium_config(CONFIG_WITH_INSTRUMENTS, "terrarium.toml")
    inst = cfg.instruments["venue_array"]
    assert inst.capabilities == frozenset({"light.surface", "audio.flsyn"})
    assert inst.light_manifest["instruments"][0]["instrument"] == "aurora"
    room = cfg.rooms["DEMO"]
    assert room.profile.fixtures[0].instrument is inst


def test_fixture_without_instrument_key_is_rejected():
    with pytest.raises(TerrariumConfigError, match="instrument"):
        parse_terrarium_config(CONFIG_MISSING_INSTRUMENT_KEY, "t.toml")


def test_unknown_instrument_reference_is_rejected():
    with pytest.raises(TerrariumConfigError, match="no_such"):
        parse_terrarium_config(CONFIG_BAD_REFERENCE, "t.toml")


def test_unknown_capability_tag_in_config_is_rejected():
    with pytest.raises(TerrariumConfigError, match="light.warp"):
        parse_terrarium_config(CONFIG_BAD_TAG, "t.toml")
```

(Author the four TOML text fixtures in the test file, copying the module's existing fixture style; `CONFIG_WITH_INSTRUMENTS` should mirror the spec section 3 example.)

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.** In `parse_terrarium_config`: parse `[instruments]` before rooms into `Instrument` values (lists -> frozenset/tuple; `ambient.light`/`ambient.ugen` -> manifests), run `validate_instrument`/`validate_instrument_manifests` per instrument rewrapping `InstrumentError` in `TerrariumConfigError` with the `[instruments.<name>]` location; in `_parse_room`'s fixture loop read `fraw["instrument"]`, look up in the parsed dict, `TerrariumConfigError` naming the reference and the known names on a miss. Add `instruments` to `TerrariumConfig`. Update `terrarium.toml`: add `[instruments.venue_array]` (spec example, ambient aurora + flsyn program 89 drone) and `[instruments.dev_strip]` (capabilities `["light.surface"]`, no ambient) and reference them from `rooms.DEMO.fixtures` (`venue_array`) and both `rooms.TEST` fixtures (`dev_strip`).
- [ ] **Step 4: Run full suite** — green (the repo `terrarium.toml` is itself parsed by tests; keep it valid).
- [ ] **Step 5: Commit** — `git commit -am "feat(config): rooms declare their instrument set in terrarium.toml"`

---

### Task 5: Bit requirements resolved at `load_bit`

**Files:**
- Modify: `control/bit.py` (add `instrument_requirements()`)
- Modify: `control/roles.py` (add `Role.requires: str | None = None`)
- Modify: `control/engine.py` (`load_bit` resolution)
- Test: `tests/test_engine_requirements.py` (new)

**Interfaces:**
- Consumes: `InstrumentRequirement`, `satisfies` (Task 1); `RoomFixture.instrument` (Task 3).
- Produces: `Bit.instrument_requirements() -> tuple[InstrumentRequirement, ...]` (base default `()`); reserved slot name `"room"`; `control/engine.py` helper `_resolve_room_requirements(bit, room) -> None` raising on no match (wrapped by `load_bit`'s existing try/except into `BitLoadError`).
- Resolution rules (spec section 4): implicit `"room"` slot synthesized when `room_manifests()` non-empty (light -> `{"light.surface"}`, ugen adds `"audio.flsyn"`) unless the Bit declares an explicit `"room"` slot; each non-optional requirement's capabilities must each be advertised by at least one fixture's instrument, `min_pixels` checked against `room.profile.pixel_count`; failure message includes each fixture's `satisfies` reason. Every `Role.requires` must name a declared slot (implicit `"room"` counts) — a typo raises.

- [ ] **Step 1: Write failing tests** (build a minimal room via the shared test instrument helper and the suite's existing GameServer construction pattern — copy from `tests/test_engine*.py`):

```python
def test_implicit_room_slot_fails_load_on_capability_gap(gs_with_light_only_room):
    # room fixtures advertise only {"light.surface"}; TestBit-like bit
    # declares non-empty ugen room manifest -> needs audio.flsyn
    with pytest.raises(BitLoadError, match="audio.flsyn"):
        gs_with_light_only_room.load_bit("RoomAudioBit")
    assert gs_with_light_only_room.state is State.IDLE


def test_explicit_room_slot_overrides_the_implication(...):
    # bit declares InstrumentRequirement(slot="room",
    #   capabilities={"light.surface"}) and non-empty ugen manifest:
    # loads fine against the light-only room


def test_min_pixels_checks_profile_pixel_count(...):
    # requirement min_pixels=1000 vs 90px room -> BitLoadError naming 1000


def test_optional_unresolved_slot_is_not_an_error(...):


def test_requires_naming_undeclared_slot_is_a_load_error(...):
    # a Role with requires="typo" -> BitLoadError mentioning "typo"


def test_bit_with_no_requirements_and_no_room_manifests_loads(...):
```

Flesh each `...` into a real test with small local Bit classes (the suite has many precedents of inline test Bits).

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement.** `Role.requires` field (default None, keeps every constructor working). In `bit.py`:

```python
def instrument_requirements(self):
    """Capability contracts this Bit demands (spec section 4). Room slots
    resolve at load_bit; a Role names a slot via Role.requires and the
    join gates on it. Base default: no demands."""
    return ()
```

In `engine.py` `load_bit`, after the ROOM-role synthesis and before `validate_role_declarations`:

```python
requirements = tuple(bit.instrument_requirements())
declared_slots = {r.slot for r in requirements}
if self.room is not None:
    implicit = None
    if (light or ugen) and "room" not in declared_slots:
        caps = set()
        if light:
            caps.add("light.surface")
        if ugen:
            caps.add("audio.flsyn")
        implicit = InstrumentRequirement(slot="room",
                                         capabilities=frozenset(caps))
    room_reqs = [r for r in requirements] + ([implicit] if implicit else [])
    _resolve_room_requirements(room_reqs, self.room)
for role in role_table.roles.values():
    if role.requires is not None and role.requires not in (
            declared_slots | {"room"}):
        raise ValueError(
            f"role {role.name!r} requires undeclared slot "
            f"{role.requires!r}; declared: {sorted(declared_slots)}")
```

where `light, ugen` are the `room_manifests()` values already fetched, and:

```python
def _resolve_room_requirements(requirements, room):
    profile = room.profile
    for req in requirements:
        if req.optional:
            continue
        reasons = []
        satisfied_caps = set()
        for fixture in profile.fixtures:
            r = satisfies(fixture.instrument, req,
                          pixel_count=profile.pixel_count)
            if r is None:
                satisfied_caps |= req.capabilities
                break
            reasons.append(r)
            satisfied_caps |= (req.capabilities
                              & fixture.instrument.capabilities)
        else:
            missing = req.capabilities - satisfied_caps
            if missing or req.min_pixels > profile.pixel_count:
                raise ValueError(
                    f"no fixture satisfies slot {req.slot!r}: "
                    + "; ".join(reasons))
```

Note the aggregate rule: capabilities may be satisfied across DIFFERENT fixtures (spec: "each advertised by at least one fixture's instrument") — implement exactly that: collect the union of advertised capabilities across fixtures, check `req.capabilities <= union` and `profile.pixel_count >= req.min_pixels`; per-fixture `satisfies` reasons feed the error text. Simplify the sketch accordingly rather than copying it blind.

- [ ] **Step 4: Run full suite** — green.
- [ ] **Step 5: Commit** — `git commit -am "feat(engine): Bit instrument requirements resolved at load_bit as capability contracts"`

---

### Task 6: carrier instruments and role-slot grant gating

**Files:**
- Modify: `control/device_pool.py` (`DeviceInfo.carried`)
- Modify: `control/engine.py` (`join` gating)
- Modify: `control/registration.py` (`JoinResult.slot`, `JoinResult.instrument`)
- Modify: `control/role_config.py` (`compose_role_config` stamps slot/instrument)
- Test: `tests/test_engine_requirements.py` (extend), `tests/test_role_config.py` (extend)

**Interfaces:**
- Consumes: `TUNESHROOM`, `satisfies`, `Role.requires` (Tasks 1, 5).
- Produces: `DeviceInfo.carried: Instrument = TUNESHROOM` (every hello'd device carries the standard Tuneshroom today; per-device seam for future kinds). `JoinResult.slot: str | None = None`, `JoinResult.instrument: str | None = None` (the instrument NAME — keep the dataclass wire-friendly). `compose_role_config(..., slot=None, instrument=None)` adds `"slot"`/`"instrument"` keys to the blob only when set.
- Grant rule: in `GameServer.join`, after `self.registration.join(...)` grants a non-ROOM role whose `Role.requires` is set, check `satisfies(carried, slot_requirement, pixel_count=None)`; on a reason, release the just-granted slot (`self.registration.release(dev)` — mirror the existing refusal paths) and return `JoinResult(granted=False, reason=<reason>)`. Prefer checking BEFORE the registration grant if the role can be resolved from the node first (read `_is_room_node`'s node->roles resolution and do the same); either order is acceptable if no slot leaks on refusal — pin that with a test.
- Slot requirements come from `self.bit.instrument_requirements()` captured at `load_bit` into `self._slot_requirements: dict[str, InstrumentRequirement]` (do not re-call the property per join — same snapshot hazard the file already documents for `role_table`).

- [ ] **Step 1: Write failing tests**

```python
def test_join_granted_when_carried_instrument_satisfies_slot(...):
    # role requires "player" slot {gesture.tap}; TUNESHROOM satisfies;
    # JoinResult.granted, .slot == "player", .instrument == "tuneshroom",
    # and result.config["slot"] == "player"


def test_join_refused_with_reason_when_contract_unsatisfied(...):
    # slot requires {"light.surface"}; TUNESHROOM lacks it; refused,
    # reason names light.surface, and the role's count is NOT consumed
    # (a second capable device can still join)


def test_role_without_requires_is_unchanged(...):
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement** per the interface block. In `load_bit`, alongside Task 5's resolution: `self._slot_requirements = {r.slot: r for r in requirements}`. In `join`, on the granted non-ROOM path before composing config:

```python
role = self.registration.role_table.roles[result.role]
if role.requires is not None:
    req = self._slot_requirements.get(role.requires)
    info = self.devices.get(dev)
    carried = getattr(info, "carried", None) or TUNESHROOM
    reason = satisfies(carried, req) if req else None
    if req is not None and reason is not None:
        self.registration.release(dev)
        return JoinResult(granted=False, reason=reason)
    result.slot = role.requires
    result.instrument = carried.name
result.config = compose_role_config(..., slot=result.slot,
                                    instrument=result.instrument)
```

(Adapt to the file's real accessors — `self.devices.get` may be `self.devices.get(dev)` returning `DeviceInfo | None`; read it. Note `role.requires` slots referencing the implicit `"room"` slot are illegal for non-ROOM roles only if no requirement exists — the Task 5 validation already guarantees `requires` names a declared or implicit slot; if it names the implicit `"room"` slot with no explicit requirement, treat `req is None` as satisfied.)

- [ ] **Step 4: Run full suite** — green.
- [ ] **Step 5: Commit** — `git commit -am "feat(engine): role grants bind carried instruments into requirement slots"`

---

### Task 7: ambient rendering from instrument manifests

**Files:**
- Modify: `control/instrument.py` (pure helper `ambient_manifests`)
- Modify: `devicelink/agent.py` (`_setup_room` falls back to ambient; state-change swap)
- Test: `tests/test_devicelink_room.py` or the file that currently covers `_setup_room`/`rewire_room` (find it: `grep -rln "_setup_room\|rewire_room" tests`); extend `tests/test_terrarium_cycle.py`

**Interfaces:**
- Consumes: `RoomFixture.instrument` (Task 3).
- Produces: `control/instrument.ambient_manifests(profile) -> tuple[dict, dict]` — concatenates each fixture's instrument's `light_manifest["instruments"]` / `ugen_manifest["instruments"]` lists in fixture order into one `(light, ugen)` pair (empty dicts when nothing declares anything). Deep-copy entries; never mutate config-held dicts.
- Behavior: `_setup_room` currently reads the ROOM role off `registration`; make it use the ROOM role's manifests when a Bit with a ROOM role is loaded, else `ambient_manifests(profile)` — building the same `LightSession` pipeline either way (compose a minimal blob: ambient needs `LightManifest.from_dict` input, i.e. `{"instruments": [...]}`; reuse `compose_role_config` only on the role path). Swap points: `rewire_room` (room load, no Bit -> ambient), the engine `on_state_change` observer the agent already registers (or its poll loop's state watch — read the file and use the existing seam): on entering `LOADED` rebuild from the Bit; on reaching `IDLE` after UNLOADING rebuild ambient. Ambient audio: if the ambient ugen manifest is non-empty and a room audio bridge exists, start the drone the same way the ROOM role path does at RUNNING — for this slice, ambient audio starts at room-ready and stops at bit-load swap/unload-room (mirror the light swap points; keep it inside the existing `_room_audio` guarded calls).
- Full-cycle pin: `tests/test_terrarium_cycle.py` gains the leg "room loaded, no bit: room session exists and renders ambient; load_bit: session renders the Bit's declaration; unload bit: ambient again".

- [ ] **Step 1: Write failing tests** — unit-test `ambient_manifests` (ordering, empties, deep-copy) in `tests/test_instrument.py`; behavior tests in the devicelink room test file using its existing fakes (assert `agent._room_light is not None` after `rewire_room` with no bit loaded, and that the session was built from the ambient declaration — assert on the manifest fed to the session factory via the file's existing fake/spy pattern); extend the cycle test.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement** per above. Keep `devicelink/agent.py` free of new engine coupling: it already holds `gs`; derive "bit with ROOM role loaded" from `gs.registration` exactly as `_setup_room` does today, with the ambient branch when that lookup finds nothing.
- [ ] **Step 4: Run full suite** — green.
- [ ] **Step 5: Commit** — `git commit -am "feat(room): fixtures render their instruments' ambient manifests when no Bit is loaded"`

---

### Task 8: `accepted_triggers` gating in `fire_trigger`

**Files:**
- Modify: `control/engine.py` (`fire_trigger`)
- Test: `tests/test_triggers_engine.py` or wherever `fire_trigger` is covered (`grep -rln "fire_trigger" tests`)

**Interfaces:**
- Consumes: `CUE_KINDS`; fixtures'/carried instruments (Tasks 3, 6).
- Produces: helper `control/instrument.cue_kind(cue) -> str` mapping `SolidCue -> "solid"`, `PlayCue -> "play"`, `MuteCue -> "mute"`, everything else (plain tuples, `LightCue`) -> `"midi"`. NOTE: `control/instrument.py` must not import `control.cues` at module level if that creates a cycle — check; if it does, put `cue_kind` in `control/cues.py` instead and have the engine import it from there (record which in the commit message).
- Rule (spec section 7): in `fire_trigger`, after `expand_script`, resolve each cue's destination surface's instrument — a device dev via `DeviceInfo.carried`, the Room's canonical dev via the room fixtures (any fixture instrument accepting the kind counts; the Room is one logical surface) — and if any cue's kind is not in that instrument's `accepted_triggers`, return the refusal reason `f"instrument {name!r} does not accept {kind!r} cues"` (fire nothing: all-or-nothing per fire, simplest to reason about and to test). Unknown dev (no pool entry): treat as accepting (today's behavior; do not invent refusals for the Room-sim path).

- [ ] **Step 1: Write failing tests**

```python
def test_fire_refused_when_target_instrument_rejects_cue_kind(...):
    # register a device whose DeviceInfo.carried is a narrow Instrument
    # (accepted_triggers=("midi",)); fire a SURFACE trigger carrying a
    # SolidCue at it; assert fire_trigger returns a reason naming "solid"
    # and no cue reached on_light_cue


def test_shipped_instruments_accept_every_cue_kind(...):
    # pin: TUNESHROOM and every instrument parsed from the repo
    # terrarium.toml accept all of CUE_KINDS
```

- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement** inside `fire_trigger`'s existing guarded try (it must keep never raising).
- [ ] **Step 4: Run full suite** — green.
- [ ] **Step 5: Commit** — `git commit -am "feat(triggers): accepted_triggers gates cue kinds at fire_trigger"`

---

### Task 9: Console views and front-end

**Files:**
- Modify: `control/room_view.py` (`room_view`, `fixtures_view`), `console/agent.py` only if a new event field needs threading (prefer none — these ride the existing `room_changed`/snapshot payloads)
- Modify: `console/static/surface.js`, `console/static/rail.js`
- Test: `tests/test_room_view.py`, `tests/js/surface_panel.test.js`, `tests/js/triggers_and_rail.test.js`

**Interfaces:**
- Consumes: `RoomFixture.instrument`, `Role.requires`, Task 5 slot requirements.
- Produces: `fixtures_view` rows gain `"instrument": {"name": ..., "capabilities": sorted list, "functions": list, "accepted_triggers": list}`. `room_view`'s `instruments` list stays ONE list discriminated by `kind` (property is test-pinned — extend the pin, do not restructure); each entry gains `"instrument_name"` (the fixture/room instrument whose declaration it came from, or the Bit's when the Bit overlay is live — when ambiguous, the room's first fixture's instrument name; keep it simple and documented in a comment). Rail role cards: `role_view` (find it in `console/` — `grep -rn "role_view" console control`) gains `"requires"` (slot name + sorted capability list, or null).
- Front-end: `surface.js` instrument cards render capabilities/functions/accepted-triggers as a small tag row; the new fields join the card's declaration signature so a changed declaration rebuilds the card and a live-value tick does not (the file's stated discipline). `rail.js` role cards show `requires` when present.

- [ ] **Step 1: Write failing tests** — extend `tests/test_room_view.py` (instrument fields present; one-list-`kind` pin still passes; Room privacy pins untouched); extend the two node test files under their existing DOM-stub pattern (card shows the capability tags; a `room_changed` with only controller changes does not rebuild the card — reuse the existing signature-discipline test shapes).
- [ ] **Step 2: Run** — `.venv/bin/python -m pytest tests/test_room_view.py tests/test_room_panel_behavior.py -q` plus the JS wrappers (`grep -rln "surface_panel" tests` for the Python wrapper names); expect failures.
- [ ] **Step 3: Implement** per interface block.
- [ ] **Step 4: Run full suite** — green.
- [ ] **Step 5: Commit** — `git commit -am "feat(console): instrument-sourced fixture cards and role requirement display"`

---

### Task 10: TestBit exemplar + docs cross-check

**Files:**
- Modify: `bits/test/test_bit.py`
- Modify: `docs/superpowers/specs/2026-08-27-instruments-and-fixtures-design.md` (Status section only)
- Test: `tests/test_test_bit.py` or wherever TestBit's declarations are pinned (`grep -rln "TEST_PLAYER_NODE" tests` to find it)

**Interfaces:**
- Consumes: everything above.
- Produces: `TestBit.instrument_requirements()` returns `(InstrumentRequirement(slot="player", capabilities=frozenset({"light.pixels", "gesture.tilt"})),)`; its `player` Role gains `requires="player"`. `jammer` stays requirement-free (pins the unchanged path).

- [ ] **Step 1: Write failing tests** — through the full engine (not a unit Bit): load TestBit into a room, hello a device (default TUNESHROOM carrier), join `TEST_PLAYER_NODE` -> granted with `slot == "player"`; a device whose `DeviceInfo.carried` is replaced with a gesture-less instrument is refused with a reason naming the missing capability.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement**; update the spec's Status to "Implemented 2026-08-27" with any recorded deviations.
- [ ] **Step 4: Run FULL suite** — `.venv/bin/python -m pytest tests -q` green; note the new pass count for the PR description.
- [ ] **Step 5: Commit** — `git commit -am "feat(bits): TestBit exercises the player requirement slot as the reference exemplar"`

---

## Self-Review Notes

- Spec coverage: section 2 -> Tasks 1-2; section 3 -> Tasks 3-4; sections 4-5 -> Tasks 5-6; section 6 -> Task 7; section 7 -> Task 8; section 8 -> Task 9; section 10's test list is distributed across all tasks; TestBit exemplar -> Task 10. Section 11 (live checklist) is post-merge, not in this plan.
- Instrument instances are unhashable (dict fields): no task keys by instance; identity is by `name` (Tasks 6, 8 use `carried.name`).
- Executors must read the real signatures before editing (`compose_role_config`, `validate_ugen_manifest`, `DeviceInfo`, `_setup_room`) — the sketches here are shape, the file is truth; when they diverge, keep the file's conventions and the interface blocks' NAMES.
