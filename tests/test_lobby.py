"""control/lobby.py: the pure lobby core (spec section 2, 5)."""
from control.lobby import (
    CeremonySlots, DEFAULT_LOBBY, DoubleTapDetector, FEEDBACK_ACCEPT,
    FEEDBACK_MINIMUM, FEEDBACK_NONE, FEEDBACK_REFUSED, InviteSchedule,
    LobbyConfig, LobbyState, NOTE_SCALE, TERRARIUM_ADMIN, decide_start,
    hue_drift_cc, lobby_light_manifest, lobby_state, scale_note)
from control.roles import Role, RoleClass, RoleTable


def _table(**roles):
    return RoleTable(roles={n: r for n, r in roles.items()}, node_map={})


def _role(name, *, scored, capacity):
    return Role(name=name, role_class=RoleClass.UNIQUE, capacity=capacity,
                scored=scored)


def test_reserved_admin_id_and_defaults():
    assert TERRARIUM_ADMIN == "terrarium"
    assert DEFAULT_LOBBY == LobbyConfig(enabled=True, invite_interval_s=5.0,
                                        ceremony_gap_s=1.0,
                                        double_tap_window_s=1.5)


def test_scale_climbs_a_major_and_wraps_every_seven():
    assert NOTE_SCALE == (69, 71, 73, 74, 76, 78, 80)
    assert [scale_note(i) for i in range(9)] == [69, 71, 73, 74, 76, 78, 80, 69, 71]


def test_full_when_every_capped_scored_role_is_at_capacity():
    table = _table(player=_role("player", scored=True, capacity=2),
                   jammer=_role("jammer", scored=False, capacity=None))
    assert lobby_state([("player", 1, 2), ("jammer", 5, None)], table) is LobbyState.WAITING
    assert lobby_state([("player", 2, 2), ("jammer", 0, None)], table) is LobbyState.FULL


def test_uncapped_scored_role_never_fills():
    table = _table(player=_role("player", scored=True, capacity=None))
    assert lobby_state([("player", 40, None)], table) is LobbyState.WAITING


def test_no_scored_roles_is_waiting_not_full():
    table = _table(jammer=_role("jammer", scored=False, capacity=None))
    assert lobby_state([("jammer", 0, None)], table) is LobbyState.WAITING


def test_count_for_a_role_missing_from_the_table_is_ignored():
    table = _table(player=_role("player", scored=True, capacity=1))
    assert lobby_state([("player", 1, 1), ("room_test", 1, 1)], table) is LobbyState.FULL


def test_hue_drift_is_a_triangle_over_the_period():
    assert hue_drift_cc(0.0) == 0
    assert hue_drift_cc(10.0) == 127
    assert hue_drift_cc(20.0) == 0
    assert hue_drift_cc(5.0) == 64


def test_lobby_light_manifest_drives_hue_and_level_lanes():
    m = lobby_light_manifest()
    decl = m["instruments"][0]
    assert decl["instrument"] == "aurora" and decl["target"] == "primary"
    assert "level" in decl["params"]
    assert {(l["source"], l["dest"]) for l in decl["lanes"]} == {("cc:74", "hue"), ("cc:11", "level")}


def _decide(**kw):
    base = dict(bit_loaded=True, in_setup=True, when="admin",
                expected_key="k", key="k", admin=False, scored=2, min_scored=2)
    base.update(kw)
    return decide_start(**base)


def test_start_rule_no_bit_is_silent():
    d = _decide(bit_loaded=False)
    assert (d.accepted, d.reason, d.feedback) == (False, "no Bit loaded", FEEDBACK_NONE)


def test_start_rule_bad_key_is_silent():
    d = _decide(key="wrong")
    assert (d.accepted, d.reason, d.feedback) == (False, "bad key", FEEDBACK_NONE)
    d = _decide(expected_key=None)
    assert d.reason == "bad key"


def test_start_rule_keyed_start_on_a_non_admin_bit_is_refused_silently():
    d = _decide(when="players")
    assert d.reason == "Bit does not take an admin start"
    assert d.feedback == FEEDBACK_NONE


def test_start_rule_unkeyed_operator_start_works_on_any_bit():
    d = _decide(when="players", key=None, admin=True)
    assert d.accepted and d.feedback == FEEDBACK_ACCEPT


def test_start_rule_valid_key_outside_setup_gives_three_red():
    d = _decide(in_setup=False)
    assert (d.accepted, d.reason, d.feedback) == (False, "not in SETUP", FEEDBACK_REFUSED)
    d = _decide(in_setup=False, key=None, admin=True)
    assert d.feedback == FEEDBACK_NONE


def test_start_rule_admin_overrides_the_minimum():
    d = _decide(admin=True, scored=0)
    assert d.accepted and d.feedback == FEEDBACK_ACCEPT


def test_start_rule_minimum_not_met_gives_two_red():
    d = _decide(scored=1)
    assert (d.accepted, d.reason, d.feedback) == (False, "minimum not met", FEEDBACK_MINIMUM)


def test_start_rule_zero_minimum_allows_an_empty_start():
    d = _decide(scored=0, min_scored=0)
    assert d.accepted


def test_double_tap_from_count_two_or_two_taps_in_window():
    det = DoubleTapDetector(1.5)
    assert det.observe("d", 2, 10.0) is True
    assert det.observe("d", 1, 20.0) is False
    assert det.observe("d", 1, 21.4) is True
    assert det.observe("d", 1, 30.0) is False
    assert det.observe("d", 1, 31.6) is False       # outside the window
    assert det.observe("d", 1, 31.7) is True        # but paired with 31.6
    det.forget("d")
    assert det.observe("d", 1, 40.0) is False


def test_invite_schedule_flashes_now_then_every_interval():
    inv = InviteSchedule(5.0)
    assert inv.invited("d") is False
    assert inv.due("d", 100.0) is True
    assert inv.invited("d") is True
    assert inv.due("d", 103.0) is False
    assert inv.due("d", 105.0) is True
    inv.forget("d")
    assert inv.invited("d") is False
    inv.due("e", 1.0)
    inv.clear()
    assert inv.invited("e") is False


def test_ceremony_slots_space_joins_by_span_plus_gap():
    slots = CeremonySlots(span_s=1.8, gap_s=1.0)
    assert slots.reserve(100.0) == 100.0
    assert slots.reserve(100.1) == 102.8
    assert slots.reserve(200.0) == 200.0
