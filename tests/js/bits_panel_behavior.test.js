"use strict";
// Behavioral test for the Bits panel, which lives directly in console.js
// rather than a new script file (see console/static/console.js's renderBits
// and console_script_isolation.test.js, which would otherwise need a new
// entry for a fourth file's exports).
//
// The Room and Trigger panels each shipped a live defect where a
// high-frequency event (room_changed / trigger_fired) rebuilt the whole card
// list on every occurrence. bits_listed fires once at connect and never
// per-frame, but state_changed DOES fire continuously (see console.js's
// handle()), so the load-bearing scenario here is the same shape: a
// state_changed event must not touch the Bits card list at all.
//
// Drives the real shipped console.js against a small hand-rolled DOM stub
// under Node. No jsdom: this repo has no build step and nothing shipped may
// depend on npm.
//
// Run directly: node tests/js/bits_panel_behavior.test.js
// Wired into pytest via tests/test_bits_panel_behavior.py.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

// ---- DOM stub -----------------------------------------------------------
// Same shape as tests/js/trigger_panel_behavior.test.js's.

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
  for (const id of ["loadBtn", "runBtn", "abortBtn"]) {
    const btn = makeNode("button");
    btn.id = id;
    root.appendChild(btn);
  }
  const bitPicker = makeNode("select");
  bitPicker.id = "bitPicker";
  root.appendChild(bitPicker);

  const bits = makeNode("div");
  bits.id = "bits";
  root.appendChild(bits);

  for (const id of ["state", "loaded", "conn", "log"]) {
    const node = makeNode("span");
    node.id = id;
    root.appendChild(node);
  }

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

// ---- fixtures -------------------------------------------------------------

const METRONOME = {
  name: "metronome_bit", version: "1.0.0", kind: "score", display_name: "Metronome",
  description: "Keeps a steady beat", hidden: false,
  room_types: ["POOL"],
  start: { when: "on_join", min_scored: 1, timeout_seconds: 30, on_timeout: "abort" },
  notes: null,
};

const HIDDEN_BIT = {
  name: "dev_bit", version: "0.0.1", kind: "score", display_name: "Dev Bit",
  description: "Internal test fixture", hidden: true,
  room_types: [],
  start: { when: "manual", min_scored: 0, timeout_seconds: null, on_timeout: null },
  notes: null,
};

const ERRORS = [{ path: "bits/broken_bit", message: "missing manifest.yaml" }];

// ---- harness ----------------------------------------------------------

let failures = 0;
function assert(cond, message) {
  if (!cond) {
    failures++;
    console.error(`FAIL: ${message}`);
  }
}

const consoleJsPath = path.join(__dirname, "..", "..", "console", "static",
                                "console.js");
const consoleJsSource = fs.readFileSync(consoleJsPath, "utf8");

function scenario(name, testBody) {
  const sent = [];
  class FakeWebSocket {
    constructor(url) { this.url = url; this.readyState = FakeWebSocket.OPEN; }
    send(data) { sent.push(JSON.parse(data)); }
  }
  FakeWebSocket.OPEN = 1;

  const sandbox = {
    document: newDocument(),
    WebSocket: FakeWebSocket,
    location: { host: "test.invalid" },
    console, assert, sent, findAll,
    METRONOME, HIDDEN_BIT, ERRORS,
    renderRoom: () => {},
    renderRoomFrame: () => {},
    renderTriggers: () => {},
    renderTriggerFired: () => {},
    renderTriggerDevices: () => {},
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  try {
    vm.runInContext(consoleJsSource + "\n" + testBody, sandbox,
                    { filename: `console.js+${name}` });
  } catch (err) {
    failures++;
    console.error(`FAIL: scenario "${name}" threw: ${err.stack || err}`);
  }
}

scenario("renders one card per bit, plus an error row, hidden bit muted", `
  renderBits([METRONOME, HIDDEN_BIT], ERRORS);
  const cards = findAll(document.getElementById("bits"),
                        (n) => n.className.split(" ").indexOf("bit") >= 0
                          && n.className.split(" ").indexOf("error") === -1);
  assert(cards.length === 2, "expected 2 bit cards, got " + cards.length);

  function cardText(card) {
    return findAll(card, (n) => true).map((n) => n.textContent).join("|");
  }

  const hiddenCard = cards.find((c) => cardText(c).indexOf("dev_bit") >= 0);
  assert(hiddenCard, "expected a card for the hidden bit");
  assert(hiddenCard.className.split(" ").indexOf("hidden") >= 0,
    "hidden bit's card should carry the hidden/muted class, got " + hiddenCard.className);

  const shownCard = cards.find((c) => cardText(c).indexOf("metronome_bit") >= 0);
  assert(shownCard.className.split(" ").indexOf("hidden") === -1,
    "a non-hidden bit must not carry the hidden class");

  const text = cardText(shownCard);
  assert(text.indexOf("1.0.0") >= 0, "version should render, got " + text);
  assert(text.indexOf("POOL") >= 0, "rooms should render, got " + text);
  assert(text.indexOf("on_join") >= 0, "start condition should render, got " + text);
  assert(text.indexOf("Keeps a steady beat") >= 0, "description should render, got " + text);

  const errorRows = findAll(document.getElementById("bits"),
                            (n) => n.className.split(" ").indexOf("error") >= 0);
  assert(errorRows.length === 1, "expected 1 error row, got " + errorRows.length);
  assert(cardText(errorRows[0]).indexOf("missing manifest.yaml") >= 0,
    "error row should show the message, got " + cardText(errorRows[0]));
`);

scenario("a state_changed event does not rebuild the bits card list", `
  renderBits([METRONOME, HIDDEN_BIT], ERRORS);
  const list = document.getElementById("bitCards");
  const childrenBefore = list.children.length;
  const first = list.children[0];

  handle({ event: "state_changed", state: "RUNNING" });

  assert(document.getElementById("bitCards") === list,
    "state_changed must not replace the bits card list node");
  assert(list.children.length === childrenBefore,
    "state_changed must not rebuild the bits cards: had " + childrenBefore +
    ", now " + list.children.length);
  assert(list.children[0] === first,
    "state_changed must not rebuild individual bit cards");
`);

scenario("bits_listed dispatches through handle() to renderBits", `
  handle({ event: "bits_listed", bits: [METRONOME], errors: [] });
  const cards = findAll(document.getElementById("bits"),
                        (n) => n.className.split(" ").indexOf("card") >= 0);
  assert(cards.length === 1, "expected handle() to render 1 card, got " + cards.length);
`);

scenario("the Load button sends load_bit with this row's name", `
  renderBits([METRONOME], []);
  const button = findAll(document.getElementById("bits"),
                         (n) => n.tagName === "button")[0];
  assert(button, "expected a Load button on the bit card");
  button.onclick();
  assert(sent.length === 1, "expected one send, got " + sent.length);
  assert(sent[0].command === "load_bit", "wrong command: " + sent[0].command);
  assert(sent[0].name === "metronome_bit", "wrong name: " + sent[0].name);
`);

scenario("an unchanged bit list does not rebuild the cards", `
  renderBits([METRONOME], []);
  const list = document.getElementById("bitCards");
  const first = list.children[0];
  renderBits([METRONOME], []);
  assert(document.getElementById("bitCards") === list,
    "an unchanged bits table must not replace the card list node");
  assert(list.children[0] === first,
    "an unchanged bits table must not rebuild individual cards");
`);

// ---- merged top control bar (Task 4) --------------------------------------
// The console used to ship two separate load/run/abort surfaces: a crude
// <select>+"Load Bit" button "Controls" block near the top of index.html,
// and this Bits panel's per-card Load buttons further down. That duplication
// is the operator complaint this task fixes: one Bits section, at the top of
// the page, owns load/run/abort.

const indexHtmlPath = path.join(__dirname, "..", "..", "console", "static", "index.html");
const indexHtml = fs.readFileSync(indexHtmlPath, "utf8");

{
  const legacySelectCount = (indexHtml.match(/id="bitPicker"/g) || []).length;
  assert(legacySelectCount === 0,
    "expected the legacy bitPicker <select> to be gone, found " + legacySelectCount);
  const legacyLoadBtnCount = (indexHtml.match(/id="loadBtn"/g) || []).length;
  assert(legacyLoadBtnCount === 0,
    "expected the legacy standalone loadBtn button to be gone, found " + legacyLoadBtnCount);
  const runBtnCount = (indexHtml.match(/id="runBtn"/g) || []).length;
  assert(runBtnCount === 1, "expected exactly one runBtn, found " + runBtnCount);
  const abortBtnCount = (indexHtml.match(/id="abortBtn"/g) || []).length;
  assert(abortBtnCount === 1, "expected exactly one abortBtn, found " + abortBtnCount);
}

{
  // The Bits section (its "Bits" <h2> heading and the #bits div) must be the
  // first panel in document order -- ahead of Room, Triggers, Registration,
  // etc. Locate the first <h2> after <h1> and confirm it is "Bits".
  const h1At = indexHtml.indexOf("<h1");
  const firstH2At = indexHtml.indexOf("<h2>", h1At);
  const firstH2Text = /<h2>([^<]+)<\/h2>/.exec(indexHtml.slice(firstH2At, firstH2At + 40));
  assert(firstH2Text && firstH2Text[1] === "Bits",
    "expected the first panel heading after <h1> to be \"Bits\", got " +
    (firstH2Text ? firstH2Text[1] : "(none found)"));
  const bitsHeadingAt = indexHtml.indexOf(">Bits<");
  const bitsDivAt = indexHtml.indexOf('id="bits"');
  const roomHeadingAt = indexHtml.indexOf(">Room<");
  assert(bitsHeadingAt >= 0 && bitsDivAt > bitsHeadingAt && roomHeadingAt > bitsDivAt,
    "expected Bits heading, then #bits div, then Room heading, in that order");
}

scenario("a state_changed event updates the header text but preserves bit-card identity", `
  renderBits([METRONOME, HIDDEN_BIT], ERRORS);
  const list = document.getElementById("bitCards");
  const first = list.children[0];
  const childrenBefore = list.children.length;

  handle({ event: "state_changed", state: "RUNNING" });

  assert(document.getElementById("state").textContent === "RUNNING",
    "state_changed should update the state header text, got " +
    document.getElementById("state").textContent);
  assert(document.getElementById("bitCards") === list,
    "state_changed must not replace the bits card list node");
  assert(list.children.length === childrenBefore,
    "state_changed must not rebuild the bits cards");
  assert(list.children[0] === first,
    "state_changed must not rebuild individual bit cards");
`);

scenario("Run and Abort send the run/abort commands", `
  document.getElementById("runBtn").onclick();
  document.getElementById("abortBtn").onclick();
  assert(sent.length === 2, "expected two sends, got " + sent.length);
  assert(sent[0].command === "run", "expected run, got " + JSON.stringify(sent[0]));
  assert(sent[1].command === "abort", "expected abort, got " + JSON.stringify(sent[1]));
`);

// ---- report --------------------------------------------------------------

if (failures > 0) {
  console.error(`${failures} assertion(s) failed`);
  process.exit(1);
} else {
  console.log("OK: bits panel behavioral checks passed");
  process.exit(0);
}
