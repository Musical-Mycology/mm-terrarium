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
  { name: "flash_device", description: "Operator-chosen flash", target: "SURFACE",
    condition: { name: "admin_fire", description: "Fired by an operator",
                 source: "admin-manual", verb: null },
    script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 }] },
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

  // redesign markup: grid + card classes reconciled to terrarium.css
  const grid = mount.children.find((c) => c.className === "triggrid");
  assert.ok(grid, "expected a .triggrid container");
  const fireworksCard = triggers._cardFor("fireworks_player");
  assert.ok(fireworksCard.className.includes("trig"), "card should carry the trig class");
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

  // SURFACE card: picker offers "Room" first, then live devices
  const pickerFor = (name) =>
    triggers._cardFor(name).children.find((c) => c.className === "firerow").children[0];
  const surfacePicker = pickerFor("flash_device");
  assert.strictEqual(surfacePicker.options[0].value, "@room");
  assert.strictEqual(surfacePicker.options[0].textContent, "Room");
  assert.deepStrictEqual(
    [...surfacePicker.options].map((o) => o.value), ["@room", "ie1", "sim-room"]);

  // a repeat triggers_changed with identical content must not rebuild (rule 1)
  const before = triggers._cardFor("fireworks_player");
  send({ event: "triggers_changed", triggers: TRIGGERS });
  assert.strictEqual(triggers._cardFor("fireworks_player"), before);

  // devices_changed refreshes SURFACE pickers in place, without rebuilding cards
  const surfaceCardBefore = triggers._cardFor("flash_device");
  send({ event: "devices_changed",
         devices: [{ dev: "ie1", name: "Tuneshroom 1", role: "player", muted: true }] });
  assert.strictEqual(triggers._cardFor("flash_device"), surfaceCardBefore);
  const refreshedPicker = pickerFor("flash_device");
  assert.strictEqual(refreshedPicker.options[0].value, "@room");
  assert.strictEqual(refreshedPicker.options[1].textContent, "ie1 (muted)");

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

  // rail: Roles & manifests renderer -- light + audio instrument kinds and
  // the welcome-line formatting transform. Shape modeled on test_bit.py's
  // `player` role (real light_manifest cc:74->hue lane, ugen_manifest, and
  // a welcome dict) via console/protocol.py's role_view().
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
  send({ event: "snapshot", state: "RUNNING", loaded_bit: "MetronomeBit",
         roles: [PLAYER_ROLE], registration: [{ role: "player", count: 2, capacity: 2 }],
         devices: [{ dev: "ie1", name: "Tuneshroom 1", role: "player" }],
         bit_status: {}, triggers: TRIGGERS,
         room: { room_type: "DEMO", capability: { pixel_count: 864,
                 color_order: "GRB", zones: [] }, fixtures: [], instruments: [],
                 controllers: {} } });

  const rolesHtml = byId.get("rolesCard").innerHTML;
  assert.ok(rolesHtml.includes("player"), "role name should render");
  assert.ok(rolesHtml.includes("PLAYER"), "role class should render");
  // light instrument path: kind-tagged "Light" and delegated to buildInstrumentCard
  assert.ok(rolesHtml.includes("Light"), "light instrument kind should render");
  assert.ok(rolesHtml.includes("aurora"), "light instrument name should render");
  assert.ok(rolesHtml.includes("primary"), "light instrument target should render");
  // audio instrument path: kind-tagged "Audio" and delegated to buildInstrumentCard
  assert.ok(rolesHtml.includes("Audio"), "audio instrument kind should render");
  assert.ok(rolesHtml.includes("flsyn"), "audio instrument name should render");
  // welcome-line formatting: `${k}: ${v.instrument}` joined with " · "
  assert.ok(rolesHtml.includes("light: glow"), "welcome light entry should render");
  assert.ok(rolesHtml.includes("audio: chime"), "welcome audio entry should render");
  assert.ok(rolesHtml.includes("light: glow · audio: chime") ||
            rolesHtml.includes("audio: chime · light: glow"),
            "welcome entries should be joined with ·");
  // requires: slot + capabilities should render on the role card so an
  // operator can see why a join was refused.
  assert.ok(rolesHtml.includes("requires"), "requires label should render");
  assert.ok(rolesHtml.includes("fixture"), "requires slot should render");
  assert.ok(rolesHtml.includes("light.pixels"), "requires capabilities should render");

  // a role with no `requires` slot renders no requires line at all.
  const NO_REQUIRES_ROLE = { ...PLAYER_ROLE, role: "watcher", requires: null };
  send({ event: "snapshot", state: "RUNNING", loaded_bit: "MetronomeBit",
         roles: [NO_REQUIRES_ROLE],
         registration: [{ role: "watcher", count: 0, capacity: null }],
         devices: [], bit_status: {}, triggers: TRIGGERS,
         room: { room_type: "DEMO", capability: { pixel_count: 864,
                 color_order: "GRB", zones: [] }, fixtures: [], instruments: [],
                 controllers: {} } });
  const noReqHtml = byId.get("rolesCard").innerHTML;
  assert.ok(!noReqHtml.includes("requires —"), "no requires line without a requires slot");

  console.log("triggers_and_rail: ok");
})().catch((e) => { console.error(e); process.exit(1); });
