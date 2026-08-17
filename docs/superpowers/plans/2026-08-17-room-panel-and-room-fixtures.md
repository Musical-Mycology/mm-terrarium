# Room Panel and Room Fixtures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Room a fixture model of its own and give the Terrarium Console a Room panel that shows the Room's declared instruments, their live controller values, and its live rendered light.

**Architecture:** A pure `RoomProfile` declaration in `control/`, adapted to a luxaeterna `SurfaceCapability` by a thin `harness/` adapter. `DeviceLinkAgent` builds the Room's `LightSession` from that instead of borrowing the Tuneshroom's, and emits each rendered Room frame to an optional guarded sink. `ConsoleAgent` consumes that sink, decimates it, and broadcasts it alongside a new `room` read model. The existing Room-hiding filters are left untouched; visibility is added through a new, separately scoped key.

**Tech Stack:** Python 3, pytest, `websockets` (sync API), luxaeterna (dev/test dependency reached from the project venv), vanilla HTML/CSS/JS with no build step.

**Spec:** [`docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md`](../specs/2026-08-17-room-panel-and-room-fixtures-design.md)

## Global Constraints

- **Run the suite through the project venv, never a bare interpreter:** `.venv/bin/python -m pytest tests -v`. There is no bare `python` on the dev boxes and luxaeterna is installed only in `.venv`. Using `python3` produces a phantom import error in `tests/test_terrarium_boot.py` that looks exactly like a real failure.
- **Baseline to not regress: 764 passed, 1 skipped.**
- **The suite must stay green with no O2, no Arco, no pyarco importable.** Any test needing luxaeterna starts with `pytest.importorskip("luxaeterna")`.
- **No `control/` module may import luxaeterna, pyarco or o2litepy AT MODULE LEVEL.** Function-scoped lazy imports are permitted and one exists deliberately (`control/arco_process.py:37`, `# noqa: PLC0415 (lazy by design)`). Task 1 adds a test pinning the module-level boundary.
- **`control/engine.py` is not edited by this plan.**
- **`uplink/` is not edited by this plan.**
- **Boundary rule 2:** nothing in `console/` or `devicelink/` may propagate an exception into the engine tick. Every new transport-owned sink is wrapped at its call site.
- **Boundary rule 5:** a test double must never be more permissive than the library it stands for.
- **No build step for the console.** No npm, no bundler, no external asset fetches.
- **Every existing CLI invocation must behave identically** when the new flags are not passed.
- **No em dashes in prose, comments, or docstrings.**

---

### Task 1: The pure Room profile declaration

**Files:**
- Create: `control/room_profile.py`
- Create: `tests/test_room_profile.py`

**Interfaces:**
- Consumes: `control.rooms.RoomType` (existing enum, members `TEST` and `DEMO`).
- Produces: `RoomZone(name: str, start: int, count: int)` frozen dataclass; `RoomProfile(surface_id: str, pixel_count: int, color_order: str, zones: tuple[RoomZone, ...])` frozen dataclass with a `channel_count -> int` property; `ROOM_PROFILES: dict[RoomType, RoomProfile]`; `room_profile(room_type: RoomType) -> RoomProfile` raising `NotImplementedError` for unsupported types.

- [ ] **Step 1: Write the failing test**

Create `tests/test_room_profile.py`:

```python
"""RoomProfile: the Room's own fixture declaration. Pure -- no luxaeterna,
which is the point (see the design spec section 4's correction note)."""

import pathlib

import pytest

from control.room_profile import ROOM_PROFILES, RoomProfile, RoomZone, room_profile
from control.rooms import RoomType


def test_test_room_is_not_shaped_like_a_tuneshroom():
    profile = room_profile(RoomType.TEST)
    assert profile.surface_id == "room_test"
    assert profile.pixel_count == 60
    assert profile.color_order == "GRB"
    assert [z.name for z in profile.zones] == ["left", "center", "right"]


def test_channel_count_is_three_per_pixel():
    assert room_profile(RoomType.TEST).channel_count == 180


def test_zones_tile_the_surface_without_gaps_or_overlap():
    profile = room_profile(RoomType.TEST)
    cursor = 0
    for zone in profile.zones:
        assert zone.start == cursor, f"zone {zone.name} does not abut its predecessor"
        cursor += zone.count
    assert cursor == profile.pixel_count


def test_primary_is_not_declared_here():
    """luxaeterna's SurfaceCapability.zone() synthesizes `primary` on demand,
    and harness/room_surface.py appends it. Declaring it here would make it a
    real zone that the Console would draw on top of every other one."""
    assert "primary" not in [z.name for z in room_profile(RoomType.TEST).zones]


def test_demo_room_raises_rather_than_downgrading():
    """Matches resolve_room_type()'s existing fail-hard-never-downgrade
    contract. DEMO's backend is a deferred follow-up spec."""
    with pytest.raises(NotImplementedError):
        room_profile(RoomType.DEMO)


def test_profile_is_immutable():
    profile = room_profile(RoomType.TEST)
    with pytest.raises(Exception):
        profile.pixel_count = 99


def test_every_room_type_key_maps_to_a_room_profile():
    for key, value in ROOM_PROFILES.items():
        assert isinstance(key, RoomType)
        assert isinstance(value, RoomProfile)


def test_zone_is_a_plain_value():
    zone = RoomZone("left", 0, 20)
    assert (zone.name, zone.start, zone.count) == ("left", 0, 20)


def test_no_control_module_imports_a_renderer_at_module_level():
    """Every control/ module must import, and the whole suite must run, with
    luxaeterna, pyarco and o2litepy absent. A MODULE-LEVEL import breaks that;
    a function-scoped one does not, because it runs only when called.

    Indented imports are deliberately not flagged. control/arco_process.py:37
    carries a lazy `from pyarco.arco_engine import arco` marked
    `# noqa: PLC0415 (lazy by design)` -- probing the Arco subprocess for
    readiness is that module's whole job. The repo states the stricter
    no-import-anywhere rule per-module where it applies (see control/audio.py's
    docstring), not package-wide. See the design spec section 4.
    """
    control_dir = pathlib.Path(__file__).resolve().parent.parent / "control"
    banned = ("luxaeterna", "pyarco", "o2litepy")
    offenders = []
    for path in sorted(control_dir.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if line[:1].isspace():          # indented: function-scoped, allowed
                continue
            if not (line.startswith("import ") or line.startswith("from ")):
                continue
            if any(line.split()[1].split(".")[0] == pkg for pkg in banned):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert offenders == [], ("control/ must have no module-level renderer "
                             "imports:\n" + "\n".join(offenders))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_room_profile.py -v`
Expected: FAIL, collection error `ModuleNotFoundError: No module named 'control.room_profile'`

- [ ] **Step 3: Write the implementation**

Create `control/room_profile.py`:

```python
"""RoomProfile: the Room's own fixture declaration, so a Room stops being
shaped like a Tuneshroom.

Deliberately pure. This module imports nothing outside the standard library
and control/, which is what lets the engine be reasoned about and tested with
no renderer present. The luxaeterna adapter lives in harness/room_surface.py,
mirroring how harness/device_bridge.py already adapts Control-side role
declarations for player devices. See
docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md
section 4.
"""

from __future__ import annotations

from dataclasses import dataclass

from control.rooms import RoomType


@dataclass(frozen=True)
class RoomZone:
    """A named, contiguous run of pixels a light instrument can target.

    Mirrors luxaeterna's Zone field-for-field so the adapter is a rename and
    nothing more. `primary` is deliberately NOT declared in any profile:
    SurfaceCapability.zone() synthesizes it on demand for the whole surface,
    and a real `primary` zone would overlay every other zone in the Console's
    view.
    """
    name: str
    start: int
    count: int


@dataclass(frozen=True)
class RoomProfile:
    """One Room's physical (or simulated) light surface."""
    surface_id: str
    pixel_count: int
    color_order: str
    zones: tuple[RoomZone, ...]

    @property
    def channel_count(self) -> int:
        """Wire width of one rendered frame. Three channels per pixel, matching
        the GRB wire devicelink/protocol.py's leds_event carries today. The
        RGBW question (widening to four) is a separate open decision about the
        Tuneshroom's white die and does not belong to the Room."""
        return self.pixel_count * 3


# A single luxaeterna Universe is 512 DMX channels, so a one-surface Room caps
# at 170 px RGB. 60 px sits well inside that. Anything larger needs
# PixelSpan/UniverseSet (luxaeterna has them; harness/array_smoke.py uses them
# for the 864 px venue array) and is out of scope for this slice.
#
# Linear with three equal zones because the physical Terrarium array is a
# single 6 m run, not a ring and a stem.
ROOM_PROFILES: dict[RoomType, RoomProfile] = {
    RoomType.TEST: RoomProfile(
        surface_id="room_test",
        pixel_count=60,
        color_order="GRB",
        zones=(RoomZone("left", 0, 20),
               RoomZone("center", 20, 20),
               RoomZone("right", 40, 20)),
    ),
}


def room_profile(room_type: RoomType) -> RoomProfile:
    """This Room type's fixture declaration.

    Raises rather than substituting a default, matching
    control/rooms.py's resolve_room_type(): a Terrarium that cannot render the
    Room it was configured for must fail at boot, not render the wrong thing
    all night.
    """
    try:
        return ROOM_PROFILES[room_type]
    except KeyError:
        raise NotImplementedError(
            f"{room_type.name} has no room profile; only "
            f"{', '.join(t.name for t in ROOM_PROFILES)} is implemented"
        ) from None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_profile.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 773 passed, 1 skipped

- [ ] **Step 6: Commit**

```bash
git add control/room_profile.py tests/test_room_profile.py
git commit -m "feat(control): RoomProfile, the Room's own fixture declaration

The Room borrowed shroom_capability(), so structurally it was a 12-LED
Tuneshroom with a ring and a stem. RoomProfile declares its own surface:
60 px in three named zones, linear like the real 6 m array.

Pure by construction, no luxaeterna import. A test pins that control/
imports none of luxaeterna, pyarco or o2litepy, which was true and
accidental until now."
```

---

### Task 2: The luxaeterna adapter

**Files:**
- Create: `harness/room_surface.py`
- Create: `tests/test_room_surface.py`

**Interfaces:**
- Consumes: `control.room_profile.RoomProfile`, `RoomZone`, `room_profile()` from Task 1.
- Produces: `to_capability(profile: RoomProfile) -> SurfaceCapability`, returning a luxaeterna `SurfaceCapability` whose `zones` are the profile's zones plus a synthesized `Zone("primary", 0, profile.pixel_count)` appended last.

- [ ] **Step 1: Write the failing test**

Create `tests/test_room_surface.py`:

```python
"""The RoomProfile -> luxaeterna SurfaceCapability adapter."""

import pytest

pytest.importorskip("luxaeterna")

from control.room_profile import RoomProfile, RoomZone, room_profile
from control.rooms import RoomType
from harness.room_surface import to_capability


def test_scalar_fields_carry_across():
    cap = to_capability(room_profile(RoomType.TEST))
    assert cap.surface_id == "room_test"
    assert cap.pixel_count == 60
    assert cap.color_order == "GRB"


def test_declared_zones_carry_across_in_order():
    cap = to_capability(room_profile(RoomType.TEST))
    named = [(z.name, z.start, z.count) for z in cap.zones]
    assert named[:3] == [("left", 0, 20), ("center", 20, 20), ("right", 40, 20)]


def test_primary_is_appended_spanning_the_whole_surface():
    """light_manifest instruments target "primary" by default (see
    bits/test_bit.py's Room declaration), so it has to resolve."""
    cap = to_capability(room_profile(RoomType.TEST))
    primary = cap.zone("primary")
    assert (primary.start, primary.count) == (0, 60)


def test_declared_zones_resolve_by_name():
    cap = to_capability(room_profile(RoomType.TEST))
    assert (cap.zone("center").start, cap.zone("center").count) == (20, 20)


def test_adapter_does_not_mutate_the_profile():
    profile = room_profile(RoomType.TEST)
    before = len(profile.zones)
    to_capability(profile)
    assert len(profile.zones) == before


def test_a_profile_with_no_zones_still_yields_a_usable_primary():
    profile = RoomProfile(surface_id="bare", pixel_count=12,
                          color_order="GRB", zones=())
    cap = to_capability(profile)
    assert cap.zone("primary").count == 12


def test_zone_order_is_preserved_for_an_unsorted_profile():
    profile = RoomProfile(
        surface_id="odd", pixel_count=30, color_order="GRB",
        zones=(RoomZone("b", 10, 20), RoomZone("a", 0, 10)))
    cap = to_capability(profile)
    assert [z.name for z in cap.zones][:2] == ["b", "a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_room_surface.py -v`
Expected: FAIL, collection error `ModuleNotFoundError: No module named 'harness.room_surface'`

- [ ] **Step 3: Write the implementation**

Create `harness/room_surface.py`:

```python
"""Adapt a Control-side RoomProfile into the luxaeterna SurfaceCapability the
renderer needs.

This module exists so control/room_profile.py can stay import-free. It is the
Room-scoped peer of harness/device_bridge.py, which already does the same job
for a player device's role declaration. Both consumers (devicelink/agent.py
and harness/room_simulator.py) already import from harness/, so this
introduces no new dependency direction. See
docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md
section 4.
"""

from __future__ import annotations

from control.room_profile import RoomProfile
from luxaeterna.synth.capability import SurfaceCapability, Zone


def to_capability(profile: RoomProfile) -> SurfaceCapability:
    """Build the renderer's view of this Room's surface.

    `primary` is appended here rather than declared in the profile. A
    light_manifest instrument that names no target resolves to it, so it has
    to exist for the renderer; but it spans the whole surface, so it must not
    appear in the Console's zone list where it would be drawn over every real
    zone. Appending it in the adapter gives both halves what they need from
    one declaration.
    """
    zones = [Zone(z.name, z.start, z.count) for z in profile.zones]
    zones.append(Zone("primary", 0, profile.pixel_count))
    return SurfaceCapability(
        surface_id=profile.surface_id,
        pixel_count=profile.pixel_count,
        color_order=profile.color_order,
        zones=zones,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_surface.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add harness/room_surface.py tests/test_room_surface.py
git commit -m "feat(harness): RoomProfile to SurfaceCapability adapter

Keeps control/room_profile.py import-free while giving the renderer the
luxaeterna type it needs. Peer of harness/device_bridge.py, which already
adapts a player role's declaration the same way.

primary is appended here rather than declared in the profile: the renderer
needs it to resolve an untargeted instrument, and the Console must not draw
it over every real zone."
```

---

### Task 3: Per-instance frame width in ShroomClient

**Files:**
- Modify: `harness/shroom_client.py:52` (comment), `:73-98` (constructor), `:155-173` (`_on_leds`)
- Modify: `tests/test_shroom_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ShroomClient(dev, node, leds=None, on_role=None, expected_channels: int = LED_CHANNELS)`. `LED_CHANNELS` stays exported at its current value of 36.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shroom_client.py`:

```python
def test_default_width_is_still_thirty_six():
    """Every existing caller constructs ShroomClient without this argument
    and must be unaffected."""
    from harness.shroom_client import LED_CHANNELS, ShroomClient
    client = ShroomClient("ie1", "node-a")
    assert client.expected_channels == LED_CHANNELS == 36


def test_a_wider_client_accepts_its_own_width():
    from harness.shroom_client import ShroomClient
    from devicelink import protocol

    class Leds:
        def __init__(self):
            self.shown = []

        def show(self, frame):
            self.shown.append(frame)

        def clear(self):
            self.shown.append(b"")

    leds = Leds()
    client = ShroomClient("sim-room", "", leds=leds, expected_channels=180)

    assert client.handle(protocol.leds_event("sim-room", list(range(180)))) \
        == "/sim-room/leds"
    client.tick(now=1.0)
    assert leds.shown == [bytes(v & 0xFF for v in range(180))]


def test_a_wider_client_drops_a_thirty_six_channel_frame():
    """Dropped, never truncated: rendering a short frame would turn a
    configuration mismatch into a subtly wrong picture instead of a logged
    drop."""
    from harness.shroom_client import ShroomClient
    from devicelink import protocol

    client = ShroomClient("sim-room", "", expected_channels=180)

    assert client.handle(protocol.leds_event("sim-room", list(range(36)))) == ""


def test_a_default_client_drops_a_one_eighty_channel_frame():
    from harness.shroom_client import ShroomClient
    from devicelink import protocol

    client = ShroomClient("ie1", "node-a")

    assert client.handle(protocol.leds_event("ie1", list(range(180)))) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_shroom_client.py -k "width or thirty_six or one_eighty" -v`
Expected: FAIL with `AttributeError: 'ShroomClient' object has no attribute 'expected_channels'` and `TypeError: __init__() got an unexpected keyword argument 'expected_channels'`

- [ ] **Step 3: Write the implementation**

In `harness/shroom_client.py`, replace the constructor signature and opening lines (currently lines 73-79):

```python
    def __init__(self, dev: str, node: str, leds=None,
                 on_role: Callable[[dict], None] | None = None,
                 expected_channels: int = LED_CHANNELS) -> None:
        self.dev = dev
        self.node = node
        self.leds = leds
        self.on_role = on_role
        # Frame width this client will accept, in channels. Defaults to the
        # 12 px x GRB Tuneshroom wire, so every existing caller is unchanged.
        # The Room simulator passes its RoomProfile.channel_count instead: a
        # Room is not a Tuneshroom and does not have 36 channels. See
        # control/room_profile.py.
        self.expected_channels = expected_channels
        self.config: dict | None = None
```

Then in `_on_leds`, replace the length check (currently lines 160-162):

```python
        if len(channels) != self.expected_channels:
            logger.debug("dropping /leds with %d channels, expected %d",
                         len(channels), self.expected_channels)
            return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_shroom_client.py -v`
Expected: PASS, all tests in the file

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green, and no fewer tests than the previous task (roughly 783).

- [ ] **Step 6: Commit**

```bash
git add harness/shroom_client.py tests/test_shroom_client.py
git commit -m "feat(harness): per-instance frame width in ShroomClient

LED_CHANNELS = 36 was a module constant and _on_leds rejected anything
else, so a Room of any other pixel count could not receive a frame.
expected_channels defaults to LED_CHANNELS, leaving every existing caller
byte-identical.

A wrong-width frame is still dropped and logged, never truncated: a short
render would turn a configuration mismatch into a subtly wrong picture."
```

---

### Task 4: DeviceLinkAgent builds the Room from its profile

**Files:**
- Modify: `devicelink/agent.py:74-87` (constructor), `:129-162` (`_setup_room`), `:228-258` (`_render_room`)
- Modify: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `control.room_profile.room_profile()` (Task 1), `harness.room_surface.to_capability()` (Task 2).
- Produces: `DeviceLinkAgent(..., room_profile=None)`. When `None`, the agent resolves the profile from `gs.room.room_type` itself. Adds `self._room_profile: RoomProfile | None`, set in `_setup_room`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_devicelink_agent.py`:

```python
def test_room_session_is_built_from_the_room_profile_not_the_shroom():
    """The whole point of the fixture model: a Room is 60 px in three zones,
    not a 12 px ring and stem."""
    from control.room_profile import room_profile
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())

    assert agent._room_profile == room_profile(RoomType.TEST)
    assert agent._room_light.session.capability.pixel_count == 60
    assert agent._room_light.session.capability.surface_id == "room_test"


def test_player_devices_still_get_the_shroom_capability():
    """Players remain Tuneshrooms. Only the Room changed."""
    agent, server, gs = _agent_with_joined_device("ie1")
    assert agent.bridges["ie1"].session.capability.pixel_count == 12


def test_room_frame_is_the_profile_width_not_thirty_six():
    from control.room_profile import room_profile
    gs = _room_ready_game_server()
    server = FakeServer()
    server.bind_dev("sim-room", "c-room")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())

    for _ in range(3):
        agent.poll()

    frames = [m for dev, m in server.sent if m["address"] == "/sim-room/leds"]
    assert frames, "the Room emitted no frame"
    assert len(frames[-1]["args"][0]) == room_profile(RoomType.TEST).channel_count
    assert len(frames[-1]["args"][0]) == 180


def test_an_explicit_room_profile_overrides_the_resolved_one():
    from control.room_profile import RoomProfile, RoomZone
    profile = RoomProfile(surface_id="custom", pixel_count=24,
                          color_order="GRB", zones=(RoomZone("all", 0, 24),))
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_profile=profile)

    assert agent._room_light.session.capability.pixel_count == 24


def test_no_room_configured_leaves_the_profile_unset():
    """A GameServer built the pre-Room way must keep working."""
    gs = GameServer({"TestBit": TestBit})
    agent = DeviceLinkAgent(gs, FakeServer())
    assert agent._room_profile is None
    assert agent._room_light is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -k "profile or shroom_capability or thirty_six" -v`
Expected: FAIL with `AttributeError: 'DeviceLinkAgent' object has no attribute '_room_profile'`

- [ ] **Step 3: Write the implementation**

In `devicelink/agent.py`, add the imports next to the existing luxaeterna ones:

```python
from control.room_profile import RoomProfile, room_profile
from harness.room_surface import to_capability
```

Change the constructor signature (currently lines 75-77):

```python
    def __init__(self, game_server: GameServer, server,
                 capability=None, clock=time.monotonic,
                 room_bridge=None, room_audio=None, horizon: float = 0.0,
                 room_profile=None):
```

Add to the Room-wiring block, immediately before the existing `self._room_dev` line (currently line 121):

```python
        # The Room's fixture declaration. None here means "resolve it from the
        # bound Room's type"; an explicit profile is for tests and for a future
        # installation that overrides the shipped shape. self._capability is
        # NOT consulted for the Room any more: that attribute is the player
        # device shape, and sharing one capability between a Tuneshroom and a
        # Room is exactly the confusion this slice removes.
        self._room_profile: RoomProfile | None = room_profile
```

In `_setup_room`, replace the capability line (currently line 153):

```python
        if self._room_profile is None:
            self._room_profile = globals()["room_profile"](room.room_type)
        cap = to_capability(self._room_profile)
```

Note: the constructor parameter shadows the module-level `room_profile` function inside `__init__` only. `_setup_room` is a different scope, so it can call `room_profile(room.room_type)` directly. Use that simpler form:

```python
        if self._room_profile is None:
            self._room_profile = room_profile(room.room_type)
        cap = to_capability(self._room_profile)
```

In `_render_room`, replace the hardcoded slice (currently line 249):

```python
        frame = bytes(universe.get_frame()[:self._room_profile.channel_count])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -v`
Expected: PASS, all tests in the file

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green, and no fewer tests than the previous task (roughly 788).

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py
git commit -m "feat(devicelink): build the Room from its own profile

The Room's LightSession was built from 'self._capability or
shroom_capability()' and its frame sliced with a literal [:36], so a Room
was a 12-LED Tuneshroom with a ring and a stem. It now resolves its
RoomProfile from the bound Room's type and renders 60 px in three zones,
180 channels wide.

Player devices are untouched and keep shroom_capability()."
```

---

### Task 5: RoomBridge records live controller values

**Files:**
- Modify: `control/room_bridge.py:59-99`
- Modify: `tests/test_room_bridge.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `RoomBridge.controllers: dict[int, int]`, mapping controller number to its last seen value. Cleared by `release()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_room_bridge.py`:

```python
def test_controllers_starts_empty():
    from control.room_bridge import RoomBridge
    assert RoomBridge().controllers == {}


def test_feed_light_records_the_controller_value():
    from control.room_bridge import FakeRoomLightSink, RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room", light=FakeRoomLightSink())

    bridge.feed_light(0xB0, 74, 93)

    assert bridge.controllers == {74: 93}


def test_feed_light_keeps_the_latest_value_per_controller():
    from control.room_bridge import FakeRoomLightSink, RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room", light=FakeRoomLightSink())

    bridge.feed_light(0xB0, 74, 10)
    bridge.feed_light(0xB0, 11, 55)
    bridge.feed_light(0xB0, 74, 120)

    assert bridge.controllers == {74: 120, 11: 55}


def test_a_note_on_is_not_recorded_as_a_controller():
    from control.room_bridge import FakeRoomLightSink, RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room", light=FakeRoomLightSink())

    bridge.feed_light(0x90, 45, 90)

    assert bridge.controllers == {}


def test_controllers_are_recorded_even_with_no_light_sink_bound():
    """The Console reads this whether or not a renderer is attached."""
    from control.room_bridge import RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room")

    bridge.feed_light(0xB0, 74, 42)

    assert bridge.controllers == {74: 42}


def test_release_clears_the_controllers():
    from control.room_bridge import FakeRoomLightSink, RoomBridge
    bridge = RoomBridge()
    bridge.bind("sim-room", light=FakeRoomLightSink())
    bridge.feed_light(0xB0, 74, 93)

    bridge.release()

    assert bridge.controllers == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_bridge.py -k controller -v`
Expected: FAIL with `AttributeError: 'RoomBridge' object has no attribute 'controllers'`

- [ ] **Step 3: Write the implementation**

In `control/room_bridge.py`, add to `RoomBridge.__init__` (currently lines 59-62):

```python
    def __init__(self) -> None:
        self.dev: str | None = None
        # Last value seen per controller number, for the Console's live
        # read-out. Recorded in feed_light rather than feed_audio because
        # light is fed on every cue while audio is released on its own
        # schedule (see feed_light/feed_audio below), so the light side is the
        # one that sees every value. A plain dict of ints: this class stays
        # backend-agnostic by construction and imports nothing.
        self.controllers: dict[int, int] = {}
        self._light: RoomLightSink | None = None
        self._audio: RoomAudioSink | None = None
```

Add the recording to `feed_light`, before the sink call:

```python
        if status & 0xF0 == 0xB0:
            self.controllers[d1] = d2
        if self._light is not None:
            self._light.feed_midi(status, d1, d2)
```

Add the clear to `release`:

```python
    def release(self) -> None:
        if self._light is not None:
            self._light.clear()
        self.controllers.clear()
        self.dev = None
        self._light = None
        self._audio = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_bridge.py -v`
Expected: PASS, all tests in the file

- [ ] **Step 5: Commit**

```bash
git add control/room_bridge.py tests/test_room_bridge.py
git commit -m "feat(control): RoomBridge records live controller values

The Console's instrument cards show each declared lane's current value
(cc:74 = 93). Recorded in feed_light, which sees every cue, rather than
feed_audio, which is released on its own schedule.

A plain dict of ints, so RoomBridge stays backend-agnostic by
construction."
```

---

### Task 6: The Room read model

**Files:**
- Create: `control/room_view.py`
- Create: `tests/test_room_view.py`

**Interfaces:**
- Consumes: `control.room_profile.RoomProfile` (Task 1), `control.rooms.Room`, `control.roles.Role`.
- Produces: `room_view(room, profile, role, controllers) -> dict | None`. Returns `None` when `room` is `None`. `role` may be `None` (no Bit loaded), yielding an empty `instruments` list. Keys: `room_type`, `bound_dev`, `capability` (`surface_id`, `pixel_count`, `color_order`, `zones`), `instruments` (each with `kind` of `"light"` or `"audio"`), `controllers`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_room_view.py`:

```python
"""The Room read model the Console renders. Pure dict builders, no engine
imports, mirroring console/protocol.py."""

from control.room_profile import room_profile
from control.room_view import room_view
from control.rooms import Room, RoomType, room_role


def _role():
    _, role, _ = room_role(
        RoomType.TEST,
        light_manifest={"instruments": [
            {"instrument": "aurora", "target": "primary",
             "params": {"hue": 0.6, "level": 0.55},
             "lanes": [{"source": "cc:74", "dest": "hue"}]}]},
        ugen_manifest={"instruments": [
            {"instrument": "flsyn", "program": 89,
             "drone": {"key": 50, "velocity": 80},
             "lanes": [{"source": "cc:74", "dest": "cc:74"}]}]},
    )
    return role


def _view():
    return room_view(Room(room_type=RoomType.TEST, bound_dev="sim-room"),
                     room_profile(RoomType.TEST), _role(), {74: 93})


def test_no_room_configured_yields_none():
    assert room_view(None, None, None, {}) is None


def test_header_fields():
    view = _view()
    assert view["room_type"] == "TEST"
    assert view["bound_dev"] == "sim-room"


def test_capability_carries_the_zones():
    view = _view()
    assert view["capability"]["surface_id"] == "room_test"
    assert view["capability"]["pixel_count"] == 60
    assert view["capability"]["color_order"] == "GRB"
    assert view["capability"]["zones"] == [
        {"name": "left", "start": 0, "count": 20},
        {"name": "center", "start": 20, "count": 20},
        {"name": "right", "start": 40, "count": 20},
    ]


def test_primary_is_absent_from_the_serialized_zones():
    """It spans the whole surface, so drawing it would cover every real zone.
    The renderer gets it from harness/room_surface.py instead."""
    assert "primary" not in [z["name"] for z in _view()["capability"]["zones"]]


def test_light_and_audio_appear_in_one_list_discriminated_by_kind():
    """The point of the panel: cc:74 is visibly the same controller driving
    aurora's hue and FluidSynth's cutoff."""
    instruments = _view()["instruments"]
    assert [i["kind"] for i in instruments] == ["light", "audio"]
    assert instruments[0]["instrument"] == "aurora"
    assert instruments[0]["target"] == "primary"
    assert instruments[1]["instrument"] == "flsyn"


def test_lanes_carry_across_for_both_kinds():
    instruments = _view()["instruments"]
    assert instruments[0]["lanes"] == [{"source": "cc:74", "dest": "hue"}]
    assert instruments[1]["lanes"] == [{"source": "cc:74", "dest": "cc:74"}]


def test_audio_extras_are_preserved():
    audio = _view()["instruments"][1]
    assert audio["program"] == 89
    assert audio["drone"] == {"key": 50, "velocity": 80}


def test_controllers_are_carried_through():
    assert _view()["controllers"] == {74: 93}


def test_no_bit_loaded_yields_capability_with_no_instruments():
    view = room_view(Room(room_type=RoomType.TEST), room_profile(RoomType.TEST),
                     None, {})
    assert view["instruments"] == []
    assert view["capability"]["pixel_count"] == 60
    assert view["bound_dev"] is None


def test_empty_manifests_yield_no_instruments():
    _, role, _ = room_role(RoomType.TEST)
    view = room_view(Room(room_type=RoomType.TEST), room_profile(RoomType.TEST),
                     role, {})
    assert view["instruments"] == []


def test_the_view_is_json_serializable():
    import json
    json.dumps(_view())


def test_the_node_id_never_appears_anywhere_in_the_view():
    """Section 3 of the design spec: the Registration Node id stays hidden."""
    import json
    from control.rooms import ROOM_NODE_IDS
    blob = json.dumps(_view())
    assert ROOM_NODE_IDS[RoomType.TEST] not in blob


def test_the_room_role_name_never_appears_in_the_view():
    import json
    from control.rooms import room_role_name
    assert room_role_name(RoomType.TEST) not in json.dumps(_view())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_room_view.py -v`
Expected: FAIL, collection error `ModuleNotFoundError: No module named 'control.room_view'`

- [ ] **Step 3: Write the implementation**

Create `control/room_view.py`:

```python
"""The Room read model the Terrarium Console renders.

Pure dict builders with no engine imports, mirroring console/protocol.py so
this is testable with no GameServer, no renderer and no socket.

Scope is deliberate and load-bearing. The 2026-08-10 room-concept spec's
section 7 said the Room is never surfaced; the 2026-08-17 room-panel spec
narrows that rather than deleting it. What this module may expose is the
Room's instruments, its surface and its live controller values. What it must
never expose is the Room's Registration Node id, its registration counts, or
its role name -- those stay behind the untouched filters in console/agent.py
and control/rooms.py's non_room_counts(). Two tests in tests/test_room_view.py
assert the node id and role name are absent from the serialized blob.
"""

from __future__ import annotations


def _light_instruments(manifest: dict) -> list[dict]:
    out = []
    for decl in manifest.get("instruments", []):
        entry = {"kind": "light",
                 "instrument": decl.get("instrument"),
                 "target": decl.get("target", "primary"),
                 "params": decl.get("params", {}),
                 "lanes": decl.get("lanes", [])}
        out.append(entry)
    return out


def _audio_instruments(manifest: dict) -> list[dict]:
    """Audio declarations carry keys light ones do not (program, drone), and
    ugen_manifest is v0 and provisional, so anything beyond the shared shape
    is copied through rather than enumerated. A Bit that grows a new audio key
    shows it on the panel without this module changing."""
    out = []
    for decl in manifest.get("instruments", []):
        entry = {"kind": "audio",
                 "instrument": decl.get("instrument"),
                 "lanes": decl.get("lanes", [])}
        for key, value in decl.items():
            if key not in ("instrument", "lanes"):
                entry[key] = value
        out.append(entry)
    return out


def capability_view(profile) -> dict:
    """The Room's surface, as the Console draws it.

    `primary` is absent by construction: control/room_profile.py does not
    declare it and harness/room_surface.py appends it only for the renderer.
    It spans the whole surface, so drawing it would cover every real zone.
    """
    return {
        "surface_id": profile.surface_id,
        "pixel_count": profile.pixel_count,
        "color_order": profile.color_order,
        "zones": [{"name": z.name, "start": z.start, "count": z.count}
                  for z in profile.zones],
    }


def room_view(room, profile, role, controllers: dict) -> dict | None:
    """Build the Console's whole Room panel payload.

    Returns None when no Room is configured, which the panel renders as
    "No Room configured". `role` is None when no Bit is loaded, which yields
    the surface with an empty instrument list.

    Light and audio instruments are returned as ONE list discriminated by
    `kind`, not two. They are declared in two fields of one Role and fed from
    one shared MIDI stream, so presenting them apart would hide the property
    the architecture is built around: one controller drives aurora's hue and
    FluidSynth's cutoff together.
    """
    if room is None or profile is None:
        return None
    instruments: list[dict] = []
    if role is not None:
        instruments = (_light_instruments(role.light_manifest or {})
                       + _audio_instruments(role.ugen_manifest or {}))
    return {
        "room_type": room.room_type.name,
        "bound_dev": room.bound_dev,
        "capability": capability_view(profile),
        "instruments": instruments,
        "controllers": dict(controllers),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_view.py -v`
Expected: PASS, 13 passed

- [ ] **Step 5: Commit**

```bash
git add control/room_view.py tests/test_room_view.py
git commit -m "feat(control): the Room read model for the Console

Light and audio instruments in ONE list discriminated by kind, because they
are declared in two fields of one Role and fed from one shared MIDI stream.
Presenting them apart would hide that cc:74 drives aurora's hue and
FluidSynth's cutoff together.

primary is absent from the serialized zones by construction. Tests assert
the Registration Node id and the Room role name never appear in the blob."
```

---

### Task 7: Console protocol and agent expose the Room

**Files:**
- Modify: `console/protocol.py:21-27` (`__all__`), append builders
- Modify: `console/agent.py:79-99` (`snapshot`), `:27-34` (`poll`)
- Modify: `tests/test_console_agent.py`, `tests/test_console_protocol.py`

**Interfaces:**
- Consumes: `control.room_view.room_view()` (Task 6), `control.room_profile.room_profile()` (Task 1), `RoomBridge.controllers` (Task 5).
- Produces: `console.protocol.room_changed_event(room) -> dict`; `snapshot_event(..., room=None)` gains a `room` keyword. `ConsoleAgent(game_server, server, room_bridge=None)` gains a `room_bridge` parameter and a `_current_room()` method.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_console_protocol.py`:

```python
def test_room_changed_event_shape():
    from console import protocol
    event = protocol.room_changed_event({"room_type": "TEST"})
    assert event == {"event": "room_changed", "room": {"room_type": "TEST"}}


def test_snapshot_carries_room():
    from console import protocol
    event = protocol.snapshot_event(
        state="IDLE", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={},
        room={"room_type": "TEST"})
    assert event["room"] == {"room_type": "TEST"}


def test_snapshot_room_defaults_to_none():
    from console import protocol
    event = protocol.snapshot_event(
        state="IDLE", installed_bits=[], loaded_bit=None, roles=[],
        registration=[], devices=[], bit_status={})
    assert event["room"] is None
```

Append to `tests/test_console_agent.py`:

```python
def _room_console(bit_name="TestBit"):
    """A GameServer with a bound TEST Room and a loaded Bit, plus a
    ConsoleAgent wired to a RoomBridge carrying a live cc value.

    TestBit, NOT tests/test_engine.py's RoomCapableBit: that fixture overrides
    role_table and rebuilds the Room role with a bare room_role(RoomType.TEST),
    so its light_manifest and ugen_manifest are both empty. TestBit declares
    the real aurora + flsyn Room instruments (bits/test_bit.py), which is what
    these tests are asserting on."""
    from control.room_bridge import RoomBridge
    binding = RoomBindingRegistry()
    gs = GameServer({bit_name: TestBit}, room_binding=binding)
    gs.room = Room(room_type=RoomType.TEST, bound_dev="sim-room")
    gs.load_bit(bit_name)
    bridge = RoomBridge()
    bridge.bind("sim-room")
    bridge.feed_light(0xB0, 74, 93)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv, room_bridge=bridge)
    return gs, srv, agent


def test_snapshot_carries_the_room_panel():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["room"]["room_type"] == "TEST"
    assert msg["room"]["bound_dev"] == "sim-room"
    assert msg["room"]["capability"]["pixel_count"] == 60
    assert [z["name"] for z in msg["room"]["capability"]["zones"]] == \
        ["left", "center", "right"]


def test_snapshot_room_instruments_include_light_and_audio():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    kinds = [i["kind"] for i in msg["room"]["instruments"]]
    assert "light" in kinds and "audio" in kinds


def test_snapshot_room_carries_live_controller_values():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["room"]["controllers"] == {74: 93}


def test_room_changed_broadcasts_only_when_it_changes():
    gs, srv, agent = _room_console()
    agent.poll()
    srv.broadcasts.clear()
    agent.poll()
    assert [b for b in srv.broadcasts if b["event"] == "room_changed"] == []

    agent._room_bridge.feed_light(0xB0, 74, 12)
    agent.poll()
    changed = [b for b in srv.broadcasts if b["event"] == "room_changed"]
    assert len(changed) == 1
    assert changed[0]["room"]["controllers"] == {74: 12}


def test_no_room_configured_yields_a_null_room():
    gs, srv, agent = _server_with_agent()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["room"] is None


def test_the_room_stays_hidden_from_roles_and_registration_while_visible_as_room():
    """The section 3 regression. BOTH halves in one test, because the whole
    safety argument for amending the 2026-08-10 spec's section 7 is that they
    hold simultaneously. This is the test most likely to catch a future
    accidental widening."""
    import json
    from control.rooms import ROOM_NODE_IDS, room_role_name
    gs, srv, agent = _room_console()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]

    # visible
    assert msg["room"] is not None
    assert msg["room"]["instruments"], "the Room panel must show instruments"

    # hidden
    room_name = room_role_name(RoomType.TEST)
    assert room_name not in [r["role"] for r in msg["roles"]]
    assert room_name not in [r["role"] for r in msg["registration"]]
    for key in ("roles", "registration"):
        assert ROOM_NODE_IDS[RoomType.TEST] not in json.dumps(msg[key])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_protocol.py tests/test_console_agent.py -k room -v`
Expected: FAIL with `AttributeError: module 'console.protocol' has no attribute 'room_changed_event'` and `TypeError: __init__() got an unexpected keyword argument 'room_bridge'`

- [ ] **Step 3: Write the protocol implementation**

In `console/protocol.py`, add `"room_changed_event"` to `__all__`, change `snapshot_event` and append the new builder:

```python
def snapshot_event(*, state, installed_bits, loaded_bit, roles,
                   registration, devices, bit_status, room=None) -> dict:
    return {
        "event": "snapshot",
        "state": state,
        "installed_bits": installed_bits,
        "loaded_bit": loaded_bit,
        "roles": roles,
        "registration": registration,
        "devices": devices,
        "bit_status": bit_status,
        "room": room,
    }


def room_changed_event(room) -> dict:
    """The Room panel's read model. `room` is control.room_view.room_view()'s
    output, or None when no Room is configured."""
    return {"event": "room_changed", "room": room}
```

- [ ] **Step 4: Write the agent implementation**

In `console/agent.py`, add the imports:

```python
from control.room_profile import room_profile
from control.room_view import room_view
from control.rooms import room_role_name
```

Change the constructor:

```python
    def __init__(self, game_server: GameServer, server, room_bridge=None):
        self.game_server = game_server
        self.server = server
        # The Room's live MIDI fan-out, for its controllers read-out. Optional:
        # a GameServer built the pre-Room way has none, and the panel then
        # shows the Room's declarations with no live values rather than
        # failing.
        self._room_bridge = room_bridge
        self._last_status: dict | None = None
        self._last_room: dict | None = None
        game_server.add_observer(self)
```

Add the read-model builder next to `_current_status`:

```python
    def _current_room(self) -> dict | None:
        """Build the Room panel payload, or None when no Room is configured.

        Deliberately scoped: see control/room_view.py's module docstring. The
        Room-hiding filters this class already applies to `roles` and
        `registration` are NOT relaxed by this method; it is a separate view.
        """
        gs = self.game_server
        if gs.room is None:
            return None
        try:
            profile = room_profile(gs.room.room_type)
        except NotImplementedError:
            logger.warning("no room profile for %s; Room panel disabled",
                           gs.room.room_type.name)
            return None
        role = None
        if gs.bit is not None:
            role = gs.bit.role_table.roles.get(room_role_name(gs.room.room_type))
        controllers = getattr(self._room_bridge, "controllers", {}) or {}
        return room_view(gs.room, profile, role, controllers)

    def _broadcast_room_if_changed(self) -> None:
        room = self._current_room()
        if room != self._last_room:
            self._last_room = room
            self.server.broadcast(protocol.room_changed_event(room))
```

In `poll()`, add the call after the existing status broadcast:

```python
        self._broadcast_status_if_changed()
        self._broadcast_room_if_changed()
```

In `snapshot()`, pass the room through and prime the change detector:

```python
        self._last_room = self._current_room()
        return protocol.snapshot_event(
            state=gs.state.name,
            installed_bits=list(gs.bit_registry.keys()),
            loaded_bit=loaded_bit,
            roles=roles,
            registration=registration,
            devices=self._devices_view(),
            bit_status=self._current_status(),
            room=self._last_room,
        )
```

- [ ] **Step 5: Audit FakeConsoleServer against the real one (boundary rule 5)**

The spec requires this before the fake is extended further, and Task 8 extends
it again. `ConsoleServer.broadcast` (`console/server.py:106-117`) drops a client
whose `send` raises; `ConsoleServer.send` (`:98-104`) does the same. The fake
does neither, so a relay that never actually reaches a browser passes its own
tests. Rule 5 was earned expensively on 2026-08-13, when `FakeO2Lite` dispatched
messages the real library only dispatches on a pump, and 611 passing tests
agreed with each other while all disagreeing with reality.

Add the dropping behavior to `FakeConsoleServer` in
`tests/test_console_agent.py`, and a test proving it:

```python
    def __init__(self):
        self.broadcasts = []                # list[dict]
        self.sent = []                      # list[(client, dict)]
        self.dropped = []                   # list[client]
        self._new_clients = []
        self._inbound = []                  # list[(client, dict)]
        self._clients = set()
        self._raising = set()               # clients whose send() raises

    def broadcast(self, msg):
        # Mirrors ConsoleServer.broadcast: a client whose send raises is
        # DROPPED, not retried and not allowed to break the loop. Modelling
        # this is boundary rule 5 -- a double must never be more permissive
        # than the library it stands for.
        self.broadcasts.append(msg)
        for client in list(self._clients):
            if client in self._raising:
                self._clients.discard(client)
                self.dropped.append(client)

    def fail_sends_to(self, client):
        self._clients.add(client)
        self._raising.add(client)
```

and update `connect()` to register the client:

```python
    def connect(self, client):
        self._new_clients.append(client)
        self._clients.add(client)
```

```python
def test_a_dead_console_client_is_dropped_not_retried():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    srv.fail_sends_to("c1")
    agent.poll()
    agent._room_bridge.feed_light(0xB0, 74, 5)
    agent.poll()
    assert srv.dropped == ["c1"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_console_protocol.py tests/test_console_agent.py -v`
Expected: PASS, all tests in both files

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green, and no fewer tests than the previous task.

- [ ] **Step 8: Commit**

```bash
git add console/protocol.py console/agent.py tests/test_console_protocol.py tests/test_console_agent.py
git commit -m "feat(console): expose the Room as a separately scoped read model

Amends the 2026-08-10 spec's section 7 by narrowing it, not deleting it.
The RoleClass.ROOM filter in snapshot() and non_room_counts() are untouched
and uplink/ is not edited; the Room becomes visible only through a new
'room' key carrying its instruments, surface and live controller values.

The section 3 regression test asserts both halves at once: the Room panel
is populated WHILE the Room's role name, its registration counts and its
Registration Node id are absent from every other view."
```

---

### Task 8: Relay the Room's rendered frames to the Console

**Files:**
- Modify: `devicelink/agent.py:74-87` (constructor), `:228-258` (`_render_room`)
- Modify: `console/agent.py` (`poll`, new `on_room_frame`)
- Modify: `console/protocol.py`
- Modify: `tests/test_devicelink_agent.py`, `tests/test_console_agent.py`

**Interfaces:**
- Consumes: Task 4's `_room_profile`, Task 7's `ConsoleAgent`.
- Produces: `DeviceLinkAgent(..., on_room_frame=None)`, a callable `(dev: str, frame: bytes) -> None` invoked once per changed Room frame. `console.protocol.room_frame_event(dev, channels) -> dict`. `ConsoleAgent.on_room_frame(dev, frame)` plus a `clock` constructor parameter defaulting to `time.monotonic` and `ROOM_FRAME_INTERVAL = 0.1`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_devicelink_agent.py`:

```python
def test_room_frames_reach_the_sink():
    gs = _room_ready_game_server()
    seen = []
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            on_room_frame=lambda dev, frame: seen.append((dev, frame)))

    for _ in range(3):
        agent.poll()

    assert seen, "no room frame reached the sink"
    assert seen[0][0] == "sim-room"
    assert len(seen[0][1]) == 180


def test_a_raising_room_frame_sink_does_not_stop_the_leds_going_out():
    """Boundary rule 2, and the same guard the other two transport sinks
    already carry: a failing console must not wedge the Room."""
    gs = _room_ready_game_server()
    server = FakeServer()
    server.bind_dev("sim-room", "c-room")

    def boom(dev, frame):
        raise RuntimeError("console exploded")

    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge(),
                            on_room_frame=boom)

    for _ in range(3):
        agent.poll()

    assert [m for _, m in server.sent if m["address"] == "/sim-room/leds"]


def test_no_sink_is_the_default_and_changes_nothing():
    gs = _room_ready_game_server()
    server = FakeServer()
    server.bind_dev("sim-room", "c-room")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())

    for _ in range(3):
        agent.poll()

    assert [m for _, m in server.sent if m["address"] == "/sim-room/leds"]
```

Append to `tests/test_console_agent.py`:

```python
class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def test_room_frames_are_broadcast_at_the_decimated_rate():
    from console.agent import ROOM_FRAME_INTERVAL
    gs, srv, agent = _room_console()
    clock = FakeClock(100.0)
    agent._clock = clock

    agent.on_room_frame("sim-room", bytes(range(180)))
    agent.poll()
    frames = [b for b in srv.broadcasts if b["event"] == "room_frame"]
    assert len(frames) == 1
    assert frames[0]["dev"] == "sim-room"
    assert frames[0]["channels"] == list(range(180))

    # Too soon: dropped, not queued.
    agent.on_room_frame("sim-room", bytes(180))
    clock.now += ROOM_FRAME_INTERVAL / 2
    agent.poll()
    assert len([b for b in srv.broadcasts if b["event"] == "room_frame"]) == 1

    # Interval elapsed: the LATEST frame goes, the skipped one is gone.
    clock.now += ROOM_FRAME_INTERVAL
    agent.on_room_frame("sim-room", bytes([7] * 180))
    agent.poll()
    frames = [b for b in srv.broadcasts if b["event"] == "room_frame"]
    assert len(frames) == 2
    assert frames[1]["channels"] == [7] * 180


def test_no_frame_received_broadcasts_nothing():
    gs, srv, agent = _room_console()
    agent._clock = FakeClock(100.0)
    agent.poll()
    assert [b for b in srv.broadcasts if b["event"] == "room_frame"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_console_agent.py -k "room_frame or sink" -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'on_room_frame'` and `AttributeError: 'ConsoleAgent' object has no attribute 'on_room_frame'`

- [ ] **Step 3: Write the devicelink implementation**

In `devicelink/agent.py`, extend the constructor signature:

```python
    def __init__(self, game_server: GameServer, server,
                 capability=None, clock=time.monotonic,
                 room_bridge=None, room_audio=None, horizon: float = 0.0,
                 room_profile=None, on_room_frame=None):
```

Store it alongside the other Room attributes:

```python
        # Display-only copy of each changed Room frame, for the Terrarium
        # Console. Optional and best-effort: None is the default, so a run
        # without a console constructs and behaves exactly as before.
        #
        # Boundary rule 2 permits this. The rule forbids the console carrying
        # per-device join/tick traffic and requires that gameplay correctness
        # never depend on the link's health. Nothing here is retransmitted,
        # nothing is awaited, and dropping every frame degrades the picture
        # and changes nothing else.
        self._on_room_frame = on_room_frame
```

Add the guarded emitter next to `_render_room`:

```python
    def _emit_room_frame(self, dev: str, frame: bytes) -> None:
        """Guarded exactly like on_release and on_light_cue already are: a
        failing console must not stop the Room rendering or wedge the tick."""
        if self._on_room_frame is None:
            return
        try:
            self._on_room_frame(dev, frame)
        except Exception:
            logger.exception("room frame sink failed; dropping frame")
```

In `_render_room`, call it inside the existing change branch, after `_last_frames` is updated and before the send:

```python
        if frame != self._last_frames.get(self._room_dev):
            self._last_frames[self._room_dev] = frame
            self._emit_room_frame(self._room_dev, frame)
            when = at if at is not None else self._clock() + self._horizon
            try:
                self._send(self._room_dev,
                           protocol.leds_event(self._room_dev, frame,
                                               when=when))
            except Exception:
                logger.exception("Room leds send failed")
```

- [ ] **Step 4: Write the console implementation**

In `console/protocol.py`, add `"room_frame_event"` to `__all__` and append:

```python
def room_frame_event(dev: str, channels) -> dict:
    """One rendered Room frame, for display only. Decimated and droppable:
    see console/agent.py's ROOM_FRAME_INTERVAL. An int list rather than base64
    for consistency with devicelink/protocol.py's leds_event."""
    return {"event": "room_frame", "dev": dev, "channels": list(channels)}
```

In `console/agent.py`, add `import time` and the module constant:

```python
# How often a Room frame may be broadcast. The Room renders at 44 Hz; the
# Console is a monitor, so it gets roughly 10 Hz and intermediate frames are
# DROPPED rather than queued. Boundary rule 2: nothing here may become
# something gameplay waits on.
ROOM_FRAME_INTERVAL = 0.1
```

Extend the constructor:

```python
    def __init__(self, game_server: GameServer, server, room_bridge=None,
                 clock=time.monotonic):
        ...
        self._clock = clock
        self._pending_room_frame: tuple[str, bytes] | None = None
        self._last_room_frame_at = 0.0
```

Add the sink and the broadcaster:

```python
    def on_room_frame(self, dev: str, frame: bytes) -> None:
        """DeviceLinkAgent's display-only frame sink. Called on the tick
        thread. Stores the LATEST frame only; anything not yet broadcast is
        overwritten, never queued."""
        self._pending_room_frame = (dev, frame)

    def _broadcast_room_frame(self) -> None:
        if self._pending_room_frame is None:
            return
        now = self._clock()
        if now - self._last_room_frame_at < ROOM_FRAME_INTERVAL:
            return
        dev, frame = self._pending_room_frame
        self._pending_room_frame = None
        self._last_room_frame_at = now
        self.server.broadcast(protocol.room_frame_event(dev, frame))
```

In `poll()`, add the call last:

```python
        self._broadcast_status_if_changed()
        self._broadcast_room_if_changed()
        self._broadcast_room_frame()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_console_agent.py tests/test_console_protocol.py -v`
Expected: PASS, all tests in all three files

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green, and no fewer tests than the previous task (roughly 812).

- [ ] **Step 7: Commit**

```bash
git add devicelink/agent.py console/agent.py console/protocol.py tests/
git commit -m "feat: relay the Room's rendered frames to the Console

DeviceLinkAgent gains an optional on_room_frame sink, guarded at the call
site exactly as on_release and on_light_cue already are, so a failing
console cannot stop the Room rendering. Wired by constructor injection, so
control/engine.py is untouched.

ConsoleAgent holds only the LATEST frame and broadcasts at ~10 Hz against
the 44 Hz render rate; intermediate frames are dropped, never queued.
Boundary rule 2 holds: nothing is retransmitted, nothing is awaited, and
dropping every frame degrades the picture and nothing else."
```

---

### Task 9: Serve the console as a static directory

**Files:**
- Modify: `console/server.py:21-34` (asset loading), `:56-63` (`_process_request`)
- Modify: `console/static/index.html`
- Create: `console/static/style.css`, `console/static/console.js`
- Modify: `tests/test_console_static.py`, `tests/test_console_server.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ConsoleServer` serves any file in `console/static/` by path with a correct `Content-Type`, and `/` still serves `index.html`. Unknown paths return 404.

- [ ] **Step 1: Write the failing test**

Replace the contents of `tests/test_console_static.py`:

```python
"""The console's static assets. Split across files as of the Room panel
slice, still with NO build step: a venue box must never need npm."""

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "console" / "static"


def _all_assets() -> str:
    return "\n".join(p.read_text() for p in sorted(STATIC.glob("*"))
                     if p.suffix in (".html", ".js", ".css"))


def test_no_external_asset_fetches_anywhere():
    """Global Constraint: self-contained. A venue box may have no internet."""
    for needle in ("http://", "https://", "//cdn", "src=\"//"):
        assert needle not in _all_assets(), f"external reference found: {needle}"


def test_the_expected_files_exist():
    names = {p.name for p in STATIC.glob("*")}
    assert {"index.html", "style.css", "console.js"} <= names


def test_index_references_its_split_assets():
    html = (STATIC / "index.html").read_text()
    assert "style.css" in html
    assert "console.js" in html


def test_the_lifecycle_controls_survived_the_split():
    assets = _all_assets()
    assert "new WebSocket" in assets
    assert "/ws" in assets
    assert "load_bit" in assets and "\"run\"" in assets and "abort" in assets
    assert "snapshot" in assets
```

Append to `tests/test_console_server.py`:

```python
def test_root_serves_index_html():
    import urllib.request
    from console.server import ConsoleServer
    server = ConsoleServer(port=0)
    server.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/") as r:
            body = r.read().decode()
            assert r.headers["Content-Type"].startswith("text/html")
        assert "Terrarium Console" in body
    finally:
        server.stop()


def test_css_and_js_are_served_with_their_own_content_types():
    import urllib.request
    from console.server import ConsoleServer
    server = ConsoleServer(port=0)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        with urllib.request.urlopen(f"{base}/style.css") as r:
            assert r.headers["Content-Type"].startswith("text/css")
        with urllib.request.urlopen(f"{base}/console.js") as r:
            assert r.headers["Content-Type"].startswith("text/javascript")
    finally:
        server.stop()


def test_an_unknown_path_is_a_404():
    import urllib.error
    import urllib.request
    from console.server import ConsoleServer
    server = ConsoleServer(port=0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/nope.js"
        try:
            urllib.request.urlopen(url)
            assert False, "expected a 404"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.stop()


def test_a_traversal_attempt_is_refused():
    """The console is trusted-LAN and unauthenticated, so path handling must
    not be the thing that widens that."""
    import urllib.error
    import urllib.request
    from console.server import ConsoleServer
    server = ConsoleServer(port=0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/../agent.py"
        try:
            urllib.request.urlopen(url)
            assert False, "expected a refusal"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_static.py tests/test_console_server.py -v`
Expected: FAIL, `assert {'console.js', 'style.css'} <= {'index.html'}` and 404s for the css/js paths

- [ ] **Step 3: Rewrite the server's asset handling**

In `console/server.py`, replace the module-level `_INDEX_HTML` line with:

```python
_STATIC_DIR = (Path(__file__).resolve().parent / "static")

# Only these extensions are servable. An allowlist rather than mimetypes.guess
# because this server is unauthenticated on a trusted LAN: the set of things it
# can hand out should be readable in one line.
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
```

Replace `self._index_bytes = _INDEX_HTML.read_bytes()` in `__init__` with:

```python
        # Read once at construction, like the single index.html always was: the
        # console is a fixed asset set, and re-reading per request would put
        # filesystem access on a request path for no benefit.
        self._assets: dict[str, tuple[bytes, str]] = {}
        for path in sorted(_STATIC_DIR.iterdir()):
            content_type = _CONTENT_TYPES.get(path.suffix)
            if path.is_file() and content_type is not None:
                self._assets[path.name] = (path.read_bytes(), content_type)
```

Replace `_process_request`:

```python
    def _process_request(self, connection, request):
        if request.path == "/ws":
            return None   # proceed to the websocket handshake
        # basename only: never join the request path onto a directory, so a
        # traversal attempt resolves to a name that is simply not in _assets.
        name = "index.html" if request.path == "/" \
            else request.path.lstrip("/").split("/")[-1]
        asset = self._assets.get(name)
        if asset is None:
            headers = Headers()
            headers["Content-Length"] = "0"
            return Response(404, "Not Found", headers, b"")
        body, content_type = asset
        headers = Headers()
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
        return Response(200, "OK", headers, body)
```

- [ ] **Step 4: Split the static files**

Create `console/static/style.css` with the contents of the current `<style>` block, plus the Room panel rules Task 10 will use:

```css
body { font: 14px/1.4 system-ui, sans-serif; margin: 1rem; color: #111; }
h1 { font-size: 1.2rem; } h2 { font-size: 1rem; margin: 1rem 0 .3rem; }
#state { font-weight: 600; }
button { margin-right: .5rem; padding: .3rem .7rem; }
table { border-collapse: collapse; margin-top: .3rem; }
th, td { border: 1px solid #ccc; padding: .2rem .5rem; text-align: left; }
#log { height: 8rem; overflow: auto; background: #f6f6f6; padding: .3rem;
       font-family: ui-monospace, monospace; white-space: pre-wrap; }
.conn-down { color: #a00; }

/* Room panel */
#roomStrip { display: flex; gap: 1px; margin: .4rem 0 .1rem; height: 2.2rem; }
#roomStrip div { flex: 1 1 0; background: #000; }
#roomZones { display: flex; gap: 1px; font-size: .75rem; color: #555; }
#roomZones span { text-align: center; border-top: 2px solid #999;
                  padding-top: .15rem; }
.cards { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .5rem; }
.card { border: 1px solid #ccc; border-radius: 4px; padding: .5rem .7rem;
        min-width: 15rem; }
.card h3 { font-size: .95rem; margin: 0 0 .3rem; }
.card .kind { font-size: .7rem; text-transform: uppercase; letter-spacing: .05em;
              color: #fff; background: #567; border-radius: 3px;
              padding: .05rem .35rem; margin-right: .4rem; }
.card .kind.audio { background: #756; }
.card dl { display: grid; grid-template-columns: auto 1fr; gap: .1rem .5rem;
           margin: .3rem 0 0; font-size: .85rem; }
.card dt { color: #666; } .card dd { margin: 0; font-family: ui-monospace, monospace; }
.muted { color: #888; }
```

Create `console/static/console.js` with the current inline `<script>` contents, unchanged except that `handle()` gains two new cases delegating to Task 10's `room.js`:

```javascript
const $ = (id) => document.getElementById(id);
let ws;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { $("conn").textContent = "(connected)"; $("conn").className = ""; };
  ws.onclose = () => {
    $("conn").textContent = "(disconnected — retrying)";
    $("conn").className = "conn-down";
    setTimeout(connect, 1000);
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}

function send(command, extra) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(Object.assign({ command }, extra || {})));
  }
}

$("loadBtn").onclick = () => send("load_bit", { name: $("bitPicker").value });
$("runBtn").onclick = () => send("run");
$("abortBtn").onclick = () => send("abort");

function rows(tbodySel, data, cells) {
  const tbody = document.querySelector(tbodySel + " tbody");
  tbody.innerHTML = "";
  for (const item of data) {
    const tr = document.createElement("tr");
    for (const c of cells(item)) {
      const td = document.createElement("td");
      td.textContent = c;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function renderRegistration(reg) {
  rows("#registration", reg, (r) => [r.role, r.count, r.capacity ?? "∞"]);
}
function renderRoles(roles) {
  rows("#roles", roles, (r) => [r.role, r.class, r.capacity ?? "∞", r.scored,
    JSON.stringify(r.ugen_manifest), JSON.stringify(r.light_manifest)]);
}
function renderDevices(devs) {
  rows("#devices", devs, (d) => [d.dev, d.name, d.role ?? "—"]);
}
function renderStatus(status) {
  rows("#bitStatus", Object.entries(status || {}), (kv) => [kv[0], kv[1]]);
}
function populateBits(bits) {
  const sel = $("bitPicker");
  sel.innerHTML = "";
  for (const b of bits) {
    const opt = document.createElement("option");
    opt.value = b; opt.textContent = b;
    sel.appendChild(opt);
  }
}
function log(level, message) {
  const el = $("log");
  el.textContent += `[${level}] ${message}\n`;
  el.scrollTop = el.scrollHeight;
}

function handle(msg) {
  switch (msg.event) {
    case "snapshot":
      $("state").textContent = msg.state;
      $("loaded").textContent = msg.loaded_bit ?? "—";
      populateBits(msg.installed_bits);
      renderRegistration(msg.registration);
      renderRoles(msg.roles);
      renderDevices(msg.devices);
      renderStatus(msg.bit_status);
      renderRoom(msg.room);
      break;
    case "state_changed":
      $("state").textContent = msg.state;
      log("info", "state → " + msg.state);
      break;
    case "registration_changed": renderRegistration(msg.roles); break;
    case "devices_changed": renderDevices(msg.devices); break;
    case "bit_status": renderStatus(msg.status); break;
    case "room_changed": renderRoom(msg.room); break;
    case "room_frame": renderRoomFrame(msg.channels); break;
    case "bit_completed": log("info", "bit completed: " + JSON.stringify(msg.result)); break;
    case "error": log("error", msg.command + ": " + msg.message); break;
    case "log": log(msg.level, msg.message); break;
  }
}

connect();
```

Rewrite `console/static/index.html` to reference them, keeping every existing element id, and adding the Room panel container Task 10 fills:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Terrarium Console</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<h1>Terrarium Console <span id="conn" class="conn-down">(connecting…)</span></h1>

<p>State: <span id="state">—</span> &nbsp; Loaded Bit: <span id="loaded">—</span></p>

<h2>Controls</h2>
<div>
  <select id="bitPicker"></select>
  <button id="loadBtn">Load Bit</button>
  <button id="runBtn">Run</button>
  <button id="abortBtn">Abort</button>
</div>

<h2>Room</h2>
<div id="room"></div>

<h2>Registration</h2>
<table id="registration"><thead><tr><th>Role</th><th>Count</th><th>Capacity</th></tr></thead><tbody></tbody></table>

<h2>Roles &amp; media manifests</h2>
<table id="roles"><thead><tr><th>Role</th><th>Class</th><th>Cap</th><th>Scored</th><th>ugen_manifest</th><th>light_manifest</th></tr></thead><tbody></tbody></table>

<h2>Devices</h2>
<table id="devices"><thead><tr><th>Device</th><th>Name</th><th>Role</th></tr></thead><tbody></tbody></table>

<h2>Bit status</h2>
<table id="bitStatus"><tbody></tbody></table>

<h2>Event log</h2>
<div id="log"></div>

<script src="room.js"></script>
<script src="console.js"></script>
</body>
</html>
```

Note: `room.js` loads before `console.js` because `console.js` calls `renderRoom` and `renderRoomFrame` at connect time. Create a placeholder `console/static/room.js` now so this task's tests pass; Task 10 fills it in:

```javascript
// Room panel rendering. Filled in by Task 10.
function renderRoom(room) {}
function renderRoomFrame(channels) {}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_console_static.py tests/test_console_server.py -v`
Expected: PASS, all tests in both files

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green, and no fewer tests than the previous task (roughly 819).

- [ ] **Step 7: Commit**

```bash
git add console/server.py console/static/ tests/test_console_static.py tests/test_console_server.py
git commit -m "refactor(console): serve static/ as a directory, no build step

One 141-line file with an inline style block and inline script does not
hold a zone canvas and instrument cards. Split into index.html, style.css,
console.js and room.js, served by the same port with correct content types.

Still no npm and still no external fetches: a venue box may have no
internet. Path handling takes the basename only, so a traversal attempt
resolves to a name that is not in the asset map, and unknown paths 404."
```

---

### Task 10: The Room panel

**Files:**
- Modify: `console/static/room.js`
- Modify: `tests/test_console_static.py`

**Interfaces:**
- Consumes: `room_changed` and `room_frame` events from Tasks 7 and 8, and the `#room` container plus the CSS classes from Task 9.
- Produces: `renderRoom(room)` and `renderRoomFrame(channels)` global functions.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_console_static.py`:

```python
def test_room_panel_renders_zones_instruments_and_live_values():
    room_js = (STATIC / "room.js").read_text()
    # zone view driven by the capability
    assert "capability" in room_js and "zones" in room_js
    # the frame relay
    assert "roomStrip" in room_js
    # instrument cards, both kinds, with live controller values
    assert "instruments" in room_js and "controllers" in room_js
    assert "lanes" in room_js
    # the empty state
    assert "No Room configured" in room_js


def test_room_panel_decodes_grb_not_rgb():
    """The wire is GRB (control/room_profile.py's color_order), so a naive
    rgb(c[0], c[1], c[2]) would render every zone the wrong colour."""
    assert "GRB" in (STATIC / "room.js").read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_static.py -k room_panel -v`
Expected: FAIL, `assert 'capability' in '// Room panel rendering. Filled in by Task 10....'`

- [ ] **Step 3: Write the implementation**

Replace `console/static/room.js`:

```javascript
// Room panel: a labelled zone view of the Room's live light, plus one card
// per declared instrument showing its target zone, its lanes and each lane's
// current controller value.
//
// The Room's declared light and audio instruments arrive in ONE list
// discriminated by `kind` (see control/room_view.py). They are rendered
// together on purpose: cc:74 drives aurora's hue and FluidSynth's cutoff from
// one shared MIDI stream, and two separate tables would hide that.

let roomCapability = null;

function renderRoom(room) {
  const el = document.getElementById("room");
  el.innerHTML = "";
  if (!room) {
    roomCapability = null;
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No Room configured";
    el.appendChild(p);
    return;
  }
  roomCapability = room.capability;

  const header = document.createElement("p");
  header.textContent = `${room.room_type} · ${room.capability.pixel_count} px · `
    + `${room.capability.color_order} · `
    + (room.bound_dev ? `bound to ${room.bound_dev}` : "not bound");
  el.appendChild(header);

  el.appendChild(buildStrip(room.capability));
  el.appendChild(buildZoneLabels(room.capability));

  const cards = document.createElement("div");
  cards.className = "cards";
  for (const inst of room.instruments) {
    cards.appendChild(buildCard(inst, room.controllers || {}));
  }
  if (room.instruments.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No instruments declared (no Bit loaded).";
    cards.appendChild(p);
  }
  el.appendChild(cards);
}

function buildStrip(capability) {
  const strip = document.createElement("div");
  strip.id = "roomStrip";
  for (let i = 0; i < capability.pixel_count; i++) {
    strip.appendChild(document.createElement("div"));
  }
  return strip;
}

function buildZoneLabels(capability) {
  const bar = document.createElement("div");
  bar.id = "roomZones";
  for (const zone of capability.zones) {
    const span = document.createElement("span");
    span.style.flex = `${zone.count} 1 0`;
    span.textContent = `${zone.name} (${zone.start}..${zone.start + zone.count - 1})`;
    bar.appendChild(span);
  }
  return bar;
}

function buildCard(inst, controllers) {
  const card = document.createElement("div");
  card.className = "card";

  const title = document.createElement("h3");
  const badge = document.createElement("span");
  badge.className = "kind" + (inst.kind === "audio" ? " audio" : "");
  badge.textContent = inst.kind;
  title.appendChild(badge);
  title.appendChild(document.createTextNode(inst.instrument));
  card.appendChild(title);

  const dl = document.createElement("dl");
  if (inst.kind === "light") {
    addRow(dl, "target", inst.target);
  }
  if (inst.program !== undefined) addRow(dl, "program", inst.program);
  if (inst.drone !== undefined) addRow(dl, "drone", JSON.stringify(inst.drone));
  if (inst.params && Object.keys(inst.params).length) {
    addRow(dl, "params", JSON.stringify(inst.params));
  }
  for (const lane of inst.lanes || []) {
    const cc = lane.source.startsWith("cc:") ? lane.source.slice(3) : null;
    const live = cc !== null && controllers[cc] !== undefined
      ? ` = ${controllers[cc]}` : "";
    addRow(dl, lane.source, `→ ${lane.dest}${live}`);
  }
  card.appendChild(dl);
  return card;
}

function addRow(dl, term, value) {
  const dt = document.createElement("dt");
  dt.textContent = term;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.appendChild(dt);
  dl.appendChild(dd);
}

function renderRoomFrame(channels) {
  const strip = document.getElementById("roomStrip");
  if (!strip || !roomCapability) return;
  const swatches = strip.children;
  // The wire is GRB, not RGB: control/room_profile.py declares color_order and
  // devicelink ships the channels in that order. Reading them as RGB would
  // render every zone the wrong colour, which is the kind of bug that looks
  // like a lighting design decision.
  for (let i = 0; i < swatches.length; i++) {
    const g = channels[i * 3] || 0;
    const r = channels[i * 3 + 1] || 0;
    const b = channels[i * 3 + 2] || 0;
    swatches[i].style.background = `rgb(${r},${g},${b})`;
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_console_static.py -v`
Expected: PASS, all tests in the file

- [ ] **Step 5: Commit**

```bash
git add console/static/room.js tests/test_console_static.py
git commit -m "feat(console): the Room panel

A labelled zone view of the Room's live light, plus one card per declared
instrument showing its target zone, its lanes and each lane's live
controller value.

Light and audio cards render together, because cc:74 drives aurora's hue
and FluidSynth's cutoff from one shared stream and two tables would hide
that. Frames are decoded GRB per the Room's declared color_order; reading
them as RGB would render every zone the wrong colour."
```

---

### Task 11: The simulator renders the Room's surface

**Files:**
- Modify: `harness/room_simulator.py:44-60` (`build`), `:63-90` (`main`)
- Modify: `harness/o2_shroom.py` (`build` and its argument parser)
- Modify: `harness/terrarium_boot.py:50-59` and `:74-93` (both simulator factories)
- Modify: `tests/test_room_simulator.py`, `tests/test_o2_shroom.py`

**Interfaces:**
- Consumes: `control.room_profile.room_profile()` (Task 1), `harness.room_surface.to_capability()` (Task 2), `ShroomClient(..., expected_channels=...)` (Task 3).
- Produces: `harness.room_simulator.build(dev, sim_host, sim_port, serve, room_type="TEST")`; a `--room-type` CLI argument on both `harness/room_simulator.py` and `harness/o2_shroom.py`, defaulting to `TEST`. On `o2_shroom` it applies only when `--no-join` is passed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_room_simulator.py`:

```python
def test_build_uses_the_room_surface_not_the_shroom():
    pytest.importorskip("luxaeterna")
    from harness.room_simulator import build
    client, backend = build("sim-room", serve=False)
    assert backend.capability.surface_id == "room_test"
    assert backend.capability.pixel_count == 60


def test_build_widens_the_client_to_the_room_frame():
    pytest.importorskip("luxaeterna")
    from harness.room_simulator import build
    client, backend = build("sim-room", serve=False)
    assert client.expected_channels == 180


def test_clear_sends_a_room_width_all_zero_frame():
    from harness.room_simulator import WebSimLeds
    backend = FakeBackend()
    WebSimLeds(backend, channels=180).clear()
    assert backend.sent == [bytes(180)]
```

Note the third test changes `WebSimLeds`'s constructor, so update the two existing tests in this file that construct it, passing `channels=36` explicitly:

```python
def test_show_forwards_the_frame_to_the_backend():
    backend = FakeBackend()
    leds = WebSimLeds(backend, channels=36)

    leds.show(bytes(range(36)))

    assert backend.sent == [bytes(range(36))]


def test_clear_sends_an_all_zero_frame():
    backend = FakeBackend()
    leds = WebSimLeds(backend, channels=36)

    leds.clear()

    assert backend.sent == [bytes(36)]
```

Append to `tests/test_o2_shroom.py`:

```python
def test_no_join_build_uses_the_room_surface():
    pytest.importorskip("luxaeterna")
    from harness.o2_shroom import build
    client, backend = build("sim-room", serve=False, room_type="TEST")
    assert backend.capability.surface_id == "room_test"
    assert client.expected_channels == 180


def test_a_player_build_is_unchanged():
    pytest.importorskip("luxaeterna")
    from harness.o2_shroom import build
    client, backend = build("ie1", serve=False)
    assert backend.capability.pixel_count == 12
    assert client.expected_channels == 36
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_simulator.py tests/test_o2_shroom.py -v`
Expected: FAIL with `AssertionError: assert 'ie0' == 'room_test'` and `TypeError: __init__() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Update `harness/room_simulator.py`**

Replace `WebSimLeds` and `build`:

```python
class WebSimLeds:
    """Adapts ShroomClient's leds.show(bytes)/leds.clear() to
    WebSimBackend's send(frame).

    `channels` is the frame width this surface expects. It is a parameter
    rather than the LED_CHANNELS constant because a Room is not a Tuneshroom:
    the Room's width comes from its RoomProfile (60 px x 3 = 180), while a
    player device is still 12 px x GRB = 36.
    """

    def __init__(self, backend, channels: int) -> None:
        self._backend = backend
        self._channels = channels

    def show(self, frame: bytes) -> None:
        self._backend.send(frame)

    def clear(self) -> None:
        self._backend.send(bytes(self._channels))


def build(dev: str, sim_host: str = "127.0.0.1", sim_port: int = 0,
          serve: bool = True, room_type: str = "TEST"):
    """Construct the client + backend WITHOUT opening a socket or serving.

    Returns ``(client, backend)``. ``serve=False`` gives a record-only
    backend (no websockets, no port) for headless tests, matching
    ``led_smoke.py``'s ``build()``/``main()`` split -- the caller is
    responsible for ``backend.open()``/``.close()`` and the real
    devicelink websocket loop.

    The surface is the ROOM's, not a Tuneshroom's: this process renders a
    Room, and borrowing shroom_capability() here is what made a Room a 12-LED
    ring and stem. See control/room_profile.py.
    """
    from luxaeterna.backends.websim import WebSimBackend

    from control.room_profile import room_profile
    from control.rooms import RoomType
    from harness.room_surface import to_capability

    profile = room_profile(RoomType[room_type])
    backend = WebSimBackend(capability=to_capability(profile),
                             host=sim_host, port=sim_port, serve=serve,
                             label=dev)
    client = ShroomClient(dev, node="", leds=WebSimLeds(backend,
                                                       profile.channel_count),
                          expected_channels=profile.channel_count)
    return client, backend
```

In `main()`, add the argument and pass it:

```python
    parser.add_argument("--room-type", default="TEST",
                        help="Which RoomType's surface to render. Resolved "
                             "through control/room_profile.py, so the "
                             "simulator and Control agree on the shape by "
                             "construction rather than by convention.")
```

```python
    client, backend = build(args.dev, args.sim_host, args.sim_port,
                            room_type=args.room_type)
```

Also drop the now-unused `LED_CHANNELS` from this module's import line, leaving `from harness.shroom_client import ShroomClient, pump_tick`.

- [ ] **Step 4: Update `harness/o2_shroom.py`**

Its `build()` also constructs `WebSimLeds(backend)`, which now needs a width, so
this file changes whether or not the Room path is used. Replace `build()`
(currently `harness/o2_shroom.py:127-145`):

```python
def build(dev: str, node: str = "TEST_PLAYER_NODE",
          sim_host: str = "127.0.0.1", sim_port: int = 0,
          serve: bool = True, room_type: str | None = None):
    """Construct the client and its LED backend WITHOUT opening a socket.

    Returns (client, backend). serve=False gives a record-only backend for
    headless tests, matching led_smoke.py's and room_simulator.py's
    build()/main() split.

    room_type, when given, renders that ROOM's surface instead of a
    Tuneshroom's. That is the --no-join path, where this module stands in for
    harness/room_simulator.py on the o2lite transport. A Room is not a
    Tuneshroom, so it must not be drawn as a 12-LED ring and stem.
    """
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import shroom_capability

    from harness.room_simulator import WebSimLeds

    if room_type is None:
        capability = shroom_capability()
        channels = LED_CHANNELS
    else:
        from control.room_profile import room_profile
        from control.rooms import RoomType
        from harness.room_surface import to_capability

        profile = room_profile(RoomType[room_type])
        capability = to_capability(profile)
        channels = profile.channel_count

    backend = WebSimBackend(capability=capability,
                            host=sim_host, port=sim_port, serve=serve,
                            label=dev)
    client = ShroomClient(dev, node, leds=WebSimLeds(backend, channels),
                          expected_channels=channels)
    return client, backend
```

Confirm `LED_CHANNELS` is imported in this module; if it is not, add it to the
existing `from harness.shroom_client import ...` line.

Add the CLI argument:

```python
    parser.add_argument("--room-type", default=None,
                        help="Render this RoomType's surface instead of a "
                             "Tuneshroom's. Only meaningful with --no-join, "
                             "which is how this module serves as the Room "
                             "simulator on the o2lite path.")
```

and pass `room_type=args.room_type` into `build()`.

- [ ] **Step 5: Update both simulator factories in `harness/terrarium_boot.py`**

In `_SimulatorFactory.__call__`, append to `command`:

```python
        command += ["--room-type", "TEST"]
```

In `_O2SimulatorFactory.__call__`, append to the argument list:

```python
             "--room-type", "TEST",
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_simulator.py tests/test_o2_shroom.py tests/test_terrarium_boot.py -v`
Expected: PASS, all tests in all three files

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green, and no fewer tests than the previous task (roughly 826).

- [ ] **Step 8: Commit**

```bash
git add harness/room_simulator.py harness/o2_shroom.py harness/terrarium_boot.py tests/
git commit -m "feat(harness): the Room simulator renders the Room's surface

Both simulator paths built WebSimBackend from shroom_capability(), so the
browser canvas was a 12-LED ring and stem no matter what the Room declared.
Both now resolve the RoomProfile, which also widens their ShroomClient to
the Room's 180-channel frame.

The simulator learns its shape by --room-type rather than over the wire:
Control spawns it and already knows, and the Room path has no join and so
receives no /ie<N>/role blob. Wire delivery belongs to the deferred
real-hardware Room backend."
```

---

### Task 12: Wire the Console into the drivers

**Files:**
- Modify: `devicelink/agent.py` (add a `room_bridge` property)
- Modify: `harness/terrarium_boot.py` (argument parser, `main()`, `_wait_in_setup`, `_serve_until_done`)
- Modify: `harness/run_stack.py:61-79` (`StackConfig`), `:90-108` (`control_command`), argument parser
- Modify: `tests/test_terrarium_boot.py`, `tests/test_run_stack.py`

**`build()`'s signature and 5-tuple return are deliberately NOT changed.** It is
unpacked at 15 sites in `tests/test_terrarium_boot.py` (lines 43, 60, 76, 113,
158, 224, 243, 261, 279, 295, 314, 328, 372, 411, and inside
`_build_with_fakes`) plus `harness/terrarium_boot.py:488`. Adding a sixth
element would churn 16 lines for no gain. `main()` owns the console instead,
which is also more correct on ordering: the console is a monitor shell whose
only clients are browsers, outside the stack entirely, so registering it after
`build()` returns (and therefore tearing it down FIRST) is right. The devicelink
server has to be last because the Room simulator is its client; the console has
no such dependent.

**Interfaces:**
- Consumes: `ConsoleAgent(gs, server, room_bridge=None, clock=time.monotonic)` (Tasks 7 and 8), `DeviceLinkAgent._on_room_frame` (Task 8).
- Produces: `DeviceLinkAgent.room_bridge` read-only property; `--console-port` on both `terrarium_boot` and `run_stack`; `StackConfig.console_port: int | None = None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_terrarium_boot.py`:

```python
def test_agent_exposes_its_room_bridge():
    """main() reaches the bridge through the agent, since build() does not
    return it and its signature is deliberately unchanged."""
    from control.room_bridge import RoomBridge
    config = _default_config()          # the file's existing config helper
    gs, server, agent, arco, teardown = _build_with_fakes(config)
    try:
        assert isinstance(agent.room_bridge, RoomBridge)
    finally:
        teardown.close()


def test_console_is_off_by_default():
    """Every existing invocation must be byte-identical."""
    config = _default_config()
    gs, server, agent, arco, teardown = _build_with_fakes(config)
    try:
        assert agent._on_room_frame is None
    finally:
        teardown.close()
```

Append to `tests/test_run_stack.py`:

```python
def test_control_command_omits_console_port_by_default():
    from harness.run_stack import StackConfig, control_command
    cfg = StackConfig(log_dir="/tmp/x")
    assert "--console-port" not in control_command(cfg, ppid=1)


def test_control_command_passes_console_port_when_set():
    from harness.run_stack import StackConfig, control_command
    cfg = StackConfig(log_dir="/tmp/x", console_port=8772)
    cmd = control_command(cfg, ppid=1)
    assert "--console-port" in cmd
    assert cmd[cmd.index("--console-port") + 1] == "8772"


def test_console_port_defaults_to_none():
    from harness.run_stack import StackConfig
    assert StackConfig(log_dir="/tmp/x").console_port is None
```

If `_default_config()` does not exist under that name in
`tests/test_terrarium_boot.py`, reuse whatever `BootConfig` the file's existing
tests build (see its `_build_with_fakes` callers) rather than inventing one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py tests/test_run_stack.py -k "console or room_bridge" -v`
Expected: FAIL with `AttributeError: 'DeviceLinkAgent' object has no attribute 'room_bridge'` and `TypeError: StackConfig() got an unexpected keyword argument 'console_port'`

- [ ] **Step 3: Expose the room bridge on the agent**

In `devicelink/agent.py`, next to the existing `clamped` and `closing`
properties:

```python
    @property
    def room_bridge(self):
        """The Room's MIDI fan-out, or None when no Room is configured.

        Public because harness/terrarium_boot.py's main() needs it to build a
        ConsoleAgent: build() does not return it, and build()'s 5-tuple return
        is unpacked at 16 sites, so widening it would be churn for no gain.
        """
        return self._room_bridge
```

- [ ] **Step 4: Add the flag and wire the console in `main()`**

Add the argument to `harness/terrarium_boot.py`'s parser:

```python
    ap.add_argument("--console-port", type=int, default=None,
                    help="Serve the Terrarium Console on this port. Off by "
                         "default, so an existing invocation is unchanged. "
                         "Binds --host, which defaults to 127.0.0.1: the "
                         "console is unauthenticated and trusted-LAN only.")
```

In `main()`, immediately after the existing `build()` call at line 488:

```python
    console_agent = None
    if args.console_port is not None:
        from console.agent import ConsoleAgent
        from console.server import ConsoleServer
        console_server = ConsoleServer(host=args.host, port=args.console_port)
        console_server.start()
        # Registered AFTER build(), so it is torn down FIRST. That is correct
        # here rather than an oversight: the console is a monitor shell whose
        # only clients are browsers, outside this stack entirely. The
        # devicelink server is last because the Room simulator is its client;
        # nothing in the stack is a client of the console.
        teardown.push("console-server", console_server.stop)
        console_agent = ConsoleAgent(gs, console_server,
                                     room_bridge=agent.room_bridge,
                                     clock=clock)
        agent._on_room_frame = console_agent.on_room_frame
        print(f"Terrarium Console: "
              f"http://{args.host}:{console_server.port}/", flush=True)
```

`clock` here is whichever clock `main()` already resolved for `build()`
(`time.monotonic` on the websocket path, `o2lite.time_get` on the o2lite path).
Use that same local, not a fresh `time.monotonic`, for the reason the
`build()` docstring already gives: two clocks disagreeing about "now" is the
bug class this module has been bitten by twice.

- [ ] **Step 5: Poll the console from the same tick loop**

Give `_wait_in_setup` and `_serve_until_done` a `console_agent=None` keyword
parameter, and in each loop body immediately after `agent.poll()`:

```python
        if console_agent is not None:
            console_agent.poll()
```

Pass `console_agent=console_agent` at both call sites in `main()`.

- [ ] **Step 6: Thread the flag through `harness/run_stack.py`**

Add to `StackConfig`, after `arco_ready_timeout`:

```python
    console_port: int | None = None   # None = no Terrarium Console
```

Replace `control_command` so the flag is appended conditionally:

```python
def control_command(cfg: StackConfig, ppid: int) -> list[str]:
    command = [
        sys.executable, "-u", "-m", "harness.terrarium_boot",
        "--transport", "o2lite",
        "--arco-command", cfg.arco_command,
        "--arco-pty",
        "--arco-log", os.path.join(cfg.log_dir, "arco.log"),
        "--arco-settle-seconds", str(cfg.settle_seconds),
        "--arco-ready-timeout", str(cfg.arco_ready_timeout),
        "--setup-seconds", str(cfg.setup_seconds),
        "--horizon", str(cfg.horizon),
        "--hold",
        # Symmetric with the devices' own --exit-with-parent below: a
        # SIGKILLed or OOM-killed run_stack cannot signal this process
        # either, and without this flag terrarium_boot -- and through it
        # Arco and the Room simulator -- would keep running un-signalled
        # in their own session. See F5 in the final review.
        "--exit-with-parent", str(ppid),
    ]
    if cfg.console_port is not None:
        command += ["--console-port", str(cfg.console_port)]
    return command
```

Add the parser argument and thread it into the `StackConfig` construction in
`main()`:

```python
    ap.add_argument("--console-port", type=int, default=None,
                    help="Serve the Terrarium Console on this port and print "
                         "its URL. Off by default.")
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_terrarium_boot.py tests/test_run_stack.py -v`
Expected: PASS, all tests in both files

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green, and no fewer tests than the previous task.

- [ ] **Step 9: Commit**

```bash
git add devicelink/agent.py harness/terrarium_boot.py harness/run_stack.py tests/
git commit -m "feat(harness): serve the Terrarium Console during a real run

ConsoleServer and ConsoleAgent existed only under tests/, so there was no
admin panel to open during a live run at all. --console-port starts both,
polls the agent from the same tick loop, and prints the URL.

build()'s 5-tuple return is deliberately unchanged: it is unpacked at 16
sites, so main() owns the console instead. That also gets the ordering
right, since the console is a monitor shell whose only clients are
browsers and nothing in the stack depends on it.

Off by default, so every existing invocation is unchanged."
```

---

### Task 13: Live verification and documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-08-17-room-panel-and-room-fixtures-design.md` (status line)
- Modify: `README.md` if it lists harness entry points

**Interfaces:**
- Consumes: everything above.
- Produces: a recorded live-run result, or a recorded failure with its cause.

- [ ] **Step 1: Confirm the full suite is green**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 831 passed, 1 skipped, and no fewer than the 764 baseline.

- [ ] **Step 2: Run the stack with the console open**

**RUN ON: MYCOLOGICAL**

```bash
.venv/bin/python -m harness.run_stack --console-port 8772 --devices 0 --seconds 60
```

- [ ] **Step 3: Check the four acceptance points in the browser**

Open the printed Console URL and confirm, **with no device joined**:

1. The Room panel shows 60 swatches with `left`, `center` and `right` labelled beneath them.
2. The swatches animate. `TestBit.cues(at)` drifts the Room's hue on its own, so this needs nobody in the room. This is why the check is written to require no join: device clock sync against a post-reset Arco is measured as intermittent (1 of 3 headless runs), is upstream, and is unfixed.
3. One card per instrument: `aurora` (light, target `primary`, `cc:74 → hue`) and `flsyn` (audio, program 89, `cc:74 → cc:74`), with the live `cc:74` value moving on both.
4. The Roles table contains `player` and `jammer` and **not** `room_test`, and the Registration table shows no Room row.

- [ ] **Step 4: Record what actually happened**

Update the spec's `**Status:**` line with the date, what was confirmed, and anything that failed. Record failures rather than omitting them; the spec is a point-in-time record and a half-verified slice that reads as verified is worse than one that reads as untested.

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs(spec): record the Room panel live-run result"
```

---

## Post-plan follow-ups (not tasks in this plan)

These came out of the Roger Dannenberg correspondence of 2026-08-16 and 2026-08-17 and are tracked so they are not lost. None blocks this plan.

1. **`docs/MM_TERRARIUM.md` needs two status changes at closeout** (a `mm-deepdive-sync` job): o2litepy's missing ensemble filter is fixed upstream (`rbdannenberg/arco` commit `379424e`, merged locally), and the "refused service announcement is unobservable" entry is not a defect per Roger, with `verify_service_ownership`'s round-trip check explicitly sanctioned as the right detection method.
2. **Boundary rule 2 may want a clarifying sentence** permitting display-only, droppable copies, so the argument in this plan's Task 8 does not have to be re-derived by the next reader.
3. **A reply to Roger** correcting his premise that Control's transport and the Room simulator offer the same service by design (they offer `actl,game` and `sim-room`; the collision was an orphan re-claiming a name on reconnect), and reporting the clock-sync-after-`/host/clear` failure, which has never been sent upstream and is the actual blocker on automated live verification.
4. **`devicelink/o2_transport.py:136`'s docstring** can record Roger's second reason for TCP: `set_services()` followed immediately by a UDP send can race the host learning the service.
