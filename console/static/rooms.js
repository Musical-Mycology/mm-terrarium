// Rooms panel: one card per configured room, rendered into #roomsPanel.
// Read model is snapshot.rooms (Task 6): [{name, description, status,
// active}], status is null when loadable, else a reason string.
//
// Rendering discipline (rule 1, same as functions.js): the card list is
// rebuilt only when the declared room table's signature actually changes.
// A room_load_progress event during a load/unload must update ONLY the
// active card's status line -- it must never rebuild the card list (that
// would discard the Unload button's confirm-tap armed state, which
// wire.confirmTap keys off the specific button element).
import * as wire from "./wire.js";

let roomsSignature = null;   // JSON of the last-rendered declaration
let terrariumState = null;   // last-seen terrarium_state
let loadingName = null;      // name of the room a load_room command targeted,
                              // until room_loaded/room_load_failed/room_unloaded
let progressStage = null;    // last room_load_progress stage, shown on loadingName's card
const cardByName = new Map();       // room name -> its card element
const loadBtnByName = new Map();    // room name -> its Load button (test hook)
const unloadBtnByName = new Map();  // room name -> its Unload button (test hook)
const statusLineByName = new Map(); // room name -> its status-line element
const detailByName = new Map();     // room name -> its detail mount (active room only)

// Live detail feeds (snapshot / room_changed / devices_changed). Rendered
// into the active card's detail mount only; rebuilt freely there (no
// buttons live inside it, so confirmTap state is never at risk).
let currentRoom = null;   // room_view payload
let deviceRows = [];      // [{dev, name, role}]
let rolesByName = {};     // role name -> role_view dict, for device tags

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

export function _loadBtnFor(name) {
  return loadBtnByName.get(name);
}

export function _unloadBtnFor(name) {
  return unloadBtnByName.get(name);
}

// -------------------------------------------------------------- rendering

function anyActive(rooms) {
  return rooms.some((r) => r.active);
}

function statusLineText(room) {
  if (loadingName === room.name && progressStage) return progressStage;
  if (room.active) return "Active";
  if (room.status) return room.status;
  return "Loadable";
}

function updateStatusLine(name, rooms) {
  const line = statusLineByName.get(name);
  if (!line) return;
  const room = rooms.find((r) => r.name === name);
  if (!room) return;
  line.textContent = statusLineText(room);
}

// ------------------------------------------------------------ room detail

function deviceTag(dev) {
  const fixtures = (currentRoom && currentRoom.fixtures) || [];
  if (fixtures.some((f) => f.dev === dev.dev)) return ["Fixture", "chip terra"];
  if (!dev.role) return ["Unregistered", "chip dim"];
  const decl = rolesByName[dev.role];
  if (decl && decl.scored) return ["Scored", "chip gold"];
  if (decl && String(decl.class).toLowerCase() === "jam") return ["Jam", "chip sage"];
  const cls = decl ? String(decl.class) : "role";
  return [cls.charAt(0).toUpperCase() + cls.slice(1), "chip dim"];
}

function section(mount, label) {
  mount.appendChild(mk("p", "eyebrow", label));
}

function renderDetail() {
  const active = lastRooms.find((r) => r.active);
  const mount = active && detailByName.get(active.name);
  if (!mount) return;
  clear(mount);
  if (!currentRoom) {
    mount.appendChild(mk("p", "muted", "No live room data yet"));
    return;
  }

  const cap = currentRoom.capability || {};
  section(mount, "Capability");
  mount.appendChild(mk("p", "mono dim",
    `${cap.pixel_count} px · ${cap.color_order}` +
    ((cap.zones || []).length ? ` · ${cap.zones.length} zones` : "")));

  section(mount, "Fixtures & bindings");
  const fixtures = currentRoom.fixtures || [];
  if (!fixtures.length) mount.appendChild(mk("p", "muted", "No fixtures"));
  for (const f of fixtures) {
    const row = mk("div", "devrow");
    row.appendChild(mk("span", "devname", f.name));
    row.appendChild(mk("span", "mono devid", f.dev || "unbound"));
    if (f.pixel_count != null) row.appendChild(mk("span", "mono dim", `${f.pixel_count} px`));
    const zones = (f.zones || []).map((z) => z.name).join(", ");
    if (zones) row.appendChild(mk("span", "mono dim", zones));
    mount.appendChild(row);
  }

  section(mount, "Connected devices");
  if (!deviceRows.length) mount.appendChild(mk("p", "muted", "No devices connected"));
  for (const dev of deviceRows) {
    const row = mk("div", "devrow");
    row.appendChild(mk("span", "mono devid", dev.dev));
    row.appendChild(mk("span", "devname", dev.name));
    if (dev.role) row.appendChild(mk("span", "mono dim", dev.role));
    const [label, chipClass] = deviceTag(dev);
    row.appendChild(mk("span", `${chipClass} roletag`, label));
    mount.appendChild(row);
  }

  section(mount, "Declared instruments");
  const instruments = currentRoom.instruments || [];
  if (!instruments.length) mount.appendChild(mk("p", "muted", "No instruments declared"));
  for (const inst of instruments) {
    const row = mk("div", "devrow");
    row.appendChild(mk("span", "devname", inst.instrument));
    row.appendChild(mk("span", "mono dim", inst.kind));
    if (inst.program !== undefined) row.appendChild(mk("span", "mono dim", `program ${inst.program}`));
    const lanes = (inst.lanes || []).map((l) => `${l.source} → ${l.dest}`).join(" · ");
    if (lanes) row.appendChild(mk("span", "mono dim", lanes));
    mount.appendChild(row);
  }
}

function buildCard(room, rooms) {
  const card = document.createElement("div");
  card.className = "card room-decl";

  const head = mk("div", "roomdeclhead");
  head.appendChild(mk("h3", null, room.name));
  card.appendChild(head);

  if (room.description) card.appendChild(mk("p", null, room.description));

  const statusLine = mk("p", "muted", statusLineText(room));
  card.appendChild(statusLine);
  statusLineByName.set(room.name, statusLine);

  const actions = mk("div", "actions");

  const loadBtn = mk("button", "btn solid-gold", "Load");
  const blocked = anyActive(rooms) || room.status != null;
  loadBtn.disabled = blocked;
  loadBtn.onclick = () => {
    loadingName = room.name;
    progressStage = null;
    wire.send("load_room", { name: room.name }, loadBtn);
  };
  actions.appendChild(loadBtn);
  loadBtnByName.set(room.name, loadBtn);

  if (room.active) {
    const unloadBtn = mk("button", "btn solid-rose", "Unload");
    unloadBtn.onclick = () => {
      wire.confirmTap(unloadBtn, { armLabel: "Confirm unload?" }, () => {
        wire.send("unload_room", { force: true }, unloadBtn);
      });
    };
    actions.appendChild(unloadBtn);
    unloadBtnByName.set(room.name, unloadBtn);
  } else {
    unloadBtnByName.delete(room.name);
  }

  card.appendChild(actions);

  if (room.active) {
    const detail = mk("div", "roomdetail");
    card.appendChild(detail);
    detailByName.set(room.name, detail);
  } else {
    detailByName.delete(room.name);
  }
  return card;
}

function render(rooms) {
  const mount = document.getElementById("roomsPanel");
  clear(mount);
  cardByName.clear();
  loadBtnByName.clear();
  unloadBtnByName.clear();
  statusLineByName.clear();
  detailByName.clear();

  if (!rooms.length) {
    mount.appendChild(mk("p", "muted", "No rooms configured"));
    return;
  }

  const grid = mk("div", "roomsgrid");
  mount.appendChild(grid);
  for (const room of rooms) {
    const card = buildCard(room, rooms);
    grid.appendChild(card);
    cardByName.set(room.name, card);
  }
}

let lastRooms = [];

function onRoomsChanged(rooms) {
  const list = rooms || [];
  lastRooms = list;
  const signature = JSON.stringify(list);
  if (signature === roomsSignature) return;
  roomsSignature = signature;
  render(list);
  renderDetail();
}

function onProgress(stage) {
  progressStage = stage;
  if (!loadingName) return;
  updateStatusLine(loadingName, lastRooms);
}

function clearLoading() {
  loadingName = null;
  progressStage = null;
}

// ---------------------------------------------------------------------- init

export function init() {
  wire.on("snapshot", (m) => {
    terrariumState = m.terrarium_state;
    currentRoom = m.room || null;
    deviceRows = m.devices || [];
    rolesByName = {};
    for (const role of m.roles || []) rolesByName[role.role] = role;
    onRoomsChanged(m.rooms);
    renderDetail();
  });
  wire.on("room_changed", (m) => {
    currentRoom = m.room || null;
    renderDetail();
  });
  wire.on("devices_changed", (m) => {
    deviceRows = m.devices || [];
    renderDetail();
  });
  wire.on("state_changed", (m) => {
    terrariumState = m.terrarium_state;
  });
  wire.on("room_load_progress", (m) => onProgress(m.stage));
  wire.on("room_loaded", () => clearLoading());
  wire.on("room_load_failed", () => clearLoading());
  wire.on("room_unloaded", () => clearLoading());
}
