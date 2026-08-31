// Right rail: Registration rollup and the Event log. The per-device
// Instruments pull and per-role rows moved to the center views (rooms.js
// carries device detail; the Live view carries the log card).
import * as wire from "./wire.js";

let rolesByName = {};        // role name -> role_view() dict, for scored/jam classing
let registrationRows = [];   // last registration_changed/snapshot rows: {role, count, capacity}
let currentRoom = null;      // last snapshot/room_changed room, for the fixtures rollup
let pointerOverLog = false;

function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// ---------------------------------------------------------------- rollup

// Category rollup across declared roles: a declared role with no
// registration row still counts (as 0), so Jam reads 0/∞ rather than
// disappearing before anyone joins. Roles without a declaration (roles
// list empty or lagging) fall into neither category.
function rollup() {
  const countByRole = {};
  for (const row of registrationRows) countByRole[row.role] = row.count;

  const sums = { scored: { count: 0, cap: 0, unbounded: false },
                 jam: { count: 0, cap: 0, unbounded: false } };
  for (const decl of Object.values(rolesByName)) {
    const isJam = String(decl.class).toLowerCase() === "jam";
    const bucket = decl.scored ? sums.scored : isJam ? sums.jam : null;
    if (!bucket) continue;
    bucket.count += countByRole[decl.role] || 0;
    if (decl.capacity == null) bucket.unbounded = true;
    else bucket.cap += decl.capacity;
  }

  const fixtures = (currentRoom && currentRoom.fixtures) || [];
  const bound = fixtures.filter((f) => f.dev).length;

  const fmt = (b) => `${b.count}/${b.unbounded ? "∞" : b.cap}`;
  return [
    ["Fixtures", `${bound}/${fixtures.length}`],
    ["Scored", fmt(sums.scored)],
    ["Jam", fmt(sums.jam)],
  ];
}

function renderRegistration() {
  const card = document.getElementById("registrationCard");
  clear(card);
  card.appendChild(mk("h3", "railhead", "Registration"));
  for (const [label, count] of rollup()) {
    const wrap = mk("div", "rolerow");
    wrap.appendChild(mk("span", "rolename", label));
    wrap.appendChild(mk("span", "mono count", count));
    card.appendChild(wrap);
  }
}

// -------------------------------------------------------------------- log

const LOG_CAP = 500;

export function logLine(level, message) {
  const card = document.getElementById("logCard");
  let list = card._listEl;
  if (!list) {
    clear(card);
    card.appendChild(mk("h3", "railhead", "Event log"));
    list = mk("div", "loglist mono");
    list.addEventListener("pointerenter", () => { pointerOverLog = true; });
    list.addEventListener("pointerleave", () => { pointerOverLog = false; });
    card.appendChild(list);
    card._listEl = list;
  }

  const stamp = new Date().toTimeString().slice(0, 8);
  const lv = level === "error" ? "err" : level === "warn" ? "warn" : "info";
  const row = mk("div", lv === "err" ? "row err" : "row");
  row.appendChild(mk("span", "dim ts", `[${stamp}]`));
  row.appendChild(mk("span", `lv lv-${lv}`, lv.toUpperCase()));
  row.appendChild(mk("span", "msg", message));
  list.appendChild(row);

  while (list.children.length > LOG_CAP) list.children[0].remove();

  if (!pointerOverLog && typeof list.scrollTop !== "undefined") {
    list.scrollTop = list.scrollHeight;
  }
}

function fmtResult(result) {
  return JSON.stringify(result);
}

// ---------------------------------------------------------------------- init

export function init() {
  wire.on("snapshot", (m) => {
    rolesByName = {};
    for (const role of m.roles || []) rolesByName[role.role] = role;
    registrationRows = m.registration || [];
    currentRoom = m.room || null;
    renderRegistration();
  });
  wire.on("registration_changed", (m) => {
    registrationRows = m.roles || [];
    renderRegistration();
  });
  wire.on("room_changed", (m) => {
    currentRoom = m.room || null;
    renderRegistration();
  });
  wire.on("state_changed", (m) => {
    logLine("info", "state → " + m.state);
  });
  wire.on("log", (m) => {
    logLine(m.level, m.message);
  });
  wire.on("bit_completed", (m) => {
    logLine("info", "bit completed: " + fmtResult(m.result));
  });
}
