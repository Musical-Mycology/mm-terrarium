// Sidebar "Play Metronome" shortcut: one click loads MetronomeBit (into the
// active Room, else the first loadable Room it supports) and runs it as soon
// as it reaches SETUP. The Console's run is an admin start, so it needs no
// joined players: the Room's flsyn plays the clicks on its own.
import * as wire from "./wire.js";
import { mk, clear } from "./dom.js";

const BIT = "MetronomeBit";

let state = "IDLE";
let loadedName = null;
let terrariumState = null;
let rooms = [];
let bitRow = null;      // MetronomeBit's bits_listed row, null if not discovered
let pending = false;    // load_bit sent; run once the Bit reaches SETUP
let btn = null;

// The active Room when one is loaded (switching Rooms needs a restart), else
// the Bit's default Room, else the first loadable Room it supports. Null
// when there is none, with the reason for the button's tooltip.
function targetRoom() {
  const supported = bitRow?.room_types || [];
  const active = rooms.find((r) => r.active);
  if (terrariumState === "ROOM_READY" && active) {
    return supported.includes(active.name)
      ? [active.name, ""]
      : [null, `Metronome does not support Room ${active.name}`];
  }
  const loadable = new Set(rooms.filter((r) => r.status === null).map((r) => r.name));
  const choices = supported.filter((name) => loadable.has(name));
  if (choices.includes(bitRow?.default_room_type)) return [bitRow.default_room_type, ""];
  return choices.length ? [choices[0], ""] : [null, "no configured Room supports Metronome"];
}

function ours() {
  return loadedName === BIT;
}

function update() {
  if (pending && ours() && state === "SETUP") {
    pending = false;
    wire.send("run", {}, btn);
  }
  if (!btn) return;
  const settled = terrariumState === "NO_ROOM" || terrariumState === "ROOM_READY";
  const playing = ours() && state === "RUNNING";
  const ready = state === "IDLE" || (ours() && state === "SETUP");
  const [room, why] = bitRow ? targetRoom() : [null, "MetronomeBit not found"];
  btn.textContent = pending ? "Starting…" : playing ? "Playing…" : "Play Metronome";
  btn.disabled = pending || !bitRow || bitRow.enabled === false || !settled
    || !ready || room === null;
  btn.title = why || (ready || pending ? "" : "another Bit is loaded");
}

function play() {
  if (ours() && state === "SETUP") {
    wire.send("run", {}, btn);
    return;
  }
  pending = true;
  wire.send("load_bit", { name: BIT, overrides: {}, room: targetRoom()[0] }, btn);
  update();
}

export function init() {
  const mount = document.getElementById("quickPlay");
  clear(mount);
  mount.appendChild(mk("p", "navlabel", "Quick Play"));
  btn = mk("button", "btn solid-gold", "Play Metronome");
  btn.type = "button";
  btn.onclick = play;
  mount.appendChild(btn);

  const onState = (m) => {
    state = m.state;
    loadedName = m.loaded_bit;
    terrariumState = m.terrarium_state;
    if (m.rooms) rooms = m.rooms;
    update();
  };
  wire.on("snapshot", onState);
  wire.on("state_changed", onState);
  wire.on("bits_listed", (m) => {
    bitRow = (m.bits || []).find((b) => b.name === BIT) ?? null;
    update();
  });
  wire.on("room_loaded", (m) => {
    rooms = rooms.map((r) => Object.assign({}, r, { active: r.name === m.name }));
    update();
  });
  wire.on("room_unloaded", () => {
    rooms = rooms.map((r) => Object.assign({}, r, { active: false }));
    update();
  });
  wire.on("error", (m) => {
    if (pending && (m.command === "load_bit" || m.command === "run")) {
      pending = false;
      update();
    }
  });
}
