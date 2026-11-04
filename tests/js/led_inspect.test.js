"use strict";
// The LED inspector model: address from the layout, four wire channels, and
// the gesture label.
const { test } = require("node:test");
const assert = require("node:assert");

const layout = {
  fixture: "tower",
  sha256: "x",
  pixels: [
    { index: 0, x_mm: 0, y_mm: 0, z_mm: 0, size: 1, model_zone: "base", room_zones: ["tower.bottom", "tower.all"] },
    { index: 1, x_mm: 0, y_mm: 0, z_mm: 0, size: 1, model_zone: null, room_zones: [] },
  ],
};

test("ledAddress lists room zones and the model zone", async () => {
  const { ledAddress } = await import("../../console/static/led_inspect.js");
  assert.deepStrictEqual(ledAddress(layout, 0), {
    fixture: "tower", index: 0, roomZones: ["tower.bottom", "tower.all"], modelZone: "base",
  });
  assert.deepStrictEqual(ledAddress(layout, 1), {
    fixture: "tower", index: 1, roomZones: [], modelZone: null,
  });
});

test("ledAddress tolerates a missing pixel", async () => {
  const { ledAddress } = await import("../../console/static/led_inspect.js");
  assert.deepStrictEqual(ledAddress(layout, 9), {
    fixture: "tower", index: 9, roomZones: [], modelZone: null,
  });
});

test("rgbwAt reads four channels per pixel", async () => {
  const { rgbwAt } = await import("../../console/static/led_inspect.js");
  const ch = [1, 2, 3, 4, 5, 6, 7, 8];
  assert.deepStrictEqual(rgbwAt(ch, 1), { r: 5, g: 6, b: 7, w: 8 });
  assert.deepStrictEqual(rgbwAt(ch, 2), { r: 0, g: 0, b: 0, w: 0 });
});

test("gestureLabel marks refusals", async () => {
  const { gestureLabel } = await import("../../console/static/led_inspect.js");
  assert.strictEqual(gestureLabel({ dev: "ie1", verb: "tap", refused: null }), "tap ie1");
  assert.strictEqual(gestureLabel({ dev: "ie1", verb: "tap", refused: "busy", count: 3 }), "tap ie1 (refused: busy)");
  assert.strictEqual(gestureLabel({ dev: "ie1", verb: "tap", count: 3 }), "tap ie1");
});
