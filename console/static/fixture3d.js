// The full-window 3D bump-out for one fixture: fixture3d.html?fixture=<name>.
// The same view and inspector as the Room card's tile, with the whole window
// to itself. It opens its own socket through wire.js (it does not load
// shell.js), takes the fixture's model from the snapshot's Room, and follows
// room_changed, room_frame (this fixture only) and gesture.
import * as wire from "./wire.js";
import { clear } from "./dom.js";
import { mountInspector } from "./inspector.js";
import { FixtureTrace } from "./led_trace.js";
import { ledAddress } from "./led_inspect.js";
import { _setViewFactory as setSharedViewFactory, viewKey as tileKey, createClock,
         freshTrace, refreshInspector as paintInspector, buildView } from "./fixture_live.js";

// The one view factory lives in fixture_live.js; re-exported for the tests.
export const _setViewFactory = setSharedViewFactory;

// This page draws only a modelled fixture: an over-budget one is a note.
function viewKey(fixture) {
  const key = tileKey(fixture);
  return key && key.startsWith("ok:") ? key : null;
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
  const clock = createClock();
  let selected = null;     // {index, layout} or null

  if (titleEl) titleEl.textContent = `${name} · 3D`;

  const say = (msg) => { noteEl.textContent = msg; noteEl.hidden = !msg; };

  function refresh() {
    if (!selected) { inspector.clear(); return; }
    paintInspector(inspector, { channels, trace, index: selected.index, now: clock.now() });
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

  function pick(index, layout, mine) {
    if (mine !== generation) return;   // a callback from a view since replaced
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
    buildView(viewEl, {
      fixture: name,
      sha256: fixture.model.sha256,
      onPick: (index, layout) => pick(index, layout, mine),
      channels: () => channels,
      selectedIndex: () => null,       // selection is always cleared before a rebuild
      isStale: () => mine !== generation,
      onView: (v) => { view = v; },
      onFail: (message) => { trace = null; say(message); },
    });
  }

  wire.on("snapshot", (m) => {
    // A snapshot starts a connection, perhaps to a restarted Control whose
    // clock began again: drop the old time axis, the trace and the selection.
    clock.reset();
    channels = [];
    if (trace) trace = freshTrace(trace);
    deselect();
    applyRoom(m.room);
  });
  wire.on("room_changed", (m) => applyRoom(m.room));
  wire.on("room_frame", (m) => {
    if (m.fixture !== name) return;
    channels = m.channels || [];
    clock.see(m.t);
    if (trace && typeof m.t === "number") trace.pushFrame(m.t, channels);
    if (view) view.paint(channels);
    if (selected) refresh();
  });
  wire.on("gesture", (m) => {
    clock.see(m.t);
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
