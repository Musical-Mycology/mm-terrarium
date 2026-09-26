// Shared DOM helpers: every console/static/*.js panel module used to carry
// its own byte-identical copy of these two functions. One copy here, per
// Tier 2 consolidation (docs/superpowers/specs/2026-09-25-tier2-
// consolidation-design.md section "PR B" item 1).

export function clear(node) {
  node.textContent = "";
}

export function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}
