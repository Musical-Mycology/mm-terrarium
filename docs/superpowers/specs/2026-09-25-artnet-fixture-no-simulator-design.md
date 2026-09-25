# Art-Net-covered fixtures get no simulator and stay unbound

Follow-up to
[`2026-09-23-artnet-fixture-sink-design.md`](2026-09-23-artnet-fixture-sink-design.md)
(PR #140). Closes bring-up prerequisite (1) recorded in the deep-dive entry
*`devicelink/artnet_sink.py`, `[[artnet]]`, routing by fixture name, native
RGBW (2026-09-23)*. Design approved by Chris 2026-09-25.

## 1. What the code does today (verified on `origin/main@fbc5c2f`)

- `harness/terrarium_boot.py` `main()` always builds
  `BootConfig(array_backend="simulator")`, and `build()` always hands the
  `Terrarium` an `_O2SimulatorFactory`.
- `control/terrarium.py` `_bind_room_fast_path` calls that factory for
  **every** fixture whenever one is present. So DEMO's `array` fixture,
  even with `[[artnet]]` coverage, spawns and binds an 864 px WebSim
  simulator and receives 3456-channel `/leds` frames over O2 as well as
  Art-Net. That contradicts D2 of the parent spec (an Art-Net fixture never
  binds), and parent §9 step 4's "Console mute on the unbound `array`
  fixture" would hit a bound fixture.
- **Latent second defect.** Skipping the simulator alone would make DEMO
  fail to load. `load_room` then sees `not room.fully_bound(...)` and
  calls `wait_for_room_binding`, which arms a tap for `array` and raises
  `RoomBindingTimeout` ("no device joined as DEMO Room") when nothing
  binds. The same failure already exists on the `array_backend=None` path
  that `validate_rooms`' `[[artnet]]`-coverage branch admits; no test loads
  a covered Room end to end, so nothing caught it.

## 2. Goals and non-goals

Goals:
- A fixture with `[[artnet]]` coverage **for the Room being loaded** gets no
  simulator, is never reconnected to a recorded binding, is never armed for
  a tap, and stays absent from `room.bound`.
- A Room whose fixtures are all Art-Net-covered loads without waiting.
- Uncovered fixtures, and every box with no `[[artnet]]`, behave exactly as
  today (same factory calls, same order, same teardown pushes).
- `validate_rooms` gating is unchanged in behavior.
- `control/` stays pure stdlib.

Non-goals:
- `BootConfig.array_backend` and `harness/terrarium_boot.py` are unchanged.
- The Console's inability to target or show an unbound fixture (bring-up
  prerequisite (2)) is a separate follow-up.
- Nothing changes in the agent, engine, or sinks: unbound-fixture routing
  and persistent Art-Net outputs already landed in PR #140.

## 3. Decisions

| # | Decision | Why | What would change it |
|---|---|---|---|
| E1 | **The Terrarium decides which fixtures need a device**, from `config.artnet_outputs` | It already holds the config and owns both the fast path and the binding wait; one decision point covers both | A second kind of device-less output that is not in `TerrariumConfig` |
| E2 | Rejected: the harness factory declines covered fixtures | The binding wait would still arm and time out, and `control/` would lean on a harness convention | n/a |
| E3 | **A covered fixture counts as driven.** `load_room` fails on binding only when the Room has no covered fixture AND no fixture bound | Extends the existing rule that one unresponsive fixture must not fail the whole boot (the design spec section 7 cited in `wait_for_room_binding`'s docstring): an Art-Net output is rendering | Chris wanting a mixed Room to fail when none of its device fixtures ever binds |
| E4 | A covered fixture's recorded binding in the store is ignored, not deleted | Staying unbound is the goal; the store is not this change's to rewrite, and removing the `[[artnet]]` entry restores today's reconnect behavior | n/a |

## 4. Design

### 4.1 `control/terrarium_config.py`

New pure function:

```python
def artnet_fixtures(config: TerrariumConfig, room_name: str) -> frozenset[str]:
    """Names of room_name's fixtures that have an [[artnet]] output."""
```

`validate_rooms` uses it per room instead of building its own
`(room, fixture)` set. Its gating is behavior-identical.

### 4.2 `control/terrarium.py`

- `_bind_room_fast_path(..., teardown, *, skip=frozenset())`: a fixture in
  `skip` is passed over before either branch (no factory call, no
  reconnect).
- `wait_for_room_binding(..., *, skip=frozenset(), ...)`:
  - the fixtures it waits for are `profile.fixtures` minus `skip`;
  - it returns immediately when every one of those is bound (including when
    there are none);
  - it never arms a fixture in `skip`;
  - it raises `RoomBindingTimeout` only when `skip` is empty and nothing is
    bound (E3); otherwise it logs the existing partial-bind warning listing
    the missing non-skipped fixtures.
- `load_room` computes `covered = artnet_fixtures(self.config, spec.name)`
  once, passes it as `skip` to both, and gates the wait on "every
  non-covered fixture bound" instead of `room.fully_bound(room.profile)`.
  `Room.fully_bound` itself is unchanged.
- Docstrings: `_bind_room_fast_path`, `wait_for_room_binding`, and the push
  order note in `control/teardown.py` ("one simulator per Room fixture")
  say covered fixtures are skipped.

### 4.3 Data flow, DEMO at the venue

`[[artnet]] room="DEMO" fixture="array"` → `covered={"array"}` → the fast
path spawns nothing → the wait sees no fixture to wait for and returns →
`room.bound == {}` → ROOM_READY. The agent's `outputs_for` builds the
`ArtNetFixtureSink` for `array` exactly as in PR #140, and cues route to
`@fixture:array`.

## 5. Testing (offline)

`tests/test_terrarium.py`:
- a covered fixture is never passed to the factory and is absent from
  `room.bound`; the Room reaches ROOM_READY with no wait;
- a mixed two-fixture Room (built directly as a `TerrariumConfig`, one
  fixture covered) spawns a simulator only
  for the uncovered one;
- a recorded, connected binding for a covered fixture is not reconnected;
- `wait_for_room_binding` never arms a skipped fixture, and does not raise
  when every non-skipped fixture fails to bind but `skip` is non-empty;
- with no `[[artnet]]`, factory calls are identical to today (existing
  tests stay green unmodified);
- `artnet_fixtures` filters by room.

`tests/test_terrarium_boot.py`: a `build()` of DEMO with a covering
`[[artnet]]` entry spawns no simulator process through the fake
`simulator_popen`.

Manual: if a local Arco is available, `run_stack --no-bit --room DEMO` with a
scratch `[[artnet]]` at `127.0.0.1:<p>` and `python -m harness.artnet_listen
--port <p> --pixels 864` shows frames received and no simulator process.

## 6. Docs

The deep-dive's bring-up prerequisite (1), in both the artnet entry and the
*Not yet built* entry, becomes closed, citing this spec. The
`array_backend=None` note is corrected to say the path now actually loads.
