"""control/admin_devices_store.py: the applied admin-device list beside
terrarium.toml (mm-fairyring box contract spec 2026-11-23 section 4.10)."""
import json
import logging

import pytest

from control.admin_devices_store import APPLIED_FILENAME, AdminDevicesStore


def test_filename():
    assert APPLIED_FILENAME == "admin_devices.applied.json"


def test_round_trip(tmp_path):
    path = tmp_path / APPLIED_FILENAME
    store = AdminDevicesStore(str(path))
    store.save(["A", "B"], 7)

    assert store.version == 7
    assert store.devices == ("A", "B")
    assert json.loads(path.read_text()) == {"version": 7, "devices": ["A", "B"]}
    reread = AdminDevicesStore(str(path))
    assert reread.version == 7
    assert reread.devices == ("A", "B")


def test_missing_file_is_version_0_and_no_devices(tmp_path, caplog):
    store = AdminDevicesStore(str(tmp_path / APPLIED_FILENAME))
    assert store.version == 0
    assert store.devices is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("text", [
    "{}", "[]", '{"version": "7"}', '{"version": 7, "devi',
    '{"version": true, "devices": []}', '{"version": 7, "devices": "A"}',
    '{"version": 7, "devices": ["A", ""]}', '{"version": 7, "devices": [3]}',
    "",
], ids=["empty-object", "list", "string-version", "truncated", "bool-version",
        "devices-not-list", "empty-gem-uid", "non-string-gem-uid", "empty-file"])
def test_malformed_file_reads_as_no_file_with_one_warning(tmp_path, caplog,
                                                          text):
    path = tmp_path / APPLIED_FILENAME
    path.write_text(text)
    caplog.set_level(logging.WARNING)

    store = AdminDevicesStore(str(path))

    assert store.version == 0
    assert store.devices is None
    store.version, store.devices                  # reading again: no new warning
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_save_leaves_no_temp_file_behind(tmp_path):
    store = AdminDevicesStore(str(tmp_path / APPLIED_FILENAME))
    store.save(["A"], 1)
    store.save([], 2)
    assert sorted(p.name for p in tmp_path.iterdir()) == [APPLIED_FILENAME]
    assert store.version == 2
    assert store.devices == ()


def test_save_into_missing_directory_raises_and_leaves_nothing(tmp_path):
    missing = tmp_path / "nope"
    store = AdminDevicesStore(str(missing / APPLIED_FILENAME))
    with pytest.raises(OSError):
        store.save(["A"], 1)
    assert not missing.exists()
    assert list(tmp_path.iterdir()) == []
    assert store.version == 0
    assert store.devices is None


def test_failed_replace_leaves_no_temp_file_and_keeps_the_old_list(
        tmp_path, monkeypatch):
    path = tmp_path / APPLIED_FILENAME
    store = AdminDevicesStore(str(path))
    store.save(["A"], 1)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("control.admin_devices_store.os.replace", boom)
    with pytest.raises(OSError):
        store.save(["B"], 2)

    assert sorted(p.name for p in tmp_path.iterdir()) == [APPLIED_FILENAME]
    assert store.version == 1
    assert store.devices == ("A",)
    assert json.loads(path.read_text()) == {"version": 1, "devices": ["A"]}
