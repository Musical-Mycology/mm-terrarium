// The full-window 3D bump-out for one fixture: fixture3d.html?fixture=<name>.
// The same view and inspector as the Room card's tile, with the whole window
// to itself. It opens its own socket through wire.js (it does not load
// shell.js), takes the fixture's model from the snapshot's Room, and follows
// room_changed, room_frame (this fixture only) and gesture.
import * as wire from "./wire.js";
import { mk, clear } from "./dom.js";
import { mountInspector } from "./inspector.js";
import { FixtureTrace } from "./led_trace.js";
import { ledAddress, rgbwAt } from "./led_inspect.js";

// view3d.js imports three.js by bare name, so node tests never load it; the
// page imports it on demand, as surface.js does.
async function defaultViewFactory(container, opts) {
  const { createFixtureView } = await import("./view3d.js");
  return createFixtureView(container, opts);
}
let viewFactory = defaultViewFactory;

export function _setViewFactory(fn) {
  viewFactory = fn || defaultViewFactory;
}

// What the view should be, as a key that changes only when it must be
// rebuilt (as surface.js tileKey): null means there is nothing to draw.
function viewKey(fixture) {
  const model = fixture.model || {};
  if (model.status === "ok" && model.sha256) return `ok:${model.sha256}`;
  return null;
}

function absence(name, fixture) {
  if (!fixture) return `${name} is not in the loaded Room`;
  const model = fixture.model || {};
  if (model.status === "over_budget") return model.reason || "the model is over budget";
  return `${name} has no 3D model`;
}

export function startPage({ fixtureName, view: viewEl, inspector: inspectorEl, note: noteEl, title: titleEl }) {
  const name = fixtureName;
  const inspector = mountInspector(inspectorEl);
  let key = null;          // key of the view built (or failed) for the current Room
  let view = null;
  let generation = 0;      // bumps on every rebuild so a stale factory result is dropped
  let trace = null;
  let channels = [];
  let latestT = null;      // latest Control-clock t seen (room_frame or gesture)
  let selected = null;     // {index, layout} or null

  if (titleEl) titleEl.textContent = `${name} · 3D`;

  const say = (msg) => { noteEl.textContent = msg; noteEl.hidden = !msg; };

  function seeT(t) {
    if (typeof t !== "number") return;
    latestT = latestT === null ? t : Math.max(latestT, t);
  }

  function refresh() {
    if (!selected) { inspector.clear(); return; }
    const now = latestT ?? 0;
    inspector.update({
      rgbw: rgbwAt(channels, selected.index),
      samples: trace ? trace.samples(selected.index, now) : [],
      gestures: trace ? trace.gestures(now) : [],
      now,
    });
  }

  function deselect() {
    selected = null;
    if (view) view.select(null);
    inspector.clear();
  }

  function drop() {
    generation += 1;
    if (view) view.dispose();
    view = null;
    key = null;
    trace = null;
    clear(viewEl);
    deselect();
  }

  function pick(index, layout) {
    selected = { index, layout };
    if (view) view.select(index);
    inspector.select({ address: ledAddress(layout, index) });
    refresh();
  }

  function applyRoom(room) {
    const fixture = ((room && room.fixtures) || []).find((f) => f.name === name);
    const next = fixture ? viewKey(fixture) : null;
    if (next === null) {
      if (key !== null || view) drop();
      say(absence(name, fixture));
      return;
    }
    if (next === key) return;          // same model (or the same failure): keep it
    drop();
    key = next;
    trace = new FixtureTrace(fixture.pixel_count);
    say("");
    const mine = generation;
    Promise.resolve()
      .then(() => viewFactory(viewEl, {
        fixture: name, sha256: fixture.model.sha256, onPick: pick,
      }))
      .then((v) => {
        if (mine !== generation) { v.dispose(); return; }
        view = v;
        if (channels.length) v.paint(channels);
        v.select(selected ? selected.index : null);
      }, (err) => {
        if (mine !== generation) return;
        trace = null;
        say((err && err.message) || "the 3D view failed to load");
      });
  }

  wire.on("snapshot", (m) => {
    // A snapshot starts a connection, perhaps to a restarted Control whose
    // clock began again: drop the old time axis, the trace and the selection.
    latestT = null;
    channels = [];
    if (trace) trace = new FixtureTrace(trace.pixelCount);
    deselect();
    applyRoom(m.room);
  });
  wire.on("room_changed", (m) => applyRoom(m.room));
  wire.on("room_frame", (m) => {
    if (m.fixture !== name) return;
    channels = m.channels || [];
    seeT(m.t);
    if (trace && typeof m.t === "number") trace.pushFrame(m.t, channels);
    if (view) view.paint(channels);
    if (selected) refresh();
  });
  wire.on("gesture", (m) => {
    seeT(m.t);
    if (typeof m.t !== "number") return;
    if (trace) trace.pushGesture(m);
    if (selected) refresh();
  });

  return { name };
}

function boot() {
  const query = new URLSearchParams(location.search);
  const fixtureName = query.get("fixture");
  const byId = (id) => document.getElementById(id);
  document.title = `${fixtureName || "Fixture"} · 3D · Terrarium Console`;
  const chip = byId("connChip");
  wire.on("_open", () => { chip.textContent = "connected"; chip.className = "chip ok"; });
  wire.on("_closed", () => { chip.textContent = "reconnecting…"; chip.className = "chip dim"; });
  if (!fixtureName) {
    byId("fixNote").textContent = "No fixture named: open this page from a fixture's ↗ on the Room card.";
    return;
  }
  startPage({
    fixtureName,
    view: byId("fixView"), inspector: byId("fixInspector"),
    note: byId("fixNote"), title: byId("fixTitle"),
  });
  wire.connect();
}

// The page marks its own body; under node there is no such body, so
// importing this module for tests boots nothing.
if (typeof document !== "undefined" && document.body && document.body.dataset
    && document.body.dataset.page === "fixture3d") {
  boot();
}
