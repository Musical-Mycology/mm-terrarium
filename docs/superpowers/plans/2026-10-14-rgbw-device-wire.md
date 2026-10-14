# RGBW Device Wire (Contract v5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every pixel frame on the wire becomes R, G, B, W (4 bytes per pixel). `color_order` leaves mm-terrarium's Room config, the light lexicon's whites drive the W die, and the device contract export moves to v5.

**Architecture:**
- luxaeterna's `shroom_capability()` becomes RGBW. Its canonical surface is already RGBW, so the player frame Control renders widens to 48 bytes with no renderer change.
- mm-terrarium gets one tiny wire module, `control/pixel_wire.py` (the order constant, colour normalising, solid frames). Every Room fixture and player path uses it instead of a per-fixture order.
- The Console decodes with one shared RGBW-plus-fold function.
- The contract kit re-records at v5, and mm-tuneshroom follows with its own frame type.

**Tech Stack:** Python 3 (pytest, `.venv`), plain ES-module JS (`node --test`), Dart/Flutter (`flutter test`), luxaeterna (numpy).

**Spec:** `docs/superpowers/specs/2026-10-14-rgbw-device-wire-design.md` (this repo, branch `claude/rgbw-device-wire`, commit 0b087c0).

## Global Constraints

- Wire layout: `pixel_count x 4` bytes, order **R, G, B, W**, 0-255 each. A 12 px player frame is 48 bytes.
- Fold for no-white-die display: `r' = min(255, r + w)`, and likewise for g and b. Every screen renderer uses exactly this.
- W is authored, never derived. An RGB colour has W = 0. There is no white extraction anywhere.
- Lexicon white = `(0, 0, 0, 255)`. Green `(0, 255, 0)` and red `(255, 0, 0)` are unchanged, with W = 0.
- `CONTRACT_VERSION = 5`. `expect_frame` key `rgbw`, 48 ints. Hard cut, no negotiation.
- A room TOML carrying `color_order` must fail to load with a located `TerrariumConfigError` naming the key.
- Python: always `.venv/bin/python`, never `python3`. In this worktree, `.venv` is a symlink to `/Users/chris/projects/mm-terrarium/.venv`.
- JS tests: `node --test tests/js/*.test.js` (the bare directory form fails).
- No em dashes in any doc or comment written for this change.
- Branches:
  - luxaeterna `claude/rgbw-shroom-capability` (worktree `/Users/chris/projects/luxaeterna/.claude/worktrees/rgbw-shroom-capability`)
  - mm-terrarium `claude/rgbw-device-wire` (this worktree)
  - mm-tuneshroom `claude/rgbw-frames` (worktree `/Users/chris/projects/mm-tuneshroom/.claude/worktrees/rgbw-frames`)
- **luxaeterna override for mm-terrarium test runs.** The venv loads luxaeterna through an editable-install meta-path finder that runs after the normal path search, so `PYTHONPATH` wins. Every mm-terrarium Python command from Task 2 on is prefixed:
  `LUX=/Users/chris/projects/luxaeterna/.claude/worktrees/rgbw-shroom-capability`
  `PYTHONPATH=$LUX .venv/bin/python ...`

## Review Focus

1. **A Room fixture frame width after `color_order` is gone.** TEST's 60 px fixtures must send 240 bytes, and the Tower 56. Pin with a test that the agent's per-fixture frame is `pixel_count * 4` (Task 4).
2. **A muted surface or blackout override.** `(0,0,0)` with level 0 must still produce all-zero RGBW bytes, including W. Pin it in the `solid_frame` tests (Task 2).
3. **A TOML `solid.rgb` with 4 values, or with 5.** Four is accepted as RGBW; five or two is refused with the located error. Pin both (Task 4).
4. **The Console showing a W-only frame.** `[0,0,0,255]` must draw white, not black. Pin it in the `pixels.js` tests (Task 5).
5. **An old 36-byte frame reaching the mm-tuneshroom session.** The decoder must produce the RGBW length (padded), never crash. Pin it in `frame_test` (Task 8).

---

### Task 1: luxaeterna, the Shroom capability becomes RGBW

**Files:**
- Modify: `luxaeterna/synth/capability.py:189-194`
- Modify: `README.md:23`, `docs/deployment.md:136-160`
- Test: `tests/synth/test_capability.py`, plus every test reading a 36-byte shroom frame: `tests/synth/test_engine.py:36,39,58,75`, `tests/synth/test_end_to_end.py:39-47`, `tests/synth/test_session.py:250-254`, `tests/synth/test_session_feed_midi.py:37`, `tests/backends/test_websim.py:14,24,27`

**Interfaces:**
- Produces: `shroom_capability(surface_id="ie0")` with `color_order == "RGBW"`, `pixel_count == 12`. `channels_for("RGBW") == 4`, so a player frame is 48 bytes. `SurfaceCapability.color_order` and `to_dmx_bytes(frame, color_order)` are unchanged.

- [ ] **Step 1: Create the worktree**

```bash
cd /Users/chris/projects/luxaeterna && git fetch -q && git worktree add -b claude/rgbw-shroom-capability .claude/worktrees/rgbw-shroom-capability origin/main
cd .claude/worktrees/rgbw-shroom-capability
```

- [ ] **Step 2: Write the failing test** (append to `tests/synth/test_capability.py`)

```python
def test_shroom_capability_is_rgbw_48_channels():
    from luxaeterna.synth.capability import shroom_capability
    from luxaeterna.synth.engine import channels_for
    cap = shroom_capability("ie7")
    assert cap.color_order == "RGBW"
    assert cap.pixel_count * channels_for(cap.color_order) == 48
```

- [ ] **Step 3: Run it to verify it fails**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/synth/test_capability.py -k rgbw -v`
Expected: FAIL, `assert 'GRB' == 'RGBW'`.

- [ ] **Step 4: Implement**

In `luxaeterna/synth/capability.py`:

```python
def shroom_capability(surface_id: str = "ie0") -> SurfaceCapability:
    """The canonical 12-LED Shroom: 8-LED ring + 4-LED stem, RGBW (the
    device wire, mm-terrarium contract v5: R, G, B, W per pixel)."""
    return SurfaceCapability(
        surface_id=surface_id, pixel_count=12, color_order="RGBW",
        zones=[Zone("ring", 0, 8), Zone("stem", 8, 4), Zone("primary", 0, 12)],
    )
```

- `README.md:23`: replace `(8-ring + 4-stem, GRB)` with `(8-ring + 4-stem, RGBW)`.
- `docs/deployment.md:136-160`: replace the sentence saying RGBW is "still open for the Tuneshroom" with: `The Tuneshroom wire is RGBW too since mm-terrarium contract v5 (2026-10-14).`

- [ ] **Step 5: Run the whole suite and fix the shroom-width assertions**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest -q`

Fix each failure with these rules. Do not touch tests that build their own `SurfaceCapability(..., "GRB", ...)`: those test the general mechanism and stay.
- `get_frame()[:36]` on a `shroom_capability()` universe becomes `get_frame()[:48]`.
- `tests/synth/test_end_to_end.py:44-47`: the frame is now RGBW, so byte 0 is red and byte 1 is green. Replace the GRB comment and index check with:

```python
    frame = uni.get_frame()[:48]
    # RGBW order: byte 0 = red, byte 1 = green. Red hue -> red channel dominant.
    assert frame[0] > frame[1]
```

  Keep any other existing assertion in that test, re-indexed the same way: red at `i*4`, green at `i*4+1`, blue at `i*4+2`, W at `i*4+3`.
- `tests/backends/test_websim.py:14`: if the test builds its backend from `shroom_capability()`, assert `msg["color_order"] == "RGBW"`. At `:24-27`, use `bytearray(range(48)) + bytearray(512 - 48)` and `bytes(range(48))`, with the comment `# 12 px * 4 = 48`.

Expected after fixes: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(capability): the Shroom surface is RGBW (mm-terrarium contract v5)"
```

---

### Task 2: mm-terrarium, the pixel wire module

**Files:**
- Create: `control/pixel_wire.py`
- Test: `tests/test_pixel_wire.py`

**Interfaces:**
- Produces:
  - `WIRE_ORDER: str = "RGBW"`
  - `CHANNELS_PER_PIXEL: int = 4`
  - `WHITE_W: tuple[int, int, int, int] = (0, 0, 0, 255)`
  - `rgbw(color: tuple) -> tuple[int, int, int, int]`: accepts a 3- or 4-tuple of ints. A 3-tuple gets W = 0. Any other length raises `ValueError("colour must be (r, g, b) or (r, g, b, w), got ...")`.
  - `solid_frame(color: tuple, level: float, length: int) -> bytes`: `length` bytes of one colour at `level`, clamped 0-255 per channel, and truncated if `length` is not a multiple of 4.

- [ ] **Step 1: Verify the luxaeterna override works**

Run: `LUX=/Users/chris/projects/luxaeterna/.claude/worktrees/rgbw-shroom-capability; PYTHONPATH=$LUX .venv/bin/python -c "import luxaeterna; print(luxaeterna.__file__)"`
Expected: a path under `.../rgbw-shroom-capability/luxaeterna/`. If it prints the main checkout, stop and report BLOCKED.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_pixel_wire.py
import pytest

from control.pixel_wire import (CHANNELS_PER_PIXEL, WHITE_W, WIRE_ORDER,
                                rgbw, solid_frame)


def test_the_wire_is_rgbw_four_per_pixel():
    assert WIRE_ORDER == "RGBW"
    assert CHANNELS_PER_PIXEL == 4
    assert WHITE_W == (0, 0, 0, 255)


def test_rgb_pads_w_zero_and_rgbw_passes_through():
    assert rgbw((1, 2, 3)) == (1, 2, 3, 0)
    assert rgbw((1, 2, 3, 4)) == (1, 2, 3, 4)


@pytest.mark.parametrize("bad", [(1, 2), (1, 2, 3, 4, 5), ()])
def test_rgbw_refuses_other_lengths(bad):
    with pytest.raises(ValueError, match="colour must be"):
        rgbw(bad)


def test_solid_frame_is_r_g_b_w_in_order():
    assert solid_frame((10, 20, 30), 1.0, 8) == bytes([10, 20, 30, 0] * 2)
    assert solid_frame(WHITE_W, 1.0, 8) == bytes([0, 0, 0, 255] * 2)


def test_solid_frame_scales_and_clamps():
    assert solid_frame((200, 0, 0, 100), 0.5, 4) == bytes([100, 0, 0, 50])
    assert solid_frame((255, 255, 255, 255), 2.0, 4) == bytes([255] * 4)


def test_blackout_is_all_zero_including_w():
    assert solid_frame((0, 0, 0), 0.0, 48) == bytes(48)
    assert solid_frame(WHITE_W, 0.0, 48) == bytes(48)


def test_solid_frame_truncates_to_length():
    assert solid_frame((1, 2, 3), 1.0, 6) == bytes([1, 2, 3, 0, 1, 2])
```

- [ ] **Step 3: Run to verify they fail**

Run: `PYTHONPATH=$LUX .venv/bin/python -m pytest tests/test_pixel_wire.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'control.pixel_wire'`.

- [ ] **Step 4: Implement**

```python
# control/pixel_wire.py
"""The one pixel wire every sink carries (contract v5, spec
docs/superpowers/specs/2026-10-14-rgbw-device-wire-design.md section 3):
R, G, B, W, one byte each. A strip's physical order is a driver setting
in firmware or WLED, never on this wire. W is authored, never derived:
an RGB colour has W = 0."""

from __future__ import annotations

WIRE_ORDER = "RGBW"
CHANNELS_PER_PIXEL = 4

# The light lexicon's white: the W die alone (spec section 4). An RGB-only
# device shows it through the fold.
WHITE_W = (0, 0, 0, 255)


def rgbw(color: tuple) -> tuple[int, int, int, int]:
    """(r, g, b) -> (r, g, b, 0); (r, g, b, w) unchanged."""
    if len(color) == 3:
        r, g, b = color
        return (r, g, b, 0)
    if len(color) == 4:
        r, g, b, w = color
        return (r, g, b, w)
    raise ValueError(f"colour must be (r, g, b) or (r, g, b, w), got {color!r}")


def solid_frame(color: tuple, level: float, length: int) -> bytes:
    """`length` bytes of one colour at `level`, R, G, B, W per pixel."""
    pixel = bytes(max(0, min(255, round(c * level))) for c in rgbw(color))
    reps = length // CHANNELS_PER_PIXEL + 1
    return (pixel * reps)[:length]
```

- [ ] **Step 5: Run to verify they pass**

Run: `PYTHONPATH=$LUX .venv/bin/python -m pytest tests/test_pixel_wire.py -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add control/pixel_wire.py tests/test_pixel_wire.py
git commit -m "feat(wire): control.pixel_wire, the RGBW pixel wire constants and solid frames"
```

---

### Task 3: Retire `color_order` from Room fixtures

**Files:**
- Modify: `control/room_profile.py:61-84` (RoomFixture), `:135-145` (validation), `:209-213` (`channel_count` docstring), `:215-219` (`color_order` property)
- Modify: `control/terrarium_config.py:612-660` (fixture parse), `:759-762` (Art-Net order refusal)
- Modify: `harness/room_surface.py:34,52`, `harness/websim_leds.py:13-41`, `harness/o2_shroom.py:470`, `harness/artnet_listen.py:87`
- Modify: `control/room_view.py:60,138`, `console/static/rooms.js:110`, `console/static/surface.js:595`
- Modify: `rooms/TEST.toml:6,27`, `rooms/TOWER.toml:6`, `rooms/DEMO.toml:6-7`, `rooms/VENUE.toml:6-7,51`, `terrarium.toml:19`
- Test: every test constructing `RoomFixture(... color_order=...)` or a room TOML with `color_order` (listed in Step 5), plus new tests in `tests/test_terrarium_config.py` and `tests/test_room_profile.py`

**Interfaces:**
- Consumes: `control.pixel_wire.WIRE_ORDER`, `CHANNELS_PER_PIXEL` (Task 2).
- Produces:
  - `RoomFixture(name, blocks, zones, instrument)`: there is no `color_order` field.
  - `RoomFixture.channels` always returns `CHANNELS_PER_PIXEL`.
  - `RoomProfile.color_order` is deleted. Every caller uses `WIRE_ORDER`.
  - `capability_view(profile)` and `fixtures_view(...)` dicts have no `color_order` key.
  - A room TOML fixture table with a `color_order` key raises `TerrariumConfigError` with message `fixture <label> sets color_order, which is retired (contract v5: every fixture is RGBW on the wire; set the strip's physical order in firmware or WLED). Delete the key.`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium_config.py`. `MINIMAL` is the existing base TOML string in that file; it already contains a `color_order = "GRB"` line, which Step 5 deletes.

```python
def test_a_fixture_with_color_order_is_refused_with_a_located_error(tmp_path):
    from control.terrarium_config import TerrariumConfigError, load_terrarium_config
    text = MINIMAL.replace('name = "main"\n', 'name = "main"\ncolor_order = "GRB"\n', 1)
    path = tmp_path / "terrarium.toml"
    path.write_text(text)
    with pytest.raises(TerrariumConfigError, match="color_order, which is retired"):
        load_terrarium_config(str(path))


def test_every_fixture_is_four_channels():
    from control.pixel_wire import CHANNELS_PER_PIXEL
    from control.terrarium_config import load_terrarium_config
    rooms = load_terrarium_config("terrarium.toml").rooms
    for room in rooms.values():
        for fixture in room.profile.fixtures:
            assert fixture.channels == CHANNELS_PER_PIXEL
            assert not hasattr(fixture, "color_order")
```

If `MINIMAL`'s first fixture is not literally `name = "main"\n`, adapt the `replace` anchor to its first fixture's name line; read `tests/test_terrarium_config.py:20-40` first.

Append to `tests/test_room_profile.py`:

```python
def test_channel_count_is_pixels_times_four():
    profile = RoomProfile(surface_id="room_x", fixtures=(
        _fixture(name="a", blocks=(RoomBlock("a", 0, 10),)),
        _fixture(name="b", blocks=(RoomBlock("b", 0, 5),))))
    assert profile.channel_count == 15 * 4
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=$LUX .venv/bin/python -m pytest tests/test_terrarium_config.py tests/test_room_profile.py -k "retired or four_channels or times_four" -v`
Expected: FAIL. The first test does not raise; the other two fail on `channels == 3` or a `color_order` attribute.

- [ ] **Step 3: Implement the model and loader**

`control/room_profile.py`: delete the `color_order: str` field. Replace the `channels` property:

```python
    @property
    def channels(self) -> int:
        """Channels per pixel on this fixture's wire: always 4, R, G, B, W
        (contract v5, control/pixel_wire.py)."""
        return CHANNELS_PER_PIXEL
```

with `from control.pixel_wire import CHANNELS_PER_PIXEL` at the top. In `RoomProfile.__post_init__` delete the permutation loop and the mixed-order check (`:135-145`). Delete the `color_order` property (`:215-219`). Change `channel_count`'s docstring to `each fixture's pixels times 4 (R, G, B, W)`.

`control/terrarium_config.py`, in the fixture loop before the `try:` that builds `RoomFixture` (`~:641`):

```python
        if "color_order" in fraw:
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"fixture {label} sets color_order, which is retired "
                        f"(contract v5: every fixture is RGBW on the wire; set "
                        f"the strip's physical order in firmware or WLED). "
                        f"Delete the key.")
```

and drop `color_order=fraw["color_order"],` from the `RoomFixture(...)` call. Delete the Art-Net `if fixture.color_order != "RGBW":` block (`:759-762`).

- [ ] **Step 4: Implement the callers**

- `harness/room_surface.py:34` and `:52`: `color_order=WIRE_ORDER,` with `from control.pixel_wire import WIRE_ORDER`.
- `harness/websim_leds.py`:
  - Update the palette comment to `laid out R, G, B, W (W = 0) when painted`.
  - In `identify_blocks_frame`, replace the order logic with:

```python
    fixture = next(f for f in profile.fixtures if f.name == fixture_name)
    width = CHANNELS_PER_PIXEL
    frame = bytearray(fixture.pixel_count * width)
    for i, block in enumerate(fixture.blocks):
        px = bytes(rgbw(BLOCK_PALETTE[i % len(BLOCK_PALETTE)]))
        frame[block.start * width:(block.start + block.count) * width] = \
            px * block.count
    return bytes(frame)
```

  with `from control.pixel_wire import CHANNELS_PER_PIXEL, rgbw`.
  - Update the `WebSimLeds` docstring's widths to `60 px x 4 = 240` and `12 px x RGBW = 48`.
- `harness/o2_shroom.py:470`: `channels = capability.pixel_count * CHANNELS_PER_PIXEL` (import from `control.pixel_wire`).
- `harness/artnet_listen.py:87`: `color_order=WIRE_ORDER,`.
- `control/room_view.py:60` and `:138`: delete the `"color_order": ...` entries.
- `console/static/rooms.js:110`: `` `${cap.pixel_count} px · RGBW` ``.
- `console/static/surface.js:595`: `` `${room.capability.pixel_count} px · RGBW` ``.
- `rooms/TEST.toml`, `rooms/TOWER.toml`, `rooms/DEMO.toml`, `rooms/VENUE.toml`: delete every `color_order = ...` line. Where a comment explained the order (DEMO and VENUE lines 6-7), replace it with `# Wire is RGBW (contract v5); WLED's own LED settings carry the strip's physical GRBW.`
- `terrarium.toml:19`: delete the `The fixture must be color_order = "RGBW".` sentence.

- [ ] **Step 5: Convert the test fixtures mechanically**

Run this once from the worktree root. It removes the keyword argument and TOML lines; it does not touch JS `toml_edit` tests, which test text editing of arbitrary keys.

```bash
PYTHONPATH=$LUX .venv/bin/python - <<'EOF'
import pathlib, re
for p in pathlib.Path("tests").glob("*.py"):
    s = p.read_text()
    t = re.sub(r'color_order\s*=\s*"[A-Z]+",\s*', "", s)        # kwarg in RoomFixture(...)
    t = re.sub(r'(?m)^[ \t]*color_order = "[A-Z]+"\n', "", t)    # TOML line inside a string
    t = t.replace('\\ncolor_order = "GRB"', "")                  # inline "\n" TOML snippets
    if t != s:
        p.write_text(t); print("converted", p)
EOF
```

Then fix these by hand. Each asserted on the retired key:
- `tests/test_room_profile.py`:
  - Delete `_fixture`'s `color_order` parameter (`:34-38`).
  - Delete `test_color_order_is_the_shared_order` (`:110-111`), `test_mixed_color_order_across_fixtures_is_rejected` (`:207-211`), and the parametrized `test_a_color_order_that_is_not_a_permutation_...` (`:374-377`).
  - At `:162` assert `array.channels == 4`.
- `tests/test_terrarium_config.py`:
  - `:113,118,131`: assert `.channels == 4`.
  - `:824`: `ARTNET_BASE = MINIMAL.replace(...)` no longer needs the order swap; drop that `.replace` call.
  - Delete any test asserting the Art-Net non-RGBW refusal (search `needs color_order`).
- `tests/test_venue_room.py:43`: `assert all(f.channels == 4 for f in VENUE.fixtures)`.
- `tests/test_tower_room.py:18`: `assert fx.channels == 4`.
- `tests/test_room_surface.py:33`: `assert cap.color_order == "RGBW"`.
- `tests/test_room_view.py`: at `:195`, assert `"color_order" not in view["capability"]`. Delete `test_fixtures_view_carries_each_fixtures_color_order` (`:311-315`).
- `tests/test_devicelink_agent.py:182`: `len(self.cap.color_order)` stays valid, because capabilities still carry `"RGBW"`.
- `tests/test_run_stack.py:784`: the inline TOML loses `color_order = "GRB"\n` (the script handles the `\n` form; check it).
- JS tests whose `capability` objects carry `color_order: "GRB"` (`tests/js/rooms_panel.test.js`, `surface_panel.test.js`, `fixture_*.test.js`, `triggers_ux.test.js`, `diagnostics_row.test.js`, `full_stack.test.js`, `functions_and_rail.test.js`) only need edits where an assertion reads the header text: change the expected `"60 px · GRB"` to `"60 px · RGBW"`. Leave unasserted fields alone.

- [ ] **Step 6: Run the full suites**

Run: `PYTHONPATH=$LUX .venv/bin/python -m pytest tests -q` and `node --test tests/js/*.test.js`
Expected: all pass, except frame-width tests in devicelink, harness and contract that Tasks 4 to 6 own. List any remaining failures in the task report with their test names. Do not fix frame-width failures here.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(rooms): retire color_order; every Room fixture is RGBW on the wire"
```

---

### Task 4: Control, the agent, lobby and Testshroom at RGBW

**Files:**
- Modify: `devicelink/agent.py:65-69` (`_DEVICE_CHANNELS`), `:930-955` (`_apply_override`, `_solid_frame`), `:957-959` (`_on_solid_cue` type), `:1092-1094`, `:1179-1180`, `:1204-1250` (order arguments)
- Modify: `control/lobby.py:59`, `control/builtins.py:36`, `control/cues.py:127-140` (SolidCue docstring and type), `control/terrarium_config.py:291-299` (`solid.rgb`)
- Modify: `devicelink/protocol.py:177-190`, `devicelink/o2_transport.py:82`, `harness/beat_tapper.py:35`, `harness/shroom_client.py:36,61-67,90-113`
- Test: `tests/test_devicelink_agent.py`, `tests/test_lobby_frames.py`, `tests/test_lobby_agent.py`, `tests/test_device_bridge.py`, `tests/test_terrarium_config.py`, `tests/test_builtins.py`, `tests/test_o2_transport.py`, `tests/test_beat_tapper.py`, `tests/test_websim_leds.py`

**Interfaces:**
- Consumes: `control.pixel_wire.solid_frame`, `rgbw`, `WHITE_W`, `CHANNELS_PER_PIXEL` (Task 2). Fixtures without `color_order` (Task 3).
- Produces:
  - `_DEVICE_CHANNELS = 48`.
  - `DeviceLinkAgent._apply_override(dev, frame) -> bytes` and `DeviceLinkAgent._solid_frame(color, level, length) -> bytes` lose their order parameter.
  - `SolidCue.rgb: tuple` accepts 3 or 4 values (the field name is kept).
  - `control.lobby.WHITE == (0, 0, 0, 255)`.
  - The builtin `flash` function's SolidCue colour is `(0, 0, 0, 255)`.
  - TOML `solid.rgb` accepts 3 or 4 ints.
  - `harness.shroom_client.LED_CHANNELS = 48`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_devicelink_agent.py`:

```python
def test_player_frames_are_48_bytes_rgbw():
    from devicelink import agent as agent_mod
    assert agent_mod._DEVICE_CHANNELS == 48


def test_solid_frame_has_no_order_and_writes_w():
    from devicelink.agent import DeviceLinkAgent
    assert DeviceLinkAgent._solid_frame((0, 255, 0), 1.0, 8) == bytes([0, 255, 0, 0] * 2)
    assert DeviceLinkAgent._solid_frame((0, 0, 0, 255), 1.0, 8) == bytes([0, 0, 0, 255] * 2)
```

Append to `tests/test_lobby_frames.py`:

```python
def test_lobby_white_is_the_w_die():
    from control.lobby import GREEN, RED, WHITE
    assert WHITE == (0, 0, 0, 255)
    assert GREEN == (0, 255, 0) and RED == (255, 0, 0)
```

Append to `tests/test_terrarium_config.py`:

```python
@pytest.mark.parametrize("rgb,ok", [([1, 2, 3], True), ([1, 2, 3, 4], True),
                                    ([1, 2], False), ([1, 2, 3, 4, 5], False)])
def test_solid_rgb_takes_three_or_four_values(rgb, ok):
    from control.terrarium_config import TerrariumConfigError, _parse_script_step
    raw = {"offset": 0.0, "solid": {"rgb": rgb, "level": 1.0}}
    if ok:
        step = _parse_script_step("i", "f", 0, raw, source="t", key="k")
        assert tuple(step.cue.rgb) == tuple(rgb)
    else:
        with pytest.raises(TerrariumConfigError, match=r"solid.rgb must be"):
            _parse_script_step("i", "f", 0, raw, source="t", key="k")
```

`_parse_script_step(iname, fname, idx, sraw, *, source, key)` is at `control/terrarium_config.py:267`. If the step kind is chosen by a key other than `solid` being present, read `:267-300` and match it.

Append to `tests/test_builtins.py` (it already defines `_inst(caps)`, building an `Instrument` from a capability set):

```python
def test_builtin_flash_is_w_white():
    fn = builtin_functions(_inst({"light.pixels"}))["flash"]
    solids = [s.cue for s in fn.script if isinstance(s.cue, SolidCue)]
    assert solids and solids[0].rgb == (0, 0, 0, 255)
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=$LUX .venv/bin/python -m pytest tests/test_devicelink_agent.py tests/test_lobby_frames.py tests/test_terrarium_config.py -k "48_bytes or no_order or w_die or three_or_four or w_white" -v`
Expected: FAIL on 36, the order argument, `(255, 255, 255)`, and the 4-value rgb.

- [ ] **Step 3: Implement**

`devicelink/agent.py`:

```python
# One player device's frame width: 12 pixels x 4 channels, R, G, B, W
# (contract v5, control/pixel_wire.py). The same figure
# harness/shroom_client.py calls LED_CHANNELS.
_DEVICE_CHANNELS = 12 * CHANNELS_PER_PIXEL
```

```python
    def _apply_override(self, dev: str, frame: bytes) -> bytes:
        """Paint every pixel of `frame` the override's colour, R, G, B, W.
        A colour named (r, g, b) has W = 0; the lexicon's white is
        (0, 0, 0, 255)."""
        entry = self._overrides.get(dev)
        if entry is None:
            return frame
        color, level, _expires = entry
        return self._solid_frame(color, level, len(frame))

    @staticmethod
    def _solid_frame(color, level: float, length: int) -> bytes:
        """`length` bytes of one colour at `level`, R, G, B, W."""
        return solid_frame(color, level, length)
```

- Import `from control.pixel_wire import CHANNELS_PER_PIXEL, solid_frame`.
- Change `_on_solid_cue`'s `rgb: tuple[int, int, int]` to `rgb: tuple`.
- Remove the third argument at every `_apply_override(...)` and `_solid_frame(...)` call (`:1093-1094`, `:1211`, `:1246-1249`), and delete `order = (...)` at `:1179-1180`.
- Update the comment at `:1230-1231` from "36-channel player frame" to "48-channel player frame".

`control/lobby.py:59`: `WHITE = WHITE_W` with `from control.pixel_wire import WHITE_W`, plus the comment `# The lexicon's white is the W die alone (contract v5).`

`control/builtins.py:36`: `SolidCue(TARGET, WHITE_W, 0.9, 5.0)` (import `WHITE_W`), and change the description to `"Light test: solid white (the W die) for 5 s, then resume"`.

`control/cues.py`, SolidCue: change the field to `rgb: tuple` and add this sentence to its docstring: `` `rgb` is (r, g, b) or (r, g, b, w); three values mean W = 0 (control/pixel_wire.py). ``

`control/terrarium_config.py:295-299`:

```python
        rgb = v.get("rgb")
        if (not isinstance(rgb, list) or len(rgb) not in (3, 4)):
            raise err(f"solid.rgb must be [r, g, b] or [r, g, b, w], got {rgb!r}")
```

`devicelink/protocol.py:177-190`: change the docstring width to `48 (12 pixels x R, G, B, W) for a player device; a Room-bound device's frames are its fixture's pixel count x 4`.

`devicelink/o2_transport.py:82` and `harness/beat_tapper.py:35`: change "36" to "48" in the comments.

`harness/shroom_client.py`:
- Module docstring row: `down  /<dev>/leds    b    [[48 ints]]`.
- Replace the comment above `LED_CHANNELS` (`:61-66`) and the constant:

```python
# 12 pixels x R, G, B, W, per protocol.leds_event (contract v5): the
# Testshroom's own declared surface shape.
LED_CHANNELS = 48
```

- Change the `expected_channels` comment (`:90-96`) to say `12 px x RGBW` and `pixel_count times 4`.

- [ ] **Step 4: Run the new tests**

Same command as Step 2. Expected: PASS.

- [ ] **Step 5: Convert the frame-shape tests**

Run: `PYTHONPATH=$LUX .venv/bin/python -m pytest tests -q -x --ignore=tests/test_contract_scenarios.py --ignore=tests/test_contract_recorder.py --ignore=tests/test_export_contract.py`

Fix each failure by these rules:
- Player widths: `36` becomes `48`. `* 3` on a player or fixture frame becomes `* 4`.
- GRB-strided reads such as `frame[1::3]` (red) become `frame[0::4]`. Green `frame[0::3]` becomes `frame[1::4]`, blue `frame[2::3]` becomes `frame[2::4]`, and W is `frame[3::4]`.
- Expected solid pixels: a GRB `[0, 255, 0]` meaning red becomes `[255, 0, 0, 0]`. An expected white `[255, 255, 255]` from the lobby becomes `[0, 0, 0, 255]`.
- Known sites: `test_device_bridge.py:52`, `test_lobby_frames.py:49`, `test_lobby_agent.py:187-196,496,513`, `test_devicelink_agent.py:1691,2484`, `test_o2_transport.py:158,771`, `test_beat_tapper.py:5`, `test_websim_leds.py:66`. Others may surface; apply the same rules.

Also add the Review Focus test for fixture width to `tests/test_devicelink_agent.py`. Model it on the existing fixture-frame test at `:1063-1100`, which builds an agent with `RoomFixture(name="main", ...)` and captures sends. Assert that a 60 px fixture's `/leds` payload is 240 bytes:

```python
    assert len(fixture_frame) == 60 * 4
```

- [ ] **Step 6: Run the suites**

Run: `PYTHONPATH=$LUX .venv/bin/python -m pytest tests -q --ignore=tests/test_contract_scenarios.py --ignore=tests/test_contract_recorder.py --ignore=tests/test_export_contract.py` and `node --test tests/js/*.test.js`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(devicelink): 48-byte RGBW player frames; lexicon white drives the W die"
```

---

### Task 5: Console, one RGBW decoder with the fold

**Files:**
- Create: `console/static/pixels.js`
- Modify: `console/static/surface.js:716-745` (`_decodePixels`, `onRoomFrame`), `console/static/design.js:182-199` (`paintBenchFrame`)
- Test: `tests/js/pixels.test.js` (new), `tests/js/surface_panel.test.js:60-64`, `tests/js/design_bench.test.js:37-42`

**Interfaces:**
- Produces: `export function decodeRgbw(channels) -> Array<[r, g, b]>`, with 4 channels per pixel and the fold applied. A trailing partial pixel is dropped.

- [ ] **Step 1: Write the failing test**

```js
// tests/js/pixels.test.js
import { test } from "node:test";
import assert from "node:assert/strict";
import { decodeRgbw } from "../../console/static/pixels.js";

test("RGBW decodes in order with W folded into r, g, b", () => {
  assert.deepStrictEqual(decodeRgbw([10, 20, 30, 0]), [[10, 20, 30]]);
  assert.deepStrictEqual(decodeRgbw([10, 20, 30, 5]), [[15, 25, 35]]);
  assert.deepStrictEqual(decodeRgbw([10, 0, 0, 250]), [[255, 250, 250]]);
});

test("a W-only frame draws white, not black", () => {
  assert.deepStrictEqual(decodeRgbw([0, 0, 0, 255, 0, 0, 0, 255]),
    [[255, 255, 255], [255, 255, 255]]);
});

test("a trailing partial pixel is dropped and missing values read 0", () => {
  assert.deepStrictEqual(decodeRgbw([1, 2, 3, 4, 9, 9]), [[5, 6, 7]]);
  assert.deepStrictEqual(decodeRgbw([]), []);
});
```

Check how existing `tests/js/*.test.js` import console modules (relative path and whether a DOM stub is loaded first) and match it.

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/pixels.test.js`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```js
// console/static/pixels.js
// The one pixel wire (contract v5): R, G, B, W per pixel. A screen has no
// white die, so W folds into r, g and b, clipped at 255, exactly as an
// RGB-only device shows it (spec 2026-10-14 rgbw-device-wire section 3.2).
export function decodeRgbw(channels) {
  const n = Math.floor(channels.length / 4);
  const out = [];
  for (let i = 0; i < n; i++) {
    const w = channels[i * 4 + 3] || 0;
    out.push([
      Math.min(255, (channels[i * 4] || 0) + w),
      Math.min(255, (channels[i * 4 + 1] || 0) + w),
      Math.min(255, (channels[i * 4 + 2] || 0) + w),
    ]);
  }
  return out;
}
```

`console/static/surface.js`: delete `_decodePixels` and its comment block (`:716-731`), add `import { decodeRgbw } from "./pixels.js";` beside the other imports, and in `onRoomFrame` replace the fixture lookup and decode with:

```js
  const pixels = decodeRgbw(msg.channels || []);
```

Delete the now-unused `fixture` const.

`console/static/design.js`: import `decodeRgbw` and replace `paintBenchFrame`'s body loop:

```js
// Paints one filled rect per pixel from a flat RGBW channel list, with the
// same fold as the Room strips (pixels.js).
export function paintBenchFrame(canvas, channels) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width || canvas.clientWidth || 0;
  const h = canvas.height || canvas.clientHeight || 0;
  ctx.clearRect(0, 0, w, h);
  const pixels = decodeRgbw(channels);
  if (pixels.length === 0) return;
  const pitch = w / pixels.length;
  pixels.forEach(([r, g, b], i) => {
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(pitch * i, 0, pitch, h);
  });
}
```

- [ ] **Step 4: Update the two existing tests**

`tests/js/surface_panel.test.js:60-64`: delete the four `surface._decodePixels` assertions and their comment; `pixels.test.js` covers decoding now.

`tests/js/design_bench.test.js:37-42`:

```js
  // -- paintBenchFrame: one filled rect per pixel, RGBW with W folded ------
  const paintCanvas = document.createElement("canvas");
  design.paintBenchFrame(paintCanvas, [10, 20, 30, 0, 40, 50, 60, 5]);
  const ctx = paintCanvas.getContext("2d");
  assert.strictEqual(ctx.calls.length, 2);
  assert.strictEqual(ctx.calls[0].fillStyle, "rgb(10,20,30)");
  assert.strictEqual(ctx.calls[1].fillStyle, "rgb(45,55,65)");
```

- [ ] **Step 5: Run the JS suite**

Run: `node --test tests/js/*.test.js`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(console): one RGBW decoder with the W fold for Room strips and the bench"
```

---

### Task 6: Contract kit and export at v5, plus docs

**Files:**
- Modify: `contract_kit/scenarios.py:113-120,343-349,445-447`, `contract_kit/recorder.py:765-796`
- Modify: `tools/export_contract.py:55-70,233-239,333-338,440-455`
- Re-record: `contract_kit/recordings/*.json` via `tools.record_scenarios`
- Modify: `docs/device-contract-guide.md`, `docs/light-lexicon.md`, `docs/MM_TERRARIUM.md`
- Test: `tests/test_contract_scenarios.py`, `tests/test_contract_recorder.py:427-497`, `tests/test_export_contract.py`

**Interfaces:**
- Consumes: Tasks 2 to 4 (Control now emits RGBW).
- Produces:
  - `CONTRACT_VERSION = 5`.
  - Every `expect_frame` is `{"rgbw": [48 ints]}`.
  - `AUTHORED_NEWER_RGBW = [0, 0, 255, 0] * 12` (pure blue) and `AUTHORED_OLDER_RGBW = [255, 0, 0, 0] * 12` (pure red).

- [ ] **Step 1: Write the failing test** (append to `tests/test_export_contract.py`)

```python
def test_v5_every_expect_frame_is_48_rgbw(tmp_path):
    import json
    from tools.export_contract import CONTRACT_VERSION, main as export_main
    assert CONTRACT_VERSION == 5
    export_main([str(tmp_path)])   # tools/export_contract.py:654, writes scenarios/ under it
    for path in (tmp_path / "scenarios").glob("*.json"):
        for step in json.loads(path.read_text())["steps"]:
            ef = step.get("expect_frame")
            if ef is not None:
                assert set(ef) == {"rgbw"}, path.name
                assert len(ef["rgbw"]) == 48, path.name
```

`main(argv)` takes the output directory and writes `scenarios/<name>.json`, each with a top-level `steps` list (`tools/export_contract.py:640-658`, `contract_kit/recorder.py:909`).

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=$LUX .venv/bin/python -m pytest tests/test_export_contract.py -k v5 -v`
Expected: FAIL, `assert 4 == 5`.

- [ ] **Step 3: Implement**

`contract_kit/scenarios.py`:
- Rename the authored constants to `AUTHORED_NEWER_RGBW = [0, 0, 255, 0] * 12` and `AUTHORED_OLDER_RGBW = [255, 0, 0, 0] * 12`.
- Change the comment above them to `(pure blue and pure red, R, G, B, W order)`.
- Update their two uses at `:445-447`.
- In the `room_node_handshake_binds` docstring (`:343-349`): `60 px RGBW, 240 values` and `a normal 48-value frame`.

`contract_kit/recorder.py`: in both `expect_frame` and `expect_frame_held`, the key `"grb"` becomes `"rgbw"`.

`tools/export_contract.py`:
- Append to the version comment: `RGBW pixel frames (spec 2026-10-14 rgbw-device-wire): every /leds frame is R, G, B, W, 4 per pixel, and expect_frame.grb becomes expect_frame.rgbw, so it bumps to 5.`
- Set `CONTRACT_VERSION = 5`.
- `:233-239`: `leds args[0] is a list of ints (0-255), R, G, B, W, 4 per pixel: 48 ints (12 pixels) for a player device, but a Room-bound device's frames ... are its fixture's pixel count x 4`.
- `:333-338`: change the field key to `"rgbw"` with the description `"list of 48 ints, 0-255: 12 pixels x 4 channels, red-green-blue-white order. A player device's own frame width; no scenario asserts a Room-bound frame, so a runner must not apply the 48-value check to one."`
- `:440-455`: the replay note says `(pure blue, RGBW [0, 0, 255, 0] repeated 12 times)` and `(pure red, RGBW [255, 0, 0, 0] repeated 12 times)`.

- [ ] **Step 4: Re-record the scenarios**

Run: `PYTHONPATH=$LUX .venv/bin/python -m tools.record_scenarios`
Then: `git diff --stat contract_kit/recordings`
Expected: the 14 recordings that carry frames change. Spot-check one with `git diff contract_kit/recordings/gestures_after_role.json | head -40`: `grb` is now `rgbw`, with 48 values per frame and white flashes as `0,0,0,255` repeats.

- [ ] **Step 5: Fix the contract tests**

`tests/test_contract_recorder.py:427-497`: change `len(...) == 36` to `48` and `"grb"` to `"rgbw"`. `tests/test_contract_scenarios.py` compares against the re-recorded files; fix any hardcoded `grb` key or GRB frame literal by the Task 4 Step 5 rules.

Run: `PYTHONPATH=$LUX .venv/bin/python -m pytest tests -q` and `node --test tests/js/*.test.js`
Expected: all pass.

- [ ] **Step 6: Docs**

`docs/device-contract-guide.md`:
- `:116` and `:158`: `expect_frame` (`rgbw`, 48 ints).
- `:212`, the `/leds` row: ``b` RGBW frame: R, G, B, W per pixel, a player device's own pixel count x 4 (48 bytes for the 12 px Rev 1)`.
- Rule 8: replace `x 3 (36 bytes for Rev 1)` with `x 4 (48 bytes for Rev 1)`.
- Rule 9: add `Draw Looking's white on the W channel.`
- New rule after 9, numbered so the later rules shift by one and every cross-reference is updated: **Fold W if you have no white die.** `A device whose LEDs have no white die shows each pixel as r + w, g + w, b + w, each clipped at 255. The physical strip order (GRB, GRBW) is your driver's setting; the wire is always R, G, B, W.`
- Add a firmware checklist item: `Decode /leds as R, G, B, W, 4 per pixel; fold W on RGB-only LEDs; replay export v5.`

`docs/light-lexicon.md`: in section 2, add `"White" in this lexicon is the W channel at the stated level: (0, 0, 0, 255) scaled. A device with no white die shows it by folding W into R, G and B (device-contract-guide rule 10).`

`docs/MM_TERRARIUM.md`:
- Delete the *Not yet built* bullet "The Tuneshroom LED wire cannot reach the white die".
- In the Status block, add: `Pixel frames are RGBW on every sink (contract v5, 2026-10-14); mm-tuneshroom and mm-devshroom adoption pending.`
- Rewrite the Room colour-order paragraph (`~:1158-1173`): `color_order` is retired, every fixture is 4 channels, and a TOML that sets it is refused.
- In the message table (`~:1481`), the leds width becomes 48.
- Replace the `design.js` GRB note (`~:2077-2078`) with `paintBenchFrame decodes RGBW through console/static/pixels.js`.
- In the contract-kit section, `CONTRACT_VERSION (5)` gets a sentence for v5.
- Run `.venv/bin/python -m tools.render_diagrams --check` if the doc's diagrams mention widths; if it reports drift, run it without `--check` and commit the output.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(contract): v5, RGBW pixel frames; re-recorded scenarios; guide, lexicon and deep-dive"
```

---

### Task 7: mm-terrarium live check

**Files:** none (verification only).

- [ ] **Step 1: Boot TEST with a Testshroom for 45 s**

Run (background, with job control as the deep-dive requires):

```bash
LUX=/Users/chris/projects/luxaeterna/.claude/worktrees/rgbw-shroom-capability
set -m; PYTHONPATH=$LUX ./smoke-test.sh --room TEST --devices 1 --ci --seconds 45 > "$SCRATCH/rgbw-smoke.log" 2>&1; echo "exit $?"
```

`$SCRATCH` is the session scratchpad. `smoke-test.sh` passes arguments through to `harness.run_stack`; confirm the flags with `PYTHONPATH=$LUX .venv/bin/python -m harness.run_stack --help` first. The script resolves `PYTHONPATH` itself when it is unset, so check in the log that it kept the `$LUX` value. If it overwrote it, report BLOCKED rather than editing the script.
Expected: exit 0, `room loaded: TEST`, and the sim device's hello in the log.

- [ ] **Step 2: Check the frames in the run log**

Run: `grep -rn "leds" runs/$(ls -t runs | head -1)/*.log | head -20`
Expected: player frames of 48 values. If an invite flash was logged, its pixels are `0,0,0,255`. Record what you saw in the report. If the logs do not show payloads, say so; do not claim it.

---

### Task 8: mm-tuneshroom, `RgbwFrame` and contract v5

**Files:**
- Create worktree: `/Users/chris/projects/mm-tuneshroom/.claude/worktrees/rgbw-frames` on `claude/rgbw-frames` from `origin/main`
- Modify: `lib/render/frame.dart`, `lib/render/looking.dart`, `lib/render/ambient.dart:29-45`, `lib/render/cue_player.dart:51-90`, `lib/host/device_session.dart` (`GrbFrame` references), `lib/sim/sim_controller.dart:98-110,501-503`
- Replace: `test/contract/` with a fresh export from mm-terrarium
- Modify: `test/contract_replay_test.dart:154-189,257`, `test/contract_export_test.dart:39`, `test/replay/scenario_runner.dart:444-449`, `test/render_cue_player_test.dart:54,87-88`, `test/render_looking_test.dart`, `test/render_ambient_test.dart`
- Test: `test/render_frame_test.dart` (new)

**Interfaces:**
- Consumes: the v5 export from Task 6.
- Produces:
  - `typedef RgbwFrame = List<int>;`
  - `blackFrame(int pixelCount)` returns `pixelCount * 4` zeros.
  - `decodeWireFrame(List<dynamic>, int pixelCount)` returns `pixelCount * 4`, padded or truncated.
  - `foldToRgb(RgbwFrame f) -> List<(int, int, int)>`.
  - `lookingFrame(...)` lights W only.
  - `SimController.frame` is an `RgbwFrame`.

- [ ] **Step 1: Create the worktree**

```bash
cd /Users/chris/projects/mm-tuneshroom && git fetch -q && git worktree add -b claude/rgbw-frames .claude/worktrees/rgbw-frames origin/main && cd .claude/worktrees/rgbw-frames
```

- [ ] **Step 2: Write the failing tests**

```dart
// test/render_frame_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:mm_shrooms_app/render/frame.dart';
import 'package:mm_shrooms_app/render/looking.dart';

void main() {
  test('frames are R, G, B, W, four per pixel', () {
    expect(blackFrame(12).length, 48);
    expect(decodeWireFrame(List<int>.filled(48, 7), 12).length, 48);
  });

  test('an old 36-byte frame pads to the RGBW length, never throws', () {
    final f = decodeWireFrame(List<int>.filled(36, 9), 12);
    expect(f.length, 48);
    expect(f.sublist(36), List<int>.filled(12, 0));
  });

  test('the fold adds W into r, g and b, clipped at 255', () {
    expect(foldToRgb([10, 20, 30, 5]), [(15, 25, 35)]);
    expect(foldToRgb([10, 0, 0, 250]), [(255, 250, 250)]);
    expect(foldToRgb([0, 0, 0, 255]), [(255, 255, 255)]);
  });

  test('Looking lights the W channel only', () {
    final f = lookingFrame(0, 2000, 2);   // the pulse peak, half a period in
    expect([f[0], f[1], f[2], f[4], f[5], f[6]], everyElement(0));
    expect(f[3], greaterThan(0));
    expect(f[7], f[3]);
  });
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `flutter test test/render_frame_test.dart`
Expected: FAIL on 36 versus 48, and `foldToRgb` undefined.

- [ ] **Step 4: Implement**

`lib/render/frame.dart` (whole file):

```dart
// Frames are flat RGBW byte lists, exactly the /ie<N>/leds wire shape
// (mm-terrarium contract v5): R, G, B, W per pixel, so server frames and
// locally rendered frames are interchangeable at the painter.

/// Flat RGBW channel list, length = pixelCount * 4, values 0-255.
typedef RgbwFrame = List<int>;

const int kChannelsPerPixel = 4;

RgbwFrame blackFrame(int pixelCount) =>
    List<int>.filled(pixelCount * kChannelsPerPixel, 0);

/// Decode a wire channel list defensively: clamp to 0-255, treat non-numbers
/// as 0, and force the length to exactly [pixelCount] * 4 (padding short
/// frames with black, truncating long ones).
RgbwFrame decodeWireFrame(List<dynamic> channels, int pixelCount) {
  int at(int i) {
    if (i >= channels.length) return 0;
    final raw = channels[i];
    final value = raw is num ? raw.toInt() : 0;
    return value.clamp(0, 255);
  }

  return List<int>.generate(pixelCount * kChannelsPerPixel, at);
}

/// A screen has no white die: W folds into r, g and b, clipped at 255,
/// exactly as an RGB-only device shows it (contract v5).
List<(int, int, int)> foldToRgb(RgbwFrame f) => [
      for (var i = 0; i + 3 < f.length; i += kChannelsPerPixel)
        (
          (f[i] + f[i + 3]).clamp(0, 255),
          (f[i + 1] + f[i + 3]).clamp(0, 255),
          (f[i + 2] + f[i + 3]).clamp(0, 255),
        ),
    ];
```

`lib/render/looking.dart`:

```dart
/// The lexicon's white (the W die) at the raised-cosine level for
/// [nowMs] - [sinceMs], every pixel.
RgbwFrame lookingFrame(int sinceMs, int nowMs, int pixelCount) {
  final t = nowMs - sinceMs;
  final level = t <= 0
      ? 0.0
      : kLookingPeak * (1 - cos(2 * pi * t / kLookingPeriodMs)) / 2;
  final v = (255 * level).round();
  final frame = blackFrame(pixelCount);
  for (var i = 0; i < pixelCount; i++) {
    frame[i * kChannelsPerPixel + 3] = v;
  }
  return frame;
}
```

Also update its header comment: `a slow white pulse on the W channel`.

`lib/render/ambient.dart:29-45`: write in RGBW order:

```dart
    for (final i in zone) {
      final base = i * kChannelsPerPixel;
      frame[base] = r;
      frame[base + 1] = g;
      frame[base + 2] = b;
    }
```

Keep the `g`, `r`, `b` computations above it. W stays 0 because the frame starts black.

`lib/render/cue_player.dart:86-89`:

```dart
      final basei = i * kChannelsPerPixel;
      frame[basei] = (r * 255).clamp(0, 255).round();
      frame[basei + 1] = (g * 255).clamp(0, 255).round();
      frame[basei + 2] = (b * 255).clamp(0, 255).round();
```

Fix any `GrbFrame` mention in that file's header comment.

Everywhere else, rename `GrbFrame` to `RgbwFrame`:

```bash
grep -rl "GrbFrame" lib test | xargs sed -i '' 's/GrbFrame/RgbwFrame/g'
```

Then fix the comments that say "GRB" (`grep -rn "GRB" lib`), excluding the legacy `lib/core` and `lib/ui`, which stay untouched.

`lib/sim/sim_controller.dart`:

```dart
  static List<Color> _colorsFromRgbw(RgbwFrame f) => [
        for (final (r, g, b) in foldToRgb(f)) Color.fromARGB(255, r, g, b)
      ];
```

Rename the three `_colorsFromGrb` call sites. Change the `frame` getter's doc (`:108`) to `The raw RGBW frame`.

- [ ] **Step 5: Re-export the contract and pin v5**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/terrarium-heartbeat-devshroom-d5dec8
PYTHONPATH=$LUX .venv/bin/python -m tools.export_contract /Users/chris/projects/mm-tuneshroom/.claude/worktrees/rgbw-frames/test/contract
cd /Users/chris/projects/mm-tuneshroom/.claude/worktrees/rgbw-frames
```

Confirm the export writes `contract.json` plus the scenarios into that directory, replacing the old files. If it writes a subdirectory instead, move the files to match the existing `test/contract/` layout.

- `test/contract_export_test.dart:39` and `test/contract_replay_test.dart:154-156`: `5`. The test name becomes `'the runner refuses any contract_version but 5'`.
- `test/contract_replay_test.dart:189`: `((ef as Map)['rgbw'] as List).length, 48`.
- `test/contract_replay_test.dart:257`: `'expect_frame': {'rgbw': List<int>.filled(48, 9)}`.
- `test/replay/scenario_runner.dart:444-449`: read `['rgbw']` into a variable named `rgbw` and use it in the three places.
- `test/render_cue_player_test.dart:54,87-88` and the ambient and looking tests: re-index from GRB to RGBW by the Task 4 Step 5 rules (red at `i*4`, green `i*4+1`, blue `i*4+2`, W `i*4+3`).

- [ ] **Step 6: Run the whole suite**

Run: `flutter test`
Expected: all pass, including the v5 replay of all 22 scenarios.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(render): RGBW frames and contract v5; Looking on the W channel, screen fold"
```

---

### Task 9: mm-devshroom issue draft and closeout docs

**Files:**
- Create: `docs/upstream/2026-10-14-devshroom-rgbw-issue.md` (mm-terrarium)

- [ ] **Step 1: Write the draft** (posting it needs Chris's go-ahead; do not file it)

```markdown
# mm-devshroom: decode the contract v5 RGBW pixel wire

mm-terrarium contract v5 (merged <date>) changes every `/<dev>/leds` frame
to R, G, B, W, 4 bytes per pixel: 48 bytes for a 12 px device. A Room-bound
device's frame is its fixture's pixel count x 4. Spec:
mm-terrarium `docs/superpowers/specs/2026-10-14-rgbw-device-wire-design.md`.
Guide: `docs/device-contract-guide.md` (the `/leds` row, rules 8 to 10).

Firmware today (origin/main 0225e78):
- `src/o2_test/hardware_control/leds.cpp:39-54` `show_rgb` reads 3-byte
  triplets and draws any length. On a v5 Terrarium every colour is wrong.

Do this:
1. Decode `/leds` as R, G, B, W, 4 per pixel. Drop a blob whose size is not
   exactly the device's declared pixel count x 4 (rule 8); never truncate.
   `testshroom` declares 12 px, so expect 48 bytes and draw the first 6.
2. The dev board's NeoPixels have no white die: show each pixel as
   `min(255, r + w)`, `min(255, g + w)`, `min(255, b + w)` (rule 10).
3. Keep `NEO_GRB` as the driver order. The wire order is now independent of
   the strip's physical order.
4. The Tower's pixels are three-core as well: same fold.
5. A Rev 1 RGBW board drives the die directly (`NEO_GRBW` or the P4 driver's
   equivalent), with no fold.
6. Replay contract export v5.

The lexicon's whites (the invite flash, the Looking pulse) now arrive as
W only, `0,0,0,255`, so without step 2 they show black.
```

- [ ] **Step 2: Commit**

```bash
git add docs/upstream/2026-10-14-devshroom-rgbw-issue.md
git commit -m "docs(upstream): mm-devshroom RGBW decode issue draft (not filed)"
```

- [ ] **Step 3: Report**

List the three branches and their head commits, and the order to merge them: luxaeterna, then mm-terrarium, then mm-tuneshroom. mm-terrarium's CI resolves luxaeterna from its sibling checkout, so the luxaeterna PR must merge first. State that the devshroom issue is drafted and not filed.
