// Design forms: structured (non-raw) editors for the currently-open design,
// mounted into #formsPanel above the raw #designText textarea. The textarea
// stays the single source of truth -- every form control reads its value by
// parsing #designText's current text with toml_edit.js's pure transforms,
// and every edit writes back through the same transforms rather than
// tracking its own copy of the design.
//
// Two directions of sync:
//   - raw -> forms: a manual edit in #designText (an "input" event) rebuilds
//     the forms, debounced 300ms so a fast typist doesn't thrash the DOM on
//     every keystroke.
//   - forms -> raw: a form edit calls applyEdit(fn), which reads the
//     textarea, runs fn(text) -> newText through a toml_edit transform, and
//     writes the result back -- guarded so that write does NOT re-trigger
//     the debounced rebuild (which would nuke whatever form control still
//     has focus). applyEdit still re-renders the forms directly afterward,
//     so the UI reflects the edit immediately.
//
// This task builds identity (description), capabilities, and accepted_cues.
// Later tasks hang additional sections off SECTION_BUILDERS -- each entry is
// `(container, text, apply) -> void`, called against #formSections on every
// rebuild.
import * as wire from "./wire.js";
import { getScalar, setScalar, getStringArray, setStringArray } from "./toml_edit.js";

let vocab = { capabilities: [], cue_kinds: [] };
let guard = false;         // true while applyEdit is writing #designText -- suppresses its "input" listener
let debounceTimer = null;

const DEBOUNCE_MS = 300;

export const SECTION_BUILDERS = [];

function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// Shared apply implementation form controls call to write an edit through:
// reads #designText, runs `fn(text) -> newText`, writes the result back
// (without tripping the debounced raw->forms rebuild), then re-renders the
// forms immediately so the UI reflects the edit.
export function applyEdit(fn) {
  const textEl = document.getElementById("designText");
  const newText = fn(textEl.value);
  guard = true;
  textEl.value = newText;
  guard = false;
  rebuild(newText);
}

// One checkbox row: a <label> wrapping a checkbox and its text, appended to
// `container`. `onToggle(checked)` fires on change.
function checkboxRow(container, name, checked, onToggle) {
  const label = mk("label", "checkitem");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  input.onchange = () => onToggle(input.checked);
  label.appendChild(input);
  label.appendChild(document.createTextNode(name));
  container.appendChild(label);
}

// Renders one checkgrid (capabilities or accepted_cues) from `vocabList`
// (the full, sorted set of options) against `key`'s current array in
// `text`. Toggling a box writes the new array back via setStringArray,
// preserving vocabList's order.
function rebuildCheckgrid(elId, vocabList, key, text) {
  const el = document.getElementById(elId);
  clear(el);
  const current = getStringArray(text, key) || [];
  for (const name of vocabList) {
    checkboxRow(el, name, current.includes(name), (checked) => {
      const next = checked
        ? vocabList.filter((v) => current.includes(v) || v === name)
        : current.filter((v) => v !== name);
      applyEdit((t) => setStringArray(t, key, next));
    });
  }
}

function rebuildDescription(text) {
  const input = document.getElementById("formDescription");
  const raw = getScalar(text, null, "description");
  let value = "";
  if (raw != null) {
    try {
      value = JSON.parse(raw);
    } catch {
      value = raw;
    }
  }
  input.value = value;
  input.onchange = () => {
    applyEdit((t) => setScalar(t, null, "description", JSON.stringify(input.value)));
  };
}

// Re-renders every form section from `text`. With no design open (no
// selection and/or empty text), renders a single muted placeholder line and
// no inputs.
export function rebuild(text) {
  const panel = document.getElementById("formsPanel");
  clear(panel);

  if (!text) {
    panel.appendChild(mk("p", "muted", "select a design"));
    return;
  }

  const descLabel = mk("label", null, "Description ");
  const descInput = document.createElement("input");
  descInput.type = "text";
  descInput.id = "formDescription";
  descLabel.appendChild(descInput);
  panel.appendChild(descLabel);

  const capsDiv = mk("div", "checkgrid");
  capsDiv.id = "formCapabilities";
  panel.appendChild(capsDiv);

  const cuesDiv = mk("div", "checkgrid");
  cuesDiv.id = "formCues";
  panel.appendChild(cuesDiv);

  const sectionsDiv = document.createElement("div");
  sectionsDiv.id = "formSections";
  panel.appendChild(sectionsDiv);

  rebuildDescription(text);
  rebuildCheckgrid("formCapabilities", vocab.capabilities || [], "capabilities", text);
  rebuildCheckgrid("formCues", vocab.cue_kinds || [], "accepted_cues", text);

  for (const builder of SECTION_BUILDERS) {
    builder(sectionsDiv, text, applyEdit);
  }
}

export function initForms() {
  wire.on("snapshot", (m) => {
    if (m.design_vocab) vocab = m.design_vocab;
  });
  wire.on("design", (m) => rebuild(m.text));

  const textEl = document.getElementById("designText");
  textEl.oninput = () => {
    if (guard) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => rebuild(textEl.value), DEBOUNCE_MS);
  };

  rebuild(null);
}
