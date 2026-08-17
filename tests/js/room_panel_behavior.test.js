"use strict";
// Behavioral test for console/static/room.js, covering Defect 1 (the Room
// strip was rebuilt -- and its painted swatches destroyed -- on every
// room_changed event, measured live at 1726 room_changed events against
// only 464 room_frame events) plus the GRB decode in renderRoomFrame.
// tests/test_console_static.py only greps room.js's source for substrings;
// that cannot see either of these, because both are about what the DOM
// looks like after running the code, not what text appears in the file.
// That gap is exactly why Defect 1 reached a live run undetected.
//
// This drives the real shipped room.js against a small hand-rolled DOM
// stub under Node. No jsdom or other dependency: this repo has no build
// step and nothing shipped may depend on npm.
//
// Run directly: node tests/js/room_panel_behavior.test.js
// Wired into pytest via tests/test_room_panel_behavior.py, which shells
// out to this file and skips cleanly if node is unavailable.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

// ---- minimal hand-rolled DOM stub ---------------------------------------
// Implements only what console/static/room.js actually touches:
// createElement / createTextNode / getElementById, id / className /
// textContent / style, appendChild / remove, children, and
// innerHTML = "" (room.js never assigns innerHTML to anything else).

function makeNode(tag) {
  const node = {
    tagName: tag,
    id: "",
    className: "",
    textContent: "",
    style: {},
    parentNode: null,
    _children: [],
  };
  Object.defineProperty(node, "children", {
    get() {
      return node._children;
    },
  });
  Object.defineProperty(node, "innerHTML", {
    get() {
      return node._children.length ? "(non-empty)" : "";
    },
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

function newDocument() {
  const root = makeNode("body");
  const roomDiv = makeNode("div");
  roomDiv.id = "room";
  root.appendChild(roomDiv);
  return {
    createElement: (tag) => makeNode(tag),
    createTextNode: (text) => ({ nodeType: 3, textContent: text }),
    getElementById: (id) => findById(root, id),
  };
}

// ---- fixtures -------------------------------------------------------------

const ZONES_3 = [
  { name: "left", start: 0, count: 20 },
  { name: "center", start: 20, count: 20 },
  { name: "right", start: 40, count: 20 },
];

function cap(pixelCount, zones) {
  return { pixel_count: pixelCount, color_order: "GRB", zones };
}

function room(overrides) {
  return Object.assign(
    {
      room_type: "led_strip",
      capability: cap(60, ZONES_3),
      bound_dev: null,
      controllers: {},
      instruments: [],
    },
    overrides || {}
  );
}

// ---- harness ----------------------------------------------------------

let failures = 0;
function assert(cond, message) {
  if (!cond) {
    failures++;
    console.error(`FAIL: ${message}`);
  }
}

const roomJsPath = path.join(__dirname, "..", "..", "console", "static", "room.js");
const roomJsSource = fs.readFileSync(roomJsPath, "utf8");

// Runs testBody against a fresh document AND fresh room.js module state
// (roomCapability starts at null again each time), by re-evaluating the
// shipped source per scenario in its own vm context. testBody and
// roomJsSource run in the same context, so testBody can call
// renderRoom/renderRoomFrame by name. Everything executed here is this
// repo's own source plus hardcoded literal test bodies below, never
// external or attacker-influenced input.
function scenario(name, testBody) {
  const sandbox = { document: newDocument(), assert, cap, room, ZONES_3, console };
  vm.createContext(sandbox);
  try {
    vm.runInContext(roomJsSource + "\n" + testBody, sandbox, { filename: `room.js+${name}` });
  } catch (err) {
    failures++;
    console.error(`FAIL: scenario "${name}" threw: ${err.stack || err}`);
  }
}

// ---- scenarios ----------------------------------------------------------

scenario("strip survives unchanged-capability re-renders", `
  renderRoom(room());
  renderRoomFrame(new Array(180).fill(1)); // paints every swatch to rgb(1,1,1)

  const stripBefore = document.getElementById("roomStrip");
  assert(stripBefore !== null, "strip should exist after the first renderRoom");
  const paintedBefore = stripBefore.children.filter((n) => n.style.background).length;
  assert(paintedBefore === 60, "expected all 60 swatches painted, got " + paintedBefore);

  // Simulate the common case: several more room_changed events carrying
  // only a moved controller value, same Room, same capability.
  for (let i = 0; i < 5; i++) {
    renderRoom(room({ controllers: { "74": i * 10 } }));
  }

  const stripAfter = document.getElementById("roomStrip");
  assert(stripAfter === stripBefore,
    "strip node identity changed across unchanged-capability renderRoom calls " +
    "(this is Defect 1: the strip was rebuilt on every room_changed)");
  const paintedAfter = stripAfter.children.filter((n) => n.style.background).length;
  assert(paintedAfter === 60,
    "painted swatches did not survive unchanged-capability re-renders, got " + paintedAfter);
`);

scenario("a changed pixel_count rebuilds the strip", `
  renderRoom(room());
  renderRoomFrame(new Array(180).fill(1));
  const stripBefore = document.getElementById("roomStrip");

  renderRoom(room({ capability: cap(10, [{ name: "all", start: 0, count: 10 }]) }));
  const stripAfter = document.getElementById("roomStrip");

  assert(stripAfter !== stripBefore, "strip was not rebuilt after pixel_count changed");
  assert(stripAfter.children.length === 10,
    "expected 10 swatches after reconfigure, got " + stripAfter.children.length);
  const stale = stripAfter.children.filter((n) => n.style.background).length;
  assert(stale === 0, "rebuilt strip should start unpainted, found " + stale + " stale swatches");
`);

scenario("changed zones (same pixel_count) also rebuilds the strip", `
  renderRoom(room());
  renderRoomFrame(new Array(180).fill(1));
  const stripBefore = document.getElementById("roomStrip");

  const twoZones = [{ name: "left", start: 0, count: 30 }, { name: "right", start: 30, count: 30 }];
  renderRoom(room({ capability: cap(60, twoZones) }));
  const stripAfter = document.getElementById("roomStrip");

  assert(stripAfter !== stripBefore,
    "strip was not rebuilt after zones changed, even though pixel_count stayed the same");
  const zonesBar = document.getElementById("roomZones");
  assert(zonesBar.children.length === 2, "zone labels were not rebuilt to match the new zones");
`);

scenario("renderRoom(null) renders the empty state and resets state cleanly", `
  renderRoom(room());
  renderRoomFrame(new Array(180).fill(1));

  renderRoom(null);
  const roomDiv = document.getElementById("room");
  const text = roomDiv.children.map((c) => c.textContent).join(" ");
  assert(text.indexOf("No Room configured") !== -1, "expected the empty-state text, got: " + text);
  assert(document.getElementById("roomStrip") === null, "strip should be gone after renderRoom(null)");

  // A later real Room must rebuild cleanly, not compare against the
  // capability from before the null and wrongly skip the rebuild.
  renderRoom(room());
  const rebuilt = document.getElementById("roomStrip");
  assert(rebuilt !== null, "a real Room after renderRoom(null) should rebuild the strip");
  assert(rebuilt.children.length === 60, "the rebuilt strip after renderRoom(null) has the wrong pixel count");
  const painted = rebuilt.children.filter((n) => n.style.background).length;
  assert(painted === 0, "the rebuilt strip after renderRoom(null) should start unpainted, found " + painted);
`);

scenario("renderRoomFrame is safe before any renderRoom, and decodes GRB not RGB", `
  assert(document.getElementById("roomStrip") === null, "no strip should exist yet in a fresh document");
  renderRoomFrame([10, 20, 30]); // must not throw

  renderRoom(room({ capability: cap(2, [{ name: "all", start: 0, count: 2 }]) }));
  // Wire order per pixel is [G, R, B] (control/room_profile.py's
  // color_order). Pixel 0: G=10 R=20 B=30. Pixel 1: G=40 R=50 B=60.
  renderRoomFrame([10, 20, 30, 40, 50, 60]);
  const swatches = document.getElementById("roomStrip").children;
  assert(swatches[0].style.background === "rgb(20,10,30)",
    "GRB decode wrong for pixel 0, got " + swatches[0].style.background);
  assert(swatches[1].style.background === "rgb(50,40,60)",
    "GRB decode wrong for pixel 1, got " + swatches[1].style.background);
`);

// ---- report ---------------------------------------------------------------

if (failures > 0) {
  console.error(`${failures} assertion(s) failed`);
  process.exit(1);
} else {
  console.log("OK: room.js behavioral checks passed");
  process.exit(0);
}
