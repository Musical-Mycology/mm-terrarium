"""Offline full-cycle integration pin: boot NO_ROOM -> Console-driven
load_room -> load_bit -> run -> complete -> unload_room -> load_room a
DIFFERENT room -> unload_room -> clean end. Drives Terrarium + GameServer +
ConsoleAgent together, through the same fake console-server pattern
tests/test_console_agent.py uses, so the room/bit commands travel the real
protocol.parse_command path rather than calling Terrarium/GameServer
directly. See docs/superpowers/specs/
2026-08-26-terrarium-lifecycle-and-config-rooms-design.md section 11.

This is a pin, not a probe for new behavior -- every step it drives is
already covered in isolation by tests/test_terrarium.py and
tests/test_console_agent.py. Its only job is to prove the pieces click
together end to end. It may pass on the first run; that is the point.
"""

import pytest

# devicelink.agent (pulled in below for the ambient leg) needs the sibling
# luxaeterna checkout -- same guard tests/test_devicelink_agent.py applies.
pytest.importorskip("luxaeterna")

from bits.test.test_bit import TestBit
from console.agent import ConsoleAgent
from control.boot_config import BootConfig
from control.engine import GameServer
from control.audio import AudioBridge, FakePool
from control.instrument import Instrument
from control.room_binding import RoomBindingRegistry
from control.room_profile import RoomBlock, RoomFixture, RoomProfile
from control.terrarium import Terrarium, TerrariumState
from control.terrarium_config import RoomSpec, TerrariumConfig
from devicelink.agent import DeviceLinkAgent
from luxaeterna.synth.manifest import LightManifest
from tests.test_console_agent import FakeConsoleServer
from tests.test_terrarium import DEMO_PROFILE, TEST_SPEC, FakeArco

# tests/test_terrarium.py's own DEMO_SPEC declares GENERIC_SURFACE (no
# ambient) for its fixture, since that file's tests are not about ambient
# rendering. This pin's ambient leg needs a Room whose fixture actually
# declares one, so it builds its own DEMO_SPEC over the SAME profile shape
# (one "array" fixture) with an ambient-declaring instrument instead --
# mirroring the shipped terrarium.toml's venue_array (aurora light + flsyn
# drone), the real-world case Task 7 covers (spec section 6).
_AMBIENT_ARRAY = Instrument(
    name="ambient_array",
    capabilities=frozenset({"light.surface", "audio.flsyn"}),
    accepted_triggers=("midi", "play", "solid", "mute"),
    light_manifest={"instruments": [{"instrument": "aurora",
                                     "target": "primary"}]},
    ugen_manifest={"instruments": [{"instrument": "flsyn", "program": 89,
                                    "drone": {"key": 48, "velocity": 80}}]},
)
_AMBIENT_DEMO_PROFILE = RoomProfile(surface_id="room_demo_ambient", fixtures=(
    RoomFixture(name="array", color_order="GRB",
               blocks=DEMO_PROFILE.fixtures[0].blocks,
               zones=DEMO_PROFILE.fixtures[0].zones,
               instrument=_AMBIENT_ARRAY),
))
DEMO_SPEC = RoomSpec(name="DEMO", description="",
                     backends=("devicelink", "array"),
                     node_id="ROOM_DEMO_NODE", profile=_AMBIENT_DEMO_PROFILE)


class _RoomWiring:
    """Test-local copy of harness.terrarium_boot._RoomWiring: keeps a
    DeviceLinkAgent's Room session in sync with Terrarium's load_room/
    unload_room, for every load AFTER agent construction -- exactly what a
    real NO_ROOM boot needs (see that class's own docstring). Not imported
    directly to keep this pin's only import from harness/ at zero, matching
    every other test in this file."""

    def __init__(self, agent, terrarium) -> None:
        self._agent = agent
        self._terrarium = terrarium

    def on_terrarium_state_change(self, old_state, new_state) -> None:
        if new_state is TerrariumState.ROOM_READY:
            self._agent.rewire_room(self._terrarium.room_bridge)
        elif new_state is TerrariumState.NO_ROOM:
            self._agent.unwire_room()


def make_cycle_terrarium():
    config = TerrariumConfig(schema=1, name="cycle-terrarium", bit_paths=(),
                             rooms={"TEST": TEST_SPEC, "DEMO": DEMO_SPEC},
                             version="cycle-test")
    gs = GameServer({"TestBit": TestBit})
    boot_config = BootConfig(room_name="TEST", bit_name="TestBit",
                             array_backend="simulator")
    FakeArco.instances = []
    return Terrarium(
        config, gs, RoomBindingRegistry(), boot_config=boot_config,
        arco_command=["arco-server"], arco_process_cls=FakeArco,
        simulator_factory=lambda td, fixture: f"sim-{fixture}-dev")


def test_full_offline_cycle_two_rooms_console_driven(monkeypatch):
    terrarium = make_cycle_terrarium()
    gs = terrarium.gs
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv, terrarium=terrarium)

    # A NO_ROOM-booted DeviceLinkAgent, wired the same way harness/
    # terrarium_boot.py's build() wires one, for the ambient leg below: the
    # room/bit swap this drives (Task 7, spec section 6) is Terrarium/
    # GameServer state this agent observes, not anything this test pokes
    # directly.
    from tests.test_devicelink_agent import FakeServer
    # room_audio wires a real AudioBridge (over a FakePool) so the ambient
    # leg below can assert on the voice-pool/drone state after unload_room --
    # final review's Important finding (unwire_room leaking the ambient
    # Room's audio grant) is only visible through this seam.
    audio_pool = FakePool()
    room_audio = AudioBridge(audio_pool)
    dl_agent = DeviceLinkAgent(gs, FakeServer(), room_audio=room_audio)
    terrarium.add_observer(_RoomWiring(dl_agent, terrarium))

    # Spy on every manifest _setup_room actually parses, so the ambient leg
    # can assert WHICH declaration reached the session factory (ambient's
    # aurora vs. TestBit's rainbow) -- same seam tests/test_devicelink_agent
    # .py's own ambient tests assert on.
    light_manifest_calls = []
    _orig_from_dict = LightManifest.from_dict

    def _spy_from_dict(d):
        manifest = _orig_from_dict(d)
        light_manifest_calls.append(manifest)
        return manifest

    monkeypatch.setattr(LightManifest, "from_dict", staticmethod(_spy_from_dict))

    def _last_instruments():
        return [decl.instrument
                for decl in light_manifest_calls[-1].instruments]

    # --- boot: roomless ---
    assert terrarium.state == TerrariumState.NO_ROOM
    assert gs.room is None

    # --- console: load_room TEST ---
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_room", "name": "TEST"})
    agent.poll()
    assert terrarium.state == TerrariumState.ROOM_READY
    assert terrarium.room.name == "TEST"
    assert not [m for _c, m in srv.sent if m.get("event") == "error"]
    first_arco = terrarium.arco
    assert isinstance(first_arco, FakeArco)
    # TEST's fixtures are dev_strip, which declares no ambient -- no Bit,
    # no session, today's unchanged behavior (Task 7, spec section 6).
    assert dl_agent._room_light is None

    # --- console: load_bit TestBit ---
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit"})
    agent.poll()
    assert gs.state.name == "SETUP"
    assert not [m for _c, m in srv.sent if m.get("event") == "error"]
    assert dl_agent._room_light is not None
    assert "rainbow" in _last_instruments()   # the Bit's own ROOM declaration

    # --- console: run ---
    srv.deliver("c1", {"command": "run"})
    agent.poll()
    assert gs.state.name == "RUNNING"

    # --- complete: TestBit's run duration elapses on the engine clock ---
    gs.tick(3.0)
    assert gs.state.name == "IDLE"
    assert gs.bit is None
    # Ambient is empty for TEST's fixtures, so the session goes back to
    # unbuilt once the Bit that declared it is gone.
    assert dl_agent._room_light is None
    # TestBit's default result() is None, so no bit_completed fires (see
    # tests/test_console_agent.py::test_bit_completed_is_broadcast_on_unload)
    # -- the state transition through COMPLETING/UNLOADING back to IDLE is
    # the round trip this pin cares about.
    unloading = [m for m in srv.broadcasts
                if m.get("event") == "state_changed" and m["state"] == "UNLOADING"]
    assert unloading

    # --- console: unload_room ---
    srv.deliver("c1", {"command": "unload_room"})
    agent.poll()
    assert terrarium.state == TerrariumState.NO_ROOM
    assert gs.room is None
    assert len(gs.devices) == 0
    unloaded = [m for m in srv.broadcasts if m.get("event") == "room_unloaded"]
    assert unloaded and unloaded[-1] == {"event": "room_unloaded", "name": "TEST"}

    # --- console: load_room DEMO -- a fresh FakeArco instance, not reused ---
    srv.deliver("c1", {"command": "load_room", "name": "DEMO"})
    agent.poll()
    assert terrarium.state == TerrariumState.ROOM_READY
    assert terrarium.room.name == "DEMO"
    second_arco = terrarium.arco
    assert isinstance(second_arco, FakeArco)
    assert second_arco is not first_arco
    assert len(FakeArco.instances) == 2
    # --- ambient leg (Task 7, spec section 6): DEMO's `array` fixture
    # declares venue_array's ambient aurora+drone, so with no Bit loaded the
    # Room renders that instead of going unbuilt. ---
    assert dl_agent._room_light is not None
    assert "aurora" in _last_instruments()
    # Ambient audio grants a voice and starts its drone the moment the Room
    # reaches ROOM_READY with no Bit loaded -- see devicelink/agent.py's
    # _setup_room(). This is the exact state final review flagged as
    # reachable by unload_room (gs.state == IDLE, no Bit) with the grant
    # still outstanding.
    ambient_voice = audio_pool.acquired[-1]
    assert any(call[0] == "note_on" for call in ambient_voice.sent)

    srv.deliver("c1", {"command": "load_bit", "name": "TestBit"})
    agent.poll()
    assert dl_agent._room_light is not None
    assert "rainbow" in _last_instruments()   # the Bit's declaration takes over

    srv.deliver("c1", {"command": "run"})
    agent.poll()
    assert gs.state.name == "RUNNING"

    gs.tick(3.0)   # TestBit's run duration elapses -> COMPLETING -> IDLE
    assert gs.state.name == "IDLE"
    assert gs.bit is None
    assert dl_agent._room_light is not None
    assert "aurora" in _last_instruments()   # ambient again once the Bit unloads
    # The ambient drone re-grants a FRESH voice on the swap back (mirrors
    # test_ambient_audio_swaps_to_the_bits_drone_at_load's pool-count check);
    # this is the voice unload_room below must release.
    reambient_voice = audio_pool.acquired[-1]
    assert reambient_voice is not ambient_voice
    assert any(call[0] == "note_on" for call in reambient_voice.sent)

    # --- console: unload_room -- clean end ---
    srv.deliver("c1", {"command": "unload_room"})
    agent.poll()
    assert terrarium.state == TerrariumState.NO_ROOM
    assert gs.room is None
    assert len(gs.devices) == 0
    assert terrarium.room_stack is None
    # Final review's Important finding, pinned: unload_room of an
    # ambient-audio Room must not leak the voice back to the pool or leave
    # its drone sounding.
    assert reambient_voice in audio_pool.released
    assert reambient_voice.sent[-1][0] in ("note_off", "all_off")
    assert first_arco.events == ["start", "wait_ready", "shutdown"]
    assert second_arco.events == ["start", "wait_ready", "shutdown"]
    unloaded = [m for m in srv.broadcasts if m.get("event") == "room_unloaded"]
    assert [m["name"] for m in unloaded] == ["TEST", "DEMO"]
