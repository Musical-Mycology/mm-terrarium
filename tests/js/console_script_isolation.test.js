"use strict";
// Collision guard for console/static/*.js.
//
// These files load as plain scripts into one shared global scope. On
// 2026-08-19 room.js and triggers.js both declared `function buildCard`,
// triggers.js silently won, and renderRoom then called the wrong one and
// threw on every room_changed -- 222 throws in 2.5s live, killing the Room
// cards, the whole Triggers panel and the Event log.
//
// The existing behavioural tests could not see it BY CONSTRUCTION:
// room_panel_behavior.test.js loads room.js + console.js, and
// trigger_panel_behavior.test.js loads triggers.js. Nothing ever loaded
// room.js and triggers.js together, which is the only pair that collides.
// Each file is correct alone; the defect exists only in the combination the
// browser actually loads.
//
// This test reads the <script src> list out of index.html rather than
// hardcoding it, so a fourth script added later is covered automatically.
// That is what makes this a class-level guard instead of a second point fix.
//
// Run directly: node tests/js/console_script_isolation.test.js

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC = path.join(__dirname, "..", "..", "console", "static");
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");

// The load order the browser actually uses.
const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)]
  .map((m) => m[1]);
assert.ok(scripts.length >= 3,
  `expected index.html to load at least 3 scripts, found ${scripts.length}`);

// A stub just rich enough for each file to evaluate its top level.
// console.js assigns button handlers and calls connect() immediately.
function freshSandbox() {
  const node = () => ({
    set onclick(_fn) {}, get onclick() { return null; },
    appendChild() {}, setAttribute() {}, addEventListener() {},
    style: {}, classList: { add() {}, remove() {} },
    innerHTML: "", textContent: "", value: "", children: [],
  });
  const sandbox = {
    console,
    document: {
      getElementById: () => node(),
      createElement: () => node(),
      querySelector: () => node(),
      querySelectorAll: () => [],
    },
    location: { host: "localhost:1", protocol: "http:" },
    setTimeout() {}, clearTimeout() {},
    WebSocket: function () { this.send = () => {}; },
  };
  sandbox.window = sandbox;          // as in a browser
  return sandbox;
}

// The names each script contributes to the shared global scope.
function globalsDefinedBy(file) {
  const sandbox = freshSandbox();
  vm.createContext(sandbox);
  const before = new Set(Object.keys(sandbox));
  const src = fs.readFileSync(path.join(STATIC, file), "utf8");
  vm.runInContext(src, sandbox, { filename: file });
  return new Set(Object.keys(sandbox).filter((k) => !before.has(k)));
}

const defined = {};
for (const file of scripts) defined[file] = globalsDefinedBy(file);

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`ok   ${name}`); }
  catch (e) { failures++; console.error(`FAIL ${name}\n     ${e.message}`); }
}

check("no two scripts define the same global name", () => {
  const collisions = [];
  for (let i = 0; i < scripts.length; i++) {
    for (let j = i + 1; j < scripts.length; j++) {
      const a = scripts[i], b = scripts[j];
      for (const nameA of defined[a]) {
        if (defined[b].has(nameA)) collisions.push(`${nameA}: ${a} vs ${b}`);
      }
    }
  }
  assert.deepStrictEqual(collisions, [],
    "these globals are defined by more than one script, so whichever loads "
    + "last silently wins:\n  " + collisions.join("\n  "));
});

check("room.js exports exactly its two entry points", () => {
  assert.deepStrictEqual([...defined["room.js"]].sort(),
    ["renderRoom", "renderRoomFrame"]);
});

check("triggers.js exports exactly its three entry points", () => {
  assert.deepStrictEqual([...defined["triggers.js"]].sort(),
    ["renderTriggerDevices", "renderTriggerFired", "renderTriggers"]);
});

check("console.js can reach every name it dispatches to", () => {
  const exported = new Set([...defined["room.js"], ...defined["triggers.js"]]);
  for (const needed of ["renderRoom", "renderRoomFrame", "renderTriggers",
                        "renderTriggerDevices", "renderTriggerFired"]) {
    assert.ok(exported.has(needed), `console.js calls ${needed}, nothing exports it`);
  }
});

process.exit(failures ? 1 : 0);
