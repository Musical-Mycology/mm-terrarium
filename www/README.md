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
  No blob type (probe P2, 2026-09-08). Three upstream defects are patched
  here (2026-09-08), each marked with a `// mm-terrarium patch` comment;
  all three were reported to Roger, and all three must be re-applied if the
  file is refreshed from the `o2` repo:
  1. Rounding order in `o2ws_schedule_handler`: upstream rounded the delay
     in seconds before scaling to milliseconds, so any timestamp under
     500 ms ahead was delivered immediately. Our copy rounds after scaling.
  2. Deferred-handler field snapshot, same function: the `o2ws_get_*`
     getters `shift()` off the single global `o2ws_message_fields`, which
     `o2ws_message_handler` reassigns for every inbound message, so a
     handler deferred by `setTimeout` read whichever payload arrived in the
     meantime. Our copy captures the fields array before scheduling and
     restores the global inside the deferred closure.
  3. `o2ws_on_error` naming: the websocket `onerror` path called
     `o2ws_error`, guarded by `typeof o2ws_error === 'function'`, but the
     documented application hook (header comment, and every other call
     site) is `o2ws_on_error`, so the guard never passed and the page never
     saw an abnormal-close error. Our copy calls `o2ws_on_error`.
- `index.htm`: Arco's directory index file (it does not serve
  `index.html`), a placeholder page linking to the clock check.
- `o2wsclocksync.htm`: the `o2` repo's clock-sync check page, ensemble set
  to `arco`. Open it to confirm the browser reaches the hub.
- `app/` (gitignored): the mm-tuneshroom guest app, built with `tool/sim
  build` there and copied here by `./smoke-test.sh --web-build
  /path/to/mm-tuneshroom/build/web`. Phones load it from the Terrarium's
  own static server (`WWW_URL`, port 8788, correct MIME types); the page
  opens its o2ws websocket to Arco on 8080. Arco also serves this tree but
  labels every file text/html, which the Flutter build cannot load under.
  The copy rewrites `index.html`'s `<base href="/">` to `<base href="/app/">`
  (the page is served under `/app/`, not the server root), so a plain
  `tool/sim build` is enough -- no `--base-href` flag needed.
