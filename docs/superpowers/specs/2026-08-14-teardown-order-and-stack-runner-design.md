# Teardown order, and a one-command Arco stack runner

Two related pieces of work, specced together because they share one subject:
who owns which subprocess, and in what order those subprocesses stop.

**Part 1** makes every o2lite *client* stop and be reaped before the hub it
talks to, structurally rather than by convention.

**Part 2** turns the current two-terminal, right-order-or-lose-your-output
workflow into a single command.

Part 2 depends on Part 1: a supervisor that spawns processes needs a correct
teardown primitive before it can be trusted with one.

Supersedes nothing. Builds directly on
[`2026-08-14-room-simulator-service-collision-design.md`](2026-08-14-room-simulator-service-collision-design.md)
(PR #24) and
[`2026-08-12-control-o2lite-and-timed-cues-design.md`](2026-08-12-control-o2lite-and-timed-cues-design.md)
(PR #23).

Baseline at the time of writing: `141e8af`, **662 passed, 1 skipped**, fully
offline.

## 1. The problem

### 1.1 The hub dies before its clients

`harness/terrarium_boot.py:189-199`:

```python
def shutdown(gs, agent, arco, simulator):
    _boot_shutdown(gs, agent._room_bridge or _NullRoomBridge(), arco)
    simulator.shutdown()
    agent.server.stop()
```

`control/boot.py`'s `shutdown()` ends with `arco.shutdown()`, and its docstring
says why: "Arco last since everything else may still want to address it during
teardown". That is correct **within `boot.py`'s scope**, which knows nothing
about the simulator subprocess.

Composed with the caller it is wrong. The O2 hub is dead before the Room
simulator is asked to stop, so the simulator spends its last moments on a dead
socket. On the `--transport o2lite` path so does `agent.server`, which is the
`O2LiteTransport` riding pyarco's connection to that same Arco and is stopped
on the line after.

Observed 2026-08-14: a device process whose operator stopped Arco first spun on
a dead socket, and its exit report (written in a `finally`) never ran until the
process was signalled directly.

### 1.2 PR #24 fixed the same class of bug on the other two paths

PR #24 landed hours before this spec. It did not touch
`terrarium_boot.shutdown()`, but it rewrote both **failure** paths, and it
happened to get the ordering right on both:

| Path | Order today | |
| --- | --- | --- |
| `control/boot.py:95-121` (`except BaseException`) | simulator, then Arco | client before hub, correct |
| `harness/terrarium_boot.py:160-185` (`except BaseException`) | simulator, then Arco, then server | client before hub, correct |
| `harness/terrarium_boot.py:189-199` (success path) | Arco (inside `_boot_shutdown`), **then** simulator | hub before client, **wrong** |

Three separately-maintained orderings that have to agree by hand. Two of them
were corrected days ago and the third was not noticed. That is the argument for
this slice: not that the ordering is wrong once, but that nothing prevents it
from being wrong again.

PR #24 also wrote the guard discipline out longhand. `build()`'s new handler is
four nested `try/except: pass` blocks whose comments each say some form of
"never let cleanup mask the real failure", and two of the PR's commit subjects
are literally `guard boot()'s arco.shutdown() against masking the original
failure` and `guard build()'s cleanup calls against masking the original
failure`. That is a per-step guarantee being reimplemented per call site.

### 1.3 Teardown can hang, or lose the exit report

Three defects PR #24 did not touch (it modified none of
`control/simulator_process.py`, `control/arco_process.py`,
`harness/room_simulator.py`, `harness/led_smoke.py`):

**Unbounded waits.** `SimulatorProcess.shutdown()`
(`control/simulator_process.py:24-29`) and `ArcoProcess.shutdown()`
(`control/arco_process.py:222-227`) both send SIGTERM and then call
`self._process.wait()` with no timeout. `_PtyProcess.wait()` defaults to 5s and
escalates to SIGKILL, but a plain `subprocess.Popen.wait()` blocks forever, and
the simulator is always a plain `Popen`. A client that ignores or is slow to
handle its stop signal hangs teardown indefinitely today.

**A signal nothing handles.** `harness/o2_shroom.py` handles `KeyboardInterrupt`
and nothing else. `harness/terrarium_boot.py`'s `_O2SimulatorFactory` spawns it
through `SimulatorProcess`, which sends **SIGTERM**. Python `finally` blocks do
not run on a bare SIGTERM, so the process dies without printing its lateness
report or closing its WebSim backend. `harness/room_simulator.py:61-66` and
`harness/led_smoke.py:87-91` each carry an identical six-line
`_sigterm_as_keyboard_interrupt` for exactly this reason;
`harness/o2_shroom.py` and `harness/terrarium_boot.py` do not have one.

Note this is a **different** hazard from the one `--exit-with-parent` closes.
That flag guards against the parent *dying*. This is about the signal the
parent deliberately *sends*.

**Buffered output.** `_O2SimulatorFactory` omits the `-u` that its websocket
sibling `_SimulatorFactory` passes, so the o2lite Room simulator's stdout is
block-buffered: its output is lost on an ungraceful exit and unusable as a
readiness signal.

### 1.4 The stack takes two terminals and exact key timing

Running the full stack today means starting `terrarium_boot` in one terminal
with a pile of non-obvious flags, waiting for the right line, starting
`o2_shroom` in a second terminal inside the SETUP window, and then Ctrl-C-ing
them in the right order or losing the run's output. Nothing captures either
process's output to a file. Arco's own output is drained into an in-memory
`bytearray` (`control/arco_process.py:114`) that nothing ever writes anywhere
and that grows without bound for a continuously-redrawing curses app.

## 2. Design, Part 1: process lifecycle

### 2.1 `control/teardown.py`, a guarded LIFO stack

```python
class TeardownStack:
    def push(self, name: str, fn: Callable[[], None]) -> None: ...
    def close(self) -> list[tuple[str, BaseException]]: ...
```

Three properties, and each one is load-bearing:

1. **Steps unwind in reverse push order.** Anything started later stops earlier.
2. **Every step is guarded.** A step that raises has its exception captured with
   its name, and unwinding continues. One failing step can never orphan
   anything registered before it, and cleanup can never mask the failure that
   triggered it.
3. **`close()` is idempotent.** A second call is a no-op. This is what lets
   `boot()`'s failure path and the caller's normal teardown both call it
   without coordinating.

`close()` catches **`BaseException`**, not `Exception`. PR #24 paid for this
lesson twice: `KeyboardInterrupt` is not an `Exception`, and a Ctrl-C landing
inside `ArcoSynthPool.start()` (which blocks for up to 30s on
`arco.initialize()`) was leaking both subprocesses. The stack encodes it once
so no future call site has to remember.

`close()` returns the list of `(name, exception)` failures rather than raising.
Callers decide: `boot()` re-raises its original `BootFailure` and leaves
teardown failures secondary; `terrarium_boot` surfaces them in its exit summary.

**Why not `contextlib.ExitStack`.** ExitStack unwinds LIFO and does continue
past a failing callback, but it re-raises the *last* exception and merely chains
the others as `__context__`, and it has no notion of step names. Teardown here
needs every failure, named, with the original boot failure staying primary.
That is roughly forty lines and it is worth owning.

**One wrinkle, stated plainly.** Push order is deliberate, not literally
creation order. `boot()` creates Arco, then `gs`, then `room_bridge`, but the
documented teardown contract is `gs`, then `room_bridge`, then Arco: the Bit's
`on_unload` may still cue into the room bridge, so the bridge must not die
first. So `boot()` pushes Arco at spawn, and pushes `room_bridge.shutdown` and
then `gs.abort` once the bridge exists. The stack's invariant is "reverse of
push, guarded, idempotent"; push points are chosen and documented.

The structural guarantee is still exactly the one this slice needs:

> Anything the caller starts **after** `boot()` returns is pushed onto the same
> stack, and is therefore torn down **before** everything `boot()` owns.

Client-before-hub stops being an ordering someone maintains and becomes a
consequence of when things start. On the o2lite path this also fixes
`agent.server.stop()` running after Arco, at no extra cost: the transport is
started after Arco, so it stops before it.

### 2.2 Signature changes

- `boot()` returns `(gs, room_bridge, arco, stack)`.
- `simulator_factory` becomes `simulator_factory(teardown)`, and a factory that
  spawns a process registers it on the stack itself. This **replaces** PR #24's
  `getattr(simulator_factory, "process", None)` convention
  (`control/boot.py:143-163`), which exists only because the factory had no way
  to hand its handle back.
- `build()` returns `(gs, server, agent, arco, stack)`. Same arity: `simulator`
  drops out, because no caller needs it once teardown owns it.
- `terrarium_boot.shutdown(stack)` replaces the four-argument version.
  `_NullRoomBridge` (`harness/terrarium_boot.py:202-211`) is deleted: it exists
  only to satisfy `_boot_shutdown`'s signature, and nothing calls that with a
  missing bridge once `boot()` registers the bridge itself.
- `boot()`'s `except BaseException` block and `build()`'s both collapse to
  `stack.close()` and a bare `raise`.

**PR #24's tests are ported, not dropped.** `tests/test_boot.py` gained cases
for the simulator being shut down on an unknown Bit, a `BitLoadError`, an
unsupported room type, a `RoomBindingTimeout`, and a `KeyboardInterrupt`, plus
a bare-callable factory with no `.process` still booting. Every one of those
behaviors must still hold on the new seam. The bare-callable case becomes "a
factory that registers nothing", which the stack handles by construction.

### 2.3 `control/process.py`, bounded stop

```python
def stop_process(process, *, sig=signal.SIGTERM, timeout=5.0,
                 kill_timeout=5.0) -> int | None
```

Signal, poll-wait to `timeout`, escalate to SIGKILL, poll-wait to
`kill_timeout`, reap, return the exit code. Modelled on `_PtyProcess.wait()`
(`control/arco_process.py:167-192`), which already got this right and is the
only place in the repo that did.

It polls in a loop rather than calling `Popen.wait(timeout=...)` **because
`_PtyProcess.poll()` drains the pty**, and an undrained pty blocks a curses app
on its own screen writes. A poll loop is the one shape that serves both a plain
`Popen` and the pty. That reason goes in the docstring, because it otherwise
reads as a gratuitous reimplementation of a stdlib feature.

`ArcoProcess.shutdown()` and `SimulatorProcess.shutdown()` both delegate to it.
`_PtyProcess` keeps its drain-aware `poll()`, gains an explicit `close()` for
the master fd, and loses its private escalation logic.

### 2.4 `harness/signals.py`, one handler

`sigterm_as_keyboard_interrupt()` installs the SIGTERM-raises-`KeyboardInterrupt`
handler. Installed by:

| Module | Change |
| --- | --- |
| `harness/led_smoke.py` | replaces its local copy |
| `harness/room_simulator.py` | replaces its local copy |
| `harness/o2_shroom.py` | **new**: it is SIGTERM'd by `SimulatorProcess` today |
| `harness/terrarium_boot.py` | **new**: it is SIGTERM'd by the Part 2 runner |

`harness/devicelink_smoke.py` and `harness/capture_smoke.py` are deliberately
left alone: nothing sends them SIGTERM. The function exists to encode a Python
gotcha, and its docstring is most of its value, so one copy means one place to
record why.

`_O2SimulatorFactory` also gains the `-u` its websocket sibling already passes.

### 2.5 Test doubles, under boundary rule 5

> A test double must never be more permissive than the library it stands for.

`control/arco_process.py`'s `FakePopen` has **no `poll()` at all** and a
`wait()` that takes no arguments. `stop_process` calls both. Bringing the fake
up to the real thing's strictness:

- `poll()` returns `None` while running and the exit code after, as `Popen` does.
- `send_signal` on an already-exited process is a no-op, as `Popen.send_signal`
  is.
- `wait(timeout=...)` raises `subprocess.TimeoutExpired` when the child is still
  alive, as the real one does.
- A configurable `ignores=(signal.SIGTERM,)` so **a child that refuses to die is
  a testable state**. Without it the SIGKILL escalation has no coverage at all,
  which is precisely the rule-5 trap: the double would be more permissive than
  reality in the one dimension the new code exists for.

Ordering regressions assert against a shared record list that both fake Popens
append to, so "the simulator was signalled before Arco" is a direct assertion
rather than an inference. Clock and sleep are injected, so escalation tests
spend no real time.

## 3. Design, Part 2: `harness/run_stack.py`

`python -m harness.run_stack` spawns `terrarium_boot` and N `o2_shroom` player
devices as subprocesses, waits on real readiness signals, tees each process to
its own log, and tears down device-first on both normal exit and Ctrl-C.

It keeps `terrarium_boot` as the single definition of how Control boots, rather
than duplicating its sequencing. It uses Part 1's `TeardownStack`, so the runner
and the boot path share one ordering primitive.

**o2lite throughout.** The runner exists to run the Arco stack, so there is no
websocket variant. See section 5 for what that costs.

### 3.1 `harness/markers.py`, the readiness contract

Named constants, emitted by `terrarium_boot`/`o2_shroom` and matched by the
runner, with tests asserting both sides. A future edit to a print string then
breaks a test rather than silently hanging the runner forever. This is the only
thing that makes stdout-watching honest.

**Failure markers are first-class**, not an afterthought: waiting out a full
timeout on a failure the child already diagnosed is the difference between a
30-second answer and a 5-minute one.

| Constant | Emitter | Kind |
| --- | --- | --- |
| `DeviceLink running on o2lite ensemble` | Control | ready |
| `Holding in SETUP` | Control | ready, registration open |
| `clock synced at` | device | ready |
| `role granted after` | device | ready |
| `JOIN DENIED:` | device | failure |
| `FATAL: service` | device | failure |

The last one is PR #24's `service_conflict()` diagnostic. It names the single
most confusing live failure in this stack (a device that clock-syncs, prints a
watch URL, and then receives nothing because another process won its service
race), and failing the run on it immediately is free.

### 3.2 Output tee

One daemon thread per child reads its stdout line by line, writes to
`<log-dir>/<name>.log`, echoes it prefixed (`[control]`, `[ie1]`) when
interactive, and sets a per-marker `Event` the main thread waits on with a
bound. Children are spawned with `-u` and `stderr=STDOUT`.

The tee thread is **joined, bounded, after** its process stops, so a device's
exit lateness report actually lands in its log instead of being cut off
mid-write.

`--log-dir` defaults to `runs/<timestamp>/`, which gets a `.gitignore` entry
alongside the existing `captures/` and `o2debug.log` ones. Each device's
`--samples-out` JSON lands in the same directory, so one run is one directory
and `python -m harness.sync_bench` has a path to point at.

### 3.3 Sequencing and teardown

Children are spawned with **`start_new_session=True`**. Without it, Ctrl-C in
an interactive terminal is delivered to the whole foreground process group at
once, every child gets SIGINT simultaneously, and the runner has no ordering
left to enforce, which would defeat Part 1 entirely. With it, Ctrl-C reaches
only the runner, which then sequences teardown itself. `setsid()` changes
session and process group, not parentage, so `--exit-with-parent` still works.

Flow:

1. Verify `o2litepy` is importable, and fail with a message about `PYTHONPATH`
   rather than letting a child die obscurely.
2. Spawn Control (`terrarium_boot --transport o2lite --arco-pty
   --arco-log ... --setup-seconds ...`), push its stop on the stack.
3. Wait bounded for transport-ready, then for the SETUP hold.
4. Spawn N devices (`o2_shroom --dev ie<N> --node TEST_PLAYER_NODE --join-retry
   --control-horizon --samples-out --exit-with-parent <runner pid>`), pushing
   each on the stack.
5. Wait bounded for each device to sync and be granted; fail fast on either
   failure marker.
6. Run: hold until Ctrl-C or Control exiting (interactive), or for `--seconds`
   (CI).
7. `stack.close()`. LIFO unwinds the devices in reverse spawn order, then
   Control, whose own stack then unwinds the Room simulator and finally Arco.

The runner passes `--exit-with-parent` for the same reason `_O2SimulatorFactory`
does: a SIGKILLed runner would otherwise leave `ie1` claimed on the hub and
poison the next run.

### 3.4 Modes and failure surfacing

One code path, two configurations. `--ci` turns echo off, bounds the run with
`--seconds`, and exits non-zero on any unmet marker or non-zero child exit.
Interactive echoes prefixed output and holds until Ctrl-C.

On failure the runner prints a summary naming the stage that failed, the
process, its log path, and the log's tail. Exit is 0 on success and non-zero
otherwise, with the reason named in the summary rather than encoded in the code.

### 3.5 `--arco-log`, and a bounded buffer

`pty_popen` takes an optional log path; `_PtyProcess` tees its drained bytes
there and keeps only a bounded tail (64 KiB) in memory.
`terrarium_boot --arco-log PATH` exposes it and the runner always passes it.

This fixes a real unbounded-growth leak and makes "Arco never came up"
diagnosable, which is currently the least diagnosable failure in the stack.

### 3.6 Offline testability

The runner must be testable with no Arco, no O2 and no pyarco, like everything
else in the suite. Three separable pieces:

- marker matching and the tee, tested against an in-memory stream;
- the sequencer, tested with an injected `popen` whose fake children emit
  scripted stdout lines (extending `FakePopen` with a readable `stdout`, which
  rule 5 requires anyway since the real `Popen` has one);
- argparse-to-config, tested directly.

## 4. Upstream report

PR #24 surfaced two upstream defects and recorded that both "deserve an upstream
report to Roger". This branch produces that report as a document, for the
operator to send on. It is a deliverable, not a code change.

- **A refused service announcement is silent on the client.** o2lite offers no
  acknowledgement, no error callback, and no way to query whether a service
  registration succeeded. A client that loses a service race is fully functional
  in every observable respect except that nothing addressed to it ever arrives.
  `verify_service_ownership` works around this; it does not fix it.
- **o2litepy's discovery has no ensemble filter.** `o2lite_disc.py:24` takes
  `ensemble` and never stores it; `py3discovery.py:34` appends every host it
  resolves. An o2lite client joins whatever O2 host mDNS offers first, in any
  ensemble, on any machine on the LAN. Observed directly during #24's
  investigation. Standing alone this is a venue-scale hazard: two Terrariums on
  one network would cross-connect, and the "one Terrarium per room" model
  assumes they do not.

The report restates both with their reproductions and their observed effects,
and names the venue consequence. It proposes nothing: these are Roger's calls.

## 5. What this deliberately does not solve

**CI mode is knowingly best-effort.** `docs/MM_TERRARIUM.md` records that a
headless device cannot reliably clock-sync after Control's `arco.initialize()`
sends `/host/clear`, and that this does not reproduce from an interactive
terminal. The cause is unknown and upstream. The runner does not fix it and
does not pretend to. What it contributes is that the failure is **bounded and
diagnosed** instead of a hang: the run fails in known time with a message
pointing at `o2debug.log` and the deep-dive entry. The spec, the runner's own
`--help`, and the deep-dive will all say so plainly rather than implying CI is
trustworthy.

**Arco's curses UI is not watchable.** Arco runs on a captured pty either way,
so the operator sees its log file, not its console. `--arco-start-audio` remains
the only console key the stack presses, and remains off by default and a
diagnostic rather than a fix.

**No new transport, no new timing work.** The clamp-counter and `cue_horizon`
questions recorded in `docs/MM_TERRARIUM.md` are untouched.

## 6. Testing

Everything below runs with no O2, no Arco and no pyarco. No module under
`control/` may import o2litepy, and `control/audio.py` never imports pyarco.

| Area | Tests |
| --- | --- |
| `TeardownStack` | reverse order; a raising step does not skip later steps; `close()` is idempotent; `BaseException` is caught; failures are returned named |
| `stop_process` | SIGTERM then exit; a child ignoring SIGTERM is SIGKILLed and reaped within the bound; an already-exited child is a no-op; injected clock and sleep so it is instant |
| Ordering | the simulator is signalled before Arco on the **success** path (the regression this slice exists for), and on both failure paths |
| PR #24 ported | unknown Bit, `BitLoadError`, unsupported room type, `RoomBindingTimeout`, `KeyboardInterrupt`, bare-callable factory |
| Signals | each of the four modules installs the SIGTERM handler |
| `FakePopen` | matches `Popen` on `poll`, `wait(timeout=)` raising `TimeoutExpired`, `send_signal` after exit, and models a signal-ignoring child |
| Markers | every constant is emitted by its module and matched by the runner |
| Tee | lines reach the log; markers set their events; the thread drains to EOF |
| Runner | full sequence against fake children; fail-fast on each failure marker; bounded-wait timeout; teardown order |
| `--arco-log` | output is teed to the file; the in-memory buffer stays bounded |

## 7. Success criteria

1. `terrarium_boot.shutdown()` stops the Room simulator, and any other spawned
   client, before Arco, and a test fails if that order is reversed.
2. All three teardown paths (success, `boot()` failure, `build()` failure) get
   their order from one mechanism, so they cannot disagree.
3. A client that ignores SIGTERM is escalated to SIGKILL and reaped within a
   bounded time, and never survives teardown.
4. Every process spawned by this repo handles the signal it is actually sent,
   and its exit report survives.
5. `python -m harness.run_stack` brings up Arco, Control, the Room simulator and
   one player device, waits on real readiness signals, captures each process to
   its own log, and tears everything down in order on both normal exit and
   Ctrl-C.
6. A CI-mode failure is bounded and names its cause, rather than hanging.
7. The suite stays fully offline and green, at or above 662 passing.
8. `docs/MM_TERRARIUM.md` is updated in the same branch via `mm-deepdive-sync`,
   including the honest note that CI mode is best-effort.
