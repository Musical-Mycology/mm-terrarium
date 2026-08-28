# Functions and the Trigger rename

Spec 3 of the Room/Instrument/Trigger restructure (brainstormed 2026-08-26/27;
direction recorded in
`2026-08-26-terrarium-lifecycle-and-config-rooms-design.md` section 12).
Spec 2 (Instruments and Fixtures, PR #61) is merged and this spec animates
the `functions` / `accepted_triggers` fields it deliberately left
declarative.

Binding decisions from the 2026-08-26/27 brainstorm, restated so this spec
cannot drift from them:

1. **Today's `Trigger` becomes a `Function` (scripted kind).**
2. **`Bit.cues(at)` becomes per-instrument generator Functions** -- one
   generator per element lane, last-start-wins, scripted Functions overlay
   rather than kill (the behavior the `play_aurora` live verify already
   exhibited: the drift superimposed on the script and resumed cleanly).
3. **Declared stream Functions replace hardcoded verb-handler cc
   mappings.**
4. **The name `Trigger` is reassigned to the sensing side**: event
   triggers (device-side detection, server-owned thresholds derived from
   `capture/` traces, shipped in the binding blob) and stream triggers
   (server-side transform/fusion pipelines).
5. **FULL rename, no dual-vocabulary period.**

Recorded dependencies (not blockers):

- The Spec 1 (lifecycle) and Spec 2 (instruments) live-Arco verification
  checklists are still **unrun** (see each spec's Status section). This
  spec's own live checklist (section 11) therefore verifies on top of
  unverified-live ground; running all three together in one dev-box
  session is the sensible move, and nothing here blocks on it.
- Spec 2's Status section records several parked minors (deviations
  recorded during execution -- the implicit-room-slot join rule, the
  dual-signature `validate_ugen_manifest`, the stand-in ambient Role).
  None of them blocks this spec; where this spec touches the same code it
  preserves those recorded shapes.

Baseline: **1529 passed, 1 skipped**, fully offline
(`.venv/bin/python -m pytest tests -q`; a fresh worktree needs
`ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first).

## 1. What changes, in one picture

Today one word does two jobs. "Trigger" names the *acting* side (a
Bit-declared, operator-visible thing that fires a cue script), while the
*sensing* side (what makes a tap a tap) has no name at all: detection
thresholds are hardcoded twice in mm-tuneshroom and disagree by 3x, the
`capture/` pipeline that measures reality has no consumer, and every
gesture-to-lane mapping is Python arithmetic buried in a verb handler.

After this spec:

```ascii
SENSING (Trigger)                      ACTING (Function)
                                       control/functions.py
EventTrigger (control/triggers.py)       kind="scripted": today's Trigger
  device-side detection;                   (condition + cue script),
  server-owned thresholds,                 fired via FireFunction
  shipped in the role blob             kind="generator": a declared lane
StreamTrigger (control/triggers.py)      driver (waveform/period/range),
  server-side transform of a raw         engine-run each tick -- what
  gesture stream, upstream of            Bit.cues(at) hand-rolled
  whatever consumes the verb           kind="stream": a declared
        |                                gesture-arg -> cc-lane mapping,
        v                                engine-applied on verb arrival --
   /game/<verb> arrives  ------------>   what _on_tilt hand-rolled
```

The render path is untouched: one shared MIDI stream still feeds light
and audio, `_dispatch_cues` still stamps presentation times, and
`DeviceLinkAgent` still renders and slices exactly as today.

## 2. The rename (binding decisions 1 and 5)

One atomic pass, entity-name discipline: every identifier, wire word,
file name, DOM id and doc reference that names the *acting* entity moves
from Trigger to Function. Incidental English uses of the verb "to
trigger" are rewritten only where they read as the old entity.

| Today | After |
|---|---|
| `control/triggers.py` | `control/functions.py` |
| `Trigger` / `TriggerTable` | `Function` / `FunctionTable` |
| `TriggerTarget` | `FunctionTarget` |
| `TriggerFired` | `FunctionFired` |
| `validate_trigger_table` | `validate_function_table` |
| `Bit.trigger_table` | `Bit.function_table` |
| `cues.FireTrigger` | `cues.FireFunction` |
| `GameServer.fire_trigger` | `GameServer.fire_function` |
| `_notify("on_trigger_fired", ...)` | `_notify("on_function_fired", ...)` |
| `control/trigger_view.py` | `control/function_view.py` |
| console `triggers_changed` / `trigger_fired` events | `functions_changed` / `function_fired` |
| console `fire_trigger` command | `fire_function` |
| `console/static/triggers.js` (and its DOM ids/headings) | `console/static/functions.js` |
| `Instrument.accepted_triggers` | `Instrument.accepted_cues` |
| `terrarium.toml` `accepted_triggers = [...]` | `accepted_cues = [...]` |
| `tests/test_triggers.py`, `test_engine_triggers.py`, `test_trigger_view.py`, `test_trigger_expansion.py` | renamed to the `function` spellings |

Notes, each load-bearing:

- **`accepted_triggers` -> `accepted_cues`.** The field names *cue
  kinds* (`midi`/`play`/`solid`/`mute`), not triggers of either
  vocabulary; keeping "triggers" in its name after the word is reassigned
  to sensing would be exactly the dual-vocabulary drift decision 5
  forbids. `CUE_KINDS` and the kind strings are unchanged. The old
  `terrarium.toml` key is **rejected with a located
  `TerrariumConfigError` telling the author the new spelling** -- the
  same fails-hard convention Spec 2 chose for the missing-instrument
  case, and for the same reason: the file is repo-local, there is one of
  it plus test fixtures.
- **Console wire events rename with it.** The console front-end ships in
  this repo and is versioned with the server; there is no external
  consumer of `triggers_changed` (the uplink never carried it). The
  `fired_by` wire strings (`"gesture-verb"` etc.) are unchanged -- they
  never contained the word.
- **Out of rename scope:** luxaeterna lane dests (`"dest": "trigger"` in
  a manifest entry is another repo's registry vocabulary, opaque to
  Control -- Spec 2 already pinned that opacity); `capture/` and
  telemetry vocabulary; English prose like "the notification was
  triggered".
- **`control/triggers.py` is reused for the sensing entities** (section
  6). Safe precisely because the rename is total: no import of the old
  names survives, and the new module exports different names, so a missed
  call site fails loudly at import rather than resolving silently.

## 3. `Function`: one entity, three kinds

`control/functions.py` (the renamed module) gains a `kind` discriminator.
A `FunctionTable` may hold all three kinds; validation dispatches on
kind.

```python
class FunctionKind(Enum):
    SCRIPTED = auto()    # condition + cue script; fired explicitly
    GENERATOR = auto()   # engine-run lane driver (section 4)
    STREAM = auto()      # engine-applied gesture->lane mapping (section 5)

@dataclass(frozen=True)
class Function:
    name: str
    description: str
    kind: FunctionKind = FunctionKind.SCRIPTED
    # SCRIPTED (exactly today's Trigger fields):
    target: FunctionTarget | None = None
    condition: Condition | None = None
    script: tuple[ScriptStep, ...] = ()
    # GENERATOR:
    generator: GeneratorSpec | None = None
    # STREAM:
    stream: StreamSpec | None = None
```

Validation: a scripted Function requires target+condition and refuses
generator/stream fields; and symmetrically for the other kinds. All
existing scripted-validation rules carry over verbatim (name as DOM id,
offset ordering, `_LEGAL_SCRIPT_DEVS`, the LightCue refusal).

`FireFunction` (renamed `FireTrigger`) may name only a **scripted**
Function; firing a generator or stream Function by name is refused with a
reason string, same never-raises convention as every other
`fire_function` refusal.

## 4. Generator Functions (binding decision 2)

What `Bit.cues(at)` hand-rolled -- TestBit's triangle drift on the
Room's `cc:74` -- becomes a declaration the engine runs.

```python
@dataclass(frozen=True)
class GeneratorSpec:
    dev: str            # cues.ROOM or cues.TARGET-style sentinel; ROOM today
    status: int         # 0xB0
    data1: int          # the cc number: the ELEMENT LANE this drives
    waveform: str       # vocabulary: "triangle" (v0; one constant set,
                        #   grown like CAPABILITY_VOCABULARY)
    period: float       # seconds per full cycle, > 0
    lo: int = 0         # emitted data2 range, 0-255, lo <= hi
    hi: int = 127
```

- **Engine-run.** `GameServer` keeps a per-lane generator registry built
  at `load_bit` (and from instrument ambients, below). Each RUNNING tick,
  where `_dispatch_bit_cues` ran, `_dispatch_generator_cues` computes
  each active generator's value from the engine's own elapsed-run clock
  and dispatches it as a plain `(dev, status, data1, value)` cue through
  the existing `_dispatch_cues` -- same stamping, same sinks, no new
  scheduler. Deterministic in elapsed time, so a test can assert the
  exact value at a given tick, exactly as TestBit's docstring promises
  today.
- **One generator per element lane** (`(resolved dev, status, data1)`),
  enforced at validation: two Functions declaring the same lane is a
  load-time error. **Last-start-wins** is the runtime rule for the same
  lane across *sources*: a Bit's declared generator supersedes an
  instrument's ambient generator on the same lane for the duration of the
  Bit, and the ambient one resumes at unload -- the same swap discipline
  the ambient/Bit manifest handoff already has.
- **Scripted overlays rather than kills.** When a scripted Function
  fires, the engine computes the script's span (its last step's offset)
  and suppresses generator emissions **on the lanes the script writes**
  until `at + span`. The generator keeps running (its phase advances; it
  is never torn down) and resumes emitting when the window closes. This
  is TestBit's `SCRIPT_QUIET_SECONDS` -- which its own comment already
  calls "the general shape here, not a TestBit quirk" -- moved into the
  engine, generalized from "all lanes for a fixed 2 s" to "the script's
  own lanes for the script's own span", and matching the live-verified
  `play_aurora` behavior.
- **Instrument ambient generators.** `Instrument.functions` stops being
  a tuple of bare strings and becomes a tuple of `Function` declarations
  (validated by the same `validate_function_table` machinery, located by
  instrument name). v0 restricts instrument-declared Functions to
  GENERATOR kind -- the concrete payoff is ambient *animation*: a room
  with no Bit loaded can now breathe instead of holding a static frame,
  closing the "ambient light is static" corner Spec 2 left. `TUNESHROOM`
  drops its `("tap", "tilt")` strings -- those were capability facts, and
  `gesture.tap`/`gesture.tilt` already say it -- and declares no
  generators. `terrarium.toml`'s `functions = []` key becomes
  `[[instruments.<name>.functions]]` tables (name, kind="generator",
  lane/waveform/period/range), parse-validated like everything else in
  that file.
- **`Bit.cues(at)` is deleted** (decision 5: no dual period). Its second
  job -- reporting a bit-adjudicated fire with a presentation time
  attached -- moves to a narrow replacement hook: `Bit.fires(at) ->
  list[FireFunction]`, drained once per RUNNING tick in the same place,
  guarded the same way, validated to contain only `FireFunction`s
  (anything else is logged and dropped -- lane-driving from this hook is
  exactly what generators exist to replace, so it must not creep back).

## 5. Stream Functions (binding decision 3)

What `_on_tilt` hand-rolls -- gamma in, cc values out -- becomes a
declaration the engine applies when the verb arrives.

```python
@dataclass(frozen=True)
class StreamSpec:
    verb: str                    # the /game/<verb> this consumes
    arg: int                     # index into the verb's args list
    in_lo: float                 # input domain, clamped
    in_hi: float
    outputs: tuple[StreamOutput, ...]

@dataclass(frozen=True)
class StreamOutput:
    dev: str                     # cues.TARGET (the gesturing device)
                                 #   or cues.ROOM
    status: int                  # 0xB0
    data1: int                   # cc number
    out_lo: int                  # mapped output range, 0-255; out_lo may
    out_hi: int                  #   exceed out_hi (inverted mapping legal)
    mode: str = "linear"         # "linear" | "abs" (|x| before mapping)
```

- **Engine-applied.** `GameServer.data()` consults the loaded
  `FunctionTable` for stream Functions on the arriving verb *before*
  dispatching to `verb_handlers()`. Each match clamps the chosen arg to
  `[in_lo, in_hi]`, maps linearly onto `[out_lo, out_hi]` (after `abs`
  when declared), and dispatches the resulting plain cues at the same
  `at` the handler call shares -- one gesture, one T, preserved.
- **Verb handlers keep game logic, lose mapping math.** A handler still
  runs after the streams (adjudication -- TestBit counting full tilts --
  and refusal strings live there), but a Bit that declares a stream for a
  verb no longer returns the mapped cues from its handler. `TestBit`
  converts fully, as the reference exemplar:
  - `tilt` -> three stream Functions: `tilt_hue` (gamma -90..90 ->
    TARGET+ROOM `cc:74` 0..127), `jam_level` (mode="abs", 0..90 ->
    TARGET `cc:1`, rest..full), `jam_hue` split into `jam_hue_neg`
    (-90..0 -> green..yellow inverted) and `jam_hue_pos` (0..90 ->
    green..purple) -- the two-segment shape falls out of two linear
    streams on disjoint domains, no curve vocabulary needed.
  - `shake` -> one stream (sweep 0..90 -> TARGET `cc:74` 0..127); the
    handler is deleted outright (it had no game logic).
  - `tap` keeps its handler (sample choice by count, the
    `FireFunction("flash_device")`) -- taps are events, not streams.
- **Multiple streams per verb are legal** (they write different lanes);
  two streams writing the *same* output lane on overlapping input
  domains is a load-time validation error, the stream analog of one
  generator per lane.
- Stream cues respect `accepted_cues` gating exactly as every other
  `midi` cue does (they pass through the same `_dispatch_cues`).
- **A gesture-verb condition's cross-reference widens**: a scripted
  Function's `verb` must now be implemented by `verb_handlers()` *or*
  consumed by a declared stream Function, since a fully-converted verb no
  longer needs a handler.

## 6. Triggers: the sensing side (binding decision 4)

A fresh `control/triggers.py` -- new entities, new meaning, pure stdlib.

```python
@dataclass(frozen=True)
class EventTrigger:
    """Device-side detection of a discrete gesture. The DEVICE runs the
    detector; the SERVER owns the thresholds and ships them in the
    composed role blob, so the two mm-tuneshroom detectors that today
    disagree by 3x converge on one server-declared truth."""
    name: str                    # the verb it produces ("tap", "shake")
    description: str
    thresholds: dict             # flat str -> number, shipped verbatim

@dataclass(frozen=True)
class StreamTrigger:
    """Server-side transform of a raw gesture stream, applied in
    GameServer.data() BEFORE stream Functions and verb handlers see the
    args -- the seam where fusion/smoothing pipelines will live."""
    name: str
    description: str
    verb: str
    arg: int
    transform: str               # vocabulary: "smooth" (EMA) v0
    params: dict                 # e.g. {"alpha": 0.3}
```

- **Instruments carry their triggers.** `Instrument` gains
  `event_triggers: tuple[EventTrigger, ...]` (and, symmetric but empty
  today, `stream_triggers`). `TUNESHROOM` declares `tap` and `shake`
  event triggers whose threshold dicts hold today's de-facto native
  detector values, each with a provenance comment naming the
  `capture/` + `tools/trace_stats.py` pipeline as the intended source --
  **deriving measured values from real traces stays a tool step, not
  done here** (the capture spec already recorded that deferral; this
  spec builds the shipping seam those values will flow through).
- **Thresholds ship in the role blob.** `compose_role_config` adds a
  `"triggers"` key: the carried instrument's event triggers as
  `{name: thresholds}`. A pure key addition to the blob, the same
  provenance-style extension Spec 2's `slot`/`instrument` stamps were;
  `devicelink/protocol.py` remains the wire's source of truth and
  documents it. The mm-tuneshroom client reading it is cross-repo
  follow-up work, recorded, not built here.
- **Stream triggers run in `data()`**, keyed `(verb, arg)`, stateful
  per-device (an EMA holds state per dev), reset on release. v0 ships
  the `smooth` transform and one TestBit usage is NOT declared --
  TestBit's tilt should stay raw (a smoothed reference fixture would
  hide gesture responsiveness regressions). Coverage comes from unit
  tests plus a dedicated test Bit fixture. Fusion (multi-verb) is
  explicitly deferred; the entity carries one verb until a real consumer
  demands more.
- **Console.** The instruments panel's fixture/device cards list event
  triggers (name + thresholds) alongside capabilities -- read-only v0.

## 7. Engine changes, collected

- `fire_function` (renamed): unchanged flow, plus the kind check
  (section 3) and the overlay-window bookkeeping (section 4).
- `data()`: stream triggers transform args -> stream Functions emit
  mapped cues -> verb handler runs (if any) -> handler cues dispatch.
  All at one shared `at`. A verb with neither streams nor a handler
  refuses exactly as an unknown verb does today.
- `tick()`: `_dispatch_generator_cues` (replacing `_dispatch_bit_cues`)
  then `Bit.fires(at)` drain. Generator state (phase origin, overlay
  windows) lives on the engine and resets on load/unload.
- Ambient generators run from `DeviceLinkAgent`'s existing ambient
  render path when no Bit is loaded (the agent already ticks ambient
  rendering; it asks the engine-side registry for ambient generator
  values the same way), and hand over to the Bit's generators per
  last-start-wins.
- `load_bit` validation order gains `validate_function_table` (renamed,
  extended per kinds) and the per-lane uniqueness checks; everything
  stays a `BitLoadError` with a located message.

## 8. Console

- `functions.js` (renamed): scripted cards keep the Fire button;
  generator and stream cards render their declarations (lane, waveform/
  period, verb/mapping) with no Fire button. Same
  declaration-signature/patch-in-place DOM discipline, same
  one-card-updates-on-`function_fired` behavior, re-pinned by the
  renamed JS behavior tests.
- `function_view.py` (renamed) gains kind-tagged card builders; the
  fired-record view is unchanged apart from names.
- Instrument cards gain the event-trigger read-out (section 6).

## 9. What this deliberately does not change

- **No wire framing change** -- devices still receive manifests, cues
  and (now) a `triggers` blob key; no new message types.
- **No mm-tuneshroom changes** -- the client consuming shipped
  thresholds is recorded follow-up.
- **No threshold derivation from traces** -- the seam ships with
  provenance-commented de-facto values; `tools/trace_stats.py` -> real
  numbers is a later tool pass.
- **No fusion stream triggers, no new waveforms/transforms/curves**
  beyond the v0 vocabularies -- each is one constant-set edit later.
- **No Spec 4 material** -- `bit.toml`, external Bits, bundling.
- **luxaeterna vocabulary untouched** (lane dests like `"trigger"`).

## 10. Testing

Offline throughout, suite green at every task boundary. By seam:

1. Rename completeness: `grep` over `control/ console/ devicelink/
   bits/ tests/` for the old acting-side identifiers comes back empty
   (a real test, not a habit: `tests/test_vocabulary.py` pins it so a
   future edit cannot reintroduce the old names); old `terrarium.toml`
   key refused with a located error.
2. Function kinds: per-kind validation acceptance/refusal; FireFunction
   at a non-scripted Function refused with a reason.
3. Generators: exact value at a given elapsed time; per-lane uniqueness
   load error; Bit-over-ambient last-start-wins and resume at unload;
   overlay window suppresses only the script's lanes for the script's
   span, generator phase uninterrupted; `Bit.fires` drains FireFunctions
   only.
4. Streams: mapping math (clamp, invert, abs, disjoint-domain pairs);
   engine dispatch shares the handler's `at`; same-lane-overlapping-
   domain load error; converted TestBit tilt/shake produce byte-identical
   cues to the old handler math (the conversion's regression pin);
   gesture-verb condition satisfied by stream-only verbs.
5. Triggers: role blob `triggers` key composed from the carried
   instrument; `smooth` EMA per-device state and reset; instrument
   validation of both trigger tuples.
6. Console: renamed events byte-shape pinned via `wire_json.dumps`;
   kind-tagged cards under the existing DOM-discipline node tests;
   `function_fired` still patches one card.
7. Full-cycle pin: ambient generator animates pre-`load_bit`, Bit
   generator supersedes during, ambient resumes after.

## 11. Live verification checklist (real Arco, dev box; post-merge)

Run together with the still-pending Spec 1 and Spec 2 checklists.

- [ ] 1. Room loaded, no Bit: ambient generator visibly animates a lane
      (the first time an empty room breathes).
- [ ] 2. `load_bit TestBit`: the Room drift runs from the declared
      generator; fire `play_aurora` from the Console: script sweeps,
      drift resumes -- the overlay behavior, now engine-owned.
- [ ] 3. Tilt a joined device: hue/jam lanes move from declared streams;
      confirm identical feel to the handler-math era.
- [ ] 4. Console shows Functions by kind; firing records unchanged in
      shape; instrument card shows tap/shake thresholds.
- [ ] 5. A joined device's role blob carries the `triggers` key with the
      declared thresholds.

## Status

Spec written 2026-08-27. Implemented 2026-08-27. Suite green at 1634
passed, 1 skipped throughout.

Deviations/rulings recorded during execution:

- Baseline correction: the pre-branch suite was 1539 passed, 1 skipped,
  not the 1529/1 this spec originally quoted.
- `accepted_triggers` legacy key: the located config error stays;
  `tests/test_vocabulary.py` carries a narrow `# legacy-vocabulary-ok`
  line-marker exemption for it.
- TestBit's drift generator is `lo=0 hi=127`, not the old `254*frac`
  math -- that formula's `frac` maxed at 0.5, so its observed max was
  127 already; the generator's `lo`/`hi` now say so directly.
- `FireFunction` gained `at: float | None` (an explicit presentation
  time). Discrete state-dependent beat cues (MetronomeBit's click
  track/flashes/pulses) ride SCRIPTED Functions fired from `fires(at)`
  at beat-grid times -- section 4's fires()-returns-FireFunctions-only
  rule stands, and its claim that scripted Functions are the only
  discrete-cue vehicle is realized through this seam, not an exception
  to it.
- Stream domain matching uses the RAW arg value; `jam_level` is a
  paired one-sided linear-stream pair (no `mode="abs"` in TestBit);
  `mode="abs"` remains in the vocabulary for a Bit that wants it.
- Edge-clamp rule (engine-side): a value beyond a (verb, lane)'s domain
  hull clamps to the nearest domain edge's function; interior gaps
  between disjoint domains still drop. Section 5's "disjoint domains"
  wording is refined: domains touching at a single shared endpoint are
  legal, and the lower domain's function applies there.
- `_validate_stream_lane_overlap` is scoped per verb.
- Cross-fixture generator lane collisions are a located room-profile
  error; ambient generator state clears on `unwire_room`.
- Thresholds ship for ALL granted non-ROOM joins, requires-less roles
  included (this spec's own full-cycle pin exercises the jammer role,
  requires-less and unscored, to prove exactly this); ROOM joins ship
  nothing.
- TUNESHROOM's shake thresholds reuse the native TapDetector constants
  (no native shake detector exists); provenance commented in code.
- `MM_TERRARIUM.md` deep-dive sync happens at branch closeout,
  separately, via `mm-deepdive-sync`.

Also recorded: the Spec 1/Spec 2 live-Arco checklists remain unrun;
this spec's section 11 checklist is pending and should run together
with them.
