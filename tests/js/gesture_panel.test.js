"use strict";
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

function findAll(node, pred, out = []) {
  if (pred(node)) out.push(node);
  for (const c of node.children) findAll(c, pred, out);
  return out;
}

const DEVICES = [
  { dev: "ie1", muted: false, fixture: null },
  { dev: "sim-room-main", muted: false, fixture: "main" },
];

(async () => {
  const wire = await import("../../console/static/wire.js");
  const gestures = await import("../../console/static/gestures.js");
  gestures.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });
  const card = () => byId.get("gestureCard");
  const gestureMsgs = () => sock.sent.filter((m) => m.command === "gesture");
  const btn = (label) =>
    findAll(card(), (n) => n.tagName === "button" && n.textContent === label)[0];

  // hidden in IDLE even with a player device
  send({ event: "snapshot", state: "IDLE", devices: DEVICES });
  assert.strictEqual(card().hidden, true, "gesture panel hidden in IDLE");

  // hidden in SETUP with fixture-only devices
  send({ event: "snapshot", state: "SETUP", devices: [DEVICES[1]] });
  assert.strictEqual(card().hidden, true, "fixture-only devices: hidden");

  // visible in SETUP with a player device; picker offers only that device
  send({ event: "snapshot", state: "SETUP", devices: DEVICES });
  assert.strictEqual(card().hidden, false);
  const picker = findAll(card(), (n) => n.tagName === "select")[0];
  assert.deepStrictEqual(picker.options.map((o) => o.value), ["ie1"]);

  // visible in RUNNING too; hidden again when the state leaves
  send({ event: "state_changed", state: "RUNNING" });
  assert.strictEqual(card().hidden, false);

  // tap and double tap send count 1 and 2
  btn("Tap").onclick();
  btn("Double tap").onclick();
  assert.deepStrictEqual(gestureMsgs(), [
    { command: "gesture", dev: "ie1", verb: "tap", count: 1 },
    { command: "gesture", dev: "ie1", verb: "tap", count: 2 },
  ]);

  // tilt input sends gamma, and the read-out follows
  sock.sent.length = 0;
  const range = findAll(card(), (n) => n.tagName === "input")[0];
  assert.strictEqual(range.min, "-90");
  assert.strictEqual(range.max, "90");
  range.value = "35";
  range.oninput();
  const sent = gestureMsgs();
  assert.deepStrictEqual(sent.at(-1),
    { command: "gesture", dev: "ie1", verb: "tilt", gamma: 35 });
  assert.ok(card().textContent.includes("35"));

  // change sends the final value even inside the throttle window
  range.value = "40";
  const before = gestureMsgs().length;
  range.oninput();            // throttled away (inside 50 ms)
  assert.strictEqual(gestureMsgs().length, before, "oninput throttled");
  range.onchange();
  assert.deepStrictEqual(gestureMsgs().at(-1),
    { command: "gesture", dev: "ie1", verb: "tilt", gamma: 40 });

  // level sends 0 and resets the slider
  btn("Level").onclick();
  assert.deepStrictEqual(gestureMsgs().at(-1),
    { command: "gesture", dev: "ie1", verb: "tilt", gamma: 0 });
  assert.strictEqual(String(range.value), "0");

  // the panel hides once the Bit leaves SETUP/RUNNING
  send({ event: "state_changed", state: "COMPLETING" });
  assert.strictEqual(card().hidden, true);

  console.log("gesture_panel: ok");
})().catch((e) => { console.error(e); process.exit(1); });
