# Console 3D Fixture View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every modelled Room fixture as a live, orbitable 3D model in the Terrarium Console's Room card, with an LED inspector (address, RGBW, 10 s trace with input markers) and a full-window bump-out per fixture.

**Architecture:** The server stays the one source of LED positions: `Instrument` gains its resolved model path and size facts, `room_view` gains a per-fixture `model` row, and the Console serves each loaded fixture's layout JSON and `.glb` from fixture-keyed routes. `room_frame` events gain Control's clock `t`, new clients get a frame snapshot, and a new engine `on_gesture` hook feeds a droppable `gesture` event. In the browser, pure modules (trace buffer, inspector model) are node-tested; a thin `view3d.js` wraps a pinned, vendored three.js and is verified in a real browser.

**Tech Stack:** Python 3.14 (stdlib, `websockets`), plain ES-module JS (no build), three.js pinned and vendored, `pytest`, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-27-console-3d-fixture-view-design.md`

## Global Constraints

- No build step, no npm, no CDN or internet at runtime: `console/static/` stays plain ES modules (spec section 5).
- Display only: nothing in Control may wait on the Console; every new hook does an append and nothing else on the calling thread (spec section 5, boundary rule 2).
- A Room that loaded before must still load and show: any model problem falls back to the flat strip with a one-line reason (spec section 5, 8.9).
- Model budget for the 3D view: 300,000 triangles and 20 MB (`docs/instrument-model-guide.md`); over it, the strip.
- Fixture routes serve only fixtures of the loaded Room, keyed by fixture name, never a client path (spec 8.2).
- Vendored files are served by exact relative path under `vendor/`; everything else keeps the basename rule (spec 8.3).
- One time axis: Control's clock (`ConsoleAgent._clock`, the same callable as `GameServer._clock`; `harness/terrarium_boot.py` wires both).
- Trace: last 10 s, step-held, at the existing `ROOM_FRAME_INTERVAL = 0.1` decimation; the inspector states "10 Hz".
- Tests: `.venv/bin/python -m pytest tests -v` and `node --test tests/js/*.test.js`. A fresh worktree has no `.venv`: `ln -s "$HOME/projects/mm-terrarium/.venv" .venv`.
- No em dashes in any doc or comment written for this work.

## Review Focus

1. **Room reload or Bit change mid-session**: the 3D tiles must not rebuild on every `room_changed` (it fires on every live controller value); rebuild only when a fixture's name or `model.sha256` changes. Test in Task 9 (`tile_survives_room_changed_with_same_model`).
2. **A fixture is unbound or Art-Net-driven**: it still gets a 3D tile and a 3D ↗ (spec 8.5). Test in Task 9 (`artnet_fixture_gets_3d_popout`).
3. **A bump-out tab opened while nothing changes**: it must show the current colours from the snapshot, not dark LEDs. Test in Task 5 (`new_client_gets_latest_frame_per_fixture`).
4. **Encoded traversal against the vendor map** (`/vendor/..%2fserver.py`, `/vendor/three/../../agent.py`): 404. Test in Task 3.
5. **The Room unloads or changes while a model request is in flight**: the route answers from the map current at request time; a fixture not in it is a 404, never a stale file. Test in Task 4 (`model_route_404s_after_room_change`).

---

### Task 1: Instrument carries its model path and size facts

**Files:**
- Modify: `control/instrument.py` (the `Instrument` dataclass, ~line 66)
- Modify: `control/model_layout.py` (add `count_triangles`, budget constants)
- Modify: `control/terrarium_config.py` (~line 516-575, where `model_sha256` and `layout` are set)
- Test: `tests/test_model_layout.py`, `tests/test_terrarium_config.py`

**Interfaces:**
- Produces: `Instrument.model_path: str | None = None` (resolved absolute path), `Instrument.model_bytes: int = 0`, `Instrument.model_triangles: int = 0`.
- Produces: `control.model_layout.count_triangles(gltf: dict) -> int`; `MODEL_VIEW_MAX_TRIANGLES = 300_000`; `MODEL_VIEW_MAX_BYTES = 20 * 1024 * 1024`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_model_layout.py`:
```python
def test_count_triangles_sums_indexed_triangle_primitives():
    gltf = {"accessors": [{"count": 30}, {"count": 12}, {"count": 9}],
            "meshes": [{"primitives": [{"indices": 0, "attributes": {"POSITION": 2}},
                                       {"indices": 1, "attributes": {"POSITION": 2}}]}]}
    assert count_triangles(gltf) == 14

def test_count_triangles_uses_position_count_without_indices():
    gltf = {"accessors": [{"count": 9}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}]}
    assert count_triangles(gltf) == 3

def test_count_triangles_skips_non_triangle_modes():
    gltf = {"accessors": [{"count": 9}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 1}]}]}
    assert count_triangles(gltf) == 0
```
In `tests/test_terrarium_config.py`, using the existing model-instrument fixture setup that loads `tests/fixtures/models/marker_fixture.glb`:
```python
def test_model_instrument_records_path_bytes_and_triangles(...):
    inst = <load as the existing model tests do>
    assert inst.model_path == str((<models dir> / "marker_fixture.glb").resolve())
    assert inst.model_bytes == (<that file>).stat().st_size
    assert inst.model_triangles > 0

def test_instrument_without_model_has_no_model_facts(...):
    assert inst.model_path is None and inst.model_bytes == 0 and inst.model_triangles == 0
```

- [ ] **Step 2: Run them; expect FAIL** (`ImportError: count_triangles`, `AttributeError: model_path`)

Run: `.venv/bin/python -m pytest tests/test_model_layout.py tests/test_terrarium_config.py -v -k "count_triangles or model_facts or model_path"`

- [ ] **Step 3: Implement**

`count_triangles`: for each primitive whose `mode` is absent or 4, add `accessors[indices].count // 3` if `indices` is set, else `accessors[attributes.POSITION].count // 3`. Malformed shapes are already refused by `parse_model_layout`; here, skip a primitive with missing keys rather than raise. In `terrarium_config.py`, after `parse_model_layout` succeeds, set the three new fields from `resolved_model`, `len(model_bytes)` and `count_triangles(read_glb_json(model_bytes))`.

- [ ] **Step 4: Run the full Python suite; expect PASS**

Run: `.venv/bin/python -m pytest tests -q`

- [ ] **Step 5: Commit**

```bash
git add control/instrument.py control/model_layout.py control/terrarium_config.py tests/test_model_layout.py tests/test_terrarium_config.py
git commit -m "feat(instrument): keep the model's resolved path, size and triangle count"
```

### Task 2: `room_view` reports each fixture's 3D model status

**Files:**
- Modify: `control/room_view.py` (`fixtures_view`, ~line 104-147)
- Test: `tests/test_room_view.py`

**Interfaces:**
- Consumes: Task 1's `Instrument` fields and budget constants.
- Produces: `control.room_view.model_view_status(instrument) -> dict`, returning `{"sha256": str | None, "status": "ok" | "none" | "over_budget", "reason": str | None}`; each `fixtures_view` row gains `"model"` with that dict.

- [ ] **Step 1: Write the failing tests**

```python
def test_model_status_ok_for_a_modelled_instrument_in_budget():
    inst = Instrument(name="t", pixels=2, model_sha256="ab"*32, model_path="/x.glb",
                      model_bytes=1000, model_triangles=10)
    assert model_view_status(inst) == {"sha256": "ab"*32, "status": "ok", "reason": None}

def test_model_status_none_without_a_model():
    assert model_view_status(Instrument(name="t"))["status"] == "none"

def test_model_status_over_budget_on_triangles():
    inst = Instrument(name="t", model_sha256="ab"*32, model_path="/x.glb",
                      model_bytes=1000, model_triangles=300_001)
    s = model_view_status(inst)
    assert s["status"] == "over_budget" and "300,001 triangles" in s["reason"]

def test_model_status_over_budget_on_bytes():
    inst = Instrument(name="t", model_sha256="ab"*32, model_path="/x.glb",
                      model_bytes=20 * 1024 * 1024 + 1, model_triangles=10)
    assert model_view_status(inst)["status"] == "over_budget"

def test_fixtures_view_rows_carry_the_model_status():
    # existing fixtures_view test setup; every row has a "model" key
```
Reason copy, exactly: `"model has {n:,} triangles; the 3D view's budget is 300,000"` and `"model is {mb:.1f} MB; the 3D view's budget is 20 MB"`.

- [ ] **Step 2: Run; expect FAIL.** `.venv/bin/python -m pytest tests/test_room_view.py -v`
- [ ] **Step 3: Implement `model_view_status` and add the row key.** `sha256` is reported even when over budget.
- [ ] **Step 4: Run the full suite; expect PASS** (existing `room` equality tests may need the new key added to their expected rows).
- [ ] **Step 5: Commit** `feat(room_view): report each fixture's 3D model status`

### Task 3: Console server serves vendored files by exact path and dynamic fixture routes

**Files:**
- Modify: `console/server.py`
- Test: `tests/test_console_server.py`

**Interfaces:**
- Produces: `ConsoleServer.set_route_provider(provider: Callable[[str], tuple[bytes, str, dict] | None]) -> None`. `provider(path)` is called on the HTTP thread with the request path (query string stripped) for any path not found in the static maps; it returns `(body, content_type, extra_headers)` or `None` (404). A raising provider is logged and answered 404.
- `_CONTENT_TYPES` is unchanged: vendored files are `.js`, and provider responses name their own content type, so the static allowlist is not widened.

- [ ] **Step 1: Write the failing tests** (each builds a temp static dir; give `ConsoleServer` a `static_dir: Path | None = None` keyword so tests need not touch the real tree)

```python
def test_vendor_files_are_served_by_exact_relative_path(tmp_path):
    # static/vendor/three/addons/utils/a.js and static/vendor/three/build/a.js
    # GET /vendor/three/addons/utils/a.js -> that file's bytes; the other path -> the other's
def test_vendor_files_are_not_reachable_by_basename(tmp_path):
    # GET /a.js -> 404
def test_vendor_traversal_and_encoded_traversal_are_404(tmp_path):
    for p in ["/vendor/../server.py", "/vendor/three/../../agent.py",
              "/vendor/..%2fserver.py", "/vendor/%2e%2e/server.py"]:
        assert _get_status(server, p) == 404
def test_top_level_basename_rule_is_unchanged(tmp_path):
    # GET /index.html and /fonts/X.ttf behave as today
def test_route_provider_answers_unknown_paths():
    srv.set_route_provider(lambda p: (b"{}", "application/json", {"Cache-Control": "no-store"})
                           if p == "/fixture-layout/tower.json" else None)
    # 200 with body, content type and header; /fixture-layout/x.json -> 404
def test_a_raising_route_provider_is_a_404():
```

- [ ] **Step 2: Run; expect FAIL.** `.venv/bin/python -m pytest tests/test_console_server.py -v`
- [ ] **Step 3: Implement.** At construction, files under `static/vendor/` go into `self._vendor_assets` keyed by their POSIX path relative to `static/` (`"vendor/three/build/three.module.js"`) and are excluded from the basename map; a request whose path (leading `/` stripped, no decoding) exactly equals a key is served. Only then fall back to the basename map, then to the provider.
- [ ] **Step 4: Run the full suite; expect PASS.**
- [ ] **Step 5: Commit** `feat(console): exact-path vendor assets and a fixture route provider`

### Task 4: Console agent publishes each loaded fixture's layout and model

**Files:**
- Create: `console/fixture_models.py`
- Modify: `console/agent.py` (`__init__`, `_broadcast_room_if_changed`)
- Test: `tests/test_console_fixture_models.py`, `tests/test_console_agent.py`

**Interfaces:**
- Consumes: Task 1 fields, Task 2's `model_view_status`, Task 3's `set_route_provider`.
- Produces: `console.fixture_models.FixtureModel` (frozen: `name: str`, `sha256: str`, `model_path: str`, `layout_json: bytes`), `build_fixture_models(profile) -> dict[str, FixtureModel]` (only fixtures whose status is `ok`), and `fixture_route(models: dict[str, FixtureModel], path: str) -> tuple[bytes, str, dict] | None`.
- Routes: `/fixture-layout/<name>.json` and `/fixture-model/<name>.glb`. Both send `Cache-Control: no-cache`; the client adds `?sha=<sha256>` to bust its own cache.
- Layout JSON, exactly: `{"fixture": name, "sha256": ..., "pixels": [{"index": i, "x_mm": ..., "y_mm": ..., "z_mm": ..., "size": "small|medium|large", "model_zone": str|null, "room_zones": ["tower.bottom", ...]}, ...]}`, `room_zones` being every Room zone of that fixture (namespaced `<fixture>.<zone>`) covering the pixel.

- [ ] **Step 1: Write the failing tests**

```python
def test_layout_json_lists_every_pixel_with_room_zones():
    # a RoomProfile with a modelled fixture (use tests/instrument_fixtures.py or
    # build an Instrument with a 3-pixel layout) and zones a:0-1, b:2
    # -> pixels[2]["room_zones"] == ["f.b"], positions copied from PixelLayout
def test_only_ok_fixtures_are_published():   # status none / over_budget -> absent
def test_fixture_route_serves_layout_and_model(tmp_path):  # glb bytes read from model_path
def test_fixture_route_404s_unknown_fixture_and_bad_names():
    for p in ["/fixture-model/nope.glb", "/fixture-model/../x.glb", "/fixture-layout/tower.glb"]:
        assert fixture_route(models, p) is None
def test_model_route_404s_after_room_change():   # in test_console_agent.py:
    # agent publishes tower; room becomes None; provider("/fixture-model/tower.glb") is None
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** The agent keeps `self._fixture_models: dict` and replaces the whole dict (never mutates it) in `_broadcast_room_if_changed` when the Room's profile identity changes, so the HTTP thread always reads a consistent map. It registers `lambda p: fixture_route(self._fixture_models, p)` with `server.set_route_provider` when the server has that method (the test `FakeConsoleServer` may not). The `.glb` is read from disk inside `fixture_route`, on the HTTP thread, never on the tick thread.
- [ ] **Step 4: Run the full suite; expect PASS.**
- [ ] **Step 5: Commit** `feat(console): serve each loaded fixture's layout and model`

### Task 5: Room frames carry Control's clock, and new clients get a snapshot

**Files:**
- Modify: `console/protocol.py` (`room_frame_event`), `console/agent.py` (`on_room_frame`, `_broadcast_room_frame`, `poll`)
- Test: `tests/test_console_protocol.py`, `tests/test_console_agent.py`

**Interfaces:**
- Produces: `room_frame_event(fixture: str, channels, t: float | None = None) -> dict`, adding `"t": t` when given.
- Produces: `ConsoleAgent._latest_room_frames: dict[str, tuple[bytes, float]]`, cleared when the Room changes.

- [ ] **Step 1: Write the failing tests**

```python
def test_room_frame_event_carries_t():
    assert room_frame_event("tower", b"\x01", t=12.5)["t"] == 12.5
def test_room_frame_broadcast_is_stamped_when_rendered():
    # clock returns 5.0 at on_room_frame, 5.2 at poll -> broadcast "t" == 5.0
def test_new_client_gets_latest_frame_per_fixture():
    # on_room_frame("main", ...) then poll, then connect c2 and poll:
    # c2 receives snapshot, then one room_frame for "main" with that frame and its t
def test_latest_frames_are_dropped_when_the_room_changes():
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** `on_room_frame` stores `(frame, self._clock())` in both the pending and latest maps; `_broadcast_room_frame` passes `t`. The per-client snapshot frames go after the existing snapshot and `bits_listed` sends.
- [ ] **Step 4: Run the full suite and `node --test tests/js/*.test.js`; expect PASS** (the browser ignores the new key).
- [ ] **Step 5: Commit** `feat(console): stamp room frames with Control's clock and replay them to new clients`

### Task 6: An engine `on_gesture` hook and a droppable `gesture` event

**Files:**
- Modify: `control/functions.py` (add `GestureSeen` next to `FunctionFired`), `control/engine.py` (`data`, `_solo_gesture`)
- Modify: `console/protocol.py` (add `gesture_event`), `console/agent.py` (`on_gesture`, drain in `poll`)
- Test: `tests/test_engine_data.py`, `tests/test_console_agent.py`, `tests/test_console_protocol.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class GestureSeen: dev: str; verb: str; received: float; at: float; refused: str | None`.
- Produces: observer hook `on_gesture(record: GestureSeen)`, notified once per `data()` call that gets past the "no Bit running" and "device not registered" checks, after routing, with the routing's return value as `refused`.
- Produces: `gesture_event(record) -> {"event": "gesture", "dev", "verb", "t": received, "at", "refused"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_on_gesture_fires_for_a_handled_tap():        # refused is None, at == received + cue_horizon (fixed clock)
def test_on_gesture_carries_a_handler_refusal():      # refuse_next = "nope" -> refused == "nope"
def test_on_gesture_fires_for_an_unknown_verb():      # refused == "unknown verb 'zap'"
def test_on_gesture_fires_for_a_solo_role_gesture():
def test_on_gesture_does_not_fire_before_registration():  # "device not registered" -> no call
def test_rejected_stamps_counts_once_per_gesture():   # a stamp >5 s ahead bumps rejected_stamps by exactly 1
def test_console_broadcasts_buffered_gestures_on_poll():  # agent.on_gesture(x); poll -> one "gesture" broadcast
def test_console_gesture_buffer_is_bounded():         # 1000 on_gesture calls before a poll -> at most 256 broadcast, newest kept
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** Compute `at = self._origin(gesture_time) + self._horizon` once in `data()` after the registration check and pass it into the routing (`_solo_gesture` takes `at` instead of `gesture_time`), so `_origin` is not called twice and `rejected_stamps` is not double-counted. Wrap the existing routing in `_route_gesture(dev, verb, args, role_name, at) -> str | None`; `data()` notifies `on_gesture` with its result and returns it. The agent's `on_gesture` only appends to `collections.deque(maxlen=256)` (thread-safe append; it may be called from whichever thread calls `data()`); `poll` pops everything and broadcasts.
- [ ] **Step 4: Run the full suite; expect PASS.**
- [ ] **Step 5: Commit** `feat(engine): an on_gesture hook, relayed to the Console as a droppable event`

### Task 7: Vendor a pinned three.js

**Files:**
- Create: `console/static/vendor/three/build/three.module.js`, `console/static/vendor/three/addons/loaders/GLTFLoader.js`, `console/static/vendor/three/addons/controls/OrbitControls.js`, `console/static/vendor/three/addons/utils/BufferGeometryUtils.js`, `console/static/vendor/three/LICENSE`, `console/static/vendor/three/VERSION` (one line: the version and source URL)
- Modify: `console/static/index.html` (an import map), `tests/test_console_static.py`
- Test: `tests/test_console_static.py`

**Interfaces:**
- Produces: browser imports `import * as THREE from "three"` and `import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js"`, resolved by `<script type="importmap">{"imports": {"three": "./vendor/three/build/three.module.js", "three/addons/": "./vendor/three/addons/"}}</script>` in `index.html` (and Task 10's page).

- [ ] **Step 1: Ask Chris before downloading.** Pin **three 0.169.0** (the last release whose `three.module.js` is a single file with no `three.core.js` split). Source: `https://cdn.jsdelivr.net/npm/three@0.169.0/` (`build/three.module.js`, `examples/jsm/...` for the three addons, `LICENSE`). State the four file names and sizes when asking; download only on his yes.
- [ ] **Step 2: Write the failing tests**

```python
def test_vendored_three_is_pinned_and_licensed():
    v = STATIC / "vendor" / "three"
    assert (v / "LICENSE").read_text().startswith("The MIT License")
    assert (v / "VERSION").read_text().startswith("0.169.0")
def test_index_loads_one_module_entry_and_one_import_map():
    # replaces test_index_loads_exactly_one_module_entry: exactly two <script> tags,
    # one type="importmap" naming ./vendor/three/build/three.module.js, one module src="shell.js"
def test_vendored_addons_import_three_only_by_bare_specifier_or_relative_path():
    # no "http" import in any vendor .js file
```
- [ ] **Step 3: Run; expect FAIL.** `.venv/bin/python -m pytest tests/test_console_static.py -v`
- [ ] **Step 4: Add the files and the import map.**
- [ ] **Step 5: Run the full suite; expect PASS.** Also confirm in a browser that `http://127.0.0.1:8772/vendor/three/build/three.module.js` returns 200 under a running Console.
- [ ] **Step 6: Commit** `chore(console): vendor three.js 0.169.0 with an import map`

### Task 8: LED trace and inspector model (pure JS)

**Files:**
- Create: `console/static/led_trace.js`, `console/static/led_inspect.js`
- Test: `tests/js/led_trace.test.js`, `tests/js/led_inspect.test.js`

**Interfaces:**
- Consumes: `decodeRgbw` is NOT used for the inspector's numbers: it shows the four wire channels.
- Produces (`led_trace.js`): `export class FixtureTrace { constructor(pixelCount, windowS = 10); pushFrame(t, channels /* RGBW ints, 4 per pixel */); pushGesture({t, at, dev, verb, refused}); samples(index, now) /* [{t, r, g, b, w}] step-held, oldest first, the first one carried in from before the window */; gestures(now) /* within the window */ }`. Only changes are pushed by the server, so `samples` holds each value until the next.
- Produces (`led_inspect.js`): `export function ledAddress(layout, index) -> {fixture, index, roomZones: string[], modelZone: string|null}`; `export function rgbwAt(channels, index) -> {r, g, b, w}`; `export function gestureLabel(g) -> string` exactly `"tap ie1"` or `"tap ie1 (refused: <reason>)"`, with `count` not shown.

- [ ] **Step 1: Write the failing tests**

```js
test("samples are step-held and windowed to 10 s", () => {
  const tr = new FixtureTrace(2);
  tr.pushFrame(0, [0,0,0,0, 1,2,3,4]);
  tr.pushFrame(4, [0,0,0,0, 9,9,9,9]);
  const s = tr.samples(1, 12);
  assert.deepStrictEqual(s.map(x => [x.t, x.r]), [[2, 1], [4, 9]]); // value at window start carried in
});
test("gestures outside the window are dropped", ...);
test("ledAddress lists room zones and the model zone", ...);
test("rgbwAt reads four channels per pixel", ...);
test("gestureLabel marks refusals", ...);
```
- [ ] **Step 2: Run; expect FAIL.** `node --test tests/js/led_trace.test.js tests/js/led_inspect.test.js`
- [ ] **Step 3: Implement.** Keep a bounded per-fixture frame history (drop frames older than the window, keeping the newest older one for the carry-in).
- [ ] **Step 4: Run all JS tests; expect PASS.** `node --test tests/js/*.test.js`
- [ ] **Step 5: Commit** `feat(console): an LED trace buffer and inspector model`

### Task 9: 3D tiles, the inspector panel and the ↗ change in the Room card

**Files:**
- Create: `console/static/view3d.js`, `console/static/inspector.js`
- Modify: `console/static/surface.js`, `console/static/terrarium.css`, `tests/test_console_static.py` (`MODULES`)
- Test: `tests/js/surface_3d.test.js`

**Interfaces:**
- Consumes: Task 2's `fixture.model`, Task 4's routes, Task 5's `t` and snapshot, Task 6's `gesture` event, Task 7's imports, Task 8's modules.
- Produces (`view3d.js`): `export async function createFixtureView(container, {fixture, sha256, onPick}) -> {paint(channels), select(index|null), dispose()}`. Fetches `/fixture-layout/<name>.json?sha=` and `/fixture-model/<name>.glb?sha=`; draws the mesh slightly transparent and each LED as a point sprite at `(x_mm, y_mm, z_mm)` (mm to scene units 1:1, camera framed from the bounding box), depth test off for the points, sized small/medium/large = 6/9/12 px; unlit LEDs (`r+g+b+w < 12`, the strip's threshold) a dim outline; the selected LED ringed; `OrbitControls` for orbit and zoom. Rejects with an `Error` whose message is the tile's fallback reason. A browser without WebGL2 rejects with `"this browser has no WebGL2"`.
- Produces (`inspector.js`): `export function mountInspector(el) -> {show({address, rgbw, samples, gestures}), clear()}`, drawing the 10 s trace on a canvas (four lines R, G, B, W; dashed vertical gesture markers; refused ones in the terracotta token) with the caption `"last 10 s · 10 Hz"`.
- Produces (`surface.js`): `export function _setViewFactory(fn)` for tests (defaults to `createFixtureView`).

- [ ] **Step 1: Write the failing tests** (`tests/js/surface_3d.test.js`, using `_dom_stub.js`, `FakeSocket`, and a fake view factory that records calls)

```js
test("a fixture with model.status ok gets a 3D tile and keeps its strip row", ...);
test("a fixture with model.status none shows only the strip", ...);
test("an over-budget fixture shows the strip and its reason", ...);
test("a factory rejection falls back to the strip with the error message", ...);
test("tile_survives_room_changed_with_same_model", ...);   // second room_changed, same sha: factory called once
test("a changed model sha rebuilds the tile", ...);
test("room_frame paints the fixture's view with its channels", ...);
test("picking an LED shows its address and RGBW in the inspector", ...);
test("a gesture event appears in the selected LED's trace", ...);
test("artnet_fixture_gets_3d_popout", ...);                 // ↗ href "fixture3d.html?fixture=bars"
test("the bound-device chip links to the device canvas", ...); // chip is <a href=url target=_blank rel=noopener>
```
- [ ] **Step 2: Run; expect FAIL.** `node --test tests/js/surface_3d.test.js`
- [ ] **Step 3: Implement.** Tiles sit in a row above the existing per-fixture strips inside the Room card, with one inspector panel to their right (spec mockup "layout A"); the strip rows stay as they are. Keep surface.js's rule-1 discipline: tiles are keyed by `name + sha256` and never rebuilt by a `room_changed` that leaves both unchanged. The view is created after first paint (strip first, mesh when ready). Add `view3d.js` and `inspector.js` to `MODULES` in `tests/test_console_static.py`.
- [ ] **Step 4: Run all JS and Python tests; expect PASS.**
- [ ] **Step 5: Commit** `feat(console): 3D fixture tiles, an LED inspector and a 3D bump-out link`

### Task 10: The full-window bump-out page

**Files:**
- Create: `console/static/fixture3d.html`, `console/static/fixture3d.js`
- Test: `tests/js/fixture3d_page.test.js`, `tests/test_console_static.py`

**Interfaces:**
- Consumes: Task 9's `createFixtureView`, `mountInspector`, Task 8's modules, `wire.js`.
- Produces: `fixture3d.html?fixture=<name>`: the import map, one module entry `fixture3d.js`, a full-window view and the inspector beside it. It reads `fixture` from the query, opens the websocket through `wire.js`, takes the fixture's `model` from the `snapshot` room payload, and listens to `room_frame` (its fixture only), `gesture` and `room_changed`.

- [ ] **Step 1: Write the failing tests**

```js
test("the page builds a view for the fixture named in the query", ...);
test("frames for other fixtures are ignored", ...);
test("a fixture missing from the Room shows 'tower is not in the loaded Room'", ...);
test("a model sha change rebuilds the view", ...);
```
and in Python: `test_fixture3d_page_has_one_import_map_and_one_module_entry`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run all tests; expect PASS.**
- [ ] **Step 5: Commit** `feat(console): a full-window 3D bump-out per fixture`

### Task 11: Live check, docs, and parking PR B

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (Console section: the 3D tiles, inspector, bump-out, routes, the `gesture` and `t` events, the vendor map; *Not yet built*: cue attribution, room placement, phone-sim 3D)
- Modify (mm-tuneshroom, its own small PR): `CLAUDE.md`'s "3D model support" paragraph, saying PR B is parked in favour of the mm-terrarium Console view, with this spec's path

- [ ] **Step 1: Live check on MacBookPro.** Run `./smoke-test.sh --bit DeuxAraDropBit --handshake-devices 1 --console-port 8772`, open `http://127.0.0.1:8772/` in the browser pane, and confirm each "Done means" item of spec section 10: the Tower in 3D with 14 LEDs at their model positions, in step with the strip; a tap turns the right third of the mast green; clicking an LED shows fixture, pixel, zones, RGBW and a 10 s trace with the tap marked; ↗ opens the full-window view with a working inspector. Then `./terrarium.sh --room DEMO` and confirm `array` shows only its strip. Screenshot each.
- [ ] **Step 2: Update the deep-dive** through the `mm-deepdive-sync` skill.
- [ ] **Step 3: Run both suites one last time; expect PASS.**
- [ ] **Step 4: Commit** `docs: the Console 3D fixture view`
- [ ] **Step 5: In mm-tuneshroom, on a fresh branch from `origin/main`, update `CLAUDE.md` and open a PR** (after checking main's tip for a concurrent change).
