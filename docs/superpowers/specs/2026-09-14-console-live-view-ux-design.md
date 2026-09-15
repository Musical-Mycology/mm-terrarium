# Terrarium Console Live-view UX pass: loading overlay, nav fix, cleaner Room card, trigger rows, icon buttons

Date: 2026-09-14. Brainstormed with Chris against the running DEMO Console
(Metronome loaded, `venue_array` fixture bound to `sim-room-array`).
Front-end only: no wire-protocol, backend, or `console/agent.py` change.
Every existing wire event and payload is sufficient. The standing
front-end-wide rule holds throughout: **no high-frequency wire event may
rebuild a DOM subtree whose declaration has not changed** (an armed
`wire.confirmTap` button dies with its element), so every new list below is
keyed by a stable identity and patched in place on `room_changed`,
`devices_changed`, `function_fired`, and `room_load_progress`.

## What the operator sees today, and what is wrong with it

1. Loading a Room (from the Room view's Load button, or implicitly from the
   Bit picker when the Terrarium is in `NO_ROOM`) takes roughly 15 s while
   Arco spawns. The only feedback is the status line on the Room view's
   card, which is invisible from the Live view, and the Bit picker closes
   with nothing in its place.
2. The sidebar nav reads `Room: none` after a Console-driven load until the
   page is reloaded. `shell.paintRoomNav` runs on `snapshot` only.
3. The Live Room card's fixture head carries a chip row (`venue_array`,
   `audio.flsyn`, `light.surface`, every scripted function, every accepted
   cue). It is static declaration data and it pushes the LED rows down.
4. "Live values · 4 instruments" lists the loaded Bit's ROOM-role manifest
   entries (three luxaeterna light declarations plus one FluidSynth
   program), each stamped with the same `instrument_name`. They are voices
   on one physical Instrument, and the card layout hides the one thing an
   operator watches: the live controller values.
5. Triggers sit below Live values, as a grid of tall cards, so an operator
   scrolls past the values to reach Fire.
6. The sidebar's Run / Restart / Abort / Load row wraps to two rows at the
   sidebar's width.

## Decisions

### 1. Loading overlay: a state-driven `busy.js`

- New module `console/static/busy.js`, initialised from `shell.js`, owning
  one modal overlay rendered into `#overlayMount` with the existing
  `.overlay` / `.picker` styling plus a `.busy` modifier (centered, narrow,
  no close button while in progress).
- **Driven by Terrarium state, not by the click.** It opens when
  `terrarium_state` becomes `ROOM_LOADING` or `ROOM_UNLOADING` (seen on
  `snapshot` or `state_changed`), so a load started from another browser
  tab, or a reconnect mid-load, shows it too. Title: `Loading Room <name>`
  or `Unloading Room <name>`; the name comes from the last
  `snapshot.rooms` row marked active, else the `room_loaded` payload, else
  the room name the Console itself sent on `load_room`.
- Body: the latest `room_load_progress` stage (`validating`, `sweeping`,
  `spawning arco`, `binding fixtures`, `room ready`) as a mono line, with
  the earlier stages listed above it and ticked, so progress is visible
  rather than a spinner. A pulsing dot (reusing `.phase .dot`'s pulse
  keyframe, skipped under `prefers-reduced-motion`).
- Closes on `room_loaded` and `room_unloaded`, and whenever
  `terrarium_state` settles to `ROOM_READY` or `NO_ROOM` (belt and braces
  for a missed event).
- **Failure keeps it open**: on `room_load_failed` (and on an `error` event
  whose `command` is `load_room`, `unload_room`, or `load_bit` while the
  overlay is up) the body switches to the reason text in `.inline-err`
  styling and a Dismiss button appears. Nothing else in the Console changes
  for the failure path; `wire.flashRefusal` still fires as today.
- It replaces the Bit picker's overlay when a `load_bit` from `NO_ROOM`
  triggers a Room load (`bit.js` already closes the picker on
  `room_loaded`; the busy overlay simply mounts after the picker closes).
- Generic surface for later long operations:
  `busy.begin({ title, closeOn: [event, ...] })`, `busy.stage(text)`,
  `busy.fail(text)`, `busy.end()`. Room load/unload is the first and only
  caller in this slice.
- The Room view's per-card status line keeps working unchanged.

### 2. `Room: none` nav fix

`shell.paintRoomNav` also runs on `room_loaded` (mark that name active) and
`room_unloaded` (clear). `bit.js` already maintains its own `rooms` copy the
same way; `shell.js` mirrors that rather than reaching into `bit.js`. A test
in `tests/js/wire_and_shell.test.js` drives `snapshot` with no active room,
then `room_loaded`, and asserts the nav text.

### 3. Room card: fixture chips move to the Room view

- `surface.js` no longer renders `instrumentTags` on the Live fixture. The
  fixture head keeps: fixture name, binding chip (dev id), pop-out link,
  Release / Arm controls. Then the LED block rows and the zone bar.
- `rooms.js`'s active-room detail (`renderDetail`) renders the same tag
  row under each fixture in "Fixtures & bindings", so the Instrument name,
  capabilities, functions, accepted cues, and event triggers stay one click
  away. `instrumentTags` moves to a tiny shared helper (exported from
  `surface.js`, imported by `rooms.js`), not duplicated.
- `fixtureShapeMatches` keeps comparing `instrument` so a changed
  declaration still rebuilds the fixture, even though the Live card no
  longer draws it; that keeps the rebuild signature identical to today and
  avoids a subtle behaviour change.

### 4. "Live values" becomes a lane table

- The accordion keeps its title "Live values"; its summary meta reads
  `<n> light · <m> audio voices` instead of `<n> instruments`.
- Body is one table, **one row per controller number** across every voice
  in `room.instruments`, in ascending cc order:

  | cc | value | read by |
  |---|---|---|
  | cc:11 | 0.47 | aurora level |
  | cc:21 | — | rainbow level |
  | cc:70 | — | bloom hue |
  | cc:74 | 0.47 | aurora hue · flsyn cutoff |

  "read by" lists `<voice> <dest>` for every lane whose `source` is that
  cc, joined with ` · `, and each voice name carries its kind as a small
  Light / Audio chip (the same chips the cards use today). A lane whose
  source is not `cc:<n>` (a note lane, `trigger`) is listed in a trailing
  "other lanes" row group with no value cell.
- The value cell reads from `room.controllers[cc]`; `—` when absent. A
  controllers-only `room_changed` patches value cells in place, exactly
  as `updateInstrumentLive` does today; the table is rebuilt only when
  the JSON signature of `room.instruments` changes.
- Per-voice static detail (target, params, program, drone) is dropped from
  the Live view. It remains visible in the Bit Details popup, which reuses
  `buildInstrumentCard` and is unchanged. `buildInstrumentCard` stays
  exported for that reason.
- The accordion is **closed by default**; it remembers the operator's last
  open/closed state in `localStorage` (wrapped in try/catch), the only
  per-viewer preference this slice introduces.

### 5. Triggers directly under the LED array, one row each

- Order inside the Room card body becomes: fixtures (LED rows) → Triggers →
  Live values. Both accordions stay in `surface.js`'s render (created once,
  as today); only the insertion order changes.
- The Diagnostics row stays first inside Triggers, unchanged.
- `functions.js` renders a `.fnlist` of rows instead of the `.fngrid` of
  cards. One row per function, keyed by name in `cardByName` (the Map is
  kept; its values become rows). Row layout, left to right:
  - name (`h3` styling as today, single line, ellipsised),
  - target chip (`ROOM` / `DEVICE` / `SURFACE`, or `generator` / `stream`),
  - mono summary: `<n> steps · <max offset>s` for scripted; `period <p>s
    · cc:<n>` for generator; `verb:<v> → <k> outputs` for stream,
  - a device picker where the target is `DEVICE` or `SURFACE` (same
    `fillDevicePicker` and compatibility refresh as today),
  - **(i)** info button,
  - Fire button (scripted only; generator/stream rows have none, matching
    `GameServer.fire_function`'s kind refusal),
  - last-fired text in `.fired-line` styling, `never fired` by default.
- The (i) button opens a popover anchored to the row (a `.popover`
  element positioned under the button, closed on outside click or Escape)
  containing: description, condition (`description (source: verb)`), and
  the full script steps as the `.script.mono` block renders them today.
  It is a read-only surface; Fire stays on the row.
- `function_fired` updates only that row's fired text and its
  `fired` / `fired-admin` left-border class, in place. A `functions_changed`
  with an unchanged signature is still a no-op.
- Row density target: one 32 px row per trigger at desktop width. On the
  narrow layout (`max-width: 800px`) the row wraps to two lines: name +
  chips + (i) on the first, picker + Fire + fired text on the second.

### 6. Sidebar action row: icons on one line

- Run, Restart, Abort, Load become square icon buttons (`.btn.icon`) on one
  row that never wraps: play ▶, restart ↻, stop ■, and a folder/plus glyph
  for Load. Inline SVG (no icon font, no build step, no CDN), 16 px, with
  `aria-label` and `title` carrying the current text ("Run", "Restart",
  "Abort", "Load a Bit").
- Colour keeps today's meaning: Run solid gold, Abort solid rose, Restart
  and Load outline.
- **Armed confirm state**: `wire.confirmTap` gains an optional
  `{ armStyle: "fill" }` path. Instead of swapping the label, it adds
  `data-armed` (already does) and the CSS renders `.btn.icon[data-armed="1"]`
  as filled in that button's colour with a subtle pulse; a single shared
  note element under the row reads `click again to confirm` (reusing
  `.inline-err` size, but in gold, not rose). Width never changes, which is
  what the 2026-09-13 "abort does nothing" fix cared about;
  `reserveConfirmWidth` is not called for icon buttons.
- Timeout without a second click keeps today's `flashNotConfirmed` note.
- `updatePanelDynamic` still gates `disabled` per `terrarium_state`; the
  phase chip below the row is unchanged.

## Out of scope

- Any `console/agent.py`, `control/`, or wire-protocol change. Every
  payload used above already exists.
- The Room view's Load / Unload buttons and its card layout beyond the
  moved chip row.
- The Design view.
- A generic toast / notification system. `busy.js` is a modal for
  blocking operations only.

## Testing

Each module keeps its node test file under `tests/js/`, run by
`tests/test_console_js.py`. New or extended assertions:

- `busy.test.js`: opens on `state_changed` with `ROOM_LOADING`; shows the
  latest stage; closes on `room_loaded`; stays open with the reason on
  `room_load_failed`; Dismiss closes it; opens from `snapshot` alone.
- `wire_and_shell.test.js`: nav text after `room_loaded` / `room_unloaded`.
- `surface_panel.test.js`: fixture head has no `.insttags`; accordion order
  is fixtures, Triggers, Live values; lane table rows keyed by cc; a
  controllers-only `room_changed` patches the value cell and keeps the
  table node identity; a changed `room.instruments` rebuilds it.
- `rooms_panel.test.js`: the active-room detail renders `.insttags` under
  each fixture row.
- `functions_and_rail.test.js`: one row per function; generator/stream
  rows have no Fire; `function_fired` keeps row identity and updates the
  fired text; the (i) popover carries description, condition, and steps.
- `bit_panel.test.js`: four icon buttons on one row with the right
  `aria-label`s; arming Abort sets `data-armed` without changing
  `textContent`; the confirm note appears; a second click sends `abort`.
- A live check in the browser against `./terrarium.sh --room DEMO` with
  the Metronome Bit loaded, at desktop and 800 px widths, before the PR is
  opened, since the last two Console passes each shipped a defect that
  only a real browser showed.
