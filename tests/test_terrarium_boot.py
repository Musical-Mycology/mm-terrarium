from bits.test_bit import TestBit
from control.arco_process import FakePopen
from control.audio import AudioBridge, FakePool
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.rooms import RoomType
from control.state import State
from harness.terrarium_boot import build, shutdown


def _fake_arco(command, popen=None):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=popen or FakePopen(), probe=lambda: True)


def _fake_room_audio():
    return AudioBridge(FakePool())


def _build_with_fakes(config):
    """Shared fake-injecting build() call for tests that don't need to
    inspect a specific fake's recorded calls afterward (contrast the tests
    below, which construct their own FakePopen so they can assert on it
    post-shutdown)."""
    return build(
        config, {"TestBit": TestBit},
        arco_command=["arco-server"], room_binding=RoomBindingRegistry(),
        host="127.0.0.1", port=0, arco_process_cls=_fake_arco,
        simulator_popen=FakePopen(), room_audio=_fake_room_audio())


def test_build_wires_devicelink_room_bridge_and_simulator():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, simulator = _build_with_fakes(config)

    assert gs.room.bound_dev == "sim-room"
    assert agent._room_light is not None
    assert server.port != 0   # devicelink server actually bound before boot() ran

    shutdown(gs, agent, arco, simulator)


def test_devicelink_server_starts_before_boot_spawns_the_simulator():
    """The whole point of building devicelink first (see design spec section
    6): by the time boot()'s simulator_factory spawns the subprocess, the
    server it needs to connect to already exists. Assert the ordering
    directly via the fake simulator Popen's recorded launch args."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    sim_popen = FakePopen()

    gs, server, agent, arco, simulator = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=sim_popen,
        room_audio=_fake_room_audio())

    launched_command = sim_popen.commands[0]
    assert f"ws://127.0.0.1:{server.port}/ws" in launched_command
    shutdown(gs, agent, arco, simulator)


def test_shutdown_tears_down_arco_and_simulator():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    fake_arco_popen = FakePopen()
    sim_popen = FakePopen()

    gs, server, agent, arco, simulator = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=fake_arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio())
    gs.run()

    shutdown(gs, agent, arco, simulator)

    assert gs.state == State.IDLE
    assert fake_arco_popen.signals
    assert sim_popen.signals


def test_build_passes_the_configured_horizon_to_the_agent():
    """The horizon lives in one place. An agent built with its own default
    would silently disagree with the audio path's scheduling."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit",
                        cue_horizon=0.075)
    gs, server, agent, arco, sim = _build_with_fakes(config)
    try:
        assert agent._horizon == 0.075
    finally:
        shutdown(gs, agent, arco, sim)
