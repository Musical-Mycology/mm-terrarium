// Design panel: list of declared designs (draft/published pairs) rendered
// into #designList, a raw-TOML editor (#designText/#designErrors), and the
// Save/Publish/Clone actions. Wire shapes are Task 5's verbatim: commands
// list_designs/get_design/save_design/publish_design/clone_design; events
// designs_listed/design/designs_changed (the snapshot carries the same rows
// under "designs").
//
// Selecting a published design and hitting Save writes a draft of the same
// name (the draft-shadowing edit flow) -- the client always sends
// save_design with the selection's name; the server decides that a save on
// a published name lands as its draft.
import * as wire from "./wire.js";

let lastDesigns = [];      // last-seen designs_listed/designs_changed/snapshot rows
let current = null;        // {name, state} of the open design, or null

function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// -------------------------------------------------------------- rendering

// Pure renderer: one row per design, "<name> [draft|published]", plus an
// error badge when the entry's error is non-null. `onSelect(design)` fires
// on row click.
export function renderDesigns(listEl, designs, onSelect) {
  clear(listEl);
  for (const design of designs) {
    const row = mk("div", "design-row");
    row.appendChild(mk("span", "name", `${design.name} [${design.state}]`));
    if (design.error) row.appendChild(mk("span", "chip rose err", "error"));
    row.onclick = () => onSelect(design);
    listEl.appendChild(row);
  }
}

function onRowSelect(design) {
  wire.send("get_design", { state: design.state, name: design.name });
}

function render() {
  const listEl = document.getElementById("designList");
  renderDesigns(listEl, lastDesigns, onRowSelect);
  if (current) {
    for (const [i, design] of lastDesigns.entries()) {
      if (design.name === current.name && design.state === current.state) {
        listEl.children[i].classList.add("selected");
      }
    }
  }
}

function onDesignsChanged(designs) {
  lastDesigns = designs || [];
  render();
}

// Fills the editor from a `design` event: text, errors, and remembers the
// open selection so Save/Publish/Clone know what they're acting on.
export function openDesign(msg) {
  current = { name: msg.name, state: msg.state };
  document.getElementById("designText").value = msg.text;
  const errEl = document.getElementById("designErrors");
  clear(errEl);
  for (const error of (msg.errors || [])) {
    errEl.appendChild(mk("div", "err", error));
  }
  render();
}

// ---------------------------------------------------------------------- init

export function init() {
  const saveBtn = document.getElementById("designSave");
  const publishBtn = document.getElementById("designPublish");
  const cloneBtn = document.getElementById("designClone");

  saveBtn.onclick = () => {
    if (!current) return;
    const text = document.getElementById("designText").value;
    wire.send("save_design", { name: current.name, text }, saveBtn);
  };

  publishBtn.onclick = () => {
    if (!current) return;
    wire.send("publish_design", { name: current.name }, publishBtn);
  };

  cloneBtn.onclick = () => {
    if (!current) return;
    const newName = window.prompt("Clone as:");
    if (!newName) return;
    wire.send("clone_design", {
      source_state: current.state,
      source_name: current.name,
      new_name: newName,
    }, cloneBtn);
  };

  wire.on("snapshot", (m) => onDesignsChanged(m.designs));
  wire.on("designs_listed", (m) => onDesignsChanged(m.designs));
  wire.on("designs_changed", (m) => onDesignsChanged(m.designs));
  wire.on("design", (m) => openDesign(m));
}
