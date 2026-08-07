# Telemetry trace schema

The cross-repo contract for sensor telemetry capture. The Python side is
`devicelink/protocol.py` (wire) and `capture/trace.py` (on disk); the Dart
side is `mm-tuneshroom/lib/capture/` (producer) and
`mm-tuneshroom/lib/sim/trace_source.dart` (preset replay). **Change them
together.**

Design: [`docs/superpowers/specs/2026-08-07-sensor-telemetry-capture-design.md`](superpowers/specs/2026-08-07-sensor-telemetry-capture-design.md).

Two schema strings are frozen literals:

| Constant | Value | Defined in |
|---|---|---|
| `TELEMETRY_BATCH_SCHEMA` | `mm-telemetry-batch/1` | `devicelink/protocol.py` |
| `TRACE_SCHEMA` | `mm-telemetry-trace/1` | `capture/trace.py` |

## Wire

Both verbs ride the generic `/game/<verb>` dispatch path, so the transport
needs no knowledge of them.

```
/game/capture   "ssb"  dev  action  meta
/game/telemetry "sfb"  dev  t0      batch
```

`/game/telemetry` is a concrete instance of the design doc's
`/game/data "stb" dev time payload`: the same `dev`/`time`/`payload` triple,
renamed because `GameServer.data()` is already the dispatch method's name.
Typespec is `"sfb"` rather than `"stb"` because `devicelink/protocol.py`
documents only `s`/`i`/`f`/`b`; over real o2lite it becomes `"stb"`.

### `/game/capture`

`action` is `open`, `close` or `abandon`. **`open` declares the label before
any sample arrives**, which is what makes an orphan batch a rejectable error
rather than an unlabelled mystery.

```jsonc
// open
{"capture_id": "shake-021", "label": "shake", "series": 3,
 "window_ms": 3000, "t0": 12345.678, "source": { /* below */ }}

// close
{"capture_id": "shake-021", "n": 301, "ok": true,
 "outputs": [{"t_ms": -1800.0, "event": "countdown", "level": 0.6},
             {"t_ms": 3000.0, "event": "window_end", "level": 0.6}]}

// abandon
{"capture_id": "shake-021", "reason": "mic permission denied"}
```

Identifier ownership, so there is one answer rather than two:

- **`capture_id`: the client**, as `<label>-<nnn>`, `nnn` monotonic per label
  within the session. It is the correlation key for every subsequent batch.
- **`session`: the store**, once, when the Bit loads. The client never sees it.
- **`window_ms`: the client**, per label, shipped on `open` so the trace
  records the window it was actually captured under.
- **`t0`: the client**, its own clock reading at the moment the window opened.
  Becomes the trace's `t0_device`, the anchor every batch's `t_ms` offsets are
  relative to. Design Rule 4 (timestamps at the source): this must come from
  the device, never be synthesized server-side.
- **Rates, units and stream identity: only inside `source`.** Never duplicated
  at the top level.

`series` becomes the trace's filename (`003.json`), so it must be unique per
label within a session.

### `/game/telemetry`

One batch per roughly 100 ms.

```jsonc
{"capture_id": "shake-021", "seq": 7,
 "t_ms": [700.1, 710.0, 719.8],
 "ax": [], "ay": [], "az": [],
 "gx": [], "gy": [], "gz": [],
 "pcm": "<base64 int16le>", "pcm_t0_ms": 700.4}
```

| Field | Rule |
|---|---|
| `capture_id` | Non-empty string. Must match the device's currently open capture, else refused. |
| `seq` | Non-negative int, monotonic per capture. A skip is recorded as a gap; a repeat or a decrease is refused. |
| `t_ms` | Non-empty, non-decreasing, offsets in ms from the capture's `t0`. **Per-sample, not an assumed rate:** `sensors_plus`'s `samplingPeriod` is a request, and the resulting jitter is data. |
| `ax`..`gz` | All six required, each the same length as `t_ms`. Structure of arrays. Accel in m/s^2 **including gravity**; gyro in rad/s. |
| `pcm` | Optional. Base64 of raw int16 little-endian mono. Even byte length. |
| `pcm_t0_ms` | Required whenever `pcm` is present. **On the audio clock, not the sensor clock.** |

The audio and motion clocks are independent. Alignment error is on the order
of one audio buffer, not zero. Do not compute an audio-versus-accelerometer
lead without accounting for it.

### The `source` block

Required on `open`, and the reason this schema exists at all. **A threshold is
only meaningful against the stream that produced it.** `www/sensors.js`
carries a threshold copied from a different stream, applied to a different
quantity, under a comment asserting an equivalence that was never true. Every
threshold derived from these traces cites a `source` block.

```jsonc
{"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
 "platform": "ios 18.5", "device_model": "iPhone 15",
 "motion_stream": "sensors_plus.accelerometer+gyroscope",
 "gravity_included": true, "requested_hz": 100,
 "units": {"accel": "m/s^2", "gyro": "rad/s"},
 "audio_stream": "record.startStream",
 "audio": {"rate": 16000, "bits": 16, "channels": 1}}
```

Required: `client`, `app_version`, `platform`, `device_model`,
`motion_stream`, `gravity_included`, `requested_hz`, `units`. An `open`
missing any of them is refused.

Optional: `audio_stream` and `audio`, which are `null` on a motion-only
capture. **Mic permission denied is not a failed session.**

## On disk

```
captures/<session-id>/
  index.jsonl              one line per closed capture
  tap/001.json  001.wav
  shake/021.json
```

Session id is `%Y-%m-%dT%H-%M-%SZ` plus a 4-hex-character suffix, so it is
sortable, filesystem-safe and collision-free within a second. The filename
stem is the capture's `series`, zero-padded to three digits.

Mic audio is a **sidecar WAV, not base64 inside the JSON**, so it opens in
Audacity and the JSON stays readable. A motion-only capture has
`"audio": null` and no `.wav`.

```jsonc
{
  "schema": "mm-telemetry-trace/1",
  "session": "2026-08-07T14-22-03Z-3f9a",
  "capture_id": "shake-021", "label": "shake", "series": 3, "dev": "ie1",
  "bit": {"name": "capture", "version": "0.1"},
  "source": { /* verbatim from open */ },
  "window_ms": 3000.0, "t0_device": 12345.678, "n": 301,
  "gaps": [{"expected": 12, "got": 14}],
  "truncated": false,
  "samples": {"t_ms": [], "ax": [], "ay": [], "az": [],
              "gx": [], "gy": [], "gz": []},
  "audio": {"file": "003.wav", "rate": 16000, "channels": 1, "t0_ms": 0.4,
            "clock": "audio clock, host-aligned, NOT sample-locked to motion"},
  "outputs": [{"t_ms": -1800.0, "event": "countdown", "level": 0.6}],
  "notes": ""
}
```

`gaps` is non-empty when a batch was lost. `truncated` is true when the
capture was closed by the idle timeout or by Bit unload rather than by a
`close` from the client, and `notes` then carries the reason. **A consumer
deriving thresholds should skip traces with non-empty `gaps` or
`truncated: true` unless it has a reason not to.**

`index.jsonl`, one line per closed capture:

```jsonc
{"capture_id":"shake-021","label":"shake","series":3,"dev":"ie1",
 "n":301,"truncated":false,"gaps":0,"path":"shake/003.json"}
```

## Preset replay

A curated trace copied into `mm-tuneshroom/assets/traces/` is replayed by
`lib/sim/trace_source.dart` at its **recorded timing**, emitting
byte-identical `/game/telemetry` batches. Re-batching is free (the batch
boundaries are not semantic) but the per-sample `t_ms` values and the sample
values themselves must survive the round trip unchanged. That property has a
test on the mm-tuneshroom side.
