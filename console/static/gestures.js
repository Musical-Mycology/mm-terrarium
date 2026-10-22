// Gesture panel (#gestureCard): drives a simulated tuneshroom. A person at
// the Console picks a player device and sends Tap, Double tap, or a tilt;
// the agent hands each to the Bit exactly as a real device's /game/<verb>
// would arrive. Visible only while a Bit is in SETUP or RUNNING and at least
// one non-fixture device exists.
//
// Rendering discipline (rule 1): the card is built once; devices_changed
// only refills the picker (preserving the selection), never rebuilds.
import * as wire from "./wire.js";
import { mk, clear } from "./dom.js";

const TILT_MIN = -90;
const TILT_MAX = 90;
const TILT_THROTTLE_MS = 50;

let state = "IDLE";
let devices = [];            // {dev, muted, fixture} rows from the server
let built = false;
let picker = null;
let tiltInput = null;
let readout = null;
let lastTiltSentAt = 0;

function players() {
  return devices.filter((d) => !d.fixture);
}

function sendGesture(extra) {
  if (!picker || !picker.value) return;
  wire.send("gesture", Object.assign({ dev: picker.value }, extra));
}

function sendTilt() {
  const gamma = Number(tiltInput.value);
  readout.textContent = `${gamma}°`;
  sendGesture({ verb: "tilt", gamma });
  lastTiltSentAt = Date.now();
}

function fillPicker() {
  const previous = picker.value;
  clear(picker);
  const offered = players();
  for (const { dev } of offered) {
    const option = document.createElement("option");
    option.value = dev;
    option.textContent = dev;
    picker.appendChild(option);
  }
  picker.value = offered.some((d) => d.dev === previous)
    ? previous
    : (offered[0] ? offered[0].dev : "");
}

function build(card) {
  picker = document.createElement("select");
  picker.setAttribute("aria-label", "Simulated device");

  const tap = mk("button", "btn", "Tap");
  tap.type = "button";
  tap.onclick = () => sendGesture({ verb: "tap", count: 1 });
  const double = mk("button", "btn", "Double tap");
  double.type = "button";
  double.onclick = () => sendGesture({ verb: "tap", count: 2 });

  const buttons = mk("div", "firerow");
  buttons.appendChild(picker);
  buttons.appendChild(tap);
  buttons.appendChild(double);

  tiltInput = document.createElement("input");
  tiltInput.type = "range";
  tiltInput.min = String(TILT_MIN);
  tiltInput.max = String(TILT_MAX);
  tiltInput.step = "1";
  tiltInput.value = "0";
  tiltInput.setAttribute("aria-label", "Tilt");
  readout = mk("span", "mono", "0°");
  tiltInput.oninput = () => {
    readout.textContent = `${Number(tiltInput.value)}°`;
    if (Date.now() - lastTiltSentAt >= TILT_THROTTLE_MS) sendTilt();
  };
  tiltInput.onchange = sendTilt;     // the final value always lands
  const level = mk("button", "btn", "Level");
  level.type = "button";
  level.onclick = () => {
    tiltInput.value = "0";
    sendTilt();
  };

  const tilt = mk("div", "firerow");
  tilt.appendChild(mk("span", "k", "TILT"));
  tilt.appendChild(tiltInput);
  tilt.appendChild(readout);
  tilt.appendChild(level);

  card.appendChild(mk("div", "k", "SIMULATED DEVICE"));
  card.appendChild(buttons);
  card.appendChild(tilt);
  built = true;
}

function render() {
  const card = document.getElementById("gestureCard");
  const live = state === "SETUP" || state === "RUNNING";
  const visible = live && players().length > 0;
  card.hidden = !visible;
  if (!visible) return;
  if (!built) build(card);
  fillPicker();
}

export function init() {
  wire.on("snapshot", (m) => {
    state = m.state;
    devices = m.devices || [];
    render();
  });
  wire.on("state_changed", (m) => {
    state = m.state;
    render();
  });
  wire.on("devices_changed", (m) => {
    devices = m.devices || [];
    render();
  });
}
