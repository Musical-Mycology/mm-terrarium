# Operator/Harness Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A held Terrarium stack keeps its own hub alive, yields to the operator instead of crashing, distinguishes a blocked hub from a service conflict, and tells the operator what devices are doing.

**Architecture:** Four code tasks, none touching `control/engine.py`. Task 1 reworks `_wait_in_setup` (pty drain, state watch, reason return, countdown) and `main()`'s handoff. Task 2 gives the ownership probe a resend window. Task 3 hardens `o2_shroom` (reconnect re-verify, join hinting, identity, dark notice). Task 4 adds device-lifecycle logging via an engine observer plus a `DeviceLinkAgent` deny sink. Task 5 is live verification.

**Tech Stack:** Python 3.14, existing test conventions (injected clocks/sleeps, fakes no more permissive than the real thing).

**Spec:** `docs/superpowers/specs/2026-08-20-operator-harness-handoff-design.md`

## Global Constraints

- **Branch:** `claude/operator-harness-handoff` in the worktree `/Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20` (stacked on the open PR #38 branch).
- Run the suite as `.venv/bin/python -m pytest tests -q` from the worktree root. **There is no bare `python`; `python3` produces a phantom failure in `tests/test_terrarium_boot.py`.** Baseline entering: **1076 passed, 1 skipped**.
- **`control/engine.py` is not modified.** All fixes live in `harness/` and `devicelink/`.
- The markers contract holds: every string in `harness/markers.py` is still emitted verbatim; new output lines are additive. `tests/test_markers.py` must stay green.
- `harness/devicelink_smoke.py`'s own `_wait_in_setup` (the websocket path) is **out of scope**: it has no Arco and no Console in its runs.
- Commit with explicit paths. Never `git add -A` or `git add .`.
- Test doubles must never be more permissive than the thing they stand for.

---

## File Structure

| File | Change | Task |
|------|--------|------|
| `harness/terrarium_boot.py` | `_wait_in_setup` rework + `main()` handoff + countdown | 1 |
| `tests/test_terrarium_boot.py` | 4 updated + new hold-loop tests | 1 |
| `devicelink/o2_transport.py` | probe resend window + reworded error | 2 |
| `tests/test_o2_transport.py` (or the existing transport test file) | resend tests | 2 |
| `harness/o2_shroom.py` | reconnect re-verify, join hint, `surface_id=dev`, dark notice | 3 |
| `tests/test_o2_shroom.py` (or existing) | new behavior tests | 3 |
| `harness/terrarium_boot.py` + `devicelink/agent.py` | lifecycle observer + `on_join_denied` sink | 4 |
| `tests/test_terrarium_boot.py`, `tests/test_devicelink_agent.py` (or existing) | lifecycle tests | 4 |

Implementers: where this plan names a test file that does not exist under that exact name, put the tests in the file that already covers that module (find it with `grep -rln <module> tests/`) and report the actual file used.

---

### Task 1: The hold loop drains Arco, watches the engine, and counts down

**Files:**
- Modify: `harness/terrarium_boot.py` (`_wait_in_setup` at ~237, `main()` at ~600-612)
- Test: `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `_wait_in_setup(agent, setup_seconds, clock=..., sleep=..., parent_pid=None, console_agent=None, arco=None, gs=None) -> str`, returning `"expired" | "parent-gone" | "state-changed"`. Task 4's observer does not depend on this, but its printing joins the same stdout.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_terrarium_boot.py` (adapt the existing `FakeAgent`/tick-clock idioms in that file; the four existing `_wait_in_setup` tests at ~463-536 will be UPDATED in Step 3, not deleted):

```python
def test_wait_in_setup_drains_arco_every_iteration():
    """The 2026-08-20 freeze: nothing drained Arco's pty during the hold,
    so Arco blocked mid-write and the whole room froze (0-byte arco.log
    tee 11 minutes after spawn, static o2debug.log, no drone at RUNNING).
    Every loop that holds while Arco is alive must drain Arco's pty."""
    from harness.terrarium_boot import _wait_in_setup

    class FakeArco:
        def __init__(self):
            self.polls = 0
        def poll(self):
            self.polls += 1
            return None                      # still running

    ticks = iter([0.0, 0.1, 0.2, 0.3, 0.4, 1.1, 1.2])
    arco = FakeArco()
    reason = _wait_in_setup(_FakeAgent(), 1.0, clock=lambda: next(ticks),
                            sleep=lambda s: None, arco=arco)
    assert reason == "expired"
    assert arco.polls >= 4          # once per iteration, not once total


def test_wait_in_setup_returns_state_changed_when_the_engine_leaves_setup():
    """The 2026-08-20 crash: the operator pressed Run on the Console during
    the hold, and main() then called gs.run() into a RUNNING engine ->
    InvalidTransition killed the harness. The hold must yield instead."""
    from control.state import State
    from harness.terrarium_boot import _wait_in_setup

    class FakeGs:
        def __init__(self):
            self.state = State.SETUP

    gs = FakeGs()
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        if calls["n"] == 3:
            gs.state = State.RUNNING         # operator clicks Run mid-hold
        return calls["n"] * 0.1

    reason = _wait_in_setup(_FakeAgent(), 10.0, clock=clock,
                            sleep=lambda s: None, gs=gs)
    assert reason == "state-changed"


def test_wait_in_setup_yields_on_abort_too():
    from control.state import State
    from harness.terrarium_boot import _wait_in_setup

    class FakeGs:
        state = State.IDLE                   # operator aborted instantly

    reason = _wait_in_setup(_FakeAgent(), 10.0, clock=iter(
        [0.0, 0.1]).__next__, sleep=lambda s: None, gs=FakeGs())
    assert reason == "state-changed"


def test_wait_in_setup_prints_a_countdown(capsys):
    from harness.terrarium_boot import _wait_in_setup

    t = {"now": 0.0}
    def clock():
        t["now"] += 4.0                      # 4s per iteration
        return t["now"]
    _wait_in_setup(_FakeAgent(), 60.0, clock=clock, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert "SETUP open," in out
    assert out.count("SETUP open,") >= 2     # every ~15s across 60s
```

If the file's existing fake agent class has a different name, use that name; `_FakeAgent` here stands for "the file's existing minimal agent fake with a poll() method".

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -k wait_in_setup -v`
Expected: the four NEW tests fail with `TypeError: _wait_in_setup() got an unexpected keyword argument` (for `arco`/`gs`) or missing-countdown assertions; the four OLD tests still pass.

- [ ] **Step 3: Implement**

Rework `_wait_in_setup` (keep the existing docstring's content, extend it):

```python
def _wait_in_setup(agent, setup_seconds: float, clock=time.monotonic,
                   sleep=time.sleep, parent_pid: int | None = None,
                   console_agent=None, arco=None, gs=None) -> str:
    """... existing docstring, plus:

    arco, when given, is drained every iteration. Every loop that holds
    while Arco is alive must drain Arco's pty: Arco is a curses app, an
    undrained pty blocks it mid-write, and a blocked Arco serves no clock
    sync, routes no messages and plays no audio. This loop not draining
    it froze whole rooms for the length of the hold (2026-08-20).

    gs, when given, is watched: the Console is a second driver, and if
    the operator moves the engine out of SETUP (Run, Abort) this hold
    yields immediately instead of letting main() call run() into a
    RUNNING engine.

    Returns "expired", "parent-gone", or "state-changed".
    """
    if setup_seconds <= 0:
        return "expired"
    from control.state import State
    start = clock()
    deadline = start + setup_seconds
    next_countdown = start + 15.0
    while True:
        now = clock()
        if now >= deadline:
            return "expired"
        if parent_is_gone(parent_pid):
            return "parent-gone"
        if arco is not None:
            arco.poll()
        agent.poll()
        if console_agent is not None:
            console_agent.poll()
        if gs is not None and gs.state is not State.SETUP:
            return "state-changed"
        if now >= next_countdown:
            print(f"SETUP open, {deadline - now:.0f}s remaining", flush=True)
            next_countdown = now + 15.0
        sleep(1.0 / 44.0)
```

Note the loop structure change: the deadline check moves to the top so a
`"state-changed"` cannot be missed on the final iteration, and `import
State` is function-scoped to keep the module's import graph unchanged.

Update the FOUR existing tests in place: the two that assert a boolean
(`parent_gone is True` / `is False` or equivalent) now assert
`== "parent-gone"` and `== "expired"`; the two no-op/deadline tests assert
`== "expired"`. Do not weaken what they verify.

Then `main()` (~line 603):

```python
        reason = _wait_in_setup(agent, args.setup_seconds,
                                parent_pid=args.exit_with_parent,
                                console_agent=console_agent,
                                arco=arco, gs=gs)
        if reason == "parent-gone":
            print("parent is gone; tearing down", file=sys.stderr)
        else:
            if gs.state is State.SETUP:
                gs.run()
            else:
                # The operator drove the engine from the Console during the
                # hold. That is a handoff, not an error: run() from here
                # would raise InvalidTransition into a live room.
                print("operator changed state from the Console; "
                      "skipping harness run()", flush=True)
            reason = _serve_until_done(gs, agent, arco,
                                       parent_pid=args.exit_with_parent,
                                       console_agent=console_agent)
            ...  # existing reason handling unchanged
```

Keep every existing print and marker byte-identical.

- [ ] **Step 4: Run the module's tests**

Run: `cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests/test_terrarium_boot.py tests/test_markers.py -v 2>&1 | tail -15`
Expected: all pass, including the four updated tests and `test_markers.py` untouched-markers checks.

- [ ] **Step 5: Full suite**

Run: `cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20 && .venv/bin/python -m pytest tests -q 2>&1 | tail -2`
Expected: 1080 passed, 1 skipped (1076 + 4 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/sweet-swirles-296e20
git add harness/terrarium_boot.py tests/test_terrarium_boot.py
git commit -m "fix(harness): the SETUP hold drains Arco, yields to the operator, and counts down

_wait_in_setup never called arco.poll(), so for the whole hold nothing
drained Arco's pty; Arco (a curses app) blocked mid-write and the room
froze: no clock sync, no routing, no audio, until _serve_until_done's
draining thawed it at hold expiry. Verified live 2026-08-20: a 0-byte
arco.log tee eleven minutes after spawn while the engine sat RUNNING with
no drone. Devices were never slow to sync; they were held hostage.

The hold also now watches the engine and returns 'state-changed' the
moment the operator moves it off SETUP from the Console, and main() calls
gs.run() only when the state is still SETUP -- the InvalidTransition
crash that killed a live session becomes a logged handoff.

Plus a 15s countdown so a closing join window is visible before a device
gets denied by it."
```

---

### Task 2: The ownership probe outwaits a blocked hub

**Files:**
- Modify: `devicelink/o2_transport.py` (`verify_service_ownership` at ~121, `start()` at ~293-330)
- Test: the existing transport test file (`grep -rln verify_service_ownership tests/`)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `verify_service_ownership(..., timeout=2.0, resend_interval=None, ...)`; `O2LiteTransport.start(..., ownership_timeout=10.0)` where the timeout is now the TOTAL window with 2s resends.

- [ ] **Step 1: Write the failing tests**

In the transport test file, using its existing fake-o2lite idiom (the fake must dispatch only on `poll()`, per the repo's rule):

```python
def test_ownership_probe_resends_and_accepts_a_late_reply():
    """2026-08-19/20, five occurrences: a hub blocked in its cold audio
    open (or frozen on an undrained pty) cannot answer inside 2s, and the
    old single-shot probe misdiagnosed that as a service conflict on a
    host proven clean. A blocked hub answers when it unblocks; a genuine
    second claimant never answers. Waiting distinguishes them."""
    fake = _FakeO2LiteAnsweringAfter(sends=2)   # ignores the 1st svcheck
    t = {"now": 0.0}
    def clock(): return t["now"]
    def sleep(s): t["now"] += s
    ok = verify_service_ownership(fake, "game", timeout=10.0,
                                  resend_interval=2.0,
                                  clock=clock, sleep=sleep)
    assert ok is True
    assert fake.svcheck_sends >= 2              # it actually resent


def test_ownership_probe_still_fails_when_nothing_ever_answers():
    fake = _FakeO2LiteNeverAnswers()
    t = {"now": 0.0}
    def clock(): return t["now"]
    def sleep(s): t["now"] += s
    ok = verify_service_ownership(fake, "game", timeout=10.0,
                                  resend_interval=2.0,
                                  clock=clock, sleep=sleep)
    assert ok is False
    assert fake.svcheck_sends >= 4              # kept trying to the end


def test_start_failure_message_names_the_blocked_hub_first():
    ...  # drive start() to failure with the never-answering fake and
         # assert the RuntimeError text contains "blocked" (cold audio
         # open / undrained pty) BEFORE it mentions an orphaned claimant,
         # and still cites o2debug.log.
```

Write the two fakes in the test file, shaped after the existing fake:
`svcheck_sends` counts `send_cmd` calls to `/game/_svcheck`; the answering
fake enqueues the reply only after N sends and dispatches it on `poll()`.

- [ ] **Step 2: Run to verify they fail** (`resend_interval` unexpected kwarg; old message text).

- [ ] **Step 3: Implement**

In `verify_service_ownership`: add `resend_interval: float | None = None`;
inside the wait loop, when `resend_interval` is set and `clock()` passes
the next resend mark, `send_cmd` the same nonce again. Docstring gains:
resends are safe because the nonce is fixed, so a late reply to an early
send is indistinguishable from a fresh one, and that is correct here.

In `start()`: default `ownership_timeout` becomes `10.0`, passed as
`timeout=ownership_timeout, resend_interval=2.0`. Replace the error text:

```python
            raise RuntimeError(
                "the `game` service did not route back to this connection "
                f"within {ownership_timeout:.0f}s. Most likely the hub is "
                "blocked and cannot answer yet: a cold audio-device open "
                "blocks Arco for seconds after idle, and an undrained pty "
                "freezes it entirely (see the SETUP-hold drain rule in "
                "harness/terrarium_boot.py). The rarer cause is a genuine "
                "second claimant already offering `game` -- O2 refuses "
                "silently (o2/src/bridge.cpp:231-237) and logs only on the "
                "hub. Check o2debug.log: a frozen hub shows no recent "
                "lines at all; a conflict shows this connection's own "
                "`sv` being refused.")
```

- [ ] **Step 4: Module tests** (`pytest <transport test file> -v`) -- all pass.
- [ ] **Step 5: Full suite** -- expected 1083 passed, 1 skipped (1080 + 3).
- [ ] **Step 6: Commit**

```bash
git add devicelink/o2_transport.py tests/<actual transport test file>
git commit -m "fix(o2): the ownership probe outwaits a blocked hub before crying conflict

Five live occurrences misdiagnosed as a service conflict on hosts proven
clean (no local process, no LAN hub, no hub-side refusal logged). The
real causes were a hub blocked in its cold audio open and, upstream of
the Task 1 fix, a hub frozen on an undrained pty. A genuine second
claimant never answers; a blocked hub answers when it unblocks, so the
probe now resends every 2s across a 10s window, and the error names the
blocked-hub cause first with the hub-side evidence to check for each."
```

---

### Task 3: `o2_shroom` survives reconnects and tells the truth

**Files:**
- Modify: `harness/o2_shroom.py`
- Test: the existing o2_shroom test file (`grep -rln o2_shroom tests/`)

**Interfaces:**
- Consumes: `verify_service_ownership(..., resend_interval=...)` from Task 2 (it already imports it lazily at ~93).
- Produces: no API change; behavior only.

Four changes, each small:

1. **Reconnect re-verification.** In the tick loop, remember
   `o2.bridge_id`; when it changes (o2litepy auto-reconnected and got a
   new id), print `reconnected to the hub (bridge id {old} -> {new}); `
   `re-verifying service` and re-run the same service check the startup
   path uses. A failed re-check hits the existing `FATAL: service` path.
   Evidence: 2026-08-20, a device reconnected during a stack transition
   lost its `ie1` announcement and fifteen Control replies were dropped
   hub-side as `/ie1/deny ... service was not found` while the device saw
   pure silence.
2. **Unanswered-join hinting.** In the join-retry loop (~line 360), when
   `joins_sent` reaches 5 with no role and no deny, replace the current
   `is Control up and in SETUP?` line with:
   `5 joins unanswered. Either Control is not up yet, or this device's `
   `service announcement was lost (check o2debug.log on the hub for `
   `"/{dev}/... service was not found").` -- keep printing every 5 joins.
   The markers in `harness/markers.py` do not include this string; verify
   nothing in `tests/test_markers.py` matches the old wording before
   changing it, and if something does, update both sides in the same
   commit.
3. **Identity.** Line ~148: `shroom_capability()` becomes
   `shroom_capability(surface_id=dev)`, so the canvas header stops
   claiming every device is `ie0`.
4. **Dark-by-design notice.** Where `client.config` is first set (role
   granted), if `not (client.config.get("light_manifest") or {}).get("instruments")`,
   print `role has no light declaration -- canvas stays dark by design`.
   Evidence: TestBit's `jammer` is deliberately light-less and its black
   canvas was reported as a failure.

- [ ] **Step 1: Write failing tests** -- one per change, in the module's
  existing socket-free style (`ShroomClient.handle()` is testable without
  a socket; the tick-loop changes need the smallest driver the file's
  existing tests use). For (1), a fake o2 whose `bridge_id` changes
  between ticks must trigger exactly one re-verification; for (2) assert
  the printed hint names the dev's own service; for (3) assert
  `build(...)[1]._cap.surface_id == dev` (or via the capability the
  backend was constructed with); for (4) drive `handle()` with a role
  config whose `light_manifest` is `{}` and assert the notice, then with
  a real instrument list and assert silence.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement the four changes.**
- [ ] **Step 4: Module tests pass; `tests/test_markers.py` still green.**
- [ ] **Step 5: Full suite** -- expected 1087 passed, 1 skipped (1083 + 4).
- [ ] **Step 6: Commit**

```bash
git add harness/o2_shroom.py tests/<actual o2_shroom test file>
git commit -m "fix(o2_shroom): re-verify after reconnect, and stop misleading the operator

A device auto-reconnected mid-stack-transition lost its service
announcement and heard nothing forever: fifteen Control replies dropped
hub-side as 'service was not found' while the device's one-shot startup
check had passed against the previous hub. The check now re-runs on any
bridge-id change.

Three lies to the operator fixed alongside: the unanswered-join message
pointed at Control when Control was healthy (it now names the lost-
service cause and the hub log line to check); every canvas header read
ie0 regardless of identity (surface_id=dev); and a deliberately
light-less role rendered a black canvas indistinguishable from the
broken kind (it now says so)."
```

---

### Task 4: The operator sees the device lifecycle on Control's stdout

**Files:**
- Modify: `devicelink/agent.py` (optional `on_join_denied` constructor sink, guarded at its call site like `on_room_frame`)
- Modify: `harness/terrarium_boot.py` (a `_LifecycleLogger` engine observer + the deny-sink printer, wired in `build()`/`main()`)
- Test: the existing agent and terrarium_boot test files

**Interfaces:**
- Consumes: Task 1's merged `terrarium_boot` (same file; sequential tasks avoid the conflict).
- Produces: `DeviceLinkAgent(..., on_join_denied=None)` calling `on_join_denied(dev, node, reason)` wherever the agent sends a deny; a `_LifecycleLogger` with `on_devices_change`/`on_registration_change`/`on_release` printing the spec's lines.

Design constraints for the implementer:

- The engine observer derives `device hello:` lines by diffing the device
  pool it last saw against `on_devices_change`'s payload, and
  `join granted:` lines by diffing per-role counts from
  `on_registration_change` joined with the device->role view -- follow
  whatever payload those hooks actually deliver (read `ConsoleAgent` for
  the authoritative shapes; do not guess).
- Denials: find where `DeviceLinkAgent` sends the deny reply (`grep -n
  deny devicelink/agent.py`) and call the sink there, wrapped in the same
  guard style as `on_room_frame` so a raising sink cannot break the deny
  path itself.
- `control/engine.py` is not touched. If deriving a line cleanly seems to
  need an engine change, stop and report NEEDS_CONTEXT.

- [ ] **Step 1: Failing tests** -- script a hello/join/deny/release
  sequence against a `GameServer` + agent with the logger attached
  (reuse the existing agent test fixtures) and assert the four line
  shapes; assert a raising `on_join_denied` sink does not prevent the
  device from receiving its deny.
- [ ] **Step 2: Verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Module tests pass.**
- [ ] **Step 5: Full suite** -- expected ~1091 passed, 1 skipped (1087 + ~4; report the exact count).
- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py harness/terrarium_boot.py tests/<actual files>
git commit -m "feat(harness): device lifecycle on Control's stdout

Hellos, grants, denials and releases were invisible outside the Console
panel and the hub debug log; a denial appeared nowhere on the Control
side at all, which cost real diagnostic time on 2026-08-20. Hellos,
grants and releases ride a small engine observer (the ConsoleAgent
seam); denials ride a new guarded on_join_denied sink on
DeviceLinkAgent, following the on_room_frame pattern. run_stack's tee
lands all of it in control.log."
```

---

### Task 5: Live verification

**Files:** none. The acceptance gate, per spec section 4. Needs the machine free of other stacks and a human for the audio/visual checks; coordinate with the operator.

- [ ] **Step 1:** Cold start (first run after >10 min idle):
  `run_stack --room-type DEMO --devices 0 --console-port 8099 --setup-seconds 240`.
  Expected: no service-conflict error on the first attempt (Task 2), and
  `SETUP open, ...s remaining` lines ticking (Task 1).
- [ ] **Step 2:** Start `o2_shroom --dev ie1 --node TEST_PLAYER_NODE ...`
  during the hold. Expected: clock sync **within seconds** (Task 1's
  drain), then `join granted: ie1 -> player ...` on Control's stdout
  (Task 4), and the device canvas header reading `ie1` (Task 3).
- [ ] **Step 3:** Press Run on the Console during the hold. Expected:
  `operator changed state from the Console; skipping harness run()`, the
  drone starts immediately, the device animates, and **no crash** when
  the hold's original window would have expired (Task 1 handoff).
- [ ] **Step 4:** Let the round complete; the device fades and releases
  (`device released: ie1`).
- [ ] **Step 5:** Also try headless: `run_stack --room-type DEMO
  --devices 1 --ci --seconds 120`. The pty starvation was plausibly the
  whole "headless clock-sync defect" (spawned devices always synced into
  a frozen hold); if this now goes green it is the first working
  end-to-end CI run in the repo's history and the deep-dive entry gets
  rewritten. If it still fails, the residue is genuinely upstream;
  record which.

---

## After the plan

Deep-dive sync per spec section 5, and PR stacked on #38.
