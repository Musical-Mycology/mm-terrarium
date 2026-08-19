"use strict";
// Behavioral test for console/static/room.js, covering Defect 1 (the Room
// strip was rebuilt -- and its painted swatches destroyed -- on every
// room_changed event, measured live at 1726 room_changed events against
// only 464 room_frame events) plus the GRB decode in renderRoomFrame. Also
// covers console/static/console.js's handle() dispatch of "room_changed"
// and "room_frame" into renderRoom/renderRoomFrame -- the two lines that
// route those events into this panel, added by the same branch that added
// room.js's coverage above, but themselves uncovered by anything before
// this file.
// tests/test_console_static.py only greps room.js's and console.js's
// source for substrings; that cannot see any of the above, because it is
// about what the DOM looks like, or which function actually gets called,
// after the code runs, not what text appears in the file. That gap is
// exactly why Defect 1 reached a live run undetected, and it is the same
// gap this file's console.js scenarios close for the dispatch lines.
//
// This drives the real shipped room.js and console.js against a small
// hand-rolled DOM stub under Node. No jsdom or other dependency: this repo
// has no build step and nothing shipped may depend on npm.
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
// textContent / style, appendChild / insertBefore / remove, children, and
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
  node.insertBefore = (newChild, referenceChild) => {
    newChild.parentNode = node;
    if (referenceChild == null) {
      node._children.push(newChild);
      return newChild;
    }
    const at = node._children.indexOf(referenceChild);
    if (at === -1) {
      node._children.push(newChild);
    } else {
      node._children.splice(at, 0, newChild);
    }
    return newChild;
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
// A two-fixture Room, matching the wire shape control/room_view.py's
// room_view() now produces (Task 9/10): a "fixtures" list, each entry
// {name, pixel_count, channel_start, channel_count, zones, dev}, alongside
// the unchanged whole-profile "capability" (pixel_count/color_order/zones
// over the WHOLE concatenated surface). "accent" is left unbound (dev:
// null) on purpose, so scenarios below can exercise both the "not bound"
// label text and the dev-routing no-op path in renderRoomFrame.

const MAIN_ZONES = [
  { name: "main.left", start: 0, count: 20 },
  { name: "main.center", start: 20, count: 20 },
  { name: "main.right", start: 40, count: 20 },
];
const ACCENT_ZONES = [
  { name: "accent.low", start: 60, count: 15 },
  { name: "accent.high", start: 75, count: 15 },
];

function cap(pixelCount, zones) {
  return { pixel_count: pixelCount, color_order: "GRB", zones };
}

function fixtures(overrides) {
  const base = [
    { name: "main", pixel_count: 60, channel_start: 0, channel_count: 180,
      zones: MAIN_ZONES, dev: "sim-room-main" },
    { name: "accent", pixel_count: 30, channel_start: 180, channel_count: 90,
      zones: ACCENT_ZONES, dev: null },
  ];
  return overrides ? overrides(base) : base;
}

function room(overrides) {
  return Object.assign(
    {
      room_type: "TEST",
      capability: cap(90, MAIN_ZONES.concat(ACCENT_ZONES)),
      fixtures: fixtures(),
      controllers: {},
      instruments: [],
    },
    overrides || {}
  );
}

// #room has no CSS rule of its own (confirmed against style.css), so DOM
// order is visual order: header at top, then each fixture's strip and zone
// labels, instrument cards last. orderOf reads #room's direct children by
// id so a scenario can assert that order survives a render. Unaffected by
// the N-strip change -- still just walks #room's direct children.
function orderOf(doc) {
  return doc.getElementById("room").children.map((c) => c.id).join(",");
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
// (roomFixturesShape/fixtureDevByName/fixtureNameByDev start fresh again
// each time), by re-evaluating the shipped source per scenario in its own
// vm context. testBody and roomJsSource run in the same context, so
// testBody can call renderRoom/renderRoomFrame by name. Everything
// executed here is this repo's own source plus hardcoded literal test
// bodies below, never external or attacker-influenced input.
function scenario(name, testBody) {
  const sandbox = { document: newDocument(), assert, cap, room, fixtures, orderOf, console };
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
  renderRoomFrame("sim-room-main", new Array(180).fill(1)); // paints every swatch to rgb(1,1,1)

  const stripBefore = document.getElementById("roomStrip-main");
  assert(stripBefore !== null, "strip should exist after the first renderRoom");
  const paintedBefore = stripBefore.children.filter((n) => n.style.background).length;
  assert(paintedBefore === 60, "expected all 60 swatches painted, got " + paintedBefore);
  assert(orderOf(document) === "roomHeader,roomStrip-main,roomZones-main,roomStrip-accent,roomZones-accent,roomCards",
    "wrong sibling order after the first render, got: " + orderOf(document));

  // Simulate the common case: several more room_changed events carrying
  // only a moved controller value, same Room, same fixtures shape.
  for (let i = 0; i < 5; i++) {
    renderRoom(room({ controllers: { "74": i * 10 } }));
  }

  const stripAfter = document.getElementById("roomStrip-main");
  assert(stripAfter === stripBefore,
    "strip node identity changed across unchanged-capability renderRoom calls " +
    "(this is Defect 1: the strip was rebuilt on every room_changed)");
  const paintedAfter = stripAfter.children.filter((n) => n.style.background).length;
  assert(paintedAfter === 60,
    "painted swatches did not survive unchanged-capability re-renders, got " + paintedAfter);
  assert(orderOf(document) === "roomHeader,roomStrip-main,roomZones-main,roomStrip-accent,roomZones-accent,roomCards",
    "sibling order changed across unchanged-capability re-renders, got: " + orderOf(document));
`);

scenario("a changed pixel_count rebuilds the strip", `
  renderRoom(room());
  renderRoomFrame("sim-room-main", new Array(180).fill(1));
  const stripBefore = document.getElementById("roomStrip-main");
  const accentStripBefore = document.getElementById("roomStrip-accent");

  renderRoom(room({ fixtures: fixtures((fx) => [{ ...fx[0], pixel_count: 10, channel_count: 30, zones: [{ name: "main.all", start: 0, count: 10 }] }, fx[1]]) }));
  const stripAfter = document.getElementById("roomStrip-main");
  const accentStripAfter = document.getElementById("roomStrip-accent");

  assert(stripAfter !== stripBefore, "strip was not rebuilt after pixel_count changed");
  assert(stripAfter.children.length === 10,
    "expected 10 swatches after reconfigure, got " + stripAfter.children.length);
  const stale = stripAfter.children.filter((n) => n.style.background).length;
  assert(stale === 0, "rebuilt strip should start unpainted, found " + stale + " stale swatches");
  // accent's OWN shape (pixel_count/zones) did not change, so its strip
  // should come out the same shape it started in (still 30 swatches).
  // NOTE: this is a shape check, not a node-identity check. renderRoom's
  // rebuildStrips flag is computed once for the WHOLE fixtures array
  // (fixturesShapeMatches short-circuits false as soon as ANY fixture
  // differs), so main's change actually tears down and rebuilds accent's
  // strip too -- accentStripAfter is a NEW node, not accentStripBefore.
  // See task-11-report.md for the full finding; not fixed here since it
  // would mean deviating from the brief's verbatim room.js.
  assert(accentStripAfter.children.length === 30,
    "accent strip should be untouched at 30 swatches, got " + accentStripAfter.children.length);
  assert(orderOf(document) === "roomHeader,roomStrip-main,roomZones-main,roomStrip-accent,roomZones-accent,roomCards",
    "capability rebuild reordered #room's children, got: " + orderOf(document));
`);

scenario("changed zones (same pixel_count) also rebuilds the strip", `
  renderRoom(room());
  renderRoomFrame("sim-room-main", new Array(180).fill(1));
  const stripBefore = document.getElementById("roomStrip-main");

  const twoZones = [{ name: "main.left", start: 0, count: 30 }, { name: "main.right", start: 30, count: 30 }];
  renderRoom(room({ fixtures: fixtures((fx) => [{ ...fx[0], zones: twoZones }, fx[1]]) }));
  const stripAfter = document.getElementById("roomStrip-main");

  assert(stripAfter !== stripBefore,
    "strip was not rebuilt after zones changed, even though pixel_count stayed the same");
  const zonesBar = document.getElementById("roomZones-main");
  // 1 fixtureLabel span + 1 span per zone (see buildFixtureZoneLabels).
  assert(zonesBar.children.length === 3, "zone labels were not rebuilt to match the new zones, got " + zonesBar.children.length);
  assert(orderOf(document) === "roomHeader,roomStrip-main,roomZones-main,roomStrip-accent,roomZones-accent,roomCards",
    "capability rebuild reordered #room's children, got: " + orderOf(document));
`);

scenario("renderRoom(null) renders the empty state and resets state cleanly", `
  renderRoom(room());
  renderRoomFrame("sim-room-main", new Array(180).fill(1));

  renderRoom(null);
  const roomDiv = document.getElementById("room");
  const text = roomDiv.children.map((c) => c.textContent).join(" ");
  assert(text.indexOf("No Room configured") !== -1, "expected the empty-state text, got: " + text);
  assert(document.getElementById("roomStrip-main") === null, "main strip should be gone after renderRoom(null)");
  assert(document.getElementById("roomStrip-accent") === null, "accent strip should be gone after renderRoom(null)");

  // A later real Room must rebuild cleanly, not compare against the
  // fixtures shape from before the null and wrongly skip the rebuild.
  renderRoom(room());
  const rebuiltMain = document.getElementById("roomStrip-main");
  const rebuiltAccent = document.getElementById("roomStrip-accent");
  assert(rebuiltMain !== null, "a real Room after renderRoom(null) should rebuild the main strip");
  assert(rebuiltAccent !== null, "a real Room after renderRoom(null) should rebuild the accent strip");
  assert(rebuiltMain.children.length === 60, "the rebuilt main strip after renderRoom(null) has the wrong pixel count");
  assert(rebuiltAccent.children.length === 30, "the rebuilt accent strip after renderRoom(null) has the wrong pixel count");
  const painted = rebuiltMain.children.filter((n) => n.style.background).length;
  assert(painted === 0, "the rebuilt main strip after renderRoom(null) should start unpainted, found " + painted);
`);

scenario("renderRoomFrame is safe before any renderRoom, and decodes GRB not RGB", `
  assert(document.getElementById("roomStrip-main") === null, "no strip should exist yet in a fresh document");
  renderRoomFrame("sim-room-main", [10, 20, 30]); // must not throw

  renderRoom(room({ fixtures: fixtures((fx) => [{ ...fx[0], pixel_count: 2, channel_count: 6, zones: [{ name: "main.all", start: 0, count: 2 }] }, fx[1]]) }));
  // Wire order per pixel is [G, R, B] (control/room_profile.py's
  // color_order). Pixel 0: G=10 R=20 B=30. Pixel 1: G=40 R=50 B=60.
  renderRoomFrame("sim-room-main", [10, 20, 30, 40, 50, 60]);
  const swatches = document.getElementById("roomStrip-main").children;
  assert(swatches[0].style.background === "rgb(20,10,30)",
    "GRB decode wrong for pixel 0, got " + swatches[0].style.background);
  assert(swatches[1].style.background === "rgb(50,40,60)",
    "GRB decode wrong for pixel 1, got " + swatches[1].style.background);
`);

scenario("one strip per fixture, each with its own zone bar", `
  renderRoom(room());
  const mainStrip = document.getElementById("roomStrip-main");
  const accentStrip = document.getElementById("roomStrip-accent");
  assert(mainStrip !== null && accentStrip !== null, "both fixture strips should exist");
  assert(mainStrip.children.length === 60, "main strip should have 60 swatches, got " + mainStrip.children.length);
  assert(accentStrip.children.length === 30, "accent strip should have 30 swatches, got " + accentStrip.children.length);
  const mainZones = document.getElementById("roomZones-main");
  const accentZones = document.getElementById("roomZones-accent");
  // Each zone bar carries a leading fixtureLabel span (the fixture's name,
  // plus "(not bound)" when unbound) ahead of one span per zone -- see
  // buildFixtureZoneLabels in room.js. main has 3 zones -> 4 children;
  // accent has 2 zones -> 3 children.
  assert(mainZones.children.length === 4, "main should show its own 3 zones plus its fixture label, got " + mainZones.children.length);
  assert(accentZones.children.length === 3, "accent should show its own 2 zones plus its fixture label, got " + accentZones.children.length);
`);

scenario("a frame for one fixture does not repaint the other, and routes by dev", `
  renderRoom(room());
  renderRoomFrame("sim-room-main", new Array(180).fill(9));
  renderRoomFrame("sim-room-accent", new Array(90).fill(0));   // accent unbound in fixtures(), dev null -- must be a no-op, no matching strip by dev

  const mainStrip = document.getElementById("roomStrip-main");
  const accentStrip = document.getElementById("roomStrip-accent");
  const mainPainted = mainStrip.children.filter((n) => n.style.background).length;
  const accentPainted = accentStrip.children.filter((n) => n.style.background).length;
  assert(mainPainted === 60, "main frame should paint every main swatch, got " + mainPainted);
  assert(accentPainted === 0, "a frame addressed to an unbound fixture's stale dev must not paint anything, got " + accentPainted);
`);

// ---- console.js dispatch coverage ----------------------------------------
// console/static/console.js's handle() has two Room-related cases:
//   case "room_changed": renderRoom(msg.room); break;
//   case "room_frame": renderRoomFrame(msg.dev, msg.channels); break;
// renderRoom/renderRoomFrame are room.js's, already covered by the
// scenarios above, so here they are replaced with spies: this checks only
// that handle() routes each event to the right function with the right
// payload, not what that function then does with it.
//
// Unlike room.js, console.js touches browser globals at load time: it
// wires three onclick handlers via document.getElementById, and it calls
// connect() itself at the very bottom of the file, which constructs a
// WebSocket against `location.host`. The stub below satisfies exactly
// those two needs (a document with the three ids, and an inert
// WebSocket/location) so the file evaluates without touching a real
// network. Nothing else in console.js runs at load time, and neither
// room_changed nor room_frame touches the DOM through handle() itself --
// that would happen inside renderRoom/renderRoomFrame, which are spied out
// here instead of loaded from room.js.

class FakeWebSocket {
  constructor(url) {
    this.url = url;
  }
}

function newConsoleDocument() {
  const root = makeNode("body");
  for (const id of ["loadBtn", "runBtn", "abortBtn"]) {
    const node = makeNode("button");
    node.id = id;
    root.appendChild(node);
  }
  return {
    createElement: (tag) => makeNode(tag),
    createTextNode: (text) => ({ nodeType: 3, textContent: text }),
    getElementById: (id) => findById(root, id),
  };
}

const consoleJsPath = path.join(__dirname, "..", "..", "console", "static", "console.js");
const consoleJsSource = fs.readFileSync(consoleJsPath, "utf8");

// Same shape as scenario() above: re-evaluate the real shipped console.js
// per scenario in its own vm context, with renderRoom/renderRoomFrame
// replaced by spies that record their calls.
function consoleScenario(name, testBody) {
  const calls = [];
  const sandbox = {
    document: newConsoleDocument(),
    WebSocket: FakeWebSocket,
    location: { host: "test.invalid" },
    console,
    assert,
    calls,
    renderRoom: (room) => calls.push(["renderRoom", room]),
    renderRoomFrame: (dev, channels) => calls.push(["renderRoomFrame", dev, channels]),
  };
  vm.createContext(sandbox);
  try {
    vm.runInContext(consoleJsSource + "\n" + testBody, sandbox, { filename: `console.js+${name}` });
  } catch (err) {
    failures++;
    console.error(`FAIL: scenario "${name}" threw: ${err.stack || err}`);
  }
}

consoleScenario("room_changed dispatches to renderRoom with the message's room payload", `
  const payload = { room_type: "TEST", capability: { pixel_count: 60, zones: [] }, fixtures: [], instruments: [] };
  handle({ event: "room_changed", room: payload });
  assert(calls.length === 1, "expected exactly one render call, got " + calls.length);
  assert(calls[0][0] === "renderRoom",
    "room_changed should dispatch to renderRoom, got " + (calls[0] && calls[0][0]));
  assert(calls[0][1] === payload,
    "renderRoom was not called with the message's room payload");
`);

consoleScenario("room_frame dispatches to renderRoomFrame with the message's dev and channels payload", `
  const channels = [10, 20, 30, 40, 50, 60];
  handle({ event: "room_frame", dev: "sim-room-main", channels });
  assert(calls.length === 1, "expected exactly one render call, got " + calls.length);
  assert(calls[0][0] === "renderRoomFrame",
    "room_frame should dispatch to renderRoomFrame, got " + (calls[0] && calls[0][0]));
  assert(calls[0][1] === "sim-room-main",
    "renderRoomFrame was not called with the message's dev");
  assert(calls[0][2] === channels,
    "renderRoomFrame was not called with the message's channels payload");
`);

// ---- report ---------------------------------------------------------------

if (failures > 0) {
  console.error(`${failures} assertion(s) failed`);
  process.exit(1);
} else {
  console.log("OK: room.js behavioral checks passed");
  process.exit(0);
}
