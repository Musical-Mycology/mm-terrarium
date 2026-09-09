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
