// Design panel: list of declared designs (draft/published pairs) rendered
// into #designList, a raw-TOML editor (#designText/#designErrors), and the
// Save/Publish/Clone actions. Wire shapes are Task 5's verbatim: commands
// list_designs/get_design/save_design/publish_design/clone_design; events
// designs_listed/design/designs_changed (the snapshot carries the same rows
// under "designs").
//
// Selecting a published design and hitting Save writes a draft of the same
// name (the draft-shadowing edit flow) -- the client always sends
// save_design with the selection's name; the server decides that a save on
// a published name lands as its draft.
import * as wire from "./wire.js";

let lastDesigns = [];      // last-seen designs_listed/designs_changed/snapshot rows
let current = null;        // {name, state} of the open design, or null

function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// -------------------------------------------------------------- rendering

// Pure renderer: one row per design, "<name> [draft|published]", plus an
// error badge when the entry's error is non-null. `onSelect(design)` fires
// on row click.
export function renderDesigns(listEl, designs, onSelect) {
  clear(listEl);
  for (const design of designs) {
    const row = mk("div", "design-row");
    row.appendChild(mk("span", "name", `${design.name} [${design.state}]`));
    if (design.error) row.appendChild(mk("span", "chip rose err", "error"));
    row.onclick = () => onSelect(design);
    listEl.appendChild(row);
  }
}

function onRowSelect(design) {
  wire.send("get_design", { state: design.state, name: design.name });
}

function render() {
  const listEl = document.getElementById("designList");
  renderDesigns(listEl, lastDesigns, onRowSelect);
  if (current) {
    for (const [i, design] of lastDesigns.entries()) {
      if (design.name === current.name && design.state === current.state) {
        listEl.children[i].classList.add("selected");
      }
    }
  }
}

function onDesignsChanged(designs) {
  lastDesigns = designs || [];
  render();
}

// Accessor for the currently-open design selection ({name, state}, or null
// when nothing is open) -- used by design_forms.js to gate form rendering
// without duplicating design.js's selection tracking.
export function getSelection() {
  return current;
}

// Fills the editor from a `design` event: text, errors, and remembers the
// open selection so Save/Publish/Clone know what they're acting on.
export function openDesign(msg) {
  current = { name: msg.name, state: msg.state };
  document.getElementById("designText").value = msg.text;
  const errEl = document.getElementById("designErrors");
  clear(errEl);
  for (const error of (msg.errors || [])) {
    errEl.appendChild(mk("div", "err", error));
  }
  render();
}

// ---------------------------------------------------------------------- init

export function init() {
  const saveBtn = document.getElementById("designSave");
  const publishBtn = document.getElementById("designPublish");
  const cloneBtn = document.getElementById("designClone");

  saveBtn.onclick = () => {
    if (!current) return;
    const text = document.getElementById("designText").value;
    wire.send("save_design", { name: current.name, text }, saveBtn);
  };

  publishBtn.onclick = () => {
    if (!current) return;
    wire.send("publish_design", { name: current.name }, publishBtn);
  };

  cloneBtn.onclick = () => {
    if (!current) return;
    const newName = window.prompt("Clone as:");
    if (!newName) return;
    wire.send("clone_design", {
      source_state: current.state,
      source_name: current.name,
      new_name: newName,
    }, cloneBtn);
  };

  wire.on("snapshot", (m) => onDesignsChanged(m.designs));
  wire.on("designs_listed", (m) => onDesignsChanged(m.designs));
  wire.on("designs_changed", (m) => onDesignsChanged(m.designs));
  wire.on("design", (m) => openDesign(m));
}

// ----------------------------------------------------------------- bench
//
// Design bench: a headless simulator run against the currently-open design
// selection (`current`, above). Simulate starts it, Stop tears it down;
// while running, bench_started's declared functions render as fire buttons
// and bench_frame paints the strip preview canvas. The tilt lane sends a
// synthetic CC (status 176, data1 74) throttled to at most one send per
// 100ms so a fast drag doesn't flood the wire.

const BENCH_TILT_THROTTLE_MS = 100;
let lastTiltSendAt = 0;

// Pure renderer: one button per declared function, labeled by name with a
// title/tooltip of its description and a builtin/instrument class per its
// `source`. Click sends {command: "bench_fire", name} via `send`.
export function renderBenchFunctions(el, functions, send) {
  clear(el);
  for (const fn of functions) {
    const btn = mk("button", `btn ${fn.source === "builtin" ? "builtin" : "instrument"}`, fn.name);
    btn.setAttribute("title", fn.description || "");
    btn.onclick = () => send({ command: "bench_fire", name: fn.name });
    el.appendChild(btn);
  }
}

// Paints one filled rect per pixel from a flat GRB channel list, matching
// surface.js's onRoomFrame color handling (channels[i*3]=g, +1=r, +2=b).
export function paintBenchFrame(canvas, channels) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width || canvas.clientWidth || 0;
  const h = canvas.height || canvas.clientHeight || 0;
  ctx.clearRect(0, 0, w, h);
  const n = Math.floor(channels.length / 3);
  if (n === 0) return;
  const pitch = w / n;
  for (let i = 0; i < n; i++) {
    const g = channels[i * 3] || 0;
    const r = channels[i * 3 + 1] || 0;
    const b = channels[i * 3 + 2] || 0;
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(pitch * i, 0, pitch, h);
  }
}

function onBenchStarted(functions) {
  const mount = document.getElementById("benchFunctions");
  renderBenchFunctions(mount, functions || [], (cmd) => wire.send(cmd.command, { name: cmd.name }));
  document.getElementById("benchStop").disabled = false;
  document.getElementById("benchTilt").disabled = false;
}

function onBenchFrame(channels) {
  paintBenchFrame(document.getElementById("benchCanvas"), channels || []);
}

export function initBench() {
  const startBtn = document.getElementById("benchStart");
  const stopBtn = document.getElementById("benchStop");
  const tilt = document.getElementById("benchTilt");

  startBtn.onclick = () => {
    if (!current) return;
    wire.send("bench_start", { state: current.state, name: current.name }, startBtn);
  };

  stopBtn.onclick = () => {
    wire.send("bench_stop", {}, stopBtn);
    stopBtn.disabled = true;
    tilt.disabled = true;
    clear(document.getElementById("benchFunctions"));
  };

  tilt.oninput = () => {
    const now = Date.now();
    if (now - lastTiltSendAt < BENCH_TILT_THROTTLE_MS) return;
    lastTiltSendAt = now;
    wire.send("bench_lane", {
      verb: "tilt",
      value: Number(tilt.value) / 100,
      status: 176,
      data1: 74,
    });
  };

  wire.on("bench_started", (m) => onBenchStarted(m.functions));
  wire.on("bench_frame", (m) => onBenchFrame(m.channels));
}

// -------------------------------------------------------------- calibrate
//
// Captures browser + threshold calibration: browse recorded gesture
// captures for the open design, review per-capture stats and a suggested
// [event_triggers.thresholds] proposal, apply it to the draft text
// in-place (the operator reviews and hits the existing Save), and replay a
// picked capture through the design's trigger evaluator to plot the trace.

let lastSessions = [];       // last captures_listed sessions
let selectedCapture = null;  // {session, label} of the picked capture row
let lastStatsRows = [];      // last capture_stats rows
let lastProposal = null;     // last capture_stats proposal (or null)

// Pure renderer: one row per session/label pair, "<session> / <label>
// (<count>)". Click fires onPick({session, label}).
export function renderCaptures(el, sessions, onPick) {
  clear(el);
  for (const s of sessions) {
    for (const [label, count] of Object.entries(s.labels || {})) {
      const row = mk("div", "capture-row", `${s.session} / ${label} (${count})`);
      row.onclick = () => onPick({ session: s.session, label });
      el.appendChild(row);
    }
  }
}

// Locates the `[[event_triggers]]` block whose `name = "<trigger>"` line
// matches, by scanning from that line to the next `[[` header (or EOF).
// Returns {start, end} line indices (end exclusive), or null if the
// trigger isn't declared.
export function findTriggerBlock(lines, trigger) {
  const nameRe = new RegExp(`^\\s*name\\s*=\\s*"${trigger}"\\s*$`);
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    if (nameRe.test(lines[i])) { start = i; break; }
  }
  if (start === -1) return null;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^\[\[/.test(lines[i])) { end = i; break; }
  }
  return { start, end };
}

const THRESHOLD_KEYS = ["peak_g", "window_ms", "double_ms"];

// Pure string -> string transform. Inside the trigger's block, replaces the
// numeric values of peak_g/window_ms/double_ms under its
// [event_triggers.thresholds] table with the proposal's values (adding a
// missing key line, dropping nothing), and inserts a
// "# calibrated from <provenance>" comment directly above the thresholds
// header, replacing any previous one there. Returns the text unchanged if
// the trigger isn't found.
export function applyProposal(text, trigger, proposal, provenance) {
  const lines = text.split("\n");
  const block = findTriggerBlock(lines, trigger);
  if (!block) return text;
  let { start, end } = block;

  let thresholdsIdx = -1;
  for (let i = start; i < end; i++) {
    if (/^\s*\[event_triggers\.thresholds\]\s*$/.test(lines[i])) { thresholdsIdx = i; break; }
  }
  if (thresholdsIdx === -1) return text;

  const commentLine = `  # calibrated from ${provenance}`;
  if (thresholdsIdx > start && /^\s*# calibrated from /.test(lines[thresholdsIdx - 1])) {
    lines[thresholdsIdx - 1] = commentLine;
  } else {
    lines.splice(thresholdsIdx, 0, commentLine);
    thresholdsIdx += 1;
    end += 1;
  }

  const presentKeys = new Set();
  let lastBodyIdx = thresholdsIdx;
  for (let i = thresholdsIdx + 1; i < end; i++) {
    const m = lines[i].match(/^(\s*)(peak_g|window_ms|double_ms)\s*=\s*.+$/);
    if (m) {
      const key = m[2];
      presentKeys.add(key);
      if (Object.prototype.hasOwnProperty.call(proposal, key)) {
        lines[i] = `${m[1]}${key} = ${proposal[key]}`;
      }
    }
    if (lines[i].trim() !== "") lastBodyIdx = i;
  }

  const missing = THRESHOLD_KEYS.filter((k) =>
    Object.prototype.hasOwnProperty.call(proposal, k) && !presentKeys.has(k));
  if (missing.length) {
    lines.splice(lastBodyIdx + 1, 0, ...missing.map((k) => `  ${k} = ${proposal[k]}`));
  }

  return lines.join("\n");
}

function calButtonsEnabled() {
  document.getElementById("calPropose").disabled =
    !(lastProposal && current && current.state === "draft");
  document.getElementById("calReplay").disabled = !(selectedCapture && selectedCapture.session);
}

function onCapturesListed(sessions) {
  lastSessions = sessions || [];
  renderCaptures(document.getElementById("calSessions"), lastSessions, (pick) => {
    selectedCapture = pick;
    calButtonsEnabled();
    wire.send("capture_stats", { session: pick.session, label: pick.label });
  });
}

function renderStats(el, rows) {
  clear(el);
  const table = mk("table", "cal-stats");
  const header = mk("tr");
  for (const col of ["label", "series", "peak_dev_g", "span_ms", "spikes"]) {
    header.appendChild(mk("th", null, col));
  }
  table.appendChild(header);
  for (const row of rows) {
    const tr = mk("tr");
    for (const col of ["label", "series", "peak_dev_g", "span_ms", "spikes"]) {
      tr.appendChild(mk("td", null, String(row[col])));
    }
    table.appendChild(tr);
  }
  el.appendChild(table);
}

function onCaptureStats(rows, proposal) {
  lastStatsRows = rows || [];
  lastProposal = proposal || null;
  renderStats(document.getElementById("calStats"), lastStatsRows);
  calButtonsEnabled();
}

// Draws accel_g over t_ms as a polyline, a horizontal line at the draft
// trigger's peak_g threshold, and one vertical tick per fire.
function drawReplay(canvas, result, triggerPeakG) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width || canvas.clientWidth || 0;
  const h = canvas.height || canvas.clientHeight || 0;
  ctx.clearRect(0, 0, w, h);
  const trace = result.trace || { t_ms: [], accel_g: [] };
  const tMs = trace.t_ms || [];
  const accel = trace.accel_g || [];
  const maxT = Math.max(1, ...tMs);
  const maxG = Math.max(1, triggerPeakG || 0, ...accel);
  const x = (t) => (t / maxT) * w;
  const y = (g) => h - (g / maxG) * h;

  ctx.strokeStyle = "rgba(200,220,255,0.9)";
  ctx.beginPath();
  tMs.forEach((t, i) => {
    const px = x(t);
    const py = y(accel[i] || 0);
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  });
  ctx.stroke();

  if (triggerPeakG != null) {
    ctx.strokeStyle = "rgba(255,180,80,0.8)";
    ctx.beginPath();
    ctx.moveTo(0, y(triggerPeakG));
    ctx.lineTo(w, y(triggerPeakG));
    ctx.stroke();
  }

  ctx.fillStyle = "rgba(255,80,80,0.9)";
  for (const fireT of (result.fires || [])) {
    ctx.fillRect(x(fireT) - 1, 0, 2, h);
  }
}

// Parses the draft trigger's peak_g out of the textarea via
// findTriggerBlock, or null if not found.
function draftTriggerPeakG(trigger) {
  const text = document.getElementById("designText").value || "";
  const lines = text.split("\n");
  const block = findTriggerBlock(lines, trigger);
  if (!block) return null;
  for (let i = block.start; i < block.end; i++) {
    const m = lines[i].match(/^\s*peak_g\s*=\s*([\d.]+)\s*$/);
    if (m) return Number(m[1]);
  }
  return null;
}

function onReplayResult(result) {
  const canvas = document.getElementById("calPlot");
  canvas.hidden = false;
  const trigger = canvas.dataset.trigger || "";
  drawReplay(canvas, result || {}, draftTriggerPeakG(trigger));
}

export function initCalibrate() {
  const refreshBtn = document.getElementById("calRefresh");
  const proposeBtn = document.getElementById("calPropose");
  const replayBtn = document.getElementById("calReplay");

  refreshBtn.onclick = () => wire.send("list_captures", {}, refreshBtn);

  proposeBtn.onclick = () => {
    if (!current || !lastProposal || !selectedCapture) return;
    const provenance = `${selectedCapture.session} on ${new Date().toISOString().slice(0, 10)}`;
    const textEl = document.getElementById("designText");
    textEl.value = applyProposal(textEl.value, selectedCapture.label, lastProposal, provenance);
  };

  replayBtn.onclick = () => {
    if (!current || !selectedCapture) return;
    const trigger = window.prompt("Trigger name", "tap");
    if (!trigger) return;
    const row = lastStatsRows.find((r) => r.label === selectedCapture.label) || lastStatsRows[0];
    const series = row ? row.series : 1;
    document.getElementById("calPlot").dataset.trigger = trigger;
    wire.send("replay_trace", {
      state: current.state,
      name: current.name,
      trigger,
      session: selectedCapture.session,
      label: selectedCapture.label,
      series,
    }, replayBtn);
  };

  wire.on("captures_listed", (m) => onCapturesListed(m.sessions));
  wire.on("capture_stats", (m) => onCaptureStats(m.rows, m.proposal));
  wire.on("replay_result", (m) => onReplayResult(m.result));

  calButtonsEnabled();
}
