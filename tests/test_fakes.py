"""Unit tests for the configurable behaviors in tests/fakes.py itself --
not integration coverage (that's every call site that already uses these
doubles), just the branchy bits: poll counting, poll_error raising,
TickingGS's state flip after N ticks, and RecordingClient's call
recording / start_error."""
from control.state import State
from control.terrarium import TerrariumState
from tests.fakes import (FakeAgent, FakeArco, FakeClock, FakeObservable,
                         RecordingClient, RoomlessTerrarium, StaticGS,
                         TickingGS)


def test_fake_agent_counts_polls():
    agent = FakeAgent()
    agent.poll()
    agent.poll()
    assert agent.polls == 2
    assert agent.closing == 0


def test_fake_agent_poll_error_raises_the_given_instance():
    err = AssertionError("must not poll")
    agent = FakeAgent(poll_error=err)
    try:
        agent.poll()
        raise AssertionError("expected poll() to raise")
    except AssertionError as caught:
        assert caught is err
    assert agent.polls == 1


def test_fake_agent_closing_is_a_plain_settable_attribute():
    agent = FakeAgent(closing=2)
    assert agent.closing == 2
    agent.closing = 0
    assert agent.closing == 0


def test_fake_arco_returncode_none_is_alive_and_counts_polls():
    arco = FakeArco()
    assert arco.poll() is None
    assert arco.poll() is None
    assert arco.polls == 2


def test_fake_arco_returncode_reports_exited():
    arco = FakeArco(returncode=1)
    assert arco.poll() == 1


def test_fake_arco_poll_error_raises():
    err = RuntimeError("boom")
    arco = FakeArco(poll_error=err)
    try:
        arco.poll()
        raise AssertionError("expected poll() to raise")
    except RuntimeError as caught:
        assert caught is err


def test_static_gs_never_changes_state():
    gs = StaticGS(State.RUNNING)
    gs.tick(1.0)
    gs.tick(1.0)
    assert gs.state is State.RUNNING
    assert gs.ticks == 2


def test_static_gs_tick_error_raises():
    err = AssertionError("must not tick")
    gs = StaticGS(State.RUNNING, tick_error=err)
    try:
        gs.tick(1.0)
        raise AssertionError("expected tick() to raise")
    except AssertionError as caught:
        assert caught is err


def test_ticking_gs_flips_state_after_the_configured_count():
    gs = TickingGS(State.RUNNING, State.IDLE, after=3)
    gs.tick(1.0)
    assert gs.state is State.RUNNING
    gs.tick(1.0)
    assert gs.state is State.RUNNING
    gs.tick(1.0)
    assert gs.state is State.IDLE
    assert gs.ticks == 3


def test_ticking_gs_default_after_is_three():
    gs = TickingGS(State.IDLE, State.LOADED)
    for _ in range(2):
        gs.tick(1.0)
    assert gs.state is State.IDLE
    gs.tick(1.0)
    assert gs.state is State.LOADED


def test_roomless_terrarium_records_force_and_flips_to_no_room_by_default():
    terrarium = RoomlessTerrarium()
    assert terrarium.state is TerrariumState.ROOM_READY
    terrarium.unload_room(force=True)
    assert terrarium.unload_calls == [True]
    assert terrarium.state is TerrariumState.NO_ROOM


def test_roomless_terrarium_can_skip_the_state_flip():
    terrarium = RoomlessTerrarium(unload_to_no_room=False)
    terrarium.unload_room()
    assert terrarium.unload_calls == [False]
    assert terrarium.state is TerrariumState.ROOM_READY


def test_recording_client_pool_and_transport_share_one_call_order():
    calls = []
    pool = RecordingClient(calls, "pool")
    transport = RecordingClient(calls, "transport")
    transport.stop()
    pool.quiesce()
    pool.start()
    transport.start(object(), pump=None)
    assert calls[:3] == ["transport-stop", "pool-quiesce", "pool-start"]
    assert calls[3][0] == "transport-start"


def test_recording_client_start_error_raises_instead_of_recording():
    calls = []
    err = RuntimeError("injected pool failure")
    pool = RecordingClient(calls, "pool", start_error=err)
    try:
        pool.start()
        raise AssertionError("expected start() to raise")
    except RuntimeError as caught:
        assert caught is err
    assert calls == []


def test_fake_clock_advance_and_direct_mutation_both_move_now():
    clock = FakeClock(100.0)
    assert clock() == 100.0
    clock.advance(5.0)
    assert clock() == 105.0
    clock.now += 1.0
    assert clock() == 106.0


def test_fake_observable_add_observer_is_a_no_op():
    obs = FakeObservable()
    obs.add_observer(object())          # must not raise
    assert obs.state is TerrariumState.NO_ROOM
