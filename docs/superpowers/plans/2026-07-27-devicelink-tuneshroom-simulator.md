# DeviceLink + Flutter Tuneshroom Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Control+GameServer its first device wire, and mm-tuneshroom a Flutter web simulator that registers, receives its light-manifest-v2 config blob, renders the resulting Shroom, and drives it with sensor input.

**Architecture:** A new `devicelink/` package in mm-terrarium mirrors the proven `console/` structure (a socket-only `DeviceLinkServer` + a transport-agnostic `DeviceLinkAgent` driven from the tick loop). The agent holds one `DeviceBridge` → luxaeterna `LightSession` per joined device and streams rendered frames out. On the mm-tuneshroom side, a `DeviceLink` interface isolates the transport so the same Dart code targets web (websocket) today and native (FFI o2lite) later.

**Tech Stack:** Python 3 + `websockets` (sync API) + luxaeterna; Dart/Flutter with `web_socket_channel`.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-27-devicelink-tuneshroom-simulator-design.md`. Every task's requirements implicitly include it.
- **Boundary rule 2:** DeviceLink is a transport shell, never the hot loop. An exception inside it must never propagate into the engine tick.
- **Boundary rule 3:** Lux Aeterna is downstream of Bit cue logic. The transport never decides colour; a Bit does.
- **Design Rule 2:** Identity in arguments, not addresses. Every `/game/*` message carries `dev` as its first argument.
- **Trust model:** trusted LAN, **no authentication**, default bind `127.0.0.1`. `0.0.0.0` is an explicit opt-in flag, never a default.
- **No hop counts, no latency figures** may be quoted anywhere in code, comments, docs, or commit messages produced by this plan. The shim is 1 hop where the real path is 2.
- **Do not modify** `www/`, `lib/ffi/`, `lib/audio/`, `lib/sensors/`, `lib/core/`, `lib/ui/`, or `harness/` in mm-tuneshroom. They are working legacy references.
- **Python style:** match the existing packages — `from __future__ import annotations` where used, module-level `logger = logging.getLogger(__name__)`, synchronous `websockets` API, no async/await.
- **Test commands:** mm-terrarium `python3 -m pytest tests -v`; mm-tuneshroom `flutter test`.

---

## Deviation from the spec (read before Task 5)

Spec §5 *Frame streaming* describes a luxaeterna `OutputLoop` rendering at 44 Hz on its own thread, with frames crossing to the socket by enqueue-and-drain.

**This plan renders on the tick loop instead**, with no `OutputLoop`, no `DMXBackend`, and no second thread. `LightSession.render_into(universe)` is directly callable, and there is no DMX hardware anywhere in this path — `OutputLoop` exists to drive a backend we do not have. Rendering in `DeviceLinkAgent.poll()` removes the only threading boundary in the design and deletes an entire class of race.

**Cost:** frame rate becomes tick rate. A caller ticking at 44 Hz gets 44 fps; a slow tick loop yields a slow simulator. This is acceptable because the simulator's own driver (`devicelink_smoke.py`, Task 6) controls its tick rate, and nothing in this slice measures timing.

If this trade is rejected, Task 5 is the only task that changes.

---

## File Structure

**mm-terrarium** (branch `claude/devicelink-tuneshroom-simulator`, already exists off `main`)

| File | Responsibility |
|---|---|
| `control/engine.py` *(modify)* | Add `data()` input entry point + `on_light_cue` sink |
| `bits/test_bit.py` *(modify)* | Add a `tilt` verb handler mapping tilt → `cc:74` |
| `devicelink/__init__.py` *(create)* | Package marker |
| `devicelink/protocol.py` *(create)* | Envelope dataclass, encode/decode, outbound event builders. Single source of truth for the wire shape |
| `devicelink/server.py` *(create)* | The only socket-touching code. Drain-based tick-thread API |
| `devicelink/agent.py` *(create)* | Brains: inbound dispatch, per-device `DeviceBridge`, frame emit |
| `harness/device_bridge.py` *(unchanged)* | Already correct; the agent holds one instance per device |
| `harness/devicelink_smoke.py` *(create)* | End-to-end driver, sibling of `led_smoke.py` |
| `tests/test_engine_data.py` *(create)* | Verb dispatch |
| `tests/test_devicelink_protocol.py` *(create)* | Envelope round-trip |
| `tests/test_devicelink_server.py` *(create)* | Socket lifecycle, fan-out |
| `tests/test_devicelink_agent.py` *(create)* | Registration path, error path |
| `tests/test_devicelink_frames.py` *(create)* | Frame emit-on-change |
| `tests/test_devicelink_smoke.py` *(create)* | Headless end-to-end |

**mm-tuneshroom** (new branch `claude/devicelink-simulator` off `main`)

| File | Responsibility |
|---|---|
| `web/` *(create, scaffolded)* | Flutter web platform target |
| `pubspec.yaml` *(modify)* | Add `web_socket_channel` |
| `lib/link/envelope.dart` *(create)* | Envelope codec. Pure Dart, no platform imports |
| `lib/link/device_link.dart` *(create)* | Abstract transport interface |
| `lib/link/websocket_link.dart` *(create)* | The one implementation today |
| `lib/sim/shroom_painter.dart` *(create)* | GRB→RGB decode + 8-ring/4-stem `CustomPainter` |
| `lib/sim/sim_screen.dart` *(create)* | Node picker, LED display, manifest panel |
| `lib/sim/sim_controller.dart` *(create)* | Wires link ↔ UI state |
| `lib/sim/tilt_source.dart` *(create)* | Sensor capture → `/game/tilt` |
| `lib/sim_main.dart` *(create)* | Simulator entry point, separate from `lib/main.dart` |
| `test/envelope_test.dart` *(create)* | Codec round-trip |
| `test/shroom_painter_test.dart` *(create)* | GRB decode |
| `test/sim_controller_test.dart` *(create)* | Link ↔ state, against a fake link |

**Wire vocabulary** (frozen here; `devicelink/protocol.py` and `lib/link/envelope.dart` must agree)

Envelope JSON: `{"timestamp": float, "address": str, "typespec": str, "args": list}`

Typespec chars: `s` string, `i` int32, `f` float, `b` blob (a JSON value; over real o2lite this becomes a serialized blob, per Design Rule 5's "blobs for sysex or bulk").

| Direction | Address | Typespec | Args |
|---|---|---|---|
| in | `/game/hello` | `sss` | dev, name, protoversion |
| in | `/game/join` | `ss` | dev, node |
| in | `/game/tilt` | `sf` | dev, gamma |
| out | `/<dev>/role` | `b` | config blob |
| out | `/<dev>/deny` | `ss` | reason, hint |
| out | `/<dev>/leds` | `b` | flat list of 36 ints (12 px × GRB) |
| out | `/<dev>/release` | *(empty)* | — |
| out | `/<dev>/error` | `ss` | context, message |

`dev` is supplied by the device in `/game/hello` (Design Rule 2) and is used verbatim as its service name, so a device calling itself `ie1` receives `/ie1/role`. Server-side `dev→N` assignment is out of scope.

---

## Task 1: Engine verb-dispatch seam

Wires `Bit.verb_handlers()`, which is declared in `control/bit.py:60` with a default and a test but which `GameServer` never calls.

**Files:**
- Modify: `control/engine.py`
- Test: `tests/test_engine_data.py`

**Interfaces:**
- Consumes: `GameServer` (existing), `Bit.verb_handlers() -> dict` (existing, unrouted), `State` (existing).
- Produces:
  - `GameServer.data(dev: str, verb: str, args: list) -> str | None` — returns `None` on success, or a refusal reason string. Never raises for bad input.
  - `GameServer.on_light_cue` — a transport-owned sink, default `None`, called as `on_light_cue(dev: str, status: int, data1: int, data2: int)`.
  - Bit verb handler contract: `handler(dev: str, args: list) -> list[tuple[str, int, int, int]] | None`, each tuple `(dev, status, data1, data2)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_engine_data.py`:

```python
"""GameServer.data: routing /game/<verb> to a Bit's verb_handlers."""

import pytest

from control.engine import GameServer
from control.roles import Role, RoleClass, RoleTable
from control.bit import Bit


class VerbBit(Bit):
    version = "0.1"

    def __init__(self):
        self.seen = []
        self.raise_next = False

    @property
    def role_table(self) -> RoleTable:
        player = Role(name="player", role_class=RoleClass.SHARED,
                      capacity=None, scored=False)
        return RoleTable(roles={"player": player},
                         node_map={"NODE_A": ["player"]})

    def update(self, dt: float) -> bool:
        return False

    def verb_handlers(self) -> dict:
        return {"tilt": self._on_tilt}

    def _on_tilt(self, dev, args):
        if self.raise_next:
            raise RuntimeError("boom")
        self.seen.append((dev, args))
        return [(dev, 0xB0, 74, 64)]


def _loaded_server():
    gs = GameServer({"verb_bit": VerbBit})
    gs.load_bit("verb_bit")
    return gs


def test_data_routes_to_handler_and_emits_cue():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    cues = []
    gs.on_light_cue = lambda *c: cues.append(c)

    assert gs.data("ie1", "tilt", ["ie1", 30.0]) is None
    assert gs.bit.seen == [("ie1", ["ie1", 30.0])]
    assert cues == [("ie1", 0xB0, 74, 64)]


def test_unregistered_device_is_refused():
    gs = _loaded_server()
    assert gs.data("ie9", "tilt", ["ie9", 0.0]) == "device not registered"


def test_unknown_verb_is_refused():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    assert gs.data("ie1", "wiggle", ["ie1"]) == "unknown verb 'wiggle'"


def test_no_bit_loaded_is_refused():
    gs = GameServer({"verb_bit": VerbBit})
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "no Bit running"


def test_raising_handler_is_contained():
    gs = _loaded_server()
    gs.join("ie1", "NODE_A")
    gs.bit.raise_next = True
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "handler error"
    assert gs.state.name == "SETUP"   # engine unharmed


def test_bit_declaring_no_verbs_is_unaffected():
    from bits.test_bit import TestBit
    gs = GameServer({"test_bit": TestBit})
    gs.load_bit("test_bit")
    gs.join("ie1", "TEST_JAM_NODE")
    assert gs.data("ie1", "tilt", ["ie1", 0.0]) == "unknown verb 'tilt'"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine_data.py -v`
Expected: FAIL — `AttributeError: 'GameServer' object has no attribute 'data'`

- [ ] **Step 3: Add the `on_light_cue` sink**

In `control/engine.py`, in `__init__`, directly after the existing `self.on_release = None` block:

```python
        # Set by a transport layer: called when a Bit's verb handler emits a
        # light cue, as on_light_cue(dev, status, data1, data2). Boundary
        # rule 3 -- the Bit decides the light consequence, the transport
        # only delivers it to that device's renderer.
        self.on_light_cue = None
```

- [ ] **Step 4: Add the `data()` entry point**

In `control/engine.py`, immediately after `join()` and before `tick()`:

```python
    def data(self, dev: str, verb: str, args: list) -> str | None:
        """Route a /game/<verb> message to the loaded Bit's verb handler.

        Returns None when handled, else a refusal reason a transport can
        surface as /<dev>/error. Never raises: a device must never be able
        to wedge Control, exactly as a Bit must never be able to.
        """
        if self.state not in (State.SETUP, State.RUNNING):
            return "no Bit running"
        if dev not in self.registration.assignments:
            return "device not registered"
        try:
            handler = self.bit.verb_handlers().get(verb)
        except Exception:
            logger.exception("Bit.verb_handlers raised; refusing %r", verb)
            return "handler error"
        if handler is None:
            return f"unknown verb {verb!r}"
        try:
            cues = handler(dev, args)
        except Exception:
            logger.exception("Bit verb handler %r raised; ignoring", verb)
            return "handler error"
        if cues and self.on_light_cue is not None:
            for cue in cues:
                try:
                    self.on_light_cue(*cue)
                except Exception:
                    logger.exception("on_light_cue raised; continuing")
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine_data.py -v`
Expected: 6 passed

- [ ] **Step 6: Run the full suite for regressions**

Run: `python3 -m pytest tests -v`
Expected: all pass (147 passed, 3 skipped previously, plus the 6 new)

- [ ] **Step 7: Commit**

```bash
git add control/engine.py tests/test_engine_data.py
git commit -m "feat(control): route /game/<verb> to Bit.verb_handlers

verb_handlers() has been declared on the Bit interface with a default and
a test since the first slice, but GameServer never called it -- there was
no path from a device message to a Bit at all.

GameServer.data(dev, verb, args) validates state and registration, looks
up the handler, and forwards any emitted light cues to the transport-owned
on_light_cue sink. It returns a reason string rather than raising, so a
misbehaving device can never wedge Control."
```

---

## Task 2: `devicelink/protocol.py`

**Files:**
- Create: `devicelink/__init__.py`, `devicelink/protocol.py`
- Test: `tests/test_devicelink_protocol.py`

**Interfaces:**
- Consumes: `control.registration.JoinResult` (for `role_event` / `deny_event`).
- Produces:
  - `Envelope` dataclass: `timestamp: float`, `address: str`, `typespec: str`, `args: list`
  - `encode(env: Envelope) -> dict`
  - `decode(msg: dict) -> Envelope` — raises `ValueError` on a malformed message
  - `parse_game_address(address: str) -> str | None` — `/game/join` → `"join"`, anything else → `None`
  - `role_event(dev, config) -> dict`, `deny_event(dev, reason, hint) -> dict`,
    `leds_event(dev, channels) -> dict`, `release_event(dev) -> dict`,
    `error_event(dev, context, message) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_devicelink_protocol.py`:

```python
"""DeviceLink wire protocol: envelope round-trip and event builders."""

import pytest

from devicelink import protocol


def test_envelope_round_trip():
    env = protocol.Envelope(timestamp=1.5, address="/game/join",
                            typespec="ss", args=["ie1", "NODE_A"])
    assert protocol.decode(protocol.encode(env)) == env


def test_decode_rejects_missing_address():
    with pytest.raises(ValueError):
        protocol.decode({"typespec": "ss", "args": []})


def test_decode_rejects_non_list_args():
    with pytest.raises(ValueError):
        protocol.decode({"address": "/game/join", "typespec": "ss",
                         "args": "nope"})


def test_decode_rejects_typespec_arity_mismatch():
    with pytest.raises(ValueError):
        protocol.decode({"address": "/game/join", "typespec": "ss",
                         "args": ["only-one"]})


def test_decode_defaults_missing_timestamp_to_zero():
    env = protocol.decode({"address": "/game/hello", "typespec": "",
                           "args": []})
    assert env.timestamp == 0.0


@pytest.mark.parametrize("address,expected", [
    ("/game/join", "join"),
    ("/game/hello", "hello"),
    ("/game/tilt", "tilt"),
    ("/ie1/role", None),
    ("/game", None),
    ("/game/", None),
    ("nonsense", None),
])
def test_parse_game_address(address, expected):
    assert protocol.parse_game_address(address) == expected


def test_role_event_carries_blob_verbatim():
    blob = {"light_manifest": {"instruments": []}, "role": "player"}
    msg = protocol.role_event("ie1", blob)
    assert msg["address"] == "/ie1/role"
    assert msg["typespec"] == "b"
    assert msg["args"][0] is blob


def test_deny_event_normalises_missing_hint():
    msg = protocol.deny_event("ie1", "player at capacity", None)
    assert msg["address"] == "/ie1/deny"
    assert msg["args"] == ["player at capacity", ""]


def test_leds_event_shape():
    msg = protocol.leds_event("ie1", list(range(36)))
    assert msg["address"] == "/ie1/leds"
    assert msg["typespec"] == "b"
    assert msg["args"] == [list(range(36))]


def test_release_and_error_events():
    assert protocol.release_event("ie1")["address"] == "/ie1/release"
    err = protocol.error_event("ie1", "join", "no such node")
    assert err["args"] == ["join", "no such node"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_devicelink_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devicelink'`

- [ ] **Step 3: Create the package marker**

Create `devicelink/__init__.py`:

```python
"""DeviceLink: the device-facing websocket transport for Control.

The inbound sibling of console/ -- same split (a socket-only server plus a
transport-agnostic agent driven from the tick loop), but its clients are
simulated Tuneshrooms speaking /game/* rather than operators.
"""
```

- [ ] **Step 4: Write the protocol module**

Create `devicelink/protocol.py`:

```python
"""DeviceLink wire protocol: a JSON envelope mirroring o2ws field-for-field.

    {"timestamp": float, "address": str, "typespec": str, "args": list}

Typespec chars: 's' string, 'i' int32, 'f' float, 'b' blob (any JSON value;
over real o2lite this becomes a serialized blob, per Design Rule 5).

This module is the single source of truth for the wire shape. Its Dart
counterpart is mm-tuneshroom lib/link/envelope.dart -- change both together.
"""

from __future__ import annotations

from dataclasses import dataclass

_GAME_PREFIX = "/game/"


@dataclass(frozen=True)
class Envelope:
    timestamp: float
    address: str
    typespec: str
    args: list


def encode(env: Envelope) -> dict:
    return {"timestamp": env.timestamp, "address": env.address,
            "typespec": env.typespec, "args": list(env.args)}


def decode(msg: dict) -> Envelope:
    """Parse an inbound message. Raises ValueError on anything malformed --
    callers treat that as 'drop this frame', never as an engine error."""
    if not isinstance(msg, dict):
        raise ValueError("envelope must be an object")
    address = msg.get("address")
    if not isinstance(address, str) or not address:
        raise ValueError("envelope needs a non-empty string address")
    typespec = msg.get("typespec", "")
    if not isinstance(typespec, str):
        raise ValueError("typespec must be a string")
    args = msg.get("args", [])
    if not isinstance(args, list):
        raise ValueError("args must be a list")
    if len(typespec) != len(args):
        raise ValueError(
            f"typespec {typespec!r} does not match {len(args)} args")
    timestamp = msg.get("timestamp", 0.0)
    if not isinstance(timestamp, (int, float)):
        raise ValueError("timestamp must be a number")
    return Envelope(timestamp=float(timestamp), address=address,
                    typespec=typespec, args=args)


def parse_game_address(address: str) -> str | None:
    """'/game/join' -> 'join'. Anything not a non-empty /game/<verb>: None."""
    if not address.startswith(_GAME_PREFIX):
        return None
    verb = address[len(_GAME_PREFIX):]
    return verb or None


def _event(address: str, typespec: str, args: list) -> dict:
    return encode(Envelope(timestamp=0.0, address=address,
                           typespec=typespec, args=args))


def role_event(dev: str, config: dict) -> dict:
    """The granted /<dev>/role blob, passed through verbatim -- it must stay
    byte-identical to JoinResult.config."""
    return _event(f"/{dev}/role", "b", [config])


def deny_event(dev: str, reason: str | None, hint: str | None) -> dict:
    return _event(f"/{dev}/deny", "ss", [reason or "", hint or ""])


def leds_event(dev: str, channels) -> dict:
    """channels: a flat sequence of 36 ints (12 pixels x GRB)."""
    return _event(f"/{dev}/leds", "b", [list(channels)])


def release_event(dev: str) -> dict:
    return _event(f"/{dev}/release", "", [])


def error_event(dev: str, context: str, message: str) -> dict:
    return _event(f"/{dev}/error", "ss", [context, message])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_devicelink_protocol.py -v`
Expected: all pass (16 including parametrized cases)

- [ ] **Step 6: Commit**

```bash
git add devicelink/__init__.py devicelink/protocol.py tests/test_devicelink_protocol.py
git commit -m "feat(devicelink): wire protocol -- o2ws-shaped JSON envelope

Single source of truth for the DeviceLink wire shape: an envelope carrying
timestamp/address/typespec/args, so the later swap to real o2ws framing is
mechanical. Malformed frames raise ValueError for the caller to drop; they
never reach the engine."
```

---

## Task 3: `devicelink/server.py`

**Files:**
- Create: `devicelink/server.py`
- Test: `tests/test_devicelink_server.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure transport).
- Produces: `DeviceLinkServer(host="127.0.0.1", port=0)` with `start()`, `stop()`, `port` (property), `drain_new_clients() -> list`, `drain_inbound() -> list[tuple[client, dict]]`, `send(client, msg: dict)`, `broadcast(msg: dict)`.

This mirrors `console/server.py` deliberately, minus the static-HTML branch — devices need no page served.

- [ ] **Step 1: Write the failing test**

Create `tests/test_devicelink_server.py`:

```python
"""DeviceLinkServer: real localhost sockets, drain-based tick-thread API."""

import json
import time

import pytest
from websockets.sync.client import connect

from devicelink.server import DeviceLinkServer


@pytest.fixture
def server():
    srv = DeviceLinkServer(host="127.0.0.1", port=0)
    srv.start()
    yield srv
    srv.stop()


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    return None


def test_binds_an_ephemeral_port(server):
    assert server.port > 0


def test_new_client_is_drained_once(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws"):
        assert _wait_for(server.drain_new_clients) is not None
    assert server.drain_new_clients() == []


def test_inbound_json_is_drained(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
        client.send(json.dumps({"address": "/game/hello"}))
        inbound = _wait_for(server.drain_inbound)
    assert inbound is not None
    _, msg = inbound[0]
    assert msg == {"address": "/game/hello"}


def test_non_json_frame_is_dropped_not_raised(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
        client.send("not json at all")
        client.send(json.dumps({"address": "/game/join"}))
        inbound = _wait_for(server.drain_inbound)
    assert inbound is not None
    assert [m for _, m in inbound] == [{"address": "/game/join"}]


def test_broadcast_reaches_every_client(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws") as a, \
         connect(f"ws://127.0.0.1:{server.port}/ws") as b:
        _wait_for(lambda: len(server.drain_new_clients()) or None)
        time.sleep(0.1)
        server.broadcast({"address": "/ie1/release"})
        assert json.loads(a.recv(timeout=2))["address"] == "/ie1/release"
        assert json.loads(b.recv(timeout=2))["address"] == "/ie1/release"


def test_send_to_a_dead_client_does_not_raise(server):
    with connect(f"ws://127.0.0.1:{server.port}/ws"):
        client = _wait_for(server.drain_new_clients)[0]
    time.sleep(0.1)
    server.send(client, {"address": "/ie1/role"})   # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_devicelink_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devicelink.server'`

- [ ] **Step 3: Write the server**

Create `devicelink/server.py`:

```python
"""DeviceLinkServer: the only socket-touching code in the devicelink package.

Deliberately the same shape as console/server.py -- handler threads touch
only thread-safe queues and a lock-guarded client set, every GameServer
access stays on the tick thread that drives DeviceLinkAgent.poll().
Devices need no page served, so there is no static-HTML branch.
"""

import json
import logging
import threading
from collections import deque

from websockets.sync.server import serve

logger = logging.getLogger(__name__)


class DeviceLinkServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._host = host
        self._port = port
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self._clients: set = set()
        self._new_clients: deque = deque()
        self._inbound: deque = deque()

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._server = serve(self._handle, self._host, self._port)
        self._port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    @property
    def port(self) -> int:
        return self._port

    # --- per-connection handler thread -------------------------------------
    def _handle(self, connection) -> None:
        with self._lock:
            self._clients.add(connection)
            self._new_clients.append(connection)
        try:
            for raw in connection:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    logger.warning("dropping non-JSON device frame")
                    continue
                with self._lock:
                    self._inbound.append((connection, msg))
        except Exception:
            logger.debug("device client handler ended", exc_info=True)
        finally:
            with self._lock:
                self._clients.discard(connection)

    # --- tick-thread API (consumed by DeviceLinkAgent) ---------------------
    def drain_new_clients(self) -> list:
        with self._lock:
            out = list(self._new_clients)
            self._new_clients.clear()
        return out

    def drain_inbound(self) -> list:
        with self._lock:
            out = list(self._inbound)
            self._inbound.clear()
        return out

    def send(self, client, msg: dict) -> None:
        try:
            client.send(json.dumps(msg))
        except Exception:
            logger.debug("device send failed; dropping client", exc_info=True)
            with self._lock:
                self._clients.discard(client)

    def broadcast(self, msg: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        payload = json.dumps(msg)
        for client in clients:
            try:
                client.send(payload)
            except Exception:
                logger.debug("device broadcast failed; dropping client",
                             exc_info=True)
                with self._lock:
                    self._clients.discard(client)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_devicelink_server.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add devicelink/server.py tests/test_devicelink_server.py
git commit -m "feat(devicelink): DeviceLinkServer -- socket-only, drain-based

Same split as console/server.py: handler threads touch only thread-safe
queues, every engine access stays on the tick thread. A dead or slow client
is dropped without blocking its peers, and a non-JSON frame is logged and
skipped rather than killing the connection."
```

---

## Task 4: `devicelink/agent.py` — registration path

**Files:**
- Create: `devicelink/agent.py`
- Test: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `GameServer.hello/join/data`, `GameServer.on_release`, `GameServer.on_light_cue` (Task 1), `devicelink.protocol` (Task 2), `DeviceLinkServer` (Task 3), `harness.device_bridge.DeviceBridge` (existing).
- Produces: `DeviceLinkAgent(game_server, server, capability=None, clock=time.monotonic)` with `poll() -> None`, `bridges: dict[str, DeviceBridge]`, `client_for(dev) -> client | None`.

Frame emission arrives in Task 5; this task establishes the connection→hello→join→role/deny path.

- [ ] **Step 1: Write the failing test**

Create `tests/test_devicelink_agent.py`:

```python
"""DeviceLinkAgent: inbound dispatch and the registration path, against an
in-process fake server (no sockets -- see test_devicelink_server.py)."""

import pytest

# devicelink.agent imports harness.device_bridge, which needs the sibling
# luxaeterna checkout. Guard it the same way tests/test_device_bridge.py and
# tests/test_led_smoke.py do, so the core suite still collects without it
# (requirements-dev.txt states that contract).
pytest.importorskip("luxaeterna")

from bits.test_bit import TestBit
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent


class FakeServer:
    """Same tick-thread API as DeviceLinkServer, no sockets."""

    def __init__(self):
        self.new_clients = []
        self.inbound = []
        self.sent = []          # (client, msg)
        self.broadcasts = []

    def drain_new_clients(self):
        out, self.new_clients = self.new_clients, []
        return out

    def drain_inbound(self):
        out, self.inbound = self.inbound, []
        return out

    def send(self, client, msg):
        self.sent.append((client, msg))

    def broadcast(self, msg):
        self.broadcasts.append(msg)

    # --- test helpers ---
    def arrive(self, client):
        self.new_clients.append(client)

    def deliver(self, client, address, typespec="", args=None):
        self.inbound.append((client, {"address": address,
                                      "typespec": typespec,
                                      "args": args or []}))

    def addressed(self, address):
        return [m for _, m in self.sent if m["address"] == address]


@pytest.fixture
def rig():
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server)
    return gs, server, agent


def _hello(server, agent, client="c1", dev="ie1"):
    server.arrive(client)
    server.deliver(client, "/game/hello", "sss", [dev, "sim", "1"])
    agent.poll()


def test_hello_registers_the_device_in_the_pool(rig):
    gs, server, agent = rig
    _hello(server, agent)
    assert [d.dev for d in gs.devices.all()] == ["ie1"]


def test_granted_join_sends_role_blob_byte_identical(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()

    roles = server.addressed("/ie1/role")
    assert len(roles) == 1
    blob = roles[0]["args"][0]
    assert blob["role"] == "player"
    # Provenance is stamped INSIDE light_manifest by compose_role_config,
    # not at the top level. Top level carries role/class/scored.
    assert blob["light_manifest"]["bit_name"] == "test_bit"
    assert blob["light_manifest"]["instruments"][0]["instrument"] == "aurora"


def test_granted_join_builds_a_light_session(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert agent.bridges["ie1"].session is not None


def test_denied_join_sends_deny_with_engine_reason(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "NO_SUCH_NODE"])
    agent.poll()

    denies = server.addressed("/ie1/deny")
    assert denies[0]["args"][0] == "no such node"
    assert "ie1" not in agent.bridges


def test_join_with_no_bit_loaded_is_denied(rig):
    gs, server, agent = rig
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    assert server.addressed("/ie1/deny")[0]["args"][0] == \
        "no Bit accepting registrations"


def test_verb_refusal_becomes_an_error_event(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_JAM_NODE"])
    agent.poll()
    server.deliver("c1", "/game/wiggle", "s", ["ie1"])
    agent.poll()
    assert server.addressed("/ie1/error")[0]["args"] == \
        ["wiggle", "unknown verb 'wiggle'"]


def test_malformed_envelope_is_dropped_silently(rig):
    gs, server, agent = rig
    server.arrive("c1")
    server.inbound.append(("c1", {"nonsense": True}))
    agent.poll()          # must not raise
    assert server.sent == []


def test_release_sends_release_and_clears_the_bridge(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    gs.abort()
    assert server.addressed("/ie1/release")
    assert "ie1" not in agent.bridges


def test_light_cue_reaches_the_devices_session(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    _hello(server, agent)
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    session = agent.bridges["ie1"].session
    gs.on_light_cue("ie1", 0xB0, 74, 100)     # must not raise
    assert session is agent.bridges["ie1"].session
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_devicelink_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devicelink.agent'`

- [ ] **Step 3: Write the agent**

Create `devicelink/agent.py`:

```python
"""DeviceLinkAgent: translates between the DeviceLink wire protocol and
GameServer calls, and owns one luxaeterna LightSession per joined device.

The device-facing sibling of console.ConsoleAgent -- transport-agnostic (it
talks to a server object, see devicelink/server.py), so it is fully testable
offline against an in-process fake. Driven from the engine tick loop via
poll().

Boundary rule 2: nothing in here may propagate into the engine tick.
"""

from __future__ import annotations

import logging
import time

from control.engine import GameServer
from devicelink import protocol
from harness.device_bridge import DeviceBridge

logger = logging.getLogger(__name__)


class DeviceLinkAgent:
    def __init__(self, game_server: GameServer, server,
                 capability=None, clock=time.monotonic):
        self.game_server = game_server
        self.server = server
        self._capability = capability
        self._clock = clock
        self.bridges: dict[str, DeviceBridge] = {}
        self._clients: dict[str, object] = {}     # dev -> client
        game_server.add_observer(self)
        game_server.on_release = self._on_release
        game_server.on_light_cue = self._on_light_cue

    def client_for(self, dev: str):
        return self._clients.get(dev)

    # --- driven once per tick-loop iteration -------------------------------
    def poll(self) -> None:
        self.server.drain_new_clients()      # devices are anonymous until hello
        for client, msg in self.server.drain_inbound():
            try:
                self._handle(client, msg)
            except Exception:
                logger.exception("devicelink inbound handling failed; "
                                 "dropping frame")

    # --- inbound dispatch ---------------------------------------------------
    def _handle(self, client, msg: dict) -> None:
        try:
            env = protocol.decode(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable device frame: %s", exc)
            return
        verb = protocol.parse_game_address(env.address)
        if verb is None:
            logger.warning("dropping non-/game address %r", env.address)
            return
        if not env.args or not isinstance(env.args[0], str):
            logger.warning("dropping /game/%s with no dev argument", verb)
            return
        dev = env.args[0]
        if verb == "hello":
            self._on_hello(client, dev, env.args)
        elif verb == "join":
            self._on_join(client, dev, env.args)
        else:
            self._on_verb(dev, verb, env.args)

    def _on_hello(self, client, dev: str, args: list) -> None:
        name = args[1] if len(args) > 1 else ""
        protoversion = args[2] if len(args) > 2 else ""
        self._clients[dev] = client
        self.game_server.hello(dev, name, protoversion)

    def _on_join(self, client, dev: str, args: list) -> None:
        if len(args) < 2:
            self._send(dev, protocol.error_event(dev, "join", "missing node"))
            return
        self._clients[dev] = client
        result = self.game_server.join(dev, args[1])
        if not result.granted:
            self._send(dev, protocol.deny_event(dev, result.reason, result.hint))
            return
        self._send(dev, protocol.role_event(dev, result.config))
        bridge = DeviceBridge(capability=self._capability, clock=self._clock)
        try:
            bridge.on_grant(result)
        except Exception:
            logger.exception("building the LightSession for %s failed", dev)
            self._send(dev, protocol.error_event(
                dev, "role", "could not build light session"))
            return
        self.bridges[dev] = bridge

    def _on_verb(self, dev: str, verb: str, args: list) -> None:
        reason = self.game_server.data(dev, verb, args)
        if reason is not None:
            self._send(dev, protocol.error_event(dev, verb, reason))

    # --- engine-owned sinks -------------------------------------------------
    def _on_release(self, dev: str) -> None:
        bridge = self.bridges.pop(dev, None)
        if bridge is not None:
            try:
                bridge.on_release(dev)
            except Exception:
                logger.exception("session clear for %s failed", dev)
        self._send(dev, protocol.release_event(dev))

    def _on_light_cue(self, dev: str, status: int,
                      data1: int, data2: int) -> None:
        bridge = self.bridges.get(dev)
        if bridge is None or bridge.session is None:
            return
        try:
            bridge.session.feed_midi(status, data1, data2)
        except Exception:
            logger.exception("feed_midi for %s failed", dev)

    # --- outbound -----------------------------------------------------------
    def _send(self, dev: str, msg: dict) -> None:
        client = self._clients.get(dev)
        if client is None:
            return
        self.server.send(client, msg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_devicelink_agent.py -v`
Expected: 9 passed

- [ ] **Step 5: Run the full suite for regressions**

Run: `python3 -m pytest tests -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "feat(devicelink): DeviceLinkAgent -- hello, join, verbs, release

Translates the wire protocol to GameServer calls and owns one DeviceBridge
-> LightSession per joined device. A granted join ships JoinResult.config
verbatim as /<dev>/role and builds that device's renderer from the same
blob; a denial ships the engine's own reason and hint.

Every inbound frame is handled inside a try/except so a malformed message
or a failing device can never reach the engine tick."
```

---

## Task 5: Frame streaming

Renders each joined device's session and emits `/<dev>/leds` when its frame changes. **See the deviation note above** — rendering happens on the tick loop, not on a 44 Hz `OutputLoop` thread.

**Files:**
- Modify: `devicelink/agent.py`
- Test: `tests/test_devicelink_frames.py`

**Interfaces:**
- Consumes: `DeviceLinkAgent` (Task 4), `luxaeterna.universe.Universe`, `LightSession.render_into(universe)`, `Universe.get_frame() -> bytearray`.
- Produces: `DeviceLinkAgent.poll()` additionally emits `/<dev>/leds` on change; `DeviceLinkAgent._universes: dict[str, Universe]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_devicelink_frames.py`:

```python
"""DeviceLinkAgent frame streaming: render on tick, emit only on change."""

import pytest

pytest.importorskip("luxaeterna")

from bits.test_bit import TestBit
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import FakeServer


@pytest.fixture
def joined():
    # Real wall-clock time cannot advance TestBit's 1.5 s welcome or the
    # LOADING->RUNNING transition inside a synchronous poll() loop -- the
    # whole test body runs in well under a millisecond. Same fake-clock
    # idiom as tests/test_device_bridge.py; nothing in this slice measures
    # timing, so a fast virtual clock is correct here.
    clk = iter([i * 2.0 for i in range(1000)]).__next__
    gs = GameServer({"test_bit": TestBit})
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk)
    gs.load_bit("test_bit")
    server.arrive("c1")
    server.deliver("c1", "/game/hello", "sss", ["ie1", "sim", "1"])
    agent.poll()
    server.deliver("c1", "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
    agent.poll()
    gs.run()
    server.sent.clear()
    return gs, server, agent


def test_emits_a_36_channel_frame(joined):
    gs, server, agent = joined
    for _ in range(5):
        agent.poll()
    frames = server.addressed("/ie1/leds")
    assert frames, "expected at least one LED frame"
    assert len(frames[0]["args"][0]) == 36
    assert all(0 <= v <= 255 for v in frames[0]["args"][0])


def test_unchanged_frame_is_sent_once(joined):
    """aurora breathes continuously, so a real session never renders the same
    frame twice. Substitute a constant renderer to test emit-on-change."""
    gs, server, agent = joined

    class ConstantSession:
        state = "running"

        def render_into(self, universe):
            universe.set_range(0, bytes([7] * 36))

    agent.bridges["ie1"].session = ConstantSession()
    for _ in range(4):
        agent.poll()
    frames = server.addressed("/ie1/leds")
    assert len(frames) == 1, "a constant frame must be sent once, not per tick"
    assert frames[0]["args"][0] == [7] * 36


def test_cue_changes_the_frame(joined):
    gs, server, agent = joined
    agent.poll()
    server.sent.clear()
    for cc in (0, 40, 80, 120):
        gs.on_light_cue("ie1", 0xB0, 74, cc)
        for _ in range(3):
            agent.poll()
    frames = [tuple(m["args"][0]) for m in server.addressed("/ie1/leds")]
    assert len(set(frames)) > 1, "hue sweep should produce distinct frames"


def test_released_device_stops_emitting(joined):
    gs, server, agent = joined
    gs.abort()
    server.sent.clear()
    for _ in range(3):
        agent.poll()
    assert server.addressed("/ie1/leds") == []


def test_a_raising_session_does_not_break_poll(joined):
    gs, server, agent = joined

    class Boom:
        state = "running"

        def render_into(self, universe):
            raise RuntimeError("boom")

    agent.bridges["ie1"].session = Boom()
    agent.poll()          # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_devicelink_frames.py -v`
Expected: FAIL — `AttributeError: 'DeviceLinkAgent' object has no attribute '_universes'`

- [ ] **Step 3: Add the universe map**

In `devicelink/agent.py`, add the import beside the existing ones:

```python
from luxaeterna.universe import Universe
```

In `__init__`, directly after `self.bridges: dict[str, DeviceBridge] = {}`:

```python
        self._universes: dict[str, Universe] = {}
        self._last_frames: dict[str, bytes] = {}
```

- [ ] **Step 4: Create a universe when a device joins**

In `_on_join`, replace the line `self.bridges[dev] = bridge` with:

```python
        self.bridges[dev] = bridge
        self._universes[dev] = Universe()
        self._last_frames.pop(dev, None)
```

- [ ] **Step 5: Drop the universe when a device is released**

In `_on_release`, directly after `bridge = self.bridges.pop(dev, None)`:

```python
        self._universes.pop(dev, None)
        self._last_frames.pop(dev, None)
```

- [ ] **Step 6: Render and emit at the end of `poll()`**

In `poll()`, after the inbound loop, add the call:

```python
        self._render_frames()
```

Then add the method, directly after `poll()`:

```python
    def _render_frames(self) -> None:
        """Render each joined device's session and emit /<dev>/leds when the
        frame actually changed. Rendering runs on the tick thread: the tick
        rate is the frame rate, and there is no second thread to race."""
        for dev, bridge in list(self.bridges.items()):
            universe = self._universes.get(dev)
            session = bridge.session
            if universe is None or session is None:
                continue
            try:
                session.render_into(universe)
            except Exception:
                logger.exception("render for %s failed; skipping frame", dev)
                continue
            frame = bytes(universe.get_frame()[:36])
            if frame == self._last_frames.get(dev):
                continue
            self._last_frames[dev] = frame
            try:
                self._send(dev, protocol.leds_event(dev, frame))
            except Exception:
                logger.exception("leds send for %s failed", dev)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_devicelink_frames.py -v`
Expected: 5 passed

- [ ] **Step 8: Run the full suite for regressions**

Run: `python3 -m pytest tests -v`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_frames.py
git commit -m "feat(devicelink): stream rendered LED frames as /<dev>/leds

Each joined device gets a Universe alongside its LightSession; poll()
renders every session and emits a 36-channel frame only when it changed,
so a static Shroom costs nothing on the wire.

Rendering runs on the tick thread rather than a luxaeterna OutputLoop:
there is no DMX backend in this path, so the loop would buy nothing but a
threading boundary. Tick rate is frame rate."
```

---

## Task 6: TestBit tilt handler, end-to-end smoke, and mm-terrarium docs

**Files:**
- Modify: `bits/test_bit.py`, `docs/MM_TERRARIUM.md`, `README.md`
- Create: `harness/devicelink_smoke.py`
- Test: `tests/test_devicelink_smoke.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces:
  - `TestBit.verb_handlers()` returning `{"tilt": ...}`; handler signature `(dev, args) -> [(dev, 0xB0, 74, cc)]`
  - `harness/devicelink_smoke.py`: `build(host, port, run_duration) -> (gs, server, agent)` and a `main()` CLI with `--host`, `--port`, `--seconds`, `--hold`

- [ ] **Step 1: Write the failing test**

Create `tests/test_devicelink_smoke.py`:

```python
"""End-to-end: a real websocket client drives registration, receives its
config blob, tilts, and watches the LEDs change."""

import json
import time

import pytest
from websockets.sync.client import connect

from harness.devicelink_smoke import build


@pytest.fixture
def rig():
    gs, server, agent = build(host="127.0.0.1", port=0, run_duration=60.0)
    yield gs, server, agent
    server.stop()


def _send(client, address, typespec, args):
    client.send(json.dumps({"timestamp": 0.0, "address": address,
                            "typespec": typespec, "args": args}))


def _collect(client, agent, gs, ticks=12):
    """Interleave engine ticks with reads so the sync client sees output."""
    out = []
    for _ in range(ticks):
        agent.poll()
        gs.tick(0.02)
        try:
            while True:
                out.append(json.loads(client.recv(timeout=0.05)))
        except TimeoutError:
            pass
    return out


def test_tilt_drives_a_visible_hue_change(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
        _send(client, "/game/hello", "sss", ["ie1", "sim", "1"])
        _send(client, "/game/join", "ss", ["ie1", "TEST_PLAYER_NODE"])
        msgs = _collect(client, agent, gs)

        roles = [m for m in msgs if m["address"] == "/ie1/role"]
        assert len(roles) == 1
        assert roles[0]["args"][0]["role"] == "player"

        gs.run()
        frames = []
        for cc in (0, 30, 60, 90, 120):
            _send(client, "/game/tilt", "sf", ["ie1", float(cc)])
            frames += [tuple(m["args"][0])
                       for m in _collect(client, agent, gs)
                       if m["address"] == "/ie1/leds"]

    assert len(set(frames)) > 1, "tilting should change the rendered frame"


def test_denied_join_reports_the_engine_reason(rig):
    gs, server, agent = rig
    gs.load_bit("test_bit")
    with connect(f"ws://127.0.0.1:{server.port}/ws") as client:
        _send(client, "/game/hello", "sss", ["ie9", "sim", "1"])
        _send(client, "/game/join", "ss", ["ie9", "NOPE"])
        msgs = _collect(client, agent, gs)
    denies = [m for m in msgs if m["address"] == "/ie9/deny"]
    assert denies[0]["args"][0] == "no such node"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_devicelink_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.devicelink_smoke'`

- [ ] **Step 3: Add the tilt handler to TestBit**

In `bits/test_bit.py`, add this method after `status()`:

```python
    def verb_handlers(self) -> dict:
        """Gameplay verbs beyond the fixed lifecycle set. `tilt` maps device
        tilt onto cc:74, which this Bit's `player` role binds to aurora's hue
        lane -- so tilting a device glides its Shroom's colour. Boundary
        rule 3: the Bit decides the light consequence, not the transport."""
        return {"tilt": self._on_tilt}

    def _on_tilt(self, dev: str, args: list) -> list:
        """args: [dev, gamma]. gamma is degrees in [-90, 90]."""
        gamma = float(args[1]) if len(args) > 1 else 0.0
        gamma = max(-90.0, min(90.0, gamma))
        cc = int(round((gamma + 90.0) / 180.0 * 127.0))
        return [(dev, 0xB0, 74, cc)]
```

- [ ] **Step 4: Write the smoke driver**

Create `harness/devicelink_smoke.py`:

```python
"""python -m harness.devicelink_smoke -- run Control with a live DeviceLink
so a browser Tuneshroom simulator can register and render.

    python -m harness.devicelink_smoke --hold
    python -m harness.devicelink_smoke --seconds 30 --host 0.0.0.0

Load a Bit from the Terrarium Console (or let this driver load TestBit),
then point the simulator at ws://<host>:<port>/ws.

Trust model: default bind is 127.0.0.1. --host 0.0.0.0 exposes the device
port to the LAN and is an explicit opt-in, never a default.
"""

from __future__ import annotations

import argparse
import time

from bits.test_bit import RUN_DURATION_SECONDS, TestBit
from control.engine import GameServer
from control.state import State
from devicelink.agent import DeviceLinkAgent
from devicelink.server import DeviceLinkServer

HOST, PORT = "127.0.0.1", 8771
TICK = 1.0 / 44.0


def build(host: str = HOST, port: int = PORT,
          run_duration: float = RUN_DURATION_SECONDS):
    """Construct engine + server + agent WITHOUT running a tick loop.

    Returns (game_server, server, agent). The server is already started and
    bound; pass port=0 for an ephemeral port in tests."""
    gs = GameServer({"test_bit": lambda: TestBit(run_duration=run_duration)})
    server = DeviceLinkServer(host=host, port=port)
    server.start()
    agent = DeviceLinkAgent(gs, server)
    return gs, server, agent


def _run_duration(args) -> float:
    if args.hold:
        return float("inf")
    return RUN_DURATION_SECONDS if args.seconds is None else args.seconds


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Serve DeviceLink for the Tuneshroom simulator.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="How long the Bit stays RUNNING before completing.")
    ap.add_argument("--hold", action="store_true",
                    help="Never auto-complete; serve until Ctrl-C.")
    ap.add_argument("--host", default=HOST,
                    help="Bind address. 0.0.0.0 exposes the device port to "
                         "the LAN -- explicit opt-in, no auth exists.")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    gs, server, agent = build(args.host, args.port, _run_duration(args))
    print(f"DeviceLink listening on ws://{args.host}:{server.port}/ws "
          f"(Ctrl-C to stop)")
    gs.load_bit("test_bit")
    gs.run()
    try:
        while True:
            agent.poll()
            gs.tick(TICK)
            if gs.state == State.IDLE and not args.hold:
                break
            time.sleep(TICK)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_devicelink_smoke.py -v`
Expected: 2 passed

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests -v`
Expected: all pass

- [ ] **Step 7: Update `README.md`**

In the *Planned layout* block, add this line directly after the `console/` line:

```
devicelink/  device-facing websocket transport (simulated Tuneshrooms)
```

- [ ] **Step 8: Update `docs/MM_TERRARIUM.md`**

Add a new subsystem section directly after the `### harness/` section:

```markdown
### `devicelink/` — the device-facing websocket transport (Slice 2)
Control's first device wire. The inbound sibling of `console/`, with the same
split: `DeviceLinkServer` (socket-only, drain-based) plus `DeviceLinkAgent`
(transport-agnostic brains, driven from the tick loop). It holds one
`DeviceBridge` → luxaeterna `LightSession` per joined device, ships
`JoinResult.config` verbatim as `/<dev>/role`, and streams rendered frames as
`/<dev>/leds` on change.

Messages are **JSON envelopes mirroring o2ws field-for-field**
(`timestamp`/`address`/`typespec`/`args`) — the vocabulary is real, the framing
is not, so the later swap to o2ws is mechanical. **Arco is not in this path**,
so nothing here may be read as a hop count or a latency figure. Same trust
model as the console: trusted LAN, no auth, `127.0.0.1` by default.

Slice 2 also **routed `Bit.verb_handlers()`**, which had been declared on the
`Bit` interface since the first slice but which `GameServer` never called.
`GameServer.data(dev, verb, args)` now dispatches to it and forwards emitted
light cues through the transport-owned `on_light_cue` sink — so a Bit still
decides the light consequence (boundary rule 3). `TestBit` gained a `tilt`
handler mapping tilt onto `cc:74`, making verb dispatch a tested behavior.

Driver: `python -m harness.devicelink_smoke --hold`.
```

Then, in *Not yet built / deferred*, replace the first bullet's opening sentence so it no longer claims there is no device wire:

```markdown
- **Real O2lite/pyarco transport wiring.** A device wire now exists
  (`devicelink/`, Slice 2) but it is a **direct websocket to Control, not
  o2lite through Arco** — no live O2 network, no Arco server, no clock sync.
  The whole suite still runs against fakes and localhost sockets.
```

- [ ] **Step 9: Commit**

```bash
git add bits/test_bit.py harness/devicelink_smoke.py \
        tests/test_devicelink_smoke.py README.md docs/MM_TERRARIUM.md
git commit -m "feat(harness): devicelink_smoke + TestBit tilt handler

TestBit gains a tilt verb handler mapping tilt onto cc:74, which its player
role already binds to aurora's hue lane -- so a tilt glides the Shroom's
colour without any new Bit. This also makes verb dispatch a tested behavior
rather than an assumption, the same reason the scored/jam role pair exists.

devicelink_smoke serves the whole stack for a browser simulator; its
headless test drives registration, the config blob, and a hue sweep over a
real socket. Docs updated for the new subsystem."
```

---

## Task 7: mm-tuneshroom — web target and the transport seam

**Repo:** `~/projects/mm-tuneshroom`. Branch from `main`: `git checkout -b claude/devicelink-simulator`.

**Files:**
- Create: `web/` (scaffolded), `lib/link/envelope.dart`, `lib/link/device_link.dart`, `lib/link/websocket_link.dart`
- Modify: `pubspec.yaml`
- Test: `test/envelope_test.dart`

**Interfaces:**
- Consumes: the wire vocabulary frozen in the File Structure table above.
- Produces:
  - `Envelope` — `double timestamp`, `String address`, `String typespec`, `List<dynamic> args`; `Envelope.fromJson(Map)`, `toJson()`, `Envelope.game(String verb, String typespec, List args)`
  - `String? parseDeviceAddress(String address, String dev)` — `/ie1/role` with dev `ie1` → `"role"`, else `null`
  - `abstract class DeviceLink` — `Future<void> connect()`, `void send(Envelope)`, `Stream<Envelope> get inbound`, `Future<void> close()`
  - `class WebSocketLink implements DeviceLink` — `WebSocketLink(Uri uri)`

**Nothing above `lib/link/` may import `dart:ffi`, `dart:io`, or `dart:html`.** That rule is what keeps every platform target buildable.

- [ ] **Step 1: Scaffold the web target and add the dependency**

```bash
cd ~/projects/mm-tuneshroom
git checkout -b claude/devicelink-simulator
flutter create --platforms=web .
flutter pub add web_socket_channel
```

Expected: a new `web/` directory; `pubspec.yaml` gains `web_socket_channel`.

- [ ] **Step 2: Write the failing test**

Create `test/envelope_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:mm_shrooms_app/link/envelope.dart';

void main() {
  test('round-trips through JSON', () {
    final env = Envelope(
        timestamp: 1.5,
        address: '/game/join',
        typespec: 'ss',
        args: const ['ie1', 'NODE_A']);
    final back = Envelope.fromJson(env.toJson());
    expect(back.address, '/game/join');
    expect(back.typespec, 'ss');
    expect(back.args, ['ie1', 'NODE_A']);
    expect(back.timestamp, 1.5);
  });

  test('defaults a missing timestamp to zero', () {
    final env = Envelope.fromJson(
        {'address': '/ie1/release', 'typespec': '', 'args': []});
    expect(env.timestamp, 0.0);
  });

  test('rejects a missing address', () {
    expect(() => Envelope.fromJson({'typespec': '', 'args': []}),
        throwsFormatException);
  });

  test('rejects a typespec/args arity mismatch', () {
    expect(
        () => Envelope.fromJson(
            {'address': '/game/join', 'typespec': 'ss', 'args': ['one']}),
        throwsFormatException);
  });

  test('game() builds a /game/<verb> address', () {
    final env = Envelope.game('tilt', 'sf', ['ie1', 30.0]);
    expect(env.address, '/game/tilt');
    expect(env.args, ['ie1', 30.0]);
  });

  test('parseDeviceAddress matches only this device', () {
    expect(parseDeviceAddress('/ie1/role', 'ie1'), 'role');
    expect(parseDeviceAddress('/ie1/leds', 'ie1'), 'leds');
    expect(parseDeviceAddress('/ie2/role', 'ie1'), isNull);
    expect(parseDeviceAddress('/game/join', 'ie1'), isNull);
    expect(parseDeviceAddress('/ie1/', 'ie1'), isNull);
  });
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `flutter test test/envelope_test.dart`
Expected: FAIL — `Error: Couldn't resolve the package 'mm_shrooms_app/link/envelope.dart'`

- [ ] **Step 4: Write the envelope codec**

Create `lib/link/envelope.dart`:

```dart
/// The DeviceLink wire shape: a JSON envelope mirroring o2ws field-for-field.
///
/// Counterpart of mm-terrarium `devicelink/protocol.py` — change both
/// together. Pure Dart: no platform imports, so this compiles for every
/// Flutter target and is testable without a browser.
class Envelope {
  final double timestamp;
  final String address;
  final String typespec;
  final List<dynamic> args;

  const Envelope({
    required this.timestamp,
    required this.address,
    required this.typespec,
    required this.args,
  });

  /// Build an outbound `/game/<verb>` message. Design Rule 2: the caller
  /// must pass `dev` as the first argument.
  factory Envelope.game(String verb, String typespec, List<dynamic> args) =>
      Envelope(
          timestamp: 0.0,
          address: '/game/$verb',
          typespec: typespec,
          args: args);

  factory Envelope.fromJson(Map<String, dynamic> json) {
    final address = json['address'];
    if (address is! String || address.isEmpty) {
      throw const FormatException('envelope needs a non-empty string address');
    }
    final typespec = json['typespec'] ?? '';
    if (typespec is! String) {
      throw const FormatException('typespec must be a string');
    }
    final args = json['args'] ?? const <dynamic>[];
    if (args is! List) {
      throw const FormatException('args must be a list');
    }
    if (typespec.length != args.length) {
      throw FormatException(
          'typespec "$typespec" does not match ${args.length} args');
    }
    final timestamp = json['timestamp'] ?? 0.0;
    if (timestamp is! num) {
      throw const FormatException('timestamp must be a number');
    }
    return Envelope(
        timestamp: timestamp.toDouble(),
        address: address,
        typespec: typespec,
        args: args);
  }

  Map<String, dynamic> toJson() => {
        'timestamp': timestamp,
        'address': address,
        'typespec': typespec,
        'args': args,
      };
}

/// `/ie1/role` with dev `ie1` -> `'role'`. Any other device, or a non-device
/// address, returns null.
String? parseDeviceAddress(String address, String dev) {
  final prefix = '/$dev/';
  if (!address.startsWith(prefix)) return null;
  final verb = address.substring(prefix.length);
  return verb.isEmpty ? null : verb;
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `flutter test test/envelope_test.dart`
Expected: 6 passed

- [ ] **Step 6: Write the transport interface**

Create `lib/link/device_link.dart`:

```dart
import 'envelope.dart';

/// The transport seam.
///
/// One implementation exists today — [WebSocketLink], which works on web and
/// native alike via package:web_socket_channel. A future FfiLink (native
/// o2lite through Arco) implements this same interface.
///
/// Nothing above lib/link/ may import dart:ffi, dart:io, or dart:html. That
/// rule is what keeps every platform target buildable from one codebase.
abstract class DeviceLink {
  Future<void> connect();
  void send(Envelope envelope);
  Stream<Envelope> get inbound;
  Future<void> close();
}
```

- [ ] **Step 7: Write the websocket implementation**

Create `lib/link/websocket_link.dart`:

```dart
import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'device_link.dart';
import 'envelope.dart';

/// DeviceLink over a websocket. package:web_socket_channel picks the right
/// implementation per platform, so this file needs no conditional imports.
class WebSocketLink implements DeviceLink {
  WebSocketLink(this.uri);

  final Uri uri;
  WebSocketChannel? _channel;
  final _inbound = StreamController<Envelope>.broadcast();

  @override
  Future<void> connect() async {
    final channel = WebSocketChannel.connect(uri);
    _channel = channel;
    await channel.ready;
    channel.stream.listen(
      (raw) {
        try {
          final decoded = jsonDecode(raw as String);
          if (decoded is Map<String, dynamic>) {
            _inbound.add(Envelope.fromJson(decoded));
          }
        } on FormatException {
          // Malformed frame: drop it. A bad message must never kill the link.
        }
      },
      onError: (_) {},
      onDone: () {},
    );
  }

  @override
  void send(Envelope envelope) {
    _channel?.sink.add(jsonEncode(envelope.toJson()));
  }

  @override
  Stream<Envelope> get inbound => _inbound.stream;

  @override
  Future<void> close() async {
    await _channel?.sink.close();
    await _inbound.close();
  }
}
```

- [ ] **Step 8: Verify the whole suite and that web builds**

Run: `flutter test`
Expected: all pass

Run: `flutter build web --debug`
Expected: build succeeds

- [ ] **Step 9: Commit**

```bash
git add web/ pubspec.yaml pubspec.lock lib/link/ test/envelope_test.dart
git commit -m "feat(link): web target + DeviceLink transport seam

Scaffolds the web platform target (ios/ was the only one present) and adds
the transport interface that makes one codebase target every platform.

dart:ffi does not exist on Flutter web, so the existing FFI o2lite client
cannot run in a browser. DeviceLink isolates that: WebSocketLink serves web
and native alike today, and a native FfiLink slots in behind the same
interface later. Nothing above lib/link/ may import a platform library.

Envelope mirrors devicelink/protocol.py field-for-field; the two are a
matched pair and change together."
```

---

## Task 8: mm-tuneshroom — Shroom display and simulator screen

**Files:**
- Create: `lib/sim/shroom_painter.dart`, `lib/sim/sim_controller.dart`, `lib/sim/sim_screen.dart`, `lib/sim_main.dart`
- Test: `test/shroom_painter_test.dart`, `test/sim_controller_test.dart`

**Interfaces:**
- Consumes: `DeviceLink`, `Envelope`, `parseDeviceAddress` (Task 7).
- Produces:
  - `List<Color> decodeGrbFrame(List<dynamic> channels)` — 36 ints (GRB) → 12 `Color`s
  - `class ShroomPainter extends CustomPainter` — `ShroomPainter(List<Color> pixels)`; 8 ring, 4 stem
  - `class SimController extends ChangeNotifier` — `SimController(link, dev)`, `Future<void> start()`, `void join(String node)`, `void sendTilt(double gamma)`, `List<Color> pixels`, `Map<String, dynamic>? roleConfig`, `String? denyReason`, `String status`

**Capability note:** luxaeterna's `shroom_capability()` is `pixel_count: 12`, `color_order: "GRB"`. Channel triples arrive **green, red, blue** — decoding them as RGB gives a plausible-looking but wrong image, so the decode test is load-bearing.

- [ ] **Step 1: Write the failing painter test**

Create `test/shroom_painter_test.dart`:

```dart
import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:mm_shrooms_app/sim/shroom_painter.dart';

void main() {
  test('decodes GRB triples, not RGB', () {
    // One pixel: green=10, red=200, blue=30.
    final channels = <int>[10, 200, 30, ...List.filled(33, 0)];
    final pixels = decodeGrbFrame(channels);
    expect(pixels.length, 12);
    expect(pixels[0].red, 200);
    expect(pixels[0].green, 10);
    expect(pixels[0].blue, 30);
  });

  test('produces twelve opaque pixels', () {
    final pixels = decodeGrbFrame(List.filled(36, 128));
    expect(pixels.length, 12);
    expect(pixels.every((c) => c.alpha == 255), isTrue);
  });

  test('pads a short frame with black rather than throwing', () {
    final pixels = decodeGrbFrame(const <int>[255, 255, 255]);
    expect(pixels.length, 12);
    expect(pixels[1], const Color(0xFF000000));
  });

  test('clamps out-of-range channel values', () {
    final pixels = decodeGrbFrame(<int>[-5, 300, 0, ...List.filled(33, 0)]);
    expect(pixels[0].green, 0);
    expect(pixels[0].red, 255);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `flutter test test/shroom_painter_test.dart`
Expected: FAIL — cannot resolve `sim/shroom_painter.dart`

- [ ] **Step 3: Write the painter**

Create `lib/sim/shroom_painter.dart`:

```dart
import 'dart:math' as math;

import 'package:flutter/material.dart';

const int kPixelCount = 12;
const int kRingCount = 8;

/// Decode a luxaeterna Shroom frame into pixel colours.
///
/// The capability is 12 pixels in **GRB** order (luxaeterna
/// `shroom_capability()`), so each triple arrives green, red, blue. Decoding
/// it as RGB yields a plausible-looking but wrong image — hence the test.
List<Color> decodeGrbFrame(List<dynamic> channels) {
  int at(int i) {
    if (i >= channels.length) return 0;
    final raw = channels[i];
    final value = raw is num ? raw.toInt() : 0;
    return value.clamp(0, 255);
  }

  return List<Color>.generate(kPixelCount, (i) {
    final base = i * 3;
    return Color.fromARGB(255, at(base + 1), at(base), at(base + 2));
  });
}

/// Draws the Shroom: an 8-LED ring with a 4-LED stem below it.
class ShroomPainter extends CustomPainter {
  ShroomPainter(this.pixels);

  final List<Color> pixels;

  @override
  void paint(Canvas canvas, Size size) {
    final centre = Offset(size.width / 2, size.height * 0.35);
    final radius = math.min(size.width, size.height) * 0.28;
    final dot = radius * 0.22;

    for (var i = 0; i < kRingCount && i < pixels.length; i++) {
      final angle = (i / kRingCount) * 2 * math.pi - math.pi / 2;
      final at = centre +
          Offset(math.cos(angle) * radius, math.sin(angle) * radius);
      canvas.drawCircle(at, dot, Paint()..color = pixels[i]);
    }

    final stemTop = centre.dy + radius * 1.35;
    final stemGap = dot * 2.6;
    for (var i = kRingCount; i < pixels.length; i++) {
      final at = Offset(centre.dx, stemTop + (i - kRingCount) * stemGap);
      canvas.drawCircle(at, dot, Paint()..color = pixels[i]);
    }
  }

  @override
  bool shouldRepaint(covariant ShroomPainter old) => old.pixels != pixels;
}
```

- [ ] **Step 4: Run painter tests to verify they pass**

Run: `flutter test test/shroom_painter_test.dart`
Expected: 4 passed

- [ ] **Step 5: Write the failing controller test**

Create `test/sim_controller_test.dart`:

```dart
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:mm_shrooms_app/link/device_link.dart';
import 'package:mm_shrooms_app/link/envelope.dart';
import 'package:mm_shrooms_app/sim/sim_controller.dart';

class FakeLink implements DeviceLink {
  final sent = <Envelope>[];
  final _controller = StreamController<Envelope>.broadcast();

  @override
  Future<void> connect() async {}

  @override
  void send(Envelope envelope) => sent.add(envelope);

  @override
  Stream<Envelope> get inbound => _controller.stream;

  @override
  Future<void> close() async => _controller.close();

  void arrive(Envelope envelope) => _controller.add(envelope);
}

void main() {
  late FakeLink link;
  late SimController controller;

  setUp(() async {
    link = FakeLink();
    controller = SimController(link, 'ie1');
    await controller.start();
  });

  test('start sends hello with dev first', () {
    expect(link.sent.single.address, '/game/hello');
    expect(link.sent.single.args.first, 'ie1');
  });

  test('join sends dev and node', () {
    controller.join('TEST_PLAYER_NODE');
    expect(link.sent.last.address, '/game/join');
    expect(link.sent.last.args, ['ie1', 'TEST_PLAYER_NODE']);
  });

  test('role message captures the config blob', () async {
    link.arrive(Envelope(
        timestamp: 0,
        address: '/ie1/role',
        typespec: 'b',
        args: [
          {
            'role': 'player',
            'class': 'SHARED',
            'scored': true,
            'light_manifest': {
              'bit_name': 'test_bit',
              'bit_version': '0.1',
              'role': 'player',
            },
          }
        ]));
    await Future<void>.delayed(Duration.zero);
    expect(controller.roleConfig!['role'], 'player');
    expect(controller.denyReason, isNull);
  });

  test('provenance reads bit_name from inside light_manifest', () async {
    link.arrive(Envelope(
        timestamp: 0,
        address: '/ie1/role',
        typespec: 'b',
        args: [
          {
            'role': 'player',
            'light_manifest': {
              'bit_name': 'test_bit',
              'bit_version': '0.1',
            },
          }
        ]));
    await Future<void>.delayed(Duration.zero);
    expect(controller.provenance, 'bit test_bit v0.1 · role player');
  });

  test('deny message captures the reason', () async {
    link.arrive(Envelope(
        timestamp: 0,
        address: '/ie1/deny',
        typespec: 'ss',
        args: const ['no such node', '']));
    await Future<void>.delayed(Duration.zero);
    expect(controller.denyReason, 'no such node');
  });

  test('leds message updates pixels', () async {
    link.arrive(Envelope(
        timestamp: 0,
        address: '/ie1/leds',
        typespec: 'b',
        args: [
          [10, 200, 30, ...List.filled(33, 0)]
        ]));
    await Future<void>.delayed(Duration.zero);
    expect(controller.pixels[0].red, 200);
  });

  test('ignores traffic addressed to another device', () async {
    link.arrive(Envelope(
        timestamp: 0,
        address: '/ie2/deny',
        typespec: 'ss',
        args: const ['not mine', '']));
    await Future<void>.delayed(Duration.zero);
    expect(controller.denyReason, isNull);
  });

  test('sendTilt carries dev then gamma', () {
    controller.sendTilt(30.0);
    expect(link.sent.last.address, '/game/tilt');
    expect(link.sent.last.args, ['ie1', 30.0]);
  });
}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `flutter test test/sim_controller_test.dart`
Expected: FAIL — cannot resolve `sim/sim_controller.dart`

- [ ] **Step 7: Write the controller**

Create `lib/sim/sim_controller.dart`:

```dart
import 'package:flutter/material.dart';

import '../link/device_link.dart';
import '../link/envelope.dart';
import 'shroom_painter.dart';

/// Wires a [DeviceLink] to simulator UI state. Holds no platform imports, so
/// it is testable with plain `flutter test` against a fake link.
class SimController extends ChangeNotifier {
  SimController(this._link, this.dev);

  final DeviceLink _link;
  final String dev;

  List<Color> pixels =
      List<Color>.filled(kPixelCount, const Color(0xFF000000));
  Map<String, dynamic>? roleConfig;
  String? denyReason;
  String status = 'connecting';

  /// Human-readable provenance for the granted role.
  ///
  /// `compose_role_config` stamps `bit_name`/`bit_version` **inside**
  /// `light_manifest`, not at the top level — the top level carries only
  /// `role`, `class`, and `scored`. Reading them from the top level yields
  /// nulls, so this accessor is the single place that knows the nesting.
  String get provenance {
    final config = roleConfig;
    if (config == null) return '';
    final manifest = config['light_manifest'] as Map<String, dynamic>?;
    final name = manifest?['bit_name'] ?? '?';
    final version = manifest?['bit_version'] ?? '?';
    return 'bit $name v$version · role ${config['role']}';
  }

  Future<void> start() async {
    _link.inbound.listen(_onEnvelope);
    await _link.connect();
    _link.send(Envelope.game('hello', 'sss', [dev, 'flutter-sim', '1']));
    status = 'connected';
    notifyListeners();
  }

  void join(String node) {
    denyReason = null;
    _link.send(Envelope.game('join', 'ss', [dev, node]));
  }

  /// gamma: device tilt in degrees, [-90, 90]. The Bit maps it to cc:74.
  void sendTilt(double gamma) =>
      _link.send(Envelope.game('tilt', 'sf', [dev, gamma]));

  void _onEnvelope(Envelope env) {
    final verb = parseDeviceAddress(env.address, dev);
    if (verb == null) return;
    switch (verb) {
      case 'role':
        roleConfig = Map<String, dynamic>.from(env.args.first as Map);
        denyReason = null;
        status = 'joined as ${roleConfig!['role']}';
      case 'deny':
        denyReason = env.args.first as String;
        status = 'denied';
      case 'leds':
        pixels = decodeGrbFrame(env.args.first as List);
      case 'release':
        roleConfig = null;
        status = 'released';
      case 'error':
        status = 'error: ${env.args.last}';
      default:
        return;
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _link.close();
    super.dispose();
  }
}
```

- [ ] **Step 8: Run controller tests to verify they pass**

Run: `flutter test test/sim_controller_test.dart`
Expected: 7 passed

- [ ] **Step 9: Write the screen and entry point**

Create `lib/sim/sim_screen.dart`:

```dart
import 'package:flutter/material.dart';

import 'shroom_painter.dart';
import 'sim_controller.dart';

/// The simulator surface: node picker, Shroom display, a tilt slider, and a
/// provenance panel showing which Bit and role produced what is on screen.
///
/// The slider is the desktop input path. Most desktop browsers have no
/// accelerometer, so without it the demo is untestable on a laptop — which is
/// the primary target for this slice. Task 9 adds the real sensor stream
/// alongside it.
class SimScreen extends StatefulWidget {
  const SimScreen({super.key, required this.controller, required this.nodes});

  final SimController controller;
  final List<String> nodes;

  @override
  State<SimScreen> createState() => _SimScreenState();
}

class _SimScreenState extends State<SimScreen> {
  double _tilt = 0;

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final config = controller.roleConfig;
        return Scaffold(
          backgroundColor: const Color(0xFF101014),
          appBar: AppBar(title: Text('Tuneshroom ${controller.dev}')),
          body: Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(12),
                child: Wrap(
                  spacing: 8,
                  children: [
                    for (final node in widget.nodes)
                      ElevatedButton(
                        onPressed: () => controller.join(node),
                        child: Text(node),
                      ),
                  ],
                ),
              ),
              Expanded(
                child: CustomPaint(
                  painter: ShroomPainter(controller.pixels),
                  child: const SizedBox.expand(),
                ),
              ),
              Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(controller.status,
                        style: const TextStyle(color: Colors.white70)),
                    if (controller.denyReason != null)
                      Text('denied: ${controller.denyReason}',
                          style: const TextStyle(color: Colors.orangeAccent)),
                    if (config != null)
                      Text(controller.provenance,
                          style: const TextStyle(color: Colors.white38)),
                    Row(
                      children: [
                        const Text('tilt',
                            style: TextStyle(color: Colors.white38)),
                        Expanded(
                          child: Slider(
                            min: -90,
                            max: 90,
                            value: _tilt,
                            onChanged: (v) {
                              setState(() => _tilt = v);
                              controller.sendTilt(v);
                            },
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}
```

Create `lib/sim_main.dart`:

```dart
/// Simulator entry point, deliberately separate from lib/main.dart so the
/// legacy app is untouched:
///
///   flutter run -d chrome -t lib/sim_main.dart --dart-define=DEV=ie1
import 'package:flutter/material.dart';

import 'link/websocket_link.dart';
import 'sim/sim_controller.dart';
import 'sim/sim_screen.dart';

const _dev = String.fromEnvironment('DEV', defaultValue: 'ie1');
const _url =
    String.fromEnvironment('LINK', defaultValue: 'ws://127.0.0.1:8771/ws');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final controller =
      SimController(WebSocketLink(Uri.parse(_url)), _dev);
  await controller.start();
  runApp(MaterialApp(
    debugShowCheckedModeBanner: false,
    home: SimScreen(
      controller: controller,
      nodes: const ['TEST_PLAYER_NODE', 'TEST_JAM_NODE'],
    ),
  ));
}
```

- [ ] **Step 10: Verify the suite and the web build**

Run: `flutter test`
Expected: all pass

Run: `flutter build web --debug -t lib/sim_main.dart`
Expected: build succeeds

- [ ] **Step 11: Commit**

```bash
git add lib/sim/ lib/sim_main.dart test/shroom_painter_test.dart test/sim_controller_test.dart
git commit -m "feat(sim): Shroom display, controller, and simulator screen

Decodes luxaeterna's 12-pixel GRB frames (green first -- decoding as RGB
gives a plausible but wrong image, so that test is load-bearing) and paints
an 8-ring/4-stem Shroom.

SimController holds no platform imports, so the whole join/deny/role/leds
path is testable against a fake link with plain flutter test. Entry point is
lib/sim_main.dart, separate from the legacy lib/main.dart."
```

---

## Task 9: mm-tuneshroom — tilt input and docs

**Files:**
- Create: `lib/sim/tilt_source.dart`
- Modify: `lib/sim_main.dart`, `CLAUDE.md`
- Test: covered by `test/sim_controller_test.dart` (`sendTilt`), plus a manual browser check

**Interfaces:**
- Consumes: `SimController.sendTilt(double)` (Task 8), `sensors_plus` (already in `pubspec.yaml`).
- Produces: `class TiltSource` — `TiltSource(SimController)`, `void start()`, `void stop()`

`sensors_plus` supports web via the browser's device-orientation events. On a desktop browser with no accelerometer the stream simply never fires, so the screen also carries a slider fallback — without it the demo is untestable on a laptop, which is the primary target.

- [ ] **Step 1: Write the tilt source**

Create `lib/sim/tilt_source.dart`:

```dart
import 'dart:async';
import 'dart:math' as math;

import 'package:sensors_plus/sensors_plus.dart';

import 'sim_controller.dart';

/// Streams device tilt into /game/tilt.
///
/// Throttled to ~20 Hz: the Bit maps tilt onto cc:74, and a coarse stream is
/// plenty because aurora glides between values rather than stepping.
class TiltSource {
  TiltSource(this._controller);

  final SimController _controller;
  StreamSubscription<AccelerometerEvent>? _sub;
  DateTime _last = DateTime.fromMillisecondsSinceEpoch(0);

  static const _minGap = Duration(milliseconds: 50);

  void start() {
    _sub = accelerometerEventStream().listen((event) {
      final now = DateTime.now();
      if (now.difference(_last) < _minGap) return;
      _last = now;
      // Roll angle from the gravity vector, in degrees, clamped to the
      // range the Bit expects.
      final gamma = math.atan2(event.x, event.z) * 180 / math.pi;
      _controller.sendTilt(gamma.clamp(-90.0, 90.0));
    }, onError: (_) {
      // No accelerometer (most desktop browsers): the slider fallback in
      // SimScreen is the input path there.
    });
  }

  void stop() {
    _sub?.cancel();
    _sub = null;
  }
}
```

- [ ] **Step 2: Start the tilt source in the entry point**

In `lib/sim_main.dart`, after `await controller.start();`:

```dart
  TiltSource(controller).start();
```

And add the import:

```dart
import 'sim/tilt_source.dart';
```

- [ ] **Step 3: Verify**

Run: `flutter test`
Expected: all pass

Run: `flutter analyze`
Expected: no errors

Run: `flutter build web --debug -t lib/sim_main.dart`
Expected: build succeeds

- [ ] **Step 4: Manual end-to-end check**

**RUN ON: MYCOLOGICAL** (terminal 1) — serve the Terrarium side:

```bash
cd ~/projects/mm-terrarium && python3 -m harness.devicelink_smoke --hold
```

**RUN ON: MYCOLOGICAL** (terminal 2) — run the simulator:

```bash
cd ~/projects/mm-tuneshroom && flutter run -d chrome -t lib/sim_main.dart
```

Expected: tap `TEST_PLAYER_NODE` → the provenance line reads `bit test_bit v0.1 · role player` → dragging the tilt slider glides the ring's hue. Tap `TEST_JAM_NODE` in a second tab → it joins and stays dark (the `jammer` role declares no light manifest).

- [ ] **Step 5: Update `CLAUDE.md`**

Add this section directly after the *O2 Messaging* section:

```markdown
### The Flutter Tuneshroom simulator (Slice 2)

`lib/sim/` plus `lib/link/` are a **new stack**, not a migration. Entry point
is `lib/sim_main.dart`; run it with
`flutter run -d chrome -t lib/sim_main.dart`. It speaks the real `/game/*` +
`/ie<N>/*` vocabulary against mm-terrarium's `devicelink/`
(`python3 -m harness.devicelink_smoke --hold`).

**`lib/link/` is the transport seam.** `dart:ffi` does not exist on Flutter
web, so the FFI o2lite client in `lib/ffi/` cannot run in a browser at all.
`DeviceLink` isolates that: `WebSocketLink` serves web and native alike today,
and a native `FfiLink` slots in behind the same interface if latency ever
demands it. **Nothing above `lib/link/` may import `dart:ffi`, `dart:io`, or
`dart:html`** — that rule is what keeps one codebase building for every
target.

`web/` was scaffolded in this slice; before it, `ios/` was the only platform
directory in the repo.

**`www/` and `lib/ffi/`, `lib/audio/`, `lib/sensors/`, `lib/core/`, `lib/ui/`
remain legacy M1a references** and are untouched. They stay until this stack
reproduces their behavior.
```

- [ ] **Step 6: Commit and open the PR**

```bash
git add lib/sim/ lib/sim_main.dart CLAUDE.md
git commit -m "feat(sim): tilt input driving /game/tilt, plus docs

Accelerometer tilt throttled to ~20 Hz becomes /game/tilt, which TestBit
maps onto cc:74 and aurora's hue lane. Desktop browsers have no
accelerometer, so the screen carries a slider fallback -- without it the
demo is untestable on a laptop, which is the primary target."
git push -u origin claude/devicelink-simulator
```

Then open the PR against `main`, titled `feat(sim): Flutter web Tuneshroom simulator over DeviceLink`, linking mm-terrarium's companion PR.

---

## Verification checklist

Before calling this plan done, confirm each spec success criterion:

| Spec §2 criterion | Proven by |
|---|---|
| 1. Registration + grant crosses a socket; blob byte-identical | `tests/test_devicelink_agent.py::test_granted_join_sends_role_blob_byte_identical`, `tests/test_devicelink_smoke.py::test_tilt_drives_a_visible_hue_change` |
| 2. Device renders light-manifest v2 from that blob | `tests/test_devicelink_frames.py::test_emits_a_36_channel_frame`, `test/shroom_painter_test.dart` |
| 3. Sensor input produces real `/game/*` traffic | `test/sim_controller_test.dart::sendTilt carries dev then gamma`, `tests/test_engine_data.py` |
| 4. Loop closes visibly (tilt → hue) | `tests/test_devicelink_frames.py::test_cue_changes_the_frame`, Task 9 Step 4 manual check |
| 5. Headless integration test over a real socket | `tests/test_devicelink_smoke.py` |
