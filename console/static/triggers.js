// Triggers panel: compact cards rendered into #triggersMount (created once
// by surface.js, outside its per-fixture rebuild path). One card per
// Bit-declared trigger: name, target chip, description, condition line,
// collapsed script <details>, a bottom-pinned action row (device picker +
// Fire for DEVICE targets, Fire only for ROOM targets), and a last-fired
// line.
//
// Rendering discipline (rule 1): the card list is rebuilt ONLY when the
// declared trigger table's signature actually changes -- `devices_changed`
// fires far more often than `triggers_changed` and must never cause a
// rebuild, only a picker refill (which preserves the operator's current
// selection). `lastFired` is tracked outside the signature-gated rebuild
// path so it survives any card rebuild -- a trigger that already fired
// still shows its fired state after its card is recreated.
import * as wire from "./wire.js";

let triggerSignature = null;         // JSON of the last-rendered declaration
const lastFired = {};                // trigger name -> its last fire record (survives rebuilds)
let triggerDevices = [];             // device ids offered by DEVICE-target pickers
let currentDeviceTargets = [];       // names of currently-rendered DEVICE-target triggers
const cardByName = new Map();        // trigger name -> its card element (test hook)

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

// Ported verbatim from the pre-redesign triggers.js: preserves the
// operator's current selection when the live device list changes under it,
// falling back to the first offered device only when the previous
// selection is no longer available.
function fillDevicePicker(picker) {
  if (!picker) return;
  const previous = picker.value;
  clear(picker);
  for (const dev of triggerDevices) {
    const option = document.createElement("option");
    option.value = dev;
    option.textContent = dev;
    picker.appendChild(option);
  }
  if (triggerDevices.indexOf(previous) >= 0) picker.value = previous;
  else if (triggerDevices.length) picker.value = triggerDevices[0];
}

function onDevicesChanged(devices) {
  triggerDevices = (devices || []).map((d) => d.dev);
  for (const name of currentDeviceTargets) {
    fillDevicePicker(document.getElementById("triggerDev_" + name));
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

function applyFired(line, fired) {
  clear(line);
  if (!fired) {
    line.className = "fired dim";
    line.textContent = "never fired";
    return;
  }
  const isAdmin = fired.fired_by === "admin-manual";
  line.className = isAdmin ? "fired fired-admin" : "fired fired-bit";
  line.appendChild(document.createTextNode(firedText(fired)));
  if (isAdmin) {
    line.appendChild(document.createTextNode(" "));
    line.appendChild(mk("span", "admintag", "Admin manual"));
  }
}

function buildCard(trigger) {
  const card = document.createElement("div");
  card.className = "card trigger";

  const head = mk("div", "trighead");
  head.appendChild(mk("h3", null, trigger.name));
  head.appendChild(mk("span", "chip dim kind", trigger.target));
  card.appendChild(head);

  card.appendChild(mk("p", null, trigger.description));

  const cond = trigger.condition;
  const condText = cond.description + "   (" + cond.source
    + (cond.verb ? ": " + cond.verb : "") + ")";
  card.appendChild(mk("p", "muted", condText));

  const scriptDetails = document.createElement("details");
  scriptDetails.className = "script";
  const summary = document.createElement("summary");
  const n = trigger.script.length;
  summary.textContent = `${n} step${n === 1 ? "" : "s"} · ${maxOffset(trigger.script).toFixed(1)}s`;
  scriptDetails.appendChild(summary);
  const steps = mk("div", "steps mono");
  for (const step of trigger.script) {
    steps.appendChild(mk("div", "step", stepText(step)));
  }
  scriptDetails.appendChild(steps);
  card.appendChild(scriptDetails);

  const firerow = mk("div", "firerow");
  let picker = null;
  if (trigger.target === "DEVICE") {
    picker = document.createElement("select");
    picker.id = "triggerDev_" + trigger.name;
    firerow.appendChild(picker);
    fillDevicePicker(picker);
  }
  const fireBtn = mk("button", "btn solid-gold", "Fire");
  fireBtn.onclick = () => {
    const extra = { name: trigger.name };
    if (picker) extra.dev = picker.value;
    wire.send("fire_trigger", extra, fireBtn);
  };
  firerow.appendChild(fireBtn);
  card.appendChild(firerow);

  const firedLine = document.createElement("div");
  applyFired(firedLine, lastFired[trigger.name]);
  card.appendChild(firedLine);
  card._firedLine = firedLine;

  return card;
}

function render(list) {
  const mount = document.getElementById("triggersMount");
  clear(mount);
  cardByName.clear();

  if (!list.length) {
    currentDeviceTargets = [];
    mount.appendChild(mk("p", "muted", "No triggers declared"));
    return;
  }

  const grid = mk("div", "trigrid");
  mount.appendChild(grid);
  currentDeviceTargets = [];
  for (const trigger of list) {
    const card = buildCard(trigger);
    grid.appendChild(card);
    cardByName.set(trigger.name, card);
    if (trigger.target === "DEVICE") currentDeviceTargets.push(trigger.name);
  }
}

function onTriggersChanged(list) {
  const triggers = list || [];
  const signature = JSON.stringify(triggers);
  if (signature === triggerSignature) return;
  triggerSignature = signature;
  render(triggers);
}

function onTriggerFired(fired) {
  if (!fired || !fired.name) return;
  lastFired[fired.name] = fired;
  const card = cardByName.get(fired.name);
  if (card && card._firedLine) applyFired(card._firedLine, fired);
}

// ---------------------------------------------------------------------- init

export function init() {
  wire.on("snapshot", (m) => {
    onDevicesChanged(m.devices);
    onTriggersChanged(m.triggers);
  });
  wire.on("triggers_changed", (m) => onTriggersChanged(m.triggers));
  wire.on("devices_changed", (m) => onDevicesChanged(m.devices));
  wire.on("trigger_fired", (m) => onTriggerFired(m.fired));
}
