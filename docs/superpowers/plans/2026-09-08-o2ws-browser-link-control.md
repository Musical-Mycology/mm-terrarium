# o2ws Browser Link, Control Side Implementation Plan (Plan A of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a browser be a device of Control over o2ws: Control re-encodes its three blob-carrying messages as strings for devices that ask for that flavor in hello, and the Terrarium serves the guest web page on the LAN with correct MIME types.

**Architecture:** The flavor is a per-device decision inside `O2LiteTransport.send`, keyed by the `protoversion` the agent already receives in hello and now hands to `bind_dev`. A new `harness/www_server.py` serves the repo's `www/` tree over plain HTTP on port 8788, bound to the LAN, started by `terrarium_boot` beside the Console and torn down with it; `run_stack` gains `--www-port` and `--web-build DIR`, and collects the new `WWW_URL` marker like `BROWSE_URL`. Two live probes open the plan and gate section 6 of the spec.

**Tech Stack:** Python 3.14 in `.venv`, pytest, stdlib `http.server`, o2ws.js from the `o2` checkout.

**Spec:** `docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md` (sections 3, 4, 5, 7, 8). The browser half is Plan B in mm-tuneshroom: `docs/superpowers/plans/2026-09-08-o2ws-browser-link.md` there.

## Global Constraints

- Tests ONLY via `.venv/bin/python -m pytest tests -q -p no:cacheprovider` from the worktree root (`.venv` is a symlink to the main checkout's venv). Baseline at branch base `5a6b754`: **2000 passed, 1 skipped**. Every task ends green and records its count.
- The offline suite needs no o2litepy, network, or Arco. `devicelink/` and `control/` never import o2litepy. `control/` stays pure stdlib.
- The wire vocabulary (migration spec section 3) is unchanged. Only the three messages in the flavor table change encoding, only for `o2ws/*` devices. Every non-browser client stays byte-identical.
- Flavor rule, verbatim: a device whose protoversion starts with `o2ws/` gets the string flavor; everything else gets blobs.
- Encodings, verbatim: `role`/`room` `s` = the identical JSON text `to_o2_arg` would have put in the blob; `leds` `s` = base64 of the identical bytes, same timestamp. A string containing byte 0x03 is refused, never sent.
- `WWW_PORT = 8788`, flag `--www-port`, `0` disables. Static server binds `0.0.0.0`. The Console stays as it is.
- Live commands run on **MYCOLOGICAL** from this worktree. Since PR #88, no path overrides are needed from a worktree.
- No em dashes anywhere (code, comments, docs, commit messages); the repo uses `--`.
- Commit after every task with the message shown.

---

## File structure

| File | Responsibility after this plan |
|------|-------------------------------|
| `devicelink/o2_transport.py` | `wire_flavor(protoversion)`, `to_string_arg(value)`, `ETX`; `bind_dev(dev, client, *, protoversion="")` keeps `_protoversions`; `send` rewrites `b` to `s` for string-flavor devices; `drop_dev` and `stop` forget flavors. |
| `devicelink/agent.py` | `_on_hello` passes `protoversion=` to `bind_dev`. |
| `devicelink/protocol.py` | The flavor table in the module docstring, beside the vocabulary. |
| `harness/www_server.py` (new) | `WWW_PORT`, `lan_ip()`, `WwwServer(root, host, port)` with `start`/`stop`/`port`/`url`. |
| `harness/markers.py` | `WWW_URL = "WWW_URL:"`. |
| `harness/terrarium_boot.py` | `--www-port`; starts `WwwServer` beside the Console, pushes `"www-server"` on `teardown`, prints `WWW_URL`. |
| `harness/run_stack.py` | `StackConfig.www_port`, `StackConfig.web_build`; `--www-port`, `--web-build DIR`; `control_command` forwards `--www-port`; `stage_web_build(src, dst)`; `collect_url` records `WWW_URL`. |
| `www/app/` | The Flutter web build, gitignored; `www/README.md` documents it. |
| `.gitignore` | `www/app/`. |
| `tests/test_o2_transport.py`, `tests/test_devicelink_agent.py`, `tests/test_www_server.py` (new), `tests/test_markers.py`, `tests/test_terrarium_boot.py`, `tests/test_run_stack.py` | Coverage per task. |
| `docs/MM_TERRARIUM.md`, the two specs | Recorded. |

---

### Task 1: Probes P7 and P8

**Files:**
- Modify: `docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md` (section 7, record results)

**Interfaces:**
- Consumes: Phase 1's `./smoke-test.sh`, `www/o2ws.js` (the o2WebMonitor copy with the `host` argument), Python's `http.server`.
- Produces: recorded pass/fail for P7 (cross-origin o2ws) and P8 (timed delivery). P8's result decides whether Plan B's link needs a timed queue; the spec's section 6.3 assumes it does not.

This is a probe, not TDD. **RUN ON: MYCOLOGICAL.** Nothing built is committed except the spec note.

- [ ] **Step 1: Stage the probe page beside `o2ws.js`**

The static server for this probe is the same stdlib server Task 4 wraps, rooted at `www/`, so create the page under `www/` temporarily and delete it in Step 5. Find the LAN IP first:

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/o2ws-browser-link
LAN=$(.venv/bin/python -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('10.255.255.255', 1)); print(s.getsockname()[0])"); echo "LAN=$LAN"
```

Create `www/probe78.htm` (replace `LAN_IP` with the printed address):

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>P7/P8 probe</title>
<script src="o2ws.js"></script>
<script>
var DEV = "ie-probe7";
var HOST = "LAN_IP:8080";
var samples = [];
var log = function (s) { document.getElementById("log").textContent += s + "\n"; };
function o2ws_status_msg(s) { log("status: " + s); }
function o2ws_on_error(s) { log("error: " + s); }
function tick_handler(timestamp, address, typespec, info) {
  var seq = o2ws_get_int32();
  var late = (o2ws_time_get() - timestamp) * 1000.0;
  samples.push(late);
  if (samples.length === 100) {
    samples.sort(function (a, b) { return a - b; });
    log("P8 lateness ms over 100 timed messages: p50=" + samples[49].toFixed(2)
        + " p99=" + samples[98].toFixed(2) + " worst=" + samples[99].toFixed(2));
  }
}
function start() {
  o2ws_initialize("arco", HOST);
  o2ws_set_services(DEV);
  o2ws_method_new("/" + DEV + "/tick", "i", true, tick_handler, null);
  var tries = 0, sent = 0;
  var timer = setInterval(function () {
    tries += 1;
    if (!o2ws_clock_synchronized) {
      if (tries > 100) { clearInterval(timer); log("P7 FAIL: no clock sync after 10 s"); }
      return;
    }
    if (sent === 0) log("P7 PASS: clock synced from origin " + document.location.origin + " to " + HOST);
    if (sent < 100) {
      o2ws_send_cmd("/" + DEV + "/tick", o2ws_time_get() + 0.2, "i", sent);
      sent += 1;
    } else { clearInterval(timer); }
  }, 100);
}
</script></head>
<body onload="start()"><pre id="log"></pre></body></html>
```

- [ ] **Step 2: Bring the stack up and serve `www/` on 8788**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/o2ws-browser-link
./smoke-test.sh --serve --devices 1
```

(in the background). In a second shell:

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/o2ws-browser-link
.venv/bin/python -m http.server 8788 --bind 0.0.0.0 --directory www
```

Wait for `ARCO_WWW:` in the stack's output.

- [ ] **Step 3: Load the probe from the LAN address, not loopback**

Open `http://LAN_IP:8788/probe78.htm` in the browser tool (the LAN address matters: the page's origin must differ from the o2ws host's port for P7 to mean anything). Give it 25 seconds, then read the page text. Expected: `P7 PASS` and a `P8 lateness` line with p99 within a few milliseconds. Also record whether the page printed any `error:` line.

- [ ] **Step 4: Record**

Append to the spec's section 7, under the probes: `**P7 result (2026-09-08).**` and `**P8 result (2026-09-08).**` with the exact printed lines. If P8's p99 is above 10 ms, add one sentence to section 6.3 saying the browser link will hold frames in its own queue until `when` and that Plan B's Task 5 carries that.

- [ ] **Step 5: Tear down and clean**

Stop the stack and the static server. `rm www/probe78.htm`. `git status --short` must show only the spec.

```bash
git add docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md
git commit -m "docs(spec): record probes P7 (cross-origin o2ws) and P8 (timed delivery)"
```

---

### Task 2: Wire flavor in the transport

**Files:**
- Modify: `devicelink/o2_transport.py` (constants near `MAX_DEV_LEN`; `O2LiteTransport.__init__`, `bind_dev`, `drop_dev`, `send`, `stop`)
- Modify: `devicelink/protocol.py` (module docstring)
- Test: `tests/test_o2_transport.py`

**Interfaces:**
- Consumes: `to_o2_arg(type_char, value)` and `_json_dumps` as they are; `FakeO2Lite.sent` rows `(addr, timestamp, typespec, rest_args)`.
- Produces: `STRING_FLAVOR_PREFIX = "o2ws/"`, `ETX = "\x03"`, `wire_flavor(protoversion: str) -> str` returning `"string"` or `"blob"`, `to_string_arg(value) -> str` (JSON text for anything that is not bytes or an int list; base64 for bytes or an int list), `O2LiteTransport.bind_dev(dev, client, *, protoversion="")`, and `send` behavior per the flavor table. Task 3 passes the keyword; Plan B decodes exactly these strings.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_o2_transport.py`:

```python
def _started_with(dev, protoversion):
    transport, fake = _started()
    transport.bind_dev(dev, object(), protoversion=protoversion)
    return transport, fake


def test_wire_flavor_is_string_only_for_o2ws_protoversions():
    from devicelink.o2_transport import wire_flavor

    assert wire_flavor("o2ws/1") == "string"
    assert wire_flavor("o2ws/2-beta") == "string"
    assert wire_flavor("1") == "blob"
    assert wire_flavor("") == "blob"
    assert wire_flavor("O2WS/1") == "blob"       # exact prefix, no case folding


def test_a_role_config_goes_to_a_browser_as_the_same_json_text():
    """Spec section 3.2: `s`, the identical JSON text the blob would hold."""
    import json

    transport, fake = _started_with("ie-abc123", "o2ws/1")
    config = {"bit_name": "TestBit", "role": "player",
              "light_manifest": {"instruments": []}}
    transport.send("ie-abc123", {"address": "/ie-abc123/role", "typespec": "b",
                                 "args": [config], "timestamp": 0.0})
    addr, _ts, typespec, args = fake.sent[0]
    assert addr == "/ie-abc123/role"
    assert typespec == "s"
    assert isinstance(args[0], str)
    assert json.loads(args[0]) == config


def test_an_led_frame_goes_to_a_browser_as_base64_with_its_timestamp():
    import base64

    transport, fake = _started_with("ie-abc123", "o2ws/1")
    frame = [255, 0, 128] * 12
    transport.send("ie-abc123", {"address": "/ie-abc123/leds", "typespec": "b",
                                 "args": [frame], "timestamp": 41.5})
    _addr, ts, typespec, args = fake.sent[0]
    assert ts == 41.5
    assert typespec == "s"
    assert base64.b64decode(args[0]) == bytes(frame)


def test_a_room_blob_goes_to_a_browser_as_json_text():
    import json

    transport, fake = _started_with("ie-abc123", "o2ws/1")
    blob = {"state": "SETUP", "roles": []}
    transport.send("ie-abc123", {"address": "/ie-abc123/room", "typespec": "b",
                                 "args": [blob], "timestamp": 0.0})
    _addr, _ts, typespec, args = fake.sent[0]
    assert typespec == "s"
    assert json.loads(args[0]) == blob


def test_non_blob_messages_are_identical_for_a_browser():
    transport, fake = _started_with("ie-abc123", "o2ws/1")
    transport.send("ie-abc123", {"address": "/ie-abc123/deny", "typespec": "ss",
                                 "args": ["role full", "try the jam node"],
                                 "timestamp": 0.0})
    _addr, _ts, typespec, args = fake.sent[0]
    assert typespec == "ss"
    assert args == ("role full", "try the jam node")


def test_a_blob_flavor_device_is_unchanged_by_the_flavor_machinery():
    """Hardware and Testshrooms send protoversion "1" or "" and must see
    exactly what they saw before this task: a Blob with size and data."""
    transport, fake = _started_with("ie1", "1")
    transport.send("ie1", {"address": "/ie1/leds", "typespec": "b",
                           "args": [[1, 2, 3] * 12], "timestamp": 0.0})
    _addr, _ts, typespec, args = fake.sent[0]
    assert typespec == "b"
    assert args[0].size == 36


def test_bind_dev_without_a_protoversion_means_blob():
    transport, fake = _started()
    transport.bind_dev("ie1", object())
    transport.send("ie1", {"address": "/ie1/role", "typespec": "b",
                           "args": [{"role": "player"}], "timestamp": 0.0})
    assert fake.sent[0][2] == "b"


def test_a_string_containing_etx_is_refused_not_sent(caplog):
    """o2ws fields end at byte 0x03; a value carrying one would corrupt the
    frame. Refuse and log; never raise into the engine tick."""
    import logging

    transport, fake = _started_with("ie-abc123", "o2ws/1")
    with caplog.at_level(logging.ERROR, logger="devicelink.o2_transport"):
        transport.send("ie-abc123", {"address": "/ie-abc123/role", "typespec": "b",
                                     "args": [{"role": "pl\x03ayer"}],
                                     "timestamp": 0.0})
    assert fake.sent == []
    assert any("0x03" in rec.getMessage() for rec in caplog.records)


def test_drop_dev_forgets_the_flavor():
    transport, fake = _started_with("ie-abc123", "o2ws/1")
    transport.drop_dev("ie-abc123")
    transport.bind_dev("ie-abc123", object())        # rebound without a flavor
    transport.send("ie-abc123", {"address": "/ie-abc123/role", "typespec": "b",
                                 "args": [{"role": "player"}], "timestamp": 0.0})
    assert fake.sent[0][2] == "b"


def test_to_string_arg_matches_to_o2_arg_s_two_way_rule():
    """The JSON-versus-base64 choice must not drift between flavors: bytes
    and int lists are base64, everything else is the JSON to_o2_arg makes."""
    import base64
    import json

    from devicelink.o2_transport import to_o2_arg, to_string_arg

    for value in (b"\x00\xff", [0, 255, 7], {"a": 1}, ["x", "y"], "plain"):
        blob = to_o2_arg("b", value)
        as_string = to_string_arg(value)
        if isinstance(value, (bytes, list)) and not any(isinstance(v, str) for v in value):
            assert base64.b64decode(as_string) == bytes(blob.data)
        else:
            assert json.loads(as_string) == json.loads(bytes(blob.data).decode("utf-8"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_o2_transport.py -q -p no:cacheprovider -k "flavor or browser or etx or forgets or to_string"`
Expected: failures (ImportError on `wire_flavor`, TypeError on the `protoversion` keyword).

- [ ] **Step 3: Implement**

In `devicelink/o2_transport.py`, after `MAX_DEV_LEN`:

```python
# Wire flavor (spec 2026-09-08-o2ws-browser-link-design.md, section 3).
# o2ws carries no blob type: the host writes a literal "?" for any argument
# type it does not know, and o2ws.js has no o2ws_get_blob. A browser asks
# for the string flavor by announcing a protoversion that starts with
# STRING_FLAVOR_PREFIX in /game/hello; Control then sends its three
# blob-carrying messages as `s`. Every other device, and every other
# message, is byte-identical to the blob flavor.
STRING_FLAVOR_PREFIX = "o2ws/"

# An o2ws text field ends at this byte; a value carrying it would corrupt
# the frame, so send() refuses such a value rather than trusting encoders.
ETX = "\x03"


def wire_flavor(protoversion) -> str:
    """"string" for a browser over o2ws, "blob" for everything else."""
    return ("string" if str(protoversion).startswith(STRING_FLAVOR_PREFIX)
            else "blob")


def to_string_arg(value) -> str:
    """The `s` form of what to_o2_arg would have put in a `b` blob: base64
    for raw bytes (bytes, or a list of ints, the LED frame), the identical
    JSON text for anything else (role and room). Mirrors to_o2_arg's
    two-way rule exactly so the choice cannot drift between flavors."""
    import base64

    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, list) and all(isinstance(v, int) for v in value):
        return base64.b64encode(bytes(v & 0xFF for v in value)).decode("ascii")
    return _json_dumps(value)
```

In `O2LiteTransport.__init__` add `self._protoversions: dict[str, str] = {}` and `self._flavors_seen: set[str] = set()`. Replace `bind_dev`, `drop_dev`, `send`, `stop`:

```python
    def bind_dev(self, dev: str, client, *, protoversion: str = "") -> None:
        if not dev or len(dev) > MAX_DEV_LEN:
            raise ValueError(
                f"dev id {dev!r} is not a valid O2 service name "
                f"(1..{MAX_DEV_LEN} characters)")
        self._devs[dev] = client
        self._protoversions[dev] = str(protoversion or "")
        flavor = wire_flavor(protoversion)
        if flavor == "string" and protoversion not in self._flavors_seen:
            self._flavors_seen.add(protoversion)
            logger.info("string wire flavor for %s (protoversion %r)",
                        dev, protoversion)

    def drop_dev(self, dev: str) -> None:
        self._devs.pop(dev, None)
        self._protoversions.pop(dev, None)

    def send(self, dev: str, msg: dict) -> None:
        """Send one outbound envelope to `dev`'s own service.

        Unknown dev is a silent no-op: a cue for a device that has gone
        away must never raise into the engine tick. A string-flavor device
        (wire_flavor of the protoversion it announced) gets every `b`
        argument rewritten to `s` per to_string_arg; the typespec is
        rewritten to match.
        """
        if dev not in self._devs or self._o2 is None:
            return
        typespec = msg.get("typespec", "")
        raw_args = msg.get("args", [])
        flavor = wire_flavor(self._protoversions.get(dev, ""))
        if flavor == "string" and "b" in typespec:
            args = [to_string_arg(v) if t == "b" else v
                    for t, v in zip(typespec, raw_args)]
            typespec = typespec.replace("b", "s")
        else:
            args = [to_o2_arg(t, v) for t, v in zip(typespec, raw_args)]
        for t, v in zip(typespec, args):
            if t in "sS" and isinstance(v, str) and ETX in v:
                logger.error("refusing %s to %s: a string argument contains "
                             "byte 0x03, the o2ws field separator",
                             msg.get("address"), dev)
                return
        try:
            self._o2.send(msg["address"], msg.get("timestamp", 0.0),
                          typespec, *args)
        except Exception:
            logger.exception("o2lite send to %s failed", dev)

    def stop(self) -> None:
        self._o2 = None
        self._devs.clear()
        self._protoversions.clear()
        self._inbound.clear()
```

Check `import base64` placement against the file's import style; a module-level import is fine (stdlib).

- [ ] **Step 4: Document the table in `devicelink/protocol.py`**

Add to the module docstring, after the vocabulary summary:

```
Wire flavor (2026-09-08, spec 2026-09-08-o2ws-browser-link-design.md):
a device whose /game/hello protoversion starts with "o2ws/" receives
/<dev>/role and /<dev>/room as `s` (the identical JSON text) and
/<dev>/leds as `s` (base64 of the identical bytes, same timestamp),
because o2ws carries no blob type. Every other device and every other
message is unchanged. The rewrite lives in devicelink/o2_transport.py's
O2LiteTransport.send; this module's builders still produce `b`.
```

- [ ] **Step 5: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -1`
Expected: green; count = 2000 + 10.

- [ ] **Step 6: Commit**

```bash
git add devicelink/o2_transport.py devicelink/protocol.py tests/test_o2_transport.py
git commit -m "feat(o2lite): per-device string wire flavor for o2ws browsers"
```

---

### Task 3: The agent hands hello's protoversion to the transport

**Files:**
- Modify: `devicelink/agent.py` (`_on_hello`, the `bind_dev` call)
- Modify: `tests/test_devicelink_agent.py` (`FakeServer.bind_dev`)
- Test: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: Task 2's `bind_dev(dev, client, *, protoversion="")`.
- Produces: `FakeServer.bind_dev(self, dev, client, protoversion="")` recording `self.protoversions[dev]`. `_on_join`'s `bind_dev` call is unchanged (no protoversion there; a join never precedes a hello from a working client, and the keyword defaults).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_devicelink_agent.py`:

```python
def test_hello_protoversion_reaches_the_transport_binding(rig):
    """The transport picks the wire flavor from what hello announced
    (devicelink/o2_transport.py wire_flavor); the agent is the only thing
    that sees the hello, so it must pass the token along at bind time."""
    gs, server, agent = rig
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "ssss",
                   ["ie-abc123", "flutter-sim", "o2ws/1", "tuneshroom"])
    agent.poll()
    assert server.protoversions["ie-abc123"] == "o2ws/1"


def test_hello_without_a_protoversion_binds_with_an_empty_one(rig):
    gs, server, agent = rig
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "s", ["ie1"])
    agent.poll()
    assert server.protoversions["ie1"] == ""
```

In `FakeServer.__init__` add `self.protoversions = {}` and change `bind_dev`:

```python
    def bind_dev(self, dev, client, protoversion=""):
        self._devs[dev] = client
        self.protoversions[dev] = protoversion
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -q -p no:cacheprovider -k protoversion`
Expected: 2 failed (KeyError: the agent never passes the keyword).

- [ ] **Step 3: Implement**

`devicelink/agent.py`, `_on_hello`: change `self.server.bind_dev(dev, client)` to `self.server.bind_dev(dev, client, protoversion=protoversion)`. Leave `_on_join`'s call as it is.

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -1`
Expected: green; count = previous + 2. (`tests/test_timed_cues.py` and the other direct `bind_dev(dev, client)` callers keep working because the keyword defaults.)

- [ ] **Step 5: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "feat(devicelink): hello's protoversion reaches the transport binding"
```

---

### Task 4: `harness/www_server.py`

**Files:**
- Create: `harness/www_server.py`
- Create: `tests/test_www_server.py`

**Interfaces:**
- Consumes: stdlib only.
- Produces: `WWW_PORT = 8788`; `lan_ip() -> str` (first non-loopback IPv4, `"127.0.0.1"` fallback); `class WwwServer(root: str, host: str = "0.0.0.0", port: int = WWW_PORT)` with `start() -> None`, `stop() -> None`, `port` (the bound port after `start`), `url(host: str | None = None) -> str` giving `http://<host or lan_ip()>:<port>/`. Task 5 starts it from `terrarium_boot`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_www_server.py`:

```python
"""The LAN static server that serves www/ to guest phones (spec section
4.1). Ephemeral port in tests; real sockets on loopback only."""
import os
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from harness.www_server import WWW_PORT, WwwServer, lan_ip


@pytest.fixture
def www(tmp_path):
    (tmp_path / "index.htm").write_text("<p>hello</p>", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.dart.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "app" / "canvaskit.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    (tmp_path / "app" / "index.html").write_text("<p>app</p>", encoding="utf-8")
    server = WwwServer(str(tmp_path), host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_default_port_is_the_documented_one():
    assert WWW_PORT == 8788


def test_serves_the_index_and_the_app_files(www):
    base = f"http://127.0.0.1:{www.port}"
    assert urlopen(f"{base}/index.htm").read() == b"<p>hello</p>"
    assert urlopen(f"{base}/app/index.html").read() == b"<p>app</p>"


def test_content_types_the_flutter_build_needs(www):
    base = f"http://127.0.0.1:{www.port}"
    js = urlopen(f"{base}/app/main.dart.js")
    assert js.headers["Content-Type"].startswith("text/javascript")
    wasm = urlopen(f"{base}/app/canvaskit.wasm")
    assert wasm.headers["Content-Type"] == "application/wasm"


def test_refuses_to_leave_the_root(www, tmp_path):
    (tmp_path.parent / "outside.txt").write_text("secret", encoding="utf-8")
    base = f"http://127.0.0.1:{www.port}"
    with pytest.raises(HTTPError) as err:
        urlopen(f"{base}/../outside.txt")
    assert err.value.code == 404


def test_url_uses_the_bound_port_and_a_given_host(www):
    assert www.url("10.0.0.7") == f"http://10.0.0.7:{www.port}/"
    assert www.url().startswith("http://")


def test_lan_ip_is_an_ipv4_literal():
    ip = lan_ip()
    parts = ip.split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts)


def test_stop_is_idempotent(tmp_path):
    server = WwwServer(str(tmp_path), host="127.0.0.1", port=0)
    server.start()
    server.stop()
    server.stop()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_www_server.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: harness.www_server`.

- [ ] **Step 3: Implement**

Create `harness/www_server.py`:

```python
"""WwwServer: the LAN static server that serves www/ to guest phones.

Spec: docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md,
section 4.1. Arco also serves www/ (on ARCO_HTTP_PORT) but labels every
file text/html, which a Flutter web build's .wasm and module scripts
cannot load under; this server exists so the page comes with the right
Content-Type while its websocket still goes to Arco. Static files only,
no state, so it binds the LAN by default; the Console's trust model
(loopback, no auth) does not apply here and guests are never pointed at
the Console.
"""

from __future__ import annotations

import functools
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# The documented port a QR poster points at (spec section 4.1).
WWW_PORT = 8788


def lan_ip() -> str:
    """The first non-loopback IPv4 address of this host, or 127.0.0.1.

    A UDP socket "connected" to a routable address never sends a packet;
    the kernel just picks the interface and source address it would use.
    That is the address a phone on the venue LAN can reach.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        ip = probe.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        probe.close()
    return ip


class _QuietHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler already confines paths to `directory`
    (translate_path drops '..' components) and maps .wasm and .js through
    the mimetypes table. Only its per-request log line is silenced: the
    stack's stdout carries markers, not access logs."""

    def log_message(self, format, *args):  # noqa: A002 (stdlib signature)
        return


class WwwServer:
    def __init__(self, root: str, host: str = "0.0.0.0",
                 port: int = WWW_PORT) -> None:
        self._root = root
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        handler = functools.partial(_QuietHandler, directory=self._root)
        self._server = ThreadingHTTPServer((self._host, self._port), handler)
        self._server.daemon_threads = True
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="www-server")
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def port(self) -> int:
        return self._port

    def url(self, host: str | None = None) -> str:
        return f"http://{host or lan_ip()}:{self._port}/"
```

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -1`
Expected: green; count = previous + 7.

- [ ] **Step 5: Commit**

```bash
git add harness/www_server.py tests/test_www_server.py
git commit -m "feat(harness): WwwServer serves www/ on the LAN with correct MIME types"
```

---

### Task 5: Wire the server into `terrarium_boot` and `run_stack`; `WWW_URL`; `--web-build`

**Files:**
- Modify: `harness/markers.py` (after `ARCO_WWW`), `harness/terrarium_boot.py` (parser; the `try:` block that starts the Console; the `ARCO_WWW` print), `harness/run_stack.py` (`StackConfig`, `control_command`, `collect_url`, argparse, `config_from_args`, a new `stage_web_build`), `.gitignore`, `www/README.md`
- Test: `tests/test_markers.py`, `tests/test_terrarium_boot.py`, `tests/test_run_stack.py`

**Interfaces:**
- Consumes: Task 4's `WwwServer`, `WWW_PORT`, `lan_ip`.
- Produces: `markers.WWW_URL = "WWW_URL:"`; `terrarium_boot --www-port N` (default `WWW_PORT`, `0` disables) starting `WwwServer(REPO_ROOT/www, host="0.0.0.0", port=N)` inside the same `try:` as the Console, pushed as `"www-server"` on `teardown`, printing `WWW_URL: http://<lan-ip>:<port>/ (guest page; o2ws goes to Arco on ARCO_HTTP_PORT)`; `run_stack`: `StackConfig.www_port: int = WWW_PORT`, `StackConfig.web_build: str | None = None`, `--www-port`, `--web-build DIR`, `control_command` forwards `--www-port`, `stage_web_build(src_dir, www_dir) -> str` replacing `www/app/` with a copy of `src_dir`, `collect_url` records a `WWW_URL` line like `BROWSE_URL` (opened under `--open`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_markers.py`:

```python
def test_www_url_marker_value():
    assert markers.WWW_URL == "WWW_URL:"


def test_www_url_marker_is_emitted_by_terrarium_boot():
    import inspect

    import harness.terrarium_boot

    assert "markers.WWW_URL" in inspect.getsource(harness.terrarium_boot)


def test_www_url_marker_is_distinct_from_every_other_marker():
    others = list(markers.READY_MARKERS.values()) + \
        list(markers.FAILURE_MARKERS.values()) + \
        [markers.BROWSE_URL, markers.ROOM_URL, markers.ARCO_WWW]
    for other in others:
        assert not markers.WWW_URL.startswith(other)
        assert not other.startswith(markers.WWW_URL)
```

Append to `tests/test_terrarium_boot.py`:

```python
def test_parser_www_port_defaults_to_the_documented_port():
    from harness.terrarium_boot import _build_arg_parser
    from harness.www_server import WWW_PORT

    args = _build_arg_parser().parse_args(["--room", "TEST"])
    assert args.www_port == WWW_PORT
    assert _build_arg_parser().parse_args(["--room", "TEST", "--www-port", "0"]).www_port == 0


def test_start_www_server_starts_pushes_teardown_and_prints_the_url(capsys):
    """The static server has no dependency on Arco or the transport, so it
    is wired through one helper that can be tested without main(): it
    constructs the server on www/, starts it, registers its stop on the
    teardown stack, and prints the WWW_URL line run_stack collects."""
    import argparse

    from control.teardown import TeardownStack
    import harness.terrarium_boot as tb

    events = []

    class FakeWww:
        def __init__(self, root, host="0.0.0.0", port=0):
            events.append(("new", root, host, port))

        def start(self):
            events.append(("start",))

        def stop(self):
            events.append(("stop",))

        @property
        def port(self):
            return 8788

        def url(self, host=None):
            return f"http://{host}:8788/"

    teardown = TeardownStack()
    args = argparse.Namespace(www_port=8788)
    server = tb._start_www_server(args, teardown, server_cls=FakeWww,
                                  ip=lambda: "10.0.0.5")
    assert server is not None
    assert events[0] == ("new", os.path.join(tb.REPO_ROOT, "www"), "0.0.0.0", 8788)
    assert events[1] == ("start",)
    out = capsys.readouterr().out
    assert f"{tb.markers.WWW_URL} http://10.0.0.5:8788/" in out
    teardown.close()
    assert events[-1] == ("stop",)


def test_start_www_server_is_off_when_the_port_is_zero(capsys):
    import argparse

    from control.teardown import TeardownStack
    import harness.terrarium_boot as tb

    class MustNotConstruct:
        def __init__(self, *args, **kwargs):
            raise AssertionError("no server when --www-port is 0")

    server = tb._start_www_server(argparse.Namespace(www_port=0), TeardownStack(),
                                  server_cls=MustNotConstruct,
                                  ip=lambda: "10.0.0.5")
    assert server is None
    assert tb.markers.WWW_URL not in capsys.readouterr().out
```

(`tests/test_terrarium_boot.py` already imports `os`; if not, add it.)

Append to `tests/test_run_stack.py`:

```python
def test_control_command_forwards_www_port():
    from harness.run_stack import StackConfig, control_command
    cfg = StackConfig(log_dir="/tmp/x", www_port=9000)
    cmd = control_command(cfg, ppid=1)
    assert cmd[cmd.index("--www-port") + 1] == "9000"


def test_www_port_defaults_to_the_documented_port():
    from harness.run_stack import StackConfig
    from harness.www_server import WWW_PORT
    assert StackConfig(log_dir="/tmp/x").www_port == WWW_PORT


def test_www_url_lines_are_collected_and_opened(tmp_path):
    script = (_CONTROL_OK_WITH_URLS.replace(
        f"{markers.CONTROL_SETUP_HOLD} for 20s\n",
        f"{markers.WWW_URL} http://10.0.0.5:8788/ (guest page)\n"
        f"{markers.CONTROL_SETUP_HOLD} for 20s\n"))
    popen = ScriptedPopen([script, _DEVICE_OK_WITH_URL])
    opened = []
    result = run(_cfg(tmp_path, open_urls=True), popen=popen,
                 sleep=lambda _s: None, opener=opened.append)
    assert result.ok is True
    assert "http://10.0.0.5:8788/" in result.urls
    assert "http://10.0.0.5:8788/" in opened


def test_stage_web_build_replaces_www_app(tmp_path):
    from harness.run_stack import stage_web_build
    src = tmp_path / "build" / "web"
    src.mkdir(parents=True)
    (src / "index.html").write_text("new", encoding="utf-8")
    www = tmp_path / "www"
    (www / "app").mkdir(parents=True)
    (www / "app" / "stale.js").write_text("old", encoding="utf-8")
    dst = stage_web_build(str(src), str(www))
    assert dst == str(www / "app")
    assert (www / "app" / "index.html").read_text(encoding="utf-8") == "new"
    assert not (www / "app" / "stale.js").exists()


def test_stage_web_build_refuses_a_dir_without_an_index(tmp_path):
    from harness.run_stack import stage_web_build
    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(SystemExit):
        stage_web_build(str(src), str(tmp_path / "www"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_markers.py tests/test_terrarium_boot.py tests/test_run_stack.py -q -p no:cacheprovider -k "www or web_build"`
Expected: failures on every new test.

- [ ] **Step 3: Implement**

`harness/markers.py`, after `ARCO_WWW`:

```python
# The Terrarium's own static server for the guest page (www/ on the LAN
# with correct MIME types; spec 2026-09-08-o2ws-browser-link-design.md
# section 4.1). Collected and opened by run_stack like BROWSE_URL.
WWW_URL = "WWW_URL:"
```

`harness/terrarium_boot.py`:

1. Import: `from harness.www_server import WWW_PORT, WwwServer, lan_ip`.
2. Parser, next to `--console-port`:

```python
    ap.add_argument("--www-port", type=int, default=WWW_PORT,
                    help=f"Serve www/ (the guest page) on this port, bound to "
                         f"the LAN. Default {WWW_PORT}; 0 disables. Phones "
                         f"scan a QR pointing here; their o2ws websocket goes "
                         f"to Arco on {ARCO_HTTP_PORT}.")
```

3. A helper next to `_arco_popen`:

```python
def _start_www_server(args, teardown, *, server_cls=WwwServer, ip=lan_ip):
    """Serve www/ to guest phones (spec section 4.1). Independent of Arco
    and the transport; constructed through `server_cls` so the wiring is
    testable without a socket. Returns the server, or None when
    --www-port 0 turned it off."""
    if args.www_port == 0:
        return None
    server = server_cls(os.path.join(REPO_ROOT, "www"), host="0.0.0.0",
                        port=args.www_port)
    server.start()
    teardown.push("www-server", server.stop)
    print(f"{markers.WWW_URL} {server.url(ip())} "
          f"(guest page; o2ws goes to Arco on {ARCO_HTTP_PORT})", flush=True)
    return server
```

4. In `main()`, inside the `try:` that builds the Console, right after the Console block (so a bind failure unwinds through the same `shutdown(teardown, terrarium)` path): `www_server = _start_www_server(args, teardown)`.

`harness/run_stack.py`:

1. `StackConfig`: add `www_port: int = WWW_PORT` and `web_build: str | None = None` (import `WWW_PORT` from `harness.www_server`).
2. `control_command`: after the `--console-port` block add `command += ["--www-port", str(cfg.www_port)]`.
3. `collect_url`: treat `markers.WWW_URL` exactly like `BROWSE_URL`:

```python
        is_browse = markers.BROWSE_URL in line or markers.WWW_URL in line
```

4. New function:

```python
def stage_web_build(src_dir: str, www_dir: str) -> str:
    """Replace www/app/ with a copy of a Flutter web build (mm-tuneshroom's
    `tool/sim build` writes build/web/). Refuses a directory with no
    index.html so a typo cannot serve an empty app."""
    import shutil

    if not os.path.isfile(os.path.join(src_dir, "index.html")):
        print(f"--web-build {src_dir!r} has no index.html; run "
              f"`tool/sim build` in mm-tuneshroom and point at build/web",
              file=sys.stderr)
        raise SystemExit(2)
    dst = os.path.join(www_dir, "app")
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src_dir, dst)
    return dst
```

5. argparse: `--www-port` (type int, default `WWW_PORT`, help as terrarium_boot's) and `--web-build` (metavar `DIR`, default None, help "Copy a Flutter web build into www/app/ before starting; the Terrarium serves it to phones."). `config_from_args` passes both into `StackConfig`. In `main()` before `run()`: `if cfg.web_build: stage_web_build(cfg.web_build, os.path.join(REPO_ROOT, "www"))`, with `REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` defined at module level in `run_stack.py` next to `DEFAULT_ARCO_COMMAND` (the same expression `terrarium_boot.py` uses; run_stack does not import terrarium_boot).

`.gitignore`: add

```
# The Flutter web build the Terrarium serves to guest phones; copied in
# by `run_stack --web-build DIR`, never committed.
www/app/
```

`www/README.md`: replace the `index.htm` placeholder bullet with:

```markdown
- `app/` (gitignored): the mm-tuneshroom guest app, built with `tool/sim
  build` there and copied here by `./smoke-test.sh --web-build
  /path/to/mm-tuneshroom/build/web`. Phones load it from the Terrarium's
  own static server (`WWW_URL`, port 8788, correct MIME types); the page
  opens its o2ws websocket to Arco on 8080. Arco also serves this tree but
  labels every file text/html, which the Flutter build cannot load under.
```

- [ ] **Step 4: Run the suite**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -1`
Expected: green; count = previous + 11 (three marker tests, three terrarium_boot tests, five run_stack tests).

- [ ] **Step 5: Live check, RUN ON: MYCOLOGICAL**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/o2ws-browser-link
./smoke-test.sh --ci --seconds 20 --devices 1 2>&1 | grep -E "WWW_URL|ARCO_WWW|stack run complete|FATAL"
```

Expected: a `WWW_URL: http://<lan-ip>:8788/` line and a green finish. Then `curl -sI http://<lan-ip>:8788/o2ws.js | grep -i content-type` while a `--serve` stack is up: `text/javascript`.

- [ ] **Step 6: Commit**

```bash
git add harness/markers.py harness/terrarium_boot.py harness/run_stack.py .gitignore www/README.md tests/test_markers.py tests/test_terrarium_boot.py tests/test_run_stack.py
git commit -m "feat(harness): serve the guest page on the LAN; WWW_URL marker; --www-port and --web-build"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (the `### devicelink/` section at about line 535, the o2lite section at about 825, the `### o2lite cutover (2026-09-08)` entry at about 3837, the suite count near 3889), `docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md` (Status), `docs/superpowers/specs/2026-09-08-o2lite-connectivity-migration-design.md` (Phase 2 pointer)

- [ ] **Step 1: Deep-dive**

1. In the `devicelink/` section, after the paragraph that says the JSON envelope is the in-process representation: a short paragraph "Wire flavor (2026-09-08)" stating the rule and the three-row table from the spec's section 3.2, and that the rewrite is in `O2LiteTransport.send` keyed by hello's protoversion.
2. In the `o2lite cutover` entry (or a new dated entry right after it, `### Browser guests over o2ws, Control side (2026-09-08)`): the static server (`harness/www_server.py`, port 8788, `0.0.0.0`, `WWW_URL`), `--www-port`, `--web-build`, `www/app/`, why the Terrarium serves the page rather than Arco (text/html for every file), and the P7/P8 results by number.
3. Update the "Suite at HEAD" line to the final count.
4. Under *Not yet built*: "the browser app itself (Plan B in mm-tuneshroom) and the live gate with a phone".

- [ ] **Step 2: Specs**

In the o2ws spec, set Status to "Control side landed (Plan A, this branch); browser side pending (Plan B in mm-tuneshroom)" and list any deviations. In the migration spec's Phase 2 section, add one line pointing at the o2ws spec as the refinement that wins for Phase 2.

- [ ] **Step 3: Run the suite and commit**

Run: `.venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -1`
Expected: unchanged count, green.

```bash
git add docs
git commit -m "docs: o2ws browser link, Control side; wire flavor table, static server, probes"
```

---

## Live verification (after Task 6; RUN ON: MYCOLOGICAL)

1. `./smoke-test.sh --ci --seconds 20 --devices 1` green with `WWW_URL` printed.
2. With `./smoke-test.sh --serve --devices 1` up, from a phone on the same LAN open `http://<lan-ip>:8788/o2wsclocksync.htm`: O2 time advances (this page uses the same-origin default, so it will NOT sync from 8788; that is expected and is exactly why the guest app passes the `o2ws` host. Open `http://<lan-ip>:8080/o2wsclocksync.htm` instead to see sync). Record which one you opened.
3. The full guest flow needs Plan B. Until it lands, the Control side's live proof is the probe page from Task 1 served at `http://<lan-ip>:8788/probe78.htm` (recreate it, do not commit it): hello with `"o2ws/1"` should produce a `/ie-probe7/room` message whose single argument is JSON text, not `?`. Extend the probe page for this: send `/game/hello "ssss" ie-probe7 probe o2ws/1 testshroom` after clock sync, register `/ie-probe7/room` with typespec `"s"`, and log `o2ws_get_string().slice(0, 40)`; the expected output starts with `{`.

Then `superpowers:finishing-a-development-branch`, and `mm-deepdive-sync` (in-repo deep-dive; rides the PR).
