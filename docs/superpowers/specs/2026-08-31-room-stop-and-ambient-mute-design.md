# Room stop and ambient mute: making the Console's Unload button actually stop sound

Found live, running `python -m harness.run_stack --open --devices 1` against a
real Arco: clicking Unload on the Console's Room card while TestBit was
RUNNING did nothing at all -- the drone kept playing. Root cause turned out to
be two separate, compounding behaviors, both confirmed by reading the code and
its existing tests, not by guessing.

**Status (revised 2026-08-31, before implementation): sections 2-4's
`ambient_muted` mechanism is SUPERSEDED.** It was designed against
`devicelink/agent.py`'s Room-level ambient fallback (DEMO room's
`venue_array` instrument, which declares `[instruments.venue_array.ambient]`
in `terrarium.toml`). But the live session that reported "Aurora starts
playing again" ran with plain defaults -- TestBit's manifest
(`bits/test/bit.toml:11`) defaults to the **TEST** room, and TEST's only
fixture instrument is `dev_strip`, which declares **no ambient at all**
(`terrarium.toml:17`, "Simulated dev strip, no ambient"). So the mechanism
this spec built never fires in the reported session. What's actually
restarting is one of TestBit's own per-instrument light declarations
(`player`/`jammer` role visuals or the `play_aurora` Function -- see
`bits/test/test_bit.py:92,130,221-234`), a different subsystem. Fixing that
is now scoped into a separate, larger redesign (instrument-typed
Triggers/Functions, Room-as-instrument-collection, a Console
target-dropdown that filters by instrument compatibility, plus three
required cross-instrument-type Functions -- Flash/Stop/Ping -- for
control-panel testing/troubleshooting on any instrument) to be brainstormed
on its own. Sections 2-4 below are kept for the record, not implemented.

**Also corrected:** section 1's original claim that a refused Unload's error
was silently swallowed by the UI was **wrong**. `console/static/shell.js:35-38`
already wires a global `wire.on("error", ...)` handler that calls
`wire.flashRefusal(m.command, m.message)` (flashes the button red with an
inline message) and logs it to the rail. The refusal *is* visible today --
it just doesn't fix the actual problem: the Bit keeps RUNNING and the drone
keeps sounding. **Only the fix in section "Remaining scope" below is still
live.**

Baseline: **1669 passed, 1 skipped**, fully offline (`.venv/bin/python -m
pytest tests -v`; a fresh worktree needs `ln -s
/Users/chris/projects/mm-terrarium/.venv .venv` first -- see
`docs/MM_TERRARIUM.md` "Landed subsystems").

## Remaining scope: force the Unload button to actually unload

`console/static/rooms.js:98-103` always sends `unload_room` with
`{ force: false }`. `Terrarium.unload_room()` (`control/terrarium.py:363`)
refuses outright -- tears nothing down -- whenever the Bit isn't `IDLE`:

```python
if self.gs.state != State.IDLE:
    if not force:
        return (f"cannot unload: Bit is {self.gs.state}, not IDLE "
                "(pass force to abort it)")
    self.gs.abort()
```

TestBit RUNNING is exactly that state, so every click is refused -- visibly
(see the corrected claim above), but the Room, and its sound, never actually
stop. Pinned by `tests/test_terrarium.py:185`
`test_unload_room_requires_bit_idle_unless_force`.

**Fix:** the Unload button's existing `wire.confirmTap` "Confirm unload?"
step already gates the destructive action (`console/static/rooms.js:100-103`).
On confirm, it changes `wire.send("unload_room", { force: false }, unloadBtn)`
to `{ force: true }`. Harmless when the Bit is already `IDLE` (force is a
no-op then, per the `if self.gs.state != State.IDLE` guard above). No other
protocol or backend change needed -- `force` is already a field
`UnloadRoomCommand` parses and `Terrarium.unload_room()` already accepts and
acts on.

**Testing:** `tests/js/rooms_panel.test.js:103` currently asserts
`{ command: "unload_room", force: false }` after the second (confirming)
click on the Unload button -- update it to `force: true`. No other test
changes needed: `Terrarium.unload_room(force=True)`'s behavior is already
covered by `tests/test_terrarium.py:185`
`test_unload_room_requires_bit_idle_unless_force`, and the error-flash path
this spec originally misdiagnosed is pre-existing, already-tested behavior
in `console/static/shell.js` unaffected by this change.

---

## 2. Scope decided with the user (SUPERSEDED -- see Status above)

- The "stay silent" state lasts **until the next Load** of that Room, not
  across the whole process lifetime and not to disk. A fresh Load always
  starts from "ambient allowed" -- this matches how `Terrarium`'s state
  already resets on `load_room()`, and needs no new persistence.
- It is set by **two** operator actions: a force-Unload (aborting a RUNNING
  Bit to tear the Room down) and the Bit panel's **Abort** button (Room stays
  loaded, Bit stops). Both are "I told it to stop." A Bit completing on its
  own (e.g. TestBit's timed auto-completion) does *not* set it -- nothing was
  explicitly requested there, so ambient resumes exactly as it does today.

## 3. Mechanism (SUPERSEDED -- see Status above)

**A new `ambient_muted: bool` field on `GameServer`**, default `False`. Same
pattern already in use for `gs.room`/`gs.provenance`: data `Terrarium`/
`ConsoleAgent` set that `DeviceLinkAgent` reads, with `GameServer` itself
inert about it -- keeps the existing boundary rule that `control/` doesn't
know about rendering.

Two writers:
- `Terrarium.unload_room()` (`control/terrarium.py:363`), in the branch that
  already calls `self.gs.abort()` when `force=True` and the Bit wasn't
  `IDLE` -- set `self.gs.ambient_muted = True` right alongside the existing
  `self.gs.room = None` assignment a few lines down.
- `console/agent.py`'s `AbortCommand` handling (`console/agent.py:141`,
  `self.game_server.abort()`) -- this path never goes through `Terrarium` at
  all, so it sets `self.game_server.ambient_muted = True` directly, right
  after the `abort()` call.

One reader: `devicelink/agent.py:213` `_setup_room()`. The ambient branch
(`role is None`, lines 260-271) already has a precedent early-return for "the
Room declares no ambient light or audio at all" (lines 262-265: empty
manifest -> `self._ambient_generators = None; self._ambient_start = None;
return`, rendering nothing). The mute check folds into that same guard:

```python
if gs.ambient_muted or (not ambient_light and not ambient_ugen):
    self._ambient_generators = None
    self._ambient_start = None
    return
```

One more writer, for clearing: `Terrarium.load_room()` (`control/terrarium.py:262`)
resets `self.gs.ambient_muted = False` alongside its existing
`self.gs.provenance = {...}` assignment (`control/terrarium.py:344`), so
every fresh Load starts unmuted regardless of how the Room was last left.

## 4. Console UI (SUPERSEDED -- see Status above)

- `console/agent.py`'s `_rooms_view()` (`console/agent.py:214-229`) gains an
  `"ambient_muted": name == active_name and gs.ambient_muted` field per room.
- That view is currently sent only inside the initial `snapshot()` -- there's
  no live re-broadcast when the rooms list changes; only the singular
  `room` detail view gets one, every tick, via `_broadcast_room_if_changed()`
  (`console/agent.py:258-262`). Add a mirrored `_broadcast_rooms_if_changed()`
  and a new `rooms_changed` event so a client with the Console already open
  reflects a mute flip without reloading.
- `console/static/rooms.js`: add `wire.on("rooms_changed", (m) =>
  onRoomsChanged(m.rooms))`, and show `"Active — silenced"` in
  `statusLineText()` when `room.active && room.ambient_muted`.

## 5. Alternatives considered and rejected

- **A separate "Force Stop" button distinct from Unload** -- rejected by the
  user in favor of reusing Unload's existing confirm-tap step, since a plain
  Unload already succeeds today whenever the Bit is IDLE; `force: true`
  after confirm is a strict superset of that, not a behavior change for the
  already-safe case. This part of the decision still holds for the
  surviving scope above.
- **Muting only on force-Unload, not on Abort** (superseded along with
  sections 2-4) -- force-Unload already leaves nothing playing until the
  next Load, so a mute flag scoped to only that trigger would never
  observably do anything; the path that actually reproduced "it started
  playing again on its own" was thought to be Abort. Moot now that the
  underlying mechanism turned out to be per-instrument, not Room-level.
- **Persisting the mute to disk / across process restarts** (superseded) --
  rejected as unneeded scope; would have required hooking into
  `RoomBindingRegistry`'s existing disk store for a case that wasn't
  reported.
