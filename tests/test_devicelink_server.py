"""DeviceLinkServer: real localhost sockets, drain-based tick-thread API."""

import json
import time

import pytest
from websockets.sync.client import connect

from devicelink.server import DeviceLinkServer


@pytest.fixture
def server():
    srv = DeviceLinkServer(host="127.0.0.1", port=0)
    srv.start()
    yield srv
    srv.stop()


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    return None


def test_binds_an_ephemeral_port(server):
    assert server.port > 0


def test_new_client_is_drained_once(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws"):
        assert _wait_for(server.drain_new_clients) is not None
    assert server.drain_new_clients() == []


def test_inbound_json_is_drained(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
        client.send(json.dumps({"address": "/game/hello"}))
        inbound = _wait_for(server.drain_inbound)
    assert inbound is not None
    _, msg = inbound[0]
    assert msg == {"address": "/game/hello"}


def test_non_json_frame_is_dropped_not_raised(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
        client.send("not json at all")
        client.send(json.dumps({"address": "/game/join"}))
        inbound = _wait_for(server.drain_inbound)
    assert inbound is not None
    assert [m for _, m in inbound] == [{"address": "/game/join"}]


def test_broadcast_reaches_every_client(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws") as a, \
         connect(f"ws://127.0.0.1:{server.port}/ws") as b:
        _wait_for(lambda: len(server.drain_new_clients()) or None)
        time.sleep(0.1)
        server.broadcast({"address": "/ie1/release"})
        assert json.loads(a.recv(timeout=2))["address"] == "/ie1/release"
        assert json.loads(b.recv(timeout=2))["address"] == "/ie1/release"


def test_send_to_a_dead_client_does_not_raise(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws"):
        client = _wait_for(server.drain_new_clients)[0]
    time.sleep(0.1)
    server.send(client, {"address": "/ie1/role"})   # must not raise
