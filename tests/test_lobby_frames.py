# tests/test_lobby_frames.py
"""The lobby's pixels, rendered by real luxaeterna sessions (spec 8)."""
import pytest

pytest.importorskip("luxaeterna")

from dataclasses import replace
from pathlib import Path

from bits.test.test_bit import TestBit
from control.bit_registry import BitRegistry
from control.engine import GameServer
from control.lobby import TERRARIUM_ADMIN
from control.rooms import Room
from control.terrarium_config import load_terrarium_config
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import FakeServer, _Clock

TEST_PROFILE = load_terrarium_config("terrarium.toml").rooms["TEST"].profile
TICK = 1.0 / 44.0


def _cfg():
    cfg = BitRegistry.scan([Path("bits")]).resolve_config("TestBit")
    return replace(cfg, start=replace(cfg.start, when="admin", key="k", min_scored=0))


def _rig():
    clk = _Clock(100.0)
    gs = GameServer({"TestBit": TestBit}, cue_horizon=0.06, clock=clk)
    gs.room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    gs.room.bound["main"] = "sim-main"
    gs.room.bound["accent"] = "sim-accent"
    frames = {}
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk,
                            on_room_frame=lambda name, f: frames.setdefault(name, []).append(bytes(f)))
    gs.load_bit("TestBit", config=_cfg())
    return gs, server, agent, clk, frames


def _run(agent, clk, seconds):
    for _ in range(int(seconds / TICK)):
        clk.advance(TICK)
        agent.poll()


def _hue_of(frame):
    g, r, b = frame[0], frame[1], frame[2]      # GRB order (Task 7 reorders overrides)
    return (r, g, b)


def test_waiting_aurora_moves_and_breathes_on_both_fixtures():
    gs, server, agent, clk, frames = _rig()
    _run(agent, clk, 6.0)
    for name in ("main", "accent"):
        colours = {_hue_of(f) for f in frames[name]}
        assert len(colours) > 10                      # drift plus breath
        levels = [max(f) for f in frames[name]]
        assert max(levels) - min(levels) > 40         # the breath is visible


def test_invite_paints_the_device_white_twice():
    gs, server, agent, clk, frames = _rig()
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    whites = []
    seen = 0
    for _ in range(int(1.0 / TICK)):
        clk.advance(TICK)
        agent.poll()
        for (_d, m) in server.sent[seen:]:
            if m["address"] == "/ie1/leds":
                whites.append(all(b >= 250 for b in bytes(m["args"][0])))
        seen = len(server.sent)
    # two runs of white frames with a non-white gap between
    runs = 0
    prev = False
    for w in whites:
        if w and not prev:
            runs += 1
        prev = w
    assert runs == 2


def test_accept_flashes_the_fixtures_green_then_the_bits_declaration_returns():
    gs, server, agent, clk, frames = _rig()
    _run(agent, clk, 0.5)
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    _run(agent, clk, 0.1)
    r, g, b = _hue_of(frames["main"][-1])
    assert g >= 250 and r < 10 and b < 10
    _run(agent, clk, 2.0)
    r, g, b = _hue_of(frames["main"][-1])
    assert not (g >= 250 and r < 10 and b < 10)       # flash expired, rainbow back


def test_minimum_refusal_flashes_red_twice():
    gs, server, agent, clk, frames = _rig()
    gs.bit.config = replace(gs.bit.config, start=replace(gs.bit.config.start, min_scored=1))
    _run(agent, clk, 0.5)
    gs.request_start("k", "ie7", "device:ie7")
    reds = []
    for _ in range(int(1.5 / TICK)):
        clk.advance(TICK)
        agent.poll()
        r, g, b = _hue_of(frames["main"][-1])
        reds.append(r >= 250 and g < 10 and b < 10)
    runs, prev = 0, False
    for x in reds:
        if x and not prev:
            runs += 1
        prev = x
    assert runs == 2
