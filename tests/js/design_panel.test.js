"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const DESIGNS = [
  { name: "tuneshroom", state: "published", error: null },
  { name: "glowcap", state: "draft", error: "bad toml" },
];

(async () => {
  const wire = await import("../../console/static/wire.js");
  const design = await import("../../console/static/design.js");
  design.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // -- renderDesigns: pure list rendering, with error badge --------------
  const listEl = document.getElementById("designList");
  let selected = null;
  design.renderDesigns(listEl, DESIGNS, (d) => { selected = d; });
  assert.ok(listEl.innerHTML.includes("tuneshroom [published]"));
  assert.ok(listEl.innerHTML.includes("glowcap [draft]"));
  const rows = listEl.children;
  assert.strictEqual(rows.length, 2);
  assert.ok(!rows[0].innerHTML.includes("err"), "published/no-error row has no badge");
  assert.ok(rows[1].innerHTML.includes("err"), "draft-with-error row shows an error badge");
  rows[1].onclick();
  assert.deepStrictEqual(selected, DESIGNS[1]);

  // -- snapshot/designs_listed feed the panel's own list ------------------
  send({ event: "snapshot", designs: DESIGNS });
  assert.ok(byId.get("designList").innerHTML.includes("tuneshroom"));

  // -- openDesign fills the editor and remembers the selection ------------
  design.openDesign({ name: "glowcap", state: "draft",
    text: "x = 1", errors: ["line 3: bad key"] });
  assert.strictEqual(byId.get("designText").value, "x = 1");
  assert.ok(byId.get("designErrors").innerHTML.includes("line 3: bad key"));

  // -- Save sends save_design with the open selection's name + editor text
  byId.get("designText").value = "x = 2";
  byId.get("designSave").onclick();
  assert.deepStrictEqual(sock.sent.at(-1),
    { command: "save_design", kind: "instrument", name: "glowcap", text: "x = 2" });

  // -- Publish sends publish_design for the open selection -----------------
  byId.get("designPublish").onclick();
  assert.deepStrictEqual(sock.sent.at(-1),
    { command: "publish_design", kind: "instrument", name: "glowcap" });

  // -- Clone prompts for a new name and sends clone_design with the current
  // selection as source ----------------------------------------------------
  const realPrompt = globalThis.window ? globalThis.window.prompt : undefined;
  globalThis.window = globalThis.window || {};
  globalThis.window.prompt = () => "glowcap_v2";
  byId.get("designClone").onclick();
  assert.deepStrictEqual(sock.sent.at(-1),
    { command: "clone_design", kind: "instrument", source_state: "draft",
      source_name: "glowcap", new_name: "glowcap_v2" });
  if (realPrompt !== undefined) globalThis.window.prompt = realPrompt;

  // Clone declines silently when the prompt is cancelled (returns falsy)
  globalThis.window.prompt = () => null;
  const beforeCancel = sock.sent.length;
  byId.get("designClone").onclick();
  assert.strictEqual(sock.sent.length, beforeCancel, "cancelled prompt sends nothing");

  // -- designs_changed re-renders the list and preserves the selection ----
  send({ event: "designs_changed", designs: DESIGNS });
  const afterChange = byId.get("designList");
  assert.ok(afterChange.innerHTML.includes("glowcap [draft]"));
  const selectedRow = afterChange.children.find((c) => c.classList.contains("selected"));
  assert.ok(selectedRow, "current selection stays highlighted after designs_changed");
  assert.ok(selectedRow.innerHTML.includes("glowcap"));

  // a duplicate designs_changed (idempotent broadcast + direct reply) must
  // not error and must still reflect the same rows
  send({ event: "designs_changed", designs: DESIGNS });
  assert.ok(byId.get("designList").innerHTML.includes("glowcap"));

  // -- rooms list renders separately and carries kind on every command ----
  const BOTH = [
    { name: "tuneshroom", state: "published", error: null, kind: "instrument" },
    { name: "TEST", state: "published", error: null, kind: "room" },
  ];
  send({ event: "designs_changed", designs: BOTH });
  assert.ok(byId.get("designList").innerHTML.includes("tuneshroom"));
  assert.ok(!byId.get("designList").innerHTML.includes("TEST"), "rooms stay out of the instrument list");
  assert.ok(byId.get("roomDesignList").innerHTML.includes("TEST [published]"));
  assert.ok(!byId.get("roomDesignList").innerHTML.includes("tuneshroom"),
    "instruments stay out of the rooms list");

  sock.sent.length = 0;
  byId.get("roomDesignList").children[0].onclick();
  // FakeSocket.send already JSON.parses, so sent frames are objects
  const get = sock.sent.at(-1);
  assert.strictEqual(get.command, "get_design");
  assert.strictEqual(get.kind, "room");
  assert.strictEqual(get.name, "TEST");

  design.openDesign({ name: "TEST", state: "published", kind: "room", text: "x = 1", errors: [] });
  assert.deepStrictEqual(design.getSelection(), { name: "TEST", state: "published", kind: "room" });
  sock.sent.length = 0;
  byId.get("designSave").onclick();
  assert.strictEqual(sock.sent.at(-1).kind, "room");

  // a row without kind is an instrument (older server)
  design.openDesign({ name: "glowcap", state: "draft", text: "x = 1", errors: [] });
  assert.strictEqual(design.getSelection().kind, "instrument");

  console.log("design_panel: ok");
})().catch((e) => { console.error(e); process.exit(1); });
