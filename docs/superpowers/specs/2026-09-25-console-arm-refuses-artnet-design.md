# Console refuses to arm an `[[artnet]]`-covered fixture

Status: approved design, 2026-09-25. Base: `main` at `c14c5e1` (PR #143,
no simulator for `[[artnet]]` fixtures, and PR #144, Console fixture targets
and fixture mute state, are both merged).

## 1. Problem

`docs/MM_TERRARIUM.md`'s *`devicelink/artnet_sink.py`, `[[artnet]]`,
routing by fixture name, native RGBW (2026-09-23)* entry lists two bring-up
prerequisites. The first (no simulator for a covered fixture) closed with
PR #143. The second (the Console cannot target or show the mute state of an
unbound fixture) closed its targeting and mute-display half with PR #144.
One gap remains, recorded there as "Still open":

- `console/agent.py`'s `ArmRoomCommand` branch calls
  `gs.room_binding.arm(room_name, command.fixture, ...)` for any fixture
  name. An operator who arms DEMO's `array` and taps a device binds that
  device to an Art-Net fixture, which gives it a `DeviceLinkSink` alongside
  the fixture's `ArtNetFixtureSink`. That contradicts parent spec
  (`2026-09-23-artnet-fixture-sink-design.md`) D2: a covered fixture never
  binds a device.
- The Room panel (`console/static/surface.js` `bindingControls`) offers an
  Arm button on every unbound fixture, and adds the fixture to
  `armedFixtures` before the server answers. A server-only refusal would
  leave a stuck gold "Armed" chip, and `flashRefusal`'s inline note would
  attach to the Confirm button that `render()` has already removed.
- Cosmetic: `_current_surface_instruments` maps every recorded binding's
  dev to its fixture's instrument. `load_room` never reconnects a covered
  fixture's recorded binding (PR #143), but `RoomBindingRegistry.load`
  still reads it from disk, so a stale dev leaks into `surface_instruments`.

## 2. Goal

1. `arm_room` naming a fixture in
   `artnet_fixtures(terrarium.config, room_name)` is refused with a reason
   string, and nothing is armed.
2. The Room panel never offers Arm on a covered fixture. It shows an
   `Art-Net` chip in place of "Not bound" plus Arm.
3. An `arm_room` refusal never leaves a fixture showing "Armed".
4. A covered fixture's stale recorded binding does not appear in
   `surface_instruments`.
5. Bound fixtures, uncovered fixtures, joined devices, and a box with no
   `[[artnet]]` behave exactly as today.

Non-goals: refusing at `RoomBindingRegistry.arm` or `control/terrarium.py`
(the Console is the only operator arm path; `wait_for_room_binding` already
skips covered fixtures); deleting stale records from the bindings file;
any change to the pickers or mute display from PR #144.

## 3. Design

### 3.1 Server refusal (`console/agent.py`)

In `_handle_admin_command`, the `ArmRoomCommand` branch checks coverage
before arming:

```python
if isinstance(command, protocol.ArmRoomCommand):
    if command.fixture in self._artnet_fixtures(room_name):
        return protocol.error_event(
            name, f"{command.fixture} is driven by [[artnet]] and never "
                  f"binds a device; arming refused")
    gs.room_binding.arm(...)
```

A new helper `_artnet_fixtures(room_name) -> frozenset[str]` returns
`artnet_fixtures(self.terrarium.config, room_name)` when a Terrarium is
wired, else `frozenset()`. With no Terrarium there is no config, and so no
coverage, which is today's behavior. `release_room` is unchanged: releasing
a covered fixture is harmless and clears a stale binding.

### 3.2 Wire, additive (`control/room_view.py`, `console/agent.py`)

`fixtures_view` and `room_view` take a new keyword `artnet=None`: an
iterable of covered fixture NAMES, the same shape as `muted`. Each row gains
`"artnet": name in artnet_names`. `room_view.py` stays engine-free and
config-free because the caller resolves the names. `_current_room` passes
`self._artnet_fixtures(gs.room.name)`.

An older browser tab ignores the new field and still offers Arm; the
server refusal (3.1) and the rollback (3.3) cover it.

### 3.3 Room panel (`console/static/surface.js`)

- `bindingControls(fixture)`: when `!fixture.dev && fixture.artnet`, render
  one `chip sage` reading `Art-Net` (title
  `Driven by [[artnet]]; never binds a device`) and return. No Arm button.
  A covered fixture with a `dev` cannot happen after PR #143; if it did,
  the existing bound branch (with Release) renders, which lets the operator
  clear it.
- `bindStateKey(fixture)`: after the `dev` check, return `"artnet"` when
  `fixture.artnet`, so a coverage change rebuilds the head.
- Refusal rollback: the Confirm handler records the fixture name in a
  module-level `pendingArm` before sending. `init()` registers
  `wire.on("error", ...)`: when `m.command === "arm_room"` and `pendingArm`
  is set, delete it from `armedFixtures`, clear `pendingArm`, and
  `render()`. `pendingArm` is NOT cleared on `room_changed`: controller
  values make `room_changed` frequent, so clearing there would race the
  refusal. A successful arm produces no event, so `pendingArm` simply lives
  until the next Confirm overwrites it; a late stray `arm_room` error can
  only un-arm that same last-armed fixture, whose window the server refused
  anyway. `render()`'s no-Room reset clears it along with `armedFixtures`.

The refusal message itself is shown by `shell.js`'s existing `error`
handler (`logLine("error", ...)`), unchanged.

### 3.4 Stale binding (`console/agent.py`)

In `_current_surface_instruments`, the loop over
`room_binding.bound_device(...)` skips fixtures in
`self._artnet_fixtures(gs.room.name)`. The `@fixture:<name>` token entry
for a covered fixture stays, so Fire and Diagnostics still work on it.

## 4. Testing

Python (`tests/test_console_agent.py`, `tests/test_room_view.py`), built
on `tests/test_terrarium.py`'s `_config_with_artnet` / `_artnet` and
`make_terrarium`, with DEMO loaded:

- `arm_room` for `array` (covered) returns an error event whose message
  names the fixture and `[[artnet]]`, and `armed_fixture("DEMO")` stays
  None.
- `arm_room` for an uncovered fixture in the same Room still arms.
- The existing no-Terrarium arm test keeps passing unchanged.
- `room.fixtures[i].artnet` is True for `array`, False otherwise;
  `fixtures_view` with no `artnet` argument yields False everywhere.
- A recorded binding for a covered fixture does not appear in
  `surface_instruments`; the `@fixture:array` token does.

JS (`tests/js/fixture_artnet_arm.test.js`, new, picked up by
`tests/test_console_js.py`):

- A covered unbound fixture renders an `Art-Net` chip and no Arm button;
  an uncovered unbound fixture still renders Arm.
- Arm + Confirm on an uncovered fixture shows Armed; an `error` event with
  `command: "arm_room"` rolls it back to Not bound with Arm.
- An `error` for another command leaves Armed alone.

## 5. Docs

`docs/MM_TERRARIUM.md`: mark the "Still open: ArmRoomCommand" bullet closed
in the artnet entry, note prerequisite (2) fully closed there and in the
*Not yet built* "A real-hardware Room backend" bullet, add a short slice
entry, and record the new test baseline.
