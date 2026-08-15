# Room Simulator WebSim Label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `harness/room_simulator.py` and `harness/o2_shroom.py` pass their own `dev` id as the new `WebSimBackend(label=...)` parameter, so a Room simulator's browser canvas is visually distinguishable from a player device's.

**Architecture:** Both files' `build()` functions already receive `dev` as their first parameter and already construct a `WebSimBackend`. Each gets exactly one new keyword argument (`label=dev`) at its existing `WebSimBackend(...)` call site — no signature changes, no new CLI flags, no `--no-join` branching (the Room simulator's `dev` is always `"sim-room"`, distinct from any real player device's id, so passing `dev` through unconditionally is already sufficient).

**Tech Stack:** Python 3.10+, pytest. Depends on luxaeterna's `WebSimBackend(label=...)` parameter (see the companion plan `docs/superpowers/plans/2026-08-14-websim-backend-label.md` in the luxaeterna repo) — **must land in luxaeterna first**.

## Global Constraints

- Both call sites pass `label=dev`, unconditionally — no special-casing Room vs. player.
- `harness/led_smoke.py` is explicitly out of scope (see spec §3) — do not touch it.
- No change to either file's `build()` signature — `dev` is already a parameter.
- No change to the `print()` watch-URL lines in either file.
- **Sequencing dependency:** this repo's editable luxaeterna install must expose `label` before these tests can pass. This worktree's `.venv` is already `pip install -e`'d against the luxaeterna worktree at `/Users/chris/projects/luxaeterna/.claude/worktrees/websim-backend-label` (which implements `label`), so local development and testing here works today. Once luxaeterna's PR actually merges to its `main`, re-point this venv back at the sibling checkout root for any future work: `.venv/bin/python -m pip install -e "/Users/chris/projects/luxaeterna[websim]"` (matches `requirements-dev.txt`'s documented command — no file changes needed, this is a local venv operation). Do not open/merge this repo's PR before luxaeterna's has merged.
- Run tests with `.venv/bin/python -m pytest <path> -v` from the repo root. Baseline: `.venv/bin/python -m pytest tests -q` currently passes 662/662 (1 skipped, 1 unrelated thread-teardown warning on Python 3.14 — pre-existing, not caused by this work).

---

### Task 1: Label `harness/room_simulator.py`'s canvas

**Files:**
- Modify: `harness/room_simulator.py:55-56`
- Test: `tests/test_room_simulator.py`

**Interfaces:**
- Consumes: `WebSimBackend(capability=..., host=..., port=..., serve=..., label: str | None = None)` from luxaeterna (already landed on the editable install per Global Constraints). `WebSimBackend.label: str` — public attribute holding what was passed.
- Produces: no new symbols — `build()`'s returned `backend.label` now equals the `dev` it was called with.

- [ ] **Step 1: Write the failing test**

In `tests/test_room_simulator.py`, extend `test_build_wires_the_client_and_backend` (do not add a new function — this is the same build() call already under test, just one more assertion on its existing return value):

```python
def test_build_wires_the_client_and_backend():
    pytest.importorskip("luxaeterna.backends.websim")

    client, backend = build("sim-room", serve=False)

    assert client.dev == "sim-room"
    assert client.leds is not None
    assert backend.is_open is False  # build() doesn't open() -- main() does
    assert backend.label == "sim-room"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_room_simulator.py -v`
Expected: `test_build_wires_the_client_and_backend` FAILS with
`AttributeError: 'WebSimBackend' object has no attribute 'label'` (the installed
luxaeterna already supports `label` per Global Constraints, but `room_simulator.py`
doesn't pass it yet, so the backend was constructed with `label=None` and
`backend.label` is `None`, not `"sim-room"` — the assertion fails, not the
attribute lookup, unless the editable install in this venv predates the `label`
attribute entirely, in which case it's the `AttributeError` above. Either failure
mode confirms the current code doesn't pass `label`.)

- [ ] **Step 3: Implement**

In `harness/room_simulator.py`, change the `WebSimBackend(...)` call inside `build()`
(currently lines 55-56):

```python
    backend = WebSimBackend(capability=shroom_capability(),
                             host=sim_host, port=sim_port, serve=serve)
```

to:

```python
    backend = WebSimBackend(capability=shroom_capability(),
                             host=sim_host, port=sim_port, serve=serve,
                             label=dev)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_room_simulator.py -v`
Expected: all tests in the file PASS, including the extended
`test_build_wires_the_client_and_backend`.

- [ ] **Step 5: Commit**

```bash
git add harness/room_simulator.py tests/test_room_simulator.py
git commit -m "feat(harness): label the Room simulator's WebSim canvas with its dev id"
```

---

### Task 2: Label `harness/o2_shroom.py`'s canvas

**Files:**
- Modify: `harness/o2_shroom.py:139-140`
- Test: `tests/test_o2_shroom.py`

**Interfaces:**
- Consumes: same `WebSimBackend(label=...)` / `.label` as Task 1.
- Produces: no new symbols — `build()`'s returned `backend.label` now equals the `dev`
  it was called with, for both the Room-simulator invocation (`dev="sim-room"`,
  `--no-join`, from `terrarium_boot.py`'s `_O2SimulatorFactory`) and a normal player
  device invocation (e.g. `dev="ie1"`).

- [ ] **Step 1: Write the failing test**

In `tests/test_o2_shroom.py`, extend `test_build_wires_the_client_and_backend` with
one more assertion on the same existing `build()` call:

```python
def test_build_wires_the_client_and_backend():
    """Mirrors tests/test_room_simulator.py's test_build_wires_the_client_
    and_backend for the same socket-free build() seam: dev id and node
    reach the client, an LED adapter is wired, and serve=False means no
    socket was opened."""
    pytest.importorskip("luxaeterna.backends.websim")

    client, backend = build("ie1", "TEST_PLAYER_NODE", serve=False)

    assert client.dev == "ie1"
    assert client.node == "TEST_PLAYER_NODE"
    assert client.leds is not None
    assert backend.is_open is False
    assert backend.label == "ie1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py -v`
Expected: `test_build_wires_the_client_and_backend` FAILS on the new
`backend.label == "ie1"` assertion (`backend.label` is `None`, since `o2_shroom.py`
doesn't pass `label` yet).

- [ ] **Step 3: Implement**

In `harness/o2_shroom.py`, change the `WebSimBackend(...)` call inside `build()`
(currently lines 139-140):

```python
    backend = WebSimBackend(capability=shroom_capability(),
                            host=sim_host, port=sim_port, serve=serve)
```

to:

```python
    backend = WebSimBackend(capability=shroom_capability(),
                            host=sim_host, port=sim_port, serve=serve,
                            label=dev)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py -v`
Expected: all tests in the file PASS, including the extended
`test_build_wires_the_client_and_backend`.

Then run the full suite to confirm nothing else broke:

Run: `.venv/bin/python -m pytest tests -q`
Expected: `662 passed, 1 skipped` (same as the documented baseline — this change adds
assertions to two existing tests rather than new test functions, so the total count
is unchanged).

- [ ] **Step 5: Commit**

```bash
git add harness/o2_shroom.py tests/test_o2_shroom.py
git commit -m "feat(harness): label o2_shroom's WebSim canvas with its dev id"
```
