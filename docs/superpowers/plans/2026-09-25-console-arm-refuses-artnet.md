# Console Refuses to Arm an `[[artnet]]`-Covered Fixture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Console refuses `arm_room` for a fixture that has `[[artnet]]` coverage, never offers Arm for one, and stops leaking a covered fixture's stale binding into `surface_instruments`.

**Architecture:** `console/agent.py` gains one helper, `_artnet_fixtures(room_name)`, wrapping `control/terrarium_config.py`'s `artnet_fixtures(config, room_name)`; the arm branch, `_current_room`, and `_current_surface_instruments` use it. `control/room_view.py` gains an additive `artnet` row flag (names passed in, module stays engine-free). `console/static/surface.js` renders an `Art-Net` chip instead of Arm and rolls back an optimistic Armed on an `arm_room` refusal.

**Tech Stack:** Python 3 stdlib (pytest), plain ES modules tested under node via `tests/js/_dom_stub.js`.

**Spec:** `docs/superpowers/specs/2026-09-25-console-arm-refuses-artnet-design.md`

## Global Constraints

- `control/` stays pure stdlib.
- No em dashes in any code comment, string, or doc written here.
- Match surrounding style: comment density, naming, the existing test helpers.
- Run Python tests with `.venv/bin/python -m pytest ...` from the worktree root (`.venv` is a symlink to `/Users/chris/projects/mm-terrarium/.venv`).
- JS tests run as `node tests/js/<file>.test.js` and are also run by `tests/test_console_js.py` (globs `tests/js/*.test.js`).
- Refusal message format, verbatim: `f"{fixture} is driven by [[artnet]] and never binds a device; arming refused"`.
- Bound fixtures, uncovered fixtures, joined devices, and a box with no `[[artnet]]` must behave exactly as today.

---

### Task 1: Server side (refusal, `artnet` wire flag, stale-binding skip)

**Files:**
- Modify: `console/agent.py` (imports; `_handle_admin_command` ArmRoomCommand branch near line 405; `_current_room` near line 755; `_current_surface_instruments` near line 843; new helper `_artnet_fixtures`)
- Modify: `control/room_view.py` (`fixtures_view` line ~105, `room_view` line ~145 and its `fixtures_view(...)` call line ~192)
- Test: `tests/test_console_agent.py`, `tests/test_room_view.py`

**Interfaces:**
- Produces: `ConsoleAgent._artnet_fixtures(room_name: str) -> frozenset[str]`; `fixtures_view(profile, room, canvas_urls=None, muted=None, artnet=None)`; `room_view(room, profile, role, controllers, canvas_urls=None, muted=None, artnet=None)`; each `room.fixtures[i]` dict gains `"artnet": bool`.

- [ ] **Step 1: Write the failing room_view tests** (append to `tests/test_room_view.py`, next to the `muted` tests, reusing its `_view`, `_room`, `_role`)

```python
def test_fixtures_default_to_not_artnet():
    assert [f["artnet"] for f in _view()["fixtures"]] == [False, False]


def test_fixtures_carry_artnet_only_for_named_fixtures():
    view = room_view(_room(), TEST_PROFILE, _role(), {}, artnet={"accent"})
    assert [f["artnet"] for f in view["fixtures"]] == [False, True]
```

- [ ] **Step 2: Write the failing agent tests** (append to `tests/test_console_agent.py`)

```python
def _artnet_agent():
    """TEST Room with `accent` covered by [[artnet]]: `main` binds its
    simulator, `accent` stays unbound (PR #143's skip)."""
    from tests.test_terrarium import TEST_SPEC, _artnet, _config_with_artnet
    binding = RoomBindingRegistry()
    gs = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding)
    terrarium = make_terrarium(
        _config_with_artnet({"TEST": TEST_SPEC}, _artnet("TEST", "accent")),
        gs=gs, room_binding=binding)
    assert terrarium.load_room("TEST") is None
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, terrarium=terrarium)
    return terrarium, binding, agent


def test_arm_room_refuses_an_artnet_covered_fixture():
    _terrarium, binding, agent = _artnet_agent()
    error = agent._handle_command(
        {"command": "arm_room", "room_type": "TEST", "fixture": "accent"})
    assert error == {"event": "error", "command": "arm_room",
                     "message": "accent is driven by [[artnet]] and never "
                                "binds a device; arming refused"}
    assert binding.armed_fixture("TEST") is None


def test_arm_room_still_arms_an_uncovered_fixture_beside_a_covered_one():
    _terrarium, binding, agent = _artnet_agent()
    binding.release("TEST", "main")
    error = agent._handle_command(
        {"command": "arm_room", "room_type": "TEST", "fixture": "main"})
    assert error is None
    assert binding.armed_fixture("TEST") == "main"


def test_room_payload_flags_the_artnet_covered_fixture():
    _terrarium, _binding, agent = _artnet_agent()
    fixtures = agent.snapshot()["room"]["fixtures"]
    assert {f["name"]: f["artnet"] for f in fixtures} == \
        {"main": False, "accent": True}


def test_a_covered_fixtures_stale_binding_is_not_a_surface_instrument():
    _terrarium, binding, agent = _artnet_agent()
    binding.bind("TEST", "accent", "ie-stale")   # a recorded, never-reconnected binding
    surface = agent._current_surface_instruments()
    assert "ie-stale" not in surface
    assert fixture_dev("accent") in surface
```

If `RoomBindingRegistry` has no `release(room, fixture)` method, read `control/room_binding.py` and use its actual unbind method in the second test; the point is only that `main` being bound does not matter to arming it (arming a bound fixture is allowed today, so the `release` line may be dropped entirely if arming works without it; keep the test's assertion as is).

- [ ] **Step 3: Run the new tests, verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_view.py tests/test_console_agent.py -q -k "artnet or covered"`
Expected: FAIL (KeyError `artnet`, arm not refused, `ie-stale` present).

- [ ] **Step 4: Implement `room_view.py`**

In `fixtures_view`, add the `artnet=None` parameter, document it in the docstring right after the `muted` paragraph:

```
    `artnet` is an iterable of fixture NAMES driven by an [[artnet]] output
    (the caller resolves them from the Terrarium config); each row's
    `artnet` is whether its name is in it. Such a fixture never binds a
    device, so the Room panel offers it no Arm button.
```

then `artnet_names = set(artnet or ())` beside `muted_names`, and add `"artnet": name in artnet_names,` after `"muted"` in the row dict. In `room_view`, add `artnet=None` to the signature and pass it: `fixtures_view(profile, room, canvas_urls, muted, artnet)`.

- [ ] **Step 5: Implement `console/agent.py`**

Import `artnet_fixtures` from `control.terrarium_config` alongside the module's existing top-level imports. Add the helper near `_current_room`:

```python
    def _artnet_fixtures(self, room_name: str) -> frozenset[str]:
        """room_name's fixtures driven by an [[artnet]] output. Such a
        fixture never binds a device (parent spec D2), so it is never armed.
        No Terrarium wired means no config and so no coverage."""
        if self.terrarium is None:
            return frozenset()
        return artnet_fixtures(self.terrarium.config, room_name)
```

Arm branch:

```python
        if isinstance(command, protocol.ArmRoomCommand):
            if command.fixture in self._artnet_fixtures(room_name):
                return protocol.error_event(
                    name, f"{command.fixture} is driven by [[artnet]] and "
                          f"never binds a device; arming refused")
            gs.room_binding.arm(room_name, command.fixture, command.window_seconds)
```

`_current_room`: `return room_view(gs.room, profile, role, controllers, urls, muted, self._artnet_fixtures(gs.room.name))`.

`_current_surface_instruments`: in the `room_binding.bound_device` loop, compute `covered = self._artnet_fixtures(gs.room.name)` before the loop and `continue` for `fixture.name in covered`, with a one-line comment: a covered fixture's recorded binding is never reconnected (load_room skips it), so its dev is stale.

If importing `control.terrarium_config` at module top creates an import cycle, import it inside `_artnet_fixtures` instead (the module already does local imports in `_load_design_catalog`).

- [ ] **Step 6: Run the new tests, verify they pass, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_room_view.py tests/test_console_agent.py -q`
Then: `.venv/bin/python -m pytest tests -q`
Expected: all pass (main's baseline plus the new tests).

- [ ] **Step 7: Commit**

```bash
git add console/agent.py control/room_view.py tests/test_console_agent.py tests/test_room_view.py
git commit -m "fix(console): refuse to arm an [[artnet]]-covered fixture; flag coverage on the wire"
```

---

### Task 2: Room panel (`Art-Net` chip, refusal rollback)

**Files:**
- Modify: `console/static/surface.js` (module state near line 24; `bindingControls` near line 190; `bindStateKey` near line 340; `render()` no-Room reset near line 566; `init()` near line 747)
- Create: `tests/js/fixture_artnet_arm.test.js`

**Interfaces:**
- Consumes: `room.fixtures[i].artnet` (bool, absent on an older server = false) from Task 1; `error` events `{event: "error", command, message}`.
- Produces: nothing new exported (uses existing `_bindCtlFor`).

- [ ] **Step 1: Write the failing JS test** `tests/js/fixture_artnet_arm.test.js`

```javascript
"use strict";
// An [[artnet]]-covered fixture never binds a device: the Room panel shows
// an Art-Net chip and no Arm button. An arm_room refusal rolls back the
// optimistic Armed chip on an uncovered fixture.
const assert = require("node:assert");
const { FakeSocket } = require("./_dom_stub.js");

const fixture = (name, artnet) => ({
  name, dev: null, url: null, muted: false, artnet,
  pixel_count: 10, channel_start: 0, channel_count: 30,
  color_order: "GRB", zones: [],
  instrument: { name: "generic_surface", capabilities: ["light.surface"],
                functions: [], accepted_cues: ["solid", "mute"] } });
const room = () => ({
  room_type: "DEMO", capability: { pixel_count: 20, color_order: "GRB", zones: [] },
  fixtures: [fixture("array", true), fixture("accent", false)],
  instruments: [], controllers: {} });

const texts = (el) => el.children.map((c) => c.textContent);
const buttons = (el) => el.children.filter((c) => c.tagName === "button");

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");
  surface.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", room: room() });

  // Covered: Art-Net chip, no Arm.
  const arrayCtl = surface._bindCtlFor("array");
  assert.ok(texts(arrayCtl).includes("Art-Net"), "covered fixture shows Art-Net");
  assert.strictEqual(buttons(arrayCtl).length, 0, "covered fixture offers no Arm");

  // Uncovered: Not bound + Arm, as today.
  let accentCtl = surface._bindCtlFor("accent");
  assert.ok(texts(accentCtl).includes("Not bound"));
  const arm = buttons(accentCtl).find((b) => b.textContent === "Arm");
  assert.ok(arm, "uncovered fixture still offers Arm");

  // Arm + Confirm shows Armed.
  arm.onclick();
  const formRow = accentCtl.children.find((c) => c.className === "armrow");
  const confirm = formRow.children.find((c) => c.textContent === "Confirm");
  confirm.onclick();
  assert.ok(texts(surface._bindCtlFor("accent")).includes("Armed"));

  // A refusal for another command leaves Armed alone.
  send({ event: "error", command: "fire_function", message: "nope" });
  assert.ok(texts(surface._bindCtlFor("accent")).includes("Armed"));

  // An arm_room refusal rolls it back to Not bound + Arm.
  send({ event: "error", command: "arm_room", message: "refused" });
  accentCtl = surface._bindCtlFor("accent");
  assert.ok(texts(accentCtl).includes("Not bound"), "refusal rolls back Armed");
  assert.ok(buttons(accentCtl).some((b) => b.textContent === "Arm"));

  // A row without `artnet` (older server) still offers Arm.
  const legacy = room();
  delete legacy.fixtures[0].artnet;
  send({ event: "room_changed", room: legacy });
  assert.ok(buttons(surface._bindCtlFor("array")).some((b) => b.textContent === "Arm"));

  console.log("fixture_artnet_arm: ok");
})().catch((e) => { console.error(e); process.exit(1); });
```

The "Armed" chip is built as a span holding a dot span plus a text node, so its `textContent` depends on how `_dom_stub.js` computes `textContent` for mixed children. If `texts(...)` does not include `"Armed"` against the unmodified code path (check by reading `_dom_stub.js`), assert on the chip's class (`chip gold`) instead: `accentCtl.children.some((c) => c.className === "chip gold")`. Likewise adapt `className`/`tagName` lookups to whatever `_dom_stub.js` actually exposes; do not change the stub unless it lacks something genuinely needed.

- [ ] **Step 2: Run it, verify it fails**

Run: `node tests/js/fixture_artnet_arm.test.js`
Expected: FAIL at "covered fixture shows Art-Net".

- [ ] **Step 3: Implement in `surface.js`**

Module state, beside `armedFixtures`:

```javascript
let pendingArm = null;               // fixture last sent arm_room; rolled back on its refusal
```

In `bindingControls`, right after the `if (fixture.dev) { ... }` block and before the `armedFixtures.has` check:

```javascript
  // An [[artnet]]-covered fixture is driven by its Art-Net sink and never
  // binds a device (the server refuses arming it), so there is no Arm.
  if (fixture.artnet) {
    const chip = mk("span", "chip sage", "Art-Net");
    chip.title = "Driven by [[artnet]]; never binds a device";
    wrap.appendChild(chip);
    return wrap;
  }
```

In the Confirm handler, set `pendingArm = fixture.name;` before `wire.send("arm_room", ...)`.

In `bindStateKey`, after the `dev` line: `if (fixture.artnet) return "artnet";`.

In `render()`'s no-Room reset, after `armedFixtures.clear();` add `pendingArm = null;`.

In `init()`, after the existing `wire.on` registrations:

```javascript
  // A refused arm_room must not leave its fixture showing Armed. Not
  // cleared on room_changed: controller values make that event frequent,
  // and clearing there would race the refusal.
  wire.on("error", (m) => {
    if (m.command !== "arm_room" || pendingArm === null) return;
    armedFixtures.delete(pendingArm);
    pendingArm = null;
    render();
  });
```

Also update the `armedFixtures` declaration comment if it no longer reads accurately.

- [ ] **Step 4: Run the new test and all JS tests**

Run: `node tests/js/fixture_artnet_arm.test.js`
Then: `.venv/bin/python -m pytest tests/test_console_js.py tests/test_console_script_isolation.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add console/static/surface.js tests/js/fixture_artnet_arm.test.js
git commit -m "feat(console): Room panel shows Art-Net instead of Arm; roll back a refused arm"
```

---

### Task 3: Deep-dive and baseline

**Files:**
- Modify: `docs/MM_TERRARIUM.md`

- [ ] **Step 1: Run the full suite and diagram check for the baseline**

Run: `.venv/bin/python -m pytest tests -q` and `.venv/bin/python -m tools.render_diagrams --check`
Record the pass/skip counts.

- [ ] **Step 2: Edit the artnet entry** (section headed "`devicelink/artnet_sink.py`, `[[artnet]]`, routing by fixture name, native RGBW (2026-09-23)")

- Replace the "**Still open: the Console's `ArmRoomCommand` ...**" bullet with a closed form: strike the title the same way the first prerequisite is struck (`~~...~~ **Closed 2026-09-25**`), link the spec `docs/superpowers/specs/2026-09-25-console-arm-refuses-artnet-design.md` with the same `https://github.com/Musical-Mycology/mm-terrarium/blob/main/...` URL form used by neighbors, and say in 3-5 lines: `console/agent.py` refuses `arm_room` for a fixture in `artnet_fixtures(config, room)` with the refusal message; `room.fixtures[i].artnet` flags coverage and the Room panel shows an `Art-Net` chip with no Arm; an `arm_room` refusal rolls back the optimistic Armed chip; a covered fixture's stale recorded binding no longer appears in `surface_instruments`.
- Amend the "(Closed 2026-09-25) Bring-up prerequisite: the Console could not target or show the mute state of an unbound fixture." bullet to say prerequisite (2) is now fully closed, arming included.
- Add a line after the existing baselines: `**Test baseline after the 2026-09-25 arm refusal:** .venv/bin/python -m pytest tests -q -> **N passed, M skipped**.`

- [ ] **Step 3: Edit the *Not yet built* bullet "A real-hardware Room backend, for either room."**

Find where that bullet lists the bring-up prerequisites (grep `prerequisite` within it) and mark prerequisite (2) closed 2026-09-25 (targeting and mute display by PR #144, arm refusal by this change). Leave the spec's section 9 hardware checklist as pending.

- [ ] **Step 4: Verify no em dashes were added**

Run: `git diff docs/MM_TERRARIUM.md | grep '^+' | grep -c '—'`
Expected: `0`.

- [ ] **Step 5: Commit**

```bash
git add docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): Console refuses arming an [[artnet]] fixture; bring-up prerequisite (2) closed"
```
