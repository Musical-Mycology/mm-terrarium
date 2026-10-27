"use strict";
// The per-fixture LED trace: step-held RGBW samples and gesture markers over
// a sliding window on Control's clock.
const { test } = require("node:test");
const assert = require("node:assert");

test("samples are step-held and windowed to 10 s", async () => {
  const { FixtureTrace } = await import("../../console/static/led_trace.js");
  const tr = new FixtureTrace(2);
  tr.pushFrame(0, [0, 0, 0, 0, 1, 2, 3, 4]);
  tr.pushFrame(4, [0, 0, 0, 0, 9, 9, 9, 9]);
  const s = tr.samples(1, 12);
  assert.deepStrictEqual(s.map((x) => [x.t, x.r]), [[2, 1], [4, 9]]);
  assert.deepStrictEqual(s[0], { t: 2, r: 1, g: 2, b: 3, w: 4 });
});

test("with no earlier frame the first sample is held back to the window start", async () => {
  const { FixtureTrace } = await import("../../console/static/led_trace.js");
  const tr = new FixtureTrace(1);
  tr.pushFrame(5, [1, 1, 1, 1]);
  // was [5] before the full-width rule: the earliest known value now holds
  // from the window start (8 - 10), not from its own t
  assert.deepStrictEqual(tr.samples(0, 8).map((x) => x.t), [-2]);
  assert.deepStrictEqual(tr.samples(0, 3), []);
});

test("a lone sample at now is returned at now - windowS", async () => {
  const { FixtureTrace } = await import("../../console/static/led_trace.js");
  const tr = new FixtureTrace(1);
  tr.pushFrame(5, [1, 2, 3, 4]);
  assert.deepStrictEqual(tr.samples(0, 5), [{ t: -5, r: 1, g: 2, b: 3, w: 4 }]);
});

test("later samples keep their own t when the first is held back", async () => {
  const { FixtureTrace } = await import("../../console/static/led_trace.js");
  const tr = new FixtureTrace(1);
  tr.pushFrame(5, [1, 0, 0, 0]);
  tr.pushFrame(7, [9, 0, 0, 0]);
  assert.deepStrictEqual(tr.samples(0, 8).map((x) => [x.t, x.r]), [[-2, 1], [7, 9]]);
});

test("old frames are pruned but the newest older one is kept", async () => {
  const { FixtureTrace } = await import("../../console/static/led_trace.js");
  const tr = new FixtureTrace(1);
  for (let t = 0; t < 100; t++) tr.pushFrame(t, [t, 0, 0, 0]);
  const s = tr.samples(0, 99);
  assert.strictEqual(s[0].t, 89);
  assert.strictEqual(s[0].r, 89);
  assert.strictEqual(s.length, 11);
  assert.ok(tr.frames.length <= 12);
});

test("gestures outside the window are dropped", async () => {
  const { FixtureTrace } = await import("../../console/static/led_trace.js");
  const tr = new FixtureTrace(1);
  tr.pushGesture({ t: 1, at: 1, dev: "ie1", verb: "tap", refused: null });
  tr.pushGesture({ t: 9, at: 9.5, dev: "ie1", verb: "tap", refused: "busy" });
  const g = tr.gestures(12);
  assert.strictEqual(g.length, 1);
  assert.strictEqual(g[0].t, 9);
  assert.strictEqual(g[0].refused, "busy");
});

test("an unknown pixel index has no samples", async () => {
  const { FixtureTrace } = await import("../../console/static/led_trace.js");
  const tr = new FixtureTrace(1);
  tr.pushFrame(0, [1, 2, 3, 4]);
  assert.deepStrictEqual(tr.samples(5, 1), []);
});

test("a static LED's one frame is carried to the window start", async () => {
  const { FixtureTrace } = await import("../../console/static/led_trace.js");
  const tr = new FixtureTrace(1);
  tr.pushFrame(0, [5, 6, 7, 8]);
  assert.deepStrictEqual(tr.samples(0, 50), [{ t: 40, r: 5, g: 6, b: 7, w: 8 }]);
});
