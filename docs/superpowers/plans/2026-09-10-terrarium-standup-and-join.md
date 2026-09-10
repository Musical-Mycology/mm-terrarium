# Terrarium standup script, room-aware Bit load, and Join card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command stands up a clean Terrarium (no Bit, optional Room); the Console prompts for a Room when a Bit is loaded; a Join card shows each node's guest URL, a QR code, and a pre-populated Tuneshroom launch command.

**Architecture:** `run_stack --no-bit` forwards a new `terrarium_boot --no-bit` so the existing Console-driven serve loops start without a round-1 Bit, and `./terrarium.sh` wraps that with dev defaults. `ConsoleAgent`'s `load_bit` gains an optional `room` and orchestrates abort / unload / load_room / load_bit; the picker in `bit.js` grows a room select. A pure `control/join_info.py` builds the guest URLs, `segno` renders QR SVGs server-side, the agent ships them in the snapshot and a `join_changed` event, and `join.js` renders them.

**Tech Stack:** Python 3 stdlib + `segno` (QR), the existing `websockets` Console server, vanilla ES-module JavaScript (no build step), pytest offline suite via `.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-09-10-terrarium-standup-and-join-design.md`

## Global Constraints

- Run every test through the project venv: `.venv/bin/python -m pytest tests -q`. A bare `python3` produces a phantom luxaeterna import error (README). This worktree already symlinks `.venv` to the main checkout's venv.
- The offline test property is load-bearing: no test may need Arco, pyarco, o2litepy, or a network. `segno` is pure Python with no transitive deps, so it keeps that property. Tests that use the real encoder must `pytest.importorskip("segno")`.
- `control/` never imports harness modules, o2litepy, or pyarco (boundary rules). `control/join_info.py` is pure and takes every input as an argument.
- Console static assets: no external fetches (`tests/test_console_static.py`), every `.js` file is an ES module, no build step.
- Markers are constants in `harness/markers.py`, emitted by name (`markers.NAME`) so `tests/test_markers.py`'s source check passes.
- Ports: Console default for the standup script is **8772**; Arco HTTP is **8080** (`ARCO_HTTP_PORT`); the guest page is **8788** (`WWW_PORT`). Never hardcode any of these outside the constants that already exist.
- No em dashes anywhere in prose, docstrings, help text, or commit messages.
- Commit messages carry no attribution lines.
- The pre-populated device command is the Chrome sim form only: `flutter run -d chrome -t lib/sim_main.dart --dart-define=NODE=<node> --dart-define=O2WS=<ip>:8080 --dart-define=ENS=<ensemble>`. Never embed a path to the mm-tuneshroom checkout.

---

### Task 1: `control/join_info.py` and the `segno` dependency

**Files:**
- Create: `control/join_info.py`
- Create: `tests/test_join_info.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `build_join_info(*, lan_ip: str, www_port: int, arco_http_port: int, ensemble: str, bit_name: str | None, nodes, app_present: bool, qr_svg=default_qr_svg) -> dict` where `nodes` is an iterable of `(role, node)` pairs (the shape of `BitConfig.launch.nodes`). Also `guest_url(...)`, `tuneshroom_command(...)`, `default_qr_svg(url) -> str`, and the constant `NATIVE_NOTE`.
- Return shape (Tasks 4, 6, 10 rely on every key):
  ```
  {"www_url": str, "o2ws_host": str, "ensemble": str, "app_present": bool,
   "bit": str | None,
   "nodes": [{"role": str, "node": str, "url": str, "qr_svg": str | None,
              "tuneshroom_cmd": str}],
   "native_note": str}
  ```

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:

```
# QR codes for the Console's Join card (control/join_info.py). Pure Python,
# no transitive dependencies, so the offline suite stays offline.
segno>=1.5
```

Install it into the venv:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_join_info.py`:

```python
"""control/join_info.py: what a guest needs to reach a loaded Bit."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from control.join_info import (NATIVE_NOTE, build_join_info, guest_url,
                               tuneshroom_command)


def _info(**overrides):
    kwargs = dict(lan_ip="10.0.0.7", www_port=8788, arco_http_port=8080,
                  ensemble="arco", bit_name="MetronomeBit",
                  nodes=(("player", "METRO_PLAYER_NODE"),),
                  app_present=True, qr_svg=lambda url: f"<svg>{url}</svg>")
    kwargs.update(overrides)
    return build_join_info(**kwargs)


def test_guest_url_carries_node_o2ws_and_ensemble_and_no_dev():
    url = guest_url(lan_ip="10.0.0.7", www_port=8788, arco_http_port=8080,
                    ensemble="arco", node="METRO_PLAYER_NODE")
    parsed = urlparse(url)
    assert parsed.scheme == "http"
    assert parsed.netloc == "10.0.0.7:8788"
    assert parsed.path == "/app/"
    query = parse_qs(parsed.query)
    assert query == {"node": ["METRO_PLAYER_NODE"], "o2ws": ["10.0.0.7:8080"],
                     "ens": ["arco"]}
    assert "dev" not in query


def test_tuneshroom_command_is_the_chrome_sim_form_with_defines():
    cmd = tuneshroom_command(lan_ip="10.0.0.7", arco_http_port=8080,
                             ensemble="arco", node="METRO_PLAYER_NODE")
    assert cmd.startswith("flutter run -d chrome -t lib/sim_main.dart ")
    assert "--dart-define=NODE=METRO_PLAYER_NODE" in cmd
    assert "--dart-define=O2WS=10.0.0.7:8080" in cmd
    assert "--dart-define=ENS=arco" in cmd
    assert "DEV=" not in cmd
    assert "/Users/" not in cmd


def test_build_join_info_shape():
    info = _info()
    assert info["www_url"] == "http://10.0.0.7:8788/app/"
    assert info["o2ws_host"] == "10.0.0.7:8080"
    assert info["ensemble"] == "arco"
    assert info["app_present"] is True
    assert info["bit"] == "MetronomeBit"
    assert info["native_note"] == NATIVE_NOTE
    assert len(info["nodes"]) == 1
    row = info["nodes"][0]
    assert row["role"] == "player"
    assert row["node"] == "METRO_PLAYER_NODE"
    assert row["url"] == guest_url(lan_ip="10.0.0.7", www_port=8788,
                                   arco_http_port=8080, ensemble="arco",
                                   node="METRO_PLAYER_NODE")
    assert row["qr_svg"] == f"<svg>{row['url']}</svg>"
    assert row["tuneshroom_cmd"] == tuneshroom_command(
        lan_ip="10.0.0.7", arco_http_port=8080, ensemble="arco",
        node="METRO_PLAYER_NODE")


def test_no_bit_yields_no_node_rows_but_still_the_guest_url():
    info = _info(bit_name=None, nodes=())
    assert info["bit"] is None
    assert info["nodes"] == []
    assert info["www_url"] == "http://10.0.0.7:8788/app/"


def test_app_present_is_threaded_verbatim():
    assert _info(app_present=False)["app_present"] is False


def test_qr_is_none_when_no_encoder_is_given():
    info = _info(qr_svg=None)
    assert info["nodes"][0]["qr_svg"] is None


def test_qr_is_none_when_the_encoder_raises():
    def boom(url):
        raise RuntimeError("no encoder")
    info = _info(qr_svg=boom)
    assert info["nodes"][0]["qr_svg"] is None
    assert info["nodes"][0]["url"]   # the row survives


def test_default_encoder_produces_inline_svg():
    pytest.importorskip("segno")
    info = build_join_info(lan_ip="10.0.0.7", www_port=8788,
                           arco_http_port=8080, ensemble="arco",
                           bit_name="TestBit",
                           nodes=(("player", "TEST_PLAYER_NODE"),),
                           app_present=True)
    svg = info["nodes"][0]["qr_svg"]
    assert svg.startswith("<svg")
    assert "<?xml" not in svg
    assert "http://" not in svg    # the URL is encoded, never inlined as text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_join_info.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.join_info'`

- [ ] **Step 4: Write the module**

Create `control/join_info.py`:

```python
"""Join info: everything a guest needs to reach the loaded Bit.

The Console's Join card and terrarium_boot's JOIN_URL stdout lines both
read this. Pure: every input is an argument (LAN address, ports,
ensemble, the Bit's role-to-node map), so it runs offline and the same
function serves both consumers. Spec:
docs/superpowers/specs/2026-09-10-terrarium-standup-and-join-design.md
section 5.

Why `dev` is absent from the URL and the command: the Tuneshroom web
app mints its own device id per page load (mm-tuneshroom
lib/sim/device_id.dart), and many phones scan the same poster, so a
pinned dev would make every scan after the first lose O2's service-name
race.

Why only the Chrome sim command: since the o2lite cutover the Terrarium
speaks o2lite and o2ws only. Native iOS/Android and the Radxa app still
use the old websocket wire, so the browser sim is the one Tuneshroom
path that connects today.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

NATIVE_NOTE = ("Native iOS/Android and the Radxa app cannot connect until "
               "mm-tuneshroom's FFI o2lite link lands; use the Chrome sim.")


def guest_url(*, lan_ip: str, www_port: int, arco_http_port: int,
              ensemble: str, node: str) -> str:
    """The URL a phone opens (or scans) to join `node` on this Terrarium.
    The page is served by harness/www_server.py under /app/; its o2ws
    websocket goes to Arco's HTTP port on the same host."""
    query = urlencode({"node": node,
                       "o2ws": f"{lan_ip}:{arco_http_port}",
                       "ens": ensemble})
    return f"http://{lan_ip}:{www_port}/app/?{query}"


def tuneshroom_command(*, lan_ip: str, arco_http_port: int, ensemble: str,
                       node: str) -> str:
    """The `flutter run` line for the Tuneshroom web sim, to be run from
    the mm-tuneshroom checkout root. The defines mirror the URL query
    parameters (mm-tuneshroom lib/sim/join_params.dart resolves URL
    params first, then --dart-define)."""
    return ("flutter run -d chrome -t lib/sim_main.dart "
            f"--dart-define=NODE={node} "
            f"--dart-define=O2WS={lan_ip}:{arco_http_port} "
            f"--dart-define=ENS={ensemble}")


def default_qr_svg(url: str) -> str:
    """An inline SVG (no XML prologue) of a medium-error-correction QR
    for `url`. segno is imported lazily so importing this module never
    requires it."""
    import segno
    return segno.make(url, error="m").svg_inline(scale=4)


def build_join_info(*, lan_ip: str, www_port: int, arco_http_port: int,
                    ensemble: str, bit_name: str | None, nodes,
                    app_present: bool, qr_svg=default_qr_svg) -> dict:
    """The Join card's read model. `nodes` is an iterable of (role, node)
    pairs, the shape of BitConfig.launch.nodes; empty when no Bit is
    loaded. `qr_svg` is a callable url -> svg string, or None for no QR;
    an encoder that raises yields qr_svg=None for that row rather than a
    failed snapshot."""
    rows = []
    for role, node in nodes:
        url = guest_url(lan_ip=lan_ip, www_port=www_port,
                        arco_http_port=arco_http_port, ensemble=ensemble,
                        node=node)
        svg = None
        if qr_svg is not None:
            try:
                svg = qr_svg(url)
            except Exception:
                logger.exception("QR encoder failed for %s; card shows the "
                                 "URL only", url)
                svg = None
        rows.append({
            "role": role,
            "node": node,
            "url": url,
            "qr_svg": svg,
            "tuneshroom_cmd": tuneshroom_command(
                lan_ip=lan_ip, arco_http_port=arco_http_port,
                ensemble=ensemble, node=node),
        })
    return {
        "www_url": f"http://{lan_ip}:{www_port}/app/",
        "o2ws_host": f"{lan_ip}:{arco_http_port}",
        "ensemble": ensemble,
        "app_present": bool(app_present),
        "bit": bit_name,
        "nodes": rows,
        "native_note": NATIVE_NOTE,
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_join_info.py -q`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add control/join_info.py tests/test_join_info.py requirements.txt
git commit -m "feat(control): join_info builds guest URLs, QR SVGs and the Tuneshroom command"
```

---

### Task 2: Wire protocol: `load_bit` gains `room`; `bits_listed` rows gain `default_room_type` and `nodes`; `join` in the snapshot

**Files:**
- Modify: `uplink/protocol.py:11-14` (LoadBitCommand), `uplink/protocol.py:53-60` (parse_command)
- Modify: `control/bit_registry.py:245-262` (list_view rows)
- Modify: `console/protocol.py:83-106` (snapshot_event) and add `join_changed_event`
- Test: `tests/test_protocol.py`, `tests/test_bit_registry.py`, `tests/test_console_protocol.py`

**Interfaces:**
- Produces: `LoadBitCommand(name, overrides=None, room=None)`; `parse_command({"command": "load_bit", "name": ..., "room": "TEST"})` returns it with `room="TEST"`; a non-string `room` raises `ValueError`.
- Produces: `list_view()` rows carry `"default_room_type": str` and `"nodes": dict[str, str]` (role to node).
- Produces: `console.protocol.snapshot_event(..., join=None)` adds a `"join"` key; `console.protocol.join_changed_event(join) -> {"event": "join_changed", "join": join}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_protocol.py`:

```python
def test_load_bit_command_parses_an_optional_room():
    from uplink.protocol import LoadBitCommand, parse_command
    cmd = parse_command({"command": "load_bit", "name": "TestBit",
                         "room": "DEMO"})
    assert cmd == LoadBitCommand(name="TestBit", overrides=None, room="DEMO")


def test_load_bit_command_room_defaults_to_none():
    from uplink.protocol import parse_command
    cmd = parse_command({"command": "load_bit", "name": "TestBit"})
    assert cmd.room is None


def test_load_bit_command_rejects_a_non_string_room():
    import pytest
    from uplink.protocol import parse_command
    with pytest.raises(ValueError):
        parse_command({"command": "load_bit", "name": "TestBit", "room": 7})
```

In `tests/test_bit_registry.py`, find `test_list_view_shape_and_hidden_filter` (line 59) and, right after the existing `assert row["room_types"] == ["TEST"]` line (line 69), add:

```python
    assert row["default_room_type"] == "TEST"
    assert isinstance(row["nodes"], dict)
```

Then add a new test at the end of that file:

```python
def test_list_view_carries_default_room_and_nodes_for_testbit():
    from control.bit_registry import BitRegistry
    registry = BitRegistry.scan(["bits"])
    row = next(r for r in registry.list_view(include_hidden=True)
               if r["name"] == "TestBit")
    assert row["default_room_type"] == "TEST"
    assert row["nodes"] == {"player": "TEST_PLAYER_NODE",
                            "jammer": "TEST_JAM_NODE"}
```

Append to `tests/test_console_protocol.py`:

```python
def test_snapshot_carries_join_and_defaults_it_to_none():
    msg = protocol.snapshot_event(
        state="IDLE", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={})
    assert msg["join"] is None
    join = {"www_url": "http://10.0.0.7:8788/app/", "nodes": []}
    msg = protocol.snapshot_event(
        state="IDLE", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={}, join=join)
    assert msg["join"] == join


def test_join_changed_event_shape():
    join = {"www_url": "http://10.0.0.7:8788/app/", "nodes": []}
    assert protocol.join_changed_event(join) == {"event": "join_changed",
                                                 "join": join}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_protocol.py tests/test_bit_registry.py tests/test_console_protocol.py -q`
Expected: the 6 new tests FAIL (`TypeError` on `room`, `KeyError: 'default_room_type'`, `KeyError: 'join'`, `AttributeError: join_changed_event`)

- [ ] **Step 3: Implement**

In `uplink/protocol.py`, change `LoadBitCommand` to:

```python
@dataclass
class LoadBitCommand:
    name: str
    overrides: dict | None = None
    # Which Room to load the Bit into. None keeps the active Room (and is
    # refused with "no room loaded" when there is none). A different name
    # than the active Room makes the Console agent unload and reload the
    # Room first (spec 2026-09-10 section 4).
    room: str | None = None
```

In `parse_command`, replace the `load_bit` branch body with:

```python
    if command == "load_bit":
        name = msg.get("name")
        if not isinstance(name, str):
            raise ValueError("load_bit requires a string 'name'")
        overrides = msg.get("overrides")
        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError("load_bit 'overrides' must be a dict when given")
        room = msg.get("room")
        if room is not None and not isinstance(room, str):
            raise ValueError("load_bit 'room' must be a string when given")
        return LoadBitCommand(name=name, overrides=overrides, room=room)
```

In `control/bit_registry.py` `list_view`, add two keys to the row dict right after `"room_types": list(config.launch.room_types),`:

```python
                "default_room_type": config.launch.default_room_type,
                "nodes": dict(config.launch.nodes),
```

In `console/protocol.py`, add `join=None` to `snapshot_event`'s keyword parameters and `"join": join,` to the returned dict (after `"design_vocab": design_vocab,`). Then add, right after `room_changed_event`:

```python
def join_changed_event(join) -> dict:
    """The Join card's read model changed (a Bit loaded or unloaded).
    `join` is control.join_info.build_join_info()'s output, or None when
    no join provider is wired up."""
    return {"event": "join_changed", "join": join}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_protocol.py tests/test_bit_registry.py tests/test_console_protocol.py tests/test_console_agent.py -q`
Expected: all pass (the agent tests still pass because `room` defaults to None)

- [ ] **Step 5: Commit**

```bash
git add uplink/protocol.py control/bit_registry.py console/protocol.py tests/test_protocol.py tests/test_bit_registry.py tests/test_console_protocol.py
git commit -m "feat(protocol): load_bit takes an optional room; bits_listed carries default_room_type and nodes; snapshot carries join"
```

---

### Task 3: Room-aware `load_bit` in `ConsoleAgent`

**Files:**
- Modify: `console/agent.py:186-197` (LoadBitCommand branch) and add `_ensure_room_for_bit`
- Test: `tests/test_console_agent.py`

**Interfaces:**
- Consumes: `LoadBitCommand.room` (Task 2), `Terrarium.load_room/unload_room/state/room`, `BitConfig.launch.room_types`.
- Produces: `ConsoleAgent._ensure_room_for_bit(command, cfg) -> str | None` (refusal reason or None). Order: resolve config, check target room support, abort a loaded Bit, unload, load_room, then load_bit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_console_agent.py` (its imports already include `GameServer`, `TestBit`, `ConsoleAgent`, `parse_manifest`, `merge_overrides`; add the two Terrarium fixture imports shown):

```python
from tests.test_terrarium import DEMO_SPEC, TEST_SPEC, make_config, make_terrarium
from control.terrarium import TerrariumState


def _two_room_terrarium(**kwargs):
    """A NO_ROOM Terrarium with TEST and DEMO configured, driving a
    GameServer that knows TestBit (room_types TEST and DEMO)."""
    return make_terrarium(
        config=make_config(rooms={"TEST": TEST_SPEC, "DEMO": DEMO_SPEC}),
        gs=GameServer({"TestBit": TestBit}), **kwargs)


def _testbit_registry(room_types=None):
    base = parse_manifest(open("bits/test/bit.toml").read(),
                          source="bits/test/bit.toml")
    if room_types is not None:
        base = merge_overrides(
            base, {"launch": {"room_types": room_types,
                              "default_room_type": room_types[0]}},
            source="bits/test/bit.toml")
    return FakeBitRegistry(config=base)


def _events(srv, name):
    return [m for m in srv.broadcasts if m.get("event") == name]


def _errors(srv):
    return [m for _c, m in srv.sent if m.get("event") == "error"]


def test_load_bit_with_room_loads_the_room_then_the_bit_from_no_room():
    terrarium = _two_room_terrarium()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, registry=_testbit_registry(),
                         terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "room": "TEST"})

    agent.poll()

    assert _errors(srv) == []
    assert terrarium.state == TerrariumState.ROOM_READY
    assert terrarium.room.name == "TEST"
    assert terrarium.gs.state.name == "SETUP"
    assert _events(srv, "room_loaded") == [{"event": "room_loaded",
                                            "name": "TEST"}]


def test_load_bit_with_the_active_room_touches_no_room():
    terrarium = _two_room_terrarium()
    assert terrarium.load_room("TEST") is None
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, registry=_testbit_registry(),
                         terrarium=terrarium)
    srv.broadcasts.clear()
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "room": "TEST"})

    agent.poll()

    assert _errors(srv) == []
    assert _events(srv, "room_unloaded") == []
    assert _events(srv, "room_loaded") == []
    assert terrarium.gs.state.name == "SETUP"


def test_load_bit_without_room_keeps_the_active_room_as_before():
    terrarium = _two_room_terrarium()
    assert terrarium.load_room("DEMO") is None
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, registry=_testbit_registry(),
                         terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit"})

    agent.poll()

    assert _errors(srv) == []
    assert terrarium.room.name == "DEMO"
    assert terrarium.gs.state.name == "SETUP"


def test_load_bit_with_a_different_room_unloads_then_loads_then_loads_bit():
    terrarium = _two_room_terrarium()
    assert terrarium.load_room("TEST") is None
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, registry=_testbit_registry(),
                         terrarium=terrarium)
    srv.broadcasts.clear()
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "room": "DEMO"})

    agent.poll()

    assert _errors(srv) == []
    assert terrarium.room.name == "DEMO"
    assert terrarium.gs.state.name == "SETUP"
    lifecycle = [(m["event"], m["name"]) for m in srv.broadcasts
                 if m.get("event") in ("room_unloaded", "room_loaded")]
    assert lifecycle == [("room_unloaded", "TEST"), ("room_loaded", "DEMO")]


def test_load_bit_with_a_different_room_aborts_a_loaded_bit_first():
    terrarium = _two_room_terrarium()
    assert terrarium.load_room("TEST") is None
    registry = _testbit_registry()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, registry=registry,
                         terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "room": "TEST"})
    agent.poll()
    assert terrarium.gs.state.name == "SETUP"

    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "room": "DEMO"})
    agent.poll()

    assert _errors(srv) == []
    assert terrarium.room.name == "DEMO"
    assert terrarium.gs.state.name == "SETUP"
    assert terrarium.gs.bit_name == "TestBit"


def test_load_bit_refuses_an_unsupported_room_before_touching_the_room():
    terrarium = _two_room_terrarium()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv,
                         registry=_testbit_registry(room_types=["TEST"]),
                         terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "room": "DEMO"})

    agent.poll()

    assert _errors(srv) == [{"event": "error", "command": "load_bit",
                             "message": "Bit 'TestBit' does not support "
                                        "room 'DEMO'"}]
    assert terrarium.state == TerrariumState.NO_ROOM
    assert terrarium.gs.state.name == "IDLE"
    assert _events(srv, "room_loaded") == []


def test_load_bit_with_no_room_anywhere_is_still_refused():
    terrarium = _two_room_terrarium()
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, registry=_testbit_registry(),
                         terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit"})

    agent.poll()

    assert _errors(srv) == [{"event": "error", "command": "load_bit",
                             "message": "no room loaded"}]
    assert terrarium.gs.state.name == "IDLE"


def test_load_bit_room_load_refusal_stops_before_load_bit():
    terrarium = _two_room_terrarium(
        ownership_probe=lambda: "another Console owns this room")
    srv = FakeConsoleServer()
    agent = ConsoleAgent(terrarium.gs, srv, registry=_testbit_registry(),
                         terrarium=terrarium)
    srv.connect("c1")
    srv.deliver("c1", {"command": "load_bit", "name": "TestBit",
                       "room": "TEST"})

    agent.poll()

    assert _errors(srv) == [{"event": "error", "command": "load_bit",
                             "message": "another Console owns this room"}]
    assert terrarium.state == TerrariumState.NO_ROOM
    assert terrarium.gs.state.name == "IDLE"
    assert _events(srv, "room_load_failed") == [
        {"event": "room_load_failed", "name": "TEST",
         "reason": "another Console owns this room"}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py -q -k "load_bit_with or load_bit_refuses or load_bit_room or load_bit_without_room"`
Expected: the first, fourth, fifth, sixth, and eighth new tests FAIL (today a NO_ROOM load is refused with "no room loaded" and a different room is ignored); the "active room" and "without room" tests may already pass.

- [ ] **Step 3: Implement**

In `console/agent.py`, replace the `LoadBitCommand` branch (the lines from `if isinstance(command, protocol.LoadBitCommand):` through `self.game_server.load_bit(command.name, config=cfg)`) with:

```python
            if isinstance(command, protocol.LoadBitCommand):
                cfg = None
                if self.registry is not None:
                    try:
                        cfg = self.registry.resolve_config(
                            command.name, command.overrides)
                    except (ManifestError, KeyError) as exc:
                        return protocol.error_event(name, str(exc))
                if self.terrarium is not None:
                    reason = self._ensure_room_for_bit(command, cfg)
                    if reason is not None:
                        return protocol.error_event(name, reason)
                if cfg is None:
                    self.game_server.load_bit(command.name)
                else:
                    self.game_server.load_bit(command.name, config=cfg)
```

Then add this method right after `_load_room`:

```python
    def _ensure_room_for_bit(self, command, cfg) -> str | None:
        """Spec 2026-09-10 section 4: bring the Terrarium to the Room a
        load_bit asks for, or leave the active one alone. Returns a
        refusal reason (None on success). Order matters: the support check
        runs BEFORE any Room is touched, so an unsupported request never
        costs an Arco restart. A different target than the active Room
        aborts any loaded Bit, unloads (force), then loads; every step's
        refusal stops the sequence with no load_bit afterwards."""
        terrarium = self.terrarium
        active = (terrarium.room.name
                  if terrarium.state is TerrariumState.ROOM_READY else None)
        target = command.room if command.room is not None else active
        if target is None:
            return "no room loaded"
        if cfg is not None and target not in cfg.launch.room_types:
            return f"Bit {command.name!r} does not support room {target!r}"
        if target == active:
            return None
        if terrarium.state not in (TerrariumState.NO_ROOM,
                                   TerrariumState.ROOM_READY):
            return (f"room is {terrarium.state.name.lower()}; try again "
                    f"once it settles")
        gs = self.game_server
        if gs.state is not State.IDLE:
            gs.abort()
        if terrarium.state is TerrariumState.ROOM_READY:
            reason = terrarium.unload_room(force=True)
            if reason is not None:
                return reason
        return self._load_room(target)
```

`State` and `TerrariumState` are already imported at the top of `console/agent.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py -q`
Expected: all pass, including every pre-existing load_bit / load_room test.

- [ ] **Step 5: Commit**

```bash
git add console/agent.py tests/test_console_agent.py
git commit -m "feat(console): load_bit takes a room and switches the Terrarium to it before loading"
```

---

### Task 4: `ConsoleAgent` join provider, `join` in the snapshot, `join_changed` on LOADED and IDLE

**Files:**
- Modify: `console/agent.py:40-45` (constructor signature), the `self._canvas_urls = canvas_urls` block, `snapshot()` (line ~515), `on_state_change` (line ~745)
- Test: `tests/test_console_agent.py`

**Interfaces:**
- Consumes: `console.protocol.snapshot_event(join=...)`, `console.protocol.join_changed_event` (Task 2).
- Produces: `ConsoleAgent(..., join_info=None)` where `join_info` is `Callable[[], dict | None]`; the snapshot's `"join"` is that callable's result (None when no provider); a `join_changed` event is broadcast whenever the engine enters `LOADED` or `IDLE`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_console_agent.py`:

```python
def test_snapshot_join_is_none_without_a_provider():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["join"] is None


def test_snapshot_join_comes_from_the_injected_provider():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    join = {"www_url": "http://10.0.0.7:8788/app/", "nodes": []}
    agent = ConsoleAgent(gs, srv, join_info=lambda: join)
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["join"] == join


def test_join_changed_is_broadcast_on_loaded_and_on_idle():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    calls = []

    def provider():
        calls.append(gs.bit_name)
        return {"bit": gs.bit_name, "nodes": []}

    agent = ConsoleAgent(gs, srv, join_info=provider)
    gs.load_bit("TestBit")
    joins = [m for m in srv.broadcasts if m.get("event") == "join_changed"]
    assert joins == [{"event": "join_changed",
                      "join": {"bit": "TestBit", "nodes": []}}]
    gs.abort()
    joins = [m for m in srv.broadcasts if m.get("event") == "join_changed"]
    assert joins[-1] == {"event": "join_changed",
                         "join": {"bit": None, "nodes": []}}
    assert len(joins) == 2


def test_no_join_changed_without_a_provider():
    gs = GameServer({"TestBit": TestBit})
    srv = FakeConsoleServer()
    ConsoleAgent(gs, srv)
    gs.load_bit("TestBit")
    gs.abort()
    assert [m for m in srv.broadcasts if m.get("event") == "join_changed"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py -q -k join`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'join_info'` and `KeyError: 'join'`

- [ ] **Step 3: Implement**

In `console/agent.py`:

1. Add `join_info=None` as the last keyword parameter of `ConsoleAgent.__init__` (after `rooms_root=None`).
2. Right after the `self._canvas_urls = canvas_urls` line, add:

```python
        # Optional Callable[[], dict | None] building the Join card's read
        # model (control/join_info.py's build_join_info), from
        # harness/terrarium_boot.py. None (every embedding without a guest
        # page) yields join=None in the snapshot and no join_changed
        # events. Called at snapshot time and on every LOADED / IDLE
        # transition, so it always reads the Bit that is loaded NOW.
        self._join_info = join_info
```

3. In `snapshot()`, add `join=self._join_view(),` to the `protocol.snapshot_event(...)` call (after `design_vocab={...},`), and add this method next to `_rooms_view`:

```python
    def _join_view(self) -> dict | None:
        return self._join_info() if self._join_info is not None else None
```

4. Replace `on_state_change` with:

```python
    def on_state_change(self, old_state: State, new_state: State) -> None:
        terrarium_state = (
            self.terrarium.state.name if self.terrarium is not None else None)
        self.server.broadcast(protocol.state_changed_event(
            new_state.name, self.game_server.bit_name,
            terrarium_state=terrarium_state))
        if new_state == State.UNLOADING:
            self._broadcast_bit_completed()
        # The Join card reads the loaded Bit's nodes: it changes exactly
        # when a Bit becomes loaded and when the engine returns to IDLE.
        if (new_state in (State.LOADED, State.IDLE)
                and self._join_info is not None):
            self.server.broadcast(protocol.join_changed_event(self._join_info()))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py tests/test_console_protocol.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add console/agent.py tests/test_console_agent.py
git commit -m "feat(console): join provider in the snapshot and join_changed on LOADED and IDLE"
```

---

### Task 5: `terrarium_boot --no-bit` and the `CONTROL_NO_ROOM_WAIT` marker

**Files:**
- Modify: `harness/markers.py` (new constant, added to `READY_MARKERS`)
- Modify: `control/boot_config.py:15-17` (`bit_name: str | None`)
- Modify: `harness/terrarium_boot.py`: `_wait_for_room_ready` (line ~754), `build()`'s load_bit block (line ~308-335), `_effective_serve` (line ~1107), `_build_arg_parser` (add `--no-bit` next to `--bit`, line ~1252), `main()` (bit resolution at line ~1322-1343 and the `if room_spec is not None:` branches at lines ~1527 and ~1594)
- Test: `tests/test_markers.py`, `tests/test_terrarium_boot.py`

**Interfaces:**
- Produces: `markers.CONTROL_NO_ROOM_WAIT == "NO_ROOM: waiting for the Console to load a Room"`, in `READY_MARKERS`.
- Produces: `BootConfig(bit_name=None)` means "load no Bit"; `build()` then leaves the engine IDLE after loading the Room.
- Produces: `terrarium_boot --no-bit` (refused with exit 2 alongside `--bit`/`--profile`, exit 1 without `--console-port`); `_effective_serve(args)` is True whenever `args.no_bit`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_markers.py`:

```python
def test_no_room_wait_marker_is_a_ready_marker_emitted_by_terrarium_boot():
    assert markers.CONTROL_NO_ROOM_WAIT == \
        "NO_ROOM: waiting for the Console to load a Room"
    assert markers.READY_MARKERS["CONTROL_NO_ROOM_WAIT"] is \
        markers.CONTROL_NO_ROOM_WAIT
    import harness.terrarium_boot
    assert "markers.CONTROL_NO_ROOM_WAIT" in inspect.getsource(
        harness.terrarium_boot)
```

Append to `tests/test_terrarium_boot.py`:

```python
def test_build_with_no_bit_loads_the_room_and_leaves_the_engine_idle():
    """--no-bit (spec 2026-09-10 section 3): a BootConfig with bit_name
    None loads the Room exactly as before and skips load_bit."""
    config = BootConfig(room_name="TEST", bit_name=None)
    gs, server, agent, arco, teardown, terrarium = _build_with_fakes(config)
    try:
        assert terrarium.state == TerrariumState.ROOM_READY
        assert gs.state is State.IDLE
        assert gs.bit_name is None
    finally:
        teardown.close()


def test_no_bit_is_refused_without_a_console(monkeypatch, capsys):
    import harness.terrarium_boot as terrarium_boot_module
    _mock_o2lite_module(monkeypatch, terrarium_boot_module)
    monkeypatch.setattr(sys, "argv", ["terrarium_boot.py", "--no-bit"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "nothing would ever load a Bit" in capsys.readouterr().err


def test_no_bit_is_refused_together_with_bit_or_profile(monkeypatch):
    import harness.terrarium_boot as terrarium_boot_module
    _mock_o2lite_module(monkeypatch, terrarium_boot_module)
    for extra in (["--bit", "TestBit"],
                  ["--profile", "profiles/dev-metronome.toml"]):
        monkeypatch.setattr(sys, "argv", ["terrarium_boot.py", "--no-bit",
                                          "--console-port", "0"] + extra)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2


def test_no_bit_with_a_console_builds_a_bitless_boot_config(monkeypatch):
    captured = _run_main_capturing_build(
        monkeypatch, ["--no-bit", "--console-port", "0", "--room", "TEST"])
    assert captured["config"].bit_name is None
    assert captured["config"].bit_config is None
    assert captured["config"].room_name == "TEST"


def test_no_bit_without_a_room_builds_a_no_room_boot_config(monkeypatch):
    captured = _run_main_capturing_build(
        monkeypatch, ["--no-bit", "--console-port", "0"])
    assert captured["config"].bit_name is None
    assert captured["config"].room_name is None


def test_no_bit_forces_serve_mode():
    from harness.terrarium_boot import _build_arg_parser, _effective_serve
    ap = _build_arg_parser()
    args = ap.parse_args(["--no-bit", "--console-port", "0", "--hold"])
    assert _effective_serve(args) is True


def test_wait_for_room_ready_prints_the_no_room_marker_once(capsys):
    from harness import markers
    from harness.terrarium_boot import _wait_for_room_ready

    class FlippingTerrarium:
        def __init__(self):
            self.polls = 0
            self.state = TerrariumState.NO_ROOM

    class Agent:
        def __init__(self, terrarium):
            self._t = terrarium

        def poll(self):
            self._t.polls += 1
            if self._t.polls >= 2:
                self._t.state = TerrariumState.ROOM_READY

    terrarium = FlippingTerrarium()
    reason = _wait_for_room_ready(Agent(terrarium), terrarium,
                                  sleep=lambda _s: None)
    assert reason == "ready"
    out = capsys.readouterr().out
    assert out.count(markers.CONTROL_NO_ROOM_WAIT) == 1


def test_wait_for_room_ready_prints_nothing_when_already_ready(capsys):
    from harness import markers
    from harness.terrarium_boot import _wait_for_room_ready

    class Ready:
        state = TerrariumState.ROOM_READY

    class Agent:
        def poll(self):
            raise AssertionError("must not poll when already ready")

    assert _wait_for_room_ready(Agent(), Ready()) == "ready"
    assert markers.CONTROL_NO_ROOM_WAIT not in capsys.readouterr().out
```

(`TerrariumState`, `State`, `BootConfig`, `sys`, `pytest`, `main` are already imported at the top of `tests/test_terrarium_boot.py`; check with `grep -n "^from\|^import" tests/test_terrarium_boot.py` and add any that are missing.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_markers.py tests/test_terrarium_boot.py -q -k "no_bit or no_room"`
Expected: FAIL (`AttributeError: CONTROL_NO_ROOM_WAIT`, `--no-bit` unrecognized, `bit_name=None` reaching `bit_registry.get(None)`)

- [ ] **Step 3: Implement the marker and BootConfig**

In `harness/markers.py`, after the `CONTROL_ROOM_UNLOADED` constant, add:

```python
# Booted (or returned) to NO_ROOM with a Console and nothing to load --
# printed once on entry to the NO_ROOM wait. run_stack --no-bit with no
# --room gates on this: no Arco exists yet in that mode, so it is the only
# thing that says Control is up and waiting.
CONTROL_NO_ROOM_WAIT = "NO_ROOM: waiting for the Console to load a Room"
```

and add `"CONTROL_NO_ROOM_WAIT": CONTROL_NO_ROOM_WAIT,` to `READY_MARKERS` after the `CONTROL_ROOM_UNLOADED` entry.

In `control/boot_config.py`, change `bit_name: str` to:

```python
    # None means "load no Bit": build() loads the Room (if any) and leaves
    # the engine IDLE for the Console to load one (terrarium_boot --no-bit).
    bit_name: str | None
```

- [ ] **Step 4: Implement in `terrarium_boot.py`**

(a) `_wait_for_room_ready`: after the `if terrarium.state is TerrariumState.ROOM_READY: return "ready"` early return and before the `while True:` loop, add:

```python
    print(markers.CONTROL_NO_ROOM_WAIT, flush=True)
```

(b) `build()`: guard the load_bit block on `config.bit_name`. Replace the whole `if room_spec is not None:` block (from `reason = terrarium.load_room(room_spec.name)` through the last `raise TerrariumBuildFailure(...)` of its `except BaseException:` clause) with:

```python
    if room_spec is not None:
        reason = terrarium.load_room(room_spec.name)
        if reason is not None:
            teardown.close()
            raise TerrariumBuildFailure(reason)
        # Room and Bit loaded together, exactly like the old boot() always
        # did -- every existing build() caller expects gs.state to already
        # be SETUP (via GameServer.load_bit) the instant build() returns
        # with a room_spec. A NO_ROOM build (room_spec is None) loads no
        # Bit either: main() defers that to whenever a Room actually
        # exists (see harness/terrarium_boot.py's main()). A bit_name of
        # None (terrarium_boot --no-bit) loads the Room only and leaves
        # the engine IDLE for the Console to load a Bit into.
        if config.bit_name is not None:
            try:
                bit_cls = bit_registry.get(config.bit_name)
                if bit_cls is None:
                    raise TerrariumBuildFailure(
                        f"unknown Bit {config.bit_name!r}")
                if terrarium.room.name not in bit_cls.room_types:
                    raise TerrariumBuildFailure(
                        f"Bit {config.bit_name!r} does not support "
                        f"{terrarium.room.name}")
                gs.load_bit(config.bit_name, config=config.bit_config)
            except BitLoadError as exc:
                if terrarium.state is TerrariumState.ROOM_READY:
                    terrarium.unload_room(force=True)
                teardown.close()
                raise TerrariumBuildFailure(f"Bit load failed: {exc}") from exc
            except BaseException:
                if terrarium.state is TerrariumState.ROOM_READY:
                    terrarium.unload_room(force=True)
                teardown.close()
                raise
```

Before pasting, read the current `except BaseException:` clause in the file (it may end with a bare `raise` or a wrapped re-raise) and keep its existing body verbatim inside the guarded block; only the `if config.bit_name is not None:` guard and the comment sentence are new.

The cold `_serve_rounds` entry the spec's risk section names is already pinned: `tests/test_terrarium_boot.py::test_serve_rounds_cycles_idle_load_run_idle` starts from IDLE and waits for a Console load, which is exactly the `--no-bit --room` path.

(c) `_effective_serve`: change the return to

```python
    return bool(args.serve or getattr(args, "no_bit", False)
                or (args.console_port is not None
                    and args.seconds is None and not args.hold))
```

and add to its docstring: `--no-bit always serves: with no round-1 Bit there is nothing but rounds to run.`

(d) `_build_arg_parser`: right after the `--bit` argument add:

```python
    ap.add_argument("--no-bit", action="store_true",
                    help="Load no Bit at all: boot the Room given by "
                         "--room (or to NO_ROOM without one) and wait for "
                         "the Console to load a Bit. Requires "
                         "--console-port; refused together with --bit or "
                         "--profile. Implies --serve. This is what "
                         "./terrarium.sh runs.")
```

(e) `main()`: replace the block from `profile = RunProfile()` through `cfg = registry.resolve_config(bit, overrides or None)` with:

```python
    if args.no_bit and (args.bit is not None or args.profile is not None):
        ap.error("--no-bit cannot be combined with --bit or --profile")
    if args.no_bit and args.console_port is None:
        print("--no-bit given with no --console-port to load a Bit from; "
              "nothing would ever load a Bit", file=sys.stderr)
        sys.exit(1)

    profile = RunProfile()
    if args.profile is not None:
        with open(args.profile, encoding="utf-8") as handle:
            profile = parse_profile(handle.read(), source=args.profile)

    # manifest < profile < explicit CLI, applied once here -- the same
    # precedence harness/run_stack.py's config_from_args applies for its
    # own launcher fields. --no-bit short-circuits all of it: no Bit is
    # resolved, so nothing below can refuse one.
    bit = None if args.no_bit else (args.bit or profile.bit or "TestBit")

    if bit is not None and bit not in registry.packages:
        available = sorted(registry.packages)
        print(f"unknown Bit {bit!r}; available: {available}",
              file=sys.stderr)
        for err in registry.errors_view():
            print(f"error: {err['path']}: {err['message']}", file=sys.stderr)
        sys.exit(1)

    if bit is not None and not registry.packages[bit].config.identity.enabled:
        print(f"Bit {bit!r} is disabled (bit.enabled = false in its "
              f"manifest); re-enable it there to load it.", file=sys.stderr)
        sys.exit(1)

    # harness/run_stack.py stops this process with SIGTERM, and the whole
    # ordered teardown below lives in a finally that a bare SIGTERM skips.
    sigterm_as_keyboard_interrupt()

    # Collect ONLY explicitly-given CLI values into the overrides dict --
    # anything left at its argparse None default falls through to the
    # selected Bit's manifest (or, absent an override, whatever that
    # manifest itself already defaulted to). See control/bit_config.py's
    # merge_overrides for the shape this dict must take.
    overrides: dict = {}
    launch_overrides: dict = {}
    if args.setup_seconds is not None:
        launch_overrides["setup_seconds"] = args.setup_seconds
    if launch_overrides:
        overrides["launch"] = launch_overrides
    run_duration = _run_duration(args)
    if run_duration is None:
        run_duration = profile.seconds
    if run_duration is not None:
        overrides["defaults"] = {"run_duration_seconds": run_duration}
    overrides = deep_merge_overrides(profile.overrides, overrides)
    cfg = (None if bit is None
           else registry.resolve_config(bit, overrides or None))
```

`ap` is the parser `main()` built at its top (`ap = _build_arg_parser()`), so `ap.error` exits 2 the argparse way.

(f) `main()`, the two round-1 branches. Change the marker block

```python
    if room_spec is not None:
        print(f"{markers.CONTROL_ROOM_LOADED} {terrarium.room.name}",
             flush=True)
        ...
        if effective_serve:
            print(f"{markers.CONTROL_ROUND_LOADED} {gs.bit_name}", flush=True)
```

to guard the round line: `if effective_serve and gs.bit_name is not None:`.

Then change the big `if room_spec is not None:` that starts with `round1_bit_name = gs.bit_name` to `if room_spec is not None and gs.bit_name is not None:` and update the `else:` branch's comment to:

```python
        else:
            # NO_ROOM boot (no --room, a console port instead) OR a --no-bit
            # boot with a Room already loaded: wait for the Console to load
            # a Room (immediate when one is up), then serve rounds against
            # it -- _wait_for_load sits in IDLE until the Console loads a
            # Bit -- looping back to the NO_ROOM wait whenever the room is
            # unloaded mid-serve (see _serve_roomless).
```

The `else` body itself is unchanged: `_serve_roomless` already returns "ready" immediately from `_wait_for_room_ready` when the Room is up, and `_serve_rounds` starts in `_wait_for_load`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_markers.py tests/test_terrarium_boot.py tests/test_boot_config.py tests/test_terrarium.py -q`
Expected: all pass (if `tests/test_boot_config.py` does not exist, drop it from the command)

- [ ] **Step 6: Commit**

```bash
git add harness/markers.py control/boot_config.py harness/terrarium_boot.py tests/test_markers.py tests/test_terrarium_boot.py
git commit -m "feat(harness): terrarium_boot --no-bit boots a Room (or NO_ROOM) and waits for the Console"
```

---

### Task 6: `terrarium_boot` wires the join provider into the Console and prints `JOIN_URL:` lines

**Files:**
- Modify: `harness/markers.py` (new `JOIN_URL` constant, outside both dicts, next to `WWW_URL`)
- Modify: `harness/terrarium_boot.py`: new `_join_info_provider`, new `_JoinLogger` observer, `_print_join_urls` helper; `main()`'s `ConsoleAgent(...)` call (line ~1564) and the observer registration after `gs.add_observer(_LifecycleLogger(gs))` (line ~1511)
- Test: `tests/test_markers.py`, `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: `control.join_info.build_join_info` (Task 1), `ConsoleAgent(join_info=...)` (Task 4).
- Produces: `markers.JOIN_URL == "JOIN_URL:"`; `_join_info_provider(gs, *, ensemble, www_port, ip=lan_ip, app_root=<www dir>) -> Callable[[], dict | None]` (returns None when `www_port == 0`); `_JoinLogger(provider)` prints `JOIN_URL: <role> <node> <url>` per node on every `LOADED`; `_print_join_urls(provider)` prints the same for an already-loaded round-1 Bit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_markers.py`:

```python
def test_join_url_marker_value_and_emit_site():
    assert markers.JOIN_URL == "JOIN_URL:"
    import harness.terrarium_boot
    assert "markers.JOIN_URL" in inspect.getsource(harness.terrarium_boot)
```

Append to `tests/test_terrarium_boot.py`:

```python
def test_join_info_provider_reads_the_loaded_bits_nodes(tmp_path):
    from harness.terrarium_boot import _join_info_provider
    from control.bit_registry import BitRegistry

    registry = BitRegistry.scan(["bits"])
    gs = GameServer({"TestBit": TestBit})
    provider = _join_info_provider(gs, ensemble="arco", www_port=8788,
                                   ip=lambda: "10.0.0.7",
                                   app_root=str(tmp_path))

    info = provider()
    assert info["bit"] is None
    assert info["nodes"] == []
    assert info["www_url"] == "http://10.0.0.7:8788/app/"
    assert info["app_present"] is False

    gs.load_bit("TestBit", config=registry.resolve_config("TestBit"))
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "index.html").write_text("<html></html>")
    info = provider()
    assert info["bit"] == "TestBit"
    assert [(r["role"], r["node"]) for r in info["nodes"]] == [
        ("player", "TEST_PLAYER_NODE"), ("jammer", "TEST_JAM_NODE")]
    assert info["app_present"] is True
    assert info["nodes"][0]["url"].startswith("http://10.0.0.7:8788/app/?")


def test_join_info_provider_is_none_when_the_guest_page_is_off():
    from harness.terrarium_boot import _join_info_provider
    gs = GameServer({"TestBit": TestBit})
    provider = _join_info_provider(gs, ensemble="arco", www_port=0,
                                   ip=lambda: "10.0.0.7", app_root="/nowhere")
    assert provider() is None


def test_join_logger_prints_one_join_url_line_per_node_on_loaded(capsys):
    from harness import markers
    from harness.terrarium_boot import _JoinLogger

    info = {"nodes": [
        {"role": "player", "node": "N1", "url": "http://h:8788/app/?node=N1"},
        {"role": "jammer", "node": "N2", "url": "http://h:8788/app/?node=N2"},
    ]}
    gs = GameServer({"TestBit": TestBit})
    gs.add_observer(_JoinLogger(lambda: info))
    gs.load_bit("TestBit")
    out = capsys.readouterr().out
    assert f"{markers.JOIN_URL} player N1 http://h:8788/app/?node=N1" in out
    assert f"{markers.JOIN_URL} jammer N2 http://h:8788/app/?node=N2" in out
    assert out.count(markers.JOIN_URL) == 2
    gs.abort()
    assert markers.JOIN_URL not in capsys.readouterr().out


def test_join_logger_is_silent_when_the_provider_returns_none(capsys):
    from harness import markers
    from harness.terrarium_boot import _JoinLogger
    gs = GameServer({"TestBit": TestBit})
    gs.add_observer(_JoinLogger(lambda: None))
    gs.load_bit("TestBit")
    assert markers.JOIN_URL not in capsys.readouterr().out
```

(`GameServer` and `TestBit` are already imported in `tests/test_terrarium_boot.py`; verify with grep and add if not.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_markers.py tests/test_terrarium_boot.py -q -k join`
Expected: FAIL with `AttributeError` / `ImportError` for `JOIN_URL`, `_join_info_provider`, `_JoinLogger`

- [ ] **Step 3: Implement**

In `harness/markers.py`, after `WWW_URL`, add:

```python
# One line per registration node of the Bit that just loaded:
# "JOIN_URL: <role> <node> <url>". The same rows the Console's Join card
# shows, for a headless box. Echoed by run_stack, never waited on (a
# variable count per load, like BROWSE_URL).
JOIN_URL = "JOIN_URL:"
```

In `harness/terrarium_boot.py`, add near the other module-level imports `from control.join_info import build_join_info`, and add these three definitions right after the `_LifecycleLogger` class:

```python
def _join_info_provider(gs, *, ensemble: str, www_port: int, ip=lan_ip,
                        app_root: str | None = None):
    """The Console's Join card provider (console.agent.ConsoleAgent
    join_info=) and _JoinLogger's source: control/join_info.py's
    build_join_info over the LAN address, the two ports, the ensemble and
    the loaded Bit's launch.nodes, read fresh on every call. Returns None
    when the guest page is off (--www-port 0): there is nothing to join
    through. `app_root` is the www/ directory; app_present is whether a
    Flutter build sits under its app/."""
    root = app_root if app_root is not None else os.path.join(REPO_ROOT, "www")

    def provider():
        if www_port == 0:
            return None
        cfg = getattr(gs.bit, "config", None)
        nodes = tuple(cfg.launch.nodes) if cfg is not None else ()
        return build_join_info(
            lan_ip=ip(), www_port=www_port, arco_http_port=ARCO_HTTP_PORT,
            ensemble=ensemble, bit_name=gs.bit_name, nodes=nodes,
            app_present=os.path.isfile(os.path.join(root, "app", "index.html")))

    return provider


def _print_join_urls(provider) -> None:
    """One markers.JOIN_URL line per node of the loaded Bit; silent when
    the provider yields nothing (guest page off, or no Bit)."""
    info = provider()
    if not info:
        return
    for row in info["nodes"]:
        print(f"{markers.JOIN_URL} {row['role']} {row['node']} {row['url']}",
              flush=True)


class _JoinLogger:
    """GameServer observer: prints the JOIN_URL lines whenever a Bit reaches
    LOADED (a Console load in serve mode). Round 1's CLI-selected Bit loads
    inside build(), before any observer exists, so main() calls
    _print_join_urls once for it explicitly, like CONTROL_ROOM_LOADED."""

    def __init__(self, provider):
        self._provider = provider

    def on_state_change(self, old_state, new_state) -> None:
        if new_state is State.LOADED:
            _print_join_urls(self._provider)
```

In `main()`:

1. Right before `gs.add_observer(_LifecycleLogger(gs))`, add:

```python
    join_info = _join_info_provider(gs, ensemble=config.o2_ensemble,
                                    www_port=args.www_port)
    gs.add_observer(_JoinLogger(join_info))
    # Round 1's Bit (if any) loaded inside build(), before the observer
    # above existed; announce its join rows once here.
    _print_join_urls(join_info)
```

2. Add `join_info=join_info,` to the `ConsoleAgent(gs, console_server, ...)` call (after `captures_root=Path("captures")`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_markers.py tests/test_terrarium_boot.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add harness/markers.py harness/terrarium_boot.py tests/test_markers.py tests/test_terrarium_boot.py
git commit -m "feat(harness): Console join provider and JOIN_URL stdout lines per node on Bit load"
```

---

### Task 7: `run_stack --no-bit`

**Files:**
- Modify: `harness/run_stack.py`: `StackConfig` (lines 84-108), `control_command` (lines 131-172), `run()`'s stage table (lines 301-318), `parse_args` (lines 553-660), `config_from_args` (lines 663-735)
- Test: `tests/test_run_stack.py`

**Interfaces:**
- Consumes: `terrarium_boot --no-bit` (Task 5), `markers.CONTROL_NO_ROOM_WAIT` (Task 5).
- Produces: `StackConfig.bit: str | None`, `StackConfig.room_type: str | None`, `StackConfig.no_bit: bool = False`; `_control_stages(cfg) -> tuple[tuple[str, str, str], ...]`; `run_stack --no-bit` CLI.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_stack.py`:

```python
def test_no_bit_refuses_bit_profile_node_and_devices():
    from harness.run_stack import parse_args
    for extra in (["--bit", "TestBit"],
                  ["--profile", "profiles/dev-metronome.toml"],
                  ["--node", "X"],
                  ["--devices", "1"]):
        with pytest.raises(SystemExit):
            parse_args(["--no-bit"] + extra)


def test_no_bit_config_has_no_bit_no_node_no_devices_and_no_room_by_default():
    from harness.run_stack import config_from_args, parse_args
    cfg = config_from_args(parse_args(["--no-bit"]))
    assert cfg.no_bit is True
    assert cfg.bit is None
    assert cfg.node is None
    assert cfg.devices == 0
    assert cfg.room_type is None


def test_no_bit_config_forwards_room_and_console_port():
    from harness.run_stack import config_from_args, parse_args
    cfg = config_from_args(parse_args(
        ["--no-bit", "--room", "DEMO", "--console-port", "8772"]))
    assert cfg.room_type == "DEMO"
    assert cfg.console_port == 8772
    assert cfg.serve is True


def test_no_bit_ci_is_bounded():
    from harness.run_stack import config_from_args, parse_args
    cfg = config_from_args(parse_args(["--no-bit", "--ci"]))
    assert cfg.seconds is not None
    assert cfg.echo is False


def test_control_command_under_no_bit_omits_bit_and_forwards_the_flag(tmp_path):
    cmd = control_command(_cfg(tmp_path, no_bit=True, bit=None,
                               room_type=None), 1)
    assert "--no-bit" in cmd
    assert "--bit" not in cmd
    assert "--room" not in cmd


def test_control_command_under_no_bit_with_a_room_forwards_it(tmp_path):
    cmd = control_command(_cfg(tmp_path, no_bit=True, bit=None,
                               room_type="DEMO"), 1)
    assert cmd[cmd.index("--room") + 1] == "DEMO"
    assert "--bit" not in cmd


def test_control_stages_by_mode(tmp_path):
    from harness.run_stack import _control_stages
    names = lambda cfg: [s[0] for s in _control_stages(cfg)]
    assert names(_cfg(tmp_path)) == [
        "control-room-loaded", "control-ready", "control-setup"]
    assert names(_cfg(tmp_path, no_bit=True, bit=None, room_type="TEST")) == [
        "control-room-loaded", "control-ready"]
    assert names(_cfg(tmp_path, no_bit=True, bit=None, room_type=None)) == [
        "control-no-room-wait"]


def test_a_no_bit_no_room_run_completes_on_the_no_room_marker(tmp_path):
    popen = ScriptedPopen([f"{markers.CONTROL_NO_ROOM_WAIT}\n"])
    result = run(_cfg(tmp_path, no_bit=True, bit=None, room_type=None,
                      devices=0), popen=popen, sleep=lambda _s: None)
    assert result.ok is True
    assert result.stage == "complete"
    assert len(popen.children) == 1     # Control only, no devices


def test_a_no_bit_room_run_completes_without_a_setup_hold(tmp_path):
    popen = ScriptedPopen([f"{markers.CONTROL_ROOM_LOADED} DEMO\n"
                           f"{markers.CONTROL_TRANSPORT_READY} 'arco'\n"])
    result = run(_cfg(tmp_path, no_bit=True, bit=None, room_type="DEMO",
                      devices=0), popen=popen, sleep=lambda _s: None)
    assert result.ok is True
    assert result.stage == "complete"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_run_stack.py -q -k "no_bit or control_stages"`
Expected: FAIL (`--no-bit` unrecognized, `no_bit` unexpected kwarg, `_control_stages` missing)

- [ ] **Step 3: Implement**

In `StackConfig`, change and add:

```python
    room_type: str | None = "TEST"    # None = boot to NO_ROOM (--no-bit only)
    config: str | None = None         # forwarded to terrarium_boot verbatim
    bit: str | None = "TestBit"       # None = --no-bit
    no_bit: bool = False              # forward --no-bit; no devices, no node
```

In `control_command`, replace

```python
    command += ["--room", cfg.room_type]
    command += ["--bit", cfg.bit]
```

with

```python
    if cfg.room_type is not None:
        command += ["--room", cfg.room_type]
    if cfg.no_bit:
        command += ["--no-bit"]
    elif cfg.bit is not None:
        command += ["--bit", cfg.bit]
```

Add this function right above `run()`:

```python
def _control_stages(cfg: StackConfig) -> tuple[tuple[str, str, str], ...]:
    """The Control readiness gates run() waits on, in order, by mode.

    A Bit run gates on the Room, the transport and the SETUP hold. A
    --no-bit run with a Room has no SETUP (nothing is loaded), and a
    --no-bit run without a Room has no Arco at all yet, so the only
    evidence Control is up is its NO_ROOM wait line."""
    room_loaded = (
        "control-room-loaded", markers.CONTROL_ROOM_LOADED,
        "Control never reported its Room loaded. Check arco.log for "
        "a failed Arco start, and control.log for a load_room refusal.")
    ready = (
        "control-ready", markers.CONTROL_TRANSPORT_READY,
        "Control never reported its o2lite transport up. Check "
        "arco.log for a failed Arco start, and o2debug.log.")
    setup = (
        "control-setup", markers.CONTROL_SETUP_HOLD,
        "Control came up but never opened registration.")
    if cfg.no_bit and cfg.room_type is None:
        return ((
            "control-no-room-wait", markers.CONTROL_NO_ROOM_WAIT,
            "Control never reached its NO_ROOM wait. Check control.log for "
            "a failed Console or guest-page start."),)
    if cfg.no_bit:
        return (room_loaded, ready)
    return (room_loaded, ready, setup)
```

In `run()`, replace the inline `for stage, marker, detail in ( ... ):` tuple literal with `for stage, marker, detail in _control_stages(cfg):` (delete the three inline tuples and their comment; they now live in `_control_stages`).

In `parse_args`, after the `--bit` argument add:

```python
    ap.add_argument("--no-bit", action="store_true",
                    help="Stand up a clean Terrarium: no Bit, no spawned "
                         "devices, --room optional (NO_ROOM without it). "
                         "The Console loads Rooms and Bits. Refused with "
                         "--bit, --profile, --node or --devices N>0. This "
                         "is what ./terrarium.sh runs.")
```

and, in the validation block after `args = ap.parse_args(argv)`, add:

```python
    if args.no_bit and (args.bit is not None or args.profile is not None
                        or args.node is not None):
        ap.error("--no-bit loads no Bit, so --bit, --profile and --node "
                 "have nothing to apply to")
    if args.no_bit and args.devices:
        ap.error("--no-bit spawns no devices: with no Bit there is no node "
                 "for a Testshroom to join")
```

In `config_from_args`, make the very first statement:

```python
    if args.no_bit:
        return _bitless_config(args)
```

and add this function right above `config_from_args`:

```python
def _bitless_config(args) -> StackConfig:
    """config_from_args for --no-bit: no registry, no Bit manifest, no
    profile, no node, zero devices. Everything else mirrors the Bit path
    (log dir, CI bound, --open implying an ephemeral Console, a Console
    implying serve)."""
    log_dir = args.log_dir or os.path.join(
        "runs", time.strftime("%Y%m%d-%H%M%S"))
    seconds = args.seconds
    if seconds is None and args.ci:
        seconds = CI_DEFAULT_SECONDS
    console_port = args.console_port
    if args.open and console_port is None:
        console_port = 0
    serve = args.serve or (console_port is not None and not args.ci)
    return StackConfig(
        log_dir=log_dir, arco_command=args.arco_command,
        devices=0, ensemble=args.ensemble,
        setup_seconds=args.setup_seconds, seconds=seconds,
        horizon=args.horizon, echo=not args.ci,
        console_port=console_port, room_type=args.room, config=args.config,
        bit=None, no_bit=True, node=None,
        open_urls=args.open, serve=serve,
        persist_shrooms=args.persist_shrooms,
        www_port=args.www_port, web_build=args.web_build)
```

`CI_DEFAULT_SECONDS` is defined later in the module (line ~538); Python resolves it at call time, so the reference is fine.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_run_stack.py -q`
Expected: all pass, including the pre-existing control_command / config_from_args tests

- [ ] **Step 5: Commit**

```bash
git add harness/run_stack.py tests/test_run_stack.py
git commit -m "feat(harness): run_stack --no-bit stands up a Terrarium with no Bit and mode-specific readiness gates"
```

---

### Task 8: `./terrarium.sh` and the README quick start

**Files:**
- Create: `terrarium.sh` (executable)
- Modify: `README.md` (the Room-panel paragraph around lines 62-72)
- Test: `tests/test_terrarium_sh.py`

**Interfaces:**
- Consumes: `run_stack --no-bit` (Task 7).
- Produces: `./terrarium.sh [--room NAME] [run_stack flags]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_terrarium_sh.py`:

```python
"""./terrarium.sh: the clean-standup wrapper. Structural checks only; the
script execs run_stack, which needs o2litepy and an Arco checkout."""
import os
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "terrarium.sh"


def test_the_wrapper_exists_and_is_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


def test_the_wrapper_runs_run_stack_bitless_in_serve_mode_on_8772():
    text = SCRIPT.read_text()
    assert ".venv/bin/python -m harness.run_stack" in text
    assert "--no-bit" in text
    assert "--serve" in text
    assert "--devices 0" in text
    assert "--console-port 8772" in text
    assert text.rstrip().endswith('"$@"'), \
        "user flags must come last so they override the defaults"
    assert "PYTHONPATH=" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_terrarium_sh.py -q`
Expected: FAIL (`terrarium.sh` missing)

- [ ] **Step 3: Create the wrapper**

Create `terrarium.sh`:

```bash
#!/usr/bin/env bash
# Clean Terrarium standup: Arco stack supervisor, Console on 8772, guest
# page on 8788, NO Bit and NO spawned Testshrooms. Without --room it boots
# to NO_ROOM and the Console loads a Room; with --room NAME that Room (and
# Arco) come up first. Any harness.run_stack flag may follow and overrides
# these defaults (argparse: the last occurrence wins), e.g.:
#   ./terrarium.sh
#   ./terrarium.sh --room TEST
#   ./terrarium.sh --room DEMO --console-port 9000
#   ./terrarium.sh --web-build /path/to/mm-tuneshroom/build/web
# Same venv + PYTHONPATH handling as smoke-test.sh (a bare python3
# collects a misleading import error; see README.md).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PYTHONPATH=/Users/chris/projects/arco
exec .venv/bin/python -m harness.run_stack --no-bit --serve --devices 0 \
  --console-port 8772 "$@"
```

Make it executable:

```bash
chmod +x terrarium.sh
```

- [ ] **Step 4: README**

In `README.md`, directly above the paragraph that begins `It also carries a **Room panel**`, insert:

````markdown
## Quick start: a clean Terrarium

```
./terrarium.sh              # Console at http://127.0.0.1:8772/, no Room, no Bit
./terrarium.sh --room TEST  # Arco + the TEST Room up, no Bit
```

`./terrarium.sh` wraps `harness/run_stack.py --no-bit`: no Bit is
loaded and no Testshrooms are spawned. From the Console, **Load** picks a
Bit and the Room it should run in (a different Room than the active one
restarts Arco, about 15 s). Once a Bit is loaded the Console's **Join**
card shows, per registration node, the guest URL, a QR code for a phone
on the same LAN, and a `flutter run` line to paste into an mm-tuneshroom
checkout for a Chrome Testshroom. The same URLs print on stdout as
`JOIN_URL:` lines. Pass `--web-build /path/to/mm-tuneshroom/build/web` to
stage the guest app under `/app/`; without it the URL serves no page.
Native iOS/Android and the Radxa app cannot connect until mm-tuneshroom's
FFI o2lite link lands.

`./smoke-test.sh` remains the Bit-first launcher (spawned Testshrooms, CI
mode, profiles).
````

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_terrarium_sh.py -q`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add terrarium.sh README.md tests/test_terrarium_sh.py
git commit -m "feat: ./terrarium.sh clean standup wrapper and README quick start"
```

---

### Task 9: Console picker: room select per Bit card, Load enabled from NO_ROOM

**Files:**
- Modify: `console/static/bit.js` (module state lines 15-21, `roomReady()` line 23, `render()` lines 62-130, `buildPickCard` lines ~283-318, `init()` lines ~395-420)
- Modify: `console/static/terrarium.css` (small additions)
- Test: `tests/test_console_static.py` (structural), then a live check in Task 11

**Interfaces:**
- Consumes: `bits_listed` rows' `room_types`, `default_room_type` (Task 2); `snapshot.rooms` rows `{name, status, active}`; `room_loaded` / `room_unloaded` / `room_load_failed` events; `load_bit` accepting `room` (Task 3).
- Produces: `load_bit` sent as `{name, overrides, room}`.

- [ ] **Step 1: Write the failing structural test**

Append to `tests/test_console_static.py`:

```python
def test_bit_picker_sends_a_room_with_load_bit():
    js = (STATIC / "bit.js").read_text()
    assert 'room: select.value' in js or "room: roomSelect.value" in js
    assert "default_room_type" in js
    assert "roomSettled" in js
    assert "no configured room supports this Bit" in js
    assert "Arco restarts" in js
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_console_static.py -q -k picker`
Expected: FAIL

- [ ] **Step 3: Implement in `bit.js`**

(a) Module state: after `let terrariumState = null;` add

```js
let rooms = [];            // snapshot.rooms rows: {name, description, status, active}
```

and replace `roomReady()` with both helpers:

```js
function roomReady() {
  return terrariumState === "ROOM_READY";
}

// Load is allowed whenever the Terrarium is settled: a Bit can be loaded
// from NO_ROOM (the picker asks for a Room and the agent loads it first)
// or from ROOM_READY. Never mid-transition.
function roomSettled() {
  return terrariumState === "NO_ROOM" || terrariumState === "ROOM_READY";
}

function activeRoomName() {
  const active = rooms.find((r) => r.active);
  return active ? active.name : null;
}

// The Rooms this Bit can run in that terrarium.toml actually defines and
// reports loadable (status null).
function roomChoices(bitRow) {
  const loadable = new Set(rooms.filter((r) => r.status == null).map((r) => r.name));
  return (bitRow.room_types || []).filter((name) => loadable.has(name));
}
```

(b) In `render()`: the empty-state `loadBtn.disabled = !roomReady();` becomes `loadBtn.disabled = !roomSettled();`. In the button row, keep `const gated = !roomReady();` for Run / Restart / Abort but change the Load button to `loadBtn.disabled = !roomSettled();`. Update the comment above the button row to: `// Run/Restart/Abort need ROOM_READY; Load only needs a settled Terrarium (see roomSettled).`

(c) In `buildPickCard(bitRow)`, after the `card.appendChild(details);` line and before `const actions = ...`, add:

```js
  // Room choice (spec 2026-09-10 section 4): the Bit's room_types that
  // the config defines and reports loadable, preselecting the active Room
  // when compatible, else the Bit's own default.
  const choices = roomChoices(bitRow);
  const active = activeRoomName();
  const roomRow = mk("div", "roomrow");
  roomRow.appendChild(mk("span", "meta", "Room"));
  const select = document.createElement("select");
  select.className = "roompick";
  for (const name of choices) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
  if (choices.includes(active)) select.value = active;
  else if (choices.includes(bitRow.default_room_type)) select.value = bitRow.default_room_type;
  roomRow.appendChild(select);
  card.appendChild(roomRow);
  const hint = mk("p", "meta roomhint", "");
  card.appendChild(hint);
  const paintHint = () => {
    if (choices.length === 0) hint.textContent = "no configured room supports this Bit";
    else if (select.value !== active) hint.textContent = "switches Room: Arco restarts (about 15 s)";
    else hint.textContent = "";
  };
  select.onchange = paintHint;
  paintHint();
```

Then change the Load button: after `const loadBtn = mk("button", "btn solid-gold", "Load");` add `loadBtn.disabled = choices.length === 0;`, and change the `wire.send` line to:

```js
    wire.send("load_bit", { name: bitRow.name, overrides: result.overrides, room: select.value }, loadBtn);
```

(d) In `init()`: in the `snapshot` handler add `rooms = m.rooms || [];`. Add three handlers:

```js
  wire.on("room_loaded", (m) => {
    rooms = rooms.map((r) => Object.assign({}, r, { active: r.name === m.name }));
    render();
  });
  wire.on("room_unloaded", () => {
    rooms = rooms.map((r) => Object.assign({}, r, { active: false }));
    render();
  });
  wire.on("room_load_failed", () => render());
```

(e) In `terrarium.css`, append:

```css
.pick .roomrow { display: flex; align-items: center; gap: 8px; grid-column: 2; }
.pick .roomrow select { min-width: 120px; }
.pick .roomhint { grid-column: 2; margin: 0; min-height: 1em; }
```

- [ ] **Step 4: Run the structural tests**

Run: `.venv/bin/python -m pytest tests/test_console_static.py -q`
Expected: all pass

- [ ] **Step 5: Syntax-check the module**

Run: `node --check console/static/bit.js` (if `node` is present; otherwise open the Console in Task 11 and check the browser console for a parse error).
Expected: no output, exit 0

- [ ] **Step 6: Commit**

```bash
git add console/static/bit.js console/static/terrarium.css tests/test_console_static.py
git commit -m "feat(console): Bit picker asks for a Room; Load enabled from NO_ROOM"
```

---

### Task 10: `join.js` Join card

**Files:**
- Create: `console/static/join.js`
- Modify: `console/static/index.html` (a `#joinCard` mount), `console/static/shell.js:4,68` (import + init), `console/static/terrarium.css`
- Test: `tests/test_console_static.py`

**Interfaces:**
- Consumes: `snapshot.join` and `join_changed` (Task 4) in the `build_join_info` shape (Task 1).
- Produces: `join.js` exporting `init()` and `render(info)`.

Placement note: the spec says "mounted below the Loaded-Bit panel". The sidebar is narrow and the URL and command lines are long, so the card goes in the Live view's content column, right after `#bitStatusCard`. Record this deviation in the spec's Status section at closeout.

- [ ] **Step 1: Write the failing structural tests**

In `tests/test_console_static.py`, add `"join.js"` to `MODULES`, and append:

```python
def test_join_card_is_mounted_and_initialised():
    html = (STATIC / "index.html").read_text()
    assert 'id="joinCard"' in html
    shell = (STATIC / "shell.js").read_text()
    assert 'from "./join.js"' in shell
    assert "initJoin()" in shell
    js = (STATIC / "join.js").read_text()
    assert 'wire.on("join_changed"' in js
    assert "tuneshroom_cmd" in js
    assert "qr_svg" in js
    assert "native_note" in js
    assert "app_present" in js
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_static.py -q`
Expected: FAIL (`join.js` missing from the expected files; mount test fails)

- [ ] **Step 3: Implement**

Create `console/static/join.js`:

```js
// Join card: how a guest reaches the loaded Bit. Read model is
// snapshot.join / join_changed.join (control/join_info.py's
// build_join_info): the guest page URL, then one row per registration
// node with a server-rendered QR SVG, the node's URL and the Tuneshroom
// `flutter run` line. Null hides the card (no guest page served).
import * as wire from "./wire.js";

function clear(node) {
  node.textContent = "";
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// Clipboard on a plain-HTTP LAN origin is not a secure context, so
// navigator.clipboard is often undefined there; fall back to a hidden
// textarea + execCommand, which still works in every venue browser.
function copyButton(text) {
  const btn = mk("button", "btn small", "Copy");
  const done = (label) => {
    btn.textContent = label;
    setTimeout(() => { btn.textContent = "Copy"; }, 1500);
  };
  btn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(text);
      done("Copied");
      return;
    } catch (_e) { /* fall through */ }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_e) { ok = false; }
    ta.remove();
    done(ok ? "Copied" : "Copy failed");
  };
  return btn;
}

function lineWithCopy(text) {
  const row = mk("div", "joinrow");
  row.appendChild(mk("code", "mono", text));
  row.appendChild(copyButton(text));
  return row;
}

export function render(info) {
  const card = document.getElementById("joinCard");
  clear(card);
  if (!info) { card.hidden = true; return; }
  card.hidden = false;
  card.appendChild(mk("h3", "railhead", "Join"));
  if (!info.app_present) {
    card.appendChild(mk("p", "muted",
      "No web build is staged under /app/ (pass --web-build to run_stack), " +
      "so this URL serves no page yet."));
  }
  card.appendChild(mk("p", "meta", "Guest page"));
  card.appendChild(lineWithCopy(info.www_url));

  const nodes = info.nodes || [];
  if (nodes.length === 0) {
    card.appendChild(mk("p", "muted", info.bit
      ? "This Bit declares no registration nodes."
      : "Load a Bit to get per-node join links."));
  }
  for (const row of nodes) {
    const block = mk("div", "joinnode");
    block.appendChild(mk("h4", null, `${row.role} · ${row.node}`));
    if (row.qr_svg) {
      // Server-rendered SVG from the trusted local Console (segno output,
      // no script content); the only innerHTML in the front end.
      const qr = mk("div", "qr");
      qr.innerHTML = row.qr_svg;
      block.appendChild(qr);
    }
    block.appendChild(lineWithCopy(row.url));
    block.appendChild(mk("p", "meta",
      "Tuneshroom (Chrome sim; run from the mm-tuneshroom checkout root):"));
    block.appendChild(lineWithCopy(row.tuneshroom_cmd));
    card.appendChild(block);
  }
  card.appendChild(mk("p", "muted", info.native_note || ""));
}

export function init() {
  wire.on("snapshot", (m) => render(m.join));
  wire.on("join_changed", (m) => render(m.join));
}
```

In `console/static/index.html`, after `<div id="bitStatusCard" class="card" hidden></div>` add:

```html
      <div id="joinCard" class="card" hidden></div>
```

In `console/static/shell.js`: add `import { init as initJoin } from "./join.js";` after the `initBit` import line, and change the init line to

```js
initBit(); initJoin(); initSurface(); initFunctions(); initRail(); initRooms(); initDesign(); initBench(); initCalibrate(); initForms();
```

Append to `console/static/terrarium.css`:

```css
.joinrow { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.joinrow code { flex: 1; font-family: var(--f-mono); font-size: 12px; word-break: break-all; }
.joinnode { border-top: 1.5px solid var(--hair); padding-top: 10px; margin-top: 10px; }
.joinnode h4 { margin: 0 0 6px; font-family: var(--f-mono); font-size: 13px; }
.joinnode .qr svg { width: 168px; height: 168px; display: block; margin: 6px 0; }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_console_static.py -q`
Expected: all pass (including `test_no_external_asset_fetches_anywhere`: `join.js` contains no `http://` literal)

- [ ] **Step 5: Syntax-check**

Run: `node --check console/static/join.js && node --check console/static/shell.js` (skip if `node` is absent; Task 11 covers it live)
Expected: exit 0

- [ ] **Step 6: Commit**

```bash
git add console/static/join.js console/static/index.html console/static/shell.js console/static/terrarium.css tests/test_console_static.py
git commit -m "feat(console): Join card with per-node URL, QR and Tuneshroom command"
```

---

### Task 11: Full suite, live verification on MYCOLOGICAL, and the deep-dive note

**Files:**
- Modify: `docs/superpowers/specs/2026-09-10-terrarium-standup-and-join-design.md` (append a `## Status` section)
- Modify: `docs/MM_TERRARIUM.md` (a dated entry before `## Boundary rules`)

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass, 1 skipped (the pre-existing skip). Record the counts.

- [ ] **Step 2: Live standup, no Room** (**RUN ON: MYCOLOGICAL**, from this worktree; `MM_ARCO_PATH` and `MM_SOUNDFONT` exported as the deep-dive's o2lite-cutover entry requires from a worktree)

```bash
./terrarium.sh
```

Check: stdout shows `BROWSE_URL: Terrarium Console at http://127.0.0.1:8772/`, a `WWW_URL:` line, and the `NO_ROOM: waiting for the Console to load a Room` line; no Arco process (`pgrep -f apps/pytest/server` is empty). Open the Console: Load is enabled in NO_ROOM; the Join card shows the guest URL with the "no web build" warning and "Load a Bit to get per-node join links".

- [ ] **Step 3: Load a Bit with a Room from the picker**

In the picker, TestBit's card shows a Room select (TEST, DEMO) with TEST preselected. Load. Check: the Rooms panel shows the spawn stages, `room loaded: TEST` and `round loaded: TestBit` print, `JOIN_URL: player TEST_PLAYER_NODE http://<lan-ip>:8788/app/?node=TEST_PLAYER_NODE&o2ws=<lan-ip>:8080&ens=arco` prints (and a jammer line), and the Join card shows two node rows with QR codes and `flutter run` lines. Copy works on both buttons.

- [ ] **Step 4: Switch Rooms through the picker**

Load TestBit again choosing DEMO. Check: the hint said "switches Room: Arco restarts (about 15 s)"; stdout shows `room unloaded: TEST` then `room loaded: DEMO`; the Bit lands in SETUP in DEMO. Ctrl-C tears everything down with zero orphans (`pgrep -f o2_shroom; pgrep -f apps/pytest/server` both empty).

- [ ] **Step 5: Live standup with a Room and a guest join**

```bash
./terrarium.sh --room DEMO --web-build /Users/chris/projects/mm-tuneshroom/build/web
```

(Build first in mm-tuneshroom with `tool/sim build` if `build/web` is stale.) Check: `room loaded: DEMO` and `DeviceLink running on o2lite ensemble` print, engine IDLE, no round line. Load MetronomeBit (re-enable it in its manifest for the check if it is still `enabled = false`, and revert afterwards) or TestBit; open the Join card's node URL in desktop Chrome and confirm `device hello:` and `join granted:` lines. Then, from `/Users/chris/projects/mm-tuneshroom`, paste the copied `flutter run` line and confirm a second `join granted:`.

- [ ] **Step 6: Record the outcome**

Append to the spec:

```markdown
## Status

**Implemented 2026-09-10.** Suite: <N> passed, 1 skipped. Live on
MYCOLOGICAL: <what was observed in steps 2-5, one line each, including any
step that could not be completed and why>. Deviation: the Join card lives in
the Live view's content column (after the Bit status card) rather than the
sidebar, because the URL and command lines need the width. Phone-on-LAN QR
scan: with Chris.
```

Add a dated entry to `docs/MM_TERRARIUM.md` immediately before `## Boundary rules (the load-bearing invariants)`:

```markdown
### `./terrarium.sh`, `run_stack --no-bit`, room-aware `load_bit`, the Join card (2026-09-10)
Design: `docs/superpowers/specs/2026-09-10-terrarium-standup-and-join-design.md`.

- **`./terrarium.sh [--room NAME]`** wraps `run_stack --no-bit --serve
  --devices 0 --console-port 8772`: a clean Terrarium with no Bit and no
  spawned Testshrooms. Without `--room` it boots to `NO_ROOM` (gate:
  `markers.CONTROL_NO_ROOM_WAIT`, printed on entry to the NO_ROOM wait;
  no Arco exists yet); with `--room` it gates on room-loaded and
  transport-ready and skips the SETUP gate. `terrarium_boot --no-bit`
  (requires `--console-port`, refused with `--bit`/`--profile`, implies
  serve) makes `BootConfig.bit_name` None; `build()` then loads the Room
  only and `main()` drops into `_serve_roomless`, whose inner
  `_wait_for_load` sits in IDLE until the Console loads a Bit.
- **Console `load_bit` takes a `room`.** `ConsoleAgent._ensure_room_for_bit`
  resolves the Bit's config, refuses a room outside its `room_types`
  BEFORE touching the Room, and for a different room than the active one
  runs abort (if a Bit is loaded), `unload_room(force=True)`, `load_room`,
  then `load_bit`. The picker (`console/static/bit.js`) shows a Room select
  per Bit card (room_types intersected with loadable configured rooms,
  preselecting the active room, else `default_room_type`), and Load is
  enabled from NO_ROOM as well as ROOM_READY. `bits_listed` rows carry
  `default_room_type` and `nodes`.
- **Join card** (`console/static/join.js`, Live view): per registration
  node, the guest URL `http://<lan-ip>:8788/app/?node=<NODE>&o2ws=<lan-ip>:8080&ens=<ens>`,
  a QR (server-side, `segno`, now in `requirements.txt`), and the Chrome
  sim command `flutter run -d chrome -t lib/sim_main.dart --dart-define=NODE=...
  --dart-define=O2WS=... --dart-define=ENS=...`. Built by the pure
  `control/join_info.py`; the agent takes a `join_info` provider,
  ships it as `snapshot.join` and broadcasts `join_changed` on LOADED
  and IDLE; `terrarium_boot` prints `JOIN_URL: <role> <node> <url>` per
  node (`markers.JOIN_URL`, echoed by run_stack, never waited on). `dev`
  is deliberately absent (the app mints one). **Native iOS/Android and the
  Radxa app still cannot connect** (old websocket wire); the card says so.
```

Then run `.venv/bin/python -m pytest tests -q` once more and commit:

```bash
git add docs/superpowers/specs/2026-09-10-terrarium-standup-and-join-design.md docs/MM_TERRARIUM.md
git commit -m "docs: terrarium.sh standup, room-aware load_bit and Join card live-verified"
```

At closeout, run the `mm-deepdive-sync` skill as the project convention requires; the entry above is its input.
