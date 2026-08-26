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
            assert snap["installed_bits"] == ["TestBit"]

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
