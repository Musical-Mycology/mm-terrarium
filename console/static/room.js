// Room panel: a labelled zone view of the Room's live light, plus one card
// per declared instrument showing its target zone, its lanes and each lane's
// current controller value.
//
// The Room's declared light and audio instruments arrive in ONE list
// discriminated by `kind` (see control/room_view.py). They are rendered
// together on purpose: cc:74 drives aurora's hue and FluidSynth's cutoff from
// one shared MIDI stream, and two separate tables would hide that.

let roomCapability = null;

// A live session measured 1726 room_changed events against 464 room_frame
// events: room_changed fires on every controller value change, while
// room_frame (the thing that actually paints #roomStrip's swatches, via
// renderRoomFrame below) arrives far less often. renderRoom used to start
// with el.innerHTML = "", which threw away #roomStrip and rebuilt it as 60
// fresh black divs on every one of those 1726 calls -- so the freshly
// painted swatches were replaced with black well before the next
// room_frame, and the Room's live light read as permanently off exactly
// when the room was active. The capability (pixel_count, zones) that the
// strip's shape depends on changes almost never -- only on a different Room
// or a reconfigured one -- so the strip is now rebuilt ONLY when that
// changes. Header text and instrument cards are cheap and change on every
// room_changed anyway, so they keep being fully re-rendered.
function capabilityShapeMatches(prev, next) {
  if (!prev) return false;
  if (prev.pixel_count !== next.pixel_count) return false;
  return JSON.stringify(prev.zones) === JSON.stringify(next.zones);
}

function renderRoom(room) {
  const el = document.getElementById("room");

  if (!room) {
    // No Room: tear the whole panel down, including the strip, and reset
    // the tracked capability so a later real Room is treated as new rather
    // than compared against a capability that no longer applies.
    el.innerHTML = "";
    roomCapability = null;
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No Room configured";
    el.appendChild(p);
    return;
  }

  let header = document.getElementById("roomHeader");
  if (!header) {
    // Nothing to reuse yet (first Room since boot, or since the last "No
    // Room configured" state cleared el). Start from a clean panel.
    el.innerHTML = "";
    header = document.createElement("p");
    header.id = "roomHeader";
    el.appendChild(header);
  }
  header.textContent = `${room.room_type} · ${room.capability.pixel_count} px · `
    + `${room.capability.color_order} · `
    + (room.bound_dev ? `bound to ${room.bound_dev}` : "not bound");

  const rebuildStrip = !capabilityShapeMatches(roomCapability, room.capability);
  roomCapability = room.capability;

  let strip = document.getElementById("roomStrip");
  let zones = document.getElementById("roomZones");
  if (rebuildStrip || !strip || !zones) {
    // Only reached when the surface actually changed shape (or doesn't
    // exist yet). This is the only place #roomStrip's nodes are created or
    // discarded -- everywhere else in this function leaves it alone.
    if (strip) strip.remove();
    if (zones) zones.remove();
    strip = buildStrip(room.capability);
    zones = buildZoneLabels(room.capability);
    el.appendChild(strip);
    el.appendChild(zones);
  }

  let cards = document.getElementById("roomCards");
  if (!cards) {
    cards = document.createElement("div");
    cards.id = "roomCards";
    cards.className = "cards";
    el.appendChild(cards);
  }
  cards.innerHTML = "";
  for (const inst of room.instruments) {
    cards.appendChild(buildCard(inst, room.controllers || {}));
  }
  if (room.instruments.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No instruments declared (no Bit loaded).";
    cards.appendChild(p);
  }
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
