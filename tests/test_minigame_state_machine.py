"""bits/minigame/state_machine.py: hook order, requested transitions and
their timing, and the refusals."""
import pytest

from bits.minigame.state_machine import (
    MAX_CHAINED_TRANSITIONS,
    State,
    StateMachine,
)


class Recorder(State):
    """Logs every hook as (name, hook, at) and returns it as its output."""

    def __init__(self, name, log, on_tick=None, on_enter=None):
        super().__init__()
        self.name = name
        self.log = log
        self._tick = on_tick
        self._enter = on_enter

    def _note(self, hook, at):
        entry = (self.name, hook, at)
        self.log.append(entry)
        return [entry]

    def on_enter(self, at):
        out = self._note("enter", at)
        if self._enter:
            self._enter(self, at)
        return out

    def on_tick(self, at):
        out = self._note("tick", at)
        if self._tick:
            self._tick(self, at)
        return out

    def on_exit(self, at):
        return self._note("exit", at)


def _machine(**hooks):
    log = []
    states = [Recorder(n, log, **hooks.get(n, {})) for n in ("A", "B", "C")]
    return StateMachine(states, initial="A"), log


def test_start_enters_the_initial_state():
    sm, _ = _machine()
    assert sm.state is None
    assert sm.start(1.0) == [("A", "enter", 1.0)]
    assert sm.state == "A"


def test_transition_exits_then_enters_and_returns_both_outputs_in_order():
    sm, _ = _machine()
    sm.start()
    assert sm.transition("B", 2.0) == [("A", "exit", 2.0), ("B", "enter", 2.0)]
    assert sm.state == "B"


def test_reentering_the_current_state_runs_exit_and_enter_again():
    sm, _ = _machine()
    sm.start()
    assert sm.transition("A", 3.0) == [("A", "exit", 3.0), ("A", "enter", 3.0)]


def test_tick_runs_only_the_current_state_and_nothing_before_start():
    sm, log = _machine()
    assert sm.tick(1.0) == []
    sm.start()
    assert sm.tick(1.0) == [("A", "tick", 1.0)]
    assert [e for e in log if e[1] == "tick"] == [("A", "tick", 1.0)]


def test_a_request_from_on_tick_runs_after_the_hook_at_the_requested_time():
    sm, _ = _machine(A={"on_tick": lambda s, at: s.request("B", at=at - 0.5)})
    sm.start()
    assert sm.tick(5.0) == [("A", "tick", 5.0),
                            ("A", "exit", 4.5), ("B", "enter", 4.5)]
    assert sm.state == "B"


def test_a_request_without_a_time_takes_the_tick_s():
    sm, _ = _machine(A={"on_tick": lambda s, at: s.request("B")})
    sm.start()
    assert sm.tick(5.0)[-1] == ("B", "enter", 5.0)


def test_a_request_from_on_enter_chains():
    sm, _ = _machine(B={"on_enter": lambda s, at: s.request("C")})
    sm.start()
    assert sm.transition("B", 1.0) == [
        ("A", "exit", 1.0), ("B", "enter", 1.0),
        ("B", "exit", 1.0), ("C", "enter", 1.0)]
    assert sm.state == "C"


def test_start_restarts_without_exiting_the_old_state():
    sm, _ = _machine()
    sm.start()
    sm.transition("B")
    assert sm.start(9.0) == [("A", "enter", 9.0)]


def test_a_transition_loop_is_stopped():
    sm, _ = _machine(A={"on_enter": lambda s, at: s.request("B")},
                     B={"on_enter": lambda s, at: s.request("A")})
    with pytest.raises(RuntimeError, match=str(MAX_CHAINED_TRANSITIONS)):
        sm.start()


def test_unknown_names_and_bad_tables_are_refused():
    sm, _ = _machine()
    with pytest.raises(ValueError, match="no state 'Z'"):
        sm.transition("Z")
    with pytest.raises(ValueError, match="no state 'Z'"):
        sm.request("Z")
    log = []
    with pytest.raises(ValueError, match="duplicate"):
        StateMachine([Recorder("A", log), Recorder("A", log)], initial="A")
    with pytest.raises(ValueError, match="initial"):
        StateMachine([Recorder("A", log)], initial="B")
    with pytest.raises(ValueError, match="no name"):
        StateMachine([State()], initial="")
