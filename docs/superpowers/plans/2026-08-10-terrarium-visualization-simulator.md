# Terrarium Visualization Simulator (TEST Room) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `RoomBridge` (landed, currently inert) a real backend for `RoomType.TEST`: a devicelink-connected simulator subprocess rendering a browser-canvas LED view, plus the Control-side wiring (a real `LightSession` and a real `AudioBridge`/Arco voice) that makes a Bit's Room-scoped cues actually visible and audible.

**Architecture:** The simulator (`harness/room_simulator.py`) is an ordinary devicelink client — indistinguishable from a real Tuneshroom — reusing `harness/shroom_client.py`'s existing protocol handling and luxaeterna's `WebSimBackend` for browser rendering. Audio never touches the simulator subprocess at all: Control drives Arco directly for the Room's declared instrument, the same way it already does for player roles, since sound doesn't need a wire hop to become audible on the venue box. `DeviceLinkAgent` gains the Room-awareness Spec 1 deliberately deferred — building real sinks and routing `gs.room.bound_dev`'s cues to `RoomBridge` instead of the per-role path.

**Tech Stack:** Python (stdlib + existing deps: `websockets`, `luxaeterna`, lazy `pyarco`). No new third-party dependencies.

## Global Constraints

- `harness/room_simulator.py` renders via `WebSimBackend`/`shroom_capability()` — the same browser-canvas view `led_smoke.py` already uses, since TEST room's hardware is conceptually a Tuneshroom.
- The simulator subprocess is spawned with a Terrarium-assigned dev id (not self-reported) and never performs a Registration Node join — Room binding is already recorded before it connects.
- Audio is always on: `boot()` (Spec 1) guarantees a live Arco server before Room binding happens, so no `--audio`-style opt-in flag is needed.
- `devicelink/agent.py` stays fully offline-testable — its existing luxaeterna imports are unchanged in kind, and any new pyarco-touching object (`ArcoSynthPool`) is constructed by the caller and injected, never imported by `devicelink/agent.py` itself.
- Follow this repo's existing conventions throughout: the `build()`/`main()` driver-script split (`led_smoke.py`, `devicelink_smoke.py`), `Protocol` + fake test-doubles for backend-agnostic seams, lazy pyarco imports only where they already exist (`harness/arco_synth.py`).

---

## File Structure

**New files:**
- `control/simulator_process.py` — `SimulatorProcess` (spawn/SIGTERM-shutdown, peer to `ArcoProcess`, reuses its `FakePopen`).
- `harness/room_simulator.py` — the simulator subprocess: a devicelink client (`ShroomClient`, reused unmodified) rendering into `WebSimBackend` via a small `WebSimLeds` adapter.
- `harness/terrarium_boot.py` — the driver script: constructs `DeviceLinkServer`, wires a `simulator_factory` that spawns `harness/room_simulator.py`, calls `boot()`, constructs the Room-aware `DeviceLinkAgent`, runs the tick loop, tears everything down.
- `tests/test_simulator_process.py`, `tests/test_room_simulator.py`, `tests/test_terrarium_boot.py`.

**Modified files:**
- `control/rooms.py` — extract `room_role_name(room_type)` (the `f"room_{room_type.name.lower()}"` naming convention) as its own function, used by both `room_role()` (unchanged behavior) and the new devicelink wiring.
- `bits/test_bit.py` — `TestBit` gains a real Room declaration (light + audio instruments) via `room_role()`, merged into its existing `role_table`.
- `devicelink/agent.py` — `DeviceLinkAgent` gains Room-awareness: real light/audio sinks built at construction time, a `_render_room()` step, cue routing to `RoomBridge` for the Room's dev, and drone start/stop on Bit state changes.
- `tests/test_rooms.py`, `tests/test_test_bit.py`, `tests/test_devicelink_agent.py` — new test cases alongside existing ones.

---

### Task 1: `SimulatorProcess` — `control/simulator_process.py`

**Files:**
- Create: `control/simulator_process.py`
- Test: `tests/test_simulator_process.py`

**Interfaces:**
- Consumes: `control.arco_process.FakePopen` (existing, Spec 1) — reused directly rather than duplicated.
- Produces: `SimulatorProcess(command, *, popen=subprocess.Popen)` with `start()`, `shutdown()` — consumed by Task 6 (`harness/terrarium_boot.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_simulator_process.py
import signal

from control.arco_process import FakePopen
from control.simulator_process import SimulatorProcess


def test_start_launches_the_configured_command():
    popen = FakePopen()
    process = SimulatorProcess(["room-simulator", "--dev", "sim-room"], popen=popen)
    process.start()
    assert popen.commands == [["room-simulator", "--dev", "sim-room"]]


def test_shutdown_sends_sigterm_and_waits():
    popen = FakePopen()
    process = SimulatorProcess(["room-simulator"], popen=popen)
    process.start()

    process.shutdown()

    assert popen.signals == [signal.SIGTERM]
    assert popen.waited is True


def test_shutdown_before_start_is_a_noop():
    process = SimulatorProcess(["room-simulator"], popen=FakePopen())
    process.shutdown()   # must not raise


def test_shutdown_twice_only_signals_once():
    popen = FakePopen()
    process = SimulatorProcess(["room-simulator"], popen=popen)
    process.start()
    process.shutdown()
    process.shutdown()
    assert popen.signals == [signal.SIGTERM]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_simulator_process.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.simulator_process'`

- [ ] **Step 3: Write the implementation**

```python
# control/simulator_process.py
"""SimulatorProcess: spawns and owns a Room simulator subprocess
(harness/room_simulator.py) for the Terrarium load sequence. Peer to
control/arco_process.py's ArcoProcess, minus a readiness probe -- the
simulator's own devicelink connection is retried/owned by the caller's
sequencing (see docs/superpowers/specs/2026-08-10-terrarium-visualization-
simulator-design.md section 3), not something boot() blocks on.
"""

from __future__ import annotations

import signal
import subprocess


class SimulatorProcess:
    def __init__(self, command: list[str], *, popen=subprocess.Popen) -> None:
        self._command = command
        self._popen = popen
        self._process = None

    def start(self) -> None:
        self._process = self._popen(self._command)

    def shutdown(self) -> None:
        if self._process is None:
            return
        self._process.send_signal(signal.SIGTERM)
        self._process.wait()
        self._process = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_simulator_process.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add control/simulator_process.py tests/test_simulator_process.py
git commit -m "feat(terrarium): add SimulatorProcess (spawn, SIGTERM shutdown)"
```

---

### Task 2: Reference Room declaration — `bits/test_bit.py`, `control/rooms.py`

**Files:**
- Modify: `control/rooms.py` (extract `room_role_name()`)
- Modify: `bits/test_bit.py`
- Test: `tests/test_rooms.py`, `tests/test_test_bit.py`

**Interfaces:**
- Consumes: `control.roles.Role`/`RoleClass` (existing).
- Produces: `control.rooms.room_role_name(room_type: RoomType) -> str` — consumed by Task 4 (`devicelink/agent.py`) to look up the Room's `Role` off a loaded Bit's `role_table`. `TestBit.role_table` now includes a real `room_test` entry with non-empty `light_manifest`/`ugen_manifest` — consumed by Task 4/5's tests and by `harness/room_simulator.py`'s manual verification.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_rooms.py`:

```python
from control.rooms import room_role_name


def test_room_role_name_matches_room_role_helper():
    name, role, node = room_role(RoomType.DEMO)
    assert name == room_role_name(RoomType.DEMO)


def test_room_role_name_is_deterministic_per_type():
    assert room_role_name(RoomType.TEST) == "room_test"
    assert room_role_name(RoomType.DEMO) == "room_demo"
```

Add to `tests/test_test_bit.py` (check its existing conventions first):

```python
from control.roles import RoleClass
from control.rooms import room_role_name, RoomType


def test_test_bit_declares_a_room_test_role():
    bit = TestBit()
    name = room_role_name(RoomType.TEST)
    role = bit.role_table.roles[name]
    assert role.role_class == RoleClass.ROOM
    assert role.light_manifest["instruments"]
    assert role.ugen_manifest["instruments"]


def test_test_bit_room_node_is_registered():
    from control.rooms import ROOM_NODE_IDS
    bit = TestBit()
    node = ROOM_NODE_IDS[RoomType.TEST]
    assert room_role_name(RoomType.TEST) in bit.role_table.node_map[node]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_rooms.py tests/test_test_bit.py -v`
Expected: FAIL — `ImportError: cannot import name 'room_role_name'`, then (after fixing that) `KeyError: 'room_test'`

- [ ] **Step 3: Write the implementation**

In `control/rooms.py`, replace the body of `room_role()`:

```python
def room_role_name(room_type: RoomType) -> str:
    """The deterministic role name every Bit supporting room_type must use
    for its ROOM-class role, so any compatible Bit's declaration is found
    the same way -- see control/rooms.py:room_role and
    devicelink/agent.py's Room-wiring, which looks this up off a loaded
    Bit's role_table."""
    return f"room_{room_type.name.lower()}"


def room_role(room_type: RoomType, *, ugen_manifest: dict | None = None,
             light_manifest: dict | None = None) -> tuple[str, Role, str]:
    """Build a ROOM-class Role for room_type plus its canonical node id, so a
    Bit can merge them into its own RoleTable.roles / node_map. The role name
    is deterministic per RoomType so two Bits supporting the same RoomType
    declare identical role names -- see design spec section 3."""
    name = room_role_name(room_type)
    role = Role(
        name=name,
        role_class=RoleClass.ROOM,
        capacity=1,
        scored=False,
        ugen_manifest=ugen_manifest or {},
        light_manifest=light_manifest or {},
    )
    return name, role, ROOM_NODE_IDS[room_type]
```

In `bits/test_bit.py`, add the import:

```python
from control.rooms import RoomType, room_role
```

Change `role_table` to merge in the Room declaration:

```python
    @property
    def role_table(self) -> RoleTable:
        player = Role(
            ...  # unchanged
        )
        jammer = Role(...)  # unchanged
        room_name, room, room_node = room_role(
            RoomType.TEST,
            # A field-rate gesture, like player's aurora -- no note lane,
            # so it renders continuously under cc:74 without the note-
            # triggered strobe TestBit's own docstring already explains.
            # Deliberately no cc:11/level lane (unlike player): breath-
            # feeding the Room is a real, separable enhancement, not
            # needed to prove RoomBridge renders at all.
            light_manifest={
                "instruments": [
                    {"instrument": "aurora", "target": "primary",
                     "params": {"hue": 0.6, "level": 0.55},
                     "lanes": [{"source": "cc:74", "dest": "hue"}]},
                ],
            },
            ugen_manifest={
                "instruments": [
                    {"instrument": "flsyn", "program": 89,
                     "drone": {"key": 50, "velocity": 80},
                     "lanes": [{"source": "cc:74", "dest": "cc:74"}]},
                ],
            },
        )
        return RoleTable(
            roles={"player": player, "jammer": jammer, room_name: room},
            node_map={"TEST_PLAYER_NODE": ["player"],
                      "TEST_JAM_NODE": ["jammer"],
                      room_node: [room_name]},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_rooms.py tests/test_test_bit.py -v`
Expected: PASS (all tests, including pre-existing ones)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

`TestBit` is used pervasively across the suite (bits, harness demos, engine tests). Confirm this purely-additive change breaks nothing.

Run: `python3 -m pytest tests -v`
Expected: PASS (every test, old and new)

- [ ] **Step 6: Commit**

```bash
git add control/rooms.py bits/test_bit.py tests/test_rooms.py tests/test_test_bit.py
git commit -m "feat(terrarium): give TestBit a real Room declaration"
```

---

### Task 3: The simulator subprocess — `harness/room_simulator.py`

**Files:**
- Create: `harness/room_simulator.py`
- Test: `tests/test_room_simulator.py`

**Interfaces:**
- Consumes: `harness.shroom_client.ShroomClient` (existing, reused unmodified), `luxaeterna.backends.websim.WebSimBackend`/`luxaeterna.synth.capability.shroom_capability` (existing).
- Produces: `WebSimLeds` (a `leds`-shaped adapter matching `ShroomClient`'s `leds.show(bytes)`/`leds.clear()` expectations) — consumed only by this module's own `main()`; `build()` is the offline-testable seam other tests/Task 6 can reuse if needed, matching `led_smoke.py`/`devicelink_smoke.py`'s convention.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_room_simulator.py
from harness.room_simulator import WebSimLeds


class FakeBackend:
    def __init__(self):
        self.sent = []

    def send(self, frame, universe_id: int = 0) -> None:
        self.sent.append(bytes(frame))


def test_show_forwards_the_frame_to_the_backend():
    backend = FakeBackend()
    leds = WebSimLeds(backend)

    leds.show(bytes(range(36)))

    assert backend.sent == [bytes(range(36))]


def test_clear_sends_an_all_zero_frame():
    backend = FakeBackend()
    leds = WebSimLeds(backend)

    leds.clear()

    assert backend.sent == [bytes(36)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_room_simulator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.room_simulator'`

- [ ] **Step 3: Write the implementation**

```python
# harness/room_simulator.py
"""python -m harness.room_simulator -- the TEST-room simulator subprocess.

An ordinary devicelink client, indistinguishable from a real Tuneshroom:
reuses harness/shroom_client.py's ShroomClient unmodified for the wire
protocol, and renders into luxaeterna's WebSimBackend (a browser canvas)
instead of real GPIO LEDs. Sends only `/game/hello` -- never `/game/join` --
because control/boot.py's boot() (Spec 1) has already recorded this dev as
the bound Room before this process is spawned; there is no Registration
Node to tap. See
docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md
section 3-4.

Audio never touches this process: Control drives Arco directly for the
Room's declared instrument (see devicelink/agent.py), the same way it
already does for player roles. This process is a pure LED display.

Usage (normally spawned by harness/terrarium_boot.py, not run by hand):
    python3 -m harness.room_simulator --dev sim-room \
        --server ws://127.0.0.1:8771/ws --sim-host 127.0.0.1 --sim-port 8770
"""

from __future__ import annotations


class WebSimLeds:
    """Adapts ShroomClient's leds.show(bytes)/leds.clear() to
    WebSimBackend's send(frame). Frame byte counts already match: both
    trace to the same shroom_capability() (12 pixels x 3 bytes = 36),
    matching devicelink's own 36-int /<dev>/leds wire shape."""

    def __init__(self, backend) -> None:
        self._backend = backend

    def show(self, frame: bytes) -> None:
        self._backend.send(frame)

    def clear(self) -> None:
        self._backend.send(bytes(36))


def main() -> None:
    import argparse
    import asyncio
    import json

    import websockets

    from harness.shroom_client import ShroomClient
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import shroom_capability

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", required=True,
                        help="Dev id Terrarium assigned this Room instance.")
    parser.add_argument("--server", required=True,
                        help="Control's devicelink URL, e.g. ws://host:port/ws")
    parser.add_argument("--sim-host", default="127.0.0.1")
    parser.add_argument("--sim-port", type=int, default=0)
    args = parser.parse_args()

    backend = WebSimBackend(capability=shroom_capability(),
                            host=args.sim_host, port=args.sim_port)
    backend.open()
    print(f"Watch the Room at http://{args.sim_host}:{backend.port}/")

    client = ShroomClient(args.dev, node="", leds=WebSimLeds(backend))

    async def run() -> None:
        async with websockets.connect(args.server) as ws:
            await ws.send(json.dumps(client.hello()))
            async for raw in ws:
                client.handle(json.loads(raw))

    try:
        asyncio.run(run())
    finally:
        backend.close()


if __name__ == "__main__":
    main()
```

(`ShroomClient(dev, node="", ...)` — `node` is unused by this process since it
never calls `.join()`; passed as an empty string rather than `None` to match
the constructor's declared `str` type.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_room_simulator.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Manual verification (not part of the automated suite)**

`main()`'s real websocket loop is untested here, matching this repo's existing
precedent (`shroom_client.py`/`led_smoke.py`/`devicelink_smoke.py`'s `main()`
functions are likewise unit-test-free — the transport half is what o2lite
eventually replaces). Real end-to-end verification happens once Task 6's
driver script exists: run it, open the printed URL, confirm the canvas
renders and updates.

- [ ] **Step 6: Commit**

```bash
git add harness/room_simulator.py tests/test_room_simulator.py
git commit -m "feat(terrarium): add the Room simulator subprocess (devicelink client + WebSimBackend)"
```

---

### Task 4: `DeviceLinkAgent` light-side Room wiring — `devicelink/agent.py`

**Files:**
- Modify: `devicelink/agent.py`
- Test: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `control.room_bridge.RoomBridge`/`RoomLightSink` (Spec 1), `control.rooms.room_role_name` (Task 2), `control.role_config.compose_role_config` (existing), `luxaeterna.synth.manifest.LightManifest`/`luxaeterna.synth.session.build_session` (existing, already used by `harness/device_bridge.py`).
- Produces: `DeviceLinkAgent(game_server, server, capability=None, clock=time.monotonic, room_bridge=None, room_audio=None)` — the `room_audio` param is consumed by Task 5, not this task (pass `None` here; Task 5 wires a real value). `DeviceLinkAgent._render_room()`, `.on_state_change()` (audio-only, added in Task 5) — consumed by Task 6's tick loop indirectly (`poll()` already calls it internally).

**Note on the approved design spec:** section 5 of
`docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md`
describes the cue-routing check living in `GameServer`'s cue dispatch. This
task instead puts it in `DeviceLinkAgent._on_light_cue` — `on_light_cue` is
already a transport-owned sink (`control/engine.py`'s own comments: "Set by a
transport layer... boundary rule 3 -- the Bit decides the light consequence,
the transport only delivers it"), so routing *how* a cue gets delivered
belongs in the transport, not in the transport-agnostic `GameServer`. This
preserves the spec's approved behavior exactly (Room cues reach `RoomBridge`,
everything else reaches the normal per-device path) while touching zero lines
of `control/engine.py` — a smaller, safer diff for the same outcome.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_devicelink_agent.py` (check its existing fixtures/imports
first — it likely already has a `FakeServer`/similar transport double and a
`build_bit_and_join(...)`-style helper; match its conventions rather than
inventing new ones):

```python
from control.room_binding import RoomBindingRegistry
from control.room_bridge import RoomBridge
from control.rooms import Room, RoomType


def _room_ready_game_server():
    """A GameServer with TestBit loaded and its Room already bound to
    'sim-room' -- the state DeviceLinkAgent sees once harness/terrarium_boot.py
    (Task 6) has already called boot()."""
    binding = RoomBindingRegistry()
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = Room(room_type=RoomType.TEST)
    gs.load_bit("TestBit")
    gs.room.bound_dev = "sim-room"
    binding.bind(RoomType.TEST, "sim-room")
    return gs


def test_room_light_session_built_from_bit_declaration():
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()   # match the existing fixture's actual class name

    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)

    assert room_bridge.dev == "sim-room"
    assert agent._room_light is not None


def test_room_dev_cue_routes_to_room_bridge_not_normal_bridges():
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)
    universe = agent._room_light.universe
    agent._room_light.session.render_into(universe)
    baseline = bytes(universe.get_frame()[:36])

    gs.on_light_cue("sim-room", 0xB0, 74, 100)
    agent._room_light.session.render_into(universe)
    after = bytes(universe.get_frame()[:36])

    assert "sim-room" not in agent.bridges   # never treated as a player device
    assert after != baseline   # the fed cc:74 actually changed aurora's hue


def test_render_room_sends_leds_event_when_frame_changes():
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)
    client = object()
    agent._clients["sim-room"] = client   # simulate the hello handshake

    agent._render_room()

    sent = server.sent_to(client)   # match existing FakeServer's inspection API
    assert any(msg["address"] == "/sim-room/leds" for msg in sent)


def test_no_room_configured_leaves_room_wiring_inert():
    gs = GameServer({"TestBit": TestBit})   # no room_binding, no room
    gs.load_bit("TestBit")
    server = FakeServer()

    agent = DeviceLinkAgent(gs, server)   # room_bridge defaults to None

    assert agent._room_light is None
    agent._render_room()   # must not raise
```

(Adjust `FakeServer`'s exact name/inspection methods to match whatever
`tests/test_devicelink_agent.py` already uses for existing tests like
`test_failing_on_grant_sends_error_not_role` — do not invent a parallel
fixture.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_devicelink_agent.py -k room -v`
Expected: FAIL — `TypeError: DeviceLinkAgent.__init__() got an unexpected keyword argument 'room_bridge'`

- [ ] **Step 3: Write the implementation**

In `devicelink/agent.py`, add imports:

```python
from control.role_config import compose_role_config
from control.rooms import room_role_name
from luxaeterna.synth.capability import shroom_capability
from luxaeterna.synth.manifest import LightManifest
from luxaeterna.synth.session import build_session
```

Change `__init__`:

```python
    def __init__(self, game_server: GameServer, server,
                 capability=None, clock=time.monotonic,
                 room_bridge=None, room_audio=None):
        self.game_server = game_server
        self.server = server
        self._capability = capability
        self._clock = clock
        self.bridges: dict[str, DeviceBridge] = {}
        self._universes: dict[str, Universe] = {}
        self._last_frames: dict[str, bytes] = {}
        self._clients: dict[str, object] = {}
        self._closing: dict[str, int] = {}
        self._breath_origin = self._clock()
        self._last_breath: dict[str, int] = {}
        # Room wiring (see design spec section 5). None of this exists
        # unless a Room is both configured and already bound -- a
        # GameServer built the pre-Room way (no room_binding/room) leaves
        # every attribute below at its default and every Room-aware method
        # below a no-op.
        self._room_bridge = room_bridge
        self._room_audio = room_audio
        self._room_dev: str | None = None
        self._room_light = None
        self._setup_room()
        game_server.add_observer(self)
        game_server.on_release = self._on_release
        game_server.on_light_cue = self._on_light_cue
        game_server.on_play_cue = self._on_play_cue

    def _setup_room(self) -> None:
        """Build the Room's real LightSession (and, if room_audio was
        injected, wire its Arco voice) from the loaded Bit's own Room
        declaration -- the same declare-then-compose pattern every per-role
        device already uses, just without a JoinResult (there is no join
        for this path; see design spec section 4)."""
        gs = self.game_server
        room = gs.room
        if room is None or room.bound_dev is None or gs.bit is None:
            return
        role = gs.bit.role_table.roles.get(room_role_name(room.room_type))
        if role is None:
            return
        self._room_dev = room.bound_dev
        blob = compose_role_config(gs.bit_name, gs.bit.version, role)
        manifest = LightManifest.from_dict(blob["light_manifest"])
        cap = self._capability or shroom_capability()
        session = build_session(manifest, cap, clock=self._clock)
        self._room_light = _RoomLightSink(session, Universe())
        audio_sink = None
        if self._room_audio is not None:
            self._room_audio.on_grant(self._room_dev, role)
            audio_sink = _RoomAudioSink(self._room_audio, self._room_dev)
        if self._room_bridge is not None:
            self._room_bridge.bind(self._room_dev, light=self._room_light,
                                   audio=audio_sink)
```

Add the two small adapter classes near the top of the module, after the
existing imports:

```python
class _RoomLightSink:
    """Satisfies control.room_bridge.RoomLightSink. `universe`/`session` are
    extra, used only by DeviceLinkAgent._render_room() -- RoomBridge itself
    only ever calls feed_midi()/clear()."""

    def __init__(self, session, universe: Universe) -> None:
        self.session = session
        self.universe = universe

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        self.session.feed_midi(status, d1, d2)

    def clear(self) -> None:
        self.session.clear()


class _RoomAudioSink:
    """Satisfies control.room_bridge.RoomAudioSink by adapting
    AudioBridge.feed_midi(dev, status, d1, d2)'s dev-keyed signature down to
    the Protocol's dev-less one -- this sink is already scoped to one dev."""

    def __init__(self, audio_bridge, dev: str) -> None:
        self._audio = audio_bridge
        self._dev = dev

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        self._audio.feed_midi(self._dev, status, d1, d2)

    def shutdown(self) -> None:
        self._audio.shutdown()
```

Add `_render_room()`, called from `poll()`:

```python
    def poll(self) -> None:
        self.server.drain_new_clients()
        for client, msg in self.server.drain_inbound():
            try:
                self._handle(client, msg)
            except Exception:
                logger.exception("devicelink inbound handling failed; "
                                 "dropping frame")
        self._feed_breath()
        self._render_frames()
        self._render_room()

    def _render_room(self) -> None:
        if self._room_light is None or self._room_dev is None:
            return
        universe = self._room_light.universe
        try:
            self._room_light.session.render_into(universe)
        except Exception:
            logger.exception("Room render failed; skipping frame")
            return
        frame = bytes(universe.get_frame()[:36])
        if frame != self._last_frames.get(self._room_dev):
            self._last_frames[self._room_dev] = frame
            try:
                self._send(self._room_dev,
                          protocol.leds_event(self._room_dev, frame))
            except Exception:
                logger.exception("Room leds send failed")
```

Change `_on_light_cue` to route Room cues first:

```python
    def _on_light_cue(self, dev: str, status: int,
                      data1: int, data2: int) -> None:
        if dev == self._room_dev and self._room_bridge is not None:
            try:
                self._room_bridge.feed_midi(status, data1, data2)
            except Exception:
                logger.exception("Room feed_midi failed")
            return
        bridge = self.bridges.get(dev)
        if bridge is None or bridge.session is None:
            return
        try:
            bridge.session.feed_midi(status, data1, data2)
        except Exception:
            logger.exception("feed_midi for %s failed", dev)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_devicelink_agent.py -v`
Expected: PASS (all tests, including pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "feat(terrarium): wire DeviceLinkAgent's real Room light rendering and cue routing"
```

---

### Task 5: `DeviceLinkAgent` audio-side Room wiring — `devicelink/agent.py`

**Files:**
- Modify: `devicelink/agent.py`
- Test: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `control.audio.AudioBridge`/`FakePool`/`FakeVoice` (existing) — this task's tests use `FakePool`, matching `control/audio.py`'s own test-double precedent; the real `ArcoSynthPool` is only ever constructed by Task 6's driver script, never imported here.
- Produces: `DeviceLinkAgent.on_state_change(old, new)` (drone start/stop) — this is the first task where `room_audio` (already accepted as a constructor param since Task 4) is exercised with a real `AudioBridge`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_devicelink_agent.py`:

```python
from control.audio import AudioBridge, FakePool
from control.state import State


def test_room_audio_bridge_gets_on_grant_at_setup():
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio)

    assert len(pool.acquired) == 1   # TestBit's room_test role has one instrument


def test_room_dev_cue_reaches_audio_bridge_too():
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    room_bridge = RoomBridge()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=room_bridge,
                            room_audio=room_audio)

    gs.on_light_cue("sim-room", 0xB0, 74, 90)

    voice = pool.acquired[0]
    assert ("cc", 74, 90) in voice.sent


def test_on_state_change_running_starts_the_drone():
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio)

    agent.on_state_change(State.SETUP, State.RUNNING)

    voice = pool.acquired[0]
    assert any(call[0] == "note_on" for call in voice.sent)


def test_on_state_change_unloading_stops_the_drone():
    gs = _room_ready_game_server()
    pool = FakePool()
    room_audio = AudioBridge(pool)
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_audio=room_audio)
    agent.on_state_change(State.SETUP, State.RUNNING)

    agent.on_state_change(State.RUNNING, State.UNLOADING)

    voice = pool.acquired[0]
    assert voice.sent[-1][0] in ("note_off", "all_off")


def test_no_room_audio_injected_state_change_is_a_noop():
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())

    agent.on_state_change(State.SETUP, State.RUNNING)   # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_devicelink_agent.py -k "room_audio or on_state_change" -v`
Expected: FAIL — `AttributeError: 'DeviceLinkAgent' object has no attribute 'on_state_change'`

- [ ] **Step 3: Write the implementation**

Add the import in `devicelink/agent.py`:

```python
from control.state import State
```

Add `on_state_change` as a new method (an engine observer hook — `GameServer._notify` already calls it if present, since `add_observer(self)` runs in `__init__`, no wiring change needed beyond defining the method):

```python
    def on_state_change(self, old_state: State, new_state: State) -> None:
        """FluidSynth is silent without a note (see control/audio.py), so
        the Room's declared drone has to start once the Bit is actually
        RUNNING and stop once it's UNLOADING -- mirrors harness/led_smoke.py's
        own start_drone/on_release-adjacent handling for a player role."""
        if self._room_audio is None or self._room_dev is None:
            return
        if new_state == State.RUNNING:
            self._room_audio.start_drone(self._room_dev)
        elif new_state == State.UNLOADING:
            self._room_audio.stop_drone(self._room_dev)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_devicelink_agent.py -v`
Expected: PASS (all tests, including pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "feat(terrarium): start/stop the Room's Arco drone on Bit state changes"
```

---

### Task 6: The driver script — `harness/terrarium_boot.py`

**Files:**
- Create: `harness/terrarium_boot.py`
- Test: `tests/test_terrarium_boot.py`

**Interfaces:**
- Consumes: `control.boot.boot`/`shutdown` (Spec 1), `control.simulator_process.SimulatorProcess` (Task 1), `devicelink.server.DeviceLinkServer` (existing), `DeviceLinkAgent` with `room_bridge`/`room_audio` (Tasks 4-5), `control.audio.AudioBridge`/`FakePool` (existing, test-only injection).
- Produces: `build(config, bit_registry, *, arco_command, room_binding, host, port, arco_process_cls=ArcoProcess, simulator_popen=subprocess.Popen, room_audio=None) -> (gs, server, agent, arco, simulator)` — `room_audio` defaults to `None`, meaning "build a real one" (lazy `ArcoSynthPool` import, exactly like `harness/arco_synth.py` already does elsewhere); tests inject an already-built `AudioBridge(FakePool())` instead, so no test ever attempts a real pyarco import. `main()` — the first real, runnable entry point for this whole feature.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_terrarium_boot.py
from bits.test_bit import TestBit
from control.arco_process import FakePopen
from control.audio import AudioBridge, FakePool
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.rooms import RoomType
from control.state import State
from harness.terrarium_boot import build, shutdown


def _fake_arco(command, popen=None):
    from control.arco_process import ArcoProcess
    return ArcoProcess(command, popen=popen or FakePopen(), probe=lambda: True)


def _fake_room_audio():
    return AudioBridge(FakePool())


def test_build_wires_devicelink_room_bridge_and_simulator():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    gs, server, agent, arco, simulator = build(
        config, {"TestBit": TestBit},
        arco_command=["arco-server"], room_binding=RoomBindingRegistry(),
        host="127.0.0.1", port=0, arco_process_cls=_fake_arco,
        simulator_popen=FakePopen(), room_audio=_fake_room_audio())

    assert gs.room.bound_dev == "sim-room"
    assert agent._room_light is not None
    assert server.port != 0   # devicelink server actually bound before boot() ran

    shutdown(gs, agent, arco, simulator)


def test_devicelink_server_starts_before_boot_spawns_the_simulator():
    """The whole point of building devicelink first (see design spec section
    6): by the time boot()'s simulator_factory spawns the subprocess, the
    server it needs to connect to already exists. Assert the ordering
    directly via the fake simulator Popen's recorded launch args."""
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    sim_popen = FakePopen()

    gs, server, agent, arco, simulator = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=_fake_arco, simulator_popen=sim_popen,
        room_audio=_fake_room_audio())

    launched_command = sim_popen.commands[0]
    assert f"ws://127.0.0.1:{server.port}/ws" in launched_command
    shutdown(gs, agent, arco, simulator)


def test_shutdown_tears_down_arco_and_simulator():
    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    fake_arco_popen = FakePopen()
    sim_popen = FakePopen()

    gs, server, agent, arco, simulator = build(
        config, {"TestBit": TestBit}, arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(), host="127.0.0.1", port=0,
        arco_process_cls=lambda cmd: _fake_arco(cmd, popen=fake_arco_popen),
        simulator_popen=sim_popen, room_audio=_fake_room_audio())
    gs.run()

    shutdown(gs, agent, arco, simulator)

    assert gs.state == State.IDLE
    assert fake_arco_popen.signals
    assert sim_popen.signals
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_terrarium_boot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.terrarium_boot'`

- [ ] **Step 3: Write the implementation**

```python
# harness/terrarium_boot.py
"""python -m harness.terrarium_boot -- boot a Terrarium into a TEST Room,
simulator included. The first real entry point for
docs/superpowers/specs/2026-08-10-room-concept-and-load-sequence-design.md
and its follow-up,
docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md.

Ordering matters here and is why this script -- not control/boot.py --
constructs DeviceLinkServer: the server must already be listening before
boot() calls its simulator_factory, which spawns harness/room_simulator.py
and expects to connect immediately. See design spec section 6.
"""

from __future__ import annotations

import subprocess
import sys

from bits.test_bit import TestBit
from control.arco_process import ArcoProcess
from control.boot import boot as _boot
from control.boot import shutdown as _boot_shutdown
from control.boot_config import BootConfig
from control.room_binding import RoomBindingRegistry
from control.simulator_process import SimulatorProcess
from devicelink.agent import DeviceLinkAgent
from devicelink.server import DeviceLinkServer

SIM_DEV = "sim-room"


class _SimulatorFactory:
    """boot()'s simulator_factory contract is a bare Callable[[], str] --
    this closure captures the SimulatorProcess handle in an attribute so the
    caller can retrieve it once boot() returns, without changing boot()'s
    signature (see design spec section 8's open question, resolved here)."""

    def __init__(self, server_url: str, *, popen=subprocess.Popen) -> None:
        self._server_url = server_url
        self._popen = popen
        self.process: SimulatorProcess | None = None

    def __call__(self) -> str:
        self.process = SimulatorProcess(
            [sys.executable, "-m", "harness.room_simulator",
             "--dev", SIM_DEV, "--server", self._server_url],
            popen=self._popen)
        self.process.start()
        return SIM_DEV


def build(config: BootConfig, bit_registry: dict, *, arco_command: list,
         room_binding: RoomBindingRegistry, host: str = "127.0.0.1",
         port: int = 0, arco_process_cls=ArcoProcess,
         simulator_popen=subprocess.Popen, room_audio=None):
    """Construct the whole stack. Returns (game_server, devicelink_server,
    devicelink_agent, arco_process, simulator_process).

    room_audio: an already-constructed AudioBridge. Default None builds a
    real one backed by ArcoSynthPool -- lazily imported, exactly like
    harness/arco_synth.py already does elsewhere, so this module costs
    nothing when Arco/pyarco are absent. Tests inject an
    AudioBridge(FakePool()) instead, so no test ever attempts a real pyarco
    import or calls ArcoSynthPool.start() (which FakePool has no equivalent
    of -- it needs no live connection to fake). Audio is unconditionally on
    once real (design spec section 3): there is no --audio-style opt-out."""
    server = DeviceLinkServer(host=host, port=port)
    server.start()

    factory = _SimulatorFactory(f"ws://{host}:{server.port}/ws",
                                popen=simulator_popen)
    gs, room_bridge, arco = _boot(
        config, bit_registry, arco_command=arco_command,
        room_binding=room_binding, arco_process_cls=arco_process_cls,
        simulator_factory=factory)

    if room_audio is None:
        from control.audio import AudioBridge
        from harness.arco_synth import ArcoSynthPool
        pool = ArcoSynthPool() if config.arco_soundfont is None \
            else ArcoSynthPool(soundfont=config.arco_soundfont)
        pool.start()
        room_audio = AudioBridge(pool)

    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge,
                            room_audio=room_audio)
    return gs, server, agent, arco, factory.process


def shutdown(gs, agent: DeviceLinkAgent, arco: ArcoProcess,
            simulator: SimulatorProcess) -> None:
    """Tear down in order: the Bit/Room (via control.boot.shutdown, which
    also frees the Room's Arco voice through room_bridge.shutdown()), then
    the simulator subprocess, then Arco itself."""
    _boot_shutdown(gs, agent._room_bridge or _NullRoomBridge(), arco)
    simulator.shutdown()


class _NullRoomBridge:
    """control.boot.shutdown() always calls room_bridge.shutdown() -- if
    this driver somehow ran with no Room configured at all, hand it
    something inert rather than special-casing shutdown()'s signature."""

    def shutdown(self) -> None:
        pass


def main() -> None:
    import argparse

    from control.rooms import RoomType

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--arco-command", default="/Users/chris/projects/arco/apps/pytest/server")
    args = ap.parse_args()

    config = BootConfig(room_type=RoomType.TEST, bit_name="TestBit")
    room_binding = RoomBindingRegistry()
    gs, server, agent, arco, simulator = build(
        config, {"TestBit": TestBit}, arco_command=[args.arco_command],
        room_binding=room_binding, host=args.host, port=args.port)

    print(f"DeviceLink listening on ws://{args.host}:{server.port}/ws "
          f"(Ctrl-C to stop)")
    gs.run()
    try:
        import time
        while True:
            agent.poll()
            gs.tick(1.0 / 44.0)
            time.sleep(1.0 / 44.0)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(gs, agent, arco, simulator)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_terrarium_boot.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest tests -v`
Expected: PASS (every test, old and new)

- [ ] **Step 6: Manual end-to-end verification (not part of the automated suite)**

This is the payoff moment for both specs. With a real Arco checkout available
(`PYTHONPATH=/Users/chris/projects/arco`) and `luxaeterna[websim]` installed:

```bash
PYTHONPATH=/Users/chris/projects/arco python3 -m harness.terrarium_boot
```

Confirm: the process starts Arco, resolves TEST, spawns the simulator, and
prints a URL. Open it — the canvas should render `aurora`'s hue and, once
`TestBit`'s natural ~2s run completes, fade out. Confirm sound plays from the
host machine's audio output during the run.

- [ ] **Step 7: Commit**

```bash
git add harness/terrarium_boot.py tests/test_terrarium_boot.py
git commit -m "feat(terrarium): add the end-to-end TEST-room boot driver"
```

---

## Self-Review Notes

- **Placeholder scan:** none remain. An earlier draft of this plan left a
  `room_type=None` placeholder in Task 6's `main()` with a follow-up step
  telling the implementer to fix it — that's exactly the pattern the
  writing-plans rubric prohibits, so it was corrected in place
  (`RoomType.TEST`, imported directly) rather than deferred to a step.
- **A real bug caught during self-review:** the first draft had `build()`
  unconditionally constructing a real `ArcoSynthPool` and calling
  `.start()` on it — which every Task 6 test would have hit, attempting a
  real pyarco import and either failing or hanging in CI, since the
  existing `FakePool` test double (`control/audio.py`) has no `.start()`
  method to fake that call against. Fixed by making `room_audio` an
  injectable, already-constructed `AudioBridge` (default `None` builds the
  real pool; tests pass `AudioBridge(FakePool())`), matching the same
  fakeable-by-injection pattern `arco_process_cls`/`simulator_popen`
  already use in this same function.
- **A non-verifying test caught during self-review:** Task 4's
  `test_room_dev_cue_routes_to_room_bridge_not_normal_bridges` originally
  asserted `agent._room_light.session.render_into is not None` — a bound
  method reference is never `None`, so this passed unconditionally and
  proved nothing. Replaced with a before/after frame comparison that
  actually fails if the cue never reached the session.
- **Type consistency:** `SimulatorProcess.start()`/`.shutdown()` (Task 1)
  match their use in `_SimulatorFactory`/`shutdown()` (Task 6) exactly.
  `DeviceLinkAgent`'s `room_bridge`/`room_audio` constructor params (Task 4)
  match Task 5's and Task 6's usage. `room_role_name()` (Task 2) matches its
  consumption in Task 4's `_setup_room()`. `build()`'s `room_audio` param
  (Task 6) is consistently `None`-defaulted/injected across all three of its
  tests and `main()`.
- **Spec coverage:** every in-scope item from
  `docs/superpowers/specs/2026-08-10-terrarium-visualization-simulator-design.md`
  section 2 has a task: `SimulatorProcess` (T1), the reference Bit (T2), the
  simulator subprocess (T3), the cue-routing gap closure (T4-T5), and the
  load-sequence wiring (T6).
