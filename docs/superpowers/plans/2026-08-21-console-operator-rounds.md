# Console Operator Rounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load→abort→load rounds from the Terrarium Console: the harness stays up across Bit lifecycles, every discovered Bit is loadable, and the Console gets one merged top control bar.

**Architecture:** A `--serve` mode wraps the existing hold/serve helpers in a round loop (harness-only; engine untouched); the engine receives a lazy Mapping over all discovered packages; the Console page merges the legacy load/run/abort controls into the Bits panel at the top.

**Tech Stack:** Python 3.11 stdlib; existing Console vanilla JS (no build step).

**Spec:** `docs/superpowers/specs/2026-08-21-console-operator-rounds-design.md`

## Global Constraints

- Tests only via `.venv/bin/python -m pytest ...` (worktree has the `.venv` symlink).
- `control/engine.py` must not change. `control/` stays stdlib-only at module level; discovery still never imports Bit code.
- `_wait_in_setup` / `_serve_until_done` invariants survive: pty drain every iteration, parent-gone check, state-change yield; marker strings in `harness/markers.py` unchanged.
- `--ci` behavior byte-identical (one-shot, exit-on-complete). Existing one-shot behavior without `--serve`/console unchanged.
- Console: no per-event panel rebuilds; no new script file unless IIFE-isolated; no build step; JS behavior tests via Node `vm` + Python wrapper.
- Suite baseline entering this plan: 1254 passed, 1 skipped — must not regress.

---

### Task 1: `BitRegistry.lazy_class_map()` and full-registry wiring

**Files:**
- Modify: `control/bit_registry.py`, `harness/terrarium_boot.py:739` (the `{bit: registry.bit_class(bit)}` literal)
- Test: `tests/test_bit_registry.py`, `tests/test_terrarium_boot.py`

**Interfaces:**
- Produces: `BitRegistry.lazy_class_map() -> Mapping[str, type]` — a `collections.abc.Mapping` whose `__getitem__(name)` calls `self.bit_class(name)` (import happens on access only), `__len__`/`__iter__` over `self.packages`. `KeyError` for unknown names passes through (the engine already wraps it in `BitLoadError`); a failing import raises the existing `ManifestError`, which the engine's `load_bit` try-block also wraps as `BitLoadError`.
- `terrarium_boot.main()` passes `registry.lazy_class_map()` to `build(...)` instead of the single-entry dict.

- [ ] **Step 1: Failing tests**

```python
# tests/test_bit_registry.py additions
def test_lazy_class_map_imports_only_on_access(tmp_path):
    make_pkg(tmp_path, "good", GOOD, MODULE)
    make_pkg(tmp_path, "boom", GOOD.replace("GoodBit", "BoomBit"),
             "raise RuntimeError('imported')\n")
    reg = BitRegistry.discover(tmp_path)
    m = reg.lazy_class_map()
    assert set(m) == {"GoodBit", "BoomBit"}      # no import yet
    assert m["GoodBit"].__name__ == "GoodBit"
    with pytest.raises(Exception):               # ManifestError on access
        m["BoomBit"]

def test_lazy_class_map_unknown_name_is_keyerror(tmp_path):
    make_pkg(tmp_path, "good", GOOD, MODULE)
    with pytest.raises(KeyError):
        BitRegistry.discover(tmp_path).lazy_class_map()["Nope"]
```

Plus, in `tests/test_terrarium_boot.py`, a test that the registry object handed to `build()` contains every discovered name (patch `build` or inspect via the existing CLI-test seam the file already uses), and an engine-level test that `GameServer(lazy_map)` can `load_bit` a real packaged Bit ("TestBit") straight through the map.

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_bit_registry.py -v` → FAIL (`lazy_class_map` missing)
- [ ] **Step 3: Implement** — small `_LazyClassMap(Mapping)` class in `control/bit_registry.py`; one-line change at `terrarium_boot.py:739`.
- [ ] **Step 4: Full suite green** — `.venv/bin/python -m pytest tests -q`
- [ ] **Step 5: Commit** — `git commit -m "feat(bits): lazy full-registry class map; every discovered Bit loadable from the Console"`

---

### Task 2: `--serve` round loop in `terrarium_boot`

**Files:**
- Modify: `harness/terrarium_boot.py` (new `_wait_for_load`, `_serve_rounds`; `main()` branches into serve mode; `--serve` flag)
- Test: `tests/test_terrarium_boot.py`

**Interfaces:**
- `--serve` argparse flag (default False). Effective serve = `args.serve or (args.console_port is not None and args.seconds is None and not args.hold)`. (`--hold`/`--seconds` are bounded/one-shot intents; a console with neither implies rounds.) `run_stack --ci` never passes `--serve` (Task 3) and `terrarium_boot` itself has no `--ci`.
- `_wait_for_load(gs, agent, arco, *, clock, sleep, parent_pid, console_agent) -> str` — ticks agent/console/gs exactly like `_serve_until_done`'s loop body while `gs.state is State.IDLE`; returns `"loaded"` the moment state leaves IDLE (a console `load_bit` ran), `"parent-gone"` / `"arco-exited"` on those conditions. If state is already not IDLE on entry, returns `"loaded"` immediately (first CLI-selected round).
- `_serve_rounds(gs, agent, arco, *, parent_pid, console_agent, drain_arco) -> str` — the loop:
  1. `reason = _wait_for_load(...)`; non-`"loaded"` → return it.
  2. Read the round's config: `cfg = getattr(gs.bit, "config", None)`; `cond = cfg.start if cfg else None`; `setup = cfg.launch.setup_seconds if cfg else 0.0`.
  3. `reason = _wait_in_setup(..., condition=cond, setup_seconds=setup, game_server=gs)` (the existing helper, with its pty-drain/parent-gone/state-change behavior).
  4. `"parent-gone"` → return; `"timeout-abort"` → `gs.abort()` and `continue`; else `gs.run()` only if state is still SETUP (same operator-handoff guard `main()` has today).
  5. `reason = _serve_until_done(...)`; `"parent-gone"`/`"arco-exited"` → return; `"completed"` (includes aborts — both land in IDLE) → print `"round complete; waiting for next load"` and `continue`.
- `main()` serve branch: after the existing boot + initial `load_bit`, call `_serve_rounds` instead of the one-shot hold/run/serve sequence; teardown path unchanged (`finally: shutdown(teardown)`); KeyboardInterrupt exits the loop cleanly.

- [ ] **Step 1: Failing tests** — in the file's existing fake-gs style:

```python
def test_serve_rounds_cycles_idle_load_run_idle():
    # fake gs scripted: IDLE -> (console load) SETUP -> RUNNING -> IDLE
    #                   -> (console load) SETUP -> operator abort -> IDLE -> parent-gone
    # assert _serve_rounds returns "parent-gone" and gs.run_calls == 1
    # (second round aborted during hold, so run() never fired for it)

def test_serve_rounds_honors_players_condition_per_round():
    # round 2's fake bit config has start=players/min_scored=1; scripted scored
    # count crosses threshold; assert the round started via "players-met"

def test_wait_for_load_returns_loaded_immediately_when_not_idle():
    ...

def test_console_port_implies_serve_and_seconds_suppresses_it():
    # parse_args-level: --console-port 0 -> serve; --console-port 0 --seconds 5 -> not serve
```

- [ ] **Step 2: Verify failure.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `git commit -m "feat(harness): --serve round loop -- load/abort/load from the Console without teardown"`

---

### Task 3: `run_stack` serve forwarding

**Files:**
- Modify: `harness/run_stack.py`
- Test: `tests/test_run_stack.py`

**Interfaces:**
- `--serve` flag on run_stack; effective serve = `args.serve or (console requested and not args.ci)`; forwarded as `--serve` on the `terrarium_boot` child command. `--ci --serve` refused at argument parsing (mirrors `--ci --open`).
- In serve mode `run_stack`'s hold has no natural end: `_hold` (or its equivalent) runs until Ctrl-C/child-exit — verify a child that exits still fails loud exactly as today.

- [ ] **Step 1: Failing tests** — command-construction test asserting `--serve` present when console requested without `--ci`, absent under `--ci`; `--ci --serve` exits non-zero at parse.
- [ ] **Step 2-4: red / implement / full suite green.**
- [ ] **Step 5: Commit** — `git commit -m "feat(harness): run_stack forwards --serve for console-driven rounds"`

---

### Task 4: Merged top control bar in the Console

**Files:**
- Modify: `console/static/index.html`, `console/static/console.js`, `console/static/style.css`
- Test: `tests/js/bits_panel_behavior.test.js` (+ new assertions), `tests/js/console_full_stack.test.js` expectations if selectors move

**Interfaces:**
- The legacy crude load interface (the pre-existing load/run/abort controls block in `index.html` — locate it; it predates this branch) merges INTO the Bits section, which moves to the TOP of the page: left side shows current bit name + engine state + Run and Abort buttons (reusing the existing `run`/`abort` command sends); right/below are the discovered-bit cards with Load buttons (existing `renderBits`). Delete the duplicated legacy block — exactly one set of load/run/abort controls remains on the page.
- Render discipline unchanged: bit cards re-render only on `bits_listed` (signature-gated); the current-bit/state header updates on `state_changed` WITHOUT rebuilding the card list (extend the existing state handling; assert via child-identity test).
- Panel order after the bar: Room, Triggers, Registration, Devices, Event log (whatever the current order is below — only the Bits/controls move to the top).

- [ ] **Step 1: Failing JS tests** — (a) the page has exactly one Load-controls region (query for the legacy block's selector: absent); (b) Bits section is the first panel in document order; (c) `state_changed` updates the header text but preserves bit-card node identity; (d) Run/Abort buttons send `{"command":"run"}` / `{"command":"abort"}`.
- [ ] **Step 2-4: red / implement / full suite green (JS wrappers included).**
- [ ] **Step 5: Commit** — `git commit -m "feat(console): merged top control bar -- one Bits section owns load/run/abort"`

---

### Task 5: Live verification + docs

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (serve mode + merged bar; adjust the one-shot-driver description), spec Status lines (this spec and, if touched, the packaging spec).

- [ ] **Step 1: Live verify (this box):** `.venv/bin/python -m harness.run_stack --console-port 0 --open` → from the panel: load MetronomeBit → Abort → load TestBit → Run → complete → load MetronomeBit again; stack stays up throughout; Ctrl-C tears down clean (zero orphans via `pgrep -f o2_shroom\|terrarium_boot\|room_simulator` after exit). Confirm every discovered Bit's Load works (lazy map). Record run dir + evidence.
- [ ] **Step 2: Docs + spec Status updated (voice per the deep-dive; over-claiming discipline).**
- [ ] **Step 3: Full suite green; commit** — `git commit -m "docs(terrarium): serve-mode rounds and merged Console control bar"`

---

## Self-review notes

- Spec §2 serve mode → Task 2; lazy registry → Task 1; top bar → Task 4; run_stack → Task 3; verification §4 → Task 5. Non-goals (device reconnection, CI rounds) appear in no task.
- Engine untouched in every task. `_wait_in_setup`/`_serve_until_done` reused, not forked.
- Type consistency: `_wait_for_load`/`_serve_rounds` signatures used identically in Tasks 2 and 5's verification; `lazy_class_map()` name identical in Tasks 1-2.
