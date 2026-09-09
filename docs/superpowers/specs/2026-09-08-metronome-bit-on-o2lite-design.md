# MetronomeBit on the o2lite stack -- design

**Date:** 2026-09-08
**Status:** approved for implementation under the autonomous flow (the
operator asked for brainstorm -> spec -> plan -> subagent-driven
development and was not available to review this file before the plan
was written; the PR carries the review).
**Scope:** get `bits/metronome/metronome_bit.py` working end to end on the
post-cutover transport (Phase 1 o2lite, Phase 2 o2ws browser guests), and
make the headless smoke test prove it.

## 1. What a live run showed

Baseline: `./smoke-test.sh --ci --profile profiles/dev-metronome.toml
--seconds 60` on MYCOLOGICAL, 2026-09-08.

1. **The Bit refuses to load.** `bits/metronome/bit.toml` carries
   `enabled = false` since the 2026-09-01 console-load-stabilization
   slice ("pending redesign"; the redesign was never specified). The
   profile fails before Arco boots.
2. With the flag flipped, `runs/20260908-211014`: both joins granted,
   RUNNING at the second join (`min_scored = 2`), 820 and 802 frames
   delivered to the two Testshrooms, the Bit ends the run itself,
   `Bit completed; tearing down`, exit 0. The o2lite path carries the
   Bit's cue traffic; the 4096-byte cap is not reached (a 864 px Room
   frame is 2592 bytes).
3. **Every perfect tap is judged 60 ms late.** Reproduced at engine level
   with a real `GameServer` (`cue_horizon = 0.060`) and a real
   `MetronomeBit`: a `/game/tap` stamped exactly at the presentation time
   of a wait beat is recorded in `tap_errors_ms` as `+60.0`. Root cause:
   `GameServer.data()` hands a handler `at = origin + cue_horizon`, the
   time the tap's *consequence* should be presented. The beat grid is
   anchored in the same presentation space and each beat is presented at
   its gridpoint, so a tap made at the instant a beat is presented has
   `origin == gridpoint` and `at == gridpoint + horizon`. The 2026-08-20
   Bit design's claim that "the horizon offset cancels" is wrong: it
   cancels for the Bit's own cues, not for input. With a 50 ms window
   the game is unwinnable at the default 60 ms horizon.
4. **No tap ever reaches judgment in CI.** `harness/o2_shroom.py` drives
   a synthetic tilt sweep and nothing else; taps come only from a browser
   drag. MetronomeBit's `player` role declares `uses = ["tap"]`, so every
   run also logs `ERROR from Control: tilt: unknown verb 'tilt'` (present
   in the pre-cutover run too). All four phrases fail by silence, so the
   run never exercises success, fireworks, or the finale.
5. **Nothing configures Python logging.** Every `logger.warning` in
   `control/engine.py` and `devicelink/agent.py` (refused gesture stamps,
   ROOM cues with no Room bound, dropped frames) is invisible in
   `control.log`.
6. **Serve-mode markers are not CI markers.** `round loaded:` /
   `round ended:` are printed only by `_serve_rounds`; a one-shot `--ci`
   run ends with `Bit completed; tearing down`. Not a defect; recorded so
   nobody chases it again.

## 2. Decisions

### 2.1 Re-enable the Bit
Delete `enabled = false` from `bits/metronome/bit.toml`. The `enabled`
key stays as a mechanism. `tests/conftest.py`'s enabled-copy fixtures keep
working (they strip a line that is no longer there) and stay, so a future
disable does not break the profile tests again.

### 2.2 The engine tells a Bit its presentation lead
`control/bit.py` gains a class attribute `cue_horizon: float = 0.0`
("the installation's presentation lead: `at` minus the gesture's own
stamp; a Bit that grades input against presented beats subtracts it").
`GameServer.load_bit` sets `bit.cue_horizon = self._horizon` right after
construction, before `on_setup_enter`. `MetronomeBit._on_tap` grades
`t = at - self.cue_horizon - self.INPUT_OFFSET_S`.

The 2026-08-14 rule "a Bit never sees the horizon" was about keeping
`origin + horizon` in one place for *cue emission*; it stands for cues. A
Bit that judges *input* against presented output needs the lead, and
this is the narrowest way to give it: no handler signature change (every
existing `handler(dev, args, at)` is untouched), no engine reference in
the Bit, no second copy of the constant.

Rejected: setting `input_offset_ms = 60` in the manifest or profile (two
constants that must silently agree with `BootConfig.cue_horizon`, and it
hijacks the knob meant for real input-path latency); a fourth handler
argument (breaks TestBit and CaptureBit handlers); pushing the origin
into `args` (mixes wire arguments with engine data).

### 2.3 The Testshroom drives the gestures the role declares
`harness/o2_shroom.py` reads `uses` off the granted role blob
(`control/role_config.py` already composes it):

- Tilt sweep runs when `"tilt"` is in `uses`, or when `uses` is empty or
  absent (legacy roles keep today's behavior).
- A **beat tapper** runs when `"tap"` is in `uses`.

**`harness/beat_tapper.py`** is a small, clock-free, socket-free class
driven by the frames the device *displays* (not receives): a
phase-locked follower of brightness rises.

- Brightness is the sum of the frame's channel bytes. A rise is a frame
  whose sum exceeds the previous displayed frame's by `RISE_RATIO`
  (0.25). MetronomeBit's beat pulse steps cc:11 from 60 to 110 (+85%),
  the aurora's own motion is a few percent, and the pulse decay 150 ms
  later is a fall.
- Lock: rises from black (previous sum below `DARK_SUM`) are ignored
  while locking, because the role grant lights a black canvas moments
  before beat 0 and would otherwise be counted as it. The first two
  counted rises give `t_first` and a provisional period, sanity-bounded
  to 0.2..2.0 s (outside that, restart from the later rise).
- After lock, a rise at `r` predicts beat index `k = round((r -
  t_first) / period)`; it is accepted if `|r - (t_first + k * period)|
  <= ACCEPT_FRACTION * period` (0.2) and refines `period = (r -
  t_first) / k` (the long-baseline estimate, so display jitter of a few
  ms is divided by the beat count). Rejected rises are fireworks flashes
  (12 in 1.4 s) or hue-only changes; a dark spell (a failed player) is
  simply beats with no rises, and the next rise re-indexes by time.
- A tap is issued on an accepted rise whose index modulo
  `beats_per_cycle` (8) is at or past `call_beats` (4): the player counts
  four and answers four, like the human the Bit was designed for. The
  tap's stamp is the display tick's O2 clock reading, at the source
  (Design Rule 4), and it goes out as the documented
  `/game/tap sffi [dev, 1.0, 50.0, 1]` row.

`harness/websim_leds.py`'s `WebSimLeds` gains an optional
`on_show(frame, now)` hook (with a `clock` callable) so the tapper sees
displayed frames without reaching into `ShroomClient`. The Room
simulator passes neither. Each tap is printed (`tap sent: beat <k> at
<t>`) so a run's device log shows them, and the exit report adds
`beat taps sent: N`.

### 2.4 Logging reaches control.log
`harness/terrarium_boot.py`'s `main()` calls a small
`configure_logging()` once: root logger at INFO to stderr, format
`%(levelname)s %(name)s: %(message)s`. `run_stack` already merges each
child's stderr into its `.log`. Only three `logger.info` sites exist in
the runtime path today, so INFO is quiet. `MetronomeBit` logs one INFO
line per judged tap (`tap ie1 cycle 0 err +3.2 ms`) and one per cycle
verdict, so a CI run's `control.log` shows taps being judged without a
Console.

### 2.5 Docs
`docs/MM_TERRARIUM.md`: the MetronomeBit entry gets the live-verified
stamps from this slice, the tap-bias root cause, the tapper, the
`uses` gating, and the CI-vs-serve marker note; the 2026-08-14 timed-cues
entry gets a correction note on "the Bit never sees the horizon"; the
2026-08-20 spec's timing section gets a Status note pointing here.

## 3. Out of scope
- A general scoring framework (MetronomeBit still reports via `result()`).
- The o2ws timer-magnitude measurement (needs a real phone; Chris).
- Making the tapper configurable from the CLI or the profile; its
  8/4 structure matches the Bit's default `[rhythm]` block and is a
  constructor parameter for tests only.

## 4. Testing
- `tests/test_metronome_bit_engine.py`: a tap stamped at a presented wait
  beat is judged within a few ms with `cue_horizon = 0.06` (fails today
  with `+60.0`).
- `tests/test_metronome_bit_judgment.py`: `cue_horizon` is subtracted;
  the default `0.0` keeps every existing judgment test byte-identical.
- `tests/test_engine*.py`: `load_bit` stamps `cue_horizon` on the Bit.
- `tests/test_beat_tapper.py`: lock, from-black ignore, wait-beat
  selection, flash rejection, dark-spell re-index, period refinement,
  sanity restart.
- `tests/test_websim_leds.py`: `on_show` receives the displayed frame and
  the clock reading; absent hook is byte-identical.
- `tests/test_o2_shroom.py`: `uses` gating helper (tilt-only, tap-only,
  both, absent).
- `tests/test_bit_packages.py`: MetronomeBit resolves through the
  registry's enabled gate again.
- `tests/test_terrarium_boot.py`: `configure_logging` sets the root level
  and is idempotent.

## 5. Live verification
1. `./smoke-test.sh --ci --profile profiles/dev-metronome.toml --seconds
   60` on MYCOLOGICAL: two joins, taps judged with errors inside the
   window, at least one success (fireworks), finale, `Bit completed`.
2. Browser gate: mm-tuneshroom `tool/sim build` on `claude/o2ws-link`,
   `./smoke-test.sh --serve --profile profiles/dev-metronome.toml
   --web-build <build/web>`, open
   `http://127.0.0.1:8788/app/index.html?node=METRO_PLAYER_NODE` in the
   automation browser; confirm the guest joins and receives the Bit's
   cues (join and frames, not timing: hidden tabs clamp timers).
3. A real phone on the venue LAN stays with Chris.
