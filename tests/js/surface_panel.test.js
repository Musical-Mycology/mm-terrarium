"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const ROOM = {
  room_type: "TEST",
  capability: { pixel_count: 90, color_order: "GRB",
    zones: [{ name: "main.left", start: 0, count: 20 }] },
  fixtures: [
    { name: "main", pixel_count: 60, channel_start: 0, channel_count: 180,
      zones: [{ name: "main.left", start: 0, count: 20 },
              { name: "main.center", start: 20, count: 20 },
              { name: "main.right", start: 40, count: 20 }], dev: "sim-room-main" },
    { name: "accent", pixel_count: 30, channel_start: 180, channel_count: 90,
      zones: [{ name: "accent.low", start: 0, count: 15 },
              { name: "accent.high", start: 15, count: 15 }], dev: null },
  ],
  instruments: [
    { kind: "light", instrument: "aurora", target: "primary",
      params: { hue: 0.33 }, lanes: [{ source: "cc:74", dest: "hue" }] },
    { kind: "audio", instrument: "flsyn", program: 115,
      lanes: [{ source: "cc:74", dest: "cc:74" }] },
  ],
  controllers: { 74: 93 },
};

(async () => {
  const wire = await import("../../console/static/wire.js");
  const surface = await import("../../console/static/surface.js");

  // pure row-split rule
  assert.deepStrictEqual(
    surface._blockRowsFor({ pixel_count: 864 }).map((r) => r.count),
    [144, 144, 144, 144, 144, 144]);
  assert.deepStrictEqual(
    surface._blockRowsFor({ pixel_count: 60 }).map((r) => r.count), [60]);

  surface.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", state: "RUNNING", loaded_bit: "TestBit", roles: [],
         registration: [], devices: [], bit_status: {}, triggers: [], room: ROOM });
  const card = byId.get("roomCard");
  assert.ok(card.innerHTML.includes("TEST"));
  assert.ok(card.innerHTML.includes("main.center (20..39)"));
  assert.ok(card.innerHTML.includes("Not bound"));       // accent unbound
  assert.ok(card.innerHTML.includes("sim-room-main"));   // main bound
  assert.ok(card.innerHTML.includes("aurora"));
  assert.ok(card.innerHTML.includes("= 93"));            // live lane value
  assert.ok(card.innerHTML.includes("Instruments"));     // accordion, not Controls

  // a controllers-only change must NOT rebuild fixture strips (rule 1/3):
  const stripBefore = surface._canvasFor("sim-room-main");
  const bindCtlBefore = surface._bindCtlFor("main");
  send({ event: "room_changed",
         room: { ...ROOM, controllers: { 74: 12 } } });
  assert.strictEqual(surface._canvasFor("sim-room-main"), stripBefore);
  assert.ok(card.innerHTML.includes("= 12"));
  // ...and must NOT rebuild the binding chip/Release button either (rule 1):
  // a fresh button on every controllers-only tick would silently discard
  // wire.confirmTap's armed state, breaking the two-tap Release confirm.
  assert.strictEqual(surface._bindCtlFor("main"), bindCtlBefore);

  // rule 3: a shape change on ONE fixture must not touch a sibling fixture
  // whose shape is unchanged -- its binding-controls node identity (the
  // only stable per-fixture handle available for an unbound fixture) must
  // survive, and main's canvas (whose shape DID change) must not.
  const mainStripBeforeShapeChange = surface._canvasFor("sim-room-main");
  const accentBindCtlBefore = surface._bindCtlFor("accent");
  send({
    event: "room_changed",
    room: {
      ...ROOM,
      fixtures: [
        { ...ROOM.fixtures[0], pixel_count: 75,
          zones: [{ name: "main.left", start: 0, count: 25 },
                  { name: "main.center", start: 25, count: 25 },
                  { name: "main.right", start: 50, count: 25 }] },
        ROOM.fixtures[1],
      ],
    },
  });
  // main's shape changed -> its canvas is a new node.
  assert.notStrictEqual(surface._canvasFor("sim-room-main"), mainStripBeforeShapeChange);
  assert.ok(card.innerHTML.includes("main.center (25..49)"));
  // accent's shape did not change -> its binding controls node survives.
  assert.strictEqual(surface._bindCtlFor("accent"), accentBindCtlBefore);

  // rule 4: rebuilding a NON-LAST fixture must reinsert it in place, not
  // append it after later surviving fixtures -- declaration order stays
  // physical DOM order.
  assert.ok(
    card.innerHTML.indexOf('class="fixname">main<')
      < card.innerHTML.indexOf('class="fixname">accent<'));

  // restore shapes back to the original baseline for the remaining assertions
  send({ event: "room_changed", room: ROOM });

  // frames: GRB decode; unknown dev is a no-op (rule 9)
  send({ event: "room_frame", dev: "sim-room-main",
         channels: [255, 0, 0].concat(Array(177).fill(0)) });  // G=255 first px
  assert.deepStrictEqual(surface._lastPaint("sim-room-main")[0], [0, 255, 0]); // [r,g,b]
  send({ event: "room_frame", dev: "ghost", channels: [1, 2, 3] }); // no throw

  // no Room configured
  send({ event: "room_changed", room: null });
  assert.ok(card.innerHTML.includes("No Room configured"));

  console.log("surface_panel: ok");
})().catch((e) => { console.error(e); process.exit(1); });
