# Console fixture targets and fixture mute state Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Console operator target any declared Room fixture by name, bound or not, from the SURFACE/Diagnostics pickers, and see each fixture's mute state on the Room panel.

**Architecture:** The engine already addresses fixtures as `@fixture:<name>` tokens. The server adds a `muted` flag to each `room.fixtures[]` row and a token key per fixture to `surface_instruments`; `_resolve_target` resolves a SURFACE token through `_resolve_devs` and refuses unknown tokens. The Console's `functions.js` builds fixture picker rows from the Room payload (signature-gated so controller ticks never refill a picker), and `surface.js` toggles a per-fixture "Muted" chip in place.

**Tech Stack:** Python 3 (pytest), plain ES modules in `console/static/` tested under node via `tests/js/*.test.js` (run by `tests/test_console_js.py`, DOM stub in `tests/js/_dom_stub.js`).

**Spec:** `docs/superpowers/specs/2026-09-25-console-fixture-targets-design.md`

## Global Constraints

- Read `docs/MM_TERRARIUM.md` before any edit (repo hook enforces it).
- `control/cues.py`'s `fixture_dev(name)` / `fixture_name(dev)` are the ONLY spellers of the `@fixture:` prefix in Python. In JS, the prefix appears once, as a named constant in `functions.js`.
- `control/room_view.py` stays engine-free: no imports from `control.engine`.
- `GameServer.fire_function` never raises; a refusal is a returned string.
- Rendering discipline: no Console DOM node carrying operator state (a `<select>`, an armed confirm-tap button) may be rebuilt by an event that did not change what it renders.
- The global `[hidden] { display: none !important; }` guard in `terrarium.css` stays; toggling visibility uses the `hidden` property.
- DEVICE-target pickers ("the firing device") never offer a fixture.
- Wire changes are additive: `room.fixtures[i].muted` (bool), `surface_instruments["@fixture:<name>"]`. JS treats a missing `muted` as false.
- No em dashes in any prose written (docs, comments, commit messages).
- Suite: `.venv/bin/python -m pytest tests -q`. JS only: `.venv/bin/python -m pytest tests/test_console_js.py -q`.

---

### Task 1: Engine resolves and validates SURFACE fixture tokens

**Files:**
- Modify: `control/engine.py` (`_resolve_target`, around line 846; `fire_function`, the target checks around line 970)
- Test: `tests/test_engine_functions.py` (append)

**Interfaces:**
- Consumes: `fixture_name`, `fixture_dev` from `control/cues.py` (already imported in `control/engine.py` line 16); `GameServer._resolve_devs(dev) -> list[str]`.
- Produces: `fire_function(name, fired_by=..., dev="@fixture:<name>")` fires at the bound dev when bound, else the token; returns `"no fixture '<name>' in Room '<room>'"` for an undeclared name and `"no Room loaded for fixture '<name>'"` with no Room. `FunctionFired.devs` holds the concrete target.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_engine_functions.py`; `_running`, `Recorder`, `ScriptBit` (which declares SURFACE `spot`), `fixture_dev` already exist in that file)

```python
def test_a_surface_fire_at_a_bound_fixture_token_lands_on_its_dev():
    gs, light, _ = _running(bound={"main": "sim-room-main"})
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_function("spot", fired_by="admin-manual",
                            dev=fixture_dev("main")) is None
    assert [c[0] for c in light] == ["sim-room-main"]
    assert observer.fired[0].devs == ("sim-room-main",)


def test_a_surface_fire_at_an_unbound_fixture_token_lands_on_the_token():
    gs, light, _ = _running(bound={"main": "sim-room-main"})
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_function("spot", fired_by="admin-manual",
                            dev=fixture_dev("accent")) is None
    assert [c[0] for c in light] == [fixture_dev("accent")]
    assert observer.fired[0].devs == (fixture_dev("accent"),)


def test_stop_at_an_unbound_fixture_token_latches_its_mute_and_a_fire_clears_it():
    gs, _, _ = _running(bound={"main": "sim-room-main"})
    assert gs.fire_function("stop", fired_by="admin-manual",
                            dev=fixture_dev("accent")) is None
    assert gs.is_muted(fixture_dev("accent"))
    assert not gs.is_muted(fixture_dev("main"))
    assert gs.fire_function("spot", fired_by="admin-manual",
                            dev=fixture_dev("accent")) is None
    assert not gs.is_muted(fixture_dev("accent"))


def test_a_surface_fire_at_an_undeclared_fixture_is_refused():
    gs, light, _ = _running()
    assert gs.fire_function("spot", fired_by="admin-manual",
                            dev=fixture_dev("ghost")) == \
        "no fixture 'ghost' in Room 'TEST'"
    assert light == []


def test_a_surface_fire_at_a_fixture_with_no_room_is_refused():
    gs, light, _ = _running()
    gs.room = None
    assert gs.fire_function("stop", fired_by="admin-manual",
                            dev=fixture_dev("accent")) == \
        "no Room loaded for fixture 'accent'"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_functions.py -q -k "fixture_token or undeclared_fixture or fixture_with_no_room"`
Expected: the bound-token test FAILS on `record.devs` (it reports `('@fixture:main',)`; the light cue itself already reaches `sim-room-main` via `_dispatch_cues`); the two refusal tests FAIL (return None). The unbound and stop tests may already pass; that is fine, they pin behavior.

- [ ] **Step 3: Implement**

In `_resolve_target`, replace:

```python
        if target is FunctionTarget.SURFACE and dev not in (ROOM, ALL):
            return [dev] if dev else []
```

with:

```python
        if target is FunctionTarget.SURFACE and dev not in (ROOM, ALL):
            # An operator-picked fixture (@fixture:<name>) lands where a cue
            # for that fixture would: its bound dev, else the token itself.
            if fixture_name(dev) is not None:
                return self._resolve_devs(dev)
            return [dev] if dev else []
```

In `fire_function`, directly after the existing block that returns `"... targets a surface; no surface given"` (still inside the `try`), add:

```python
            if target is FunctionTarget.SURFACE:
                fname = fixture_name(dev)
                if fname is not None:
                    if self.room is None:
                        return f"no Room loaded for fixture {fname!r}"
                    if all(f.name != fname
                           for f in self.room.profile.fixtures):
                        return (f"no fixture {fname!r} in Room "
                                f"{self.room.name!r}")
```

Also extend the `_resolve_target` docstring with one sentence: "A SURFACE `@fixture:<name>` dev resolves through `_resolve_devs` (bound dev, else the token)."

- [ ] **Step 4: Run to verify they pass, plus the engine suites**

Run: `.venv/bin/python -m pytest tests/test_engine_functions.py tests/test_engine.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add control/engine.py tests/test_engine_functions.py
git commit -m "feat(engine): resolve and validate SURFACE fires at @fixture: tokens"
```

---

### Task 2: Room payload carries fixture mute state; surface_instruments keys every fixture

**Files:**
- Modify: `control/room_view.py` (`fixtures_view`, `room_view`)
- Modify: `console/agent.py` (`_current_room` around line 724, `_current_surface_instruments` around line 817, imports)
- Test: `tests/test_room_view.py`, `tests/test_console_agent.py` (append)

**Interfaces:**
- Consumes: `GameServer.is_muted(dev) -> bool`, `GameServer._mute_key(dev) -> str`, `fixture_dev(name) -> str`.
- Produces: `fixtures_view(profile, room, canvas_urls=None, muted=None)` and `room_view(room, profile, role, controllers, canvas_urls=None, muted=None)`, `muted` an iterable of fixture NAMES; each fixture row gains `"muted": bool`. `surface_instruments` gains `"@fixture:<name>" -> instrument name` for every declared fixture of the loaded Room. Tasks 3 and 4 read these on the wire.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_room_view.py`:

```python
def test_fixtures_default_to_unmuted():
    assert [f["muted"] for f in _view()["fixtures"]] == [False, False]


def test_fixtures_carry_muted_only_for_named_fixtures():
    view = room_view(_room(), TEST_PROFILE, _role(), {}, muted={"accent"})
    assert [f["muted"] for f in view["fixtures"]] == [False, True]
```

Append to `tests/test_console_agent.py` (add `from control.cues import fixture_dev` to its imports; `_room_console` binds `main` to `sim-room-main` and leaves `accent` unbound; both fixtures use `GENERIC_SURFACE`, named `generic_surface`):

```python
def test_room_fixtures_report_mute_state_for_an_unbound_fixture():
    gs, srv, agent = _room_console()
    gs.muted.add(fixture_dev("accent"))
    by_name = {f["name"]: f for f in agent.snapshot()["room"]["fixtures"]}
    assert by_name["accent"]["muted"] is True
    assert by_name["main"]["muted"] is False


def test_room_fixtures_report_mute_state_for_a_bound_fixture():
    gs, srv, agent = _room_console()
    gs.muted.add(gs._mute_key("sim-room-main"))
    by_name = {f["name"]: f for f in agent.snapshot()["room"]["fixtures"]}
    assert by_name["main"]["muted"] is True


def test_a_fixture_mute_change_broadcasts_room_changed():
    gs, srv, agent = _room_console()
    agent.poll()
    srv.broadcasts.clear()
    gs.muted.add(fixture_dev("accent"))
    agent.poll()
    changed = [b for b in srv.broadcasts if b["event"] == "room_changed"]
    assert len(changed) == 1
    by_name = {f["name"]: f for f in changed[0]["room"]["fixtures"]}
    assert by_name["accent"]["muted"] is True


def test_surface_instruments_key_every_declared_fixture_by_token():
    gs, srv, agent = _room_console()
    si = agent.snapshot()["surface_instruments"]
    assert si[fixture_dev("main")] == "generic_surface"
    assert si[fixture_dev("accent")] == "generic_surface"


def test_surface_instruments_carry_no_fixture_tokens_without_a_room():
    gs, srv, agent = _server_with_agent()
    si = agent.snapshot()["surface_instruments"]
    assert not [k for k in si if k.startswith("@fixture:")]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_view.py tests/test_console_agent.py -q -k "muted or mute_state or mute_change or fixture_by_token or fixture_tokens"`
Expected: FAIL (`KeyError: 'muted'`, `TypeError` on the `muted=` kwarg, `KeyError: '@fixture:main'`). The no-Room test passes already.

- [ ] **Step 3: Implement `control/room_view.py`**

Change the signature and body of `fixtures_view`:

```python
def fixtures_view(profile, room, canvas_urls=None, muted=None) -> list[dict]:
```

Add to its docstring: "`muted` is an iterable of fixture NAMES currently latched mute (the caller resolves them through the engine, keeping this module engine-free); each row's `muted` is whether its name is in it."

Inside, before the loop: `muted_names = set(muted or ())`, and add `"muted": name in muted_names,` to the appended dict (after `"url"`).

Change `room_view`'s signature to `def room_view(room, profile, role, controllers: dict, canvas_urls=None, muted=None) -> dict | None:` and its `"fixtures"` entry to `fixtures_view(profile, room, canvas_urls, muted)`.

- [ ] **Step 4: Implement `console/agent.py`**

Add `from control.cues import fixture_dev` to the imports.

At the end of `_current_room`, replace `return room_view(gs.room, profile, role, controllers, urls)` with:

```python
        # By fixture token, so an unbound fixture's mute shows too
        # (GameServer.is_muted canonicalizes a bound dev to the same key).
        muted = {f.name for f in profile.fixtures
                 if gs.is_muted(fixture_dev(f.name))}
        return room_view(gs.room, profile, role, controllers, urls, muted)
```

In `_current_surface_instruments`, directly after `out: dict[str, str] = {}`, add:

```python
        if gs.room is not None:
            # Every declared fixture by its @fixture: token, bound or not:
            # the Console's pickers offer fixtures by name, so an unbound
            # fixture needs an instrument to check compatibility against.
            for fixture in gs.room.profile.fixtures:
                out[fixture_dev(fixture.name)] = fixture.instrument.name
```

Update that method's docstring first sentence to: "dev -> instrument name, for every declared Room fixture (keyed by its `@fixture:<name>` token), every bound fixture's dev, and every connected device: ..." (keep the rest).

- [ ] **Step 5: Run to verify they pass, plus the neighbours**

Run: `.venv/bin/python -m pytest tests/test_room_view.py tests/test_console_agent.py tests/test_uplink*.py -q`
Expected: all PASS. If a pre-existing test compares a whole fixture row or the whole `surface_instruments` dict for equality, update its expected value to include the new `muted` / token keys (additive only); do not loosen any other assertion.

- [ ] **Step 6: Commit**

```bash
git add control/room_view.py console/agent.py tests/test_room_view.py tests/test_console_agent.py
git commit -m "feat(console): Room payload carries fixture mute state; fixtures keyed in surface_instruments"
```

---

### Task 3: functions.js offers every fixture as a named SURFACE target

**Files:**
- Modify: `console/static/functions.js` (module state near line 18, `fillDevicePicker` ~line 90, `onDevicesChanged` ~line 122, `refreshFireButton` ~line 186, `init` ~line 515)
- Modify: `tests/js/triggers_ux.test.js` (its SURFACE/Diagnostics expectations encode the old bound-dev row)
- Create: `tests/js/fixture_targets.test.js`

**Interfaces:**
- Consumes (wire, from Task 2): `snapshot.room.fixtures[]` and `room_changed.room.fixtures[]` rows `{name, dev|null, muted?}`; `surface_instruments["@fixture:<name>"]`.
- Produces: picker option values `"@fixture:<name>"` sent as `fire_function`'s `dev` (Task 1 resolves them).

- [ ] **Step 1: Write the failing test** `tests/js/fixture_targets.test.js`

```js
"use strict";
// Fixture targets: every declared Room fixture is a SURFACE/Diagnostics
// picker row by NAME (value @fixture:<name>), bound or not, with its mute
// state in the label; devices bound to a fixture are not listed twice;
// DEVICE pickers never offer a fixture; a controllers-only room_changed
// never refills a picker.
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const STROBE = {
  kind: "scripted", name: "strobe", description: "Bit fallback", target: "SURFACE",
  condition: null, script: [],
};
const HOLD = {
  kind: "scripted", name: "hold_flash", description: "Hold", target: "DEVICE",
  condition: { name: "held", description: "held", source: "gesture-verb", verb: "hold" },
  script: [{ offset: 0.0, kind: "play", dev: "@target", name: "hold", params: {} }],
};
const INSTRUMENT_FUNCTIONS = {
  dev_strip_accent: [{ kind: "scripted", name: "strobe", description: "Accent strobe",
                       target: "SURFACE", condition: null, script: [] }],
  dev_strip_main: [], tuneshroom: [],
};
const SURFACE_INSTRUMENTS = {
  "sim-room-main": "dev_strip_main", "@fixture:main": "dev_strip_main",
  "@fixture:accent": "dev_strip_accent", "ie1": "tuneshroom",
};
const BUILTINS = { dev_strip_main: ["flash", "stop"], dev_strip_accent: ["flash", "stop"],
                   tuneshroom: ["flash", "ping", "stop"] };
const room = (fixtures, controllers = {}) => ({
  room_type: "DEMO", capability: { pixel_count: 90, color_order: "GRB", zones: [] },
  fixtures, instruments: [], controllers });
const MAIN = { name: "main", dev: "sim-room-main", zones: [], muted: false };
const ACCENT = { name: "accent", dev: null, zones: [], muted: false };
const DEVICES = [{ dev: "sim-room-main", name: "Room", role: null, fixture: "main" },
                 { dev: "ie1", name: "Shroom", role: "player" }];

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");
  const functions = await import("../../console/static/functions.js");
  const rail = await import("../../console/static/rail.js");
  surface.init(); functions.init(); rail.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });
  const values = (p) => [...p.options].map((o) => o.value);
  const labels = (p) => [...p.options].map((o) => o.textContent);

  send({ event: "snapshot", state: "RUNNING", loaded_bit: "X", roles: [], registration: [],
         devices: DEVICES, bit_status: {}, functions: [STROBE, HOLD],
         room: room([MAIN, ACCENT]), instrument_functions: INSTRUMENT_FUNCTIONS,
         surface_instruments: SURFACE_INSTRUMENTS, builtins: BUILTINS });

  // ---- SURFACE picker: All, fixtures by name in order, then unbound devices
  const surf = byId.get("functionDev_strobe");
  assert.deepStrictEqual(values(surf), ["@all", "@fixture:main", "@fixture:accent", "ie1"]);
  assert.deepStrictEqual(labels(surf),
    ["All", "main (sim-room-main)", "accent (unbound)", "ie1"]);

  // ---- Diagnostics picker: same rows
  assert.deepStrictEqual(values(functions._diagPicker()),
    ["@all", "@fixture:main", "@fixture:accent", "ie1"]);

  // ---- DEVICE picker never offers a fixture
  assert.deepStrictEqual(values(byId.get("functionDev_hold_flash")), ["ie1"]);

  // ---- Fire at an unbound fixture whose instrument has the function
  surf.value = "@fixture:accent";
  surf.onchange();
  const fire = functions._fireBtnFor("strobe");
  assert.strictEqual(fire.disabled, false, fire.title);
  fire.onclick();
  assert.deepStrictEqual(sock.sent.at(-1),
    { command: "fire_function", name: "strobe", dev: "@fixture:accent" });

  // ---- Not available on a fixture whose instrument lacks it: reason names the fixture
  surf.value = "@fixture:main";
  surf.onchange();
  assert.strictEqual(fire.disabled, true);
  assert.ok(/Not available on main/.test(fire.title), fire.title);
  surf.value = "@fixture:accent";
  surf.onchange();

  // ---- a controllers-only room_changed does not refill (option identity survives)
  const optBefore = surf.options[2];
  send({ event: "room_changed", room: room([MAIN, ACCENT], { 74: 12 }) });
  assert.strictEqual(surf.options[2], optBefore, "controllers tick must not refill the picker");
  assert.strictEqual(surf.value, "@fixture:accent");

  // ---- a mute change refills and labels, keeping the selection
  send({ event: "room_changed", room: room([MAIN, { ...ACCENT, muted: true }]) });
  assert.deepStrictEqual(labels(surf),
    ["All", "main (sim-room-main)", "accent (unbound) (muted)", "ie1"]);
  assert.strictEqual(surf.value, "@fixture:accent");

  // ---- a row without `muted` (older server) reads as unmuted
  send({ event: "room_changed", room: room([{ name: "main", dev: "sim-room-main", zones: [] }]) });
  assert.deepStrictEqual(labels(surf), ["All", "main (sim-room-main)", "ie1"]);

  // ---- no Room: no fixture rows
  send({ event: "room_changed", room: null });
  assert.deepStrictEqual(values(surf), ["@all", "ie1"]);

  console.log("fixture_targets: ok");
})().catch((e) => { console.error(e); process.exit(1); });
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_console_js.py -q -k fixture_targets`
Expected: FAIL at the first `deepStrictEqual` (current rows are `["@all", "sim-room-main", "ie1"]`).

- [ ] **Step 3: Implement in `console/static/functions.js`**

Module state, next to `let fnDevices = [];`:

```js
let fnFixtures = [];                 // {name, dev, muted}: declared Room fixtures, profile order
let fixtureSignature = "[]";         // last fnFixtures applied to the pickers (rule 1 gate)
const FIXTURE_PREFIX = "@fixture:";  // the engine's fixture token; the only JS speller
```

Replace the comment above `fillDevicePicker` and the function with:

```js
// Preserves the operator's current selection when the offered list changes
// under it, falling back to the first option only when the previous
// selection is no longer available.
//
// withRoom (SURFACE targets, Diagnostics) offers All, then every declared
// Room fixture BY NAME (value @fixture:<name>, bound or not -- the engine
// resolves the token to the bound dev), then every device not bound to a
// fixture (a bound device is already reachable as its fixture). Without it
// (DEVICE targets: "the firing device") a Room fixture is never offered --
// it used to be, and as the first option it was the default, so a manual
// fire meant for a board landed on a strip. With no device connected the
// picker holds one empty placeholder and the row's Fire button stays
// disabled (refreshFireButton).
function fillDevicePicker(picker, withRoom) {
  if (!picker) return;
  const previous = picker.value;
  clear(picker);
  const values = [];
  const add = (value, text) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    picker.appendChild(option);
    values.push(value);
  };
  if (withRoom) {
    add(ALL_OPTION, "All");
    for (const { name, dev, muted } of fnFixtures) {
      const base = `${name} (${dev || "unbound"})`;
      add(FIXTURE_PREFIX + name, muted ? `${base} (muted)` : base);
    }
  }
  const offered = fnDevices.filter((d) => !d.fixture);
  if (!withRoom && !offered.length) {
    add("", "no device joined");
    picker.value = "";
    return;
  }
  for (const { dev, muted } of offered) {
    add(dev, muted ? `${dev} (muted)` : dev);
  }
  if (values.indexOf(previous) >= 0) picker.value = previous;
  else if (values.length) picker.value = values[0];
}

// A picker value as an operator reads it: a fixture token by its name.
function targetLabel(value) {
  return value && value.startsWith(FIXTURE_PREFIX)
    ? value.slice(FIXTURE_PREFIX.length) : value;
}
```

Replace `onDevicesChanged` with a split into a shared refill plus two feeders:

```js
function refillPickers() {
  if (diagPicker) {
    fillDevicePicker(diagPicker, true);
    refreshDiagButtons();
  }
  for (const [name, info] of currentDeviceTargets) {
    const picker = document.getElementById("functionDev_" + name);
    fillDevicePicker(picker, info.target === "SURFACE");
    refreshCardCompatibility(info.fn, picker, cardByName.get(name));
  }
}

function onDevicesChanged(devices) {
  fnDevices = (devices || []).map((d) => (
    { dev: d.dev, muted: !!d.muted, fixture: d.fixture || null }));
  refillPickers();
}

// Rule 1: room_changed fires on every live controller value, so the
// fixture rows are applied (and pickers refilled) only when a fixture's
// name, binding or mute state actually changed. Returns whether it did.
function applyRoomFixtures(room) {
  const next = ((room && room.fixtures) || []).map((f) => (
    { name: f.name, dev: f.dev || null, muted: !!f.muted }));
  const signature = JSON.stringify(next);
  if (signature === fixtureSignature) return false;
  fixtureSignature = signature;
  fnFixtures = next;
  return true;
}
```

In `refreshFireButton`, change `reason = \`Not available on ${picker.value}\` +` to `reason = \`Not available on ${targetLabel(picker.value)}\` +`.

In `init`, change the snapshot handler to apply the Room first and add a `room_changed` handler:

```js
  wire.on("snapshot", (m) => {
    applyRoomFixtures(m.room);
    onDevicesChanged(m.devices);
    updateInstrumentData(m);
    onFunctionsChanged(m.functions);
  });
  wire.on("room_changed", (m) => {
    if (applyRoomFixtures(m.room)) refillPickers();
  });
```

Also update the `fnDevices` line comment to `// {dev, muted, fixture} from devices_changed; fixture-bound ones are offered as their fixture`.

- [ ] **Step 4: Update `tests/js/triggers_ux.test.js` to the new rows**

Its `ROOM.fixtures[0]` is `main` bound to `sim-room-main`, so the fixture is now offered as `@fixture:main`:
- Both `surface_instruments` literals in that file become `{ "sim-room-main": "dev_strip_main", "@fixture:main": "dev_strip_main" }`.
- `diagPicker.value = "sim-room-main";` becomes `diagPicker.value = "@fixture:main";`.
- `assert.deepStrictEqual([...surfPicker.options].map((o) => o.value), ["@all", "sim-room-main"]);` becomes `["@all", "@fixture:main"]`.

Leave every other assertion as is.

- [ ] **Step 5: Run the whole JS suite**

Run: `.venv/bin/python -m pytest tests/test_console_js.py -q`
Expected: all PASS. If another existing `tests/js/*.test.js` fails because it expected a fixture-bound device's dev (or the old `dev (fixture)` label) in a SURFACE/Diagnostics picker, update that expectation to the fixture row (`@fixture:<name>`, label `<name> (<dev>)`) and add the token to its `surface_instruments`; change nothing else.

- [ ] **Step 6: Commit**

```bash
git add console/static/functions.js tests/js/fixture_targets.test.js tests/js/triggers_ux.test.js
git commit -m "feat(console): SURFACE and Diagnostics pickers offer every Room fixture by name"
```

(Add any other test file Step 5 touched.)

---

### Task 4: Room panel shows each fixture's mute state

**Files:**
- Modify: `console/static/surface.js` (`buildFixture` ~line 277, in-place head rebuild inside `render()` ~line 605-615, fixture drop loop ~line 585, the no-room reset ~line 541, test hooks ~line 116)
- Create: `tests/js/fixture_mute_chip.test.js`

**Interfaces:**
- Consumes (wire, from Task 2): `room.fixtures[i].muted` (missing reads as false).
- Produces: test hook `export function _muteChipFor(name)` returning the fixture's "Muted" chip element.

The chip is appended to the fixture head AFTER the binding controls, so `_bindCtlFor` (`head.children[1]`) keeps pointing at the binding controls.

- [ ] **Step 1: Write the failing test** `tests/js/fixture_mute_chip.test.js`

```js
"use strict";
// The Room panel's per-fixture Muted chip: shown only while that fixture is
// latched mute, toggled in place so a mute change never rebuilds the head
// (an armed Release confirm-tap must survive it).
const assert = require("node:assert");
const { FakeSocket } = require("./_dom_stub.js");

const fixture = (name, dev, muted) => ({
  name, dev, url: null, muted, pixel_count: 10, channel_start: 0, channel_count: 30,
  color_order: "GRB", zones: [],
  instrument: { name: "generic_surface", capabilities: ["light.surface"],
                functions: [], accepted_cues: ["solid", "mute"] } });
const room = (mainMuted, accentMuted) => ({
  room_type: "DEMO", capability: { pixel_count: 20, color_order: "GRB", zones: [] },
  fixtures: [fixture("main", "sim-room-main", mainMuted), fixture("accent", null, accentMuted)],
  instruments: [], controllers: {} });

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");
  surface.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", room: room(false, false) });
  assert.strictEqual(surface._muteChipFor("main").hidden, true);
  assert.strictEqual(surface._muteChipFor("accent").hidden, true);
  assert.strictEqual(surface._muteChipFor("accent").textContent, "Muted");

  // Arm main's Release confirm-tap, then mute main: head untouched, chip shown.
  const bindCtl = surface._bindCtlFor("main");
  const release = bindCtl.children.find((c) => c.tagName === "button");
  release.onclick();
  const armedText = release.textContent;
  send({ event: "room_changed", room: room(true, false) });
  assert.strictEqual(surface._bindCtlFor("main"), bindCtl, "mute must not rebuild the head");
  assert.strictEqual(release.textContent, armedText, "armed Release survives a mute change");
  assert.strictEqual(surface._muteChipFor("main").hidden, false);
  assert.strictEqual(surface._muteChipFor("accent").hidden, true);

  // An unbound fixture's mute shows too; unmuting hides it again.
  send({ event: "room_changed", room: room(false, true) });
  assert.strictEqual(surface._muteChipFor("main").hidden, true);
  assert.strictEqual(surface._muteChipFor("accent").hidden, false);

  // A bind-state change rebuilds the head; the new chip carries current state.
  const accentBound = room(false, true);
  accentBound.fixtures[1].dev = "sim-room-accent";
  send({ event: "room_changed", room: accentBound });
  assert.strictEqual(surface._muteChipFor("accent").hidden, false);

  // A row without `muted` (older server) reads as unmuted.
  const legacy = room(false, false);
  delete legacy.fixtures[0].muted;
  send({ event: "room_changed", room: legacy });
  assert.strictEqual(surface._muteChipFor("main").hidden, true);

  console.log("fixture_mute_chip: ok");
})().catch((e) => { console.error(e); process.exit(1); });
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_console_js.py -q -k fixture_mute_chip`
Expected: FAIL (`surface._muteChipFor is not a function`).

- [ ] **Step 3: Implement in `console/static/surface.js`**

Module state next to `fixtureElByName`:

```js
const muteChipByName = new Map();    // fixture name -> its "Muted" chip, toggled in place (rule 1)
```

Test hook after `_bindCtlFor`:

```js
// The fixture head's "Muted" chip, so tests can assert it toggles in place.
export function _muteChipFor(name) {
  return muteChipByName.get(name);
}
```

A head filler used by both head builders (place it just above `buildFixture`):

```js
// Fills a fixture head: name, binding controls, then the Muted chip. The
// chip comes AFTER the binding controls so _bindCtlFor (children[1]) is
// unchanged, and it is shown/hidden in place on every render rather than
// being part of bindStateKey: a mute change must never rebuild the head
// and discard an armed Release confirm-tap.
function fillFixtureHead(head, fixture) {
  head.appendChild(mk("span", "fixname", fixture.name));
  head.appendChild(bindingControls(fixture));
  const chip = mk("span", "chip solid-rose", "Muted");
  chip.hidden = !fixture.muted;
  head.appendChild(chip);
  muteChipByName.set(fixture.name, chip);
}
```

In `buildFixture`, replace

```js
  head.appendChild(mk("span", "fixname", fixture.name));
  head.appendChild(bindingControls(fixture));
```

with `fillFixtureHead(head, fixture);`.

In `render()`'s unchanged-fixture branch, replace

```js
            clear(oldHead);
            oldHead.appendChild(mk("span", "fixname", fixture.name));
            oldHead.appendChild(bindingControls(fixture));
```

with

```js
            clear(oldHead);
            fillFixtureHead(oldHead, fixture);
```

and, still inside `if (existing) {`, after the `bindStateByName` block, add:

```js
        const muteChip = muteChipByName.get(fixture.name);
        if (muteChip) muteChip.hidden = !fixture.muted;
```

In the "Drop fixtures no longer present" loop, add `muteChipByName.delete(oldName);` next to `fixtureElByName.delete(oldName);`. In the `if (!currentRoom)` reset block, add `muteChipByName.clear();` next to `armedFixtures.clear();`.

- [ ] **Step 4: Run the JS suite and the static-CSS guard**

Run: `.venv/bin/python -m pytest tests/test_console_js.py tests/test_console_static.py -q`
Expected: all PASS (including `surface_panel.test.js`'s `_bindCtlFor` identity assertions).

- [ ] **Step 5: Commit**

```bash
git add console/static/surface.js tests/js/fixture_mute_chip.test.js
git commit -m "feat(console): Room panel shows each fixture's mute state"
```

---

### Task 5: Deep-dive update and full verification

**Files:**
- Modify: `docs/MM_TERRARIUM.md`

**Interfaces:**
- Consumes: Tasks 1-4 merged on the branch.
- Produces: documentation only.

- [ ] **Step 1: Run the full suite and record the count**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass (baseline before this branch: 2662 passed, 1 skipped, plus whatever PR #141 added). Record the exact `N passed, M skipped` line.

Run: `.venv/bin/python -m tools.render_diagrams --check`
Expected: diagrams current.

- [ ] **Step 2: Close the prerequisite bullet**

In `docs/MM_TERRARIUM.md`, section `### \`devicelink/artnet_sink.py\`, \`[[artnet]]\`, routing by fixture name, native RGBW (2026-09-23)`, replace the bullet beginning `- **Bring-up prerequisite: the Console cannot target or show the mute state` (through `...manage individual fixtures by name.`) with:

```markdown
- **(Closed 2026-09-25) Bring-up prerequisite: the Console could not target
  or show the mute state of an unbound fixture.** See *Console fixture
  targets and fixture mute state (2026-09-25)* below.
```

- [ ] **Step 3: Add the slice entry**

Directly after that section's `**Test baseline for this slice:**` paragraph (before `## Boundary rules`), add:

```markdown
### `console/static/functions.js`, `surface.js`, `control/room_view.py` -- Console fixture targets and fixture mute state (2026-09-25)
Closes the Console bring-up prerequisite above. Design:
[`.../2026-09-25-console-fixture-targets-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-09-25-console-fixture-targets-design.md).

- **Every declared fixture is a named picker target, bound or not.** SURFACE
  and Diagnostics pickers list All, then each fixture in profile order
  (value `@fixture:<name>`, label `<name> (<dev>)` or `<name> (unbound)`,
  plus ` (muted)`), then only devices NOT bound to a fixture: a bound
  fixture's device is reached as its fixture, never as a second row.
  DEVICE pickers are unchanged and still never offer a fixture.
- **Fixture rows come from the Room payload, signature-gated.**
  `functions.js` reads `room.fixtures[]` off `snapshot`/`room_changed` and
  refills pickers only when a fixture's `(name, dev, muted)` changes;
  `room_changed` fires on every live controller value, and an unconditional
  refill would close an open `<select>` under the operator.
- **Engine.** `_resolve_target` resolves a SURFACE `@fixture:` dev through
  `_resolve_devs`, so a fire at a bound fixture lands on (and
  `FunctionFired.devs` reports) its dev; `fire_function` refuses a token
  naming no declared fixture (`no fixture '<name>' in Room '<room>'`) or
  any token with no Room loaded.
- **Wire, additive.** `room.fixtures[i].muted` (`gs.is_muted(fixture_dev(
  name))`, computed in `ConsoleAgent._current_room`; `room_view.py` stays
  engine-free and takes the muted NAMES) and a
  `surface_instruments["@fixture:<name>"]` key per declared fixture, so
  compatibility and Diagnostics buttons work for an unbound fixture.
- **Room panel.** Each fixture head carries a `Muted` chip after its
  binding controls, toggled in place via `hidden`, outside `bindStateKey`:
  a mute change never rebuilds the head, so an armed Release confirm-tap
  survives it (pinned by `tests/js/fixture_mute_chip.test.js`).
- **Not verified live.** Offline suite only; no Console session against a
  real Arco or Art-Net fixture has exercised this yet.

**Test baseline for this slice:** `.venv/bin/python -m pytest tests -q` ->
**<N> passed, <M> skipped**.
```

Replace `<N>`/`<M>` with the numbers recorded in Step 1 (these two are the only values filled in at execution time).

- [ ] **Step 4: Verify docs build check still passes**

Run: `.venv/bin/python -m tools.render_diagrams --check && .venv/bin/python -m pytest tests -q -k "doc or diagram"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): Console fixture targets close the Art-Net bring-up prerequisite"
```
