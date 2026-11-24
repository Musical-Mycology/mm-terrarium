# tests/test_websocket_transport.py
import json
import threading
import time

import pytest
from websockets.sync.server import serve

from uplink.transport import WebSocketTransport


@pytest.fixture
def echo_server():
    """A local websocket server bound to an OS-assigned localhost port.
    Records every message it receives and echoes it straight back.
    """
    received = []

    def handler(connection):
        for raw in connection:
            received.append(json.loads(raw))
            connection.send(raw)

    server = serve(handler, "localhost", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    try:
        yield f"ws://localhost:{port}", received
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_connect_sets_connected(echo_server):
    uri, _received = echo_server
    transport = WebSocketTransport(uri)

    transport.connect()

    assert transport.connected is True


def test_send_reaches_the_server(echo_server):
    uri, received = echo_server
    transport = WebSocketTransport(uri)
    transport.connect()

    transport.send({"command": "run"})

    # receive() is deliberately non-blocking (timeout=0, see the class
    # docstring) so it never blocks a caller's tick loop -- it won't
    # necessarily see the echo on the very first call, since that requires
    # a real network round trip. Poll it a few times, the way a real
    # tick-loop caller would, instead of asserting an instant reply.
    reply = None
    for _ in range(50):
        reply = transport.receive()
        if reply is not None:
            break
        time.sleep(0.01)

    assert reply == {"command": "run"}  # echoed back
    assert received == [{"command": "run"}]


def test_receive_returns_none_when_nothing_waiting(echo_server):
    uri, _received = echo_server
    transport = WebSocketTransport(uri)
    transport.connect()

    assert transport.receive() is None


def test_receive_returns_none_when_never_connected():
    transport = WebSocketTransport("ws://localhost:1")
    assert transport.receive() is None


def test_connect_raises_when_no_server_listening():
    transport = WebSocketTransport("ws://localhost:1")  # nothing bound there
    with pytest.raises(OSError):
        transport.connect()


def test_handshake_headers_reach_the_server():
    seen = {}

    def handler(connection):
        for name in ("X-Fairyring-Tenant", "X-Fairyring-Terrarium",
                     "X-Fairyring-Secret"):
            seen[name] = connection.request.headers.get(name)

    server = serve(handler, "localhost", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    try:
        transport = WebSocketTransport(
            f"ws://localhost:{port}",
            headers={"X-Fairyring-Tenant": "mm",
                     "X-Fairyring-Terrarium": "lobby",
                     "X-Fairyring-Secret": "s3cret"})
        transport.connect()
        for _ in range(100):
            if len(seen) == 3:
                break
            time.sleep(0.02)
        transport.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert seen == {"X-Fairyring-Tenant": "mm",
                    "X-Fairyring-Terrarium": "lobby",
                    "X-Fairyring-Secret": "s3cret"}


def test_close_disconnects_and_is_idempotent(echo_server):
    uri, _received = echo_server
    transport = WebSocketTransport(uri)
    transport.connect()

    transport.close()
    assert transport.connected is False
    transport.close()  # a second close must not raise
    assert transport.connected is False


def test_close_before_connect_does_not_raise():
    transport = WebSocketTransport("ws://localhost:1")
    transport.close()
    assert transport.connected is False


def test_close_swallows_errors_from_the_socket():
    class Boom:
        def close(self):
            raise OSError("boom")

    transport = WebSocketTransport("ws://localhost:1")
    transport._ws = Boom()
    transport.connected = True
    transport.close()
    assert transport.connected is False


def test_no_headers_still_connects(echo_server):
    uri, _received = echo_server
    transport = WebSocketTransport(uri, headers=None)
    transport.connect()
    assert transport.connected is True
    transport.close()


# --- final fix wave: bounded blocking calls (item 1) ------------------------

def test_open_and_close_timeouts_are_3_and_2_seconds():
    from uplink import transport as transport_module
    assert transport_module.OPEN_TIMEOUT_SECONDS == 3
    assert transport_module.CLOSE_TIMEOUT_SECONDS == 2


def test_connect_passes_both_timeouts_to_the_library(monkeypatch):
    from uplink import transport as transport_module
    seen = {}

    def fake_connect(uri, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(transport_module, "ws_connect", fake_connect)
    WebSocketTransport("ws://localhost:1", headers={"H": "v"}).connect()
    assert seen["open_timeout"] == 3
    assert seen["close_timeout"] == 2
    assert seen["additional_headers"] == {"H": "v"}


def test_connect_to_a_silent_listener_gives_up_within_the_open_timeout():
    import socket
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    accepted = []

    def accept():
        try:
            conn, _ = listener.accept()   # accept, then never answer
            accepted.append(conn)
        except OSError:
            pass

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    port = listener.getsockname()[1]
    transport = WebSocketTransport(f"ws://127.0.0.1:{port}")
    start = time.monotonic()
    try:
        with pytest.raises(Exception):
            transport.connect()
        elapsed = time.monotonic() - start
    finally:
        for conn in accepted:
            conn.close()
        listener.close()
        thread.join(timeout=1)
    assert elapsed < 5
    assert transport.connected is False


# --- final fix wave: a send that notices the close keeps buffered frames ----
# (item 7)

def _replacing_server():
    """Sends session_replaced as soon as the client connects, then closes."""
    def handler(connection):
        connection.send(json.dumps({"command": "session_replaced"}))
        connection.close()

    server = serve(handler, "localhost", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"ws://localhost:{server.socket.getsockname()[1]}"


def _send_until_it_raises(transport):
    from websockets.exceptions import ConnectionClosed
    for _ in range(100):
        try:
            transport.send({"event": "heartbeat"})
        except ConnectionClosed:
            return True
        time.sleep(0.02)
    return False


def test_receive_still_returns_a_frame_buffered_before_the_close():
    server, thread, uri = _replacing_server()
    try:
        transport = WebSocketTransport(uri)
        transport.connect()
        assert _send_until_it_raises(transport)
        assert transport.connected is False

        assert transport.receive() == {"command": "session_replaced"}
        assert transport.receive() is None
        assert transport._ws is None            # the old socket is let go
        assert transport.receive() is None
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_connect_starts_clean_after_a_closed_link_with_frames_left():
    server, thread, uri = _replacing_server()
    try:
        transport = WebSocketTransport(uri)
        transport.connect()
        assert _send_until_it_raises(transport)
        old = transport._ws

        transport.connect()                     # reconnect before draining
        assert transport.connected is True
        assert transport._ws is not old
        # Only the new connection's own frame arrives, once.
        got = None
        for _ in range(100):
            got = transport.receive()
            if got is not None:
                break
            time.sleep(0.02)
        assert got == {"command": "session_replaced"}
        transport.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)
