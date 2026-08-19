# The N-fixture Room (Spec C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the Terrarium Room from one bound device to N named light fixtures rendered from one shared MIDI stream over a concatenated virtual surface, per `docs/superpowers/specs/2026-08-18-n-fixture-room-design.md`.

**Architecture:** `RoomProfile` gains `RoomFixture` (N named surfaces, declaration order = physical order); `Room.bound_dev` becomes `Room.bound: dict[fixture, dev]`; `RoomBindingRegistry` keys by `(RoomType, fixture)`; `GameServer` resolves `ROOM` cues to one canonical dev (first bound fixture) and collapses TARGET-fanout across Room fixtures so the shared session is fed once per cue; `DeviceLinkAgent` renders the concatenated surface once per tick and slices the resulting frame across every bound fixture's own o2lite client; `boot()`/`terrarium_boot.py` spawn one simulator subprocess per fixture with a unique service name; the Console shows one strip per fixture. A companion `rainbow` preset in the sibling `luxaeterna` checkout proves the cross-fixture property live.

**Tech Stack:** Python 3.14, pytest, numpy (luxaeterna only), plain JS + Node `vm` for browser-behavior tests, no build step.

## Global Constraints

- Run the suite ONLY as `.venv/bin/python -m pytest tests -v` from the mm-terrarium worktree root. No bare `python`. A fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` (already present in this worktree; verify with `ls -la .venv` before assuming).
- The mm-terrarium suite must stay green with no O2, no Arco, no pyarco importable.
- No `control/` module may import luxaeterna, pyarco or o2litepy at module level (function-scoped imports are fine; `tests/test_room_profile.py::test_no_control_module_imports_a_renderer_at_module_level` pins this).
- No build step for the Console (no npm, no bundler). New browser code needs behavioral tests via Node's `vm` (see `tests/js/room_panel_behavior.test.js` for the pattern), not substring greps.
- No em dashes anywhere in code, comments, docstrings, commit messages, or docs.
- Console exposure is addition, never relaxation: the node id, registration counts, and role name stay hidden from every Console/uplink view; only new, separately-built keys may add visibility.
- luxaeterna lives at `/Users/chris/projects/luxaeterna`, a separate git repo, editable-installed into mm-terrarium's `.venv` (confirmed: `Editable project location: /Users/chris/projects/luxaeterna`). Its own tests run as `.venv/bin/python -m pytest tests -v` from ITS OWN root, using ITS OWN `.venv` (already present there), not mm-terrarium's. It currently has one unrelated uncommitted change (`docs/deployment.md`) sitting in its working tree; do not touch, stage, or commit that file.
- Commit only the files each task actually changes (`git add <specific paths>`, never `-A`).

---

### Task 1: luxaeterna: the `rainbow` preset

**Repo:** `/Users/chris/projects/luxaeterna` (NOT the mm-terrarium worktree).

**Files:**
- Modify: `luxaeterna/luxaeterna/synth/presets.py`
- Test: `luxaeterna/tests/synth/test_presets.py`

**Interfaces:**
- Produces: `registry.build("rainbow", hue=<float>, level=<float>?, span=<float>?, speed=<float>?) -> LightInstrument` with `param_names() == {"hue"}` or `{"hue", "level"}` (mirrors `aurora`'s existing contract exactly).

- [ ] **Step 1: Write the failing tests**

Append to `luxaeterna/tests/synth/test_presets.py` (same file `aurora`'s tests already live in; reuses its `_ctx`/`_out_hue` helpers already defined above in the file):

```python
def test_rainbow_builds_as_instrument():
    assert isinstance(registry.build("rainbow", hue=0.0), LightInstrument)


def test_rainbow_varies_hue_across_positions_not_uniform():
    """The whole point: unlike aurora/glow/bloom, adjacent pixels differ."""
    out = registry.build("rainbow", hue=0.0, span=1.0, speed=0.0).render(
        _ctx(n=8, dt=0.1))
    assert out.shape == (8, 3)
    first_hue = _out_hue(out[0])
    last_hue = _out_hue(out[7])
    assert abs(first_hue - last_hue) > 0.1   # meaningfully different across the field


def test_rainbow_span_zero_is_uniform_like_aurora():
    """span=0 collapses the gradient to a single hue -- a sanity bound on the
    formula, not a real operating mode."""
    out = registry.build("rainbow", hue=0.2, span=0.0, speed=0.0).render(
        _ctx(n=6, dt=0.1))
    np.testing.assert_allclose(out[0], out[5], atol=1e-6)


def test_rainbow_scrolls_over_time():
    """speed != 0 advances the phase, so the SAME pixel's hue at frame N
    differs from frame 0 -- the "scrolling" in scrolling gradient."""
    inst = registry.build("rainbow", hue=0.0, span=1.0, speed=0.5)
    out0 = inst.render(_ctx(frame=0, n=8, dt=0.1))
    out1 = inst.render(_ctx(frame=1, n=8, dt=0.1))
    assert abs(_out_hue(out0[0]) - _out_hue(out1[0])) > 0.01


def test_rainbow_param_names_and_rejects_unknown():
    r = registry.build("rainbow", hue=0.1)
    assert r.param_names() == {"hue"}
    with pytest.raises(KeyError):
        registry.build("rainbow", huue=0.5)


def test_rainbow_with_level_param_is_externally_driven():
    r = registry.build("rainbow", hue=0.0, level=1.0)
    assert r.param_names() == {"hue", "level"}
    brights = [r.render(_ctx(frame=f, n=4, dt=0.5)).max() for f in range(14)]
    assert max(brights) - min(brights) < 0.02   # held steady, no private breathing


def test_rainbow_without_level_still_breathes_like_aurora():
    r = registry.build("rainbow", hue=0.0)
    assert r.param_names() == {"hue"}
    brights = [r.render(_ctx(frame=f, n=4, dt=0.5)).max() for f in range(14)]
    assert max(brights) - min(brights) > 0.1
    assert min(brights) > 0.0


def test_rainbow_hue_glides_toward_target_not_snap():
    r = registry.build("rainbow", hue=0.0, span=0.0, speed=0.0)
    r.render(_ctx(frame=0, n=4, dt=0.1))
    r.set("hue", 0.33)
    h1 = _out_hue(r.render(_ctx(frame=1, n=4, dt=0.1))[0])
    last = None
    for f in range(2, 40):
        last = r.render(_ctx(frame=f, n=4, dt=0.1))
    hN = _out_hue(last[0])
    assert 0.0 < h1 < 0.33
    assert abs(hN - 0.33) < 0.02
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/chris/projects/luxaeterna && .venv/bin/python -m pytest tests/synth/test_presets.py -k rainbow -v`
Expected: FAIL with `KeyError: 'unknown instrument type 'rainbow''` (nothing registered yet).

- [ ] **Step 3: Implement the `Rainbow` field-rate ugen and the `rainbow` preset**

In `luxaeterna/luxaeterna/synth/presets.py`, add near the top (after the existing imports) and after `_make_aurora`/`registry.register("aurora", ...)`:

```python
from .ugens import Const, Envelope, Bloom, Fill, SegmentLevel, Smooth, HueColor, hsv_to_rgb
```
(the file already imports exactly this line; confirm `hsv_to_rgb` is already in it before adding anything -- it is, per the existing import at the top of the file.)

This step has two parts: a new field-rate ugen in `ugens.py`, then a new preset in `presets.py` that builds on it.

First, add the ugen to `luxaeterna/luxaeterna/synth/ugens.py`, next to `Noise` (which already computes `ctx.positions * self._scale + ctx.time * self._speed` -- the exact pattern this reuses):

```python
class Rainbow(LightUgen):
    """Field-rate scrolling hue gradient: hue varies by position across the
    bound zone and advances over time. `positions` spans 0..1 across
    whatever zone this instrument targets -- for target="primary" on a
    multi-fixture Room, that is the WHOLE concatenated surface, which is
    what lets one declaration paint a gradient that crosses fixture
    boundaries with no seam."""

    rate = "field"

    def __init__(self, level, base_hue, span: float, speed: float) -> None:
        super().__init__()
        self._level = as_ugen(level)
        self._base_hue = as_ugen(base_hue)
        self._span = float(span)
        self._speed = float(speed)

    def _compute(self, ctx: RenderContext) -> np.ndarray:
        level = float(np.asarray(self._level.render(ctx)))
        base_hue = float(np.asarray(self._base_hue.render(ctx)))
        hue = (base_hue + self._span * ctx.positions + self._speed * ctx.time) % 1.0
        out = np.empty((ctx.n, ctx.channels))
        for i in range(ctx.n):
            out[i, :3] = hsv_to_rgb(float(hue[i]), 1.0, 1.0)
        if ctx.channels > 3:
            out[:, 3:] = 0.0
        return np.clip(level * out, 0.0, 1.0)
```

(Bounded loop: `ctx.n` is at most the profile's total pixel count, capped at 170 px this slice by `RoomProfile`'s own validation -- see the mm-terrarium tasks below -- so this is cheap at 44 Hz.)

Second, add the preset to `presets.py`, appended at the end of the file, mirroring `_make_aurora`'s shape exactly:

```python
_RAINBOW_PARAMS = frozenset({"hue", "level", "span", "speed"})
_RAINBOW_DEFAULT_SPAN = 1.0     # one full hue cycle across the whole bound zone
_RAINBOW_DEFAULT_SPEED = 0.05   # hue cycles per second


def _make_rainbow(**params) -> LightInstrument:
    unknown = set(params) - _RAINBOW_PARAMS
    if unknown:                    # reject typo'd manifest params, don't discard them
        raise KeyError(f"unknown rainbow param(s) {sorted(unknown)} "
                       f"(known: {sorted(_RAINBOW_PARAMS)})")
    hue = Smooth(Const(float(params.get("hue", 0.0))), _AURORA_HUE_GLIDE_TAU)
    exposed = {"hue": Param("hue", hue)}
    if "level" in params:
        level = Smooth(Const(float(params["level"])), _AURORA_LEVEL_GLIDE_TAU)
        exposed["level"] = Param("level", level)
    else:
        level = SegmentLevel(_AURORA_BREATHE, loop_from=0.0)
    span = float(params.get("span", _RAINBOW_DEFAULT_SPAN))
    speed = float(params.get("speed", _RAINBOW_DEFAULT_SPEED))
    return LightInstrument(Rainbow(level, hue, span, speed), exposed)


registry.register("rainbow", _make_rainbow)
```

Add `Rainbow` to `presets.py`'s ugens import line:
```python
from .ugens import (Const, Envelope, Bloom, Fill, Rainbow, SegmentLevel, Smooth,
                    HueColor, hsv_to_rgb)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/chris/projects/luxaeterna && .venv/bin/python -m pytest tests/synth/test_presets.py -v`
Expected: all pass, including the 7 new `rainbow` tests and every pre-existing `aurora`/`glow`/`bloom` test (unchanged).

Also run the whole luxaeterna suite to confirm nothing else broke: `.venv/bin/python -m pytest tests -v`

- [ ] **Step 5: Commit (in the luxaeterna repo, NOT mm-terrarium)**

```bash
cd /Users/chris/projects/luxaeterna
git add luxaeterna/synth/ugens.py luxaeterna/synth/presets.py tests/synth/test_presets.py
git commit -m "feat(synth): rainbow, a scrolling hue gradient across the bound zone"
```

Do not push. Do not touch `docs/deployment.md` (pre-existing unrelated modification). Note in your task report that this commit sits on luxaeterna's `main` locally and a PR against `Musical-Mycology/luxaeterna` is a separate, user-confirmed follow-up (pushing/opening a PR is not part of this task).

---

### Task 2: `control/room_profile.py` -- `RoomFixture` and the N-fixture `RoomProfile`

**Files:**
- Modify: `control/room_profile.py`
- Test: `tests/test_room_profile.py` (full rewrite of the fixture-shape assertions)

**Interfaces:**
- Produces: `RoomFixture(name, pixel_count, color_order, zones)`; `RoomProfile(surface_id, fixtures: tuple[RoomFixture, ...])` with properties `pixel_count`, `channel_count`, `color_order`, `zones -> tuple[RoomZone, ...]` (namespaced `<fixture>.<zone>`, offset into the concatenated surface) and method `fixture_slices() -> tuple[tuple[str, int, int], ...]` (name, channel_start, channel_count), all in fixture declaration order. `room_profile(RoomType.TEST)` returns a profile with fixtures `main` (60px, zones left/center/right 20px each) and `accent` (30px, zones low/high 15px each).
- Consumes: nothing new (still stdlib + `control.rooms.RoomType` only).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_room_profile.py` in full:

```python
"""RoomProfile: the Room's own N-fixture declaration. Pure -- no luxaeterna,
which is the point (see the design spec section 4's correction note)."""

import pathlib

import pytest

from control.room_profile import (ROOM_PROFILES, RoomFixture, RoomProfile,
                                  RoomZone, room_profile)
from control.rooms import RoomType


def test_test_room_declares_two_asymmetric_fixtures():
    profile = room_profile(RoomType.TEST)
    assert profile.surface_id == "room_test"
    assert [f.name for f in profile.fixtures] == ["main", "accent"]
    assert [f.pixel_count for f in profile.fixtures] == [60, 30]


def test_main_fixture_keeps_the_original_three_zones():
    main = room_profile(RoomType.TEST).fixtures[0]
    assert [z.name for z in main.zones] == ["left", "center", "right"]
    assert [(z.start, z.count) for z in main.zones] == [(0, 20), (20, 20), (40, 20)]


def test_accent_fixture_has_its_own_two_zones():
    accent = room_profile(RoomType.TEST).fixtures[1]
    assert [z.name for z in accent.zones] == ["low", "high"]
    assert [(z.start, z.count) for z in accent.zones] == [(0, 15), (15, 15)]


def test_pixel_count_sums_every_fixture():
    assert room_profile(RoomType.TEST).pixel_count == 90


def test_channel_count_is_three_per_pixel_of_the_whole_profile():
    assert room_profile(RoomType.TEST).channel_count == 270


def test_color_order_is_the_shared_order():
    assert room_profile(RoomType.TEST).color_order == "GRB"


def test_zones_are_namespaced_by_fixture():
    names = [z.name for z in room_profile(RoomType.TEST).zones]
    assert names == ["main.left", "main.center", "main.right",
                     "accent.low", "accent.high"]


def test_zones_are_offset_into_the_concatenated_surface():
    zones = {z.name: (z.start, z.count) for z in room_profile(RoomType.TEST).zones}
    assert zones["main.left"] == (0, 20)
    assert zones["main.right"] == (40, 20)
    assert zones["accent.low"] == (60, 15)   # offset past main's 60 px
    assert zones["accent.high"] == (75, 15)


def test_test_rooms_declared_zones_happen_to_tile_gaplessly():
    """A property of THIS profile's declared data, not an enforced
    invariant -- see test_zones_need_not_be_declared_in_position_order_or_
    tile_gaplessly below for what validation actually requires (no overlap,
    no overrun)."""
    profile = room_profile(RoomType.TEST)
    cursor = 0
    for zone in profile.zones:
        assert zone.start == cursor, f"zone {zone.name} does not abut its predecessor"
        cursor += zone.count
    assert cursor == profile.pixel_count


def test_fixture_slices_are_channel_offsets_in_declaration_order():
    slices = room_profile(RoomType.TEST).fixture_slices()
    assert slices == (("main", 0, 180), ("accent", 180, 90))


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
        profile.fixtures = ()


def test_fixture_is_immutable():
    fixture = room_profile(RoomType.TEST).fixtures[0]
    with pytest.raises(Exception):
        fixture.pixel_count = 99


def test_every_room_type_key_maps_to_a_room_profile():
    for key, value in ROOM_PROFILES.items():
        assert isinstance(key, RoomType)
        assert isinstance(value, RoomProfile)


def test_zone_is_a_plain_value():
    zone = RoomZone("left", 0, 20)
    assert (zone.name, zone.start, zone.count) == ("left", 0, 20)


def test_a_profile_needs_at_least_one_fixture():
    with pytest.raises(ValueError, match="no fixtures"):
        RoomProfile(surface_id="empty", fixtures=())


def test_duplicate_fixture_names_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        RoomProfile(surface_id="dup", fixtures=(
            RoomFixture("a", 10, "GRB", ()), RoomFixture("a", 10, "GRB", ())))


def test_mixed_color_order_across_fixtures_is_rejected():
    with pytest.raises(ValueError, match="color_order"):
        RoomProfile(surface_id="mixed", fixtures=(
            RoomFixture("a", 10, "GRB", ()), RoomFixture("b", 10, "RGB", ())))


def test_zones_overrunning_their_fixture_are_rejected():
    with pytest.raises(ValueError, match="overrun"):
        RoomProfile(surface_id="overrun", fixtures=(
            RoomFixture("a", 10, "GRB", (RoomZone("all", 0, 20),)),))


def test_overlapping_zones_within_one_fixture_are_rejected():
    with pytest.raises(ValueError, match="overlap"):
        RoomProfile(surface_id="overlap", fixtures=(
            RoomFixture("a", 10, "GRB",
                       (RoomZone("x", 0, 5), RoomZone("y", 3, 5))),))


def test_zones_need_not_be_declared_in_position_order_or_tile_gaplessly():
    """Validation catches overlap and overrun (real configuration bugs), not
    declaration order or full coverage -- a fixture may leave pixels
    undeclared (no zone covers them) and may declare its zones in any order,
    exactly as harness/room_surface.py's adapter has always preserved
    whatever order a profile gives it (see tests/test_room_surface.py's
    test_zone_order_is_preserved_for_an_unsorted_profile, unaffected by this
    slice)."""
    profile = RoomProfile(surface_id="sparse-and-unsorted", fixtures=(
        RoomFixture("a", 10, "GRB",
                   (RoomZone("b", 6, 2), RoomZone("a", 0, 2))),))
    assert [z.name for z in profile.zones] == ["a.b", "a.a"]   # order preserved, gap at 2-6 allowed


def test_a_profile_over_the_single_universe_cap_is_rejected():
    with pytest.raises(ValueError, match="170"):
        RoomProfile(surface_id="huge", fixtures=(
            RoomFixture("a", 171, "GRB", ()),))


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

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_profile.py -v`
Expected: FAIL, mostly `ImportError: cannot import name 'RoomFixture'`.

- [ ] **Step 3: Rewrite `control/room_profile.py`**

Replace the file in full:

```python
"""RoomProfile: the Room's own N-fixture declaration, so a Room stops being
shaped like a Tuneshroom and stops being shaped like exactly one device.

Deliberately pure. This module imports nothing outside the standard library
and control/, which is what lets the engine be reasoned about and tested with
no renderer present. The luxaeterna adapter lives in harness/room_surface.py,
mirroring how harness/device_bridge.py already adapts Control-side role
declarations for player devices. See
docs/superpowers/specs/2026-08-18-n-fixture-room-design.md sections 2-3.
"""

from __future__ import annotations

from dataclasses import dataclass

from control.rooms import RoomType

# A single luxaeterna Universe is 512 DMX channels, so a Room caps at 170 px
# RGB in total across every fixture. Anything larger needs PixelSpan/
# UniverseSet (luxaeterna has them; harness/array_smoke.py uses them for the
# 864 px venue array) and is out of scope for this slice (non-goal N5).
_MAX_PROFILE_PIXELS = 170


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
class RoomFixture:
    """One physical (or simulated) light fixture -- its own o2lite client,
    its own unique service name, once bound. See design spec section 3."""
    name: str
    pixel_count: int
    color_order: str
    zones: tuple[RoomZone, ...]


@dataclass(frozen=True)
class RoomProfile:
    """One Room's physical (or simulated) light surface: N fixtures laid end
    to end. Declaration order IS physical order -- a spatial instrument's
    position 0.0 is the first pixel of the first declared fixture, so a
    bottom-to-top effect means the fixtures are declared bottom-to-top. See
    design spec section 2."""
    surface_id: str
    fixtures: tuple[RoomFixture, ...]

    def __post_init__(self) -> None:
        if not self.fixtures:
            raise ValueError(f"profile {self.surface_id!r} declares no fixtures")
        names = [f.name for f in self.fixtures]
        if len(names) != len(set(names)):
            raise ValueError(
                f"profile {self.surface_id!r} has duplicate fixture names: {names}")
        orders = {f.color_order for f in self.fixtures}
        if len(orders) > 1:
            raise ValueError(
                f"profile {self.surface_id!r} mixes color_order {sorted(orders)}; "
                f"one Room renders through one shared session this slice "
                f"(non-goal N4)")
        for fixture in self.fixtures:
            # Overlap and overrun are real configuration bugs; declaration
            # order and full coverage are not requirements -- a fixture may
            # leave pixels undeclared, and harness/room_surface.py's adapter
            # has always preserved whatever zone order a profile gives it
            # (see tests/test_room_surface.py's
            # test_zone_order_is_preserved_for_an_unsorted_profile).
            intervals = sorted((z.start, z.start + z.count) for z in fixture.zones)
            for start, end in intervals:
                if start < 0 or end > fixture.pixel_count:
                    raise ValueError(
                        f"fixture {fixture.name!r} has a zone overrunning "
                        f"its {fixture.pixel_count} px (extends to {end})")
            for i in range(1, len(intervals)):
                if intervals[i][0] < intervals[i - 1][1]:
                    raise ValueError(
                        f"fixture {fixture.name!r} has overlapping zones")
        if self.pixel_count > _MAX_PROFILE_PIXELS:
            raise ValueError(
                f"profile {self.surface_id!r} has {self.pixel_count} px "
                f"total, over the {_MAX_PROFILE_PIXELS} px single-universe "
                f"cap (non-goal N5)")

    @property
    def pixel_count(self) -> int:
        """Total pixels across every fixture, i.e. the concatenated virtual
        surface's width."""
        return sum(f.pixel_count for f in self.fixtures)

    @property
    def channel_count(self) -> int:
        """Wire width of one rendered frame, whole-profile. Three channels
        per pixel, matching the GRB wire devicelink/protocol.py's leds_event
        carries today. The RGBW question (widening to four) is a separate
        open decision about the Tuneshroom's white die and does not belong
        to the Room."""
        return self.pixel_count * 3

    @property
    def color_order(self) -> str:
        """The shared color order every fixture in this profile declares
        (validated equal in __post_init__)."""
        return self.fixtures[0].color_order

    @property
    def zones(self) -> tuple[RoomZone, ...]:
        """Every fixture's zones, renamed `<fixture>.<zone>` and offset into
        the concatenated surface, in fixture declaration order. This is the
        namespaced union the Console draws and a spatial instrument's
        `primary` target spans."""
        out: list[RoomZone] = []
        offset = 0
        for fixture in self.fixtures:
            for zone in fixture.zones:
                out.append(RoomZone(f"{fixture.name}.{zone.name}",
                                    offset + zone.start, zone.count))
            offset += fixture.pixel_count
        return tuple(out)

    def fixture_slices(self) -> tuple[tuple[str, int, int], ...]:
        """Per fixture: (name, channel_start, channel_count) into one
        rendered whole-profile frame, in declaration order. What
        devicelink/agent.py's _render_room() slices a frame with, and what
        each simulator's own client passes as expected_channels."""
        out: list[tuple[str, int, int]] = []
        offset = 0
        for fixture in self.fixtures:
            out.append((fixture.name, offset * 3, fixture.pixel_count * 3))
            offset += fixture.pixel_count
        return tuple(out)


# Linear because the real Terrarium array is a single 6 m run, not a ring and
# a stem. Two fixtures, deliberately asymmetric: the smallest N that
# exercises fan-out, distinct service names, distinct frame widths and
# namespaced zones, with asymmetry so same-shape assumptions cannot hide.
# `main` is the original single-fixture TEST surface, unchanged in shape;
# `accent` is new.
ROOM_PROFILES: dict[RoomType, RoomProfile] = {
    RoomType.TEST: RoomProfile(
        surface_id="room_test",
        fixtures=(
            RoomFixture(
                name="main", pixel_count=60, color_order="GRB",
                zones=(RoomZone("left", 0, 20),
                      RoomZone("center", 20, 20),
                      RoomZone("right", 40, 20))),
            RoomFixture(
                name="accent", pixel_count=30, color_order="GRB",
                zones=(RoomZone("low", 0, 15),
                      RoomZone("high", 15, 15))),
        ),
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

- [ ] **Step 4: Fix the two other direct `RoomProfile(...)` construction sites this rewrite breaks**

`grep -rn "RoomProfile(" tests/ control/ harness/` finds exactly three
call sites outside `control/room_profile.py` itself:
`tests/test_devicelink_agent.py:822` (Task 6 handles this one, rewriting it
alongside that task's other agent-level test changes), and two in
`tests/test_room_surface.py` that construct `RoomProfile` with the OLD flat
`pixel_count`/`color_order`/`zones` keyword arguments directly. Those two
are this task's responsibility, since this task is what changes the
constructor's shape:

```python
def test_a_profile_with_no_zones_still_yields_a_usable_primary():
    profile = RoomProfile(surface_id="bare", fixtures=(
        RoomFixture(name="only", pixel_count=12, color_order="GRB", zones=()),))
    cap = to_capability(profile)
    assert cap.zone("primary").count == 12


def test_zone_order_is_preserved_for_an_unsorted_profile():
    profile = RoomProfile(surface_id="odd", fixtures=(
        RoomFixture(name="only", pixel_count=30, color_order="GRB",
                   zones=(RoomZone("b", 10, 20), RoomZone("a", 0, 10))),))
    cap = to_capability(profile)
    # Namespaced now (RoomProfile.zones prefixes every zone with its
    # fixture's name), and still in declaration order, not position order --
    # this test's whole point, unchanged: names[:2] == ["only.b", "only.a"].
    assert [z.name for z in cap.zones][:2] == ["only.b", "only.a"]
```

Add the `RoomFixture` import to `tests/test_room_surface.py`'s existing
`from control.room_profile import RoomProfile, RoomZone, room_profile` line.

Four MORE tests in the same file assert old single-fixture numbers against
the REAL `room_profile(RoomType.TEST)` (not a custom-constructed profile):
`test_scalar_fields_carry_across`, `test_declared_zones_carry_across_in_order`,
`test_primary_is_appended_spanning_the_whole_surface`, and
`test_declared_zones_resolve_by_name`. These also break once TEST becomes a
90 px, 2-fixture, namespaced-zone profile. Update all four to the new
numbers (the same numbers `tests/test_room_profile.py`'s own rewrite above
already established for this exact profile):

```python
def test_scalar_fields_carry_across():
    cap = to_capability(room_profile(RoomType.TEST))
    assert cap.surface_id == "room_test"
    assert cap.pixel_count == 90
    assert cap.color_order == "GRB"


def test_declared_zones_carry_across_in_order():
    cap = to_capability(room_profile(RoomType.TEST))
    named = [(z.name, z.start, z.count) for z in cap.zones]
    assert named[:3] == [("main.left", 0, 20), ("main.center", 20, 20),
                         ("main.right", 40, 20)]


def test_primary_is_appended_spanning_the_whole_surface():
    """light_manifest instruments target "primary" by default (see
    bits/test_bit.py's Room declaration), so it has to resolve -- now over
    the whole concatenated surface, not one fixture."""
    cap = to_capability(room_profile(RoomType.TEST))
    primary = cap.zone("primary")
    assert (primary.start, primary.count) == (0, 90)


def test_declared_zones_resolve_by_name():
    cap = to_capability(room_profile(RoomType.TEST))
    assert (cap.zone("main.center").start, cap.zone("main.center").count) == (20, 20)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_profile.py tests/test_room_surface.py -v`
Expected: all pass. (`test_room_surface.py` needs `pytest.importorskip("luxaeterna")`
at its top already, per the file's existing first lines -- confirm that
import-skip guard is still there before running; it is not something this
task should remove.)

- [ ] **Step 6: Commit**

```bash
git add control/room_profile.py tests/test_room_profile.py tests/test_room_surface.py
git commit -m "feat(rooms): RoomFixture and the N-fixture RoomProfile"
```

---

### Task 3: `control/rooms.py` + `control/roles.py` -- `Room.bound`, `fully_bound`, and N-fixture role capacity

**Files:**
- Modify: `control/rooms.py`
- Modify: `control/roles.py` (one comment)
- Test: `tests/test_rooms.py`

**Interfaces:**
- Consumes: `RoomProfile`/`RoomFixture` from Task 2 (`control.room_profile`).
- Produces: `Room(room_type, bound: dict[str, str] = {})` with `fully_bound(profile) -> bool`; `room_role(room_type, ...)` now sets `capacity=len(room_profile(room_type).fixtures)` instead of the hardcoded `1`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_rooms.py` in full:

```python
import pytest

from control.room_profile import room_profile
from control.roles import Role, RoleClass
from control.rooms import (
    ROOM_NODE_IDS,
    Room,
    RoomResolutionError,
    RoomType,
    resolve_room_type,
    room_role,
    room_role_name,
)


def test_resolve_room_type_test_needs_no_array_backend():
    assert resolve_room_type(
        RoomType.TEST, array_backend_configured=False) == RoomType.TEST


def test_resolve_room_type_demo_succeeds_with_array_backend():
    assert resolve_room_type(
        RoomType.DEMO, array_backend_configured=True) == RoomType.DEMO


def test_resolve_room_type_demo_fails_without_array_backend():
    with pytest.raises(RoomResolutionError):
        resolve_room_type(RoomType.DEMO, array_backend_configured=False)


def test_room_role_capacity_matches_the_profiles_fixture_count():
    name, role, node = room_role(RoomType.TEST)
    assert role.role_class == RoleClass.ROOM
    assert role.capacity == len(room_profile(RoomType.TEST).fixtures)
    assert role.capacity == 2
    assert role.scored is False
    assert node == ROOM_NODE_IDS[RoomType.TEST]
    assert name == "room_test"


def test_room_role_carries_declared_manifests():
    _, role, _ = room_role(
        RoomType.TEST,
        light_manifest={"instruments": [{"instrument": "rainbow", "target": "primary"}]},
        ugen_manifest={"instruments": [{"instrument": "flsyn"}]})
    assert role.light_manifest["instruments"][0]["instrument"] == "rainbow"
    assert role.ugen_manifest["instruments"][0]["instrument"] == "flsyn"


def test_room_defaults_to_unbound():
    room = Room(room_type=RoomType.TEST)
    assert room.bound == {}
    assert room.fully_bound(room_profile(RoomType.TEST)) is False


def test_room_fully_bound_requires_every_fixture():
    room = Room(room_type=RoomType.TEST)
    room.bound["main"] = "sim-room-main"
    assert room.fully_bound(room_profile(RoomType.TEST)) is False
    room.bound["accent"] = "sim-room-accent"
    assert room.fully_bound(room_profile(RoomType.TEST)) is True


def test_room_role_name_matches_room_role_helper():
    name, role, node = room_role(RoomType.TEST)
    assert name == room_role_name(RoomType.TEST)


def test_room_role_name_is_deterministic_per_type():
    assert room_role_name(RoomType.TEST) == "room_test"
    assert room_role_name(RoomType.DEMO) == "room_demo"
```

Note: `test_room_role_carries_declared_manifests` and the earlier
`test_room_role_builds_capacity_one_room_class_role` both used to call
`room_role(RoomType.DEMO, ...)` because DEMO's capacity was hardcoded to 1
regardless of any profile. That no longer holds -- `room_role()` now calls
`room_profile(room_type)` to size capacity, and DEMO has no profile
(`NotImplementedError`). Both tests above now use `RoomType.TEST` instead.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_rooms.py -v`
Expected: FAIL -- `Room.bound` does not exist yet (`AttributeError`), capacity still hardcoded to 1.

- [ ] **Step 3: Update `control/rooms.py`**

Replace the `Room` dataclass and `room_role()` function (leave `RoomType`, `RoomResolutionError`, `RoomRecipe`, `ROOM_RECIPES`, `ROOM_NODE_IDS`, `resolve_room_type()`, `room_role_name()`, `non_room_counts()` untouched):

```python
from control.room_profile import room_profile   # new import, top of file with the others
```

```python
@dataclass
class Room:
    """Resolved once at boot. `bound` maps fixture name to the dev bound as
    that fixture's rendering backend -- see control/room_binding.py and
    control/boot.py. A fixture absent from this dict is simply not bound
    yet; a Room with SOME but not all fixtures bound renders to the ones it
    has (see design spec section 6)."""
    room_type: RoomType
    bound: dict[str, str] = field(default_factory=dict)

    def fully_bound(self, profile) -> bool:
        return all(fixture.name in self.bound for fixture in profile.fixtures)
```

(Add `field` to the existing `from dataclasses import dataclass` import: `from dataclasses import dataclass, field`.)

```python
def room_role(room_type: RoomType, *, ugen_manifest: dict | None = None,
             light_manifest: dict | None = None) -> tuple[str, Role, str]:
    """Build a ROOM-class Role for room_type plus its canonical node id, so a
    Bit can merge them into its own RoleTable.roles / node_map. The role name
    is deterministic per RoomType so two Bits supporting the same RoomType
    declare identical role names -- see design spec section 3. Capacity is
    the profile's own fixture count: one join per fixture, no more -- see
    design spec section 4.
    """
    name = room_role_name(room_type)
    role = Role(
        name=name,
        role_class=RoleClass.ROOM,
        capacity=len(room_profile(room_type).fixtures),
        scored=False,
        ugen_manifest=ugen_manifest or {},
        light_manifest=light_manifest or {},
    )
    return name, role, ROOM_NODE_IDS[room_type]
```

- [ ] **Step 4: Update `control/roles.py`'s comment**

In `control/roles.py`, change:
```python
    ROOM = auto()     # capacity 1; binds the Room's rendering backend, not a
                       # player -- see control/rooms.py:room_role and
                       # docs/superpowers/specs/2026-08-10-room-concept-and-
                       # load-sequence-design.md section 3.
```
to:
```python
    ROOM = auto()     # capacity = the Room's own fixture count; binds one
                       # fixture's rendering backend per join, not a player
                       # -- see control/rooms.py:room_role and
                       # docs/superpowers/specs/2026-08-18-n-fixture-room-
                       # design.md section 4.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_rooms.py tests/test_roles.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add control/rooms.py control/roles.py tests/test_rooms.py
git commit -m "feat(rooms): Room.bound as a fixture map, room_role capacity from the profile"
```

---

### Task 4: `control/room_binding.py` -- fixture-keyed `RoomBindingRegistry`

**Files:**
- Modify: `control/room_binding.py`
- Test: `tests/test_room_binding.py`

**Interfaces:**
- Produces: `bind(room_type, fixture, dev)`, `bound_device(room_type, fixture) -> str | None`, `release(room_type, fixture=None)`, `arm(room_type, fixture, window_seconds)`, `disarm(room_type)`, `is_armed(room_type) -> bool` (unchanged signature), `armed_fixture(room_type) -> str | None`, `save(path)`/`load(path)` (fixture-keyed on-disk shape `{room_type: {fixture: dev}}`; an old flat-string file is ignored with a logged warning, not migrated).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_room_binding.py` in full:

```python
import json

from control.room_binding import RoomBindingRegistry
from control.rooms import RoomType


def make_clock():
    now = [0.0]

    def clock():
        return now[0]

    def advance(seconds):
        now[0] += seconds

    return clock, advance


def test_bind_and_bound_device_round_trip():
    registry = RoomBindingRegistry()
    assert registry.bound_device(RoomType.TEST, "main") is None
    registry.bind(RoomType.TEST, "main", "sim-room-main")
    assert registry.bound_device(RoomType.TEST, "main") == "sim-room-main"


def test_fixtures_are_independent_within_one_room_type():
    registry = RoomBindingRegistry()
    registry.bind(RoomType.TEST, "main", "sim-room-main")
    registry.bind(RoomType.TEST, "accent", "sim-room-accent")
    assert registry.bound_device(RoomType.TEST, "main") == "sim-room-main"
    assert registry.bound_device(RoomType.TEST, "accent") == "sim-room-accent"


def test_release_one_fixture_leaves_the_other_bound():
    registry = RoomBindingRegistry()
    registry.bind(RoomType.TEST, "main", "sim-room-main")
    registry.bind(RoomType.TEST, "accent", "sim-room-accent")
    registry.release(RoomType.TEST, "main")
    assert registry.bound_device(RoomType.TEST, "main") is None
    assert registry.bound_device(RoomType.TEST, "accent") == "sim-room-accent"


def test_release_with_no_fixture_clears_every_fixture():
    registry = RoomBindingRegistry()
    registry.bind(RoomType.TEST, "main", "sim-room-main")
    registry.bind(RoomType.TEST, "accent", "sim-room-accent")
    registry.release(RoomType.TEST)
    assert registry.bound_device(RoomType.TEST, "main") is None
    assert registry.bound_device(RoomType.TEST, "accent") is None


def test_bindings_are_independent_per_room_type():
    registry = RoomBindingRegistry()
    registry.bind(RoomType.TEST, "main", "sim-room-main")
    registry.bind(RoomType.DEMO, "array", "array-1")
    assert registry.bound_device(RoomType.TEST, "main") == "sim-room-main"
    assert registry.bound_device(RoomType.DEMO, "array") == "array-1"


def test_arm_opens_a_window_that_expires():
    clock, advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    assert registry.is_armed(RoomType.TEST) is False
    registry.arm(RoomType.TEST, "main", window_seconds=10.0)
    assert registry.is_armed(RoomType.TEST) is True
    assert registry.armed_fixture(RoomType.TEST) == "main"
    advance(10.1)
    assert registry.is_armed(RoomType.TEST) is False
    assert registry.armed_fixture(RoomType.TEST) is None


def test_disarm_closes_the_window_immediately():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm(RoomType.TEST, "main", window_seconds=10.0)
    registry.disarm(RoomType.TEST)
    assert registry.is_armed(RoomType.TEST) is False
    assert registry.armed_fixture(RoomType.TEST) is None


def test_arming_a_second_fixture_replaces_the_first():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm(RoomType.TEST, "main", window_seconds=10.0)
    registry.arm(RoomType.TEST, "accent", window_seconds=10.0)
    assert registry.armed_fixture(RoomType.TEST) == "accent"


def test_bind_disarms_only_when_the_bound_fixture_was_the_armed_one():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm(RoomType.TEST, "main", window_seconds=10.0)
    registry.bind(RoomType.TEST, "main", "sim-room-main")
    assert registry.is_armed(RoomType.TEST) is False


def test_bind_of_an_unarmed_fixture_does_not_disturb_a_different_armed_window():
    """control/boot.py's fast path calls bind() directly with nothing armed
    at all -- must not raise or disarm something it never armed."""
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm(RoomType.TEST, "accent", window_seconds=10.0)
    registry.bind(RoomType.TEST, "main", "sim-room-main")   # not the armed fixture
    assert registry.is_armed(RoomType.TEST) is True
    assert registry.armed_fixture(RoomType.TEST) == "accent"


def test_save_then_load_restores_bindings_into_a_fresh_registry(tmp_path):
    path = str(tmp_path / "room_binding.json")
    original = RoomBindingRegistry()
    original.bind(RoomType.TEST, "main", "sim-room-main")
    original.bind(RoomType.TEST, "accent", "sim-room-accent")
    original.bind(RoomType.DEMO, "array", "array-1")
    original.save(path)

    restored = RoomBindingRegistry()
    restored.load(path)
    assert restored.bound_device(RoomType.TEST, "main") == "sim-room-main"
    assert restored.bound_device(RoomType.TEST, "accent") == "sim-room-accent"
    assert restored.bound_device(RoomType.DEMO, "array") == "array-1"


def test_load_missing_file_is_a_noop(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    registry = RoomBindingRegistry()
    registry.load(path)  # must not raise
    assert registry.bound_device(RoomType.TEST, "main") is None


def test_save_does_not_persist_armed_state(tmp_path):
    path = str(tmp_path / "room_binding.json")
    original = RoomBindingRegistry()
    original.arm(RoomType.TEST, "main", window_seconds=10.0)
    original.save(path)

    restored = RoomBindingRegistry()
    restored.load(path)
    assert restored.is_armed(RoomType.TEST) is False


def test_load_ignores_an_old_flat_format_file(tmp_path, caplog):
    """Pre-N-fixture files bound one dev id per room_type as a plain string.
    That is dead data: nothing calls load() from boot() yet (see 'Not yet
    built' in the deep-dive), and guessing which fixture a bare string names
    would risk binding a stale dev to the wrong fixture."""
    path = str(tmp_path / "old_format.json")
    with open(path, "w") as f:
        json.dump({"TEST": "sim-room"}, f)   # old shape: room_type -> dev string

    registry = RoomBindingRegistry()
    registry.load(path)   # must not raise
    assert registry.bound_device(RoomType.TEST, "main") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_binding.py -v`
Expected: FAIL -- `bind()`/`arm()` take the wrong number of arguments.

- [ ] **Step 3: Rewrite `control/room_binding.py`**

Replace the file in full:

```python
"""RoomBindingRegistry: Control-global record of which device is bound as
each RoomType's Room rendering backend, per fixture. Survives Bit load/unload
cycles the same way DevicePool does. See
docs/superpowers/specs/2026-08-18-n-fixture-room-design.md section 4.
"""

from __future__ import annotations

import json
import logging
import os
import time

from control.rooms import RoomType

logger = logging.getLogger(__name__)


class RoomBindingRegistry:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._bound: dict[RoomType, dict[str, str]] = {}
        self._armed_fixture: dict[RoomType, str] = {}
        self._armed_until: dict[RoomType, float] = {}

    def bound_device(self, room_type: RoomType, fixture: str) -> str | None:
        return self._bound.get(room_type, {}).get(fixture)

    def bind(self, room_type: RoomType, fixture: str, dev: str) -> None:
        self._bound.setdefault(room_type, {})[fixture] = dev
        if self._armed_fixture.get(room_type) == fixture:
            self._armed_fixture.pop(room_type, None)
            self._armed_until.pop(room_type, None)

    def release(self, room_type: RoomType, fixture: str | None = None) -> None:
        """Release one fixture, or every fixture of this room_type when
        fixture is None. Only clears the armed window if it belonged to the
        fixture being released (or fixture is None, releasing everything)."""
        if fixture is None:
            self._bound.pop(room_type, None)
            self._armed_fixture.pop(room_type, None)
            self._armed_until.pop(room_type, None)
            return
        self._bound.get(room_type, {}).pop(fixture, None)
        if self._armed_fixture.get(room_type) == fixture:
            self._armed_fixture.pop(room_type, None)
            self._armed_until.pop(room_type, None)

    def arm(self, room_type: RoomType, fixture: str, window_seconds: float) -> None:
        """Open a registration window for window_seconds, naming which
        fixture the next join against the Room node binds. One fixture armed
        at a time per RoomType; arming a second replaces the first -- see
        design spec section 4."""
        self._armed_fixture[room_type] = fixture
        self._armed_until[room_type] = self._clock() + window_seconds

    def disarm(self, room_type: RoomType) -> None:
        self._armed_fixture.pop(room_type, None)
        self._armed_until.pop(room_type, None)

    def is_armed(self, room_type: RoomType) -> bool:
        deadline = self._armed_until.get(room_type)
        return deadline is not None and self._clock() < deadline

    def armed_fixture(self, room_type: RoomType) -> str | None:
        """Which fixture the next Room-node join binds, or None if nothing
        is currently armed (including an expired window)."""
        if not self.is_armed(room_type):
            return None
        return self._armed_fixture.get(room_type)

    def save(self, path: str) -> None:
        """Persist just the bound device IDs, per fixture -- not armed-window
        state, which never survives a restart. See design spec section 4."""
        data = {room_type.name: dict(fixtures)
               for room_type, fixtures in self._bound.items()}
        with open(path, "w") as f:
            json.dump(data, f)

    def load(self, path: str) -> None:
        """Replace in-memory bindings with whatever's on disk. A missing
        file is a no-op (fresh installation, nothing recorded yet). A file in
        the pre-N-fixture flat format (room_type -> single dev string) is
        ignored with a warning rather than guessed at -- see design spec
        section 4."""
        if not os.path.isfile(path):
            return
        with open(path) as f:
            data = json.load(f)
        loaded: dict[RoomType, dict[str, str]] = {}
        for name, value in data.items():
            if not isinstance(value, dict):
                logger.warning(
                    "ignoring room binding file %r: old flat format "
                    "(room_type -> dev string) is no longer supported, "
                    "fixture-keyed format expected", path)
                return
            loaded[RoomType[name]] = dict(value)
        self._bound = loaded
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_binding.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add control/room_binding.py tests/test_room_binding.py
git commit -m "feat(rooms): fixture-keyed RoomBindingRegistry"
```

---

### Task 5: `control/engine.py` -- canonical Room dev, fanout collapse, fixture-aware binding

**Files:**
- Modify: `control/engine.py`
- Test: `tests/test_engine_triggers.py`

**Interfaces:**
- Consumes: `Room.bound`/`fully_bound` (Task 3), `RoomBindingRegistry.armed_fixture`/`bind(room_type, fixture, dev)` (Task 4), `control.room_profile.room_profile`.
- Produces: `GameServer._canonical_room_dev() -> str | None` (first bound fixture in the profile's declaration order); `_resolve_dev(dev)` now resolves the `ROOM` sentinel to the canonical dev; `_resolve_target(target, dev)` unchanged in shape (still returns every bound fixture dev for `ROOM`/`ALL`); new `_collapse_room_fanout(devs) -> list[str]` used only when expanding a trigger's script; `_bind_room(dev)` now consults `room_binding.armed_fixture()` to know which fixture is binding.

- [ ] **Step 1: Write the failing tests**

In `tests/test_engine_triggers.py`, replace the `_Room`/`_running` helpers (lines ~171-187) with:

```python
class _Room:
    def __init__(self, bound):
        from control.rooms import RoomType
        self.room_type = RoomType.TEST
        self.bound = bound   # dict[str, str], fixture name -> dev


def _running(bit_cls=ScriptBit, bound=None, clock=None):
    if bound is None:
        bound = {"main": "sim-room-main"}
    gs = GameServer({"bit": bit_cls}, clock=clock or (lambda: 100.0))
    gs.room = _Room(bound)
    light, play = [], []
    gs.on_light_cue = lambda *a: light.append(a)
    gs.on_play_cue = lambda *a: play.append(a)
    gs.load_bit("bit")
    gs.join("ie1", "NODE")
    gs.run()
    return gs, light, play
```

Then apply this rename throughout the rest of the file (every remaining
`_running(...)` call and every dev-string assertion):
- `_running()` with no args: unchanged call, but every assertion of the
  literal string `"sim-room"` becomes `"sim-room-main"` (the dev the default
  single-bound-fixture `main` now resolves to).
- `_running(bound_dev="ie1")` -> `_running(bound={"main": "ie1"})`
  (`test_all_never_lists_a_room_bound_device_twice`).
- `_running(bound_dev=None)` -> `_running(bound={})`
  (`test_a_room_target_with_no_room_bound_fires_and_reaches_nothing`).

Concretely, these five existing tests change to:

```python
def test_manual_fire_dispatches_every_step_with_its_offset():
    gs, light, _ = _running()
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert [c[0] for c in light] == ["sim-room-main"] * 3
    assert [c[4] for c in light] == [100.0, 100.5, 102.0]
    assert [c[3] for c in light] == [127, 40, 0]


def test_a_verb_handler_fire_shares_the_gestures_presentation_time():
    gs, light, play = _running()
    assert gs.data("ie1", "tap", ["ie1"]) is None
    assert play == [("ie1", "click", "")]
    assert [c[0] for c in light] == ["ie1", "ie1"]
    assert {c[4] for c in light} == {100.0}


def test_the_record_reports_what_the_fire_resolved_to():
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.fire_trigger("sweep", fired_by="admin-manual")
    record = observer.fired[0]
    assert record.name == "sweep"
    assert record.condition == "round_won"
    assert record.devs == ("sim-room-main",)
    assert record.steps == 3
    assert record.at == 100.0


def test_all_resolves_to_the_room_plus_registered_players_deduped():
    gs, light, _ = _running()
    gs.fire_trigger("everywhere", fired_by="admin-manual")
    assert [c[0] for c in light] == ["sim-room-main", "ie1"]


def test_all_never_lists_a_room_bound_device_twice():
    gs, light, _ = _running(bound={"main": "ie1"})
    gs.fire_trigger("everywhere", fired_by="admin-manual")
    assert [c[0] for c in light] == ["ie1"]


def test_a_room_target_with_no_room_bound_fires_and_reaches_nothing():
    """A fire that reached nothing must be visible as such, not absent."""
    gs, light, _ = _running(bound={})
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert light == []
    assert observer.fired[0].devs == ()
    assert observer.fired[0].steps == 0
```

(`test_fired_by_never_inherits_declared_source`, `test_a_device_target_with_no_device_is_refused_not_silently_empty`,
`test_an_unknown_trigger_is_refused`, `test_firing_with_no_bit_running_is_refused`,
`test_a_raising_observer_does_not_stop_the_cues_or_its_peers`,
`test_an_unknown_trigger_from_a_bit_does_not_break_neighbouring_cues` need no
change at all -- none of them inspect a Room dev string.)

Add one new test, right after `test_the_record_reports_what_the_fire_resolved_to`:

```python
def test_a_target_fanout_across_two_bound_fixtures_feeds_the_room_once_per_step():
    """The Room's TARGET-fanout would double-feed the shared session once per
    bound fixture if not collapsed -- see control/engine.py's
    _collapse_room_fanout. Two fixtures bound, three script steps: still
    exactly 3 light cues, not 6, all addressed to the canonical
    (first-declared) fixture's dev. The fired record still reports every
    fixture the trigger's target resolved to, uncollapsed -- collapsing is a
    fan-out concern, not a reporting one."""
    gs, light, _ = _running(bound={"main": "sim-room-main",
                                   "accent": "sim-room-accent"})
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert [c[0] for c in light] == ["sim-room-main"] * 3
    assert observer.fired[0].devs == ("sim-room-main", "sim-room-accent")
    assert observer.fired[0].steps == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_triggers.py -v`
Expected: FAIL -- `_Room` has no `.bound_dev` any more (AttributeError inside `_resolve_dev`/`_resolve_target`, which still read `self.room.bound_dev` at this point), and the new fanout test fails with 6 cues instead of 3.

- [ ] **Step 3: Update `control/engine.py`**

Add this import alongside the existing ones at the top of the file:
```python
from control.room_profile import room_profile
```

Replace `_resolve_dev`, `_resolve_target`, and `_bind_room` (and add
`_canonical_room_dev`/`_collapse_room_fanout`):

```python
    def _bind_room(self, dev: str) -> None:
        fixture = None
        if self.room_binding is not None and self.room is not None:
            fixture = self.room_binding.armed_fixture(self.room.room_type)
        if fixture is not None:
            if self.room_binding is not None:
                self.room_binding.bind(self.room.room_type, fixture, dev)
            self.room.bound[fixture] = dev
        self._notify("on_devices_change")
```

```python
    def _canonical_room_dev(self) -> str | None:
        """The Room's one dev for MIDI-feed purposes: the first bound
        fixture in the profile's declaration order. Room light/audio is one
        shared session (design spec section 2), so every path that feeds it
        -- the ROOM cue sentinel and TARGET-fanout across Room fixtures --
        must resolve to exactly this one dev, never to whichever fixture
        happened to bind first or most recently."""
        if self.room is None or not self.room.bound:
            return None
        profile = room_profile(self.room.room_type)
        for fixture in profile.fixtures:
            dev = self.room.bound.get(fixture.name)
            if dev is not None:
                return dev
        return None

    def _resolve_dev(self, dev: str) -> str | None:
        """cues.ROOM -> the Room's canonical dev; anything else passes
        through.

        Returns None when a ROOM cue has no Room to go to, which the caller
        treats as a drop, never a raise. Warned once per Bit load rather than
        once per cue: a 20 Hz gesture stream would otherwise flood the log.
        """
        if dev != ROOM:
            return dev
        canonical = self._canonical_room_dev()
        if canonical is None:
            if not self._warned_no_room:
                self._warned_no_room = True
                logger.warning("Bit emitted a ROOM cue with no Room bound; "
                               "dropping (logged once per Bit load)")
            return None
        return canonical

    def _resolve_target(self, target, dev: str | None) -> list[str]:
        """A trigger's declared target, resolved to the devs it lands on.

        Returns every bound Room fixture dev for ROOM, in declaration order
        -- this is the one-method change the N-fixture Room slice makes; no
        Bit's trigger declaration changes alongside it (design spec section
        5). This full list is what TriggerFired.devs reports; a script's
        TARGET fanout is collapsed separately, see _collapse_room_fanout.
        """
        if target is TriggerTarget.DEVICE:
            return [dev] if dev else []
        room_devs: list[str] = []
        if self.room is not None and self.room.bound:
            profile = room_profile(self.room.room_type)
            room_devs = [self.room.bound[f.name] for f in profile.fixtures
                        if f.name in self.room.bound]
        if target is TriggerTarget.ROOM:
            return room_devs
        out = list(room_devs)
        assignments = (self.registration.assignments
                       if self.registration is not None else {})
        for player, (_node, _role, role_class) in assignments.items():
            if role_class != RoleClass.ROOM and player not in out:
                out.append(player)
        return out

    def _collapse_room_fanout(self, devs: list[str]) -> list[str]:
        """A script step addressed at cues.TARGET fans out to every dev in
        `devs` (control/triggers.py's expand_script), one independent cue
        per dev. That is correct for player devices, each with its own
        LightSession, but wrong for the Room: every Room fixture dev in
        `devs` shares ONE session (design spec section 2), so feeding it
        once per fixture would double-apply the same relative MIDI. Collapse
        every Room-fixture dev down to the Room's single canonical dev, keep
        every other dev untouched and in order."""
        room_devs = set(self.room.bound.values()) if self.room is not None else set()
        if not room_devs:
            return devs
        canonical = self._canonical_room_dev()
        out: list[str] = []
        seen_room = False
        for d in devs:
            if d not in room_devs:
                out.append(d)
            elif not seen_room:
                out.append(canonical)
                seen_room = True
        return out
```

In `fire_trigger`, change the line
```python
            devs = self._resolve_target(trigger.target, dev)
            cues = expand_script(trigger, at, devs)
```
to
```python
            devs = self._resolve_target(trigger.target, dev)
            cues = expand_script(trigger, at, self._collapse_room_fanout(devs))
```
(`devs` itself, uncollapsed, still flows into the `TriggerFired` record below
unchanged -- only the copy handed to `expand_script` is collapsed.)

- [ ] **Step 3.5: Fix two more directly-broken tests this task's own reconnaissance missed**

`tests/test_engine.py` and `tests/test_engine_data.py` both exercise
`GameServer`'s Room-binding/cue paths directly against `.bound_dev`/the old
un-fixture-aware `arm()`/`bound_device()` signatures, and break the moment
`control/engine.py` changes above land. Neither file was in this task's
original `Files:` list; both are this task's responsibility to close, since
they test exactly the mechanism this step just changed.

In `tests/test_engine.py`, `test_room_node_join_binds_device_once_armed`
(around line 449) needs its old 2-arg `arm()` call widened and its
assertions updated to the fixture-keyed shape:

```python
def test_room_node_join_binds_device_once_armed():
    binding = RoomBindingRegistry()
    server = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=binding)
    server.room = Room(room_type=RoomType.TEST)
    server.load_bit("RoomCapableBit")
    binding.arm(RoomType.TEST, "main", window_seconds=10.0)

    result = server.join("ie9", "ROOM_TEST_NODE")

    assert result.granted is True
    assert result.role_class == RoleClass.ROOM
    assert result.config is None
    assert server.room.bound == {"main": "ie9"}
    assert binding.bound_device(RoomType.TEST, "main") == "ie9"
```

Still in `tests/test_engine.py`, `test_bit_cues_are_dispatched_once_per_running_tick`
(around line 512) sets the Room's bound dev directly; change only this one
line (the `seen == [("sim-room", ...)]` assertion two lines below needs no
change, since the dev STRING is unchanged, only how it's stored):

```python
    gs.room.bound = {"main": "sim-room"}
```
(replaces `gs.room.bound_dev = "sim-room"`)

In `tests/test_engine_data.py`, the private helper `_room_bound` (around
line 365, used by both `test_room_target_resolves_to_the_bound_dev` and
`test_play_cue_can_target_the_room_too`) needs the same one-line fix, which
closes both tests at once:

```python
    gs.room.bound = {"main": bound}
```
(replaces `gs.room.bound_dev = bound`; the `bound="sim-room"` default
parameter and every assertion referencing that string stay unchanged,
since only the storage shape moved, not the value)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_engine_triggers.py tests/test_engine.py tests/test_engine_data.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add control/engine.py tests/test_engine_triggers.py tests/test_engine.py tests/test_engine_data.py
git commit -m "feat(engine): canonical Room dev and TARGET-fanout collapse for N fixtures"
```

---

### Task 6: `devicelink/agent.py` + `harness/room_surface.py` -- render once, slice, send N

**Files:**
- Modify: `devicelink/agent.py`
- Modify: `harness/room_surface.py` (add `to_fixture_capability`, `to_capability` untouched)
- Test: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `Room.bound` (Task 3), `RoomProfile.fixture_slices()` (Task 2).
- Produces: `DeviceLinkAgent._room_dev` (the old singular attribute) is deleted, not replaced by a stored dict -- every consumer reads `self.game_server.room.bound` live instead, via a new `_is_room_dev(dev) -> bool` helper and a new `_canonical_room_dev() -> str | None` helper (walks `self._room_profile.fixtures` in declaration order, mirroring `GameServer._canonical_room_dev` -- used everywhere this file needs "the one Room dev," never `next(iter(bound.values()))`, which would return dict-insertion order instead of profile order); `_render_room()` renders the one shared session once per tick and sends each bound fixture's own slice to its own dev; `harness.room_surface.to_fixture_capability(profile, fixture_name) -> SurfaceCapability` (NEW, for per-fixture simulator canvases -- distinct from the existing whole-profile `to_capability`, which stays unchanged and keeps building the ONE session's capability).

- [ ] **Step 1: Write the failing tests**

In `tests/test_devicelink_agent.py`, replace the two Room test helpers:

```python
def _room_ready_game_server(bound=None):
    """A GameServer with TestBit loaded and its Room's `main` fixture
    already bound to 'sim-room-main' -- the state DeviceLinkAgent sees once
    harness/terrarium_boot.py has already called boot(). `accent` is left
    unbound by default: these tests are fundamentally about cue
    timing/routing on ONE fixture, and leaving `accent` unbound doubles as
    coverage that a partially bound Room still renders (design spec section
    6)."""
    if bound is None:
        bound = {"main": "sim-room-main"}
    binding = RoomBindingRegistry()
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = Room(room_type=RoomType.TEST)
    gs.load_bit("TestBit")
    for fixture, dev in bound.items():
        gs.room.bound[fixture] = dev
        binding.bind(RoomType.TEST, fixture, dev)
    return gs


def _agent_with_bound_room():
    """An agent with its Room's `main` fixture bound to 'sim-room-main',
    light routed through a bare FakeRoomLightSink rather than a real
    luxaeterna session -- for tests about cue routing/timing, where what
    matters is which MIDI tuple reached the sink and when, not the rendered
    frame."""
    gs = _room_ready_game_server()
    room_bridge = RoomBridge()
    server = FakeServer()
    agent = DeviceLinkAgent(gs, server, room_bridge=room_bridge)
    bridge = FakeRoomLightSink()
    room_bridge.bind("sim-room-main", light=bridge)
    return gs, agent, bridge
```

Rename every remaining `"sim-room"` string literal in this file's Room
section (lines ~455-880, the block right after these two helpers) to
`"sim-room-main"` -- these tests only ever bind `main` via the helper above,
so the dev id changes but nothing about what each test proves does. This
covers: `test_room_light_session_built_from_bit_declaration`,
`test_room_dev_cue_routes_to_room_bridge_not_normal_bridges`,
`test_render_room_sends_leds_event_when_frame_changes`,
`test_a_timed_room_cue_is_withheld_until_its_time`,
`test_an_untimed_room_cue_still_applies_on_arrival`,
`test_a_late_room_cue_applies_and_counts_as_clamped`,
`test_room_audio_waits_for_its_moment_and_light_does_not`,
`test_room_frame_carries_a_time`, `test_a_room_audio_cue_already_past_clamps_and_counts`,
`test_room_frames_reach_the_sink`, `test_a_raising_room_frame_sink_does_not_stop_the_leds_going_out`.
For the last two, also change the frame-length assertion from
`len(seen[0][1]) == 180` to `len(seen[0][1]) == 180` -- UNCHANGED, since
`main`'s own slice width (60 px x 3) does not depend on how many other
fixtures the profile declares. Likewise
`test_room_frame_is_the_profile_width_not_thirty_six`'s
`len(frames[-1]["args"][0]) == room_profile(RoomType.TEST).channel_count`
must change (the profile's TOTAL is now 270, but `main`'s own sent slice is
still 180): replace with:

```python
def test_room_frame_is_the_bound_fixtures_own_width_not_the_whole_profile():
    from control.room_profile import room_profile
    gs = _room_ready_game_server()
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-room")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())

    for _ in range(3):
        agent.poll()

    frames = [m for dev, m in server.sent if m["address"] == "/sim-room-main/leds"]
    assert frames, "the Room emitted no frame for its bound fixture"
    main = next(f for f in room_profile(RoomType.TEST).fixtures if f.name == "main")
    assert len(frames[-1]["args"][0]) == main.pixel_count * 3
    assert len(frames[-1]["args"][0]) == 180
```

`test_room_session_is_built_from_the_room_profile_not_the_shroom` changes
because the SESSION now spans the WHOLE concatenated profile (both
fixtures), not just the bound one:

```python
def test_room_session_is_built_from_the_whole_concatenated_profile():
    """The session renders every fixture's pixels, bound or not -- only the
    SEND is scoped to bound fixtures. This is what lets a spatial instrument
    (e.g. luxaeterna's rainbow) paint one gradient across every fixture from
    one declaration."""
    from control.room_profile import room_profile
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())

    assert agent._room_profile == room_profile(RoomType.TEST)
    assert agent._room_light.session.cap.pixel_count == 90
    assert agent._room_light.session.cap.surface_id == "room_test"
```

`test_an_explicit_room_profile_overrides_the_resolved_one` changes because
`RoomProfile` no longer takes flat `pixel_count`/`color_order`/`zones`:

```python
def test_an_explicit_room_profile_overrides_the_resolved_one():
    from control.room_profile import RoomFixture, RoomProfile, RoomZone
    profile = RoomProfile(surface_id="custom", fixtures=(
        RoomFixture(name="only", pixel_count=24, color_order="GRB",
                   zones=(RoomZone("all", 0, 24),)),))
    gs = _room_ready_game_server()
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge(),
                            room_profile=profile)

    assert agent._room_light.session.cap.pixel_count == 24
```

Add three new tests, right after
`test_room_frame_is_the_bound_fixtures_own_width_not_the_whole_profile`:

```python
def test_two_bound_fixtures_each_receive_their_own_slice_of_one_render():
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    server.bind_dev("sim-room-accent", "c-accent")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())

    for _ in range(3):
        agent.poll()

    main_frames = [m for d, m in server.sent if m["address"] == "/sim-room-main/leds"]
    accent_frames = [m for d, m in server.sent if m["address"] == "/sim-room-accent/leds"]
    assert main_frames and accent_frames
    assert len(main_frames[-1]["args"][0]) == 180     # main: 60 px
    assert len(accent_frames[-1]["args"][0]) == 90    # accent: 30 px
    # same presentation time for both slices of the same render
    assert main_frames[-1]["timestamp"] == accent_frames[-1]["timestamp"]


def test_an_unbound_second_fixture_does_not_block_the_first_from_rendering():
    """Partial binding renders -- design spec section 6. One unplugged
    fixture must not black out the rest of the room mid-show."""
    gs = _room_ready_game_server(bound={"main": "sim-room-main"})
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())

    for _ in range(3):
        agent.poll()

    main_frames = [m for d, m in server.sent if m["address"] == "/sim-room-main/leds"]
    accent_frames = [m for d, m in server.sent if m["address"] == "/sim-room-accent/leds"]
    assert main_frames
    assert accent_frames == []   # never bound, never sent to


def test_a_room_cue_feeds_the_shared_session_once_and_reaches_both_fixtures():
    """Integration-level proof of control/engine.py's _collapse_room_fanout:
    a single ROOM-sentinel cue must not double-apply, and its rendered
    consequence must reach every bound fixture's own slice."""
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    server.bind_dev("sim-room-accent", "c-accent")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge())
    agent.poll()   # settle the initial render

    gs.on_light_cue("sim-room-main", 0xB0, 74, 100)   # canonical dev, single cue
    agent.poll()

    main_frames = [m for d, m in server.sent if m["address"] == "/sim-room-main/leds"]
    accent_frames = [m for d, m in server.sent if m["address"] == "/sim-room-accent/leds"]
    assert main_frames and accent_frames   # both slices of the one render went out


def test_an_unchanged_fixture_slice_is_not_resent_after_settling():
    """_last_frames is keyed per fixture dev, so once the shared session's
    output is stable, neither fixture keeps resending on every tick.

    TestBit's Room manifest targets "primary" (the whole concatenated
    surface) with a uniform-fill instrument, so there is no way to change
    only ONE fixture's pixels through its real declaration -- proving
    per-fixture selectivity that way is not available at this integration
    level. What IS provable, and is the same underlying _last_frames
    mechanism: once the render has genuinely stabilized (no new cue, no
    breath reaching the Room -- TestBit's Room role declares no cc:11
    lane, unlike player), NEITHER fixture keeps resending, which could
    only hold if each fixture's slice is compared against its OWN last-sent
    bytes rather than some shared/always-different state.

    Uses a fake, manually-advanced clock (same idiom as
    test_room_dev_cue_routes_to_room_bridge_not_normal_bridges above) so
    aurora's level glide (a real Smooth time constant) has actually
    converged before the counts being compared are captured -- with the
    default wall clock, successive polls advance real time by
    microseconds, nowhere near enough for the glide to settle, and this
    assertion would be flaky by construction without it."""
    clk = _Clock()
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    server.bind_dev("sim-room-accent", "c-accent")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge(), clock=clk)

    for _ in range(5):
        clk.advance(2.0)
        agent.poll()   # let aurora's level glide converge

    def counts():
        main = len([m for d, m in server.sent if m["address"] == "/sim-room-main/leds"])
        accent = len([m for d, m in server.sent if m["address"] == "/sim-room-accent/leds"])
        return main, accent

    before = counts()
    clk.advance(2.0)
    agent.poll()   # no new cue; settled output should be byte-identical
    after = counts()

    assert after == before   # neither fixture resent an unchanged frame


def test_setup_room_builds_the_session_even_with_nothing_bound_yet():
    """A late admin tap must not need a session rebuild -- the session
    spans the whole profile regardless of binding state (see this task's
    changed _setup_room gate)."""
    gs = _room_ready_game_server(bound={})
    agent = DeviceLinkAgent(gs, FakeServer(), room_bridge=RoomBridge())

    assert agent._room_light is not None
    assert agent._room_profile is not None
```

(`_Clock` is already defined in this file, used by the existing
`test_room_dev_cue_routes_to_room_bridge_not_normal_bridges` -- reuse it,
do not redefine it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py -v -k room`
Expected: FAIL -- `_room_dev` singular attribute still in `devicelink/agent.py`, no per-fixture slicing yet.

- [ ] **Step 3: Update `devicelink/agent.py`**

Change the import line:
```python
from control.room_profile import RoomProfile, room_profile
```
stays the same (still needed).

Replace the `_room_dev`/`_room_light` construction section. In `__init__`,
delete the `self._room_dev: str | None = None` line entirely (leaving
`self._room_light = None` in place). No replacement attribute is needed:
every place that used to read `self._room_dev` below now reads
`self.game_server.room.bound` fresh instead, which is already the live
source of truth and picks up a fixture bound after construction (a late
admin tap) with no separate cache to keep in sync.

Replace `_setup_room()`:
```python
    def _setup_room(self) -> None:
        """Build the Room's real LightSession over the WHOLE concatenated
        profile (every fixture, bound or not -- see design spec section 2)
        and, if room_audio was injected, wire its Arco voice. The
        declare-then-compose pattern every per-role device already uses,
        just without a JoinResult (there is no join for this path).

        Construction happens eagerly here, at agent-construction time, and
        _render_room() below is what scopes SENDING to whichever fixtures
        are actually bound at the moment -- bound_devs() reads self.room.
        bound fresh on every render, so a fixture bound after construction
        (a late admin tap) is picked up on its next tick with no rebuild."""
        gs = self.game_server
        room = gs.room
        if room is None or gs.bit is None:
            return
        role = gs.bit.role_table.roles.get(room_role_name(room.room_type))
        if role is None:
            return
        blob = compose_role_config(gs.bit_name, gs.bit.version, role)
        manifest = LightManifest.from_dict(blob["light_manifest"])
        if self._room_profile is None:
            self._room_profile = room_profile(room.room_type)
        cap = to_capability(self._room_profile)
        session = build_session(manifest, cap, clock=self._clock)
        self._room_light = _RoomLightSink(session, Universe())
        audio_sink = None
        canonical = self._canonical_room_dev()
        if self._room_audio is not None and canonical is not None:
            self._room_audio.on_grant(canonical, role)
            audio_sink = _RoomAudioSink(self._room_audio, canonical)
        if self._room_bridge is not None:
            self._room_bridge.bind(canonical, light=self._room_light,
                                   audio=audio_sink)

    def _canonical_room_dev(self) -> str | None:
        """The Room's one dev for MIDI-feed/audio-grant purposes: the
        first bound fixture in the profile's declaration order. Mirrors
        GameServer._canonical_room_dev's algorithm as a self-contained
        copy rather than reaching into the engine's method across the
        module boundary -- this agent already holds everything the walk
        needs (self._room_profile, self.game_server.room.bound). Every
        caller of this method must get the SAME answer for the SAME
        state, the same way every Room-dev decision in the engine goes
        through its own single canonical-dev method -- see design spec
        section 5's 'frame fan-out is the only per-fixture step'."""
        gs = self.game_server
        if gs.room is None or not gs.room.bound or self._room_profile is None:
            return None
        for fixture in self._room_profile.fixtures:
            dev = gs.room.bound.get(fixture.name)
            if dev is not None:
                return dev
        return None
```

Note the changed gate at the top: the ORIGINAL code returned early when
`room.bound_dev is None`, so with no Room bound at all, `_room_light`/
`_room_profile` stayed `None` and every Room-aware method was a no-op
(`test_no_room_configured_leaves_room_wiring_inert` and
`test_no_room_configured_leaves_the_profile_unset` cover this). The NEW code
only requires `room is not None` and a matching role -- it builds the
session even with ZERO fixtures bound yet, because the session spans the
WHOLE profile regardless of binding state, and a late admin tap must not
need a session rebuild. Confirm both of those two tests still construct
`GameServer({"TestBit": TestBit})` with NO `gs.room` set at all (`gs.room`
stays its default `None`) -- if so they still pass unchanged, since the
`room is None` branch of the new gate still returns early exactly as
before. Verify this by reading both tests before editing further; if either
one instead sets `gs.room = Room(...)` with an empty `bound`, adjust the
test (not the production code) to leave `gs.room` unset, matching what
"no Room configured" means elsewhere in this file.

Replace `_render_room()`:
```python
    def _render_room(self) -> None:
        if self._room_light is None or self._room_profile is None:
            return
        gs = self.game_server
        bound = gs.room.bound if gs.room is not None else {}
        if not bound:
            return
        canonical = self._canonical_room_dev()
        # Room AUDIO waits here for its moment. Room LIGHT was already fed in
        # _on_light_cue (or _drain_light_cues), because the frame it renders
        # still has to cross the wire to reach the simulator by `at`. One
        # anchor, two releases -- see the 2026-08-14 spec section 2.
        for (status, d1, d2) in self._room_cues.due(self._clock()):
            try:
                self._room_bridge.feed_audio(status, d1, d2)
            except Exception:
                logger.exception("Room feed_audio failed")
        # Popped unconditionally, for the same reason _render_frames does it:
        # a cue that changes no frame must not leave a stale time behind.
        # Keyed by the canonical dev: every bound fixture's slice shares one
        # `at`, since they all come from the same single render.
        at = self._pending_at.pop(canonical, None)
        universe = self._room_light.universe
        try:
            self._room_light.session.render_into(universe)
        except Exception:
            logger.exception("Room render failed; skipping frame")
            return
        frame = bytes(universe.get_frame()[:self._room_profile.channel_count])
        when = at if at is not None else self._clock() + self._horizon
        for name, start, count in self._room_profile.fixture_slices():
            dev = bound.get(name)
            if dev is None:
                continue   # this fixture is not bound yet -- send to the rest
            slice_ = frame[start:start + count]
            if slice_ != self._last_frames.get(dev):
                self._last_frames[dev] = slice_
                self._emit_room_frame(dev, slice_)
                try:
                    self._send(dev, protocol.leds_event(dev, slice_, when=when))
                except Exception:
                    logger.exception("Room leds send failed for %s", dev)
```

`_feed_light_now` and `_on_light_cue` each have one `dev == self._room_dev`
check. Replace both with a small helper that reads `gs.room.bound` fresh
rather than a stored dict, since that dict is already the live source of
truth and can gain a fixture after construction (a late admin tap):

```python
    def _is_room_dev(self, dev: str) -> bool:
        gs = self.game_server
        return gs.room is not None and dev in gs.room.bound.values()
```

Then in `_feed_light_now`:
```python
        if self._is_room_dev(dev) and self._room_bridge is not None:
```
(replaces `if dev == self._room_dev and self._room_bridge is not None:`)

And in `_on_light_cue`:
```python
        if self._is_room_dev(dev) and self._room_bridge is not None:
            self._room_cues.push(when, (status, data1, data2), now=now)
```
(replaces `if dev == self._room_dev and self._room_bridge is not None:`)

Also fix `on_state_change`'s drone start/stop, which read `self._room_dev`
directly:
```python
        if self._room_audio is None or gs.room is None or not gs.room.bound:
            return
        canonical = self._canonical_room_dev()
        if new_state == State.RUNNING:
            self._room_audio.start_drone(canonical)
        elif new_state == State.UNLOADING:
            self._room_audio.stop_drone(canonical)
```
(`gs = self.game_server` -- add that local at the top of `on_state_change`
if not already present; check the existing method body before editing.)

- [ ] **Step 4: Add `to_fixture_capability` to `harness/room_surface.py`**

Append to the file (leave `to_capability` completely unchanged -- it still
builds the ONE session's whole-profile capability):

```python
def to_fixture_capability(profile: RoomProfile, fixture_name: str):
    """Build ONE fixture's own standalone capability -- for a simulator
    process that displays only that fixture's own physical strip, with
    LOCAL (unprefixed) zone names, not the profile's global namespaced
    union. Distinct from to_capability(), which builds the WHOLE
    concatenated surface for DeviceLinkAgent's one shared session. See
    design spec section 7."""
    from luxaeterna.synth.capability import SurfaceCapability, Zone

    fixture = next(f for f in profile.fixtures if f.name == fixture_name)
    zones = [Zone(z.name, z.start, z.count) for z in fixture.zones]
    zones.append(Zone("primary", 0, fixture.pixel_count))
    return SurfaceCapability(
        surface_id=f"{profile.surface_id}_{fixture_name}",
        pixel_count=fixture.pixel_count,
        color_order=fixture.color_order,
        zones=zones,
    )
```

(The module-level import of `SurfaceCapability, Zone` already exists at the
top of the file for `to_capability`; reuse it there instead of a second
local import if you prefer -- either is fine, since the existing top-level
import is not luxaeterna-guarded by any test in this file that forbids it.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_room_surface.py -v`
Expected: all pass. `tests/test_room_surface.py` needs no CODE change in
this task (`to_capability()` itself is not touched here, and was already
fixed for the new `RoomProfile` constructor shape back in Task 2 step 4,
since `to_capability` reads `pixel_count`/`color_order`/`zones` through
properties either way) -- this run just confirms Task 2's fix still holds.

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py harness/room_surface.py tests/test_devicelink_agent.py
git commit -m "feat(devicelink): render the Room once, slice per fixture, send N"
```

---

### Task 7: `control/boot.py` -- per-fixture fast path and sequential admin-armed wait

**Files:**
- Modify: `control/boot.py`
- Test: `tests/test_boot.py`

**Interfaces:**
- Consumes: `Room.fully_bound` (Task 3), `RoomBindingRegistry.bind(room_type, fixture, dev)`/`armed_fixture` (Task 4), `room_profile` (Task 2).
- Produces: `simulator_factory` contract changes from `Callable[[TeardownStack], str]` to `Callable[[TeardownStack, str], str]` (teardown, fixture_name) -> dev, called once per fixture; `wait_for_room_binding` arms and waits for fixtures sequentially, sharing one overall timeout budget, and only raises `RoomBindingTimeout` when NO fixture ever binds (partial binding after timeout is a warning, not a failure); new module-level `_canonical_room_dev(profile, bound: dict) -> str | None` (the profile-declaration-order dev pick, extracted as its own function so it is directly unit-testable rather than only reachable through a full `boot()` run).

- [ ] **Step 1: Write the failing tests**

In `tests/test_boot.py`:

Replace `_setup_loaded_room_bit`:
```python
def _setup_loaded_room_bit():
    room_binding = RoomBindingRegistry()
    gs = GameServer({"RoomCapableBit": RoomCapableBit}, room_binding=room_binding)
    gs.room = Room(room_type=RoomType.TEST)
    gs.load_bit("RoomCapableBit")
    return gs, room_binding
```
(unchanged -- `Room(room_type=RoomType.TEST)` already defaults `bound` to
`{}` via Task 3's `field(default_factory=dict)`, no call-site change
needed here.)

Replace the three `wait_for_room_binding` tests:

```python
def test_wait_for_room_binding_returns_immediately_if_already_bound():
    gs, room_binding = _setup_loaded_room_bit()
    gs.room.bound = {"main": "ie7", "accent": "ie8"}
    calls = []
    wait_for_room_binding(gs, room_binding, timeout=5.0,
                          tick=lambda: calls.append(1))
    assert calls == []


def test_wait_for_room_binding_arms_each_unbound_fixture_in_turn():
    gs, room_binding = _setup_loaded_room_bit()
    ticks = [0]
    joined = []

    def tick():
        ticks[0] += 1
        armed = room_binding.armed_fixture(gs.room.room_type)
        if armed is not None and armed not in joined and ticks[0] % 3 == 0:
            joined.append(armed)
            gs.join(f"dev-{armed}", "ROOM_TEST_NODE")

    clock, sleep = _fake_clock()
    wait_for_room_binding(gs, room_binding, timeout=5.0, tick=tick,
                          clock=clock, sleep=sleep)

    assert gs.room.bound == {"main": "dev-main", "accent": "dev-accent"}
    assert room_binding.is_armed(gs.room.room_type) is False


def test_wait_for_room_binding_times_out_with_nothing_bound():
    gs, room_binding = _setup_loaded_room_bit()
    clock, sleep = _fake_clock()

    with pytest.raises(RoomBindingTimeout):
        wait_for_room_binding(gs, room_binding, timeout=1.0, tick=lambda: None,
                              clock=clock, sleep=sleep)

    assert room_binding.is_armed(gs.room.room_type) is False


def test_wait_for_room_binding_proceeds_partially_bound_after_timeout(caplog):
    """One unresponsive fixture must not fail the whole boot -- design spec
    section 7."""
    gs, room_binding = _setup_loaded_room_bit()
    ticks = [0]

    def tick():
        ticks[0] += 1
        if ticks[0] == 2 and room_binding.armed_fixture(gs.room.room_type) == "main":
            gs.join("dev-main", "ROOM_TEST_NODE")

    clock, sleep = _fake_clock()
    wait_for_room_binding(gs, room_binding, timeout=1.0, tick=tick,
                          clock=clock, sleep=sleep)   # must not raise

    assert gs.room.bound == {"main": "dev-main"}
```

`test_shutdown_aborts_a_running_bit_then_tears_down` and
`test_shutdown_on_already_idle_server_does_not_raise` call
`room_bridge.bind("ie7")`/no room bind at all respectively -- both are
about `RoomBridge`, untouched this slice; no change needed.

For the remaining ~15 occurrences of `simulator_factory=lambda td: "sim-room-dev"`
in this file (every boot-failure and teardown-order test that does NOT
inspect `gs.room.bound`'s specific contents -- `test_boot_fails_when_arco_never_ready`,
`test_boot_fails_for_unknown_bit_name`, `test_boot_fails_when_bit_does_not_support_resolved_room_type`,
`test_boot_shuts_down_arco_on_any_failure_after_start`,
`test_boot_shuts_down_arco_when_wait_ready_times_out`,
`test_teardown_aborts_the_bit_before_the_room_bridge`,
`test_a_caller_supplied_stack_gets_boots_steps_pushed_onto_it`,
`test_boot_still_accepts_a_factory_that_spawns_nothing`,
`test_boot_shuts_arco_down_even_if_the_simulator_shutdown_raises` (rename its
inner `_RaisingFactory.__call__(self, teardown)` to
`__call__(self, teardown, fixture)`, body unchanged),
`test_boot_raises_the_original_failure_even_if_arco_shutdown_raises`): widen
the lambda's signature only, same return value --
`simulator_factory=lambda td, fixture: "sim-room-dev"`. This is a pure
signature widening: `_bind_room_fast_path` now calls the factory once per
fixture (main, accent), and none of these tests inspect which fixture got
which dev, only that boot fails/tears down/orphans nothing.

For the two happy-path tests that DO inspect binding:

```python
def test_boot_happy_path_via_simulator_factory():
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        arco_process_cls=lambda cmd: _ready_arco(cmd),
        simulator_factory=lambda td, fixture: f"sim-room-{fixture}-dev")

    assert gs.state == State.SETUP
    assert gs.room.room_type == RoomType.TEST
    assert gs.room.bound == {"main": "sim-room-main-dev",
                             "accent": "sim-room-accent-dev"}
    assert room_bridge.dev == "sim-room-main-dev"   # canonical: first declared


def test_boot_happy_path_via_recorded_device_reconnect():
    binding = RoomBindingRegistry()
    binding.bind(RoomType.TEST, "main", "ie7")
    binding.bind(RoomType.TEST, "accent", "ie8")
    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")

    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=binding, arco_process_cls=_ready_arco,
        known_device_connected=lambda dev: dev in ("ie7", "ie8"))

    assert gs.room.bound == {"main": "ie7", "accent": "ie8"}
    assert room_bridge.dev == "ie7"
```

For `_SpyFactory` and `test_teardown_stops_the_simulator_before_arco`'s
inner `_RecordingFactory` (both spawn subprocesses and are inspected for
shutdown counts), widen to track a LIST since the factory is now called
once per fixture:

```python
class _SpyFactory:
    """A simulator_factory that SPAWNS, once per fixture. The contract is
    Callable[[TeardownStack, str], str]: a factory that spawns a process
    registers its own teardown on the stack it is handed."""

    def __init__(self):
        self.processes = []

    def __call__(self, teardown, fixture):
        process = _SpyProcess()
        self.processes.append(process)
        teardown.push(f"simulator-{fixture}", process.shutdown)
        return f"sim-room-{fixture}-dev"
```

Update its three call sites (`test_boot_shuts_down_the_simulator_on_a_failure_after_it_spawned`,
`test_boot_shuts_down_the_simulator_when_the_bit_fails_to_load`,
`test_boot_shuts_both_down_on_a_keyboard_interrupt`) from
`assert factory.process.shutdowns == 1` to
`assert len(factory.processes) == 2` and
`assert all(p.shutdowns == 1 for p in factory.processes)`.

```python
def test_teardown_stops_the_simulator_before_arco():
    order = []

    class _RecordingProcess:
        def __init__(self, fixture):
            self.fixture = fixture

        def shutdown(self):
            order.append(f"simulator-{self.fixture}")

    class _RecordingFactory:
        def __call__(self, teardown, fixture):
            teardown.push(f"simulator-{fixture}", _RecordingProcess(fixture).shutdown)
            return f"sim-room-{fixture}-dev"

    class _RecordingArco(ArcoProcess):
        def shutdown(self):
            order.append("arco")

    config = BootConfig(room_type=RoomType.TEST, bit_name="RoomCapableBit")
    gs, room_bridge, arco, teardown = boot(
        config, make_registry(), arco_command=["arco-server"],
        room_binding=RoomBindingRegistry(),
        arco_process_cls=lambda cmd: _RecordingArco(
            cmd, popen=FakePopen(), probe=lambda: True),
        simulator_factory=_RecordingFactory())

    teardown.close()

    # Both fixture simulators (registered before Arco, since
    # _bind_room_fast_path spawns them before this function's own Arco
    # readiness/Bit-load steps complete) stop before Arco, in LIFO order.
    assert order == ["simulator-accent", "simulator-main", "arco"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_boot.py -v`
Expected: FAIL -- `_bind_room_fast_path` still calls the factory once with
one arg, `wait_for_room_binding` still reads `gs.room.bound_dev`.

- [ ] **Step 3: Update `control/boot.py`**

Add the import:
```python
from control.room_profile import room_profile
```

Replace `_bind_room_fast_path`:
```python
def _bind_room_fast_path(room: Room, room_binding: RoomBindingRegistry,
                         simulator_factory, known_device_connected,
                         teardown) -> None:
    """Attempt the no-tap-needed path per fixture: a Terrarium-spawned
    simulator, or a reconnect to a previously recorded physical device.
    Leaves any fixture unbound (absent from room.bound) if neither applies
    -- wait_for_room_binding below is what holds for a fresh admin-armed
    tap, not this function's job.

    The factory is handed the teardown stack and the fixture name, and
    registers whatever it spawns, so an orphaned Room simulator is
    impossible by construction rather than by a getattr convention. Called
    once per fixture -- each fixture is its own o2lite client with its own
    unique service name (design spec section 3).
    """
    try:
        profile = room_profile(room.room_type)
    except NotImplementedError:
        return   # no fixture declaration for this RoomType yet (e.g. DEMO)
    for fixture in profile.fixtures:
        if simulator_factory is not None:
            dev = simulator_factory(teardown, fixture.name)
            room.bound[fixture.name] = dev
            room_binding.bind(room.room_type, fixture.name, dev)
            continue
        recorded = room_binding.bound_device(room.room_type, fixture.name)
        if recorded is not None and known_device_connected(recorded):
            room.bound[fixture.name] = recorded
```

Change `boot()`'s binding-wait section from:
```python
        if room.bound_dev is None:
            try:
                wait_for_room_binding(
                    gs, room_binding, config.room_setup_timeout,
                    tick=tick or (lambda: gs.tick(0.05)))
            except RoomBindingTimeout as exc:
                gs.abort()
                raise BootFailure(str(exc)) from exc

        room_bridge = RoomBridge()
        if room.bound_dev is not None:
            room_bridge.bind(room.bound_dev)
```
to:
```python
        profile_for_wait = None
        try:
            profile_for_wait = room_profile(room.room_type)
        except NotImplementedError:
            pass
        if profile_for_wait is not None and not room.fully_bound(profile_for_wait):
            try:
                wait_for_room_binding(
                    gs, room_binding, config.room_setup_timeout,
                    tick=tick or (lambda: gs.tick(0.05)))
            except RoomBindingTimeout as exc:
                gs.abort()
                raise BootFailure(str(exc)) from exc

        room_bridge = RoomBridge()
        canonical = (_canonical_room_dev(profile_for_wait, room.bound)
                    if profile_for_wait is not None else None)
        if canonical is not None:
            room_bridge.bind(canonical)
```

Add a new small module-level function, near `_abort_if_running` (this file's
other small helper), extracted as its own named, separately-testable unit
rather than left inline -- mirroring `GameServer._canonical_room_dev` and
`DeviceLinkAgent._canonical_room_dev`, which are both already separate,
testable units for the identical algorithm. `boot.py`'s version cannot be a
method (there is no persistent object to hang it on here), so it is a
free function instead:

```python
def _canonical_room_dev(profile, bound: dict) -> str | None:
    """The Room's one dev for RoomBridge purposes: the first bound fixture
    in the profile's declaration order, not dict-insertion order -- the
    same algorithm as GameServer._canonical_room_dev and
    DeviceLinkAgent._canonical_room_dev. Extracted as its own function
    specifically so this guarantee is unit-testable directly, without
    needing to drive a full boot() through admin-tap timing to construct
    a bound dict whose insertion order differs from declaration order."""
    for fixture in profile.fixtures:
        dev = bound.get(fixture.name)
        if dev is not None:
            return dev
    return None
```

Replace `wait_for_room_binding`:
```python
def wait_for_room_binding(gs: GameServer, room_binding: RoomBindingRegistry,
                          timeout: float, *, tick, clock=time.monotonic,
                          sleep=time.sleep) -> None:
    """Hold until every fixture is bound (each admin-armed tap grants one
    fixture's ROOM-class join) or the shared timeout budget elapses,
    arming fixtures one at a time in the profile's declaration order.
    `tick` is called once per iteration -- driving whatever transport/tick
    loop might deliver that join -- so this function has no transport
    opinion of its own.

    Raises RoomBindingTimeout only when NO fixture ever binds. A Room that
    is SOME but not all fixtures bound after the timeout proceeds anyway --
    see design spec section 7: one unresponsive fixture must not fail the
    whole boot.
    """
    profile = room_profile(gs.room.room_type)
    if gs.room.fully_bound(profile):
        return
    deadline = clock() + timeout
    for fixture in profile.fixtures:
        if fixture.name in gs.room.bound:
            continue
        remaining = deadline - clock()
        if remaining <= 0:
            break
        room_binding.arm(gs.room.room_type, fixture.name, remaining)
        while clock() < deadline and fixture.name not in gs.room.bound:
            tick()
            sleep(0.05)
        room_binding.disarm(gs.room.room_type)
    if not gs.room.bound:
        raise RoomBindingTimeout(
            f"no device joined as {gs.room.room_type.name} Room within {timeout}s")
    missing = [f.name for f in profile.fixtures if f.name not in gs.room.bound]
    if missing:
        logger.warning("Room %s partially bound; missing fixtures: %s",
                       gs.room.room_type.name, missing)
```

Add near the top of the file, with the other imports:
```python
import logging
```
and after the imports, before `class BootFailure`:
```python
logger = logging.getLogger(__name__)
```

Add two small direct tests for the new `_canonical_room_dev` function, near
the top of `tests/test_boot.py` (after `make_registry()`, before the boot
happy-path tests is a fine spot). These test the extracted function
directly rather than through a full `boot()` run, since driving `boot()`
end-to-end into a bind-order that differs from declaration order would
need scripting a join against a `GameServer` instance `boot()` builds
internally and never exposes to the caller -- these two tests are the
actual regression coverage for the ordering guarantee `boot()`'s own
`room_bridge.bind()` call site depends on:

```python
def test_canonical_room_dev_prefers_profile_order_over_bind_order():
    """Regression test: control/engine.py needed two review rounds because
    a similar canonical-dev pick used dict-insertion order instead of the
    profile's declared order. Same algorithm here, tested directly against
    a dict whose insertion order is deliberately reversed from profile
    declaration order (accent inserted first, main second)."""
    from control.boot import _canonical_room_dev
    from control.room_profile import room_profile
    profile = room_profile(RoomType.TEST)
    bound = {"accent": "accent-dev", "main": "main-dev"}
    assert _canonical_room_dev(profile, bound) == "main-dev"


def test_canonical_room_dev_returns_none_when_nothing_bound():
    from control.boot import _canonical_room_dev
    from control.room_profile import room_profile
    profile = room_profile(RoomType.TEST)
    assert _canonical_room_dev(profile, {}) is None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_boot.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add control/boot.py tests/test_boot.py
git commit -m "feat(boot): per-fixture fast path, sequential admin-armed wait"
```

---

### Task 8: `harness/terrarium_boot.py` + `harness/room_simulator.py` + `harness/o2_shroom.py` -- one simulator per fixture

**Files:**
- Modify: `harness/terrarium_boot.py`
- Modify: `harness/room_simulator.py`
- Modify: `harness/o2_shroom.py`
- Test: `tests/test_terrarium_boot.py`, `tests/test_room_simulator.py`, `tests/test_o2_shroom.py`

**Interfaces:**
- Consumes: the widened `simulator_factory` contract from Task 7; `to_fixture_capability` from Task 6.
- Produces: `sim_dev(fixture: str) -> str` (`f"sim-room-{fixture}"`) shared by both factories; `_SimulatorFactory.__call__(self, teardown, fixture)` and `_O2SimulatorFactory.__call__(self, teardown, fixture)`; `room_simulator.build(..., fixture: str = "main")`; `o2_shroom.build(..., fixture: str | None = None)` (required alongside `room_type`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_room_simulator.py`, update the two Room-shape tests to pass
`fixture="main"` explicitly (numbers stay identical, since `main` is
unchanged in shape from before this whole slice):

```python
def test_build_uses_the_room_surface_not_the_shroom():
    client, backend = build("dev", room_type="TEST", fixture="main", serve=False)
    assert backend._cap.pixel_count == 60


def test_build_widens_the_client_to_the_room_frame():
    client, backend = build("dev", room_type="TEST", fixture="main", serve=False)
    assert client.expected_channels == 180
```

Add one new test proving fixture selection actually changes the shape:
```python
def test_build_scopes_to_the_named_fixture_not_the_whole_profile():
    client, backend = build("dev", room_type="TEST", fixture="accent", serve=False)
    assert backend._cap.pixel_count == 30
    assert client.expected_channels == 90
```

In `tests/test_o2_shroom.py`, update:
```python
def test_no_join_build_uses_the_room_surface():
    client, backend = build("sim-room", serve=False, room_type="TEST", fixture="main")
    assert client.expected_channels == 180
```

In `tests/test_terrarium_boot.py`, update
`test_o2_simulator_factory_ties_the_simulator_to_this_process`:
```python
def test_o2_simulator_factory_ties_the_simulator_to_this_process():
    ...
    factory = _O2SimulatorFactory("arco", popen=popen)

    assert factory(TeardownStack(), "main") == "sim-room-main"
```
(read the ~15 surrounding lines first to keep whatever `popen`/assertion
context already exists around the `assert` line; only the call signature
and expected return value change.)

Read `test_build_wires_devicelink_room_bridge_and_simulator` in full before
editing (it is not fully shown in this plan's reconnaissance) and update its
`gs.room.bound_dev == "sim-room"` assertion to
`gs.room.bound == {"main": "sim-room-main", "accent": "sim-room-accent"}`,
following the `sim_dev(fixture)` naming this task introduces.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_simulator.py tests/test_o2_shroom.py tests/test_terrarium_boot.py -v`
Expected: FAIL -- `build()` does not accept `fixture=` yet; factories are
still one-arg.

- [ ] **Step 3: Update `harness/room_simulator.py`**

```python
def build(dev: str, sim_host: str = "127.0.0.1", sim_port: int = 0,
          serve: bool = True, room_type: str = "TEST", fixture: str = "main"):
    """Construct the client + backend WITHOUT opening a socket or serving.

    Returns ``(client, backend)``. Renders exactly one fixture's own
    surface, with LOCAL zone names -- this process IS one physical strip,
    not the whole Room. See harness/room_surface.py's to_fixture_capability
    and design spec section 7.
    """
    from luxaeterna.backends.websim import WebSimBackend

    from control.room_profile import room_profile
    from control.rooms import RoomType
    from harness.room_surface import to_fixture_capability

    profile = room_profile(RoomType[room_type])
    cap = to_fixture_capability(profile, fixture)
    backend = WebSimBackend(capability=cap, host=sim_host, port=sim_port,
                            serve=serve, label=dev)
    client = ShroomClient(dev, node="", leds=WebSimLeds(backend, cap.pixel_count * 3),
                          expected_channels=cap.pixel_count * 3)
    return client, backend
```

Add `--fixture` to `main()`'s argparse (required, alongside `--dev`):
```python
    parser.add_argument("--fixture", required=True,
                        help="Which of the Room's declared fixtures this "
                             "process renders (see control/room_profile.py).")
```
and thread it through the call: `client, backend = build(args.dev, args.sim_host, args.sim_port, room_type=args.room_type, fixture=args.fixture)`.

- [ ] **Step 4: Update `harness/o2_shroom.py`**

Change `build()`'s signature and Room branch:
```python
def build(dev: str, node: str = "TEST_PLAYER_NODE",
          sim_host: str = "127.0.0.1", sim_port: int = 0,
          serve: bool = True, room_type: str | None = None,
          fixture: str | None = None):
    """...

    room_type, when given, renders that ROOM's ONE named fixture instead of
    a Tuneshroom's surface -- fixture is then required. This is the
    --no-join path, where this module stands in for
    harness/room_simulator.py, once per fixture, on the o2lite transport.
    """
    from luxaeterna.backends.websim import WebSimBackend
    from luxaeterna.synth.capability import shroom_capability

    from harness.room_simulator import WebSimLeds

    if room_type is None:
        capability = shroom_capability()
        channels = LED_CHANNELS
    else:
        if fixture is None:
            raise ValueError("room_type requires fixture")
        from control.room_profile import room_profile
        from control.rooms import RoomType
        from harness.room_surface import to_fixture_capability

        profile = room_profile(RoomType[room_type])
        capability = to_fixture_capability(profile, fixture)
        channels = capability.pixel_count * 3

    backend = WebSimBackend(capability=capability,
                            host=sim_host, port=sim_port, serve=serve,
                            label=dev)
    client = ShroomClient(dev, node, leds=WebSimLeds(backend, channels),
                          expected_channels=channels)
    return client, backend
```

Add `--fixture` to `main()`'s argparse, alongside `--room-type`:
```python
    parser.add_argument("--fixture", default=None,
                        help="Which Room fixture to render. Required "
                             "together with --room-type.")
```
and thread it into `build(args.dev, args.node, args.sim_host, args.sim_port, room_type=args.room_type, fixture=args.fixture)`.

- [ ] **Step 5: Update `harness/terrarium_boot.py`**

Replace `SIM_DEV = "sim-room"` with:
```python
def sim_dev(fixture: str) -> str:
    """Deterministic o2lite service name per fixture -- unique per fixture,
    which is the entire reason each is spawned as its own client (design
    spec section 3)."""
    return f"sim-room-{fixture}"
```

Replace `_SimulatorFactory.__call__` and `_O2SimulatorFactory.__call__`:
```python
    def __call__(self, teardown, fixture: str) -> str:
        dev = sim_dev(fixture)
        command = [sys.executable, "-u", "-m", "harness.room_simulator",
                   "--dev", dev, "--server", self._server_url,
                   "--fixture", fixture]
        command += ["--room-type", "TEST"]
        if self._horizon is not None:
            command += ["--control-horizon", str(self._horizon)]
        process = SimulatorProcess(command, popen=self._popen)
        process.start()
        teardown.push(f"simulator-{fixture}", process.shutdown)
        self.processes.append(process)   # was self.process = process (singular)
        return dev
```
(change `_SimulatorFactory.__init__` to initialize `self.processes: list[SimulatorProcess] = []` instead of `self.process = None`, and same for `_O2SimulatorFactory`.)

```python
    def __call__(self, teardown, fixture: str) -> str:
        dev = sim_dev(fixture)
        process = SimulatorProcess(
            [sys.executable, "-u", "-m", "harness.o2_shroom",
             "--dev", dev, "--ensemble", self._ensemble, "--no-join",
             "--exit-with-parent", str(os.getpid()),
             "--room-type", "TEST", "--fixture", fixture],
            popen=self._popen)
        process.start()
        teardown.push(f"simulator-{fixture}", process.shutdown)
        self.processes.append(process)
        return dev
```
(same `self.process` -> `self.processes` list change in `_O2SimulatorFactory.__init__`.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_simulator.py tests/test_o2_shroom.py tests/test_terrarium_boot.py -v`
Expected: all pass.

- [ ] **Step 7: Full-suite smoke check before moving on**

Run: `.venv/bin/python -m pytest tests -v 2>&1 | tail -40`
Expected: failures only in files this plan has not yet reached (room_view,
console, room.js, test_bit) -- confirm nothing in `control/`, `devicelink/`,
`harness/` regressed beyond what Tasks 9-12 below are about to touch.

- [ ] **Step 8: Commit**

```bash
git add harness/terrarium_boot.py harness/room_simulator.py harness/o2_shroom.py \
       tests/test_room_simulator.py tests/test_o2_shroom.py tests/test_terrarium_boot.py
git commit -m "feat(harness): one Room simulator subprocess per fixture"
```

---

### Task 9: `control/room_view.py` -- per-fixture read model

**Files:**
- Modify: `control/room_view.py`
- Test: `tests/test_room_view.py`

**Interfaces:**
- Consumes: `Room.bound` (Task 3), `RoomProfile.fixture_slices()` (Task 2).
- Produces: `room_view(room, profile, role, controllers) -> dict` gains a `"fixtures"` key (list of `{name, pixel_count, channel_start, channel_count, zones, dev}`, `dev` is `None` when unbound) and DROPS the top-level `"bound_dev"` key. `capability_view` unchanged (still the whole-profile surface/zone view).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_room_view.py` in full:

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
            {"instrument": "rainbow", "target": "primary",
             "params": {"hue": 0.6, "level": 0.55},
             "lanes": [{"source": "cc:74", "dest": "hue"}]}]},
        ugen_manifest={"instruments": [
            {"instrument": "flsyn", "program": 89,
             "drone": {"key": 50, "velocity": 80},
             "lanes": [{"source": "cc:74", "dest": "cc:74"}]}]},
    )
    return role


def _room(bound=None):
    room = Room(room_type=RoomType.TEST)
    # `or` would treat an explicitly-passed {} the same as "no argument",
    # since both are falsy -- and _view(bound={}) below needs a genuinely
    # empty dict to reach the "no fixture bound" case.
    room.bound = {"main": "sim-room-main"} if bound is None else bound
    return room


def _view(bound=None):
    return room_view(_room(bound), room_profile(RoomType.TEST), _role(), {74: 93})


def test_no_room_configured_yields_none():
    assert room_view(None, None, None, {}) is None


def test_header_fields():
    view = _view()
    assert view["room_type"] == "TEST"


def test_fixtures_list_carries_name_dev_and_slice():
    fixtures = _view()["fixtures"]
    assert [f["name"] for f in fixtures] == ["main", "accent"]
    assert fixtures[0]["dev"] == "sim-room-main"
    assert fixtures[0]["pixel_count"] == 60
    assert fixtures[0]["channel_start"] == 0
    assert fixtures[0]["channel_count"] == 180
    assert fixtures[1]["dev"] is None    # accent not bound in this fixture's default
    assert fixtures[1]["pixel_count"] == 30
    assert fixtures[1]["channel_start"] == 180
    assert fixtures[1]["channel_count"] == 90


def test_fixtures_zones_are_scoped_to_their_own_fixture():
    fixtures = _view()["fixtures"]
    main_zones = [z["name"] for z in fixtures[0]["zones"]]
    accent_zones = [z["name"] for z in fixtures[1]["zones"]]
    assert main_zones == ["main.left", "main.center", "main.right"]
    assert accent_zones == ["accent.low", "accent.high"]


def test_both_fixtures_bound_report_their_own_dev():
    view = _view(bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    fixtures = view["fixtures"]
    assert fixtures[0]["dev"] == "sim-room-main"
    assert fixtures[1]["dev"] == "sim-room-accent"


def test_capability_carries_the_whole_concatenated_surface():
    view = _view()
    assert view["capability"]["surface_id"] == "room_test"
    assert view["capability"]["pixel_count"] == 90
    assert view["capability"]["color_order"] == "GRB"
    assert [z["name"] for z in view["capability"]["zones"]] == [
        "main.left", "main.center", "main.right", "accent.low", "accent.high"]


def test_primary_is_absent_from_the_serialized_zones():
    assert "primary" not in [z["name"] for z in _view()["capability"]["zones"]]


def test_light_and_audio_appear_in_one_list_discriminated_by_kind():
    instruments = _view()["instruments"]
    assert [i["kind"] for i in instruments] == ["light", "audio"]
    assert instruments[0]["instrument"] == "rainbow"
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
    view = room_view(_room(bound={}), room_profile(RoomType.TEST), None, {})
    assert view["instruments"] == []
    assert view["capability"]["pixel_count"] == 90
    assert all(f["dev"] is None for f in view["fixtures"])


def test_empty_manifests_yield_no_instruments():
    _, role, _ = room_role(RoomType.TEST)
    view = room_view(_room(bound={}), room_profile(RoomType.TEST), role, {})
    assert view["instruments"] == []


def test_the_view_is_json_serializable():
    import json
    json.dumps(_view())


def test_the_node_id_never_appears_anywhere_in_the_view():
    """Section 3 of the room-panel design spec: the Registration Node id
    stays hidden."""
    import json
    from control.rooms import ROOM_NODE_IDS
    blob = json.dumps(_view())
    assert ROOM_NODE_IDS[RoomType.TEST] not in blob


def test_the_room_role_name_never_appears_in_the_view():
    """room_role_name(TEST) == "room_test", and RoomProfile(TEST).surface_id
    == "room_test" too -- two independently authored, already-locked-in facts
    that happen to collide for this one RoomType. capability.surface_id is
    meant to be visible, so its presence is not the role name leaking. The
    check is scoped past that one legitimate field to assert the real fact:
    the role name has no other route into the view."""
    import json
    from control.rooms import room_role_name
    view = _view()
    view["capability"].pop("surface_id")
    assert room_role_name(RoomType.TEST) not in json.dumps(view)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_room_view.py -v`
Expected: FAIL -- `room_view()` still returns `bound_dev`, no `fixtures` key.

- [ ] **Step 3: Update `control/room_view.py`**

Add a `fixtures_view` function and rewrite `room_view`:

```python
def fixtures_view(profile, room) -> list[dict]:
    """One entry per fixture: its own pixel count, its zones (already
    namespaced <fixture>.<zone> by RoomProfile.zones), its channel offset
    into the concatenated frame, and which dev is bound (None if not yet).
    The dev id is shown, matching the precedent this module already set for
    the whole Room (the old single bound_dev field): it is not the
    Registration Node id, the role name, or a registration count, so it is
    not covered by the hiding rule in this module's docstring.
    """
    out = []
    for name, start, count in profile.fixture_slices():
        fixture = next(f for f in profile.fixtures if f.name == name)
        out.append({
            "name": name,
            "pixel_count": fixture.pixel_count,
            "channel_start": start,
            "channel_count": count,
            "zones": [{"name": z.name, "start": z.start, "count": z.count}
                      for z in profile.zones if z.name.startswith(f"{name}.")],
            "dev": room.bound.get(name),
        })
    return out


def room_view(room, profile, role, controllers: dict) -> dict | None:
    """Build the Console's whole Room panel payload.

    Returns None when no Room is configured, which the panel renders as
    "No Room configured". `role` is None when no Bit is loaded, which yields
    the surface with an empty instrument list.

    Light and audio instruments are returned as ONE list discriminated by
    `kind`, not two -- see the module docstring. `fixtures` replaced the
    single `bound_dev` field once the Room became N fixtures: an old browser
    tab reading `room.bound_dev` degrades gracefully to `undefined` rather
    than breaking, and the privacy filters this module's docstring describes
    (node id, role name, registration counts) are unaffected either way.
    """
    if room is None or profile is None:
        return None
    instruments: list[dict] = []
    if role is not None:
        instruments = (_light_instruments(role.light_manifest or {})
                       + _audio_instruments(role.ugen_manifest or {}))
    return {
        "room_type": room.room_type.name,
        "fixtures": fixtures_view(profile, room),
        "capability": capability_view(profile),
        "instruments": instruments,
        "controllers": dict(controllers),
    }
```

(`capability_view` and `_light_instruments`/`_audio_instruments` are
unchanged -- leave them exactly as they are in the file today.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_room_view.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add control/room_view.py tests/test_room_view.py
git commit -m "feat(console): per-fixture Room read model, drop the singular bound_dev"
```

---

### Task 10: `console/protocol.py` + `console/agent.py` -- fixture-aware arm/release

**Files:**
- Modify: `console/protocol.py`
- Modify: `console/agent.py`
- Test: `tests/test_console_agent.py`

**Interfaces:**
- Consumes: `room_view`/`fixtures_view` (Task 9), `RoomBindingRegistry.arm(room_type, fixture, window)`/`release(room_type, fixture=None)` (Task 4).
- Produces: `ArmRoomCommand(room_type, fixture, window_seconds=30.0)`; `ReleaseRoomCommand(room_type, fixture=None)`; `parse_admin_command` requires a non-empty string `fixture` for `arm_room`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_console_agent.py`, update the Room command tests:

```python
def test_arm_room_arms_the_configured_room_binding():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = Room(room_type=RoomType.TEST)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command(
        {"command": "arm_room", "room_type": "TEST", "fixture": "main"})

    assert error is None
    assert gs.room_binding.is_armed(RoomType.TEST) is True
    assert gs.room_binding.armed_fixture(RoomType.TEST) == "main"


def test_arm_room_without_a_fixture_is_refused():
    gs = GameServer({"TestBit": TestBit}, room_binding=RoomBindingRegistry())
    gs.room = Room(room_type=RoomType.TEST)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command({"command": "arm_room", "room_type": "TEST"})

    assert error is not None
    assert error["event"] == "error"


def test_release_room_clears_one_fixtures_binding():
    binding = RoomBindingRegistry()
    binding.bind(RoomType.TEST, "main", "ie7")
    binding.bind(RoomType.TEST, "accent", "ie8")
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = Room(room_type=RoomType.TEST)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command(
        {"command": "release_room", "room_type": "TEST", "fixture": "main"})

    assert error is None
    assert binding.bound_device(RoomType.TEST, "main") is None
    assert binding.bound_device(RoomType.TEST, "accent") == "ie8"


def test_release_room_without_a_fixture_clears_every_fixture():
    binding = RoomBindingRegistry()
    binding.bind(RoomType.TEST, "main", "ie7")
    binding.bind(RoomType.TEST, "accent", "ie8")
    gs = GameServer({"TestBit": TestBit}, room_binding=binding)
    gs.room = Room(room_type=RoomType.TEST)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv)

    error = agent._handle_command(
        {"command": "release_room", "room_type": "TEST"})

    assert error is None
    assert binding.bound_device(RoomType.TEST, "main") is None
    assert binding.bound_device(RoomType.TEST, "accent") is None
```

(`test_arm_room_errors_when_no_room_configured` and
`test_arm_room_errors_for_mismatched_room_type` need a `"fixture": "main"`
key added to their command dicts so the failure they test for is still the
one they name, not a new "missing fixture" refusal reached first -- add it
to both.)

For `_room_console()` and every test using it
(`test_snapshot_carries_the_room_panel`,
`test_snapshot_room_instruments_include_light_and_audio`,
`test_snapshot_room_carries_live_controller_values`,
`test_room_changed_broadcasts_only_when_it_changes`,
`test_the_room_stays_hidden_from_roles_and_registration_while_visible_as_room`,
`test_the_room_stays_hidden_while_triggers_are_visible`), update the helper:

```python
def _room_console(bit_name="TestBit"):
    from control.room_bridge import RoomBridge
    binding = RoomBindingRegistry()
    gs = GameServer({bit_name: TestBit}, room_binding=binding)
    gs.room = Room(room_type=RoomType.TEST)
    gs.room.bound = {"main": "sim-room-main"}
    gs.load_bit(bit_name)
    bridge = RoomBridge()
    bridge.bind("sim-room-main")
    bridge.feed_light(0xB0, 74, 93)
    srv = FakeConsoleServer()
    agent = ConsoleAgent(gs, srv, room_bridge=bridge)
    return gs, srv, agent
```

And `test_snapshot_carries_the_room_panel`'s body:
```python
def test_snapshot_carries_the_room_panel():
    gs, srv, agent = _room_console()
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    assert msg["room"]["room_type"] == "TEST"
    main = msg["room"]["fixtures"][0]
    assert main["name"] == "main"
    assert main["dev"] == "sim-room-main"
    assert [z["name"] for z in main["zones"]] == \
        ["main.left", "main.center", "main.right"]
```

The other four `_room_console()`-based tests already assert on
`msg["room"]["instruments"]`/`["controllers"]`/change-detection, none of
which reference `bound_dev`, so they need no further change beyond the
helper rewrite above.

Read `test_the_room_stays_hidden_from_roles_and_registration_while_visible_as_room`
and `test_the_room_stays_hidden_while_triggers_are_visible` in full before
editing (only their `_room_console()` setup changes; their own assertions
are about role/registration/trigger visibility, unrelated to this task,
and should not need edits beyond whatever the helper rewrite above already
covers).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py -v`
Expected: FAIL -- `arm_room` accepts no `fixture` yet.

- [ ] **Step 3: Update `console/protocol.py`**

```python
@dataclass
class ArmRoomCommand:
    room_type: str
    fixture: str
    window_seconds: float = 30.0


@dataclass
class ReleaseRoomCommand:
    room_type: str
    fixture: str | None = None
```

```python
def parse_admin_command(msg: dict):
    command = msg.get("command")
    if command == "arm_room":
        room_type = msg.get("room_type")
        if not isinstance(room_type, str):
            raise ValueError("arm_room requires a string 'room_type'")
        fixture = msg.get("fixture")
        if not isinstance(fixture, str) or not fixture:
            raise ValueError("arm_room requires a non-empty string 'fixture'")
        window = msg.get("window_seconds", 30.0)
        return ArmRoomCommand(room_type=room_type, fixture=fixture,
                              window_seconds=float(window))
    if command == "release_room":
        room_type = msg.get("room_type")
        if not isinstance(room_type, str):
            raise ValueError("release_room requires a string 'room_type'")
        fixture = msg.get("fixture")
        if fixture is not None and not isinstance(fixture, str):
            raise ValueError("release_room 'fixture' must be a string when given")
        return ReleaseRoomCommand(room_type=room_type, fixture=fixture)
    if command == "fire_trigger":
        name = msg.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("fire_trigger requires a non-empty string 'name'")
        dev = msg.get("dev")
        if dev is not None and not isinstance(dev, str):
            raise ValueError("fire_trigger 'dev' must be a string when given")
        return FireTriggerCommand(name=name, dev=dev or None)
    raise ValueError(f"unrecognized admin command: {command!r}")
```

- [ ] **Step 4: Update `console/agent.py`**

```python
        if isinstance(command, protocol.ArmRoomCommand):
            gs.room_binding.arm(room_type, command.fixture, command.window_seconds)
        elif isinstance(command, protocol.ReleaseRoomCommand):
            gs.room_binding.release(room_type, command.fixture)
```
(replaces the existing two-line body in `_handle_admin_command`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add console/protocol.py console/agent.py tests/test_console_agent.py
git commit -m "feat(console): arm/release a specific Room fixture"
```

---

### Task 11: `console/static/room.js` + `console/static/console.js` -- N per-fixture strips

**Files:**
- Modify: `console/static/room.js`
- Modify: `console/static/console.js`
- Test: `tests/js/room_panel_behavior.test.js`

**Interfaces:**
- Consumes: `room.fixtures` (Task 9/10's wire shape: `{name, pixel_count, channel_start, channel_count, zones, dev}`).
- Produces: `renderRoom(room)` builds one strip + zone bar per fixture, keyed by fixture name (`roomStrip-<name>`); `renderRoomFrame(dev, channels)` (gains a `dev` param) routes each fixture's frame to its own strip by matching `dev` against the fixture list's `dev` field, since a strip is identified by fixture name but a `room_frame` event only carries a `dev`.

- [ ] **Step 1: Write the failing tests**

In `tests/js/room_panel_behavior.test.js`, replace the `room()` fixture
builder and `ZONES_3` with a two-fixture shape:

```javascript
const MAIN_ZONES = [
  { name: "main.left", start: 0, count: 20 },
  { name: "main.center", start: 20, count: 20 },
  { name: "main.right", start: 40, count: 20 },
];
const ACCENT_ZONES = [
  { name: "accent.low", start: 60, count: 15 },
  { name: "accent.high", start: 75, count: 15 },
];

function cap(pixelCount, zones) {
  return { pixel_count: pixelCount, color_order: "GRB", zones };
}

function fixtures(overrides) {
  const base = [
    { name: "main", pixel_count: 60, channel_start: 0, channel_count: 180,
      zones: MAIN_ZONES, dev: "sim-room-main" },
    { name: "accent", pixel_count: 30, channel_start: 180, channel_count: 90,
      zones: ACCENT_ZONES, dev: null },
  ];
  return overrides ? overrides(base) : base;
}

function room(overrides) {
  return Object.assign(
    {
      room_type: "TEST",
      capability: cap(90, MAIN_ZONES.concat(ACCENT_ZONES)),
      fixtures: fixtures(),
      controllers: {},
      instruments: [],
    },
    overrides || {}
  );
}
```

Update the `scenario` sandbox line to also expose `fixtures`:
```javascript
  const sandbox = { document: newDocument(), assert, cap, room, fixtures, orderOf, console };
```

Rewrite `orderOf` (still reads `#room`'s direct children by id, unaffected
by the N-strip change -- leave it as-is) and every `renderRoomFrame(...)`
call in the existing five scenarios to the new two-arg form, and every
swatch-count assertion of `60` for `main`'s strip specifically:

- `"strip survives unchanged-capability re-renders"`: `renderRoomFrame(new Array(180).fill(1))` becomes `renderRoomFrame("sim-room-main", new Array(180).fill(1))`; `paintedBefore === 60`/`paintedAfter === 60` stay checking `document.getElementById("roomStrip-main")`'s children instead of `roomStrip` (rename every `getElementById("roomStrip")` in this scenario to `getElementById("roomStrip-main")`). `orderOf` assertions change from `"roomHeader,roomStrip,roomZones,roomCards"` to `"roomHeader,roomStrip-main,roomZones-main,roomStrip-accent,roomZones-accent,roomCards"` (see Step 3's DOM order below).
- `"a changed pixel_count rebuilds the strip"` and `"changed zones (same pixel_count) also rebuilds the strip"`: replaced in full below by two scenarios with `"only the fixture that changed"` in their names -- these are the load-bearing regression tests for the per-fixture (not whole-array) rebuild guarantee `renderRoom` must provide, so they are given complete rather than described.
- `"renderRoom(null) renders the empty state and resets state cleanly"`: unchanged shape, just confirm `getElementById("roomStrip-main")` and `getElementById("roomStrip-accent")` are BOTH null after `renderRoom(null)`, and both rebuild after a following `renderRoom(room())`.
- `"renderRoomFrame is safe before any renderRoom, and decodes GRB not RGB"`: becomes `renderRoomFrame("sim-room-main", [10, 20, 30])` (must not throw with no strip yet, exactly as before); then `renderRoom(room({ fixtures: fixtures((fx) => [{ ...fx[0], pixel_count: 2, channel_count: 6, zones: [{name: "main.all", start: 0, count: 2}] }, fx[1]]) }))`; `renderRoomFrame("sim-room-main", [10, 20, 30, 40, 50, 60])`; assert against `getElementById("roomStrip-main")`'s children.

Add these scenarios in full (the two rebuild-granularity ones replace the two
named above; the last two are brand new):

```javascript
scenario("a changed pixel_count rebuilds only the fixture that changed", `
  renderRoom(room());
  renderRoomFrame("sim-room-main", new Array(180).fill(1));
  const mainStripBefore = document.getElementById("roomStrip-main");
  const accentStripBefore = document.getElementById("roomStrip-accent");

  renderRoom(room({ fixtures: fixtures((fx) => [
    { ...fx[0], pixel_count: 10, channel_count: 30,
      zones: [{ name: "main.all", start: 0, count: 10 }] },
    fx[1],
  ]) }));

  const mainStripAfter = document.getElementById("roomStrip-main");
  const accentStripAfter = document.getElementById("roomStrip-accent");
  assert(mainStripAfter !== mainStripBefore, "main's strip should have been rebuilt after its own pixel_count changed");
  assert(mainStripAfter.children.length === 10, "expected 10 swatches on the rebuilt main strip, got " + mainStripAfter.children.length);
  const staleMain = mainStripAfter.children.filter((n) => n.style.background).length;
  assert(staleMain === 0, "rebuilt main strip should start unpainted, found " + staleMain + " stale swatches");
  assert(accentStripAfter === accentStripBefore,
    "accent's strip node identity changed even though only main's capability changed " +
    "(a fixture's OWN shape change must not rebuild an unrelated fixture's strip)");
  assert(accentStripAfter.children.length === 30, "accent strip should be untouched at 30 swatches, got " + accentStripAfter.children.length);
  assert(orderOf(document) === "roomHeader,roomStrip-main,roomZones-main,roomStrip-accent,roomZones-accent,roomCards",
    "declaration order must survive a partial rebuild, got: " + orderOf(document));
`);

scenario("changed zones (same pixel_count) rebuilds only that fixture", `
  renderRoom(room());
  renderRoomFrame("sim-room-main", new Array(180).fill(1));
  const mainStripBefore = document.getElementById("roomStrip-main");
  const accentStripBefore = document.getElementById("roomStrip-accent");

  const twoZones = [{ name: "main.left", start: 0, count: 30 }, { name: "main.right", start: 30, count: 30 }];
  renderRoom(room({ fixtures: fixtures((fx) => [{ ...fx[0], zones: twoZones }, fx[1]]) }));

  const mainStripAfter = document.getElementById("roomStrip-main");
  const accentStripAfter = document.getElementById("roomStrip-accent");
  assert(mainStripAfter !== mainStripBefore,
    "main's strip should have been rebuilt after its zones changed, even though pixel_count stayed the same");
  const mainZonesBar = document.getElementById("roomZones-main");
  assert(mainZonesBar.children.length === 3, "main's zone bar should show 1 fixture-name label + 2 zones, got " + mainZonesBar.children.length);
  assert(accentStripAfter === accentStripBefore,
    "accent's strip node identity changed even though only main's zones changed");
  assert(orderOf(document) === "roomHeader,roomStrip-main,roomZones-main,roomStrip-accent,roomZones-accent,roomCards",
    "declaration order must survive a partial rebuild, got: " + orderOf(document));
`);

scenario("one strip per fixture, each with its own zone bar", `
  renderRoom(room());
  const mainStrip = document.getElementById("roomStrip-main");
  const accentStrip = document.getElementById("roomStrip-accent");
  assert(mainStrip !== null && accentStrip !== null, "both fixture strips should exist");
  assert(mainStrip.children.length === 60, "main strip should have 60 swatches, got " + mainStrip.children.length);
  assert(accentStrip.children.length === 30, "accent strip should have 30 swatches, got " + accentStrip.children.length);
  const mainZones = document.getElementById("roomZones-main");
  const accentZones = document.getElementById("roomZones-accent");
  // Each zone bar prepends a fixture-name label span before its per-zone
  // spans (buildFixtureZoneLabels below), so a 3-zone fixture's bar has 4
  // children, not 3 -- one label + three zones.
  assert(mainZones.children.length === 4, "main's zone bar should show 1 fixture-name label + 3 zones, got " + mainZones.children.length);
  assert(accentZones.children.length === 3, "accent's zone bar should show 1 fixture-name label + 2 zones, got " + accentZones.children.length);
`);

scenario("a frame for one fixture does not repaint the other, and routes by dev", `
  renderRoom(room());
  renderRoomFrame("sim-room-main", new Array(180).fill(9));
  renderRoomFrame("sim-room-accent", new Array(90).fill(0));   // accent unbound in fixtures(), dev null -- must be a no-op, no matching strip by dev

  const mainStrip = document.getElementById("roomStrip-main");
  const accentStrip = document.getElementById("roomStrip-accent");
  const mainPainted = mainStrip.children.filter((n) => n.style.background).length;
  const accentPainted = accentStrip.children.filter((n) => n.style.background).length;
  assert(mainPainted === 60, "main frame should paint every main swatch, got " + mainPainted);
  assert(accentPainted === 0, "a frame addressed to an unbound fixture's stale dev must not paint anything, got " + accentPainted);
`);
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node tests/js/room_panel_behavior.test.js`
Expected: FAIL -- `room.js` still builds one `#roomStrip`, reads
`room.bound_dev`/`room.capability` only.

- [ ] **Step 3: Rewrite `console/static/room.js`**

Replace the file in full:

```javascript
// Room panel: N labelled strips (one per declared fixture), plus one card
// per declared instrument showing its target zone, its lanes and each
// lane's current controller value.
//
// The Room's declared light and audio instruments arrive in ONE list
// discriminated by `kind` (see control/room_view.py). They are rendered
// together on purpose: cc:74 drives aurora's/rainbow's hue and FluidSynth's
// cutoff from one shared MIDI stream, and two separate tables would hide
// that.
//
// One shared LightSession renders the WHOLE concatenated surface every
// tick (control/room_view.py's `fixtures` list carries each fixture's own
// channel_start/channel_count into that one frame); a spatial instrument
// like luxaeterna's rainbow can therefore paint one gradient across every
// fixture. Each fixture's OWN strip is repainted only from ITS OWN
// room_frame event (matched by `dev`, since a frame event names a dev, not
// a fixture) -- see renderRoomFrame below.

let roomFixtureShapes = {};     // fixture name -> last-seen {pixel_count, zones}, PER FIXTURE
let fixtureDevByName = {};      // name -> dev, refreshed every renderRoom call
let fixtureNameByDev = {};      // dev -> name, the reverse lookup renderRoomFrame needs

function fixtureShapeMatches(prev, next) {
  if (!prev) return false;
  if (prev.pixel_count !== next.pixel_count) return false;
  return JSON.stringify(prev.zones) === JSON.stringify(next.zones);
}

function renderRoom(room) {
  const el = document.getElementById("room");

  if (!room) {
    el.innerHTML = "";
    roomFixtureShapes = {};
    fixtureDevByName = {};
    fixtureNameByDev = {};
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No Room configured";
    el.appendChild(p);
    return;
  }

  let header = document.getElementById("roomHeader");
  if (!header) {
    el.innerHTML = "";
    header = document.createElement("p");
    header.id = "roomHeader";
    el.appendChild(header);
  }
  const boundCount = room.fixtures.filter((f) => f.dev).length;
  header.textContent = `${room.room_type} · ${room.capability.pixel_count} px · `
    + `${room.capability.color_order} · `
    + `${boundCount}/${room.fixtures.length} fixture(s) bound`;

  fixtureDevByName = {};
  fixtureNameByDev = {};
  for (const fixture of room.fixtures) {
    fixtureDevByName[fixture.name] = fixture.dev;
    if (fixture.dev) fixtureNameByDev[fixture.dev] = fixture.name;
  }

  let cards = document.getElementById("roomCards");

  // Drop strips/zone-bars for any fixture no longer in the profile (not
  // expected in practice -- room_profile()'s declared fixtures are fixed
  // per RoomType -- but keeps stale nodes from surviving a hypothetical
  // reconfiguration).
  const currentNames = new Set(room.fixtures.map((f) => f.name));
  for (const oldName of Object.keys(roomFixtureShapes)) {
    if (!currentNames.has(oldName)) {
      const oldStrip = document.getElementById(`roomStrip-${oldName}`);
      const oldZones = document.getElementById(`roomZones-${oldName}`);
      if (oldStrip) oldStrip.remove();
      if (oldZones) oldZones.remove();
      delete roomFixtureShapes[oldName];
    }
  }

  // Rebuild only the fixtures whose OWN shape changed -- this is Defect 1's
  // whole point applied at fixture granularity: an untouched fixture's live
  // strip must survive another fixture's reconfiguration, not just survive
  // an unrelated controller-value-only room_changed event. A rebuilt
  // fixture is reinserted immediately before the nearest LATER fixture that
  // still has a node in the DOM (or before #roomCards if it's the last),
  // so declaration order survives a partial rebuild instead of every
  // rebuilt fixture being appended after every untouched one.
  for (let i = 0; i < room.fixtures.length; i++) {
    const fixture = room.fixtures[i];
    const nextShape = { pixel_count: fixture.pixel_count, zones: fixture.zones };
    if (fixtureShapeMatches(roomFixtureShapes[fixture.name], nextShape)) {
      continue;
    }
    const oldStrip = document.getElementById(`roomStrip-${fixture.name}`);
    const oldZones = document.getElementById(`roomZones-${fixture.name}`);
    if (oldStrip) oldStrip.remove();
    if (oldZones) oldZones.remove();

    let anchor = cards;
    for (let j = i + 1; j < room.fixtures.length; j++) {
      const nextStrip = document.getElementById(`roomStrip-${room.fixtures[j].name}`);
      if (nextStrip) {
        anchor = nextStrip;
        break;
      }
    }

    const strip = buildFixtureStrip(fixture);
    const zones = buildFixtureZoneLabels(fixture);
    if (anchor) {
      el.insertBefore(strip, anchor);
      el.insertBefore(zones, anchor);
    } else {
      el.appendChild(strip);
      el.appendChild(zones);
    }
    roomFixtureShapes[fixture.name] = nextShape;
  }

  if (!cards) {
    cards = document.createElement("div");
    cards.id = "roomCards";
    cards.className = "cards";
    el.appendChild(cards);
  }
  cards.innerHTML = "";
  for (const inst of room.instruments) {
    cards.appendChild(buildCard(inst, room.controllers || {}));
  }
  if (room.instruments.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No instruments declared (no Bit loaded).";
    cards.appendChild(p);
  }
}

function buildFixtureStrip(fixture) {
  const strip = document.createElement("div");
  strip.id = `roomStrip-${fixture.name}`;
  strip.className = "roomFixtureStrip";
  for (let i = 0; i < fixture.pixel_count; i++) {
    strip.appendChild(document.createElement("div"));
  }
  return strip;
}

function buildFixtureZoneLabels(fixture) {
  const bar = document.createElement("div");
  bar.id = `roomZones-${fixture.name}`;
  const label = document.createElement("span");
  label.className = "fixtureLabel";
  label.textContent = `${fixture.name}${fixture.dev ? "" : " (not bound)"}`;
  bar.appendChild(label);
  for (const zone of fixture.zones) {
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

function renderRoomFrame(dev, channels) {
  // A room_frame event names the DEV that produced it, not the fixture --
  // devicelink/agent.py's _render_room() sends one slice per bound fixture's
  // own dev. fixtureNameByDev, rebuilt on every renderRoom(), is the lookup
  // from dev back to which strip to paint. A dev with no matching fixture
  // (unbound, or a frame that raced a Room reconfiguration) is a no-op, not
  // an error -- boundary rule 2, nothing here may propagate a failure.
  const name = fixtureNameByDev[dev];
  if (!name) return;
  const strip = document.getElementById(`roomStrip-${name}`);
  if (!strip) return;
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

Note the DOM order this produces: `roomHeader`, then for each fixture in
declaration order `roomStrip-<name>` then `roomZones-<name>`, then
`roomCards` last -- i.e. for TEST:
`roomHeader,roomStrip-main,roomZones-main,roomStrip-accent,roomZones-accent,roomCards`,
matching what Step 1's rewritten `orderOf` assertions expect.

- [ ] **Step 4: Update `console/static/console.js`**

Change:
```javascript
    case "room_frame": renderRoomFrame(msg.channels); break;
```
to:
```javascript
    case "room_frame": renderRoomFrame(msg.dev, msg.channels); break;
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `node tests/js/room_panel_behavior.test.js`
Expected: all scenarios pass (script exits 0, no `FAIL:` lines).

Also run: `.venv/bin/python -m pytest tests/test_room_panel_behavior.py tests/test_console_static.py -v`
Expected: pass (the former shells out to the file above; the latter greps
source and should still find whatever substrings it checks for, but read it
first -- if it asserts the literal string `"roomStrip"` appears, that
substring still appears as a prefix inside `roomStrip-${fixture.name}`
template literals in the source text, so a plain substring grep should
still match; if it asserts an EXACT `id="roomStrip"` HTML fragment instead,
update that one assertion to match the new per-fixture id shape).

- [ ] **Step 6: Commit**

```bash
git add console/static/room.js console/static/console.js tests/js/room_panel_behavior.test.js
git commit -m "feat(console): one strip per Room fixture in the panel"
```

(If Step 5 required touching `tests/test_console_static.py`, add it to this
commit too.)

---

### Task 12: `bits/test_bit.py` -- the Room declares `rainbow`

**Files:**
- Modify: `bits/test_bit.py`
- Modify: `tests/test_devicelink_agent.py` (one test, see Step 3 -- a real cross-task interaction, not a hypothetical)

**Interfaces:**
- Consumes: the `rainbow` preset (Task 1, already merged into luxaeterna's editable-installed checkout by the time this task runs).
- Produces: no new Python interface -- TestBit's Room role's `light_manifest` instrument changes from `aurora` to `rainbow`. `play_aurora`'s trigger script and `cues(at)`'s ambient drift are UNCHANGED: both already only ever set `cc:74` (the `hue` param), which `rainbow` exposes identically to `aurora`.

- [ ] **Step 1: Confirm no test locks TestBit's Room instrument name to "aurora"**

Already verified during planning (grep across `tests/*.py` and `bits/*.py`
for `"aurora"`): every hit is either `bits/test_bit.py`'s OWN `player` role
declaration (line ~70, unrelated, unchanged) or a test fixture that builds
its OWN Room role via `room_role(RoomType.TEST, light_manifest={...})`
directly (`tests/test_rooms.py`, `tests/test_room_view.py`) rather than
importing TestBit's declaration. No existing test needs updating for this
task. Skip straight to Step 2.

- [ ] **Step 2: Change the Room role's light_manifest**

In `bits/test_bit.py`, inside `role_table`'s `room_role(RoomType.TEST, ...)`
call, change:
```python
            light_manifest={
                "instruments": [
                    {"instrument": "aurora", "target": "primary",
                     "params": {"hue": 0.6, "level": 0.55},
                     "lanes": [{"source": "cc:74", "dest": "hue"}]},
                ],
            },
```
to:
```python
            light_manifest={
                "instruments": [
                    {"instrument": "rainbow", "target": "primary",
                     "params": {"hue": 0.6, "level": 0.55,
                               "span": 1.0, "speed": 0.05},
                     "lanes": [{"source": "cc:74", "dest": "hue"}]},
                ],
            },
```

Update the surrounding module docstring/comment block that currently
explains "Both of its light instruments are luxaeterna field-rate gestures"
and describes `aurora` for the Room: add one sentence noting the Room's
instrument is now `rainbow`, a scrolling gradient across the Room's whole
concatenated surface, proving the cross-fixture property live -- see design
spec section 9. Keep the existing explanation of why NEITHER instrument
carries a note lane (still true: `rainbow`, like `aurora`, is a field-rate
gesture with no note lane).

- [ ] **Step 3: Run the whole suite, and fix the one real cross-task interaction it surfaces**

Run: `.venv/bin/python -m pytest tests -v`

`tests/test_test_bit.py`'s player-role assertions are untouched (Step 1
confirmed no test names the Room's instrument), and none of
`tests/test_devicelink_agent.py`'s Room tests assert a specific rendered
COLOR tied to `aurora`'s uniform-fill behavior (they assert frame WIDTH
and cue ROUTING, as anticipated during planning). But ONE test asserts a
TIME-based property that only held for `aurora`, not for any
Room instrument in general, and this task's own change breaks it for a
real reason, not a flaky one:
`tests/test_devicelink_agent.py::test_an_unchanged_fixture_slice_is_not_resent_after_settling`
(added in Task 6) proves `_last_frames`' per-fixture dedup by settling the
render, advancing the clock once more with no new cue, and asserting the
resend counts didn't move. `aurora`, once its `level`/`hue` glide
converges, renders a genuinely constant frame forever (no cc:11 lane on
the Room role, so no self-breathing either) -- so advancing time changed
nothing to resend. `rainbow` has no such constant steady state: its hue
keeps scrolling from `ctx.time` forever by design (that animation is the
whole point of the instrument), so advancing the clock further, with or
without a new cue, legitimately produces a different frame every time --
correctly detected and resent, not a bug.

The fix is not to touch production code (`_last_frames`'s comparison logic
is correct and unaffected) -- it's to stop coupling this SPECIFIC test's
final comparison to elapsed time, since "no cue, no resend" was never
actually a property of `_last_frames` alone, it depended on the settled
render also being time-invariant, which was only ever true for the
instrument Task 6 happened to test against. `RenderContext.time` is
derived from the injected clock in luxaeterna's own
`LightSession.render_into` (`t = now - self._start`), so a render at the
SAME clock instant is byte-identical regardless of which instrument
produced it -- proving the actual property under test (do two identical
renders both dedup correctly) without depending on any instrument's
animation behavior. Replace the test's docstring and final comparison
in `tests/test_devicelink_agent.py`:

```python
def test_an_unchanged_fixture_slice_is_not_resent_after_settling():
    """_last_frames is keyed per fixture dev, so once the shared session's
    output is stable, neither fixture keeps resending on every tick.

    TestBit's Room manifest targets "primary" (the whole concatenated
    surface) with one instrument, so there is no way to change only ONE
    fixture's pixels through its real declaration -- proving per-fixture
    selectivity that way is not available at this integration level. What
    IS provable, and is the same underlying _last_frames mechanism: with
    no NEW cue and no elapsed time (no breath reaching the Room either --
    TestBit's Room role declares no cc:11 lane, unlike player), a second
    render produces byte-identical output to the first, and NEITHER
    fixture resends it -- which could only hold if each fixture's slice is
    compared against its OWN last-sent bytes rather than some shared or
    always-different state.

    Uses a fake, manually-advanced clock (same idiom as
    test_room_dev_cue_routes_to_room_bridge_not_normal_bridges above) so
    the settling loop's Smooth-driven params (hue/level glide) actually
    converge before the counts being compared are captured -- with the
    default wall clock, successive polls advance real time by
    microseconds, nowhere near enough to settle, and this assertion would
    be flaky by construction without it.

    The final "before" vs "after" comparison deliberately does NOT advance
    the clock further, unlike the settling loop above it. TestBit's Room
    declares rainbow (see bits/test_bit.py), which -- unlike aurora's
    settle-to-a-constant behavior -- keeps its hue scrolling forever from
    ctx.time even with no new cue, by design (that animation is the whole
    point of the instrument). So "render again after real time passes,
    expect no resend" is no longer a universally true property once the
    Room's instrument can be a perpetually-animating one; "render again at
    the SAME instant, expect no resend" still is, for any instrument,
    because RenderContext.time is derived from the injected clock
    (luxaeterna's LightSession.render_into: t = now - self._start), so a
    frozen clock yields byte-identical output regardless of which
    instrument computed it. This isolates the property actually under
    test (_last_frames' own comparison logic) from whichever instrument
    the Room happens to declare."""
    clk = _Clock()
    gs = _room_ready_game_server(
        bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    server = FakeServer()
    server.bind_dev("sim-room-main", "c-main")
    server.bind_dev("sim-room-accent", "c-accent")
    agent = DeviceLinkAgent(gs, server, room_bridge=RoomBridge(), clock=clk)

    for _ in range(5):
        clk.advance(2.0)
        agent.poll()   # let hue/level glide converge

    def counts():
        main = len([m for d, m in server.sent if m["address"] == "/sim-room-main/leds"])
        accent = len([m for d, m in server.sent if m["address"] == "/sim-room-accent/leds"])
        return main, accent

    before = counts()
    agent.poll()   # same clock instant, no new cue: output must be identical
    after = counts()

    assert after == before   # neither fixture resent an unchanged frame
```

(only the docstring and the removal of the `clk.advance(2.0)` line right
before the final `agent.poll()` change; the settling loop, the `counts()`
helper, and every other line are unchanged.)

Before applying, confirm this reasoning empirically rather than taking it
on faith: run the test against the pre-fix version to see it genuinely
fail on `rainbow` (not for some unrelated reason), apply the fix, confirm
it passes, then run the full suite once more to confirm this is the only
casualty.

- [ ] **Step 4: Commit**

```bash
git add bits/test_bit.py tests/test_devicelink_agent.py
git commit -m "feat(bits): TestBit's Room declares rainbow, not aurora"
```

---

### Task 13: Final sweep, docs/diagram sync, and live-verification readiness

**Files:**
- Grep sweep across `control/`, `devicelink/`, `harness/`, `console/`, `bits/`, `tests/` for any remaining `bound_dev` reference Tasks 2-12 missed.
- Modify: `console/static/style.css` (Task 11's own review confirmed the Room panel currently renders functionally correct but visually unstyled -- the exact-id selectors `#roomStrip`/`#roomZones` no longer match anything now that every strip/zone-bar id is fixture-suffixed).
- Modify: `bits/test_bit.py` (Task 12's own review confirmed several docstrings, comments, and the `play_aurora` trigger's operator-facing `description` field still describe the Room's light instrument as `aurora`; the Room declares `rainbow` as of Task 12, and this text is now inaccurate -- see Step 1.6).
- Modify: `docs/diagrams/cue-path.d2`, `docs/diagrams/boot-teardown.d2` (regenerate via `tools/render_diagrams.py`)
- Modify: `docs/MM_TERRARIUM.md` (move the Spec C entry from "Not yet built / deferred" to a new Landed-subsystems section, mirroring how the trigger slice got its own section)

**Interfaces:** none new; this task only verifies and documents.

- [ ] **Step 1: Grep for stragglers**

```bash
grep -rn '\.bound_dev\b' control/ devicelink/ harness/ console/ bits/ tests/ uplink/ | grep -v "\.pyc"
```

(anchored on `.bound_dev` -- the literal attribute-access form -- rather
than the bare substring `bound_dev`, which false-positives on the
legitimate `bound_device` method name (`RoomBindingRegistry.bound_device`,
already landed in Task 4) and on unrelated identifiers like
`an_unbound_dev_is_a_silent_no_op` in `tests/test_devicelink_server.py`/
`tests/test_o2_transport.py`.)

Expected: zero results. If any remain, they are files this plan's
reconnaissance did not reach (most likely `uplink/` or a test file not
enumerated above) -- fix each following the exact same
`bound_dev: str | None` -> `bound: dict[str, str]` pattern Tasks 3-11
already applied, then re-run this grep until it is empty.

```bash
grep -rn 'self\._room_dev\b' devicelink/ | grep -v "\.pyc"
```

Expected: zero results (this pattern is anchored on `self._room_dev` so it
does not false-positive on the new `self._is_room_dev(...)` helper Task 6
introduces; every use of the old singular attribute was replaced by that
helper or by reading `gs.room.bound` directly).

```bash
grep -rln "simulator_factory=lambda td: " tests/
```

Expected: zero results (every occurrence widened to `lambda td, fixture:` in
Task 7).

- [ ] **Step 1.5: Restyle the Room panel for fixture-suffixed ids**

`console/static/style.css`'s Room-panel block still targets the old
singular `#roomStrip`/`#roomZones` ids, which no longer exist -- every
strip/zone-bar id is now `roomStrip-<fixture>`/`roomZones-<fixture>` (Task
11). `buildFixtureStrip` already sets `className = "roomFixtureStrip"` on
each strip (anticipating exactly this fix); the zone bar itself has no
class, and the new fixture-name label span inside it has
`className = "fixtureLabel"`. Replace the Room panel block:

```css
#roomStrip { display: flex; gap: 1px; margin: .4rem 0 .1rem; height: 2.2rem; }
#roomStrip div { flex: 1 1 0; background: #000; }
#roomZones { display: flex; gap: 1px; font-size: .75rem; color: #555; }
#roomZones span { text-align: center; border-top: 2px solid #999;
                  padding-top: .15rem; }
```
with:
```css
[id^="roomStrip-"] { display: flex; gap: 1px; margin: .4rem 0 .1rem; height: 2.2rem; }
[id^="roomStrip-"] div { flex: 1 1 0; background: #000; }
[id^="roomZones-"] { display: flex; gap: 1px; font-size: .75rem; color: #555; }
[id^="roomZones-"] span:not(.fixtureLabel) { text-align: center; border-top: 2px solid #999;
                  padding-top: .15rem; }
.fixtureLabel { font-weight: bold; margin-right: .4rem; color: #333; }
```

The prefix-match attribute selectors (`[id^="..."]`) restore the original
strip/zone-bar layout and swatch styling for every fixture without touching
`room.js` again (Task 11 is closed and reviewed; this is a CSS-only fix in
a file that was explicitly out of that task's scope). `.fixtureLabel` gets
its own rule so the new per-fixture name label reads as a label, not
another zone.

Run: `.venv/bin/python -m pytest tests/test_console_static.py -v`
Expected: still passes (that file only checks for substrings/file
existence, not computed styles, so this step has no test of its own
beyond not breaking that one).

Commit this step (and any straggler fixes from Step 1) separately from
the docs/diagram sync below, keeping that commit docs-only:

```bash
git add console/static/style.css
git commit -m "fix(console): restyle the Room panel for fixture-suffixed strip/zone-bar ids"
```

- [ ] **Step 1.6: Fix stale "aurora" text in `bits/test_bit.py`**

Task 12 correctly left every line of CODE and BEHAVIOR in this file
untouched outside the Room role's own `light_manifest` block (per its own
brief's explicit instruction), but several docstrings, comments, and one
operator-facing field still describe the Room's light instrument as
`aurora`. Fix the TEXT only -- no code, no logic, no behavior changes
anywhere in this step:

- Line ~165, the `play_aurora` trigger's `description` field (shown on the
  Terrarium Console's trigger panel to a real operator, so this one must
  be exact): change `"A slow aurora sweep across the Room"` to
  `"A slow rainbow sweep across the Room"`.
- Line ~251, `_on_tilt`'s docstring: change the parenthetical
  `"(aurora hue)"` to `"(rainbow hue)"` (it's part of "The Room role
  declares cc:74 on BOTH its light_manifest (aurora hue) and its
  ugen_manifest (FluidSynth cutoff)").
- Lines ~197 and ~203, `cues(at)`'s docstring: both currently name
  `aurora` specifically ("the Room's aurora reached its declared static
  hue," "aurora GLIDES to its target"). Update both to name `rainbow`
  instead of `aurora`. For line ~197's surrounding claim specifically ("...
  reached its declared static hue once and held it, unanimated, for a
  whole run") -- rainbow's own `speed` param means it keeps scrolling on
  its own even with no cc:74 input at all, unlike aurora's true
  once-settled stillness, so this exact claim needs to be true for
  rainbow, not copied verbatim with only the instrument name swapped. Use
  your own judgment on the precise wording (you can verify rainbow's
  actual behavior by reading `luxaeterna/synth/presets.py`'s
  `_make_rainbow` and `luxaeterna/synth/ugens.py`'s `Rainbow._compute`,
  both in the sibling `/Users/chris/projects/luxaeterna` checkout, or by
  running a quick scripted check) -- the point this comment needs to keep
  making accurately is why `cues(at)`'s own periodic cc:74 drift still
  matters (it moves the gradient's BASE hue; `speed` alone would leave the
  base hue fixed even though the gradient keeps scrolling around it).

Do NOT touch: the `player` role's own `aurora` declaration and its
surrounding comments (lines ~65-70, unrelated, correct, unchanged), line
~104's "like player's aurora" comparison (still factually true --
`rainbow` is also a field-rate gesture with no note lane, same structural
point), line ~110's already-correct new sentence from Task 12, or any
line ~34/156/238 reference to the `play_aurora` TRIGGER'S NAME itself
(renaming that identifier is a separate decision this step does not make
-- text accuracy only, not an identifier rename).

Run: `.venv/bin/python -m pytest tests/test_test_bit.py tests/test_devicelink_agent.py -v`
Expected: all pass, unchanged (this step touches no code, so nothing
should move; run it anyway as a cheap confirmation the file still
imports and parses correctly).

```bash
git add bits/test_bit.py
git commit -m "docs(bits): fix stale aurora references in TestBit's Room-facing text"
```

- [ ] **Step 2: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -v 2>&1 | tail -60`
Expected: all pass, matching the Global Constraints (no O2, no Arco, no
pyarco importable). Record the final `N passed, M skipped` count for the
deep-dive update in Step 4.

Also run the JS suite directly once more:
```bash
node tests/js/room_panel_behavior.test.js
```
Expected: exit 0.

- [ ] **Step 3: Regenerate the two stale diagrams**

Edit `docs/diagrams/cue-path.d2`: change the edge label
`"ROOM resolved to bound dev"` to `"ROOM resolved to bound fixture devs"`.

Edit `docs/diagrams/boot-teardown.d2`: change
`p3: 3. Room simulator (pop 3rd)` to
`p3: 3. Room simulators, one per fixture (pop 3rd)`.

Run: `.venv/bin/python -m tools.render_diagrams` (check
`tools/render_diagrams.py`'s own `--help` or its docstring first for the
exact invocation this repo uses -- it may be `python -m
tools.render_diagrams` or a specific script path; confirm before running,
since this plan's reconnaissance did not read that tool in detail).

Confirm the regenerated `docs/diagrams/out/cue-path.svg`,
`docs/diagrams/out/cue-path.txt`, `docs/diagrams/out/boot-teardown.svg`,
`docs/diagrams/out/boot-teardown.txt`, and `docs/diagrams/manifest.json`
all changed, and that the injected ASCII blocks inside
`docs/MM_TERRARIUM.md` between the `<!-- diagram:cue-path -->` and
`<!-- diagram:boot-teardown -->` markers updated to match (the tool injects
these automatically per the file's own header comment; do not hand-edit
the ASCII blocks).

- [ ] **Step 4: Update `docs/MM_TERRARIUM.md`**

In the "Not yet built / deferred" section, change the "A real venue Room is
N light fixtures, not one (Spec C)" entry to a strikethrough-and-closed
entry, following the exact pattern the trigger slice used for its own Spec
B closure (search for `~~**Bit-declared triggers, cue scripts and
conditions (Spec B).**~~` for the pattern to mirror):

```markdown
- ~~**A real venue Room is N light fixtures, not one (Spec C).**~~ **Closed.**
  See *The N-fixture Room (Spec C)* under Landed subsystems above. Live-
  verified against a real Arco: NOT YET DONE. Offline suite only.
```

Add a new Landed-subsystems section, placed after the existing "Bit-declared
triggers, cue scripts and conditions" section (mirroring that section's own
shape: a short intro, a design-doc link, then bullets for the declaration,
the render/fan-out, the boot sequence, and the Console):

```markdown
### `control/room_profile.py`, `control/room_binding.py`, `control/engine.py`, `devicelink/agent.py` -- the N-fixture Room (Spec C)
The Room stops being exactly one bound device. Design:
[`.../2026-08-18-n-fixture-room-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-18-n-fixture-room-design.md).

- **One logical light, N surfaces, namespaced zones.** `RoomProfile` now
  declares N named `RoomFixture`s laid end to end in physical declaration
  order; TEST declares `main` (60 px, the original three zones) and `accent`
  (30 px, two new zones). One `LightSession` renders the WHOLE concatenated
  surface every tick -- a spatial instrument targeting `primary` sees one
  continuous position axis across every fixture, which is what lets a single
  declaration (luxaeterna's new `rainbow` preset) paint a gradient that
  crosses fixture boundaries with no seam.
- **`Room.bound` is a fixture map, `RoomBindingRegistry` keys by
  `(RoomType, fixture)`.** Admin arming now names which fixture the next
  Room-node join binds; `RoleClass.ROOM`'s capacity is the profile's own
  fixture count.
- **`GameServer` resolves ROOM cues to one canonical dev** (the first bound
  fixture in declaration order) so the shared session is fed exactly once
  per cue; a script step addressed at `cues.TARGET` on a ROOM-target trigger
  is collapsed the same way before expansion, so it cannot double-apply the
  same relative MIDI once per bound fixture.
- **`DeviceLinkAgent._render_room()` renders once, slices, sends N.** Each
  bound fixture receives its own channel slice of the one rendered frame,
  stamped with the same presentation time. A partially bound Room renders to
  the fixtures it has.
- **Boot spawns one simulator subprocess per fixture**, each its own o2lite
  client with a unique service name (`sim-room-main`, `sim-room-accent`),
  each on the `TeardownStack` individually.
- **The Console shows one strip per fixture**, each painted only from its
  own fixture's `room_frame` events.
- **Live-verified against a real Arco: NOT YET DONE.** Everything above is
  offline-suite-only as of this slice. Live-verify per the spec's section
  13.1: fire a rainbow-bearing cue with both simulator tabs open and confirm
  one gradient scrolls continuously across both canvases with no seam.
```

Update the suite baseline line ("**844 passed, 1 skipped as of the
2026-08-17 Room-panel slice; 933 passed, 1 skipped as of the trigger
slice**") with the count recorded in Step 2, following the same
running-tally format.

- [ ] **Step 5: Commit**

```bash
git add docs/diagrams/cue-path.d2 docs/diagrams/boot-teardown.d2 \
       docs/diagrams/out/ docs/diagrams/manifest.json docs/MM_TERRARIUM.md
git commit -m "docs(terrarium): sync the deep-dive and diagrams after the N-fixture Room slice"
```

- [ ] **Step 6: Report readiness for live verification**

This plan's scope ends here, fully offline-verified. Live verification
against a real Arco (two simulator browser tabs, a fired rainbow cue,
confirming the gradient crosses the fixture boundary with no seam -- success
criterion 10 of the design spec) is a separate, manual, hands-on-hardware
step for the user to run via `harness/run_stack.py` or
`harness/terrarium_boot.py`, not something this plan's tasks execute
themselves.

---

## Plan self-review notes

- **Spec coverage:** every numbered success criterion (1-10) in the design
  spec maps to a task above: 1->Task 2, 2->Task 4, 3->Task 3, 4->Task 5,
  5->Task 5+6, 6->Task 7/8, 7->Task 9/10/11, 8->Global Constraints (checked
  every task), 9->Task 1/12, 10->Task 13 Step 6 (flagged as the one
  criterion this plan cannot close itself).
- **Cross-repo boundary:** Task 1 is explicitly scoped to the luxaeterna
  checkout, with its own commit step, and does not touch mm-terrarium.
  Task 12 depends on it only through the already-editable-installed package,
  not through any mm-terrarium-side vendoring.
- **The one non-obvious correctness fix this plan makes beyond the spec's
  prose:** `_collapse_room_fanout` (Task 5) is a refinement the spec's
  section 5 described in words ("the agent treats any bound fixture dev as
  Room-owned") but which, read literally, would not have prevented a
  TARGET-fanout script step from feeding the shared session once per bound
  fixture. Task 5's design traces this precisely against the actual
  `expand_script`/`_step_devs` code and TestBit's own `play_aurora`-shaped
  trigger, and Task 5's new test
  (`test_a_target_fanout_across_two_bound_fixtures_feeds_the_room_once_per_step`)
  is the concrete regression proof.
