// Functions panel: compact cards rendered into #functionsMount (created once
// by surface.js, outside its per-fixture rebuild path). One card per
// Bit-declared function: name, target chip, description, condition line,
// collapsed script <details>, a bottom-pinned action row (device picker +
// Fire for DEVICE targets, Fire only for ROOM targets), and a last-fired
// line.
//
// Rendering discipline (rule 1): the card list is rebuilt ONLY when the
// declared function table's signature actually changes -- `devices_changed`
// fires far more often than `functions_changed` and must never cause a
// rebuild, only a picker refill (which preserves the operator's current
// selection). `lastFired` is tracked outside the signature-gated rebuild
// path so it survives any card rebuild -- a function that already fired
// still shows its fired state after its card is recreated.
import * as wire from "./wire.js";

let fnSignature = null;              // JSON of the last-rendered declaration
const lastFired = {};                // function name -> its last fire record (survives rebuilds)
let fnDevices = [];                  // {dev, muted} offered by DEVICE/SURFACE pickers
let currentDeviceTargets = new Map(); // name -> {target, fn} for rendered SURFACE/DEVICE pickers
const cardByName = new Map();        // function name -> its card element (test hook)
const ROOM_OPTION = "@room";

// Instrument-compatibility data, carried on `snapshot`/`functions_changed`
// (Task 7): instrument name -> [scripted function views]; dev/room-option ->
// instrument name; instrument name -> [builtin function names]. All default
// {} so a Bit-less or instrument-less snapshot still renders cleanly.
let instrumentFunctions = {};
let surfaceInstruments = {};
let builtinsMap = {};
const BUILTIN_NAMES = new Set(["flash", "stop", "ping"]);

let diagRowEl = null;                // Diagnostics row, built once, reused across rebuilds
let diagPicker = null;
const diagButtons = {};              // "flash"/"stop"/"ping" -> button element

function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// -------------------------------------------------------------- test hooks

export function _cardFor(name) {
  return cardByName.get(name);
}

export function _diagRow() {
  return diagRowEl;
}

export function _diagPicker() {
  return diagPicker;
}

export function _diagButton(name) {
  return diagButtons[name];
}

// ---------------------------------------------------------- device pickers

// Ported verbatim from the pre-redesign functions.js: preserves the
// operator's current selection when the live device list changes under it,
// falling back to the first offered device only when the previous
// selection is no longer available.
function fillDevicePicker(picker, withRoom) {
  if (!picker) return;
  const previous = picker.value;
  clear(picker);
  const values = [];
  if (withRoom) {
    const option = document.createElement("option");
    option.value = ROOM_OPTION;
    option.textContent = "Room";
    picker.appendChild(option);
    values.push(ROOM_OPTION);
  }
  for (const { dev, muted } of fnDevices) {
    const option = document.createElement("option");
    option.value = dev;
    option.textContent = muted ? `${dev} (muted)` : dev;
    picker.appendChild(option);
    values.push(dev);
  }
  if (values.indexOf(previous) >= 0) picker.value = previous;
  else if (values.length) picker.value = values[0];
}

function onDevicesChanged(devices) {
  fnDevices = (devices || []).map((d) => ({ dev: d.dev, muted: !!d.muted }));
  if (diagPicker) {
    fillDevicePicker(diagPicker, true);
    refreshDiagButtons();
  }
  for (const [name, info] of currentDeviceTargets) {
    const picker = document.getElementById("functionDev_" + name);
    fillDevicePicker(picker, info.target === "SURFACE");
    refreshCardCompatibility(info.fn, picker, cardByName.get(name));
  }
}

// -------------------------------------------------------- instrument compat

// A scripted function is compatible with a surface when: the Bit supplied
// its own script (script.length > 0 -- the Bit script rides above whatever
// instrument is present, per the brief), OR its name is a reserved builtin
// name (flash/stop/ping -- every instrument answers those), OR the surface's
// bound instrument declares a same-named scripted function.
function isCompatible(fn, surfaceValue) {
  if (fn.script && fn.script.length) return true;
  if (BUILTIN_NAMES.has(fn.name)) return true;
  const instrumentName = surfaceInstruments[surfaceValue];
  if (!instrumentName) return false;
  const fns = instrumentFunctions[instrumentName] || [];
  return fns.some((f) => f.name === fn.name);
}

// The description shown for a card at its currently-selected surface: the
// resolved instrument's own function description when it declares one,
// falling back to the Bit's own description otherwise.
function resolvedDescription(fn, surfaceValue) {
  const instrumentName = surfaceInstruments[surfaceValue];
  if (instrumentName) {
    const fns = instrumentFunctions[instrumentName] || [];
    const match = fns.find((f) => f.name === fn.name);
    if (match) return match.description;
  }
  return fn.description;
}

function refreshCardCompatibility(fn, picker, card) {
  if (picker) {
    for (const option of picker.options) {
      option.disabled = !isCompatible(fn, option.value);
    }
  }
  if (card && card._descEl) {
    card._descEl.textContent = resolvedDescription(fn, picker ? picker.value : null);
  }
}

function refreshAllCardCompatibility() {
  refreshDiagButtons();
  for (const [name, info] of currentDeviceTargets) {
    refreshCardCompatibility(info.fn, document.getElementById("functionDev_" + name),
      cardByName.get(name));
  }
}

function updateInstrumentData(m) {
  instrumentFunctions = (m && m.instrument_functions) || {};
  surfaceInstruments = (m && m.surface_instruments) || {};
  builtinsMap = (m && m.builtins) || {};
  refreshAllCardCompatibility();
}

// ------------------------------------------------------------ diagnostics

function refreshDiagButtons() {
  if (!diagPicker) return;
  const instrumentName = surfaceInstruments[diagPicker.value];
  const names = instrumentName ? (builtinsMap[instrumentName] || []) : [];
  for (const name of ["flash", "stop", "ping"]) {
    diagButtons[name].disabled = !names.includes(name);
  }
}

function buildDiagRow() {
  const row = mk("div", "fn diagrow");

  const head = mk("div", "fnhead");
  head.appendChild(mk("h3", null, "Diagnostics"));
  row.appendChild(head);

  const bar = mk("div", "firerow");
  diagPicker = document.createElement("select");
  bar.appendChild(diagPicker);
  fillDevicePicker(diagPicker, true);
  diagPicker.onchange = refreshDiagButtons;

  bar.appendChild(mk("span", "grow"));
  for (const name of ["flash", "stop", "ping"]) {
    const btn = mk("button", "btn", name[0].toUpperCase() + name.slice(1));
    btn.onclick = () => wire.send("fire_function", { name, dev: diagPicker.value }, btn);
    diagButtons[name] = btn;
    bar.appendChild(btn);
  }
  row.appendChild(bar);

  refreshDiagButtons();
  return row;
}

function ensureDiagRow() {
  if (!diagRowEl) diagRowEl = buildDiagRow();
  return diagRowEl;
}

// -------------------------------------------------------------- rendering

function stepText(step) {
  const offset = "+" + Number(step.offset).toFixed(2) + "s";
  if (step.kind === "play") {
    return `${offset}   ${step.dev}   play "${step.name}"`;
  }
  return `${offset}   ${step.dev}   cc:${step.data1} = ${step.data2}`;
}

function maxOffset(script) {
  let max = 0;
  for (const step of script) max = Math.max(max, Number(step.offset) || 0);
  return max;
}

function firedText(fired) {
  const where = fired.devs && fired.devs.length ? fired.devs.join(", ") : "nothing";
  return `${fired.fired_by} → ${where} (${fired.steps} cue${fired.steps === 1 ? "" : "s"})`;
}

function applyFired(card, line, fired) {
  clear(line);
  card.classList.remove("fired", "fired-admin");
  if (!fired) {
    line.textContent = "never fired";
    return;
  }
  const isAdmin = fired.fired_by === "admin-manual";
  card.classList.add(isAdmin ? "fired-admin" : "fired");
  line.appendChild(document.createTextNode(firedText(fired)));
  if (isAdmin) {
    line.appendChild(document.createTextNode(" "));
    line.appendChild(mk("span", "admin", "Admin manual"));
  }
}

function laneText(lane) {
  return `${lane.dev}   status:${lane.status}   cc:${lane.data1}`;
}

function outputText(output) {
  return `${output.dev}   status:${output.status}   cc:${output.data1}   `
    + `[${output.out_lo}, ${output.out_hi}]   ${output.mode}`;
}

function buildGeneratorCard(fn) {
  const card = document.createElement("div");
  card.className = "fn";

  const head = mk("div", "fnhead");
  head.appendChild(mk("h3", null, fn.name));
  head.appendChild(mk("span", "grow"));
  head.appendChild(mk("span", "chip dim kind", "generator"));
  card.appendChild(head);

  card.appendChild(mk("p", "desc", fn.description));

  const decl = mk("div", "script mono");
  decl.appendChild(mk("div", "step", laneText(fn.lane)));
  decl.appendChild(mk("div", "step",
    `waveform:${fn.waveform}   period:${fn.period}s   [${fn.lo}, ${fn.hi}]`));
  card.appendChild(decl);

  return card;
}

function buildStreamCard(fn) {
  const card = document.createElement("div");
  card.className = "fn";

  const head = mk("div", "fnhead");
  head.appendChild(mk("h3", null, fn.name));
  head.appendChild(mk("span", "grow"));
  head.appendChild(mk("span", "chip dim kind", "stream"));
  card.appendChild(head);

  card.appendChild(mk("p", "desc", fn.description));

  const decl = mk("div", "script mono");
  decl.appendChild(mk("div", "step",
    `verb:${fn.verb}   arg:${fn.arg}   in:[${fn.in_lo}, ${fn.in_hi}]`));
  for (const output of fn.outputs) {
    decl.appendChild(mk("div", "step", outputText(output)));
  }
  card.appendChild(decl);

  return card;
}

function buildScriptedCard(fn) {
  const card = document.createElement("div");
  card.className = "fn";

  const head = mk("div", "fnhead");
  head.appendChild(mk("h3", null, fn.name));
  head.appendChild(mk("span", "grow"));
  head.appendChild(mk("span", "chip dim kind", fn.target));
  card.appendChild(head);

  const descEl = mk("p", "desc", fn.description);
  card.appendChild(descEl);
  card._descEl = descEl;

  const cond = fn.condition;
  const condText = cond.description + "   (" + cond.source
    + (cond.verb ? ": " + cond.verb : "") + ")";
  card.appendChild(mk("p", "cond", condText));

  const scriptbar = mk("div", "scriptbar");
  const n = fn.script.length;
  const expander = mk("button", "expander", null);
  expander.type = "button";
  expander.setAttribute("aria-expanded", "false");
  const tri = mk("span", "tri", "▸");
  const label = mk("span", "mono",
    `${n} step${n === 1 ? "" : "s"} · ${maxOffset(fn.script).toFixed(1)}s`);
  expander.appendChild(tri);
  expander.appendChild(label);
  scriptbar.appendChild(expander);
  card.appendChild(scriptbar);

  const script = mk("div", "script mono");
  for (const step of fn.script) {
    script.appendChild(mk("div", "step", stepText(step)));
  }
  card.appendChild(script);

  expander.onclick = () => {
    const open = !script.classList.contains("open");
    script.classList.toggle("open", open);
    expander.setAttribute("aria-expanded", open ? "true" : "false");
  };

  const firerow = mk("div", "firerow");
  let picker = null;
  if (fn.target === "DEVICE" || fn.target === "SURFACE") {
    picker = document.createElement("select");
    picker.id = "functionDev_" + fn.name;
    firerow.appendChild(picker);
    fillDevicePicker(picker, fn.target === "SURFACE");
    picker.onchange = () => refreshCardCompatibility(fn, picker, card);
    refreshCardCompatibility(fn, picker, card);
  }
  firerow.appendChild(mk("span", "grow"));
  const fireBtn = mk("button", "btn solid-gold", "Fire");
  fireBtn.onclick = () => {
    const extra = { name: fn.name };
    if (picker) extra.dev = picker.value;
    wire.send("fire_function", extra, fireBtn);
  };
  firerow.appendChild(fireBtn);
  card.appendChild(firerow);

  const firedLine = mk("div", "fired-line");
  applyFired(card, firedLine, lastFired[fn.name]);
  card.appendChild(firedLine);
  card._firedLine = firedLine;

  return card;
}

// GENERATOR and STREAM cards render their declaration lines only -- no Fire
// button (they never accept an admin-manual fire, see GameServer.fire_function's
// kind refusal) and no fired-line (function_fired is scoped to SCRIPTED fires).
function buildCard(fn) {
  if (fn.kind === "generator") return buildGeneratorCard(fn);
  if (fn.kind === "stream") return buildStreamCard(fn);
  return buildScriptedCard(fn);
}

function render(list) {
  const mount = document.getElementById("functionsMount");
  clear(mount);
  cardByName.clear();
  // The diagnostics row is built once and reused across rebuilds -- clear()
  // just emptied the mount, so re-append the same node rather than
  // reconstructing it.
  mount.appendChild(ensureDiagRow());

  if (!list.length) {
    currentDeviceTargets = new Map();
    mount.appendChild(mk("p", "muted", "No functions declared"));
    return;
  }

  const grid = mk("div", "fngrid");
  mount.appendChild(grid);
  currentDeviceTargets = new Map();
  for (const fn of list) {
    const card = buildCard(fn);
    grid.appendChild(card);
    cardByName.set(fn.name, card);
    if (fn.target === "DEVICE" || fn.target === "SURFACE") {
      currentDeviceTargets.set(fn.name, { target: fn.target, fn });
    }
  }
}

function onFunctionsChanged(list) {
  const functions = list || [];
  const signature = JSON.stringify(functions);
  if (signature === fnSignature) return;
  fnSignature = signature;
  render(functions);
}

function onFunctionFired(fired) {
  if (!fired || !fired.name) return;
  lastFired[fired.name] = fired;
  const card = cardByName.get(fired.name);
  if (card && card._firedLine) applyFired(card, card._firedLine, fired);
}

// ---------------------------------------------------------------------- init

export function init() {
  wire.on("snapshot", (m) => {
    onDevicesChanged(m.devices);
    updateInstrumentData(m);
    onFunctionsChanged(m.functions);
  });
  wire.on("functions_changed", (m) => {
    updateInstrumentData(m);
    onFunctionsChanged(m.functions);
  });
  wire.on("devices_changed", (m) => onDevicesChanged(m.devices));
  wire.on("function_fired", (m) => onFunctionFired(m.fired));
}
