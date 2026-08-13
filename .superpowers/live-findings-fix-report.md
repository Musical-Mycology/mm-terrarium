# Live-demo findings: fix report

Two bugs found during a live demo run of the mm-terrarium venue server, both
fixed. Suite: 621 passed, 1 skipped (was 614 passed, 1 skipped; +7 new tests,
zero failures, zero errors).

## Finding 1: `AudioBridge.tick()` was never called

Verified by grep across `devicelink/` and `harness/`: `DeviceLinkAgent` only
ever called `on_grant`, `start_drone`, `stop_drone` on `_room_audio`; the
driver loop in `harness/terrarium_boot.py` never touched it either. Confirmed
`AudioBridge.tick()` (`control/audio.py:178`) really does both jobs its
docstring claims: expire `_pending_offs` and call `self._pool.poll()`.

**Fix — `devicelink/agent.py`.** Added `DeviceLinkAgent._tick_audio()`,
called from `poll()` after `_render_room()`. Guards `_room_audio is None`
(the websocket-path/test-default case) and wraps the call in `try/except`
so an audio failure cannot escape into the engine tick (boundary rule 2,
same shape as the existing sinks in `_render_room`/`_render_frames`).

**Clock decision.** `_tick_audio()` calls `self._room_audio.tick(now=self._clock())`
rather than letting `AudioBridge.tick()` fall back to its own clock. Reason:
reading `harness/terrarium_boot.py`'s `build()`, the default (`room_audio=None`)
branch was constructing `AudioBridge(pool)` with **no** `clock=` at all —
silently defaulting to `time.monotonic` regardless of what clock `build()`
itself was given. On the o2lite path, `DeviceLinkAgent` ticks on
`o2lite.time_get` (per `a7943a0`) while that stray `AudioBridge` would have
kept `time.monotonic` — two clocks measuring completely different things,
exactly the shape of the frame-timing bug `a7943a0` fixed, just for welcome
cues instead of frames. Passing `now=self._clock()` explicitly makes the
*tick's* notion of "now" agree with the rest of the driver loop regardless
of how `room_audio` was built, but that's only half the fix: the *due time*
is set in `AudioBridge.on_grant()`/`_play_welcome()` against `AudioBridge`'s
own `self._clock`, so the two clocks still had to agree for a welcome cue to
ever expire. So I also changed `harness/terrarium_boot.py`'s default
construction to `AudioBridge(pool, clock=clock)`, threading through the
same `clock` parameter `build()` already accepts and already threads into
`DeviceLinkAgent`. This isn't new plumbing — `harness/led_smoke.py` already
does exactly this (`AudioBridge(pool, clock=clock)`) for its own standalone
loop; `terrarium_boot.py`'s default branch was just the one place that
pattern hadn't been applied. This one-line change is **not** part of the
clock wiring `a7943a0`/`2bd5360` touched (checked via `git show` on both —
neither commit touches the `room_audio = AudioBridge(pool)` line), so it
doesn't violate the "don't touch that clock wiring" constraint; it extends
the same already-existing `clock` parameter to a construction site those
commits left unfixed.

**Tests added (`tests/test_devicelink_agent.py`):**
- `test_poll_ticks_the_room_audio_bridge` — `agent.poll()` increments the
  injected `FakePool.polls` counter (was 0 before, 1 after).
- `test_poll_releases_a_pending_welcome_cue_after_elapsed_time` — pins the
  leak. Grants `TestBit`'s `player` role (the one that declares
  `welcome.audio`, since `TestBit`'s room role doesn't) directly against
  the agent's own `AudioBridge`, then drives `agent.poll()` across a
  hand-advanced shared clock: not released before the 1.5s welcome
  duration elapses, released after. Confirmed red without the fix (`poll()`
  never calling `tick()` at all, so the voice was never freed).
- `test_poll_with_no_room_audio_injected_does_not_raise` and
  `test_poll_survives_a_raising_audio_tick` (via a small `_RaisingAudioBridge`
  double) — the guard/boundary-rule-2 cases.
- `tests/test_terrarium_boot.py::test_build_threads_its_clock_into_the_default_room_audio` —
  monkeypatches `harness.arco_synth.ArcoSynthPool` and `control.audio.AudioBridge`
  so the `room_audio=None` branch of `build()` can be exercised with no real
  pyarco/Arco, and asserts the clock `build()` was given reaches `AudioBridge`.
  Confirmed red by temporarily reverting the one-line fix and re-running.

## Finding 2: `harness/o2_shroom.py` raced itself on startup

Confirmed the trap as described: `main()` sends the join via `o2lite.send_cmd`
(TCP) but gestures via `o2lite.send` (UDP-default), so UDP could overtake TCP
and the first tilt could reach Control before `GameServer.data()` had
processed the join — hence the spurious `tilt: device not registered`.
Verified in `harness/shroom_client.py` that `ShroomClient._on_role()`
(`shroom_client.py:146`) really is the only place `self.config` is set —
confirmed by reading, not assumed.

**Fix — `harness/o2_shroom.py`.** Added a small gating helper:

```python
def _gestures_ready(client) -> bool:
    return client.config is not None
```

`main()`'s loop now only sends a tilt when
`not args.no_join and _gestures_ready(client)`. `--no-join` is unaffected:
`not args.no_join` short-circuits first, so a `--no-join` run never even
calls the gate and never waits on a role that will never arrive (that mode
never joins at all). The loop still polls o2lite and ticks the LED queue
every iteration regardless of gate state.

`next_tilt` is now deferred (`None`) until the gate first opens, then set to
the arrival moment rather than backdated to loop start — avoids a burst of
"overdue" tilts firing back-to-back the instant a role finally lands.

**Deny handling.** The existing fix already prints `JOIN DENIED: ...` once.
Since a denied join can never receive a role, `_gestures_ready()` can never
become true, so I added a `break` right after the deny is printed — the loop
no longer spins forever polling o2lite for a role that will never come; it
exits with the reason already explained.

**Test added (`tests/test_o2_shroom.py`):** a bare `_FakeClient` with just a
`.config` attribute (no socket, no `ShroomClient`) drives `_gestures_ready()`
directly: `False` with `config=None`, `True` once `config` is a dict.

## Brief-vs-repo discrepancies found

None. Every citation in the brief (docstring wording, `_on_grant`/`_play_welcome`
line shapes, `DeviceLinkAgent.poll()`'s existing `_feed_breath`/`_render_frames`/
`_render_room` calls, `FakeVoice`/`FakePool` in `control/audio.py`,
`ShroomClient._on_role` setting `self.config`, `send_cmd`'s TCP default vs
`send`'s UDP default) checked out exactly as described against the actual
files.

## Note: concurrent activity in this worktree

While this task was in progress, an external process committed directly to
this branch (`388bc20 docs(terrarium): record the live o2lite run, its
findings, and the fake rule`), landing between my two commits. It touches
`docs/MM_TERRARIUM.md` only, documents the *already-committed* `a7943a0`/
`2bd5360` fixes (not the two findings in this report), and does not conflict
with anything I changed — `git diff HEAD --stat` is clean and the suite is
green post-commit. Also present but untouched: an untracked `o2debug.log`
(evidently the live Arco run's own log output) and an untracked
`.superpowers/terrarium-boot-flags-report.md` from an earlier task in this
worktree. Worth knowing this worktree isn't exclusively mine during this
session, even though it caused no problems here.

## Commits

- `a8c86e1` — `fix(terrarium): tick AudioBridge from DeviceLinkAgent.poll()`
- `80f86b0` — `fix(terrarium): hold o2_shroom's gestures until the role arrives`

## Files touched

- `devicelink/agent.py` — `_tick_audio()`, called from `poll()`
- `harness/terrarium_boot.py` — thread `clock=clock` into the default
  `room_audio`'s `AudioBridge`; docstring update
- `harness/o2_shroom.py` — `_gestures_ready()`, gated tilt emission, deferred
  `next_tilt`, break-on-deny
- `tests/test_devicelink_agent.py` — 4 new tests
- `tests/test_terrarium_boot.py` — 1 new test
- `tests/test_o2_shroom.py` — 2 new tests
