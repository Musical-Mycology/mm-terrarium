// Sidebar Loaded-Bit panel: identity, phase chip, Run/Restart/Abort/Load icon
// buttons, the Load picker overlay, and the Bit status card.
import * as wire from "./wire.js";
import { buildInstrumentCard } from "./surface.js";

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
let rolesByName = {};      // role name -> role_view() dict, for the Bit Details popup
let rooms = [];            // snapshot.rooms rows: {name, description, status, active}

function roomReady() {
  return terrariumState === "ROOM_READY";
}

// Load is allowed whenever the Terrarium is settled: a Bit can be loaded
// from NO_ROOM (the picker asks for a Room and the agent loads it first)
// or from ROOM_READY. Never mid-transition.
function roomSettled() {
  return terrariumState === "NO_ROOM" || terrariumState === "ROOM_READY";
}

function activeRoomName() {
  const active = rooms.find((r) => r.active);
  return active ? active.name : null;
}

// The Rooms this Bit can run in that terrarium.toml actually defines and
// reports loadable (status null).
function roomChoices(bitRow) {
  const loadable = new Set(rooms.filter((r) => r.status === null).map((r) => r.name));
  return (bitRow.room_types || []).filter((name) => loadable.has(name));
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

const ICONS = {
  run: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>',
  restart: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M8 2.5a5.5 5.5 0 1 1-5.2 3.7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M2.5 2.5v4h4z" fill="currentColor"/></svg>',
  abort: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="3" y="3" width="10" height="10" rx="1.5" fill="currentColor"/></svg>',
  load: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M2 4.5h4l1.5 1.5H14v7H2z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 7.5v4M6 9.5h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
};

function iconBtn(className, label, icon) {
  const b = mk("button", `btn icon ${className}`);
  b.type = "button";
  b.setAttribute("aria-label", label);
  b.title = label;
  b.innerHTML = ICONS[icon];
  return b;
}

// ---------------------------------------------------------------- #bitPanel

// Rendering discipline (rule 1, same as rooms.js/surface.js): the panel's
// structural DOM -- which buttons exist -- is rebuilt only when the loaded
// bit's identity actually changes (panelSignatureFor). An unrelated
// snapshot/state_changed/room_* tick (a phase transition, a status poll, a
// round auto-completing) must never recreate the Run/Restart/Abort/Load
// buttons, or it would silently discard whichever one is mid confirm-tap
// (wire.confirmTap keys its armed/timer state off the specific button
// element). Phase chip text and disabled/gated state DO change on every
// such tick, so those are updated in place instead.
let panelSignature = null;  // identity of what's currently built
let panelEmpty = null;      // true/false once built; null before first render

let elDetailsPill = null;
let elRunBtn = null;
let elRestartBtn = null;
let elAbortBtn = null;
let elLoadBtn = null;       // the loaded-state Load button
let elEmptyLoadBtn = null;  // the empty-state Load button
let elPhase = null;
let elPhaseLabel = null;
let elPhaseSub = null;

function panelSignatureFor() {
  const bit = loadedName ? findBit(loadedName) : null;
  return JSON.stringify([
    loadedName,
    bit ? [bit.display_name, bit.kind, bit.version, bit.start] : null,
  ]);
}

function buildEmptyPanel() {
  const panel = document.getElementById("bitPanel");
  clear(panel);
  const wrap = mk("div", "bitcard empty");
  wrap.appendChild(mk("p", "muted", "No Bit loaded"));
  const loadBtn = mk("button", "btn solid-gold", "Load");
  loadBtn.onclick = openPicker;
  wrap.appendChild(loadBtn);
  panel.appendChild(wrap);

  elEmptyLoadBtn = loadBtn;
  elDetailsPill = elRunBtn = elRestartBtn = elAbortBtn = elLoadBtn = null;
  elPhase = elPhaseLabel = elPhaseSub = null;
  panelEmpty = true;
}

function buildLoadedPanel() {
  const panel = document.getElementById("bitPanel");
  clear(panel);
  const bit = findBit(loadedName);
  const wrap = mk("div", "bitcard");

  // identity: icon + title one row, version + details pill beneath
  const idrow = mk("div", "bitname-row");
  idrow.appendChild(mk("span", "art", "✦"));
  const h3 = mk("h3", null, (bit && bit.display_name) || loadedName);
  idrow.appendChild(h3);
  if (bit) idrow.appendChild(mk("span", "kind", bit.kind));
  wrap.appendChild(idrow);

  const versionSuffix = bit ? ` · v${bit.version}` : "";
  wrap.appendChild(mk("p", "bitversion", `${loadedName}${versionSuffix}`));

  const detailsPill = mk("button", "pill", "Bit Details");
  detailsPill.onclick = () => openDetails(bit);
  wrap.appendChild(detailsPill);

  // Run/Restart/Abort need ROOM_READY; Load only needs a settled Terrarium (see roomSettled).
  const btnrow = mk("div", "btnrow icons");
  const note = mk("div", "confirm-note", "click again to confirm");
  note.hidden = true;
  const showNote = () => { note.hidden = false; };
  const hideNote = () => { note.hidden = true; };

  const runBtn = iconBtn("solid-gold", "Run", "run");
  runBtn.onclick = () => wire.send("run", {}, runBtn);
  btnrow.appendChild(runBtn);

  const restartBtn = iconBtn("outline", "Restart", "restart");
  restartBtn.onclick = () => {
    wire.confirmTap(restartBtn, { armStyle: "fill", onArm: showNote, onDisarm: hideNote }, () => {
      wire.send("restart", {}, restartBtn);
    });
  };
  btnrow.appendChild(restartBtn);

  const abortBtn = iconBtn("solid-rose", "Abort", "abort");
  abortBtn.onclick = () => {
    wire.confirmTap(abortBtn, { armStyle: "fill", onArm: showNote, onDisarm: hideNote }, () => {
      wire.send("abort", {}, abortBtn);
    });
  };
  btnrow.appendChild(abortBtn);

  const loadBtn = iconBtn("outline", "Load a Bit", "load");
  loadBtn.onclick = openPicker;
  btnrow.appendChild(loadBtn);

  wrap.appendChild(btnrow);
  wrap.appendChild(note);

  // phase chip (label/tone/sub filled in by updatePanelDynamic)
  const phase = mk("div", "phase");
  const plabel = mk("div", "p-label");
  plabel.appendChild(mk("span", "dot"));
  const phaseLabel = mk("span", null, "");
  plabel.appendChild(phaseLabel);
  phase.appendChild(plabel);
  wrap.appendChild(phase);

  panel.appendChild(wrap);

  elDetailsPill = detailsPill;
  elRunBtn = runBtn;
  elRestartBtn = restartBtn;
  elAbortBtn = abortBtn;
  elLoadBtn = loadBtn;
  elEmptyLoadBtn = null;
  elPhase = phase;
  elPhaseLabel = phaseLabel;
  elPhaseSub = null;
  panelEmpty = false;
}

// Updates the parts of the panel that legitimately change on every tick
// (phase chip, gated/disabled state) without touching button identity.
function updatePanelDynamic() {
  if (panelEmpty) {
    if (elEmptyLoadBtn) elEmptyLoadBtn.disabled = !roomSettled();
    return;
  }
  if (!elRunBtn) return;

  const gated = !roomReady();
  elRunBtn.disabled = gated;
  elRestartBtn.disabled = gated;
  elAbortBtn.disabled = gated;
  elLoadBtn.disabled = !roomSettled();

  const [label, tone] = PHASES[state] || [state, "dim"];
  elPhase.className = `phase ${tone}`;
  elPhaseLabel.textContent = label;

  const bit = loadedName ? findBit(loadedName) : null;
  if (state === "SETUP" && bit) {
    if (!elPhaseSub) {
      elPhaseSub = mk("div", "p-sub");
      elPhase.appendChild(elPhaseSub);
    }
    elPhaseSub.textContent = `starts: ${startText(bit.start)}`;
  } else if (elPhaseSub) {
    elPhaseSub.remove();
    elPhaseSub = null;
  }
}

function render() {
  const sig = panelSignatureFor();
  if (sig !== panelSignature) {
    panelSignature = sig;
    if (loadedName) buildLoadedPanel();
    else buildEmptyPanel();
  }
  updatePanelDynamic();
}

// ------------------------------------------------------------ Bit Details

function manifestInstruments(role) {
  const out = [];
  for (const inst of (role.light_manifest && role.light_manifest.instruments) || []) {
    out.push(Object.assign({ kind: "light" }, inst));
  }
  for (const inst of (role.ugen_manifest && role.ugen_manifest.instruments) || []) {
    out.push(Object.assign({ kind: "audio" }, inst));
  }
  return out;
}

function buildRefCard(role) {
  const details = document.createElement("details");
  details.className = "refcard";

  const summary = document.createElement("summary");
  summary.appendChild(mk("span", "tri", "▸"));
  summary.appendChild(document.createTextNode(role.role));
  summary.appendChild(mk("span", "chip dim classtag", role.class));
  if (role.scored) summary.appendChild(mk("span", "chip gold scoredtag", "scored"));
  details.appendChild(summary);

  const body = mk("div", "accbody");
  if (role.requires) {
    const caps = (role.requires.capabilities || []).join(", ");
    const reqText = caps ? `${role.requires.slot} (${caps})` : role.requires.slot;
    body.appendChild(mk("p", "muted requires", `requires — ${reqText}`));
  }
  if (role.welcome) {
    const welcomeText = Object.entries(role.welcome)
      .map(([k, v]) => `${k}: ${v && v.instrument ? v.instrument : JSON.stringify(v)}`)
      .join(" · ");
    body.appendChild(mk("p", "muted welcome", `welcome — ${welcomeText}`));
  }

  const instruments = manifestInstruments(role);
  if (!instruments.length) {
    body.appendChild(mk("p", "muted", "No manifest declared"));
  } else {
    const grid = mk("div", "instgrid");
    for (const inst of instruments) grid.appendChild(buildInstrumentCard(inst, {}));
    body.appendChild(grid);
  }
  details.appendChild(body);
  return details;
}

function openDetails(bit) {
  const mount = document.getElementById("overlayMount");
  clear(mount);
  const overlay = mk("div", "overlay open");
  overlay.onclick = (e) => { if (e.target === overlay) closeOverlay(); };
  const picker = mk("div", "picker");

  const head = mk("div", "pickhead");
  head.appendChild(mk("h2", null, "Bit Details"));
  const xbtn = mk("button", "xbtn", "✕");
  xbtn.onclick = closeOverlay;
  head.appendChild(xbtn);
  picker.appendChild(head);

  const dl = mk("dl", "detail");
  const addRow = (k, v) => { dl.appendChild(mk("dt", null, k)); dl.appendChild(mk("dd", null, v)); };
  addRow("Rooms", (bit && bit.room_types || []).join(", ") || "none");
  addRow("Roles", rolesText(bit && bit.roles));
  addRow("About", (bit && bit.description) || "—");
  addRow("Notes", (bit && bit.notes) || "—");
  picker.appendChild(dl);

  for (const name of Object.keys(rolesByName)) {
    picker.appendChild(buildRefCard(rolesByName[name]));
  }
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

// Build the nested overrides dict merge_overrides expects:
// "rhythm.bpm" -> {rhythm: {bpm: 100}}. Returns {overrides} (null when the
// form is empty) or {error} naming the first malformed key. A key with no
// dot is refused here: every override lives in a table, and the old
// {table: {...}} wrapper this replaces made EVERY console load fail with
// "[table] unknown override table" (live log 2026-08-31 11:39:25).
function overridesFromPairs(pairs) {
  const overrides = {};
  let any = false;
  for (const [keyInput, valInput] of pairs) {
    const key = (keyInput.value || "").trim();
    if (!key) continue;
    const dot = key.indexOf(".");
    if (dot <= 0 || dot === key.length - 1) return { error: key };
    const table = key.slice(0, dot);
    const field = key.slice(dot + 1);
    const raw = valInput.value;
    const num = Number(raw);
    if (!(table in overrides)) overrides[table] = {};
    overrides[table][field] = raw !== "" && Number.isFinite(num) ? num : raw;
    any = true;
  }
  return { overrides: any ? overrides : null };
}

function flashError(el, message) {
  el.title = message;
  el.classList.add("err");
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

  // Room choice (spec 2026-09-10 section 4): the Bit's room_types that
  // the config defines and reports loadable, preselecting the active Room
  // when compatible, else the Bit's own default.
  const choices = roomChoices(bitRow);
  const active = activeRoomName();
  const roomRow = mk("div", "roomrow");
  roomRow.appendChild(mk("span", "meta", "Room"));
  const select = document.createElement("select");
  select.className = "roompick";
  for (const name of choices) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
  if (choices.includes(active)) select.value = active;
  else if (choices.includes(bitRow.default_room_type)) select.value = bitRow.default_room_type;
  roomRow.appendChild(select);
  card.appendChild(roomRow);
  const hint = mk("p", "meta roomhint", "");
  card.appendChild(hint);
  const actions = mk("div", "actions");
  const loadBtn = mk("button", "btn solid-gold", "Load");
  loadBtn.onclick = () => {
    const result = overridesFromPairs(pairs);
    if (result.error !== undefined) {
      flashError(loadBtn, `override key must be table.key (got "${result.error}")`);
      return;
    }
    wire.send("load_bit", { name: bitRow.name, overrides: result.overrides, room: select.value }, loadBtn);
    closeOverlay();
  };
  const paintHint = () => {
    if (choices.length === 0) hint.textContent = "no configured room supports this Bit";
    else if (active === null && choices.length > 0) hint.textContent = "loads Room: Arco starts (about 15 s)";
    else if (select.value !== active) hint.textContent = `switching Rooms needs a restart: ./terrarium.sh --room ${select.value}`;
    else hint.textContent = "";
    loadBtn.disabled = choices.length === 0 || (active !== null && select.value !== active);
  };
  select.onchange = paintHint;
  paintHint();

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

  for (const bitRow of bits) {
    if (bitRow.enabled === false) continue;
    picker.appendChild(buildPickCard(bitRow));
  }
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
    rooms = m.rooms || [];
    rolesByName = {};
    for (const role of m.roles || []) rolesByName[role.role] = role;
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
  wire.on("room_loaded", (m) => {
    rooms = rooms.map((r) => Object.assign({}, r, { active: r.name === m.name }));
    // Cards capture the active room at build time, so close and rebuild the picker.
    closeOverlay();
    render();
  });
  wire.on("room_unloaded", () => {
    rooms = rooms.map((r) => Object.assign({}, r, { active: false }));
    // Cards capture the active room at build time, so close and rebuild the picker.
    closeOverlay();
    render();
  });
  wire.on("room_load_failed", () => {
    // Cards capture the active room at build time, so close and rebuild the picker.
    closeOverlay();
    render();
  });
}
