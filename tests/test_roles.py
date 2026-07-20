from control.roles import Role, RoleClass, RoleTable


def make_two_role_table():
    player = Role(name="player", role_class=RoleClass.SHARED,
                  capacity=None, scored=True)
    jammer = Role(name="jammer", role_class=RoleClass.JAM,
                  capacity=None, scored=False)
    return RoleTable(
        roles={"player": player, "jammer": jammer},
        node_map={"A": ["player"], "B": ["jammer"]},
    )


def test_role_defaults_to_empty_ugen_manifest():
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True)
    assert role.ugen_manifest == []


def test_role_table_holds_roles_and_node_fallback_lists():
    table = make_two_role_table()
    assert table.roles["player"].scored is True
    assert table.roles["jammer"].scored is False
    assert table.node_map["A"] == ["player"]
    assert table.node_map["B"] == ["jammer"]


def test_unique_role_has_integer_capacity():
    conductor = Role(name="conductor", role_class=RoleClass.UNIQUE,
                      capacity=1, scored=True)
    assert conductor.capacity == 1
