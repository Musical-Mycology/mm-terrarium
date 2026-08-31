// Right rail: Registration, Devices, Event log. Three independent
// renderers sharing nothing but wire.js.
import * as wire from "./wire.js";

let rolesByName = {};        // role name -> role_view() dict, used for registration tags
let registrationRows = [];   // last registration_changed/snapshot rows: {role, count, capacity}
let deviceRows = [];         // last devices_changed/snapshot rows: {dev, name, role}
let fixtureDevs = new Set(); // devs bound to a room fixture, for the "Fixture" tag
let instPullOpen = false;    // Instruments pull open/closed, preserved across rebuilds
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

// ---------------------------------------------------------------- rolerows

function deviceTag(dev) {
  if (fixtureDevs.has(dev.dev)) return ["Fixture", "chip terra"];
  if (!dev.role) return ["Unregistered", "chip dim"];
  const decl = rolesByName[dev.role];
  if (decl && decl.scored) return ["Scored", "chip gold"];
  if (decl && decl.class === "jam") return ["Jam", "chip sage"];
  const cls = decl ? decl.class : "role";
  return [cls.charAt(0).toUpperCase() + cls.slice(1), "chip dim"];
}

function renderRegistration() {
  const card = document.getElementById("registrationCard");
  clear(card);
  card.appendChild(mk("h3", "railhead", "Registration"));

  if (!registrationRows.length) {
    card.appendChild(mk("p", "muted", "No roles"));
  } else {
    for (const row of registrationRows) {
      const decl = rolesByName[row.role];
      const wrap = mk("div", "rolerow");

      const top = mk("div", "rolerow-top");
      top.appendChild(mk("span", "rolename", row.role));
      if (decl) top.appendChild(mk("span", "chip dim classtag", decl.class));
      if (decl && decl.scored) top.appendChild(mk("span", "chip gold scoredtag", "scored"));
      wrap.appendChild(top);

      const capIsBounded = row.capacity != null;
      const countText = capIsBounded ? `${row.count}/${row.capacity}` : `${row.count}/∞`;
      if (capIsBounded) {
        const meter = mk("div", "meter");
        const fill = mk("div", "meter-fill");
        const pct = row.capacity > 0 ? Math.min(100, (row.count / row.capacity) * 100) : 100;
        fill.style.width = `${pct}%`;
        meter.appendChild(fill);
        wrap.appendChild(meter);
      }
      wrap.appendChild(mk("span", "mono count", countText));

      card.appendChild(wrap);
    }
  }

  const pull = document.createElement("details");
  pull.className = "acc instpull";
  const summary = document.createElement("summary");
  summary.appendChild(mk("span", "tri", "▸"));
  summary.appendChild(document.createTextNode(`Instruments (${deviceRows.length})`));
  pull.appendChild(summary);
  const body = mk("div", "accbody");
  if (!deviceRows.length) body.appendChild(mk("p", "muted", "No instruments connected"));
  for (const dev of deviceRows) {
    const row = mk("div", "devrow");
    row.appendChild(mk("span", "mono devid", dev.dev));
    row.appendChild(mk("span", "devname", dev.name));
    const [label, chipClass] = deviceTag(dev);
    row.appendChild(mk("span", `${chipClass} roletag`, label));
    body.appendChild(row);
  }
  pull.appendChild(body);
  if (instPullOpen) pull.setAttribute("open", "");
  pull.addEventListener("toggle", () => { instPullOpen = pull.open; });
  card.appendChild(pull);
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
    deviceRows = m.devices || [];
    fixtureDevs = new Set(((m.room && m.room.fixtures) || []).map((f) => f.dev).filter(Boolean));
    renderRegistration();
  });
  wire.on("registration_changed", (m) => {
    registrationRows = m.roles || [];
    renderRegistration();
  });
  wire.on("devices_changed", (m) => {
    deviceRows = m.devices || [];
    renderRegistration();
  });
  wire.on("room_changed", (m) => {
    fixtureDevs = new Set(((m.room && m.room.fixtures) || []).map((f) => f.dev).filter(Boolean));
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
