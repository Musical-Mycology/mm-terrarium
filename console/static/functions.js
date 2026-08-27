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
let currentDeviceTargets = new Map(); // name -> target ("DEVICE"/"SURFACE") for rendered pickers
const cardByName = new Map();        // function name -> its card element (test hook)
const ROOM_OPTION = "@room";

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
  for (const [name, target] of currentDeviceTargets) {
    fillDevicePicker(document.getElementById("functionDev_" + name), target === "SURFACE");
  }
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

function buildCard(fn) {
  const card = document.createElement("div");
  card.className = "fn";

  const head = mk("div", "fnhead");
  head.appendChild(mk("h3", null, fn.name));
  head.appendChild(mk("span", "grow"));
  head.appendChild(mk("span", "chip dim kind", fn.target));
  card.appendChild(head);

  card.appendChild(mk("p", "desc", fn.description));

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

function render(list) {
  const mount = document.getElementById("functionsMount");
  clear(mount);
  cardByName.clear();

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
      currentDeviceTargets.set(fn.name, fn.target);
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
    onFunctionsChanged(m.functions);
  });
  wire.on("functions_changed", (m) => onFunctionsChanged(m.functions));
  wire.on("devices_changed", (m) => onDevicesChanged(m.devices));
  wire.on("function_fired", (m) => onFunctionFired(m.fired));
}
