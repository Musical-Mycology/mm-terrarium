"""The LAN static server that serves www/ to guest phones (spec section
4.1). Ephemeral port in tests; real sockets on loopback only."""
import os
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from harness.www_server import WWW_PORT, WwwServer, lan_ip


@pytest.fixture
def www(tmp_path):
    (tmp_path / "index.htm").write_text("<p>hello</p>", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.dart.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "app" / "canvaskit.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    (tmp_path / "app" / "index.html").write_text("<p>app</p>", encoding="utf-8")
    server = WwwServer(str(tmp_path), host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_default_port_is_the_documented_one():
    assert WWW_PORT == 8788


def test_serves_the_index_and_the_app_files(www):
    base = f"http://127.0.0.1:{www.port}"
    assert urlopen(f"{base}/index.htm").read() == b"<p>hello</p>"
    assert urlopen(f"{base}/app/index.html").read() == b"<p>app</p>"


def test_content_types_the_flutter_build_needs(www):
    base = f"http://127.0.0.1:{www.port}"
    js = urlopen(f"{base}/app/main.dart.js")
    assert js.headers["Content-Type"].startswith("text/javascript")
    wasm = urlopen(f"{base}/app/canvaskit.wasm")
    assert wasm.headers["Content-Type"] == "application/wasm"


def test_refuses_to_leave_the_root(www, tmp_path):
    (tmp_path.parent / "outside.txt").write_text("secret", encoding="utf-8")
    base = f"http://127.0.0.1:{www.port}"
    with pytest.raises(HTTPError) as err:
        urlopen(f"{base}/../outside.txt")
    assert err.value.code == 404


def test_url_uses_the_bound_port_and_a_given_host(www):
    assert www.url("10.0.0.7") == f"http://10.0.0.7:{www.port}/"
    assert www.url().startswith("http://")


def test_lan_ip_is_an_ipv4_literal():
    ip = lan_ip()
    parts = ip.split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts)


def test_stop_is_idempotent(tmp_path):
    server = WwwServer(str(tmp_path), host="127.0.0.1", port=0)
    server.start()
    server.stop()
    server.stop()


def test_lan_ip_falls_back_to_loopback_when_the_probe_finds_no_address(
        monkeypatch, caplog):
    """An unspecified 0.0.0.0 from getsockname is not an address a phone can
    open, so it means the same thing as the probe raising: no LAN. Both take
    the loopback fallback, and the fallback is logged, because the printed
    WWW_URL would otherwise read as a working link that reaches no guest."""
    import logging
    import socket as socket_module

    import harness.www_server as www_server

    class NoRouteSocket:
        def connect(self, address):
            return None

        def getsockname(self):
            return ("0.0.0.0", 0)

        def close(self):
            return None

    monkeypatch.setattr(www_server.socket, "socket",
                        lambda *a, **kw: NoRouteSocket())
    with caplog.at_level(logging.WARNING, logger="harness.www_server"):
        assert www_server.lan_ip() == "127.0.0.1"
    assert any("no LAN address" in rec.getMessage() for rec in caplog.records)
    assert socket_module is www_server.socket        # module, not shadowed


import urllib.request
from control.lobby import StartRequest, TERRARIUM_ADMIN


def test_start_route_queues_a_loopback_hit_as_the_terrarium(www):
    with urllib.request.urlopen(f"http://127.0.0.1:{www.port}/start?key=abc&dev=gem-1") as r:
        assert r.status == 202
        body = r.read().decode()
    assert "abc" not in body
    req = www.start_requests.get_nowait()
    assert req == StartRequest("abc", TERRARIUM_ADMIN, "web:terrarium")


def test_start_route_labels_a_remote_hit_by_dev_or_anonymous(www, monkeypatch):
    from harness import www_server
    monkeypatch.setattr(www_server, "_LOOPBACK", ())          # pretend not loopback
    urllib.request.urlopen(f"http://127.0.0.1:{www.port}/start?key=k&dev=gem-2").read()
    assert www.start_requests.get_nowait() == StartRequest("k", "gem-2", "web:gem-2")
    urllib.request.urlopen(f"http://127.0.0.1:{www.port}/start?key=k").read()
    assert www.start_requests.get_nowait() == StartRequest("k", None, "web:anonymous")


def test_start_route_only_get_and_other_paths_still_serve_files(www):
    # /index.html doesn't exist in this fixture (it writes index.htm); use
    # a path the fixture actually creates so this exercises "not /start
    # still serves files" rather than a fixture mismatch.
    with urllib.request.urlopen(f"http://127.0.0.1:{www.port}/index.htm") as r:
        assert r.status == 200


import threading
from urllib.parse import quote

from control.prepare import PrepareRequest


def _answer(www, *, accepted, reason=None, visible=True):
    """Stand in for DeviceLinkAgent's drain on a helper thread: take one
    request, fill its reply, release the handler."""
    taken = []

    def drain():
        req = www.prepare_requests.get(timeout=2.0)
        taken.append(req)
        req.reply.accepted = accepted
        req.reply.reason = reason
        req.reply.visible = visible
        req.reply.done.set()

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    return taken, t


def _get(www, query):
    url = f"http://127.0.0.1:{www.port}/prepare?{query}"
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read().decode()
    except HTTPError as err:
        return err.code, err.read().decode()


def test_prepare_accept_is_202_and_queues_a_loopback_hit_as_the_terrarium(www):
    taken, t = _answer(www, accepted=True)
    status, body = _get(www, "key=abc&bit=MetronomeBit&dev=gem-1")
    t.join(2.0)
    assert (status, body) == (202, "prepare requested\n")
    assert "abc" not in body
    req = taken[0]
    assert isinstance(req, PrepareRequest)
    assert (req.key, req.bit, req.dev, req.source) == (
        "abc", "MetronomeBit", TERRARIUM_ADMIN, "web:terrarium")


def test_prepare_visible_refusal_is_409_with_the_reason(www):
    _, t = _answer(www, accepted=False, reason="busy", visible=True)
    status, body = _get(www, "key=abc&bit=MetronomeBit")
    t.join(2.0)
    assert (status, body) == (409, "busy\n")


def test_prepare_silent_refusal_looks_exactly_like_an_accept(www):
    _, t = _answer(www, accepted=False, reason="bad key", visible=False)
    status, body = _get(www, "key=wrong&bit=MetronomeBit")
    t.join(2.0)
    assert (status, body) == (202, "prepare requested\n")
    assert "wrong" not in body and "bad key" not in body


def test_prepare_undrained_request_is_503(www, monkeypatch):
    from harness import www_server
    monkeypatch.setattr(www_server, "PREPARE_REPLY_TIMEOUT_S", 0.05)
    status, body = _get(www, "key=abc&bit=MetronomeBit")
    assert (status, body) == (503, "prepare not drained\n")
    www.prepare_requests.get_nowait()     # it was queued, nobody answered


def test_prepare_labels_a_remote_hit_by_dev_or_anonymous(www, monkeypatch):
    from harness import www_server
    monkeypatch.setattr(www_server, "_LOOPBACK", ())
    taken, t = _answer(www, accepted=True)
    _get(www, "key=k&bit=B&dev=gem-2")
    t.join(2.0)
    assert (taken[0].dev, taken[0].source) == ("gem-2", "web:gem-2")
    taken, t = _answer(www, accepted=True)
    _get(www, "key=k&bit=B")
    t.join(2.0)
    assert (taken[0].dev, taken[0].source) == (None, "web:anonymous")


def test_prepare_missing_key_is_queued_with_none(www):
    taken, t = _answer(www, accepted=False, reason="bad key", visible=False)
    _get(www, "bit=B")
    t.join(2.0)
    assert taken[0].key is None and taken[0].bit == "B"


def test_prepare_queue_full_is_503(www, monkeypatch):
    from harness import www_server
    monkeypatch.setattr(www_server, "PREPARE_REPLY_TIMEOUT_S", 0.05)
    for _ in range(www_server.PREPARE_QUEUE_MAX):
        www.prepare_requests.put_nowait(object())
    status, body = _get(www, "key=k&bit=B")
    assert (status, body) == (503, "prepare queue full\n")


def test_prepare_is_404_when_not_wired(tmp_path):
    from harness.www_server import WwwServer
    server = WwwServer(str(tmp_path), host="127.0.0.1", port=0)
    server.prepare_requests = None
    server.start()
    try:
        status, _ = _get(server, "key=k&bit=B")
    finally:
        server.stop()
    assert status == 404


def test_prepare_key_never_reaches_a_log_line(www, caplog):
    import logging
    _, t = _answer(www, accepted=False, reason="busy", visible=True)
    with caplog.at_level(logging.DEBUG):
        _get(www, "key=" + quote("hunter2") + "&bit=B")
    t.join(2.0)
    assert "hunter2" not in caplog.text
