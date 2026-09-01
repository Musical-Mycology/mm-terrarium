"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const VOCAB = { capabilities: [], cue_kinds: [] };

const FIXTURE = `description = "A test shroom"
capabilities = []
accepted_cues = []
[[functions]]
name = "venue_array"
kind = "generator"
waveform = "triangle"
period = 12.0
lo = 0
hi = 127
  [functions.lane]
  dev = "room"
  status = 176
  data1 = 74

[[functions]]
name = "play_aurora"
kind = "scripted"
description = "Hue bloom on the ring"
script = [
  { offset = 0.0, midi = [176, 74, 127] },
  { offset = 1.0, midi = [176, 74, 0] },
]
`;

function findByKey(root, key) {
  if (root.getAttribute && root.getAttribute("data-form-key") === key) return root;
  for (const c of root.children || []) {
    const found = findByKey(c, key);
    if (found) return found;
  }
  return null;
}

(async () => {
  const wire = await import("../../console/static/wire.js");
  const forms = await import("../../console/static/design_forms.js");
  forms.initForms();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();

  send({ event: "snapshot", design_vocab: VOCAB });
  function send(m) { sock.onmessage({ data: JSON.stringify(m) }); }

  const textEl = byId.get("designText");
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);

  const sections = byId.get("formSections");

  // -- both function cards render ------------------------------------------
  const fnCards = sections.children.filter(
    (c) => c.getAttribute && (c.getAttribute("data-form-key") || "").startsWith("fn:"),
  );
  assert.strictEqual(fnCards.length, 2, "expected two function cards");

  // -- generator card: fields render with correct values -------------------
  const waveformInput = findByKey(sections, "fn:venue_array:waveform");
  assert.notStrictEqual(waveformInput, null);
  assert.strictEqual(waveformInput.value, "triangle");

  const periodInput = findByKey(sections, "fn:venue_array:period");
  assert.notStrictEqual(periodInput, null);
  assert.strictEqual(Number(periodInput.value), 12.0);

  const loInput = findByKey(sections, "fn:venue_array:lo");
  assert.strictEqual(Number(loInput.value), 0);
  const hiInput = findByKey(sections, "fn:venue_array:hi");
  assert.strictEqual(Number(hiInput.value), 127);

  // lane info rendered read-only (not an input) somewhere under the card
  const generatorCard = findByKey(sections, "fn:venue_array");
  assert.notStrictEqual(generatorCard, null);

  // -- editing generator period rewrites only that line ---------------------
  periodInput.value = "8.0";
  periodInput.onchange();
  assert.strictEqual(textEl.value.includes("period = 8.0"), true);
  assert.strictEqual(textEl.value.includes('waveform = "triangle"'), true);
  assert.strictEqual(textEl.value.includes("dev = \"room\""), true);

  // -- scripted card: description + step table ------------------------------
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);
  const descInput = findByKey(byId.get("formSections"), "fn:play_aurora:description");
  assert.notStrictEqual(descInput, null);
  assert.strictEqual(descInput.value, "Hue bloom on the ring");

  const step0Offset = findByKey(byId.get("formSections"), "fn:play_aurora:step:0:offset");
  assert.notStrictEqual(step0Offset, null);
  assert.strictEqual(Number(step0Offset.value), 0.0);

  const step0Args = findByKey(byId.get("formSections"), "fn:play_aurora:step:0:args");
  assert.notStrictEqual(step0Args, null);
  assert.strictEqual(step0Args.value, "[176, 74, 127]");

  // -- editing a scripted step's args rewrites exactly that line -----------
  step0Args.value = "[176, 74, 100]";
  step0Args.onchange();
  assert.strictEqual(
    textEl.value.includes("{ offset = 0, midi = [176, 74, 100] },"),
    true,
  );
  assert.strictEqual(
    textEl.value.includes("{ offset = 1.0, midi = [176, 74, 0] },"),
    true,
  );

  // -- adding a step appends before the closing bracket ---------------------
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);
  const addOffset = findByKey(byId.get("formSections"), "fn:play_aurora:step:add-offset");
  const addKind = findByKey(byId.get("formSections"), "fn:play_aurora:step:add-kind");
  const addArgs = findByKey(byId.get("formSections"), "fn:play_aurora:step:add-args");
  const addBtn = findByKey(byId.get("formSections"), "fn:play_aurora:step:add");
  assert.notStrictEqual(addOffset, null);
  assert.notStrictEqual(addKind, null);
  assert.notStrictEqual(addArgs, null);
  assert.notStrictEqual(addBtn, null);

  addOffset.value = "2.0";
  addKind.value = "play";
  addArgs.value = '"chime"';
  addBtn.onclick();
  const lines = textEl.value.split("\n");
  assert.strictEqual(textEl.value.includes('{ offset = 2.0, play = "chime" },'), true);
  const scriptCloseIdx = lines.indexOf("]");
  const addedLineIdx = lines.findIndex((l) => l.includes('play = "chime"'));
  assert.strictEqual(addedLineIdx < scriptCloseIdx, true);

  // -- removing a step deletes one line --------------------------------------
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);
  const removeStep0 = findByKey(byId.get("formSections"), "fn:play_aurora:step:0:remove");
  assert.notStrictEqual(removeStep0, null);
  removeStep0.onclick();
  assert.strictEqual(textEl.value.includes("midi = [176, 74, 127]"), false);
  assert.strictEqual(textEl.value.includes("midi = [176, 74, 0]"), true);

  // -- removing a step never restores focus onto a (now-shifted) remove -----
  // button by key: with focus on step 0's Remove control before the click,
  // the post-rebuild activeElement must not be some other row's Remove
  // button (a double-click / stray Enter would otherwise delete the wrong
  // step).
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);
  const removeStep0Again = findByKey(byId.get("formSections"), "fn:play_aurora:step:0:remove");
  removeStep0Again.focus();
  assert.strictEqual(
    globalThis.document.activeElement === removeStep0Again,
    true,
    "expected focus on the remove button before the click",
  );
  removeStep0Again.onclick();
  const activeAfterRemove = globalThis.document.activeElement;
  const activeKeyAfterRemove =
    activeAfterRemove && activeAfterRemove.getAttribute
      ? activeAfterRemove.getAttribute("data-form-key")
      : null;
  assert.strictEqual(
    activeKeyAfterRemove == null || !activeKeyAfterRemove.endsWith(":remove"),
    true,
    "focus must not land on any remove control after a destructive removal",
  );

  // -- removing a function deletes its whole block, sibling untouched -------
  textEl.value = FIXTURE;
  forms.rebuild(FIXTURE);
  const removeGenerator = findByKey(byId.get("formSections"), "fn:venue_array:remove");
  assert.notStrictEqual(removeGenerator, null);
  removeGenerator.onclick();
  assert.strictEqual(textEl.value.includes("venue_array"), false);
  assert.strictEqual(
    textEl.value.includes(
      `[[functions]]
name = "play_aurora"
kind = "scripted"
description = "Hue bloom on the ring"
script = [
  { offset = 0.0, midi = [176, 74, 127] },
  { offset = 1.0, midi = [176, 74, 0] },
]`,
    ),
    true,
  );

  console.log("design_forms_functions.test.js OK");
})();
