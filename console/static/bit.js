// Sidebar Loaded-Bit panel: identity, phase chip, Run/Abort/Load buttons,
// the Load picker overlay, and the Bit status card.
import * as wire from "./wire.js";

const PHASES = {
  LOADING: ["Loaded", "gold"],
  LOADED: ["Loaded", "gold"],
  SETUP: ["Waiting Room — registration open", "sage"],
  RUNNING: ["Running", "sage"],
  COMPLETING: ["Wrapping up", "gold"],
  UNLOADING: ["Wrapping up", "gold"],
};

let bits = [];            // last bits_listed rows
let errors = [];          // last bits_listed manifest errors
let bitsSignature = null; // rule 1: bits_listed gated by signature
let state = "IDLE";
let loadedName = null;
let terrariumState = null; // gates Load/Run/Abort: only enabled in ROOM_READY

function roomReady() {
  return terrariumState === "ROOM_READY";
}

function startText(start) {
  if (!start) return "—";
  if (start.when === "players") {
    let text = `${start.min_scored} players`;
    if (start.timeout_seconds != null) text += `, ${start.timeout_seconds}s timeout`;
    return text;
  }
  if (start.when === "operator") return "operator";
  if (start.when === "immediate") return "immediately";
  return String(start.when);
}

function rolesText(r) {
  if (!r) return "—";
  const jam = r.jam_open ? "jam open" : "no jam";
  return `${r.scored} scored · ${jam}`;
}

function findBit(name) {
  return bits.find((b) => b.name === name) || null;
}

function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// ---------------------------------------------------------------- #bitPanel

function render() {
  const panel = document.getElementById("bitPanel");
  clear(panel);

  if (!loadedName) {
    const wrap = mk("div", "bitcard empty");
    wrap.appendChild(mk("p", "muted", "No Bit loaded"));
    const loadBtn = mk("button", "btn solid-gold", "Load");
    loadBtn.disabled = !roomReady();
    loadBtn.onclick = openPicker;
    wrap.appendChild(loadBtn);
    panel.appendChild(wrap);
    return;
  }

  const bit = findBit(loadedName);
  const wrap = mk("div", "bitcard");

  // identity row
  const idrow = mk("div", "identity");
  idrow.appendChild(mk("span", "art", "✦"));
  const namewrap = mk("div");
  namewrap.appendChild(mk("h3", null, (bit && bit.display_name) || loadedName));
  const versionSuffix = bit ? ` · v${bit.version}` : "";
  namewrap.appendChild(mk("p", "muted mono", `${loadedName}${versionSuffix}`));
  idrow.appendChild(namewrap);
  if (bit) idrow.appendChild(mk("span", "kind", bit.kind));
  wrap.appendChild(idrow);

  // button row -- Run/Abort/Load, disabled outside ROOM_READY (spec: a Bit
  // cannot run, abort, or be (re)loaded without a Room bound and ready).
  const btnrow = mk("div", "btnrow");
  const gated = !roomReady();

  const runBtn = mk("button", "btn solid-gold", "Run");
  runBtn.disabled = gated;
  runBtn.onclick = () => wire.send("run", {}, runBtn);
  btnrow.appendChild(runBtn);

  const abortBtn = mk("button", "btn solid-rose", "Abort");
  abortBtn.disabled = gated;
  abortBtn.onclick = () => {
    wire.confirmTap(abortBtn, { armLabel: "Confirm abort?" }, () => {
      wire.send("abort", {}, abortBtn);
    });
  };
  btnrow.appendChild(abortBtn);

  const loadBtn = mk("button", "btn", "Load");
  loadBtn.disabled = gated;
  loadBtn.onclick = openPicker;
  btnrow.appendChild(loadBtn);

  wrap.appendChild(btnrow);

  // phase chip
  const [label, tone] = PHASES[state] || [state, "dim"];
  const phase = mk("div", `phase ${tone}`);
  const plabel = mk("div", "p-label");
  plabel.appendChild(mk("span", "dot"));
  plabel.appendChild(mk("span", null, label));
  phase.appendChild(plabel);
  if (state === "SETUP" && bit) {
    phase.appendChild(mk("div", "p-sub", `starts: ${startText(bit.start)}`));
  }
  wrap.appendChild(phase);

  // details
  if (bit) {
    const dl = mk("dl", "detail");
    const addRow = (k, v) => {
      dl.appendChild(mk("dt", null, k));
      dl.appendChild(mk("dd", null, v));
    };
    addRow("Rooms", (bit.room_types || []).join(", ") || "none");
    addRow("Roles", rolesText(bit.roles));
    addRow("About", bit.description || "—");
    addRow("Notes", bit.notes || "—");
    wrap.appendChild(dl);
  }

  panel.appendChild(wrap);
}

// ---------------------------------------------------------------- picker

function closeOverlay() {
  clear(document.getElementById("overlayMount"));
}

function buildOverridesForm() {
  const details = document.createElement("details");
  details.className = "ovr";
  details.appendChild(mk("summary", null, "Overrides"));

  const form = mk("div", "form");
  const pairs = [];
  for (let i = 0; i < 3; i++) {
    const keyInput = document.createElement("input");
    keyInput.setAttribute("placeholder", "key e.g. rhythm.bpm");
    const valInput = document.createElement("input");
    valInput.setAttribute("placeholder", "value");
    form.appendChild(keyInput);
    form.appendChild(valInput);
    pairs.push([keyInput, valInput]);
  }
  details.appendChild(form);
  return { details, pairs };
}

function overridesFromPairs(pairs) {
  const table = {};
  for (const [keyInput, valInput] of pairs) {
    const key = (keyInput.value || "").trim();
    if (!key) continue;
    const raw = valInput.value;
    const num = Number(raw);
    table[key] = raw !== "" && Number.isFinite(num) ? num : raw;
  }
  return { table };
}

function buildPickCard(bitRow) {
  const card = mk("div", bitRow.hidden ? "pick hiddenbit" : "pick");
  card.appendChild(mk("span", "part", "✦"));

  const h3 = document.createElement("h3");
  h3.appendChild(mk("span", null, bitRow.display_name || bitRow.name));
  if (bitRow.name === loadedName) h3.appendChild(mk("span", "chip solid-gold", "Loaded"));
  card.appendChild(h3);

  const roomsText = (bitRow.room_types || []).join(", ") || "none";
  card.appendChild(mk("p", "meta",
    `${bitRow.name} · v${bitRow.version} · rooms: ${roomsText} · starts: ${startText(bitRow.start)} · ${rolesText(bitRow.roles)}`));

  card.appendChild(mk("p", "pdesc", bitRow.description || ""));

  const { details, pairs } = buildOverridesForm();
  card.appendChild(details);

  const actions = mk("div", "actions");
  const loadBtn = mk("button", "btn solid-gold", "Load");
  loadBtn.onclick = () => {
    const overrides = overridesFromPairs(pairs);
    wire.send("load_bit", { name: bitRow.name, overrides }, loadBtn);
    closeOverlay();
  };
  actions.appendChild(loadBtn);
  card.appendChild(actions);

  return card;
}

function buildErrRow(err) {
  const row = mk("div", "pick err");
  row.appendChild(mk("h3", null, err.path));
  row.appendChild(mk("p", "pdesc", err.message));
  return row;
}

function openPicker() {
  const mount = document.getElementById("overlayMount");
  clear(mount);

  const overlay = mk("div", "overlay open");
  overlay.onclick = (e) => { if (e.target === overlay) closeOverlay(); };

  const picker = mk("div", "picker");

  const head = mk("div", "pickhead");
  head.appendChild(mk("h2", null, "Load a Bit"));
  const xbtn = mk("button", "xbtn", "✕");
  xbtn.onclick = closeOverlay;
  head.appendChild(xbtn);
  picker.appendChild(head);

  for (const bitRow of bits) picker.appendChild(buildPickCard(bitRow));
  for (const err of errors) picker.appendChild(buildErrRow(err));

  overlay.appendChild(picker);
  mount.appendChild(overlay);

  const onKey = (e) => {
    if (e.key === "Escape") {
      closeOverlay();
      document.removeEventListener("keydown", onKey);
    }
  };
  document.addEventListener("keydown", onKey);
}

// ---------------------------------------------------------- #bitStatusCard

function fmt(v) {
  if (Array.isArray(v)) return v.map(fmt).join(" ");
  if (typeof v === "number") return String(v);
  if (v !== null && typeof v === "object") {
    return Object.entries(v).map(([k, vv]) => `${k}=${vv}`).join(" ");
  }
  return String(v);
}

function renderStatus(status) {
  const card = document.getElementById("bitStatusCard");
  const entries = Object.entries(status || {});
  clear(card);
  if (entries.length === 0) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const grid = mk("div", "statusgrid");
  for (const [k, v] of entries) {
    const stat = mk("div", "stat");
    stat.appendChild(mk("div", "k", k.toUpperCase()));
    stat.appendChild(mk("div", "v", fmt(v)));
    grid.appendChild(stat);
  }
  card.appendChild(grid);
}

// ------------------------------------------------------------------- init

export function init() {
  wire.on("snapshot", (m) => {
    state = m.state;
    loadedName = m.loaded_bit;
    terrariumState = m.terrarium_state;
    render();
    renderStatus(m.bit_status || {});
  });
  wire.on("bits_listed", (m) => {
    const sig = JSON.stringify([m.bits, m.errors]);
    if (sig === bitsSignature) return;
    bitsSignature = sig;
    bits = m.bits || [];
    errors = m.errors || [];
    render();
  });
  wire.on("state_changed", (m) => {
    state = m.state;
    loadedName = m.loaded_bit;
    terrariumState = m.terrarium_state;
    render();
  });
  wire.on("bit_status", (m) => renderStatus(m.status || {}));
}
