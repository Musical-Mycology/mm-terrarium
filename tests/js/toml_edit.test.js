"use strict";
const assert = require("node:assert");

const FIXTURE = `description = "A test shroom"
capabilities = ["light.pixels", "gesture.tap"]
accepted_cues = ["midi", "play"]

[[event_triggers]]
name = "tap"
description = "a tap"
  # calibrated from sess-1 on 2026-08-30
  [event_triggers.thresholds]
  peak_g = 2.0
  window_ms = 200

[[functions]]
name = "pulse"
kind = "scripted"
description = "two step pulse"
script = [
  { offset = 0.0, midi = [176, 74, 127] },
  { offset = 1.0, midi = [176, 74, 0] },
]
`;

(async () => {
  const t = await import("../../console/static/toml_edit.js");

  // -- splitBlocks ----------------------------------------------------------
  const lines = FIXTURE.split("\n");
  const blocks = t.splitBlocks(FIXTURE);
  assert.strictEqual(blocks.length, 3);
  assert.strictEqual(blocks[0].header, null);
  assert.strictEqual(blocks[0].name, null);
  assert.strictEqual(blocks[0].start, 0);
  assert.strictEqual(blocks[0].end, 4);

  assert.strictEqual(blocks[1].header, "[[event_triggers]]");
  assert.strictEqual(blocks[1].name, "tap");
  assert.strictEqual(lines[blocks[1].start], "[[event_triggers]]");
  assert.strictEqual(blocks[1].end, 12);
  assert.strictEqual(lines[10], "  window_ms = 200");

  assert.strictEqual(blocks[2].header, "[[functions]]");
  assert.strictEqual(blocks[2].name, "pulse");
  assert.strictEqual(lines[blocks[2].start], "[[functions]]");
  assert.strictEqual(blocks[2].end, lines.length);

  // -- getScalar / setScalar (top level) ------------------------------------
  assert.strictEqual(t.getScalar(FIXTURE, null, "description"), '"A test shroom"');
  assert.strictEqual(t.getScalar(FIXTURE, null, "nope"), null);

  const setDesc = t.setScalar(FIXTURE, null, "description", '"Renamed shroom"');
  const expectedSetDesc = FIXTURE.replace(
    'description = "A test shroom"',
    'description = "Renamed shroom"',
  );
  assert.strictEqual(setDesc, expectedSetDesc);

  // setScalar within a block, key absent -> appended as last line of block
  const funcBlock = { header: "[[functions]]", name: "pulse" };
  const withNewKey = t.setScalar(FIXTURE, funcBlock, "extra", '"x"');
  assert.ok(withNewKey.includes('\nextra = "x"\n'));
  assert.notStrictEqual(withNewKey, FIXTURE);

  // missing block -> unchanged
  const missingBlock = { header: "[[functions]]", name: "nope" };
  assert.strictEqual(t.setScalar(FIXTURE, missingBlock, "extra", '"x"'), FIXTURE);

  // -- getStringArray / setStringArray ---------------------------------------
  assert.deepStrictEqual(t.getStringArray(FIXTURE, "capabilities"), ["light.pixels", "gesture.tap"]);
  assert.strictEqual(t.getStringArray(FIXTURE, "nope"), null);

  const setArr = t.setStringArray(FIXTURE, "capabilities", ["light.pixels"]);
  const expectedSetArr = FIXTURE.replace(
    'capabilities = ["light.pixels", "gesture.tap"]',
    'capabilities = ["light.pixels"]',
  );
  assert.strictEqual(setArr, expectedSetArr);

  // -- getThresholds / setThreshold ------------------------------------------
  assert.deepStrictEqual(t.getThresholds(FIXTURE, "tap"), { peak_g: 2.0, window_ms: 200 });
  assert.strictEqual(t.getThresholds(FIXTURE, "nope"), null);

  const setThr = t.setThreshold(FIXTURE, "tap", "peak_g", 1.6);
  const expectedSetThr = FIXTURE.replace("  peak_g = 2.0", "  peak_g = 1.6");
  assert.strictEqual(setThr, expectedSetThr);
  assert.ok(setThr.includes("  # calibrated from sess-1 on 2026-08-30"));

  const addThr = t.setThreshold(FIXTURE, "tap", "double_ms", 400);
  const expectedAddThr = FIXTURE.replace(
    "  window_ms = 200\n",
    "  window_ms = 200\n  double_ms = 400\n",
  );
  assert.strictEqual(addThr, expectedAddThr);

  assert.strictEqual(t.setThreshold(FIXTURE, "nope", "peak_g", 1.0), FIXTURE);

  // -- listScriptSteps / setScriptStep / addScriptStep / removeScriptStep ---
  const steps = t.listScriptSteps(FIXTURE, "pulse");
  assert.strictEqual(steps.length, 2);
  assert.strictEqual(steps[0].kind, "midi");
  assert.strictEqual(steps[0].offset, 0.0);
  assert.strictEqual(steps[0].args, "[176, 74, 127]");
  assert.strictEqual(steps[1].kind, "midi");
  assert.strictEqual(steps[1].offset, 1.0);
  assert.strictEqual(steps[1].args, "[176, 74, 0]");
  assert.strictEqual(steps[0].line, lines.indexOf("  { offset = 0.0, midi = [176, 74, 127] },"));

  assert.deepStrictEqual(t.listScriptSteps(FIXTURE, "nope"), null);

  const setStep = t.setScriptStep(FIXTURE, "pulse", 1, { offset: 1.0, kind: "midi", args: "[176, 74, 64]" });
  const expectedSetStep = FIXTURE.replace(
    "  { offset = 1.0, midi = [176, 74, 0] },",
    "  { offset = 1, midi = [176, 74, 64] },",
  );
  assert.strictEqual(setStep, expectedSetStep);
  assert.strictEqual(t.setScriptStep(FIXTURE, "nope", 0, { offset: 0, kind: "midi", args: "[]" }), FIXTURE);

  const addStep = t.addScriptStep(FIXTURE, "pulse", { offset: 2.0, kind: "play", args: '"chime"' });
  const expectedAddStep = FIXTURE.replace(
    "  { offset = 1.0, midi = [176, 74, 0] },\n]",
    "  { offset = 1.0, midi = [176, 74, 0] },\n  { offset = 2, play = \"chime\" },\n]",
  );
  assert.strictEqual(addStep, expectedAddStep);
  assert.strictEqual(t.addScriptStep(FIXTURE, "nope", { offset: 0, kind: "midi", args: "[]" }), FIXTURE);

  const removeStep = t.removeScriptStep(FIXTURE, "pulse", 0);
  const expectedRemoveStep = FIXTURE.replace("  { offset = 0.0, midi = [176, 74, 127] },\n", "");
  assert.strictEqual(removeStep, expectedRemoveStep);
  assert.strictEqual(t.removeScriptStep(FIXTURE, "nope", 0), FIXTURE);
  assert.strictEqual(t.removeScriptStep(FIXTURE, "pulse", 99), FIXTURE);

  // -- appendBlock / removeBlock round-trip ----------------------------------
  const newBlock = [
    '[[event_triggers]]',
    'name = "wiggle"',
    'description = "a wiggle"',
  ].join("\n");
  const appended = t.appendBlock(FIXTURE, newBlock);
  assert.strictEqual(appended, FIXTURE + "\n" + newBlock + "\n");

  const removed = t.removeBlock(appended, "[[event_triggers]]", "wiggle");
  assert.strictEqual(removed, FIXTURE);

  assert.strictEqual(t.removeBlock(FIXTURE, "[[event_triggers]]", "nope"), FIXTURE);

  console.log("toml_edit.test.js OK");
})();
