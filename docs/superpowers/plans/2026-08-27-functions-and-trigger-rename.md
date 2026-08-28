# Functions and the Trigger Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the acting-side Trigger vocabulary to Function (scripted /
generator / stream kinds), replace `Bit.cues(at)` and hardcoded verb-handler
cc math with declared generator/stream Functions, and reassign the name
Trigger to the sensing side (event triggers with server-owned thresholds
shipped in the role blob; stream triggers as server-side transforms).

**Architecture:** One atomic rename pass first (identifiers, files, wire
events, console JS, tests), then behavior lands in slices on the renamed
vocabulary: a `kind` discriminator on `Function`, an engine-owned
`GeneratorRunner`, engine-applied stream mappings in `GameServer.data()`,
and a fresh `control/triggers.py` housing the sensing entities. All work is
offline-tested; nothing touches the wire framing.

**Tech Stack:** Python 3 stdlib (control/ discipline: no luxaeterna, no
pyarco imports in control modules), pytest, Node `vm` DOM-stub tests for
console JS.

**Spec:** `docs/superpowers/specs/2026-08-27-functions-and-trigger-rename-design.md`

## Global Constraints

- Full suite green at every task boundary: `.venv/bin/python -m pytest tests -q`
  (baseline 1529 passed, 1 skipped). Fresh worktree: `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`.
- FULL rename, no dual vocabulary: no acting-side `Trigger` identifier may
  survive anywhere in `control/ console/ devicelink/ bits/ harness/ tests/`.
- Out of rename scope: luxaeterna lane dests (`"dest": "trigger"`),
  `capture/` vocabulary, incidental English verb uses.
- Never-raises conventions preserved: `fire_function` and `data()` return
  reason strings, never raise, exactly as today.
- Engine modules stay pure stdlib; `DeviceLinkAgent` never imports pyarco.
- Cue-kind vocabulary `CUE_KINDS = ("midi", "play", "solid", "mute")` and
  the `fired_by` wire strings (`"gesture-verb"`, `"bit-adjudicated"`,
  `"admin-manual"`) are unchanged.
- Commit per task with conventional-commit messages.

---

### Task 1: The atomic rename (acting side: Trigger -> Function)

Pure mechanical rename, no behavior change. The suite count must end
exactly where it started (1529/1) modulo renamed test files.

**Files:**
- Rename: `control/triggers.py` -> `control/functions.py`
- Rename: `control/trigger_view.py` -> `control/function_view.py`
- Rename: `console/static/triggers.js` -> `console/static/functions.js`
- Rename: `tests/test_triggers.py` -> `tests/test_functions.py`,
  `tests/test_engine_triggers.py` -> `tests/test_engine_functions.py`,
  `tests/test_trigger_view.py` -> `tests/test_function_view.py`,
  `tests/test_trigger_expansion.py` -> `tests/test_function_expansion.py`
- Modify: `control/cues.py`, `control/engine.py`, `control/bit.py`,
  `control/instrument.py`, `control/terrarium_config.py`,
  `control/terrarium.py`, `control/room_view.py`, `console/agent.py`,
  `console/protocol.py`, `console/server.py` (asset map),
  `console/static/index.html` + `console.js` (script tag, panel ids),
  `bits/test/test_bit.py`, `bits/metronome/metronome_bit.py`,
  `terrarium.toml`, every test that imports the old names
  (`grep -rl` finds them), `tests/instrument_fixtures.py`
- Create: `tests/test_vocabulary.py`

**Interfaces (produced, relied on by every later task):**
- `control/functions.py`: `Function`, `FunctionTable`, `FunctionTarget`,
  `Condition`, `ConditionSource`, `ScriptStep`, `FunctionFired`,
  `SOURCE_WIRE`, `FIRED_BY_*` (values unchanged),
  `validate_function_table(function_table, verb_names)`,
  `expand_script(function, at, devs)`
- `control/cues.py`: `FireFunction(name, dev=None)` (renamed `FireTrigger`)
- `control/engine.py`: `GameServer.fire_function(name, *, fired_by, dev=None,
  at=None) -> str | None`; observer event `on_function_fired`;
  `Bit.function_table` property
- `control/instrument.py`: `Instrument.accepted_cues` (renamed
  `accepted_triggers`; same kind strings)
- `console/protocol.py`: `functions_changed_event(functions)`,
  `function_fired_event(fired)`, `FireFunctionCommand`, command name
  `"fire_function"`, snapshot key `"functions"`
- `control/function_view.py`: `function_view`, `functions_view`,
  `function_fired_view`

- [ ] **Step 1: Write the vocabulary pin test (failing)**

```python
# tests/test_vocabulary.py
"""Pins the Spec 3 rename: the acting-side Trigger vocabulary is gone.

A grep-shaped test, deliberately: the rename is total (spec section 2),
and this is what stops a future edit reintroducing the old names."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["control", "console", "devicelink", "bits", "harness", "tests"]
# Old acting-side identifiers and wire words. \b guards keep incidental
# English ("triggered the notification") and the NEW sensing entities
# (EventTrigger/StreamTrigger, control/triggers.py) out of scope.
FORBIDDEN = [
    r"\bFireTrigger\b", r"\bTriggerTable\b", r"\bTriggerTarget\b",
    r"\bTriggerFired\b", r"\bfire_trigger\b", r"\btrigger_table\b",
    r"\bvalidate_trigger_table\b", r"\btrigger_view\b", r"\btriggers_view\b",
    r"\btrigger_fired\b", r"\btriggers_changed\b", r"\baccepted_triggers\b",
    r"\bon_trigger_fired\b",
]

def test_acting_side_trigger_vocabulary_is_gone():
    pattern = re.compile("|".join(FORBIDDEN))
    hits = []
    for d in SCAN_DIRS:
        for path in (ROOT / d).rglob("*"):
            if path.suffix not in {".py", ".js", ".html", ".toml", ".css"}:
                continue
            if path.name == "test_vocabulary.py":
                continue
            for i, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "old acting-side vocabulary survives:\n" + "\n".join(hits)
```

- [ ] **Step 2: Run it to verify it fails** (hundreds of hits):
`.venv/bin/python -m pytest tests/test_vocabulary.py -q` -> FAIL

- [ ] **Step 3: Execute the rename**

`git mv` the four renamed source files and four renamed test files. Then a
repo-wide identifier sweep (sed or editor) applying exactly this mapping,
in `control/ console/ devicelink/ bits/ harness/ tests/ terrarium.toml`:

```
Trigger->Function  TriggerTable->FunctionTable  TriggerTarget->FunctionTarget
TriggerFired->FunctionFired  FireTrigger->FireFunction
fire_trigger->fire_function  trigger_table->function_table
validate_trigger_table->validate_function_table
control.triggers->control.functions  control.trigger_view->control.function_view
trigger_view->function_view  triggers_view->functions_view
trigger_fired_view->function_fired_view
on_trigger_fired->on_function_fired
triggers_changed->functions_changed  trigger_fired->function_fired
FireTriggerCommand->FireFunctionCommand
accepted_triggers->accepted_cues
_last_triggers->_last_functions  _current_triggers->_current_functions
triggers_changed_event->functions_changed_event
trigger_fired_event->function_fired_event
```

Then the non-mechanical residue, by hand:
- `control/functions.py` docstring: "Bit-declared functions"; local
  variable names (`trigger` -> `function_decl` where `function` would
  shadow nothing but reads badly as a keyword-ish name; use `fn` in
  comprehensions); `TriggerTable.triggers` dict field ->
  `FunctionTable.functions`; error-message wording ("must be a Function").
- `console/protocol.py` snapshot key `"triggers"` -> `"functions"`; event
  payload keys `"triggers"` -> `"functions"`.
- `console/static/functions.js`: DOM ids/classes (`#triggers`,
  `trigger-card`, headings "Triggers") -> function spellings;
  `index.html`'s panel markup and script tag; `console/server.py`'s asset
  allowlist entry; `tests/test_console_static.py` and
  `tests/js/triggers_and_rail.test.js` (rename to
  `functions_and_rail.test.js`, update `tests/test_room_panel_behavior.py`
  -style wrapper references — check `tests/js/` wiring with grep).
- `control/instrument.py`: field `accepted_cues`; `validate_instrument`
  message "unknown accepted cue kind(s)"; `TUNESHROOM` declaration.
- `control/engine.py` `_check_cue_kinds` refusal strings: "does not
  accept ... cues" already cue-worded; update the docstring reference.
- `terrarium.toml` + `tests/` fixture TOMLs: `accepted_triggers = [...]`
  keys -> `accepted_cues = [...]`.
- `control/terrarium_config.py` `_parse_instrument`: read `accepted_cues`;
  a table still carrying `accepted_triggers` raises
  `TerrariumConfigError(f"{source}: instrument {iname!r}: "
  f"'accepted_triggers' was renamed to 'accepted_cues' (Spec 3); "
  f"update the key")` — located, fails hard, tells the author the fix.
- Engine-fired record wording in `fire_function`: refusal strings
  "unknown function", "function table error", "function script error".

- [ ] **Step 4: Run the full suite + the pin**

`.venv/bin/python -m pytest tests -q` -> same pass count as baseline
(renamed files included), 1 skipped; `tests/test_vocabulary.py` passes.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "refactor: rename acting-side Trigger vocabulary to Function (Spec 3 section 2)"
```

---

### Task 2: `FunctionKind` and per-kind validation

**Files:**
- Modify: `control/functions.py`
- Test: `tests/test_functions.py`

**Interfaces:**
- Consumes: Task 1's `Function`/`FunctionTable`/`validate_function_table`.
- Produces:
  - `class FunctionKind(Enum): SCRIPTED; GENERATOR; STREAM`
  - `@dataclass(frozen=True) GeneratorSpec: dev: str; status: int;
    data1: int; waveform: str; period: float; lo: int = 0; hi: int = 127`
  - `@dataclass(frozen=True) StreamOutput: dev: str; status: int;
    data1: int; out_lo: float; out_hi: float; mode: str = "linear"`
  - `@dataclass(frozen=True) StreamSpec: verb: str; arg: int; in_lo: float;
    in_hi: float; outputs: tuple[StreamOutput, ...]`
  - `Function` gains `kind: FunctionKind = FunctionKind.SCRIPTED`,
    `generator: GeneratorSpec | None = None`,
    `stream: StreamSpec | None = None`; `target`/`condition` become
    `| None = None` defaults (scripted validation still requires them).
  - `WAVEFORMS: frozenset[str] = frozenset({"triangle"})`
  - `STREAM_MODES: frozenset[str] = frozenset({"linear", "abs"})`
  - `generator_lane(fn) -> tuple[str, int, int]` (dev, status, data1)
- `validate_function_table` dispatches on kind and enforces:
  scripted requires target+condition, refuses generator/stream fields;
  generator requires `generator`, refuses target/condition/script, checks
  waveform in `WAVEFORMS`, period > 0 finite, 0 <= lo <= hi <= 255,
  status/data1 in 0-255, dev in `(cues.ROOM, cues.TARGET)`; stream
  requires `stream`, checks mode in `STREAM_MODES`, `in_lo < in_hi`
  finite, arg >= 0, outputs non-empty with dev/status/data1 legal and
  out_lo/out_hi finite; **one generator per lane** (two GENERATOR
  functions with equal `generator_lane` -> ValueError naming both); **two
  streams may not write the same output lane over overlapping input
  domains** (touching at a single shared endpoint is legal; the
  lower-domain function applies at the shared point — document in the
  StreamSpec docstring).

- [ ] **Step 1: Write failing tests** — in `tests/test_functions.py` add:

```python
def _gen(name="drift", dev=ROOM, data1=74, period=12.0, lo=0, hi=254):
    return Function(name=name, description="d", kind=FunctionKind.GENERATOR,
                    generator=GeneratorSpec(dev=dev, status=0xB0, data1=data1,
                                            waveform="triangle",
                                            period=period, lo=lo, hi=hi))

def _stream(name="tilt_hue", verb="tilt", in_lo=-90.0, in_hi=90.0,
            outputs=None):
    outputs = outputs or (StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),)
    return Function(name=name, description="d", kind=FunctionKind.STREAM,
                    stream=StreamSpec(verb=verb, arg=1, in_lo=in_lo,
                                      in_hi=in_hi, outputs=outputs))

def test_generator_function_validates():
    validate_function_table(FunctionTable(functions={"drift": _gen()}), set())

def test_scripted_function_refuses_generator_field(): ...
    # a SCRIPTED Function carrying generator=GeneratorSpec(...) -> ValueError
def test_generator_refuses_script_and_unknown_waveform(): ...
def test_two_generators_same_lane_refused():
    table = FunctionTable(functions={
        "a": _gen("a"), "b": _gen("b")})
    with pytest.raises(ValueError, match="lane"):
        validate_function_table(table, set())
def test_streams_same_lane_overlapping_domains_refused(): ...
    # two streams, same output (TARGET, 0xB0, 74), domains [-90,10] and
    # [0,90] -> ValueError; domains [-90,0] and [0,90] -> OK
def test_stream_bad_mode_and_inverted_domain_refused(): ...
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_functions.py -q` -> FAIL (names undefined)

- [ ] **Step 3: Implement** the dataclasses, vocabularies, and the
per-kind branches inside `validate_function_table` (new private helpers
`_validate_generator(fn)`, `_validate_stream(fn)`, plus the table-level
lane-uniqueness and domain-overlap sweeps after the per-entry loop).
Overlap rule: for each output lane, collect `(in_lo, in_hi)` intervals
across stream functions writing it; sort; adjacent pairs may share at most
the single boundary point (`prev.in_hi <= next.in_lo`).

- [ ] **Step 4: Run the full suite** -> green (existing scripted tests
untouched by the kind default).

- [ ] **Step 5: Commit** `git commit -m "feat(functions): FunctionKind with generator/stream specs and per-kind validation"`

---

### Task 3: `GeneratorRunner` and the engine tick

**Files:**
- Create: `control/generator_runner.py`
- Modify: `control/engine.py` (`__init__`, `load_bit`, `tick`,
  `fire_function`, `_unload`; delete `_dispatch_bit_cues`)
- Modify: `control/bit.py` (delete `cues(at)`; add `fires(at)`)
- Test: `tests/test_generator_runner.py`, `tests/test_engine_functions.py`

**Interfaces:**
- Consumes: Task 2's `FunctionKind`, `GeneratorSpec`, `generator_lane`.
- Produces:
  - `control/generator_runner.py`:
    ```python
    class GeneratorRunner:
        def __init__(self, functions: Sequence[Function]): ...
        def cues(self, elapsed: float, at: float) -> list[tuple]:
            """One (dev, status, data1, value) per non-suppressed lane."""
        def suppress(self, lanes: Iterable[tuple[str, int, int]],
                     until_at: float) -> None: ...
        @staticmethod
        def value(spec: GeneratorSpec, elapsed: float) -> int:
            """triangle: phase=(elapsed % period)/period;
            frac = 2*phase if phase < 0.5 else 2*(1-phase);
            int(round(lo + frac * (hi - lo)))."""
    ```
    Pure stdlib, deterministic in `elapsed`, no clock of its own.
    Suppression is per-lane `until_at` on the **at** timeline (absolute O2
    time); `cues()` skips a lane while `at < until_at`; phase keeps
    advancing regardless (overlay never kills, spec section 4).
  - `Bit.fires(self, at: float) -> list` — new optional hook, default
    `[]`; documented: may return only `FireFunction`s, anything else is
    logged and dropped by the engine.
  - `Bit.cues` is **deleted** from the ABC (grep confirms no caller).
  - Engine: `self._generators: GeneratorRunner | None`, built in
    `load_bit` from the validated table's GENERATOR functions; `tick()`
    calls `_dispatch_generator_cues()` then `_dispatch_bit_fires()`
    (replacing `_dispatch_bit_cues`); `fire_function` after a successful
    expansion calls
    `self._generators.suppress(script_lanes, at + span)` where
    `script_lanes` are the `(resolved dev, status, data1)` of the
    expanded midi-kind cues and `span` is the script's last step offset;
    `_unload` clears `self._generators`.
- Note: `_dispatch_generator_cues` computes
  `at = self._clock() + self._horizon` and passes the engine's elapsed-run
  clock (add `self._run_elapsed`, accumulated in `tick`, reset in
  `_run()`), dispatching via the existing `_dispatch_cues(cues, at,
  FIRED_BY_BIT_ADJUDICATED)`; `_dispatch_bit_fires` drains
  `bit.fires(at)`, guarded exactly as `_dispatch_bit_cues` was
  (exception -> log + ignore tick).

- [ ] **Step 1: Write failing runner tests**

```python
# tests/test_generator_runner.py
def test_triangle_value_deterministic():
    spec = GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                         waveform="triangle", period=12.0, lo=0, hi=254)
    assert GeneratorRunner.value(spec, 0.0) == 0
    assert GeneratorRunner.value(spec, 3.0) == 127
    assert GeneratorRunner.value(spec, 6.0) == 254
    assert GeneratorRunner.value(spec, 9.0) == 127

def test_suppression_skips_lane_but_phase_advances():
    runner = GeneratorRunner([_gen()])
    runner.suppress([(ROOM, 0xB0, 74)], until_at=105.0)
    assert runner.cues(elapsed=3.0, at=100.0) == []
    assert runner.cues(elapsed=6.0, at=105.0) == [(ROOM, 0xB0, 74, 254)]
```

- [ ] **Step 2: Run** -> FAIL (module missing)

- [ ] **Step 3: Implement** `control/generator_runner.py`.

- [ ] **Step 4: Write failing engine tests** in
`tests/test_engine_functions.py`: a unit Bit declaring one generator on
ROOM cc:74; after `run()`, `tick(dt)` emits the expected `(room_dev, 0xB0,
74, value)` through `on_light_cue`; `fire_function` of a scripted
Function writing cc:74 suppresses generator emissions until `at + span`
and they resume after; a Bit whose `fires(at)` returns
`[FireFunction("x", ROOM)]` fires it exactly as `cues()`-returned
`FireTrigger`s did (reuse/port the existing bit-adjudicated fire test); a
`fires()` returning a plain cue tuple is dropped with nothing dispatched;
`fire_function` naming a GENERATOR function returns
`"function 'drift' is not scripted"` -style refusal.

- [ ] **Step 5: Implement the engine changes**, delete `Bit.cues`,
migrate every test Bit under `tests/` that implemented `cues()` (grep
`def cues` in `tests/`; move FireFunction returns to `fires()`, lane
drift into declared generators or drop where the test only needed *a*
cue — judge per test intent).

- [ ] **Step 6: Full suite green; commit**
`git commit -m "feat(engine): engine-run generator Functions replace Bit.cues(at)"`

---

### Task 4: TestBit and MetronomeBit conversion (generators + fires)

**Files:**
- Modify: `bits/test/test_bit.py`, `bits/metronome/metronome_bit.py`
- Test: `tests/test_test_bit.py`, `tests/test_metronome_bit_declarations.py`,
  `tests/test_metronome_bit_judgment.py`, `tests/test_metronome_bit_finale.py`,
  `tests/test_terrarium_cycle.py`

**Interfaces:**
- Consumes: Task 3's `Bit.fires(at)`, GENERATOR functions.
- Produces: `TestBit.function_table` gains
  `"room_drift": Function(kind=GENERATOR, generator=GeneratorSpec(dev=ROOM,
  status=0xB0, data1=74, waveform="triangle", period=12.0, lo=0, hi=254))`;
  `TestBit.cues` deleted; `TestBit.fires(at)` returns the latched
  `[FireFunction("play_aurora", ROOM)]` (latch/rounds-won bookkeeping
  moves verbatim); `SCRIPT_QUIET_SECONDS` and `_quiet_until` are deleted
  (the engine's overlay window owns it now). MetronomeBit: grep
  `def cues` — convert the same way if present, else only the import
  spellings already handled in Task 1.

- [ ] **Step 1: Update `tests/test_test_bit.py`** — the drift-value
assertions move from calling `bit.cues(at)` to
`GeneratorRunner.value(TestBit's declared spec, elapsed)` plus one
engine-level test that the value reaches `on_light_cue` at the right tick;
the round-won test asserts `fires(at)` latches (returns the FireFunction
once, then `[]`).

- [ ] **Step 2: Run** -> FAIL (TestBit still has cues()).

- [ ] **Step 3: Convert both Bits.** Values must reproduce today's drift
exactly: old `cc = int(round(254 * triangle_frac))` == GeneratorSpec
`lo=0, hi=254` under `GeneratorRunner.value` (`254 *` is not a typo for
127 — preserve it; the old code's comment context lives in the Function's
description).

- [ ] **Step 4: Full suite green** (cycle tests included). **Commit**
`git commit -m "feat(bits): TestBit/MetronomeBit declare generator Functions; cues() gone"`

---

### Task 5: Instrument ambient generators + `terrarium.toml` functions tables

**Files:**
- Modify: `control/instrument.py`, `control/terrarium_config.py`,
  `terrarium.toml`, `devicelink/agent.py`
- Test: `tests/test_instrument.py`, `tests/test_terrarium_config.py`,
  `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: Task 2's `Function`/`GeneratorSpec`/`validate_function_table`;
  Task 3's `GeneratorRunner`.
- Produces:
  - `Instrument.functions: tuple[Function, ...] = ()` (was
    `tuple[str, ...]`); `validate_instrument` additionally builds a
    throwaway `FunctionTable` from them and runs
    `validate_function_table`, refusing any non-GENERATOR kind with
    `f"instrument {name!r}: only generator Functions may be declared on an
    instrument (v0)"`; `TUNESHROOM.functions = ()` (the old
    `("tap","tilt")` strings are deleted — `gesture.*` capabilities
    already carry that fact).
  - `control/terrarium_config.py`: `[[instruments.<name>.functions]]`
    array-of-tables, each `{name, description = "", kind = "generator",
    lane = {dev = "room"|"target", status, data1}, waveform, period,
    lo = 0, hi = 127}` parsed into a `Function`; any parse/validation
    defect is a located `TerrariumConfigError`. A bare
    `functions = ["tap"]` legacy list is refused with a located message
    naming the new table shape.
  - `devicelink/agent.py`: `_setup_room()` additionally builds
    `self._ambient_generators = GeneratorRunner(all fixtures' instruments'
    functions)` when rendering ambient (no Bit); the ambient render tick
    feeds `runner.cues(elapsed, at)` values into the room session via the
    existing feed path (`_feed_room` / wherever ambient midi enters —
    match the existing ambient code's seam), with elapsed measured from
    ambient session start; the Bit-loaded branch skips it entirely
    (last-start-wins by construction: ambient session and its runner are
    swapped out with the session itself).
  - `room_view.py` fixture cards: `functions` renders each declared
    generator as `{"name", "kind": "generator", "lane": "cc:74",
    "period": 12.0}` (extend the existing instrument block builder).

- [ ] **Step 1: Failing tests** — instrument: non-generator kind refused,
generators validated (bad waveform refused, located by instrument name);
config: a fixture instrument with one `[[instruments.x.functions]]` table
parses to the exact `Function`; legacy `functions = ["tap"]` refused with
a located error; agent: with no Bit loaded and an instrument declaring a
generator, the room session receives the triangle value on tick (extend
the existing ambient-render test fixture in
`tests/test_devicelink_agent.py`; use `tests/instrument_fixtures.py`).

- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Suite
green.** (Update `terrarium.toml` itself only if a shipped instrument
should animate: give `dev_strip`'s instrument no generator — shipped
config behavior stays visually unchanged this slice; the test fixtures
carry the animated case.)

- [ ] **Step 5: Commit** `git commit -m "feat(instruments): declared ambient generator Functions, config tables, ambient animation"`

---

### Task 6: Stream Functions in `GameServer.data()`

**Files:**
- Modify: `control/engine.py` (`data()`, `load_bit`),
  `control/functions.py` (mapping helper)
- Test: `tests/test_engine_functions.py`, `tests/test_functions.py`

**Interfaces:**
- Consumes: Task 2's `StreamSpec`/`StreamOutput`.
- Produces:
  - `control/functions.py`:
    ```python
    def stream_cues(fn: Function, dev: str, args: list) -> list[tuple]:
        """Mapped plain cues for one arriving verb. Clamps args[spec.arg]
        to [in_lo, in_hi] (mode "abs" takes abs(x) first), maps linearly
        onto each output's [out_lo, out_hi] (floats; inverted legal),
        int(round(...)), clamps 0-255. TARGET -> the gesturing dev;
        ROOM passes through for _resolve_dev. A missing/non-numeric arg
        returns [] (a malformed gesture maps to nothing, never raises).
        Boundary rule: when the value sits on two touching domains, the
        function whose in_hi it is applies (enforced by matching
        in_lo <= x <= in_hi in declaration order and skipping a later
        match on an already-written lane)."""
    ```
  - Engine `load_bit` snapshots `self._stream_functions: dict[str,
    list[Function]]` (verb -> declaration-ordered streams) beside the
    existing snapshots; `data()` after the registration check computes
    `at` once, collects `stream_cues` for every matching stream, then
    looks up the handler: **verb resolution changes** — a verb with
    streams and no handler is legal (dispatch stream cues, return None);
    a verb with neither remains `f"unknown verb {verb!r}"`; when both
    exist, handler cues and stream cues dispatch at the same `at`
    (streams first, one `_dispatch_cues` call with the concatenated
    list, `FIRED_BY_GESTURE_VERB`). A handler refusal string still
    refuses the whole gesture — stream cues are NOT dispatched on
    refusal (one gesture, one verdict).
  - `validate_function_table`'s gesture-verb condition check widens: the
    condition's verb must be in `verb_names | {stream verbs declared in
    this table}`.

- [ ] **Step 1: Failing tests** — mapping math unit tests
(clamp/invert/abs/rounding, touching-domain boundary: value 0.0 against
domains [-90,0] and [0,90] on the same lane yields ONE cue from the
lower-domain function); engine: stream-only verb dispatches mapped cues
and returns None; stream+handler share one `at`; handler refusal
suppresses stream cues; unknown verb unchanged; gesture-verb condition
naming a stream-only verb validates.

- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Suite
green.** **Step 5: Commit**
`git commit -m "feat(engine): declared stream Functions map gesture args to lanes in data()"`

---

### Task 7: TestBit stream conversion (byte-identical regression pin)

**Files:**
- Modify: `bits/test/test_bit.py`
- Test: `tests/test_test_bit.py`

**Interfaces:**
- Consumes: Task 6's stream dispatch.
- Produces: `TestBit.function_table` gains four STREAM functions:

```python
"tilt_hue": Function(..., kind=STREAM, stream=StreamSpec(
    verb="tilt", arg=1, in_lo=-90.0, in_hi=90.0,
    outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),
             StreamOutput(ROOM,   0xB0, 74, 0.0, 127.0)))),
"jam_level": Function(..., stream=StreamSpec(
    verb="tilt", arg=1, in_lo=0.0, in_hi=90.0,
    outputs=(StreamOutput(TARGET, 0xB0, 1,
                          JAMMER_LEVEL_REST * 127.0,
                          JAMMER_LEVEL_FULL * 127.0, mode="abs"),))),
"jam_hue_neg": Function(..., stream=StreamSpec(
    verb="tilt", arg=1, in_lo=-90.0, in_hi=0.0,
    outputs=(StreamOutput(TARGET, 0xB0, 2,
                          JAMMER_HUE_YELLOW * 127.0,
                          JAMMER_HUE_GREEN * 127.0),))),
"jam_hue_pos": Function(..., stream=StreamSpec(
    verb="tilt", arg=1, in_lo=0.0, in_hi=90.0,
    outputs=(StreamOutput(TARGET, 0xB0, 2,
                          JAMMER_HUE_GREEN * 127.0,
                          JAMMER_HUE_PURPLE * 127.0),))),
"shake_hue": Function(..., stream=StreamSpec(
    verb="shake", arg=3, in_lo=0.0, in_hi=90.0,
    outputs=(StreamOutput(TARGET, 0xB0, 74, 0.0, 127.0),))),
```

  `_on_tilt` keeps ONLY adjudication (clamp gamma, count full tilts, set
  `_round_won`) and returns `[]`; `_on_shake` is deleted (`shake` becomes
  stream-only); `_on_tap` unchanged. (`jam_level`'s mode="abs" with
  domain [0, 90] folds negative gamma; note in the Function description.)

- [ ] **Step 1: The regression pin (failing first).** Before converting,
add to `tests/test_test_bit.py` a table-driven engine-level test capturing
today's handler output for gammas
`(-90, -45, -0.0, 0.0, 30, 90, 120 (clamps to 90))` and sweep values
`(0, 45, 90, 100)` — drive `GameServer.data("dev-1", "tilt", ["dev-1",
gamma])` with a recording `on_light_cue` and assert the exact
`(dev, status, data1, data2, ...)` set matches the old `_on_tilt` math
(compute expected inline with the old formulas, spelled out in the test).
Run against the UNCONVERTED Bit first: PASS (this pins current behavior).

- [ ] **Step 2: Convert TestBit** as above.

- [ ] **Step 3: Run the pin** — must still pass byte-identically (the
mapping constants above were derived to make it so; if a ±1 rounding
mismatch appears, fix the declaration constants, never the pin).
Cue ORDER may differ (streams dispatch before handler cues; the old
handler emitted all four itself) — the pin asserts as a multiset per
lane, with a comment saying exactly that.

- [ ] **Step 4: Full suite green** (tilt-driven tests across
`tests/test_devicelink_agent.py`, cycle tests keep passing since bytes
are identical). **Step 5: Commit**
`git commit -m "feat(bits): TestBit gesture mappings become declared stream Functions"`

---

### Task 8: The sensing side — `control/triggers.py` (new meaning)

**Files:**
- Create: `control/triggers.py`
- Modify: `control/instrument.py`
- Test: `tests/test_triggers_sensing.py`, `tests/test_instrument.py`

**Interfaces:**
- Produces:
  - `control/triggers.py` (module docstring: "Sensing-side Triggers —
    Spec 3 section 6; the acting side lives in control/functions.py"):
    ```python
    @dataclass(frozen=True)
    class EventTrigger:
        name: str            # the verb it produces ("tap", "shake")
        description: str
        thresholds: dict     # flat str -> int|float, shipped verbatim

    @dataclass(frozen=True)
    class StreamTrigger:
        name: str
        description: str
        verb: str
        arg: int
        transform: str       # TRANSFORMS = frozenset({"smooth"})
        params: dict         # smooth: {"alpha": 0<a<=1}

    def validate_event_trigger(t, where: str) -> None
    def validate_stream_trigger(t, where: str) -> None
    ```
    Validation: names match `[A-Za-z0-9_-]+`; thresholds values numeric,
    keys str; transform in `TRANSFORMS`; smooth requires alpha in (0, 1].
  - `Instrument` gains `event_triggers: tuple[EventTrigger, ...] = ()`
    and `stream_triggers: tuple[StreamTrigger, ...] = ()`;
    `validate_instrument` validates both (located by instrument name).
  - `TUNESHROOM.event_triggers` declares `tap` and `shake` with today's
    de-facto native-detector values, provenance-commented:
    ```python
    # Values are the mm-tuneshroom native TapDetector's current constants,
    # carried here so the server owns them (Spec 3 section 6). The two
    # client detectors disagree by ~3x; real values come from capture/
    # traces via tools/trace_stats.py -- a later tool pass, not this
    # slice. Read the actual numbers from mm-tuneshroom's TapDetector at
    # conversion time (do NOT invent them; if the repo is not on disk,
    # ship placeholder keys {"peak_g", "window_ms", "double_ms"} with
    # value 0 REFUSED -- instead ask the human for the constants).
    ```
    (Executor: `ls /Users/chris/projects/mm-tuneshroom` and grep
    `TapDetector` for the constants; surface a blocker if absent.)

- [ ] **Step 1: Failing tests** (entities validate/refuse per rule above;
`TUNESHROOM.event_triggers` non-empty with numeric thresholds; instrument
validation catches a bad transform).
- [ ] **Step 2: Run -> FAIL. Step 3: Implement. Step 4: Suite green.**
- [ ] **Step 5: Commit** `git commit -m "feat(triggers): sensing-side EventTrigger/StreamTrigger entities on Instrument"`

---

### Task 9: Thresholds ship in the role blob

**Files:**
- Modify: `control/role_config.py` (`compose_role_config`),
  `control/engine.py` (`join` call site), `devicelink/protocol.py`
  (blob-shape doc comment)
- Test: `tests/test_role_config.py`, `tests/test_engine.py`

**Interfaces:**
- Consumes: Task 8's `EventTrigger`, `Instrument.event_triggers`.
- Produces: `compose_role_config(..., event_triggers: tuple = ())` — when
  non-empty, `config["triggers"] = {t.name: dict(t.thresholds) for t in
  event_triggers}` (deep-copied; omitted entirely when empty, same
  never-null discipline as the slot/instrument stamps).
  `GameServer.join`'s granted path passes the carried instrument's
  `event_triggers` (the same `carried` already resolved for the
  `satisfies` check / instrument stamp). ROOM joins pass nothing.

- [ ] **Step 1: Failing tests** — blob carries `"triggers"` with
TUNESHROOM's tap/shake thresholds on a granted join; absent for a carried
instrument with no event triggers; absent from ROOM-join blobs; mutation
of the returned dict does not alias the instrument's declaration.
- [ ] **Step 2-4: Run/implement/suite green.** Also update
`devicelink/protocol.py`'s role-blob documentation block (it is the wire
source of truth) describing the new optional `triggers` key and that the
mm-tuneshroom client consuming it is recorded cross-repo follow-up.
- [ ] **Step 5: Commit** `git commit -m "feat(wire): carried instrument's event-trigger thresholds ship in the role blob"`

---

### Task 10: Stream triggers transform args in `data()`

**Files:**
- Modify: `control/engine.py`
- Test: `tests/test_engine_functions.py`

**Interfaces:**
- Consumes: Task 8's `StreamTrigger`; Task 6's `data()` shape.
- Produces: `data()` — before stream Functions and the handler run, the
  arriving `args` pass through the carried instrument's `stream_triggers`
  matching this verb: for `transform="smooth"`,
  `y = alpha * x + (1 - alpha) * y_prev` with per-`(dev, trigger.name)`
  state in `self._stream_trigger_state`, seeded `y_prev = x` on first
  sample; the transformed value REPLACES `args[trigger.arg]` for
  everything downstream (streams, handler). State for a dev clears on its
  release (`_unload`'s per-device path and `leave`) and wholesale on
  `_unload`. No shipped instrument declares one (TUNESHROOM's tuple stays
  empty — a smoothed reference fixture would hide responsiveness
  regressions, spec section 6); coverage comes from a test instrument in
  `tests/instrument_fixtures.py`.

- [ ] **Step 1: Failing tests** — first sample passes through unchanged;
second is EMA-blended; state is per-device; state resets after release;
a verb with no matching stream trigger is untouched.
- [ ] **Step 2-4: Run/implement/suite green. Step 5: Commit**
`git commit -m "feat(engine): sensing stream triggers transform gesture args server-side"`

---

### Task 11: Console — kind-tagged Function cards and trigger read-outs

**Files:**
- Modify: `control/function_view.py`, `control/room_view.py`,
  `console/static/functions.js`, `console/agent.py` (only if the
  fire-command guard needs the kind refusal surfaced — check)
- Test: `tests/test_function_view.py`, `tests/test_room_view.py`,
  `tests/test_console_agent.py`, `tests/test_console_protocol.py`,
  `tests/js/functions_and_rail.test.js`

**Interfaces:**
- Consumes: Tasks 2/5/8 declarations.
- Produces:
  - `function_view(fn)` gains `"kind": fn.kind.name.lower()`; SCRIPTED
    keeps today's fields; GENERATOR emits `{"kind": "generator",
    "lane": {"dev", "status", "data1"}, "waveform", "period", "lo",
    "hi"}`; STREAM emits `{"kind": "stream", "verb", "arg",
    "in_lo", "in_hi", "outputs": [{"dev", "status", "data1", "out_lo",
    "out_hi", "mode"}]}`. `functions_view` unchanged (declaration order,
    all kinds).
  - `room_view`/`fixtures_view` instrument blocks add
    `"event_triggers": [{"name", "thresholds"}]` and the generator
    summary from Task 5.
  - `functions.js`: scripted cards keep the Fire button; generator and
    stream cards render their declaration lines and NO Fire button; the
    `function_fired` single-card patch discipline is untouched; new
    fields join the declaration signature per the established
    `surface.js` discipline.
- Wire shapes pinned via `wire_json.dumps` in the existing
  console-protocol byte-shape tests (extend, following the file's
  established pattern).

- [ ] **Step 1: Failing view tests** (exact dicts per kind; instrument
block extensions). **Step 2: Run -> FAIL. Step 3: Implement views.**
- [ ] **Step 4: JS** — extend `tests/js/functions_and_rail.test.js`: a
snapshot containing all three kinds renders three cards, only the
scripted one has a Fire button, and a `function_fired` event patches one
card with children intact. Implement in `functions.js`. Run
`tests/test_room_panel_behavior.py`-family wrappers (skip clean without
node, per the established pattern).
- [ ] **Step 5: Suite green; commit**
`git commit -m "feat(console): kind-tagged Function cards and event-trigger read-outs"`

---

### Task 12: Full-cycle pin, spec status, docs

**Files:**
- Modify: `tests/test_terrarium_cycle.py`,
  `docs/superpowers/specs/2026-08-27-functions-and-trigger-rename-design.md`
- Test: the cycle test itself

**Interfaces:** Consumes everything.

- [ ] **Step 1: Extend the full-cycle pin** (failing first where the
behavior is new): ambient generator animates the room session before
`load_bit` (two ticks, values differ); TestBit's declared generator
supersedes during RUNNING; a `play_aurora` fire suppresses the drift lane
for the script span then the drift resumes; ambient animation returns
after unload; a joined device's blob carries `triggers`.
- [ ] **Step 2: Run -> implement any glue the pin exposes -> full suite
green.**
- [ ] **Step 3: Update the spec's Status section** (implemented date,
final suite count, deviations recorded during execution — including the
touching-domain boundary rule refinement of section 5 if the spec text
still says "disjoint").
- [ ] **Step 4: Commit** `git commit -m "test: full-cycle pin for generator/stream Functions and shipped thresholds"`

(Deep-dive `docs/MM_TERRARIUM.md` sync happens at branch closeout via
`mm-deepdive-sync`, per house convention — not a plan task.)
