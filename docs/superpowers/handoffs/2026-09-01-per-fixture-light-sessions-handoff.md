# Handoff: per-fixture light sessions and cross-fixture light effects

Paste this file's path into a fresh mm-terrarium session to launch the next
slice. It is the follow-up named in spec section 9 of the per-fixture
instruments slice (PR #81, branch claude/busy-bassi-984dc3; verify merge to
main before branching, same as this note asks of every successor).

## Where the last slice left the system

PR #81 made each Room fixture a real instrument for AUDIO and for the
diagnostics built-ins, but deliberately kept the Room's LIGHT side as ONE
shared LightSession built over the whole concatenated profile and sliced
per fixture (devicelink/agent.py: _setup_room builds it, _render_room
slices it). Consequences that this next slice exists to remove:

- The transport DROPS the light half of any MIDI cue addressed to a
  non-canonical fixture dev (_on_light_cue returns early after queueing
  the audio half). So an instrument-scripted light function (play_aurora
  and friends on dev_strip_main/dev_strip_accent) fired explicitly at the
  accent has audio but NO light until fixtures get their own sessions.
- fire_function's rung 1 still collapses ROOM/@all/PLAYERS fanout of
  Bit-declared scripts to the canonical dev (_collapse_room_fanout), and
  _suppress_generator_lanes folds only the canonical dev back to the ROOM
  sentinel for lane comparison. A parked finding from the last slice's
  review lives here: the fold-back misses rung-1 SURFACE Bit scripts with
  TARGET midi fired at a non-canonical fixture while a Room GENERATOR runs
  the same lane. It is inert today ONLY because of the transport drop
  above; per-fixture sessions must resolve it properly.
- A ROOM-addressed SolidCue resolves to the canonical dev and paints only
  the canonical fixture's slice (accepted delta, spec section 5).

## Scope of the next slice (from spec section 9, plus Chris's direction)

1. Each bound fixture gets its own LightSession over its own slice,
   completing the "each fixture is a real instrument" model in both
   halves.
2. Rework how a Bit ROOM role's light manifest binds: per-fixture
   manifests, or a manifest slicing rule. This is the central design
   question; the Bit role-declaration contract may change.
3. Design cross-fixture light effects: chases, sweeps, and anything else
   that spans main + accent once there is no single shared session to
   carry them. Chris explicitly wants the brainstorm to "get into more
   detail on how to define cross fixture light effects" -- treat this as
   an open design conversation, not a settled approach.
4. Retire the collapse machinery this unlocks: _collapse_room_fanout,
   the canonical-dev special cases in _suppress_generator_lanes and
   _resolve_dev's ROOM handling, and the transport's non-canonical light
   drop, to whatever extent the new model makes them unnecessary.

## Key code seams (verified as of PR #81)

- devicelink/agent.py: _setup_room (session build + per-fixture audio
  grants), _render_room (render, per-fixture override on each slice,
  audio drain), _on_light_cue / _feed_light_now (canonical-only light
  feed), _on_mute_change (per-fixture mute), _tick_overrides.
- control/engine.py: fire_function (explicit_surface gate, rung 1
  collapse, rungs 2/3 per-dev ladder), _collapse_room_fanout,
  _canonical_room_dev, _suppress_generator_lanes, _resolve_dev.
- control/room_bridge.py: RoomBridge is light-only now (one light sink);
  per-fixture sessions likely replace or multiply it.
- control/room_profile.py: fixture_slices(), blocks/zones. Note DEMO's
  venue_array is ONE fixture with six blocks; per-fixture sessions must
  not regress single-fixture rooms.
- harness/room_surface.py, harness/room_simulator.py: the sim canvases
  that display the slices.
- Ambient path: control/instrument.py's ambient_manifests is already
  per-fixture in shape; the Bit ROOM role manifest is the whole-room
  holdout.

## Deferred minors from PR #81 (fold in if touching the same lines)

- Docstring line-wrap artifacts: control/room_view.py:39-40,
  devicelink/agent.py:68-69.
- Drone-start gate ordering in _setup_room's grant loop is load-bearing
  and covered only by the surrounding comment.
- harness/o2_shroom.py's send_hello/send_join guards have no direct unit
  test (thin wrappers; module convention).

## Live verification debt

PR #81's operator checklist (Stop/Flash/Ping at sim-room-accent, Stop at
All, ABORT then Load Room recovery, diag order) may still be unchecked;
confirm with Chris before building on top of unverified behavior.

## House workflow and repo gotchas

- brainstorm -> spec -> writing-plans -> subagent-driven-development with
  the mm-sdd-board progress board. Lead every option set with a
  (recommended) pick.
- Tests ONLY via `.venv/bin/python -m pytest tests -q`; a fresh worktree
  needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`. Baseline
  at PR #81 HEAD: 1903 passed, 1 skipped.
- No em dashes in anything authored (docs, comments, commit messages);
  the repo's "--" style is fine.
- control/ is pure stdlib; control/ and devicelink/ never import pyarco
  or luxaeterna (the light session types are injected/lazily imported at
  the harness edge).
- Read docs/MM_TERRARIUM.md's sections on the per-fixture instruments
  slice (2026-09-01), instrument-scripted functions (2026-08-31), and the
  N-fixture Room before designing. Update the deep-dive on the same
  branch at closeout.
