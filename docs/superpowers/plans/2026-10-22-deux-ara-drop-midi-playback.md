# Deux Ara Drop MIDI Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Bit play multi-track MIDI files in time on the Room's speaker with per-track immediate on/off, and ship the MIDITEST Room, the Deux Ara Drop Bit and a Console gesture panel that demo it.

**Architecture:** A stdlib MIDI file reader and a Bit-owned look-ahead sequencer (in `control/`) emit new `MusicCue`/`MusicPurge` cues from `Bit.fires(at)`. The engine routes them to new agent sinks that queue audio only (no light feed) on the existing Room `TimedQueue`, tagged by track so a track can be purged. `AudioBridge` grows multiple parts per Room role (channel nibble selects the part, one part can be GM drums). Tower light is Bit state: three `aurora` zones on hue lanes plus a new per-pixel `PixelFlashCue` for white flickers. The Console injects tap/tilt into `GameServer.data`.

**Tech Stack:** Python 3 stdlib (Bits may not add deps), pytest, luxaeterna (light render, dev dep), pyarco/Arco + FluidSynth GM soundfont (live only), plain ES-module JS + `node --test` for the Console.

**Spec:** `docs/superpowers/specs/2026-10-22-deux-ara-drop-midi-playback-design.md` (read it first; it holds the why and every decided behaviour).

## Global Constraints

- Bits and `control/` use only the stdlib and mm-terrarium. No `mido`.
- `control/audio.py` never imports pyarco; the offline suite stays offline.
- Only Control writes to `/arco`; audio declarations never ship to devices.
- GM soundfont (FluidR3_GM); fix the font, never the program numbers.
- 16 MIDI channels per Flsyn; channel 9 (0-based) is GM drums.
- Tempo for everything in Deux Ara Drop: **130 bpm**, 4/4.
- Tap toggles are **immediate** (heard at the gesture's `at`), never bar-quantized.
- Steady partial green/red is free game light; never a 2-3x full-strip solid flash (light lexicon section 3).
- Tests: `.venv/bin/python -m pytest tests -v` and `node --test tests/js/*.test.js` (never `python3`, never the bare `tests/js` directory form).
- Every commit message ends without attribution lines.

## Review Focus

1. **A toggle that lands while notes are already queued** (look-ahead window): toggle-off must leave no stuck note or pedal; toggle-on must not double-play a note. Owned by Task 6 (`test_no_stuck_notes_over_random_toggles`, `test_enable_does_not_double_emit_boundary_note`).
2. **Double tap while the Arabesque has just ended naturally**: must start it again, not "stop" a finished track. Task 6 (`test_one_shot_ends_and_can_replay`) and Task 10 (`test_double_tap_after_natural_end_restarts`).
3. **Tilt jitter around the threshold** (a real hand hovers at 40-50 degrees): volume must not flicker. Task 10 (`test_tilt_hysteresis`).
4. **Console gesture aimed at a fixture-bound device or the admin id**: refused with an error, never routed as a player gesture. Task 8 (`test_gesture_refuses_fixture_and_admin`).
5. **Abort/unload mid-song**: no voice keeps sounding and the next Bit gets clean voices. Task 5 (`test_release_silences_every_part`) and Task 3 (`test_mute_purges_music_cues`).

## Setup (controller runs this before Task 1; not a subagent task)

This Mac has no `.venv` and no pytest. luxaeterna is cloned at `~/projects/luxaeterna`.

```bash
cd /Users/chris/projects/mm-terrarium && python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e "$HOME/projects/luxaeterna[websim]"
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/terrarium-midi-playback-2c08ab && ln -s "$HOME/projects/mm-terrarium/.venv" .venv
.venv/bin/python -m pytest tests -q
node --test tests/js/*.test.js
```

Record the baseline pass/fail counts; later tasks must not add failures. If the baseline has failures, list them so reviewers do not attribute them to this branch.

---

### Task 1: MIDI file reader and the Bit's MIDI assets

**Files:**
- Create: `control/midifile.py`
- Create: `bits/deux_ara_drop/assets/piano.mid`, `drums.mid`, `synth.mid`, `arabesque.mid` (copies)
- Test: `tests/test_midifile.py`

**Interfaces:**
- Produces:
  - `MidiEvent(beat: float, status: int, data1: int, data2: int)` frozen dataclass. Channel-voice messages only (0x80-0xEF); a note-on with velocity 0 is normalized to `0x80 | channel`, data2 0. 1-byte messages (0xC0, 0xD0) have `data2 = 0`.
  - `MidiFile(ticks_per_beat: int, tempo_bpm: float | None, events: tuple[MidiEvent, ...], end_beat: float, name: str | None)` frozen dataclass, with `loop_beats(beats_per_bar: int = 4) -> float` (end_beat rounded **up** to a whole bar, minimum one bar) and `first_note_beat() -> float | None`.
  - `parse_midi(data: bytes) -> MidiFile` (raises `ValueError` on anything malformed) and `read_midi_file(path) -> MidiFile`.
  - Events from all tracks merged and sorted by beat, stable (track order, then file order). Meta/SysEx skipped except: first tempo meta sets `tempo_bpm`; first track-name meta (0x03) sets `name`; `end_beat` is the largest End-of-Track tick in beats.

- [ ] **Step 1: Copy the assets.**

```bash
mkdir -p bits/deux_ara_drop/assets
cp "/Users/chris/projects/mm-terrarium/docs/MIDITest/3-Grand Piano Single Sample 3.mid" bits/deux_ara_drop/assets/piano.mid
cp "/Users/chris/projects/mm-terrarium/docs/MIDITest/Melt Kit.mid" bits/deux_ara_drop/assets/drums.mid
cp "/Users/chris/projects/mm-terrarium/docs/MIDITest/Basic Saw Pad.mid" bits/deux_ara_drop/assets/synth.mid
cp "/Users/chris/projects/mm-terrarium/docs/MIDITest/Arabesque-in-E-Nr-1.mid" bits/deux_ara_drop/assets/arabesque.mid
```

- [ ] **Step 2: Write the failing tests** in `tests/test_midifile.py`. Inline-bytes tests build tiny SMFs with a local helper; asset tests read `bits/deux_ara_drop/assets/`.

```python
def note_ons(mf): return [e for e in mf.events if e.status & 0xF0 == 0x90]

def test_piano_loop():      # piano.mid
    mf = read_midi_file(ASSETS / "piano.mid")
    assert (mf.ticks_per_beat, mf.tempo_bpm) == (96, None)
    assert len(note_ons(mf)) == 55
    assert mf.end_beat == 16.0 and mf.loop_beats() == 16.0
    assert mf.name == "3-Grand Piano Single Sample 3"

def test_synth_loop_rounds_up_to_the_bar():
    mf = read_midi_file(ASSETS / "synth.mid")
    assert len(note_ons(mf)) == 19
    assert mf.end_beat == 3015 / 96 and mf.loop_beats() == 32.0

def test_drums_are_all_kick():
    mf = read_midi_file(ASSETS / "drums.mid")
    assert len(note_ons(mf)) == 32 and {e.data1 for e in note_ons(mf)} == {36}
    assert mf.loop_beats() == 32.0

def test_arabesque():
    mf = read_midi_file(ASSETS / "arabesque.mid")
    assert (mf.ticks_per_beat, mf.tempo_bpm) == (384, 120.0)
    assert len(note_ons(mf)) == 1458
    assert mf.first_note_beat() == 4.0 and mf.end_beat == 484.5
    assert any(e.status & 0xF0 == 0xB0 and e.data1 == 64 for e in mf.events)
    assert MidiEvent(4.0, 0xC0, 0, 0) in mf.events

def test_running_status_and_velocity_zero_note_off(): ...
    # bytes: format 0, 96 tpq, one track: 0x90 60 100 @0, running-status 60 0 @96,
    # EOT @96 -> events == (MidiEvent(0.0, 0x90, 60, 100), MidiEvent(1.0, 0x80, 60, 0))

def test_events_sorted_across_tracks(): ...   # format 1, two tracks interleave by beat

def test_malformed_raises():
    for bad in (b"", b"junk", b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk\x00\x00\x00\x09"):
        with pytest.raises(ValueError):
            parse_midi(bad)
```

- [ ] **Step 3: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_midifile.py -v` → FAIL (`ModuleNotFoundError: control.midifile`).
- [ ] **Step 4: Implement `control/midifile.py`.** Variable-length quantities, running status, SysEx (0xF0/0xF7 with VLQ length) and meta (0xFF type VLQ length) skipping; any read past the buffer or a bad chunk id raises `ValueError`.
- [ ] **Step 5: Run to verify they pass.** Same command → PASS.
- [ ] **Step 6: Commit.**

```bash
git add control/midifile.py tests/test_midifile.py bits/deux_ara_drop/assets
git commit -m "feat(control): stdlib MIDI file reader; Deux Ara Drop MIDI assets"
```

---

### Task 2: `MusicCue` / `MusicPurge` cues, and timed cues from `fires()`

**Files:**
- Modify: `control/cues.py` (add two dataclasses after `LightCue`)
- Modify: `control/engine.py` (`_dispatch_cues` ~1420-1500, `_dispatch_bit_fires` ~1522-1546, sink attributes where `on_light_cue` is declared)
- Modify: `control/bit.py` (`fires` docstring)
- Test: `tests/test_engine_music_cues.py`

**Interfaces:**
- Produces:
  - `MusicCue(dev: str, status: int, data1: int, data2: int, when: float | None = None, tag: str | None = None)` frozen dataclass: audio-only MIDI for a Room fixture. The status channel nibble selects the Room role's part (Task 5).
  - `MusicPurge(dev: str, tag: str)` frozen dataclass: drop every still-queued `MusicCue` carrying `tag` on that dev.
  - `GameServer.on_music_cue: Callable[[str, int, int, int, float, str | None], None] | None` and `GameServer.on_music_purge: Callable[[str, str], None] | None`, both default `None`, called once per resolved dev (`_resolve_devs`, so `ROOM` fans out per fixture).
  - `Bit.fires(at)` may now return `FireFunction`, `LightCue`, `MusicCue`, `MusicPurge` (Task 4 adds `PixelFlashCue`). Anything else is still logged and dropped.

- [ ] **Step 1: Write the failing tests** (follow the GameServer + Room fixture setup in `tests/test_timed_cues.py`; record sink calls in lists):
  - `test_music_cue_to_room_reaches_music_sink_per_fixture`: dispatching `MusicCue(ROOM, 0x91, 60, 100, when=5.0, tag="piano")` calls `on_music_cue(<fixture dev>, 0x91, 60, 100, 5.0, "piano")` once per Room fixture; `on_light_cue` is not called.
  - `test_music_cue_without_when_inherits_at`: `when=None` arrives as the dispatch `at`.
  - `test_music_purge_reaches_purge_sink`: `MusicPurge(ROOM, "piano")` → `on_music_purge(<fixture dev>, "piano")`.
  - `test_muted_dev_drops_music_cue`: after a `MuteCue` on the fixture, a `MusicCue` reaches no sink.
  - `test_fires_may_return_light_and_music_cues`: a Bit whose `fires` returns one `LightCue`, one `MusicCue`, one `MusicPurge` → all three sinks called on that tick.
  - `test_fires_still_drops_play_cue`: a `PlayCue` from `fires` reaches no sink.
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_engine_music_cues.py -v` → FAIL (`ImportError: MusicCue`).
- [ ] **Step 3: Implement.** Add the dataclasses; add two `isinstance` branches in `_dispatch_cues` (MusicCue skips `is_muted` devs, `when = at if cue.when is None else cue.when`); widen the type filter in `_dispatch_bit_fires` and its docstring; update `Bit.fires`'s docstring to list the allowed kinds.
- [ ] **Step 4: Run the new tests and the engine suites.** `.venv/bin/python -m pytest tests/test_engine_music_cues.py tests/test_engine.py tests/test_timed_cues.py tests/test_engine_functions.py -v` → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(engine): MusicCue and MusicPurge; fires() may return timed cues"`

---

### Task 3: Agent routes music to Room audio only, purgeable by tag

**Files:**
- Modify: `devicelink/agent.py` (sink registration ~308-311, `_room_cues` payload, `_render_room` ~1063, `_on_light_cue` ~1797, `_on_mute_change` purge ~996; new `_on_music_cue`, `_on_music_purge`)
- Test: `tests/test_devicelink_music.py` (importorskip luxaeterna like `tests/test_devicelink_agent.py`)

**Interfaces:**
- Consumes: Task 2's sinks.
- Produces: `_room_cues` payload becomes `(fixture_name, status, d1, d2, tag)`; light-path pushes use `tag=None`. `DeviceLinkAgent._on_music_cue(dev, status, d1, d2, when, tag)` pushes onto `_room_cues` only (never `_feed_light_now`, never `_light_cues`); a dev that is not a Room fixture or is muted is dropped. `DeviceLinkAgent._on_music_purge(dev, tag)` purges that fixture's entries with that tag.

- [ ] **Step 1: Write the failing tests** (agent + `AudioBridge(FakePool())` as `room_audio`, a fake clock you advance, an audio-capable Room fixture such as TEST's `main`):
  - `test_music_cue_reaches_voice_at_when`: `_on_music_cue(<main dev>, 0x90, 60, 100, T, "piano")`; render before T → voice has no `note_on`; advance to T, render → `("note_on", 60, 100)`.
  - `test_music_cue_does_not_touch_light`: a `MusicCue` CC (0xB0, 20, 99) leaves that fixture's `controllers` without key 20 and queues nothing on `_light_cues`.
  - `test_music_purge_drops_only_that_tag`: queue tag "piano" and tag "drums" at T, purge "piano", advance → only the drums note sounds.
  - `test_mute_purges_music_cues`: queue a music cue, mute the fixture → after T nothing sounds.
  - `test_light_cue_still_feeds_room_audio`: existing light-cue → audio path unchanged (regression).
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_devicelink_music.py -v` → FAIL (`AttributeError: _on_music_cue`).
- [ ] **Step 3: Implement** as specified in Interfaces; register both sinks next to `on_light_cue`.
- [ ] **Step 4: Run.** `.venv/bin/python -m pytest tests/test_devicelink_music.py tests/test_devicelink_agent.py tests/test_lobby_agent.py -v` → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(devicelink): audio-only music cues on the Room queue, purgeable by tag"`

---

### Task 4: `PixelFlashCue` for single-pixel flashes

**Files:**
- Modify: `control/cues.py`, `control/engine.py` (dispatch branch, fires filter, sink attribute)
- Modify: `devicelink/agent.py` (store, paint at the three `_apply_override` call sites ~1085/1200/1237, expire in `_tick_overrides`, clear on mute)
- Test: `tests/test_pixel_flash.py` (agent half importorskips luxaeterna)

**Interfaces:**
- Produces:
  - `PixelFlashCue(dev: str, pixel: int, rgb: tuple, duration: float, when: float | None = None)` frozen dataclass. `rgb` is (r, g, b) or (r, g, b, w); three values mean W = 0. The lexicon's white is `(0, 0, 0, 255)`.
  - `GameServer.on_pixel_flash: Callable[[str, int, tuple, float, float], None] | None` (dev, pixel, rgb, duration, when), per resolved dev, skipped for muted devs. `fires()` may return it.
  - `DeviceLinkAgent._on_pixel_flash(...)` keeps `self._pixel_flashes: dict[str, dict[int, tuple[tuple, float]]]` (fixture key → pixel → (rgb, expires=when+duration)), invalidates the frame, and `_apply_pixel_flashes(dev, frame) -> bytes` paints each live pixel's 4 RGBW bytes **after** `_apply_override`. A pixel index outside the frame is ignored. Same semantics as `SolidCue`: painted from arrival, gone at `expires`.

- [ ] **Step 1: Write the failing tests:**
  - `test_pixel_flash_reaches_sink` (engine): `PixelFlashCue(ROOM, 3, (0,0,0,255), 0.08)` → `on_pixel_flash(<fixture dev>, 3, (0,0,0,255), 0.08, at)`.
  - `test_fires_may_return_pixel_flash` (engine).
  - `test_flash_paints_one_pixel` (agent): the next rendered frame for that fixture has bytes `[12:16] == bytes((0,0,0,255))` and every other pixel equal to the frame rendered just before the flash.
  - `test_flash_expires`: after `duration` passes and a tick runs, pixel 3 is back to the session's value.
  - `test_flash_ignored_while_muted` and `test_out_of_range_pixel_is_ignored` (no raise, frame unchanged).
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_pixel_flash.py -v` → FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run.** `.venv/bin/python -m pytest tests/test_pixel_flash.py tests/test_devicelink_agent.py tests/test_engine_music_cues.py -v` → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat: PixelFlashCue, a single-pixel solid flash painted over the frame"`

---

### Task 5: Several parts per Room role, one of them GM drums

**Files:**
- Modify: `control/role_config.py` (`validate_ugen_manifest` ~242-290)
- Modify: `control/audio.py` (`FakePool.acquire`, `FakeVoice`, `_DeviceAudio`, `on_grant`, `_apply_midi`, `set_control`, `silence`, `on_release`, start/stop drone; module docstring's "v0: one instrument per role" note)
- Modify: `harness/arco_synth.py` (`ArcoSynthPool.acquire`)
- Test: `tests/test_audio.py`, `tests/test_role_config.py`, `tests/test_arco_synth.py`

**Interfaces:**
- Produces:
  - ugen manifest instrument keys: `program` (int 0-127, optional), `drums` (bool, optional; at most one instrument per manifest), at most 16 instruments. Instrument list index = **part** number.
  - Pool protocol: `acquire(*, drums: bool = False) -> voice`. `ArcoSynthPool`: `DRUM_CHANNEL = 9`; `drums=True` takes channel 9 or raises `RuntimeError("drum channel 9 is taken")`; `drums=False` takes the lowest free channel **other than 9**, `RuntimeError` when none. `FakePool.acquire(drums=False)` records the flag on the returned `FakeVoice.drums`.
  - `AudioBridge`: one voice per declared instrument, program applied per part, lanes per part, drone on part 0 only. `_apply_midi` routes by `status & 0x0F`; a nibble naming no declared part falls back to part 0 (today's behaviour). `set_control` writes part 0. `silence`/`on_release` cover every part.

- [ ] **Step 1: Write the failing tests:**
  - `test_role_config.py`: `test_ugen_drums_must_be_bool`, `test_ugen_only_one_drums_part`, `test_ugen_program_range` (128 rejected, error mentions "program"), `test_ugen_at_most_16_instruments`.
  - `test_audio.py`: `test_multi_part_grant` (four instruments, programs 0, none, 90, 0 → 4 voices acquired, `FakeVoice.drums` only on part 1, program messages match); `test_channel_nibble_selects_part` (0x92 60 100 → part 2's voice only); `test_unknown_nibble_falls_back_to_part_zero` (single-part role, status 0x95); `test_cc64_and_cc7_lanes_per_part`; `test_release_silences_every_part` (every part's voice gets `all_off` and is released); existing single-instrument tests unchanged.
  - `test_arco_synth.py`: `test_drums_take_channel_nine`, `test_melodic_voices_skip_channel_nine` (acquire 10 melodic voices → channels 0-8 and 10), `test_second_drums_part_raises`. Set `pool._flsyn = object()` so no pyarco is needed.
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_audio.py tests/test_role_config.py tests/test_arco_synth.py -v` → new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** the same command plus `tests/test_devicelink_agent.py tests/test_lobby_agent.py tests/test_sim_audio.py` → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(audio): multi-part Room roles with a GM drum part"`

---

### Task 6: The look-ahead sequencer

**Files:**
- Create: `control/sequencer.py`
- Test: `tests/test_sequencer.py`

**Interfaces:**
- Consumes: `MidiFile`/`MidiEvent` (Task 1), `MusicCue`/`MusicPurge`/`ROOM` (Task 2).
- Produces `Sequencer`:
  - `__init__(self, bpm: float, *, dev: str = ROOM, lookahead: float = 0.25)`
  - `add_track(self, name: str, midi: MidiFile, *, part: int, loop: bool, skip_beats: float = 0.0) -> None`. Looped tracks repeat every `midi.loop_beats()` from grid beat 0. A one-shot drops events before `skip_beats` and shifts the rest so `skip_beats` lands on its start beat.
  - `start(self, t0: float) -> None`: grid beat 0 at `t0`; every track off. `beat_at(t) -> float`, `time_at(beat) -> float` (60/bpm seconds per beat).
  - `is_on(self, name) -> bool`
  - `enable(self, name, at) -> list` (looped tracks): the track's current CC 7 (scaled), then a note-on at `at` for every note whose note-on beat is **strictly before** `beat_at(at)` (within the loop) and whose note-off is after it (chase); the cursor resumes at `at` so an event exactly at `at` is emitted once, by `advance`.
  - `disable(self, name, at) -> list`: `MusicPurge(dev, name)`, then at `at` a note-off for every key whose note-on has `when <= at` with no note-off at `when <= at`, and CC 64 = 0 if the last emitted CC 64 at `when <= at` was >= 64.
  - `play_once(self, name, at) -> list`: one-shot starts at `time_at(ceil(beat_at(at)))` (next whole beat; equals `at`'s beat if exactly on it). Returns its scaled CC 7 at the start time. Playing again after a natural end restarts from the top.
  - `advance(self, at) -> list[MusicCue]`: every enabled track's events with `when < at + lookahead` not yet emitted. A one-shot turns off once its last event is emitted and `at` has passed it.
  - `set_master(self, level: float, at) -> list[MusicCue]`: CC 7 at `at` on every track's part = `round(file_cc7 * level)`, `file_cc7` = last CC 7 the track's file sent (default 100); file CC 7 events emitted by `advance` are scaled the same way.
  - Every cue is `MusicCue(dev, (event.status & 0xF0) | part, d1, d2, when, tag=name)`.

- [ ] **Step 1: Write the failing tests** (build small `MidiFile`s directly; real assets where named):
  - `test_grid_at_130_bpm`: `time_at(1) - time_at(0) == pytest.approx(60/130)`.
  - `test_advance_emits_at_grid_times`: enabled track with a note at beat 1 → cue `when == pytest.approx(t0 + 60/130)`, status `0x90 | part`.
  - `test_disabled_track_emits_nothing`.
  - `test_loop_repeats_at_loop_length`: piano asset (loop 16): its first note recurs at `time_at(16)`.
  - `test_enable_chases_held_note`: note on beat 0, off beat 8, enable at beat 2 → note-on with `when == at`.
  - `test_enable_does_not_double_emit_boundary_note`: enable exactly at a note's beat → that note emitted once over the next `advance` calls.
  - `test_disable_purges_and_releases`: returns `MusicPurge(ROOM, "synth")`, a note-off for the sounding key, and CC 64 = 0 when the pedal was down.
  - `test_no_stuck_notes_over_random_toggles`: seeded random enable/disable of three asset loops over 60 s of 44 Hz `advance`; model the queue (a `MusicPurge` removes queued cues with that tag and `when > purge_at`); after disabling everything, each (part, key) has as many delivered note-offs as note-ons.
  - `test_master_scales_file_cc7`: Arabesque (file CC 7 127) at master 0.2 → its CC 7 cue value 25; a loop with no CC 7 → 20.
  - `test_play_once_on_next_beat_skipping_lead_in`: Arabesque with `skip_beats=4.0`, `play_once` at `t0 + 0.1` → first note-on `when == time_at(1)`.
  - `test_one_shot_ends_and_can_replay`: a 2-beat one-shot is off after `advance` past its end; `play_once` again emits its first note again.
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_sequencer.py -v` → FAIL.
- [ ] **Step 3: Implement `control/sequencer.py`.** Pair note-ons with the next note-off of the same key in the track to know each note's span (for chase). Track emitted state per track: last CC 7, last CC 64, and the sounding-key set as of `when`.
- [ ] **Step 4: Run.** Same command → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(control): look-ahead MIDI sequencer with immediate per-track on/off"`

---

### Task 7: MIDITEST Room and the speaker-equipped Tower

**Files:**
- Create: `instruments/tower_sound.toml`
- Create: `rooms/MIDITEST.toml`
- Test: `tests/test_miditest_room.py` (follow `tests/test_tower_room.py`)

**Interfaces:**
- Produces: instrument `tower_sound` = `instruments/tower.toml` plus `"audio.flsyn"` in `capabilities`, same `pixels = 14`, `accepted_cues`, `model = "models/tower.glb"`. Room `MIDITEST`: `backends = ["devicelink"]`, one fixture `tower` (instrument `tower_sound`), blocks exactly as `rooms/TOWER.toml`, zones `bottom` (0, 3), `middle` (3, 2), `top` (5, 3), `responder` (8, 4), `beat` (12, 2).

- [ ] **Step 1: Write the failing tests:** `test_miditest_zones` (names/starts/counts above), `test_miditest_tower_has_audio` (`"audio.flsyn"` in capabilities, 14 pixels), `test_miditest_model_loads` (the same model/layout check `test_tower_room.py` and `test_catalog.py` apply passes for MIDITEST), `test_tower_instrument_unchanged` (`instruments/tower.toml` capabilities still `["light.surface"]`).
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_miditest_room.py -v` → FAIL.
- [ ] **Step 3: Write the two TOML files.** Description strings: instrument "Tower fixture with room speaker (MIDITEST): 8 progress, 4 responders, 2 PARs (14 px)"; Room "MIDITEST: simulated Tower with sound, for MIDI playback (Deux Ara Drop)".
- [ ] **Step 4: Run** the new file plus `tests/test_rooms.py tests/test_tower_room.py tests/test_catalog.py tests/test_terrarium_config.py` → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(rooms): MIDITEST Room with a speaker-equipped Tower"`

---

### Task 8: Console gesture command and panel

**Files:**
- Modify: `console/protocol.py` (`GestureCommand`, `parse_admin_command` branch, `__all__`)
- Modify: `console/agent.py` (admin-command name tuple ~188, `_handle_admin_command`)
- Create: `console/static/gestures.js`; Modify: `console/static/index.html` (a `<div id="gestureCard" class="card" hidden>` after `bitStatusCard`), `console/static/shell.js` (import and `init`)
- Test: `tests/test_console_protocol.py`, `tests/test_console_agent.py`, `tests/js/gesture_panel.test.js` (follow `tests/js/bit_panel.test.js` and `_dom_stub.js`)

**Interfaces:**
- Produces:
  - Wire: `{"command": "gesture", "dev": str, "verb": "tap", "count": 1|2}` or `{"command": "gesture", "dev": str, "verb": "tilt", "gamma": float}` (gamma clamped to -90..90).
  - `GestureCommand(dev: str, verb: str, args: list)` where `args` is exactly what a real device delivers to `GameServer.data`: tap `[dev, 0.0, 50.0, count]`, tilt `[dev, gamma]`. Any other verb, a missing/empty dev, a count not in {1, 2} or non-numeric gamma → `ValueError`.
  - Agent: refuses (error_event) a dev bound to a Room fixture or an admin id (`TERRARIUM_ADMIN` and `[admin] devices`); otherwise `GameServer.data(dev, verb, args)` (gesture_time None → Control's clock) and a returned reason becomes `error_event`.
  - Panel: visible while state is SETUP or RUNNING and at least one non-fixture device exists. A device picker (player devices only), **Tap** and **Double tap** buttons, a tilt range input -90..90 step 1 (sends on input, at most every 50 ms, plus the final value on change), a degree read-out, and a **Level** button that sets 0 and sends it.

- [ ] **Step 1: Write the failing tests:** protocol `test_parse_gesture_tap`, `test_parse_gesture_tilt_clamps`, `test_parse_gesture_rejects_bad` (verb "shake", count 3, empty dev); agent `test_gesture_reaches_bit_verb_handler` (a recording Bit's `tap` handler receives `["ie1", 0.0, 50.0, 2]`), `test_gesture_refuses_fixture_and_admin`; JS `gesture panel hidden in IDLE`, `tap and double tap send count 1 and 2`, `tilt input sends gamma`, `level sends 0`.
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_console_protocol.py tests/test_console_agent.py -v` and `node --test tests/js/gesture_panel.test.js` → new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces, styled with existing Console classes (`card`, `btn`, `firerow`).
- [ ] **Step 4: Run** the same commands plus `node --test tests/js/*.test.js` → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(console): tap, double tap and tilt panel for player devices"`

---

### Task 9: Quiet simulated shroom and per-Bit device instrument

**Files:**
- Modify: `harness/o2_shroom.py` (`--manual` flag; when set, never arm the beat tapper and never run the tilt sweep; operator-page gestures still work)
- Modify: `control/bit_config.py` (`LaunchConfig.device_instrument: str = ""`, `LaunchConfig.manual_devices: bool = False`, parsed from `[launch]`, added to the known-keys set)
- Modify: `harness/run_stack.py` (`StackConfig.device_instrument: str | None`, `StackConfig.manual_devices: bool`; flags `--device-instrument NAME` and `--manual-devices`; defaults from the Bit's launch config; `device_command` appends `--instrument NAME` and `--manual`)
- Test: `tests/test_o2_shroom.py`, `tests/test_bit_config.py`, `tests/test_run_stack.py`

- [ ] **Step 1: Write the failing tests:** `test_manual_flag_disables_synthetic_gestures` (follow `test_main_gates_the_tilt_sweep_and_the_tapper_on_uses`: the gate expressions also require `not args.manual`); `test_launch_device_instrument_and_manual_parse` (values round-trip; absent → `""`/`False`); `test_device_command_passes_instrument_and_manual` (`"--instrument", "tuneshroom"` and `"--manual"` present when set, absent by default); `test_launch_defaults_flow_into_stack_config` (a Bit declaring them, no CLI flags → StackConfig carries them; CLI flag wins).
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_o2_shroom.py tests/test_bit_config.py tests/test_run_stack.py -v` → new tests FAIL.
- [ ] **Step 3: Implement** per Files.
- [ ] **Step 4: Run** the same command → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(harness): --manual simulated shrooms and a Bit-declared device instrument"`

---

### Task 10: The Deux Ara Drop Bit

**Files:**
- Create: `bits/deux_ara_drop/__init__.py`, `bits/deux_ara_drop/bit.toml`, `bits/deux_ara_drop/deux_ara_drop_bit.py`
- Test: `tests/test_deux_ara_drop_bit.py` (follow `tests/test_minigame_bit.py` for loading through `BitRegistry`)

**Interfaces:**
- Consumes: Tasks 1-9.
- `bit.toml`:

```toml
[bit]
name = "DeuxAraDropBit"
version = "0.1.0"
description = "Deux Ara Drop: tap drops piano, drums and synth loops in and out; double tap plays the Arabesque; tilt ducks the mix"
entry = "deux_ara_drop_bit:DeuxAraDropBit"
kind = "music"
author = "Musical Mycology"
requires_terrarium_api = 1

[launch]
room_types = ["MIDITEST"]
default_room_type = "MIDITEST"
default_devices = 1
default_join_role = "player"
device_instrument = "tuneshroom"
manual_devices = true

[launch.nodes]
player = "DEUX_PLAYER_NODE"

[start]
when = "players"
min_scored = 1

[console]
display_name = "Deux Ara Drop"

[assets]
piano = "assets/piano.mid"
drums = "assets/drums.mid"
synth = "assets/synth.mid"
arabesque = "assets/arabesque.mid"
```

- Module constants (exact): `BPM = 130.0`; `ORDER = ("piano", "drums", "synth")`; parts `piano=0, drums=1, synth=2, arabesque=3`; `THIRD_CC = {"piano": 20, "drums": 21, "synth": 22}`; `GREEN = 42` (hue 0.333 x 127), `RED = 0`; `TIP_DEG = 45.0`, `RIGHT_DEG = 20.0`; `DUCK = 0.2`; `FLICKER_HZ = 12.0`, `FLICKER_S = 0.08`, `WHITE = (0, 0, 0, 255)`, `TOWER_PIXELS = 14`.
- `role_table`: one Role `player` (UNIQUE, capacity 1, scored, `uses=["tap", "tilt"]`, empty `light_manifest`), `node_map = {"DEUX_PLAYER_NODE": ["player"]}`.
- `room_manifests()`: light = three `aurora` instruments targeting `tower.bottom`, `tower.middle`, `tower.top`, `params {"hue": 0.0, "level": 0.8}`, lane `cc:20|21|22 -> hue`. Ugen = four `flsyn` instruments in part order: piano `program 0`, drums `drums: true`, synth `program 90`, arabesque `program 0`; every part has lanes `cc:7 -> cc:7`, and piano/synth/arabesque also `cc:64 -> cc:64`.
- `__init__(config=None, rng=None)`: parses the four assets via `config.asset_path(...)`; `rng` defaults to `random.Random()`.
- `fires(at)`: on the first call, `Sequencer(BPM).start(at)` with tracks piano/drums/synth looped and arabesque one-shot with `skip_beats = 4.0` (its first bar), and returns a program change per melodic part (`MusicCue(ROOM, 0xC0 | part, program, 0, when=at)` for piano 0, synth 90, arabesque 0; the lobby reprograms part 0 during SETUP, so the Bit must restore it), the three thirds at RED (`LightCue(ROOM, 0xB0, cc, RED, when=at)`), plus `set_master(1.0, at)`. Every call returns `advance(at)` plus, while the Arabesque is on, flickers: per call emit `PixelFlashCue(ROOM, rng.randrange(14), WHITE, FLICKER_S, when=at)` with probability `FLICKER_HZ * dt` (dt = this `at` minus the previous call's, capped at 0.1).
- `verb_handlers()`: `{"tap": ..., "tilt": ...}`. Tap count (args[3], default 1) >= 2 toggles the Arabesque (`play_once` if off, `disable` if on) and leaves the cycle alone. Count 1 toggles `ORDER[cursor % 3]` (`enable`/`disable`), advances the cursor, and returns the sequencer cues plus `LightCue(ROOM, 0xB0, THIRD_CC[track], GREEN or RED, when=at)`. Tilt (args[1]) with `abs >= TIP_DEG` while upright → `set_master(DUCK, at)`; `abs <= RIGHT_DEG` while tipped → `set_master(1.0, at)`; otherwise nothing.
- `status()`: `{"piano": "on"|"off", "drums": ..., "synth": ..., "arabesque": "playing"|"stopped", "volume": "100%"|"20%"}`. `update(dt)` returns False.

- [ ] **Step 1: Write the failing tests:**
  - `test_manifest`: registry loads `DeuxAraDropBit`, `room_types == ("MIDITEST",)`, display name "Deux Ara Drop", all four assets resolve to files.
  - `test_first_fires_paints_thirds_red`: three LightCues with data1 20/21/22, data2 0.
  - `test_first_fires_restores_programs`: MusicCues with status/data1 (0xC0, 0), (0xC2, 90), (0xC3, 0) and none for part 1.
  - `test_tap_cycle`: six single taps toggle piano, drums, synth, piano, drums, synth; `status()` after 3 taps all "on", after 6 all "off"; tap 1 returns a LightCue (20, 42), tap 4 returns (20, 0) and a `MusicPurge(ROOM, "piano")`.
  - `test_double_tap_toggles_arabesque_not_cycle`: double tap → `status()["arabesque"] == "playing"`, the next single tap still toggles piano; second double tap → `MusicPurge(ROOM, "arabesque")`, "stopped".
  - `test_double_tap_after_natural_end_restarts`: run `fires` past the Arabesque's end (status "stopped"), double tap → "playing".
  - `test_tilt_hysteresis`: tilt 50 → CC 7 cues with value 20 on the loops, volume "20%"; then 30 and 40 → no cues; 15 → volume "100%"; -60 → "20%".
  - `test_flickers_only_while_arabesque_plays`: 1 s of `fires` at 44 Hz with a seeded rng → no `PixelFlashCue` before the double tap; at least one after, all `rgb == WHITE`, `0 <= pixel < 14`.
  - `test_loads_in_miditest_room`: `GameServer.load_bit("DeuxAraDropBit")` with the MIDITEST Room succeeds (follow `tests/test_engine_bit_config.py`).
- [ ] **Step 2: Run to verify they fail.** `.venv/bin/python -m pytest tests/test_deux_ara_drop_bit.py -v` → FAIL.
- [ ] **Step 3: Implement** the Bit per Interfaces.
- [ ] **Step 4: Run** the new file plus `tests/test_bit_registry.py tests/test_catalog.py` and `.venv/bin/python -m harness.run_stack --list-bits` (lists DeuxAraDropBit, no manifest errors) → PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(bits): Deux Ara Drop, MIDI loops on taps, Arabesque on double tap"`

---

### Task 11: Docs, full suite, live smoke

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (Status bullet; `control/` audio section: multi-part Room roles and drum part; Bit interface: `fires` may return timed cues; new `MusicCue`/`MusicPurge`/`PixelFlashCue`; `bits/` section: Deux Ara Drop; `console/` section: gesture panel; Running it: the launch command below; harness: `--manual-devices`, `--device-instrument`)
- Modify: `docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md` (section 9.2 open question: note the multi-part extension and drum flag)

- [ ] **Step 1: Update the docs** as listed (match the deep-dive's existing voice; no new top-level sections beyond what the content needs).
- [ ] **Step 2: Full suites.** `.venv/bin/python -m pytest tests -q` and `node --test tests/js/*.test.js` → no failures beyond the Setup baseline.
- [ ] **Step 3: Live boot check (controller).** `./smoke-test.sh --bit DeuxAraDropBit --handshake-devices 1 --console-port 8772 --ci --seconds 45` → exits 0, `room loaded: MIDITEST`, `ROLE GRANTED: ... scored` for `ie1`. If Arco or the soundfont is missing on this host, record that and leave the listen to Chris.
- [ ] **Step 4: Commit.** `git commit -m "docs: Deux Ara Drop, music cues, multi-part Room audio, gesture panel"`
- [ ] **Step 5: Hand to Chris for the listen:** `./smoke-test.sh --bit DeuxAraDropBit --handshake-devices 1 --console-port 8772`, open the Console, tap/double-tap/tilt from the gesture panel.
