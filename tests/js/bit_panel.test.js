"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

(async () => {
  const wire = await import("../../console/static/wire.js");
  const bit = await import("../../console/static/bit.js");
  bit.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();

  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // empty state
  send({ event: "snapshot", state: "IDLE", loaded_bit: null, roles: [],
         registration: [], devices: [], bit_status: {}, room: null, functions: [] });
  assert.ok(byId.get("bitPanel").innerHTML.includes("No Bit loaded"));

  // bits_listed builds the picker data (hidden until Load is clicked)
  send({ event: "bits_listed", errors: [{ path: "bits/broken/bit.toml", message: "bad" }],
         bits: [{ name: "MetronomeBit", display_name: "Metronome", version: "1.0.0",
                  kind: "r_game", hidden: false, description: "Call-and-response",
                  room_types: ["DEMO"], notes: "",
                  start: { when: "players", min_scored: 2, timeout_seconds: 120, on_timeout: "start" },
                  roles: { scored: 2, shared_open: false, jam_open: false } }] });

  // loading a bit paints the identity card and phase chip
  send({ event: "state_changed", state: "SETUP", loaded_bit: "MetronomeBit" });
  const panel = byId.get("bitPanel");
  assert.ok(panel.innerHTML.includes("Metronome"));
  assert.ok(panel.innerHTML.includes("Waiting Room"));

  // phase chip follows further transitions; buttons never disabled
  send({ event: "state_changed", state: "RUNNING", loaded_bit: "MetronomeBit" });
  assert.ok(panel.innerHTML.includes("Running"));

  // bit_status paints the status card and empty status hides it
  send({ event: "bit_status", status: { turn: "ie2", cycle: 2, tap_errors_ms: [1.5, -2] } });
  const card = byId.get("bitStatusCard");
  assert.strictEqual(card.hidden, false);
  assert.ok(card.innerHTML.includes("ie2"));
  assert.ok(card.innerHTML.includes("1.5"));          // list formatted, not [object
  send({ event: "bit_status", status: {} });
  assert.strictEqual(card.hidden, true);

  console.log("bit_panel: ok");
})().catch((e) => { console.error(e); process.exit(1); });
