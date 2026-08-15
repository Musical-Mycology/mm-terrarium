# Device Cleanup and SETUP Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a signalled device shut down cleanly wherever it is in startup, and stop `run_stack`'s SETUP window being the thing that fails a run.

**Architecture:** Three independent changes. `harness/o2_shroom.py`'s `main()` gets one `try/except/finally` covering everything after `backend.open()`, collapsing three hand-written `backend.close()` calls into one. `harness/run_stack.py`'s SETUP-window default moves from 20s to 90s in the two places it is declared. `docs/MM_TERRARIUM.md` records what the live run measured.

**Tech Stack:** Python 3, pytest. No new dependencies.

Spec: [`docs/superpowers/specs/2026-08-14-device-cleanup-and-setup-window-design.md`](../specs/2026-08-14-device-cleanup-and-setup-window-design.md)

## Global Constraints

- **Run the suite through the project venv.** There is no bare `python`. The command is exactly:
  `PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q`
- **Baseline is 729 passed, 1 skipped** at `edeae99`. The suite must stay green and never drop below that count.
- **The suite stays fully offline.** No O2, no Arco server, no pyarco, no o2litepy. No module under `control/` may import `o2litepy`. **No test may call `harness.o2_shroom.main()` or `harness.run_stack.main()`** -- both import `o2litepy`, which is absent by design.
- **No em dashes in prose.** The repo's docstrings use `--`; match the surrounding file.
- Commit after every task. Conventional-commit subjects: `fix(terrarium):` / `docs(terrarium):`.

---

## File Structure

**Modify:**

| File | Change |
| --- | --- |
| `harness/o2_shroom.py` | `main()`'s body after `backend.open()` moves inside one `try/except KeyboardInterrupt/finally`; the three `backend.close()` calls become one |
| `tests/test_o2_shroom.py` | source-inspection test asserting exactly one `backend.close()` |
| `harness/run_stack.py` | `setup_seconds` default 20.0 to 90.0 in **both** declaration sites, with the measurement in the help text |
| `tests/test_run_stack.py` | assert the new default |
| `docs/MM_TERRARIUM.md` | live-run results and two corrections to the clock-sync entry |

**Create:** nothing.

---

## Task 1: One guaranteed cleanup path in `o2_shroom.main()`

**Why this matters.** `sigterm_as_keyboard_interrupt()` is installed at line 203, but the `try:` does not begin until line 283. Those 70 lines contain four blocking operations, and a SIGTERM in any of them raises `KeyboardInterrupt` with no handler in scope: an unhandled traceback, and `backend.close()` never runs. Observed live on 2026-08-14. The clock-sync wait is where a device sits when the upstream defect bites, so it is the likeliest place in the program to be signalled and the one path the existing fix does not cover.

**Files:**
- Modify: `harness/o2_shroom.py:211-343`
- Test: `tests/test_o2_shroom.py`

**Interfaces:**
- Consumes: `harness.signals.sigterm_as_keyboard_interrupt()` (already installed at line 203, unchanged); `harness.o2_shroom.parent_is_gone`, `service_conflict`, `_report_latency` (all unchanged).
- Produces: no new public names. `main()` keeps its signature and its exit-code contract.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_o2_shroom.py`:

```python
def test_main_has_exactly_one_backend_close():
    """main() used to close the backend by hand at each exit path -- the
    parent-gone return, the service-conflict SystemExit, and the tick loop's
    finally -- with SIGTERM as a forgotten fourth. A KeyboardInterrupt raised
    anywhere between backend.open() and the tick loop's try: therefore left
    the WebSim backend open and printed an unhandled traceback, which is
    exactly what a live run produced on 2026-08-14.

    Asserted by source inspection rather than by running main(), because
    main() imports o2litepy, which is absent from this offline suite by
    design. The same technique and the same reason as tests/test_signals.py.

    One close call means one cleanup path. A second appearing is the
    regression this guards."""
    import inspect

    import harness.o2_shroom

    source = inspect.getsource(harness.o2_shroom)
    assert source.count("backend.close()") == 1, (
        "harness/o2_shroom.py must close its backend in exactly one place. "
        "Per-exit-path cleanup is how the SIGTERM path got missed.")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_o2_shroom.py::test_main_has_exactly_one_backend_close -q
```

Expected: FAIL, `assert 3 == 1`.

- [ ] **Step 3: Restructure `main()`**

In `harness/o2_shroom.py`, everything from line 216 (`o2lite.initialize(args.ensemble)`) to line 343 (`backend.close()`) moves inside one guard. The three exit paths change as follows, and **nothing else about their behaviour changes**:

1. The `parent_is_gone` branch in the clock-sync loop (lines 242-245) drops its `backend.close()` and keeps its `print` and `return`. The `return` still runs the `finally`.
2. The `service_conflict` branch (lines 254-257) drops its `backend.close()` and keeps its `print` and `raise SystemExit(1)`. `SystemExit` is a `BaseException`, so it passes through `except KeyboardInterrupt` untouched, still exits 1, and now gets its cleanup from the `finally`.
3. The tick loop's existing `try/except KeyboardInterrupt/finally` (lines 283-343) is subsumed by the new outer one; its inner `try:` and `except KeyboardInterrupt: pass` are removed, and its `finally` body becomes the outer `finally`.

The result, with the unchanged middle elided:

```python
    client, backend = build(args.dev, args.node,
                            args.sim_host, args.sim_port)
    backend.open()
    print(f"Watch the Shroom at http://{args.sim_host}:{backend.port}/")

    # ONE cleanup path, covering everything after backend.open(). The guard
    # starts here and not at the tick loop because every step between is
    # interruptible: o2lite.initialize() blocks on mDNS discovery,
    # set_services() rides the same socket, the clock-sync wait below spins
    # until the hub answers, and service_conflict() polls a self-addressed
    # nonce against a timeout. A SIGTERM in any of them used to raise
    # KeyboardInterrupt with no handler in scope, printing a traceback and
    # leaving the WebSim backend open.
    #
    # That is not a hypothetical. The clock-sync wait is exactly where a
    # device sits when the upstream /host/clear defect bites (see
    # docs/MM_TERRARIUM.md, "A device cannot clock-sync to Arco after
    # Control has connected"), so it is the likeliest place in this program
    # to be signalled -- and it was the one path the SIGTERM handler did not
    # protect. Measured live on 2026-08-14.
    try:
        o2lite.initialize(args.ensemble)
        o2lite.set_services(args.dev)      # the device offers its own ie<N>

        def on_down(address, typespec, info):
            """o2litepy handler: THREE parameters, and `address` has already
            had its leading '/' stripped. Arguments are pulled in typespec
            order, not handed over as a list."""
            try:
                values = pull_args(o2lite, typespec or "")
            except Exception:
                # Mirrors devicelink/o2_transport.py's _on_message
                # diagnostic, but print rather than logging: this module has
                # no logging setup, and every other operator-facing line here
                # (the watch URL, the clock-synced line, the frames-displayed-
                # late count) is already print, so that is what a person
                # running this tool will actually see.
                print(f"dropping /{address}: unreadable arguments")
                return                      # drop the frame, never raise
            client.handle({"timestamp": o2lite.msg_timestamp,
                           "address": f"/{address}",
                           "typespec": typespec or "", "args": values})

        for kind in ("role", "leds", "release", "deny", "error"):
            o2lite.method_new(f"/{args.dev}/{kind}", None, True, on_down, None)

        while o2lite.time_get() < 0:       # block until clock sync
            if parent_is_gone(args.exit_with_parent):
                print("parent is gone; exiting before clock sync")
                return                     # the finally below still runs
            o2lite.poll()
            time.sleep(0.01)
        print(f"{markers.DEVICE_CLOCK_SYNCED} {o2lite.time_get():.3f}",
              flush=True)

        # The service announcement went out at set_services time and was
        # never acknowledged. Check it actually took before serving a canvas
        # that would otherwise stay dark for the whole run with no
        # explanation.
        problem = service_conflict(o2lite, args.dev)
        if problem is not None:
            print(problem, file=sys.stderr)
            # SystemExit is a BaseException, so it passes through the
            # except below untouched and still exits 1 -- it just gets its
            # cleanup from the finally now instead of by hand.
            raise SystemExit(1)

        o2lite.send_cmd("/game/hello", 0, "s", args.dev)
        if not args.no_join:
            o2lite.send_cmd("/game/join", 0, "ss", args.dev, args.node)

        # ... unchanged: start, interval, next_tilt, deny_printed,
        # error_printed, next_join, joins_sent, and the whole
        # `while not client.released:` loop ...

    except KeyboardInterrupt:
        pass
    finally:
        print(f"frames displayed late: {client.clamped}")
        _report_latency(client, args.control_horizon, args.samples_out)
        backend.close()
```

**Two things to get right while moving code.** The whole block from `start = o2lite.time_get()` through the end of the `while not client.released:` loop keeps its existing body verbatim, only re-indented one level. And the loop's own `if parent_is_gone(...): print("parent is gone; exiting"); break` stays as a `break`, not a `return` -- breaking out of the loop falls through to the `finally` the same way and preserves the existing flow.

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_o2_shroom.py -q
```

Expected: all PASS, including the new test.

- [ ] **Step 5: Prove the module still parses and the exit paths are intact**

The test only counts a string, so confirm the restructure is real:

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -c "
import ast, inspect, harness.o2_shroom as m
src = inspect.getsource(m)
ast.parse(src)
main = [n for n in ast.parse(src).body if getattr(n, 'name', '') == 'main'][0]
tries = [n for n in ast.walk(main) if isinstance(n, ast.Try)]
print('Try blocks in main():', len(tries))
print('backend.close() count:', src.count('backend.close()'))
print('SystemExit still raised:', 'raise SystemExit(1)' in src)
"
```

Expected: the module parses; `backend.close()` count is 1; `SystemExit still raised: True`. There will be two `Try` blocks in `main()` -- the new outer one and `on_down`'s inner `try/except Exception` around `pull_args`, which is unrelated and must stay.

- [ ] **Step 6: Run the whole suite**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
```

Expected: PASS, 730 passed, 1 skipped (729 baseline plus 1 new test).

- [ ] **Step 7: Commit**

```bash
git add harness/o2_shroom.py tests/test_o2_shroom.py
git commit -m "fix(terrarium): one cleanup path in o2_shroom, so a signalled device closes

sigterm_as_keyboard_interrupt() is installed at the top of main(), but the
try block did not begin until the tick loop 70 lines later. Everything in
between blocks on something: o2lite.initialize() on mDNS discovery,
set_services() on the same socket, the clock-sync wait on the hub, and
service_conflict() on a self-addressed nonce. A SIGTERM in any of them
raised KeyboardInterrupt with no handler in scope, printing an unhandled
traceback and leaving the WebSim backend open.

Measured live on 2026-08-14, twice, and the sting is that it is not a
corner: the clock-sync wait is exactly where a device sits when the
upstream /host/clear defect bites, so it is the likeliest place in the
program to be signalled and it was the one path the handler did not cover.

The three hand-written backend.close() calls -- one per exit path, with
SIGTERM as the forgotten fourth -- collapse into one finally. That is the
same enumerated-cleanup-versus-structural-guarantee argument the teardown
work already made, applied to the file that still had it.

Behaviour is otherwise unchanged: KeyboardInterrupt still exits cleanly,
service_conflict's SystemExit(1) still exits 1 (BaseException passes
through the except untouched), and the parent-gone return still reports.

The test asserts by source inspection that exactly one backend.close()
remains, because main() imports o2litepy and is unreachable from the
offline suite. Same technique and same reason as tests/test_signals.py. It
catches a regression to per-exit cleanup; it does not prove the signal
path, which only a live run does."
```

---

## Task 2: The SETUP window stops being the binding constraint

**Why this matters.** On the live run, Control connected at O2time 7.806 and the device clock-synced at 30.04, so device cold start (python, luxaeterna import, WebSim backend, o2lite discovery, sync) is about 22s. `run_stack` spawns devices only after Control reports SETUP, so the whole cold start burns the window. At the 20s default the window closed first and the device was refused with `JOIN DENIED: registration closed for scored roles`, because `player` is a scored role and `RegistrationState.join()` refuses those once RUNNING.

**Files:**
- Modify: `harness/run_stack.py:66` and `harness/run_stack.py:378-381`
- Test: `tests/test_run_stack.py`

**Interfaces:**
- Consumes: `harness.run_stack.StackConfig`, `config_from_args`, `parse_args` (all existing).
- Produces: no new names. `StackConfig.setup_seconds` default becomes `90.0`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_stack.py`:

```python
def test_the_setup_window_clears_a_measured_device_cold_start():
    """A live run on 2026-08-14 measured device cold start at about 22s:
    Control connected at O2time 7.806 and the device clock-synced at 30.04.
    run_stack spawns devices only AFTER Control reports SETUP, so that whole
    cold start burns the window. At the old 20s default the window closed
    first and the device was refused -- `player` is a scored role, and
    RegistrationState.join() refuses scored roles once RUNNING.

    Asserted on both declaration sites, because the dataclass default and
    the argparse default are separate and only the argparse one reaches a
    CLI run."""
    from harness.run_stack import StackConfig, config_from_args, parse_args

    measured_cold_start = 22.0

    assert StackConfig(log_dir="x").setup_seconds > measured_cold_start
    assert config_from_args(parse_args([])).setup_seconds > measured_cold_start
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_run_stack.py::test_the_setup_window_clears_a_measured_device_cold_start -q
```

Expected: FAIL, `assert 20.0 > 22.0`.

- [ ] **Step 3: Raise the dataclass default**

`harness/run_stack.py:66`, inside `StackConfig`:

```python
    # 90s, not 20s. A live run on 2026-08-14 measured device cold start at
    # about 22s (python, luxaeterna import, WebSim backend, o2lite discovery,
    # clock sync), and devices are spawned only AFTER Control reports SETUP,
    # so the whole cold start burns this window. At 20s it closed first and
    # the device was refused: `player` is a scored role, and
    # RegistrationState.join() refuses scored roles once RUNNING.
    setup_seconds: float = 90.0
```

- [ ] **Step 4: Raise the argparse default, with the derivation in the help**

Replace `harness/run_stack.py:378-381`:

```python
    ap.add_argument("--setup-seconds", type=float, default=90.0,
                    help="How long Control holds registration open. A "
                         "device must join a SCORED role inside this "
                         "window or be refused. Default 90s, not a round "
                         "number: a live run measured device cold start at "
                         "~22s, and devices only spawn once Control reports "
                         "SETUP, so the cold start is inside this window.")
```

**Do not skip either site.** `config_from_args` builds `StackConfig(..., setup_seconds=args.setup_seconds, ...)`, so the argparse default is what a CLI run gets and the dataclass default is what direct construction gets. Changing one leaves the other stale, and the test above asserts both.

- [ ] **Step 5: Run the test to verify it passes**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_run_stack.py -q
```

Expected: all PASS.

- [ ] **Step 6: Run the whole suite**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
```

Expected: PASS, 731 passed, 1 skipped.

- [ ] **Step 7: Commit**

```bash
git add harness/run_stack.py tests/test_run_stack.py
git commit -m "fix(terrarium): a SETUP window that clears a measured device cold start

A live run on 2026-08-14 died with 'JOIN DENIED: registration closed for
scored roles'. Control connected at O2time 7.806 and the device clock-synced
at 30.04, so device cold start is about 22s: python, luxaeterna import,
WebSim backend, o2lite discovery, sync. run_stack spawns devices only after
Control reports SETUP, so that whole cold start sits inside the window, and
the 20s default closed it first. `player` is a scored role, and
RegistrationState.join() refuses scored roles once RUNNING.

90s, with the measurement in the flag's help so the number has a derivation
rather than being a fresh guess. Not scaled by --devices: devices spawn
concurrently, so cold start is roughly constant in device count, and a
formula nobody has measured would be worse than a measured constant.

Both declaration sites move together. config_from_args feeds the argparse
default into StackConfig, so the argparse one is what a CLI run gets and the
dataclass one is what direct construction gets; the test asserts both.

This does NOT make a live run go green. Two of that day's three runs failed
on clock sync, not on the window, and that defect is upstream."
```

---

## Task 3: What the deep-dive learns from the live run

**Files:**
- Modify: `docs/MM_TERRARIUM.md`

**Interfaces:** none. Documentation only.

- [ ] **Step 1: Confirm the suite is untouched before starting**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
```

Expected: 731 passed, 1 skipped. This task changes no code and must not move that number.

- [ ] **Step 2: Correct the clock-sync entry's two now-wrong claims**

Find the *Not yet built / deferred* bullet beginning **"A device cannot clock-sync to Arco after Control has connected"**. Two statements in it are now contradicted by measurement, and both must be corrected in place rather than appended to, so a reader does not meet the old claim first:

1. It says a new client, after one pyarco `initialize()`, "does not sync at all". Measured 2026-08-14 via `harness/run_stack.py`: **1 of 3 runs synced**, at O2time 30.04. Change the claim from deterministic to intermittent, and say what the ratio was. This matters for how a reader treats a single successful run.
2. It describes the connect-first half's dead socket as silent and unnoticeable. That is no longer true: `verify_service_ownership` (`devicelink/o2_transport.py`) landed in PR #24, after that entry was written, and a dead socket now fails loud with `FATAL: service ... is not routed back to this process`. The **defect** is unchanged; its **observability** is not. Say exactly that, so nobody reads it as the defect being fixed.

Leave the rest of the entry alone, including the "no ordering that reliably works from a cold start" conclusion, which the live run did not contradict.

- [ ] **Step 3: Record that the runner has been exercised live**

In the `harness/run_stack.py` bullet of the teardown-and-runner section, add what the 2026-08-14 run established. Keep it to what was measured:

- Three runs, one device each. No run reached a granted role.
- **Teardown reaped every process, all three times, zero orphans**, including on the two failing runs. That is the primary claim of the teardown work, and this is the first time it was exercised outside fakes.
- Arco spawned on a pty and came up with audio (`Audio open completed successfully`, `Audio latency = 10 ms`); `--arco-log` captured its curses output, which is what made the failures diagnosable at all.
- `o2debug.log` recorded zero dropped messages and zero service-provider refusals across all three runs.
- CI mode bounded and named both failure modes and exited non-zero rather than hanging.
- The two failures: one `JOIN DENIED` from the SETUP window (fixed in Task 2), two clock-sync failures (upstream, not fixed).

Do not claim the runner works end to end. It has not produced a green run.

- [ ] **Step 4: Check style**

```bash
grep -c "—" docs/MM_TERRARIUM.md
```

Note the count before your edits and confirm you did not increase it. The repo's prose uses `--`.

- [ ] **Step 5: Run the suite and commit**

```bash
PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests -q
git add docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): what the first live run_stack run measured

Three runs against a live Arco on 2026-08-14, one device each. No run
reached a granted role, and the entry says so.

What held, none of it previously exercised outside fakes: teardown reaped
every process all three times with zero orphans, including on the two
failing runs; Arco spawned on a pty and came up with audio; --arco-log
captured its curses output; o2debug.log stayed clean at zero dropped
messages; and CI mode bounded and named both failures rather than hanging.

Two corrections to the clock-sync entry, both from measurement. It said a
new client 'does not sync at all' after a reset -- 1 of 3 runs synced, so
that is intermittent, not deterministic, which changes how a reader should
treat a single successful run. And it describes the connect-first dead
socket as silent, which verify_service_ownership made false when it landed
in PR #24, after that entry was written. The defect is unchanged; its
observability is not."
```

---

## Self-Review

**Spec coverage.** Spec §2 (Fix A) to Task 1. §3 (Fix B) to Task 2. §4 (Fix C) to Task 3. §6's testing requirements are inside each task: the source-inspection test in Task 1 Step 1, the default assertion in Task 2 Step 1, and Task 3's prose-reviewed-not-tested is reflected in its having no test step. §5's "does not make a live run go green" appears in Task 2's commit body and Task 3 Step 3's closing instruction. §7's five success criteria map to: Task 1 Steps 3 and 5 (criteria 1 and 2), Task 2 Steps 3 and 4 (criterion 3), Task 3 Steps 2 and 3 (criterion 4), and every task's suite step (criterion 5).

**Placeholder scan.** No TBD, TODO, "handle edge cases", or "similar to Task N". Task 1 Step 3 elides the unchanged tick-loop body with an explicit instruction that it moves verbatim and re-indents, rather than asking the implementer to invent it.

**Type consistency.** `setup_seconds` is a `float` in both declaration sites and both assertions. `backend.close()` is spelled identically in the implementation, the test, and the verification command. `StackConfig(log_dir="x")` in Task 2's test matches the dataclass's one required field.

**Ordering note.** The three tasks are independent and could run in any order, but they are numbered so the suite count rises monotonically (729, 730, 731) and each task's expected count is checkable against the previous one.
