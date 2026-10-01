from control.instrument import DEFAULTSHROOM, TUNESHROOM
from control.jam_role import (SOLO_PREFIX, bit_jam_role, is_solo_role,
                              solo_event, solo_role)
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


def test_bit_jam_role():
    jam = Role("jammer", RoleClass.JAM, None, False)
    t = RoleTable(roles={"p": Role("p", RoleClass.UNIQUE, 1, True),
                         "jammer": jam}, node_map={})
    assert bit_jam_role(t) is jam
    assert bit_jam_role(RoleTable(roles={}, node_map={})) is None


def test_solo_event():
    assert solo_event("tap", ["d", 1.0, 50.0, 2]) == "double_tap"
    assert solo_event("tap", ["d", 1.0, 50.0, 1]) == "tap"
    assert solo_event("shake", ["d", 1.0, 1.0, 1.0]) == "shake"
    assert is_solo_role("solo:tuneshroom") and not is_solo_role("jammer")
