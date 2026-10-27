// The LED inspector panel: one selected LED's address, its four wire
// channels, and a 10 s step trace of R, G, B and W with each input gesture
// marked on the time axis. Everything is on Control's clock: the caller
// passes `now` as the latest `t` it has seen, never the browser's clock, so
// a gesture and the LED's response line up. Shared by the Room card
// (surface.js) and the bump-out page.
import { mk, clear } from "./dom.js";
import { gestureLabel } from "./led_inspect.js";

const TRACE_W = 320;
const TRACE_H = 96;
const CHANNELS = [
  ["r", "#e0705f"],
  ["g", "#8fc77a"],
  ["b", "#79a2e6"],
  ["w", "#ffdbcc"],
];

// A CSS token's value, read live so the trace follows the stylesheet; the
// fallback is the token's value in terrarium.css (also what the node test
// DOM sees, since it has no getComputedStyle).
function token(el, name, fallback) {
  try {
    if (typeof getComputedStyle === "function") {
      const v = getComputedStyle(el).getPropertyValue(name).trim();
      if (v) return v;
    }
  } catch (e) { /* no computed style: use the fallback */ }
  return fallback;
}

function drawTrace(canvas, { samples, gestures, now, windowS }) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const start = now - windowS;
  const xOf = (t) => Math.max(0, Math.min(w, ((t - start) / windowS) * w));
  const yOf = (v) => h - 2 - (Math.max(0, Math.min(255, v)) / 255) * (h - 4);

  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  const terra = token(canvas, "--terra", "#c07850");
  const dim = token(canvas, "--ink-dim", "rgba(255,219,204,0.55)");
  for (const g of gestures) {
    const x = xOf(g.t);
    ctx.strokeStyle = g.refused ? terra : dim;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }

  // Step trace: each value holds until the next sample, and the last one
  // holds out to `now` (the right edge), so a static LED is a full line.
  ctx.setLineDash([]);
  ctx.lineWidth = 1.5;
  if (!samples.length) return;
  for (const [key, colour] of CHANNELS) {
    ctx.strokeStyle = colour;
    ctx.beginPath();
    let prevY = null;
    for (const s of samples) {
      const x = xOf(s.t);
      const y = yOf(s[key]);
      if (prevY === null) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, prevY);
        ctx.lineTo(x, y);
      }
      prevY = y;
    }
    ctx.lineTo(w, prevY);
    ctx.stroke();
  }
}

export function mountInspector(el) {
  clear(el);
  el.classList.add("inspector");
  const title = mk("h4", null, "LED");
  const empty = mk("p", "muted", "Click an LED on a tile to inspect it.");
  const addrEl = mk("dl", "rows");
  const rgbwEl = mk("p", "mono rgbw");
  const canvas = document.createElement("canvas");
  canvas.className = "trace";
  canvas.width = TRACE_W;
  canvas.height = TRACE_H;
  const caption = mk("p", "mono dim tracecap", "last 10 s · 10 Hz");
  const gestureList = mk("ul", "gestlist mono");
  const detail = mk("div", "inspdetail");
  detail.appendChild(addrEl);
  detail.appendChild(rgbwEl);
  detail.appendChild(canvas);
  detail.appendChild(caption);
  detail.appendChild(gestureList);
  detail.hidden = true;
  el.appendChild(title);
  el.appendChild(empty);
  el.appendChild(detail);

  const row = (k, v) => {
    addrEl.appendChild(mk("dt", null, k));
    addrEl.appendChild(mk("dd", null, v));
  };

  // Gesture rows are rebuilt only when the gesture set changes, so a frame
  // (10 Hz) never replaces a hovered row or its tooltip.
  let gestureKey = null;

  return {
    // Once per pick: the address rows.
    select({ address }) {
      empty.hidden = true;
      detail.hidden = false;
      clear(addrEl);
      row("fixture", `${address.fixture} · pixel ${address.index}`);
      row("zones", address.roomZones.length ? address.roomZones.join(", ") : "none");
      row("model zone", address.modelZone ?? "none");
      clear(gestureList);
      gestureKey = null;
    },
    // On every frame or gesture: text and canvas in place; the gesture
    // list only when its count or newest t changed.
    update({ rgbw, samples = [], gestures = [], now, windowS = 10 }) {
      const text = `R ${rgbw.r}  G ${rgbw.g}  B ${rgbw.b}  W ${rgbw.w}`;
      if (rgbwEl.textContent !== text) rgbwEl.textContent = text;
      drawTrace(canvas, { samples, gestures, now, windowS });
      const key = `${gestures.length}:${gestures.length ? gestures[gestures.length - 1].t : ""}`;
      if (key === gestureKey) return;
      gestureKey = key;
      clear(gestureList);
      for (const g of gestures) {
        const li = mk("li", g.refused ? "refused" : null,
          `${Number(g.t).toFixed(2)}  ${gestureLabel(g)}`);
        li.title = `received ${Number(g.t).toFixed(2)}, cue at ${Number(g.at).toFixed(2)} (Control clock)`;
        gestureList.appendChild(li);
      }
    },
    clear() {
      empty.hidden = false;
      detail.hidden = true;
      clear(addrEl);
      clear(gestureList);
      gestureKey = null;
      rgbwEl.textContent = "";
      canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    },
  };
}
