"use strict";
// The Room panel's per-fixture Muted chip: shown only while that fixture is
// latched mute, toggled in place so a mute change never rebuilds the head
// (an armed Release confirm-tap must survive it).
const assert = require("node:assert");
const { FakeSocket } = require("./_dom_stub.js");

const fixture = (name, dev, muted) => ({
  name, dev, url: null, muted, pixel_count: 10, channel_start: 0, channel_count: 30,
  color_order: "GRB", zones: [],
  instrument: { name: "generic_surface", capabilities: ["light.surface"],
                functions: [], accepted_cues: ["solid", "mute"] } });
const room = (mainMuted, accentMuted) => ({
  room_type: "DEMO", capability: { pixel_count: 20, color_order: "GRB", zones: [] },
  fixtures: [fixture("main", "sim-room-main", mainMuted), fixture("accent", null, accentMuted)],
  instruments: [], controllers: {} });

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");
  surface.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", room: room(false, false) });
  assert.strictEqual(surface._muteChipFor("main").hidden, true);
  assert.strictEqual(surface._muteChipFor("accent").hidden, true);
  assert.strictEqual(surface._muteChipFor("accent").textContent, "Muted");

  // Arm main's Release confirm-tap, then mute main: head untouched, chip shown.
  const bindCtl = surface._bindCtlFor("main");
  const release = bindCtl.children.find((c) => c.tagName === "button");
  release.onclick();
  const armedText = release.textContent;
  send({ event: "room_changed", room: room(true, false) });
  assert.strictEqual(surface._bindCtlFor("main"), bindCtl, "mute must not rebuild the head");
  assert.strictEqual(release.textContent, armedText, "armed Release survives a mute change");
  assert.strictEqual(surface._muteChipFor("main").hidden, false);
  assert.strictEqual(surface._muteChipFor("accent").hidden, true);

  // An unbound fixture's mute shows too; unmuting hides it again.
  send({ event: "room_changed", room: room(false, true) });
  assert.strictEqual(surface._muteChipFor("main").hidden, true);
  assert.strictEqual(surface._muteChipFor("accent").hidden, false);

  // A bind-state change rebuilds the head; the new chip carries current state.
  const accentBound = room(false, true);
  accentBound.fixtures[1].dev = "sim-room-accent";
  send({ event: "room_changed", room: accentBound });
  assert.strictEqual(surface._muteChipFor("accent").hidden, false);

  // A row without `muted` (older server) reads as unmuted.
  const legacy = room(false, false);
  delete legacy.fixtures[0].muted;
  send({ event: "room_changed", room: legacy });
  assert.strictEqual(surface._muteChipFor("main").hidden, true);

  console.log("fixture_mute_chip: ok");
})().catch((e) => { console.error(e); process.exit(1); });
