"use strict";
// Regression for the 2026-09-14 NO_ROOM crash: functions.js's snapshot
// handler did `document.getElementById("functionsMount").textContent = ""`
// unconditionally, but surface.js only creates #functionsMount once a Room
// actually renders (its `!currentRoom` branch never builds it). The very
// first `snapshot` a NO_ROOM client receives hit that null and threw inside
// wire.js's dispatch (see docs/MM_TERRARIUM.md's entry on this bug).
//
// This test's DOM stub auto-vivifies a placeholder <div> for any
// not-yet-seen id instead of returning null (see _dom_stub.js's own
// "Residual gap" comment), so it cannot reproduce the literal null-deref.
// What it CAN and does reproduce is the closely related consequence of the
// same root cause: functions.js cached a content signature ("[]", no
// functions declared) against that placeholder before a real Room ever
// mounted the genuine #functionsMount node, then treated an unchanged
// functions list as "already rendered" and never painted the real node at
// all once the Room did mount it.
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");
  const functions = await import("../../console/static/functions.js");
  surface.init(); functions.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // NO_ROOM: the connect-time snapshot every client gets, before any Room
  // or Bit is loaded. Must not throw (the historical bug did, in a real
  // browser, exactly here).
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [],
         registration: [], devices: [], bit_status: {}, functions: [],
         room: null });

  // A Room now loads (still no Bit, so the declared functions list stays
  // empty) -- surface.js mints the real #functionsMount for the first
  // time, a different node than whatever functions.js touched above.
  send({ event: "room_changed",
         room: { room_type: "DEMO", capability: { pixel_count: 12,
                 color_order: "GRB", zones: [] }, fixtures: [],
                 instruments: [], controllers: {} } });

  // The server re-broadcasts functions_changed on the same tick (surface/
  // builtin instrument data changed with the Room bind) even though the
  // declared functions list itself is still "[]" -- the exact repeat-
  // signature case the old code short-circuited on.
  send({ event: "functions_changed", functions: [],
         instrument_functions: {}, surface_instruments: {}, builtins: {} });

  const mount = byId.get("functionsMount");
  assert.ok(mount.innerHTML.includes("No functions declared"),
    "functions panel must render into the Room's real mount once it " +
    "exists, even if the declared functions list never changed");

  console.log("functions_mount_lifecycle: ok");
})().catch((e) => { console.error(e); process.exit(1); });
