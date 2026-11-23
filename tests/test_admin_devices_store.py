"""control/admin_devices_store.py: the applied admin-device list beside
terrarium.toml (mm-fairyring box contract spec 2026-11-23 section 4.10)."""
import json
import logging

import pytest

from control.admin_devices_store import APPLIED_FILENAME, AdminDevicesStore


def test_filename():
    assert APPLIED_FILENAME == "admin_devices.applied.json"


IDENT = dict(tenant_slug="mm", terrarium_name="main-stage")


def test_round_trip(tmp_path):
    path = tmp_path / APPLIED_FILENAME
    store = AdminDevicesStore(str(path), **IDENT)
    store.save(["A", "B"], 7, **IDENT)

    assert store.version == 7
    assert store.devices == ("A", "B")
    assert json.loads(path.read_text()) == {
        "version": 7, "devices": ["A", "B"],
        "tenant_slug": "mm", "terrarium_name": "main-stage"}
    reread = AdminDevicesStore(str(path), **IDENT)
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
    store.save(["A"], 1, **IDENT)
    store.save([], 2, **IDENT)
    assert sorted(p.name for p in tmp_path.iterdir()) == [APPLIED_FILENAME]
    assert store.version == 2
    assert store.devices == ()


def test_save_into_missing_directory_raises_and_leaves_nothing(tmp_path):
    missing = tmp_path / "nope"
    store = AdminDevicesStore(str(missing / APPLIED_FILENAME))
    with pytest.raises(OSError):
        store.save(["A"], 1, **IDENT)
    assert not missing.exists()
    assert list(tmp_path.iterdir()) == []
    assert store.version == 0
    assert store.devices is None


def test_failed_replace_leaves_no_temp_file_and_keeps_the_old_list(
        tmp_path, monkeypatch):
    path = tmp_path / APPLIED_FILENAME
    store = AdminDevicesStore(str(path))
    store.save(["A"], 1, **IDENT)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("control.admin_devices_store.os.replace", boom)
    with pytest.raises(OSError):
        store.save(["B"], 2, **IDENT)

    assert sorted(p.name for p in tmp_path.iterdir()) == [APPLIED_FILENAME]
    assert store.version == 1
    assert store.devices == ("A",)
    assert json.loads(path.read_text()) == {
        "version": 1, "devices": ["A"], **IDENT}


# --- final fix wave: the file is bound to the identity it was applied for --

def _write(path, **fields):
    data = {"version": 4, "devices": ["gem-secretish"], **IDENT}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    path.write_text(json.dumps(data))


@pytest.mark.parametrize("field,value", [
    ("tenant_slug", "crf"), ("terrarium_name", "side-stage"),
    ("tenant_slug", None), ("terrarium_name", None),
], ids=["other-tenant", "other-terrarium", "no-tenant", "no-terrarium"])
def test_identity_mismatch_reads_as_absent_with_one_warning(
        tmp_path, caplog, field, value):
    path = tmp_path / APPLIED_FILENAME
    _write(path, **{field: value})
    caplog.set_level(logging.WARNING)

    store = AdminDevicesStore(str(path), **IDENT)

    assert store.version == 0
    assert store.devices is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert field in warnings[0].getMessage()
    assert "gem-secretish" not in caplog.text


def test_previous_format_without_identity_reads_as_absent(tmp_path, caplog):
    path = tmp_path / APPLIED_FILENAME
    path.write_text(json.dumps({"version": 4, "devices": ["A"]}))
    caplog.set_level(logging.WARNING)
    store = AdminDevicesStore(str(path), **IDENT)
    assert store.version == 0 and store.devices is None
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_an_expected_none_skips_that_comparison(tmp_path, caplog):
    path = tmp_path / APPLIED_FILENAME
    _write(path, tenant_slug="crf")
    caplog.set_level(logging.WARNING)

    store = AdminDevicesStore(str(path), terrarium_name="main-stage")
    assert store.version == 4 and store.devices == ("gem-secretish",)

    _write(path, terrarium_name="other", tenant_slug=None)
    store = AdminDevicesStore(str(path), tenant_slug=None, terrarium_name=None)
    assert store.version == 4
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    # The terrarium name is still compared when only the tenant is unknown.
    store = AdminDevicesStore(str(path), terrarium_name="main-stage")
    assert store.version == 0 and store.devices is None


def test_non_string_identity_in_the_file_is_a_mismatch(tmp_path):
    path = tmp_path / APPLIED_FILENAME
    _write(path, tenant_slug=["mm"])
    assert AdminDevicesStore(str(path), **IDENT).devices is None


def test_save_requires_the_identity_keywords(tmp_path):
    store = AdminDevicesStore(str(tmp_path / APPLIED_FILENAME))
    with pytest.raises(TypeError):
        store.save(["A"], 1)
    with pytest.raises(TypeError):
        store.save(["A"], 1, "mm", "main-stage")
