// Room panel: N labelled strips (one per declared fixture), plus one card
// per declared instrument showing its target zone, its lanes and each
// lane's current controller value.
//
// The Room's declared light and audio instruments arrive in ONE list
// discriminated by `kind` (see control/room_view.py). They are rendered
// together on purpose: cc:74 drives aurora's/rainbow's hue and FluidSynth's
// cutoff from one shared MIDI stream, and two separate tables would hide
// that.
//
// One shared LightSession renders the WHOLE concatenated surface every
// tick (control/room_view.py's `fixtures` list carries each fixture's own
// channel_start/channel_count into that one frame); a spatial instrument
// like luxaeterna's rainbow can therefore paint one gradient across every
// fixture. Each fixture's OWN strip is repainted only from ITS OWN
// room_frame event (matched by `dev`, since a frame event names a dev, not
// a fixture) -- see renderRoomFrame below.

let roomFixtureShapes = {};     // fixture name -> last-seen {pixel_count, zones}, PER FIXTURE
let fixtureDevByName = {};      // name -> dev, refreshed every renderRoom call
let fixtureNameByDev = {};      // dev -> name, the reverse lookup renderRoomFrame needs

function fixtureShapeMatches(prev, next) {
  if (!prev) return false;
  if (prev.pixel_count !== next.pixel_count) return false;
  return JSON.stringify(prev.zones) === JSON.stringify(next.zones);
}

function renderRoom(room) {
  const el = document.getElementById("room");

  if (!room) {
    el.innerHTML = "";
    roomFixtureShapes = {};
    fixtureDevByName = {};
    fixtureNameByDev = {};
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No Room configured";
    el.appendChild(p);
    return;
  }

  let header = document.getElementById("roomHeader");
  if (!header) {
    el.innerHTML = "";
    header = document.createElement("p");
    header.id = "roomHeader";
    el.appendChild(header);
  }
  const boundCount = room.fixtures.filter((f) => f.dev).length;
  header.textContent = `${room.room_type} · ${room.capability.pixel_count} px · `
    + `${room.capability.color_order} · `
    + `${boundCount}/${room.fixtures.length} fixture(s) bound`;

  fixtureDevByName = {};
  fixtureNameByDev = {};
  for (const fixture of room.fixtures) {
    fixtureDevByName[fixture.name] = fixture.dev;
    if (fixture.dev) fixtureNameByDev[fixture.dev] = fixture.name;
  }

  let cards = document.getElementById("roomCards");

  // Drop strips/zone-bars for any fixture no longer in the profile (not
  // expected in practice -- room_profile()'s declared fixtures are fixed
  // per RoomType -- but keeps stale nodes from surviving a hypothetical
  // reconfiguration).
  const currentNames = new Set(room.fixtures.map((f) => f.name));
  for (const oldName of Object.keys(roomFixtureShapes)) {
    if (!currentNames.has(oldName)) {
      const oldStrip = document.getElementById(`roomStrip-${oldName}`);
      const oldZones = document.getElementById(`roomZones-${oldName}`);
      if (oldStrip) oldStrip.remove();
      if (oldZones) oldZones.remove();
      delete roomFixtureShapes[oldName];
    }
  }

  // Rebuild only the fixtures whose OWN shape changed -- this is Defect 1's
  // whole point applied at fixture granularity: an untouched fixture's live
  // strip must survive another fixture's reconfiguration, not just survive
  // an unrelated controller-value-only room_changed event. A rebuilt
  // fixture is reinserted immediately before the nearest LATER fixture that
  // still has a node in the DOM (or before #roomCards if it's the last),
  // so declaration order survives a partial rebuild instead of every
  // rebuilt fixture being appended after every untouched one.
  for (let i = 0; i < room.fixtures.length; i++) {
    const fixture = room.fixtures[i];
    const nextShape = { pixel_count: fixture.pixel_count, zones: fixture.zones };
    if (fixtureShapeMatches(roomFixtureShapes[fixture.name], nextShape)) {
      continue;
    }
    const oldStrip = document.getElementById(`roomStrip-${fixture.name}`);
    const oldZones = document.getElementById(`roomZones-${fixture.name}`);
    if (oldStrip) oldStrip.remove();
    if (oldZones) oldZones.remove();

    let anchor = cards;
    for (let j = i + 1; j < room.fixtures.length; j++) {
      const nextStrip = document.getElementById(`roomStrip-${room.fixtures[j].name}`);
      if (nextStrip) {
        anchor = nextStrip;
        break;
      }
    }

    const strip = buildFixtureStrip(fixture);
    const zones = buildFixtureZoneLabels(fixture);
    if (anchor) {
      el.insertBefore(strip, anchor);
      el.insertBefore(zones, anchor);
    } else {
      el.appendChild(strip);
      el.appendChild(zones);
    }
    roomFixtureShapes[fixture.name] = nextShape;
  }

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

function buildFixtureStrip(fixture) {
  const strip = document.createElement("div");
  strip.id = `roomStrip-${fixture.name}`;
  strip.className = "roomFixtureStrip";
  for (let i = 0; i < fixture.pixel_count; i++) {
    strip.appendChild(document.createElement("div"));
  }
  return strip;
}

function buildFixtureZoneLabels(fixture) {
  const bar = document.createElement("div");
  bar.id = `roomZones-${fixture.name}`;
  const label = document.createElement("span");
  label.className = "fixtureLabel";
  label.textContent = `${fixture.name}${fixture.dev ? "" : " (not bound)"}`;
  bar.appendChild(label);
  for (const zone of fixture.zones) {
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

function renderRoomFrame(dev, channels) {
  // A room_frame event names the DEV that produced it, not the fixture --
  // devicelink/agent.py's _render_room() sends one slice per bound fixture's
  // own dev. fixtureNameByDev, rebuilt on every renderRoom(), is the lookup
  // from dev back to which strip to paint. A dev with no matching fixture
  // (unbound, or a frame that raced a Room reconfiguration) is a no-op, not
  // an error -- boundary rule 2, nothing here may propagate a failure.
  const name = fixtureNameByDev[dev];
  if (!name) return;
  const strip = document.getElementById(`roomStrip-${name}`);
  if (!strip) return;
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
