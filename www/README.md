# www/ -- what the Arco server serves

Arco is launched from `arcoserver/` with `http_root = www`
(`arcoserver/arco_server_prefs.json`), reached through the `arcoserver/www`
symlink to this directory. O2's HTTP server rejects any served path
containing `..` (`o2/src/websock.cpp:905`), so `http_root` cannot be
`../www`; the symlink lets Arco's cwd stay at `arcoserver/` while still
serving this top-level directory. One port (8080) serves these files and
the o2ws websocket upgrade, so a page loaded from here reaches the O2 hub
with no configuration. Arco's directory index file is `index.htm`, not
`index.html`.

- `o2ws.js`: O2 over websockets, from the `o2` repo
  (`test/www/o2WebMonitor/o2ws.js` at commit d4dc921, 2024-08-21; the
  newest copy, with the optional host argument and the due-timestamp
  delivery fix). Text frames only: strings, times, doubles, floats, ints.
  No blob type (probe P2, 2026-09-08). Patched here (2026-09-08):
  `o2ws_schedule_handler` rounded the delay in seconds before scaling to
  milliseconds, so any timestamp under 500 ms ahead was delivered
  immediately; our copy rounds after scaling. Reported upstream;
  re-apply if the file is refreshed from the `o2` repo.
- `o2wsclocksync.htm`: the `o2` repo's clock-sync check page, ensemble set
  to `arco`. Open it to confirm the browser reaches the hub.
- `app/` (gitignored): the mm-tuneshroom guest app, built with `tool/sim
  build` there and copied here by `./smoke-test.sh --web-build
  /path/to/mm-tuneshroom/build/web`. Phones load it from the Terrarium's
  own static server (`WWW_URL`, port 8788, correct MIME types); the page
  opens its o2ws websocket to Arco on 8080. Arco also serves this tree but
  labels every file text/html, which the Flutter build cannot load under.
