# Sensor Telemetry Capture, Server Half: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the mm-terrarium half of the sensor telemetry capture system, so that a real phone can stream labelled accelerometer, gyroscope and microphone telemetry into Control and have each gesture land on disk as an inspectable trace file.

**Architecture:** Two new `/game/*` verbs ride the existing generic verb-dispatch path (`DeviceLinkAgent._on_verb` -> `GameServer.data` -> `Bit.verb_handlers`), so the transport is untouched. `devicelink/protocol.py` gains the wire decoders. A new `capture/` package holds a pure in-memory `Trace` record and a `CaptureStore` that owns all filesystem contact. `bits/capture_bit.py` is a thin Bit wiring the two together. `harness/capture_smoke.py` is the driver, and `tools/trace_stats.py` reads the output back.

**Tech Stack:** Python 3, stdlib only for the new code (`base64`, `wave`, `json`, `pathlib`, `dataclasses`, `datetime`, `secrets`, `math`). pytest for tests.

**Source spec:** [`docs/superpowers/specs/2026-08-07-sensor-telemetry-capture-design.md`](../specs/2026-08-07-sensor-telemetry-capture-design.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **No new runtime dependencies.** All new code is Python stdlib only. Do not add to `requirements.txt` or `requirements-dev.txt`.
- **The offline suite stays green with no network, no O2, no Arco, no pyarco.** This is the repo's load-bearing property. Verify with `python -m pytest tests -v` after every task.
- **luxaeterna is a dev/test dependency, guarded.** Any test that imports `devicelink.agent` or `harness.capture_smoke` must begin with `pytest.importorskip("luxaeterna")`, because `devicelink/agent.py` imports `harness.device_bridge` at module level. Follow the existing pattern in `tests/test_devicelink_agent.py:11`. Tests that touch only `capture/`, `bits/capture_bit.py`, `devicelink/protocol.py` or `tools/` must NOT have this guard, so they run in the core suite.
- **Boundary rule 2: nothing in this path may propagate into the engine tick.** Every failure mode logs and continues. No new exception type may escape into `GameServer.tick`.
- **Never quote a timing figure from this path as a latency or hop count.** This is a direct websocket to Control with Arco nowhere in it.
- **Schema string constants are frozen wire contract.** `mm-telemetry-batch/1` and `mm-telemetry-trace/1` are literals, defined once, never inlined a second time.
- **Gravity constant is `9.80665` m/s^2** everywhere it appears, matching `mm-tuneshroom/lib/sensors/sensor_service.dart:59`.
- Run the full suite with `python -m pytest tests -v`.

## File Structure

| Path | Responsibility |
|---|---|
| `devicelink/protocol.py` (modify) | Wire decoders for the two new verbs. Stays the single source of truth for the wire shape. |
| `control/engine.py` (modify) | One-line contract widening: a Bit handler may refuse by returning a string. |
| `control/bit.py` (modify) | Docstring for the widened `verb_handlers()` contract. |
| `capture/__init__.py` (create) | Package marker. |
| `capture/trace.py` (create) | Pure in-memory `Trace` record: accumulates batches, detects seq gaps, serialises to the on-disk shape. No I/O. |
| `capture/store.py` (create) | All filesystem contact: session layout, WAV framing, index append, idle expiry. |
| `bits/capture_bit.py` (create) | The Bit: role table, verb handlers, `status()`, idle expiry from `update(dt)`. Thin by design. |
| `harness/capture_smoke.py` (create) | The driver, mirroring `harness/devicelink_smoke.py`. |
| `tools/__init__.py` (create) | Package marker. |
| `tools/trace_stats.py` (create) | Offline feature summary over a capture directory. |
| `docs/telemetry-trace-schema.md` (create) | The cross-repo contract mm-tuneshroom's capture client implements. |
| `.gitignore` (modify) | Ignore `captures/`. |

---

### Task 1: Wire decoders for `/game/capture` and `/game/telemetry`

**Files:**
- Modify: `devicelink/protocol.py` (append after `error_event`, line 89)
- Test: `tests/test_capture_batch.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `TELEMETRY_BATCH_SCHEMA: str = "mm-telemetry-batch/1"`
  - `MOTION_AXES: tuple[str, ...] = ("ax", "ay", "az", "gx", "gy", "gz")`
  - `REQUIRED_SOURCE_KEYS: frozenset[str]`
  - `@dataclass(frozen=True) TelemetryBatch(capture_id: str, seq: int, t_ms: list[float], axes: dict[str, list[float]], pcm: bytes, pcm_t0_ms: float | None)`
  - `@dataclass(frozen=True) CaptureCommand(action: str, capture_id: str, meta: dict)`
  - `decode_telemetry_batch(args: list) -> TelemetryBatch` — raises `ValueError`
  - `decode_capture_command(args: list) -> CaptureCommand` — raises `ValueError`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_capture_batch.py`:

```python
"""Wire decoders for /game/capture and /game/telemetry. No sockets, no
luxaeterna: these run in the core offline suite."""

import base64

import pytest

from devicelink.protocol import (
    MOTION_AXES,
    TELEMETRY_BATCH_SCHEMA,
    decode_capture_command,
    decode_telemetry_batch,
)


def _axes(n, fill=0.0):
    return {axis: [fill] * n for axis in MOTION_AXES}


def _batch(**over):
    body = {"capture_id": "shake-021", "seq": 0,
            "t_ms": [0.0, 10.0, 20.0], **_axes(3)}
    body.update(over)
    return ["ie1", 1234.5, body]


def _source(**over):
    src = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
           "platform": "ios 18.5", "device_model": "iPhone 15",
           "motion_stream": "sensors_plus.accelerometer+gyroscope",
           "gravity_included": True, "requested_hz": 100,
           "units": {"accel": "m/s^2", "gyro": "rad/s"}}
    src.update(over)
    return src


def _open(**over):
    meta = {"capture_id": "shake-021", "label": "shake", "series": 3,
            "window_ms": 3000, "t0": 12345.678, "source": _source()}
    meta.update(over)
    return ["ie1", "open", meta]


# --- telemetry -----------------------------------------------------------

def test_schema_constant_is_frozen():
    assert TELEMETRY_BATCH_SCHEMA == "mm-telemetry-batch/1"


def test_decodes_a_motion_only_batch():
    batch = decode_telemetry_batch(_batch())
    assert batch.capture_id == "shake-021"
    assert batch.seq == 0
    assert batch.t_ms == [0.0, 10.0, 20.0]
    assert batch.axes["ax"] == [0.0, 0.0, 0.0]
    assert batch.pcm == b""
    assert batch.pcm_t0_ms is None


def test_decodes_pcm_as_raw_bytes():
    pcm = (1234).to_bytes(2, "little", signed=True) * 4
    batch = decode_telemetry_batch(
        _batch(pcm=base64.b64encode(pcm).decode(), pcm_t0_ms=0.4))
    assert batch.pcm == pcm
    assert batch.pcm_t0_ms == 0.4


def test_ints_are_accepted_as_floats():
    batch = decode_telemetry_batch(_batch(t_ms=[0, 10, 20]))
    assert batch.t_ms == [0.0, 10.0, 20.0]


@pytest.mark.parametrize("args, message", [
    (["ie1", 1.0], "needs 3 args"),
    (["ie1", 1.0, "nope"], "batch must be an object"),
    (_batch(capture_id=""), "capture_id"),
    (_batch(capture_id=7), "capture_id"),
    (_batch(seq=-1), "seq"),
    (_batch(seq="0"), "seq"),
    (_batch(t_ms=[]), "t_ms"),
    (_batch(t_ms=[0.0, 20.0, 10.0]), "non-decreasing"),
    (_batch(t_ms=[0.0, "x", 20.0]), "t_ms"),
    (_batch(ax=[0.0, 0.0]), "ax"),
    (_batch(gz=None), "gz"),
    (_batch(pcm="not base64!!", pcm_t0_ms=0.0), "pcm"),
    (_batch(pcm=base64.b64encode(b"odd").decode(), pcm_t0_ms=0.0), "int16"),
    (_batch(pcm=base64.b64encode(b"\x00\x00").decode()), "pcm_t0_ms"),
])
def test_malformed_telemetry_is_rejected(args, message):
    with pytest.raises(ValueError) as exc:
        decode_telemetry_batch(args)
    assert message in str(exc.value)


def test_missing_axis_is_rejected():
    body = _batch()[2]
    del body["gy"]
    with pytest.raises(ValueError) as exc:
        decode_telemetry_batch(["ie1", 1.0, body])
    assert "gy" in str(exc.value)


# --- capture -------------------------------------------------------------

def test_decodes_open():
    cmd = decode_capture_command(_open())
    assert cmd.action == "open"
    assert cmd.capture_id == "shake-021"
    assert cmd.meta["label"] == "shake"
    assert cmd.meta["source"]["requested_hz"] == 100


def test_decodes_close_without_a_source_block():
    cmd = decode_capture_command(
        ["ie1", "close", {"capture_id": "shake-021", "n": 301, "ok": True,
                          "outputs": []}])
    assert cmd.action == "close"
    assert cmd.meta["n"] == 301


def test_decodes_abandon():
    cmd = decode_capture_command(
        ["ie1", "abandon", {"capture_id": "shake-021", "reason": "cancelled"}])
    assert cmd.action == "abandon"
    assert cmd.meta["reason"] == "cancelled"


def test_open_accepts_a_null_audio_block():
    """Mic permission denied is not fatal: the client still opens the
    capture, motion-only, with audio explicitly null."""
    cmd = decode_capture_command(
        _open(source=_source(audio_stream=None, audio=None)))
    assert cmd.meta["source"]["audio"] is None


@pytest.mark.parametrize("args, message", [
    (["ie1", "open"], "needs 3 args"),
    (["ie1", "wiggle", {"capture_id": "x"}], "unknown capture action"),
    (["ie1", "open", "nope"], "meta must be an object"),
    (["ie1", "open", {}], "capture_id"),
    (_open(label=""), "label"),
    (_open(series="3"), "series"),
    (_open(window_ms=0), "window_ms"),
    (_open(window_ms=-1), "window_ms"),
    (_open(t0="soon"), "t0"),
    (_open(source={}), "source"),
    (_open(source=None), "source"),
])
def test_malformed_capture_command_is_rejected(args, message):
    with pytest.raises(ValueError) as exc:
        decode_capture_command(args)
    assert message in str(exc.value)


def test_open_with_an_incomplete_source_block_is_rejected():
    """Section 7.1 of the spec: a threshold is only meaningful against the
    stream that produced it, so a trace with a partial source block is not
    usable and must never reach disk."""
    src = _source()
    del src["motion_stream"]
    with pytest.raises(ValueError) as exc:
        decode_capture_command(_open(source=src))
    assert "motion_stream" in str(exc.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_capture_batch.py -v`
Expected: FAIL at collection, `ImportError: cannot import name 'MOTION_AXES' from 'devicelink.protocol'`

- [ ] **Step 3: Implement the decoders**

Append to `devicelink/protocol.py`. Also add `import base64` and `import binascii` at the top of the file, alongside the existing `from dataclasses import dataclass`.

```python
# --- telemetry capture (see docs/telemetry-trace-schema.md) ---------------
#
# Two research verbs used by the capture Bit. Both ride the generic
# /game/<verb> dispatch path, so devicelink/agent.py needs no change.
# /game/telemetry is a concrete instance of the design doc's
# /game/data "stb" dev time payload -- same dev/time/payload triple, renamed
# because GameServer.data() is already the dispatch method's name. Typespec
# is "sfb" rather than "stb" because this module documents only s/i/f/b;
# over real o2lite it becomes "stb".

TELEMETRY_BATCH_SCHEMA = "mm-telemetry-batch/1"

MOTION_AXES = ("ax", "ay", "az", "gx", "gy", "gz")

CAPTURE_ACTIONS = ("open", "close", "abandon")

# Everything needed to say WHICH stream a trace came from. Enforced at
# `open` so a partial block can never reach disk: a threshold derived from a
# trace whose source is unknown is exactly the mistake www/sensors.js made.
# audio_stream/audio are deliberately NOT required -- mic permission denied
# is a motion-only capture, not a failed one.
REQUIRED_SOURCE_KEYS = frozenset({
    "client", "app_version", "platform", "device_model",
    "motion_stream", "gravity_included", "requested_hz", "units",
})


@dataclass(frozen=True)
class TelemetryBatch:
    capture_id: str
    seq: int
    t_ms: list
    axes: dict          # keyed by MOTION_AXES, each a list the length of t_ms
    pcm: bytes          # decoded int16le; b"" when the batch carries no audio
    pcm_t0_ms: float | None


@dataclass(frozen=True)
class CaptureCommand:
    action: str         # one of CAPTURE_ACTIONS
    capture_id: str
    meta: dict


def _number(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _number_list(value, field: str, length: int | None) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of numbers")
    if length is not None and len(value) != length:
        raise ValueError(f"{field} has {len(value)} values, expected {length}")
    return [_number(v, field) for v in value]


def _decode_pcm(body: dict) -> tuple[bytes, float | None]:
    raw = body.get("pcm")
    if raw is None:
        return b"", None
    if not isinstance(raw, str):
        raise ValueError("pcm must be a base64 string")
    try:
        pcm = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"pcm is not valid base64: {exc}") from exc
    if len(pcm) % 2:
        raise ValueError("pcm length is odd, so it is not int16 samples")
    if "pcm_t0_ms" not in body:
        raise ValueError("a batch carrying pcm must carry pcm_t0_ms")
    return pcm, _number(body["pcm_t0_ms"], "pcm_t0_ms")


def decode_telemetry_batch(args: list) -> TelemetryBatch:
    """Parse /game/telemetry "sfb" args: [dev, t0, batch].

    Raises ValueError on anything malformed. Callers treat that as 'refuse
    this batch', never as an engine error.
    """
    if not isinstance(args, list) or len(args) < 3:
        raise ValueError("/game/telemetry needs 3 args: dev, t0, batch")
    body = args[2]
    if not isinstance(body, dict):
        raise ValueError("batch must be an object")

    capture_id = body.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        raise ValueError("batch needs a non-empty string capture_id")

    seq = body.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValueError("batch needs a non-negative int seq")

    t_ms = _number_list(body.get("t_ms"), "t_ms", None)
    if not t_ms:
        raise ValueError("t_ms must not be empty")
    if any(b < a for a, b in zip(t_ms, t_ms[1:])):
        raise ValueError("t_ms must be non-decreasing")

    axes = {}
    for axis in MOTION_AXES:
        if axis not in body:
            raise ValueError(f"batch is missing axis {axis}")
        axes[axis] = _number_list(body[axis], axis, len(t_ms))

    pcm, pcm_t0_ms = _decode_pcm(body)
    return TelemetryBatch(capture_id=capture_id, seq=seq, t_ms=t_ms,
                          axes=axes, pcm=pcm, pcm_t0_ms=pcm_t0_ms)


def _validate_open(meta: dict) -> None:
    label = meta.get("label")
    if not isinstance(label, str) or not label:
        raise ValueError("open needs a non-empty string label")
    if isinstance(meta.get("series"), bool) or \
            not isinstance(meta.get("series"), int):
        raise ValueError("open needs an int series")
    if _number(meta.get("window_ms"), "window_ms") <= 0:
        raise ValueError("window_ms must be positive")
    # The device's own clock reading at the moment the capture window
    # opened -- what every batch's t_ms offsets are relative to. Design
    # Rule 4 (timestamps at the source): this must come from the device,
    # never be synthesized server-side, or every trace's t0_device would
    # silently read as a meaningless 0.0.
    _number(meta.get("t0"), "t0")
    source = meta.get("source")
    if not isinstance(source, dict):
        raise ValueError("open needs a source object")
    missing = sorted(REQUIRED_SOURCE_KEYS - set(source))
    if missing:
        raise ValueError(f"source is missing {', '.join(missing)}")


def decode_capture_command(args: list) -> CaptureCommand:
    """Parse /game/capture "ssb" args: [dev, action, meta].

    Raises ValueError on anything malformed.
    """
    if not isinstance(args, list) or len(args) < 3:
        raise ValueError("/game/capture needs 3 args: dev, action, meta")
    action = args[1]
    if action not in CAPTURE_ACTIONS:
        raise ValueError(f"unknown capture action {action!r}")
    meta = args[2]
    if not isinstance(meta, dict):
        raise ValueError("meta must be an object")
    capture_id = meta.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        raise ValueError("meta needs a non-empty string capture_id")
    if action == "open":
        _validate_open(meta)
    return CaptureCommand(action=action, capture_id=capture_id, meta=meta)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_capture_batch.py -v`
Expected: PASS, all cases.

Then run the full suite to confirm nothing regressed: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add devicelink/protocol.py tests/test_capture_batch.py
git commit -m "feat(protocol): wire decoders for /game/capture and /game/telemetry"
```

---

### Task 2: A Bit handler can refuse

Spec section 4.0. Today a handler either returns cues or raises, and raising surfaces to the device as the generic `"handler error"`. Task 5's handlers need to say *why* they refused.

**Files:**
- Modify: `control/engine.py:123-134` (the `data()` handler-call block)
- Modify: `control/bit.py:60-64` (the `verb_handlers()` docstring)
- Test: `tests/test_engine_data.py` (extend)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `GameServer.data(dev, verb, args) -> str | None` now also returns a handler-supplied refusal string. A handler returning `str` is a refusal; a handler returning a list is cues, unchanged.

- [ ] **Step 1: Write the failing tests**

In `tests/test_engine_data.py`, add a `refuse_next` attribute to `VerbBit`. Replace the existing `__init__` and `_on_tilt` with:

```python
    def __init__(self):
        self.seen = []
        self.raise_next = False
        self.refuse_next = None      # set to a str to exercise 4.0

    def _on_tilt(self, dev, args):
        if self.raise_next:
            raise RuntimeError("boom")
        if self.refuse_next is not None:
            return self.refuse_next
        self.seen.append((dev, args))
        return [(dev, 0xB0, 74, 64)]
```

Then append these tests to the end of the file:

```python
def test_handler_returning_a_string_is_a_refusal():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)
    gs.bit.refuse_next = "no open capture for 'shake-021'"

    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "no open capture for 'shake-021'"
    # The refusal must NOT be walked character by character as cues.
    assert cues == []
    assert gs.state.name == "SETUP"


def test_an_empty_refusal_still_carries_a_reason():
    """A device must never receive /<dev>/error with a blank reason: an
    empty string is a Bit bug, and a blank error frame hides it."""
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    gs.bit.refuse_next = ""
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "handler refused"


def test_returning_cues_still_works():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)
    assert gs.data("ie1", "tilt", ["ie1", 30.0]) is None
    assert cues == [("ie1", 0xB0, 74, 64)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_engine_data.py -v`
Expected: `test_handler_returning_a_string_is_a_refusal` FAILS. It returns `None` and `cues` is populated with garbage, because the existing `if cues:` branch iterates the string character by character and tries `self.on_light_cue(*cue)` on each character. That failure is the exact bug the `isinstance` check exists to prevent, so read the output before fixing it.

- [ ] **Step 3: Implement the contract change**

In `control/engine.py`, in `data()`, insert the new block between the `except` that returns `"handler error"` and the `if cues and self.on_light_cue is not None:` line:

```python
        try:
            cues = handler(dev, args)
        except Exception:
            logger.exception("Bit verb handler %r raised; ignoring", verb)
            return "handler error"
        if isinstance(cues, str):
            # A handler-declared refusal. Checked BEFORE the truthiness test
            # below, which would otherwise iterate the string character by
            # character and try to unpack each character as a cue tuple.
            # `or` guards a blank reason so /<dev>/error is never empty.
            return cues or "handler refused"
        if cues and self.on_light_cue is not None:
```

Then update the `data()` docstring's first paragraph to:

```python
        """Route a /game/<verb> message to the loaded Bit's verb handler.

        Returns None when handled, else a refusal reason a transport can
        surface as /<dev>/error. The reason is either engine-level (no Bit
        running, device not registered, unknown verb, handler raised) or
        handler-declared: a handler returning a str is refusing. Never
        raises: a device must never be able to wedge Control, exactly as a
        Bit must never be able to.
        """
```

And in `control/bit.py`, replace the `verb_handlers` docstring:

```python
    def verb_handlers(self) -> dict:
        """Extra /game/* verb handlers this Bit adds, beyond the fixed
        lifecycle verbs Control always handles. Empty by default.

        A handler is called as handler(dev, args) and returns either a list
        of (dev, status, data1, data2) light cues, or a str refusal reason
        that Control surfaces to that device as /<dev>/error. Returning a
        str is how a Bit rejects a well-formed call for its own reasons;
        raising is for bugs and yields a generic "handler error".
        """
        return {}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_engine_data.py -v`
Expected: PASS, including the pre-existing `test_data_routes_to_handler_and_emits_cue` and `test_raising_handler_is_contained`.

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add control/engine.py control/bit.py tests/test_engine_data.py
git commit -m "feat(control): a Bit verb handler can refuse by returning a reason"
```

---

### Task 3: The `Trace` record

Pure, no I/O. Accumulates decoded batches, detects sequence gaps, and serialises to the on-disk shape of spec section 7.

**Files:**
- Create: `capture/__init__.py`
- Create: `capture/trace.py`
- Test: `tests/test_capture_trace.py`

**Interfaces:**
- Consumes: `devicelink.protocol.TelemetryBatch`, `devicelink.protocol.MOTION_AXES` (Task 1).
- Produces:
  - `TRACE_SCHEMA: str = "mm-telemetry-trace/1"`
  - `class Trace` with `__init__(session, capture_id, label, series, dev, bit, source, window_ms, t0_device)`
  - `Trace.append(batch: TelemetryBatch) -> None` — raises `ValueError` on a stale seq
  - `Trace.to_dict(audio_file: str | None) -> dict`
  - attributes read by Task 4: `.pcm` (`bytearray`), `.label`, `.capture_id`, `.series`, `.truncated` (settable `bool`), `.notes` (settable `str`), `.outputs` (settable `list`), `.n` (property)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_capture_trace.py`:

```python
"""The in-memory Trace record: accumulation, gap detection, serialisation.
Pure -- no filesystem, no luxaeterna, core offline suite."""

import pytest

from capture.trace import TRACE_SCHEMA, Trace
from devicelink.protocol import MOTION_AXES, TelemetryBatch

SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"},
          "audio_stream": "record.startStream",
          "audio": {"rate": 16000, "bits": 16, "channels": 1}}


def make_trace(**over):
    kwargs = {"session": "2026-08-07T14-22-03Z-3f9a",
              "capture_id": "shake-021", "label": "shake", "series": 3,
              "dev": "ie1", "bit": {"name": "capture", "version": "0.1"},
              "source": SOURCE, "window_ms": 3000.0, "t0_device": 12345.678}
    kwargs.update(over)
    return Trace(**kwargs)


def make_batch(seq=0, t_ms=None, pcm=b"", pcm_t0_ms=None):
    t_ms = [0.0, 10.0] if t_ms is None else t_ms
    return TelemetryBatch(
        capture_id="shake-021", seq=seq, t_ms=t_ms,
        axes={axis: [float(i) for i in range(len(t_ms))]
              for axis in MOTION_AXES},
        pcm=pcm, pcm_t0_ms=pcm_t0_ms)


def test_schema_constant_is_frozen():
    assert TRACE_SCHEMA == "mm-telemetry-trace/1"


def test_a_fresh_trace_is_empty():
    trace = make_trace()
    assert trace.n == 0
    assert trace.gaps == []
    assert trace.truncated is False


def test_batches_concatenate_in_order():
    trace = make_trace()
    trace.append(make_batch(seq=0, t_ms=[0.0, 10.0]))
    trace.append(make_batch(seq=1, t_ms=[20.0, 30.0]))
    body = trace.to_dict(audio_file=None)
    assert body["n"] == 4
    assert body["samples"]["t_ms"] == [0.0, 10.0, 20.0, 30.0]
    assert body["samples"]["ax"] == [0.0, 1.0, 0.0, 1.0]
    assert body["gaps"] == []


def test_a_skipped_seq_is_recorded_as_a_gap():
    """A trace that lies about its own continuity is worse than no trace."""
    trace = make_trace()
    trace.append(make_batch(seq=0))
    trace.append(make_batch(seq=3))
    body = trace.to_dict(audio_file=None)
    assert body["gaps"] == [{"expected": 1, "got": 3}]
    assert body["n"] == 4          # the surviving samples are still kept


def test_a_stale_seq_is_rejected_rather_than_corrupting_the_trace():
    trace = make_trace()
    trace.append(make_batch(seq=0))
    trace.append(make_batch(seq=1))
    with pytest.raises(ValueError) as exc:
        trace.append(make_batch(seq=1))
    assert "stale" in str(exc.value)
    assert trace.n == 4            # unchanged by the rejected batch


def test_pcm_accumulates_and_the_first_offset_wins():
    trace = make_trace()
    trace.append(make_batch(seq=0, pcm=b"\x01\x00", pcm_t0_ms=0.4))
    trace.append(make_batch(seq=1, pcm=b"\x02\x00", pcm_t0_ms=100.4))
    assert bytes(trace.pcm) == b"\x01\x00\x02\x00"
    body = trace.to_dict(audio_file="021.wav")
    assert body["audio"]["t0_ms"] == 0.4
    assert body["audio"]["file"] == "021.wav"
    assert body["audio"]["rate"] == 16000


def test_a_motion_only_trace_serialises_audio_as_null():
    """Mic permission denied is not fatal (spec 6.2)."""
    trace = make_trace()
    trace.append(make_batch(seq=0))
    assert trace.to_dict(audio_file=None)["audio"] is None


def test_the_serialised_shape_matches_the_spec():
    trace = make_trace()
    trace.append(make_batch(seq=0, pcm=b"\x00\x00", pcm_t0_ms=0.0))
    trace.outputs = [{"t_ms": -1800.0, "event": "countdown", "level": 0.6}]
    trace.notes = "third rep felt sloppy"
    body = trace.to_dict(audio_file="021.wav")

    assert body["schema"] == TRACE_SCHEMA
    assert body["session"] == "2026-08-07T14-22-03Z-3f9a"
    assert body["capture_id"] == "shake-021"
    assert body["label"] == "shake"
    assert body["series"] == 3
    assert body["dev"] == "ie1"
    assert body["bit"] == {"name": "capture", "version": "0.1"}
    assert body["source"] == SOURCE
    assert body["window_ms"] == 3000.0
    assert body["t0_device"] == 12345.678
    assert body["truncated"] is False
    assert body["notes"] == "third rep felt sloppy"
    assert body["outputs"][0]["event"] == "countdown"
    assert set(body["samples"]) == {"t_ms", *MOTION_AXES}
    assert "NOT sample-locked" in body["audio"]["clock"]


def test_truncated_is_reported():
    trace = make_trace()
    trace.append(make_batch(seq=0))
    trace.truncated = True
    assert trace.to_dict(audio_file=None)["truncated"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_capture_trace.py -v`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'capture'`

- [ ] **Step 3: Implement**

Create `capture/__init__.py` (empty file).

Create `capture/trace.py`:

```python
"""The in-memory trace record a capture accumulates into.

Pure: no filesystem, no clock, no transport. capture/store.py owns all I/O.
The serialised shape is the cross-repo contract in
docs/telemetry-trace-schema.md -- change both together.
"""

from __future__ import annotations

from devicelink.protocol import MOTION_AXES, TelemetryBatch

TRACE_SCHEMA = "mm-telemetry-trace/1"

# The motion samples and the audio samples come off two independent clocks.
# Anyone measuring a tap's audio-versus-accelerometer lead needs to know the
# alignment error is about one audio buffer, not zero, so the trace says so
# in words rather than leaving it to be rediscovered.
_AUDIO_CLOCK_NOTE = "audio clock, host-aligned, NOT sample-locked to motion"

DEFAULT_AUDIO_RATE = 16000


class Trace:
    def __init__(self, session: str, capture_id: str, label: str, series: int,
                 dev: str, bit: dict, source: dict, window_ms: float,
                 t0_device: float):
        self.session = session
        self.capture_id = capture_id
        self.label = label
        self.series = series
        self.dev = dev
        self.bit = dict(bit)
        self.source = dict(source)
        self.window_ms = float(window_ms)
        self.t0_device = float(t0_device)

        self.t_ms: list = []
        self.axes: dict = {axis: [] for axis in MOTION_AXES}
        self.pcm = bytearray()
        self.pcm_t0_ms: float | None = None
        self.gaps: list = []
        self.outputs: list = []
        self.truncated = False
        self.notes = ""
        self._next_seq = 0

    @property
    def n(self) -> int:
        return len(self.t_ms)

    def append(self, batch: TelemetryBatch) -> None:
        """Concatenate a decoded batch. A skipped seq is recorded as a gap
        and the batch is still kept; a stale or duplicate seq raises, because
        appending it would silently corrupt the sample order."""
        if batch.seq < self._next_seq:
            raise ValueError(
                f"stale batch seq {batch.seq}, expected {self._next_seq}")
        if batch.seq > self._next_seq:
            self.gaps.append({"expected": self._next_seq, "got": batch.seq})
        self._next_seq = batch.seq + 1

        self.t_ms.extend(batch.t_ms)
        for axis in MOTION_AXES:
            self.axes[axis].extend(batch.axes[axis])
        if batch.pcm:
            if self.pcm_t0_ms is None:
                self.pcm_t0_ms = batch.pcm_t0_ms
            self.pcm.extend(batch.pcm)

    def _audio_dict(self, audio_file: str | None) -> dict | None:
        if audio_file is None or not self.pcm:
            return None
        declared = self.source.get("audio") or {}
        return {"file": audio_file,
                "rate": declared.get("rate", DEFAULT_AUDIO_RATE),
                "channels": declared.get("channels", 1),
                "t0_ms": self.pcm_t0_ms,
                "clock": _AUDIO_CLOCK_NOTE}

    def to_dict(self, audio_file: str | None) -> dict:
        return {
            "schema": TRACE_SCHEMA,
            "session": self.session,
            "capture_id": self.capture_id,
            "label": self.label,
            "series": self.series,
            "dev": self.dev,
            "bit": self.bit,
            "source": self.source,
            "window_ms": self.window_ms,
            "t0_device": self.t0_device,
            "n": self.n,
            "gaps": self.gaps,
            "truncated": self.truncated,
            "samples": {"t_ms": self.t_ms,
                        **{axis: self.axes[axis] for axis in MOTION_AXES}},
            "audio": self._audio_dict(audio_file),
            "outputs": self.outputs,
            "notes": self.notes,
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_capture_trace.py -v`
Expected: PASS.

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add capture/__init__.py capture/trace.py tests/test_capture_trace.py
git commit -m "feat(capture): the in-memory Trace record with seq-gap detection"
```

---

### Task 4: `CaptureStore`, all filesystem contact

**Files:**
- Create: `capture/store.py`
- Modify: `.gitignore`
- Test: `tests/test_capture_store.py`

**Interfaces:**
- Consumes: `capture.trace.Trace` (Task 3), `devicelink.protocol.CaptureCommand` and `TelemetryBatch` (Task 1).
- Produces:
  - `class CaptureError(Exception)` — its `str()` is the refusal reason a Bit returns
  - `new_session_id(now=None, suffix=None) -> str`
  - `wav_bytes(pcm: bytes, rate: int, channels: int = 1) -> bytes`
  - `class CaptureStore(root, session_id, bit, clock=time.monotonic)` with
    `open_capture(dev, cmd) -> None`, `append(dev, batch) -> None`,
    `close_capture(dev, meta) -> None`, `abandon(dev, reason) -> None`,
    `expire(idle_s) -> list[str]`, `truncate_all(reason) -> list[str]`,
    `open_ids() -> dict[str, str]`, `counts() -> dict[str, int]`,
    `failures: int`, `bytes_written: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_capture_store.py`:

```python
"""CaptureStore: layout, WAV sidecars, the index, gaps, expiry, and the
failure modes that must never wedge the tick loop. Uses tmp_path only --
no luxaeterna, core offline suite."""

import json
import struct
import wave

import pytest

from capture.store import CaptureError, CaptureStore, new_session_id, wav_bytes
from devicelink.protocol import (MOTION_AXES, CaptureCommand, TelemetryBatch)

BIT = {"name": "capture", "version": "0.1"}
SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"},
          "audio_stream": "record.startStream",
          "audio": {"rate": 16000, "bits": 16, "channels": 1}}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def open_cmd(capture_id="shake-021", label="shake", series=3):
    return CaptureCommand(action="open", capture_id=capture_id,
                          meta={"capture_id": capture_id, "label": label,
                                "series": series, "window_ms": 3000.0,
                                "t0": 12345.678, "source": SOURCE})


def close_cmd(capture_id="shake-021", outputs=None):
    return CaptureCommand(action="close", capture_id=capture_id,
                          meta={"capture_id": capture_id, "n": 2, "ok": True,
                                "outputs": outputs or []})


def batch(seq=0, capture_id="shake-021", t_ms=None, pcm=b"", pcm_t0_ms=None):
    t_ms = [0.0, 10.0] if t_ms is None else t_ms
    return TelemetryBatch(capture_id=capture_id, seq=seq, t_ms=t_ms,
                          axes={a: [1.0] * len(t_ms) for a in MOTION_AXES},
                          pcm=pcm, pcm_t0_ms=pcm_t0_ms)


def make_store(tmp_path, clock=None):
    return CaptureStore(root=tmp_path, session_id="SESSION", bit=BIT,
                        clock=clock or FakeClock())


# --- session ids ---------------------------------------------------------

def test_session_id_is_filesystem_safe_and_sortable():
    import datetime
    sid = new_session_id(
        now=datetime.datetime(2026, 8, 7, 14, 22, 3,
                              tzinfo=datetime.timezone.utc),
        suffix="3f9a")
    assert sid == "2026-08-07T14-22-03Z-3f9a"
    assert ":" not in sid


def test_session_ids_differ_without_arguments():
    assert new_session_id() != new_session_id()


# --- wav framing ---------------------------------------------------------

def test_wav_bytes_is_a_readable_16bit_mono_wav():
    pcm = struct.pack("<4h", 0, 1000, -1000, 32767)
    data = wav_bytes(pcm, rate=16000)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"

    import io
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.readframes(4) == pcm


# --- the happy path ------------------------------------------------------

def test_a_closed_capture_lands_on_disk_with_its_wav(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch(seq=0, pcm=struct.pack("<2h", 5, -5),
                              pcm_t0_ms=0.4))
    store.close_capture("ie1", close_cmd().meta)

    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["label"] == "shake"
    assert body["capture_id"] == "shake-021"
    assert body["n"] == 2
    assert body["truncated"] is False
    assert body["audio"]["file"] == "003.wav"
    assert (tmp_path / "SESSION" / "shake" / "003.wav").exists()


def test_t0_device_comes_from_the_open_command_not_a_default(tmp_path):
    """Design Rule 4, timestamps at the source: t0_device must be the
    device's own clock reading, or every trace's offsets would silently
    anchor to a meaningless 0.0 instead of a real moment."""
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd().meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["t0_device"] == 12345.678


def test_the_file_number_comes_from_the_series(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd(capture_id="tap-007", label="tap",
                                       series=7))
    store.append("ie1", batch(capture_id="tap-007"))
    store.close_capture("ie1", close_cmd(capture_id="tap-007").meta)
    assert (tmp_path / "SESSION" / "tap" / "007.json").exists()


def test_the_index_gains_one_line_per_closed_capture(tmp_path):
    store = make_store(tmp_path)
    for series, label in ((1, "tap"), (2, "shake")):
        cid = f"{label}-{series:03d}"
        store.open_capture("ie1", open_cmd(cid, label, series))
        store.append("ie1", batch(capture_id=cid))
        store.close_capture("ie1", close_cmd(cid).meta)

    lines = (tmp_path / "SESSION" / "index.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["label"] == "tap"
    assert first["path"] == "tap/001.json"
    assert first["n"] == 2
    assert first["truncated"] is False


def test_outputs_from_close_reach_the_trace(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd(outputs=[
        {"t_ms": -1800.0, "event": "countdown", "level": 0.6}]).meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["outputs"][0]["event"] == "countdown"


def test_a_motion_only_capture_writes_no_wav(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd().meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["audio"] is None
    assert not (tmp_path / "SESSION" / "shake" / "003.wav").exists()


def test_a_dropped_batch_is_stamped_as_a_gap(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch(seq=0))
    store.append("ie1", batch(seq=2))
    store.close_capture("ie1", close_cmd().meta)
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["gaps"] == [{"expected": 1, "got": 2}]


# --- refusals ------------------------------------------------------------

def test_telemetry_for_an_unopened_capture_is_refused(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(CaptureError) as exc:
        store.append("ie1", batch())
    assert "no open capture" in str(exc.value)


def test_telemetry_for_the_wrong_capture_id_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    with pytest.raises(CaptureError) as exc:
        store.append("ie1", batch(capture_id="tap-001"))
    assert "tap-001" in str(exc.value)


def test_opening_twice_on_one_device_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    with pytest.raises(CaptureError) as exc:
        store.open_capture("ie1", open_cmd())
    assert "already open" in str(exc.value)


def test_closing_nothing_is_refused(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(CaptureError):
        store.close_capture("ie1", close_cmd().meta)


def test_a_stale_batch_is_refused_not_appended(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch(seq=0))
    store.append("ie1", batch(seq=1))
    with pytest.raises(CaptureError) as exc:
        store.append("ie1", batch(seq=1))
    assert "stale" in str(exc.value)


def test_two_devices_capture_independently(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd("shake-021", "shake", 21))
    store.open_capture("ie2", open_cmd("tap-004", "tap", 4))
    store.append("ie1", batch(capture_id="shake-021"))
    store.append("ie2", batch(capture_id="tap-004"))
    store.close_capture("ie1", close_cmd("shake-021").meta)
    store.close_capture("ie2", close_cmd("tap-004").meta)
    assert (tmp_path / "SESSION" / "shake" / "021.json").exists()
    assert (tmp_path / "SESSION" / "tap" / "004.json").exists()


# --- abandon, expiry, unload --------------------------------------------

def test_abandon_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())
    store.abandon("ie1", "mic permission denied")
    assert not (tmp_path / "SESSION" / "shake").exists()
    assert store.open_ids() == {}


def test_an_idle_capture_is_closed_truncated(tmp_path):
    """A phone that walks out of WiFi range must not leave a capture open
    forever."""
    clock = FakeClock()
    store = make_store(tmp_path, clock)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())

    clock.advance(5.0)
    assert store.expire(10.0) == []

    clock.advance(6.0)
    assert store.expire(10.0) == ["ie1"]
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["truncated"] is True
    assert store.open_ids() == {}


def test_activity_resets_the_idle_timer(tmp_path):
    clock = FakeClock()
    store = make_store(tmp_path, clock)
    store.open_capture("ie1", open_cmd())
    clock.advance(9.0)
    store.append("ie1", batch(seq=0))
    clock.advance(9.0)
    assert store.expire(10.0) == []


def test_truncate_all_closes_everything_still_open(tmp_path):
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd("shake-021", "shake", 21))
    store.open_capture("ie2", open_cmd("tap-004", "tap", 4))
    store.append("ie1", batch(capture_id="shake-021"))
    store.append("ie2", batch(capture_id="tap-004"))
    assert sorted(store.truncate_all("bit unloaded")) == ["ie1", "ie2"]
    assert store.open_ids() == {}
    for label, num in (("shake", "021"), ("tap", "004")):
        body = json.loads(
            (tmp_path / "SESSION" / label / f"{num}.json").read_text())
        assert body["truncated"] is True
        assert body["notes"] == "bit unloaded"


# --- status read-out -----------------------------------------------------

def test_counts_and_open_ids_track_the_session(tmp_path):
    store = make_store(tmp_path)
    assert store.counts() == {}
    store.open_capture("ie1", open_cmd())
    assert store.open_ids() == {"ie1": "shake-021"}
    store.append("ie1", batch())
    store.close_capture("ie1", close_cmd().meta)
    assert store.counts() == {"shake": 1}
    assert store.open_ids() == {}
    assert store.bytes_written > 0


# --- failure containment -------------------------------------------------

def test_a_failing_write_is_contained_and_counted(tmp_path, monkeypatch):
    """Boundary rule 2: a full disk must never wedge the tick loop."""
    store = make_store(tmp_path)
    store.open_capture("ie1", open_cmd())
    store.append("ie1", batch())

    def boom(*_a, **_kw):
        raise OSError("no space left on device")

    monkeypatch.setattr("pathlib.Path.write_text", boom)
    monkeypatch.setattr("pathlib.Path.write_bytes", boom)

    store.close_capture("ie1", close_cmd().meta)      # must not raise
    assert store.failures == 1
    assert store.open_ids() == {}                      # capture still released
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_capture_store.py -v`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'capture.store'`

- [ ] **Step 3: Implement**

Create `capture/store.py`:

```python
"""CaptureStore: the only filesystem-touching code in the capture package.

Layout, one directory per session:

    <root>/<session-id>/
        index.jsonl                one line appended per closed capture
        <label>/<series>.json      the trace
        <label>/<series>.wav       its mic audio, when the capture had any

Writes happen at capture close, never per batch: append() only accumulates
into the open Trace, so filesystem contact is roughly once per gesture and
never touches the hot path. A crash loses at most the capture in flight.

Boundary rule 2: no method here may raise into the engine tick. Refusals a
Bit should surface to its device raise CaptureError, which the Bit converts
into a refusal string; anything else (a full disk, a read-only mount) is
logged, counted, and swallowed.
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import secrets
import time
import wave
from pathlib import Path

from capture.trace import Trace
from devicelink.protocol import CaptureCommand, TelemetryBatch

logger = logging.getLogger(__name__)


class CaptureError(Exception):
    """A refusal the Bit surfaces to the device as /<dev>/error. Its str()
    is the reason, so keep the message device-facing and actionable."""


def new_session_id(now=None, suffix=None) -> str:
    """A sortable, filesystem-safe session id: no colons, UTC, plus a short
    random suffix so two sessions started in the same second cannot collide."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    suffix = suffix or secrets.token_hex(2)
    return now.strftime("%Y-%m-%dT%H-%M-%SZ") + "-" + suffix


def wav_bytes(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Frame raw int16le PCM as a WAV file. A sidecar rather than base64
    inside the JSON so the audio is openable in Audacity and listenable."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


class CaptureStore:
    def __init__(self, root, session_id: str, bit: dict,
                 clock=time.monotonic):
        self.root = Path(root)
        self.session_id = session_id
        self.bit = dict(bit)
        self._clock = clock
        self._open: dict = {}          # dev -> Trace
        self._last_seen: dict = {}     # dev -> clock reading
        self._counts: dict = {}        # label -> closed captures
        self.failures = 0
        self.bytes_written = 0

    # --- read-out for Bit.status() ---------------------------------------
    def open_ids(self) -> dict:
        return {dev: trace.capture_id for dev, trace in self._open.items()}

    def counts(self) -> dict:
        return dict(self._counts)

    @property
    def session_dir(self) -> Path:
        return self.root / self.session_id

    # --- capture lifecycle -----------------------------------------------
    def open_capture(self, dev: str, cmd: CaptureCommand) -> None:
        if dev in self._open:
            raise CaptureError(
                f"{dev} already open on {self._open[dev].capture_id}")
        meta = cmd.meta
        self._open[dev] = Trace(
            session=self.session_id, capture_id=cmd.capture_id,
            label=meta["label"], series=meta["series"], dev=dev,
            bit=self.bit, source=meta["source"],
            window_ms=meta["window_ms"], t0_device=float(meta["t0"]))
        self._last_seen[dev] = self._clock()

    def append(self, dev: str, batch: TelemetryBatch) -> None:
        trace = self._require_open(dev)
        if batch.capture_id != trace.capture_id:
            raise CaptureError(
                f"batch is for {batch.capture_id}, {dev} has "
                f"{trace.capture_id} open")
        try:
            trace.append(batch)
        except ValueError as exc:
            raise CaptureError(str(exc)) from exc
        self._last_seen[dev] = self._clock()

    def close_capture(self, dev: str, meta: dict) -> None:
        trace = self._require_open(dev)
        trace.outputs = meta.get("outputs") or []
        self._release(dev)
        self._write(trace)

    def abandon(self, dev: str, reason: str) -> None:
        """Drop an in-flight capture without writing it. Used when the client
        cancels, or when the mic was denied partway through."""
        if dev not in self._open:
            return
        logger.info("abandoning %s on %s: %s",
                    self._open[dev].capture_id, dev, reason)
        self._release(dev)

    def expire(self, idle_s: float) -> list:
        """Close any capture whose device has gone quiet, marking it
        truncated. Returns the devices closed. Without this, a phone that
        leaves WiFi mid-window leaves a capture open forever."""
        now = self._clock()
        stale = [dev for dev, seen in self._last_seen.items()
                 if now - seen >= idle_s]
        for dev in stale:
            self._truncate(dev, f"no telemetry for {idle_s:g}s")
        return stale

    def truncate_all(self, reason: str) -> list:
        for dev in list(self._open):
            self._truncate(dev, reason)
        return list(self._counts) and [] or []   # placeholder, replaced below

    # --- internals --------------------------------------------------------
    def _require_open(self, dev: str) -> Trace:
        trace = self._open.get(dev)
        if trace is None:
            raise CaptureError(f"no open capture for {dev}")
        return trace

    def _release(self, dev: str) -> None:
        self._open.pop(dev, None)
        self._last_seen.pop(dev, None)

    def _truncate(self, dev: str, reason: str) -> None:
        trace = self._open[dev]
        trace.truncated = True
        trace.notes = reason
        self._release(dev)
        self._write(trace)

    def _write(self, trace: Trace) -> None:
        """The single write per capture. Everything here is best-effort: a
        write failure is logged and counted, never raised."""
        stem = f"{trace.series:03d}"
        audio_file = f"{stem}.wav" if trace.pcm else None
        try:
            directory = self.session_dir / trace.label
            directory.mkdir(parents=True, exist_ok=True)
            body = json.dumps(trace.to_dict(audio_file), separators=(",", ":"))
            (directory / f"{stem}.json").write_text(body)
            self.bytes_written += len(body)
            if audio_file is not None:
                rate = (trace.source.get("audio") or {}).get("rate", 16000)
                data = wav_bytes(bytes(trace.pcm), rate=rate)
                (directory / audio_file).write_bytes(data)
                self.bytes_written += len(data)
        except Exception:
            self.failures += 1
            logger.exception("writing capture %s failed; continuing",
                             trace.capture_id)
            return
        self._counts[trace.label] = self._counts.get(trace.label, 0) + 1
        self._append_index(trace, stem)

    def _append_index(self, trace: Trace, stem: str) -> None:
        line = json.dumps({"capture_id": trace.capture_id,
                           "label": trace.label, "series": trace.series,
                           "dev": trace.dev, "n": trace.n,
                           "truncated": trace.truncated,
                           "gaps": len(trace.gaps),
                           "path": f"{trace.label}/{stem}.json"},
                          separators=(",", ":"))
        try:
            with (self.session_dir / "index.jsonl").open("a") as fh:
                fh.write(line + "\n")
        except Exception:
            logger.exception("index append for %s failed; continuing",
                             trace.capture_id)
```

Now fix `truncate_all`, which the draft above left with a placeholder return. Replace its body with:

```python
    def truncate_all(self, reason: str) -> list:
        """Close every capture still open, marking each truncated. Called
        from the Bit's on_unload so a session teardown never strands data."""
        devs = list(self._open)
        for dev in devs:
            self._truncate(dev, reason)
        return devs
```

Finally, add `captures/` to `.gitignore` under the `# Local session state` heading:

```
# Local session state
.claude/
captures/
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_capture_store.py -v`
Expected: PASS.

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add capture/store.py tests/test_capture_store.py .gitignore
git commit -m "feat(capture): CaptureStore, the only filesystem contact in the path"
```

---

### Task 5: `CaptureBit`

**Files:**
- Create: `bits/capture_bit.py`
- Test: `tests/test_capture_bit.py`

**Interfaces:**
- Consumes: `capture.store.CaptureStore` and `CaptureError` (Task 4), `devicelink.protocol.decode_capture_command` / `decode_telemetry_batch` (Task 1), the refusal contract from Task 2.
- Produces:
  - `CAPTURE_NODE: str = "CAPTURE_NODE"`
  - `IDLE_TIMEOUT_S: float = 10.0`
  - `class CaptureBit(Bit)` with `__init__(store, idle_timeout_s=IDLE_TIMEOUT_S)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_capture_bit.py`:

```python
"""CaptureBit: role table, verb dispatch, refusals, status, teardown.
Runs against a real CaptureStore over tmp_path. No luxaeterna -- this is the
Bit, not the transport -- so it lives in the core offline suite."""

import json

import pytest

from bits.capture_bit import CAPTURE_NODE, CaptureBit
from capture.store import CaptureStore
from control.engine import GameServer
from control.roles import RoleClass

SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"}}

AXES = {"ax": [1.0, 1.0], "ay": [0.0, 0.0], "az": [9.8, 9.8],
        "gx": [0.0, 0.0], "gy": [0.0, 0.0], "gz": [0.0, 0.0]}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def open_args(dev="ie1", capture_id="shake-021", label="shake", series=3):
    return [dev, "open", {"capture_id": capture_id, "label": label,
                          "series": series, "window_ms": 3000.0,
                          "t0": 12345.678, "source": SOURCE}]


def close_args(dev="ie1", capture_id="shake-021"):
    return [dev, "close", {"capture_id": capture_id, "n": 2, "ok": True,
                           "outputs": []}]


def telemetry_args(dev="ie1", capture_id="shake-021", seq=0):
    return [dev, 1234.5, {"capture_id": capture_id, "seq": seq,
                          "t_ms": [0.0, 10.0], **AXES}]


def make_bit(tmp_path, clock=None):
    store = CaptureStore(root=tmp_path, session_id="SESSION",
                         bit={"name": "capture", "version": "0.1"},
                         clock=clock or FakeClock())
    return CaptureBit(store=store), store


# --- declarations --------------------------------------------------------

def test_declares_one_unscored_recorder_role(tmp_path):
    bit, _ = make_bit(tmp_path)
    table = bit.role_table
    assert set(table.roles) == {"recorder"}
    recorder = table.roles["recorder"]
    assert recorder.role_class is RoleClass.SHARED
    assert recorder.scored is False
    assert recorder.capacity is None
    assert table.node_map == {CAPTURE_NODE: ["recorder"]}


def test_declares_no_light_or_audio(tmp_path):
    """The phone is the whole instrument here; the Bit has no light or audio
    consequence to decide. This also keeps the no-manifest path exercised."""
    bit, _ = make_bit(tmp_path)
    recorder = bit.role_table.roles["recorder"]
    assert recorder.light_manifest == {}
    assert recorder.ugen_manifest == {}
    assert recorder.welcome is None


def test_never_self_completes(tmp_path):
    bit, _ = make_bit(tmp_path)
    assert bit.update(1000.0) is False


def test_loads_cleanly_through_the_engine(tmp_path):
    """Role declarations are validated at load_bit; a typo would be a
    BitLoadError here rather than a device-side parse error later."""
    bit, _ = make_bit(tmp_path)
    gs = GameServer({"capture": lambda: bit})
    gs.load_bit("capture")
    assert gs.join("ie1", CAPTURE_NODE).granted is True


# --- the happy path through verb dispatch --------------------------------

def test_a_full_capture_round_trip_writes_a_trace(tmp_path):
    bit, _ = make_bit(tmp_path)
    gs = GameServer({"capture": lambda: bit})
    gs.load_bit("capture")
    gs.join("ie1", CAPTURE_NODE)

    assert gs.data("ie1", "capture", open_args()) is None
    assert gs.data("ie1", "telemetry", telemetry_args(seq=0)) is None
    assert gs.data("ie1", "telemetry", telemetry_args(seq=1)) is None
    assert gs.data("ie1", "capture", close_args()) is None

    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["label"] == "shake"
    assert body["n"] == 4


def test_abandon_drops_the_capture(tmp_path):
    bit, store = make_bit(tmp_path)
    bit._on_capture("ie1", open_args())
    bit._on_capture("ie1", ["ie1", "abandon",
                            {"capture_id": "shake-021", "reason": "cancelled"}])
    assert store.open_ids() == {}
    assert not (tmp_path / "SESSION" / "shake").exists()


# --- refusals reach the device -------------------------------------------

def test_telemetry_without_an_open_capture_is_refused(tmp_path):
    bit, _ = make_bit(tmp_path)
    reason = bit._on_telemetry("ie1", telemetry_args())
    assert isinstance(reason, str)
    assert "no open capture" in reason


def test_a_malformed_batch_is_refused_with_the_parse_reason(tmp_path):
    bit, _ = make_bit(tmp_path)
    bit._on_capture("ie1", open_args())
    reason = bit._on_telemetry("ie1", ["ie1", 1.0, {"capture_id": "shake-021",
                                                    "seq": 0, "t_ms": []}])
    assert isinstance(reason, str)
    assert "t_ms" in reason


def test_an_incomplete_source_block_is_refused(tmp_path):
    """Spec 7.1: a trace whose source is unknown is not usable."""
    bit, _ = make_bit(tmp_path)
    args = open_args()
    del args[2]["source"]["motion_stream"]
    reason = bit._on_capture("ie1", args)
    assert isinstance(reason, str)
    assert "motion_stream" in reason


def test_a_refusal_reaches_the_device_as_an_error_reason(tmp_path):
    """End to end through Task 2's contract widening."""
    bit, _ = make_bit(tmp_path)
    gs = GameServer({"capture": lambda: bit})
    gs.load_bit("capture")
    gs.join("ie1", CAPTURE_NODE)
    reason = gs.data("ie1", "telemetry", telemetry_args())
    assert "no open capture" in reason


# --- expiry and teardown -------------------------------------------------

def test_update_expires_an_idle_capture(tmp_path):
    clock = FakeClock()
    bit, store = make_bit(tmp_path, clock)
    bit._on_capture("ie1", open_args())
    bit._on_telemetry("ie1", telemetry_args())

    clock.advance(5.0)
    bit.update(5.0)
    assert store.open_ids() == {"ie1": "shake-021"}

    clock.advance(6.0)
    bit.update(6.0)
    assert store.open_ids() == {}
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["truncated"] is True


def test_on_unload_truncates_whatever_is_still_open(tmp_path):
    bit, store = make_bit(tmp_path)
    bit._on_capture("ie1", open_args())
    bit._on_telemetry("ie1", telemetry_args())
    bit.on_unload()
    assert store.open_ids() == {}
    body = json.loads((tmp_path / "SESSION" / "shake" / "003.json").read_text())
    assert body["truncated"] is True


# --- status --------------------------------------------------------------

def test_status_reports_the_session_for_the_console(tmp_path):
    bit, _ = make_bit(tmp_path)
    bit._on_capture("ie1", open_args())
    assert bit.status()["open"] == {"ie1": "shake-021"}

    bit._on_telemetry("ie1", telemetry_args())
    bit._on_capture("ie1", close_args())
    status = bit.status()
    assert status["session"] == "SESSION"
    assert status["captures"] == {"shake": 1}
    assert status["open"] == {}
    assert status["failures"] == 0
    assert status["bytes"] > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_capture_bit.py -v`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'bits.capture_bit'`

- [ ] **Step 3: Implement**

Create `bits/capture_bit.py`:

```python
"""CaptureBit: records labelled sensor telemetry from a real phone so
gesture definitions come from measured data rather than guessed thresholds.
See docs/superpowers/specs/2026-08-07-sensor-telemetry-capture-design.md.

This is a TOOL Bit, not a production game Bit: it declares no light and no
audio, and it does not close the repo's "no production Bit exists" gap.

Deliberately thin. Wire parsing lives in devicelink/protocol.py and every
byte of persistence lives in capture/store.py, so what is left here is the
declaration plus dispatch.
"""

from capture.store import CaptureError, CaptureStore
from control.bit import Bit
from control.roles import Role, RoleClass, RoleTable
from devicelink.protocol import decode_capture_command, decode_telemetry_batch

CAPTURE_NODE = "CAPTURE_NODE"

# How long a device may go silent before its open capture is closed as
# truncated. A phone that walks out of WiFi range mid-window must not leave
# a capture open for the rest of the session.
IDLE_TIMEOUT_S = 10.0


class CaptureBit(Bit):
    version = "0.1"

    def __init__(self, store: CaptureStore,
                 idle_timeout_s: float = IDLE_TIMEOUT_S):
        self._store = store
        self._idle_timeout_s = idle_timeout_s

    @property
    def role_table(self) -> RoleTable:
        recorder = Role(name="recorder", role_class=RoleClass.SHARED,
                        capacity=None, scored=False)
        return RoleTable(roles={"recorder": recorder},
                         node_map={CAPTURE_NODE: ["recorder"]})

    def update(self, dt: float) -> bool:
        """Never self-completes: a capture session ends when the operator
        ends it from the console. The tick is still used, to expire captures
        whose device has gone quiet."""
        self._store.expire(self._idle_timeout_s)
        return False

    def on_unload(self) -> None:
        # This engine has no per-device "leave" event -- GameServer.on_release
        # fires only for every registered device at once, when the whole Bit
        # unloads. So "close never arrives" (idle timeout, via update()) and
        # "device released mid-capture" (spec section 8) are the SAME code
        # path here: on_unload -> truncate_all. There is nothing else to wire.
        self._store.truncate_all("bit unloaded")

    def status(self) -> dict:
        """Rendered by the Terrarium Console with no console changes, which
        is what makes the console a live capture dashboard."""
        return {"session": self._store.session_id,
                "captures": self._store.counts(),
                "open": self._store.open_ids(),
                "failures": self._store.failures,
                "bytes": self._store.bytes_written}

    def verb_handlers(self) -> dict:
        return {"capture": self._on_capture, "telemetry": self._on_telemetry}

    # Both handlers return [] on success (there are no light cues to emit)
    # or a refusal string, which control/engine.py surfaces to the device as
    # /<dev>/error. Neither ever raises: boundary rule 2.
    def _on_capture(self, dev: str, args: list):
        try:
            cmd = decode_capture_command(args)
        except ValueError as exc:
            return f"bad capture command: {exc}"
        try:
            if cmd.action == "open":
                self._store.open_capture(dev, cmd)
            elif cmd.action == "close":
                self._store.close_capture(dev, cmd.meta)
            else:
                self._store.abandon(dev, cmd.meta.get("reason", ""))
        except CaptureError as exc:
            return str(exc)
        return []

    def _on_telemetry(self, dev: str, args: list):
        try:
            batch = decode_telemetry_batch(args)
        except ValueError as exc:
            return f"bad telemetry batch: {exc}"
        try:
            self._store.append(dev, batch)
        except CaptureError as exc:
            return str(exc)
        return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_capture_bit.py -v`
Expected: PASS.

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bits/capture_bit.py tests/test_capture_bit.py
git commit -m "feat(bits): CaptureBit, labelled sensor telemetry recording"
```

---

### Task 6: The driver, and the end-to-end smoke test

**Files:**
- Create: `harness/capture_smoke.py`
- Test: `tests/test_capture_smoke.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 5.
- Produces: `build(host, port, capture_dir, session_id=None, clock=time.monotonic) -> (GameServer, DeviceLinkServer, DeviceLinkAgent, CaptureStore)` and `main()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_capture_smoke.py`:

```python
"""End to end: a synthetic device drives hello -> join -> open -> telemetry
-> close through the real DeviceLinkAgent and lands a real trace file.

This is the regression that makes the whole capture path exercisable with no
phone, no microphone and no hardware."""

import json
import struct

import pytest

# harness.capture_smoke imports devicelink.agent, which imports
# harness.device_bridge and therefore luxaeterna. Guarded exactly as
# tests/test_devicelink_agent.py does, so the core suite still collects
# without the sibling checkout.
pytest.importorskip("luxaeterna")

from bits.capture_bit import CAPTURE_NODE
from harness.capture_smoke import build
from tests.test_devicelink_agent import FakeServer

SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"},
          "audio_stream": "record.startStream",
          "audio": {"rate": 16000, "bits": 16, "channels": 1}}


def _axes(n):
    return {"ax": [1.0] * n, "ay": [0.0] * n, "az": [9.8] * n,
            "gx": [0.0] * n, "gy": [0.0] * n, "gz": [0.0] * n}


def test_a_synthetic_device_produces_a_real_trace_on_disk(tmp_path):
    import base64

    from control.engine import GameServer
    from devicelink.agent import DeviceLinkAgent
    from bits.capture_bit import CaptureBit
    from capture.store import CaptureStore

    store = CaptureStore(root=tmp_path, session_id="SESSION",
                         bit={"name": "capture", "version": "0.1"})
    gs = GameServer({"capture": lambda: CaptureBit(store=store)})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    gs.load_bit("capture")
    gs.run()

    client = object()
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", ["ie1", "capture-client", "1"])
    server.deliver(client, "/game/join", "ss", ["ie1", CAPTURE_NODE])
    agent.poll()
    assert server.addressed("/ie1/deny") == []

    server.deliver(client, "/game/capture", "ssb", [
        "ie1", "open", {"capture_id": "tap-001", "label": "tap", "series": 1,
                        "window_ms": 1500.0, "t0": 100.0, "source": SOURCE}])
    pcm = struct.pack("<4h", 0, 900, -900, 0)
    server.deliver(client, "/game/telemetry", "sfb", [
        "ie1", 100.0, {"capture_id": "tap-001", "seq": 0,
                       "t_ms": [0.0, 10.0, 20.0], **_axes(3),
                       "pcm": base64.b64encode(pcm).decode(),
                       "pcm_t0_ms": 0.5}])
    server.deliver(client, "/game/capture", "ssb", [
        "ie1", "close", {"capture_id": "tap-001", "n": 3, "ok": True,
                         "outputs": [{"t_ms": -1500.0, "event": "countdown",
                                      "level": 0.6}]}])
    agent.poll()

    assert server.addressed("/ie1/error") == []
    body = json.loads((tmp_path / "SESSION" / "tap" / "001.json").read_text())
    assert body["label"] == "tap"
    assert body["capture_id"] == "tap-001"
    assert body["n"] == 3
    assert body["samples"]["az"] == [9.8, 9.8, 9.8]
    assert body["outputs"][0]["event"] == "countdown"
    assert body["audio"]["t0_ms"] == 0.5
    assert (tmp_path / "SESSION" / "tap" / "001.wav").exists()


def test_a_refusal_comes_back_as_an_error_frame(tmp_path):
    from control.engine import GameServer
    from devicelink.agent import DeviceLinkAgent
    from bits.capture_bit import CaptureBit
    from capture.store import CaptureStore

    store = CaptureStore(root=tmp_path, session_id="SESSION",
                         bit={"name": "capture", "version": "0.1"})
    gs = GameServer({"capture": lambda: CaptureBit(store=store)})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    gs.load_bit("capture")
    gs.run()

    client = object()
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", ["ie1", "capture-client", "1"])
    server.deliver(client, "/game/join", "ss", ["ie1", CAPTURE_NODE])
    server.deliver(client, "/game/telemetry", "sfb", [
        "ie1", 100.0, {"capture_id": "tap-001", "seq": 0,
                       "t_ms": [0.0], **_axes(1)}])
    agent.poll()

    errors = server.addressed("/ie1/error")
    assert len(errors) == 1
    assert "no open capture" in errors[0]["args"][1]


def test_build_wires_the_store_to_the_bit(tmp_path):
    gs, server, agent, store = build(host="127.0.0.1", port=0,
                                     capture_dir=tmp_path,
                                     session_id="SESSION")
    try:
        assert store.session_dir == tmp_path / "SESSION"
        gs.load_bit("capture")
        assert gs.bit.status()["session"] == "SESSION"
    finally:
        server.stop()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_capture_smoke.py -v`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'harness.capture_smoke'`. If instead the whole module is SKIPPED, the sibling luxaeterna checkout is missing; that is a valid outcome for the guarded module but you must still verify the file compiles with `python -c "import harness.capture_smoke"` after Step 3.

- [ ] **Step 3: Implement**

Create `harness/capture_smoke.py`:

```python
"""python -m harness.capture_smoke -- run Control with the CaptureBit loaded
and a live DeviceLink, so a phone running the mm-tuneshroom capture client
can join and stream labelled telemetry.

    python -m harness.capture_smoke --hold --host 0.0.0.0
    python -m harness.capture_smoke --hold --capture-dir /data/captures

Traces land under <capture-dir>/<session-id>/. Point the capture client at
ws://<host>:<port>/ws and tap the CAPTURE_NODE registration node.

Trust model: default bind is 127.0.0.1, so a real phone needs --host 0.0.0.0.
That is an explicit opt-in and no auth exists -- unchanged from devicelink/
and console/, but now an actual handheld device is on the network.

Nothing measured here is a hop count or a latency figure: this is a direct
websocket to Control with Arco nowhere in the path.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from bits.capture_bit import CAPTURE_NODE, CaptureBit
from capture.store import CaptureStore, new_session_id
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent
from devicelink.server import DeviceLinkServer

HOST, PORT = "127.0.0.1", 8771
CAPTURE_DIR = "./captures"
TICK = 1.0 / 44.0
BIT_NAME = "capture"


def build(host: str = HOST, port: int = PORT,
          capture_dir=CAPTURE_DIR, session_id: str | None = None,
          clock=time.monotonic):
    """Construct engine + store + server + agent WITHOUT running a tick loop.

    Returns (game_server, server, agent, store). The server is already bound;
    pass port=0 for an ephemeral port in tests. `session_id` and `clock` are
    pure test seams; the defaults keep main()'s production path unchanged.
    """
    store = CaptureStore(root=Path(capture_dir),
                         session_id=session_id or new_session_id(),
                         bit={"name": BIT_NAME, "version": CaptureBit.version},
                         clock=clock)
    gs = GameServer({BIT_NAME: lambda: CaptureBit(store=store)})
    server = DeviceLinkServer(host=host, port=port)
    server.start()
    agent = DeviceLinkAgent(gs, server, clock=clock)
    return gs, server, agent, store


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Serve DeviceLink with the CaptureBit loaded.")
    ap.add_argument("--hold", action="store_true",
                    help="Serve until Ctrl-C. CaptureBit never self-completes, "
                         "so this is the normal mode.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="Abort the Bit after this long instead of serving "
                         "until Ctrl-C.")
    ap.add_argument("--host", default=HOST,
                    help="Bind address. 0.0.0.0 exposes the device port to "
                         "the LAN -- explicit opt-in, no auth exists.")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--capture-dir", default=CAPTURE_DIR,
                    help="Root for trace files. Default ./captures "
                         "(gitignored).")
    args = ap.parse_args()

    gs, server, agent, store = build(args.host, args.port, args.capture_dir)
    print(f"DeviceLink listening on ws://{args.host}:{server.port}/ws")
    print(f"Traces -> {store.session_dir}   (node: {CAPTURE_NODE})")
    gs.load_bit(BIT_NAME)
    gs.run()
    started = time.monotonic()
    try:
        while True:
            agent.poll()
            gs.tick(TICK)
            if args.seconds is not None and \
                    time.monotonic() - started >= args.seconds:
                break
            time.sleep(TICK)
    except KeyboardInterrupt:
        pass
    finally:
        gs.abort()
        server.stop()
        print(f"captures: {store.counts()}  failures: {store.failures}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_capture_smoke.py -v`
Expected: PASS, or SKIPPED if luxaeterna is not installed.

If it skipped, additionally verify the module imports and the CLI parses:
```bash
python -c "import harness.capture_smoke"
python -m harness.capture_smoke --help
```

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/capture_smoke.py tests/test_capture_smoke.py
git commit -m "feat(harness): capture_smoke driver and the end-to-end regression"
```

---

### Task 7: `trace_stats.py`, reading the traces back

Spec section 4.5. Everything here is pure over a loaded trace dict, so it tests against hand-built traces with known analytic answers.

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/trace_stats.py`
- Test: `tests/test_trace_stats.py`

**Interfaces:**
- Consumes: the on-disk trace shape from Task 3 and the layout from Task 4.
- Produces:
  - `GRAVITY: float = 9.80665`
  - `DEFAULT_THRESHOLDS_G: tuple[float, ...] = (1.5, 2.0, 2.5, 3.0)`
  - `motion_features(trace: dict, thresholds_g=DEFAULT_THRESHOLDS_G) -> dict`
  - `audio_features(pcm: bytes, rate: int) -> dict`
  - `read_wav(path) -> tuple[bytes, int]`
  - `rows_for(session_dir) -> list[dict]`
  - `main()`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trace_stats.py`:

```python
"""trace_stats features, checked against hand-built traces whose answers are
known analytically. Pure -- no luxaeterna, core offline suite."""

import math
import struct

import pytest

from tools.trace_stats import (GRAVITY, audio_features, motion_features,
                               rows_for)


def trace(t_ms, ax=None, ay=None, az=None, gx=None, gy=None, gz=None,
          label="shake"):
    n = len(t_ms)
    zeros = [0.0] * n
    return {"label": label, "capture_id": f"{label}-001", "series": 1,
            "n": n, "truncated": False, "gaps": [],
            "samples": {"t_ms": list(t_ms),
                        "ax": ax or list(zeros), "ay": ay or list(zeros),
                        "az": az or list(zeros),
                        "gx": gx or list(zeros), "gy": gy or list(zeros),
                        "gz": gz or list(zeros)}}


# --- motion --------------------------------------------------------------

def test_a_resting_trace_reads_as_one_g_with_no_deviation():
    t = [0.0, 10.0, 20.0, 30.0]
    feats = motion_features(trace(t, az=[GRAVITY] * 4))
    assert feats["peak_a_g"] == pytest.approx(1.0)
    assert feats["peak_dev_g"] == pytest.approx(0.0)
    assert feats["span_ms"] == 0.0
    assert feats["time_above_g"]["2.0"] == 0.0


def test_peak_and_deviation_come_off_the_magnitude():
    """A 3-4-5 triangle: |a| = 5 * GRAVITY exactly."""
    t = [0.0, 10.0]
    feats = motion_features(
        trace(t, ax=[0.0, 3 * GRAVITY], az=[GRAVITY, 4 * GRAVITY]))
    assert feats["peak_a_g"] == pytest.approx(5.0)
    assert feats["peak_dev_g"] == pytest.approx(4.0)


def test_time_above_threshold_counts_sample_intervals():
    """Four samples 10 ms apart, two of them above 2 g -> 20 ms."""
    t = [0.0, 10.0, 20.0, 30.0]
    az = [GRAVITY, 3 * GRAVITY, 3 * GRAVITY, GRAVITY]
    feats = motion_features(trace(t, az=az), thresholds_g=(2.0,))
    assert feats["time_above_g"]["2.0"] == pytest.approx(20.0)
    assert feats["span_ms"] == pytest.approx(10.0)


def test_a_constant_rotation_integrates_to_a_known_swept_angle():
    """1.0 rad/s held for 1.0 s is 1 radian, i.e. 57.29578 degrees."""
    t = [i * 10.0 for i in range(101)]          # 0 .. 1000 ms
    feats = motion_features(trace(t, gz=[1.0] * 101))
    assert feats["peak_omega"] == pytest.approx(1.0)
    assert feats["swept_deg"] == pytest.approx(math.degrees(1.0), rel=1e-6)


def test_inter_spike_intervals_measure_rising_edges():
    """Two separated spikes 40 ms apart -> one interval of 40 ms. This is the
    feature a double-tap window has to be derived from."""
    t = [i * 10.0 for i in range(9)]
    az = [GRAVITY] * 9
    az[2] = 4 * GRAVITY
    az[6] = 4 * GRAVITY
    feats = motion_features(trace(t, az=az), thresholds_g=(2.0,))
    assert feats["isi_ms"] == [pytest.approx(40.0)]
    assert feats["spikes"] == 2


def test_a_single_spike_has_no_interval():
    t = [i * 10.0 for i in range(5)]
    az = [GRAVITY] * 5
    az[2] = 4 * GRAVITY
    feats = motion_features(trace(t, az=az), thresholds_g=(2.0,))
    assert feats["isi_ms"] == []
    assert feats["spikes"] == 1


def test_an_empty_trace_does_not_divide_by_zero():
    feats = motion_features(trace([]))
    assert feats["peak_a_g"] == 0.0
    assert feats["swept_deg"] == 0.0
    assert feats["isi_ms"] == []


# --- audio ---------------------------------------------------------------

def test_full_scale_audio_reads_as_zero_dbfs():
    pcm = struct.pack("<2h", 32767, -32767)
    feats = audio_features(pcm, rate=16000)
    assert feats["peak_dbfs"] == pytest.approx(0.0, abs=0.01)


def test_half_scale_audio_reads_as_minus_six_dbfs():
    pcm = struct.pack("<2h", 16384, 0)
    feats = audio_features(pcm, rate=16000)
    assert feats["peak_dbfs"] == pytest.approx(-6.02, abs=0.05)


def test_attack_time_is_measured_from_ten_percent_of_peak_to_peak():
    """Ramp at 1000 Hz: 10% of peak at sample 1, peak at sample 10, so the
    attack is 9 samples = 9 ms."""
    pcm = struct.pack("<11h", *[i * 1000 for i in range(11)])
    feats = audio_features(pcm, rate=1000)
    assert feats["attack_ms"] == pytest.approx(9.0)


def test_silence_is_reported_rather_than_crashing_on_log_of_zero():
    feats = audio_features(struct.pack("<4h", 0, 0, 0, 0), rate=16000)
    assert feats["peak_dbfs"] == float("-inf")
    assert feats["attack_ms"] == 0.0


def test_no_audio_is_none():
    assert audio_features(b"", rate=16000) is None


# --- directory walk ------------------------------------------------------

def test_rows_for_reads_every_trace_in_a_session(tmp_path):
    import json
    session = tmp_path / "SESSION"
    for label, series in (("tap", 1), ("shake", 2)):
        directory = session / label
        directory.mkdir(parents=True)
        body = trace([0.0, 10.0], az=[GRAVITY, 4 * GRAVITY], label=label)
        body["audio"] = None
        (directory / f"{series:03d}.json").write_text(json.dumps(body))

    rows = rows_for(session)
    assert sorted(r["label"] for r in rows) == ["shake", "tap"]
    assert all(r["peak_a_g"] > 3.0 for r in rows)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_trace_stats.py -v`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'tools'`

- [ ] **Step 3: Implement**

Create `tools/__init__.py` (empty file).

Create `tools/trace_stats.py`:

```python
"""Offline feature summary over a capture directory.

    python -m tools.trace_stats captures/2026-08-07T14-22-03Z-3f9a
    python -m tools.trace_stats captures/<session> --csv > features.csv

This is what turns "we have data" into "we have definitions": it reports the
features a tap-versus-shake discriminator would be built out of, per trace
and per label, so the separation is visible in one command.

Every feature here is pure over a loaded trace dict, so it is tested against
hand-built traces with known analytic answers rather than against captures.

Deriving actual thresholds from real captures is the NEXT spec. Nothing in
this file encodes a threshold as truth: DEFAULT_THRESHOLDS_G is a ladder to
report against, not an answer.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
import sys
import wave
from pathlib import Path

# Matches mm-tuneshroom/lib/sensors/sensor_service.dart.
GRAVITY = 9.80665

DEFAULT_THRESHOLDS_G = (1.5, 2.0, 2.5, 3.0)

_FULL_SCALE = 32768.0


def _magnitudes(samples: dict, axes: tuple) -> list:
    xs, ys, zs = (samples[a] for a in axes)
    return [math.sqrt(x * x + y * y + z * z) for x, y, z in zip(xs, ys, zs)]


def _mean_dt_ms(t_ms: list) -> float:
    if len(t_ms) < 2:
        return 0.0
    return (t_ms[-1] - t_ms[0]) / (len(t_ms) - 1)


def _rising_edges(values: list, threshold: float) -> list:
    """Indices where the signal crosses up through threshold. The count is
    the spike count and the gaps between them are the inter-spike intervals
    a double-tap window has to be derived from."""
    edges, above = [], False
    for i, value in enumerate(values):
        now = value >= threshold
        if now and not above:
            edges.append(i)
        above = now
    return edges


def motion_features(trace: dict, thresholds_g=DEFAULT_THRESHOLDS_G) -> dict:
    samples = trace["samples"]
    t_ms = samples["t_ms"]
    accel = _magnitudes(samples, ("ax", "ay", "az"))
    omega = _magnitudes(samples, ("gx", "gy", "gz"))

    accel_g = [a / GRAVITY for a in accel]
    dt_ms = _mean_dt_ms(t_ms)

    time_above, span_ms, isi_ms, spikes = {}, 0.0, [], 0
    primary = thresholds_g[0] if len(thresholds_g) == 1 else 2.0
    for threshold in thresholds_g:
        hits = [i for i, g in enumerate(accel_g) if g >= threshold]
        time_above[f"{threshold:g}"] = len(hits) * dt_ms
        if threshold == primary and hits:
            span_ms = t_ms[hits[-1]] - t_ms[hits[0]]
    edges = _rising_edges(accel_g, primary)
    spikes = len(edges)
    isi_ms = [t_ms[b] - t_ms[a] for a, b in zip(edges, edges[1:])]

    # Trapezoidal integral of |omega| dt, in degrees. Rotation rate is in
    # rad/s and t_ms in milliseconds, hence the 1000.
    swept = 0.0
    for (t0, w0), (t1, w1) in zip(zip(t_ms, omega), zip(t_ms[1:], omega[1:])):
        swept += (w0 + w1) / 2.0 * (t1 - t0) / 1000.0

    return {
        "n": len(t_ms),
        "duration_ms": (t_ms[-1] - t_ms[0]) if t_ms else 0.0,
        "mean_dt_ms": dt_ms,
        "peak_a_g": max(accel_g) if accel_g else 0.0,
        "peak_dev_g": max((abs(a - GRAVITY) / GRAVITY for a in accel),
                          default=0.0),
        "time_above_g": time_above,
        "span_ms": span_ms,
        "spikes": spikes,
        "isi_ms": isi_ms,
        "peak_omega": max(omega) if omega else 0.0,
        "swept_deg": math.degrees(swept),
    }


def audio_features(pcm: bytes, rate: int) -> dict | None:
    """Peak level and attack time off raw int16le PCM. None when the capture
    was motion-only (mic denied, or no mic in the client)."""
    if not pcm:
        return None
    count = len(pcm) // 2
    values = struct.unpack(f"<{count}h", pcm[:count * 2])
    peak = max(abs(v) for v in values)
    if peak == 0:
        return {"peak_dbfs": float("-inf"), "attack_ms": 0.0,
                "duration_ms": count / rate * 1000.0}

    peak_i = next(i for i, v in enumerate(values) if abs(v) == peak)
    onset_i = next((i for i, v in enumerate(values)
                    if abs(v) >= peak * 0.1), peak_i)
    return {
        "peak_dbfs": 20.0 * math.log10(peak / _FULL_SCALE),
        "attack_ms": (peak_i - onset_i) / rate * 1000.0,
        "duration_ms": count / rate * 1000.0,
    }


def read_wav(path: Path):
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes()), w.getframerate()


def rows_for(session_dir, thresholds_g=DEFAULT_THRESHOLDS_G) -> list:
    """One flat row per trace in a session directory, features included."""
    session_dir = Path(session_dir)
    rows = []
    for path in sorted(session_dir.glob("*/[0-9]*.json")):
        trace = json.loads(path.read_text())
        row = {"label": trace["label"], "capture_id": trace["capture_id"],
               "series": trace["series"], "truncated": trace["truncated"],
               "gaps": len(trace.get("gaps", [])),
               **motion_features(trace, thresholds_g)}
        audio = trace.get("audio")
        if audio:
            pcm, rate = read_wav(path.parent / audio["file"])
            row.update({f"mic_{k}": v
                        for k, v in (audio_features(pcm, rate) or {}).items()})
        rows.append(row)
    return rows


_FLAT = ("label", "capture_id", "series", "n", "duration_ms", "mean_dt_ms",
         "peak_a_g", "peak_dev_g", "span_ms", "spikes", "peak_omega",
         "swept_deg", "mic_peak_dbfs", "mic_attack_ms", "truncated", "gaps")


def _print_table(rows: list) -> None:
    for row in rows:
        print(f"{row['capture_id']:<14} "
              f"peak {row['peak_a_g']:6.2f}g  dev {row['peak_dev_g']:6.2f}g  "
              f"span {row['span_ms']:7.1f}ms  spikes {row['spikes']:2d}  "
              f"omega {row['peak_omega']:6.2f}  swept {row['swept_deg']:7.1f}deg"
              f"  mic {row.get('mic_peak_dbfs', float('nan')):7.1f}dBFS")

    print()
    labels = sorted({row["label"] for row in rows})
    print(f"{'label':<12}{'n':>4}{'peak_a_g':>22}{'peak_omega':>22}"
          f"{'swept_deg':>22}")
    for label in labels:
        group = [r for r in rows if r["label"] == label]
        cells = []
        for key in ("peak_a_g", "peak_omega", "swept_deg"):
            values = sorted(r[key] for r in group)
            cells.append(f"{values[0]:7.2f}..{values[-1]:7.2f}"
                         f" ({sum(values) / len(values):6.2f})")
        print(f"{label:<12}{len(group):>4}" + "".join(f"{c:>22}" for c in cells))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Summarise captured telemetry traces.")
    ap.add_argument("session_dir",
                    help="A capture session directory, e.g. "
                         "captures/2026-08-07T14-22-03Z-3f9a")
    ap.add_argument("--csv", action="store_true",
                    help="Emit CSV on stdout instead of the table.")
    ap.add_argument("--thresholds", default=None,
                    help="Comma-separated g thresholds to report time-above "
                         f"for. Default {','.join(str(t) for t in DEFAULT_THRESHOLDS_G)}.")
    args = ap.parse_args()

    thresholds = DEFAULT_THRESHOLDS_G
    if args.thresholds:
        thresholds = tuple(float(t) for t in args.thresholds.split(","))

    rows = rows_for(args.session_dir, thresholds)
    if not rows:
        print(f"no traces under {args.session_dir}", file=sys.stderr)
        raise SystemExit(1)

    if args.csv:
        writer = csv.DictWriter(sys.stdout, fieldnames=_FLAT,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    else:
        _print_table(rows)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_trace_stats.py -v`
Expected: PASS.

Run: `python -m pytest tests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/__init__.py tools/trace_stats.py tests/test_trace_stats.py
git commit -m "feat(tools): trace_stats, the offline feature summary"
```

---

### Task 8: The cross-repo schema document

This is what mm-tuneshroom's capture client implements against. A reviewer could reasonably approve every prior task and reject this one, which is why it gets its own gate.

**Files:**
- Create: `docs/telemetry-trace-schema.md`
- Modify: `devicelink/protocol.py` (module docstring, lines 1-10)

**Interfaces:**
- Consumes: the shapes fixed by Tasks 1, 3 and 4.
- Produces: no code.

- [ ] **Step 1: Write the document**

Create `docs/telemetry-trace-schema.md`:

````markdown
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
````

- [ ] **Step 2: Point the wire module at it**

In `devicelink/protocol.py`, extend the module docstring. Replace the final paragraph (currently "This module is the single source of truth for the wire shape. Its Dart counterpart is mm-tuneshroom lib/link/envelope.dart -- change both together.") with:

```python
This module is the single source of truth for the wire shape. Its Dart
counterpart is mm-tuneshroom lib/link/envelope.dart -- change both together.

The telemetry-capture verbs at the foot of this file (/game/capture and
/game/telemetry) are specified in docs/telemetry-trace-schema.md, which is
also the contract mm-tuneshroom lib/capture/ implements against.
```

- [ ] **Step 3: Verify the document is internally consistent**

Check each claim against the code, and fix the document if any disagree:

```bash
grep -n 'TELEMETRY_BATCH_SCHEMA\|REQUIRED_SOURCE_KEYS\|MOTION_AXES' devicelink/protocol.py
grep -n 'TRACE_SCHEMA\|_AUDIO_CLOCK_NOTE' capture/trace.py
grep -n 'strftime\|:03d' capture/store.py
python -m pytest tests -v
```

Expected: the required source keys in the document match `REQUIRED_SOURCE_KEYS` exactly; the session-id format string matches; the `:03d` stem padding matches; the suite passes.

- [ ] **Step 4: Commit**

```bash
git add docs/telemetry-trace-schema.md devicelink/protocol.py
git commit -m "docs: the telemetry trace schema, mm-tuneshroom's contract"
```

---

## Done criteria

- [ ] `python -m pytest tests -v` passes with no luxaeterna installed (capture tests run, smoke test skips).
- [ ] `python -m pytest tests -v` passes with luxaeterna installed (smoke test runs).
- [ ] `python -m harness.capture_smoke --help` prints, and `--host 0.0.0.0` is documented as an explicit opt-in.
- [ ] A synthetic-device run produces a real `.json` and `.wav` under `captures/<session>/`.
- [ ] `python -m tools.trace_stats captures/<session>` prints a table over that output.

## Follow-on work, deliberately out of this plan

- **mm-tuneshroom capture client** (spec slice 2). Its own plan and PR in that repo. It implements `docs/telemetry-trace-schema.md`.
- **Derived tap and shake definitions** (spec slice 3). Cannot be planned before real captures exist. `tools/trace_stats.py` lands here; the thresholds it informs do not.
- **Preset assets and control-surface buttons** (spec slice 4). Blocked on slice 2 producing data.
- **Deep-dive sync.** `docs/MM_TERRARIUM.md` gains a `capture/` subsystem entry at closeout, via the `mm-deepdive-sync` skill.
