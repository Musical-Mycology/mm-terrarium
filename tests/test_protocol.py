import pytest

from uplink.protocol import (
    AbortCommand,
    ListBitsCommand,
    LoadBitCommand,
    LoadRoomCommand,
    RunCommand,
    UnloadRoomCommand,
    bit_completed_event,
    bits_listed_event,
    error_event,
    parse_command,
    registration_changed_event,
    room_load_failed_event,
    room_load_progress_event,
    room_loaded_event,
    room_unloaded_event,
    state_changed_event,
)
from control.wire_json import dumps
from control.roles import RoleClass


def test_parse_load_bit_command():
    cmd = parse_command({"command": "load_bit", "name": "test_bit"})
    assert cmd == LoadBitCommand(name="test_bit")


def test_parse_run_command():
    assert parse_command({"command": "run"}) == RunCommand()


def test_parse_abort_command():
    assert parse_command({"command": "abort"}) == AbortCommand()


def test_parse_load_bit_missing_name_raises():
    with pytest.raises(ValueError, match="requires a string 'name'"):
        parse_command({"command": "load_bit"})


def test_parse_load_bit_command_with_overrides():
    cmd = parse_command({
        "command": "load_bit", "name": "test_bit",
        "overrides": {"launch": {"setup_seconds": 1}},
    })
    assert cmd == LoadBitCommand(
        name="test_bit", overrides={"launch": {"setup_seconds": 1}})


def test_parse_load_bit_rejects_non_dict_overrides():
    with pytest.raises(ValueError, match="'overrides' must be a dict"):
        parse_command({"command": "load_bit", "name": "test_bit",
                       "overrides": "nope"})


def test_parse_list_bits_command():
    assert parse_command({"command": "list_bits"}) == ListBitsCommand()


def test_parse_unknown_command_raises():
    with pytest.raises(ValueError, match="unrecognized command"):
        parse_command({"command": "self_destruct"})


def test_state_changed_event_shape():
    assert state_changed_event("RUNNING") == {
        "event": "state_changed", "state": "RUNNING", "loaded_bit": None,
        "terrarium_state": None,
    }
    assert state_changed_event("RUNNING", "metronome_bit") == {
        "event": "state_changed", "state": "RUNNING", "loaded_bit": "metronome_bit",
        "terrarium_state": None,
    }
    assert state_changed_event("RUNNING", "metronome_bit",
                               terrarium_state="ROOM_READY") == {
        "event": "state_changed", "state": "RUNNING", "loaded_bit": "metronome_bit",
        "terrarium_state": "ROOM_READY",
    }
    assert dumps(state_changed_event("RUNNING")) == (
        '{"event": "state_changed", "state": "RUNNING", "loaded_bit": null, '
        '"terrarium_state": null}')


def test_parse_load_room_command():
    assert parse_command({"command": "load_room", "name": "greenhouse"}) == \
        LoadRoomCommand(name="greenhouse")


def test_parse_load_room_missing_name_raises():
    with pytest.raises(ValueError, match="requires a string 'name'"):
        parse_command({"command": "load_room"})


def test_parse_unload_room_command_default():
    assert parse_command({"command": "unload_room"}) == UnloadRoomCommand(force=False)


def test_parse_unload_room_command_with_force():
    assert parse_command({"command": "unload_room", "force": True}) == \
        UnloadRoomCommand(force=True)


def test_parse_unload_room_rejects_non_bool_force():
    with pytest.raises(ValueError, match="'force' must be a bool"):
        parse_command({"command": "unload_room", "force": "yes"})


def test_room_loaded_event_shape():
    assert room_loaded_event("greenhouse") == {
        "event": "room_loaded", "name": "greenhouse"}
    assert dumps(room_loaded_event("greenhouse")) == (
        '{"event": "room_loaded", "name": "greenhouse"}')


def test_room_unloaded_event_shape():
    assert room_unloaded_event("greenhouse") == {
        "event": "room_unloaded", "name": "greenhouse"}
    assert dumps(room_unloaded_event("greenhouse")) == (
        '{"event": "room_unloaded", "name": "greenhouse"}')


def test_room_load_failed_event_shape():
    assert room_load_failed_event("greenhouse", "arco failed") == {
        "event": "room_load_failed", "name": "greenhouse",
        "reason": "arco failed"}
    assert dumps(room_load_failed_event("greenhouse", "arco failed")) == (
        '{"event": "room_load_failed", "name": "greenhouse", '
        '"reason": "arco failed"}')


def test_room_load_progress_event_shape():
    assert room_load_progress_event("spawning arco") == {
        "event": "room_load_progress", "stage": "spawning arco"}
    assert dumps(room_load_progress_event("spawning arco")) == (
        '{"event": "room_load_progress", "stage": "spawning arco"}')


def test_registration_changed_event_shape():
    counts = [("player", 2, None), ("conductor", 1, 1)]
    assert registration_changed_event(counts) == {
        "event": "registration_changed",
        "roles": [
            {"role": "player", "count": 2, "capacity": None},
            {"role": "conductor", "count": 1, "capacity": 1},
        ],
    }


def test_bit_completed_event_shape():
    assert bit_completed_event({"score": 42}) == {
        "event": "bit_completed", "result": {"score": 42},
        "bit": {"name": "", "version": ""}, "players": [],
    }


def test_bit_completed_event_stamps_bit_name_and_version():
    assert bit_completed_event({"score": 42}, "test_bit", "1.0") == {
        "event": "bit_completed", "result": {"score": 42},
        "bit": {"name": "test_bit", "version": "1.0"}, "players": [],
    }


def test_bit_completed_event_stamps_room_provenance_when_given():
    assert bit_completed_event({"score": 42}, "test_bit", "1.0",
                               room_name="atrium",
                               terrarium_config_version="1-abcdef012345") == {
        "event": "bit_completed", "result": {"score": 42},
        "bit": {"name": "test_bit", "version": "1.0"},
        "room_name": "atrium",
        "terrarium_config_version": "1-abcdef012345",
        "players": [],
    }


def test_bit_completed_event_omits_room_provenance_when_none():
    event = bit_completed_event({"score": 42}, "test_bit", "1.0")
    assert "room_name" not in event
    assert "terrarium_config_version" not in event


def test_bits_listed_event_shape():
    bits = [{"name": "test_bit"}]
    errors = [{"path": "x", "message": "bad"}]
    assert bits_listed_event(bits, errors) == {
        "event": "bits_listed", "bits": bits, "errors": errors,
    }


def test_error_event_shape():
    assert error_event("run", "requires SETUP") == {
        "event": "error", "command": "run", "message": "requires SETUP",
    }


def test_load_bit_command_parses_an_optional_room():
    from uplink.protocol import LoadBitCommand, parse_command
    cmd = parse_command({"command": "load_bit", "name": "TestBit",
                         "room": "DEMO"})
    assert cmd == LoadBitCommand(name="TestBit", overrides=None, room="DEMO")


def test_load_bit_command_room_defaults_to_none():
    from uplink.protocol import parse_command
    cmd = parse_command({"command": "load_bit", "name": "TestBit"})
    assert cmd.room is None


def test_load_bit_command_rejects_a_non_string_room():
    import pytest
    from uplink.protocol import parse_command
    with pytest.raises(ValueError):
        parse_command({"command": "load_bit", "name": "TestBit", "room": 7})


def test_players_view_maps_jam_and_everything_else_to_scored():
    from uplink.protocol import players_view
    granted = [("g1", "jammer", RoleClass.JAM), ("g2", "player", RoleClass.SHARED),
               ("g3", "lead", RoleClass.UNIQUE)]
    assert players_view(granted) == [
        {"dev": "g1", "role": "jammer", "class": "jam"},
        {"dev": "g2", "role": "player", "class": "scored"},
        {"dev": "g3", "role": "lead", "class": "scored"}]


def test_bit_completed_event_always_carries_players():
    assert bit_completed_event({"score": 1}, "B", "0.1")["players"] == []
    ev = bit_completed_event(None, "B", "0.1",
                             players=[{"dev": "g1", "role": "player", "class": "scored"}])
    assert ev["result"] is None
    assert ev["players"] == [{"dev": "g1", "role": "player", "class": "scored"}]
    dumps(ev)     # JSON-serialisable
