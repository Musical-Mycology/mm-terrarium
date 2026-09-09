"""DeviceLink wire protocol: a JSON envelope mirroring o2ws field-for-field.

    {"timestamp": float, "address": str, "typespec": str, "args": list}

Typespec chars: 's' string, 'i' int32, 'f' float, 'b' blob (any JSON value;
over real o2lite this becomes a serialized blob, per Design Rule 5).

This module is the single source of truth for the wire shape. Its Dart
counterpart is mm-tuneshroom lib/link/envelope.dart -- change both together.

The telemetry-capture verbs at the foot of this file (/game/capture and
/game/telemetry) are specified in docs/telemetry-trace-schema.md, which is
also the contract mm-tuneshroom lib/capture/ implements against.

Wire flavor (2026-09-08, spec 2026-09-08-o2ws-browser-link-design.md):
a device whose /game/hello protoversion starts with "o2ws/" receives
/<dev>/role and /<dev>/room as `s` (the identical JSON text) and
/<dev>/leds as `s` (base64 of the identical bytes, same timestamp),
because o2ws carries no blob type. Every other device and every other
message is unchanged. The rewrite lives in devicelink/o2_transport.py's
O2LiteTransport.send; this module's builders still produce `b`.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from dataclasses import dataclass

_GAME_PREFIX = "/game/"


@dataclass(frozen=True)
class Envelope:
    timestamp: float
    address: str
    typespec: str
    args: list


def encode(env: Envelope) -> dict:
    return {"timestamp": env.timestamp, "address": env.address,
            "typespec": env.typespec, "args": list(env.args)}


def decode(msg: dict) -> Envelope:
    """Parse an inbound message. Raises ValueError on anything malformed --
    callers treat that as 'drop this frame', never as an engine error."""
    if not isinstance(msg, dict):
        raise ValueError("envelope must be an object")
    address = msg.get("address")
    if not isinstance(address, str) or not address:
        raise ValueError("envelope needs a non-empty string address")
    typespec = msg.get("typespec", "")
    if not isinstance(typespec, str):
        raise ValueError("typespec must be a string")
    args = msg.get("args", [])
    if not isinstance(args, list):
        raise ValueError("args must be a list")
    if len(typespec) != len(args):
        raise ValueError(
            f"typespec {typespec!r} does not match {len(args)} args")
    timestamp = msg.get("timestamp", 0.0)
    if not isinstance(timestamp, (int, float)):
        raise ValueError("timestamp must be a number")
    if not math.isfinite(timestamp):
        raise ValueError("timestamp must be finite")
    return Envelope(timestamp=float(timestamp), address=address,
                    typespec=typespec, args=args)


def parse_game_address(address: str) -> str | None:
    """'/game/join' -> 'join'. Anything not a non-empty /game/<verb>: None."""
    if not address.startswith(_GAME_PREFIX):
        return None
    verb = address[len(_GAME_PREFIX):]
    return verb or None


# Schemes a device-reported canvas URL may carry. The URL becomes a link
# in the operator's admin panel, so this is enforced at the decode
# boundary (same reasoning as the capture-label restriction below): a
# hostile device must not be able to plant a javascript: link.
CANVAS_SCHEMES = ("http://", "https://")


def parse_canvas_url(args: list) -> str:
    """Validate a /game/canvas message's args ([dev, url]) and return the
    URL. Raises ValueError on anything malformed; callers treat that as
    'refuse and log', never as an engine error."""
    if len(args) < 2 or not isinstance(args[1], str):
        raise ValueError("canvas needs a string url argument")
    url = args[1]
    if not url.startswith(CANVAS_SCHEMES):
        raise ValueError(f"canvas url must start with one of "
                         f"{CANVAS_SCHEMES}, got {url!r}")
    return url


def _event(address: str, typespec: str, args: list,
           timestamp: float = 0.0) -> dict:
    return encode(Envelope(timestamp=timestamp, address=address,
                           typespec=typespec, args=args))


def role_event(dev: str, config: dict) -> dict:
    """The granted /<dev>/role blob, passed through verbatim -- it must stay
    byte-identical to JoinResult.config (control/role_config.py's
    compose_role_config). Optional keys, present only when the underlying
    value is non-null/non-empty (never shipped as null):

        room_name, terrarium_config_version -- Room provenance stamps.
        slot -- the requirement slot a granted join filled.
        instrument -- the carried instrument that filled it, as a dict
            (2026-08-31 carried-instrument-wire): {name, capabilities
            (sorted), pixels, ambient (light/ugen manifests), functions
            (function_view's wire shape)}. See
            docs/carried-instrument-schema.md. Omitted for a ROOM join,
            same never-null discipline as every other stamp here.
        triggers -- {event_trigger_name: {threshold_key: number}} for every
            Task 8 EventTrigger the carried instrument declares (e.g.
            Tuneshroom's "tap"/"shake"): the DEVICE runs the gesture
            detector, the SERVER owns the numeric thresholds, so this key
            ships them at adoption time instead of each client guessing its
            own. Consuming this key on the mm-tuneshroom client is recorded
            cross-repo follow-up, not yet implemented there.
    """
    return _event(f"/{dev}/role", "b", [config])


def deny_event(dev: str, reason: str | None, hint: str | None) -> dict:
    return _event(f"/{dev}/deny", "ss", [reason or "", hint or ""])


def leds_event(dev: str, channels, when: float = 0.0) -> dict:
    """channels: a flat sequence of ints, width-agnostic. This function does
    `list(channels)` with no length assertion, so any frame width rides the
    same wire shape. Two real callers, two widths: a Tuneshroom sends 36
    (12 pixels x GRB, harness/shroom_client.py's LED_CHANNELS), and a Room
    sends its RoomProfile.channel_count, currently 180 (60 pixels x GRB,
    control/room_profile.py).

    `when` is an absolute O2 time at which the device should display this
    frame. 0.0 means no declared time: display on arrival, the pre-timing
    behavior. Control renders every joined device's light and ships finished
    frames, so the device schedules a FRAME, not MIDI -- see the design
    spec section 5.3.
    """
    return _event(f"/{dev}/leds", "b", [list(channels)], timestamp=when)


def release_event(dev: str) -> dict:
    return _event(f"/{dev}/release", "", [])


def error_event(dev: str, context: str, message: str) -> dict:
    return _event(f"/{dev}/error", "ss", [context, message])


def play_event(dev: str, name: str, params: str = "") -> dict:
    """Fire a device-local sample by name.

    The canonical design (docs/control-gameserver-design.md, player flow
    step 4) writes this `/ie<N>/play "tis" time id params`. Two deviations,
    both forced. There is no 't' in this transport's typespec vocabulary and
    every envelope already carries `timestamp`, so the time argument is
    dropped. And `id` becomes a name: harness/local_sample.py's SamplePlayer
    keys samples by name, so an int index would oblige every client to keep
    an ordered list in sync with Control -- an off-by-one there plays the
    wrong sound instead of failing.

    Nothing schedules this yet: DeviceLink has no shared clock, so the
    device plays on arrival. `timestamp` is carried anyway so that adding
    scheduling later is a device-side change, not a wire change.
    """
    return _event(f"/{dev}/play", "ss", [name, params])


def room_event(dev: str, blob: dict) -> dict:
    """Informational room snapshot pushed to hello'd devices: engine state,
    loaded Bit, and the Registration Nodes a player could tap with their
    fill counts. Hardware ignores it (NFC tells it the node); the Flutter
    simulator draws its node tiles from it. Downstream only -- no device
    ever asks for it. See mm-tuneshroom's
    docs/superpowers/specs/2026-08-21-simulated-room-flow-design.md."""
    return _event(f"/{dev}/room", "b", [blob])


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

# o2lite's C library caps a message at 4096 bytes (o2/src/o2lite.h:135).
# o2litepy allows 8192, but hardware and the FFI link use the C library, so
# 4096 is the contract. The blob budget leaves headroom for everything else
# in a /game/telemetry message: the O2 header, the padded address and
# typespec, dev (up to 31 chars) and t0, and the blob's own length word and
# padding -- measured well under 128 bytes.
O2_MAX_MSG_LEN = 4096
TELEMETRY_BLOB_BUDGET = O2_MAX_MSG_LEN - 128


def encode_telemetry_body(body: dict) -> bytes:
    """Exactly the bytes devicelink/o2_transport.py's to_o2_arg puts in the
    blob for a batch dict: wire_json.dumps, UTF-8. The size test and the
    chunker both measure through this so they cannot drift from the wire."""
    from control.wire_json import dumps   # noqa: PLC0415 (protocol stays light)

    return dumps(body).encode("utf-8")


def chunk_telemetry_batch(body: dict, *, first_seq: int, rate: int = 16000,
                          budget: int = TELEMETRY_BLOB_BUDGET) -> list[dict]:
    """Split one producer-side batch into wire batches that each encode
    under `budget` bytes.

    `body` is the batch before framing: capture_id, t_ms, the six axes,
    optional raw int16le `pcm` bytes with `pcm_t0_ms`, and no seq. Motion
    samples and PCM frames are split into the same number of equal runs, so
    chunk i carries the i-th run of each; seq counts up from first_seq and
    each chunk's pcm_t0_ms advances by the frames already sent, on the
    audio clock. Re-batching is free by the trace schema (batch boundaries
    are not semantic), so the decoder needs no change and Trace.append sees
    consecutive seqs. Raises ValueError when even one-sample chunks do not
    fit: the producer must send more often.
    """
    t_ms = list(body["t_ms"])
    n = len(t_ms)
    if n == 0:
        raise ValueError("a batch needs at least one motion sample")
    pcm = bytes(body.get("pcm") or b"")
    frames = len(pcm) // 2
    for parts in range(1, n + 1):
        chunks = []
        for i in range(parts):
            lo, hi = n * i // parts, n * (i + 1) // parts
            flo, fhi = frames * i // parts, frames * (i + 1) // parts
            chunk = {"capture_id": body["capture_id"],
                     "seq": first_seq + i, "t_ms": t_ms[lo:hi]}
            for axis in MOTION_AXES:
                chunk[axis] = list(body[axis][lo:hi])
            if fhi > flo:
                chunk["pcm"] = base64.b64encode(pcm[flo * 2:fhi * 2]).decode("ascii")
                chunk["pcm_t0_ms"] = body["pcm_t0_ms"] + flo * 1000.0 / rate
            chunks.append(chunk)
        if all(len(encode_telemetry_body(c)) <= budget for c in chunks):
            return chunks
    raise ValueError(
        f"a single-sample chunk still exceeds {budget} bytes; the producer "
        f"must send batches more often")

# Everything needed to say WHICH stream a trace came from. Enforced at
# `open` so a partial block can never reach disk: a threshold derived from a
# trace whose source is unknown is exactly the mistake www/sensors.js made.
# audio_stream/audio are deliberately NOT required -- mic permission denied
# is a motion-only capture, not a failed one.
REQUIRED_SOURCE_KEYS = frozenset({
    "client", "app_version", "platform", "device_model",
    "motion_stream", "gravity_included", "requested_hz", "units",
})

# label becomes a filesystem directory component in capture/store.py -- must
# never contain a path separator or traversal sequence.
_LABEL_RE = re.compile(r"[A-Za-z0-9_-]+")


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
    if not _LABEL_RE.fullmatch(label):
        raise ValueError(
            "label must contain only letters, digits, '_' or '-'")
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
