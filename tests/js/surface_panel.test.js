"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const ROOM = {
  room_type: "TEST",
  capability: { pixel_count: 90, color_order: "GRB",
    zones: [{ name: "main.left", start: 0, count: 20 }] },
  fixtures: [
    { name: "main", pixel_count: 60, channel_start: 0, channel_count: 180,
      color_order: "GRB",
      zones: [{ name: "main.left", start: 0, count: 20 },
              { name: "main.center", start: 20, count: 20 },
              { name: "main.right", start: 40, count: 20 }], dev: "sim-room-main",
      url: "http://sim-room-main.local/surface",
      instrument: { name: "generic_surface",
                    capabilities: ["audio.flsyn", "light.surface"],
                    functions: [{ name: "glow", kind: "generator",
                                  lane: "cc:74", period: 12.0 }],
                    accepted_cues: ["midi", "solid"],
                    event_triggers: [{ name: "tap", thresholds: { z_delta: 2.5 } }] } },
    { name: "accent", pixel_count: 30, channel_start: 180, channel_count: 90,
      color_order: "GRB",
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

  // pure lane merge: one row per source across every voice, cc ascending,
  // non-cc sources last
  assert.deepStrictEqual(
    surface._laneRowsFor([
      { kind: "light", instrument: "aurora", lanes: [{ source: "cc:74", dest: "hue" }, { source: "cc:11", dest: "level" }] },
      { kind: "light", instrument: "bloom", lanes: [{ source: "note", dest: "trigger" }] },
      { kind: "audio", instrument: "flsyn", lanes: [{ source: "cc:74", dest: "cc:74" }] },
    ]).map((r) => [r.source, r.readers.map((x) => `${x.instrument} ${x.dest}`).join(" · ")]),
    [["cc:11", "aurora level"], ["cc:74", "aurora hue · flsyn cc:74"], ["note", "bloom trigger"]]);

  // pure pixel decode: width from color_order, W drawn additively
  assert.deepStrictEqual(surface._decodePixels([255, 0, 0], "GRB"), [[0, 255, 0]]);
  assert.deepStrictEqual(surface._decodePixels([10, 20, 30, 5], "RGBW"), [[15, 25, 35]]);
  assert.deepStrictEqual(surface._decodePixels([10, 0, 0, 250], "RGBW"), [[255, 250, 250]]);
  assert.deepStrictEqual(surface._decodePixels([1, 2, 3], undefined), [[2, 1, 3]]); // GRB default

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
  assert.ok(card.innerHTML.includes("93"), "live lane value shown");
  assert.ok(card.innerHTML.includes("1 light · 1 audio voices"), "summary meta counts voices");
  // accordion order: fixtures, Triggers, then Live values
  const bodyHtml = card.innerHTML;
  assert.ok(bodyHtml.indexOf("Triggers") < bodyHtml.indexOf("Live values"), "Triggers sits above Live values");
  // the accordion shows real-time controller values, so it is labeled
  // "Live values" -- the official instrument declarations live on the
  // Registration rollup / Room view instead.
  assert.ok(card.innerHTML.includes("Live values"));
  assert.ok(!card.innerHTML.includes("Instruments"));
  assert.ok(card.innerHTML.includes("Triggers"));        // renamed from Functions
  assert.ok(!card.innerHTML.includes("Functions"));
  // the fixture's Instrument declaration chips no longer render on the
  // Live card (they moved to the Room view, rooms.js); the head shows only
  // name, binding, pop-out, Release/Arm.
  assert.ok(!card.innerHTML.includes("insttags"));
  assert.ok(!card.innerHTML.includes("glow (generator)"));
  // the helper is still exported for rooms.js and renders the compact
  // strings, never "[object Object]"
  const tags = surface.instrumentTags(ROOM.fixtures[0].instrument);
  assert.ok(tags.innerHTML.includes("generic_surface"));
  assert.ok(tags.innerHTML.includes("audio.flsyn"));
  assert.ok(tags.innerHTML.includes("glow (generator)"));
  assert.ok(tags.innerHTML.includes("z_delta:2.5"));
  assert.ok(!tags.innerHTML.includes("[object Object]"));

  // a controllers-only change must NOT rebuild fixture strips (rule 1/3):
  const stripBefore = surface._canvasFor("main");
  const laneTableBefore = surface._laneTable();
  const bindCtlBefore = surface._bindCtlFor("main");
  // ...and must NOT rebuild the lane table's rows either -- same bug
  // class as the binding-controls chip/button above, just recurring in the
  // Live values accordion instead.
  const laneRowBefore = surface._laneRowFor("cc:74");
  send({ event: "room_changed",
         room: { ...ROOM, controllers: { 74: 12 } } });
  assert.strictEqual(surface._canvasFor("main"), stripBefore);
  assert.ok(card.innerHTML.includes("12"));
  // ...and must NOT rebuild the binding chip/Release button either (rule 1):
  // a fresh button on every controllers-only tick would silently discard
  // wire.confirmTap's armed state, breaking the two-tap Release confirm.
  assert.strictEqual(surface._bindCtlFor("main"), bindCtlBefore);
  // lane row node identity survives too, while its live value text updates
  // in place.
  assert.strictEqual(surface._laneRowFor("cc:74"), laneRowBefore);
  assert.ok(laneRowBefore.innerHTML.includes("12"));
  assert.ok(!laneRowBefore.innerHTML.includes("93"));
  assert.strictEqual(surface._laneTable(), laneTableBefore, "lane table node survives a controllers-only tick");

  // rule 3: a shape change on ONE fixture must not touch a sibling fixture
  // whose shape is unchanged. main is the unchanged fixture here, so its
  // CANVAS identity (via _canvasFor, keyed by fixture NAME) is what is
  // checked. accent is the one whose shape changes; its binding-controls
  // node identity is checked too, as an extra (not a substitute)
  // assertion.
  const mainCanvasBeforeAccentShapeChange = surface._canvasFor("main");
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
  assert.strictEqual(surface._canvasFor("main"), mainCanvasBeforeAccentShapeChange);
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
    assert.ok(!card.innerHTML.includes("gesture.tap"), "chips are not drawn on the Live card");

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

  // a changed instrument list rebuilds the lane table
  {
    const tableBefore = surface._laneTable();
    send({ event: "room_changed", room: { ...ROOM,
      instruments: [...ROOM.instruments,
        { kind: "light", instrument: "rainbow", target: "primary", params: {}, lanes: [{ source: "cc:21", dest: "level" }] }] } });
    assert.notStrictEqual(surface._laneTable(), tableBefore);
    assert.ok(surface._laneRowFor("cc:21"));
    assert.ok(card.innerHTML.includes("2 light · 1 audio voices"));
    send({ event: "room_changed", room: ROOM });
  }

  // empty instruments: the "No voices declared" message patches in place
  // across repeated ticks instead of being rebuilt every time -- the same
  // patch-in-place discipline the lane table itself already gets.
  {
    send({ event: "room_changed", room: { ...ROOM, instruments: [] } });
    assert.ok(card.innerHTML.includes("No voices declared"));
    assert.strictEqual(surface._laneTable(), null);
    const emptyMsgBefore = surface._laneEmptyMsg();
    assert.ok(emptyMsgBefore, "empty-state message rendered");
    // a second identical (still-empty) tick must not rebuild the message
    send({ event: "room_changed", room: { ...ROOM, instruments: [] } });
    assert.strictEqual(surface._laneEmptyMsg(), emptyMsgBefore,
      "empty-state message survives an unchanged tick instead of being rebuilt");
    send({ event: "room_changed", room: ROOM });
    assert.ok(surface._laneTable(), "lane table returns once instruments are non-empty again");
  }

  // frames: GRB decode, keyed by fixture NAME; an unknown fixture is a
  // no-op (rule 9)
  send({ event: "room_frame", fixture: "main",
         channels: [255, 0, 0].concat(Array(177).fill(0)) });  // G=255 first px
  assert.deepStrictEqual(surface._lastPaint("main")[0], [0, 255, 0]); // [r,g,b]
  send({ event: "room_frame", fixture: "ghost", channels: [1, 2, 3] }); // no throw

  // an UNBOUND fixture (dev: null) still has a strip canvas and still
  // paints: the Room is loaded with all its instruments whether or not a
  // device is bound, so accent's frames must land too.
  send({ event: "room_frame", fixture: "accent",
         channels: [0, 0, 255].concat(Array(87).fill(0)) });  // B=255 first px
  assert.ok(surface._lastPaint("accent").length > 0);
  assert.deepStrictEqual(surface._lastPaint("accent")[0], [0, 0, 255]);

  // no Room configured
  send({ event: "room_changed", room: null });
  assert.ok(card.innerHTML.includes("No Room configured"));

  console.log("surface_panel: ok");
})().catch((e) => { console.error(e); process.exit(1); });
