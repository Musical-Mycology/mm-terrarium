# Per-Round Device Respawn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In serve mode, run_stack spawns a fresh set of simulated devices for each console-loaded round, with node/count resolved from the loaded Bit's manifest; plus the shared ARCO_PYTHONPATH fallback for hand-run o2_shroom.

**Architecture:** Control emits a `round loaded: <BitName>` marker each round; run_stack watches its control tee for it (same `on_line` hook BROWSE_URL uses), skips the first, and respawns via the existing `spawn()` path with fresh `ie<k>-r<N>` ids. All harness-layer; engine untouched.

**Tech Stack:** Python 3.11 stdlib; existing ProcTee/TeardownStack machinery.

**Spec:** `docs/superpowers/specs/2026-08-21-per-round-device-respawn-design.md`

## Global Constraints

- Tests only via `.venv/bin/python -m pytest ...` (worktree needs the `.venv` symlink).
- Engine (`control/engine.py`) untouched. `--ci` behavior byte-identical. Marker strings live in `harness/markers.py` and are pinned by `tests/test_markers.py` on both emit and match sides.
- Every hold loop keeps its pty-drain / parent-gone / state-change guards (this plan only ADDS a tee callback; it must not add blocking work to `_hold`'s tick).
- Suite baseline entering this plan: 1272 passed, 1 skipped — no regressions.

---

### Task 1: Shared `ARCO_PYTHONPATH` fallback; `o2_shroom` uses it

**Files:**
- Create: `harness/arco_paths.py`
- Modify: `harness/run_stack.py` (`ARCO_PYTHONPATH`, `_import_o2litepy`, `_ensure_o2litepy` move; run_stack imports from the new module), `harness/o2_shroom.py` (`main()` calls `ensure_o2litepy()` before `from o2litepy import o2lite`, with the same clear error if it still fails)
- Test: `tests/test_arco_paths.py` (move/adapt run_stack's existing `_ensure_o2litepy` tests — find them: `grep -n ensure_o2litepy tests/test_run_stack.py`), plus a test that `o2_shroom.main`'s import path consults the helper (patch `ensure_o2litepy` and assert it ran before the import; follow `tests/test_o2_shroom.py`'s existing CLI-test style).

**Interfaces:**
- Produces: `harness/arco_paths.py` with `ARCO_PYTHONPATH: str`, `ensure_o2litepy(*, importer=..., syspath=sys.path, environ=os.environ) -> bool` — byte-identical logic to run_stack's current `_ensure_o2litepy` (appends to `sys.path` AND child-visible `PYTHONPATH`; explicit PYTHONPATH wins). `harness/run_stack.py` re-exports nothing; its callers switch to the new module. Backward-compat alias `run_stack.ARCO_PYTHONPATH` may remain if tests reference it (check first).

- [ ] **Step 1: Write failing tests** (moved helper importable from `harness.arco_paths`; o2_shroom consults it).
- [ ] **Step 2: Verify red.** — [ ] **Step 3: Implement (pure move + one call site).** — [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `git commit -m "refactor(harness): shared ARCO_PYTHONPATH fallback; hand-run o2_shroom finds o2litepy"`

---

### Task 2: `CONTROL_ROUND_LOADED` marker emitted per round

**Files:**
- Modify: `harness/markers.py` (`CONTROL_ROUND_LOADED = "round loaded:"`, added to the control marker dict), `harness/terrarium_boot.py` (`_serve_rounds` prints `f"{markers.CONTROL_ROUND_LOADED} {gs.bit_name}"` immediately after `_wait_for_load` returns `"loaded"`; serve-mode `main()` prints it once for the initial CLI-selected bit before entering the round machinery so EVERY round announces itself)
- Test: `tests/test_markers.py` (pin the emit site per its existing pattern), `tests/test_terrarium_boot.py` (fake-gs serve-rounds test asserts the line is printed with the loaded bit's name each round — capsys, in the file's existing style)

**Interfaces:**
- Produces: every serve-mode round emits exactly one `round loaded: <BitName>` line on Control's stdout, including round 1. One-shot (non-serve) mode emits nothing new.

- [ ] **Step 1: Failing tests.** — [ ] **Step 2: Red.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `git commit -m "feat(harness): serve rounds announce 'round loaded: <Bit>' as a pinned marker"`

---

### Task 3: run_stack respawns devices on the marker

**Files:**
- Modify: `harness/run_stack.py`
- Test: `tests/test_run_stack.py`

**Interfaces (implement exactly):**
- `run()` builds a respawn hook when `cfg.serve`: the control child's `ProcTee` gets an `on_line` callback (compose with the existing `collect_url` — a small fan-out wrapper calling both; do not replace URL collection) that, on a line containing `markers.CONTROL_ROUND_LOADED`, parses the bit name (text after the marker, stripped) and appends it to a thread-safe `queue.Queue` (`_round_loads`). The tee thread only enqueues — no spawning from the reader thread.
- `_hold` gains an optional `on_round=None` callable, invoked once per drained queue entry each tick (both the `seconds is None` and deadline loops). Passing `None` keeps current behavior; non-serve callers pass nothing.
- The `on_round(bit_name)` closure (built in `run()`, capturing cfg/popen/teardown/tees/log-dir/round counter and the `BitRegistry`):
  - Skips the FIRST dequeued name (round 1's devices were spawned by the launch path); a `first_seen` flag, tested.
  - Resolves `node = cfg.node if cfg.node_explicit else registry-resolved join node for bit_name` and `count = cfg.devices if devices explicitly given else that bit's launch.default_devices`. To know "explicitly given", carry two new booleans on `StackConfig` (`node_explicit: bool = False`, `devices_explicit: bool = False`) set in `config_from_args` from `args.node is not None` / `args.devices is not None`.
  - Unknown bit name or no resolvable node → one `print(..., file=sys.stderr)` warning, spawn nothing (tested).
  - Spawns `count` children named `ie<k>-r<N>` (N = round number starting at 2) via the same `spawn()` helper with `device_command(...)` — extend `device_command(cfg, index, ppid, *, dev=None, node=None)` so the respawn can pass the per-round dev id and node without mutating cfg. No readiness gating for respawns.
- `_dead_child`'s tolerate-clean-devices rule already covers respawned children (they are non-"control" keys) — add one test proving a respawned child's clean exit is tolerated.

- [ ] **Step 1: Failing tests** — (a) marker line on the control tee enqueues and `_hold` invokes `on_round`; (b) first occurrence skipped, second spawns; (c) manifest resolution: MetronomeBit → METRO_PLAYER_NODE, 2 children named `ie1-r2`,`ie2-r2` (patch `BitRegistry.discover` as existing tests do); (d) explicit `--node`/`--devices` override; (e) unknown bit warns, no spawn; (f) URL collection still works alongside the new callback (a BROWSE_URL line still lands in `urls`).
- [ ] **Step 2: Red.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `git commit -m "feat(harness): serve mode respawns per-round devices from the loaded bit's manifest"`

---

### Task 4: Live verify + docs

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (rounds section: respawn behavior replaces the device-less-round-2 trap note; o2_shroom PYTHONPATH note), spec Status.

- [ ] **Step 1: Live verify (this box):** serve stack `--console-port 0 --devices 1 --room-type DEMO`; over the console `/ws`: abort round 1 → `load_bit MetronomeBit` → assert (from run_stack output/logs) two fresh `ie*-r2` children spawn, join METRO_PLAYER_NODE (`join granted` lines), round runs; then `load_bit TestBit` → one `ie1-r3` child joins TEST_PLAYER_NODE. SIGINT: clean teardown, zero orphans. Also: hand-run `PYTHONPATH= .venv/bin/python -m harness.o2_shroom --dev probe --node TEST_PLAYER_NODE --exit-with-parent <fake>` against no stack — must get PAST the o2litepy import (fails later on discovery, fine) with no ModuleNotFoundError.
- [ ] **Step 2: Docs + spec Status (over-claiming discipline; record what was and wasn't verified).**
- [ ] **Step 3: Full suite; commit** — `git commit -m "docs(terrarium): per-round device respawn and shared o2litepy fallback"`

---

## Self-review notes

- Spec §1 bullets map: fallback → T1; marker → T2; respawn/ids/overrides/best-effort → T3; verification → T4. Non-goals appear in no task.
- Names used consistently: `CONTROL_ROUND_LOADED`, `ensure_o2litepy`, `on_round`, `ie<k>-r<N>`, `node_explicit`/`devices_explicit`.
- `_hold` changes are additive (optional param), preserving PR #48's tolerance tests unchanged.
