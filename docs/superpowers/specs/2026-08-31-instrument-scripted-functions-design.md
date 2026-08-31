# Instrument-scripted Functions: room-authored cue content, universal diagnostics

**Date:** 2026-08-31
**Status:** Draft for approval
**Brainstormed with:** Chris (this session)

## Problem

Scripted Function content is authored once, in Python, inside a Bit, and
reused verbatim in every room the Bit loads into. TestBit's `play_aurora`
renders the identical cc:74 sweep whether the target is a 12-LED handheld
Testshroom or an 864-px venue array; nothing lets a room say "aurora looks
like THIS here." And the operator's troubleshooting vocabulary
(`flash_device`, `stop`, `win`) exists only because TestBit happens to
declare it: load a different Bit -- or no Bit -- and the Console has no way
to flash, silence, or ping any surface at all, because
`GameServer.fire_function` hard-refuses outside SETUP/RUNNING
(`control/engine.py:677`).

Direction, in the user's words: *"Aurora(Room) can be a trigger and an
instrument would have a Aurora(TuneShroom) or appropriate instrument name.
The idea is that the trigger will fire for that specific object... Triggers
can only be applied to the appropriate Instrument, and the drop down should
reflect this."* Plus: *"Flash, Stop, and maybe Ping... should be required
for all instruments, so the control panel should be able to call these for
any valid instrument to allow for testing and troubleshooting."*

## Decisions (from brainstorm)

| Question | Decision |
|---|---|
| Where do Flash/Stop/Ping live | Capability-driven built-ins, synthesized by Control from an instrument's `capabilities` -- authored nowhere |
| One spec or two | One spec covering both the built-ins and instrument-authored functions |
| Authoring home for room-custom functions | `terrarium.toml`, under the existing `[instruments.<name>]` tables |
| Bit linkage | Bit fires by NAME; the target's instrument supplies the script |
| Missing name on target | Load-time Console-visible warning + fire-time logged no-op (never an error) |
| Which Function kinds move | SCRIPTED only; GENERATOR and STREAM stay Bit-declared (gesture mappings and idle-drift intent are gameplay) |
| Bit-owned scripts | Removed entirely -- name-fire is the only scripted path; a bespoke effect is added to the room's instrument config |
| No-Bit firing path | Extend `GameServer.fire_function` to be Bit-optional (one dispatch path, one record shape), not a parallel maintenance path |

## Baseline

**1669 passed, 1 skipped**, fully offline (`.venv/bin/python -m pytest
tests -q`; a fresh worktree needs
`ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first -- see
`docs/MM_TERRARIUM.md` "Landed subsystems").

Prior art this spec builds directly on:

- `control/instrument.py` -- `Instrument` is first-class, carries
  `capabilities`, `accepted_cues`, `functions` (GENERATOR-only "v0" -- this
  spec is the v1 that restriction anticipated), and per-instrument ambient
  manifests.
- `terrarium.toml` `[instruments.<name>]` tables + `control/
  terrarium_config.py` parsing -- the config home and located-error
  validation style.
- PR #56 (spec `2026-08-26-trigger-cards-and-surface-triggers-design.md`)
  -- `SolidCue` (timed override, expiry, blackout), `MuteCue` (per-surface
  latch: cancel pending, silence voice, blackout, suppress, un-mute on
  play), `SURFACE` targeting and the Console picker. All reused unchanged.
- `control/functions.py` -- `ScriptStep`/`expand_script`/
  `validate_function_table`; the SCRIPTED branch is what this spec
  restructures.
- `control/generator_runner.py` -- lane suppression is computed from
  expanded cues' lanes, so it keeps working regardless of where a script
  came from.

## 1. The instrument-function model

An `Instrument` gains **scripted functions**, authored in `terrarium.toml`
under the instrument's own table:

```toml
[instruments.venue_array.functions.aurora]
description = "Slow rainbow sweep across the whole array"
script = [
  { offset = 0.0, midi = [176, 74, 127] },
  { offset = 0.5, midi = [176, 74, 40] },
  { offset = 2.0, midi = [176, 74, 0] },
]

[instruments.tuneshroom.functions.aurora]
description = "Hue bloom on the handheld's ring"
script = [
  { offset = 0.0, midi = [176, 74, 127] },
  { offset = 1.0, midi = [176, 74, 0] },
]
```

- **Same name on two instruments is the point, not a conflict** -- that IS
  the per-instrument customization ("aurora behaves differently for the
  entire room").
- Step vocabulary matches the cue kinds `accepted_cues` already names:
  `midi = [status, data1, data2]`, `play = "sample-name"`,
  `solid = { rgb = [r,g,b], level = 0.9, duration = 5.0 }`, `mute = true`.
  A step kind an instrument's `accepted_cues` does not list is a config
  load error (same check `fire_function`'s `_check_cue_kinds` applies at
  fire time today, moved to load time where the author sees it).
- **No dev addressing in authored content.** An instrument script
  implicitly targets the surface it is resolved on; resolution supplies
  the dev. The `TARGET`/`ROOM` distinction disappears from authored
  scripts entirely (it remains an engine-internal concern).
- Parsed into the existing `Function`/`ScriptStep` shapes
  (kind=SCRIPTED) attached to `Instrument.functions`;
  `validate_instrument` drops its GENERATOR-only restriction and instead
  requires: GENERATOR entries follow today's rules; SCRIPTED entries carry
  a script, no condition, no target (both are fire-time concerns).
- Validation at config load, located-error style
  (`control/terrarium_config.py`'s existing pattern): bad offsets,
  non-monotonic steps, unknown step kinds, cue-kind-vs-accepted_cues
  mismatches, reserved names (section 2) all fail `terrarium.toml`
  loading with a message naming the instrument and function.

## 2. Universal built-ins: Flash, Stop, Ping

Synthesized by Control from an instrument's `capabilities` -- never
authored, never declared, identical at every venue:

| Built-in | Requires | Behavior |
|---|---|---|
| `flash` | any `light.*` | `SolidCue(white, 0.9, 5.0)` -- expires back to whatever was rendering; plus `PlayCue("chime")` when the instrument also has `audio.samples` (exactly today's `flash_device`) |
| `stop` | any `light.*` or `audio.*` | The existing `MuteCue` latch: cancel pending cues, silence voice, unexpiring blackout, suppressed until un-muted (un-mute rules unchanged from PR #56) |
| `ping` | any `audio.*` | `audio.samples`: `PlayCue("chime")` (device-local, sub-20 ms). `audio.flsyn`-only (the venue array -- no local sampler): a short fixed note-on/note-off via its Arco voice, so the Room is ping-able too |

- `flash`/`stop`/`ping` are **reserved names**: config validation refuses
  an instrument-authored function with any of these names, so the
  troubleshooting vocabulary cannot drift per venue.
- Always fireable: no Bit, any GameServer state, any TerrariumState with
  a Room loaded.
- A built-in a target's capabilities cannot support (ping at a
  light-only instrument) resolves as "not present" -- greyed out on the
  Console, a logged no-op if fired anyway over the wire.

## 3. The firing ladder, and Bit name-fires

### fire_function becomes Bit-optional

`GameServer.fire_function(name, dev, ...)` resolves `name` in order:

1. **Built-ins** (`flash`/`stop`/`ping`) -- synthesized against the
   resolved target's instrument capabilities.
2. **The target's instrument-declared functions** -- the room-authored
   content of section 1.
3. **Nothing** -- a logged no-op recorded as a `FunctionFired` with zero
   resolved steps (the existing "reached nothing is visibly that" shape),
   never an error.

The current `if self.state not in (SETUP, RUNNING): return "no Bit
running"` gate (`control/engine.py:677`) applies only to Bit-mediated
fires (a gesture-verb or bit-adjudicated condition). A manual fire
(`fired_by="admin-manual"`) walks the ladder in any state, Bit or no Bit.
Everything downstream is reused as-is: `_dispatch_cues`, the timed queue,
horizon stamping, the mute latch, generator-lane suppression, and
`FunctionFired` records reaching the Console's event log.

### Bits fire names, never scripts

The Bit-side SCRIPTED kind is replaced by a **name-fire** declaration: a
`FunctionTable` entry keeps `name`/`description`/`condition`/`target` and
has no script -- content comes from whatever instrument the fire resolves
on. `FireFunction` returns from verb handlers and `fires(at)` work
unchanged; they were already name-based.

- `load_bit` cross-checks every name the Bit declares against the loaded
  Room's present instrument types and **warns Console-visibly** for each
  instrument type lacking the name (built-ins never warn -- they are
  everywhere). The Bit still loads: a room with mixed instruments where
  one fixture lacks a flourish is a degraded room, not a broken one.
- At fire time a missing name on the resolved target is a logged no-op
  per target (a ROOM-target fire fanning to three fixtures fires the two
  that implement the name and no-ops the third).
- **TestBit migration:** `flash_device` and `stop` are deleted (built-ins
  supersede both -- `flash_device`'s chime+white and `stop`'s latch are
  byte-for-byte what the built-ins do). `play_aurora` and `win` become
  name-fires; their scripts move into `terrarium.toml` for
  `venue_array`, `dev_strip`, and `tuneshroom` (each free to differ).
  `drift` (GENERATOR) and `tilt_hue`/`jam_level` (STREAM) are untouched.

### What stays Bit-side, and why

GENERATOR and STREAM Functions remain Bit-declared. A stream mapping
("tilting controls hue") IS the game mechanic; a generator's existence
("this game idles with a slow hue wander") is gameplay intent. Instruments
already have their own ambient generator channel for the no-Bit case, so
the per-instrument-feel gap is smallest exactly there. Revisit only if a
real room-customization need appears for either.

## 4. Console

Function cards merge two sources: the loaded Bit's name-fires (as today)
plus, per surface, that surface's instrument-declared functions and the
built-ins.

- **The dropdown filters both ways.** Picking a surface filters the
  fireable list to that instrument's names + its supported built-ins;
  picking a function greys out surfaces whose instrument lacks it. This
  is the "drop down should reflect this" requirement.
- **A diagnostics row is always present** -- Flash / Stop / Ping per
  surface, rendered compactly, available with no Bit loaded (today's
  panel is Bit-scoped and disappears entirely between Bits).
- A Bit name-fire's card shows the per-instrument description of whatever
  the current picker selection resolves to, so the operator reads what
  will actually happen on THAT surface.
- Wire shape: `functions_changed`/snapshot views gain the instrument
  provenance (which instrument's declaration a card renders); the
  `fire_function` command is unchanged.

## 5. Testing

Offline (house rule -- no Arco, no luxaeterna in core paths):

- TOML parsing/validation: scripts parse into `Function`/`ScriptStep`;
  located errors for bad offsets, unknown step kinds, cue kinds not in
  `accepted_cues`, reserved names.
- `validate_instrument` v1: SCRIPTED allowed with script/no
  condition/no target; GENERATOR rules unchanged.
- Built-in synthesis per capability shape: light-only gets flash+stop,
  audio-only gets ping+stop, both get all three; unsupported built-in
  resolves absent.
- Ladder order: built-in shadows nothing (reserved names make collisions
  impossible); instrument name resolves per-target; missing name no-ops
  with a zero-step `FunctionFired`.
- No-Bit firing: manual fires work in IDLE with no Bit; Bit-mediated
  fires still gated to SETUP/RUNNING.
- `load_bit` gap warnings: a name-fire missing from one present
  instrument type warns once, Console-visibly, and still loads.
- TestBit migration: `function_table` carries no scripts; the
  suppression interaction (drift suppressed during a resolved aurora,
  resuming in phase) still holds via expanded-cue lanes.
- Console JS: filtered pickers both directions; the always-present
  diagnostics row; no-rebuild disciplines preserved.

Live checklist (deferred to post-implementation, house pattern): fire all
three built-ins at the Room and a Testshroom with **no Bit loaded**; run
TestBit end-to-end confirming `play_aurora`/`win` render from
config-authored scripts, differently on the Room vs a device.

## Out of scope / deferred

- Moving GENERATOR/STREAM authoring to instruments (revisit on real need).
- o2audioio-out to device speakers (unchanged from PR #56's deferral).
- Per-room overrides of another room's instrument functions beyond what
  "each room's config declares its own instruments" already provides.
- Any Bit-manifest (bit.toml) surface for declaring name-fires -- they
  stay in Python `FunctionTable` declarations this slice.
