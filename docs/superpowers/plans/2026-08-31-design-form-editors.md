# Design Panel Structured Form Editors Implementation Plan (Plan 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Design panel structured form editors (identity,
capabilities, accepted cues, triggers, functions, ambient manifests) as a
second view over the same draft TOML text the raw tab already edits.

**Architecture:** A new pure module `console/static/toml_edit.js` does
line-based, comment-preserving reads and targeted writes against the
instrument TOML subset (top-level scalars and string arrays,
`[[event_triggers]]`/`[[stream_triggers]]`/`[[functions]]` blocks with
nested `[x.thresholds]`-style tables and inline-table `script` step
lines). `console/static/design_forms.js` renders the forms and writes
every edit straight through to the `#designText` textarea via those
transforms; the textarea stays the single source of truth, the existing
Save/Publish flow is untouched, and manual raw edits rebuild the forms
(debounced). Vocabularies (capability tags, cue kinds) arrive from the
server in the snapshot so the checkboxes can never drift from
`control/instrument.py`. No server-side TOML writer is introduced.

**Tech Stack:** Vanilla JS console modules + node tests via
`tests/test_console_js.py`; one small Python wire addition
(`design_vocab` in the snapshot).

**Spec:** `docs/superpowers/specs/2026-08-31-design-panel-and-instrument-catalog-design.md`
section 4 (its Status section marks this as Plan 3). Post-PR-#74
amendment applied: instruments now also author SCRIPTED functions in
TOML, so scripted functions join the editable set; GENERATOR functions
are editable; STREAM functions do not exist on instruments and get no
editor.

## Global Constraints

- Run tests via `.venv/bin/python -m pytest` (never bare python3);
  fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`.
- Baseline at plan start: **1828 passed, 1 skipped** (main @ 0f3faeb).
- `console/protocol.py` keeps zero engine imports (vocab values are
  passed in by the agent, not imported by protocol).
- Form edits must preserve comments and untouched formatting -- notably
  the `# calibrated from <provenance>` lines the Calibrate flow writes.
  That is WHY the transforms are line-based; a parse-and-reemit
  serializer is out of bounds.
- The textarea `#designText` remains the single source of truth; forms
  never hold state the text does not. Save/Publish/Clone flows are
  untouched.
- Validation stays server-side: forms do not duplicate
  `validate_instrument`; a bad value surfaces through the existing
  save -> errors path.
- No em dashes in authored prose; docs use " -- ".
- Commit per task; full python suite + JS suite green before each commit.

---

### Task 1: `console/static/toml_edit.js` — line-based TOML subset transforms

**Files:**
- Create: `console/static/toml_edit.js`
- Test: `tests/js/toml_edit.test.js`

**Interfaces:**
- Consumes: nothing (pure strings). Read
  `instruments/tuneshroom.toml` and `instruments/venue_array.toml` first:
  they define the exact shapes these functions must handle (top-level
  scalars/arrays; `[[event_triggers]]` with an indented
  `[event_triggers.thresholds]` child table; `[[functions]]` with
  `kind = "scripted"` and a multi-line `script = [` ... `]` array of
  inline tables, or `kind = "generator"` sketched in the plan below;
  `[ambient.light]` / `[ambient.ugen]` tables with an `instruments = [...]`
  inline-table array). Also read `design.js`'s `findTriggerBlock` for the
  established block-scanning idiom (leave design.js untouched in this
  task).
- Produces (all pure; `text` in, `text` or value out; every writer
  returns the input unchanged when the target is not found, mirroring
  `applyProposal`'s unknown-trigger contract):

```js
export function splitBlocks(text)
// -> [{header: null|"[[event_triggers]]"|"[[stream_triggers]]"|"[[functions]]"|"[ambient.light]"|"[ambient.ugen]",
//      name: string|null, start, end}]  // line index ranges, end exclusive;
// header null = the top-level scalar region before the first [ line;
// name = the block's `name = "..."` value when present.

export function getScalar(text, block, key)      // -> raw RHS string | null
export function setScalar(text, block, key, raw) // -> new text (replace in
// place; when the key is absent, append it as the last line of the block,
// matching the block's dominant indentation). `block` is null for the
// top-level region or {header, name}.

export function getStringArray(text, key)        // top-level only -> [..]|null
export function setStringArray(text, key, values)// one-line rewrite:
// key = ["a", "b"]

export function getThresholds(text, triggerName)   // -> {key: number}|null
export function setThreshold(text, triggerName, key, value) // scalar write
// inside the trigger's indented thresholds child table (append when absent),
// never touching a `# calibrated from` comment line.

export function listScriptSteps(text, functionName)
// -> [{line, offset, kind, args}] where kind in "midi"|"play"|"solid"|"mute",
// args = the raw text after `<kind> = ` on that step line; line = absolute
// line index of the step.
export function setScriptStep(text, functionName, index, step)
// step = {offset, kind, args(raw string)} -> rewrites that one line as
// `  { offset = <offset>, <kind> = <args> },`
export function addScriptStep(text, functionName, step)   // append before `]`
export function removeScriptStep(text, functionName, index)

export function appendBlock(text, blockText)  // blockText appended with one
// separating blank line at end of file
export function removeBlock(text, header, name)
```

- [ ] **Step 1: Write failing node tests** (`tests/js/toml_edit.test.js`,
  using the same node test harness the other `tests/js/*.test.js` files
  use — read `tests/js/design_calibrate.test.js` first and copy its
  import/assert idiom). Build one fixture string modeled on
  tuneshroom.toml:

```js
const FIXTURE = `description = "A test shroom"
capabilities = ["light.pixels", "gesture.tap"]
accepted_cues = ["midi", "play"]

[[event_triggers]]
name = "tap"
description = "a tap"
  # calibrated from sess-1 on 2026-08-30
  [event_triggers.thresholds]
  peak_g = 2.0
  window_ms = 200

[[functions]]
name = "pulse"
kind = "scripted"
description = "two step pulse"
script = [
  { offset = 0.0, midi = [176, 74, 127] },
  { offset = 1.0, midi = [176, 74, 0] },
]
`;
```

  Cover at minimum, with exact-string assertions on every writer:
  - `splitBlocks` returns the top-level region, the tap trigger block,
    and the pulse function block with correct names and ranges.
  - `getScalar(FIXTURE, null, "description")` -> `"A test shroom"` (raw
    includes quotes; assert the raw form you implement and keep it
    consistent); `setScalar` on `description` rewrites only that line.
  - `getStringArray`/`setStringArray` on `capabilities` (set to
    `["light.pixels"]` and assert the one-line rewrite, everything else
    byte-identical).
  - `getThresholds(FIXTURE, "tap")` -> `{peak_g: 2.0, window_ms: 200}`;
    `setThreshold(..., "tap", "peak_g", 1.6)` rewrites only that line
    and leaves the `# calibrated from` comment untouched;
    `setThreshold(..., "tap", "double_ms", 400)` appends inside the
    thresholds table.
  - `listScriptSteps(FIXTURE, "pulse")` -> two steps with kinds "midi"
    and parsed offsets 0.0/1.0; `setScriptStep` rewriting step 1's args
    to `[176, 74, 64]`; `addScriptStep` appending
    `{offset: 2.0, kind: "play", args: '"chime"'}` before the closing
    `]`; `removeScriptStep(..., 0)` deleting exactly one line.
  - `appendBlock` + `removeBlock` round-trip a new `[[event_triggers]]`
    block; `removeBlock` on an unknown name returns input unchanged.
  - Every writer on a missing target (unknown block name, unknown key
    outside setScalar's append contract) returns the input unchanged.
- [ ] **Step 2: Run, verify failure:**
  `.venv/bin/python -m pytest tests/test_console_js.py -q` (the glob
  picks the new file up). Expected: new test file fails to import
  toml_edit.js.
- [ ] **Step 3: Implement** `toml_edit.js`. Implementation notes that are
  contractual: block scanning treats a line matching `/^\[\[/` or
  `/^\[/` (unindented) as a new block start; indented child tables
  (`  [event_triggers.thresholds]`) belong to the enclosing block;
  script step lines are the lines between `script = [` and the matching
  `]` that match `/^\s*\{\s*offset\s*=/`; step rewrite emits two-space
  indentation and a trailing comma exactly as the shipped files do.
- [ ] **Step 4: Run, verify pass; run full python suite.**
- [ ] **Step 5: Commit:** `git add console/static/toml_edit.js tests/js/toml_edit.test.js && git commit -m "feat(console): line-based TOML subset transforms for design forms"`

---

### Task 2: `design_vocab` on the snapshot wire

**Files:**
- Modify: `console/protocol.py` (snapshot_event kwarg), `console/agent.py` (snapshot)
- Test: `tests/test_console_agent.py` (append), `tests/test_console_protocol.py` (append)

**Interfaces:**
- Consumes: `control.instrument.CAPABILITY_VOCABULARY` (frozenset) and
  `control.instrument.CUE_KINDS` (tuple) — imported in `console/agent.py`
  only (protocol stays engine-free).
- Produces: `snapshot_event(..., design_vocab=None)` passes the value
  through as the snapshot's `"design_vocab"` key (None -> key present
  with value None, matching how other optional snapshot keys behave in
  this file — read snapshot_event and mirror its existing
  optional-kwarg convention exactly, whichever it is).
  `ConsoleAgent.snapshot()` fills
  `{"capabilities": sorted(CAPABILITY_VOCABULARY), "cue_kinds": list(CUE_KINDS)}`.

- [ ] **Step 1: Write failing tests:**

```python
def test_snapshot_carries_design_vocab(agent_fixture):
    snap = agent_fixture.snapshot()
    vocab = snap["design_vocab"]
    assert "light.pixels" in vocab["capabilities"]
    assert vocab["capabilities"] == sorted(vocab["capabilities"])
    assert vocab["cue_kinds"] == ["midi", "play", "solid", "mute"]
```

  (adapt `agent_fixture` to the file's real fixture idiom), plus a
  protocol test asserting `snapshot_event` passes the kwarg through
  verbatim.
- [ ] **Step 2: Run, verify failure; implement; run full suite.**
- [ ] **Step 3: Commit:** `git commit -m "feat(console): design_vocab (capabilities, cue kinds) in the snapshot"`

---

### Task 3: Forms core — identity, capabilities, accepted cues, raw/form sync

**Files:**
- Create: `console/static/design_forms.js`, `tests/js/design_forms.test.js`
- Modify: `console/static/index.html` (forms section inside `viewDesign`,
  directly above the raw `#designText` block), `console/static/shell.js`
  (call `initForms()` in the init sequence), `console/static/design.js`
  (export a `getSelection()` accessor for the current `{name, state}` if
  one does not already exist — smallest possible addition)

**Interfaces:**
- Consumes: Task 1's `getScalar`/`setScalar`/`getStringArray`/
  `setStringArray`; Task 2's snapshot `design_vocab`; the `design` wire
  event (`{name, state, text, errors}`) and the `#designText` textarea.
- Produces:

```html
      <section id="formsPanel" class="card">
        <label>Description <input id="formDescription" type="text"></label>
        <div id="formCapabilities" class="checkgrid"></div>
        <div id="formCues" class="checkgrid"></div>
        <div id="formSections"></div>
      </section>
```

```js
export function initForms()
// wire.on("snapshot", m => vocab = m.design_vocab)
// wire.on("design", m => rebuild(m.text))
// #designText "input" listener -> debounced (300 ms) rebuild(textarea.value)
export function rebuild(text)
// re-renders every form section from the text; later tasks hang their
// sections off this by pushing into SECTION_BUILDERS (see below)
export const SECTION_BUILDERS = []
// each entry: (container, text, apply) -> void, where apply(fn) reads the
// textarea, runs fn(text) -> newText through a toml_edit transform, writes
// the textarea back, and does NOT trigger the debounced rebuild (guard
// flag), so form edits update text without nuking form focus.
export function applyEdit(fn)  // the shared apply implementation
```

  Behavior in this task: description input writes through
  `setScalar(text, null, "description", JSON.stringify(value))` on
  change; capability and cue checkboxes render from `vocab` with checked
  state from `getStringArray`, and toggling writes
  `setStringArray(text, "capabilities"| "accepted_cues", newList)`
  (preserving the order the vocab lists them in). With no design open,
  the panel renders a single muted line "select a design" and no inputs.
- [ ] **Step 1: Write failing node tests:** rebuild from a fixture text
  populates the description input and checks the right capability boxes
  (vocab injected via a fake snapshot event); toggling a checkbox
  rewrites the textarea's `capabilities = [...]` line (exact-string
  assert) and leaves the rest byte-identical; editing description writes
  through; a manual textarea input event followed by the debounce
  rebuilds the checkboxes (drive the debounce the way existing JS tests
  drive timers — read how `design_bench.test.js` handled the tilt
  throttle and use the same approach, accepting immediate-rebuild
  assertions if the stub has no timer control).
- [ ] **Step 2: Run, verify failure; implement; run JS + full python suite.**
- [ ] **Step 3: Commit:** `git commit -m "feat(console): design form core (identity, capabilities, cues)"`

---

### Task 4: Trigger editors

**Files:**
- Modify: `console/static/design_forms.js`
- Test: `tests/js/design_forms_triggers.test.js` (new)

**Interfaces:**
- Consumes: Task 1's `splitBlocks`/`getThresholds`/`setThreshold`/
  `getScalar`/`setScalar`/`appendBlock`/`removeBlock`; Task 3's
  `SECTION_BUILDERS`/`applyEdit`.
- Produces: a builder pushed onto `SECTION_BUILDERS` rendering, into
  `#formSections`, one card per `[[event_triggers]]` block (name
  read-only label, description text input, one numeric input per
  thresholds key, an "add threshold" pair of key/value inputs, a Remove
  button) and one card per `[[stream_triggers]]` block (description,
  verb, arg, transform read-only, numeric inputs for each `params` key —
  reuse the thresholds machinery, the child-table name differs); plus an
  "Add event trigger" button that appends

```toml
[[event_triggers]]
name = "<prompted name>"
description = ""
  [event_triggers.thresholds]
  peak_g = 2.0
  window_ms = 200
```

  via `appendBlock` with the name from `window.prompt` (Clone's idiom;
  decline on falsy). Remove uses `removeBlock`. Threshold numeric inputs
  write through `setThreshold` on change; description via
  `setScalar(text, {header: "[[event_triggers]]", name}, "description", ...)`.
- [ ] **Step 1: Write failing node tests:** fixture with two event
  triggers renders two cards with the right numeric values; editing
  peak_g writes only that line (exact-string, `# calibrated from`
  comment untouched); add-trigger with a stubbed prompt appends the
  block; Remove deletes exactly that block leaving the sibling
  byte-identical; a stream trigger card renders its params.
- [ ] **Step 2: Run, verify failure; implement; run JS + python suites.**
- [ ] **Step 3: Commit:** `git commit -m "feat(console): design form trigger editors"`

---

### Task 5: Function editors (generator fields, scripted step table)

**Files:**
- Modify: `console/static/design_forms.js`
- Test: `tests/js/design_forms_functions.test.js` (new)

**Interfaces:**
- Consumes: Task 1's `splitBlocks`/`getScalar`/`setScalar`/
  `listScriptSteps`/`setScriptStep`/`addScriptStep`/`removeScriptStep`/
  `removeBlock`; Task 3's `SECTION_BUILDERS`/`applyEdit`.
- Produces: a builder rendering one card per `[[functions]]` block,
  branched on the block's `kind` scalar:
  - `kind = "generator"`: text/number inputs for `waveform`, `period`,
    `lo`, `hi` (each a plain `setScalar` write into the block; lane
    fields are shown read-only as text — lane rewiring stays a raw-TOML
    edit in v1, note rendered under the card).
  - `kind = "scripted"`: description input plus a step table from
    `listScriptSteps`: per row a number input for `offset`, a read-only
    kind chip, a text input holding the raw args (`[176, 74, 127]` or
    `"chime"`), Save-on-change via `setScriptStep`, a per-row Remove via
    `removeScriptStep`, and an "add step" row (offset input + kind
    select over midi/play/solid/mute + args text input) via
    `addScriptStep`.
  - Every card gets a Remove button (`removeBlock(text, "[[functions]]", name)`).
  No "add function" button in v1: a new function starts as a raw-TOML
  paste or a clone; rendered as a muted hint line under the section.
- [ ] **Step 1: Write failing node tests:** fixture with one generator
  and one scripted function renders both card types; editing a scripted
  step's args rewrites exactly that line; adding a step appends before
  the `]`; removing a step deletes one line; editing generator `period`
  rewrites its line; removing a function deletes its whole block leaving
  the other byte-identical.
- [ ] **Step 2: Run, verify failure; implement; run JS + python suites.**
- [ ] **Step 3: Commit:** `git commit -m "feat(console): design form function editors"`

---

### Task 6: Ambient editors + docs + spec status

**Files:**
- Modify: `console/static/design_forms.js`,
  `docs/MM_TERRARIUM.md`,
  `docs/superpowers/specs/2026-08-31-design-panel-and-instrument-catalog-design.md`
- Test: `tests/js/design_forms_ambient.test.js` (new)

**Interfaces:**
- Consumes: Task 1's `splitBlocks` (the `[ambient.light]` /
  `[ambient.ugen]` headers), `getScalar`/`setScalar`; read
  `instruments/venue_array.toml` for the real ambient shape first
  (`instruments = [ { instrument = "aurora", target = "primary" } ]`
  inline-table arrays, `program`/`drone` on the ugen side).
- Produces: a builder rendering, when the blocks exist: for
  `[ambient.light]`, one row per entry of its `instruments` inline-table
  array (parse the single `instruments = [...]` line with the same
  inline-table regex approach as script steps; text inputs for
  `instrument` and `target`, rewrite the whole line on change); for
  `[ambient.ugen]`, the same row treatment plus number inputs for
  `program` and the drone's `key`/`velocity` where present (drone is
  nested in the inline table: rewrite the entry's inline table
  wholesale from the row's inputs). Instruments without ambient blocks
  render a muted "no ambient declaration (add via raw TOML)" line —
  authoring a new ambient block from scratch stays raw-TOML in v1.
- [ ] **Step 1: Write failing node tests:** fixture with venue_array's
  real ambient shape renders the aurora row and the flsyn row with
  program 89 / key 48 / velocity 80; editing `target` rewrites the
  light `instruments = [...]` line exactly; editing `program` rewrites
  the ugen line exactly; a fixture without ambient renders the muted
  line and no inputs.
- [ ] **Step 2: Run, verify failure; implement; run JS + python suites.**
- [ ] **Step 3: Docs:** extend the deep-dive's Design panel material with
  a short entry: `toml_edit.js` (line-based, comment-preserving — the
  provenance-comment constraint is the stated reason there is no TOML
  serializer), `design_forms.js` (forms as a second view over
  `#designText`, write-through transforms, debounced raw-to-form
  rebuild, `design_vocab` snapshot key), and the v1 raw-TOML-only
  residues (new function authoring, lane rewiring, new ambient blocks).
  Update the spec's Status section: section 4 shipped with those three
  named residues. Match the file's " -- " style, no em dashes.
- [ ] **Step 4: Full python suite + JS suite; record counts. Commit:**
  `git commit -m "feat(console): design form ambient editors; docs + spec status"`

---

## Self-review notes (already applied)

- Spec 4 coverage: identity (T3), capabilities + accepted_cues against
  server vocab (T2/T3), ambient manifests as structured editors (T6),
  functions (T5 — GENERATOR editable; SCRIPTED added per the PR #74
  amendment; STREAM correctly absent from instruments), triggers with
  numeric threshold fields feeding the Calibrate synergy (T4), geometry
  correctly absent (fixture territory, spec says view-only elsewhere),
  clone-based creation already shipped in Plan 1.
- Placeholder scan: v1 residues (new-function authoring, lane rewiring,
  new ambient blocks) are named limitations rendered as UI hints and
  documented in T6, not deferred implementation steps.
- Type consistency: `SECTION_BUILDERS`/`applyEdit` (T3) consumed by
  T4/T5/T6; toml_edit function names identical across all tasks;
  `{header, name}` block spec shape consistent.
