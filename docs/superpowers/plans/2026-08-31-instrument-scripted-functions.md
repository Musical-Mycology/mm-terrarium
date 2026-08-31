# Instrument-Scripted Functions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scripted Function content moves from Bits to per-instrument authoring (TOML for config instruments, Python for TUNESHROOM); Bits fire by name; Flash/Stop/Ping become capability-derived built-ins fireable with no Bit loaded.

**Architecture:** `GameServer.fire_function` becomes a Bit-optional resolution ladder (built-ins → target instrument's declared functions → logged no-op), resolving per-target-dev so one fire renders differently per instrument. Bit `FunctionTable` SCRIPTED entries lose their scripts (empty script = name-fire); instrument SCRIPTED entries carry the content. Everything downstream (`_dispatch_cues`, timed queue, mute latch, generator suppression, `FunctionFired` records) is reused unchanged.

**Tech Stack:** Python 3 (stdlib-only in `control/`), pytest, TOML config (`terrarium.toml`), vanilla-JS Console modules tested under node.

**Spec:** `docs/superpowers/specs/2026-08-31-instrument-scripted-functions-design.md` (commits fcbd5d3 + 8f778b7 — read BOTH the spec and its amendment row about MetronomeBit before starting).

## Global Constraints

- `control/` stays pure stdlib: no luxaeterna, no pyarco imports (module level), pinned by existing tests.
- Run the suite as `.venv/bin/python -m pytest tests -q` from the worktree root. A fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first. NEVER use bare `python3`.
- Baseline before Task 1: **1669 passed, 1 skipped**. The suite must be green at the end of every task.
- JS tests: `node tests/js/<file>.test.js` individually; all of them run under pytest via `tests/test_console_js.py`.
- Reserved built-in names are exactly `flash`, `stop`, `ping` — refused in every authored FunctionTable (Bit and instrument).
- `fire_function` NEVER raises (existing contract, preserved).
- Instrument scripts address only the implicit self-target: `cues.TARGET` steps only, no `cues.ROOM`, no literal devs.

---

## File structure (locked here)

- **Create** `control/builtins.py` — capability-derived built-in Functions + `RESERVED_NAMES`. Pure, imports only `control.cues`/`control.functions`.
- **Create** `tests/test_builtins.py`, `tests/test_instrument_scripted.py`, `tests/test_fire_ladder.py`, `tests/js/diagnostics_row.test.js`.
- **Modify** `control/functions.py` (owner-aware `validate_function_table`), `control/instrument.py` (v1 validation + TUNESHROOM scripted functions), `control/terrarium_config.py` (`kind = "scripted"` parsing), `terrarium.toml` (room-instrument scripts), `control/engine.py` (ladder + load warnings), `bits/test/test_bit.py`, `bits/metronome/metronome_bit.py`, `control/function_view.py`, `console/protocol.py`, `console/agent.py`, `console/static/functions.js`, plus affected tests.

---

### Task 1: `control/builtins.py` — capability-derived Flash/Stop/Ping

**Files:**
- Create: `control/builtins.py`
- Test: `tests/test_builtins.py`

**Interfaces:**
- Consumes: `control.functions.Function/FunctionKind/ScriptStep`, `control.cues.TARGET/MuteCue/PlayCue/SolidCue`, `control.instrument.Instrument`.
- Produces: `RESERVED_NAMES: frozenset[str]` (`{"flash","stop","ping"}`); `builtin_functions(instrument) -> dict[str, Function]` — SCRIPTED Functions with `condition=None`, `target=None`, non-empty `script`, every step dev = `cues.TARGET`. Constants `PING_KEY = 57`, `PING_VEL = 100`, `PING_OFF_OFFSET = 0.5`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_builtins.py
from control.builtins import RESERVED_NAMES, builtin_functions
from control.cues import TARGET, MuteCue, PlayCue, SolidCue
from control.functions import FunctionKind
from control.instrument import Instrument


def _inst(caps):
    return Instrument(name="t", capabilities=frozenset(caps),
                      accepted_cues=("midi", "play", "solid", "mute"))


def test_reserved_names_are_exactly_flash_stop_ping():
    assert RESERVED_NAMES == frozenset({"flash", "stop", "ping"})


def test_light_only_instrument_gets_flash_and_stop_no_ping():
    fns = builtin_functions(_inst({"light.surface"}))
    assert set(fns) == {"flash", "stop"}
    flash = fns["flash"]
    assert flash.kind is FunctionKind.SCRIPTED
    assert flash.condition is None and flash.target is None
    # light-only: solid white override, no chime
    assert len(flash.script) == 1
    cue = flash.script[0].cue
    assert isinstance(cue, SolidCue)
    assert (cue.dev, cue.rgb, cue.level, cue.duration) == (
        TARGET, (255, 255, 255), 0.9, 5.0)


def test_samples_instrument_flash_adds_chime_and_ping_is_playcue():
    fns = builtin_functions(_inst({"light.pixels", "audio.samples"}))
    assert set(fns) == {"flash", "stop", "ping"}
    kinds = [type(s.cue).__name__ for s in fns["flash"].script]
    assert kinds == ["PlayCue", "SolidCue"]
    ping = fns["ping"].script
    assert len(ping) == 1 and isinstance(ping[0].cue, PlayCue)
    assert ping[0].cue.name == "chime"


def test_flsyn_only_ping_is_a_short_note():
    fns = builtin_functions(_inst({"audio.flsyn"}))
    assert set(fns) == {"stop", "ping"}   # no light.* -> no flash
    steps = fns["ping"].script
    assert [s.cue for s in steps] == [
        (TARGET, 0x90, 57, 100), (TARGET, 0x80, 57, 0)]
    assert [s.offset for s in steps] == [0.0, 0.5]


def test_stop_is_a_mute_latch_and_requires_light_or_audio():
    fns = builtin_functions(_inst({"light.surface"}))
    assert isinstance(fns["stop"].script[0].cue, MuteCue)
    assert builtin_functions(_inst({"gesture.tap"})) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_builtins.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'control.builtins'`

- [ ] **Step 3: Implement `control/builtins.py`**

```python
"""Capability-derived built-in Functions: flash, stop, ping.

Synthesized from an Instrument's capabilities, never authored -- the
operator's troubleshooting vocabulary is identical at every venue (spec
section 2). Names in RESERVED_NAMES are refused in every authored
FunctionTable, Bit or instrument, so a built-in can never be shadowed.
Pure stdlib (control/ discipline).
"""
from __future__ import annotations

from control.cues import TARGET, MuteCue, PlayCue, SolidCue
from control.functions import Function, FunctionKind, ScriptStep

RESERVED_NAMES: frozenset[str] = frozenset({"flash", "stop", "ping"})

PING_KEY = 57
PING_VEL = 100
PING_OFF_OFFSET = 0.5


def builtin_functions(instrument) -> dict[str, Function]:
    """The built-ins this instrument's capabilities support, keyed by name.

    flash needs any light.*; stop needs any light.* or audio.*; ping needs
    an audio capability (samples preferred -- sub-20 ms local path -- else
    a short note through the flsyn voice)."""
    caps = instrument.capabilities
    has_light = any(c.startswith("light.") for c in caps)
    has_samples = "audio.samples" in caps
    has_flsyn = "audio.flsyn" in caps
    out: dict[str, Function] = {}
    if has_light:
        steps = []
        if has_samples:
            steps.append(ScriptStep(0.0, PlayCue(TARGET, "chime", "")))
        steps.append(ScriptStep(0.0, SolidCue(TARGET, (255, 255, 255), 0.9, 5.0)))
        out["flash"] = Function(
            name="flash", kind=FunctionKind.SCRIPTED, script=tuple(steps),
            description="Light test: solid white for 5 s, then resume")
    if has_light or has_samples or has_flsyn:
        out["stop"] = Function(
            name="stop", kind=FunctionKind.SCRIPTED,
            script=(ScriptStep(0.0, MuteCue(TARGET)),),
            description="Latch this surface dark and silent until a play "
                        "un-mutes it")
    if has_samples:
        out["ping"] = Function(
            name="ping", kind=FunctionKind.SCRIPTED,
            script=(ScriptStep(0.0, PlayCue(TARGET, "chime", "")),),
            description="Audio test: play a chime on this surface")
    elif has_flsyn:
        out["ping"] = Function(
            name="ping", kind=FunctionKind.SCRIPTED,
            script=(ScriptStep(0.0, (TARGET, 0x90, PING_KEY, PING_VEL)),
                    ScriptStep(PING_OFF_OFFSET, (TARGET, 0x80, PING_KEY, 0))),
            description="Audio test: a short note through this surface's voice")
    return out
```

- [ ] **Step 4: Run to verify pass, then full suite**

Run: `.venv/bin/python -m pytest tests/test_builtins.py -q` then `.venv/bin/python -m pytest tests -q`
Expected: PASS; suite still 1669+5 passed (numbers grow by this file's tests), 1 skipped.

- [ ] **Step 5: Commit**

```bash
git add control/builtins.py tests/test_builtins.py
git commit -m "feat(builtins): capability-derived flash/stop/ping Functions"
```

---

### Task 2: owner-aware `validate_function_table` + reserved names

**Files:**
- Modify: `control/functions.py` (`validate_function_table` at the bottom third; `_validate_scripted`)
- Modify: `control/instrument.py:86-95` (`validate_instrument`'s function check)
- Test: `tests/test_instrument_scripted.py` (new), plus run existing suites

**Interfaces:**
- Consumes: `control.builtins.RESERVED_NAMES` (Task 1).
- Produces: `validate_function_table(function_table, verb_names, *, owner="bit")` — `owner` is `"bit"` or `"instrument"`. Bit SCRIPTED = name-fire: `script == ()` required, `target`+`condition` required (existing checks). Instrument SCRIPTED = content: non-empty `script`, `target is None`, `condition is None`, script devs `TARGET`-only. Reserved names refused for BOTH owners. GENERATOR unchanged both sides; STREAM allowed only for `owner="bit"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_instrument_scripted.py
import pytest
from control.cues import ROOM, TARGET
from control.functions import (
    Condition, ConditionSource, Function, FunctionKind, FunctionTable,
    FunctionTarget, ScriptStep, validate_function_table,
)


def _scripted(name, script=(), target=None, condition=None):
    return Function(name=name, description="d", kind=FunctionKind.SCRIPTED,
                    script=script, target=target, condition=condition)


def _namefire(name):
    return _scripted(name, script=(), target=FunctionTarget.SURFACE,
                     condition=Condition(name="c", description="d",
                                         source=ConditionSource.ADMIN_MANUAL))


def _content(name, script):
    return _scripted(name, script=script)


def test_bit_scripted_with_a_script_is_refused():
    table = FunctionTable(functions={"aurora": _scripted(
        "aurora", script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),),
        target=FunctionTarget.SURFACE,
        condition=Condition(name="c", description="d",
                            source=ConditionSource.ADMIN_MANUAL))})
    with pytest.raises(ValueError, match="name-fire"):
        validate_function_table(table, frozenset(), owner="bit")


def test_bit_namefire_passes():
    table = FunctionTable(functions={"aurora": _namefire("aurora")})
    validate_function_table(table, frozenset(), owner="bit")


def test_instrument_scripted_requires_content_and_no_condition_or_target():
    ok = FunctionTable(functions={"aurora": _content(
        "aurora", (ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),))})
    validate_function_table(ok, frozenset(), owner="instrument")
    empty = FunctionTable(functions={"aurora": _content("aurora", ())})
    with pytest.raises(ValueError, match="non-empty script"):
        validate_function_table(empty, frozenset(), owner="instrument")
    with_target = FunctionTable(functions={"aurora": _scripted(
        "aurora", (ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),),
        target=FunctionTarget.SURFACE)})
    with pytest.raises(ValueError, match="target"):
        validate_function_table(with_target, frozenset(), owner="instrument")


def test_instrument_script_may_not_address_the_room_sentinel():
    table = FunctionTable(functions={"aurora": _content(
        "aurora", (ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),))})
    with pytest.raises(ValueError, match="TARGET"):
        validate_function_table(table, frozenset(), owner="instrument")


@pytest.mark.parametrize("owner", ["bit", "instrument"])
@pytest.mark.parametrize("name", ["flash", "stop", "ping"])
def test_reserved_names_refused_for_both_owners(owner, name):
    fn = (_namefire(name) if owner == "bit"
          else _content(name, (ScriptStep(0.0, (TARGET, 0xB0, 74, 1)),)))
    with pytest.raises(ValueError, match="reserved"):
        validate_function_table(FunctionTable(functions={name: fn}),
                                frozenset(), owner=owner)


def test_stream_refused_on_instruments():
    from control.functions import StreamOutput, StreamSpec
    fn = Function(name="s", description="d", kind=FunctionKind.STREAM,
                  stream=StreamSpec(verb="tilt", arg=1, in_lo=-1.0, in_hi=1.0,
                                    outputs=(StreamOutput(TARGET, 0xB0, 74,
                                                          0.0, 127.0),)))
    with pytest.raises(ValueError, match="STREAM"):
        validate_function_table(FunctionTable(functions={"s": fn}),
                                frozenset(), owner="instrument")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_instrument_scripted.py -q`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'owner'`

- [ ] **Step 3: Implement**

In `control/functions.py`, change the signature and the SCRIPTED branch:

```python
from control.builtins import RESERVED_NAMES   # add near existing imports
# NOTE: control.builtins imports control.functions -- avoid the cycle by
# importing RESERVED_NAMES lazily INSIDE validate_function_table instead:
#   from control.builtins import RESERVED_NAMES  (function-scoped)

def validate_function_table(function_table, verb_names, *, owner="bit") -> None:
    from control.builtins import RESERVED_NAMES  # lazy: builtins imports us
    if owner not in ("bit", "instrument"):
        raise ValueError(f"owner must be 'bit' or 'instrument', got {owner!r}")
    ...  # existing preamble unchanged
    for key, function_decl in function_table.functions.items():
        where = f"function {key!r}"
        ...  # existing name/description/kind checks unchanged
        if function_decl.name in RESERVED_NAMES:
            raise ValueError(
                f"{where}: {function_decl.name!r} is a reserved built-in "
                f"name (flash/stop/ping) and may not be declared")
        if function_decl.kind is FunctionKind.SCRIPTED:
            if owner == "bit":
                if function_decl.script:
                    raise ValueError(
                        f"{where}: a Bit scripted function is a name-fire "
                        f"and carries no script; content lives on the "
                        f"instrument (declare script=())")
                _validate_scripted(function_decl, allowed_verbs)   # validates
                # target+condition exactly as today; script loop is a no-op
            else:
                if not function_decl.script:
                    raise ValueError(
                        f"{where}: an instrument scripted function must "
                        f"carry a non-empty script")
                if function_decl.target is not None:
                    raise ValueError(
                        f"{where}: target is resolution-time; an instrument "
                        f"function may not declare one")
                if function_decl.condition is not None:
                    raise ValueError(
                        f"{where}: condition is a Bit concern; an "
                        f"instrument function may not declare one")
                _validate_script(function_decl)
                for idx, step in enumerate(function_decl.script):
                    dev = getattr(step.cue, "dev", step.cue[0]
                                  if isinstance(step.cue, tuple) else None)
                    if dev != TARGET:
                        raise ValueError(
                            f"{where} script[{idx}]: instrument scripts "
                            f"implicitly target their own surface; only "
                            f"cues.TARGET is legal, got {dev!r}")
        elif function_decl.kind is FunctionKind.GENERATOR:
            ...  # unchanged
        elif function_decl.kind is FunctionKind.STREAM:
            if owner == "instrument":
                raise ValueError(
                    f"{where}: STREAM functions are Bit-declared gameplay "
                    f"and may not live on an instrument")
            ...  # unchanged
```

In `control/instrument.py`, `validate_instrument` (lines 86-95): drop the GENERATOR-only refusal; allow GENERATOR and SCRIPTED, and call the table validator with `owner="instrument"`:

```python
    for fn in instrument.functions:
        if not isinstance(fn, Function) or fn.kind not in (
                FunctionKind.GENERATOR, FunctionKind.SCRIPTED):
            raise InstrumentError(
                f"instrument {instrument.name!r}: only generator and "
                f"scripted Functions may be declared on an instrument")
    table = FunctionTable(functions={fn.name: fn for fn in instrument.functions})
    try:
        validate_function_table(table, verb_names=frozenset(),
                                owner="instrument")
    except ValueError as exc:
        raise InstrumentError(f"instrument {instrument.name!r}: {exc}") from exc
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_instrument_scripted.py tests/test_builtins.py -q`
Expected: PASS. Then `.venv/bin/python -m pytest tests -q` — EXPECT FAILURES in existing tests that declare Bit-side scripts (`tests/test_engine_functions.py`'s fixture Bit declares `"stop"` with a script, TestBit/MetronomeBit load tests, console tests). Record the failing list — Tasks 4-6 burn it down; do NOT fix them here beyond confirming every failure is a to-be-migrated declaration, not a new defect.

- [ ] **Step 5: Commit**

```bash
git add control/functions.py control/instrument.py tests/test_instrument_scripted.py
git commit -m "feat(functions): owner-aware validation -- Bit name-fires, instrument script content, reserved names"
```

---

### Task 3: TUNESHROOM's scripted functions (MetronomeBit device content + aurora/win)

**Files:**
- Modify: `control/instrument.py:185-199` (the `TUNESHROOM` constant), adding module-level script builders above it
- Test: extend `tests/test_instrument_scripted.py`

**Interfaces:**
- Consumes: Task 2's `owner="instrument"` validation.
- Produces: `TUNESHROOM.functions` containing SCRIPTED Functions named `play_aurora`, `win`, `fireworks_player`, `fail_player`, `metro_pulse_player`, `metro_recovery` (content below, byte-identical to today's Bit scripts with every dev = `TARGET`). MetronomeBit's constants move with them: define at module level in `control/instrument.py`: `RED_CC = 5`, `GREEN_CC = 78`, `LEVEL_BASE = 40`, `LEVEL_PULSE = 96` — **first read the actual values from `bits/metronome/metronome_bit.py`'s constants and copy them verbatim; the numbers here are illustrative and MUST be replaced by the real ones** (this is the one place this plan defers to the source file, because the Bit keeps importing them back from `control.instrument` afterward to avoid drift).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_instrument_scripted.py
def test_tuneshroom_declares_its_scripted_vocabulary():
    from control.instrument import TUNESHROOM, validate_instrument
    from control.functions import FunctionKind
    names = {fn.name for fn in TUNESHROOM.functions
             if fn.kind is FunctionKind.SCRIPTED}
    assert names == {"play_aurora", "win", "fireworks_player",
                     "fail_player", "metro_pulse_player", "metro_recovery"}
    validate_instrument(TUNESHROOM)   # v1 rules hold


def test_tuneshroom_fireworks_matches_the_bits_seeded_script():
    # Deterministic (random.Random(2026)) -- 12 flashes, 36 steps.
    from control.instrument import TUNESHROOM
    fw = next(fn for fn in TUNESHROOM.functions
              if fn.name == "fireworks_player")
    assert len(fw.script) == 36
    assert fw.script[0].offset == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_instrument_scripted.py -q`
Expected: FAIL — TUNESHROOM has no scripted functions.

- [ ] **Step 3: Implement**

Move `_fireworks_script()` (verbatim, seeded `random.Random(2026)`) from `bits/metronome/metronome_bit.py` into `control/instrument.py` (module-private `_fireworks_script`), move the four constants (real values), and extend the constant:

```python
TUNESHROOM = Instrument(
    name="tuneshroom",
    ...  # existing fields unchanged
    functions=(
        Function(name="play_aurora", kind=FunctionKind.SCRIPTED,
                 description="Hue bloom on the handheld's ring",
                 script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                         ScriptStep(1.0, (TARGET, 0xB0, 74, 0)))),
        Function(name="win", kind=FunctionKind.SCRIPTED,
                 description="Win celebration: ascending chime plus a hue "
                             "flourish",
                 script=(ScriptStep(0.0, PlayCue(TARGET, "win", "")),
                         ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                         ScriptStep(0.3, (TARGET, 0xB0, 74, 60)),
                         ScriptStep(0.6, (TARGET, 0xB0, 74, 110)),
                         ScriptStep(1.2, (TARGET, 0xB0, 74, 0)))),
        Function(name="fireworks_player", kind=FunctionKind.SCRIPTED,
                 description="Celebratory flashes on the player who nailed it",
                 script=_fireworks_script()),
        Function(name="fail_player", kind=FunctionKind.SCRIPTED,
                 description="Player's light goes red and dark on a miss",
                 script=(ScriptStep(0.0, (TARGET, 0xB0, 74, RED_CC)),
                         ScriptStep(1.0, (TARGET, 0xB0, 11, 0)))),
        Function(name="metro_pulse_player", kind=FunctionKind.SCRIPTED,
                 description="A non-failed player's every-beat level "
                             "pulse-then-decay",
                 script=(ScriptStep(0.0, (TARGET, 0xB0, 11, LEVEL_PULSE)),
                         ScriptStep(0.15, (TARGET, 0xB0, 11, LEVEL_BASE)))),
        Function(name="metro_recovery", kind=FunctionKind.SCRIPTED,
                 description="A failed player's green flash and level reset",
                 script=(ScriptStep(0.0, (TARGET, 0xB0, 74, GREEN_CC)),
                         ScriptStep(0.0, (TARGET, 0xB0, 11, LEVEL_BASE)))),
    ),
    event_triggers=(...),   # unchanged
)
```

Imports needed at top of `control/instrument.py`: `random`, `ScriptStep`, `TARGET`, `PlayCue` (`from control.cues import TARGET, PlayCue` — check this does not create a cycle; `control.cues` imports nothing from `control.instrument`, so it is safe). `win`'s content is TestBit's current `win` script verbatim; `play_aurora`'s tuneshroom variant is the two-step bloom above (the per-instrument-difference exemplar — the Room's TOML version in Task 4 keeps TestBit's original three steps).

- [ ] **Step 4: Run to verify pass** — same two commands as Task 2 Step 4; same expectation (target tests green, known migration failures unchanged).

- [ ] **Step 5: Commit**

```bash
git add control/instrument.py tests/test_instrument_scripted.py
git commit -m "feat(instrument): TUNESHROOM declares its scripted vocabulary (metronome device content, aurora, win)"
```

---

### Task 4: TOML `kind = "scripted"` parsing + `terrarium.toml` room scripts

**Files:**
- Modify: `control/terrarium_config.py:100-165` (`_parse_functions`)
- Modify: `terrarium.toml` (`[instruments.venue_array]`, `[instruments.dev_strip]`)
- Test: extend the existing terrarium-config test module (find it: `grep -l terrarium_config tests/*.py`; it is `tests/test_terrarium_config.py`)

**Interfaces:**
- Consumes: Task 2's instrument-owner validation (already wired through `validate_instrument` inside `_parse_instrument`).
- Produces: `[[instruments.<name>.functions]]` entries accepting `kind = "scripted"` with `script = [ ... ]` where each step is a table with `offset` (float, required) plus exactly one of: `midi = [status, data1, data2]`, `play = "<sample>"`, `solid = { rgb = [r,g,b], level = <0..1>, duration = <s> }`, `mute = true`. Parsed into `Function(kind=SCRIPTED, script=(ScriptStep(offset, cue), ...))` with dev `TARGET` supplied by the parser.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_terrarium_config.py (reuse its existing load helper /
# tmp-file pattern -- read the module top first and follow it)
SCRIPTED_TOML = """
schema = 1
[terrarium]
name = "t"
[instruments.arr]
capabilities = ["light.surface", "audio.flsyn"]
accepted_cues = ["midi", "play", "solid", "mute"]
[[instruments.arr.functions]]
name = "play_aurora"
kind = "scripted"
description = "sweep"
script = [
  { offset = 0.0, midi = [176, 74, 127] },
  { offset = 0.5, midi = [176, 74, 40] },
  { offset = 2.0, midi = [176, 74, 0] },
]
[rooms.R]
backends = ["devicelink"]
[[rooms.R.fixtures]]
name = "main"
color_order = "GRB"
instrument = "arr"
[[rooms.R.fixtures.blocks]]
name = "main"
start = 0
count = 10
[[rooms.R.fixtures.zones]]
name = "all"
start = 0
count = 10
"""

def test_scripted_function_parses_with_target_devs(tmp_path):
    cfg = _load_from_string(SCRIPTED_TOML, tmp_path)   # module's helper
    from control.cues import TARGET
    from control.functions import FunctionKind
    arr = cfg.instruments["arr"]
    fn = next(f for f in arr.functions if f.name == "play_aurora")
    assert fn.kind is FunctionKind.SCRIPTED
    assert [s.cue for s in fn.script] == [
        (TARGET, 176, 74, 127), (TARGET, 176, 74, 40), (TARGET, 176, 74, 0)]


def test_scripted_reserved_name_is_a_located_config_error(tmp_path):
    bad = SCRIPTED_TOML.replace('name = "play_aurora"', 'name = "flash"')
    with pytest.raises(TerrariumConfigError, match="reserved"):
        _load_from_string(bad, tmp_path)


def test_scripted_step_with_no_cue_key_is_located(tmp_path):
    bad = SCRIPTED_TOML.replace("midi = [176, 74, 127]", "offset2 = 1")
    with pytest.raises(TerrariumConfigError, match="exactly one of"):
        _load_from_string(bad, tmp_path)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q`
Expected: new tests FAIL with `kind 'scripted' must be 'generator'`.

- [ ] **Step 3: Implement the parser branch**

In `_parse_functions`, replace the `kind != "generator"` refusal with a dispatch; add:

```python
def _parse_script_step(iname, fname, idx, sraw, *, source, key):
    def err(message):
        return TerrariumConfigError(source=source, key=key,
            message=f"function {fname!r} script[{idx}]: {message}")
    if not isinstance(sraw, dict):
        raise err(f"must be a table, got {type(sraw).__name__}")
    offset = sraw.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        raise err(f"offset must be a number, got {offset!r}")
    cue_keys = [k for k in ("midi", "play", "solid", "mute") if k in sraw]
    if len(cue_keys) != 1:
        raise err("must carry exactly one of midi/play/solid/mute")
    kind = cue_keys[0]
    if kind == "midi":
        v = sraw["midi"]
        if (not isinstance(v, list) or len(v) != 3
                or any(isinstance(x, bool) or not isinstance(x, int) for x in v)):
            raise err(f"midi must be [status, data1, data2] ints, got {v!r}")
        return ScriptStep(float(offset), (TARGET, v[0], v[1], v[2]))
    if kind == "play":
        v = sraw["play"]
        if not isinstance(v, str) or not v:
            raise err(f"play must be a sample name, got {v!r}")
        return ScriptStep(float(offset), PlayCue(TARGET, v, ""))
    if kind == "solid":
        v = sraw["solid"]
        if not isinstance(v, dict):
            raise err(f"solid must be a table, got {type(v).__name__}")
        rgb = v.get("rgb")
        if (not isinstance(rgb, list) or len(rgb) != 3):
            raise err(f"solid.rgb must be [r, g, b], got {rgb!r}")
        return ScriptStep(float(offset), SolidCue(
            TARGET, tuple(rgb), v.get("level", 1.0), v.get("duration")))
    return ScriptStep(float(offset), MuteCue(TARGET))
```

and in the entry loop:

```python
        kind = entry.get("kind", "generator")
        if kind == "scripted":
            sraw_list = entry.get("script")
            if not isinstance(sraw_list, list) or not sraw_list:
                raise TerrariumConfigError(source=source, key=key,
                    message=f"function {name!r}: scripted functions require "
                            f"a non-empty script array")
            steps = tuple(
                _parse_script_step(iname, name, i, s, source=source, key=key)
                for i, s in enumerate(sraw_list))
            functions.append(Function(
                name=name, description=entry.get("description", ""),
                kind=FunctionKind.SCRIPTED, script=steps))
            continue
        if kind != "generator":
            raise TerrariumConfigError(source=source, key=key,
                message=f"function {name!r}: kind {kind!r} must be "
                        f"'generator' or 'scripted'")
```

Imports: `from control.cues import TARGET, MuteCue, PlayCue, SolidCue` and `ScriptStep` from `control.functions`. Reserved names, deep step validation (offsets monotonic, ranges) and the TARGET-only rule all arrive free via `validate_instrument` in `_parse_instrument` — assert the reserved-name test passes through that path (the error message check `match="reserved"` relies on Task 2's wording).

Then extend **`terrarium.toml`**: under BOTH `[instruments.venue_array]` and `[instruments.dev_strip]` add `[[...functions]]` scripted entries for `play_aurora` (TestBit's original 3-step sweep: `[176,74,127]@0.0, [176,74,40]@0.5, [176,74,0]@2.0`), `win` (TestBit's: `play="win"@0.0, [176,74,127]@0.0, [176,74,60]@0.3, [176,74,110]@0.6, [176,74,0]@1.2`), and MetronomeBit's Room set copied byte-for-byte from `bits/metronome/metronome_bit.py` (statuses/data as written there, `0x..` converted to decimal): `fireworks_room` (the 36 seeded steps — generate the literal list once with a scratch script that prints `_fireworks_script()` steps), `fail_room`, `finale` (24 steps, same approach), `metro_downbeat`, `metro_click`, `metro_pulse_room`.

- [ ] **Step 4: Run to verify pass** — `tests/test_terrarium_config.py` green; full suite: the migration-failure list from Task 2 should now be shrinking (config loads).

- [ ] **Step 5: Commit**

```bash
git add control/terrarium_config.py terrarium.toml tests/test_terrarium_config.py
git commit -m "feat(config): instruments author scripted functions in terrarium.toml; room content lands"
```

---

### Task 5: the fire ladder — Bit-optional `fire_function`, per-dev resolution, load warnings

**Files:**
- Modify: `control/engine.py` (`fire_function` ~line 656, `_suppress_generator_lanes`, `load_bit` validation block ~line 298, add `_instrument_for` + `_resolve_script_for` helpers near `_check_cue_kinds`)
- Test: `tests/test_fire_ladder.py` (new)

**Interfaces:**
- Consumes: `builtin_functions`/`RESERVED_NAMES` (Task 1), owner-aware validation (Task 2), TUNESHROOM functions (Task 3).
- Produces (REDIRECTED 2026-08-31 — Bits keep their scripts; see the spec's mid-execution-redirect row): `fire_function(name, *, fired_by, dev=None, at=None)` semantics: (a) Bit table consulted only in SETUP/RUNNING; **a declared entry with a NON-EMPTY script fires exactly as today's code path does — byte-identical behavior, first rung of the ladder**; (b) a declared entry with an EMPTY script is a name-fire: its `target`/`condition` metadata applies but content resolves down the ladder; (c) an undeclared name defaults to `target=SURFACE` (dev required, `cues.ROOM` sentinel allowed) and works in ANY state, Bit or none; (d) ladder per resolved dev: built-ins → that dev's instrument SCRIPTED function of that name → skip (logged); (e) a fire resolving zero devs-with-script returns None and emits a `FunctionFired` with `steps=0`; (f) `condition` on the record = Bit condition name when declared, else `"builtin"`/`"instrument"`; `declared_source` = Bit's when declared, else `fired_by`. Also `GameServer.load_warnings: tuple[str, ...]` set on every `load_bit` — gap check applies ONLY to name-fire (empty-script) declarations, so unmigrated Bits produce zero warnings — + observer hook `on_load_warnings(warnings)`. The Task 5 test `test_load_warnings_report_target_aware_gaps`'s NameFireBit (empty script) is still valid. TestBit/MetronomeBit MUST load and behave unchanged after this task; the full suite must be GREEN at the end of Task 5 (the red window closed with the Task 2 redirect).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fire_ladder.py
from control.engine import GameServer
from control.cues import ROOM
from control.functions import FIRED_BY_ADMIN_MANUAL
from control.instrument import Instrument
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.rooms import Room

ARR = Instrument(name="arr", capabilities=frozenset({"light.surface",
                                                     "audio.flsyn"}),
                 accepted_cues=("midi", "play", "solid", "mute"))
PROFILE = RoomProfile(surface_id="r", fixtures=(
    RoomFixture(name="main", color_order="GRB",
                blocks=(RoomBlock("main", 0, 10),),
                zones=(RoomZone("all", 0, 10),), instrument=ARR),))


def _gs_with_room():
    gs = GameServer({})
    gs.room = Room(name="R", profile=PROFILE, node_id="N")
    gs.room.bound["main"] = "fix-dev"
    return gs


def test_builtin_fires_with_no_bit_in_idle():
    gs = _gs_with_room()
    seen = []
    gs.on_solid_cue = lambda dev, rgb, level, duration, when: seen.append(dev)
    assert gs.fire_function("flash", fired_by=FIRED_BY_ADMIN_MANUAL,
                            dev=ROOM) is None
    assert seen == ["fix-dev"]


def test_ping_on_flsyn_room_emits_the_note_pair():
    gs = _gs_with_room()
    cues = []
    gs.on_light_cue = lambda dev, status, data1, data2, when: cues.append(
        (dev, status, data1, data2))
    assert gs.fire_function("ping", fired_by=FIRED_BY_ADMIN_MANUAL,
                            dev=ROOM) is None
    assert cues == [("fix-dev", 0x90, 57, 100), ("fix-dev", 0x80, 57, 0)]


def test_unknown_name_is_a_zero_step_fire_not_an_error():
    gs = _gs_with_room()
    fired = []
    class Obs:
        def on_function_fired(self, record): fired.append(record)
    gs.add_observer(Obs())
    assert gs.fire_function("nope", fired_by=FIRED_BY_ADMIN_MANUAL,
                            dev=ROOM) is None
    assert fired[-1].steps == 0 and fired[-1].devs == ()


def test_surface_fire_without_dev_still_refused():
    gs = _gs_with_room()
    assert gs.fire_function("flash",
                            fired_by=FIRED_BY_ADMIN_MANUAL) is not None


def test_load_warnings_report_target_aware_gaps():
    # A Bit name-fire with ROOM target whose name no room instrument
    # declares warns; the same name on TUNESHROOM does not suppress it.
    from tests.test_engine import RoomCapableBit  # reuse existing fixture Bit
    # RoomCapableBit is migrated by Task 6 -- here build a minimal local Bit:
    from control.bit import Bit
    from control.functions import (Condition, ConditionSource, Function,
                                   FunctionKind, FunctionTable, FunctionTarget)
    from control.roles import RoleTable
    class NameFireBit(Bit):
        version = "1"
        @property
        def role_table(self): return RoleTable(roles={}, node_map={})
        @property
        def function_table(self):
            return FunctionTable(functions={"aurora_room": Function(
                name="aurora_room", description="d",
                kind=FunctionKind.SCRIPTED, script=(),
                target=FunctionTarget.ROOM,
                condition=Condition(name="c", description="d",
                                    source=ConditionSource.ADMIN_MANUAL))})
    gs = _gs_with_room()
    gs.bit_registry["NF"] = NameFireBit
    gs.load_bit("NF")
    assert any("aurora_room" in w and "arr" in w for w in gs.load_warnings)
```

(Adjust the `Bit` subclass to the real abstract surface — read `control/bit.py` first and implement whatever hooks are abstract as no-ops.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_fire_ladder.py -q`
Expected: FAIL — `"no Bit running"` refusals and missing `load_warnings`.

- [ ] **Step 3: Implement in `control/engine.py`**

Helpers (near `_check_cue_kinds`):

```python
    def _instrument_for(self, dev: str):
        """The Instrument behind a resolved dev: a bound Room fixture's
        declared instrument, else the device's carried instrument
        (TUNESHROOM default -- same fallback _check_cue_kinds uses)."""
        if self.room is not None and self.room.bound:
            for fixture in self.room.profile.fixtures:
                if self.room.bound.get(fixture.name) == dev:
                    return fixture.instrument
        info = self.devices.get(dev)
        if info is None:
            return None
        return getattr(info, "carried", None) or TUNESHROOM

    def _resolve_script_for(self, name: str, instrument):
        """The ladder, per instrument: built-ins first (reserved names make
        shadowing impossible), then the instrument's own SCRIPTED function,
        else None."""
        if instrument is None:
            return None
        builtin = builtin_functions(instrument).get(name)
        if builtin is not None:
            return builtin
        for fn in instrument.functions:
            if fn.name == name and fn.kind is FunctionKind.SCRIPTED:
                return fn
        return None
```

`fire_function` rewrite (keep the docstring, extend it): drop the top `state not in (SETUP, RUNNING)` refusal. Read the Bit table only when `self.bit is not None and self.state in (State.SETUP, State.RUNNING)` (same try/except guards as today). Then:

```python
        decl = table.functions.get(name) if table is not None else None
        if decl is not None and decl.kind is not FunctionKind.SCRIPTED:
            return f"function {name!r} is not scripted"
        target = decl.target if decl is not None else FunctionTarget.SURFACE
        # dev-required check: identical wording to today, driven by `target`
        ...
        if at is None:
            at = self._clock() + self._horizon
        devs = self._collapse_room_fanout(self._resolve_target(target, dev))
        all_cues, resolved = [], []
        for d in devs:
            fn = self._resolve_script_for(name, self._instrument_for(d))
            if fn is None:
                logger.info("function %r: no script for %r; skipped", name, d)
                continue
            dev_cues = expand_script(fn, at, (d,))
            if self._check_cue_kinds(dev_cues) is not None:
                logger.info("function %r: cue kind refused for %r; skipped",
                            name, d)
                continue
            if self._generators is not None:
                self._suppress_generator_lanes(fn, dev_cues, at)
            all_cues.extend(dev_cues)
            resolved.append(d)
        if all_cues and not any(isinstance(c, MuteCue) for c in all_cues):
            self._clear_mutes(resolved)
        self._dispatch_cues(all_cues, at)
        self._notify("on_function_fired", FunctionFired(
            name=name,
            condition=(decl.condition.name if decl is not None
                       else ("builtin" if name in RESERVED_NAMES
                             else "instrument")),
            fired_by=fired_by,
            declared_source=(SOURCE_WIRE[decl.condition.source]
                             if decl is not None else fired_by),
            dev=dev, devs=tuple(resolved), at=at, steps=len(all_cues),
            room_name=self.provenance.get("room_name")))
        return None
```

All of that stays inside the existing broad `try/except ... return "function script error"` guard. Imports: `from control.builtins import RESERVED_NAMES, builtin_functions`.

`load_bit` (after `validate_function_table(function_table, set(bit.verb_handlers()), owner="bit")` — add the `owner` argument here too): build target-aware warnings:

```python
        room_instruments = {}
        if self.room is not None:
            for fixture in self.room.profile.fixtures:
                room_instruments[fixture.instrument.name] = fixture.instrument
        carried_instruments = {TUNESHROOM.name: TUNESHROOM}
        warnings = []
        for fn in function_table.functions.values():
            if fn.kind is not FunctionKind.SCRIPTED:
                continue
            if fn.target is FunctionTarget.DEVICE:
                check = carried_instruments
            elif fn.target is FunctionTarget.ROOM:
                check = room_instruments
            else:   # SURFACE, ALL
                check = {**room_instruments, **carried_instruments}
            for iname, inst in check.items():
                if self._resolve_script_for(fn.name, inst) is None:
                    warnings.append(
                        f"function {fn.name!r} has no script on instrument "
                        f"{iname!r}; fires there will no-op")
        self.load_warnings = tuple(warnings)
        if warnings:
            self._notify("on_load_warnings", self.load_warnings)
```

Initialize `self.load_warnings: tuple[str, ...] = ()` in `__init__` and reset it in `_unload`.

- [ ] **Step 4: Run to verify pass** — `tests/test_fire_ladder.py` green; full suite: remaining failures must all be in the Bit/console migration set (Task 6/7 targets).

- [ ] **Step 5: Commit**

```bash
git add control/engine.py tests/test_fire_ladder.py
git commit -m "feat(engine): Bit-optional fire ladder -- builtins, per-dev instrument scripts, load-gap warnings"
```

---

### Task 6: Bit/built-in interplay verification (REDIRECTED 2026-08-31 — the Bit migration is deferred)

**Chris's mid-execution redirect: TestBit and MetronomeBit are NOT migrated. They keep their scripted functions, scripts and all. Everything below the REDIRECTED SCOPE block is the ORIGINAL task text, retained for the record only — do NOT execute it.**

**REDIRECTED SCOPE — what this task now does:**
- Verify zero-migration: `bits/` is untouched by this branch (`git diff main -- bits/` empty), TestBit and MetronomeBit load and run exactly as on main.
- Add integration tests (in `tests/test_fire_ladder.py`): (1) with TestBit loaded and RUNNING, firing its declared `stop` (which shares a reserved built-in name) runs TESTBIT's script — the Bit's non-empty script is the ladder's first rung and shadows the built-in; (2) with TestBit loaded, firing `flash` (which TestBit does NOT declare) resolves the built-in — undeclared names fall through the ladder even mid-Bit; (3) `gs.load_warnings == ()` after loading TestBit and after loading MetronomeBit (non-empty-script declarations never warn).
- Fix the two straggler test files the Task 2 redirect's fix round may have left (`tests/test_functions.py`, `tests/test_instrument.py`): only assertions about INSTRUMENT-side validation change (instruments now accept scripted functions); every Bit-side assertion must pass unchanged.
- Full suite green; commit `test(ladder): pin Bit-script-first precedence and zero-warning unmigrated Bits`.

--- ORIGINAL (superseded) TEXT BELOW ---


**Files:**
- Modify: `bits/test/test_bit.py` (function_table + `_on_tap`), `bits/metronome/metronome_bit.py` (function_table; script builders/constants now imported from `control.instrument`), `tests/test_engine_functions.py` (fixture Bits: `"stop"` at line ~172 renames to `"halt"` or becomes a name-fire; every Bit-declared script empties), `tests/test_test_bit.py` (`SCRIPTED_FUNCTION_NAMES` etc.), `tests/instrument_fixtures.py` (give `GENERIC_SURFACE` the scripted functions engine tests fire), MetronomeBit's test module, `tests/test_console_agent.py` where function shapes are asserted.

**Interfaces:**
- Consumes: everything above.
- Produces: every Bit SCRIPTED entry is a name-fire (`script=()`); `flash_device` and `stop` DELETED from TestBit; TestBit's `_on_tap` returns `FireFunction("flash", dev)`; the tap-condition name-fire card that replaces `flash_device` is not re-declared (built-ins cover it — the `tap` handler simply fires the built-in). MetronomeBit keeps all ten names as name-fires with conditions/targets unchanged.

- [ ] **Step 1: Run the suite and enumerate the failures** — `.venv/bin/python -m pytest tests -q 2>&1 | tail -40`. Every failure must map to a file in this task's list; anything else is a Task 2-5 regression to fix first.

- [ ] **Step 2: Migrate TestBit** — delete the `flash_device` and `stop` entries; `play_aurora`/`win` keep name, description, target, condition and get `script=()`; `_on_tap` (line ~447) returns `[..., FireFunction("flash", dev)]`; delete the now-unused `MuteCue`/`SolidCue` imports. `fires()` (line ~388) keeps `FireFunction("play_aurora", ROOM)` unchanged.

- [ ] **Step 3: Migrate MetronomeBit** — all ten entries keep name/description/target/condition, `script=()`; `_fireworks_script`/`_finale_script` and the moved constants are deleted here and imported from `control.instrument` where the Bit still references them (`LEVEL_BASE` etc. in `fires()` beat logic stays working).

- [ ] **Step 4: Migrate the test fleet** — fixture Bits in `tests/test_engine_functions.py` and `tests/test_test_bit.py`: empty their scripts, and move the cue-content assertions onto instrument declarations: extend `tests/instrument_fixtures.py`'s `GENERIC_SURFACE` with the SCRIPTED functions those tests fire (same names the fixture Bits declare), so engine dispatch tests still assert concrete cue output. `test_test_bit.py:280`'s `SCRIPTED_FUNCTION_NAMES` becomes `{"play_aurora", "win"}`; its line-311 `stop` lookup is deleted (Console `stop` coverage moves to the built-in tests). Where a test asserted `flash_device` end-to-end, re-point it at the `flash` built-in.

- [ ] **Step 5: Full suite green** — `.venv/bin/python -m pytest tests -q` → all passed (count will differ from baseline; record it).

- [ ] **Step 6: Commit**

```bash
git add bits/ tests/
git commit -m "refactor(bits): TestBit and MetronomeBit fire by name; scripts live on instruments"
```

---

### Task 7: Console backend — instrument functions on the wire, warning log lines

**Files:**
- Modify: `control/function_view.py` (`_scripted_view` tolerates `target=None`/`condition=None`; add `instrument_functions_view`), `console/protocol.py` (`functions_changed_event(functions, instruments=None, surfaces=None, builtins=None)`; snapshot gains the same three keys), `console/agent.py` (`_current_functions` builds all three; `on_load_warnings` observer broadcasting `protocol.log_event("warn", w)` per warning)
- Test: `tests/test_console_agent.py` additions

**Interfaces:**
- Produces (wire): `snapshot`/`functions_changed` carry `functions` (Bit name-fire views, `script: []`), `instrument_functions: {instrument_name: [function_view,...]}` (SCRIPTED only), `surface_instruments: {dev_or_"room": instrument_name}` (Room fixtures via `terrarium/gs.room`; connected devices via `info.carried` name or `"tuneshroom"`), `builtins: {instrument_name: [names]}`. `log` events (existing shape `{event:"log", level, message}`) for each load warning.

- [ ] **Step 1: Write failing tests** — in `tests/test_console_agent.py`: (a) snapshot for a `_server_with_agent()`-style setup with a Room carrying `ARR`-like instrument shows `instrument_functions` keyed by instrument name with the declared scripted views and `builtins` listing `["flash","ping","stop"]` sorted; (b) loading a Bit whose name-fire misses an instrument broadcasts a `log` event with level `warn`; (c) `functions` entries for name-fires carry `"script": []` and their `target`/`condition` as before.

- [ ] **Step 2-4: Implement, run, iterate** — `_scripted_view` change: `"target": function_decl.target.name if function_decl.target else None`, same guard for `condition` (emit `None`). New builder in `control/function_view.py`:

```python
def instrument_functions_view(instruments: dict) -> dict:
    """{instrument name: [scripted function views]} for every present
    instrument; SCRIPTED only (generators render on the Room panel)."""
    from control.functions import FunctionKind
    return {name: [function_view(fn) for fn in inst.functions
                   if fn.kind is FunctionKind.SCRIPTED]
            for name, inst in instruments.items()}
```

`ConsoleAgent` collects present instruments exactly like `load_bit`'s warning block (room fixtures + TUNESHROOM), builds `builtins` via `sorted(builtin_functions(inst))`, and re-broadcasts `functions_changed` when any of the three views change (fold into the existing `_broadcast_functions_if_changed` signature diff).

- [ ] **Step 5: Full suite green, commit**

```bash
git add control/function_view.py console/protocol.py console/agent.py tests/test_console_agent.py
git commit -m "feat(console): instrument function views, builtins map, load-warning log lines"
```

---

### Task 8: `functions.js` — diagnostics row + compatibility filtering

**Files:**
- Modify: `console/static/functions.js`
- Test: `tests/js/diagnostics_row.test.js` (new; copy the harness boilerplate — `_dom_stub.js` import, FakeSocket connect, snapshot send — from `tests/js/functions_and_rail.test.js`), extend `tests/js/functions_and_rail.test.js` for filtering.

**Behavior to implement and assert:**
1. **Diagnostics row**: always rendered (even `functions: []`, no Bit): one surface picker (Room + every device, reusing `fillDevicePicker`) plus three buttons Flash/Stop/Ping. A button is `disabled` when the selected surface's instrument (via `surface_instruments` → `builtins`) lacks that name. Click sends `wire.send("fire_function", {name: "flash", dev: <picker value>}, btn)`.
2. **Name-fire cards**: SURFACE/DEVICE pickers grey out (`option.disabled = true`) surfaces whose instrument lacks the card's name in `instrument_functions` (built-ins always compatible); the card's description line re-renders on picker change to the resolved instrument's function description (fallback: the Bit's own description).
3. Existing no-rebuild-on-fire discipline preserved (assert card node identity across a `function_fired`, as the existing test does).

- [ ] **Steps: write the failing node test, run `node tests/js/diagnostics_row.test.js` (fails), implement, re-run (passes), run `.venv/bin/python -m pytest tests/test_console_js.py -q` (all JS files green), commit**

```bash
git add console/static/functions.js tests/js/
git commit -m "feat(console-js): diagnostics row and instrument-compatibility filtering"
```

---

### Task 9: verification + docs

- [ ] **Step 1:** Full suite: `.venv/bin/python -m pytest tests -q` — green; record the new baseline count.
- [ ] **Step 2:** Spec status: flip `docs/superpowers/specs/2026-08-31-instrument-scripted-functions-design.md` header to `Status: Implemented (this branch)` and note the final suite count.
- [ ] **Step 3:** Deep-dive: add a slice section to `docs/MM_TERRARIUM.md` (in-repo, rides this branch per the docs convention) covering: the fire ladder, reserved built-ins, where instrument functions are authored (TOML vs TUNESHROOM Python), the load-warning surface, and the TestBit/MetronomeBit migration. This is the mm-deepdive-sync obligation handled in-branch.
- [ ] **Step 4:** Commit docs; the live-Arco checklist from spec section 5 is a post-merge activity — list it in the PR body, do not attempt it headless.

```bash
git add docs/
git commit -m "docs(terrarium): deep-dive slice for instrument-scripted functions"
```

---

## Self-review (performed at plan-write time)

- **Spec coverage:** section 1 → Tasks 2/3/4; section 2 → Tasks 1/5; section 3 (ladder, name-fires, migrations, warnings) → Tasks 5/6; section 4 → Tasks 7/8; section 5 → tests in every task + Task 9. Amendment row (MetronomeBit/TUNESHROOM) → Tasks 3/4/6.
- **Known judgment points for executors:** exact constant values moved in Task 3 MUST be copied from `bits/metronome/metronome_bit.py`, not from this plan's illustrative numbers; Task 4's TOML fireworks/finale literals are generated from the moved Python builders, not hand-typed.
- **Type consistency:** `builtin_functions(instrument) -> dict[str, Function]` used identically in Tasks 1/5/7; `owner=` keyword consistent across Tasks 2/5; wire keys `instrument_functions`/`surface_instruments`/`builtins` consistent across Tasks 7/8.
