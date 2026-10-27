"use strict";
// The full-window 3D bump-out page. WebGL cannot run under node, so the view
// is a fake installed through fixture3d._setViewFactory.
const { test, before } = require("node:test");
const assert = require("node:assert");
const { FakeSocket } = require("./_dom_stub.js");

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);
const REASON = "model is 412000 triangles (budget 300000)";

const fixture = (name, model) => ({
  name, pixel_count: 4, channel_start: 0, channel_count: 16, color_order: "GRB",
  zones: [{ name: `${name}.all`, start: 0, count: 4 }], model,
});
const ok = (sha) => ({ sha256: sha, status: "ok", reason: null });
const room = (towerModel = ok(SHA_A), extra = []) => ({
  room_type: "MIDITEST", fixtures: [fixture("tower", towerModel), fixture("bars", ok(SHA_B)), ...extra],
});
const LAYOUT = {
  fixture: "tower", sha256: SHA_A,
  pixels: [0, 1, 2, 3].map((i) => ({ index: i, x_mm: 0, y_mm: 0, z_mm: i, size: "medium",
    model_zone: "mast", room_zones: ["tower.all"] })),
};
const flush = () => new Promise((r) => setImmediate(r));
const frame = (fixtureName, v, t) => ({ event: "room_frame", fixture: fixtureName,
  channels: [v, 0, 0, 0, v, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], t });

let page, send, made, rejectWith, mount, handle;

before(async () => {
  const wire = await import("../../console/static/wire.js");
  page = await import("../../console/static/fixture3d.js");
  page._setViewFactory((container, opts) => {
    const v = { container, opts, painted: [], selected: [], disposed: false,
      paint(ch) { this.painted.push(ch); },
      select(i) { this.selected.push(i); },
      dispose() { this.disposed = true; } };
    made.push(v);
    return rejectWith ? Promise.reject(new Error(rejectWith)) : Promise.resolve(v);
  });
  mount = { view: document.createElement("div"), inspector: document.createElement("div"),
            note: document.createElement("p"), title: document.createElement("h1") };
  handle = page.startPage({ fixtureName: "tower", ...mount });
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
const text = (n) => n.textContent + n.children.map(text).join("");

test("the page builds a view for the fixture named in the query", async () => {
  fresh();
  await flush();
  assert.strictEqual(made.length, 1);
  assert.strictEqual(made[0].opts.fixture, "tower");
  assert.strictEqual(made[0].opts.sha256, SHA_A);
  assert.strictEqual(made[0].container, mount.view);
  assert.ok(text(mount.title).includes("tower"));
});

test("frames for other fixtures are ignored", async () => {
  fresh();
  await flush();
  send(frame("bars", 9, 1));
  assert.strictEqual(made[0].painted.length, 0);
  send(frame("tower", 7, 2));
  assert.strictEqual(made[0].painted.length, 1);
  assert.strictEqual(made[0].painted[0][0], 7);
});

test("a fixture missing from the Room shows 'tower is not in the loaded Room'", async () => {
  fresh({ room_type: "MIDITEST", fixtures: [fixture("bars", ok(SHA_B))] });
  await flush();
  assert.strictEqual(made.length, 0);
  assert.ok(text(mount.note).includes("tower is not in the loaded Room"));
});

test("no loaded Room shows the same message", async () => {
  fresh(null);
  await flush();
  assert.ok(text(mount.note).includes("tower is not in the loaded Room"));
});

test("a fixture with no model or an over-budget model says why", async () => {
  fresh(room({ sha256: null, status: "none", reason: null }));
  await flush();
  assert.strictEqual(made.length, 0);
  assert.ok(text(mount.note).includes("no 3D model"));
  send({ event: "room_changed", room: room({ sha256: null, status: "over_budget", reason: REASON }) });
  await flush();
  assert.ok(text(mount.note).includes(REASON));
});

test("a model sha change rebuilds the view; the same sha does not", async () => {
  fresh();
  await flush();
  send({ event: "room_changed", room: room(ok(SHA_A)) });
  await flush();
  assert.strictEqual(made.length, 1);
  send({ event: "room_changed", room: room(ok(SHA_B)) });
  await flush();
  assert.strictEqual(made.length, 2);
  assert.ok(made[0].disposed);
  assert.strictEqual(made[1].opts.sha256, SHA_B);
});

test("a late-arriving frame is painted onto a view that was still loading", async () => {
  fresh();
  send(frame("tower", 5, 1));   // before the factory promise resolves
  await flush();
  assert.strictEqual(made[0].painted.length, 1);
});

test("a factory rejection shows the reason and does not retry on room_changed", async () => {
  send({ event: "snapshot", room: null });
  made = [];
  rejectWith = "this browser has no WebGL2";
  send({ event: "snapshot", room: room() });
  await flush();
  assert.ok(text(mount.note).includes("this browser has no WebGL2"));
  send({ event: "room_changed", room: room() });
  await flush();
  assert.strictEqual(made.length, 1);
});

test("a pick fills the inspector, and a gesture marks the trace", async () => {
  fresh();
  await flush();
  send(frame("tower", 200, 5));
  made[0].opts.onPick(1, LAYOUT);
  assert.deepStrictEqual(made[0].selected.at(-1), 1);
  assert.ok(text(mount.inspector).includes("tower · pixel 1"));
  send({ event: "gesture", dev: "ie1", verb: "tap", t: 6, at: 6, refused: null });
  assert.ok(text(mount.inspector).includes("6.00"), "the gesture row shows its Control-clock t");
});

test("a snapshot clears the selection", async () => {
  fresh();
  await flush();
  made[0].opts.onPick(0, LAYOUT);
  send({ event: "snapshot", room: room() });
  await flush();
  assert.ok(text(mount.inspector).includes("Click an LED"));
});

test("a snapshot restarts the Control-clock axis and the trace", async () => {
  fresh();
  await flush();
  send(frame("tower", 50, 1000));
  made[0].opts.onPick(0, LAYOUT);
  send({ event: "gesture", dev: "ie1", verb: "tap", t: 1001, at: 1001, refused: null });
  assert.ok(text(mount.inspector).includes("1001.00"));
  send({ event: "snapshot", room: room() });   // a restarted Control: its clock starts again
  made[0].opts.onPick(0, LAYOUT);
  send(frame("tower", 60, 2));
  const out = text(mount.inspector);
  assert.ok(!out.includes("1001.00"), "the old gesture is gone with the old trace");
  assert.ok(out.includes("R 60"), "the new low-t frame is shown at now = 2");
});

test("a view that resolves after a rebuild is disposed and never adopted", async () => {
  send({ event: "snapshot", room: null });
  made = [];
  rejectWith = null;
  const held = [];
  page._setViewFactory((container, opts) => {
    const v = { opts, painted: [], disposed: false, paint(ch) { this.painted.push(ch); },
      select() {}, dispose() { this.disposed = true; } };
    made.push(v);
    return new Promise((res) => held.push(() => res(v)));
  });
  try {
    send({ event: "snapshot", room: room(ok(SHA_A)) });
    await flush();
    send({ event: "room_changed", room: room(ok(SHA_B)) });
    await flush();
    assert.strictEqual(made.length, 2);
    held[0](); held[1]();
    await flush();
    assert.ok(made[0].disposed, "the stale view is disposed");
    assert.ok(!made[1].disposed);
    send(frame("tower", 3, 1));
    assert.strictEqual(made[0].painted.length, 0);
    assert.strictEqual(made[1].painted.length, 1);
    // a pick from the stale view is ignored
    made[0].opts.onPick(1, LAYOUT);
    assert.ok(!text(mount.inspector).includes("pixel 1"), "no address rows from a stale pick");
  } finally {
    page._setViewFactory(null);
    page._setViewFactory((container, opts) => {
      const v = { container, opts, painted: [], selected: [], disposed: false,
        paint(ch) { this.painted.push(ch); }, select(i) { this.selected.push(i); },
        dispose() { this.disposed = true; } };
      made.push(v);
      return rejectWith ? Promise.reject(new Error(rejectWith)) : Promise.resolve(v);
    });
  }
});
