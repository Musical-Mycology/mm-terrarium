# A player's mute carries over when the device binds to a Room fixture

Follow-up to
[`2026-09-23-artnet-fixture-sink-design.md`](2026-09-23-artnet-fixture-sink-design.md)
section 8.3 (mute-key canonicalization). Fixes a pre-existing mismatch found
by the final review of the Console fixture-targets branch
(`claude/console-fixture-targets`, 2026-09-25). Design approved by Chris
2026-09-25, with the choice "carry over on bind".

## 1. What the code does today (verified on `main@60c8788`)

- `GameServer.muted` (`control/engine.py`) and `DeviceLinkAgent._muted`
  (`devicelink/agent.py`) are meant to hold one canonical spelling per
  surface: `@fixture:<name>` for a Room fixture (`GameServer._mute_key`,
  `DeviceLinkAgent._fixture_key`), the raw dev id for a player.
- **Defect.** A device `ie1` muted while it is still a plain player is
  latched under its raw id on both sides: `"ie1"` in `GameServer.muted`,
  and `"ie1"` in the agent's `_muted` and `_overrides` (a blackout). When
  `ie1` later taps in as a Room fixture, `GameServer._bind_room` sets
  `room.bound[fixture] = "ie1"` and leaves the raw entries where they are.
  From then on every canonical lookup resolves to `@fixture:<name>`, which
  is not latched:
  - `is_muted("ie1")` and `is_muted(fixture_dev(name))` read False, so the
    Console Room panel shows the fixture unmuted;
  - PlayCues (`_dispatch_cues`, gated on `is_muted`) and light cues
    (`_on_light_cue`, gated on `_fixture_key(dev) in _muted`) go through;
  - but `_feed_breath` checks the raw `dev in self._muted`, so breath on
    `ie1`'s bridge stays suppressed, and the stale `_overrides["ie1"]`
    blackout is still applied to `ie1`'s player-bridge frame in
    `_render_frames`.
  The Console and the device disagree until the next non-mute fire at the
  fixture, which clears both spellings (`GameServer._clear_mutes` and the
  agent's unmute branch each discard the raw and the canonical key).
- **Mirror defect (found while tracing this one).** A device that joined as
  a player and then bound to a fixture keeps its player bridge in
  `DeviceLinkAgent.bridges`: the ROOM join carries no role config, so
  `_on_join` fails to build a new bridge and returns, leaving the old one
  (verified by a probe on 2026-09-25). Muting the fixture stores
  `@fixture:<name>`; `_feed_breath` checks the raw bound dev, misses it,
  and keeps feeding breath to that bridge. A device that was never a
  player has no bridge, so `_feed_breath` never sees it. The stale bridge
  itself is removed by
  [`2026-09-25-lobby-flash-mute-and-room-bridge-design.md`](2026-09-25-lobby-flash-mute-and-room-bridge-design.md).

## 2. Goals and non-goals

Goals:
- After a bind, `GameServer.muted` and `DeviceLinkAgent._muted` hold the
  same single canonical spelling for the bound surface.
- A mute latched on a device while it was a player **carries over** to the
  fixture it binds to: the fixture is muted (dark, silent, cues purged)
  until the next non-mute fire at it, exactly like any other mute. Stop is
  the panic button, and only a non-mute fire un-latches (`2026-08-26-trigger-cards-and-surface-triggers-design.md`
  section 4); a bind is not a fire.
- Every agent-side mute read resolves through `_fixture_key`, so breath is
  suppressed for a muted fixture's bound device and not for an unmuted one.

Non-goals:
- Unbind. `room.bound` entries are only removed wholesale at Room unload,
  which already clears every mute.
- The Terrarium fast path (`control/terrarium.py` `_bind_room_fast_path`)
  and tests that write `room.bound` directly. The fast path runs at Room
  load, when `GameServer.muted` is always empty.
- The Console's `ArmRoomCommand` not refusing an `[[artnet]]`-covered
  fixture. Documented separately in the deep-dive; different file, no
  shared code.
- Removing the existing "discard both spellings" code in
  `GameServer._clear_mutes` and the agent's unmute branch. It stays as a
  safety net for the direct-write paths above.

## 3. Design

### 3.1 Engine: migrate on bind (`control/engine.py`)

`GameServer._bind_room(dev)`, after `self.room.bound[fixture] = dev`, calls
a new `_migrate_mute_on_bind(dev, fixture)`:

- If the raw `dev` is not in `self.muted`, do nothing (no calls).
- Otherwise: `self.muted.discard(dev)`, `self.muted.add(fixture_dev(fixture))`,
  then, if `on_mute_change` is set, call it twice through the existing seam:
  1. `on_mute_change(dev, False)`: the agent's unmute branch discards both
     `_fixture_key(dev)` (now the token) and the raw `dev` from `_muted`
     and `_overrides`, dropping the stale raw blackout.
  2. `on_mute_change(fixture_dev(fixture), True)`: the agent's mute branch
     latches the fixture properly: `_muted` and a blackout override under
     the token, queued light and room cues for the fixture purged, the
     fixture's Room voice silenced.

  Order matters: the unmute has to come first, because its "discard both
  spellings" would otherwise also discard a token latched just before it.
  If the fixture was already muted by its own token, the pair re-latches
  it: same end state, one extra purge and silence, which is harmless.

`_bind_room` already notifies `on_devices_change` after binding, so the
Console sees the fixture's `muted` flag without a separate notify. No new
agent API: the agent learns about the migration only through
`on_mute_change`, the seam it already handles.

### 3.2 Agent: canonical reads (`devicelink/agent.py`)

Every raw `dev in self._muted` read resolves through `_fixture_key` first,
matching `_on_light_cue`:

- `_feed_breath`: `if self._fixture_key(dev) in self._muted: continue`.
  This fixes the mirror defect.
- The lobby's `send_play` sink (`_lobby_sinks`): `if self._fixture_key(dev)
  not in self._muted`. No behavior change for a player (its key is its own
  id); a bound fixture device now honors its fixture's mute.

`_drain_light_cues` already reads canonical payloads (`_on_light_cue` keys
them by `_fixture_key` before pushing), and the flash sentinel carries a
fixture token, so neither changes. `feed_light` in `_lobby_sinks` already
checks `fixture_dev(name)`.

### 3.3 Docstrings

- `GameServer.muted` comment and `_clear_mutes` docstring: raw-spelling
  entries for a bound dev no longer arise through `_bind_room`; the both-
  spellings discard remains for direct `room.bound` writes.
- `_on_mute_change` unmute-branch comment: same note, plus that
  `_migrate_mute_on_bind` relies on it.
- `_feed_breath` and the `_muted` field comment: keyed by `_fixture_key`.

## 4. Error handling

Corrected after the final review: the existing call sites are already
guarded, just not identically. `_clear_mutes` wraps its own `on_mute_change`
call in a per-call try/except (`logger.exception("on_mute_change failed for
%s", d)`); `_dispatch_cues` guards the whole per-cue block rather than the
sink call alone. The migration now follows `_clear_mutes`'s pattern: each of
its two `on_mute_change` calls (unmute the raw dev, mute the token) is
wrapped in its own try/except, so a raising unmute cannot stop the mute
call that follows it. The agent's `_on_mute_change` also already guards its
only fallible step (the Room-voice silence) so a failure cannot reach the
engine tick (boundary rule 2). The migration mutates `self.muted` before
calling out, so the engine's state is correct even if no transport is
attached.

## 5. Testing (TDD: each test written and seen failing first)

`tests/test_engine_functions.py`:
- **Carry-over through the real bind path.** A running GameServer with an
  armed Room binding; `MuteCue("ie1")` while unbound; `ie1` joins the armed
  ROOM node (`_bind_room` runs). The engine-test harness (`_running`) has
  no ROOM role, so these call `_bind_room("ie1")` directly after arming;
  the real `join` path is covered end to end by the agent tests below. Then `gs.muted == {fixture_dev(name)}`,
  `is_muted("ie1")` and `is_muted(fixture_dev(name))` are both True, and
  the recorded `on_mute_change` calls after the mute are exactly
  `[("ie1", False), (fixture_dev(name), True)]`.
- **No mute, no calls.** The same bind with nothing muted makes no
  `on_mute_change` call and leaves `gs.muted` empty.
- **Carry-over then clear.** After the carry-over, a non-mute fire at the
  fixture clears it: `gs.muted` is empty.

`tests/test_devicelink_agent.py`:
- **The defect, end to end.** GameServer plus agent; player `ie1` joined
  (has a bridge) and muted via `MuteCue("ie1")`; `ie1` then binds through
  `_bind_room`. The agent's `_muted` is `{fixture_dev(name)}` with no raw
  `"ie1"`, `_overrides` has the token's blackout and no `"ie1"` entry, and
  `_feed_breath` skips `ie1` (the Console and the device agree: both
  muted). After `gs._clear_mutes(["ie1"])` breath feeds `ie1` again.
- **The mirror defect.** A bound `ie1` with a bridge; `MuteCue` at the
  fixture token; `_feed_breath` does not feed `ie1`'s bridge session.
- **Unmuted bound device still breathes.** A bound `ie1` with no mute is
  fed breath (guards against over-suppression from the `_fixture_key`
  change).

Suite: from the worktree, `/Users/chris/projects/mm-terrarium/.venv/bin/python
-m pytest tests -q`; baseline before this change is 2673 passed, 1 skipped.

## 6. Docs

Update the deep-dive (`docs/MM_TERRARIUM.md`) entry *`devicelink/artnet_sink.py`,
`[[artnet]]`, routing by fixture name, native RGBW (2026-09-23)*: note that a
player's mute now carries over on bind (one canonical spelling on both
sides), that `_feed_breath` reads through `_fixture_key`, and record the new
test baseline.
