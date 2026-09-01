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
from control.cues import ROOM
from control.engine import GameServer
from control.audio import AudioBridge, FakePool
from control.functions import Function, FunctionKind, GeneratorSpec
from control.generator_runner import GeneratorRunner
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
    accepted_cues=("midi", "play", "solid", "mute"),
    light_manifest={"instruments": [{"instrument": "aurora",
                                     "target": "primary"}]},
    ugen_manifest={"instruments": [{"instrument": "flsyn", "program": 89,
                                    "drone": {"key": 48, "velocity": 80}}]},
    # The shipped terrarium.toml instruments declare no generators (Task 5's
    # brief: shipped visuals unchanged) -- this test-local fixture DOES, so
    # the ambient leg below can prove the Room breathes on cc:74 before any
    # Bit is loaded (spec section 6/7), the same lane TestBit's own "drift"
    # generator later drives once a Bit takes the Room.
    functions=(Function(
        name="ambient_drift",
        description="Ambient hue drift across the Room with no Bit loaded",
        kind=FunctionKind.GENERATOR,
        generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                                waveform="triangle", period=12.0,
                                lo=0, hi=127)),),
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
    # A controllable clock, not time.monotonic: the ambient-generator leg
    # below needs to advance elapsed time deterministically across two
    # dl_agent.poll() calls and read back two DIFFERENT triangle-wave
    # values off the same cc:74 lane.
    clock_value = [0.0]
    dl_agent = DeviceLinkAgent(gs, FakeServer(), room_audio=room_audio,
                              clock=lambda: clock_value[0])
    # Same fake clock for GameServer's own `at` computations (generator/
    # script dispatch timestamps): the suppression-window arithmetic below
    # compares a fire's `at` against dl_agent's feed-now check, and both
    # sides need to agree on what time it is.
    gs._clock = lambda: clock_value[0]
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

    # --- ambient generator leg: the Room breathes on cc:74 before any Bit
    # is loaded, with no session poked directly -- two ticks of the
    # engine-owned ambient GeneratorRunner (spec section 6/7), read back
    # off terrarium.room_bridge, the same sink a Bit's own generator later
    # drives. ---
    room_bridge = terrarium.room_bridge
    clock_value[0] = 3.0
    dl_agent.poll()
    ambient_value_at_3 = room_bridge.controllers.get(74)
    clock_value[0] = 6.0
    dl_agent.poll()
    ambient_value_at_6 = room_bridge.controllers.get(74)
    assert ambient_value_at_3 is not None and ambient_value_at_6 is not None
    assert ambient_value_at_3 != ambient_value_at_6

    srv.deliver("c1", {"command": "load_bit", "name": "TestBit"})
    agent.poll()
    assert dl_agent._room_light is not None
    assert "rainbow" in _last_instruments()   # the Bit's declaration takes over
    # TestBit's own declared "drift" generator supersedes the ambient one --
    # _setup_room drops the ambient runner the moment a Bit's ROOM role is
    # composed (last-start-wins, spec section 7).
    assert dl_agent._ambient_generators is None

    srv.deliver("c1", {"command": "run"})
    agent.poll()
    assert gs.state.name == "RUNNING"

    # --- TestBit's declared generator drives cc:74 during RUNNING, engine-
    # dispatched (not through dl_agent.poll()): gs.tick() feeds it straight
    # through GameServer.on_light_cue into the same room_bridge. clock_value
    # is held at 6.0 (left where the ambient leg above last set it) so every
    # `at`/`when` below shares one deterministic instant until advanced. ---
    gs.tick(1.0)   # run_elapsed=1.0
    assert gs.state.name == "RUNNING"
    drift_spec = TestBit().function_table.functions["drift"].generator
    assert room_bridge.controllers.get(74) == GeneratorRunner.value(drift_spec, 1.0)

    # --- a play_aurora fire (bit-adjudicated in real play; fired manually
    # here as the same test-local shortcut test_engine_functions.py uses)
    # overlay-suppresses the drift lane for the script's span, then the
    # drift resumes once the window closes -- spec section 4/7's "overlay,
    # not kill". ---
    assert gs.fire_function("play_aurora", fired_by="admin-manual", dev=ROOM) is None
    # play_aurora's own offset-0.0 step (value 127) is due immediately
    # (when == now == 6.0) and lands synchronously on the same lane.
    assert room_bridge.controllers.get(74) == 127
    gs.tick(0.1)   # run_elapsed=1.1, at=6.0 -- still inside the 2.0s span
    # The generator's own next tick would emit a different value here; it
    # does not, because the lane is suppressed until at(6.0) + span(2.0).
    assert room_bridge.controllers.get(74) == 127
    gs.tick(0.05)   # run_elapsed=1.15, at=6.0 -- still suppressed
    assert room_bridge.controllers.get(74) == 127

    # Advance the shared clock past the suppression window (8.0) and drain
    # play_aurora's two remaining deferred script steps (due at 6.5 and 8.0)
    # through dl_agent.poll(), the same way a real device link would.
    clock_value[0] = 9.0
    dl_agent.poll()
    assert room_bridge.controllers.get(74) == 0   # play_aurora's last step
    gs.tick(0.05)   # run_elapsed=1.20, at=9.0 -- past the window, drift resumes
    assert room_bridge.controllers.get(74) == GeneratorRunner.value(drift_spec, 1.20)

    # --- a joined device's blob carries `triggers`: TUNESHROOM (the default
    # carried instrument gs.join() grants when none is declared) ships its
    # own event-trigger thresholds in the composed config (control/
    # role_config.py's compose_role_config, Task 8/spec section 5). The
    # jammer role is requires-less and unscored (registration for the
    # scored "player" role is already closed mid-run) -- exactly the case
    # the docstring calls out: thresholds ship for every granted non-ROOM
    # join, slot requirement or not. ---
    join_result = gs.join("jammer_dev", "TEST_JAM_NODE")
    assert join_result.granted
    assert join_result.config["triggers"] == {
        "tap": {"peak_g": 2.0, "window_ms": 200, "double_ms": 400},
        "shake": {"peak_g": 2.0, "window_ms": 200},
    }
    # ...and the instrument section (Task 3) ships the same carried
    # instrument's full definition under config["instrument"].
    assert join_result.config["instrument"]["name"] == "tuneshroom"

    gs.tick(3.0)   # TestBit's run duration elapses -> COMPLETING -> IDLE
    assert gs.state.name == "IDLE"
    assert gs.bit is None
    assert dl_agent._room_light is not None
    assert "aurora" in _last_instruments()   # ambient again once the Bit unloads
    # --- ambient animation returns once the Bit unloads: two more ticks,
    # two more distinct values, off the SAME lane the Bit's drift just
    # supplied (spec section 7's resume-at-unload guarantee). ---
    clock_value[0] = 20.0
    dl_agent.poll()
    ambient_value_at_20 = room_bridge.controllers.get(74)
    clock_value[0] = 23.0
    dl_agent.poll()
    ambient_value_at_23 = room_bridge.controllers.get(74)
    assert ambient_value_at_20 is not None and ambient_value_at_23 is not None
    assert ambient_value_at_20 != ambient_value_at_23
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
