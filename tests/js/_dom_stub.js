"use strict";
// Shared node-test DOM stub for the console's front-end panel tests.
//
// Exports:
//   el(tagName)   -- build one stub element. Real enough that appendChild/
//                    textContent mutate a tree that a lazy `innerHTML`
//                    getter serializes recursively, so panel tests can
//                    assert against rendered HTML strings.
//   byId          -- the Map backing document.getElementById; tests use
//                    this directly (e.g. byId.get("bitPanel")) to read
//                    back whatever a module rendered into a mount point.
//   FakeSocket    -- minimal WebSocket stand-in, passed to wire.connect
//                    as WebSocketImpl.
//
// Installs `document`, `matchMedia`, and `addEventListener` on globalThis
// as a side effect of being required, so every panel test just needs
// `const { byId, FakeSocket } = require("./_dom_stub.js");` before
// importing the module under test.
//
// getElementById fidelity: reassigning .id, and removing a node via
// removeChild/remove/`node.textContent = ""`, unregisters the node's old
// byId entries, so a stale or detached id stops resolving -- matching real
// getElementById, which only finds nodes attached to the live tree.
// Residual gap: document.getElementById auto-vivifies (and permanently
// registers) an empty placeholder div for any id it has not seen yet, so
// probing an id nothing ever rendered silently succeeds instead of
// returning null the way a real DOM would. Don't lean on that null-check
// behavior in a new test; assert on the rendered node's shape instead.

function escapeText(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Drops byId's mapping for `node` and everything under it, but only where
// the entry still points at that exact node -- a later reassignment of the
// same id to a different node (or a fallback placeholder document.getElementById
// minted for a not-yet-rendered id) must not be clobbered by an unrelated
// removal.
// Called from genuine removal paths (removeChild/remove, and the
// textContent="" clear the panels use to empty a mount) so a detached or
// stale id can no longer be resolved -- real getElementById only finds
// nodes attached to the live tree. NOT called from the internal reparent
// path (_detach, used by appendChild/insertBefore moving a node that is
// already someone's child), since a same-tick move keeps the node "in the
// tree" the whole time, same as the real DOM.
function unregisterIdTree(n) {
  if (n._id && byId.get(n._id) === n) byId.delete(n._id);
  for (const c of n.children) unregisterIdTree(c);
}

function serialize(node) {
  const tag = node.tagName || "div";
  const attrs = [];
  if (node.className) attrs.push(`class="${node.className}"`);
  if (node.hidden) attrs.push("hidden");
  for (const [k, v] of Object.entries(node._attrs)) attrs.push(`${k}="${v}"`);
  const open = `<${tag}${attrs.length ? " " + attrs.join(" ") : ""}>`;
  const inner = node.children.length
    ? node.children.map(serialize).join("")
    : escapeText(node._text);
  return `${open}${inner}</${tag}>`;
}

function el(tagName) {
  const node = {
    tagName: tagName || "div",
    children: [],
    parentNode: null,
    _attrs: {},
    _text: "",
    className: "",
    hidden: false,
    value: "",
    dataset: {},
    style: {},
    onclick: null,
    classList: (() => {
      const s = new Set();
      return {
        add: (...cs) => cs.forEach((c) => s.add(c)),
        remove: (...cs) => cs.forEach((c) => s.delete(c)),
        contains: (c) => s.has(c),
        toggle: (c, v) => (v === undefined ? (s.has(c) ? s.delete(c) : s.add(c)) : (v ? s.add(c) : s.delete(c))),
      };
    })(),
    // Internal move helper: detaches `child` from its current parent without
    // unregistering its id(s) -- a reparent within the same tick never makes
    // the node unresolvable in the real DOM either. Only the public
    // removeChild/remove (a genuine removal) does that.
    _detach(child) {
      node.children = node.children.filter((c) => c !== child);
      child.parentNode = null;
      return child;
    },
    appendChild(child) {
      if (child.parentNode) child.parentNode._detach(child);
      node.children.push(child);
      node._text = "";
      child.parentNode = node;
      return child;
    },
    insertBefore(child, refNode) {
      if (child.parentNode) child.parentNode._detach(child);
      if (refNode) {
        const idx = node.children.indexOf(refNode);
        if (idx === -1) node.children.push(child);
        else node.children.splice(idx, 0, child);
      } else {
        node.children.push(child);
      }
      child.parentNode = node;
      return child;
    },
    removeChild(child) {
      node._detach(child);
      unregisterIdTree(child);
      return child;
    },
    remove() {
      if (node.parentNode) node.parentNode.removeChild(node);
    },
    setAttribute(k, v) { node._attrs[k] = v; },
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    getContext: () => {
      if (!node._ctx) {
        node._ctx = {
          fillStyle: null,
          calls: [],
          clearRect() {},
          beginPath() {},
          arc() {},
          fill() {},
          moveTo() {},
          lineTo() {},
          stroke() {
            node._ctx.calls.push({ type: "stroke", strokeStyle: node._ctx.strokeStyle });
          },
          fillRect(x, y, w, h) {
            node._ctx.calls.push({ type: "fillRect", fillStyle: node._ctx.fillStyle, x, y, w, h });
          },
        };
      }
      return node._ctx;
    },
  };
  Object.defineProperty(node, "options", {
    // Real <select>.options only ever lists <option> children; filtering
    // matches that instead of assuming a picker's children are all options.
    get() { return node.children.filter((c) => c.tagName === "option"); },
  });
  Object.defineProperty(node, "id", {
    get() { return node._id || ""; },
    // Mirrors real DOM: assigning .id makes the node findable by
    // document.getElementById, same as setting the id attribute. Drop
    // the old mapping first so a reassigned id can't leave a stale entry
    // pointing at this node under its previous id.
    set(v) {
      if (node._id && byId.get(node._id) === node) byId.delete(node._id);
      node._id = v;
      byId.set(v, node);
    },
  });
  Object.defineProperty(node, "textContent", {
    get() { return node.children.length ? node.children.map((c) => c.textContent).join("") : node._text; },
    // `clear(node)` (node.textContent = "") is how the panels empty a mount
    // before rebuilding it -- unregister every id under the discarded
    // subtree so a stale picker/card id can't still resolve via
    // getElementById after its card was rebuilt.
    set(v) {
      for (const c of node.children) unregisterIdTree(c);
      node._text = v == null ? "" : String(v);
      node.children = [];
    },
  });
  Object.defineProperty(node, "innerHTML", {
    get() { return node.children.map(serialize).join("") + (node.children.length ? "" : escapeText(node._text)); },
    set() { /* discipline: never write markup strings; use createElement */ },
  });
  return node;
}

const byId = new Map();
globalThis.document = {
  getElementById: (id) => byId.get(id) ?? (byId.set(id, el("div")), byId.get(id)),
  createElement: (tag) => el(tag),
  createTextNode: (text) => { const n = el("#text"); n.textContent = text; return n; },
  body: el("body"),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.matchMedia = () => ({ matches: true }); // reduced motion: no timers
globalThis.addEventListener = () => {};

class FakeSocket {
  constructor(url) {
    FakeSocket.instances.push(this);
    this.url = url;
    this.sent = [];
    this.readyState = 1;
    FakeSocket.OPEN = 1;
  }
  send(s) { this.sent.push(JSON.parse(s)); }
}
FakeSocket.instances = [];
globalThis.WebSocket = FakeSocket;

module.exports = { el, byId, FakeSocket };
