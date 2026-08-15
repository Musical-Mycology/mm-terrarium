"""control/teardown.py's TeardownStack: the one mechanism all three
teardown paths get their ordering from."""
from __future__ import annotations

import pytest

from control.teardown import TeardownStack


def test_steps_unwind_in_reverse_push_order():
    """The whole point: anything started later stops earlier. On the o2lite
    path the Room simulator is spawned after Arco, so it must be signalled
    before Arco dies, or it spends its last moments on a dead socket."""
    order = []
    stack = TeardownStack()
    stack.push("arco", lambda: order.append("arco"))
    stack.push("simulator", lambda: order.append("simulator"))

    stack.close()

    assert order == ["simulator", "arco"]


def test_a_raising_step_does_not_skip_the_steps_below_it():
    """PR #24 wrote this guarantee out by hand at four call sites, each with
    its own try/except: pass and a comment saying 'never let cleanup mask
    the real failure'. Here it is once, structurally."""
    order = []
    stack = TeardownStack()
    stack.push("arco", lambda: order.append("arco"))
    stack.push("simulator", _raise(OSError("no such process")))

    failures = stack.close()

    assert order == ["arco"]
    assert [name for name, _exc in failures] == ["simulator"]
    assert isinstance(failures[0][1], OSError)


def test_a_keyboard_interrupt_in_a_step_is_captured_not_propagated():
    """BaseException, not Exception. ArcoProcess.shutdown() waits on a
    subprocess, and a second Ctrl-C landing inside that wait would otherwise
    replace a well-labeled BootFailure with a bare KeyboardInterrupt and
    abandon every step below it. PR #24 paid for this lesson twice."""
    order = []
    stack = TeardownStack()
    stack.push("arco", lambda: order.append("arco"))
    stack.push("simulator", _raise(KeyboardInterrupt()))

    failures = stack.close()

    assert order == ["arco"]
    assert isinstance(failures[0][1], KeyboardInterrupt)


def test_close_is_idempotent():
    """boot()'s failure path and the caller's normal teardown both close the
    same stack without coordinating. A second close must not re-run a step."""
    calls = []
    stack = TeardownStack()
    stack.push("arco", lambda: calls.append(1))

    stack.close()
    stack.close()

    assert calls == [1]


def test_close_returns_no_failures_when_every_step_succeeds():
    stack = TeardownStack()
    stack.push("arco", lambda: None)

    assert stack.close() == []


def test_pushing_after_close_is_a_programming_error():
    """A step registered after the stack has unwound would never run, and
    silently never running is how a subprocess gets orphaned. Say so."""
    stack = TeardownStack()
    stack.close()

    with pytest.raises(RuntimeError, match="closed"):
        stack.push("late", lambda: None)


def test_an_empty_stack_closes_cleanly():
    assert TeardownStack().close() == []


def _raise(exc):
    def step():
        raise exc
    return step
