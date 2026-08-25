"use strict";
// Run directly: node tests/js/wire_and_shell.test.js
const assert = require("node:assert");

// -- minimal DOM stub ------------------------------------------------------
function el() {
  return {
    children: [], classList: (() => { const s = new Set(); return {
      add: (c) => s.add(c), remove: (c) => s.delete(c),
      contains: (c) => s.has(c), toggle: (c, v) => v ? s.add(c) : s.delete(c),
    }; })(),
    style: {}, dataset: {}, hidden: false,
    textContent: "", innerHTML: "", className: "", value: "",
    appendChild(child) { this.children.push(child); return child; },
    insertBefore(child) { this.children.unshift(child); return child; },
    remove() {}, setAttribute() {}, addEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
    getContext: () => ({ clearRect() {}, beginPath() {}, arc() {},
                         fill() {}, stroke() {}, fillRect() {} }),
  };
}
const byId = new Map();
globalThis.document = {
  getElementById: (id) => byId.get(id) ?? byId.set(id, el()).get(id),
  createElement: () => el(),
  body: el(),
};
globalThis.matchMedia = () => ({ matches: true });   // reduced motion: no timers
globalThis.addEventListener = () => {};

class FakeSocket {
  constructor(url) { FakeSocket.instances.push(this); this.url = url;
    this.sent = []; this.readyState = 1; FakeSocket.OPEN = 1; }
  send(s) { this.sent.push(JSON.parse(s)); }
}
FakeSocket.instances = [];
globalThis.WebSocket = FakeSocket;

(async () => {
  const wire = await import("../../console/static/wire.js");

  // dispatch: two handlers, in order
  const calls = [];
  wire.on("snapshot", (m) => calls.push(["a", m.state]));
  wire.on("snapshot", (m) => calls.push(["b", m.state]));
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  sock.onmessage({ data: JSON.stringify({ event: "snapshot", state: "IDLE" }) });
  assert.deepStrictEqual(calls, [["a", "IDLE"], ["b", "IDLE"]]);

  // send stamps the payload and remembers the source element
  const btn = el();
  wire.send("run", {}, btn);
  assert.deepStrictEqual(sock.sent.at(-1), { command: "run" });
  wire.flashRefusal("run", "invalid transition");
  assert.ok(btn.classList.contains("errflash"));

  // an unknown event is a no-op, never a throw
  sock.onmessage({ data: JSON.stringify({ event: "never_heard_of_it" }) });

  console.log("wire_and_shell: ok");

  // -- confirmTap -----------------------------------------------------------
  const cbtn = el();
  cbtn.textContent = "Abort";
  let confirmed = 0;
  const onConfirm = () => { confirmed += 1; };

  // first tap: arms the button, does not confirm
  wire.confirmTap(cbtn, { armLabel: "Confirm Abort?" }, onConfirm);
  assert.strictEqual(cbtn.dataset.armed, "1");
  assert.strictEqual(cbtn.textContent, "Confirm Abort?");
  assert.strictEqual(confirmed, 0);

  // second tap within the window: confirms and clears armed state
  wire.confirmTap(cbtn, { armLabel: "Confirm Abort?" }, onConfirm);
  assert.strictEqual(confirmed, 1);
  assert.strictEqual(cbtn.dataset.armed, undefined);

  console.log("confirmTap arm/confirm: ok");
  // Timeout-revert half (button reverts to original text if not confirmed
  // within timeoutMs) is implemented but not independently exercised here
  // with real timers; see task-3-report.md for the note.
})().catch((e) => { console.error(e); process.exit(1); });
