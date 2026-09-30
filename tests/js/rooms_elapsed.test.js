"use strict";
// rooms.js card status line: "stage · N s" ticking client-side, reset per
// stage, cleared when the load ends.
const assert = require("node:assert");
const { mock } = require("node:test");
const { FakeSocket } = require("./_dom_stub.js");

const ROOMS = [{ name: "TEST", description: "", status: null, active: false }];

(async () => {
  mock.timers.enable({ apis: ["setInterval", "Date"] });
  const wire = await import("../../console/static/wire.js");
  const rooms = await import("../../console/static/rooms.js");
  rooms.init();
  wire.connect({ WebSocketImpl: FakeSocket, retryMs: 5 });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [], registration: [],
         devices: [], bit_status: {}, functions: [], room: null,
         terrarium_state: "NO_ROOM", rooms: ROOMS });
  const card = rooms._cardFor("TEST");
  rooms._loadBtnFor("TEST").onclick();

  send({ event: "room_load_progress", stage: "spawning arco" });
  assert.ok(card.innerHTML.includes("spawning arco"));
  mock.timers.tick(7000);
  assert.ok(card.innerHTML.includes("spawning arco · 7 s"));

  send({ event: "room_load_progress", stage: "binding fixtures" });
  assert.ok(card.innerHTML.includes("binding fixtures"));
  assert.ok(!card.innerHTML.includes("binding fixtures ·"));
  mock.timers.tick(3000);
  assert.ok(card.innerHTML.includes("binding fixtures · 3 s"));

  send({ event: "room_load_failed", name: "TEST", reason: "x" });
  mock.timers.tick(5000);
  assert.ok(!card.innerHTML.includes("· 8 s"), "nothing ticks after the load ends");
  assert.ok(!card.innerHTML.includes("· 4 s"), "nothing ticks after the load ends");
  mock.timers.reset();
})();
