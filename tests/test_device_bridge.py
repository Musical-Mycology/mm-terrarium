"""DeviceBridge: the in-process stand-in for a device consuming /ie<N>/role.
Turns a granted JoinResult's composed config blob into a luxaeterna session and
maps GameServer release -> session.clear()."""

from __future__ import annotations

import pytest

pytest.importorskip("luxaeterna.backends.websim")

from bits.test_bit import TestBit
from control.engine import GameServer
from harness.device_bridge import DeviceBridge


def _granted_join():
    gs = GameServer({"test_bit": TestBit})
    gs.load_bit("test_bit")
    res = gs.join("dev1", "TEST_PLAYER_NODE")
    assert res.granted
    return res


def test_on_grant_builds_a_session_that_lights_from_the_composed_blob():
    from luxaeterna.universe import Universe
    clk = iter([i * (1 / 44) for i in range(400)]).__next__
    bridge = DeviceBridge(clock=clk)
    session = bridge.on_grant(_granted_join())
    assert session is not None

    uni = Universe()
    for _ in range(200):
        session.render_into(uni)
        if session.state == "running":
            break
    assert session.state == "running"
    session.render_into(uni)
    assert max(uni.get_frame()[:36]) == 0                 # dark before note

    session.feed_midi(0xB0, 74, 0)                        # cc:74=0 -> hue red
    session.feed_midi(0x90, 60, 100)                      # note-on
    session.render_into(uni)
    frame = uni.get_frame()[:36]
    assert max(frame) > 0
    assert max(frame[1::3]) > max(frame[0::3])            # GRB: red > green


def test_on_release_requests_close():
    from luxaeterna.universe import Universe
    clk = iter([i * (1 / 44) for i in range(400)]).__next__
    bridge = DeviceBridge(clock=clk)
    session = bridge.on_grant(_granted_join())
    uni = Universe()
    for _ in range(200):
        session.render_into(uni)
        if session.state == "running":
            break
    bridge.on_release("dev1")
    session.render_into(uni)
    assert session.state == "closing"


def test_on_release_is_safe_before_any_grant():
    DeviceBridge().on_release("dev1")                     # must not raise
