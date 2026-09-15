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
  dispatch("_sent", Object.assign({ command }, extra));
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

// Reserves room for `btn`'s armed label before it is ever needed, via an
// offscreen clone, so confirmTap's label swap never changes the button's
// rendered width. Call once, right after creating the button. Without
// this, arming grew the button in place and reflowed the row -- on a
// narrow sidebar that could carry the button itself to a new line, so a
// fast second click aimed at its pre-arm position landed on nothing and
// silently failed to confirm (2026-09-13, "abort does nothing" report).
export function reserveConfirmWidth(btn, armLabel) {
  if (typeof btn.cloneNode !== "function") return; // no-op under the JS test stub's minimal DOM
  const clone = btn.cloneNode(true);
  clone.textContent = armLabel;
  clone.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;left:-9999px;";
  document.body.appendChild(clone);
  const armedWidth = clone.getBoundingClientRect().width;
  clone.remove();
  if (armedWidth) btn.style.minWidth = `${armedWidth}px`;
}

// Shared two-tap confirm helper used by any panel with a destructive/
// state-changing action that needs "click once to arm, click again to
// confirm" behavior (per spec section 5: one confirm mechanism, reused
// everywhere rather than each panel inventing its own modal/dialog).
// Pair with reserveConfirmWidth at button creation so arming never
// reflows the row (see its comment).
export function confirmTap(btn, { armLabel, armStyle = "label", timeoutMs = 4000,
                                  onArm = null, onDisarm = null } = {}, onConfirm) {
  if (btn.dataset.armed === "1") {
    delete btn.dataset.armed;
    clearTimeout(btn._confirmTimer);
    if (armStyle === "label") btn.textContent = btn._restLabel ?? btn.textContent;
    if (onDisarm) onDisarm();
    onConfirm();
    return;
  }
  const original = btn.textContent;
  btn.dataset.armed = "1";
  if (armStyle === "label") {
    btn._restLabel = original;
    btn.textContent = armLabel;
  }
  if (onArm) onArm();
  btn._confirmTimer = setTimeout(() => {
    if (btn.dataset.armed === "1") {
      delete btn.dataset.armed;
      if (armStyle === "label") btn.textContent = original;
      if (onDisarm) onDisarm();
      flashNotConfirmed(btn);
    }
  }, timeoutMs);
}

// The arm window closed with no second click: previously a silent revert
// indistinguishable from the confirm click having been swallowed. Now
// visible, reusing flashRefusal's look, so a missed or late confirm is
// never mistaken for "it worked" or explained by nothing at all.
function flashNotConfirmed(btn) {
  btn.classList.add("errflash");
  const note = document.createElement("span");
  note.className = "inline-err";
  note.textContent = "not confirmed, click to try again";
  btn.parentNode?.appendChild?.(note);
  setTimeout(() => { btn.classList.remove("errflash"); note.remove(); }, 4000);
}
