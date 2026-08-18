const $ = (id) => document.getElementById(id);
let ws;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { $("conn").textContent = "(connected)"; $("conn").className = ""; };
  ws.onclose = () => {
    $("conn").textContent = "(disconnected — retrying)";
    $("conn").className = "conn-down";
    setTimeout(connect, 1000);
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

function send(command, extra) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(Object.assign({ command }, extra || {})));
  }
}

$("loadBtn").onclick = () => send("load_bit", { name: $("bitPicker").value });
$("runBtn").onclick = () => send("run");
$("abortBtn").onclick = () => send("abort");

function rows(tbodySel, data, cells) {
  const tbody = document.querySelector(tbodySel + " tbody");
  tbody.innerHTML = "";
  for (const item of data) {
    const tr = document.createElement("tr");
    for (const c of cells(item)) {
      const td = document.createElement("td");
      td.textContent = c;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function renderRegistration(reg) {
  rows("#registration", reg, (r) => [r.role, r.count, r.capacity ?? "∞"]);
}
function renderRoles(roles) {
  rows("#roles", roles, (r) => [r.role, r.class, r.capacity ?? "∞", r.scored,
    JSON.stringify(r.ugen_manifest), JSON.stringify(r.light_manifest)]);
}
function renderDevices(devs) {
  rows("#devices", devs, (d) => [d.dev, d.name, d.role ?? "—"]);
  renderTriggerDevices(devs);
}
function renderStatus(status) {
  rows("#bitStatus", Object.entries(status || {}), (kv) => [kv[0], kv[1]]);
}
function populateBits(bits) {
  const sel = $("bitPicker");
  sel.innerHTML = "";
  for (const b of bits) {
    const opt = document.createElement("option");
    opt.value = b; opt.textContent = b;
    sel.appendChild(opt);
  }
}
function log(level, message) {
  const el = $("log");
  el.textContent += `[${level}] ${message}\n`;
  el.scrollTop = el.scrollHeight;
}

function handle(msg) {
  switch (msg.event) {
    case "snapshot":
      $("state").textContent = msg.state;
      $("loaded").textContent = msg.loaded_bit ?? "—";
      populateBits(msg.installed_bits);
      renderRegistration(msg.registration);
      renderRoles(msg.roles);
      renderDevices(msg.devices);
      renderStatus(msg.bit_status);
      renderRoom(msg.room);
      renderTriggerDevices(msg.devices);
      renderTriggers(msg.triggers);
      break;
    case "state_changed":
      $("state").textContent = msg.state;
      log("info", "state → " + msg.state);
      break;
    case "registration_changed": renderRegistration(msg.roles); break;
    case "devices_changed": renderDevices(msg.devices); break;
    case "bit_status": renderStatus(msg.status); break;
    case "room_changed": renderRoom(msg.room); break;
    case "room_frame": renderRoomFrame(msg.channels); break;
    case "triggers_changed": renderTriggers(msg.triggers); break;
    case "trigger_fired": renderTriggerFired(msg.fired); break;
    case "bit_completed": log("info", "bit completed: " + JSON.stringify(msg.result)); break;
    case "error": log("error", msg.command + ": " + msg.message); break;
    case "log": log(msg.level, msg.message); break;
  }
}

connect();
