"use strict";
// Rooms panel: one card per configured room, load/unload with progress
// updates in place (card list children preserved -- same discipline as
// functions.js), Load disabled while a room is active or unloadable, Unload
// via confirmTap surviving a progress event without losing its armed state.
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const ROOMS = [
  { name: "TEST", description: "Test room", status: null, active: false },
  { name: "OTHER", description: "Other room", status: null, active: false },
  { name: "BROKEN", description: "Broken room", status: "missing device map", active: false },
];

function snapshotMsg(rooms, terrarium_state) {
  return {
    event: "snapshot", state: "IDLE", loaded_bit: null,
    roles: [], registration: [], devices: [], bit_status: {}, functions: [],
    room: null, terrarium_state, rooms,
  };
}

(async () => {
  const wire = await import("../../console/static/wire.js");
  const rooms = await import("../../console/static/rooms.js");

  rooms.init();
  wire.connect({ WebSocketImpl: FakeSocket, retryMs: 5 });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // 1. One card per configured room, keyed by name.
  send(snapshotMsg(ROOMS, "NO_ROOM"));
  const mount = byId.get("roomsPanel");
  assert.ok(mount.innerHTML.includes("TEST"));
  assert.ok(mount.innerHTML.includes("OTHER"));
  assert.ok(mount.innerHTML.includes("BROKEN"));

  const cardTest = rooms._cardFor("TEST");
  const cardOther = rooms._cardFor("OTHER");
  const cardBroken = rooms._cardFor("BROKEN");
  assert.ok(cardTest, "TEST card should exist");
  assert.ok(cardOther, "OTHER card should exist");
  assert.ok(cardBroken, "BROKEN card should exist");

  // Unloadable room: reason shown, Load disabled.
  assert.ok(cardBroken.innerHTML.includes("missing device map"));
  const brokenLoadBtn = rooms._loadBtnFor("BROKEN");
  assert.strictEqual(brokenLoadBtn.disabled, true);

  // 2. Load sends the command.
  const testLoadBtn = rooms._loadBtnFor("TEST");
  assert.strictEqual(testLoadBtn.disabled, false);
  testLoadBtn.onclick();
  assert.strictEqual(sock.sent.length, 1);
  assert.deepStrictEqual(sock.sent[0], { command: "load_room", name: "TEST" });

  // 3. room_load_progress updates only the active card's status line,
  //    preserving card list child identity (no rebuild).
  const gridBefore = mount.children[0];
  const childrenBefore = gridBefore.children.slice();
  send({ event: "room_load_progress", stage: "validating" });
  assert.ok(cardTest.innerHTML.includes("validating"));
  assert.strictEqual(cardOther.innerHTML.includes("validating"), false);
  const gridAfter = mount.children[0];
  assert.strictEqual(gridAfter, gridBefore, "grid node identity preserved");
  assert.deepStrictEqual(gridAfter.children, childrenBefore, "card children preserved across progress");
  assert.strictEqual(rooms._cardFor("TEST"), cardTest, "TEST card identity preserved");

  // 4. room_loaded flips terrarium_state via state_changed and re-renders
  //    rooms with TEST active; Load disabled on every room while one is
  //    active.
  send({ event: "room_loaded", name: "TEST" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_READY" });
  send(snapshotMsg(
    [
      { name: "TEST", description: "Test room", status: null, active: true },
      { name: "OTHER", description: "Other room", status: null, active: false },
      { name: "BROKEN", description: "Broken room", status: "missing device map", active: false },
    ],
    "ROOM_READY"));
  const otherLoadBtn = rooms._loadBtnFor("OTHER");
  assert.strictEqual(otherLoadBtn.disabled, true, "Load disabled on other rooms while one is active");

  // 5. Unload uses confirmTap and survives a progress event without losing
  //    armed state.
  const unloadBtn = rooms._unloadBtnFor("TEST");
  assert.ok(unloadBtn, "TEST card should have an unload button while active");
  sock.sent = [];
  unloadBtn.onclick();
  assert.strictEqual(unloadBtn.dataset.armed, "1", "first tap arms confirm");
  assert.strictEqual(sock.sent.length, 0, "first tap does not send");

  send({ event: "room_load_progress", stage: "tearing down" });
  assert.strictEqual(rooms._unloadBtnFor("TEST").dataset.armed, "1",
    "armed state survives a progress event");
  assert.strictEqual(rooms._unloadBtnFor("TEST"), unloadBtn,
    "unload button node identity preserved across progress");

  unloadBtn.onclick();
  assert.strictEqual(sock.sent.length, 1);
  assert.deepStrictEqual(sock.sent[0], { command: "unload_room", force: false });

  console.log("rooms_panel: ok");
})().catch((e) => { console.error(e); process.exit(1); });
