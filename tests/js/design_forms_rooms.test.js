"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const ROOM = `description = "Two strips"
backends = ["devicelink"]

[[fixtures]]
name = "main"
color_order = "GRB"
instrument = "dev_strip_main"
  [[fixtures.blocks]]
  name = "main"
  start = 0
  count = 60

[[fixtures]]
name = "accent"
color_order = "GRB"
instrument = "dev_strip_accent"
  [[fixtures.blocks]]
  name = "accent"
  start = 0
  count = 30
`;

// The DOM stub has no working querySelector, so locate controls the way
// design_forms.js's own findByFormKey does: walk descendants matching
// data-form-key.
function findByKey(root, key) {
  if (!root) return null;
  if (root.getAttribute && root.getAttribute("data-form-key") === key) return root;
  for (const c of root.children || []) {
    const found = findByKey(c, key);
    if (found) return found;
  }
  return null;
}

const GHOST_ROOM = `description = "One unpublished strip"
backends = ["devicelink"]

[[fixtures]]
name = "solo"
color_order = "GRB"
instrument = "ghost_strip"
  [[fixtures.blocks]]
  name = "solo"
  start = 0
  count = 12
`;

(async () => {
  const wire = await import("../../console/static/wire.js");
  const design = await import("../../console/static/design.js");
  const forms = await import("../../console/static/design_forms.js");
  design.init();
  forms.initForms();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  send({ event: "snapshot", designs: [
    { name: "dev_strip_main", state: "published", error: null, kind: "instrument" },
    { name: "dev_strip_accent", state: "published", error: null, kind: "instrument" },
    { name: "venue_array", state: "published", error: null, kind: "instrument" },
    { name: "sketch", state: "draft", error: null, kind: "instrument" },
    { name: "TEST", state: "published", error: null, kind: "room" },
  ], design_vocab: { capabilities: ["light.pixels"], cue_kinds: ["midi"] } });
  send({ event: "design", name: "TEST", state: "published", kind: "room", text: ROOM, errors: [] });

  const panel = byId.get("formsPanel");
  assert.ok(panel.innerHTML.includes("Fixtures"));
  // The room form carries none of the instrument identity controls. A
  // <label>'s own text is not serialized once it has children, so these
  // assert on data-form-key rather than on rendered text.
  assert.strictEqual(findByKey(panel, "description"), null,
    "room form has no instrument identity field");
  assert.strictEqual(findByKey(panel, "cap:light.pixels"), null,
    "room form has no capabilities checkgrid");
  assert.strictEqual(findByKey(panel, "cue:midi"), null,
    "room form has no accepted_cues checkgrid");
  const up1 = findByKey(panel, "fixture:1:up");
  const up0 = findByKey(panel, "fixture:0:up");
  const down0 = findByKey(panel, "fixture:0:down");
  assert.notStrictEqual(up1, null, "expected an Up button on the second fixture");
  assert.notStrictEqual(down0, null, "expected a Down button on the first fixture");
  assert.ok(up0.disabled, "first fixture cannot move up");
  assert.ok(findByKey(panel, "fixture:1:down").disabled, "last fixture cannot move down");
  up1.onclick();
  let text = byId.get("designText").value;
  assert.ok(text.indexOf('name = "accent"') < text.indexOf('name = "main"\ncolor_order'));

  const pick = findByKey(panel, "fixture:0:instrument");
  const options = Array.from(pick.children).map((o) => o.value);
  assert.deepStrictEqual(options, ["dev_strip_accent", "dev_strip_main", "venue_array"],
    "picker offers published instruments only, sorted");
  // The fixture's own instrument is the preselected option, not just the
  // first one a browser would fall back to.
  assert.strictEqual(pick.options.find((o) => o.selected).value, "dev_strip_accent",
    "the fixture's published instrument is preselected");
  pick.value = "venue_array";
  pick.onchange();
  text = byId.get("designText").value;
  assert.ok(text.includes('instrument = "venue_array"'));

  // A form edit on a room stays a room form (applyEdit rebuilds with the
  // open design's kind, not the instrument default).
  assert.ok(findByKey(panel, "fixture:0:instrument"), "room form survives an edit");
  assert.strictEqual(findByKey(panel, "description"), null, "still no identity field");

  // An instrument the catalog does not publish is shown truthfully: it is
  // prepended as its own option, marked unpublished and preselected, rather
  // than letting the browser fall back to showing the first published name.
  send({ event: "design", name: "GHOST", state: "published", kind: "room",
         text: GHOST_ROOM, errors: [] });
  const ghostPick = findByKey(panel, "fixture:0:instrument");
  const ghostSelected = ghostPick.options.find((o) => o.selected);
  assert.notStrictEqual(ghostSelected, undefined, "an unpublished instrument still preselects");
  assert.strictEqual(ghostSelected.value, "ghost_strip");
  assert.ok(ghostSelected.textContent.includes("unpublished"),
    "the unpublished option says so");
  assert.deepStrictEqual(ghostPick.options.map((o) => o.value),
    ["ghost_strip", "dev_strip_accent", "dev_strip_main", "venue_array"],
    "the unpublished option is prepended, published names keep their order");

  // Opening an instrument design after a room restores the instrument forms.
  send({ event: "design", name: "sketch", state: "draft", kind: "instrument",
         text: 'description = "a sketch"\ncapabilities = []\naccepted_cues = []\n', errors: [] });
  assert.notStrictEqual(findByKey(panel, "description"), null, "instrument forms come back");
  assert.notStrictEqual(findByKey(panel, "cap:light.pixels"), null, "capabilities come back");
  assert.strictEqual(findByKey(panel, "fixture:0:instrument"), null);

  console.log("design_forms_rooms.test.js OK");
})().catch((e) => { console.error(e); process.exit(1); });
