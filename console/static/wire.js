// The console's only socket-touching module. Everything else registers
// handlers here and renders DOM; nothing else may construct a WebSocket.
const handlers = new Map();          // event name -> [fn, ...]
const sources = new Map();           // command name -> last source element
let ws = null;
let attempts = 0;

export function on(event, fn) {
  if (!handlers.has(event)) handlers.set(event, []);
  handlers.get(event).push(fn);
}

function dispatch(event, msg) {
  for (const fn of handlers.get(event) ?? []) fn(msg);
}

export function send(command, extra = {}, sourceEl = null) {
  if (sourceEl) sources.set(command, sourceEl);
  if (ws && ws.readyState === (ws.constructor.OPEN ?? 1)) {
    ws.send(JSON.stringify(Object.assign({ command }, extra)));
  }
}

export function flashRefusal(command, message) {
  const elx = sources.get(command);
  if (!elx) return;
  elx.classList.add("errflash");
  const note = document.createElement("span");
  note.className = "inline-err";
  note.textContent = message;
  elx.parentNode?.appendChild?.(note);
  setTimeout(() => { elx.classList.remove("errflash"); note.remove(); }, 6000);
}

export function connect({ WebSocketImpl = WebSocket, retryMs = 1000 } = {}) {
  ws = new WebSocketImpl(`ws://${typeof location !== "undefined" ? location.host : ""}/ws`);
  ws.onopen = () => { attempts = 0; dispatch("_open", {}); };
  ws.onclose = () => {
    attempts += 1;
    dispatch("_closed", { attempts });
    setTimeout(() => connect({ WebSocketImpl, retryMs }), retryMs);
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    dispatch(msg.event, msg);
  };
}

// Shared two-tap confirm helper used by any panel with a destructive/
// state-changing action that needs "click once to arm, click again to
// confirm" behavior (per spec section 5: one confirm mechanism, reused
// everywhere rather than each panel inventing its own modal/dialog).
export function confirmTap(btn, { armLabel, timeoutMs = 4000 } = {}, onConfirm) {
  if (btn.dataset.armed === "1") {
    delete btn.dataset.armed;
    onConfirm();
    return;
  }
  const original = btn.textContent;
  btn.dataset.armed = "1";
  btn.textContent = armLabel;
  setTimeout(() => {
    if (btn.dataset.armed === "1") {
      delete btn.dataset.armed;
      btn.textContent = original;
    }
  }, timeoutMs);
}
