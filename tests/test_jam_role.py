from control.instrument import DEFAULTSHROOM, TUNESHROOM
from control.jam_role import (SOLO_PREFIX, is_solo_role,
                              solo_event, solo_role, unscored_roles)
from control.roles import Role, RoleClass, RoleTable


def test_solo_role_from_solo_table():
    role = solo_role(TUNESHROOM)
    assert role.name == SOLO_PREFIX + TUNESHROOM.name
    assert role.role_class is RoleClass.JAM and role.scored is False
    assert role.capacity is None
    assert role.light_manifest == TUNESHROOM.solo.light_manifest
    assert set(role.uses) == {"tap", "shake"}


def test_solo_role_falls_back_to_ambient():
    assert DEFAULTSHROOM.solo is None
    role = solo_role(DEFAULTSHROOM)
    assert role.light_manifest == DEFAULTSHROOM.light_manifest
    assert role.uses == []


def test_solo_role_manifest_is_a_copy():
    role = solo_role(TUNESHROOM)
    role.light_manifest["x"] = 1
    assert "x" not in TUNESHROOM.solo.light_manifest


def test_unscored_roles_jam_first_then_declaration_order():
    shared1 = Role("s1", RoleClass.SHARED, None, False)
    scored = Role("p", RoleClass.UNIQUE, 1, True)
    uniq = Role("u", RoleClass.UNIQUE, 1, False)
    jam = Role("jammer", RoleClass.JAM, None, False)
    room = Role("fixture", RoleClass.ROOM, None, False)
    t = RoleTable(roles={"s1": shared1, "p": scored, "u": uniq,
                         "fixture": room, "jammer": jam}, node_map={})
    assert unscored_roles(t) == [jam, shared1, uniq]
    assert unscored_roles(RoleTable(roles={}, node_map={})) == []


def test_solo_event():
    assert solo_event("tap", ["d", 1.0, 50.0, 2]) == "double_tap"
    assert solo_event("tap", ["d", 1.0, 50.0, 1]) == "tap"
    assert solo_event("shake", ["d", 1.0, 1.0, 1.0]) == "shake"
    assert is_solo_role("solo:tuneshroom") and not is_solo_role("jammer")


def test_unscored_roles_never_returns_a_solo_role():
    shared = Role("recorder", RoleClass.SHARED, None, False)
    t = RoleTable(roles={"recorder": shared}, node_map={})
    t.roles["solo:tuneshroom"] = solo_role(TUNESHROOM)
    assert unscored_roles(t) == [shared]
