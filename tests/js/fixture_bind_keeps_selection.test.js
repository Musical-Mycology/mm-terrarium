"use strict";
// When an operator's selected device binds to a fixture, the SURFACE/
// Diagnostics picker must keep the operator's target -- now expressed as
// the fixture -- instead of falling back to @all with Fire/Stop still
// enabled (one Stop would then mute every surface). Covers both arrival
// orders: devices_changed carrying the new binding before room_changed,
// and room_changed carrying it before devices_changed.
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const STROBE = {
  kind: "scripted", name: "strobe", description: "Bit fallback", target: "SURFACE",
  condition: null, script: [],
};
const room = (fixtures) => ({
  room_type: "DEMO", capability: { pixel_count: 90, color_order: "GRB", zones: [] },
  fixtures, instruments: [], controllers: {} });

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");
  const functions = await import("../../console/static/functions.js");
  const rail = await import("../../console/static/rail.js");
  surface.init(); functions.init(); rail.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });
  const values = (p) => [...p.options].map((o) => o.value);

  // ---- devices_changed carries the new binding first
  send({ event: "snapshot", state: "RUNNING", loaded_bit: "X", roles: [], registration: [],
         devices: [{ dev: "ie1", name: "Shroom", role: "player" }], bit_status: {},
         functions: [STROBE], room: room([{ name: "main", dev: null, zones: [], muted: false }]),
         instrument_functions: {}, surface_instruments: {}, builtins: {} });

  const surf = byId.get("functionDev_strobe");
  assert.deepStrictEqual(values(surf), ["@all", "@fixture:main", "ie1"]);
  surf.value = "ie1";
  surf.onchange();
  assert.strictEqual(surf.value, "ie1");

  send({ event: "devices_changed",
         devices: [{ dev: "ie1", name: "Shroom", role: "player", fixture: "main" }] });
  assert.deepStrictEqual(values(surf), ["@all", "@fixture:main"]);
  assert.strictEqual(surf.value, "@fixture:main",
    "device binding to a fixture must keep the row selected as that fixture");

  // ---- reverse order: room_changed carries the new binding first (via
  // fnFixtures), and only later does devices_changed drop the device from
  // the unbound-devices list (it is now reachable solely as the fixture) --
  // by then fnDevices has no entry for it at all, so the fallback must come
  // from fnFixtures, not fnDevices.
  send({ event: "snapshot", state: "RUNNING", loaded_bit: "X", roles: [], registration: [],
         devices: [{ dev: "ie1", name: "Shroom", role: "player" }], bit_status: {},
         functions: [STROBE], room: room([{ name: "main", dev: null, zones: [], muted: false }]),
         instrument_functions: {}, surface_instruments: {}, builtins: {} });

  const surf2 = byId.get("functionDev_strobe");
  surf2.value = "ie1";
  surf2.onchange();
  assert.strictEqual(surf2.value, "ie1");

  send({ event: "room_changed",
         room: room([{ name: "main", dev: "ie1", zones: [], muted: false }]) });
  assert.strictEqual(surf2.value, "ie1", "device row still offered: no fallback needed yet");

  send({ event: "devices_changed", devices: [] });
  assert.deepStrictEqual(values(surf2), ["@all", "@fixture:main"]);
  assert.strictEqual(surf2.value, "@fixture:main",
    "fnFixtures must resolve the fixture row when fnDevices no longer has the device at all");

  console.log("fixture_bind_keeps_selection: ok");
})().catch((e) => { console.error(e); process.exit(1); });
