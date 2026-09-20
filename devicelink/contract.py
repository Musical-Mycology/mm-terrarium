"""The device contract kit's verb table: one row per verb Control and a
device exchange (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md section 5.1).

GAME_VERBS -- the /game/<verb> method names devicelink/o2_transport.py's
O2LiteTransport registers on the o2lite connection -- is DERIVED from this
table's up rows, so a new up verb is added in exactly one place.

This table is read by:
- tests/test_devicelink_contract.py (every devicelink/protocol.py builder's
  typespec must be one this table allows for its address)
- tools/export_contract.py (the exported contract.json "verbs" list)

Down-row transport is "udp-ok" for every row: O2LiteTransport.send calls
o2lite's send() with no tcp= keyword, and o2litepy's send() defaults to
UDP (send_cmd is the one that passes tcp=True) -- verified against
o2litepy/src/o2litepy/o2lite.py, not assumed. Nothing here changes that.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VerbRow:
    verb: str                     # "hello", "tap", "role", ...
    direction: str                # "up" (/game/<verb>) or "down" (/<dev>/<verb>)
    typespecs: tuple[str, ...]    # every typespec this verb may be sent with
    args: tuple[str, ...] = field(default_factory=tuple)  # arg names of the LONGEST typespec
    transport: str = "udp-ok"     # "tcp" or "udp-ok"
    pre_role: bool = False        # may be sent/received before a role exists
    notes: str = ""

    @property
    def address(self) -> str:
        prefix = "/game/" if self.direction == "up" else "/<dev>/"
        return f"{prefix}{self.verb}"


VERB_TABLE: tuple[VerbRow, ...] = (
    # --- up: device -> Control (/game/<verb>) ---
    VerbRow("hello", "up", ("s", "ssss"),
            ("dev", "name", "protoversion", "instrument"), "tcp", True,
            "The 'ssss' form declares the carried instrument (2026-08-31 "
            "carried-instrument-wire); the bare 's' form declares nothing "
            "and resolves to defaultshroom."),
    VerbRow("join", "up", ("ss",), ("dev", "node"), "tcp", True, ""),
    VerbRow("start", "up", ("ss",), ("dev", "key"), "tcp", True,
            "key is '' for an unkeyed (Console/uplink) start."),
    VerbRow("tap", "up", ("sffi",),
            ("dev", "peak_g", "duration_ms", "count"), "udp-ok", True,
            "peak_g is 0 for a touch tap (Rev 1); count is always 1 on "
            "Rev 1 -- Control pairs double taps itself. Stamped at onset. "
            "Allowed before a role for the lobby's tap-to-join handshake."),
    VerbRow("tilt", "up", ("sf",), ("dev", "gamma"), "udp-ok", False, ""),
    VerbRow("shake", "up", ("sfff",),
            ("dev", "peak_g", "duration_ms", "sweep_deg"), "udp-ok", False, ""),
    VerbRow("hold", "up", ("sfi",), ("dev", "held_seconds", "count"),
            "udp-ok", False,
            "New for Rev 1. Sent on release of a touch held past the hold "
            "window; stamped at touch-down. count is always 1. Waits for "
            "a role, unlike tap."),
    VerbRow("swing", "up", ("sfi",), ("dev", "signed_peak_g", "count"),
            "udp-ok", False,
            "New for Rev 1. Negative means left; stamped at onset. count "
            "is always 1. Waits for a role, unlike tap."),
    VerbRow("canvas", "up", ("ss",), ("dev", "url"), "tcp", True,
            "Simulators only (harness/o2_shroom.py, run_stack's browser "
            "canvases); hardware and phones never send it."),
    VerbRow("capture", "up", ("ssb",), ("dev", "action", "meta"), "tcp", False,
            "(recommended) tcp: a research telemetry-capture lifecycle "
            "verb (docs/telemetry-trace-schema.md) whose loss would leave "
            "a capture session stuck open. No code currently pins a "
            "reliability choice for this verb; this is the contract "
            "kit's own choice, not a measured fact."),
    VerbRow("telemetry", "up", ("sfb",), ("dev", "t0", "batch"), "tcp", False,
            "(recommended) tcp, for the same reason as capture: a dropped "
            "chunk shows up as a gap in the trace (capture/store.py)."),
    # --- down: Control -> device (/<dev>/<verb>) ---
    VerbRow("role", "down", ("b",), ("config",), "udp-ok", False,
            "Sent once a join is granted."),
    VerbRow("deny", "down", ("ss",), ("reason", "hint"), "udp-ok", True,
            "Sent in reply to a join Control refuses; the device holds no "
            "role before or after."),
    VerbRow("leds", "down", ("b",), ("frame",), "udp-ok", True,
            "Rule 3: a frame shows at its presentation time; when several "
            "are due, only the newest shows; the last frame holds. Also "
            "sent to a hello'd-but-unjoined device (lobby invite/"
            "handshake flashes)."),
    VerbRow("play", "down", ("ss",), ("name", "params"), "udp-ok", False,
            "Fires a device-local sample by name; an unknown name is the "
            "device's own business."),
    VerbRow("release", "down", ("",), (), "udp-ok", False,
            "Ends the role but does not clear the display."),
    VerbRow("error", "down", ("ss",), ("context", "message"), "udp-ok", True,
            "A handler-declared or engine-level refusal; changes no "
            "state."),
    VerbRow("room", "down", ("b",), ("blob",), "udp-ok", True,
            "Informational room snapshot, sent after every hello whether "
            "or not the device has joined; hardware ignores it."),
)

GAME_VERBS: tuple[str, ...] = tuple(
    row.verb for row in VERB_TABLE if row.direction == "up")


def row_for(direction: str, verb: str) -> VerbRow:
    for row in VERB_TABLE:
        if row.direction == direction and row.verb == verb:
            return row
    raise KeyError(f"no {direction} row for verb {verb!r}")


def typespec_allowed(direction: str, verb: str, typespec: str) -> bool:
    return typespec in row_for(direction, verb).typespecs
