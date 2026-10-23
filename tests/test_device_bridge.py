"""DeviceBridge: the in-process stand-in for a device consuming /ie<N>/role.
Turns a granted JoinResult's composed config blob into a luxaeterna session and
maps GameServer release -> session.clear()."""

from __future__ import annotations

import pytest

pytest.importorskip("luxaeterna.backends.websim")

from bits.test.test_bit import TestBit
from control.engine import GameServer
from harness.device_bridge import DeviceBridge
from tests.helpers_admit import admit, admit_running


def _granted_join():
    gs = GameServer({"test_bit": TestBit})
    gs.load_bit("test_bit")
    grants = []
    gs.on_grant = lambda dev, result: grants.append(result)
    # The composed grant (config blob) exists only at start (spec 3.5).
    admit_running(gs, "dev1", "TEST_PLAYER_NODE")
    res = grants[0]
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
    frame = uni.get_frame()[:48]
    assert max(frame) > 0                                 # aurora lit without a note
    assert max(frame[1::4]) > max(frame[0::4])            # green-dominant (hue 0.33)

    session.feed_midi(0xB0, 74, 0)                        # cc:74=0 -> hue glides toward red
    for _ in range(60):
        session.render_into(uni)
    frame = uni.get_frame()[:48]
    assert max(frame) > 0
    assert max(frame[0::4]) > max(frame[1::4])            # RGBW: red > green, after the glide settles


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


def test_on_grant_rejects_a_denied_join():
    gs = GameServer({"test_bit": TestBit})          # no load_bit -> not accepting
    res = admit(gs, "dev1", "TEST_PLAYER_NODE")
    assert res.granted is False
    assert res.reason == "registration closed"
    with pytest.raises(ValueError):
        DeviceBridge().on_grant(res)


def test_zoned_capability_pads_the_pixels_a_role_leaves_unnamed():
    # A 4-light bar on the 12 px frame: luxaeterna refuses zones that do
    # not tile the surface ("gap at pixel 4") unless the rest is filled.
    from harness.device_bridge import zoned_capability
    cap = zoned_capability("devtower1", [(f"p{k}", k, 1) for k in range(4)])
    assert [(z.name, z.start, z.count) for z in cap.zones] == [
        ("p0", 0, 1), ("p1", 1, 1), ("p2", 2, 1), ("p3", 3, 1),
        ("unzoned4", 4, 8), ("primary", 0, 12)]
    gappy = zoned_capability("devtower1", [("a", 2, 3), ("b", 7, 1)])
    assert [(z.name, z.start, z.count) for z in gappy.zones
            if z.name.startswith("unzoned")] == [
        ("unzoned0", 0, 2), ("unzoned5", 5, 2), ("unzoned8", 8, 4)]


def test_zoned_capability_spans_a_wider_device_and_refuses_overflow():
    # A devtower's 32 px: the surface, its padding and `primary` follow
    # the device's pixel count, and a zone past it is refused.
    from harness.device_bridge import zoned_capability
    cap = zoned_capability("devtower1", [("p0", 0, 2), ("p1", 2, 2)], 32)
    assert cap.pixel_count == 32
    assert [(z.name, z.start, z.count) for z in cap.zones] == [
        ("p0", 0, 2), ("p1", 2, 2), ("unzoned4", 4, 28), ("primary", 0, 32)]
    with pytest.raises(ValueError, match="12 pixels"):
        zoned_capability("devtower1", [("p15", 30, 2)])
