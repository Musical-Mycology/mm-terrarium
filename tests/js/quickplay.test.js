"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

function findByTag(node, tag) {
  if (node.tagName === tag) return node;
  for (const c of node.children) {
    const found = findByTag(c, tag);
    if (found) return found;
  }
  return null;
}

const METRONOME = { name: "MetronomeBit", display_name: "Metronome", enabled: true,
                    room_types: ["VENUE", "DEMO", "TEST"], default_room_type: "DEMO" };
const ROOMS = [{ name: "VENUE", status: "no [[artnet]]", active: false },
               { name: "TEST", status: null, active: false },
               { name: "DEMO", status: null, active: false }];

(async () => {
  const wire = await import("../../console/static/wire.js");
  const quickplay = await import("../../console/static/quickplay.js");
  quickplay.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });
  const btn = findByTag(byId.get("quickPlay"), "button");

  // disabled until the Bit is discovered
  send({ event: "snapshot", state: "IDLE", loaded_bit: null,
         terrarium_state: "NO_ROOM", rooms: ROOMS });
  assert.strictEqual(btn.disabled, true, "no bits_listed yet");
  send({ event: "bits_listed", bits: [METRONOME], errors: [] });
  assert.strictEqual(btn.disabled, false);

  // NO_ROOM: loads into the Bit's default Room when it is loadable
  btn.onclick();
  assert.deepStrictEqual(sock.sent.at(-1),
    { command: "load_bit", name: "MetronomeBit", overrides: {}, room: "DEMO" });
  assert.strictEqual(btn.disabled, true, "disabled while starting");

  // reaching SETUP sends run exactly once
  send({ event: "state_changed", state: "SETUP", loaded_bit: "MetronomeBit",
         terrarium_state: "ROOM_READY" });
  assert.deepStrictEqual(sock.sent.at(-1), { command: "run" });
  const sentCount = sock.sent.length;
  send({ event: "state_changed", state: "SETUP", loaded_bit: "MetronomeBit",
         terrarium_state: "ROOM_READY" });
  assert.strictEqual(sock.sent.length, sentCount, "run is not resent");

  send({ event: "state_changed", state: "RUNNING", loaded_bit: "MetronomeBit",
         terrarium_state: "ROOM_READY" });
  assert.strictEqual(btn.textContent, "Playing…");
  assert.strictEqual(btn.disabled, true);

  // another Bit loaded: unavailable
  send({ event: "state_changed", state: "SETUP", loaded_bit: "ChaseBit",
         terrarium_state: "ROOM_READY" });
  assert.strictEqual(btn.disabled, true);

  // ROOM_READY with an active Room: loads into that Room
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_READY",
         rooms: [{ name: "VENUE", status: null, active: true }, ROOMS[0]] });
  btn.onclick();
  assert.strictEqual(sock.sent.at(-1).room, "VENUE");
  send({ event: "error", command: "load_bit", message: "nope" });

  // an active Room the Bit does not support: disabled, with the reason
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_READY",
         rooms: [{ name: "MIDITEST", status: null, active: true }] });
  assert.strictEqual(btn.disabled, true);
  assert.strictEqual(btn.title, "Metronome does not support Room MIDITEST");
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, terrarium_state: "ROOM_READY",
         rooms: [{ name: "VENUE", status: null, active: true }, ROOMS[2]] });
  btn.onclick();

  // a refused load clears the pending run
  send({ event: "error", command: "load_bit", message: "nope" });
  assert.strictEqual(btn.disabled, false);
  send({ event: "state_changed", state: "SETUP", loaded_bit: "MetronomeBit",
         terrarium_state: "ROOM_READY" });
  assert.strictEqual(sock.sent.at(-1).command, "load_bit", "no run after a refusal");

  // already in SETUP: one click just runs
  btn.onclick();
  assert.deepStrictEqual(sock.sent.at(-1), { command: "run" });

  console.log("quickplay ok");
})().catch((e) => { console.error(e); process.exit(1); });
