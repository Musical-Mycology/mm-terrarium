"""DeviceLink wire protocol: a JSON envelope mirroring o2ws field-for-field.

    {"timestamp": float, "address": str, "typespec": str, "args": list}

Typespec chars: 's' string, 'i' int32, 'f' float, 'b' blob (any JSON value;
over real o2lite this becomes a serialized blob, per Design Rule 5).

This module is the single source of truth for the wire shape. Its Dart
counterpart is mm-tuneshroom lib/link/envelope.dart -- change both together.
"""

from __future__ import annotations

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
