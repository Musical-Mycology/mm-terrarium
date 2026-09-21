"""devicelink/contract.py: the verb table, and consistency checks against
devicelink/protocol.py's builders and devicelink/o2_transport.py's
GAME_VERBS. See docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md section 5.1."""
from __future__ import annotations

import inspect

import pytest

from devicelink import protocol
from devicelink.contract import GAME_VERBS, VERB_TABLE, row_for, typespec_allowed
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport
from devicelink.o2_transport import GAME_VERBS as TRANSPORT_GAME_VERBS


def test_game_verbs_is_derived_from_the_up_rows():
    assert GAME_VERBS == tuple(r.verb for r in VERB_TABLE if r.direction == "up")


# The verbs devicelink/o2_transport.py's GAME_VERBS carried before this task
# (recorded from the pre-change source), plus exactly the two Rev 1
# additions. A set, not a tuple: o2_transport.py only ever loops GAME_VERBS
# to register a handler per verb, so no caller depends on its order, and
# deriving it from VERB_TABLE is free to produce a different one.
EXPECTED_GAME_VERBS = frozenset({
    "hello", "join", "tilt", "tap", "shake", "capture", "telemetry",
    "canvas", "start", "hold", "swing",
})


def test_o2_transport_game_verbs_matches_the_table():
    # Identity, not just equality: proves o2_transport.py really imports
    # the table's own tuple instead of keeping a second copy that merely
    # happens to compare equal today.
    assert TRANSPORT_GAME_VERBS is GAME_VERBS
    assert set(GAME_VERBS) == EXPECTED_GAME_VERBS


def test_hold_and_swing_are_new_up_rows_with_the_designed_shapes():
    hold = row_for("up", "hold")
    assert hold.typespecs == ("sfi",)
    assert hold.args == ("dev", "held_seconds", "count")
    assert hold.transport == "udp-ok"
    swing = row_for("up", "swing")
    assert swing.typespecs == ("sfi",)
    assert swing.args == ("dev", "signed_peak_g", "count")


def test_hello_join_start_are_tcp_and_gestures_are_udp_ok():
    for verb in ("hello", "join", "start"):
        assert row_for("up", verb).transport == "tcp"
    for verb in ("tap", "tilt", "shake", "hold", "swing"):
        assert row_for("up", verb).transport == "udp-ok"


# --- every protocol.py builder against the table --------------------------
#
# devicelink/protocol.py's convention for a device-wire message builder is
# a public function whose name ends in "_event"; each one builds exactly
# one down verb's envelope (role_event -> /<dev>/role, and so on -- see
# that module's `_event` helper). The list below is DERIVED from the
# module by that convention rather than hand-listed, so a future builder
# is either covered by this test or fails it; it cannot ship unchecked.

def _protocol_event_builders() -> tuple[str, ...]:
    return tuple(sorted(
        name for name, fn in inspect.getmembers(protocol, inspect.isfunction)
        if fn.__module__ == protocol.__name__
        and not name.startswith("_")
        and name.endswith("_event")))


def _dummy_value(param: inspect.Parameter):
    """A value good enough to make the builder run: its own default when
    it has one, else a plausible stand-in for its annotation. This only
    needs to be good enough to exercise the SHAPE (typespec) of what comes
    back, never the content of a field."""
    if param.default is not inspect.Parameter.empty:
        return param.default
    if param.annotation is inspect.Parameter.empty:
        return []
    if "dict" in str(param.annotation):
        return {}
    if "float" in str(param.annotation):
        return 0.0
    return "x"


@pytest.mark.parametrize("builder_name", _protocol_event_builders())
def test_every_protocol_event_builder_typespec_is_allowed(builder_name):
    builder = getattr(protocol, builder_name)
    args = [_dummy_value(p) for p in inspect.signature(builder).parameters.values()]
    msg = builder(*args)
    verb = builder_name[: -len("_event")]
    assert typespec_allowed("down", verb, msg["typespec"])


def test_protocol_event_builders_cover_every_down_row_in_the_table():
    """The reverse direction: every down row in the table has a builder,
    so the table cannot grow a down verb protocol.py never actually
    builds."""
    builder_verbs = {name[: -len("_event")] for name in _protocol_event_builders()}
    table_down_verbs = {row.verb for row in VERB_TABLE if row.direction == "down"}
    assert builder_verbs == table_down_verbs


def test_hold_and_swing_delivered_through_fake_o2lite_reach_drain_inbound():
    fake = FakeO2Lite(now=100.0)
    fake.set_services("actl")
    transport = O2LiteTransport()
    transport.start(fake)
    fake.deliver("/game/hold", "sfi", ("ie1", 0.65, 1), timestamp=100.5)
    fake.deliver("/game/swing", "sfi", ("ie1", -2.1, 1), timestamp=100.6)
    drained = [env for (_client, env) in transport.drain_inbound()]
    addresses = [(e["address"], e["typespec"], e["args"]) for e in drained]
    assert ("/game/hold", "sfi", ["ie1", 0.65, 1]) in addresses
    assert ("/game/swing", "sfi", ["ie1", -2.1, 1]) in addresses
