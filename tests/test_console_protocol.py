import pytest

from console import protocol
from console.protocol import ArmRoomCommand, ReleaseRoomCommand, parse_admin_command
from control.roles import Role, RoleClass


def test_role_view_shape():
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True)
    assert protocol.role_view(role) == {
        "role": "player", "class": "SHARED", "capacity": None,
        "scored": True, "ugen_manifest": {}, "light_manifest": {},
        "welcome": None}


def test_role_view_carries_v2_manifest_and_welcome():
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True,
                light_manifest={"instruments": [
                    {"instrument": "bloom", "target": "primary"}]},
                welcome={"light": {"instrument": "bloom"}})
    view = protocol.role_view(role)
    assert view["light_manifest"] == {"instruments": [
        {"instrument": "bloom", "target": "primary"}]}
    assert view["welcome"] == {"light": {"instrument": "bloom"}}


def test_device_view_shape():
    from control.device_pool import DeviceInfo
    info = DeviceInfo(dev="ie3", name="Shroom Three", protoversion="1")
    assert protocol.device_view(info, "player") == {
        "dev": "ie3", "name": "Shroom Three", "role": "player"}
    assert protocol.device_view(info, None)["role"] is None


def test_snapshot_event_shape():
    msg = protocol.snapshot_event(
        state="SETUP", installed_bits=["TestBit"], loaded_bit="TestBit",
        roles=[{"role": "player"}], registration=[{"role": "player"}],
        devices=[{"dev": "ie3"}], bit_status={"elapsed": 0.0})
    assert msg["event"] == "snapshot"
    assert msg["state"] == "SETUP"
    assert msg["installed_bits"] == ["TestBit"]
    assert msg["loaded_bit"] == "TestBit"
    assert msg["roles"] == [{"role": "player"}]
    assert msg["registration"] == [{"role": "player"}]
    assert msg["devices"] == [{"dev": "ie3"}]
    assert msg["bit_status"] == {"elapsed": 0.0}


def test_incremental_event_shapes():
    assert protocol.devices_changed_event([{"dev": "ie1"}]) == {
        "event": "devices_changed", "devices": [{"dev": "ie1"}]}
    assert protocol.bit_status_event({"k": 1}) == {
        "event": "bit_status", "status": {"k": 1}}
    assert protocol.log_event("info", "hi") == {
        "event": "log", "level": "info", "message": "hi"}


def test_command_parsing_is_reused_from_uplink():
    from uplink.protocol import LoadBitCommand
    assert protocol.parse_command(
        {"command": "load_bit", "name": "TestBit"}) == LoadBitCommand("TestBit")


def test_parse_admin_command_arm_room_with_default_window():
    command = parse_admin_command({"command": "arm_room", "room_type": "TEST"})
    assert command == ArmRoomCommand(room_type="TEST", window_seconds=30.0)


def test_parse_admin_command_arm_room_with_explicit_window():
    command = parse_admin_command(
        {"command": "arm_room", "room_type": "DEMO", "window_seconds": 45.0})
    assert command == ArmRoomCommand(room_type="DEMO", window_seconds=45.0)


def test_parse_admin_command_release_room():
    command = parse_admin_command({"command": "release_room", "room_type": "TEST"})
    assert command == ReleaseRoomCommand(room_type="TEST")


def test_parse_admin_command_rejects_missing_room_type():
    with pytest.raises(ValueError):
        parse_admin_command({"command": "arm_room"})


def test_parse_admin_command_rejects_unrecognized_command():
    with pytest.raises(ValueError):
        parse_admin_command({"command": "not_a_real_command"})


def test_room_changed_event_shape():
    from console import protocol
    event = protocol.room_changed_event({"room_type": "TEST"})
    assert event == {"event": "room_changed", "room": {"room_type": "TEST"}}


def test_snapshot_carries_room():
    from console import protocol
    event = protocol.snapshot_event(
        state="IDLE", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={},
        room={"room_type": "TEST"})
    assert event["room"] == {"room_type": "TEST"}


def test_snapshot_room_defaults_to_none():
    from console import protocol
    event = protocol.snapshot_event(
        state="IDLE", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={})
    assert event["room"] is None


def test_snapshot_carries_a_triggers_key():
    event = protocol.snapshot_event(
        state="SETUP", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={},
        triggers=[{"name": "play_aurora"}])
    assert event["triggers"] == [{"name": "play_aurora"}]


def test_snapshot_defaults_triggers_to_an_empty_list():
    """An old caller that does not pass triggers must still produce a key the
    browser can read, rather than an absent one it has to guard."""
    event = protocol.snapshot_event(
        state="IDLE", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={})
    assert event["triggers"] == []


def test_triggers_changed_event_shape():
    assert protocol.triggers_changed_event([{"name": "x"}]) == {
        "event": "triggers_changed", "triggers": [{"name": "x"}]}


def test_trigger_fired_event_shape():
    fired = {"name": "x", "fired_by": "admin-manual"}
    assert protocol.trigger_fired_event(fired) == {
        "event": "trigger_fired", "fired": fired}


def test_parse_fire_trigger_with_a_device():
    command = protocol.parse_admin_command(
        {"command": "fire_trigger", "name": "flash_device", "dev": "ie1"})
    assert command == protocol.FireTriggerCommand(name="flash_device", dev="ie1")


def test_parse_fire_trigger_without_a_device():
    command = protocol.parse_admin_command(
        {"command": "fire_trigger", "name": "play_aurora"})
    assert command.name == "play_aurora"
    assert command.dev is None


def test_parse_fire_trigger_rejects_a_missing_name():
    with pytest.raises(ValueError, match="non-empty string 'name'"):
        protocol.parse_admin_command({"command": "fire_trigger"})


def test_parse_fire_trigger_rejects_a_non_string_dev():
    with pytest.raises(ValueError, match="'dev' must be a string"):
        protocol.parse_admin_command(
            {"command": "fire_trigger", "name": "x", "dev": 7})


def test_fire_trigger_is_not_a_command_the_uplink_can_send():
    """Firing a venue's trigger is a local trusted-operator action, exactly
    like arm_room. A remote fairyring peer must not be able to request it."""
    from uplink import protocol as uplink_protocol
    with pytest.raises(ValueError):
        uplink_protocol.parse_command(
            {"command": "fire_trigger", "name": "play_aurora"})
