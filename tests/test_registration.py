from control.registration import RegistrationState
from control.roles import Role, RoleClass, RoleTable


def make_table():
    player = Role(name="player", role_class=RoleClass.SHARED,
                  capacity=None, scored=True)
    jammer = Role(name="jammer", role_class=RoleClass.JAM,
                  capacity=None, scored=False)
    conductor = Role(name="conductor", role_class=RoleClass.UNIQUE,
                      capacity=1, scored=True)
    understudy = Role(name="understudy", role_class=RoleClass.SHARED,
                       capacity=None, scored=True)
    return RoleTable(
        roles={"player": player, "jammer": jammer, "conductor": conductor,
               "understudy": understudy},
        node_map={
            "NODE_PLAYER": ["player"],
            "NODE_JAM": ["jammer"],
            "NODE_CONDUCTOR": ["conductor"],
            "NODE_LEAD": ["conductor", "understudy"],
        },
    )


def test_join_unknown_node_is_denied():
    reg = RegistrationState(make_table())
    result = reg.validate("ie1", "NODE_MISSING")
    assert result.granted is False
    assert result.reason == "no such node"


def test_join_grants_shared_scored_role_in_setup():
    reg = RegistrationState(make_table())
    result = reg.validate("ie1", "NODE_PLAYER")
    assert result.granted is True
    assert result.role == "player"
    assert result.scored is True


def test_unique_role_denied_once_capacity_reached():
    table = make_table()
    reg = RegistrationState(table)
    first = reg.validate("ie1", "NODE_CONDUCTOR")
    second = reg.validate("ie2", "NODE_CONDUCTOR")
    assert first.granted is True
    assert second.granted is False
    assert second.reason == "scored full"


def test_validate_ignores_second_node_returns_original_grant():
    reg = RegistrationState(_table())
    first = reg.validate("a", "P")
    assert first.granted and first.role == "player"
    second = reg.validate("a", "J")
    assert second.granted and second.role == "player"
    assert reg.validated["a"] == ("P", "player")
    assert ("player", 1, 2) in reg.counts()


def test_join_falls_through_a_multi_candidate_node_to_the_next_role():
    reg = RegistrationState(make_table())
    first = reg.validate("ie1", "NODE_LEAD")
    assert first.granted is True
    assert first.role == "conductor"  # first candidate on the fallback list

    second = reg.validate("ie2", "NODE_LEAD")
    assert second.granted is True
    assert second.role == "understudy"  # conductor now full; falls through


def test_release_all_clears_assignments_and_counts():
    table = make_table()
    reg = RegistrationState(table)
    reg.validate("ie1", "NODE_PLAYER")
    reg.assign("ie2", "NODE_JAM", table.roles["jammer"])
    released = reg.release_all()
    assert set(released) == {"ie2"}
    assert reg.assignments == {}
    assert reg.validated == {}
    assert reg._counts["player"] == 0
    assert reg._counts["jammer"] == 0


def test_counts_reflects_live_registrations_and_capacity():
    table = make_table()
    reg = RegistrationState(table)
    reg.validate("ie1", "NODE_PLAYER")
    reg.validate("ie2", "NODE_CONDUCTOR")

    counts = {name: (count, capacity) for name, count, capacity in reg.counts()}

    assert counts["player"] == (1, None)
    assert counts["conductor"] == (1, 1)
    assert counts["jammer"] == (0, None)
    assert counts["understudy"] == (0, None)


def test_granted_lists_assignments_in_materialize_order_and_skips_room():
    table = make_table()
    room = Role(name="room", role_class=RoleClass.ROOM, capacity=1, scored=False)
    table.roles["room"] = room
    table.node_map["NODE_ROOM"] = ["room"]
    reg = RegistrationState(table)
    reg.validate("ie1", "NODE_PLAYER")
    reg.materialize(["ie2", "fx1"], lambda dev: table.roles["jammer"] if dev != "fx1" else room)
    assert reg.granted() == [("ie1", "player", RoleClass.SHARED),
                             ("ie2", "jammer", RoleClass.JAM)]
    reg.release("ie2")
    assert reg.granted() == [("ie1", "player", RoleClass.SHARED)]


def _table(cap=2):
    player = Role("player", RoleClass.UNIQUE, cap, True)
    jam = Role("jammer", RoleClass.JAM, None, False)
    return RoleTable(roles={"player": player, "jammer": jam},
                     node_map={"P": ["player"], "J": ["jammer"]})


def test_validate_reserves_until_capacity():
    reg = RegistrationState(_table(cap=1))
    assert reg.validate("a", "P").granted
    r = reg.validate("b", "P")
    assert not r.granted and r.reason == "scored full"
    assert r.hint
    assert list(reg.validated) == ["a"]
    assert ("player", 1, 1) in reg.counts()


def test_validate_is_idempotent():
    reg = RegistrationState(_table())
    reg.validate("a", "P")
    again = reg.validate("a", "P")
    assert again.granted and again.role == "player"
    assert ("player", 1, 2) in reg.counts()


def test_validate_skips_unscored_and_unknown_nodes():
    reg = RegistrationState(_table())
    assert reg.validate("a", "J").reason == "no such node"
    assert reg.validate("a", "NOPE").reason == "no such node"


def test_max_scored_lowers_cap():
    reg = RegistrationState(_table(cap=5), max_scored=1)
    assert reg.scored_cap() == 1
    reg.validate("a", "P")
    assert reg.validate("b", "P").reason == "scored full"


def test_unbounded_scored_cap_is_none_without_max():
    shared = Role("player", RoleClass.SHARED, None, True)
    reg = RegistrationState(RoleTable(roles={"player": shared},
                                      node_map={"P": ["player"]}))
    assert reg.scored_cap() is None


def test_materialize_orders_scored_then_jam():
    t = _table()
    reg = RegistrationState(t)
    reg.validate("b", "P")
    reg.validate("a", "P")
    out = reg.materialize(["c", "d"], lambda dev: t.roles["jammer"])
    assert [(d, r.name) for d, r in out] == [
        ("b", "player"), ("a", "player"), ("c", "jammer"), ("d", "jammer")]
    assert reg.validated == {}
    assert reg.assignments["c"][1] == "jammer"
    assert ("player", 2, 2) in reg.counts()
    assert ("jammer", 2, None) in reg.counts()


def test_materialize_adds_synthesized_role_to_table():
    t = _table()
    reg = RegistrationState(t)
    solo = Role("solo:tuneshroom", RoleClass.JAM, None, False)
    reg.materialize(["c"], lambda dev: solo)
    assert "solo:tuneshroom" in reg.role_table.roles
    assert reg.assignments["c"] == ("", "solo:tuneshroom", RoleClass.JAM)


def test_assign_is_idempotent():
    t = _table()
    reg = RegistrationState(t)
    assert reg.assign("c", "J", t.roles["jammer"]) is True
    assert reg.assign("c", "J", t.roles["jammer"]) is False
    assert ("jammer", 1, None) in reg.counts()


def test_release_frees_validated_slot():
    reg = RegistrationState(_table(cap=1))
    reg.validate("a", "P")
    assert reg.release("a") is True
    assert reg.validate("b", "P").granted


def test_release_all_returns_only_assigned():
    t = _table()
    reg = RegistrationState(t)
    reg.validate("a", "P")
    reg.assign("c", "J", t.roles["jammer"])
    assert reg.release_all() == ["c"]
    assert reg.validated == {} and ("player", 0, 2) in reg.counts()
