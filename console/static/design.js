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
