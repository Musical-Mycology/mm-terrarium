"use strict";
// An [[artnet]]-covered fixture never binds a device: the Room panel shows
// an Art-Net chip and no Arm button. An arm_room refusal rolls back the
// optimistic Armed chip on an uncovered fixture.
const assert = require("node:assert");
const { FakeSocket } = require("./_dom_stub.js");

const fixture = (name, artnet) => ({
  name, dev: null, url: null, muted: false, artnet,
  pixel_count: 10, channel_start: 0, channel_count: 30,
  color_order: "GRB", zones: [],
  instrument: { name: "generic_surface", capabilities: ["light.surface"],
                functions: [], accepted_cues: ["solid", "mute"] } });
const room = () => ({
  room_type: "DEMO", capability: { pixel_count: 20, color_order: "GRB", zones: [] },
  fixtures: [fixture("array", true), fixture("accent", false)],
  instruments: [], controllers: {} });

const texts = (el) => el.children.map((c) => c.textContent);
const buttons = (el) => el.children.filter((c) => c.tagName === "button");

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");
  surface.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", room: room() });

  // Covered: Art-Net chip, no Arm.
  const arrayCtl = surface._bindCtlFor("array");
  assert.ok(texts(arrayCtl).includes("Art-Net"), "covered fixture shows Art-Net");
  assert.strictEqual(buttons(arrayCtl).length, 0, "covered fixture offers no Arm");

  // Uncovered: Not bound + Arm, as today.
  let accentCtl = surface._bindCtlFor("accent");
  assert.ok(texts(accentCtl).includes("Not bound"));
  const arm = buttons(accentCtl).find((b) => b.textContent === "Arm");
  assert.ok(arm, "uncovered fixture still offers Arm");

  // Arm + Confirm shows Armed.
  arm.onclick();
  const formRow = accentCtl.children.find((c) => c.className === "armrow");
  const confirm = formRow.children.find((c) => c.textContent === "Confirm");
  confirm.onclick();
  assert.ok(texts(surface._bindCtlFor("accent")).includes("Armed"));

  // A refusal for another command leaves Armed alone.
  send({ event: "error", command: "fire_function", message: "nope" });
  assert.ok(texts(surface._bindCtlFor("accent")).includes("Armed"));

  // An arm_room refusal rolls it back to Not bound + Arm.
  send({ event: "error", command: "arm_room", message: "refused" });
  accentCtl = surface._bindCtlFor("accent");
  assert.ok(texts(accentCtl).includes("Not bound"), "refusal rolls back Armed");
  assert.ok(buttons(accentCtl).some((b) => b.textContent === "Arm"));

  // A row without `artnet` (older server) still offers Arm.
  const legacy = room();
  delete legacy.fixtures[0].artnet;
  send({ event: "room_changed", room: legacy });
  assert.ok(buttons(surface._bindCtlFor("array")).some((b) => b.textContent === "Arm"));

  console.log("fixture_artnet_arm: ok");
})().catch((e) => { console.error(e); process.exit(1); });
