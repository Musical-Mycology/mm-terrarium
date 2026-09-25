# Console fixture targets and fixture mute state

Status: approved design, 2026-09-25. Base: `main` at `fbc5c2f` (PR #140,
the Art-Net FixtureSink, is merged).

## 1. Problem

`docs/MM_TERRARIUM.md`'s *`devicelink/artnet_sink.py`, `[[artnet]]`,
routing by fixture name, native RGBW (2026-09-23)* entry records a bring-up
prerequisite: the Console cannot target, or show the mute state of, an
unbound Room fixture.

- The engine already addresses every declared fixture, bound or not:
  `GameServer._fixture_target(name)` returns the bound dev, else the
  `@fixture:<name>` token (`control/cues.py`'s `fixture_dev` /
  `fixture_name` are the only spellers of that prefix). Fixture mutes live
  in `GameServer.muted` under the token (`_mute_key` / `is_muted`).
- The Console's SURFACE and Diagnostics pickers (`console/static/functions.js`
  `fillDevicePicker`) offer only "All" plus rows from `devices_changed`,
  which `console/agent.py` `_devices_view` builds from joined devices only.
- So an unbound fixture (e.g. an Art-Net-driven array) is reachable only
  through "All", and its mute state is shown nowhere.

## 2. Goal

1. Every declared Room fixture appears as its own target, named by
   fixture, in the SURFACE-target and Diagnostics pickers.
2. A fire at a fixture resolves through the engine's `@fixture:<name>`
   path: to the bound dev when bound, else to the token.
3. The Room panel shows each fixture's mute state
   (`gs.is_muted(fixture_dev(name))`).
4. Device targeting is otherwise unchanged. DEVICE-target pickers ("the
   firing device") still never offer a fixture.

Non-goals: an un-mute control (any non-mute fire at a muted surface already
un-mutes it); the simulator-plus-Art-Net double-bind prerequisite (a
separate follow-up); zone-level targeting.

## 3. Wire changes

Both additive; an old browser tab ignores the new data.

- **`room.fixtures[]` rows gain `"muted": bool`.** Carried on `snapshot.room`
  and `room_changed`.
- **`surface_instruments` gains one key per declared fixture:**
  `"@fixture:<name>" -> <fixture instrument name>`, bound or not. Existing
  dev keys (bound fixture devs and connected devices) are unchanged.

## 4. Server design

### 4.1 `control/room_view.py`

`fixtures_view(profile, room, canvas_urls=None, muted=None)` and
`room_view(..., canvas_urls=None, muted=None)`: `muted` is an iterable of
fixture NAMES (default empty). Each fixture row carries
`"muted": name in muted`. The module stays engine-free (boundary: no engine
imports).

### 4.2 `console/agent.py`

- `_current_room` computes
  `{f.name for f in profile.fixtures if gs.is_muted(fixture_dev(f.name))}`
  and passes it to `room_view`. `_broadcast_room_if_changed` already runs
  every Console tick and diffs the whole payload, so a mute change reaches
  the browser with no new observer hook.
- `_current_surface_instruments` adds `fixture_dev(f.name) ->
  f.instrument.name` for every declared fixture of the loaded Room.

### 4.3 `control/engine.py` `_resolve_target`

For `FunctionTarget.SURFACE` with a `dev` that is a `@fixture:` token
(`fixture_name(dev) is not None`), return `self._resolve_devs(dev)`, so a
bound fixture lands on its bound dev exactly as a fire at that dev does
today, and an unbound one lands on its token. `FunctionFired.devs` then
reports the concrete target.

`fire_function` refuses (returns a reason, never raises) a SURFACE fire
whose token names no declared fixture of the loaded Room, or any token
with no Room loaded: `no fixture '<name>' in Room '<room>'` /
`no Room loaded for fixture '<name>'`. The check runs before
`_resolve_target`, alongside the existing "no surface given" refusal.

DEVICE targets are not changed.

## 5. Front-end design

### 5.1 `console/static/functions.js`

- New module state `fnFixtures = [] // {name, dev, muted}` in profile
  order, fed from `snapshot.room` and `room_changed` (`room` null ->
  `[]`).
- **Rendering discipline:** `room_changed` fires on every controller-value
  change. The handler computes a signature of `(name, dev, muted)` per
  fixture and refills pickers ONLY when it differs from the last one. A
  controllers-only `room_changed` never touches a picker (an open
  `<select>` would otherwise close under the operator).
- `fillDevicePicker(picker, withRoom)` with `withRoom`:
  1. `All` (`@all`), as today;
  2. one row per fixture: value `@fixture:<name>`, label
     `<name> (<dev>)` when bound, `<name> (unbound)` when not, with
     ` (muted)` appended when muted;
  3. every device from `fnDevices` that is NOT bound to a fixture
     (`!d.fixture`), labelled as today.
  Without `withRoom` (DEVICE targets): unchanged.
  The previous selection is preserved when still offered.
- A refill after a fixture-signature change also re-runs
  `refreshCardCompatibility` / `refreshDiagButtons`, exactly as
  `onDevicesChanged` does.
- `isCompatible`, `resolvedDescription`, `builtinsFor` need no change: a
  token picker value looks up `surfaceInstruments[token]`, which §3 adds.

### 5.2 `console/static/surface.js`

Each fixture head gets a `chip` reading `muted`, toggled in place via its
`hidden` property on every render. It is NOT part of `bindStateKey`, so a
mute change never rebuilds the head and never discards an armed Release
button's confirm-tap state. When the head IS rebuilt (a bind-state
change), the chip is recreated with the current state. The global
`[hidden] { display: none !important; }` guard in `terrarium.css` already
covers `.chip`'s `display: inline-flex`.

## 6. Error handling

- Unknown or Room-less fixture token: refusal string (§4.3), surfaced by
  the Console as the existing `error` event / `flashRefusal`.
- `is_muted` / `fixture_dev` cannot raise on a declared fixture name; no
  new guard in `_current_room`.
- Front end tolerates `room.fixtures[i].muted` being absent (older server):
  treated as false.

## 7. Testing

Python (`tests/`):
- `fixtures_view` / `room_view` emit `muted` true only for named fixtures;
  default is all false.
- `ConsoleAgent._current_room` reports `muted` after a Stop fired at an
  unbound fixture token, and clears after a non-mute fire.
- `_current_surface_instruments` carries a token key per declared fixture,
  bound and unbound.
- `fire_function` SURFACE at a bound fixture token: `FunctionFired.devs`
  is the bound dev. At an unbound token: devs is the token; Stop latches
  `is_muted(token)`. Unknown token and no-Room token: refusal strings.

JS (`tests/js/*.test.js`, run by `tests/test_console_js.py`):
- SURFACE picker rows: All, fixtures in order with bound/unbound/muted
  labels, then only non-fixture devices. DEVICE picker unchanged.
- A controllers-only `room_changed` does not refill the picker (option
  node identity survives); a mute change does.
- Fire is enabled for an unbound fixture whose instrument has the
  function, and sends `dev: "@fixture:<name>"`.
- Room panel: the mute chip toggles `hidden` on a mute change while the
  armed Release button's DOM node survives.

Suite: `.venv/bin/python -m pytest tests -q` green.

## 8. Docs

Update the deep-dive entry's "Bring-up prerequisite: the Console cannot
target or show the mute state of an unbound fixture" bullet to say it is
closed, and add a short entry for this slice with its test baseline.
