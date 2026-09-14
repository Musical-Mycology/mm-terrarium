"use strict";
// Loading overlay: state-driven (ROOM_LOADING/ROOM_UNLOADING), shows the
// latest room_load_progress stage, closes when the Terrarium settles, and
// stays open with the reason on failure until dismissed.
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

function findByClass(node, cls) {
  if (node.className && node.className.split(" ").includes(cls)) return node;
  for (const c of node.children) {
    const found = findByClass(c, cls);
    if (found) return found;
  }
  return null;
}

(async () => {
  const wire = await import("../../console/static/wire.js");
  const busy = await import("../../console/static/busy.js");
  busy.init();
  wire.connect({ WebSocketImpl: FakeSocket, retryMs: 5 });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // settled: nothing shown
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [], registration: [],
         devices: [], bit_status: {}, room: null, functions: [], terrarium_state: "NO_ROOM",
         rooms: [{ name: "DEMO", description: "", status: null, active: false }] });
  assert.strictEqual(busy._overlay(), null);

  // ROOM_LOADING opens it, titled with the room the Console asked for
  wire.send("load_room", { name: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  const overlay = busy._overlay();
  assert.ok(overlay, "overlay opens on ROOM_LOADING");
  // Fetched fresh (not hoisted above) because the stub's document.getElementById
  // only registers #overlayMount in byId on first access, which busy.begin()
  // triggers here; the same div persists for the rest of the test after that.
  const mount = byId.get("overlayMount");
  assert.ok(mount.innerHTML.includes("Loading Room DEMO"));
  assert.ok(!findByClass(overlay, "btn"), "no Dismiss while in progress");

  // stages accumulate; the latest is marked current
  send({ event: "room_load_progress", stage: "validating" });
  send({ event: "room_load_progress", stage: "spawning arco" });
  assert.strictEqual(busy._overlay(), overlay, "progress never rebuilds the overlay");
  const stages = findByClass(overlay, "stages");
  assert.deepStrictEqual(stages.children.map((c) => c.textContent), ["validating", "spawning arco"]);
  assert.ok(stages.children[0].className.includes("done"));
  assert.ok(stages.children[1].className.includes("current"));

  // room_loaded closes it
  send({ event: "room_loaded", name: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_READY" });
  assert.strictEqual(busy._overlay(), null);

  // a snapshot alone (reconnect mid-load) opens it too
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [], registration: [],
         devices: [], bit_status: {}, room: null, functions: [], terrarium_state: "ROOM_UNLOADING",
         rooms: [{ name: "DEMO", description: "", status: null, active: true }] });
  assert.ok(busy._overlay());
  assert.ok(mount.innerHTML.includes("Unloading Room DEMO"));
  send({ event: "room_unloaded", name: "DEMO" });
  assert.strictEqual(busy._overlay(), null);

  // failure keeps it open with the reason and a Dismiss button
  wire.send("load_room", { name: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  send({ event: "room_load_failed", name: "DEMO", reason: "arco did not start" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "NO_ROOM" });
  const failed = busy._overlay();
  assert.ok(failed, "failure keeps the overlay up despite NO_ROOM");
  assert.ok(mount.innerHTML.includes("arco did not start"));
  const dismiss = findByClass(failed, "btn");
  assert.ok(dismiss && dismiss.textContent === "Dismiss");
  dismiss.onclick();
  assert.strictEqual(busy._overlay(), null);

  // an error on load_bit while up also fails it
  wire.send("load_bit", { name: "MetronomeBit", room: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  send({ event: "error", command: "load_bit", message: "no such bit" });
  assert.ok(mount.innerHTML.includes("no such bit"));
  findByClass(busy._overlay(), "btn").onclick();
  assert.strictEqual(busy._overlay(), null);

  console.log("busy: ok");
})().catch((e) => { console.error(e); process.exit(1); });
