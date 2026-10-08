# Bidirectional Heartbeat (mm-terrarium) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the beat heartbeat to mm-terrarium: Control answers every `/game/beat` with `/<dev>/beat seq epoch`, tracks per-device link health for the Console, repaints a relinked device, and the Testshroom and contract kit implement the reference device side.

**Architecture:** Two new pure units carry the logic: `devicelink/link_monitor.py` (Control side: who beats, live or missing, loss, RTT, bars) and `harness/beat_link.py` (device side: the LINKING / LINKED / LOOKING / SOLO state machine). `DeviceLinkAgent` wires the first into its inbound dispatch; `harness/o2_shroom.py` and `contract_kit/recorder.py` both drive the second, so the Testshroom and the recorded contract scenarios run the same reference code that firmware and the Flutter app will port.

**Tech Stack:** Python 3 (stdlib only in `control/`, `devicelink/`, `harness/`), pytest, o2litepy, plain ES-module JS under `console/static/` tested with `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md`

**Scope:** mm-terrarium only. mm-tuneshroom gets its own plan once this plan's contract export v4 exists (its replay tests pin that export). mm-devshroom is an issue for Victor, drafted in Task 10.

## Global Constraints

- Run Python through the venv: `.venv/bin/python -m pytest ...`. A fresh worktree has no `.venv`; create the symlink first: `ln -s "$HOME/projects/mm-terrarium/.venv" .venv` from the worktree root. Never use bare `python3` for tests.
- JS tests: `node --test tests/js/*.test.js` (the bare directory form fails).
- Contract constants (spec section 4), exact values: `BEAT_INTERVAL_S = 1.0`, `BEAT_JITTER_S = 0.1`, `LINK_LOST_S = 3.0`, `GRACE_S = 15.0`. `HELLO_INTERVAL_S = 5.0` stays.
- `/game/beat`: up, typespecs `("si", "sii")`, args `("dev", "seq", "rtt_ms")`, `udp-ok`, `pre_role=True`.
- `/<dev>/beat`: down, typespec `("is",)`, args `("seq", "epoch")`, `udp-ok`, `pre_role=True`.
- Epoch: 6 lowercase hex characters, `secrets.token_hex(3)`, minted once per `DeviceLinkAgent`.
- Purely additive on the wire: a client that never sends `/game/beat` must see exactly today's behavior (one `/room` on first contact, nothing per repeated hello).
- Control resends no `/role`, `/validated` or `/room` on a relink; only the current `/leds` frame (spec section 7, contract rule 9).
- The 15 s reap (`stale_timeout`) and Room-bound devices never being reaped are unchanged.
- All outbound JSON goes through `control/wire_json.dumps()`; never `json.dumps` in shipped code.
- No em dashes in any doc, comment or commit message (house style).
- Contract kit `CONTRACT_VERSION` becomes 4.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `devicelink/contract.py` | modify | constants and the two `beat` verb rows |
| `devicelink/protocol.py` | modify | `beat_event`, `parse_beat_args` |
| `devicelink/link_monitor.py` | create | pure per-device link health (Control side) |
| `devicelink/agent.py` | modify | epoch, beat reply, relink repaint, link view, Console notify |
| `console/protocol.py`, `console/agent.py` | modify | `link` field on each device view |
| `harness/terrarium_boot.py` | modify | pass `link_view=agent.link_view` to `ConsoleAgent` |
| `console/static/rooms.js`, `console/static/terrarium.css` | modify | link chip (Missing, bars) |
| `harness/beat_link.py` | create | pure reference device state machine |
| `harness/o2_shroom.py`, `harness/markers.py` | modify | Testshroom beats, `--no-beat`, `LINK STATE:` marker |
| `contract_kit/recorder.py`, `contract_kit/scenarios.py`, `contract_kit/recordings/*.json` | modify/create | beats mode, four beat scenarios, `device.beats` |
| `tools/export_contract.py` | modify | v4, lifecycle values, step schema, replay notes |
| docs (guide, lexicon, deep-dive, liveness spec, player-flow diagram) | modify | the contract and behavior in prose |

---

### Task 1: Contract rows, constants and protocol builders

**Files:**
- Modify: `devicelink/contract.py` (constants after `HELLO_INTERVAL_S` at line 28; rows in `VERB_TABLE`)
- Modify: `devicelink/protocol.py` (after `validated_event`, around line 141)
- Test: `tests/test_devicelink_contract.py`, `tests/test_devicelink_protocol.py`

**Interfaces:**
- Produces: `contract.BEAT_INTERVAL_S`, `BEAT_JITTER_S`, `LINK_LOST_S`, `GRACE_S` (floats); `protocol.beat_event(dev: str, seq: int, epoch: str) -> dict`; `protocol.parse_beat_args(args: list) -> tuple[str, int, int]` returning `(dev, seq, rtt_ms)`, `rtt_ms` 0 when absent, `ValueError` when malformed. `"beat"` appears in `contract.GAME_VERBS`, so `O2LiteTransport` registers `/game/beat` automatically.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_devicelink_contract.py`:

```python
def test_beat_constants():
    assert contract.BEAT_INTERVAL_S == 1.0
    assert contract.BEAT_JITTER_S == 0.1
    assert contract.LINK_LOST_S == 3.0
    assert contract.GRACE_S == 15.0
    assert contract.HELLO_INTERVAL_S == 5.0


def test_beat_rows():
    up = contract.row_for("up", "beat")
    assert up.typespecs == ("si", "sii")
    assert up.args == ("dev", "seq", "rtt_ms")
    assert up.transport == "udp-ok" and up.pre_role
    down = contract.row_for("down", "beat")
    assert down.typespecs == ("is",)
    assert down.args == ("seq", "epoch")
    assert down.transport == "udp-ok" and down.pre_role
    assert "beat" in contract.GAME_VERBS


def test_beat_delivered_through_fake_o2lite_reaches_drain_inbound():
    fake = FakeO2Lite(now=100.0)
    fake.set_services("actl")
    transport = O2LiteTransport()
    transport.start(fake)
    fake.deliver("/game/beat", "sii", ("ie1", 4, 12), timestamp=0.0)
    drained = [env for (_client, env) in transport.drain_inbound()]
    assert ("/game/beat", "sii", ["ie1", 4, 12]) in [
        (e["address"], e["typespec"], e["args"]) for e in drained]
```

Append to `tests/test_devicelink_protocol.py`:

```python
def test_beat_event_shape():
    msg = protocol.beat_event("ie1", 7, "a1b2c3")
    assert msg["address"] == "/ie1/beat"
    assert msg["typespec"] == "is"
    assert msg["args"] == [7, "a1b2c3"]


def test_parse_beat_args_both_forms():
    assert protocol.parse_beat_args(["ie1", 3]) == ("ie1", 3, 0)
    assert protocol.parse_beat_args(["ie1", 3, 41]) == ("ie1", 3, 41)


@pytest.mark.parametrize("bad", [
    [], ["ie1"], [1, 2], ["ie1", "3"], ["ie1", -1], ["ie1", True],
    ["ie1", 3, -5], ["ie1", 3, 1.5], ["ie1", 3, 4, 5],
])
def test_parse_beat_args_rejects_malformed(bad):
    with pytest.raises(ValueError):
        protocol.parse_beat_args(bad)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_devicelink_contract.py tests/test_devicelink_protocol.py -v`
Expected: the new tests FAIL (`AttributeError: ... BEAT_INTERVAL_S`, `KeyError: no up row for verb 'beat'`, `AttributeError: ... beat_event`). `test_protocol_event_builders_cover_every_down_row_in_the_table` still passes.

- [ ] **Step 3: Implement**

In `devicelink/contract.py`, directly after `HELLO_INTERVAL_S = 5.0`:

```python
# The beat heartbeat (spec 2026-10-08-bidirectional-heartbeat-design.md
# section 4). A beat-capable device sends /game/beat every BEAT_INTERVAL_S
# +/- BEAT_JITTER_S and Control answers each with /<dev>/beat. The device
# declares its link lost after LINK_LOST_S with nothing from Control, and
# its slot is gone after GRACE_S (equal to BootConfig.stale_timeout's
# default, the reap). tools/export_contract.py publishes all four.
BEAT_INTERVAL_S = 1.0
BEAT_JITTER_S = 0.1
LINK_LOST_S = 3.0
GRACE_S = 15.0
```

In `VERB_TABLE`, after the `telemetry` up row:

```python
    VerbRow("beat", "up", ("si", "sii"), ("dev", "seq", "rtt_ms"),
            "udp-ok", True,
            "Heartbeat from a beat-capable device (spec 2026-10-08): "
            "sent every BEAT_INTERVAL_S +/- BEAT_JITTER_S once the link "
            "is up. seq restarts at 0 on every link. rtt_ms is the "
            "device's last measured round trip in ms, 0 or absent when "
            "unknown. Control answers each one with /<dev>/beat."),
```

After the `room` down row:

```python
    VerbRow("beat", "down", ("is",), ("seq", "epoch"), "udp-ok", True,
            "Control's reply to every /game/beat: seq is echoed; epoch "
            "is 6 hex characters fixed for the life of one Control "
            "process, so a change means Control restarted."),
```

In `devicelink/protocol.py`, after `validated_event`:

```python
def beat_event(dev: str, seq: int, epoch: str) -> dict:
    """Control's reply to /game/beat (spec 2026-10-08 section 4)."""
    return _event(f"/{dev}/beat", "is", [seq, epoch])


def _non_negative_int(value) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= 0)


def parse_beat_args(args: list) -> tuple[str, int, int]:
    """(dev, seq, rtt_ms) from a /game/beat; rtt_ms is 0 when the device
    sent the two-argument form. ValueError when the shape is wrong
    (contract rule 6: malformed is dropped)."""
    if len(args) not in (2, 3) or not isinstance(args[0], str):
        raise ValueError(f"beat wants dev, seq[, rtt_ms], got {args!r}")
    seq = args[1]
    rtt_ms = args[2] if len(args) == 3 else 0
    if not _non_negative_int(seq) or not _non_negative_int(rtt_ms):
        raise ValueError(f"beat seq and rtt_ms must be ints >= 0, "
                         f"got {args!r}")
    return args[0], seq, rtt_ms
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_devicelink_contract.py tests/test_devicelink_protocol.py -v`
Expected: all PASS, including the existing `test_down_transport_split` (beat is not in its tcp set, so `udp-ok` is expected).

- [ ] **Step 5: Run the contract kit tests and note the expected failures**

Run: `.venv/bin/python -m pytest tests/test_export_contract.py tests/test_contract_scenarios.py -q`
Expected: some export tests may FAIL because the exported verb list changed. Do not fix them here; Task 7 re-exports and bumps the version. Record which tests fail in the commit message body.

- [ ] **Step 6: Commit**

```bash
git add devicelink/contract.py devicelink/protocol.py tests/test_devicelink_contract.py tests/test_devicelink_protocol.py
git commit -m "feat(contract): /game/beat and /<dev>/beat rows and constants"
```

---

### Task 2: LinkMonitor (Control-side link health)

**Files:**
- Create: `devicelink/link_monitor.py`
- Test: `tests/test_link_monitor.py`

**Interfaces:**
- Consumes: `contract.LINK_LOST_S`.
- Produces:
  - `LinkMonitor(lost_after: float = LINK_LOST_S)`
  - `.on_beat(dev: str, seq: int, rtt_ms: int, now: float) -> None`
  - `.beats(dev: str) -> bool`
  - `.view(dev: str, last_seen: float, now: float) -> dict | None`, returning `{"state": "live" | "missing", "bars": int | None, "rtt_ms": int | None, "loss": float | None}`, or `None` for a device that never beat
  - `.forget(dev: str) -> None`, `.clear() -> None`
  - module function `bars(loss: float | None, rtt_ms: int | None) -> int | None`
  - `LOSS_WINDOW = 30`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_link_monitor.py`:

```python
"""LinkMonitor: Control's per-device view of a beat-capable link (spec
2026-10-08 section 7). Pure, so every test drives time by hand."""

import pytest

from devicelink.link_monitor import LOSS_WINDOW, LinkMonitor, bars


def test_a_device_that_never_beat_has_no_view():
    m = LinkMonitor()
    assert m.beats("ie1") is False
    assert m.view("ie1", last_seen=0.0, now=0.0) is None


def test_live_until_lost_after_then_missing():
    m = LinkMonitor(lost_after=3.0)
    m.on_beat("ie1", 0, 0, now=10.0)
    assert m.beats("ie1") is True
    assert m.view("ie1", last_seen=10.0, now=13.0)["state"] == "live"
    assert m.view("ie1", last_seen=10.0, now=13.01)["state"] == "missing"


def test_loss_from_seq_gaps():
    m = LinkMonitor()
    for seq in (0, 1, 2, 4, 5, 7, 8, 9):     # 3 and 6 lost: 8 of 10
        m.on_beat("ie1", seq, 20, now=float(seq))
    v = m.view("ie1", last_seen=9.0, now=9.0)
    assert v["loss"] == pytest.approx(0.2)
    assert v["rtt_ms"] == 20


def test_a_seq_reset_starts_a_new_window():
    m = LinkMonitor()
    for seq in (0, 2, 4):                     # lossy first link
        m.on_beat("ie1", seq, 0, now=float(seq))
    m.on_beat("ie1", 0, 0, now=10.0)          # relink: seq back to 0
    m.on_beat("ie1", 1, 0, now=11.0)
    assert m.view("ie1", last_seen=11.0, now=11.0)["loss"] == 0.0


def test_a_late_duplicate_is_ignored():
    m = LinkMonitor()
    for seq in (5, 6, 7):
        m.on_beat("ie1", seq, 0, now=float(seq))
    m.on_beat("ie1", 6, 0, now=8.0)           # reordered UDP
    assert m.view("ie1", last_seen=8.0, now=8.0)["loss"] == 0.0


def test_window_is_bounded():
    m = LinkMonitor()
    for seq in range(LOSS_WINDOW + 10):
        m.on_beat("ie1", seq, 0, now=float(seq))
    assert m.view("ie1", last_seen=0.0, now=0.0)["loss"] == 0.0


def test_one_beat_has_no_loss_figure_yet():
    m = LinkMonitor()
    m.on_beat("ie1", 0, 0, now=0.0)
    v = m.view("ie1", last_seen=0.0, now=0.0)
    assert v["loss"] is None and v["bars"] is None


def test_rtt_zero_means_unknown():
    m = LinkMonitor()
    m.on_beat("ie1", 0, 0, now=0.0)
    assert m.view("ie1", last_seen=0.0, now=0.0)["rtt_ms"] is None


def test_forget_and_clear():
    m = LinkMonitor()
    m.on_beat("ie1", 0, 0, now=0.0)
    m.on_beat("ie2", 0, 0, now=0.0)
    m.forget("ie1")
    assert m.beats("ie1") is False and m.beats("ie2") is True
    m.clear()
    assert m.beats("ie2") is False


@pytest.mark.parametrize("loss,rtt,want", [
    (None, 10, None),
    (0.0, 10, 4), (0.02, 49, 4),
    (0.03, 10, 3), (0.0, 99, 3),
    (0.10, 10, 2), (0.0, 249, 2),
    (0.20, 10, 1), (0.0, 400, 1),
    (0.0, None, 4),
])
def test_bars(loss, rtt, want):
    assert bars(loss, rtt) == want
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_link_monitor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devicelink.link_monitor'`.

- [ ] **Step 3: Implement**

Create `devicelink/link_monitor.py`:

```python
"""Control's per-device view of a beat-capable link (spec
docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md
section 7). Pure: no transport, no clock of its own.

Only a device that has sent /game/beat has an entry; a legacy client
(plain 5 s hello) never appears here and the Console shows no link state
for it. "missing" is display only: the 15 s reap in GameServer.reap_stale
still decides when a slot is freed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from devicelink.contract import LINK_LOST_S

# How many recent beats the loss figure covers.
LOSS_WINDOW = 30


def bars(loss: float | None, rtt_ms: int | None) -> int | None:
    """Signal bars, 1 to 4, from loss and round trip. None until there is
    a loss figure. An unknown rtt counts as fast: loss alone decides."""
    if loss is None:
        return None
    rtt = rtt_ms or 0
    if loss <= 0.02 and rtt < 50:
        return 4
    if loss <= 0.05 and rtt < 100:
        return 3
    if loss <= 0.15 and rtt < 250:
        return 2
    return 1


@dataclass
class _Link:
    seqs: deque = field(default_factory=lambda: deque(maxlen=LOSS_WINDOW))
    rtt_ms: int | None = None


class LinkMonitor:
    def __init__(self, lost_after: float = LINK_LOST_S):
        self._lost_after = lost_after
        self._links: dict[str, _Link] = {}

    def on_beat(self, dev: str, seq: int, rtt_ms: int, now: float) -> None:
        """Record one beat. seq 0, or a seq older than the window, starts a
        new window (the device relinked); a seq at or below the newest one
        is a duplicate or reordered packet and is ignored."""
        link = self._links.setdefault(dev, _Link())
        if rtt_ms > 0:
            link.rtt_ms = rtt_ms
        seqs = link.seqs
        if seq == 0 or not seqs or seq < seqs[0]:
            seqs.clear()
            seqs.append(seq)
        elif seq > seqs[-1]:
            seqs.append(seq)

    def beats(self, dev: str) -> bool:
        return dev in self._links

    def _loss(self, link: _Link) -> float | None:
        seqs = link.seqs
        if len(seqs) < 2:
            return None
        span = seqs[-1] - seqs[0] + 1
        return max(0.0, 1.0 - len(seqs) / span)

    def view(self, dev: str, last_seen: float, now: float) -> dict | None:
        """The Console's link read-out for dev, or None for a device that
        never beat. last_seen is DevicePool's (any traffic counts)."""
        link = self._links.get(dev)
        if link is None:
            return None
        loss = self._loss(link)
        state = "missing" if now - last_seen > self._lost_after else "live"
        return {"state": state, "bars": bars(loss, link.rtt_ms),
                "rtt_ms": link.rtt_ms,
                "loss": None if loss is None else round(loss, 3)}

    def forget(self, dev: str) -> None:
        self._links.pop(dev, None)

    def clear(self) -> None:
        self._links.clear()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_link_monitor.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add devicelink/link_monitor.py tests/test_link_monitor.py
git commit -m "feat(devicelink): LinkMonitor for per-device beat link health"
```

---

### Task 3: DeviceLinkAgent answers beats, repaints a relink, reports link state

**Files:**
- Modify: `devicelink/agent.py`: imports; `__init__` (signature at line 98, new state near `self._canvas_urls` at about line 156); `_handle` (verb dispatch at about line 1299); `_on_hello` (line 1334); `poll` (line 855); `_forget_reaped` (line 880); `unwire_room` (line 711)
- Test: create `tests/test_devicelink_beat.py`

**Interfaces:**
- Consumes: `protocol.beat_event`, `protocol.parse_beat_args` (Task 1); `LinkMonitor` (Task 2).
- Produces:
  - `DeviceLinkAgent(..., epoch: str | None = None)`
  - `agent.epoch: str`
  - `agent.link_view() -> dict[str, dict]`, mapping dev to the `LinkMonitor.view` dict, for pooled beat-capable devices only
  - `GameServer.notify_devices_changed()` is called when any device's `(state, bars)` changes, checked at most once a second

- [ ] **Step 1: Write the failing tests**

Create `tests/test_devicelink_beat.py`:

```python
"""DeviceLinkAgent and the beat heartbeat (spec 2026-10-08 sections 4 and
7), against the in-process FakeServer from test_devicelink_agent."""

import re

import pytest

pytest.importorskip("luxaeterna")

from bits.test.test_bit import TestBit
from control.engine import GameServer
from devicelink.agent import DeviceLinkAgent
from tests.test_devicelink_agent import (FakeServer, _Clock, _hello, _join)


def _rig(epoch="abc123"):
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, epoch=epoch)
    gs.load_bit("test_bit")
    return gs, server, agent, clk


def _beat(server, agent, seq, rtt=None, client="c1", dev="ie1"):
    args = [dev, seq] if rtt is None else [dev, seq, rtt]
    server.deliver(client, "/game/beat", "si" if rtt is None else "sii", args)
    agent.poll()


def test_default_epoch_is_six_hex():
    gs = GameServer({"test_bit": TestBit})
    agent = DeviceLinkAgent(gs, FakeServer(), clock=lambda: 0.0)
    assert re.fullmatch(r"[0-9a-f]{6}", agent.epoch)


def test_each_beat_is_answered_with_its_seq_and_the_epoch():
    gs, server, agent, clk = _rig()
    _hello(server, agent)
    for seq in (0, 1, 2):
        _beat(server, agent, seq)
    replies = server.addressed("/ie1/beat")
    assert [m["args"] for m in replies] == [[0, "abc123"], [1, "abc123"],
                                            [2, "abc123"]]
    assert all(m["typespec"] == "is" for m in replies)


def test_the_rtt_form_is_answered_too():
    gs, server, agent, clk = _rig()
    _hello(server, agent)
    _beat(server, agent, 0, rtt=18)
    assert server.addressed("/ie1/beat")[-1]["args"] == [0, "abc123"]
    assert agent.link_view()["ie1"]["rtt_ms"] == 18


def test_a_beat_before_hello_is_dropped():
    gs, server, agent, clk = _rig()
    server.arrive("c1")
    _beat(server, agent, 0)
    assert server.addressed("/ie1/beat") == []
    assert gs.devices.known("ie1") is False


def test_a_malformed_beat_is_dropped():
    gs, server, agent, clk = _rig()
    _hello(server, agent)
    server.deliver("c1", "/game/beat", "ss", ["ie1", "zero"])
    agent.poll()
    assert server.addressed("/ie1/beat") == []


def test_a_beat_keeps_the_device_alive_past_the_reap():
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.load_bit("test_bit")
    _hello(server, agent)
    for seq in range(1, 15):
        clk.advance(1.0)
        _beat(server, agent, seq)
    assert gs.devices.known("ie1") is True


def test_link_view_live_then_missing_and_none_for_legacy():
    gs, server, agent, clk = _rig()
    _hello(server, agent, dev="ie1")
    _hello(server, agent, client="c2", dev="ie2")   # legacy: never beats
    _beat(server, agent, 0)
    assert agent.link_view()["ie1"]["state"] == "live"
    assert "ie2" not in agent.link_view()
    clk.advance(3.5)
    agent.poll()
    assert agent.link_view()["ie1"]["state"] == "missing"


def test_a_legacy_repeated_hello_still_gets_one_room_and_nothing_else():
    gs, server, agent, clk = _rig()
    for _ in range(3):
        _hello(server, agent)
    assert len(server.addressed("/ie1/room")) == 1
    assert server.addressed("/ie1/beat") == []


def test_relink_of_a_beating_role_holder_resends_its_frame_and_no_role():
    gs, server, agent, clk = _rig()
    _hello(server, agent)
    _beat(server, agent, 0)
    _join(server, agent, gs)
    for _ in range(5):                       # let the role's frame settle
        clk.advance(1.0 / 44.0)
        agent.poll()
    leds_before = len(server.addressed("/ie1/leds"))
    roles_before = len(server.addressed("/ie1/role"))
    rooms_before = len(server.addressed("/ie1/room"))
    clk.advance(1.0 / 44.0)
    agent.poll()
    assert len(server.addressed("/ie1/leds")) == leds_before  # static look

    _hello(server, agent)                    # relink: hello on the new link
    _beat(server, agent, 0)

    assert len(server.addressed("/ie1/leds")) == leds_before + 1
    assert len(server.addressed("/ie1/role")) == roles_before
    assert len(server.addressed("/ie1/room")) == rooms_before


def test_a_link_state_change_notifies_the_console():
    gs, server, agent, clk = _rig()
    calls = []
    gs.notify_devices_changed = lambda: calls.append(clk.t)
    _hello(server, agent)
    _beat(server, agent, 0)
    _beat(server, agent, 1)                  # two beats: bars are known
    clk.advance(1.0)
    agent.poll()                             # first check: live, 4 bars
    n = len(calls)
    assert n >= 1
    clk.advance(1.0)
    agent.poll()                             # unchanged: no new notify
    assert len(calls) == n
    clk.advance(3.0)
    agent.poll()                             # 5 s silent: missing
    assert len(calls) == n + 1


def test_reaping_forgets_the_link():
    clk = _Clock()
    gs = GameServer({"test_bit": TestBit}, clock=clk)
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, clock=clk, stale_timeout=10.0)
    gs.load_bit("test_bit")
    _hello(server, agent)
    _beat(server, agent, 0)
    clk.advance(11.0)
    agent.poll()
    assert gs.devices.known("ie1") is False
    assert agent.link_view() == {}
    _hello(server, agent)                    # back as a new device
    assert len(server.addressed("/ie1/room")) == 2   # first contact again
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_devicelink_beat.py -v`
Expected: FAIL with `TypeError: DeviceLinkAgent.__init__() got an unexpected keyword argument 'epoch'`.

- [ ] **Step 3: Implement in `devicelink/agent.py`**

Imports (top of file, with the existing imports):

```python
import secrets

from devicelink.link_monitor import LinkMonitor
```

Signature: add `epoch: str | None = None` as the last keyword in `__init__`. In the body, after `self._canvas_urls: dict[str, str] = {}`:

```python
        # The beat heartbeat (spec 2026-10-08-bidirectional-heartbeat-
        # design.md). epoch is fixed for this agent's life and rides every
        # /<dev>/beat reply, so a device that sees it change knows Control
        # restarted. Injectable only so the contract kit records
        # deterministically.
        self.epoch = epoch or secrets.token_hex(3)
        self._links = LinkMonitor()
        # dev -> (state, bars) as last reported to the Console, and when
        # the next comparison is due (once a second is plenty for a
        # human-facing read-out).
        self._link_marks: dict[str, tuple] = {}
        self._next_link_check = 0.0
```

In `_handle`, add a branch beside the others (before the final `else`):

```python
        elif verb == "beat":
            self._on_beat(client, dev, env.args)
```

New method, next to `_on_canvas`:

```python
    def _on_beat(self, client, dev: str, args: list) -> None:
        """/game/beat: answer at once with /<dev>/beat seq epoch (spec
        2026-10-08 section 7). A beat from a dev not in the pool is
        dropped: hello creates the entry, and the device's next beat a
        second later is answered."""
        try:
            _dev, seq, rtt_ms = protocol.parse_beat_args(args)
        except ValueError as exc:
            logger.warning("dropping malformed beat from %s: %s", dev, exc)
            return
        if not self.game_server.devices.known(dev):
            return
        self.transport.bind_dev(dev, client)
        self._links.on_beat(dev, seq, rtt_ms, self._clock())
        self._send(dev, protocol.beat_event(dev, seq, self.epoch))
```

In `_on_hello`, after `self.transport.bind_dev(...)` and before the `/room` block:

```python
        if (self.game_server.devices.get(dev) is not None
                and self._links.beats(dev)):
            # A relink inside the grace window (spec 2026-10-08 section 7):
            # the device kept its role (contract rule 9), but its Looking
            # pulse painted over the display, so the next render sends its
            # current frame whole. Nothing else is re-sent.
            self._last_frames.pop(dev, None)
```

In `_forget_reaped`, as its first line (before the early return, so a reaped role holder is forgotten too):

```python
        self._links.forget(dev)
```

In `unwire_room`, at the end of the method body (the pool is cleared with the Room):

```python
        self._links.clear()
        self._link_marks.clear()
```

Public view, next to `canvas_urls`:

```python
    def link_view(self) -> dict:
        """dev -> LinkMonitor.view for every pooled beat-capable device,
        for the Console."""
        now = self._clock()
        out = {}
        for info in self.game_server.devices.all():
            view = self._links.view(info.dev, info.last_seen, now)
            if view is not None:
                out[info.dev] = view
        return out

    def _tick_link_marks(self) -> None:
        """Tell the Console when any device's (state, bars) changed. At
        most once a second: bars move slowly and the Console only needs
        the edges."""
        now = self._clock()
        if now < self._next_link_check:
            return
        self._next_link_check = now + 1.0
        marks = {dev: (v["state"], v["bars"])
                 for dev, v in self.link_view().items()}
        if marks != self._link_marks:
            self._link_marks = marks
            self.game_server.notify_devices_changed()
```

In `poll`, directly after the `reap_stale` loop:

```python
        self._tick_link_marks()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_devicelink_beat.py -v`
Expected: all PASS. If `test_relink_of_a_beating_role_holder_resends_its_frame_and_no_role` sees an extra `/leds` before the relink, raise the settle loop from 5 to 50 ticks; the assertion that matters is the `+1` after the relink.

- [ ] **Step 5: Run the whole devicelink and lobby suites**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_lobby_agent.py tests/test_devicelink_frames.py tests/test_device_pool.py -q`
Expected: all PASS (legacy behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_beat.py
git commit -m "feat(devicelink): answer /game/beat, repaint a relink, report link state"
```

---

### Task 4: Console shows live, missing and signal bars

**Files:**
- Modify: `console/protocol.py:79` (`device_view`)
- Modify: `console/agent.py:40-45` (constructor), `:84-86` (store), `:892-907` (`_devices_view`)
- Modify: `harness/terrarium_boot.py:2001-2004` (`ConsoleAgent(...)` call)
- Modify: `console/static/rooms.js:117-125` (device rows), `console/static/terrarium.css` (one rule)
- Test: `tests/test_console_agent.py`, `tests/js/rooms_panel.test.js`

**Interfaces:**
- Consumes: `DeviceLinkAgent.link_view()` (Task 3).
- Produces: `device_view(info, role_name, url=None, muted=False, fixture=None, link=None)`, which adds a `"link"` key (`None` or the view dict); `ConsoleAgent(..., link_view=None)`.

- [ ] **Step 1: Write the failing Python test**

Append to `tests/test_console_agent.py` (reuse that file's existing fixture that builds a `ConsoleAgent` around a `GameServer` with a fake server; if its helper is named differently, follow its pattern exactly):

```python
def test_devices_view_carries_link_state():
    from console.agent import ConsoleAgent
    from control.engine import GameServer
    from bits.test.test_bit import TestBit

    gs = GameServer({"test_bit": TestBit})
    gs.devices.hello("ie1", "one", "1")
    gs.devices.hello("ie2", "two", "1")

    class _Srv:
        def broadcast(self, msg):
            pass

    agent = ConsoleAgent(gs, _Srv(), link_view=lambda: {
        "ie1": {"state": "missing", "bars": 2, "rtt_ms": 40, "loss": 0.1}})
    views = {v["dev"]: v for v in agent._devices_view()}
    assert views["ie1"]["link"]["state"] == "missing"
    assert views["ie2"]["link"] is None


def test_devices_view_without_a_link_source():
    from console.agent import ConsoleAgent
    from control.engine import GameServer
    from bits.test.test_bit import TestBit

    gs = GameServer({"test_bit": TestBit})
    gs.devices.hello("ie1", "one", "1")

    class _Srv:
        def broadcast(self, msg):
            pass

    agent = ConsoleAgent(gs, _Srv())
    assert agent._devices_view()[0]["link"] is None
```

- [ ] **Step 2: Write the failing JS assertions**

In `tests/js/rooms_panel.test.js`, directly after the line `assert.ok(!html().includes("Wanderer"), "departed device disappears");`, insert:

```js
  // Beat link state (spec 2026-10-08 section 7): Missing chip, signal
  // bars with the round trip as a tooltip, nothing for a legacy device.
  send({ event: "devices_changed",
         devices: [
           { dev: "ie1", name: "Testshroom 1", role: "player",
             link: { state: "live", bars: 3, rtt_ms: 42, loss: 0.03 } },
           { dev: "ie7", name: "Newcomer", role: null,
             link: { state: "missing", bars: 1, rtt_ms: null, loss: 0.4 } },
           { dev: "ie8", name: "Legacy", role: null, link: null }] });
  assert.ok(/Testshroom 1[\s\S]*?▮▮▮▯/.test(html()), "3 of 4 bars");
  assert.ok(html().includes("42 ms"), "rtt tooltip");
  assert.ok(/Newcomer[\s\S]*?Missing/.test(html()), "missing chip");
  assert.ok(!/Legacy[\s\S]*?(Missing|▮)/.test(html()), "legacy shows no link");
```

- [ ] **Step 3: Run both to verify failure**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py -k link -v` and `node --test tests/js/rooms_panel.test.js`
Expected: Python FAIL with `TypeError: ... unexpected keyword argument 'link_view'`; JS FAIL on "3 of 4 bars".

- [ ] **Step 4: Implement**

`console/protocol.py`:

```python
def device_view(info, role_name, url=None, muted=False, fixture=None,
                link=None) -> dict:
    return {"dev": info.dev, "name": info.name, "role": role_name, "url": url,
            "muted": muted, "instrument": info.carried.name,
            "fixture": fixture, "link": link}
```

`console/agent.py`: add `link_view=None` as the last constructor keyword; store it beside `self._canvas_urls`:

```python
        # Optional Callable[[], dict] of dev -> beat link view, from
        # DeviceLinkAgent.link_view() (spec 2026-10-08 section 7). None
        # yields link: null on every device.
        self._link_view = link_view
```

In `_devices_view`, after `urls = ...`:

```python
        links = self._link_view() if self._link_view else {}
```

and pass `links.get(info.dev)` as the new last argument of `protocol.device_view(...)`.

`harness/terrarium_boot.py`: in the `ConsoleAgent(...)` call, after `canvas_urls=agent.canvas_urls,` add `link_view=agent.link_view,`.

`console/static/rooms.js`: above `function deviceTag(dev)` add:

```js
// Beat link read-out (spec 2026-10-08 section 7). null for a legacy device.
function linkChip(link) {
  if (!link) return null;
  if (link.state === "missing") return mk("span", "chip rose linktag", "Missing");
  if (link.bars == null) return null;
  const chip = mk("span", "mono linktag",
    "▮".repeat(link.bars) + "▯".repeat(4 - link.bars));
  if (link.rtt_ms != null) chip.title = `${link.rtt_ms} ms`;
  return chip;
}
```

In the device-row loop, after `row.appendChild(mk("span", \`${chipClass} roletag\`, label));`:

```js
    const linkEl = linkChip(dev.link);
    if (linkEl) row.appendChild(linkEl);
```

The JS test reads `innerHTML`; if `tests/js/_dom_stub.js` does not serialize `title`, change the assertion target to the chip text plus a separate check of `title` through the stub's element API, keeping the meaning (rtt shown on hover).

`console/static/terrarium.css`, after the `.chip.dim` rule:

```css
.linktag { letter-spacing: 1px; color: var(--sage); }
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py tests/test_console_js.py -q` and `node --test tests/js/*.test.js`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add console/ harness/terrarium_boot.py tests/test_console_agent.py tests/js/rooms_panel.test.js
git commit -m "feat(console): show beat link state and signal bars per device"
```

---

### Task 5: BeatLink, the reference device state machine

**Files:**
- Create: `harness/beat_link.py`
- Test: `tests/test_beat_link.py`

**Interfaces:**
- Consumes: the contract constants (Task 1).
- Produces:
  - States: `DOWN = "down"`, `LINKING = "linking"`, `LINKED = "linked"`, `LOOKING = "looking"`, `SOLO = "solo"`.
  - Actions (frozen dataclasses): `SendHello()`, `SendBeat(seq: int, rtt_ms: int)`, `DropTransport()`, `DropRole()`, `StateChanged(state: str)`.
  - `BeatLink(*, interval=BEAT_INTERVAL_S, jitter=BEAT_JITTER_S, lost_after=LINK_LOST_S, grace=GRACE_S, legacy_hello=HELLO_INTERVAL_S, rng=None)`
  - Attributes: `.state: str`, `.armed: bool`, `.epoch: str | None`, `.rtt_ms: int`
  - Methods:
    - `.link_up(now: float) -> list`
    - `.link_down(now: float) -> list`
    - `.on_control_message(now: float) -> None`
    - `.on_beat_reply(now: float, seq: int, epoch: str) -> list`
    - `.tick(now: float) -> list`
    - `.next_due() -> float | None`
  - Time is the device's own local seconds, never O2 time: clock sync can be lost on a relink, and the heartbeat must keep running through that.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_beat_link.py`:

```python
"""BeatLink: the device side of the beat heartbeat (spec 2026-10-08
section 6.1). Jitter 0 throughout, so every time is exact."""

from harness.beat_link import (DOWN, LINKED, LINKING, LOOKING, SOLO, BeatLink,
                               DropRole, DropTransport, SendBeat, SendHello,
                               StateChanged)


def _bl():
    return BeatLink(jitter=0.0)


def _sends(actions, kind):
    return [a for a in actions if isinstance(a, kind)]


def test_link_up_sends_hello_and_beat_zero():
    bl = _bl()
    acts = bl.link_up(0.0)
    assert acts[:2] == [SendHello(), SendBeat(0, 0)]
    assert StateChanged(LINKING) in acts
    assert bl.state == LINKING and bl.armed is False


def test_beats_every_interval_with_increasing_seq():
    bl = _bl()
    bl.link_up(0.0)
    assert _sends(bl.tick(0.5), SendBeat) == []
    assert _sends(bl.tick(1.0), SendBeat) == [SendBeat(1, 0)]
    assert _sends(bl.tick(2.0), SendBeat) == [SendBeat(2, 0)]


def test_first_reply_arms_and_links_and_measures_rtt():
    bl = _bl()
    bl.link_up(0.0)
    acts = bl.on_beat_reply(0.025, 0, "abc123")
    assert StateChanged(LINKED) in acts
    assert bl.armed and bl.state == LINKED
    assert bl.epoch == "abc123" and bl.rtt_ms == 25
    assert _sends(bl.tick(1.0), SendBeat) == [SendBeat(1, 25)]


def test_unarmed_keeps_the_legacy_hello_and_never_looks():
    bl = _bl()
    bl.link_up(0.0)
    hellos = []
    for t in range(1, 21):
        hellos += _sends(bl.tick(float(t)), SendHello)
    assert len(hellos) == 4                   # t = 5, 10, 15, 20
    assert bl.state == LINKING


def test_three_seconds_of_silence_means_looking_and_drop_transport():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    assert StateChanged(LOOKING) not in bl.tick(2.99)
    acts = bl.tick(3.0)
    assert DropTransport() in acts and StateChanged(LOOKING) in acts
    assert bl.state == LOOKING
    assert _sends(bl.tick(4.0), SendBeat) == []        # stops beating


def test_any_control_message_resets_the_lost_timer():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.on_control_message(2.5)               # e.g. a /leds frame
    bl.tick(5.0)
    assert bl.state == LINKED
    assert StateChanged(LOOKING) in bl.tick(5.5)


def test_solo_at_grace_after_the_last_message():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.tick(3.0)                             # LOOKING
    assert bl.tick(14.9) == []
    assert StateChanged(SOLO) in bl.tick(15.0)


def test_relink_keeps_looking_until_the_first_reply():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.tick(3.0)                             # LOOKING, transport dropped
    acts = bl.link_up(5.0)
    assert acts[:2] == [SendHello(), SendBeat(0, 0)]   # seq restarts
    assert bl.state == LOOKING and bl.armed is False
    assert StateChanged(LINKED) in bl.on_beat_reply(5.01, 0, "abc123")


def test_a_transport_drop_from_below_while_linked_is_looking_at_once():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    assert StateChanged(LOOKING) in bl.link_down(1.0)
    assert StateChanged(SOLO) in bl.tick(16.0)


def test_a_transport_drop_before_ever_linking_is_just_down():
    bl = _bl()
    bl.link_up(0.0)
    bl.link_down(1.0)
    assert bl.state == DOWN
    assert bl.tick(30.0) == []


def test_epoch_change_drops_role_and_rehellos():
    bl = _bl()
    bl.link_up(0.0)
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.tick(1.0)
    acts = bl.on_beat_reply(1.0, 1, "ffffff")
    assert acts[:2] == [DropRole(), SendHello()]
    assert bl.epoch == "ffffff" and bl.state == LINKED


def test_next_due():
    bl = _bl()
    assert bl.next_due() is None
    bl.link_up(0.0)
    assert bl.next_due() == 1.0
    bl.on_beat_reply(0.0, 0, "abc123")
    bl.tick(2.0)
    assert bl.next_due() == 3.0              # lost check and beat coincide


def test_jitter_stays_inside_the_band():
    import random
    bl = BeatLink(rng=random.Random(7))
    bl.link_up(0.0)
    t, last = 0.0, 0.0
    gaps = []
    while len(gaps) < 50:
        t += 0.01
        if _sends(bl.tick(t), SendBeat):
            gaps.append(t - last)
            last = t
    assert all(0.88 <= g <= 1.12 for g in gaps)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_beat_link.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.beat_link'`.

- [ ] **Step 3: Implement**

Create `harness/beat_link.py`:

```python
"""The device side of the beat heartbeat (spec
docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md
section 6.1). Pure: no sockets and no clock of its own. The caller feeds
it the device's LOCAL time in seconds (never O2 time, which goes away
during a relink) and carries out the actions it returns.

harness/o2_shroom.py drives it over real o2lite, and
contract_kit/recorder.py drives it with jitter 0 to record the beat
scenarios. mm-devshroom firmware and the mm-tuneshroom app port this
state machine; this file is the reference they are checked against.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from devicelink.contract import (BEAT_INTERVAL_S, BEAT_JITTER_S, GRACE_S,
                                 HELLO_INTERVAL_S, LINK_LOST_S)

DOWN = "down"          # no transport, never linked: display untouched
LINKING = "linking"    # transport up, no reply yet on this link
LINKED = "linked"      # a reply arrived on this link
LOOKING = "looking"    # lost: show the white Looking pulse
SOLO = "solo"          # lost past the grace window: show Solo

_SEQ_WRAP = 2 ** 31
_RTT_MEMORY = 32       # how many unanswered seqs keep a send time


@dataclass(frozen=True)
class SendHello:
    pass


@dataclass(frozen=True)
class SendBeat:
    seq: int
    rtt_ms: int


@dataclass(frozen=True)
class DropTransport:
    pass


@dataclass(frozen=True)
class DropRole:
    pass


@dataclass(frozen=True)
class StateChanged:
    state: str


class BeatLink:
    def __init__(self, *, interval: float = BEAT_INTERVAL_S,
                 jitter: float = BEAT_JITTER_S,
                 lost_after: float = LINK_LOST_S, grace: float = GRACE_S,
                 legacy_hello: float = HELLO_INTERVAL_S,
                 rng: random.Random | None = None):
        self._interval = interval
        self._jitter = jitter
        self._lost_after = lost_after
        self._grace = grace
        self._legacy_hello = legacy_hello
        self._rng = rng or random.Random()
        self.state = DOWN
        self.armed = False
        self.epoch: str | None = None
        self.rtt_ms = 0
        self._up = False
        self._seq = 0
        self._sent_at: dict[int, float] = {}
        self._next_beat: float | None = None
        self._next_hello: float | None = None
        self._last_heard: float | None = None
        self._lost_since: float | None = None

    # --- helpers -----------------------------------------------------------

    def _gap(self) -> float:
        if self._jitter <= 0:
            return self._interval
        return self._interval + self._rng.uniform(-self._jitter, self._jitter)

    def _set(self, state: str, out: list) -> None:
        if state != self.state:
            self.state = state
            out.append(StateChanged(state))

    def _beat(self, now: float) -> SendBeat:
        seq = self._seq
        self._sent_at[seq] = now
        if len(self._sent_at) > _RTT_MEMORY:
            del self._sent_at[min(self._sent_at)]
        self._seq = (self._seq + 1) % _SEQ_WRAP
        return SendBeat(seq, self.rtt_ms)

    @staticmethod
    def _advance(due: float, step: float, now: float) -> float:
        nxt = due + step
        return nxt if nxt > now else now + step

    # --- inputs ------------------------------------------------------------

    def link_up(self, now: float) -> list:
        """The transport came up: hello, then beat 0. The display stays in
        LOOKING or SOLO until a reply proves Control is there."""
        out: list = [SendHello()]
        self._up = True
        self.armed = False
        self._seq = 0
        self._sent_at.clear()
        out.append(self._beat(now))
        self._next_beat = now + self._gap()
        self._next_hello = now + self._legacy_hello
        self._last_heard = now
        if self.state == DOWN:
            self._set(LINKING, out)
        return out

    def link_down(self, now: float) -> list:
        """The transport dropped from below (a socket error)."""
        out: list = []
        self._up = False
        self._next_beat = self._next_hello = None
        if self.state == LINKED:
            self._lost_since = now
            self._set(LOOKING, out)
        elif self.state == LINKING:
            self._set(DOWN, out)
        self.armed = False
        return out

    def on_control_message(self, now: float) -> None:
        """Any down message counts as proof of life, not only a beat
        reply: a /leds frame or a /role proves Control just as well."""
        if self._up:
            self._last_heard = now

    def on_beat_reply(self, now: float, seq: int, epoch: str) -> list:
        out: list = []
        if not self._up:
            return out
        self.on_control_message(now)
        sent = self._sent_at.pop(seq, None)
        if sent is not None:
            self.rtt_ms = max(0, round((now - sent) * 1000))
        if self.epoch is not None and epoch != self.epoch:
            # Control restarted and holds nothing for this device.
            out += [DropRole(), SendHello()]
        self.epoch = epoch
        self.armed = True
        self._lost_since = None
        self._set(LINKED, out)
        return out

    def tick(self, now: float) -> list:
        out: list = []
        if self._up:
            if self._next_beat is not None and now >= self._next_beat:
                out.append(self._beat(now))
                self._next_beat = self._advance(self._next_beat,
                                                self._gap(), now)
            if (not self.armed and self._next_hello is not None
                    and now >= self._next_hello):
                out.append(SendHello())
                self._next_hello = self._advance(self._next_hello,
                                                 self._legacy_hello, now)
            if (self.armed and self.state == LINKED
                    and now - self._last_heard >= self._lost_after):
                self._lost_since = self._last_heard
                self._up = False
                self.armed = False
                self._next_beat = self._next_hello = None
                out.append(DropTransport())
                self._set(LOOKING, out)
        if (self.state == LOOKING and self._lost_since is not None
                and now - self._lost_since >= self._grace):
            self._set(SOLO, out)
        return out

    def next_due(self) -> float | None:
        """The earliest time tick() has something to do, for a caller
        that steps time (contract_kit/recorder.py)."""
        due = []
        if self._up and self._next_beat is not None:
            due.append(self._next_beat)
        if self._up and not self.armed and self._next_hello is not None:
            due.append(self._next_hello)
        if self._up and self.armed and self.state == LINKED:
            due.append(self._last_heard + self._lost_after)
        if self.state == LOOKING and self._lost_since is not None:
            due.append(self._lost_since + self._grace)
        return min(due) if due else None
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_beat_link.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/beat_link.py tests/test_beat_link.py
git commit -m "feat(harness): BeatLink reference device state machine"
```

---

### Task 6: The Testshroom beats

**Files:**
- Modify: `harness/markers.py` (new marker plus `INFO_MARKERS`)
- Modify: `harness/o2_shroom.py`: argparse (about line 463); `on_down` and its `kinds` loop (lines 661-682); initial `send_hello()` (about line 703); the tick loop (lines 741-755)
- Test: `tests/test_markers.py`, `tests/test_o2_shroom.py`

**Interfaces:**
- Consumes: `BeatLink` and its actions (Task 5); `ShroomClient.reset_for_lobby()` (existing, `harness/shroom_client.py:315`), used for `DropRole`.
- Produces: `markers.DEVICE_LINK_STATE = "LINK STATE:"`, printed as `LINK STATE: <state>` on every `StateChanged`; flag `--no-beat`; `o2_shroom.run_beat_actions(actions, *, send_beat, send_hello, drop_transport, drop_role, say) -> None`, a pure dispatcher so it can be tested without o2lite.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_markers.py`:

```python
def test_link_state_marker_is_info_and_emitted_by_o2_shroom():
    import ast

    import harness.o2_shroom
    assert markers.DEVICE_LINK_STATE == "LINK STATE:"
    assert markers.DEVICE_LINK_STATE in markers.INFO_MARKERS.values()
    tree = ast.parse(inspect.getsource(harness.o2_shroom))
    assert any(isinstance(n, ast.Attribute) and n.attr == "DEVICE_LINK_STATE"
               for n in ast.walk(tree))
```

Append to `tests/test_o2_shroom.py`:

```python
def test_run_beat_actions_dispatches_each_kind():
    from harness.beat_link import (DropRole, DropTransport, SendBeat,
                                   SendHello, StateChanged)
    from harness.o2_shroom import run_beat_actions
    log = []
    run_beat_actions(
        [SendHello(), SendBeat(3, 17), DropTransport(), DropRole(),
         StateChanged("looking")],
        send_beat=lambda seq, rtt: log.append(("beat", seq, rtt)),
        send_hello=lambda: log.append(("hello",)),
        drop_transport=lambda: log.append(("drop",)),
        drop_role=lambda: log.append(("role",)),
        say=lambda line: log.append(("say", line)))
    assert log == [("hello",), ("beat", 3, 17), ("drop",), ("role",),
                   ("say", "LINK STATE: looking")]


def test_no_beat_flag_parses():
    import harness.o2_shroom as o2s
    src = inspect.getsource(o2s.main)
    assert '"--no-beat"' in src
```

Add `import inspect` to the imports at the top of `tests/test_o2_shroom.py`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_markers.py tests/test_o2_shroom.py -k "link_state or beat" -v`
Expected: FAIL (`AttributeError: ... DEVICE_LINK_STATE`, `ImportError: cannot import name 'run_beat_actions'`).

- [ ] **Step 3: Implement**

`harness/markers.py`, after `DEVICE_JOIN_DENIED`:

```python
# The beat heartbeat's device-side state changed (spec 2026-10-08 section
# 6.1): "LINK STATE: linking|linked|looking|solo|down". Printed by the
# Testshroom, watched in live checks, never fatal.
DEVICE_LINK_STATE = "LINK STATE:"
```

and add `"DEVICE_LINK_STATE": DEVICE_LINK_STATE,` to `INFO_MARKERS`.

`harness/o2_shroom.py`, module level (near `next_heartbeat_time`), with `from harness.beat_link import (BeatLink, DropRole, DropTransport, SendBeat, SendHello, StateChanged)` added to the imports:

```python
def run_beat_actions(actions, *, send_beat, send_hello, drop_transport,
                     drop_role, say) -> None:
    """Carry out BeatLink's actions (harness/beat_link.py). Each effect is
    injected, so this stays testable without o2lite."""
    for action in actions:
        if isinstance(action, SendHello):
            send_hello()
        elif isinstance(action, SendBeat):
            send_beat(action.seq, action.rtt_ms)
        elif isinstance(action, DropTransport):
            drop_transport()
        elif isinstance(action, DropRole):
            drop_role()
        elif isinstance(action, StateChanged):
            say(f"{markers.DEVICE_LINK_STATE} {action.state}")
```

In `main()`'s argparse, after `--heartbeat-interval`:

```python
    parser.add_argument("--no-beat", action="store_true",
                        help="behave as a legacy client: no /game/beat, "
                             "only the hello every --heartbeat-interval "
                             "seconds (spec 2026-10-08)")
```

After `send_hello` is defined, build the link and its effects:

```python
    beat = None if args.no_beat else BeatLink(
        legacy_hello=args.heartbeat_interval)

    def _send_beat(seq: int, rtt_ms: int) -> None:
        try:
            o2lite.send("/game/beat", 0, "sii", args.dev, seq, rtt_ms)
        except (AssertionError, OSError):
            pass   # hub away; BeatLink's lost timer handles it

    def do_beat(actions) -> None:
        run_beat_actions(
            actions, send_beat=_send_beat, send_hello=send_hello,
            drop_transport=o2lite.tcp_close,
            drop_role=client.reset_for_lobby,
            say=lambda line: print(line, flush=True))
```

In `on_down`, after `values` are pulled and before `client.handle(...)`:

```python
            if beat is not None:
                beat.on_control_message(time.monotonic())
                if address.endswith("/beat"):
                    do_beat(beat.on_beat_reply(time.monotonic(),
                                               values[0], values[1]))
                    return
```

Add `"beat"` to the `for kind in (...)` tuple that registers `/<dev>/<kind>` handlers.

Replace the single `send_hello()` after the service check with:

```python
        if beat is None:
            send_hello()
        else:
            do_beat(beat.link_up(time.monotonic()))
        was_linked = True
```

In the tick loop, directly after `bridge_id, problem = reconnect_recheck(...)` and its `problem` check (before `now = o2lite.time_get()`):

```python
                if beat is not None:
                    linked = isinstance(bridge_id, int) and bridge_id >= 0
                    if linked and not was_linked:
                        do_beat(beat.link_up(time.monotonic()))
                    elif was_linked and not linked:
                        do_beat(beat.link_down(time.monotonic()))
                    was_linked = linked
                    do_beat(beat.tick(time.monotonic()))
```

Guard the legacy heartbeat so it runs only without beats:

```python
                if beat is None and now >= next_heartbeat:
                    send_hello()
                    next_heartbeat = next_heartbeat_time(now, args.heartbeat_interval)
```

`reconnect_recheck` keeps the previous bridge id while an ownership re-check is retrying, so `was_linked` only flips when the hub is really back or really gone.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_markers.py tests/test_o2_shroom.py tests/test_o2_shroom_input.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/markers.py harness/o2_shroom.py tests/test_markers.py tests/test_o2_shroom.py
git commit -m "feat(harness): Testshroom beats via BeatLink; --no-beat for legacy"
```

---

### Task 7: Contract kit: beats mode, four scenarios, export v4

**Files:**
- Modify: `contract_kit/recorder.py`: imports; `__init__` (line 179); `_wrapped_send` (line 282); `advance_to` (line 321); `_run_due_device_sends` (line 346); `link_up` (line 361); `link_down` (line 376); `finish` (line 749); new methods
- Modify: `contract_kit/scenarios.py` (new constants, four scenario functions, `ALL_SCENARIOS`)
- Modify: `tools/export_contract.py`: `CONTRACT_VERSION`, `STEP_SCHEMA` (`scenario_fields.device`, new kind `expect_link_state`), `LIFECYCLE_NOTES`, the `lifecycle` block (line 563), `REPLAY_NOTES`
- Regenerate: every file in `contract_kit/recordings/`, plus four new ones
- Test: `tests/test_contract_scenarios.py`, `tests/test_contract_recorder.py`, `tests/test_export_contract.py`

**Interfaces:**
- Consumes: `BeatLink` (Task 5); `DeviceLinkAgent(epoch=)` (Task 3).
- Produces:
  - `Recorder(..., beats: bool = False)`
  - `.control_freeze(t_ms)`, `.control_thaw(t_ms)`: operator input, records no step
  - `.expect_beat_out(t_ms, seq)`
  - `.expect_link_state(t_ms, state)`
  - `.control_beat_now(seq, epoch)`: a hand-authored reply, recorded and fed to the scripted device
  - Constant `RECORDED_EPOCH = "e0e0e0"`.
  - Scenario dict `device` gains `"beats": bool`.
  - New step kind `{"t": ..., "expect_link_state": {"state": "linked" | "looking" | "solo"}}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contract_scenarios.py`:

```python
def test_every_scenario_declares_beats():
    for path in RECORDINGS.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["device"]["beats"] in (True, False), path.name


def test_beat_reply_echo_answers_every_seq_with_one_epoch():
    data = _load("beat_reply_echo")
    assert data["device"]["beats"] is True
    replies = _sends(data, "/$DEV/beat")
    assert [s["control_sends"]["args"] for s in replies][:3] == [
        [0, "e0e0e0"], [1, "e0e0e0"], [2, "e0e0e0"]]
    outs = [s["expect_out"] for s in data["steps"] if "expect_out" in s]
    assert [o["args"][1] for o in outs if o["address"] == "/game/beat"] == [0, 1, 2]


def test_beat_link_lost_looking_then_solo():
    data = _load("beat_link_lost_looking")
    states = [(s["t"], s["expect_link_state"]["state"])
              for s in data["steps"] if "expect_link_state" in s]
    assert [st for _t, st in states] == ["linked", "looking", "solo"]


def test_beat_relink_within_grace_repaints_and_sends_no_role():
    data = _load("beat_relink_within_grace")
    relink_t = next(s["t"] for s in data["steps"]
                    if s.get("link") == "up" and s["t"] > 0)
    after = [s for s in data["steps"] if s["t"] >= relink_t]
    assert any("control_sends" in s
               and s["control_sends"]["address"] == "/$DEV/leds" for s in after)
    assert not any("control_sends" in s
                   and s["control_sends"]["address"] in ("/$DEV/role",
                                                         "/$DEV/room")
                   for s in after)
    assert any("expect_play" in s for s in after)


def test_beat_epoch_change_rehellos():
    data = _load("beat_epoch_change_rehellos")
    new = [s for s in _sends(data, "/$DEV/beat")
           if s["control_sends"]["args"][1] == "f1f1f1"]
    assert new, "no reply ever carried the new epoch"
    t = new[0]["t"]
    assert any("expect_out" in s and s["expect_out"]["address"] == "/game/hello"
               and s["t"] == t for s in data["steps"])
```

Append to `tests/test_export_contract.py`:

```python
def test_export_v4_publishes_the_beat_lifecycle():
    data = export_contract(commit="abc123")
    assert data["contract_version"] == 4
    life = data["lifecycle"]
    assert life["beat_interval_s"] == 1.0
    assert life["beat_jitter_s"] == 0.1
    assert life["link_lost_s"] == 3.0
    assert life["grace_s"] == 15.0
    for key in ("beat_interval_s", "beat_jitter_s", "link_lost_s", "grace_s"):
        assert key in data["lifecycle_notes"]
    assert "expect_link_state" in data["step_schema"]["kinds"]
    joined = " ".join(data["replay_notes"]).lower()
    assert "/game/beat" in joined and "beats" in joined
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_contract_scenarios.py tests/test_export_contract.py -k "beat or beats" -v`
Expected: FAIL (`KeyError: 'beats'`, missing recordings).

- [ ] **Step 3: Recorder beats mode**

In `contract_kit/recorder.py` add these imports:

```python
import math

from harness.beat_link import (BeatLink, DropRole, DropTransport, SendBeat,
                               SendHello, StateChanged)
```

and this constant beside the others:

```python
# The epoch every recording's Control replies with. DeviceLinkAgent mints
# a random one in production; a fixed one keeps recordings deterministic.
# A device compares epochs only for equality, so the value is opaque.
RECORDED_EPOCH = "e0e0e0"
```

`__init__`: add the keyword `beats: bool = False`. Store it:

```python
        self.beats = beats
        # The scripted device's own beat state machine (spec 2026-10-08
        # section 6.1), jitter 0 so every beat lands on a whole second.
        self._beat_link = BeatLink(jitter=0.0) if beats else None
        self._beat_actions: list = []
        self._link_states: list[tuple[int, str]] = []
        self._control_frozen = False
```

Pass `epoch=RECORDED_EPOCH` in the `DeviceLinkAgent(...)` call.

`_wrapped_send`: at the end, feed the scripted device (only while its link is up, per `link_down_delivery`):

```python
        if self._beat_link is not None and self._linked_up:
            now_s = self._now_ms / 1000.0
            self._beat_link.on_control_message(now_s)
            if addr == f"/{self.dev}/beat":
                self._beat_actions += self._beat_link.on_beat_reply(
                    now_s, values[0], values[1])
```

`advance_to`: inside the loop, add the beat link's due time to the step clamp, and skip `agent.poll()` while frozen:

```python
            dues = [self._next_hello_ms, self._accept_due_ms]
            if self._beat_link is not None:
                nd = self._beat_link.next_due()
                if nd is not None:
                    dues.append(math.ceil(round(nd * 1000, 6)))
            for due in dues:
                if (due is not None
                        and (self._linked_up or self._beat_link is not None)
                        and due < self._now_ms + step_ms):
                    step_ms = max(1, min(step_ms, due - self._now_ms))
            self._set_now(self._now_ms + step_ms)
            if not self._control_frozen:
                self._agent.poll()
            self._run_due_device_sends()
```

Replace the existing `for due in (...)` loop with this block; keep the legacy hello logic intact when `beats` is false.

`_run_due_device_sends`: at the top, before the existing `if not self._linked_up: return`:

```python
        if self._beat_link is not None:
            self._beat_actions += self._beat_link.tick(self._now_ms / 1000.0)
            self._apply_beat_actions()
```

New helpers:

```python
    def _apply_beat_actions(self) -> None:
        actions, self._beat_actions = self._beat_actions, []
        for action in actions:
            if isinstance(action, SendHello):
                self._send_hello(self._now_ms)
            elif isinstance(action, SendBeat):
                self._send_beat(self._now_ms, action.seq, action.rtt_ms)
            elif isinstance(action, DropTransport):
                self._linked_up = False      # the device closed its link
            elif isinstance(action, DropRole):
                pass                         # the rig models no role state
            elif isinstance(action, StateChanged):
                self._link_states.append((self._now_ms, action.state))
        if self._beat_actions:               # a send fed back a reply
            self._apply_beat_actions()

    def _send_beat(self, t_ms: int, seq: int, rtt_ms: int) -> None:
        self._scripted.append((t_ms, "/game/beat", seq))
        self._fake.deliver("/game/beat", "sii", (self.dev, seq, rtt_ms),
                           timestamp=0.0)
        if not self._control_frozen:
            self._agent.poll()
```

`link_up`: in beats mode, the BeatLink sends the hello, and the 5 s legacy schedule is unused:

```python
        if self._beat_link is not None:
            self._next_hello_ms = None
            self._beat_actions += self._beat_link.link_up(t_ms / 1000.0)
            self._apply_beat_actions()
            self._run_due_device_sends()
            return
```

Insert it after `self._accept_due_ms = None`, in place of the legacy `self._next_hello_ms = ...; self._send_hello(t_ms)` lines, which stay as the `else` path.

`link_down`: in beats mode also call `self._beat_actions += self._beat_link.link_down(t_ms / 1000.0)` then `self._apply_beat_actions()`.

New public methods:

```python
    def control_freeze(self, t_ms: int) -> None:
        """Control stops answering at t_ms (a frozen or dead Terrarium);
        the device's link stays up. Operator input: records no step, the
        device only ever sees the silence."""
        self.advance_to(t_ms)
        self._control_frozen = True

    def control_thaw(self, t_ms: int) -> None:
        self.advance_to(t_ms)
        self._control_frozen = False
        self._agent.poll()

    def expect_beat_out(self, t_ms: int, seq: int) -> None:
        """The device must send /game/beat with this seq at t_ms. rtt_ms is
        "*": a device's own measurement is not contract."""
        if not any(t == t_ms and addr == "/game/beat" and d == seq
                   for (t, addr, d) in self._scripted):
            raise AssertionError(self._no_send_scripted(t_ms, "/game/beat"))
        self.steps.append({"t": t_ms, "expect_out": {
            "address": "/game/beat", "typespec": "sii",
            "args": ["$DEV", seq, "*"], "stamp_t": None,
            "within_ms": DEFAULT_WITHIN_MS}})

    def expect_link_state(self, t_ms: int, state: str) -> None:
        """HAND-AUTHORED like expect_frame_held: the device's own link
        state at t_ms (spec 2026-10-08 section 6.1). Checked against this
        rig's reference BeatLink, so it can never be a guessed time."""
        self.advance_to(t_ms)
        reached = [s for (t, s) in self._link_states if t <= t_ms]
        if not reached or reached[-1] != state:
            raise AssertionError(
                f"the reference device is {reached[-1] if reached else None!r} "
                f"at t={t_ms}ms, not {state!r}")
        self.steps.append({"t": t_ms, "expect_link_state": {"state": state}})

    def control_beat_now(self, seq: int, epoch: str) -> None:
        """A hand-authored /$DEV/beat reply (the epoch-change scenario):
        recorded like control_send_now AND delivered to the scripted
        device, which reacts to it. The rig's Control takes the new epoch
        too, as a restarted Control would, so its later replies agree and
        the device does not see the epoch flip back."""
        self._agent.epoch = epoch
        self.control_send_now("/$DEV/beat", "is", [seq, epoch])
        self._beat_actions += self._beat_link.on_beat_reply(
            self._now_ms / 1000.0, seq, epoch)
        self._apply_beat_actions()
```

`finish`: change the `"device"` entry to

```python
            "device": {"handshake": (dict(self.handshake)
                                     if self.handshake is not None
                                     else None),
                       "beats": self.beats},
```

- [ ] **Step 4: The four scenarios**

In `contract_kit/scenarios.py`, with the other timing constants:

```python
# beat_link_lost_looking: Control freezes once the role look has settled.
FREEZE_T = ROLE_SETTLED_T + 500
# beat_epoch_change_rehellos: the hand-authored reply with a new epoch.
EPOCH_CHANGE_T = 4000
NEW_EPOCH = "f1f1f1"
```

Functions, placed before `ALL_SCENARIOS`:

```python
def beat_reply_echo() -> dict:
    """Spec 2026-10-08 section 4: a beat-capable device hellos and beats
    seq 0 at link-up, then beats every second; Control answers each beat
    at once with the same seq and one epoch. No 5 s hello."""
    rec = Recorder(name="beat_reply_echo",
                   summary="Each /game/beat is answered with its seq and "
                           "the epoch; the 5 s hello stops",
                   handshake=None, beats=True)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.expect_beat_out(0, 0)
    rec.advance_to(1000)
    rec.expect_beat_out(1000, 1)
    rec.advance_to(2000)
    rec.expect_beat_out(2000, 2)
    rec.expect_link_state(2000, "linked")
    rec.expect_quiet(1, ["/game/hello"], 5500)
    rec.advance_to(5600)
    return rec.finish()


def beat_link_lost_looking() -> dict:
    """Spec 2026-10-08 section 6.1: with a role held while RUNNING, Control
    goes silent. 3 s after its last message the device shows Looking,
    closes its link and stops beating; at 15 s it shows Solo."""
    rec = Recorder(name="beat_link_lost_looking",
                   summary="Control goes silent: Looking after 3 s, beats "
                           "stop, Solo at 15 s",
                   handshake=ACCEPT_POLICY, beats=True)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)
    rec.control_freeze(FREEZE_T)
    rec.expect_link_state(FREEZE_T + 1500, "linked")
    rec.expect_link_state(FREEZE_T + 3500, "looking")
    rec.expect_quiet(FREEZE_T + 3500, ["/game/beat", "/game/hello"], 10000)
    rec.expect_link_state(FREEZE_T + 15500, "solo")
    return rec.finish()


def beat_relink_within_grace() -> dict:
    """Spec 2026-10-08 section 7: the link drops while RUNNING and is back
    at BLIP_BACK_T. The device hellos, restarts seq at 0, and is linked at
    the first reply; Control repaints its current frame and re-sends no
    /role or /room (rule 9: the device kept them); a tap still plays."""
    rec = Recorder(name="beat_relink_within_grace",
                   summary="A relink inside 15 s: seq restarts, Control "
                           "repaints the frame, no role is re-sent",
                   handshake=ACCEPT_POLICY, beats=True)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(ACCEPT_AFTER_MS)
    rec.expect_handshake_out(ACCEPT_AFTER_MS)
    rec.start(START_T)
    rec.advance_to(ROLE_SETTLED_T)
    rec.expect_frame(ROLE_SETTLED_T)
    rec.link_down(ROLE_SETTLED_T)
    rec.expect_link_state(ROLE_SETTLED_T, "looking")
    rec.link_up(BLIP_BACK_T)
    rec.expect_hello(BLIP_BACK_T)
    rec.expect_beat_out(BLIP_BACK_T, 0)
    rec.expect_link_state(BLIP_BACK_T + 50, "linked")
    rec.advance_to(BLIP_BACK_T + 100)
    rec.expect_frame(BLIP_BACK_T + 100)
    rec.tap(BLIP_TAP_T, duration_ms=80.0)
    rec.expect_play(BLIP_TAP_T, "tick")
    rec.advance_to(BLIP_TAP_T + 500)
    return rec.finish()


def beat_epoch_change_rehellos() -> dict:
    """Spec 2026-10-08 section 6.1: a reply carrying a different epoch
    means Control restarted; the device drops its role and hellos at
    once. The new-epoch reply is hand-authored, like the pair in
    timed_frames_hold_last."""
    rec = Recorder(name="beat_epoch_change_rehellos",
                   summary="A reply with a new epoch makes the device "
                           "drop its role and hello again",
                   handshake=None, beats=True)
    rec.link_up(0)
    rec.expect_hello(0)
    rec.advance_to(EPOCH_CHANGE_T)
    rec.control_beat_now(seq=4, epoch=NEW_EPOCH)
    rec.expect_hello(EPOCH_CHANGE_T)
    rec.advance_to(EPOCH_CHANGE_T + 1500)
    return rec.finish()
```

Append the four to `ALL_SCENARIOS`, after `link_blip_keeps_role`.

- [ ] **Step 5: Export v4**

In `tools/export_contract.py`:
- `CONTRACT_VERSION = 4`, and add one sentence to the comment above it: "The beat heartbeat (spec 2026-10-08) adds /game/beat and /<dev>/beat, four beat scenarios, device.beats and expect_link_state, so it bumps to 4."
- Extend the existing import at line 45 to `from devicelink.contract import (BEAT_INTERVAL_S, BEAT_JITTER_S, GRACE_S, HELLO_INTERVAL_S, LINK_LOST_S, VERB_TABLE, row_for)`.
- In the `"lifecycle"` dict: `"beat_interval_s": BEAT_INTERVAL_S, "beat_jitter_s": BEAT_JITTER_S, "link_lost_s": LINK_LOST_S, "grace_s": GRACE_S,`.
- `LIFECYCLE_NOTES` gains:

```python
    "beat_interval_s": (
        "Seconds between a beat-capable device's /game/beat sends while "
        "its link is up."
    ),
    "beat_jitter_s": (
        "Seconds of random spread, plus or minus, on each beat gap so "
        "devices do not beat in step. Recordings use none."
    ),
    "link_lost_s": (
        "Seconds with no message from Control after which an armed "
        "beat-capable device shows Looking and closes its link."
    ),
    "grace_s": (
        "Seconds after Control's last message at which a lost device "
        "shows Solo; Control drops it at the same point."
    ),
```

- `STEP_SCHEMA["scenario_fields"]["device"]`: change to describe `{"handshake": ..., "beats": bool}` by appending: `" beats is true when the device under test runs the beat heartbeat in this scenario: it hellos and beats at link-up, beats every lifecycle.beat_interval_s, and follows expect_link_state; false scenarios record no /$DEV/beat reply, so a beat-capable device never arms in them."`
- `STEP_SCHEMA["kinds"]["expect_link_state"]`:

```python
        "expect_link_state": {
            "role": (
                "expectation, hand-authored: the device's own link state "
                "at t (spec 2026-10-08 section 6.1). Only in scenarios "
                "with device.beats true."
            ),
            "fields": {
                "state": (
                    "str; \"linked\" (a reply arrived on this link), "
                    "\"looking\" (lost: the white Looking pulse) or "
                    "\"solo\" (lost past lifecycle.grace_s: the aurora)."
                ),
            },
        },
```

- `REPLAY_NOTES`: append two sentences and extend the transport sentence.

```python
    "In a scenario with device.beats false a beat-capable device still "
    "sends /game/beat; a runner ignores those outputs, and since the "
    "recording holds no /$DEV/beat reply the device never arms, keeps "
    "its lifecycle.hello_interval_s hello and never shows Looking.",
    "A beat-capable device closes its own link when it shows Looking; "
    "a runner treats that as the link going down until the scenario's "
    "next link step brings it up.",
```

  In the last note, change "leds and play over UDP" to "leds, play and beat over UDP".

Then fix the docstring references: `test_step_schema_matches_the_recordings_exactly` checks both directions, so `expect_link_state` must appear in the recordings (it does, from Step 4) and every new `device.beats` field must be described (done above).

- [ ] **Step 6: Re-record everything**

Run: `.venv/bin/python -m tools.record_scenarios`
Expected: all 22 recordings written; the 18 existing files differ only by `"beats": false` inside `device`. Check with `git diff --stat contract_kit/recordings/` and spot-check one legacy file's diff.

- [ ] **Step 7: Run the kit tests**

Run: `.venv/bin/python -m pytest tests/test_contract_scenarios.py tests/test_contract_recorder.py tests/test_export_contract.py tests/test_devicelink_contract.py -v`
Expected: all PASS. If `test_recording_matches_committed_json` fails for a beat scenario, re-run Step 6: the recording must reproduce byte for byte, so any non-determinism (a set iteration, a wall clock) is a bug to fix, not to paper over.

- [ ] **Step 8: Commit**

```bash
git add contract_kit/ tools/export_contract.py tests/test_contract_scenarios.py tests/test_contract_recorder.py tests/test_export_contract.py
git commit -m "feat(contract-kit): beat scenarios, device.beats, export v4"
```

---

### Task 8: Docs

**Files:**
- Modify: `docs/device-contract-guide.md` (verb table near line 193; rule 1; rule 9 at line 225; section 5.3 firmware checklist at line 236)
- Modify: `docs/light-lexicon.md` (lines 87-92, and G4 at lines 124-129)
- Modify: `docs/MM_TERRARIUM.md` (*Device pool and stale reaping*, around line 953; *The Testshroom*, around line 1880; *Not yet built*, around line 2394)
- Modify: `docs/superpowers/specs/2026-08-25-device-liveness-detection-design.md` (banner at the top)
- Modify: `docs/diagrams/player-flow.seq:10`, then regenerate
- Modify: `docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md` (Status line)

- [ ] **Step 1: Contract guide**
  - Add both `beat` rows to the vocabulary table, matching the `VERB_TABLE` notes from Task 1.
  - **Rule 1:** add "A beat-capable device sends /game/beat every 1 s instead of repeating hello every 5 s, once its first reply has armed it; until then it keeps the 5 s hello."
  - **Rule 9:** keep the existing text, which applies to unarmed devices. Append: "An armed beat-capable device holds its last frame for up to 3 s (`lifecycle.link_lost_s`), then shows Looking, then Solo at 15 s (`lifecycle.grace_s`); on relink inside the window Control repaints its current frame and re-sends nothing else."
  - **Scenario list:** add the four beat scenarios.
  - **Section 5.3 firmware checklist:**
    - beats and arming
    - force the transport down on loss
    - Looking and Solo
    - keep role state through a link loss
    - `WIFI_PS_NONE`
    - the mDNS discovery prerequisite (spec section 9)

- [ ] **Step 2: Light lexicon**

At lines 87-92, replace "Whether a long link loss should drop back to Looking is open (gap G4)." with "An armed beat-capable device that hears nothing from Control for 3 s drops back to Looking, and to Solo at 15 s (spec 2026-10-08 section 6.2)." In section 6, rewrite G4 as: "**G4. Decided 2026-10-08; firmware open.** Looking after 3 s of silence, Solo at 15 s, for beat-capable devices (spec `docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md`). Rev 1 firmware still has to render both (mm-devshroom)." Update the "G4 is still open" sentence above the list to match.

- [ ] **Step 3: Deep-dive**

In `docs/MM_TERRARIUM.md`, under *Device pool and stale reaping*, replace the heartbeat bullet with:

```markdown
- The heartbeat has two forms (spec
  `docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md`).
  A legacy client resends its first `/game/hello` every 5 s
  (`harness/o2_shroom.py --heartbeat-interval`; `--no-beat` keeps a
  Testshroom legacy) and gets nothing back. A beat-capable client sends
  `/game/beat dev seq [rtt_ms]` (UDP) every 1 s, and `DeviceLinkAgent`
  answers each with `/<dev>/beat seq epoch` (`epoch` minted once per agent,
  so a device sees a Control restart). `devicelink/link_monitor.py`
  tracks live or missing (3 s) and loss and RTT for the Console's signal
  bars; missing is display only, the 15 s reap is unchanged. A hello
  from a pooled beat-capable dev is a relink: the agent pops its
  `_last_frames` entry so the current frame is repainted, and re-sends
  nothing else (contract rule 9). The device side is
  `harness/beat_link.py`, the reference the firmware and app port.
```

Under *The Testshroom*, add one sentence: the Testshroom beats by default via `BeatLink` and prints `LINK STATE: <state>`. Under *Not yet built*, add: mm-tuneshroom and mm-devshroom beat adoption pending (spec section 9).

- [ ] **Step 4: Liveness spec banner**

At the top of `2026-08-25-device-liveness-detection-design.md`, under the title, add: "**Partly superseded (2026-10-08):** section 2's rejection of a Control-side reply is reversed by `2026-10-08-bidirectional-heartbeat-design.md`; sections 4 and 5 stand."

- [ ] **Step 5: Player-flow diagram**

Change `docs/diagrams/player-flow.seq` line 10 to `note: heartbeat: /game/beat every 1 s, each answered /ie1/beat seq epoch (legacy: hello every 5 s, no reply)`. Then run: `.venv/bin/python -m tools.render_diagrams` (it writes by default; `--check` only reports) and confirm `docs/MM_TERRARIUM.md`'s player-flow block changed.

- [ ] **Step 6: Spec status**

In the heartbeat spec header, set `**Status:** implemented in mm-terrarium (branch claude/terrarium-heartbeat-devshroom-d5dec8); mm-tuneshroom and mm-devshroom pending.`

- [ ] **Step 7: Verify and commit**

Run: `.venv/bin/python -m pytest tests -q` and `node --test tests/js/*.test.js`
Expected: all PASS (the diagram tests check that the generated blocks match their sources).

```bash
git add docs/
git commit -m "docs: beat heartbeat in the contract guide, lexicon G4, deep-dive"
```

---

### Task 9: Live stack verification

This needs a host with a built Arco server. If the executor's host cannot run Arco, mark this task BLOCKED and hand the steps to Chris; do not skip it silently.

- [ ] **Step 1: Normal run**

Run (from the worktree root): `./smoke-test.sh --bit MetronomeBit --devices 2 --seconds 40 --ci`
Expected: exit 0; each device log under `runs/<timestamp>/` contains `LINK STATE: linking` then `LINK STATE: linked`. Check the Bit name first with `.venv/bin/python -m harness.run_stack --list-bits` and use the listed name.

- [ ] **Step 2: Freeze Control, recover inside the grace window**

Start a held stack: `set -m; ./smoke-test.sh --bit <name> --devices 1 --serve > beat-run.log 2>&1 &`. Wait for `ROLE GRANTED:` in `beat-run.log`. Find Control: `pgrep -f harness.terrarium_boot`. Then `kill -STOP <pid>`, wait 6 s, `kill -CONT <pid>`.
Expected in the device log: `LINK STATE: looking` 2 to 3.5 s after the freeze, then `LINK STATE: linked` after the thaw, and no second `ROLE GRANTED:` (same role kept).

- [ ] **Step 3: Freeze past the grace window**

Repeat with a 20 s freeze.
Expected: `LINK STATE: solo` around 15 s; after the thaw the device is reaped and comes back with a fresh jam role.

- [ ] **Step 4: Legacy client against the new Control**

Run: `./smoke-test.sh --bit <name> --devices 1 --seconds 30 --ci` with the device spawned with `--no-beat`. Check how `run_stack` forwards extra Testshroom flags (`--help`); if it has no pass-through, run one Testshroom by hand with `.venv/bin/python -m harness.o2_shroom --dev ie9 --no-beat` against a `./terrarium.sh --room TEST` stack.
Expected: the device joins and plays, and no `LINK STATE:` lines appear.

- [ ] **Step 5: Load**

Run: `./smoke-test.sh --bit <name> --devices 30 --seconds 45 --ci`
Expected: exit 0; Control's log shows no tick overrun warnings beyond what the same run without beats shows (run once with legacy devices to compare if any appear).

- [ ] **Step 6: Record results**

Append a short "Live check 2026-10-xx" paragraph to the spec's section 10 with the measured Looking delay and the load result, then commit:

```bash
git add docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md
git commit -m "docs(spec): record the live beat checks"
```

---

### Task 10: Draft the mm-devshroom issue for Victor

**Files:**
- Create: `docs/superpowers/plans/2026-10-08-devshroom-beat-issue.md` (the issue body; filing it is outward-facing and needs Chris's go-ahead)

- [ ] **Step 1: Write the issue body.** Title: "Beat heartbeat: device side (contract v4)". The body should:
  - Link the spec (`mm-terrarium` `docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md`).
  - Name `harness/beat_link.py` as the state machine to port, and paste its state table.
  - **Prerequisite:** quote the spec's section 9 prerequisite on the mDNS `continue` hang, with the fix `for (r = results; r; r = r->next)` and a `lib/o2/VENDORED.md` entry.
  - **Checklist:**
    - send `/game/beat dev seq rtt_ms` over UDP every 1 s ±0.1 s
    - arm on the first reply
    - treat any down message as proof of life
    - at 3 s of silence: show Looking and drop the transport (declare o2lite's `disconnect()`, `lib/o2/o2lite.c:772`, or add `o2l_disconnect()`)
    - at 15 s: show Solo
    - on a new epoch: drop the role and hello
    - keep the role through a link loss (remove the `g_device_registered = false` reset at `src/o2_test/main.cpp:85-88`)
    - call `esp_wifi_set_ps(WIFI_PS_NONE)`
    - replay contract export v4
  - **Bench items:** cover the spec section 10 bench row, including the second O2 host and the stale-clock check.

- [ ] **Step 2: Commit the draft and stop.**

```bash
git add docs/superpowers/plans/2026-10-08-devshroom-beat-issue.md
git commit -m "docs: draft mm-devshroom beat heartbeat issue for Victor"
```

Then report to Chris that the draft is ready and ask before running `gh issue create --repo Musical-Mycology/mm-devshroom --title "..." --body-file docs/superpowers/plans/2026-10-08-devshroom-beat-issue.md`.

---

## After this plan

- mm-tuneshroom plan: port `BeatLink` into `lib/link/device_link.dart` and `lib/host/device_session.dart`, render Looking and Solo in the simulator, beat over o2ws in the web build, adopt export v4 in `test/contract_replay_test.dart`. Written once Task 7's export exists.
- Closeout: `superpowers:finishing-a-development-branch`, then `mm-deepdive-sync` (Task 8 already edits the deep-dive; the sync confirms nothing was missed).
