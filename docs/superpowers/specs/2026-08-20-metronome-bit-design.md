# MetronomeBit -- a call-and-response metronome game for RoomType.DEMO

**Status:** approved design, 2026-08-20. The repo's first production game Bit.
Brainstormed with Chris; gameplay decisions below are his, recorded verbatim
where they resolve an open question.

## 1. What this is

A call-and-response rhythm game. A regular 4/4 metronome plays at 100 BPM;
players (up to 2 Tuneshrooms) tap back in time during wait beats. In-time
phrases earn a fireworks light burst; missed phrases earn a red flash and a
low tone. A run with at least one success anywhere ends in a 10-second
rainbow across the DEMO array plus a modulating pad sound.

Alongside the Bit, two simulator-input improvements land so a human can
actually tap in rhythm from the browser-canvas Tuneshroom, and
`AudioBridge` gains MIDI program-change handling so one voice can switch
between the click, fail-tone, and finale sounds.

## 2. Gameplay decisions (settled in brainstorming)

- **Tempo/grid:** 100 BPM, beat = 0.6 s, 4/4. Loop is 8 beats: beats 1-4
  the metronome plays (beat 1 HARD, 2-4 soft), beats 5-8 are WAIT beats.
- **Run length:** the 8-beat call-and-response cycle repeats **4 times per
  run** (4 phrases total, regardless of player count).
- **Turns:** round-robin. Each wait phrase belongs to exactly one joined
  Tuneshroom, rotating in join order; only its taps are judged that phrase
  (the other device's taps are ignored, not penalized). A solo player owns
  every phrase.
- **Tolerance:** a tap is in time if its (offset-corrected) time is within
  **plus or minus 50 ms** of a wait-beat gridpoint. Start at 50 and see how
  well it works; if the input path cannot deliver ~50 ms response, a
  calibration tool becomes its own later slice (see section 8).
- **Success rule (per phrase):** ALL 4 wait beats must each receive at
  least one in-time tap, AND no off-grid tap may occur during the phrase --
  **an off-grid tap spoils the phrase** immediately (judgment consequence
  still lands at phrase end, with everything else).
- **Failure granularity:** judged **per phrase, at phrase end** -- one
  red/low-tone consequence per failed phrase, never per silent beat.
- **Consequences:** success fires fireworks on **that Tuneshroom**, and the
  DEMO room array fires fireworks on **any one** successful Tuneshroom.
  Failure turns both the room and that Tuneshroom red and plays a low tone.
- **Neutral state:** flashing green in time with the metronome (room and
  joined devices).
- **Recovery:** after a red, the room returns to neutral green by the time
  the failing player's next turn comes up; the failed Tuneshroom goes
  non-glowing after its red flash and relights when its turn comes up.
- **End of run:** if at least 1 phrase success was made by ANY connected
  Tuneshroom, play a rainbow across the array for 10 seconds plus a sound
  that modulates up and down; then the Bit completes. Otherwise complete
  immediately after the last judgment.
- **Cap:** 2 connected Tuneshrooms, enforced by role capacity.
- **Sounds** (FluidR3_GM.sf2, GM programs, ArcoSynthPool Flsyn backend):
  clicks = GM 115 Woodblock, key 76, vel 120 hard / 65 soft; fail low tone
  = GM 38 Synth Bass 1, key 33; finale = GM 89 Warm Pad (the proven
  sustaining program on this backend) with cc:74 swept up and down for the
  10 s rainbow.

## 3. The Bit (`bits/metronome_bit.py`)

- `class MetronomeBit(Bit)`, `version = "0.1"`,
  `room_types = {RoomType.DEMO}`.
- **Roles:** `player` -- `RoleClass.UNIQUE`, `capacity=2`, `scored=True`,
  node `METRO_PLAYER_NODE`, `uses=["tap"]`, no samples (all audio is
  room-side Arco; see section 9). Plus the DEMO room role via
  `room_role()`. No jam
  role in v1 (YAGNI; TestBit keeps that seam exercised).
- **Light manifests.** Player and room each declare two instruments:
  - `aurora` targeting `primary` with `hue` (green, 0.33) and `level`
    params and lanes `cc:74 -> hue`, `cc:11 -> level`. Neutral flashing
    green = the Bit pulsing cc:11 on the beat grid; red = cc:74 snapped to
    red; non-glowing = cc:11 to 0.
  - `bloom` with a note lane (`note -> trigger`) and a `cc:74 -> hue`
    lane, for fireworks: rapid note-on bursts at random hues. Bloom's
    freeze-hue-at-note-on behavior (the documented strobe that was wrong
    for aurora) is exactly what a firework flash wants. **Risk:** if bloom
    bursts read poorly on the canvas, the fallback is a new luxaeterna
    `sparkle` preset (cross-repo, luxaeterna PR), without changing this
    Bit's cue logic.
  - The **room role only** additionally declares `rainbow` targeting
    `primary` with `level: 0.0` and a `cc:21 -> level` lane: dark for the
    whole run, raised to full by the finale (while aurora's `cc:11` level
    drops to 0), giving the spatial across-the-array rainbow without two
    instruments fighting over the hue lane.
- **Ugen manifests.** One `flsyn` instrument per role, initial program 115
  (Woodblock), **no drone** (the metronome must be silent between clicks;
  FluidSynth needs note-ons, which the Bit supplies itself). Lanes
  `cc:74 -> cc:74`, `cc:11 -> cc:11`. Program switches at runtime via
  program-change cues (section 6).

## 4. Timing model -- everything in `at`-space

A verb handler and `cues(at)` both see `at = origin + cue_horizon` and
never the raw stamp or the horizon. Because the metronome's own cues and
the players' taps pass through the same computation, the Bit defines its
beat grid entirely in `at`-space and the horizon offset cancels.

- **Anchor:** on the first `cues(at)` call after `on_run_start`,
  `t0 = at + LEAD_IN` (one beat of lead-in). All gridpoints are
  `t0 + k * 0.6`.
- **Emission:** `cues(at)` emits each upcoming beat's cues once, one beat
  ahead, as absolutely-timed cues (`LightCue(when=...)` for light;
  note-on/note-off 4-tuple pairs stamped by the same dispatch for audio),
  riding the live-verified `TimedQueue` path. A `_next_emit_index` cursor
  makes emission idempotent per beat regardless of tick rate.
- **Judgment:** a tap handler receives `(dev, args, at)`; the Bit records
  `t_tap = at - INPUT_OFFSET_S` and grades against the grid.
  `INPUT_OFFSET_S = 0.0` is the class-constant calibration knob (section 8).
- **Determinism:** all state advances off `at` and `update(dt)`'s
  accumulated elapsed time; tests can drive the whole game at exact times
  with no clock.

## 5. Game state machine (inside the Bit)

Phases per 8-beat cycle: `CALL` (beats 1-4) -> `WAIT` (beats 5-8) ->
judgment at beat 8's end -> next cycle or `FINALE`/complete.

- Turn assignment: at each cycle start, the owning dev is
  `joined_players[cycle_index % len(joined_players)]` in join order. If no
  player is joined, the metronome still plays and the phrase is unjudged
  (no failure fires with nobody to fail).
- Per-phrase tap log: list of graded taps (beat index or OFF_GRID). At
  judgment: success iff all 4 wait beats covered and no OFF_GRID entry.
- Consequences are reported as `FireTrigger` cues from `cues(at)` at
  judgment time (latched, exactly-once), same shape as TestBit's
  `play_aurora`.
- After 4 cycles: if total successes >= 1, enter FINALE (fire the finale
  trigger, run 10 s while `cues(at)` sweeps cc:74 up and down on the room);
  then `update` returns True. Else True immediately.
- A device released mid-run drops out of the rotation at the next cycle
  boundary; if the current turn's device disappears mid-phrase, the phrase
  becomes unjudged.
- Join order is learned via a new optional no-op
  `Bit.on_join(dev, role_name)` hook (the one engine-seam addition; same
  extension precedent as `cues(at)`).

## 6. TriggerTable

All consequence choreography is declared as bit-adjudicated triggers, so
the Terrarium Console shows them and an operator can fire any of them
manually -- for free, via machinery live-verified 2026-08-20:

| trigger | target | script sketch |
|---|---|---|
| `fireworks_player` | DEVICE | ~1.5 s of bloom note-ons at random hues (script steps at 60-120 ms spacing, hue cc before each note-on) |
| `fireworks_room` | ROOM | same shape across the array |
| `fail_player` | DEVICE | cc:74 -> red; program 38; note-on key 33; note-off; then cc:11 -> 0 (non-glowing) -- light-only: players have no Arco voice in `devicelink/agent.py`, all audio is ROOM-side |
| `fail_room` | ROOM | cc:74 -> red; program 38; note-on key 33; note-off |
| `finale` | ROOM | program 89; sustained note-on; cc:21 -> 127 (rainbow up) + cc:11 -> 0 (aurora dark); 10 s of cc:74 up/down steps (pad filter sweep); then cc:21 -> 0, cc:11 restored, note-off |

Random hues in fireworks scripts: chosen at `trigger_table` build time from
a seeded sequence (scripts are static declarations), varied enough to read
as random. `Date`-style nondeterminism is avoided so tests stay exact.

Note the recovery choreography (room back to green by the failing player's
next turn; failed device relit at its turn) is driven by `cues(at)` on the
beat grid, not by scripts -- it depends on whose turn is next, which a
static script cannot know.

## 7. Engine/bridge change: MIDI program change (0xC0)

`control/audio.py`'s `_apply_midi` handles 0x90/0x80/0xB0 today. It gains
`0xC0` -> `entry.voice.program_change(d1)`. `DeviceVoice` already declares
`program_change` in its protocol; `FakeVoice` already records it. Status
byte flows through the existing cue tuples (`(dev, 0xC0, prog, 0)`),
`RoomBridge.feed_audio`, and the wire unchanged -- light sessions ignore
status bytes they don't bind. A small, local, tested change.

## 8. Simulator input improvements (`harness/o2_shroom.py` + luxaeterna)

The live-verified browser-tap path has two latency problems fatal to a
plus-or-minus 50 ms window:

1. **The 250 ms held click.** luxaeterna's `PAGE_HTML` holds every single
   click 250 ms to disambiguate a double-click. Fix (luxaeterna, cross-repo
   PR): send `{"type":"tap","count":1}` immediately on **pointerdown**;
   drop the page's double-click disambiguation (the wire keeps `count` so
   nothing else changes shape). Rationale: a rhythm game cannot wait, and
   no current Bit depends on browser double-taps (TestBit's chime is
   reachable from real hardware; the sim loses only that nicety).
2. **Drain-time stamping.** `o2_shroom` stamps `o2lite.time_get()` at
   drain (tick) time, adding up to ~23 ms of 44 Hz quantization. Fix:
   stamp at **enqueue** time in the websocket callback thread and carry
   `(stamp, msg)` through the queue; `drain_gestures` sends with the
   carried stamp. Design Rule 4 is preserved -- the whole simulator
   process is still the device, and the stamp moves earlier within it.
   (`o2lite.time_get()` is a clock read; verify thread-safety at
   implementation time -- if unsafe, capture `time.monotonic` deltas at
   enqueue and convert at drain.)

**Measurement, not faith:** the Bit's `status()` surfaces the last 8
signed tap errors in ms plus per-device tallies, so a live run shows the
real error distribution on the Console. If p95 error exceeds the window,
tune `INPUT_OFFSET_S` (systematic bias) or widen the tolerance constant
(jitter), with data. A dedicated interactive calibration tool is a
separate later slice, only if this fails.

## 9. What this deliberately does not do

- No local sample playback on the sim (the `/dev/play` gap is a separate
  slice; the room speakers carry all audio, which is also the venue
  reality).
- No jam role, no scoring framework beyond `result()`, no new engine
  seams, no luxaeterna preset (unless bloom fireworks fail visually).
- No per-device audio localization: "a low tone on that Tuneshroom" is
  synthesized room-side on that player's Arco voice, like all Tuneshroom
  audio today.
- TEST-room support: not declared. DEMO is the point; offline tests do not
  need a RoomType to exercise the Bit.
- All audio is ROOM-side: players have no Arco voice in
  `devicelink/agent.py`, so `fail_player` (and every other DEVICE trigger)
  is light-only.

## 10. Testing

Offline (the suite stays runnable with no Arco/pyarco/o2litepy):

- Beat-grid unit tests at exact times: tap at `t0 + k*0.6 +/- 0.049`
  counts, `+/- 0.051` does not; off-grid tap spoils; all-4 rule; per-phrase
  judgment timing; round-robin ownership incl. solo and mid-run release;
  capacity: a third `METRO_PLAYER_NODE` join is denied.
- `cues(at)` emission: idempotent per beat, correct absolute `when`s,
  lead-in anchor, finale sweep values, exactly-once `FireTrigger` latching.
- Trigger scripts validate at `load_bit` (existing `TriggerTable`
  validation).
- `_apply_midi` 0xC0 unit tests via `FakeVoice`.
- `o2_shroom` enqueue-stamp tests against the strict `FakeO2Lite`
  (boundary rule 5: the fake dispatches only on `poll()`).
- luxaeterna: page-behavior test for pointerdown-immediate tap (its
  existing JS/page test pattern).

Live verification (after offline green):

```
python -m harness.run_stack --devices 2 --open --room-type DEMO
```

Tap both sim canvases through a run; confirm clicks, green pulse,
fireworks, red/low-tone, recovery choreography, finale rainbow + pad; read
the tap-error distribution off the Console `bit_status` panel.

## 11. Open follow-ups this spec creates

- Calibration tool slice, only if measured error defeats the 50 ms window.
- luxaeterna `sparkle` preset, only if bloom fireworks read poorly.
- Double-tap semantics on the sim page, if any future Bit wants them back
  (a press-duration or modifier scheme that does not delay the first tap).
