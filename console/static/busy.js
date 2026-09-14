// Loading overlay for blocking Terrarium operations. Driven by Terrarium
// state, not by the click: it opens when terrarium_state becomes
// ROOM_LOADING / ROOM_UNLOADING (from snapshot or state_changed), so a load
// started from another tab, or a reconnect mid-load, shows progress too.
// Closes when the state settles; a failure keeps it up with the reason
// and a Dismiss button. Generic surface: begin/stage/fail/end.
import * as wire from "./wire.js";

let overlayEl = null;      // the .overlay node while shown, else null
let stagesEl = null;       // .stages list inside it
let bodyEl = null;         // .busybody (stages or failure text)
let failed = false;        // true after fail(): state settling must not close it
let requestedRoom = null;  // room name this tab last asked to load
let activeRoom = null;     // active room name from snapshot/room_loaded
const ROOM_COMMANDS = new Set(["load_room", "unload_room", "load_bit"]);

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

export function _overlay() {
  return overlayEl;
}

export function begin({ title }) {
  if (overlayEl) { end(); }
  failed = false;
  const mount = document.getElementById("overlayMount");
  overlayEl = mk("div", "overlay open busy");
  const panel = mk("div", "picker busypanel");
  const head = mk("div", "pickhead");
  const dot = mk("span", "dot");
  head.appendChild(dot);
  head.appendChild(mk("h2", null, title));
  panel.appendChild(head);
  bodyEl = mk("div", "busybody");
  stagesEl = mk("ul", "stages mono");
  bodyEl.appendChild(stagesEl);
  panel.appendChild(bodyEl);
  overlayEl.appendChild(panel);
  mount.appendChild(overlayEl);
}

export function stage(text) {
  if (!overlayEl || failed) return;
  for (const li of stagesEl.children) li.className = "done";
  stagesEl.appendChild(mk("li", "current", text));
}

export function fail(text) {
  if (!overlayEl) begin({ title: "Failed" });
  failed = true;
  bodyEl.textContent = "";
  bodyEl.appendChild(mk("p", "inline-err", text));
  const btn = mk("button", "btn outline small", "Dismiss");
  btn.onclick = end;
  bodyEl.appendChild(btn);
}

export function end() {
  if (overlayEl) overlayEl.remove();
  overlayEl = null;
  stagesEl = null;
  bodyEl = null;
  failed = false;
}

function roomName() {
  return activeRoom || requestedRoom || "";
}

function onTerrariumState(state) {
  if (state === "ROOM_LOADING") {
    if (!overlayEl) begin({ title: `Loading Room ${roomName()}`.trim() });
  } else if (state === "ROOM_UNLOADING") {
    if (!overlayEl) begin({ title: `Unloading Room ${roomName()}`.trim() });
  } else if (state === "ROOM_READY" || state === "NO_ROOM") {
    if (overlayEl && !failed) end();
  }
}

export function init() {
  // Remember which room this tab asked for, for the overlay title.
  wire.on("_sent", (m) => {
    if (m.command === "load_room") requestedRoom = m.name;
    if (m.command === "load_bit") requestedRoom = m.room || requestedRoom;
  });
  wire.on("snapshot", (m) => {
    const active = (m.rooms || []).find((r) => r.active);
    activeRoom = active ? active.name : null;
    onTerrariumState(m.terrarium_state);
  });
  wire.on("state_changed", (m) => onTerrariumState(m.terrarium_state));
  wire.on("room_load_progress", (m) => stage(m.stage));
  wire.on("room_loaded", (m) => { activeRoom = m.name; if (!failed) end(); });
  wire.on("room_unloaded", () => { activeRoom = null; if (!failed) end(); });
  wire.on("room_load_failed", (m) => fail(m.reason || "load failed"));
  wire.on("error", (m) => {
    if (overlayEl && ROOM_COMMANDS.has(m.command)) fail(m.message);
  });
}
