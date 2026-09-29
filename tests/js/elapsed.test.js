"use strict";
// elapsed.js: "stage · N s" formatting and the injectable stage timer
// (ticks with a fake clock, resets on a new stage, stops on finish).
const assert = require("node:assert");

(async () => {
  const { formatStage, createStageTimer } = await import("../../console/static/elapsed.js");

  assert.strictEqual(formatStage("spawning arco", 0), "spawning arco");
  assert.strictEqual(formatStage("spawning arco", 0.9), "spawning arco");
  assert.strictEqual(formatStage("spawning arco", 7), "spawning arco · 7 s");
  assert.strictEqual(formatStage("spawning arco", 7.9), "spawning arco · 7 s");

  let t = 1000;
  const ivs = new Map();
  let nextId = 1;
  const painted = [];
  const timer = createStageTimer({
    render: (s) => painted.push(s),
    now: () => t,
    setInterval: (fn, ms) => { ivs.set(nextId, { fn, ms }); return nextId++; },
    clearInterval: (h) => ivs.delete(h),
  });
  const tick = (ms) => { t += ms; for (const iv of [...ivs.values()]) iv.fn(); };

  timer.start("validating");
  assert.deepStrictEqual(painted, ["validating"]);
  assert.strictEqual(ivs.size, 1);
  assert.strictEqual([...ivs.values()][0].ms, 1000);

  tick(1000);
  tick(1000);
  assert.strictEqual(painted.at(-1), "validating · 2 s");

  // a new stage resets the count and never leaves two intervals running
  timer.start("spawning arco");
  assert.strictEqual(painted.at(-1), "spawning arco");
  assert.strictEqual(ivs.size, 1);
  tick(7000);
  assert.strictEqual(painted.at(-1), "spawning arco · 7 s");

  // stop clears and nothing paints afterwards
  timer.stop();
  assert.strictEqual(ivs.size, 0);
  assert.strictEqual(timer.active(), false);
  const n = painted.length;
  tick(5000);
  assert.strictEqual(painted.length, n);
})();
