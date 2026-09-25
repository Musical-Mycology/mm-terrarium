# Tier 2 consolidation: shared test doubles and de-duplicated production helpers

**Status:** approved in session 2026-09-25. Second of three tiers from the
codebase optimization audit (Tier 1 was dead-code removal, PR #151; Tier 3
is the docs rewrite).

**Goal:** remove duplicated code without changing runtime behavior. Two
stacked PRs on top of PR #151: **PR A** touches tests only; **PR B** refactors
production code.

## Non-goals (decided, with reasons)

- **Moving test doubles out of production modules** (`control/arco_process.py`
  `FakePopen`, `control/audio.py` `FakeVoice`/`FakePool`, `uplink/transport.py`
  `FakeTransport`). Saves zero lines, and `FakePopen`'s docstring makes the
  double part of that module's contract.
- **A shared argparse helper for `run_stack.py` and `terrarium_boot.py`.**
  Defaults differ on purpose: run_stack's `None` means "let the child
  decide" and its 90 s / 0.060 are launcher policy; boot's `None` means "the
  manifest or BootConfig decides". About 20 lines for a mode switch.
- **One generic helper for all five boot poll loops.** They differ in
  ordering, pacing (pacer vs `sleep(1/20)`), arco handling and exit
  predicates. Only `_wait_for_load` / `_serve_until_done` are merged.
- **De-duplicating `_fireworks_script`** between `control/instrument.py` and
  `bits/metronome/metronome_bit.py`. The copy is documented as deliberate;
  `control/` must not import `bits/`, and making the Bit import from
  `control.instrument` is a policy change not taken here.
- **Unifying unknown-key policy.** `bit_config` and `run_profile` warn;
  `terrarium_config` raises. Each keeps its behavior.

## PR A: test consolidation (about 540 lines)

1. **`tests/fakes.py`** holds configurable doubles, each replacing copies
   defined inside individual tests in `tests/test_terrarium_boot.py` and
   elsewhere:
   - `FakeAgent(closing=0, poll_error=None)`, counting `polls`. Replaces
     the no-op, poll-recording, `closing`, and raising variants (and the
     module-level `_FakeAgent`).
   - `TickingGS(state, end_state, after=3)` and
     `StaticGS(state, tick_error=None)`.
   - `FakeArco(returncode=None, poll_error=None)`, counting polls; replaces
     the module-level `_FakeArco` and its local copies.
   - `RoomlessTerrarium(unload_to_no_room=False)`, recording `unload_calls`.
   - `RecordingClient(calls, prefix, start_error=None)` for the local
     `FakePool`/`FakeTransport` copies that record start/stop/quiesce. It
     gets a distinct name because those locals currently shadow the
     unrelated `control.audio.FakePool` imported by the same file.
   - `FakeClock(start=0.0)` with `.now` and `advance()`, for
     `test_capture_store.py`, `test_capture_bit.py`, `test_link.py`,
     `test_console_agent.py`.
   - `FakeObservable` and `RecordingPopen(label, order)` (a `FakePopen`
     subclass).
   - Data-only doubles (`FakeGs`, `GS`, state-only `FakeTerrarium`) become
     `types.SimpleNamespace(...)`.
2. **Bespoke doubles stay local**, e.g. the GS that flips state on its third
   poll, the closing-fade countdown agent, `FakeRegistration`,
   `_FakeTerrarium`, and `tests/test_run_stack.py`'s child-wrapping popen.
3. **Parametrize or share setup:**
   - Parametrize `_serve_until_done` completes vs restart; swap announced
     vs silent; recycle success vs failure; the two pacer tests; the
     `RecordingArco` pair in `tests/test_terrarium.py`.
   - Delete the exact-duplicate body
     (`test_wait_in_setup_ignores_a_live_parent` duplicates
     `test_wait_in_setup_polls_for_the_requested_window`; keep the one
     whose docstring states the parent-alive case, merging docstrings).
   - Add a `_run_roomless(monkeypatch, wait_results, serve_results, **kw)`
     helper for the five `_serve_roomless` tests.

**Verification:** the full suite passes at every commit. The collected test
IDs are diffed against the baseline, and every change is listed in the PR
(parametrized splits, the removed duplicate). As a mutation check, break
`_serve_until_done`'s exit predicate and `_serve_roomless`'s restart order
temporarily and confirm the consolidated tests fail.

## PR B: production refactors (about 150 to 200 lines)

1. **`console/static/dom.js`** exports `mk` and `clear`. The nine modules
   with byte-identical copies (bit, design, design_forms, join, rail,
   rooms, surface, functions; busy has `mk` only) import them. `index.html`
   is unchanged (it loads only `shell.js`); `console/server.py` serves every
   file in `static/`.
2. **Boot tick loop.** `_wait_for_load` and `_serve_until_done` in
   `harness/terrarium_boot.py` share one loop body with different exit
   predicates. A `_pump(agent, console_agent, uplink)` helper covers the
   per-iteration pumping that the loops repeat. `_serve_rounds` loses its
   unused `drain_arco` parameter. Constraint: helpers resolve
   `parent_is_gone` through `harness.terrarium_boot`'s module globals,
   because tests monkeypatch it there.
3. **`print_bit_list(registry)`** is shared by both scripts' `--list-bits`.
   This fixes run_stack's copy, which omits the `DISABLED` column.
4. **`parent_is_gone`** moves to `harness/signals.py`, and `o2_shroom.py`
   re-exports it so existing imports keep working. `run_stack.py` imports it
   from `signals` and no longer loads `o2_shroom` for it.
5. **Config validation:**
   - `ManifestError` and `TerrariumConfigError` share a base class that
     formats `"source: [key] message"`.
   - A raising `get_typed(...)` helper replaces the mechanical isinstance
     checks in `control/terrarium_config.py`. Domain-specific messages
     (e.g. max_amps "power limiting has no opt-out") stay hand-written.
   - Existing test substrings must keep matching.
   - Correct `run_profile.py`'s docstring: manifest unknown keys warn, they
     are not strict.
6. **`parse_cc_ref(ref, where=None)`** in `control/audio.py` replaces
   `audio._cc_number` (unvalidated) and `role_config._cc_number`
   (validating), and the duplicated `_CC_PREFIX`. `role_config` already
   imports `control.audio`, so this adds no cycle. `audio`'s callers gain
   validation. Callers that previously passed malformed refs are to be
   checked; none are expected, because role_config validates manifests at
   load.
7. **Rename `DeviceLinkAgent.server` to `transport`** (parameter, attribute,
   about 27 sites across `devicelink/agent.py`, `tests/test_devicelink_agent.py`,
   `tests/test_terrarium_boot.py`, `harness/terrarium_boot.py`), including
   the `devicelink_server` return slot. `console/agent.py`'s `self.server`
   is a real ConsoleServer and is not renamed.

**Verification:**
- The full Python suite and the JS suite pass at every commit.
- Mutation checks: flip the merged loop's exit predicate, and make
  `get_typed` accept a wrong type. The tests must fail in both cases.
- Run `./smoke-test.sh` end to end before opening the PR.
- Update `docs/MM_TERRARIUM.md` for the rename, the moved helper and
  `dom.js`.

## Execution

A plan in `docs/superpowers/plans/`, executed via subagent-driven
development with the progress board. PR A on branch
`claude/tier2-test-consolidation` (stacked on #151), then PR B on a branch
stacked on PR A.
