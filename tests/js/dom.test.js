"use strict";
// dom.js: the shared mk/clear DOM helpers every console panel module now
// imports instead of carrying its own copy.
const assert = require("node:assert");
const { byId } = require("./_dom_stub.js");

(async () => {
  const dom = await import("../../console/static/dom.js");

  const p = dom.mk("p", "muted", "hello");
  assert.strictEqual(p.tagName, "p");
  assert.strictEqual(p.className, "muted");
  assert.strictEqual(p.textContent, "hello");

  const span = dom.mk("span");
  assert.strictEqual(span.className, "");
  assert.strictEqual(span.textContent, "");

  const wrap = dom.mk("div");
  wrap.appendChild(dom.mk("span", null, "child"));
  assert.strictEqual(wrap.textContent, "child");
  dom.clear(wrap);
  assert.strictEqual(wrap.textContent, "");
  assert.strictEqual(wrap.children.length, 0);

  console.log("dom.test.js OK");
})();
