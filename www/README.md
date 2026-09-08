# www/ -- what the Arco server serves

Arco is launched from `arcoserver/` with `http_root = ../www` (this
directory) on port 8080 (`arcoserver/arco_server_prefs.json`). One port
serves these files and the o2ws websocket upgrade, so a page loaded from
here reaches the O2 hub with no configuration. Arco's directory index file
is `index.htm`, not `index.html`.

- `o2ws.js`: O2 over websockets, from the `o2` repo
  (`test/www/o2WebMonitor/o2ws.js` at commit d4dc921, 2024-08-21; the
  newest copy, with the optional host argument and the due-timestamp
  delivery fix). Text frames only: strings, times, doubles, floats, ints.
  No blob type (probe P2, 2026-09-08).
- `o2wsclocksync.htm`: the `o2` repo's clock-sync check page, ensemble set
  to `arco`. Open it to confirm the browser reaches the hub.
- `index.htm`: placeholder. The mm-tuneshroom web build deploys here in
  Phase 2 of `docs/superpowers/specs/2026-09-08-o2lite-connectivity-migration-design.md`.
