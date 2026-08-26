# Trigger cards compaction, SURFACE targeting, and the four operator triggers

**Date:** 2026-08-26
**Status:** Draft for approval
**Brainstormed with:** Chris (this session)

## Problem

The Console's Triggers panel and TestBit's reference triggers have outgrown
their first slice:

1. Trigger cards are wider and wordier than Instrument cards; at normal
   console width only ~2 fit per row, and long content overflows.
2. `flash_device` is a weak diagnostic: a cc:74 hue blip plus a click. The
   operator wants a real identify gesture: sound on the device, all LEDs
   solid white at 90% for 5 seconds, then dark.
3. `play_aurora` targets only the Room; it should be fireable at any
   connected device too.
4. There is no way to stop sound and light on a surface, and no win
   celebration trigger.

## Decisions (from brainstorm)

| Question | Decision |
|---|---|
| How to render solid white | New override cue type (`SolidCue`), applied over the session, bypassing instruments |
| Targeting model | New `SURFACE` target kind: picker lists Room + every connected device; all four triggers use it |
| Stop depth | Latched per-surface mute (dark + silent until cleared) |
| Un-mute | Any Play-type fire (Play Aurora, Win, Flash) at a muted surface clears the mute first |
| Flash/Win sound | Device-local samples (`PlayCue`) — the shipped sub-20 ms path. Arco-synthesized per-device sound was considered and rejected for now: Arco's `o2audioio` ugen can stream audio out to a device, but no device-side receiver exists anywhere in the stack; that is a separate future slice ("o2audioio out to Tuneshroom"), not this one |
| Flash after 5 s | Override expires and the surface reverts to session ambient (not latched dark) |

## 1. Card layout compaction

Goal: ~3 cards per row, Instrument form factor, nothing overflowing.

- `.triggrid` min column width drops from 240px to match `.instgrid`'s
  215px scale (tune on the live panel; the acceptance test is 3-across at
  the console's default 1460px max width with the rail visible).
- Card typography drops to the `.inst` scale: mono detail rows at
  ~11.5px, name/heading reduced accordingly.
- Buttons, target pickers and status lines get `min-width: 0`, ellipsis or
  wrap rules so a long device id or script step never widens the card.
- `console/static/triggers.js` markup adjusted only as needed for the
  wrap containers; the no-rebuild-on-`trigger_fired` discipline and
  `tests/js/trigger_panel_behavior.test.js` invariants are preserved.

## 2. `SolidCue` — the override cue

A new cue type in `control/cues.py`, sibling of `PlayCue`/`LightCue`
(distinct type, not a magic tuple, same rationale as the existing cues):

```
SolidCue(target, rgb, level, duration)   # e.g. (TARGET, (255,255,255), 0.9, 5.0)
```

Semantics: the renderer applies a solid frame (rgb scaled by level) **on
top of** whatever the session renders, for `duration` seconds from its
presentation time, then reverts. It bypasses instruments entirely — no
manifest declaration is needed, so it works on every surface including
roles with empty light manifests.

- Rides the existing timed-cue path: stamped with `when` by
  `GameServer._dispatch_cues`, held on `DeviceLinkAgent._light_cues` like
  any timed cue.
- Wire: a new envelope in `devicelink/protocol.py` (single source of truth),
  mirrored in the o2lite transport; device side handled by `ShroomClient`
  and the websocket clients. A `duration=0` solid with `level=0` is a
  blackout with no expiry — the primitive Stop reuses.
- Renderer: applied at the frame-slicing point so a Room fixture gets its
  slice of the override exactly like it gets its slice of the session
  frame.

## 3. `TargetKind.SURFACE`

A third trigger target kind alongside ROOM and DEVICE.

- Console: a SURFACE card always renders a picker listing the Room
  (as one entry, resolved to the canonical bound dev like ROOM cues
  already are) plus every connected device, reusing the existing
  DEVICE-picker plumbing (`fillDevicePicker`).
- Engine: `GameServer.fire_trigger`'s `_resolve_target` gains the SURFACE
  branch — the fire command carries the chosen surface id; "room" resolves
  exactly as ROOM does today (first bound fixture, fed once).
- Validation at `load_bit` in the established shallow-structural style.

## 4. Per-surface mute (Stop's latch)

Control-global state, keyed by surface (dev id, with the Room as its
canonical dev), surviving nothing beyond the running Bit — cleared at
UNLOADING like other runtime state.

On **Stop** fire at a surface:
1. Cancel that surface's pending trigger-script cues in the timed queue.
2. Silence its Arco voice (expression 0 / note-off via its
   `AudioBridge`; the Room's drone likewise).
3. Apply an unexpiring blackout `SolidCue`.
4. Record the mute; Console shows a MUTED chip on the surface (Room strip
   and/or device row, and on SURFACE cards' pickers).

While muted: gameplay cues and ambient rendering for that surface are
suppressed at the dispatch/render seam (cheapest single choke point —
`DeviceLinkAgent`), so nothing re-lights it.

On any Play-type fire (Play Aurora, Win, Flash) at a muted surface: clear
the mute first, then run the script. No dedicated un-mute control.

## 5. The four TestBit triggers

All four are SURFACE-target, declared in `bits/test/test_bit.py`'s
`trigger_table`:

- **`flash_device`** (Test) — script: `PlayCue(TARGET, "chime")` +
  `SolidCue(TARGET, white, 0.9, 5.0)` at t=0. After 5 s the override
  expires; the surface reverts to ambient. (Room target: the Room has no
  local-sample player, so the sound half is skipped there — the flash is
  the identify signal; documented limitation until o2audioio-out.)
- **`play_aurora`** — existing cc:74 script unchanged, target widened to
  SURFACE. Still also fired bit-adjudicated by the three-tilt latch (which
  keeps targeting the Room).
- **`stop`** (new) — admin-manual condition; performs the mute latch
  above. Not a script in the normal sense: the spec adds a
  `MuteCue`/engine action rather than forcing latch semantics through
  ScriptSteps. (Exact mechanism decided at plan time; the constraint is
  that firing stays observable via the normal `TriggerFired` record.)
- **`win`** (new) — admin-manual condition; script:
  `PlayCue(TARGET, "win")` (new ascending sample added to the role sample
  sets) plus a short celebratory light figure on existing lanes (hue
  sweep), so it reads visually as well as audibly. Room target: light
  figure only, same limitation note as Flash.

## Out of scope / deferred

- **o2audioio-out to device speakers.** Arco's ugen supports it
  (bidirectional, with flow control); no device-side receiver exists in
  mm-terrarium or mm-tuneshroom, phones have no path at all, and the
  manifest vocabulary doesn't cover output routing. Its own future slice.
- Real win/chime asset design beyond a placeholder ascending figure.
- Any scoring semantics behind Win — this is an operator celebration
  button, not adjudication.

## Verification

- Offline: unit tests for `SolidCue` (protocol round-trip, expiry,
  blackout-no-expiry), SURFACE resolution, mute latch (suppression +
  Play-clears-it), TestBit declaration validation; JS tests for the
  compacted cards (3-across is CSS, but picker/wrap behavior and
  no-rebuild invariants are assertable).
- Live (per the house pattern): run the stack against a real Arco, fire
  all four from the panel at both the Room and a simulated device;
  confirm white-90%-5s-then-ambient off the canvas, mute latch dark and
  silent until a Play, and card layout at 3-across.

## Live verification (pending)

Offline gates passed 2026-08-26: Python suite 1379 passed, 1 skipped, 0
failures (`.venv/bin/python -m pytest tests -q`); JS suite 5 files, 0
failures (`node --test tests/js/*.test.js`). No live verification has been
performed. The checklist below is recorded for the operator/session to run
against a real Arco, per the house pattern.

- [ ] `python -m harness.run_stack` (TEST room), open the Console.
- [ ] Triggers panel shows 4 cards, 3-across at full width, nothing overflowing.
- [ ] Fire `flash_device` at the Room: both sim canvases go solid white ~90% for 5 s, then resume ambient drift; at a joined sim device: its canvas flashes and (if the client has a `chime` asset) sounds.
- [ ] Fire `stop` at the Room: canvases go dark, drone silent; stays dark >10 s.
- [ ] Fire `play_aurora` at the stopped Room: un-mutes, aurora script runs.
- [ ] Fire `win` at a device: hue flourish visible; `win` PlayCue observable in the device client log even if no asset.
- [ ] Card status lines update in place (no list rebuild).
