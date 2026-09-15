"use strict";
const assert = require("node:assert");
const { FakeSocket } = require("./_dom_stub.js");

// Regression test for the residual gap left by d286f97: fnSignature is
// cached on a successful render but is never invalidated when the mount
// later disappears (Room unload tears down surface.js's whole #roomCard
// subtree, including #functionsMount). If the Room then reloads with the
// same functions list, the signature-match short-circuit fires before
// render() ever runs again, so the freshly (re)mounted #functionsMount
// stays blank.
const TEST_FUNCTIONS = [
  { kind: "scripted", name: "test_fire", description: "Test function",
    target: "DEVICE",
    condition: { name: "admin_fire", description: "Operator fires",
                 source: "admin-manual", verb: null },
    script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 }] },
];

(async () => {
  // Capture the pristine stub before functions.js is imported/used, so it
  // can be restored later to simulate the mount coming back.
  const realGetById = globalThis.document.getElementById;

  const wire = await import("../../console/static/wire.js");
  const functions = await import("../../console/static/functions.js");

  functions.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // STEP 1: mount present from the start. A normal render populates it and
  // caches fnSignature against this exact function list.
  send({ event: "snapshot", state: "RUNNING", loaded_bit: "TestBit",
         roles: [], registration: [],
         devices: [],
         bit_status: {}, functions: TEST_FUNCTIONS,
         room: null });

  assert.ok(functions._cardFor("test_fire"), "test_fire card should render while the mount exists");
  const mountEl = realGetById("functionsMount");
  assert.ok(mountEl.innerHTML.length > 0, "mount should have content after the first render");

  // STEP 2: simulate Room unload. surface.js tears down the whole #roomCard
  // subtree (including #functionsMount) -- clear the mount's content by hand
  // so the DOM stub can't just keep serving this same, still-populated
  // element across the gap and mask the bug with stale content. Then force
  // getElementById to return null for "functionsMount" specifically: the
  // stub auto-vivifies a placeholder for any unseen id instead of returning
  // null (see _dom_stub.js's "Residual gap" comment), so a real null has to
  // be forced by hand, the same technique functions_no_room.test.js uses.
  mountEl.textContent = "";
  globalThis.document.getElementById = (id) =>
    (id === "functionsMount" ? null : realGetById(id));

  // Resend the SAME functions list -- with the mount present this would
  // correctly be a no-change no-op. Confirm it's still safe with the mount
  // gone (this is the call that would otherwise be a no-change signature
  // match).
  let threw = false;
  try {
    send({ event: "functions_changed", functions: TEST_FUNCTIONS });
  } catch (e) {
    threw = true;
  }
  assert.ok(!threw, "should not throw while the mount is missing");

  // STEP 3: simulate Room reload -- the mount is reachable again (same stub
  // element, now empty). Resend the identical functions list. fnSignature
  // was cached against this exact list before the mount disappeared and,
  // under the residual gap, is never invalidated -- so today this repaint
  // is silently skipped and the mount stays blank forever.
  globalThis.document.getElementById = realGetById;
  send({ event: "functions_changed", functions: TEST_FUNCTIONS });

  assert.ok(mountEl.innerHTML.length > 0,
    "mount should be repainted after remounting, even though the function list didn't change");
  assert.ok(functions._cardFor("test_fire"), "test_fire card should exist again after remount");

  console.log("functions_stale_signature_on_remount: ok");
})().catch((e) => { console.error(e); process.exit(1); });
