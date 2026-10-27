"use strict";
// The Room card's 3D tiles, the LED inspector and the 3D bump-out link.
// WebGL cannot run under node, so the view is a fake installed through
// surface._setViewFactory; view3d.js itself is checked in a real browser.
const { test, before } = require("node:test");
const assert = require("node:assert");
const { FakeSocket } = require("./_dom_stub.js");

const SHA_T = "a".repeat(64);
const SHA_B = "b".repeat(64);
const REASON = "model is 412000 triangles (budget 300000)";

const fixture = (name, extra) => Object.assign({
  name, pixel_count: 4, channel_start: 0, channel_count: 16, color_order: "GRB",
  zones: [{ name: `${name}.all`, start: 0, count: 4 }],
  dev: null, url: null, muted: false, artnet: false,
  instrument: { name: "generic_surface", capabilities: ["light.surface"],
                functions: [], accepted_cues: ["solid"] },
  model: { sha256: null, status: "none", reason: null },
}, extra);

const room = (over = {}) => ({
  room_type: "MIDITEST",
  capability: { pixel_count: 16, color_order: "GRB", zones: [] },
  fixtures: [
    fixture("tower", { dev: "ie1", url: "http://ie1.local/watch",
                       model: { sha256: over.towerSha || SHA_T, status: "ok", reason: null } }),
    fixture("bars", { artnet: true, model: { sha256: SHA_B, status: "ok", reason: null } }),
    fixture("array"),
    fixture("big", { model: { sha256: null, status: "over_budget", reason: REASON } }),
  ],
  instruments: [], controllers: over.controllers || {},
});

const LAYOUT = {
  fixture: "tower", sha256: SHA_T,
  pixels: [0, 1, 2, 3].map((i) => ({
    index: i, x_mm: 0, y_mm: 0, z_mm: i * 100, size: "medium",
    model_zone: i < 2 ? "mast" : "responders", room_zones: ["tower.all"] })),
};

const walk = (node, pred, out = []) => {
  if (pred(node)) out.push(node);
  for (const c of node.children || []) walk(c, pred, out);
  return out;
};
const flush = () => new Promise((r) => setImmediate(r));

let surface;
let send;
let made = [];
let rejectWith = null;

before(async () => {
  const wire = await import("../../console/static/wire.js");
  surface = await import("../../console/static/surface.js");
  surface._setViewFactory((container, opts) => {
    const v = { container, opts, painted: [], selected: [], disposed: false,
      paint(ch) { this.painted.push(ch); },
      select(i) { this.selected.push(i); },
      dispose() { this.disposed = true; } };
    made.push(v);
    return rejectWith ? Promise.reject(new Error(rejectWith)) : Promise.resolve(v);
  });
  surface.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  send = (m) => sock.onmessage({ data: JSON.stringify(m) });
});

function fresh(r = room()) {
  send({ event: "snapshot", room: null });
  made = [];
  rejectWith = null;
  send({ event: "snapshot", room: r });
}

const viewFor = (name) => made.filter((v) => v.opts.fixture === name).at(-1);

test("a fixture with model.status ok gets a 3D tile and keeps its strip row", async () => {
  fresh();
  await flush();
  assert.ok(surface._tileFor("tower"), "tower has a tile");
  assert.ok(surface._canvasFor("tower"), "tower keeps its strip");
  const v = viewFor("tower");
  assert.ok(v, "the view factory was called for tower");
  assert.strictEqual(v.opts.sha256, SHA_T);
  assert.strictEqual(typeof v.opts.onPick, "function");
});

test("a fixture with model.status none shows only the strip", async () => {
  fresh();
  await flush();
  assert.strictEqual(surface._tileFor("array"), undefined);
  assert.ok(surface._canvasFor("array"));
  assert.ok(!made.some((v) => v.opts.fixture === "array"));
});

test("an over-budget fixture shows the strip and its reason", async () => {
  fresh();
  await flush();
  assert.ok(surface._canvasFor("big"));
  assert.ok(!made.some((v) => v.opts.fixture === "big"), "no view for an over-budget model");
  const tile = surface._tileFor("big");
  assert.ok(tile && tile.textContent.includes(REASON));
});

test("a factory rejection falls back to the strip with the error message", async () => {
  send({ event: "snapshot", room: null });
  made = [];
  rejectWith = "this browser has no WebGL2";
  send({ event: "snapshot", room: room() });
  await flush();
  assert.ok(surface._canvasFor("tower"));
  assert.ok(surface._tileFor("tower").textContent.includes("this browser has no WebGL2"));
  // the failure sticks: a later room_changed does not retry every tick
  const calls = made.length;
  send({ event: "room_changed", room: room({ controllers: { 74: 3 } }) });
  await flush();
  assert.strictEqual(made.length, calls);
});

test("tile_survives_room_changed_with_same_model", async () => {
  fresh();
  await flush();
  const tile = surface._tileFor("tower");
  const calls = made.filter((v) => v.opts.fixture === "tower").length;
  send({ event: "room_changed", room: room({ controllers: { 74: 9 } }) });
  await flush();
  assert.strictEqual(surface._tileFor("tower"), tile);
  assert.strictEqual(made.filter((v) => v.opts.fixture === "tower").length, calls);
  assert.strictEqual(calls, 1);
  assert.ok(!viewFor("tower").disposed);
});

test("a changed model sha rebuilds the tile", async () => {
  fresh();
  await flush();
  const first = viewFor("tower");
  const tile = surface._tileFor("tower");
  send({ event: "room_changed", room: room({ towerSha: "c".repeat(64) }) });
  await flush();
  assert.ok(first.disposed, "the old view is disposed");
  assert.notStrictEqual(surface._tileFor("tower"), tile);
  assert.strictEqual(viewFor("tower").opts.sha256, "c".repeat(64));
});

test("room_frame paints the fixture's view with its channels", async () => {
  fresh();
  // a replayed frame can arrive before the view is ready: it is painted on arrival
  send({ event: "room_frame", fixture: "tower", channels: [1, 2, 3, 4], t: 1 });
  await flush();
  const v = viewFor("tower");
  assert.deepStrictEqual(v.painted.at(-1), [1, 2, 3, 4]);
  send({ event: "room_frame", fixture: "tower", channels: [9, 9, 9, 9], t: 2 });
  assert.deepStrictEqual(v.painted.at(-1), [9, 9, 9, 9]);
  assert.ok(!viewFor("bars").painted.some((c) => c[0] === 9), "only its own fixture");
});

test("picking an LED shows its address and RGBW in the inspector", async () => {
  fresh();
  await flush();
  const ch = [0, 0, 0, 0, 0, 0, 0, 0, 10, 20, 30, 40, 0, 0, 0, 0];
  send({ event: "room_frame", fixture: "tower", channels: ch, t: 3 });
  viewFor("tower").opts.onPick(2, LAYOUT);
  const insp = surface._inspectorEl();
  const text = insp.textContent;
  assert.ok(text.includes("tower"), text);
  assert.ok(text.includes("pixel 2"), text);
  assert.ok(text.includes("tower.all"), text);
  assert.ok(text.includes("responders"), text);
  assert.ok(text.includes("R 10") && text.includes("G 20") && text.includes("B 30") && text.includes("W 40"), text);
  assert.ok(text.includes("last 10 s · 10 Hz"), text);
  assert.deepStrictEqual(viewFor("tower").selected.at(-1), 2);
  assert.strictEqual(viewFor("bars").selected.at(-1), null, "other tiles drop their ring");
  // a new frame updates the live RGBW in place
  ch[8] = 99;
  send({ event: "room_frame", fixture: "tower", channels: ch, t: 4 });
  assert.ok(surface._inspectorEl().textContent.includes("R 99"));
});

test("a gesture event appears in the selected LED's trace", async () => {
  fresh();
  await flush();
  send({ event: "room_frame", fixture: "tower", channels: new Array(16).fill(0), t: 10 });
  viewFor("tower").opts.onPick(0, LAYOUT);
  send({ event: "gesture", dev: "ie1", verb: "tap", t: 11, at: 11.2, refused: null });
  send({ event: "gesture", dev: "ie2", verb: "shake", t: 12, at: 12.2, refused: "busy" });
  const text = surface._inspectorEl().textContent;
  assert.ok(text.includes("tap ie1"), text);
  assert.ok(text.includes("shake ie2 (refused: busy)"), text);
});

test("artnet_fixture_gets_3d_popout", async () => {
  fresh();
  await flush();
  const links = walk(surface._tileFor("bars"), (n) => n.className === "popout");
  assert.strictEqual(links.length, 1);
  assert.strictEqual(links[0].tagName, "a");
  assert.strictEqual(links[0].href, "fixture3d.html?fixture=bars");
  assert.strictEqual(links[0].target, "_blank");
  assert.strictEqual(links[0].rel, "noopener");
  // the strip head no longer carries a ↗ of its own
  assert.ok(!surface._bindCtlFor("tower").children.some((c) => c.className === "popout"));
});

test("the bound-device chip links to the device canvas", async () => {
  fresh();
  await flush();
  const chip = surface._bindCtlFor("tower").children[0];
  assert.strictEqual(chip.tagName, "a");
  assert.strictEqual(chip.className, "chip sage");
  assert.strictEqual(chip.textContent, "ie1");
  assert.strictEqual(chip.href, "http://ie1.local/watch");
  assert.strictEqual(chip.target, "_blank");
  assert.strictEqual(chip.rel, "noopener");
});

test("a static LED's trace is held flat to now across the full width", async () => {
  const { mountInspector } = await import("../../console/static/inspector.js");
  const el = document.createElement("div");
  const insp = mountInspector(el);
  insp.select({ address: { fixture: "tower", index: 0, roomZones: [], modelZone: null } });
  insp.update({
    rgbw: { r: 200, g: 0, b: 0, w: 0 },
    samples: [{ t: 40, r: 200, g: 0, b: 0, w: 0 }],
    gestures: [{ t: 45, at: 45.2, dev: "ie1", verb: "tap", refused: "busy" }],
    now: 50,
  });
  const canvas = walk(el, (n) => n.tagName === "canvas")[0];
  const ctx = canvas.getContext("2d");
  const lines = ctx.ops.filter((o) => o.op === "lineTo");
  // four channels, each reaching the right edge
  assert.strictEqual(lines.filter((o) => o.x === canvas.width).length >= 4, true,
    JSON.stringify(ctx.ops));
  const dashed = ctx.calls.filter((c) => c.type === "stroke" && c.lineDash && c.lineDash.length);
  assert.strictEqual(dashed.length, 1, "one dashed gesture marker");
  assert.strictEqual(dashed[0].strokeStyle, "#c07850", "a refused gesture is terracotta");
});

test("the inspector's rows survive a second room_frame (rule: frames repaint, never rebuild)", async () => {
  fresh();
  await flush();
  send({ event: "room_frame", fixture: "tower", channels: new Array(16).fill(0), t: 20 });
  viewFor("tower").opts.onPick(1, LAYOUT);
  send({ event: "gesture", dev: "ie1", verb: "tap", t: 21, at: 21.2, refused: null });
  const insp = surface._inspectorEl();
  const dds = walk(insp, (n) => n.tagName === "dd");
  const lis = walk(insp, (n) => n.tagName === "li");
  assert.ok(dds.length >= 3 && lis.length === 1);
  send({ event: "room_frame", fixture: "tower", channels: [0, 0, 0, 0, 77, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], t: 22 });
  send({ event: "room_frame", fixture: "tower", channels: [0, 0, 0, 0, 88, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], t: 23 });
  const dds2 = walk(insp, (n) => n.tagName === "dd");
  const lis2 = walk(insp, (n) => n.tagName === "li");
  assert.deepStrictEqual(dds2, dds, "address rows are the same nodes");
  assert.deepStrictEqual(lis2, lis, "gesture rows are the same nodes");
  assert.ok(insp.textContent.includes("R 88"), "the RGBW text still updates");
  // a new gesture does rebuild the list
  send({ event: "gesture", dev: "ie2", verb: "shake", t: 24, at: 24.2, refused: null });
  assert.strictEqual(walk(insp, (n) => n.tagName === "li").length, 2);
});

test("a rejected view stops collecting a trace", async () => {
  send({ event: "snapshot", room: null });
  made = [];
  rejectWith = "the model failed to load (bad glb)";
  send({ event: "snapshot", room: room() });
  await flush();
  assert.strictEqual(surface._traceFor("tower"), undefined);
});

test("switching Rooms disposes absent fixtures' tiles and rebuilds a changed sha", async () => {
  fresh();
  await flush();
  const tower = viewFor("tower");
  const bars = viewFor("bars");
  const other = {
    ...room({ towerSha: "d".repeat(64) }),
    room_type: "OTHER",
    fixtures: [fixture("tower", { model: { sha256: "d".repeat(64), status: "ok", reason: null } })],
  };
  send({ event: "room_changed", room: other });
  await flush();
  assert.ok(bars.disposed, "bars left the Room: its view is disposed");
  assert.strictEqual(surface._tileFor("bars"), undefined);
  assert.ok(tower.disposed, "tower's sha changed: the old view is disposed");
  assert.strictEqual(viewFor("tower").opts.sha256, "d".repeat(64));
  assert.ok(!viewFor("tower").disposed);
});
