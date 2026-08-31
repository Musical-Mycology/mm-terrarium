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

  // timeout-revert: click once to arm, then let the timer fire with no
  // second click -- button should revert and onConfirm must NOT fire.
  {
    const realSetTimeout = globalThis.setTimeout;
    let capturedFn = null;
    let capturedMs = null;
    globalThis.setTimeout = (fn, ms) => { capturedFn = fn; capturedMs = ms; return 0; };

    const tbtn = el();
    tbtn.textContent = "Release";
    let tconfirmed = 0;
    wire.confirmTap(tbtn, { armLabel: "Confirm Release?", timeoutMs: 4000 }, () => { tconfirmed += 1; });

    assert.strictEqual(tbtn.dataset.armed, "1");
    assert.strictEqual(tbtn.textContent, "Confirm Release?");
    assert.strictEqual(capturedMs, 4000);
    assert.ok(typeof capturedFn === "function");

    globalThis.setTimeout = realSetTimeout;

    // simulate the timeout firing with no second click in between
    capturedFn();

    assert.strictEqual(tbtn.dataset.armed, undefined);
    assert.strictEqual(tbtn.textContent, "Release");
    assert.strictEqual(tconfirmed, 0);

    console.log("confirmTap timeout-revert: ok");
  }

  // timeout-revert guard: a confirm that lands before the timer fires
  // clears dataset.armed, so the (now-stale) timer callback becomes a
  // no-op -- it must not revert the button text or double-fire onConfirm.
  {
    const realSetTimeout = globalThis.setTimeout;
    let capturedFn = null;
    globalThis.setTimeout = (fn) => { capturedFn = fn; return 0; };

    const gbtn = el();
    gbtn.textContent = "Release";
    let gconfirmed = 0;
    wire.confirmTap(gbtn, { armLabel: "Confirm Release?" }, () => { gconfirmed += 1; });
    globalThis.setTimeout = realSetTimeout;

    // second tap arrives before the timer would have fired
    wire.confirmTap(gbtn, { armLabel: "Confirm Release?" }, () => { gconfirmed += 1; });
    assert.strictEqual(gconfirmed, 1);
    assert.strictEqual(gbtn.dataset.armed, undefined);

    // now the stale timer fires -- must be a no-op (guard checks armed==="1")
    capturedFn();
    assert.strictEqual(gconfirmed, 1);
    assert.strictEqual(gbtn.dataset.armed, undefined);

    console.log("confirmTap stale-timer-after-confirm: ok");
  }

  const shell = await import("../../console/static/shell.js");
  // view switcher: exactly one visible view at a time
  shell.showView("log");
  assert.strictEqual(byId.get("viewLive").hidden, true);
  assert.strictEqual(byId.get("viewLog").hidden, false);
  assert.ok(byId.get("navLog").className.includes("active"));
  assert.ok(!byId.get("navLive").className.includes("active"));
  shell.showView("live");
  assert.strictEqual(byId.get("viewLive").hidden, false);
  assert.strictEqual(byId.get("viewLog").hidden, true);
  // Room nav label tracks the active room
  shell.paintRoomNav([{ name: "TEST", active: true }]);
  assert.strictEqual(byId.get("navRoom").textContent, "Room: TEST");
  shell.paintRoomNav([{ name: "TEST", active: false }]);
  assert.strictEqual(byId.get("navRoom").textContent, "Room: none");

  console.log("shell view switcher: ok");
})().catch((e) => { console.error(e); process.exit(1); });
