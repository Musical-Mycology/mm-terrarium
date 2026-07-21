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
