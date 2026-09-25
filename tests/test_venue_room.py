"""The VENUE Room: LED bars + three fiber-optic engines, two RGBW
fixtures on two WLED controllers (spec 2026-09-25)."""

import pathlib
import tomllib

import pytest

from bits.metronome.metronome_bit import MetronomeBit
from bits.test.test_bit import TestBit
from control.builtins import builtin_functions
from control.cues import fixture_dev
from control.engine import BitLoadError, GameServer
from control.functions import (Condition, ConditionSource, Function,
                               FunctionTarget, ScriptStep)
from control.role_config import slice_light_manifest
from control.room_binding import RoomBindingRegistry
from control.rooms import Room, room_role_name
from control.terrarium_config import (ArtNetOutput, _parse_artnet, _parse_psus,
                                      load_terrarium_config,
                                      validate_artnet_outputs,
                                      validate_psu_budgets)
from devicelink.agent import DeviceLinkAgent
from devicelink.artnet_sink import outputs_factory
from tests.artnet_fake import StrictFakeArtNet
from tests.test_devicelink_agent import FakeServer, _FakeOutput, _fake_sessions

CONFIG = load_terrarium_config("terrarium.toml")
VENUE = CONFIG.rooms["VENUE"].profile
# LEDs per fiber engine. Spec 2026-09-25 section 2, input I1: pending, so
# 1 until the hardware owner supplies it. rooms/VENUE.toml must agree.
N = 1


def test_venue_fiber_is_a_light_only_instrument():
    inst = CONFIG.instruments["venue_fiber"]
    assert inst.capabilities == frozenset({"light.surface"})
    assert set(builtin_functions(inst)) == {"flash", "stop"}


def test_venue_declares_bars_then_fiber_both_rgbw():
    assert [f.name for f in VENUE.fixtures] == ["bars", "fiber"]
    assert all(f.color_order == "RGBW" for f in VENUE.fixtures)
    assert CONFIG.rooms["VENUE"].node_id == "ROOM_VENUE_NODE"
    assert CONFIG.rooms["VENUE"].backends == ("devicelink", "array")


def test_venue_bars_match_the_demo_array():
    bars = VENUE.fixtures[0]
    (array,) = CONFIG.rooms["DEMO"].profile.fixtures
    assert bars.instrument.name == "venue_array"
    assert bars.pixel_count == 864
    assert bars.blocks == array.blocks
    assert bars.zones == array.zones


def test_venue_fiber_has_one_block_and_one_zone_per_engine():
    fiber = VENUE.fixtures[1]
    assert fiber.instrument.name == "venue_fiber"
    assert fiber.pixel_count == 3 * N
    assert [(b.name, b.start, b.count) for b in fiber.blocks] == [
        ("e1", 0, N), ("e2", N, N), ("e3", 2 * N, N)]
    assert [(z.name, z.start, z.count) for z in fiber.zones] == [
        ("b1", 0, N), ("b2", N, N), ("b3", 2 * N, N)]
    assert VENUE.channel_count == (864 + 3 * N) * 4


def _venue_room():
    return Room(name="VENUE", profile=VENUE, node_id="ROOM_VENUE_NODE")


def test_testbit_loads_on_venue_with_a_two_fixture_room_role():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = _venue_room()
    gs.load_bit("TestBit")
    role = gs.registration.role_table.roles[room_role_name("VENUE")]
    assert role.capacity == 2


def test_metronome_loads_on_venue():
    gs = GameServer({"MetronomeBit": MetronomeBit},
                    room_binding=RoomBindingRegistry())
    gs.room = _venue_room()
    gs.load_bit("MetronomeBit")


class _FiberBit(TestBit):
    """TestBit plus one ROOM function per VENUE fixture, addressed by name."""
    @property
    def function_table(self):
        table = super().function_table
        for name in ("bars", "fiber"):
            table.functions[f"{name}_pulse"] = Function(
                name=f"{name}_pulse", description="d", target=FunctionTarget.ROOM,
                condition=Condition(name="c", description="d",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, (fixture_dev(name), 0xB0, 74, 127)),))
        return table


def test_a_bit_addressing_both_venue_fixtures_loads_on_venue():
    gs = GameServer({"B": _FiberBit}, room_binding=RoomBindingRegistry())
    gs.room = _venue_room()
    gs.load_bit("B")


def test_a_bit_addressing_the_fiber_is_refused_on_demo():
    gs = GameServer({"B": _FiberBit}, room_binding=RoomBindingRegistry())
    gs.room = Room(name="DEMO", profile=CONFIG.rooms["DEMO"].profile,
                   node_id="ROOM_DEMO_NODE")
    with pytest.raises(BitLoadError, match="fiber"):
        gs.load_bit("B")


def test_a_fiber_bundle_target_binds_only_that_zone_on_the_fiber():
    """This slicing is what confines a fiber.b2 cue to the middle bundle
    (spec 2026-09-25 section 11, item 5)."""
    manifest = {"instruments": [
        {"instrument": "glow", "target": "fiber.b2", "params": {"hue": 0.6}}]}
    fiber = slice_light_manifest(manifest, VENUE, "fiber")
    bars = slice_light_manifest(manifest, VENUE, "bars")
    assert [d["target"] for d in fiber["instruments"]] == ["b2"]
    assert bars.get("instruments", []) == []


BEGIN = "# --- VENUE [[artnet]] example begin ---"
END = "# --- VENUE [[artnet]] example end ---"
# Loopback stand-ins for the committed placeholders. The amps are a worked
# example only (a 12.5 A PSU shared by both outputs), not a venue figure.
PLACEHOLDERS = {
    "<WLED controller IP>": "127.0.0.1",
    "<BARS_MAX_AMPS>": "9.4",
    "<FIBER_MAX_AMPS>": str(3 * N * 0.025),
    "<FIBER_AMPS_PER_PIXEL>": "0.025",
    "<PSU_RATED_AMPS>": "12.5",
}


def venue_example_toml() -> str:
    """terrarium.toml's commented VENUE block, uncommented, with every
    placeholder replaced by its loopback stand-in."""
    text = pathlib.Path("terrarium.toml").read_text(encoding="utf-8")
    block = text[text.index(BEGIN):text.index(END)].splitlines()[1:]
    body = "\n".join(line[2:] if line.startswith("# ") else line.lstrip("#")
                     for line in block)
    for placeholder, value in PLACEHOLDERS.items():
        body = body.replace(placeholder, value)
    return body


def test_the_committed_venue_example_validates_on_loopback():
    raw = tomllib.loads(venue_example_toml())
    outputs = _parse_artnet(raw["artnet"], source="example")
    assert [(o.fixture, o.port, o.start_universe, o.psu) for o in outputs] == [
        ("bars", 6454, 0, "led12v"), ("fiber", 6455, 0, "led12v")]
    validate_artnet_outputs(outputs, CONFIG.rooms, source="example")
    validate_psu_budgets(outputs, _parse_psus(raw["psus"], source="example"),
                         source="example")


def test_the_committed_venue_example_names_no_real_host():
    text = pathlib.Path("terrarium.toml").read_text(encoding="utf-8")
    block = text[text.index(BEGIN):text.index(END)]
    hosts = [line for line in block.splitlines() if "host =" in line]
    assert hosts and all("<WLED controller IP>" in line for line in hosts)


def test_venue_outputs_build_one_sink_per_fixture_on_its_own_port():
    outs = (ArtNetOutput(room="VENUE", fixture="bars", host="127.0.0.1",
                         max_amps=9.4),
            ArtNetOutput(room="VENUE", fixture="fiber", host="127.0.0.1",
                         max_amps=3 * N * 0.025, port=6455))
    made = {}

    def backend_cls(host, port):
        made[port] = StrictFakeArtNet(host, port)
        return made[port]

    built = outputs_factory(outs, clock=lambda: 100.0,
                            backend_cls=backend_cls)("VENUE", VENUE)
    assert sorted(built) == ["bars", "fiber"]
    (bars,), (fiber,) = built["bars"], built["fiber"]
    bars.send_frame(bytes(864 * 4), when=100.0)
    bars._service_once(100.0)
    fiber.send_frame(bytes(3 * N * 4), when=100.0)
    fiber._service_once(100.0)
    assert len(made[6454].sent) == 7      # 864 px / 128 px per RGBW universe
    assert len(made[6455].sent) == 1


def test_an_unbound_venue_renders_each_fixture_to_its_own_output(monkeypatch):
    """G2: no device bound, both fixtures still render and reach their
    outputs, each at its own width."""
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = _venue_room()
    gs.load_bit("TestBit")
    _fake_sessions(monkeypatch)
    bars_out, fiber_out = _FakeOutput(), _FakeOutput()
    agent = DeviceLinkAgent(
        gs, FakeServer(), clock=lambda: 100.0,
        outputs_for=lambda room, profile: {"bars": [bars_out], "fiber": [fiber_out]})
    assert bars_out.started == 1 and fiber_out.started == 1
    agent._render_room()
    assert [len(f) for f, _ in bars_out.frames] == [864 * 4]
    assert [len(f) for f, _ in fiber_out.frames] == [3 * N * 4]
