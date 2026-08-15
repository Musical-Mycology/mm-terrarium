# Device cleanup on signal, and a SETUP window that is not the binding constraint

Two fixes found by running `harness/run_stack.py` against a **live Arco** for
the first time, on 2026-08-14, plus the deep-dive corrections that run earned.

Neither fix makes a live run go green. The thing that blocks that is an
upstream clock-sync defect this repo does not own, and section 5 says so
plainly rather than implying otherwise.

Builds on the branch merged as PRs #25, #26 and #28 (`TeardownStack`,
`stop_process`, `harness/signals.py`, `harness/run_stack.py`) and sits on top
of #29 (`edeae99`).

Baseline: **729 passed, 1 skipped**, fully offline.

## 1. What the live run actually did

Three runs, one device each, via
`python -m harness.run_stack --ci --seconds N --devices 1`.

| Run | Outcome |
| --- | --- |
| `20260814-214721` | device synced at O2time 30.04, then `JOIN DENIED: registration closed for scored roles` |
| `20260814-214855` | device never clock-synced |
| `20260814-215103` | device never clock-synced |

**What held up, none of it previously exercised outside fakes:**

- **Teardown reaped every process, all three times, zero orphans** -- including
  on the two failing runs, which is the harder case. That is the primary
  purpose of the work in #25, confirmed live.
- Arco spawned on a pty and came up with audio (`Audio open completed
  successfully`, `Audio latency = 10 ms`), and `--arco-log` captured its curses
  output to a file, which is what made any of this diagnosable.
- `o2debug.log` recorded **zero** dropped messages and zero service-provider
  refusals across all three runs. No orphan collisions, nothing silently lost.
- CI mode **bounded and named** both failure modes and exited non-zero rather
  than hanging. That was the correctness gap #25's whole-branch review caught,
  working as intended.

## 2. Fix A: one guaranteed cleanup path in `o2_shroom.main()`

**The bug.** A SIGTERM arriving while the device is in its clock-sync wait
produces an **unhandled `KeyboardInterrupt` traceback**, and `backend.close()`
never runs. Observed on runs 2 and 3:

```
  File "harness/o2_shroom.py", line 246, in main
    time.sleep(0.01)
  File "harness/signals.py", line 26, in _raise_keyboard_interrupt
    raise KeyboardInterrupt
KeyboardInterrupt
```

That traceback is verbatim from the run, so its line numbers are the ones the
file had **before** PR #29 shifted everything below `build()` by one. Every
line number cited elsewhere in this spec is current as of `edeae99`; the
`time.sleep(0.01)` it names is now line 247.

`sigterm_as_keyboard_interrupt()` is installed at line 203 and the `try:` does
not begin until line 283, so **70 lines are exposed**, and everything in them
is interruptible:

| Line | What | Why it blocks |
| --- | --- | --- |
| 216 | `o2lite.initialize(ensemble)` | mDNS discovery and connect |
| 217 | `o2lite.set_services(dev)` | fire-and-forget, but on the same socket |
| 241-247 | `while o2lite.time_get() < 0` | the sync wait, where the live failure landed |
| 253 | `service_conflict(o2lite, dev)` | polls a self-addressed nonce with a timeout |

The sting: **the clock-sync wait is exactly where a device sits when the
documented upstream defect bites, so it is the most likely place in the whole
program to be signalled, and it is the one path the SIGTERM fix does not
cover.** Fix A is a direct consequence of the defect in section 5.

There are also **three hand-written `backend.close()` calls** (lines 244, 256
and 343), one per exit path, with SIGTERM as the forgotten fourth. That is the
enumerated-cleanup pattern the #25 branch replaced everywhere else, surviving
in the one file that still had it.

**The change.** Everything after `backend.open()` moves inside a single
`try` / `except KeyboardInterrupt` / `finally`, and the three close calls
collapse into one in the `finally`.

Behavior preserved deliberately:

- `KeyboardInterrupt` still means a clean exit. That is the existing contract
  for a Ctrl-C'd device and nothing should change it.
- `service_conflict`'s `SystemExit(1)` is a `BaseException`, so it passes
  through `except KeyboardInterrupt` untouched, still exits 1, and now gets
  its cleanup for free rather than by a hand-written call.
- The `parent_is_gone` early `return` still runs the `finally`.

One intended behavior change: a device killed during sync now also prints
`no timed frames observed -- nothing to summarise`, because the lateness
report shares that `finally`. That is accurate, not noise.

## 3. Fix B: the SETUP window stops being the binding constraint

**The measurement.** On run 1, Control connected at **O2time 7.806** and both
the Room simulator and `ie1` clock-synced at **30.04**. Device cold start
(python, luxaeterna import, WebSim backend, o2lite discovery, sync) is
therefore about **22 s**.

`run_stack` spawns devices only after Control prints its SETUP marker, so the
entire cold start burns the window. With the default `setup_seconds = 20.0`
the window closes first, and `player` is a **scored** role, which
`RegistrationState.join()` refuses once RUNNING. Hence run 1's
`JOIN DENIED: registration closed for scored roles`.

**The change.** `StackConfig.setup_seconds` default goes from `20.0` to
`90.0`, with the measurement recorded in the flag's help text so the number
has a derivation rather than being a fresh guess.

**Not derived from `--devices`,** deliberately: devices are spawned
concurrently, so cold start is roughly constant in device count, and a formula
nobody has measured is worse than a measured constant.

**Why the window and not the spawn ordering.** The obvious alternative is to
spawn devices earlier so they sync against a bare Arco before Control's
`/host/clear`. `harness/o2_shroom.py --join-retry`'s own help calls that "the
only reliable ordering". **That is contradicted by measurement**, in this
repo's own deep-dive: a client that synced *before* the reset keeps a valid
`time_get()` while its socket is dead, and sent **120 joins over 240 s** that
Control never received. The deep-dive's conclusion is explicit: "there is no
ordering that reliably works from a cold start". Restructuring the sequencing
would trade one failure for another, so this spec does not.

## 4. Fix C: what the deep-dive learns

Two corrections and one addition to `docs/MM_TERRARIUM.md`.

**Correction 1: the clock-sync defect is intermittent, not absolute.** The
entry says a new client "does not sync at all" after a `/host/clear`. Measured
2026-08-14: **1 of 3 runs synced**, at O2time 30.04. Deterministic-sounding
prose becomes unreliable-in-practice, which changes how a reader should treat
a single successful run.

**Correction 2: the silent half is no longer silent.** That entry predates
`verify_service_ownership` (`devicelink/o2_transport.py`, landed in PR #24).
The dead-socket case it describes as unnoticeable would now fail loud with
`FATAL: service ... is not routed back to this process`. The defect is
unchanged; its **observability** is not.

**Addition: `run_stack` has been exercised against a live Arco**, with the
section 1 results, including that teardown held with zero orphans across three
runs and that `o2debug.log` stayed clean.

## 5. What this deliberately does not do

**It does not make a live run go green, and is not intended to.** Runs 2 and 3
failed on clock sync, not on the window or the signal handling. Both fixes
here are orthogonal to that. The blocker remains the upstream defect recorded
under *Not yet built / deferred*: a device often cannot clock-sync to Arco
after Control's `arco.initialize()` sends `/host/clear`, it does not reproduce
from an interactive terminal, and the cause is unknown. `arco` and `o2litepy`
are sibling checkouts reached by `PYTHONPATH`, never vendored, so a fix does
not belong in this repo.

It also does not touch `harness/room_simulator.py`. Its exposed region is 13
lines with no blocking calls (`backend.open()` at 89, `try:` at 102), so the
same restructuring there would buy uniformity rather than fix a hole.

## 6. Testing

**Fix A cannot be unit-tested end to end, and this spec says so rather than
implying coverage it does not have.** `o2_shroom.main()` imports `o2litepy`,
which is absent from the offline suite by design, so `main()` is unreachable
from tests; existing coverage is of its pure functions (`tilt_sweep`,
`parent_is_gone`, `service_conflict`).

Fix A therefore gets a **source-inspection test**, the same technique
`tests/test_signals.py` already uses and for the same reason: assert
`backend.close()` appears **exactly once** in `harness/o2_shroom.py`. That
catches a regression to per-exit hand-written cleanup, which is the actual
failure mode. It does not prove the signal path works. Only a live run does,
which is how this was found.

Fix B gets an assertion on the default in `tests/test_run_stack.py`, alongside
the existing CI-mode default tests.

Fix C is prose, reviewed not tested.

The suite must stay fully offline and at or above **729 passed, 1 skipped**.

## 7. Success criteria

1. A SIGTERM anywhere between `backend.open()` and the tick loop exits the
   device cleanly, with no traceback and with `backend.close()` run.
2. `harness/o2_shroom.py` contains exactly one `backend.close()` call, and a
   test fails if a second appears.
3. `run_stack`'s SETUP window is no longer the binding constraint on a
   single-device run, and the default's derivation is recorded in its help.
4. The deep-dive records the intermittency, the improved observability, and
   the live-run results, without claiming the upstream defect is fixed.
5. Suite fully offline, at or above 729 passed, 1 skipped.
