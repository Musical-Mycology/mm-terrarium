"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const TRIGGERS = [
  { name: "fireworks_player", description: "Celebratory flashes", target: "DEVICE",
    condition: { name: "phrase_success", description: "Player matches the call phrase",
                 source: "bit-adjudicated", verb: null },
    script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 },
             { offset: 1.4, kind: "light", dev: "@target", status: 176, data1: 70, data2: 0 }] },
  { name: "finale", description: "Closing sweep", target: "ROOM",
    condition: { name: "run_complete", description: "All cycles finished",
                 source: "bit-adjudicated", verb: null },
    script: [{ offset: 0.0, kind: "play", dev: "@target", name: "sweep", params: {} }] },
];

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");
  const triggers = await import("../../console/static/triggers.js");
  const rail = await import("../../console/static/rail.js");
  surface.init(); triggers.init(); rail.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", state: "RUNNING", loaded_bit: "MetronomeBit",
         roles: [], registration: [{ role: "player", count: 2, capacity: 2 }],
         devices: [{ dev: "ie1", name: "Tuneshroom 1", role: "player" },
                   { dev: "sim-room", name: "Room", role: null }],
         bit_status: {}, triggers: TRIGGERS,
         room: { room_type: "DEMO", capability: { pixel_count: 864,
                 color_order: "GRB", zones: [] }, fixtures: [], instruments: [],
                 controllers: {} } });

  const mount = byId.get("triggersMount");
  assert.ok(mount.innerHTML.includes("fireworks_player"));
  assert.ok(mount.innerHTML.includes("2 steps · 1.4s"));   // count + span
  assert.ok(mount.innerHTML.includes("never fired"));
  // device picker only on DEVICE targets, offering live devices
  assert.ok(mount.innerHTML.includes("ie1"));

  // a repeat triggers_changed with identical content must not rebuild (rule 1)
  const before = triggers._cardFor("fireworks_player");
  send({ event: "triggers_changed", triggers: TRIGGERS });
  assert.strictEqual(triggers._cardFor("fireworks_player"), before);

  // trigger_fired updates the one line, tags admin-manual
  send({ event: "trigger_fired", fired: { name: "finale", fired_by: "admin-manual",
         declared_source: "bit-adjudicated", dev: null, devs: ["sim-room"],
         at: 12.5, steps: 1 } });
  assert.ok(mount.innerHTML.includes("Admin manual"));

  // rail: registration meter, devices, log severities
  assert.ok(byId.get("registrationCard").innerHTML.includes("2/2"));
  assert.ok(byId.get("devicesCard").innerHTML.includes("Tuneshroom 1"));
  send({ event: "log", level: "error", message: "boom" });
  assert.ok(byId.get("logCard").innerHTML.includes("boom"));
  send({ event: "bit_completed", result: { phrases: 4 }, bit_name: "MetronomeBit" });
  assert.ok(byId.get("logCard").innerHTML.includes("phrases"));

  console.log("triggers_and_rail: ok");
})().catch((e) => { console.error(e); process.exit(1); });
