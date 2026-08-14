# Teardown Order and Stack Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every o2lite client stop and be reaped before the hub it talks to, structurally rather than by convention, then build a single command that runs and tears down the whole Arco stack.

**Architecture:** A guarded LIFO `TeardownStack` replaces three separately-maintained teardown orderings with one mechanism, so "started later, stopped earlier" is a consequence of startup order rather than a list anyone can reorder. A shared `stop_process` gives every spawned subprocess a bounded SIGTERM-then-SIGKILL-and-reap cycle. A shared SIGTERM handler makes each process handle the signal it is actually sent. `harness/run_stack.py` then supervises `terrarium_boot` plus N `o2_shroom` devices on top of that primitive.

**Tech Stack:** Python 3, pytest, `subprocess`, `pty`, `signal`, `threading`. No new third-party dependencies.

Spec: [`docs/superpowers/specs/2026-08-14-teardown-order-and-stack-runner-design.md`](../specs/2026-08-14-teardown-order-and-stack-runner-design.md)

## Global Constraints

- **Run the suite through the project venv.** There is no bare `python`, and the luxaeterna dev dependency is installed only in `.venv`. The command is:
  `PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q`
- **Baseline is 662 passed, 1 skipped** at commit `141e8af`. The suite must stay green and never drop below that count.
- **The suite stays fully offline.** No O2, no Arco server, no pyarco, no o2litepy. No module under `control/` may import `o2litepy`. `control/audio.py` never imports pyarco.
- **Boundary rule 5:** a test double must never be more permissive than the library it stands for. When a double stands in for something outside this repo, encode the strictness as well as the shape: what the real thing refuses, when it dispatches, and what it requires you to call.
- **Boundary rule 4:** an in-process consumer is reached by a Python method call, not by O2. `game` and `actl` stay inbound-only.
- **No em dashes in prose.** The repo's existing docstrings use `--`; match the surrounding file.
- Commit after every task. Conventional-commit subjects, `feat(terrarium):` / `fix(terrarium):` / `docs(terrarium):`.

---

## File Structure

**Create:**

| File | Responsibility |
| --- | --- |
| `control/teardown.py` | `TeardownStack`: guarded, idempotent, LIFO teardown |
| `control/process.py` | `stop_process`: bounded signal, escalate, reap |
| `harness/signals.py` | `sigterm_as_keyboard_interrupt`: one copy of the SIGTERM gotcha |
| `harness/markers.py` | readiness and failure marker strings, the runner's contract |
| `harness/proc_tee.py` | `ProcTee`: per-child log file, prefixed echo, marker events |
| `harness/run_stack.py` | the supervisor: spawn, wait, run, tear down, report |
| `docs/upstream/2026-08-14-o2-service-and-discovery-report.md` | the upstream report for Roger |
| `tests/test_teardown.py`, `tests/test_process.py`, `tests/test_signals.py`, `tests/test_markers.py`, `tests/test_proc_tee.py`, `tests/test_run_stack.py` | their tests |

**Modify:**

| File | Change |
| --- | --- |
| `control/arco_process.py` | `FakePopen` up to `Popen` strictness; `_PtyProcess.close()`; escalation moves out; `pty_popen(log_path=)`; bounded `output` |
| `control/simulator_process.py` | `shutdown()` delegates to `stop_process` |
| `control/boot.py` | `boot(teardown=)`, returns the stack, pushes its steps; `shutdown()` and `_shutdown_simulator()` deleted; `simulator_factory(teardown)` |
| `harness/terrarium_boot.py` | `build()` returns the stack; `shutdown(teardown)`; `_NullRoomBridge` deleted; factories register themselves; `-u`; `--arco-log`; signal handler; markers |
| `harness/o2_shroom.py`, `harness/room_simulator.py`, `harness/led_smoke.py` | use `harness/signals.py` |
| `tests/test_arco_process.py`, `tests/test_arco_pty.py`, `tests/test_simulator_process.py`, `tests/test_boot.py`, `tests/test_terrarium_boot.py` | follow the signature and behavior changes |
| `.gitignore` | `runs/` |
| `docs/MM_TERRARIUM.md` | deep-dive sync |

---

## Task 1: `stop_process`, and `FakePopen` brought up to `Popen`'s strictness

**Why first:** `stop_process` is the primitive every later teardown step uses, and its SIGKILL-escalation branch is untestable until `FakePopen` can model a child that refuses to die. The two ship together.

**Files:**
- Create: `control/process.py`
- Create: `tests/test_process.py`
- Modify: `control/arco_process.py:36-53` (`FakePopen`)
- Modify: `tests/test_arco_process.py:51-59`, `tests/test_simulator_process.py:14-22`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `control.process.stop_process(process, *, sig=signal.SIGTERM, timeout=5.0, kill_timeout=5.0, clock=time.monotonic, sleep=time.sleep) -> int | None`
  - `control.arco_process.FakePopen(*, ignores=())` with `poll()`, `send_signal(sig)`, `wait(timeout=None)`, and attributes `commands: list[list[str]]`, `signals: list[int]`, `kwargs: dict`, `returncode: int | None`, `waited: bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_process.py`:

```python
"""control/process.py's stop_process: the bounded signal/escalate/reap
cycle every spawned subprocess in this repo now shares."""
from __future__ import annotations

import signal

from control.arco_process import FakePopen
from control.process import stop_process


def _started(popen: FakePopen) -> FakePopen:
    """FakePopen is both the factory and the process, like the real seam."""
    return popen(["some-command"])


def test_sigterm_is_enough_for_a_well_behaved_child():
    popen = FakePopen()
    process = _started(popen)

    assert stop_process(process) is not None
    assert popen.signals == [signal.SIGTERM]


def test_an_already_exited_child_is_not_signalled_again():
    """Teardown runs unconditionally; a child that already exited must not
    turn it into a second signal or an error."""
    popen = FakePopen()
    process = _started(popen)
    process.send_signal(signal.SIGTERM)
    popen.signals.clear()

    assert stop_process(process) is not None
    assert popen.signals == []


def test_a_child_that_ignores_sigterm_is_killed_and_reaped():
    """The whole reason this function exists. A venue box restarting into a
    still-running Arco cannot bind its ports, and an orphaned o2lite client
    re-claims its dev name on the next hub, so teardown must escalate rather
    than return with the child alive."""
    popen = FakePopen(ignores=(signal.SIGTERM,))
    process = _started(popen)

    code = stop_process(process, timeout=0.05, kill_timeout=0.05,
                        sleep=lambda _s: None)

    assert popen.signals == [signal.SIGTERM, signal.SIGKILL]
    assert code is not None
    assert process.poll() is not None


def test_an_unkillable_child_returns_none_rather_than_hanging():
    """A child stuck in uninterruptible sleep is the one real way SIGKILL
    fails to take effect promptly. Bounded means bounded: report it and let
    the caller carry on tearing the rest down."""
    popen = FakePopen(ignores=(signal.SIGTERM, signal.SIGKILL))
    process = _started(popen)

    code = stop_process(process, timeout=0.05, kill_timeout=0.05,
                        sleep=lambda _s: None)

    assert code is None
    assert popen.signals == [signal.SIGTERM, signal.SIGKILL]


def test_the_clock_and_sleep_are_injectable_so_tests_spend_no_real_time():
    ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0, 20.0])
    slept = []
    popen = FakePopen(ignores=(signal.SIGTERM, signal.SIGKILL))
    process = _started(popen)

    stop_process(process, timeout=5.0, kill_timeout=5.0,
                 clock=lambda: next(ticks), sleep=slept.append)

    assert slept                       # it did loop
    assert all(s > 0 for s in slept)   # and it would have slept for real
```

Add to `tests/test_arco_process.py` (a new block; the existing tests stay):

```python
def test_fake_popen_poll_is_none_until_the_child_exits():
    """Boundary rule 5: Popen.poll() returns None while the child runs and
    its exit code after. A double whose poll() always returned a code would
    let stop_process's wait loop terminate instantly in every test, so the
    bounded-wait path would never actually be exercised."""
    popen = FakePopen()
    process = popen(["cmd"])

    assert process.poll() is None
    process.send_signal(signal.SIGTERM)
    assert process.poll() is not None


def test_fake_popen_wait_raises_timeout_expired_while_the_child_lives():
    """Popen.wait(timeout=...) raises TimeoutExpired rather than returning.
    A double that returned instead would let a caller believe it had reaped
    a process that never died."""
    import subprocess

    popen = FakePopen()
    process = popen(["cmd"])

    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=0.01)


def test_fake_popen_send_signal_after_exit_is_a_no_op():
    """Popen.send_signal checks returncode first and does nothing if the
    child is gone."""
    popen = FakePopen()
    process = popen(["cmd"])
    process.send_signal(signal.SIGTERM)
    popen.signals.clear()

    process.send_signal(signal.SIGTERM)
    assert popen.signals == []


def test_fake_popen_records_the_keyword_arguments_it_was_given():
    """harness/run_stack.py spawns with stdout=, stderr= and
    start_new_session=True, and the ordering tests assert on them."""
    popen = FakePopen()
    popen(["cmd"], start_new_session=True)

    assert popen.kwargs["start_new_session"] is True
```

`tests/test_arco_process.py` needs `import pytest` if it does not already have it.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_process.py tests/test_arco_process.py -q
```

Expected: FAIL, `ModuleNotFoundError: No module named 'control.process'` and `AttributeError`/`TypeError` on `FakePopen`.

- [ ] **Step 3: Write `control/process.py`**

```python
"""stop_process: the bounded signal/escalate/reap cycle every spawned
subprocess in this repo shares.

Before this existed, ArcoProcess.shutdown() and SimulatorProcess.shutdown()
both sent SIGTERM and then called self._process.wait() with NO timeout. A
plain subprocess.Popen.wait() blocks forever, and the Room simulator is
always a plain Popen, so one client that ignored or was slow to handle its
stop signal hung teardown indefinitely. control/arco_process.py's
_PtyProcess.wait() had the right discipline and was the only place that did;
this is that discipline, factored out.

Why it polls in a loop rather than calling Popen.wait(timeout=...): a
_PtyProcess DRAINS ITS PTY inside poll(). Arco is a curses app redrawing
continuously, so an undrained pty buffer fills and blocks the server on its
own screen writes. A poll loop is the one shape that serves both a plain
Popen and the pty; Popen.wait() would starve the drain.
"""

from __future__ import annotations

import signal
import time


def stop_process(process, *, sig=signal.SIGTERM, timeout: float = 5.0,
                 kill_timeout: float = 5.0, clock=time.monotonic,
                 sleep=time.sleep):
    """Stop `process` and reap it. Returns its exit code, or None if it
    survived even SIGKILL.

    `process` needs only the two-method slice poll()/send_signal() that both
    subprocess.Popen and control/arco_process.py's _PtyProcess offer. It is
    deliberately NOT given the fd-closing responsibility: a plain Popen has
    no fd of its own, so the caller closes what it owns.

    None is a real return value, not a failure to report: a child stuck in
    uninterruptible sleep is the one way SIGKILL does not take effect
    promptly, and bounded has to mean bounded. The caller carries on tearing
    the rest down and says so.
    """
    code = process.poll()
    if code is not None:
        return code                     # already gone; do not signal a corpse
    process.send_signal(sig)
    code = _wait_bounded(process, timeout, clock, sleep)
    if code is not None:
        return code
    process.send_signal(signal.SIGKILL)
    # SIGKILL is delivered asynchronously: polling immediately can still see
    # the child unreaped and would report "still running" for a process that
    # is already dying.
    return _wait_bounded(process, kill_timeout, clock, sleep)


def _wait_bounded(process, timeout: float, clock, sleep):
    """Poll until the child exits or the budget runs out. Polls BEFORE
    checking the deadline, so a zero timeout still gives the child one look
    rather than none."""
    deadline = clock() + timeout
    while True:
        code = process.poll()
        if code is not None:
            return code
        if clock() >= deadline:
            return None
        sleep(0.02)
```

- [ ] **Step 4: Bring `FakePopen` up to `Popen`'s strictness**

Replace `control/arco_process.py:36-53` entirely:

```python
class FakePopen:
    """In-process test double for subprocess.Popen, sibling of
    control/audio.py's FakeVoice/FakePool.

    BOUNDARY RULE 5 applies here with force: this must never be more
    permissive than Popen, because control/process.py's stop_process exists
    precisely to handle the case where a child does NOT do as it is told.
    What that means concretely:

      * poll() returns None while the child runs and its exit code after.
        A double whose poll() always answered would let stop_process's wait
        loop terminate instantly in every test, so the bounded-wait path
        would never be exercised at all.
      * wait(timeout=...) RAISES subprocess.TimeoutExpired while the child
        is alive, exactly as Popen does. A double that returned instead
        would let a caller believe it had reaped a process that never died.
      * send_signal on an exited child is a no-op, as Popen.send_signal is
        (it checks returncode first).
      * `ignores` models a child that does not die on a signal. Without it,
        the SIGKILL escalation has no coverage and this double would agree
        with a test that never runs the real risk. SIGKILL may be listed
        too: that models a child in uninterruptible sleep, the one real way
        SIGKILL fails to take effect promptly.
    """

    def __init__(self, *, ignores=()) -> None:
        self.commands: list[list[str]] = []
        self.kwargs: dict = {}
        self.signals: list[int] = []
        self.waited = False
        self.returncode = None
        self._ignores = set(ignores)

    def __call__(self, command: list[str], **kwargs):
        self.commands.append(command)
        self.kwargs = kwargs
        return self

    def poll(self):
        return self.returncode

    def send_signal(self, sig: int) -> None:
        if self.returncode is not None:
            return                       # Popen.send_signal no-ops after exit
        self.signals.append(sig)
        if sig not in self._ignores:
            self.returncode = -sig

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            raise subprocess.TimeoutExpired(
                self.commands[-1] if self.commands else "fake", timeout)
        return self.returncode
```

`subprocess` is already imported at `control/arco_process.py:15`.

- [ ] **Step 5: Run the whole suite**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
```

Expected: PASS, no fewer than 662 passed.

Note that `tests/test_arco_process.py`'s and `tests/test_simulator_process.py`'s existing `test_shutdown_sends_sigterm_and_waits` still pass **unchanged** here, and must. `shutdown()` still calls `wait()` at this point, and the new `send_signal` sets `returncode` before it, so `wait()` returns cleanly and `popen.waited` is still `True`. Those two tests change in Task 3, which is where `shutdown()` stops calling `wait()`. Do not touch them in this task.

- [ ] **Step 6: Commit**

```bash
git add control/process.py tests/test_process.py control/arco_process.py tests/test_arco_process.py
git commit -m "feat(terrarium): bounded stop_process, and a FakePopen that can refuse to die

Teardown could hang forever: ArcoProcess.shutdown() and
SimulatorProcess.shutdown() both send SIGTERM then call Popen.wait() with
no timeout, and the Room simulator is always a plain Popen.

stop_process is _PtyProcess.wait()'s discipline factored out: signal, poll
to a bound, escalate to SIGKILL, poll again, reap. It polls rather than
calling Popen.wait(timeout=) because _PtyProcess drains its pty inside
poll(), and Arco blocks on its own screen writes if that drain starves.

FakePopen had no poll() at all and a wait() taking no arguments, so under
boundary rule 5 it was more permissive than Popen in exactly the dimension
this code exists for. It now models poll(), TimeoutExpired, the no-op
send_signal after exit, and -- the point -- a child that ignores a signal,
without which the escalation branch has no coverage.

No caller changes yet, so the suite stays green: shutdown() still calls
wait(), and send_signal sets returncode before it."
```

---

## Task 2: `TeardownStack`

**Files:**
- Create: `control/teardown.py`
- Create: `tests/test_teardown.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `control.teardown.TeardownStack` with `push(name: str, fn: Callable[[], None]) -> None` and `close() -> list[tuple[str, BaseException]]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_teardown.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_teardown.py -q
```

Expected: FAIL, `ModuleNotFoundError: No module named 'control.teardown'`.

- [ ] **Step 3: Write `control/teardown.py`**

```python
"""TeardownStack: a guarded, idempotent, LIFO stack of named teardown steps.

WHY THIS EXISTS. mm-terrarium had three separately-maintained teardown
orderings: control/boot.py's failure handler, harness/terrarium_boot.py's
build() failure handler, and harness/terrarium_boot.py's success-path
shutdown(). PR #24 corrected the first two and did not notice the third, so
on a normal, successful run the O2 hub was still being killed before the
o2lite clients that talk to it. Three lists that have to agree by hand is
the defect; a mechanism they all share is the fix.

THE INVARIANT, and the one thing to know before editing:

    Anything registered LATER is torn down EARLIER.

That is what makes client-before-hub structural. The devicelink server is
started before boot() and pushed first, so it stops last. Arco is spawned
next. The Room simulator is spawned after Arco and therefore stops before
it. An o2lite transport adopted after boot() returns stops before all of
them. Nobody maintains that order; it falls out of when things start.

Push order is DELIBERATE, not literally creation order. control/boot.py
creates Arco, then the GameServer, then the RoomBridge, but the Bit must
abort before the room bridge it may still cue into during on_unload -- so
boot() pushes the bridge step and then the Bit step, both after Arco. Push
points are chosen and documented at each call site.

WHY NOT contextlib.ExitStack. It unwinds LIFO and does continue past a
failing callback, but it re-raises the LAST exception and merely chains the
others as __context__, and it has no notion of step names. Teardown here
needs every failure, named, with the original boot failure staying primary.
"""

from __future__ import annotations

from typing import Callable


class TeardownStack:
    """Registered steps run in reverse order, each one guarded, once."""

    def __init__(self) -> None:
        self._steps: list[tuple[str, Callable[[], None]]] = []
        self._closed = False

    def push(self, name: str, fn: Callable[[], None]) -> None:
        """Register a teardown step. `name` appears in the failure report,
        so make it the thing an operator would look for in a log: "arco",
        "simulator", "devicelink-server"."""
        if self._closed:
            raise RuntimeError(
                f"cannot push {name!r}: this TeardownStack is already closed, "
                f"so the step would never run and whatever it owns would be "
                f"orphaned silently")
        self._steps.append((name, fn))

    def close(self) -> list[tuple[str, BaseException]]:
        """Unwind every step in reverse push order and return the failures.

        Returns rather than raises: the caller nearly always has a more
        important exception in flight (the BootFailure that triggered
        teardown), and cleanup must never mask it.

        Catches BaseException per step, which means a KeyboardInterrupt
        raised INSIDE a step is captured rather than propagating. That is
        deliberate: a second Ctrl-C during teardown must not abandon the
        remaining steps and orphan a subprocess. Teardown is bounded now
        (control/process.py's stop_process), so completing it is safe.

        Idempotent. A second call is a no-op and returns an empty list, so
        boot()'s failure path and the caller's normal teardown can both call
        it without coordinating.
        """
        if self._closed:
            return []
        self._closed = True
        failures: list[tuple[str, BaseException]] = []
        while self._steps:
            name, fn = self._steps.pop()
            try:
                fn()
            except BaseException as exc:   # noqa: BLE001 (guarded by design)
                failures.append((name, exc))
        return failures
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_teardown.py -q
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add control/teardown.py tests/test_teardown.py
git commit -m "feat(terrarium): a guarded LIFO TeardownStack

Three separately-maintained teardown orderings is the actual defect behind
the hub-dies-before-its-clients bug: PR #24 corrected two of them days ago
and the third went unnoticed. One mechanism they all share is the fix, and
its invariant is that anything registered later is torn down earlier, so
client-before-hub falls out of when things start rather than being a list
someone maintains.

Guarded per step so a failing teardown can neither skip the steps below it
nor mask the failure that triggered it -- the guarantee PR #24 wrote out
longhand at four call sites. BaseException rather than Exception, because
a Ctrl-C landing inside a subprocess wait was leaking both subprocesses
before #24 and must not abandon the remaining steps now. Idempotent so the
boot failure path and the caller's normal teardown can both close it."
```

---

## Task 3: wire `stop_process` into the two process owners

**Files:**
- Modify: `control/arco_process.py:100-192` (`_PtyProcess`), `:222-227` (`ArcoProcess.shutdown`)
- Modify: `control/simulator_process.py:24-29`
- Modify: `tests/test_arco_pty.py:60-67`

**Interfaces:**
- Consumes: `control.process.stop_process` (Task 1).
- Produces: `_PtyProcess.close() -> None`; `ArcoProcess.shutdown()` and `SimulatorProcess.shutdown()` unchanged in signature, now bounded.

- [ ] **Step 1: Update the two tests that assert on the old `wait()` behavior**

`stop_process` polls rather than calling `wait()`, so `popen.waited` is no longer set by a shutdown. In `tests/test_arco_process.py`, replace the `assert popen.waited is True` on line 59 with:

```python
    assert popen.returncode is not None      # signalled AND reaped
```

Make the identical replacement in `tests/test_simulator_process.py` line 22. Rename both tests from `test_shutdown_sends_sigterm_and_waits` to `test_shutdown_sends_sigterm_and_reaps`.

- [ ] **Step 2: Move the real-pty escalation test onto `stop_process`**

In `tests/test_arco_pty.py`, replace `test_wait_escalates_to_sigkill_when_sigterm_is_ignored` (lines 60-67) with:

```python
def test_stop_process_escalates_to_sigkill_on_a_real_pty_child():
    """Escalation now lives in control/process.py, but it is worth keeping
    one test of it against a REAL child that really ignores SIGTERM rather
    than only against FakePopen. A venue box restarting into a still-running
    Arco cannot bind its ports, so teardown must not return with the child
    alive."""
    from control.process import stop_process

    proc = pty_popen(["/bin/sh", "-c", "trap '' TERM; sleep 30"])
    time.sleep(0.3)

    assert stop_process(proc, timeout=1.0, kill_timeout=5.0) is not None
    assert proc.poll() is not None
    proc.close()
```

- [ ] **Step 3: Run it to verify it fails**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_arco_pty.py -q
```

Expected: FAIL, `AttributeError: '_PtyProcess' object has no attribute 'close'`.

- [ ] **Step 4: Give `_PtyProcess` a `close()` and take the escalation out of `wait()`**

Replace `control/arco_process.py`'s `_PtyProcess.wait` (lines 167-192) with:

```python
    def close(self) -> None:
        """Close the pty master fd.

        Separate from wait() because control/process.py's stop_process owns
        the signal/escalate/reap cycle now and deliberately does not touch
        fds: a plain subprocess.Popen has none of its own, so the owner
        closes what it owns. Idempotent.
        """
        import os
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def wait(self, timeout: float = 5.0):
        """Bounded wait, then close. Kept for the Popen-compatible surface
        and for tests that just want a child reaped; ESCALATION MOVED OUT to
        control/process.py's stop_process, which is what ArcoProcess.
        shutdown() and SimulatorProcess.shutdown() both use now. Returns the
        exit code, or None if the child outlived the timeout."""
        import time as _time
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if self.poll() is not None:
                break
            _time.sleep(0.05)
        self.close()
        return self.returncode
```

Also guard `_drain` and `send_signal` against the closed fd. In `_drain`, change the first line of the loop body region to return early:

```python
    def _drain(self) -> None:
        import os
        import select
        if self._fd is None:
            return
        while True:
```

And update the class docstring's first line from `(poll / send_signal / wait)` to `(poll / send_signal / wait / close)`.

- [ ] **Step 5: Point `ArcoProcess.shutdown()` at `stop_process`**

Replace `control/arco_process.py:222-227`:

```python
    def shutdown(self) -> None:
        """SIGTERM, then SIGKILL if that is ignored, then reap.

        Arco has no message-based quit (arco/doc/server.md documents only a
        console keypress), so a signal is the only lever. Bounded via
        control/process.py: an unbounded wait() here used to mean one
        wedged server hung the whole teardown.
        """
        if self._process is None:
            return
        process, self._process = self._process, None
        try:
            stop_process(process)
        finally:
            close = getattr(process, "close", None)
            if close is not None:
                close()          # _PtyProcess owns a pty master; Popen does not
```

Add the import at the top of `control/arco_process.py`, next to the existing `import signal`:

```python
from control.process import stop_process
```

- [ ] **Step 6: Point `SimulatorProcess.shutdown()` at `stop_process`**

Replace `control/simulator_process.py:24-29`:

```python
    def shutdown(self) -> None:
        """SIGTERM, then SIGKILL if that is ignored, then reap.

        Bounded via control/process.py. The simulator is ALWAYS a plain
        subprocess.Popen, whose wait() has no timeout, so before this the
        one client most likely to be slow on its way out could hang teardown
        forever.
        """
        if self._process is None:
            return
        process, self._process = self._process, None
        stop_process(process)
```

And add to its imports:

```python
from control.process import stop_process
```

`signal` is no longer referenced in `control/simulator_process.py`; remove `import signal`.

- [ ] **Step 7: Run the tests**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_arco_pty.py tests/test_arco_process.py tests/test_simulator_process.py tests/test_process.py -q
```

Expected: all PASS, including the two renamed `test_shutdown_sends_sigterm_and_reaps` tests left failing by Task 1.

- [ ] **Step 8: Run the whole suite**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
```

Expected: PASS, no fewer than 662 passed.

- [ ] **Step 9: Commit**

```bash
git add control/arco_process.py control/simulator_process.py tests/test_arco_pty.py tests/test_arco_process.py tests/test_simulator_process.py
git commit -m "fix(terrarium): teardown cannot hang on a child that ignores SIGTERM

ArcoProcess.shutdown() and SimulatorProcess.shutdown() both sent SIGTERM
and then called Popen.wait() with no timeout. The Room simulator is always
a plain Popen, so a client slow or unwilling to die hung teardown forever.
Both now delegate to control/process.py's stop_process, which bounds the
wait and escalates.

_PtyProcess keeps its drain-aware poll(), gains an explicit close() for the
pty master (stop_process deliberately owns no fds, since a plain Popen has
none), and loses the private escalation logic it was the only holder of.
The real-pty ignores-SIGTERM test moves with it rather than being dropped:
FakePopen coverage is not a substitute for a child that genuinely refuses."
```

---

## Task 4: `control/boot.py` on the stack

**Files:**
- Modify: `control/boot.py` (whole file)
- Modify: `tests/test_boot.py` (every `boot(...)` call site, plus the two `shutdown` tests, plus PR #24's six cases)

**Interfaces:**
- Consumes: `control.teardown.TeardownStack` (Task 2).
- Produces:
  - `boot(config, bit_registry, *, arco_command, room_binding, arco_process_cls=ArcoProcess, simulator_factory=None, known_device_connected=..., tick=None, teardown=None) -> tuple[GameServer, RoomBridge, ArcoProcess, TeardownStack]`
  - `simulator_factory` contract is now `Callable[[TeardownStack], str]`
  - `control.boot.shutdown` and `control.boot._shutdown_simulator` are **deleted**

- [ ] **Step 1: Write the failing tests**

In `tests/test_boot.py`, replace the `_SpyFactory` class (PR #24's, at line ~221) with one matching the new contract, and add the ordering test. The `_SpyProcess` class above it is unchanged.

```python
class _SpyFactory:
    """A simulator_factory that SPAWNS. The contract is now
    Callable[[TeardownStack], str]: a factory that spawns a process
    registers its own teardown on the stack it is handed. That replaces
    PR #24's getattr(factory, "process", None) convention, which existed
    only because the factory had no way to hand its handle back."""

    def __init__(self):
        self.process = None

    def __call__(self, teardown):
        self.process = _SpyProcess()
        teardown.push("simulator", self.process.shutdown)
        return "sim-room-dev"


def test_teardown_stops_the_simulator_before_arco():
    """THE regression this slice exists for. control/boot.py's own
    shutdown() used to end with arco.shutdown(), which is right within this
    module's scope and wrong composed with a caller that owns o2lite client
    subprocesses: the hub died first and the clients spent their last
    moments on a dead socket."""
    order = []

    class _RecordingProcess:
        def shutdown(self):
            order.append("simulator")

    class _RecordingFactory:
        def __call__(self, teardown):
            teardown.push("simulator", _RecordingProcess().shutdown)
            return "sim-room-dev"

    class _RecordingArco(ArcoProcess):
        def shutdown(self):
            order.append("arco")

    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        arco_process_cls=lambda cmd: _RecordingArco(
            cmd, popen=FakePopen(), probe=lambda: True),
        simulator_factory=_RecordingFactory())

    teardown.close()

    assert order == ["simulator", "arco"]


def test_teardown_aborts_the_bit_before_the_room_bridge():
    """Deliberate push order, not creation order: the Bit's on_unload may
    still cue into the room bridge, so the bridge must not die first."""
    order = []
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), arco_process_cls=_ready_arco,
        simulator_factory=lambda td: "sim-room-dev")

    gs.run()
    gs.abort = lambda: order.append("bit")
    room_bridge.shutdown = lambda: order.append("room-bridge")

    teardown.close()

    assert order == ["bit", "room-bridge"]


def test_a_caller_supplied_stack_gets_boots_steps_pushed_onto_it():
    """harness/terrarium_boot.py starts the devicelink server BEFORE boot()
    (the simulator must have something to connect to), so it needs to
    register that server on the same stack first and have it torn down
    last. Passing the stack in is what makes the LIFO invariant hold across
    the boundary between the two modules."""
    order = []
    teardown = TeardownStack()
    teardown.push("devicelink-server", lambda: order.append("server"))

    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco, returned = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        arco_process_cls=lambda cmd: _ready_arco(cmd),
        simulator_factory=lambda td: "sim-room-dev",
        teardown=teardown)

    assert returned is teardown
    returned.close()
    assert order[-1] == "server"      # started first, therefore stopped last
```

Add `from control.teardown import TeardownStack` to the imports, and drop `shutdown` from the `control.boot` import list.

Update **every existing** `boot(...)` call in the file to unpack four values, and every bare `simulator_factory=lambda: "sim-room-dev"` to `simulator_factory=lambda td: "sim-room-dev"`. Replace the two existing shutdown tests (`test_shutdown_aborts_a_running_bit_then_tears_down` at line 155 and `test_shutdown_on_already_idle_server_does_not_raise` at line 171) with versions that call `teardown.close()` instead of `shutdown(gs, room_bridge, arco)`, keeping their assertions.

PR #24's six failure-path tests keep their names and assertions. Only their factory call signature changes, via `_SpyFactory` above; `test_boot_still_accepts_a_factory_that_spawns_nothing` becomes `simulator_factory=lambda td: "sim-room-dev"` and asserts no exception, and `test_boot_shuts_arco_down_even_if_the_simulator_shutdown_raises`'s `_RaisingFactory` gains the `teardown` parameter and pushes `_RaisingProcess().shutdown`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_boot.py -q
```

Expected: FAIL, `ValueError: not enough values to unpack (expected 4, got 3)` and `TypeError: <lambda>() takes 0 positional arguments but 1 was given`.

- [ ] **Step 3: Rewrite `boot()`**

Replace `control/boot.py` lines 27-123 with:

```python
def boot(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, arco_process_cls=ArcoProcess,
         simulator_factory=None, known_device_connected=lambda dev: False,
         tick=None, teardown=None):
    """Run the full load sequence. Returns (game_server, room_bridge,
    arco_process, teardown) once the Bit is loaded and either the Room is
    already bound (fast path) or a fresh tap has bound it.

    TEARDOWN IS THE RETURNED STACK. There is no boot.shutdown() any more:
    the caller closes the stack, and every step this function registered
    unwinds in reverse. That deleted function's docstring used to say "Arco
    last since everything else may still want to address it during
    teardown", which was true within this module's scope and wrong composed
    with harness/terrarium_boot.py, which owns o2lite CLIENT subprocesses
    that talk to that hub. Reverse-of-registration gets it right in both
    scopes without either having to know about the other.

    Push order here is deliberate, and unwinds as: the Bit, then the Room
    bridge (which frees the Room's Arco voice), then any simulator
    subprocess, then Arco. The Bit goes before the bridge because its
    on_unload may still cue into it.

    `teardown` lets a caller that started something BEFORE boot() register
    it first and have it torn down last. harness/terrarium_boot.py starts
    its DeviceLinkServer before calling boot(), deliberately, because the
    simulator this function spawns connects immediately.

    `simulator_factory` is Callable[[TeardownStack], str]: a factory that
    spawns a process registers its own teardown on the stack it is handed.

    Raises BootFailure on any stage failure. Once Arco has actually
    started, EVERY failure -- wait_ready timing out, an unknown or
    unsupported Bit, a Bit load error, a Ctrl-C, or anything unanticipated
    -- closes the stack before propagating, so nothing this function
    started is orphaned and no cleanup exception masks the real one.
    """
    if teardown is None:
        teardown = TeardownStack()

    try:
        room_type = resolve_room_type(
            config.room_type,
            array_backend_configured=config.array_backend_configured)
    except RoomResolutionError as exc:
        raise BootFailure(str(exc)) from exc
    room = Room(room_type=room_type)

    arco = arco_process_cls(arco_command)
    try:
        arco.start()
    except Exception as exc:
        # Nothing was actually spawned, so there's nothing to shut down.
        raise BootFailure(f"Arco failed to start: {exc}") from exc
    teardown.push("arco", arco.shutdown)

    try:
        try:
            arco.wait_ready(config.arco_ready_timeout)
        except Exception as exc:
            raise BootFailure(f"Arco failed to start: {exc}") from exc

        _bind_room_fast_path(room, room_binding, simulator_factory,
                             known_device_connected, teardown)

        bit_cls = bit_registry.get(config.bit_name)
        if bit_cls is None:
            raise BootFailure(f"unknown Bit {config.bit_name!r}")
        if room.room_type not in bit_cls.room_types:
            raise BootFailure(
                f"Bit {config.bit_name!r} does not support {room.room_type.name}")

        gs = GameServer(bit_registry, room_binding=room_binding)
        gs.room = room
        try:
            gs.load_bit(config.bit_name)
        except BitLoadError as exc:
            raise BootFailure(f"Bit load failed: {exc}") from exc

        if room.bound_dev is None:
            try:
                wait_for_room_binding(
                    gs, room_binding, config.room_setup_timeout,
                    tick=tick or (lambda: gs.tick(0.05)))
            except RoomBindingTimeout as exc:
                gs.abort()
                raise BootFailure(str(exc)) from exc

        room_bridge = RoomBridge()
        if room.bound_dev is not None:
            room_bridge.bind(room.bound_dev)
        teardown.push("room-bridge", room_bridge.shutdown)
        teardown.push("bit", lambda: _abort_if_running(gs))
    except BaseException:
        # Arco is a live subprocess by this point, and _bind_room_fast_path
        # may have spawned a simulator subprocess too. Closing the stack
        # unwinds whatever got as far as being registered, in the right
        # order, with each step guarded so cleanup cannot mask this
        # failure. Re-raise unchanged: the inner handlers above already
        # produced a well-labeled BootFailure for every stage.
        teardown.close()
        raise

    return gs, room_bridge, arco, teardown


def _abort_if_running(gs) -> None:
    """The Bit teardown step. Guarded on state because the driver may have
    already run the Bit to completion, and abort() on an IDLE server is not
    meaningful."""
    from control.state import State
    if gs.state != State.IDLE:
        gs.abort()
```

Update `_bind_room_fast_path` (now at lines ~126-140) to take and forward the stack:

```python
def _bind_room_fast_path(room: Room, room_binding: RoomBindingRegistry,
                         simulator_factory, known_device_connected,
                         teardown) -> None:
    """Attempt the no-tap-needed path: a Terrarium-spawned simulator, or a
    reconnect to a previously recorded physical device. Leaves the Room
    unbound (room.bound_dev stays None) if neither applies -- wait_for_room_
    binding below is what holds for a fresh admin-armed tap, not this
    function's job.

    The factory is handed the teardown stack and registers whatever it
    spawns, so an orphaned Room simulator is impossible by construction
    rather than by a getattr convention. An orphan matters: it never exits
    on its own, reconnects to the NEXT Arco and re-claims its dev name
    there, so that run's own simulator is refused by O2
    (o2/src/bridge.cpp:231-237) and renders nothing, silently.
    """
    if simulator_factory is not None:
        dev = simulator_factory(teardown)
        room.bound_dev = dev
        room_binding.bind(room.room_type, dev)
        return
    recorded = room_binding.bound_device(room.room_type)
    if recorded is not None and known_device_connected(recorded):
        room.bound_dev = recorded
```

Delete `_shutdown_simulator` (lines 143-163) and `shutdown` (lines 194-203) entirely. Add to the imports:

```python
from control.teardown import TeardownStack
```

- [ ] **Step 4: Run the tests**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_boot.py -q
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add control/boot.py tests/test_boot.py
git commit -m "fix(terrarium): boot() owns a teardown stack, not a shutdown order

boot.shutdown() is deleted. Its docstring said Arco goes last 'since
everything else may still want to address it during teardown', which was
true within this module's scope and wrong composed with the caller, which
owns o2lite CLIENT subprocesses talking to that hub. Reverse-of-
registration is right in both scopes without either knowing about the
other.

boot() now returns the stack and pushes Arco at spawn, the room bridge and
the Bit once they exist. It also ACCEPTS a stack, so a caller that starts
something before boot() -- terrarium_boot's DeviceLinkServer, which must
be listening before the simulator spawns -- registers it first and has it
torn down last.

simulator_factory becomes Callable[[TeardownStack], str] and registers
what it spawns, replacing PR #24's getattr(factory, 'process') convention,
which existed only because the factory had no way to hand its handle back.
All six of #24's failure-path cases are ported, not dropped, and the
success-path ordering now has the regression test it never had."
```

---

## Task 5: `harness/terrarium_boot.py` on the stack

**Files:**
- Modify: `harness/terrarium_boot.py:33-76` (factories), `:86-186` (`build`), `:189-211` (`shutdown`, `_NullRoomBridge`), `main()`
- Modify: `tests/test_terrarium_boot.py` (every `build(...)` and `shutdown(...)` call site)

**Interfaces:**
- Consumes: `boot(..., teardown=)` returning a 4-tuple (Task 4); `TeardownStack` (Task 2).
- Produces:
  - `build(...) -> tuple[GameServer, server, DeviceLinkAgent, ArcoProcess, TeardownStack]`
  - `shutdown(teardown: TeardownStack) -> None`
  - `_SimulatorFactory.__call__(teardown)` and `_O2SimulatorFactory.__call__(teardown)`

- [ ] **Step 1: Write the failing ordering test**

Add to `tests/test_terrarium_boot.py`:

```python
def test_shutdown_stops_the_simulator_before_arco():
    """THE bug this slice exists for. shutdown() called control.boot's
    shutdown() first, which ended with arco.shutdown(), so the O2 hub died
    before the Room simulator was asked to stop and the simulator spent its
    last moments on a dead socket. PR #24 corrected both FAILURE paths and
    left this success path wrong, which is why the order is now a
    consequence of registration rather than a list."""
    order = []

    class _RecordingPopen(FakePopen):
        def __init__(self, label):
            super().__init__()
            self._label = label

        def send_signal(self, sig):
            if self.returncode is None:
                order.append(self._label)
            super().send_signal(sig)

    arco_popen = _RecordingPopen("arco")
    sim_popen = _RecordingPopen("simulator")

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio())

    shutdown(teardown)

    assert order == ["simulator", "arco"]


def test_shutdown_stops_the_devicelink_server_last():
    """The Room simulator is a CLIENT of that server, and the server is
    started before boot() precisely so the simulator has something to
    connect to. Started first, therefore stopped last."""
    stopped = []
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, teardown = _build_with_fakes(config)
    server.stop = lambda: stopped.append("server")

    shutdown(teardown)

    assert stopped == ["server"]


def test_shutdown_reports_a_failing_step_without_skipping_the_rest():
    """A guarded stack: one broken teardown step must not orphan Arco."""
    arco_popen = FakePopen()
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, teardown = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=arco_popen),
        simulator_popen=FakePopen(), room_audio=_fake_room_audio())

    teardown.push("broken", _boom)

    shutdown(teardown)

    assert arco_popen.signals            # Arco still stopped
```

with, at the bottom of the file:

```python
def _boom():
    raise OSError("no such process")
```

Then update every existing call in the file: `_build_with_fakes` and each direct `build(...)` unpack five values ending in `teardown`, and every `shutdown(gs, agent, arco, sim)` becomes `shutdown(teardown)`. `test_o2lite_frame_is_released_across_the_shared_clock` and `test_build_threads_its_clock_into_the_default_room_audio` need the same treatment.

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_terrarium_boot.py -q
```

Expected: FAIL, `ValueError: not enough values to unpack (expected 5, got 5)` resolving to a `TypeError` on `shutdown()` arity.

- [ ] **Step 3: Update both simulator factories**

Replace `harness/terrarium_boot.py:33-76`. The `_SimulatorFactory` docstring changes because the `.process` attribute is no longer how the handle escapes:

```python
class _SimulatorFactory:
    """boot()'s simulator_factory contract is Callable[[TeardownStack], str]:
    the factory registers whatever it spawns on the stack it is handed, so
    an orphaned simulator is impossible by construction. `self.process` is
    kept only so tests can inspect the handle."""

    def __init__(self, server_url: str, *, popen=subprocess.Popen,
                 horizon: float | None = None) -> None:
        self._server_url = server_url
        self._popen = popen
        self._horizon = horizon
        self.process: SimulatorProcess | None = None

    def __call__(self, teardown) -> str:
        command = [sys.executable, "-u", "-m", "harness.room_simulator",
                   "--dev", SIM_DEV, "--server", self._server_url]
        if self._horizon is not None:
            # So the Room reports frame latency in absolute terms on exit.
            command += ["--control-horizon", str(self._horizon)]
        self.process = SimulatorProcess(command, popen=self._popen)
        self.process.start()
        teardown.push("simulator", self.process.shutdown)
        return SIM_DEV


class _O2SimulatorFactory:
    """Spawns the Room simulator as an o2lite client rather than a
    websocket one. Reuses harness/o2_shroom.py with --no-join: Control has
    already recorded this dev as the bound Room before the process is
    spawned, so there is no Registration Node to tap -- the same rule
    harness/room_simulator.py follows."""

    def __init__(self, ensemble: str, *, popen=subprocess.Popen) -> None:
        self._ensemble = ensemble
        self._popen = popen
        self.process: SimulatorProcess | None = None

    def __call__(self, teardown) -> str:
        # -u for the same reason _SimulatorFactory passes it: without it
        # this child's stdout is block-buffered, so its exit report is lost
        # on an ungraceful exit and harness/run_stack.py cannot watch it for
        # readiness markers.
        #
        # --exit-with-parent is what stops this subprocess outliving the
        # Terrarium. An orphan keeps its browser canvas open, reconnects to
        # the next Arco (o2litepy reconnects on its own) and claims sim-room
        # there, so the NEXT run's simulator is refused by O2 and renders
        # nothing. Passing our own pid covers the case teardown cannot: an
        # external SIGKILL of this process.
        self.process = SimulatorProcess(
            [sys.executable, "-u", "-m", "harness.o2_shroom",
             "--dev", SIM_DEV, "--ensemble", self._ensemble, "--no-join",
             "--exit-with-parent", str(os.getpid())],
            popen=self._popen)
        self.process.start()
        teardown.push("simulator", self.process.shutdown)
        return SIM_DEV
```

- [ ] **Step 4: Rewrite `build()`'s framing and `shutdown()`**

In `build()`, replace the opening (line 126 onward) so the stack exists before the server starts:

```python
    teardown = TeardownStack()
    if transport is None:
        server = DeviceLinkServer(host=host, port=port)
        server.start()
        # Pushed BEFORE boot() so it is torn down LAST. The Room simulator
        # is a client of this server, and boot() spawns it, so registration
        # order is what keeps client-before-server true here.
        teardown.push("devicelink-server", server.stop)
    else:
        # o2lite mode: there is no socket to listen on. The connection is
        # pyarco's, already clock-synced by arco.initialize(), and the
        # caller started the transport on it -- and therefore the caller
        # registers its teardown, after this function returns, so it stops
        # before everything registered here.
        server = transport
```

Delete the now-unused `owns_server` local. Pass the stack into `_boot`:

```python
    gs, room_bridge, arco, teardown = _boot(
        config, bit_registry, arco_command=arco_command,
        room_binding=room_binding, arco_process_cls=arco_process_cls,
        simulator_factory=factory, teardown=teardown)
```

Replace the whole `except BaseException:` block (lines 160-185) with:

```python
    except BaseException:
        # _boot() has already spawned Arco AND the simulator by this point,
        # and main() cannot clean either up: build() never returns, so its
        # `finally: shutdown(...)` has no handle at all. Closing the stack
        # unwinds everything registered so far, in order, each step guarded
        # so cleanup cannot mask this failure. BaseException so a Ctrl-C
        # during the ArcoSynthPool connect -- which blocks for up to 30s --
        # is covered too.
        teardown.close()
        raise

    return gs, server, agent, arco, teardown
```

Replace `shutdown` and delete `_NullRoomBridge` (lines 189-211):

```python
def shutdown(teardown) -> None:
    """Unwind everything, in reverse registration order, and report.

    Every step is registered at the point the thing it owns starts, so the
    order here is not a list anyone maintains: o2lite transport, then the
    Bit, then the Room bridge (which frees the Room's Arco voice), then the
    Room simulator subprocess, then Arco, then the devicelink server.

    Client before hub is the property that matters and the one that was
    broken: this function used to call control.boot.shutdown() first, which
    ends by killing Arco, and only then stop the simulator that talks to it.
    """
    for name, exc in teardown.close():
        print(f"teardown step {name!r} failed: {exc!r}", file=sys.stderr)
```

Add `from control.teardown import TeardownStack` to the imports and drop `from control.boot import shutdown as _boot_shutdown`.

- [ ] **Step 5: Update `main()`**

`main()` unpacks the new tuple and registers the transport itself:

```python
    gs, server, agent, arco, teardown = build(
        config, {"TestBit": _timed_test_bit_cls(_run_duration(args))},
        arco_command=[args.arco_command],
        room_binding=room_binding, host=args.host, port=args.port,
        transport=transport, clock=clock,
        arco_process_cls=arco_process_cls)

    try:
        if transport is not None:
            transport.start(o2lite)            # raises if the clock is unsynced
            # Registered AFTER everything build() registered, so it stops
            # BEFORE them -- including before Arco, whose hub this transport
            # is a guest on. Stopping it after Arco died was the same
            # client-after-hub bug as the simulator's, one layer up.
            teardown.push("o2lite-transport", transport.stop)
            print(...)
```

and the `finally` becomes:

```python
    finally:
        shutdown(teardown)
```

- [ ] **Step 6: Run the tests**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_terrarium_boot.py tests/test_boot.py -q
```

Expected: all PASS.

- [ ] **Step 7: Run the whole suite**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
```

Expected: PASS, no fewer than 662.

- [ ] **Step 8: Commit**

```bash
git add harness/terrarium_boot.py tests/test_terrarium_boot.py
git commit -m "fix(terrarium): stop every o2lite client before the hub it talks to

shutdown() called control.boot's shutdown() first, which ends with
arco.shutdown(), then stopped the Room simulator, then the transport. So
on every successful run the O2 hub died first and its clients spent their
last moments on a dead socket. PR #24 corrected both FAILURE paths and
left this success path untouched, which is the argument for making the
order a consequence of registration rather than a third hand-kept list.

build() now creates the stack before starting the DeviceLinkServer, so
that server is registered first and stops last -- correct, since the Room
simulator is its client. main() registers the o2lite transport after
build() returns, so it stops before Arco, fixing the same client-after-hub
bug one layer up. _NullRoomBridge is deleted with the function that needed
it.

_O2SimulatorFactory also gains the -u its websocket sibling always passed;
without it that child's stdout is block-buffered and its exit report is
lost on an ungraceful exit."
```

---

## Task 6: one SIGTERM handler, installed where the signal is actually sent

**Files:**
- Create: `harness/signals.py`, `tests/test_signals.py`
- Modify: `harness/led_smoke.py:87-91,113`, `harness/room_simulator.py:61-66,93`, `harness/o2_shroom.py` (`main`), `harness/terrarium_boot.py` (`main`)

**Interfaces:**
- Consumes: nothing.
- Produces: `harness.signals.sigterm_as_keyboard_interrupt() -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_signals.py`:

```python
"""harness/signals.py: one copy of the SIGTERM gotcha, and proof that the
four modules a supervisor signals actually install it."""
from __future__ import annotations

import signal

import pytest

from harness.signals import sigterm_as_keyboard_interrupt


def test_it_installs_a_handler_that_raises_keyboard_interrupt():
    previous = signal.getsignal(signal.SIGTERM)
    try:
        sigterm_as_keyboard_interrupt()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.parametrize("module_name", [
    "harness.led_smoke",
    "harness.room_simulator",
    "harness.o2_shroom",
    "harness.terrarium_boot",
])
def test_every_supervised_module_installs_the_handler(module_name, monkeypatch):
    """Python finally blocks do NOT run on a bare SIGTERM, only on
    KeyboardInterrupt. control/simulator_process.py signals its children
    with SIGTERM and harness/run_stack.py signals terrarium_boot the same
    way, so a module without this loses its exit report: o2_shroom's whole
    lateness summary and its backend.close() live in a finally.

    Asserted by source inspection rather than by running main(), because
    main() needs argv, sockets and in two cases a live Arco."""
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    source = inspect.getsource(module)
    assert "sigterm_as_keyboard_interrupt()" in source, (
        f"{module_name} is sent SIGTERM by a supervisor and would lose its "
        f"finally block without the handler")
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_signals.py -q
```

Expected: FAIL, `ModuleNotFoundError: No module named 'harness.signals'`.

- [ ] **Step 3: Write `harness/signals.py`**

```python
"""One copy of a Python gotcha that costs an exit report every time it is
forgotten.

Python's finally blocks do NOT run on a bare SIGTERM. The default
disposition terminates the process immediately, with no unwinding. So a
module whose cleanup, its measurement summary, or its backend.close() lives
in a finally loses all of it the moment a supervisor signals it.

Three modules in this repo are signalled with SIGTERM:
control/simulator_process.py sends it to the Room simulator (whichever of
harness/room_simulator.py or harness/o2_shroom.py is playing that role),
and harness/run_stack.py sends it to harness/terrarium_boot.py. A bare
`kill <pid>` sends it to any of them.

This lived as an identical six-line copy in harness/led_smoke.py and
harness/room_simulator.py, and was about to become a third and fourth. The
docstring is most of the value, so one copy means one place to record why.
"""

from __future__ import annotations

import signal


def _raise_keyboard_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def sigterm_as_keyboard_interrupt() -> None:
    """Make `kill <pid>` clean up the same way Ctrl-C already does.

    Call once, at the top of main(), before anything with a finally block
    that matters.
    """
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
```

- [ ] **Step 4: Install it in all four modules**

In `harness/led_smoke.py`: delete `_sigterm_as_keyboard_interrupt` (lines 87-91), add `from harness.signals import sigterm_as_keyboard_interrupt` to the imports, and replace line 113's `signal.signal(signal.SIGTERM, _sigterm_as_keyboard_interrupt)` with `sigterm_as_keyboard_interrupt()`. Remove `import signal` if nothing else in the file uses it.

In `harness/room_simulator.py`: delete `_sigterm_as_keyboard_interrupt` (lines 61-66), add the same import at module level, and replace line 93 with `sigterm_as_keyboard_interrupt()`. Drop `import signal` from `main()`'s local imports.

In `harness/o2_shroom.py`'s `main()`, immediately after `args = parser.parse_args()`:

```python
    # control/simulator_process.py shuts this process down with SIGTERM when
    # it is playing the Room simulator, and finally blocks do not run on a
    # bare SIGTERM -- so without this the exit lateness report and
    # backend.close() below are simply lost.
    sigterm_as_keyboard_interrupt()
```

with `from harness.signals import sigterm_as_keyboard_interrupt` at module level.

In `harness/terrarium_boot.py`'s `main()`, immediately after `args = ap.parse_args()`:

```python
    # harness/run_stack.py stops this process with SIGTERM, and the whole
    # ordered teardown below lives in a finally that a bare SIGTERM skips.
    sigterm_as_keyboard_interrupt()
```

with the same module-level import.

- [ ] **Step 5: Run the tests**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_signals.py tests/test_led_smoke.py tests/test_room_simulator.py tests/test_o2_shroom.py -q
```

Expected: all PASS.

- [ ] **Step 6: Run the whole suite and commit**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
git add harness/signals.py tests/test_signals.py harness/led_smoke.py harness/room_simulator.py harness/o2_shroom.py harness/terrarium_boot.py
git commit -m "fix(terrarium): make each process handle the signal it is actually sent

harness/o2_shroom.py handled KeyboardInterrupt and nothing else, while
control/simulator_process.py shuts it down with SIGTERM whenever it is
playing the Room simulator. Python finally blocks do not run on a bare
SIGTERM, so its lateness summary and its backend.close() were lost on
every teardown. harness/terrarium_boot.py has the same gap now that
run_stack signals it, and its entire ordered teardown is in a finally.

The handler existed as an identical six-line copy in led_smoke and
room_simulator and was about to become a fourth, so it moves to
harness/signals.py where the reason can be recorded once. A test asserts
all four modules install it, because the failure mode is silent: the
process still dies, it just stops telling you anything on the way out."
```

---

## Task 7: `--arco-log`, and a bounded pty buffer

**Files:**
- Modify: `control/arco_process.py:56-97` (`pty_popen`), `:100-140` (`_PtyProcess`)
- Modify: `harness/terrarium_boot.py` (`main`'s argparse and `arco_popen` selection)
- Modify: `tests/test_arco_pty.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `pty_popen(command, log_path=None)`; `_PtyProcess(pid, fd, log_path=None)` with `output` capped at `_OUTPUT_TAIL_BYTES = 65536`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_arco_pty.py`:

```python
def test_the_child_output_is_teed_to_a_log_file(tmp_path):
    """Arco's output was drained into an in-memory bytearray that nothing
    ever wrote anywhere, so 'Arco never came up' was the least diagnosable
    failure in the stack: the runner is a separate process and could not
    reach the buffer even in principle."""
    log = tmp_path / "arco.log"
    proc = pty_popen(["/bin/echo", "sentinel"], log_path=str(log))
    _wait_for_exit(proc)
    proc.wait()

    assert b"sentinel" in log.read_bytes()


def test_the_in_memory_buffer_is_bounded(monkeypatch):
    """A curses app redrawing continuously for a long --hold run grew this
    without bound. Keep a tail for diagnostics, not the whole run."""
    from control import arco_process

    monkeypatch.setattr(arco_process, "_OUTPUT_TAIL_BYTES", 64)
    proc = pty_popen(["/bin/sh", "-c", "for i in $(seq 1 500); do echo aaaaaaaaaa; done"])
    _wait_for_exit(proc)
    proc.wait()

    assert len(proc.output) <= 64
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_arco_pty.py -q
```

Expected: FAIL, `TypeError: pty_popen() got an unexpected keyword argument 'log_path'`.

- [ ] **Step 3: Implement the tee and the cap**

Add near the top of `control/arco_process.py`:

```python
# How much of Arco's console output to keep in memory. It is a curses app
# redrawing continuously, so an uncapped buffer grows without bound over a
# long --hold run. This is a diagnostic tail; the full stream goes to
# log_path when one is given.
_OUTPUT_TAIL_BYTES = 65536
```

Change `pty_popen`'s signature to `def pty_popen(command: list[str], log_path: str | None = None):`, extend its docstring with a `log_path` paragraph, and change its final line to `return _PtyProcess(pid, fd, log_path=log_path)`.

In `_PtyProcess.__init__`, accept and open the log:

```python
    def __init__(self, pid: int, fd: int, *, log_path: str | None = None) -> None:
        self.pid = pid
        self._fd = fd
        self.returncode = None
        self.output = bytearray()
        # Line-buffered append: the operator tails this while Arco is
        # coming up, and a crashed run must leave the reason behind.
        self._log = open(log_path, "ab", buffering=0) if log_path else None
```

In `_drain`, after `self.output += chunk`:

```python
            if self._log is not None:
                self._log.write(chunk)
            self.output += chunk
            if len(self.output) > _OUTPUT_TAIL_BYTES:
                del self.output[:-_OUTPUT_TAIL_BYTES]
```

In `close()` (added in Task 3), close the log too, before returning:

```python
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None
```

- [ ] **Step 4: Add `--arco-log` to `terrarium_boot`**

In `main()`'s argparse, after `--arco-pty`:

```python
    ap.add_argument("--arco-log", default=None, metavar="PATH",
                    help="Tee Arco's console output to this file. Needs "
                         "--arco-pty (that is what owns Arco's stdio). "
                         "Without it Arco's output is drained into memory "
                         "and discarded, which makes 'Arco never came up' "
                         "the least diagnosable failure in the stack.")
```

and in the `arco_popen` selection:

```python
    arco_popen = subprocess.Popen
    if args.arco_pty:
        from control.arco_process import pty_popen
        log_path = args.arco_log

        def arco_popen(command):
            return pty_popen(command, log_path=log_path)
    elif args.arco_log:
        print("--arco-log needs --arco-pty; ignoring", file=sys.stderr)
```

- [ ] **Step 5: Run the tests, the suite, and commit**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
git add control/arco_process.py harness/terrarium_boot.py tests/test_arco_pty.py
git commit -m "feat(terrarium): tee Arco's console to a log, and bound the buffer

_PtyProcess drained Arco's pty into an in-memory bytearray that nothing
ever wrote anywhere and nothing capped. Two problems in one: a curses app
redrawing continuously grows it without bound over a long --hold run, and
'Arco never came up' was the least diagnosable failure in the stack
because the buffer lives in a process the operator is not looking at.

--arco-log tees the stream to a file and the in-memory copy becomes a
64 KiB diagnostic tail. harness/run_stack.py always passes it, which is
what lets its failure summary point at Arco's own log."
```

---

## Task 8: `harness/markers.py`, the readiness contract

**Files:**
- Create: `harness/markers.py`, `tests/test_markers.py`
- Modify: `harness/terrarium_boot.py` (`main`'s prints), `harness/o2_shroom.py` (`main`'s prints, `service_conflict`)

**Interfaces:**
- Consumes: nothing.
- Produces, all `str` constants: `CONTROL_TRANSPORT_READY`, `CONTROL_SETUP_HOLD`, `DEVICE_CLOCK_SYNCED`, `DEVICE_ROLE_GRANTED`, `DEVICE_JOIN_DENIED`, `DEVICE_SERVICE_CONFLICT`; plus `READY_MARKERS: dict[str, str]` and `FAILURE_MARKERS: dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_markers.py`:

```python
"""harness/markers.py: the contract harness/run_stack.py watches stdout for.

These constants are the ONLY thing standing between a reworded print and a
runner that hangs forever with no diagnostic. Assert both sides."""
from __future__ import annotations

import inspect

import pytest

from harness import markers


def test_every_ready_marker_is_emitted_by_its_module():
    for name, marker in markers.READY_MARKERS.items():
        module = _module_for(name)
        assert marker in inspect.getsource(module), (
            f"{name}: harness/run_stack.py waits for {marker!r}, and nothing "
            f"in {module.__name__} emits it any more. A reworded print is a "
            f"hang, not a test failure, unless this test catches it.")


def test_every_failure_marker_is_emitted_by_its_module():
    for name, marker in markers.FAILURE_MARKERS.items():
        module = _module_for(name)
        assert marker in inspect.getsource(module)


def test_markers_are_non_empty_and_distinct():
    """A blank marker matches every line, and a marker that is a prefix of
    another would fire the wrong event."""
    all_markers = list(markers.READY_MARKERS.values()) + \
        list(markers.FAILURE_MARKERS.values())
    assert all(m.strip() for m in all_markers)
    assert len(set(all_markers)) == len(all_markers)
    for a in all_markers:
        for b in all_markers:
            if a is not b:
                assert not a.startswith(b)


def _module_for(name: str):
    import harness.o2_shroom
    import harness.terrarium_boot
    return (harness.terrarium_boot if name.startswith("CONTROL_")
            else harness.o2_shroom)
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, `ModuleNotFoundError: No module named 'harness.markers'`.

- [ ] **Step 3: Write `harness/markers.py`**

```python
"""The stdout contract harness/run_stack.py supervises processes through.

The runner has to know when Control is ready for devices to join, and when
a device has actually been granted its role, because both are the
difference between a working run and a silent one. Waiting a fixed number
of seconds for either was tried and is not good enough: the SETUP window is
short, and a device that joins outside it is refused
(control/registration.py refuses a SCORED role once RUNNING).

Promoting these strings from incidental print() calls to named constants
matched on both sides is what makes stdout-watching honest. A reworded
print then breaks tests/test_markers.py rather than hanging the runner
forever with nothing to show for it.

FAILURE markers matter as much as ready ones. Waiting out a full timeout on
a failure the child has already diagnosed turns a 30-second answer into a
five-minute one, and both of the ones here are conditions the child knows
about precisely and the runner cannot infer.
"""

from __future__ import annotations

# --- Control (harness/terrarium_boot.py) -------------------------------

# The o2lite transport has claimed `game` on the hub and is serving.
CONTROL_TRANSPORT_READY = "DeviceLink running on o2lite ensemble"

# Registration is open. Devices must join scored roles inside this window.
CONTROL_SETUP_HOLD = "Holding in SETUP"

# --- Device (harness/o2_shroom.py) -------------------------------------

# o2lite.time_get() went non-negative. Until this, the device has no clock
# and cannot stamp a gesture. This is the step the documented headless
# clock-sync defect stalls at, so it is the one the runner names when a CI
# run fails.
DEVICE_CLOCK_SYNCED = "clock synced at"

# Control answered the join with a role. Gestures start here.
DEVICE_ROLE_GRANTED = "role granted after"

# Control refused the join. Never recovers; fail the run now.
DEVICE_JOIN_DENIED = "JOIN DENIED:"

# The hub refused this device's service announcement because another
# process already offers that name (o2/src/bridge.cpp:231-237). The device
# clock-syncs, prints a watch URL and then receives nothing at all, which
# is the single most confusing live failure in this stack. See
# docs/superpowers/specs/2026-08-14-room-simulator-service-collision-design.md.
DEVICE_SERVICE_CONFLICT = "FATAL: service"

READY_MARKERS = {
    "CONTROL_TRANSPORT_READY": CONTROL_TRANSPORT_READY,
    "CONTROL_SETUP_HOLD": CONTROL_SETUP_HOLD,
    "DEVICE_CLOCK_SYNCED": DEVICE_CLOCK_SYNCED,
    "DEVICE_ROLE_GRANTED": DEVICE_ROLE_GRANTED,
}

FAILURE_MARKERS = {
    "DEVICE_JOIN_DENIED": DEVICE_JOIN_DENIED,
    "DEVICE_SERVICE_CONFLICT": DEVICE_SERVICE_CONFLICT,
}
```

- [ ] **Step 4: Emit the constants from the two modules**

In `harness/terrarium_boot.py`, add `from harness import markers` and rewrite the two prints in `main()`:

```python
            print(f"{markers.CONTROL_TRANSPORT_READY} "
                  f"{config.o2_ensemble!r} (Ctrl-C to stop)", flush=True)
```

```python
        if args.setup_seconds > 0:
            print(f"{markers.CONTROL_SETUP_HOLD} for {args.setup_seconds:g}s "
                  f"-- join now", flush=True)
```

Note the added `flush=True` on both: the runner reads these through a pipe, and `-u` covers the child only when the runner remembers to pass it. Being explicit costs nothing.

In `harness/o2_shroom.py`, add `from harness import markers` and rewrite three sites:

```python
    print(f"{markers.DEVICE_CLOCK_SYNCED} {o2lite.time_get():.3f}", flush=True)
```

```python
                    print(f"{markers.DEVICE_ROLE_GRANTED} {joins_sent} "
                          f"join(s); gestures starting at {now:.3f}", flush=True)
```

```python
                print(f"{markers.DEVICE_JOIN_DENIED} {reason} ({hint})",
                      flush=True)
```

and in `service_conflict`, change the opening of the returned string to use the constant:

```python
    return (f"{markers.DEVICE_SERVICE_CONFLICT} {dev!r} is not routed back "
            f"to this process. Another process on the Arco hub already "
            ...
```

Check `tests/test_o2_shroom.py` for assertions on the old literal wording and update them to reference `markers.*`.

- [ ] **Step 5: Run the tests, the suite, and commit**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
git add harness/markers.py tests/test_markers.py harness/terrarium_boot.py harness/o2_shroom.py tests/test_o2_shroom.py
git commit -m "feat(terrarium): promote the readiness lines to a tested contract

harness/run_stack.py watches Control's and each device's stdout for the
moments it cannot infer: registration opening, and a role actually being
granted. A fixed sleep is not good enough, because the SETUP window is
short and a device joining outside it is refused outright.

Matching on incidental print() wording would make a reworded line a hang
with no diagnostic. These constants, asserted on both sides, turn that
into a test failure instead.

Failure markers are first-class: JOIN DENIED never recovers, and PR #24's
'FATAL: service' names the one live failure where a device clock-syncs,
prints a watch URL and then silently receives nothing forever. Waiting out
a full timeout on either is pure lost time."
```

---

## Task 9: `harness/proc_tee.py`

**Files:**
- Create: `harness/proc_tee.py`, `tests/test_proc_tee.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `harness.proc_tee.ProcTee(name, stream, log_path, *, markers, echo=False, out=sys.stderr)` with `start() -> None`, `wait_for(marker: str, timeout: float, clock=..., sleep=...) -> bool`, `seen(marker: str) -> bool`, `join(timeout: float = 2.0) -> None`, `tail(lines: int = 20) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proc_tee.py`:

```python
"""harness/proc_tee.py: one child's stdout, fanned to a log file, the
operator's terminal, and a set of marker events."""
from __future__ import annotations

import io

from harness.proc_tee import ProcTee


def _tee(text, tmp_path, **kwargs):
    log = tmp_path / "child.log"
    tee = ProcTee("ie1", io.StringIO(text), str(log),
                  markers=["role granted after"], **kwargs)
    tee.start()
    tee.join(timeout=2.0)
    return tee, log


def test_every_line_reaches_the_log(tmp_path):
    tee, log = _tee("first\nsecond\n", tmp_path)
    assert log.read_text().splitlines() == ["first", "second"]


def test_a_marker_line_sets_its_event(tmp_path):
    tee, _log = _tee("noise\nrole granted after 3 join(s)\n", tmp_path)
    assert tee.seen("role granted after")


def test_an_absent_marker_stays_unseen(tmp_path):
    tee, _log = _tee("noise\n", tmp_path)
    assert not tee.seen("role granted after")


def test_wait_for_returns_true_once_the_marker_arrives(tmp_path):
    tee, _log = _tee("role granted after 1 join(s)\n", tmp_path)
    assert tee.wait_for("role granted after", timeout=1.0) is True


def test_wait_for_returns_false_on_timeout_rather_than_hanging(tmp_path):
    """CI mode's whole value: a failure is bounded and named, not a hang."""
    tee, _log = _tee("noise\n", tmp_path)
    ticks = iter([0.0, 5.0, 10.0])
    assert tee.wait_for("role granted after", timeout=1.0,
                        clock=lambda: next(ticks),
                        sleep=lambda _s: None) is False


def test_echo_writes_a_prefixed_copy(tmp_path):
    """Interactive mode: the operator watches the run unfold and the WebSim
    URLs are readable as they appear."""
    out = io.StringIO()
    tee, _log = _tee("hello\n", tmp_path, echo=True, out=out)
    assert out.getvalue() == "[ie1] hello\n"


def test_echo_is_off_by_default(tmp_path):
    out = io.StringIO()
    tee, _log = _tee("hello\n", tmp_path, out=out)
    assert out.getvalue() == ""


def test_tail_returns_the_last_lines_for_a_failure_summary(tmp_path):
    tee, _log = _tee("".join(f"line{i}\n" for i in range(50)), tmp_path)
    assert tee.tail(3) == ["line47", "line48", "line49"]
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, `ModuleNotFoundError: No module named 'harness.proc_tee'`.

- [ ] **Step 3: Write `harness/proc_tee.py`**

```python
"""ProcTee: fan one supervised child's stdout three ways.

To its own log file, so a run leaves evidence behind and a failure summary
has something to quote. To the operator's terminal with a short prefix, so
an interactive run reads as one narrative rather than three silent
processes. And to a set of marker events, so the supervisor can wait on
what actually happened instead of sleeping and hoping.

One daemon thread per child, reading line by line. Children are spawned
with -u and stderr folded into stdout, so there is exactly one stream per
process and it is not block-buffered.

The thread is JOINED, bounded, after its process stops. A device prints its
whole lateness summary on the way out, and reading to EOF is what puts that
in the log rather than cutting it off mid-write.
"""

from __future__ import annotations

import sys
import threading
import time


class ProcTee:
    """Reads `stream` to EOF on a daemon thread."""

    def __init__(self, name: str, stream, log_path: str, *, markers,
                 echo: bool = False, out=None) -> None:
        self.name = name
        self._stream = stream
        self._log_path = log_path
        self._echo = echo
        self._out = out if out is not None else sys.stderr
        self._events = {marker: threading.Event() for marker in markers}
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._pump, daemon=True,
                                        name=f"tee-{self.name}")
        self._thread.start()

    def _pump(self) -> None:
        with open(self._log_path, "w", encoding="utf-8", buffering=1) as log:
            for raw in self._stream:
                line = raw.rstrip("\n")
                log.write(line + "\n")
                if self._echo:
                    self._out.write(f"[{self.name}] {line}\n")
                    flush = getattr(self._out, "flush", None)
                    if flush is not None:
                        flush()
                with self._lock:
                    self._lines.append(line)
                for marker, event in self._events.items():
                    if marker in line:
                        event.set()

    def seen(self, marker: str) -> bool:
        return self._events[marker].is_set()

    def wait_for(self, marker: str, timeout: float, clock=time.monotonic,
                 sleep=time.sleep) -> bool:
        """True once `marker` has been seen, False once `timeout` elapses.

        Polls rather than using Event.wait(timeout) so a test can inject a
        clock and spend no real time. Bounded by construction: this is the
        function that turns the documented headless clock-sync defect from
        a hang into a named failure.
        """
        deadline = clock() + timeout
        event = self._events[marker]
        while True:
            if event.is_set():
                return True
            if clock() >= deadline:
                return False
            sleep(0.05)

    def join(self, timeout: float = 2.0) -> None:
        """Wait for the reader to reach EOF, so the child's last words land
        in the log. Bounded: a child whose stdout never closes must not hold
        teardown open."""
        if self._thread is not None:
            self._thread.join(timeout)

    def tail(self, lines: int = 20) -> list[str]:
        with self._lock:
            return list(self._lines[-lines:])
```

- [ ] **Step 4: Run the tests and commit**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_proc_tee.py -q
git add harness/proc_tee.py tests/test_proc_tee.py
git commit -m "feat(terrarium): ProcTee, one child's stdout fanned three ways

To a log file so a run leaves evidence and a failure summary has something
to quote; to the terminal with a prefix so an interactive run reads as one
narrative; and to marker events so the supervisor waits on what happened
rather than sleeping and hoping.

wait_for polls against an injectable clock and returns False on timeout
rather than blocking, which is what turns the documented headless
clock-sync defect from a hang into a named failure. join() is bounded and
called after the child stops, because a device prints its whole lateness
summary on the way out and reading to EOF is what keeps it."
```

---

## Task 10: `harness/run_stack.py`, the sequencer

**Files:**
- Create: `harness/run_stack.py`, `tests/test_run_stack.py`

**Interfaces:**
- Consumes: `TeardownStack` (Task 2), `stop_process` (Task 1), `markers` (Task 8), `ProcTee` (Task 9), `FakePopen` (Task 1).
- Produces:
  - `harness.run_stack.StackConfig` dataclass: `devices: int = 1`, `ensemble: str = "arco"`, `arco_command: str`, `setup_seconds: float = 20.0`, `seconds: float | None = None`, `horizon: float = 0.060`, `log_dir: str`, `echo: bool = True`, `ready_timeout: float = 90.0`, `join_timeout: float = 60.0`
  - `control_command(cfg) -> list[str]`, `device_command(cfg, index, ppid) -> list[str]`
  - `run(cfg, *, popen=subprocess.Popen, clock=time.monotonic, sleep=time.sleep, getpid=os.getpid) -> RunResult`
  - `RunResult` dataclass: `ok: bool`, `stage: str`, `detail: str`, `logs: dict[str, str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_stack.py`:

```python
"""harness/run_stack.py: the supervisor, driven entirely against fake
children so it stays in the offline suite."""
from __future__ import annotations

import io
import signal

from control.arco_process import FakePopen
from harness import markers
from harness.run_stack import (StackConfig, control_command, device_command,
                               run)


class ScriptedPopen(FakePopen):
    """A FakePopen whose children also SPEAK. Boundary rule 5 again: the
    real Popen we pass stdout=PIPE hands back an object with a readable
    .stdout, and a double without one would let every test pass while the
    runner's entire readiness path went unexercised."""

    def __init__(self, scripts, *, ignores=()):
        super().__init__(ignores=ignores)
        self._scripts = list(scripts)
        self.children: list[FakePopen] = []

    def __call__(self, command, **kwargs):
        child = FakePopen(ignores=self._ignores)
        child(command, **kwargs)
        text = self._scripts.pop(0) if self._scripts else ""
        child.stdout = io.StringIO(text)
        self.commands.append(command)
        self.kwargs = kwargs
        self.children.append(child)
        return child


def _cfg(tmp_path, **kwargs):
    return StackConfig(arco_command="/bin/true", log_dir=str(tmp_path),
                       echo=False, seconds=0.0, **kwargs)


_CONTROL_OK = (f"{markers.CONTROL_TRANSPORT_READY} 'arco'\n"
               f"{markers.CONTROL_SETUP_HOLD} for 20s\n")
_DEVICE_OK = (f"{markers.DEVICE_CLOCK_SYNCED} 12.345\n"
              f"{markers.DEVICE_ROLE_GRANTED} 1 join(s)\n")


def test_a_clean_run_reports_success(tmp_path):
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK])
    result = run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert result.ok is True
    assert result.stage == "complete"


def test_control_is_spawned_before_any_device(tmp_path):
    """A device that joins before registration opens is refused outright:
    TestBit's `player` is a SCORED role and RegistrationState.join()
    refuses those once RUNNING."""
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK])
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert "harness.terrarium_boot" in popen.commands[0]
    assert "harness.o2_shroom" in popen.commands[1]


def test_devices_are_stopped_before_control(tmp_path):
    """The ordering this whole slice exists for, at the runner's layer:
    every o2lite client stops before the process that owns the hub."""
    order = []

    class _RecordingPopen(ScriptedPopen):
        def __call__(self, command, **kwargs):
            child = super().__call__(command, **kwargs)
            label = "control" if "harness.terrarium_boot" in command else "device"
            original = child.send_signal

            def send_signal(sig, _label=label, _orig=original):
                if child.returncode is None:
                    order.append(_label)
                _orig(sig)

            child.send_signal = send_signal
            return child

    popen = _RecordingPopen([_CONTROL_OK, _DEVICE_OK])
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert order == ["device", "control"]


def test_children_are_spawned_in_their_own_session(tmp_path):
    """Without start_new_session, Ctrl-C in an interactive terminal is
    delivered to the whole foreground process group at once, every child
    gets SIGINT simultaneously, and the runner has no ordering left to
    enforce."""
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK])
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    for child in popen.children:
        assert child.kwargs["start_new_session"] is True


def test_a_device_carries_exit_with_parent(tmp_path):
    """A SIGKILLed runner would otherwise leave ie1 claimed on the hub, and
    the next run's own ie1 is refused by O2 silently."""
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK])
    run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None, getpid=lambda: 4242)

    assert "--exit-with-parent" in popen.commands[1]
    assert "4242" in popen.commands[1]


def test_control_never_becoming_ready_fails_bounded(tmp_path):
    popen = ScriptedPopen(["nothing useful\n"])
    ticks = iter([0.0] + [1000.0] * 20)
    result = run(_cfg(tmp_path), popen=popen, clock=lambda: next(ticks),
                 sleep=lambda _s: None)

    assert result.ok is False
    assert result.stage == "control-ready"


def test_a_device_that_never_syncs_fails_bounded_and_names_the_defect(tmp_path):
    """The documented headless clock-sync defect. The runner cannot fix it;
    it must not hang on it, and it must say which of the two documented
    halves it hit."""
    popen = ScriptedPopen([_CONTROL_OK, "watch at http://x\n"])
    ticks = iter([0.0, 0.0, 0.0] + [1000.0] * 20)
    result = run(_cfg(tmp_path), popen=popen, clock=lambda: next(ticks),
                 sleep=lambda _s: None)

    assert result.ok is False
    assert result.stage == "device-sync"
    assert "o2debug.log" in result.detail


def test_a_denied_join_fails_immediately(tmp_path):
    """Never recovers, so waiting out the timeout is pure lost time."""
    popen = ScriptedPopen([_CONTROL_OK,
                           f"{markers.DEVICE_CLOCK_SYNCED} 1.0\n"
                           f"{markers.DEVICE_JOIN_DENIED} scored role closed\n"])
    result = run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert result.ok is False
    assert result.stage == "device-join"


def test_a_service_conflict_fails_immediately(tmp_path):
    popen = ScriptedPopen([_CONTROL_OK,
                           f"{markers.DEVICE_CLOCK_SYNCED} 1.0\n"
                           f"{markers.DEVICE_SERVICE_CONFLICT} 'ie1' is taken\n"])
    result = run(_cfg(tmp_path), popen=popen, sleep=lambda _s: None)

    assert result.ok is False
    assert result.stage == "device-join"


def test_teardown_still_runs_when_a_stage_fails(tmp_path):
    popen = ScriptedPopen(["nothing useful\n"])
    ticks = iter([0.0] + [1000.0] * 20)
    run(_cfg(tmp_path), popen=popen, clock=lambda: next(ticks),
        sleep=lambda _s: None)

    assert popen.children[0].signals == [signal.SIGTERM]


def test_control_command_carries_the_flags_a_headless_run_needs(tmp_path):
    """--arco-pty because a piped-stdio Popen cannot open /dev/tty;
    --arco-settle-seconds and --arco-ready-timeout because the FIRST probe
    against a cold Arco can take ~18s and a failed probe sends a SECOND
    /host/clear that can leave arco.output None."""
    command = control_command(_cfg(tmp_path))

    assert "--transport" in command and "o2lite" in command
    assert "--arco-pty" in command
    assert "--arco-log" in command
    assert "--arco-settle-seconds" in command
    assert "--arco-ready-timeout" in command
    assert "--setup-seconds" in command
    assert "-u" in command


def test_device_command_carries_join_retry_and_samples_out(tmp_path):
    """--join-retry because a join sent before Control is listening is
    dropped with no queue behind it."""
    command = device_command(_cfg(tmp_path), 1, 99)

    assert "--join-retry" in command
    assert "--samples-out" in command
    assert "--control-horizon" in command
    assert "ie1" in command
    assert "TEST_PLAYER_NODE" in command


def test_more_than_one_device_gets_distinct_dev_names(tmp_path):
    popen = ScriptedPopen([_CONTROL_OK, _DEVICE_OK, _DEVICE_OK])
    run(_cfg(tmp_path, devices=2), popen=popen, sleep=lambda _s: None)

    assert "ie1" in popen.commands[1]
    assert "ie2" in popen.commands[2]
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, `ModuleNotFoundError: No module named 'harness.run_stack'`.

- [ ] **Step 3: Write the sequencer half of `harness/run_stack.py`**

```python
"""python -m harness.run_stack -- run the whole Arco stack from one command.

Before this, running the stack meant two interactive terminals in the right
order, a pile of non-obvious flags, and Ctrl-C in the right sequence or the
run's output was lost. Nothing captured either process's output to a file.

WHAT IT SUPERVISES. harness/terrarium_boot.py (which itself spawns Arco and
the Room simulator) plus N harness/o2_shroom.py player devices. It does NOT
reimplement terrarium_boot's sequencing: that module stays the single
definition of how Control boots.

o2lite throughout. There is no websocket variant, because the point is to
run the Arco stack.

WHY THE FLAGS ARE NOT OPTIONAL, all of these bought the hard way:

  --arco-pty: Arco's curses init opens /dev/tty, and a plain Popen whose
    stdio is a pipe fails with "Could not open /dev/tty". `script` does not
    rescue it.
  --arco-settle-seconds: the readiness probe is DESTRUCTIVE (pyarco's
    arco.initialize() sends /host/clear). One reset is survivable; a probe
    that fails and retries adds a second, and the extra teardown can leave
    arco.output None, after which ArcoSynthPool.start() dies.
  --arco-ready-timeout: the FIRST probe against a cold Arco can take ~18s
    while the second succeeds instantly, so the 15s default expires inside
    probe #1 with Arco perfectly healthy.
  --setup-seconds: `player` is a SCORED role and RegistrationState.join()
    refuses scored roles once RUNNING, so a device must join during SETUP.
  --join-retry: a join sent before Control is listening is dropped by Arco
    with no queue behind it.
  --exit-with-parent: an o2lite client that outlives its supervisor
    re-claims its dev name on the next hub, where O2 silently refuses the
    next run's own client.

WHAT IT CANNOT FIX. docs/MM_TERRARIUM.md records that a headless device
often never clock-syncs after Control's /host/clear, and that this does not
reproduce from an interactive terminal. The cause is unknown and upstream.
This runner does not fix it and does not pretend to. What it contributes is
that the failure is BOUNDED and NAMED rather than a hang.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

from control.process import stop_process
from control.teardown import TeardownStack
from harness import markers
from harness.proc_tee import ProcTee

DEFAULT_ARCO_COMMAND = "/Users/chris/projects/arco/apps/pytest/server"
PLAYER_NODE = "TEST_PLAYER_NODE"


@dataclass
class StackConfig:
    log_dir: str
    arco_command: str = DEFAULT_ARCO_COMMAND
    devices: int = 1
    ensemble: str = "arco"
    setup_seconds: float = 20.0
    seconds: float | None = None      # None means hold until Ctrl-C
    horizon: float = 0.060
    echo: bool = True
    ready_timeout: float = 90.0       # covers the ~18s first probe with room
    join_timeout: float = 60.0
    settle_seconds: float = 5.0
    arco_ready_timeout: float = 60.0


@dataclass
class RunResult:
    ok: bool
    stage: str
    detail: str
    logs: dict = field(default_factory=dict)


def control_command(cfg: StackConfig) -> list[str]:
    return [
        sys.executable, "-u", "-m", "harness.terrarium_boot",
        "--transport", "o2lite",
        "--arco-command", cfg.arco_command,
        "--arco-pty",
        "--arco-log", os.path.join(cfg.log_dir, "arco.log"),
        "--arco-settle-seconds", str(cfg.settle_seconds),
        "--arco-ready-timeout", str(cfg.arco_ready_timeout),
        "--setup-seconds", str(cfg.setup_seconds),
        "--horizon", str(cfg.horizon),
        "--hold",
    ]


def device_command(cfg: StackConfig, index: int, ppid: int) -> list[str]:
    dev = f"ie{index}"
    return [
        sys.executable, "-u", "-m", "harness.o2_shroom",
        "--dev", dev,
        "--node", PLAYER_NODE,
        "--ensemble", cfg.ensemble,
        "--join-retry", "2.0",
        "--control-horizon", str(cfg.horizon),
        "--samples-out", os.path.join(cfg.log_dir, f"{dev}-samples.json"),
        "--exit-with-parent", str(ppid),
    ]


def run(cfg: StackConfig, *, popen=subprocess.Popen, clock=time.monotonic,
        sleep=time.sleep, getpid=os.getpid) -> RunResult:
    """Bring the stack up, hold it, and tear it down in order.

    Every process is registered on the TeardownStack at the moment it is
    spawned, so Control (spawned first) stops last and the devices stop
    before it. That is the whole point of the primitive: the ordering is a
    consequence of startup, not a list this function maintains.
    """
    os.makedirs(cfg.log_dir, exist_ok=True)
    teardown = TeardownStack()
    tees: dict[str, ProcTee] = {}
    logs = {}

    def spawn(name: str, command: list[str], watch) -> ProcTee:
        process = popen(command, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True,
                        start_new_session=True)
        teardown.push(name, lambda: _stop(process, tees.get(name)))
        log_path = os.path.join(cfg.log_dir, f"{name}.log")
        logs[name] = log_path
        tee = ProcTee(name, process.stdout, log_path, markers=watch,
                      echo=cfg.echo)
        tee.start()
        tees[name] = tee
        return tee

    try:
        control = spawn("control", control_command(cfg),
                        [markers.CONTROL_TRANSPORT_READY,
                         markers.CONTROL_SETUP_HOLD])

        for stage, marker, detail in (
            ("control-ready", markers.CONTROL_TRANSPORT_READY,
             "Control never reported its o2lite transport up. Check "
             "arco.log for a failed Arco start, and o2debug.log."),
            ("control-setup", markers.CONTROL_SETUP_HOLD,
             "Control came up but never opened registration."),
        ):
            if not control.wait_for(marker, cfg.ready_timeout, clock, sleep):
                return RunResult(False, stage, detail, logs)

        devices = []
        for index in range(1, cfg.devices + 1):
            devices.append(spawn(
                f"ie{index}", device_command(cfg, index, getpid()),
                [markers.DEVICE_CLOCK_SYNCED, markers.DEVICE_ROLE_GRANTED,
                 markers.DEVICE_JOIN_DENIED, markers.DEVICE_SERVICE_CONFLICT]))

        for tee in devices:
            failed = _failed_marker(tee)
            if failed is not None:
                return RunResult(False, "device-join", failed, logs)
            if not tee.wait_for(markers.DEVICE_CLOCK_SYNCED,
                                cfg.join_timeout, clock, sleep):
                return RunResult(
                    False, "device-sync",
                    f"{tee.name} never clock-synced. This is the documented "
                    f"headless clock-sync defect: pyarco's initialize() sends "
                    f"/host/clear and a NEW o2lite client then hangs on "
                    f"time_get() < 0. It does not reproduce from an "
                    f"interactive terminal. Check o2debug.log -- 'dropping "
                    f"message because service was not found' means Control "
                    f"was not up yet, and total silence means the socket is "
                    f"dead. See docs/MM_TERRARIUM.md 'Not yet built'.", logs)
            failed = _failed_marker(tee)
            if failed is not None:
                return RunResult(False, "device-join", failed, logs)
            if not tee.wait_for(markers.DEVICE_ROLE_GRANTED,
                                cfg.join_timeout, clock, sleep):
                failed = _failed_marker(tee)
                return RunResult(
                    False, "device-join",
                    failed or f"{tee.name} synced but was never granted a "
                              f"role. Is Control still in SETUP? `player` is "
                              f"a scored role and is refused once RUNNING.",
                    logs)

        _hold(cfg, control, clock, sleep)
        return RunResult(True, "complete", "", logs)
    except KeyboardInterrupt:
        return RunResult(True, "interrupted", "stopped by Ctrl-C", logs)
    finally:
        for name, exc in teardown.close():
            print(f"teardown step {name!r} failed: {exc!r}", file=sys.stderr)


def _failed_marker(tee: ProcTee) -> str | None:
    """Both of these are conditions the child has already diagnosed
    precisely, and neither ever recovers, so waiting out the timeout is
    pure lost time."""
    if tee.seen(markers.DEVICE_JOIN_DENIED):
        return (f"{tee.name}: Control refused the join. See "
                f"{tee.name}.log for the reason and hint.")
    if tee.seen(markers.DEVICE_SERVICE_CONFLICT):
        return (f"{tee.name}: the hub refused this device's service "
                f"announcement because another process already offers that "
                f"name. Look for a stale `python -m harness.o2_shroom "
                f"--dev {tee.name}` and kill it.")
    return None


def _hold(cfg: StackConfig, control: ProcTee, clock, sleep) -> None:
    """Run for --seconds, or until Ctrl-C when no duration was asked for."""
    if cfg.seconds is None:
        while True:
            sleep(0.5)
    deadline = clock() + cfg.seconds
    while clock() < deadline:
        sleep(0.1)


def _stop(process, tee) -> None:
    """Stop one child and then drain what it said on the way out.

    Order matters within the step too: a device prints its whole lateness
    summary from a finally block, so joining the tee BEFORE the process has
    exited would cut it off, and skipping the join entirely would lose it.
    """
    stop_process(process)
    if tee is not None:
        tee.join(timeout=2.0)
```

- [ ] **Step 4: Run the tests**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_run_stack.py -q
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/run_stack.py tests/test_run_stack.py
git commit -m "feat(terrarium): run_stack sequences the whole Arco stack

Spawns terrarium_boot (which owns Arco and the Room simulator) plus N
o2_shroom devices, waits on real readiness markers rather than sleeps, and
tears down devices-before-Control on the shared TeardownStack, so the
runner's own ordering comes from the same primitive as the boot path's.

Children get start_new_session=True: without it, Ctrl-C in an interactive
terminal reaches the whole foreground group at once, every child takes
SIGINT simultaneously, and there is no ordering left to enforce. They also
get --exit-with-parent, because a SIGKILLed runner would otherwise leave
ie1 claimed on the hub and the next run's own ie1 refused silently.

The flags terrarium_boot needs for a headless run are baked in rather than
rediscovered: --arco-pty, --arco-settle-seconds, --arco-ready-timeout,
--setup-seconds, --join-retry. Each one has its reason recorded at the top
of the module.

The documented headless clock-sync defect is not fixed and not papered
over: the run fails in bounded time, names which of the two halves it hit,
and points at o2debug.log."
```

---

## Task 11: `run_stack`'s CLI, modes, and failure summary

**Files:**
- Modify: `harness/run_stack.py` (add `main`), `tests/test_run_stack.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `run`, `StackConfig`, `RunResult` (Task 10).
- Produces: `harness.run_stack.config_from_args(args) -> StackConfig`, `format_failure(result) -> str`, `main() -> None` exiting 0 on success and 1 on failure.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_run_stack.py`:

```python
def test_ci_mode_turns_echo_off_and_bounds_the_run():
    """One code path, two configurations: CI is the same machinery with the
    terminal echo off and a duration that ends the run."""
    from harness.run_stack import config_from_args, parse_args

    cfg = config_from_args(parse_args(["--ci", "--seconds", "30"]))

    assert cfg.echo is False
    assert cfg.seconds == 30.0


def test_interactive_mode_echoes_and_holds_by_default():
    from harness.run_stack import config_from_args, parse_args

    cfg = config_from_args(parse_args([]))

    assert cfg.echo is True
    assert cfg.seconds is None


def test_ci_mode_requires_a_duration():
    """An unbounded CI run is a hung CI job."""
    from harness.run_stack import config_from_args, parse_args

    cfg = config_from_args(parse_args(["--ci"]))

    assert cfg.seconds is not None


def test_the_failure_summary_names_the_stage_and_points_at_a_log(tmp_path):
    from harness.run_stack import RunResult, format_failure

    log = tmp_path / "ie1.log"
    log.write_text("first\nsecond\nthird\n")
    result = RunResult(False, "device-sync", "never synced",
                       {"ie1": str(log)})

    summary = format_failure(result)

    assert "device-sync" in summary
    assert "never synced" in summary
    assert str(log) in summary
    assert "third" in summary       # the tail is quoted, not just referenced


def test_the_failure_summary_survives_a_missing_log(tmp_path):
    """A stage can fail before its log has any content, and the summary
    must still be the thing that explains the run."""
    from harness.run_stack import RunResult, format_failure

    result = RunResult(False, "control-ready", "never came up",
                       {"control": str(tmp_path / "absent.log")})

    assert "control-ready" in format_failure(result)
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, `ImportError: cannot import name 'config_from_args'`.

- [ ] **Step 3: Add the CLI to `harness/run_stack.py`**

```python
CI_DEFAULT_SECONDS = 45.0


def parse_args(argv=None):
    import argparse

    ap = argparse.ArgumentParser(
        description="Run the whole Arco stack from one command.",
        epilog="Needs PYTHONPATH=/Users/chris/projects/arco for pyarco and "
               "o2litepy. CI mode is BEST-EFFORT: the headless clock-sync "
               "defect documented in docs/MM_TERRARIUM.md is upstream and "
               "unfixed, and this runner bounds and names it rather than "
               "fixing it.")
    ap.add_argument("--ci", action="store_true",
                    help="Non-interactive: no terminal echo, a bounded run, "
                         "and a non-zero exit on any failure.")
    ap.add_argument("--devices", type=int, default=1,
                    help="How many simulated player devices to join.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="How long to hold the stack up. Default: forever "
                         "(Ctrl-C to stop), or 45s under --ci.")
    ap.add_argument("--log-dir", default=None,
                    help="Where per-process logs and sample files go. "
                         "Default: runs/<timestamp>/.")
    ap.add_argument("--ensemble", default="arco")
    ap.add_argument("--arco-command", default=DEFAULT_ARCO_COMMAND)
    ap.add_argument("--setup-seconds", type=float, default=20.0,
                    help="How long Control holds registration open. A "
                         "device must join a SCORED role inside this "
                         "window or be refused.")
    ap.add_argument("--horizon", type=float, default=0.060,
                    help="Cue scheduling horizon, passed to both Control "
                         "and the devices so their reports agree.")
    return ap.parse_args(argv)


def config_from_args(args) -> StackConfig:
    log_dir = args.log_dir or os.path.join(
        "runs", time.strftime("%Y%m%d-%H%M%S"))
    seconds = args.seconds
    if seconds is None and args.ci:
        seconds = CI_DEFAULT_SECONDS      # an unbounded CI run is a hung job
    return StackConfig(
        log_dir=log_dir, arco_command=args.arco_command,
        devices=args.devices, ensemble=args.ensemble,
        setup_seconds=args.setup_seconds, seconds=seconds,
        horizon=args.horizon, echo=not args.ci)


def format_failure(result: RunResult, tail_lines: int = 20) -> str:
    lines = [
        "",
        "=" * 70,
        f"STACK RUN FAILED at stage {result.stage!r}",
        result.detail,
        "",
    ]
    for name, path in result.logs.items():
        lines.append(f"  {name}: {path}")
    lines.append("")
    failing = result.logs.get(result.stage.split("-")[0]) or \
        next(iter(reversed(list(result.logs.values()))), None)
    if failing:
        lines.append(f"last {tail_lines} lines of {failing}:")
        try:
            with open(failing, encoding="utf-8") as handle:
                for line in handle.read().splitlines()[-tail_lines:]:
                    lines.append(f"  | {line}")
        except OSError as exc:
            lines.append(f"  (could not read: {exc})")
    lines.append("=" * 70)
    return "\n".join(lines)


def main() -> None:
    sigterm_as_keyboard_interrupt()
    args = parse_args()
    cfg = config_from_args(args)

    try:
        from o2litepy import o2lite      # noqa: F401 (import is the check)
    except ImportError:
        print("run_stack needs o2litepy on the path. Re-run with "
              "PYTHONPATH=/Users/chris/projects/arco", file=sys.stderr)
        raise SystemExit(1) from None

    print(f"logs: {cfg.log_dir}")
    result = run(cfg)
    if result.ok:
        print(f"stack run {result.stage}; logs in {cfg.log_dir}")
        return
    print(format_failure(result), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
```

Add `from harness.signals import sigterm_as_keyboard_interrupt` to the module imports.

- [ ] **Step 4: Add `runs/` to `.gitignore`**

Append, after the `captures/` entry:

```
# harness/run_stack.py writes one directory per run: per-process logs plus
# each device's raw lateness samples.
runs/
```

- [ ] **Step 5: Run the tests, the suite, and commit**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
git add harness/run_stack.py tests/test_run_stack.py .gitignore
git commit -m "feat(terrarium): run_stack CLI, with a failure summary that ends the search

Interactive echoes prefixed child output and holds until Ctrl-C; --ci
turns echo off, bounds the run (an unbounded CI run is a hung CI job) and
exits non-zero. One code path, two configurations.

A failure prints the stage, the reason, every log path and the tail of the
relevant log, because the alternative is an operator who knows only that
something did not come up. The o2litepy import is checked before anything
is spawned, so a missing PYTHONPATH is one clear line rather than a child
dying obscurely.

Each run writes one directory under runs/, holding per-process logs and
each device's raw lateness samples, so sync_bench has a path to point at."
```

---

## Task 12: the upstream report

**Files:**
- Create: `docs/upstream/2026-08-14-o2-service-and-discovery-report.md`

**Interfaces:** none. Documentation only.

- [ ] **Step 1: Write the report**

Source the facts from `docs/superpowers/specs/2026-08-14-room-simulator-service-collision-design.md` section 3, which is the investigation that found both. Do not re-derive them; cite it.

The document covers, in this order:

1. **What this is:** two observations from mm-terrarium about o2lite/O2 behavior, written up for Roger Dannenberg. Neither is patched here, and neither is proposed as a fix: they are reports.
2. **Defect 1, a refused service announcement is silent on the client.** O2 logs the drop on the hub (`dropping message because /_o2/*/sv not from service provider`, `o2/src/bridge.cpp:231-237`), but o2lite offers no acknowledgement, no error callback, and no way to query whether a registration succeeded. A client that loses a service race is fully functional in every observable respect except that nothing addressed to it ever arrives: it clock-syncs, it serves its UI, and it receives nothing forever. Include the reproduction (two `harness/o2_shroom.py` processes claiming the same `--dev` against one Arco) and the workaround now in `devicelink/o2_transport.py`'s `verify_service_ownership`, which sends a nonce to its own service and waits for it to come back, exploiting the fact that o2lite `send()` has no local short circuit.
3. **Defect 2, o2litepy discovery has no ensemble filter.** `o2litepy/o2lite_disc.py:24` takes `ensemble` as a constructor argument and never stores it. `py3discovery.py:74` browses `_o2proc._tcp.local.` and `handle_new_service` at line 34 appends every host it resolves to `discovered_services`, with no comparison against the requested ensemble anywhere in the module. Consequence: an o2lite client joins whatever O2 host mDNS offers first, in any ensemble, on any machine on the LAN. Observed directly: an `--ensemble arco` client registered `sim-room` on a host whose ensemble was `svprobe`, and unrelated clients from concurrent sessions arrived in the same ensemble uninvited.
4. **Why defect 2 matters beyond this bug.** It widens an orphaned client's reach from "this run's Arco" to "any O2 host on the LAN". Standing alone it is a venue-scale hazard: two Terrariums on one network today would cross-connect, and `docs/MM_TERRARIUM.md`'s one-Terrarium-per-room model assumes they do not. That is the part worth Roger's attention even if defect 1 is judged acceptable.
5. **What mm-terrarium did about it,** so the report is not a request disguised as a bug list: `verify_service_ownership`, `--exit-with-parent`, and the teardown ordering work in this branch. All three are workarounds at the application layer.

Keep it to roughly two pages. Do not speculate about O2 internals beyond the cited line numbers.

- [ ] **Step 2: Commit**

```bash
git add docs/upstream/2026-08-14-o2-service-and-discovery-report.md
git commit -m "docs(terrarium): write up the two O2 defects for Roger

PR #24's spec recorded that both deserve an upstream report and did not
write one. This is it, ready to send: a silently-refused service
announcement that leaves a client fully functional except that nothing
ever reaches it, and an o2litepy discovery path that ignores the ensemble
argument entirely and joins whatever O2 host mDNS offers first.

The second is the one worth attention beyond this bug. It widens an
orphaned client's reach from this run's Arco to any O2 host on the LAN,
which makes two Terrariums on one network cross-connect and contradicts
the one-Terrarium-per-room model outright.

Reports, not proposals. What this repo did about them is recorded as
application-layer workarounds, so the scope of the real fix stays Roger's
call."
```

---

## Task 13: deep-dive sync and closeout

**Files:**
- Modify: `docs/MM_TERRARIUM.md`

- [ ] **Step 1: Run the full suite one final time**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
```

Expected: PASS, comfortably above 662 passed, 1 skipped. Record the exact number.

- [ ] **Step 2: Invoke `mm-deepdive-sync`**

`docs/MM_TERRARIUM.md` is an in-repo doc for this public repo, so it rides this same PR. No `mm-docs-push.sh`, no direct-to-main.

The sync must cover:

- **A new landed-subsystem entry** for `control/teardown.py` and `control/process.py`, stating the invariant (anything registered later is torn down earlier) and why `control/boot.shutdown()` was deleted rather than reordered.
- **A correction to the `harness/terrarium_boot.py` bullet** under the Terrarium Visualization Simulator section, which currently says it "tears down in order: Bit/Room (via `control.boot.shutdown()`, which frees the Room's Arco voice and shuts Arco down last) then simulator subprocess then the devicelink server". That description is now wrong in both its mechanism and its order.
- **A new entry recording that three teardown paths disagreed**, with PR #24 having fixed two and missed the third, as the reason the ordering is now structural. This is the kind of hard-won operational fact the deep-dive exists to carry.
- **A `harness/run_stack.py` entry**, including the honest statement that **CI mode is best-effort**: the headless clock-sync defect is upstream and unfixed, and the runner bounds and names it rather than fixing it. Link the existing *Not yet built / deferred* entry rather than duplicating its measurements.
- **An update to the `--exit-with-parent` / service-collision material** noting that `run_stack` extends the same guard to player devices.
- **A note on `harness/signals.py`** and the SIGTERM-skips-finally gotcha, since it now has one home.
- **A pointer to the upstream report** at `docs/upstream/2026-08-14-o2-service-and-discovery-report.md` under *Relationships to other repos*, so the two O2 defects have a destination rather than only a description.
- **The new suite baseline count.**

- [ ] **Step 3: Closeout**

Invoke `superpowers:finishing-a-development-branch` for the merge, branch and worktree cleanup.

---

## Self-Review

**Spec coverage.** Every section of the design maps to a task: §2.1 TeardownStack to Task 2, §2.2 signature changes to Tasks 4 and 5, §2.3 stop_process to Tasks 1 and 3, §2.4 signals to Task 6, §2.5 test doubles to Task 1, §3.1 markers to Task 8, §3.2 tee to Task 9, §3.3 sequencing to Task 10, §3.4 modes and failure surfacing to Task 11, §3.5 arco-log to Task 7, §3.6 offline testability across Tasks 9 to 11, §4 upstream report to Task 12, §5 the honest CI caveat to Tasks 10, 11 and 13, §6 testing throughout, §7 success criteria to Task 13.

**Ordering note.** Tasks 1 and 3 are deliberately split even though Task 1 leaves two tests red: Task 1's deliverable (the primitive plus a double strict enough to test it) is independently reviewable, and Task 3 is where the behavior change actually lands. The red tests are named in Task 1's commit message so a reviewer is not surprised.

**Type consistency.** `TeardownStack.push(name, fn)` and `.close() -> list[tuple[str, BaseException]]` are used identically in Tasks 4, 5, 10 and 11. `stop_process(process, *, sig, timeout, kill_timeout, clock, sleep)` is called with keyword arguments only in Tasks 3 and 10. `simulator_factory(teardown)` is consistent between `control/boot.py` (Task 4), both factories (Task 5) and the test doubles (Task 4). `ProcTee.wait_for(marker, timeout, clock, sleep)` is called positionally in Task 10 exactly as defined in Task 9. `build()` returns a 5-tuple ending in `teardown` in both Task 5's implementation and its tests.

