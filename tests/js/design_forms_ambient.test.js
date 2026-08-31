"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const VOCAB = { capabilities: [], cue_kinds: [] };

// The real shipped shape (instruments/venue_array.toml): fully-qualified
// headers, not the shorthand `[ambient.light]`.
const QUALIFIED_FIXTURE = `description = "6 m SK6812 venue array"
capabilities = ["light.surface", "audio.flsyn"]
accepted_cues = ["midi", "play", "solid", "mute"]
[instruments.venue_array.ambient]
[instruments.venue_array.ambient.light]
instruments = [ { instrument = "aurora", target = "primary" } ]
[instruments.venue_array.ambient.ugen]
instruments = [ { instrument = "flsyn", program = 89, drone = { key = 48, velocity = 80 } } ]
`;

// Shorthand form the plan originally described.
const SHORTHAND_FIXTURE = `description = "A test shroom"
capabilities = []
accepted_cues = []
[ambient.light]
instruments = [ { instrument = "aurora", target = "primary" } ]
[ambient.ugen]
instruments = [ { instrument = "flsyn", program = 89, drone = { key = 48, velocity = 80 } } ]
`;

const NO_AMBIENT_FIXTURE = `description = "No ambient here"
capabilities = []
accepted_cues = []
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

  // -- qualified header form: both rows render -------------------------------
  textEl.value = QUALIFIED_FIXTURE;
  forms.rebuild(QUALIFIED_FIXTURE);
  let sections = byId.get("formSections");

  const lightInstrument = findByKey(sections, "amb:light:0:instrument");
  assert.notStrictEqual(lightInstrument, null, "expected the light row's instrument input");
  assert.strictEqual(lightInstrument.value, "aurora");

  const lightTarget = findByKey(sections, "amb:light:0:target");
  assert.notStrictEqual(lightTarget, null, "expected the light row's target input");
  assert.strictEqual(lightTarget.value, "primary");

  const ugenInstrument = findByKey(sections, "amb:ugen:0:instrument");
  assert.notStrictEqual(ugenInstrument, null);
  assert.strictEqual(ugenInstrument.value, "flsyn");

  const ugenProgram = findByKey(sections, "amb:ugen:0:program");
  assert.notStrictEqual(ugenProgram, null);
  assert.strictEqual(Number(ugenProgram.value), 89);

  const ugenKey = findByKey(sections, "amb:ugen:0:key");
  assert.notStrictEqual(ugenKey, null);
  assert.strictEqual(Number(ugenKey.value), 48);

  const ugenVelocity = findByKey(sections, "amb:ugen:0:velocity");
  assert.notStrictEqual(ugenVelocity, null);
  assert.strictEqual(Number(ugenVelocity.value), 80);

  // -- editing target rewrites exactly the light instruments line -----------
  lightTarget.value = "secondary";
  lightTarget.onchange();
  assert.strictEqual(
    textEl.value.includes('instruments = [ { instrument = "aurora", target = "secondary" } ]'),
    true,
  );
  assert.strictEqual(
    textEl.value.includes('instruments = [ { instrument = "flsyn", program = 89, drone = { key = 48, velocity = 80 } } ]'),
    true,
    "ugen line must be untouched by the light edit",
  );

  // -- editing program rewrites exactly the ugen instruments line -----------
  textEl.value = QUALIFIED_FIXTURE;
  forms.rebuild(QUALIFIED_FIXTURE);
  sections = byId.get("formSections");
  const ugenProgramAgain = findByKey(sections, "amb:ugen:0:program");
  ugenProgramAgain.value = "90";
  ugenProgramAgain.onchange();
  assert.strictEqual(
    textEl.value.includes('instruments = [ { instrument = "flsyn", program = 90, drone = { key = 48, velocity = 80 } } ]'),
    true,
  );
  assert.strictEqual(
    textEl.value.includes('instruments = [ { instrument = "aurora", target = "primary" } ]'),
    true,
    "light line must be untouched by the ugen edit",
  );

  // -- shorthand header form renders the same rows ---------------------------
  textEl.value = SHORTHAND_FIXTURE;
  forms.rebuild(SHORTHAND_FIXTURE);
  sections = byId.get("formSections");
  const shorthandTarget = findByKey(sections, "amb:light:0:target");
  assert.notStrictEqual(shorthandTarget, null);
  assert.strictEqual(shorthandTarget.value, "primary");
  const shorthandProgram = findByKey(sections, "amb:ugen:0:program");
  assert.notStrictEqual(shorthandProgram, null);
  assert.strictEqual(Number(shorthandProgram.value), 89);

  // -- no ambient blocks: muted hint, no ambient inputs ----------------------
  textEl.value = NO_AMBIENT_FIXTURE;
  forms.rebuild(NO_AMBIENT_FIXTURE);
  sections = byId.get("formSections");
  assert.strictEqual(findByKey(sections, "amb:light:0:instrument"), null);
  assert.strictEqual(findByKey(sections, "amb:ugen:0:instrument"), null);
  assert.strictEqual(
    sections.innerHTML.includes("no ambient declaration (add via raw TOML)"),
    true,
    "expected the muted no-ambient hint",
  );

  console.log("design_forms_ambient.test.js OK");
})();
