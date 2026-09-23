# Art-Net FixtureSink Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Room fixture configured in `terrarium.toml` drives a WLED ESP32
controller over Art-Net, from Room load to unload. It gets every Bit cue,
override and mute, whether or not a device is bound. Frames are held in
Control until their `when`, power-limited per output, and rendered RGBW
natively.

**Architecture:** There is one `ArtNetFixtureSink` per configured output, in
`devicelink/artnet_sink.py`:
- `send_frame` only pushes to a locked `TimedQueue`.
- A sender thread wakes at the next due time or keepalive. It takes the
  newest due frame, power-limits it, fills a luxaeterna `UniverseSet` and
  sends every universe.
- The engine resolves each fixture to its bound dev, or to `@fixture:<name>`
  when nothing is bound. The agent keys fixture state by that token.
- Fixtures declare 4-letter color orders. Channels per pixel equal
  `len(color_order)`.

**Tech Stack:** Python 3.11+ stdlib (`control/` stays stdlib-only);
luxaeterna (`ArtNet`, `PixelSpan`, `UniverseSet`, `PowerBudget`,
`PowerLimiter`, `ThrottledLog`, `WebSimBackend`); pytest; node for
`tests/js/*.test.js`.

**Spec:** [`docs/superpowers/specs/2026-09-23-artnet-fixture-sink-design.md`](../specs/2026-09-23-artnet-fixture-sink-design.md).
Read it before any task. The section numbers below (§) refer to it.

## Global Constraints

- **Repos and worktrees.**
  - mm-terrarium work happens in this worktree,
    `/Users/chris/projects/mm-terrarium-worktrees/artnet-fixture-sink-spec`,
    on branch `claude/artnet-fixture-sink-spec`.
  - Task 1 is luxaeterna work, in `/Users/chris/projects/luxaeterna`, on its
    own branch.
- **Running the suite.** Run it only through the venv, never `python3`:
  `.venv/bin/python -m pytest tests -q`. The worktree's `.venv` is a
  symlink to `/Users/chris/projects/mm-terrarium/.venv`. Create it with
  `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` if it is missing.
- **luxaeterna suite:**
  `cd /Users/chris/projects/luxaeterna && .venv/bin/pytest tests -q`.
- **Baseline.** At `origin/main@1276cfb` the suite gives **2589 passed, 1
  skipped**. Every task ends green, with only the test changes that task
  names.
- **How luxaeterna reaches mm-terrarium.** mm-terrarium's venv imports
  luxaeterna as an editable install of
  `/Users/chris/projects/luxaeterna/luxaeterna`. Task 1 must be merged to
  luxaeterna `main` and that checkout fast-forwarded before Task 4 starts.
- **`control/` imports nothing outside the stdlib and `control/`**
  (deep-dive boundary). luxaeterna imports belong in `devicelink/` or
  `harness/`.
- **Boundary rule 2.** Nothing raised by a sink, a backend or a factory may
  propagate into the engine tick.
- **Boundary rule 5.** A test double is never more permissive than what it
  stands for. The fake Art-Net backend refuses what `ArtNet.send` refuses.
- **Spelling the fixture prefix.** Only `control/cues.py` spells
  `@fixture:`. Everything else calls `cues.fixture_dev(name)` or
  `cues.fixture_name(dev)`.
- **Art-Net fixtures:**
  - `color_order = "RGBW"`, which is the wire order;
  - 4 channels per pixel and 128 pixels per universe;
  - `max_amps` is required and has no opt-out;
  - defaults: `port = 6454`, `start_universe = 0`,
    `amps_per_pixel_full = 0.025`, `lead_ms = 0`, `keepalive_ms = 250`.
- **Offline.** Tests use fakes or `127.0.0.1` UDP sockets only. Every test
  file that imports luxaeterna starts with `pytest.importorskip("luxaeterna")`.
- **Commits** follow the repo style `feat(scope): ...` / `test(scope): ...` /
  `docs(scope): ...`. End each with the trailer
  `Co-Authored-By: Claude <noreply@anthropic.com>`.
- **Addresses.** Never write a real controller IP anywhere. Examples use
  `127.0.0.1` or the literal placeholder `<WLED controller IP>`.

## File Structure

| File | Task | Responsibility |
|---|---|---|
| luxaeterna `luxaeterna/backends/websim.py` | 1 | Width = `channels_for(color_order)`; page decodes W additively |
| luxaeterna `tests/backends/test_websim.py`, `tests/js/websim_layout.test.js` | 1 | RGBW record and paint tests |
| `control/engine.py` | 2 | `_fixture_target`; every fixture resolvable by name |
| `tests/test_engine_functions.py` | 2 | 12 expectations updated for unbound `@fixture:accent` |
| `devicelink/agent.py` | 3, 4, 7 | Fixture-keyed state (3), per-fixture channel width (4), persistent outputs (7) |
| `tests/test_devicelink_agent.py`, `tests/test_lobby_agent.py` | 3, 4, 7 | Routing, RGBW and outputs tests |
| `control/room_profile.py` | 4 | `RoomFixture.channels`, color-order validation, per-fixture widths |
| `control/room_view.py` | 4 | Fixture entries carry `color_order` |
| `console/static/surface.js`, `tests/js/surface_panel.test.js` | 4 | `_decodePixels(channels, order)` |
| `harness/o2_shroom.py`, `harness/websim_leds.py` | 4 | Width by `len(color_order)` |
| `rooms/DEMO.toml` | 4 | `color_order = "RGBW"` |
| `control/timed_queue.py` | 5 | `next_due()` |
| `devicelink/artnet_sink.py` (new) | 5, 7 | `ArtNetFixtureSink` (5); `outputs_factory` (7) |
| `tests/artnet_fake.py` (new) | 5 | `StrictFakeArtNet` |
| `tests/test_artnet_sink.py` (new) | 5, 7 | Sink and factory tests |
| `control/terrarium_config.py` | 6 | `ArtNetOutput`, `[[artnet]]` parse and validate, `validate_rooms` gate |
| `control/boot_config.py` | 6 | `array_backend` only `None` or `"simulator"` |
| `harness/terrarium_boot.py` | 7 | Passes `outputs_for=outputs_factory(...)` |
| `harness/artnet_listen.py` (new), `tests/test_artnet_listen.py` (new) | 8 | Fake WLED receiver and end-to-end test |
| `docs/MM_TERRARIUM.md`, `terrarium.toml`, the spec | 9 | Deep-dive sync, commented example, status |

---

### Task 1: luxaeterna WebSim renders RGBW surfaces

**Repo:** `/Users/chris/projects/luxaeterna`. Branch off `main`:
`git -C /Users/chris/projects/luxaeterna switch -c claude/websim-rgbw`.

**Files:**
- Modify: `luxaeterna/backends/websim.py`. Change `self._n` in
  `WebSimBackend.__init__` and the page script's `rgb(f,i)`.
- Test: `tests/backends/test_websim.py`, `tests/js/websim_layout.test.js`

**Interfaces:**
- Produces: `WebSimBackend(capability=cap).send(frame)` records
  `frame[:cap.pixel_count * len(cap.color_order)]`. The page paints each
  pixel as `rgb(R+W, G+W, B+W)`, clipped to 255. mm-terrarium's DEMO
  simulator (Task 4) depends on this.

- [ ] **Step 1: Write the failing Python test.** Append to
  `tests/backends/test_websim.py`:

```python
def test_record_only_backend_slices_rgbw_by_four_channels():
    from luxaeterna.synth.capability import SurfaceCapability, Zone
    cap = SurfaceCapability(surface_id="rgbw", pixel_count=3,
                            color_order="RGBW", zones=[Zone("primary", 0, 3)])
    b = WebSimBackend(capability=cap, serve=False)
    b.open()
    frame = bytearray(range(12)) + bytearray(512 - 12)
    b.send(frame)
    assert b.frames[0] == bytes(range(12))      # 3 px * 4 ch, not 3 px * 3
    b.close()
```

- [ ] **Step 2: Write the failing JS test.** Append to
  `tests/js/websim_layout.test.js`, before the file's runner loop at the
  bottom:

```js
test('an RGBW surface paints white additively onto r, g and b', () => {
  const cap = {
    type: 'capability', surface_id: 'rgbw', pixel_count: 2, color_order: 'RGBW',
    zones: [{ name: 'primary', start: 0, count: 2 }],
  };
  // px0: R=10 G=20 B=30 W=5 ; px1: W=250 with R=10 (clips at 255)
  const { canvas } = run(cap, new Uint8Array([10, 20, 30, 5, 10, 0, 0, 250]),
                         { w: 800, h: 600 });
  const styles = canvas.ops.filter((o) => o[0] === 'fillStyle').map((o) => o[1]);
  assert.ok(styles.includes('rgb(15,25,35)'), `got ${styles}`);
  assert.ok(styles.includes('rgb(255,250,250)'), `got ${styles}`);
});
```

- [ ] **Step 3: Run both and confirm they fail.**
  `.venv/bin/pytest tests/backends/test_websim.py tests/backends/test_websim_layout.py -q`
  Expected: the Python test fails because `frames[0]` is 9 bytes, and
  `test_page_layout_behaviour` fails naming the new JS test.

- [ ] **Step 4: Implement.** In `luxaeterna/backends/websim.py`:
  - add `from ..synth.engine import channels_for` to the imports;
  - in `__init__`, replace
    `self._n = self._cap.pixel_count * 3          # bytes we care about`
    with
    `self._n = self._cap.pixel_count * channels_for(self._cap.color_order)  # bytes we care about`;
  - in `PAGE_HTML`, replace the whole `function rgb(f,i){...}` with:

```js
function rgb(f,i){
  const o=cap.color_order,w=o.length,m={};
  for(let j=0;j<w;j++)m[o[j]]=f[i*w+j];
  const W=m.W||0,c=(v)=>Math.min(255,(v||0)+W);
  return 'rgb('+c(m.R)+','+c(m.G)+','+c(m.B)+')';
}
```

- [ ] **Step 5: Run the full luxaeterna suite.** `.venv/bin/pytest tests -q`
  Expected: all pass. The existing GRB Shroom tests are unchanged, because
  `w = 3` there.

- [ ] **Step 6: Commit, open the PR, merge, and fast-forward.**

```bash
git add luxaeterna/backends/websim.py tests/backends/test_websim.py tests/js/websim_layout.test.js
git commit -m "feat(websim): RGBW surfaces -- width from color_order, white drawn additively" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
git push -u origin claude/websim-rgbw
gh pr create --title "feat(websim): RGBW surfaces" --body "WebSimBackend sized its frame and decoded pixels as 3 channels. mm-terrarium's DEMO array becomes RGBW (mm-terrarium spec 2026-09-23-artnet-fixture-sink-design.md section 7). Width now comes from channels_for(color_order), and the page draws W additively onto R, G and B."
```

  **Gate:** Task 4 must not start until this PR is merged **and**
  `git -C /Users/chris/projects/luxaeterna switch main && git -C /Users/chris/projects/luxaeterna pull --ff-only`
  has run. The controller asks the user before merging.

---

### Task 2: Engine resolves every fixture by name

**Files:**
- Modify: `control/engine.py`: the import at lines 15-16, `load_bit`
  (`self._warned_unbound = set()` near line 418), `_room_devs`,
  `_resolve_devs`, the `_resolve_target` docstring, and `_instrument_for`.
- Test: `tests/test_engine_functions.py`

**Interfaces:**
- Produces: `GameServer._fixture_target(name: str) -> str`. It returns the
  bound dev if `name` is bound, else `cues.fixture_dev(name)`.
  `_room_devs()` returns one target per **declared** fixture, in profile
  order, and returns `[]` only when `self.room is None`.
  `_resolve_devs("@fixture:x")` returns `[self._fixture_target("x")]`.
  `_instrument_for("@fixture:x")` returns fixture `x`'s instrument. From
  here on, `on_light_cue`, `on_solid_cue`, `on_mute_change` and
  `on_play_cue` can receive `@fixture:<name>` for unbound fixtures.

- [ ] **Step 1: Rewrite the two unbound-behavior tests to the new rule.** In
  `tests/test_engine_functions.py`, replace
  `test_a_cue_at_an_unbound_fixture_is_dropped_and_warned_once` and
  `test_resolve_target_on_an_unbound_room_returns_nothing` with:

```python
def test_a_cue_at_an_unbound_fixture_reaches_it_by_name(caplog):
    """Spec 2026-09-23 section 4.1: an unbound fixture is addressable by
    name. Its cue goes out as the @fixture: token, with no warning."""
    gs, light, _ = _running(bound={"main": "sim-room-main"})
    with caplog.at_level("WARNING"):
        gs._dispatch_cues([(fixture_dev("accent"), 0xB0, 74, 5),
                           (fixture_dev("accent"), 0xB0, 74, 6)], at=1.0)
    assert light == [(fixture_dev("accent"), 0xB0, 74, 5, 1.0),
                     (fixture_dev("accent"), 0xB0, 74, 6, 1.0)]
    assert not [r for r in caplog.records if "accent" in r.message]


def test_resolve_target_on_an_unbound_room_returns_every_fixture_by_name():
    from control.rooms import Room

    gs = GameServer({}, clock=lambda: 0.0)
    gs.room = Room(name="DEMO", profile=_Room({}).profile, node_id="ROOM_DEMO_NODE")
    assert gs._resolve_target(FunctionTarget.ROOM, None) == [
        fixture_dev("main"), fixture_dev("accent")]


def test_a_bound_fixture_still_resolves_to_its_dev():
    gs, light, _ = _running(bound={"main": "sim-room-main",
                                   "accent": "sim-room-accent"})
    assert gs._resolve_devs(fixture_dev("accent")) == ["sim-room-accent"]
    assert gs._resolve_devs(ROOM) == ["sim-room-main", "sim-room-accent"]


def test_an_unbound_fixture_token_resolves_to_its_instrument():
    gs, _, _ = _running(bound={})
    assert gs._instrument_for(fixture_dev("accent")) is \
        gs.room.profile.fixtures[1].instrument
```

- [ ] **Step 2: Run them and confirm they fail.**
  `.venv/bin/python -m pytest tests/test_engine_functions.py -q -k "by_name or still_resolves or its_instrument"`
  Expected: 3 FAIL (the cue is dropped, `[]` comes back, and
  `_instrument_for` returns `None`). `still_resolves` passes already.

- [ ] **Step 3: Implement.**
  - In `control/engine.py`, change the cues import to
    `from control.cues import (ALL, ROOM, FireFunction, LightCue, MuteCue, PlayCue, SolidCue, fixture_dev, fixture_name)`.
  - Delete `self._warned_unbound = set()` from `load_bit`.
  - Replace `_room_devs` and `_resolve_devs` with:

```python
    def _fixture_target(self, name: str) -> str:
        """The dev a cue for fixture `name` is delivered as: the bound device
        when there is one, else the fixture's own @fixture: token. Every
        declared fixture is addressable, bound or not -- spec
        2026-09-23-artnet-fixture-sink-design.md section 4.1. A bound
        fixture keeps its dev spelling, so the transport sends to that
        device exactly as before."""
        bound = self.room.bound.get(name) if self.room is not None else None
        return bound if bound is not None else fixture_dev(name)

    def _room_devs(self) -> list[str]:
        """One target per DECLARED fixture, in the profile's declaration
        order (never dict/bind order): the bound dev, or @fixture:<name>."""
        if self.room is None:
            return []
        return [self._fixture_target(f.name) for f in self.room.profile.fixtures]

    def _resolve_devs(self, dev: str) -> list[str]:
        """cues.ROOM -> every declared fixture (a broadcast); @fixture:<name>
        -> that fixture's target; anything else passes through as itself.

        An empty list means drop, never raise. Only a ROOM cue with no Room
        loaded drops, warned once per Bit load. load_bit already refuses a
        Bit naming a fixture its Room does not declare."""
        if dev == ROOM:
            devs = self._room_devs()
            if not devs and not self._warned_no_room:
                self._warned_no_room = True
                logger.warning("Bit emitted a ROOM cue with no Room loaded; "
                               "dropping (logged once per Bit load)")
            return devs
        name = fixture_name(dev)
        if name is None:
            return [dev]
        if self.room is None:
            return []
        return [self._fixture_target(name)]
```

  - In `_instrument_for`, replace the `if self.room is not None and self.room.bound:` block with:

```python
        if self.room is not None:
            name = fixture_name(dev)
            for fixture in self.room.profile.fixtures:
                if (fixture.name == name
                        or self.room.bound.get(fixture.name) == dev):
                    return fixture.instrument
```

  - In `_resolve_target`'s docstring, replace "Returns every bound Room
    fixture dev for ROOM" with "Returns every declared Room fixture's
    target for ROOM (its bound dev, or @fixture:<name>)".

- [ ] **Step 4: Update the 10 other expectations the new rule changes.**
  Each of these tests uses `_running()`, which binds only `main`, so the
  unbound `accent` now receives `@fixture:accent` alongside it. The
  prototype run on 2026-09-23 found exactly these. Make exactly these
  edits:
  - `test_manual_fire_dispatches_every_step_with_its_offset`:
    - devs become `["sim-room-main", fixture_dev("accent")] * 3`;
    - times become `[100.0, 100.0, 100.5, 100.5, 102.0, 102.0]`;
    - values become `[127, 127, 40, 40, 0, 0]`.
  - `test_fires_returned_fire_with_explicit_at_overrides_the_ticks_at`:
    `[500.0, 500.0, 500.5, 500.5, 502.0, 502.0]`.
  - `test_generator_cues_dispatch_once_per_running_tick`: `light ==` the
    existing main tuple, followed by
    `(fixture_dev("accent"), 0xB0, 74, 127, pytest.approx(100.0))`.
  - `test_the_record_reports_what_the_fire_resolved_to`:
    `record.devs == ("sim-room-main", fixture_dev("accent"))` and
    `record.steps == 6`.
  - `test_all_resolves_to_the_room_plus_registered_players_deduped`:
    `["sim-room-main", fixture_dev("accent"), "ie1"]`.
  - `test_all_never_lists_a_room_bound_device_twice`:
    `["ie1", fixture_dev("accent")]`.
  - `test_surface_fire_with_room_sentinel_lights_the_room`:
    `["sim-room-main", fixture_dev("accent")]`.
  - `test_a_room_target_with_no_room_bound_fires_and_reaches_nothing`:
    - rename it to
      `test_a_room_target_with_nothing_bound_reaches_every_fixture_by_name`;
    - its docstring becomes "An unbound Room is still addressable by
      fixture name.";
    - assert `[c[0] for c in light] == [fixture_dev("main"), fixture_dev("accent")] * 3`,
      `observer.fired[0].devs == (fixture_dev("main"), fixture_dev("accent"))`
      and `observer.fired[0].steps == 6`.
  - `test_a_raising_observer_does_not_stop_the_cues_or_its_peers`:
    `len(light) == 6`.
  - `test_an_unknown_trigger_from_a_bit_does_not_break_neighbouring_cues`:
    `[5, 5, 6, 6]`.

- [ ] **Step 5: Run the full suite.** `.venv/bin/python -m pytest tests -q`
  Expected: **2591 passed, 1 skipped** (baseline + 2 new tests, net of the
  2 replaced). The agent silently ignores a token it does not know yet;
  Task 3 wires it.

- [ ] **Step 6: Commit.**

```bash
git add control/engine.py tests/test_engine_functions.py
git commit -m "feat(engine): every declared fixture is addressable by name, bound or not" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: The agent keys fixture state by fixture, not by bound dev

**Files:**
- Modify: `devicelink/agent.py`:
  - the imports (`from control.cues import TARGET` becomes
    `from control.cues import TARGET, fixture_dev, fixture_name`);
  - `__init__` (add `self._warned_play: set[str] = set()` next to
    `self._refused_ids`);
  - `_lobby_sinks`, `_flash_fixtures_now`, `unwire_room`;
  - `_fixture_for_dev` becomes `_fixture_for`, with the new
    `_fixture_key`;
  - `_invalidate_frame`, `_render_frames`' override-only pass;
  - `_on_solid_cue`, `_on_mute_change`, `_on_light_cue`, `_on_play_cue`,
    `_render_room`.
- Test: `tests/test_devicelink_agent.py`, `tests/test_lobby_agent.py`

**Interfaces:**
- Consumes: Task 2's `@fixture:<name>` targets.
- Produces:
  - `DeviceLinkAgent._fixture_key(dev) -> str` maps a bound fixture dev,
    or a token, to `fixture_dev(name)`; any other dev passes through
    unchanged.
  - `DeviceLinkAgent._fixture_for(dev) -> _FixtureState | None`.
  - `_overrides`, `_muted` and the queued light payloads use the fixture
    token for every Room fixture.

- [ ] **Step 1: Write the failing tests.** Append to
  `tests/test_devicelink_agent.py`. Also add `fixture_dev` to its
  `from control.cues import ...` line.

```python
# --- routing by fixture name (spec 2026-09-23 section 4.2) -----------------

def test_an_unbound_fixture_receives_a_room_cue_by_name(monkeypatch):
    gs = _room_ready_game_server(bound={})
    sessions = _fake_sessions(monkeypatch)
    DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0)
    gs._dispatch_cues([(ROOM, 0xB0, 74, 99)], at=100.0)
    assert sessions["room_test_main"].fed[-1] == (0xB0, 74, 99)
    assert sessions["room_test_accent"].fed[-1] == (0xB0, 74, 99)


def test_a_solid_cue_paints_an_unbound_fixture(monkeypatch):
    gs = _room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)
    frames = {}
    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0,
                            on_room_frame=lambda n, f: frames.__setitem__(n, f))
    gs._dispatch_cues([SolidCue(fixture_dev("main"), (255, 0, 0), 1.0, 5.0)],
                      at=100.0)
    agent._render_room()
    assert frames["main"] == bytes([0, 255, 0]) * 60     # GRB red


def test_a_bound_devs_mute_is_keyed_by_its_fixture(monkeypatch):
    gs = _room_ready_game_server(bound={"main": "sim-room-main"})
    _fake_sessions(monkeypatch)
    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0)
    agent._on_mute_change("sim-room-main", True)
    assert agent._overrides[fixture_dev("main")] == ((0, 0, 0), 0.0, None)
    assert "sim-room-main" not in agent._overrides
    assert fixture_dev("main") in agent._muted


def test_a_mute_latched_while_unbound_still_blacks_the_fixture_after_a_bind(monkeypatch):
    gs = _room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=lambda: 100.0)
    agent._on_mute_change(fixture_dev("main"), True)
    gs.room.bound["main"] = "sim-room-main"
    server.bind_dev("sim-room-main", "c")
    agent._render_room()
    (frame,) = [m["args"][0] for d, m in server.sent if d == "sim-room-main"]
    assert bytes(frame) == bytes(180)


def test_a_play_cue_for_an_unbound_fixture_is_dropped_and_warned_once(monkeypatch, caplog):
    gs = _room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=lambda: 100.0)
    caplog.clear()
    with caplog.at_level("WARNING"):
        agent._on_play_cue(fixture_dev("main"), "click", "")
        agent._on_play_cue(fixture_dev("main"), "click", "")
    assert server.sent == []
    assert sum("main" in r.message for r in caplog.records) == 1


def test_a_fixture_token_override_never_reaches_the_player_pass(monkeypatch):
    gs = _room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=lambda: 100.0)
    agent._on_solid_cue(fixture_dev("main"), (0, 0, 255), 1.0, 5.0, 100.0)
    agent.poll()
    assert not [m for _d, m in server.sent if m["address"].startswith("/@")]


def test_accept_flash_reaches_an_unbound_fixture(monkeypatch):
    gs = _room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)
    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0)
    agent._flash_fixtures_now((0, 255, 0), 1)
    agent._drain_light_cues()
    assert agent._overrides[fixture_dev("main")][0] == (0, 255, 0)
    assert agent._overrides[fixture_dev("accent")][0] == (0, 255, 0)
```

- [ ] **Step 2: Run them and confirm they fail.**
  `.venv/bin/python -m pytest tests/test_devicelink_agent.py -q -k "by_name or unbound_fixture or keyed_by_its_fixture or after_a_bind or player_pass"`
  Expected: FAIL. Tokens are not recognized, and overrides are keyed by the
  bound dev.

- [ ] **Step 3: Implement.** In `devicelink/agent.py`:

  (a) Replace `_fixture_for_dev` with:

```python
    def _fixture_key(self, dev: str) -> str:
        """The agent's own key for `dev`: @fixture:<name> for anything that
        addresses a Room fixture -- the token itself, or a device currently
        bound to one -- and `dev` unchanged for a player. Fixture state
        (overrides, mutes, queued feeds) is keyed by fixture, so it survives
        a device binding or rebinding mid-run. Spec 2026-09-23 section 4.2."""
        if fixture_name(dev) is not None:
            return dev
        gs = self.game_server
        if gs.room is not None:
            for name, bound in gs.room.bound.items():
                if bound == dev:
                    return fixture_dev(name)
        return dev

    def _fixture_for(self, dev: str) -> _FixtureState | None:
        name = fixture_name(self._fixture_key(dev))
        return None if name is None else self._fixtures.get(name)
```

  (b) Rename every remaining `self._fixture_for_dev(` to
  `self._fixture_for(`. It appears in `_invalidate_frame`,
  `_on_mute_change`, `_render_frames`, `_feed_light_now` and
  `_on_light_cue`.

  (c) Make the first statement of `_on_solid_cue`, `_on_mute_change` and
  `_on_light_cue` `dev = self._fixture_key(dev)`. This covers the
  `_light_cues` payloads and the `__flash__` sentinel too, because they
  are pushed from those methods.

  (d) Replace `_on_play_cue`'s body with:

```python
        if fixture_name(dev) is not None:
            # Task 2 only produces a token for a fixture with no bound
            # device; a play cue names a sample ON a device, so there is
            # nowhere to send it. Logged once per Room.
            if dev not in self._warned_play:
                self._warned_play.add(dev)
                logger.warning("play cue for %s: no device bound; dropping "
                               "(logged once per Room)", dev)
            return
        self._send(dev, protocol.play_event(dev, name, params))
```

  (e) In `_render_room`, replace

```python
            if dev is not None:
                frame = self._apply_override(dev, frame, fixture.color_order)
```

  with

```python
            frame = self._apply_override(fixture_dev(fixture.name), frame,
                                         fixture.color_order)
```

  (f) In `_render_frames`' override-only loop, extend the skip condition
  to:
  `if (fixture_name(dev) is not None or dev in self.bridges or dev in bound or self._fixture_for(dev) is not None or gs.devices.get(dev) is None):`

  (g) In `_lobby_sinks`:
  - `feed_light` becomes
    `if st is None or fixture_dev(name) in self._muted: return`
    (drop its `dev =` line);
  - `bound_dev` becomes
    `bound_dev=lambda name: fixture_dev(name) if name in self._fixtures else None,`.

  (h) In `_flash_fixtures_now`, replace the inner loop body with:

```python
            for name in self._fixtures:
                dev = fixture_dev(name)
                self._light_cues.push(at, ("__flash__", dev, rgb, at), now=now)
```

  (i) In `unwire_room`, after the `gone` set is built, add
  `gone |= {fixture_dev(n) for n in self._fixtures}`. At the end of the
  method, add `self._warned_play.clear()`.

  (j) In the comments on `_overrides` in `__init__`, change "Keyed by the
  real fixture dev for a Room fixture" to "Keyed by @fixture:<name> for a
  Room fixture (_fixture_key)".

- [ ] **Step 4: Update the internal-key assertions.** Only these change
  (bound dev → fixture token). Add `from control.cues import fixture_dev`
  where it's missing.
  - `tests/test_lobby_agent.py`:
    - `agent._overrides["sim-main"]` becomes
      `agent._overrides[fixture_dev("main")]`;
    - `"sim-main" not in agent._overrides` becomes
      `fixture_dev("main") not in agent._overrides`;
    - `agent._overrides["sim-accent"]` becomes
      `agent._overrides[fixture_dev("accent")]`.
  - `tests/test_devicelink_agent.py`:
    - in `test_unwire_room_drops_a_muted_fixtures_latched_override`,
      `agent._overrides[main]` and `main not in agent._overrides` become
      `fixture_dev("main")`. The `_override_only` and sent-frame assertions
      stay on `main`;
    - in `test_override_expiry_clears_only_that_fixtures_last_frame`,
      `agent._overrides["sim-room-main"]` becomes
      `agent._overrides[fixture_dev("main")]`.

- [ ] **Step 5: Run the full suite.** `.venv/bin/python -m pytest tests -q`
  Expected: **2598 passed, 1 skipped**. If any other failure asserts on an
  internal `_overrides`/`_muted` key, convert it the same way and name it
  in the commit body. Any other kind of failure is a bug in Step 3.

- [ ] **Step 6: Commit.**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py tests/test_lobby_agent.py
git commit -m "feat(devicelink): key fixture overrides, mutes and feeds by fixture; unbound fixtures take cues" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Native RGBW fixtures

**Precondition:** Task 1's gate has been passed. Check it with
`.venv/bin/python -c "import inspect,luxaeterna.backends.websim as w; assert 'channels_for' in inspect.getsource(w)"`.

**Files:**
- Modify:
  - `control/room_profile.py`: `RoomFixture.channels`, color-order
    validation in `RoomProfile.__post_init__`, `channel_count`,
    `fixture_slices`, and the `_MAX_PROFILE_PIXELS` comment;
  - `devicelink/agent.py`: the `_setup_room` `Universe(...)`, the
    `_render_room` slice, `_apply_override`;
  - `control/room_view.py`: `fixtures_view`;
  - `console/static/surface.js`: `onRoomFrame`, plus a new export;
  - `harness/o2_shroom.py` (line ~437) and `harness/websim_leds.py`
    (`identify_blocks_frame`);
  - `rooms/DEMO.toml`.
- Test:
  - `tests/test_room_profile.py`, `tests/test_room_view.py`,
    `tests/test_websim_leds.py`, `tests/test_devicelink_agent.py`,
    `tests/test_terrarium_config.py`;
  - `tests/js/surface_panel.test.js`.

**Interfaces:**
- Produces:
  - `RoomFixture.channels -> int` (`len(color_order)`);
  - `RoomProfile.channel_count` is the sum of `pixel_count * channels`;
  - `fixture_slices()` gives `(name, channel_start, channel_count)` in
    real per-fixture channels;
  - each `fixtures_view` entry has `"color_order"`;
  - `surface.js` exports
    `_decodePixels(channels: number[], colorOrder: string) -> [r,g,b][]`;
  - DEMO's `array` fixture is RGBW, 3456 channels.

- [ ] **Step 1: Write the failing Python tests.**

  Append to `tests/test_room_profile.py`:

```python
def test_rgbw_fixture_has_four_channels_and_sizes_the_profile():
    profile = RoomProfile(surface_id="r", fixtures=(
        _fixture(name="a", color_order="RGBW", blocks=(RoomBlock("a", 0, 10),)),))
    assert profile.fixtures[0].channels == 4
    assert profile.channel_count == 40
    assert profile.fixture_slices() == (("a", 0, 40),)


@pytest.mark.parametrize("order", ["RG", "RGBB", "RGBX", "RGBWW", "rgb"])
def test_a_color_order_that_is_not_a_permutation_of_rgb_or_rgbw_is_refused(order):
    with pytest.raises(ValueError, match="color_order"):
        RoomProfile(surface_id="r", fixtures=(
            _fixture(name="a", color_order=order, blocks=(RoomBlock("a", 0, 10),)),))
```

  In `test_demo_profile_matches_the_real_array_scale`, change
  `profile.channel_count == 2592` to `== 3456`. Add
  `assert array.color_order == "RGBW"`.

  In `tests/test_terrarium_config.py`, the DEMO assertion
  `array.color_order == "GRB"` becomes `== "RGBW"`.

  Append to `tests/test_room_view.py`, which already imports `Room` and
  defines `TEST_PROFILE`:

```python
def test_fixtures_view_carries_each_fixtures_color_order():
    from control.room_view import fixtures_view
    room = Room(name="TEST", profile=TEST_PROFILE, node_id="ROOM_TEST_NODE")
    assert [f["color_order"] for f in fixtures_view(TEST_PROFILE, room)] == \
        [f.color_order for f in TEST_PROFILE.fixtures]
```

  In `tests/test_websim_leds.py`, change
  `test_identify_blocks_frame_paints_demo_blocks_distinctly` to 4 channels:
  - `len(frame) == array.pixel_count * 4`;
  - `offset = block.start * 4`;
  - `frame[offset:offset + 4] == bytes((r, g, b, 0))`;
  - boundary indices `* 4` with 4-byte slices.

  In `tests/test_devicelink_agent.py`:
  - `FakeFixtureSession.render_into` becomes
    `universe.set_range(0, bytes([self._last & 0xFF]) * (self.cap.pixel_count * len(self.cap.color_order)))`;
  - in `test_render_room_does_not_raise_for_a_profile_wider_than_512_channels`,
    `len(universe) == array.pixel_count * 4`, and fix the docstring's
    "2592" to "3456";
  - append:

```python
def test_an_rgbw_solid_override_leaves_white_dark(monkeypatch):
    gs = _demo_room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)
    frames = {}
    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0,
                            on_room_frame=lambda n, f: frames.__setitem__(n, f))
    agent._on_solid_cue(fixture_dev("array"), (255, 0, 0), 1.0, 5.0, 100.0)
    agent._render_room()
    assert frames["array"] == bytes([255, 0, 0, 0]) * 864
```

- [ ] **Step 2: Write the failing JS test.** In
  `tests/js/surface_panel.test.js`, right after the `_laneRowsFor`
  assertion block, add:

```js
  // pure pixel decode: width from color_order, W drawn additively
  assert.deepStrictEqual(surface._decodePixels([255, 0, 0], "GRB"), [[0, 255, 0]]);
  assert.deepStrictEqual(surface._decodePixels([10, 20, 30, 5], "RGBW"), [[15, 25, 35]]);
  assert.deepStrictEqual(surface._decodePixels([10, 0, 0, 250], "RGBW"), [[255, 250, 250]]);
  assert.deepStrictEqual(surface._decodePixels([1, 2, 3], undefined), [[2, 1, 3]]); // GRB default
```

  In the `ROOM` constant at the top of the file, add
  `color_order: "GRB",` to both fixture entries.

- [ ] **Step 3: Run and confirm failure.**
  `.venv/bin/python -m pytest tests/test_room_profile.py tests/test_room_view.py tests/test_websim_leds.py tests/test_devicelink_agent.py tests/test_terrarium_config.py tests/test_console_js.py -q`
  Expected: the new and changed tests FAIL.

- [ ] **Step 4: Implement `control/room_profile.py`.**
  - Add to `RoomFixture`:

```python
    @property
    def channels(self) -> int:
        """Channels per pixel on this fixture's wire: 3 for RGB orders, 4 for
        RGBW. Spec 2026-09-23 section 6.2."""
        return len(self.color_order)
```

  - In `RoomProfile.__post_init__`, before the mixed-orders check, add:

```python
        for fixture in self.fixtures:
            if sorted(fixture.color_order) not in (sorted("RGB"), sorted("RGBW")):
                raise ValueError(
                    f"fixture {fixture.name!r} color_order "
                    f"{fixture.color_order!r} must be a permutation of RGB "
                    f"or RGBW")
```

  - `channel_count` returns
    `sum(f.pixel_count * f.channels for f in self.fixtures)`. Its docstring
    becomes "Wire width of one rendered frame, whole-profile: each
    fixture's pixels times its own channels (3 for RGB, 4 for RGBW)."
  - `fixture_slices` becomes:

```python
        out: list[tuple[str, int, int]] = []
        offset = 0
        for fixture in self.fixtures:
            width = fixture.pixel_count * fixture.channels
            out.append((fixture.name, offset, width))
            offset += width
        return tuple(out)
```

  - Replace the comment above `_MAX_PROFILE_PIXELS` with:
    "One BLOCK is one physical run (a meter of strip, one controller
    output), capped at 170 px. Universes are no longer a block's concern:
    an Art-Net output spans as many as its fixture needs through
    luxaeterna's PixelSpan (devicelink/artnet_sink.py)."
  - In the `RoomBlock` docstring, replace "(one DMX universe / one
    controller's worth)" with "(one physical run)".

- [ ] **Step 5: Implement the agent.** In `devicelink/agent.py`:
  - in `_setup_room`,
    `universe=Universe(channel_count=fixture.pixel_count * fixture.channels)`;
  - in `_render_room`,
    `frame = bytes(st.universe.get_frame()[:fixture.pixel_count * fixture.channels])`;
  - `_apply_override`'s body after `rgb, level, _expires = entry` becomes:

```python
        by_name = {**dict(zip("RGB", rgb)), "W": 0}
        pixel = bytes(max(0, min(255, round(by_name[ch] * level)))
                      for ch in color_order)
        reps = len(frame) // len(pixel) + 1
        return (pixel * reps)[:len(frame)]
```

  Add to its docstring: "A SolidCue names an RGB colour; on an RGBW strip
  W stays 0."

- [ ] **Step 6: Implement `room_view`, the harness and the room.**
  - In `control/room_view.py` `fixtures_view`, add
    `"color_order": fixture.color_order,` after `"pixel_count"`.
  - In `harness/o2_shroom.py`,
    `channels = capability.pixel_count * len(capability.color_order)`.
  - In `harness/websim_leds.py` `identify_blocks_frame`:

```python
    fixture = next(f for f in profile.fixtures if f.name == fixture_name)
    order = fixture.color_order.upper()
    width = len(order)
    frame = bytearray(fixture.pixel_count * width)
    for i, block in enumerate(fixture.blocks):
        rgb = dict(zip("RGB", BLOCK_PALETTE[i % len(BLOCK_PALETTE)]), W=0)
        px = bytes(rgb[ch] for ch in order)
        frame[block.start * width:(block.start + block.count) * width] = \
            px * block.count
    return bytes(frame)
```

  - In `rooms/DEMO.toml`, the `array` fixture gets
    `color_order = "RGBW"`. Add the comment line above it:
    `# Art-Net wire order. WLED's own LED settings carry the strip's physical GRBW.`

- [ ] **Step 7: Implement `surface.js`.** In `console/static/surface.js`:
  - add above `onRoomFrame`:

```js
// One fixture frame -> [r, g, b] per pixel. The width comes from the
// fixture's color_order (3 for GRB/RGB, 4 for RGBW); W is drawn additively
// onto r, g and b, clipped at 255. Spec 2026-09-23 section 6.2.
export function _decodePixels(channels, colorOrder) {
  const order = colorOrder || "GRB";
  const width = order.length;
  const n = Math.floor(channels.length / width);
  const out = [];
  for (let i = 0; i < n; i++) {
    const v = {};
    for (let j = 0; j < width; j++) v[order[j]] = channels[i * width + j] || 0;
    const w = v.W || 0;
    out.push([Math.min(255, (v.R || 0) + w), Math.min(255, (v.G || 0) + w),
              Math.min(255, (v.B || 0) + w)]);
  }
  return out;
}
```

  - in `onRoomFrame`, replace everything from `const channels = ...`
    through the pixel loop with:

```js
  const fixture = ((currentRoom && currentRoom.fixtures) || [])
    .find((f) => f.name === msg.fixture);
  const pixels = _decodePixels(msg.channels || [], fixture && fixture.color_order);
```

  - leave `console/static/design.js` unchanged. Its bench renders a Shroom
    capability, which is always GRB. That is recorded in the spec
    amendment.

- [ ] **Step 8: Run the full suite.** `.venv/bin/python -m pytest tests -q`
  Expected: all pass. Any remaining failure pinned to DEMO's old 3-channel
  width (2592, `* 3`, a GRB triple on `array`) is updated to 4 channels
  and listed in the commit body.

- [ ] **Step 9: Commit.**

```bash
git add control/room_profile.py control/room_view.py devicelink/agent.py console/static/surface.js harness/o2_shroom.py harness/websim_leds.py rooms/DEMO.toml tests
git commit -m "feat(rooms): native RGBW fixtures -- channels from color_order end to end; DEMO array is RGBW" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: `ArtNetFixtureSink` and the strict fake backend

**Files:**
- Modify: `control/timed_queue.py` (add `next_due`), `tests/test_timed_queue.py`
- Create: `devicelink/artnet_sink.py`, `tests/artnet_fake.py`,
  `tests/test_artnet_sink.py`

**Interfaces:**
- Produces:
  - `TimedQueue.next_due() -> float | None`.
  - `ArtNetFixtureSink(*, name: str, pixel_count: int,
    start_universe: int, budget: PowerBudget, backend, clock: Callable[[],
    float], lead: float = 0.0, keepalive: float = 0.25)`, with methods
    `send_frame(frame: bytes, when: float) -> None`, `start()`, `close()`,
    `stats() -> dict` and `_service_once(now: float) -> float` (returns the
    next wake time).
  - `CHANNELS_PER_PIXEL = 4`.
  - `stats()` keys: `frames_sent`, `keepalives`, `send_errors`, `late`,
    `lateness` (a tuple of floats).
  - `tests.artnet_fake.StrictFakeArtNet(host=..., port=...)` with
    `.sent: list[tuple[int, bytes]]`, `.opens`, `.closes`, `.fail`
    (an exception or None) and `.gate` (a `threading.Event` or None).

- [ ] **Step 1: Write the failing `next_due` test.** Append to
  `tests/test_timed_queue.py`:

```python
def test_next_due_is_the_earliest_pending_time_or_none():
    q = TimedQueue()
    assert q.next_due() is None
    q.push(5.0, "b", now=0.0)
    q.push(2.0, "a", now=0.0)
    assert q.next_due() == 2.0
    q.due(2.0)
    assert q.next_due() == 5.0
```

- [ ] **Step 2: Implement `next_due`.** Add to `TimedQueue`:

```python
    def next_due(self) -> float | None:
        """The earliest pending release time, or None when empty. Lets a
        sender thread sleep exactly until the next payload is due
        (devicelink/artnet_sink.py)."""
        return min((item[0] for item in self._items), default=None)
```

  Run: `.venv/bin/python -m pytest tests/test_timed_queue.py -q`.
  Expected: PASS.

- [ ] **Step 3: Create the strict fake.** Create `tests/artnet_fake.py`:

```python
"""StrictFakeArtNet: luxaeterna's ArtNet backend, minus the socket, WITH its
strictness (boundary rule 5). It refuses exactly what ArtNet.send refuses:
a send before open(), and a payload that is odd or outside 2-512 bytes.
tests/test_artnet_sink.py's contract test holds it to the real class."""

from __future__ import annotations

from luxaeterna.exceptions import BackendError


class StrictFakeArtNet:
    def __init__(self, host: str = "255.255.255.255", port: int = 6454) -> None:
        self.host = host
        self.port = port
        self.is_open = False
        self.opens = 0
        self.closes = 0
        self.sent: list[tuple[int, bytes]] = []
        self.fail: Exception | None = None
        self.gate = None          # threading.Event: send() blocks until set

    def open(self) -> None:
        self.is_open = True
        self.opens += 1

    def close(self) -> None:
        self.is_open = False
        self.closes += 1

    def send(self, frame, universe_id: int = 0) -> None:
        if self.gate is not None:
            self.gate.wait(5.0)
        if not self.is_open:
            raise BackendError("Art-Net socket not open")
        n = len(frame)
        if n < 2 or n > 512:
            raise BackendError(f"Art-Net frame length {n} outside 2-512")
        if n % 2 != 0:
            raise BackendError(f"Art-Net frame length {n} must be even")
        if self.fail is not None:
            raise self.fail
        self.sent.append((universe_id, bytes(frame)))
```

- [ ] **Step 4: Write the failing sink tests.** Create
  `tests/test_artnet_sink.py`:

```python
"""ArtNetFixtureSink: hold until due, coalesce, keepalive, power limit,
universe split, close-to-black, never block the tick. Spec 2026-09-23
sections 4.3, 5, 8."""

import socket
import threading
import time

import pytest

pytest.importorskip("luxaeterna")

from luxaeterna.backends.artnet import ArtNet
from luxaeterna.exceptions import BackendError
from luxaeterna.power import PowerBudget, PowerLimiter

from devicelink.artnet_sink import ArtNetFixtureSink
from tests.artnet_fake import StrictFakeArtNet


def _sink(px=4, *, max_amps=10.0, lead=0.0, keepalive=0.25, start_universe=0):
    now = [100.0]
    backend = StrictFakeArtNet()
    sink = ArtNetFixtureSink(
        name="DEMO-array", pixel_count=px, start_universe=start_universe,
        budget=PowerBudget(max_amps=max_amps), backend=backend,
        clock=lambda: now[0], lead=lead, keepalive=keepalive)
    return sink, backend, now


def _frame(px, value):
    return bytes([value]) * (px * 4)


def test_a_frame_is_held_until_its_when():
    sink, backend, now = _sink()
    sink.send_frame(_frame(4, 10), when=100.06)
    sink._service_once(100.05)
    assert backend.sent == []
    sink._service_once(100.06)
    assert backend.sent == [(0, _frame(4, 10) + bytes(512 - 16))]


def test_lead_sends_early():
    sink, backend, now = _sink(lead=0.02)
    sink.send_frame(_frame(4, 10), when=100.06)
    sink._service_once(100.03)
    assert backend.sent == []
    sink._service_once(100.041)                  # when - lead, past float noise
    assert len(backend.sent) == 1


def test_several_due_frames_coalesce_to_the_newest():
    sink, backend, now = _sink()
    for v in (1, 2, 3):
        sink.send_frame(_frame(4, v), when=100.0 + v / 100)
    sink._service_once(101.0)
    assert [p[:16] for _u, p in backend.sent] == [_frame(4, 3)]


def test_a_late_frame_is_sent_and_counted():
    sink, backend, now = _sink()
    sink.send_frame(_frame(4, 10), when=99.0)
    sink._service_once(100.0)
    assert len(backend.sent) == 1
    assert sink.stats()["late"] == 1


def test_keepalive_resends_the_last_frame_after_the_interval_and_not_before():
    sink, backend, now = _sink(keepalive=0.25)
    sink.send_frame(_frame(4, 10), when=100.0)
    sink._service_once(100.0)
    sink._service_once(100.2)
    assert len(backend.sent) == 1
    sink._service_once(100.25)
    assert len(backend.sent) == 2
    assert sink.stats()["keepalives"] == 1


def test_nothing_is_sent_before_the_first_frame():
    sink, backend, now = _sink()
    assert sink._service_once(100.0) == pytest.approx(100.25)
    assert backend.sent == []


def test_service_returns_the_earlier_of_next_due_and_keepalive():
    sink, backend, now = _sink(keepalive=0.25)
    sink.send_frame(_frame(4, 10), when=100.0)
    sink.send_frame(_frame(4, 11), when=100.1)
    assert sink._service_once(100.0) == pytest.approx(100.1)


def test_every_frame_passes_the_power_limiter():
    sink, backend, now = _sink(px=864, max_amps=5.0)
    sink.send_frame(_frame(864, 255), when=100.0)
    sink._service_once(100.0)
    payload = b"".join(p for _u, p in backend.sent)[:864 * 4]
    limiter = PowerLimiter(PowerBudget(max_amps=5.0))
    assert max(payload) <= limiter.hard_ceiling
    assert limiter.estimate_amps(payload) <= 5.0 + 1e-9


def test_864_rgbw_pixels_go_out_as_seven_full_universes():
    sink, backend, now = _sink(px=864, start_universe=3)
    sink.send_frame(_frame(864, 10), when=100.0)
    sink._service_once(100.0)
    assert [u for u, _p in backend.sent] == [3, 4, 5, 6, 7, 8, 9]
    assert all(len(p) == 512 for _u, p in backend.sent)
    last = backend.sent[-1][1]
    assert last[:384] == bytes([10]) * 384 and last[384:] == bytes(128)


def test_a_wrong_width_frame_is_dropped_never_truncated(caplog):
    sink, backend, now = _sink()
    with caplog.at_level("WARNING"):
        sink.send_frame(bytes(15), when=100.0)
        sink.send_frame(bytes(15), when=100.0)
    sink._service_once(100.0)
    assert backend.sent == []
    assert sum("width" in r.message for r in caplog.records) == 1


def test_a_send_error_is_counted_and_retried_on_keepalive():
    sink, backend, now = _sink()
    backend.fail = BackendError("boom")
    sink.send_frame(_frame(4, 10), when=100.0)
    sink._service_once(100.0)
    assert sink.stats()["send_errors"] == 1
    backend.fail = None
    sink._service_once(100.25)
    assert len(backend.sent) == 1


def test_close_sends_black_and_closes_the_backend():
    sink, backend, now = _sink()
    sink.send_frame(_frame(4, 10), when=100.0)
    sink._service_once(100.0)
    sink.close()
    assert backend.sent[-1] == (0, bytes(512))
    assert backend.closes == 1


def test_send_frame_never_blocks_while_the_backend_is_stalled():
    now = [0.0]
    backend = StrictFakeArtNet()
    backend.gate = threading.Event()
    sink = ArtNetFixtureSink(name="t", pixel_count=4, start_universe=0,
                             budget=PowerBudget(max_amps=10.0), backend=backend,
                             clock=time.monotonic)
    sink.start()
    try:
        sink.send_frame(_frame(4, 1), when=time.monotonic())
        time.sleep(0.05)                       # sender is now inside send()
        t0 = time.monotonic()
        for v in range(50):
            sink.send_frame(_frame(4, v), when=time.monotonic())
        assert time.monotonic() - t0 < 0.05
    finally:
        backend.gate.set()
        sink.close()


def test_the_thread_starts_and_stops():
    backend = StrictFakeArtNet()
    sink = ArtNetFixtureSink(name="t", pixel_count=4, start_universe=0,
                             budget=PowerBudget(max_amps=10.0), backend=backend,
                             clock=time.monotonic, keepalive=0.02)
    sink.start()
    sink.send_frame(_frame(4, 10), when=time.monotonic())
    deadline = time.monotonic() + 2.0
    while sink.stats()["keepalives"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    sink.close()
    assert sink.stats()["frames_sent"] >= 1
    assert sink.stats()["keepalives"] >= 2
    assert not any(t.name == "artnet-t" for t in threading.enumerate())


@pytest.mark.parametrize("payload", [bytes(0), bytes(1), bytes(2), bytes(511),
                                     bytes(512), bytes(514)])
def test_the_fake_refuses_exactly_what_the_real_backend_refuses(payload):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    real = ArtNet(host="127.0.0.1", port=rx.getsockname()[1])
    fake = StrictFakeArtNet()
    try:
        for backend in (real, fake):
            with pytest.raises(BackendError):
                backend.send(payload)          # before open(): both refuse
            backend.open()
        outcomes = []
        for backend in (real, fake):
            try:
                backend.send(payload)
                outcomes.append("ok")
            except BackendError:
                outcomes.append("refused")
        assert outcomes[0] == outcomes[1]
    finally:
        real.close()
        rx.close()
```

- [ ] **Step 5: Run and confirm failure.**
  `.venv/bin/python -m pytest tests/test_artnet_sink.py -q`
  Expected: collection error, `No module named 'devicelink.artnet_sink'`.

- [ ] **Step 6: Implement.** Create `devicelink/artnet_sink.py`:

```python
"""ArtNetFixtureSink: one Room fixture's frames, over Art-Net, to a WLED
controller that has no clock.

WLED latches a frame the moment it arrives, so `when` can only be honored
here: send_frame() queues the frame, and a sender thread sends it once it is
due (minus `lead`, the controller's own measured latency). The newest due
frame wins, so a stall never builds a backlog. With nothing new to send, the
last frame is resent every `keepalive` so WLED stays in realtime mode and a
lost UDP packet heals. Every frame, including keepalives and the close
frame, passes this output's PowerLimiter first.

send_frame() runs on the engine tick: a lock, a push and a notify, and no
I/O (boundary rule 2). Everything else runs on the sender thread.

Spec: docs/superpowers/specs/2026-09-23-artnet-fixture-sink-design.md
sections 4.3, 5 and 8.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from luxaeterna.logutil import ThrottledLog
from luxaeterna.pixelspan import PixelSpan
from luxaeterna.power import PowerBudget, PowerLimiter
from luxaeterna.universeset import UniverseSet

from control.timed_queue import TimedQueue

logger = logging.getLogger(__name__)

CHANNELS_PER_PIXEL = 4          # RGBW: 128 px per universe, no straddling


class ArtNetFixtureSink:
    def __init__(self, *, name: str, pixel_count: int, start_universe: int,
                 budget: PowerBudget, backend, clock: Callable[[], float],
                 lead: float = 0.0, keepalive: float = 0.25) -> None:
        self.name = name
        self._width = pixel_count * CHANNELS_PER_PIXEL
        self._set = UniverseSet(PixelSpan(pixel_count, CHANNELS_PER_PIXEL,
                                          start_universe))
        self._limiter = PowerLimiter(budget)
        self._backend = backend
        self._clock = clock
        self._lead = lead
        self._keepalive = keepalive
        self._queue = TimedQueue()
        self._cond = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None
        self._opened = False
        self._last: bytes | None = None
        self._last_sent_at: float | None = None
        self._throttle = ThrottledLog(logger)
        self._warned_width = False
        self._first_ok = False
        self._frames_sent = 0
        self._keepalives = 0
        self._send_errors = 0

    # --- tick thread -------------------------------------------------------
    def send_frame(self, frame: bytes, when: float) -> None:
        if len(frame) != self._width:
            if not self._warned_width:
                self._warned_width = True
                logger.warning("art-net output %s: frame width %d, expected "
                               "%d; dropping (logged once)", self.name,
                               len(frame), self._width)
            return
        with self._cond:
            self._queue.push(when - self._lead, bytes(frame), now=self._clock())
            self._cond.notify()

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop = False
        self._thread = threading.Thread(target=self._run,
                                        name=f"artnet-{self.name}", daemon=True)
        self._thread.start()

    def close(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._send(bytes(self._width))          # leave the array dark
        try:
            self._backend.close()
        except Exception:
            logger.exception("art-net output %s: backend close failed", self.name)
        self._opened = False

    def stats(self) -> dict:
        with self._cond:
            return {"frames_sent": self._frames_sent,
                    "keepalives": self._keepalives,
                    "send_errors": self._send_errors,
                    "late": self._queue.clamped,
                    "lateness": tuple(self._queue.lateness)}

    # --- sender thread -----------------------------------------------------
    def _run(self) -> None:
        while True:
            with self._cond:
                if self._stop:
                    return
            try:
                wake = self._service_once(self._clock())
            except Exception:
                logger.exception("art-net output %s: sender loop error; "
                                 "continuing", self.name)
                wake = self._clock() + self._keepalive
            with self._cond:
                if self._stop:
                    return
                # Re-read under the lock: a frame pushed after
                # _service_once released it must shorten this wait, or it
                # would sit until the keepalive.
                pending = self._queue.next_due()
                if pending is not None:
                    wake = min(wake, pending)
                timeout = wake - self._clock()
                if timeout > 0:
                    self._cond.wait(timeout)

    def _service_once(self, now: float) -> float:
        """Send whatever is due at `now`; return the next wake time."""
        with self._cond:
            due = self._queue.due(now)
            pending = self._queue.next_due()
        if due:
            frame, keepalive = due[-1], False
        elif (self._last is not None and self._last_sent_at is not None
              and now - self._last_sent_at >= self._keepalive):
            frame, keepalive = self._last, True
        else:
            frame = None
        if frame is not None:
            ok = self._send(frame)
            self._last, self._last_sent_at = frame, now
            if ok:
                with self._cond:
                    if keepalive:
                        self._keepalives += 1
                    else:
                        self._frames_sent += 1
        next_keepalive = (now + self._keepalive if self._last_sent_at is None
                          else self._last_sent_at + self._keepalive)
        return next_keepalive if pending is None else min(pending, next_keepalive)

    def _send(self, frame: bytes) -> bool:
        try:
            if not self._opened:
                self._backend.open()
                self._opened = True
            self._set.set_pixels(self._limiter.apply(frame))
            for universe_id, data in self._set.frames():
                self._backend.send(data, universe_id)
        except Exception as exc:
            with self._cond:
                self._send_errors += 1
            self._throttle.log(f"send:{self.name}", logging.WARNING,
                               "art-net output %s: send failed: %s",
                               self.name, exc)
            return False
        if not self._first_ok:
            self._first_ok = True
            logger.info("art-net output %s: first frame sent", self.name)
        return True
```

- [ ] **Step 7: Run the tests.**
  `.venv/bin/python -m pytest tests/test_artnet_sink.py tests/test_timed_queue.py -q`
  Expected: all PASS. If
  `test_send_frame_never_blocks_while_the_backend_is_stalled` fails, the
  lock is being held across `_send`. Fix the implementation, not the test.

- [ ] **Step 8: Run the full suite, then commit.**
  `.venv/bin/python -m pytest tests -q`. Expected: all pass.

```bash
git add control/timed_queue.py devicelink/artnet_sink.py tests/artnet_fake.py tests/test_artnet_sink.py tests/test_timed_queue.py
git commit -m "feat(devicelink): ArtNetFixtureSink -- hold until due, coalesce, keepalive, power-limit, per-output thread" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: `[[artnet]]` config, the `array` backend gate, and `BootConfig.array_backend`

**Files:**
- Modify: `control/terrarium_config.py` (`ArtNetOutput`, `_parse_artnet`,
  `validate_artnet_outputs`, the `TerrariumConfig.artnet_outputs` field,
  calls in `parse_terrarium_config` and `load_terrarium_config`,
  `validate_rooms`) and `control/boot_config.py`.
- Test: `tests/test_terrarium_config.py`, `tests/test_boot_config.py`

**Interfaces:**
- Consumes: Task 4's RGBW `color_order`.
- Produces:
  - `control.terrarium_config.ArtNetOutput` (frozen), with fields `room`,
    `fixture`, `host`, `max_amps`, `start_universe=0`, `port=6454`,
    `amps_per_pixel_full=0.025`, `lead_ms=0.0`, `keepalive_ms=250.0`;
  - `TerrariumConfig.artnet_outputs: tuple[ArtNetOutput, ...] = ()`;
  - `validate_rooms(config, *, array_backend_configured)` treats an
    `"array"` room as loadable when the simulator is configured or every
    fixture has an output;
  - `BootConfig.array_backend` accepts only `None` or `"simulator"`.

- [ ] **Step 1: Write the failing tests.** Append to
  `tests/test_terrarium_config.py`:

```python
ARTNET_BASE = MINIMAL.replace('color_order = "GRB"', 'color_order = "RGBW"').replace(
    'backends = ["devicelink"]', 'backends = ["devicelink", "array"]')


def _with_artnet(extra: str):
    return parse_terrarium_config(ARTNET_BASE + "\n" + textwrap.dedent(extra),
                                  source="t.toml")


def test_an_artnet_output_parses_with_defaults():
    cfg = _with_artnet("""
        [[artnet]]
        room = "ONE"
        fixture = "main"
        host = "127.0.0.1"
        max_amps = 3.0
    """)
    (out,) = cfg.artnet_outputs
    assert (out.room, out.fixture, out.host, out.max_amps) == ("ONE", "main", "127.0.0.1", 3.0)
    assert (out.start_universe, out.port, out.amps_per_pixel_full,
            out.lead_ms, out.keepalive_ms) == (0, 6454, 0.025, 0.0, 250.0)


@pytest.mark.parametrize("body, needle", [
    ('room = "NOPE"\nfixture = "main"\nhost = "h"\nmax_amps = 1.0', "unknown room"),
    ('room = "ONE"\nfixture = "nope"\nhost = "h"\nmax_amps = 1.0', "unknown fixture"),
    ('room = "ONE"\nfixture = "main"\nhost = "h"', "max_amps"),
    ('room = "ONE"\nfixture = "main"\nhost = "h"\nmax_amps = 0', "max_amps"),
    ('room = "ONE"\nfixture = "main"\nmax_amps = 1.0', "host"),
    ('room = "ONE"\nfixture = "main"\nhost = "h"\nmax_amps = 1.0\nstart_universe = -1', "start_universe"),
    ('room = "ONE"\nfixture = "main"\nhost = "h"\nmax_amps = 1.0\nbogus = 1', "unknown key"),
])
def test_a_bad_artnet_entry_is_a_located_error(body, needle):
    with pytest.raises(TerrariumConfigError, match=needle) as exc:
        _with_artnet("[[artnet]]\n" + body)
    assert exc.value.key.startswith("artnet[0]")


def test_an_rgb_fixture_cannot_be_an_artnet_output():
    text = MINIMAL + '\n[[artnet]]\nroom = "ONE"\nfixture = "main"\nhost = "h"\nmax_amps = 1.0\n'
    with pytest.raises(TerrariumConfigError, match="RGBW"):
        parse_terrarium_config(text, source="t.toml")


def test_duplicate_and_overlapping_outputs_are_refused():
    entry = '[[artnet]]\nroom = "ONE"\nfixture = "main"\nhost = "h"\nmax_amps = 1.0\n'
    with pytest.raises(TerrariumConfigError, match="more than one"):
        _with_artnet(entry + entry)


def test_validate_rooms_accepts_an_array_room_covered_by_artnet():
    cfg = _with_artnet('[[artnet]]\nroom = "ONE"\nfixture = "main"\nhost = "h"\nmax_amps = 1.0\n')
    assert validate_rooms(cfg, array_backend_configured=False)["ONE"] is None


def test_validate_rooms_names_the_uncovered_fixtures():
    cfg = _with_artnet("")
    reason = validate_rooms(cfg, array_backend_configured=False)["ONE"]
    assert "array" in reason and "main" in reason
```

  In `tests/test_boot_config.py`, replace
  `test_array_backend_configured_true_for_real_host` with:

```python
def test_a_host_string_is_no_longer_an_array_backend():
    """Spec 2026-09-23 section 6.1: a real array is configured by [[artnet]]
    entries in terrarium.toml, never by a host here."""
    with pytest.raises(ValueError, match=r"\[\[artnet\]\]"):
        BootConfig(room_name="DEMO", bit_name=None, array_backend="10.44.0.50")
```

  Add `import pytest` there if it's missing, and keep the file's existing
  `BootConfig` import.

- [ ] **Step 2: Run and confirm failure.**
  `.venv/bin/python -m pytest tests/test_terrarium_config.py tests/test_boot_config.py -q`
  Expected: the new tests FAIL (`artnet_outputs` doesn't exist, and the
  host string isn't refused).

- [ ] **Step 3: Implement `BootConfig`.** In `control/boot_config.py`:
  - replace the two-line `array_backend` comment with:
    `# None = no simulated array; "simulator" = Terrarium spawns one. A real
    # array is wired by [[artnet]] entries in terrarium.toml (spec
    # 2026-09-23-artnet-fixture-sink-design.md section 6.1), never here.`
  - add:

```python
    def __post_init__(self) -> None:
        if self.array_backend not in (None, "simulator"):
            raise ValueError(
                f"array_backend must be None or 'simulator', got "
                f"{self.array_backend!r}; a real array is configured by "
                f"[[artnet]] entries in terrarium.toml")
```

- [ ] **Step 4: Implement the config.** In `control/terrarium_config.py`:

  - Add after `UplinkConfig`:

```python
@dataclass(frozen=True)
class ArtNetOutput:
    """One [[artnet]] entry: a Room fixture's physical output to a WLED
    controller. Pure data; devicelink/artnet_sink.py's outputs_factory
    builds the sink. Spec 2026-09-23 section 6.1."""
    room: str
    fixture: str
    host: str
    max_amps: float
    start_universe: int = 0
    port: int = 6454
    amps_per_pixel_full: float = 0.025
    lead_ms: float = 0.0
    keepalive_ms: float = 250.0


_ARTNET_KEYS = frozenset({"room", "fixture", "host", "max_amps",
                          "start_universe", "port", "amps_per_pixel_full",
                          "lead_ms", "keepalive_ms"})
_PIXELS_PER_RGBW_UNIVERSE = 128
```

  - Add the field `artnet_outputs: tuple[ArtNetOutput, ...] = ()` to
    `TerrariumConfig`, after `uplink`.
  - Add the parser and the cross-check:

```python
def _parse_artnet(raw, *, source: str) -> tuple[ArtNetOutput, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TerrariumConfigError(source=source, key="artnet",
                                   message="expected [[artnet]] tables")
    out = []
    for i, entry in enumerate(raw):
        key = f"artnet[{i}]"

        def err(message, key=key):
            return TerrariumConfigError(source=source, key=key, message=message)

        if not isinstance(entry, dict):
            raise err("expected a table")
        unknown = sorted(set(entry) - _ARTNET_KEYS)
        if unknown:
            raise err(f"unknown key(s) {unknown}; known: {sorted(_ARTNET_KEYS)}")
        for name in ("room", "fixture", "host"):
            if not isinstance(entry.get(name), str) or not entry[name]:
                raise err(f"{name} is a required non-empty string")
        amps = entry.get("max_amps")
        if isinstance(amps, bool) or not isinstance(amps, (int, float)) or amps <= 0:
            raise err("max_amps is required and must be a positive number "
                      "(power limiting has no opt-out)")
        start = entry.get("start_universe", 0)
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise err("start_universe must be an integer >= 0")
        port = entry.get("port", 6454)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            raise err("port must be an integer in 1-65535")
        numbers = {}
        for name, default in (("amps_per_pixel_full", 0.025), ("lead_ms", 0.0),
                              ("keepalive_ms", 250.0)):
            v = entry.get(name, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise err(f"{name} must be a number >= 0")
            numbers[name] = float(v)
        if numbers["amps_per_pixel_full"] == 0 or numbers["keepalive_ms"] == 0:
            raise err("amps_per_pixel_full and keepalive_ms must be > 0")
        out.append(ArtNetOutput(room=entry["room"], fixture=entry["fixture"],
                                host=entry["host"], max_amps=float(amps),
                                start_universe=start, port=port, **numbers))
    return tuple(out)


def validate_artnet_outputs(outputs, rooms: dict, *, source: str) -> None:
    """Cross-check [[artnet]] against the rooms it names: the room and
    fixture exist, the fixture is RGBW, one output per fixture, and no two
    outputs on one host:port share a universe."""
    seen: dict[tuple[str, str], int] = {}
    spans: dict[tuple[str, int], list[tuple[int, int, int]]] = {}
    for i, out in enumerate(outputs):
        key = f"artnet[{i}]"
        spec = rooms.get(out.room)
        if spec is None:
            raise TerrariumConfigError(source=source, key=key,
                message=f"unknown room {out.room!r}; known: {sorted(rooms)}")
        fixture = next((f for f in spec.profile.fixtures if f.name == out.fixture), None)
        if fixture is None:
            raise TerrariumConfigError(source=source, key=key,
                message=f"unknown fixture {out.fixture!r} in room {out.room!r}; "
                        f"known: {[f.name for f in spec.profile.fixtures]}")
        if fixture.color_order != "RGBW":
            raise TerrariumConfigError(source=source, key=key,
                message=f"fixture {out.fixture!r} is {fixture.color_order}; an "
                        f"Art-Net output needs color_order = \"RGBW\" (the wire order)")
        if (out.room, out.fixture) in seen:
            raise TerrariumConfigError(source=source, key=key,
                message=f"fixture {out.room}.{out.fixture} has more than one "
                        f"[[artnet]] output (first: artnet[{seen[(out.room, out.fixture)]}])")
        seen[(out.room, out.fixture)] = i
        count = -(-fixture.pixel_count // _PIXELS_PER_RGBW_UNIVERSE)
        lo, hi = out.start_universe, out.start_universe + count - 1
        for (olo, ohi, oi) in spans.setdefault((out.host, out.port), []):
            if lo <= ohi and olo <= hi:
                raise TerrariumConfigError(source=source, key=key,
                    message=f"universes {lo}-{hi} on {out.host}:{out.port} "
                            f"overlap artnet[{oi}] ({olo}-{ohi})")
        spans[(out.host, out.port)].append((lo, hi, i))
```

  - In `parse_terrarium_config`, before building `rooms`, add
    `artnet = _parse_artnet(raw.get("artnet"), source=source)`. After the
    rooms loop, add
    `if require_rooms: validate_artnet_outputs(artnet, rooms, source=source)`.
    Pass `artnet_outputs=artnet` into `TerrariumConfig(...)`.
  - In `load_terrarium_config`, before the final `return`, add
    `validate_artnet_outputs(config.artnet_outputs, rooms, source=path)`.
  - Replace `validate_rooms`' loop body with:

```python
    covered = {(o.room, o.fixture) for o in config.artnet_outputs}
    for name, spec in config.rooms.items():
        out[name] = None
        if "array" not in spec.backends or array_backend_configured:
            continue
        missing = [f.name for f in spec.profile.fixtures
                   if (name, f.name) not in covered]
        if missing:
            out[name] = (f"{name} requires an array backend, none configured: "
                         f"no simulator, and no [[artnet]] output for "
                         f"fixture(s) {missing}")
    return out
```

  In its docstring, add: "`array_backend_configured` means the simulator;
  a real array is `[[artnet]]` coverage of every fixture."

- [ ] **Step 5: Run the full suite.** `.venv/bin/python -m pytest tests -q`
  Expected: all pass. The existing `test_validate_rooms_reports_per_room`
  still passes, because `terrarium.toml` has no `[[artnet]]` and DEMO's
  reason still contains "array".

- [ ] **Step 6: Commit.**

```bash
git add control/terrarium_config.py control/boot_config.py tests/test_terrarium_config.py tests/test_boot_config.py
git commit -m "feat(config): [[artnet]] outputs in terrarium.toml; array rooms load on Art-Net coverage" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Persistent outputs in the agent, and composition at boot

**Files:**
- Modify: `devicelink/agent.py` (the `outputs_for` constructor parameter,
  `_ensure_outputs`, `_close_outputs`, `_sinks_for`, the end of
  `_setup_room` including its early return, `unwire_room`);
  `devicelink/artnet_sink.py` (add `outputs_factory`);
  `harness/terrarium_boot.py` (the `DeviceLinkAgent(...)` call in `build`).
- Test: `tests/test_devicelink_agent.py`, `tests/test_artnet_sink.py`

**Interfaces:**
- Consumes: Task 5's `ArtNetFixtureSink`, Task 6's `ArtNetOutput`.
- Produces:
  - `DeviceLinkAgent(..., outputs_for=None)`. `outputs_for(room_name: str,
    profile: RoomProfile) -> dict[str, list[sink]]` is called once per
    Room. Each sink needs `start()`, `close()` and
    `send_frame(frame, when)`.
  - `devicelink.artnet_sink.outputs_factory(outputs:
    tuple[ArtNetOutput, ...], *, clock, backend_cls=None) -> outputs_for`.

- [ ] **Step 1: Write the failing agent tests.** Append to
  `tests/test_devicelink_agent.py`:

```python
class _FakeOutput:
    def __init__(self):
        self.frames, self.started, self.closed = [], 0, 0

    def start(self):
        self.started += 1

    def close(self):
        self.closed += 1

    def send_frame(self, frame, when):
        self.frames.append((bytes(frame), when))


def _outputs_rig(monkeypatch, bound=None):
    gs = _room_ready_game_server(bound={} if bound is None else bound)
    _fake_sessions(monkeypatch)
    calls, out = [], _FakeOutput()

    def outputs_for(room_name, profile):
        calls.append(room_name)
        return {"main": [out]}

    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0,
                            outputs_for=outputs_for)
    return gs, agent, out, calls


def test_a_configured_output_starts_with_the_room_and_gets_unbound_frames(monkeypatch):
    gs, agent, out, calls = _outputs_rig(monkeypatch)
    assert calls == ["TEST"] and out.started == 1
    agent._render_room()
    assert len(out.frames) == 1 and len(out.frames[0][0]) == 180


def test_outputs_follow_the_room_not_the_bit(monkeypatch):
    gs, agent, out, calls = _outputs_rig(monkeypatch)
    agent._setup_room()
    agent.rewire_room()
    assert calls == ["TEST"] and out.closed == 0


def test_unwire_room_closes_outputs(monkeypatch):
    gs, agent, out, calls = _outputs_rig(monkeypatch)
    gs.room = None
    agent.unwire_room()
    assert out.closed == 1


def test_a_raising_outputs_factory_never_breaks_the_room(monkeypatch):
    gs = _room_ready_game_server(bound={})
    _fake_sessions(monkeypatch)

    def boom(room_name, profile):
        raise RuntimeError("no network")

    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 100.0, outputs_for=boom)
    agent._render_room()                       # must not raise
```

  Append to `tests/test_artnet_sink.py`:

```python
def test_outputs_factory_builds_one_sink_per_matching_output():
    from control.terrarium_config import ArtNetOutput, load_terrarium_config
    from devicelink.artnet_sink import outputs_factory

    profile = load_terrarium_config("terrarium.toml").rooms["DEMO"].profile
    made = []

    def backend_cls(host, port):
        made.append((host, port))
        return StrictFakeArtNet(host, port)

    outputs_for = outputs_factory(
        (ArtNetOutput(room="DEMO", fixture="array", host="127.0.0.1",
                      max_amps=10.0, port=16454, lead_ms=20.0),
         ArtNetOutput(room="OTHER", fixture="x", host="127.0.0.1", max_amps=1.0)),
        clock=lambda: 100.0, backend_cls=backend_cls)
    built = outputs_for("DEMO", profile)
    (sink,) = built["array"]
    assert made == [("127.0.0.1", 16454)]
    assert sink.name == "DEMO-array"
    sink.send_frame(bytes(864 * 4), when=100.02)
    sink._service_once(100.001)                # lead 20 ms: due at ~100.0
    assert len(sink._backend.sent) == 7
```

- [ ] **Step 2: Run and confirm failure.**
  `.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_artnet_sink.py -q -k "output"`
  Expected: FAIL (an unexpected keyword `outputs_for`, and no
  `outputs_factory`).

- [ ] **Step 3: Implement the agent.** In `devicelink/agent.py`:
  - add the constructor parameter `outputs_for=None` after
    `on_join_denied=None`;
  - in `__init__`, before `self._setup_room()`, add:

```python
        # Physical outputs (spec 2026-09-23 section 4.2): built once per
        # Room by outputs_for(room_name, profile) -> {fixture: [sink]},
        # started, handed every changed frame via _sinks_for, closed on
        # unwire. They own threads and sockets, so unlike the per-render
        # sinks they persist across renders AND across Bit loads.
        self._outputs_for = outputs_for
        self._outputs: dict[str, list] = {}
        self._outputs_key = None
```

  - add the methods:

```python
    def _ensure_outputs(self) -> None:
        room = self.game_server.room
        key = None if room is None else (room.name, self._room_profile)
        if key == self._outputs_key:
            return
        self._close_outputs()
        self._outputs_key = key
        if key is None or self._outputs_for is None:
            return
        try:
            built = self._outputs_for(room.name, self._room_profile) or {}
        except Exception:
            logger.exception("building physical outputs for Room %s failed; "
                             "the Room runs without them", room.name)
            return
        for name, sinks in built.items():
            for sink in sinks:
                try:
                    sink.start()
                except Exception:
                    logger.exception("output for fixture %s failed to start", name)
            self._outputs[name] = list(sinks)

    def _close_outputs(self) -> None:
        outputs, self._outputs = self._outputs, {}
        for name, sinks in outputs.items():
            for sink in sinks:
                try:
                    sink.close()
                except Exception:
                    logger.exception("output for fixture %s failed to close", name)
```

  - in `_setup_room`: the early `if room is None: return` becomes
    `if room is None: self._ensure_outputs(); return`. Add
    `self._ensure_outputs()` as the last line, after
    `self._grant_room_audio(role)`;
  - in `_sinks_for`, before `return sinks`, add
    `sinks.extend(self._outputs.get(name, ()))`. Update its docstring to
    "...and the fixture's physical outputs (Art-Net) when configured.";
  - in `unwire_room`, right after `self._exit_lobby(restore_light=False)`,
    add `self._close_outputs()` and `self._outputs_key = None`.

- [ ] **Step 4: Implement the factory.** Append to
  `devicelink/artnet_sink.py`:

```python
def outputs_factory(outputs, *, clock: Callable[[], float], backend_cls=None):
    """terrarium.toml's [[artnet]] entries -> DeviceLinkAgent's outputs_for.
    One ArtNetFixtureSink per entry whose room is the one being loaded."""
    if backend_cls is None:
        from luxaeterna.backends.artnet import ArtNet as backend_cls

    def outputs_for(room_name: str, profile) -> dict[str, list]:
        built: dict[str, list] = {}
        for o in outputs:
            if o.room != room_name:
                continue
            fixture = next((f for f in profile.fixtures if f.name == o.fixture), None)
            if fixture is None:
                continue
            built.setdefault(o.fixture, []).append(ArtNetFixtureSink(
                name=f"{o.room}-{o.fixture}",
                pixel_count=fixture.pixel_count,
                start_universe=o.start_universe,
                budget=PowerBudget(max_amps=o.max_amps,
                                   amps_per_pixel_full=o.amps_per_pixel_full,
                                   channels_per_pixel=CHANNELS_PER_PIXEL),
                backend=backend_cls(host=o.host, port=o.port),
                clock=clock,
                lead=o.lead_ms / 1000.0,
                keepalive=o.keepalive_ms / 1000.0))
        return built

    return outputs_for
```

  Note: `backend_cls` is called with keywords `host=` and `port=`, which
  matches `ArtNet.__init__`. The test's `backend_cls(host, port)` accepts
  keywords too.

- [ ] **Step 5: Wire boot.** In `harness/terrarium_boot.py` `build()`,
  change the `DeviceLinkAgent(...)` call to add
  `outputs_for=_artnet_outputs(terrarium_config, clock),`, and add the
  module-level helper:

```python
def _artnet_outputs(terrarium_config, clock):
    """terrarium.toml's [[artnet]] entries as the agent's outputs_for, or
    None when there are none. The import is local so a box with no Art-Net
    output never touches the Art-Net module."""
    outputs = getattr(terrarium_config, "artnet_outputs", ())
    if not outputs:
        return None
    from devicelink.artnet_sink import outputs_factory
    return outputs_factory(outputs, clock=clock)
```

- [ ] **Step 6: Run the full suite.** `.venv/bin/python -m pytest tests -q`
  Expected: all pass.

- [ ] **Step 7: Commit.**

```bash
git add devicelink/agent.py devicelink/artnet_sink.py harness/terrarium_boot.py tests/test_devicelink_agent.py tests/test_artnet_sink.py
git commit -m "feat(devicelink): persistent per-Room physical outputs; boot builds Art-Net sinks from [[artnet]]" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: `harness/artnet_listen.py`, a fake WLED, and the no-hardware end-to-end test

**Files:**
- Create: `harness/artnet_listen.py`, `tests/test_artnet_listen.py`

**Interfaces:**
- Consumes: Task 5's `ArtNetFixtureSink` and Task 1's RGBW WebSim (for
  `--websim`).
- Produces:
  - `parse_artdmx(packet: bytes) -> tuple[int, int, bytes]`, returning
    `(universe, sequence, payload)` and raising `ArtNetParseError`;
  - `Reassembler(pixel_count, start_universe=0, channels_per_pixel=4)`
    with `.feed(universe, sequence, payload) -> bytes | None` and
    `.seq_gaps: int`;
  - `main()` CLI with `--host`, `--port`, `--pixels`, `--start-universe`,
    `--seconds` and `--websim`.

- [ ] **Step 1: Write the failing tests.** Create
  `tests/test_artnet_listen.py`:

```python
"""The fake WLED: strict ArtDmx parsing, span reassembly, and a real
sink -> real ArtNet -> localhost receiver run. No network beyond 127.0.0.1."""

import socket
import time

import pytest

pytest.importorskip("luxaeterna")

from luxaeterna.backends.artnet import ArtNet
from luxaeterna.power import PowerBudget

from devicelink.artnet_sink import ArtNetFixtureSink
from harness.artnet_listen import ArtNetParseError, Reassembler, parse_artdmx


def _packet(payload=bytes(512), universe=3):
    return ArtNet(host="127.0.0.1")._build_packet(bytearray(payload), universe)


def test_parse_reads_what_the_real_backend_builds():
    universe, _seq, payload = parse_artdmx(_packet(bytes([7]) * 512, universe=5))
    assert universe == 5 and payload == bytes([7]) * 512


@pytest.mark.parametrize("mutate", [
    lambda p: b"Art-Nex\x00" + p[8:],                 # bad ID
    lambda p: p[:8] + b"\x00\x21" + p[10:],           # wrong opcode
    lambda p: p[:-2],                                 # length field lies
    lambda p: p[:17],                                 # truncated header
])
def test_parse_is_strict(mutate):
    with pytest.raises(ArtNetParseError):
        parse_artdmx(mutate(_packet()))


def test_reassembly_emits_once_every_universe_arrived():
    r = Reassembler(pixel_count=200, start_universe=2)   # 800 ch -> universes 2, 3
    assert r.feed(2, 1, bytes([1]) * 512) is None
    frame = r.feed(3, 2, bytes([2]) * 512)
    assert frame == bytes([1]) * 512 + bytes([2]) * 288
    assert r.feed(9, 3, bytes(512)) is None              # not in the span


def test_sequence_gaps_are_counted():
    r = Reassembler(pixel_count=128)
    r.feed(0, 1, bytes(512))
    r.feed(0, 3, bytes(512))
    assert r.seq_gaps == 1


def test_a_sink_frame_reaches_a_localhost_receiver_intact():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2.0)
    sink = ArtNetFixtureSink(
        name="e2e", pixel_count=864, start_universe=0,
        budget=PowerBudget(max_amps=10.0),
        backend=ArtNet(host="127.0.0.1", port=rx.getsockname()[1]),
        clock=time.monotonic)
    frame = bytes(i % 100 for i in range(864 * 4))      # under the 117 ceiling
    sink.start()
    try:
        sink.send_frame(frame, when=time.monotonic())
        r = Reassembler(pixel_count=864)
        got = None
        while got is None:
            universe, seq, payload = parse_artdmx(rx.recv(1024))
            got = r.feed(universe, seq, payload)
        assert got == frame
    finally:
        sink.close()
        rx.close()
```

- [ ] **Step 2: Run and confirm failure.**
  `.venv/bin/python -m pytest tests/test_artnet_listen.py -q`
  Expected: collection error, `No module named 'harness.artnet_listen'`.

- [ ] **Step 3: Implement.** Create `harness/artnet_listen.py`:

```python
"""A fake WLED: receive Art-Net ArtDmx on a UDP port, parse it strictly, and
reassemble one fixture's universes into frames -- for a full no-hardware run
of an [[artnet]] output (spec 2026-09-23 section 8.3).

Usage:
    python -m harness.artnet_listen --port 16454 --pixels 864
    python -m harness.artnet_listen --port 16454 --pixels 864 --websim

Point a terrarium.toml [[artnet]] entry at host = "127.0.0.1" and the same
port. Reports fps, sequence gaps and frame inter-arrival jitter once a
second; --websim also paints each frame on a luxaeterna WebSim canvas.
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

ARTNET_ID = b"Art-Net\x00"
OPCODE_DMX = 0x5000
_HEADER = 18
_DMX_CHANNELS = 512


class ArtNetParseError(ValueError):
    pass


def parse_artdmx(packet: bytes) -> tuple[int, int, bytes]:
    """(universe, sequence, payload). Strict: the ID, opcode, protocol
    version and the length field must all agree with the packet."""
    if len(packet) < _HEADER:
        raise ArtNetParseError(f"packet is {len(packet)} bytes, under the 18-byte header")
    if packet[:8] != ARTNET_ID:
        raise ArtNetParseError("not an Art-Net packet (bad ID)")
    (opcode,) = struct.unpack_from("<H", packet, 8)
    if opcode != OPCODE_DMX:
        raise ArtNetParseError(f"opcode 0x{opcode:04x} is not ArtDmx")
    (version,) = struct.unpack_from(">H", packet, 10)
    if version < 14:
        raise ArtNetParseError(f"protocol version {version} < 14")
    sequence = packet[12]
    (universe,) = struct.unpack_from("<H", packet, 14)
    (length,) = struct.unpack_from(">H", packet, 16)
    payload = packet[_HEADER:]
    if length != len(payload):
        raise ArtNetParseError(f"length field {length} != payload {len(payload)}")
    if length < 2 or length > _DMX_CHANNELS or length % 2:
        raise ArtNetParseError(f"payload length {length} invalid")
    return universe, sequence, bytes(payload)


class Reassembler:
    """Collect one span's universes and emit a frame once every universe has
    arrived since the last emit. Sequence gaps are counted across all
    packets: luxaeterna's ArtNet numbers every send(), not each universe."""

    def __init__(self, pixel_count: int, start_universe: int = 0,
                 channels_per_pixel: int = 4) -> None:
        self._channels = pixel_count * channels_per_pixel
        count = -(-self._channels // _DMX_CHANNELS)
        self.universes = list(range(start_universe, start_universe + count))
        self._parts: dict[int, bytes] = {}
        self._last_seq: int | None = None
        self.seq_gaps = 0

    def feed(self, universe: int, sequence: int, payload: bytes) -> bytes | None:
        if self._last_seq is not None and sequence != (self._last_seq + 1) % 256:
            self.seq_gaps += 1
        self._last_seq = sequence
        if universe not in self.universes:
            return None
        self._parts[universe] = payload.ljust(_DMX_CHANNELS, b"\0")
        if len(self._parts) < len(self.universes):
            return None
        frame = b"".join(self._parts[u] for u in self.universes)[:self._channels]
        self._parts = {}
        return frame


def _websim(pixel_count: int):
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import SurfaceCapability, Zone
    cap = SurfaceCapability(surface_id="artnet_listen", pixel_count=pixel_count,
                            color_order="RGBW",
                            zones=[Zone("primary", 0, pixel_count)])
    backend = WebSimBackend(capability=cap, serve=True, label="artnet_listen")
    backend.open()
    print(f"websim: http://127.0.0.1:{backend.port}/")
    return backend


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6454)
    parser.add_argument("--pixels", type=int, required=True)
    parser.add_argument("--start-universe", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="stop after this long (0 = until Ctrl-C)")
    parser.add_argument("--websim", action="store_true")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(0.5)
    r = Reassembler(args.pixels, args.start_universe)
    sim = _websim(args.pixels) if args.websim else None
    start = report = time.monotonic()
    arrivals: list[float] = []
    errors = 0
    try:
        while not args.seconds or time.monotonic() - start < args.seconds:
            try:
                packet = sock.recv(2048)
            except socket.timeout:
                packet = None
            if packet is not None:
                try:
                    frame = r.feed(*parse_artdmx(packet))
                except ArtNetParseError as exc:
                    errors += 1
                    print(f"bad packet: {exc}")
                    frame = None
                if frame is not None:
                    arrivals.append(time.monotonic())
                    if sim is not None:
                        sim.send(frame)
            now = time.monotonic()
            if now - report >= 1.0:
                gaps = sorted(b - a for a, b in zip(arrivals, arrivals[1:]))
                p50 = gaps[len(gaps) // 2] * 1000 if gaps else 0.0
                p99 = gaps[int(len(gaps) * 0.99)] * 1000 if gaps else 0.0
                print(f"fps {len(arrivals) / (now - report):.1f}  "
                      f"interval p50 {p50:.1f} ms p99 {p99:.1f} ms  "
                      f"seq gaps {r.seq_gaps}  bad packets {errors}")
                arrivals, report = arrivals[-1:], now
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        if sim is not None:
            sim.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests.**
  `.venv/bin/python -m pytest tests/test_artnet_listen.py -q`
  Expected: all PASS.

- [ ] **Step 5: Do a manual no-hardware run and record it in the commit
  body.**
  - In a scratch copy of terrarium.toml (don't commit it), add
    `[[artnet]] room = "DEMO" fixture = "array" host = "127.0.0.1" port = 16454 max_amps = 10.0`.
  - Run
    `.venv/bin/python -m harness.artnet_listen --port 16454 --pixels 864 --websim --seconds 60`
    in one terminal.
  - In another, run
    `.venv/bin/python -m harness.run_stack --room-type DEMO --config <scratch toml>`.
    `run_stack` forwards `--config` to `terrarium_boot`.
  - Record in the commit body: the fps (expect ~44 with motion, ≥4
    keepalive-only), seq gaps 0, bad packets 0, and that the canvas shows
    the DEMO rainbow.

- [ ] **Step 6: Commit.**

```bash
git add harness/artnet_listen.py tests/test_artnet_listen.py
git commit -m "feat(harness): artnet_listen -- a strict fake WLED receiver with WebSim view; e2e sink test" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: Documentation sync

**Files:**
- Modify: `docs/MM_TERRARIUM.md`, `terrarium.toml`,
  `docs/superpowers/specs/2026-09-23-artnet-fixture-sink-design.md`
  (Status line)

- [ ] **Step 1: `terrarium.toml`.** After the commented `[uplink]` block,
  add a commented example. Keep it commented, because the dev box has no
  controller:

```toml
# [[artnet]]
# A Room fixture's physical output to a WLED ESP32 controller (Art-Net).
# The fixture must be color_order = "RGBW". max_amps is required: outputs
# sharing one PSU must SUM to <= 80% of its rating. For a no-hardware run,
# point host at 127.0.0.1 and run `python -m harness.artnet_listen`.
# room = "DEMO"
# fixture = "array"
# host = "<WLED controller IP>"
# start_universe = 0
# max_amps = 10.0
# lead_ms = 0          # measured at bring-up (spec section 9 step 6)
```

- [ ] **Step 2: Deep-dive.** In `docs/MM_TERRARIUM.md`:
  - Add a Landed-subsystems section
    `### devicelink/artnet_sink.py, [[artnet]], routing by fixture name, native RGBW (2026-09-23)`.
    Give the design link and one bullet per task's behavior, and end with
    the test baseline the final suite run reports.
  - In *Per-fixture light sessions*, strike the "Accepted limitation (spec
    section 5.2)" bullet with `~~...~~`, adding **Closed 2026-09-23** and a
    pointer to the new section.
  - Update the sentence "A physical controller ... is a later third
    implementation" to say it landed.
  - In *Not yet built*, mark "A real-hardware Room backend, for either
    room" closed **for DEMO over Art-Net, pending the spec §9 hardware
    bring-up**. Keep "No hardware exists" as it is.
  - In the `harness/` venue-array section, add a line for
    `artnet_listen.py`.
- [ ] **Step 3: Spec status.** Change the spec's Status line to
  "Implemented (Tasks 1-8 of plans/2026-09-23-artnet-fixture-sink.md);
  hardware bring-up (§9) pending."
- [ ] **Step 4: Run the full suite one last time.**
  `.venv/bin/python -m pytest tests -q`. Also run
  `.venv/bin/python -m tools.render_diagrams --check` (the deep-dive's
  generated diagrams must still be current). Put the exact pass count in
  the deep-dive section.
- [ ] **Step 5: Commit.**

```bash
git add docs/MM_TERRARIUM.md terrarium.toml docs/superpowers/specs/2026-09-23-artnet-fixture-sink-design.md
git commit -m "docs(terrarium): Art-Net FixtureSink landed; per-fixture 5.2 limitation closed" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-review notes (plan author, 2026-09-23)

- **Spec coverage.**

  | Spec section | Task(s) |
  |---|---|
  | §4.1 | 2 |
  | §4.2 | 3 and 7 |
  | §4.3 and §5 | 5 |
  | §6.1 | 6 and 7 |
  | §6.2 | 4 |
  | §6.3 | 5 (limiter), 6 (`max_amps` required), 9 (shared-PSU note) |
  | §7 | 1 |
  | §8.1-8.2 | 5 |
  | §8.3 | 5, 7 and 8 |
  | §9 | stays a hardware checklist, outside this plan |
  | §10 | this plan's order |

- **Spec amendments made alongside this plan.**
  - §4.1: the engine keeps a bound fixture's dev spelling and uses the
    token only for unbound fixtures. The agent canonicalizes to the token
    (§4.2). This keeps the §8.3 regression-guard promise; the prototype
    showed only the 12 `test_engine_functions.py` expectations change.
  - §6.2: `console/static/design.js` is unchanged, because the design
    bench renders a Shroom capability, which is GRB.
