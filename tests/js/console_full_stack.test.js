"use strict";
// All three Console scripts loaded together, in index.html's order, into
// one context -- the combination the browser actually runs, and the one no
// existing test covered.
//
// This reproduces the 2026-08-19 live failure directly: with room.js and
// triggers.js both defining buildCard, dispatching a room_changed threw
// inside renderRoom, which aborted handle() and left the Room's instrument
// cards, the Triggers panel and the Event log permanently empty.
//
// Run directly: node tests/js/console_full_stack.test.js

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC = path.join(__dirname, "..", "..", "console", "static");
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map((m) => m[1]);

// ---- minimal DOM stub ---------------------------------------------------
// Nodes register themselves into the document's byId map as soon as an id
// is assigned, the way a real DOM makes any element with an id reachable
// via getElementById once it exists -- room.js/triggers.js both create
// elements with document.createElement() and set .id afterward, so the
// stub has to track that, not just ids that were fetched first.
function makeNode(byId, initialId) {
  let _id = initialId || "";
  const node = {
    get id() { return _id; },
    set id(v) { _id = v; if (v) byId[v] = node; },
    tagName: "DIV", children: [], style: {}, dataset: {},
    innerHTML: "", textContent: "", value: "", className: "",
    onclick: null,
    appendChild(child) { this.children.push(child); return child; },
    insertBefore(child, ref) {
      const idx = this.children.indexOf(ref);
      if (idx === -1) this.children.push(child);
      else this.children.splice(idx, 0, child);
      return child;
    },
    setAttribute(k, v) { this[k] = v; },
    addEventListener() {},
    remove() {},
    classList: { add() {}, remove() {}, contains: () => false },
  };
  if (initialId) byId[initialId] = node;
  return node;
}

function makeDocument() {
  const byId = {};
  return {
    _byId: byId,
    getElementById(id) { return (byId[id] = byId[id] || makeNode(byId, id)); },
    createElement(tag) { const n = makeNode(byId, null); n.tagName = tag.toUpperCase(); return n; },
    createTextNode(text) { const n = makeNode(byId, null); n.tagName = "#text"; n.textContent = text; return n; },
    querySelector() { return makeNode(byId, null); },
    querySelectorAll() { return []; },
  };
}

function loadAll() {
  const doc = makeDocument();
  const sandbox = {
    console, document: doc,
    location: { host: "localhost:1", protocol: "http:" },
    setTimeout() {}, clearTimeout() {},
    WebSocket: function () { sandbox.__sock = this; this.send = () => {}; },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  for (const file of scripts) {
    const src = fs.readFileSync(path.join(STATIC, file), "utf8");
    vm.runInContext(src, sandbox, { filename: file });
  }
  return sandbox;
}

// ---- fixtures matching the real wire shapes -----------------------------
const room = () => ({
  room_type: "TEST",
  capability: { surface_id: "room_test", pixel_count: 90, color_order: "GRB",
                zones: [{ name: "main.left", start: 0, count: 20 }] },
  fixtures: [{ name: "main", pixel_count: 60, channel_start: 0,
               channel_count: 180, dev: "sim-room-main",
               zones: [{ name: "main.left", start: 0, count: 20 }] }],
  instruments: [{ kind: "light", instrument: "rainbow", target: "primary",
                  lanes: [{ source: "cc:74", dest: "hue" }] }],
  controllers: { "cc:74": 64 },
});

const triggers = () => ([{
  name: "play_aurora", description: "A slow rainbow sweep across the Room",
  target: "ROOM",
  condition: { name: "round_won", description: "User wins a round",
               source: "bit-adjudicated", verb: null },
  script: [{ offset: 0.0, kind: "light", dev: "@target",
             status: 176, data1: 74, data2: 127 }],
}]);

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`ok   ${name}`); }
  catch (e) { failures++; console.error(`FAIL ${name}\n     ${e.message}`); }
}

check("a room_changed does not throw with every script loaded", () => {
  const box = loadAll();
  box.__sock.onmessage({ data: JSON.stringify({ event: "room_changed", room: room() }) });
});

check("the Room panel renders its instrument cards", () => {
  const box = loadAll();
  box.__sock.onmessage({ data: JSON.stringify({ event: "room_changed", room: room() }) });
  const cards = box.document._byId["roomCards"];
  assert.ok(cards && cards.children.length >= 1,
    "expected at least one instrument card in #roomCards");
});

check("the Triggers panel renders one card per declared trigger", () => {
  const box = loadAll();
  box.__sock.onmessage({ data: JSON.stringify({
    event: "snapshot", state: "RUNNING", loaded_bit: "TestBit",
    installed_bits: ["TestBit"], registration: [], roles: [], devices: [],
    bit_status: {}, room: room(), triggers: triggers(),
  }) });
  const cards = box.document._byId["triggerCards"];
  assert.ok(cards && cards.children.length === 1,
    `expected 1 trigger card, got ${cards ? cards.children.length : "no #triggerCards"}`);
});

check("a room_changed after a snapshot leaves the trigger cards intact", () => {
  const box = loadAll();
  box.__sock.onmessage({ data: JSON.stringify({
    event: "snapshot", state: "RUNNING", loaded_bit: "TestBit",
    installed_bits: ["TestBit"], registration: [], roles: [], devices: [],
    bit_status: {}, room: room(), triggers: triggers(),
  }) });
  const before = box.document._byId["triggerCards"].children.length;
  box.__sock.onmessage({ data: JSON.stringify({ event: "room_changed", room: room() }) });
  assert.strictEqual(box.document._byId["triggerCards"].children.length, before,
    "a room_changed must not disturb the trigger cards");
});

process.exit(failures ? 1 : 0);
