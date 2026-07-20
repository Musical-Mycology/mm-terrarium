from bits.test_bit import TestBit
from control.roles import RoleClass


def test_role_table_has_one_scored_and_one_jam_role():
    bit = TestBit()
    table = bit.role_table
    assert table.roles["player"].scored is True
    assert table.roles["player"].role_class == RoleClass.SHARED
    assert table.roles["jammer"].scored is False
    assert table.roles["jammer"].role_class == RoleClass.JAM


def test_role_ugen_manifests_are_present_but_empty_placeholders():
    bit = TestBit()
    table = bit.role_table
    assert table.roles["player"].ugen_manifest == []
    assert table.roles["jammer"].ugen_manifest == []


def test_node_map_grants_each_role_from_its_own_node():
    bit = TestBit()
    table = bit.role_table
    assert table.node_map["TEST_PLAYER_NODE"] == ["player"]
    assert table.node_map["TEST_JAM_NODE"] == ["jammer"]


def test_lifecycle_hooks_flip_flags():
    bit = TestBit()
    bit.on_setup_enter()
    bit.on_run_start()
    bit.on_complete()
    bit.on_unload()
    assert bit._setup_entered is True
    assert bit._run_started is True
    assert bit._completed is True
    assert bit._unloaded is True


def test_update_completes_after_run_duration_elapses():
    bit = TestBit(run_duration=1.0)
    bit.on_run_start()
    assert bit.update(0.4) is False
    assert bit.update(0.4) is False
    assert bit.update(0.4) is True  # 1.2s elapsed >= 1.0s
