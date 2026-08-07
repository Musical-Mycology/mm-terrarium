# Sensor Telemetry Capture: design

**Date:** 2026-08-07
**Status:** design, approved for planning
**Repos:** mm-terrarium (server half), mm-tuneshroom (phone half)

## 1. Why

The Flutter Tuneshroom simulator (`mm-tuneshroom/lib/sim/`) is gaining Tap and
Shake buttons on its control surface. For those buttons to be faithful, we have
to know what a real phone's sensors actually emit during a tap versus a shake.
Today we do not, and the two detectors that claim to know disagree with each
other in two separate ways.

### 1.1 The two detectors, and why neither is trustworthy

`mm-tuneshroom/lib/sensors/tap_detector.dart` (`TapDetector`): threshold 2.0 g,
200 ms debounce, 400 ms double-tap window. It consumes the magnitude of
`sensors_plus`'s `accelerometerEventStream`, which **includes gravity**, so the
resting magnitude is about 1.0 g. A threshold of 2.0 g on that stream therefore
fires on roughly **1 g of deviation** from rest.

`mm-tuneshroom/www/sensors.js`: `SHAKE_THRESHOLD = 2.0 * GRAVITY`,
`SHAKE_DEBOUNCE_MS = 200`, tested as `Math.abs(mag - 9.81) > 19.6`. That needs a
total magnitude above 3 g, which is **2 g of deviation** from rest.

Two consequences:

1. **The stated problem is real.** Both use the same nominal number and the same
   debounce, so as written a tap and a shake are not distinguishable by the
   feature either one measures. Chris's intent is that tap is a compound read of
   force and duration, and shake is speed, force and angles in a clean sweep.
   Neither detector measures duration, and neither measures angle at all.
2. **There is a second, unnoticed problem.** The `sensors.js` comment claims it
   "mirrors the native TapDetector heuristic (2.0g spike / 200ms debounce)". It
   does not. It is roughly 3x stricter, because the two thresholds are applied to
   different quantities on different streams. The same file already flags its own
   number as "UNVALIDATED for a browser DeviceMotionEvent stream". Both facts
   point at the same root cause: **a threshold was copied without its source
   stream**, and nothing recorded what stream it was ever valid for.

That root cause is what this design is built to prevent recurring. Every trace
this system produces carries a `source` block describing the stream that produced
it, and every derived threshold will cite one.

### 1.2 What we are building

A capture Bit for the Control+GameServer Bit runtime that records raw per-sample
sensor telemetry from a real phone, tagged with the gesture label the operator
declares *before* performing it, and persists each capture as an inspectable
trace file. Plus the phone-side capture client, an offline feature-summary tool,
and the path by which a curated trace becomes a named preset button in the
Flutter simulator that emits exactly the payload a real phone produced.

**Non-goal for this spec:** rewriting `TapDetector` or `sensors.js`. That is the
payoff and it is the next spec, because it cannot be specified before the data
exists.

## 2. Scope and repo split

**mm-terrarium** owns the server half: the wire verbs, the trace store, the Bit,
the offline analysis tool, and the trace schema document.

**mm-tuneshroom** owns the phone half: a new capture entrypoint, and the preset
buttons that later consume the traces.

Nothing couples at build time. The contract is `docs/telemetry-trace-schema.md`
in mm-terrarium, following exactly the pattern `devicelink/protocol.py` already
uses with `mm-tuneshroom/lib/link/envelope.dart`: two files, one contract, change
both together.

```
mm-terrarium                          mm-tuneshroom
  devicelink/protocol.py  <---------->  lib/link/envelope.dart      (exists)
  docs/telemetry-trace-schema.md <--->  lib/capture/                (new)
  bits/capture_bit.py                   lib/capture_main.dart
  capture/store.py  ---> captures/ ---> assets/traces/ ---> lib/sim/trace_source.dart
  tools/trace_stats.py                                       (curated by hand)
```

### 2.1 Client platform: Flutter native on iPhone

The capture client is a Flutter **native iOS** build, not a mobile web page.

Reasons. `sensors_plus` gives a real high-rate native stream with no permission
dialog for the accelerometer and no secure-context requirement. A browser client
would need HTTPS for `DeviceMotionEvent.requestPermission()` on iOS while the
Terrarium serves plain HTTP on the LAN, and it would capture the lower-rate,
smoothed browser stream. mm-tuneshroom already carries `ios/` and `web/` platform
directories, so a native build is a build target rather than new plumbing. And
the preset payoff lands in the same repo as the sim that consumes it.

**Deferred, not rejected:** a browser-stream capture client, which is the only
way `www/sensors.js` gets a threshold derived from its own stream rather than
inherited. The trace schema tags its source stream (section 6), so that client
drops in later with no format change.

### 2.2 A separate Flutter entrypoint

`lib/capture_main.dart` is separate from `lib/sim_main.dart`. `sim_main` is a
desktop-browser surface with a tilt slider; capture is a phone surface with a
mic. They share `lib/link/` unchanged.

## 3. The wire

Two new verbs. Both ride the **existing** generic `/game/<verb>` path:
`DeviceLinkAgent._on_verb` already forwards any unrecognised verb to
`GameServer.data(dev, verb, args)`, which dispatches to `Bit.verb_handlers()`.
So `devicelink/agent.py` needs **no change at all**, and `devicelink/protocol.py`
gains only a batch decoder plus its docstring. One small engine change is
required, see section 4.0.

```
/game/capture   "ssb"  dev  action  meta
/game/telemetry "sfb"  dev  t0      batch
```

### 3.1 Relationship to the documented vocabulary

`docs/control-gameserver-design.md` Player Flow step 4 already specifies
`/game/data "stb" dev time payload` for control-rate data at the rate the Bit
requested. `/game/telemetry` is a concrete instance of that shape: same
`dev, time, payload` triple. It carries a distinct verb name because
`GameServer.data()` is already the name of the whole verb-dispatch method, so a
verb literally named `data` reads as `game_server.data(dev, "data", args)`.

Typespec is `"sfb"` rather than the design doc's `"stb"` because
`devicelink/protocol.py` documents only `s`/`i`/`f`/`b` today. Over real o2lite
this becomes `"stb"`. This is a framing detail, not a vocabulary change.

### 3.2 `/game/capture`

Actions are `open`, `close`, `abandon`. **`open` is where the label is declared,
on the wire, before any sample arrives.** That is the "operator declares before
they perform it" requirement made literal, and it is what makes an orphan sample
batch a rejectable error rather than an unlabelled mystery.

```jsonc
// open
{"capture_id": "shake-021", "label": "shake", "series": 3,
 "window_ms": 3000,
 "source": { /* see section 7 */ }}

// close
{"capture_id": "shake-021", "n": 301, "ok": true,
 "outputs": [{"t_ms": -1800.0, "event": "countdown", "level": 0.6},
             {"t_ms": 3000.0, "event": "window_end", "level": 0.6}]}

// abandon
{"capture_id": "shake-021", "reason": "operator cancelled"}
```

Ownership of the identifiers, so there is one answer rather than two:

- **`capture_id` is generated by the client**, as `<label>-<nnn>` with `nnn`
  monotonic per label within the session. It is the correlation key for every
  subsequent batch.
- **`session` is generated by the store**, once, when the Bit loads. The client
  never sees or sets it.
- **`window_ms` is configured in the client**, per label, and shipped on `open`
  so the trace records the window it was actually captured under rather than
  whatever the client's default happens to be later.
- **Rates, units and stream identity live only in `source`**, never duplicated at
  the top level of `open`. One place to read them, one place for them to be
  wrong.

### 3.3 `/game/telemetry`

One batch per approximately 100 ms:

```jsonc
{"capture_id": "shake-021", "seq": 7,
 "t_ms": [700.1, 710.0, 719.8, ...],
 "ax": [...], "ay": [...], "az": [...],
 "gx": [...], "gy": [...], "gz": [...],
 "pcm": "<base64 int16le>", "pcm_t0_ms": 700.4}
```

Four decisions inside that blob, each load-bearing:

**Per-sample timestamps, not an assumed rate.** `sensors_plus`'s
`samplingPeriod` is a request, not a guarantee, and iOS delivery is jittery. That
jitter is data about the stream, not noise to be papered over by asserting a
nominal rate. `t_ms` is an offset from the capture's `t0`, so each batch is
self-locating even if a neighbour is lost.

**Structure of arrays.** Slices cleanly, compresses better in JSON, and drops
straight into pandas or numpy without a reshape.

**`seq` is monotonic per capture.** The store detects a dropped batch and stamps
the resulting trace with `"gaps": [...]` rather than quietly emitting a trace
with an invisible hole in it. A trace that lies about its own continuity is worse
than no trace.

**`pcm_t0_ms` is separate from `t_ms`.** The audio clock and the sensor clock are
independent. Anyone measuring a tap's audio-versus-accelerometer lead needs to
know the alignment error is on the order of one audio buffer, not zero. The trace
says so in words as well (section 6).

### 3.4 Channels and rates

- Accelerometer, 3-axis, `sensors_plus.accelerometerEventStream`, **gravity
  included**, requested at 100 Hz, units m/s^2.
- Gyroscope, 3-axis, `sensors_plus.gyroscopeEventStream`, requested at 100 Hz,
  units rad/s.
- Microphone, 16 kHz mono int16.

Gyroscope is in from day one because the stated framing of a shake is "speed,
force, and angles in a clean sweep". Angle and sweep are rotation, which the
accelerometer does not show, and the tap-versus-shake separation plausibly lives
there. You cannot derive a feature you did not record, and adding gyro later
means re-running every capture session.

Microphone is in because a tap on the phone body is a percussive acoustic
transient and a shake is nearly silent, which makes audio a candidate
discriminator at least as strong as anything in the motion data. 16 kHz keeps the
whole attack and everything below 8 kHz, which is where a tap on a plastic body
puts its discriminating energy; full-rate 44.1 kHz would be roughly 4x the bytes
for content the gesture barely produces.

Recording accelerometer-including-gravity rather than user-acceleration is
deliberate: it is what `TapDetector` consumes today, so traces stay directly
comparable to the code we are trying to replace.

**Budget:** roughly 5 KB per batch, about 50 KB/s per device (motion is small;
PCM dominates at about 4.3 KB per 100 ms after base64).

## 4. Server components (mm-terrarium)

### 4.0 One engine change: a Bit handler can refuse

`GameServer.data()` today returns a refusal reason for its own checks (no Bit
running, device not registered, unknown verb, handler raised) but gives a Bit
handler no way to refuse a well-formed call for its own reasons. A handler either
returns a cue list or raises, and raising surfaces to the device as the generic
`"handler error"`.

Section 8 needs real refusals ("no open capture for that id", "source block
incomplete"), and a generic string tells the client nothing actionable. So the
handler contract widens by one line:

```python
cues = handler(dev, args)
if isinstance(cues, str):
    return cues          # handler-declared refusal, surfaced as /<dev>/error
```

Backwards compatible: `TestBit`'s `tilt` handler returns a list and is
unaffected. The `isinstance` check is not optional, because the existing
`if cues:` branch would otherwise iterate a refusal string character by character
and try to unpack each character as a MIDI cue tuple.

This is the only change outside the new files.

### 4.1 `capture/trace.py`

Pure, no I/O. The `Trace` record dataclass, the schema version constant, and
`to_dict()`. Testable with no filesystem.

### 4.2 `capture/store.py`

All filesystem contact lives here. `CaptureStore(root)` with `open_capture()`,
`append_batch()`, `close_capture()`, `abandon_capture()`.

```
captures/<session-id>/
  index.jsonl              one line appended per closed capture
  tap/001.json    001.wav
  shake/021.json  021.wav
  rest/001.json   001.wav
```

Default root `./captures/`, overridable on the harness driver, gitignored.

**Mic audio is a sidecar WAV, not base64 inside the JSON.** You can open it in
Audacity, you can listen to it, and the JSON stays readable. The JSON points at
the file.

**Writes happen at capture close, not per batch.** `append_batch()` accumulates
into the open capture's in-memory buffers and touches no file; `close_capture()`
does the single JSON write, the single WAV write, and the index append. That is
one file write per gesture, on the order of one every five seconds, so filesystem
contact never touches the hot path. A crash or a pulled cable loses at most the
capture in flight rather than the whole session. Resident state is bounded by one
window per device: a 3 s capture is roughly 100 KB, dominated by PCM.

### 4.3 `bits/capture_bit.py`

`CaptureBit`, a tool Bit rather than a production game Bit. It does not close the
repo's "no production Bit exists" gap and should not be read as doing so.

- **Role table:** one role, `recorder`, class `shared`, `scored=False`, granted by
  `CAPTURE_NODE`.
- **Empty `light_manifest` and `ugen_manifest`.** The phone is the whole
  instrument here and the Bit has no light or audio consequence to decide. This
  also keeps the no-light path exercised alongside `TestBit`'s `jammer`.
- **`verb_handlers()`** returns `{"capture": ..., "telemetry": ...}`. Both return
  `[]` on success, since there are no light cues, or a refusal string per
  section 4.0.
- **`update(dt)` never self-completes.** A capture session ends when the operator
  ends it, via the console's `abort`. `update(dt)` does run the per-capture idle
  timeout (section 7).
- **`status()`** returns live per-label counts, which captures are open, and bytes
  written. Because the Terrarium Console already renders `Bit.status()`, this
  makes **the console a live capture dashboard with no console changes at all**.

### 4.4 `harness/capture_smoke.py`

The driver, mirroring `harness/devicelink_smoke.py`:

```
python -m harness.capture_smoke --hold --host 0.0.0.0 --capture-dir ./captures
```

`--host 0.0.0.0` is required for a real phone to reach it, because
`DeviceLinkServer` binds `127.0.0.1` by default. See section 8.

### 4.5 `tools/trace_stats.py`

The offline feature-summary tool. Reads a capture directory and reports, per
trace: peak |a| and peak deviation from rest, time above each of a candidate
threshold ladder, gesture span from first to last sample above threshold,
peak |omega|, integrated swept angle in degrees, inter-spike intervals, mic peak
dBFS, and mic attack time. Per label: the distribution of each. Plus `--csv`.

This is what turns "we have data" into "we have definitions". Without it, every
threshold derivation is a from-scratch task; with it, the tap-versus-shake
separation is visible in one command.

### 4.6 `docs/telemetry-trace-schema.md`

The cross-repo contract, describing the wire batch and the on-disk trace, and
naming its Dart counterparts. Referenced from `devicelink/protocol.py`.

## 5. The capture client (mm-tuneshroom)

`lib/capture_main.dart` plus `lib/capture/`:

| File | Job |
|---|---|
| `capture_controller.dart` | Arm/countdown/window/re-arm state machine, batch assembly, wire sends. No platform imports, so it is testable against a fake link exactly as `SimController` is. |
| `sensor_capture.dart` | Accelerometer and gyroscope at 100 Hz requested, each sample stamped on a device monotonic clock. |
| `mic_capture.dart` | 16 kHz mono int16 stream via the `record` package's `startStream`. |
| `cue_player.dart` | Countdown and window-end tones, and the output-event log. |
| `capture_screen.dart` | Label picker, Arm/Stop, countdown, per-label counters. |
| `detector_preview.dart` | Runs today's `TapDetector` and a Dart port of `detectSpike` on the live stream. |

### 5.1 The session loop

Pick a label, tap **Arm**, a 2 s countdown runs with audible beeps, the window
opens for the label's configured length, it auto-closes, and it **auto re-arms
for the same label** until you tap Stop. Each window is a numbered capture in a
series.

Two properties of that loop are the reason for it:

**Both the arming touch and the stopping touch fall outside the recorded window
by construction.** The screen tap that starts a recording is itself a tap on the
device. Any press-to-record or hold-to-record scheme contaminates the very trace
it is capturing. The countdown is not a nicety.

**Auto re-arm makes 20 reps of "shake" a 90 second job** rather than 20 separate
interactions, which is the difference between statistically useful traces and
anecdotes.

Window length is per-label: short for `tap` and `double tap`, longer for `rest`.

### 5.2 Live detector preview

The preview runs today's `TapDetector` and a Dart port of `sensors.js`'s
`detectSpike` on the live stream and shows what each **would** have fired. It is
labelled in the UI as "what today's code would fire".

**It never gates or alters what is recorded.** That is the whole point: it keeps
the known-inconsistent thresholds of section 1.1 out of the data while still
letting the operator watch the two detectors disagree in the hand, which is the
fastest way to build intuition for what the traces will show.

### 5.3 New dependencies

- `record` for the microphone stream.
- `audioplayers` for the cue tones.
- `NSMicrophoneUsageDescription` in `ios/Runner/Info.plist`.

## 6. Speaker and mic handling

Cue tones are scheduled in **pre-roll and post-close only**. The trace carries an
`outputs` log of what played, when, and at what level, with negative `t_ms` for
pre-roll events, so any bleed into the mic or the accelerometer is attributable
rather than mysterious.

A **per-label opt-in** can deliberately fire a cue *inside* the window, for when
you want to measure speaker-to-mic and speaker-to-accelerometer coupling. Off by
default.

Two operational gotchas, both going into the deep-dive when this lands:

1. **On iOS an `AVAudioSession` in `playAndRecord` routes to the earpiece unless
   `defaultToSpeaker` is set.** The client sets the category explicitly, once,
   rather than letting `record` and `audioplayers` each try to own it. Two
   packages contending for one shared audio session is the most likely first live
   bug.
2. **Mic permission denied is not fatal.** The client sends `abandon` for the
   in-flight capture, then continues motion-only with `"audio": null`. Losing
   audio must not lose the session.

## 7. The trace record

`captures/<session>/<label>/<nnn>.json`, with `<nnn>.wav` alongside it.

```jsonc
{
  "schema": "mm-telemetry-trace/1",
  "session": "2026-08-07T14-22-03Z-3f9a",
  "capture_id": "shake-021", "label": "shake", "series": 3, "dev": "ie1",
  "bit": {"name": "capture", "version": "0.1"},
  "source": {
    "client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
    "platform": "ios 18.5", "device_model": "iPhone 15",
    "motion_stream": "sensors_plus.accelerometer+gyroscope",
    "gravity_included": true, "requested_hz": 100,
    "units": {"accel": "m/s^2", "gyro": "rad/s"},
    "audio_stream": "record.startStream",
    "audio": {"rate": 16000, "bits": 16, "channels": 1}
  },
  "window_ms": 3000, "t0_device": 12345.678, "n": 301,
  "gaps": [], "truncated": false,
  "samples": {"t_ms": [], "ax": [], "ay": [], "az": [],
              "gx": [], "gy": [], "gz": []},
  "audio": {"file": "021.wav", "rate": 16000, "channels": 1, "t0_ms": 0.4,
            "clock": "audio clock, host-aligned, NOT sample-locked to motion"},
  "outputs": [{"t_ms": -1800.0, "event": "countdown", "level": 0.6}],
  "notes": ""
}
```

### 7.1 Why `source` is load-bearing

The `source` block is not bookkeeping. **A threshold is only meaningful against
the stream that produced it.** Dropping that association is precisely how
`www/sensors.js` came to carry a number copied from a different stream, applied
to a different quantity, with a comment asserting an equivalence that was never
true (section 1.1).

Every threshold derived from these traces will cite a `source` block. A trace
whose `source` is missing or incomplete is not usable and the store rejects it at
`open`.

## 8. Error handling

All of it obeys boundary rule 2: nothing in this path may propagate into the
engine tick.

| Case | Behaviour |
|---|---|
| Malformed batch | Drop the frame, log, stamp a gap on the open capture. Never raise. |
| `telemetry` for an unopened `capture_id` | Refuse per 4.0, surfaced as `/<dev>/error`, drop the batch. An unlabelled trace is worthless, and silently accepting one hides a client bug. |
| `open` with a missing or incomplete `source` block | Refuse per 4.0. See 7.1. |
| `open` for a `capture_id` already open on that device | Refuse per 4.0. The client is the sole generator of `capture_id` (3.2), so a collision is a client bug, not a race. |
| `close` never arrives (phone leaves WiFi) | A per-capture idle timeout in `update(dt)` closes it with `"truncated": true`. Without this a dropped device leaves a capture open forever. |
| Device released mid-capture | Same, closed truncated. |
| Mic permission denied | Session continues motion-only, `"audio": null`. |
| Disk write fails | Log, note it in the index, keep running. Never wedge. |

### 8.1 Trust model, unchanged and now more exposed

`DeviceLinkServer` binds `127.0.0.1` by default. A real phone needs
`--host 0.0.0.0`. The trust model is unchanged from `devicelink/` and `console/`:
trusted LAN, no authentication. What changes is that an actual handheld device is
now on that network, so the assumption is being exercised rather than assumed.

### 8.2 What this does not measure

None of this is o2lite. It is the same direct websocket to Control that
`devicelink/` already uses, with Arco nowhere in the path. **Nothing measured
here is a hop count or a latency figure**, and no timing number from a capture
session may be quoted as one.

## 9. Testing

The suite stays fully offline, which is the repo's load-bearing property. No
phone, no microphone, no Arco, no O2.

**Python:**

- `tests/test_capture_batch.py`: batch decode and validation, including every
  malformed shape.
- `tests/test_capture_store.py`: file layout, index append, WAV sidecar bytes,
  gap detection, truncation on timeout, orphan rejection, missing-`source`
  rejection, disk-failure tolerance (monkeypatched).
- `tests/test_capture_bit.py`: role table, verb handlers, `status()`, lifecycle.
- `tests/test_engine_data.py` (extended): a handler returning a string is
  surfaced as a refusal rather than iterated as cues, and a handler returning a
  cue list still works. This pins the 4.0 contract change.
- `tests/test_capture_smoke.py`: a synthetic device pushing a scripted batch
  sequence through the in-process fake server end to end, producing a real trace
  file in `tmp_path`. This is the regression that makes the whole path
  exercisable with no hardware.
- `tests/test_trace_stats.py`: features computed against hand-built traces with
  known analytic answers.

**Dart:**

- `test/capture_controller_test.dart`: the arm/countdown/window/re-arm state
  machine and batch assembly against a fake link, no platform imports.

**The keystone, spanning both:**

- A trace written by the Python store is loaded by the Dart `TraceSource`,
  replayed, and the emitted batches compared against the originals. **That is what
  makes "the preset button emits exactly what a real phone produced" a tested
  claim rather than an intention.** It runs in mm-tuneshroom against a trace
  fixture checked in from a real session.

## 10. The preset payoff

A curated capture becomes a named preset button in the simulator's control
surface.

1. You pick good captures from `captures/` and copy their JSON into
   `mm-tuneshroom/assets/traces/`.
2. `lib/sim/trace_source.dart` loads a trace and replays it at its **recorded
   timing**, emitting byte-identical `/game/telemetry` batches.
3. `sim_screen.dart` gains one button per asset, named by the preset.

Curation is deliberately a human step. There is no build-time coupling between
the repos, the sim stays runnable with no Terrarium reachable, and a bad capture
does not automatically become a button.

## 11. Slice order

1. **Server half.** Wire batch decoder, `capture/` package, `CaptureBit`,
   `capture_smoke` driver, offline tests. No phone needed.
2. **Capture client.** `lib/capture/`, the iOS build, and the first real capture
   session on the phone.
3. **Analysis.** `tools/trace_stats.py`, and the actual derived tap and shake
   feature definitions.
4. **Presets.** Trace assets, `TraceSource`, control-surface buttons, and the
   round-trip test.

Slice 4 cannot start before slice 2 has produced data. Slice 3 is where this
design stops and the detector-rewrite spec begins.
