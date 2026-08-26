# Trigger Cards & Surface Triggers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compact the Console's Triggers panel to ~3 cards per row, add a `SolidCue` override cue and `SURFACE` trigger targeting, a latched per-surface Stop/mute, and rework TestBit's operator triggers (Flash, Play Aurora, Stop, Win).

**Architecture:** Devices are dumb pixel sinks — Control renders every frame and ships `/dev/leds` — so `SolidCue` and the mute blackout are implemented entirely Control-side in `DeviceLinkAgent`'s frame path; **no wire-protocol or device-side change is needed**. New cue types (`SolidCue`, `MuteCue`) follow the existing distinct-dataclass pattern in `control/cues.py`; dispatch rides `GameServer._dispatch_cues` with two new guarded transport sinks. Mute state lives on `GameServer` (`muted: set[str]`), suppression happens at the `DeviceLinkAgent` seam, and any non-mute trigger fire at a surface un-mutes it first.

**Tech Stack:** Python 3 (offline pytest suite via `.venv`), vanilla-JS Console modules under `console/static/`, node-based JS tests under `tests/js/`.

**Spec:** `docs/superpowers/specs/2026-08-26-trigger-cards-and-surface-triggers-design.md`

## Global Constraints

- Run the Python suite ONLY as `.venv/bin/python -m pytest tests -v` (never bare `python3`). In a fresh worktree first: `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`.
- The whole suite must stay green **offline**: no O2 network, no Arco, no pyarco import at module level anywhere in `control/`, `devicelink/`, `console/`.
- Engine sinks (`on_light_cue`, `on_play_cue`, and the new `on_solid_cue`, `on_mute_change`) must be guarded call sites in the engine — a raising transport must never wedge Control. `GameServer.data()` and `fire_trigger()` never raise.
- `console/agent.py` reuses `uplink.protocol` builders where they exist; `devicelink/protocol.py` stays the single source of truth for the device wire (unchanged this plan).
- JS card lists must NOT rebuild on `trigger_fired` or `devices_changed` (only the picker options refresh) — `tests/js/trigger_panel_behavior.test.js` pins this.
- Commit after every task; commit messages in the repo's `feat:`/`fix:` style.

---

### Task 1: `SolidCue` and `MuteCue` types + script validation + view serialization

**Files:**
- Modify: `control/cues.py` (append after `FireTrigger`)
- Modify: `control/triggers.py` (`_validate_step_cue`, `expand_script`)
- Modify: `control/trigger_view.py` (`_step_view`)
- Test: `tests/test_triggers.py`, `tests/test_trigger_view.py`

**Interfaces:**
- Produces: `SolidCue(dev: str, rgb: tuple[int,int,int], level: float, duration: float | None, when: float | None = None)` and `MuteCue(dev: str)`, both frozen dataclasses in `control.cues`. `duration=None` means "latched until cleared" (Stop's blackout). `expand_script` emits per-dev `SolidCue`s with `when = at + offset`, and passes `MuteCue` through per-dev (offset must be 0).

- [ ] **Step 1: Write failing tests** in `tests/test_triggers.py`:

```python
from control.cues import ROOM, TARGET, MuteCue, SolidCue

def _trigger(script):
    return Trigger(name="t", description="d", target=TriggerTarget.ROOM,
                   condition=Condition(name="c", description="d",
                                       source=ConditionSource.ADMIN_MANUAL),
                   script=script)

def test_solid_cue_step_validates():
    table = TriggerTable({"t": _trigger(
        (ScriptStep(1.0, SolidCue(TARGET, (255, 255, 255), 0.9, 5.0)),))})
    validate_trigger_table(table, set())   # no raise

def test_solid_cue_bad_level_refused():
    table = TriggerTable({"t": _trigger(
        (ScriptStep(0.0, SolidCue(TARGET, (255, 255, 255), 1.5, 5.0)),))})
    with pytest.raises(ValueError, match="level"):
        validate_trigger_table(table, set())

def test_mute_cue_nonzero_offset_refused():
    table = TriggerTable({"t": _trigger(
        (ScriptStep(0.5, MuteCue(TARGET)),))})
    with pytest.raises(ValueError, match="offset 0"):
        validate_trigger_table(table, set())

def test_expand_solid_cue_fans_out_with_when():
    trig = _trigger((ScriptStep(2.0, SolidCue(TARGET, (255,255,255), 0.9, 5.0)),))
    out = expand_script(trig, at=100.0, devs=["d1", "d2"])
    assert [c.dev for c in out] == ["d1", "d2"]
    assert all(isinstance(c, SolidCue) and c.when == 102.0 for c in out)

def test_expand_mute_cue_fans_out():
    trig = _trigger((ScriptStep(0.0, MuteCue(TARGET)),))
    out = expand_script(trig, at=100.0, devs=["d1"])
    assert out == [MuteCue("d1")]
```

And in `tests/test_trigger_view.py`:

```python
def test_step_view_solid_and_mute():
    solid = _step_view(ScriptStep(1.0, SolidCue(TARGET, (255,255,255), 0.9, 5.0)))
    assert solid == {"offset": 1.0, "kind": "solid", "dev": TARGET,
                     "rgb": [255, 255, 255], "level": 0.9, "duration": 5.0}
    mute = _step_view(ScriptStep(0.0, MuteCue(TARGET)))
    assert mute == {"offset": 0.0, "kind": "mute", "dev": TARGET}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_triggers.py tests/test_trigger_view.py -v`
Expected: FAIL with `ImportError: cannot import name 'SolidCue'`

- [ ] **Step 3: Implement.** In `control/cues.py` append:

```python
@dataclass(frozen=True)
class SolidCue:
    """A solid-color override applied ON TOP of a device's rendered session
    frame, bypassing instruments entirely -- so it works on every surface,
    including roles with empty light manifests. Applied Control-side at the
    frame-building seam (DeviceLinkAgent); nothing on the device wire changes.

    `duration` is seconds from `when`; None means latched until explicitly
    cleared (the mute blackout). `when=None` follows LightCue's convention:
    apply at the dispatch-supplied presentation time.
    """
    dev: str
    rgb: tuple[int, int, int]
    level: float
    duration: float | None
    when: float | None = None


@dataclass(frozen=True)
class MuteCue:
    """Latch a surface dark and silent (the Stop trigger). Distinct type for
    the same identity-dispatch reason as every cue here. Un-latching is not a
    cue: any non-mute trigger fired at the surface clears it (engine rule)."""
    dev: str
```

In `control/triggers.py`: import both; in `_validate_step_cue`, before the 4-tuple branch add:

```python
    if isinstance(cue, SolidCue):
        _validate_script_dev(cue.dev, where)
        for i, ch in enumerate(cue.rgb):
            if isinstance(ch, bool) or not isinstance(ch, int) or not 0 <= ch <= 255:
                raise ValueError(f"{where}: rgb[{i}] {ch!r} is outside 0-255")
        if not isinstance(cue.level, (int, float)) or not 0.0 <= float(cue.level) <= 1.0:
            raise ValueError(f"{where}: level {cue.level!r} is outside 0.0-1.0")
        if cue.duration is not None and (
                not isinstance(cue.duration, (int, float))
                or not math.isfinite(float(cue.duration))
                or float(cue.duration) <= 0):
            raise ValueError(f"{where}: duration must be > 0 seconds or None")
        if cue.when is not None:
            raise ValueError(f"{where}: a script SolidCue's timing is its "
                             f"offset; leave when=None")
        return
    if isinstance(cue, MuteCue):
        if float(step.offset) != 0.0:
            raise ValueError(f"{where}: a MuteCue must sit at offset 0; the "
                             f"latch is immediate, an offset would be ignored")
        _validate_script_dev(cue.dev, where)
        return
```

In `expand_script`'s loop, before the 4-tuple unpack:

```python
        if isinstance(cue, SolidCue):
            for dev in _step_devs(cue.dev, devs):
                out.append(SolidCue(dev, cue.rgb, cue.level, cue.duration,
                                    when=when))
            continue
        if isinstance(cue, MuteCue):
            for dev in _step_devs(cue.dev, devs):
                out.append(MuteCue(dev))
            continue
```

In `control/trigger_view.py`'s `_step_view`, before the tuple branch:

```python
    if isinstance(cue, SolidCue):
        return {"offset": float(step.offset), "kind": "solid", "dev": cue.dev,
                "rgb": list(cue.rgb), "level": cue.level,
                "duration": cue.duration}
    if isinstance(cue, MuteCue):
        return {"offset": float(step.offset), "kind": "mute", "dev": cue.dev}
```

- [ ] **Step 4: Run to verify pass** (same command). Expected: PASS.
- [ ] **Step 5: Commit** — `feat(cues): SolidCue override and MuteCue latch types with script validation`

---

### Task 2: Engine dispatch — `on_solid_cue` / mute latch / un-mute-on-play

**Files:**
- Modify: `control/engine.py` (`__init__`, `_dispatch_cues`, `fire_trigger`, `_unload`)
- Test: `tests/test_engine_triggers.py` (or the existing engine trigger test module — follow where `fire_trigger` tests live today)

**Interfaces:**
- Consumes: `SolidCue`, `MuteCue` from Task 1.
- Produces: `GameServer.on_solid_cue: callable | None`, called `(dev, rgb, level, duration, when)`; `GameServer.on_mute_change: callable | None`, called `(dev, muted: bool)`; `GameServer.muted: set[str]` (resolved dev ids). Un-mute rule: `fire_trigger` clears the mute of every resolved dev **when the trigger's expanded script contains no `MuteCue`**, before dispatch. `muted` is cleared (with `on_mute_change(dev, False)` per dev) during `_unload`.

- [ ] **Step 1: Write failing tests** (use the suite's existing GameServer fixture pattern — a loaded TestBit-style fixture Bit with a stub trigger table; mirror how current `fire_trigger` tests construct it):

```python
def test_solid_cue_dispatch_reaches_sink(gs_running):
    got = []
    gs_running.on_solid_cue = lambda *a: got.append(a)
    gs_running._dispatch_cues([SolidCue("d1", (255,255,255), 0.9, 5.0,
                                        when=123.0)], at=120.0)
    assert got == [("d1", (255, 255, 255), 0.9, 5.0, 123.0)]

def test_solid_cue_without_when_takes_at(gs_running):
    got = []
    gs_running.on_solid_cue = lambda *a: got.append(a)
    gs_running._dispatch_cues([SolidCue("d1", (0,0,0), 0.0, None)], at=120.0)
    assert got[0][4] == 120.0

def test_mute_cue_latches_and_notifies(gs_running):
    got = []
    gs_running.on_mute_change = lambda dev, m: got.append((dev, m))
    gs_running._dispatch_cues([MuteCue("d1")], at=None)
    assert "d1" in gs_running.muted and got == [("d1", True)]

def test_non_mute_fire_clears_mute_first(gs_with_flash_trigger):
    gs = gs_with_flash_trigger
    gs.muted.add("d1")
    events = []
    gs.on_mute_change = lambda dev, m: events.append((dev, m))
    assert gs.fire_trigger("flash", fired_by="admin-manual", dev="d1") is None
    assert "d1" not in gs.muted and ("d1", False) in events

def test_mute_fire_does_not_unmute_itself(gs_with_stop_trigger):
    gs = gs_with_stop_trigger
    assert gs.fire_trigger("stop", fired_by="admin-manual", dev="d1") is None
    assert "d1" in gs.muted

def test_raising_sinks_never_wedge(gs_running):
    gs_running.on_solid_cue = lambda *a: 1/0
    gs_running.on_mute_change = lambda *a: 1/0
    gs_running._dispatch_cues([SolidCue("d1",(1,1,1),1.0,1.0),
                               MuteCue("d1")], at=1.0)   # must not raise

def test_unload_clears_mutes(gs_running):
    gs_running.muted.add("d1")
    events = []
    gs_running.on_mute_change = lambda dev, m: events.append((dev, m))
    gs_running.abort()
    assert not gs_running.muted and ("d1", False) in events
```

- [ ] **Step 2: Run to verify failure.** Expected: `AttributeError: ... 'on_solid_cue'` / `'muted'`.
- [ ] **Step 3: Implement.** In `GameServer.__init__` alongside the existing sinks: `self.on_solid_cue = None`, `self.on_mute_change = None`, `self.muted: set[str] = set()`. In `_dispatch_cues`'s guarded per-cue block, before the `PlayCue` branch:

```python
                if isinstance(cue, SolidCue):
                    dev = self._resolve_dev(cue.dev)
                    if dev is None:
                        continue
                    when = at if cue.when is None else cue.when
                    sink, args = self.on_solid_cue, (dev, cue.rgb, cue.level,
                                                     cue.duration, when)
                elif isinstance(cue, MuteCue):
                    dev = self._resolve_dev(cue.dev)
                    if dev is None:
                        continue
                    self.muted.add(dev)
                    sink, args = self.on_mute_change, (dev, True)
                elif isinstance(cue, PlayCue):
                    ...
```

(`MuteCue` sits in the same guarded block, so a raising `on_mute_change` still leaves `muted` correct.) In `fire_trigger`, after `cues = expand_script(...)` inside the same `try`:

```python
            if not any(isinstance(c, MuteCue) for c in cues):
                self._clear_mutes(devs)
```

with the helper (guarded, engine style):

```python
    def _clear_mutes(self, devs) -> None:
        """Any non-mute fire at a surface un-latches it (spec section 4)."""
        for d in devs:
            if d in self.muted:
                self.muted.discard(d)
                if self.on_mute_change is not None:
                    try:
                        self.on_mute_change(d, False)
                    except Exception:
                        logger.exception("on_mute_change failed for %s", d)
```

In `_unload` (next to the existing per-device `on_release` loop): call `self._clear_mutes(list(self.muted))`.

Additionally: PlayCue suppression while muted — in `_dispatch_cues`'s `PlayCue` branch, after resolving `dev`, add `if dev in self.muted: continue` (light suppression happens transport-side; sound suppression is cheapest here, one line, and keeps the "muted = silent" rule even for gameplay-emitted samples).

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Run the whole engine test file** (`.venv/bin/python -m pytest tests -v -k engine`) to catch regressions.
- [ ] **Step 6: Commit** — `feat(engine): dispatch SolidCue/MuteCue, per-surface mute latch, un-mute on play`

---

### Task 3: `TargetKind.SURFACE`

**Files:**
- Modify: `control/triggers.py` (`TriggerTarget`), `control/engine.py` (`_resolve_target`, `fire_trigger` refusal)
- Test: `tests/test_triggers.py`, engine trigger tests

**Interfaces:**
- Produces: `TriggerTarget.SURFACE`. Fire semantics: `fire_trigger(name, fired_by=..., dev=<surface>)` where `<surface>` is a device id **or `cues.ROOM` (`"@room"`)**; SURFACE with no dev is refused (`"targets a surface; no surface given"`). `_resolve_target(SURFACE, cues.ROOM)` returns the bound Room fixture devs (same list ROOM returns); `_resolve_target(SURFACE, "d1")` returns `["d1"]`. The Console fire command reuses the existing `FireTriggerCommand.dev` field carrying `"@room"` for the Room entry — no protocol change.

- [ ] **Step 1: Write failing tests**

```python
def test_surface_resolves_device(gs_running):
    assert gs_running._resolve_target(TriggerTarget.SURFACE, "d1") == ["d1"]

def test_surface_resolves_room_sentinel(gs_with_bound_room):
    devs = gs_with_bound_room._resolve_target(TriggerTarget.SURFACE, ROOM)
    assert devs == gs_with_bound_room._resolve_target(TriggerTarget.ROOM, None)

def test_surface_fire_without_dev_refused(gs_with_surface_trigger):
    reason = gs_with_surface_trigger.fire_trigger(
        "t", fired_by="admin-manual", dev=None)
    assert "no surface" in reason
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** `TriggerTarget` gains `SURFACE = auto()  # operator-chosen: one device, or the Room`. In `_resolve_target`, first branch:

```python
        if target is TriggerTarget.SURFACE:
            if dev == CUES_ROOM:            # from control.cues import ROOM as CUES_ROOM
                pass                        # fall through to room_devs below
            else:
                return [dev] if dev else []
```

then after `room_devs` is built: `if target in (TriggerTarget.ROOM, TriggerTarget.SURFACE): return room_devs`. In `fire_trigger`, widen the existing DEVICE refusal:

```python
            if trigger.target in (TriggerTarget.DEVICE,
                                  TriggerTarget.SURFACE) and not dev:
                kind = ("the firing device"
                        if trigger.target is TriggerTarget.DEVICE
                        else "a surface")
                return f"trigger {name!r} targets {kind}; no surface given"
```

(keep the DEVICE message wording asserted by existing tests — check and preserve.)

- [ ] **Step 4: Run to verify pass**, then the full suite: `.venv/bin/python -m pytest tests -v`.
- [ ] **Step 5: Commit** — `feat(triggers): SURFACE target kind resolving to one device or the Room`

---

### Task 4: `DeviceLinkAgent` — override rendering, expiry, mute suppression

**Files:**
- Modify: `devicelink/agent.py` (`__init__`, `attach` wiring, `poll`, `_render_frames`, `_render_room`, `_feed_breath`, `_on_play_cue` passthrough already engine-suppressed)
- Test: `tests/test_devicelink_agent.py` (follow the file's existing fake-server/fixture pattern)

**Interfaces:**
- Consumes: `gs.on_solid_cue(dev, rgb, level, duration, when)`, `gs.on_mute_change(dev, muted)` from Task 2.
- Produces: outgoing `/dev/leds` frames overridden while a solid is active. Internal state: `self._overrides: dict[str, tuple[tuple[int,int,int], float, float | None]]` (dev → rgb, level, expires_at-or-None) and `self._muted: set[str]`.

Implementation notes (spec section 2): the override fills the outgoing frame **uniformly per 3-channel pixel** with `bytes(round(ch * level) for ch in rgb)` repeated to the frame's length (white and black are channel-order-independent, which is all this slice ships; a truncated last pixel on non-multiple-of-3 widths just repeats the pattern). Apply at the two send seams: in `_render_frames` per-device before the changed-frame comparison, and in `_render_room` on `frame` before slicing (one fill covers every fixture slice). On expiry (checked each `poll()` before renders, against `self._clock()`): drop the override AND `self._last_frames.pop(dev, None)` so the next session frame re-sends even if unchanged. `on_solid_cue` also pops `_last_frames[dev]` so the override frame goes out immediately, stamped with the cue's `when`.

Mute: `on_mute_change(dev, True)` → add to `_muted`, install a latched blackout override `((0,0,0), 0.0, None)`, and if `dev` is the Room's canonical dev, silence Room audio (`self._room_bridge.feed_audio(0xB0, 11, 0)` guarded — expression 0; the breath stops re-raising it because muted devs are skipped in `_feed_breath`). `on_mute_change(dev, False)` → discard from `_muted`, drop the override, pop `_last_frames[dev]` (breath resumes feeding cc:11 on its own). While `dev in self._muted`: `_feed_breath` skips it and `_on_light_cue` drops cues for it (light suppression at the transport seam; PlayCue suppression already happened engine-side).

- [ ] **Step 1: Write failing tests** (sketch — adapt constructor/fixture details to the file's existing tests):

```python
def test_solid_override_replaces_outgoing_frame(agent_with_joined_device):
    agent, dev, sent = agent_with_joined_device
    agent._on_solid_cue(dev, (255, 255, 255), 0.9, 5.0, when=agent._clock())
    agent.poll()
    frame = last_leds_payload(sent, dev)
    assert set(frame) == {round(255 * 0.9)}

def test_solid_override_expires_back_to_session(agent_with_joined_device, fake_clock):
    agent, dev, sent = agent_with_joined_device
    agent._on_solid_cue(dev, (255, 255, 255), 0.9, 0.5, when=fake_clock.now)
    agent.poll()
    fake_clock.advance(1.0)
    agent.poll()
    assert set(last_leds_payload(sent, dev)) != {round(255 * 0.9)}

def test_mute_blackout_is_latched_and_suppresses_cues(agent_with_joined_device, fake_clock):
    agent, dev, sent = agent_with_joined_device
    agent._on_mute_change(dev, True)
    agent._on_light_cue(dev, 0xB0, 74, 127)     # dropped
    fake_clock.advance(10.0); agent.poll()
    assert set(last_leds_payload(sent, dev)) == {0}

def test_unmute_restores_session_rendering(agent_with_joined_device):
    agent, dev, sent = agent_with_joined_device
    agent._on_mute_change(dev, True); agent.poll()
    agent._on_mute_change(dev, False); agent.poll()
    assert set(last_leds_payload(sent, dev)) != {0}

def test_room_override_covers_every_fixture_slice(agent_with_bound_room):
    agent, bound, sent = agent_with_bound_room
    agent._on_solid_cue(agent._canonical_room_dev(), (255,255,255), 0.9, 5.0,
                        when=agent._clock())
    agent.poll()
    for dev in bound.values():
        assert set(last_leds_payload(sent, dev)) == {round(255 * 0.9)}
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** per the notes above. Wiring in the same place `attach` sets `on_light_cue`/`on_play_cue`: `game_server.on_solid_cue = self._on_solid_cue`, `game_server.on_mute_change = self._on_mute_change`. Helper:

```python
    def _apply_override(self, dev: str, frame: bytes) -> bytes:
        entry = self._overrides.get(dev)
        if entry is None:
            return frame
        rgb, level, _expires = entry
        pixel = bytes(max(0, min(255, round(ch * level))) for ch in rgb)
        reps = len(frame) // 3 + 1
        return (pixel * reps)[:len(frame)]
```

Expiry sweep in `poll()` before `_render_frames()`:

```python
    def _tick_overrides(self) -> None:
        now = self._clock()
        for dev, (_rgb, _lvl, expires) in list(self._overrides.items()):
            if expires is not None and now >= expires:
                del self._overrides[dev]
                self._last_frames.pop(dev, None)
```

`_on_solid_cue(dev, rgb, level, duration, when)` stores `(rgb, level, None if duration is None else when + duration)` and pops `_last_frames[dev]`. Room note: overrides for the Room are keyed by the **canonical dev** (that's what `_resolve_dev` hands the engine sink); `_render_room` checks `self._overrides.get(canonical)` and applies to `frame` before slicing, and its expiry pop must also clear each bound fixture dev's `_last_frames` entry so post-override slices re-send.

- [ ] **Step 4: Run to verify pass**, then full suite.
- [ ] **Step 5: Commit** — `feat(devicelink): solid-override frame rendering, expiry, and mute suppression`

---

### Task 5: TestBit — the four SURFACE triggers + `win` sample

**Files:**
- Modify: `bits/test/test_bit.py` (samples list line ~83; `trigger_table` property lines ~195-230; the tilt-latch `FireTrigger("play_aurora")` call ~line 256; tap handler's `FireTrigger("flash_device", dev)` stays)
- Test: `tests/test_test_bit.py`

**Interfaces:**
- Consumes: `SolidCue`, `MuteCue`, `TriggerTarget.SURFACE`, `cues.ROOM`.
- Produces: trigger names `flash_device`, `play_aurora`, `stop`, `win` — all `target=TriggerTarget.SURFACE`. Samples list becomes `["click", "chime", "win"]` (the device maps names to assets; an unknown name is the device's business per `_on_play_cue`, so browser/sim clients without a `win` asset degrade silently — documented limitation).

Declarations (exact):

```python
"flash_device": Trigger(
    name="flash_device",
    description="Identify a surface: chime plus 5 s of solid white at 90%.",
    target=TriggerTarget.SURFACE,
    condition=Condition(name="tapped", description="Two-tap on the device",
                        source=ConditionSource.GESTURE_VERB, verb="tap"),
    script=(
        ScriptStep(0.0, PlayCue(TARGET, "chime", "")),
        ScriptStep(0.0, SolidCue(TARGET, (255, 255, 255), 0.9, 5.0)),
    ),
),
"play_aurora": <existing trigger, target=TriggerTarget.SURFACE, script unchanged>,
"stop": Trigger(
    name="stop",
    description="Latch this surface dark and silent until a Play un-mutes it.",
    target=TriggerTarget.SURFACE,
    condition=Condition(name="operator-stop", description="Fired by the operator",
                        source=ConditionSource.ADMIN_MANUAL),
    script=(ScriptStep(0.0, MuteCue(TARGET)),),
),
"win": Trigger(
    name="win",
    description="Win celebration: ascending chime plus a hue flourish.",
    target=TriggerTarget.SURFACE,
    condition=Condition(name="operator-win", description="Fired by the operator",
                        source=ConditionSource.ADMIN_MANUAL),
    script=(
        ScriptStep(0.0, PlayCue(TARGET, "win", "")),
        ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
        ScriptStep(0.3, (TARGET, 0xB0, 74, 60)),
        ScriptStep(0.6, (TARGET, 0xB0, 74, 110)),
        ScriptStep(1.2, (TARGET, 0xB0, 74, 0)),
    ),
),
```

The three-tilt latch changes `FireTrigger("play_aurora")` → `FireTrigger("play_aurora", ROOM)` (SURFACE needs a surface; the latch keeps targeting the Room).

- [ ] **Step 1: Write failing tests**

```python
def test_testbit_declares_four_surface_triggers():
    table = TestBit().trigger_table
    assert set(table.triggers) == {"flash_device", "play_aurora", "stop", "win"}
    assert all(t.target is TriggerTarget.SURFACE
               for t in table.triggers.values())

def test_testbit_trigger_table_validates():
    bit = TestBit()
    validate_trigger_table(bit.trigger_table, set(bit.verb_handlers()))

def test_flash_script_is_chime_plus_white_5s():
    trig = TestBit().trigger_table.triggers["flash_device"]
    kinds = [type(s.cue).__name__ for s in trig.script]
    assert kinds == ["PlayCue", "SolidCue"]
    solid = trig.script[1].cue
    assert solid.rgb == (255, 255, 255) and solid.level == 0.9 and solid.duration == 5.0

def test_stop_script_is_single_mute():
    trig = TestBit().trigger_table.triggers["stop"]
    assert len(trig.script) == 1 and isinstance(trig.script[0].cue, MuteCue)

def test_win_sample_declared_on_player_role():
    role = TestBit().role_table.roles["player"]
    assert "win" in role.samples

def test_tilt_latch_fires_play_aurora_at_room():
    # drive the existing three-tilt latch path exactly as the current latch
    # test does, then assert the emitted FireTrigger carries dev == ROOM
    ...  # adapt the existing latch test in this file; assert .dev == ROOM
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the declarations above; add `"win"` to every role `samples` list that has one (player, and jammer if it declares samples — check line ~83 context).
- [ ] **Step 4: Run `tests/test_test_bit.py`, then the full suite** (TestBit is the reference fixture — expect and fix fallout in engine/console tests that enumerate its triggers or targets).
- [ ] **Step 5: Commit** — `feat(testbit): flash/play_aurora/stop/win as SURFACE triggers, win sample`

---

### Task 6: Console — SURFACE picker with Room entry, MUTED chip, fire plumbing

**Files:**
- Modify: `console/agent.py` (`_devices_view` gains `muted`; `on_mute_change` observation — simplest: `ConsoleAgent` reads `gs.muted` when building device views and the engine's existing `on_devices_change` notify is poked by a small `_notify("on_devices_change")` added to mute/unmute paths in Task 2 — verify whether `_clear_mutes`/`MuteCue` dispatch should call it, and add there if reviewers agree)
- Modify: `console/static/triggers.js` (SURFACE picker; muted tag in picker labels)
- Test: `tests/test_console_agent.py`, `tests/js/trigger_panel_behavior.test.js`

**Interfaces:**
- Consumes: `TriggerTarget.SURFACE` (wire spelling `"SURFACE"` via `trigger_view`'s `target.name`), `gs.muted`, existing `FireTriggerCommand.dev`.
- Produces: device view rows gain `"muted": bool`; SURFACE cards render a picker whose first option is `Room` with value `"@room"`, then every connected device (muted ones labelled `d1 (muted)`); Fire sends `{name, dev: picker.value}` through the existing `fire_trigger` command unchanged.

- [ ] **Step 1: Python failing test** in `tests/test_console_agent.py`:

```python
def test_devices_view_carries_muted_flag(console_agent_with_device):
    agent, gs, dev = console_agent_with_device
    gs.muted.add(dev)
    view = agent.snapshot()["devices"]
    assert [d for d in view if d["dev"] == dev][0]["muted"] is True
```

- [ ] **Step 2: JS failing tests** in `tests/js/trigger_panel_behavior.test.js` (follow its jsdom/module harness):

```js
test("SURFACE card renders picker with Room first", () => {
  renderTriggers([surfaceTrigger("flash_device")]);
  const picker = document.getElementById("triggerDev_flash_device");
  expect(picker.options[0].value).toBe("@room");
  expect(picker.options[0].textContent).toBe("Room");
});

test("devices_changed refreshes SURFACE pickers without rebuilding cards", () => {
  renderTriggers([surfaceTrigger("flash_device")]);
  const card = cardEl("flash_device");
  fireDevicesChanged([{dev: "d1", muted: false}]);
  expect(cardEl("flash_device")).toBe(card);           // same node
  const picker = document.getElementById("triggerDev_flash_device");
  expect([...picker.options].map(o => o.value)).toEqual(["@room", "d1"]);
});

test("muted device is labelled in the picker", () => {
  renderTriggers([surfaceTrigger("flash_device")]);
  fireDevicesChanged([{dev: "d1", muted: true}]);
  const picker = document.getElementById("triggerDev_flash_device");
  expect(picker.options[1].textContent).toBe("d1 (muted)");
});
```

- [ ] **Step 3: Run both to verify failure** (`.venv/bin/python -m pytest tests/test_console_agent.py -v`; the JS suite via the command `tests/js/` already uses — check `package.json`/README in that directory and run the same way existing JS tests run).
- [ ] **Step 4: Implement.** Python: add `"muted": dev in self.game_server.muted` to the device row builder in `_devices_view`; ensure a mute/unmute reaches the console — in Task 2's mute paths add `self._notify("on_devices_change")` (engine) so the existing `devices_changed` broadcast fires. JS: in `buildCard`, `if (trigger.target === "DEVICE" || trigger.target === "SURFACE")` build the picker; `fillDevicePicker` gains the Room-first option for SURFACE (pass the target kind or a `withRoom` flag) and labels muted devices; `onDevicesChanged` keeps refreshing pickers in place (extend `triggerDevices` to carry `{dev, muted}` objects). Keep `currentDeviceTargets` naming but include SURFACE cards.
- [ ] **Step 5: Run both suites to verify pass.**
- [ ] **Step 6: Commit** — `feat(console): SURFACE trigger pickers with Room entry and muted flags`

---

### Task 7: Card compaction — reconcile classes to the redesign CSS, 3-across

**Files:**
- Modify: `console/static/triggers.js` (`buildCard` class names/markup)
- Modify: `console/static/terrarium.css` (`.triggrid` min width; wrap rules)
- Test: `tests/js/trigger_panel_behavior.test.js`

**Background (verified):** `buildCard` emits `card trigger` / `trigrid` / bare `<p>`s, while the 2026-08-25 redesign CSS styles `.trig`, `.triggrid`, `.trighead`, `.desc`, `.cond`, `.scriptbar`/`.expander`/`.script`, `.firerow`, `.fired-line` — the panel is not currently picking up the redesign card styles at all. This task reconciles the markup to the CSS that already exists, then compacts.

- [ ] **Step 1: Write failing JS tests**

```js
test("card uses the redesign classes", () => {
  renderTriggers([surfaceTrigger("t")]);
  const grid = document.querySelector(".triggrid");
  expect(grid).not.toBeNull();
  const card = grid.firstChild;
  expect(card.className).toContain("trig");
  expect(card.querySelector(".desc")).not.toBeNull();
  expect(card.querySelector(".cond")).not.toBeNull();
  expect(card.querySelector(".fired-line")).not.toBeNull();
});
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** In `buildCard`: card class `"trig"`, grid class `"triggrid"`, description `mk("p", "desc", ...)`, condition `mk("p", "cond", ...)`, fired line class `"fired-line"`; keep `trighead`, `firerow`, the `<details class="script">` block (restyle `details.script` in CSS if the redesign's `.script` show/hide expected the `.open` class — verify against `shell.js`/other panels' expander usage and match whichever pattern the redesign panels use). CSS compaction in `terrarium.css`:

```css
.triggrid { grid-template-columns: repeat(auto-fill, minmax(215px, 1fr)); }
.trighead h3 { font-size: 14px; overflow-wrap: anywhere; }
.trig .desc { font-size: 11.5px; }
.trig select { min-width: 0; flex: 1; max-width: 100%; }
.trig .firerow { flex-wrap: wrap; }
.fired-line { overflow-wrap: anywhere; }
```

(exact values tunable at live-verify; the invariant is 3 columns at the 1460px content width with the 320px rail — 1460 − 320 − gaps leaves ~1100px for `.maincol`, and 3 × 215px + gaps fits.)

- [ ] **Step 4: Run JS tests to verify pass; run the full JS suite.**
- [ ] **Step 5: Commit** — `feat(console): compact trigger cards to instrument form factor, adopt redesign classes`

---

### Task 8: Full-suite gate + live-verify checklist

**Files:**
- Modify: `docs/superpowers/specs/2026-08-26-trigger-cards-and-surface-triggers-design.md` (append a "Live verification" results section after the run)

- [ ] **Step 1: Run the complete offline suite**: `.venv/bin/python -m pytest tests -v` — 0 failures.
- [ ] **Step 2: Run the JS suite** the same way `tests/js/` is normally run — 0 failures.
- [ ] **Step 3: Prepare the live-verify checklist** (executed by the operator/session against a real Arco, per the house pattern; not automatable offline):
  - `python -m harness.run_stack` (TEST room), open the Console.
  - Triggers panel shows 4 cards, 3-across at full width, nothing overflowing.
  - Fire `flash_device` at the Room: both sim canvases go solid white ~90% for 5 s, then resume ambient drift; at a joined sim device: its canvas flashes and (if the client has a `chime` asset) sounds.
  - Fire `stop` at the Room: canvases go dark, drone silent; stays dark >10 s.
  - Fire `play_aurora` at the stopped Room: un-mutes, aurora script runs.
  - Fire `win` at a device: hue flourish visible; `win` PlayCue observable in the device client log even if no asset.
  - Card status lines update in place (no list rebuild).
- [ ] **Step 4: Commit docs** — `docs(spec): record live-verify results for surface triggers`

---

## Self-Review (performed)

- **Spec coverage:** layout → Task 7; SolidCue → Tasks 1, 2, 4; SURFACE → Tasks 3, 6; mute latch + un-mute-on-play → Tasks 2, 4; four TestBit triggers + win sample → Task 5; Console picker/MUTED chip → Task 6; verification → Task 8. Deferred items (o2audioio-out, real win asset) have no tasks by design.
- **Type consistency:** `SolidCue(dev, rgb, level, duration, when=None)` and sink `(dev, rgb, level, duration, when)` used identically in Tasks 1/2/4; `MuteCue(dev)` and `on_mute_change(dev, bool)` in Tasks 1/2/4/6; `TriggerTarget.SURFACE` wire spelling `"SURFACE"` in Tasks 3/6.
- **Known judgment calls left to the executor+reviewer:** where the engine pokes `on_devices_change` for mute visibility (Task 6 names the option); `details.script` vs `.expander` markup pattern (Task 7 says match the redesign's other panels).
