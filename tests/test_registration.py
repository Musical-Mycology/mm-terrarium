from control.registration import RegistrationState
from control.roles import Role, RoleClass, RoleTable
from control.state import State


def make_table():
    player = Role(name="player", role_class=RoleClass.SHARED,
                  capacity=None, scored=True)
    jammer = Role(name="jammer", role_class=RoleClass.JAM,
                  capacity=None, scored=False)
    conductor = Role(name="conductor", role_class=RoleClass.UNIQUE,
                      capacity=1, scored=True)
    return RoleTable(
        roles={"player": player, "jammer": jammer, "conductor": conductor},
        node_map={
            "NODE_PLAYER": ["player"],
            "NODE_JAM": ["jammer"],
            "NODE_CONDUCTOR": ["conductor"],
        },
    )


def test_join_unknown_node_is_denied():
    reg = RegistrationState(make_table())
    result = reg.join("ie1", "NODE_MISSING", State.SETUP)
    assert result.granted is False
    assert result.reason == "no such node"


def test_join_grants_shared_scored_role_in_setup():
    reg = RegistrationState(make_table())
    result = reg.join("ie1", "NODE_PLAYER", State.SETUP)
    assert result.granted is True
    assert result.role == "player"
    assert result.scored is True


def test_scored_role_denied_once_running_but_jam_still_allowed():
    reg = RegistrationState(make_table())
    scored_result = reg.join("ie1", "NODE_PLAYER", State.RUNNING)
    jam_result = reg.join("ie2", "NODE_JAM", State.RUNNING)
    assert scored_result.granted is False
    assert scored_result.reason == "registration closed for scored roles"
    assert jam_result.granted is True
    assert jam_result.role == "jammer"


def test_unique_role_denied_once_capacity_reached():
    reg = RegistrationState(make_table())
    first = reg.join("ie1", "NODE_CONDUCTOR", State.SETUP)
    second = reg.join("ie2", "NODE_CONDUCTOR", State.SETUP)
    assert first.granted is True
    assert second.granted is False
    assert second.reason == "conductor at capacity"


def test_retapping_a_different_node_switches_role():
    reg = RegistrationState(make_table())
    reg.join("ie1", "NODE_PLAYER", State.SETUP)
    switch = reg.join("ie1", "NODE_JAM", State.SETUP)
    assert switch.granted is True
    assert switch.role == "jammer"
    assert reg.assignments["ie1"][1] == "jammer"
    assert reg._counts["player"] == 0  # released when ie1 switched away


def test_release_all_clears_assignments_and_counts():
    reg = RegistrationState(make_table())
    reg.join("ie1", "NODE_PLAYER", State.SETUP)
    reg.join("ie2", "NODE_JAM", State.SETUP)
    released = reg.release_all()
    assert set(released) == {"ie1", "ie2"}
    assert reg.assignments == {}
    assert reg._counts["player"] == 0
    assert reg._counts["jammer"] == 0
