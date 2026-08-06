# Tuneshroom Audio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `harness/led_smoke.py` play sound whose modulation tracks the light pulsing, driven off the same MIDI control stream that drives the light.

**Architecture:** Two controllers carry the demo. `cc:74` glides aurora's hue and sweeps FluidSynth's filter; `cc:11` drives aurora's breath and the synth's loudness. Because aurora's breath is currently a preset-internal envelope nothing can bind to, luxaeterna gains an additive `level` param (Task 1) so Control can generate the breath and both consumers can read the same number. Control-side fan-out lives in a pure, pyarco-free `control/audio.py`; the pyarco-backed synth pool lives in `harness/arco_synth.py` and is imported lazily, so the offline suite stays green with no Arco server.

**Tech Stack:** Python 3.14, pytest, luxaeterna (sibling checkout, dev-only), pyarco + o2litepy (sibling checkout at `/Users/chris/projects/arco`, dev-only via PYTHONPATH), Arco server with the `flsyn` (FluidSynth) ugen.

**Spec:** [`docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md`](../specs/2026-08-06-tuneshroom-audio-design.md)

## Global Constraints

- **No em dashes** in any written output: code comments, docstrings, commit messages, docs. Use commas, colons, or parentheses.
- **`python -m pytest tests` must pass with no Arco server running and no pyarco importable.** This is a hard requirement. `control/audio.py` must never import pyarco, at module level or otherwise.
- **`python -m harness.led_smoke` with no `--audio` must behave exactly as it does today**, including on a box with no Arco server.
- **Boundary rule 1 (single writer to `/arco`):** only Control builds ugen graphs and owns the ugen id space, which includes freeing it at unload. Audio declarations never ship to the device.
- **Do not freeze the `Synth` abstraction.** The Control-side voice type is named `DeviceVoice`, not `Synth`, and takes **no channel parameter** in its public API. The channel is real but internal to `harness/arco_synth.py`.
- **`ugen_manifest` v0 is provisional**, not the audio-manifest freeze that light-manifest v2 was for light. Validation stays shallow.
- Two repos. **luxaeterna Task 1 lands first**; every mm-terrarium task after it depends on `aurora` accepting a `level` param in the sibling checkout at `/Users/chris/projects/luxaeterna`.
- Arco server path: `/Users/chris/projects/arco/apps/pytest/server`. It is a curses app and **cannot be launched from a tool call**; ask Chris to start it.
- Soundfont default: `/Users/chris/projects/fluidsynth/sf2/VintageDreamsWaves-v2.sf2`.

## File Structure

**luxaeterna** (`/Users/chris/projects/luxaeterna`, separate branch and PR):

| File | Responsibility |
|---|---|
| `luxaeterna/synth/presets.py` | Modify `_make_aurora`: additive `level` param. |
| `tests/synth/test_presets.py` | Modify: level-absent and level-present behavior. |
| `tests/synth/test_binding.py` | Modify: a `cc:11 -> level` lane resolves and drives; without the param it raises. |

**mm-terrarium** (this repo):

| File | Responsibility |
|---|---|
| `control/roles.py` | Modify: `ugen_manifest` becomes `dict`. |
| `control/role_config.py` | Modify: add `validate_ugen_manifest`, called from `validate_role_declarations`. |
| `control/audio.py` | **Create.** Pure Control-side fan-out. `DeviceVoice`/`SynthPool` protocols, `FakeVoice`/`FakePool` in-package test doubles, `AudioBridge`. Never imports pyarco. |
| `bits/test_bit.py` | Modify: `player` gains a `ugen_manifest` and a `cc:11 -> level` light lane. |
| `harness/arco_synth.py` | **Create.** pyarco-backed `SynthPool`. Lazy import, channel allocation, `sched.poll()` pumping, teardown. |
| `control/breath.py` | **Create.** The breath as a Control-owned cc:11 signal. Pure, dependency-free, consumed by both the demo and devicelink. |
| `harness/led_smoke.py` | Modify: `--audio`/`--soundfont`/`--program`, shared-stream wiring. |
| `devicelink/agent.py` | Modify: drive the breath, so a connected device does not render a static surface. |
| `tests/test_audio.py` | **Create.** Every `AudioBridge` decision against fakes. No importorskip. |
| `tests/test_breath.py` | **Create.** The breath envelope's knots, interpolation, looping, and floor. |
| `tests/test_arco_synth.py` | **Create.** One thin integration test, skipped without pyarco and without a live server. |
| `tests/test_roles.py`, `tests/test_test_bit.py`, `tests/test_role_config.py`, `tests/test_led_smoke_cli.py`, `tests/test_led_smoke.py` | Modify: schema change, new declarations, breath generator, shared-stream regression. |
| `requirements-dev.txt` | Modify: optional audio heading (`zeroconf`, `netifaces`, PYTHONPATH note). |
| `docs/MM_TERRARIUM.md`, `docs/control-gameserver-design.md` | Modify: see Task 9. |

---

### Task 1: luxaeterna, additive `level` param on `aurora`

**Repo:** `/Users/chris/projects/luxaeterna` (NOT this repo). Branch off `main`.

**Files:**
- Modify: `luxaeterna/synth/presets.py:50-67`
- Test: `tests/synth/test_presets.py`, `tests/synth/test_binding.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `registry.build("aurora", hue=float, level=float)` returns a `LightInstrument` whose `param_names()` is `{"hue", "level"}`. `registry.build("aurora", hue=float)` (no `level`) is unchanged and its `param_names()` is `{"hue"}`. `Task 5` and `Task 8` in mm-terrarium depend on the first form existing.

**Why this shape:** `Param.set` calls `ugen.set_target`, and `Smooth.set_target` forwards to its `Const` source. `SegmentLevel` has no `set_target`, which is exactly why the self-breathing and externally-driven modes cannot be one graph. Declaring `level` is what opts into external drive, the same way declaring `hue` already supplies a cc-drivable starting value.

- [ ] **Step 1: Write the failing tests**

Add to `tests/synth/test_presets.py` (the file already has `_ctx`, `_out_hue`, `registry`, `np`, `pytest` imported):

```python
def test_aurora_without_level_param_still_self_breathes():
    a = registry.build("aurora", hue=0.0)
    assert a.param_names() == {"hue"}             # level is NOT exposed
    brights = [a.render(_ctx(frame=f, n=4, dt=0.5)).max() for f in range(14)]
    assert max(brights) - min(brights) > 0.1      # unchanged: still breathes
    assert min(brights) > 0.0                     # unchanged: never dark


def test_aurora_with_level_param_is_externally_driven_not_breathing():
    a = registry.build("aurora", hue=0.0, level=1.0)
    assert a.param_names() == {"hue", "level"}    # so a cc lane can target it
    brights = [a.render(_ctx(frame=f, n=4, dt=0.5)).max() for f in range(14)]
    assert max(brights) - min(brights) < 0.02     # held steady, breath is gone


def test_aurora_level_glides_toward_target_not_snap():
    a = registry.build("aurora", hue=0.0, level=1.0)
    a.render(_ctx(frame=0, n=4, dt=0.1))          # settle at full
    a.set("level", 0.2)
    b1 = a.render(_ctx(frame=1, n=4, dt=0.1)).max()
    last = None
    for f in range(2, 60):
        last = a.render(_ctx(frame=f, n=4, dt=0.1))
    bN = last.max()
    assert 0.2 < b1 < 1.0                         # started gliding, did not snap
    assert abs(bN - 0.2) < 0.02                   # converged near the target
```

Add to `tests/synth/test_binding.py` (it already imports `LightInstrumentDecl`, `LightLane`, `resolve`, `shroom_capability`, `RenderContext`, `np`, `pytest`):

```python
def test_resolve_aurora_cc_level_lane_drives_brightness():
    decl = LightInstrumentDecl(
        instrument="aurora", target="primary", params={"hue": 0.33, "level": 1.0},
        lanes=[LightLane("cc:11", "level")])
    binding = resolve(decl, shroom_capability("ie3"))       # must NOT raise
    assert "cc:11" in binding.routes
    for f in range(20):                                     # settle at full
        binding.render(RenderContext(0.0, f, 0.1, np.linspace(0, 1, 12), 12, 3))
    binding.routes["cc:11"](0.2)                            # drive the breath down
    out = None
    for f in range(20, 80):
        out = binding.render(RenderContext(0.0, f, 0.1, np.linspace(0, 1, 12), 12, 3))
    assert out.max() < 0.3                                  # followed the lane down


def test_resolve_aurora_level_lane_without_level_param_raises():
    # A manifest that wants the breath driven must declare the param. Without it
    # aurora self-breathes and has nothing to set, so this is a located failure.
    decl = LightInstrumentDecl(
        instrument="aurora", target="primary", params={"hue": 0.33},
        lanes=[LightLane("cc:11", "level")])
    with pytest.raises(ValueError) as ei:
        resolve(decl, shroom_capability("ie3"))
    assert "level" in str(ei.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests/synth/test_presets.py tests/synth/test_binding.py -v`
Expected: the five new tests FAIL. `test_aurora_with_level_param_is_externally_driven_not_breathing` and the two binding tests fail with `KeyError: "unknown aurora param(s) ['level']"`; `test_aurora_level_glides_toward_target_not_snap` fails the same way. `test_aurora_without_level_param_still_self_breathes` should already PASS (it asserts today's behavior).

- [ ] **Step 3: Implement the additive param**

Replace `luxaeterna/synth/presets.py` lines 50 to 67 with:

```python
_AURORA_PARAMS = frozenset({"hue", "level"})

_AURORA_BREATHE = [(0.0, 0.55), (3.0, 1.0), (6.0, 0.55)]   # ~6 s cycle, never dark
_AURORA_HUE_GLIDE_TAU = 0.4                                # seconds
_AURORA_LEVEL_GLIDE_TAU = 0.15                             # seconds


def _make_aurora(**params) -> LightInstrument:
    unknown = set(params) - _AURORA_PARAMS
    if unknown:                    # reject typo'd manifest params, don't discard them
        raise KeyError(f"unknown aurora param(s) {sorted(unknown)} "
                       f"(known: {sorted(_AURORA_PARAMS)})")
    hue = Smooth(Const(float(params.get("hue", 0.0))), _AURORA_HUE_GLIDE_TAU)
    exposed = {"hue": Param("hue", hue)}
    if "level" in params:
        # Declaring level opts into external drive: the breath moves off this
        # preset's private clock and onto whatever cc lane targets it, so a
        # sound engine reading the same controller swells in step with the
        # light. SegmentLevel has no set_target, which is why these are two
        # graphs rather than one.
        level = Smooth(Const(float(params["level"])), _AURORA_LEVEL_GLIDE_TAU)
        exposed["level"] = Param("level", level)
    else:
        level = SegmentLevel(_AURORA_BREATHE, loop_from=0.0)
    return LightInstrument(Fill(level, HueColor(hue)), exposed)


registry.register("aurora", _make_aurora)
```

- [ ] **Step 4: Run the full luxaeterna suite**

Run: `cd /Users/chris/projects/luxaeterna && python -m pytest tests -q`
Expected: all PASS, including the four pre-existing aurora tests and
`test_resolve_aurora_cc_hue_lane_drives_glide`, none of which were modified.

- [ ] **Step 5: Update the aurora spec's decision record**

Append to `docs/superpowers/specs/2026-07-23-aurora-smooth-glow-design.md`:

```markdown
## Amendment 2026-08-06: additive `level` param

`aurora` gained an optional `level` build param. Declaring it replaces the
internal `_AURORA_BREATHE` envelope with a `Smooth(Const(...))` exposed as a
cc-drivable `Param`; omitting it leaves the self-breathing graph byte for byte
unchanged, so nothing above this line moved.

Motivation is cross-repo: mm-terrarium's Tuneshroom audio demo needs the visible
breath and the audible swell to be the same number rather than two clocks that
happen to agree. See mm-terrarium
`docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md` section 3.
```

- [ ] **Step 6: Commit**

```bash
cd /Users/chris/projects/luxaeterna && git add luxaeterna/synth/presets.py tests/synth/test_presets.py tests/synth/test_binding.py docs/superpowers/specs/2026-07-23-aurora-smooth-glow-design.md && git commit -m "feat(synth): additive level param on aurora, so the breath can be externally driven

Declaring level opts into external drive: the breath moves off the preset's
private clock onto a cc lane, so a sound engine reading the same controller
swells in step with the light. Omitting it leaves the self-breathing graph
unchanged, so all four existing aurora tests pass unmodified."
```

---

### Task 2: `ugen_manifest` becomes a dict, with shallow validation

**Files:**
- Modify: `control/roles.py:22`
- Modify: `control/role_config.py:21-28` (add a call) and append a new validator
- Test: `tests/test_roles.py:15-18`, `tests/test_test_bit.py:14-18`, `tests/test_role_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Role.ugen_manifest: dict` defaulting to `{}`. `control.role_config.validate_ugen_manifest(role: Role) -> None` raising `ValueError` with a located message. `validate_role_declarations` calls it, so a bad `ugen_manifest` surfaces as `BitLoadError` from `GameServer.load_bit`. Task 3 reads `role.ugen_manifest["instruments"]`; Task 5 authors one.

**Authored v0 shape** (deliberately not frozen, see spec section 9.2):

```python
{"instruments": [
    {"instrument": "flsyn", "program": int,
     "drone": {"key": int, "velocity": int},
     "lanes": [{"source": "cc:<n>", "dest": "cc:<n>"}]},
]}
```

- [ ] **Step 1: Write the failing tests**

Update `tests/test_roles.py`, replacing `test_role_defaults_to_empty_ugen_manifest`'s assertion:

```python
def test_role_defaults_to_empty_ugen_manifest():
    role = Role(name="r", role_class=RoleClass.SHARED, capacity=None, scored=True)
    assert role.ugen_manifest == {}
```

Add to `tests/test_role_config.py` (it already imports `pytest`, `Role`, `RoleClass`, `RoleTable`, and `validate_role_declarations`; add `validate_ugen_manifest` to that import):

```python
def _role(**kw):
    base = dict(name="player", role_class=RoleClass.SHARED, capacity=None,
                scored=True)
    base.update(kw)
    return Role(**base)


def test_empty_ugen_manifest_is_valid():
    validate_ugen_manifest(_role())                      # {} means "no audio"


def test_ugen_manifest_must_be_a_dict():
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest=[]))
    assert "ugen_manifest" in str(ei.value) and "list" in str(ei.value)


def test_ugen_manifest_instruments_must_be_a_list():
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest={"instruments": {}}))
    assert "'instruments' must be a list" in str(ei.value)


def test_ugen_manifest_instrument_field_is_required():
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest={"instruments": [{}]}))
    assert "instruments[0]" in str(ei.value) and "instrument" in str(ei.value)


def test_ugen_manifest_lane_source_must_be_a_cc_reference():
    bad = {"instruments": [{"instrument": "flsyn",
                            "lanes": [{"source": "note", "dest": "cc:74"}]}]}
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest=bad))
    assert "lanes[0]" in str(ei.value) and "cc:" in str(ei.value)


def test_ugen_manifest_lane_dest_must_be_a_cc_reference():
    bad = {"instruments": [{"instrument": "flsyn",
                            "lanes": [{"source": "cc:74", "dest": "brightness"}]}]}
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest=bad))
    assert "lanes[0]" in str(ei.value) and "brightness" in str(ei.value)


def test_ugen_manifest_drone_requires_key_and_velocity():
    bad = {"instruments": [{"instrument": "flsyn", "drone": {"key": 45}}]}
    with pytest.raises(ValueError) as ei:
        validate_ugen_manifest(_role(ugen_manifest=bad))
    assert "drone" in str(ei.value) and "velocity" in str(ei.value)


def test_bad_ugen_manifest_fails_the_bit_at_load():
    # The whole point of load-time validation: a typo'd Bit fails as a
    # BitLoadError, never as a mid-installation surprise.
    from control.engine import BitLoadError, GameServer
    from control.bit import Bit

    class BadBit(Bit):
        version = "0.1"

        @property
        def role_table(self):
            return RoleTable(
                roles={"player": _role(
                    ugen_manifest={"instruments": [{"program": 1}]})},
                node_map={"N": ["player"]})

    gs = GameServer({"bad": BadBit})
    with pytest.raises(BitLoadError):
        gs.load_bit("bad")
```

Update `tests/test_test_bit.py:14-18` to expect dicts:

```python
def test_role_ugen_manifests_are_present_but_empty_placeholders():
    table = TestBit().role_table
    assert table.roles["jammer"].ugen_manifest == {}
```

(Drop the `player` assertion from this test. Task 5 replaces it with a real one.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_roles.py tests/test_role_config.py tests/test_test_bit.py -v`
Expected: FAIL. `test_role_defaults_to_empty_ugen_manifest` fails with `assert [] == {}`; every `validate_ugen_manifest` test fails on `ImportError`/`cannot import name`.

- [ ] **Step 3: Change the field type**

In `control/roles.py`, replace lines 19 to 22 with:

```python
    # This role's audio declaration, v0 and deliberately provisional (see
    # docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md section 9.2).
    # It is NOT the frozen wire contract light_manifest is, and it never ships
    # to the device: audio is Control's business (boundary rule 1). Shape:
    #   {"instruments": [{instrument, program?, drone?: {key, velocity},
    #                     lanes?: [{source: "cc:<n>", dest: "cc:<n>"}]}]}
    # Validated shallowly at Bit load (control/role_config.py). {} = no audio.
    ugen_manifest: dict = field(default_factory=dict)
```

- [ ] **Step 4: Add the validator**

In `control/role_config.py`, add `_CC_PREFIX = "cc:"` next to `_WELCOME_HALVES`, add the call to `validate_role_declarations`:

```python
def validate_role_declarations(role_table: RoleTable) -> None:
    """Shallow structural validation of every role's light_manifest, welcome
    and ugen_manifest against their authored shapes. Raises ValueError with a
    message locating the offending field."""
    for role in role_table.roles.values():
        _validate_light_manifest(role)
        _validate_welcome(role)
        validate_ugen_manifest(role)
```

and append this public function to the end of the file:

```python
def _cc_number(ref, where: str) -> int:
    """Parse a 'cc:<n>' reference. Both lane ends use this form: the mapping
    to a synth parameter is FluidSynth's own reading of the controller number,
    so there is no destination vocabulary for Control to invent here."""
    if not isinstance(ref, str) or not ref.startswith(_CC_PREFIX):
        raise ValueError(f"{where}: must be a {_CC_PREFIX!r} reference, got {ref!r}")
    try:
        num = int(ref[len(_CC_PREFIX):])
    except ValueError:
        raise ValueError(
            f"{where}: {ref!r} is not a controller number") from None
    if not 0 <= num <= 127:
        raise ValueError(f"{where}: controller {num} is outside 0-127")
    return num


def validate_ugen_manifest(role: Role) -> None:
    """Shallow structural validation of a Role's authored ugen_manifest.
    Deliberately provisional (v0): instrument names and programs belong to the
    Arco/FluidSynth side Control cannot see, so only shape is checked here."""
    where = f"role {role.name!r} ugen_manifest"
    manifest = role.ugen_manifest
    if not isinstance(manifest, dict):
        raise ValueError(
            f"{where}: must be a dict, got {type(manifest).__name__}")
    instruments = manifest.get("instruments", [])
    if not isinstance(instruments, list):
        raise ValueError(f"{where}: 'instruments' must be a list")
    for idx, decl in enumerate(instruments):
        decl_where = f"{where} instruments[{idx}]"
        if not isinstance(decl, dict):
            raise ValueError(f"{decl_where}: must be a dict")
        if "instrument" not in decl:
            raise ValueError(
                f"{decl_where}: missing required field 'instrument'")
        drone = decl.get("drone")
        if drone is not None:
            if not isinstance(drone, dict):
                raise ValueError(f"{decl_where}: 'drone' must be a dict")
            for req in ("key", "velocity"):
                if req not in drone:
                    raise ValueError(
                        f"{decl_where} drone: missing required field {req!r}")
        lanes = decl.get("lanes", [])
        if not isinstance(lanes, list):
            raise ValueError(f"{decl_where}: 'lanes' must be a list")
        for lidx, lane in enumerate(lanes):
            lane_where = f"{decl_where} lanes[{lidx}]"
            if not isinstance(lane, dict):
                raise ValueError(f"{lane_where}: must be a dict")
            for req in ("source", "dest"):
                if req not in lane:
                    raise ValueError(
                        f"{lane_where}: missing required field {req!r}")
            _cc_number(lane["source"], f"{lane_where} source")
            _cc_number(lane["dest"], f"{lane_where} dest")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests -q`
Expected: all PASS **after** one more edit this plan originally got wrong.
`tests/test_console_protocol.py:5-11` builds a **real defaulted `Role`** and
asserts `protocol.role_view(role)`, so it *does* exercise the default and its
`"ugen_manifest": []` literal must become `{}`. That is the same one-line
update `tests/test_roles.py` and `tests/test_test_bit.py` get. No console
*source* change is needed: `console/protocol.py` and `console/static/index.html`
pass the field through as JSON.

- [ ] **Step 6: Commit**

```bash
git add control/roles.py control/role_config.py tests/test_roles.py tests/test_role_config.py tests/test_test_bit.py && git commit -m "feat(control): ugen_manifest v0, a dict validated at Bit load

Brings ugen_manifest in line with light_manifest as a dict, and adds shallow
structural validation so a typo'd audio declaration fails as a BitLoadError
rather than mid-installation. Deliberately provisional: this is not the frozen
wire contract light-manifest v2 is, and it never ships to the device."
```

---

### Task 3: `control/audio.py`, voice protocols and the lane/drone fan-out

**Files:**
- Create: `control/audio.py`
- Test: `tests/test_audio.py` (create)

**Interfaces:**
- Consumes: `Role.ugen_manifest` (Task 2), `control.role_config._cc_number` is NOT reused here (audio.py stays import-light; it re-parses with its own tiny helper so `control/audio.py` has no role_config dependency).
- Produces, relied on by Tasks 4, 6, 7, 8:
  - `class DeviceVoice(Protocol)`: `note_on(key: int, vel: int)`, `note_off(key: int)`, `control_change(num: int, val: int)`, `program_change(prog: int)`, `all_off()`. **No channel parameter.**
  - `class SynthPool(Protocol)`: `acquire() -> DeviceVoice`, `release(voice: DeviceVoice)`, `poll()`, `shutdown()`.
  - `class FakeVoice`: records into `self.sent: list[tuple]` as `("note_on", key, vel)`, `("note_off", key)`, `("cc", num, val)`, `("program", prog)`, `("all_off",)`.
  - `class FakePool`: `acquire()` returns a fresh `FakeVoice` appended to `self.acquired`; `release(v)` appends to `self.released`; `poll()` increments `self.polls`; `shutdown()` sets `self.shut = True`.
  - `class AudioBridge`: `__init__(pool, clock=time.monotonic)`, `on_grant(dev: str, role: Role) -> None`, `feed_midi(dev: str, status: int, d1: int, d2: int) -> None`, `start_drone(dev: str) -> None`, `stop_drone(dev: str) -> None`, `on_release(dev: str) -> None`, `shutdown() -> None`. Task 4 adds `tick(now=None)` and the welcome cue.

**Design notes for the implementer:**
- The drone goes through `feed_midi` rather than straight to the voice, so there is exactly one code path from a MIDI byte to a synth call. `start_drone`/`stop_drone` are thin lookups over the role's declared drone.
- `feed_midi` also handles `0xC0` (program change), because Task 8's `--program` override rides the same path. Without it that flag would silently do nothing.
- A `cc` with no declared lane is **dropped**, not forwarded. That is what makes the lane a real remap seam rather than decoration.
- A role with an empty `ugen_manifest` (TestBit's `jammer`) must acquire **no** voice. Devices in a silent role must not consume a channel.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audio.py`:

```python
"""Control-side audio fan-out. Pure and offline: no pyarco, no Arco server,
no network. Every decision AudioBridge makes is asserted against FakeVoice."""

from __future__ import annotations

import pytest

from control.audio import AudioBridge, FakePool
from control.roles import Role, RoleClass

PLAYER_UGENS = {
    "instruments": [
        {"instrument": "flsyn", "program": 89,
         "drone": {"key": 45, "velocity": 90},
         "lanes": [{"source": "cc:74", "dest": "cc:74"},
                   {"source": "cc:11", "dest": "cc:11"}]},
    ],
}


def _role(name="player", ugens=None, welcome=None):
    return Role(name=name, role_class=RoleClass.SHARED, capacity=None,
                scored=True, ugen_manifest=ugens or {}, welcome=welcome)


def test_grant_acquires_a_voice_and_sets_the_program():
    pool = FakePool()
    AudioBridge(pool).on_grant("dev1", _role(ugens=PLAYER_UGENS))
    assert len(pool.acquired) == 1
    assert ("program", 89) in pool.acquired[0].sent


def test_grant_with_empty_ugen_manifest_acquires_nothing():
    # TestBit's jammer role: silent, and it must not burn a channel.
    pool = FakePool()
    AudioBridge(pool).on_grant("dev1", _role(name="jammer"))
    assert pool.acquired == []


def test_declared_cc_lane_reaches_the_voice():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.feed_midi("dev1", 0xB0, 74, 100)
    assert ("cc", 74, 100) in pool.acquired[0].sent


def test_undeclared_cc_is_dropped_not_forwarded():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.feed_midi("dev1", 0xB0, 7, 100)                  # cc:7 has no lane
    assert not [s for s in pool.acquired[0].sent if s[0] == "cc"]


def test_lane_remaps_the_controller_number():
    # The lane is a remap seam, not decoration: cc:74 in, cc:11 out.
    remap = {"instruments": [{"instrument": "flsyn",
                              "lanes": [{"source": "cc:74", "dest": "cc:11"}]}]}
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=remap))
    br.feed_midi("dev1", 0xB0, 74, 64)
    assert ("cc", 11, 64) in pool.acquired[0].sent


def test_program_change_rides_the_same_path():
    # led_smoke's --program override goes through feed_midi like everything
    # else; without 0xC0 handling that flag would silently do nothing.
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.feed_midi("dev1", 0xC0, 5, 0)
    assert ("program", 5) in pool.acquired[0].sent


def test_midi_for_an_ungranted_device_is_ignored():
    pool = FakePool()
    AudioBridge(pool).feed_midi("nobody", 0xB0, 74, 100)   # must not raise


def test_start_and_stop_drone_use_the_declared_note():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.start_drone("dev1")
    assert ("note_on", 45, 90) in pool.acquired[0].sent
    br.stop_drone("dev1")
    assert ("note_off", 45) in pool.acquired[0].sent


def test_start_drone_is_idempotent():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.start_drone("dev1")
    br.start_drone("dev1")
    assert len([s for s in pool.acquired[0].sent if s[0] == "note_on"]) == 1


def test_role_without_a_drone_starts_no_note():
    no_drone = {"instruments": [{"instrument": "flsyn", "program": 1}]}
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=no_drone))
    br.start_drone("dev1")
    assert not [s for s in pool.acquired[0].sent if s[0] == "note_on"]


def test_release_stops_the_drone_silences_and_frees_the_voice():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.start_drone("dev1")
    voice = pool.acquired[0]
    br.on_release("dev1")
    assert ("note_off", 45) in voice.sent
    assert ("all_off",) in voice.sent
    assert pool.released == [voice]


def test_release_is_idempotent_and_midi_after_it_is_ignored():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.on_release("dev1")
    br.on_release("dev1")                               # must not raise
    br.feed_midi("dev1", 0xB0, 74, 100)                 # must not raise
    assert len(pool.released) == 1


def test_shutdown_frees_every_voice_and_shuts_the_pool():
    # Boundary rule 1: owning the ugen id space means freeing it at unload.
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS))
    br.on_grant("dev2", _role(ugens=PLAYER_UGENS))
    br.shutdown()
    assert len(pool.released) == 2
    assert pool.shut is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_audio.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'control.audio'`.

- [ ] **Step 3: Implement `control/audio.py`**

```python
"""Control-side audio: turning a role's declared MIDI intent into synth calls.

Pure by construction. This module MUST NOT import pyarco, at module level or
anywhere else, so the offline test suite runs with no Arco server and no
pyarco checkout. The concrete pyarco-backed pool lives in harness/arco_synth.py
and is injected.

Boundary rule 1 (docs/MM_TERRARIUM.md): only Control builds ugen graphs and
owns the ugen id space, which includes freeing it at unload. Audio declarations
never ship to the device.

The channel question is OPEN and out with Roger Dannenberg: his written notes
argue against a channel parameter (allocate up to 16 Synths sharing one Flsyn),
his shipped MidiSender takes chan on every method. Those disagree about the API,
not the implementation. So the channel here is real but INTERNAL to the backend:
nothing in this file names one. The type is DeviceVoice, not Synth, so this is
not read as that abstraction having landed. See
docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md section 9.1.
"""

from __future__ import annotations

import time
from typing import Protocol

from control.roles import Role

_CC_PREFIX = "cc:"


class DeviceVoice(Protocol):
    """One device's slice of the room synth. No channel in this API."""

    def note_on(self, key: int, vel: int) -> None: ...
    def note_off(self, key: int) -> None: ...
    def control_change(self, num: int, val: int) -> None: ...
    def program_change(self, prog: int) -> None: ...
    def all_off(self) -> None: ...


class SynthPool(Protocol):
    def acquire(self) -> DeviceVoice: ...
    def release(self, voice: DeviceVoice) -> None: ...
    def poll(self) -> None: ...
    def shutdown(self) -> None: ...


class FakeVoice:
    """In-process test double, sibling of uplink.transport.FakeTransport."""

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def note_on(self, key: int, vel: int) -> None:
        self.sent.append(("note_on", key, vel))

    def note_off(self, key: int) -> None:
        self.sent.append(("note_off", key))

    def control_change(self, num: int, val: int) -> None:
        self.sent.append(("cc", num, val))

    def program_change(self, prog: int) -> None:
        self.sent.append(("program", prog))

    def all_off(self) -> None:
        self.sent.append(("all_off",))


class FakePool:
    def __init__(self) -> None:
        self.acquired: list[FakeVoice] = []
        self.released: list[FakeVoice] = []
        self.polls = 0
        self.shut = False

    def acquire(self) -> FakeVoice:
        voice = FakeVoice()
        self.acquired.append(voice)
        return voice

    def release(self, voice) -> None:
        self.released.append(voice)

    def poll(self) -> None:
        self.polls += 1

    def shutdown(self) -> None:
        self.shut = True


def _cc_number(ref: str) -> int:
    return int(ref[len(_CC_PREFIX):])


class _DeviceAudio:
    """Per-device state: the voice, its lane map, and its drone."""

    __slots__ = ("voice", "lanes", "drone", "drone_key")

    def __init__(self, voice, lanes: dict[int, int], drone: dict | None) -> None:
        self.voice = voice
        self.lanes = lanes
        self.drone = drone
        self.drone_key: int | None = None       # set while the drone sounds


class AudioBridge:
    """Fans a device's MIDI stream out to its synth voice, per the role's
    declared lanes. The light-side sibling is harness/device_bridge.py; both
    consume the SAME stream, which is the point (spec section 3)."""

    def __init__(self, pool, clock=time.monotonic) -> None:
        self._pool = pool
        self._clock = clock
        self._devices: dict[str, _DeviceAudio] = {}

    def on_grant(self, dev: str, role: Role) -> None:
        """Role adopted: acquire a voice and wire its lanes. A role declaring
        no instruments is silent and must not consume a voice."""
        instruments = role.ugen_manifest.get("instruments", [])
        if not instruments:
            return
        decl = instruments[0]        # v0: one instrument per role
        voice = self._pool.acquire()
        program = decl.get("program")
        if program is not None:
            voice.program_change(int(program))
        lanes = {_cc_number(lane["source"]): _cc_number(lane["dest"])
                 for lane in decl.get("lanes", [])}
        self._devices[dev] = _DeviceAudio(voice, lanes, decl.get("drone"))

    def feed_midi(self, dev: str, status: int, d1: int, d2: int) -> None:
        """The one path from a MIDI byte to a synth call. An undeclared cc is
        dropped, which is what makes the lane a remap seam and not decoration."""
        entry = self._devices.get(dev)
        if entry is None:
            return
        kind = status & 0xF0
        if kind == 0x90 and d2 > 0:
            entry.voice.note_on(d1, d2)
        elif kind == 0x80 or (kind == 0x90 and d2 == 0):
            entry.voice.note_off(d1)
        elif kind == 0xB0:
            dest = entry.lanes.get(d1)
            if dest is not None:
                entry.voice.control_change(dest, d2)
        elif kind == 0xC0:
            entry.voice.program_change(d1)

    def start_drone(self, dev: str) -> None:
        """FluidSynth is silent without a note, so the role's declared drone is
        the substrate its lanes modulate. Light ignores it: the running light
        declaration has no note lane."""
        entry = self._devices.get(dev)
        if entry is None or entry.drone is None or entry.drone_key is not None:
            return
        key, vel = int(entry.drone["key"]), int(entry.drone["velocity"])
        self.feed_midi(dev, 0x90, key, vel)
        entry.drone_key = key

    def stop_drone(self, dev: str) -> None:
        entry = self._devices.get(dev)
        if entry is None or entry.drone_key is None:
            return
        self.feed_midi(dev, 0x80, entry.drone_key, 0)
        entry.drone_key = None

    def on_release(self, dev: str) -> None:
        entry = self._devices.pop(dev, None)
        if entry is None:
            return
        if entry.drone_key is not None:
            entry.voice.note_off(entry.drone_key)
        entry.voice.all_off()
        self._pool.release(entry.voice)

    def shutdown(self) -> None:
        """Free every voice, then the pool. Boundary rule 1: owning the ugen id
        space means freeing it at Bit unload."""
        for dev in list(self._devices):
            self.on_release(dev)
        self._pool.shutdown()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_audio.py -v`
Expected: all 12 PASS.

- [ ] **Step 5: Confirm the module is genuinely pyarco-free**

Run: `python -c "import control.audio, sys; assert not [m for m in sys.modules if m.startswith('pyarco')]; print('clean')"`
Expected: prints `clean`.

- [ ] **Step 6: Commit**

```bash
git add control/audio.py tests/test_audio.py && git commit -m "feat(control): AudioBridge, the Control-side MIDI fan-out to a synth voice

Turns a role's declared lanes into synth calls, with the drone note going
through the same feed_midi path so there is one route from a MIDI byte to a
sound. Pure and pyarco-free by construction, so the offline suite stays green.

The voice type is DeviceVoice, not Synth, and takes no channel parameter: the
channel abstraction is still open with Roger and this must not freeze it."
```

---

### Task 4: The welcome audio cue

**Files:**
- Modify: `control/audio.py` (add the instrument table, `tick`, and the cue in `on_grant`)
- Test: `tests/test_audio.py`

**Interfaces:**
- Consumes: Task 3's `AudioBridge`, `FakePool`, `FakeVoice`.
- Produces: `AudioBridge.tick(now: float | None = None) -> None`, which fires due welcome note-offs and calls `pool.poll()`. `WELCOME_INSTRUMENTS: dict[str, tuple[int, int, int]]` mapping name to `(program, key, velocity)`. `AudioBridge.__init__` gains `welcome_instruments=None`. Task 8's driver loop calls `tick(now)` once per iteration.

**Why a transient second voice:** the cue must not disturb the sustained drone, so it plays on its own voice, which is released as soon as its `duration` expires. It does not permanently consume a channel.

**Why an unknown instrument name raises:** a silent welcome is indistinguishable from a broken one. `Role.welcome` is already validated at Bit load, so the name check belongs there too, but v0 keeps the table in `audio.py`; raising at `on_grant` is loud enough and the smoke test covers it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_audio.py`:

```python
WELCOME = {"light": {"instrument": "glow", "params": {"hue": 0.33},
                     "duration": 1.5},
           "audio": {"instrument": "chime", "duration": 1.5}}


def _clock_from(values):
    return iter(values).__next__


def test_welcome_cue_plays_on_its_own_voice_not_the_drone_voice():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([0.0] * 8))
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS, welcome=WELCOME))
    assert len(pool.acquired) == 2                      # drone voice + cue voice
    cue = pool.acquired[1]
    assert [s for s in cue.sent if s[0] == "note_on"]   # the chime sounded
    drone_voice = pool.acquired[0]
    assert not [s for s in drone_voice.sent if s[0] == "note_on"]


def test_welcome_cue_note_off_fires_after_its_duration_and_frees_the_voice():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([0.0, 1.0, 2.0]))
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS, welcome=WELCOME))
    cue = pool.acquired[1]
    br.tick()                                            # now = 1.0, not due yet
    assert not [s for s in cue.sent if s[0] == "note_off"]
    assert cue not in pool.released
    br.tick()                                            # now = 2.0, past 1.5
    assert [s for s in cue.sent if s[0] == "note_off"]
    assert cue in pool.released


def test_tick_polls_the_pool():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([0.0, 0.1, 0.2]))
    br.tick()
    br.tick()
    assert pool.polls == 2


def test_role_without_a_welcome_audio_half_plays_no_cue():
    pool = FakePool()
    br = AudioBridge(pool)
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS,
                              welcome={"light": {"instrument": "glow"}}))
    assert len(pool.acquired) == 1                       # drone voice only


def test_unknown_welcome_instrument_raises_rather_than_playing_silence():
    pool = FakePool()
    br = AudioBridge(pool)
    with pytest.raises(KeyError) as ei:
        br.on_grant("dev1", _role(ugens=PLAYER_UGENS,
                                  welcome={"audio": {"instrument": "gong"}}))
    assert "gong" in str(ei.value)


def test_shutdown_releases_a_still_sounding_welcome_voice():
    pool = FakePool()
    br = AudioBridge(pool, clock=_clock_from([0.0] * 8))
    br.on_grant("dev1", _role(ugens=PLAYER_UGENS, welcome=WELCOME))
    br.shutdown()
    assert len(pool.released) == 2                       # drone voice and cue voice
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_audio.py -v`
Expected: the six new tests FAIL with `AttributeError: 'AudioBridge' object has no attribute 'tick'` or on the `welcome_instruments`/cue behavior; the 12 from Task 3 still PASS.

- [ ] **Step 3: Add the instrument table**

In `control/audio.py`, below `_CC_PREFIX`:

```python
# Welcome-cue instrument names, provisional v0. Names are opaque to Control
# exactly the way light_manifest instrument names are opaque luxaeterna
# registry names; this table is the audio-side equivalent of that registry.
# (program, key, velocity). Numbers picked by listening on the venue soundfont.
WELCOME_INSTRUMENTS: dict[str, tuple[int, int, int]] = {
    "chime": (9, 84, 88),        # 9 = Tubular Bells (General MIDI)
}

_DEFAULT_WELCOME_DURATION = 1.5
```

- [ ] **Step 4: Play the cue and expire it**

In `control/audio.py`, change `__init__` and add the cue. Replace `AudioBridge.__init__` with:

```python
    def __init__(self, pool, clock=time.monotonic, welcome_instruments=None) -> None:
        self._pool = pool
        self._clock = clock
        self._welcome = (WELCOME_INSTRUMENTS if welcome_instruments is None
                         else welcome_instruments)
        self._devices: dict[str, _DeviceAudio] = {}
        # (due_time, voice, key) for welcome cues still sounding. A cue plays on
        # its own transient voice so it never disturbs the sustained drone, and
        # that voice is released the moment its declared duration expires.
        self._pending_offs: list[tuple[float, object, int]] = []
```

Append to the end of `on_grant`, after the `self._devices[dev] = ...` line:

```python
        self._play_welcome(role)
```

and move the `if not instruments: return` guard so the welcome still plays for a
silent role. Replace the top of `on_grant` with:

```python
    def on_grant(self, dev: str, role: Role) -> None:
        """Role adopted: acquire a voice, wire its lanes, sound the welcome.
        A role declaring no instruments is silent and must not consume a voice,
        but it may still have a welcome cue."""
        instruments = role.ugen_manifest.get("instruments", [])
        if instruments:
            decl = instruments[0]        # v0: one instrument per role
            voice = self._pool.acquire()
            program = decl.get("program")
            if program is not None:
                voice.program_change(int(program))
            lanes = {_cc_number(lane["source"]): _cc_number(lane["dest"])
                     for lane in decl.get("lanes", [])}
            self._devices[dev] = _DeviceAudio(voice, lanes, decl.get("drone"))
        self._play_welcome(role)
```

Add these two methods:

```python
    def _play_welcome(self, role: Role) -> None:
        """The audio half of the adoption ceremony. Declared alongside the light
        half in Role.welcome since PR #5; this is its first consumer."""
        decl = (role.welcome or {}).get("audio")
        if not decl:
            return
        name = decl["instrument"]
        if name not in self._welcome:
            raise KeyError(
                f"role {role.name!r} welcome audio: unknown instrument {name!r} "
                f"(known: {sorted(self._welcome)})")
        program, key, vel = self._welcome[name]
        duration = float(decl.get("duration", _DEFAULT_WELCOME_DURATION))
        voice = self._pool.acquire()
        voice.program_change(program)
        voice.note_on(key, vel)
        self._pending_offs.append((self._clock() + duration, voice, key))

    def tick(self, now: float | None = None) -> None:
        """Called once per driver-loop iteration: expire welcome cues, then let
        the backend pump its transport. The single place the audio side ticks."""
        if now is None:
            now = self._clock()
        still_sounding = []
        for due, voice, key in self._pending_offs:
            if now >= due:
                voice.note_off(key)
                voice.all_off()
                self._pool.release(voice)
            else:
                still_sounding.append((due, voice, key))
        self._pending_offs = still_sounding
        self._pool.poll()
```

Replace `shutdown` so it also frees still-sounding cues:

```python
    def shutdown(self) -> None:
        """Free every voice, then the pool. Boundary rule 1: owning the ugen id
        space means freeing it at Bit unload."""
        for _due, voice, key in self._pending_offs:
            voice.note_off(key)
            voice.all_off()
            self._pool.release(voice)
        self._pending_offs = []
        for dev in list(self._devices):
            self.on_release(dev)
        self._pool.shutdown()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_audio.py -v`
Expected: all 18 PASS.

- [ ] **Step 6: Commit**

```bash
git add control/audio.py tests/test_audio.py && git commit -m "feat(control): play the welcome audio half at role grant

Role.welcome['audio'] has been declared since PR #5 with no consumer. It now
sounds on its own transient voice, released when its declared duration expires,
so the sustained drone is never disturbed and no channel is held. An unknown
instrument name raises rather than playing silence, since a silent welcome is
indistinguishable from a broken one."
```

---

### Task 5: TestBit declares its audio and its breath lane

**Files:**
- Modify: `bits/test_bit.py:26-45`
- Test: `tests/test_test_bit.py`

**Interfaces:**
- Consumes: Task 1 (`aurora` accepts `level`), Task 2 (`ugen_manifest` is a validated dict).
- Produces: `TestBit().role_table.roles["player"]` carries the `ugen_manifest` and `light_manifest` shown below. Tasks 7 and 8 render and sound them.

**Blocked on Task 1.** The `level` param must exist in `/Users/chris/projects/luxaeterna` or `tests/test_led_smoke.py` fails at `resolve()`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_test_bit.py`'s `test_role_ugen_manifests_are_present_but_empty_placeholders` with:

```python
def test_jammer_keeps_empty_media_defaults():
    # The no-light, no-audio path stays exercised.
    table = TestBit().role_table
    assert table.roles["jammer"].ugen_manifest == {}
    assert table.roles["jammer"].light_manifest == {}


def test_player_declares_a_flsyn_instrument_with_a_drone():
    decl = TestBit().role_table.roles["player"].ugen_manifest["instruments"][0]
    assert decl["instrument"] == "flsyn"
    assert decl["drone"]["key"] > 0 and decl["drone"]["velocity"] > 0


def test_player_binds_the_same_two_controllers_in_light_and_audio():
    # The property this whole slice exists to establish: one control stream,
    # two consumers. If these ever diverge, the demo is two timelines again.
    roles = TestBit().role_table.roles["player"]
    light_sources = {lane["source"]
                     for inst in roles.light_manifest["instruments"]
                     for lane in inst["lanes"]}
    audio_sources = {lane["source"]
                     for inst in roles.ugen_manifest["instruments"]
                     for lane in inst.get("lanes", [])}
    assert light_sources == audio_sources == {"cc:74", "cc:11"}


def test_player_light_declares_level_so_the_breath_is_externally_driven():
    inst = TestBit().role_table.roles["player"].light_manifest["instruments"][0]
    assert "level" in inst["params"]                # opts aurora into external drive
    assert {"source": "cc:11", "dest": "level"} in inst["lanes"]


def test_player_light_still_declares_no_note_lane():
    # PR #9's strobe fix: aurora froze its colour at note-on, so sweeping the hue
    # re-triggered constantly. The audio path adds a drone note-on; light must
    # keep ignoring it.
    inst = TestBit().role_table.roles["player"].light_manifest["instruments"][0]
    assert not [lane for lane in inst["lanes"] if lane["source"] == "note"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_test_bit.py -v`
Expected: FAIL. The `ugen_manifest["instruments"]` lookups raise `KeyError: 'instruments'`; the level-param test fails on `assert "level" in inst["params"]`.

- [ ] **Step 3: Update TestBit's declarations**

In `bits/test_bit.py`, replace the `player = Role(...)` block (lines 26 to 45) with:

```python
        player = Role(
            name="player", role_class=RoleClass.SHARED, capacity=None,
            scored=True,
            # First real light-lane declaration: the act that freezes the
            # light-manifest v2 authored shape (see control/roles.py).
            # Instrument names are opaque to Control; these are luxaeterna
            # registry names. Declaring `level` opts aurora out of its private
            # breathing clock and onto cc:11, which is what lets the audio
            # swell in step with the visible pulse rather than near it.
            light_manifest={
                "instruments": [
                    {"instrument": "aurora", "target": "primary",
                     "params": {"hue": 0.33, "level": 0.55},
                     "lanes": [{"source": "cc:74", "dest": "hue"},
                               {"source": "cc:11", "dest": "level"}]},
                ],
            },
            # The audio half of the SAME two controllers. cc:74 is General
            # MIDI Brightness (FluidSynth reads it as filter cutoff) and cc:11
            # is Expression (a direct attenuation, so the swell is audible on
            # any soundfont). Both lanes forward the controller unchanged; the
            # lane exists so a role CAN remap a gesture, not because it must.
            # v0 and provisional, not a frozen wire contract: see
            # docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md.
            ugen_manifest={
                "instruments": [
                    {"instrument": "flsyn", "program": 89,
                     "drone": {"key": 45, "velocity": 90},
                     "lanes": [{"source": "cc:74", "dest": "cc:74"},
                               {"source": "cc:11", "dest": "cc:11"}]},
                ],
            },
            welcome={
                "light": {"instrument": "glow",
                          "params": {"hue": 0.33}, "duration": 1.5},
                "audio": {"instrument": "chime", "duration": 1.5},
            },
        )
```

- [ ] **Step 4: Run the suite to verify it passes**

Run: `python -m pytest tests -q`
Expected: all PASS, including `tests/test_led_smoke.py`, which now resolves an
aurora with a `level` param. If it fails at `resolve()` with "unknown param
'level'", Task 1 has not landed in `/Users/chris/projects/luxaeterna`. Stop and
land it; do not work around it.

- [ ] **Step 5: Commit**

```bash
git add bits/test_bit.py tests/test_test_bit.py && git commit -m "feat(bits): TestBit declares audio, and its breath moves onto cc:11

The player role now binds the same two controllers in both media: cc:74 glides
aurora's hue and sweeps FluidSynth's filter, cc:11 drives aurora's level and the
synth's expression. Declaring level opts aurora out of its private breathing
clock, so the visible swell and the audible swell are one number.

The light declaration still has no note lane, so PR #9's strobe fix holds and
the drone note-on the audio path adds is ignored by light."
```

---

### Task 6: `harness/arco_synth.py`, the pyarco-backed pool

**Files:**
- Create: `harness/arco_synth.py`
- Test: `tests/test_arco_synth.py` (create)

**Interfaces:**
- Consumes: Task 3's `DeviceVoice`/`SynthPool` protocols (structurally, not by inheritance).
- Produces: `ArcoSynthPool(soundfont: str = DEFAULT_SOUNDFONT, ensemble: str = "arco", max_channels: int = 16)` with `start()`, `acquire()`, `release(voice)`, `poll()`, `shutdown()`. `ArcoVoice(flsyn, channel)` implementing `DeviceVoice`, with a public `.channel` attribute the tests read. `DEFAULT_SOUNDFONT: str`. Task 8's `main()` constructs one.

**Critical constraints:**
- `from pyarco...` imports go **inside** `start()`, never at module level. `import harness.arco_synth` must succeed with no pyarco on the path.
- `arco.initialize()` **blocks** until the server is reachable and reset, then `sched.poll()` is all that is needed. Do **not** call `sched.run()`; the driver loop owns the loop.
- The channel is internal. `ArcoVoice.control_change(num, val)` takes no channel; it closes over its own.
- `shutdown()` must drop the `Flsyn` reference so pyarco's destructor frees the Arco ugen id (boundary rule 1). Read `~/projects/arco/doc/pyarco.md` section "Ugen IDs": if the Python Ugen exists, the Arco Ugen exists too.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_arco_synth.py`:

```python
"""Arco-backed synth pool. The import-hygiene test runs everywhere; the live
test needs both pyarco on PYTHONPATH and a running Arco server, so it skips.

Start the server by hand (it is a curses app with no headless mode):
    cd /Users/chris/projects/arco/apps/pytest && ./server
then run with:
    PYTHONPATH=/Users/chris/projects/arco python -m pytest tests/test_arco_synth.py
"""

from __future__ import annotations

import os

import pytest


def test_module_imports_without_pyarco():
    # The load-bearing property: importing this module must cost nothing when
    # Arco is absent, so the offline suite stays green.
    import sys

    import harness.arco_synth as mod

    assert hasattr(mod, "ArcoSynthPool")
    assert not [m for m in sys.modules if m.startswith("pyarco")]


def test_channels_are_allocated_and_recycled_without_a_server():
    # Channel bookkeeping is pure, so it is testable with no Arco at all.
    from harness.arco_synth import ArcoSynthPool

    pool = ArcoSynthPool(max_channels=2)
    pool._flsyn = object()                   # pretend start() ran
    a, b = pool.acquire(), pool.acquire()
    assert a.channel != b.channel
    with pytest.raises(RuntimeError):
        pool.acquire()                       # exhausted, and it says so
    pool.release(a)
    c = pool.acquire()
    assert c.channel == a.channel            # recycled


def test_acquire_before_start_is_a_clear_error():
    from harness.arco_synth import ArcoSynthPool

    with pytest.raises(RuntimeError) as ei:
        ArcoSynthPool().acquire()
    assert "start()" in str(ei.value)


@pytest.mark.skipif(not os.environ.get("MM_ARCO_LIVE"),
                    reason="needs a running Arco server; set MM_ARCO_LIVE=1")
def test_live_pool_acquires_sends_and_releases():
    pytest.importorskip("pyarco")
    from harness.arco_synth import ArcoSynthPool

    pool = ArcoSynthPool()
    pool.start()
    try:
        voice = pool.acquire()
        voice.program_change(89)
        voice.note_on(45, 90)
        voice.control_change(74, 100)
        pool.poll()
        voice.note_off(45)
        pool.release(voice)
    finally:
        pool.shutdown()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_arco_synth.py -v`
Expected: three FAIL with `ModuleNotFoundError: No module named 'harness.arco_synth'`; the live test SKIPs.

- [ ] **Step 3: Implement `harness/arco_synth.py`**

```python
"""ArcoSynthPool: the pyarco-backed SynthPool behind control/audio.py.

Dev/test-only, and a DELIBERATE holding position. Boundary rule 1 puts audio
decisions in Control, and the pure half of this already lives there; only the
pyarco-touching half sits in harness/, matching how luxaeterna is currently
carried (requirements-dev.txt, importorskip in tests). It moves into control/
once pyarco's source-of-truth is settled, which is bootstrap open question #1
and Roger Dannenberg's call. See
docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md section 5.

pyarco imports happen INSIDE start(), never at module level, so importing this
module costs nothing when Arco is absent and the offline suite stays green.

Run a client with:
    PYTHONPATH=/Users/chris/projects/arco <interpreter> ...
"""

from __future__ import annotations

DEFAULT_SOUNDFONT = "/Users/chris/projects/fluidsynth/sf2/VintageDreamsWaves-v2.sf2"


class ArcoVoice:
    """One MIDI channel on the shared Flsyn ugen.

    The channel is REAL but INTERNAL: no method here takes one, so callers use
    the same surface Roger's written notes describe (allocate N voices sharing
    one Flsyn) while the implementation does what his shipped MidiSender does.
    If the channel-parameter design wins instead, the change stops at this file.
    """

    def __init__(self, flsyn, channel: int) -> None:
        self._flsyn = flsyn
        self.channel = channel

    def note_on(self, key: int, vel: int) -> None:
        self._flsyn.noteon(self.channel, key, vel)

    def note_off(self, key: int) -> None:
        self._flsyn.noteoff(self.channel, key)

    def control_change(self, num: int, val: int) -> None:
        self._flsyn.control_change(self.channel, num, val)

    def program_change(self, prog: int) -> None:
        self._flsyn.program_change(self.channel, prog)

    def all_off(self) -> None:
        self._flsyn.alloff(self.channel)


class ArcoSynthPool:
    """One Flsyn ugen, up to max_channels voices sharing it."""

    def __init__(self, soundfont: str = DEFAULT_SOUNDFONT,
                 ensemble: str = "arco", max_channels: int = 16) -> None:
        self._soundfont = soundfont
        self._ensemble = ensemble
        self._free = list(range(max_channels))
        self._flsyn = None
        self._sched = None
        self._arco = None

    def start(self) -> None:
        """Connect to the Arco server and build the shared Flsyn.

        arco.initialize() BLOCKS until connected and reset, then poll() is all
        that is needed: we deliberately do not call sched.run(), because the
        driver loop owns the loop.
        """
        from pyarco import sched                       # noqa: PLC0415 (lazy by design)
        from pyarco.arco_engine import arco            # noqa: PLC0415
        from pyarco.ugens.flsyn import Flsyn           # noqa: PLC0415

        arco.initialize(ensemble=self._ensemble)       # raises TimeoutError if no server
        self._sched = sched
        self._arco = arco
        self._flsyn = Flsyn(self._soundfont)
        self._flsyn.play()

    def acquire(self) -> ArcoVoice:
        if self._flsyn is None:
            raise RuntimeError("ArcoSynthPool.start() must run before acquire()")
        if not self._free:
            raise RuntimeError(
                "no free MIDI channels: one Flsyn carries 16 voices")
        return ArcoVoice(self._flsyn, self._free.pop(0))

    def release(self, voice: ArcoVoice) -> None:
        if voice.channel not in self._free:
            self._free.append(voice.channel)
            self._free.sort()

    def poll(self) -> None:
        if self._sched is not None:
            self._sched.poll()                 # pumps o2lite via its poll functions

    def shutdown(self) -> None:
        """Silence every channel, then drop the Flsyn so pyarco's destructor
        frees the Arco ugen id. Boundary rule 1: Control owns the id space,
        which means freeing it at unload (see arco/doc/pyarco.md, "Ugen IDs")."""
        if self._flsyn is not None:
            for chan in range(16):
                self._flsyn.alloff(chan)
            self._flsyn = None
        if self._arco is not None:
            self._arco.finish()
            self._arco = None
        self._sched = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_arco_synth.py -v`
Expected: three PASS, one SKIP.

- [ ] **Step 5: Verify the whole offline suite is still green**

Run: `python -m pytest tests -q`
Expected: all PASS with no Arco server and no `PYTHONPATH` set.

- [ ] **Step 6: Commit**

```bash
git add harness/arco_synth.py tests/test_arco_synth.py && git commit -m "feat(harness): ArcoSynthPool, the pyarco-backed voice pool

One Flsyn ugen with up to 16 voices sharing it. pyarco imports are lazy and
inside start(), so importing this module costs nothing with no Arco present and
the offline suite stays green. The driver loop owns the loop: initialize()
blocks until ready, then poll() is all that is needed, never sched.run().

The channel is real but internal, so no caller names one. Lives in harness/ as
a holding position until pyarco's source-of-truth is settled."
```

---

### Task 7: `control/breath.py`, the breath generator

**Files:**
- Create: `control/breath.py`
- Test: `tests/test_breath.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `control.breath.breath_cc(t: float) -> int`, sampling luxaeterna's
  `_AURORA_BREATHE` shape at time `t` (looping every 6 s) scaled to 7-bit. Plus
  `BREATH_CC = 11`, `BREATHE_POINTS`, `BREATHE_PERIOD`. Task 8's driver loop and
  Task 9's `DeviceLinkAgent` both call it.

**Why this lives in `control/` and not in the demo.** Declaring `level` opts
aurora out of its private breathing clock, so **every** renderer of a
level-declaring role must now be fed the breath, not just the one demo. If the
generator lived inside `harness/led_smoke.py`'s `main()`, it would be a demo
script detail rather than part of the control architecture, and any other
consumer of the same role would render a static, unbreathing surface. That is
not hypothetical: it is exactly what `devicelink/` does today, which Task 9
fixes. Putting the generator in `control/` is what makes the "one shared control
stream" claim true rather than aspirational.

It is **not** in `control/audio.py`, because the breath is not audio. It is a
control signal both media consume, and a name that says so is worth one small
module.

**Why this exact shape:** reproducing `_AURORA_BREATHE` point for point means the
light looks identical to before: same 6 s period, same 0.55 floor, same
never-dark property. The only change is where the number comes from.

- [ ] **Step 1: Write the failing test**

Create `tests/test_breath.py`:

```python
"""The breath Control generates now that aurora no longer breathes itself.
Pure and offline: no luxaeterna, no Arco, no network."""

from __future__ import annotations

from control.breath import BREATH_CC, breath_cc


def test_breath_cc_is_midi_expression():
    # cc:11 is General MIDI Expression, which FluidSynth honors as a direct
    # attenuation, so the swell is audible on any soundfont.
    assert BREATH_CC == 11


def test_breath_cc_matches_auroras_own_envelope_at_the_knots():
    # Control now generates the breath aurora used to generate itself. These
    # are luxaeterna's _AURORA_BREATHE points scaled to 7-bit, so the light
    # looks identical to before: same period, same floor, never dark.
    assert breath_cc(0.0) == 70          # round(0.55 * 127)
    assert breath_cc(3.0) == 127
    assert breath_cc(6.0) == 70          # loops back to the start


def test_breath_cc_interpolates_between_the_knots():
    assert breath_cc(1.5) == 98          # round(0.775 * 127)


def test_breath_cc_rises_monotonically_over_the_first_half():
    vals = [breath_cc(t / 10.0) for t in range(0, 31)]
    assert vals == sorted(vals)


def test_breath_cc_falls_over_the_second_half():
    vals = [breath_cc(3.0 + t / 10.0) for t in range(0, 31)]
    assert vals == sorted(vals, reverse=True)


def test_breath_cc_never_reaches_zero():
    assert min(breath_cc(t / 10.0) for t in range(0, 61)) >= 70


def test_breath_cc_loops_rather_than_running_off_the_end():
    assert breath_cc(13.5) == breath_cc(1.5)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_breath.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'control.breath'`.

- [ ] **Step 3: Implement `control/breath.py`**

```python
"""The breath: a slow control signal Control generates and both media consume.

luxaeterna's aurora used to breathe on its own private clock. A role that
declares a `level` param opts out of that clock, so the envelope moves here and
travels as cc:11 on the shared MIDI stream. A light renderer binding cc:11 to
`level` and a sound engine binding it to expression then swell together, because
they are reading the same number in the same tick rather than two clocks that
happen to agree.

Consequence worth stating plainly: every renderer of a level-declaring role has
to be fed this, or it renders a static surface. harness/led_smoke.py and
devicelink/agent.py both tick it for that reason.

Pure and dependency-free: no luxaeterna, no pyarco, no clock of its own.
"""

from __future__ import annotations

BREATH_CC = 11        # General MIDI Expression: a direct attenuation in FluidSynth

# luxaeterna's _AURORA_BREATHE, point for point, so the light is unchanged.
BREATHE_POINTS = [(0.0, 0.55), (3.0, 1.0), (6.0, 0.55)]
BREATHE_PERIOD = 6.0


def breath_cc(t: float) -> int:
    """Sample the breath envelope at time t (looping), scaled to 7-bit MIDI."""
    phase = t % BREATHE_PERIOD
    for (x0, y0), (x1, y1) in zip(BREATHE_POINTS, BREATHE_POINTS[1:]):
        if phase <= x1:
            frac = 0.0 if x1 == x0 else (phase - x0) / (x1 - x0)
            return round((y0 + frac * (y1 - y0)) * 127)
    return round(BREATHE_POINTS[-1][1] * 127)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_breath.py -v`
Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add control/breath.py tests/test_breath.py && git commit -m "feat(control): the breath as a Control-generated cc:11 stream

Reproduces luxaeterna's _AURORA_BREATHE point for point, so the light looks
identical to before. The difference is where the number comes from: once
Control owns it, the light and the sound read the same value in the same tick
and cannot drift.

It lives in control/ rather than in the demo because declaring `level` opts
aurora out of its private clock, so every renderer of such a role must be fed
this. A generator inside one demo's main() would leave every other consumer
rendering a static surface."
```

---

### Task 8: Wire audio into the demo

**Files:**
- Modify: `harness/led_smoke.py`
- Test: `tests/test_led_smoke_cli.py`, `tests/test_led_smoke.py`

**Interfaces:**
- Consumes: `AudioBridge`, `FakePool` (Tasks 3 and 4), `ArcoSynthPool`, `DEFAULT_SOUNDFONT` (Task 6), `control.breath.breath_cc` and `BREATH_CC` (Task 7), TestBit's declarations (Task 5).
- Produces: `build(...)` now returns a **4-tuple** `(loop, session, gs, audio)` where `audio` is `None` unless a `pool` is passed. New `build` keyword: `pool=None`. New CLI flags `--audio`, `--soundfont`, `--program`.

**The shared-stream statement.** Both consumers must be fed from one place, so a
reader can confirm the property at a glance:

```python
for status, d1, d2 in stream:
    session.feed_midi(status, d1, d2)        # light
    if audio is not None:
        audio.feed_midi("sim-dev", status, d1, d2)   # sound, SAME bytes
```

- [ ] **Step 1: Write the failing tests**

Update `tests/test_led_smoke_cli.py`'s existing build test and add three:

```python
def test_build_constructs_headless_pipeline():
    loop, session, gs, audio = build(run_duration=float("inf"), serve=False)
    assert isinstance(gs.state, State)           # a real GameServer wired up
    assert callable(session.render_into)         # luxaeterna session ready to render
    assert loop is not None
    assert audio is None                         # no --audio: nothing audio exists


def test_build_with_a_pool_wires_audio_and_grants_a_voice():
    from control.audio import FakePool

    pool = FakePool()
    _loop, _session, _gs, audio = build(run_duration=float("inf"), serve=False,
                                        pool=pool)
    assert audio is not None
    assert len(pool.acquired) == 2               # drone voice + welcome cue voice


```

**Amended mid-execution.** An earlier draft of this step also added
`test_run_duration_default_still_test_bit_natural_with_audio_flag`. It was
byte-identical to `test_run_duration_default_is_test_bit_natural` and could not
exercise `--audio` at all, because `_run_duration()` never reads `args.audio`.
It was deleted in Task 8's fix round.

**Also amended:** the shared-stream statement below was extracted into a public
`feed_shared(session, audio, dev, pairs)` helper at module level in
`harness/led_smoke.py`, which **both** `main()` and
`test_one_cc_stream_reaches_both_the_light_and_the_audio` call. The original
plan had the test reimplement the pairing in its own body, which meant it would
have passed even if `main()` fed light and audio from two separate paths, and
that is the one property this whole feature exists to establish.

Add the shared-stream regression to `tests/test_led_smoke.py`:

```python
def test_one_cc_stream_reaches_both_the_light_and_the_audio():
    """The property this whole slice exists to establish. If someone later
    splits the stream into two timelines, this fails loudly."""
    from control.audio import FakePool
    from harness.led_smoke import build

    pool = FakePool()
    # A fake clock, like the test above it: build() threads it into both the
    # light bridge and the audio bridge, so the 1.5 s welcome plays out in
    # hand-driven ticks rather than real seconds.
    clk = iter([i * (1 / 44) for i in range(3000)]).__next__
    loop, session, gs, audio = build(run_duration=float("inf"), serve=False,
                                     clock=clk, pool=pool)
    loop.backend.open()          # build() does not open it; loop.start() would
    gs.run()
    for _ in range(300):                         # let the welcome play out
        loop._loop_once()
        if session.state == "running":
            break
    assert session.state == "running"

    for status, d1, d2 in ((0xB0, 74, 100), (0xB0, 11, 120)):
        session.feed_midi(status, d1, d2)
        audio.feed_midi("sim-dev", status, d1, d2)
    loop._loop_once()                            # drain the light queue

    drone_voice = pool.acquired[0]
    assert ("cc", 74, 100) in drone_voice.sent   # audio saw both controllers
    assert ("cc", 11, 120) in drone_voice.sent
    assert max(backend_frame(loop)) > 0          # light is still rendering them
```

and add this helper near the top of `tests/test_led_smoke.py`:

```python
def backend_frame(loop):
    """The most recent frame the loop's backend recorded."""
    return loop.backend.frames[-1]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_led_smoke_cli.py tests/test_led_smoke.py -v`
Expected: FAIL with `ValueError: not enough values to unpack (expected 4, got 3)` on the build tests, and `TypeError: build() got an unexpected keyword argument 'pool'`.

- [ ] **Step 3: Extend `build()`**

In `harness/led_smoke.py`, replace `build` with:

```python
def build(run_duration: float, host: str = HOST, port: int = PORT,
          serve: bool = True, clock=time.monotonic, pool=None):
    """Construct the demo pipeline WITHOUT starting the loop.

    Returns ``(loop, session, gs, audio)``. ``audio`` is None unless a
    SynthPool is passed, so the demo stays byte-identical without --audio.
    ``run_duration`` is threaded into TestBit via a factory so the Bit's
    RUNNING window is caller-controlled (``float('inf')`` = never completes).
    ``serve=False`` gives a record-only backend (no websockets, no port) for
    headless tests."""
    gs = GameServer({"test_bit": lambda: TestBit(run_duration=run_duration)})
    cap = shroom_capability()
    bridge = DeviceBridge(capability=cap, clock=clock)
    gs.on_release = bridge.on_release
    gs.load_bit("test_bit")
    result = gs.join("sim-dev", "TEST_PLAYER_NODE")
    session = bridge.on_grant(result)
    audio = None
    if pool is not None:
        # The audio declaration is read off the Role, not off the composed
        # /ie<N>/role blob: audio never ships to the device (boundary rule 1).
        audio = AudioBridge(pool, clock=clock)
        audio.on_grant("sim-dev", gs.bit.role_table.roles[result.role])
    uni = Universe()
    backend = WebSimBackend(capability=cap, host=host, port=port, serve=serve)
    loop = OutputLoop(uni, backend, on_frame=session.render_into, always_send=True)
    return loop, session, gs, audio
```

Add to the imports at the top of the file:

```python
from control.audio import AudioBridge
from control.breath import BREATH_CC, breath_cc
```

- [ ] **Step 4: Run the build tests to verify they pass**

Run: `python -m pytest tests/test_led_smoke_cli.py tests/test_led_smoke.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the CLI flags and the shared-stream loop**

Replace `main()` in `harness/led_smoke.py` with:

```python
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Watch (and hear) TestBit render on the Web LED simulator.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="Keep the Bit RUNNING/sweeping this long before it "
                         "completes + fades (default: TestBit's natural ~2 s).")
    ap.add_argument("--hold", action="store_true",
                    help="Serve until Ctrl-C (never auto-complete).")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--audio", action="store_true",
                    help="Also play sound through a running Arco server. Off "
                         "by default, so this demo needs no Arco to watch.")
    ap.add_argument("--soundfont", default=os.environ.get("MM_SOUNDFONT"),
                    help="SoundFont for the Flsyn ugen (default: "
                         "$MM_SOUNDFONT, else the venue soundfont).")
    ap.add_argument("--program", type=int, default=None,
                    help="Override the General MIDI program TestBit declares.")
    args = ap.parse_args()

    pool = None
    if args.audio:
        from harness.arco_synth import DEFAULT_SOUNDFONT, ArcoSynthPool
        pool = ArcoSynthPool(soundfont=args.soundfont or DEFAULT_SOUNDFONT)
        pool.start()                     # blocks until the Arco server answers

    loop, session, gs, audio = build(_run_duration(args), args.host, args.port,
                                     pool=pool)
    if audio is not None and args.program is not None:
        audio.feed_midi("sim-dev", 0xC0, args.program, 0)
    loop.start()
    print(f"Watch the Shroom at http://{args.host}:{args.port}/  (Ctrl-C to stop)")

    gs.run()
    started = time.monotonic()
    try:
        while session.state != "running":
            time.sleep(0.02)
        if audio is not None:
            audio.start_drone("sim-dev")     # FluidSynth is silent without a note
        cc, step = 0, 2
        while gs.state == State.RUNNING:
            breath = breath_cc(time.monotonic() - started)
            # ONE stream, two consumers. This is the property the whole slice
            # exists to establish: light and sound read the same numbers in the
            # same tick, so they cannot drift.
            for status, d1, d2 in ((0xB0, 74, cc), (0xB0, BREATH_CC, breath)):
                session.feed_midi(status, d1, d2)
                if audio is not None:
                    audio.feed_midi("sim-dev", status, d1, d2)
            if audio is not None:
                audio.tick()
            cc += step
            if cc >= 127 or cc <= 0:         # ping-pong (no wrap discontinuity)
                cc = max(0, min(127, cc))
                step = -step
            gs.tick(0.15)                    # advances TestBit toward complete
            time.sleep(0.15)
        if audio is not None:
            audio.on_release("sim-dev")
        time.sleep(1.2)                      # let the closing fade + idle play
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()
        if audio is not None:
            audio.shutdown()                 # frees the ugen id space
```

Add `import os` to the imports. `breath_cc`/`BREATH_CC` come from
`control.breath` (Task 7), NOT from this module: the generator lives in
`control/` so every renderer of a level-declaring role can be fed the same
breath, which is what Task 9 relies on.

Update the module docstring's usage block to add:

```
To hear it too, start the Arco server first (it is a curses app, so it needs a
real terminal):
    cd /Users/chris/projects/arco/apps/pytest && ./server
then:
    PYTHONPATH=/Users/chris/projects/arco python -m harness.led_smoke --audio --hold
```

- [ ] **Step 6: Run the full offline suite**

Run: `python -m pytest tests -q`
Expected: all PASS with no Arco server.

- [ ] **Step 7: Confirm the no-audio path is unchanged**

Run: `python -m harness.led_smoke --seconds 3`
Expected: runs and exits cleanly, printing the watch URL, exactly as before. No Arco needed.

- [ ] **Step 8: Commit**

```bash
git add harness/led_smoke.py tests/test_led_smoke_cli.py tests/test_led_smoke.py && git commit -m "feat(harness): led_smoke plays sound that tracks the light, off one stream

--audio (off by default) starts an Arco-backed voice pool and feeds the SAME
cc:74 and cc:11 bytes to the light session and the synth from one statement, so
the hue glide and the filter sweep move together and the visible breath and the
audible swell are one number.

Without --audio the demo is byte-identical to before and needs no Arco."
```

---

### Task 9: DeviceLink breathes too

**Files:**
- Modify: `devicelink/agent.py`
- Test: `tests/test_devicelink_agent.py`

**Interfaces:**
- Consumes: `control.breath.breath_cc`, `BREATH_CC` (Task 7); TestBit's
  `cc:11 -> level` light lane (Task 5).
- Produces: nothing later tasks consume.

**Why this task exists.** It was not in the original plan, and it is not
optional polish. Declaring `level` on TestBit's `player` role opts `aurora` out
of its private breathing clock for **every** consumer of that role, not just the
demo the plan was written around. `devicelink/agent.py` only forwards MIDI a
Bit's verb handler emits, and TestBit's `tilt` handler emits `cc:74` only, so
nothing there originates `cc:11`. Without this task, a device connected over
`harness/devicelink_smoke.py` renders a **static** aurora pinned at 0.55
forever: a visible regression in a demo that works today. The spec deferred
devicelink as out of scope; this turned out to be collateral damage rather than
deferral, so it is in scope now.

**Design notes for the implementer:**
- Feed the breath **unconditionally** to every joined, non-closing device.
  luxaeterna's `dispatch_midi` drops a cc with no matching lane, so a role that
  does not bind `cc:11` simply ignores it. Do not try to inspect the role's
  manifest to decide.
- **Skip devices in `_closing`.** They are rendering their release fade, and
  feeding the breath mid-fade would fight it.
- **Only send on change.** `breath_cc` returns an int that changes a few times a
  second; the tick loop runs at 44 Hz. Sending only when the value changes keeps
  the render path quiet without any timing assumption.
- Use the agent's existing `self._clock` seam. Do not call `time.monotonic()`
  directly: the tests drive a fake clock through it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_devicelink_agent.py`. Match the file's existing fixture style
for building an agent with a joined device rather than inventing a new one: read
the top of the file first and reuse whatever helper the existing tests use.

```python
def test_joined_device_receives_the_breath_on_cc11():
    # Declaring `level` opts aurora out of its own breathing clock, so Control
    # has to drive it. Without this, a devicelink device renders a static
    # surface: the regression this task exists to prevent.
    gs, server, agent, dev = _agent_with_joined_device()
    seen = []
    agent.bridges[dev].session.feed_midi = lambda s, a, b: seen.append((s, a, b))
    agent.poll()
    assert [m for m in seen if m[0] == 0xB0 and m[1] == BREATH_CC]


def test_breath_is_only_sent_when_the_value_changes():
    # 44 Hz tick, ~6 s envelope: resending an unchanged 7-bit value every frame
    # is pure noise on the render path.
    gs, server, agent, dev = _agent_with_joined_device()
    seen = []
    agent.bridges[dev].session.feed_midi = lambda s, a, b: seen.append((s, a, b))
    for _ in range(3):
        agent.poll()                      # fake clock barely advances
    breaths = [m for m in seen if m[1] == BREATH_CC]
    assert len(breaths) == 1


def test_a_closing_device_is_not_fed_the_breath():
    # It is rendering its release fade; the breath would fight it.
    gs, server, agent, dev = _agent_with_joined_device()
    agent._closing[dev] = 0
    seen = []
    agent.bridges[dev].session.feed_midi = lambda s, a, b: seen.append((s, a, b))
    agent.poll()
    assert not [m for m in seen if m[1] == BREATH_CC]
```

Add `from control.breath import BREATH_CC` to the file's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_devicelink_agent.py -v`
Expected: the three new tests FAIL (no breath is emitted at all, so the first
asserts an empty list and the third passes trivially; confirm the first two fail
and note whether the third was vacuous before the change).

- [ ] **Step 3: Implement the breath feed**

In `devicelink/agent.py`, add to the imports:

```python
from control.breath import BREATH_CC, breath_cc
```

In `__init__`, alongside the other per-device state:

```python
        # Control owns the breath now (control/breath.py): a role declaring
        # aurora's `level` param no longer breathes on its own clock, so every
        # renderer has to be fed cc:11 or it renders a static surface.
        self._breath_origin = self._clock()
        self._last_breath: dict[str, int] = {}
```

Add this method, and call `self._feed_breath()` from `poll()` immediately
before `self._render_frames()`:

```python
    def _feed_breath(self) -> None:
        """Drive every joined device's breath. Sent on change only, and never
        to a device mid-release-fade."""
        value = breath_cc(self._clock() - self._breath_origin)
        for dev, bridge in list(self.bridges.items()):
            if dev in self._closing or bridge.session is None:
                continue
            if self._last_breath.get(dev) == value:
                continue
            self._last_breath[dev] = value
            try:
                bridge.session.feed_midi(0xB0, BREATH_CC, value)
            except Exception:
                logger.exception("breath feed for %s failed", dev)
```

In `_finish_release`, alongside the other per-device cleanup pops, add:

```python
        self._last_breath.pop(dev, None)
```

and in `_on_join`, alongside `self._closing.pop(dev, None)`, add:

```python
        self._last_breath.pop(dev, None)
```

so a rejoining device is not starved of its first breath by a stale entry.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests/test_devicelink_agent.py -v`
Expected: all PASS, including every pre-existing test in the file.

- [ ] **Step 5: Run the full suite**

Run: `/Users/chris/projects/mm-terrarium/.venv/bin/python -m pytest tests -q`
Expected: all PASS with no Arco server.

- [ ] **Step 6: Commit**

```bash
git add devicelink/agent.py tests/test_devicelink_agent.py && git commit -m "fix(devicelink): drive the breath, so a device does not render static

Declaring aurora's level param opts it out of its own breathing clock for every
consumer of that role, not just the demo this work was written around. Nothing
in devicelink originates cc:11, so a connected device would have rendered a
static surface pinned at 0.55.

Sent on change only, and never to a device mid-release-fade, where it would
fight the fade."
```

---
### Task 10: Dependencies and documentation

**Files:**
- Modify: `requirements-dev.txt`
- Modify: `docs/MM_TERRARIUM.md`
- Modify: `docs/control-gameserver-design.md`

**Two documentation items added mid-execution** (see the amendment in
Self-Review): `control/breath.py` exists and is consumed by both `led_smoke` and
`devicelink`, and `devicelink/agent.py` now drives the breath. Both belong in
the deep-dive's write-up alongside the audio modules.

**Interfaces:**
- Consumes: everything above.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the optional audio dependencies**

Append to `requirements-dev.txt`:

```
# --- Optional: Arco audio (harness/led_smoke.py --audio, tests/test_arco_synth.py) ---
# pyarco and o2litepy are NOT pip-installable. They live in the sibling arco
# checkout and are reached by PYTHONPATH, following the dev/test-only precedent
# set for luxaeterna. Nothing is vendored or submoduled: pyarco's
# source-of-truth stays Roger Dannenberg's open decision (bootstrap question 1).
#
#     PYTHONPATH=/Users/chris/projects/arco python -m harness.led_smoke --audio
#
# o2litepy needs these two. tests/test_arco_synth.py skips without them, and
# control/audio.py never imports pyarco at all, so the core suite runs offline.
zeroconf
netifaces
```

- [ ] **Step 2: Install them and confirm the offline suite still passes**

Run: `python -m pip install -r requirements-dev.txt && python -m pytest tests -q`
Expected: installs cleanly on Python 3.14 (verified: both are present in
`/Users/chris/projects/arco/.venv`, same interpreter version) and all tests PASS.

- [ ] **Step 3: Update the deep-dive**

In `docs/MM_TERRARIUM.md`, make these edits:

1. In the `bits/` section, replace the final paragraph (beginning "Both of its
   light instruments are luxaeterna **field-rate** gestures") ending sentence
   "The running declaration therefore carries **no note lane**, and nothing in
   the pipeline feeds note-ons." with:

```markdown
The running declaration therefore carries **no note lane**. As of the
Tuneshroom audio slice the pipeline *does* feed a note-on, a sustained drone the
audio path needs because FluidSynth is silent without one, but light still
ignores it: no note lane, so the strobe fix holds. `player` also gained a
`cc:11 -> level` lane, which opts `aurora` out of its private breathing clock so
Control generates the breath and the light and the sound read the same number.
```

2. In the `harness/` section, append:

```markdown
`--audio` (off by default) additionally starts an Arco-backed voice pool and
feeds the **same** `cc:74`/`cc:11` bytes to the synth that go to the light
session, from one statement. `cc:74` glides aurora's hue and sweeps FluidSynth's
filter; `cc:11` drives aurora's `level` and the synth's expression, so the
visible breath and the audible swell are one value rather than two clocks that
agree. Needs a hand-started Arco server (`apps/pytest/server` is a curses app)
and `PYTHONPATH=/Users/chris/projects/arco`. Without the flag the demo is
unchanged and needs no Arco.
```

3. Add a new subsection after `harness/`:

```markdown
### `control/audio.py` + `harness/arco_synth.py`: the first Arco write path
`AudioBridge` is the audio-side sibling of `DeviceBridge`: it reads a role's
`ugen_manifest`, acquires a voice, applies the role's declared cc lanes, holds
the drone, plays the welcome audio half (its first consumer since PR #5), and
frees every voice at unload. It is **pure and never imports pyarco**, which is
what keeps the offline suite green. `ArcoSynthPool` is the concrete backend: one
`Flsyn` ugen with up to 16 voices sharing it, lazy pyarco imports inside
`start()`, and `sched.poll()` driven from the existing tick rather than
`sched.run()` owning the loop.

Two things here are **deliberately provisional**. The voice type is
`DeviceVoice`, not `Synth`, and takes **no channel parameter**: the channel is
real but internal, so the abstraction Roger has open is not frozen by this demo.
And `ugen_manifest` v0 is *not* the audio-manifest freeze that light-manifest v2
was for light: shallow validation, no cross-repo contract, no device-side parser.
The backend living in `harness/` is likewise a holding position until pyarco's
source-of-truth is settled.
```

4. In *Relationships to other repos*, replace the **pyarco** bullet with:

```markdown
- **pyarco**: the Python control layer Control+GameServer builds ugen graphs
  through. Now a **dev/test-only dependency reached by `PYTHONPATH`**, following
  the luxaeterna precedent: nothing is vendored or submoduled, and
  `control/audio.py` never imports it, so the whole suite still runs offline.
  Its source-of-truth (submodule vs. pinned sibling) remains Roger Dannenberg's
  open decision.
```

5. In *Not yet built / deferred*, replace the "**Real ugen graph-building on
   Arco** and **real scoring**" bullet with:

```markdown
- **Real ugen graph-building on Arco** has a first, provisional slice: the
  Tuneshroom audio demo builds one `Flsyn` and up to 16 voices, driven by a
  role's `ugen_manifest` v0. Still unbuilt: per-role synthesis beyond
  FluidSynth, the real Flsyn-parameterizing manifest schema, audio over the
  device wire, and **real scoring** (`on_complete()` is still a stub hook).
```

6. In the same list, update the `light_manifest` parenthetical. It currently
   ends (wrapped across two source lines, so match on the pieces rather than
   pasting one string): "the o2lite transport that reads `JoinResult.config`,
   and the Arco cue path that plays the welcome audio half, are both unbuilt."
   Rewrite the tail so it reads: "the o2lite transport that reads
   `JoinResult.config` is still unbuilt; the Arco cue path that plays the
   welcome audio half now exists in `control/audio.py`."

7. Add to *Design docs (in-repo, authoritative)*:

```markdown
- Tuneshroom audio:
  [`.../2026-08-06-tuneshroom-audio-design.md`](https://github.com/Musical-Mycology/mm-terrarium/blob/main/docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md).
```

- [ ] **Step 4: Add the design-doc note**

Boundary rule 1 is honored, not amended, so this is a short record only. In
`docs/control-gameserver-design.md`, under *Open Questions*, add:

```markdown
- **The Control-side `Synth` abstraction is still open with Roger Dannenberg.**
  His written notes argue against a channel parameter (allocate up to 16
  `Synth`s sharing one `Flsyn`); his shipped `MidiSender` in
  `arco/apps/pytest/miditest.py` takes `chan` on every method. Those disagree
  about the API, not the implementation. The Tuneshroom audio slice ships a
  provisional `DeviceVoice` that keeps the channel **internal**, so callers use
  the no-channel surface while the backend does what `MidiSender` does. It is
  named `DeviceVoice` precisely so it is not read as this question having been
  answered. See
  `docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md` section 9.1.
```

- [ ] **Step 5: Verify no em dashes crept in**

Run: `grep -c "—" docs/MM_TERRARIUM.md docs/control-gameserver-design.md control/audio.py harness/arco_synth.py harness/led_smoke.py bits/test_bit.py`
Expected: `docs/MM_TERRARIUM.md` and `docs/control-gameserver-design.md` have pre-existing em dashes, which is fine; the count must not have **increased** for them, and the four Python files must report `0`. Check with `git diff` that no added line contains one.

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt docs/MM_TERRARIUM.md docs/control-gameserver-design.md && git commit -m "docs(terrarium): the first Arco write path, and what stays provisional

Records the audio slice in the deep-dive: control/audio.py and
harness/arco_synth.py, pyarco as a dev/test-only PYTHONPATH dependency, the
welcome audio half finally having a consumer, and real ugen graph-building
moving from unbuilt to a first provisional slice.

Notes in the design doc that the Synth channel question remains open with Roger
and that DeviceVoice is named so as not to answer it."
```

---

### Task 11: Live acceptance

**Files:** none. This task changes nothing; it verifies.

**Interfaces:**
- Consumes: everything above.
- Produces: a reported result. **Report what actually happened, including
  failures. Do not assert it should work.**

- [ ] **Step 1: Ask Chris to start the Arco server**

The server is a curses application: it calls `fopen("/dev/tty")` and has no
headless mode, so it **cannot be launched from a tool call**. Post this block
and wait:

**RUN ON: MYCOLOGICAL**

```bash
cd /Users/chris/projects/arco/apps/pytest && ./server
```

- [ ] **Step 2: Confirm the live pool test passes**

Run: `MM_ARCO_LIVE=1 PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m pytest tests/test_arco_synth.py -v`
Expected: four PASS (the live test no longer skips). If it fails with
`TimeoutError: Could not connect to Arco server`, the server is not running or
is on a different O2 ensemble name.

- [ ] **Step 3: Run the demo**

**RUN ON: MYCOLOGICAL**

```bash
cd /Users/chris/projects/mm-terrarium && PYTHONPATH=/Users/chris/projects/arco .venv/bin/python -m harness.led_smoke --audio --hold
```

- [ ] **Step 4: Judge the acceptance criteria by watching and listening at once**

1. The Shroom breathes in the browser exactly as it did before, and the sound
   swells and fades **with** that breath, not near it.
2. As the hue glides through the `cc:74` ping-pong, the timbre moves with it.
3. The welcome chime is heard at grant, at the moment the glow welcome is seen.
4. On completion the drone stops and the light fades.
5. `python -m harness.led_smoke` with no `--audio`, Arco server stopped, behaves
   exactly as before.
6. `python -m pytest tests` passes with the Arco server stopped.

- [ ] **Step 5: If program 89 is wrong, pick a better one by listening**

`--program 89` is a guess at a sustained pad in VintageDreamsWaves-v2. If it is
percussive (nothing sustains, so the breath has nothing to swell) or does not
respond to `cc:74`, try other programs with `--program N` until one sustains and
responds, then update the number in `bits/test_bit.py` and commit. **This is a
tuning change, not a design change.** Criterion 1 does not depend on it:
`cc:11` expression is program-independent.

- [ ] **Step 6: Report the result honestly**

State what was heard and seen against each criterion. If a criterion failed, say
so with the output, rather than reporting completion.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| 3 Two controllers, shared stream | 5 (declarations), 7 (breath), 8 (fan-out statement) |
| 3.1 Preserving the current look | 7 (`breath_cc` reproduces `_AURORA_BREATHE`), 9 (devicelink keeps breathing) |
| 4 luxaeterna `level` param | 1 |
| 4.1 luxaeterna tests | 1 Step 1 |
| 5 `control/audio.py` | 3, 4 |
| 5 `harness/arco_synth.py` | 6 |
| 5 `led_smoke.py` flags | 8 |
| Collateral: devicelink must be fed the breath | 9 (added mid-execution, see below) |
| 6 `ugen_manifest` v0 | 2 (schema + validation), 5 (declaration) |
| 7 Welcome audio cue | 4 |
| 8 Testing, offline suite green | 3, 4, 6 (import hygiene), 8 (shared-stream regression) |
| 9.1 `Synth` not frozen | 3 (naming, docstring), 9 (design-doc note) |
| 9.2 `ugen_manifest` not frozen | 2 (comment), 9 (deep-dive) |
| 9.3 New dependency stated | 10 |
| 10 Verification | 11 |
| 12 Documentation | 10 |

**Amendment, 2026-08-06 (mid-execution).** Spec section 11 listed
`harness/devicelink_smoke.py` as out of scope. That was wrong, and Task 5's
implementer caught it: declaring `level` on TestBit's shared `player` role
opts aurora out of its private breathing clock for **every** consumer of that
role, and nothing in `devicelink/` originates `cc:11`. Leaving it alone would
have shipped a working demo in a visibly worse state (a static surface pinned
at 0.55). So the breath generator moved from `harness/led_smoke.py` into
`control/breath.py` (Task 7) and `devicelink/agent.py` now ticks it (Task 9).
The spec's section 11 was updated to match.

**Type consistency check:** `DeviceVoice` methods (`note_on`, `note_off`,
`control_change`, `program_change`, `all_off`) are identical in the protocol
(Task 3), `FakeVoice` (Task 3), and `ArcoVoice` (Task 6). `SynthPool` methods
(`acquire`, `release`, `poll`, `shutdown`) are identical in the protocol,
`FakePool`, and `ArcoSynthPool`. `AudioBridge.feed_midi(dev, status, d1, d2)`
has the same signature everywhere it is called (Tasks 3, 4, 8).
`build()` returns a 4-tuple in Task 8 and every caller unpacks four.
`breath_cc(t)` is defined in Task 7 and called in Task 8.
