# Console Live-view UX Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Terrarium Console a loading overlay, a nav label that tracks the loaded Room, a cleaner Live Room card (LED rows, then one-row Triggers, then a compact lane table), and a single-row icon action bar in the sidebar.

**Architecture:** Front-end only, in the ES modules under `console/static/`. Every payload needed already arrives on the wire (`snapshot`, `state_changed`, `room_load_progress`, `room_loaded`, `room_load_failed`, `room_unloaded`, `room_changed`, `functions_changed`, `function_fired`). One new module `busy.js` owns the loading overlay; `shell.js`, `surface.js`, `rooms.js`, `functions.js`, `bit.js`, `wire.js`, and `terrarium.css` are modified. Work ships as two PRs: PR A (Tasks 1-3: nav fix and overlay) and PR B (Tasks 4-9: Live view and sidebar relayout).

**Tech Stack:** Plain ES modules, no build step, no npm. Tests are plain node scripts under `tests/js/*.test.js` using `tests/js/_dom_stub.js`, run by `tests/test_console_js.py` through pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-console-live-view-ux-design.md`

## Global Constraints

- No `console/agent.py`, `control/`, or wire-protocol change.
- No build step, no npm, no CDN, no icon font. Icons are inline SVG strings.
- Front-end-wide rule: no high-frequency wire event (`room_changed`, `devices_changed`, `function_fired`, `room_load_progress`) may rebuild a DOM subtree whose declaration has not changed. Keyed lists patch in place.
- `wire.confirmTap` keys armed state off the specific button element; never mint a fresh button on a tick.
- `tests/js/_dom_stub.js` has no `querySelector`, no events, no layout. Cache structural nodes as module state and expose `_xxxFor()` test hooks. `innerHTML` on a leaf node stores a raw string (used for SVG).
- Run the JS suite with **RUN ON: MYCOLOGICAL** `/opt/homebrew/bin/node tests/js/<file>.test.js`, or the whole suite with `.venv/bin/python -m pytest tests/test_console_js.py -v`. `.venv` in a worktree is a symlink to the main clone's venv (see the deep-dive); create it with `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` if missing.
- No em dashes in any written copy or commit message.
- Commit after every task with a conventional prefix (`feat(console):`, `fix(console):`, `test(console):`).

---

## PR A: loading overlay and nav fix

### Task 1: `Room: none` nav tracks `room_loaded` / `room_unloaded`

**Files:**
- Modify: `console/static/shell.js:31-38`
- Test: `tests/js/wire_and_shell.test.js`

**Interfaces:**
- Produces: `shell.paintRoomNav(rooms)` unchanged signature; `shell.js` now keeps a module-level `navRooms` array updated by `snapshot`, `room_loaded`, `room_unloaded`.

- [ ] **Step 1: Write the failing test**

Append inside the async block of `tests/js/wire_and_shell.test.js`, after the existing `shell view switcher: ok` log:

```js
  // Room nav label follows Console-driven loads: only the connect-time
  // snapshot used to repaint it, so a load from this tab left "Room: none"
  // until reload.
  {
    const sock2 = FakeSocket.instances.at(-1);
    const send = (m) => sock2.onmessage({ data: JSON.stringify(m) });
    send({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [], registration: [],
           devices: [], bit_status: {}, room: null, functions: [], terrarium_state: "NO_ROOM",
           rooms: [{ name: "DEMO", description: "", status: null, active: false }] });
    assert.strictEqual(byId.get("navRoom").textContent, "Room: none");
    send({ event: "room_loaded", name: "DEMO" });
    assert.strictEqual(byId.get("navRoom").textContent, "Room: DEMO");
    send({ event: "room_unloaded", name: "DEMO" });
    assert.strictEqual(byId.get("navRoom").textContent, "Room: none");
    console.log("shell room nav follows room_loaded: ok");
  }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/bin/node tests/js/wire_and_shell.test.js`
Expected: AssertionError `'Room: none' !== 'Room: DEMO'`

- [ ] **Step 3: Implement**

In `console/static/shell.js` replace the single `wire.on("snapshot", (m) => paintRoomNav(m.rooms));` line with:

```js
let navRooms = [];  // last-known snapshot.rooms rows, kept current on room events
wire.on("snapshot", (m) => { navRooms = m.rooms || []; paintRoomNav(navRooms); });
wire.on("room_loaded", (m) => {
  navRooms = navRooms.map((r) => Object.assign({}, r, { active: r.name === m.name }));
  if (!navRooms.some((r) => r.name === m.name)) navRooms.push({ name: m.name, active: true });
  paintRoomNav(navRooms);
});
wire.on("room_unloaded", () => {
  navRooms = navRooms.map((r) => Object.assign({}, r, { active: false }));
  paintRoomNav(navRooms);
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/bin/node tests/js/wire_and_shell.test.js`
Expected: all `ok` lines including `shell room nav follows room_loaded: ok`

- [ ] **Step 5: Commit**

```bash
git add console/static/shell.js tests/js/wire_and_shell.test.js
git commit -m "fix(console): Room nav label follows room_loaded and room_unloaded"
```

### Task 2: `busy.js` loading overlay

**Files:**
- Create: `console/static/busy.js`
- Create: `tests/js/busy.test.js`
- Modify: `console/static/shell.js` (import + init)
- Modify: `console/static/terrarium.css` (append `.busy` rules)

**Interfaces:**
- Produces: `busy.init()`, `busy.begin({ title })`, `busy.stage(text)`, `busy.fail(text)`, `busy.end()`, test hook `busy._overlay()` returning the overlay element or `null`.
- Consumes: `wire.on` events `snapshot`, `state_changed`, `room_load_progress`, `room_loaded`, `room_unloaded`, `room_load_failed`, `error`.

- [ ] **Step 1: Write the failing test**

Create `tests/js/busy.test.js`:

```js
"use strict";
// Loading overlay: state-driven (ROOM_LOADING/ROOM_UNLOADING), shows the
// latest room_load_progress stage, closes when the Terrarium settles, and
// stays open with the reason on failure until dismissed.
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

function findByClass(node, cls) {
  if (node.className && node.className.split(" ").includes(cls)) return node;
  for (const c of node.children) {
    const found = findByClass(c, cls);
    if (found) return found;
  }
  return null;
}

(async () => {
  const wire = await import("../../console/static/wire.js");
  const busy = await import("../../console/static/busy.js");
  busy.init();
  wire.connect({ WebSocketImpl: FakeSocket, retryMs: 5 });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });
  const mount = byId.get("overlayMount");

  // settled: nothing shown
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [], registration: [],
         devices: [], bit_status: {}, room: null, functions: [], terrarium_state: "NO_ROOM",
         rooms: [{ name: "DEMO", description: "", status: null, active: false }] });
  assert.strictEqual(busy._overlay(), null);

  // ROOM_LOADING opens it, titled with the room the Console asked for
  wire.send("load_room", { name: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  const overlay = busy._overlay();
  assert.ok(overlay, "overlay opens on ROOM_LOADING");
  assert.ok(mount.innerHTML.includes("Loading Room DEMO"));
  assert.ok(!findByClass(overlay, "btn"), "no Dismiss while in progress");

  // stages accumulate; the latest is marked current
  send({ event: "room_load_progress", stage: "validating" });
  send({ event: "room_load_progress", stage: "spawning arco" });
  assert.strictEqual(busy._overlay(), overlay, "progress never rebuilds the overlay");
  const stages = findByClass(overlay, "stages");
  assert.deepStrictEqual(stages.children.map((c) => c.textContent), ["validating", "spawning arco"]);
  assert.ok(stages.children[0].className.includes("done"));
  assert.ok(stages.children[1].className.includes("current"));

  // room_loaded closes it
  send({ event: "room_loaded", name: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_READY" });
  assert.strictEqual(busy._overlay(), null);

  // a snapshot alone (reconnect mid-load) opens it too
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [], registration: [],
         devices: [], bit_status: {}, room: null, functions: [], terrarium_state: "ROOM_UNLOADING",
         rooms: [{ name: "DEMO", description: "", status: null, active: true }] });
  assert.ok(busy._overlay());
  assert.ok(mount.innerHTML.includes("Unloading Room DEMO"));
  send({ event: "room_unloaded", name: "DEMO" });
  assert.strictEqual(busy._overlay(), null);

  // failure keeps it open with the reason and a Dismiss button
  wire.send("load_room", { name: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  send({ event: "room_load_failed", name: "DEMO", reason: "arco did not start" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "NO_ROOM" });
  const failed = busy._overlay();
  assert.ok(failed, "failure keeps the overlay up despite NO_ROOM");
  assert.ok(mount.innerHTML.includes("arco did not start"));
  const dismiss = findByClass(failed, "btn");
  assert.ok(dismiss && dismiss.textContent === "Dismiss");
  dismiss.onclick();
  assert.strictEqual(busy._overlay(), null);

  // an error on load_bit while up also fails it
  wire.send("load_bit", { name: "MetronomeBit", room: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  send({ event: "error", command: "load_bit", message: "no such bit" });
  assert.ok(mount.innerHTML.includes("no such bit"));
  findByClass(busy._overlay(), "btn").onclick();
  assert.strictEqual(busy._overlay(), null);

  console.log("busy: ok");
})().catch((e) => { console.error(e); process.exit(1); });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/bin/node tests/js/busy.test.js`
Expected: `Error: Cannot find module '../../console/static/busy.js'`

- [ ] **Step 3: Implement `console/static/busy.js`**

```js
// Loading overlay for blocking Terrarium operations. Driven by Terrarium
// state, not by the click: it opens when terrarium_state becomes
// ROOM_LOADING / ROOM_UNLOADING (from snapshot or state_changed), so a load
// started from another tab, or a reconnect mid-load, shows progress too.
// Closes when the state settles; a failure keeps it up with the reason
// and a Dismiss button. Generic surface: begin/stage/fail/end.
import * as wire from "./wire.js";

let overlayEl = null;      // the .overlay node while shown, else null
let stagesEl = null;       // .stages list inside it
let bodyEl = null;         // .busybody (stages or failure text)
let failed = false;        // true after fail(): state settling must not close it
let requestedRoom = null;  // room name this tab last asked to load
let activeRoom = null;     // active room name from snapshot/room_loaded
const ROOM_COMMANDS = new Set(["load_room", "unload_room", "load_bit"]);

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

export function _overlay() {
  return overlayEl;
}

export function begin({ title }) {
  if (overlayEl) { end(); }
  failed = false;
  const mount = document.getElementById("overlayMount");
  overlayEl = mk("div", "overlay open busy");
  const panel = mk("div", "picker busypanel");
  const head = mk("div", "pickhead");
  const dot = mk("span", "dot");
  head.appendChild(dot);
  head.appendChild(mk("h2", null, title));
  panel.appendChild(head);
  bodyEl = mk("div", "busybody");
  stagesEl = mk("ul", "stages mono");
  bodyEl.appendChild(stagesEl);
  panel.appendChild(bodyEl);
  overlayEl.appendChild(panel);
  mount.appendChild(overlayEl);
}

export function stage(text) {
  if (!overlayEl || failed) return;
  for (const li of stagesEl.children) li.className = "done";
  stagesEl.appendChild(mk("li", "current", text));
}

export function fail(text) {
  if (!overlayEl) begin({ title: "Failed" });
  failed = true;
  bodyEl.textContent = "";
  bodyEl.appendChild(mk("p", "inline-err", text));
  const btn = mk("button", "btn outline small", "Dismiss");
  btn.onclick = end;
  bodyEl.appendChild(btn);
}

export function end() {
  if (overlayEl) overlayEl.remove();
  overlayEl = null;
  stagesEl = null;
  bodyEl = null;
  failed = false;
}

function roomName() {
  return activeRoom || requestedRoom || "";
}

function onTerrariumState(state) {
  if (state === "ROOM_LOADING") {
    if (!overlayEl) begin({ title: `Loading Room ${roomName()}`.trim() });
  } else if (state === "ROOM_UNLOADING") {
    if (!overlayEl) begin({ title: `Unloading Room ${roomName()}`.trim() });
  } else if (state === "ROOM_READY" || state === "NO_ROOM") {
    if (overlayEl && !failed) end();
  }
}

export function init() {
  // Remember which room this tab asked for, for the overlay title.
  wire.on("_sent", (m) => {
    if (m.command === "load_room") requestedRoom = m.name;
    if (m.command === "load_bit") requestedRoom = m.room || requestedRoom;
  });
  wire.on("snapshot", (m) => {
    const active = (m.rooms || []).find((r) => r.active);
    activeRoom = active ? active.name : null;
    onTerrariumState(m.terrarium_state);
  });
  wire.on("state_changed", (m) => onTerrariumState(m.terrarium_state));
  wire.on("room_load_progress", (m) => stage(m.stage));
  wire.on("room_loaded", (m) => { activeRoom = m.name; if (!failed) end(); });
  wire.on("room_unloaded", () => { activeRoom = null; if (!failed) end(); });
  wire.on("room_load_failed", (m) => fail(m.reason || "load failed"));
  wire.on("error", (m) => {
    if (overlayEl && ROOM_COMMANDS.has(m.command)) fail(m.message);
  });
}
```

`wire.js` has no `_sent` event yet. Add it: in `wire.send`, after the `ws.send(...)` line, add `dispatch("_sent", Object.assign({ command }, extra));`. Do this edit in `console/static/wire.js` inside `send()`:

```js
export function send(command, extra = {}, sourceEl = null) {
  if (sourceEl) sources.set(command, sourceEl);
  if (ws && ws.readyState === (ws.constructor.OPEN ?? 1)) {
    ws.send(JSON.stringify(Object.assign({ command }, extra)));
  }
  dispatch("_sent", Object.assign({ command }, extra));
}
```

- [ ] **Step 4: Wire into `shell.js`**

Add `import { init as initBusy } from "./busy.js";` after the rooms import, and add `initBusy();` to the init line so it reads:

```js
initBit(); initJoin(); initSurface(); initFunctions(); initRail(); initRooms(); initBusy(); initDesign(); initBench(); initCalibrate(); initForms();
```

- [ ] **Step 5: CSS**

Append to `console/static/terrarium.css` after the `.ovr input:focus` rule:

```css
/* ---------- busy overlay (room load/unload) ---------- */
.overlay.busy { align-items: center; }
.busypanel { max-width: 420px; }
.busypanel .pickhead .dot {
  width: 10px; height: 10px; border-radius: 50%; background: var(--gold);
  animation: pulse 1.6s ease-in-out infinite;
}
.busypanel .pickhead h2 { font-size: 22px; }
.stages { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.stages li { font-size: 12.5px; color: var(--ink-dim); }
.stages li::before { content: "·"; display: inline-block; width: 16px; color: var(--ink-dim); }
.stages li.done::before { content: "✓"; color: var(--sage); }
.stages li.current { color: var(--ink); }
.stages li.current::before { content: "▸"; color: var(--gold); }
.busybody .btn { margin-top: 12px; }
```

- [ ] **Step 6: Run tests**

Run: `/opt/homebrew/bin/node tests/js/busy.test.js && /opt/homebrew/bin/node tests/js/wire_and_shell.test.js && /opt/homebrew/bin/node tests/js/full_stack.test.js`
Expected: `busy: ok` and the others' `ok` lines; no throws.

- [ ] **Step 7: Commit**

```bash
git add console/static/busy.js console/static/wire.js console/static/shell.js console/static/terrarium.css tests/js/busy.test.js
git commit -m "feat(console): state-driven loading overlay for Room load and unload"
```

### Task 3: Browser check and PR A

**Files:** none new.

- [ ] **Step 1: Run the whole offline suite**

Run: `.venv/bin/python -m pytest tests/test_console_js.py tests/test_console_script_isolation.py -v`
Expected: all pass (busy.test.js appears as a new parametrized case).

- [ ] **Step 2: Live check**

**RUN ON: MYCOLOGICAL** from the worktree root, with no other Terrarium running on 8772:

```bash
./terrarium.sh
```

Open `http://127.0.0.1:8772/`, go to the Room view, click Load on DEMO. Confirm: the overlay appears titled `Loading Room DEMO`, stages tick through, it closes on ready, and the sidebar nav reads `Room: DEMO` without a reload. Then Unload and confirm `Unloading Room DEMO` and `Room: none`. Stop the stack with Ctrl-C.

- [ ] **Step 3: Open PR A**

Push the branch and open a PR titled `console: loading overlay and Room nav fix` with a body listing the two changes and the live-check result. Use the `commit-commands:commit-push-pr` skill.

---

## PR B: Live view and sidebar relayout

### Task 4: Fixture chips move from the Live card to the Room view

**Files:**
- Modify: `console/static/surface.js:236-267` (export `instrumentTags`, stop rendering it in `buildFixture`)
- Modify: `console/static/rooms.js:112-121` (render tags under each fixture row)
- Test: `tests/js/surface_panel.test.js`, `tests/js/rooms_panel.test.js`

**Interfaces:**
- Produces: `export function instrumentTags(instrument)` from `surface.js` returning a `.insttags` element.

- [ ] **Step 1: Update the surface test**

In `tests/js/surface_panel.test.js`, replace the block from the comment `// fixture cards show the fixture's own Instrument as a small tag row` through `assert.ok(!card.innerHTML.includes("[object Object]"));` with:

```js
  // the fixture's Instrument declaration chips no longer render on the
  // Live card (they moved to the Room view, rooms.js); the head shows only
  // name, binding, pop-out, Release/Arm.
  assert.ok(!card.innerHTML.includes("insttags"));
  assert.ok(!card.innerHTML.includes("glow (generator)"));
  // the helper is still exported for rooms.js and renders the compact
  // strings, never "[object Object]"
  const tags = surface.instrumentTags(ROOM.fixtures[0].instrument);
  assert.ok(tags.innerHTML.includes("generic_surface"));
  assert.ok(tags.innerHTML.includes("audio.flsyn"));
  assert.ok(tags.innerHTML.includes("glow (generator)"));
  assert.ok(tags.innerHTML.includes("z_delta:2.5"));
  assert.ok(!tags.innerHTML.includes("[object Object]"));
```

Later in the same file, in the block `// instrument fields join the fixture card's declaration signature`, change `assert.ok(card.innerHTML.includes("gesture.tap"));` to `assert.ok(!card.innerHTML.includes("gesture.tap"), "chips are not drawn on the Live card");` (the rebuild is still asserted via `notStrictEqual` on the bind controls).

- [ ] **Step 2: Add the rooms test**

In `tests/js/rooms_panel.test.js`, find the place where the active room's detail is asserted (search for `Fixtures & bindings` or `renderDetail`; if the file has no detail assertion, add this block right after the first `room_loaded` + `state_changed` pair at lines 74-75):

```js
  // the active card's detail renders each fixture's Instrument chips
  send({ event: "room_changed", room: {
    room_type: "TEST", capability: { pixel_count: 60, color_order: "GRB", zones: [] },
    fixtures: [{ name: "main", pixel_count: 60, zones: [], dev: "sim-room-main", url: null,
                 instrument: { name: "generic_surface", capabilities: ["light.surface"],
                               functions: [{ name: "glow", kind: "generator", lane: "cc:74", period: 12 }],
                               accepted_cues: ["midi"], event_triggers: [] } }],
    instruments: [], controllers: {} } });
  const activeCard = rooms._cardFor("TEST");
  assert.ok(activeCard.innerHTML.includes("insttags"), "fixture chips render in the Room view");
  assert.ok(activeCard.innerHTML.includes("generic_surface"));
  assert.ok(activeCard.innerHTML.includes("glow (generator)"));
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `/opt/homebrew/bin/node tests/js/surface_panel.test.js; /opt/homebrew/bin/node tests/js/rooms_panel.test.js`
Expected: surface fails on `!card.innerHTML.includes("insttags")`; rooms fails on `insttags`.

- [ ] **Step 4: Implement**

In `console/static/surface.js`: change `function instrumentTags(instrument) {` to `export function instrumentTags(instrument) {`, and delete the line `if (fixture.instrument) wrap.appendChild(instrumentTags(fixture.instrument));` from `buildFixture`. Leave `fixtureShapeMatches` comparing `instrument` (the spec keeps the rebuild signature unchanged).

In `console/static/rooms.js`: add `import { instrumentTags } from "./surface.js";` after the wire import. In `renderDetail`, inside the `for (const f of fixtures)` loop, after `mount.appendChild(row);` add:

```js
    if (f.instrument) mount.appendChild(instrumentTags(f.instrument));
```

- [ ] **Step 5: Run tests**

Run: `/opt/homebrew/bin/node tests/js/surface_panel.test.js && /opt/homebrew/bin/node tests/js/rooms_panel.test.js && /opt/homebrew/bin/node tests/js/full_stack.test.js`
Expected: all `ok`.

- [ ] **Step 6: Commit**

```bash
git add console/static/surface.js console/static/rooms.js tests/js/surface_panel.test.js tests/js/rooms_panel.test.js
git commit -m "feat(console): move fixture Instrument chips from the Live card to the Room view"
```

### Task 5: "Live values" becomes a lane table, Triggers moves above it, collapsed by default

**Files:**
- Modify: `console/static/surface.js` (replace `renderInstruments`/`updateInstrumentLive`/`instKey` with a lane table; reorder accordions)
- Modify: `console/static/terrarium.css` (append `.lanes` rules)
- Test: `tests/js/surface_panel.test.js`

**Interfaces:**
- Produces: test hooks `surface._laneRowFor(source)` (row element keyed by lane source string such as `"cc:74"`) and `surface._laneTable()`. `buildInstrumentCard` stays exported (used by `bit.js`). `_instCardFor` is deleted.
- Pure helper `export function _laneRowsFor(instruments)` returning `[{ source, cc, readers: [{ kind, instrument, dest }] }]` sorted cc ascending, non-cc sources last in first-seen order.

- [ ] **Step 1: Update the surface test**

In `tests/js/surface_panel.test.js`, after the `_blockRowsFor` assertions, add:

```js
  // pure lane merge: one row per source across every voice, cc ascending,
  // non-cc sources last
  assert.deepStrictEqual(
    surface._laneRowsFor([
      { kind: "light", instrument: "aurora", lanes: [{ source: "cc:74", dest: "hue" }, { source: "cc:11", dest: "level" }] },
      { kind: "light", instrument: "bloom", lanes: [{ source: "note", dest: "trigger" }] },
      { kind: "audio", instrument: "flsyn", lanes: [{ source: "cc:74", dest: "cc:74" }] },
    ]).map((r) => [r.source, r.readers.map((x) => `${x.instrument} ${x.dest}`).join(" · ")]),
    [["cc:11", "aurora level"], ["cc:74", "aurora hue · flsyn cc:74"], ["note", "bloom trigger"]]);
```

Replace the three assertions

```js
  assert.ok(card.innerHTML.includes("aurora"));
  assert.ok(card.innerHTML.includes("= 93"));            // live lane value
```

with

```js
  assert.ok(card.innerHTML.includes("aurora"));
  assert.ok(card.innerHTML.includes("93"), "live lane value shown");
  assert.ok(card.innerHTML.includes("1 light · 1 audio voices"), "summary meta counts voices");
  // accordion order: fixtures, Triggers, then Live values
  const bodyHtml = card.innerHTML;
  assert.ok(bodyHtml.indexOf("Triggers") < bodyHtml.indexOf("Live values"), "Triggers sits above Live values");
```

Replace every `surface._instCardFor("light", "aurora", "primary")` usage with `surface._laneRowFor("cc:74")`, and change the two value assertions on the row from `instCardBefore.innerHTML.includes("= 12")` / `!...("= 93")` to `.includes("12")` / `!.includes("93")`. Rename the variable `instCardBefore` to `laneRowBefore` for clarity. Also add, after the first controllers-only `room_changed` assertions:

```js
  assert.strictEqual(surface._laneTable(), laneTableBefore, "lane table node survives a controllers-only tick");
```

with `const laneTableBefore = surface._laneTable();` captured next to `stripBefore`. And add a rebuild case after the baseline restore `send({ event: "room_changed", room: ROOM });` that follows the popout block:

```js
  // a changed instrument list rebuilds the lane table
  {
    const tableBefore = surface._laneTable();
    send({ event: "room_changed", room: { ...ROOM,
      instruments: [...ROOM.instruments,
        { kind: "light", instrument: "rainbow", target: "primary", params: {}, lanes: [{ source: "cc:21", dest: "level" }] }] } });
    assert.notStrictEqual(surface._laneTable(), tableBefore);
    assert.ok(surface._laneRowFor("cc:21"));
    assert.ok(card.innerHTML.includes("2 light · 1 audio voices"));
    send({ event: "room_changed", room: ROOM });
  }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/bin/node tests/js/surface_panel.test.js`
Expected: TypeError `surface._laneRowsFor is not a function`

- [ ] **Step 3: Implement the lane table in `surface.js`**

Delete `_instCardFor`, `updateInstrumentLive`, `instKey`, `renderInstruments`, and the module state `instGridEl`, `instCardByKey`, `instShapeByKey` (including their lines in `resetStructure`). Keep `buildInstrumentCard`. Add module state:

```js
let laneTableEl = null;              // <table class="lanes"> inside instMountEl
let laneRowBySource = new Map();     // lane source ("cc:74") -> its <tr>
let laneValueBySource = new Map();   // lane source -> its value <td>
let lanesSignature = null;           // JSON of the last-rendered instruments list
```

and reset those four in `resetStructure()`. Add the pure helper and hooks:

```js
// One row per lane source across every voice. cc:<n> sources sort by n
// ascending; anything else (a note lane, "trigger") follows in first-seen
// order with no live value.
export function _laneRowsFor(instruments) {
  const bySource = new Map();
  for (const inst of instruments || []) {
    for (const lane of inst.lanes || []) {
      if (!bySource.has(lane.source)) {
        const cc = lane.source.startsWith("cc:") ? Number(lane.source.slice(3)) : null;
        bySource.set(lane.source, { source: lane.source, cc, readers: [] });
      }
      bySource.get(lane.source).readers.push(
        { kind: inst.kind, instrument: inst.instrument, dest: lane.dest });
    }
  }
  const rows = Array.from(bySource.values());
  rows.sort((a, b) => {
    if (a.cc === null && b.cc === null) return 0;
    if (a.cc === null) return 1;
    if (b.cc === null) return -1;
    return a.cc - b.cc;
  });
  return rows;
}

export function _laneRowFor(source) {
  return laneRowBySource.get(source);
}

export function _laneTable() {
  return laneTableEl;
}

function voiceCounts(instruments) {
  let light = 0;
  let audio = 0;
  for (const inst of instruments || []) {
    if (inst.kind === "audio") audio += 1; else light += 1;
  }
  return `${light} light · ${audio} audio voices`;
}

function valueText(controllers, cc) {
  if (cc === null) return "";
  const v = controllers && controllers[cc];
  return v === undefined ? "—" : String(v);
}

function buildLaneTable(instruments, controllers) {
  const table = mk("table", "lanes mono");
  laneRowBySource = new Map();
  laneValueBySource = new Map();
  for (const row of _laneRowsFor(instruments)) {
    const tr = mk("tr", row.cc === null ? "lane other" : "lane");
    tr.appendChild(mk("td", "src", row.source));
    const val = mk("td", "val", valueText(controllers, row.cc));
    tr.appendChild(val);
    const readers = mk("td", "readers");
    row.readers.forEach((r, i) => {
      if (i > 0) readers.appendChild(mk("span", "dim", " · "));
      readers.appendChild(mk("span", `kind ${r.kind === "audio" ? "audio" : "light"}`,
        r.kind === "audio" ? "Audio" : "Light"));
      readers.appendChild(document.createTextNode(` ${r.instrument} ${r.dest}`));
    });
    tr.appendChild(readers);
    table.appendChild(tr);
    laneRowBySource.set(row.source, tr);
    laneValueBySource.set(row.source, { td: val, cc: row.cc });
  }
  return table;
}

function updateLaneValues(controllers) {
  for (const { td, cc } of laneValueBySource.values()) {
    const text = valueText(controllers, cc);
    if (td.textContent !== text) td.textContent = text;
  }
}

function renderLanes(container, instruments, controllers) {
  const signature = JSON.stringify(instruments || []);
  if (laneTableEl && laneTableEl.parentNode === container && signature === lanesSignature) {
    updateLaneValues(controllers);
    return;
  }
  clear(container);
  lanesSignature = signature;
  if (!instruments || instruments.length === 0) {
    laneTableEl = null;
    laneRowBySource = new Map();
    laneValueBySource = new Map();
    container.appendChild(mk("p", "muted", "No voices declared (no Bit loaded)."));
    return;
  }
  laneTableEl = buildLaneTable(instruments, controllers);
  container.appendChild(laneTableEl);
}
```

In `render()`, replace the two accordion blocks (from `// Instruments accordion (created once, refreshed each render).` to the end of the Functions accordion block) with the Functions accordion first, then Live values, so the order is Triggers then Live values, and make Live values closed by default with a remembered preference:

```js
  // Triggers accordion shell -- created ONCE here; functions.js renders into
  // #functionsMount. Sits directly under the LED rows.
  if (!functionsAccEl) {
    functionsAccEl = document.createElement("details");
    functionsAccEl.className = "acc";
    functionsAccEl.id = "functionsAcc";
    functionsAccEl.open = true;
    const summary = document.createElement("summary");
    summary.appendChild(mk("span", "tri", "▸"));
    summary.appendChild(document.createTextNode("Triggers"));
    functionsAccEl.appendChild(summary);
    const functionsBody = mk("div", "accbody");
    functionsBody.id = "functionsMount";
    functionsAccEl.appendChild(functionsBody);
    body.appendChild(functionsAccEl);
  }

  // Live values accordion (created once, refreshed each render). Closed by
  // default; the operator's last choice is remembered per browser.
  if (!instAccEl) {
    instAccEl = document.createElement("details");
    instAccEl.className = "acc";
    instAccEl.open = readLiveValuesOpen();
    const summary = document.createElement("summary");
    summary.appendChild(mk("span", "tri", "▸"));
    summary.appendChild(document.createTextNode("Live values"));
    instSummaryMetaEl = mk("span", "summeta mono dim", "");
    summary.appendChild(instSummaryMetaEl);
    instAccEl.appendChild(summary);
    instAccEl.addEventListener("toggle", () => writeLiveValuesOpen(instAccEl.open));
    instMountEl = mk("div", "accbody");
    instAccEl.appendChild(instMountEl);
    body.appendChild(instAccEl);
  }
  instSummaryMetaEl.textContent = voiceCounts(room.instruments);
  renderLanes(instMountEl, room.instruments, room.controllers || {});
```

Add the two storage helpers near the top of the file:

```js
const LIVE_VALUES_KEY = "terrarium.liveValuesOpen";

function readLiveValuesOpen() {
  try { return globalThis.localStorage && localStorage.getItem(LIVE_VALUES_KEY) === "1"; }
  catch (e) { return false; }
}

function writeLiveValuesOpen(open) {
  try { if (globalThis.localStorage) localStorage.setItem(LIVE_VALUES_KEY, open ? "1" : "0"); }
  catch (e) { /* private window or blocked storage: keep the default */ }
}
```

Update the file's header comment: replace "and the Instruments/Functions accordions" with "the Triggers accordion shell, and the Live values lane table".

- [ ] **Step 4: CSS**

Append to `console/static/terrarium.css` after the `details.acc > .accbody` rule:

```css
.lanes { border-collapse: collapse; width: 100%; font-size: 12.5px; }
.lanes td { padding: 4px 8px 4px 0; border-bottom: 1px solid var(--hair); vertical-align: top; }
.lanes tr:last-child td { border-bottom: none; }
.lanes .src { color: var(--gold); white-space: nowrap; width: 1%; }
.lanes .val { color: var(--ink); font-variant-numeric: tabular-nums; white-space: nowrap; width: 1%; min-width: 3.5em; }
.lanes .readers { color: var(--ink-var); }
.lanes .readers .kind { font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; padding: 0 5px; border-radius: 4px; margin-right: 2px; }
.lanes tr.other .val { color: var(--ink-dim); }
```

Check whether `.inst h4 .kind` rules already define `.kind.light` / `.kind.audio` colours (grep `\.kind\.` in the css); if they are scoped under `.inst`, add `.lanes .readers .kind.light { color: var(--sage); border: 1px solid var(--sage); } .lanes .readers .kind.audio { color: var(--terra); border: 1px solid var(--terra); }`.

- [ ] **Step 5: Run tests**

Run: `/opt/homebrew/bin/node tests/js/surface_panel.test.js && /opt/homebrew/bin/node tests/js/functions_and_rail.test.js && /opt/homebrew/bin/node tests/js/full_stack.test.js && /opt/homebrew/bin/node tests/js/bit_panel.test.js`
Expected: all `ok` (bit_panel still imports `buildInstrumentCard`).

- [ ] **Step 6: Commit**

```bash
git add console/static/surface.js console/static/terrarium.css tests/js/surface_panel.test.js
git commit -m "feat(console): Live values as a per-cc lane table, Triggers above it, collapsed by default"
```

### Task 6: Triggers as one row per function with an (i) popover

**Files:**
- Modify: `console/static/functions.js:150-153, 240-420`
- Modify: `console/static/terrarium.css` (replace `.fngrid`/`.fn` rules)
- Test: `tests/js/functions_and_rail.test.js`, `tests/js/diagnostics_row.test.js` (only if it asserts `fngrid`; grep first)

**Interfaces:**
- Produces: `functions._cardFor(name)` still returns the row element (class `fn fnrow`); new hook `functions._infoBtnFor(name)`; row property `row._popover` is the open popover element or `null`.

- [ ] **Step 1: Update the functions test**

In `tests/js/functions_and_rail.test.js`, replace the block from `// redesign markup: grid + card classes reconciled to terrarium.css` through `assert.ok(scriptEl.classList.contains("open"), "expander opens the script block");` with:

```js
  // row layout: one .fnrow per function inside a .fnlist; description,
  // condition and script live in the (i) popover, not on the row
  const list = mount.children.find((c) => c.className === "fnlist");
  assert.ok(list, "expected a .fnlist container");
  const fireworksCard = functions._cardFor("fireworks_player");
  assert.ok(fireworksCard.className.includes("fnrow"), "row carries the fnrow class");
  assert.ok(!fireworksCard.children.some((c) => c.className === "desc"), "no description on the row");
  assert.ok(fireworksCard.children.some((c) => c.className === "fired-line"));
  assert.ok(fireworksCard.innerHTML.includes("DEVICE"), "target chip on the row");
  // (i) opens a popover carrying description, condition and the steps
  const infoBtn = functions._infoBtnFor("fireworks_player");
  assert.ok(infoBtn && infoBtn.getAttribute("aria-label") === "Details for fireworks_player");
  assert.strictEqual(fireworksCard._popover, null);
  infoBtn.onclick({ stopPropagation() {} });
  const pop = fireworksCard._popover;
  assert.ok(pop && pop.className.includes("popover"));
  assert.ok(pop.innerHTML.includes("Celebratory flashes"));
  assert.ok(pop.innerHTML.includes("Player matches the call phrase"));
  assert.ok(pop.innerHTML.includes("bit-adjudicated"));
  assert.ok(pop.innerHTML.includes("+0.00s"));
  assert.ok(pop.innerHTML.includes("+1.40s"));
  infoBtn.onclick({ stopPropagation() {} });
  assert.strictEqual(fireworksCard._popover, null, "second click closes the popover");
```

Replace `assert.strictEqual(grid.children.length, 5, "one card per declared function");` with `assert.strictEqual(list.children.length, 5, "one row per declared function");`. The `driftCard`/`tiltCard` assertions checking `triangle`, `12`, `tilt`, `linear` move into their popovers:

```js
  const driftCard = functions._cardFor("drift");
  assert.ok(driftCard, "generator row should render");
  assert.ok(driftCard.innerHTML.includes("generator"));
  assert.ok(driftCard.innerHTML.includes("period 12s"));
  functions._infoBtnFor("drift").onclick({ stopPropagation() {} });
  assert.ok(driftCard._popover.innerHTML.includes("triangle"));
  functions._infoBtnFor("drift").onclick({ stopPropagation() {} });
  assert.ok(!driftCard.children.some((c) => c.tagName === "button" && c.textContent === "Fire"),
    "generator row must not offer a Fire button");
  const tiltCard = functions._cardFor("tilt_hue");
  assert.ok(tiltCard, "stream row should render");
  assert.ok(tiltCard.innerHTML.includes("verb:tilt"));
  functions._infoBtnFor("tilt_hue").onclick({ stopPropagation() {} });
  assert.ok(tiltCard._popover.innerHTML.includes("linear"));
  functions._infoBtnFor("tilt_hue").onclick({ stopPropagation() {} });
  assert.ok(!tiltCard.children.some((c) => c.tagName === "button" && c.textContent === "Fire"),
    "stream row must not offer a Fire button");
  const scriptedFireBtn = fireworksCard.children.find((c) => c.tagName === "button" && c.textContent === "Fire");
  assert.ok(scriptedFireBtn, "scripted row keeps its Fire button");
```

Keep `pickerFor` working by changing it to read the row's first `select` child:

```js
  const pickerFor = (name) => functions._cardFor(name).children.find((c) => c.tagName === "select");
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/bin/node tests/js/functions_and_rail.test.js`
Expected: AssertionError `expected a .fnlist container`

- [ ] **Step 3: Implement rows in `functions.js`**

Add a hook and popover state near the other hooks:

```js
const infoBtnByName = new Map();     // function name -> its (i) button (test hook)
let openPopoverRow = null;           // the row whose popover is open, else null

export function _infoBtnFor(name) {
  return infoBtnByName.get(name);
}
```

Change `refreshCardCompatibility` so the description travels with the row instead of a visible `.desc` node:

```js
  if (card) {
    card._descText = resolvedDescription(fn, picker ? picker.value : null);
    if (card._popDesc) card._popDesc.textContent = card._descText;
  }
```

Replace `buildGeneratorCard`, `buildStreamCard`, `buildScriptedCard`, and `buildCard` with:

```js
function summaryText(fn) {
  if (fn.kind === "generator") return `period ${fn.period}s · cc:${fn.lane.data1}`;
  if (fn.kind === "stream") return `verb:${fn.verb} → ${fn.outputs.length} output${fn.outputs.length === 1 ? "" : "s"}`;
  const n = fn.script.length;
  return `${n} step${n === 1 ? "" : "s"} · ${maxOffset(fn.script).toFixed(1)}s`;
}

function chipText(fn) {
  if (fn.kind === "generator" || fn.kind === "stream") return fn.kind;
  return fn.target;
}

function detailLines(fn) {
  if (fn.kind === "generator") {
    return [laneText(fn.lane), `waveform:${fn.waveform}   period:${fn.period}s   [${fn.lo}, ${fn.hi}]`];
  }
  if (fn.kind === "stream") {
    return [`verb:${fn.verb}   arg:${fn.arg}   in:[${fn.in_lo}, ${fn.in_hi}]`,
            ...fn.outputs.map(outputText)];
  }
  return fn.script.map(stepText);
}

function closePopover() {
  if (!openPopoverRow) return;
  if (openPopoverRow._popover) openPopoverRow._popover.remove();
  openPopoverRow._popover = null;
  openPopoverRow._popDesc = null;
  openPopoverRow = null;
}

function openPopover(fn, row) {
  closePopover();
  const pop = mk("div", "popover");
  const desc = mk("p", "desc", row._descText || fn.description);
  pop.appendChild(desc);
  row._popDesc = desc;
  if (fn.condition) {
    const cond = fn.condition;
    pop.appendChild(mk("p", "cond",
      cond.description + "   (" + cond.source + (cond.verb ? ": " + cond.verb : "") + ")"));
  }
  const script = mk("div", "script mono open");
  for (const line of detailLines(fn)) script.appendChild(mk("div", "step", line));
  pop.appendChild(script);
  row.appendChild(pop);
  row._popover = pop;
  openPopoverRow = row;
}

function buildRow(fn) {
  const row = mk("div", "fn fnrow");
  row._popover = null;
  row._popDesc = null;
  row._descText = fn.description;

  row.appendChild(mk("h3", null, fn.name));
  row.appendChild(mk("span", "chip dim kind", chipText(fn)));
  row.appendChild(mk("span", "mono summary", summaryText(fn)));

  let picker = null;
  if (fn.target === "DEVICE" || fn.target === "SURFACE") {
    picker = document.createElement("select");
    picker.id = "functionDev_" + fn.name;
    row.appendChild(picker);
    fillDevicePicker(picker, fn.target === "SURFACE");
    picker.onchange = () => refreshCardCompatibility(fn, picker, row);
  }
  row.appendChild(mk("span", "grow"));

  const infoBtn = mk("button", "infobtn", "i");
  infoBtn.type = "button";
  infoBtn.setAttribute("aria-label", `Details for ${fn.name}`);
  infoBtn.title = "Details";
  infoBtn.onclick = (e) => {
    if (e && e.stopPropagation) e.stopPropagation();
    if (row._popover) closePopover(); else openPopover(fn, row);
  };
  row.appendChild(infoBtn);
  infoBtnByName.set(fn.name, infoBtn);

  if (fn.kind !== "generator" && fn.kind !== "stream") {
    const fireBtn = mk("button", "btn solid-gold small", "Fire");
    fireBtn.onclick = () => {
      const extra = { name: fn.name };
      if (picker) extra.dev = picker.value;
      wire.send("fire_function", extra, fireBtn);
    };
    row.appendChild(fireBtn);

    const firedLine = mk("div", "fired-line");
    applyFired(row, firedLine, lastFired[fn.name]);
    row.appendChild(firedLine);
    row._firedLine = firedLine;
  }

  refreshCardCompatibility(fn, picker, row);
  return row;
}
```

In `render(list)`: replace `const grid = mk("div", "fngrid");` with `const list = mk("div", "fnlist");` (rename uses accordingly), call `buildRow(fn)` instead of `buildCard(fn)`, and add `closePopover(); infoBtnByName.clear();` right after `cardByName.clear();`. In `init()`, register the outside-click and Escape closers once:

```js
  document.addEventListener("click", () => closePopover());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopover(); });
```

Update the module header comment to describe rows and the popover.

- [ ] **Step 4: CSS**

In `console/static/terrarium.css` replace the `/* ---------- functions ---------- */` block from `.fngrid` through `.fn select { ... }` with:

```css
/* ---------- triggers (one row per function) ---------- */
.fnlist { display: flex; flex-direction: column; gap: 4px; }
.fn {
  background: var(--s-high); border-radius: 10px; border: 1.5px solid var(--hair);
  border-left: 4px solid var(--hair-tan);
  padding: 4px 10px; margin: 0; min-height: 32px;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; position: relative;
}
.fn.fired-admin { border-left-color: var(--terra); }
.fn.fired { border-left-color: var(--sage); }
.fn h3 { font-family: var(--f-disp); font-weight: 400; font-size: 14px; margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 180px; }
.fn .summary { font-size: 11px; color: var(--ink-dim); white-space: nowrap; }
.fn .grow { flex: 1; }
.fn select { min-width: 0; max-width: 200px; font-size: 11.5px; padding: 2px 6px; }
.fn .fired-line { flex-basis: 100%; }
.fn .desc { margin: 0 0 4px; font-size: 12px; }
.fn .cond { font-size: 11px; color: var(--ink-dim); margin: 0 0 6px; }
.infobtn {
  width: 22px; height: 22px; border-radius: 50%; border: 1.5px solid var(--gold-deep);
  background: none; color: var(--gold); font-family: var(--f-mono); font-size: 12px;
  line-height: 1; padding: 0; flex: none;
}
.infobtn:hover { background: var(--s-highest); }
.popover {
  flex-basis: 100%; margin: 4px 0 2px; padding: 10px 12px;
  background: var(--s); border: 1.5px solid var(--hair-tan); border-radius: 10px;
  box-shadow: var(--shadow-top);
}
@media (max-width: 800px) {
  .fn h3 { max-width: 100%; }
  .fn .summary { flex-basis: 100%; }
}
```

Keep the `.scriptbar`, `.expander`, `.script`, `select`, and `.fired-line` rules that follow (the popover reuses `.script.open`). Delete the `.scriptbar` and `.expander` rules only if grep shows no other user of those classes in `console/static/*.js`.

- [ ] **Step 5: Run tests**

Run: `/opt/homebrew/bin/node tests/js/functions_and_rail.test.js && /opt/homebrew/bin/node tests/js/diagnostics_row.test.js && /opt/homebrew/bin/node tests/js/full_stack.test.js`
Expected: all `ok`. If `diagnostics_row.test.js` asserts `fngrid`, change that assertion to `fnlist`.

- [ ] **Step 6: Commit**

```bash
git add console/static/functions.js console/static/terrarium.css tests/js/functions_and_rail.test.js tests/js/diagnostics_row.test.js
git commit -m "feat(console): one row per trigger with an info popover"
```

### Task 7: `confirmTap` fill-style arming

**Files:**
- Modify: `console/static/wire.js:74-107`
- Test: `tests/js/wire_and_shell.test.js`

**Interfaces:**
- Produces: `wire.confirmTap(btn, { armLabel, armStyle, timeoutMs, onArm, onDisarm }, onConfirm)`. With `armStyle: "fill"` the button's text is never changed; `onArm()` runs after arming, `onDisarm()` runs on confirm and on timeout.

- [ ] **Step 1: Write the failing test**

Append inside the async block of `tests/js/wire_and_shell.test.js` before the shell import:

```js
  // fill-style arming: icon buttons cannot swap a label, so the armed
  // state is data-armed plus onArm/onDisarm hooks and the text is untouched
  {
    const realSetTimeout = globalThis.setTimeout;
    let capturedFn = null;
    globalThis.setTimeout = (fn) => { capturedFn = fn; return 0; };
    const ibtn = el();
    ibtn.textContent = "<svg/>";
    const calls = [];
    let fconfirmed = 0;
    const opts = { armStyle: "fill", onArm: () => calls.push("arm"), onDisarm: () => calls.push("disarm") };
    wire.confirmTap(ibtn, opts, () => { fconfirmed += 1; });
    assert.strictEqual(ibtn.dataset.armed, "1");
    assert.strictEqual(ibtn.textContent, "<svg/>", "fill style never touches the text");
    assert.deepStrictEqual(calls, ["arm"]);
    wire.confirmTap(ibtn, opts, () => { fconfirmed += 1; });
    assert.strictEqual(fconfirmed, 1);
    assert.deepStrictEqual(calls, ["arm", "disarm"]);
    // timeout path also disarms via the hook and keeps the text
    wire.confirmTap(ibtn, opts, () => {});
    capturedFn();
    globalThis.setTimeout = realSetTimeout;
    assert.strictEqual(ibtn.dataset.armed, undefined);
    assert.strictEqual(ibtn.textContent, "<svg/>");
    assert.deepStrictEqual(calls, ["arm", "disarm", "arm", "disarm"]);
    console.log("confirmTap fill style: ok");
  }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/bin/node tests/js/wire_and_shell.test.js`
Expected: AssertionError on `ibtn.textContent` (label was swapped to `undefined`) or on `calls`.

- [ ] **Step 3: Implement**

Replace `confirmTap` in `console/static/wire.js` with:

```js
export function confirmTap(btn, { armLabel, armStyle = "label", timeoutMs = 4000,
                                  onArm = null, onDisarm = null } = {}, onConfirm) {
  if (btn.dataset.armed === "1") {
    delete btn.dataset.armed;
    clearTimeout(btn._confirmTimer);
    if (armStyle === "label") btn.textContent = btn._restLabel ?? btn.textContent;
    if (onDisarm) onDisarm();
    onConfirm();
    return;
  }
  const original = btn.textContent;
  btn.dataset.armed = "1";
  if (armStyle === "label") {
    btn._restLabel = original;
    btn.textContent = armLabel;
  }
  if (onArm) onArm();
  btn._confirmTimer = setTimeout(() => {
    if (btn.dataset.armed === "1") {
      delete btn.dataset.armed;
      if (armStyle === "label") btn.textContent = original;
      if (onDisarm) onDisarm();
      flashNotConfirmed(btn);
    }
  }, timeoutMs);
}
```

Note: the existing label path restored the text only on timeout, never on confirm (the button was usually rebuilt by the resulting state change). Restoring on confirm too is harmless and keeps the two paths symmetric. Run the full `wire_and_shell` test to confirm the existing label assertions still hold (`Confirm abort?` text after arming, rest label after timeout).

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/bin/node tests/js/wire_and_shell.test.js`
Expected: all `ok` including `confirmTap fill style: ok`.

- [ ] **Step 5: Commit**

```bash
git add console/static/wire.js tests/js/wire_and_shell.test.js
git commit -m "feat(console): confirmTap fill-style arming for icon buttons"
```

### Task 8: Sidebar icon action row

**Files:**
- Modify: `console/static/bit.js:149-176, 205-216`
- Modify: `console/static/terrarium.css` (`.btnrow`, `.btn.icon`)
- Test: `tests/js/bit_panel.test.js`

**Interfaces:**
- Consumes: `wire.confirmTap` with `armStyle: "fill"` from Task 7.
- Produces: four `.btn.icon` buttons with `aria-label` `Run`, `Restart`, `Abort`, `Load a Bit`; a `.confirm-note` element under the row, `hidden` unless a button is armed.

- [ ] **Step 1: Update the bit panel test**

In `tests/js/bit_panel.test.js`, change `findButton`:

```js
  function findButton(node, label) {
    return findNode(node, (n) => n.tagName === "button" && n.getAttribute("aria-label") === label);
  }
```

After `const abortBtn = findButton(panelForConfirm, "Abort");` add:

```js
  // icon row: four icon buttons on one row, aria-labelled, SVG inside
  const btnrow = findByClass(panelForConfirm, "btnrow");
  const iconBtns = btnrow.children.filter((c) => c.tagName === "button");
  assert.deepStrictEqual(iconBtns.map((b) => b.getAttribute("aria-label")), ["Run", "Restart", "Abort", "Load a Bit"]);
  assert.ok(iconBtns.every((b) => b.className.includes("icon")));
  assert.ok(abortBtn.innerHTML.includes("<svg"), "icon button carries inline SVG");
  const note = findByClass(panelForConfirm, "confirm-note");
  assert.strictEqual(note.hidden, true, "confirm note hidden at rest");
```

After `assert.strictEqual(abortBtn.dataset.armed, "1", "first tap arms confirm");` add:

```js
  assert.ok(abortBtn.innerHTML.includes("<svg"), "arming never swaps the icon for text");
  assert.strictEqual(note.hidden, false, "confirm note shows while armed");
  assert.ok(note.textContent.includes("click again to confirm"));
```

After `assert.strictEqual(abortBtn.dataset.armed, undefined, "second tap clears armed state");` add:

```js
  assert.strictEqual(note.hidden, true, "confirm note hides after confirm");
```

Also in the earlier part of the file, the two lines `const pickLoadBtn = findByClass(byId.get("bitPanel"), "btn");` and `findByClass(byId.get("bitPanel"), "btn").onclick();` target the empty-state Load button, which stays a text button; leave them.

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/bin/node tests/js/bit_panel.test.js`
Expected: AssertionError `Abort button should be present while ROOM_READY` (no aria-label yet).

- [ ] **Step 3: Implement in `bit.js`**

Add near the top:

```js
const ICONS = {
  run: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>',
  restart: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M8 2.5a5.5 5.5 0 1 1-5.2 3.7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M2.5 2.5v4h4z" fill="currentColor"/></svg>',
  abort: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="3" y="3" width="10" height="10" rx="1.5" fill="currentColor"/></svg>',
  load: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M2 4.5h4l1.5 1.5H14v7H2z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 7.5v4M6 9.5h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
};

function iconBtn(className, label, icon) {
  const b = mk("button", `btn icon ${className}`);
  b.type = "button";
  b.setAttribute("aria-label", label);
  b.title = label;
  b.innerHTML = ICONS[icon];
  return b;
}
```

Replace the button-row section of `buildLoadedPanel` (from `const btnrow = mk("div", "btnrow");` through `wrap.appendChild(btnrow);`) with:

```js
  const btnrow = mk("div", "btnrow icons");
  const note = mk("div", "confirm-note", "click again to confirm");
  note.hidden = true;
  const showNote = () => { note.hidden = false; };
  const hideNote = () => { note.hidden = true; };

  const runBtn = iconBtn("solid-gold", "Run", "run");
  runBtn.onclick = () => wire.send("run", {}, runBtn);
  btnrow.appendChild(runBtn);

  const restartBtn = iconBtn("outline", "Restart", "restart");
  restartBtn.onclick = () => {
    wire.confirmTap(restartBtn, { armStyle: "fill", onArm: showNote, onDisarm: hideNote }, () => {
      wire.send("restart", {}, restartBtn);
    });
  };
  btnrow.appendChild(restartBtn);

  const abortBtn = iconBtn("solid-rose", "Abort", "abort");
  abortBtn.onclick = () => {
    wire.confirmTap(abortBtn, { armStyle: "fill", onArm: showNote, onDisarm: hideNote }, () => {
      wire.send("abort", {}, abortBtn);
    });
  };
  btnrow.appendChild(abortBtn);

  const loadBtn = iconBtn("outline", "Load a Bit", "load");
  loadBtn.onclick = openPicker;
  btnrow.appendChild(loadBtn);

  wrap.appendChild(btnrow);
  wrap.appendChild(note);
```

Update the file header comment's "Run/Abort/Load buttons" to "Run/Restart/Abort/Load icon buttons". `reserveConfirmWidth` is no longer called in this file; leave it in `wire.js` (rooms.js and surface.js still use it).

- [ ] **Step 4: CSS**

In `console/static/terrarium.css` replace the `.btnrow` two lines with:

```css
.btnrow { display: flex; gap: 8px; flex-wrap: wrap; }
.btnrow .btn { padding: 5px 16px; font-size: 13.5px; }
.btnrow.icons { flex-wrap: nowrap; }
.btn.icon {
  width: 36px; height: 36px; padding: 0; display: inline-flex;
  align-items: center; justify-content: center; flex: none;
}
.btn.icon.solid-gold { background: var(--gold-deep); color: var(--on-gold); }
.btn.icon.solid-rose { background: var(--rose); color: var(--bg); }
.btn.icon:disabled { opacity: 0.35; transform: none; }
.btn.icon[data-armed="1"] { box-shadow: 0 0 0 3px var(--gold); animation: pulse 1.2s ease-in-out infinite; }
.btn.icon.solid-rose[data-armed="1"] { box-shadow: 0 0 0 3px var(--rose); }
.btn.icon.outline[data-armed="1"] { background: var(--gold-deep); color: var(--on-gold); }
.confirm-note { font-family: var(--f-mono); font-size: 11.5px; color: var(--gold); margin-top: -4px; }
```

Check with grep that `.btn.solid-gold` / `.btn.solid-rose` already exist (they are used by text buttons today); if they do, drop the two duplicated `.btn.icon.solid-*` lines.

- [ ] **Step 5: Run tests**

Run: `/opt/homebrew/bin/node tests/js/bit_panel.test.js && /opt/homebrew/bin/node tests/js/full_stack.test.js`
Expected: all `ok`.

- [ ] **Step 6: Commit**

```bash
git add console/static/bit.js console/static/terrarium.css tests/js/bit_panel.test.js
git commit -m "feat(console): single-row icon buttons for Run, Restart, Abort, Load"
```

### Task 9: Browser check, docs, PR B

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (dated entry; see step 3)

- [ ] **Step 1: Full offline suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, none newly skipped beyond the documented ones.

- [ ] **Step 2: Live check at two widths**

**RUN ON: MYCOLOGICAL** from the worktree root:

```bash
./terrarium.sh --room DEMO
```

Load the Metronome Bit from the Console. Confirm at desktop width and at a 800 px window: the fixture head shows no chips; Triggers sits directly under the LED rows as one row per trigger with (i) popovers that open and close (outside click and Escape); Live values is collapsed, opens to the lane table, and its values tick while a Testshroom tilts; the four sidebar icon buttons sit on one row, Abort arms with the note and fires on a second click; Room view shows the chips under the fixture. Stop the stack with Ctrl-C.

- [ ] **Step 3: Deep-dive entry**

Add a dated section to `docs/MM_TERRARIUM.md` next to the other Console entries (after the "Console nav follow-up 2" entry) titled `### Console Live-view UX pass: loading overlay, nav fix, lane table, trigger rows, icon buttons (2026-09-14)` summarising: `busy.js` and the `_sent` wire event, the nav fix and why it was a bug, the chip move, the lane table replacing instrument cards (with `_instCardFor` gone and `_laneRowFor`/`_laneTable` in its place), trigger rows with the popover, and `confirmTap`'s `armStyle: "fill"`. Link the spec. Then run the `mm-deepdive-sync` skill at closeout.

- [ ] **Step 4: Open PR B**

Use `commit-commands:commit-push-pr` with title `console: Live view relayout and icon action row`, body listing Tasks 4-8 and the live-check result, noting it stacks on PR A.
