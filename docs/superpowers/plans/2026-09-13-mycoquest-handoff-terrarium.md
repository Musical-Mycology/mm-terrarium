# MycoQuest handoff, Terrarium side Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give MycoQuest the four things its play path needs from the box: a keyed `GET /prepare` on the LAN static server, a `players` list on `bit_completed`, an `[uplink]` config table with an identity frame on connect, and a persisted replay journal of `bit_completed`; plus `lan_ip` in the resync frame.

**Architecture:** The prepare route follows `/start` exactly (server thread enqueues, `DeviceLinkAgent` drains on the tick thread) with one addition, a reply slot the handler waits on so the app can learn busy. The rule is a pure function in a new `control/prepare.py`; a `PrepareAuthority` applies it against the registry and the engine. On the uplink side `UplinkAgent` grows an identity frame, a journal, a durable-aware replay and an injected `lan_ip`, and `terrarium_boot` constructs it for the first time when `[uplink]` is present.

**Tech Stack:** Python 3.12 stdlib (`tomllib`, `http.server`, `queue`, `threading`), `websockets` (already a dependency), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-13-mycoquest-handoff-terrarium-design.md` (read it first; the plan argues from it).

## Global Constraints

- Run the suite through the project venv only: `.venv/bin/python -m pytest tests -q`. In a fresh worktree first `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`.
- `control/` never imports `uplink/`, `harness/`, luxaeterna, pyarco or o2litepy (pinned by test). `uplink/` never imports `harness/`.
- The engine is touched from the tick thread only; the www server thread only enqueues.
- Every persisted or outbound JSON payload goes through `control/wire_json.dumps()`; never bare `json.dumps`.
- The start key and the uplink secret never appear in a log line, a marker, an HTTP body, or a dataclass `repr`.
- Reserved dev id `"terrarium"` (`control.lobby.TERRARIUM_ADMIN`) is always admin and never a player.
- Constants from the spec, verbatim: `PREPARE_PATH = "/prepare"`, `PREPARE_QUEUE_MAX = 16`, `PREPARE_REPLY_TIMEOUT_S = 3.0`, journal cap `500`, journal file `uplink_journal.jsonl` directly under the runs dir, secret pattern `^[0-9a-f]{64}$`.
- No em dashes anywhere in prose, comments, or docs.
- Commit after every task with a conventional-commit subject; no attribution lines.

---

### Task 1: `control/prepare.py`, the pure prepare rule and the observer record

**Files:**
- Create: `control/prepare.py`
- Modify: `control/engine.py` (add `notify_prepare_requested` next to `notify_lobby`, around line 490)
- Test: `tests/test_prepare.py`

**Interfaces:**
- Consumes: `control.state.State`.
- Produces (used by Tasks 2, 3, 4, 5):
  - `PrepareReply` mutable dataclass: `done: threading.Event`, `accepted: bool = False`, `reason: str | None = None`, `visible: bool = False`
  - `PrepareRequest(key: str | None, bit: str, dev: str | None, source: str, reply: PrepareReply)` frozen; `key` is `field(repr=False)`
  - `PrepareDecision(accepted: bool, reason: str | None, action: str, visible: bool)` frozen; `action` in `"load" | "noop" | "none"`
  - `decide_prepare(*, room_ready, state, loaded_bit, bit, known, when, expected_key, key) -> PrepareDecision`
  - `PrepareRequested(source: str, source_dev: str | None, bit: str, accepted: bool, reason: str | None)` frozen
  - `GameServer.notify_prepare_requested(record) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prepare.py
"""control/prepare.py: the LAN prepare rule (spec section 4.2)."""
import threading

from control.prepare import (PrepareDecision, PrepareReply, PrepareRequest,
                             PrepareRequested, decide_prepare)
from control.state import State


def _decide(**over):
    base = dict(room_ready=True, state=State.IDLE, loaded_bit=None,
                bit="MetronomeBit", known=True, when="admin",
                expected_key="metro-dev", key="metro-dev")
    base.update(over)
    return decide_prepare(**base)


def test_no_room_is_a_visible_refusal_before_anything_else():
    d = _decide(room_ready=False, key="wrong")
    assert d == PrepareDecision(False, "no room loaded", "none", True)


def test_unknown_bit_is_a_silent_bad_key():
    d = _decide(known=False, when=None, expected_key=None)
    assert d == PrepareDecision(False, "bad key", "none", False)


def test_non_admin_start_condition_is_a_silent_bad_key():
    assert _decide(when="players").visible is False
    assert _decide(when="players").reason == "bad key"


def test_missing_or_wrong_key_is_silent():
    assert _decide(expected_key=None) == PrepareDecision(False, "bad key", "none", False)
    assert _decide(key=None) == PrepareDecision(False, "bad key", "none", False)
    assert _decide(key="nope") == PrepareDecision(False, "bad key", "none", False)


def test_idle_loads():
    assert _decide() == PrepareDecision(True, None, "load", True)


def test_setup_with_the_same_bit_is_a_noop():
    d = _decide(state=State.SETUP, loaded_bit="MetronomeBit")
    assert d == PrepareDecision(True, None, "noop", True)


def test_setup_with_a_different_bit_is_busy():
    d = _decide(state=State.SETUP, loaded_bit="TestBit")
    assert d == PrepareDecision(False, "busy", "none", True)


def test_every_other_state_is_busy():
    for state in (State.LOADING, State.LOADED, State.RUNNING,
                  State.COMPLETING, State.UNLOADING):
        d = _decide(state=state, loaded_bit="MetronomeBit")
        assert d == PrepareDecision(False, "busy", "none", True), state


def test_bad_key_is_checked_before_busy():
    d = _decide(state=State.RUNNING, loaded_bit="MetronomeBit", key="nope")
    assert d.visible is False


def test_request_repr_never_carries_the_key():
    req = PrepareRequest("secret-key", "MetronomeBit", "gem-1", "web:gem-1",
                         PrepareReply(threading.Event()))
    assert "secret-key" not in repr(req)
    assert "secret-key" not in str(req)


def test_requested_record_shape():
    rec = PrepareRequested("web:gem-1", "gem-1", "MetronomeBit", False, "busy")
    assert rec.bit == "MetronomeBit" and rec.reason == "busy"


def test_game_server_notifies_prepare_observers():
    from control.engine import GameServer
    from bits.test.test_bit import TestBit
    gs = GameServer({"test_bit": TestBit})
    seen = []

    class Obs:
        def on_prepare_requested(self, record):
            seen.append(record)

    gs.add_observer(Obs())
    rec = PrepareRequested("web:terrarium", "terrarium", "test_bit", True, None)
    gs.notify_prepare_requested(rec)
    assert seen == [rec]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prepare.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.prepare'`

- [ ] **Step 3: Write the module and the engine hook**

```python
# control/prepare.py
"""The LAN prepare rule: what the Terrarium does when MycoQuest asks it to
load a Bit into the lobby (spec docs/superpowers/specs/
2026-09-13-mycoquest-handoff-terrarium-design.md, section 4).

Pure stdlib. `decide_prepare` is the rule; `PrepareAuthority` (below, Task
2) applies it against the registry and the engine. Nothing here touches a
socket: harness/www_server.py enqueues a PrepareRequest on the server
thread and devicelink/agent.py drains it on the tick thread, the same shape
as the web start queue.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from control.state import State

ACTION_LOAD = "load"
ACTION_NOOP = "noop"
ACTION_NONE = "none"

REASON_NO_ROOM = "no room loaded"
REASON_BAD_KEY = "bad key"
REASON_BUSY = "busy"


@dataclass
class PrepareReply:
    """The slot the www handler waits on. Filled by the drain."""
    done: threading.Event
    accepted: bool = False
    reason: str | None = None
    visible: bool = False


@dataclass(frozen=True)
class PrepareRequest:
    """One queued web prepare (harness/www_server.py -> DeviceLinkAgent).
    `key` is excluded from repr so a logged request never leaks it."""
    key: str | None = field(repr=False)
    bit: str
    dev: str | None
    source: str
    reply: PrepareReply


@dataclass(frozen=True)
class PrepareDecision:
    accepted: bool
    reason: str | None
    action: str            # ACTION_LOAD | ACTION_NOOP | ACTION_NONE
    visible: bool          # may the refusal reason reach the caller


@dataclass(frozen=True)
class PrepareRequested:
    """The engine observer record for every prepare attempt."""
    source: str
    source_dev: str | None
    bit: str
    accepted: bool
    reason: str | None


def decide_prepare(*, room_ready: bool, state: State, loaded_bit: str | None,
                   bit: str, known: bool, when: str | None,
                   expected_key: str | None, key: str | None) -> PrepareDecision:
    """Spec section 4.2, evaluated in order. A bad key (or an unknown Bit,
    or a Bit that does not take an admin start) is refused silently so a
    stranger learns nothing; busy and no-room are visible because the app
    needs them."""
    if not room_ready:
        return PrepareDecision(False, REASON_NO_ROOM, ACTION_NONE, True)
    if (not known or when != "admin" or not expected_key
            or key is None or key != expected_key):
        return PrepareDecision(False, REASON_BAD_KEY, ACTION_NONE, False)
    if state is State.IDLE:
        return PrepareDecision(True, None, ACTION_LOAD, True)
    if state is State.SETUP and loaded_bit == bit:
        return PrepareDecision(True, None, ACTION_NOOP, True)
    return PrepareDecision(False, REASON_BUSY, ACTION_NONE, True)
```

In `control/engine.py`, directly after `notify_lobby`:

```python
    def notify_prepare_requested(self, record) -> None:
        """Let the prepare authority (control/prepare.py) announce every
        prepare attempt through the engine's observer list, the same way
        request_start fires on_start_requested."""
        self._notify("on_prepare_requested", record)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prepare.py -q`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add control/prepare.py control/engine.py tests/test_prepare.py
git commit -m "feat(prepare): pure LAN prepare rule and observer record"
```

---

### Task 2: `PrepareAuthority`, applying the rule against registry and engine

**Files:**
- Modify: `control/prepare.py` (append)
- Test: `tests/test_prepare.py` (append)

**Interfaces:**
- Consumes: Task 1 types; `control.bit_registry.BitRegistry` (`packages[name].config.start.when/.key`, `resolve_config(name)`); `control.engine.GameServer` (`state`, `bit_name`, `load_bit(name, config=)`, `notify_prepare_requested`); `control.terrarium.TerrariumState.ROOM_READY`; `control.bit_config.ManifestError`; `control.engine.BitLoadError, InvalidTransition`.
- Produces (used by Tasks 4 and 5): `PrepareAuthority(game_server, registry, terrarium=None)` with `request(req: PrepareRequest) -> PrepareDecision`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prepare.py`:

```python
from pathlib import Path

from control.bit_registry import BitRegistry
from control.engine import GameServer
from control.prepare import PrepareAuthority
from control.terrarium import TerrariumState

PREP_MANIFEST = """
[bit]
name = "PrepBit"
entry = "prep_bit:PrepBit"
requires_terrarium_api = 1
[start]
when = "admin"
key = "k"
min_scored = 0
"""

PLAYERS_MANIFEST = PREP_MANIFEST.replace('name = "PrepBit"', 'name = "PlayersBit"') \
    .replace('entry = "prep_bit:PrepBit"', 'entry = "prep_bit:PlayersBit"') \
    .replace('when = "admin"', 'when = "players"')

DISABLED_MANIFEST = PREP_MANIFEST.replace('name = "PrepBit"', 'name = "OffBit"') \
    .replace('entry = "prep_bit:PrepBit"', 'entry = "prep_bit:OffBit"') \
    .replace("[bit]\n", "[bit]\nenabled = false\n")

MODULE = ("from bits.test.test_bit import TestBit\n"
          "class PrepBit(TestBit):\n    pass\n"
          "class PlayersBit(TestBit):\n    pass\n"
          "class OffBit(TestBit):\n    pass\n")


def _pkg(root, dirname, manifest):
    d = root / dirname
    d.mkdir(parents=True)
    (d / "bit.toml").write_text(manifest)
    (d / "__init__.py").write_text("")
    (d / "prep_bit.py").write_text(MODULE)


class _Terrarium:
    def __init__(self, state=TerrariumState.ROOM_READY):
        self.state = state


class _Recorder:
    def __init__(self):
        self.records = []

    def on_prepare_requested(self, record):
        self.records.append(record)


def _rig(tmp_path, terrarium=None):
    _pkg(tmp_path, "prep", PREP_MANIFEST)
    _pkg(tmp_path, "players", PLAYERS_MANIFEST)
    _pkg(tmp_path, "off", DISABLED_MANIFEST)
    registry = BitRegistry.scan([tmp_path])
    gs = GameServer(registry.lazy_class_map())
    rec = _Recorder()
    gs.add_observer(rec)
    auth = PrepareAuthority(gs, registry,
                            terrarium if terrarium is not None else _Terrarium())
    return gs, auth, rec


def _req(bit="PrepBit", key="k", dev="gem-1"):
    return PrepareRequest(key, bit, dev, f"web:{dev}", PrepareReply(threading.Event()))


def test_authority_loads_into_idle_with_the_resolved_config(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    d = auth.request(_req())
    assert d.accepted and d.action == "load"
    assert gs.state is State.SETUP and gs.bit_name == "PrepBit"
    assert gs.bit.config.start.key == "k"
    assert rec.records[-1] == PrepareRequested("web:gem-1", "gem-1", "PrepBit", True, None)


def test_authority_noops_on_the_same_bit_in_setup(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    auth.request(_req())
    bit_before = gs.bit
    d = auth.request(_req())
    assert d.accepted and d.action == "noop"
    assert gs.bit is bit_before


def test_authority_reports_busy_for_a_different_bit_in_setup(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    auth.request(_req())
    d = auth.request(_req(bit="PlayersBit", key="k"))
    # PlayersBit's start is "players", so this is a silent bad key, not busy
    assert d.visible is False
    gs2, auth2, rec2 = _rig(tmp_path / "two")
    auth2.request(_req())
    gs2.bit_name = "Other"          # a different Bit occupies SETUP
    d2 = auth2.request(_req())
    assert d2 == PrepareDecision(False, "busy", "none", True)


def test_authority_never_loads_on_a_bad_key_and_still_records(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    d = auth.request(_req(key="wrong"))
    assert d.visible is False and gs.state is State.IDLE
    assert rec.records[-1].accepted is False and rec.records[-1].reason == "bad key"
    assert "wrong" not in repr(rec.records[-1])


def test_authority_treats_an_unknown_bit_as_a_bad_key(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    d = auth.request(_req(bit="NoSuchBit"))
    assert d == PrepareDecision(False, "bad key", "none", False)
    assert gs.state is State.IDLE


def test_authority_refuses_without_a_room(tmp_path):
    gs, auth, rec = _rig(tmp_path, terrarium=_Terrarium(TerrariumState.NO_ROOM))
    d = auth.request(_req())
    assert d == PrepareDecision(False, "no room loaded", "none", True)
    assert gs.state is State.IDLE


def test_authority_without_a_terrarium_treats_the_room_as_ready(tmp_path):
    _pkg(tmp_path, "prep", PREP_MANIFEST)
    registry = BitRegistry.scan([tmp_path])
    gs = GameServer(registry.lazy_class_map())
    auth = PrepareAuthority(gs, registry)
    assert auth.request(_req()).accepted


def test_authority_turns_a_disabled_package_into_a_visible_reason(tmp_path):
    gs, auth, rec = _rig(tmp_path)
    d = auth.request(_req(bit="OffBit"))
    assert d.accepted is False and d.visible is True
    assert "disabled" in d.reason
    assert gs.state is State.IDLE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prepare.py -q`
Expected: FAIL with `ImportError: cannot import name 'PrepareAuthority'`

- [ ] **Step 3: Append the authority**

Append to `control/prepare.py`:

```python
import logging

from control.bit_config import ManifestError
from control.engine import BitLoadError, GameServer, InvalidTransition
from control.terrarium import TerrariumState

logger = logging.getLogger(__name__)


class PrepareAuthority:
    """Applies decide_prepare against the registry and the engine. Called
    on the tick thread only (DeviceLinkAgent's drain). Never raises; every
    outcome is a PrepareDecision and every attempt fires
    on_prepare_requested through the engine."""

    def __init__(self, game_server: GameServer, registry, terrarium=None) -> None:
        self.game_server = game_server
        self.registry = registry
        self.terrarium = terrarium

    def _room_ready(self) -> bool:
        return (self.terrarium is None
                or self.terrarium.state is TerrariumState.ROOM_READY)

    def request(self, req: PrepareRequest) -> PrepareDecision:
        gs = self.game_server
        pkg = self.registry.packages.get(req.bit)
        cond = pkg.config.start if pkg is not None else None
        decision = decide_prepare(
            room_ready=self._room_ready(), state=gs.state,
            loaded_bit=gs.bit_name, bit=req.bit, known=pkg is not None,
            when=cond.when if cond is not None else None,
            expected_key=cond.key if cond is not None else None,
            key=req.key)
        if decision.action == ACTION_LOAD:
            try:
                cfg = self.registry.resolve_config(req.bit)
                gs.load_bit(req.bit, config=cfg)
            except (ManifestError, KeyError, BitLoadError,
                    InvalidTransition) as exc:
                decision = PrepareDecision(False, str(exc), ACTION_NONE, True)
        gs.notify_prepare_requested(PrepareRequested(
            source=req.source, source_dev=req.dev, bit=req.bit,
            accepted=decision.accepted, reason=decision.reason))
        return decision
```

Move the `import logging` and the three `from control...` lines up to the module's import block so the file has one import section; `control/engine.py` importing nothing from `control/prepare.py` keeps the dependency one-way.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prepare.py -q`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add control/prepare.py tests/test_prepare.py
git commit -m "feat(prepare): PrepareAuthority applies the rule against registry and engine"
```

---

### Task 3: `GET /prepare` on the LAN static server

**Files:**
- Modify: `harness/www_server.py` (constants at top, `_QuietHandler.__init__`/`do_GET`, `WwwServer.__init__`/`start`)
- Test: `tests/test_www_server.py` (append)

**Interfaces:**
- Consumes: Task 1 `PrepareRequest`, `PrepareReply`; `control.lobby.TERRARIUM_ADMIN`.
- Produces (used by Task 5): `WwwServer.prepare_requests: queue.Queue` (maxsize `PREPARE_QUEUE_MAX`), module constants `PREPARE_PATH`, `PREPARE_QUEUE_MAX`, `PREPARE_REPLY_TIMEOUT_S`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_www_server.py`:

```python
import threading
from urllib.parse import quote

from control.prepare import PrepareRequest


def _answer(www, *, accepted, reason=None, visible=True):
    """Stand in for DeviceLinkAgent's drain on a helper thread: take one
    request, fill its reply, release the handler."""
    taken = []

    def drain():
        req = www.prepare_requests.get(timeout=2.0)
        taken.append(req)
        req.reply.accepted = accepted
        req.reply.reason = reason
        req.reply.visible = visible
        req.reply.done.set()

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    return taken, t


def _get(www, query):
    url = f"http://127.0.0.1:{www.port}/prepare?{query}"
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read().decode()
    except HTTPError as err:
        return err.code, err.read().decode()


def test_prepare_accept_is_202_and_queues_a_loopback_hit_as_the_terrarium(www):
    taken, t = _answer(www, accepted=True)
    status, body = _get(www, "key=abc&bit=MetronomeBit&dev=gem-1")
    t.join(2.0)
    assert (status, body) == (202, "prepare requested\n")
    assert "abc" not in body
    req = taken[0]
    assert isinstance(req, PrepareRequest)
    assert (req.key, req.bit, req.dev, req.source) == (
        "abc", "MetronomeBit", TERRARIUM_ADMIN, "web:terrarium")


def test_prepare_visible_refusal_is_409_with_the_reason(www):
    _, t = _answer(www, accepted=False, reason="busy", visible=True)
    status, body = _get(www, "key=abc&bit=MetronomeBit")
    t.join(2.0)
    assert (status, body) == (409, "busy\n")


def test_prepare_silent_refusal_looks_exactly_like_an_accept(www):
    _, t = _answer(www, accepted=False, reason="bad key", visible=False)
    status, body = _get(www, "key=wrong&bit=MetronomeBit")
    t.join(2.0)
    assert (status, body) == (202, "prepare requested\n")
    assert "wrong" not in body and "bad key" not in body


def test_prepare_undrained_request_is_503(www, monkeypatch):
    from harness import www_server
    monkeypatch.setattr(www_server, "PREPARE_REPLY_TIMEOUT_S", 0.05)
    status, body = _get(www, "key=abc&bit=MetronomeBit")
    assert (status, body) == (503, "prepare not drained\n")
    www.prepare_requests.get_nowait()     # it was queued, nobody answered


def test_prepare_labels_a_remote_hit_by_dev_or_anonymous(www, monkeypatch):
    from harness import www_server
    monkeypatch.setattr(www_server, "_LOOPBACK", ())
    taken, t = _answer(www, accepted=True)
    _get(www, "key=k&bit=B&dev=gem-2")
    t.join(2.0)
    assert (taken[0].dev, taken[0].source) == ("gem-2", "web:gem-2")
    taken, t = _answer(www, accepted=True)
    _get(www, "key=k&bit=B")
    t.join(2.0)
    assert (taken[0].dev, taken[0].source) == (None, "web:anonymous")


def test_prepare_missing_key_is_queued_with_none(www):
    taken, t = _answer(www, accepted=False, reason="bad key", visible=False)
    _get(www, "bit=B")
    t.join(2.0)
    assert taken[0].key is None and taken[0].bit == "B"


def test_prepare_queue_full_is_503(www, monkeypatch):
    from harness import www_server
    monkeypatch.setattr(www_server, "PREPARE_REPLY_TIMEOUT_S", 0.05)
    for _ in range(www_server.PREPARE_QUEUE_MAX):
        www.prepare_requests.put_nowait(object())
    status, body = _get(www, "key=k&bit=B")
    assert (status, body) == (503, "prepare queue full\n")


def test_prepare_is_404_when_not_wired(tmp_path):
    from harness.www_server import WwwServer
    server = WwwServer(str(tmp_path), host="127.0.0.1", port=0)
    server.prepare_requests = None
    server.start()
    try:
        status, _ = _get(server, "key=k&bit=B")
    finally:
        server.stop()
    assert status == 404


def test_prepare_key_never_reaches_a_log_line(www, caplog):
    import logging
    _, t = _answer(www, accepted=False, reason="busy", visible=True)
    with caplog.at_level(logging.DEBUG):
        _get(www, "key=" + quote("hunter2") + "&bit=B")
    t.join(2.0)
    assert "hunter2" not in caplog.text
```

`HTTPError.read()` on a 4xx/5xx from `send_error` returns the stdlib HTML error page, not our plain text, so the handler must write the 409 and 503 bodies itself (see Step 3); the tests above assert the plain bodies.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_www_server.py -q -k prepare`
Expected: FAIL (404 or `AttributeError: 'WwwServer' object has no attribute 'prepare_requests'`)

- [ ] **Step 3: Add the route**

In `harness/www_server.py`:

```python
from control.lobby import StartRequest, TERRARIUM_ADMIN
from control.prepare import PrepareReply, PrepareRequest

# The documented port a QR poster points at (spec section 4.1).
WWW_PORT = 8788

# The two dynamic routes this server answers; everything else is a static
# file under `directory`. /prepare (spec 2026-09-13 section 4.1) is /start
# with a reply slot: the handler waits for the tick thread's answer so the
# app can learn busy.
START_PATH = "/start"
PREPARE_PATH = "/prepare"
_LOOPBACK = ("127.0.0.1", "::1")
START_QUEUE_MAX = 16
PREPARE_QUEUE_MAX = 16
PREPARE_REPLY_TIMEOUT_S = 3.0
```

Handler changes:

```python
    def __init__(self, *args, start_requests=None, prepare_requests=None,
                 **kwargs):
        self._start_requests = start_requests
        self._prepare_requests = prepare_requests
        super().__init__(*args, **kwargs)

    def _plain(self, status: int, text: str) -> None:
        body = (text + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _source(self, dev):
        if self.client_address[0] in _LOOPBACK:
            return TERRARIUM_ADMIN, "web:terrarium"
        return dev, (f"web:{dev}" if dev else "web:anonymous")

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == PREPARE_PATH:
            return self._do_prepare(parsed)
        if parsed.path != START_PATH:
            return super().do_GET()
        params = parse_qs(parsed.query)
        key = params.get("key", [""])[0]
        dev, source = self._source(params.get("dev", [""])[0] or None)
        if self._start_requests is None:
            self.send_error(404, "start is not wired on this server")
            return
        try:
            self._start_requests.put_nowait(StartRequest(key, dev, source))
        except queue.Full:
            self.send_error(503, "start queue full")
            return
        self._plain(202, "start requested")

    def _do_prepare(self, parsed):
        if self._prepare_requests is None:
            self.send_error(404, "prepare is not wired on this server")
            return
        params = parse_qs(parsed.query)
        key = params.get("key", [None])[0]
        bit = params.get("bit", [""])[0]
        dev, source = self._source(params.get("dev", [""])[0] or None)
        reply = PrepareReply(threading.Event())
        try:
            self._prepare_requests.put_nowait(
                PrepareRequest(key, bit, dev, source, reply))
        except queue.Full:
            self._plain(503, "prepare queue full")
            return
        if not reply.done.wait(PREPARE_REPLY_TIMEOUT_S):
            self._plain(503, "prepare not drained")
            return
        if reply.accepted or not reply.visible:
            self._plain(202, "prepare requested")
            return
        self._plain(409, reply.reason or "refused")
```

`PREPARE_REPLY_TIMEOUT_S` is read as a module global inside `_do_prepare` (not bound as a default argument) so tests can monkeypatch it. Add `import threading` to the imports. The `/start` branch keeps its exact previous behaviour; refactoring its body through `_plain` and `_source` is fine because the existing start tests pin it. `WwwServer`:

```python
        self.start_requests: queue.Queue = queue.Queue(maxsize=START_QUEUE_MAX)
        self.prepare_requests: queue.Queue = queue.Queue(maxsize=PREPARE_QUEUE_MAX)
```

and in `start()` pass `prepare_requests=self.prepare_requests` to the `functools.partial`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_www_server.py -q`
Expected: all pass (existing start tests included)

- [ ] **Step 5: Commit**

```bash
git add harness/www_server.py tests/test_www_server.py
git commit -m "feat(www): GET /prepare with a reply slot beside /start"
```

---

### Task 4: the drain in `DeviceLinkAgent` and the Console log line

**Files:**
- Modify: `devicelink/agent.py` (attributes near line 249, `poll()` near line 725, new `_drain_prepare_requests` after `_drain_start_requests`)
- Modify: `console/agent.py` (new `on_prepare_requested` after `on_start_requested`, around line 964)
- Test: `tests/test_lobby_agent.py` (append), `tests/test_console_agent.py` (append; if the Console log tests live in another file, add there and say so in the commit)

**Interfaces:**
- Consumes: Task 1 `PrepareRequest`, `PrepareReply`, `PrepareRequested`; Task 2 `PrepareAuthority`.
- Produces (used by Task 5): `DeviceLinkAgent.prepare_requests: queue.Queue | None`, `DeviceLinkAgent.prepare_authority: PrepareAuthority | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_lobby_agent.py`:

```python
import threading

from control.prepare import PrepareReply, PrepareRequest


class _Authority:
    def __init__(self, decision=None, raises=None):
        self.decision = decision
        self.raises = raises
        self.seen = []

    def request(self, req):
        self.seen.append(req)
        if self.raises is not None:
            raise self.raises
        return self.decision


def _prepare_req():
    return PrepareRequest("k", "TestBit", "gem-1", "web:gem-1",
                          PrepareReply(threading.Event()))


def test_prepare_queue_is_drained_on_poll_and_the_reply_is_filled(monkeypatch):
    from control.prepare import PrepareDecision
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    auth = _Authority(PrepareDecision(False, "busy", "none", True))
    agent.prepare_requests = queue.Queue()
    agent.prepare_authority = auth
    req = _prepare_req()
    agent.prepare_requests.put(req)
    agent.poll()
    assert auth.seen == [req]
    assert req.reply.done.is_set()
    assert (req.reply.accepted, req.reply.reason, req.reply.visible) == (False, "busy", True)


def test_prepare_without_an_authority_answers_not_wired(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    agent.prepare_requests = queue.Queue()
    req = _prepare_req()
    agent.prepare_requests.put(req)
    agent.poll()
    assert req.reply.done.is_set()
    assert (req.reply.accepted, req.reply.reason, req.reply.visible) == (
        False, "prepare is not wired", True)


def test_prepare_raising_authority_answers_failed_and_keeps_polling(monkeypatch):
    gs, server, agent, audio, sessions, clk = _rig(monkeypatch, _admin_cfg())
    agent.prepare_requests = queue.Queue()
    agent.prepare_authority = _Authority(raises=RuntimeError("boom"))
    req = _prepare_req()
    agent.prepare_requests.put(req)
    agent.poll()
    assert req.reply.done.is_set()
    assert (req.reply.accepted, req.reply.reason, req.reply.visible) == (
        False, "prepare failed", True)
    agent.poll()      # nothing left, no raise
```

Console log test (add to the file that already tests `on_start_requested`; find it with `grep -rn "start refused" tests/`):

```python
def test_prepare_attempts_are_logged_to_the_console():
    from control.prepare import PrepareRequested
    agent, server, gs = _console_rig()     # the file's existing rig helper
    agent.on_prepare_requested(PrepareRequested("web:gem-1", "gem-1", "MetronomeBit", True, None))
    agent.on_prepare_requested(PrepareRequested("web:gem-2", "gem-2", "MetronomeBit", False, "busy"))
    logs = [m for m in server.broadcasts if m.get("event") == "log"]
    assert logs[-2]["message"] == "prepare MetronomeBit from web:gem-1: accepted"
    assert logs[-1]["message"] == "prepare MetronomeBit from web:gem-2: refused (busy)"
    assert logs[-2]["level"] == "info" and logs[-1]["level"] == "warn"
```

Adapt `_console_rig` and `server.broadcasts` to whatever names the existing `on_start_requested` test in that file uses; the assertion strings above are the contract.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_lobby_agent.py -q -k prepare`
Expected: FAIL (`reply.done` never set)

- [ ] **Step 3: Implement the drain and the log line**

In `devicelink/agent.py`, next to `self.start_requests`:

```python
        # Web prepares (harness/www_server.py, spec 2026-09-13 section 4.4)
        # arrive on the server thread and are drained here too; the handler
        # waits on each request's reply slot.
        self.prepare_requests: queue.Queue[PrepareRequest] | None = None
        self.prepare_authority = None
```

Import: `from control.prepare import PrepareRequest`. After `_drain_start_requests`:

```python
    def _drain_prepare_requests(self) -> None:
        q = self.prepare_requests
        if q is None:
            return
        while True:
            try:
                req = q.get_nowait()
            except queue.Empty:
                return
            reply = req.reply
            if self.prepare_authority is None:
                reply.accepted, reply.reason, reply.visible = (
                    False, "prepare is not wired", True)
            else:
                try:
                    decision = self.prepare_authority.request(req)
                    reply.accepted, reply.reason, reply.visible = (
                        decision.accepted, decision.reason, decision.visible)
                except Exception:
                    logger.exception("prepare authority raised for %s", req)
                    reply.accepted, reply.reason, reply.visible = (
                        False, "prepare failed", True)
            reply.done.set()
```

In `poll()`, right after `self._drain_start_requests()`, add `self._drain_prepare_requests()`.

In `console/agent.py` after `on_start_requested`:

```python
    def on_prepare_requested(self, record) -> None:
        if record.accepted:
            message = f"prepare {record.bit} from {record.source}: accepted"
            level = "info"
        else:
            message = (f"prepare {record.bit} from {record.source}: "
                       f"refused ({record.reason})")
            level = "warn"
        self.server.broadcast(protocol.log_event(level, message))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lobby_agent.py tests/test_console_agent.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add devicelink/agent.py console/agent.py tests/test_lobby_agent.py tests/test_console_agent.py
git commit -m "feat(prepare): DeviceLinkAgent drains prepare requests; Console logs every attempt"
```

---

### Task 5: Join card prepare URL, marker, and boot wiring of the prepare path

**Files:**
- Modify: `control/join_info.py` (`start_url` sibling `prepare_url`, `build_join_info` start dict)
- Modify: `console/static/join.js` (start block, lines 92 to 104)
- Modify: `harness/markers.py` (after `START_URL`), `harness/terrarium_boot.py` (`_print_join_urls`, and the www wiring near line 1825)
- Test: `tests/test_join_info.py`, `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: Task 2 `PrepareAuthority`; Task 3 `WwwServer.prepare_requests`; Task 4 agent attributes.
- Produces: `control.join_info.prepare_url(*, lan_ip, www_port, key, bit) -> str`; `info["start"]["prepare_url"]`; `markers.PREPARE_URL = "PREPARE_URL:"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_join_info.py`:

```python
from control.join_info import prepare_url


def test_prepare_url_carries_key_and_bit():
    assert prepare_url(lan_ip="10.0.0.7", www_port=8788, key="a b", bit="MetronomeBit") == \
        "http://10.0.0.7:8788/prepare?key=a+b&bit=MetronomeBit"


def test_build_join_info_adds_the_prepare_url_to_the_start_row():
    info = _info(start_key="metro-dev")
    assert info["start"]["prepare_url"] == \
        "http://10.0.0.7:8788/prepare?key=metro-dev&bit=" + info["bit"]


def test_prepare_url_is_absent_without_a_bit_name():
    info = _info(start_key="metro-dev", bit_name=None)
    assert info["start"]["prepare_url"] is None
```

Check `_info`'s signature in the file; if it does not accept `bit_name`, extend the helper to pass it through to `build_join_info`.

Append to `tests/test_terrarium_boot.py`:

```python
def test_print_join_urls_prints_the_prepare_url_after_the_start_url(capsys):
    from harness import markers
    from harness.terrarium_boot import _print_join_urls

    info = {"nodes": [], "start": {
        "url": "http://10.0.0.7:8788/start?key=k",
        "prepare_url": "http://10.0.0.7:8788/prepare?key=k&bit=B"}}
    _print_join_urls(lambda: info)
    out = capsys.readouterr().out.splitlines()
    assert out == [f"{markers.START_URL} http://10.0.0.7:8788/start?key=k",
                   f"{markers.PREPARE_URL} http://10.0.0.7:8788/prepare?key=k&bit=B"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_join_info.py tests/test_terrarium_boot.py -q -k prepare`
Expected: FAIL with `ImportError` / `AttributeError: PREPARE_URL`

- [ ] **Step 3: Implement**

`control/join_info.py`, after `start_url`:

```python
def prepare_url(*, lan_ip: str, www_port: int, key: str, bit: str) -> str:
    """The keyed LAN prepare URL MycoQuest builds (spec 2026-09-13 section
    4.5); the operator's copy carries no dev, loopback is the Terrarium."""
    return (f"http://{lan_ip}:{www_port}/prepare?"
            f"{urlencode({'key': key, 'bit': bit})}")
```

In `build_join_info`, where `start` is assembled:

```python
        start = {"url": url, "qr_svg": svg, "key": start_key,
                 "wire": f'/game/start "ss" <dev> {start_key}',
                 "prepare_url": (prepare_url(lan_ip=lan_ip, www_port=www_port,
                                             key=start_key, bit=bit_name)
                                 if bit_name else None)}
```

`console/static/join.js`, after the `info.start.wire` line:

```javascript
    if (info.start.prepare_url) {
      block.appendChild(mk("p", "meta", "Prepare (loads this Bit into the lobby; MycoQuest sends this):"));
      block.appendChild(lineWithCopy(info.start.prepare_url));
    }
```

`harness/markers.py`, after `START_URL`:

```python
# The keyed LAN prepare URL for the loaded Bit (spec 2026-09-13 section
# 4.5): MycoQuest sends it to load the Bit into the lobby. Printed right
# after START_URL; echoed by run_stack, never waited on.
PREPARE_URL = "PREPARE_URL:"
```

`harness/terrarium_boot.py` `_print_join_urls`:

```python
    if info.get("start"):
        print(f"{markers.START_URL} {info['start']['url']}", flush=True)
        if info["start"].get("prepare_url"):
            print(f"{markers.PREPARE_URL} {info['start']['prepare_url']}",
                  flush=True)
```

Boot wiring, replacing the two lines after `_start_www_server`:

```python
        www = _start_www_server(args, teardown)
        if www is not None:
            agent.start_requests = www.start_requests
            agent.prepare_requests = www.prepare_requests
            from control.prepare import PrepareAuthority
            agent.prepare_authority = PrepareAuthority(gs, registry, terrarium)
```

Do not add `prepare_url` anywhere outside the `start` dict, and do not serve it on the LAN: the mm-tuneshroom join launcher plan (its spec section 4.1) will add `GET /join.json` over this same read model and must pass `start_key=None` so `start` is always null there. Say so in the commit body so the launcher planner finds it in `git log`.

If `run_stack.py` has a list of echoed markers that includes `START_URL`, add `PREPARE_URL` beside it (grep `START_URL` in `harness/run_stack.py`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_join_info.py tests/test_terrarium_boot.py tests/test_run_stack.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add control/join_info.py console/static/join.js harness/markers.py harness/terrarium_boot.py harness/run_stack.py tests/test_join_info.py tests/test_terrarium_boot.py
git commit -m "feat(prepare): Join card and PREPARE_URL marker; boot wires the prepare authority"
```

---

### Task 6: `RegistrationState.granted()` and `players` on the wire

**Files:**
- Modify: `control/registration.py` (after `counts()`), `uplink/protocol.py` (`bit_completed_event`, new `players_view`)
- Test: `tests/test_registration.py`, `tests/test_protocol.py`

**Interfaces:**
- Consumes: `control.roles.RoleClass`.
- Produces (used by Task 7): `RegistrationState.granted() -> list[tuple[str, str, RoleClass]]`; `protocol.players_view(granted) -> list[dict]`; `protocol.bit_completed_event(..., players=())`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_registration.py`:

```python
def test_granted_lists_assignments_in_join_order_and_skips_room():
    table = make_table()
    room = Role(name="room", role_class=RoleClass.ROOM, capacity=1, scored=False)
    table.roles["room"] = room
    table.node_map["NODE_ROOM"] = ["room"]
    reg = RegistrationState(table)
    reg.join("ie2", "NODE_JAM", State.SETUP)
    reg.join("ie1", "NODE_PLAYER", State.SETUP)
    reg.join("fx1", "NODE_ROOM", State.SETUP)
    assert reg.granted() == [("ie2", "jammer", RoleClass.JAM),
                             ("ie1", "player", RoleClass.SHARED)]
    reg.release("ie2")
    assert reg.granted() == [("ie1", "player", RoleClass.SHARED)]
```

Append to `tests/test_protocol.py`:

```python
from control.roles import RoleClass
from uplink.protocol import bit_completed_event, players_view


def test_players_view_maps_jam_and_everything_else_to_scored():
    granted = [("g1", "jammer", RoleClass.JAM), ("g2", "player", RoleClass.SHARED),
               ("g3", "lead", RoleClass.UNIQUE)]
    assert players_view(granted) == [
        {"dev": "g1", "role": "jammer", "class": "jam"},
        {"dev": "g2", "role": "player", "class": "scored"},
        {"dev": "g3", "role": "lead", "class": "scored"}]


def test_bit_completed_event_always_carries_players():
    assert bit_completed_event({"score": 1}, "B", "0.1")["players"] == []
    ev = bit_completed_event(None, "B", "0.1",
                             players=[{"dev": "g1", "role": "player", "class": "scored"}])
    assert ev["result"] is None
    assert ev["players"] == [{"dev": "g1", "role": "player", "class": "scored"}]
    dumps(ev)     # JSON-serialisable
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_registration.py tests/test_protocol.py -q -k "granted or players"`
Expected: FAIL with `AttributeError` / `ImportError`

- [ ] **Step 3: Implement**

`control/registration.py`, after `counts()`:

```python
    def granted(self) -> list[tuple[str, str, RoleClass]]:
        """Every current assignment as (dev, role_name, role_class) in join
        order, without ROOM-class bindings (a fixture, never a player). The
        uplink builds bit_completed.players from this at COMPLETING."""
        return [(dev, role_name, role_class)
                for dev, (_node, role_name, role_class) in self.assignments.items()
                if role_class is not RoleClass.ROOM]
```

`uplink/protocol.py`:

```python
from control.roles import RoleClass


def players_view(granted) -> list[dict]:
    """bit_completed.players (spec 2026-09-13 section 5.2): JAM is "jam",
    every other player-bearing class is "scored". ROOM never reaches here."""
    return [{"dev": dev, "role": role,
             "class": "jam" if role_class is RoleClass.JAM else "scored"}
            for dev, role, role_class in granted]


def bit_completed_event(result, bit_name: str = "",
                        bit_version: str = "", *, room_name=None,
                        terrarium_config_version=None, players=()) -> dict:
    event = {
        "event": "bit_completed",
        "result": result,
        "bit": {"name": bit_name, "version": bit_version},
        "players": list(players),
    }
    ...  # room_name / terrarium_config_version unchanged
```

`uplink/protocol.py` already imports nothing from `control/`; importing `control.roles` is allowed (the rule is that `control/` never imports `uplink/`, not the reverse).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_registration.py tests/test_protocol.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add control/registration.py uplink/protocol.py tests/test_registration.py tests/test_protocol.py
git commit -m "feat(uplink): RegistrationState.granted and players on bit_completed"
```

---

### Task 7: emit `bit_completed` at COMPLETING with players, never on abort

**Files:**
- Modify: `uplink/link.py` (`on_state_change`, `_send_bit_completed`)
- Test: `tests/test_link.py` (three existing tests change; new tests appended)

**Interfaces:**
- Consumes: Task 6 `granted()`, `players_view`, `bit_completed_event(players=)`.
- Produces (used by Task 12): `UplinkAgent._send_bit_completed()` now builds the full event and hands it to `self._emit_bit_completed(event)` (a one-line `self._send(event)` in this task; Task 12 adds the journal there).

- [ ] **Step 1: Update the three existing tests and add the new ones**

In `tests/test_link.py`:

- `test_bit_completed_sent_at_unload_when_result_present`: rename to `test_bit_completed_sent_at_completing_when_result_present` and change the expected event to include `"players": []`.
- `test_exploding_result_does_not_wedge_state_machine`: change the last assertion to

```python
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert len(completed) == 1 and completed[0]["result"] is None
    assert completed[0]["players"] == [{"dev": "ie1", "role": "player", "class": "scored"}]
```

- `test_no_bit_completed_event_when_result_is_none`: rename to `test_bit_completed_with_null_result_when_bit_has_none` and assert one event with `result is None` and `players == []`.

Append:

```python
def test_bit_completed_carries_players_captured_before_release():
    agent, server, transport = make_agent()
    server.hello("ie1", "Testshroom 1", "1.0")
    server.hello("ie2", "Testshroom 2", "1.0")
    server.load_bit("test_bit")
    server.join("ie1", "TEST_PLAYER_NODE")
    server.join("ie2", "TEST_JAM_NODE")
    server.run()
    server.tick(3.0)
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert completed[0]["players"] == [
        {"dev": "ie1", "role": "player", "class": "scored"},
        {"dev": "ie2", "role": "jammer", "class": "jam"}]
    states = [m["state"] for m in transport.sent if m["event"] == "state_changed"]
    # sent on COMPLETING, i.e. before the UNLOADING state_changed
    idx_completed = transport.sent.index(completed[0])
    idx_unloading = next(i for i, m in enumerate(transport.sent)
                         if m.get("event") == "state_changed" and m["state"] == "UNLOADING")
    assert idx_completed < idx_unloading


def test_abort_sends_no_bit_completed():
    class ScoringBit(TestBit):
        def result(self):
            return {"score": 99}
    server = GameServer(bit_registry={"scoring_bit": ScoringBit})
    transport = FakeTransport()
    UplinkAgent(server, transport)
    transport.connect()
    server.load_bit("scoring_bit")
    server.run()
    server.abort()
    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []
    assert server.state.name == "IDLE"


def test_the_reserved_terrarium_id_never_appears_in_players():
    agent, server, transport = make_agent()
    server.hello("terrarium", "Box", "1.0")      # refused at hello
    server.load_bit("test_bit")
    assert server.join("terrarium", "TEST_PLAYER_NODE").granted is False
    server.run()
    server.tick(3.0)
    completed = [m for m in transport.sent if m["event"] == "bit_completed"]
    assert completed[0]["players"] == []
```

Check `TEST_JAM_NODE` is TestBit's jammer node name (`grep -n NODE bits/test/test_bit.py`); use the real name. If `server.join("terrarium", ...)` raises rather than refusing, wrap it in `pytest.raises` and keep the players assertion.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_link.py -q -k "completed or abort or terrarium_id"`
Expected: FAIL (players key missing; abort case sends an event)

- [ ] **Step 3: Implement**

In `uplink/link.py`:

```python
    def on_state_change(self, old_state: State, new_state: State) -> None:
        terrarium_state = (
            self.terrarium.state.name if self.terrarium is not None else None)
        self._send(protocol.state_changed_event(
            new_state.name, self.game_server.bit_name,
            terrarium_state=terrarium_state))
        # COMPLETING is reached only by the tick-triggered completion path;
        # abort() skips it, so an aborted round is never reported as
        # completed (spec 2026-09-13 section 5.3). Registration is still
        # populated here; it is released during UNLOADING.
        if new_state == State.COMPLETING:
            self._send_bit_completed()

    def _send_bit_completed(self) -> None:
        gs = self.game_server
        bit = gs.bit
        if bit is None:
            return
        try:
            result = bit.result()
        except Exception:
            logger.exception("Bit.result raised; sending bit_completed with a null result")
            result = None
        granted = gs.registration.granted() if gs.registration is not None else []
        event = protocol.bit_completed_event(
            result, gs.bit_name or "", bit.version,
            room_name=gs.provenance.get("room_name"),
            terrarium_config_version=gs.provenance.get("terrarium_config_version"),
            players=protocol.players_view(granted))
        self._emit_bit_completed(event)

    def _emit_bit_completed(self, event: dict) -> None:
        self._send(event)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_link.py tests/test_console_agent.py -q`
Expected: all pass. If a Console test asserted on `bit_completed` timing, update it to COMPLETING the same way.

- [ ] **Step 5: Commit**

```bash
git add uplink/link.py tests/test_link.py
git commit -m "feat(uplink): bit_completed at COMPLETING with players, null result allowed, never on abort"
```

---

### Task 8: `[uplink]` in `terrarium.toml`

**Files:**
- Modify: `control/terrarium_config.py` (dataclass `TerrariumConfig`, `parse_terrarium_config` after the `[admin]` block, `load_terrarium_config` pass-through)
- Modify: `terrarium.toml` (commented example)
- Test: `tests/test_terrarium_config.py`

**Interfaces:**
- Produces (used by Task 13): `UplinkConfig(tenant_slug: str, secret: str, url: str = "")` frozen, `secret` is `field(repr=False)`; `TerrariumConfig.uplink: UplinkConfig | None = None`; `SECRET_PATTERN`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium_config.py`:

```python
GOOD_SECRET = "0123456789abcdef" * 4


def test_uplink_absent_is_none():
    assert parse_terrarium_config(MINIMAL, source="t").uplink is None


def test_uplink_parses_and_hides_the_secret_from_repr():
    cfg = parse_terrarium_config(
        MINIMAL + f'\n[uplink]\ntenant_slug = "mm"\nsecret = "{GOOD_SECRET}"\n'
        'url = "wss://broker.example/uplink"\n', source="t")
    assert cfg.uplink.tenant_slug == "mm"
    assert cfg.uplink.secret == GOOD_SECRET
    assert cfg.uplink.url == "wss://broker.example/uplink"
    assert GOOD_SECRET not in repr(cfg.uplink) and GOOD_SECRET not in repr(cfg)


def test_uplink_url_defaults_to_empty():
    cfg = parse_terrarium_config(
        MINIMAL + f'\n[uplink]\ntenant_slug = "mm"\nsecret = "{GOOD_SECRET}"\n',
        source="t")
    assert cfg.uplink.url == ""


@pytest.mark.parametrize("body,key", [
    (f'secret = "{GOOD_SECRET}"', "uplink.tenant_slug"),
    ('tenant_slug = ""\nsecret = "' + GOOD_SECRET + '"', "uplink.tenant_slug"),
    ('tenant_slug = "mm"', "uplink.secret"),
    ('tenant_slug = "mm"\nsecret = "abc"', "uplink.secret"),
    ('tenant_slug = "mm"\nsecret = "' + GOOD_SECRET.upper() + '"', "uplink.secret"),
    ('tenant_slug = "mm"\nsecret = "' + GOOD_SECRET + '"\nurl = 7', "uplink.url"),
])
def test_uplink_validation_is_located_and_never_echoes_the_secret(body, key):
    with pytest.raises(TerrariumConfigError) as err:
        parse_terrarium_config(MINIMAL + "\n[uplink]\n" + body + "\n", source="t")
    assert err.value.key == key
    assert GOOD_SECRET not in str(err.value) and GOOD_SECRET.upper() not in str(err.value)


def test_uplink_must_be_a_table():
    with pytest.raises(TerrariumConfigError) as err:
        parse_terrarium_config(MINIMAL + "\nuplink = 1\n", source="t")
    assert err.value.key == "uplink"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q -k uplink`
Expected: FAIL with `AttributeError: 'TerrariumConfig' object has no attribute 'uplink'`

- [ ] **Step 3: Implement**

`control/terrarium_config.py`:

```python
import re

SECRET_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class UplinkConfig:
    """[uplink]: the broker and the MycoQuest-issued box secret (spec
    2026-09-13 section 6.1). The name presented is [terrarium] name."""
    tenant_slug: str
    secret: str = field(repr=False)
    url: str = ""
```

Add to `TerrariumConfig`:

```python
    # [uplink], None when the table is absent: no uplink is built at boot.
    uplink: UplinkConfig | None = None
```

In `parse_terrarium_config`, after the `[admin]` block:

```python
    uplink_raw = raw.get("uplink")
    uplink = None
    if uplink_raw is not None:
        if not isinstance(uplink_raw, dict):
            raise TerrariumConfigError(source=source, key="uplink",
                                       message="expected a table")
        slug = uplink_raw.get("tenant_slug")
        if not isinstance(slug, str) or not slug:
            raise TerrariumConfigError(source=source, key="uplink.tenant_slug",
                                       message="required non-empty string")
        secret = uplink_raw.get("secret")
        if not isinstance(secret, str) or not SECRET_PATTERN.match(secret):
            raise TerrariumConfigError(
                source=source, key="uplink.secret",
                message="expected 64 lowercase hex characters, pasted from "
                        "the MycoQuest admin site (value not shown)")
        url = uplink_raw.get("url", "")
        if not isinstance(url, str):
            raise TerrariumConfigError(source=source, key="uplink.url",
                                       message="expected a string")
        uplink = UplinkConfig(tenant_slug=slug, secret=secret, url=url)
```

and pass `uplink=uplink` into the `TerrariumConfig(...)` constructor at the end. Check `load_terrarium_config` builds its result with `replace(...)` from `parse_terrarium_config`'s output (so `uplink` carries through); if it constructs a new `TerrariumConfig` by hand, add `uplink=parsed.uplink`.

`terrarium.toml`, at the end:

```toml
# [uplink]
# Presence of this table turns the uplink on. The secret is issued once by
# the MycoQuest admin site (64 lowercase hex characters); url may be left
# out until mm-fairyring exists, in which case frames are logged locally.
# tenant_slug = "your-tenant"
# secret = "0000000000000000000000000000000000000000000000000000000000000000"
# url = "wss://fairyring.example/uplink"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add control/terrarium_config.py terrarium.toml tests/test_terrarium_config.py
git commit -m "feat(config): [uplink] table with tenant_slug, validated secret, optional url"
```

---

### Task 9: identity frame first, `lan_ip` in the resync

**Files:**
- Modify: `uplink/protocol.py` (`UplinkIdentity`, `identity_frame`, `state_changed_event(lan_ip=)`), `uplink/link.py` (`__init__`, `maintain_connection`, `_send_resync`)
- Test: `tests/test_protocol.py`, `tests/test_link.py`

**Interfaces:**
- Produces (used by Task 13): `protocol.UplinkIdentity(tenant_slug, terrarium_name, secret)` frozen, `secret` `repr=False`; `protocol.identity_frame(identity) -> dict`; `UplinkAgent(..., identity=None, lan_ip=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_protocol.py`:

```python
from uplink.protocol import UplinkIdentity, identity_frame, state_changed_event


def test_identity_frame_shape_and_hidden_repr():
    ident = UplinkIdentity("mm", "main-stage", "ab" * 32)
    assert identity_frame(ident) == {"event": "identity", "tenant_slug": "mm",
                                     "terrarium_name": "main-stage", "secret": "ab" * 32}
    assert "ab" * 32 not in repr(ident)


def test_state_changed_event_carries_lan_ip_only_when_given():
    assert "lan_ip" not in state_changed_event("IDLE")
    assert state_changed_event("IDLE", lan_ip="10.0.0.7")["lan_ip"] == "10.0.0.7"
```

Append to `tests/test_link.py`:

```python
from uplink.protocol import UplinkIdentity


def test_identity_is_the_first_frame_on_every_connect():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    ident = UplinkIdentity("mm", "main-stage", "ab" * 32)
    agent = UplinkAgent(server, transport, identity=ident, time_source=FakeClock())
    agent.maintain_connection()
    assert transport.sent[0] == {"event": "identity", "tenant_slug": "mm",
                                 "terrarium_name": "main-stage", "secret": "ab" * 32}
    assert transport.sent[1]["event"] == "state_changed"
    transport.disconnect()
    transport.sent.clear()
    agent.maintain_connection()
    assert transport.sent[0]["event"] == "identity"


def test_no_identity_means_no_identity_frame():
    agent, server, transport = make_agent()
    transport.disconnect()
    transport.sent.clear()
    agent.maintain_connection()
    assert transport.sent[0]["event"] == "state_changed"


def test_resync_carries_the_injected_lan_ip_and_ordinary_events_do_not():
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, lan_ip=lambda: "10.0.0.7",
                        time_source=FakeClock())
    agent.maintain_connection()
    assert transport.sent[0]["lan_ip"] == "10.0.0.7"
    server.load_bit("test_bit")
    later = [m for m in transport.sent[1:] if m["event"] == "state_changed"]
    assert all("lan_ip" not in m for m in later)


def test_a_raising_lan_ip_is_omitted_not_fatal():
    def boom():
        raise OSError("no route")
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    agent = UplinkAgent(server, transport, lan_ip=boom, time_source=FakeClock())
    agent.maintain_connection()
    assert "lan_ip" not in transport.sent[0]
```

`FakeClock` already exists in `tests/test_link.py`; reuse it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_protocol.py tests/test_link.py -q -k "identity or lan_ip"`
Expected: FAIL with `ImportError` / `TypeError: unexpected keyword`

- [ ] **Step 3: Implement**

`uplink/protocol.py`:

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class UplinkIdentity:
    """What the box presents in the first frame of every connection (spec
    2026-09-13 section 6.2; shape from mm-fairyring issue #1)."""
    tenant_slug: str
    terrarium_name: str
    secret: str = field(repr=False)


def identity_frame(identity: UplinkIdentity) -> dict:
    return {"event": "identity", "tenant_slug": identity.tenant_slug,
            "terrarium_name": identity.terrarium_name,
            "secret": identity.secret}


def state_changed_event(state_name: str, loaded_bit: str | None = None, *,
                        terrarium_state: str | None = None,
                        lan_ip: str | None = None) -> dict:
    event = {"event": "state_changed", "state": state_name,
             "loaded_bit": loaded_bit, "terrarium_state": terrarium_state}
    if lan_ip is not None:
        event["lan_ip"] = lan_ip
    return event
```

`uplink/link.py`:

```python
    def __init__(self, game_server: GameServer, transport, *,
                 time_source=time.monotonic, registry=None, terrarium=None,
                 identity=None, lan_ip=None):
        ...
        # Identity presented first on every connect (None: a test-only or
        # pre-config agent sends none). lan_ip is an injected callable so
        # uplink/ never imports harness/.
        self.identity = identity
        self._lan_ip = lan_ip
```

In `maintain_connection`, after the backoff reset and before `self._send_resync()`:

```python
        if self.identity is not None:
            self._send(protocol.identity_frame(self.identity))
        self._send_resync()
```

In `_send_resync`, compute `lan_ip`:

```python
        lan_ip = None
        if self._lan_ip is not None:
            try:
                lan_ip = self._lan_ip()
            except Exception:
                logger.exception("lan_ip probe raised; resync carries no address")
        self._send(protocol.state_changed_event(
            self.game_server.state.name, self.game_server.bit_name,
            terrarium_state=terrarium_state, lan_ip=lan_ip))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_protocol.py tests/test_link.py tests/test_console_protocol.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add uplink/protocol.py uplink/link.py tests/test_protocol.py tests/test_link.py
git commit -m "feat(uplink): identity frame first on connect; lan_ip in the resync"
```

---

### Task 10: `durable` on transports and `LogTransport`

**Files:**
- Modify: `uplink/transport.py`
- Test: `tests/test_transport.py`

**Interfaces:**
- Produces (used by Tasks 12 and 13): `Transport.durable: bool`; `WebSocketTransport.durable = True`; `FakeTransport(durable=True)`; `LogTransport()` with `connected`, `durable = False`, `connect()`, `send(msg)`, `receive() -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transport.py`:

```python
import logging

from uplink.transport import LogTransport, WebSocketTransport


def test_fake_transport_is_durable_by_default_and_settable():
    assert FakeTransport().durable is True
    assert FakeTransport(durable=False).durable is False


def test_websocket_transport_is_durable():
    assert WebSocketTransport("ws://127.0.0.1:1/").durable is True


def test_log_transport_connects_is_not_durable_and_receives_nothing():
    t = LogTransport()
    assert t.connected is False and t.durable is False
    t.connect()
    assert t.connected is True
    assert t.receive() is None


def test_log_transport_logs_frames_with_the_secret_redacted(caplog):
    t = LogTransport()
    t.connect()
    with caplog.at_level(logging.DEBUG, logger="uplink.transport"):
        t.send({"event": "identity", "tenant_slug": "mm",
                "terrarium_name": "n", "secret": "ab" * 32})
        t.send({"event": "state_changed", "state": "IDLE"})
    assert "ab" * 32 not in caplog.text
    assert "[redacted]" in caplog.text
    assert '"state": "IDLE"' in caplog.text or '"state":"IDLE"' in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_transport.py -q`
Expected: FAIL with `ImportError: cannot import name 'LogTransport'`

- [ ] **Step 3: Implement**

`uplink/transport.py`:

```python
import logging

logger = logging.getLogger(__name__)


class Transport(Protocol):
    connected: bool
    # True when a send() that returns without raising has left the box on
    # a real socket; the agent replays and trims its journal only then
    # (spec 2026-09-13 section 6.3).
    durable: bool
    ...


class FakeTransport:
    def __init__(self, durable: bool = True):
        self.connected = False
        self.durable = durable
        ...


class LogTransport:
    """A box with [uplink] but no broker URL: frames go to the log so the
    identity, resync and journal-append paths run live, and nothing is ever
    trimmed on its account (durable is False)."""

    durable = False

    def __init__(self) -> None:
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def send(self, msg: dict) -> None:
        shown = dict(msg)
        if "secret" in shown:
            shown["secret"] = "[redacted]"
        logger.debug("uplink (log-only) %s", _json_dumps(shown))

    def receive(self) -> dict | None:
        return None


class WebSocketTransport:
    durable = True
    ...
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_transport.py tests/test_websocket_transport.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add uplink/transport.py tests/test_transport.py
git commit -m "feat(uplink): durable flag on transports and a log-only LogTransport"
```

---

### Task 11: the journal

**Files:**
- Create: `uplink/journal.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- Produces (used by Tasks 12 and 13): `Journal(path: str, cap: int = 500)` with `append(event: dict)`, `entries() -> list[dict]`, `clear()`, `path`; `JOURNAL_FILENAME = "uplink_journal.jsonl"`, `JOURNAL_CAP = 500`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_journal.py
"""uplink/journal.py: the bounded bit_completed journal (spec 2026-09-13
section 6.4, mm-terrarium issue #103)."""
import logging

from uplink.journal import JOURNAL_CAP, JOURNAL_FILENAME, Journal


def test_constants():
    assert JOURNAL_FILENAME == "uplink_journal.jsonl"
    assert JOURNAL_CAP == 500


def test_missing_file_reads_as_empty(tmp_path):
    j = Journal(str(tmp_path / "runs" / "uplink_journal.jsonl"))
    assert j.entries() == []


def test_append_creates_the_directory_and_reads_back_in_order(tmp_path):
    j = Journal(str(tmp_path / "runs" / "uplink_journal.jsonl"))
    j.append({"event": "bit_completed", "n": 1})
    j.append({"event": "bit_completed", "n": 2})
    assert [e["n"] for e in j.entries()] == [1, 2]
    text = (tmp_path / "runs" / "uplink_journal.jsonl").read_text()
    assert text.count("\n") == 2


def test_cap_keeps_the_newest(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"), cap=3)
    for n in range(5):
        j.append({"n": n})
    assert [e["n"] for e in j.entries()] == [2, 3, 4]


def test_corrupt_line_is_skipped_and_logged(tmp_path, caplog):
    p = tmp_path / "j.jsonl"
    p.write_text('{"n": 1}\nnot json\n{"n": 3}\n')
    j = Journal(str(p))
    with caplog.at_level(logging.WARNING, logger="uplink.journal"):
        assert [e["n"] for e in j.entries()] == [1, 3]
    assert any("line 2" in r.getMessage() for r in caplog.records)


def test_clear_truncates(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"))
    j.append({"n": 1})
    j.clear()
    assert j.entries() == []
    assert (tmp_path / "j.jsonl").read_text() == ""


def test_lines_go_through_wire_json(tmp_path, monkeypatch):
    import uplink.journal as journal_module
    calls = []
    real = journal_module.dumps
    monkeypatch.setattr(journal_module, "dumps", lambda obj: (calls.append(obj), real(obj))[1])
    Journal(str(tmp_path / "j.jsonl")).append({"n": 1})
    assert calls == [{"n": 1}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_journal.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# uplink/journal.py
"""A bounded on-box journal of bit_completed events (spec docs/superpowers/
specs/2026-09-13-mycoquest-handoff-terrarium-design.md section 6.4,
mm-terrarium issue #103).

One JSON line per event under the runs directory, appended at emit time
whether or not the uplink is up, replayed in file order on reconnect over a
durable transport, then cleared. Bounded by count: the newest `cap`
entries survive. Pure stdlib plus control/wire_json.dumps.
"""

from __future__ import annotations

import json
import logging
import os

from control.wire_json import dumps

logger = logging.getLogger(__name__)

JOURNAL_FILENAME = "uplink_journal.jsonl"
JOURNAL_CAP = 500


class Journal:
    def __init__(self, path: str, cap: int = JOURNAL_CAP) -> None:
        self.path = path
        self.cap = cap

    def _lines(self) -> list[str]:
        try:
            with open(self.path, encoding="utf-8") as f:
                return [line for line in f.read().split("\n") if line]
        except FileNotFoundError:
            return []

    def append(self, event: dict) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(dumps(event) + "\n")
        lines = self._lines()
        if len(lines) > self.cap:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines[-self.cap:]) + "\n")

    def entries(self) -> list[dict]:
        out = []
        for number, line in enumerate(self._lines(), start=1):
            try:
                out.append(json.loads(line))
            except ValueError:
                logger.warning("journal %s line %d is not JSON; skipping",
                               self.path, number)
        return out

    def clear(self) -> None:
        with open(self.path, "w", encoding="utf-8"):
            pass
```

`json.loads` for reading is fine; the repo rule is about writing.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_journal.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add uplink/journal.py tests/test_journal.py
git commit -m "feat(uplink): count-bounded JSONL journal"
```

---

### Task 12: journal append on emit, replay after resync on a durable transport

**Files:**
- Modify: `uplink/link.py` (`__init__`, `maintain_connection`, `_emit_bit_completed`, new `_replay_journal`)
- Test: `tests/test_link.py`

**Interfaces:**
- Consumes: Task 7 `_emit_bit_completed`; Task 10 `durable`; Task 11 `Journal`.
- Produces (used by Task 13): `UplinkAgent(..., journal=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_link.py`:

```python
from uplink.journal import Journal


def _completed_round(server):
    server.load_bit("test_bit")
    server.run()
    server.tick(3.0)


def test_bit_completed_is_journaled_even_while_disconnected(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    _completed_round(server)          # never connected
    assert [e["event"] for e in journal.entries()] == ["bit_completed"]
    assert transport.sent == []


def test_replay_after_resync_on_a_durable_transport_then_clear(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    journal.append({"event": "bit_completed", "bit": {"name": "old", "version": "0"},
                    "result": None, "players": []})
    agent = UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    agent.maintain_connection()
    events = [m["event"] for m in transport.sent]
    assert events[0] == "state_changed"
    assert events[-1] == "bit_completed"
    assert transport.sent[-1]["bit"]["name"] == "old"
    assert journal.entries() == []


def test_live_send_and_journal_both_happen_when_connected(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    agent = UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    agent.maintain_connection()
    _completed_round(server)
    assert [m for m in transport.sent if m["event"] == "bit_completed"]
    assert len(journal.entries()) == 1       # trimmed only on the next replay
    transport.disconnect()
    transport.sent.clear()
    agent.maintain_connection()
    assert [m for m in transport.sent if m["event"] == "bit_completed"]
    assert journal.entries() == []


def test_non_durable_transport_replays_nothing_and_clears_nothing(tmp_path):
    server = GameServer(bit_registry=REGISTRY)
    transport = FakeTransport(durable=False)
    journal = Journal(str(tmp_path / "j.jsonl"))
    journal.append({"event": "bit_completed", "bit": {"name": "old", "version": "0"},
                    "result": None, "players": []})
    agent = UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    agent.maintain_connection()
    assert [m for m in transport.sent if m["event"] == "bit_completed"] == []
    assert len(journal.entries()) == 1


def test_a_send_that_raises_mid_replay_leaves_the_journal_intact(tmp_path):
    class DroppingTransport(FakeTransport):
        def send(self, msg):
            if msg.get("event") == "bit_completed" and msg["bit"]["name"] == "second":
                self.connected = False
                raise ConnectionError("dropped")
            super().send(msg)

    server = GameServer(bit_registry=REGISTRY)
    transport = DroppingTransport()
    journal = Journal(str(tmp_path / "j.jsonl"))
    for name in ("first", "second", "third"):
        journal.append({"event": "bit_completed", "bit": {"name": name, "version": "0"},
                        "result": None, "players": []})
    agent = UplinkAgent(server, transport, journal=journal, time_source=FakeClock())
    agent.maintain_connection()          # must not raise
    assert [e["bit"]["name"] for e in journal.entries()] == ["first", "second", "third"]
    assert transport.connected is False


def test_no_journal_means_no_replay_and_no_file(tmp_path):
    agent, server, transport = make_agent()
    transport.disconnect()
    agent.maintain_connection()
    assert not list(tmp_path.iterdir())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_link.py -q -k "journal or replay or durable"`
Expected: FAIL with `TypeError: unexpected keyword argument 'journal'`

- [ ] **Step 3: Implement**

`uplink/link.py`:

```python
    def __init__(self, game_server, transport, *, time_source=time.monotonic,
                 registry=None, terrarium=None, identity=None, lan_ip=None,
                 journal=None):
        ...
        # uplink/journal.py Journal or None: bit_completed is appended before
        # any send and replayed after the resync over a durable transport
        # (spec 2026-09-13 section 6.4).
        self.journal = journal
```

In `maintain_connection`, after `self._send_resync()`:

```python
        self._replay_journal()
```

New methods:

```python
    def _replay_journal(self) -> None:
        if self.journal is None or not getattr(self.transport, "durable", False):
            return
        entries = self.journal.entries()
        for event in entries:
            if not self.transport.connected:
                return
            try:
                self.transport.send(event)
            except Exception:
                logger.warning("uplink dropped mid-replay; %d journal entries "
                               "kept for the next connect", len(entries))
                return
        if entries:
            self.journal.clear()

    def _emit_bit_completed(self, event: dict) -> None:
        if self.journal is not None:
            try:
                self.journal.append(event)
            except OSError:
                logger.exception("could not journal bit_completed; sending live only")
        self._send(event)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_link.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add uplink/link.py tests/test_link.py
git commit -m "feat(uplink): journal bit_completed and replay it after the resync on a durable link"
```

---

### Task 13: boot wiring of the uplink and the marker

**Files:**
- Modify: `harness/terrarium_boot.py` (new `_build_uplink`, new `_pump_uplink`, `uplink=None` keyword on `_wait_in_setup`, `_serve_until_done`, `_wait_for_load`, `_serve_rounds`, `_wait_for_room_ready`, `_serve_roomless`, every call site of those in `main()` and in each other, and the construction in `main()` after the www server block)
- Modify: `harness/markers.py` (`UPLINK`)
- Test: `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: Task 8 `TerrariumConfig.uplink`; Task 9 `UplinkIdentity`, `UplinkAgent(identity=, lan_ip=)`; Task 10 `LogTransport`, `WebSocketTransport`; Task 11 `Journal`, `JOURNAL_FILENAME`; Task 12 `journal=`.
- Produces: `_build_uplink(terrarium_config, gs, registry, terrarium, runs_dir) -> UplinkAgent | None`; `_pump_uplink(uplink) -> None`; `markers.UPLINK = "UPLINK:"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium_boot.py`:

```python
def _uplink_config(url=""):
    from control.terrarium_config import UplinkConfig
    return UplinkConfig(tenant_slug="mm", secret="ab" * 32, url=url)


class _CfgWithUplink:
    name = "main-stage"

    def __init__(self, uplink):
        self.uplink = uplink


def test_build_uplink_returns_none_without_the_table(tmp_path):
    from harness.terrarium_boot import _build_uplink
    gs = GameServer({"TestBit": TestBit})
    assert _build_uplink(_CfgWithUplink(None), gs, None, None, str(tmp_path)) is None


def test_build_uplink_log_only_when_url_is_empty(tmp_path):
    from harness.terrarium_boot import _build_uplink
    from uplink.journal import Journal
    from uplink.transport import LogTransport
    gs = GameServer({"TestBit": TestBit})
    up = _build_uplink(_CfgWithUplink(_uplink_config()), gs, None, None, str(tmp_path))
    assert isinstance(up.transport, LogTransport)
    assert isinstance(up.journal, Journal)
    assert up.journal.path == str(tmp_path / "uplink_journal.jsonl")
    assert up.identity.tenant_slug == "mm" and up.identity.terrarium_name == "main-stage"
    assert up._lan_ip is not None


def test_build_uplink_websocket_when_url_is_set(tmp_path):
    from harness.terrarium_boot import _build_uplink
    from uplink.transport import WebSocketTransport
    gs = GameServer({"TestBit": TestBit})
    up = _build_uplink(_CfgWithUplink(_uplink_config("ws://127.0.0.1:1/")), gs,
                       None, None, str(tmp_path))
    assert isinstance(up.transport, WebSocketTransport)
    assert up.transport.uri == "ws://127.0.0.1:1/"


def test_build_uplink_has_no_journal_without_run_records():
    from harness.terrarium_boot import _build_uplink
    gs = GameServer({"TestBit": TestBit})
    up = _build_uplink(_CfgWithUplink(_uplink_config()), gs, None, None, None)
    assert up.journal is None


def test_pump_uplink_maintains_then_polls_and_tolerates_none():
    from harness.terrarium_boot import _pump_uplink
    calls = []

    class Up:
        def maintain_connection(self):
            calls.append("maintain")

        def poll(self):
            calls.append("poll")

    _pump_uplink(None)
    _pump_uplink(Up())
    assert calls == ["maintain", "poll"]


def test_wait_in_setup_pumps_the_uplink():
    from harness.terrarium_boot import _wait_in_setup
    pumps = []

    class FakeAgent:
        def poll(self):
            pass

    class Up:
        def maintain_connection(self):
            pumps.append("m")

        def poll(self):
            pumps.append("p")

    ticks = iter([0.0, 0.1, 0.2, 5.0])
    _wait_in_setup(FakeAgent(), 1.0, clock=lambda: next(ticks),
                   sleep=lambda _s: None, uplink=Up())
    assert pumps[:2] == ["m", "p"] and len(pumps) >= 4
```

Use the file's existing imports for `GameServer` and `TestBit` (check the top of `tests/test_terrarium_boot.py`; add them if absent).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py -q -k uplink`
Expected: FAIL with `ImportError: cannot import name '_build_uplink'`

- [ ] **Step 3: Implement**

`harness/markers.py`, after `PREPARE_URL`:

```python
# Printed once at startup when terrarium.toml has an [uplink] table:
# "UPLINK: log-only tenant=<slug>" or "UPLINK: <url> tenant=<slug>". Never
# carries the secret. Echoed by run_stack, never waited on.
UPLINK = "UPLINK:"
```

`harness/terrarium_boot.py`, module-level helpers (near `_start_www_server`):

```python
def _build_uplink(terrarium_config, gs, registry, terrarium, runs_dir):
    """Spec 2026-09-13 section 6.5: an [uplink] table is the switch. No
    table, no agent. An empty url means LogTransport (frames logged, the
    journal grows, nothing trimmed). The journal lives directly under
    runs_dir so it outlives any single run; with run records off (runs_dir
    None) there is no journal and the box keeps no state."""
    cfg = terrarium_config.uplink
    if cfg is None:
        return None
    from uplink.journal import JOURNAL_FILENAME, Journal
    from uplink.link import UplinkAgent
    from uplink.protocol import UplinkIdentity
    from uplink.transport import LogTransport, WebSocketTransport
    transport = WebSocketTransport(cfg.url) if cfg.url else LogTransport()
    journal = (Journal(os.path.join(runs_dir, JOURNAL_FILENAME))
               if runs_dir is not None else None)
    if journal is None:
        logging.getLogger(__name__).warning(
            "uplink: run records are off, so bit_completed is not journaled")
    identity = UplinkIdentity(cfg.tenant_slug, terrarium_config.name, cfg.secret)
    return UplinkAgent(gs, transport, registry=registry, terrarium=terrarium,
                       identity=identity, journal=journal, lan_ip=lan_ip)


def _pump_uplink(uplink) -> None:
    """One uplink tick: reconnect on the backoff schedule, then drain
    inbound commands. Called wherever console_agent.poll() is."""
    if uplink is None:
        return
    uplink.maintain_connection()
    uplink.poll()
```

Add `uplink=None` as a keyword parameter to each of `_wait_in_setup`, `_serve_until_done`, `_wait_for_load`, `_serve_rounds`, `_wait_for_room_ready`, `_serve_roomless`. In each of the five loop bodies, immediately after the existing

```python
        if console_agent is not None:
            console_agent.poll()
```

add `_pump_uplink(uplink)`. In `_serve_rounds` pass `uplink=uplink` to its calls of `_wait_for_load`, `_wait_in_setup`, `_serve_until_done`; in `_serve_roomless` pass it to `_wait_for_room_ready` and `_serve_rounds`. In `main()`, right after the www server block from Task 5:

```python
        uplink = _build_uplink(terrarium_config, gs, registry, terrarium, runs_dir)
        if uplink is not None:
            mode = terrarium_config.uplink.url or "log-only"
            print(f"{markers.UPLINK} {mode} tenant={terrarium_config.uplink.tenant_slug}",
                  flush=True)
```

and pass `uplink=uplink` to every call of `_wait_in_setup`, `_serve_until_done`, and `_serve_roomless` in `main()` (the call sites at roughly lines 1868, 1889, 1904, 1916, 1931, 1945, 1963 before this task; grep for them). Every existing test that calls these functions without `uplink` keeps working because the default is `None`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py tests/test_run_stack.py -q`
Expected: all pass

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass; note the count in the commit body.

- [ ] **Step 6: Commit**

```bash
git add harness/terrarium_boot.py harness/markers.py tests/test_terrarium_boot.py
git commit -m "feat(boot): build the uplink from [uplink] and pump it beside the Console"
```

---

### Task 14: deep-dive and closeout notes

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (the `uplink/` subsection under Landed subsystems, and a new bullet on `GET /prepare` next to the `GET /start` text)
- Modify: `BACKLOG.md` if it carries the uplink follow-ups (grep `fairyring`)

- [ ] **Step 1: Update the deep-dive**

In the `uplink/` subsection replace "nothing is buffered during an outage" with the journal behaviour (append at COMPLETING, replay after resync on a durable transport, then clear, cap 500, file `runs/uplink_journal.jsonl`), state that the agent is now built by `terrarium_boot` when `[uplink]` is present, that the identity frame is first on every connect, and that `bit_completed` fires at COMPLETING with `players` and never on abort. Add a `GET /prepare` paragraph beside the `/start` description: the rule, the 202/409/503 mapping, the reply-slot wait, and that the key checked is the named Bit's `[start] key` read from the registry. Record the six section 7.2 deviations from the spec's section 8 so the mm-renquest mirror can be done from the deep-dive alone.

- [ ] **Step 2: Commit**

```bash
git add docs/MM_TERRARIUM.md BACKLOG.md
git commit -m "docs: deep-dive for /prepare, players, uplink identity and journal"
```

---

## Self-review

**Spec coverage.** 4.1 (Task 3), 4.2 (Task 1), 4.3 (Task 2), 4.4 (Task 4), 4.5 (Task 5), 5.1 and 5.2 (Task 6), 5.3 (Task 7), 6.1 (Task 8), 6.2 (Task 9), 6.3 (Task 10), 6.4 (Tasks 11 and 12), 6.5 (Task 13), section 7 tests are spread across the tasks' Step 1 blocks, section 8 deviations recorded in Task 14. The `PrepareRequest` dataclass field order (`key` first with `repr=False`) requires every later positional constructor call to pass `key` first; Tasks 3, 4 and the tests do.

**Placeholders.** None; the Task 4 Console test names a rig helper the executor must map to the file's existing helper, and says so.

**Type consistency.** `PrepareReply` fields `done/accepted/reason/visible` are the same in Tasks 1, 3, 4. `PrepareDecision(accepted, reason, action, visible)` positional order is the same in Tasks 1, 2, 4. `UplinkAgent` keywords `identity`, `lan_ip` (Task 9) and `journal` (Task 12) match Task 13's constructor call. `Transport.durable` (Task 10) is what Task 12 reads via `getattr`. `bit_completed_event(players=)` (Task 6) matches Task 7's call.
