# A muted surface stays dark under a SolidCue; a Room join drops the player bridge

Follow-up to
[`2026-09-25-mute-key-bind-migration-design.md`](2026-09-25-mute-key-bind-migration-design.md).
Fixes two pre-existing defects found by that branch's final review
(`claude/interesting-bhaskara-62910e`). This branch stacks on that one and
rebases onto `main` once it merges. Design approved by Chris 2026-09-25.

## 1. What the code does today (verified on the carry-over branch tip `c9d4d79` merged with `main@ed5ec9f`)

### 1.1 A SolidCue un-blacks a muted surface for good

- `DeviceLinkAgent._on_mute_change(dev, True)` latches mute under
  `key = _fixture_key(dev)`. It adds `key` to `_muted` and writes the blackout
  override `_overrides[key] = ((0, 0, 0), 0.0, None)`. That override never
  expires.
- `DeviceLinkAgent._on_solid_cue(dev, ...)` resolves the same key and
  **overwrites** `_overrides[key]` with no mute check. Once the new override's
  duration runs out, `_tick_overrides` deletes the entry, and the blackout
  goes with it. The surface renders lit again while `_muted` still holds
  `key`, the engine still reports it muted, and the Console shows `Muted`.
- Three callers reach `_on_solid_cue`:
  1. **The lobby `set_override` sink** (`_lobby_sinks`), used by
     `LobbyRuntime.on_scored_join` (the green join flash on the joining
     device), `consider_invite` (the white invite flash) and
     `_flash_fixtures` (start feedback on every bound fixture). **Unguarded.**
  2. **The `__flash__` sentinel** in `_drain_light_cues`, queued by
     `_flash_fixtures_now`. Already guarded by a raw `dev in self._muted`
     check. `dev` there is always `fixture_dev(name)`, so the raw check is
     correct today.
  3. **The engine's SolidCue dispatch** (`GameServer._dispatch_cues`).
     **Unguarded.** The engine checks mute for PlayCue only. For LightCue the
     agent drops the cue at `_on_light_cue`. Nobody checks a SolidCue. A
     *trigger fire* is not affected: `fire_function` calls `_clear_mutes`
     before `_dispatch_cues`, so the agent's `_muted` is already clear when a
     fired SolidCue arrives. The exposed path is a Bit's own SolidCue,
     returned from a gesture handler or `Bit.cues()`, which is not a fire.
- Any flash on a muted surface therefore undoes Stop. Stop is the panic
  button, and only a non-mute fire should un-latch it
  (`2026-08-26-trigger-cards-and-surface-triggers-design.md` section 4).
  A concrete case: `ie1` joins as a scored player, gets muted, then binds to
  fixture `main`. The lobby's green join flash for `ie1` is still queued. It
  lands after the bind, resolves to `@fixture:main`, and un-blacks the fixture.

### 1.2 A ROOM join keeps the device's player bridge

- `GameServer.join` grants a ROOM node through `Registration.join`. Its
  `_assign` already **releases** the device's earlier player role (a role
  switch), and then calls `_bind_room`.
- `DeviceLinkAgent._on_join` then always builds a `DeviceBridge`. A ROOM
  grant has no role config (`result.config` is None), so `on_grant` raises.
  The agent logs "building the LightSession for <dev> failed" with a
  traceback and sends the device `/<dev>/error role "could not build light
  session"` **on every Room tap**. `harness/o2_shroom.py` prints that as
  `ERROR from Control`.
- The early return also leaves the device's **player-era bridge** in
  `self.bridges`, even though the engine no longer holds that role. From
  then on `_render_frames` sends that bridge's 36-channel frames to
  `/<dev>/leds`, and `_render_room` sends the fixture's frames to the same
  address. The device gets **two LED streams**. The carry-over branch's
  `_fixture_key` reads in `_feed_breath`/`_render_frames` stop a *muted*
  fixture's stale bridge from breathing or lighting. They do not stop the
  double stream on an unmuted fixture.

## 2. Goals and non-goals

Goals:
- While a surface is muted, no SolidCue from any source (lobby, `__flash__`
  or engine) changes its override. The blackout stays until the mute is
  lifted.
- A non-mute fire still un-latches and then applies its SolidCue, as it does
  today.
- A ROOM join builds no bridge, logs no traceback and sends no `/error`.
- When a device binds to a Room fixture, its player-era bridge and all
  per-device render state are dropped. It gets exactly one LED stream: the
  fixture's.

Non-goals:
- **`Bit.on_leave` for the released player role.** Registration releases
  the player role on a ROOM role switch, but the engine never tells the Bit.
  That is an engine-side gap and gets its own follow-up.
- **What a Room-bound device is told on join.** It gets nothing: no `/role`,
  no `/error`. Its fixture frames start arriving on `/<dev>/leds`. A
  `--join-retry` client re-joins once, gets `deny "no such node"` (the arm
  was used up by the bind) and stops retrying. A dedicated "bound" event
  would be a protocol change, so it is out of scope.
- The fast-path bind (`control/terrarium.py` `_bind_room_fast_path`) does not
  go through `_on_join`. A spawned simulator was never a player, so it has
  no bridge to drop.

## 3. Design

### 3.1 One mute guard at the SolidCue seam

`_on_solid_cue` resolves `key = self._fixture_key(dev)` (it already does)
and returns straight away if `key in self._muted`. No override is written
and no frame is invalidated. That one check covers all three callers in
section 1.1. The `__flash__` branch in `_drain_light_cues` keeps its own
check as a first line of defense, and its comment is updated to name the
new guard as the one that actually matters.

The lobby's `send_play` sink already reads the mute through `_fixture_key`
on the carry-over branch. `feed_light` checks `fixture_dev(name)`, which
already is the key. Neither changes.

Ordering is safe for fires: `GameServer.fire_function` →
`_clear_mutes` → `on_mute_change(d, False)` discards the agent key
*before* `_dispatch_cues` hands over the fire's SolidCue.

### 3.2 A ROOM join drops the player bridge instead of building one

In `_on_join`, after a granted join, if `result.role_class ==
RoleClass.ROOM`:

1. `self._drop_player_bridge(dev)`: a new helper that removes `dev` from
   `bridges`, `_universes`, `_last_frames`, `_pending_at`, `_last_breath`,
   `_breathless`, `_closing` and `_closing_revived`, plus any raw-key entry
   in `_overrides` and `_override_only`. There is no fade and no
   `/<dev>/release`: the device is not leaving, and the fixture's frames take
   over its LEDs on the same tick. There is no `server.drop_dev` either,
   because the connection stays live. `_canvas_urls` and `_muted` are left
   alone. Raw mute entries are the engine migration's job, which already
   sends `(dev, False)` then `(token, True)` on bind.
2. `self._lobby.forget(dev)` when a lobby is running, same as a player join.
3. Return. No `DeviceBridge`, no `/role`, no `/error`.

The existing `on_grant` failure path (a *player* join whose bridge fails)
is unchanged and still sends `/error`.

Later lifecycle: an engine release of a Room-bound device reaches
`_on_release` with no bridge and takes the existing immediate-release
branch. Room unload (`unwire_room`) already clears per-fixture state.

The carry-over branch's `_fixture_key` reads in `_feed_breath` and
`_render_frames` stay. They are still correct for any bridge-holding dev,
and cost nothing.

## 4. Testing

Offline, in `tests/test_devicelink_agent.py` and `tests/test_lobby_agent.py`,
test-first:

- **Lobby flash on a muted fixture stays dark.** Mute `@fixture:main`, run
  the lobby `set_override` sink for its bound dev with a short duration,
  advance past it, and check that `_overrides[@fixture:main]` is still the
  latched blackout and the rendered fixture frame is black.
- **`_flash_fixtures_now` on a muted fixture** (the `__flash__` path): same
  result.
- **A Bit SolidCue on a muted surface** through `gs._dispatch_cues([SolidCue(...)])`
  is dropped for a fixture and for a player.
- **A non-mute fire still un-latches** and its SolidCue then applies.
- **The end-to-end case** from section 1.1: `_player_then_room(mute_as_player=True)`
  with the lobby left running, polled past every ceremony flash. The fixture
  frame stays black.
- **A ROOM join**: `_player_then_room` leaves `"ie1"` out of `agent.bridges`,
  records no `/ie1/error` and no `/ie1/role` for the ROOM join, and sends no
  36-channel `/ie1/leds` frame after the bind (one stream only).
- **A never-a-player device's ROOM join** also sends no `/error`.
- **Update the carry-over tests** that relied on the stale bridge.
  `_player_then_room` now asserts `"ie1" not in agent.bridges`. The breath
  tests assert that nothing is fed at all. The two "blacks the stale player
  bridge" tests become "no player frame at all". Assertions about mute state
  stay.

Full suite: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests -q`
from the worktree. Baseline after merging the carry-over branch and
`main@ed5ec9f`: **2747 passed, 1 skipped**.

## 5. Docs

- `docs/MM_TERRARIUM.md`: a new entry for this slice, the new test baseline,
  and the carry-over entry's "keeps its player bridge" wording corrected.
- The carry-over spec's section 1 mirror-defect bullet gets a pointer to
  this spec.
