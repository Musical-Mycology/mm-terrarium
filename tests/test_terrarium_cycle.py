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

from bits.test.test_bit import TestBit
from console.agent import ConsoleAgent
from control.boot_config import BootConfig
from control.engine import GameServer
from control.room_binding import RoomBindingRegistry
from control.terrarium import Terrarium, TerrariumState
from control.terrarium_config import TerrariumConfig
from tests.test_console_agent import FakeConsoleServer
from tests.test_terrarium import DEMO_SPEC, TEST_SPEC, FakeArco


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


def test_full_offline_cycle_two_rooms_console_driven():
    terrarium = make_cycle_terrarium()
    gs = terrarium.gs
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv, terrarium=terrarium)

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

    # --- console: load_bit TestBit ---
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit"})
    agent.poll()
    assert gs.state.name == "SETUP"
    assert not [m for _c, m in srv.sent if m.get("event") == "error"]

    # --- console: run ---
    srv.deliver("c1", {"command": "run"})
    agent.poll()
    assert gs.state.name == "RUNNING"

    # --- complete: TestBit's run duration elapses on the engine clock ---
    gs.tick(3.0)
    assert gs.state.name == "IDLE"
    assert gs.bit is None
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

    # --- console: unload_room -- clean end ---
    srv.deliver("c1", {"command": "unload_room"})
    agent.poll()
    assert terrarium.state == TerrariumState.NO_ROOM
    assert gs.room is None
    assert len(gs.devices) == 0
    assert terrarium.room_stack is None
    assert first_arco.events == ["start", "wait_ready", "shutdown"]
    assert second_arco.events == ["start", "wait_ready", "shutdown"]
    unloaded = [m for m in srv.broadcasts if m.get("event") == "room_unloaded"]
    assert [m["name"] for m in unloaded] == ["TEST", "DEMO"]
