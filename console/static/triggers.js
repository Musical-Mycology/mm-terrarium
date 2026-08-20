// Trigger panel: one card per Bit-declared trigger, showing what makes it
// happen and the actual steps it runs, plus a Fire button for the operator.
//
// The card list is rebuilt ONLY when the declared table changes. trigger_fired
// arrives per fire and updates one status line in place. This is the same
// discipline room.js's #roomStrip needed after Defect 1, where rebuilding on
// every event destroyed what the panel was there to show; a trigger table is
// static per Bit, so a rebuild on every fire would be pure waste and would
// discard the picker selections an operator had made.

(function () {
"use strict";
let triggerSignature = null;      // JSON of the last rendered declaration
const lastFired = {};             // trigger name -> its last fire record
let triggerDevices = [];          // device ids offered by DEVICE-target pickers

function renderTriggerDevices(devices) {
  // Kept as ids only: the picker needs nothing else, and the device list
  // re-renders far more often than the trigger table does.
  triggerDevices = (devices || []).map((d) => d.dev);
  for (const trigger of currentDeviceTargets) {
    fillDevicePicker(document.getElementById("triggerDev_" + trigger));
  }
}

let currentDeviceTargets = [];

function fillDevicePicker(picker) {
  if (!picker) return;
  const previous = picker.value;
  picker.innerHTML = "";
  for (const dev of triggerDevices) {
    const option = document.createElement("option");
    option.value = dev;
    option.textContent = dev;
    picker.appendChild(option);
  }
  if (triggerDevices.indexOf(previous) >= 0) picker.value = previous;
  else if (triggerDevices.length) picker.value = triggerDevices[0];
}

function stepText(step) {
  const offset = "+" + Number(step.offset).toFixed(2) + "s";
  if (step.kind === "play") {
    return `${offset}   ${step.dev}   play "${step.name}"`;
  }
  return `${offset}   ${step.dev}   cc:${step.data1} = ${step.data2}`;
}

function firedText(fired) {
  if (!fired) return "never fired";
  const where = fired.devs && fired.devs.length
    ? fired.devs.join(", ") : "nothing";
  const tag = fired.fired_by === "admin-manual" ? "   ADMIN MANUAL" : "";
  return `last fired by ${fired.fired_by} -> ${where}`
    + ` (${fired.steps} cue${fired.steps === 1 ? "" : "s"})${tag}`;
}

function applyFired(line, fired) {
  line.textContent = firedText(fired);
  line.className = fired && fired.fired_by === "admin-manual"
    ? "fired manual" : "fired";
}

function buildCard(trigger) {
  const card = document.createElement("div");
  card.className = "card trigger";

  const title = document.createElement("h3");
  title.textContent = trigger.name;
  card.appendChild(title);

  const target = document.createElement("span");
  target.className = "kind";
  target.textContent = trigger.target;
  card.appendChild(target);

  const description = document.createElement("p");
  description.textContent = trigger.description;
  card.appendChild(description);

  const condition = document.createElement("p");
  condition.className = "muted";
  condition.textContent = trigger.condition.description
    + "   (" + trigger.condition.source
    + (trigger.condition.verb ? ": " + trigger.condition.verb : "") + ")";
  card.appendChild(condition);

  for (const step of trigger.script) {
    const line = document.createElement("div");
    line.className = "step";
    line.textContent = stepText(step);
    card.appendChild(line);
  }

  if (trigger.target === "DEVICE") {
    const picker = document.createElement("select");
    picker.id = "triggerDev_" + trigger.name;
    card.appendChild(picker);
    fillDevicePicker(picker);
  }

  const button = document.createElement("button");
  button.textContent = "Fire";
  // Assigned here rather than looked up at top level: console.js already owns
  // the only top-level element lookups, and adding one for an element that
  // does not exist until a Bit is loaded would break on an empty console.
  button.onclick = () => {
    const picker = document.getElementById("triggerDev_" + trigger.name);
    const extra = { name: trigger.name };
    if (picker) extra.dev = picker.value;
    send("fire_trigger", extra);
  };
  card.appendChild(button);

  const fired = document.createElement("div");
  fired.id = "triggerFired_" + trigger.name;
  applyFired(fired, lastFired[trigger.name]);
  card.appendChild(fired);

  return card;
}

function renderTriggers(triggers) {
  const el = document.getElementById("triggers");
  const list = triggers || [];
  const signature = JSON.stringify(list);
  if (signature === triggerSignature) return;
  triggerSignature = signature;

  el.innerHTML = "";
  if (!list.length) {
    currentDeviceTargets = [];
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No triggers declared";
    el.appendChild(empty);
    return;
  }

  const cards = document.createElement("div");
  cards.id = "triggerCards";
  cards.className = "cards";
  el.appendChild(cards);
  currentDeviceTargets = [];
  for (const trigger of list) {
    cards.appendChild(buildCard(trigger));
    if (trigger.target === "DEVICE") currentDeviceTargets.push(trigger.name);
  }
}

function renderTriggerFired(fired) {
  if (!fired || !fired.name) return;
  // Recorded even when no card exists yet, so a fire that arrives before the
  // snapshot has rendered still shows once the cards are built.
  lastFired[fired.name] = fired;
  const line = document.getElementById("triggerFired_" + fired.name);
  if (line) applyFired(line, fired);
}

// The panel's entry points, and the ONLY names this file puts in the shared
// global scope. buildCard in particular is private now: it previously
// collided with room.js's same-named helper, and because triggers.js loads
// second it silently won, making renderRoom throw on every room_changed.
window.renderTriggers = renderTriggers;
window.renderTriggerDevices = renderTriggerDevices;
window.renderTriggerFired = renderTriggerFired;
})();
