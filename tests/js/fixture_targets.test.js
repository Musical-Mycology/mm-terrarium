"use strict";
// Fixture targets: every declared Room fixture is a SURFACE/Diagnostics
// picker row by NAME (value @fixture:<name>), bound or not, with its mute
// state in the label; devices bound to a fixture are not listed twice;
// DEVICE pickers never offer a fixture; a controllers-only room_changed
// never refills a picker.
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const STROBE = {
  kind: "scripted", name: "strobe", description: "Bit fallback", target: "SURFACE",
  condition: null, script: [],
};
const HOLD = {
  kind: "scripted", name: "hold_flash", description: "Hold", target: "DEVICE",
  condition: { name: "held", description: "held", source: "gesture-verb", verb: "hold" },
  script: [{ offset: 0.0, kind: "play", dev: "@target", name: "hold", params: {} }],
};
const INSTRUMENT_FUNCTIONS = {
  dev_strip_accent: [{ kind: "scripted", name: "strobe", description: "Accent strobe",
                       target: "SURFACE", condition: null, script: [] }],
  dev_strip_main: [], tuneshroom: [],
};
const SURFACE_INSTRUMENTS = {
  "sim-room-main": "dev_strip_main", "@fixture:main": "dev_strip_main",
  "@fixture:accent": "dev_strip_accent", "ie1": "tuneshroom",
};
const BUILTINS = { dev_strip_main: ["flash", "stop"], dev_strip_accent: ["flash", "stop"],
                   tuneshroom: ["flash", "ping", "stop"] };
const room = (fixtures, controllers = {}) => ({
  room_type: "DEMO", capability: { pixel_count: 90, color_order: "GRB", zones: [] },
  fixtures, instruments: [], controllers });
const MAIN = { name: "main", dev: "sim-room-main", zones: [], muted: false };
const ACCENT = { name: "accent", dev: null, zones: [], muted: false };
const DEVICES = [{ dev: "sim-room-main", name: "Room", role: null, fixture: "main" },
                 { dev: "ie1", name: "Shroom", role: "player" }];

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
  const labels = (p) => [...p.options].map((o) => o.textContent);

  send({ event: "snapshot", state: "RUNNING", loaded_bit: "X", roles: [], registration: [],
         devices: DEVICES, bit_status: {}, functions: [STROBE, HOLD],
         room: room([MAIN, ACCENT]), instrument_functions: INSTRUMENT_FUNCTIONS,
         surface_instruments: SURFACE_INSTRUMENTS, builtins: BUILTINS });

  // ---- SURFACE picker: All, fixtures by name in order, then unbound devices
  const surf = byId.get("functionDev_strobe");
  assert.deepStrictEqual(values(surf), ["@all", "@fixture:main", "@fixture:accent", "ie1"]);
  assert.deepStrictEqual(labels(surf),
    ["All", "main (sim-room-main)", "accent (unbound)", "ie1"]);

  // ---- Diagnostics picker: same rows
  assert.deepStrictEqual(values(functions._diagPicker()),
    ["@all", "@fixture:main", "@fixture:accent", "ie1"]);

  // ---- DEVICE picker never offers a fixture
  assert.deepStrictEqual(values(byId.get("functionDev_hold_flash")), ["ie1"]);

  // ---- Fire at an unbound fixture whose instrument has the function
  surf.value = "@fixture:accent";
  surf.onchange();
  const fire = functions._fireBtnFor("strobe");
  assert.strictEqual(fire.disabled, false, fire.title);
  fire.onclick();
  assert.deepStrictEqual(sock.sent.at(-1),
    { command: "fire_function", name: "strobe", dev: "@fixture:accent" });

  // ---- Not available on a fixture whose instrument lacks it: reason names the fixture
  surf.value = "@fixture:main";
  surf.onchange();
  assert.strictEqual(fire.disabled, true);
  assert.ok(/Not available on main/.test(fire.title), fire.title);
  surf.value = "@fixture:accent";
  surf.onchange();

  // ---- a controllers-only room_changed does not refill (option identity survives)
  const optBefore = surf.options[2];
  send({ event: "room_changed", room: room([MAIN, ACCENT], { 74: 12 }) });
  assert.strictEqual(surf.options[2], optBefore, "controllers tick must not refill the picker");
  assert.strictEqual(surf.value, "@fixture:accent");

  // ---- a mute change refills and labels, keeping the selection
  send({ event: "room_changed", room: room([MAIN, { ...ACCENT, muted: true }]) });
  assert.deepStrictEqual(labels(surf),
    ["All", "main (sim-room-main)", "accent (unbound) (muted)", "ie1"]);
  assert.strictEqual(surf.value, "@fixture:accent");

  // ---- a row without `muted` (older server) reads as unmuted
  send({ event: "room_changed", room: room([{ name: "main", dev: "sim-room-main", zones: [] }]) });
  assert.deepStrictEqual(labels(surf), ["All", "main (sim-room-main)", "ie1"]);

  // ---- no Room: no fixture rows
  send({ event: "room_changed", room: null });
  assert.deepStrictEqual(values(surf), ["@all", "ie1"]);

  console.log("fixture_targets: ok");
})().catch((e) => { console.error(e); process.exit(1); });
