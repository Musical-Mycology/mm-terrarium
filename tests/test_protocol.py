import pytest

from uplink.protocol import (
    AbortCommand,
    LoadBitCommand,
    RunCommand,
    bit_completed_event,
    error_event,
    parse_command,
    registration_changed_event,
    state_changed_event,
)


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


def test_parse_unknown_command_raises():
    with pytest.raises(ValueError, match="unrecognized command"):
        parse_command({"command": "self_destruct"})


def test_state_changed_event_shape():
    assert state_changed_event("RUNNING") == {
        "event": "state_changed", "state": "RUNNING",
    }


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
    }


def test_error_event_shape():
    assert error_event("run", "requires SETUP") == {
        "event": "error", "command": "run", "message": "requires SETUP",
    }
