"use strict";
// busy.js overlay: the current stage carries a live "· N s" that keeps
// counting between server events, resets on a new stage, and stops when the
// load ends or fails.
const assert = require("node:assert");
const { mock } = require("node:test");
const { FakeSocket } = require("./_dom_stub.js");

function findByClass(node, cls) {
  if (node.className && node.className.split(" ").includes(cls)) return node;
  for (const c of node.children) {
    const found = findByClass(c, cls);
    if (found) return found;
  }
  return null;
}

(async () => {
  mock.timers.enable({ apis: ["setInterval", "Date"] });
  const wire = await import("../../console/static/wire.js");
  const busy = await import("../../console/static/busy.js");
  busy.init();
  wire.connect({ WebSocketImpl: FakeSocket, retryMs: 5 });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });
  const texts = () => findByClass(busy._overlay(), "stages").children.map((c) => c.textContent);

  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  send({ event: "room_load_progress", stage: "validating" });
  send({ event: "room_load_progress", stage: "spawning arco" });
  assert.deepStrictEqual(texts(), ["validating", "spawning arco"]);

  // server silent: the counter still moves, on the current stage only
  mock.timers.tick(7000);
  assert.deepStrictEqual(texts(), ["validating", "spawning arco · 7 s"]);

  // a new stage resets the count; the previous line keeps its final time
  send({ event: "room_load_progress", stage: "binding fixtures" });
  assert.deepStrictEqual(texts(), ["validating", "spawning arco · 7 s", "binding fixtures"]);
  mock.timers.tick(2000);
  assert.deepStrictEqual(texts(), ["validating", "spawning arco · 7 s", "binding fixtures · 2 s"]);

  // load finishes: overlay gone, nothing left ticking
  send({ event: "room_loaded", name: "DEMO" });
  assert.strictEqual(busy._overlay(), null);
  mock.timers.tick(5000);

  // failure stops the counter too
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  send({ event: "room_load_progress", stage: "spawning arco" });
  mock.timers.tick(3000);
  send({ event: "room_load_failed", name: "DEMO", reason: "arco did not start" });
  assert.ok(busy._overlay());
  assert.ok(!busy._overlay().innerHTML.includes(" s<"), "no counter on the failure panel");
  mock.timers.tick(5000);
  assert.ok(!busy._overlay().innerHTML.includes("· 8 s"), "nothing ticks after failure");
  mock.timers.reset();
})();
