"""DeviceLinkAgent and the beat heartbeat (spec 2026-10-08 sections 4 and
7), against the in-process FakeServer from test_devicelink_agent."""

import re

import pytest

pytest.importorskip("luxaeterna")

from bits.test.test_bit import TestBit
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import (FakeServer, _Clock, _hello, _join)


def _rig(epoch="abc123"):
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, epoch=epoch)
    gs.load_bit("test_bit")
    return gs, server, agent, clk


def _beat(server, agent, seq, rtt=None, client="c1", dev="ie1"):
    args = [dev, seq] if rtt is None else [dev, seq, rtt]
    server.deliver(client, "/game/beat", "si" if rtt is None else "sii", args)
    agent.poll()


def test_default_epoch_is_six_hex():
    gs = GameServer({"test_bit": TestBit})
    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 0.0)
    assert re.fullmatch(r"[0-9a-f]{6}", agent.epoch)


def test_each_beat_is_answered_with_its_seq_and_the_epoch():
    gs, server, agent, clk = _rig()
    _hello(server, agent)
    for seq in (0, 1, 2):
        _beat(server, agent, seq)
    replies = server.addressed("/ie1/beat")
    assert [m["args"] for m in replies] == [[0, "abc123"], [1, "abc123"],
                                            [2, "abc123"]]
    assert all(m["typespec"] == "is" for m in replies)


def test_the_rtt_form_is_answered_too():
    gs, server, agent, clk = _rig()
    _hello(server, agent)
    _beat(server, agent, 0, rtt=18)
    assert server.addressed("/ie1/beat")[-1]["args"] == [0, "abc123"]
    assert agent.link_view()["ie1"]["rtt_ms"] == 18


def test_a_beat_before_hello_is_dropped():
    gs, server, agent, clk = _rig()
    server.arrive("c1")
    _beat(server, agent, 0)
    assert server.addressed("/ie1/beat") == []
    assert gs.devices.known("ie1") is False


def test_a_malformed_beat_is_dropped():
    gs, server, agent, clk = _rig()
    _hello(server, agent)
    server.deliver("c1", "/game/beat", "ss", ["ie1", "zero"])
    agent.poll()
    assert server.addressed("/ie1/beat") == []


def test_a_beat_keeps_the_device_alive_past_the_reap():
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.load_bit("test_bit")
    _hello(server, agent)
    for seq in range(1, 15):
        clk.advance(1.0)
        _beat(server, agent, seq)
    assert gs.devices.known("ie1") is True


def test_link_view_live_then_missing_and_none_for_legacy():
    gs, server, agent, clk = _rig()
    _hello(server, agent, dev="ie1")
    _hello(server, agent, client="c2", dev="ie2")   # legacy: never beats
    _beat(server, agent, 0)
    assert agent.link_view()["ie1"]["state"] == "live"
    assert "ie2" not in agent.link_view()
    clk.advance(3.5)
    agent.poll()
    assert agent.link_view()["ie1"]["state"] == "missing"


def test_a_legacy_repeated_hello_still_gets_one_room_and_nothing_else():
    gs, server, agent, clk = _rig()
    for _ in range(3):
        _hello(server, agent)
    assert len(server.addressed("/ie1/room")) == 1
    assert server.addressed("/ie1/beat") == []


def test_relink_of_a_beating_role_holder_resends_its_frame_and_no_role():
    gs, server, agent, clk = _rig()
    _hello(server, agent)
    _beat(server, agent, 0)
    _join(server, agent, gs)
    for _ in range(50):                      # let the role's frame settle
        clk.advance(1.0 / 44.0)
        agent.poll()
    leds_before = len(server.addressed("/ie1/leds"))
    roles_before = len(server.addressed("/ie1/role"))
    rooms_before = len(server.addressed("/ie1/room"))
    clk.advance(1.0 / 44.0)
    agent.poll()
    assert len(server.addressed("/ie1/leds")) == leds_before  # static look

    _hello(server, agent)                    # relink: hello on the new link
    _beat(server, agent, 0)

    assert len(server.addressed("/ie1/leds")) == leds_before + 1
    assert len(server.addressed("/ie1/role")) == roles_before
    assert len(server.addressed("/ie1/room")) == rooms_before


def test_a_link_state_change_notifies_the_console():
    gs, server, agent, clk = _rig()
    calls = []
    gs.notify_devices_changed = lambda: calls.append(clk.t)
    _hello(server, agent)
    _beat(server, agent, 0)
    _beat(server, agent, 1)                  # two beats: bars are known
    clk.advance(1.0)
    agent.poll()                             # first check: live, 4 bars
    n = len(calls)
    assert n >= 1
    clk.advance(1.0)
    agent.poll()                             # unchanged: no new notify
    assert len(calls) == n
    clk.advance(3.0)
    agent.poll()                             # 5 s silent: missing
    assert len(calls) == n + 1


def test_reaping_forgets_the_link():
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.load_bit("test_bit")
    _hello(server, agent)
    _beat(server, agent, 0)
    clk.advance(11.0)
    agent.poll()
    assert gs.devices.known("ie1") is False
    assert agent.link_view() == {}
    _hello(server, agent)                    # back as a new device
    assert len(server.addressed("/ie1/room")) == 2   # first contact again
