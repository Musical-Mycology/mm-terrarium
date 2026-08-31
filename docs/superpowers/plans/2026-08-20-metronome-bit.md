# MetronomeBit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The repo's first production game Bit -- a 100 BPM call-and-response
metronome game for RoomType.DEMO -- plus the simulator input-latency fixes
that make tapping in rhythm from the browser canvas possible.

**Architecture:** One new Bit (`bits/metronome_bit.py`) whose entire game
runs on the existing seams: `cues(at)` emits the beat grid as absolutely
timed cues, `verb_handlers()["tap"]` grades taps in the same `at`-space so
the cue-horizon offset cancels, `TriggerTable` scripts carry the
fireworks/fail/finale choreography, and role capacity caps players at 2.
Three small supporting changes: a new optional `Bit.on_join` hook (a Bit
cannot otherwise learn join order, which round-robin turns need), MIDI
program-change (0xC0) handling in `AudioBridge`, and tap-latency fixes in
`harness/o2_shroom.py` (stamp at enqueue) and luxaeterna's WebSim page
(tap on pointerup, no 250 ms hold).

**Tech Stack:** Python 3 offline suite (pytest, fakes only -- no Arco, no
pyarco, no o2litepy importable), luxaeterna (sibling checkout, editable
install in `.venv`), FluidR3_GM.sf2 GM programs at live-verify time.

**Spec:** `docs/superpowers/specs/2026-08-20-metronome-bit-design.md`

## Global Constraints

- The whole test suite MUST stay runnable with no Arco, no pyarco, and no
  o2litepy importable. Nothing under `control/` or `bits/` may import
  pyarco/o2litepy/luxaeterna at module level.
- Run everything through the project venv: `.venv/bin/python -m pytest`.
  A fresh worktree has no `.venv`; from the worktree root run
  `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first. Never use
  a bare `python3` (collects a phantom import error in
  `tests/test_terrarium_boot.py`).
- Test doubles must never be more permissive than the library they stand
  for (boundary rule 5). Use the existing strict fakes.
- All outbound JSON goes through `control/wire_json.dumps()`; `status()`
  payloads must contain only finite floats or None.
- luxaeterna changes happen in the sibling checkout
  `/Users/chris/projects/luxaeterna` (its own git repo, its own commit).
  mm-terrarium's `.venv` has it as an editable install, so changes are
  picked up immediately.
- Gameplay constants come from the spec verbatim: 100 BPM (beat 0.6 s),
  8-beat cycle (4 call + 4 wait), 4 cycles per run, tolerance 50 ms,
  2-player cap, GM programs 115 (Woodblock) / 38 (Synth Bass 1) /
  89 (Warm Pad), clicks key 76 vel 120 hard / 65 soft, fail tone key 33,
  finale 10 s.

**Two recorded deviations from the spec (amend the spec inline when noted
in Tasks 1 and 5):**
1. The spec says the fail low tone plays "on that player's Arco voice".
   `devicelink/agent.py` gives only the Room an Arco voice (player audio
   over the device wire is a documented deferral), so ALL audio -- clicks,
   fail tone, finale pad -- rides ROOM-targeted cues. `fail_player` is
   light-only.
2. The spec's design listed "no new engine seams". A Bit has no way to
   learn which devs joined (no hook exists; `GameServer.join` notifies
   only observers), and round-robin needs join order, so Task 1 adds an
   optional no-op `Bit.on_join(dev, role_name)` hook -- same shape as the
   `cues(at)` extension precedent.

---

### Task 1: `Bit.on_join` hook

**Files:**
- Modify: `control/bit.py` (add hook after `on_run_start`)
- Modify: `control/engine.py:136-155` (`GameServer.join`, guarded call)
- Test: `tests/test_engine_on_join.py` (create)

**Interfaces:**
- Produces: `Bit.on_join(self, dev: str, role_name: str) -> None` --
  called once per granted non-ROOM join, after the grant is recorded,
  guarded so a raising Bit cannot break `join()`. Default no-op.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_engine_on_join.py"""
from control.engine import GameServer, State
from bits.test_bit import TestBit


class _JoinRecorder(TestBit):
    def __init__(self):
        super().__init__()
        self.joins = []

    def on_join(self, dev, role_name):
        self.joins.append((dev, role_name))


class _RaisingJoin(_JoinRecorder):
    def on_join(self, dev, role_name):
        super().on_join(dev, role_name)
        raise RuntimeError("bit bug")


def _setup(bit_cls):
    gs = GameServer({"TestBit": bit_cls})
    gs.load_bit("TestBit")
    gs.setup()
    return gs


def test_on_join_called_with_dev_and_role_name():
    gs = _setup(_JoinRecorder)
    result = gs.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted
    assert gs.bit.joins == [("ie1", "player")]


def test_on_join_not_called_on_denied_join():
    gs = _setup(_JoinRecorder)
    gs.run()
    result = gs.join("ie1", "TEST_PLAYER_NODE")   # scored role, RUNNING
    assert not result.granted
    assert gs.bit.joins == []


def test_raising_on_join_does_not_break_join():
    gs = _setup(_RaisingJoin)
    result = gs.join("ie1", "TEST_PLAYER_NODE")
    assert result.granted                          # grant survives the raise
    assert gs.bit.joins == [("ie1", "player")]
```

Check the exact `GameServer` constructor/`load_bit`/`setup` signatures
against an existing engine test (e.g. `tests/test_engine.py`) and adjust
the `_setup` helper to match the established pattern -- do not invent new
setup style.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_on_join.py -v`
Expected: FAIL (`on_join` never called / AttributeError absent because the
base class lacks the hook -- the recorder subclass defines it, so failures
show empty `joins` lists).

- [ ] **Step 3: Implement**

In `control/bit.py`, after `on_run_start`:

```python
    def on_join(self, dev: str, role_name: str) -> None:
        """Called once per granted (non-ROOM) join, after the grant is
        recorded. `role_name` is the granted role's name. Default: no-op.
        Guarded by GameServer -- a raising Bit cannot break join()."""
```

In `control/engine.py` `join()`, inside the `if result.granted:` block for
non-ROOM grants, after `result.config = ...` and before the notifies:

```python
            try:
                self.bit.on_join(dev, result.role)
            except Exception:
                logger.exception("Bit.on_join failed; continuing")
```

(Confirm the granted-role name attribute on `JoinResult` -- the tests in
`tests/test_registration.py` show whether it is `result.role` -- and use
that.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_engine_on_join.py tests/test_engine.py -v`
Expected: PASS, and no regressions in the existing engine tests.

- [ ] **Step 5: Amend the spec** -- in
`docs/superpowers/specs/2026-08-20-metronome-bit-design.md` section 5, add
one sentence: "Join order is learned via a new optional no-op
`Bit.on_join(dev, role_name)` hook (the one engine-seam addition; same
extension precedent as `cues(at)`)." In section 6's `fail_player` row and
section 9, note that all audio is ROOM-side (players have no Arco voice in
`devicelink/agent.py`), so `fail_player` is light-only.

- [ ] **Step 6: Commit**

```bash
git add control/bit.py control/engine.py tests/test_engine_on_join.py docs/superpowers/specs/2026-08-20-metronome-bit-design.md
git commit -m "feat(engine): optional Bit.on_join hook for granted joins"
```

---

### Task 2: MIDI program change (0xC0) in AudioBridge

**Files:**
- Modify: `control/audio.py` (`_apply_midi`, around line 198)
- Test: `tests/test_audio_bridge.py` (extend the existing file; find the
  existing `_apply_midi`/`feed_midi` tests and add alongside)

**Interfaces:**
- Consumes: `DeviceVoice.program_change(prog)` (already in the protocol;
  `FakeVoice` already records `("program_change", prog)`).
- Produces: a `(dev, 0xC0, prog, 0)` cue reaching `AudioBridge.feed_midi`
  switches that dev's voice program. Task 5's trigger scripts rely on this.

- [ ] **Step 1: Write the failing test**

Follow the existing test style in `tests/test_audio_bridge.py` (fake pool,
grant a role, feed midi). Add:

```python
def test_program_change_reaches_voice():
    bridge, pool = _granted_bridge()      # reuse/adapt the file's existing helper
    voice = pool.voices[-1]
    bridge.feed_midi("ie1", 0xC0, 38, 0)
    assert ("program_change", 38) in voice.sent


def test_program_change_for_unknown_dev_is_ignored():
    bridge, pool = _granted_bridge()
    bridge.feed_midi("nobody", 0xC0, 38, 0)   # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_audio_bridge.py -v -k program_change`
Expected: FAIL (0xC0 currently falls through `_apply_midi` unhandled).

- [ ] **Step 3: Implement**

In `control/audio.py` `_apply_midi`, after the note-off branch:

```python
        elif kind == 0xC0:
            entry.voice.program_change(d1)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_audio_bridge.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add control/audio.py tests/test_audio_bridge.py
git commit -m "feat(audio): route MIDI program change (0xC0) to the voice"
```

---

### Task 3: enqueue-time gesture stamping in `harness/o2_shroom.py`

**Files:**
- Modify: `harness/o2_shroom.py` (`enqueue_input` ~line 62,
  `drain_gestures` ~line 79, the `on_input` lambda ~line 292, the drain
  call site ~line 537)
- Test: `tests/test_o2_shroom.py` (extend -- it already tests
  `enqueue_input`/`drain_gestures`; find those tests and update)

**Interfaces:**
- Produces: `enqueue_input(q, msg, stamp: float)` stores `(stamp, msg)`;
  `drain_gestures(q, send, dev, now)` sends each gesture with its carried
  per-message stamp instead of drain-time `now` (`now` remains the
  fallback for entries whose stamp is None, and the returned
  sweep-suspension value stays the drained tilt's stamp).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_o2_shroom.py` (match its existing fake-`send` pattern):

```python
def test_gesture_carries_enqueue_stamp_not_drain_time():
    q = queue.Queue(maxsize=8)
    o2_shroom.enqueue_input(q, {"type": "tap", "count": 1}, stamp=12.345)
    sent = []
    o2_shroom.drain_gestures(q, lambda *a: sent.append(a), "ie1", now=99.0)
    address, when, typespec, dev, peak, dur, count = sent[0]
    assert address == "/game/tap"
    assert when == 12.345          # the enqueue-time stamp, not 99.0
```

Also update every existing `enqueue_input(...)` call in the test file to
pass a `stamp=` value.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py -v`
Expected: the new test FAILS (TypeError on `stamp` kwarg).

- [ ] **Step 3: Implement**

- `enqueue_input(q, msg, stamp)`: `q.put_nowait((stamp, msg))`, same
  drop-oldest loop.
- `drain_gestures`: unpack `stamp, msg = q.get_nowait()`; use
  `when = stamp if stamp is not None else now` for the send time; a
  drained tilt sets `tilted = when`.
- The `on_input` wiring in `build()` (~line 292): the callback runs on the
  websocket handler thread, so read the clock there:

```python
    on_input = (None if input_queue is None
                else lambda msg: enqueue_input(input_queue, msg,
                                               stamp=clock()))
```

  where `clock` is a new `build(...)` parameter defaulting to None; in
  `main()` pass `clock=o2lite.time_get`. First VERIFY thread-safety: read
  `o2litepy`'s `time_get` in the arco checkout
  (`/Users/chris/projects/arco/o2litepy/`, falling back to the
  `rbdannenberg/o2` checkout if absent -- see the deep-dive's o2litepy
  note). It must be a pure read of synced-clock offsets (no socket I/O, no
  state mutation). If it is anything more, instead capture
  `time.monotonic()` at enqueue and convert at drain:
  `stamp = now - (time.monotonic() - mono_at_enqueue)`.
  Document whichever you did in a comment at the lambda.
- Keep Design Rule 4 honest in the `drain_gestures` docstring: the whole
  simulator process is still the device; the stamp just moves earlier
  inside it.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py tests/test_markers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/o2_shroom.py tests/test_o2_shroom.py
git commit -m "feat(harness): stamp browser gestures at enqueue, not drain"
```

---

### Task 4: pointerup-immediate taps in luxaeterna's WebSim page

**Files (sibling repo `/Users/chris/projects/luxaeterna`, its own git):**
- Modify: `luxaeterna/backends/websim.py` (PAGE_HTML script, ~lines 86-107)
- Test: whatever test in luxaeterna's `tests/` already covers PAGE_HTML
  gesture wiring (search: `grep -rn "TAP_DELAY\|dblclick\|onclick" tests/`
  in that repo); update it, or add a source-shape test beside it if none
  asserts behavior.

**Interfaces:**
- Produces: a click on the sim canvas sends `{"type":"tap","count":1}`
  immediately at pointerup, with no 250 ms disambiguation hold. Double-click
  taps (`count:2`) are gone from the page. Drag/tilt behavior unchanged.

**Why pointerup, not pointerdown:** pointerdown cannot yet know whether a
drag (tilt) follows, so a down-fired tap would put a spurious,
phrase-spoiling tap in front of every tilt drag. Pointerup after a
non-drag is unambiguous, immediate, and its small consistent down-to-up
latency is exactly what MetronomeBit's `INPUT_OFFSET_S` knob absorbs.

- [ ] **Step 1: Write/adjust the failing test** in luxaeterna asserting
the page source no longer contains `TAP_DELAY_MS` or `ondblclick`, and
does send a tap from the pointerup handler (match that repo's existing
page-test idiom -- if it has behavioral JS tests, use those; if only
source-shape tests, assert on the script text).

- [ ] **Step 2: Run luxaeterna's suite to see it fail**

Run (from `/Users/chris/projects/luxaeterna`): `.venv/bin/python -m pytest tests -v -k websim`
(use that repo's documented invocation if different -- check its README).

- [ ] **Step 3: Implement.** In PAGE_HTML: delete `TAP_DELAY_MS` and
`tapTimer`, delete `cv.onclick` and `cv.ondblclick`, and extend the
existing `cv.onpointerup` (or add one; the current code sets
`dragging=false` somewhere on up -- find it) so a non-drag release sends
immediately:

```javascript
cv.onpointerup=(e)=>{
  const wasDrag=dragging&&dragMoved;
  dragging=false;
  if(!wasDrag)sendGesture({type:'tap',count:1});
};
```

Keep the >5 px `dragMoved` suppression exactly as is.

- [ ] **Step 4: Run luxaeterna's suite; expect PASS. Then run
mm-terrarium's suite** (`.venv/bin/python -m pytest tests -x -q` from the
worktree) to confirm nothing here asserted on the old page text.

- [ ] **Step 5: Commit in luxaeterna** (its repo, on a branch following
that repo's convention -- check `git -C /Users/chris/projects/luxaeterna
log --oneline -5` for message style):

```bash
git -C /Users/chris/projects/luxaeterna checkout -b websim-pointerup-taps
git -C /Users/chris/projects/luxaeterna add luxaeterna/backends/websim.py tests
git -C /Users/chris/projects/luxaeterna commit -m "feat(websim): send taps immediately on pointerup, drop the 250 ms hold"
```

Do NOT push or open a PR; report the branch name back for Chris to review.

---

### Task 5: MetronomeBit declarations -- roles, manifests, triggers

**Files:**
- Create: `bits/metronome_bit.py`
- Test: `tests/test_metronome_bit_declarations.py` (create)

**Interfaces:**
- Consumes: `Role`, `RoleClass`, `RoleTable` (control/roles.py);
  `room_role(RoomType.DEMO, light_manifest=..., ugen_manifest=...)
  -> (name, role, node_id)` (control/rooms.py:85); `Trigger`, `Condition`,
  `ConditionSource`, `ScriptStep`, `TriggerTable`, `TriggerTarget`
  (control/triggers.py); `TARGET`, `PlayCue` (control/cues.py);
  `Bit.on_join` from Task 1.
- Produces: `class MetronomeBit(Bit)` with `version="0.1"`,
  `room_types={RoomType.DEMO}`, node `METRO_PLAYER_NODE`, and the class
  constants Tasks 6-8 build on:

```python
BEAT_S = 0.6                 # 100 BPM
BEATS_PER_CYCLE = 8          # 4 call + 4 wait
CYCLES = 4
LEAD_IN_S = BEAT_S
TOLERANCE_S = 0.050
INPUT_OFFSET_S = 0.0         # calibration knob, subtracted from tap `at`
JUDGE_SLACK_S = 0.050
FINALE_S = 10.0
CLICK_KEY, HARD_VEL, SOFT_VEL = 76, 120, 65
FAIL_KEY, FAIL_VEL = 33, 110
PROG_CLICK, PROG_FAIL, PROG_PAD = 115, 38, 89
GREEN_CC, RED_CC = 42, 0     # cc:74 hue values (~0.33 green, 0.0 red)
LEVEL_BASE, LEVEL_PULSE = 60, 110   # cc:11 neutral vs on-beat pulse
BLOOM_HUE_CC = 70            # bloom hue lane, separate from aurora's 74
RAINBOW_LEVEL_CC = 21        # room rainbow's level lane (finale only)
```

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_metronome_bit_declarations.py"""
from bits.metronome_bit import MetronomeBit
from control.engine import GameServer
from control.rooms import RoomType


def _running_gs(n_players=0):
    gs = GameServer({"MetronomeBit": MetronomeBit})
    gs.load_bit("MetronomeBit")
    gs.setup()
    for i in range(n_players):
        assert gs.join(f"ie{i+1}", "METRO_PLAYER_NODE").granted
    return gs


def test_loads_and_validates():
    # load_bit runs role_config + trigger validation; loading IS the test.
    _running_gs()


def test_demo_only():
    assert MetronomeBit.room_types == {RoomType.DEMO}


def test_third_player_is_denied_by_capacity():
    gs = _running_gs(n_players=2)
    assert not gs.join("ie3", "METRO_PLAYER_NODE").granted


def test_on_join_records_rotation_in_join_order():
    gs = _running_gs(n_players=2)
    assert gs.bit._players == ["ie1", "ie2"]


def test_player_light_manifest_shape():
    table = MetronomeBit().role_table
    player = table.roles["player"]
    instruments = {i["instrument"] for i in
                   player.light_manifest["instruments"]}
    assert instruments == {"aurora", "bloom"}
    assert player.scored and player.capacity == 2


def test_room_declares_rainbow_dark_by_default():
    table = MetronomeBit().role_table
    room = table.roles["demo_room"]     # adjust to room_role_name(RoomType.DEMO)
    by_name = {i["instrument"]: i for i in room.light_manifest["instruments"]}
    assert by_name["rainbow"]["params"]["level"] == 0.0


def test_trigger_names():
    names = set(MetronomeBit().trigger_table.triggers)
    assert names == {"fireworks_player", "fireworks_room",
                     "fail_player", "fail_room", "finale"}
```

Use `control.rooms.room_role_name(RoomType.DEMO)` for the room role key
rather than the literal `"demo_room"`.

- [ ] **Step 2: Run to verify failure** (module doesn't exist).

Run: `.venv/bin/python -m pytest tests/test_metronome_bit_declarations.py -v`

- [ ] **Step 3: Implement the declarations** in `bits/metronome_bit.py`
(constants above, plus):

- `player` role: `RoleClass.UNIQUE`, `capacity=2`, `scored=True`,
  `uses=["tap"]`, no samples; light manifest:

```python
light_manifest={
    "instruments": [
        {"instrument": "aurora", "target": "primary",
         "params": {"hue": 0.33, "level": 0.47},
         "lanes": [{"source": "cc:74", "dest": "hue"},
                   {"source": "cc:11", "dest": "level"}]},
        {"instrument": "bloom",
         "params": {"hue": 0.33},
         "lanes": [{"source": "note", "dest": "trigger"},
                   {"source": "cc:70", "dest": "hue"}]},
    ],
}
```

  No ugen manifest for the player (players have no Arco voice -- deviation
  note 1). Node map: `{"METRO_PLAYER_NODE": ["player"]}` plus the room
  entry.
- Room role via `room_role(RoomType.DEMO, light_manifest=..., ugen_manifest=...)`:
  light = the same aurora + bloom pair PLUS
  `{"instrument": "rainbow", "target": "primary", "params": {"hue": 0.0,
  "level": 0.0, "span": 1.0, "speed": 0.05}, "lanes": [{"source": "cc:21",
  "dest": "level"}]}`; ugen =
  `{"instruments": [{"instrument": "flsyn", "program": 115,
  "lanes": [{"source": "cc:74", "dest": "cc:74"},
  {"source": "cc:11", "dest": "cc:11"}]}]}` -- **no `drone` key** (the
  metronome must be silent between clicks; the Bit sends its own
  note-ons). Verify `AudioBridge.on_grant` tolerates a missing `drone`
  (read `control/audio.py:137-152`); if it requires one, pass
  `"drone": None` or adapt minimally with a test.
- `trigger_table`: all five triggers `ConditionSource.BIT_ADJUDICATED`.
  Scripts (all steps address `TARGET`):
  - `fireworks_player` / `fireworks_room` (identical script builder,
    targets DEVICE / ROOM): seeded `random.Random(2026)` at build; 12
    flashes over ~1.4 s:

```python
def _fireworks_script():
    rng = random.Random(2026)
    steps = []
    for i in range(12):
        t = i * 0.12
        pitch = rng.randrange(48, 84)
        steps.append(ScriptStep(t, (TARGET, 0xB0, 70, rng.randrange(0, 128))))
        steps.append(ScriptStep(t, (TARGET, 0x90, pitch, 100)))
        steps.append(ScriptStep(t + 0.08, (TARGET, 0x80, pitch, 0)))
    return tuple(steps)
```

  - `fail_room`: `ScriptStep(0.0, (TARGET, 0xC0, 38, 0))`,
    `(0.0, (TARGET, 0xB0, 74, 0))` red,
    `(0.05, (TARGET, 0x90, 33, 110))`,
    `(0.9, (TARGET, 0x80, 33, 0))`,
    `(1.0, (TARGET, 0xC0, 115, 0))` restore click program.
  - `fail_player` (light-only): `(0.0, (TARGET, 0xB0, 74, 0))` red;
    `(1.0, (TARGET, 0xB0, 11, 0))` non-glowing.
  - `finale`: `(0.0, (TARGET, 0xC0, 89, 0))`,
    `(0.0, (TARGET, 0xB0, 21, 127))` rainbow up,
    `(0.0, (TARGET, 0xB0, 11, 0))` aurora dark,
    `(0.1, (TARGET, 0x90, 57, 100))` pad on; then 20 cc:74 steps at 0.5 s
    spacing sweeping a triangle 0->127->0 twice
    (`value = int(127 * tri(i / 10.0))` with
    `tri(x) = 2*f if (f := x % 1.0) < 0.5 else 2*(1-f)`); then
    `(10.0, (TARGET, 0x80, 57, 0))`, `(10.0, (TARGET, 0xB0, 21, 0))`,
    `(10.0, (TARGET, 0xB0, 11, LEVEL_BASE))`.
- `on_join(dev, role_name)`: append to `self._players` when
  `role_name == "player"`.
- `__init__` state (Tasks 6-7 use these): `self._players = []`,
  `self._rotation = []`, `self._t0 = None`, `self._next_beat = 0`,
  `self._elapsed = 0.0`, `self._done = False`, `self._successes = {}`,
  `self._phrase = None` (per-phrase dict), `self._judged_cycles = 0`,
  `self._finale_end = None`, `self._tap_errors_ms = []`,
  `self._pending_fires = []`.
- `on_run_start`: `self._rotation = list(self._players)`; reset the rest.
- Lifecycle no-ops and a placeholder `update(dt)` accumulating
  `self._elapsed` and returning `self._done`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_metronome_bit_declarations.py -v`
Expected: PASS. Also run `.venv/bin/python -m pytest tests -x -q` for
regressions.

- [ ] **Step 5: Commit**

```bash
git add bits/metronome_bit.py tests/test_metronome_bit_declarations.py
git commit -m "feat(bits): MetronomeBit declarations -- roles, manifests, triggers"
```

---

### Task 6: beat grid and cue emission (`cues(at)`)

**Files:**
- Modify: `bits/metronome_bit.py`
- Test: `tests/test_metronome_bit_grid.py` (create)

**Interfaces:**
- Consumes: `LightCue`, `ROOM` from control/cues.py; constants from Task 5.
- Produces: `MetronomeBit.cues(at) -> list`; internal helpers Tasks 7-8
  reuse: `_grid(self, k: int) -> float` returning `self._t0 + k *
  self.BEAT_S` (k is a global beat index 0..31), and
  `_beat_cues(self, k: int) -> list` (the cues for beat k, all
  absolutely timed `LightCue`s with `when=self._grid(k)` or derived).

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_metronome_bit_grid.py"""
from bits.metronome_bit import MetronomeBit
from control.cues import ROOM, LightCue


def _started(players=("ie1",)):
    bit = MetronomeBit()
    for dev in players:
        bit.on_join(dev, "player")
    bit.on_run_start()
    return bit


def _lightcues(cues):
    return [c for c in cues if isinstance(c, LightCue)]


def test_anchor_set_on_first_cues_call():
    bit = _started()
    bit.cues(100.0)
    assert bit._t0 == 100.0 + bit.LEAD_IN_S


def test_beat_zero_emits_hard_click_at_t0():
    bit = _started()
    cues = bit.cues(100.0)
    ons = [c for c in _lightcues(cues)
           if c.dev == ROOM and c.status == 0x90 and c.data1 == bit.CLICK_KEY]
    assert ons and ons[0].data2 == bit.HARD_VEL
    assert ons[0].when == bit._t0
    offs = [c for c in _lightcues(cues) if c.status == 0x80]
    assert offs and offs[0].when > ons[0].when


def test_soft_clicks_on_beats_1_to_3_and_none_on_wait_beats():
    bit = _started()
    bit.cues(100.0)                    # anchor + beat 0
    seen = {}
    for step in range(1, 200):         # tick forward well past one cycle
        at = 100.0 + step * 0.05
        for c in _lightcues(bit.cues(at)):
            if c.dev == ROOM and c.status == 0x90 and c.data1 == bit.CLICK_KEY:
                k = round((c.when - bit._t0) / bit.BEAT_S)
                seen[k] = c.data2
    assert seen[1] == seen[2] == seen[3] == bit.SOFT_VEL
    assert 4 not in seen and 5 not in seen and 6 not in seen and 7 not in seen
    assert seen[8] == bit.HARD_VEL     # next cycle's hard click


def test_emission_is_idempotent_per_beat():
    bit = _started()
    bit.cues(100.0)
    again = [c for c in _lightcues(bit.cues(100.0))
             if c.status == 0x90 and c.data1 == bit.CLICK_KEY
             and c.when == bit._t0]
    assert again == []                 # beat 0 not re-emitted


def test_green_pulse_rides_every_beat_on_room_and_players():
    bit = _started(players=("ie1", "ie2"))
    cues = _lightcues(bit.cues(100.0))
    pulse_devs = {c.dev for c in cues if c.status == 0xB0 and c.data1 == 11
                  and c.data2 == bit.LEVEL_PULSE}
    assert pulse_devs == {ROOM, "ie1", "ie2"}
```

- [ ] **Step 2: Run to verify failure.**

Run: `.venv/bin/python -m pytest tests/test_metronome_bit_grid.py -v`

- [ ] **Step 3: Implement.** In `cues(at)`:

```python
def cues(self, at: float) -> list:
    out = []
    if self._t0 is None:
        self._t0 = at + self.LEAD_IN_S
    # Emit each beat's cues once, when its gridpoint enters the horizon
    # one beat ahead of `at`. TimedQueue holds them until `when`.
    total = self.BEATS_PER_CYCLE * self.CYCLES
    while (self._next_beat < total
           and self._grid(self._next_beat) <= at + self.BEAT_S):
        out.extend(self._beat_cues(self._next_beat))
        self._next_beat += 1
    return out
```

`_beat_cues(k)`: `t = self._grid(k)`, `pos = k % 8`, `cycle = k // 8`.
- Every beat: level pulse on ROOM and every dev in `self._rotation`:
  `LightCue(dev, 0xB0, 11, self.LEVEL_PULSE, when=t)` and the return to
  `LEVEL_BASE` at `when=t + 0.15`.
- `pos in (0, 1, 2, 3)`: click note pair on ROOM --
  `LightCue(ROOM, 0x90, CLICK_KEY, HARD_VEL if pos == 0 else SOFT_VEL,
  when=t)` and `LightCue(ROOM, 0x80, CLICK_KEY, 0, when=t + 0.1)`.
- `pos == 0`: turn-start recovery (green restored) -- covered in Task 7;
  leave a `# recovery: Task 7` comment for now.

- [ ] **Step 4: Run tests** (`pytest tests/test_metronome_bit_grid.py -v`,
then the full suite `-x -q`). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bits/metronome_bit.py tests/test_metronome_bit_grid.py
git commit -m "feat(bits): MetronomeBit beat grid and timed click/pulse emission"
```

---

### Task 7: tap grading, phrase judgment, turns, consequences

**Files:**
- Modify: `bits/metronome_bit.py`
- Test: `tests/test_metronome_bit_judgment.py` (create)

**Interfaces:**
- Consumes: `_grid(k)`, constants, `FireTrigger` (control/cues.py).
- Produces: `verb_handlers() -> {"tap": self._on_tap}`;
  `_on_tap(dev, args, at) -> list`; judgment emitted from `cues(at)` as
  `FireTrigger("fireworks_player", dev)` + `FireTrigger("fireworks_room")`
  on success, `FireTrigger("fail_player", dev)` + `FireTrigger("fail_room")`
  on failure; `self._successes[dev]` counts; `_turn_dev(cycle) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_metronome_bit_judgment.py"""
from bits.metronome_bit import MetronomeBit
from control.cues import FireTrigger

B = MetronomeBit.BEAT_S


def _started(players=("ie1",)):
    bit = MetronomeBit()
    for dev in players:
        bit.on_join(dev, "player")
    bit.on_run_start()
    bit.cues(100.0)                    # anchor: t0 = 100.0 + LEAD_IN_S
    return bit


def _wait_grid(bit, cycle, wait_beat):
    return bit._t0 + (cycle * 8 + 4 + wait_beat) * B


def _drain_until(bit, at, step=0.02):
    """Advance cues(at) to `at`, returning every FireTrigger seen."""
    fires, t = [], bit._last_drained if hasattr(bit, "_last_drained") else 100.0
    while t < at:
        t = min(t + step, at)
        fires += [c for c in bit.cues(t) if isinstance(c, FireTrigger)]
    bit._last_drained = t
    return fires


def _tap_all_four(bit, cycle, dev="ie1", err=0.0):
    for w in range(4):
        bit._on_tap(dev, [dev, 1.0, 50.0, 1], _wait_grid(bit, cycle, w) + err)


def test_all_four_in_time_taps_fire_fireworks():
    bit = _started()
    _tap_all_four(bit, 0, err=0.049)
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    names = [(f.name, f.dev) for f in fires]
    assert ("fireworks_player", "ie1") in names
    assert ("fireworks_room", None) in names
    assert bit._successes["ie1"] == 1


def test_tap_51ms_off_is_off_grid_and_spoils():
    bit = _started()
    _tap_all_four(bit, 0)
    bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, 1) + 0.051)
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    assert ("fail_player", "ie1") in [(f.name, f.dev) for f in fires]
    assert bit._successes.get("ie1", 0) == 0


def test_missing_beat_fails_phrase():
    bit = _started()
    for w in (0, 1, 3):                # beat 2 never tapped
        bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, w))
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    assert any(f.name == "fail_room" for f in fires)


def test_round_robin_ignores_off_turn_taps():
    bit = _started(players=("ie1", "ie2"))
    # cycle 0 is ie1's; ie2's taps must neither help nor spoil
    bit._on_tap("ie2", ["ie2", 1.0, 50.0, 1], _wait_grid(bit, 0, 0) + 0.3)
    _tap_all_four(bit, 0, dev="ie1")
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.2)
    assert ("fireworks_player", "ie1") in [(f.name, f.dev) for f in fires]
    # cycle 1 belongs to ie2
    assert bit._turn_dev(1) == "ie2"


def test_no_players_means_no_judgment():
    bit = MetronomeBit()
    bit.on_run_start()
    bit.cues(100.0)
    fires = _drain_until(bit, _wait_grid(bit, 0, 3) + 0.3)
    assert fires == []


def test_judgment_fires_exactly_once_per_cycle():
    bit = _started()
    _tap_all_four(bit, 0)
    end = _wait_grid(bit, 0, 3) + 0.2
    first = _drain_until(bit, end)
    again = _drain_until(bit, end + 0.5)
    assert sum(f.name == "fireworks_room" for f in first) == 1
    assert not any(f.name == "fireworks_room" for f in again)


def test_tap_errors_are_recorded_in_ms():
    bit = _started()
    bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1], _wait_grid(bit, 0, 0) + 0.02)
    assert bit._tap_errors_ms[-1] == 20.0
```

- [ ] **Step 2: Run to verify failure.**

Run: `.venv/bin/python -m pytest tests/test_metronome_bit_judgment.py -v`

- [ ] **Step 3: Implement.**

- `verb_handlers()` returns `{"tap": self._on_tap}`.
- `_turn_dev(cycle)`: `self._rotation[cycle % len(self._rotation)]` or
  None when empty.
- Phrase state: on entering each cycle's judgment scope lazily create
  `self._phrase = {"cycle": c, "hits": set(), "spoiled": False}`.
- `_on_tap(dev, args, at)`:

```python
def _on_tap(self, dev: str, args: list, at: float) -> list:
    if self._t0 is None or self._done:
        return []
    t = at - self.INPUT_OFFSET_S
    cycle = self._current_cycle(t)
    if cycle is None or dev != self._turn_dev(cycle):
        return []
    phrase = self._phrase_for(cycle)
    # nearest wait-beat gridpoint of this cycle
    best_w, best_err = None, None
    for w in range(4):
        err = t - self._grid(cycle * 8 + 4 + w)
        if best_err is None or abs(err) < abs(best_err):
            best_w, best_err = w, err
    self._tap_errors_ms.append(round(best_err * 1000.0, 1))
    if abs(best_err) <= self.TOLERANCE_S:
        phrase["hits"].add(best_w)
    else:
        phrase["spoiled"] = True
    return []
```

  `_current_cycle(t)`: the cycle whose span (beat 0 gridpoint minus
  tolerance through last wait gridpoint plus tolerance) contains `t`;
  None outside every span. Keep it simple:
  `k = (t - self._t0) / self.BEAT_S`; cycle = `int(k // 8)` clamped to
  `0..CYCLES-1` only when `-self.TOLERANCE_S <= t - self._t0` and
  `t <= self._grid(self.CYCLES * 8 - 1) + self.TOLERANCE_S` accounting for
  edge overlap at cycle boundaries: a tap within TOLERANCE_S of the last
  wait beat of cycle c but landing after cycle c+1 starts must still grade
  against cycle c -- write the boundary test values above to pin this.
- Judgment inside `cues(at)` (after emission loop): for the oldest
  unjudged cycle `c < CYCLES`, when
  `at >= self._grid(c * 8 + 7) + self.TOLERANCE_S + self.JUDGE_SLACK_S`:
  judge -- `dev = self._turn_dev(c)`; if dev is None, mark judged, no
  fires; else success iff `phrase["hits"] == {0,1,2,3}` and not spoiled;
  append the pair of `FireTrigger`s to the returned list; increment
  `self._successes[dev]` on success; record per-dev fail state for
  recovery; `self._judged_cycles += 1`.
- Recovery in `_beat_cues` at `pos == 0` (replacing Task 6's comment): at
  cycle start, `LightCue(ROOM, 0xB0, 74, GREEN_CC, when=t)` (room back to
  green), and for the turn dev `LightCue(dev, 0xB0, 74, GREEN_CC, when=t)`
  + `LightCue(dev, 0xB0, 11, LEVEL_BASE, when=t)` (relight).

- [ ] **Step 4: Run tests** (judgment file, grid file, then full suite).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bits/metronome_bit.py tests/test_metronome_bit_judgment.py
git commit -m "feat(bits): MetronomeBit tap grading, phrase judgment, round-robin turns"
```

---

### Task 8: finale, completion, result() and status()

**Files:**
- Modify: `bits/metronome_bit.py`
- Test: `tests/test_metronome_bit_finale.py` (create)

**Interfaces:**
- Consumes: everything above.
- Produces: `update(dt) -> bool` completion; `result() -> dict`;
  `status() -> dict` (finite floats only -- wire_json rule).

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_metronome_bit_finale.py"""
from bits.metronome_bit import MetronomeBit
from control.cues import FireTrigger

B = MetronomeBit.BEAT_S


def _run_through(bit, tap_cycles=()):
    """Anchor at 100.0, tap all four wait beats of the given cycles as ie1,
    and drain cues to the end of the last cycle's judgment."""
    bit.cues(100.0)
    for c in tap_cycles:
        for w in range(4):
            bit._on_tap("ie1", ["ie1", 1.0, 50.0, 1],
                        bit._t0 + (c * 8 + 4 + w) * B)
    fires, t = [], 100.0
    end = bit._t0 + (bit.CYCLES * 8 - 1) * B + 0.3
    while t < end:
        t += 0.02
        bit.update(0.02)
        fires += [c for c in bit.cues(t) if isinstance(c, FireTrigger)]
    return fires, t


def _started():
    bit = MetronomeBit()
    bit.on_join("ie1", "player")
    bit.on_run_start()
    return bit


def test_finale_fires_after_any_success_and_completes_after_10s():
    bit = _started()
    fires, t = _run_through(bit, tap_cycles=(0,))
    assert any(f.name == "finale" for f in fires)
    assert not bit.update(0.02)                  # finale still running
    for _ in range(600):                          # ~12 s of ticks
        bit.update(0.02)
        t += 0.02
        bit.cues(t)
    assert bit.update(0.02)


def test_no_success_completes_immediately_without_finale():
    bit = _started()
    fires, t = _run_through(bit, tap_cycles=())
    assert not any(f.name == "finale" for f in fires)
    assert bit.update(0.02)


def test_result_reports_successes():
    bit = _started()
    _run_through(bit, tap_cycles=(0,))
    assert bit.result() == {"phrases": 4, "successes": {"ie1": 1}}


def test_status_is_wire_safe():
    import math
    bit = _started()
    bit.cues(100.0)
    status = bit.status()
    assert "turn" in status and "cycle" in status
    for v in status.values():
        if isinstance(v, float):
            assert math.isfinite(v)
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** After the 4th judgment in `cues(at)`: if
`sum(self._successes.values()) >= 1` and `self._finale_end is None`,
return `FireTrigger("finale")` among the cues and set
`self._finale_end = at + self.FINALE_S`; `cues(at)` sets
`self._done = True` when `self._finale_end is not None and
at >= self._finale_end`, or immediately after the 4th judgment when no
success. `update(dt)` accumulates `self._elapsed` and returns
`self._done`. `result()` and `status()` per the tests; `status()` also
carries `{"tap_errors_ms": self._tap_errors_ms[-8:]}` (a list of finite
floats renders fine in the Console's generic k/v table as its repr).
- [ ] **Step 4: Run the three metronome test files, then the full suite.**
- [ ] **Step 5: Commit**

```bash
git add bits/metronome_bit.py tests/test_metronome_bit_finale.py
git commit -m "feat(bits): MetronomeBit finale, completion, result and status"
```

---

### Task 9: harness wiring -- `--bit` flag through terrarium_boot and run_stack

**Files:**
- Modify: `harness/terrarium_boot.py` (registry ~line 659, bit_name
  ~line 617, new `--bit` argument beside the existing flags)
- Modify: `harness/run_stack.py` (new `--bit` argument, forwarded into the
  spawned terrarium_boot command beside `--room-type` ~line 121)
- Test: `tests/test_terrarium_boot.py` and `tests/test_run_stack.py`
  (extend -- both files already test CLI flag plumbing; follow their
  existing flag-forwarding test pattern)

**Interfaces:**
- Produces: `--bit {TestBit,MetronomeBit}` (default `TestBit`) on both
  entry points; `MetronomeBit` in the registry
  (`{"TestBit": _timed_test_bit_cls(...), "MetronomeBit": MetronomeBit}`)
  and `BootConfig.bit_name` set from the flag. `--run-duration` continues
  to apply only to TestBit (it wraps only that class).

- [ ] **Step 1: Write the failing tests** -- in each file, copy the
existing test that asserts `--room-type` reaches `BootConfig` /
the spawned command, and assert the same for `--bit MetronomeBit`
(terrarium_boot: `config.bit_name == "MetronomeBit"`; run_stack: the
spawned control command list contains `["--bit", "MetronomeBit"]`).
Also assert the default stays `TestBit`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the two `add_argument` calls
(`choices=["TestBit", "MetronomeBit"]`, `default="TestBit"`), the registry
entry (`from bits.metronome_bit import MetronomeBit` at terrarium_boot's
existing bit-import site), `bit_name=args.bit`, and run_stack's
passthrough (`command += ["--bit", cfg.bit]` beside the `--room-type`
forward, with `bit: str = "TestBit"` on its config dataclass).
Note: MetronomeBit is DEMO-only; `boot()` already fails loud when the
resolved RoomType is not in `bit_cls.room_types`, so `--bit MetronomeBit`
without `--room-type DEMO` fails with the existing clear BootFailure --
no extra validation needed here (do not duplicate the gate).
- [ ] **Step 4: Run both test files, then the full suite.**
- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py harness/run_stack.py tests/test_terrarium_boot.py tests/test_run_stack.py
git commit -m "feat(harness): --bit flag selects the Bit; MetronomeBit registered"
```

---

### Task 10: full offline suite + live verification

**Files:** none new (fixes only, if the suite finds any).

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: everything passes (baseline before this branch: 1099 passed,
1 skipped; expect that plus every test added above). Fix regressions.

- [ ] **Step 2: luxaeterna suite** (from `/Users/chris/projects/luxaeterna`):
its pytest run, green.

- [ ] **Step 3: Live run** (needs the real Arco checkout; run_stack
resolves o2litepy's PYTHONPATH itself):

**RUN ON: MYCOLOGICAL**
```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m harness.run_stack --devices 2 --open --room-type DEMO --bit MetronomeBit
```

Verify, tapping the two sim canvases during wait beats:
woodblock clicks (1 hard + 3 soft) each cycle; green pulse in time on
array + both shrooms; all-4 taps -> fireworks on that shroom AND the
array; a missed/spoiled phrase -> red + low tone, shroom dark, both
restored at that player's next turn; after any success, run ends with
10 s rainbow + modulating pad. Read `tap_errors_ms` off the Console
`bit_status` panel and record the distribution in the run report -- this
is the plus-or-minus 50 ms feasibility measurement.

- [ ] **Step 4: Commit any fixes; report results** (including the
tap-error numbers and the luxaeterna branch name) back to Chris.
