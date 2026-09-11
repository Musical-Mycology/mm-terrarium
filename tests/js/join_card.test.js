"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

(async () => {
  const wire = await import("../../console/static/wire.js");
  const join = await import("../../console/static/join.js");
  join.init();
  wire.connect({ WebSocketImpl: FakeSocket });
  const sock = FakeSocket.instances.at(-1);
  sock.onopen();
  const send = (m) => sock.onmessage({ data: JSON.stringify(m) });

  const info = {
    www_url: "http://10.0.0.7:8788/app/", o2ws_host: "10.0.0.7:8080",
    ensemble: "arco", app_present: true, bit: "MetronomeBit",
    nodes: [{ role: "player", node: "METRO_PLAYER_NODE",
              url: "http://10.0.0.7:8788/app/?node=METRO_PLAYER_NODE",
              qr_svg: "<svg></svg>", tuneshroom_cmd: "flutter run" }],
    native_note: "",
    start: { url: "http://10.0.0.7:8788/start?key=metro-dev", qr_svg: "<svg id=\"sqr\"></svg>",
             key: "metro-dev", wire: '/game/start "ss" <dev> metro-dev' },
  };
  send({ event: "join_changed", join: info });
  const html = byId.get("joinCard").innerHTML;
  assert.ok(html.includes("Start"), "start heading");
  assert.ok(html.includes("http://10.0.0.7:8788/start?key=metro-dev"));
  assert.ok(html.includes("metro-dev"));
  assert.ok(html.includes('/game/start "ss"'));
  assert.ok(html.includes("sqr"), "start QR rendered");

  send({ event: "join_changed", join: { ...info, start: null } });
  assert.ok(!byId.get("joinCard").innerHTML.includes("/start?key="));
  console.log("join_card ok");
})();
