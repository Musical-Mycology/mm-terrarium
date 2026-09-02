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
        "welcome": None, "requires": None}


def test_role_view_requires_with_requirement():
    from control.instrument import InstrumentRequirement
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True, requires="fixture")
    requirement = InstrumentRequirement(
        slot="fixture", capabilities=frozenset({"light.pixels", "light.surface"}))
    view = protocol.role_view(role, requirement)
    assert view["requires"] == {
        "slot": "fixture", "capabilities": ["light.pixels", "light.surface"]}


def test_role_view_requires_without_resolved_requirement():
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True, requires="fixture")
    view = protocol.role_view(role)
    assert view["requires"] == {"slot": "fixture", "capabilities": []}


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
        "dev": "ie3", "name": "Shroom Three", "role": "player", "url": None,
        "muted": False, "instrument": "defaultshroom", "fixture": None}
    assert protocol.device_view(info, None)["role"] is None
    assert protocol.device_view(info, "player", "http://h:9/")["url"] == \
        "http://h:9/"
    assert protocol.device_view(info, "player", None, True)["muted"] is True
    assert protocol.device_view(info, "player", None, False, "main")["fixture"] \
        == "main"


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
    assert msg["terrarium_state"] is None
    assert msg["rooms"] == []


def test_snapshot_carries_terrarium_state_and_rooms():
    rooms = [{"name": "greenhouse", "description": "the greenhouse",
             "status": None, "active": True}]
    msg = protocol.snapshot_event(
        state="SETUP", installed_bits=["TestBit"], loaded_bit="TestBit",
        roles=[], registration=[], devices=[], bit_status={},
        terrarium_state="ROOM_READY", rooms=rooms)
    assert msg["terrarium_state"] == "ROOM_READY"
    assert msg["rooms"] == rooms


def test_snapshot_carries_design_vocab_verbatim():
    vocab = {"capabilities": ["light.pixels"], "cue_kinds": ["midi"]}
    msg = protocol.snapshot_event(
        state="SETUP", installed_bits=["TestBit"], loaded_bit="TestBit",
        roles=[], registration=[], devices=[], bit_status={},
        design_vocab=vocab)
    assert msg["design_vocab"] == vocab


def test_snapshot_design_vocab_defaults_to_none():
    msg = protocol.snapshot_event(
        state="SETUP", installed_bits=["TestBit"], loaded_bit="TestBit",
        roles=[], registration=[], devices=[], bit_status={})
    assert msg["design_vocab"] is None


def test_room_lifecycle_events_are_reused_from_uplink():
    from uplink.protocol import (
        room_load_failed_event, room_load_progress_event, room_loaded_event,
        room_unloaded_event)
    assert protocol.room_loaded_event is room_loaded_event
    assert protocol.room_unloaded_event is room_unloaded_event
    assert protocol.room_load_failed_event is room_load_failed_event
    assert protocol.room_load_progress_event is room_load_progress_event


def test_room_commands_are_reused_from_uplink():
    from uplink.protocol import LoadRoomCommand, UnloadRoomCommand
    assert protocol.LoadRoomCommand is LoadRoomCommand
    assert protocol.UnloadRoomCommand is UnloadRoomCommand


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


def test_list_bits_command_parsing_is_reused_from_uplink():
    from uplink.protocol import ListBitsCommand
    assert protocol.parse_command(
        {"command": "list_bits"}) == ListBitsCommand()


def test_bits_listed_event_is_reused_from_uplink():
    assert protocol.bits_listed_event([{"name": "TestBit"}], []) == {
        "event": "bits_listed", "bits": [{"name": "TestBit"}], "errors": []}


def test_parse_admin_command_arm_room_with_default_window():
    command = parse_admin_command(
        {"command": "arm_room", "room_type": "TEST", "fixture": "main"})
    assert command == ArmRoomCommand(
        room_type="TEST", fixture="main", window_seconds=30.0)


def test_parse_admin_command_arm_room_with_explicit_window():
    command = parse_admin_command(
        {"command": "arm_room", "room_type": "DEMO", "fixture": "main",
         "window_seconds": 45.0})
    assert command == ArmRoomCommand(
        room_type="DEMO", fixture="main", window_seconds=45.0)


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


def test_snapshot_carries_a_functions_key():
    event = protocol.snapshot_event(
        state="SETUP", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={},
        functions=[{"name": "play_aurora"}])
    assert event["functions"] == [{"name": "play_aurora"}]


def test_snapshot_defaults_functions_to_an_empty_list():
    """An old caller that does not pass functions must still produce a key the
    browser can read, rather than an absent one it has to guard."""
    event = protocol.snapshot_event(
        state="IDLE", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={})
    assert event["functions"] == []


def test_functions_changed_event_shape():
    assert protocol.functions_changed_event([{"name": "x"}]) == {
        "event": "functions_changed", "functions": [{"name": "x"}],
        "instrument_functions": {}, "surface_instruments": {}, "builtins": {}}


def test_functions_changed_event_wire_bytes_for_a_generator_function():
    """Pins the enriched, kind-tagged functions payload on the wire (Task 11):
    a GENERATOR Function's card carries lane/waveform/period/lo/hi, not the
    SCRIPTED target/condition/script shape."""
    from control.cues import ROOM
    from control.function_view import function_view
    from control.functions import Function, FunctionKind, GeneratorSpec
    from control.wire_json import dumps

    glow = Function(
        name="glow", description="ambient breathing glow",
        kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                                waveform="triangle", period=12.0, lo=0, hi=127))
    event = protocol.functions_changed_event([function_view(glow)])
    assert dumps(event) == (
        '{"event": "functions_changed", "functions": [{"kind": "generator", '
        '"name": "glow", "description": "ambient breathing glow", '
        '"lane": {"dev": "@room", "status": 176, "data1": 74}, '
        '"waveform": "triangle", "period": 12.0, "lo": 0, "hi": 127}], '
        '"instrument_functions": {}, "surface_instruments": {}, "builtins": {}}')


def test_function_fired_event_shape():
    fired = {"name": "x", "fired_by": "admin-manual"}
    assert protocol.function_fired_event(fired) == {
        "event": "function_fired", "fired": fired}


def test_parse_fire_function_with_a_device():
    command = protocol.parse_admin_command(
        {"command": "fire_function", "name": "flash_device", "dev": "ie1"})
    assert command == protocol.FireFunctionCommand(name="flash_device", dev="ie1")


def test_parse_fire_function_without_a_device():
    command = protocol.parse_admin_command(
        {"command": "fire_function", "name": "play_aurora"})
    assert command.name == "play_aurora"
    assert command.dev is None


def test_parse_fire_function_rejects_a_missing_name():
    with pytest.raises(ValueError, match="non-empty string 'name'"):
        protocol.parse_admin_command({"command": "fire_function"})


def test_parse_fire_function_rejects_a_non_string_dev():
    with pytest.raises(ValueError, match="'dev' must be a string"):
        protocol.parse_admin_command(
            {"command": "fire_function", "name": "x", "dev": 7})


def test_fire_function_is_not_a_command_the_uplink_can_send():
    """Firing a venue's trigger is a local trusted-operator action, exactly
    like arm_room. A remote fairyring peer must not be able to request it."""
    from uplink import protocol as uplink_protocol
    with pytest.raises(ValueError):
        uplink_protocol.parse_command(
            {"command": "fire_function", "name": "play_aurora"})


def test_design_admin_commands_parse():
    cmd = protocol.parse_admin_command({"command": "get_design",
                                        "state": "draft", "name": "wip"})
    assert isinstance(cmd, protocol.GetDesignCommand)
    assert (cmd.state, cmd.name) == ("draft", "wip")
    cmd = protocol.parse_admin_command(
        {"command": "save_design", "name": "wip", "text": "x = 1"})
    assert isinstance(cmd, protocol.SaveDesignCommand)
    cmd = protocol.parse_admin_command({"command": "publish_design",
                                        "name": "wip"})
    assert isinstance(cmd, protocol.PublishDesignCommand)
    cmd = protocol.parse_admin_command(
        {"command": "clone_design", "source_state": "published",
         "source_name": "tuneshroom", "new_name": "fungiflute"})
    assert isinstance(cmd, protocol.CloneDesignCommand)
    cmd = protocol.parse_admin_command({"command": "list_designs"})
    assert isinstance(cmd, protocol.ListDesignsCommand)


def test_design_command_missing_field_raises():
    with pytest.raises(ValueError):
        protocol.parse_admin_command({"command": "save_design", "name": "w"})


def test_get_design_rejects_bad_state():
    with pytest.raises(ValueError):
        protocol.parse_admin_command(
            {"command": "get_design", "state": "bogus", "name": "wip"})


def test_clone_design_rejects_bad_source_state():
    with pytest.raises(ValueError):
        protocol.parse_admin_command(
            {"command": "clone_design", "source_state": "bogus",
             "source_name": "a", "new_name": "b"})


def test_design_commands_default_kind_to_instrument():
    cmd = protocol.parse_admin_command({"command": "list_designs"})
    assert cmd.kind == "instrument"
    cmd = protocol.parse_admin_command({"command": "get_design",
                                        "state": "draft", "name": "wip"})
    assert cmd.kind == "instrument"


def test_design_commands_parse_an_explicit_room_kind():
    cmd = protocol.parse_admin_command(
        {"command": "get_design", "state": "published", "name": "LOFT",
         "kind": "room"})
    assert cmd.kind == "room"


def test_unknown_design_kind_is_refused():
    with pytest.raises(ValueError, match="kind"):
        protocol.parse_admin_command({"command": "list_designs", "kind": "venue"})


def test_design_row_shape():
    class FakeEntry:
        name = "glowcap"
        state = "published"
        error = None
        kind = "instrument"
    assert protocol.design_row(FakeEntry()) == {
        "name": "glowcap", "state": "published", "error": None,
        "kind": "instrument"}


def test_designs_listed_event_shape():
    designs = [{"name": "a", "state": "published", "error": None}]
    assert protocol.designs_listed_event(designs) == {
        "event": "designs_listed", "designs": designs}


def test_designs_changed_event_shape():
    designs = [{"name": "a", "state": "draft", "error": None}]
    assert protocol.designs_changed_event(designs) == {
        "event": "designs_changed", "designs": designs}


def test_design_event_shape():
    assert protocol.design_event("glowcap", "draft", "x = 1", []) == {
        "event": "design", "name": "glowcap", "state": "draft",
        "text": "x = 1", "errors": [], "kind": "instrument"}


def test_bench_start_command_parses():
    cmd = protocol.parse_admin_command(
        {"command": "bench_start", "state": "published", "name": "tuneshroom"})
    assert isinstance(cmd, protocol.BenchStartCommand)
    assert (cmd.state, cmd.name) == ("published", "tuneshroom")


def test_bench_start_rejects_bad_state():
    with pytest.raises(ValueError):
        protocol.parse_admin_command(
            {"command": "bench_start", "state": "bogus", "name": "x"})


def test_bench_stop_command_parses():
    assert isinstance(protocol.parse_admin_command({"command": "bench_stop"}),
                      protocol.BenchStopCommand)


def test_bench_fire_command_parses():
    cmd = protocol.parse_admin_command({"command": "bench_fire", "name": "flash"})
    assert isinstance(cmd, protocol.BenchFireCommand)
    assert cmd.name == "flash"


def test_bench_fire_missing_name_raises():
    with pytest.raises(ValueError):
        protocol.parse_admin_command({"command": "bench_fire"})


def test_bench_lane_command_parses():
    cmd = protocol.parse_admin_command(
        {"command": "bench_lane", "verb": "tilt", "value": 0.5,
         "status": 176, "data1": 74})
    assert isinstance(cmd, protocol.BenchLaneCommand)
    assert (cmd.verb, cmd.value, cmd.status, cmd.data1) == ("tilt", 0.5, 176, 74)


def test_bench_lane_rejects_non_numeric_value():
    with pytest.raises(ValueError):
        protocol.parse_admin_command(
            {"command": "bench_lane", "verb": "tilt", "value": "nope",
             "status": 176, "data1": 74})


def test_list_captures_command_parses():
    assert isinstance(
        protocol.parse_admin_command({"command": "list_captures"}),
        protocol.ListCapturesCommand)


def test_capture_stats_command_parses():
    cmd = protocol.parse_admin_command(
        {"command": "capture_stats", "session": "s1", "label": "tap"})
    assert isinstance(cmd, protocol.CaptureStatsCommand)
    assert (cmd.session, cmd.label) == ("s1", "tap")


def test_capture_stats_missing_field_raises():
    with pytest.raises(ValueError):
        protocol.parse_admin_command({"command": "capture_stats", "session": "s1"})


def test_replay_trace_command_parses():
    cmd = protocol.parse_admin_command(
        {"command": "replay_trace", "state": "published", "name": "tuneshroom",
         "trigger": "tap", "session": "s1", "label": "tap", "series": 1})
    assert isinstance(cmd, protocol.ReplayTraceCommand)
    assert (cmd.state, cmd.name, cmd.trigger, cmd.session, cmd.label,
           cmd.series) == ("published", "tuneshroom", "tap", "s1", "tap", 1)


def test_replay_trace_missing_field_raises():
    with pytest.raises(ValueError):
        protocol.parse_admin_command(
            {"command": "replay_trace", "state": "published", "name": "tuneshroom",
             "trigger": "tap", "session": "s1", "label": "tap"})


def test_bench_started_event_shape():
    functions = [{"name": "flash", "description": "", "source": "builtin"}]
    assert protocol.bench_started_event(functions) == {
        "event": "bench_started", "functions": functions}


def test_bench_frame_event_shape():
    assert protocol.bench_frame_event([1, 2]) == {
        "event": "bench_frame", "channels": [1, 2]}


def test_captures_listed_event_shape():
    sessions = [{"session": "s1", "labels": {"tap": 3}}]
    assert protocol.captures_listed_event(sessions) == {
        "event": "captures_listed", "sessions": sessions}


def test_capture_stats_event_shape():
    rows = [{"label": "tap", "n": 3}]
    proposal = {"peak_g": 1.6}
    assert protocol.capture_stats_event(rows, proposal) == {
        "event": "capture_stats", "rows": rows, "proposal": proposal}
    assert protocol.capture_stats_event([], None) == {
        "event": "capture_stats", "rows": [], "proposal": None}


def test_replay_result_event_shape():
    result = {"fires": [10], "trace": {"t_ms": [0, 10], "accel_g": [1.0, 3.0]}}
    assert protocol.replay_result_event(result) == {
        "event": "replay_result", "result": result}


def test_restart_parses():
    cmd = protocol.parse_command({"command": "restart"})
    assert isinstance(cmd, protocol.RestartCommand)
