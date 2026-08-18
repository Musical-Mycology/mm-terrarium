# Bit-declared triggers, cue scripts, and conditions: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Bit declare, as data, that a named thing exists, what makes it
happen, and what it does, so the Terrarium Console can show an operator what will
make something happen and let them fire it by hand.

**Architecture:** A new `TriggerTable` is declared parallel to the existing
`RoleTable` and validated at `load_bit` exactly as `control/role_config.py`
already validates manifests. Actions are declarative cue scripts (an ordered list
of `(offset_seconds, cue)`) rather than callables, expanded at fire time into the
`LightCue`/`PlayCue` vocabulary `GameServer._dispatch_cues` already dispatches, so
the existing far-future-cue holding in `DeviceLinkAgent._on_light_cue` does all
the scheduling. The Bit evaluates its own conditions and reports a fire by
returning a new `FireTrigger` cue; Control expands, dispatches, and emits a
`TriggerFired` record on a new multi-observer engine hook.

**Tech Stack:** Python 3 stdlib only for `control/`; `pytest` for the suite;
plain browser JavaScript with no build step for `console/static/`; Node used only
as a test runner via `vm`.

**Spec:** [`docs/superpowers/specs/2026-08-17-bit-declared-triggers-and-cue-scripts-design.md`](../specs/2026-08-17-bit-declared-triggers-and-cue-scripts-design.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **Run the suite ONLY as `.venv/bin/python -m pytest tests -v`.** There is no
  bare `python` on these boxes and luxaeterna is installed only in `.venv`. Using
  `python3` produces a phantom import error in `tests/test_terrarium_boot.py`
  that looks exactly like a real failure.
- **A fresh git worktree has no `.venv`.** Symlink it from the worktree root
  before anything else: `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`
- **Baseline: 844 passed, 1 skipped.** Verified at commit `6c03ede` by running
  the suite, not read off a doc. No task may regress it. The per-task
  checkpoints below give a floor rather than an exact total, because a
  reviewer adding a case must not read as a failure.
- **The suite must stay green with no O2, no Arco and no pyarco importable.**
- **No `control/` module may import luxaeterna, pyarco or o2litepy at module
  level.** One deliberate function-scoped exception exists at
  `control/arco_process.py:37`, marked `# noqa: PLC0415 (lazy by design)`. A test
  enforces the module-level rule.
- **No build step for the console.** No npm, no bundler, no external asset fetch.
- **New browser code gets behavioral tests, not substring greps.** Two Important
  defects reached a live browser run during Spec A because `room.js` was checked
  only by grepping its own source text.
- **House style: no em dashes** in code, comments, docstrings, or prose. Use `--`,
  a comma, or a restructured sentence.
- **Console exposure is addition, never relaxation.** Never loosen an existing
  filter to make something appear; add a separately scoped payload key.
- **Commit after every task.** Conventional-commit prefixes (`feat:`, `test:`,
  `docs:`, `refactor:`).

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `control/triggers.py` | The declaration dataclasses, `validate_trigger_table`, `expand_script`, `TriggerFired`. Pure: stdlib plus `control.cues` only. |
| `control/trigger_view.py` | The Console read model. Pure dict builders, no engine imports, mirroring `control/room_view.py`. |
| `console/static/triggers.js` | The trigger panel. Loaded by the existing directory-glob asset map, so `console/server.py` needs no change. |
| `tests/test_triggers.py` | Declaration + validation unit tests. |
| `tests/test_trigger_expansion.py` | `expand_script` unit tests. |
| `tests/test_trigger_view.py` | Read-model unit tests. |
| `tests/test_engine_triggers.py` | Engine fire-path tests. |
| `tests/js/trigger_panel_behavior.test.js` | Browser behavioral test under a DOM stub. |
| `tests/test_trigger_panel_behavior.py` | pytest wrapper, skips cleanly with no node. |

**Modified:**

| File | Change |
|---|---|
| `control/cues.py` | Add the `TARGET` sentinel and the `FireTrigger` cue type. |
| `control/bit.py` | Add the `trigger_table` property, defaulting to empty. |
| `control/engine.py` | `load_bit` validation; `_dispatch_cues` gains `fired_by` and a `FireTrigger` branch; new `fire_trigger` and `_resolve_target`. |
| `devicelink/agent.py` | Clear `_light_cues`/`_room_cues` at `UNLOADING`. |
| `console/protocol.py` | `triggers_changed_event`, `trigger_fired_event`, `FireTriggerCommand`, `snapshot_event` gains `triggers`. |
| `console/agent.py` | Snapshot key, change broadcast, `on_trigger_fired`, fire command handling. |
| `console/static/index.html` | Triggers section and `<script src="triggers.js">`. |
| `console/static/console.js` | Two dispatch cases and one snapshot line. |
| `console/static/style.css` | Trigger-card and source-tag rules. |
| `bits/test_bit.py` | Two declared triggers and their adjudication. |
| `docs/MM_TERRARIUM.md` | Deep-dive sync. |

**Deliberately NOT modified:** `console/server.py` (its asset map globs the
static directory), `uplink/link.py`, `uplink/protocol.py`, `control/rooms.py`,
`control/room_profile.py`, `control/room_view.py`, `control/room_binding.py`.

---

### Task 1: The declaration types and their load-time validation

Spec sections 5 and 6.

**Files:**
- Modify: `control/cues.py` (append after `LightCue`)
- Create: `control/triggers.py`
- Test: `tests/test_triggers.py`

**Interfaces:**
- Consumes: `control.cues.ROOM`, `control.cues.LightCue`, `control.cues.PlayCue`.
- Produces: `control.cues.TARGET: str`, `control.cues.FireTrigger(name, dev=None)`;
  `control.triggers.TriggerTarget` (`ROOM`/`DEVICE`/`ALL`),
  `control.triggers.ConditionSource` (`GESTURE_VERB`/`BIT_ADJUDICATED`/`ADMIN_MANUAL`),
  `Condition(name, description, source, verb=None)`,
  `ScriptStep(offset, cue)`,
  `Trigger(name, description, target, condition, script=())`,
  `TriggerTable(triggers={})`,
  `SOURCE_WIRE: dict[ConditionSource, str]`,
  `FIRED_BY_GESTURE_VERB`/`FIRED_BY_BIT_ADJUDICATED`/`FIRED_BY_ADMIN_MANUAL: str`,
  `validate_trigger_table(trigger_table, verb_names) -> None` (raises `ValueError`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_triggers.py`:

```python
"""Declaration shape and load-time validation for Bit-declared triggers.

Mirrors tests/test_role_config.py's discipline: one case per refusal, each
asserting the message locates the offending field, because a Bit author reads
the message and nothing else.
"""

import pytest

from control.cues import ROOM, TARGET, FireTrigger, LightCue, PlayCue
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerTable,
    TriggerTarget,
    validate_trigger_table,
)

VERBS = {"tap", "tilt"}


def _condition(**overrides):
    base = dict(name="round_won", description="User wins a round",
                source=ConditionSource.BIT_ADJUDICATED)
    base.update(overrides)
    return Condition(**base)


def _trigger(**overrides):
    base = dict(name="play_aurora", description="A slow aurora sweep",
                target=TriggerTarget.ROOM, condition=_condition(),
                script=(ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),))
    base.update(overrides)
    return Trigger(**base)


def _table(trigger):
    return TriggerTable(triggers={trigger.name: trigger})


def test_a_well_formed_table_validates():
    validate_trigger_table(_table(_trigger()), VERBS)


def test_an_empty_table_validates():
    validate_trigger_table(TriggerTable(triggers={}), VERBS)


def test_an_empty_script_is_legal_and_means_observe_only():
    validate_trigger_table(_table(_trigger(script=())), VERBS)


def test_a_key_disagreeing_with_its_trigger_name_is_refused():
    table = TriggerTable(triggers={"mislabelled": _trigger()})
    with pytest.raises(ValueError, match="does not match its key"):
        validate_trigger_table(table, VERBS)


def test_a_name_with_characters_illegal_in_a_dom_id_is_refused():
    with pytest.raises(ValueError, match="play aurora"):
        validate_trigger_table(_table(_trigger(name="play aurora")), VERBS)


def test_an_empty_trigger_description_is_refused():
    with pytest.raises(ValueError, match="description must be non-empty"):
        validate_trigger_table(_table(_trigger(description="")), VERBS)


def test_an_empty_condition_description_is_refused():
    bad = _condition(description="")
    with pytest.raises(ValueError, match="description must be non-empty"):
        validate_trigger_table(_table(_trigger(condition=bad)), VERBS)


def test_a_gesture_verb_condition_naming_an_unimplemented_verb_is_refused():
    """Goal 4: declared-but-unimplemented fails at load, not mid-installation."""
    bad = _condition(source=ConditionSource.GESTURE_VERB, verb="wiggle")
    with pytest.raises(ValueError, match="'wiggle' is not implemented"):
        validate_trigger_table(_table(_trigger(condition=bad)), VERBS)


def test_a_gesture_verb_condition_with_no_verb_is_refused():
    bad = _condition(source=ConditionSource.GESTURE_VERB, verb=None)
    with pytest.raises(ValueError, match="must name a verb"):
        validate_trigger_table(_table(_trigger(condition=bad)), VERBS)


def test_a_verb_on_a_non_gesture_condition_is_refused():
    bad = _condition(source=ConditionSource.BIT_ADJUDICATED, verb="tap")
    with pytest.raises(ValueError, match="only meaningful on a gesture-verb"):
        validate_trigger_table(_table(_trigger(condition=bad)), VERBS)


def test_a_negative_offset_is_refused():
    bad = (ScriptStep(-0.5, (ROOM, 0xB0, 74, 127)),)
    with pytest.raises(ValueError, match=r"script\[0\]: offset must be >= 0"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_out_of_order_offsets_are_refused():
    bad = (ScriptStep(1.0, (ROOM, 0xB0, 74, 127)),
           ScriptStep(0.5, (ROOM, 0xB0, 74, 0)))
    with pytest.raises(ValueError, match="non-decreasing order"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_light_cue_in_a_script_is_refused():
    """The offset IS the timing; LightCue is what expansion produces."""
    bad = (ScriptStep(0.0, LightCue(ROOM, 0xB0, 74, 127, when=1.0)),)
    with pytest.raises(ValueError, match="names its own absolute time"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_play_cue_at_a_non_zero_offset_is_refused():
    bad = (ScriptStep(1.5, PlayCue(TARGET, "click", "")),)
    with pytest.raises(ValueError, match="must sit at offset 0"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_play_cue_at_offset_zero_is_accepted():
    good = (ScriptStep(0.0, PlayCue(TARGET, "click", "")),)
    validate_trigger_table(_table(_trigger(script=good)), VERBS)


def test_a_literal_dev_id_in_a_step_is_refused():
    bad = (ScriptStep(0.0, ("ie1", 0xB0, 74, 127)),)
    with pytest.raises(ValueError, match="assigned at runtime"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_wrong_arity_cue_tuple_is_refused():
    bad = (ScriptStep(0.0, (ROOM, 0xB0, 74)),)
    with pytest.raises(ValueError, match="4-tuple"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_data_byte_outside_0_255_is_refused():
    bad = (ScriptStep(0.0, (ROOM, 0xB0, 74, 300)),)
    with pytest.raises(ValueError, match="data2 300 is outside 0-255"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_a_fire_trigger_in_a_script_is_refused_so_chaining_cannot_cycle():
    bad = (ScriptStep(0.0, FireTrigger("play_aurora")),)
    with pytest.raises(ValueError, match="4-tuple"):
        validate_trigger_table(_table(_trigger(script=bad)), VERBS)


def test_target_is_used_by_name_not_by_value():
    assert {t.name for t in TriggerTarget} == {"ROOM", "DEVICE", "ALL"}


def test_fire_trigger_defaults_its_dev_to_none():
    assert FireTrigger("x").dev is None
    assert FireTrigger("x", "ie1").dev == "ie1"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_triggers.py -v
```

Expected: collection error, `ImportError: cannot import name 'TARGET' from 'control.cues'`.

- [ ] **Step 3: Add the sentinel and the cue type to `control/cues.py`**

Append to the end of `control/cues.py`:

```python
# Sentinel dev id for a cue script step addressed at whatever the firing
# trigger declared as its target. Substituted during expansion
# (control.triggers.expand_script), before the cue ever reaches
# GameServer._resolve_dev, so that method is not edited by the trigger slice
# and ROOM resolution keeps working exactly as it does today. See
# docs/superpowers/specs/
# 2026-08-17-bit-declared-triggers-and-cue-scripts-design.md section 7.2.
TARGET = "@target"


@dataclass(frozen=True)
class FireTrigger:
    """A Bit's report that one of its own declared conditions is satisfied.

    Returned in the same list a Bit already returns cues in, from a verb
    handler or from cues(at), so a fire inherits that path's single
    presentation time and lands on the same frame as the ordinary cues
    returned beside it. `dev` names the device the fire is about, when there
    is one; it is what TriggerTarget.DEVICE resolves to.

    A distinct type rather than a magic tuple, for the same reason PlayCue and
    LightCue are: GameServer._dispatch_cues tells cue kinds apart by identity,
    never by guessing at tuple arity.
    """
    name: str
    dev: str | None = None
```

- [ ] **Step 4: Create `control/triggers.py`**

```python
"""Bit-declared triggers: the named things an operator can see coming.

A Bit declares a TriggerTable parallel to its RoleTable. Each entry names a
thing that can happen, describes it in words an operator reads, says where it
lands, names the condition the BIT evaluates, and carries a declarative cue
script: an ordered list of (offset_seconds, cue).

Declarative rather than a callable for three reasons. The Console can render
the real steps rather than only a prose description; a test can assert the
exact cue sequence with no Arco; and manual fire becomes pushing data through
the dispatch path that already exists rather than calling into Bit code at an
arbitrary moment.

Pure and stdlib-only apart from control.cues, which is itself pure, so this
module imports in the offline suite with no renderer and no Arco. Validation
lives here rather than in a sibling config module because, unlike
control/role_config.py, there is no composed device-side blob to keep apart
from the declaration, and expand_script belongs next to the shape it expands.

See docs/superpowers/specs/
2026-08-17-bit-declared-triggers-and-cue-scripts-design.md sections 5 to 7.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum, auto

from control.cues import ROOM, TARGET, LightCue, PlayCue


class TriggerTarget(Enum):
    ROOM = auto()      # every Room fixture
    DEVICE = auto()    # the device that fired, when there was one
    ALL = auto()       # Room fixtures plus every registered non-ROOM device


class ConditionSource(Enum):
    GESTURE_VERB = auto()       # a device gesture the Bit adjudicates
    BIT_ADJUDICATED = auto()    # decided inside the Bit, no message behind it
    ADMIN_MANUAL = auto()       # exists only to be fired by an operator


# Wire spellings, so the Console and the fired record read the same words and
# neither has to lowercase an enum name and hope.
SOURCE_WIRE: dict[ConditionSource, str] = {
    ConditionSource.GESTURE_VERB: "gesture-verb",
    ConditionSource.BIT_ADJUDICATED: "bit-adjudicated",
    ConditionSource.ADMIN_MANUAL: "admin-manual",
}

# What actually fired a trigger THIS time, which is not the same thing as the
# source its condition declares: an operator may fire a gesture-verb trigger by
# hand, and the record has to keep those distinguishable. See TriggerFired.
FIRED_BY_GESTURE_VERB = "gesture-verb"
FIRED_BY_BIT_ADJUDICATED = "bit-adjudicated"
FIRED_BY_ADMIN_MANUAL = "admin-manual"

# A trigger name becomes a DOM id in console/static/triggers.js, the same way
# a capture label becomes a path component in capture/store.py. Restricted at
# the declaration boundary for the same reason: it is cheaper to refuse an odd
# name at Bit load than to escape it at every consumer.
_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")

_LEGAL_SCRIPT_DEVS = (TARGET, ROOM)


@dataclass(frozen=True)
class Condition:
    """What makes a trigger fire, in the Bit's own words.

    `verb` is required exactly when source is GESTURE_VERB, and is metadata
    plus something real for load-time validation to check. It does NOT cause
    Control to fire the trigger when that verb arrives: a tap that misses or a
    tilt below threshold must not fire, so the Bit's handler returns a
    FireTrigger explicitly or does not. Auto-firing on verb dispatch would put
    condition evaluation, however trivial, inside Control.
    """
    name: str
    description: str
    source: ConditionSource
    verb: str | None = None


@dataclass(frozen=True)
class ScriptStep:
    """One step of a cue script. `offset` is seconds from the trigger's `at`.

    `cue` is a plain (dev, status, data1, data2) tuple or a PlayCue, whose dev
    is cues.TARGET or cues.ROOM. A LightCue is refused: it names its own
    absolute time, and the offset is this step's timing.
    """
    offset: float
    cue: object


@dataclass(frozen=True)
class Trigger:
    name: str
    description: str
    target: TriggerTarget
    condition: Condition
    script: tuple[ScriptStep, ...] = ()


@dataclass
class TriggerTable:
    triggers: dict[str, Trigger] = field(default_factory=dict)


@dataclass(frozen=True)
class TriggerFired:
    """One fire, as the Console and any future uplink observer see it.

    fired_by and declared_source are separate on purpose. A manual fire of a
    gesture-verb trigger records fired_by="admin-manual" against
    declared_source="gesture-verb"; collapsing them is what would make an
    operator action indistinguishable from real gameplay in the log.

    devs and steps report what the fire RESOLVED to, not what it declared, so a
    fire that reached nothing is visibly that rather than silently absent.
    """
    name: str
    condition: str
    fired_by: str
    declared_source: str
    dev: str | None
    devs: tuple[str, ...]
    at: float
    steps: int


def validate_trigger_table(trigger_table, verb_names) -> None:
    """Shallow structural validation of a Bit's authored TriggerTable.

    Called from GameServer.load_bit alongside validate_role_declarations, and
    raises ValueError with a message locating the offending field, so a typo'd
    Bit fails as a load-time BitLoadError rather than mid-installation.

    `verb_names` is the key set of the Bit's verb_handlers(). Cross-referencing
    it here is what makes a declared-but-unimplemented gesture trigger a load
    failure. Deliberately shallow everywhere else, in the same places
    control/role_config.py is: a cue's controller number is not checked against
    any manifest lane, because an undeclared cc is already dropped by design in
    AudioBridge._apply_midi, and the light half's instrument registry belongs
    to luxaeterna, which Control cannot see.
    """
    if not isinstance(trigger_table, TriggerTable):
        raise ValueError(
            f"trigger_table: must be a TriggerTable, "
            f"got {type(trigger_table).__name__}")
    for key, trigger in trigger_table.triggers.items():
        where = f"trigger {key!r}"
        if not isinstance(trigger, Trigger):
            raise ValueError(
                f"{where}: must be a Trigger, got {type(trigger).__name__}")
        if trigger.name != key:
            raise ValueError(
                f"{where}: name {trigger.name!r} does not match its key")
        if not isinstance(trigger.name, str) or not _NAME_RE.match(trigger.name):
            raise ValueError(
                f"{where}: name {trigger.name!r} must match [A-Za-z0-9_-]+ "
                f"(it becomes a DOM id on the Console)")
        if not isinstance(trigger.description, str) or not trigger.description:
            raise ValueError(f"{where}: description must be non-empty")
        if not isinstance(trigger.target, TriggerTarget):
            raise ValueError(
                f"{where}: target must be a TriggerTarget, "
                f"got {trigger.target!r}")
        _validate_condition(trigger, verb_names)
        _validate_script(trigger)


def _validate_condition(trigger: Trigger, verb_names) -> None:
    where = f"trigger {trigger.name!r} condition"
    condition = trigger.condition
    if not isinstance(condition, Condition):
        raise ValueError(
            f"{where}: must be a Condition, got {type(condition).__name__}")
    if not isinstance(condition.name, str) or not condition.name:
        raise ValueError(f"{where}: name must be non-empty")
    if not isinstance(condition.description, str) or not condition.description:
        raise ValueError(f"{where}: description must be non-empty")
    if not isinstance(condition.source, ConditionSource):
        raise ValueError(
            f"{where}: source must be a ConditionSource, "
            f"got {condition.source!r}")
    if condition.source is ConditionSource.GESTURE_VERB:
        if not condition.verb:
            raise ValueError(
                f"{where}: a gesture-verb condition must name a verb")
        if condition.verb not in verb_names:
            raise ValueError(
                f"{where}: verb {condition.verb!r} is not implemented by "
                f"verb_handlers() (implemented: {sorted(verb_names)})")
    elif condition.verb is not None:
        raise ValueError(
            f"{where}: verb {condition.verb!r} is only meaningful on a "
            f"gesture-verb condition, not on "
            f"{SOURCE_WIRE[condition.source]}")


def _validate_script(trigger: Trigger) -> None:
    script = trigger.script
    if not isinstance(script, tuple):
        raise ValueError(
            f"trigger {trigger.name!r} script: must be a tuple, "
            f"got {type(script).__name__}")
    previous: float | None = None
    for idx, step in enumerate(script):
        where = f"trigger {trigger.name!r} script[{idx}]"
        if not isinstance(step, ScriptStep):
            raise ValueError(
                f"{where}: must be a ScriptStep, got {type(step).__name__}")
        offset = _validate_offset(step, where, previous)
        previous = offset
        _validate_step_cue(step, where)


def _validate_offset(step: ScriptStep, where: str,
                     previous: float | None) -> float:
    offset = step.offset
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        raise ValueError(f"{where}: offset must be a number, got {offset!r}")
    offset = float(offset)
    if not math.isfinite(offset):
        raise ValueError(f"{where}: offset must be finite, got {step.offset!r}")
    if offset < 0:
        raise ValueError(f"{where}: offset must be >= 0, got {offset}")
    if previous is not None and offset < previous:
        raise ValueError(
            f"{where}: offset {offset} is earlier than the previous step's "
            f"{previous}; steps must be in non-decreasing order, because the "
            f"Console renders them as a sequence")
    return offset


def _validate_step_cue(step: ScriptStep, where: str) -> None:
    cue = step.cue
    if isinstance(cue, LightCue):
        raise ValueError(
            f"{where}: a LightCue names its own absolute time, and a script "
            f"step's timing is its offset. Declare a plain "
            f"(dev, status, data1, data2) tuple instead")
    if isinstance(cue, PlayCue):
        if float(step.offset) != 0.0:
            raise ValueError(
                f"{where}: a PlayCue must sit at offset 0. The device owns "
                f"when a local sample fires and the play path has no queue, so "
                f"a non-zero offset would be silently ignored")
        _validate_script_dev(cue.dev, where)
        return
    if not isinstance(cue, tuple) or len(cue) != 4:
        raise ValueError(
            f"{where}: cue must be a PlayCue or a 4-tuple "
            f"(dev, status, data1, data2), got {cue!r}")
    dev, status, data1, data2 = cue
    _validate_script_dev(dev, where)
    for label, value in (("status", status), ("data1", data1),
                         ("data2", data2)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where}: {label} must be an int, got {value!r}")
        if not 0 <= value <= 255:
            raise ValueError(f"{where}: {label} {value} is outside 0-255")


def _validate_script_dev(dev, where: str) -> None:
    if dev not in _LEGAL_SCRIPT_DEVS:
        raise ValueError(
            f"{where}: dev must be cues.TARGET ({TARGET!r}) or cues.ROOM "
            f"({ROOM!r}), got {dev!r}. Device ids are assigned at runtime, so "
            f"a literal in a static declaration can never resolve")
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_triggers.py -v
```

Expected: PASS, 22 tests.

- [ ] **Step 6: Confirm the no-third-party-import invariant still holds**

```bash
.venv/bin/python -m pytest tests -q -k "import" -v
```

Expected: PASS. `control/triggers.py` imports only `math`, `re`, `dataclasses`,
`enum` and `control.cues`.

- [ ] **Step 7: Commit**

```bash
git add control/cues.py control/triggers.py tests/test_triggers.py
git commit -m "feat(triggers): declaration types and load-time validation

A Bit declares a TriggerTable parallel to its RoleTable: named triggers with
a description, a target, a Bit-evaluated condition, and a declarative cue
script. validate_trigger_table refuses a gesture-verb condition naming a verb
verb_handlers() does not implement, which is the declared-but-unimplemented
check the spec's goal 4 asks for.

cues.TARGET and cues.FireTrigger land here because validation needs both to
say which devs a script step may address."
```

---

### Task 2: Script expansion

Spec section 7.2.

**Files:**
- Modify: `control/triggers.py` (append `expand_script` and `_step_devs`)
- Test: `tests/test_trigger_expansion.py`

**Interfaces:**
- Consumes: Task 1's `Trigger`, `ScriptStep`, `control.cues.TARGET`/`ROOM`/`PlayCue`/`LightCue`.
- Produces: `control.triggers.expand_script(trigger, at, devs) -> list`, returning
  `LightCue(dev, status, data1, data2, when=at + offset)` and
  `PlayCue(dev, name, params)` objects, ready for `GameServer._dispatch_cues`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trigger_expansion.py`:

```python
"""expand_script: a declarative script becomes concrete, timed cues.

Pure and engine-free by construction, which is half the point of scripts being
data: the exact cue sequence a trigger produces is assertable with no Arco, no
renderer and no GameServer.
"""

from control.cues import ROOM, TARGET, LightCue, PlayCue
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerTarget,
    expand_script,
)

AT = 100.0


def _trigger(script, target=TriggerTarget.ROOM):
    return Trigger(
        name="t", description="d", target=target,
        condition=Condition(name="c", description="cd",
                            source=ConditionSource.BIT_ADJUDICATED),
        script=script)


def test_an_empty_script_expands_to_nothing():
    assert expand_script(_trigger(()), AT, ["sim-room"]) == []


def test_each_step_carries_its_offset_added_to_at():
    trigger = _trigger((
        ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
        ScriptStep(0.5, (TARGET, 0xB0, 74, 40)),
        ScriptStep(2.0, (TARGET, 0xB0, 74, 0)),
    ))
    out = expand_script(trigger, AT, ["sim-room"])
    assert [c.when for c in out] == [100.0, 100.5, 102.0]
    assert [c.data2 for c in out] == [127, 40, 0]
    assert all(isinstance(c, LightCue) for c in out)


def test_target_is_substituted_with_the_resolved_dev():
    trigger = _trigger((ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),))
    out = expand_script(trigger, AT, ["sim-room"])
    assert [c.dev for c in out] == ["sim-room"]


def test_target_fans_out_to_every_resolved_dev():
    trigger = _trigger((ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),),
                       target=TriggerTarget.ALL)
    out = expand_script(trigger, AT, ["sim-room", "ie1", "ie2"])
    assert [c.dev for c in out] == ["sim-room", "ie1", "ie2"]
    assert {c.when for c in out} == {100.0}


def test_target_with_no_resolved_devs_expands_to_nothing():
    trigger = _trigger((ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),),
                       target=TriggerTarget.DEVICE)
    assert expand_script(trigger, AT, []) == []


def test_a_room_addressed_step_is_left_for_resolve_dev_downstream():
    """ROOM passes through untouched: GameServer._resolve_dev already turns it
    into the Room's bound dev, so this module never needs to know what a Room
    is, and _resolve_dev is not edited by this slice."""
    trigger = _trigger((ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),),
                       target=TriggerTarget.DEVICE)
    out = expand_script(trigger, AT, ["ie1"])
    assert [c.dev for c in out] == [ROOM]


def test_a_room_step_does_not_fan_out_even_when_several_devs_resolved():
    trigger = _trigger((ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),),
                       target=TriggerTarget.ALL)
    out = expand_script(trigger, AT, ["sim-room", "ie1", "ie2"])
    assert [c.dev for c in out] == [ROOM]


def test_a_play_cue_step_keeps_its_name_and_params_and_gains_the_dev():
    trigger = _trigger((ScriptStep(0.0, PlayCue(TARGET, "click", "soft")),),
                       target=TriggerTarget.DEVICE)
    out = expand_script(trigger, AT, ["ie1"])
    assert out == [PlayCue("ie1", "click", "soft")]


def test_a_mixed_script_preserves_declaration_order():
    trigger = _trigger((
        ScriptStep(0.0, PlayCue(TARGET, "click", "")),
        ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
    ), target=TriggerTarget.DEVICE)
    out = expand_script(trigger, AT, ["ie1"])
    assert isinstance(out[0], PlayCue)
    assert isinstance(out[1], LightCue)


def test_expansion_never_produces_a_fire_trigger_so_chaining_cannot_cycle():
    trigger = _trigger((ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),))
    out = expand_script(trigger, AT, ["sim-room"])
    assert all(isinstance(c, (LightCue, PlayCue)) for c in out)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_trigger_expansion.py -v
```

Expected: collection error, `ImportError: cannot import name 'expand_script'`.

- [ ] **Step 3: Append the implementation to `control/triggers.py`**

```python
def expand_script(trigger: Trigger, at: float, devs) -> list:
    """Turn a declared script into concrete, timed cues for _dispatch_cues.

    Every light step becomes a LightCue carrying an explicit when of
    at + offset. GameServer._dispatch_cues already honors a LightCue's own
    when, and DeviceLinkAgent._on_light_cue already holds a cue further out
    than one horizon on its _light_cues queue, so this slice adds no
    scheduler and no second copy of horizon arithmetic. That holding branch
    was written on 2026-08-14 for exactly this case; expansion only supplies
    it with input.

    A step addressed at cues.TARGET fans out to every dev the trigger's target
    resolved to. A step addressed at cues.ROOM is left alone and resolved
    downstream, so one script can address the Room explicitly even when its
    own target is DEVICE.
    """
    out: list = []
    for step in trigger.script:
        when = at + float(step.offset)
        cue = step.cue
        if isinstance(cue, PlayCue):
            for dev in _step_devs(cue.dev, devs):
                out.append(PlayCue(dev, cue.name, cue.params))
            continue
        step_dev, status, data1, data2 = cue
        for dev in _step_devs(step_dev, devs):
            out.append(LightCue(dev, status, data1, data2, when=when))
    return out


def _step_devs(step_dev: str, devs) -> tuple:
    """TARGET fans out; anything else (in practice ROOM, the only other legal
    value per _validate_script_dev) passes through as itself."""
    if step_dev == TARGET:
        return tuple(devs)
    return (step_dev,)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_trigger_expansion.py -v
```

Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add control/triggers.py tests/test_trigger_expansion.py
git commit -m "feat(triggers): expand a declared script into timed cues

expand_script substitutes the TARGET sentinel per resolved dev and stamps
each step with when = at + offset, producing LightCue/PlayCue objects the
existing GameServer._dispatch_cues already handles. A ROOM-addressed step is
left untouched for _resolve_dev downstream, so that method is not edited.

Pure and engine-free, so a trigger's exact cue sequence is assertable with no
Arco and no renderer."
```

---

### Task 3: `Bit.trigger_table`, validated at `load_bit`

Spec sections 5 and 6.

**Files:**
- Modify: `control/bit.py` (add the property after `role_table`)
- Modify: `control/engine.py:98-106` (`load_bit`'s `try` block)
- Test: `tests/test_engine_triggers.py` (new file, first tests)

**Interfaces:**
- Consumes: Task 1's `TriggerTable`, `validate_trigger_table`.
- Produces: `Bit.trigger_table -> TriggerTable` (a plain property, default empty);
  `load_bit` raising `BitLoadError` on an invalid table and leaving state `IDLE`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_engine_triggers.py`:

```python
"""Engine-side trigger behavior: load-time validation, firing, and the
on_trigger_fired observer hook.

Grouped in one file because they share the Bit fixtures below; split from
tests/test_engine.py so the existing lifecycle tests stay readable.
"""

import pytest

from control.bit import Bit
from control.cues import ROOM, TARGET, PlayCue
from control.engine import BitLoadError, GameServer
from control.roles import Role, RoleClass, RoleTable
from control.state import State
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerTable,
    TriggerTarget,
)


class _BaseBit(Bit):
    version = "0.1"

    @property
    def role_table(self) -> RoleTable:
        return RoleTable(
            roles={"player": Role(name="player", role_class=RoleClass.SHARED,
                                  capacity=None, scored=True)},
            node_map={"NODE": ["player"]})


class PlainBit(_BaseBit):
    """No trigger_table override at all: the default must keep working."""


class GoodTriggerBit(_BaseBit):
    def verb_handlers(self) -> dict:
        return {"tap": lambda dev, args, at: []}

    @property
    def trigger_table(self) -> TriggerTable:
        return TriggerTable(triggers={
            "flash": Trigger(
                name="flash", description="Flash the device",
                target=TriggerTarget.DEVICE,
                condition=Condition(name="tapped", description="Player taps",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="tap"),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),)),
        })


class UnimplementedVerbBit(_BaseBit):
    def verb_handlers(self) -> dict:
        return {"tap": lambda dev, args, at: []}

    @property
    def trigger_table(self) -> TriggerTable:
        return TriggerTable(triggers={
            "flash": Trigger(
                name="flash", description="Flash the device",
                target=TriggerTarget.DEVICE,
                condition=Condition(name="wiggled", description="Player wiggles",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="wiggle"),
                script=()),
        })


def _server(bit_cls, **kwargs):
    return GameServer({"bit": bit_cls}, **kwargs)


def test_a_bit_declaring_no_triggers_loads_exactly_as_before():
    gs = _server(PlainBit)
    gs.load_bit("bit")
    assert gs.state == State.SETUP
    assert gs.bit.trigger_table.triggers == {}


def test_a_valid_trigger_table_loads():
    gs = _server(GoodTriggerBit)
    gs.load_bit("bit")
    assert gs.state == State.SETUP
    assert "flash" in gs.bit.trigger_table.triggers


def test_a_trigger_naming_an_unimplemented_verb_fails_load():
    """Goal 4 and the spec's section 13 test 1: declared-but-unimplemented
    fails as a BitLoadError at load, and Control returns cleanly to IDLE."""
    gs = _server(UnimplementedVerbBit)
    with pytest.raises(BitLoadError, match="wiggle"):
        gs.load_bit("bit")
    assert gs.state == State.IDLE
    assert gs.bit is None
    assert gs.registration is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_engine_triggers.py -v
```

Expected: `test_a_bit_declaring_no_triggers_loads_exactly_as_before` fails with
`AttributeError: 'PlainBit' object has no attribute 'trigger_table'`, and
`test_a_trigger_naming_an_unimplemented_verb_fails_load` fails because no
`BitLoadError` is raised.

- [ ] **Step 3: Add the property to `control/bit.py`**

Add this import at the top, after `from control.rooms import RoomType`:

```python
from control.triggers import TriggerTable
```

Add this method immediately after the `role_table` abstract property:

```python
    @property
    def trigger_table(self) -> TriggerTable:
        """This Bit's declared triggers: the named things an operator can see
        coming, each with a description, a target, a condition this Bit
        evaluates itself, and a declarative cue script.

        A plain property with an empty default, deliberately not abstract the
        way role_table is, so every Bit written before triggers existed keeps
        working untouched. Validated at load_bit (control/triggers.py), so a
        trigger declared against a verb this Bit does not implement fails as a
        BitLoadError rather than mid-installation.
        """
        return TriggerTable(triggers={})
```

- [ ] **Step 4: Wire validation into `load_bit`**

In `control/engine.py`, add to the imports:

```python
from control.triggers import validate_trigger_table
```

In `load_bit`, inside the existing `try`, add one line after
`validate_role_declarations(role_table)`:

```python
            bit_cls = self.bit_registry[name]
            bit = bit_cls()
            role_table = bit.role_table
            validate_role_declarations(role_table)
            validate_trigger_table(bit.trigger_table, set(bit.verb_handlers()))
            registration = RegistrationState(role_table)
```

Inside the same `try` on purpose: its `except` already re-raises as
`BitLoadError` and resets the state to `IDLE`, which is exactly the contract a
bad trigger table needs.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_engine_triggers.py -v
```

Expected: PASS, 3 tests.

- [ ] **Step 6: Run the whole suite for regressions**

```bash
.venv/bin/python -m pytest tests -q
```

Expected: no failures, and at least `879 passed` (844 baseline plus 22
from Task 1, 10 from Task 2 and 3 here). The number that matters is that
nothing regressed from 844, not the exact total: a reviewer may have added
a case.

- [ ] **Step 7: Commit**

```bash
git add control/bit.py control/engine.py tests/test_engine_triggers.py
git commit -m "feat(bit): declare a TriggerTable, validated at load_bit

Bit.trigger_table is a plain property defaulting to empty, not abstract like
role_table, so every existing Bit is unchanged. Validation runs inside
load_bit's existing try, whose except already re-raises as BitLoadError and
resets to IDLE."
```

---

### Task 4: Firing, target resolution, and the `on_trigger_fired` hook

Spec sections 7.1 and 8. This is the task the whole slice turns on.

**Files:**
- Modify: `control/engine.py` (`_dispatch_cues`, plus new `fire_trigger` and `_resolve_target`)
- Test: `tests/test_engine_triggers.py` (append)

**Interfaces:**
- Consumes: Task 2's `expand_script`, Task 1's `TriggerFired`, `SOURCE_WIRE`,
  `FIRED_BY_*`, `TriggerTarget`; the existing `GameServer._dispatch_cues`,
  `_resolve_dev`, `_notify`, `on_light_cue`, `on_play_cue`.
- Produces: `GameServer.fire_trigger(name, *, fired_by, dev=None, at=None) -> str | None`;
  `GameServer._resolve_target(target, dev) -> list[str]`;
  `_dispatch_cues(cues, at, fired_by=None)`;
  the observer callback `on_trigger_fired(record: TriggerFired)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine_triggers.py`:

```python
class Recorder:
    """An observer that records every hook the engine offers it."""

    def __init__(self):
        self.fired = []

    def on_trigger_fired(self, record):
        self.fired.append(record)


class RaisingRecorder:
    def __init__(self):
        self.calls = 0

    def on_trigger_fired(self, record):
        self.calls += 1
        raise RuntimeError("observer exploded")


class ScriptBit(_BaseBit):
    """Both fire paths plus a three-step Room script."""

    def __init__(self):
        self.fire_next = None

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap}

    def _on_tap(self, dev, args, at):
        from control.cues import FireTrigger
        return [(dev, 0xB0, 74, 1), FireTrigger("flash", dev)]

    def cues(self, at):
        from control.cues import FireTrigger
        if self.fire_next is None:
            return []
        name, self.fire_next = self.fire_next, None
        return [FireTrigger(name)]

    @property
    def trigger_table(self) -> TriggerTable:
        return TriggerTable(triggers={
            "sweep": Trigger(
                name="sweep", description="Sweep the Room",
                target=TriggerTarget.ROOM,
                condition=Condition(name="round_won",
                                    description="User wins a round",
                                    source=ConditionSource.BIT_ADJUDICATED),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                        ScriptStep(0.5, (TARGET, 0xB0, 74, 40)),
                        ScriptStep(2.0, (TARGET, 0xB0, 74, 0)))),
            "flash": Trigger(
                name="flash", description="Flash the tapping device",
                target=TriggerTarget.DEVICE,
                condition=Condition(name="tapped", description="Player taps",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="tap"),
                script=(ScriptStep(0.0, PlayCue(TARGET, "click", "")),
                        ScriptStep(0.0, (TARGET, 0xB0, 74, 127)))),
            "everywhere": Trigger(
                name="everywhere", description="Light the whole room",
                target=TriggerTarget.ALL,
                condition=Condition(name="manual", description="Operator asks",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 64)),)),
        })


class _Room:
    def __init__(self, bound_dev):
        from control.rooms import RoomType
        self.room_type = RoomType.TEST
        self.bound_dev = bound_dev


def _running(bit_cls=ScriptBit, bound_dev="sim-room", clock=None):
    gs = GameServer({"bit": bit_cls}, clock=clock or (lambda: 100.0))
    gs.room = _Room(bound_dev)
    light, play = [], []
    gs.on_light_cue = lambda *a: light.append(a)
    gs.on_play_cue = lambda *a: play.append(a)
    gs.load_bit("bit")
    gs.join("ie1", "NODE")
    gs.run()
    return gs, light, play


def test_manual_fire_dispatches_every_step_with_its_offset():
    gs, light, _ = _running()
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert [c[0] for c in light] == ["sim-room"] * 3
    assert [c[4] for c in light] == [100.0, 100.5, 102.0]
    assert [c[3] for c in light] == [127, 40, 0]


def test_a_verb_handler_fire_shares_the_gestures_presentation_time():
    gs, light, play = _running()
    assert gs.data("ie1", "tap", ["ie1"]) is None
    # The handler's own cue, then the script's play and light steps, all at
    # the same `at` the engine computed once for this gesture.
    assert play == [("ie1", "click", "")]
    assert [c[0] for c in light] == ["ie1", "ie1"]
    assert {c[4] for c in light} == {100.0}


def test_a_cues_fire_is_recorded_as_bit_adjudicated():
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.bit.fire_next = "sweep"
    gs.tick(0.01)
    assert [r.fired_by for r in observer.fired] == ["bit-adjudicated"]


def test_fired_by_never_inherits_declared_source():
    """Spec section 13 test 2: an operator firing a gesture-verb trigger must
    stay distinguishable from a player actually doing it."""
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.fire_trigger("flash", fired_by="admin-manual", dev="ie1")
    record = observer.fired[0]
    assert record.fired_by == "admin-manual"
    assert record.declared_source == "gesture-verb"


def test_the_record_reports_what_the_fire_resolved_to():
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.fire_trigger("sweep", fired_by="admin-manual")
    record = observer.fired[0]
    assert record.name == "sweep"
    assert record.condition == "round_won"
    assert record.devs == ("sim-room",)
    assert record.steps == 3
    assert record.at == 100.0


def test_all_resolves_to_the_room_plus_registered_players_deduped():
    gs, light, _ = _running()
    gs.fire_trigger("everywhere", fired_by="admin-manual")
    assert [c[0] for c in light] == ["sim-room", "ie1"]


def test_all_never_lists_a_room_bound_device_twice():
    gs, light, _ = _running(bound_dev="ie1")
    gs.fire_trigger("everywhere", fired_by="admin-manual")
    assert [c[0] for c in light] == ["ie1"]


def test_a_device_target_with_no_device_is_refused_not_silently_empty():
    gs, light, _ = _running()
    reason = gs.fire_trigger("flash", fired_by="admin-manual")
    assert reason is not None
    assert "no device given" in reason
    assert light == []


def test_an_unknown_trigger_is_refused():
    gs, _, _ = _running()
    assert "unknown trigger" in gs.fire_trigger("nope", fired_by="admin-manual")


def test_firing_with_no_bit_running_is_refused():
    gs = GameServer({"bit": ScriptBit})
    assert gs.fire_trigger("sweep", fired_by="admin-manual") == "no Bit running"


def test_a_room_target_with_no_room_bound_fires_and_reaches_nothing():
    """A fire that reached nothing must be visible as such, not absent."""
    gs, light, _ = _running(bound_dev=None)
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert light == []
    assert observer.fired[0].devs == ()
    assert observer.fired[0].steps == 0


def test_a_raising_observer_does_not_stop_the_cues_or_its_peers():
    """Spec section 13 test 3, mirroring the on_release/on_light_cue guards."""
    gs, light, _ = _running()
    raiser, recorder = RaisingRecorder(), Recorder()
    gs.add_observer(raiser)
    gs.add_observer(recorder)
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert len(light) == 3
    assert raiser.calls == 1
    assert len(recorder.fired) == 1


def test_an_unknown_trigger_from_a_bit_does_not_break_neighbouring_cues():
    from control.cues import FireTrigger
    gs, light, _ = _running()
    gs._dispatch_cues([(ROOM, 0xB0, 74, 5), FireTrigger("nope"),
                       (ROOM, 0xB0, 74, 6)], 100.0)
    assert [c[3] for c in light] == [5, 6]


def test_a_bit_whose_trigger_table_raises_is_refused_not_crashed():
    class ExplodingBit(_BaseBit):
        @property
        def trigger_table(self):
            raise RuntimeError("boom")

    gs = GameServer({"bit": PlainBit})
    gs.load_bit("bit")
    gs.run()
    gs.bit = ExplodingBit()
    assert gs.fire_trigger("x", fired_by="admin-manual") == "trigger table error"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_engine_triggers.py -v
```

Expected: FAIL with `AttributeError: 'GameServer' object has no attribute 'fire_trigger'`.

- [ ] **Step 3: Add `fire_trigger` and `_resolve_target` to `control/engine.py`**

Extend the imports:

```python
from control.cues import ROOM, FireTrigger, LightCue, PlayCue
from control.triggers import (
    FIRED_BY_BIT_ADJUDICATED,
    SOURCE_WIRE,
    TriggerFired,
    TriggerTarget,
    expand_script,
    validate_trigger_table,
)
```

Add both methods immediately after `_resolve_dev`:

```python
    def _resolve_target(self, target, dev: str | None) -> list[str]:
        """A trigger's declared target, resolved to the devs it lands on.

        Returns a LIST even where at most one dev can come back today. The Room
        is one bound device now and becomes N o2lite clients in the N-fixture
        Room slice (spec section 4.2); returning a list from the start is what
        makes that a change to this method and to nothing a Bit declares.
        """
        if target is TriggerTarget.DEVICE:
            return [dev] if dev else []
        room_devs: list[str] = []
        if self.room is not None and self.room.bound_dev is not None:
            room_devs.append(self.room.bound_dev)
        if target is TriggerTarget.ROOM:
            return room_devs
        out = list(room_devs)
        assignments = (self.registration.assignments
                       if self.registration is not None else {})
        for player, (_node, _role, role_class) in assignments.items():
            if role_class != RoleClass.ROOM and player not in out:
                out.append(player)
        return out

    def fire_trigger(self, name: str, *, fired_by: str,
                     dev: str | None = None,
                     at: float | None = None) -> str | None:
        """Fire one declared trigger: expand its script, dispatch it, and tell
        every observer it happened.

        Returns None when fired, else a refusal reason, and NEVER raises, for
        the same reason data() does not: neither a device nor a browser may be
        able to wedge Control.

        `fired_by` is what actually fired it THIS time, which is deliberately
        not the same field as the condition's declared source: an operator may
        fire a gesture-verb trigger by hand, and the record has to keep those
        two distinguishable or a manual action reads as gameplay.

        `at` is supplied by _dispatch_cues when a Bit fired this from a verb
        handler or from cues(), so the whole script shares that gesture's
        single presentation time. A manual fire has no origin, so it takes
        Control's clock plus the installation's horizon, exactly as a
        self-driven cue does.
        """
        if self.state not in (State.SETUP, State.RUNNING):
            return "no Bit running"
        try:
            table = self.bit.trigger_table
        except Exception:
            logger.exception("Bit.trigger_table raised; refusing to fire %r",
                             name)
            return "trigger table error"
        trigger = table.triggers.get(name)
        if trigger is None:
            return f"unknown trigger {name!r}"
        if trigger.target is TriggerTarget.DEVICE and not dev:
            return (f"trigger {name!r} targets the firing device; "
                    f"no device given")
        if at is None:
            at = self._clock() + self._horizon
        devs = self._resolve_target(trigger.target, dev)
        cues = expand_script(trigger, at, devs)
        # No fired_by passed on: expansion never yields a FireTrigger (a script
        # step may only be a plain tuple or a PlayCue, enforced at load), so
        # this cannot recurse and a trigger cannot chain into another.
        self._dispatch_cues(cues, at)
        self._notify("on_trigger_fired", TriggerFired(
            name=trigger.name,
            condition=trigger.condition.name,
            fired_by=fired_by,
            declared_source=SOURCE_WIRE[trigger.condition.source],
            dev=dev,
            devs=tuple(devs),
            at=at,
            steps=len(cues),
        ))
        return None
```

Notification comes after dispatch on purpose, so `devs` and `steps` report what
actually went out rather than what was declared.

- [ ] **Step 4: Add the `FireTrigger` branch to `_dispatch_cues`**

Change the signature and add the first branch. The rest of the method is
unchanged:

```python
    def _dispatch_cues(self, cues, at: float | None,
                       fired_by: str | None = None) -> None:
        for cue in cues or ():
            try:
                if isinstance(cue, FireTrigger):
                    # A Bit reporting one of its own conditions satisfied.
                    # fire_trigger re-enters this method with the expanded
                    # script, carrying the same `at`, so a trigger fired from a
                    # gesture lands on the same frame as the ordinary cues
                    # returned beside it.
                    reason = self.fire_trigger(
                        cue.name,
                        fired_by=fired_by or FIRED_BY_BIT_ADJUDICATED,
                        dev=cue.dev, at=at)
                    if reason is not None:
                        logger.warning("Bit fired trigger %r: %s",
                                       cue.name, reason)
                    continue
                if isinstance(cue, PlayCue):
                    ...
```

- [ ] **Step 5: Pass `fired_by` from the two existing call sites**

In `data()`, change the dispatch call:

```python
        self._dispatch_cues(cues, at, FIRED_BY_GESTURE_VERB)
```

In `_dispatch_bit_cues()`:

```python
        self._dispatch_cues(cues, at, FIRED_BY_BIT_ADJUDICATED)
```

Add `FIRED_BY_GESTURE_VERB` to the `control.triggers` import.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_engine_triggers.py -v
```

Expected: PASS, 17 tests.

- [ ] **Step 7: Run the whole suite for regressions**

```bash
.venv/bin/python -m pytest tests -q
```

Expected: no failures, and at least `893 passed` (14 more than Task 3's
checkpoint).

- [ ] **Step 8: Commit**

```bash
git add control/engine.py tests/test_engine_triggers.py
git commit -m "feat(engine): fire a declared trigger, and observe that it fired

fire_trigger expands a script, dispatches it through the existing cue path,
and notifies observers with a TriggerFired record. A FireTrigger returned by a
verb handler or by cues() carries that path's single presentation time into
the whole script, so one gesture still means one T.

The record rides the multi-observer hook rather than a transport-owned sink:
it is produced in the engine, has no device destination, and is exactly the
event the uplink chain will want. _notify already guards every observer.

Target resolution returns a list even though the Room is one device today, so
the N-fixture Room slice changes this method and no Bit declaration."
```

---

### Task 5: A script cannot outlive its Bit

Spec section 7.3.

**Files:**
- Modify: `devicelink/agent.py:452-462` (`on_state_change`)
- Test: `tests/test_devicelink_agent.py` (append)

**Interfaces:**
- Consumes: the existing `DeviceLinkAgent._light_cues`, `_room_cues`, and its
  `on_state_change` observer callback.
- Produces: no new public surface. Behavior only.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_devicelink_agent.py`. Match the fixture helpers already in
that file for building an agent; the assertions below are what matter:

```python
def test_pending_script_cues_are_dropped_at_unloading():
    """A step scheduled past its Bit's completion must not still feed the Room
    after UNLOADING. Player devices are already safe by accident, because
    _feed_light_now returns early once _finish_release has cleared the bridge,
    but the Room's bridge persists across a Bit lifecycle by design, so the
    Room is the case that needs saying."""
    gs, agent, bridge = _agent_with_bound_room()   # existing helper, line 438
    now = agent._clock()
    # Far enough out that the room queue holds it AND the light-session feed
    # is deferred too (feed_at = when - horizon is still in the future).
    agent._on_light_cue("sim-room", 0xB0, 74, 40, when=now + 5.0)
    assert agent._room_cues.pending() == 1
    assert agent._light_cues.pending() == 1

    agent.on_state_change(State.RUNNING, State.UNLOADING)

    assert agent._room_cues.pending() == 0
    assert agent._light_cues.pending() == 0


def test_unloading_still_stops_the_room_drone():
    """The queue clear must not displace what this branch already did."""
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio)
    room_audio.on_grant("sim-room",
                        gs.bit.role_table.roles[room_role_name(RoomType.TEST)])
    agent.on_state_change(State.SETUP, State.RUNNING)
    agent.on_state_change(State.RUNNING, State.UNLOADING)
    voice = pool.acquired[0]
    assert voice.sent[-1][0] in ("note_off", "all_off")


def test_unloading_clears_queues_even_with_no_room_audio_injected():
    """The clear sits before the room-audio early return on purpose."""
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())
    agent._light_cues.push(agent._clock() + 5.0, ("ie1", 0xB0, 74, 1, 0.0),
                           now=agent._clock())
    agent.on_state_change(State.RUNNING, State.UNLOADING)
    assert agent._light_cues.pending() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_devicelink_agent.py -k "unloading" -v
```

Expected: `test_pending_script_cues_are_dropped_at_unloading` and
`test_unloading_clears_queues_even_with_no_room_audio_injected` FAIL with
`assert 1 == 0`. `test_unloading_still_stops_the_room_drone` passes already;
it is here to pin the behavior the edit must not displace.

The imports these need (`AudioBridge`, `FakePool`, `RoomBridge`, `RoomType`,
`room_role_name`, `_room_ready_game_server`, `_agent_with_bound_room`) are all
already present in `tests/test_devicelink_agent.py`.

- [ ] **Step 3: Rewrite `on_state_change`**

Replace the method at `devicelink/agent.py:452`:

```python
    def on_state_change(self, old_state: State, new_state: State) -> None:
        """FluidSynth is silent without a note (see control/audio.py), so
        the Room's declared drone has to start once the Bit is actually
        RUNNING and stop once it's UNLOADING -- mirrors harness/led_smoke.py's
        own start_drone/on_release-adjacent handling for a player role.

        UNLOADING also drops every still-pending timed cue. A trigger's cue
        script can schedule a step past its Bit's own completion, and the
        Room's bridge persists across a Bit lifecycle by design, so without
        this the Room keeps gliding after the drone has stopped and the Bit is
        gone. Player devices are already covered, because _feed_light_now
        returns early once _finish_release has cleared the bridge. Dropped
        rather than drained: these are cues for a Bit that no longer exists.
        """
        if new_state == State.UNLOADING:
            self._room_cues = TimedQueue()
            self._light_cues = TimedQueue()
        if self._room_audio is None or self._room_dev is None:
            return
        if new_state == State.RUNNING:
            self._room_audio.start_drone(self._room_dev)
        elif new_state == State.UNLOADING:
            self._room_audio.stop_drone(self._room_dev)
```

The clear is placed **before** the `_room_audio is None` early return on
purpose: an agent with no Room audio still has both queues and still must not
leak a Bit's cues past its unload.

Fresh `TimedQueue()` objects rather than mutating the existing ones, because
`TimedQueue` exposes no `clear()` and its `clamped`/`lateness` counters are
measurement state that a new Bit should start clean anyway.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_devicelink_agent.py -v
```

Expected: PASS, including the pre-existing tests in that file.

- [ ] **Step 5: Run the whole suite for regressions**

```bash
.venv/bin/python -m pytest tests -q
```

Expected: no failures, and at least `896 passed`.

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "fix(devicelink): drop pending timed cues at UNLOADING

A trigger's cue script can schedule a step past its Bit's completion, and the
Room's bridge persists across a Bit lifecycle, so the Room would keep gliding
after the drone stopped and the Bit was gone. Cleared in the branch that
already stops the drone, before its room-audio early return so an agent with
no Room audio is covered too."
```

---

### Task 6: The Console read model

Spec section 9.

**Files:**
- Create: `control/trigger_view.py`
- Test: `tests/test_trigger_view.py`

**Interfaces:**
- Consumes: Task 1's `Trigger`, `ScriptStep`, `TriggerTable`, `TriggerFired`,
  `SOURCE_WIRE`; `control.cues.PlayCue`.
- Produces: `control.trigger_view.triggers_view(trigger_table) -> list[dict]`,
  `trigger_view(trigger) -> dict`, `trigger_fired_view(record) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trigger_view.py`:

```python
"""The Console's trigger read model: pure dict builders, no engine, no socket.

Mirrors tests/test_room_view.py. The steps are serialized field by field
rather than as raw tuples so the browser renders them without re-deriving MIDI
semantics.
"""

import json

from control.cues import ROOM, TARGET, PlayCue
from control.trigger_view import (
    trigger_fired_view,
    trigger_view,
    triggers_view,
)
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerFired,
    TriggerTable,
    TriggerTarget,
)

SWEEP = Trigger(
    name="play_aurora", description="A slow aurora sweep across the Room",
    target=TriggerTarget.ROOM,
    condition=Condition(name="round_won", description="User wins a round",
                        source=ConditionSource.BIT_ADJUDICATED),
    script=(ScriptStep(0.0, (ROOM, 0xB0, 74, 127)),
            ScriptStep(2.0, (ROOM, 0xB0, 74, 0))))

FLASH = Trigger(
    name="flash_device", description="Flash the tapping device",
    target=TriggerTarget.DEVICE,
    condition=Condition(name="tapped", description="Player taps their Shroom",
                        source=ConditionSource.GESTURE_VERB, verb="tap"),
    script=(ScriptStep(0.0, PlayCue(TARGET, "click", "")),))


def test_a_trigger_serializes_its_declaration():
    view = trigger_view(SWEEP)
    assert view["name"] == "play_aurora"
    assert view["description"] == "A slow aurora sweep across the Room"
    assert view["target"] == "ROOM"
    assert view["condition"] == {
        "name": "round_won", "description": "User wins a round",
        "source": "bit-adjudicated", "verb": None}


def test_a_light_step_is_serialized_field_by_field():
    step = trigger_view(SWEEP)["script"][0]
    assert step == {"offset": 0.0, "kind": "light", "dev": ROOM,
                    "status": 176, "data1": 74, "data2": 127}


def test_a_play_step_carries_its_name_and_params():
    step = trigger_view(FLASH)["script"][0]
    assert step == {"offset": 0.0, "kind": "play", "dev": TARGET,
                    "name": "click", "params": ""}


def test_a_gesture_condition_reports_its_verb():
    assert trigger_view(FLASH)["condition"]["verb"] == "tap"


def test_triggers_view_preserves_declaration_order():
    table = TriggerTable(triggers={"play_aurora": SWEEP, "flash_device": FLASH})
    assert [t["name"] for t in triggers_view(table)] == ["play_aurora",
                                                          "flash_device"]


def test_triggers_view_of_none_is_empty():
    assert triggers_view(None) == []


def test_triggers_view_of_an_empty_table_is_empty():
    assert triggers_view(TriggerTable(triggers={})) == []


def test_the_whole_view_is_json_serializable():
    """It crosses a websocket, so an enum leaking through would fail there
    rather than here."""
    table = TriggerTable(triggers={"play_aurora": SWEEP, "flash_device": FLASH})
    json.dumps(triggers_view(table))


def test_a_fired_record_keeps_fired_by_and_declared_source_apart():
    record = TriggerFired(
        name="flash_device", condition="tapped", fired_by="admin-manual",
        declared_source="gesture-verb", dev="ie1", devs=("ie1",),
        at=100.0, steps=2)
    view = trigger_fired_view(record)
    assert view["fired_by"] == "admin-manual"
    assert view["declared_source"] == "gesture-verb"
    assert view["devs"] == ["ie1"]
    json.dumps(view)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_trigger_view.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'control.trigger_view'`.

- [ ] **Step 3: Create `control/trigger_view.py`**

```python
"""The trigger read model the Terrarium Console renders.

Pure dict builders with no engine imports, mirroring control/room_view.py so
this is testable with no GameServer, no renderer and no socket.

Script steps are serialized field by field rather than as raw cue tuples: the
browser then renders "cc:74 = 127" without re-deriving MIDI semantics, and
`kind` discriminates light from play the same way the Room panel's instrument
list discriminates light from audio.
"""

from __future__ import annotations

from control.cues import PlayCue
from control.triggers import SOURCE_WIRE


def _step_view(step) -> dict:
    cue = step.cue
    if isinstance(cue, PlayCue):
        return {"offset": float(step.offset), "kind": "play",
                "dev": cue.dev, "name": cue.name, "params": cue.params}
    dev, status, data1, data2 = cue
    return {"offset": float(step.offset), "kind": "light", "dev": dev,
            "status": status, "data1": data1, "data2": data2}


def trigger_view(trigger) -> dict:
    """One declared trigger, as the Console draws its card."""
    return {
        "name": trigger.name,
        "description": trigger.description,
        "target": trigger.target.name,
        "condition": {
            "name": trigger.condition.name,
            "description": trigger.condition.description,
            "source": SOURCE_WIRE[trigger.condition.source],
            "verb": trigger.condition.verb,
        },
        "script": [_step_view(step) for step in trigger.script],
    }


def triggers_view(trigger_table) -> list[dict]:
    """Every declared trigger, in declaration order. Empty when no Bit is
    loaded, which the panel renders as "No triggers declared"."""
    if trigger_table is None:
        return []
    return [trigger_view(t) for t in trigger_table.triggers.values()]


def trigger_fired_view(record) -> dict:
    """One fire.

    fired_by and declared_source are both carried, deliberately: the panel
    tags an admin-manual fire distinctly, and collapsing the two fields is
    what would let an operator action read as gameplay.
    """
    return {
        "name": record.name,
        "condition": record.condition,
        "fired_by": record.fired_by,
        "declared_source": record.declared_source,
        "dev": record.dev,
        "devs": list(record.devs),
        "at": record.at,
        "steps": record.steps,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_trigger_view.py -v
```

Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add control/trigger_view.py tests/test_trigger_view.py
git commit -m "feat(console): trigger read model

Pure dict builders mirroring control/room_view.py. Steps are serialized field
by field so the browser renders them without re-deriving MIDI semantics, and a
fired record keeps fired_by and declared_source as separate fields."
```

---

### Task 7: Wire protocol

Spec section 9.1.

**Files:**
- Modify: `console/protocol.py`
- Test: `tests/test_console_protocol.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `console.protocol.triggers_changed_event(triggers) -> dict`,
  `trigger_fired_event(fired) -> dict`,
  `FireTriggerCommand(name, dev=None)`,
  `parse_admin_command` accepting `fire_trigger`,
  `snapshot_event(..., triggers=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_console_protocol.py`:

```python
def test_snapshot_carries_a_triggers_key():
    event = protocol.snapshot_event(
        state="SETUP", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={},
        triggers=[{"name": "play_aurora"}])
    assert event["triggers"] == [{"name": "play_aurora"}]


def test_snapshot_defaults_triggers_to_an_empty_list():
    """An old caller that does not pass triggers must still produce a key the
    browser can read, rather than an absent one it has to guard."""
    event = protocol.snapshot_event(
        state="IDLE", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={})
    assert event["triggers"] == []


def test_triggers_changed_event_shape():
    assert protocol.triggers_changed_event([{"name": "x"}]) == {
        "event": "triggers_changed", "triggers": [{"name": "x"}]}


def test_trigger_fired_event_shape():
    fired = {"name": "x", "fired_by": "admin-manual"}
    assert protocol.trigger_fired_event(fired) == {
        "event": "trigger_fired", "fired": fired}


def test_parse_fire_trigger_with_a_device():
    command = protocol.parse_admin_command(
        {"command": "fire_trigger", "name": "flash_device", "dev": "ie1"})
    assert command == protocol.FireTriggerCommand(name="flash_device", dev="ie1")


def test_parse_fire_trigger_without_a_device():
    command = protocol.parse_admin_command(
        {"command": "fire_trigger", "name": "play_aurora"})
    assert command.name == "play_aurora"
    assert command.dev is None


def test_parse_fire_trigger_rejects_a_missing_name():
    with pytest.raises(ValueError, match="non-empty string 'name'"):
        protocol.parse_admin_command({"command": "fire_trigger"})


def test_parse_fire_trigger_rejects_a_non_string_dev():
    with pytest.raises(ValueError, match="'dev' must be a string"):
        protocol.parse_admin_command(
            {"command": "fire_trigger", "name": "x", "dev": 7})


def test_fire_trigger_is_not_a_command_the_uplink_can_send():
    """Firing a venue's trigger is a local trusted-operator action, exactly
    like arm_room. A remote fairyring peer must not be able to request it."""
    from uplink import protocol as uplink_protocol
    with pytest.raises(ValueError):
        uplink_protocol.parse_command(
            {"command": "fire_trigger", "name": "play_aurora"})
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_console_protocol.py -v
```

Expected: FAIL with `TypeError: snapshot_event() got an unexpected keyword
argument 'triggers'` and `AttributeError: module 'console.protocol' has no
attribute 'triggers_changed_event'`.

- [ ] **Step 3: Extend `console/protocol.py`**

Add to `__all__`:

```python
    "triggers_changed_event", "trigger_fired_event", "FireTriggerCommand",
```

Change `snapshot_event`:

```python
def snapshot_event(*, state, installed_bits, loaded_bit, roles,
                   registration, devices, bit_status, room=None,
                   triggers=None) -> dict:
    return {
        "event": "snapshot",
        "state": state,
        "installed_bits": installed_bits,
        "loaded_bit": loaded_bit,
        "roles": roles,
        "registration": registration,
        "devices": devices,
        "bit_status": bit_status,
        "room": room,
        "triggers": triggers or [],
    }
```

Add after `room_frame_event`:

```python
def triggers_changed_event(triggers) -> dict:
    """Every trigger the loaded Bit declares, as control.trigger_view's
    triggers_view() builds them. A trigger table is static per Bit, so in
    practice this fires on load and unload."""
    return {"event": "triggers_changed", "triggers": triggers}


def trigger_fired_event(fired) -> dict:
    """One fire, as control.trigger_view's trigger_fired_view() builds it."""
    return {"event": "trigger_fired", "fired": fired}
```

Add the command dataclass beside `ArmRoomCommand`:

```python
@dataclass
class FireTriggerCommand:
    name: str
    dev: str | None = None
```

Add the branch in `parse_admin_command`, before its final `raise`:

```python
    if command == "fire_trigger":
        name = msg.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("fire_trigger requires a non-empty string 'name'")
        dev = msg.get("dev")
        if dev is not None and not isinstance(dev, str):
            raise ValueError("fire_trigger 'dev' must be a string when given")
        return FireTriggerCommand(name=name, dev=dev or None)
```

Extend that function's docstring with one sentence, since it now covers a
second kind of local-only action:

```python
    """Console-only admin commands -- never sent by the uplink's remote
    broker. Kept separate from uplink.protocol.parse_command: Room
    registration is a local, trusted-operator action (design spec section
    7), not something a remote fairyring peer should ever request. Firing a
    declared trigger is the same kind of action for the same reason, so it
    lives here too rather than in the shared parser."""
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_console_protocol.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the whole suite for regressions**

```bash
.venv/bin/python -m pytest tests -q
```

Expected: no failures, and at least `914 passed` (9 from Task 6, which has
no full-suite checkpoint of its own, plus 9 here).

- [ ] **Step 6: Commit**

```bash
git add console/protocol.py tests/test_console_protocol.py
git commit -m "feat(console): triggers on the wire

snapshot gains a triggers key, plus triggers_changed/trigger_fired events and
a fire_trigger command. fire_trigger is parsed in parse_admin_command rather
than the shared uplink parser, same separation and same reason as arm_room.

Addition only: no existing message changes shape, so an old browser tab
against a new server degrades to today's behavior."
```

---

### Task 8: `ConsoleAgent`

Spec section 9.

**Files:**
- Modify: `console/agent.py`
- Test: `tests/test_console_agent.py` (append)

**Interfaces:**
- Consumes: Task 6's `triggers_view`/`trigger_fired_view`, Task 7's protocol
  builders and `FireTriggerCommand`, Task 4's `GameServer.fire_trigger`.
- Produces: `ConsoleAgent.on_trigger_fired(record)`, a `triggers` key on
  `snapshot()`, a `triggers_changed` broadcast from `poll()`, and `fire_trigger`
  command handling.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_console_agent.py`. The fixtures below are the ones that
file already defines: `_server_with_agent()` returns `(gs, srv, agent)` with no
Bit loaded, `_room_console()` returns the same with `TestBit` loaded and the
Room bound to `sim-room`, and `FakeConsoleServer` offers `.connect(client)`,
`.deliver(client, msg)`, `.sent` (a list of `(client, msg)`) and `.broadcasts`
(a list of `msg`).

```python
def test_snapshot_carries_the_loaded_bits_triggers():
    gs, srv, agent = _room_console()
    names = [t["name"] for t in agent.snapshot()["triggers"]]
    assert names == ["play_aurora", "flash_device"]


def test_snapshot_triggers_is_empty_with_no_bit_loaded():
    gs, srv, agent = _server_with_agent()
    assert agent.snapshot()["triggers"] == []


def test_the_room_stays_hidden_while_triggers_are_visible():
    """The Spec A section 3 regression, extended. Both halves in one test,
    because the safety argument is that they hold simultaneously: a trigger
    panel must not become the thing that leaks the Room's role."""
    gs, srv, agent = _room_console()
    snapshot = agent.snapshot()

    assert snapshot["triggers"]                      # the new surface is live
    room_name = room_role_name(RoomType.TEST)
    assert all(r["role"] != room_name for r in snapshot["roles"])
    assert all(r["role"] != room_name for r in snapshot["registration"])
    # The node id must not appear anywhere in the payload, including inside
    # the new triggers key.
    assert ROOM_NODE_IDS[RoomType.TEST] not in json.dumps(snapshot["triggers"])


def test_triggers_changed_broadcasts_on_change_only():
    gs, srv, agent = _room_console()
    agent.poll()
    srv.broadcasts.clear()
    agent.poll()
    assert not [b for b in srv.broadcasts
                if b.get("event") == "triggers_changed"]


def test_triggers_changed_broadcasts_when_a_bit_unloads():
    gs, srv, agent = _room_console()
    agent.poll()
    srv.broadcasts.clear()
    gs.abort()
    agent.poll()
    changed = [b for b in srv.broadcasts
               if b.get("event") == "triggers_changed"]
    assert changed and changed[-1]["triggers"] == []


def test_on_trigger_fired_broadcasts_the_record():
    gs, srv, agent = _room_console()
    agent.on_trigger_fired(TriggerFired(
        name="play_aurora", condition="round_won", fired_by="admin-manual",
        declared_source="bit-adjudicated", dev=None, devs=("sim-room",),
        at=1.0, steps=3))
    fired = [b for b in srv.broadcasts if b["event"] == "trigger_fired"]
    assert fired[0]["fired"]["fired_by"] == "admin-manual"
    assert fired[0]["fired"]["declared_source"] == "bit-adjudicated"
    assert fired[0]["fired"]["devs"] == ["sim-room"]


def test_a_fire_trigger_command_reaches_the_engine_as_admin_manual():
    gs, srv, agent = _room_console()
    calls = []
    gs.fire_trigger = lambda name, **kw: calls.append((name, kw))
    srv.connect("c1")
    srv.deliver("c1", {"command": "fire_trigger", "name": "play_aurora"})
    agent.poll()
    assert calls == [("play_aurora",
                      {"fired_by": "admin-manual", "dev": None})]


def test_a_fire_trigger_command_forwards_its_device():
    gs, srv, agent = _room_console()
    calls = []
    gs.fire_trigger = lambda name, **kw: calls.append((name, kw))
    srv.connect("c1")
    srv.deliver("c1", {"command": "fire_trigger", "name": "flash_device",
                       "dev": "ie1"})
    agent.poll()
    assert calls[0][1]["dev"] == "ie1"


def test_a_refused_fire_is_surfaced_as_an_error_event():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    srv.deliver("c1", {"command": "fire_trigger", "name": "nope"})
    agent.poll()
    errors = [msg for _client, msg in srv.sent
              if msg.get("event") == "error"]
    assert "unknown trigger" in errors[0]["message"]


def test_an_unparseable_fire_command_is_surfaced_not_dropped():
    """arm_room/release_room already surface a parse failure rather than
    logging and dropping it, because an operator pressing a button deserves to
    see why nothing happened."""
    gs, srv, agent = _room_console()
    srv.connect("c1")
    srv.deliver("c1", {"command": "fire_trigger"})
    agent.poll()
    errors = [msg for _client, msg in srv.sent
              if msg.get("event") == "error"]
    assert "name" in errors[0]["message"]
```

Add the imports these need at the top of the file, alongside the ones already
there:

```python
import json

from control.rooms import ROOM_NODE_IDS
from control.triggers import TriggerFired
```

`room_role_name` and `RoomType` are already imported by that file.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_console_agent.py -v
```

Expected: FAIL with `KeyError: 'triggers'` and `AttributeError: 'ConsoleAgent'
object has no attribute 'on_trigger_fired'`.

- [ ] **Step 3: Extend `console/agent.py`**

Add the imports:

```python
from control.trigger_view import trigger_fired_view, triggers_view
from control.triggers import FIRED_BY_ADMIN_MANUAL
```

Add `"fire_trigger"` to the admin-command names in `_handle_command`:

```python
        if name in ("arm_room", "release_room", "fire_trigger"):
            return self._handle_admin_command(msg)
```

Handle the command in `_handle_admin_command`, **before** the `room_type`
resolution, which is specific to the two Room commands:

```python
    def _handle_admin_command(self, msg: dict) -> dict | None:
        name = msg.get("command")
        try:
            command = protocol.parse_admin_command(msg)
        except ValueError as exc:
            return protocol.error_event(name, str(exc))
        if isinstance(command, protocol.FireTriggerCommand):
            # An operator action, tagged as one so the event log never reads it
            # as gameplay. GameServer.fire_trigger never raises, so a refusal
            # comes back as a reason string rather than an exception.
            reason = self.game_server.fire_trigger(
                command.name, fired_by=FIRED_BY_ADMIN_MANUAL, dev=command.dev)
            if reason is not None:
                return protocol.error_event(name, reason)
            return None
        try:
            room_type = RoomType[command.room_type]
        except KeyError:
            ...
```

Track and broadcast the table. Add to `__init__`:

```python
        self._last_triggers: list | None = None
```

Add the reader and the broadcaster:

```python
    def _current_triggers(self) -> list:
        bit = self.game_server.bit
        if bit is None:
            return []
        try:
            return triggers_view(bit.trigger_table)
        except Exception:
            logger.exception("Bit.trigger_table raised; reporting no triggers")
            return []

    def _broadcast_triggers_if_changed(self) -> None:
        triggers = self._current_triggers()
        if triggers != self._last_triggers:
            self._last_triggers = triggers
            self.server.broadcast(protocol.triggers_changed_event(triggers))
```

Call it from `poll()`, after the Room broadcasts:

```python
        self._broadcast_status_if_changed()
        self._broadcast_room_if_changed()
        self._broadcast_room_frame()
        self._broadcast_triggers_if_changed()
```

Seed the tracked value in `snapshot()` and pass it through, exactly as
`_last_room` already is:

```python
        self._last_room = self._current_room()
        self._last_triggers = self._current_triggers()
        return protocol.snapshot_event(
            state=gs.state.name,
            installed_bits=list(gs.bit_registry.keys()),
            loaded_bit=loaded_bit,
            roles=roles,
            registration=registration,
            devices=self._devices_view(),
            bit_status=self._current_status(),
            room=self._last_room,
            triggers=self._last_triggers,
        )
```

Add the observer callback beside the other three:

```python
    def on_trigger_fired(self, record) -> None:
        """Engine observer hook. A fire is engine-produced and has no device
        destination, which is why it rides the multi-observer list rather than
        a transport-owned sink -- see the design spec's section 8.1."""
        self.server.broadcast(
            protocol.trigger_fired_event(trigger_fired_view(record)))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_console_agent.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the whole suite for regressions**

```bash
.venv/bin/python -m pytest tests -q
```

Expected: no failures, and at least `924 passed`.

- [ ] **Step 6: Commit**

```bash
git add console/agent.py tests/test_console_agent.py
git commit -m "feat(console): serve triggers, and relay every fire

ConsoleAgent carries the loaded Bit's trigger table on snapshot, broadcasts it
on change, relays each TriggerFired through the new engine observer hook, and
routes a fire_trigger command to the engine tagged admin-manual.

The Spec A hiding regression is extended rather than replaced: the Room's role
name, node id and counts stay absent from the same snapshot that now carries
triggers, asserted in one test."
```

---

### Task 9: The trigger panel

Spec section 9.2. **This task carries the Spec A lesson: browser code gets a
behavioral test, not a substring grep.**

**Files:**
- Create: `console/static/triggers.js`
- Create: `tests/js/trigger_panel_behavior.test.js`
- Create: `tests/test_trigger_panel_behavior.py`
- Modify: `console/static/index.html`, `console/static/console.js`, `console/static/style.css`

**Interfaces:**
- Consumes: Task 7's `triggers_changed` / `trigger_fired` message shapes and the
  `fire_trigger` command shape.
- Produces: globals `renderTriggers(triggers)`, `renderTriggerFired(fired)`,
  `renderTriggerDevices(devices)`; a `#triggers` container in `index.html`.

**Two constraints that shape the code:**

1. **`triggers.js` must add no top-level `document.getElementById` for a new
   element.** `console.js` already does that for `loadBtn`/`runBtn`/`abortBtn`,
   and the existing `tests/js/room_panel_behavior.test.js` builds a document
   containing exactly those three. A new top-level lookup would break that file.
   Every trigger button is therefore created inside `renderTriggers` with its
   `onclick` assigned at creation.
2. **`trigger_fired` must not rebuild the card list.** It is the high-frequency
   event here exactly as `room_changed` was in Spec A, where rebuilding on every
   event made the live light effectively never display.

- [ ] **Step 1: Write the failing behavioral test**

Create `tests/js/trigger_panel_behavior.test.js`:

```javascript
"use strict";
// Behavioral test for console/static/triggers.js and console/static/console.js's
// dispatch of the two trigger events.
//
// Spec A's two Important defects reached a live browser past 843 passing tests
// because room.js was covered only by substring greps over its own source. The
// worst of them was renderRoom rebuilding #roomStrip on every room_changed, so
// the painted swatches were destroyed roughly four times per painted frame.
// trigger_fired is this panel's equivalent high-frequency event, so the
// scenario that matters most below is that a fire does NOT rebuild the card
// list.
//
// Drives the real shipped triggers.js against a small hand-rolled DOM stub
// under Node. No jsdom: this repo has no build step and nothing shipped may
// depend on npm.
//
// Run directly: node tests/js/trigger_panel_behavior.test.js
// Wired into pytest via tests/test_trigger_panel_behavior.py.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

// ---- DOM stub -----------------------------------------------------------
// Same shape as tests/js/room_panel_behavior.test.js's, plus `value` (the
// device picker is a <select>) and `onclick` (a plain property on these
// objects, so a scenario can invoke a button by calling node.onclick()).

function makeNode(tag) {
  const node = {
    tagName: tag,
    id: "",
    className: "",
    textContent: "",
    value: "",
    onclick: null,
    style: {},
    parentNode: null,
    _children: [],
  };
  Object.defineProperty(node, "children", {
    get() { return node._children; },
  });
  Object.defineProperty(node, "innerHTML", {
    get() { return node._children.length ? "(non-empty)" : ""; },
    set(value) {
      if (value !== "") {
        throw new Error('DOM stub only supports innerHTML = ""');
      }
      for (const child of node._children) child.parentNode = null;
      node._children = [];
    },
  });
  node.appendChild = (child) => {
    child.parentNode = node;
    node._children.push(child);
    return child;
  };
  node.remove = () => {
    if (node.parentNode) {
      const siblings = node.parentNode._children;
      const at = siblings.indexOf(node);
      if (at >= 0) siblings.splice(at, 1);
      node.parentNode = null;
    }
  };
  return node;
}

function findById(node, id) {
  if (node.id === id) return node;
  for (const child of node._children) {
    const found = findById(child, id);
    if (found) return found;
  }
  return null;
}

function findAll(node, predicate, out) {
  out = out || [];
  if (predicate(node)) out.push(node);
  for (const child of node._children) findAll(child, predicate, out);
  return out;
}

function newDocument() {
  const root = makeNode("body");
  const div = makeNode("div");
  div.id = "triggers";
  root.appendChild(div);
  return {
    _root: root,
    createElement: (tag) => makeNode(tag),
    createTextNode: (text) => ({ nodeType: 3, textContent: text }),
    getElementById: (id) => findById(root, id),
  };
}

// ---- fixtures -----------------------------------------------------------

const SWEEP = {
  name: "play_aurora",
  description: "A slow aurora sweep across the Room",
  target: "ROOM",
  condition: { name: "round_won", description: "User wins a round",
               source: "bit-adjudicated", verb: null },
  script: [
    { offset: 0.0, kind: "light", dev: "@room", status: 176, data1: 74, data2: 127 },
    { offset: 0.5, kind: "light", dev: "@room", status: 176, data1: 74, data2: 40 },
    { offset: 2.0, kind: "light", dev: "@room", status: 176, data1: 74, data2: 0 },
  ],
};

const FLASH = {
  name: "flash_device",
  description: "Flash the tapping device",
  target: "DEVICE",
  condition: { name: "tapped", description: "Player taps their Shroom",
               source: "gesture-verb", verb: "tap" },
  script: [
    { offset: 0.0, kind: "play", dev: "@target", name: "click", params: "" },
    { offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 74, data2: 127 },
  ],
};

// ---- harness ------------------------------------------------------------

let failures = 0;
function assert(cond, message) {
  if (!cond) {
    failures++;
    console.error(`FAIL: ${message}`);
  }
}

const triggersJsPath = path.join(__dirname, "..", "..", "console", "static",
                                 "triggers.js");
const triggersJsSource = fs.readFileSync(triggersJsPath, "utf8");

// Fresh document AND fresh triggers.js module state per scenario, by
// re-evaluating the shipped source in its own vm context. `send` is a spy:
// in the browser it is console.js's global, resolved at click time.
function scenario(name, testBody) {
  const sent = [];
  const sandbox = {
    document: newDocument(),
    console, assert, sent, SWEEP, FLASH, findAll,
    send: (command, extra) => sent.push([command, extra]),
  };
  vm.createContext(sandbox);
  try {
    vm.runInContext(triggersJsSource + "\n" + testBody, sandbox,
                    { filename: `triggers.js+${name}` });
  } catch (err) {
    failures++;
    console.error(`FAIL: scenario "${name}" threw: ${err.stack || err}`);
  }
}

scenario("renders one card per trigger with every script step visible", `
  renderTriggers([SWEEP, FLASH]);
  const cards = findAll(document.getElementById("triggerCards"),
                        (n) => n.className.indexOf("card") >= 0);
  assert(cards.length === 2, "expected 2 cards, got " + cards.length);

  const steps = findAll(document.getElementById("triggers"),
                        (n) => n.className === "step");
  assert(steps.length === 5,
    "expected 5 rendered steps (3 + 2), got " + steps.length);

  const text = steps.map((s) => s.textContent).join("|");
  assert(text.indexOf("+0.00s") >= 0, "offsets should render, got " + text);
  assert(text.indexOf("cc:74 = 127") >= 0,
    "a light step should render as cc:<n> = <v>, got " + text);
  assert(text.indexOf('play "click"') >= 0,
    "a play step should render its sample name, got " + text);
`);

scenario("the card list survives a trigger_fired re-render", `
  renderTriggers([SWEEP, FLASH]);
  const list = document.getElementById("triggerCards");
  const childrenBefore = list.children.length;

  renderTriggerFired({ name: "play_aurora", condition: "round_won",
    fired_by: "admin-manual", declared_source: "bit-adjudicated",
    dev: null, devs: ["sim-room"], at: 1.0, steps: 3 });

  assert(document.getElementById("triggerCards") === list,
    "trigger_fired must not replace the card list node");
  assert(list.children.length === childrenBefore,
    "trigger_fired must not rebuild the cards: had " + childrenBefore +
    ", now " + list.children.length);
`);

scenario("a fire updates only its own card's status line", `
  renderTriggers([SWEEP, FLASH]);
  renderTriggerFired({ name: "play_aurora", condition: "round_won",
    fired_by: "admin-manual", declared_source: "bit-adjudicated",
    dev: null, devs: ["sim-room"], at: 1.0, steps: 3 });

  const fired = document.getElementById("triggerFired_play_aurora");
  const other = document.getElementById("triggerFired_flash_device");
  assert(fired.textContent.indexOf("ADMIN MANUAL") >= 0,
    "an admin-manual fire must be tagged, got " + fired.textContent);
  assert(other.textContent.indexOf("never fired") >= 0,
    "the other card must be untouched, got " + other.textContent);
`);

scenario("a gesture-verb fire is not tagged as admin manual", `
  renderTriggers([FLASH]);
  renderTriggerFired({ name: "flash_device", condition: "tapped",
    fired_by: "gesture-verb", declared_source: "gesture-verb",
    dev: "ie1", devs: ["ie1"], at: 1.0, steps: 2 });

  const line = document.getElementById("triggerFired_flash_device");
  assert(line.textContent.indexOf("ADMIN MANUAL") === -1,
    "only an admin-manual fire carries the tag, got " + line.textContent);
  assert(line.textContent.indexOf("gesture-verb") >= 0,
    "the fire source should still be shown, got " + line.textContent);
`);

scenario("the Fire button sends fire_trigger with no dev for a ROOM target", `
  renderTriggers([SWEEP]);
  const button = findAll(document.getElementById("triggers"),
                         (n) => n.tagName === "button")[0];
  button.onclick();
  assert(sent.length === 1, "expected one send, got " + sent.length);
  assert(sent[0][0] === "fire_trigger", "wrong command: " + sent[0][0]);
  assert(sent[0][1].name === "play_aurora", "wrong name: " + sent[0][1].name);
  assert(!("dev" in sent[0][1]),
    "a ROOM-target fire must not carry a dev, got " + JSON.stringify(sent[0][1]));
`);

scenario("a DEVICE target renders a picker and sends the selected dev", `
  renderTriggerDevices([{ dev: "ie1" }, { dev: "ie2" }]);
  renderTriggers([FLASH]);
  const picker = document.getElementById("triggerDev_flash_device");
  assert(picker, "a DEVICE-target card must render a device picker");
  assert(picker.children.length === 2,
    "picker should list both devices, got " + picker.children.length);
  picker.value = "ie2";

  const button = findAll(document.getElementById("triggers"),
                         (n) => n.tagName === "button")[0];
  button.onclick();
  assert(sent[0][1].dev === "ie2", "wrong dev sent: " + sent[0][1].dev);
`);

scenario("no triggers renders the empty state and clears prior cards", `
  renderTriggers([SWEEP]);
  renderTriggers([]);
  assert(document.getElementById("triggerCards") === null,
    "the card list must be torn down when no Bit declares triggers");
  const el = document.getElementById("triggers");
  assert(el.children.length === 1 &&
         el.children[0].textContent === "No triggers declared",
    "expected the empty state, got " + el.children.length + " children");
`);

scenario("an unchanged table does not rebuild the cards", `
  renderTriggers([SWEEP, FLASH]);
  const list = document.getElementById("triggerCards");
  const first = list.children[0];
  renderTriggers([SWEEP, FLASH]);
  assert(document.getElementById("triggerCards") === list,
    "an unchanged table must not replace the card list node");
  assert(list.children[0] === first,
    "an unchanged table must not rebuild individual cards");
`);

scenario("a fire arriving before any render does not throw", `
  renderTriggerFired({ name: "play_aurora", fired_by: "admin-manual",
    declared_source: "bit-adjudicated", devs: [], at: 0, steps: 0 });
  renderTriggers([SWEEP]);
  const line = document.getElementById("triggerFired_play_aurora");
  assert(line.textContent.indexOf("ADMIN MANUAL") >= 0,
    "a fire seen before the cards existed should show once they do, got "
    + line.textContent);
`);

// ---- console.js dispatch -------------------------------------------------

class FakeWebSocket {
  constructor(url) { this.url = url; }
}

function newConsoleDocument() {
  const root = makeNode("body");
  for (const id of ["loadBtn", "runBtn", "abortBtn"]) {
    const node = makeNode("button");
    node.id = id;
    root.appendChild(node);
  }
  return {
    createElement: (tag) => makeNode(tag),
    createTextNode: (text) => ({ nodeType: 3, textContent: text }),
    getElementById: (id) => findById(root, id),
  };
}

const consoleJsPath = path.join(__dirname, "..", "..", "console", "static",
                                "console.js");
const consoleJsSource = fs.readFileSync(consoleJsPath, "utf8");

function consoleScenario(name, testBody) {
  const calls = [];
  const sandbox = {
    document: newConsoleDocument(),
    WebSocket: FakeWebSocket,
    location: { host: "test.invalid" },
    console, assert, calls, SWEEP,
    renderRoom: () => {},
    renderRoomFrame: () => {},
    renderTriggers: (t) => calls.push(["renderTriggers", t]),
    renderTriggerFired: (f) => calls.push(["renderTriggerFired", f]),
    renderTriggerDevices: (d) => calls.push(["renderTriggerDevices", d]),
  };
  vm.createContext(sandbox);
  try {
    vm.runInContext(consoleJsSource + "\n" + testBody, sandbox,
                    { filename: `console.js+${name}` });
  } catch (err) {
    failures++;
    console.error(`FAIL: scenario "${name}" threw: ${err.stack || err}`);
  }
}

consoleScenario("triggers_changed dispatches to renderTriggers", `
  const triggers = [SWEEP];
  handle({ event: "triggers_changed", triggers });
  assert(calls.length === 1, "expected one call, got " + calls.length);
  assert(calls[0][0] === "renderTriggers", "wrong target: " + calls[0][0]);
  assert(calls[0][1] === triggers, "wrong payload");
`);

consoleScenario("trigger_fired dispatches to renderTriggerFired", `
  const fired = { name: "play_aurora", fired_by: "admin-manual" };
  handle({ event: "trigger_fired", fired });
  assert(calls.length === 1, "expected one call, got " + calls.length);
  assert(calls[0][0] === "renderTriggerFired", "wrong target: " + calls[0][0]);
  assert(calls[0][1] === fired, "wrong payload");
`);

consoleScenario("devices_changed also feeds the trigger device pickers", `
  const devices = [{ dev: "ie1", name: "shroom", role: "player" }];
  handle({ event: "devices_changed", devices });
  const names = calls.map((c) => c[0]);
  assert(names.indexOf("renderTriggerDevices") >= 0,
    "the picker source must follow the device list, got " + names.join(","));
`);

// ---- report --------------------------------------------------------------

if (failures > 0) {
  console.error(`${failures} assertion(s) failed`);
  process.exit(1);
} else {
  console.log("OK: triggers.js behavioral checks passed");
  process.exit(0);
}
```

- [ ] **Step 2: Create the pytest wrapper**

Create `tests/test_trigger_panel_behavior.py`:

```python
"""Behavioral test for console/static/triggers.js and console.js's dispatch.

A grep over browser source is not a test of behavior: that is exactly how two
Important defects reached a live browser run during the Room-panel slice past
843 passing tests. This drives the real shipped triggers.js against a DOM stub
under Node, and its load-bearing scenario is that a trigger_fired event does
NOT rebuild the card list, which is the same defect class as the Room strip's.

No build step: node is used here only as a test runner for a plain script,
never as a shipped dependency. Skips cleanly if node is not available rather
than failing the whole suite on a box without it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_SCRIPT = ROOT / "tests" / "js" / "trigger_panel_behavior.test.js"


def _find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    fallback = Path("/opt/homebrew/bin/node")
    return str(fallback) if fallback.exists() else None


NODE = _find_node()


@pytest.mark.skipif(NODE is None, reason="node not found on this box")
def test_trigger_panel_behavior():
    result = subprocess.run(
        [NODE, str(TEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        "trigger_panel_behavior.test.js failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_trigger_panel_behavior.py -v
```

Expected: FAIL, `ENOENT: no such file or directory ... console/static/triggers.js`.

- [ ] **Step 4: Create `console/static/triggers.js`**

```javascript
// Trigger panel: one card per Bit-declared trigger, showing what makes it
// happen and the actual steps it runs, plus a Fire button for the operator.
//
// The card list is rebuilt ONLY when the declared table changes. trigger_fired
// arrives per fire and updates one status line in place. This is the same
// discipline room.js's #roomStrip needed after Defect 1, where rebuilding on
// every event destroyed what the panel was there to show; a trigger table is
// static per Bit, so a rebuild on every fire would be pure waste and would
// discard the picker selections an operator had made.

let triggerSignature = null;      // JSON of the last rendered declaration
const lastFired = {};             // trigger name -> its last fire record
let triggerDevices = [];          // device ids offered by DEVICE-target pickers

function renderTriggerDevices(devices) {
  // Kept as ids only: the picker needs nothing else, and the device list
  // re-renders far more often than the trigger table does.
  triggerDevices = (devices || []).map((d) => d.dev);
  for (const trigger of currentDeviceTargets) {
    fillDevicePicker(document.getElementById("triggerDev_" + trigger));
  }
}

let currentDeviceTargets = [];

function fillDevicePicker(picker) {
  if (!picker) return;
  const previous = picker.value;
  picker.innerHTML = "";
  for (const dev of triggerDevices) {
    const option = document.createElement("option");
    option.value = dev;
    option.textContent = dev;
    picker.appendChild(option);
  }
  if (triggerDevices.indexOf(previous) >= 0) picker.value = previous;
  else if (triggerDevices.length) picker.value = triggerDevices[0];
}

function stepText(step) {
  const offset = "+" + Number(step.offset).toFixed(2) + "s";
  if (step.kind === "play") {
    return `${offset}   ${step.dev}   play "${step.name}"`;
  }
  return `${offset}   ${step.dev}   cc:${step.data1} = ${step.data2}`;
}

function firedText(fired) {
  if (!fired) return "never fired";
  const where = fired.devs && fired.devs.length
    ? fired.devs.join(", ") : "nothing";
  const tag = fired.fired_by === "admin-manual" ? "   ADMIN MANUAL" : "";
  return `last fired by ${fired.fired_by} -> ${where}`
    + ` (${fired.steps} cue${fired.steps === 1 ? "" : "s"})${tag}`;
}

function applyFired(line, fired) {
  line.textContent = firedText(fired);
  line.className = fired && fired.fired_by === "admin-manual"
    ? "fired manual" : "fired";
}

function buildCard(trigger) {
  const card = document.createElement("div");
  card.className = "card trigger";

  const title = document.createElement("h3");
  title.textContent = trigger.name;
  card.appendChild(title);

  const target = document.createElement("span");
  target.className = "kind";
  target.textContent = trigger.target;
  card.appendChild(target);

  const description = document.createElement("p");
  description.textContent = trigger.description;
  card.appendChild(description);

  const condition = document.createElement("p");
  condition.className = "muted";
  condition.textContent = trigger.condition.description
    + "   (" + trigger.condition.source
    + (trigger.condition.verb ? ": " + trigger.condition.verb : "") + ")";
  card.appendChild(condition);

  for (const step of trigger.script) {
    const line = document.createElement("div");
    line.className = "step";
    line.textContent = stepText(step);
    card.appendChild(line);
  }

  if (trigger.target === "DEVICE") {
    const picker = document.createElement("select");
    picker.id = "triggerDev_" + trigger.name;
    card.appendChild(picker);
    fillDevicePicker(picker);
  }

  const button = document.createElement("button");
  button.textContent = "Fire";
  // Assigned here rather than looked up at top level: console.js already owns
  // the only top-level element lookups, and adding one for an element that
  // does not exist until a Bit is loaded would break on an empty console.
  button.onclick = () => {
    const picker = document.getElementById("triggerDev_" + trigger.name);
    const extra = { name: trigger.name };
    if (picker) extra.dev = picker.value;
    send("fire_trigger", extra);
  };
  card.appendChild(button);

  const fired = document.createElement("div");
  fired.id = "triggerFired_" + trigger.name;
  applyFired(fired, lastFired[trigger.name]);
  card.appendChild(fired);

  return card;
}

function renderTriggers(triggers) {
  const el = document.getElementById("triggers");
  const list = triggers || [];
  const signature = JSON.stringify(list);
  if (signature === triggerSignature) return;
  triggerSignature = signature;

  el.innerHTML = "";
  if (!list.length) {
    currentDeviceTargets = [];
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No triggers declared";
    el.appendChild(empty);
    return;
  }

  const cards = document.createElement("div");
  cards.id = "triggerCards";
  cards.className = "cards";
  el.appendChild(cards);
  currentDeviceTargets = [];
  for (const trigger of list) {
    cards.appendChild(buildCard(trigger));
    if (trigger.target === "DEVICE") currentDeviceTargets.push(trigger.name);
  }
}

function renderTriggerFired(fired) {
  if (!fired || !fired.name) return;
  // Recorded even when no card exists yet, so a fire that arrives before the
  // snapshot has rendered still shows once the cards are built.
  lastFired[fired.name] = fired;
  const line = document.getElementById("triggerFired_" + fired.name);
  if (line) applyFired(line, fired);
}
```

- [ ] **Step 5: Add the section to `console/static/index.html`**

Insert after the Room section and before Registration:

```html
<h2>Triggers</h2>
<div id="triggers"></div>
```

And add the script tag before `console.js`, since `console.js` calls
`renderTriggers` from `handle()`:

```html
<script src="room.js"></script>
<script src="triggers.js"></script>
<script src="console.js"></script>
```

`console/server.py` needs no change: its asset map globs `console/static/` at
construction and allowlists by extension.

- [ ] **Step 6: Add the dispatch to `console/static/console.js`**

In `handle()`'s `snapshot` case, after `renderRoom(msg.room);`:

```javascript
      renderTriggerDevices(msg.devices);
      renderTriggers(msg.triggers);
```

In `renderDevices`, feed the pickers too, so a device joining mid-run appears in
them:

```javascript
function renderDevices(devs) {
  rows("#devices", devs, (d) => [d.dev, d.name, d.role ?? "\u2014"]);
  renderTriggerDevices(devs);
}
```

The em-dash literal in that line is the shipped file's existing content, quoted
here so the edit is a one-line addition. Leave it as it is in the source; do not
"fix" it, and do not introduce a new one anywhere else.

Add two cases beside the Room ones:

```javascript
    case "triggers_changed": renderTriggers(msg.triggers); break;
    case "trigger_fired": renderTriggerFired(msg.fired); break;
```

- [ ] **Step 7: Add the styles to `console/static/style.css`**

Append. `.card`, `.cards`, `.kind` and `.muted` already exist and are reused:

```css
/* Trigger panel */
.card.trigger { min-width: 20rem; }
.card.trigger p { margin: .2rem 0; }
.step { font-family: ui-monospace, monospace; font-size: .8rem;
        color: #333; white-space: pre; }
.fired { font-size: .8rem; color: #666; margin-top: .4rem; }
.fired.manual { color: #a60; font-weight: 600; }
.card.trigger select { margin: .4rem .5rem .2rem 0; }
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_trigger_panel_behavior.py tests/test_room_panel_behavior.py -v
```

Expected: PASS. The Room panel test must still pass: `triggers.js` adds no
top-level element lookup, so the document that file builds is still sufficient.

- [ ] **Step 9: Run the whole suite for regressions**

```bash
.venv/bin/python -m pytest tests -q
```

Expected: no failures, and at least `925 passed`. If
`tests/test_console_static.py` greps
`index.html` for an exact script-tag list, update that expectation there.

- [ ] **Step 10: Commit**

```bash
git add console/static/triggers.js console/static/index.html \
        console/static/console.js console/static/style.css \
        tests/js/trigger_panel_behavior.test.js \
        tests/test_trigger_panel_behavior.py
git commit -m "feat(console): the trigger panel

One card per declared trigger showing its condition and its actual script
steps, a Fire button, a device picker for DEVICE targets, and a last-fired
line that tags an admin-manual fire distinctly.

Behavioral tests, not greps: the load-bearing scenario is that trigger_fired
updates one status line and does not rebuild the card list, which is the same
defect class that reached a live browser during the Room-panel slice."
```

---

### Task 10: TestBit declares two triggers

Spec section 10.

**Files:**
- Modify: `bits/test_bit.py`
- Test: `tests/test_test_bit.py` (append; create if the repo has no such file,
  matching whichever file already covers `TestBit`)

**Interfaces:**
- Consumes: everything from Tasks 1 to 4.
- Produces: `TestBit.trigger_table` declaring `play_aurora` and `flash_device`;
  `TestBit.ROUND_TILTS`, `TestBit.SCRIPT_QUIET_SECONDS`.

- [ ] **Step 1: Write the failing tests**

```python
def test_test_bit_declares_both_triggers():
    bit = TestBit()
    names = sorted(bit.trigger_table.triggers)
    assert names == ["flash_device", "play_aurora"]


def test_test_bits_trigger_table_validates_against_its_own_verbs():
    """The gesture-verb condition names `tap`, which TestBit implements. This
    is the fixture behind the declared-but-unimplemented check."""
    bit = TestBit()
    validate_trigger_table(bit.trigger_table, set(bit.verb_handlers()))


def test_a_tap_fires_flash_device_for_the_tapping_device():
    bit = TestBit()
    cues = bit.verb_handlers()["tap"]("ie1", ["ie1", 2.0, 30, 1], 100.0)
    fires = [c for c in cues if isinstance(c, FireTrigger)]
    assert [(f.name, f.dev) for f in fires] == [("flash_device", "ie1")]


def test_full_deflection_tilts_win_a_round_and_fire_play_aurora():
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    for _ in range(TestBit.ROUND_TILTS):
        bit.verb_handlers()["tilt"]("ie1", ["ie1", 90.0], 100.0)
    bit.update(0.01)
    fires = [c for c in bit.cues(100.0) if isinstance(c, FireTrigger)]
    assert [f.name for f in fires] == ["play_aurora"]


def test_a_partial_tilt_does_not_count_toward_the_round():
    """The Bit adjudicates: Control must never fire just because the verb
    arrived."""
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    for _ in range(TestBit.ROUND_TILTS):
        bit.verb_handlers()["tilt"]("ie1", ["ie1", 10.0], 100.0)
    bit.update(0.01)
    assert not [c for c in bit.cues(100.0) if isinstance(c, FireTrigger)]


def test_the_round_fires_once_not_every_tick():
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    for _ in range(TestBit.ROUND_TILTS):
        bit.verb_handlers()["tilt"]("ie1", ["ie1", 90.0], 100.0)
    bit.update(0.01)
    first = [c for c in bit.cues(100.0) if isinstance(c, FireTrigger)]
    bit.update(0.01)
    second = [c for c in bit.cues(100.0) if isinstance(c, FireTrigger)]
    assert len(first) == 1 and second == []


def test_the_ambient_drift_yields_the_lane_while_the_script_plays():
    """The drift and play_aurora's script both drive the Room's cc:74. Without
    this the 44 Hz drift would overwrite every script step within one tick and
    the declared sweep would never be visible."""
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    for _ in range(TestBit.ROUND_TILTS):
        bit.verb_handlers()["tilt"]("ie1", ["ie1", 90.0], 100.0)
    bit.update(0.01)
    bit.cues(100.0)                       # the fire tick
    bit.update(0.5)
    assert bit.cues(100.5) == []          # still inside the script
    bit.update(TestBit.SCRIPT_QUIET_SECONDS)
    assert bit.cues(103.0)                # drift resumes afterwards


def test_the_ambient_drift_is_unchanged_when_no_round_has_been_won():
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    bit.update(0.0)
    assert bit.cues(100.0) == [(ROOM, 0xB0, 74, 0)]
```

Imports these need:

```python
from control.cues import ROOM, FireTrigger
from control.triggers import validate_trigger_table
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_test_bit.py -v
```

Expected: FAIL, `assert [] == ['flash_device', 'play_aurora']`.

- [ ] **Step 3: Extend `bits/test_bit.py`**

Extend the imports:

```python
from control.cues import ROOM, TARGET, FireTrigger, PlayCue
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerTable,
    TriggerTarget,
)
```

Add the class constants beside `ROOM_DRIFT_PERIOD`:

```python
    # Full-deflection tilts that win a round. A fixture's adjudication, not a
    # game: it exists to be deterministic and assertable at an exact tick.
    ROUND_TILTS = 3

    # How long this Bit stops driving the Room's cc:74 after firing
    # play_aurora. The drift below runs at 44 Hz and shares that lane with the
    # script, so without yielding it the very next tick would overwrite step 0
    # and the declared sweep would never be visible. A Bit yielding a lane it
    # shares with its own script is the general shape here, not a TestBit quirk.
    SCRIPT_QUIET_SECONDS = 2.0
```

Add to `__init__`:

```python
        self._full_tilts = 0
        self._round_won = False
        self._rounds_won = 0
        self._quiet_until = 0.0
```

Reset them in `on_run_start`, beside the existing `self._elapsed = 0.0`:

```python
    def on_run_start(self) -> None:
        self._run_started = True
        self._elapsed = 0.0
        self._full_tilts = 0
        self._round_won = False
        self._quiet_until = 0.0
```

Add the declaration:

```python
    @property
    def trigger_table(self) -> TriggerTable:
        """Two triggers, deliberately: one per fire path.

        play_aurora is bit-adjudicated, so nothing outside this Bit decides
        when a round is won. flash_device is reached through the `tap` verb
        this Bit already implements, and Control does NOT fire it just because
        a tap arrived: _on_tap returns the FireTrigger itself, which is what
        keeps condition evaluation inside the Bit.
        """
        return TriggerTable(triggers={
            "play_aurora": Trigger(
                name="play_aurora",
                description="A slow aurora sweep across the Room",
                target=TriggerTarget.ROOM,
                condition=Condition(
                    name="round_won",
                    description="User wins a round",
                    source=ConditionSource.BIT_ADJUDICATED),
                script=(
                    ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                    ScriptStep(0.5, (TARGET, 0xB0, 74, 40)),
                    ScriptStep(2.0, (TARGET, 0xB0, 74, 0)),
                ),
            ),
            "flash_device": Trigger(
                name="flash_device",
                description="Flash the tapping device and click its speaker",
                target=TriggerTarget.DEVICE,
                condition=Condition(
                    name="tapped",
                    description="Player taps their Shroom",
                    source=ConditionSource.GESTURE_VERB,
                    verb="tap"),
                script=(
                    ScriptStep(0.0, PlayCue(TARGET, "click", "")),
                    ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                ),
            ),
        })
```

Replace `cues`:

```python
    def cues(self, at: float) -> list:
        """Self-driven Room animation, plus this Bit's own adjudication report.

        verb_handlers() can only ever react to a device, so without the drift
        the Room's aurora reached its declared static hue once and held it,
        unanimated, for a whole run. Deterministic in self._elapsed, which
        update(dt) already accumulates, so a test can assert the exact value at
        a given elapsed time.

        Triangle rather than sawtooth: a sawtooth snaps from 127 back to 0 once
        per period, and aurora GLIDES to its target, so the snap reads as a
        visible lurch rather than a wrap.

        A won round is reported here rather than from update(dt) because a fire
        is returned in the cue vocabulary, and this is the hook that carries it
        with a presentation time already computed. It latches, so a round fires
        exactly once however many ticks pass before it is drained.
        """
        if self._round_won:
            self._round_won = False
            self._rounds_won += 1
            self._quiet_until = self._elapsed + self.SCRIPT_QUIET_SECONDS
            return [FireTrigger("play_aurora")]
        if self._elapsed < self._quiet_until:
            # play_aurora owns cc:74 until its script finishes. See
            # SCRIPT_QUIET_SECONDS.
            return []
        phase = (self._elapsed % self.ROOM_DRIFT_PERIOD) / self.ROOM_DRIFT_PERIOD
        cc = int(round(254 * (phase if phase < 0.5 else 1.0 - phase)))
        return [(ROOM, 0xB0, 74, cc)]
```

Extend `_on_tilt` to adjudicate, keeping everything it already returns:

```python
    def _on_tilt(self, dev: str, args: list, at: float) -> list:
        """args: [dev, gamma]. gamma is degrees in [-90, 90].

        Two cues, one `at`. The calling device's own hue lane, and the Room's.
        The Room role declares cc:74 on BOTH its light_manifest (aurora hue)
        and its ugen_manifest (FluidSynth cutoff), so one tilt moves the Room's
        colour and the Room's drone timbre against a single shared time.
        Neither cue names a time: control/engine.py stamps both with `at`,
        which is what makes "one gesture, one T" hold without a Bit having to
        remember to say so.

        Full deflection also counts toward the round. Counted here rather than
        reported here: a round is not a light consequence of this gesture, and
        cues() is where this Bit reports one.
        """
        gamma = float(args[1]) if len(args) > 1 else 0.0
        gamma = max(-90.0, min(90.0, gamma))
        cc = int(round((gamma + 90.0) / 180.0 * 127.0))
        if cc >= 127:
            self._full_tilts += 1
            if self._full_tilts >= self.ROUND_TILTS:
                self._full_tilts = 0
                self._round_won = True
        return [(dev, 0xB0, 74, cc), (ROOM, 0xB0, 74, cc)]
```

Extend `_on_tap` to fire, keeping what it already returns:

```python
    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """args: [dev, peak_g, duration_ms, count]. A single tap clicks, a
        double chimes; both flash the hue lane so the tap is visible as well
        as audible, and both fire this Bit's declared flash_device trigger for
        the tapping device."""
        count = int(args[3]) if len(args) > 3 else 1
        name = "chime" if count >= 2 else "click"
        return [PlayCue(dev, name, ""), (dev, 0xB0, 74, 127),
                FireTrigger("flash_device", dev)]
```

Extend `status()` so the Console shows the adjudication:

```python
    def status(self) -> dict:
        return {"elapsed": round(self._elapsed, 2),
                "run_duration": self._run_duration,
                "full_tilts": self._full_tilts,
                "rounds_won": self._rounds_won}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_test_bit.py -v
```

Expected: PASS, 8 tests.

- [ ] **Step 5: Run the whole suite for regressions**

```bash
.venv/bin/python -m pytest tests -q
```

Expected: no failures, and at least `933 passed`. Two existing expectations
may need updating,
and both are correct updates rather than regressions: any test asserting
`TestBit.status()`'s exact key set (it gains `full_tilts` and `rounds_won`), and
any asserting `_on_tap`'s exact return list (it gains a trailing
`FireTrigger("flash_device", dev)`). Update them to the new values; do not weaken the assertions to `in`.

- [ ] **Step 6: Commit**

```bash
git add bits/test_bit.py tests/test_test_bit.py
git commit -m "feat(bits): TestBit declares play_aurora and flash_device

One trigger per fire path. play_aurora is bit-adjudicated on three full-
deflection tilts, latched so it fires once, and reported from cues() where a
presentation time is already computed. flash_device rides the existing tap
handler, which returns the FireTrigger itself: Control must not fire just
because the verb arrived.

cues() yields cc:74 for the script's duration, because the 44 Hz drift shares
that lane and would otherwise overwrite step 0 within one tick."
```

---

### Task 11: Documentation sync and live verification

Spec sections 13.1 and 15.

**Files:**
- Modify: `docs/MM_TERRARIUM.md`
- Modify: `README.md` (only if it enumerates console panels or `console/static/` files)

**Interfaces:**
- Consumes: everything.
- Produces: no code.

- [ ] **Step 1: Run the full suite one final time**

```bash
.venv/bin/python -m pytest tests -v
```

Expected: no failures, at least `933 passed`, and no test skipped for a
reason other than
the pre-existing one plus a genuinely absent node.

- [ ] **Step 2: Confirm the offline invariants still hold**

```bash
.venv/bin/python -c "import control.triggers, control.trigger_view; print('ok')"
grep -rn "^import \|^from " control/triggers.py control/trigger_view.py
```

Expected: `ok`, and no luxaeterna, pyarco or o2litepy in either import list.

- [ ] **Step 3: Live-verify with no device joined**

**RUN ON: MYCOLOGICAL** (or whichever box has the Arco checkout at
`/Users/chris/projects/arco`)

```bash
.venv/bin/python -m harness.run_stack --console-port 8772 --devices 0 --seconds 180
```

Open the printed Console URL and confirm, in this order:

1. The Triggers panel lists **two** cards, `play_aurora` and `flash_device`,
   each showing its description, its condition, and its script steps with
   offsets.
2. `flash_device` shows a device picker; `play_aurora` does not.
3. Pressing Fire on `play_aurora` sweeps the Room's three zones through
   127 -> 40 -> 0 over two seconds, **with no device joined**. This is the
   acceptance criterion that matters: headless device clock sync is measured at
   1 of 3, so a verification requiring a join is a verification that works one
   time in three.
4. Its status line reads `ADMIN MANUAL`, styled distinctly.
5. Pressing Fire on `flash_device` with no device in the picker is refused, and
   the refusal appears in the event log rather than silently doing nothing.
6. The Roles and Registration tables still list only `player` and `jammer`, with
   no `room_test` row anywhere.

Record what was and was not observed in the spec's Status line, in the same
shape Spec A's uses. **If something was not verified, say so rather than
implying it was.**

- [ ] **Step 4: Sync the deep-dive**

Use the `mm-deepdive-sync` skill. The edits it needs to make:

1. **A new subsystem section** after the Room-panel one, covering
   `control/triggers.py`, `control/trigger_view.py`, `console/static/triggers.js`,
   the `on_trigger_fired` hook and the `fire_trigger` command. State plainly
   that firing reuses `_on_light_cue`'s existing far-future branch and adds no
   scheduler.
2. **Close the deferred entry.** `docs/MM_TERRARIUM.md:1396` currently reads
   "**Bit-declared triggers, cue scripts and conditions (Spec B).** ... Spec not
   yet written." Strike it through and mark it closed, in the same shape as the
   other closed entries.
3. **Add a new deferred entry for Spec C**, the N-fixture Room: `Room.bound_dev`
   is singular, `RoleClass.ROOM` is capacity 1, `RoomProfile` is single-surface,
   and `DeviceLinkAgent` holds one `_room_dev`. Record the reason it matters (a
   real venue Room is N light fixtures, each its own o2lite client with its own
   unique service name) and the correction that lights are **not** wrapped in
   Arco ugens: Lux Aeterna and Arco are siblings, Arco relays the light blob as
   the room's hub without rendering it, and an audio instrument is a channel on
   a shared `Flsyn` with no dev id. Note that `GameServer._resolve_target`
   already returns a list, so Spec C changes that method and no Bit declaration.
4. **Add the spec and this plan** to the *Design docs* list at the end.
5. **Update the suite baseline** from 844 to the measured final number.

- [ ] **Step 5: Commit the docs**

```bash
git add docs/MM_TERRARIUM.md README.md
git commit -m "docs(terrarium): sync the deep-dive after the trigger slice

Closes the Spec B deferred entry, adds the trigger subsystem, and opens a new
deferred entry for Spec C (the N-fixture Room) carrying the correction that
lights are o2lite clients rather than Arco ugens."
```

- [ ] **Step 6: Close the branch**

Use the `superpowers:finishing-a-development-branch` skill.

---

## Task dependency order

Tasks 1 to 4 are strictly sequential. After Task 4:

- **5** (transport queue clear) is independent of 6 to 9.
- **6 -> 7 -> 8** is sequential (read model, then wire, then agent).
- **9** depends on 7's message shapes, not on 8's implementation.
- **10** depends on 1 to 4 only.
- **11** depends on everything.

---

## Self-review

Run against the spec after writing, per the writing-plans skill.

**Spec coverage.** Section 4.1 target vocabulary -> Task 4 (`_resolve_target`).
Section 4.2 Spec C deferral -> Task 11 step 4. Section 5 declaration -> Task 1.
Section 5.1 no auto-fire -> Task 10 (`_on_tap` returns the fire) and asserted by
`test_a_partial_tilt_does_not_count_toward_the_round`. Section 6 validation ->
Task 1, one test per row of the spec's table. Section 7.1 firing -> Task 4.
Section 7.2 expansion -> Task 2. Section 7.3 outliving -> Task 5. Section 8
record and hook -> Task 4. Section 9 Console -> Tasks 6, 7, 8, 9. Section 10
TestBit -> Task 10. Section 12 error handling -> Task 4's refusal tests plus
Task 8's error-event tests. Section 13 four load-bearing tests -> Task 3 (test
1), Task 4 (tests 2 and 3), Task 5 (test 4). Section 13.1 -> Task 11 step 3.
Section 15's eleven criteria all map to a task. **No gaps found.**

**Placeholder scan.** No TBD, no "add appropriate error handling", no "similar
to Task N". Every code step carries real code. Task 5's and Task 8's test code was
corrected against the real modules after a first draft invented helper names:
Task 5 uses `_agent_with_bound_room()` / `_room_ready_game_server()` and Task 8
uses `_server_with_agent()` / `_room_console()`, all of which exist today.

**Type consistency.** `TriggerTarget`/`ConditionSource` members are referenced by
the same names in Tasks 1, 2, 4, 6 and 10. `fire_trigger`'s signature is
identical in Tasks 4 and 8. `triggers_view`/`trigger_fired_view` are spelled the
same in Tasks 6 and 8. `renderTriggers`/`renderTriggerFired`/
`renderTriggerDevices` are spelled the same in Task 9's test, its
implementation, and the `console.js` edit. `SOURCE_WIRE` values
(`"gesture-verb"`, `"bit-adjudicated"`, `"admin-manual"`) match the
`FIRED_BY_*` constants and the browser's `"admin-manual"` comparison.
