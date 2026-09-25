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
let fnDevices = [];                  // {dev, muted, fixture} from devices_changed; fixture-bound ones are offered as their fixture
let fnFixtures = [];                 // {name, dev, muted}: declared Room fixtures, profile order
let fixtureSignature = "[]";         // last fnFixtures applied to the pickers (rule 1 gate)
const FIXTURE_PREFIX = "@fixture:";  // the engine's fixture token; the only JS speller
let currentDeviceTargets = new Map(); // name -> {target, fn, picker} for rendered SURFACE/DEVICE pickers
const cardByName = new Map();        // function name -> its row element (test hook)
const infoBtnByName = new Map();     // function name -> its (i) button (test hook)
const fireBtnByName = new Map();     // function name -> its Fire button (test hook)
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

export function _fireBtnFor(name) {
  return fireBtnByName.get(name);
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

// Preserves the operator's current selection when the offered list changes
// under it, falling back to the first option only when the previous
// selection is no longer available.
//
// withRoom (SURFACE targets, Diagnostics) offers All, then every declared
// Room fixture BY NAME (value @fixture:<name>, bound or not -- the engine
// resolves the token to the bound dev), then every device not bound to a
// fixture (a bound device is already reachable as its fixture). Without it
// (DEVICE targets: "the firing device") a Room fixture is never offered --
// it used to be, and as the first option it was the default, so a manual
// fire meant for a board landed on a strip. With no device connected the
// picker holds one empty placeholder and the row's Fire button stays
// disabled (refreshFireButton).
function fillDevicePicker(picker, withRoom) {
  if (!picker) return;
  const previous = picker.value;
  clear(picker);
  const values = [];
  const add = (value, text) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    picker.appendChild(option);
    values.push(value);
  };
  if (withRoom) {
    add(ALL_OPTION, "All");
    for (const { name, dev, muted } of fnFixtures) {
      const base = `${name} (${dev || "unbound"})`;
      add(FIXTURE_PREFIX + name, muted ? `${base} (muted)` : base);
    }
  }
  const offered = fnDevices.filter((d) => !d.fixture);
  if (!withRoom && !offered.length) {
    add("", "no device joined");
    picker.value = "";
    return;
  }
  for (const { dev, muted } of offered) {
    add(dev, muted ? `${dev} (muted)` : dev);
  }
  if (values.indexOf(previous) >= 0) picker.value = previous;
  else if (values.length) picker.value = values[0];
}

// A picker value as an operator reads it: a fixture token by its name.
function targetLabel(value) {
  return value && value.startsWith(FIXTURE_PREFIX)
    ? value.slice(FIXTURE_PREFIX.length) : value;
}

function refillPickers() {
  if (diagPicker) {
    fillDevicePicker(diagPicker, true);
    refreshDiagButtons();
  }
  for (const [name, info] of currentDeviceTargets) {
    fillDevicePicker(info.picker, info.target === "SURFACE");
    refreshCardCompatibility(info.fn, info.picker, cardByName.get(name));
  }
}

function onDevicesChanged(devices) {
  fnDevices = (devices || []).map((d) => (
    { dev: d.dev, muted: !!d.muted, fixture: d.fixture || null }));
  refillPickers();
}

// Rule 1: room_changed fires on every live controller value, so the
// fixture rows are applied (and pickers refilled) only when a fixture's
// name, binding or mute state actually changed. Returns whether it did.
function applyRoomFixtures(room) {
  const next = ((room && room.fixtures) || []).map((f) => (
    { name: f.name, dev: f.dev || null, muted: !!f.muted }));
  const signature = JSON.stringify(next);
  if (signature === fixtureSignature) return false;
  fixtureSignature = signature;
  fnFixtures = next;
  return true;
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
      option.disabled = option.value === "" || !isCompatible(fn, option.value);
    }
  }
  if (card) {
    card._descText = resolvedDescription(fn, picker ? picker.value : null);
    if (card._popDesc) card._popDesc.textContent = card._descText;
    refreshFireButton(fn, picker, card._fireBtn);
  }
}

// Fire is disabled, with the reason as its tooltip, whenever the server
// would only refuse it: no device to target, or a surface whose
// instrument has no function of this name. A refusal after the click
// told the operator the same thing, later and less clearly.
function refreshFireButton(fn, picker, btn) {
  if (!btn) return;
  let reason = "";
  if (picker && !picker.value) {
    reason = "Join a device first: this trigger targets one device";
  } else if (picker && !isCompatible(fn, picker.value)) {
    const inst = surfaceInstruments[surfaceLookupKey(picker.value)];
    reason = `Not available on ${targetLabel(picker.value)}` +
      (inst ? `: ${inst} has no ${fn.name}` : "");
  }
  btn.disabled = !!reason;
  btn.title = reason;
}

function refreshAllCardCompatibility() {
  refreshDiagButtons();
  for (const [name, info] of currentDeviceTargets) {
    refreshCardCompatibility(info.fn, info.picker, cardByName.get(name));
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

// What each builtin needs (control/builtins.py), for the disabled tooltip.
const BUILTIN_NEEDS = { stop: "light or audio", flash: "light", ping: "audio" };

function refreshDiagButtons() {
  if (!diagPicker) return;
  const value = diagPicker.value;
  const names = builtinsFor(value);
  const inst = surfaceInstruments[surfaceLookupKey(value)];
  // Stop first: it is the panic button.
  for (const name of ["stop", "flash", "ping"]) {
    const ok = names.includes(name);
    diagButtons[name].disabled = !ok;
    diagButtons[name].title = ok ? ""
      : value === ALL_OPTION
        ? `No connected surface has a ${BUILTIN_NEEDS[name]} capability`
        : `${value}${inst ? ` (${inst})` : ""} has no ${BUILTIN_NEEDS[name]} capability`;
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
  // Held directly, the same pattern diagPicker already uses above, rather
  // than re-looked-up by id: a refill must keep mutating this exact node
  // regardless of whether its id is still registered, independent of any
  // getElementById quirk (the node test stub's included -- _dom_stub.js
  // auto-vivifies a fresh placeholder for an id it has not seen, which a
  // real browser's getElementById would not do). currentDeviceTargets
  // carries this reference only for the current rows; the next full
  // render() replaces both the Map and every row, so a detached picker
  // is retained no longer than that.
  row._picker = picker;
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
    row._fireBtn = fireBtn;
    fireBtnByName.set(fn.name, fireBtn);

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
  fireBtnByName.clear();
  // The diagnostics row is built once and reused across rebuilds -- clear()
  // just emptied the mount, so re-append the same node rather than
  // reconstructing it.
  mount.appendChild(ensureDiagRow());

  if (!list.length) {
    currentDeviceTargets = new Map();
    mount.appendChild(mk("p", "muted",
      "This Bit declares no triggers. Diagnostics above works on any device."));
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
      currentDeviceTargets.set(fn.name, { target: fn.target, fn, picker: row._picker });
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

const PULSE_MS = 1200;

function onFunctionFired(fired) {
  if (!fired || !fired.name) return;
  lastFired[fired.name] = fired;
  const card = cardByName.get(fired.name);
  if (card && card._firedLine) applyFired(card, card._firedLine, fired);
  // A brief mark on the row, so a fire (manual or from a live gesture) is
  // noticeable without reading the fired-line text.
  if (card) {
    card.classList.add("justfired");
    clearTimeout(card._pulseTimer);
    card._pulseTimer = setTimeout(() => card.classList.remove("justfired"), PULSE_MS);
  }
}

// ---------------------------------------------------------------------- init

export function init() {
  wire.on("snapshot", (m) => {
    applyRoomFixtures(m.room);
    onDevicesChanged(m.devices);
    updateInstrumentData(m);
    onFunctionsChanged(m.functions);
  });
  wire.on("room_changed", (m) => {
    if (applyRoomFixtures(m.room)) refillPickers();
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
