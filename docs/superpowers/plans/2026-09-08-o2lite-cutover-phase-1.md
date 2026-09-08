# o2lite Cutover, Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make o2lite the only device wire in mm-terrarium: delete the Control-side websocket transport and its drivers, pin Control's ownership of the o2lite services string, make O2 time the agent's only clock, chunk telemetry under the o2lite message cap, and start Arco with its HTTP server serving a `www/` root so Phase 2's browser page has a server.

**Architecture:** `harness/terrarium_boot.py` stops branching on a transport and always builds `O2LiteTransport` on the o2litepy singleton pyarco already synced; `build()` takes `transport` and `clock` as required arguments. `DeviceLinkServer`, `room_simulator.py`, `devicelink_smoke.py` and `capture_smoke.py` are deleted; `harness/o2_shroom.py` becomes the only Testshroom, absorbing the `--identify-blocks` debug tool, and the small `WebSimLeds` adapter it needs moves to its own module. `O2LiteTransport.start()` refuses to run before pyarco has announced `actl` and verifies both `actl` and `game` route back after it writes the full string. `devicelink/protocol.py` gains a reference chunker that splits a telemetry batch so every blob fits under 4096 bytes. Arco is launched from a committed `arcoserver/` directory whose prefs file turns on the HTTP server with `www/` as its root.

**Tech Stack:** Python 3.14 in `.venv`, pytest, o2litepy reached by `PYTHONPATH` (unchanged in this phase), Arco `apps/pytest/server` from the sibling `arco` checkout, `o2ws.js` from the sibling `o2` checkout.

**Spec:** `docs/superpowers/specs/2026-09-08-o2lite-connectivity-migration-design.md`, section 4 Phase 1, section 5 constraints, section 6 probes P2, P3, P5. Section 8 (out of scope) and the Phase 2 and 3 sections are not this plan.

## Global Constraints

- Tests ONLY via `.venv/bin/python -m pytest tests -q` from the repo root (a fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`). Baseline at this branch's base `6a4a365`: **1986 passed, 1 skipped**. Every task ends with the full suite green; the count drops by the deleted tests and rises by the new ones, and each task records the number it landed on.
- **The offline-suite invariant holds.** Nothing under `tests/` needs a network, an Arco server, or an importable o2litepy. `FakeO2Lite` (`devicelink/o2_transport.py:180`) stands in for the connection, and it must never be more permissive than o2litepy.
- **`devicelink/` and `control/` never import o2litepy.** `harness/` may, lazily, after `ensure_o2litepy()`.
- **One o2lite connection per process** (spec section 5). No second `O2lite()` instance anywhere.
- **4096 bytes is the o2lite message cap** (`o2/src/o2lite.h:135`). The telemetry blob budget is that minus fixed headroom.
- **The wire vocabulary does not change** (spec section 3). Addresses, typespecs and the blob rule are as today.
- The Console (`console/server.py`) and the Uplink (`uplink/transport.py`) keep their websockets. `websockets>=13` stays in `requirements.txt`.
- No em dashes anywhere (code, comments, docs, commit messages). The repo's `--` style is fine.
- Shell commands the operator runs are labeled with the host in bold caps. Everything in this plan runs on **MYCOLOGICAL** (this dev box, the one with the `arco` and `o2` sibling checkouts).
- Commit after every task with the prefix shown in the task.

---

## File structure

| File | Responsibility after this plan |
|------|-------------------------------|
| `devicelink/o2_transport.py` | Control's `game` service on the Arco hub, and the only device transport. Owns `PYARCO_SERVICE`, `CONTROL_SERVICE`, `SERVICES`; `start()` refuses to run before pyarco announced `actl` and verifies both services after claiming. `FakeO2Lite` unchanged. |
| `devicelink/protocol.py` | Wire vocabulary source of truth (unchanged), plus `O2_MAX_MSG_LEN`, `TELEMETRY_BLOB_BUDGET`, `encode_telemetry_body`, `chunk_telemetry_batch`. |
| `devicelink/agent.py` | Unchanged behavior; `clock` becomes a required keyword argument. |
| `devicelink/server.py` | **Deleted.** |
| `devicelink/__init__.py` | Docstring names the o2lite transport. |
| `harness/terrarium_boot.py` | Always o2lite. `build(..., *, transport, clock, ...)`; `_o2lite_module()` seam; `_O2SimulatorFactory` the only factory; `--transport` and `--port` gone; Arco launched with `cwd=arcoserver/`; prints the Arco www URL. |
| `harness/run_stack.py` | No `--flutter-sim`/`--flutter-devices`, no `FLUTTER_LINK`; `control_command` no longer passes `--transport`. |
| `harness/websim_leds.py` (new) | `WebSimLeds`, `BLOCK_PALETTE`, `identify_blocks_frame`, moved out of the deleted simulator. |
| `harness/o2_shroom.py` | The only Testshroom. Imports `WebSimLeds` from `harness.websim_leds`; gains `--identify-blocks`. |
| `harness/shroom_client.py` | `ShroomClient` and `LED_CHANNELS` (shared wire logic) stay; the websocket `main()`, `pump_tick` and the `ws://` docstring go. |
| `harness/room_simulator.py`, `harness/devicelink_smoke.py`, `harness/capture_smoke.py` | **Deleted.** |
| `control/arco_process.py` | `pty_popen(command, log_path=None, cwd=None)`. |
| `harness/markers.py` | `ARCO_WWW` marker. |
| `arcoserver/arco_server_prefs.json` (new) | Arco prefs: `http_root = ../www`, `http_port = 8080`. Arco's cwd. |
| `www/o2ws.js`, `www/o2wsclocksync.htm`, `www/index.htm`, `www/README.md` (new) | What Arco serves. `o2ws.js` is the `o2` repo's newest copy, provenance recorded. |
| `.gitignore` | `arcoserver/o2debug.log`. |
| `docs/telemetry-trace-schema.md` | Chunking rule. |
| `docs/MM_TERRARIUM.md`, `README.md`, the spec's Status | Recorded. |
| Tests | `tests/test_devicelink_server.py`, `test_devicelink_smoke.py`, `test_devicelink_smoke_cli.py`, `test_room_simulator.py`, `test_capture_smoke.py` deleted; `tests/test_websim_leds.py`, `tests/test_telemetry_chunks.py`, `tests/test_capture_o2.py`, `tests/test_arcoserver.py` new; targeted edits in `test_terrarium_boot.py`, `test_run_stack.py`, `test_o2_transport.py`, `test_devicelink_agent.py`, `test_shroom_client.py`, `test_markers.py`, `test_signals.py`, `test_wire_json_boundaries.py`, `test_arco_pty.py`. |

---

### Task 1: Probe P3, does the `o2` repo's o2litepy install and run the suite

**Files:**
- Modify: `docs/superpowers/specs/2026-09-08-o2lite-connectivity-migration-design.md` (P3 section, record the result)

**Interfaces:**
- Consumes: nothing.
- Produces: a recorded pass/fail for P3. No code changes in this phase either way; Phase 3 consumes the result.

This is a probe, not TDD. Nothing it builds is committed except the spec note. **RUN ON: MYCOLOGICAL.**

- [ ] **Step 1: Create a throwaway venv and install o2litepy from the `o2` repo**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/test-harness-testshroom-ac2047
python3 -m venv /tmp/p3-venv 2>/dev/null || /Users/chris/projects/mm-terrarium/.venv/bin/python -m venv /tmp/p3-venv
/tmp/p3-venv/bin/python -m pip install -q -r requirements-dev.txt
/tmp/p3-venv/bin/python -m pip install -q -e /Users/chris/projects/o2/o2litepy
/tmp/p3-venv/bin/python -m pip install -q -e "/Users/chris/projects/luxaeterna[websim]"
/tmp/p3-venv/bin/python -c "import o2litepy, inspect; print(inspect.getfile(o2litepy))"
```

Expected: the last line prints a path under `/Users/chris/projects/o2/o2litepy/src/o2litepy/`. If the venv creation fails because there is no `python3`, use the second form (the project venv's interpreter can create another venv).

- [ ] **Step 2: Run the offline suite with no `PYTHONPATH` and no `MM_ARCO_PATH`**

```bash
env -u PYTHONPATH -u MM_ARCO_PATH /tmp/p3-venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3
```

Expected: the same pass count as the baseline (1986 passed, 1 skipped at the moment this task runs; if later tasks have landed first, the then-current count).

- [ ] **Step 3: Run the live stack with the installed package, still with no `PYTHONPATH` for o2litepy**

`pyarco` is not installable, so `PYTHONPATH` must still point at the `arco` checkout for it. The probe question is whether o2litepy comes from site-packages when both are visible. Put the installed package first:

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/test-harness-testshroom-ac2047
PYTHONPATH=/Users/chris/projects/arco /tmp/p3-venv/bin/python -c "import o2litepy, inspect; print(inspect.getfile(o2litepy))"
```

Note which copy wins (this tells Phase 3 whether `sys.path` order needs handling). Then:

```bash
PYTHONPATH=/Users/chris/projects/arco /tmp/p3-venv/bin/python -m harness.run_stack --ci --seconds 20 --devices 1 2>&1 | tail -15
```

Expected: the CI run reports green, same as `./smoke-test.sh --ci --seconds 20 --devices 1` does with the project venv.

- [ ] **Step 4: Record the result in the spec and clean up**

In the spec's `### P3` section, append a `**Result (2026-09-08).**` paragraph: pass or fail, the `o2` commit installed (`git -C /Users/chris/projects/o2 log -1 --format=%h -- o2litepy`), which copy won on `sys.path` with both visible, and the exact command that ran green or the exact failure. Then:

```bash
rm -rf /tmp/p3-venv
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-08-o2lite-connectivity-migration-design.md
git commit -m "docs(spec): record probe P3 result, o2litepy from the o2 repo"
```

---

### Task 2: Probe P2, Arco serves a page and o2ws reaches Control

**Files:**
- Modify: `docs/superpowers/specs/2026-09-08-o2lite-connectivity-migration-design.md` (P2 section, record the result)

**Interfaces:**
- Consumes: the facts in this task's preamble.
- Produces: a recorded result that Task 8 (Arco serving) and Phase 2 depend on. Expected findings, to be confirmed or refuted: (1) Arco's `apps/pytest/server` binary has websocket support compiled in (`BUILD_WITH_WEBSOCKET_SUPPORT` defaults ON in `o2/CMakeLists.txt:25`); (2) a page served by Arco clock-syncs over o2ws; (3) `o2ws.js` carries no blob type, so a `b`-typed message such as `/ie99/room` does not decode in the browser.

Facts established by reading the sources (do not re-derive):

- Arco reads `arco_server_prefs.json` from its **current working directory** (`arco/server/src/prefs.cpp:72,139`). No file exists anywhere today, so every run gets defaults.
- There is no `http_enable` key. The HTTP server is on when `http_root` is non-empty (`prefs.cpp:197-209`). `http_port` defaults to 8080. `http_root` is relative to Arco's cwd and is capped at 119 characters.
- One port serves both files and the o2ws websocket upgrade (`o2/src/websock.cpp:218-239, 877-903`). The directory index file is `index.htm`, not `index.html`.
- `o2ws.js` opens `ws://<page host>/o2ws`, so a page served by Arco needs no endpoint configuration. Use the newest copy, `/Users/chris/projects/o2/test/www/o2WebMonitor/o2ws.js` (commit `d4dc921`, 2024-08-21): it adds an optional `host` argument to `o2ws_initialize` and fixes delivery of messages whose timestamp is already due.
- Arco's cwd under `harness/terrarium_boot.py` today is the mm-terrarium repo root (Arco is spawned with no `cwd`, and `smoke-test.sh` does `cd` to the repo root).

**RUN ON: MYCOLOGICAL** throughout.

- [ ] **Step 1: Stage the pages in the scratchpad**

Use this session's scratchpad directory (listed in the environment as the scratchpad; create a `p2-www` subdirectory in it). Copy in `o2ws.js` and the clock-sync page, and set the ensemble to `arco`:

```bash
S=<scratchpad>/p2-www
mkdir -p "$S"
cp /Users/chris/projects/o2/test/www/o2WebMonitor/o2ws.js "$S/o2ws.js"
cp /Users/chris/projects/o2/test/www/o2wsclocksync.htm "$S/o2wsclocksync.htm"
grep -n 'o2ws_initialize(' "$S/o2wsclocksync.htm"
```

Edit the `o2ws_initialize("test")` call the grep shows to `o2ws_initialize("arco")`.

- [ ] **Step 2: Write the Control round-trip probe page**

Create `$S/probe.htm`:

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>o2ws probe</title>
<script src="o2ws.js"></script>
<script>
var DEV = "ie99";
var log = function (s) {
  var el = document.getElementById("log");
  el.textContent += s + "\n";
};
function o2ws_status_msg(s) { log("status: " + s); }
function o2ws_on_error(s) { log("error: " + s); }
function room_handler(timestamp, address, typespec, info) {
  log("got " + address + " types=" + typespec + " at " + timestamp);
  // o2ws.js has no o2ws_get_blob; report what the parser left us.
  try { log("  as string: " + o2ws_get_string()); } catch (e) { log("  get_string failed: " + e); }
}
function role_handler(timestamp, address, typespec, info) {
  log("got " + address + " types=" + typespec);
}
function start() {
  o2ws_initialize("arco");
  o2ws_set_services(DEV);
  o2ws_method_new("/" + DEV + "/room", null, true, room_handler, null);
  o2ws_method_new("/" + DEV + "/role", null, true, role_handler, null);
  o2ws_method_new("/" + DEV + "/deny", "ss", true, function () {
    log("deny: " + o2ws_get_string() + " / " + o2ws_get_string()); }, null);
  var tries = 0;
  var timer = setInterval(function () {
    tries += 1;
    if (o2ws_clock_synchronized) {
      clearInterval(timer);
      log("clock synced, O2 time " + o2ws_time_get());
      o2ws_send_cmd("/game/hello", 0, "ssss", DEV, "", "", "testshroom");
      log("sent /game/hello");
      setTimeout(function () {
        o2ws_send_cmd("/game/join", 0, "ss", DEV, "TEST_PLAYER_NODE");
        log("sent /game/join");
      }, 1000);
    } else if (tries > 100) {
      clearInterval(timer);
      log("no clock sync after 10 s");
    }
  }, 100);
}
</script></head>
<body onload="start()"><pre id="log"></pre></body></html>
```

- [ ] **Step 3: Point Arco at the pages and bring the stack up**

Write the prefs file where Arco will look, the repo root (temporary; it is removed in Step 6):

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/test-harness-testshroom-ac2047
cat > arco_server_prefs.json <<EOF
{
  "__configuration__": [
    {"__configuration__": "default"}
  ],
  "default": [
    {"http_root": "$S"},
    {"http_port": "8080"}
  ]
}
EOF
./smoke-test.sh --serve --devices 0 --setup-seconds 600
```

`--serve` holds the stack with Control in SETUP; leave it running in the background (use `run_in_background`) and watch its log for `CONTROL_TRANSPORT_READY`. If `$S` is longer than 119 characters, Arco truncates it silently: use a shorter scratch path (for example `/tmp/p2-www`, then delete it in Step 6).

- [ ] **Step 4: Load the clock-sync page**

Open `http://127.0.0.1:8080/o2wsclocksync.htm` in the browser tool. Expected: the page reports a bridge id and then displays O2 time advancing once per second. If the page does not load at all, Arco's HTTP server is not on: check Arco's log (`runs/<run>/arco.log`) for the `finished reading arco_server_prefs.json` line and for any `o2_http_initialize` failure. If the file loads but the websocket never connects, the binary was built without websocket support; that is the finding, and Task 8 is blocked on rebuilding Arco with `BUILD_WITH_WEBSOCKET_SUPPORT` on.

- [ ] **Step 5: Load the probe page and read the log**

Open `http://127.0.0.1:8080/probe.htm`. Expected sequence in the page's log: `clock synced`, `sent /game/hello`, then a `got /ie99/room` line (Control pushes room on hello), then `sent /game/join`, then either `got /ie99/role` or a `deny`. Record exactly what the `room` handler printed for the `b` argument: the expectation is that the blob does not decode. Also record Control's side: `runs/<run>/control.log` should show `ie99` in the device pool.

- [ ] **Step 6: Tear down and record**

Stop the background stack (Ctrl-C equivalent: kill the `run_stack` process; it tears down Arco and Control in order). Then:

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/test-harness-testshroom-ac2047
rm -f arco_server_prefs.json
git status --short
```

`git status` must show nothing from this probe. In the spec's `### P2` section append `**Result (2026-09-08).**` with: whether files served, whether clock sync happened, what `/ie99/room` looked like in the browser (the blob finding), and whether the join produced a role or a deny. If the blob did not decode, add one sentence under the spec's Phase 2 section 4 (session shape): "Confirmed 2026-09-08 by probe P2: `o2ws.js` has no blob type, so `/ie<N>/role`, `/ie<N>/room` and `/ie<N>/leds` need either an o2ws blob extension or a string-typed variant before a browser can render. Decide at Phase 2 start."

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/specs/2026-09-08-o2lite-connectivity-migration-design.md
git commit -m "docs(spec): record probe P2 result, Arco serving and o2ws reach"
```

---

### Task 3: Control owns the services string

**Files:**
- Modify: `devicelink/o2_transport.py:62-66` (constants), `:309-352` (`start()`)
- Modify: `tests/test_o2_transport.py` (the `_started()` helper and every direct `start(` call), `tests/test_terrarium_boot.py:335-352`
- Test: `tests/test_o2_transport.py`

**Interfaces:**
- Consumes: `verify_service_ownership(o2lite, service, *, timeout, resend_interval, clock, sleep) -> bool` (`o2_transport.py:122`).
- Produces: `PYARCO_SERVICE = "actl"`, `CONTROL_SERVICE = "game"`, `SERVICES = "actl,game"` (unchanged value). `O2LiteTransport.start(o2lite, ...)` raises `RuntimeError` if `actl` is not already announced on the connection, and after claiming verifies both `game` (existing message, unchanged) and `actl` (new message). Every test that starts a transport on a `FakeO2Lite` must first call `fake.set_services("actl")`, exactly what `arco.initialize()` does at `pyarco/arco_engine.py:98`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_o2_transport.py`:

```python
def test_start_refuses_before_pyarco_has_announced_actl():
    """Control shares pyarco's o2lite connection. arco.initialize() writes
    "actl" first (pyarco/arco_engine.py:98); Control then writes the whole
    string. Starting before that would have Control's set_services erase
    nothing and then pyarco's later call erase `game`. Fail loud instead."""
    from devicelink.o2_transport import FakeO2Lite, O2LiteTransport

    fake = FakeO2Lite()                 # services == "" : pyarco not yet up
    transport = O2LiteTransport()
    with pytest.raises(RuntimeError, match="actl"):
        transport.start(fake)
    assert fake.services == ""          # never wrote over pyarco's slot


def test_start_verifies_actl_still_routes_after_claiming_game():
    """set_services REPLACES (o2lite.py:707). Writing "actl,game" must leave
    pyarco's control replies working, and O2 refuses a claim silently, so
    the only proof is a round trip on actl too."""
    from devicelink.o2_transport import FakeO2Lite, O2LiteTransport

    fake = FakeO2Lite()
    fake.set_services("actl")
    fake.refuse("actl")
    transport = O2LiteTransport()
    with pytest.raises(RuntimeError, match="actl"):
        transport.start(fake, ownership_timeout=0.05, sleep=lambda s: None)


def test_services_string_is_pyarco_then_control():
    from devicelink.o2_transport import (CONTROL_SERVICE, PYARCO_SERVICE,
                                         SERVICES)

    assert PYARCO_SERVICE == "actl"
    assert CONTROL_SERVICE == "game"
    assert SERVICES == "actl,game"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_o2_transport.py -q -k "pyarco or actl_still or pyarco_then"`
Expected: 3 failed (`ImportError` on `PYARCO_SERVICE`; the first two do not raise).

- [ ] **Step 3: Implement**

Replace `devicelink/o2_transport.py:62-66` with:

```python
# The complete services string this PROCESS offers on its one o2lite
# connection. pyarco announces PYARCO_SERVICE first (arco.initialize(),
# pyarco/arco_engine.py:98); Control then owns the full string, because
# set_services REPLACES rather than appends (o2litepy o2lite.py:707).
# One connection per process is o2lite's model (spec 2026-09-08 section
# 2): the C library keeps its connection in process statics, so a second
# connection is not an option on hardware and is not used here either.
PYARCO_SERVICE = "actl"
CONTROL_SERVICE = "game"
SERVICES = f"{PYARCO_SERVICE},{CONTROL_SERVICE}"
```

In `start()`, immediately after the existing unsynced-clock check (`if now < 0: raise ...`, lines 325-329) and before `self._o2 = o2lite`, insert:

```python
        announced = [name for name in
                     getattr(o2lite, "services", "").split(",") if name]
        if PYARCO_SERVICE not in announced:
            raise RuntimeError(
                f"pyarco has not announced {PYARCO_SERVICE!r} on this o2lite "
                f"connection yet (services={announced}); Control's transport "
                f"must start after arco.initialize() returns")
```

Leave the existing `game` verification and its error message exactly as they are. Immediately after that block (after the `raise RuntimeError("the `game` service did not route back ...")` statement's `if`), add:

```python
        if not verify_service_ownership(o2lite, PYARCO_SERVICE,
                                        timeout=ownership_timeout,
                                        resend_interval=2.0,
                                        clock=clock, sleep=sleep):
            self._o2 = None
            raise RuntimeError(
                f"claiming {self._services!r} left {PYARCO_SERVICE!r} not "
                f"routed back to this connection: pyarco's Arco control "
                f"replies would be lost. set_services replaces the whole "
                f"string; check SERVICES still names every service this "
                f"process offers")
```

Update `start()`'s docstring's first line to "Adopt pyarco's already-connected o2lite object and claim the full services string on it."

- [ ] **Step 4: Update the existing tests to announce `actl` first**

In `tests/test_o2_transport.py`, find the `_started()` helper and every other place a transport is started on a fake:

```bash
grep -n "start(fake\|\.start(" tests/test_o2_transport.py tests/test_terrarium_boot.py
```

For each `FakeO2Lite()` that is then passed to `transport.start(...)`, add `fake.set_services("actl")` right after construction. For the two custom doubles in `tests/test_o2_transport.py` (around lines 321 and 370, `_FakeO2LiteAnsweringAfter` and `_FakeO2LiteNeverAnswers`), give them `services = "actl"` as an initial attribute if they do not already set one, so `getattr(o2lite, "services", "")` sees pyarco's claim. In `tests/test_terrarium_boot.py:335-352` (`test_build_can_run_the_agent_on_the_o2lite_transport`) add `fake.set_services("actl")` before `transport.start(fake)`; the assertion `fake.services == "actl,game"` stays.

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3`
Expected: all green; count = baseline + 3.

- [ ] **Step 6: Commit**

```bash
git add devicelink/o2_transport.py tests/test_o2_transport.py tests/test_terrarium_boot.py
git commit -m "feat(o2lite): Control owns the services string and verifies actl after claiming game"
```

---

### Task 4: `harness/websim_leds.py`, and `--identify-blocks` on the o2lite Testshroom

**Files:**
- Create: `harness/websim_leds.py`
- Modify: `harness/o2_shroom.py:353` (import), `main()` (new flag, new early path near line 516)
- Create: `tests/test_websim_leds.py`
- Modify: `tests/test_room_simulator.py` (remove the three tests that move; the file is deleted in Task 5b)

**Interfaces:**
- Consumes: `control.terrarium_config.load_terrarium_config(path).rooms[name].profile` (as `harness/room_simulator.py:150-154` uses it), `harness.o2_shroom.parent_is_gone(pid)`.
- Produces: `harness.websim_leds.WebSimLeds(backend, channels: int)` with `show(frame: bytes)` and `clear()`; `BLOCK_PALETTE`; `identify_blocks_frame(profile, fixture_name: str) -> bytes`. `harness.o2_shroom` accepts `--identify-blocks` (requires `--no-join`, `--room-type`, `--fixture`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_websim_leds.py` by moving these five tests out of `tests/test_room_simulator.py` verbatim, changing only their imports: `test_show_forwards_the_frame_to_the_backend` (line 20), `test_clear_sends_an_all_zero_frame` (29), `test_clear_sends_a_room_width_all_zero_frame` (65; its function-local `from harness.room_simulator import WebSimLeds` at line 66 becomes `from harness.websim_leds import WebSimLeds`), `test_identify_blocks_frame_paints_demo_blocks_distinctly` (72) and `test_identify_blocks_frame_works_for_a_single_block_fixture` (91). Bring along whatever module-level helpers or fixtures those five use (the fake backend class near the top of the file). The new file's imports:

```python
import pytest

from harness.websim_leds import BLOCK_PALETTE, WebSimLeds, identify_blocks_frame
```

Keep whatever `importorskip("luxaeterna.backends.websim")` the moved tests carried. Then add:

```python
def test_o2_shroom_exposes_identify_blocks():
    """The --identify-blocks build-out tool used to live on the websocket
    Room simulator (deleted in the o2lite cutover). It rides the o2lite
    Testshroom now, on the --no-join path, and never touches o2lite."""
    import inspect

    import harness.o2_shroom as o2_shroom

    source = inspect.getsource(o2_shroom)
    assert "--identify-blocks" in source
    assert "identify_blocks_frame(" in source
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_websim_leds.py -q`
Expected: `ModuleNotFoundError: harness.websim_leds`.

- [ ] **Step 3: Create the module**

`harness/websim_leds.py`, moving lines 29-75 of `harness/room_simulator.py` verbatim under this header:

```python
"""WebSimLeds: adapt ShroomClient's leds.show(bytes)/leds.clear() to a
luxaeterna WebSimBackend, plus the block-identification frame the
--identify-blocks build-out tool paints.

Lived in harness/room_simulator.py until the o2lite cutover deleted that
websocket simulator; harness/o2_shroom.py is the one consumer now.
"""

from __future__ import annotations
```

(then `BLOCK_PALETTE`, `identify_blocks_frame`, `WebSimLeds` exactly as they were.)

- [ ] **Step 4: Point `o2_shroom.py` at it and add the flag**

At `harness/o2_shroom.py:353` replace `from harness.room_simulator import WebSimLeds` with `from harness.websim_leds import WebSimLeds`.

In `main()`'s parser (the block at lines 398-470) add:

```python
    parser.add_argument("--identify-blocks", action="store_true",
                        help="Debug: skip Control and o2lite entirely; "
                             "paint each of this fixture's declared blocks "
                             "a distinct solid color and hold until Ctrl-C, "
                             "so the physical build-out mapping can be "
                             "confirmed visually. Needs --no-join, "
                             "--room-type and --fixture.")
```

Right after the canvas URL print (`harness/o2_shroom.py:516-518`, the `url_marker` print) and before `o2lite.initialize` (line 565), insert:

```python
    if args.identify_blocks:
        if not (args.no_join and args.room_type and args.fixture):
            parser.error("--identify-blocks needs --no-join, --room-type "
                         "and --fixture")
        from control.terrarium_config import load_terrarium_config
        from harness.websim_leds import identify_blocks_frame

        profile = load_terrarium_config(
            "terrarium.toml").rooms[args.room_type].profile
        backend.send(identify_blocks_frame(profile, args.fixture))
        print(f"identify-blocks: {args.fixture} painted; Ctrl-C to exit",
              flush=True)
        try:
            while not parent_is_gone(args.exit_with_parent):
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            backend.close()
        return
```

Confirm `backend` and `time` are in scope at that point (both are used nearby in `main()`; if `backend.open()` happens after line 518, place the block after it). Remove the five moved tests from `tests/test_room_simulator.py` so they do not run twice.

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3`
Expected: green; count = previous + 1 (five moved, one added).

- [ ] **Step 6: Commit**

```bash
git add harness/websim_leds.py harness/o2_shroom.py tests/test_websim_leds.py tests/test_room_simulator.py
git commit -m "refactor(harness): move WebSimLeds out of the websocket simulator; identify-blocks on o2_shroom"
```

---

### Task 5a: `terrarium_boot` is o2lite-only

**Files:**
- Modify: `harness/terrarium_boot.py:7-9` (docstring), `:36` (import), `:67-96` (delete `_SimulatorFactory`), `:167-260` (`build()`), `:337-394` (`shutdown` docstring), `:990-1042` (`_recycle_room`, `_restart_room_clients`), `:1086-1248` (parser), `:1297-1317`, `:1404-1412`, `:1422-1455`, `:1551-1558` (`main()`)
- Modify: `harness/run_stack.py:125-128` (`control_command`)
- Modify: `tests/test_terrarium_boot.py` (helper, 14 `build()` call sites, 6 deletions), `tests/test_run_stack.py:301-308`
- Test: `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: `O2LiteTransport`, `FakeO2Lite` (Task 3 semantics: announce `actl` before `start`), `harness.arco_paths.ensure_o2litepy()`.
- Produces: `build(config, bit_registry, *, arco_command, room_binding, transport, clock, room_spec=None, terrarium_config=None, arco_process_cls=ArcoProcess, simulator_popen=subprocess.Popen, room_audio=None, on_join_denied=None, binding_store_path=None, runs_dir=None, run_id=None)`; `_o2lite_module()` returning the o2litepy singleton (monkeypatch seam); `_recycle_room(terrarium, *, transport, pool, o2lite)`; `_restart_room_clients(*, transport, pool, o2lite)`. `--transport` and `--port` are gone from the parser; `--host` stays (Console bind only). `run_stack.control_command` no longer emits `--transport`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_terrarium_boot.py`, replace the `_build_with_fakes` helper (lines 47-58) with:

```python
def _fake_transport():
    """An O2LiteTransport started on a FakeO2Lite that pyarco has already
    announced actl on -- the state build() expects its transport in."""
    from devicelink.o2_transport import FakeO2Lite, O2LiteTransport

    fake = FakeO2Lite()
    fake.set_services("actl")
    transport = O2LiteTransport()
    transport.start(fake)
    return transport


def _build_with_fakes(config, *, transport=None, clock=time.monotonic):
    """Shared fake-injecting build() call for tests that don't need to
    inspect a specific fake's recorded calls afterward. transport defaults
    to a started transport on a FakeO2Lite; clock to time.monotonic, which
    is just "some clock" here, since these tests never compare it with a
    device's."""
    if transport is None:
        transport = _fake_transport()
    return build(
        config, {"TestBit": TestBit},
        arco_command=["arco-server"], room_binding=RoomBindingRegistry(),
        room_spec=TEST_SPEC, arco_process_cls=_fake_arco,
        simulator_popen=FakePopen(), room_audio=_fake_room_audio(),
        transport=transport, clock=clock)
```

Add these tests:

```python
def test_build_requires_a_transport_and_a_clock():
    """No default device wire and no default clock: both were how the
    websocket path crept back in, and the two-clocks bug of 2026-08-13 is
    what a defaulted clock costs."""
    config = BootConfig(room_name="TEST", bit_name="TestBit")
    with pytest.raises(TypeError):
        build(config, {"TestBit": TestBit}, arco_command=["arco-server"],
              room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
              arco_process_cls=_fake_arco, simulator_popen=FakePopen(),
              room_audio=_fake_room_audio(), clock=time.monotonic)
    with pytest.raises(TypeError):
        build(config, {"TestBit": TestBit}, arco_command=["arco-server"],
              room_binding=RoomBindingRegistry(), room_spec=TEST_SPEC,
              arco_process_cls=_fake_arco, simulator_popen=FakePopen(),
              room_audio=_fake_room_audio(), transport=_fake_transport())


def test_parser_has_no_transport_or_port_flag():
    from harness.terrarium_boot import _build_arg_parser

    ap = _build_arg_parser()
    flags = {a for action in ap._actions for a in action.option_strings}
    assert "--transport" not in flags
    assert "--port" not in flags
    assert "--host" in flags          # the Console bind survives


def test_main_resolves_o2lite_before_build(monkeypatch):
    """main() has one device wire. It asks _o2lite_module() for the
    singleton and hands its time_get in as the clock."""
    from devicelink.o2_transport import FakeO2Lite
    import harness.terrarium_boot as terrarium_boot_module

    fake = FakeO2Lite(now=42.0)
    fake.set_services("actl")
    monkeypatch.setattr(terrarium_boot_module, "_o2lite_module", lambda: fake)
    captured = {}

    def fake_build(config, bit_registry, **kwargs):
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(terrarium_boot_module, "build", fake_build)
    monkeypatch.setattr(sys, "argv", ["terrarium_boot.py", "--room", "TEST"])
    with pytest.raises(SystemExit):
        main()
    assert captured["clock"]() == 42.0
    assert type(captured["transport"]).__name__ == "O2LiteTransport"
```

Delete these tests: `test_devicelink_server_starts_before_boot_spawns_the_simulator` (line 92), `test_shutdown_stops_the_devicelink_server_last` (167), `test_build_omitting_clock_keeps_the_existing_default` (390), `test_simulator_factory_spawns_with_its_room_type` (1634; the `_O2SimulatorFactory` twin at 1649 stays), `test_recycle_room_websocket_mode_skips_transport` (2657). In `test_build_wires_devicelink_fixture_sessions_and_simulator` (81) delete only the line `assert server.port != 0`. Delete the import at line 17 (`from devicelink.server import DeviceLinkServer`).

Update the 14 direct `build()` call sites (lines 112, 132, 279, 297, 309, 355, 369, 407, 1462, 1663, 1705, 1800, 2390, and 81): replace `host="127.0.0.1", port=0,` with `transport=_fake_transport(), clock=time.monotonic,` (the exact text may wrap across two lines; check each). Update `_run_main_capturing_build` (line 1438) to monkeypatch `_o2lite_module` the same way the new test does, so the six `main()` plumbing tests keep running offline:

```python
    from devicelink.o2_transport import FakeO2Lite
    fake = FakeO2Lite()
    fake.set_services("actl")
    monkeypatch.setattr(terrarium_boot_module, "_o2lite_module", lambda: fake)
```

In `tests/test_run_stack.py:308` change `assert "--transport" in command and "o2lite" in command` to `assert "--transport" not in command`.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py tests/test_run_stack.py -q 2>&1 | tail -5`
Expected: failures on the three new tests and on every site that now passes `transport=` to a `build()` still accepting `host`/`port` (TypeError on unexpected keyword is fine; the point is the file is consistent before implementation).

- [ ] **Step 3: Implement in `harness/terrarium_boot.py`**

1. Module docstring lines 7-9: replace with "This script constructs the o2lite transport Control rides on (pyarco's connection, synced by arco.initialize() inside build()) and starts it after build() returns; see build()'s docstring for the ordering."
2. Delete line 36 (`from devicelink.server import DeviceLinkServer`).
3. Delete `_SimulatorFactory` (lines 67-96). Keep `_O2SimulatorFactory` and `sim_dev`.
4. `build()` signature becomes the one in **Produces** above (drop `host`, `port`; make `transport` and `clock` keyword-only with no default). Replace lines 233-260 with:

```python
    teardown = TeardownStack()
    # o2lite mode is the only mode: there is no socket to listen on. The
    # connection is pyarco's, clock-synced by arco.initialize() inside
    # room_audio's ArcoSynthPool.start() below, and the caller starts the
    # transport on it AFTER this function returns -- and therefore
    # registers its teardown then, so it stops before everything here.
    server = transport
    factory = _O2SimulatorFactory(config.o2_ensemble,
                                  popen=simulator_popen,
                                  room_type=config.room_name or "")
```

Rewrite the docstring paragraphs at 203-232 to describe only this path: `transport` is the not-yet-started `O2LiteTransport`; `clock` is `o2lite.time_get` in production and is threaded into `GameServer`, `AudioBridge` and `DeviceLinkAgent` so every stamp reads one clock. Update the return-tuple line (177-178) to say slot 2 is the transport.

5. `shutdown()` docstring (342-354, 377-384): delete the websocket-mode sentences; the code does not change.
6. `_recycle_room(terrarium, *, transport, pool, o2lite)`: drop the `if transport is not None:` guard (call `transport.stop()` unconditionally) and delete the docstring sentence at 1006-1008. `_restart_room_clients(*, transport, pool, o2lite)`: drop the `if transport is not None` guard (keep the `pool is not None` one; audio may be absent in tests).
7. Parser: delete `--port` (line 1091) and `--transport` (1160-1164). Change `--host`'s help to "Console bind address (see --console-port). 0.0.0.0 exposes it to the LAN; no auth exists."
8. Add near `_register_o2lite_transport`:

```python
def _o2lite_module():
    """The o2litepy singleton this process rides on -- pyarco's connection.
    A module-level seam so the offline main() tests can hand in a
    FakeO2Lite. Resolved through ensure_o2litepy so a hand-run
    terrarium_boot finds the sibling arco checkout the way run_stack does."""
    from harness.arco_paths import ensure_o2litepy

    if not ensure_o2litepy():
        raise SystemExit("terrarium_boot needs o2litepy and could not find "
                         "it: set MM_ARCO_PATH to the arco checkout or put "
                         "it on PYTHONPATH")
    from o2litepy import o2lite       # noqa: PLC0415 (after ensure_o2litepy)
    return o2lite
```

9. In `main()`, replace lines 1297-1317 with:

```python
    from devicelink.o2_transport import O2LiteTransport

    o2lite = _o2lite_module()
    # pyarco's ArcoSynthPool.start() runs arco.initialize(), which connects
    # o2lite and blocks until clock sync. build() does that while
    # constructing room_audio, so the transport is started after build()
    # returns rather than before it. The clock is the very same singleton's
    # time_get, so Control stamps frames on the clock the device ticks on.
    transport = O2LiteTransport()
    clock = o2lite.time_get
```

At 1404-1412 drop `host=args.host, port=args.port,`. In `stop_clients` (1429-1430) call `transport.stop()` unconditionally. In `restart_clients` (1448) replace the conditional with `o2 = o2lite`. Replace 1551-1558 with the `if` branch's body only (start, register, print `CONTROL_TRANSPORT_READY`), no `else`. Delete the stale comment sentence at 1523-1528 about the devicelink server being last, and reword 1530-1533 to "clock is main()'s own already-resolved o2lite.time_get".

10. `harness/run_stack.py:126-128`: delete the `"--transport", "o2lite",` line.

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3`
Expected: green. Count = previous + 3 new, minus 5 deleted.

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py harness/run_stack.py tests/test_terrarium_boot.py tests/test_run_stack.py
git commit -m "feat(harness): terrarium_boot is o2lite-only; build() requires transport and clock"
```

---

### Task 5b: Delete the websocket wire and the Flutter websocket launch

**Files:**
- Delete: `devicelink/server.py`, `harness/room_simulator.py`, `harness/devicelink_smoke.py`
- Modify: `harness/shroom_client.py` (delete `main()`, `pump_tick`, the `ws://` docstring lines), `harness/run_stack.py` (Flutter launch), `devicelink/__init__.py`
- Delete: `tests/test_devicelink_server.py`, `tests/test_devicelink_smoke.py`, `tests/test_devicelink_smoke_cli.py`, `tests/test_room_simulator.py`
- Modify: `tests/test_shroom_client.py`, `tests/test_run_stack.py:1055-1070`, `tests/test_markers.py:81-87`, `tests/test_signals.py:26`, `tests/test_wire_json_boundaries.py:69-74`
- Test: `tests/test_run_stack.py`, `tests/test_shroom_client.py`

**Interfaces:**
- Consumes: Task 5a (nothing imports `DeviceLinkServer` any more except the files deleted here).
- Produces: `harness.shroom_client` exports `LED_CHANNELS` and `ShroomClient` only. `harness.run_stack.StackConfig` has no `flutter_sim`/`flutter_devices`; `run_stack` has no `--flutter-sim`/`--flutter-devices`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_stack.py`:

```python
def test_run_stack_has_no_flutter_websocket_launch():
    """The Flutter sim was a websocket client of the deleted DeviceLink
    server. It returns in Phase 2 as a page Arco serves over o2ws."""
    import inspect

    import harness.run_stack as run_stack

    source = inspect.getsource(run_stack)
    assert "FLUTTER_LINK" not in source
    assert "flutter_command" not in source
    assert "ws://" not in source
```

Append to `tests/test_shroom_client.py`:

```python
def test_shroom_client_has_no_websocket_entry_point():
    """ShroomClient is the shared wire logic every Testshroom uses; the
    process that runs it is harness/o2_shroom.py. The websocket main()
    and its pump went with the websocket wire."""
    import harness.shroom_client as shroom_client

    assert not hasattr(shroom_client, "main")
    assert not hasattr(shroom_client, "pump_tick")
    assert "websockets" not in shroom_client.__doc__
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_run_stack.py tests/test_shroom_client.py -q -k "no_flutter_websocket or no_websocket_entry"`
Expected: 2 failed.

- [ ] **Step 3: Delete and trim**

```bash
git rm -q devicelink/server.py harness/room_simulator.py harness/devicelink_smoke.py
git rm -q tests/test_devicelink_server.py tests/test_devicelink_smoke.py tests/test_devicelink_smoke_cli.py tests/test_room_simulator.py
```

`harness/shroom_client.py`: delete `main()` and the trailing `if __name__ == "__main__":` block (lines 345-392), delete `pump_tick` (line 317 to the end of that function), delete the docstring's `python3 -m harness.shroom_client --server ws://...` usage lines (around 40-48) and any sentence saying this module is "the part that gets replaced when o2lite lands" (line 20); replace it with "The process that runs this client is harness/o2_shroom.py." Remove now-unused imports (`asyncio`, `json`, anything only `main()` used); confirm with `.venv/bin/python -c "import harness.shroom_client"`.

`harness/run_stack.py`: delete `StackConfig.flutter_sim` and `flutter_devices` (99-100), `FLUTTER_LINK` (191), `flutter_command` (194-202), the spawn block (292-296), the tolerance clause (499-500) and its docstring paragraph (488-493), the two `add_argument` calls (609-614), and the two `config_from_args` fields (706-707).

`devicelink/__init__.py` docstring:

```python
"""DeviceLink: Control's device-facing transport, on o2lite.

The inbound sibling of console/ -- the same split (a transport-only
object plus a transport-agnostic agent driven from the tick loop), but
its clients are Testshrooms and real devices speaking /game/* over the
Arco hub rather than operators. devicelink/o2_transport.py is the
transport; devicelink/protocol.py is the wire vocabulary.
"""
```

Tests: in `tests/test_shroom_client.py` delete every test that imports or calls `pump_tick` (`grep -n pump_tick tests/test_shroom_client.py`); in `tests/test_run_stack.py` delete `test_flutter_sim_is_spawned_after_control_with_serve_args` (1055) and `test_no_flutter_flags_spawns_nothing_extra` (1067); in `tests/test_markers.py` delete `test_room_url_marker_is_emitted_by_room_simulator` (81-87); in `tests/test_signals.py:26` remove `"harness.room_simulator"` from the parametrize list; in `tests/test_wire_json_boundaries.py` delete `test_devicelink_payload_survives_a_non_finite_value` (69-74).

- [ ] **Step 4: Check nothing else references the deleted modules**

```bash
grep -rn "devicelink.server\|DeviceLinkServer\|room_simulator\|devicelink_smoke\|pump_tick\|FLUTTER_LINK" --include=*.py . | grep -v "^./.venv"
```

Expected: hits only in docstrings or comments that describe history (these are cleaned in Task 9), never an import or a call. Fix any import you find.

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3`
Expected: green. Record the count.

- [ ] **Step 6: Commit**

```bash
git add -A devicelink harness tests
git commit -m "refactor: delete the websocket device wire and its drivers; o2lite is the only device transport"
```

---

### Task 6: `DeviceLinkAgent` requires a clock

**Files:**
- Modify: `devicelink/agent.py:85-93`
- Modify: `tests/test_devicelink_agent.py` (52 constructions), `tests/test_terrarium_cycle.py`, `tests/test_timed_cues.py`, `tests/test_terrarium_boot.py` (2 constructions), `harness/terrarium_boot.py` (1, if any lacks `clock=`)
- Test: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `DeviceLinkAgent(game_server, server, *, clock, capability=None, room_audio=None, horizon=0.0, room_profile=None, on_room_frame=None, on_join_denied=None, stale_timeout=15.0)`. Omitting `clock` is a `TypeError`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_devicelink_agent.py`:

```python
def test_agent_requires_a_clock():
    """No default clock. The 2026-08-13 live run went dark because Control
    stamped frames on time.monotonic while the device ticked on O2 time;
    the only structural fix is that nobody can construct an agent without
    saying which clock it reads."""
    gs = GameServer({"TestBit": TestBit})
    with pytest.raises(TypeError):
        DeviceLinkAgent(gs, FakeServer())
```

(Use whatever `GameServer`/`TestBit` construction the file already uses at its top; copy the one from the nearest existing test.)

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -q -k requires_a_clock`
Expected: FAIL (no TypeError; the default `time.monotonic` is used).

- [ ] **Step 3: Implement**

`devicelink/agent.py:85-89`:

```python
    def __init__(self, game_server: GameServer, server, *, clock,
                 capability=None, room_audio=None, horizon: float = 0.0,
                 room_profile=None, on_room_frame=None, on_join_denied=None,
                 stale_timeout: float = 15.0):
```

Update the `_tick_audio` docstring at lines 562-566 to "this agent's clock is o2lite.time_get in production (harness/terrarium_boot.py), and every per-tick concern here reads it". If `time` is now unused in `agent.py`, drop the import.

- [ ] **Step 4: Update every construction**

```bash
grep -rn "DeviceLinkAgent(" --include=*.py . | grep -v "clock=" | grep -v "def __init__" | grep -v "^./.venv"
```

For each hit add `clock=time.monotonic` (tests that never control time) or `clock=clk` where the test already has a `_Clock` instance in scope. Check no caller passes `capability` positionally (`DeviceLinkAgent(gs, server, something,` with three positionals); if one does, make it `capability=something`. Add `import time` to any test file that needs it. Re-run the grep until it prints nothing.

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3`
Expected: green; count = previous + 1.

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests harness/terrarium_boot.py
git commit -m "feat(devicelink): DeviceLinkAgent requires an explicit clock"
```

---

### Task 7: Telemetry chunking under the o2lite cap, and capture over o2lite

**Files:**
- Modify: `devicelink/protocol.py` (after the telemetry section, line 197 onward), `docs/telemetry-trace-schema.md`, `bits/capture/capture_bit.py:27` (comment)
- Delete: `harness/capture_smoke.py`, `tests/test_capture_smoke.py`
- Create: `tests/test_telemetry_chunks.py`, `tests/test_capture_o2.py`

**Interfaces:**
- Consumes: `decode_telemetry_batch(args) -> TelemetryBatch` (`protocol.py:262`), `control.wire_json.dumps`, `capture.trace.Trace.append` semantics (a skipped seq is a gap, a repeat is refused), `FakeO2Lite.deliver(address, typespec, args, timestamp)` where a `b` argument is passed as **bytes of UTF-8 JSON** (that is what `get_blob` wraps and `from_o2_arg` decodes).
- Produces: in `devicelink/protocol.py`: `O2_MAX_MSG_LEN = 4096`, `TELEMETRY_BLOB_BUDGET = 4096 - 128`, `encode_telemetry_body(body: dict) -> bytes` (exactly the blob bytes `to_o2_arg` would send), `chunk_telemetry_batch(body: dict, *, first_seq: int, rate: int = 16000, budget: int = TELEMETRY_BLOB_BUDGET) -> list[dict]` where `body` carries `capture_id`, `t_ms`, the six axes, optional raw `pcm: bytes` and `pcm_t0_ms`, and no `seq`; each returned chunk is a complete wire batch (base64 `pcm`, consecutive `seq` from `first_seq`, `pcm_t0_ms` advanced by frames sent so far) whose encoded body is at most `budget` bytes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_telemetry_chunks.py`:

```python
"""Probe P5 of the o2lite migration spec, as a permanent test: a 100 ms
telemetry batch with 16 kHz audio does not fit one o2lite message, and the
reference chunker splits it so every blob fits under the C library's cap.
Runs in the core offline suite."""
import base64
import struct

import pytest

from devicelink.protocol import (MOTION_AXES, O2_MAX_MSG_LEN,
                                 TELEMETRY_BLOB_BUDGET, chunk_telemetry_batch,
                                 decode_telemetry_batch, encode_telemetry_body)


def _hundred_ms_batch(with_audio: bool = True) -> dict:
    n = 10                                  # 100 Hz motion for 100 ms
    body = {"capture_id": "shake-021",
            "t_ms": [700.0 + 10.0 * i for i in range(n)]}
    for k, axis in enumerate(MOTION_AXES):
        body[axis] = [round(-9.80665 + 0.001 * (i + k), 6) for i in range(n)]
    if with_audio:
        frames = 1600                       # 16 kHz for 100 ms
        body["pcm"] = struct.pack(f"<{frames}h",
                                  *[(i * 37) % 65536 - 32768 for i in range(frames)])
        body["pcm_t0_ms"] = 700.4
    return body


def test_budget_sits_under_the_c_library_cap():
    assert O2_MAX_MSG_LEN == 4096
    assert 0 < TELEMETRY_BLOB_BUDGET < O2_MAX_MSG_LEN


def test_a_100ms_batch_with_audio_does_not_fit_one_message():
    body = _hundred_ms_batch()
    whole = dict(body, seq=0,
                 pcm=base64.b64encode(body["pcm"]).decode("ascii"))
    assert len(encode_telemetry_body(whole)) > TELEMETRY_BLOB_BUDGET


def test_chunks_fit_and_reassemble_without_loss():
    body = _hundred_ms_batch()
    chunks = chunk_telemetry_batch(body, first_seq=7)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(encode_telemetry_body(chunk)) <= TELEMETRY_BLOB_BUDGET

    decoded = [decode_telemetry_batch(["ie1", 0.0, c]) for c in chunks]
    assert [d.seq for d in decoded] == list(range(7, 7 + len(chunks)))
    assert sum((d.t_ms for d in decoded), []) == body["t_ms"]
    for axis in MOTION_AXES:
        assert sum((d.axes[axis] for d in decoded), []) == body[axis]
    assert b"".join(d.pcm for d in decoded) == body["pcm"]
    assert decoded[0].pcm_t0_ms == body["pcm_t0_ms"]
    # each later chunk's pcm_t0_ms is the frames-before offset on the audio clock
    sent = 0
    for d in decoded:
        assert d.pcm_t0_ms == pytest.approx(body["pcm_t0_ms"] + sent * 1000.0 / 16000)
        sent += len(d.pcm) // 2


def test_a_motion_only_batch_is_one_chunk():
    chunks = chunk_telemetry_batch(_hundred_ms_batch(with_audio=False), first_seq=0)
    assert len(chunks) == 1
    assert "pcm" not in chunks[0]
    assert chunks[0]["seq"] == 0


def test_an_unsplittable_batch_is_refused():
    """One motion sample cannot be split further, so a batch whose single
    sample carries more audio than fits has to be refused: the producer
    must send more often, and silently dropping audio is not an option."""
    body = _hundred_ms_batch()
    body["t_ms"] = body["t_ms"][:1]
    for axis in MOTION_AXES:
        body[axis] = body[axis][:1]
    with pytest.raises(ValueError, match="more often"):
        chunk_telemetry_batch(body, first_seq=0)
```

Create `tests/test_capture_o2.py`. It is `tests/test_capture_smoke.py`'s first two tests (lines 36-108) carried onto the o2lite transport: same lifecycle, same `open`/`close` metadata, same on-disk assertions, with `FakeO2Lite.deliver` in place of the websocket `FakeServer` and a chunked 100 ms batch in place of the 3-sample one. A `b` argument reaches `FakeO2Lite.deliver` as the UTF-8 JSON bytes `to_o2_arg` would have put on the wire (`get_blob` wraps them, `from_o2_arg` decodes them), so the blobs below are encoded through `wire_json.dumps`:

```python
"""CaptureBit end to end over the o2lite transport: hello, join, open,
chunked telemetry, close, and a trace on disk with every sample and every
PCM frame, no gaps. Carries tests/test_capture_smoke.py (deleted with the
websocket wire) onto the only device transport."""
import json
import wave

import pytest

pytest.importorskip("luxaeterna")

from bits.capture.capture_bit import CAPTURE_NODE, CaptureBit   # noqa: E402
from capture.store import CaptureStore                            # noqa: E402
from control.engine import GameServer                             # noqa: E402
from control.wire_json import dumps as wire_dumps                 # noqa: E402
from devicelink.agent import DeviceLinkAgent                      # noqa: E402
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport   # noqa: E402
from devicelink.protocol import (chunk_telemetry_batch,           # noqa: E402
                                 encode_telemetry_body)
from tests.test_telemetry_chunks import _hundred_ms_batch         # noqa: E402

DEV = "ie1"

SOURCE = {"client": "mm-tuneshroom-capture", "app_version": "1.0.0+1",
          "platform": "ios 18.5", "device_model": "iPhone 15",
          "motion_stream": "sensors_plus.accelerometer+gyroscope",
          "gravity_included": True, "requested_hz": 100,
          "units": {"accel": "m/s^2", "gyro": "rad/s"},
          "audio_stream": "record.startStream",
          "audio": {"rate": 16000, "bits": 16, "channels": 1}}


def _blob(value) -> bytes:
    return wire_dumps(value).encode("utf-8")


def _stack(tmp_path):
    fake = FakeO2Lite(now=100.0)
    fake.set_services("actl")                 # what arco.initialize() did
    transport = O2LiteTransport()
    transport.start(fake)
    store = CaptureStore(root=tmp_path, session_id="SESSION",
                         bit={"name": "capture", "version": "0.1"},
                         clock=fake.time_get)
    gs = GameServer({"capture": lambda: CaptureBit(store=store)},
                    clock=fake.time_get)
    agent = DeviceLinkAgent(gs, transport, clock=fake.time_get)
    gs.load_bit("capture")
    gs.run()
    fake.deliver("/game/hello", "ssss", (DEV, "capture-client", "1", "testshroom"))
    fake.deliver("/game/join", "ss", (DEV, CAPTURE_NODE))
    agent.poll()
    return fake, store, gs, agent


def _addressed(fake, address: str) -> list:
    return [sent for sent in fake.sent if sent[0] == address]


def test_a_chunked_capture_lands_on_disk_whole(tmp_path):
    fake, store, gs, agent = _stack(tmp_path)
    assert _addressed(fake, f"/{DEV}/deny") == []

    body = _hundred_ms_batch()                # 10 motion samples, 1600 PCM frames
    fake.deliver("/game/capture", "ssb", (DEV, "open", _blob(
        {"capture_id": "tap-001", "label": "tap", "series": 1,
         "window_ms": 1500.0, "t0": 100.0, "source": SOURCE})))
    chunks = chunk_telemetry_batch(body, first_seq=0)
    assert len(chunks) >= 2                   # it really was split
    for chunk in chunks:
        fake.deliver("/game/telemetry", "sfb",
                     (DEV, 100.0, encode_telemetry_body(chunk)))
    fake.deliver("/game/capture", "ssb", (DEV, "close", _blob(
        {"capture_id": "tap-001", "n": 10, "ok": True,
         "outputs": [{"t_ms": -1500.0, "event": "countdown", "level": 0.6}]})))
    agent.poll()

    assert _addressed(fake, f"/{DEV}/error") == []
    trace = json.loads((tmp_path / "SESSION" / "tap" / "001.json").read_text())
    assert trace["label"] == "tap"
    assert trace["capture_id"] == "tap-001"
    assert trace["n"] == 10
    assert trace["samples"]["t_ms"] == body["t_ms"]
    assert trace["samples"]["az"] == body["az"]
    assert trace["outputs"][0]["event"] == "countdown"
    assert trace["audio"]["t0_ms"] == body["pcm_t0_ms"]
    assert trace.get("gaps", []) == []
    with wave.open(str(tmp_path / "SESSION" / "tap" / "001.wav")) as wav:
        assert wav.getframerate() == 16000
        assert wav.readframes(wav.getnframes()) == body["pcm"]


def test_a_refusal_comes_back_as_an_error_over_o2lite(tmp_path):
    fake, store, gs, agent = _stack(tmp_path)
    chunk = chunk_telemetry_batch(_hundred_ms_batch(with_audio=False),
                                  first_seq=0)[0]
    fake.deliver("/game/telemetry", "sfb",
                 (DEV, 100.0, encode_telemetry_body(chunk)))
    agent.poll()

    errors = _addressed(fake, f"/{DEV}/error")
    assert len(errors) == 1
    # fake.sent rows are (address, timestamp, typespec, args)
    assert "no open capture" in errors[0][3][1]
```

Two things to confirm while writing it, both in files that already exist: the trace's serialized key for the gap list (`capture/trace.py`, the method that builds the on-disk dict; if it is not `gaps`, use the real name and drop the `.get` default), and the sample key layout `trace["samples"]["t_ms"]` (the deleted smoke test only checked `samples["az"]`; if `t_ms` lives elsewhere in the trace shape, assert it there).

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_telemetry_chunks.py tests/test_capture_o2.py -q`
Expected: `ImportError` on `chunk_telemetry_batch`.

- [ ] **Step 3: Implement the chunker**

Append to `devicelink/protocol.py` after `CAPTURE_ACTIONS` (line 197):

```python
# o2lite's C library caps a message at 4096 bytes (o2/src/o2lite.h:135).
# o2litepy allows 8192, but hardware and the FFI link use the C library, so
# 4096 is the contract. The blob budget leaves headroom for everything else
# in a /game/telemetry message: the O2 header, the padded address and
# typespec, dev (up to 31 chars) and t0, and the blob's own length word and
# padding -- measured well under 128 bytes.
O2_MAX_MSG_LEN = 4096
TELEMETRY_BLOB_BUDGET = O2_MAX_MSG_LEN - 128


def encode_telemetry_body(body: dict) -> bytes:
    """Exactly the bytes devicelink/o2_transport.py's to_o2_arg puts in the
    blob for a batch dict: wire_json.dumps, UTF-8. The size test and the
    chunker both measure through this so they cannot drift from the wire."""
    from control.wire_json import dumps   # noqa: PLC0415 (protocol stays light)

    return dumps(body).encode("utf-8")


def chunk_telemetry_batch(body: dict, *, first_seq: int, rate: int = 16000,
                          budget: int = TELEMETRY_BLOB_BUDGET) -> list[dict]:
    """Split one producer-side batch into wire batches that each encode
    under `budget` bytes.

    `body` is the batch before framing: capture_id, t_ms, the six axes,
    optional raw int16le `pcm` bytes with `pcm_t0_ms`, and no seq. Motion
    samples and PCM frames are split into the same number of equal runs, so
    chunk i carries the i-th run of each; seq counts up from first_seq and
    each chunk's pcm_t0_ms advances by the frames already sent, on the
    audio clock. Re-batching is free by the trace schema (batch boundaries
    are not semantic), so the decoder needs no change and Trace.append sees
    consecutive seqs. Raises ValueError when even one-sample chunks do not
    fit: the producer must send more often.
    """
    t_ms = list(body["t_ms"])
    n = len(t_ms)
    if n == 0:
        raise ValueError("a batch needs at least one motion sample")
    pcm = bytes(body.get("pcm") or b"")
    frames = len(pcm) // 2
    for parts in range(1, n + 1):
        chunks = []
        for i in range(parts):
            lo, hi = n * i // parts, n * (i + 1) // parts
            flo, fhi = frames * i // parts, frames * (i + 1) // parts
            chunk = {"capture_id": body["capture_id"],
                     "seq": first_seq + i, "t_ms": t_ms[lo:hi]}
            for axis in MOTION_AXES:
                chunk[axis] = list(body[axis][lo:hi])
            if fhi > flo:
                chunk["pcm"] = base64.b64encode(pcm[flo * 2:fhi * 2]).decode("ascii")
                chunk["pcm_t0_ms"] = body["pcm_t0_ms"] + flo * 1000.0 / rate
            chunks.append(chunk)
        if all(len(encode_telemetry_body(c)) <= budget for c in chunks):
            return chunks
    raise ValueError(
        f"a single-sample chunk still exceeds {budget} bytes; the producer "
        f"must send batches more often")
```

`base64` is already imported in `protocol.py` (used by `_decode_pcm`); confirm.

- [ ] **Step 4: Delete the websocket capture driver and document the rule**

```bash
git rm -q harness/capture_smoke.py tests/test_capture_smoke.py
```

In `bits/capture/capture_bit.py:27` change the comment to `# Default trace root when no store is supplied (./captures, gitignored).`

In `docs/telemetry-trace-schema.md`, after the `/game/telemetry` field table (ends about line 94), add:

```markdown
### Chunking (o2lite message cap)

o2lite's C library caps a message at **4096 bytes**, and the base64 PCM of a
100 ms batch at 16 kHz is 4268 bytes on its own. A producer therefore splits
each 100 ms window into several batches so that every batch's encoded JSON
body is at most **3968 bytes** (`devicelink/protocol.py`'s
`TELEMETRY_BLOB_BUDGET`, 128 bytes of headroom for the O2 header, address,
typespec, `dev` and `t0`). Motion samples and PCM frames are split into the
same number of equal runs; `seq` counts up by one per chunk; each chunk's
`pcm_t0_ms` is the window's `pcm_t0_ms` plus the frames already sent divided
by the rate. Because re-batching is free (below), the receiver needs no
knowledge of chunking. `chunk_telemetry_batch` in `devicelink/protocol.py`
is the reference implementation and `tests/test_telemetry_chunks.py` the
conformance test a Dart producer can mirror.
```

Also update the schema's `## Wire` note if it mentions `capture_smoke.py`, and any mention of a websocket, to "over o2lite through Arco".

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3`
Expected: green; count = previous + 7 new (five chunking, two capture), minus 3 deleted.

- [ ] **Step 6: Commit**

```bash
git add devicelink/protocol.py docs/telemetry-trace-schema.md bits/capture/capture_bit.py tests/test_telemetry_chunks.py tests/test_capture_o2.py
git add -A harness/capture_smoke.py tests/test_capture_smoke.py
git commit -m "feat(capture): chunk telemetry under the o2lite cap; capture path exercised over o2lite"
```

---

### Task 8: Arco serves `www/` from a committed `arcoserver/` prefs directory

**Files:**
- Create: `arcoserver/arco_server_prefs.json`, `www/o2ws.js`, `www/o2wsclocksync.htm`, `www/index.htm`, `www/README.md`
- Modify: `control/arco_process.py:101-146` (`pty_popen` gains `cwd`), `harness/terrarium_boot.py:1381-1393` (Arco popen), `harness/markers.py`, `.gitignore`
- Create: `tests/test_arcoserver.py`
- Modify: `tests/test_arco_pty.py`, `tests/test_markers.py` (only if its marker-uniqueness test enumerates markers by hand)

**Interfaces:**
- Consumes: probe P2's result (Task 2). If P2 found the binary has no websocket support, this task still lands (serving files works regardless) and the spec's Phase 2 section records the rebuild requirement.
- Produces: `harness.terrarium_boot.ARCOSERVER_DIR` (absolute path of `<repo>/arcoserver`), `ARCO_HTTP_PORT = 8080`, `_arco_popen(args) -> callable` returning the popen used for Arco (both the plain and the `--arco-pty` variants launch with `cwd=ARCOSERVER_DIR`); `control.arco_process.pty_popen(command, log_path=None, cwd=None)`; `harness.markers.ARCO_WWW = "ARCO_WWW:"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arcoserver.py`:

```python
"""The committed Arco launch directory: Arco reads arco_server_prefs.json
from its cwd (arco/server/src/prefs.cpp:72,139) and turns its HTTP server
on when http_root is non-empty (prefs.cpp:209). These pin the file, the
root it names, and the port terrarium_boot prints."""
import json
import os

from harness.terrarium_boot import ARCO_HTTP_PORT, ARCOSERVER_DIR


def _prefs() -> dict:
    with open(os.path.join(ARCOSERVER_DIR, "arco_server_prefs.json"),
              encoding="utf-8") as handle:
        raw = json.load(handle)
    # Arco's format: {"config name": [{"key": "value"}, ...]}
    return {k: v for entry in raw["default"] for k, v in entry.items()}


def test_prefs_name_the_default_configuration():
    with open(os.path.join(ARCOSERVER_DIR, "arco_server_prefs.json"),
              encoding="utf-8") as handle:
        raw = json.load(handle)
    assert raw["__configuration__"] == [{"__configuration__": "default"}]
    assert "default" in raw


def test_http_root_is_the_repo_www_dir_and_port_matches_the_printed_one():
    prefs = _prefs()
    assert prefs["http_root"] == "../www"
    assert prefs["http_port"] == str(ARCO_HTTP_PORT)
    www = os.path.normpath(os.path.join(ARCOSERVER_DIR, prefs["http_root"]))
    assert os.path.isfile(os.path.join(www, "o2ws.js"))
    assert os.path.isfile(os.path.join(www, "index.htm"))   # Arco's index name
    assert os.path.isfile(os.path.join(www, "o2wsclocksync.htm"))


def test_http_root_fits_arcos_buffer():
    assert len(_prefs()["http_root"]) < 120       # prefs.cpp:64 char[120]


def test_arco_popen_launches_from_the_arcoserver_dir(monkeypatch):
    import argparse

    from harness.terrarium_boot import _arco_popen

    seen = {}

    class _Popen:
        def __init__(self, command, **kwargs):
            seen["command"] = command
            seen["kwargs"] = kwargs

    monkeypatch.setattr("harness.terrarium_boot.subprocess.Popen", _Popen)
    popen = _arco_popen(argparse.Namespace(arco_pty=False, arco_log=None))
    popen(["arco-server"])
    assert seen["kwargs"]["cwd"] == ARCOSERVER_DIR


def test_arco_pty_popen_launches_from_the_arcoserver_dir(monkeypatch):
    import argparse

    import control.arco_process as arco_process
    from harness.terrarium_boot import _arco_popen

    seen = {}

    def fake_pty_popen(command, log_path=None, cwd=None):
        seen["cwd"] = cwd
        seen["log_path"] = log_path
        return object()

    monkeypatch.setattr(arco_process, "pty_popen", fake_pty_popen)
    popen = _arco_popen(argparse.Namespace(arco_pty=True, arco_log="/tmp/a.log"))
    popen(["arco-server"])
    assert seen == {"cwd": ARCOSERVER_DIR, "log_path": "/tmp/a.log"}
```

Append to `tests/test_arco_pty.py`, using that file's own `_wait_for_exit` helper and the `proc.output` buffer `_PtyProcess` drains (`control/arco_process.py:165`):

```python
def test_pty_popen_changes_directory_for_the_child(tmp_path):
    """Arco reads arco_server_prefs.json from its cwd, so the pty spawn has
    to honor cwd the way subprocess.Popen(cwd=...) does. /bin/pwd reports
    the physical directory, hence resolve()."""
    proc = pty_popen(["/bin/pwd"], cwd=str(tmp_path))
    _wait_for_exit(proc)
    proc.wait()
    assert str(tmp_path.resolve()).encode() in bytes(proc.output)
```

Append to `tests/test_markers.py`, mirroring `test_room_url_marker_value` (line 77) and `test_room_url_marker_is_distinct_from_every_other_marker` (line 100) for the new marker; if `test_markers_are_non_empty_and_distinct` (line 35) builds `all_markers` from an explicit list, add `markers.ARCO_WWW` to that list too:

```python
def test_arco_www_marker_value():
    assert markers.ARCO_WWW == "ARCO_WWW:"


def test_arco_www_marker_is_emitted_by_terrarium_boot():
    import inspect

    import harness.terrarium_boot

    assert "markers.ARCO_WWW" in inspect.getsource(harness.terrarium_boot)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arcoserver.py tests/test_arco_pty.py tests/test_markers.py -q 2>&1 | tail -5`
Expected: ImportError on `ARCOSERVER_DIR`, and the pty test failing on the unexpected `cwd` keyword.

- [ ] **Step 3: Create the served files and the prefs**

`arcoserver/arco_server_prefs.json` (Arco's own save format, one property per line, values as strings; the loader is line-based, keep this exact shape):

```json
{
  "__configuration__": [
    {"__configuration__": "default"}
  ],
  "default": [
    {"http_root": "../www"},
    {"http_port": "8080"}
  ]
}
```

`www/o2ws.js`: copy of `/Users/chris/projects/o2/test/www/o2WebMonitor/o2ws.js`. `www/o2wsclocksync.htm`: copy of `/Users/chris/projects/o2/test/www/o2wsclocksync.htm` with its `o2ws_initialize("test")` changed to `o2ws_initialize("arco")`. `www/index.htm`:

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Terrarium www</title></head>
<body>
<p>Served by the Arco server. <a href="o2wsclocksync.htm">o2ws clock sync check</a>.</p>
<p>The instrument web build lands here in Phase 2 (mm-tuneshroom).</p>
</body></html>
```

`www/README.md`:

```markdown
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
```

`.gitignore`: under the existing `o2debug.log` line add `arcoserver/o2debug.log` with the comment `# Arco's cwd is arcoserver/ now (harness/terrarium_boot.py), so its log lands there`.

- [ ] **Step 4: Implement the launch changes**

`control/arco_process.py:101`: `def pty_popen(command: list[str], log_path: str | None = None, cwd: str | None = None):` and in the child branch (line 139-144), before `os.execv`:

```python
        if cwd:
            os.chdir(cwd)
```

Document `cwd` in the docstring: "the child's working directory; Arco reads `arco_server_prefs.json` from it."

`harness/markers.py`: add `ARCO_WWW = "ARCO_WWW:"` beside the other URL markers with a one-line comment "Arco's HTTP root (www/), printed once Arco is ready".

`harness/terrarium_boot.py`: add module constants near the top (after the imports):

```python
# Arco is launched from here so it reads the committed
# arcoserver/arco_server_prefs.json (Arco reads prefs from its cwd) and
# serves ../www on ARCO_HTTP_PORT. tests/test_arcoserver.py pins the file.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCOSERVER_DIR = os.path.join(REPO_ROOT, "arcoserver")
ARCO_HTTP_PORT = 8080
```

Replace lines 1381-1389 (the `arco_popen` selection) with a call to a new helper, and define the helper next to `make_arco_process_cls`:

```python
def _arco_popen(args):
    """The popen Arco is spawned with. Both variants launch from
    ARCOSERVER_DIR; --arco-pty adds the controlling terminal curses needs."""
    if args.arco_pty:
        from control import arco_process

        log_path = args.arco_log

        def popen(command):
            return arco_process.pty_popen(command, log_path=log_path,
                                          cwd=ARCOSERVER_DIR)
        return popen
    if args.arco_log:
        print("--arco-log needs --arco-pty; ignoring", file=sys.stderr)
    return functools.partial(subprocess.Popen, cwd=ARCOSERVER_DIR)
```

(`import functools` at the top.) In `main()`: `arco_popen = _arco_popen(args)` then the existing `arco_process_cls = make_arco_process_cls(arco_popen, settle)`. Note the pty variant imports the module and calls `arco_process.pty_popen` through it, so the test's monkeypatch on the module attribute takes effect.

Right after the `CONTROL_TRANSPORT_READY` print in `main()` (the block edited in Task 5a), add:

```python
        print(f"{markers.ARCO_WWW} http://127.0.0.1:{ARCO_HTTP_PORT}/ "
              f"(www/ over Arco's HTTP server; o2ws on the same port)",
              flush=True)
```

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3`
Expected: green; count = previous + 8 (five arcoserver, one pty, two markers).

- [ ] **Step 6: Live check, RUN ON: MYCOLOGICAL**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/test-harness-testshroom-ac2047
./smoke-test.sh --ci --seconds 20 --devices 1 2>&1 | grep -E "ARCO_WWW|CONTROL_TRANSPORT_READY|passed|failed|FATAL" 
ls arcoserver/
```

Expected: the `ARCO_WWW` line prints, the CI run is green, and `arcoserver/` now holds an `o2debug.log` that `git status` does not list. Then, with `./smoke-test.sh --serve --devices 0` running in the background, open `http://127.0.0.1:8080/o2wsclocksync.htm` and confirm O2 time advances. Stop the stack.

- [ ] **Step 7: Commit**

```bash
git add arcoserver www .gitignore control/arco_process.py harness/terrarium_boot.py harness/markers.py tests/test_arcoserver.py tests/test_arco_pty.py tests/test_markers.py
git commit -m "feat(arco): launch Arco from arcoserver/ with its HTTP server serving www/"
```

---

### Task 9: Documentation sync and stale-comment sweep

**Files:**
- Modify: `docs/MM_TERRARIUM.md`, `README.md:31-43`, `requirements-dev.txt`, `docs/superpowers/specs/2026-09-08-o2lite-connectivity-migration-design.md` (Status), `control/terrarium.py:174`, `control/teardown.py:15,52`, `devicelink/o2_transport.py:4,415`, `control/room_profile.py:53`, `harness/terrarium_boot.py` (`_wait_in_setup` docstring 401-411), `harness/o2_shroom.py:329,335,449`

**Interfaces:**
- Consumes: every prior task's landed state and recorded test count.
- Produces: docs that describe the repo as it is.

- [ ] **Step 1: Sweep the code comments**

```bash
grep -rn "websocket\|DeviceLinkServer\|room_simulator\|devicelink_smoke\|capture_smoke\|devicelink-server" --include=*.py control devicelink harness bits capture | grep -v "console/\|uplink/"
```

For every hit that describes the deleted wire as present, reword it to the o2lite reality or delete it. Known sites: `control/terrarium.py:174` (comment about `DeviceLinkServer` started before `boot()`), `control/teardown.py:15,52` (`"devicelink-server"` example step name; use `"o2lite-transport"`), `devicelink/o2_transport.py:4` ("Satisfies the same small interface DeviceLinkServer does": reword to "the small transport interface DeviceLinkAgent drives: drain_new_clients, drain_inbound, send, bind_dev, drop_dev") and `:415` ("matching DeviceLinkServer": delete the clause), `control/room_profile.py:53` (`--identify-blocks` now on `harness/o2_shroom.py`), `harness/terrarium_boot.py:401-411` (`_wait_in_setup` docstring cross-references `devicelink_smoke.py`), `harness/o2_shroom.py:329,335,449` (says it "stands in for harness/room_simulator.py": it is the Room simulator now). Mentions of the Console's or Uplink's websocket stay.

- [ ] **Step 2: README planned layout**

`README.md:38`: `devicelink/  Control's device-facing transport: the game service on the Arco hub (o2lite)`. `:39`: `arcoserver/  Arco launch dir: arco_server_prefs.json (HTTP root www/, port 8080); Arco's cwd`. `:40`: `www/         what Arco serves: o2ws.js, the clock-sync check page; the simulator web build lands here (Phase 2)`. If the README lists `harness/` drivers by name anywhere, drop the deleted ones.

- [ ] **Step 3: `requirements-dev.txt`**

Replace the paragraph starting `# pyarco and o2litepy are NOT pip-installable.` with the P3 result: pyarco is not installable and stays on `PYTHONPATH`; o2litepy is installable from the `o2` repo (`pip install -e /Users/chris/projects/o2/o2litepy`, verified 2026-09-08, probe P3) and Phase 3 of the migration spec pins it; until then the `PYTHONPATH` reach into the `arco` checkout remains the path.

- [ ] **Step 4: `docs/MM_TERRARIUM.md`**

Make these edits, each a small in-place change (the deep-dive is a history, not a spec; keep dated context, change present-tense claims):

1. Lines 48-49 ("The websocket transport remains and is still the default; o2lite is opt-in per run."): replace with "As of 2026-09-08 (o2lite cutover, Phase 1 of the connectivity migration spec) o2lite is the only device wire: `DeviceLinkServer`, `room_simulator.py`, `devicelink_smoke.py` and `capture_smoke.py` are deleted, `terrarium_boot` has no `--transport`, and Control's transport verifies both `actl` and `game` after claiming the services string."
2. The `### devicelink/` heading at line 522 and its first paragraph: retitle "`devicelink/` -- the device-facing transport (o2lite)"; say the JSON envelope is now the in-process representation only and the wire is O2 messages; the "Arco is not in this path" sentence becomes historical ("was not, on the deleted websocket wire").
3. Line 574-581 (driver `devicelink_smoke --hold` and its trap): replace with "Driver: `./smoke-test.sh --open --devices 1` (run_stack); `--setup-seconds` is the same knob there."
4. Lines 625-628 (`capture_smoke.py`) and 640-641 ("Nothing here is o2lite"): replace with "The capture path rides the o2lite transport like every verb; `tests/test_capture_o2.py` exercises it end to end with a chunked 100 ms batch. Telemetry is chunked under o2lite's 4096-byte cap (`chunk_telemetry_batch`, `docs/telemetry-trace-schema.md`). No producer exists yet (Phase 2/3 of the migration spec); CaptureBit loads with `./smoke-test.sh --serve --bit CaptureBit`."
5. Lines 785-799 (teardown order per mode): keep only the o2lite order.
6. Line 880-887 (`--transport o2lite` opt-in): reword to "the only mode as of 2026-09-08".
7. Lines 4155-4156 ("The websocket device wire is still the default"): strike through and mark **Closed 2026-09-08**.
8. Lines 4162-4165 (the claim that nothing sends the composed role blob and the o2lite transport reading `JoinResult.config` is unbuilt): replace with "the composed `/ie<N>/role` blob ships over o2lite via `devicelink/agent.py` (`role_event`), and has since the 2026-08-12 slice; this line was stale."
9. Add to *Landed subsystems* a short dated section "o2lite cutover (2026-09-08)": what was deleted, the services-string ownership check, the required clock, telemetry chunking, `arcoserver/` + `www/`, the `--identify-blocks` move, and the blob finding from P2 for Phase 2. Note that `o2debug.log` now lands in `arcoserver/`.
10. In *Relationships to other repos*, the o2litepy bullet (line 3813 onward): add the P3 result.

- [ ] **Step 5: Spec Status**

In the migration spec, set Phase 1's status line to "Landed 2026-09-08 (plan `docs/superpowers/plans/2026-09-08-o2lite-cutover-phase-1.md`)" and add a "Deviations from the plan as approved" list under Phase 1: `shroom_client.py` kept (its `ShroomClient` is the shared wire logic `o2_shroom.py` runs; only its websocket entry point went); `capture_smoke.py` deleted rather than ported (CaptureBit loads through run_stack; the o2lite capture path is pinned by a test until a producer exists); `WebSimLeds` moved to `harness/websim_leds.py`; Arco launched from `arcoserver/` (cwd) because Arco reads prefs from its cwd and has no `http_enable` key.

- [ ] **Step 6: Run the suite one last time and commit**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3`
Expected: green. Record the final count in the spec's Phase 1 status line.

```bash
git add -A docs README.md requirements-dev.txt control devicelink harness
git commit -m "docs: o2lite cutover Phase 1 landed; deep-dive, README, spec status, stale comments"
```

---

## Live verification (after Task 9, before merge; RUN ON: MYCOLOGICAL)

The spec's Phase 1 live gate, item by item. Record each result in the spec's Phase 1 status.

1. **Headless stack green with the services string pinned.**
   ```bash
   cd /Users/chris/projects/mm-terrarium/.claude/worktrees/test-harness-testshroom-ac2047
   ./smoke-test.sh --ci --seconds 30 --devices 1 2>&1 | tail -20
   ```
   Expected: green, `CONTROL_TRANSPORT_READY` and `ARCO_WWW` both printed, no `FATAL`.
2. **A Testshroom joins and renders at its declared `when`.**
   ```bash
   ./smoke-test.sh --open --devices 1
   ```
   Expected: the browser canvas shows the drone-driven hue motion; the run's device log reports frames displayed and a lateness summary in the range the deep-dive records (p50 about 4.5 ms, p99 about 12 ms on this box).
3. **Capture round trip with PCM reassembled from chunks.** Covered by `tests/test_capture_o2.py` until a producer exists; state that in the spec rather than claiming a live run.
4. **`o2wsclocksync.htm` reaches clock sync from Arco's `www/`.** With `./smoke-test.sh --serve --devices 0` running, open `http://127.0.0.1:8080/o2wsclocksync.htm`; O2 time advances once per second.
5. **Ensemble filter, live, with a second O2 host on the LAN.** Start a second Arco from a scratch directory holding a prefs file identical to `arcoserver/arco_server_prefs.json` plus `{"ensemble": "other"}` in its `default` list and no `http_root` (so it does not fight for port 8080), then repeat step 1. Expected: every client's log shows it synced against ensemble `arco`, and the `other` host's `o2debug.log` shows no `ie1`, `sim-room-*` or `game` service. Stop both. If two Arco processes cannot share the audio device on this box, run the second host with audio disabled per `arco/doc/server.md`'s device prefs, or on Mycelium over the tailnet, and say which in the spec.

Then closeout: `superpowers:finishing-a-development-branch`, and `mm-deepdive-sync` for the in-repo deep-dive (its edits are already part of Task 9; the sync skill confirms nothing was missed).
