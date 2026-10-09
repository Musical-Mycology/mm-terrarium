"""The committed Arco launch directory: Arco reads arco_server_prefs.json
from its cwd (arco/server/src/prefs.cpp:72,139) and turns its HTTP server
on when http_root is non-empty (prefs.cpp:209). These pin the file, the
root it names, and the port terrarium_boot prints."""
import json
import os

from harness.terrarium_boot import ARCO_HTTP_PORT, ARCOSERVER_DIR


def _prefs() -> dict:
    with open(os.path.join(ARCOSERVER_DIR, "arco_server_prefs.json"),
              encoding="utf-8") as handle:
        raw = json.load(handle)
    # Arco's format: {"config name": [{"key": "value"}, ...]}
    return {k: v for entry in raw["default"] for k, v in entry.items()}


def test_prefs_name_the_default_configuration():
    with open(os.path.join(ARCOSERVER_DIR, "arco_server_prefs.json"),
              encoding="utf-8") as handle:
        raw = json.load(handle)
    assert raw["__configuration__"] == [{"__configuration__": "default"}]
    assert "default" in raw


def test_http_root_is_the_repo_www_dir_and_port_matches_the_printed_one():
    prefs = _prefs()
    assert prefs["http_root"] == "www"
    # O2's HTTP server rejects any served path containing "..", root
    # included (o2/src/websock.cpp:905), so http_root must never be a
    # parent-relative path -- reach www/ through the arcoserver/www symlink.
    assert ".." not in prefs["http_root"]
    assert prefs["http_port"] == str(ARCO_HTTP_PORT)
    www = os.path.normpath(os.path.join(ARCOSERVER_DIR, prefs["http_root"]))
    assert os.path.isfile(os.path.join(www, "o2ws.js"))
    assert os.path.isfile(os.path.join(www, "index.htm"))   # Arco's index name
    assert os.path.isfile(os.path.join(www, "o2wsclocksync.htm"))


def test_polling_rate_clears_thirty_devices():
    # O2 reads one message per socket per o2_poll, and Arco polls once per
    # main-loop pass at polling_rate (default 500 Hz). Every device's /leds
    # shares Arco's one UDP socket, so 500 Hz backs frames up by seconds
    # from 6 devices; 5000 Hz measured clean at 30 (2026-10-09 sweep).
    assert int(_prefs()["polling_rate"]) >= 5000


def test_each_setting_sits_on_one_line():
    # Arco's config reader silently ignored a json.dump(indent=2) rewrite of
    # this file (every setting fell back to its default) while still
    # printing "finished reading". Keep one {"key": "value"} per line.
    with open(os.path.join(ARCOSERVER_DIR, "arco_server_prefs.json"),
              encoding="utf-8") as handle:
        text = handle.read()
    for key, value in _prefs().items():
        assert f'{{"{key}": "{value}"}}' in text, key


def test_http_root_fits_arcos_buffer():
    assert len(_prefs()["http_root"]) < 120       # prefs.cpp:64 char[120]


def test_arcoserver_www_is_a_symlink_to_the_top_level_www():
    link = os.path.join(ARCOSERVER_DIR, "www")
    assert os.path.islink(link)
    assert os.readlink(link) == "../www"


def test_arco_popen_launches_from_the_arcoserver_dir(monkeypatch):
    import argparse

    from harness.terrarium_boot import _arco_popen

    seen = {}

    class _Popen:
        def __init__(self, command, **kwargs):
            seen["command"] = command
            seen["kwargs"] = kwargs

    monkeypatch.setattr("harness.terrarium_boot.subprocess.Popen", _Popen)
    popen = _arco_popen(argparse.Namespace(arco_pty=False, arco_log=None))
    popen(["arco-server"])
    assert seen["kwargs"]["cwd"] == ARCOSERVER_DIR


def test_arco_pty_popen_launches_from_the_arcoserver_dir(monkeypatch):
    import argparse

    import control.arco_process as arco_process
    from harness.terrarium_boot import _arco_popen

    seen = {}

    def fake_pty_popen(command, log_path=None, cwd=None):
        seen["cwd"] = cwd
        seen["log_path"] = log_path
        return object()

    monkeypatch.setattr(arco_process, "pty_popen", fake_pty_popen)
    popen = _arco_popen(argparse.Namespace(arco_pty=True, arco_log="/tmp/a.log"))
    popen(["arco-server"])
    assert seen == {"cwd": ARCOSERVER_DIR, "log_path": "/tmp/a.log"}
