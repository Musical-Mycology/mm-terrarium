# Terrarium Console Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `console/static/` into the approved dark Musical Mycology
design (see the committed mockup), mapping every existing console feature
plus the two no-UI wire commands (`arm_room`/`release_room`, `load_bit`
overrides), with one additive backend change (role summary in `bits_listed`).

**Architecture:** No build step. One CSS token sheet + five vanilla ES
modules served by the existing allowlisted single-port `ConsoleServer`.
`wire.js` owns the websocket and an event-dispatch registry; each panel
module registers handlers and renders its own DOM region; `shell.js` is the
single `<script type="module">` entry that imports and initializes the rest.

**Tech Stack:** Vanilla JS (ES modules), hand-written CSS, `<canvas>` for
LED rendering, self-hosted TTF fonts, pytest + node (plain `node` as a test
runner, per the existing `tests/js/` pattern).

**Spec:** `docs/superpowers/specs/2026-08-25-console-redesign-design.md`
**Visual reference (approved by the user):** `docs/mockups/console-redesign-mockup.html`
— the CSS in its `<style>` block and the DOM shapes in its body are the
approved look. Copy from it; do not re-invent styling.

## Global Constraints

- **No CDN / no external fetches anywhere in `console/static/`** — a venue
  box may be offline. `tests/test_console_static.py::test_no_external_asset_fetches_anywhere`
  enforces this; fonts are self-hosted files.
- **No build step, no npm dependency.** Node appears only as a test runner.
- **Single-port server model unchanged**: `console/server.py` serves only
  allowlisted extensions, basename-only resolution.
- **Engine untouched** except Task 1's additive `BitRegistry.list_view`
  change. `ConsoleAgent`, `console/protocol.py`, all `control/` code:
  no changes.
- **Run all tests through the venv**: `.venv/bin/python -m pytest tests -v`
  (a bare `python3` produces a phantom import error; see docs/MM_TERRARIUM.md).
  In a fresh worktree first run `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`.
- **The nine rendering rules** (spec section 6) are binding on every panel
  task: signature-gated rebuilds, paint-don't-rebuild, per-fixture
  granularity + in-place reinsertion, per-dev pending frames (server side,
  already done), GRB decode, module isolation, Room privacy filter
  untouched, unknown-dev frames are no-ops.
- **Status colors**: sage `#7a9e6e` ok/running, rose `#d96680` error/abort,
  terracotta `#c07850` warning/admin/unbound. Fonts: Londrina Solid
  (display), Atkinson Hyperlegible (body), JetBrains Mono (data).
- Commit after every green test cycle; conventional-commit style
  (`feat(console): …`, `test(console): …`).

---

### Task 1: Role summary in `bits_listed` (backend, additive)

**Files:**
- Modify: `control/bit_registry.py` (the `list_view` method, currently
  lines ~139-162)
- Test: `tests/test_bit_registry.py` (append tests; file exists)

**Interfaces:**
- Consumes: `BitPackage.config` (`BitConfig`), `self.bit_class(name)`,
  `control.roles.RoleClass`.
- Produces: each `list_view()` row gains a `"roles"` key:
  `{"scored": int, "shared_open": bool, "jam_open": bool} | None`.
  `None` means "could not summarize" (constructor or role_table raised).
  Task 4 (Load picker) renders this. ROOM-class roles are never counted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bit_registry.py`:

```python
class _RaisingBit:
    """Constructor raises: list_view must degrade to roles=None, never
    propagate."""
    def __init__(self, config=None):
        raise RuntimeError("boom")


def test_list_view_carries_a_role_summary_for_testbit():
    registry = BitRegistry.discover()
    row = next(r for r in registry.list_view() if r["name"] == "TestBit")
    # TestBit: scored shared 'player' + unscored jam 'jammer' (+ hidden ROOM
    # roles, which must NOT be counted).
    assert row["roles"] == {"scored": 1, "shared_open": True, "jam_open": True}


def test_list_view_role_summary_counts_unique_capacity():
    registry = BitRegistry.discover()
    row = next(r for r in registry.list_view() if r["name"] == "MetronomeBit")
    # MetronomeBit: one UNIQUE scored role, capacity 2, no jam.
    assert row["roles"] == {"scored": 2, "shared_open": False, "jam_open": False}


def test_list_view_role_summary_is_none_when_the_bit_raises(monkeypatch):
    registry = BitRegistry.discover()
    monkeypatch.setattr(registry, "bit_class", lambda name: _RaisingBit)
    for row in registry.list_view():
        assert row["roles"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_bit_registry.py -v -k role_summary or carries_a_role`
Expected: FAIL with `KeyError: 'roles'`.

- [ ] **Step 3: Implement**

In `control/bit_registry.py`, add a helper and call it from `list_view`
(inside the row-building loop, after the existing keys):

```python
def _role_summary(self, name: str, config) -> dict | None:
    """Best-effort scored/jam summary for the Load picker. Instantiates
    the Bit class (first console connect pays the import); any failure
    yields None rather than breaking discovery -- a broken Bit already
    fails loudly at load_bit."""
    from control.roles import RoleClass
    try:
        bit = self.bit_class(name)(config=config)
        table = bit.role_table
    except Exception:
        return None
    scored = 0
    shared_open = False
    jam_open = False
    for role in table.roles.values():
        if role.role_class == RoleClass.ROOM:
            continue                     # never leak the Room role
        if role.role_class == RoleClass.JAM:
            jam_open = True
        elif role.scored:
            scored += role.capacity if role.capacity is not None else 0
            if role.capacity is None:
                shared_open = True
        if role.role_class == RoleClass.SHARED and role.scored:
            shared_open = True
    return {"scored": scored, "shared_open": shared_open, "jam_open": jam_open}
```

Note: check `Role`'s actual fields first (`control/roles.py`) — `capacity`
is `None` for unbounded shared roles; TestBit's `player` is a scored SHARED
role with capacity 1 or None — **adjust the two expected dicts in Step 1 to
the real declarations after reading `bits/test/test_bit.py` and
`bits/metronome/metronome_bit.py`**, keeping the shape
`{"scored": int, "shared_open": bool, "jam_open": bool}`. The shape is the
contract; the exact numbers must match the real role tables.

In `list_view`'s row dict add: `"roles": self._role_summary(name, config),`.

- [ ] **Step 4: Run the full offline suite**

Run: `.venv/bin/python -m pytest tests -x -q`
Expected: PASS (existing `list_view` consumers ignore the new key; the
`--list-bits` CLI prints selected fields only and is unchanged).

- [ ] **Step 5: Commit**

```bash
git add control/bit_registry.py tests/test_bit_registry.py
git commit -m "feat(console): bits_listed rows carry a best-effort role summary"
```

---

### Task 2: Static scaffold — tokens CSS, shell HTML, fonts, allowlist

**Files:**
- Create: `console/static/terrarium.css`
- Create: `console/static/fonts/` (6 TTFs, see Step 2)
- Rewrite: `console/static/index.html`
- Delete: `console/static/style.css`, `console/static/console.js`,
  `console/static/room.js`, `console/static/triggers.js`
- Modify: `console/server.py` (`_CONTENT_TYPES`)
- Rewrite: `tests/test_console_static.py`
- Delete: `tests/js/bits_panel_behavior.test.js`,
  `tests/js/room_panel_behavior.test.js`,
  `tests/js/trigger_panel_behavior.test.js`,
  `tests/js/console_script_isolation.test.js`,
  `tests/js/console_full_stack.test.js`
- Modify: `tests/test_console_script_isolation.py` (glob-driven runner)

**Interfaces:**
- Produces: `index.html` with the three-region shell (`.topbar`, `.shell` >
  `.sidebar` + `.content` > `.maincol` + `.rail`), empty mount points:
  `#bitPanel` (sidebar), `#roomCard`, `#bitStatusCard` (maincol),
  `#registrationCard`, `#devicesCard`, `#rolesCard`, `#logCard` (rail),
  `#overlayMount` (body-level, for the Load picker), topbar spans
  `#connChip`, `#roomChip`. Loads exactly one script:
  `<script type="module" src="shell.js"></script>`.
- Produces: `terrarium.css` — the complete design system; class names are
  the mockup's (`.chip`, `.tag`, `.btn`, `.card`, `.acc`, `.inst*`,
  `.trig*`, `.phase`, `.detail`, `.blockrow`, `.zones`, `.log`, `.pick*`,
  `.overlay`, `.meter`, `.devrow`, `.rolerow`, `.statusgrid`, `.fired-line`,
  `.errflash`, `.dimmed`). Later tasks write DOM that uses these names.

- [ ] **Step 1: Write the failing static tests**

Rewrite `tests/test_console_static.py`:

```python
"""The console's static assets. NO build step: a venue box never needs npm.
As of the 2026-08-25 redesign the front end is ES modules with one entry."""

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "console" / "static"

MODULES = {"wire.js", "shell.js", "bit.js", "surface.js", "triggers.js",
           "rail.js"}


def _text_assets() -> str:
    return "\n".join(p.read_text() for p in sorted(STATIC.glob("*"))
                     if p.suffix in (".html", ".js", ".css"))


def test_no_external_asset_fetches_anywhere():
    for needle in ("http://", "https://", "//cdn", "src=\"//",
                   "fonts.googleapis", "@import url"):
        assert needle not in _text_assets(), f"external reference: {needle}"


def test_the_expected_files_exist():
    names = {p.name for p in STATIC.glob("*") if p.is_file()}
    assert {"index.html", "terrarium.css"} | MODULES <= names
    # the old plain-script front end is gone
    assert not {"style.css", "console.js", "room.js"} & names


def test_fonts_are_self_hosted_and_servable():
    fonts = {p.name for p in (STATIC / "fonts").glob("*")}
    assert any("LondrinaSolid" in f for f in fonts)
    assert any("AtkinsonHyperlegible" in f for f in fonts)
    assert any("JetBrainsMono" in f for f in fonts)
    from console.server import _CONTENT_TYPES
    for f in fonts:
        assert Path(f).suffix in _CONTENT_TYPES, f"{f} not servable"


def test_index_loads_exactly_one_module_entry():
    html = (STATIC / "index.html").read_text()
    import re
    tags = re.findall(r"<script[^>]*>", html)
    assert len(tags) == 1
    assert 'type="module"' in tags[0] and 'src="shell.js"' in tags[0]


def test_every_js_file_is_an_es_module():
    """Module isolation by construction: no shared global scope exists, so
    the 2026-08-19 buildCard collision class is structurally impossible."""
    for name in MODULES:
        text = (STATIC / name).read_text()
        assert ("export " in text) or ("import " in text), f"{name} is not a module"


def test_css_defines_the_status_palette_and_faces():
    css = (STATIC / "terrarium.css").read_text()
    for token in ("#7a9e6e", "#d96680", "#c07850",   # sage/rose/terracotta
                  "Londrina Solid", "Atkinson Hyperlegible", "JetBrains Mono"):
        assert token in css
```

Also modify `tests/test_console_script_isolation.py`: replace the
hardcoded `@pytest.mark.parametrize` list with a glob so per-task test
files are picked up automatically:

```python
JS_TESTS = sorted(p.name for p in (ROOT / "tests" / "js").glob("*.test.js"))


@pytest.mark.skipif(NODE is None, reason="node not found on this box")
@pytest.mark.parametrize("script", JS_TESTS)
def test_console_scripts(script):
    ...  # body unchanged
```

- [ ] **Step 2: Gather fonts**

Copy from the design system (no conversion; TTF is the spec-sanctioned
fallback and `.ttf` joins the allowlist):

```bash
DS=~/projects/mm-documents/"Musical Mycology Design System"/fonts
mkdir -p console/static/fonts
cp "$DS/LondrinaSolid-Regular.ttf" "$DS/LondrinaSolid-Black.ttf" \
   "$DS/AtkinsonHyperlegible-Regular.ttf" "$DS/AtkinsonHyperlegible-Bold.ttf" \
   console/static/fonts/
```

JetBrains Mono (OFL) — fetch the TTFs once at dev time (any of: a local
checkout, `brew --prefix`/font caches, or download the official release
zip from github.com/JetBrains/JetBrainsMono and copy
`fonts/ttf/JetBrainsMono-Regular.ttf` and `JetBrainsMono-Bold.ttf` in).
Committed as files; runtime never fetches.

- [ ] **Step 3: Server allowlist**

In `console/server.py` `_CONTENT_TYPES` add:

```python
    ".ttf": "font/ttf",
```

And fix asset loading for the new subdirectory: `__init__` currently reads
only top-level files via `_STATIC_DIR.iterdir()`. Change the loop to:

```python
        for path in sorted(_STATIC_DIR.rglob("*")):
            content_type = _CONTENT_TYPES.get(path.suffix)
            if path.is_file() and content_type is not None:
                self._assets[path.name] = (path.read_bytes(), content_type)
```

`_process_request` already resolves by basename, so `fonts/X.ttf` requests
resolve to the `X.ttf` key. Add a server test in
`tests/test_console_server.py`: GET `/fonts/JetBrainsMono-Regular.ttf`
returns 200 with `font/ttf` (follow the file's existing HTTP-test pattern).

- [ ] **Step 4: Write `terrarium.css` and `index.html`**

`terrarium.css`: copy the entire `<style>` block from
`docs/mockups/console-redesign-mockup.html`, then:
1. Replace the Google-Fonts assumption with local `@font-face` rules at the
   top (family names identical to the mockup's `--f-disp/--f-body/--f-mono`
   stacks):

```css
@font-face { font-family: "Londrina Solid"; src: url("fonts/LondrinaSolid-Regular.ttf") format("truetype"); font-weight: 400; font-display: swap; }
@font-face { font-family: "Londrina Solid"; src: url("fonts/LondrinaSolid-Black.ttf") format("truetype"); font-weight: 900; font-display: swap; }
@font-face { font-family: "Atkinson Hyperlegible"; src: url("fonts/AtkinsonHyperlegible-Regular.ttf") format("truetype"); font-weight: 400; font-display: swap; }
@font-face { font-family: "Atkinson Hyperlegible"; src: url("fonts/AtkinsonHyperlegible-Bold.ttf") format("truetype"); font-weight: 700; font-display: swap; }
@font-face { font-family: "JetBrains Mono"; src: url("fonts/JetBrainsMono-Regular.ttf") format("truetype"); font-weight: 400; font-display: swap; }
@font-face { font-family: "JetBrains Mono"; src: url("fonts/JetBrainsMono-Bold.ttf") format("truetype"); font-weight: 700; font-display: swap; }
```

2. Drop the mockup-only `.refstrip` / `.reflabel` / `.refrow` rules.
3. Add the two runtime-only classes the mockup lacks:

```css
/* whole-page stale-data signal while the socket is down */
body.dimmed .shell { opacity: 0.8; }
/* refusal flash on the control that sent a refused command */
.errflash { box-shadow: 0 0 0 2.5px var(--rose); }
```

`index.html` (complete file):

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Terrarium Console</title>
<link rel="stylesheet" href="terrarium.css">
</head>
<body>
<div class="topbar">
  <span class="wordmark"><span class="orn">&#10022;</span> Terrarium Console</span>
  <span id="connChip" class="chip dim">connecting&hellip;</span>
  <span class="spacer"></span>
  <span id="roomChip" class="chip dim" hidden></span>
</div>
<div class="shell">
  <div class="sidebar">
    <p class="navlabel">Loaded Bit</p>
    <div id="bitPanel"></div>
  </div>
  <div class="content">
    <div class="maincol">
      <div id="roomCard" class="card"></div>
      <div id="bitStatusCard" class="card" hidden></div>
    </div>
    <div class="rail">
      <div id="registrationCard" class="card"></div>
      <div id="devicesCard" class="card"></div>
      <div id="rolesCard" class="card"></div>
      <div id="logCard" class="card"></div>
    </div>
  </div>
</div>
<div id="overlayMount"></div>
<script type="module" src="shell.js"></script>
</body>
</html>
```

Create six placeholder modules so the static tests pass this task (each
replaced by its own task): `wire.js`, `shell.js`, `bit.js`, `surface.js`,
`triggers.js`, `rail.js`, each containing for now only its final export
signature, e.g. `shell.js`:

```js
// Entry point. Panels are wired in as their tasks land.
import "./wire.js";
export {};
```

and for the others `export function init() {}` (exact final signatures in
their tasks; a placeholder module that exports the right name keeps
`import` graphs valid while panels land one by one).

- [ ] **Step 5: Delete the old front end and its behavior tests**

```bash
git rm console/static/style.css console/static/console.js \
       console/static/room.js console/static/triggers.js \
       tests/js/bits_panel_behavior.test.js tests/js/room_panel_behavior.test.js \
       tests/js/trigger_panel_behavior.test.js \
       tests/js/console_script_isolation.test.js tests/js/console_full_stack.test.js
```

- [ ] **Step 6: Run the suite**

Run: `.venv/bin/python -m pytest tests -x -q`
Expected: PASS. (`tests/test_console_wiring.py`, `test_console_agent.py`
etc. never read the static files' contents; `test_console_static.py` is the
rewritten version; the JS-runner test now globs an empty-but-growing set.)

- [ ] **Step 7: Commit**

```bash
git add -A console/static tests/test_console_static.py \
        tests/test_console_script_isolation.py tests/test_console_server.py \
        console/server.py
git commit -m "feat(console): dark-brand static scaffold, fonts, module entry; retire the old panels"
```

---

### Task 3: `wire.js` + `shell.js` — socket, dispatch, top bar, refusal feedback

**Files:**
- Rewrite: `console/static/wire.js`
- Rewrite: `console/static/shell.js`
- Test: `tests/js/wire_and_shell.test.js`

**Interfaces:**
- Produces (`wire.js`):
  - `on(event, handler)` — register `handler(msg)` for a wire event name;
    multiple handlers per event allowed, called in registration order.
  - `send(command, extra = {}, sourceEl = null)` — JSON-send
    `{command, ...extra}` when open; remembers `sourceEl` per command name
    for refusal feedback.
  - `flashRefusal(command, message)` — adds `.errflash` to the remembered
    `sourceEl` for 6s and appends an inline `<span class="inline-err">`;
    exported so tests can drive it.
  - `connect({WebSocketImpl = WebSocket, retryMs = 1000} = {})` — starts
    the connect/reconnect loop; injectable for tests.
  - `connectionState` events: dispatches synthetic events `"_open"` and
    `"_closed"` (with `{attempts}`) through the same `on()` registry.
- Produces (`shell.js`): imports every panel module, calls each `init()`,
  renders `#connChip` / `#roomChip`, toggles `body.dimmed`, and handles
  `error` events by calling `flashRefusal` and forwarding to the log via
  `rail.js`'s `logLine(level, message)` (Task 6 defines it; the placeholder
  export exists now).
- Consumes: DOM ids from Task 2's `index.html`.

- [ ] **Step 1: Write the failing node test**

`tests/js/wire_and_shell.test.js` — the new harness pattern: set global DOM
stubs, then dynamically `import()` the real modules. (Node runs ES modules
natively; `node:test` is not needed — plain asserts, exit non-zero on
throw, same as the deleted suite.)

```js
"use strict";
// Run directly: node tests/js/wire_and_shell.test.js
const assert = require("node:assert");

// -- minimal DOM stub ------------------------------------------------------
function el() {
  return {
    children: [], classList: (() => { const s = new Set(); return {
      add: (c) => s.add(c), remove: (c) => s.delete(c),
      contains: (c) => s.has(c), toggle: (c, v) => v ? s.add(c) : s.delete(c),
    }; })(),
    style: {}, dataset: {}, hidden: false,
    textContent: "", innerHTML: "", className: "", value: "",
    appendChild(child) { this.children.push(child); return child; },
    insertBefore(child) { this.children.unshift(child); return child; },
    remove() {}, setAttribute() {}, addEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
    getContext: () => ({ clearRect() {}, beginPath() {}, arc() {},
                         fill() {}, stroke() {}, fillRect() {} }),
  };
}
const byId = new Map();
globalThis.document = {
  getElementById: (id) => byId.get(id) ?? byId.set(id, el()).get(id),
  createElement: () => el(),
  body: el(),
};
globalThis.matchMedia = () => ({ matches: true });   // reduced motion: no timers
globalThis.addEventListener = () => {};

class FakeSocket {
  constructor(url) { FakeSocket.instances.push(this); this.url = url;
    this.sent = []; this.readyState = 1; FakeSocket.OPEN = 1; }
  send(s) { this.sent.push(JSON.parse(s)); }
}
FakeSocket.instances = [];
globalThis.WebSocket = FakeSocket;

(async () => {
  const wire = await import("../../console/static/wire.js");

  // dispatch: two handlers, in order
  const calls = [];
  wire.on("snapshot", (m) => calls.push(["a", m.state]));
  wire.on("snapshot", (m) => calls.push(["b", m.state]));
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  sock.onmessage({ data: JSON.stringify({ event: "snapshot", state: "IDLE" }) });
  assert.deepStrictEqual(calls, [["a", "IDLE"], ["b", "IDLE"]]);

  // send stamps the payload and remembers the source element
  const btn = el();
  wire.send("run", {}, btn);
  assert.deepStrictEqual(sock.sent.at(-1), { command: "run" });
  wire.flashRefusal("run", "invalid transition");
  assert.ok(btn.classList.contains("errflash"));

  // an unknown event is a no-op, never a throw
  sock.onmessage({ data: JSON.stringify({ event: "never_heard_of_it" }) });

  console.log("wire_and_shell: ok");
})().catch((e) => { console.error(e); process.exit(1); });
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/js/wire_and_shell.test.js`
Expected: FAIL (placeholder `wire.js` exports nothing).

- [ ] **Step 3: Implement `wire.js`**

```js
// The console's only socket-touching module. Everything else registers
// handlers here and renders DOM; nothing else may construct a WebSocket.
const handlers = new Map();          // event name -> [fn, ...]
const sources = new Map();           // command name -> last source element
let ws = null;
let attempts = 0;

export function on(event, fn) {
  if (!handlers.has(event)) handlers.set(event, []);
  handlers.get(event).push(fn);
}

function dispatch(event, msg) {
  for (const fn of handlers.get(event) ?? []) fn(msg);
}

export function send(command, extra = {}, sourceEl = null) {
  if (sourceEl) sources.set(command, sourceEl);
  if (ws && ws.readyState === (ws.constructor.OPEN ?? 1)) {
    ws.send(JSON.stringify(Object.assign({ command }, extra)));
  }
}

export function flashRefusal(command, message) {
  const elx = sources.get(command);
  if (!elx) return;
  elx.classList.add("errflash");
  const note = document.createElement("span");
  note.className = "inline-err";
  note.textContent = message;
  elx.parentNode?.appendChild?.(note);
  setTimeout(() => { elx.classList.remove("errflash"); note.remove(); }, 6000);
}

export function connect({ WebSocketImpl = WebSocket, retryMs = 1000 } = {}) {
  ws = new WebSocketImpl(`ws://${location?.host ?? ""}/ws`);
  ws.onopen = () => { attempts = 0; dispatch("_open", {}); };
  ws.onclose = () => {
    attempts += 1;
    dispatch("_closed", { attempts });
    setTimeout(() => connect({ WebSocketImpl, retryMs }), retryMs);
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    dispatch(msg.event, msg);
  };
}
```

(Adjust for the node stub: guard `location` as above; `parentNode` optional-
chained so a detached test element is fine.)

- [ ] **Step 4: Implement `shell.js`**

```js
// Entry point: wires the top bar, the disconnect dim, and every panel.
import * as wire from "./wire.js";
import { init as initBit } from "./bit.js";
import { init as initSurface } from "./surface.js";
import { init as initTriggers } from "./triggers.js";
import { init as initRail, logLine } from "./rail.js";

const conn = document.getElementById("connChip");
const roomChip = document.getElementById("roomChip");

wire.on("_open", () => {
  conn.className = "chip sage";
  conn.textContent = "Connected";
  document.body.classList.remove("dimmed");
});
wire.on("_closed", ({ attempts }) => {
  conn.className = "chip rose";
  conn.textContent = `Disconnected — retrying (${attempts})`;
  document.body.classList.add("dimmed");
});

function paintRoomChip(room) {
  if (!room) { roomChip.hidden = true; return; }
  roomChip.hidden = false;
  const bound = room.fixtures.filter((f) => f.dev).length;
  const total = room.fixtures.length;
  roomChip.className = bound === total ? "chip gold" : "chip terra";
  roomChip.textContent =
    `${room.room_type} · ${bound}/${total} fixtures bound`;
}
wire.on("snapshot", (m) => paintRoomChip(m.room));
wire.on("room_changed", (m) => paintRoomChip(m.room));

wire.on("error", (m) => {
  wire.flashRefusal(m.command, m.message);
  logLine("error", `${m.command}: ${m.message}`);
});

initBit(); initSurface(); initTriggers(); initRail();
wire.connect();
```

Task 2's placeholder `rail.js` must already export `logLine` (no-op) so
this import resolves before Task 6; update the placeholder now:
`export function init() {}\nexport function logLine() {}`.

- [ ] **Step 5: Run node test, then the pytest wrapper**

Run: `node tests/js/wire_and_shell.test.js` → `ok`.
Run: `.venv/bin/python -m pytest tests/test_console_script_isolation.py tests/test_console_static.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add console/static/wire.js console/static/shell.js console/static/rail.js tests/js/wire_and_shell.test.js
git commit -m "feat(console): wire.js socket/dispatch core and shell entry with refusal feedback"
```

---

### Task 4: `bit.js` — sidebar Loaded-Bit panel, Load picker, Bit status card

**Files:**
- Rewrite: `console/static/bit.js`
- Test: `tests/js/bit_panel.test.js`

**Interfaces:**
- Consumes: `wire.on/send`; events `snapshot` (`state`, `loaded_bit`,
  `bit_status`, `roles`), `bits_listed` (`bits` rows incl. Task 1's
  `roles` key, `errors`), `state_changed` (`state`, `loaded_bit`),
  `bit_status` (`status`); CSS classes from Task 2; mount `#bitPanel`,
  `#bitStatusCard`, `#overlayMount`.
- Produces: `init()`. Sends `run`, `abort`, `load_bit {name, overrides?}`.

- [ ] **Step 1: Write the failing node test** (same harness prelude as
  Task 3's test; factor it into `tests/js/_dom_stub.js` and
  `require("./_dom_stub.js")` from both — the stub file exports
  `{el, byId, FakeSocket}` and installs the globals on require).

```js
"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

(async () => {
  const wire = await import("../../console/static/wire.js");
  const bit = await import("../../console/static/bit.js");
  bit.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();

  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // empty state
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [],
         registration: [], devices: [], bit_status: {}, room: null, triggers: [] });
  assert.ok(byId.get("bitPanel").innerHTML.includes("No Bit loaded"));

  // bits_listed builds the picker data (hidden until Load is clicked)
  send({ event: "bits_listed", errors: [{ path: "bits/broken/bit.toml", message: "bad" }],
         bits: [{ name: "MetronomeBit", display_name: "Metronome", version: "1.0.0",
                  kind: "r_game", hidden: false, description: "Call-and-response",
                  room_types: ["DEMO"], notes: "",
                  start: { when: "players", min_scored: 2, timeout_seconds: 120, on_timeout: "start" },
                  roles: { scored: 2, shared_open: false, jam_open: false } }] });

  // loading a bit paints the identity card and phase chip
  send({ event: "state_changed", state: "SETUP", loaded_bit: "MetronomeBit" });
  const panel = byId.get("bitPanel");
  assert.ok(panel.innerHTML.includes("Metronome"));
  assert.ok(panel.innerHTML.includes("Waiting Room"));

  // phase chip follows further transitions; buttons never disabled
  send({ event: "state_changed", state: "RUNNING", loaded_bit: "MetronomeBit" });
  assert.ok(panel.innerHTML.includes("Running"));

  // bit_status paints the status card and empty status hides it
  send({ event: "bit_status", status: { turn: "ie2", cycle: 2, tap_errors_ms: [1.5, -2] } });
  const card = byId.get("bitStatusCard");
  assert.strictEqual(card.hidden, false);
  assert.ok(card.innerHTML.includes("ie2"));
  assert.ok(card.innerHTML.includes("1.5"));          // list formatted, not [object
  send({ event: "bit_status", status: {} });
  assert.strictEqual(card.hidden, true);

  console.log("bit_panel: ok");
})().catch((e) => { console.error(e); process.exit(1); });
```

Note on the stub: this test asserts against `innerHTML` strings, so the
stub's `appendChild` must fold children's markup into the parent's
`innerHTML`. Give `_dom_stub.js`'s `el()` a real serializer: track
`tagName`, `className`, `textContent`, children, and implement a lazy
`get innerHTML()` that renders them recursively. ~40 lines; it is the load-
bearing piece of the new harness and every later panel test reuses it.

- [ ] **Step 2: Run to verify it fails** — `node tests/js/bit_panel.test.js`.

- [ ] **Step 3: Implement `bit.js`**

Structure (full DOM per the mockup's sidebar; key logic):

```js
import * as wire from "./wire.js";

const PHASES = {
  LOADING: ["Loaded", "gold"], LOADED: ["Loaded", "gold"],
  SETUP: ["Waiting Room — registration open", "sage"],
  RUNNING: ["Running", "sage"],
  COMPLETING: ["Wrapping up", "gold"], UNLOADING: ["Wrapping up", "gold"],
};

let bits = [];            // last bits_listed rows
let errors = [];
let bitsSignature = null; // rule 1: bits_listed gated by signature
let state = "IDLE";
let loadedName = null;

function startText(start) { /* same formatting as the old console.js:
  "2 players, 120s timeout" | "operator" | "immediately" */ }

function rolesText(r) {
  if (!r) return "—";
  const jam = r.jam_open ? "jam open" : "no jam";
  return `${r.scored} scored · ${jam}`;
}

function render() { /* paints #bitPanel:
  - IDLE/no loadedName: "No Bit loaded" empty state + Load button
  - else: identity row (art placeholder ✦, display_name, name·vversion,
    kind tag), button row (Run gold / Abort rose two-tap / Load outline),
    phase chip from PHASES[state] with SETUP sub-line startText(),
    details dl (Rooms with active bold, Roles rolesText, About, Notes).
  Buttons wired with wire.send("run", {}, btn) etc.
  Abort: first click sets button text "Confirm abort?" + 4s revert timer;
  second click within window sends abort. */ }

function openPicker() { /* builds the overlay into #overlayMount from
  `bits` + `errors`: one .pick card per bit (dimmed when hidden, "Loaded"
  chip when name === loadedName), meta line `name · vversion · rooms: X ·
  starts: startText · rolesText`, description, an <details class="ovr">
  overrides expander containing key/value input rows (three empty rows,
  each a pair of text inputs: dotted key e.g. "rhythm.bpm", value), Load
  button per card sending load_bit with overrides parsed as
  {table: {key: value}} from the dotted keys (numbers coerced via
  Number() when finite), .pick.err rows for errors. Esc/✕/outside-click
  removes the overlay. */ }

function renderStatus(status) { /* #bitStatusCard: hidden when empty.
  One .stat per key: uppercase key label + value via fmt():
  fmt(v): Array -> v.map(fmt).join(" "); number -> String(v);
  object -> Object.entries one level "k=v"; else String(v). */ }

export function init() {
  wire.on("snapshot", (m) => {
    state = m.state; loadedName = m.loaded_bit; render();
    renderStatus(m.bit_status || {});
  });
  wire.on("bits_listed", (m) => {
    const sig = JSON.stringify([m.bits, m.errors]);
    if (sig === bitsSignature) return;
    bitsSignature = sig; bits = m.bits || []; errors = m.errors || [];
    render();
  });
  wire.on("state_changed", (m) => { state = m.state; loadedName = m.loaded_bit; render(); });
  wire.on("bit_status", (m) => renderStatus(m.status || {}));
}
```

Write the full bodies (no elided comments in the shipped file); DOM
construction via `document.createElement` + `textContent` (never string
concatenation into innerHTML — same XSS discipline as the old console.js).

- [ ] **Step 4: Run the test and wrapper**

`node tests/js/bit_panel.test.js` → ok; `.venv/bin/python -m pytest tests/test_console_script_isolation.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add console/static/bit.js tests/js/bit_panel.test.js tests/js/_dom_stub.js
git commit -m "feat(console): sidebar Loaded-Bit panel, Load picker with overrides, Bit status card"
```

---

### Task 5: `surface.js` — Room card: LED dot rows, zones, binding, Instruments accordion

**Files:**
- Rewrite: `console/static/surface.js`
- Test: `tests/js/surface_panel.test.js`

**Interfaces:**
- Consumes: events `snapshot`/`room_changed` (`room`: `room_type`,
  `fixtures[{name,pixel_count,channel_start,channel_count,zones,dev}]`,
  `capability{pixel_count,color_order,zones}`, `instruments[]`,
  `controllers{}`), `room_frame` (`dev`, `channels`); mount `#roomCard`;
  `wire.send` for `arm_room`/`release_room`.
- Produces: `init()`, plus `export function _blockRowsFor(fixture)` (pure,
  tested directly): splits a fixture into rows — one per 144 px, or one row
  if `pixel_count <= 160` — returning `[{label, start, count}]`. (Blocks
  are not on the wire; the row split is a rendering rule: DEMO's 864 px
  fixture → 6 rows matching its physical meters, TEST's 60/30 px fixtures
  → one row each. Labels are `px start..end` — honest, since block *names*
  are not in `room_view`.)

- [ ] **Step 1: Write the failing node test**

```js
"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const ROOM = {
  room_type: "TEST",
  capability: { pixel_count: 90, color_order: "GRB",
    zones: [{ name: "main.left", start: 0, count: 20 }] },
  fixtures: [
    { name: "main", pixel_count: 60, channel_start: 0, channel_count: 180,
      zones: [{ name: "main.left", start: 0, count: 20 },
              { name: "main.center", start: 20, count: 20 },
              { name: "main.right", start: 40, count: 20 }], dev: "sim-room-main" },
    { name: "accent", pixel_count: 30, channel_start: 180, channel_count: 90,
      zones: [{ name: "accent.low", start: 0, count: 15 },
              { name: "accent.high", start: 15, count: 15 }], dev: null },
  ],
  instruments: [
    { kind: "light", instrument: "aurora", target: "primary",
      params: { hue: 0.33 }, lanes: [{ source: "cc:74", dest: "hue" }] },
    { kind: "audio", instrument: "flsyn", program: 115,
      lanes: [{ source: "cc:74", dest: "cc:74" }] },
  ],
  controllers: { 74: 93 },
};

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");

  // pure row-split rule
  assert.deepStrictEqual(
    surface._blockRowsFor({ pixel_count: 864 }).map((r) => r.count),
    [144, 144, 144, 144, 144, 144]);
  assert.deepStrictEqual(
    surface._blockRowsFor({ pixel_count: 60 }).map((r) => r.count), [60]);

  surface.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", state: "RUNNING", loaded_bit: "TestBit", roles: [],
         registration: [], devices: [], bit_status: {}, triggers: [], room: ROOM });
  const card = byId.get("roomCard");
  assert.ok(card.innerHTML.includes("TEST"));
  assert.ok(card.innerHTML.includes("main.center (20..39)"));
  assert.ok(card.innerHTML.includes("Not bound"));       // accent unbound
  assert.ok(card.innerHTML.includes("sim-room-main"));   // main bound
  assert.ok(card.innerHTML.includes("aurora"));
  assert.ok(card.innerHTML.includes("= 93"));            // live lane value
  assert.ok(card.innerHTML.includes("Instruments"));     // accordion, not Controls

  // a controllers-only change must NOT rebuild fixture strips (rule 1/3):
  const stripBefore = surface._canvasFor("sim-room-main");
  send({ event: "room_changed",
         room: { ...ROOM, controllers: { 74: 12 } } });
  assert.strictEqual(surface._canvasFor("sim-room-main"), stripBefore);
  assert.ok(card.innerHTML.includes("= 12"));

  // frames: GRB decode; unknown dev is a no-op (rule 9)
  send({ event: "room_frame", dev: "sim-room-main",
         channels: [255, 0, 0].concat(Array(177).fill(0)) });  // G=255 first px
  assert.deepStrictEqual(surface._lastPaint("sim-room-main")[0], [0, 255, 0]); // [r,g,b]
  send({ event: "room_frame", dev: "ghost", channels: [1, 2, 3] }); // no throw

  // no Room configured
  send({ event: "room_changed", room: null });
  assert.ok(card.innerHTML.includes("No Room configured"));

  console.log("surface_panel: ok");
})().catch((e) => { console.error(e); process.exit(1); });
```

`_canvasFor(dev)` / `_lastPaint(dev)` are small exported test hooks:
`_canvasFor` returns the canvas element list for that dev's fixture,
`_lastPaint` the last decoded `[r,g,b]` array per pixel. Cheap, and they
make rules 1/3/6/9 assertable without a real canvas.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `surface.js`**

Key mechanics (write full code, DOM per the mockup's Room card):

- Per-fixture signature (rule 3): `{pixel_count, zones}` compared as JSON;
  a fixture whose signature is unchanged is NOT rebuilt; binding chip and
  header text update in place via `textContent`. Rebuilt fixtures re-insert
  before the nearest later surviving fixture block (rule 4) — port this
  loop directly from the old `room.js` (git history has it; the algorithm
  is in spec section 6 and the old file's comments).
- `_blockRowsFor(fixture)`: `const per = 144; if (fixture.pixel_count <= 160) return [{label: 'px 0..' + (fixture.pixel_count-1), start: 0, count: fixture.pixel_count}]; ...` split into `ceil(n/144)` rows, last row the remainder.
- Each row: `<div class="blockrow"><span class="blk">px S..E</span><canvas class="strip"></canvas></div>`; canvases stored per fixture in a Map keyed by the fixture's bound dev (re-keyed on `room_changed`).
- `room_frame` handler: look up fixture by dev (map rebuilt each
  `renderRoom`, rule: unknown dev → return). Decode GRB
  (`g = channels[i*3]; r = channels[i*3+1]; b = channels[i*3+2]`), record
  into `_lastPaint`, then paint each row's canvas: one `arc()` dot per
  pixel, dark pixels (r+g+b < 12) drawn as a stroked ring (the dead-socket
  treatment), others filled `rgb(r,g,b)`. No transitions (motion rule).
- Frames chip: sage `Live` if a frame arrived < 2000ms ago else dim
  `No frames`; updated on each frame and on a 1s interval (interval skipped
  under `matchMedia('(prefers-reduced-motion: reduce)')` is NOT right —
  liveness is state, not decoration; keep the interval always).
- Binding controls: bound → dev chip + Release with the two-tap confirm
  (shared helper `confirmTap(btn, label, fn)` — put it in `wire.js` and
  export it; bit.js's Abort uses the same helper); unbound → `Not bound`
  terra chip + Arm button revealing inline `window: [30]s` input +
  confirm, sending `arm_room {room_type, fixture: name, window_seconds}`;
  after sending, chip shows gold `Armed` until the next `room_changed`
  reports a dev.
- Instruments accordion: `<details class="acc" open>` summary
  `Instruments · N declared · live values`; instrument cards exactly as the
  mockup (LIGHT/AUDIO badge, target for light, program/drone/extra keys
  copied through for audio, one row per lane with live value from
  `controllers` when the lane source is `cc:N`).
- Triggers accordion: `<details class="acc" open id="triggersAcc">` with an
  empty `<div class="accbody" id="triggersMount"></div>` — Task 6 renders
  into it. The accordion element and summary (`Triggers`) belong to this
  card's structure and are created here, ONCE, outside the per-fixture
  rebuild path.

- [ ] **Step 4: Run test + wrapper; commit**

```bash
git add console/static/surface.js console/static/wire.js tests/js/surface_panel.test.js
git commit -m "feat(console): Room card with per-block LED dot rows, binding UI, Instruments accordion"
```

---

### Task 6: `triggers.js` + `rail.js` — trigger grid, registration, devices, roles, log

**Files:**
- Rewrite: `console/static/triggers.js`
- Rewrite: `console/static/rail.js`
- Test: `tests/js/triggers_and_rail.test.js`

**Interfaces:**
- Consumes: `#triggersMount` (Task 5), rail mounts (Task 2); events
  `snapshot`, `triggers_changed` (`triggers[]` per `control/trigger_view.py`:
  `name, description, target, condition{name,description,source,verb},
  script[{offset,kind,dev,status,data1,data2}|{offset,kind,play,dev,name,params}]`),
  `trigger_fired` (`fired{name,fired_by,declared_source,dev,devs,at,steps}`),
  `registration_changed` (`roles[{role,count,capacity}]`… match
  `uplink/protocol.py`'s builder exactly), `devices_changed`
  (`devices[{dev,name,role}]`), `log` (`level,message`), `bit_completed`.
- Produces: `triggers.init()`; `rail.init()`, `rail.logLine(level, message)`
  (already imported by shell.js).

- [ ] **Step 1: Write the failing node test** — one file, both modules:

```js
"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const TRIGGERS = [
  { name: "fireworks_player", description: "Celebratory flashes", target: "DEVICE",
    condition: { name: "phrase_success", description: "Player matches the call phrase",
                 source: "bit-adjudicated", verb: null },
    script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 },
             { offset: 1.4, kind: "light", dev: "@target", status: 176, data1: 70, data2: 0 }] },
  { name: "finale", description: "Closing sweep", target: "ROOM",
    condition: { name: "run_complete", description: "All cycles finished",
                 source: "bit-adjudicated", verb: null },
    script: [{ offset: 0.0, kind: "play", dev: "@target", name: "sweep", params: {} }] },
];

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");
  const triggers = await import("../../console/static/triggers.js");
  const rail = await import("../../console/static/rail.js");
  surface.init(); triggers.init(); rail.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", state: "RUNNING", loaded_bit: "MetronomeBit",
         roles: [], registration: [{ role: "player", count: 2, capacity: 2 }],
         devices: [{ dev: "ie1", name: "Tuneshroom 1", role: "player" },
                   { dev: "sim-room", name: "Room", role: null }],
         bit_status: {}, triggers: TRIGGERS,
         room: { room_type: "DEMO", capability: { pixel_count: 864,
                 color_order: "GRB", zones: [] }, fixtures: [], instruments: [],
                 controllers: {} } });

  const mount = byId.get("triggersMount");
  assert.ok(mount.innerHTML.includes("fireworks_player"));
  assert.ok(mount.innerHTML.includes("2 steps · 1.4s"));   // count + span
  assert.ok(mount.innerHTML.includes("never fired"));
  // device picker only on DEVICE targets, offering live devices
  assert.ok(mount.innerHTML.includes("ie1"));

  // a repeat triggers_changed with identical content must not rebuild (rule 1)
  const before = triggers._cardFor("fireworks_player");
  send({ event: "triggers_changed", triggers: TRIGGERS });
  assert.strictEqual(triggers._cardFor("fireworks_player"), before);

  // trigger_fired updates the one line, tags admin-manual
  send({ event: "trigger_fired", fired: { name: "finale", fired_by: "admin-manual",
         declared_source: "bit-adjudicated", dev: null, devs: ["sim-room"],
         at: 12.5, steps: 1 } });
  assert.ok(mount.innerHTML.includes("Admin manual"));

  // rail: registration meter, devices, log severities
  assert.ok(byId.get("registrationCard").innerHTML.includes("2/2"));
  assert.ok(byId.get("devicesCard").innerHTML.includes("Tuneshroom 1"));
  send({ event: "log", level: "error", message: "boom" });
  assert.ok(byId.get("logCard").innerHTML.includes("boom"));
  send({ event: "bit_completed", result: { phrases: 4 }, bit_name: "MetronomeBit" });
  assert.ok(byId.get("logCard").innerHTML.includes("phrases"));

  console.log("triggers_and_rail: ok");
})().catch((e) => { console.error(e); process.exit(1); });
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `triggers.js`**

Port the old `triggers.js` logic (git show HEAD~N or spec section 4.6) onto
the compact-card DOM from the mockup: signature-gated rebuild
(`JSON.stringify(list)`), `lastFired` map surviving rebuilds, device pickers
(ids only, selection preserved — port `fillDevicePicker` verbatim),
`stepText` for both cue kinds, script `<details>` per card with the summary
`${n} steps · ${maxOffset.toFixed(1)}s`, bottom-pinned `.firerow`, fired
line + left-edge class (`fired-admin` when last fire was admin-manual,
`fired` otherwise), `wire.send("fire_trigger", {name, dev?}, fireBtn)`.
Export `_cardFor(name)` returning the live card element (test hook).
`devices_changed` re-fills pickers only, never rebuilds cards.

- [ ] **Step 4: Implement `rail.js`**

Four renderers, DOM per mockup:
- Registration: `.rolerow` per role — name, class tag, scored chip, meter
  (`width: ${Math.min(100, count/capacity*100)}%`; unbounded capacity →
  no meter, count + `/∞`).
- Devices: `.devrow` per device — mono dev id, name, role tag or dim `—`.
- Roles & manifests: `<details class="refcard">` collapsed; body renders
  per-role instrument cards by REUSING surface.js's instrument-card builder
  — export `buildInstrumentCard(inst, controllers)` from `surface.js` and
  import it here (one component, two consumers, as the spec requires), plus
  a welcome line when `role.welcome` present.
- Log: `logLine(level, message)` appends a `.row` div (`.err` class +
  `lv-err` span for error, `lv-warn` for warn, `lv-info` otherwise) with a
  client-side `[HH:MM:SS]` stamp (`new Date().toTimeString().slice(0, 8)`),
  caps at 500 rows (`firstChild.remove()` beyond), auto-scrolls unless the
  pointer is over the log (`pointerenter`/`pointerleave` flag).
  Event wiring: `state_changed` → `logLine("info", "state → " + m.state)`;
  `log` → passthrough; `bit_completed` → `logLine("info", "bit completed: "
  + JSON.stringify(m.result))`.

- [ ] **Step 5: Run test + wrapper + full suite; commit**

```bash
git add console/static/triggers.js console/static/rail.js console/static/surface.js tests/js/triggers_and_rail.test.js
git commit -m "feat(console): compact trigger grid and right rail (registration, devices, roles, log)"
```

---

### Task 7: Full-stack JS test + live UAT + docs

**Files:**
- Create: `tests/js/full_stack.test.js`
- Modify: `docs/MM_TERRARIUM.md` (console section — via the mm-deepdive
  flow at closeout, not hand-edited here)

**Interfaces:**
- Consumes: everything.

- [ ] **Step 1: Write the full-stack node test**

`tests/js/full_stack.test.js`: import ALL six modules together (the
collision-class guard reborn for ESM — the only test that loads the whole
graph), replay a realistic session — the Task 4/5/6 snapshots followed by
`state_changed → RUNNING`, 3 `room_frame`s, a `trigger_fired`, an `error`
for `run`, `_closed` then `_open` with a fresh snapshot — and assert: no
throw anywhere, `body` gains and loses `dimmed`, and the reconnect
snapshot repopulates `#bitPanel` (its innerHTML non-empty and containing
the loaded bit name). Full code follows the Task 6 test's harness pattern.

- [ ] **Step 2: Run the entire offline suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS, no skips beyond the usual (node present on the dev boxes).

- [ ] **Step 3: Live UAT against the real stack**

**RUN ON: MYCOLOGICAL**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/priceless-booth-e6abb0
.venv/bin/python -m harness.run_stack --console-port 8772 --devices 2
```

Checklist (each item maps to a spec section):
1. Open the console; connection chip sage; Load picker lists 3 Bits with
   role summaries + the error row absent (no broken bit on disk).
2. Load Metronome with an override `rhythm.bpm = 60`; phase chip walks
   LOADED → Waiting Room; registration meter fills as the sim devices join;
   RUNNING starts on min players.
3. Room card: DEMO title, 6 dot rows painting live, zone bar, bound chip.
4. Collapse Instruments; fire `fireworks_room`; watch the array respond
   with the Fire button and dots on one screen; fired line tags Admin
   manual in terracotta.
5. Abort with the two-tap confirm; then Run while IDLE → rose flash on the
   Run button + reasoned line in the log (refusal path).
6. Kill run_stack; page dims + rose chip with attempt counter; restart;
   everything repopulates from snapshot.
7. Narrow the window to ~1000px: rail folds under main, nav + divider stay.

Record any failures as fix-forward commits on this branch before closeout.

- [ ] **Step 4: Commit any UAT fixes, then closeout**

Standard closeout per house rules: `finishing-a-development-branch`, with
the `mm-deepdive-sync` nudge updating `docs/MM_TERRARIUM.md`'s console
section (new file list, ES modules, the role-summary wire addition, the
test-lane follow-up pointer).
