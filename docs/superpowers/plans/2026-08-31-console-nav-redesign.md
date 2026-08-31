# Console Nav Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Terrarium Console to a sidebar-nav layout: no right rail, Registration + Instruments in the sidebar, a Bit Details popup, and three switchable center views (Live / Room / Event Log).

**Architecture:** Front-end only, in `console/static/` (six ES modules + `index.html` + `terrarium.css`). `shell.js` gains a view switcher that toggles `hidden` on three persistent center containers; `rail.js` loses the Devices/Roles cards (Devices becomes an Instruments pull under Registration; Roles refcards move into `bit.js`'s new Bit Details popup). No backend or wire change.

**Tech Stack:** Vanilla ES modules, node-run DOM-stub tests (`tests/js/*.test.js` via `tests/test_console_js.py`), pytest through `.venv`.

**Spec:** `docs/superpowers/specs/2026-08-31-console-nav-redesign-design.md`

## Global Constraints

- **Never rebuild a DOM subtree whose declaration hasn't changed** on high-frequency events (`room_changed`, `devices_changed`, `trigger_fired`): armed `wire.confirmTap` buttons die with their element. The view switcher must toggle `hidden`, never unmount.
- Run tests as `.venv/bin/python -m pytest tests/test_console_js.py -v` (never bare `python3`; the worktree `.venv` is a symlink to the main checkout's). Individual JS tests: `node tests/js/<file>.test.js`.
- UI copy says "Instruments", never "Devices".
- Modules use the local `mk(tag, className, text)` / `clear(node)` helper idiom already present in each file — keep it.
- Commit after every task.

---

### Task 1: New skeleton, CSS, and shell.js view switcher

**Files:**
- Modify: `console/static/index.html`
- Modify: `console/static/terrarium.css`
- Modify: `console/static/shell.js`
- Test: `tests/js/wire_and_shell.test.js`

**Interfaces:**
- Produces: DOM ids `viewLive`, `viewRoom`, `viewLog`, `navLive`, `navRoom`, `navLog`, `bitPanel`, `registrationCard`, `roomsPanel`, `roomCard`, `bitStatusCard`, `logCard`, `overlayMount`; `shell.js` exports `showView(name)` (`"live" | "room" | "log"`).
- Consumes: existing `wire.js` API unchanged.

- [ ] **Step 1: Rewrite `index.html` body**

Replace the `.shell` block with:

```html
<div class="shell">
  <div class="sidebar">
    <p class="navlabel">Loaded Bit</p>
    <div id="bitPanel"></div>
    <div id="registrationCard" class="sidecard"></div>
    <nav class="viewnav">
      <button id="navLive" class="navbtn active">Live</button>
      <button id="navRoom" class="navbtn">Room: none</button>
      <button id="navLog" class="navbtn">Event Log</button>
    </nav>
  </div>
  <div class="content">
    <div id="viewLive" class="maincol">
      <div id="roomCard" class="card"></div>
      <div id="bitStatusCard" class="card" hidden></div>
    </div>
    <div id="viewRoom" class="maincol" hidden>
      <section id="roomsPanel" class="roomspanel"></section>
    </div>
    <div id="viewLog" class="maincol" hidden>
      <div id="logCard" class="card"></div>
    </div>
  </div>
</div>
<div id="overlayMount"></div>
```

(`devicesCard`/`rolesCard` and the `.rail` div are gone; `roomsPanel` is no longer above `roomCard`.)

- [ ] **Step 2: CSS changes in `terrarium.css`**

In the layout section: change `.content { display: grid; grid-template-columns: 1fr 320px; ... }` to `grid-template-columns: 1fr;` and delete the `@media (max-width: 1400px)` block that collapsed the rail (keep the 800px one). Remove `.rail` from the `.maincol, .rail` selector. Add:

```css
/* ---------- sidebar nav + cards ---------- */
.viewnav { display: flex; flex-direction: column; gap: 6px; margin-top: auto; }
.navbtn {
  font-family: var(--f-disp); font-size: 15px; letter-spacing: 0.06em;
  text-align: left; background: none; border: none; color: var(--ink-var);
  border-left: 3px solid transparent; border-radius: 0 9999px 9999px 0;
  padding: 7px 14px;
}
.navbtn:hover { color: var(--ink); background: var(--s-high); }
.navbtn.active { color: var(--gold); border-left-color: var(--gold); background: var(--s); }
.sidecard { border-top: 1.5px solid var(--hair); padding-top: 12px; }
.pill {
  font-family: var(--f-disp); font-size: 12px; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--gold);
  background: none; border: 1.5px solid var(--gold-deep);
  border-radius: 9999px; padding: 2px 12px;
}
.pill:hover { background: var(--s-high); }
```

Identity/version sizing (Task 2 relies on these): add `.bitname-row { display: flex; align-items: center; gap: 10px; min-width: 0; } .bitname-row h3 { font-family: var(--f-disp); font-weight: 400; font-size: 17px; line-height: 1.1; margin: 0; overflow-wrap: anywhere; } .bitversion { font-family: var(--f-mono); font-size: 9.5px; color: var(--ink-dim); margin: 0; }`.

- [ ] **Step 3: Write the failing shell test**

Append to `tests/js/wire_and_shell.test.js` (inside the async IIFE, after existing asserts — the stub's `byId` auto-vivifies mounts, so pre-seed nothing):

```js
  const shell = await import("../../console/static/shell.js");
  // view switcher: exactly one visible view at a time
  shell.showView("log");
  assert.strictEqual(byIdLocal("viewLive").hidden, true);
  assert.strictEqual(byIdLocal("viewLog").hidden, false);
  assert.ok(byIdLocal("navLog").className.includes("active"));
  assert.ok(!byIdLocal("navLive").className.includes("active"));
  shell.showView("live");
  assert.strictEqual(byIdLocal("viewLive").hidden, false);
  assert.strictEqual(byIdLocal("viewLog").hidden, true);
  // Room nav label tracks the active room
  shell.paintRoomNav([{ name: "TEST", active: true }]);
  assert.strictEqual(byIdLocal("navRoom").textContent, "Room: TEST");
  shell.paintRoomNav([{ name: "TEST", active: false }]);
  assert.strictEqual(byIdLocal("navRoom").textContent, "Room: none");
```

where `byIdLocal` is this file's own `byId.get` accessor (this test file has its own inline stub; use `document.getElementById(...)` directly if no map is exposed). NOTE: this test file's inline stub predates `_dom_stub.js`; if importing `shell.js` here drags in every panel module and fights the minimal stub, move these asserts into `tests/js/full_stack.test.js` (which uses the shared stub) instead and leave this file untouched — that placement is acceptable.

- [ ] **Step 4: Run it, verify failure** — `node tests/js/wire_and_shell.test.js` (or `full_stack`) fails: `showView` not exported.

- [ ] **Step 5: Implement in `shell.js`**

```js
const VIEWS = { live: ["viewLive", "navLive"], room: ["viewRoom", "navRoom"], log: ["viewLog", "navLog"] };

export function showView(name) {
  for (const [key, [viewId, navId]] of Object.entries(VIEWS)) {
    const on = key === name;
    document.getElementById(viewId).hidden = !on;
    const btn = document.getElementById(navId);
    btn.className = on ? "navbtn active" : "navbtn";
  }
}

export function paintRoomNav(rooms) {
  const active = (rooms || []).find((r) => r.active);
  document.getElementById("navRoom").textContent =
    `Room: ${active ? active.name : "none"}`;
}
```

In the init section: wire `onclick` for the three nav buttons to `showView("live"|"room"|"log")`; subscribe `wire.on("snapshot", (m) => paintRoomNav(m.rooms))` and re-request nothing else (`room_loaded`/`room_unloaded` are followed by a fresh snapshot from the agent; ALSO subscribe them defensively: `wire.on("room_loaded", ...)`/`wire.on("room_unloaded", ...)` are fine to leave out if the snapshot already repaints — check `console/agent.py`'s broadcast behavior; if those events do NOT carry `rooms`, keep only the snapshot subscription and accept the label updates on next snapshot; if snapshots do not follow, add `rooms` tracking from the `rooms.js` pattern). Keep existing top-bar handlers; `roomChip` behavior unchanged.

- [ ] **Step 6: Run tests, verify pass** — `node tests/js/wire_and_shell.test.js && node tests/js/full_stack.test.js` (full_stack will still fail on rail asserts until Task 3 — confirm the shell asserts pass; a temporarily red full_stack line naming `devicesCard` is expected and noted for Task 3).

- [ ] **Step 7: Commit** — `git add -A console/static tests/js && git commit -m "feat(console): sidebar nav skeleton with switchable center views"`

---

### Task 2: Bit identity row + Bit Details popup (absorbs roles refcards)

**Files:**
- Modify: `console/static/bit.js`
- Modify: `console/static/rail.js` (delete `buildRefCard`/`renderRoles`/`manifestInstruments` after moving them)
- Test: `tests/js/bit_panel.test.js`, `tests/js/functions_and_rail.test.js`

**Interfaces:**
- Consumes: `buildInstrumentCard(inst, liveValues)` from `surface.js` (unchanged); `#overlayMount`.
- Produces: `bit.js` renders `.bitname-row` (icon + h3 on one row), `.bitversion` line, and a `.pill` button labeled `Bit Details`; popup contains the Rooms/Roles/About/Notes `dl` plus one refcard per role. `bit.js` stores roles from `snapshot.roles` (same `role_view` rows `rail.js` used).

- [ ] **Step 1: Write the failing tests**

In `tests/js/bit_panel.test.js`, after the existing snapshot dispatch that loads a bit, add:

```js
  const panelHtml = byId.get("bitPanel").innerHTML;
  assert.ok(panelHtml.includes("bitname-row"), "icon+title share one row");
  assert.ok(panelHtml.includes("bitversion"), "version line present");
  assert.ok(panelHtml.includes("Bit Details"), "details pill present");
  assert.ok(!panelHtml.includes("<dt>Rooms</dt>"), "detail dl moved to popup");
  // open the popup
  const pill = findByClass(byId.get("bitPanel"), "pill");
  pill.onclick();
  const overlayHtml = byId.get("overlayMount").innerHTML;
  assert.ok(overlayHtml.includes("Rooms"));
  assert.ok(overlayHtml.includes("Notes"));
  assert.ok(overlayHtml.includes("refcard"), "roles refcards inside popup");
```

with a small local helper `findByClass(node, cls)` doing a recursive child search on `className.includes(cls)`. Feed the snapshot a `roles` array (copy the shape from `tests/js/functions_and_rail.test.js`'s roles fixture) so refcards have data.

In `tests/js/functions_and_rail.test.js`: delete the `rolesCard` assertions (lines asserting on `byId.get("rolesCard")`) — that card no longer exists; the refcard behavior is now covered by `bit_panel.test.js`.

- [ ] **Step 2: Run, verify failure** — `node tests/js/bit_panel.test.js` fails (no `bitname-row`).

- [ ] **Step 3: Implement in `bit.js`**

Move `manifestInstruments(role)` and `buildRefCard(role)` verbatim from `rail.js` into `bit.js` (they only need `mk`, `buildInstrumentCard` — add `import { buildInstrumentCard } from "./surface.js";`). Track roles: in `init()`'s snapshot handler add `rolesByName = {}; for (const role of m.roles || []) rolesByName[role.role] = role;` (module-level `let rolesByName = {}`).

In `render()`, replace the identity row + details `dl` block:

```js
  // identity: icon + title one row, version + details pill beneath
  const idrow = mk("div", "bitname-row");
  idrow.appendChild(mk("span", "art", "✦"));
  const h3 = mk("h3", null, (bit && bit.display_name) || loadedName);
  idrow.appendChild(h3);
  if (bit) idrow.appendChild(mk("span", "kind", bit.kind));
  wrap.appendChild(idrow);

  const versionSuffix = bit ? ` · v${bit.version}` : "";
  wrap.appendChild(mk("p", "bitversion", `${loadedName}${versionSuffix}`));

  const detailsPill = mk("button", "pill", "Bit Details");
  detailsPill.onclick = () => openDetails(bit);
  wrap.appendChild(detailsPill);
```

Delete the old `dl` "details" block from `render()`. Add:

```js
function openDetails(bit) {
  const mount = document.getElementById("overlayMount");
  clear(mount);
  const overlay = mk("div", "overlay open");
  overlay.onclick = (e) => { if (e.target === overlay) closeOverlay(); };
  const picker = mk("div", "picker");

  const head = mk("div", "pickhead");
  head.appendChild(mk("h2", null, "Bit Details"));
  const xbtn = mk("button", "xbtn", "✕");
  xbtn.onclick = closeOverlay;
  head.appendChild(xbtn);
  picker.appendChild(head);

  const dl = mk("dl", "detail");
  const addRow = (k, v) => { dl.appendChild(mk("dt", null, k)); dl.appendChild(mk("dd", null, v)); };
  addRow("Rooms", (bit && bit.room_types || []).join(", ") || "none");
  addRow("Roles", rolesText(bit && bit.roles));
  addRow("About", (bit && bit.description) || "—");
  addRow("Notes", (bit && bit.notes) || "—");
  picker.appendChild(dl);

  for (const name of Object.keys(rolesByName)) {
    picker.appendChild(buildRefCard(rolesByName[name]));
  }
  overlay.appendChild(picker);
  mount.appendChild(overlay);
}
```

(Reuse the existing `closeOverlay`; Escape-key handling mirrors `openPicker` — copy those four lines.)

In `rail.js`: delete `renderRoles`, `buildRefCard`, `manifestInstruments`, the `rolesCard` usage in the snapshot handler, and the now-unused `import { buildInstrumentCard }` if nothing else uses it (Task 3 does not).

- [ ] **Step 4: Run, verify pass** — `node tests/js/bit_panel.test.js && node tests/js/functions_and_rail.test.js`

- [ ] **Step 5: Commit** — `git add -A console/static tests/js && git commit -m "feat(console): one-row bit identity and Bit Details popup with role refcards"`

---

### Task 3: Registration in sidebar with Instruments pull

**Files:**
- Modify: `console/static/rail.js`
- Test: `tests/js/functions_and_rail.test.js`

**Interfaces:**
- Consumes: `snapshot`/`registration_changed`/`devices_changed` rows as today; `snapshot.room` / `room_changed`'s `room.fixtures[].dev` for fixture tagging; `rolesByName` (rail keeps its own copy — it still has one from the snapshot handler).
- Produces: `#registrationCard` (now sidebar-mounted) containing the role rows AND a `<details class="acc instpull">` whose summary is `Instruments` and whose body holds one `.devrow` per device with a tag chip: `Scored` / `Jam` / `Fixture` / capitalized role class / `Unregistered`.

- [ ] **Step 1: Write the failing test**

In `tests/js/functions_and_rail.test.js`, replace the old `devicesCard` assertions with (adapt the existing snapshot fixture: give it `devices: [{dev:"ie1",name:"Testshroom 1",role:"player"},{dev:"ie2",name:"Testshroom 2",role:"jammer"},{dev:"sim-room",name:"Room sim",role:null},{dev:"ie9",name:"Wanderer",role:null}]`, `roles` including `player` (`scored: true, class: "shared"`) and `jammer` (`class: "jam"`), and `room: { room_type: "TEST", fixtures: [{name:"main", dev:"sim-room"}], ... }` matching the shape the surface tests already feed):

```js
  const reg = byId.get("registrationCard").innerHTML;
  assert.ok(reg.includes("Instruments"), "pull is labeled Instruments");
  assert.ok(!reg.includes(">Devices<"), "no Devices wording");
  assert.ok(reg.includes("Testshroom 1"));
  assert.ok(/Testshroom 1[\s\S]*?Scored/.test(reg), "scored role tagged Scored");
  assert.ok(/Testshroom 2[\s\S]*?Jam/.test(reg), "jam role tagged Jam");
  assert.ok(/Room sim[\s\S]*?Fixture/.test(reg), "room-bound dev tagged Fixture");
  assert.ok(/Wanderer[\s\S]*?Unregistered/.test(reg), "no-role dev tagged Unregistered");
```

- [ ] **Step 2: Run, verify failure** — `node tests/js/functions_and_rail.test.js`

- [ ] **Step 3: Implement in `rail.js`**

Add module state `let fixtureDevs = new Set();`. In the snapshot handler and a new `wire.on("room_changed", ...)` handler: `fixtureDevs = new Set(((m.room && m.room.fixtures) || []).map((f) => f.dev).filter(Boolean)); renderRegistration();` (snapshot sets it before the first `renderRegistration()` call).

```js
function deviceTag(dev) {
  if (fixtureDevs.has(dev.dev)) return ["Fixture", "chip terra"];
  if (!dev.role) return ["Unregistered", "chip dim"];
  const decl = rolesByName[dev.role];
  if (decl && decl.scored) return ["Scored", "chip gold"];
  if (decl && decl.class === "jam") return ["Jam", "chip sage"];
  const cls = decl ? decl.class : "role";
  return [cls.charAt(0).toUpperCase() + cls.slice(1), "chip dim"];
}
```

At the end of `renderRegistration()` (after the role rows), append the pull:

```js
  const pull = document.createElement("details");
  pull.className = "acc instpull";
  const summary = document.createElement("summary");
  summary.appendChild(mk("span", "tri", "▸"));
  summary.appendChild(document.createTextNode(`Instruments (${deviceRows.length})`));
  pull.appendChild(summary);
  const body = mk("div", "accbody");
  if (!deviceRows.length) body.appendChild(mk("p", "muted", "No instruments connected"));
  for (const dev of deviceRows) {
    const row = mk("div", "devrow");
    row.appendChild(mk("span", "mono devid", dev.dev));
    row.appendChild(mk("span", "devname", dev.name));
    const [label, chipClass] = deviceTag(dev);
    row.appendChild(mk("span", `${chipClass} roletag`, label));
    body.appendChild(row);
  }
  pull.appendChild(body);
  if (instPullOpen) pull.setAttribute("open", "");
  pull.addEventListener("toggle", () => { instPullOpen = pull.open; });
  card.appendChild(pull);
```

with module state `let instPullOpen = false;` so a `devices_changed` rebuild does not slam the pull shut. Change the `devices_changed` handler to call `renderRegistration()` (the pull lives there now) and delete `renderDevices()` and the `devicesCard` reference. NOTE: `renderRegistration()` rebuilds wholesale on `registration_changed`/`devices_changed` exactly as the old registration card did — it contains no `confirmTap` buttons, so the no-rebuild rule's protected state is not present; preserving `instPullOpen` across rebuilds is the one piece of DOM state to carry, done above.

- [ ] **Step 4: Run, verify pass** — `node tests/js/functions_and_rail.test.js`

- [ ] **Step 5: Update `full_stack.test.js`** — replace its `devicesCard` assert with `assert.ok(byId.get("registrationCard").innerHTML.includes("Testshroom 1"));`, add `shell.showView` smoke assert if Task 1 parked it here, and confirm `node tests/js/full_stack.test.js` passes.

- [ ] **Step 6: Commit** — `git add -A console/static tests/js && git commit -m "feat(console): registration card gains Instruments pull with type tags"`

---

### Task 4: Full suite, docs sync, PR

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (console module section: rail retired, nav views, Bit Details popup, Instruments pull)
- Test: whole suite

- [ ] **Step 1: Run everything** — `.venv/bin/python -m pytest tests -v` (JS tests run via `tests/test_console_js.py`; the rest of the suite guards against accidental backend drift). Expected: all pass.
- [ ] **Step 2: Manual smoke** — `.venv/bin/python -m harness.terrarium_boot --console-port 8765` is the real driver, but headless CI-style verification is the node suite; if a browser is available, load the console, click through Live/Room/Event Log, open Bit Details, expand Instruments.
- [ ] **Step 3: Update `docs/MM_TERRARIUM.md`** — in the ES-modules section (≈ lines 2050–2130): note the 2026-08-31 nav redesign (rail deleted; `rail.js` = registration + Instruments pull + log; roles refcards live in `bit.js`'s Bit Details popup; `shell.js` owns `showView`/`paintRoomNav`; rooms panel is the Room view, not a top strip).
- [ ] **Step 4: Commit and PR** — `git add -A && git commit -m "docs(terrarium): console nav redesign notes"`, push branch, open PR against `main` titled "Console nav redesign: sidebar nav, Bit Details popup, Instruments pull".
