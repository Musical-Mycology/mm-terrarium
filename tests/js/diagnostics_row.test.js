"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

// A DEVICE-target scripted function whose Bit declares no script of its own
// (script: []) -- compatibility is decided entirely by whether the selected
// surface's bound instrument declares a same-named scripted function.
const STROBE_FN = {
  kind: "scripted", name: "strobe", description: "Bit fallback description", target: "DEVICE",
  condition: { name: "admin_fire", description: "Fired by an operator",
               source: "admin-manual", verb: null },
  script: [],
};

const FUNCTIONS = [STROBE_FN];

const INSTRUMENT_FUNCTIONS = {
  dev_strip: [
    { kind: "scripted", name: "strobe", description: "Strip-specific strobe pattern",
      target: "DEVICE", condition: null, script: [] },
  ],
  tuneshroom: [],
};

const SURFACE_INSTRUMENTS = { "ie1": "tuneshroom", "sim-strip": "dev_strip" };

const BUILTINS = {
  tuneshroom: ["flash", "ping", "stop"],
  dev_strip: ["flash"],
};

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

  const baseSnapshot = {
    event: "snapshot", state: "RUNNING", loaded_bit: null,
    roles: [], registration: [],
    devices: [{ dev: "ie1", name: "Testshroom 1", role: null },
              { dev: "sim-strip", name: "Strip", role: null }],
    bit_status: {}, functions: [],
    instrument_functions: {}, surface_instruments: SURFACE_INSTRUMENTS, builtins: BUILTINS,
    room: { room_type: "DEMO", capability: { pixel_count: 864,
            color_order: "GRB", zones: [] }, fixtures: [], instruments: [],
            controllers: {} },
  };

  // ---- 1. Diagnostics row renders even with functions: [] / no Bit -------
  send(baseSnapshot);
  const mount = byId.get("functionsMount");
  assert.ok(functions._diagRow(), "diagnostics row should build even with no functions");
  assert.ok(mount.innerHTML.includes("Diagnostics"), "diagnostics row rendered into the mount");
  assert.ok(mount.innerHTML.includes("No functions declared"));

  const diagPicker = functions._diagPicker();
  assert.ok(diagPicker, "diagnostics picker should exist");
  assert.strictEqual(diagPicker.options[0].value, "@all");
  assert.deepStrictEqual(
    [...diagPicker.options].map((o) => o.value), ["@all", "ie1", "sim-strip"]);

  // Buttons render Stop first -- it is the panic button, and must be
  // reachable without hunting past Flash/Ping.
  const diagBar = functions._diagRow().children[1];
  const buttonTexts = diagBar.children
    .filter((c) => c.tagName === "button")
    .map((c) => c.textContent);
  assert.deepStrictEqual(buttonTexts, ["Stop", "Flash", "Ping"]);

  // ---- 2. Button disabled/enabled per selected surface's builtins -------
  // "ie1" -> tuneshroom, which has all three builtins.
  diagPicker.value = "ie1";
  diagPicker.onchange();
  assert.strictEqual(functions._diagButton("flash").disabled, false);
  assert.strictEqual(functions._diagButton("stop").disabled, false);
  assert.strictEqual(functions._diagButton("ping").disabled, false);

  // "sim-strip" -> dev_strip, which only declares "flash".
  diagPicker.value = "sim-strip";
  diagPicker.onchange();
  assert.strictEqual(functions._diagButton("flash").disabled, false);
  assert.strictEqual(functions._diagButton("stop").disabled, true);
  assert.strictEqual(functions._diagButton("ping").disabled, true);

  // "@all" (All) unions builtins across every surface_instruments entry --
  // ie1/tuneshroom and sim-strip/dev_strip between them declare all three
  // builtins, so all three buttons must be enabled.
  assert.strictEqual(diagPicker.options[0].value, "@all");
  diagPicker.value = "@all";
  diagPicker.onchange();
  assert.strictEqual(functions._diagButton("flash").disabled, false);
  assert.strictEqual(functions._diagButton("stop").disabled, false);
  assert.strictEqual(functions._diagButton("ping").disabled, false);

  // ---- 3. Click sends the right command ----------------------------------
  // All stays selected -- the fire command must keep sending the picker's
  // own wire value ("@all", the ALL sentinel).
  const pingBtn = functions._diagButton("ping");
  pingBtn.onclick();
  const allSent = sock.sent.at(-1);
  assert.deepStrictEqual(allSent, { command: "fire_function", name: "ping", dev: "@all" });

  diagPicker.value = "sim-strip";
  diagPicker.onchange();
  const flashBtn = functions._diagButton("flash");
  flashBtn.onclick();
  const lastSent = sock.sent.at(-1);
  assert.deepStrictEqual(lastSent, { command: "fire_function", name: "flash", dev: "sim-strip" });

  // ---- 4. Option-disabling + description swap on a scripted card's picker
  const withFunctions = Object.assign({}, baseSnapshot,
    { functions: FUNCTIONS, instrument_functions: INSTRUMENT_FUNCTIONS });
  send(withFunctions);

  const card = functions._cardFor("strobe");
  assert.ok(card, "strobe card should render");
  const picker = card.children.find((c) => c.className === "firerow").children[0];
  assert.deepStrictEqual([...picker.options].map((o) => o.value), ["ie1", "sim-strip"]);
  // tuneshroom has no "strobe" scripted function and the Bit's own script is
  // empty and "strobe" isn't a builtin name -- ie1 must be disabled.
  const ie1Option = [...picker.options].find((o) => o.value === "ie1");
  assert.strictEqual(ie1Option.disabled, true, "ie1 (tuneshroom) lacks strobe -- disabled");
  const stripOption = [...picker.options].find((o) => o.value === "sim-strip");
  assert.strictEqual(stripOption.disabled, false, "sim-strip (dev_strip) declares strobe");

  // description starts resolved against the picker's default selection
  // (first offered device, "ie1" / tuneshroom -- no matching instrument
  // function, so it falls back to the Bit's own description).
  assert.strictEqual(picker.value, "ie1");
  assert.strictEqual(card._descEl.textContent, "Bit fallback description");

  // switching the picker to sim-strip / dev_strip resolves to the
  // instrument's own function description.
  picker.value = "sim-strip";
  picker.onchange();
  assert.strictEqual(card._descEl.textContent, "Strip-specific strobe pattern");

  // switching back falls back to the Bit's own description again.
  picker.value = "ie1";
  picker.onchange();
  assert.strictEqual(card._descEl.textContent, "Bit fallback description");

  // ---- 5. No-rebuild-on-fire discipline preserved -------------------------
  const cardBefore = functions._cardFor("strobe");
  send({ event: "function_fired", fired: { name: "strobe", fired_by: "admin-manual",
         declared_source: "admin-manual", dev: "sim-strip", devs: ["sim-strip"],
         at: 1.0, steps: 0 } });
  assert.strictEqual(functions._cardFor("strobe"), cardBefore, "card identity preserved on fire");
  assert.ok(byId.get("functionsMount").innerHTML.includes("Admin manual"));

  console.log("diagnostics_row: ok");
})().catch((e) => { console.error(e); process.exit(1); });
