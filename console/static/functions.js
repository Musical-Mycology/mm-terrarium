// Functions panel: compact rows rendered into #functionsMount (created once
// by surface.js, outside its per-fixture rebuild path). One row per
// Bit-declared function: name, target/kind chip, a one-line summary, a
// device picker where the target needs one, an (i) button that opens a
// popover with the description/condition/script detail, and (for scripted
// functions only) a Fire button and a last-fired line.
//
// Rendering discipline (rule 1): the row list is rebuilt ONLY when the
// declared function table's signature actually changes -- `devices_changed`
// fires far more often than `functions_changed` and must never cause a
// rebuild, only a picker refill (which preserves the operator's current
// selection). `lastFired` is tracked outside the signature-gated rebuild
// path so it survives any row rebuild -- a function that already fired
// still shows its fired state after its row is recreated.
import * as wire from "./wire.js";

let fnSignature = null;              // JSON of the last-rendered declaration
const lastFired = {};                // function name -> its last fire record (survives rebuilds)
let fnDevices = [];                  // {dev, muted, fixture} offered by DEVICE/SURFACE pickers
let currentDeviceTargets = new Map(); // name -> {target, fn} for rendered SURFACE/DEVICE pickers
const cardByName = new Map();        // function name -> its row element (test hook)
const infoBtnByName = new Map();     // function name -> its (i) button (test hook)
let openPopoverRow = null;           // the row whose popover is open, else null
const ALL_OPTION = "@all";

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

export function _infoBtnFor(name) {
  return infoBtnByName.get(name);
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
    option.value = ALL_OPTION;
    option.textContent = "All";
    picker.appendChild(option);
    values.push(ALL_OPTION);
  }
  for (const { dev, muted, fixture } of fnDevices) {
    const option = document.createElement("option");
    option.value = dev;
    const base = fixture ? `${dev} (${fixture})` : dev;
    option.textContent = muted ? `${base} (muted)` : base;
    picker.appendChild(option);
    values.push(dev);
  }
  if (values.indexOf(previous) >= 0) picker.value = previous;
  else if (values.length) picker.value = values[0];
}

function onDevicesChanged(devices) {
  fnDevices = (devices || []).map((d) => (
    { dev: d.dev, muted: !!d.muted, fixture: d.fixture || null }));
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
  if (surfaceValue === ALL_OPTION) {
    return Object.values(surfaceInstruments).some((instrumentName) => {
      const fns = instrumentFunctions[instrumentName] || [];
      return fns.some((f) => f.name === fn.name);
    });
  }
  const instrumentName = surfaceInstruments[surfaceLookupKey(surfaceValue)];
  if (!instrumentName) return false;
  const fns = instrumentFunctions[instrumentName] || [];
  return fns.some((f) => f.name === fn.name);
}

// The description shown for a card at its currently-selected surface: the
// resolved instrument's own function description when it declares one,
// falling back to the Bit's own description otherwise.
function resolvedDescription(fn, surfaceValue) {
  const instrumentName = surfaceInstruments[surfaceLookupKey(surfaceValue)];
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
  if (card) {
    card._descText = resolvedDescription(fn, picker ? picker.value : null);
    if (card._popDesc) card._popDesc.textContent = card._descText;
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

// ALL_OPTION ("@all") names no single surface_instruments entry -- it fans
// out to the Room plus every device, so there is no one instrument to look
// up. builtinsFor's ALL_OPTION branch unions across every entry instead of
// calling this. Any other picker value passes through unchanged.
function surfaceLookupKey(pickerValue) {
  return pickerValue === ALL_OPTION ? null : pickerValue;
}

// The builtin names available at a picker value: for ALL_OPTION, the union
// of every surface's builtins (the Room fixture's own entry included, since
// the union walks every key of surfaceInstruments); otherwise the one
// surface's own builtins.
function builtinsFor(pickerValue) {
  if (pickerValue === ALL_OPTION) {
    const set = new Set();
    for (const key of Object.keys(surfaceInstruments)) {
      for (const n of builtinsMap[surfaceInstruments[key]] || []) set.add(n);
    }
    return [...set];
  }
  const inst = surfaceInstruments[pickerValue];
  return inst ? builtinsMap[inst] || [] : [];
}

function refreshDiagButtons() {
  if (!diagPicker) return;
  const names = builtinsFor(diagPicker.value);
  // Stop first: it is the panic button.
  for (const name of ["stop", "flash", "ping"]) {
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
  // Stop first: it is the panic button.
  for (const name of ["stop", "flash", "ping"]) {
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

function summaryText(fn) {
  if (fn.kind === "generator") return `period ${fn.period}s · cc:${fn.lane.data1}`;
  if (fn.kind === "stream") return `verb:${fn.verb} → ${fn.outputs.length} output${fn.outputs.length === 1 ? "" : "s"}`;
  const n = fn.script.length;
  return `${n} step${n === 1 ? "" : "s"} · ${maxOffset(fn.script).toFixed(1)}s`;
}

function chipText(fn) {
  if (fn.kind === "generator" || fn.kind === "stream") return fn.kind;
  return fn.target;
}

function detailLines(fn) {
  if (fn.kind === "generator") {
    return [laneText(fn.lane), `waveform:${fn.waveform}   period:${fn.period}s   [${fn.lo}, ${fn.hi}]`];
  }
  if (fn.kind === "stream") {
    return [`verb:${fn.verb}   arg:${fn.arg}   in:[${fn.in_lo}, ${fn.in_hi}]`,
            ...fn.outputs.map(outputText)];
  }
  return fn.script.map(stepText);
}

function closePopover() {
  if (!openPopoverRow) return;
  if (openPopoverRow._popover) openPopoverRow._popover.remove();
  openPopoverRow._popover = null;
  openPopoverRow._popDesc = null;
  openPopoverRow = null;
}

function openPopover(fn, row) {
  closePopover();
  const pop = mk("div", "popover");
  // Stop a click inside the popover (e.g. selecting script text) from
  // bubbling to the document-level "click anywhere closes it" listener
  // registered in init() -- only outside clicks (and Escape) should close.
  pop.onclick = (e) => { if (e && e.stopPropagation) e.stopPropagation(); };
  const desc = mk("p", "desc", row._descText || fn.description);
  pop.appendChild(desc);
  row._popDesc = desc;
  if (fn.condition) {
    const cond = fn.condition;
    pop.appendChild(mk("p", "cond",
      cond.description + "   (" + cond.source + (cond.verb ? ": " + cond.verb : "") + ")"));
  }
  const script = mk("div", "script mono open");
  for (const line of detailLines(fn)) script.appendChild(mk("div", "step", line));
  pop.appendChild(script);
  row.appendChild(pop);
  row._popover = pop;
  openPopoverRow = row;
}

// GENERATOR and STREAM rows render their declaration lines only -- no Fire
// button (they never accept an admin-manual fire, see GameServer.fire_function's
// kind refusal) and no fired-line (function_fired is scoped to SCRIPTED fires).
function buildRow(fn) {
  const row = mk("div", "fn fnrow");
  row._popover = null;
  row._popDesc = null;
  row._descText = fn.description;

  row.appendChild(mk("h3", null, fn.name));
  row.appendChild(mk("span", "chip dim kind", chipText(fn)));
  row.appendChild(mk("span", "mono summary", summaryText(fn)));

  let picker = null;
  if (fn.target === "DEVICE" || fn.target === "SURFACE") {
    picker = document.createElement("select");
    picker.id = "functionDev_" + fn.name;
    row.appendChild(picker);
    fillDevicePicker(picker, fn.target === "SURFACE");
    picker.onchange = () => refreshCardCompatibility(fn, picker, row);
  }
  row.appendChild(mk("span", "grow"));

  const infoBtn = mk("button", "infobtn", "i");
  infoBtn.type = "button";
  infoBtn.setAttribute("aria-label", `Details for ${fn.name}`);
  infoBtn.title = "Details";
  infoBtn.onclick = (e) => {
    if (e && e.stopPropagation) e.stopPropagation();
    if (row._popover) closePopover(); else openPopover(fn, row);
  };
  row.appendChild(infoBtn);
  infoBtnByName.set(fn.name, infoBtn);

  if (fn.kind !== "generator" && fn.kind !== "stream") {
    const fireBtn = mk("button", "btn solid-gold small", "Fire");
    fireBtn.onclick = () => {
      const extra = { name: fn.name };
      if (picker) extra.dev = picker.value;
      wire.send("fire_function", extra, fireBtn);
    };
    row.appendChild(fireBtn);

    const firedLine = mk("div", "fired-line");
    applyFired(row, firedLine, lastFired[fn.name]);
    row.appendChild(firedLine);
    row._firedLine = firedLine;
  }

  refreshCardCompatibility(fn, picker, row);
  return row;
}

function render(list) {
  const mount = document.getElementById("functionsMount");
  if (!mount) return false;
  clear(mount);
  cardByName.clear();
  closePopover();
  infoBtnByName.clear();
  // The diagnostics row is built once and reused across rebuilds -- clear()
  // just emptied the mount, so re-append the same node rather than
  // reconstructing it.
  mount.appendChild(ensureDiagRow());

  if (!list.length) {
    currentDeviceTargets = new Map();
    mount.appendChild(mk("p", "muted", "No functions declared"));
    return true;
  }

  const fnlist = mk("div", "fnlist");
  mount.appendChild(fnlist);
  currentDeviceTargets = new Map();
  for (const fn of list) {
    const row = buildRow(fn);
    fnlist.appendChild(row);
    cardByName.set(fn.name, row);
    if (fn.target === "DEVICE" || fn.target === "SURFACE") {
      currentDeviceTargets.set(fn.name, { target: fn.target, fn });
    }
  }
  return true;
}

function onFunctionsChanged(list) {
  const functions = list || [];
  const signature = JSON.stringify(functions);
  // The mount is also checked here, not just inside render(): if it's
  // missing when a same-signature message arrives, we must not just return
  // -- render() itself never runs, so fnSignature never gets a chance to be
  // invalidated below, and a later remount with the same list would stay
  // blocked by this same stale signature forever.
  if (signature === fnSignature && document.getElementById("functionsMount")) return;
  if (render(functions)) fnSignature = signature;
  else fnSignature = null;
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

  document.addEventListener("click", () => closePopover());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopover(); });
}
