"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

// Test functions that will be sent in the snapshot
const TEST_FUNCTIONS = [
  { kind: "scripted", name: "test_fire", description: "Test function",
    target: "DEVICE",
    condition: { name: "admin_fire", description: "Operator fires",
                 source: "admin-manual", verb: null },
    script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 }] },
];

(async () => {
  // Before importing wire and functions, wrap document.getElementById
  // to return null specifically for "functionsMount", simulating the condition
  // where the mount doesn't exist yet (room is null, so surface.js never created it).
  const realGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) =>
    (id === "functionsMount" ? null : realGetById(id));

  const wire = await import("../../console/static/wire.js");
  const functions = await import("../../console/static/functions.js");

  functions.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // TEST 1: Send a snapshot with room: null and non-empty functions.
  // The mount doesn't exist yet (surface.js never created it because room is null),
  // so render() returns false, fnSignature is NOT cached, and no crash occurs.
  let threw = false;
  try {
    send({ event: "snapshot", state: "RUNNING", loaded_bit: "TestBit",
           roles: [], registration: [],
           devices: [],
           bit_status: {}, functions: TEST_FUNCTIONS,
           room: null });
  } catch (e) {
    threw = true;
  }
  assert.ok(!threw, "should not throw when mount is missing");

  // At this point, render() returned false (mount was null), so _cardFor()
  // should return undefined because no actual rendering happened.
  assert.strictEqual(functions._cardFor("test_fire"), undefined,
    "no card should exist yet (render was skipped)");

  // TEST 2: Restore document.getElementById to normal behavior,
  // then manually create the functionsMount element so it exists in the DOM.
  globalThis.document.getElementById = realGetById;
  const mountEl = realGetById("functionsMount");
  // At this point mountEl will be an auto-vivified element from the stub.
  assert.ok(mountEl, "mount element should exist");

  // TEST 3: CRITICAL REGRESSION TEST: Send functions_changed with the SAME
  // functions list. Without the corrected fix, fnSignature would have been
  // cached on the first (no-op) render, so this identical list would trigger
  // signature-gating and return early, leaving the mount empty. With the fix,
  // render() returned false the first time (so fnSignature was NOT cached),
  // so this message triggers a real render.
  send({ event: "functions_changed", functions: TEST_FUNCTIONS });

  // Confirm that the function card now exists and the mount is non-empty.
  // This is the exact regression the signature-gating fix prevents.
  const card = functions._cardFor("test_fire");
  assert.ok(card, "test_fire card should now exist even with identical function list");
  assert.strictEqual(card._descText, "Test function", "card should render correctly");
  assert.ok(mountEl.innerHTML.length > 0, "mount should have content");

  // Secondary check: send a different list to verify rendering still works normally.
  const DIFFERENT_FUNCTIONS = [
    { kind: "scripted", name: "test_fire", description: "Test function (updated)",
      target: "ROOM",
      condition: { name: "admin_fire", description: "Operator fires",
                   source: "admin-manual", verb: null },
      script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 }] },
    { kind: "scripted", name: "another_fire", description: "Another function",
      target: "DEVICE",
      condition: { name: "admin_fire", description: "Operator fires",
                   source: "admin-manual", verb: null },
      script: [{ offset: 0.0, kind: "light", dev: "@target", status: 176, data1: 70, data2: 81 }] },
  ];
  send({ event: "functions_changed", functions: DIFFERENT_FUNCTIONS });

  // Confirm both cards render with the new list.
  const updatedCard = functions._cardFor("test_fire");
  assert.ok(updatedCard, "test_fire card should exist after signature change");
  assert.strictEqual(updatedCard._descText, "Test function (updated)", "card should update");

  const newCard = functions._cardFor("another_fire");
  assert.ok(newCard, "another_fire card should exist");
  assert.strictEqual(newCard._descText, "Another function", "new card should render");

  console.log("functions_no_room: ok");
})().catch((e) => { console.error(e); process.exit(1); });
