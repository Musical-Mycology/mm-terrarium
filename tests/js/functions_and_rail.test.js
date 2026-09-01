"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const FUNCTIONS = [
  { kind: "scripted", name: "fireworks_player", description: "Celebratory flashes", target: "DEVICE",
    condition: { name: "phrase_success", description: "Player matches the call phrase",
                 source: "bit-adjudicated", verb: null },
    script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 },
             { offset: 1.4, kind: "light", dev: "@target", status: 176, data1: 70, data2: 0 }] },
  { kind: "scripted", name: "finale", description: "Closing sweep", target: "ROOM",
    condition: { name: "run_complete", description: "All cycles finished",
                 source: "bit-adjudicated", verb: null },
    script: [{ offset: 0.0, kind: "play", dev: "@target", name: "sweep", params: {} }] },
  { kind: "scripted", name: "flash_device", description: "Operator-chosen flash", target: "SURFACE",
    condition: { name: "admin_fire", description: "Fired by an operator",
                 source: "admin-manual", verb: null },
    script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 }] },
  { kind: "generator", name: "drift", description: "ambient breathing glow",
    lane: { dev: "@room", status: 176, data1: 74 },
    waveform: "triangle", period: 12.0, lo: 0, hi: 127 },
  { kind: "stream", name: "tilt_hue", description: "tilt drives the hue lane",
    verb: "tilt", arg: 0, in_lo: -90.0, in_hi: 90.0,
    outputs: [{ dev: "@target", status: 176, data1: 74, out_lo: 0.0, out_hi: 127.0, mode: "linear" }] },
];

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

  send({ event: "snapshot", state: "RUNNING", loaded_bit: "MetronomeBit",
         roles: [], registration: [{ role: "player", count: 2, capacity: 2 }],
         devices: [{ dev: "ie1", name: "Testshroom 1", role: "player" },
                   { dev: "sim-room", name: "Room", role: null }],
         bit_status: {}, functions: FUNCTIONS,
         room: { room_type: "DEMO", capability: { pixel_count: 864,
                 color_order: "GRB", zones: [] }, fixtures: [], instruments: [],
                 controllers: {} } });

  const mount = byId.get("functionsMount");
  assert.ok(mount.innerHTML.includes("fireworks_player"));
  assert.ok(mount.innerHTML.includes("2 steps · 1.4s"));   // count + span
  assert.ok(mount.innerHTML.includes("never fired"));
  // device picker only on DEVICE targets, offering live devices
  assert.ok(mount.innerHTML.includes("ie1"));

  // redesign markup: grid + card classes reconciled to terrarium.css
  const grid = mount.children.find((c) => c.className === "fngrid");
  assert.ok(grid, "expected a .fngrid container");
  const fireworksCard = functions._cardFor("fireworks_player");
  assert.ok(fireworksCard.className.includes("fn"), "card should carry the fn class");
  assert.ok(fireworksCard.children.some((c) => c.className === "desc"));
  assert.ok(fireworksCard.children.some((c) => c.className === "cond"));
  assert.ok(fireworksCard.children.some((c) => c.className === "fired-line"));
  // collapsed script block toggles via the expander, not a bare <details>
  const scriptEl = fireworksCard.children.find((c) => c.className.split(" ")[0] === "script");
  assert.ok(scriptEl && !scriptEl.classList.contains("open"), "script starts collapsed");
  const scriptbar = fireworksCard.children.find((c) => c.className === "scriptbar");
  const expander = scriptbar.children.find((c) => c.className === "expander");
  expander.onclick();
  assert.ok(scriptEl.classList.contains("open"), "expander opens the script block");

  // SURFACE card: picker offers "All" first, then live devices
  const pickerFor = (name) =>
    functions._cardFor(name).children.find((c) => c.className === "firerow").children[0];
  const surfacePicker = pickerFor("flash_device");
  assert.strictEqual(surfacePicker.options[0].value, "@all");
  assert.strictEqual(surfacePicker.options[0].textContent, "All");
  assert.deepStrictEqual(
    [...surfacePicker.options].map((o) => o.value), ["@all", "ie1", "sim-room"]);

  // a repeat functions_changed with identical content must not rebuild (rule 1)
  const before = functions._cardFor("fireworks_player");
  send({ event: "functions_changed", functions: FUNCTIONS });
  assert.strictEqual(functions._cardFor("fireworks_player"), before);

  // devices_changed refreshes SURFACE pickers in place, without rebuilding cards
  const surfaceCardBefore = functions._cardFor("flash_device");
  send({ event: "devices_changed",
         devices: [{ dev: "ie1", name: "Testshroom 1", role: "player", muted: true }] });
  assert.strictEqual(functions._cardFor("flash_device"), surfaceCardBefore);
  const refreshedPicker = pickerFor("flash_device");
  assert.strictEqual(refreshedPicker.options[0].value, "@all");
  assert.strictEqual(refreshedPicker.options[1].textContent, "ie1 (muted)");

  // function_fired updates the one line, tags admin-manual
  send({ event: "function_fired", fired: { name: "finale", fired_by: "admin-manual",
         declared_source: "bit-adjudicated", dev: null, devs: ["sim-room"],
         at: 12.5, steps: 1 } });
  assert.ok(mount.innerHTML.includes("Admin manual"));

  // kind-tagged cards: a snapshot with all three kinds renders three cards;
  // only the scripted one has a Fire button; generator/stream render their
  // declaration lines with none.
  assert.strictEqual(grid.children.length, 5, "one card per declared function");
  const driftCard = functions._cardFor("drift");
  assert.ok(driftCard, "generator card should render");
  assert.ok(driftCard.innerHTML.includes("triangle"));
  assert.ok(driftCard.innerHTML.includes("12"));
  assert.ok(!driftCard.children.some((c) => c.tagName === "button" && c.textContent === "Fire"),
    "generator card must not offer a Fire button");
  const tiltCard = functions._cardFor("tilt_hue");
  assert.ok(tiltCard, "stream card should render");
  assert.ok(tiltCard.innerHTML.includes("tilt"));
  assert.ok(tiltCard.innerHTML.includes("linear"));
  assert.ok(!tiltCard.children.some((c) => c.tagName === "button" && c.textContent === "Fire"),
    "stream card must not offer a Fire button");
  const scriptedFireBtn = fireworksCard.children
    .find((c) => c.className === "firerow").children
    .find((c) => c.tagName === "button");
  assert.ok(scriptedFireBtn && scriptedFireBtn.textContent === "Fire",
    "scripted card keeps its Fire button");

  // a function_fired patch on the scripted card leaves the generator and
  // stream cards' children intact -- the single-card patch discipline is
  // untouched by the new kinds.
  const driftChildrenBefore = driftCard.children.length;
  const tiltChildrenBefore = tiltCard.children.length;
  send({ event: "function_fired", fired: { name: "fireworks_player", fired_by: "gesture-verb",
         declared_source: "gesture-verb", dev: "ie1", devs: ["ie1"], at: 20.0, steps: 2 } });
  assert.strictEqual(functions._cardFor("drift"), driftCard);
  assert.strictEqual(functions._cardFor("tilt_hue"), tiltCard);
  assert.strictEqual(driftCard.children.length, driftChildrenBefore);
  assert.strictEqual(tiltCard.children.length, tiltChildrenBefore);

  // rail: rollup categories render even with no role declarations
  assert.ok(byId.get("registrationCard").innerHTML.includes("Fixtures"));
  send({ event: "log", level: "error", message: "boom" });
  assert.ok(byId.get("logCard").innerHTML.includes("boom"));
  send({ event: "bit_completed", result: { phrases: 4 }, bit_name: "MetronomeBit" });
  assert.ok(byId.get("logCard").innerHTML.includes("phrases"));

  // Roles refcard data (shape modeled on test_bit.py's `player` role via
  // console/protocol.py's role_view()) now feeds bit.js's Bit Details popup;
  // that rendering is covered by bit_panel.test.js. This block only keeps
  // the PLAYER_ROLE fixture as snapshot input so the rest of the rail
  // (registration/devices/log) still exercises a populated snapshot.
  const PLAYER_ROLE = {
    role: "player", class: "PLAYER", capacity: 2, scored: true,
    light_manifest: {
      instruments: [
        { instrument: "aurora", target: "primary",
          params: { hue: 0.33, level: 0.55 },
          lanes: [{ source: "cc:74", dest: "hue" },
                  { source: "cc:11", dest: "level" }] },
      ],
    },
    ugen_manifest: {
      instruments: [
        { instrument: "flsyn", program: 89,
          drone: { key: 45, velocity: 90 },
          lanes: [{ source: "cc:74", dest: "cc:74" },
                  { source: "cc:11", dest: "cc:11" }] },
      ],
    },
    welcome: {
      light: { instrument: "glow", params: { hue: 0.33 }, duration: 1.5 },
      audio: { instrument: "chime", duration: 1.5 },
    },
    requires: { slot: "fixture", capabilities: ["light.pixels", "light.surface"] },
  };
  const JAMMER_ROLE = { role: "jammer", class: "jam", capacity: null, scored: false };
  send({ event: "snapshot", state: "RUNNING", loaded_bit: "MetronomeBit",
         roles: [PLAYER_ROLE, JAMMER_ROLE],
         registration: [{ role: "player", count: 2, capacity: 2 }],
         devices: [{ dev: "ie1", name: "Testshroom 1", role: "player" },
                   { dev: "ie2", name: "Testshroom 2", role: "jammer" },
                   { dev: "sim-room", name: "Room sim", role: null },
                   { dev: "ie9", name: "Wanderer", role: null }],
         bit_status: {}, functions: FUNCTIONS,
         room: { room_type: "TEST", capability: { pixel_count: 864,
                 color_order: "GRB", zones: [] },
                 fixtures: [{ name: "main", dev: "sim-room", zones: [] }], instruments: [],
                 controllers: {} } });

  {
    // Sidebar registration is now a three-line category rollup only; the
    // per-device Instruments pull moved to the Room view (rooms.js).
    const reg = byId.get("registrationCard").innerHTML;
    assert.ok(/Fixtures[\s\S]*?1\/1/.test(reg), "fixtures rollup bound/total");
    assert.ok(/Scored[\s\S]*?2\/2/.test(reg), "scored rollup count/capacity");
    assert.ok(/Jam[\s\S]*?0\/∞/.test(reg), "jam rollup count/unbounded");
    assert.ok(!reg.includes("Instruments"), "no Instruments pull in sidebar");
    assert.ok(!reg.includes("Testshroom 1"), "no per-device rows in sidebar");
  }

  console.log("functions_and_rail: ok");
})().catch((e) => { console.error(e); process.exit(1); });
