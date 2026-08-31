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
//     the debounced rebuild. applyEdit re-renders the forms directly
//     afterward (there's no in-place patching), so to keep a keyboard/mouse
//     user's focus from falling out to document.body on every toggle, it
//     captures the focused control's identity before rebuilding and
//     restores it after.
//
// Every form control carries a stable `data-form-key` attribute identifying
// its role -- "description" for the identity field, "cap:<name>" for a
// capability checkbox, "cue:<name>" for a cue checkbox. applyEdit reads
// document.activeElement's data-form-key (plus selectionStart/selectionEnd
// for text inputs) before rebuilding, then re-focuses (and restores the
// caret on) whichever freshly-rendered control carries the same key. Tasks
// 4-6 must give their own controls a data-form-key for the same reason --
// any field wired through applyEdit loses focus on every edit otherwise.
//
// This task builds identity (description), capabilities, and accepted_cues.
// Later tasks hang additional sections off SECTION_BUILDERS -- each entry is
// `(container, text, apply) -> void`, called against #formSections on every
// rebuild.
import * as wire from "./wire.js";
import {
  getScalar,
  setScalar,
  getStringArray,
  setStringArray,
  splitBlocks,
  getThresholds,
  setThreshold,
  appendBlock,
  removeBlock,
} from "./toml_edit.js";

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

// Depth-first search under `root` for the control carrying `key` as its
// data-form-key attribute -- how applyEdit relocates the focused control
// after rebuild() has thrown away and recreated the whole tree.
function findByFormKey(root, key) {
  if (!root) return null;
  if (root.getAttribute && root.getAttribute("data-form-key") === key) return root;
  for (const child of root.children || []) {
    const found = findByFormKey(child, key);
    if (found) return found;
  }
  return null;
}

// Shared apply implementation form controls call to write an edit through:
// reads #designText, runs `fn(text) -> newText`, writes the result back
// (without tripping the debounced raw->forms rebuild), then re-renders the
// forms immediately so the UI reflects the edit. rebuild() has no in-place
// patching -- it clears and recreates every control -- so a naive rebuild
// here would drop focus to document.body on every keystroke/toggle. Instead
// this captures the focused control's data-form-key (and, for text inputs,
// its caret) before rebuilding, then finds the freshly-rendered control with
// the same key afterward and restores focus (and the caret) onto it.
export function applyEdit(fn) {
  const textEl = document.getElementById("designText");
  const panel = document.getElementById("formsPanel");

  const active = document.activeElement;
  const activeKey = active && active.getAttribute ? active.getAttribute("data-form-key") : null;
  const selStart = active && typeof active.selectionStart === "number" ? active.selectionStart : null;
  const selEnd = active && typeof active.selectionEnd === "number" ? active.selectionEnd : null;

  const newText = fn(textEl.value);
  guard = true;
  textEl.value = newText;
  guard = false;
  rebuild(newText);

  if (activeKey) {
    const restored = findByFormKey(panel, activeKey);
    if (restored && restored.focus) {
      restored.focus();
      if (selStart != null && typeof restored.selectionStart === "number") {
        restored.selectionStart = selStart;
        restored.selectionEnd = selEnd != null ? selEnd : selStart;
      }
    }
  }
}

// One checkbox row: a <label> wrapping a checkbox and its text, appended to
// `container`. `onToggle(checked)` fires on change. `key` is the
// checkbox's data-form-key ("cap:<name>" / "cue:<name>").
function checkboxRow(container, name, key, checked, onToggle) {
  const label = mk("label", "checkitem");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  input.setAttribute("data-form-key", key);
  input.onchange = () => onToggle(input.checked);
  label.appendChild(input);
  label.appendChild(document.createTextNode(name));
  container.appendChild(label);
}

// Renders one checkgrid (capabilities or accepted_cues) from `vocabList`
// (the full, sorted set of options) against `arrayKey`'s current array in
// `text`. Toggling a box writes the new array back via setStringArray,
// preserving vocabList's order. `keyPrefix` ("cap"/"cue") namespaces each
// checkbox's data-form-key.
function rebuildCheckgrid(elId, vocabList, arrayKey, keyPrefix, text) {
  const el = document.getElementById(elId);
  clear(el);
  const current = getStringArray(text, arrayKey) || [];
  for (const name of vocabList) {
    checkboxRow(el, name, `${keyPrefix}:${name}`, current.includes(name), (checked) => {
      const next = checked
        ? vocabList.filter((v) => current.includes(v) || v === name)
        : current.filter((v) => v !== name);
      applyEdit((t) => setStringArray(t, arrayKey, next));
    });
  }
}

function rebuildDescription(text) {
  const input = document.getElementById("formDescription");
  input.setAttribute("data-form-key", "description");
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

// One numeric input row bound to a threshold/param key, writing through
// setThreshold on change. `opts` forwards {header, childTable} for
// non-event-trigger callers (stream triggers reuse this machinery against
// `[stream_triggers.params]`).
function thresholdRow(container, name, key, value, keyPrefix, opts) {
  const row = mk("label", "field");
  row.appendChild(document.createTextNode(`${key} `));
  const input = document.createElement("input");
  input.type = "number";
  input.step = "any";
  input.value = value;
  input.setAttribute("data-form-key", `${keyPrefix}:${name}:${key}`);
  input.onchange = () => {
    applyEdit((t) => setThreshold(t, name, key, input.value, opts));
  };
  row.appendChild(input);
  container.appendChild(row);
}

// One [[event_triggers]] card: name label, description input, one numeric
// input per thresholds key, an add-threshold key/value pair, and a Remove
// button.
function buildEventTriggerCard(container, text, name) {
  const card = mk("div", "card");
  card.setAttribute("data-form-key", `trig:${name}`);
  card.appendChild(mk("h4", null, name));

  const descLabel = mk("label", "field");
  descLabel.appendChild(document.createTextNode("Description "));
  const descInput = document.createElement("input");
  descInput.type = "text";
  const rawDesc = getScalar(text, { header: "[[event_triggers]]", name }, "description");
  let descValue = "";
  if (rawDesc != null) {
    try { descValue = JSON.parse(rawDesc); } catch { descValue = rawDesc; }
  }
  descInput.value = descValue;
  descInput.setAttribute("data-form-key", `trig:${name}:description`);
  descInput.onchange = () => {
    applyEdit((t) => setScalar(t, { header: "[[event_triggers]]", name }, "description", JSON.stringify(descInput.value)));
  };
  descLabel.appendChild(descInput);
  card.appendChild(descLabel);

  const thresholds = getThresholds(text, name) || {};
  for (const key of Object.keys(thresholds)) {
    thresholdRow(card, name, key, thresholds[key], "trig");
  }

  const addRow = mk("div", "field");
  const addKeyInput = document.createElement("input");
  addKeyInput.type = "text";
  addKeyInput.placeholder = "key";
  addKeyInput.setAttribute("data-form-key", `trig:${name}:add-key`);
  const addValueInput = document.createElement("input");
  addValueInput.type = "number";
  addValueInput.step = "any";
  addValueInput.placeholder = "value";
  addValueInput.setAttribute("data-form-key", `trig:${name}:add-value`);
  const addBtn = document.createElement("button");
  addBtn.textContent = "Add threshold";
  addBtn.setAttribute("data-form-key", `trig:${name}:add-threshold`);
  addBtn.onclick = () => {
    if (!addKeyInput.value) return;
    applyEdit((t) => setThreshold(t, name, addKeyInput.value, addValueInput.value || 0));
  };
  addRow.appendChild(addKeyInput);
  addRow.appendChild(addValueInput);
  addRow.appendChild(addBtn);
  card.appendChild(addRow);

  const removeBtn = document.createElement("button");
  removeBtn.textContent = "Remove";
  removeBtn.setAttribute("data-form-key", `trig:${name}:remove`);
  removeBtn.onclick = () => {
    applyEdit((t) => removeBlock(t, "[[event_triggers]]", name));
  };
  card.appendChild(removeBtn);

  container.appendChild(card);
}

// One [[stream_triggers]] card: description input, read-only verb/arg/
// transform, and one numeric input per `params` key (reusing the
// thresholds machinery against the `params` child table).
function buildStreamTriggerCard(container, text, name) {
  const card = mk("div", "card");
  card.setAttribute("data-form-key", `strig:${name}`);
  card.appendChild(mk("h4", null, name));

  const descLabel = mk("label", "field");
  descLabel.appendChild(document.createTextNode("Description "));
  const descInput = document.createElement("input");
  descInput.type = "text";
  const rawDesc = getScalar(text, { header: "[[stream_triggers]]", name }, "description");
  let descValue = "";
  if (rawDesc != null) {
    try { descValue = JSON.parse(rawDesc); } catch { descValue = rawDesc; }
  }
  descInput.value = descValue;
  descInput.setAttribute("data-form-key", `strig:${name}:description`);
  descInput.onchange = () => {
    applyEdit((t) => setScalar(t, { header: "[[stream_triggers]]", name }, "description", JSON.stringify(descInput.value)));
  };
  descLabel.appendChild(descInput);
  card.appendChild(descLabel);

  const verb = getScalar(text, { header: "[[stream_triggers]]", name }, "verb");
  const arg = getScalar(text, { header: "[[stream_triggers]]", name }, "arg");
  const transform = getScalar(text, { header: "[[stream_triggers]]", name }, "transform");
  card.appendChild(mk("p", "muted", `verb ${verb ?? ""} / arg ${arg ?? ""} / transform ${transform ?? ""}`));

  const opts = { header: "[[stream_triggers]]", childTable: "params" };
  const params = getThresholds(text, name, opts) || {};
  for (const key of Object.keys(params)) {
    thresholdRow(card, name, key, params[key], "strig", opts);
  }

  container.appendChild(card);
}

// Section builder for event/stream triggers, pushed onto SECTION_BUILDERS.
// Renders one card per [[event_triggers]] block, one card per
// [[stream_triggers]] block, and an "Add event trigger" button that
// appends a new block (name via window.prompt, Clone's idiom -- decline on
// falsy).
function buildTriggerSection(container, text, apply) {
  const blocks = splitBlocks(text);
  for (const block of blocks) {
    if (block.header === "[[event_triggers]]" && block.name) {
      buildEventTriggerCard(container, text, block.name);
    }
  }
  for (const block of blocks) {
    if (block.header === "[[stream_triggers]]" && block.name) {
      buildStreamTriggerCard(container, text, block.name);
    }
  }

  const addBtn = document.createElement("button");
  addBtn.textContent = "Add event trigger";
  addBtn.setAttribute("data-form-key", "add-event-trigger");
  addBtn.onclick = () => {
    const name = window.prompt("Trigger name");
    if (!name) return;
    const block = `[[event_triggers]]
name = "${name}"
description = ""
  [event_triggers.thresholds]
  peak_g = 2.0
  window_ms = 200`;
    apply((t) => appendBlock(t, block));
  };
  container.appendChild(addBtn);
}

SECTION_BUILDERS.push(buildTriggerSection);

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
  rebuildCheckgrid("formCapabilities", vocab.capabilities || [], "capabilities", "cap", text);
  rebuildCheckgrid("formCues", vocab.cue_kinds || [], "accepted_cues", "cue", text);

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
