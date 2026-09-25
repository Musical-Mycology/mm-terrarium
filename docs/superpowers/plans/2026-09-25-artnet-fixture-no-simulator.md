# Art-Net-covered fixtures: no simulator, stay unbound — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Room fixture covered by a `[[artnet]]` entry gets no simulator, is never reconnected or armed for a tap, stays unbound, and its Room loads without waiting on it.

**Architecture:** `control/terrarium_config.py` gains a pure `artnet_fixtures(config, room_name)` helper. `control/terrarium.py`'s `load_room` computes that set once and hands it as `skip` to `_bind_room_fast_path` and `wait_for_room_binding`. A covered fixture counts as driven, so the binding timeout fires only for a Room with no covered fixture and nothing bound. The harness and `BootConfig` are untouched.

**Tech Stack:** Python 3 stdlib, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-artnet-fixture-no-simulator-design.md` (parent: `docs/superpowers/specs/2026-09-23-artnet-fixture-sink-design.md`).

## Global Constraints

- `control/` stays pure stdlib: no luxaeterna, no harness import.
- A box with no `[[artnet]]` entries behaves exactly as today: same factory calls, same order, same teardown pushes. Existing tests stay green **unmodified**.
- `BootConfig.array_backend` and `harness/terrarium_boot.py` are unchanged.
- `validate_rooms` gating is behavior-identical.
- Run tests with `.venv/bin/python -m pytest ...` from the worktree root (`.venv` is a symlink to the main checkout's venv).
- Prose you write (docstrings, comments, commit messages, docs) must not use em dashes; use a comma, colon, parentheses, or `--` as the surrounding code does.
- Match surrounding comment density and idiom; docstrings in this repo explain *why*.

---

### Task 1: `artnet_fixtures` helper, reused by `validate_rooms`

**Files:**
- Modify: `control/terrarium_config.py` (add function just above `def validate_rooms`, around line 630; refactor `validate_rooms` body lines 639-651)
- Test: `tests/test_terrarium_config.py` (append after `test_validate_rooms_names_the_uncovered_fixtures`, around line 882; add `artnet_fixtures` to the `from control.terrarium_config import (...)` block at line 9)

**Interfaces:**
- Produces: `artnet_fixtures(config: TerrariumConfig, room_name: str) -> frozenset[str]` in `control.terrarium_config`. Returns the fixture names of `room_name` that appear in `config.artnet_outputs`. Pure; never raises for an unknown room (returns an empty set).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_terrarium_config.py`:

```python
def test_artnet_fixtures_filters_by_room():
    from control.terrarium_config import ArtNetOutput, TerrariumConfig
    cfg = TerrariumConfig(
        schema=1, name="t", bit_paths=(), rooms={}, version="v",
        artnet_outputs=(
            ArtNetOutput(room="DEMO", fixture="array", host="h", max_amps=1.0),
            ArtNetOutput(room="DEMO", fixture="fiber", host="h2", max_amps=1.0),
            ArtNetOutput(room="OTHER", fixture="x", host="h", max_amps=1.0)))
    assert artnet_fixtures(cfg, "DEMO") == frozenset({"array", "fiber"})
    assert artnet_fixtures(cfg, "OTHER") == frozenset({"x"})
    assert artnet_fixtures(cfg, "TEST") == frozenset()
```

Add `artnet_fixtures,` to the existing `from control.terrarium_config import (` block.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py::test_artnet_fixtures_filters_by_room -q`
Expected: FAIL / collection error, `ImportError: cannot import name 'artnet_fixtures'`.

- [ ] **Step 3: Implement**

In `control/terrarium_config.py`, directly above `def validate_rooms`:

```python
def artnet_fixtures(config: TerrariumConfig, room_name: str) -> frozenset[str]:
    """Names of room_name's fixtures that have an [[artnet]] output. Such a
    fixture is driven by its ArtNetFixtureSink and never binds a device:
    control/terrarium.py's load_room neither spawns a simulator for it nor
    waits for a tap on it. Spec 2026-09-25-artnet-fixture-no-simulator."""
    return frozenset(o.fixture for o in config.artnet_outputs
                     if o.room == room_name)
```

Replace the body of `validate_rooms` so it uses the helper (behavior identical):

```python
    out: dict[str, str | None] = {}
    for name, spec in config.rooms.items():
        out[name] = None
        if "array" not in spec.backends or array_backend_configured:
            continue
        covered = artnet_fixtures(config, name)
        missing = [f.name for f in spec.profile.fixtures
                   if f.name not in covered]
        if missing:
            out[name] = (f"{name} requires an array backend, none configured: "
                         f"no simulator, and no [[artnet]] output for "
                         f"fixture(s) {missing}")
    return out
```

- [ ] **Step 4: Run the config tests**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q`
Expected: all pass, including the two existing `test_validate_rooms_*artnet*` tests.

- [ ] **Step 5: Commit**

```bash
git add control/terrarium_config.py tests/test_terrarium_config.py
git commit -m "feat(config): artnet_fixtures names a Room's Art-Net-covered fixtures"
```

---

### Task 2: `load_room` skips covered fixtures in the fast path and the binding wait

**Files:**
- Modify: `control/terrarium.py` (`_bind_room_fast_path` lines 48-72, `wait_for_room_binding` lines 75-110, `load_room` binding block around lines 314-329, import at line 24)
- Modify: `control/teardown.py` (module docstring push-order paragraph, around lines 21-29)
- Test: `tests/test_terrarium.py` (append at end)

**Interfaces:**
- Consumes: `artnet_fixtures(config, room_name) -> frozenset[str]` from Task 1.
- Produces:
  - `_bind_room_fast_path(room, room_binding, simulator_factory, known_device_connected, teardown, *, skip=frozenset()) -> None`
  - `wait_for_room_binding(gs, room_binding, timeout, *, tick, clock=time.monotonic, sleep=time.sleep, skip=frozenset()) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium.py` (the module already defines `TEST_SPEC`, `DEMO_SPEC`, `make_config`, `make_gs`, `make_terrarium`, `BootConfig`, `RoomBindingRegistry`, `TerrariumState`):

```python
from control.rooms import Room
from control.terrarium import RoomBindingTimeout, wait_for_room_binding
from control.terrarium_config import ArtNetOutput


def _artnet(room, fixture):
    return ArtNetOutput(room=room, fixture=fixture, host="127.0.0.1",
                        max_amps=1.0)


def _config_with_artnet(rooms, *outputs):
    return TerrariumConfig(schema=1, name="test-terrarium", bit_paths=(),
                           rooms=rooms, version="1-test",
                           artnet_outputs=tuple(outputs))


class _RecordingBinding(RoomBindingRegistry):
    def __init__(self):
        super().__init__()
        self.armed = []

    def arm(self, room_name, fixture, window_seconds):
        self.armed.append(fixture)
        super().arm(room_name, fixture, window_seconds)


def _recording_factory(calls):
    def factory(teardown, fixture):
        calls.append(fixture)
        return f"sim-{fixture}-dev"
    return factory


def test_an_artnet_covered_fixture_gets_no_simulator_and_stays_unbound():
    calls = []
    binding = _RecordingBinding()
    terrarium = make_terrarium(
        _config_with_artnet({"DEMO": DEMO_SPEC}, _artnet("DEMO", "array")),
        room_binding=binding, simulator_factory=_recording_factory(calls),
        boot_config=BootConfig(room_name="DEMO", bit_name="RoomCapableBit",
                               array_backend="simulator"))
    assert terrarium.load_room("DEMO") is None
    assert terrarium.state == TerrariumState.ROOM_READY
    assert calls == []
    assert binding.armed == []
    assert terrarium.room.bound == {}


def test_an_all_artnet_room_loads_without_the_simulator_flag():
    """The array_backend=None path validate_rooms admits on [[artnet]]
    coverage alone used to time out in wait_for_room_binding."""
    terrarium = make_terrarium(
        _config_with_artnet({"DEMO": DEMO_SPEC}, _artnet("DEMO", "array")),
        boot_config=BootConfig(room_name="DEMO", bit_name="RoomCapableBit"))
    terrarium.simulator_factory = None
    assert terrarium.load_room("DEMO") is None
    assert terrarium.room.bound == {}


def test_a_mixed_room_spawns_a_simulator_only_for_its_uncovered_fixture():
    calls = []
    terrarium = make_terrarium(
        _config_with_artnet({"TEST": TEST_SPEC}, _artnet("TEST", "accent")),
        simulator_factory=_recording_factory(calls))
    assert terrarium.load_room("TEST") is None
    assert calls == ["main"]
    assert terrarium.room.bound == {"main": "sim-main-dev"}


def test_artnet_coverage_for_another_room_changes_nothing():
    calls = []
    terrarium = make_terrarium(
        _config_with_artnet({"TEST": TEST_SPEC, "DEMO": DEMO_SPEC},
                            _artnet("DEMO", "array")),
        simulator_factory=_recording_factory(calls))
    assert terrarium.load_room("TEST") is None
    assert calls == ["main", "accent"]


def test_a_recorded_binding_for_a_covered_fixture_is_not_reconnected():
    binding = RoomBindingRegistry()
    binding.bind("DEMO", "array", "old-dev")
    terrarium = make_terrarium(
        _config_with_artnet({"DEMO": DEMO_SPEC}, _artnet("DEMO", "array")),
        room_binding=binding,
        boot_config=BootConfig(room_name="DEMO", bit_name="RoomCapableBit"))
    terrarium.simulator_factory = None
    terrarium.known_device_connected = lambda dev: True
    assert terrarium.load_room("DEMO") is None
    assert terrarium.room.bound == {}


def _waiting_gs(spec, bound=None):
    gs = make_gs()
    gs.room = Room(name=spec.name, profile=spec.profile, node_id=spec.node_id,
                   bound=dict(bound or {}))
    return gs


class _StepClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_never_arms_a_skipped_fixture():
    binding = _RecordingBinding()
    clock = _StepClock()
    gs = _waiting_gs(TEST_SPEC)
    wait_for_room_binding(gs, binding, 0.2, tick=lambda: None, clock=clock,
                          sleep=clock.sleep, skip=frozenset({"main"}))
    assert binding.armed == ["accent"]


def test_wait_with_a_skipped_fixture_does_not_raise_when_nothing_binds():
    clock = _StepClock()
    gs = _waiting_gs(TEST_SPEC)
    wait_for_room_binding(gs, RoomBindingRegistry(), 0.2, tick=lambda: None,
                          clock=clock, sleep=clock.sleep,
                          skip=frozenset({"accent"}))
    assert gs.room.bound == {}


def test_wait_returns_at_once_when_every_fixture_is_skipped():
    binding = _RecordingBinding()
    gs = _waiting_gs(DEMO_SPEC)
    wait_for_room_binding(gs, binding, 0.2, tick=lambda: None,
                          skip=frozenset({"array"}))
    assert binding.armed == []


def test_wait_without_skip_still_raises_when_nothing_binds():
    clock = _StepClock()
    gs = _waiting_gs(TEST_SPEC)
    with pytest.raises(RoomBindingTimeout):
        wait_for_room_binding(gs, RoomBindingRegistry(), 0.2,
                              tick=lambda: None, clock=clock,
                              sleep=clock.sleep)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium.py -q -k "artnet or covered or wait_ or mixed"`
Expected: the `wait_*skip*` tests FAIL with `TypeError: ... unexpected keyword argument 'skip'`; `test_an_artnet_covered_fixture_gets_no_simulator_and_stays_unbound` FAILS on `calls == []` (it is `["array"]`); `test_an_all_artnet_room_loads_without_the_simulator_flag` FAILS (load returns a "no device joined" reason, after `room_setup_timeout`). `test_wait_without_skip_still_raises_when_nothing_binds` and `test_artnet_coverage_for_another_room_changes_nothing` already PASS (they pin today's behavior).

Note: the all-Art-Net failure waits the real `BootConfig.room_setup_timeout` (30 s). That is expected on this red run only.

- [ ] **Step 3: Implement `_bind_room_fast_path`**

In `control/terrarium.py`, change the signature and loop, and extend the docstring:

```python
def _bind_room_fast_path(room: Room, room_binding: RoomBindingRegistry,
                         simulator_factory, known_device_connected,
                         teardown, *, skip=frozenset()) -> None:
    """...existing docstring text unchanged...

    A fixture in `skip` (one with an [[artnet]] output, see
    control/terrarium_config.py's artnet_fixtures) is passed over entirely:
    no simulator, no reconnect to a recorded binding. Its ArtNetFixtureSink
    drives it and it stays unbound (spec
    2026-09-25-artnet-fixture-no-simulator).
    """
    profile = room.profile
    for fixture in profile.fixtures:
        if fixture.name in skip:
            continue
        if simulator_factory is not None:
            ...unchanged...
```

- [ ] **Step 4: Implement `wait_for_room_binding`**

```python
def wait_for_room_binding(gs: GameServer, room_binding: RoomBindingRegistry,
                          timeout: float, *, tick, clock=time.monotonic,
                          sleep=time.sleep, skip=frozenset()) -> None:
    """...existing docstring text unchanged...

    A fixture in `skip` is driven by an [[artnet]] output and never binds:
    it is never armed and never waited for, and it counts as driven, so
    RoomBindingTimeout is raised only when `skip` is empty AND nothing
    bound (spec 2026-09-25-artnet-fixture-no-simulator, E3).
    """
    profile = gs.room.profile
    needed = [f for f in profile.fixtures if f.name not in skip]
    if all(f.name in gs.room.bound for f in needed):
        return
    deadline = clock() + timeout
    for fixture in needed:
        if fixture.name in gs.room.bound:
            continue
        remaining = deadline - clock()
        if remaining <= 0:
            break
        room_binding.arm(gs.room.name, fixture.name, remaining)
        while clock() < deadline and fixture.name not in gs.room.bound:
            tick()
            sleep(0.05)
        room_binding.disarm(gs.room.name)
    if not gs.room.bound and not skip:
        raise RoomBindingTimeout(
            f"no device joined as {gs.room.name} Room within {timeout}s")
    missing = [f.name for f in needed if f.name not in gs.room.bound]
    if missing:
        logger.warning("Room %s partially bound; missing fixtures: %s",
                       gs.room.name, missing)
```

- [ ] **Step 5: Wire `load_room`**

Change the import at line 24 to:

```python
from control.terrarium_config import (TerrariumConfig, artnet_fixtures,
                                      validate_rooms)
```

Replace the binding block in `load_room` (from `_bind_room_fast_path(` through the `except RoomBindingTimeout` clause) with:

```python
            covered = artnet_fixtures(self.config, spec.name)
            if covered:
                logger.info("Room %s: fixture(s) %s driven by [[artnet]]; "
                            "no simulator, no device bound", name,
                            sorted(covered))
            _bind_room_fast_path(room, self.room_binding,
                                 self._simulator_factory_with_recording(),
                                 self.known_device_connected, stack,
                                 skip=covered)

            if any(f.name not in room.bound for f in room.profile.fixtures
                   if f.name not in covered):
                try:
                    wait_for_room_binding(
                        self.gs, self.room_binding,
                        self.boot_config.room_setup_timeout,
                        tick=self.tick or (lambda: self.gs.tick(0.05)),
                        skip=covered)
                except RoomBindingTimeout as exc:
                    raise RoomLoadError(str(exc)) from exc
```

- [ ] **Step 6: Update the teardown push-order note**

In `control/teardown.py`'s module docstring, change "then pushes one simulator per Room fixture, in the profile's declaration order, as each fixture binds (_bind_room_fast_path)." to "then pushes one simulator per simulated Room fixture (every fixture without an [[artnet]] output), in the profile's declaration order, as each fixture binds (_bind_room_fast_path)." Rewrap to the paragraph's width.

- [ ] **Step 7: Run the Terrarium tests**

Run: `.venv/bin/python -m pytest tests/test_terrarium.py tests/test_terrarium_cycle.py tests/test_terrarium_config.py -q`
Expected: all pass, and quickly (no 30 s wait).

- [ ] **Step 8: Commit**

```bash
git add control/terrarium.py control/teardown.py tests/test_terrarium.py
git commit -m "fix(terrarium): an [[artnet]] fixture gets no simulator and is never waited on"
```

---

### Task 3: Shipped-entry-point proof, deep-dive update, full suite

**Files:**
- Test: `tests/test_terrarium_boot.py` (append at end)
- Modify: `docs/MM_TERRARIUM.md` (artnet entry's two "Bring-up prerequisite" bullets around lines 5156-5181, the test-baseline line after them, and the *Not yet built* "A real-hardware Room backend" bullet around lines 5745-5763)

**Interfaces:**
- Consumes: the behavior from Task 2 through `harness.terrarium_boot.build()`.

- [ ] **Step 1: Write the build()-level test**

`build()` with `room_spec=None` boots to NO_ROOM, so the test can load DEMO through the real `_O2SimulatorFactory` without a Bit. Append to `tests/test_terrarium_boot.py` (reuse the module's existing `build`, `BootConfig`, `RoomBindingRegistry`, `FakePopen`, `_fake_arco`, `_fake_transport`, `_fake_room_audio`, `TestBit`, `time`, `pytest`; check the imports at the top and add any missing):

```python
def test_build_spawns_no_simulator_for_an_artnet_covered_fixture():
    """The shipped entry point always declares array_backend="simulator"
    and always hands the Terrarium an _O2SimulatorFactory. A fixture with
    an [[artnet]] output must still get no simulator process (spec
    2026-09-25-artnet-fixture-no-simulator)."""
    pytest.importorskip("luxaeterna")
    from control.terrarium_config import ArtNetOutput, TerrariumConfig
    import dataclasses
    from tests.test_terrarium import DEMO_SPEC
    # [[artnet]] fixtures are RGBW (validate_artnet_outputs); the shared
    # DEMO_SPEC is GRB, so give the sink a real RGBW fixture.
    rgbw = dataclasses.replace(DEMO_SPEC.profile.fixtures[0],
                               color_order="RGBW")
    demo = dataclasses.replace(DEMO_SPEC, profile=dataclasses.replace(
        DEMO_SPEC.profile, fixtures=(rgbw,)))
    terrarium_config = TerrariumConfig(
        schema=1, name="t", bit_paths=(), rooms={"DEMO": demo},
        version="v", artnet_outputs=(ArtNetOutput(
            room="DEMO", fixture="array", host="127.0.0.1", port=1,
            max_amps=1.0),))
    sim_popen = FakePopen()
    config = BootConfig(room_name=None, bit_name="TestBit",
                        array_backend="simulator")
    gs, server, agent, arco, teardown, terrarium = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), room_spec=None,
        terrarium_config=terrarium_config, transport=_fake_transport(),
        clock=time.monotonic, arco_process_cls=_fake_arco,
        simulator_popen=sim_popen, room_audio=_fake_room_audio())
    try:
        assert terrarium.load_room("DEMO") is None
        assert sim_popen.commands == []
        assert terrarium.room.bound == {}
    finally:
        terrarium.unload_room(force=True)
        teardown.close()
```

If `BootConfig` refuses `room_name=None`, or `build()` needs a different shutdown call, mirror the nearest existing `room_spec=None` test (around line 3325) rather than inventing one.

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py::test_build_spawns_no_simulator_for_an_artnet_covered_fixture -q`
Expected: PASS (Task 2 already landed). To prove it guards the defect, temporarily revert `skip=covered` in `load_room`'s `_bind_room_fast_path` call, confirm the test FAILS with a non-empty `sim_popen.commands`, then restore.

- [ ] **Step 3: Full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass. Record the pass/skip counts for the deep-dive (baseline before this work: 2662 passed, 1 skipped on PR #140, plus whatever PR #141 added).

Also run: `.venv/bin/python -m tools.render_diagrams --check`
Expected: generated diagrams current.

- [ ] **Step 4: Update the deep-dive**

In `docs/MM_TERRARIUM.md`:

1. Replace the bullet starting `- **Bring-up prerequisite: the shipped entry point always ALSO spawns a` (through `...no simulator competing with it.`) with:

```markdown
- ~~**Bring-up prerequisite: the shipped entry point always ALSO spawns a
  WebSim simulator for an `[[artnet]]` fixture.**~~ **Closed 2026-09-25**
  ([`.../2026-09-25-artnet-fixture-no-simulator-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-25-artnet-fixture-no-simulator-design.md)).
  `control/terrarium_config.py`'s `artnet_fixtures(config, room)` names a
  Room's covered fixtures, and `Terrarium.load_room` hands that set as
  `skip` to both `_bind_room_fast_path` (no simulator spawn, no reconnect
  to a recorded binding) and `wait_for_room_binding` (never armed, never
  waited for). A covered fixture stays out of `room.bound`, so DEMO's
  `array` is genuinely unbound under `harness/terrarium_boot.py` even
  though that entry point still declares `array_backend="simulator"`, and
  the spec section 9 step 4 unbound-fixture mute test now hits an unbound
  fixture. A covered fixture counts as driven: `RoomBindingTimeout` fires
  only for a Room with no covered fixture and nothing bound, so an
  all-Art-Net Room loads with no wait. That also fixed a latent defect:
  the `array_backend=None` path `validate_rooms` admits on coverage alone
  used to time out in `wait_for_room_binding` ("no device joined as DEMO
  Room") because nothing could ever bind `array`. Uncovered fixtures, and
  boxes with no `[[artnet]]`, are unchanged.
```

2. In the *Not yet built* "A real-hardware Room backend" bullet, change "Two bring-up prerequisites" to "One bring-up prerequisite remains", and replace the numbered text from `(1) \`harness/terrarium_boot.py\` always spawns a` through `(2) the` with: "(the simulator-alongside-`[[artnet]]` one closed 2026-09-25):", leaving the Console device-picker sentence as the one remaining item. Read the result and fix grammar.

3. After the existing *Test baseline for this slice* paragraph under the artnet entry, add one line: `**Test baseline after the 2026-09-25 no-simulator follow-up:** \`.venv/bin/python -m pytest tests -q\` -> **<N> passed, <M> skipped**.` with the counts from Step 3.

No em dashes in anything you add.

- [ ] **Step 5: Commit**

```bash
git add tests/test_terrarium_boot.py docs/MM_TERRARIUM.md
git commit -m "test(boot),docs(terrarium): prove no simulator for [[artnet]] fixtures; close bring-up prerequisite (1)"
```
