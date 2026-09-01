# Per-Fixture Instruments, Diagnostics, and ABORT Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each Room fixture an individually-addressable instrument with its own audio channel and mute, fix the diagnostics button order, refuse reserved function names on Bits, and stop ABORT from killing the whole stack.

**Architecture:** The Room keeps ONE shared LightSession (sliced per fixture) but loses its audio channel entirely: each audio-capable bound fixture gets its own `AudioBridge` voice, and `RoomBridge` becomes light-only. The engine stops collapsing an explicitly-addressed fixture dev to the canonical dev, so Stop/Flash/Ping land on the fixture the operator picked. Mute, solid overrides, and cue purges become per-fixture at the transport seam. The persistent device harness treats a hub-down send failure as "wait for reconnect" instead of crashing.

**Tech Stack:** Python 3 stdlib only in `control/` (no third-party imports there), pytest, vanilla ES-module JS for the Console, node-driven JS tests run through pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-per-fixture-instruments-and-diagnostics-design.md`

## Global Constraints

- Run tests ONLY via `.venv/bin/python -m pytest tests -q` (bare python3 gives a phantom luxaeterna import error). A fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first.
- Baseline before this slice: 1887 passed, 1 skipped. Full suite must end green.
- No em dashes in any authored text (docs, comments, commit messages). The repo's existing double-hyphen comment style is fine.
- `control/` and `devicelink/` modules must not import pyarco or luxaeterna.
- `GameServer.fire_function` and everything it calls must never raise (existing contract).
- Do not modify `~/projects/arco/o2litepy` (external repo).
- Frequent commits: every task ends with its own commit.

---

### Task 1: Split dev_strip into dev_strip_main and dev_strip_accent

**Files:**
- Create: `instruments/dev_strip_main.toml`, `instruments/dev_strip_accent.toml` (from `instruments/dev_strip.toml`)
- Delete: `instruments/dev_strip.toml`
- Modify: `terrarium.toml:15` and `terrarium.toml:36` (the two `instrument = "dev_strip"` lines)
- Test: existing tests that reference the catalog name (grep-driven sweep)

**Interfaces:**
- Produces: catalog entries named `dev_strip_main` and `dev_strip_accent`, identical capabilities to today's `dev_strip` (`light.surface`, `audio.flsyn`; accepted cues `midi`/`play`/`solid`/`mute`; same eight scripted functions). Later tasks rely on these names resolving through `control/catalog.py` (file stem = instrument name).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_terrarium_config.py`:

```python
def test_test_room_fixtures_carry_distinct_instruments():
    config = load_terrarium_config(Path("terrarium.toml"))
    fixtures = config.rooms["TEST"].profile.fixtures
    names = [f.instrument.name for f in fixtures]
    assert names == ["dev_strip_main", "dev_strip_accent"]
```

Match the file's existing import style and how its other tests load the real `terrarium.toml` (there are existing tests loading it; copy their setup exactly, including any `instrument_paths` plumbing).

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q -k distinct`
Expected: FAIL (names are `["dev_strip", "dev_strip"]`)

- [ ] **Step 3: Split the catalog files**

```bash
git mv instruments/dev_strip.toml instruments/dev_strip_main.toml
cp instruments/dev_strip_main.toml instruments/dev_strip_accent.toml
```

Edit the first line of each: `description = "Simulated dev strip (main), no ambient"` and `description = "Simulated dev strip (accent), no ambient"`. In `terrarium.toml`, point the `main` fixture at `dev_strip_main` and the `accent` fixture at `dev_strip_accent`.

- [ ] **Step 4: Sweep the remaining references**

Run `grep -rn "dev_strip" --include="*.py" --include="*.toml" --include="*.js" .` (excluding `.venv`, `docs/`). For every hit that refers to the real catalog entry (loads `instruments/dev_strip.toml`, resolves the name through the catalog, or loads the real `terrarium.toml`), switch to `dev_strip_main` (or `dev_strip_accent` where the test is exercising the accent fixture). Hits that are purely synthetic local fixtures (e.g. the ad-hoc maps in `tests/js/diagnostics_row.test.js`, or Python tests that construct their own `Instrument(...)` literal named "dev_strip" without touching the catalog) may keep the old string, but prefer renaming for consistency where the edit is trivial. Expect roughly 28 test references across `tests/test_catalog.py`, `tests/test_devicelink_agent.py`, `tests/test_terrarium_cycle.py`, `tests/test_instrument_scripted.py`, `tests/test_console_agent.py`, `tests/test_terrarium_config.py`, `tests/test_room_view.py`.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all green (1888 passed, 1 skipped, plus the new test).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(instruments): TEST room fixtures carry distinct dev_strip_main/dev_strip_accent instruments"
```

---

### Task 2: Refuse reserved names on Bit FunctionTables; delete TestBit's stop

**Files:**
- Modify: `control/functions.py:384` (the `owner == "instrument"` guard on the RESERVED_NAMES check)
- Modify: `bits/test/test_bit.py:250-260` (the `"stop"` Function entry)
- Test: `tests/test_functions.py`, plus updates to any test asserting Bit-declared shadowing (`tests/test_fire_ladder.py`, `tests/test_test_bit.py`)

**Interfaces:**
- Consumes: `RESERVED_NAMES` from `control/builtins.py` (`{"flash", "stop", "ping"}`).
- Produces: `validate_function_table(..., owner="bit")` raises `ValueError` for reserved names. Later tasks rely on the built-in `stop` always resolving through `fire_function` rungs 2/3, never rung 1.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_functions.py` (mirror the file's existing helpers for building a minimal valid FunctionTable and its `validate_function_table` call signature):

```python
def test_bit_declared_reserved_name_is_refused():
    table = _table_with_function(_scripted_function(name="stop"))
    with pytest.raises(ValueError, match="reserved built-in"):
        validate_function_table(table, set(), owner="bit")
```

Use whatever local helper pattern the file already has for constructing tables; if none fits, build the Function/FunctionTable literals inline the way neighboring tests do.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_functions.py -q -k reserved`
Expected: FAIL (no ValueError raised for owner="bit")

- [ ] **Step 3: Make the validator refuse both owners**

In `control/functions.py`, change:

```python
        if owner == "instrument" and function_decl.name in RESERVED_NAMES:
```

to:

```python
        if function_decl.name in RESERVED_NAMES:
```

The message already reads correctly for both owners. Update the docstring's owner paragraph if it still describes the refusal as instrument-only (the current docstring already claims "refused for both owners", so the code is catching up to it).

- [ ] **Step 4: Delete TestBit's stop and fix shadowing tests**

Remove the whole `"stop": Function(...)` entry from `bits/test/test_bit.py`'s `function_table()` (the SURFACE-target, admin-manual, single `MuteCue(TARGET)` entry around lines 250-260). Then run the suite and fix every failure it surfaces:
- tests asserting TestBit declares four SURFACE functions or a `stop` card now expect three,
- any `tests/test_fire_ladder.py` test that loads a Bit declaring `stop`/`flash`/`ping` to prove rung-1 shadowing must flip to asserting the load is REFUSED with a located ValueError (keep the test, invert its expectation, rename it accordingly).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(functions): reserved built-in names are refused on Bit FunctionTables; TestBit drops its stop"
```

---

### Task 3: Engine: explicit fixture targeting is never collapsed

**Files:**
- Modify: `control/engine.py` (`fire_function`, around lines 850-885: the two `_collapse_room_fanout` call sites)
- Test: `tests/test_fire_ladder.py` (or `tests/test_engine_functions.py`, whichever already exercises fire_function against a multi-fixture Room; follow the existing fixtures)

**Interfaces:**
- Consumes: `ROOM` (`"@room"`) and `ALL` (`"@all"`) sentinels from `control/cues.py`; `FunctionTarget.SURFACE`.
- Produces: `fire_function(name, dev=<fixture dev>)` dispatches cues carrying that fixture's real dev (MuteCue.dev, SolidCue.dev, PlayCue.dev, note tuples). `fire_function(name, dev="@all")` resolves rungs 2/3 per real dev with NO collapse, so every Room fixture fires its own builtin. Task 4/5 rely on cues reaching the transport keyed by real fixture devs.

- [ ] **Step 1: Write the failing tests**

In the chosen test file, using its existing two-fixture Room fixture setup (the `_collapse_room_fanout` tests there show how a bound two-fixture room is built; reuse that helper):

```python
def test_explicit_fixture_fire_is_not_collapsed(two_fixture_gs):
    gs, main_dev, accent_dev = two_fixture_gs
    gs.fire_function("stop", fired_by="admin-manual", dev=accent_dev)
    assert accent_dev in gs.muted
    assert main_dev not in gs.muted

def test_all_fire_reaches_every_fixture(two_fixture_gs):
    gs, main_dev, accent_dev = two_fixture_gs
    gs.fire_function("stop", fired_by="admin-manual", dev="@all")
    assert {main_dev, accent_dev} <= gs.muted
```

Adapt names to the file's actual fixtures. The fixtures' instruments must have a `stop` builtin (any `light.*` or `audio.*` capability).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_fire_ladder.py -q -k "collapsed or reaches_every"`
Expected: FAIL (accent collapsed to main; only main muted)

- [ ] **Step 3: Implement**

In `fire_function`, after `devs = self._resolve_target(target, dev)` compute:

```python
            explicit_surface = (target is FunctionTarget.SURFACE
                                and dev not in (ROOM, ALL))
```

Rung 1 becomes:

```python
                fan = devs if explicit_surface else self._collapse_room_fanout(devs)
                cues = expand_script(decl, at, fan)
```

Rungs 2/3: iterate `devs` directly instead of `self._collapse_room_fanout(devs)` (the per-dev ladder resolves each fixture's own instrument; collapsing there is what folded the accent into main). Add a short comment on the rung-2/3 loop stating that per-dev resolution deliberately never collapses: each fixture is its own surface, and the light half of a non-canonical fixture's MIDI is dropped at the transport seam (Task 5).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green. Existing collapse tests for ROOM-targeted rung-1 scripts must still pass (collapse is unchanged for non-explicit lanes).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(engine): explicit fixture targeting is never collapsed; @all resolves per real dev"
```

---

### Task 4: Per-fixture audio voices; RoomBridge goes light-only

**Files:**
- Modify: `control/audio.py` (add `AudioBridge.silence`)
- Modify: `control/room_bridge.py` (remove the audio sink: `bind` loses `audio=`, delete `feed_audio`, `RoomAudioSink`, `FakeRoomAudioSink`; `shutdown` becomes light-release only or is deleted in favor of `release`)
- Modify: `devicelink/agent.py` (`_setup_room`, `unwire_room` (the block at lines 383-401), `_on_light_cue`, `_render_room`'s drain, delete `_RoomAudioSink`, replace `_room_audio_dev` with `_room_audio_devs: set[str]`)
- Test: `tests/test_audio.py`, `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `AudioBridge.on_grant(dev, role)` / `on_release(dev)` / `feed_midi(dev, status, d1, d2)` / `start_drone(dev)` (all already dev-keyed), `Role` from `control/roles.py`.
- Produces: `AudioBridge.silence(dev)` (stops the drone if sounding, then `voice.all_off()`; no release, the voice stays granted). `DeviceLinkAgent._room_audio_devs: set[str]`, the granted fixture devs. `_room_cues` payloads become `(dev, status, d1, d2)`. Task 5's mute path relies on `silence` and on the dev-tagged queue.

- [ ] **Step 1: Write the failing AudioBridge test**

Add to `tests/test_audio.py` (the file already has FakePool/FakeVoice and a granted-role helper; reuse them):

```python
def test_silence_stops_drone_and_flushes_voice(granted_bridge):
    bridge, dev, voice = granted_bridge   # a grant with a drone sounding
    bridge.silence(dev)
    assert ("all_off",) in voice.sent
    # a second silence is a no-op, and the voice is still granted:
    bridge.silence(dev)
    bridge.feed_midi(dev, 0x90, 60, 100)
    assert ("note_on", 60, 100) in voice.sent
```

- [ ] **Step 2: Run to verify failure, then implement `silence`**

Run: `.venv/bin/python -m pytest tests/test_audio.py -q -k silence` (FAIL: no attribute). Then in `control/audio.py` after `stop_drone`:

```python
    def silence(self, dev: str) -> None:
        """Quiet a granted voice in place: drone off (if sounding) and every
        note flushed. The grant survives, so the next feed sounds again --
        this is Stop's audio half, not a release."""
        entry = self._devices.get(dev)
        if entry is None:
            return
        if entry.drone_key is not None:
            entry.voice.note_off(entry.drone_key)
            entry.drone_key = None
        entry.voice.all_off()
```

Re-run: PASS.

- [ ] **Step 3: Write the failing agent tests**

Add to `tests/test_devicelink_agent.py`, following its existing pattern for building an agent with a two-fixture bound room and a fake `room_audio` (the file already fakes AudioBridge grants for the canonical dev; extend that fake to record grants per dev):

```python
def test_every_audio_fixture_gets_its_own_voice(two_fixture_agent):
    agent, audio, main_dev, accent_dev = two_fixture_agent
    assert set(audio.granted) == {main_dev, accent_dev}
    assert agent._room_audio_devs == {main_dev, accent_dev}

def test_fixture_midi_feeds_that_fixtures_voice(two_fixture_agent):
    agent, audio, main_dev, accent_dev = two_fixture_agent
    agent._on_light_cue(accent_dev, 0x90, 57, 100, when=agent._clock())
    agent._render_room()
    assert (accent_dev, 0x90, 57, 100) in audio.fed
    assert all(f[0] != main_dev for f in audio.fed)
```

Also assert the welcome plays once, not per fixture, if the existing fakes track welcomes; otherwise cover it at the AudioBridge level: grant two fixtures where the second grant's role has `welcome=None` and assert `_pending_offs` grew once.

- [ ] **Step 4: Implement the agent changes**

In `devicelink/agent.py`:

1. Replace `self._room_audio_dev: str | None = None` with `self._room_audio_devs: set[str] = set()`.
2. In `_setup_room`, move audio granting ABOVE the ambient early-return (audio must exist even when there is no light manifest at all, so Bit-less ping works on TEST). After `role` and `ambient_ugen` are determined, replace the whole canonical-grant block with:

```python
        if self._room_audio is not None:
            for d in list(self._room_audio_devs):
                self._room_audio.on_release(d)
            self._room_audio_devs.clear()
            audio_role = role
            if audio_role is None and ambient_ugen:
                audio_role = Role(name="ambient", role_class=RoleClass.ROOM,
                                  capacity=None, scored=False,
                                  ugen_manifest=ambient_ugen)
            first = True
            for fixture in self._room_profile.fixtures:
                d = gs.room.bound.get(fixture.name)
                if d is None:
                    continue
                caps = fixture.instrument.capabilities
                if not any(c.startswith("audio.") for c in caps):
                    continue
                grant_role = audio_role
                if grant_role is None:
                    # No Bit ROOM role and no ambient declaration: a
                    # minimal flsyn pass-through so the built-ins (ping's
                    # note pair, stop's silence) have a voice to land on.
                    grant_role = _DEFAULT_FIXTURE_ROLE
                if not first:
                    grant_role = replace(grant_role, welcome=None)
                self._room_audio.on_grant(d, grant_role)
                self._room_audio_devs.add(d)
                if role is None and first and grant_role.ugen_manifest.get(
                        "instruments"):
                    self._room_audio.start_drone(d)
                first = False
```

with module-level (near `_RoomAudioSink`, which this task deletes):

```python
_DEFAULT_FIXTURE_ROLE = Role(
    name="fixture-builtin", role_class=RoleClass.ROOM, capacity=None,
    scored=False,
    ugen_manifest={"instruments": [
        {"lanes": [{"source": "cc:11", "dest": "cc:11"}]}]})
```

Verify `Role` in `control/roles.py` is a dataclass with a `welcome` field and import `replace` from `dataclasses`; if `Role` is not a dataclass, build the welcome-less copy by constructing a new `Role` with the same fields. Check `Role`'s required constructor arguments and match them (the existing ambient stand-in shows the pattern).

3. `_room_bridge.bind(canonical, light=self._room_light)` (no audio). Update `control/room_bridge.py` accordingly: `bind(self, dev, light=None)`, delete `feed_audio`, `RoomAudioSink`, `FakeRoomAudioSink`, and fold `shutdown` into `release` (grep callers of `RoomBridge.shutdown` and `feed_audio` first and update them; `tests/test_room_bridge.py` style tests move with it).
4. `unwire_room`'s audio block (lines 383-401) becomes: stop drones and release every dev in `_room_audio_devs`, then clear the set.
5. `_on_light_cue`: room-dev pushes become `self._room_cues.push(when, (dev, status, data1, data2), now=now)`, and immediately after the push, a non-canonical room dev returns (its light half is dropped at this seam; see the spec's scope split):

```python
        if self._is_room_dev(dev) and self._room_bridge is not None:
            self._room_cues.push(when, (dev, status, data1, data2), now=now)
            if dev != self._canonical_room_dev():
                return
```

6. `_render_room`'s drain becomes:

```python
        for (cue_dev, status, d1, d2) in self._room_cues.due(self._clock()):
            if self._room_audio is None or cue_dev not in self._room_audio_devs:
                continue
            try:
                self._room_audio.feed_midi(cue_dev, status, d1, d2)
            except Exception:
                logger.exception("fixture feed_midi failed for %s", cue_dev)
```

7. Delete `_RoomAudioSink`. Fix the existing canonical-only room-audio tests in `tests/test_devicelink_agent.py` to the new per-fixture contract.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(audio): per-fixture voices; RoomBridge is light-only; the Room owns no audio channel"
```

---

### Task 5: Per-fixture overrides and mute at the transport seam

**Files:**
- Modify: `devicelink/agent.py` (`_on_mute_change` at lines 508-539, `_render_room`'s override application, `_apply_override` if it assumes whole-frame length)
- Test: `tests/test_devicelink_agent.py`, `tests/test_devicelink_frames.py`

**Interfaces:**
- Consumes: `AudioBridge.silence(dev)` and `_room_audio_devs` from Task 4; engine mutes arriving keyed by real fixture devs from Task 3.
- Produces: `_overrides[fixture_dev]` applied to that fixture's slice in `_render_room`; mute of fixture F purges only F's queues and silences only F's voice. No canonical-dev special case remains in the mute path.

- [ ] **Step 1: Write the failing tests**

```python
def test_mute_of_one_fixture_blacks_only_its_slice(two_fixture_agent):
    agent, audio, main_dev, accent_dev = two_fixture_agent
    agent._on_mute_change(accent_dev, True)
    frames = render_and_collect(agent)   # follow the file's frame-capture helper
    assert frames[accent_dev] == b"\x00" * len(frames[accent_dev])
    assert frames[main_dev] != b"\x00" * len(frames[main_dev])

def test_mute_of_one_fixture_silences_only_its_voice(two_fixture_agent):
    agent, audio, main_dev, accent_dev = two_fixture_agent
    agent._on_mute_change(accent_dev, True)
    assert audio.silenced == [accent_dev]

def test_solid_cue_at_one_fixture_paints_only_its_slice(two_fixture_agent):
    agent, audio, main_dev, accent_dev = two_fixture_agent
    agent._on_solid_cue(accent_dev, (255, 255, 255), 0.9, 5.0, agent._clock())
    frames = render_and_collect(agent)
    assert frames[accent_dev] != frames_before[accent_dev]
    assert frames[main_dev] == frames_before[main_dev]
```

Use the file's existing frame-capture pattern (`_emit_room_frame` hook or `_send` capture); `frames_before` is a first render taken before the cue. Extend the fake AudioBridge with a `silenced` list.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -q -k "only_its"`
Expected: FAIL (overrides keyed by canonical apply to the whole frame; accent mute does not black its slice)

- [ ] **Step 3: Implement**

In `_render_room`, delete `frame = self._apply_override(canonical, frame)` and apply per fixture instead:

```python
            slice_ = frame[start:start + count]
            slice_ = self._apply_override(dev, slice_)
```

Read `_apply_override` first: it must size its output to the input bytes it was given. If it currently derives length from the room profile, change it to use `len(frame)` of its argument.

In `_on_mute_change`, replace the canonical-dev special case with the per-fixture path:

```python
        if muted:
            self._muted.add(dev)
            self._overrides[dev] = ((0, 0, 0), 0.0, None)
            self._last_frames.pop(dev, None)
            self._light_cues.purge(lambda payload: payload[0] == dev)
            if self._is_room_dev(dev):
                self._room_cues.purge(lambda payload: payload[0] == dev)
                if (self._room_audio is not None
                        and dev in self._room_audio_devs):
                    try:
                        self._room_audio.silence(dev)
                    except Exception:
                        logger.exception("mute silence failed for %s", dev)
        else:
            self._muted.discard(dev)
            self._overrides.pop(dev, None)
            self._last_frames.pop(dev, None)
```

Update the method docstring: mute is per fixture now; a Stop at `@all` arrives as one mute per fixture, so there is no whole-room case left here.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green. Watch `tests/test_devicelink_frames.py`: any test asserting a ROOM SolidCue paints the whole frame must be updated to the new per-slice contract (spec section 5 accepts that a ROOM-addressed SolidCue now paints only the canonical fixture's slice).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(devicelink): per-fixture overrides and mute; Stop at a fixture blacks and silences only that fixture"
```

---

### Task 6: Console: diag order Stop/Flash/Ping, fixture labels, drop the "room" key

**Files:**
- Modify: `console/static/functions.js` (the two `["flash", "stop", "ping"]` arrays at lines 198 and 217, `fillDevicePicker`, `onDevicesChanged`)
- Modify: `console/agent.py` (`_current_surface_instruments` drops the `"room"` key block; `_devices_view` passes the fixture name)
- Modify: `console/protocol.py` (`device_view` gains `fixture=None`)
- Test: `tests/js/diagnostics_row.test.js`, `tests/test_console_agent.py`, `tests/test_console_protocol.py` (run through `tests/test_console_js.py` for the JS side)

**Interfaces:**
- Consumes: `surface_instruments` dev-keyed entries (Task 1's distinct instruments make each fixture resolve its own builtins).
- Produces: `device_view(info, role_name, url=None, muted=False, fixture=None)` adding a `"fixture"` key; picker option labels `"<dev> (<fixture>)"` for bound fixtures; diagnostics buttons in order Stop, Flash, Ping.

- [ ] **Step 1: Write the failing tests**

Python, in `tests/test_console_agent.py` (follow its existing surface_instruments test setup):

```python
def test_surface_instruments_has_no_room_key(console_agent_with_room):
    out = console_agent_with_room._current_surface_instruments()
    assert "room" not in out

def test_devices_view_labels_bound_fixtures(console_agent_with_room):
    rows = console_agent_with_room._devices_view()
    by_dev = {r["dev"]: r for r in rows}
    assert by_dev[MAIN_DEV]["fixture"] == "main"
    assert by_dev[ACCENT_DEV]["fixture"] == "accent"
    assert by_dev[LOBBY_DEV]["fixture"] is None
```

JS, in `tests/js/diagnostics_row.test.js`: find the assertions over the diagnostics row's buttons and assert their order is `["Stop", "Flash", "Ping"]`; also remove `"room"` from its `SURFACE_INSTRUMENTS` fixture (and any assertion that consumed it). If no order assertion exists yet, add one that reads the diag row's button texts in DOM order.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py tests/test_console_js.py -q`
Expected: FAIL on the new assertions.

- [ ] **Step 3: Implement**

`console/agent.py` `_current_surface_instruments`: delete the `if "room" not in out: out["room"] = ...` block and its comment. `_devices_view`:

```python
        bound_fixtures = {}
        if gs.room is not None and gs.room.bound:
            bound_fixtures = {d: name for name, d in gs.room.bound.items()}
        ...
            out.append(protocol.device_view(
                info, role_name, urls.get(info.dev), info.dev in gs.muted,
                bound_fixtures.get(info.dev)))
```

`console/protocol.py`:

```python
def device_view(info, role_name, url=None, muted=False, fixture=None) -> dict:
    return {"dev": info.dev, "name": info.name, "role": role_name, "url": url,
            "muted": muted, "instrument": info.carried.name,
            "fixture": fixture}
```

`console/static/functions.js`: change both arrays to `["stop", "flash", "ping"]` with a one-line comment `// Stop first: it is the panic button.` In `onDevicesChanged`, carry `fixture`: `fnDevices = (devices || []).map((d) => ({ dev: d.dev, muted: !!d.muted, fixture: d.fixture || null }));` and in `fillDevicePicker` label:

```javascript
    const base = fixture ? `${dev} (${fixture})` : dev;
    option.textContent = muted ? `${base} (muted)` : base;
```

(destructure `fixture` in the loop alongside `dev` and `muted`).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green, including every `tests/js/*.test.js` via `test_console_js.py`. Other JS tests that feed `devices_changed` rows without a `fixture` field must still pass (the `|| null` default covers them); fix any that assert exact option text.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(console): Stop/Flash/Ping order, fixture-labeled picker, surface_instruments drops the room key"
```

---

### Task 7: o2_shroom survives a hub-down (ABORT resilience)

**Files:**
- Modify: `harness/o2_shroom.py` (`reconnect_recheck` at lines 205-252; the in-loop `send_hello`/`send_cmd` call sites at lines 491-503, 637-645, 704-706)
- Test: `tests/test_o2_shroom.py`

**Interfaces:**
- Consumes: o2litepy behavior (NOT modified): on hub loss `send_cmd` raises `AssertionError("cannot send")`, and `bridge_id` may read negative while disconnected; on reconnect a new positive `bridge_id` is stamped.
- Produces: `reconnect_recheck` returns `(current, None)` while the hub is away (never raises, never SystemExits on a send failure); a module-level `HUB_AWAY_NOTE` string printed once per transition, so `run_stack` log watchers can key on it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_o2_shroom.py` (it already has fake-o2lite doubles for `reconnect_recheck`; extend that fake):

```python
def test_recheck_treats_negative_bridge_id_as_hub_away(capsys):
    o2 = FakeO2(bridge_id=-1)
    bridge_id, problem = reconnect_recheck(o2, "ie1", 7)
    assert (bridge_id, problem) == (-1, None)
    assert "hub connection lost" in capsys.readouterr().out

def test_recheck_survives_send_failure_during_verify():
    o2 = FakeO2(bridge_id=9)

    def dead_verify(o2lite, dev, timeout, resend_interval):
        raise AssertionError("cannot send")

    bridge_id, problem = reconnect_recheck(o2, "ie1", 7, verify=dead_verify)
    assert problem is None
    assert bridge_id == 7   # unchanged, so the next lap re-detects and retries
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py -q -k "hub_away or survives_send"`
Expected: first FAILs (verify is attempted at bridge_id -1), second FAILs (AssertionError propagates).

- [ ] **Step 3: Implement `reconnect_recheck` hardening**

At the top of the changed-bridge-id branch:

```python
    if current is None or (isinstance(current, int) and current < 0):
        # The hub itself went away (Console ABORT takes Arco down by
        # design). Not an error: idle, keep polling, and re-verify when
        # o2lite reconnects and stamps a real bridge id.
        print(f"{HUB_AWAY_NOTE} (bridge id {previous_bridge_id} -> {current})")
        return current, None
```

and wrap the verify call:

```python
    try:
        verified = verify(o2lite, dev, timeout=10.0, resend_interval=2.0)
    except (AssertionError, OSError):
        print(f"{HUB_AWAY_NOTE} (send failed during the ownership re-check; "
              f"will retry)")
        return previous_bridge_id, None
    if verified:
        return current, None
```

with `HUB_AWAY_NOTE = "hub connection lost; waiting for it to return"` at module level. Returning `current` for the negative case (and `previous` for the mid-verify failure) keeps the once-per-transition print: equal ids are silent on later laps.

- [ ] **Step 4: Guard the loop's own sends**

Inside `main()` where `send_hello` is defined, wrap its body:

```python
    def send_hello() -> None:
        try:
            o2lite.send_cmd("/game/hello", 0, hello_typespec, *hello_args)
            o2lite.send_cmd("/game/canvas", 0, "ss", args.dev, canvas_url)
        except (AssertionError, OSError):
            pass   # hub away; the heartbeat/join retry loop resends later
```

and give the loop's raw join sends the same guard via a small helper defined next to `send_hello`:

```python
    def send_join() -> None:
        try:
            o2lite.send_cmd("/game/join", 0, "ss", args.dev, args.node)
        except (AssertionError, OSError):
            pass
```

Replace the three `o2lite.send_cmd("/game/join", ...)` call sites (initial, in-loop retry, post-round rejoin) with `send_join()`. The UDP tilt send (`o2lite.send`) does not assert on a lost hub (it only prints) and stays as is.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "fix(harness): o2_shroom rides out a hub-down instead of crashing, so ABORT no longer kills the stack"
```

---

### Task 8: Full verification and deep-dive sync

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (new slice section; superseded notes on the 2026-08-31 instrument-scripted-functions section's shadowing bullet and the surface_instruments "room"-key bullet)

**Interfaces:**
- Consumes: everything above, merged on this branch.

- [ ] **Step 1: Full suite, recorded**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green; record the exact passed/skipped counts for the doc section and the PR body.

- [ ] **Step 2: Update the deep-dive**

In `docs/MM_TERRARIUM.md`:
1. Add a new `###` section after the console-load-stabilization entry titled "Per-fixture instruments, operator Stop, and ABORT resilience (2026-09-01)" summarizing, in the doc's established voice: the Room-owns-no-audio decision and per-fixture voices, the never-collapse rule for explicit fixture targets, per-fixture mute/overrides, dev_strip_main/dev_strip_accent, the Stop/Flash/Ping order, reserved names refused on both owners, the o2_shroom hub-away hardening, and the named follow-up (per-fixture light sessions and cross-fixture light effects). Link the spec path. Include the new test baseline.
2. Add a superseded note to the 2026-08-31 section's "a Bit that declares its own stop Function shadows the built-in by design" text pointing at the new section.
3. Add a superseded note wherever `surface_instruments["room"]` / first-bound-fixture is described (the Console-changes bullet of the 2026-08-31 section).
No em dashes in the new text.

- [ ] **Step 3: Commit**

```bash
git add docs/MM_TERRARIUM.md
git commit -m "docs: MM_TERRARIUM deep-dive sync for per-fixture instruments slice"
```

---

## Live verification checklist (operator, after merge, TEST room)

Not automatable here; carried from the spec for the PR body:
- Stop/Flash/Ping at `sim-room-accent` acts on the accent only; Stop silences it.
- Stop at All silences everything; any play un-mutes.
- ABORT leaves ie1 alive ("hub connection lost; waiting for it to return" in ie1.log), Load Room then Load Bit recovers.
- Diag row reads Stop, Flash, Ping.
