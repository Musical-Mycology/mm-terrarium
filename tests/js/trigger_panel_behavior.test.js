"use strict";
// Behavioral test for console/static/triggers.js and console/static/console.js's
// dispatch of the two trigger events.
//
// Spec A's two Important defects reached a live browser past 843 passing tests
// because room.js was covered only by substring greps over its own source. The
// worst of them was renderRoom rebuilding #roomStrip on every room_changed, so
// the painted swatches were destroyed roughly four times per painted frame.
// trigger_fired is this panel's equivalent high-frequency event, so the
// scenario that matters most below is that a fire does NOT rebuild the card
// list.
//
// Drives the real shipped triggers.js against a small hand-rolled DOM stub
// under Node. No jsdom: this repo has no build step and nothing shipped may
// depend on npm.
//
// Run directly: node tests/js/trigger_panel_behavior.test.js
// Wired into pytest via tests/test_trigger_panel_behavior.py.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

// ---- DOM stub -----------------------------------------------------------
// Same shape as tests/js/room_panel_behavior.test.js's, plus `value` (the
// device picker is a <select>) and `onclick` (a plain property on these
// objects, so a scenario can invoke a button by calling node.onclick()).

function makeNode(tag) {
  const node = {
    tagName: tag,
    id: "",
    className: "",
    textContent: "",
    value: "",
    onclick: null,
    style: {},
    parentNode: null,
    _children: [],
  };
  Object.defineProperty(node, "children", {
    get() { return node._children; },
  });
  Object.defineProperty(node, "innerHTML", {
    get() { return node._children.length ? "(non-empty)" : ""; },
    set(value) {
      if (value !== "") {
        throw new Error('DOM stub only supports innerHTML = ""');
      }
      for (const child of node._children) child.parentNode = null;
      node._children = [];
    },
  });
  node.appendChild = (child) => {
    child.parentNode = node;
    node._children.push(child);
    return child;
  };
  node.remove = () => {
    if (node.parentNode) {
      const siblings = node.parentNode._children;
      const at = siblings.indexOf(node);
      if (at >= 0) siblings.splice(at, 1);
      node.parentNode = null;
    }
  };
  return node;
}

function findById(node, id) {
  if (node.id === id) return node;
  for (const child of node._children) {
    const found = findById(child, id);
    if (found) return found;
  }
  return null;
}

function findAll(node, predicate, out) {
  out = out || [];
  if (predicate(node)) out.push(node);
  for (const child of node._children) findAll(child, predicate, out);
  return out;
}

function newDocument() {
  const root = makeNode("body");
  const div = makeNode("div");
  div.id = "triggers";
  root.appendChild(div);
  return {
    _root: root,
    createElement: (tag) => makeNode(tag),
    createTextNode: (text) => ({ nodeType: 3, textContent: text }),
    getElementById: (id) => findById(root, id),
  };
}

// ---- fixtures -----------------------------------------------------------

const SWEEP = {
  name: "play_aurora",
  description: "A slow aurora sweep across the Room",
  target: "ROOM",
  condition: { name: "round_won", description: "User wins a round",
               source: "bit-adjudicated", verb: null },
  script: [
    { offset: 0.0, kind: "light", dev: "@room", status: 176, data1: 74, data2: 127 },
    { offset: 0.5, kind: "light", dev: "@room", status: 176, data1: 74, data2: 40 },
    { offset: 2.0, kind: "light", dev: "@room", status: 176, data1: 74, data2: 0 },
  ],
};

const FLASH = {
  name: "flash_device",
  description: "Flash the tapping device",
  target: "DEVICE",
  condition: { name: "tapped", description: "Player taps their Shroom",
               source: "gesture-verb", verb: "tap" },
  script: [
    { offset: 0.0, kind: "play", dev: "@target", name: "click", params: "" },
    { offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 74, data2: 127 },
  ],
};

// ---- harness ------------------------------------------------------------

let failures = 0;
function assert(cond, message) {
  if (!cond) {
    failures++;
    console.error(`FAIL: ${message}`);
  }
}

const triggersJsPath = path.join(__dirname, "..", "..", "console", "static",
                                 "triggers.js");
const triggersJsSource = fs.readFileSync(triggersJsPath, "utf8");

// Fresh document AND fresh triggers.js module state per scenario, by
// re-evaluating the shipped source in its own vm context. `send` is a spy:
// in the browser it is console.js's global, resolved at click time.
function scenario(name, testBody) {
  const sent = [];
  const sandbox = {
    document: newDocument(),
    console, assert, sent, SWEEP, FLASH, findAll,
    send: (command, extra) => sent.push([command, extra]),
  };
  sandbox.window = sandbox; // triggers.js is IIFE-wrapped and exports via
  // window.*; giving the sandbox its own window makes those names reachable
  // as bare globals again inside this vm context, the way a browser's
  // top-level `window` already does.
  vm.createContext(sandbox);
  try {
    vm.runInContext(triggersJsSource + "\n" + testBody, sandbox,
                    { filename: `triggers.js+${name}` });
  } catch (err) {
    failures++;
    console.error(`FAIL: scenario "${name}" threw: ${err.stack || err}`);
  }
}

scenario("renders one card per trigger with every script step visible", `
  renderTriggers([SWEEP, FLASH]);
  const cards = findAll(document.getElementById("triggerCards"),
                        (n) => n.className.split(" ").indexOf("card") >= 0);
  assert(cards.length === 2, "expected 2 cards, got " + cards.length);

  const steps = findAll(document.getElementById("triggers"),
                        (n) => n.className === "step");
  assert(steps.length === 5,
    "expected 5 rendered steps (3 + 2), got " + steps.length);

  const text = steps.map((s) => s.textContent).join("|");
  assert(text.indexOf("+0.00s") >= 0, "offsets should render, got " + text);
  assert(text.indexOf("cc:74 = 127") >= 0,
    "a light step should render as cc:<n> = <v>, got " + text);
  assert(text.indexOf('play "click"') >= 0,
    "a play step should render its sample name, got " + text);
`);

scenario("the card list survives a trigger_fired re-render", `
  renderTriggers([SWEEP, FLASH]);
  const list = document.getElementById("triggerCards");
  const childrenBefore = list.children.length;

  renderTriggerFired({ name: "play_aurora", condition: "round_won",
    fired_by: "admin-manual", declared_source: "bit-adjudicated",
    dev: null, devs: ["sim-room"], at: 1.0, steps: 3 });

  assert(document.getElementById("triggerCards") === list,
    "trigger_fired must not replace the card list node");
  assert(list.children.length === childrenBefore,
    "trigger_fired must not rebuild the cards: had " + childrenBefore +
    ", now " + list.children.length);
`);

scenario("a fire updates only its own card's status line", `
  renderTriggers([SWEEP, FLASH]);
  renderTriggerFired({ name: "play_aurora", condition: "round_won",
    fired_by: "admin-manual", declared_source: "bit-adjudicated",
    dev: null, devs: ["sim-room"], at: 1.0, steps: 3 });

  const fired = document.getElementById("triggerFired_play_aurora");
  const other = document.getElementById("triggerFired_flash_device");
  assert(fired.textContent.indexOf("ADMIN MANUAL") >= 0,
    "an admin-manual fire must be tagged, got " + fired.textContent);
  assert(other.textContent.indexOf("never fired") >= 0,
    "the other card must be untouched, got " + other.textContent);
`);

scenario("a gesture-verb fire is not tagged as admin manual", `
  renderTriggers([FLASH]);
  renderTriggerFired({ name: "flash_device", condition: "tapped",
    fired_by: "gesture-verb", declared_source: "gesture-verb",
    dev: "ie1", devs: ["ie1"], at: 1.0, steps: 2 });

  const line = document.getElementById("triggerFired_flash_device");
  assert(line.textContent.indexOf("ADMIN MANUAL") === -1,
    "only an admin-manual fire carries the tag, got " + line.textContent);
  assert(line.textContent.indexOf("gesture-verb") >= 0,
    "the fire source should still be shown, got " + line.textContent);
`);

scenario("the Fire button sends fire_trigger with no dev for a ROOM target", `
  renderTriggers([SWEEP]);
  const button = findAll(document.getElementById("triggers"),
                         (n) => n.tagName === "button")[0];
  button.onclick();
  assert(sent.length === 1, "expected one send, got " + sent.length);
  assert(sent[0][0] === "fire_trigger", "wrong command: " + sent[0][0]);
  assert(sent[0][1].name === "play_aurora", "wrong name: " + sent[0][1].name);
  assert(!("dev" in sent[0][1]),
    "a ROOM-target fire must not carry a dev, got " + JSON.stringify(sent[0][1]));
`);

scenario("a DEVICE target renders a picker and sends the selected dev", `
  renderTriggerDevices([{ dev: "ie1" }, { dev: "ie2" }]);
  renderTriggers([FLASH]);
  const picker = document.getElementById("triggerDev_flash_device");
  assert(picker, "a DEVICE-target card must render a device picker");
  assert(picker.children.length === 2,
    "picker should list both devices, got " + picker.children.length);
  picker.value = "ie2";

  const button = findAll(document.getElementById("triggers"),
                         (n) => n.tagName === "button")[0];
  button.onclick();
  assert(sent[0][1].dev === "ie2", "wrong dev sent: " + sent[0][1].dev);
`);

scenario("no triggers renders the empty state and clears prior cards", `
  renderTriggers([SWEEP]);
  renderTriggers([]);
  assert(document.getElementById("triggerCards") === null,
    "the card list must be torn down when no Bit declares triggers");
  const el = document.getElementById("triggers");
  assert(el.children.length === 1 &&
         el.children[0].textContent === "No triggers declared",
    "expected the empty state, got " + el.children.length + " children");
`);

scenario("an unchanged table does not rebuild the cards", `
  renderTriggers([SWEEP, FLASH]);
  const list = document.getElementById("triggerCards");
  const first = list.children[0];
  renderTriggers([SWEEP, FLASH]);
  assert(document.getElementById("triggerCards") === list,
    "an unchanged table must not replace the card list node");
  assert(list.children[0] === first,
    "an unchanged table must not rebuild individual cards");
`);

scenario("a fire arriving before any render does not throw", `
  renderTriggerFired({ name: "play_aurora", fired_by: "admin-manual",
    declared_source: "bit-adjudicated", devs: [], at: 0, steps: 0 });
  renderTriggers([SWEEP]);
  const line = document.getElementById("triggerFired_play_aurora");
  assert(line.textContent.indexOf("ADMIN MANUAL") >= 0,
    "a fire seen before the cards existed should show once they do, got "
    + line.textContent);
`);

// ---- console.js dispatch -------------------------------------------------

class FakeWebSocket {
  constructor(url) { this.url = url; }
}

function newConsoleDocument() {
  const root = makeNode("body");
  for (const id of ["loadBtn", "runBtn", "abortBtn"]) {
    const node = makeNode("button");
    node.id = id;
    root.appendChild(node);
  }
  // console.js's renderDevices() (reached by the "devices_changed" scenario
  // below) calls rows("#devices", ...) before it calls renderTriggerDevices,
  // and rows() looks up "#devices tbody" via document.querySelector. The
  // room panel's console document stub this was copied from never exercises
  // renderDevices, so it has neither a #devices table nor a querySelector
  // implementation; both are added here so that path can run.
  const devicesTable = makeNode("table");
  devicesTable.id = "devices";
  const devicesTbody = makeNode("tbody");
  devicesTable.appendChild(devicesTbody);
  root.appendChild(devicesTable);
  return {
    createElement: (tag) => makeNode(tag),
    createTextNode: (text) => ({ nodeType: 3, textContent: text }),
    getElementById: (id) => findById(root, id),
    querySelector: (selector) => {
      const match = /^#(\S+)\s+tbody$/.exec(selector);
      if (!match) return null;
      const el = findById(root, match[1]);
      if (!el) return null;
      return el._children.find((c) => c.tagName === "tbody") || null;
    },
  };
}

const consoleJsPath = path.join(__dirname, "..", "..", "console", "static",
                                "console.js");
const consoleJsSource = fs.readFileSync(consoleJsPath, "utf8");

function consoleScenario(name, testBody) {
  const calls = [];
  const sandbox = {
    document: newConsoleDocument(),
    WebSocket: FakeWebSocket,
    location: { host: "test.invalid" },
    console, assert, calls, SWEEP,
    renderRoom: () => {},
    renderRoomFrame: () => {},
    renderTriggers: (t) => calls.push(["renderTriggers", t]),
    renderTriggerFired: (f) => calls.push(["renderTriggerFired", f]),
    renderTriggerDevices: (d) => calls.push(["renderTriggerDevices", d]),
  };
  vm.createContext(sandbox);
  try {
    vm.runInContext(consoleJsSource + "\n" + testBody, sandbox,
                    { filename: `console.js+${name}` });
  } catch (err) {
    failures++;
    console.error(`FAIL: scenario "${name}" threw: ${err.stack || err}`);
  }
}

consoleScenario("triggers_changed dispatches to renderTriggers", `
  const triggers = [SWEEP];
  handle({ event: "triggers_changed", triggers });
  assert(calls.length === 1, "expected one call, got " + calls.length);
  assert(calls[0][0] === "renderTriggers", "wrong target: " + calls[0][0]);
  assert(calls[0][1] === triggers, "wrong payload");
`);

consoleScenario("trigger_fired dispatches to renderTriggerFired", `
  const fired = { name: "play_aurora", fired_by: "admin-manual" };
  handle({ event: "trigger_fired", fired });
  assert(calls.length === 1, "expected one call, got " + calls.length);
  assert(calls[0][0] === "renderTriggerFired", "wrong target: " + calls[0][0]);
  assert(calls[0][1] === fired, "wrong payload");
`);

consoleScenario("devices_changed also feeds the trigger device pickers", `
  const devices = [{ dev: "ie1", name: "shroom", role: "player" }];
  handle({ event: "devices_changed", devices });
  const names = calls.map((c) => c[0]);
  assert(names.indexOf("renderTriggerDevices") >= 0,
    "the picker source must follow the device list, got " + names.join(","));
`);

// ---- report --------------------------------------------------------------

if (failures > 0) {
  console.error(`${failures} assertion(s) failed`);
  process.exit(1);
} else {
  console.log("OK: triggers.js behavioral checks passed");
  process.exit(0);
}
