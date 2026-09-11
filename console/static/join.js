// Join card: how a guest reaches the loaded Bit. Read model is
// snapshot.join / join_changed.join (control/join_info.py's
// build_join_info): the guest page URL, then one row per registration
// node with a server-rendered QR SVG, the node's URL and the Tuneshroom
// `flutter run` line. Null hides the card (no guest page served).
import * as wire from "./wire.js";

function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// Clipboard on a plain-HTTP LAN origin is not a secure context, so
// navigator.clipboard is often undefined there; fall back to a hidden
// textarea + execCommand, which still works in every venue browser.
function copyButton(text) {
  const btn = mk("button", "btn small", "Copy");
  const done = (label) => {
    btn.textContent = label;
    setTimeout(() => { btn.textContent = "Copy"; }, 1500);
  };
  btn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(text);
      done("Copied");
      return;
    } catch (_e) { /* fall through */ }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_e) { ok = false; }
    ta.remove();
    done(ok ? "Copied" : "Copy failed");
  };
  return btn;
}

function lineWithCopy(text) {
  const row = mk("div", "joinrow");
  row.appendChild(mk("code", "mono", text));
  row.appendChild(copyButton(text));
  return row;
}

export function render(info) {
  const card = document.getElementById("joinCard");
  clear(card);
  if (!info) { card.hidden = true; return; }
  card.hidden = false;
  card.appendChild(mk("h3", "railhead", "Join"));
  if (!info.app_present) {
    card.appendChild(mk("p", "muted",
      "No web build is staged under /app/ (pass --web-build to run_stack), " +
      "so this URL serves no page yet."));
  }
  card.appendChild(mk("p", "meta", "Guest page"));
  card.appendChild(lineWithCopy(info.www_url));

  const nodes = info.nodes || [];
  if (nodes.length === 0) {
    card.appendChild(mk("p", "muted", info.bit
      ? "This Bit declares no registration nodes."
      : "Load a Bit to get per-node join links."));
  }
  for (const row of nodes) {
    const block = mk("div", "joinnode");
    block.appendChild(mk("h4", null, `${row.role} · ${row.node}`));
    if (row.qr_svg) {
      // Server-rendered SVG from the trusted local Console (segno output,
      // no script content); the only innerHTML in the front end.
      const qr = mk("div", "qr");
      qr.innerHTML = row.qr_svg;
      block.appendChild(qr);
    }
    block.appendChild(lineWithCopy(row.url));
    block.appendChild(mk("p", "meta",
      "Tuneshroom (Chrome sim; run from the mm-tuneshroom checkout root):"));
    block.appendChild(lineWithCopy(row.tuneshroom_cmd));
    card.appendChild(block);
  }
  if (info.start) {
    const block = mk("div", "joinnode");
    block.appendChild(mk("h4", null, "Start (admin)"));
    if (info.start.qr_svg) {
      const qr = mk("div", "qr");
      qr.innerHTML = info.start.qr_svg;   // server-rendered segno SVG, trusted local Console
      block.appendChild(qr);
    }
    block.appendChild(lineWithCopy(info.start.url));
    block.appendChild(mk("p", "meta", "Key (also accepted on the device wire):"));
    block.appendChild(lineWithCopy(info.start.key));
    block.appendChild(lineWithCopy(info.start.wire));
    card.appendChild(block);
  }
  card.appendChild(mk("p", "muted", info.native_note || ""));
}

export function init() {
  wire.on("snapshot", (m) => render(m.join));
  wire.on("join_changed", (m) => render(m.join));
}
