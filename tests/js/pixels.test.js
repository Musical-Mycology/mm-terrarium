"use strict";
// The one RGBW pixel decoder (contract v5): 4 channels per pixel, W folded
// onto r, g and b clipped at 255. A trailing partial pixel is dropped.
const { test } = require("node:test");
const assert = require("node:assert");

test("RGBW decodes in order with W folded into r, g, b", async () => {
  const { decodeRgbw } = await import("../../console/static/pixels.js");
  assert.deepStrictEqual(decodeRgbw([10, 20, 30, 0]), [[10, 20, 30]]);
  assert.deepStrictEqual(decodeRgbw([10, 20, 30, 5]), [[15, 25, 35]]);
  assert.deepStrictEqual(decodeRgbw([10, 0, 0, 250]), [[255, 250, 250]]);
});

test("a W-only frame draws white, not black", async () => {
  const { decodeRgbw } = await import("../../console/static/pixels.js");
  assert.deepStrictEqual(decodeRgbw([0, 0, 0, 255, 0, 0, 0, 255]),
    [[255, 255, 255], [255, 255, 255]]);
});

test("a trailing partial pixel is dropped and missing values read 0", async () => {
  const { decodeRgbw } = await import("../../console/static/pixels.js");
  assert.deepStrictEqual(decodeRgbw([1, 2, 3, 4, 9, 9]), [[5, 6, 7]]);
  assert.deepStrictEqual(decodeRgbw([]), []);
});
