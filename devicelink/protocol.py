"""DeviceLink wire protocol: a JSON envelope mirroring o2ws field-for-field.

    {"timestamp": float, "address": str, "typespec": str, "args": list}

Typespec chars: 's' string, 'i' int32, 'f' float, 'b' blob (any JSON value;
over real o2lite this becomes a serialized blob, per Design Rule 5).

This module is the single source of truth for the wire shape. Its Dart
counterpart is mm-tuneshroom lib/link/envelope.dart -- change both together.

The telemetry-capture verbs at the foot of this file (/game/capture and
/game/telemetry) are specified in docs/telemetry-trace-schema.md, which is
also the contract mm-tuneshroom lib/capture/ implements against.
"""

from __future__ import annotations

import base64
import binascii
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
    return Envelope(timestamp=float(timestamp), address=address,
                    typespec=typespec, args=args)


def parse_game_address(address: str) -> str | None:
    """'/game/join' -> 'join'. Anything not a non-empty /game/<verb>: None."""
    if not address.startswith(_GAME_PREFIX):
        return None
    verb = address[len(_GAME_PREFIX):]
    return verb or None


def _event(address: str, typespec: str, args: list) -> dict:
    return encode(Envelope(timestamp=0.0, address=address,
                           typespec=typespec, args=args))


def role_event(dev: str, config: dict) -> dict:
    """The granted /<dev>/role blob, passed through verbatim -- it must stay
    byte-identical to JoinResult.config."""
    return _event(f"/{dev}/role", "b", [config])


def deny_event(dev: str, reason: str | None, hint: str | None) -> dict:
    return _event(f"/{dev}/deny", "ss", [reason or "", hint or ""])


def leds_event(dev: str, channels) -> dict:
    """channels: a flat sequence of 36 ints (12 pixels x GRB)."""
    return _event(f"/{dev}/leds", "b", [list(channels)])


def release_event(dev: str) -> dict:
    return _event(f"/{dev}/release", "", [])


def error_event(dev: str, context: str, message: str) -> dict:
    return _event(f"/{dev}/error", "ss", [context, message])


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
