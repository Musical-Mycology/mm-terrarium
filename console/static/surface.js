// Room card: LED surface as discrete per-pixel dot rows (one per physical
// block), zone bar, binding controls, the Triggers accordion shell, and the
// Live values lane table. Per-fixture rebuild discipline (spec section 6, rules
// 1/2/3/4/6/9) is the load-bearing part of this file -- a controllers-only
// room_changed must repaint nothing but live lane values; a room_frame must
// only ever repaint a canvas, never rebuild DOM.
//
// Everything frame-related is keyed by fixture NAME, not by dev: every
// fixture in the Room profile renders whether or not a device is bound to
// it, so an UNBOUND fixture gets a strip and paints its frames like any
// other. The dev is still SHOWN (the binding chip), it is just not an
// identity anything here is keyed on.
//
// A fixture whose model.status is "ok" also gets a 3D tile in a row above
// the strips, with one LED inspector to the right of the tiles. Tiles are
// keyed by fixture name + model sha256 and follow the same rule 1: a
// room_changed that leaves both unchanged never rebuilds one (a WebGL
// context and a model download per controller tick would be ruinous). The
// strip is always built first; the 3D view arrives when it is ready, and a
// view that fails leaves the strip with a one-line reason on the tile.
import * as wire from "./wire.js";
import { mk, clear } from "./dom.js";
import { decodeRgbw } from "./pixels.js";
import { FixtureTrace } from "./led_trace.js";
import { ledAddress, rgbwAt } from "./led_inspect.js";
import { mountInspector } from "./inspector.js";

const FRAME_LIVE_MS = 2000;
const PIX_PER_BLOCK = 144;
const SINGLE_ROW_MAX = 160;

let currentRoom = null;              // last-seen room (or null)
let fixtureShapes = {};              // fixture name -> last-seen {pixel_count, zones}
let canvasesByName = {};             // fixture name -> [canvas, ...] (in pixel order across rows)
let lastPaintByName = {};            // fixture name -> [[r,g,b], ...] per pixel
let lastFrameAt = {};                // fixture name -> ms timestamp of last room_frame
let armedFixtures = new Set();       // fixture names showing "Armed" until a device binds or the arm_room is refused
let pendingArm = null;               // fixture last sent arm_room; rolled back on its refusal
let lastChannelsByName = {};         // fixture name -> last room_frame channels (raw RGBW)
let traceByName = {};                // fixture name -> FixtureTrace, for tiled fixtures
let latestT = null;                  // latest Control-clock t seen (room_frame or gesture)
let selectedLed = null;              // {name, index, layout} picked on a tile, or null

// view3d.js imports three.js by bare name, so it is loaded only when the
// first tile needs it: node tests never touch it, and the Console's first
// paint never waits on the three.js download.
async function defaultViewFactory(container, opts) {
  const { createFixtureView } = await import("./view3d.js");
  return createFixtureView(container, opts);
}
let viewFactory = defaultViewFactory;

export function _setViewFactory(fn) {
  viewFactory = fn || defaultViewFactory;
}

// Structural elements this module owns, cached as module state rather than
// re-located via getElementById -- the DOM stub (and, harmlessly, real
// DOM) auto-vivifies unknown ids there, so "does it already exist" can
// only be answered by remembering the reference ourselves.
let headEl = null;
let bodyEl = null;
let fixturesMountEl = null;
let viewRowEl = null;                // tiles + inspector, above the strips
let tilesEl = null;
let inspectorEl = null;
let inspector = null;                // mountInspector() handle
let tileByName = new Map();          // fixture name -> {key, el, viewEl, popout, view, disposed}
let framesChipEl = null;
let instAccEl = null;
let instSummaryMetaEl = null;
let instMountEl = null;
let functionsAccEl = null;           // created once, outside the per-fixture rebuild path
let fixtureElByName = new Map();     // fixture name -> its .fixture wrapper element
let muteChipByName = new Map();      // fixture name -> its "Muted" chip, toggled in place (rule 1)
let bindStateByName = new Map();     // fixture name -> last-rendered binding-state key
let laneTableEl = null;              // <table class="lanes"> inside instMountEl
let laneRowBySource = new Map();     // lane source ("cc:74") -> its <tr>
let laneValueBySource = new Map();   // lane source -> its value <td>
let lanesSignature = null;           // JSON of the last-rendered instruments list
let emptyMsgEl = null;               // "No voices declared" <p>, when instruments is empty

// ------------------------------------------------------------ persistence

const LIVE_VALUES_KEY = "terrarium.liveValuesOpen";

// Guarded with `typeof window` (matching syncCanvasBackingSize's dpr check
// below) rather than `globalThis.localStorage` -- Node's own experimental
// global localStorage stub emits an ExperimentalWarning on first touch when
// no --localstorage-file is given, which would dirty every test run's
// stderr even inside a try/catch (a warning isn't a thrown exception). No
// real browser lacks `window`, so this is a no-op guard there.
function readLiveValuesOpen() {
  try {
    return typeof window !== "undefined" && window.localStorage
      && window.localStorage.getItem(LIVE_VALUES_KEY) === "1";
  } catch (e) { return false; }
}

function writeLiveValuesOpen(open) {
  try {
    if (typeof window !== "undefined" && window.localStorage) {
      window.localStorage.setItem(LIVE_VALUES_KEY, open ? "1" : "0");
    }
  } catch (e) { /* private window or blocked storage: keep the default */ }
}

// ------------------------------------------------------------ block rows

// Pure: splits a fixture into rows -- one per 144 px, or a single row when
// pixel_count <= 160. Not on the wire; block *names* aren't in room_view,
// so labels are honest "px start..end" ranges rather than invented m1/m2
// names.
export function _blockRowsFor(fixture) {
  const n = fixture.pixel_count;
  if (n <= SINGLE_ROW_MAX) {
    return [{ label: `px 0..${n - 1}`, start: 0, count: n }];
  }
  const rows = [];
  let start = 0;
  while (start < n) {
    const count = Math.min(PIX_PER_BLOCK, n - start);
    rows.push({ label: `px ${start}..${start + count - 1}`, start, count });
    start += count;
  }
  return rows;
}

// -------------------------------------------------------------- test hooks

export function _canvasFor(name) {
  return canvasesByName[name];
}

export function _lastPaint(name) {
  return lastPaintByName[name];
}

// Binding-controls span (the chip + Release/Arm button) for a fixture, so
// tests can assert its DOM node identity survives a controllers-only
// room_changed (rule 1) instead of being silently replaced.
export function _bindCtlFor(name) {
  const wrap = fixtureElByName.get(name);
  if (!wrap) return undefined;
  const head = wrap.children[0];
  return head && head.children[1];
}

// The fixture head's "Muted" chip, so tests can assert it toggles in place.
export function _muteChipFor(name) {
  return muteChipByName.get(name);
}

export function _tileFor(name) {
  const entry = tileByName.get(name);
  return entry ? entry.el : undefined;
}

export function _traceFor(name) {
  return traceByName[name];
}

export function _inspectorEl() {
  return inspectorEl;
}

// -------------------------------------------------------------- painting

function syncCanvasBackingSize(canvas) {
  // Backing store follows the CSS-laid-out .strip size * DPR, re-synced on
  // every paint so a window resize (or the row's first-ever layout) is
  // picked up without a separate ResizeObserver. clientWidth/clientHeight
  // are 0 in the node DOM stub, in which case we leave whatever width/
  // height the canvas already has (the stub's default 0x0 -- fine, the
  // stub's getContext is a no-op anyway).
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  if (!cw || !ch) return;
  const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
  const targetW = Math.round(cw * dpr);
  const targetH = Math.round(ch * dpr);
  if (canvas.width !== targetW) canvas.width = targetW;
  if (canvas.height !== targetH) canvas.height = targetH;
}

function paintCanvas(canvas, pixels) {
  syncCanvasBackingSize(canvas);
  const ctx = canvas.getContext("2d");
  const w = canvas.width || canvas.clientWidth || 0;
  const h = canvas.height || canvas.clientHeight || 0;
  ctx.clearRect(0, 0, w, h);
  const n = pixels.length;
  if (n === 0) return;
  const pitch = w / n;
  const r = Math.min(h / 2, (pitch / 2) * 0.72);
  for (let i = 0; i < n; i++) {
    const [red, green, blue] = pixels[i];
    const cx = pitch * (i + 0.5);
    const cy = h / 2;
    ctx.beginPath();
    if (red + green + blue < 12) {
      ctx.strokeStyle = "rgba(255,219,204,0.25)";
      ctx.lineWidth = 1;
      ctx.arc(cx, cy, r * 0.8, 0, 6.283);
      ctx.stroke();
    } else {
      ctx.fillStyle = `rgb(${red},${green},${blue})`;
      ctx.arc(cx, cy, r, 0, 6.283);
      ctx.fill();
    }
  }
}

function repaintFixture(name) {
  const canvases = canvasesByName[name];
  const pixels = lastPaintByName[name];
  if (!canvases || !pixels) return;
  let offset = 0;
  for (const canvas of canvases) {
    const count = canvas._pixelCount;
    paintCanvas(canvas, pixels.slice(offset, offset + count));
    offset += count;
  }
}

// -------------------------------------------------------------- binding

function bindingControls(fixture) {
  const wrap = document.createElement("span");
  wrap.className = "bindctl";

  if (fixture.dev) {
    armedFixtures.delete(fixture.name);
    // With a device canvas URL the chip itself is the link to it; the ↗
    // belongs to the fixture's 3D tile (the bump-out view).
    const chip = mk(fixture.url ? "a" : "span", "chip sage", fixture.dev);
    if (fixture.url) {
      chip.href = fixture.url;
      chip.target = "_blank";
      chip.rel = "noopener";
      chip.title = `Open ${fixture.dev}'s canvas`;
    }
    wrap.appendChild(chip);
    const releaseBtn = mk("button", "btn outline small", "Release");
    wire.reserveConfirmWidth(releaseBtn, "Confirm release?");
    releaseBtn.onclick = () => {
      wire.confirmTap(releaseBtn, { armLabel: "Confirm release?" }, () => {
        wire.send("release_room", { room_type: currentRoom.room_type, fixture: fixture.name }, releaseBtn);
      });
    };
    wrap.appendChild(releaseBtn);
    return wrap;
  }

  // An [[artnet]]-covered fixture is driven by its Art-Net sink and never
  // binds a device (the server refuses arming it), so there is no Arm.
  if (fixture.artnet) {
    const chip = mk("span", "chip sage", "Art-Net");
    chip.title = "Driven by [[artnet]]; never binds a device";
    wrap.appendChild(chip);
    return wrap;
  }

  if (armedFixtures.has(fixture.name)) {
    const chip = mk("span", "chip gold");
    chip.appendChild(mk("span", "dot"));
    chip.appendChild(document.createTextNode("Armed"));
    wrap.appendChild(chip);
    return wrap;
  }

  const chip = mk("span", "chip terra", "Not bound");
  wrap.appendChild(chip);
  const armBtn = mk("button", "btn outline small", "Arm");
  const formRow = mk("span", "armrow");
  formRow.hidden = true;
  const label = mk("span", "mono dim", "window: ");
  const input = document.createElement("input");
  input.setAttribute("value", "30");
  input.value = "30";
  const sLabel = mk("span", "mono dim", "s");
  const confirmBtn = mk("button", "btn outline small", "Confirm");
  formRow.appendChild(label);
  formRow.appendChild(input);
  formRow.appendChild(sLabel);
  formRow.appendChild(confirmBtn);

  armBtn.onclick = () => { formRow.hidden = false; };
  confirmBtn.onclick = () => {
    const windowSeconds = Number(input.value) || 30;
    pendingArm = fixture.name;
    wire.send("arm_room",
      { room_type: currentRoom.room_type, fixture: fixture.name, window_seconds: windowSeconds },
      confirmBtn);
    armedFixtures.add(fixture.name);
    render();
  };

  wrap.appendChild(armBtn);
  wrap.appendChild(formRow);
  return wrap;
}

// -------------------------------------------------------------- fixtures

// A small tag row for an Instrument's static declaration -- name plus its
// capabilities/functions/accepted_cues. Exported for rooms.js's per-fixture
// detail row, its sole caller; these fields never carry a live value (no
// controller/lane), so a plain tag row (no <dl> live-update machinery) is
// enough.
export function instrumentTags(instrument) {
  const row = mk("div", "insttags");
  row.appendChild(mk("span", "insttag instname", instrument.name));
  for (const cap of instrument.capabilities || []) {
    row.appendChild(mk("span", "insttag cap", cap));
  }
  for (const fn of instrument.functions || []) {
    row.appendChild(mk("span", "insttag fn", `${fn.name} (${fn.kind})`));
  }
  for (const cue of instrument.accepted_cues || []) {
    row.appendChild(mk("span", "insttag cue", cue));
  }
  for (const trig of instrument.event_triggers || []) {
    const thresholds = Object.entries(trig.thresholds || {})
      .map(([k, v]) => `${k}:${v}`).join(" ");
    row.appendChild(mk("span", "insttag trigger",
      thresholds ? `${trig.name} (${thresholds})` : trig.name));
  }
  return row;
}

// Fills a fixture head: name, binding controls, then the Muted chip. The
// chip comes AFTER the binding controls so _bindCtlFor (children[1]) is
// unchanged, and it is shown/hidden in place on every render rather than
// being part of bindStateKey: a mute change must never rebuild the head
// and discard an armed Release confirm-tap.
function fillFixtureHead(head, fixture) {
  head.appendChild(mk("span", "fixname", fixture.name));
  head.appendChild(bindingControls(fixture));
  const chip = mk("span", "chip solid-rose", "Muted");
  chip.hidden = !fixture.muted;
  head.appendChild(chip);
  muteChipByName.set(fixture.name, chip);
}

function buildFixture(fixture) {
  const wrap = document.createElement("div");
  wrap.className = "fixture";
  wrap.id = `fixture-${fixture.name}`;

  const head = mk("div", "fixhead");
  fillFixtureHead(head, fixture);
  wrap.appendChild(head);

  const blockrows = mk("div", "blockrows");
  const rows = _blockRowsFor(fixture);
  const canvases = [];
  for (const row of rows) {
    const rowEl = mk("div", "blockrow");
    rowEl.appendChild(mk("span", "blk", row.label));
    const canvas = document.createElement("canvas");
    canvas.className = "strip";
    canvas._pixelCount = row.count;
    rowEl.appendChild(canvas);
    blockrows.appendChild(rowEl);
    canvases.push(canvas);
  }
  wrap.appendChild(blockrows);

  const zones = mk("div", "zones");
  for (const zone of fixture.zones) {
    const span = document.createElement("span");
    span.style.flex = `${zone.count}`;
    span.textContent = `${zone.name} (${zone.start}..${zone.start + zone.count - 1})`;
    zones.appendChild(span);
  }
  wrap.appendChild(zones);

  return { wrap, canvases };
}

// Key summarizing the binding-relevant state that bindingControls() renders
// off of. Only when this actually changes should the chip/button DOM node
// be discarded and rebuilt -- otherwise a controllers-only room_changed
// would mint a fresh Release button on every tick, silently discarding any
// in-progress confirm-tap arm state (wire.confirmTap keys its timer/armed
// flag off the specific button element).
function bindStateKey(fixture) {
  if (fixture.dev) return `dev:${fixture.dev}:${fixture.url || ""}`;
  if (fixture.artnet) return "artnet";
  if (armedFixtures.has(fixture.name)) return "armed";
  return "unbound";
}

function fixtureShapeMatches(prev, next) {
  if (!prev) return false;
  if (prev.pixel_count !== next.pixel_count) return false;
  if (JSON.stringify(prev.instrument) !== JSON.stringify(next.instrument)) return false;
  return JSON.stringify(prev.zones) === JSON.stringify(next.zones);
}

// ------------------------------------------------------------------- tiles

// What a fixture's tile should be, as a key that changes only when the tile
// must be rebuilt: a modelled fixture by its model's sha256, an over-budget
// one by its reason. null means no tile (no model): the strip alone.
function tileKey(fixture) {
  const model = fixture.model || {};
  if (model.status === "ok" && model.sha256) return `ok:${model.sha256}`;
  if (model.status === "over_budget") return `over:${model.reason || ""}`;
  return null;
}

function showTileFallback(entry, reason) {
  entry.el.classList.add("fallback");
  if (entry.popout) entry.popout.hidden = true;
  clear(entry.viewEl);
  entry.viewEl.appendChild(mk("p", "tilereason mono dim", reason));
}

function startView(name, fixture, entry) {
  // Deferred a tick so the strip is in the DOM before any view work starts.
  Promise.resolve()
    .then(() => viewFactory(entry.viewEl, {
      fixture: name,
      sha256: fixture.model.sha256,
      onPick: (index, layout) => pickLed(name, index, layout),
    }))
    .then((view) => {
      if (entry.disposed) { view.dispose(); return; }
      entry.view = view;
      const channels = lastChannelsByName[name];
      if (channels) view.paint(channels);
      view.select(selectedLed && selectedLed.name === name ? selectedLed.index : null);
    }, (err) => {
      if (entry.disposed) return;
      delete traceByName[name];
      showTileFallback(entry, (err && err.message) || "the 3D view failed to load");
    });
}

function buildTile(fixture, key) {
  const name = fixture.name;
  const el = mk("div", "tile");
  const head = mk("div", "tilehead");
  head.appendChild(mk("span", "fixname", name));
  el.appendChild(head);
  const viewEl = mk("div", "tileview");
  el.appendChild(viewEl);
  const entry = { key, el, viewEl, popout: null, view: null, disposed: false };

  if (key.startsWith("over:")) {
    showTileFallback(entry, fixture.model.reason || "model over budget");
    return entry;
  }
  const popout = mk("a", "popout", "↗");
  popout.href = `fixture3d.html?fixture=${encodeURIComponent(name)}`;
  popout.target = "_blank";
  popout.rel = "noopener";
  popout.title = `Open ${name} in 3D`;
  head.appendChild(popout);
  entry.popout = popout;
  traceByName[name] = new FixtureTrace(fixture.pixel_count);
  startView(name, fixture, entry);
  return entry;
}

function dropTile(name) {
  const entry = tileByName.get(name);
  if (!entry) return;
  entry.disposed = true;
  if (entry.view) entry.view.dispose();
  entry.el.remove();
  tileByName.delete(name);
  delete traceByName[name];
  if (selectedLed && selectedLed.name === name) {
    selectedLed = null;
    if (inspector) inspector.clear();
  }
}

function dropAllTiles() {
  for (const name of Array.from(tileByName.keys())) dropTile(name);
}

function renderTiles(room) {
  const keys = new Map();
  for (const fixture of room.fixtures) {
    const key = tileKey(fixture);
    if (key) keys.set(fixture.name, key);
  }
  for (const [name, entry] of Array.from(tileByName.entries())) {
    if (keys.get(name) !== entry.key) dropTile(name);
  }
  // New tiles go before the nearest later surviving tile, so tiles keep
  // the Room's fixture order.
  for (let i = 0; i < room.fixtures.length; i++) {
    const fixture = room.fixtures[i];
    const key = keys.get(fixture.name);
    if (!key || tileByName.has(fixture.name)) continue;
    let anchor = null;
    for (let j = i + 1; j < room.fixtures.length; j++) {
      const later = tileByName.get(room.fixtures[j].name);
      if (later) { anchor = later.el; break; }
    }
    const entry = buildTile(fixture, key);
    if (anchor) tilesEl.insertBefore(entry.el, anchor);
    else tilesEl.appendChild(entry.el);
    tileByName.set(fixture.name, entry);
  }
  viewRowEl.hidden = tileByName.size === 0;
}

// ---------------------------------------------------------------- inspector

function pickLed(name, index, layout) {
  if (!tileByName.has(name)) return;
  selectedLed = { name, index, layout };
  for (const [tileName, entry] of tileByName) {
    if (entry.view) entry.view.select(tileName === name ? index : null);
  }
  if (inspector) inspector.select({ address: ledAddress(layout, index) });
  refreshInspector();
}

// Values only: the address rows were built by the pick (rule 1 for frames).
function refreshInspector() {
  if (!inspector) return;
  if (!selectedLed) { inspector.clear(); return; }
  const { name, index } = selectedLed;
  const trace = traceByName[name];
  const now = latestT ?? 0;
  inspector.update({
    rgbw: rgbwAt(lastChannelsByName[name] || [], index),
    samples: trace ? trace.samples(index, now) : [],
    gestures: trace ? trace.gestures(now) : [],
    now,
  });
}

function seeT(t) {
  if (typeof t !== "number") return;
  latestT = latestT === null ? t : Math.max(latestT, t);
}

// ------------------------------------------------------------- instruments

export function buildInstrumentCard(inst, controllers) {
  const card = mk("div", "inst");
  const h4 = document.createElement("h4");
  h4.appendChild(mk("span", `kind ${inst.kind === "audio" ? "audio" : "light"}`,
    inst.kind === "audio" ? "Audio" : "Light"));
  h4.appendChild(document.createTextNode(inst.instrument));
  card.appendChild(h4);

  const dl = mk("dl", "rows");
  const addRow = (k, v) => {
    dl.appendChild(mk("dt", null, k));
    dl.appendChild(mk("dd", null, v));
  };

  if (inst.kind === "light") addRow("target", inst.target);
  if (inst.program !== undefined) addRow("program", inst.program);
  if (inst.drone !== undefined) addRow("drone", JSON.stringify(inst.drone));
  if (inst.params && Object.keys(inst.params).length) {
    addRow("params", Object.entries(inst.params).map(([k, v]) => `${k} ${v}`).join(" · "));
  }
  for (const [k, v] of Object.entries(inst)) {
    if (["kind", "instrument", "target", "params", "lanes", "program", "drone"].includes(k)) continue;
    addRow(k, typeof v === "object" ? JSON.stringify(v) : String(v));
  }

  // Live lane rows, tracked directly on the card rather than re-located via
  // querySelector -- the shared node test DOM stub's querySelector(All) are
  // no-ops (see tests/js/_dom_stub.js), matching the same reasoning this
  // file already gives (line ~23) for caching structural elements as module
  // state instead of re-fetching them.
  const liveRows = [];
  for (const lane of inst.lanes || []) {
    const cc = lane.source.startsWith("cc:") ? lane.source.slice(3) : null;
    const dt = mk("dt", null, lane.source);
    const dd = document.createElement("dd");
    dd.appendChild(document.createTextNode(`→ ${lane.dest} `));
    let liveSpan = null;
    if (cc !== null && controllers && controllers[cc] !== undefined) {
      liveSpan = mk("span", "live", `= ${controllers[cc]}`);
      dd.appendChild(liveSpan);
    }
    if (cc !== null) liveRows.push({ cc, dd, span: liveSpan });
    dl.appendChild(dt);
    dl.appendChild(dd);
  }
  card._liveRows = liveRows;

  card.appendChild(dl);
  return card;
}

// -------------------------------------------------------------------- lanes

// One row per lane source across every voice. cc:<n> sources sort by n
// ascending; anything else (a note lane, "trigger") follows in first-seen
// order with no live value.
export function _laneRowsFor(instruments) {
  const bySource = new Map();
  for (const inst of instruments || []) {
    for (const lane of inst.lanes || []) {
      if (!bySource.has(lane.source)) {
        const cc = lane.source.startsWith("cc:") ? Number(lane.source.slice(3)) : null;
        bySource.set(lane.source, { source: lane.source, cc, readers: [] });
      }
      bySource.get(lane.source).readers.push(
        { kind: inst.kind, instrument: inst.instrument, dest: lane.dest });
    }
  }
  const rows = Array.from(bySource.values());
  rows.sort((a, b) => {
    if (a.cc === null && b.cc === null) return 0;
    if (a.cc === null) return 1;
    if (b.cc === null) return -1;
    return a.cc - b.cc;
  });
  return rows;
}

export function _laneRowFor(source) {
  return laneRowBySource.get(source);
}

export function _laneTable() {
  return laneTableEl;
}

export function _laneEmptyMsg() {
  return emptyMsgEl;
}

function voiceCounts(instruments) {
  let light = 0;
  let audio = 0;
  for (const inst of instruments || []) {
    if (inst.kind === "audio") audio += 1; else light += 1;
  }
  return `${light} light · ${audio} audio voices`;
}

function valueText(controllers, cc) {
  if (cc === null) return "";
  const v = controllers && controllers[cc];
  return v === undefined ? "—" : String(v);
}

function buildLaneTable(instruments, controllers) {
  const table = mk("table", "lanes mono");
  laneRowBySource = new Map();
  laneValueBySource = new Map();
  for (const row of _laneRowsFor(instruments)) {
    const tr = mk("tr", row.cc === null ? "lane other" : "lane");
    tr.appendChild(mk("td", "src", row.source));
    const val = mk("td", "val", valueText(controllers, row.cc));
    tr.appendChild(val);
    const readers = mk("td", "readers");
    row.readers.forEach((r, i) => {
      if (i > 0) readers.appendChild(mk("span", "dim", " · "));
      readers.appendChild(mk("span", `kind ${r.kind === "audio" ? "audio" : "light"}`,
        r.kind === "audio" ? "Audio" : "Light"));
      readers.appendChild(document.createTextNode(` ${r.instrument} ${r.dest}`));
    });
    tr.appendChild(readers);
    table.appendChild(tr);
    laneRowBySource.set(row.source, tr);
    laneValueBySource.set(row.source, { td: val, cc: row.cc });
  }
  return table;
}

function updateLaneValues(controllers) {
  for (const { td, cc } of laneValueBySource.values()) {
    const text = valueText(controllers, cc);
    if (td.textContent !== text) td.textContent = text;
  }
}

function renderLanes(container, instruments, controllers) {
  const signature = JSON.stringify(instruments || []);
  const empty = !instruments || instruments.length === 0;
  // Patch in place whichever of the two rendered forms (table or empty
  // message) is still current, matching this file's rendering discipline --
  // an unchanged signature must repaint nothing structural, empty state
  // included.
  const current = empty ? emptyMsgEl : laneTableEl;
  if (current && current.parentNode === container && signature === lanesSignature) {
    if (!empty) updateLaneValues(controllers);
    return;
  }
  clear(container);
  lanesSignature = signature;
  if (empty) {
    laneTableEl = null;
    laneRowBySource = new Map();
    laneValueBySource = new Map();
    emptyMsgEl = mk("p", "muted", "No voices declared (no Bit loaded).");
    container.appendChild(emptyMsgEl);
    return;
  }
  emptyMsgEl = null;
  laneTableEl = buildLaneTable(instruments, controllers);
  container.appendChild(laneTableEl);
}

// -------------------------------------------------------------------- frame

function updateFramesChip() {
  const chip = framesChipEl;
  if (!chip) return;
  const now = Date.now();
  const anyLive = Object.values(lastFrameAt).some((t) => now - t < FRAME_LIVE_MS);
  clear(chip);
  if (anyLive) {
    chip.className = "chip sage";
    chip.appendChild(mk("span", "dot"));
    chip.appendChild(document.createTextNode("Live"));
  } else {
    chip.className = "chip dim";
    chip.textContent = "No frames";
  }
}

// -------------------------------------------------------------------- render

function resetStructure() {
  dropAllTiles();
  headEl = null;
  bodyEl = null;
  fixturesMountEl = null;
  viewRowEl = null;
  tilesEl = null;
  inspectorEl = null;
  inspector = null;
  framesChipEl = null;
  instAccEl = null;
  instSummaryMetaEl = null;
  instMountEl = null;
  functionsAccEl = null;
  fixtureElByName = new Map();
  bindStateByName = new Map();
  laneTableEl = null;
  laneRowBySource = new Map();
  laneValueBySource = new Map();
  lanesSignature = null;
  emptyMsgEl = null;
}

function render() {
  const card = document.getElementById("roomCard");

  if (!currentRoom) {
    clear(card);
    resetStructure();
    fixtureShapes = {};
    canvasesByName = {};
    lastPaintByName = {};
    lastChannelsByName = {};
    lastFrameAt = {};
    armedFixtures.clear();
    pendingArm = null;
    muteChipByName.clear();
    card.appendChild(mk("p", "muted", "No Room configured"));
    return;
  }

  const room = currentRoom;

  if (!headEl || !bodyEl) {
    clear(card);
    resetStructure();
    headEl = mk("div", "cardhead");
    card.appendChild(headEl);
    bodyEl = document.createElement("div");
    card.appendChild(bodyEl);
  }
  const head = headEl;
  const body = bodyEl;

  clear(head);
  const h2 = document.createElement("h2");
  h2.appendChild(document.createTextNode(room.room_type + " "));
  h2.appendChild(mk("span", "dim", "Room"));
  head.appendChild(h2);
  const headRight = mk("div");
  headRight.appendChild(mk("span", "mono dim",
    `${room.capability.pixel_count} px · RGBW`));
  framesChipEl = mk("span", "chip dim", "No frames");
  headRight.appendChild(framesChipEl);
  head.appendChild(headRight);
  updateFramesChip();

  // Tiles and the inspector sit above the strips (spec mockup "layout A").
  if (!viewRowEl) {
    viewRowEl = mk("div", "viewrow");
    tilesEl = mk("div", "tiles");
    inspectorEl = mk("div", "inspector");
    viewRowEl.appendChild(tilesEl);
    viewRowEl.appendChild(inspectorEl);
    viewRowEl.hidden = true;
    body.appendChild(viewRowEl);
    inspector = mountInspector(inspectorEl);
  }

  if (!fixturesMountEl) {
    fixturesMountEl = document.createElement("div");
    body.appendChild(fixturesMountEl);
  }
  const fixturesMount = fixturesMountEl;

  // Drop fixtures no longer present.
  const currentNames = new Set(room.fixtures.map((f) => f.name));
  for (const oldName of Object.keys(fixtureShapes)) {
    if (!currentNames.has(oldName)) {
      const oldEl = fixtureElByName.get(oldName);
      if (oldEl) oldEl.remove();
      fixtureElByName.delete(oldName);
      muteChipByName.delete(oldName);
      delete fixtureShapes[oldName];
    }
  }

  // Rebuild only fixtures whose own shape changed; reinsert before the
  // nearest later surviving fixture so declaration order is preserved.
  const newCanvasesByName = {};
  for (let i = 0; i < room.fixtures.length; i++) {
    const fixture = room.fixtures[i];
    const nextShape = { pixel_count: fixture.pixel_count, zones: fixture.zones,
                        instrument: fixture.instrument };
    const unchanged = fixtureShapeMatches(fixtureShapes[fixture.name], nextShape);

    if (unchanged) {
      // In-place updates: binding chip/controls and header text only.
      const existing = fixtureElByName.get(fixture.name);
      if (existing) {
        // Rebuild the head's binding controls only when binding-relevant
        // state actually changed -- not on every "unchanged" render pass,
        // since that would mint a fresh chip/Release button (with a fresh
        // confirm-tap closure) on every controllers-only room_changed.
        const nextBindKey = bindStateKey(fixture);
        if (bindStateByName.get(fixture.name) !== nextBindKey) {
          const oldHead = existing.children[0];
          if (oldHead) {
            clear(oldHead);
            fillFixtureHead(oldHead, fixture);
          }
          bindStateByName.set(fixture.name, nextBindKey);
        }
        const muteChip = muteChipByName.get(fixture.name);
        if (muteChip) muteChip.hidden = !fixture.muted;
        // Preserve the canvas list under this fixture's own name.
        if (canvasesByName[fixture.name]) {
          newCanvasesByName[fixture.name] = canvasesByName[fixture.name];
        }
      }
      fixtureShapes[fixture.name] = nextShape;
      continue;
    }

    const oldEl = fixtureElByName.get(fixture.name);
    if (oldEl) oldEl.remove();

    let anchor = null;
    for (let j = i + 1; j < room.fixtures.length; j++) {
      const nextEl = fixtureElByName.get(room.fixtures[j].name);
      if (nextEl) { anchor = nextEl; break; }
    }

    const { wrap, canvases } = buildFixture(fixture);
    if (anchor) {
      fixturesMount.insertBefore(wrap, anchor);
    } else {
      fixturesMount.appendChild(wrap);
    }
    fixtureElByName.set(fixture.name, wrap);
    bindStateByName.set(fixture.name, bindStateKey(fixture));
    newCanvasesByName[fixture.name] = canvases;
    fixtureShapes[fixture.name] = nextShape;
  }
  canvasesByName = newCanvasesByName;
  renderTiles(room);

  // Triggers accordion shell -- created ONCE here; functions.js renders into
  // #functionsMount. Sits directly under the LED rows.
  if (!functionsAccEl) {
    functionsAccEl = document.createElement("details");
    functionsAccEl.className = "acc";
    functionsAccEl.id = "functionsAcc";
    functionsAccEl.open = true;
    const summary = document.createElement("summary");
    summary.appendChild(mk("span", "tri", "▸"));
    summary.appendChild(document.createTextNode("Triggers"));
    functionsAccEl.appendChild(summary);
    const functionsBody = mk("div", "accbody");
    functionsBody.id = "functionsMount";
    functionsAccEl.appendChild(functionsBody);
    body.appendChild(functionsAccEl);
  }

  // Live values accordion (created once, refreshed each render). Closed by
  // default; the operator's last choice is remembered per browser.
  if (!instAccEl) {
    instAccEl = document.createElement("details");
    instAccEl.className = "acc";
    instAccEl.open = readLiveValuesOpen();
    const summary = document.createElement("summary");
    summary.appendChild(mk("span", "tri", "▸"));
    summary.appendChild(document.createTextNode("Live values"));
    instSummaryMetaEl = mk("span", "summeta mono dim", "");
    summary.appendChild(instSummaryMetaEl);
    instAccEl.appendChild(summary);
    instAccEl.addEventListener("toggle", () => writeLiveValuesOpen(instAccEl.open));
    instMountEl = mk("div", "accbody");
    instAccEl.appendChild(instMountEl);
    body.appendChild(instAccEl);
  }
  instSummaryMetaEl.textContent = voiceCounts(room.instruments);
  renderLanes(instMountEl, room.instruments, room.controllers || {});
}

// -------------------------------------------------------------------- frames

function onRoomFrame(msg) {
  // rule 9: a frame for a fixture this panel has no strip for is a no-op.
  if (!canvasesByName[msg.fixture]) return;
  const channels = msg.channels || [];
  const pixels = decodeRgbw(channels);
  lastPaintByName[msg.fixture] = pixels;
  lastChannelsByName[msg.fixture] = channels;
  lastFrameAt[msg.fixture] = Date.now();
  repaintFixture(msg.fixture);
  updateFramesChip();

  seeT(msg.t);
  const trace = traceByName[msg.fixture];
  if (trace && typeof msg.t === "number") trace.pushFrame(msg.t, channels);
  const tile = tileByName.get(msg.fixture);
  if (tile && tile.view) tile.view.paint(channels);
  if (selectedLed && selectedLed.name === msg.fixture) refreshInspector();
}

// A gesture is not tied to a fixture: it marks every tiled fixture's trace.
function onGesture(msg) {
  seeT(msg.t);
  if (typeof msg.t !== "number") return;
  for (const trace of Object.values(traceByName)) trace.pushGesture(msg);
  if (selectedLed) refreshInspector();
}

// ---------------------------------------------------------------------- init

export function init() {
  wire.on("snapshot", (m) => {
    // A snapshot starts a connection, perhaps to a restarted Control whose
    // clock began again: drop the old time axis and every trace with it.
    latestT = null;
    for (const name of Object.keys(traceByName)) {
      traceByName[name] = new FixtureTrace(traceByName[name].pixelCount);
    }
    currentRoom = m.room || null;
    render();
  });
  wire.on("room_changed", (m) => {
    currentRoom = m.room || null;
    render();
  });
  wire.on("room_frame", onRoomFrame);
  wire.on("gesture", onGesture);

  // A refused arm_room must not leave its fixture showing Armed. Not
  // cleared on room_changed: controller values make that event frequent,
  // and clearing there would race the refusal.
  wire.on("error", (m) => {
    if (m.command !== "arm_room" || pendingArm === null) return;
    armedFixtures.delete(pendingArm);
    pendingArm = null;
    render();
  });

  // Liveness is state, not decoration -- unlike tint transitions, this
  // interval is NOT skipped under prefers-reduced-motion.
  const timer = setInterval(updateFramesChip, 1000);
  if (timer && typeof timer.unref === "function") timer.unref();
}
