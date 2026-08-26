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

function escapeText(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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
    appendChild(child) {
      if (child.parentNode) child.parentNode.removeChild(child);
      node.children.push(child);
      node._text = "";
      child.parentNode = node;
      return child;
    },
    insertBefore(child, refNode) {
      if (child.parentNode) child.parentNode.removeChild(child);
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
      node.children = node.children.filter((c) => c !== child);
      child.parentNode = null;
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
    getContext: () => ({ clearRect() {}, beginPath() {}, arc() {}, fill() {}, stroke() {}, fillRect() {} }),
  };
  Object.defineProperty(node, "options", {
    get() { return node.children; },
  });
  Object.defineProperty(node, "id", {
    get() { return node._id || ""; },
    // Mirrors real DOM: assigning .id makes the node findable by
    // document.getElementById, same as setting the id attribute.
    set(v) { node._id = v; byId.set(v, node); },
  });
  Object.defineProperty(node, "textContent", {
    get() { return node.children.length ? node.children.map((c) => c.textContent).join("") : node._text; },
    set(v) { node._text = v == null ? "" : String(v); node.children = []; },
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
