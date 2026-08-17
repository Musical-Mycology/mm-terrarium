// Room panel: a labelled zone view of the Room's live light, plus one card
// per declared instrument showing its target zone, its lanes and each lane's
// current controller value.
//
// The Room's declared light and audio instruments arrive in ONE list
// discriminated by `kind` (see control/room_view.py). They are rendered
// together on purpose: cc:74 drives aurora's hue and FluidSynth's cutoff from
// one shared MIDI stream, and two separate tables would hide that.

let roomCapability = null;

function renderRoom(room) {
  const el = document.getElementById("room");
  el.innerHTML = "";
  if (!room) {
    roomCapability = null;
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No Room configured";
    el.appendChild(p);
    return;
  }
  roomCapability = room.capability;

  const header = document.createElement("p");
  header.textContent = `${room.room_type} · ${room.capability.pixel_count} px · `
    + `${room.capability.color_order} · `
    + (room.bound_dev ? `bound to ${room.bound_dev}` : "not bound");
  el.appendChild(header);

  el.appendChild(buildStrip(room.capability));
  el.appendChild(buildZoneLabels(room.capability));

  const cards = document.createElement("div");
  cards.className = "cards";
  for (const inst of room.instruments) {
    cards.appendChild(buildCard(inst, room.controllers || {}));
  }
  if (room.instruments.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No instruments declared (no Bit loaded).";
    cards.appendChild(p);
  }
  el.appendChild(cards);
}

function buildStrip(capability) {
  const strip = document.createElement("div");
  strip.id = "roomStrip";
  for (let i = 0; i < capability.pixel_count; i++) {
    strip.appendChild(document.createElement("div"));
  }
  return strip;
}

function buildZoneLabels(capability) {
  const bar = document.createElement("div");
  bar.id = "roomZones";
  for (const zone of capability.zones) {
    const span = document.createElement("span");
    span.style.flex = `${zone.count} 1 0`;
    span.textContent = `${zone.name} (${zone.start}..${zone.start + zone.count - 1})`;
    bar.appendChild(span);
  }
  return bar;
}

function buildCard(inst, controllers) {
  const card = document.createElement("div");
  card.className = "card";

  const title = document.createElement("h3");
  const badge = document.createElement("span");
  badge.className = "kind" + (inst.kind === "audio" ? " audio" : "");
  badge.textContent = inst.kind;
  title.appendChild(badge);
  title.appendChild(document.createTextNode(inst.instrument));
  card.appendChild(title);

  const dl = document.createElement("dl");
  if (inst.kind === "light") {
    addRow(dl, "target", inst.target);
  }
  if (inst.program !== undefined) addRow(dl, "program", inst.program);
  if (inst.drone !== undefined) addRow(dl, "drone", JSON.stringify(inst.drone));
  if (inst.params && Object.keys(inst.params).length) {
    addRow(dl, "params", JSON.stringify(inst.params));
  }
  for (const lane of inst.lanes || []) {
    const cc = lane.source.startsWith("cc:") ? lane.source.slice(3) : null;
    const live = cc !== null && controllers[cc] !== undefined
      ? ` = ${controllers[cc]}` : "";
    addRow(dl, lane.source, `→ ${lane.dest}${live}`);
  }
  card.appendChild(dl);
  return card;
}

function addRow(dl, term, value) {
  const dt = document.createElement("dt");
  dt.textContent = term;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.appendChild(dt);
  dl.appendChild(dd);
}

function renderRoomFrame(channels) {
  const strip = document.getElementById("roomStrip");
  if (!strip || !roomCapability) return;
  const swatches = strip.children;
  // The wire is GRB, not RGB: control/room_profile.py declares color_order and
  // devicelink ships the channels in that order. Reading them as RGB would
  // render every zone the wrong colour, which is the kind of bug that looks
  // like a lighting design decision.
  for (let i = 0; i < swatches.length; i++) {
    const g = channels[i * 3] || 0;
    const r = channels[i * 3 + 1] || 0;
    const b = channels[i * 3 + 2] || 0;
    swatches[i].style.background = `rgb(${r},${g},${b})`;
  }
}
