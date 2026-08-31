"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const VOCAB = {
  capabilities: ["gesture.tap", "light.pixels"],
  cue_kinds: ["midi", "play", "solid", "mute"],
};

const FIXTURE = `description = "A test shroom"
capabilities = ["light.pixels"]
accepted_cues = ["midi", "play"]
`;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

(async () => {
  const wire = await import("../../console/static/wire.js");
  const forms = await import("../../console/static/design_forms.js");
  forms.initForms();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // -- with no design open, the panel shows only a muted placeholder ------
  forms.rebuild("");
  assert.strictEqual(
    byId.get("formsPanel").innerHTML.includes("select a design"),
    true,
  );

  // -- snapshot delivers vocab, then rebuild from fixture text -------------
  send({ event: "snapshot", design_vocab: VOCAB });
  forms.rebuild(FIXTURE);

  const descInput = byId.get("formDescription");
  assert.strictEqual(descInput.value, "A test shroom");

  const capsEl = byId.get("formCapabilities");
  assert.strictEqual(capsEl.children.length, 2);
  const capLabels = capsEl.children.map((c) => c.textContent);
  assert.deepStrictEqual(capLabels, ["gesture.tap", "light.pixels"]);
  // Checkboxes are the label's first child input.
  const capInputs = capsEl.children.map((c) => c.children[0]);
  assert.strictEqual(capInputs[0].checked, false); // gesture.tap not in fixture
  assert.strictEqual(capInputs[1].checked, true);  // light.pixels is

  const cuesEl = byId.get("formCues");
  assert.strictEqual(cuesEl.children.length, 4);
  const cueInputs = cuesEl.children.map((c) => c.children[0]);
  assert.strictEqual(cueInputs[0].checked, true);  // midi
  assert.strictEqual(cueInputs[1].checked, true);  // play
  assert.strictEqual(cueInputs[2].checked, false); // solid
  assert.strictEqual(cueInputs[3].checked, false); // mute

  // -- toggling a capability checkbox rewrites capabilities = [...] -------
  const textEl = byId.get("designText");
  textEl.value = FIXTURE;
  capInputs[0].checked = true; // gesture.tap
  capInputs[0].onchange();
  assert.strictEqual(
    textEl.value,
    `description = "A test shroom"
capabilities = ["gesture.tap", "light.pixels"]
accepted_cues = ["midi", "play"]
`,
  );

  // -- toggling a cue checkbox rewrites accepted_cues = [...] -------------
  textEl.value = FIXTURE;
  cueInputs[2].checked = true; // solid
  cueInputs[2].onchange();
  assert.strictEqual(
    textEl.value,
    `description = "A test shroom"
capabilities = ["light.pixels"]
accepted_cues = ["midi", "play", "solid"]
`,
  );

  // -- editing description writes through ----------------------------------
  textEl.value = FIXTURE;
  descInput.value = "Renamed";
  descInput.onchange();
  assert.strictEqual(
    textEl.value,
    `description = "Renamed"
capabilities = ["light.pixels"]
accepted_cues = ["midi", "play"]
`,
  );

  // -- a manual textarea input event debounces a rebuild -------------------
  textEl.value = `description = "Manual edit"
capabilities = ["gesture.tap"]
accepted_cues = ["midi"]
`;
  textEl.oninput();
  await wait(350);
  assert.strictEqual(byId.get("formDescription").value, "Manual edit");
  const cueInputsAfter = byId.get("formCues").children.map((c) => c.children[0]);
  assert.strictEqual(cueInputsAfter[0].checked, true);  // midi
  assert.strictEqual(cueInputsAfter[1].checked, false); // play

  console.log("design_forms.test.js OK");
})();
