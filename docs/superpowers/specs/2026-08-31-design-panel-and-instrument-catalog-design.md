# Design panel and the instrument catalog

**Date:** 2026-08-31
**Status:** Brainstormed design, pre-plan. Decisions below were made
interactively with Chris; each records what would change the call where one
was discussed.

## 1. Problem

Instruments became first-class data entities in the 2026-08-27 restructure
(`control/instrument.py`, `[instruments.<name>]` in `terrarium.toml`), but:

- They are hand-edited TOML plus one Python constant. There is no authoring
  surface, no preview, and a typo is only caught at boot.
- `TUNESHROOM` is hardcoded in code and every hello'ing device is presumed
  to carry it. There is no path for a device (or a simulator) to *become*
  any other instrument.
- The measurement pipeline (`capture/` + `CaptureBit` +
  `tools/trace_stats.py`) exists precisely to replace the guessed
  tap/shake thresholds, but nothing feeds its output back into an
  instrument definition. The shipped thresholds are documented guesses.

The Design panel is the write-side and preview-side of all three: a new
left-nav console view (under ROOM) where an operator creates or edits an
Instrument as data, simulates it in the browser, and calibrates its
triggers from real recorded gestures.

## 2. Architecture decision: catalog first

**All instruments are Terrarium-defined; devices never self-describe.**
This stays. Server-owned definitions (Spec 3's server-owned thresholds,
generalized) mean one authority, updateable without reflashing hardware.

Consequence, confirmed in brainstorm: a "simulated Tuneshroom" is really a
generic *instrument host* (canvas + LEDs + gesture inputs + wire). Which
instrument it embodies should come from loading a Terrarium-provided
definition.

**Sequenced as three slices; this doc specs the first two:**

1. **Instrument catalog (pure refactor, no wire change).** Instruments
   move to per-instrument TOML files in an `instruments/` directory,
   loaded the same fails-hard way as today (pattern: `bit_paths`).
   `terrarium.toml`'s `[instruments.*]` table and the code-defined
   `TUNESHROOM` migrate in as the first entries. Fixture instruments and
   carried instruments are already the same dataclass; this is file
   layout, not model change.
2. **Design panel** over the catalog (this doc, sections 3-7).
3. **Carried-instrument wire support** (trailing slice, own spec):
   hello gains an optional instrument name, unknown/absent falls back to
   TUNESHROOM; Terrarium ships the full definition down so any host can
   become any catalog instrument. Cross-repo mm-tuneshroom follow-up.
   Would move up if the near-term goal became "new physical instrument on
   real hardware this fall".

## 3. Persistence: draft/published catalog entries

Each catalog entry has a **draft** state the panel edits and simulates
freely, and a **published** state that is the only thing rooms and devices
can load. Publish runs the full existing validation
(`validate_instrument`, function-table and trigger validators, manifest
validators via role_config) and refuses with located errors. Boot stays
fails-hard on published entries only; a half-finished draft can never
brick a room load.

## 4. What is editable

Structured forms, plus a raw-TOML tab for power editing. **Data-only in
v1: instruments carry no Python.** Scripts stay in Bits (SCRIPTED
functions, verb handlers); instrument-owned scripting would need a
sandboxing/API-version story and is its own future spec if generators +
streams prove insufficient.

- Identity: name, description.
- `capabilities` (checkboxes against `CAPABILITY_VOCABULARY`) and
  `accepted_cues` (against `CUE_KINDS`).
- Ambient manifests: light (luxaeterna instruments, targets, cc lanes)
  and ugen (program, drone) as structured editors.
- Functions: GENERATOR declarations (waveform/period/lo/hi per lane).
  STREAM mappings (gesture arg -> cc lane, domains, verb scoping) where
  the instrument declares them.
- Triggers: EventTriggers (verb, thresholds, window) and StreamTriggers
  (e.g. EMA alpha). New event-trigger verbs may be introduced freely
  (verbs are open strings); the Calibrate flow (section 6) is how they
  get real thresholds.
- **Surface geometry (pixels, zones, color order) is view-only in v1** --
  it lives on the Fixture per Spec 2, and a Room editor is separate
  future work.

Creation is **clone-based**: a new instrument starts as a copy of a
chosen catalog entry (TUNESHROOM default) under a new name, so every
draft starts valid. No blank-form path in v1.

## 5. In-browser simulator

The panel embeds an instrument host that loads the current **draft**
definition and renders through the real pipeline, not a lookalike:

- Light: a `WebSimBackend`-style canvas fed by a real `LightSession`
  built from the draft's manifests, with the draft's generators run by
  the real `GeneratorRunner`.
- Input: synthetic gesture controls (tilt slider, tap/shake buttons)
  routed through the draft's StreamTriggers and STREAM functions --
  the same transform code a live room runs.
- **Trace replay:** recorded `capture/` traces (labelled real
  accel/gyro/mic data) can be replayed against the draft, so a threshold
  edit is immediately validated against a real recorded gesture: tune,
  replay, watch the trigger fire or not.
- Audio preview only when Arco is up, clearly badged; the panel must be
  fully usable with no Arco (offline discipline holds).

## 6. Calibrate flow (core feature of the first slice)

Per event trigger:

1. Operator hits Calibrate; the panel arms a capture session labelled for
   that trigger (reusing `CaptureBit`/the capture wire verbs).
2. Operator performs the gesture on a real device N times.
3. `tools/trace_stats.py` analysis runs server-side over the session.
4. The panel proposes thresholds; operator accepts them into the draft,
   with provenance recorded (session id, date) replacing the current
   "guessed, awaiting capture pass" comments.
5. Trace replay (section 5) verifies before Publish.

This closes the documented guessed-thresholds gap and is the panel's
unique value over hand-editing TOML.

## 7. Console integration

- New left-nav view **Design** under the ROOM group (`console/static`),
  same ConsoleServer/ConsoleAgent transport, same trusted-LAN/no-auth
  model. Catalog CRUD, draft/publish, calibrate-arm, and trace-replay
  data ride new console wire commands/events; `uplink` never carries any
  of this (local admin only).
- Existing read-only instrument cards (`surface.js`
  `buildInstrumentCard`) remain the Live/Room views' renderers; Design
  reuses them where display-only.

## 8. Out of scope (recorded)

- Instrument-owned Python scripting.
- Fixture/room geometry editing (future Room editor).
- Carried-instrument wire support and mm-tuneshroom client changes
  (slice 3).
- Deriving thresholds automatically without operator accept.
- Any authentication story (trusted-LAN model unchanged).

## Status

**Shipped (this plan):**

- Section 2, slice 1 (instrument catalog, pure refactor): `control/
  catalog.py`, `[terrarium] instrument_paths`, the `terrarium.toml` ->
  `instruments/*.toml` migration, `TUNESHROOM` pinned to `instruments/
  tuneshroom.toml`.
- Section 2, slice 2 (Design panel), **partial**: catalog CRUD only --
  list/get/save/publish/clone as Console admin commands and events, the
  Design nav view, and a raw-TOML editor with draft-shadowing saves.
- Section 3 (draft/published persistence): full -- publish re-validates
  and fails hard, draft errors collect without blocking a save.
- Section 5 (in-browser instrument-host simulator): shipped, with one
  deliberate cut. Light preview runs the real pipeline (`DesignBench` +
  `LuxBenchSession`, `control/design_bench.py`, `harness/
  design_session.py`). Gesture input is fire buttons (one per `fireable()`
  row) plus a smoothed tilt lane -- **not** the stream-function idea this
  doc's rev 1 sketched; that path is superseded by the
  instrument-scripted-functions decision (see `control/builtins.py`'s
  section above), which forecloses instrument-owned Python. Trace replay
  (`replay_trace` command, `evaluate_trace`) is shipped. **Audio preview
  is explicitly not built** -- no Arco badge, no audio path in the bench
  at all; `DesignBench` skips `PlayCue` outright (see its module
  docstring).
- Section 6 (Calibrate flow): shipped. Capture-session stats
  (`capture_stats` command, `control/gesture_eval.py`), threshold
  proposal (`propose_thresholds`), provenance (a `# calibrated from`
  comment inserted into the raw TOML by `design.js`'s client-side
  `applyProposal`, reviewed by the operator before Save), and
  replay-before-publish all shipped. "Operator accepts" is that
  applyProposal-edit-review-Save sequence, not a server-side TOML writer.
- Section 7 (Console integration): full -- new Design nav view under the
  same ConsoleServer/ConsoleAgent transport, local-admin-only wire
  commands that never ride `uplink`.
- Section 4 (structured editing forms), Plan 3: shipped, with three named
  v1 residues. `console/static/design_forms.js` (a second view over
  `#designText`, write-through transforms via `toml_edit.js`) renders
  live editors for identity (description), capabilities/accepted_cues
  checkgrids against `design_vocab`, event/stream trigger cards
  (description, threshold/param fields, add/remove), function cards
  (generator: waveform/period/lo/hi; scripted: description plus a
  step-table with add/remove), and ambient manifest rows
  (`[ambient.light]`/`[ambient.ugen]`, matched by header suffix so the
  shipped fully-qualified form and the shorthand both resolve). **Named
  residues, all rendered as muted UI hints rather than left silently
  unsupported:** authoring a brand-new function from scratch, rewiring a
  generator function's lane, and declaring a new ambient block where none
  exists -- all three stay raw-TOML edits in v1.

**Unplanned (section 2, slice 3):** carried-instrument wire support (hello
gains an optional instrument name; Terrarium ships the full definition
down; mm-tuneshroom client changes). Hello still carries no instrument
name and `DeviceInfo.carried` still defaults unconditionally to
`TUNESHROOM`.
