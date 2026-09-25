# VENUE Room Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `VENUE` Room (LED bars + three fiber-optic engines as two RGBW fixtures, each on its own WLED controller over `[[artnet]]`), make an all-Art-Net Room load with no bound device, and validate shared-PSU power budgets at config load.

**Architecture:** VENUE is data (`rooms/VENUE.toml`, `instruments/venue_fiber.toml`) on top of the per-fixture sessions and `ArtNetFixtureSink` that PR #140 shipped. Two code changes carry it: `control/terrarium.py` stops spawning simulators for, and waiting on, fixtures that have an `[[artnet]]` output; `control/terrarium_config.py` learns `[psus.<name>]` and an optional `psu` key on `[[artnet]]`, and refuses a PSU whose outputs' `max_amps` sum past 80 % of its rating. Bits opt in by name.

**Tech Stack:** Python 3 (stdlib `tomllib`, dataclasses), pytest, luxaeterna (already a dev dependency).

**Spec:** `docs/superpowers/specs/2026-09-25-venue-room-design.md`. Read it with this plan. Its §2 lists hardware inputs that are still open. This plan does not wait for them (see Global Constraints).

## Global Constraints

- Suite: `.venv/bin/python -m pytest tests -q`. It must stay fully offline (no network, no Arco, no pyarco import).
- **N = 1** LED per fiber engine (spec §2 rule). Every fiber count in code and tests goes through one name, `N`, so closing spec input I1 is a single edit per file.
- Never commit a real controller IP. Use `<WLED controller IP>` in committed examples, and `127.0.0.1` in tests.
- Never commit a real `max_amps` for VENUE. The committed example uses the placeholders `<BARS_MAX_AMPS>`, `<FIBER_MAX_AMPS>`, `<FIBER_AMPS_PER_PIXEL>` and `<PSU_RATED_AMPS>` (spec §7, input I3).
- Art-Net fixtures are `color_order = "RGBW"`. A profile may not mix color orders.
- Every block is at most 170 px.
- Boundary rule 2 is unchanged: nothing in this plan touches `send_frame` or the tick.
- Written prose (docs, comments, commit messages) uses no em dashes.
- Base: `main` at `fbc5c2f`. Branch: `claude/venue-room-spec` (this plan and the spec already live there). Before opening a PR, re-check `origin/main`'s tip for a concurrent fix of the same thing.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `control/terrarium.py` | Modify | `_bind_room_fast_path` and `wait_for_room_binding` skip Art-Net-covered fixtures, and `load_room` computes the covered set |
| `control/terrarium_config.py` | Modify | `ArtNetOutput.psu`, `TerrariumConfig.psus`, `_parse_psus`, `validate_psu_budgets` |
| `instruments/venue_fiber.toml` | Create | Light-only fiber instrument |
| `rooms/VENUE.toml` | Create | The two-fixture Room |
| `bits/test/test_bit.py`, `bits/test/bit.toml` | Modify | `VENUE` in `room_types` |
| `bits/metronome/metronome_bit.py`, `bits/metronome/bit.toml` | Modify | `VENUE` in `room_types` |
| `terrarium.toml` | Modify | Commented VENUE `[[artnet]]` + `[psus]` example between markers |
| `tests/test_venue_room.py` | Create | Everything VENUE-specific: instrument, profile, Bits, addressing, outputs, the committed example |
| `tests/test_terrarium.py` | Modify | Covered-fixture binding tests |
| `tests/test_terrarium_config.py` | Modify | PSU tests, and the two shipped-room goldens gain `VENUE` |
| `tests/test_test_bit.py`, `tests/test_metronome_bit_declarations.py` | Modify | `room_types` assertions |
| `docs/MM_TERRARIUM.md` | Modify | New VENUE entry, double-bind prerequisite closed |

---

### Task 1: An Art-Net-covered fixture needs no binding

Fixes spec §9.1: today an all-Art-Net Room with no simulator factory times out in `wait_for_room_binding`, and under the harness every Art-Net fixture also binds a WebSim simulator.

**Files:**
- Modify: `control/terrarium.py:48-111` (`_bind_room_fast_path`, `wait_for_room_binding`) and `control/terrarium.py:311-328` (inside `load_room`)
- Test: `tests/test_terrarium.py`

**Interfaces:**
- Consumes: `TerrariumConfig.artnet_outputs: tuple[ArtNetOutput, ...]` (each has `.room`, `.fixture`), existing.
- Produces:
  - `_bind_room_fast_path(room, room_binding, simulator_factory, known_device_connected, teardown, covered=frozenset())`
  - `wait_for_room_binding(gs, room_binding, timeout, *, tick, clock=time.monotonic, sleep=time.sleep, covered=frozenset())`
  - `covered` is a `frozenset[str]` of fixture names in the Room being loaded.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium.py`. Add `ArtNetOutput` to the existing `from control.terrarium_config import ...` line so it reads `from control.terrarium_config import ArtNetOutput, RoomSpec, TerrariumConfig`.

```python
VENUE_PROFILE = RoomProfile(surface_id="room_venue", fixtures=(
    RoomFixture(name="bars", color_order="RGBW",
               blocks=(RoomBlock("m1", 0, 10),),
               zones=(RoomZone("all", 0, 10),), instrument=GENERIC_SURFACE),
    RoomFixture(name="fiber", color_order="RGBW",
               blocks=(RoomBlock("e1", 0, 3),),
               zones=(RoomZone("all", 0, 3),), instrument=GENERIC_SURFACE),
))
VENUE_SPEC = RoomSpec(name="VENUE", description="",
                      backends=("devicelink", "array"),
                      node_id="ROOM_VENUE_NODE", profile=VENUE_PROFILE)


def _venue_config(*covered):
    """A config holding only VENUE, with an [[artnet]] output for each
    fixture name in `covered`."""
    return TerrariumConfig(
        schema=1, name="test-terrarium", bit_paths=(),
        rooms={"VENUE": VENUE_SPEC}, version="1-test",
        artnet_outputs=tuple(
            ArtNetOutput(room="VENUE", fixture=f, host="127.0.0.1", max_amps=1.0)
            for f in covered))


def _spy_factory(spawned):
    def factory(teardown, fixture):
        spawned.append(fixture)
        return f"sim-{fixture}-dev"
    return factory


def test_an_all_artnet_room_loads_with_no_simulator_and_no_device():
    """Spec 2026-09-25 section 9.1: with no simulator factory, nothing can
    bind, and before the fix the load waited room_setup_timeout and then
    failed with RoomBindingTimeout."""
    terrarium = make_terrarium(
        config=_venue_config("bars", "fiber"),
        boot_config=BootConfig(room_name="VENUE", bit_name="RoomCapableBit",
                               room_setup_timeout=0.2))
    terrarium.simulator_factory = None
    assert terrarium.load_room("VENUE") is None
    assert terrarium.state == TerrariumState.ROOM_READY
    assert terrarium.room.bound == {}


def test_an_artnet_covered_fixture_never_spawns_a_simulator():
    spawned = []
    terrarium = make_terrarium(
        config=_venue_config("bars", "fiber"),
        simulator_factory=_spy_factory(spawned),
        boot_config=BootConfig(room_name="VENUE", bit_name="RoomCapableBit",
                               array_backend="simulator",
                               room_setup_timeout=0.2))
    assert terrarium.load_room("VENUE") is None
    assert spawned == []
    assert terrarium.room.bound == {}


def test_an_uncovered_fixture_still_binds_beside_a_covered_one():
    spawned = []
    terrarium = make_terrarium(
        config=_venue_config("bars"),
        simulator_factory=_spy_factory(spawned),
        boot_config=BootConfig(room_name="VENUE", bit_name="RoomCapableBit",
                               array_backend="simulator",
                               room_setup_timeout=0.2))
    assert terrarium.load_room("VENUE") is None
    assert spawned == ["fiber"]
    assert terrarium.room.bound == {"fiber": "sim-fiber-dev"}


def test_an_uncovered_fixture_with_nothing_to_bind_still_times_out():
    """Coverage of one fixture must not mask a Room where nothing at all
    drives the other: with no factory and no output on either fixture the
    load still fails, exactly as before."""
    terrarium = make_terrarium(
        config=_venue_config(),
        boot_config=BootConfig(room_name="VENUE", bit_name="RoomCapableBit",
                               array_backend="simulator",
                               room_setup_timeout=0.2))
    terrarium.simulator_factory = None
    reason = terrarium.load_room("VENUE")
    assert reason is not None and "no device joined" in reason
    assert terrarium.state == TerrariumState.NO_ROOM
```

- [ ] **Step 2: Run the tests to verify the first two fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium.py -q -k "artnet or covered or nothing_to_bind"`
Expected: `test_an_all_artnet_room_loads_with_no_simulator_and_no_device` FAILS (`load_room` returns a "no device joined as VENUE Room within 0.2s" reason). `test_an_artnet_covered_fixture_never_spawns_a_simulator` FAILS (`spawned == ["bars", "fiber"]`). The other two pass already; they are guards.

- [ ] **Step 3: Implement**

In `control/terrarium.py`, replace `_bind_room_fast_path` (lines 48-72) with:

```python
def _bind_room_fast_path(room: Room, room_binding: RoomBindingRegistry,
                         simulator_factory, known_device_connected,
                         teardown, covered=frozenset()) -> None:
    """Attempt the no-tap-needed path per fixture: a Terrarium-spawned
    simulator, or a reconnect to a previously recorded physical device.
    Leaves any fixture unbound (absent from room.bound) if neither applies
    -- wait_for_room_binding below is what holds for a fresh admin-armed
    tap, not this function's job.

    A fixture in `covered` has an [[artnet]] output that drives it, so it
    is skipped entirely: no simulator competes with the output and no
    recorded device is reattached (spec 2026-09-25 section 9).

    The factory is handed the teardown stack and the fixture name, and
    registers whatever it spawns, so an orphaned Room simulator is
    impossible by construction rather than by a getattr convention. Called
    once per fixture -- each fixture is its own o2lite client with its own
    unique service name (design spec section 3).
    """
    profile = room.profile
    for fixture in profile.fixtures:
        if fixture.name in covered:
            continue
        if simulator_factory is not None:
            dev = simulator_factory(teardown, fixture.name)
            room.bound[fixture.name] = dev
            room_binding.bind(room.name, fixture.name, dev)
            continue
        recorded = room_binding.bound_device(room.name, fixture.name)
        if recorded is not None and known_device_connected(recorded):
            room.bound[fixture.name] = recorded
```

Replace `wait_for_room_binding` (lines 75-111) with:

```python
def wait_for_room_binding(gs: GameServer, room_binding: RoomBindingRegistry,
                          timeout: float, *, tick, clock=time.monotonic,
                          sleep=time.sleep, covered=frozenset()) -> None:
    """Hold until every fixture not in `covered` is bound (each admin-armed
    tap grants one fixture's ROOM-class join) or the shared timeout budget
    elapses, arming fixtures one at a time in the profile's declaration
    order. A covered fixture has an [[artnet]] output, so it is never
    waited for and counts as present (spec 2026-09-25 section 9).
    `tick` is called once per iteration -- driving whatever transport/tick
    loop might deliver that join -- so this function has no transport
    opinion of its own.

    Raises RoomBindingTimeout only when NO fixture ever binds and none is
    covered. A Room that is SOME but not all fixtures bound after the
    timeout proceeds anyway -- see design spec section 7: one unresponsive
    fixture must not fail the whole boot.
    """
    profile = gs.room.profile
    pending = [f for f in profile.fixtures
               if f.name not in gs.room.bound and f.name not in covered]
    if not pending:
        return
    deadline = clock() + timeout
    for fixture in pending:
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
    if not gs.room.bound and not covered:
        raise RoomBindingTimeout(
            f"no device joined as {gs.room.name} Room within {timeout}s")
    missing = [f.name for f in profile.fixtures
               if f.name not in gs.room.bound and f.name not in covered]
    if missing:
        logger.warning("Room %s partially bound; missing fixtures: %s",
                       gs.room.name, missing)
```

In `load_room`, replace the block from `_bind_room_fast_path(room, self.room_binding,` through the `except RoomBindingTimeout` clause (lines 318-328) with:

```python
            covered = frozenset(o.fixture for o in self.config.artnet_outputs
                                if o.room == spec.name)
            _bind_room_fast_path(room, self.room_binding,
                                 self._simulator_factory_with_recording(),
                                 self.known_device_connected, stack,
                                 covered=covered)

            if any(f.name not in room.bound and f.name not in covered
                   for f in room.profile.fixtures):
                try:
                    wait_for_room_binding(
                        self.gs, self.room_binding,
                        self.boot_config.room_setup_timeout,
                        tick=self.tick or (lambda: self.gs.tick(0.05)),
                        covered=covered)
                except RoomBindingTimeout as exc:
                    raise RoomLoadError(str(exc)) from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_terrarium.py tests/test_terrarium_cycle.py tests/test_room_binding.py -q`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, 1 skipped, count = baseline + 4.

- [ ] **Step 6: Commit**

```bash
git add control/terrarium.py tests/test_terrarium.py
git commit -m "fix(terrarium): an [[artnet]]-covered fixture spawns no simulator and is never waited for"
```

---

### Task 2: Shared-PSU budget validation

Spec §8. Outputs that name the same `[psus.<name>]` may not sum past 80 % of its `amps`.

**Files:**
- Modify: `control/terrarium_config.py` (`ArtNetOutput` at line 41, `_ARTNET_KEYS` at line 56, `TerrariumConfig` at line 82, `parse_terrarium_config` around line 236, `_parse_artnet` at line 528)
- Test: `tests/test_terrarium_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `ArtNetOutput.psu: str | None = None`
  - `TerrariumConfig.psus: dict[str, float]` (name -> rated amps; default empty)
  - `_parse_psus(raw, *, source: str) -> dict[str, float]`
  - `validate_psu_budgets(outputs, psus: dict[str, float], *, source: str) -> None`, raising `TerrariumConfigError`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium_config.py`. Add `ArtNetOutput` and `validate_psu_budgets` to the module's existing `from control.terrarium_config import ...` list.

```python
def test_an_artnet_output_may_name_a_declared_psu():
    cfg = _with_artnet("""
        [[artnet]]
        room = "ONE"
        fixture = "main"
        host = "127.0.0.1"
        max_amps = 3.0
        psu = "led12v"

        [psus.led12v]
        amps = 12.5
    """)
    (out,) = cfg.artnet_outputs
    assert out.psu == "led12v"
    assert cfg.psus == {"led12v": 12.5}


def test_an_output_without_a_psu_is_unchecked():
    cfg = _with_artnet("""
        [[artnet]]
        room = "ONE"
        fixture = "main"
        host = "127.0.0.1"
        max_amps = 99.0
    """)
    assert cfg.artnet_outputs[0].psu is None
    assert cfg.psus == {}


@pytest.mark.parametrize("extra, key, needle", [
    ('[[artnet]]\nroom = "ONE"\nfixture = "main"\nhost = "h"\nmax_amps = 1.0\npsu = "nope"\n',
     "artnet[0]", "unknown psu"),
    ('[[artnet]]\nroom = "ONE"\nfixture = "main"\nhost = "h"\nmax_amps = 1.0\npsu = ""\n',
     "artnet[0]", "psu"),
    ('[psus.p]\namps = 0\n', "psus.p", "amps"),
    ('[psus.p]\namps = 12.5\nvolts = 12\n', "psus.p", "unknown key"),
    ('psus = 3\n', "psus", "expected"),
])
def test_a_bad_psu_is_a_located_error(extra, key, needle):
    # `psus = 3` must sit at the top level, before MINIMAL's tables.
    text = (extra + ARTNET_BASE) if extra.startswith("psus =") else (
        ARTNET_BASE + "\n" + extra)
    with pytest.raises(TerrariumConfigError, match=needle) as exc:
        parse_terrarium_config(text, source="t.toml")
    assert exc.value.key == key


def _out(fixture, amps, psu="p"):
    return ArtNetOutput(room="R", fixture=fixture, host="h", max_amps=amps, psu=psu)


def test_psu_budget_accepts_a_sum_of_exactly_80_percent():
    validate_psu_budgets((_out("bars", 9.4), _out("fiber", 0.6)), {"p": 12.5},
                         source="t")


def test_psu_budget_refuses_a_sum_just_over_80_percent():
    with pytest.raises(TerrariumConfigError, match="exceeds 80%") as exc:
        validate_psu_budgets((_out("bars", 9.5), _out("fiber", 0.6)), {"p": 12.5},
                             source="t")
    assert exc.value.key == "psus.p"
    assert "artnet[0]" in str(exc.value) and "artnet[1]" in str(exc.value)


def test_psu_budget_ignores_outputs_on_other_or_no_psus():
    validate_psu_budgets((_out("bars", 9.4), _out("fiber", 50.0, psu=None),
                          _out("x", 3.0, psu="q")), {"p": 12.5, "q": 5.0},
                         source="t")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q -k psu`
Expected: collection ERROR, `ImportError: cannot import name 'validate_psu_budgets'`.

- [ ] **Step 3: Implement**

In `control/terrarium_config.py`:

1. Add the field at the end of `ArtNetOutput` (after `keepalive_ms`):

```python
    # Which [psus.<name>] this output's strip hangs on; None = unchecked.
    psu: str | None = None
```

2. Replace `_ARTNET_KEYS` and add the PSU constants after it:

```python
_ARTNET_KEYS = frozenset({"room", "fixture", "host", "max_amps",
                          "start_universe", "port", "amps_per_pixel_full",
                          "lead_ms", "keepalive_ms", "psu"})
_PSU_KEYS = frozenset({"amps"})
# Outputs sharing one PSU must sum to at most this fraction of its rating
# (spec 2026-09-23 section 6.3, enforced per spec 2026-09-25 section 8).
_PSU_BUDGET_FRACTION = 0.8
```

3. Add the field at the end of `TerrariumConfig` (after `artnet_outputs`):

```python
    # [psus.<name>] tables: PSU name -> rated amps. Validation only.
    psus: dict[str, float] = field(default_factory=dict)
```

4. In `_parse_artnet`, just before `out.append(ArtNetOutput(...))`, add:

```python
        psu = entry.get("psu")
        if psu is not None and (not isinstance(psu, str) or not psu):
            raise err("psu must be a non-empty string naming a [psus.<name>] table")
```

and change the `out.append(...)` call to pass `psu=psu`:

```python
        out.append(ArtNetOutput(room=entry["room"], fixture=entry["fixture"],
                                host=entry["host"], max_amps=float(amps),
                                start_universe=start, port=port, psu=psu,
                                **numbers))
```

5. Add these two functions directly after `validate_artnet_outputs`:

```python
def _parse_psus(raw, *, source: str) -> dict[str, float]:
    """[psus.<name>] tables -> {name: rated amps}. Optional."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TerrariumConfigError(source=source, key="psus",
                                   message="expected [psus.<name>] tables")
    out: dict[str, float] = {}
    for name, entry in raw.items():
        key = f"psus.{name}"
        if not isinstance(entry, dict):
            raise TerrariumConfigError(source=source, key=key,
                                       message="expected a table")
        unknown = sorted(set(entry) - _PSU_KEYS)
        if unknown:
            raise TerrariumConfigError(source=source, key=key,
                message=f"unknown key(s) {unknown}; known: {sorted(_PSU_KEYS)}")
        amps = entry.get("amps")
        if isinstance(amps, bool) or not isinstance(amps, (int, float)) or amps <= 0:
            raise TerrariumConfigError(source=source, key=key,
                message="amps is required and must be a positive number "
                        "(the PSU's rated current)")
        out[name] = float(amps)
    return out


def validate_psu_budgets(outputs, psus: dict[str, float], *, source: str) -> None:
    """Every output naming a PSU names a declared one, and each PSU's
    outputs' max_amps sum to at most 80% of its rating. The sum spans every
    room: only one Room loads at a time, but one box has one supply. An
    output with no psu is not checked."""
    loads: dict[str, list[tuple[int, float]]] = {}
    for i, out in enumerate(outputs):
        if out.psu is None:
            continue
        if out.psu not in psus:
            raise TerrariumConfigError(source=source, key=f"artnet[{i}]",
                message=f"unknown psu {out.psu!r}; known: {sorted(psus)}")
        loads.setdefault(out.psu, []).append((i, out.max_amps))
    for name, entries in loads.items():
        total = sum(amps for _, amps in entries)
        limit = _PSU_BUDGET_FRACTION * psus[name]
        if total > limit + 1e-9:
            parts = ", ".join(f"artnet[{i}] {amps:g} A" for i, amps in entries)
            raise TerrariumConfigError(source=source, key=f"psus.{name}",
                message=f"max_amps sum {total:g} A exceeds 80% of the "
                        f"{psus[name]:g} A rating ({limit:g} A): {parts}")
```

6. In `parse_terrarium_config`, directly after `artnet = _parse_artnet(raw.get("artnet"), source=source)`, add:

```python
    psus = _parse_psus(raw.get("psus"), source=source)
    validate_psu_budgets(artnet, psus, source=source)
```

and add `psus=psus` to the final `TerrariumConfig(...)` call (after `artnet_outputs=artnet`). `load_terrarium_config` builds its result with `replace(config, ...)`, so `psus` carries through with no change there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add control/terrarium_config.py tests/test_terrarium_config.py
git commit -m "feat(config): [psus] and [[artnet]] psu; refuse outputs that sum past 80% of a PSU"
```

---

### Task 3: The `venue_fiber` instrument and the `VENUE` Room

Spec §5, §6. Data only, plus tests.

**Files:**
- Create: `instruments/venue_fiber.toml`
- Create: `rooms/VENUE.toml`
- Create: `tests/test_venue_room.py`
- Modify: `tests/test_terrarium_config.py:104` and `tests/test_terrarium_config.py:739` (the two `{"TEST", "DEMO"}` goldens)

**Interfaces:**
- Consumes: `load_terrarium_config("terrarium.toml")` (existing; loads `rooms/` and `instruments/` catalogs).
- Produces: room `"VENUE"` with fixtures `bars` (864 px) and `fiber` (`3 * N` px), node id `ROOM_VENUE_NODE`; instrument `"venue_fiber"`. In `tests/test_venue_room.py`: module constants `CONFIG`, `VENUE`, `N`, which Tasks 4 and 5 append tests beside.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_venue_room.py`:

```python
"""The VENUE Room: LED bars + three fiber-optic engines, two RGBW
fixtures on two WLED controllers (spec 2026-09-25)."""

from control.builtins import builtin_functions
from control.terrarium_config import load_terrarium_config

CONFIG = load_terrarium_config("terrarium.toml")
VENUE = CONFIG.rooms["VENUE"].profile
# LEDs per fiber engine. Spec 2026-09-25 section 2, input I1: pending, so
# 1 until the hardware owner supplies it. rooms/VENUE.toml must agree.
N = 1


def test_venue_fiber_is_a_light_only_instrument():
    inst = CONFIG.instruments["venue_fiber"]
    assert inst.capabilities == frozenset({"light.surface"})
    assert set(builtin_functions(inst)) == {"flash", "stop"}


def test_venue_declares_bars_then_fiber_both_rgbw():
    assert [f.name for f in VENUE.fixtures] == ["bars", "fiber"]
    assert all(f.color_order == "RGBW" for f in VENUE.fixtures)
    assert CONFIG.rooms["VENUE"].node_id == "ROOM_VENUE_NODE"
    assert CONFIG.rooms["VENUE"].backends == ("devicelink", "array")


def test_venue_bars_match_the_demo_array():
    bars = VENUE.fixtures[0]
    (array,) = CONFIG.rooms["DEMO"].profile.fixtures
    assert bars.instrument.name == "venue_array"
    assert bars.pixel_count == 864
    assert bars.blocks == array.blocks
    assert bars.zones == array.zones


def test_venue_fiber_has_one_block_and_one_zone_per_engine():
    fiber = VENUE.fixtures[1]
    assert fiber.instrument.name == "venue_fiber"
    assert fiber.pixel_count == 3 * N
    assert [(b.name, b.start, b.count) for b in fiber.blocks] == [
        ("e1", 0, N), ("e2", N, N), ("e3", 2 * N, N)]
    assert [(z.name, z.start, z.count) for z in fiber.zones] == [
        ("b1", 0, N), ("b2", N, N), ("b3", 2 * N, N)]
    assert VENUE.channel_count == (864 + 3 * N) * 4
```

In `tests/test_terrarium_config.py`, change both `{"TEST", "DEMO"}` assertions (lines 104 and 739) to `{"TEST", "DEMO", "VENUE"}`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_venue_room.py tests/test_terrarium_config.py -q`
Expected: collection ERROR in `test_venue_room.py` (`KeyError: 'VENUE'`), and the two golden tests FAIL (`{"TEST", "DEMO"} != {"TEST", "DEMO", "VENUE"}`).

- [ ] **Step 3: Create `instruments/venue_fiber.toml`**

```toml
description = "Fiber-optic end-glow engines, one colour per bundle"
capabilities = ["light.surface"]
accepted_cues = ["midi", "solid", "mute"]
  [ambient]
  [ambient.light]
  instruments = [ { instrument = "aurora", target = "primary" } ]
```

- [ ] **Step 4: Create `rooms/VENUE.toml`**

```toml
description = "Terrarium venue: 6 m LED bars + 3 fiber-optic engines, two WLED controllers"
backends = ["devicelink", "array"]

[[fixtures]]
name = "bars"
# Art-Net wire order. WLED's own LED settings carry the strip's physical GRBW.
color_order = "RGBW"
instrument = "venue_array"
  [[fixtures.blocks]]
  name = "m1"
  start = 0
  count = 144
  [[fixtures.blocks]]
  name = "m2"
  start = 144
  count = 144
  [[fixtures.blocks]]
  name = "m3"
  start = 288
  count = 144
  [[fixtures.blocks]]
  name = "m4"
  start = 432
  count = 144
  [[fixtures.blocks]]
  name = "m5"
  start = 576
  count = 144
  [[fixtures.blocks]]
  name = "m6"
  start = 720
  count = 144
  [[fixtures.zones]]
  name = "left"
  start = 0
  count = 288
  [[fixtures.zones]]
  name = "center"
  start = 288
  count = 288
  [[fixtures.zones]]
  name = "right"
  start = 576
  count = 288

# One block per RGBW engine, one zone per bundle. N = 1 LED per engine until
# the hardware owner supplies it (spec 2026-09-25 section 2, input I1): each
# engine's block/zone count is N and each start is a multiple of N.
[[fixtures]]
name = "fiber"
color_order = "RGBW"
instrument = "venue_fiber"
  [[fixtures.blocks]]
  name = "e1"
  start = 0
  count = 1
  [[fixtures.blocks]]
  name = "e2"
  start = 1
  count = 1
  [[fixtures.blocks]]
  name = "e3"
  start = 2
  count = 1
  [[fixtures.zones]]
  name = "b1"
  start = 0
  count = 1
  [[fixtures.zones]]
  name = "b2"
  start = 1
  count = 1
  [[fixtures.zones]]
  name = "b3"
  start = 2
  count = 1
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_venue_room.py tests/test_terrarium_config.py tests/test_catalog.py tests/test_room_profile.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, 1 skipped. If any other test pins the set of shipped rooms or instruments, update its literal to include `VENUE` / `venue_fiber` and name the file in the commit message body.

- [ ] **Step 7: Commit**

```bash
git add instruments/venue_fiber.toml rooms/VENUE.toml tests/test_venue_room.py tests/test_terrarium_config.py
git commit -m "feat(rooms): VENUE room (bars + fiber) and the light-only venue_fiber instrument"
```

---

### Task 4: Bits on VENUE, and fixture addressing

Spec §10, D9, D10.

**Files:**
- Modify: `bits/test/test_bit.py:41`, `bits/test/bit.toml` (`[launch] room_types`)
- Modify: `bits/metronome/metronome_bit.py:92`, `bits/metronome/bit.toml` (`[launch] room_types`)
- Modify: `tests/test_test_bit.py:235`, `tests/test_metronome_bit_declarations.py:20`
- Test: `tests/test_venue_room.py` (append)

**Interfaces:**
- Consumes: `VENUE`, `N` from `tests/test_venue_room.py` (Task 3); `GameServer.load_bit`, `BitLoadError` (`control/engine.py`); `fixture_dev` (`control/cues.py`); `slice_light_manifest(manifest, profile, fixture_name)` (`control/role_config.py`); `Room(name=, profile=, node_id=)` (`control/rooms.py`).
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_venue_room.py`. Move the `import` / `from ... import` lines in this block up into the module's import block at the top of the file (after the existing imports), and append the rest:

```python
import pytest

from bits.metronome.metronome_bit import MetronomeBit
from bits.test.test_bit import TestBit
from control.cues import fixture_dev
from control.engine import BitLoadError, GameServer
from control.functions import (Condition, ConditionSource, Function,
                               FunctionTarget, ScriptStep)
from control.role_config import slice_light_manifest
from control.room_binding import RoomBindingRegistry
from control.rooms import Room, room_role_name


def _venue_room():
    return Room(name="VENUE", profile=VENUE, node_id="ROOM_VENUE_NODE")


def test_testbit_loads_on_venue_with_a_two_fixture_room_role():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = _venue_room()
    gs.load_bit("TestBit")
    role = gs.registration.role_table.roles[room_role_name("VENUE")]
    assert role.capacity == 2


def test_metronome_loads_on_venue():
    gs = GameServer({"MetronomeBit": MetronomeBit},
                    room_binding=RoomBindingRegistry())
    gs.room = _venue_room()
    gs.load_bit("MetronomeBit")


class _FiberBit(TestBit):
    """TestBit plus one ROOM function per VENUE fixture, addressed by name."""
    @property
    def function_table(self):
        table = super().function_table
        for name in ("bars", "fiber"):
            table.functions[f"{name}_pulse"] = Function(
                name=f"{name}_pulse", description="d", target=FunctionTarget.ROOM,
                condition=Condition(name="c", description="d",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, (fixture_dev(name), 0xB0, 74, 127)),))
        return table


def test_a_bit_addressing_both_venue_fixtures_loads_on_venue():
    gs = GameServer({"B": _FiberBit}, room_binding=RoomBindingRegistry())
    gs.room = _venue_room()
    gs.load_bit("B")


def test_a_bit_addressing_the_fiber_is_refused_on_demo():
    gs = GameServer({"B": _FiberBit}, room_binding=RoomBindingRegistry())
    gs.room = Room(name="DEMO", profile=CONFIG.rooms["DEMO"].profile,
                   node_id="ROOM_DEMO_NODE")
    with pytest.raises(BitLoadError, match="fiber"):
        gs.load_bit("B")


def test_a_fiber_bundle_target_binds_only_that_zone_on_the_fiber():
    """This slicing is what confines a fiber.b2 cue to the middle bundle
    (spec 2026-09-25 section 11, item 5)."""
    manifest = {"instruments": [
        {"instrument": "glow", "target": "fiber.b2", "params": {"hue": 0.6}}]}
    fiber = slice_light_manifest(manifest, VENUE, "fiber")
    bars = slice_light_manifest(manifest, VENUE, "bars")
    assert [d["target"] for d in fiber["instruments"]] == ["b2"]
    assert bars.get("instruments", []) == []
```

Change `tests/test_test_bit.py:235` to `assert TestBit.room_types == {"TEST", "DEMO", "VENUE"}` and `tests/test_metronome_bit_declarations.py:20` to `assert MetronomeBit.room_types == {"DEMO", "VENUE"}`.

- [ ] **Step 2: Run the tests to verify which fail**

Run: `.venv/bin/python -m pytest tests/test_venue_room.py tests/test_test_bit.py tests/test_metronome_bit_declarations.py -q`
Expected: the two `room_types` assertions FAIL. The `test_venue_room.py` tests may already pass, because `GameServer.load_bit` does not gate on `room_types` (the Console does, from the manifest). They are the behavior guard for this task.

- [ ] **Step 3: Implement**

- `bits/test/test_bit.py:41`: `room_types = {"TEST", "DEMO", "VENUE"}`
- `bits/test/bit.toml` `[launch]`: `room_types = ["TEST", "DEMO", "VENUE"]` (leave `default_room_type = "TEST"`)
- `bits/metronome/metronome_bit.py:92`: `room_types = {"DEMO", "VENUE"}`
- `bits/metronome/bit.toml` `[launch]`: `room_types = ["DEMO", "VENUE"]` (leave `default_room_type = "DEMO"`)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_venue_room.py tests/test_test_bit.py tests/test_metronome_bit_declarations.py tests/test_bit_registry.py tests/test_bit_packages.py tests/test_console_agent.py -q`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add bits/test/test_bit.py bits/test/bit.toml bits/metronome/metronome_bit.py bits/metronome/bit.toml tests/test_venue_room.py tests/test_test_bit.py tests/test_metronome_bit_declarations.py
git commit -m "feat(bits): TestBit and MetronomeBit run on VENUE; pin @fixture addressing of bars and fiber"
```

---

### Task 5: VENUE's Art-Net wiring example and outputs

Spec §7, D6, §11 items 3 and 6 (offline half).

**Files:**
- Modify: `terrarium.toml` (append a commented block after the existing DEMO `[[artnet]]` example, which ends at the `# lead_ms = 0 ...` line)
- Test: `tests/test_venue_room.py` (append)

**Interfaces:**
- Consumes: `_parse_artnet`, `_parse_psus`, `validate_artnet_outputs`, `validate_psu_budgets`, `ArtNetOutput` (`control/terrarium_config.py`, Task 2); `outputs_factory(outputs, *, clock, backend_cls)` (`devicelink/artnet_sink.py`); `StrictFakeArtNet` (`tests/artnet_fake.py`); `DeviceLinkAgent(gs, server, clock=, outputs_for=)` (`devicelink/agent.py`); `FakeServer`, `_FakeOutput`, `_fake_sessions` (`tests/test_devicelink_agent.py`).
- Produces: the marker lines `# --- VENUE [[artnet]] example begin ---` and `# --- VENUE [[artnet]] example end ---` in `terrarium.toml`, which the Task 6 live run also reads.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_venue_room.py`. Move the `import` / `from ... import` lines in this block up into the module's import block at the top of the file (after the existing imports), and append the rest:

```python
import pathlib
import tomllib

from control.terrarium_config import (ArtNetOutput, _parse_artnet, _parse_psus,
                                      validate_artnet_outputs,
                                      validate_psu_budgets)
from devicelink.agent import DeviceLinkAgent
from devicelink.artnet_sink import outputs_factory
from tests.artnet_fake import StrictFakeArtNet
from tests.test_devicelink_agent import FakeServer, _FakeOutput, _fake_sessions

BEGIN = "# --- VENUE [[artnet]] example begin ---"
END = "# --- VENUE [[artnet]] example end ---"
# Loopback stand-ins for the committed placeholders. The amps are a worked
# example only (a 12.5 A PSU shared by both outputs), not a venue figure.
PLACEHOLDERS = {
    "<WLED controller IP>": "127.0.0.1",
    "<BARS_MAX_AMPS>": "9.4",
    "<FIBER_MAX_AMPS>": str(3 * N * 0.025),
    "<FIBER_AMPS_PER_PIXEL>": "0.025",
    "<PSU_RATED_AMPS>": "12.5",
}


def venue_example_toml() -> str:
    """terrarium.toml's commented VENUE block, uncommented, with every
    placeholder replaced by its loopback stand-in."""
    text = pathlib.Path("terrarium.toml").read_text(encoding="utf-8")
    block = text[text.index(BEGIN):text.index(END)].splitlines()[1:]
    body = "\n".join(line[2:] if line.startswith("# ") else line.lstrip("#")
                     for line in block)
    for placeholder, value in PLACEHOLDERS.items():
        body = body.replace(placeholder, value)
    return body


def test_the_committed_venue_example_validates_on_loopback():
    raw = tomllib.loads(venue_example_toml())
    outputs = _parse_artnet(raw["artnet"], source="example")
    assert [(o.fixture, o.port, o.start_universe, o.psu) for o in outputs] == [
        ("bars", 6454, 0, "led12v"), ("fiber", 6455, 0, "led12v")]
    validate_artnet_outputs(outputs, CONFIG.rooms, source="example")
    validate_psu_budgets(outputs, _parse_psus(raw["psus"], source="example"),
                         source="example")


def test_the_committed_venue_example_names_no_real_host():
    text = pathlib.Path("terrarium.toml").read_text(encoding="utf-8")
    block = text[text.index(BEGIN):text.index(END)]
    hosts = [line for line in block.splitlines() if "host =" in line]
    assert hosts and all("<WLED controller IP>" in line for line in hosts)


def test_venue_outputs_build_one_sink_per_fixture_on_its_own_port():
    outs = (ArtNetOutput(room="VENUE", fixture="bars", host="127.0.0.1",
                         max_amps=9.4),
            ArtNetOutput(room="VENUE", fixture="fiber", host="127.0.0.1",
                         max_amps=3 * N * 0.025, port=6455))
    made = {}

    def backend_cls(host, port):
        made[port] = StrictFakeArtNet(host, port)
        return made[port]

    built = outputs_factory(outs, clock=lambda: 100.0,
                            backend_cls=backend_cls)("VENUE", VENUE)
    assert sorted(built) == ["bars", "fiber"]
    (bars,), (fiber,) = built["bars"], built["fiber"]
    bars.send_frame(bytes(864 * 4), when=100.0)
    bars._service_once(100.0)
    fiber.send_frame(bytes(3 * N * 4), when=100.0)
    fiber._service_once(100.0)
    assert len(made[6454].sent) == 7      # 864 px / 128 px per RGBW universe
    assert len(made[6455].sent) == 1


def test_an_unbound_venue_renders_each_fixture_to_its_own_output(monkeypatch):
    """G2: no device bound, both fixtures still render and reach their
    outputs, each at its own width."""
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = _venue_room()
    gs.load_bit("TestBit")
    _fake_sessions(monkeypatch)
    bars_out, fiber_out = _FakeOutput(), _FakeOutput()
    agent = DeviceLinkAgent(
        gs, FakeServer(), clock=lambda: 100.0,
        outputs_for=lambda room, profile: {"bars": [bars_out], "fiber": [fiber_out]})
    assert bars_out.started == 1 and fiber_out.started == 1
    agent._render_room()
    assert [len(f) for f, _ in bars_out.frames] == [864 * 4]
    assert [len(f) for f, _ in fiber_out.frames] == [3 * N * 4]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_venue_room.py -q -k "example or outputs or unbound"`
Expected: the two `example` tests FAIL with `ValueError: substring not found` (no markers yet). The two outputs/render tests may pass already, since they exercise PR #140's machinery on the new Room; they are guards.

- [ ] **Step 3: Add the example to `terrarium.toml`**

Append directly after the existing DEMO example's last line (`# lead_ms = 0          # measured at bring-up (spec section 9 step 6)`):

```toml

# The VENUE room's two outputs, one WLED controller each (spec 2026-09-25
# sections 7 and 8). Uncomment and replace every <...> placeholder. The
# amps come from that spec's section 2 inputs: fiber gets its full draw
# (3 x N x amps_per_pixel_full), bars get 80% of the PSU minus that. For a
# no-hardware run, set both hosts to 127.0.0.1 and run one
# `python -m harness.artnet_listen` per output (the fiber on --port 6455).
# --- VENUE [[artnet]] example begin ---
# [[artnet]]
# room = "VENUE"
# fixture = "bars"
# host = "<WLED controller IP>"
# start_universe = 0                # universes 0-6
# max_amps = <BARS_MAX_AMPS>
# psu = "led12v"
#
# [[artnet]]
# room = "VENUE"
# fixture = "fiber"
# host = "<WLED controller IP>"
# start_universe = 0
# port = 6455                       # loopback only; omit on hardware
# max_amps = <FIBER_MAX_AMPS>
# amps_per_pixel_full = <FIBER_AMPS_PER_PIXEL>
# psu = "led12v"                    # only if the fiber engines share this PSU
#
# [psus.led12v]
# amps = <PSU_RATED_AMPS>
# --- VENUE [[artnet]] example end ---
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_venue_room.py tests/test_terrarium_config.py -q`
Expected: all PASS. `load_terrarium_config("terrarium.toml")` still sees no active `[[artnet]]` (the block is all comments).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add terrarium.toml tests/test_venue_room.py
git commit -m "feat(config): commented VENUE [[artnet]]/[psus] example, pinned valid and host-free"
```

---

### Task 6: No-hardware live run and deep-dive sync

Spec §11 item 6 (live half), §13, §14 step 7. This is the only task that touches a live Arco.

**Files:**
- Modify: `docs/MM_TERRARIUM.md`
- Scratch only (not committed): a loopback `terrarium.toml` in the session scratchpad

**Interfaces:**
- Consumes: everything above; the `BEGIN`/`END` markers from Task 5.
- Produces: the measured figures recorded in the deep-dive.

- [ ] **Step 1: Build the loopback config in the scratchpad**

**RUN ON: MYCOLOGICAL** (from the worktree root; `$SCRATCH` is this session's scratchpad directory)

```bash
.venv/bin/python - "$SCRATCH/venue-loopback.toml" <<'EOF'
import pathlib, sys
from tests.test_venue_room import venue_example_toml
root = pathlib.Path.cwd()
text = f'''schema = 1

[terrarium]
name = "venue-loopback"
bit_paths = ["{root}/bits"]
instrument_paths = ["{root}/instruments"]
room_paths = ["{root}/rooms"]

''' + venue_example_toml() + "\n"
pathlib.Path(sys.argv[1]).write_text(text)
print(text)
EOF
```

Expected: the printed config holds two active `[[artnet]]` tables on `127.0.0.1` (ports 6454 and 6455) and `[psus.led12v] amps = 12.5`.

- [ ] **Step 2: Start the two fake WLED receivers**

**RUN ON: MYCOLOGICAL**, each in its own terminal tab, from the worktree root:

```bash
.venv/bin/python -m harness.artnet_listen --pixels 864 --seconds 60
```

```bash
.venv/bin/python -m harness.artnet_listen --pixels 3 --port 6455 --seconds 60
```

- [ ] **Step 3: Run the stack on VENUE with no Bit**

**RUN ON: MYCOLOGICAL**

```bash
.venv/bin/python -m harness.run_stack --no-bit --room VENUE --config "$SCRATCH/venue-loopback.toml" --ci --seconds 45
```

Expected:
- `run_stack` exits 0, and the log shows the Room reaching `room ready` with no `binding fixtures` wait and no `sim-room-bars` / `sim-room-fiber` simulator spawned.
- Both receivers report frames: a nonzero `fps`, 0 sequence gaps, 0 bad packets.

Record the fps, gaps and bad-packet counts from both receivers. If the fps is below 44, that is the known open item the `claude/tick-pacing` spec owns; record it and do not investigate here.

- [ ] **Step 4: Sync the deep-dive**

In `docs/MM_TERRARIUM.md`:

1. In the *`devicelink/artnet_sink.py`, `[[artnet]]`, routing by fixture name, native RGBW (2026-09-23)* entry, change the bullet that starts **Bring-up prerequisite: the shipped entry point always ALSO spawns a WebSim simulator** so its first sentence reads `**Closed 2026-09-25 (the VENUE Room entry below):**`, followed by one sentence: an `[[artnet]]`-covered fixture now spawns no simulator and is never waited for, and an all-Art-Net Room loads with no bound device. Keep the historical text after it.
2. Insert a new section directly before `## Boundary rules (the load-bearing invariants)`:

```markdown
### `rooms/VENUE.toml`, `instruments/venue_fiber.toml`, covered-fixture binding, `[psus]` -- the VENUE Room (2026-09-25)
Design: [`.../2026-09-25-venue-room-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-25-venue-room-design.md).

- **VENUE is the real venue Room: two RGBW fixtures, each on its own WLED
  controller.** `bars` copies DEMO's `array` exactly (864 px, blocks
  `m1`..`m6`, zones `left`/`center`/`right`, instrument `venue_array`).
  `fiber` is one block (`e1`..`e3`) and one zone (`b1`..`b3`) per
  fiber-optic engine, instrument `venue_fiber` (light only, so the Room
  has one drone). DEMO stays the single-fixture dev room.
- **N = 1 LED per fiber engine is a placeholder.** The engine part, its
  current, the Terrarium PSU rating (the hardware doc says both 20 A and
  12.5 A) and whether the fiber shares that PSU are the spec's section 2
  inputs, still open. `rooms/VENUE.toml` and `tests/test_venue_room.py`'s
  `N` change together when they close.
- **An `[[artnet]]`-covered fixture needs no binding.** `load_room` spawns
  no simulator for it, reattaches no recorded device to it, and does not
  wait for it; an all-Art-Net Room loads with nothing bound. Before this,
  such a Room timed out in `wait_for_room_binding` (found by reading, then
  pinned by a regression test).
- **`[psus.<name>]` and `[[artnet]] psu`.** Outputs naming one PSU must sum
  `max_amps` to at most 80 % of its `amps`, checked at config load across
  every room. An output with no `psu` is unchecked.
- **Addressing, for Bit authors.** Script steps, generators and streams
  use `@fixture:bars` / `@fixture:fiber`; ROOM light-manifest targets are
  `bars`, `bars.left|center|right`, `fiber`, `fiber.b1|b2|b3`; `primary`
  binds both fixtures. A Bit that names either fixture can list only rooms
  that declare it. TestBit and MetronomeBit list VENUE.
- **Operator gap:** the Console cannot yet target or show the mute state
  of an unbound fixture; `claude/console-fixture-targets` owns that.
- **MEASURED 2026-09-25, loopback, not hardware:** `run_stack --no-bit
  --room VENUE` against two `harness/artnet_listen.py` receivers: bars
  <fps> fps / <gaps> gaps / <bad> bad packets; fiber <fps> fps / <gaps>
  gaps / <bad> bad packets. No physical LED driven yet.
```

Replace each `<fps>`, `<gaps>`, `<bad>` with the Step 3 figures before committing. Also update the entry's test-baseline line if the file's convention has one for this slice: `.venv/bin/python -m pytest tests -q` -> the actual passed/skipped counts from Step 5.

3. In `## Not yet built / deferred`, add one bullet: the spec 2026-09-25 section 2 hardware inputs (fiber N, fiber current, PSU rating, PSU sharing), and a per-bundle fiber ambient.

- [ ] **Step 5: Verify**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, 1 skipped. Record the counts in the deep-dive entry.

Run: `.venv/bin/python -m tools.render_diagrams --check`
Expected: reports the deep-dive's generated diagrams current.

Run: `grep -n "—" docs/MM_TERRARIUM.md | grep -n "VENUE"`
Expected: no output (no em dashes in the new text).

- [ ] **Step 6: Commit**

```bash
git add docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): VENUE room landed; covered-fixture binding closes the double-bind prerequisite"
```
