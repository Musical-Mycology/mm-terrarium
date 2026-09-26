# Tier 2 Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove duplicated test doubles and production helpers in mm-terrarium without changing runtime behavior.

**Architecture:** Two stacked PRs. PR A (Tasks A1-A8, tests only) adds `tests/fakes.py` and migrates in-test doubles onto it, parametrizes near-duplicate tests, and adds a `_run_roomless` helper. PR B (Tasks B1-B8, production) adds `console/static/dom.js`, merges two boot tick loops, shares `print_bit_list`, moves `parent_is_gone` to `harness/signals.py`, unifies `parse_cc_ref`, and renames `DeviceLinkAgent.server` to `transport`.

**Tech Stack:** Python 3 + pytest (run via `.venv/bin/python`), vanilla ES-module JS tested with `node --test`.

**Spec:** `docs/superpowers/specs/2026-09-25-tier2-consolidation-design.md`

## Global Constraints

- Worktree: `/Users/chris/projects/mm-terrarium/.claude/worktrees/interesting-bhaskara-62910e`. A fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first.
- Python: `.venv/bin/python -m pytest tests -q -p no:cacheprovider`. Never bare `python3` (luxaeterna is only in `.venv`).
- JS: `node --test tests/js/*.test.js` (the bare directory form fails).
- Baseline before PR A: `2750 passed, 1 skipped`; JS 25/25 pass.
- No runtime behavior change, except the intended `--list-bits` DISABLED fix in run_stack (Task B3).
- A test that asserted something must still assert it. Bespoke doubles stay local (see Task A1).
- `control/` must not import `bits/`. `console/agent.py`'s `self.server` is a real ConsoleServer: do not rename it.
- Tests monkeypatch `harness.terrarium_boot.parent_is_gone`: boot helpers must resolve it through that module's globals.
- No em dashes in code comments, docs or commit messages.
- Branches: PR A on `claude/tier2-test-consolidation` (stacked on PR #151). PR B on `claude/tier2-prod-consolidation`, cut from PR A's tip after Task A8.

---

# Part 1: PR A, test consolidation

### Task A1: Create `tests/fakes.py` and `tests/test_fakes.py`

**Files:**
- Create: `tests/fakes.py`
- Create: `tests/test_fakes.py`

**Interfaces:**
- Produces: `tests.fakes.FakeAgent`, `tests.fakes.FakeArco`, `tests.fakes.StaticGS`,
  `tests.fakes.TickingGS`, `tests.fakes.RoomlessTerrarium`, `tests.fakes.RecordingClient`,
  `tests.fakes.FakeClock`, `tests.fakes.FakeObservable`.
- Consumes: `control.state.State`, `control.terrarium.TerrariumState` (both already exist;
  `tests/test_terrarium_boot.py` already imports both at its top).

`tests/` is already an importable package (`tests/__init__.py` exists, empty) and the convention
of a shared-fixtures module imported as `from tests.<module> import <name>` is already live
(`tests/instrument_fixtures.py`, imported that way from `tests/test_console_agent.py`). Follow
that same convention for `tests/fakes.py`.

- [ ] **Step 1: write `tests/fakes.py`**

  Create the file with exactly this content:

  ```python
  """Shared test doubles for the mm-terrarium test suite.

  Tier 2 consolidation, PR A
  (docs/superpowers/specs/2026-09-25-tier2-consolidation-design.md). Each class
  here replaces two or more near-identical local copies that used to be
  defined inside individual test functions across tests/test_terrarium_boot.py
  and other test modules.

  Bespoke doubles that only ever had ONE real use -- the closing-fade
  countdown agent, the poll-driven state-flip agent tied to a specific gs,
  FakeRegistration (two genuinely different constructor shapes), the wide
  fixed-attribute _FakeTerrarium, tests/test_run_stack.py's child-wrapping
  _RecordingPopen -- are NOT here. (test_terrarium_boot.py's own three
  identical _RecordingPopen copies become one module-level class in that
  file, Task A4 Step 5b.) They stay local to their one call site.
  """
  from __future__ import annotations

  from control.terrarium import TerrariumState


  class FakeAgent:
      """Stands in for harness/o2_shroom.py's Agent in the terrarium_boot
      poll loops. `closing` mirrors the real agent's attribute the loops
      read via `getattr(agent, "closing", 0)`. `poll_error`, when given, is
      raised (the same exception instance, every call) instead of counting
      -- use this for a test asserting a loop must never reach poll() at
      all, e.g. `FakeAgent(poll_error=AssertionError("must not poll..."))`.
      `polls` counts every call, whether or not poll_error is set."""

      def __init__(self, closing=0, poll_error=None):
          self.closing = closing
          self.poll_error = poll_error
          self.polls = 0

      def poll(self):
          self.polls += 1
          if self.poll_error is not None:
              raise self.poll_error


  class FakeArco:
      """Stands in for the Arco subprocess handle the boot loops poll().
      `returncode=None` (the default) models a live process; any other
      value models one that already exited. `poll_error`, when given, is
      raised instead of returning `returncode`. `returncode` is a plain
      public attribute, so a test may still flip it after construction
      (e.g. `arco.returncode = 1`) instead of passing it at construction
      time."""

      def __init__(self, returncode=None, poll_error=None):
          self.returncode = returncode
          self.poll_error = poll_error
          self.polls = 0

      def poll(self):
          self.polls += 1
          if self.poll_error is not None:
              raise self.poll_error
          return self.returncode


  class StaticGS:
      """A GameServer double whose `.state` never changes on its own.
      `tick_error`, when given, is raised by tick() instead of no-op'ing --
      for a loop that must never call tick() once some other condition
      (parent-gone, no-room, ...) has already fired. `ticks` counts every
      tick() call, error or not."""

      def __init__(self, state, tick_error=None):
          self.state = state
          self.tick_error = tick_error
          self.ticks = 0

      def tick(self, dt):
          self.ticks += 1
          if self.tick_error is not None:
              raise self.tick_error


  class TickingGS:
      """A GameServer double whose `.state` flips from `state` to
      `end_state` once tick() has been called `after` times (default 3,
      matching every current call site)."""

      def __init__(self, state, end_state, after=3):
          self.state = state
          self.end_state = end_state
          self.after = after
          self.ticks = 0

      def tick(self, dt):
          self.ticks += 1
          if self.ticks >= self.after:
              self.state = self.end_state


  class RoomlessTerrarium:
      """Stands in for a Terrarium in the `_serve_roomless`/
      `_restart_room_clients` supporting cast. `.arco` defaults to a fresh
      FakeArco(). `unload_room(force=False)` always appends `force` to
      `.unload_calls` (a list, so a caller that only wants a count reads
      `len(unload_calls)`); when `unload_to_no_room` is True (the default)
      it also flips `.state` to TerrariumState.NO_ROOM. Pass
      `unload_to_no_room=False` for a caller whose original local double
      never transitioned state on unload."""

      def __init__(self, unload_to_no_room=True):
          self.state = TerrariumState.ROOM_READY
          self.arco = FakeArco()
          self.unload_calls = []
          self._unload_to_no_room = unload_to_no_room

      def unload_room(self, force=False):
          self.unload_calls.append(force)
          if self._unload_to_no_room:
              self.state = TerrariumState.NO_ROOM


  class RecordingClient:
      """Stands in for a transport or pool client whose start()/stop()/
      quiesce() calls a recycle/restart test asserts an order over.
      `calls` is the list every call appends a label to -- pass the SAME
      list to a transport instance and a pool instance to get one merged
      call-order record, or a fresh list per instance to record each one
      separately. `prefix` names this instance ("pool" or "transport") in
      the recorded labels. `start_error`, when given, is raised by
      start() instead of recording.

      start() accepts an optional positional `o2` and keyword-only `pump`
      to match both real call shapes (`pool.start()` and
      `transport.start(o2, *, pump=None)`): a "transport" instance
      records the tuple `(f"{prefix}-start", o2)`; any other prefix
      records the bare string `f"{prefix}-start"`."""

      def __init__(self, calls, prefix, start_error=None):
          self.calls = calls
          self.prefix = prefix
          self.start_error = start_error

      def start(self, o2=None, *, pump=None):
          if self.start_error is not None:
              raise self.start_error
          if self.prefix == "transport":
              self.calls.append((f"{self.prefix}-start", o2))
          else:
              self.calls.append(f"{self.prefix}-start")

      def stop(self):
          self.calls.append(f"{self.prefix}-stop")

      def quiesce(self):
          self.calls.append(f"{self.prefix}-quiesce")


  class FakeClock:
      """Shared fake time source. `.now` is the current time; `__call__()`
      returns it (the shape every `clock`/`time_source` parameter this
      replaces expects); `advance(seconds)` adds to it. `.now` is a plain
      public attribute, so a caller may also mutate it directly
      (`clock.now += 5.0`) instead of calling advance()."""

      def __init__(self, start: float = 0.0):
          self.now = start

      def __call__(self) -> float:
          return self.now

      def advance(self, seconds: float) -> None:
          self.now += seconds


  class FakeObservable:
      """Stands in for both `gs` and `terrarium` in tests that only need a
      no-op `add_observer()` plus fixed `room`/`state`/`bit`/`bit_name`
      attributes -- instantiate it once per role
      (`gs = FakeObservable(); terrarium = FakeObservable()`)."""

      room = None
      state = TerrariumState.NO_ROOM
      bit = None
      bit_name = None

      def add_observer(self, observer):
          pass
  ```

- [ ] **Step 2: write `tests/test_fakes.py`**

  Create the file with exactly this content:

  ```python
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
  ```

- [ ] **Step 3: verify**

  ```
  .venv/bin/python -m pytest tests/test_fakes.py -q -p no:cacheprovider
  ```

  Expected: `16 passed`.

  Then run the full suite to confirm nothing else moved:

  ```
  .venv/bin/python -m pytest tests -q -p no:cacheprovider
  ```

  Expected: `2766 passed, 1 skipped` (2750 baseline + 16 new tests in
  `tests/test_fakes.py`; no existing test file was touched by this task).

- [ ] **Step 4: commit**

  ```
  git add tests/fakes.py tests/test_fakes.py
  git commit -m "$(cat <<'EOF'
  test: add shared tests/fakes.py doubles

  Introduces the consolidated FakeAgent/FakeArco/StaticGS/TickingGS/
  RoomlessTerrarium/RecordingClient/FakeClock/FakeObservable doubles that
  subsequent commits migrate call sites onto. No existing test file is
  touched yet.
  EOF
  )"
  ```

---

### Task A2: Migrate `FakeAgent`/`FakeArco` call sites in `tests/test_terrarium_boot.py`

**Files:**
- Modify: `tests/test_terrarium_boot.py` (3919 lines)

**Interfaces:**
- Consumes: `tests.fakes.FakeAgent(closing=0, poll_error=None)`,
  `tests.fakes.FakeArco(returncode=None, poll_error=None)` (from Task A1).

Depends on Task A1 being merged first (or applied to this same branch first).

This task replaces every local `class FakeAgent:` / `class FakeArco:` (and the one local `class
Arco:` / `class Agent:`) in `tests/test_terrarium_boot.py` that matches one of the shapes below,
**except** the two bespoke ones called out in Step 1, which stay untouched. It also removes the
module-level `_FakeAgent`/`_FakeArco` classes and renames their call sites.

- [ ] **Step 1: add the import, and confirm the two bespoke exceptions**

  In `tests/test_terrarium_boot.py`, add to the import block (after the existing
  `from tests.instrument_fixtures import GENERIC_SURFACE` line, around line 16):

  ```python
  from tests.fakes import FakeAgent, FakeArco
  ```

  Do **not** touch these two local classes -- they are bespoke and stay local per the spec:
  - Lines 1060-1068, inside `test_serve_until_done_lets_closing_devices_finish_their_fade`: the
    `FakeAgent` whose `closing` decrements to 0 after its 4th poll (a stateful fade, not a fixed
    value FakeAgent's constructor can express).
  - Lines 3598-3602, inside `test_wait_in_setup_admin_yields_on_state_change_not_on_setup_seconds`:
    the `FakeAgent` whose `poll()` flips an enclosing `gs.state` on its 3rd call (a bespoke
    interaction, not a FakeAgent-only behavior).
  - Also do not touch lines 856-874 (`test_wait_in_setup_ignores_a_live_parent`) -- Task A6 deletes
    that whole function outright as an exact duplicate, so migrating its body here would be wasted
    work.

- [ ] **Step 2: no-op `FakeAgent` -> `FakeAgent()`**

  These local classes are byte-identical:

  ```python
      class FakeAgent:
          def poll(self):
              pass
  ```

  Delete the class definition and replace every construction `FakeAgent()` with
  `FakeAgent()` from `tests.fakes` (the call syntax is unchanged; only the class definition goes
  away) at these line ranges (class definition only -- leave the rest of each test body alone):
  655-657, 682-684, 708-710, 754-756, 790-792, 814-816, 882-884, 2099-2101, 2133-2135, 2186-2188,
  2244-2246, 2298-2300, 2355-2357, 3566-3568, 3745-3747, 3840-3842.

- [ ] **Step 3: `closing = 0`, no-op poll -> `FakeAgent()`**

  ```python
      class FakeAgent:
          closing = 0

          def poll(self):
              pass
  ```

  Same transform (delete the class def; `FakeAgent()` already defaults `closing=0`) at: 939-943,
  972-976, 998-1002, 3813-3817, 3867-3871.

  Also at 3366-3368, the same shape but named `Agent` (not `FakeAgent`) inside
  `test_serve_until_done_polls_the_terrariums_arco_not_a_stale_handle` -- delete the local `Agent`
  class and rename its construction sites from `Agent()` to `FakeAgent()`.

- [ ] **Step 4: raising poll -> `FakeAgent(poll_error=...)`**

  Example (lines 1032-1036):

  ```python
      class FakeAgent:
          closing = 0

          def poll(self):
              raise AssertionError("must not poll once the parent is gone")
  ```

  becomes, at the construction site: `FakeAgent(poll_error=AssertionError("must not poll once the parent is gone"))`.

  Delete the class definition and apply the same transform (keeping each test's own exact message
  string) at:
  - 1032-1036: `"must not poll once the parent is gone"`
  - 1094-1098: `"must not poll once the room is down"`
  - 1462-1464: `"must not poll when already loaded"` (no `closing` attribute on the original --
    `FakeAgent(poll_error=...)` still defaults `closing=0`, which is harmless since the loop
    exits before ever reading `.closing` in this test)
  - 2032-2034: `"must not poll when already ready"`
  - 2497-2499: `"must not poll past the no-room check"`

- [ ] **Step 5: outer-list poll counting -> `agent.polls`**

  Example (lines 621-623, inside `test_wait_in_setup_polls_for_the_requested_window`):

  ```python
      polls = []

      class FakeAgent:
          def poll(self):
              polls.append(1)

      ticks = iter([0.0, 0.1, 0.2, 0.3, 5.0])
      reason = _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                              sleep=lambda _s: None)
      assert reason == "expired"
      assert len(polls) >= 3
  ```

  becomes:

  ```python
      agent = FakeAgent()
      ticks = iter([0.0, 0.1, 0.2, 0.3, 5.0])
      reason = _wait_in_setup(agent, 1.0, clock=lambda: next(ticks),
                              sleep=lambda _s: None)
      assert reason == "expired"
      assert agent.polls >= 3
  ```

  Apply the same transform (drop the outer `polls` list, bind `agent = FakeAgent()`, pass `agent`
  positionally where `FakeAgent()` was passed inline, rewrite `len(polls)`/`polls == []` to
  `agent.polls`) at:
  - 638-640 (`test_wait_in_setup_returns_immediately_when_not_requested`): assertion is
    `polls == []` -> `agent.polls == 0`.
  - 845-847 (`test_wait_in_setup_exits_early_when_the_parent_is_gone`): assertion is
    `polls == []` -> `agent.polls == 0`.
  - 3787-3789 (`test_wait_in_setup_paces_each_iteration_with_the_pacer`): assertion is
    `pacer.waits == len(polls) == 3` -> `pacer.waits == agent.polls == 3`.

- [ ] **Step 6: instance-counting poll (no outer list) -> `agent.polls`**

  Lines 2058-2064, inside `test_wait_for_room_ready_polls_until_a_console_load_room_lands`:

  ```python
      class FakeAgent:
          def __init__(self):
              self.polls = 0

          def poll(self):
              self.polls += 1
  ```

  Delete the class; replace `FakeAgent()` construction with the shared one (its `.polls`
  attribute already behaves identically). Check the rest of that test body for any
  `<name>.polls` reads and leave them as-is (they already read the right attribute name).

- [ ] **Step 7: remove the module-level `_FakeAgent`/`_FakeArco` and rename call sites**

  Delete these two classes (lines 1135-1141 and 1130-1132 respectively):

  ```python
  class _FakeArco:
      def poll(self):
          return None


  class _FakeAgent:
      closing = 0

      def poll(self):
          pass
  ```

  Then rename every construction site in the file from `_FakeAgent()` to `FakeAgent()` and from
  `_FakeArco()` to `FakeArco()`. Find them with:

  ```
  grep -n "_FakeAgent()\|_FakeArco()" tests/test_terrarium_boot.py
  ```

  (as of this HEAD: `_FakeAgent()` at lines 1225, 1310, 1356, 1438; `_FakeArco()` at lines 1225,
  1310, 1356, 1438, 1466, 2125, 2178, 2232, 2290, 2342, 3820, 3845, 3874 -- re-run the grep after
  Step 2-6's edits shift line numbers, since those edits land above several of these).

- [ ] **Step 8: `FakeArco` local classes -> `FakeArco(...)`**

  Plain "alive" (identical at 945-947, 978-980, 1038-1040, 1070-1072):

  ```python
      class FakeArco:
          def poll(self):
              return None
  ```

  -> delete, construct `FakeArco()`.

  Plain "dead" (identical at 1004-1006 and 1100-1102, modulo a trailing comment):

  ```python
      class FakeArco:
          def poll(self):
              return 1                      # exited
  ```

  -> delete, construct `FakeArco(returncode=1)`.

  Counting variant (lines 659-665, inside `test_wait_in_setup_drains_arco_every_iteration`):

  ```python
      class FakeArco:
          def __init__(self):
              self.polls = 0

          def poll(self):
              self.polls += 1
              return None                      # still running
  ```

  -> delete; construct `arco = FakeArco()`; the test's existing assertion
  `assert arco.polls >= 4` already reads the right attribute, no further edit needed.

  Settable-after-construction variant (lines ~3352-3354, class named `Arco` not `FakeArco`,
  inside `test_serve_until_done_polls_the_terrariums_arco_not_a_stale_handle`):

  ```python
      class Arco:
          def __init__(self): self.polls = 0; self.exit = None
          def poll(self): self.polls += 1; return self.exit
  ```

  -> delete; replace `Arco()` constructions with `FakeArco()`, and replace every
  `<name>.exit = ...` mutation in that test body with `<name>.returncode = ...` (the shared
  class's public attribute is named `returncode`, not `exit`).

- [ ] **Step 9: verify**

  ```
  .venv/bin/python -m pytest tests/test_terrarium_boot.py -q -p no:cacheprovider
  ```

  Expected: all `test_terrarium_boot.py` tests still pass (same count as before this task --
  no tests were added or removed here, only their bodies rewritten).

  ```
  .venv/bin/python -m pytest tests -q -p no:cacheprovider
  ```

  Expected: `2766 passed, 1 skipped` (unchanged from Task A1's end state -- this task is a pure
  refactor, no new/removed tests).

- [ ] **Step 10: commit**

  ```
  git add tests/test_terrarium_boot.py
  git commit -m "$(cat <<'EOF'
  test(terrarium_boot): migrate FakeAgent/FakeArco onto tests.fakes

  Replaces the ~30 near-identical local FakeAgent/FakeArco copies (and the
  module-level _FakeAgent/_FakeArco) with the shared doubles from
  tests/fakes.py. The closing-fade countdown agent and the poll-driven
  state-flip agent stay local (bespoke, not shared behavior); the exact
  duplicate test_wait_in_setup_ignores_a_live_parent is left for the
  parametrize/dedup commit to delete outright.
  EOF
  )"
  ```

---

### Task A3: Migrate `FakeGS`/`FakeGs`/`FakeGameServer` variants and the `_FakeLaunch`/`_FakeBitConfig`/`_FakeBit` data doubles

**Files:**
- Modify: `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: `tests.fakes.StaticGS(state, tick_error=None)`,
  `tests.fakes.TickingGS(state, end_state, after=3)` (from Task A1), `types.SimpleNamespace`
  (stdlib).

Depends on Task A2 (touches many of the same test bodies; doing Task A2 first avoids overlapping
edits to the same lines in two different tasks).

This task has two parts: (A) GS doubles that call code actually invokes `.tick()` on become
`StaticGS`/`TickingGS`; (B) GS doubles that are pure data (no `.tick()` ever called on them by
the function under test) become `types.SimpleNamespace(...)`. `_FakeLaunch`/`_FakeBitConfig`/
`_FakeBit` (module-level, lines 1113-1127) have no duplicate copies anywhere in this file --
leave them exactly as they are; do not move them into `tests/fakes.py` (moving a single-copy
class saves no lines and adds cross-file indirection for no benefit).

- [ ] **Step 1: add the import**

  ```python
  import types
  ```

  (only if not already imported in this file -- check with `grep -n "^import types"
  tests/test_terrarium_boot.py` first; several tests in this file already do
  `import types` locally inside the test function, e.g. `test_recycle_room_orders_client_stops
  _before_unload_and_restarts_after` -- leave those local imports alone, they are unrelated to
  this task's module-level SimpleNamespace uses) and

  ```python
  from tests.fakes import StaticGS, TickingGS
  ```

  to the top-level import block.

- [ ] **Step 2: `StaticGS` -- state-only with no-op tick**

  Example (lines 992-996, inside `test_serve_until_done_stops_when_arco_dies`):

  ```python
      class FakeGS:
          state = State.RUNNING

          def tick(self, dt):
              pass
  ```

  -> delete the class; construct `StaticGS(State.RUNNING)`.

  Apply the same transform at:
  - 992-996: `StaticGS(State.RUNNING)`
  - 1054-1058 (`test_serve_until_done_lets_closing_devices_finish_their_fade`):
    `StaticGS(State.IDLE)`
  - 2127-2131, 2180-2184, 2238-2242, 2292-2296, 2349-2353 (the five `_serve_roomless` tests --
    Task A7 also touches these lines via the `_run_roomless` helper; if Task A7 has already landed
    when this task runs, these five are already gone and this bullet is a no-op for them):
    `StaticGS(State.IDLE)`
  - ~3362-3364 (class named `GS`, inside
    `test_serve_until_done_polls_the_terrariums_arco_not_a_stale_handle`): delete, replace `GS()`
    constructions with `StaticGS(State.RUNNING)`.

- [ ] **Step 3: `StaticGS` -- `tick_error`**

  Example (lines 1026-1030, inside `test_serve_until_done_stops_when_the_parent_is_gone`):

  ```python
      class FakeGS:
          state = State.RUNNING

          def tick(self, dt):
              raise AssertionError("must not tick once the parent is gone")
  ```

  -> `StaticGS(State.RUNNING, tick_error=AssertionError("must not tick once the parent is gone"))`.

  Apply the same transform, keeping each exact message, at:
  - 1026-1030: `"must not tick once the parent is gone"`, state `RUNNING`
  - 1088-1092: `"must not tick once the room is down"`, state `RUNNING`
  - 1456-1460: `"must not tick when already loaded"`, state `SETUP`
  - 2487-2491: `"must not tick past the no-room check"`, state `IDLE`

- [ ] **Step 4: `TickingGS`**

  Example (lines 929-937, inside `test_serve_until_done_stops_when_the_bit_completes`):

  ```python
      class FakeGS:
          def __init__(self):
              self.state = State.RUNNING
              self.ticks = 0

          def tick(self, dt):
              self.ticks += 1
              if self.ticks >= 3:
                  self.state = State.IDLE
  ```

  -> delete; construct `TickingGS(State.RUNNING, State.IDLE)` (default `after=3` matches).

  Apply at:
  - 929-937, 3803-3811, 3857-3865 (all three identical, RUNNING -> IDLE after 3):
    `TickingGS(State.RUNNING, State.IDLE)`
  - 962-970 (RUNNING -> LOADED after 3, `test_serve_until_done_reports_restart` -- Task A6
    parametrizes this test together with 929-937; if Task A6 has already landed, this bullet
    applies to whatever the parametrized test's two cases construct instead):
    `TickingGS(State.RUNNING, State.LOADED)`
  - 3830-3838 (IDLE -> LOADED after 3): `TickingGS(State.IDLE, State.LOADED)`

- [ ] **Step 5: data-only `FakeGs`/`FakeGameServer` -> `SimpleNamespace`**

  Example (lines 758-761, inside
  `test_wait_in_setup_announces_a_bit_swapped_in_by_one_console_poll`):

  ```python
      class FakeGs:
          def __init__(self):
              self.state = State.SETUP
              self.bit_name = "OldBit"

      gs = FakeGs()
  ```

  -> `gs = types.SimpleNamespace(state=State.SETUP, bit_name="OldBit")`. This `gs` is passed to
  `_make_fake_swap_console_agent(gs, State)`, which mutates `gs.state`/`gs.bit_name` directly --
  `SimpleNamespace` supports that with no further change.

  Apply the same transform at:
  - 686-688 (`test_wait_in_setup_returns_state_changed_when_the_engine_leaves_setup`):
    `types.SimpleNamespace(state=State.SETUP)` (the enclosing `clock()` closure flips
    `gs.state = State.RUNNING` directly -- leave that line as-is).
  - 712-713 (`test_wait_in_setup_yields_on_abort_too`): the original is a bare class attribute
    (`class FakeGs: state = State.IDLE`) with no `__init__` -> `types.SimpleNamespace(state=State.IDLE)`.
  - 758-761 and 794-797 (the swap-announced/swap-silent pair): as shown above.
  - 902-905 (`test_wait_in_setup_returns_players_met_when_threshold_crossed`, class
    `FakeGameServer`): original is
    ```python
        class FakeGameServer:
            def __init__(self):
                self.bit = FakeBit()
                self.registration = FakeRegistration(0)
        game_server = FakeGameServer()
    ```
    -> `game_server = types.SimpleNamespace(bit=FakeBit(), registration=FakeRegistration(0))`.
    Leave the local `FakeBit`/`FakeRole`/`FakeRoleTable`/`FakeRegistration` classes in that test
    untouched (bespoke, single-use, not part of this task).

- [ ] **Step 6: verify**

  ```
  .venv/bin/python -m pytest tests/test_terrarium_boot.py -q -p no:cacheprovider
  .venv/bin/python -m pytest tests -q -p no:cacheprovider
  ```

  Expected both times: same pass count as Task A2 left (`2766 passed, 1 skipped` for the full
  suite) -- this is a pure refactor.

- [ ] **Step 7: commit**

  ```
  git add tests/test_terrarium_boot.py
  git commit -m "$(cat <<'EOF'
  test(terrarium_boot): migrate FakeGS variants onto StaticGS/TickingGS

  GS doubles a loop actually calls tick() on become StaticGS/TickingGS from
  tests/fakes.py; pure-data GS doubles (never ticked) become
  types.SimpleNamespace. _FakeLaunch/_FakeBitConfig/_FakeBit are left as-is
  (no duplicate copies exist for them in this file).
  EOF
  )"
  ```

---

### Task A4: Migrate `FakeTerrarium` variants, the local `FakePool`/`FakeTransport` pairs, and `_FakeObservable`

**Files:**
- Modify: `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: `tests.fakes.RoomlessTerrarium(unload_to_no_room=True)`,
  `tests.fakes.RecordingClient(calls, prefix, start_error=None)`,
  `tests.fakes.FakeObservable` (from Task A1), `types.SimpleNamespace`.

Depends on Task A3 (shares several enclosing test bodies).

Note on scope: `tests/test_terrarium_boot.py` defines `_RecordingPopen(FakePopen)` three
times, byte-identical, inside three tests (around lines 201, 261, 326 as of this HEAD; confirm
with `grep -n "class _RecordingPopen" tests/test_terrarium_boot.py`). Step 5b hoists it to one
module-level class in that file. `tests/test_run_stack.py`'s `_RecordingPopen(ScriptedPopen)` is
a different, single-use class and is not touched.

Also note: `_FakeObservable` (three copies) lives in **`tests/test_terrarium_boot.py`**, not
`tests/test_console_agent.py` -- confirm with `grep -n "_FakeObservable"
tests/test_terrarium_boot.py` before starting (should show three `class _FakeObservable:`
definitions, around lines 2596, 2650, 2707 as of this HEAD, each immediately followed by a
`fake_build` closure using it).

- [ ] **Step 1: add the import**

  ```python
  from tests.fakes import FakeObservable, RecordingClient, RoomlessTerrarium
  ```

- [ ] **Step 2: state-only `FakeTerrarium` -> `SimpleNamespace`**

  Example (lines 1104-1105, inside `test_room_down_mid_run_returns_no_room_not_arco_exited`):

  ```python
      class FakeTerrarium:
          state = TerrariumState.NO_ROOM
  ```

  -> `terrarium = types.SimpleNamespace(state=TerrariumState.NO_ROOM)` (construct at the same
  point the original `FakeTerrarium()` call was made).

  Apply the same transform at: 1104-1105, 2029-2030, 2052-2054 (instance-attr style
  `self.state = TerrariumState.NO_ROOM` in `__init__` -- same target), 2096-2097, 2484-2485,
  3474-3475, 3506-3507, 3544-3545.

  For the four with an extra `.arco = _FakeArco()` (now `FakeArco()` after Task A2) --
  2122-2125, 2175-2178, 2287-2290 -- use
  `types.SimpleNamespace(state=TerrariumState.ROOM_READY, arco=FakeArco())`.

- [ ] **Step 3: `RoomlessTerrarium` -- the two unload-tracking variants**

  Int-counter variant (lines 2229-2236, inside
  `test_serve_roomless_restarts_pool_then_transport_after_failed_recycle`):

  ```python
      class FakeTerrarium:
          def __init__(self):
              self.state = TerrariumState.ROOM_READY
              self.arco = _FakeArco()
              self.unload_calls = 0

          def unload_room(self, force=False):
              self.unload_calls += 1
  ```

  -> `terrarium = RoomlessTerrarium(unload_to_no_room=False)`. Its final assertion,
  `assert terrarium.unload_calls == 0`, must change to `assert len(terrarium.unload_calls) == 0`
  (the shared double records a list, not a count).

  List-of-force-values variant (lines 2339-2347, inside
  `test_serve_roomless_unloads_and_returns_to_no_room_wait_when_restart_fails`):

  ```python
      class FakeTerrarium:
          def __init__(self):
              self.state = TerrariumState.ROOM_READY
              self.arco = _FakeArco()
              self.unload_calls = []

          def unload_room(self, force=False):
              self.unload_calls.append(force)
              self.state = TerrariumState.NO_ROOM
  ```

  -> `terrarium = RoomlessTerrarium()` (default `unload_to_no_room=True` matches). Its existing
  assertion `assert terrarium.unload_calls == [True]` needs no change.

  (If Task A7 has already landed by the time this task runs, both of these two tests' bodies are
  already rewritten around the `_run_roomless` helper, and these two `FakeTerrarium` classes no
  longer exist standalone -- in that case, confirm the helper in Task A7 already constructs
  `RoomlessTerrarium` with the matching flag for each test, and skip this step.)

- [ ] **Step 4: local `FakePool`/`FakeTransport` -> `RecordingClient`**

  Simplest pair (lines 2399-2405, inside `test_restart_room_clients_starts_pool_then_transport`):

  ```python
      calls = []

      class FakePool:
          def start(self):
              calls.append("pool-start")

      class FakeTransport:
          def start(self, o2, *, pump=None):
              calls.append(("transport-start", o2))

      o2 = object()
      reason = terrarium_boot._restart_room_clients(
          transport=FakeTransport(), pool=FakePool(), o2lite=o2)
      assert reason is None
      assert calls == ["pool-start", ("transport-start", o2)]
  ```

  becomes:

  ```python
      calls = []
      pool = RecordingClient(calls, "pool")
      transport = RecordingClient(calls, "transport")

      o2 = object()
      reason = terrarium_boot._restart_room_clients(
          transport=transport, pool=pool, o2lite=o2)
      assert reason is None
      assert calls == ["pool-start", ("transport-start", o2)]
  ```

  (assertion is unchanged -- `RecordingClient`'s recorded labels already match).

  Failure-injection pair (lines 2421-2427, inside
  `test_restart_room_clients_catches_a_raising_start_and_returns_reason`):

  ```python
      class FailingPool:
          def start(self):
              raise RuntimeError("injected pool failure")

      class FakeTransport:
          def start(self, o2, *, pump=None):
              pass

      reason = terrarium_boot._restart_room_clients(
          transport=FakeTransport(), pool=FailingPool())
      assert reason == "injected pool failure"
  ```

  becomes:

  ```python
      reason = terrarium_boot._restart_room_clients(
          transport=RecordingClient([], "transport"),
          pool=RecordingClient([], "pool",
                               start_error=RuntimeError("injected pool failure")))
      assert reason == "injected pool failure"
  ```

  (production code never reaches `transport.start()` once `pool.start()` raises -- confirmed by
  reading `harness/terrarium_boot.py:_restart_room_clients`, lines 1303-1335 -- so the transport's
  `RecordingClient([], "transport")` never actually records anything; this is safe).

  Instance-`self.calls` pair (lines 2443-2465, inside
  `test_restart_room_clients_quiesces_the_pool_when_the_transport_fails`):

  ```python
      class FakePool:
          def __init__(self):
              self.calls = []

          def start(self):
              self.calls.append("start")

          def quiesce(self):
              self.calls.append("quiesce")

      class FailingTransport:
          def start(self, o2, *, pump=None):
              raise RuntimeError("no clock")

      pool = FakePool()
      reason = terrarium_boot._restart_room_clients(
          transport=FailingTransport(), pool=pool, o2lite=None)
      assert reason == "no clock"
      assert pool.calls == ["start", "quiesce"]

      class SucceedingTransport:
          def start(self, o2, *, pump=None):
              pass

      pool2 = FakePool()
      reason2 = terrarium_boot._restart_room_clients(
          transport=SucceedingTransport(), pool=pool2, o2lite=None)
      assert reason2 is None
  ```

  becomes (note the assertion text changes from bare `"start"`/`"quiesce"` to the prefixed
  `"pool-start"`/`"pool-quiesce"` -- same two events, same order, just the shared double's
  labeling convention):

  ```python
      pool = RecordingClient([], "pool")
      reason = terrarium_boot._restart_room_clients(
          transport=RecordingClient([], "transport",
                                    start_error=RuntimeError("no clock")),
          pool=pool, o2lite=None)
      assert reason == "no clock"
      assert pool.calls == ["pool-start", "pool-quiesce"]

      pool2 = RecordingClient([], "pool")
      reason2 = terrarium_boot._restart_room_clients(
          transport=RecordingClient([], "transport"), pool=pool2, o2lite=None)
      assert reason2 is None
  ```

  `_recycle_room` pair (lines 2853-2865 and 2891-2900, inside
  `test_recycle_room_orders_client_stops_before_unload_and_restarts_after` and
  `test_recycle_room_failure_skips_restarts_and_returns_reason`): same `RecordingClient(calls,
  "pool")`/`RecordingClient(calls, "transport")` transform as the simplest pair above, sharing
  one `calls` list; assertions (`calls == ["transport-stop", "pool-quiesce", "recycle",
  "pool-start", ("transport-start", o2)]` and `"pool-start" not in calls`) are unchanged. Leave
  the local `FakeTerrarium` in both of these two tests untouched (bespoke `.room =
  types.SimpleNamespace(name="TEST")` plus a recording `recycle_room()` -- not a RoomlessTerrarium
  shape, not covered by this task).

  `_Transport` pair (lines 2917-2919, inside
  `test_restart_room_clients_forwards_the_arco_pump_to_transport_start`): single-purpose,
  records `pump` only -- leave this one local (it has no `FakePool` counterpart and records a
  different thing than `RecordingClient` does; not a duplicate of anything else in this file).

  `FakePool` at lines 2769-2774 (inside
  `test_main_no_room_boot_skips_transport_start_and_leaves_clients_stopped`): `self.calls = []`,
  `start()` appends `"start"` (no `quiesce`) -> `RecordingClient([], "pool")`, with the test's
  assertion changed from `pool.calls == ["start"]` to `pool.calls == ["pool-start"]`.

- [ ] **Step 5: `_FakeObservable` (three copies)**

  All three are byte-identical:

  ```python
      class _FakeObservable:
          room = None
          state = TerrariumState.NO_ROOM
          bit = None
          bit_name = None

          def add_observer(self, observer):
              pass
  ```

  Delete all three local definitions (confirm their line numbers first with `grep -n
  "class _FakeObservable" tests/test_terrarium_boot.py`, since Steps 2-4 above shift line
  numbers). Replace every `_FakeObservable()` construction with `FakeObservable()` (from
  `tests.fakes`).

- [ ] **Step 5b: hoist the three `_RecordingPopen` copies to module level**

  Each of the three tests currently contains this block (with `order = []` just above it):

  ```python
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
  ```

  Add once at module level (after the imports, near the other module-level doubles):

  ```python
  class _RecordingPopen(FakePopen):
      """FakePopen that appends its label to `order` on the first signal
      it receives while still running."""

      def __init__(self, label, order):
          super().__init__()
          self._label = label
          self._order = order

      def send_signal(self, sig):
          if self.returncode is None:
              self._order.append(self._label)
          super().send_signal(sig)
  ```

  In each of the three tests, delete the local class and pass `order` explicitly:

  ```python
      order = []
      arco_popen = _RecordingPopen("arco", order)
      sim_popen = _RecordingPopen("simulator", order)
  ```

  Confirm: `grep -c "class _RecordingPopen" tests/test_terrarium_boot.py` prints `1`.

- [ ] **Step 6: verify**

  ```
  .venv/bin/python -m pytest tests/test_terrarium_boot.py -q -p no:cacheprovider
  .venv/bin/python -m pytest tests -q -p no:cacheprovider
  ```

  Expected: `2766 passed, 1 skipped` for the full suite (pure refactor, no count change from
  Task A3's end state).

- [ ] **Step 7: commit**

  ```
  git add tests/test_terrarium_boot.py
  git commit -m "$(cat <<'EOF'
  test(terrarium_boot): migrate FakeTerrarium/FakePool/FakeTransport/
  _FakeObservable onto tests.fakes

  State-only FakeTerrarium copies become SimpleNamespace; the two
  unload-tracking variants become RoomlessTerrarium; the local
  FakePool/FakeTransport call-recording pairs become RecordingClient; the
  three identical _FakeObservable copies become the shared FakeObservable;
  the three identical in-test _RecordingPopen classes become one
  module-level class.
  EOF
  )"
  ```

---

### Task A5: Migrate `FakeClock` in the four other test files

**Files:**
- Modify: `tests/test_capture_store.py` (423 lines)
- Modify: `tests/test_capture_bit.py` (229 lines)
- Modify: `tests/test_link.py` (962 lines)
- Modify: `tests/test_console_agent.py` (2527 lines)

**Interfaces:**
- Consumes: `tests.fakes.FakeClock(start=0.0)` (from Task A1).

Independent of Tasks 2-4 (different files) -- may run in parallel with them once Task A1 has
landed.

Each of the four files has exactly one `class FakeClock:` definition (verified: `grep -c "class
FakeClock" <file>` is 1 for each). None are byte-identical to each other, but every call site in
all four files only ever uses `FakeClock()`/`FakeClock(x)` construction, `clock()` (the
`__call__`), `.advance(x)`, or direct `.now +=`/`.now =` mutation -- never the internal `.t`
attribute by name from outside the class. This means the consolidated `FakeClock` (which uses
`.now` and always has `advance()`) is a drop-in replacement everywhere, with one call site needing
its own mutation style checked (test_console_agent.py, Step 4 below).

- [ ] **Step 1: `tests/test_capture_store.py`**

  Delete the local class (lines 25-33):

  ```python
  class FakeClock:
      def __init__(self):
          self.t = 0.0

      def __call__(self):
          return self.t

      def advance(self, dt):
          self.t += dt
  ```

  Add to the import block (after `from devicelink.protocol import (...)`, around line 13):

  ```python
  from tests.fakes import FakeClock
  ```

  No call site needs editing beyond removing the class -- every use in this file is
  `FakeClock()` then `.advance(dt)`, both of which the shared class supports identically (the
  internal attribute name change from `.t` to `.now` is invisible to every caller).

- [ ] **Step 2: `tests/test_capture_bit.py`**

  Identical situation: delete the local class (lines 25-33, byte-for-byte identical to
  `test_capture_store.py`'s copy), add `from tests.fakes import FakeClock` to the import block
  (after `from control.roles import RoleClass`, around line 9). No call-site edits needed.

- [ ] **Step 3: `tests/test_link.py`**

  Delete the local class (lines 282-290):

  ```python
  class FakeClock:
      def __init__(self, start: float = 0.0):
          self.now = start

      def __call__(self) -> float:
          return self.now

      def advance(self, seconds: float) -> None:
          self.now += seconds
  ```

  Add `from tests.fakes import FakeClock` to the existing import block (this file already imports
  `from uplink.transport import FakeTransport`; add the new import as its own line, alphabetized
  among the existing `from ...` block, e.g. directly before the `from uplink.journal import
  Journal` line). This file's copy is already byte-identical in shape to the shared one (same
  constructor signature, same `.now`/`advance()`), so no call-site edits are needed.

- [ ] **Step 4: `tests/test_console_agent.py`**

  Delete the local class (lines 503-508):

  ```python
  class FakeClock:
      def __init__(self, now=0.0):
          self.now = now

      def __call__(self):
          return self.now
  ```

  Add `from tests.fakes import FakeClock` to the existing import block (this file already imports
  `from tests.instrument_fixtures import GENERIC_SURFACE` and `from tests.test_terrarium import
  DEMO_SPEC, TEST_SPEC, make_config, make_terrarium` -- add the new import as another `tests.`
  line near those).

  This copy has **no** `advance()` method -- its one call site
  (`test_room_frames_are_broadcast_at_the_decimated_rate`, and a second test right after it)
  mutates `.now` directly:

  ```python
      clock.now += ROOM_FRAME_INTERVAL / 2
  ```

  Leave those direct-mutation lines exactly as they are (`.now` is a public attribute on the
  shared `FakeClock` too, so `clock.now += X` keeps working unchanged) -- do not rewrite them to
  `clock.advance(...)`; that would be an unrequested behavior-preserving-but-unnecessary edit.
  Confirm with `grep -n "clock\.now" tests/test_console_agent.py` that every mutation site is of
  this direct form before deleting the class.

- [ ] **Step 5: verify**

  ```
  .venv/bin/python -m pytest tests/test_capture_store.py tests/test_capture_bit.py tests/test_link.py tests/test_console_agent.py -q -p no:cacheprovider
  ```

  Expected: all pass, same counts as before this task.

  ```
  .venv/bin/python -m pytest tests -q -p no:cacheprovider
  ```

  Expected: `2766 passed, 1 skipped` (unchanged -- pure refactor across 4 files, no tests
  added/removed).

- [ ] **Step 6: commit**

  ```
  git add tests/test_capture_store.py tests/test_capture_bit.py tests/test_link.py tests/test_console_agent.py
  git commit -m "$(cat <<'EOF'
  test: migrate FakeClock in capture/link/console_agent tests onto tests.fakes

  The four local FakeClock copies differed in attribute name (.t vs .now)
  and constructor signature, but no call site outside each class ever read
  the internal attribute directly, so the shared tests.fakes.FakeClock
  (.now + advance()) is a drop-in replacement in all four files.
  EOF
  )"
  ```

---

### Task A6: Parametrize the near-duplicate pairs, and delete the exact duplicate

**Files:**
- Modify: `tests/test_terrarium_boot.py`
- Modify: `tests/test_terrarium.py` (606 lines)

**Interfaces:**
- Consumes: `pytest.mark.parametrize`, `tests.fakes.TickingGS`, `tests.fakes.FakeAgent`,
  `tests.fakes.FakeArco` (all from earlier tasks -- run this task after Tasks 2-5).

This task covers five items from the spec's "Parametrize or share setup" bullet, plus the
duplicate deletion. Do all six edits in this one task/commit since they are all small,
independent, and easy to review together.

- [ ] **Step 1: parametrize "completes vs restart"**

  Replace `test_serve_until_done_stops_when_the_bit_completes` (lines 923-953, post-Task-2/3
  rewrite) and `test_serve_until_done_reports_restart` (lines 954-986) with:

  ```python
  @pytest.mark.parametrize("end_state,expected_reason", [
      pytest.param(State.IDLE, "completed", id="completed"),
      pytest.param(State.LOADED, "restarted", id="restarted"),
  ])
  def test_serve_until_done_stops_on_completion_or_restart(end_state,
                                                            expected_reason):
      """The Bit declares itself finished via update() (state -> IDLE), or a
      Console RESTART lands mid-poll and puts the engine back in LOADED
      without this loop ever calling run() again (gs.abort()+load_bit()
      already ran synchronously inside _handle_command). Either way the
      driver must notice and stop -- IDLE means "completed", anything else
      after three ticks means a restart landed."""
      from harness.terrarium_boot import _serve_until_done

      gs = TickingGS(State.RUNNING, end_state)
      reason = _serve_until_done(gs, FakeAgent(), FakeArco(),
                                 sleep=lambda _s: None)
      assert reason == expected_reason
  ```

  This produces **two** collected test IDs
  (`test_serve_until_done_stops_on_completion_or_restart[completed]` and
  `[...][restarted]`) replacing the two original test IDs
  (`test_serve_until_done_stops_when_the_bit_completes` and
  `test_serve_until_done_reports_restart`) -- net test count unchanged (2 -> 2).

- [ ] **Step 2: parametrize "swap announced vs silent"**

  Replace `test_wait_in_setup_announces_a_bit_swapped_in_by_one_console_poll` and
  `test_wait_in_setup_swap_detection_is_silent_in_one_shot_mode` (both already migrated to
  `types.SimpleNamespace` gs by Task A3) with:

  ```python
  @pytest.mark.parametrize("announce_swaps,expect_marker", [
      pytest.param(True, True, id="announced"),
      pytest.param(False, False, id="silent"),
  ])
  def test_wait_in_setup_swap_announcement_follows_announce_swaps(
          capsys, announce_swaps, expect_marker):
      """Round-review 2026-08-24 finding: if an Abort and a LoadBit are both
      queued when console_agent.poll() runs, gs goes SETUP -> IDLE -> SETUP
      inside that single poll call. The plain `gs.state is not State.SETUP`
      check never observes the mid-poll dip, so bit_name changing while
      state reads SETUP both times is the only signal available. The
      "state-changed" handoff always fires; the "round loaded:" print is
      gated on announce_swaps (True in serve mode, False for a one-shot
      --console-port run combined with --seconds/--hold)."""
      from control.state import State
      from harness.terrarium_boot import _wait_in_setup
      from harness import markers

      gs = types.SimpleNamespace(state=State.SETUP, bit_name="OldBit")

      reason = _wait_in_setup(FakeAgent(), 10.0, clock=iter(
          [0.0, 0.1]).__next__, sleep=lambda s: None, gs=gs,
          console_agent=_make_fake_swap_console_agent(gs, State),
          announce_swaps=announce_swaps)

      assert reason == "state-changed"
      out = capsys.readouterr().out
      lines = [l for l in out.splitlines()
               if l.startswith(markers.CONTROL_ROUND_LOADED)]
      if expect_marker:
          assert lines == [f"{markers.CONTROL_ROUND_LOADED} NewBit"]
      else:
          assert lines == []
          assert markers.CONTROL_ROUND_LOADED not in out
  ```

  Keep `_make_fake_swap_console_agent` (the shared helper both original tests already called)
  exactly as-is. Net test count: 2 -> 2 (`[announced]`, `[silent]`).

- [ ] **Step 3: parametrize "recycle success vs failure"**

  Replace `test_recycle_room_orders_client_stops_before_unload_and_restarts_after` and
  `test_recycle_room_failure_skips_restarts_and_returns_reason` (both already migrated to
  `RecordingClient` by Task A4) with:

  ```python
  @pytest.mark.parametrize("recycle_result,expected_reason,expect_restart", [
      pytest.param(None, None, True, id="success"),
      pytest.param("arco failed to start: injected",
                  "arco failed to start: injected", False, id="failure"),
  ])
  def test_recycle_room_orders_restarts_only_on_success(
          recycle_result, expected_reason, expect_restart):
      """Client-before-hub (control/teardown.py's invariant): both of
      Control's own Arco clients stop before terrarium.recycle_room() tears
      down the old Arco. On success the relaunch mirrors process launch
      order (pool first, then transport). On failure there is no hub to
      restart against, so the restarts are skipped and the reason string
      propagates."""
      import types as types_module

      import harness.terrarium_boot as terrarium_boot

      calls = []

      terrarium = types_module.SimpleNamespace(
          room=types_module.SimpleNamespace(name="TEST"),
          recycle_room=lambda: (calls.append("recycle") or recycle_result)
                              if recycle_result is None
                              else recycle_result)
      transport = RecordingClient(calls, "transport")
      pool = RecordingClient(calls, "pool")

      o2 = object()
      reason = terrarium_boot._recycle_room(
          terrarium, transport=transport, pool=pool, o2lite=o2)
      assert reason == expected_reason
      if expect_restart:
          assert calls == ["transport-stop", "pool-quiesce", "recycle",
                           "pool-start", ("transport-start", o2)]
      else:
          assert calls == ["transport-stop", "pool-quiesce"]
          assert "pool-start" not in calls
  ```

  This inlines a minimal `recycle_room` stand-in via `SimpleNamespace` rather than a full local
  class, since `recycle_room` only needs to (a) optionally record `"recycle"` and (b) return
  `recycle_result`. Double-check the lambda's `calls.append(...) or recycle_result` idiom actually
  appends before returning in both branches when writing this -- if a fresh implementer finds
  that idiom too clever to trust at a glance, a two-line local closure
  (`def recycle_room(): calls.append("recycle"); return recycle_result` when
  `recycle_result is None`, else `def recycle_room(): return recycle_result`) is an acceptable,
  clearer substitute with identical behavior; prefer clarity over cleverness here. Net test count:
  2 -> 2 (`[success]`, `[failure]`).

- [ ] **Step 4: parametrize "the two pacer tests"**

  The spec names "the two pacer tests" but this file actually has **four** pacer-related tests
  (`test_wait_in_setup_paces_each_iteration_with_the_pacer`,
  `test_serve_until_done_paces_each_iteration_with_the_pacer`,
  `test_wait_for_load_paces_each_iteration_with_the_pacer`,
  `test_the_default_pacer_routes_through_the_loops_sleep_seam`). Of these, exactly two share an
  identical body shape (`FakeGS`/now `TickingGS`, ticks 3 times then flips state, `FakeAgent`/now
  `FakeAgent()` with no-op poll, `pacer, sleeps = _CountingPacer(), []`, call with
  `sleep=_no_fixed_sleep(sleeps), pacer=pacer`, assert `pacer.waits == 2`, assert `sleeps == []`):
  `test_serve_until_done_paces_each_iteration_with_the_pacer` (target function
  `_serve_until_done`, `TickingGS(State.RUNNING, State.IDLE)`) and
  `test_wait_for_load_paces_each_iteration_with_the_pacer` (target function `_wait_for_load`,
  `TickingGS(State.IDLE, State.LOADED)`). Leave `test_wait_in_setup_paces_each_iteration_with_the
  _pacer` (different FakeAgent/ticks-iterator shape entirely) and
  `test_the_default_pacer_routes_through_the_loops_sleep_seam` (verifies the no-pacer-given seam,
  a different scenario) as standalone tests -- do not fold them into this parametrization.

  Replace the two matching tests with:

  ```python
  @pytest.mark.parametrize("target_name,start_state,end_state,expected_reason", [
      pytest.param("_serve_until_done", State.RUNNING, State.IDLE,
                  "completed", id="serve_until_done"),
      pytest.param("_wait_for_load", State.IDLE, State.LOADED,
                  "loaded", id="wait_for_load"),
  ])
  def test_boot_loop_paces_each_iteration_with_the_pacer(
          target_name, start_state, end_state, expected_reason):
      """The 2026-09-23 Art-Net run received ~33 fps against the 44 Hz tick:
      a fixed sleep(1/44) after each tick runs at 1 / (work + actual
      sleep), and macOS oversleeps 1/44 by ~4 ms. Each loop must pace to
      deadlines (harness/tick_pacer.py) instead."""
      import harness.terrarium_boot as terrarium_boot_module

      target = getattr(terrarium_boot_module, target_name)
      gs = TickingGS(start_state, end_state)
      agent = FakeAgent()
      pacer, sleeps = _CountingPacer(), []
      reason = target(gs, agent, FakeArco(),
                      sleep=_no_fixed_sleep(sleeps), pacer=pacer)
      assert reason == expected_reason
      assert pacer.waits == 2
      assert sleeps == []
  ```

  Net test count: 2 -> 2 (`[serve_until_done]`, `[wait_for_load]`). Total pacer-related tests in
  the file go from 4 to 3 (this parametrized pair, plus the two untouched standalone tests).

- [ ] **Step 5: parametrize the `RecordingArco` pair in `tests/test_terrarium.py`**

  Replace `test_arco_ready_timeout_override_wins_over_the_room_spec` (lines 375-393) and
  `test_arco_ready_timeout_defaults_to_the_room_spec_value` (lines 394-406) with:

  ```python
  def test_arco_ready_timeout_resolution(monkeypatch_unused=None):
      pass  # placeholder -- see parametrized version below, do not add this stub
  ```

  Do **not** add the placeholder above -- write directly:

  ```python
  @pytest.mark.parametrize("override,expect_room_spec_default", [
      pytest.param(42.5, False, id="override_wins"),
      pytest.param(None, True, id="defaults_to_room_spec"),
  ])
  def test_arco_ready_timeout_resolution(override, expect_room_spec_default):
      """--arco-ready-timeout was a dead flag: harness/terrarium_boot.py set
      BootConfig.arco_ready_timeout, but load_room waited on the RoomSpec's
      value. The Terrarium-level override now reaches the wait, and falls
      back to the RoomSpec's own value when no override is given."""
      seen = []

      class RecordingArco(FakeArco):
          def wait_ready(self, timeout):
              seen.append(timeout)
              super().wait_ready(timeout)

      kwargs = {"arco_process_cls": RecordingArco}
      if override is not None:
          kwargs["arco_ready_timeout"] = override
      terrarium = make_terrarium(**kwargs)
      assert terrarium.load_room("TEST") is None
      if expect_room_spec_default:
          assert seen == [terrarium.config.rooms["TEST"].arco_ready_timeout]
      else:
          assert seen == [override]
  ```

  Keep the local `RecordingArco(FakeArco)` class inside the parametrized test (it is genuinely
  bespoke -- a one-off `wait_ready` recorder over the file's own `FakeArco`, not a
  `tests.fakes` candidate; it is not duplicated anywhere else). Net test count: 2 -> 2
  (`[override_wins]`, `[defaults_to_room_spec]`).

- [ ] **Step 6: delete the exact duplicate**

  Delete `test_wait_in_setup_ignores_a_live_parent` (lines 856-874) entirely. Its body is
  byte-identical to `test_wait_in_setup_polls_for_the_requested_window` (lines 614-631) after the
  docstring. Merge the deleted test's parent-alive framing into the kept test's docstring:

  In `test_wait_in_setup_polls_for_the_requested_window`, replace the docstring:

  ```python
      """A scored role is refused once RUNNING, so a device needs a window to
      join before run() closes it."""
  ```

  with:

  ```python
      """A scored role is refused once RUNNING, so a device needs a window to
      join before run() closes it. Also covers the default shape (no
      --exit-with-parent): parent_pid=None is documented on parent_is_gone
      as 'the caller did not ask for this guard', and it must never fire,
      so the loop keeps polling for the full window regardless."""
  ```

  Net test count: 2 -> 1 (one fewer collected test ID).

- [ ] **Step 7: verify**

  ```
  .venv/bin/python -m pytest tests/test_terrarium_boot.py tests/test_terrarium.py -q -p no:cacheprovider
  .venv/bin/python -m pytest tests -q -p no:cacheprovider
  ```

  Starting from Task A5's `2766 passed, 1 skipped`: Steps 1-5 are each a 2-for-2 test-ID swap (net
  0 change to the pass count, 5 fewer distinct test **names** but the same number of collected
  IDs), and Step 6 removes exactly one test. Expected final count:

  ```
  2765 passed, 1 skipped
  ```

  If the number differs, run
  `.venv/bin/python -m pytest tests/test_terrarium_boot.py tests/test_terrarium.py --collect-only -q -p no:cacheprovider | tail -5`
  and account for the discrepancy before moving on -- do not adjust the expected count to match
  without explaining why.

- [ ] **Step 8: commit**

  ```
  git add tests/test_terrarium_boot.py tests/test_terrarium.py
  git commit -m "$(cat <<'EOF'
  test: parametrize near-duplicate pairs, delete one exact duplicate

  Parametrizes: _serve_until_done completes-vs-restart, the wait_in_setup
  swap announced-vs-silent pair, _recycle_room success-vs-failure, the two
  identically-shaped pacer tests, and test_terrarium.py's RecordingArco
  pair. Deletes test_wait_in_setup_ignores_a_live_parent as an exact
  duplicate of test_wait_in_setup_polls_for_the_requested_window, merging
  its parent-alive framing into that test's docstring. Net: 2765 passed,
  1 skipped (was 2766; one test removed, five pairs collapsed to five
  parametrized pairs with unchanged ID counts).
  EOF
  )"
  ```

---

### Task A7: Add the `_run_roomless` helper for the five `_serve_roomless` tests

**Files:**
- Modify: `tests/test_terrarium_boot.py`

**Interfaces:**
- Produces: a module-level `_run_roomless(monkeypatch, wait_results, serve_results, *,
  terrarium=None, gs=None, agent=None, **kw)` helper inside `tests/test_terrarium_boot.py`.
- Consumes: `tests.fakes.RoomlessTerrarium`/`FakeArco`/`FakeAgent` (from Task A1),
  `harness.terrarium_boot._serve_roomless(gs, agent, terrarium, *, console_agent=None,
  parent_pid=None, restart_clients=None, stop_clients=None, uplink=None) -> str` (harness/
  terrarium_boot.py, lines 908-960).

Depends on Tasks 2-4 (uses `FakeAgent`/`FakeArco`/`RoomlessTerrarium` from `tests.fakes`). Run
after Task A4, before or after Task A6 (Task A6 does not touch these five tests).

All five `_serve_roomless` tests share this scaffold: a local `FakeTerrarium` (state
`ROOM_READY` initially, `.arco`), a local `FakeGS` (`state = State.IDLE`, no-op `tick`), a local
`FakeAgent` (no-op `poll`), then `monkeypatch.setattr` on exactly two
`harness.terrarium_boot` module globals -- `_wait_for_room_ready` and `_serve_rounds` -- each
replaced with a closure that returns a value from a fixed sequence keyed off a call-count list.
None of the five tests' assertions depend on the scaffold `FakeGS`/`FakeTerrarium.state` actually
being read by `_serve_roomless` itself (that function never reads `gs.state` or
`terrarium.state` directly -- only the mocked-out real `_wait_for_room_ready`/`_serve_rounds`
would have), so the helper below drops the state-mutation side effects the original inline
closures carried (verified: no assertion in any of the five tests reads `terrarium.state` or
`gs.state` after the call).

- [ ] **Step 1: write the helper**

  Add this module-level function to `tests/test_terrarium_boot.py`, near the other
  `_serve_roomless`-adjacent module-level helpers (e.g. directly above
  `test_serve_roomless_loops_back_to_no_room_after_serve_rounds_no_room`):

  ```python
  def _run_roomless(monkeypatch, wait_results, serve_results, *,
                    terrarium=None, gs=None, agent=None, **kw):
      """Shared driver for the _serve_roomless tests below: builds the
      common FakeTerrarium/FakeGS/FakeAgent scaffold (unless overridden),
      monkeypatches harness.terrarium_boot._wait_for_room_ready and
      _serve_rounds to return wait_results/serve_results in sequence (one
      entry consumed per call -- StopIteration if a test calls either more
      often than it supplied results for, which is a bug signal, not a
      silent repeat), calls _serve_roomless once, and returns
      (reason, terrarium, wait_calls, serve_calls) so each test can layer
      its own extra assertions (e.g. on a custom terrarium's unload_calls,
      or on a restart_clients/stop_clients closure passed via **kw).
      """
      import harness.terrarium_boot as terrarium_boot_module
      from harness.terrarium_boot import _serve_roomless

      if terrarium is None:
          terrarium = RoomlessTerrarium()
          terrarium.state = TerrariumState.ROOM_READY
      if gs is None:
          gs = StaticGS(State.IDLE)
      if agent is None:
          agent = FakeAgent()

      wait_iter = iter(wait_results)
      serve_iter = iter(serve_results)
      wait_calls = []
      serve_calls = []

      def fake_wait_for_room_ready(agent, terr, **kwargs):
          wait_calls.append(1)
          return next(wait_iter)

      def fake_serve_rounds(gs, agent, arco, *, parent_pid=None,
                            console_agent=None, terrarium=None, **_kw):
          serve_calls.append(1)
          return next(serve_iter)

      monkeypatch.setattr(terrarium_boot_module, "_wait_for_room_ready",
                          fake_wait_for_room_ready)
      monkeypatch.setattr(terrarium_boot_module, "_serve_rounds",
                          fake_serve_rounds)

      reason = _serve_roomless(gs, agent, terrarium, **kw)
      return reason, terrarium, wait_calls, serve_calls
  ```

  This needs `RoomlessTerrarium`, `StaticGS`, `FakeAgent`, `TerrariumState`, and `State` already
  imported at module scope (all true after Tasks 1-4).

- [ ] **Step 2: rewrite the five tests**

  Test 1 -- `test_serve_roomless_loops_back_to_no_room_after_serve_rounds_no_room` (before, per
  Task A2-4's rewrite, still has its own inline `FakeTerrarium`/`FakeGS`/`FakeAgent` and
  monkeypatch calls):

  ```python
  def test_serve_roomless_loops_back_to_no_room_after_serve_rounds_no_room(
          monkeypatch):
      """main()'s top-level loop for a NO_ROOM boot: wait for a Room, serve
      rounds against it, and -- the behavior this Task's brief calls out by
      name -- return to the NO_ROOM wait rather than stopping outright when
      `_serve_rounds` reports "no-room" (a Console `unload_room` mid-serve).
      A second lap then runs to "parent-gone" so this test terminates."""
      reason, terrarium, wait_calls, serve_calls = _run_roomless(
          monkeypatch, wait_results=["ready"],
          serve_results=["no-room", "parent-gone"])

      assert reason == "parent-gone"
      assert len(serve_calls) == 2
  ```

  Note this test's original `wait_results` was effectively infinite (`fake_wait_for_room_ready`
  always returned `"ready"` unconditionally, called once); with the helper's `next(wait_iter)`
  consuming one entry per call, `wait_results=["ready"]` is enough since `_serve_roomless` only
  calls `_wait_for_room_ready` once here (the loop's second iteration exits via `_serve_rounds`
  returning `"parent-gone"` before reaching another `_wait_for_room_ready` call) -- verify this
  against `harness/terrarium_boot.py`'s `_serve_roomless` loop body (lines 908-960) before
  trusting a fixed-length list: if in doubt, pass more entries than strictly needed is harmless
  only if they're never consumed and the test doesn't assert on `wait_calls`'s exact length; this
  test doesn't, so extra unused entries are safe padding.

  Test 5 -- `test_serve_roomless_unloads_and_returns_to_no_room_wait_when_restart_fails` (needs a
  custom `terrarium` with `unload_room` tracking, and asserts `wait_calls`'s length):

  ```python
  def test_serve_roomless_unloads_and_returns_to_no_room_wait_when_restart_fails(
          monkeypatch):
      """If the restart-after-reload itself fails, `_serve_roomless` must
      unload the Room (so ROOM_READY never lies about live clients) and
      loop back to the NO_ROOM wait rather than serving with dead clients --
      never calling `_serve_rounds` for that lap."""
      terrarium = RoomlessTerrarium()

      reason, terrarium, wait_calls, serve_calls = _run_roomless(
          monkeypatch, wait_results=["ready", "parent-gone"],
          serve_results=[],
          terrarium=terrarium,
          restart_clients=lambda: "injected restart failure")

      assert reason == "parent-gone"
      assert serve_calls == []
      assert terrarium.unload_calls == [True]
      assert len(wait_calls) == 2
  ```

  Apply the same pattern (build any test-specific `terrarium`/`restart_clients`/`stop_clients`
  inline, then call `_run_roomless(monkeypatch, wait_results=[...], serve_results=[...],
  terrarium=..., **extra_serve_roomless_kwargs)` and assert on the returned tuple) to the
  remaining three:
  - `test_serve_roomless_stops_clients_on_no_room`: `wait_results=["ready", "ready"]`,
    `serve_results=["no-room", "parent-gone"]`, pass
    `stop_clients=lambda: calls.append("stop")` and `restart_clients=lambda: None` (build `calls
    = []` before the call, assert `calls == ["stop"]` after).
  - `test_serve_roomless_restarts_pool_then_transport_after_failed_recycle`: build a
    `RoomlessTerrarium(unload_to_no_room=False)`, `wait_results=["ready"]`,
    `serve_results=["parent-gone"]`, pass a `restart_clients` closure that appends
    `"pool-start"`/`"transport-start"` to a `calls` list and returns `None`; assert
    `calls == ["pool-start", "transport-start"]` and
    `len(terrarium.unload_calls) == 0` (note: the original asserted `calls == ["pool-start",
    "transport-start", "serve-rounds"]` because its inline `fake_serve_rounds` appended
    `"serve-rounds"` to the SAME `calls` list the restart closure used; the shared helper's
    `fake_serve_rounds` does not append to a caller-supplied `calls` list, only to its own
    internal `serve_calls`, so drop `"serve-rounds"` from the expected list here and instead
    additionally assert `len(serve_calls) == 1`, preserving the same "restart happened before
    serve" ordering guarantee via a separate assertion).
  - `test_serve_roomless_skips_restart_when_recycle_already_succeeded`: `wait_results=["ready"]`,
    `serve_results=["parent-gone"]`, pass `restart_clients=lambda: None`; assert
    `len(serve_calls) == 1` (original asserted `calls == ["serve-rounds"]`, which only checked
    that `serve_rounds` ran once and `restart_clients`'s own no-op left no trace -- `len(
    serve_calls) == 1` preserves that).

- [ ] **Step 3: verify**

  ```
  .venv/bin/python -m pytest tests/test_terrarium_boot.py -k serve_roomless -q -p no:cacheprovider
  ```

  Expected: `5 passed`.

  ```
  .venv/bin/python -m pytest tests -q -p no:cacheprovider
  ```

  Expected: `2765 passed, 1 skipped` (unchanged from Task A6's end state if this task runs after
  Task A6; if it runs before Task A6, expect `2766 passed, 1 skipped` -- either way, this task
  itself adds/removes zero tests).

- [ ] **Step 4: commit**

  ```
  git add tests/test_terrarium_boot.py
  git commit -m "$(cat <<'EOF'
  test(terrarium_boot): add _run_roomless helper for the five
  _serve_roomless tests

  Factors out the shared FakeTerrarium/FakeGS/FakeAgent scaffold and the
  _wait_for_room_ready/_serve_rounds monkeypatching every _serve_roomless
  test repeated. Two tests' call-order assertions are restated (same
  guarantee, different bookkeeping) since the helper tracks serve_rounds
  call count separately from each test's own restart_clients/stop_clients
  call-recording list rather than sharing one list across both.
  EOF
  )"
  ```

---

### Task A8: Verification -- collected-ID diff, mutation checks, line-count report

**Files:**
- None modified (verification only; the one exception is a temporary edit to
  `harness/terrarium_boot.py` in Step 3, reverted before this task ends).

**Interfaces:** none (this task runs commands and inspects output; it does not touch test code).

Depends on Tasks 1-7 all being committed on this branch.

- [ ] **Step 1: full-suite baseline**

  ```
  .venv/bin/python -m pytest tests -q -p no:cacheprovider
  ```

  Expected: `2765 passed, 1 skipped`. If this does not match, stop and reconcile against Task A6's
  Step 7 note before continuing.

- [ ] **Step 2: collected-test-ID diff against the pre-PR-A commit**

  Add a temporary worktree at the pre-PR-A base commit (`0d2cec80c7e8f5aad13c247723e01b931bc8397d`
  -- the commit this whole plan started from) inside the scratchpad directory, with its own
  `.venv` symlink:

  ```
  git worktree add /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/pr-a-base 0d2cec80c7e8f5aad13c247723e01b931bc8397d
  ln -s /Users/chris/projects/mm-terrarium/.venv /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/pr-a-base/.venv
  ```

  Collect test IDs from both trees:

  ```
  cd /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/pr-a-base
  .venv/bin/python -m pytest tests --collect-only -q -p no:cacheprovider | grep "::" | sort > /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/ids-before.txt

  cd /Users/chris/projects/mm-terrarium/.claude/worktrees/interesting-bhaskara-62910e
  .venv/bin/python -m pytest tests --collect-only -q -p no:cacheprovider | grep "::" | sort > /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/ids-after.txt

  comm -23 /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/ids-before.txt /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/ids-after.txt
  comm -13 /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/ids-before.txt /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/ids-after.txt
  ```

  The first `comm` call lists IDs only in the "before" set (removed/renamed away); the second
  lists IDs only in the "after" set (added/renamed to). Confirm every line in each list is
  explained by this plan's changes:
  - removed-only: `tests/test_fakes.py` did not exist before (all its IDs appear as
    added-only, not removed-only); `test_wait_in_setup_ignores_a_live_parent`,
    `test_serve_until_done_stops_when_the_bit_completes`,
    `test_serve_until_done_reports_restart`,
    `test_wait_in_setup_announces_a_bit_swapped_in_by_one_console_poll`,
    `test_wait_in_setup_swap_detection_is_silent_in_one_shot_mode`,
    `test_recycle_room_orders_client_stops_before_unload_and_restarts_after`,
    `test_recycle_room_failure_skips_restarts_and_returns_reason`,
    `test_serve_until_done_paces_each_iteration_with_the_pacer`,
    `test_wait_for_load_paces_each_iteration_with_the_pacer`,
    `test_arco_ready_timeout_override_wins_over_the_room_spec`,
    `test_arco_ready_timeout_defaults_to_the_room_spec_value`.
  - added-only: every `tests/test_fakes.py` ID (16 of them, from Task A1), plus
    `test_serve_until_done_stops_on_completion_or_restart[completed]` and `[restarted]`,
    `test_wait_in_setup_swap_announcement_follows_announce_swaps[announced]` and `[silent]`,
    `test_recycle_room_orders_restarts_only_on_success[success]` and `[failure]`,
    `test_boot_loop_paces_each_iteration_with_the_pacer[serve_until_done]` and
    `[wait_for_load]`, `test_arco_ready_timeout_resolution[override_wins]` and
    `[defaults_to_room_spec]`.

  If any other ID appears in either list, stop and investigate before continuing -- an
  unexplained ID change means some test body changed behavior, not just structure.

  Remove the temporary worktree once done:

  ```
  git worktree remove /private/tmp/claude-501/-Users-chris-projects-mm-terrarium--claude-worktrees-interesting-bhaskara-62910e/a494b0ef-0ada-4da2-b29a-b418ec0ec2a3/scratchpad/pr-a-base
  ```

- [ ] **Step 3: mutation checks**

  From the main worktree (`/Users/chris/projects/mm-terrarium/.claude/worktrees/interesting-bhaskara-62910e`):

  Check 1 -- break `_serve_until_done`'s exit predicate. In `harness/terrarium_boot.py`, find:

  ```python
          if gs.state == State.IDLE and not getattr(agent, "closing", 0):
              return "completed"
  ```

  (around line 723, inside `_serve_until_done`) and temporarily change it to:

  ```python
          if gs.state == State.IDLE and getattr(agent, "closing", 0):
              return "completed"
  ```

  (flip `not` away, inverting the predicate). Run:

  ```
  .venv/bin/python -m pytest tests/test_terrarium_boot.py -k "serve_until_done" -q -p no:cacheprovider
  ```

  Expected: failures in `test_serve_until_done_stops_on_completion_or_restart[completed]` (the
  parametrized case with `end_state=State.IDLE`) and
  `test_serve_until_done_lets_closing_devices_finish_their_fade` (the closing-fade test, which is
  the one this exact predicate exists for). Confirm at least these two fail; note any other
  failures too.

  Revert:

  ```
  git checkout -- harness/terrarium_boot.py
  ```

  Check 2 -- break `_serve_roomless`'s restart order. In `harness/terrarium_boot.py`, find inside
  `_serve_roomless` (around lines 908-960):

  ```python
          if restart_clients is not None:
              restart_reason = restart_clients()
              if restart_reason is not None:
                  print(f"room client restart failed: {restart_reason}",
                       file=sys.stderr)
                  terrarium.unload_room(force=True)
                  continue
          reason = _serve_rounds(gs, agent, terrarium.arco,
  ```

  Temporarily swap the order so `_serve_rounds` runs before `restart_clients`:

  ```python
          reason = _serve_rounds(gs, agent, terrarium.arco,
                                 parent_pid=parent_pid,
                                 console_agent=console_agent,
                                 terrarium=terrarium, uplink=uplink)
          if restart_clients is not None:
              restart_reason = restart_clients()
              if restart_reason is not None:
                  print(f"room client restart failed: {restart_reason}",
                       file=sys.stderr)
                  terrarium.unload_room(force=True)
                  continue
          if reason != "no-room":
              return reason
          if stop_clients is not None:
              stop_clients()
  ```

  (this is a deliberately crude reordering for the mutation check, not production-quality code --
  it does not need to compile cleanly for any other caller, only to make the restart-before-serve
  guarantee observably false for this one test run). Run:

  ```
  .venv/bin/python -m pytest tests/test_terrarium_boot.py -k "serve_roomless_restarts_pool_then_transport_after_failed_recycle" -q -p no:cacheprovider
  ```

  Expected: this test fails (it asserts `calls == ["pool-start", "transport-start"]` recorded
  before `serve_calls` grows, per Task A7's rewrite).

  Revert:

  ```
  git checkout -- harness/terrarium_boot.py
  ```

  Confirm the tree is clean again:

  ```
  git status
  ```

  Expected: no changes (both mutation checks reverted).

- [ ] **Step 4: full suite re-run after reverting mutations**

  ```
  .venv/bin/python -m pytest tests -q -p no:cacheprovider
  ```

  Expected: back to `2765 passed, 1 skipped`.

- [ ] **Step 5: line-count report**

  ```
  git diff --shortstat 0d2cec80c7e8f5aad13c247723e01b931bc8397d..HEAD
  ```

  Record the reported `X files changed, Y insertions(+), Z deletions(-)` line in the PR
  description. The spec estimates "about 540 lines" for all of PR A (this is a net figure --
  Task A1 adds a new file, Tasks 2-5 are large deletions offset by smaller insertions, Task A6 is
  roughly neutral, Task A7 is a small net reduction); report the actual number rather than
  adjusting it to match the estimate.

- [ ] **Step 6: nothing to commit**

  This task makes no permanent code changes (Step 3's edits are reverted within the task). Do not
  create a commit for this task -- confirm with `git status` that the tree is clean and stop.

---

## Notes for the plan author

- **`_RecordingPopen`:** the drafter first read only `tests/test_run_stack.py`'s single copy;
  `tests/test_terrarium_boot.py` has three identical in-test copies, handled in Task A4 Step 5b.

- **`_FakeObservable` is in the wrong file in the spec's mental model.** It lives in
  `tests/test_terrarium_boot.py` (three copies, lines ~2596/2650/2707), not
  `tests/test_console_agent.py`. Confirmed no `_FakeObservable` exists anywhere in
  `test_console_agent.py`. Task A4 targets the correct file.

- **"The two pacer tests" is ambiguous.** The file has four pacer-adjacent tests, not two. Task
  6 Step 4 picks the two with the most structurally identical bodies
  (`test_serve_until_done_paces_each_iteration_with_the_pacer` and
  `test_wait_for_load_paces_each_iteration_with_the_pacer`) and leaves the other two
  (`test_wait_in_setup_paces_each_iteration_with_the_pacer`,
  `test_the_default_pacer_routes_through_the_loops_sleep_seam`) alone, but a fresh implementer
  should sanity-check this choice against the spec author's intent before executing Task A6 Step 4
  if that person is reachable.

- **`FakeRegistration` (two copies) is confirmed genuinely bespoke, not an oversight.** The two
  copies have different constructor signatures (`__init__(self, count)` with an immutable
  per-instance count vs `__init__(self)` with a settable `.count`) -- they are not
  near-duplicates that got missed, they are two different shapes solving two different test
  needs. Correctly left local per the spec; no task above touches them.

- **`FakeClock`'s four copies are less identical than the spec's phrasing suggests**, but turned
  out to be safely unifiable: two use `.t` internally, two use `.now`; one (`test_console_agent
  .py`'s) has no `advance()` at all and is only ever mutated via direct `.now +=`. None of the
  four is ever read via its internal attribute name from outside the class, so the unified
  `.now`/`advance()` shape (matching `test_link.py`'s copy, which was already closest to the
  spec's target signature) is a safe drop-in everywhere. This was verified by grepping every call
  site in all four files, not assumed from the class bodies alone.

- **The `RoomlessTerrarium`/`RecordingClient` consolidations require call-site assertion edits,
  not just class-definition deletions**, because the two "unload-tracking" `FakeTerrarium`
  variants use genuinely different recording shapes (an int counter vs a list of `force` values),
  and one `FakePool` variant records bare `"start"`/`"quiesce"` into its own `self.calls` rather
  than a prefixed, shared list. Tasks A4 and A7 call out each specific assertion string that must
  change; a mechanical find-and-delete-the-class approach would leave several tests broken.

- **Task A7's `_run_roomless` helper deliberately drops some cosmetic state-mutation side effects**
  the original five tests' inline mocks carried (e.g. `fake_wait_for_room_ready` flipping
  `terrarium.state` back to `ROOM_READY`). This is safe only because none of the five tests'
  assertions read `terrarium.state`/`gs.state` after the call -- verified by reading all five
  tests' full bodies, not assumed. If a future edit to any of these five tests starts asserting
  on `terrarium.state`, the helper will need a `terrarium_state_after_wait`/similar parameter
  added back.

---

# Part 2: PR B, production refactors

Before Task B1: `git switch -c claude/tier2-prod-consolidation` from PR A's final commit.
PR A rewrites doubles in `tests/test_terrarium_boot.py`, so every PR B step touching that file
locates its target by test name and grep, never by line number. Production line numbers are
orientation only; re-grep before editing.

### Task B1: console/static/dom.js -- shared mk/clear

**Files:**
- Create `console/static/dom.js`
- Modify `console/static/bit.js`, `console/static/busy.js`,
  `console/static/design.js`, `console/static/design_forms.js`,
  `console/static/join.js`, `console/static/rail.js`,
  `console/static/rooms.js`, `console/static/surface.js`,
  `console/static/functions.js`
- Modify `tests/test_console_static.py` (`MODULES` set, line 8-9)
- Create `tests/js/dom.test.js`

**Interfaces:**
- Produces: `console/static/dom.js` exports `mk(tag, className, text)` and
  `clear(node)`.
- Consumes: every listed module currently defines its own byte-identical
  `mk`/`clear` (verified identical across all nine files during planning);
  each import site becomes `import { mk, clear } from "./dom.js";`.

Byte-identical body to copy into `dom.js` (from `console/static/bit.js`,
lines 73-83):

```js
function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}
```

`console/static/busy.js`'s `mk` (it has no `clear`) is the same body, just
without the `clear` function above it.

- [ ] **Step 1: create dom.js**
Write `console/static/dom.js`:
```js
// Shared DOM helpers: every console/static/*.js panel module used to carry
// its own byte-identical copy of these two functions. One copy here, per
// Tier 2 consolidation (docs/superpowers/specs/2026-09-25-tier2-
// consolidation-design.md section "PR B" item 1).

export function clear(node) {
  node.textContent = "";
}

export function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}
```

- [ ] **Step 2: bit.js** -- confirm the local copy is byte-identical to
dom.js's (re-run `sed -n '73,83p' console/static/bit.js` and diff by eye
against Step 1's body -- it was identical when this plan was written), then
delete lines defining `function clear(node) {...}` and
`function mk(tag, className, text) {...}` from `console/static/bit.js`,
and add near the top import block:
```js
import { mk, clear } from "./dom.js";
```
(`bit.js` currently imports `wire.js` and `surface.js` at the top; add the
`dom.js` import as a third line in that same import block.)

- [ ] **Step 3: busy.js** -- same pattern, `mk` only (no `clear` defined in
busy.js today). Delete its local `function mk(tag, className, text) {...}`
and add `import { mk } from "./dom.js";` alongside its existing
`import * as wire from "./wire.js";`.

- [ ] **Step 4: design.js** -- delete its local `clear`/`mk` (currently at
lines 30-38) and add `import { mk, clear } from "./dom.js";` next to its
existing `import * as wire from "./wire.js";` / `import { rebuild as
rebuildForms } from "./design_forms.js";` lines.

- [ ] **Step 5: design_forms.js** -- delete its local `clear`/`mk`
(currently around lines 87-97; re-grep `^function clear\|^function mk` to
confirm) and add the `dom.js` import to its existing multi-line
`import * as wire from "./wire.js";` / `import { ... } from "./toml_edit.js";`
block.

- [ ] **Step 6: join.js** -- delete its local `clear`/`mk` (lines 8-17) and
add `import { mk, clear } from "./dom.js";` next to
`import * as wire from "./wire.js";`.

- [ ] **Step 7: rail.js** -- delete its local `clear`/`mk` (lines 12-21)
and add the import next to `import * as wire from "./wire.js";`.

- [ ] **Step 8: rooms.js** -- delete its local `clear`/`mk` (lines 32-41)
and add the import next to its existing
`import { instrumentTags } from "./surface.js";` / `import * as wire ...`
block. Note: `rooms.js` also defines an UNRELATED `function clearLoading()`
around line 250 -- do not touch that, only the exact `clear(node)`/`mk(tag,
className, text)` pair.

- [ ] **Step 9: surface.js** -- delete its local `clear`/`mk` (lines 48-57)
and add the import next to `import * as wire from "./wire.js";`.

- [ ] **Step 10: functions.js** -- delete its local `clear`/`mk` (lines
43-52) and add the import next to `import * as wire from "./wire.js";`.

- [ ] **Step 11: update tests/test_console_static.py's MODULES set**
Read the current set (line 8-9):
```python
MODULES = {"wire.js", "shell.js", "bit.js", "surface.js", "functions.js",
           "rail.js", "join.js"}
```
This set is already a partial list (it omits `design.js`, `design_forms.js`,
`rooms.js`, `busy.js`, `toml_edit.js` -- `test_the_expected_files_exist`
only checks `MODULES <= names`, a subset test, so this omission is
pre-existing and out of scope). Add `"dom.js"` to the set so
`test_every_js_file_is_an_es_module` covers it too:
```python
MODULES = {"wire.js", "shell.js", "bit.js", "surface.js", "functions.js",
           "rail.js", "join.js", "dom.js"}
```
`dom.js` has `export`, so this test passes unmodified.

- [ ] **Step 12: add tests/js/dom.test.js**
Follow the existing pattern in `tests/js/busy.test.js` / other panel tests
(dynamic `import(...)`, no build step, `node:assert`). Write:
```js
"use strict";
// dom.js: the shared mk/clear DOM helpers every console panel module now
// imports instead of carrying its own copy.
const assert = require("node:assert");

(async () => {
  const dom = await import("../../console/static/dom.js");

  const p = dom.mk("p", "muted", "hello");
  assert.strictEqual(p.tagName, "P");
  assert.strictEqual(p.className, "muted");
  assert.strictEqual(p.textContent, "hello");

  const span = dom.mk("span");
  assert.strictEqual(span.className, "");
  assert.strictEqual(span.textContent, "");

  const wrap = dom.mk("div");
  wrap.appendChild(dom.mk("span", null, "child"));
  assert.strictEqual(wrap.textContent, "child");
  dom.clear(wrap);
  assert.strictEqual(wrap.textContent, "");
  assert.strictEqual(wrap.children.length, 0);

  console.log("dom.test.js OK");
})();
```
This file needs no `_dom_stub.js` import: `dom.js`'s `mk`/`clear` only call
`document.createElement`/`.textContent`, which Node's global `document`
(installed as a side effect by importing `_dom_stub.js` elsewhere in the
same `node --test` run) or -- if this file runs in isolation -- nothing at
all provides. Check first: run `node --test tests/js/dom.test.js` alone; if
`document is not defined`, add
`const { byId } = require("./_dom_stub.js");` at the top (unused `byId` is
fine, the stub's module-load side effect is what's needed) before the
`(async () => { ... })()` block, exactly like `tests/js/busy.test.js` does.

- [ ] **Step 13: run JS suite**
```
node --test tests/js/*.test.js
```
Expect every file's pass count to match its previous run plus
`dom.test.js`'s new assertions, 0 failures.

- [ ] **Step 14: run Python suite**
```
.venv/bin/python -m pytest tests -q -p no:cacheprovider
```
Expect no new failures; `tests/test_console_static.py` in particular must
still pass (external-fetch guard, module-set checks, ES-module checks).

- [ ] **Step 15: commit**
```
git add console/static/dom.js console/static/bit.js console/static/busy.js \
  console/static/design.js console/static/design_forms.js \
  console/static/join.js console/static/rail.js console/static/rooms.js \
  console/static/surface.js console/static/functions.js \
  tests/test_console_static.py tests/js/dom.test.js
git commit -m "refactor(console): extract shared dom.js mk/clear helpers"
```

---

### Task B2: harness/terrarium_boot.py -- merge the boot tick loops

**Files:** Modify `harness/terrarium_boot.py` (lines 650-777 for the two
functions being merged, 779-781 for `_serve_rounds`'s dropped param).

**Interfaces:**
- Produces: `_pump(agent, console_agent, uplink) -> None` (module-private
  helper). `_run_tick_loop(gs, agent, arco, exit_predicate, *, parent_pid=None,
  console_agent=None, terrarium=None, uplink=None, pacer=None,
  sleep=time.sleep) -> str` (module-private helper).
- Consumes: `harness.terrarium_boot.parent_is_gone` (module-global lookup
  -- see constraint below), `control.state.State`,
  `control.terrarium.TerrariumState`, `harness.tick_pacer.TickPacer`,
  `_live_arco`, `_pump_uplink` (all already imported/defined in this file).
- Preserves exactly: `_wait_for_load(gs, agent, arco, *, clock=time.monotonic,
  sleep=time.sleep, parent_pid=None, console_agent=None, terrarium=None,
  uplink=None, pacer=None) -> str` and `_serve_until_done(gs, agent, arco,
  clock=time.monotonic, sleep=time.sleep, parent_pid=None,
  console_agent=None, terrarium=None, uplink=None, pacer=None) -> str` --
  same signatures, same docstrings, same return strings
  ("loaded"/"no-room"/"arco-exited"/"parent-gone" for `_wait_for_load";
  "completed"/"restarted"/"arco-exited"/"no-room"/"parent-gone" for
  `_serve_until_done`), same check ORDER on every tick.

**Constraint (load-bearing):** `tests/test_terrarium_boot.py` monkeypatches
`harness.terrarium_boot.parent_is_gone` (e.g.
`monkeypatch.setattr("harness.terrarium_boot.parent_is_gone", fake)`).
`parent_is_gone` is imported into this module at the top
(`from harness.o2_shroom import parent_is_gone`, line 41) and calling it as
a bare name (`parent_is_gone(parent_pid)`) inside any function defined in
this module -- including the new `_run_tick_loop` -- resolves it through
this module's own globals at CALL time, so the monkeypatch is honored
automatically. Do NOT rebind it to a local variable before the loop (e.g.
`pig = parent_is_gone` then calling `pig(...)` inside the loop) -- that
would freeze the reference to whatever was imported at function-definition
time and break every test that monkeypatches
`harness.terrarium_boot.parent_is_gone`.

Read the two functions first with:
```
sed -n '650,777p' harness/terrarium_boot.py
```
to confirm they still match what's described below (nothing else should
have changed them since this plan was written).

Current bodies (for reference -- confirm live text with the sed above
before editing):

```python
def _serve_until_done(gs, agent, arco, clock=time.monotonic,
                      sleep=time.sleep, parent_pid: int | None = None,
                      console_agent=None, terrarium=None, uplink=None,
                      pacer=None) -> str:
    """<all existing docstring text, unchanged>"""
    if pacer is None:
        pacer = TickPacer(_TICK_PERIOD, sleep=sleep)
    while True:
        arco = _live_arco(terrarium, arco)
        if parent_is_gone(parent_pid):
            return "parent-gone"
        if (terrarium is not None
                and terrarium.state is not TerrariumState.ROOM_READY):
            return "no-room"
        if arco.poll() is not None:
            return "arco-exited"
        agent.poll()
        if console_agent is not None:
            console_agent.poll()
        _pump_uplink(uplink)
        gs.tick(1.0 / 44.0)
        if gs.state in (State.LOADING, State.LOADED, State.SETUP):
            return "restarted"
        if gs.state == State.IDLE and not getattr(agent, "closing", 0):
            return "completed"
        pacer.wait()


def _wait_for_load(gs, agent, arco, *, clock=time.monotonic,
                   sleep=time.sleep, parent_pid: int | None = None,
                   console_agent=None, terrarium=None, uplink=None,
                   pacer=None) -> str:
    """<all existing docstring text, unchanged>"""
    if gs.state is not State.IDLE:
        return "loaded"
    if pacer is None:
        pacer = TickPacer(_TICK_PERIOD, sleep=sleep)
    while True:
        arco = _live_arco(terrarium, arco)
        if parent_is_gone(parent_pid):
            return "parent-gone"
        if terrarium is not None and terrarium.state is not TerrariumState.ROOM_READY:
            return "no-room"
        if arco.poll() is not None:
            return "arco-exited"
        agent.poll()
        if console_agent is not None:
            console_agent.poll()
        _pump_uplink(uplink)
        gs.tick(1.0 / 44.0)
        if gs.state is not State.IDLE:
            return "loaded"
        pacer.wait()
```

The loop bodies are identical except: (a) `_wait_for_load` has a pre-loop
early return, (b) the exit-predicate check at the bottom of the loop. Both
must become one shared loop taking that predicate as a parameter.

- [ ] **Step 1: add `_pump`, right before `_wait_in_setup` (which already
has its own inline version of the same three lines -- leave `_wait_in_setup`
alone, this task only touches `_wait_for_load`/`_serve_until_done`/
`_serve_rounds`):**
```python
def _pump(agent, console_agent, uplink) -> None:
    """One tick's worth of agent/console/uplink pumping, shared by every
    poll loop in this module that needs it: agent.poll(), console_agent's
    poll() when given, then drain the uplink. Factored out of
    `_wait_for_load`/`_serve_until_done`'s identical three lines (Tier 2
    consolidation, PR B item 2)."""
    agent.poll()
    if console_agent is not None:
        console_agent.poll()
    _pump_uplink(uplink)
```

- [ ] **Step 2: add `_run_tick_loop` directly above `_wait_for_load`
(displacing nothing -- `_serve_until_done` and `_wait_for_load` both move
to call it):**
```python
def _run_tick_loop(gs, agent, arco, exit_predicate, *,
                   parent_pid: int | None = None, console_agent=None,
                   terrarium=None, uplink=None, pacer=None,
                   sleep=time.sleep) -> str:
    """Shared body of `_wait_for_load` and `_serve_until_done`: tick
    agent/console/gs once per iteration, checking parent-gone, Room-down and
    Arco-liveness in that order every time (see both callers' docstrings
    for why that order matters -- an operator-driven room-down or unload
    has already torn Arco down by the time arco.poll() would notice, so
    checking `terrarium` first avoids misreporting it as "arco-exited").

    `exit_predicate(gs, agent)` is called once per iteration, after
    `gs.tick()`, and must return a non-None reason string the instant its
    caller's own exit condition is met, else None to keep looping.

    `parent_is_gone` is looked up as this module's own global on every
    call (not captured into a local), so a test's
    `monkeypatch.setattr("harness.terrarium_boot.parent_is_gone", ...)`
    is honored from the very next tick."""
    if pacer is None:
        pacer = TickPacer(_TICK_PERIOD, sleep=sleep)
    while True:
        arco = _live_arco(terrarium, arco)
        if parent_is_gone(parent_pid):
            return "parent-gone"
        if (terrarium is not None
                and terrarium.state is not TerrariumState.ROOM_READY):
            return "no-room"
        if arco.poll() is not None:
            return "arco-exited"
        _pump(agent, console_agent, uplink)
        gs.tick(1.0 / 44.0)
        reason = exit_predicate(gs, agent)
        if reason is not None:
            return reason
        pacer.wait()
```

- [ ] **Step 3: rewrite `_wait_for_load` to call it, keeping its exact
signature and full docstring:**
```python
def _wait_for_load(gs, agent, arco, *, clock=time.monotonic,
                   sleep=time.sleep, parent_pid: int | None = None,
                   console_agent=None, terrarium=None, uplink=None,
                   pacer=None) -> str:
    """<paste the existing docstring verbatim, unchanged>"""
    if gs.state is not State.IDLE:
        return "loaded"

    def _exit(gs, agent):
        return "loaded" if gs.state is not State.IDLE else None

    return _run_tick_loop(gs, agent, arco, _exit, parent_pid=parent_pid,
                          console_agent=console_agent, terrarium=terrarium,
                          uplink=uplink, pacer=pacer, sleep=sleep)
```

- [ ] **Step 4: rewrite `_serve_until_done` to call it, keeping its exact
signature and full docstring:**
```python
def _serve_until_done(gs, agent, arco, clock=time.monotonic,
                      sleep=time.sleep, parent_pid: int | None = None,
                      console_agent=None, terrarium=None, uplink=None,
                      pacer=None) -> str:
    """<paste the existing docstring verbatim, unchanged>"""

    def _exit(gs, agent):
        if gs.state in (State.LOADING, State.LOADED, State.SETUP):
            return "restarted"
        if gs.state == State.IDLE and not getattr(agent, "closing", 0):
            return "completed"
        return None

    return _run_tick_loop(gs, agent, arco, _exit, parent_pid=parent_pid,
                          console_agent=console_agent, terrarium=terrarium,
                          uplink=uplink, pacer=pacer, sleep=sleep)
```

- [ ] **Step 5: drop `_serve_rounds`'s unused `drain_arco` parameter.**
Confirm it is genuinely unused first:
```
grep -n "drain_arco" harness/terrarium_boot.py
```
Should show only the parameter declaration (line ~780) and its docstring
paragraph (line ~804) -- never referenced in the function body, and no
caller anywhere in the repo passes it
(`grep -rn "_serve_rounds(" harness/ tests/` -- confirm none use
`drain_arco=`). Remove the parameter from the signature:
```python
def _serve_rounds(gs, agent, arco, *, parent_pid: int | None = None,
                  console_agent=None, terrarium=None,
                  uplink=None) -> str:
```
and delete the docstring paragraph that starts `` `drain_arco`, when given,
is called once per iteration of the setup `` (the paragraph right before
the closing `"""`).

- [ ] **Step 6: run the full Python suite**
```
.venv/bin/python -m pytest tests -q -p no:cacheprovider
```
Every existing `test_wait_for_load_*`, `test_serve_until_done_*`,
`test_serve_rounds_*`, `test_wait_in_setup_*` test in
`tests/test_terrarium_boot.py` must still pass unmodified -- this task
changes no test file. If any fail, the merged loop's behavior diverged from
the original; diff against the "Current bodies" block above rather than
guessing.

- [ ] **Step 7: mutation check (do this, then immediately revert -- do not
leave it committed).** Flip `_serve_until_done`'s exit predicate to prove
the consolidated tests actually exercise it:
```
sed -n '/def _exit(gs, agent):/,/return None/p' harness/terrarium_boot.py
```
Temporarily swap the `"restarted"`/`"completed"` return values in
`_serve_until_done`'s inner `_exit` (not `_wait_for_load`'s), rerun
```
.venv/bin/python -m pytest tests/test_terrarium_boot.py -q -p no:cacheprovider -k "serve_until_done or serve_rounds"
```
confirm at least one test now fails, then
```
git checkout -- harness/terrarium_boot.py
```
and redo steps 1-5 if `git checkout` discarded uncommitted work from this
task (it will, since nothing was committed yet -- re-apply the edits from
steps 1-5 before continuing, or better: do this mutation check via a
throwaway copy so nothing needs re-applying, e.g. `cp
harness/terrarium_boot.py /tmp/tb_orig.py` before mutating, and `cp
/tmp/tb_orig.py harness/terrarium_boot.py` to revert).

- [ ] **Step 8: commit**
```
git add harness/terrarium_boot.py
git commit -m "refactor(boot): merge _wait_for_load/_serve_until_done into a shared tick loop"
```

---

### Task B3: print_bit_list(registry) shared by run_stack and terrarium_boot

**Files:**
- Modify `control/bit_registry.py` (add the function, near the end of the
  file, after `errors_view` at line ~267).
- Modify `harness/run_stack.py` (`main()`, `--list-bits` branch, currently
  lines 953-959).
- Modify `harness/terrarium_boot.py` (`main()`, `--list-bits` branch,
  currently lines 1608-1617).
- Test: `tests/test_run_stack.py` (add a new test near
  `test_list_bits_prints_every_discovered_bit_and_exits_zero`, line 744).
- Test: consider a small direct unit test of `print_bit_list` itself in
  `tests/test_bit_registry.py` (optional but recommended -- see step 2).

**Interfaces:**
- Produces: `control.bit_registry.print_bit_list(registry: BitRegistry, *,
  file=sys.stdout, err_file=sys.stderr) -> None`. Prints one tab-separated
  line per `registry.list_view(include_hidden=True)` row (name, version,
  kind, comma-joined room_types, start.when, description, and a trailing
  `\tDISABLED` when the row's `enabled` is False), then one
  `error: {path}: {message}` line per `registry.errors_view()` entry to
  `err_file`.
- Consumes: `BitRegistry.list_view`/`errors_view` (already defined,
  `control/bit_registry.py` lines 238-268 -- unchanged by this task).

`control/bit_registry.py` already `import sys` (line 9) and already
imports nothing from `harness/`, so adding this function creates no import
cycle: both `harness/run_stack.py` and `harness/terrarium_boot.py` already
import `from control.bit_registry import BitRegistry`.

This is TDD: write the failing run_stack test FIRST (step 1), confirm it
fails, THEN implement (steps 2-4).

- [ ] **Step 1 (RED): add a DISABLED-column test to
tests/test_run_stack.py, right after
`test_list_bits_prints_every_discovered_bit_and_exits_zero` (line ~759):**
```python
def test_list_bits_marks_a_disabled_bit(capsys, tmp_path):
    """run_stack's --list-bits used to omit the DISABLED column
    terrarium_boot's copy already had -- both now share
    control.bit_registry.print_bit_list."""
    from harness.run_stack import main
    import sys as _sys

    pkg = tmp_path / "offbit"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(
        '[bit]\nname = "OffBit"\nentry = "m:C"\n'
        'requires_terrarium_api = 1\nenabled = false\n')

    argv = ["run_stack.py", "--list-bits", "--bits-root", str(tmp_path)]
    old_argv = _sys.argv
    _sys.argv = argv
    try:
        with pytest.raises(SystemExit) as exc_info:
            main()
    finally:
        _sys.argv = old_argv

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "OffBit" in out
    assert "DISABLED" in out
```
Before trusting this test, confirm `run_stack.py` actually has a
`--bits-root`-shaped way to point `discover_registry` at a tmp_path -- run:
```
grep -n "bits.root\|bits_root\|--config\|discover_registry" harness/run_stack.py | head -20
```
If there is no such flag and `discover_registry(args.config)` instead reads
bit roots from a terrarium config file's `bit_paths`, adapt the test to
build a minimal `[terrarium]` config TOML in `tmp_path` pointing
`bit_paths` at the `offbit` package's parent directory, and pass
`--config <that file>` instead of a nonexistent `--bits-root`. Whichever
shape it turns out to be, the point of the test is unchanged: a disabled
bit's row must contain `DISABLED` in `run_stack --list-bits` output.

- [ ] **Step 2: confirm RED**
```
.venv/bin/python -m pytest tests/test_run_stack.py -q -p no:cacheprovider -k disabled
```
Expect a failure (current run_stack output has no DISABLED column).

- [ ] **Step 3 (GREEN): add print_bit_list to control/bit_registry.py**,
after `errors_view` (current end of file, ~line 267-268):
```python
def print_bit_list(registry: "BitRegistry", *, file=None, err_file=None) -> None:
    """Print `--list-bits` output: one tab-separated row per discovered
    package (name, version, kind, comma-joined room_types, start.when,
    description), with a trailing DISABLED column for a disabled bit
    (`bit.enabled = false`). Shared by harness/run_stack.py and
    harness/terrarium_boot.py's `--list-bits` (Tier 2 consolidation, PR B
    item 3) -- run_stack's own copy used to omit the DISABLED column."""
    out = sys.stdout if file is None else file
    err = sys.stderr if err_file is None else err_file
    for row in registry.list_view(include_hidden=True):
        rooms = ",".join(row["room_types"])
        status = "" if row.get("enabled", True) else "\tDISABLED"
        print(f"{row['name']}\t{row['version']}\t{row['kind']}\t"
             f"{rooms}\t{row['start']['when']}\t{row['description']}"
             f"{status}", file=out)
    for e in registry.errors_view():
        print(f"error: {e['path']}: {e['message']}", file=err)
```

- [ ] **Step 4: switch both callers to it.**

`harness/run_stack.py`, replace the `--list-bits` branch (currently):
```python
    if args.list_bits:
        registry = discover_registry(args.config)
        for row in registry.list_view(include_hidden=True):
            rooms = ",".join(row["room_types"])
            print(f"{row['name']}\t{row['version']}\t{row['kind']}\t"
                 f"{rooms}\t{row['start']['when']}\t{row['description']}")
        for err in registry.errors_view():
            print(f"error: {err['path']}: {err['message']}", file=sys.stderr)
        raise SystemExit(0)
```
with:
```python
    if args.list_bits:
        registry = discover_registry(args.config)
        print_bit_list(registry)
        raise SystemExit(0)
```
and add `print_bit_list` to its `from control.bit_registry import
BitRegistry` line (line 55):
```python
from control.bit_registry import BitRegistry, print_bit_list
```

`harness/terrarium_boot.py`, replace the `--list-bits` branch (currently
lines ~1608-1616):
```python
    if args.list_bits:
        for row in registry.list_view(include_hidden=True):
            rooms = ",".join(row["room_types"])
            status = "" if row.get("enabled", True) else "\tDISABLED"
            print(f"{row['name']}\t{row['version']}\t{row['kind']}\t"
                 f"{rooms}\t{row['start']['when']}\t{row['description']}"
                 f"{status}")
        for err in registry.errors_view():
            print(f"error: {err['path']}: {err['message']}", file=sys.stderr)
        sys.exit(0)
```
with:
```python
    if args.list_bits:
        print_bit_list(registry)
        sys.exit(0)
```
and add `print_bit_list` to its `from control.bit_registry import
BitRegistry` line (line 25):
```python
from control.bit_registry import BitRegistry, print_bit_list
```

- [ ] **Step 5: confirm GREEN**
```
.venv/bin/python -m pytest tests/test_run_stack.py tests/test_terrarium_boot.py -q -p no:cacheprovider -k list_bits
```
All list-bits tests (both files' existing ones plus the new one) pass.

- [ ] **Step 6: full suite**
```
.venv/bin/python -m pytest tests -q -p no:cacheprovider
```

- [ ] **Step 7: commit**
```
git add control/bit_registry.py harness/run_stack.py harness/terrarium_boot.py tests/test_run_stack.py
git commit -m "refactor(bits): share print_bit_list between run_stack and terrarium_boot"
```
Note in the PR description that this is also a genuine bugfix: run_stack's
`--list-bits` previously omitted the DISABLED column terrarium_boot's copy
already had.

---

### Task B4: move parent_is_gone to harness/signals.py

**Files:**
- Modify `harness/signals.py` (add the function).
- Modify `harness/o2_shroom.py` (remove the function body, lines 219-242;
  re-export instead).
- Modify `harness/run_stack.py` (line 63: import source).

**Interfaces:**
- Produces: `harness.signals.parent_is_gone(expected_ppid, getppid=os.getppid) -> bool`
  (same signature/behavior/docstring as today's
  `harness.o2_shroom.parent_is_gone`).
- Preserves: `harness.o2_shroom.parent_is_gone` still resolves (existing
  importers `harness/terrarium_boot.py` line 41
  (`from harness.o2_shroom import parent_is_gone`) and
  `tests/test_o2_shroom.py` (`from harness.o2_shroom import
  parent_is_gone`, 3 call sites) keep working unmodified) -- via
  re-export, not a copy.
- Changes: `harness/run_stack.py` imports it from `harness.signals`
  instead of `harness.o2_shroom`, and (confirmed by grep below) no longer
  needs `harness.o2_shroom` for anything else.

- [ ] **Step 1: confirm the function's current text**
```
sed -n '219,242p' harness/o2_shroom.py
```
It should read:
```python
def parent_is_gone(expected_ppid, getppid=os.getppid) -> bool:
    """True once this process's parent is no longer the one that spawned it.

    The Room simulator is spawned by harness/terrarium_boot.py and, with
    --no-join, never exits on its own: main()'s loop below waits for a
    /release that only a live Control sends. So a Terrarium that dies
    without running its shutdown leaves this process running forever, and
    o2litepy reconnects it to the NEXT Arco that starts (o2lite.py:912
    connects whenever _tcp_socket is None, and _id_handler at :601
    re-announces every service on connect). There it claims this same dev
    name, and O2 refuses the new run's own simulator with "not from service
    provider" (o2/src/bridge.cpp:231-237) -- silently, since /_o2/*/sv is
    fire-and-forget. See docs/superpowers/specs/
    2026-08-14-room-simulator-service-collision-design.md.

    Compares against the pid the parent stamped in rather than watching
    getppid() for a change: if the parent died before this process read its
    argv, getppid() is ALREADY 1 and a change detector would wait forever.
    Comparison against a recorded value is correct in either order.

    expected_ppid None means the caller did not ask for this guard -- the
    default for a hand-run device -- and it never fires.
    """
    return expected_ppid is not None and getppid() != expected_ppid
```
If it differs, adapt the following steps to the live text rather than
blindly pasting.

- [ ] **Step 2: add it to harness/signals.py**, after the module docstring
and imports, before `_raise_keyboard_interrupt`:
```python
from __future__ import annotations

import os
import signal


def parent_is_gone(expected_ppid, getppid=os.getppid) -> bool:
    """<paste the docstring from Step 1 verbatim>"""
    return expected_ppid is not None and getppid() != expected_ppid


def _raise_keyboard_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt
```
(`harness/signals.py` currently only `import signal`; add `import os`
above it, alphabetized, matching the repo's existing import-ordering
convention in this file.)

- [ ] **Step 3: remove the body from harness/o2_shroom.py, re-export
instead.** Delete lines 219-242 (the full function from Step 1) and in
their place write:
```python
from harness.signals import parent_is_gone  # noqa: F401  (re-exported)
```
Do NOT put this new import at that mid-file location -- imports belong at
the top of the file. Instead: delete the function body entirely from its
current location (~line 219), and add
`from harness.signals import parent_is_gone` to `harness/o2_shroom.py`'s
existing top-of-file import block (check
`sed -n '20,35p' harness/o2_shroom.py` for the current block -- it already
imports `from harness import markers` and
`from harness.arco_paths import ARCO_PYTHONPATH, ensure_o2litepy`; add the
new import alphabetized among the `harness.*` imports). Confirm
`harness/o2_shroom.py` still uses `parent_is_gone` at its own three call
sites (lines ~697, ~728, ~784 per
`grep -n parent_is_gone harness/o2_shroom.py`) -- those are unchanged,
they just now resolve through the re-export.

- [ ] **Step 4: switch run_stack.py's import.**
Change line 63 from:
```python
from harness.o2_shroom import parent_is_gone
```
to:
```python
from harness.signals import parent_is_gone
```
(it sits next to `from harness.signals import sigterm_as_keyboard_interrupt`
on the following line today -- consider merging into one import line:
`from harness.signals import parent_is_gone, sigterm_as_keyboard_interrupt`).

- [ ] **Step 5: verify run_stack.py no longer imports o2_shroom for
anything.**
```
grep -n "o2_shroom" harness/run_stack.py
```
Expected remaining hits are all non-import: the subprocess argv building
`"-m", "harness.o2_shroom"` (spawns the Testshroom as a child process, ~line
234), and prose in help text/comments mentioning `o2_shroom` by name
(~lines 482, 596, 743). None of these are a Python import of the
`harness.o2_shroom` module -- confirm specifically with
`grep -n "^from harness.o2_shroom\|^import harness.o2_shroom"
harness/run_stack.py` and expect NO output. If any other symbol besides
`parent_is_gone` WAS being imported from `harness.o2_shroom` (this plan
found none), keep that import and only redirect `parent_is_gone`'s source,
and say so explicitly in the PR description per the spec's instruction to
report if run_stack needs anything else from o2_shroom.

- [ ] **Step 6: run the affected tests**
```
.venv/bin/python -m pytest tests/test_o2_shroom.py tests/test_run_stack.py tests/test_terrarium_boot.py -q -p no:cacheprovider
```
`tests/test_o2_shroom.py`'s three `parent_is_gone` tests must still pass
unmodified (they import `from harness.o2_shroom import parent_is_gone`,
which now resolves via the re-export).

- [ ] **Step 7: full suite**
```
.venv/bin/python -m pytest tests -q -p no:cacheprovider
```

- [ ] **Step 8: commit**
```
git add harness/signals.py harness/o2_shroom.py harness/run_stack.py
git commit -m "refactor(harness): move parent_is_gone to signals.py, re-export from o2_shroom"
```

---

### Task B5: correct run_profile.py's docstring (get_typed dropped)

**Scope note:** the spec's item 5 (shared `ConfigError` base plus a raising `get_typed`
helper) was dropped during planning. Reading every isinstance check in
`control/terrarium_config.py` showed only 3 to 5 convert cleanly (most fetch with a
non-None `.get(key, default)` or carry domain-specific messages), so the helper plus its
tests would add more lines than it removes. Only the stale docstring is fixed.

**Files:**
- Modify: `control/run_profile.py` (the `parse_profile` docstring, about lines 40-50)

**Interfaces:** none.

- [ ] **Step 1: edit the docstring.** Find the last clause of `parse_profile`'s docstring:

  ```python
      not turn a working profile into a launch failure -- unlike a manifest's
      unknown keys, which are strict (see bit_config.parse_manifest)."""
  ```

  Replace it with:

  ```python
      not turn a working profile into a launch failure. A Bit manifest's
      unknown keys warn the same way (see bit_config._warn_unknown_keys)."""
  ```

  Confirm the exact current text first with
  `grep -n "which are strict" control/run_profile.py`; if the wording differs, keep the
  intent (manifest unknown keys warn, not raise).

- [ ] **Step 2: run the profile tests**

  ```
  .venv/bin/python -m pytest tests/test_run_profile.py -q -p no:cacheprovider
  ```
  Expected: all pass (docstring-only change).

- [ ] **Step 3: commit**

  ```
  git add control/run_profile.py
  git commit -m "docs(run_profile): manifest unknown keys warn, they are not strict"
  ```

---

### Task B6: parse_cc_ref(ref, where=None) in control/audio.py

**Files:**
- Modify `control/audio.py` (lines 29, 102-103, 153; the `_CC_PREFIX`
  constant and `_cc_number` function).
- Modify `control/role_config.py` (lines 29, 217-230, 280-281).
- Test: `tests/test_audio.py` and/or `tests/test_role_config.py` (add
  coverage for the new shared function -- check which file exists first).

**Interfaces:**
- Produces: `control.audio.parse_cc_ref(ref, where: str | None = None) -> int`.
  Parses a `"cc:<n>"` reference; raises `ValueError` (not a new exception
  type -- both current implementations raise `ValueError`, and
  `role_config`'s callers/tests expect `ValueError`) when `ref` is not a
  string starting with `cc:`, when the suffix does not parse as an int, or
  when the parsed number is outside 0-127. `where`, when given, prefixes
  the message (`f"{where}: ..."`); when `None`, a generic location-less
  message is used.
- Removes: `control.audio._cc_number(ref)` (unvalidated) and
  `control.role_config._cc_number(ref, where)` (validating), and the
  duplicated `_CC_PREFIX = "cc:"` constant in `role_config.py` (the one in
  `audio.py` stays -- it's now the single copy `parse_cc_ref` uses).
- `control/role_config.py` already does `from control.audio import
  WELCOME_INSTRUMENTS` (confirmed, `control/role_config.py` line 20), so
  adding `parse_cc_ref` to that same import line closes no new cycle --
  the module docstring already documents this one-directional edge
  (`control.audio` imports `control.roles` only, never `role_config`).

**Runtime-safety finding (report this in the PR description, do not treat
it as something to fix):** `control.audio.AudioBridge.on_grant` reads
`role.ugen_manifest` off an already-adopted `Role`. Every `Role`'s
`ugen_manifest` is built from a Bit's `light_manifest`/`ugen_manifest`
declarations, which `control.role_config.validate_ugen_manifest` (called
from `validate_role_declarations`, itself invoked when a Bit's manifest is
loaded/validated) already runs `_cc_number`/`parse_cc_ref`-equivalent
validation over, including every lane's `source`/`dest`. So by the time
`AudioBridge.on_grant` runs at role-adoption time, the lanes it reads have
already passed the same check `parse_cc_ref` will now also apply at the
`audio.py` call site. This plan's authors traced every caller of
`AudioBridge.on_grant`/`AudioBridge(...)`
(`grep -rn "AudioBridge(" --include=*.py .`, and
`grep -rn "\.on_grant(" --include=*.py .`) and found none that could feed
it a manifest bypassing `role_config`'s load-time validation. Confirm this
is still true at execution time by re-running both greps, and if a new
caller has appeared since this plan was written that does NOT go through
Bit-manifest loading, flag it in the PR description rather than silently
tightening its behavior -- adding validation to `audio.py`'s path is a
behavior change (a call that used to crash with a raw `IndexError`/
`ValueError` on a malformed ref, or possibly silently produce a wrong int,
now raises a clean `ValueError` instead) and the point of this check is to
confirm nothing currently relies on the old unvalidated leniency.

- [ ] **Step 1 (RED): write the failing test first.**
Check which test file already covers `control/audio.py`:
```
ls tests/test_audio.py tests/test_role_config.py 2>/dev/null
grep -n "_cc_number\|def test_" tests/test_audio.py 2>/dev/null | head -5
grep -n "_cc_number\|def test_" tests/test_role_config.py 2>/dev/null | head -5
```
Add to whichever file already exists (prefer `tests/test_audio.py` since
`parse_cc_ref` lives in `control/audio.py`; create it only if it truly
doesn't exist -- re-check, this plan's authors did not confirm its
presence):
```python
import pytest

from control.audio import parse_cc_ref


def test_parse_cc_ref_parses_a_valid_reference():
    assert parse_cc_ref("cc:74") == 74


def test_parse_cc_ref_refuses_a_non_cc_string():
    with pytest.raises(ValueError, match="cc:"):
        parse_cc_ref("note:74")


def test_parse_cc_ref_refuses_a_non_numeric_suffix():
    with pytest.raises(ValueError, match="controller number"):
        parse_cc_ref("cc:seventy")


def test_parse_cc_ref_refuses_an_out_of_range_number():
    with pytest.raises(ValueError, match="0-127"):
        parse_cc_ref("cc:200")


def test_parse_cc_ref_prefixes_the_message_with_where_when_given():
    with pytest.raises(ValueError, match="role 'x' lane\\[0\\] source"):
        parse_cc_ref("bogus", where="role 'x' lane[0] source")


def test_parse_cc_ref_uses_a_generic_message_with_no_where():
    with pytest.raises(ValueError):
        parse_cc_ref("bogus")
```

- [ ] **Step 2: confirm RED**
```
.venv/bin/python -m pytest tests/test_audio.py -q -p no:cacheprovider -k parse_cc_ref
```
(adjust the path if you added the tests to a different file per Step 1's
discovery).

- [ ] **Step 3 (GREEN): implement parse_cc_ref in control/audio.py.**
Current (lines 102-103):
```python
def _cc_number(ref: str) -> int:
    return int(ref[len(_CC_PREFIX):])
```
Replace with:
```python
def parse_cc_ref(ref, where: str | None = None) -> int:
    """Parse a 'cc:<n>' reference (0-127). Both lane ends use this form:
    the mapping to a synth parameter is FluidSynth's own reading of the
    controller number, so there is no destination vocabulary for Control to
    invent here. `where` locates the error message for a caller that has
    one (control.role_config, validating a Bit's authored manifest at load
    time); this module's own caller (AudioBridge.on_grant, reading an
    already-validated Role) has none to give, since its lanes are expected
    to have already passed this same check at load time -- see Tier 2
    consolidation PR B item 6 for why that's safe."""
    loc = where or "cc ref"
    if not isinstance(ref, str) or not ref.startswith(_CC_PREFIX):
        raise ValueError(f"{loc}: must be a {_CC_PREFIX!r} reference, got {ref!r}")
    try:
        num = int(ref[len(_CC_PREFIX):])
    except ValueError:
        raise ValueError(f"{loc}: {ref!r} is not a controller number") from None
    if not 0 <= num <= 127:
        raise ValueError(f"{loc}: controller {num} is outside 0-127")
    return num
```
Update its one call site in this same file (line ~153, inside
`AudioBridge.on_grant`):
```python
# before
lanes = {_cc_number(lane["source"]): _cc_number(lane["dest"])
         for lane in decl.get("lanes", [])}
# after
lanes = {parse_cc_ref(lane["source"]): parse_cc_ref(lane["dest"])
         for lane in decl.get("lanes", [])}
```

- [ ] **Step 4: switch control/role_config.py to use it.**
Add `parse_cc_ref` to the existing import (line 20):
```python
from control.audio import WELCOME_INSTRUMENTS, parse_cc_ref
```
Delete the duplicated constant (line 29):
```python
_CC_PREFIX = "cc:"
```
Delete the local function entirely (lines 217-230):
```python
def _cc_number(ref, where: str) -> int:
    """Parse a 'cc:<n>' reference. Both lane ends use this form: the mapping
    to a synth parameter is FluidSynth's own reading of the controller number,
    so there is no destination vocabulary for Control to invent here."""
    if not isinstance(ref, str) or not ref.startswith(_CC_PREFIX):
        raise ValueError(f"{where}: must be a {_CC_PREFIX!r} reference, got {ref!r}")
    try:
        num = int(ref[len(_CC_PREFIX):])
    except ValueError:
        raise ValueError(
            f"{where}: {ref!r} is not a controller number") from None
    if not 0 <= num <= 127:
        raise ValueError(f"{where}: controller {num} is outside 0-127")
    return num
```
Update its two call sites (lines ~280-281):
```python
# before
_cc_number(lane["source"], f"{lane_where} source")
_cc_number(lane["dest"], f"{lane_where} dest")
# after
parse_cc_ref(lane["source"], f"{lane_where} source")
parse_cc_ref(lane["dest"], f"{lane_where} dest")
```

- [ ] **Step 5: confirm GREEN and run both modules' full test files**
```
.venv/bin/python -m pytest tests/test_audio.py tests/test_role_config.py -q -p no:cacheprovider
```
(only the files that actually exist -- adjust per Step 1's discovery).
Every existing role_config validation test that exercises a bad `cc:` lane
ref must still raise `ValueError` with the same located message shape
(`"{lane_where} source: ..."`).

- [ ] **Step 6: full suite**
```
.venv/bin/python -m pytest tests -q -p no:cacheprovider
```

- [ ] **Step 7: commit**
```
git add control/audio.py control/role_config.py tests/test_audio.py tests/test_role_config.py
git commit -m "refactor(audio): share parse_cc_ref between audio.py and role_config.py"
```
(drop whichever test file path from the `git add` wasn't actually touched).

---

### Task B7: rename DeviceLinkAgent.server to .transport

**Files:**
- Modify `devicelink/agent.py` (constructor param ~line 97, attribute
  assignment ~line 102, 7 body usages + 2 comments referencing
  `self.server`: lines 172, 823, 824, 1269, 1277, 1469, 1487, 1524, 1713).
- Modify `tests/test_devicelink_agent.py` (10 sites: lines 834, 910, 1534,
  1535, 1549, 1554, 1555, 2337, 2361, 2366).
- Modify `tests/test_terrarium_boot.py` (1 site -- locate by grep, not line
  number, since PR A shifts this file's lines; the assertion reads
  `assert agent.server is transport` today, in a test whose name can be
  found via `grep -n "agent.server is transport" tests/test_terrarium_boot.py`).
- Do NOT modify `console/agent.py` -- its `self.server` (constructor param
  and 4 usages, confirmed via
  `grep -n "self.server" console/agent.py`) is `ConsoleServer`, an
  unrelated class. This task's grep in Step 1 already excludes it; do not
  let a broader find-and-replace catch it.
- Do NOT modify anything matching `console.server`/`ConsoleServer`/
  `http.server`/`websockets.sync.server` anywhere in the repo -- all
  unrelated modules that happen to share the substring `server`.

**Interfaces:**
- Renames: `DeviceLinkAgent.__init__`'s `server` parameter ->
  `transport`. `self.server` attribute -> `self.transport`. Every
  `agent.server`/`self.server` access at the sites listed above ->
  `agent.transport`/`self.transport`.
- Unaffected: `DeviceLinkAgent(gs, <positional>, ...)` call sites that pass
  the transport positionally (the overwhelming majority in the test suite
  and in `harness/terrarium_boot.py`) need NO change -- only call sites
  using the keyword `server=` (this plan's authors found none via
  `grep -rn "DeviceLinkAgent(.*server=" --include=*.py .` -- re-run this
  before starting to confirm still none) or accessing `.server` as an
  attribute need editing.

- [ ] **Step 1: get the authoritative, current site list.** This is the
grep that proves completeness -- run it BEFORE editing and again AFTER, and
the "after" run must be empty apart from `console/agent.py` and unrelated
`console.server`/`http.server`/`websockets.sync.server` hits:
```
grep -rn "\.server\b" --include=*.py . | grep -v "\.venv" | grep -v "console/agent.py"
```
As read while writing this plan, that produced exactly:
```
tests/test_devicelink_agent.py:834:    agent.server.bind_dev(...)
tests/test_devicelink_agent.py:910:    agent.server.bind_dev(...)
tests/test_devicelink_agent.py:1534:    accent_frame = _last_leds_payload(agent.server, accent_dev)
tests/test_devicelink_agent.py:1535:    main_frame = _last_leds_payload(agent.server, main_dev)
tests/test_devicelink_agent.py:1549:    main_before = _last_leds_payload(agent.server, main_dev)
tests/test_devicelink_agent.py:1554:    accent_frame = _last_leds_payload(agent.server, accent_dev)
tests/test_devicelink_agent.py:1555:    main_frame = _last_leds_payload(agent.server, main_dev)
tests/test_devicelink_agent.py:2337:    frames = {dev: bytes(msg["args"][0]) for dev, msg in agent.server.sent}
tests/test_devicelink_agent.py:2361:    agent.server.sent.clear()
tests/test_devicelink_agent.py:2366:    assert not [m for dev, m in agent.server.sent ...
tests/test_terrarium_boot.py:447:        assert agent.server is transport
devicelink/agent.py:102:        self.server = server
devicelink/agent.py:172:        # (comment) later call self.server.drop_dev(dev) unconditionally...
devicelink/agent.py:823:        self.server.drain_new_clients()
devicelink/agent.py:824:        for client, msg in self.server.drain_inbound():
devicelink/agent.py:1269:        self.server.bind_dev(dev, client, protoversion=protoversion)
devicelink/agent.py:1277:        self.server.bind_dev(dev, client)
devicelink/agent.py:1469:            self.server.drop_dev(dev)
devicelink/agent.py:1487:        (comment) self.server.drop_dev(dev) is skipped when...
devicelink/agent.py:1524:            self.server.drop_dev(dev)
devicelink/agent.py:1713:        self.server.send(dev, msg)
```
Plus the constructor parameter itself (line ~97: `def __init__(self,
game_server: GameServer, server, *, clock, ...)`), which `\.server\b`
doesn't match (it's a bare parameter name, not an attribute access) --
handle it explicitly in Step 2. `tests/test_terrarium_boot.py:447` is a
PRE-PR-A line number; find it in the live file via
`grep -n "agent.server is transport" tests/test_terrarium_boot.py` instead
of trusting `447`.

- [ ] **Step 2: devicelink/agent.py -- rename the parameter and
attribute.**
```python
# before (~line 97)
    def __init__(self, game_server: GameServer, server, *, clock,
                 capability=None, room_audio=None, horizon: float = 0.0,
                 room_profile=None, on_room_frame=None, on_join_denied=None,
                 stale_timeout: float = 15.0, outputs_for=None):
        self.game_server = game_server
        self.server = server
# after
    def __init__(self, game_server: GameServer, transport, *, clock,
                 capability=None, room_audio=None, horizon: float = 0.0,
                 room_profile=None, on_room_frame=None, on_join_denied=None,
                 stale_timeout: float = 15.0, outputs_for=None):
        self.game_server = game_server
        self.transport = transport
```
Then replace every remaining `self.server` in this file (lines 172 comment,
823, 824, 1269, 1277, 1469, 1487 comment, 1524, 1713) with `self.transport`.
Since this file has no OTHER `.server` usage (confirmed by Step 1's grep),
a scoped find-and-replace within `devicelink/agent.py` alone
(`self.server` -> `self.transport`, all occurrences including the two
comments) is safe here -- just re-run
`grep -n "self\.server" devicelink/agent.py` afterward and confirm zero
hits, then `grep -n "self\.transport" devicelink/agent.py` and confirm 9
hits (1 assignment + 8 usages/comments).

- [ ] **Step 3: tests/test_devicelink_agent.py -- rename `agent.server` to
`agent.transport` at all 10 sites from Step 1's list.** Confirm no other
meaning of `.server` exists in this file first:
```
grep -n "\.server\b" tests/test_devicelink_agent.py
```
If that's exactly the 10 lines from Step 1, a scoped find-and-replace
(`agent.server` -> `agent.transport`) across the whole file is safe.
Re-run the grep afterward and confirm zero hits for `agent\.server\b`.

- [ ] **Step 4: tests/test_terrarium_boot.py -- one site, found by
pattern.**
```
grep -n "agent.server" tests/test_terrarium_boot.py
```
Change the one match (`assert agent.server is transport` or equivalent) to
`assert agent.transport is transport`. If the grep returns more than one
match, or none, STOP and re-read the surrounding test (PR A may have
altered this assertion's shape) rather than guessing at a replacement.

- [ ] **Step 5: harness/terrarium_boot.py -- REQUIRED: remove the `server = transport`
alias in `build()` (the spec's "devicelink_server return slot"), per the "after" block below.
Leave `main()`'s destructured local and every test's `gs, server, agent, ... = build(...)`
local alone.** This file has NO
`.server` attribute access on a `DeviceLinkAgent` (confirmed by Step 1's
grep showing zero hits in this file) -- its `build()` function just uses a
local variable literally named `server` as an alias for its own `transport`
parameter (`server = transport`, ~line 349), passed positionally into
`DeviceLinkAgent(gs, server, ...)` (~line 423) and returned as the second
element of `build()`'s 6-tuple (~line 441: `return gs, server, agent,
terrarium.arco, teardown, terrarium`), and `main()` destructures that
return into a local also named `server` (~line 1744:
`gs, server, agent, arco, teardown, terrarium = build(...)`). None of this
is the renamed attribute -- it's independent local-variable naming, and
`tests/test_terrarium_boot.py`'s many `gs, server, agent, ... = build(...)`
call sites are the same: local names, not `DeviceLinkAgent.server`
accesses, and do NOT need to change for correctness. For readability
consistency with the rename, you MAY clean up `harness/terrarium_boot.py`'s
own `build()`:
```python
# before (~line 349)
    server = transport
    ...
# (~line 423)
        agent = DeviceLinkAgent(gs, server, room_audio=room_audio,
        ...
# (~line 441)
    return gs, server, agent, terrarium.arco, teardown, terrarium
```
```python
# after -- delete the `server = transport` alias entirely (it was always
# a plain alias to the `transport` parameter already in scope), and use
# `transport` directly at both remaining sites:
    ... (no alias line)
    ...
        agent = DeviceLinkAgent(gs, transport, room_audio=room_audio,
        ...
    return gs, transport, agent, terrarium.arco, teardown, terrarium
```
If you make this change, do NOT also rename `main()`'s destructured local
at line ~1744 (`gs, server, agent, ... = build(...)`) to `transport` --
`main()` already has its own local named `transport`
(`transport = O2LiteTransport()`, a few lines above the `build()` call) and
reusing that name for the destructured return value, while harmless (same
object), reads confusingly next to the still-live `transport` local it
would shadow. Leave `main()`'s destructured local named `server`, or
rename it to something distinct like `devicelink_transport` if you want
the cleanup -- your call, this is optional polish, not a required part of
the rename contract. Do not touch any of the many
`gs, server, agent, ... = build(...)` call sites in
`tests/test_terrarium_boot.py` for this reason either.

- [ ] **Step 6: re-run the completeness grep from Step 1.**
```
grep -rn "\.server\b" --include=*.py . | grep -v "\.venv" | grep -v "console/agent.py"
```
Expected remaining output: only unrelated hits
(`console.server`/`ConsoleServer`/`http.server`/`websockets.sync.server`
imports, e.g. `tests/test_console_server.py`,
`harness/terrarium_boot.py:1913`'s `from console.server import
ConsoleServer`, `harness/www_server.py`'s `from http.server import ...`).
Zero hits for `agent.server` or `self.server` tied to `DeviceLinkAgent`.
Paste this grep's output into the PR description as the completeness
proof.

- [ ] **Step 7: run the affected suites**
```
.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_terrarium_boot.py -q -p no:cacheprovider
```

- [ ] **Step 8: full suite**
```
.venv/bin/python -m pytest tests -q -p no:cacheprovider
```

- [ ] **Step 9: commit**
```
git add devicelink/agent.py tests/test_devicelink_agent.py tests/test_terrarium_boot.py harness/terrarium_boot.py
git commit -m "refactor(devicelink): rename DeviceLinkAgent.server to .transport"
```

---

### Task B8: final verification

**Files:** none modified except `docs/MM_TERRARIUM.md`.

- [ ] **Step 1: full Python suite**
```
.venv/bin/python -m pytest tests -q -p no:cacheprovider
```
Expect 0 failures. Compare the collected test count against the count
before this PR's first commit (`git log --oneline` to find the base
commit, `git stash` is NOT needed -- just note the count from Task B1's
Step 14 run) -- it should only have grown (Task B1's `dom.test.js` is JS,
not Python; Task B3's new run_stack test and Task B5/6's new test files are
the only Python growth expected).

- [ ] **Step 2: full JS suite**
```
node --test tests/js/*.test.js
```
Expect 0 failures, `dom.test.js` present and passing.

- [ ] **Step 3: mutation check 1 -- merged loop's exit predicate.**
(Task B2 already did this once for `_serve_until_done`; repeat it here as
the PR-level gate covering BOTH merged functions together.) In
`harness/terrarium_boot.py`, temporarily make `_wait_for_load`'s inner
`_exit` always return `None` (never exits):
```python
    def _exit(gs, agent):
        return None  # mutated: was `"loaded" if gs.state is not State.IDLE else None`
```
Run:
```
.venv/bin/python -m pytest tests/test_terrarium_boot.py -q -p no:cacheprovider -k wait_for_load --timeout=30
```
(add `--timeout=30` via `pytest-timeout` if installed, or just watch for a
hang and Ctrl-C -- a test that used to return promptly on "loaded" will now
spin forever since the predicate never fires; if no timeout plugin is
available, just run one specific fast test and manually interrupt it after
a few seconds, confirming it does NOT return.) Then revert:
```
git checkout -- harness/terrarium_boot.py
```
(safe: Task B2 already committed the real change, so this discards only the
mutation).

- [ ] **Step 5: run ./smoke-test.sh end to end.**
Read it first if you haven't:
```
cat smoke-test.sh
```
It is a thin wrapper around `.venv/bin/python -m harness.run_stack "$@"`
that resolves an arco checkout's `pyarco`/`o2litepy` onto `PYTHONPATH`
before exec'ing. Resolution order: `$ARCO_ROOT` env var if set, else
`$PYTHONPATH` if already set (left alone), else a sibling `arco` checkout
next to this repo's git common dir (i.e. `../arco` relative to wherever
`git rev-parse --git-common-dir` resolves, which for a worktree is the
MAIN checkout's location, not this worktree's own directory) -- it refuses
to start if that resolved directory has no `pyarco/` subdirectory.
**Prerequisite: a real `arco` checkout with `pyarco/` must exist at one of
those locations** (or `ARCO_ROOT`/`PYTHONPATH` must already point at one).
Check for it first:
```
ARCO_GUESS="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/../arco"
ls "$ARCO_GUESS/pyarco" 2>&1 | head -1
```
If that fails and no `ARCO_ROOT`/`PYTHONPATH` is set, `./smoke-test.sh`
CANNOT run in this environment -- state that plainly in the PR description
(do not fabricate a successful run) and skip to Step 6. If it succeeds,
run:
```
./smoke-test.sh --ci --seconds 10 --devices 1
```
Expected success shape (per `harness/run_stack.py`'s `--ci` contract,
`harness/markers.py`'s marker constants, and the README's documented
output): stdout carries `logs: <dir>` first, then, once the stack is up,
lines for `CONTROL_ROOM_LOADED`, `CONTROL_TRANSPORT_READY`, `BROWSE_URL`
lines for the Console/Room/guest surfaces, `CONTROL_SETUP_HOLD`, then (with
`--devices 1`) the spawned Testshroom's own `DEVICE_CLOCK_SYNCED`/
`DEVICE_ROLE_GRANTED` lines, and finally (since `--ci` exits after
`--seconds` rather than running forever) a `stack run <stage>; logs in
<dir>` line with exit code 0. A non-zero exit or a marker missing from
stdout is a real failure -- do not wave it off as an environment quirk
without first reading the tail of the log files `run_stack` printed the
path to.

- [ ] **Step 6: update docs/MM_TERRARIUM.md.**
Three things changed that this doc should reflect: the `DeviceLinkAgent`
rename, `parent_is_gone`'s move to `harness/signals.py`, and the new
`console/static/dom.js`. Find the passages to touch with:
```
grep -n "o2_shroom.*parent_is_gone\|parent_is_gone.*o2_shroom" docs/MM_TERRARIUM.md
grep -n "console/static/ is a directory now" docs/MM_TERRARIUM.md
grep -n "DeviceLinkAgent" docs/MM_TERRARIUM.md | grep -i "server\b"
```
As read while writing this plan: lines ~1133-1139 and ~1167 discuss
`harness/o2_shroom.py --exit-with-parent` and `o2_shroom.parent_is_gone` by
name -- update these to say the predicate now lives in
`harness/signals.py` and is re-exported from `o2_shroom.py` for backward
compatibility, not defined there. Line ~1734 (`**console/static/ is a
directory now**...`) documents the ES-module split into
`index.html`/`shell.js`/etc -- add a sentence noting `dom.js` now holds the
shared `mk`/`clear` DOM-construction helpers every panel module imports,
rather than each carrying its own copy. This plan's authors found NO
passage in `docs/MM_TERRARIUM.md` mentioning `DeviceLinkAgent.server` by
that name specifically (the doc discusses `DeviceLinkAgent` extensively but
never spells out `.server` as an attribute name in prose) -- confirm this
is still true with
`grep -n "DeviceLinkAgent" docs/MM_TERRARIUM.md | grep -i server` before
skipping that update; if it returns nothing, no rename-specific doc change
is needed for Task B7, only the `parent_is_gone`/`dom.js` updates above.

- [ ] **Step 7: commit the doc update**
```
git add docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): sync deep-dive with Tier 2 PR B (signals move, dom.js)"
```

- [ ] **Step 8: final full-suite confirmation, both languages**
```
.venv/bin/python -m pytest tests -q -p no:cacheprovider
node --test tests/js/*.test.js
```
Both must report 0 failures before this PR is considered ready to open.

---
