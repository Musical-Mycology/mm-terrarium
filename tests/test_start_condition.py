from types import SimpleNamespace

from control.bit_config import StartCondition
from control.registration import RegistrationState
from control.roles import Role, RoleClass, RoleTable
from control.start_condition import scored_count, start_decision


def test_immediate_matches_todays_hold():
    c = StartCondition(when="immediate")
    assert start_decision(c, scored=0, elapsed=1.0, setup_seconds=5.0) is None
    assert start_decision(c, scored=0, elapsed=5.0, setup_seconds=5.0) == "start"


def test_players_threshold_then_timeout_start_and_abort():
    c = StartCondition(when="players", min_scored=2, timeout_seconds=10,
                       on_timeout="start")
    assert start_decision(c, scored=1, elapsed=9, setup_seconds=0) is None
    assert start_decision(c, scored=2, elapsed=1, setup_seconds=0) == "start"
    assert start_decision(c, scored=1, elapsed=10, setup_seconds=0) == "start"
    a = StartCondition(when="players", min_scored=2, timeout_seconds=10,
                       on_timeout="abort")
    assert start_decision(a, scored=0, elapsed=10, setup_seconds=0) == "abort"


def test_operator_never_self_starts():
    c = StartCondition(when="operator")
    assert start_decision(c, scored=9, elapsed=999, setup_seconds=0) is None


def _role_table():
    player = Role(name="player", role_class=RoleClass.UNIQUE, capacity=None,
                  scored=True)
    jam = Role(name="jam", role_class=RoleClass.JAM, capacity=None,
               scored=False)
    return RoleTable(
        roles={"player": player, "jam": jam},
        node_map={"/ie1": ["player"], "/ie2": ["jam"]},
    )


def test_scored_count_sums_only_scored_roles():
    role_table = _role_table()
    registration = RegistrationState(role_table)
    registration.join("dev1", "/ie1", state=None)
    registration.join("dev2", "/ie1", state=None)
    registration.join("dev3", "/ie2", state=None)
    gs = SimpleNamespace(bit=SimpleNamespace(role_table=role_table),
                          registration=registration)
    assert scored_count(gs) == 2


def test_scored_count_zero_when_registration_none():
    gs = SimpleNamespace(bit=SimpleNamespace(role_table=_role_table()),
                          registration=None)
    assert scored_count(gs) == 0
