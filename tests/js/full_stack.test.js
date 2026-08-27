"use strict";
// Whole-graph collision-class guard: the ONLY test file that imports all
// seven real console modules together in one process. Every other panel
// test only imports the two-to-three modules it needs. This file replays
// a realistic session sequence across the whole graph and asserts nothing
// throws, plus a couple of specific end-to-end (cross-module) behaviors:
// dimmed on/off around a disconnect/reconnect, and the reconnect snapshot
// repopulating the Bit panel.
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const TRIGGERS = [
  { name: "fireworks_player", description: "Celebratory flashes", target: "DEVICE",
    condition: { name: "phrase_success", description: "Player matches the call phrase",
                 source: "bit-adjudicated", verb: null },
    script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 },
             { offset: 1.4, kind: "light", dev: "@target", status: 176, data1: 70, data2: 0 }] },
];

const ROOM = {
  room_type: "DEMO",
  capability: { pixel_count: 60, color_order: "GRB",
    zones: [{ name: "main.left", start: 0, count: 30 }] },
  fixtures: [
    { name: "main", pixel_count: 60, channel_start: 0, channel_count: 180,
      zones: [{ name: "main.left", start: 0, count: 30 },
              { name: "main.right", start: 30, count: 30 }], dev: "sim-room-main" },
  ],
  instruments: [],
  controllers: {},
};

function snapshotMsg() {
  return {
    event: "snapshot", state: "RUNNING", loaded_bit: "MetronomeBit",
    roles: [], registration: [{ role: "player", count: 2, capacity: 2 }],
    devices: [{ dev: "ie1", name: "Tuneshroom 1", role: "player" },
              { dev: "sim-room-main", name: "Room", role: null }],
    bit_status: {}, triggers: TRIGGERS, room: ROOM,
    terrarium_state: "ROOM_READY",
    rooms: [{ name: "DEMO", description: "", status: null, active: true }],
  };
}

(async () => {
  const wire = await import("../../console/static/wire.js");
  const shell = await import("../../console/static/shell.js");
  const bit = await import("../../console/static/bit.js");
  const surface = await import("../../console/static/surface.js");
  const triggers = await import("../../console/static/triggers.js");
  const rail = await import("../../console/static/rail.js");
  const rooms = await import("../../console/static/rooms.js");

  bit.init(); surface.init(); triggers.init(); rail.init(); rooms.init();
  // small retryMs so the auto-reconnect this test relies on fires quickly
  // instead of leaving a 1s timer hanging the process.
  wire.connect({ WebSocketImpl: FakeSocket, retryMs: 5 });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // 1. Loaded RUNNING Bit, bound Room with a fixture, declared triggers,
  //    devices, registration roles.
  send(snapshotMsg());
  const bitPanel = byId.get("bitPanel");
  assert.ok(bitPanel.innerHTML.includes("Metronome"), "bit panel should show loaded bit");
  assert.ok(byId.get("roomCard").innerHTML.includes("DEMO"));
  assert.ok(byId.get("registrationCard").innerHTML.includes("2/2"));
  assert.ok(byId.get("devicesCard").innerHTML.includes("Tuneshroom 1"));

  // 2. A few room_frame events for the bound fixture -- must only repaint,
  //    never throw or rebuild.
  for (let i = 0; i < 3; i++) {
    const channels = [];
    for (let p = 0; p < 60; p++) channels.push(10 + i, 20 + i, 30 + i);
    send({ event: "room_frame", dev: "sim-room-main", channels });
  }

  // 3. A trigger fires.
  send({ event: "trigger_fired", fired: { name: "fireworks_player", fired_by: "admin-manual",
         declared_source: "bit-adjudicated", dev: "ie1", devs: ["ie1"], at: 3.2, steps: 2 } });
  assert.ok(byId.get("triggersMount").innerHTML.includes("Admin manual"));

  // 4. An error for a command -- flashRefusal-style handling must not throw,
  //    even with no armed source element for "run".
  send({ event: "error", command: "run", message: "not enough players" });
  assert.ok(byId.get("logCard").innerHTML.includes("not enough players"));

  // 5. Disconnect: dimmed appears.
  assert.strictEqual(document.body.classList.contains("dimmed"), false);
  sock.onclose();
  assert.strictEqual(document.body.classList.contains("dimmed"), true);

  // 6. Reconnect: wire's own auto-retry (retryMs: 5) fires and opens a
  //    fresh socket; dimmed clears, and a fresh snapshot repopulates the
  //    sidebar/panel end to end.
  await new Promise((resolve) => setTimeout(resolve, 30));
  const sock2 = FakeSocket.instances.at(-1);
  assert.notStrictEqual(sock2, sock, "reconnect should open a new socket instance");
  sock2.onopen();
  assert.strictEqual(document.body.classList.contains("dimmed"), false);
  const send2 = (m) => sock2.onmessage({ data: JSON.stringify(m) });
  send2(snapshotMsg());
  const bitPanelAfter = byId.get("bitPanel");
  assert.ok(bitPanelAfter.innerHTML.length > 0, "bit panel should repopulate after reconnect");
  assert.ok(bitPanelAfter.innerHTML.includes("Metronome"), "bit panel should show bit name after reconnect");

  console.log("full_stack: ok");
})().catch((e) => { console.error(e); process.exit(1); });
