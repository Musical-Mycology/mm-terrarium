"use strict";
// Pure, line-based transforms over the small TOML subset the design forms
// edit: top-level scalars/arrays, `[[event_triggers]]` / `[[stream_triggers]]`
// / `[[functions]]` array-of-table blocks (each optionally carrying an
// indented child table such as `[event_triggers.thresholds]`), and
// `[ambient.light]` / `[ambient.ugen]` tables. No DOM, no wire imports --
// text in, text or a value out. Every writer returns the input unchanged
// when its target isn't found, mirroring design.js's applyProposal contract.

const TOP_HEADER_RE = /^\[\[?[A-Za-z0-9_.]+\]?\]$/;
const ARRAY_TABLE_HEADER_RE = /^\[\[[A-Za-z0-9_.]+\]\]$/;
const NAME_RE = /^\s*name\s*=\s*"([^"]*)"\s*$/;

// -- splitBlocks --------------------------------------------------------

// Scans `text` into blocks: the top-level scalar region before the first
// unindented `[` line (header: null), then one block per unindented
// `[...]`/`[[...]]` line through (but not including) the next one.
// Indented child-table lines (e.g. `  [event_triggers.thresholds]`) belong
// to the enclosing block, not a block of their own.
export function splitBlocks(text) {
  const lines = text.split("\n");
  const blocks = [];
  let starts = [];
  for (let i = 0; i < lines.length; i++) {
    if (TOP_HEADER_RE.test(lines[i])) starts.push(i);
  }
  const firstHeader = starts.length ? starts[0] : lines.length;
  blocks.push({ header: null, name: null, start: 0, end: firstHeader });
  for (let s = 0; s < starts.length; s++) {
    const start = starts[s];
    const end = s + 1 < starts.length ? starts[s + 1] : lines.length;
    let name = null;
    for (let i = start + 1; i < end; i++) {
      const m = lines[i].match(NAME_RE);
      if (m) { name = m[1]; break; }
    }
    blocks.push({ header: lines[start], name, start, end });
  }
  return blocks;
}

function findBlock(text, header, name) {
  const blocks = splitBlocks(text);
  return blocks.find((b) => b.header === header && b.name === name) || null;
}

// -- getScalar / setScalar -----------------------------------------------

function scalarRe(key) {
  return new RegExp(`^(\\s*)${key}\\s*=\\s*(.+)$`);
}

export function getScalar(text, block, key) {
  const lines = text.split("\n");
  let start = 0, end = lines.length;
  if (block === null) {
    const b = splitBlocks(text)[0];
    start = b.start; end = b.end;
  } else {
    const b = findBlock(text, block.header, block.name);
    if (!b) return null;
    start = b.start; end = b.end;
  }
  const re = scalarRe(key);
  for (let i = start; i < end; i++) {
    const m = lines[i].match(re);
    if (m) return m[2];
  }
  return null;
}

export function setScalar(text, block, key, raw) {
  const lines = text.split("\n");
  let start, end;
  if (block === null) {
    const b = splitBlocks(text)[0];
    start = b.start; end = b.end;
  } else {
    const b = findBlock(text, block.header, block.name);
    if (!b) return text;
    start = b.start; end = b.end;
  }
  const re = scalarRe(key);
  for (let i = start; i < end; i++) {
    const m = lines[i].match(re);
    if (m) {
      lines[i] = `${m[1]}${key} = ${raw}`;
      return lines.join("\n");
    }
  }
  // Absent: append as the last line of the block, matching the block's
  // dominant (most common non-blank) indentation.
  let lastBodyIdx = end - 1;
  while (lastBodyIdx > start && lines[lastBodyIdx].trim() === "") lastBodyIdx--;
  const indentCounts = new Map();
  for (let i = start; i < end; i++) {
    if (lines[i].trim() === "") continue;
    const m = lines[i].match(/^(\s*)/);
    const ind = m[1];
    indentCounts.set(ind, (indentCounts.get(ind) || 0) + 1);
  }
  let dominant = "";
  let best = -1;
  for (const [ind, count] of indentCounts) {
    if (count > best) { best = count; dominant = ind; }
  }
  const newLine = `${dominant}${key} = ${raw}`;
  lines.splice(lastBodyIdx + 1, 0, newLine);
  return lines.join("\n");
}

// -- getStringArray / setStringArray (top-level only) ---------------------

function stringArrayRe(key) {
  return new RegExp(`^${key}\\s*=\\s*\\[(.*)\\]\\s*$`);
}

export function getStringArray(text, key) {
  const lines = text.split("\n");
  const top = splitBlocks(text)[0];
  const re = stringArrayRe(key);
  for (let i = top.start; i < top.end; i++) {
    const m = lines[i].match(re);
    if (m) {
      const inner = m[1].trim();
      if (inner === "") return [];
      return inner.split(",").map((s) => s.trim().replace(/^"(.*)"$/, "$1"));
    }
  }
  return null;
}

export function setStringArray(text, key, values) {
  const lines = text.split("\n");
  const top = splitBlocks(text)[0];
  const re = stringArrayRe(key);
  for (let i = top.start; i < top.end; i++) {
    if (re.test(lines[i])) {
      const rendered = values.map((v) => `"${v}"`).join(", ");
      lines[i] = `${key} = [${rendered}]`;
      return lines.join("\n");
    }
  }
  return text;
}

// -- getThresholds / setThreshold -----------------------------------------
//
// `opts` (both optional) lets callers reuse this machinery for a different
// array-of-table block / child-table pair than `[[event_triggers]]` /
// `[event_triggers.thresholds]` -- e.g. stream triggers' `[[stream_triggers]]`
// / `[stream_triggers.params]`. Defaults preserve the original callers.

const THRESHOLD_LINE_RE = /^(\s*)([A-Za-z0-9_]+)\s*=\s*(.+)\s*$/;

function childTableHeaderRe(header, childTable) {
  const blockName = header.replace(/^\[\[|\]\]$/g, "");
  return new RegExp(`^\\s*\\[${blockName}\\.${childTable}\\]\\s*$`);
}

function findThresholdsRange(text, triggerName, header = "[[event_triggers]]", childTable = "thresholds") {
  const block = findBlock(text, header, triggerName);
  if (!block) return null;
  const lines = text.split("\n");
  const headerRe = childTableHeaderRe(header, childTable);
  let thresholdsIdx = -1;
  for (let i = block.start; i < block.end; i++) {
    if (headerRe.test(lines[i])) { thresholdsIdx = i; break; }
  }
  if (thresholdsIdx === -1) return null;
  let bodyEnd = thresholdsIdx + 1;
  while (bodyEnd < block.end && lines[bodyEnd].trim() !== "") bodyEnd++;
  return { block, thresholdsIdx, bodyEnd };
}

export function getThresholds(text, triggerName, opts = {}) {
  const { header = "[[event_triggers]]", childTable = "thresholds" } = opts;
  const range = findThresholdsRange(text, triggerName, header, childTable);
  if (!range) return null;
  const lines = text.split("\n");
  const out = {};
  for (let i = range.thresholdsIdx + 1; i < range.bodyEnd; i++) {
    const m = lines[i].match(THRESHOLD_LINE_RE);
    if (m) out[m[2]] = Number(m[3]);
  }
  return out;
}

export function setThreshold(text, triggerName, key, value, opts = {}) {
  const { header = "[[event_triggers]]", childTable = "thresholds" } = opts;
  const range = findThresholdsRange(text, triggerName, header, childTable);
  if (!range) return text;
  const lines = text.split("\n");
  for (let i = range.thresholdsIdx + 1; i < range.bodyEnd; i++) {
    const m = lines[i].match(THRESHOLD_LINE_RE);
    if (m && m[2] === key) {
      lines[i] = `${m[1]}${key} = ${value}`;
      return lines.join("\n");
    }
  }
  // Absent: append inside the thresholds table, matching its indentation;
  // never touch a `# calibrated from` comment line above the header.
  const indentMatch = lines[range.thresholdsIdx].match(/^(\s*)/);
  const indent = indentMatch[1];
  lines.splice(range.bodyEnd, 0, `${indent}${key} = ${value}`);
  return lines.join("\n");
}

// -- listScriptSteps / setScriptStep / addScriptStep / removeScriptStep ---

const SCRIPT_OPEN_RE = /^\s*script\s*=\s*\[\s*$/;
const SCRIPT_CLOSE_RE = /^\s*\]\s*$/;
const STEP_LINE_RE = /^\s*\{\s*offset\s*=\s*([-\d.]+)\s*,\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*\},?\s*$/;

function findScriptRange(text, functionName) {
  const block = findBlock(text, "[[functions]]", functionName);
  if (!block) return null;
  const lines = text.split("\n");
  let openIdx = -1;
  for (let i = block.start; i < block.end; i++) {
    if (SCRIPT_OPEN_RE.test(lines[i])) { openIdx = i; break; }
  }
  if (openIdx === -1) return null;
  let closeIdx = -1;
  for (let i = openIdx + 1; i < block.end; i++) {
    if (SCRIPT_CLOSE_RE.test(lines[i])) { closeIdx = i; break; }
  }
  if (closeIdx === -1) return null;
  return { block, openIdx, closeIdx };
}

export function listScriptSteps(text, functionName) {
  const range = findScriptRange(text, functionName);
  if (!range) return null;
  const lines = text.split("\n");
  const steps = [];
  for (let i = range.openIdx + 1; i < range.closeIdx; i++) {
    const m = lines[i].match(STEP_LINE_RE);
    if (!m) continue;
    steps.push({
      line: i,
      offset: Number(m[1]),
      kind: m[2],
      args: m[3],
    });
  }
  return steps;
}

export function setScriptStep(text, functionName, index, step) {
  const steps = listScriptSteps(text, functionName);
  if (!steps || index < 0 || index >= steps.length) return text;
  const lines = text.split("\n");
  const lineIdx = steps[index].line;
  lines[lineIdx] = `  { offset = ${step.offset}, ${step.kind} = ${step.args} },`;
  return lines.join("\n");
}

export function addScriptStep(text, functionName, step) {
  const range = findScriptRange(text, functionName);
  if (!range) return text;
  const lines = text.split("\n");
  const newLine = `  { offset = ${step.offset}, ${step.kind} = ${step.args} },`;
  lines.splice(range.closeIdx, 0, newLine);
  return lines.join("\n");
}

export function removeScriptStep(text, functionName, index) {
  const steps = listScriptSteps(text, functionName);
  if (!steps || index < 0 || index >= steps.length) return text;
  const lines = text.split("\n");
  lines.splice(steps[index].line, 1);
  return lines.join("\n");
}

// -- appendBlock / removeBlock ---------------------------------------------

export function appendBlock(text, blockText) {
  return `${text}\n${blockText}\n`;
}

export function removeBlock(text, header, name) {
  const block = findBlock(text, header, name);
  if (!block) return text;
  const lines = text.split("\n");
  lines.splice(block.start, block.end - block.start);
  return lines.join("\n");
}

// -- ambient instruments (listAmbientLight/Ugen, setAmbientLightRow/UgenRow) --
//
// `[ambient.light]` / `[ambient.ugen]` tables hold a single
// `instruments = [ {...}, {...} ]` line -- an inline-table array, same shape
// as script steps but on one line instead of one line per entry. Shipped
// catalog files use the fully-qualified header
// (`[instruments.venue_array.ambient.light]`), so blocks are matched by
// header suffix (".ambient.light]"/".ambient.ugen]") as well as by the
// shorthand ("[ambient.light]"/"[ambient.ugen]").

function splitTopLevel(inner) {
  const parts = [];
  let depth = 0;
  let cur = "";
  for (const ch of inner) {
    if (ch === "{") depth++;
    if (ch === "}") depth--;
    if (ch === "," && depth === 0) {
      parts.push(cur);
      cur = "";
      continue;
    }
    cur += ch;
  }
  if (cur.trim() !== "") parts.push(cur);
  return parts.map((p) => p.trim()).filter((p) => p !== "");
}

function parseInlineEntry(raw) {
  const body = raw.trim().replace(/^\{\s*/, "").replace(/\s*\}$/, "");
  const fields = {};
  for (const kv of splitTopLevel(body)) {
    const eq = kv.indexOf("=");
    if (eq === -1) continue;
    fields[kv.slice(0, eq).trim()] = kv.slice(eq + 1).trim();
  }
  return fields;
}

function unquote(raw) {
  if (raw == null) return "";
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

const AMBIENT_INSTR_LINE_RE = /^(\s*)instruments\s*=\s*\[(.*)\]\s*$/;

function findAmbientLine(text, kind) {
  const blocks = splitBlocks(text);
  const suffix = `.ambient.${kind}]`;
  const shorthand = `[ambient.${kind}]`;
  const block = blocks.find(
    (b) => b.header && (b.header.endsWith(suffix) || b.header === shorthand),
  );
  if (!block) return null;
  const lines = text.split("\n");
  for (let i = block.start; i < block.end; i++) {
    const m = lines[i].match(AMBIENT_INSTR_LINE_RE);
    if (m) return { block, line: i, indent: m[1], inner: m[2] };
  }
  return null;
}

// Returns null when no `[ambient.light]` block (qualified or shorthand)
// exists; otherwise one row per entry of its `instruments` array.
export function listAmbientLight(text) {
  const found = findAmbientLine(text, "light");
  if (!found) return null;
  return splitTopLevel(found.inner).map((raw, index) => {
    const f = parseInlineEntry(raw);
    return { index, instrument: unquote(f.instrument), target: unquote(f.target) };
  });
}

export function setAmbientLightRow(text, index, { instrument, target }) {
  const found = findAmbientLine(text, "light");
  if (!found) return text;
  const entries = splitTopLevel(found.inner);
  if (index < 0 || index >= entries.length) return text;
  entries[index] = `{ instrument = ${JSON.stringify(instrument)}, target = ${JSON.stringify(target)} }`;
  const lines = text.split("\n");
  lines[found.line] = `${found.indent}instruments = [ ${entries.join(", ")} ]`;
  return lines.join("\n");
}

// Returns null when no `[ambient.ugen]` block (qualified or shorthand)
// exists; otherwise one row per entry, with `key`/`velocity` present only
// when the entry carries a nested `drone = { ... }` table.
export function listAmbientUgen(text) {
  const found = findAmbientLine(text, "ugen");
  if (!found) return null;
  return splitTopLevel(found.inner).map((raw, index) => {
    const f = parseInlineEntry(raw);
    const row = {
      index,
      instrument: unquote(f.instrument),
      program: f.program != null ? Number(f.program) : null,
    };
    if (f.drone != null) {
      const d = parseInlineEntry(f.drone);
      row.key = d.key != null ? Number(d.key) : null;
      row.velocity = d.velocity != null ? Number(d.velocity) : null;
    }
    return row;
  });
}

// Rewrites the ugen entry at `index` wholesale. A `drone = { key, velocity }`
// nested table is included only when both `key` and `velocity` are
// non-empty; otherwise the entry drops (or stays without) a drone.
export function setAmbientUgenRow(text, index, { instrument, program, key, velocity }) {
  const found = findAmbientLine(text, "ugen");
  if (!found) return text;
  const entries = splitTopLevel(found.inner);
  if (index < 0 || index >= entries.length) return text;
  let entry = `{ instrument = ${JSON.stringify(instrument)}, program = ${program}`;
  if (key !== null && key !== undefined && key !== "" && velocity !== null && velocity !== undefined && velocity !== "") {
    entry += `, drone = { key = ${key}, velocity = ${velocity} }`;
  }
  entry += " }";
  entries[index] = entry;
  const lines = text.split("\n");
  lines[found.line] = `${found.indent}instruments = [ ${entries.join(", ")} ]`;
  return lines.join("\n");
}
