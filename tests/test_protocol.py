import pytest

from uplink.protocol import (
    AbortCommand,
    ListBitsCommand,
    LoadBitCommand,
    RunCommand,
    bit_completed_event,
    bits_listed_event,
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
        "bit": {"name": "", "version": ""},
    }


def test_bit_completed_event_stamps_bit_name_and_version():
    assert bit_completed_event({"score": 42}, "test_bit", "1.0") == {
        "event": "bit_completed", "result": {"score": 42},
        "bit": {"name": "test_bit", "version": "1.0"},
    }


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
