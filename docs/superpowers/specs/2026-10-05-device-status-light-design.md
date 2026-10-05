# Device status light: Ready and invite pulses, deny flash, start accept x2

**Date:** 2026-10-05
**Status:** approved design, ready for a plan
**Closes:** gaps G1, G2 and G3 of
[`docs/light-lexicon.md`](../../light-lexicon.md) section 6 (added in
mm-terrarium PR #178). G4 is firmware and out of scope.

## 1. Purpose

The light lexicon (agreed 2026-10-05) is normative; three of its signals
are not rendered by Control today:

| Gap | Lexicon signal | Today | Target |
|---|---|---|---|
| G1 | Invite (between flashes), Ready | a hello'd device with no role gets only override flashes, then one black frame (`DeviceLinkAgent._override_only`): dark between invites and after the validation ceremony | a slow **white** pulse while invited, a slow **green** pulse once validated, until `/<dev>/role` |
| G2 | Failure | `/<dev>/deny` shows no light | **red x2** on the denied device, device flash timing |
| G3 | Start accepted (Room) | green x1 on every bound Room fixture | **green x2** |

## 2. Decision: the lobby owns the device status light

The pulses and the deny flash live in `LobbyRuntime`
(`devicelink/lobby_runtime.py`), next to the invite flash and the join
ceremony, and are gated the same way: they exist only while a lobby runtime
exists (a Room loaded, the Bit's `[lobby] enabled` true, the engine in
SETUP). Every shipped Bit leaves the lobby on, so in production this is
always present. With no lobby (no Room, a lobby-disabled Bit, or RUNNING),
a device gets no pulse and no deny flash, exactly as it gets no invite
flash today.

Rejected alternative: render them in `DeviceLinkAgent` for any pooled
device in SETUP. A no-Room run would then show a pulse with no invite flash
(the flash stays lobby-gated), so the signals would stop lining up, and all
eighteen contract scenarios would change for no device-visible gain.

## 3. The base layer (G1)

### 3.1 Pulse shape

Pure, in `control/lobby.py`:

```python
PULSE_PERIOD_S = 4.0
PULSE_PEAK = 0.35

def pulse_level(t: float) -> float:
    """0 for t <= 0, else a raised cosine from dark: PEAK at T/2, dark at T."""
    if t <= 0:
        return 0.0
    return PULSE_PEAK * (1.0 - math.cos(2.0 * math.pi * t / PULSE_PERIOD_S)) / 2.0
```

The period matches Lux Aeterna's `sys:idle` (4 s). The peak stays well under
a flash's 1.0, so a flash always reads as a flash against a pulse.

### 3.2 Per-device status in LobbyRuntime

`LobbyRuntime` keeps `dev -> _Status(rgb, start)`, where `rgb` is `WHITE` or
`GREEN` and `start` is the clock time the pulse begins. Each `tick()`, for
every status, it computes `pulse_level(now - start)` and calls a new sink:

```python
set_base: Callable[[str, tuple | None, float], None]   # (dev, rgb or None, level)
```

`set_base(dev, None, 0.0)` clears the device's base. The runtime only
calls `set_base` when the value it would send changed since the last call
for that dev (the same de-dupe idea as `_light`), so a static state costs
nothing.

**Flash gaps stay dark.** Every flash train scheduled for a device (invite,
ceremony, deny) pushes that device's `start` to at least the end of the
train's last flash plus `DEVICE_FLASH_GAP_S`. For two flashes beginning at
`t0`, that is `t0 + 2 * (DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S)` =
`t0 + 0.8`. Before `start` the level is 0, so the off-gaps between flashes
are black and the pulse rises from dark afterwards. A helper
`_hold_dark(dev, until)` does `start = max(start, until)`.

### 3.3 Transitions

| Event | Status change |
|---|---|
| `consider_invite` fires an invite train at `now` | set `WHITE` (keep an existing `WHITE`), `_hold_dark(dev, now + 0.8)` |
| `on_scored_join(dev)` (ceremony slot at `at`) | set `GREEN` with `start = at + 0.8`; between validation and its slot (a queued ceremony) the device stays dark |
| `on_deny(dev)` at `now` | see section 4 |
| `set_state(FULL)` | drop every `WHITE` status (clear its base); `GREEN` stays |
| back to `WAITING` | nothing directly; `consider_invite` sets `WHITE` again on the next due invite |
| `forget(dev)` (validation, reap, Room bind) | drop the status (clear its base). On validation the agent calls `forget` then `on_scored_join`, so the device goes `WHITE` to `GREEN` |
| `stop()` (RUNNING, abort, Room unload) | drop every status (clear each base). The queued ceremony still drains (spec 2026-10-01 section 5.5), but no pulse restarts |

At RUNNING every pooled device gets a role, and a device holding a bridge
is skipped by the agent's role-less pass, so the pulse cannot fight a role's
own light.

### 3.4 Agent side

`DeviceLinkAgent` gains `_bases: dict[str, tuple[tuple[int, int, int],
float]]`, filled by the `set_base` sink (a `None` rgb pops the entry).

The role-less pass in `_render_frames` (the loop after the bridges loop)
iterates `set(self._overrides) | self._override_only | set(self._bases)`
with the same eligibility guards it has today (not a fixture token, no
bridge, not bound, no fixture, a known device). Its starting frame is the
base painted in the strip's channel order (`rgb * level`, W stays 0), or
black with no base; `_apply_override` then paints any override on top.
A muted player (`dev in self._muted`) gets black, not its base. The
existing rules stay: a frame equal to the last one sent is skipped, a
frame is stamped `clock() + horizon`, and a black frame with no base and no
override is sent once and the dev drops out of the loop.

The colour-to-bytes painting that `_apply_override` does today is factored
into one helper (`_solid_frame(rgb, level, length, color_order)`) used by
both.

Every place that already forgets a dev's overrides (`_forget_reaped`,
`unwire_room`, `_drop_player_bridge`, `_finish_release`) also pops
`_bases`.

Frame rate: a pulsing device changes level on most ticks, so it gets about
one 36-byte frame per tick, the same rate a role's own animation already
uses.

## 4. Deny flash (G2)

In `DeviceLinkAgent._on_handshake`, after `deny_event` is sent and the
console is notified:

```python
if self._lobby is not None:
    self._lobby.on_deny(dev)
```

`LobbyRuntime.on_deny(dev)`:

1. Purges the dev's queued `_InviteFlash` thunks (the same purge `forget`
   uses, without forgetting the invite schedule), so a deny landing
   mid-invite does not interleave white and red.
2. Queues red x2 from now: `set_override(dev, RED, 1.0, DEVICE_FLASH_ON_S)`
   at `now` and `now + DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S`.
3. If the dev has a `WHITE` status, `_hold_dark(dev, now + 0.8)`: the white
   pulse resumes after the red train (the device is still invited). This is
   the WAITING case, e.g. a `no such node` deny.
4. With no status (a `scored full` deny arrives with the lobby FULL, where
   white statuses were dropped), the device goes black after the red train.

The deny reply itself is unchanged and still goes out first on the wire. A
deny with no lobby (no Room, or RUNNING's `registration closed`) shows no
light; the lexicon row says so.

## 5. Start accepted x2 (G3)

- `LobbyRuntime.feedback`: `FEEDBACK_ACCEPT: (2, GREEN)`.
- `DeviceLinkAgent.on_start_requested`: `self._flash_fixtures_now(GREEN, 2)`.

Timing stays the fixture timing (`FIXTURE_FLASH_ON_S`, `FIXTURE_FLASH_GAP_S`).

## 6. Contract scenarios

Re-recorded with `tools/record_scenarios.py`; `tests/test_contract_scenarios.py`
fails on any diff against the committed recordings.

- `handshake_validate_then_role`: gains white pulse frames after the invite
  train and green pulse frames after the ceremony, until `/role` at 3000.
- `room_node_handshake_binds`: gains white pulse frames between the invite
  train and the Room-node accept at 1000; the bind's `forget` clears it.
- `deny_stays_hellod`: switches to `with_room=True`, so it records the
  invite train, red x2 at the deny (300), and the white pulse resuming,
  with re-invites every 5 s. Its test's "no pixels" assertion becomes:
  red x2 frames start at the deny, and a dim white frame follows.
- New `expect_frame` checkpoints so a device runner actually checks the new
  signals: one on the red flash in `deny_stays_hellod`, one near a green
  pulse peak in `handshake_validate_then_role` (the cosine is flat at its
  peak, so the frame is stable inside the 50 ms frame tolerance).
- Any other scenario whose recording changes on re-record is inspected and
  explained before it is committed; none is expected (no other scenario
  loads a Room, and no other scenario starts a round with Room fixtures
  bound).
- **No `contract_version` bump.** No verb and no `step_schema` key changes;
  only recorded frames do, and a device is a pixel sink for them.

Then the export (device contract guide section 2, module form) is
regenerated into `~/projects/mm-tuneshroom/test/contract/`, committed there
in its own commit on its own branch naming `_provenance.commit`, and
mm-tuneshroom's contract replay tests are run with Flutter. mm-devshroom is
not re-exported by this work.

## 7. Tests

`tests/test_lobby_runtime.py` (recording sinks, fake clock):

- `pulse_level`: 0 at and before 0, `PULSE_PEAK` at T/2, back to ~0 at T.
- an invite sets a white base that is 0 through the invite train and rises
  after `+0.8`.
- validation (`forget` then `on_scored_join`) turns the base green, dark
  until the ceremony's slot `+0.8`, including a queued second ceremony.
- `set_state(FULL)` clears white bases and keeps green ones.
- `stop()` clears every base.
- `on_deny`: purges a pending white invite flash, queues red x2 at device
  timing, and holds the white pulse dark until `+0.8`; with no status, no
  base is set.
- `feedback(FEEDBACK_ACCEPT)` flashes green twice per bound fixture.
- `set_base` is not re-sent when the value is unchanged.

`tests/test_lobby_agent.py` (real agent, fake transport):

- an invited device receives dim white GRB frames between invite flashes,
  not black.
- after validation the device receives dim green frames until `/role`, then
  its role's light.
- a deny sends `/deny`, then red frames in GRB order.
- a muted player gets black, not its base.
- a cleared base sends exactly one black frame.
- the accept start flashes every fixture green twice (replaces the x1
  expectation in `test_accept_flash_is_gated_on_the_lobby_being_enabled`).

## 8. Docs

- `docs/light-lexicon.md`: Invite's *Today* (no longer black between
  flashes), Ready, Failure (note: lobby-gated, no light on a RUNNING deny),
  Start accepted; section 6 marks G1 to G3 done, G4 remains.
- `docs/MM_TERRARIUM.md`: the lobby bullet (pulses, deny flash), the start
  feedback line ("accept green x2"), the deny text under *Lobby and the
  handshake*, the overrides / role-less render notes, and *Not yet built /
  deferred*.
- `docs/team-walkthrough-metronome-bit.md`: the invite step (white flashes
  with a white pulse between), the ceremony step (then a green pulse until
  start), and the start step (fixtures flash green twice).
- `docs/device-contract-guide.md`: rule 4's table row and step list where
  they say nothing changes on the pixels before `/role` (they now show
  Control-rendered status frames; the device still just displays frames).

**Branching.** Everything lands on `claude/device-status-light`, cut from
`main` after PR #178 (the lexicon) merged, so no stacking is needed.

## 9. Out of scope

- G4 (firmware Solo and Looking, link-loss fallback).
- Moving the invite flash out of the lobby.
- Any Room fixture signal other than the accept count.
