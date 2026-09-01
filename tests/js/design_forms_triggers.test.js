"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const VOCAB = { capabilities: [], cue_kinds: [] };

const FIXTURE = `description = "A test shroom"
capabilities = []
accepted_cues = []
[[event_triggers]]
name = "tap"
description = "a tap"
  # calibrated from sess-1 on 2026-08-30
  [event_triggers.thresholds]
  peak_g = 2.0
  window_ms = 200
[[event_triggers]]
name = "shake"
description = "a shake"
  [event_triggers.thresholds]
  peak_g = 3.0
[[stream_triggers]]
name = "smooth_tilt"
description = "EMA over tilt"
verb = "tilt"
arg = 0
transform = "smooth"
  [stream_triggers.params]
  alpha = 0.4
`;

(async () => {
  const wire = await import("../../console/static/wire.js");
  const forms = await import("../../console/static/design_forms.js");
  forms.initForms();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", design_vocab: VOCAB });
  forms.rebuild(FIXTURE);

  const sections = byId.get("formSections");

  // -- two event trigger cards render with the right numeric values -------
  const trigCards = sections.children.filter(
    (c) => c.getAttribute && (c.getAttribute("data-form-key") || "").startsWith("trig:"),
  );
  assert.strictEqual(trigCards.length, 2, "expected two event trigger cards");

  function findByKey(root, key) {
    if (root.getAttribute && root.getAttribute("data-form-key") === key) return root;
    for (const c of root.children || []) {
      const found = findByKey(c, key);
      if (found) return found;
    }
    return null;
  }

  const peakInput = findByKey(sections, "trig:tap:peak_g");
  assert.notStrictEqual(peakInput, null);
  assert.strictEqual(Number(peakInput.value), 2.0);
  const windowInput = findByKey(sections, "trig:tap:window_ms");
  assert.strictEqual(Number(windowInput.value), 200);

  const shakePeak = findByKey(sections, "trig:shake:peak_g");
  assert.strictEqual(Number(shakePeak.value), 3.0);

  // -- editing peak_g writes only that line; comment untouched ------------
  const textEl = byId.get("designText");
  textEl.value = FIXTURE;
  peakInput.value = "1.6";
  peakInput.onchange();
  assert.strictEqual(
    textEl.value.includes("  peak_g = 1.6"),
    true,
  );
  assert.strictEqual(
    textEl.value.includes("  # calibrated from sess-1 on 2026-08-30"),
    true,
  );
  assert.strictEqual(
    textEl.value.includes("  window_ms = 200"),
    true,
  );
  // shake's block is untouched, byte-identical.
  const shakeBlockBefore = FIXTURE.split("\n").slice(9, 12).join("\n");
  const shakeBlockAfter = textEl.value.split("\n").slice(9, 12).join("\n");
  assert.strictEqual(shakeBlockAfter, shakeBlockBefore);

  // -- description field write-through --------------------------------
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);
  const tapDesc = findByKey(byId.get("formSections"), "trig:tap:description");
  tapDesc.value = "renamed tap";
  tapDesc.onchange();
  assert.strictEqual(textEl.value.includes('description = "renamed tap"'), true);

  // -- Remove deletes exactly that block, sibling byte-identical ----------
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);
  const removeBtn = findByKey(byId.get("formSections"), "trig:shake:remove");
  assert.notStrictEqual(removeBtn, null);
  removeBtn.onclick();
  assert.strictEqual(textEl.value.includes('name = "shake"'), false);
  assert.strictEqual(textEl.value.includes('name = "tap"'), true);
  assert.strictEqual(
    textEl.value.includes(
      `[[event_triggers]]
name = "tap"
description = "a tap"
  # calibrated from sess-1 on 2026-08-30
  [event_triggers.thresholds]
  peak_g = 2.0
  window_ms = 200`,
    ),
    true,
  );

  // -- add-trigger with a stubbed prompt appends the block -----------------
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);
  const realWindow = globalThis.window;
  globalThis.window = globalThis.window || {};
  globalThis.window.prompt = () => "double_tap";
  const addBtn = findByKey(byId.get("formSections"), "add-event-trigger");
  assert.notStrictEqual(addBtn, null);
  addBtn.onclick();
  assert.strictEqual(textEl.value.includes('[[event_triggers]]\nname = "double_tap"'), true);

  // decline on falsy prompt result: no block appended.
  const beforeDecline = textEl.value;
  globalThis.window.prompt = () => "";
  forms.rebuild(textEl.value);
  const addBtn2 = findByKey(byId.get("formSections"), "add-event-trigger");
  addBtn2.onclick();
  assert.strictEqual(textEl.value, beforeDecline);
  if (realWindow !== undefined) globalThis.window = realWindow;

  // -- a stream trigger card renders its params ----------------------------
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);
  const alphaInput = findByKey(byId.get("formSections"), "strig:smooth_tilt:alpha");
  assert.notStrictEqual(alphaInput, null);
  assert.strictEqual(Number(alphaInput.value), 0.4);

  alphaInput.value = "0.5";
  alphaInput.onchange();
  assert.strictEqual(textEl.value.includes("  alpha = 0.5"), true);

  console.log("design_forms_triggers.test.js OK");
})();
