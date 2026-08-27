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
              { name: "main.right", start: 40, count: 20 }], dev: "sim-room-main",
      url: "http://sim-room-main.local/surface",
      instrument: { name: "generic_surface",
                    capabilities: ["audio.flsyn", "light.surface"],
                    functions: [], accepted_cues: ["midi", "solid"] } },
    { name: "accent", pixel_count: 30, channel_start: 180, channel_count: 90,
      zones: [{ name: "accent.low", start: 0, count: 15 },
              { name: "accent.high", start: 15, count: 15 }], dev: null, url: null,
      instrument: { name: "generic_surface",
                    capabilities: ["audio.flsyn", "light.surface"],
                    functions: [], accepted_cues: ["midi", "solid"] } },
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
         registration: [], devices: [], bit_status: {}, functions: [], room: ROOM });
  const card = byId.get("roomCard");
  assert.ok(card.innerHTML.includes("TEST"));
  assert.ok(card.innerHTML.includes("main.center (20..39)"));
  assert.ok(card.innerHTML.includes("Not bound"));       // accent unbound
  assert.ok(card.innerHTML.includes("sim-room-main"));   // main bound
  assert.ok(card.innerHTML.includes("aurora"));
  assert.ok(card.innerHTML.includes("= 93"));            // live lane value
  assert.ok(card.innerHTML.includes("Instruments"));     // accordion, not Controls
  // fixture cards show the fixture's own Instrument as a small tag row
  // (name + capabilities + accepted cues).
  assert.ok(card.innerHTML.includes("light.surface"));
  assert.ok(card.innerHTML.includes("audio.flsyn"));
  assert.ok(card.innerHTML.includes("generic_surface"));

  // a controllers-only change must NOT rebuild fixture strips (rule 1/3):
  const stripBefore = surface._canvasFor("sim-room-main");
  const bindCtlBefore = surface._bindCtlFor("main");
  // ...and must NOT rebuild the Instruments grid's cards either -- same bug
  // class as the binding-controls chip/button above, just recurring in the
  // Instruments accordion instead.
  const instCardBefore = surface._instCardFor("light", "aurora", "primary");
  send({ event: "room_changed",
         room: { ...ROOM, controllers: { 74: 12 } } });
  assert.strictEqual(surface._canvasFor("sim-room-main"), stripBefore);
  assert.ok(card.innerHTML.includes("= 12"));
  // ...and must NOT rebuild the binding chip/Release button either (rule 1):
  // a fresh button on every controllers-only tick would silently discard
  // wire.confirmTap's armed state, breaking the two-tap Release confirm.
  assert.strictEqual(surface._bindCtlFor("main"), bindCtlBefore);
  // instrument card node identity survives too, while its live value text
  // updates in place.
  assert.strictEqual(surface._instCardFor("light", "aurora", "primary"), instCardBefore);
  assert.ok(instCardBefore.innerHTML.includes("= 12"));
  assert.ok(!instCardBefore.innerHTML.includes("= 93"));

  // rule 3: a shape change on ONE fixture must not touch a sibling fixture
  // whose shape is unchanged. main (bound, has a dev) is the unchanged
  // fixture here so its CANVAS identity (via _canvasFor) can actually be
  // checked -- an unbound fixture has no canvasesByDev entry at all, so
  // that check is only meaningful for a bound sibling. accent (unbound)
  // is the one whose shape changes; its binding-controls node identity is
  // checked too, as an extra (not a substitute) assertion.
  const mainCanvasBeforeAccentShapeChange = surface._canvasFor("sim-room-main");
  const mainBindCtlBefore = surface._bindCtlFor("main");
  send({
    event: "room_changed",
    room: {
      ...ROOM,
      fixtures: [
        ROOM.fixtures[0],
        { ...ROOM.fixtures[1], pixel_count: 40,
          zones: [{ name: "accent.low", start: 0, count: 20 },
                  { name: "accent.high", start: 20, count: 20 }] },
      ],
    },
  });
  // accent's shape changed; main's did not -> main's canvas is the SAME node.
  assert.strictEqual(surface._canvasFor("sim-room-main"), mainCanvasBeforeAccentShapeChange);
  // main's shape did not change -> its binding controls node survives too.
  assert.strictEqual(surface._bindCtlFor("main"), mainBindCtlBefore);
  assert.ok(card.innerHTML.includes("accent.high (20..39)"));

  // rule 4: rebuilding a NON-LAST fixture must reinsert it in place, not
  // append it after later surviving fixtures -- declaration order stays
  // physical DOM order.
  assert.ok(
    card.innerHTML.indexOf('class="fixname">main<')
      < card.innerHTML.indexOf('class="fixname">accent<'));

  // restore shapes back to the original baseline for the remaining assertions
  send({ event: "room_changed", room: ROOM });

  // pop-out anchor: dev+url renders exactly one .popout anchor with the
  // right attributes; dev with url:null renders none; a url arriving (or
  // leaving) rebuilds the binding controls (bindStateKey folds in url), but
  // an unrelated controllers-only tick must NOT recreate the anchor node.
  {
    const bindCtlWithUrl = surface._bindCtlFor("main");
    const popoutsWithUrl = bindCtlWithUrl.children.filter((c) => c.className === "popout");
    assert.strictEqual(popoutsWithUrl.length, 1);
    const anchor = popoutsWithUrl[0];
    assert.strictEqual(anchor.tagName, "a");
    assert.strictEqual(anchor.href, "http://sim-room-main.local/surface");
    assert.strictEqual(anchor.target, "_blank");
    assert.strictEqual(anchor.rel, "noopener");

    // dev set, url: null -> no anchor, and the rebuild replaces the node
    // (bindStateKey folds the url in, so its disappearance is a state change)
    send({ event: "room_changed",
           room: { ...ROOM, fixtures: [{ ...ROOM.fixtures[0], url: null }, ROOM.fixtures[1]] } });
    const bindCtlNoUrl = surface._bindCtlFor("main");
    assert.notStrictEqual(bindCtlNoUrl, bindCtlWithUrl);
    assert.ok(!bindCtlNoUrl.children.some((c) => c.className === "popout"));

    // url null -> value: rebuilds again and the anchor reappears
    send({ event: "room_changed", room: ROOM });
    const bindCtlUrlBack = surface._bindCtlFor("main");
    assert.notStrictEqual(bindCtlUrlBack, bindCtlNoUrl);
    const anchorBack = bindCtlUrlBack.children.find((c) => c.className === "popout");
    assert.ok(anchorBack);

    // a controllers-only room_changed must NOT recreate the anchor (rule 1)
    send({ event: "room_changed", room: { ...ROOM, controllers: { 74: 55 } } });
    const bindCtlAfterCtl = surface._bindCtlFor("main");
    assert.strictEqual(bindCtlAfterCtl, bindCtlUrlBack);
    const anchorAfterCtl = bindCtlAfterCtl.children.find((c) => c.className === "popout");
    assert.strictEqual(anchorAfterCtl, anchorBack);

    // restore baseline controllers value for the remaining assertions
    send({ event: "room_changed", room: ROOM });
  }

  // instrument fields join the fixture card's declaration signature: a
  // changed instrument (e.g. a new capability) rebuilds the card, but a
  // controllers-only tick must not.
  {
    const bindCtlBaseline = surface._bindCtlFor("main");
    send({
      event: "room_changed",
      room: {
        ...ROOM,
        fixtures: [
          { ...ROOM.fixtures[0],
            instrument: { ...ROOM.fixtures[0].instrument,
                          capabilities: ["audio.flsyn", "gesture.tap", "light.surface"] } },
          ROOM.fixtures[1],
        ],
      },
    });
    assert.notStrictEqual(surface._bindCtlFor("main"), bindCtlBaseline);
    assert.ok(card.innerHTML.includes("gesture.tap"));

    const bindCtlAfterInstChange = surface._bindCtlFor("main");
    send({ event: "room_changed",
           room: { ...ROOM, controllers: { 74: 41 },
                   fixtures: [
                     { ...ROOM.fixtures[0],
                       instrument: { ...ROOM.fixtures[0].instrument,
                                     capabilities: ["audio.flsyn", "gesture.tap", "light.surface"] } },
                     ROOM.fixtures[1],
                   ] } });
    assert.strictEqual(surface._bindCtlFor("main"), bindCtlAfterInstChange);

    // restore baseline for any subsequent assertions
    send({ event: "room_changed", room: ROOM });
  }

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
