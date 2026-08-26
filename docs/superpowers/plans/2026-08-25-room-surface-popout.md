# Room-Surface Pop-out and Launch Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Room fixture canvases stop auto-opening as browser tabs under `run_stack --open`; instead each fixture row on the Console's Room card gets a pop-out link, fed by a new `/game/canvas` wire message.

**Architecture:** The simulator process that owns a canvas URL reports it once over the devicelink wire right after `hello`. `DeviceLinkAgent` stores `dev -> url`; `ConsoleAgent` reads the map through an injected callback and `room_view` joins it onto fixture entries; `surface.js` renders a real anchor per fixture. A new `ROOM_URL:` marker splits Room-surface URLs from `BROWSE_URL:` so `run_stack --open` collects but never auto-opens them.

**Tech Stack:** Python 3 (offline pytest suite, `.venv/bin/python`), vanilla ES-module JS with Node `vm` DOM-stub tests, JSON-envelope websocket + o2lite transports.

**Spec:** `docs/superpowers/specs/2026-08-25-room-surface-popout-and-launch-defaults-design.md`

## Global Constraints

- Run all Python tests through `.venv/bin/python -m pytest`; there is no bare `python` on the dev boxes.
- The whole suite must stay green offline: no O2 network, no Arco, no pyarco import at module level anywhere under `control/`.
- `control/` gains no new module-level imports (existing purity test pins this); the canvas URL map crosses into `control/room_view.py` as a plain dict argument.
- `devicelink/protocol.py` stays the single source of truth for the wire shape.
- Only `http://` and `https://` canvas URLs are ever stored; anything else is refused at decode and logged.
- Front-end: URL is only ever set as `href` on `<a target="_blank" rel="noopener">`, never via `innerHTML`.
- No em dashes in any prose written for docs.
- Work happens on branch `room-surface-popout` (already exists, holds the spec commit).

---

### Task 1: `/game/canvas` wire message and client encoder

**Files:**
- Modify: `devicelink/protocol.py` (after `parse_game_address`, near the other helpers)
- Modify: `harness/shroom_client.py` (outbound section, next to `hello()` at ~line 115)
- Test: `tests/test_devicelink_protocol.py`, `tests/test_shroom_client.py`

**Interfaces:**
- Produces: `protocol.parse_canvas_url(args: list) -> str` (validated URL; raises `ValueError` on bad shape or scheme), `protocol.CANVAS_SCHEMES = ("http://", "https://")`, and `ShroomClient.canvas(url: str) -> dict` (an encoded `/game/canvas` envelope, typespec `"ss"`, args `[self.dev, url]`).
- Consumes: existing `protocol.encode` / `Envelope` and `ShroomClient._up`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_devicelink_protocol.py` add:

```python
class TestParseCanvasUrl:
    def test_accepts_http_and_https(self):
        assert protocol.parse_canvas_url(["ie1", "http://127.0.0.1:8123/"]) == \
            "http://127.0.0.1:8123/"
        assert protocol.parse_canvas_url(["ie1", "https://host/"]) == "https://host/"

    def test_refuses_javascript_scheme(self):
        with pytest.raises(ValueError):
            protocol.parse_canvas_url(["ie1", "javascript:alert(1)"])

    def test_refuses_data_scheme_relative_path_and_non_string(self):
        for bad in ["data:text/html,x", "/relative", "", None, 7]:
            with pytest.raises(ValueError):
                protocol.parse_canvas_url(["ie1", bad])

    def test_refuses_missing_url_arg(self):
        with pytest.raises(ValueError):
            protocol.parse_canvas_url(["ie1"])
```

In `tests/test_shroom_client.py` add (mirror the file's existing construction pattern for a client, e.g. however `test_hello`-style tests build one):

```python
def test_canvas_message_shape():
    client = ShroomClient("ie1", "TEST_PLAYER_NODE")   # adapt to existing fixture
    msg = client.canvas("http://127.0.0.1:8123/")
    assert msg["address"] == "/game/canvas"
    assert msg["typespec"] == "ss"
    assert msg["args"] == ["ie1", "http://127.0.0.1:8123/"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_devicelink_protocol.py -k canvas tests/test_shroom_client.py -k canvas -v`
Expected: FAIL with `AttributeError` (no `parse_canvas_url`, no `canvas`).

- [ ] **Step 3: Implement**

In `devicelink/protocol.py`, after `parse_game_address`:

```python
# Schemes a device-reported canvas URL may carry. The URL becomes a link
# in the operator's admin panel, so this is enforced at the decode
# boundary (same reasoning as the capture-label restriction below): a
# hostile device must not be able to plant a javascript: link.
CANVAS_SCHEMES = ("http://", "https://")


def parse_canvas_url(args: list) -> str:
    """Validate a /game/canvas message's args ([dev, url]) and return the
    URL. Raises ValueError on anything malformed; callers treat that as
    'refuse and log', never as an engine error."""
    if len(args) < 2 or not isinstance(args[1], str):
        raise ValueError("canvas needs a string url argument")
    url = args[1]
    if not url.startswith(CANVAS_SCHEMES):
        raise ValueError(f"canvas url must start with one of "
                         f"{CANVAS_SCHEMES}, got {url!r}")
    return url
```

In `harness/shroom_client.py`, next to `hello()`:

```python
    def canvas(self, url: str) -> dict:
        """Report the URL of this device's own browser canvas, sent once
        right after hello. Devices with no canvas simply never send it."""
        return self._up("canvas", "ss", [self.dev, url])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_devicelink_protocol.py tests/test_shroom_client.py -v`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add devicelink/protocol.py harness/shroom_client.py tests/test_devicelink_protocol.py tests/test_shroom_client.py
git commit -m "feat(devicelink): /game/canvas wire message with scheme allowlist"
```

---

### Task 2: `DeviceLinkAgent` stores canvas URLs; o2lite routes the verb

**Files:**
- Modify: `devicelink/agent.py` (constructor ~line 77, `_handle` dispatch ~line 449, `_finish_release` ~line 596)
- Modify: `devicelink/o2_transport.py` (GAME_VERBS tuple, line 71)
- Test: `tests/test_devicelink_agent.py`, `tests/test_o2_transport.py` (if a GAME_VERBS pin exists there; otherwise only the agent tests)

**Interfaces:**
- Consumes: `protocol.parse_canvas_url` from Task 1.
- Produces: `DeviceLinkAgent.canvas_urls() -> dict[str, str]` (a copy of the live `dev -> url` map). Task 3's `ConsoleAgent` calls this through an injected callback; Task 7 wires it in `terrarium_boot`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_devicelink_agent.py`, following the file's existing agent-construction fixtures (a fake server plus a real `GameServer`; mirror how neighboring tests build and drive `_handle`):

```python
class TestCanvasUrls:
    def _canvas_msg(self, dev, url):
        return {"timestamp": 0.0, "address": "/game/canvas",
                "typespec": "ss", "args": [dev, url]}

    def test_hello_then_canvas_is_stored(self, agent_and_server):
        agent, server = agent_and_server            # adapt to file's fixture
        agent._handle(object(), _hello_msg("ie1"))  # reuse existing helper
        agent._handle(object(), self._canvas_msg("ie1", "http://h:1/"))
        assert agent.canvas_urls() == {"ie1": "http://h:1/"}

    def test_bad_scheme_is_refused_not_stored(self, agent_and_server):
        agent, server = agent_and_server
        agent._handle(object(), _hello_msg("ie1"))
        agent._handle(object(), self._canvas_msg("ie1", "javascript:x"))
        assert agent.canvas_urls() == {}

    def test_canvas_urls_returns_a_copy(self, agent_and_server):
        agent, server = agent_and_server
        agent._handle(object(), _hello_msg("ie1"))
        agent._handle(object(), self._canvas_msg("ie1", "http://h:1/"))
        agent.canvas_urls().clear()
        assert agent.canvas_urls() == {"ie1": "http://h:1/"}

    def test_release_clears_the_url(self, agent_and_server):
        # Drive a full grant-then-release through the file's existing
        # join/release helpers, then assert the dev's entry is gone after
        # _finish_release runs.
        ...
```

Write the release test concretely against whatever helper the file already
uses to reach `_finish_release` (there are existing closing-fade tests to
copy from).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -k canvas -v`
Expected: FAIL with `AttributeError: canvas_urls`.

- [ ] **Step 3: Implement**

In `devicelink/agent.py` constructor, with the other dict state:

```python
        # dev -> the URL of that device's own browser canvas, reported by
        # /game/canvas (simulators only; hardware and phones never send
        # it). No persistence: an ephemeral port is stale the moment its
        # process dies. Read by the Console through canvas_urls().
        self._canvas_urls: dict[str, str] = {}
```

In `_handle`, add a branch before the generic `_on_verb` fallthrough:

```python
        elif verb == "canvas":
            self._on_canvas(dev, env.args)
```

New method next to `_on_hello`:

```python
    def _on_canvas(self, dev: str, args: list) -> None:
        try:
            url = protocol.parse_canvas_url(args)
        except ValueError as exc:
            logger.warning("refusing canvas url from %s: %s", dev, exc)
            return
        self._canvas_urls[dev] = url

    def canvas_urls(self) -> dict:
        """A copy of the live dev -> canvas-url map, for the Console."""
        return dict(self._canvas_urls)
```

In `_finish_release(dev)`, alongside the other per-dev pops:

```python
        self._canvas_urls.pop(dev, None)
```

In `devicelink/o2_transport.py` line 71, add the verb so o2lite routes it:

```python
GAME_VERBS = ("hello", "join", "tilt", "tap", "shake", "capture",
              "telemetry", "canvas")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_o2_transport.py -v`
Expected: PASS. If `tests/test_o2_transport.py` pins the GAME_VERBS list, update the pin in the same commit.

- [ ] **Step 5: Commit**

```bash
git add devicelink/agent.py devicelink/o2_transport.py tests/test_devicelink_agent.py tests/test_o2_transport.py
git commit -m "feat(devicelink): agent stores per-dev canvas URLs; o2lite routes the verb"
```

---

### Task 3: `room_view` URL join and `ConsoleAgent` injection

**Files:**
- Modify: `control/room_view.py` (`fixtures_view` line 64, `room_view` line 88)
- Modify: `console/agent.py` (constructor line 32, `_current_room` line 167, `_devices_view` line 239)
- Modify: `console/protocol.py` (`device_view` line 47)
- Test: `tests/test_room_view.py`, `tests/test_console_agent.py`

**Interfaces:**
- Consumes: `DeviceLinkAgent.canvas_urls()` shape from Task 2 (via a plain callable; the tests use a lambda, never a devicelink import).
- Produces: `fixtures_view(profile, room, canvas_urls=None)` and `room_view(room, profile, role, controllers, canvas_urls=None)`; each fixture entry gains `"url": str | None`. `ConsoleAgent.__init__(..., canvas_urls=None)` where `canvas_urls` is `Callable[[], dict] | None`. `device_view(info, role_name, url=None)` gains `"url"` in its dict.

- [ ] **Step 1: Write the failing tests**

In `tests/test_room_view.py` (reuse the file's existing profile/room fakes):

```python
def test_bound_fixture_with_reported_canvas_gets_url(...):
    view = room_view(room, profile, role, {},
                     canvas_urls={"sim-room-main": "http://h:9/"})
    by_name = {f["name"]: f for f in view["fixtures"]}
    assert by_name["main"]["url"] == "http://h:9/"

def test_unbound_or_unreported_fixture_url_is_none(...):
    view = room_view(room, profile, role, {}, canvas_urls={})
    assert all(f["url"] is None for f in view["fixtures"])

def test_omitting_canvas_urls_still_works(...):
    view = room_view(room, profile, role, {})
    assert all(f["url"] is None for f in view["fixtures"])
```

In `tests/test_console_agent.py` (reuse its GameServer/console fixtures; a
bound-Room fixture setup already exists for the Room-panel tests):

```python
def test_room_payload_carries_fixture_urls(...):
    agent = ConsoleAgent(gs, server, room_bridge=bridge,
                         canvas_urls=lambda: {"sim-room-main": "http://h:9/"})
    room = agent.snapshot()["room"]
    by_name = {f["name"]: f for f in room["fixtures"]}
    assert by_name["main"]["url"] == "http://h:9/"

def test_device_view_carries_url_when_known(...):
    # hello a device, report its url via the callback, assert
    # snapshot()["devices"] entry has "url": "http://h:9/" and a
    # device with no reported canvas has "url": None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_view.py tests/test_console_agent.py -k "url or canvas" -v`
Expected: FAIL (`TypeError: unexpected keyword argument 'canvas_urls'` or `KeyError: 'url'`).

- [ ] **Step 3: Implement**

`control/room_view.py`: `fixtures_view` gains the parameter and field
(`control/` purity is untouched, it is a plain dict):

```python
def fixtures_view(profile, room, canvas_urls=None) -> list[dict]:
    urls = canvas_urls or {}
    out = []
    for name, start, count in profile.fixture_slices():
        fixture = next(f for f in profile.fixtures if f.name == name)
        dev = room.bound.get(name)
        out.append({
            ...existing fields unchanged...,
            "dev": dev,
            "url": urls.get(dev) if dev else None,
        })
    return out
```

`room_view` passes it through:

```python
def room_view(room, profile, role, controllers: dict,
              canvas_urls=None) -> dict | None:
    ...
        "fixtures": fixtures_view(profile, room, canvas_urls),
```

`console/agent.py`: constructor stores `self._canvas_urls = canvas_urls`;
`_current_room` ends with:

```python
        urls = self._canvas_urls() if self._canvas_urls else {}
        return room_view(gs.room, profile, role, controllers, urls)
```

`_devices_view` passes each device's URL:

```python
        urls = self._canvas_urls() if self._canvas_urls else {}
        ...
            out.append(protocol.device_view(info, role_name,
                                            urls.get(info.dev)))
```

`console/protocol.py`:

```python
def device_view(info, role_name, url=None) -> dict:
    return {"dev": info.dev, "name": info.name, "role": role_name,
            "url": url}
```

Note: no observer poke is needed. `ConsoleAgent.poll()` already calls
`_broadcast_room_if_changed()` every tick and diffs the whole payload, so
the fixture URL arriving changes the payload and broadcasts on the next
tick. Record this as a deliberate simplification of the spec's section 2
poke (same outcome, less machinery) in the commit message.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_view.py tests/test_console_agent.py tests/test_console_protocol.py -v`
Expected: PASS. If `test_console_protocol.py` pins `device_view`'s shape, update the pin here.

- [ ] **Step 5: Commit**

```bash
git add control/room_view.py console/agent.py console/protocol.py tests/test_room_view.py tests/test_console_agent.py tests/test_console_protocol.py
git commit -m "feat(console): fixture and device views carry reported canvas URLs

Spec section 2 described an observer poke on canvas arrival; poll()'s
existing room-payload diff already rebroadcasts within one tick, so the
poke is dropped as redundant machinery, same outcome."
```

---

### Task 4: simulators report their canvas after every hello

**Files:**
- Modify: `harness/o2_shroom.py` (hello send sites: initial ~line 518, join-retry resend ~line 566, heartbeat ~line 574)
- Modify: `harness/room_simulator.py` (hello send ~line 172, heartbeat resend ~line 190)
- Test: `tests/test_o2_shroom.py`, `tests/test_room_simulator.py`

**Interfaces:**
- Consumes: `ShroomClient.canvas(url)` from Task 1 (websocket path); raw `o2lite.send_cmd("/game/canvas", 0, "ss", dev, url)` (o2lite path).
- Produces: the rule "wherever hello is sent, canvas follows", so a Control restart (which re-hellos via heartbeat) re-learns every URL.

- [ ] **Step 1: Write the failing tests**

In `tests/test_o2_shroom.py`, extend whatever fake-o2lite harness the
heartbeat tests already use to record `send_cmd` calls, and assert that
each recorded `/game/hello` is immediately followed by a
`/game/canvas` with `("ss", dev, url)` where url is
`http://{sim_host}:{backend.port}/`. Cover the initial send and the
heartbeat resend.

In `tests/test_room_simulator.py`, same pattern against the recorded
websocket sends: hello then canvas on connect, and again on each
heartbeat resend, with `client.canvas(...)`'s envelope shape.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py tests/test_room_simulator.py -k canvas -v`
Expected: FAIL (no canvas message recorded).

- [ ] **Step 3: Implement**

In `harness/o2_shroom.py` `main()`, after `backend.open()` compute once:

```python
    canvas_url = f"http://{args.sim_host}:{backend.port}/"
```

and define a small local helper used at all three hello sites:

```python
    def send_hello():
        o2lite.send_cmd("/game/hello", 0, "s", args.dev)
        o2lite.send_cmd("/game/canvas", 0, "ss", args.dev, canvas_url)
```

Replace each bare hello `send_cmd` (lines ~518, ~566, ~574) with
`send_hello()` (the ~566 site keeps its following join send).

In `harness/room_simulator.py`, after the connect-time hello:

```python
            await ws.send(json.dumps(client.hello()))
            await ws.send(json.dumps(client.canvas(canvas_url)))
```

with `canvas_url = f"http://{args.sim_host}:{backend.port}/"` computed
next to the existing URL print, and the same pair in the heartbeat resend
branch (~line 190).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_o2_shroom.py tests/test_o2_shroom_input.py tests/test_room_simulator.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/o2_shroom.py harness/room_simulator.py tests/test_o2_shroom.py tests/test_room_simulator.py
git commit -m "feat(harness): simulators report their canvas URL after every hello"
```

---

### Task 5: `ROOM_URL` marker split and run_stack collect-but-never-open

**Files:**
- Modify: `harness/markers.py` (next to `BROWSE_URL`, line 74)
- Modify: `harness/room_simulator.py` (URL print, line 148)
- Modify: `harness/o2_shroom.py` (URL print, line 447)
- Modify: `harness/run_stack.py` (`collect_url`, lines 210-226; success-summary echo)
- Test: `tests/test_markers.py`, `tests/test_run_stack.py`

**Interfaces:**
- Produces: `markers.ROOM_URL = "ROOM_URL:"`. Contract: `BROWSE_URL` lines are collected, echoed, and auto-opened under `--open`; `ROOM_URL` lines are collected and echoed with a `room surface (open from the Console):` label and never auto-opened.

- [ ] **Step 1: Write the failing tests**

In `tests/test_markers.py` (follow its existing emit-site pinning style):
pin `markers.ROOM_URL == "ROOM_URL:"`; pin `harness/room_simulator.py`'s
print to `ROOM_URL`; pin `harness/o2_shroom.py` to emit `ROOM_URL` under
`--no-join` and `BROWSE_URL` otherwise; keep the existing
`terrarium_boot` `BROWSE_URL` pin unchanged.

In `tests/test_run_stack.py` (reuse its fake-opener pattern around
`collect_url`): a `BROWSE_URL` line is opened under `open_urls` and
collected; a `ROOM_URL` line is collected but the opener is never called,
in both `open_urls` modes; the success summary labels the two kinds
distinctly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_markers.py tests/test_run_stack.py -k "room_url or ROOM" -v`
Expected: FAIL (`AttributeError: ROOM_URL`).

- [ ] **Step 3: Implement**

`harness/markers.py`, directly below `BROWSE_URL`:

```python
# A line carrying a URL worth knowing but NOT worth an automatic browser
# tab: a Room fixture canvas, opened on demand from the Console's Room
# card instead. run_stack collects and echoes these, never opens them.
ROOM_URL = "ROOM_URL:"
```

`harness/room_simulator.py` line 148: swap `markers.BROWSE_URL` for
`markers.ROOM_URL` (message text unchanged).

`harness/o2_shroom.py` line 447:

```python
    url_marker = markers.ROOM_URL if args.no_join else markers.BROWSE_URL
    print(f"{url_marker} Watch the Shroom at "
          f"http://{args.sim_host}:{backend.port}/", flush=True)
```

`harness/run_stack.py` `collect_url`: keep one `_URL_PATTERN` parse, split
the destinations. Collected room URLs go to a separate `room_urls` list;
the opener fires only for `BROWSE_URL`:

```python
    room_urls: list[str] = []

    def collect_url(line: str) -> None:
        is_browse = markers.BROWSE_URL in line
        is_room = markers.ROOM_URL in line
        if not (is_browse or is_room):
            return
        match = _URL_PATTERN.search(line)
        if match is None:
            return
        if is_room:
            room_urls.append(match.group())
            return
        urls.append(match.group())
        if cfg.open_urls:
            opener(match.group())
```

In the success summary where `urls` are echoed, echo `room_urls` too, one
line each, labelled `room surface (open from the Console): <url>`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_markers.py tests/test_run_stack.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/markers.py harness/room_simulator.py harness/o2_shroom.py harness/run_stack.py tests/test_markers.py tests/test_run_stack.py
git commit -m "feat(harness): ROOM_URL marker; run_stack echoes room surfaces, never auto-opens them"
```

---

### Task 6: per-fixture pop-out anchor on the Room card

**Files:**
- Modify: `console/static/surface.js` (`bindingControls` line 159, `bindStateKey` line 262)
- Test: `tests/js/surface_panel.test.js` (run through its Python wrapper)

**Interfaces:**
- Consumes: fixture entries carrying `"url": str | null` from Task 3.
- Produces: inside the binding-controls span, a fixture with both `dev` and `url` renders `<a class="popout" href=url target="_blank" rel="noopener">` labelled with a north-east arrow character; `url: null` renders no anchor. The anchor lives inside `bindingControls` so the existing bind-key rebuild machinery owns its lifecycle.

- [ ] **Step 1: Write the failing tests**

In `tests/js/surface_panel.test.js`, following the file's existing
render-and-inspect pattern (DOM stub from `_dom_stub.js`; drive the module
through its exported render path with a room payload):

- a fixture with `dev` and `url` set renders exactly one `.popout` anchor
  with `href` equal to the url, `target === "_blank"`,
  `rel === "noopener"`;
- a fixture with `dev` set and `url: null` renders no `.popout` anchor;
- two `room_changed` payloads identical except `controllers` do NOT
  recreate the anchor element (same node identity, the file's existing
  sibling-fixture identity test at `test(surface_panel)` shows the
  pattern);
- a payload whose only change is `url` going from null to a value DOES
  rebuild the binding controls (anchor appears).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_panel_behavior.py -v` (the Python wrapper that runs the js tests; skip-if-no-node applies)
Expected: FAIL on the new cases.

- [ ] **Step 3: Implement**

`surface.js` `bindingControls(fixture)`, inside the `if (fixture.dev)`
branch after the dev chip:

```js
    if (fixture.url) {
      const popout = mk("a", "popout", "↗");
      popout.href = fixture.url;
      popout.target = "_blank";
      popout.rel = "noopener";
      popout.title = `Open ${fixture.name} surface`;
      span.appendChild(popout);
    }
```

(adapt `span` to whatever container variable the function actually
appends the chip and Release button to).

`bindStateKey(fixture)`: include the url so its arrival triggers exactly
one rebuild:

```js
function bindStateKey(fixture) {
  if (fixture.dev) return `dev:${fixture.dev}:${fixture.url || ""}`;
  if (armedFixtures.has(fixture.name)) return "armed";
  return "unbound";
}
```

Style: add a minimal `.popout` rule to `console/static/terrarium.css`
using existing color tokens (an icon-sized link next to the chip; follow
the file's existing chip/button vocabulary, no new palette).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_panel_behavior.py tests/test_console_agent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add console/static/surface.js console/static/terrarium.css tests/js/surface_panel.test.js
git commit -m "feat(console): per-fixture pop-out link on the Room card"
```

---

### Task 7: boot wiring, full suite, live smoke, deep-dive doc

**Files:**
- Modify: `harness/terrarium_boot.py` (ConsoleAgent construction, ~line 994)
- Modify: `docs/MM_TERRARIUM.md` (BROWSE_URL/marker prose, run_stack `--open` description, Room-panel section)
- Test: full suite + live smoke

**Interfaces:**
- Consumes: everything above. `ConsoleAgent(..., canvas_urls=agent.canvas_urls)` where `agent` is the `DeviceLinkAgent` built at ~line 204.

- [ ] **Step 1: Wire the console to the agent's URL map**

At the `ConsoleAgent` construction (~line 994):

```python
            console_agent = ConsoleAgent(gs, console_server,
                                         ...existing args...,
                                         canvas_urls=agent.canvas_urls)
```

Add a boot-path test if `tests/test_terrarium_boot.py` covers console
construction; otherwise the live smoke below is the check.

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass (baseline was 1326 passed, 1 skipped; new total higher).

- [ ] **Step 3: Live smoke**

Run: `./smoke-test.sh --open --devices 2` (needs a browser; hold it up,
then Ctrl-C). Verify: exactly Console + two shroom tabs auto-open; no
Room-surface tabs; the Room card shows a pop-out link per bound fixture;
clicking one opens the right canvas; the run_stack summary echoes the
room-surface URLs with their distinct label. Then confirm clean teardown:
`pgrep -f "o2_shroom|terrarium_boot|room_simulator"` is empty.

- [ ] **Step 4: Update the deep-dive doc**

In `docs/MM_TERRARIUM.md`: the `harness/markers.py` bullet gains
`ROOM_URL`; the `--open` description states Room surfaces are echoed, not
opened, and are reachable from the Room card's pop-out links; the Room
panel section mentions the per-fixture pop-out and the `/game/canvas`
message with its scheme allowlist. Keep prose style; no em dashes.

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py docs/MM_TERRARIUM.md
git commit -m "feat(harness): console reads canvas URLs; document the pop-out flow"
```

---

## Completion

After Task 7: push `room-surface-popout`, open a PR against `main`
(include the spec and plan paths in the description), and merge once CI or
the local suite confirms green, per the approved session goal.
