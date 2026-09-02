# Handoff: rooms catalog, Design tab Room editor, and luxaeterna O2 time

Paste this file's path into a fresh mm-terrarium session to launch the next
slice. It is Plans 2 and 3 named in
`docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md`
sections 6 and 7 (verify merge to main before branching, same as this note
asks of every successor).

## Where Plan 1 left the system

Plan 1 gave every Room fixture a complete instrument on its light side, not
only its audio side. A Room is now an ordered list of named fixtures with no
session or audio channel of its own; each fixture has its own `LightSession`
from Room load (bound or not), its own Arco voice grant when audio-capable,
and its own `FixtureSink` list (`ConsoleFrameSink` always, `DeviceLinkSink`
when a device is bound). A Bit addresses a fixture by name with
`@fixture:<name>`; `@room` broadcasts to every bound fixture; `load_bit`
refuses a Bit naming a fixture its Room does not declare. See
`docs/MM_TERRARIUM.md`'s "Per-fixture light sessions, `@fixture:<name>`
addressing, FixtureSinks (2026-09-01)" entry for the full landed detail, and
the design spec's section 12 Status for what shipped versus what deviated
from the plan-facing prose.

One deviation worth carrying forward explicitly: the reference cross-fixture
chase does NOT live on `TestBit`. A `TestBit` naming `@fixture:main`/`accent`
could not load on the one-fixture DEMO room, so it lives in a new packaged
Bit, `bits/chase/` (`ChaseBit`, a `TestBit` subclass, `bit.toml`'s `[launch]
room_types = ["TEST"]`). Any future Bit that addresses fixtures by name
belongs to the Room spec it names, not to a Bit meant to run everywhere.

Accepted limitation carried forward (spec section 5.2): an unbound fixture
renders its session and shows in the Console but receives no Bit cues until
a device binds, because Control's resolver only ever produces bound devs.
Cue routing by fixture name for a fixture that never binds a device is a
named follow-up, not part of Plan 2 or 3.

## Scope of Plan 2: rooms catalog and the Design tab Room editor (spec section 6)

1. `rooms/<NAME>.toml` published, `rooms/drafts/<NAME>.toml` drafts,
   mirroring the instrument catalog. `[terrarium] room_paths` (default
   `["rooms"]`) beside `instrument_paths`. Inline `[rooms.<NAME>]` in
   `terrarium.toml` still parses; a name present both inline and in the
   catalog is a located `TerrariumConfigError`. "At least one room from
   either source" replaces today's "at least one `[rooms.<NAME>]`" rule.
2. `rooms/TEST.toml` and `rooms/DEMO.toml` are created with the current
   bodies verbatim; the `[rooms.TEST]`/`[rooms.DEMO]` tables come out of
   `terrarium.toml`. Pin a test that the loaded TEST and DEMO `RoomProfile`s
   equal the pre-migration ones -- this is the regression that matters most,
   since every live checklist step depends on TEST and DEMO staying exactly
   what they are today.
3. `control/catalog.py` gains a `kind` ("instrument" | "room") with
   `_parse_room_file` alongside the existing `_parse_instrument`.
   `CatalogEntry` carries `kind`; `load_catalog`, `save_draft`,
   `clone_entry`, `publish_entry` take it. The five design commands and the
   `designs_listed`/`designs_changed`/`design` events carry `kind`,
   defaulting to `"instrument"` so existing Console behavior does not move.
4. The Design tab gains a Rooms list beside Instruments: raw TOML editor
   with draft/publish/clone (same as instruments get today), plus ONE
   structured form -- the fixture list with move-up/move-down and an
   instrument picker limited to published instruments. Reordering rewrites
   the `[[fixtures]]` array order in the draft text. Blocks and zones stay
   raw TOML. Changes apply at the next Room load; this is not a live
   control.

## Scope of Plan 3: luxaeterna renders at O2 time (spec section 7, luxaeterna repo)

`LightSession.render_into` (luxaeterna `synth/session.py`) drops its private
`_start`; `t` becomes the injected clock's reading, `dt` the delta since the
previous read (first frame: a small positive constant, as today). Since
Control injects `o2lite.time_get` in o2lite mode, every session on the box
agrees on `t`. The plan's own audit: built-in ugens and `StatusDirector` for
a "t starts at zero" assumption (welcome signatures, `SegmentLevel` with
`loop_from`) -- anything that needs a local origin must capture it on its
own first render rather than relying on the session's. A test asserts two
sessions built at different clock readings return equal `t` for the same
clock value. This work lands in the `luxaeterna` repo, not `mm-terrarium`;
mm-terrarium's per-fixture sessions already work without it, they merely
disagree on phase by their construction skew until it lands. Rainbow
continuity across main and accent (the live checklist's step 5) is gated on
this landing first.

## Key seams (verified as of Plan 1, this branch)

- `control/catalog.py`: `CatalogEntry`, `load_catalog(root)` (no `kind`
  param yet -- Plan 2 adds it), `_parse_instrument`.
- `control/terrarium_config.py`: `_parse_room` (line ~338), the inline
  `[rooms.<NAME>]` parse path Plan 2 must keep working alongside the new
  catalog.
- `console/static/design.js`: the Instruments list/editor Plan 2's Rooms
  list mirrors.
- `console/static/toml_edit.js`: the structured-TOML-rewrite helper Plan 2's
  fixture reorder form uses (the instrument forms already use it for a
  precedent).
- luxaeterna `synth/session.py`'s `render_into`: the O2-time change, and the
  ugen/`StatusDirector` audit that goes with it.

## Live checklist status (spec section 10) -- none of this has been run

1. Load TEST with no simulators spawned; both Console strips animate from
   the Bit's ROOM manifest. Not run.
2. Spawn both simulators; both bind and match the strips. Not run.
3. Fire `play_aurora` at `sim-room-accent`; only the accent changes, main's
   generator continues. Not run.
4. Fire `chase` (now on `ChaseBit`, not `TestBit` -- load `ChaseBit`, not
   `TestBit`, for this step). Main flashes, then accent, then both clear.
   Not run.
5. Rainbow continuity across main and accent, measured off the canvases.
   Blocked on Plan 3 landing first.
6. Load DEMO: unchanged, one strip. Not run.
7. Stop at `@all`, then ABORT, then Load Room recovery (PR #81's checklist,
   folded in here). Not run.

Run this on MYCOLOGICAL (the dev box with the Arco stack) before merging
Plan 1, per this slice's task-10 brief's live-verification section, which
gives the exact boot invocation and step-by-step operator script.

## Known minors deferred during Plan 1 (see the SDD ledger for full context)

These were flagged during Plan 1's task execution and deliberately not
fixed; triage them if you touch the same lines, otherwise they are safe to
leave:

- `test_cues`'s `'@fixture:z'` malformed-name case passes only because `:`
  falls outside the name regex, not from a deliberate double-prefix guard.
- The instrument SCRIPTED branch runs `_validate_script` then a second
  `dev != TARGET` loop; pre-existing, and it rejects `@room` on instrument
  scripts even though spec section 3.2's table lists `@room` as legal for
  instruments. Needs a decision: relax the check, or fix the spec table.
- Redundant `dict()`/deepcopy wrapping in `fixture_ambient`; a redundant
  `{**_decl()}` in one test; a test file with mid-file imports (pre-existing
  pattern elsewhere).
- `if sink is None: continue` is re-checked inside each `_dispatch_cues`
  loop iteration; no test pins "`@room` MuteCue mutes both fixtures with one
  `devices_change` notification."
- `_sinks_for` allocates two sink objects per fixture per tick (the brief's
  mandated shape; a cache would be the optimization).
- `_FakeAudioBridge`'s docstring still says "all dev-keyed" (stale,
  pre-existing).
- No JS test pins a live fixture's dev change keeping its strip/paint intact;
  the Console's `_current_room` guarded path is untested; the
  `fixture_controllers` payload has no JS consumer yet; a `control/teardown.py`
  paragraph's example no longer demonstrates its own claim; stale
  `lastPaintByName` entries for a fixture dropped from a live Room are not
  cleaned up (pre-existing shape).

## House workflow and repo gotchas

- brainstorm -> spec -> writing-plans -> subagent-driven-development with
  the mm-sdd-board progress board. Lead every option set with a
  `(recommended)` pick.
- Tests ONLY via `.venv/bin/python -m pytest tests -q`; a fresh worktree
  needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`. Baseline at
  this slice's HEAD: 1951 passed, 1 skipped.
- No em dashes in anything authored (docs, comments, commit messages); the
  repo's "--" style is fine.
- `control/` is pure stdlib; `control/` and `devicelink/` never import
  pyarco or luxaeterna (light session types are injected/lazily imported at
  the harness edge -- `harness/room_surface.py` is that seam for
  `SurfaceCapability`).
- Read `docs/MM_TERRARIUM.md`'s per-fixture light sessions entry
  (2026-09-01) and the per-fixture instruments entry (2026-09-01, same day,
  earlier in the file) before designing. Update the deep-dive on the same
  branch at closeout.
