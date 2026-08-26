// Room card: LED surface as discrete per-pixel dot rows (one per physical
// block), zone bar, binding controls, and the Instruments/Triggers
// accordions. Per-fixture rebuild discipline (spec section 6, rules
// 1/2/3/4/6/9) is the load-bearing part of this file -- a controllers-only
// room_changed must repaint nothing but live lane values; a room_frame must
// only ever repaint a canvas, never rebuild DOM.
import * as wire from "./wire.js";

const FRAME_LIVE_MS = 2000;
const PIX_PER_BLOCK = 144;
const SINGLE_ROW_MAX = 160;

let currentRoom = null;              // last-seen room (or null)
let fixtureShapes = {};              // fixture name -> last-seen {pixel_count, zones}
let fixtureNameByDev = {};           // dev -> fixture name, rebuilt every renderRoom
let fixtureDevByName = {};           // fixture name -> dev, rebuilt every renderRoom
let canvasesByDev = {};              // dev -> [canvas, ...] (in pixel order across rows)
let lastPaintByDev = {};             // dev -> [[r,g,b], ...] per pixel
let lastFrameAt = {};                // dev -> ms timestamp of last room_frame
let armedFixtures = new Set();       // fixture names showing "Armed" until next room_changed

// Structural elements this module owns, cached as module state rather than
// re-located via getElementById -- the DOM stub (and, harmlessly, real
// DOM) auto-vivifies unknown ids there, so "does it already exist" can
// only be answered by remembering the reference ourselves.
let headEl = null;
let bodyEl = null;
let fixturesMountEl = null;
let framesChipEl = null;
let instAccEl = null;
let instSummaryMetaEl = null;
let instMountEl = null;
let triggersAccEl = null;            // created once, outside the per-fixture rebuild path
let fixtureElByName = new Map();     // fixture name -> its .fixture wrapper element
let bindStateByName = new Map();     // fixture name -> last-rendered binding-state key
let instGridEl = null;               // .instgrid wrapper inside instMountEl
let instCardByKey = new Map();       // instKey(inst) -> its .inst card element
let instShapeByKey = new Map();      // instKey(inst) -> last-rendered JSON.stringify(inst)

function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
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

export function _canvasFor(dev) {
  return canvasesByDev[dev];
}

export function _lastPaint(dev) {
  return lastPaintByDev[dev];
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

// Instrument card element for a given instrument declaration, so tests can
// assert its DOM node identity survives a controllers-only room_changed.
export function _instCardFor(kind, instrument, target) {
  return instCardByKey.get(`${kind}:${instrument}:${target || ""}`);
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

function repaintDev(dev) {
  const canvases = canvasesByDev[dev];
  const pixels = lastPaintByDev[dev];
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
    const chip = mk("span", "chip sage", fixture.dev);
    wrap.appendChild(chip);
    const releaseBtn = mk("button", "btn outline small", "Release");
    releaseBtn.onclick = () => {
      wire.confirmTap(releaseBtn, { armLabel: "Confirm release?" }, () => {
        wire.send("release_room", { room_type: currentRoom.room_type, fixture: fixture.name }, releaseBtn);
      });
    };
    wrap.appendChild(releaseBtn);
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

function buildFixture(fixture) {
  const wrap = document.createElement("div");
  wrap.className = "fixture";
  wrap.id = `fixture-${fixture.name}`;

  const head = mk("div", "fixhead");
  head.appendChild(mk("span", "fixname", fixture.name));
  head.appendChild(bindingControls(fixture));
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
  if (fixture.dev) return `dev:${fixture.dev}`;
  if (armedFixtures.has(fixture.name)) return "armed";
  return "unbound";
}

function fixtureShapeMatches(prev, next) {
  if (!prev) return false;
  if (prev.pixel_count !== next.pixel_count) return false;
  return JSON.stringify(prev.zones) === JSON.stringify(next.zones);
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

// In-place update of an unchanged instrument card's live lane values only --
// mirrors the fixture in-place-update path above. Never touches the rest of
// the card's DOM, so any future interactive per-node state within a card
// would survive a controllers-only room_changed the same way binding
// controls already do.
function updateInstrumentLive(card, controllers) {
  for (const row of card._liveRows || []) {
    const value = controllers && controllers[row.cc];
    if (value !== undefined) {
      const text = `= ${value}`;
      if (!row.span) {
        row.span = mk("span", "live", text);
        row.dd.appendChild(row.span);
      } else if (row.span.textContent !== text) {
        row.span.textContent = text;
      }
    } else if (row.span) {
      row.span.remove();
      row.span = null;
    }
  }
}

// Key uniquely identifying an instrument declaration within one Room's
// instrument list. `instrument` alone isn't unique -- room_view.py's
// _light_instruments defaults `target` to "primary" per declaration, so two
// light instruments can share a name while differing by target; audio
// entries carry no `target` at all, so the empty-string fallback still
// distinguishes them from a same-named light entry.
function instKey(inst) {
  return `${inst.kind}:${inst.instrument}:${inst.target || ""}`;
}

function renderInstruments(container, instruments, controllers) {
  if (!instruments || instruments.length === 0) {
    clear(container);
    instCardByKey = new Map();
    instShapeByKey = new Map();
    container.appendChild(mk("p", "muted", "No instruments declared (no Bit loaded)."));
    return;
  }

  if (!instGridEl || instGridEl.parentNode !== container) {
    // First paint (or recovering from the empty-state branch above, which
    // replaced the grid with a "no instruments" <p>).
    clear(container);
    instGridEl = mk("div", "instgrid");
    container.appendChild(instGridEl);
    instCardByKey = new Map();
    instShapeByKey = new Map();
  }
  const grid = instGridEl;

  // Drop instruments no longer declared.
  const currentKeys = new Set(instruments.map(instKey));
  for (const oldKey of Array.from(instCardByKey.keys())) {
    if (!currentKeys.has(oldKey)) {
      const oldEl = instCardByKey.get(oldKey);
      if (oldEl) oldEl.remove();
      instCardByKey.delete(oldKey);
      instShapeByKey.delete(oldKey);
    }
  }

  // Rebuild only cards whose own declaration changed; update everyone
  // else's live lane values in place. Reinsert before the nearest later
  // surviving card so declaration order is preserved.
  for (let i = 0; i < instruments.length; i++) {
    const inst = instruments[i];
    const key = instKey(inst);
    const nextShape = JSON.stringify(inst);
    const unchanged = instShapeByKey.get(key) === nextShape;

    if (unchanged) {
      const existing = instCardByKey.get(key);
      if (existing) updateInstrumentLive(existing, controllers);
      continue;
    }

    const oldEl = instCardByKey.get(key);
    if (oldEl) oldEl.remove();

    let anchor = null;
    for (let j = i + 1; j < instruments.length; j++) {
      const nextEl = instCardByKey.get(instKey(instruments[j]));
      if (nextEl) { anchor = nextEl; break; }
    }

    const card = buildInstrumentCard(inst, controllers);
    if (anchor) {
      grid.insertBefore(card, anchor);
    } else {
      grid.appendChild(card);
    }
    instCardByKey.set(key, card);
    instShapeByKey.set(key, nextShape);
  }
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
  headEl = null;
  bodyEl = null;
  fixturesMountEl = null;
  framesChipEl = null;
  instAccEl = null;
  instSummaryMetaEl = null;
  instMountEl = null;
  triggersAccEl = null;
  fixtureElByName = new Map();
  bindStateByName = new Map();
  instGridEl = null;
  instCardByKey = new Map();
  instShapeByKey = new Map();
}

function render() {
  const card = document.getElementById("roomCard");

  if (!currentRoom) {
    clear(card);
    resetStructure();
    fixtureShapes = {};
    fixtureNameByDev = {};
    fixtureDevByName = {};
    canvasesByDev = {};
    lastPaintByDev = {};
    lastFrameAt = {};
    armedFixtures.clear();
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
    `${room.capability.pixel_count} px · ${room.capability.color_order}`));
  framesChipEl = mk("span", "chip dim", "No frames");
  headRight.appendChild(framesChipEl);
  head.appendChild(headRight);
  updateFramesChip();

  fixtureNameByDev = {};
  fixtureDevByName = {};
  for (const fixture of room.fixtures) {
    fixtureDevByName[fixture.name] = fixture.dev;
    if (fixture.dev) fixtureNameByDev[fixture.dev] = fixture.name;
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
      delete fixtureShapes[oldName];
    }
  }

  // Rebuild only fixtures whose own shape changed; reinsert before the
  // nearest later surviving fixture so declaration order is preserved.
  const newCanvasesByDev = {};
  for (let i = 0; i < room.fixtures.length; i++) {
    const fixture = room.fixtures[i];
    const nextShape = { pixel_count: fixture.pixel_count, zones: fixture.zones };
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
            oldHead.appendChild(mk("span", "fixname", fixture.name));
            oldHead.appendChild(bindingControls(fixture));
          }
          bindStateByName.set(fixture.name, nextBindKey);
        }
        // Preserve the canvas list under the existing dev key(s).
        for (const [dev, name] of Object.entries(fixtureNameByDev)) {
          if (name === fixture.name && canvasesByDev[dev]) {
            newCanvasesByDev[dev] = canvasesByDev[dev];
          }
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
    if (fixture.dev) newCanvasesByDev[fixture.dev] = canvases;
    fixtureShapes[fixture.name] = nextShape;
  }
  canvasesByDev = newCanvasesByDev;

  // Instruments accordion (created once, refreshed each render).
  if (!instAccEl) {
    instAccEl = document.createElement("details");
    instAccEl.className = "acc";
    instAccEl.open = true;
    const summary = document.createElement("summary");
    summary.appendChild(mk("span", "tri", "▸"));
    summary.appendChild(document.createTextNode("Instruments"));
    instSummaryMetaEl = mk("span", "summeta mono dim", "");
    summary.appendChild(instSummaryMetaEl);
    instAccEl.appendChild(summary);
    instMountEl = mk("div", "accbody");
    instAccEl.appendChild(instMountEl);
    body.appendChild(instAccEl);
  }
  instSummaryMetaEl.textContent = `${room.instruments.length} declared · live values`;
  renderInstruments(instMountEl, room.instruments, room.controllers || {});

  // Triggers accordion shell -- created ONCE here; Task 6 renders into
  // #triggersMount.
  if (!triggersAccEl) {
    triggersAccEl = document.createElement("details");
    triggersAccEl.className = "acc";
    triggersAccEl.id = "triggersAcc";
    triggersAccEl.open = true;
    const summary = document.createElement("summary");
    summary.appendChild(mk("span", "tri", "▸"));
    summary.appendChild(document.createTextNode("Triggers"));
    triggersAccEl.appendChild(summary);
    const triggersBody = mk("div", "accbody");
    triggersBody.id = "triggersMount";
    triggersAccEl.appendChild(triggersBody);
    body.appendChild(triggersAccEl);
  }
}

// -------------------------------------------------------------------- frames

function onRoomFrame(msg) {
  const name = fixtureNameByDev[msg.dev];
  if (!name) return; // rule 9: unknown dev is a no-op
  const channels = msg.channels || [];
  const pixelCount = Math.floor(channels.length / 3);
  const pixels = [];
  for (let i = 0; i < pixelCount; i++) {
    const g = channels[i * 3] || 0;
    const r = channels[i * 3 + 1] || 0;
    const b = channels[i * 3 + 2] || 0;
    pixels.push([r, g, b]);
  }
  lastPaintByDev[msg.dev] = pixels;
  lastFrameAt[msg.dev] = Date.now();
  repaintDev(msg.dev);
  updateFramesChip();
}

// ---------------------------------------------------------------------- init

export function init() {
  wire.on("snapshot", (m) => {
    currentRoom = m.room || null;
    render();
  });
  wire.on("room_changed", (m) => {
    currentRoom = m.room || null;
    render();
  });
  wire.on("room_frame", onRoomFrame);

  // Liveness is state, not decoration -- unlike tint transitions, this
  // interval is NOT skipped under prefers-reduced-motion.
  const timer = setInterval(updateFramesChip, 1000);
  if (timer && typeof timer.unref === "function") timer.unref();
}
