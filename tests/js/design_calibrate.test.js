"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const DESIGNS = [
  { name: "tuneshroom", state: "draft", error: null },
];

const SESSIONS = [
  { session: "s1", labels: { tap: 3, shake: 1 } },
];

const TOML_FIXTURE = [
  '[[event_triggers]]',
  'name = "tap"',
  'description = "a single or double tap"',
  '  [event_triggers.thresholds]',
  '  peak_g = 2.0',
  '  window_ms = 200',
  '  double_ms = 400',
  '',
  '[[event_triggers]]',
  'name = "shake"',
  'description = "a shake gesture"',
  '  [event_triggers.thresholds]',
  '  peak_g = 2.0',
  '  window_ms = 200',
].join("\n");

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
  const design = await import("../../console/static/design.js");
  const forms = await import("../../console/static/design_forms.js");
  design.init();
  design.initBench();
  design.initCalibrate();
  forms.initForms();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  // -- renderCaptures: one row per session/label pair, click selects -------
  const mount = document.getElementById("calSessions");
  const picked = [];
  design.renderCaptures(mount, SESSIONS, (pick) => picked.push(pick));
  const rows = mount.children;
  assert.strictEqual(rows.length, 2);
  assert.strictEqual(rows[0].textContent, "s1 / tap (3)");
  assert.strictEqual(rows[1].textContent, "s1 / shake (1)");
  rows[0].onclick();
  assert.deepStrictEqual(picked, [{ session: "s1", label: "tap" }]);

  // -- applyProposal: replace values, add missing key, insert/replace the
  // provenance comment; unknown trigger leaves text unchanged -------------
  const proposal = { peak_g: 1.6, window_ms: 180, double_ms: 350 };
  const out = design.applyProposal(TOML_FIXTURE, "tap", proposal, "s1 on 2026-08-31");
  const expected = [
    '[[event_triggers]]',
    'name = "tap"',
    'description = "a single or double tap"',
    '  # calibrated from s1 on 2026-08-31',
    '  [event_triggers.thresholds]',
    '  peak_g = 1.6',
    '  window_ms = 180',
    '  double_ms = 350',
    '',
    '[[event_triggers]]',
    'name = "shake"',
    'description = "a shake gesture"',
    '  [event_triggers.thresholds]',
    '  peak_g = 2.0',
    '  window_ms = 200',
  ].join("\n");
  assert.strictEqual(out, expected);

  // shake block (second block) must be untouched by the tap proposal.
  const shakeBlockOut = out.split("\n").slice(9);
  const shakeBlockIn = TOML_FIXTURE.split("\n").slice(8);
  assert.deepStrictEqual(shakeBlockOut, shakeBlockIn);

  // Re-applying with a missing double_ms proposal adds it to shake, which
  // has no prior double_ms line.
  const shakeProposal = { peak_g: 3.0, window_ms: 220, double_ms: 500 };
  const shakeOut = design.applyProposal(TOML_FIXTURE, "shake", shakeProposal, "s1 on 2026-08-31");
  const expectedShake = [
    '[[event_triggers]]',
    'name = "tap"',
    'description = "a single or double tap"',
    '  [event_triggers.thresholds]',
    '  peak_g = 2.0',
    '  window_ms = 200',
    '  double_ms = 400',
    '',
    '[[event_triggers]]',
    'name = "shake"',
    'description = "a shake gesture"',
    '  # calibrated from s1 on 2026-08-31',
    '  [event_triggers.thresholds]',
    '  peak_g = 3',
    '  window_ms = 220',
    '  double_ms = 500',
  ].join("\n");
  assert.strictEqual(shakeOut, expectedShake);

  // Replacing an existing "# calibrated from" comment overwrites it in place.
  const alreadyCalibrated = TOML_FIXTURE.replace(
    '  [event_triggers.thresholds]\n  peak_g = 2.0\n  window_ms = 200\n  double_ms = 400',
    '  # calibrated from old-session on 2026-01-01\n  [event_triggers.thresholds]\n  peak_g = 2.0\n  window_ms = 200\n  double_ms = 400',
  );
  const recalibrated = design.applyProposal(alreadyCalibrated, "tap", proposal, "s1 on 2026-08-31");
  assert.ok(recalibrated.includes('  # calibrated from s1 on 2026-08-31'));
  assert.ok(!recalibrated.includes("old-session"));
  assert.strictEqual(recalibrated.split("\n").filter((l) => l.includes("# calibrated from")).length, 1);

  // Unknown trigger: text unchanged.
  assert.strictEqual(design.applyProposal(TOML_FIXTURE, "nope", proposal, "s1 on today"), TOML_FIXTURE);

  // -- capture_stats: renders a table and enables the buttons -------------
  send({ event: "snapshot", designs: DESIGNS, design_vocab: { capabilities: [], cue_kinds: [] } });
  design.openDesign({ name: "tuneshroom", state: "draft", text: TOML_FIXTURE, errors: [] });
  forms.rebuild(TOML_FIXTURE);

  const proposeBtn = byId.get("calPropose");
  const replayBtn = byId.get("calReplay");
  assert.strictEqual(proposeBtn.disabled, true);
  assert.strictEqual(replayBtn.disabled, true);

  send({
    event: "capture_stats",
    rows: [{ label: "tap", series: 1, peak_dev_g: 1.6, span_ms: 12.5, spikes: 2 }],
    proposal,
  });
  const statsEl = byId.get("calStats");
  assert.ok(statsEl.textContent.includes("tap"));
  assert.ok(statsEl.textContent.includes("1.6"));
  assert.ok(statsEl.textContent.includes("12.5"));
  assert.ok(statsEl.textContent.includes("2"));
  assert.strictEqual(proposeBtn.disabled, false, "proposal + draft selected -> enabled");
  assert.strictEqual(replayBtn.disabled, true, "no capture row selected yet -> Replay stays disabled");

  // Selecting a capture row (via renderCaptures inside the module) enables
  // Replay once a session is known too.
  send({ event: "captures_listed", sessions: SESSIONS });
  byId.get("calSessions").children[0].onclick();
  assert.strictEqual(replayBtn.disabled, false);

  // -- calPropose applies the proposal to the draft textarea --------------
  proposeBtn.onclick();
  const textArea = byId.get("designText");
  assert.ok(textArea.value.includes("# calibrated from s1 on"));
  assert.ok(textArea.value.includes("peak_g = 1.6"));

  // -- forms panel rebuilds after Propose writes the textarea: the trigger
  // card's peak_g input reflects the new value, not the pre-calibration one.
  const sections = byId.get("formSections");
  const peakInput = findByKey(sections, "trig:tap:peak_g");
  assert.notStrictEqual(peakInput, null);
  assert.strictEqual(Number(peakInput.value), 1.6, "forms panel must rebuild after Propose, not keep stale threshold");

  // -- replay_result: unhides #calPlot and draws the trace -----------------
  const plot = document.getElementById("calPlot");
  send({
    event: "replay_result",
    result: {
      fires: [10, 40],
      peak_dev_g: 1.6,
      spikes: 2,
      isi_ms: [30],
      trace: { t_ms: [0, 10, 20, 30, 40], accel_g: [1.0, 3.0, 1.2, 1.1, 3.4] },
    },
  });
  assert.strictEqual(plot.hidden, false);
  const plotCtx = plot.getContext("2d");
  assert.ok(plotCtx.calls.length > 0, "replay draws something to the plot");

  console.log("design_calibrate.test.js OK");
})();
