"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const DESIGNS = [
  { name: "tuneshroom", state: "published", error: null },
];

const FUNCTIONS = [
  { name: "flash", description: "flash the strip", source: "builtin" },
  { name: "riser", description: "rising sweep", source: "instrument" },
];

(async () => {
  const wire = await import("../../console/static/wire.js");
  const design = await import("../../console/static/design.js");
  design.init();
  design.initBench();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // -- renderBenchFunctions: pure renderer, one button per fn -------------
  const mount = document.getElementById("benchFunctions");
  const fired = [];
  design.renderBenchFunctions(mount, FUNCTIONS, (cmd) => fired.push(cmd));
  const buttons = mount.children;
  assert.strictEqual(buttons.length, 2);
  assert.strictEqual(buttons[0].textContent, "flash");
  assert.ok(buttons[0].className.includes("builtin"));
  assert.strictEqual(buttons[0]._attrs.title, "flash the strip");
  assert.ok(buttons[1].className.includes("instrument"));
  buttons[0].onclick();
  assert.deepStrictEqual(fired, [{ command: "bench_fire", name: "flash" }]);

  // -- paintBenchFrame: one filled rect per pixel, GRB -> rgb -------------
  const paintCanvas = document.createElement("canvas");
  design.paintBenchFrame(paintCanvas, [10, 20, 30, 40, 50, 60]);
  const ctx = paintCanvas.getContext("2d");
  assert.strictEqual(ctx.calls.length, 2);
  // channels arrive GRB: [g, r, b] per pixel
  assert.strictEqual(ctx.calls[0].fillStyle, "rgb(20,10,30)");
  assert.strictEqual(ctx.calls[1].fillStyle, "rgb(50,40,60)");

  // -- Simulate sends bench_start for the current selection, disabled with
  // no selection -----------------------------------------------------------
  const simBtn = byId.get("benchStart");
  const stopBtn = byId.get("benchStop");
  const tilt = byId.get("benchTilt");
  simBtn.onclick();
  assert.strictEqual(sock.sent.length, 0, "no selection: Simulate is a no-op");

  send({ event: "snapshot", designs: DESIGNS });
  design.openDesign({ name: "tuneshroom", state: "published", text: "x=1", errors: [] });

  simBtn.onclick();
  assert.deepStrictEqual(sock.sent.at(-1),
    { command: "bench_start", state: "published", name: "tuneshroom" });

  // -- bench_started renders fire buttons and enables Stop + slider -------
  send({ event: "bench_started", functions: FUNCTIONS });
  assert.strictEqual(byId.get("benchFunctions").children.length, 2);
  assert.strictEqual(stopBtn.disabled, false);
  assert.strictEqual(tilt.disabled, false);

  byId.get("benchFunctions").children[1].onclick();
  assert.deepStrictEqual(sock.sent.at(-1), { command: "bench_fire", name: "riser" });

  // -- bench_frame paints the canvas ---------------------------------------
  send({ event: "bench_frame", channels: [1, 2, 3] });
  const canvasCtx = byId.get("benchCanvas").getContext("2d");
  assert.strictEqual(canvasCtx.calls.length, 1);
  assert.strictEqual(canvasCtx.calls[0].fillStyle, "rgb(2,1,3)");

  // -- tilt slider sends bench_lane with the scaled value -----------------
  tilt.value = "50";
  tilt.oninput();
  assert.deepStrictEqual(sock.sent.at(-1),
    { command: "bench_lane", verb: "tilt", value: 0.5, status: 176, data1: 74 });

  // -- Stop sends bench_stop and disables/clears controls ------------------
  stopBtn.onclick();
  assert.deepStrictEqual(sock.sent.at(-1), { command: "bench_stop" });
  assert.strictEqual(stopBtn.disabled, true);
  assert.strictEqual(tilt.disabled, true);
  assert.strictEqual(byId.get("benchFunctions").children.length, 0);

  console.log("design_bench.test.js OK");
})();
