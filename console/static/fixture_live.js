// What the Room card's 3D tiles (surface.js) and the bump-out page
// (fixture3d.js) share, so the two cannot drift apart: the view loader, the
// model key, the Control-clock `now`, the inspector refresh and the build of
// one view. No DOM of its own and no wire.

import { FixtureTrace } from "./led_trace.js";
import { rgbwAt } from "./led_inspect.js";

// view3d.js imports three.js by bare name, so it is loaded only when a view
// is first needed: node tests never touch it, and a page's first paint never
// waits on the three.js download.
async function defaultViewFactory(container, opts) {
  const { createFixtureView } = await import("./view3d.js");
  return createFixtureView(container, opts);
}
let viewFactory = defaultViewFactory;

export function _setViewFactory(fn) {
  viewFactory = fn || defaultViewFactory;
}

// What a fixture's view should be, as a key that changes only when the view
// must be rebuilt: a modelled fixture by its model's sha256, an over-budget
// one by its reason. null means no view (no model).
export function viewKey(fixture) {
  const model = fixture.model || {};
  if (model.status === "ok" && model.sha256) return `ok:${model.sha256}`;
  if (model.status === "over_budget") return `over:${model.reason || ""}`;
  return null;
}

// The latest Control-clock t a page has seen (room_frame or gesture), never
// the browser's clock. A snapshot may come from a restarted Control whose
// clock began again, so reset() drops the axis.
export function createClock() {
  let latest = null;
  return {
    see(t) {
      if (typeof t !== "number") return;
      latest = latest === null ? t : Math.max(latest, t);
    },
    now() { return latest ?? 0; },
    reset() { latest = null; },
  };
}

// A snapshot starts a connection: every trace restarts empty.
export function freshTrace(trace) {
  return new FixtureTrace(trace.pixelCount);
}

// Values only (the address rows were built by the pick): one LED's current
// RGBW, its trace and the gestures, at the clock's now.
export function refreshInspector(inspector, { channels, trace, index, now }) {
  inspector.update({
    rgbw: rgbwAt(channels || [], index),
    samples: trace ? trace.samples(index, now) : [],
    gestures: trace ? trace.gestures(now) : [],
    now,
  });
}

// Builds one view. `isStale()` is asked when the factory settles: a stale
// result is disposed. On success `onView(view)` runs after the last channels
// are painted and the selection (`selectedIndex()`) is restored; on failure
// `onFail(message)` runs.
export function buildView(container, { fixture, sha256, onPick, channels, selectedIndex,
                                       isStale, onView, onFail }) {
  return Promise.resolve()
    .then(() => viewFactory(container, { fixture, sha256, onPick }))
    .then((view) => {
      if (isStale()) { view.dispose(); return; }
      const last = channels();
      if (last && last.length) view.paint(last);
      view.select(selectedIndex());
      onView(view);
    }, (err) => {
      if (isStale()) return;
      onFail((err && err.message) || "the 3D view failed to load");
    });
}
