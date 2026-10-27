import json
import time

from urllib.request import urlopen

from websockets.sync.client import connect as ws_connect

from bits.test.test_bit import TestBit
from console.agent import ConsoleAgent
from console.server import ConsoleServer
from control.engine import GameServer


def test_get_root_serves_index_html():
    # "new WebSocket" used to be asserted here too, back when index.html was
    # one self-contained file. It now lives in console.js; that split is
    # covered by test_console_static.py's test_the_lifecycle_controls_
    # survived_the_split, which scans all served assets together.
    server = ConsoleServer(port=0)
    server.start()
    try:
        body = urlopen(f"http://127.0.0.1:{server.port}/").read().decode()
        assert "Terrarium Console" in body
    finally:
        server.stop()


def test_get_font_serves_from_fonts_subdirectory():
    server = ConsoleServer(port=0)
    server.start()
    try:
        resp = urlopen(
            f"http://127.0.0.1:{server.port}/fonts/JetBrainsMono-Regular.ttf")
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "font/ttf"
    finally:
        server.stop()


def test_client_gets_snapshot_and_command_round_trips():
    gs = GameServer({"TestBit": TestBit})
    server = ConsoleServer(port=0)
    agent = ConsoleAgent(gs, server)
    server.start()
    try:
        with ws_connect(f"ws://127.0.0.1:{server.port}/ws") as ws:
            # _recv_event drives agent.poll() until the event arrives; the
            # first poll drains the new client and sends its snapshot.
            snap = _recv_event(ws, agent, "snapshot")
            assert snap["state"] == "IDLE"

            ws.send(json.dumps({"command": "load_bit", "name": "TestBit"}))
            state = _recv_event(ws, agent, "state_changed")
            assert state["state"] in ("LOADING", "LOADED", "SETUP")
    finally:
        server.stop()


def _recv_event(ws, agent, event_name, timeout=2.0):
    """Interleave agent.poll() (tick thread work) with client recv until the
    named event arrives."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        agent.poll()
        try:
            raw = ws.recv(timeout=0.05)
        except TimeoutError:
            continue
        msg = json.loads(raw)
        if msg.get("event") == event_name:
            return msg
    raise AssertionError(f"did not receive {event_name!r} in time")


def test_root_serves_index_html():
    import urllib.request
    from console.server import ConsoleServer
    server = ConsoleServer(port=0)
    server.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/") as r:
            body = r.read().decode()
            assert r.headers["Content-Type"].startswith("text/html")
        assert "Terrarium Console" in body
    finally:
        server.stop()


def test_css_and_js_are_served_with_their_own_content_types():
    import urllib.request
    from console.server import ConsoleServer
    server = ConsoleServer(port=0)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        with urllib.request.urlopen(f"{base}/terrarium.css") as r:
            assert r.headers["Content-Type"].startswith("text/css")
        with urllib.request.urlopen(f"{base}/shell.js") as r:
            assert r.headers["Content-Type"].startswith("text/javascript")
    finally:
        server.stop()


def test_an_unknown_path_is_a_404():
    import urllib.error
    import urllib.request
    from console.server import ConsoleServer
    server = ConsoleServer(port=0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/nope.js"
        try:
            urllib.request.urlopen(url)
            assert False, "expected a 404"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.stop()


def test_a_traversal_attempt_is_refused():
    """The console is trusted-LAN and unauthenticated, so path handling must
    not be the thing that widens that."""
    import urllib.error
    import urllib.request
    from console.server import ConsoleServer
    server = ConsoleServer(port=0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/../agent.py"
        try:
            urllib.request.urlopen(url)
            assert False, "expected a refusal"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.stop()


# --- vendored files and the fixture route provider -------------------------
def _get(server, path):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read(), resp
    finally:
        conn.close()


def _get_status(server, path):
    return _get(server, path)[0]


def _static_tree(tmp_path):
    static = tmp_path / "static"
    for rel, text in [("index.html", "<title>x</title>"),
                      ("vendor/three/addons/utils/a.js", "addons"),
                      ("vendor/three/build/a.js", "build"),
                      ("fonts/X.ttf", "ttf")]:
        f = static / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return static


def _started(tmp_path):
    server = ConsoleServer(port=0, static_dir=_static_tree(tmp_path))
    server.start()
    return server


def test_vendor_files_are_served_by_exact_relative_path(tmp_path):
    server = _started(tmp_path)
    try:
        assert _get(server, "/vendor/three/addons/utils/a.js")[1] == b"addons"
        assert _get(server, "/vendor/three/build/a.js")[1] == b"build"
    finally:
        server.stop()


def test_vendor_files_are_not_reachable_by_basename(tmp_path):
    server = _started(tmp_path)
    try:
        assert _get_status(server, "/a.js") == 404
    finally:
        server.stop()


def test_vendor_traversal_and_encoded_traversal_are_404(tmp_path):
    server = _started(tmp_path)
    try:
        for p in ["/vendor/../server.py", "/vendor/three/../../agent.py",
                  "/vendor/..%2fserver.py", "/vendor/%2e%2e/server.py",
                  # real files, non-canonical spellings
                  "/vendor/three/build/../build/a.js",
                  "/vendor/three/addons/utils/%61.js",
                  "/vendor//three/build/a.js"]:
            assert _get_status(server, p) == 404
    finally:
        server.stop()


def test_top_level_basename_rule_is_unchanged(tmp_path):
    server = _started(tmp_path)
    try:
        assert _get_status(server, "/index.html") == 200
        status, body, resp = _get(server, "/fonts/X.ttf")
        assert (status, body) == (200, b"ttf")
        assert resp.getheader("Content-Type") == "font/ttf"
    finally:
        server.stop()


def test_route_provider_answers_unknown_paths(tmp_path):
    server = _started(tmp_path)
    seen = []

    def provider(p):
        seen.append(p)
        if p == "/fixture-layout/tower.json":
            return b"{}", "application/json", {"Cache-Control": "no-store"}
        return None

    server.set_route_provider(provider)
    try:
        status, body, resp = _get(server, "/fixture-layout/tower.json?sha=1")
        assert (status, body) == (200, b"{}")
        assert resp.getheader("Content-Type") == "application/json"
        assert resp.getheader("Cache-Control") == "no-store"
        assert seen == ["/fixture-layout/tower.json"]   # query stripped
        assert _get_status(server, "/fixture-layout/x.json") == 404
    finally:
        server.stop()


def test_a_raising_route_provider_is_a_404(tmp_path, caplog):
    import logging
    server = _started(tmp_path)

    def boom(p):
        raise RuntimeError("nope")

    server.set_route_provider(boom)
    try:
        with caplog.at_level(logging.ERROR, logger="console.server"):
            assert _get_status(server, "/fixture-model/tower.glb") == 404
        assert any("route provider failed" in r.getMessage()
                   and r.exc_info for r in caplog.records)
    finally:
        server.stop()


def test_a_malformed_route_provider_return_is_a_404(tmp_path):
    server = _started(tmp_path)
    try:
        for bad in (b"body", (b"body", "text/plain"), 5):
            server.set_route_provider(lambda p, bad=bad: bad)
            assert _get_status(server, "/fixture-model/tower.glb") == 404
    finally:
        server.stop()


def test_provider_cannot_duplicate_framing_headers(tmp_path):
    server = _started(tmp_path)
    server.set_route_provider(lambda p: (
        b"abc", "application/json",
        {"content-type": "x/y", "Content-Length": "99", "X-Extra": "1"}))
    try:
        status, body, resp = _get(server, "/fixture-model/tower.glb")
        assert (status, body) == (200, b"abc")
        assert resp.msg.get_all("Content-Type") == ["application/json"]
        assert resp.msg.get_all("Content-Length") == ["3"]
        assert resp.getheader("X-Extra") == "1"
    finally:
        server.stop()
