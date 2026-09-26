// Shared DOM helpers imported by every console panel module.

export function clear(node) {
  node.textContent = "";
}

export function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}
