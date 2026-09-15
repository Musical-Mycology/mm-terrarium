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

  // failure keeps it open with the reason and a Dismiss button. Real
  // backend order is state_changed(NO_ROOM) BEFORE room_load_failed
  // (Terrarium._set_state notifies observers synchronously from inside
  // load_room(), before load_room() has returned the reason to the caller
  // that broadcasts room_load_failed). The settle-triggered close still
  // fires unconditionally on that state_changed (closing the "Loading Room
  // DEMO" overlay), and room_load_failed's fail() reopens a fresh one --
  // but fail() rebuilds the same title from roomName() (module state the
  // close never touches), so the title is identical before and after.
  wire.send("load_room", { name: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "NO_ROOM" });
  send({ event: "room_load_failed", name: "DEMO", reason: "arco did not start" });
  const failed = busy._overlay();
  assert.ok(failed, "failure keeps the overlay up despite NO_ROOM");
  assert.ok(mount.innerHTML.includes("Loading Room DEMO"), "title survives the failure");
  assert.ok(mount.innerHTML.includes("arco did not start"));
  const dismiss = findByClass(failed, "btn");
  assert.ok(dismiss && dismiss.textContent === "Dismiss");
  dismiss.onclick();
  assert.strictEqual(busy._overlay(), null);

  // an error on load_bit while up also fails it. In the real backend a
  // load_bit that has to bring up a Room routes through the same
  // _load_room a direct load_room uses (console/agent.py's
  // _ensure_room_for_bit), so a room-load failure here broadcasts
  // room_load_failed (same as above) AND separately replies to this tab
  // with a targeted error for the load_bit command -- room_load_failed
  // first, since it is broadcast synchronously inside _load_room before
  // that call returns up to the command handler that builds the error.
  // fail() runs twice for the same text: a known, harmless double-fail
  // (one of the four minor findings deferred to the whole-branch review),
  // not something fixed here. Same real ordering as above
  // (state_changed(NO_ROOM) before either failure event) and same title
  // preservation.
  wire.send("load_bit", { name: "MetronomeBit", room: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "NO_ROOM" });
  send({ event: "room_load_failed", name: "DEMO", reason: "no such bit" });
  send({ event: "error", command: "load_bit", message: "no such bit" });
  assert.ok(mount.innerHTML.includes("Loading Room DEMO"), "title survives the failure");
  assert.ok(mount.innerHTML.includes("no such bit"));
  findByClass(busy._overlay(), "btn").onclick();
  assert.strictEqual(busy._overlay(), null);

  // A disconnect mid-load must self-heal, not stick the overlay open
  // forever: if the load fails while this client is disconnected, the
  // server's room_load_failed goes out to nobody, and console/agent.py's
  // poll() sends only a point-in-time snapshot to a reconnecting client,
  // never a replay of missed events. The settle-close is intentionally
  // unconditional (not guarded on the previous state) so the reconnect
  // snapshot's NO_ROOM still closes a stale overlay rather than leaving a
  // Dismiss-less modal stuck open forever. Mirrors full_stack.test.js's
  // disconnect/reconnect idiom (short retryMs, wait, grab the new
  // FakeSocket instance).
  wire.send("load_room", { name: "DEMO" });
  send({ event: "state_changed", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_LOADING" });
  assert.ok(busy._overlay(), "overlay open before the disconnect");
  sock.onclose();
  await new Promise((resolve) => setTimeout(resolve, 30));
  const sock2 = FakeSocket.instances.at(-1);
  assert.notStrictEqual(sock2, sock, "reconnect should open a new socket instance");
  sock2.onopen();
  const send2 = (m) => sock2.onmessage({ data: JSON.stringify(m) });
  send2({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [], registration: [],
          devices: [], bit_status: {}, room: null, functions: [], terrarium_state: "NO_ROOM",
          rooms: [{ name: "DEMO", description: "", status: null, active: false }] });
  assert.strictEqual(busy._overlay(), null, "reconnect snapshot must close a stale overlay, not leave it stuck");

  console.log("busy: ok");
})().catch((e) => { console.error(e); process.exit(1); });
