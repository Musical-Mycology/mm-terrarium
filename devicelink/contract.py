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

Down-row transport is per row (spec 2026-10-01-instrument-handshake-protocol
section 3.3): control verbs go tcp through o2litepy's send_cmd, leds and
play stay udp-ok.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# How often a device should repeat /game/hello while connected (spec
# section 4.3, rule 1: "Hello goes out only once the link is up, then
# repeats every 5 s over TCP"). The contract owns this number;
# harness/o2_shroom.py's --heartbeat-interval default and
# tools/export_contract.py's exported lifecycle.hello_interval_s both
# read it from here rather than restating it.
HELLO_INTERVAL_S = 5.0


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
    VerbRow("start", "up", ("ss",), ("dev", "key"), "tcp", True,
            "key is '' for an unkeyed (Console/uplink) start."),
    VerbRow("handshake", "up", ("sss",), ("dev", "round_id", "node"),
            "tcp", True,
            "Received Handshake: sent when the user accepts. round_id "
            "echoes the latest /<dev>/handshake; node '' asks for the "
            "Bit's default scored role, else a Registration Node id. A "
            "Room node binds an armed fixture and ignores round_id."),
    VerbRow("tap", "up", ("sffi",),
            ("dev", "peak_g", "duration_ms", "count"), "udp-ok", False,
            "peak_g is 0 for a touch tap (Rev 1); count is the device's "
            "own pairing (Rev 1 sends 1). Stamped at onset. Gameplay "
            "only: Control no longer reads taps as a lobby handshake."),
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
    VerbRow("role", "down", ("b",), ("config",), "tcp", False,
            "Sent once per round at RUNNING (or at a RUNNING walk-up's "
            "first hello)."),
    VerbRow("deny", "down", ("ss",), ("reason", "hint"), "tcp", True,
            "Reply to a refused /game/handshake; reason and hint are both "
            "set."),
    VerbRow("handshake", "down", ("s",), ("round_id",), "tcp", True,
            "Invite for this round: sent on first hello in SETUP and every "
            "invite cycle until validated, FULL, or SETUP ends."),
    VerbRow("validated", "down", ("ss",), ("round_id", "role"), "tcp", True,
            "The handshake was accepted and a scored slot is reserved; the "
            "role itself arrives at RUNNING."),
    VerbRow("leds", "down", ("b",), ("frame",), "udp-ok", True,
            "Rule 3: a frame shows at its presentation time; when several "
            "are due, only the newest shows; the last frame holds. Also "
            "sent to a hello'd-but-unjoined device (lobby invite/"
            "handshake flashes)."),
    VerbRow("play", "down", ("ss",), ("name", "params"), "udp-ok", False,
            "Fires a device-local sample by name; an unknown name is the "
            "device's own business."),
    VerbRow("release", "down", ("",), (), "tcp", False,
            "Ends the role but does not clear the display."),
    VerbRow("error", "down", ("ss",), ("context", "message"), "tcp", True,
            "A handler-declared or engine-level refusal; changes no "
            "state."),
    VerbRow("room", "down", ("b",), ("blob",), "tcp", True,
            "Sent on first contact and on every state or registration "
            "change; hardware may ignore it."),
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


def down_transport(verb: str) -> str:
    return row_for("down", verb).transport
