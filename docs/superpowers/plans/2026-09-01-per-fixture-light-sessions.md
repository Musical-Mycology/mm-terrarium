# Per-Fixture Light Sessions Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Room fixture its own LightSession from Room load, let Bits address fixtures by name with `@fixture:<name>`, and retire the canonical-dev collapse machinery.

**Architecture:** A Room becomes an ordered list of named fixtures with no session of its own. `control/cues.py` gains the `@fixture:<name>` sentinel; `control/functions.py` and `control/role_config.py` validate and slice declarations per fixture; `control/engine.py` resolves devs to lists and fans `@room` out; `devicelink/agent.py` holds one `_FixtureState` (session, universe, controllers, pending time) per fixture, renders each every tick, and hands frames to `FixtureSink`s (Console frame, bound devicelink device). `RoomBridge` and every canonical-dev helper are deleted.

**Tech Stack:** Python 3.11 stdlib in `control/` and `devicelink/` (no pyarco/luxaeterna imports there; luxaeterna types are imported only at the harness edge, which `devicelink/agent.py` already is), pytest, Node-based Console JS tests run through pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md` (sections 3, 4, 5, 9, 10). Plans 2 (rooms catalog, Design Room editor) and 3 (luxaeterna O2 time) are written after this plan lands.

## Global Constraints

- Tests ONLY via `.venv/bin/python -m pytest tests -q` (a fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`). Baseline at PR #81 HEAD: 1903 passed, 1 skipped. JS Console tests run through `tests/test_console_js.py` inside that same command.
- No em dashes anywhere (code, comments, docs, commit messages). The repo's `--` style is fine.
- `control/` is pure stdlib. `control/` and `devicelink/` never import pyarco or luxaeterna directly except where `devicelink/agent.py` already does (`LightManifest`, `build_session`, `Universe`).
- Fixture names match `[A-Za-z0-9_-]+` (the catalog name rule).
- `@fixture:<name>` is legal only on Bit-owned declarations. Instrument-owned functions stay `@target` and `@room` only.
- Every retired symbol in spec section 9 must be gone at the end of this plan: `GameServer._canonical_room_dev`, `DeviceLinkAgent._canonical_room_dev`, `_collapse_room_fanout`, the fold-back in `_suppress_generator_lanes`, the `explicit_surface` branch, the non-canonical light drop, `RoomBridge`, `FakeRoomLightSink`, `_RoomLightSink`, `ambient_manifests(profile)`, the `RoomProfile` cross-fixture generator lane rule, `control/terrarium.py`'s `_canonical_room_dev`, and the `room_bridge` plumbing in `control/terrarium.py`, `console/agent.py`, `harness/terrarium_boot.py`.
- Commit after every task with the prefix shown in the task.

---

## File structure

| File | Responsibility after this plan |
|------|-------------------------------|
| `control/cues.py` | Cue types plus the three sentinels `ROOM`, `TARGET`, `ALL` and the new `FIXTURE_PREFIX`, `fixture_dev`, `fixture_name`. |
| `control/functions.py` | Declaration validation with owner-aware dev rules; `expand_script`, stream expansion unchanged in shape. |
| `control/role_config.py` | Role blob composition plus `slice_light_manifest` and `manifest_fixture_targets`. |
| `control/instrument.py` | Instrument type; `fixture_ambient(fixture)` replaces `ambient_manifests(profile)`. |
| `control/generator_runner.py` | Per-fixture emission and suppression via an injected resolver. |
| `control/engine.py` | `_resolve_devs` (list), `@room` fan-out, load-time fixture contract, per-fixture suppression. |
| `control/fixture_sink.py` (new) | `FixtureSink` protocol, `ConsoleFrameSink`, `DeviceLinkSink`. Pure stdlib. |
| `control/room_profile.py` | Profile validation minus the cross-fixture generator lane rule. |
| `control/room_view.py` | Room panel payload; controllers merged flat plus `fixture_controllers`. |
| `control/terrarium.py` | Room load/unload without `RoomBridge`. |
| `devicelink/agent.py` | One `_FixtureState` per fixture; per-fixture feed, render, ambient generators, audio grants keyed by fixture name. |
| `console/agent.py`, `console/protocol.py`, `console/static/surface.js` | Controllers via a callable; `room_frame` keyed by fixture name; strips keyed by fixture name. |
| `harness/terrarium_boot.py` | Wiring without `room_bridge`. |
| `bits/test/test_bit.py` | Reference `chase` function using `@fixture:`. |
| Deleted | `control/room_bridge.py`, `tests/test_room_bridge.py`. |

---

### Task 1: The `@fixture:<name>` sentinel

**Files:**
- Modify: `control/cues.py`
- Test: `tests/test_cues.py` (create)

**Interfaces:**
- Produces: `FIXTURE_PREFIX: str = "@fixture:"`, `fixture_dev(name: str) -> str` (raises `ValueError` on a malformed name), `fixture_name(dev: object) -> str | None` (None for anything that is not a well-formed fixture sentinel).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cues.py
import pytest

from control.cues import ALL, FIXTURE_PREFIX, ROOM, TARGET, fixture_dev, fixture_name


def test_fixture_dev_round_trips_through_fixture_name():
    assert fixture_name(fixture_dev("accent")) == "accent"


def test_fixture_dev_spells_the_prefix_once():
    assert fixture_dev("main") == FIXTURE_PREFIX + "main" == "@fixture:main"


@pytest.mark.parametrize("bad", ["", "with space", "a.b", "x/y", "@fixture:z"])
def test_fixture_dev_refuses_malformed_names(bad):
    with pytest.raises(ValueError):
        fixture_dev(bad)


@pytest.mark.parametrize("dev", [ROOM, TARGET, ALL, "ie1", "@fixture:", "@fixture:a.b",
                                 None, 42, "@fixtures:main"])
def test_fixture_name_is_none_for_non_fixture_devs(dev):
    assert fixture_name(dev) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cues.py -q`
Expected: FAIL with `ImportError: cannot import name 'FIXTURE_PREFIX'`

- [ ] **Step 3: Implement**

Add to `control/cues.py`, after the `ALL` sentinel:

```python
import re

# Sentinel prefix a Bit uses to address ONE named Room fixture, e.g.
# "@fixture:accent". Legal only on Bit-owned declarations (an Instrument is a
# type and cannot know a Room's fixture names). Resolved by
# GameServer._resolve_devs to that fixture's bound dev, or dropped (logged
# once per load) while the fixture is unbound. See docs/superpowers/specs/
# 2026-09-01-per-fixture-light-sessions-design.md section 3.1.
FIXTURE_PREFIX = "@fixture:"
_FIXTURE_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def fixture_dev(name: str) -> str:
    """The sentinel dev for fixture `name`. The only place the prefix is
    spelled when building; fixture_name is the only place when parsing."""
    if not isinstance(name, str) or not _FIXTURE_NAME_RE.match(name):
        raise ValueError(f"fixture name {name!r} must match [A-Za-z0-9_-]+")
    return FIXTURE_PREFIX + name


def fixture_name(dev) -> str | None:
    """The fixture name a sentinel dev addresses, or None when `dev` is not a
    well-formed fixture sentinel (any other sentinel, a real dev id, a
    non-string)."""
    if not isinstance(dev, str) or not dev.startswith(FIXTURE_PREFIX):
        return None
    name = dev[len(FIXTURE_PREFIX):]
    return name if _FIXTURE_NAME_RE.match(name) else None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cues.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control/cues.py tests/test_cues.py
git commit -m "feat(cues): @fixture:<name> sentinel with fixture_dev/fixture_name helpers"
```

---

### Task 2: Owner-aware dev validation in declarations

**Files:**
- Modify: `control/functions.py` (`validate_function_table`, `_validate_scripted`, `_validate_script`, `_validate_step_cue`, `_validate_script_dev`, `_validate_generator`, `_validate_stream`, `_validate_stream_output`; delete `_LEGAL_SCRIPT_DEVS` and `_LEGAL_GENERATOR_DEVS`)
- Test: `tests/test_functions.py`

**Interfaces:**
- Consumes: `fixture_name` from Task 1.
- Produces: `validate_function_table(function_table, verb_names, *, owner="bit")` accepts `@fixture:<name>` devs on script steps (4-tuples, PlayCue, SolidCue, MuteCue), `GeneratorSpec.dev`, and `StreamOutput.dev` when `owner == "bit"`, and refuses them with a located `ValueError` when `owner == "instrument"`. New private helper `_check_dev(dev, where, owner)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_functions.py` (reuse that file's existing imports of `Function`, `FunctionKind`, `FunctionTable`, `FunctionTarget`, `Condition`, `ConditionSource`, `ScriptStep`, `GeneratorSpec`, `StreamSpec`, `StreamOutput`, `validate_function_table`; add `from control.cues import fixture_dev, MuteCue, SolidCue, PlayCue` if not already imported):

```python
def _admin(name="c"):
    return Condition(name=name, description="d", source=ConditionSource.ADMIN_MANUAL)


def _scripted(step_dev):
    return FunctionTable(functions={"f": Function(
        name="f", description="d", target=FunctionTarget.ROOM, condition=_admin(),
        script=(ScriptStep(0.0, (step_dev, 0xB0, 74, 127)),))})


def test_bit_script_step_may_address_a_fixture():
    validate_function_table(_scripted(fixture_dev("accent")), set(), owner="bit")


def test_instrument_script_step_may_not_address_a_fixture():
    table = FunctionTable(functions={"f": Function(
        name="f", description="d",
        script=(ScriptStep(0.0, (fixture_dev("accent"), 0xB0, 74, 127)),))})
    with pytest.raises(ValueError, match="@fixture"):
        validate_function_table(table, set(), owner="instrument")


@pytest.mark.parametrize("cue", [
    SolidCue(fixture_dev("main"), (255, 255, 255), 0.9, 1.0),
    MuteCue(fixture_dev("main")),
    PlayCue(fixture_dev("main"), "chime"),
])
def test_bit_typed_cues_may_address_a_fixture(cue):
    table = FunctionTable(functions={"f": Function(
        name="f", description="d", target=FunctionTarget.ROOM, condition=_admin(),
        script=(ScriptStep(0.0, cue),))})
    validate_function_table(table, set(), owner="bit")


def test_malformed_fixture_sentinel_is_refused_on_a_bit():
    with pytest.raises(ValueError, match="dev must be"):
        validate_function_table(_scripted("@fixture:a.b"), set(), owner="bit")


def test_bit_generator_may_drive_one_fixture_lane():
    table = FunctionTable(functions={"g": Function(
        name="g", description="d", kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=fixture_dev("accent"), status=0xB0, data1=74,
                                waveform="triangle", period=4.0))})
    validate_function_table(table, set(), owner="bit")


def test_instrument_generator_may_not_name_a_fixture():
    table = FunctionTable(functions={"g": Function(
        name="g", description="d", kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=fixture_dev("accent"), status=0xB0, data1=74,
                                waveform="triangle", period=4.0))})
    with pytest.raises(ValueError, match="@fixture"):
        validate_function_table(table, set(), owner="instrument")


def test_bit_stream_output_may_name_a_fixture():
    table = FunctionTable(functions={"s": Function(
        name="s", description="d", kind=FunctionKind.STREAM,
        stream=StreamSpec(verb="tilt", arg=0, in_lo=-90.0, in_hi=90.0, outputs=(
            StreamOutput(fixture_dev("main"), 0xB0, 74, 0.0, 127.0),)))})
    validate_function_table(table, {"tilt"}, owner="bit")
```

Check `StreamSpec`'s actual field names at `control/functions.py:147` before running and match them exactly (the fields are `verb`, `arg`, `in_lo`, `in_hi`, `outputs`; adjust if the file differs).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_functions.py -q -k "fixture"`
Expected: the "may" tests FAIL with `ValueError: ... dev must be cues.TARGET ... or cues.ROOM`; the "may not" tests pass or fail incidentally. All must be green after Step 3.

- [ ] **Step 3: Implement**

In `control/functions.py`:

1. Import `fixture_name` from `control.cues`.
2. Replace `_LEGAL_GENERATOR_DEVS` and `_LEGAL_SCRIPT_DEVS` with:

```python
def _check_dev(dev, where: str, owner: str) -> None:
    """A declaration dev is cues.TARGET, cues.ROOM, or (Bit owner only) a
    well-formed @fixture:<name> sentinel. Device ids are assigned at
    runtime, so a literal id can never resolve; an Instrument is a type
    and cannot know a Room's fixture names, so it may not name one."""
    if dev in (TARGET, ROOM):
        return
    if fixture_name(dev) is not None:
        if owner == "bit":
            return
        raise ValueError(
            f"{where}: {dev!r} names a fixture, but only a Bit may address "
            f"fixtures (@fixture:<name>); an instrument declaration may use "
            f"cues.TARGET ({TARGET!r}) or cues.ROOM ({ROOM!r})")
    raise ValueError(
        f"{where}: dev must be cues.TARGET ({TARGET!r}), cues.ROOM ({ROOM!r}) "
        f"or @fixture:<name> on a Bit, got {dev!r}. Device ids are assigned "
        f"at runtime, so a literal in a static declaration can never resolve")
```

3. Thread `owner` through: `validate_function_table` already has it; pass it to `_validate_scripted(fn, verb_names, owner)`, `_validate_generator(fn, owner)`, `_validate_stream(fn, owner)`. Inside: `_validate_script(fn, owner)` -> `_validate_step_cue(step, where, owner)` -> replace every `_validate_script_dev(x, where)` call with `_check_dev(x, where, owner)`, and delete `_validate_script_dev`. In `_validate_generator` replace the `spec.dev not in _LEGAL_GENERATOR_DEVS` block with `_check_dev(spec.dev, where, owner)`. In `_validate_stream` pass `owner` to `_validate_stream_output(output, where, owner)` and replace its dev check with `_check_dev(output.dev, where, owner)`.
4. Grep the file for any remaining `_LEGAL_` reference and remove it.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS (existing tests that matched the old error text `"dev must be cues.TARGET"` still match the new message's prefix; if one matches the full old sentence, update that assertion to `match="dev must be"`).

- [ ] **Step 5: Commit**

```bash
git add control/functions.py tests/test_functions.py
git commit -m "feat(functions): owner-aware dev validation accepts @fixture:<name> on Bit declarations"
```

---

### Task 3: Manifest slicing and per-fixture ambient

**Files:**
- Modify: `control/role_config.py` (add `slice_light_manifest`, `manifest_fixture_targets`)
- Modify: `control/instrument.py` (replace `ambient_manifests` with `fixture_ambient`)
- Test: `tests/test_role_config.py`, `tests/test_instrument.py`

**Interfaces:**
- Produces: `slice_light_manifest(manifest: dict, profile, fixture_name: str) -> dict` and `manifest_fixture_targets(manifest: dict, profile) -> set[str]` in `control/role_config.py`; `fixture_ambient(fixture) -> tuple[dict, dict]` in `control/instrument.py`.
- Consumes: `RoomProfile.fixtures`, `RoomFixture.zones` (existing).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_role_config.py`:

```python
from control.instrument import Instrument
from control.role_config import manifest_fixture_targets, slice_light_manifest
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone

_INST = Instrument(name="strip", capabilities=frozenset({"light.surface"}),
                   accepted_cues=("midi", "play", "solid", "mute"))
_PROFILE = RoomProfile(surface_id="r", fixtures=(
    RoomFixture(name="main", color_order="GRB",
                blocks=(RoomBlock("b", 0, 60),),
                zones=(RoomZone("left", 0, 30), RoomZone("right", 30, 30)),
                instrument=_INST),
    RoomFixture(name="accent", color_order="GRB",
                blocks=(RoomBlock("b", 0, 30),),
                zones=(RoomZone("low", 0, 15), RoomZone("high", 15, 15)),
                instrument=_INST),
))


def _decl(target, hue=0.1):
    return {"instrument": "rainbow", "target": target, "params": {"hue": hue},
            "lanes": [{"source": "cc:74", "dest": "hue"}]}


def test_primary_binds_on_every_fixture_as_primary():
    m = {"instruments": [_decl("primary")], "bit_name": "b"}
    for name in ("main", "accent"):
        out = slice_light_manifest(m, _PROFILE, name)
        assert [d["target"] for d in out["instruments"]] == ["primary"]
        assert out["bit_name"] == "b"


def test_fixture_target_binds_only_on_that_fixture_as_primary():
    m = {"instruments": [_decl("accent", hue=0.5)]}
    assert slice_light_manifest(m, _PROFILE, "main")["instruments"] == []
    out = slice_light_manifest(m, _PROFILE, "accent")["instruments"]
    assert out == [{**_decl("primary", hue=0.5)}]


def test_fixture_zone_target_binds_as_the_local_zone():
    m = {"instruments": [_decl("main.left")]}
    out = slice_light_manifest(m, _PROFILE, "main")["instruments"]
    assert out[0]["target"] == "left"
    assert slice_light_manifest(m, _PROFILE, "accent")["instruments"] == []


def test_slice_does_not_alias_the_source_manifest():
    m = {"instruments": [_decl("primary")]}
    out = slice_light_manifest(m, _PROFILE, "main")
    out["instruments"][0]["params"]["hue"] = 0.9
    assert m["instruments"][0]["params"]["hue"] == 0.1


def test_manifest_fixture_targets_collects_named_fixtures():
    m = {"instruments": [_decl("primary"), _decl("accent"), _decl("main.right")]}
    assert manifest_fixture_targets(m, _PROFILE) == {"accent", "main"}


@pytest.mark.parametrize("target", ["ceiling", "main.centre", "accent.left", "main."])
def test_manifest_fixture_targets_refuses_unknown_names(target):
    with pytest.raises(ValueError, match="instruments\\[0\\]"):
        manifest_fixture_targets({"instruments": [_decl(target)]}, _PROFILE)
```

Replace the four `ambient_manifests` tests in `tests/test_instrument.py` (`test_empty_ambient_manifests_validate`, `test_ambient_manifests_concatenates_in_fixture_order`, `test_ambient_manifests_empty_when_nothing_declares_anything`, `test_ambient_manifests_deep_copies_entries`) with:

```python
def test_fixture_ambient_returns_that_fixtures_own_manifests():
    light = {"instruments": [{"instrument": "aurora", "target": "primary"}]}
    ugen = {"instruments": [{"instrument": "flsyn", "program": 1}]}
    inst = Instrument(name="glow", capabilities=frozenset({"light.surface"}),
                      light_manifest=light, ugen_manifest=ugen)
    fixture = RoomFixture(name="f", color_order="GRB",
                          blocks=(RoomBlock("b", 0, 10),), zones=(), instrument=inst)
    out_light, out_ugen = fixture_ambient(fixture)
    assert (out_light, out_ugen) == (light, ugen)
    out_light["instruments"][0]["target"] = "ring"
    assert inst.light_manifest["instruments"][0]["target"] == "primary"


def test_fixture_ambient_is_empty_dicts_when_nothing_declared():
    inst = Instrument(name="bare", capabilities=frozenset({"light.surface"}))
    fixture = RoomFixture(name="f", color_order="GRB",
                          blocks=(RoomBlock("b", 0, 10),), zones=(), instrument=inst)
    assert fixture_ambient(fixture) == ({}, {})
```

Update that file's import from `ambient_manifests` to `fixture_ambient` and import `RoomBlock`, `RoomFixture` from `control.room_profile` if not already present.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_role_config.py tests/test_instrument.py -q`
Expected: FAIL with ImportError for `slice_light_manifest` / `fixture_ambient`.

- [ ] **Step 3: Implement**

In `control/role_config.py` add:

```python
def _fixture(profile, name: str):
    for fixture in profile.fixtures:
        if fixture.name == name:
            return fixture
    return None


def _split_target(target: str, profile, where: str) -> tuple[str | None, str | None]:
    """(fixture_name, local_zone) for a manifest target. "primary" ->
    (None, "primary"). "<fixture>" -> (fixture, "primary"). "<fixture>.<zone>"
    -> (fixture, zone). Anything else is a located ValueError."""
    if target == "primary":
        return None, "primary"
    fixture_part, dot, zone_part = target.partition(".")
    fixture = _fixture(profile, fixture_part)
    if fixture is None:
        raise ValueError(
            f"{where}: target {target!r} names no fixture of this Room; "
            f"fixtures: {[f.name for f in profile.fixtures]}")
    if not dot:
        return fixture.name, "primary"
    if not any(z.name == zone_part for z in fixture.zones):
        raise ValueError(
            f"{where}: target {target!r} names no zone of fixture "
            f"{fixture.name!r}; zones: {[z.name for z in fixture.zones]}")
    return fixture.name, zone_part


def manifest_fixture_targets(manifest: dict, profile) -> set[str]:
    """Every fixture a ROOM-role light manifest addresses by name (as
    "<fixture>" or "<fixture>.<zone>"). Raises a located ValueError on an
    unknown fixture or zone. "primary" decls contribute nothing."""
    out: set[str] = set()
    for idx, decl in enumerate(manifest.get("instruments", [])):
        where = f"light_manifest instruments[{idx}]"
        fixture, _zone = _split_target(decl.get("target", "primary"), profile, where)
        if fixture is not None:
            out.add(fixture)
    return out


def slice_light_manifest(manifest: dict, profile, fixture_name: str) -> dict:
    """The part of a ROOM-role light manifest that binds on ONE fixture,
    with targets rewritten to that fixture's local zone names (spec section
    3.3): "primary" and "<fixture>" become "primary"; "<fixture>.<zone>"
    becomes "<zone>"; decls for other fixtures are dropped. Every other
    manifest key (bit_name, role, welcome, ...) is carried through.
    Deep-copied so the session can never alias the Bit's declaration."""
    out = {k: deepcopy(v) for k, v in manifest.items() if k != "instruments"}
    kept = []
    for idx, decl in enumerate(manifest.get("instruments", [])):
        where = f"light_manifest instruments[{idx}]"
        fixture, local = _split_target(decl.get("target", "primary"), profile, where)
        if fixture is not None and fixture != fixture_name:
            continue
        new = deepcopy(decl)
        new["target"] = local
        kept.append(new)
    out["instruments"] = kept
    return out
```

In `control/instrument.py` delete `ambient_manifests` and add:

```python
def fixture_ambient(fixture) -> tuple[dict, dict]:
    """(light, ugen) ambient manifests of ONE fixture's instrument, deep-
    copied. ({}, {}) when the instrument declares neither, so a caller can
    tell "nothing ambient" from "an empty surface" (spec section 3.3)."""
    inst = fixture.instrument
    return copy.deepcopy(dict(inst.light_manifest)), copy.deepcopy(dict(inst.ugen_manifest))
```

Leave the import in `devicelink/agent.py` broken for now only if Task 6 is the very next thing you run; otherwise change `devicelink/agent.py:22` to `from control.instrument import fixture_ambient` and the one call at line 257 to build per fixture is done in Task 6. To keep the suite green at this commit, do the minimal edit: change the import line and replace the call `ambient_light, ambient_ugen = ambient_manifests(self._room_profile)` with a local concatenation:

```python
            ambient_light_instruments, ambient_ugen_instruments = [], []
            for fixture in self._room_profile.fixtures:
                fl, fu = fixture_ambient(fixture)
                ambient_light_instruments.extend(fl.get("instruments", []))
                ambient_ugen_instruments.extend(fu.get("instruments", []))
            ambient_light = ({"instruments": ambient_light_instruments}
                             if ambient_light_instruments else {})
            ambient_ugen = ({"instruments": ambient_ugen_instruments}
                            if ambient_ugen_instruments else {})
```

(Task 6 deletes this block.)

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control/role_config.py control/instrument.py devicelink/agent.py tests/test_role_config.py tests/test_instrument.py
git commit -m "feat(role_config): slice a ROOM light manifest per fixture; per-fixture ambient accessor"
```

---

### Task 4: Engine resolves devs to lists and fans `@room` out

**Files:**
- Modify: `control/engine.py` (`_resolve_dev` -> `_resolve_devs`, `_resolve_target`, `_dispatch_cues`, `_check_cue_kinds`, `fire_function`, `_suppress_generator_lanes`; delete `_canonical_room_dev`, `_collapse_room_fanout`)
- Test: `tests/test_engine_functions.py`, `tests/test_fire_ladder.py`, `tests/test_timed_cues.py`

**Interfaces:**
- Consumes: `fixture_name` (Task 1).
- Produces: `GameServer._resolve_devs(dev: str) -> list[str]`; `GameServer._room_devs() -> list[str]` (bound fixture devs in profile order). `GeneratorRunner` still receives concrete lanes from `_suppress_generator_lanes`; Task 5 changes its emission side.

- [ ] **Step 1: Write the failing tests**

In `tests/test_engine_functions.py`, replace `test_a_target_fanout_across_two_bound_fixtures_feeds_the_room_once_per_step` with:

```python
def test_a_target_fanout_across_two_bound_fixtures_feeds_each_fixture():
    """Each fixture has its own session now, so a TARGET step fans out to
    every bound fixture: 3 steps x 2 fixtures = 6 light cues, in profile
    order within each step. The fired record reports both devs as before."""
    gs, light, _ = _running(bound={"main": "sim-room-main",
                                   "accent": "sim-room-accent"})
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_function("sweep", fired_by="admin-manual") is None
    assert [c[0] for c in light] == ["sim-room-main", "sim-room-accent"] * 3
    assert observer.fired[0].devs == ("sim-room-main", "sim-room-accent")
    assert observer.fired[0].steps == 6
```

Add, in the same file (use its existing `_running` helper and `Recorder`):

```python
from control.cues import fixture_dev


def test_a_room_literal_step_reaches_every_bound_fixture():
    """A step written literally as cues.ROOM is a broadcast."""
    gs, light, _ = _running(bound={"main": "sim-room-main",
                                   "accent": "sim-room-accent"})
    gs._dispatch_cues([(ROOM, 0xB0, 74, 5)], at=1.0)
    assert [c[0] for c in light] == ["sim-room-main", "sim-room-accent"]


def test_a_fixture_cue_reaches_only_that_fixture():
    gs, light, _ = _running(bound={"main": "sim-room-main",
                                   "accent": "sim-room-accent"})
    gs._dispatch_cues([(fixture_dev("accent"), 0xB0, 74, 5)], at=1.0)
    assert [c[0] for c in light] == ["sim-room-accent"]


def test_a_cue_at_an_unbound_fixture_is_dropped_and_warned_once(caplog):
    gs, light, _ = _running(bound={"main": "sim-room-main"})
    with caplog.at_level("WARNING"):
        gs._dispatch_cues([(fixture_dev("accent"), 0xB0, 74, 5),
                           (fixture_dev("accent"), 0xB0, 74, 6)], at=1.0)
    assert light == []
    assert sum("accent" in r.message for r in caplog.records) == 1


def test_scripted_fire_at_one_fixture_suppresses_only_that_fixtures_lane():
    """The parked finding from PR #81: a Bit generator on ROOM cc:74 keeps
    driving main while a script fired at the accent writes accent cc:74."""
    gs, light, _ = _running(bound={"main": "sim-room-main",
                                   "accent": "sim-room-accent"})
    # TestBit declares "drift" on ROOM cc:74 and "play_aurora" as a SURFACE
    # function whose steps write TARGET cc:74.
    assert gs.fire_function("play_aurora", fired_by="admin-manual",
                            dev="sim-room-accent") is None
    light.clear()
    gs._dispatch_generator_cues()
    assert [c[0] for c in light] == ["sim-room-main"]
```

Check `_running`'s signature and what Bit it loads (it is TestBit-based; the `sweep` name is that file's own three-step fixture). If `_running` loads a Bit other than TestBit for the suppression test, build the game server the way `test_scripted_fire_suppresses_the_generator_lane_it_writes_and_it_resumes` in the same file does.

In `tests/test_fire_ladder.py`, `test_explicit_fixture_fire_is_not_collapsed` keeps its assertions (they now hold trivially) but update its docstring to say collapse no longer exists.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_functions.py -q -k "fixture or room_literal"`
Expected: FAIL (the fan-out test sees 3 cues, the fixture tests see the sentinel passed through unresolved).

- [ ] **Step 3: Implement**

In `control/engine.py`:

1. Import `fixture_name` from `control.cues`. Add `self._warned_unbound: set[str] = set()` in `__init__` next to `_warned_no_room`, and clear it in `load_bit` where `_warned_no_room` is reset.
2. Delete `_canonical_room_dev` and `_collapse_room_fanout`.
3. Add and replace:

```python
    def _room_devs(self) -> list[str]:
        """Every bound fixture dev, in the profile's declaration order (the
        Room's ordered fixture list), never dict/bind order."""
        if self.room is None or not self.room.bound:
            return []
        return [self.room.bound[f.name] for f in self.room.profile.fixtures
                if f.name in self.room.bound]

    def _resolve_devs(self, dev: str) -> list[str]:
        """cues.ROOM -> every bound fixture dev (a broadcast); @fixture:<name>
        -> that fixture's bound dev; anything else passes through as itself.

        An empty list means drop, never raise. Warned once per Bit load per
        missing target rather than once per cue: a 20 Hz gesture stream
        would otherwise flood the log."""
        if dev == ROOM:
            devs = self._room_devs()
            if not devs and not self._warned_no_room:
                self._warned_no_room = True
                logger.warning("Bit emitted a ROOM cue with no Room bound; "
                               "dropping (logged once per Bit load)")
            return devs
        name = fixture_name(dev)
        if name is None:
            return [dev]
        bound = self.room.bound.get(name) if self.room is not None else None
        if bound is None:
            if name not in self._warned_unbound:
                self._warned_unbound.add(name)
                logger.warning("cue addressed to fixture %r, which is not "
                               "bound; dropping (logged once per Bit load)", name)
            return []
        return [bound]
```

4. `_resolve_target`: replace the inline `room_devs` computation with `room_devs = self._room_devs()`.
5. `_dispatch_cues`: for each of the SolidCue / MuteCue / PlayCue / LightCue / 4-tuple branches, replace `dev = self._resolve_dev(...); if dev is None: continue` with a loop over `self._resolve_devs(...)` that calls the sink once per dev. For MuteCue, `self.muted.add(dev)` per dev and one `_notify("on_devices_change")` after the loop. For PlayCue skip muted devs individually.
6. `_check_cue_kinds`: iterate `for resolved in self._resolve_devs(cue.dev)`, look up `inst = self._instrument_for(resolved)`, `if inst is None: continue`, refuse with `f"instrument {inst.name!r} does not accept {kind!r} cues"` when `kind not in inst.accepted_cues`. Delete the "any fixture accepts" branch.
7. `fire_function`: delete the `explicit_surface` variable and use `cues = expand_script(decl, at, devs)` directly in rung 1.
8. `_suppress_generator_lanes`: delete the `canonical_room` lookup and fold-back. For each cue, `for d in self._resolve_devs(dev): lanes.add((d, status, data1))`.
9. Grep the file for `_resolve_dev(` and `_canonical_room_dev` and make sure none remain.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: the new engine tests pass. The suppression test `test_scripted_fire_at_one_fixture_suppresses_only_that_fixtures_lane` may still FAIL until Task 5 (the runner still emits `@room` lanes, so per-fixture suppression cannot match). If so, mark it `@pytest.mark.xfail(strict=True, reason="Task 5: per-fixture generator emission")` for this commit and remove the marker in Task 5. Tests in `tests/test_devicelink_agent.py` and `tests/test_timed_cues.py` that assert canonical-dev behaviour through the agent still pass because the agent is unchanged until Task 6. Anything else that fails names a behaviour this task changed on purpose (canonical collapse): update its assertion to the per-fixture expectation and its docstring to say so.

- [ ] **Step 5: Commit**

```bash
git add control/engine.py tests/test_engine_functions.py tests/test_fire_ladder.py
git commit -m "feat(engine): resolve devs to lists, fan @room out, resolve @fixture:<name>; drop canonical collapse"
```

---

### Task 5: Per-fixture generator emission and the load-time fixture contract

**Files:**
- Modify: `control/generator_runner.py`, `control/engine.py` (`load_bit`)
- Test: `tests/test_generator_runner.py` (create if absent; else append), `tests/test_engine_functions.py`, `tests/test_engine_room_role_synthesis.py`

**Interfaces:**
- Produces: `GeneratorRunner(functions, resolve: Callable[[str], list[str]] | None = None)`; `cues(elapsed, at)` yields one tuple per RESOLVED dev per declared lane; `suppress(lanes, until_at)` takes concrete `(dev, status, data1)` lanes. New engine helpers `_bit_fixture_names(function_table, light_manifest) -> set[str]` and `_check_generator_lane_collisions(function_table)`.
- Consumes: `_resolve_devs` (Task 4), `manifest_fixture_targets` (Task 3), `fixture_name` (Task 1).

- [ ] **Step 1: Write the failing tests**

`tests/test_generator_runner.py` (create or append; existing tests in the file that build `GeneratorRunner(functions)` with no resolver must keep passing):

```python
from control.cues import ROOM, fixture_dev
from control.functions import Function, FunctionKind, GeneratorSpec
from control.generator_runner import GeneratorRunner


def _gen(dev, data1=74):
    return Function(name=f"g{data1}", description="d", kind=FunctionKind.GENERATOR,
                    generator=GeneratorSpec(dev=dev, status=0xB0, data1=data1,
                                            waveform="triangle", period=4.0, lo=0, hi=100))


def _resolve(dev):
    return ["main-dev", "accent-dev"] if dev == ROOM else (
        ["accent-dev"] if dev == fixture_dev("accent") else [dev])


def test_room_generator_emits_once_per_resolved_fixture():
    runner = GeneratorRunner([_gen(ROOM)], resolve=_resolve)
    assert [c[0] for c in runner.cues(1.0, 10.0)] == ["main-dev", "accent-dev"]


def test_suppressing_one_fixture_lane_leaves_the_other_emitting():
    runner = GeneratorRunner([_gen(ROOM)], resolve=_resolve)
    runner.suppress([("accent-dev", 0xB0, 74)], until_at=20.0)
    assert [c[0] for c in runner.cues(1.0, 10.0)] == ["main-dev"]
    assert [c[0] for c in runner.cues(1.0, 21.0)] == ["main-dev", "accent-dev"]


def test_fixture_generator_emits_only_on_its_fixture():
    runner = GeneratorRunner([_gen(fixture_dev("accent"))], resolve=_resolve)
    assert [c[0] for c in runner.cues(1.0, 10.0)] == ["accent-dev"]


def test_no_resolver_passes_devs_through_unchanged():
    runner = GeneratorRunner([_gen(ROOM)])
    assert [c[0] for c in runner.cues(1.0, 10.0)] == [ROOM]
```

In `tests/test_engine_room_role_synthesis.py` (or `tests/test_engine_functions.py` if that file's helpers fit better) add:

```python
from control.cues import fixture_dev
from control.engine import BitLoadError, GameServer
from control.functions import (Condition, ConditionSource, Function, FunctionKind,
                               FunctionTable, FunctionTarget, GeneratorSpec, ScriptStep)


class _FixtureBit(TestBit):
    """TestBit plus one declaration that names a fixture the TEST Room lacks."""
    @property
    def function_table(self):
        table = super().function_table
        table.functions["ceiling"] = Function(
            name="ceiling", description="d", target=FunctionTarget.ROOM,
            condition=Condition(name="c", description="d",
                                source=ConditionSource.ADMIN_MANUAL),
            script=(ScriptStep(0.0, (fixture_dev("ceiling"), 0xB0, 74, 1)),))
        return table


def test_load_bit_refuses_a_bit_that_addresses_a_missing_fixture():
    gs = GameServer({"B": _FixtureBit})
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="N")
    with pytest.raises(BitLoadError, match="ceiling"):
        gs.load_bit("B")
    assert gs.state is State.IDLE


class _CollidingBit(TestBit):
    """TestBit's drift (ROOM cc:74) plus a fixture generator on accent cc:74:
    both write the accent's cc:74 lane once resolved."""
    @property
    def function_table(self):
        table = super().function_table
        table.functions["accent_drift"] = Function(
            name="accent_drift", description="d", kind=FunctionKind.GENERATOR,
            generator=GeneratorSpec(dev=fixture_dev("accent"), status=0xB0, data1=74,
                                    waveform="triangle", period=3.0))
        return table


def test_load_bit_refuses_generators_that_collide_after_resolution():
    gs = GameServer({"B": _CollidingBit})
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="N")
    with pytest.raises(BitLoadError, match="lane"):
        gs.load_bit("B")


def test_load_bit_with_no_room_skips_the_fixture_contract():
    gs = GameServer({"B": _FixtureBit})
    gs.load_bit("B")     # roomless boot: nothing to check against
    assert gs.state is State.SETUP
```

Use that test file's existing imports for `TestBit`, `Room`, `State`, and the TEST profile (`tests/test_devicelink_agent.py` shows the `load_terrarium_config("terrarium.toml").rooms["TEST"].profile` idiom).

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_generator_runner.py tests/test_engine_room_role_synthesis.py -q`
Expected: FAIL (`GeneratorRunner.__init__` rejects `resolve`; load_bit accepts the bad Bits).

- [ ] **Step 3: Implement**

`control/generator_runner.py`:

```python
class GeneratorRunner:
    def __init__(self, functions: Sequence, resolve=None) -> None:
        # Declared lanes, keyed by the DECLARED (dev, status, data1);
        # validate_function_table guarantees no two share one.
        self._generators: dict[tuple[str, int, int], object] = {}
        for fn in functions:
            spec = fn.generator
            self._generators[(spec.dev, spec.status, spec.data1)] = spec
        # dev sentinel -> concrete devs. None passes the declared dev
        # through unchanged (pure unit tests, and the pre-resolution shape).
        self._resolve = resolve if resolve is not None else (lambda dev: [dev])
        # CONCRETE (dev, status, data1) -> suppression window end.
        self._suppressed_until: dict[tuple[str, int, int], float] = {}

    def suppress(self, lanes, until_at: float) -> None:
        for lane in lanes:
            self._suppressed_until[lane] = until_at

    def cues(self, elapsed: float, at: float) -> list[tuple]:
        out: list[tuple] = []
        for (dev, status, data1), spec in self._generators.items():
            value = None
            for resolved in self._resolve(dev):
                until = self._suppressed_until.get((resolved, status, data1))
                if until is not None and at < until:
                    continue
                if value is None:
                    value = self.value(spec, elapsed)
                out.append((resolved, status, data1, value))
        return out
```

Keep `value()` unchanged and keep the docstrings' substance (rewrite them to describe resolution).

`control/engine.py`, in `load_bit`:

1. Construct the runner with the resolver: `self._generators = GeneratorRunner([...], resolve=self._resolve_devs)`.
2. Inside the `try:` block, after `validate_function_table(...)`, when `self.room is not None`:

```python
                missing = self._bit_fixture_names(function_table, light_m) - {
                    f.name for f in self.room.profile.fixtures}
                if missing:
                    raise ValueError(
                        f"Bit addresses fixtures {sorted(missing)} that Room "
                        f"{self.room.name!r} does not declare; its fixtures are "
                        f"{[f.name for f in self.room.profile.fixtures]}")
                self._check_generator_lane_collisions(function_table)
```

Note `light_m` is only assigned inside `if self.room is not None:`; the new code sits inside that same block after `_resolve_room_requirements`.

3. Add the helpers:

```python
    def _bit_fixture_names(self, function_table, light_manifest) -> set[str]:
        """Every fixture name a Bit's declarations address: @fixture: devs on
        script steps, generator lanes and stream outputs, plus fixture-scoped
        light manifest targets (spec section 3.4). manifest_fixture_targets
        raises on an unknown fixture or zone; load_bit turns that into a
        BitLoadError like every other declaration defect."""
        names: set[str] = set()
        for fn in function_table.functions.values():
            for step in fn.script:
                cue = step.cue
                dev = cue[0] if isinstance(cue, tuple) else cue.dev
                n = fixture_name(dev)
                if n is not None:
                    names.add(n)
            if fn.generator is not None:
                n = fixture_name(fn.generator.dev)
                if n is not None:
                    names.add(n)
            if fn.stream is not None:
                for output in fn.stream.outputs:
                    n = fixture_name(output.dev)
                    if n is not None:
                        names.add(n)
        if light_manifest:
            names |= manifest_fixture_targets(light_manifest, self.room.profile)
        return names

    def _check_generator_lane_collisions(self, function_table) -> None:
        """Two GENERATOR functions may not write the same CONCRETE fixture
        lane once resolved against the loaded Room: a @room generator and a
        @fixture:accent generator on one cc both write the accent (spec
        section 3.5). Resolution here is by fixture NAME, not bound dev, so
        an unbound fixture still counts."""
        fixtures = [f.name for f in self.room.profile.fixtures]
        owners: dict[tuple[str, int, int], str] = {}
        for fn in function_table.functions.values():
            if fn.kind is not FunctionKind.GENERATOR:
                continue
            spec = fn.generator
            if spec.dev == ROOM:
                targets = fixtures
            else:
                n = fixture_name(spec.dev)
                targets = [n] if n is not None else [spec.dev]
            for t in targets:
                lane = (t, spec.status, spec.data1)
                if lane in owners:
                    raise ValueError(
                        f"generators {owners[lane]!r} and {fn.name!r} both "
                        f"write lane cc:{spec.data1} on fixture {t!r} once "
                        f"resolved against Room {self.room.name!r}")
                owners[lane] = fn.name
```

Import `manifest_fixture_targets` from `control.role_config`. Remove any `xfail` marker added in Task 4.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS, including `test_scripted_fire_at_one_fixture_suppresses_only_that_fixtures_lane`.

- [ ] **Step 5: Commit**

```bash
git add control/generator_runner.py control/engine.py tests/test_generator_runner.py tests/test_engine_room_role_synthesis.py tests/test_engine_functions.py
git commit -m "feat(engine): per-fixture generator emission and suppression; load-time fixture contract"
```

---

### Task 6: One session per fixture in the transport, with fixture sinks

This is the largest task. It rewrites the Room half of `devicelink/agent.py` and its tests. Read `devicelink/agent.py` end to end first.

**Files:**
- Create: `control/fixture_sink.py`
- Modify: `devicelink/agent.py`
- Test: `tests/test_fixture_sink.py` (create), `tests/test_devicelink_agent.py`, `tests/test_timed_cues.py`

**Interfaces:**
- Produces:
  - `control/fixture_sink.py`: `class FixtureSink(Protocol): def send_frame(self, frame: bytes, when: float) -> None`; `ConsoleFrameSink(fixture_name: str, on_room_frame: Callable[[str, bytes], None])`; `DeviceLinkSink(dev: str, send: Callable[[str, dict], None], leds_event: Callable[..., dict])` whose `send_frame` calls `send(dev, leds_event(dev, frame, when=when))`. Both guard exceptions with `logger.exception` and never raise.
  - `DeviceLinkAgent.__init__(game_server, server, capability=None, clock=time.monotonic, room_audio=None, horizon=0.0, room_profile=None, on_room_frame=None, on_join_denied=None, stale_timeout=15.0)` (the `room_bridge` parameter is REMOVED).
  - `DeviceLinkAgent.rewire_room()` (no argument), `unwire_room()`.
  - `DeviceLinkAgent.controllers() -> dict[str, dict[int, int]]` keyed by fixture name.
  - `DeviceLinkAgent._fixtures: dict[str, _FixtureState]`.
  - `on_room_frame(fixture_name: str, frame: bytes)` (was keyed by dev).
- Consumes: `slice_light_manifest`, `fixture_ambient` (Task 3); `GeneratorRunner(..., resolve=)` (Task 5); `to_fixture_capability` (`harness/room_surface.py`, existing).

- [ ] **Step 1: Write the sink tests**

```python
# tests/test_fixture_sink.py
from control.fixture_sink import ConsoleFrameSink, DeviceLinkSink


def test_console_sink_forwards_by_fixture_name():
    seen = []
    ConsoleFrameSink("main", lambda name, frame: seen.append((name, frame))
                     ).send_frame(b"\x01\x02\x03", 5.0)
    assert seen == [("main", b"\x01\x02\x03")]


def test_console_sink_swallows_a_failing_console():
    def boom(name, frame):
        raise RuntimeError("console down")
    ConsoleFrameSink("main", boom).send_frame(b"\x00", 1.0)   # must not raise


def test_devicelink_sink_sends_a_leds_event_to_the_bound_dev():
    sent = []
    def leds_event(dev, frame, when=None):
        return {"event": "leds", "dev": dev, "frame": frame, "when": when}
    DeviceLinkSink("sim-room-main", lambda dev, msg: sent.append((dev, msg)),
                   leds_event).send_frame(b"\x07", 9.5)
    assert sent == [("sim-room-main", {"event": "leds", "dev": "sim-room-main",
                                       "frame": b"\x07", "when": 9.5})]


def test_devicelink_sink_swallows_a_failing_send():
    def boom(dev, msg):
        raise OSError("gone")
    DeviceLinkSink("d", boom, lambda dev, frame, when=None: {}).send_frame(b"", 1.0)
```

- [ ] **Step 2: Implement `control/fixture_sink.py`**

```python
"""FixtureSink: where one fixture's rendered frames go.

A Room is loaded with all its instruments (one session per fixture) from
Room load; devices are OUTPUTS that attach to a fixture. Each render hands
the fixture's changed frame to every sink it currently has: the Console's
display strip always, the bound devicelink device when there is one, and
later a physical controller. Pure stdlib (control/ discipline): the
devicelink protocol's leds_event is injected, never imported. See
docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md
section 5.3.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


class FixtureSink(Protocol):
    def send_frame(self, frame: bytes, when: float) -> None: ...


class ConsoleFrameSink:
    """Display-only: the Console strip for `fixture_name`. Best-effort, per
    boundary rule 2; a failing console never costs the fixture a frame."""

    def __init__(self, fixture_name: str,
                 on_room_frame: Callable[[str, bytes], None]) -> None:
        self.fixture_name = fixture_name
        self._on_room_frame = on_room_frame

    def send_frame(self, frame: bytes, when: float) -> None:
        try:
            self._on_room_frame(self.fixture_name, frame)
        except Exception:
            logger.exception("console frame sink failed for %s; dropping frame",
                             self.fixture_name)


class DeviceLinkSink:
    """The bound devicelink device: a dumb pixel sink that displays `frame`
    at `when` (O2 time)."""

    def __init__(self, dev: str, send: Callable[[str, dict], None],
                 leds_event: Callable[..., dict]) -> None:
        self.dev = dev
        self._send = send
        self._leds_event = leds_event

    def send_frame(self, frame: bytes, when: float) -> None:
        try:
            self._send(self.dev, self._leds_event(self.dev, frame, when=when))
        except Exception:
            logger.exception("leds send failed for %s", self.dev)
```

Run: `.venv/bin/python -m pytest tests/test_fixture_sink.py -q` -> PASS. Commit:

```bash
git add control/fixture_sink.py tests/test_fixture_sink.py
git commit -m "feat(control): FixtureSink protocol with Console and devicelink sinks"
```

- [ ] **Step 3: Write the agent tests (they fail until Step 4)**

In `tests/test_devicelink_agent.py`:

a. Remove `from control.room_bridge import FakeRoomLightSink, RoomBridge`. Add a fake-session helper near the top:

```python
class FakeFixtureSession:
    """Stands in for a luxaeterna LightSession built by _setup_room: records
    fed MIDI, renders a frame whose bytes encode the last cc value so a
    test can tell fixtures apart."""
    def __init__(self, manifest, cap):
        self.manifest = manifest
        self.cap = cap
        self.fed = []
        self.state = "running"
        self._last = 0

    def feed_midi(self, status, d1, d2):
        self.fed.append((status, d1, d2))
        self._last = d2

    def render_into(self, universe):
        universe.set_range(0, bytes([self._last & 0xFF]) * (self.cap.pixel_count * 3))


def _fake_sessions(monkeypatch):
    """Patch devicelink.agent.build_session so every fixture session is a
    FakeFixtureSession; returns the dict the agent's sessions land in, keyed
    by capability surface_id (f"{profile}_{fixture}")."""
    import devicelink.agent as agent_mod
    built = {}
    def build(manifest, cap, clock=None):
        built[cap.surface_id] = FakeFixtureSession(manifest, cap)
        return built[cap.surface_id]
    monkeypatch.setattr(agent_mod, "build_session", build)
    return built
```

`Universe.set_range` exists in luxaeterna (`LightEngine.render_into` calls it); if the name differs in the checkout, use whatever `render_into` calls.

b. Replace `_agent_with_bound_room()`:

```python
def _agent_with_bound_room(monkeypatch):
    gs = _room_ready_game_server()
    sessions = _fake_sessions(monkeypatch)
    agent = DeviceLinkAgent(gs, FakeServer())
    return gs, agent, sessions["TEST_main"]
```

and give every caller a `monkeypatch` parameter (pytest's fixture). Callers that asserted `bridge.fed == [...]` now assert `sessions["TEST_main"].fed == [...]`.

c. Update every `DeviceLinkAgent(..., room_bridge=...)` construction in the file to drop the argument, and every `RoomBridge()` / `room_bridge.bind(...)` line. `two_fixture_agent` becomes:

```python
@pytest.fixture
def two_fixture_agent(monkeypatch):
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    audio = _FakeAudioBridge()
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    server.bind_dev("sim-room-accent", "c-accent")
    agent = DeviceLinkAgent(gs, server, room_audio=audio)
    return agent, audio, "sim-room-main", "sim-room-accent"
```

d. Rewrite the tests whose behaviour changes:

```python
def test_every_fixture_gets_its_own_session_from_room_load(monkeypatch):
    gs = _room_ready_game_server(bound={})          # nothing bound at all
    sessions = _fake_sessions(monkeypatch)
    DeviceLinkAgent(gs, FakeServer())
    assert set(sessions) == {"TEST_main", "TEST_accent"}
    assert sessions["TEST_main"].cap.pixel_count == 60
    assert sessions["TEST_accent"].cap.pixel_count == 30


def test_unbound_fixture_renders_to_the_console_and_sends_nothing(monkeypatch):
    gs = _room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)
    frames = []
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server,
                            on_room_frame=lambda name, frame: frames.append(name))
    agent._render_room()
    assert sorted(frames) == ["accent", "main"]
    assert server.sent == []


def test_bound_fixture_receives_its_own_frame_stamped_when(monkeypatch):
    gs = _room_ready_game_server(bound={"main": "sim-room-main"})
    _fake_sessions(monkeypatch)
    now = [100.0]
    server = FakeServer()
    server.bind_dev("sim-room-main", "c")
    agent = DeviceLinkAgent(gs, server, horizon=0.5, clock=lambda: now[0])
    agent._render_room()
    (dev, msg), = server.sent
    assert dev == "sim-room-main"
    assert len(msg["channels"]) == 180
    assert msg["when"] == 100.5


def test_a_light_cue_at_the_accent_feeds_only_the_accents_session(monkeypatch):
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    sessions = _fake_sessions(monkeypatch)
    DeviceLinkAgent(gs, FakeServer())
    gs.on_light_cue("sim-room-accent", 0xB0, 74, 99, None)
    assert sessions["TEST_accent"].fed == [(0xB0, 74, 99)]
    assert sessions["TEST_main"].fed == []


def test_a_room_cue_reaches_every_bound_fixtures_session(monkeypatch):
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    sessions = _fake_sessions(monkeypatch)
    DeviceLinkAgent(gs, FakeServer())
    gs._dispatch_cues([(ROOM, 0xB0, 74, 7)], at=None)
    assert sessions["TEST_main"].fed == [(0xB0, 74, 7)]
    assert sessions["TEST_accent"].fed == [(0xB0, 74, 7)]


def test_controllers_are_recorded_per_fixture(monkeypatch):
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    _fake_sessions(monkeypatch)
    agent = DeviceLinkAgent(gs, FakeServer())
    gs.on_light_cue("sim-room-accent", 0xB0, 74, 99, None)
    assert agent.controllers() == {"main": {}, "accent": {74: 99}}


def test_room_manifest_is_sliced_per_fixture(monkeypatch):
    """TestBit's ROOM role declares rainbow on `primary`: each fixture's
    session must receive one rainbow decl targeting its own `primary`."""
    gs = _room_ready_game_server(bound={"main": "sim-room-main"})
    sessions = _fake_sessions(monkeypatch)
    DeviceLinkAgent(gs, FakeServer())
    for key in ("TEST_main", "TEST_accent"):
        decls = sessions[key].manifest.instruments
        assert [(d.instrument, d.target) for d in decls] == [("rainbow", "primary")]


def test_room_solid_cue_paints_every_bound_fixture(two_fixture_agent):
    agent, _audio, main, accent = two_fixture_agent
    gs = agent.game_server
    gs._dispatch_cues([SolidCue(ROOM, (255, 0, 0), 1.0, 5.0)], at=1.0)
    agent._render_room()
    frames = {dev: bytes(msg["channels"]) for dev, msg in agent.server.sent}
    assert frames[main] == bytes([255, 0, 0]) * 60
    assert frames[accent] == bytes([255, 0, 0]) * 30
```

Import `SolidCue` and `ROOM` from `control.cues` at the top if missing. Delete or rewrite these now-obsolete tests: `test_room_light_session_built_from_bit_declaration` (assert `set(agent._fixtures) == {"main", "accent"}` instead of `_room_light`), `test_room_dev_cue_routes_to_room_bridge_not_normal_bridges` (rename to `..._to_its_fixture_session...` and use `agent._fixtures["main"].session`), `test_ambient_session_renders_nothing_when_fixtures_declare_no_ambient` (now: sessions exist with empty manifests; assert `agent._fixtures["main"].session.manifest.instruments == []`), `test_room_frame_is_the_bound_fixtures_own_width_not_the_whole_profile` (frames arrive keyed by fixture name), `test_two_bound_fixtures_each_receive_their_own_slice_of_one_render` (each fixture its own render; keep the width assertions), `test_a_room_cue_feeds_the_shared_session_once_and_reaches_both_fixtures` (replaced by `test_a_room_cue_reaches_every_bound_fixtures_session` above), `test_room_override_paints_only_the_canonical_fixtures_slice` (replaced by `test_room_solid_cue_paints_every_bound_fixture`), `test_canonical_override_expiry_clears_only_the_canonical_last_frame` (rename to per-fixture: expiry of main's override forces a resend of main only). Any test asserting a `room_frame` callback's first argument is a dev now expects the fixture name.

In `tests/test_timed_cues.py`, tests that construct `DeviceLinkAgent(..., room_bridge=...)` drop the argument and observe feeds through `_fake_sessions(monkeypatch)` in the same way. Audio assertions there stay keyed by dev until Task 7.

- [ ] **Step 4: Implement the agent rewrite**

In `devicelink/agent.py`:

1. Imports: remove `ambient_manifests` (already `fixture_ambient` after Task 3); add `from control.fixture_sink import ConsoleFrameSink, DeviceLinkSink`, `from control.role_config import compose_role_config, slice_light_manifest`, `from control.cues import ROOM, TARGET`, `from harness.room_surface import to_fixture_capability` (drop `to_capability` if it becomes unused), `from dataclasses import dataclass, field, replace`.
2. Delete `_RoomLightSink`. Add:

```python
@dataclass
class _FixtureState:
    """Everything the agent holds for ONE fixture: its own session over its
    own strip, the universe it renders into, the live controller read-out
    the Console shows, the earliest pending presentation time, and the last
    frame sent (per bound dev, so a rebind forces a resend)."""
    name: str
    session: object
    universe: object
    controllers: dict = field(default_factory=dict)
    pending_at: float | None = None
    last_frame: bytes | None = None
    last_dev: str | None = None
    generators: GeneratorRunner | None = None     # ambient only
```

3. `__init__`: remove the `room_bridge` parameter and `self._room_bridge`, `self._room_light`, `self._ambient_generators`. Add `self._fixtures: dict[str, _FixtureState] = {}`. Keep `_ambient_start`, `_room_audio`, `_room_audio_devs` (Task 7 renames it), `_on_room_frame`.
4. Replace `_setup_room` with:

```python
    def _setup_room(self) -> None:
        """One LightSession per fixture, over that fixture's own strip with
        its LOCAL zone names, for EVERY fixture in the profile whether or
        not a device is bound (spec section 5.1): the Room is loaded with
        all its instruments; devices are outputs (_sinks_for). With a Bit
        ROOM role loaded each fixture gets its slice of the role's light
        manifest (role_config.slice_light_manifest); otherwise its own
        instrument's ambient declaration, or an empty manifest."""
        gs = self.game_server
        room = gs.room
        self._fixtures = {}
        self._ambient_start = None
        if room is None:
            return
        if self._room_profile is None:
            self._room_profile = room.profile
        profile = self._room_profile
        role = None
        if gs.registration is not None:
            role = gs.registration.role_table.roles.get(room_role_name(room.name))
        blob = (compose_role_config(gs.bit_name, gs.bit.version, role)
                if role is not None else None)
        fixture_names = [f.name for f in profile.fixtures]
        for fixture in profile.fixtures:
            if blob is not None:
                light = slice_light_manifest(blob["light_manifest"], profile, fixture.name)
                generators = None
            else:
                light, _ugen = fixture_ambient(fixture)
                light = light or {"instruments": []}
                own = fixture.name
                generators = GeneratorRunner(
                    [fn for fn in fixture.instrument.functions
                     if fn.kind is FunctionKind.GENERATOR],
                    resolve=lambda dev, own=own: (
                        [own] if dev == TARGET else
                        list(fixture_names) if dev == ROOM else []))
            cap = to_fixture_capability(profile, fixture.name)
            session = build_session(LightManifest.from_dict(light), cap, clock=self._clock)
            self._fixtures[fixture.name] = _FixtureState(
                name=fixture.name, session=session,
                universe=Universe(channel_count=fixture.pixel_count * 3),
                generators=generators)
        if blob is None:
            self._ambient_start = self._clock()
        self._grant_room_audio(role)
```

Move the existing audio-grant loop (the block from `if self._room_audio is not None:` through the `first = False` line) verbatim into a new method `_grant_room_audio(self, role)`, computing `ambient_ugen` per fixture inside the loop with `_light, ugen = fixture_ambient(fixture)` and building the ambient stand-in `Role` per fixture from that `ugen`. Task 7 changes its keying; here it still grants per BOUND fixture dev, so keep the `d = gs.room.bound.get(fixture.name); if d is None: continue` lines for now.

5. Delete `_canonical_room_dev`. `rewire_room(self)` takes no argument and just resets `_room_profile = None` and calls `_setup_room()`. `unwire_room` drops the `_room_light` / `_room_bridge` lines and sets `self._fixtures = {}`. Delete the `room_bridge` property. Add:

```python
    def controllers(self) -> dict[str, dict[int, int]]:
        """Live controller read-out per fixture name, for the Console."""
        return {name: dict(st.controllers) for name, st in self._fixtures.items()}

    def _fixture_for_dev(self, dev: str) -> _FixtureState | None:
        gs = self.game_server
        if gs.room is None:
            return None
        for name, bound in gs.room.bound.items():
            if bound == dev:
                return self._fixtures.get(name)
        return None

    def _invalidate_frame(self, dev: str) -> None:
        """Force the next render for `dev` to resend even if unchanged (an
        override landed or expired)."""
        self._last_frames.pop(dev, None)
        st = self._fixture_for_dev(dev)
        if st is not None:
            st.last_frame = None

    def _sinks_for(self, name: str, dev: str | None) -> list:
        sinks = []
        if self._on_room_frame is not None:
            sinks.append(ConsoleFrameSink(name, self._on_room_frame))
        if dev is not None:
            sinks.append(DeviceLinkSink(dev, self._send, protocol.leds_event))
        return sinks
```

6. `_is_room_dev` unchanged. `_feed_light_now`: replace the `_room_bridge` branch with:

```python
        st = self._fixture_for_dev(dev)
        if st is not None:
            try:
                st.session.feed_midi(status, d1, d2)
            except Exception:
                logger.exception("fixture feed_midi failed for %s", dev)
                return
            if status & 0xF0 == 0xB0:
                st.controllers[d1] = d2
            if at is not None and (st.pending_at is None or at < st.pending_at):
                st.pending_at = at
            return
```

then the existing device-bridge path and `_pending_at` handling for non-fixture devs.

7. `_on_light_cue`: the room branch becomes `if self._is_room_dev(dev): self._room_cues.push(when, (dev, status, data1, data2), now=now)` with NO canonical check and no early return.
8. `_render_room`:

```python
    def _render_room(self) -> None:
        if not self._fixtures or self._room_profile is None:
            return
        gs = self.game_server
        bound = gs.room.bound if gs.room is not None else {}
        for (cue_dev, status, d1, d2) in self._room_cues.due(self._clock()):
            if self._room_audio is None or cue_dev not in self._room_audio_devs:
                continue
            try:
                self._room_audio.feed_midi(cue_dev, status, d1, d2)
            except Exception:
                logger.exception("fixture feed_midi failed for %s", cue_dev)
        for fixture in self._room_profile.fixtures:
            st = self._fixtures.get(fixture.name)
            if st is None:
                continue
            dev = bound.get(fixture.name)
            at, st.pending_at = st.pending_at, None
            try:
                st.session.render_into(st.universe)
            except Exception:
                logger.exception("fixture %s render failed; skipping frame", fixture.name)
                continue
            frame = bytes(st.universe.get_frame()[:fixture.pixel_count * 3])
            if dev is not None:
                frame = self._apply_override(dev, frame)
            if dev != st.last_dev:
                st.last_dev, st.last_frame = dev, None
            if frame == st.last_frame:
                continue
            st.last_frame = frame
            when = at if at is not None else self._clock() + self._horizon
            for sink in self._sinks_for(fixture.name, dev):
                sink.send_frame(frame, when)
```

Delete `_emit_room_frame`. In `_tick_overrides`, `_on_solid_cue`, and both branches of `_on_mute_change`, replace `self._last_frames.pop(dev, None)` with `self._invalidate_frame(dev)`.

9. `_feed_ambient_generators`:

```python
    def _feed_ambient_generators(self) -> None:
        if self._ambient_start is None:
            return
        now = self._clock()
        elapsed = now - self._ambient_start
        for st in self._fixtures.values():
            if st.generators is None:
                continue
            for (name, status, data1, value) in st.generators.cues(elapsed, now):
                target = self._fixtures.get(name)
                if target is None:
                    continue
                try:
                    target.session.feed_midi(status, data1, value)
                except Exception:
                    logger.exception("ambient generator feed failed for %s", name)
                    continue
                if status & 0xF0 == 0xB0:
                    target.controllers[data1] = value
```

10. `on_state_change`: `if new_state == State.SETUP and not self._fixtures: self._setup_room()`; `if new_state in (LOADED, IDLE): self._setup_room()` (it resets `_fixtures` itself); replace the `not gs.room.bound` guard with `gs.room is None`.
11. Grep the file for `_room_light`, `_room_bridge`, `_canonical_room_dev`, `_emit_room_frame`, `to_capability(`; none may remain. Update the module docstring (line 2) and the `_pending_at` / `_overrides` comments that mention the canonical dev.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: `tests/test_devicelink_agent.py`, `tests/test_timed_cues.py`, `tests/test_fixture_sink.py` PASS. `tests/test_terrarium_boot.py`, `tests/test_console_agent.py`, `tests/test_room_bridge.py` will FAIL on `room_bridge` plumbing; that is Task 8's work. Confirm the failures are only in those three files before committing.

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py tests/test_timed_cues.py
git commit -m "feat(devicelink): one LightSession per fixture from Room load, frames through FixtureSinks"
```

---

### Task 7: Audio grants keyed by fixture name from Room load

**Files:**
- Modify: `devicelink/agent.py` (`_grant_room_audio`, `_on_light_cue`, `_render_room`, `_on_mute_change`, `unwire_room`, `on_state_change`)
- Test: `tests/test_devicelink_agent.py`, `tests/test_timed_cues.py`

**Interfaces:**
- Produces: `DeviceLinkAgent._room_audio_fixtures: set[str]` (fixture names; replaces `_room_audio_devs`). `AudioBridge` calls (`on_grant`, `feed_midi`, `silence`, `start_drone`, `stop_drone`, `on_release`) are keyed by FIXTURE NAME. `_room_cues` payloads carry the fixture name.
- Consumes: `_fixture_for_dev` (Task 6).

- [ ] **Step 1: Update and add tests**

In `tests/test_devicelink_agent.py`, the per-fixture audio tests (`test_every_audio_fixture_gets_its_own_voice`, `test_fixture_midi_feeds_that_fixtures_voice`, `test_mute_of_one_fixture_silences_only_its_voice`, `test_only_the_first_granted_fixture_plays_the_welcome`, `test_bitless_bound_fixture_still_gets_an_audio_voice`) change every expected key from a dev (`"sim-room-main"`) to the fixture name (`"main"`, `"accent"`). Add:

```python
def test_audio_is_granted_for_every_audio_fixture_even_when_unbound(monkeypatch):
    gs = _room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)
    audio = _FakeAudioBridge()
    DeviceLinkAgent(gs, FakeServer(), room_audio=audio)
    assert sorted(d for d, _role in audio.granted) == ["accent", "main"]


def test_a_fixture_midi_cue_feeds_that_fixtures_voice_by_name(two_fixture_agent):
    agent, audio, main, accent = two_fixture_agent
    audio.fed.clear()
    agent.game_server.on_light_cue(accent, 0x90, 60, 100, None)
    agent._render_room()
    assert audio.fed == [("accent", 0x90, 60, 100)]
```

Check `_FakeAudioBridge`'s recorded attribute names in the file (`granted`, `fed`, `silenced`, ...) and match them.

In `tests/test_timed_cues.py`, audio assertions keyed by dev change to fixture names the same way.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -q -k "audio or voice or welcome"`
Expected: FAIL on the keys.

- [ ] **Step 3: Implement**

In `devicelink/agent.py`:

1. Rename `_room_audio_devs` to `_room_audio_fixtures` everywhere (grep).
2. `_grant_room_audio(role)`: iterate `for fixture in profile.fixtures` with NO bound check; grant with `self._room_audio.on_grant(fixture.name, grant_role)`; add `fixture.name` to `_room_audio_fixtures`; `start_drone(fixture.name)` on the ambient first-fixture branch.
3. `_on_light_cue`: `st = self._fixture_for_dev(dev); if st is not None: self._room_cues.push(when, (st.name, status, data1, data2), now=now)`.
4. `_render_room`'s drain: `for (name, status, d1, d2) in ...: if name not in self._room_audio_fixtures: continue; self._room_audio.feed_midi(name, ...)`.
5. `_on_mute_change`: `st = self._fixture_for_dev(dev); if st is not None: self._room_cues.purge(lambda payload: payload[0] == st.name); if self._room_audio is not None and st.name in self._room_audio_fixtures: self._room_audio.silence(st.name)`.
6. `unwire_room` and `on_state_change` drone loops iterate `_room_audio_fixtures`.
7. Update docstrings that say "keyed by its own real dev".

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: same three files failing as after Task 6 (`test_terrarium_boot.py`, `test_console_agent.py`, `test_room_bridge.py`); everything else PASS.

- [ ] **Step 5: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py tests/test_timed_cues.py
git commit -m "feat(devicelink): grant every audio fixture a voice from Room load, keyed by fixture name"
```

---

### Task 8: Retire RoomBridge; Console controllers and frames by fixture name

**Files:**
- Delete: `control/room_bridge.py`, `tests/test_room_bridge.py`
- Modify: `control/terrarium.py`, `harness/terrarium_boot.py`, `console/agent.py`, `console/protocol.py`, `control/room_view.py`, `console/static/surface.js`
- Test: `tests/test_terrarium_boot.py`, `tests/test_console_agent.py`, `tests/test_room_view.py`, `tests/js/surface_panel.test.js`

**Interfaces:**
- Produces: `ConsoleAgent.__init__(..., room_controllers: Callable[[], dict] | None = None, ...)` replacing `room_bridge=`; `room_view(room, profile, role, controllers: dict[str, dict[int, int]], canvas_urls=None)` emitting `"controllers"` (flat, merged in profile order, first fixture wins on a conflict) and `"fixture_controllers"` (per fixture); `protocol.room_frame_event(fixture: str, channels) -> {"event": "room_frame", "fixture": fixture, "channels": [...]}`; `ConsoleAgent.on_room_frame(fixture: str, frame: bytes)`.
- Consumes: `DeviceLinkAgent.controllers()` and `rewire_room()` (Task 6).

- [ ] **Step 1: Update tests**

`tests/test_terrarium_boot.py`: in `test_build_wires_devicelink_room_bridge_and_simulator` rename to `test_build_wires_devicelink_fixture_sessions_and_simulator` and assert `set(agent._fixtures) == {"main", "accent"}` instead of `_room_light`; delete `test_agent_exposes_its_room_bridge`; grep the file for `room_bridge` and remove every remaining reference.

`tests/test_console_agent.py`: replace `test_room_panel_controllers_read_terrarium_room_bridge_live` with:

```python
def test_room_panel_controllers_come_from_the_injected_callable():
    terrarium = make_terrarium()
    srv = FakeConsoleServer()
    live = {"main": {74: 93}, "accent": {74: 5, 11: 60}}
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium,
                         room_controllers=lambda: live)
    assert terrarium.load_room("TEST") is None
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["room"]["controllers"] == {74: 93, 11: 60}
    assert msg["room"]["fixture_controllers"] == live
```

Grep that file for `room_bridge` and fix every construction. Any test of `on_room_frame` passes a fixture name and expects `{"event": "room_frame", "fixture": "main", ...}`.

`tests/test_room_view.py`: add

```python
def test_room_view_merges_fixture_controllers_first_fixture_wins():
    view = room_view(room, profile, None,
                     {"main": {74: 1}, "accent": {74: 2, 11: 3}})
    assert view["controllers"] == {74: 1, 11: 3}
    assert view["fixture_controllers"] == {"main": {74: 1}, "accent": {74: 2, 11: 3}}
```

using that file's existing `room` / `profile` fixtures (read the file for their names).

`tests/js/surface_panel.test.js`: every `room_frame` message in the file changes `dev: "..."` to `fixture: "<name>"`; add a case where a fixture with `dev: null` receives a `room_frame` and its strip canvas is painted (assert `_lastPaint("main")` is non-empty).

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py tests/test_console_agent.py tests/test_room_view.py tests/test_console_js.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

1. `git rm control/room_bridge.py tests/test_room_bridge.py`.
2. `control/terrarium.py`: remove the `RoomBridge` import, `_canonical_room_dev`, `self.room_bridge` (constructor, `load_room`, `unload_room`, the failure branch), and the `stack.push("room-bridge", ...)` line. Grep the file for `room_bridge`.
3. `harness/terrarium_boot.py`: `DeviceLinkAgent(gs, server, room_audio=room_audio, horizon=..., clock=clock, on_join_denied=..., stale_timeout=...)`; the observer calls `self._agent.rewire_room()`; `ConsoleAgent(..., room_controllers=agent.controllers, ...)`. Fix the observer docstring. Grep for `room_bridge`.
4. `console/agent.py`: `__init__` takes `room_controllers=None` and stores it; `_current_room` computes `controllers = self._room_controllers() if self._room_controllers is not None else {}` (guard with try/except logging and `{}`); `on_room_frame(self, fixture, frame)` stores by fixture; `_broadcast_room_frame` calls `protocol.room_frame_event(fixture, frame)`. Grep for `room_bridge`.
5. `console/protocol.py`: `room_frame_event(fixture, channels)` with the `"fixture"` key.
6. `control/room_view.py`: `room_view` merges:

```python
    merged: dict[int, int] = {}
    for fixture in profile.fixtures:
        for cc, value in (controllers.get(fixture.name) or {}).items():
            merged.setdefault(cc, value)
    return {..., "controllers": merged,
            "fixture_controllers": {k: dict(v) for k, v in controllers.items()}}
```

7. `console/static/surface.js`: rename `canvasesByDev` -> `canvasesByName`, `lastPaintByDev` -> `lastPaintByName`, `lastFrameAt` keyed by name, `repaintDev` -> `repaintFixture(name)`, `_canvasFor(name)`, `_lastPaint(name)`; build canvases per fixture NAME in `renderRoom` regardless of `fixture.dev` (drop the `if (fixture.dev)` gate around canvas registration and the dev-keyed preservation loop; preserve by name); `onRoomFrame` uses `msg.fixture` and no longer returns early on an unknown dev (return early only when no canvas exists for `msg.fixture`). Keep the dev chip display. Update the file's header comment.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS. Then `grep -rn "room_bridge\|RoomBridge\|_canonical_room_dev\|_collapse_room_fanout\|FakeRoomLightSink\|_RoomLightSink\|ambient_manifests" --include=*.py --include=*.js . | grep -v .venv | grep -v docs/` must print nothing.

- [ ] **Step 5: Commit**

```bash
git add -A control/room_bridge.py tests/test_room_bridge.py control/terrarium.py harness/terrarium_boot.py console/agent.py console/protocol.py control/room_view.py console/static/surface.js tests/test_terrarium_boot.py tests/test_console_agent.py tests/test_room_view.py tests/js/surface_panel.test.js
git commit -m "refactor: retire RoomBridge; Console controllers and room frames keyed by fixture name"
```

---

### Task 9: Drop the RoomProfile lane rule; TestBit reference chase

**Files:**
- Modify: `control/room_profile.py`, `bits/test/test_bit.py`
- Test: `tests/test_room_profile.py`, `tests/test_test_bit.py`

**Interfaces:**
- Produces: TestBit `function_table` gains `"chase"` (SCRIPTED, target ROOM, condition `ADMIN_MANUAL`), steps: `(fixture_dev("main"), 0xB0, 74, 127)` at 0.0, `(fixture_dev("accent"), 0xB0, 74, 127)` at 0.5, `(fixture_dev("main"), 0xB0, 74, 0)` at 1.0, `(fixture_dev("accent"), 0xB0, 74, 0)` at 1.0.

- [ ] **Step 1: Update tests**

`tests/test_room_profile.py`: replace `test_two_fixtures_sharing_a_generator_lane_are_refused` with:

```python
def test_two_fixtures_may_share_a_generator_lane():
    """Each fixture has its own session and lane space now (spec section
    3.5); the collision check moved to load_bit, after resolution."""
    first = _generator_instrument("first", data1=74)
    second = _generator_instrument("second", data1=74)
    profile = RoomProfile(surface_id="r", fixtures=(
        _fixture("a", first), _fixture("b", second)))
    assert [f.name for f in profile.fixtures] == ["a", "b"]
```

(reuse the file's fixture-building helper; read lines 17 to 70 for its name). Delete or adjust the test near line 92 that exercises the lane sweep on a SCRIPTED function, since the sweep is gone.

`tests/test_test_bit.py`: add

```python
from control.cues import fixture_dev


def test_chase_steps_main_then_accent():
    fn = TestBit().function_table.functions["chase"]
    assert [(s.offset, s.cue[0]) for s in fn.script] == [
        (0.0, fixture_dev("main")), (0.5, fixture_dev("accent")),
        (1.0, fixture_dev("main")), (1.0, fixture_dev("accent"))]
```

If a test in that file counts TestBit's functions, raise the count by one.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_profile.py tests/test_test_bit.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`control/room_profile.py`: delete the `generator_lanes` loop and its `ValueError` in `__post_init__` (and the `generator_lane` import if unused). Update the class docstring.

`bits/test/test_bit.py`: import `fixture_dev`; add to `function_table`:

```python
            "chase": Function(
                name="chase",
                description="Steps a hue flash from main to accent, then clears "
                            "both: the reference cross-fixture effect, addressed "
                            "by fixture name against the TEST Room spec",
                target=FunctionTarget.ROOM,
                condition=Condition(
                    name="operator_chase", description="Operator fires it",
                    source=ConditionSource.ADMIN_MANUAL),
                script=(
                    ScriptStep(0.0, (fixture_dev("main"), 0xB0, 74, 127)),
                    ScriptStep(0.5, (fixture_dev("accent"), 0xB0, 74, 127)),
                    ScriptStep(1.0, (fixture_dev("main"), 0xB0, 74, 0)),
                    ScriptStep(1.0, (fixture_dev("accent"), 0xB0, 74, 0)),
                ),
            ),
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS. Record the new count.

- [ ] **Step 5: Commit**

```bash
git add control/room_profile.py bits/test/test_bit.py tests/test_room_profile.py tests/test_test_bit.py
git commit -m "feat(bits): TestBit chase addresses fixtures by name; drop the profile lane rule"
```

---

### Task 10: Documentation sync and handoff

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (new dated section after "Per-fixture instruments, operator Stop, and ABORT resilience (2026-09-01)"; mark the N-fixture entry's "One LightSession renders the WHOLE concatenated surface" and the PR #81 entry's "light half ... dropped" as superseded), `docs/superpowers/specs/2026-08-18-n-fixture-room-design.md` (one line under non-goal N1: "Reversed 2026-09-01, see the per-fixture light sessions spec"), `docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md` (add a Status section: Plan 1 landed, Plans 2 and 3 pending, live checklist status)
- Create: `docs/superpowers/handoffs/2026-09-01-rooms-catalog-and-o2-time-handoff.md`

- [ ] **Step 1: Write the deep-dive section**

Add a section titled `### Per-fixture light sessions, @fixture:<name> addressing, FixtureSinks (2026-09-01)` covering, in the deep-dive's bullet style: the Room as ordered fixture list with no session; `_FixtureState` per fixture from Room load, bound or not; `FixtureSink` with the two implementations and the hardware follow-up; `@fixture:<name>` and the owner rule; the manifest slicing rule; `_resolve_devs` and `@room` broadcast; per-fixture generator emission closing the PR #81 parked finding; the load-time fixture contract and post-resolution lane collision; audio grants by fixture name; Console controllers merged plus per fixture, `room_frame` by fixture name, strips painting with no device; the retired symbols list; the accepted limitation (an unbound fixture receives no Bit cues until it binds); the test baseline line. Add `**(Superseded 2026-09-01: ...)**` notes to the N-fixture entry's shared-session bullet and the PR #81 entry's deferred bullet, matching the doc's existing convention.

- [ ] **Step 2: Write the handoff**

Follow the shape of `docs/superpowers/handoffs/2026-09-01-per-fixture-light-sessions-handoff.md`: where Plan 1 left the system, scope of Plan 2 (spec section 6) and Plan 3 (spec section 7, luxaeterna repo), key seams (`control/catalog.py`, `control/terrarium_config.py` `_parse_room`, `console/static/design.js`, `console/static/toml_edit.js`, luxaeterna `synth/session.py` `render_into`), the live checklist from spec section 10 with what has and has not been run, and the house workflow notes.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/python -m pytest tests -q` (record the count in the deep-dive) and `grep -rn $'\xe2\x80\x94' docs/MM_TERRARIUM.md docs/superpowers/handoffs/2026-09-01-rooms-catalog-and-o2-time-handoff.md docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md | grep -v '^.*(Superseded' | head` (only pre-existing em dashes in old sections may appear; none in new text).

```bash
git add docs/MM_TERRARIUM.md docs/superpowers/specs/2026-08-18-n-fixture-room-design.md docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md docs/superpowers/handoffs/2026-09-01-rooms-catalog-and-o2-time-handoff.md
git commit -m "docs: deep-dive sync and handoff for per-fixture light sessions"
```

---

## Live verification (after Task 10, on the dev box, before merge)

**RUN ON: MYCOLOGICAL** (the dev box with the Arco stack). From the worktree:

1. Start the stack with the TEST room and the Console but WITHOUT spawning simulators (use the existing no-simulator boot option documented in `harness/terrarium_boot.py --help`). Load TestBit. Both Console strips animate.
2. Spawn both simulators; both bind and match the strips.
3. From the Functions panel fire `play_aurora` at `sim-room-accent`. Only the accent changes; main keeps drifting.
4. Fire `chase`. Main flashes, then accent, then both clear.
5. Fire Stop at All, then ABORT, Load Room, Load Bit: recovers as in PR #81's checklist.
6. Load DEMO: one strip, unchanged behaviour.

Rainbow continuity across fixtures is checked after Plan 3 lands.
