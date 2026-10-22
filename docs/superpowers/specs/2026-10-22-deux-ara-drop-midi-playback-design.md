# Deux Ara Drop: MIDI file playback in a MIDITEST Room

Date: 2026-10-22. Status: design, awaiting approval.

## Intent

Terrarium can play single notes and drones on Arco, but it cannot play a
song. Chris bounced MIDI tracks out of Ableton and wants a Bit to launch
one or more of them and have game events switch whole tracks on and off
while the music keeps time. This first build is a test of that system: a
demo Room with the simulated Tower, one simulated tuneshroom, and a person
at the Console tapping and tilting it to build up and thin out a song.

Success: someone at the Console boots the demo, taps the shroom and hears
piano, then drums, then synth drop in on the spot, in time with each other,
while the matching third of the Tower turns from red to green; tips it over and the music falls to 20%;
stands it up and it comes back; double-taps and hears the Debussy Arabesque
start, double-taps again and it stops.

## Goals

- A reusable way for any Bit to play multi-track MIDI files on the Room's
  speakers, in time, with each track switchable on and off immediately.
- A reusable Console panel that lets a person send tap, double tap and
  tilt for a player device, so a Bit can be demoed and tested without
  hardware.
- The **MIDITEST** Room: the Tower (with its 3D model) plus sound.
- The **Deux Ara Drop** Bit, wired to the behaviour below.

## Non-goals

- Replacing the `TEST` Room. Chris's first thought was that MIDITEST
  might replace it. *Builder's call (Claude):* MIDITEST is added alongside
  `TEST`, because `TEST` is the default Room of every Bit and appears in
  ~30 test files; retiring it is a separate cleanup.
- Tempo changes inside a file, MIDI files with more than one part per
  channel, live tempo control, recording.
- Scoring, winning or ending the round. Deux Ara Drop runs until the
  operator stops it.
- Real (non-simulated) Tower or tuneshroom hardware. Nothing here should
  block it, but it is not tested.

## Anti-goals (would make this a failure even if it technically works)

- A tap that takes a bar or more to be heard. Chris was explicit: a tap
  adds or drops its track **immediately**.
- Tracks drifting out of time with each other, or the Arabesque landing
  off the loops' beat.
- Stuck notes: a track switched off, or the Arabesque stopped, leaves a
  note or the sustain pedal hanging.
- Tilt volume that the MIDI file's own volume messages override (the
  Arabesque sets volume to full on its first bar).
- The simulated shroom tapping or tilting by itself during the demo.

## The experience (decided by Chris)

- Room **MIDITEST**, Bit **Deux Ara Drop**. The Bit waits for one
  simulated tuneshroom.
- Everything plays at **130 bpm**: the three loops and the Arabesque.
- When the Bit starts, the three loops start together, silent, and keep
  running in sync.
- **Tap** cycles through the tracks in this order: 1st tap piano on, 2nd
  drums on, 3rd synth pad on. A tap on a track that is already on turns it
  off: after all three are on, taps 4-6 turn piano, drums, synth off in
  the same order, and the cycle repeats (confirmed by Chris).
- A tap's change is **immediate**, not quantized to a bar.
- **Double tap** starts the Arabesque (`Arabesque-in-E-Nr-1.mid`) at 130
  bpm. The next double tap stops it.
- **Tilt** past a threshold turns the whole mix down to 20%. Righting the
  shroom brings it back to 100%.
- A person drives the shroom from the **Console**: tap, double tap and
  tilt controls.
- **The Tower shows the loops.** The Tower's 8-light progress column is
  split into thirds, bottom up: loop 1 (piano) is progress pixels 0-2,
  loop 2 (drums) pixels 3-4, loop 3 (synth) pixels 5-7. A third is **green
  while its loop plays and red while it is stopped**. The responders
  (8-11) and base PARs (12-13) are not part of the thirds; they stay dark
  apart from the Arabesque flickers.
- **While the Arabesque plays**, random white flickers run along the
  entire Tower, on top of the green/red thirds.

## The material

The source files are in the main checkout at `docs/MIDITest/` (untracked).
Copies of all four are committed into the Bit package as `[assets]`
(Chris cleared all four for this public repo).

| File | What it is | Length | Notes |
|---|---|---|---|
| `3-Grand Piano Single Sample 3.mid` | piano loop | 4 bars (1536 ticks @ 96 tpq) | exact loop length |
| `Melt Kit.mid` | kick drum only, note 36 | 8 bars | end marker at 3010 ticks; true loop is 3072 |
| `Basic Saw Pad.mid` | pad chords | 8 bars | end marker at 3015 ticks; true loop is 3072 |
| `Arabesque-in-E-Nr-1.mid` | Debussy solo piano, 1458 notes | ~121 bars, ~3:44 at 130 | format 1, 384 tpq, one tempo (120) |

- The three loops carry no tempo and are all on channel 1, one file each.
  Their length must be rounded up to the whole bar (the piano loops twice
  per pad/kit cycle). They share a key family with the Arabesque (E/B
  major).
- The Arabesque's first bar is empty (a GS-reset bar with a SysEx). It
  sets program 0 and volume 127, and uses the **sustain pedal (CC 64)**
  constantly. The pedal must reach the synth or it sounds dry and broken.
- *Builder's call (Claude):* skip the Arabesque's empty first bar so a
  double tap is heard at once; start it on the next beat of the loops'
  130 bpm grid (at most ~0.46 s) so it lands in time with them.
- *Builder's call (Claude):* sounds are General MIDI: piano program 0
  (both the loop and the Arabesque, each on its own channel), synth pad
  program 90 (Pad 3 "polysynth", the closest GM sound to a saw pad), kick
  on the GM drum channel (channel 10, note 36 is Bass Drum 1).

## What exists, and what is missing

Facts from the code (2026-10-22, `main` at `cbceae7`):

1. **No MIDI file reading.** Nothing parses `.mid`. Bits may use only the
   stdlib and mm-terrarium (`docs/MM_TERRARIUM.md`, *Bit packages*). A
   stdlib Standard MIDI File reader (formats 0 and 1, running status,
   tempo/meta skipping) is ~50 lines.
2. **Bits cannot stream arbitrary timed notes.** `Bit.fires(at)` may return
   only `FireFunction` (`control/engine.py`, the fires dispatch), and
   Functions are fixed scripts declared up front (`control/functions.py`).
   Raw timed `LightCue`s reach the Room only from a verb handler's return.
   `MetronomeBit.fires` is the existing look-ahead walker to copy
   (`bits/metronome/metronome_bit.py`: anchor `_t0` on first `fires`,
   emit each gridpoint once it is within one beat of `at`).
3. **One sound per fixture.** A Room fixture with `audio.flsyn` gets one
   `ArcoVoice` (one MIDI channel) from the Bit's ROOM role;
   `AudioBridge.on_grant` reads only `ugen_manifest["instruments"][0]`
   ("v0: one instrument per role") and `_apply_midi` discards the channel
   nibble (`control/audio.py`). `ArcoSynthPool` hands out channels 0-15 in
   order with no way to ask for the drum channel (`harness/arco_synth.py`).
4. **Undeclared CCs are dropped.** Only lane-mapped CCs reach the synth,
   so the sustain pedal and volume need declaring (`control/audio.py`).
5. **The Tower is silent.** `instruments/tower.toml` declares only
   `light.surface`, so the `TOWER` Room has no speaker voice.
6. **Room notes also drive light.** A Room-addressed cue feeds both the
   fixture's luxaeterna session and the audio queue
   (`devicelink/agent.py`, `_on_light_cue` / `_render_room`); there is no
   audio-only routing. What a note does to the light is decided by the
   Room role's light manifest, so the music must be kept from painting
   over the state lights below.
6a. **Per-zone Tower light already exists.** MinigameBit gives each Tower
   zone its own `aurora` instrument whose `level` rides its own CC lane,
   and sends `LightCue(tower, 0xB0, cc, value)` to change one zone
   (`bits/minigame/minigame_bit.py`, `role_table` and `_on_tap`). Room
   zones are `start`/`count` ranges declared in the Room TOML. Steady green
   or red on part of a fixture is free game light; only two or three
   full-strip solid flashes and a lone slow green/white pulse are reserved
   (`docs/light-lexicon.md` section 3).
6b. **Tower pixel heights** (`tests/test_tower_import.py`, `TOWER_LAYOUT`,
   z in mm on a 2100 mm Tower): PARs 12-13 at 90; progress 0-7 at 482,
   632, 791, 937, 1089, 1242, 1394, 1547; responders 8-11 at 1000, 1250,
   1500, 1750.
7. **Timing.** Room cues sit in a `TimedQueue` drained at 44 Hz (~23 ms
   granularity). `TimedQueue.purge(predicate)` exists for removing queued
   notes when a track goes off.
8. **Mute is all-or-nothing** (`MuteCue` blacks out and silences a whole
   fixture), so per-track on/off belongs in the Bit's sequencer, not in
   `MuteCue`.
9. **Gestures.** `/game/tap` carries `count`; the device pairs a double
   tap itself and sends one tap with `count >= 2` (`devicelink/contract.py`;
   `control/jam_role.py` `solo_event`). Tilt is `gamma` in degrees,
   -90..90.
10. **The simulated shroom plays by itself.** `harness/o2_shroom.py` arms a
    beat tapper and a sine tilt sweep whenever the role's `uses` lists tap
    or tilt. It already accepts operator gestures from its own luxaeterna
    "Watch the Shroom" page (`drain_gestures`), but that page is not the
    Console.
11. **The Console has no live gesture controls.** Its only tilt slider is
    in the light Design bench. `ConsoleAgent` holds the `GameServer` and
    already shows the Bit's `status()`.

## The design

### A. MIDI file playback (reusable, in `control/`)

- **A stdlib SMF reader** turning a file into a list of timed events per
  channel (ticks to beats), plus loop length rounded up to the bar.
- **A sequencer a Bit owns**, driven from `fires(at)` like MetronomeBit:
  one shared beat clock at a fixed bpm, any number of *tracks*, each a
  parsed file bound to a *part* (an instrument on the Room's speaker),
  looping or one-shot. Each tick it emits the notes that fall inside a
  short look-ahead window, stamped with their exact beat time.
- **Track on/off is immediate.** Switching a track off drops its queued
  notes and sends note-off (and pedal-off) for anything sounding.
  Switching a track on must be heard straight away: *Builder's call
  (Claude):* it also starts any note of that track that should already be
  sounding at the current position (so a long pad chord comes in mid-chord
  rather than waiting for the next chord, which could be bars away).
- **Master volume** as a level 0..1 applied to every part's MIDI volume,
  multiplied with whatever volume the file itself asks for, so a file's
  CC 7 never overrides it.
- **A path for the sequencer's timed notes to reach the Room.** Today
  `fires` can return only `FireFunction`. This needs either a new cue type
  `fires` may return, or a Room-music entry point on the engine. The
  mechanism is the builder's; the constraints are: notes keep the Bit's
  timestamps, go through the existing Room cue queue (so light and sound
  stay coupled and Control remains the only writer to `/arco`), and a
  track can be purged from the queue.

### B. Several parts on one Room speaker (`control/audio.py`,
`harness/arco_synth.py`)

- A Room role's `ugen_manifest` may declare **several instruments**
  (parts); each gets its own channel from the pool, its own program, and a
  `drums` flag that puts it on the GM drum channel.
- A Room MIDI cue's channel nibble selects the part, so a Bit sends
  `(0x90 | part, note, velocity)`.
- Sustain pedal (CC 64) and volume (CC 7) pass through to every part.
- Existing single-instrument roles behave exactly as today (part 0).
- `ugen_manifest` v0 is provisional (tuneshroom-audio spec section 9.2);
  this extends it and must update the validator in
  `control/role_config.py` and the spec's open questions.

### C. The MIDITEST Room

- `rooms/MIDITEST.toml`: the Tower fixture with the same 14-pixel layout
  as `rooms/TOWER.toml` (blocks lower/upper/responders/par, zones
  progress/responder/beat), showing `models/tower.glb` in the Console.
- MIDITEST declares its own Tower zones for the thirds, each a contiguous
  range: `bottom` (start 0, count 3), `middle` (start 3, count 2), `top`
  (start 5, count 3), plus zones covering the responders (8-11) and PARs
  (12-13) so the flickers can reach the whole Tower.
- The Tower needs a speaker. *Builder's call (Claude):* a separate
  instrument file for MIDITEST (the Tower's model, pixels and cues plus
  `audio.flsyn`), leaving `instruments/tower.toml`, the `TOWER` Room and
  their tests untouched. The real Tower's instrument should not claim a
  speaker a given install may not have.

### D. The Console gesture panel (`console/`)

- While a Bit is RUNNING, a panel lists the player devices (not fixtures,
  not the admin id) with **Tap**, **Double tap** and a **tilt slider**
  (-90..90 degrees).
- *Builder's call (Claude):* the panel injects the gesture into
  `GameServer.data(dev, verb, args)` in-process, stamped with Control's
  clock, rather than relaying it through the simulated shroom's process.
  The Bit sees exactly the same verb and arguments a real shroom sends
  (`tap` with `count` 1 or 2, `tilt` with `gamma`). The over-the-wire
  device path already has its own tests.
- The simulated shroom must not gesture by itself: add a way to start
  `harness/o2_shroom.py` with its synthetic tapper and tilt sweep off, and
  use it for this Bit's launch.
- The panel also shows the Bit's `status()` (which tracks are on, volume,
  whether the Arabesque is playing); the Console already displays it.

### E. The Deux Ara Drop Bit (`bits/deux_ara_drop/`)

- Manifest name `DeuxAraDropBit` (the existing convention: `MetronomeBit`,
  `SoloTestBit`), description "Deux Ara Drop". Room types `["MIDITEST"]`,
  default Room `MIDITEST`, one default device using the `tuneshroom`
  instrument, start as soon as one scored player has validated.
- One scored player role using `tap` and `tilt`. A ROOM role declaring the
  four parts: piano loop, drums, synth pad, Arabesque piano.
- The four MIDI files as package `[assets]`, read via
  `BitConfig.asset_path()`.
- On run start: start the 130 bpm clock and the three loops, all off.
- `tap` (count 1): toggle the next track in the piano, drums, synth cycle.
- `tap` (count 2 or more): start the Arabesque on the next beat, or stop
  it if playing. It plays once; at its end it is simply off.
- `tilt`: *Builder's call (Claude):* tipped means `|gamma| >= 45`; righted
  means `|gamma| <= 20`, so hovering near the line does not flicker.
  Tipped sets master volume to 0.2, righted to 1.0.
- **Tower light** (the ROOM role's light manifest, the MinigameBit
  pattern): one instrument per third whose colour the Bit sets with CC
  `LightCue`s: green when that loop is on, red when off, sent the moment
  a tap toggles it and once at run start (all red). The music's notes must
  not drive the Tower's light: the thirds bind no note lanes. Every Room
  cue, including the music's CC 7, CC 11 and CC 64 on all four channels,
  reaches the Tower's light session (`devicelink/agent.py`,
  `_feed_light_now`), so *Builder's call (Claude):* the thirds' lanes use
  CC numbers the music never sends (none of 0, 7, 11, 64, and none any of
  the four files contains), and the light manifest binds nothing else.
- The tap cycle is not affected by the Arabesque: a double tap neither
  advances nor resets it (*Builder's call (Claude)*).
- **Arabesque flickers:** while the Arabesque plays, the Bit itself sends
  short random white flickers on random Tower pixels, overlaid on the
  thirds, and stops when the Arabesque stops or ends. *Builder's call
  (Claude):* the flickers are generated by the Bit, not triggered by the
  Arabesque's notes, because notes reach every light instrument on the
  fixture. Rate and length are the builder's, as long as it reads as
  sparkle, not as a full-Tower flash (which the lexicon reserves).
- `status()`: tracks on/off, volume, Arabesque playing.
- Never completes on its own; the operator stops it from the Console.
- A one-command launch: `./smoke-test.sh --bit DeuxAraDropBit` boots
  MIDITEST with one quiet simulated tuneshroom that accepts the handshake.

## Constraints

- Bits use only the stdlib and mm-terrarium. No `mido`.
- Only Control writes to `/arco`; audio declarations never ship to
  devices; Control owns the ugen ids and frees them at unload.
- `control/audio.py` never imports pyarco; the offline test suite stays
  offline.
- The soundfont must be GM (FluidR3_GM); fix the font, never the program
  numbers.
- 16 channels per Flsyn. This Bit uses four.
- The light lexicon's reserved patterns are not reused for game feedback.
- Tests: the repo's suites are `.venv/bin/python -m pytest tests -v` and
  `node --test tests/js/*.test.js`. A fresh worktree needs the `.venv`
  symlink (`docs/MM_TERRARIUM.md`, *Running it*).
- Update `docs/MM_TERRARIUM.md` for the new Room, Bit, Console panel and
  the multi-part Room audio.

## Acceptance (*Builder's call (Claude)*)

- Offline, with a fake clock: the SMF reader against the four files
  (event counts, loop lengths); the sequencer's emitted timestamps at 130
  bpm; a toggle-on is heard within one 44 Hz tick plus `cue_horizon`;
  toggle-off and Arabesque stop purge queued notes and leave no note or
  pedal on (every note-on matched by a note-off); master volume scales the
  files' own CC 7; the Bit's tap cycle, tilt hysteresis, light cues and
  `status()`.
- Live: `./smoke-test.sh --bit DeuxAraDropBit` on a host with real Arco
  and the GM soundfont, driven from the Console panel, then a listen by
  Chris. Cross-track skew of one 44 Hz tick (~23 ms) is acceptable.
- The Console panel is part of done. The builder may order the Bit first
  and use the simulated shroom's own "Watch the Shroom" page in the
  meantime.

## Left to the builder

- The exact sequencer and cue API shapes, the look-ahead window size, and
  how a part's channel is chosen beyond "drums get channel 10".
- How the quiet simulated shroom is selected (flag name, profile).
- The Console panel's look, within the existing Console style.
- Which luxaeterna instrument renders the thirds and the flickers.
