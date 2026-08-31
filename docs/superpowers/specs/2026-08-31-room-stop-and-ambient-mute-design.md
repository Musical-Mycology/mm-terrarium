# Room stop and ambient mute: making the Console's Unload button actually stop sound

Found live, running `python -m harness.run_stack --open --devices 1` against a
real Arco: clicking Unload on the Console's Room card while TestBit was
RUNNING did nothing at all -- the drone kept playing. Root cause turned out to
be two separate, compounding behaviors, both confirmed by reading the code and
its existing tests, not by guessing.

Baseline: **1669 passed, 1 skipped**, fully offline (`.venv/bin/python -m
pytest tests -v`; a fresh worktree needs `ln -s
/Users/chris/projects/mm-terrarium/.venv .venv` first -- see
`docs/MM_TERRARIUM.md` "Landed subsystems").

## 1. What's actually happening today

**The Unload button can't do the one thing it's for.**
`console/static/rooms.js:98-103` always sends `unload_room` with
`{ force: false }`. `Terrarium.unload_room()` (`control/terrarium.py:363`)
refuses outright -- tears nothing down -- whenever the Bit isn't `IDLE`:

```python
if self.gs.state != State.IDLE:
    if not force:
        return (f"cannot unload: Bit is {self.gs.state}, not IDLE "
                "(pass force to abort it)")
```

TestBit RUNNING is exactly that state, so every click is refused. The
refusal comes back as an `error_event` to the clicking client only
(`console/agent.py:117-123`), and `rooms.js` never listens for an `"error"`
event -- only `room_load_progress`/`room_loaded`/`room_load_failed`/
`room_unloaded` -- so the click silently no-ops. Pinned by
`tests/test_terrarium.py:185` `test_unload_room_requires_bit_idle_unless_force`.

**Once a Bit does stop, ambient resumes with no operator action at all.**
`devicelink/agent.py:213` `_setup_room()` unconditionally falls back to the
Room's declared `[instruments.<name>.ambient]` session (Aurora light + the
`flsyn` drone, from `terrarium.toml:11-15`) any time no Bit's `ROOM` role is
present. This is deliberate, tested behavior --
`tests/test_devicelink_agent.py:906` `test_unload_bit_swaps_back_to_ambient`
-- not a bug on its own. But it means the Bit panel's Abort button (Room
stays loaded, Bit stops) causes ambient to swap in on the very next tick,
with zero further operator action -- which reads as "it just started playing
again on its own."

A force-Unload doesn't hit this second path: `unload_room()` tears the whole
Room down synchronously in one call (`room_stack.close()`, `room = None`,
state -> `NO_ROOM`), so there's no tick in between where ambient could
audibly start. Nothing plays after a force-Unload until the next Load.

## 2. Scope decided with the user

- The "stay silent" state lasts **until the next Load** of that Room, not
  across the whole process lifetime and not to disk. A fresh Load always
  starts from "ambient allowed" -- this matches how `Terrarium`'s state
  already resets on `load_room()`, and needs no new persistence.
- It is set by **two** operator actions: a force-Unload (aborting a RUNNING
  Bit to tear the Room down) and the Bit panel's **Abort** button (Room stays
  loaded, Bit stops). Both are "I told it to stop." A Bit completing on its
  own (e.g. TestBit's timed auto-completion) does *not* set it -- nothing was
  explicitly requested there, so ambient resumes exactly as it does today.
- The Unload button itself gains no new UI element -- its existing
  `wire.confirmTap` "Confirm unload?" step already gates the destructive
  action; on confirm it now sends `force: true` instead of `force: false`.
  Harmless when the Bit is already `IDLE` (force is a no-op then).

## 3. Mechanism

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

No new teardown path is needed. In both writer sites a Bit currently holds
the `ROOM` role at the moment the flag flips, so ambient was never actually
running when muted -- this only ever prevents a *future* tick from starting
it.

One more writer, for clearing: `Terrarium.load_room()` (`control/terrarium.py:262`)
resets `self.gs.ambient_muted = False` alongside its existing
`self.gs.provenance = {...}` assignment (`control/terrarium.py:344`), so
every fresh Load starts unmuted regardless of how the Room was last left.

## 4. Console UI

- `console/agent.py`'s `_rooms_view()` (`console/agent.py:214-229`) gains an
  `"ambient_muted": name == active_name and gs.ambient_muted` field per room.
- That view is currently sent only inside the initial `snapshot()` -- there's
  no live re-broadcast when the rooms list changes; only the singular
  `room` detail view gets one, every tick, via `_broadcast_room_if_changed()`
  (`console/agent.py:258-262`). Without a live path, a client with the
  Console already open would stay showing "Active" after an Abort until
  reload. Add a mirrored `_broadcast_rooms_if_changed()`, called from the
  same per-tick spot as `_broadcast_room_if_changed()`, diffing against a
  cached last-rooms-view and broadcasting a new `rooms_changed` event
  (`protocol.rooms_changed_event(rooms)`, same shape as the `rooms` field
  in `snapshot_event`) when it differs.
- `console/static/rooms.js`: add `wire.on("rooms_changed", (m) =>
  onRoomsChanged(m.rooms))` -- reuses the existing `onRoomsChanged()` path,
  which already dedupes via a JSON signature before re-rendering, so this is
  one more subscription, not new render logic.
- `statusLineText()` in `rooms.js`: when `room.active && room.ambient_muted`,
  show `"Active — silenced"` (or similar) instead of bare `"Active"`, so a
  muted Room reads as intentional rather than broken.
- The Unload button's `onclick` (`console/static/rooms.js:100-103`) changes
  its `wire.send("unload_room", { force: false }, unloadBtn)` to
  `{ force: true }`. No other protocol change needed here -- `force` is
  already a field `UnloadRoomCommand` parses and `Terrarium.unload_room()`
  already accepts.

## 5. Testing

- `tests/test_terrarium.py`: `unload_room(force=True)` while the Bit is
  RUNNING sets `gs.ambient_muted`; a plain successful unload (Bit already
  IDLE, no force needed) does not; `load_room()` clears a pre-existing mute.
- `tests/test_console_agent.py`: `AbortCommand` sets `gs.ambient_muted`;
  `_rooms_view()` reports it for the active room and `False` for every other
  configured room; a mute flip broadcasts a `rooms_changed` event.
- `tests/test_devicelink_agent.py`: a sibling to
  `test_unload_bit_swaps_back_to_ambient` asserting no ambient session or
  drone starts when `gs.ambient_muted` is set, using the same assertions the
  existing empty-manifest test already uses.
- JS-side coverage for the button's `force: true` change and the "silenced"
  status text, matching however this repo currently tests `rooms.js` (see
  its existing test file, if any, before adding a new harness for this).

## 6. Alternatives considered and rejected

- **A separate "Force Stop" button distinct from Unload** -- rejected by the
  user in favor of reusing Unload's existing confirm-tap step, since a plain
  Unload already succeeds today whenever the Bit is IDLE; `force: true`
  after confirm is a strict superset of that, not a behavior change for the
  already-safe case.
- **Muting only on force-Unload, not on Abort** -- considered first, but
  force-Unload already leaves nothing playing until the next Load, so a mute
  flag scoped to only that trigger would never observably do anything. The
  path that actually reproduces "it started playing again on its own" is
  Abort (Room stays loaded, ambient swaps in with no further click), so
  Abort has to be a trigger too for the fix to have any effect.
- **Persisting the mute to disk / across process restarts** -- rejected as
  unneeded scope for what the user asked for (an in-session "stop means
  stop" fix); would require hooking into `RoomBindingRegistry`'s existing
  disk store for a case that wasn't reported.
