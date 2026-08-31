// Entry point: wires the top bar, the disconnect dim, and every panel.
import * as wire from "./wire.js";
import { init as initBit } from "./bit.js";
import { init as initSurface } from "./surface.js";
import { init as initFunctions } from "./functions.js";
import { init as initRail, logLine } from "./rail.js";
import { init as initRooms } from "./rooms.js";

const conn = document.getElementById("connChip");
const roomChip = document.getElementById("roomChip");

const VIEWS = { live: ["viewLive", "navLive"], room: ["viewRoom", "navRoom"], log: ["viewLog", "navLog"] };

export function showView(name) {
  for (const [key, [viewId, navId]] of Object.entries(VIEWS)) {
    const on = key === name;
    document.getElementById(viewId).hidden = !on;
    const btn = document.getElementById(navId);
    btn.className = on ? "navbtn active" : "navbtn";
  }
}

export function paintRoomNav(rooms) {
  const active = (rooms || []).find((r) => r.active);
  document.getElementById("navRoom").textContent =
    `Room: ${active ? active.name : "none"}`;
}

document.getElementById("navLive").onclick = () => showView("live");
document.getElementById("navRoom").onclick = () => showView("room");
document.getElementById("navLog").onclick = () => showView("log");
wire.on("snapshot", (m) => paintRoomNav(m.rooms));

wire.on("_open", () => {
  conn.className = "chip sage";
  conn.textContent = "Connected";
  document.body.classList.remove("dimmed");
});
wire.on("_closed", ({ attempts }) => {
  conn.className = "chip rose";
  conn.textContent = `Disconnected — retrying (${attempts})`;
  document.body.classList.add("dimmed");
});

function paintRoomChip(room) {
  if (!room) { roomChip.hidden = true; return; }
  roomChip.hidden = false;
  const bound = room.fixtures.filter((f) => f.dev).length;
  const total = room.fixtures.length;
  roomChip.className = bound === total ? "chip gold" : "chip terra";
  roomChip.textContent =
    `${room.room_type} · ${bound}/${total} fixtures bound`;
}
wire.on("snapshot", (m) => paintRoomChip(m.room));
wire.on("room_changed", (m) => paintRoomChip(m.room));

wire.on("error", (m) => {
  wire.flashRefusal(m.command, m.message);
  logLine("error", `${m.command}: ${m.message}`);
});

initBit(); initSurface(); initFunctions(); initRail(); initRooms();
wire.connect();
