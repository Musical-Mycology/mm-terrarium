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
// Destructive actions never restore focus by key: applyEdit(fn, {restoreFocus:
// false}) skips the by-key refocus entirely. Removing a row (a script step,
// an event/stream trigger's threshold, a function card, etc.) shifts every
// later row's index down, so a key like "fn:<name>:step:2:remove" now names
// a DIFFERENT row after rebuild -- restoring focus onto it would silently
// arm the next row's destructive control for a stray Enter/double-click.
// Every Remove button (and any other index/name-shifting destructive action)
// must pass {restoreFocus: false}; non-destructive edits keep the default
// (true) restore behavior.
//
// This task builds identity (description), capabilities, and accepted_cues.
// Later tasks hang additional sections off SECTION_BUILDERS -- each entry is
// `(container, text, apply) -> void`, called against #formSections on every
// rebuild.
//
// Two design kinds share this panel. An instrument design gets the identity
// / capabilities / cues header plus every SECTION_BUILDERS section; a room
// design gets one section only -- Fixtures: the room's `[[fixtures]]` order
// with an instrument picker per fixture, over the same raw TOML editor.
// `currentKind` tracks the open design's kind (from the `design` event,
// defaulting to "instrument" for an older server) so that a form edit or a
// debounced raw->forms rebuild re-renders the SAME kind of form rather than
// silently falling back to the instrument layout.
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
  listScriptSteps,
  setScriptStep,
  addScriptStep,
  removeScriptStep,
  listAmbientLight,
  listAmbientUgen,
  setAmbientLightRow,
  setAmbientUgenRow,
  listFixtures,
  moveFixture,
  setFixtureInstrument,
} from "./toml_edit.js";

let vocab = { capabilities: [], cue_kinds: [] };
let lastDesigns = [];      // last-seen catalog rows (both kinds) -- populates the fixture instrument picker
let currentKind = "instrument";  // kind of the open design, from the `design` event
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
export function applyEdit(fn, opts = {}) {
  const { restoreFocus = true } = opts;
  const textEl = document.getElementById("designText");
  const panel = document.getElementById("formsPanel");

  const active = document.activeElement;
  const activeKey = restoreFocus && active && active.getAttribute ? active.getAttribute("data-form-key") : null;
  const selStart = active && typeof active.selectionStart === "number" ? active.selectionStart : null;
  const selEnd = active && typeof active.selectionEnd === "number" ? active.selectionEnd : null;

  const newText = fn(textEl.value);
  guard = true;
  textEl.value = newText;
  guard = false;
  rebuild(newText, currentKind);

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
    applyEdit((t) => removeBlock(t, "[[event_triggers]]", name), { restoreFocus: false });
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

// One text/number input row bound to a top-level scalar in a [[functions]]
// block, writing through setScalar on change. `type` is the input's HTML
// type ("text"/"number"); `parse`/`render` convert between the raw TOML
// scalar text and the input's display value (default: raw string in,
// JSON.stringify out for quoted-string fields).
function functionFieldRow(container, name, key, text, type, opts = {}) {
  const { parse = (v) => v, render = (v) => v } = opts;
  const row = mk("label", "field");
  row.appendChild(document.createTextNode(`${key} `));
  const input = document.createElement("input");
  input.type = type;
  if (type === "number") input.step = "any";
  const raw = getScalar(text, { header: "[[functions]]", name }, key);
  input.value = raw != null ? parse(raw) : "";
  input.setAttribute("data-form-key", `fn:${name}:${key}`);
  input.onchange = () => {
    applyEdit((t) => setScalar(t, { header: "[[functions]]", name }, key, render(input.value)));
  };
  row.appendChild(input);
  container.appendChild(row);
  return input;
}

// One [[functions]] card with kind = "generator": text/number inputs for
// waveform/period/lo/hi, read-only lane info (rewiring the lane stays a
// raw-TOML edit in v1), and a Remove button.
function buildGeneratorCard(container, text, name) {
  const card = mk("div", "card");
  card.setAttribute("data-form-key", `fn:${name}`);
  card.appendChild(mk("h4", null, name));

  functionFieldRow(card, name, "waveform", text, "text", {
    parse: (v) => { try { return JSON.parse(v); } catch { return v; } },
    render: (v) => JSON.stringify(v),
  });
  functionFieldRow(card, name, "period", text, "number");
  functionFieldRow(card, name, "lo", text, "number");
  functionFieldRow(card, name, "hi", text, "number");

  const lane = getThresholds(text, name, { header: "[[functions]]", childTable: "lane" }) || {};
  const laneParts = Object.keys(lane).map((k) => `${k} = ${lane[k]}`);
  card.appendChild(mk("p", "muted", `lane: ${laneParts.join(", ")} (edit lane wiring in raw TOML)`));

  const removeBtn = document.createElement("button");
  removeBtn.textContent = "Remove";
  removeBtn.setAttribute("data-form-key", `fn:${name}:remove`);
  removeBtn.onclick = () => {
    applyEdit((t) => removeBlock(t, "[[functions]]", name), { restoreFocus: false });
  };
  card.appendChild(removeBtn);

  container.appendChild(card);
}

// One row of a scripted function's step table: offset input, read-only kind
// chip, args text input (Save-on-change via setScriptStep), Remove button.
function scriptStepRow(container, name, index, step) {
  const row = mk("div", "field");

  const offsetInput = document.createElement("input");
  offsetInput.type = "number";
  offsetInput.step = "any";
  offsetInput.value = step.offset;
  offsetInput.setAttribute("data-form-key", `fn:${name}:step:${index}:offset`);
  offsetInput.onchange = () => {
    applyEdit((t) => setScriptStep(t, name, index, { offset: offsetInput.value, kind: step.kind, args: argsInput.value }));
  };
  row.appendChild(offsetInput);

  row.appendChild(mk("span", "chip", step.kind));

  const argsInput = document.createElement("input");
  argsInput.type = "text";
  argsInput.value = step.args;
  argsInput.setAttribute("data-form-key", `fn:${name}:step:${index}:args`);
  argsInput.onchange = () => {
    applyEdit((t) => setScriptStep(t, name, index, { offset: offsetInput.value, kind: step.kind, args: argsInput.value }));
  };
  row.appendChild(argsInput);

  const removeBtn = document.createElement("button");
  removeBtn.textContent = "Remove";
  removeBtn.setAttribute("data-form-key", `fn:${name}:step:${index}:remove`);
  removeBtn.onclick = () => {
    applyEdit((t) => removeScriptStep(t, name, index), { restoreFocus: false });
  };
  row.appendChild(removeBtn);

  container.appendChild(row);
}

// One [[functions]] card with kind = "scripted": description input, a step
// table (one row per script step), an "add step" row, and a Remove button.
function buildScriptedCard(container, text, name) {
  const card = mk("div", "card");
  card.setAttribute("data-form-key", `fn:${name}`);
  card.appendChild(mk("h4", null, name));

  const descLabel = mk("label", "field");
  descLabel.appendChild(document.createTextNode("Description "));
  const descInput = document.createElement("input");
  descInput.type = "text";
  const rawDesc = getScalar(text, { header: "[[functions]]", name }, "description");
  let descValue = "";
  if (rawDesc != null) {
    try { descValue = JSON.parse(rawDesc); } catch { descValue = rawDesc; }
  }
  descInput.value = descValue;
  descInput.setAttribute("data-form-key", `fn:${name}:description`);
  descInput.onchange = () => {
    applyEdit((t) => setScalar(t, { header: "[[functions]]", name }, "description", JSON.stringify(descInput.value)));
  };
  descLabel.appendChild(descInput);
  card.appendChild(descLabel);

  const steps = listScriptSteps(text, name) || [];
  steps.forEach((step, index) => scriptStepRow(card, name, index, step));

  const addRow = mk("div", "field");
  const addOffsetInput = document.createElement("input");
  addOffsetInput.type = "number";
  addOffsetInput.step = "any";
  addOffsetInput.placeholder = "offset";
  addOffsetInput.setAttribute("data-form-key", `fn:${name}:step:add-offset`);
  const addKindSelect = document.createElement("select");
  addKindSelect.setAttribute("data-form-key", `fn:${name}:step:add-kind`);
  for (const kind of ["midi", "play", "solid", "mute"]) {
    const opt = document.createElement("option");
    opt.value = kind;
    opt.textContent = kind;
    addKindSelect.appendChild(opt);
  }
  const addArgsInput = document.createElement("input");
  addArgsInput.type = "text";
  addArgsInput.placeholder = "args";
  addArgsInput.setAttribute("data-form-key", `fn:${name}:step:add-args`);
  const addBtn = document.createElement("button");
  addBtn.textContent = "Add step";
  addBtn.setAttribute("data-form-key", `fn:${name}:step:add`);
  addBtn.onclick = () => {
    applyEdit((t) => addScriptStep(t, name, {
      offset: addOffsetInput.value || 0,
      kind: addKindSelect.value,
      args: addArgsInput.value,
    }));
  };
  addRow.appendChild(addOffsetInput);
  addRow.appendChild(addKindSelect);
  addRow.appendChild(addArgsInput);
  addRow.appendChild(addBtn);
  card.appendChild(addRow);

  const removeBtn = document.createElement("button");
  removeBtn.textContent = "Remove";
  removeBtn.setAttribute("data-form-key", `fn:${name}:remove`);
  removeBtn.onclick = () => {
    applyEdit((t) => removeBlock(t, "[[functions]]", name), { restoreFocus: false });
  };
  card.appendChild(removeBtn);

  container.appendChild(card);
}

// Section builder for functions, pushed onto SECTION_BUILDERS. Renders one
// card per [[functions]] block, branched on its `kind` scalar. No "add
// function" button in v1 -- a new function starts as a raw-TOML paste or a
// clone; a muted hint line notes this under the section.
function buildFunctionSection(container, text) {
  const blocks = splitBlocks(text);
  for (const block of blocks) {
    if (block.header !== "[[functions]]" || !block.name) continue;
    const kind = getScalar(text, { header: "[[functions]]", name: block.name }, "kind");
    let parsedKind = kind;
    try { parsedKind = JSON.parse(kind); } catch { /* already bare */ }
    if (parsedKind === "generator") {
      buildGeneratorCard(container, text, block.name);
    } else if (parsedKind === "scripted") {
      buildScriptedCard(container, text, block.name);
    }
  }

  container.appendChild(mk("p", "muted", "New functions start as a raw-TOML paste or a clone."));
}

SECTION_BUILDERS.push(buildFunctionSection);

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

// One ambient instrument row: instrument text input plus kind-specific
// fields (light: target text input; ugen: program/key/velocity number
// inputs), rewriting the whole `instruments = [...]` line on any change.
function ambientRow(container, kind, row) {
  const wrapper = mk("div", "field");

  const instrumentInput = document.createElement("input");
  instrumentInput.type = "text";
  instrumentInput.value = row.instrument;
  instrumentInput.setAttribute("data-form-key", `amb:${kind}:${row.index}:instrument`);
  wrapper.appendChild(instrumentInput);

  if (kind === "light") {
    const targetInput = document.createElement("input");
    targetInput.type = "text";
    targetInput.value = row.target;
    targetInput.setAttribute("data-form-key", `amb:${kind}:${row.index}:target`);
    const onChange = () => {
      applyEdit((t) => setAmbientLightRow(t, row.index, {
        instrument: instrumentInput.value,
        target: targetInput.value,
      }));
    };
    instrumentInput.onchange = onChange;
    targetInput.onchange = onChange;
    wrapper.appendChild(targetInput);
  } else {
    const programInput = document.createElement("input");
    programInput.type = "number";
    programInput.step = "any";
    programInput.value = row.program != null ? row.program : "";
    programInput.setAttribute("data-form-key", `amb:${kind}:${row.index}:program`);

    const keyInput = document.createElement("input");
    keyInput.type = "number";
    keyInput.step = "any";
    keyInput.value = row.key != null ? row.key : "";
    keyInput.setAttribute("data-form-key", `amb:${kind}:${row.index}:key`);

    const velocityInput = document.createElement("input");
    velocityInput.type = "number";
    velocityInput.step = "any";
    velocityInput.value = row.velocity != null ? row.velocity : "";
    velocityInput.setAttribute("data-form-key", `amb:${kind}:${row.index}:velocity`);

    const onChange = () => {
      applyEdit((t) => setAmbientUgenRow(t, row.index, {
        instrument: instrumentInput.value,
        program: programInput.value,
        key: keyInput.value,
        velocity: velocityInput.value,
      }));
    };
    instrumentInput.onchange = onChange;
    programInput.onchange = onChange;
    keyInput.onchange = onChange;
    velocityInput.onchange = onChange;
    wrapper.appendChild(programInput);
    wrapper.appendChild(keyInput);
    wrapper.appendChild(velocityInput);
  }

  container.appendChild(wrapper);
}

// Section builder for ambient manifests, pushed onto SECTION_BUILDERS.
// Renders one row per `[ambient.light]`/`[ambient.ugen]` instruments entry
// (matched by header suffix so both the shorthand and the shipped
// fully-qualified `[instruments.<name>.ambient.light]` form resolve). An
// instrument with neither block renders a muted hint instead -- authoring a
// new ambient block from scratch stays raw-TOML in v1.
function buildAmbientSection(container, text) {
  const lightRows = listAmbientLight(text);
  const ugenRows = listAmbientUgen(text);

  if (!lightRows && !ugenRows) {
    container.appendChild(mk("p", "muted", "no ambient declaration (add via raw TOML)"));
    return;
  }

  if (lightRows) {
    container.appendChild(mk("h4", null, "Ambient light"));
    for (const row of lightRows) ambientRow(container, "light", row);
  }
  if (ugenRows) {
    container.appendChild(mk("h4", null, "Ambient ugen"));
    for (const row of ugenRows) ambientRow(container, "ugen", row);
  }
}

SECTION_BUILDERS.push(buildAmbientSection);

// The whole structured editor for a room design: the `[[fixtures]]` order,
// one row each, with the fixture's name, an instrument picker (published
// instruments from the last catalog rows, sorted), and Up/Down buttons.
// Rooms carry no description/capabilities/accepted_cues, so none of the
// instrument identity controls render here.
//
// Reordering renumbers every later fixture, so Up/Down pass
// {restoreFocus: false} -- the same rule the destructive controls follow:
// "fixture:2:up" names a DIFFERENT fixture after the rebuild, and
// restoring focus onto it would arm the wrong row for a stray Enter.
function buildFixtureSection(panel, text) {
  panel.appendChild(mk("h4", null, "Fixtures"));
  const fixtures = listFixtures(text);
  if (!fixtures.length) {
    panel.appendChild(mk("p", "muted", "no [[fixtures]] declared (add via raw TOML)"));
    return;
  }
  const published = lastDesigns
    .filter((d) => (d.kind || "instrument") === "instrument" && d.state === "published")
    .map((d) => d.name).sort();
  fixtures.forEach((fx, i) => {
    const row = mk("div", "fixture-row");
    row.appendChild(mk("span", "name", fx.name || `(fixture ${i})`));

    const pick = document.createElement("select");
    pick.setAttribute("data-form-key", `fixture:${i}:instrument`);
    for (const name of published) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      if (name === fx.instrument) opt.selected = true;
      pick.appendChild(opt);
    }
    pick.onchange = () => applyEdit((t) => setFixtureInstrument(t, i, pick.value));
    row.appendChild(pick);

    const up = mk("button", "btn", "Up");
    up.setAttribute("data-form-key", `fixture:${i}:up`);
    up.disabled = i === 0;
    up.onclick = () => applyEdit((t) => moveFixture(t, i, -1), { restoreFocus: false });
    row.appendChild(up);

    const down = mk("button", "btn", "Down");
    down.setAttribute("data-form-key", `fixture:${i}:down`);
    down.disabled = i === fixtures.length - 1;
    down.onclick = () => applyEdit((t) => moveFixture(t, i, 1), { restoreFocus: false });
    row.appendChild(down);

    panel.appendChild(row);
  });
  panel.appendChild(mk("p", "muted", "Order applies at the next Room load."));
}

// Re-renders every form section from `text`, laid out for `kind`. With no
// design open (no selection and/or empty text), renders a single muted
// placeholder line and no inputs. A room renders only the Fixtures section.
export function rebuild(text, kind = "instrument") {
  const panel = document.getElementById("formsPanel");
  clear(panel);

  if (!text) {
    panel.appendChild(mk("p", "muted", "select a design"));
    return;
  }

  if (kind === "room") {
    buildFixtureSection(panel, text);
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
    if (m.designs) lastDesigns = m.designs;
  });
  wire.on("designs_listed", (m) => { lastDesigns = m.designs || []; });
  wire.on("designs_changed", (m) => { lastDesigns = m.designs || []; });
  wire.on("design", (m) => {
    currentKind = m.kind || "instrument";
    rebuild(m.text, currentKind);
  });

  const textEl = document.getElementById("designText");
  textEl.oninput = () => {
    if (guard) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => rebuild(textEl.value, currentKind), DEBOUNCE_MS);
  };

  rebuild(null);
}
