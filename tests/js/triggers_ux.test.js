"use strict";
// Triggers panel UX, driven by the Rev1Bit bring-up: a DEVICE trigger's
// picker offers devices only (never a Room fixture, which is what the
// first option used to be), Fire says why it is disabled, a Diagnostics
// button says why it is disabled, the empty state points at Diagnostics,
// and a fire briefly marks its row.
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const HOLD_FLASH = {
  kind: "scripted", name: "hold_flash", description: "Hold response", target: "DEVICE",
  condition: { name: "held", description: "Touch pad held", source: "gesture-verb", verb: "hold" },
  script: [{ offset: 0.0, kind: "play", dev: "@target", name: "hold", params: {} }],
};
const IDENTIFY = {
  kind: "scripted", name: "identify", description: "Flash any surface", target: "SURFACE",
  condition: null,
  script: [{ offset: 0.0, kind: "play", dev: "@target", name: "chime", params: {} }],
};

const ROOM = { room_type: "TEST", capability: { pixel_count: 90, color_order: "GRB", zones: [] },
               fixtures: [{ name: "main", dev: "sim-room-main", zones: [] }],
               instruments: [], controllers: {} };
const FIXTURE_DEV = { dev: "sim-room-main", name: "Room", role: null, fixture: "main" };
const BOARD = { dev: "rev1a", name: "rev1-board", role: "player" };

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
  const sent = () => sock.sent;

  // ---- empty state points at Diagnostics --------------------------------
  send({ event: "snapshot", state: "RUNNING", loaded_bit: "Rev1Bit", roles: [], registration: [],
         devices: [FIXTURE_DEV], bit_status: {}, functions: [], room: ROOM,
         instrument_functions: {},
         surface_instruments: { "sim-room-main": "dev_strip_main", "@fixture:main": "dev_strip_main" },
         builtins: { dev_strip_main: ["flash", "stop"] } });
  const mount = byId.get("functionsMount");
  assert.ok(mount.innerHTML.includes("This Bit declares no triggers"),
    "empty state names what is missing");
  assert.ok(mount.innerHTML.includes("Diagnostics above"), "and points at Diagnostics");

  // ---- Diagnostics: a disabled button says why ---------------------------
  const diagPicker = functions._diagPicker();
  diagPicker.value = "@fixture:main";
  diagPicker.onchange();
  const ping = functions._diagButton("ping");
  assert.strictEqual(ping.disabled, true);
  assert.ok(/dev_strip_main/.test(ping.title) && /audio/.test(ping.title),
    `disabled Ping names the instrument and the missing capability: ${ping.title}`);
  assert.strictEqual(functions._diagButton("flash").title, "");

  // ---- DEVICE trigger with only a fixture connected: no target, no fire --
  send({ event: "functions_changed", functions: [HOLD_FLASH, IDENTIFY],
         instrument_functions: {},
         surface_instruments: { "sim-room-main": "dev_strip_main", "@fixture:main": "dev_strip_main" },
         builtins: { dev_strip_main: ["flash", "stop"] } });
  const devPicker = byId.get("functionDev_hold_flash");
  assert.deepStrictEqual([...devPicker.options].map((o) => o.value), [""],
    "a Room fixture is not offered to a DEVICE trigger");
  assert.ok(devPicker.options[0].textContent.includes("no device joined"));
  const holdFire = functions._fireBtnFor("hold_flash");
  assert.strictEqual(holdFire.disabled, true);
  assert.ok(/Join a device/.test(holdFire.title), holdFire.title);

  // A SURFACE trigger still offers All and the fixture.
  const surfPicker = byId.get("functionDev_identify");
  assert.deepStrictEqual([...surfPicker.options].map((o) => o.value), ["@all", "@fixture:main"]);
  assert.strictEqual(functions._fireBtnFor("identify").disabled, false);

  // ---- a board joins: it becomes the DEVICE target and Fire enables -------
  send({ event: "devices_changed", devices: [FIXTURE_DEV, BOARD] });
  assert.deepStrictEqual([...devPicker.options].map((o) => o.value), ["rev1a"]);
  assert.strictEqual(devPicker.value, "rev1a");
  assert.strictEqual(holdFire.disabled, false);
  assert.strictEqual(holdFire.title, "");
  holdFire.onclick();
  assert.deepStrictEqual(sent().at(-1), { command: "fire_function", name: "hold_flash", dev: "rev1a" });

  // ---- a fire briefly marks its row ---------------------------------------
  send({ event: "function_fired", fired: { name: "hold_flash", fired_by: "admin-manual",
         declared_source: "gesture-verb", dev: "rev1a", devs: ["rev1a"], at: 1.0, steps: 2 } });
  const card = functions._cardFor("hold_flash");
  assert.ok(card.classList.contains("justfired"), "row pulses on a fire");
  await new Promise((r) => setTimeout(r, 1300));
  assert.ok(!card.classList.contains("justfired"), "and the pulse clears");

  console.log("triggers_ux: ok");
})().catch((e) => { console.error(e); process.exit(1); });
