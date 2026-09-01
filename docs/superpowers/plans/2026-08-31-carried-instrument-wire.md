# Carried-Instrument Wire Support Implementation Plan (Slice 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Devices declare which published catalog instrument they carry at
hello; unresolved devices fall to a new DEFAULTSHROOM floor; the full
instrument definition ships down in the role blob; Testshroom proves the
loop and a contract doc hands it to mm-tuneshroom.

**Architecture:** `DEFAULTSHROOM` joins `TUNESHROOM` as a code-defined
constant with a pinned catalog file. `/game/hello` gains an optional 4th
argument; `GameServer.hello` resolves it against code constants plus the
config-loaded catalog and stores the result on `DeviceInfo.carried`
(default flipped to DEFAULTSHROOM -- a deliberate breaking change gated
on the mm-tuneshroom follow-up). `compose_role_config` gains an
`"instrument"` blob section (capabilities, pixels, ambient manifests,
functions in the existing wire-view shape). A heartbeat hello without the
argument preserves an already-declared instrument.

**Tech Stack:** Python stdlib throughout (`control/` discipline); no
luxaeterna/pyarco; offline suite.

**Spec:** `docs/superpowers/specs/2026-08-31-carried-instrument-wire-design.md`

## Global Constraints

- Run tests via `.venv/bin/python -m pytest tests -q`; worktree needs the
  `.venv` symlink. Baseline at plan start: **1841 passed, 1 skipped**
  (main @ 22d797f).
- `control/` imports stdlib only. The suite stays fully offline.
- Published catalog entries only on the wire; drafts never resolve.
- Fallback semantics verbatim from the spec: declared+resolved -> that
  instrument; unknown -> DEFAULTSHROOM + console-visible warning;
  absent -> DEFAULTSHROOM, silent (documented legacy meaning) -- EXCEPT
  a hello on an already-known device with no instrument argument
  preserves that device's existing carried instrument (heartbeat rule).
- 12-LED floor: `pixels >= 12` required whenever `light.pixels` is
  declared, refused at validate time with a located error.
- Never-null blob discipline: the `"instrument"` section ships for every
  granted non-ROOM join; ROOM joins ship nothing new.
- No em dashes in authored prose; docs use " -- ".
- Commit per task; full suite green before each commit.

---

### Task 1: Model -- DEFAULTSHROOM, audio.mic, pixels floor

**Files:**
- Modify: `control/instrument.py` (CAPABILITY_VOCABULARY, Instrument,
  validate_instrument, TUNESHROOM, new DEFAULTSHROOM),
  `control/terrarium_config.py` (`_parse_instrument` parses `pixels`),
  `instruments/tuneshroom.toml`
- Create: `instruments/defaultshroom.toml`
- Test: `tests/test_instrument.py` (append), `tests/test_catalog.py`
  (append), `tests/test_terrarium_config.py` (append)

**Interfaces:**
- Consumes: existing `Instrument` dataclass, `validate_instrument`,
  `validate_instrument_manifests`, the TUNESHROOM constant/file pinning
  pattern (`tests/test_catalog.py::test_shipped_tuneshroom_catalog_file_matches_the_code_constant`).
- Produces:
  - `"audio.mic"` in `CAPABILITY_VOCABULARY`.
  - `Instrument.pixels: int = 0` (0 = undeclared).
  - `validate_instrument` refuses `light.pixels` instruments with
    `pixels < 12`: message
    `instrument {name!r}: light.pixels requires pixels >= 12 (the
    DefaultShroom floor), got {pixels}`.
  - `DEFAULTSHROOM: Instrument` -- name `"defaultshroom"`, description
    `"Ecosystem floor: any 12-LED instrument host"`, `pixels=12`,
    capabilities `{light.pixels, gesture.tap, gesture.tilt}`,
    `accepted_cues=("midi", "play", "solid", "mute")`, TUNESHROOM's two
    event triggers copied verbatim (same guessed thresholds, same
    provenance caveat in a comment), and a minimal ambient light
    manifest `{"instruments": [{"instrument": "aurora", "target":
    "primary"}]}` so an idle unknown device glows instead of staying
    dark. Validate the constant with `validate_instrument` +
    `validate_instrument_manifests` in a test; if the manifest shape
    needs more keys to validate, fix constant AND file together.
  - TUNESHROOM gains `pixels=12` and `"audio.mic"` in capabilities.
  - `_parse_instrument` reads a top-level `pixels = <int>` key
    (default 0; non-int is a located TerrariumConfigError).
  - `instruments/defaultshroom.toml` pinned equal to the constant;
    `instruments/tuneshroom.toml` updated to stay pinned (add
    `pixels = 12` and `"audio.mic"` to its capabilities line).

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_instrument.py (adapt imports to the file's own)
from control.instrument import (DEFAULTSHROOM, TUNESHROOM, Instrument,
                                InstrumentError, validate_instrument,
                                validate_instrument_manifests)


def test_defaultshroom_is_a_valid_12_led_floor():
    validate_instrument(DEFAULTSHROOM)
    validate_instrument_manifests(DEFAULTSHROOM)
    assert DEFAULTSHROOM.name == "defaultshroom"
    assert DEFAULTSHROOM.pixels == 12
    assert "light.pixels" in DEFAULTSHROOM.capabilities
    assert {t.name for t in DEFAULTSHROOM.event_triggers} == {"tap", "shake"}
    assert DEFAULTSHROOM.light_manifest  # idle glow, not dark


def test_tuneshroom_declares_pixels_and_mic():
    assert TUNESHROOM.pixels == 12
    assert "audio.mic" in TUNESHROOM.capabilities


def test_light_pixels_requires_the_12_led_floor():
    import pytest
    bad = Instrument(name="tiny", capabilities=frozenset({"light.pixels"}),
                     pixels=11)
    with pytest.raises(InstrumentError) as exc:
        validate_instrument(bad)
    assert "pixels >= 12" in str(exc.value)
    validate_instrument(Instrument(
        name="ok", capabilities=frozenset({"light.pixels"}), pixels=12))
    validate_instrument(Instrument(  # non-light.pixels exempt
        name="surface", capabilities=frozenset({"light.surface"})))
```

```python
# append to tests/test_catalog.py
def test_shipped_defaultshroom_catalog_file_matches_the_code_constant():
    from control.instrument import DEFAULTSHROOM
    cat = load_catalog(Path("instruments"))
    assert cat.published["defaultshroom"] == DEFAULTSHROOM
```

  plus a `tests/test_terrarium_config.py` test that `pixels = 12` parses
  onto the instrument and a non-int `pixels = "many"` is a located
  TerrariumConfigError naming the instrument.
- [ ] **Step 2: Run, verify failure:** `.venv/bin/python -m pytest tests/test_instrument.py tests/test_catalog.py -q` -> ImportError/AssertionError.
- [ ] **Step 3: Implement** per Produces. The existing tuneshroom pin
  test will fail until the TOML file gains the two new values -- fix the
  files, never the constants.
- [ ] **Step 4: Run full suite** (config parsing + instrument validation
  touched everywhere).
- [ ] **Step 5: Commit:** `git commit -m "feat(instrument): DEFAULTSHROOM floor, audio.mic, pixels validation"` with all touched files.

---

### Task 2: Engine -- hello resolution and the carried default flip

**Files:**
- Modify: `control/device_pool.py` (DeviceInfo default, hello signature),
  `control/engine.py` (GameServer kwarg, hello, carried dict at ~line 336,
  a device-warning notify), `console/agent.py` + `console/protocol.py`
  (surface the warning as a console log line the way load warnings are)
- Test: `tests/test_device_pool.py` (append; create if absent -- check),
  `tests/test_engine.py` or the file holding GameServer.hello tests
  (grep for `def test_.*hello` under tests/ and append there),
  `tests/test_console_agent.py` (append)

**Interfaces:**
- Consumes: Task 1's `DEFAULTSHROOM`; `TerrariumConfig.instruments`
  (dict[str, Instrument], already merged from the catalog).
- Produces:
  - `DeviceInfo.carried` default = `DEFAULTSHROOM`.
  - `DevicePool.hello(dev, name, protoversion, now=0.0, carried=None)`:
    `carried=None` on a KNOWN dev preserves the existing entry's
    carried instrument (heartbeat rule); `carried=None` on an unknown
    dev -> DEFAULTSHROOM; a passed Instrument is stored.
  - `GameServer.__init__(..., carried_instruments=None)`: a
    `dict[str, Instrument]` of config/catalog instruments; stored as
    `self.carried_instruments` = `{TUNESHROOM.name: TUNESHROOM,
    DEFAULTSHROOM.name: DEFAULTSHROOM, **(carried_instruments or {})}`.
    The load-time dict at engine.py:336 now reads
    `self.carried_instruments` instead of building its own.
  - `GameServer.hello(dev, name, protoversion, instrument=None)`:
    resolves `instrument` through `self.carried_instruments`; unknown
    name -> DEFAULTSHROOM plus
    `self._notify("on_device_warning", f"device {dev!r} declared unknown
    instrument {instrument!r}; using defaultshroom")`; absent -> None
    passed through to DevicePool (heartbeat rule applies).
  - Observer hook `on_device_warning(message: str)` -- guarded like the
    other notifies; `ConsoleAgent` implements it by appending a log
    line through the same mechanism `on_load_warnings` already uses
    (read that path first and mirror it exactly, including the wire
    event it produces).
  - `console/protocol.py`'s `device_view` gains
    `"instrument": info.carried.name` (read the current signature and
    thread it; update its callers in `_devices_view`).

- [ ] **Step 1: Write failing tests** covering: default carried is
  defaultshroom; hello with a config-declared name resolves it (build a
  GameServer with `carried_instruments={"glowharp": <Instrument>}`);
  hello with an unknown name lands defaultshroom AND fires
  on_device_warning (attach a recording observer); heartbeat -- hello
  with a name, then hello again with `instrument=None`, carried is
  preserved; declared tuneshroom resolves the constant; console agent
  test asserting the warning reaches the log/event path and that
  `device_view` rows carry `"instrument"`.
- [ ] **Step 2: Run, verify failure; implement; run full suite.** Expect
  collateral: any existing test asserting the TUNESHROOM default (grep
  `carried` under tests/) updates to DEFAULTSHROOM -- that flip is the
  spec's deliberate breaking change, adjust the tests to the new
  contract and note each in the report.
- [ ] **Step 3: Commit:** `git commit -m "feat(engine): hello resolves the carried instrument; DEFAULTSHROOM default"`.

---

### Task 3: Blob -- the instrument section

**Files:**
- Modify: `control/role_config.py` (`compose_role_config`),
  `control/engine.py` (join passes the carried Instrument)
- Test: `tests/test_role_config.py` (append; find the compose tests),
  the engine join/blob tests file (grep `compose_role_config` under
  tests/ and append beside the triggers-key tests)

**Interfaces:**
- Consumes: Task 1's `Instrument.pixels`; `control/function_view.py`'s
  `function_view(function_decl) -> dict` (the existing wire shape for
  functions).
- Produces: `compose_role_config(..., carried: Instrument | None = None)`
  -- when a non-None Instrument is passed, the blob gains

```python
    config["instrument"] = {
        "name": carried.name,
        "capabilities": sorted(carried.capabilities),
        "pixels": carried.pixels,
        "ambient": {"light": copy.deepcopy(carried.light_manifest),
                    "ugen": copy.deepcopy(carried.ugen_manifest)},
        "functions": [function_view(f) for f in carried.functions],
    }
```

  (deep-copied like the triggers key; key omitted entirely when None --
  never present as null, matching the file's stated discipline).
  `GameServer.join` passes `carried=carried` for every granted non-ROOM
  join, right beside the existing `event_triggers=carried.event_triggers`
  argument; ROOM joins are untouched (they never reach compose).
  Import `function_view` inside `compose_role_config` if a module-level
  import would create a cycle -- check `control/function_view.py`'s
  imports first.

- [ ] **Step 1: Write failing tests**: composing with
  `carried=DEFAULTSHROOM` yields the exact section above (assert full
  dict equality on name/capabilities/pixels and presence of ambient
  light); composing with `carried=None` omits the key entirely; the
  section is a deep copy (mutate the blob, assert the constant
  unchanged); an engine-level join test asserting `result.config
  ["instrument"]["name"] == "defaultshroom"` for a granted TestBit
  jammer join with no declaration.
- [ ] **Step 2: Run, verify failure; implement; run full suite.**
- [ ] **Step 3: Commit:** `git commit -m "feat(blob): carried instrument definition ships in the role config"`.

---

### Task 4: Transports and boot wiring

**Files:**
- Modify: `devicelink/agent.py` (`_on_hello` reads args[3]),
  `devicelink/o2_transport.py` (hello typespec tolerance -- read how the
  hello handler registers/forwards; if it forwards raw args to the same
  `_on_hello`, only the handler registration's typespec needs to accept
  both `"sss"` and `"ssss"` -- check what o2lite handler registration
  allows and pick the mechanism that keeps old clients working),
  `harness/terrarium_boot.py` (thread `terrarium_config.instruments`
  into the `GameServer(...)` construction at ~line 267)
- Test: `tests/test_devicelink_agent.py` (append; find the _on_hello
  tests), `tests/test_o2_transport.py` (append), `tests/test_terrarium_boot.py` (append)

**Interfaces:**
- Consumes: Task 2's `GameServer.hello(..., instrument=None)` and
  `GameServer.__init__(carried_instruments=...)`.
- Produces: wire shape -- `/game/hello` args are
  `[dev, name?, protoversion?, instrument?]`; the websocket agent reads
  `args[3] if len(args) > 3 else None` and passes it through; the o2
  path accepts both old and new arities; boot passes
  `carried_instruments=terrarium_config.instruments` when a config is
  loaded (None otherwise, preserving bare-GameServer construction).

- [ ] **Step 1: Write failing tests**: agent-level hello with 4 args
  reaches `GameServer.hello` with the instrument string (fake/recording
  game_server, matching the file's fixtures); 3-arg hello passes None;
  o2-transport test for both typespecs (mirror the file's existing
  hello-delivery test); boot test asserting the built GameServer's
  `carried_instruments` contains a config instrument name (e.g.
  `"venue_array" in gs.carried_instruments` -- config instruments merge
  in wholesale; carried-vs-fixture filtering is not this slice's
  concern, resolution just looks names up).
- [ ] **Step 2: Run, verify failure; implement; run full suite.**
- [ ] **Step 3: Commit:** `git commit -m "feat(wire): hello carries the instrument name over both transports"`.

---

### Task 5: Testshroom declaration + round-trip smoke

**Files:**
- Modify: `harness/shroom_client.py` (`hello()` gains the declaration;
  an `instrument=None` constructor/param following the file's existing
  per-instance-config pattern like `expected_channels`),
  `harness/o2_shroom.py` and the websocket driver path in
  `shroom_client.py`'s `main()` (`--instrument <name>` flag threaded
  through; the o2 heartbeat re-hello must RESEND the declaration so the
  heartbeat rule never has to fire for a well-behaved client)
- Test: `tests/test_shroom_client.py` (append; find the hello() unit
  tests), `tests/test_devicelink_agent.py` or the round-trip smoke test
  file (grep for the existing hello->join->role blob round-trip test and
  extend it)

**Interfaces:**
- Consumes: Task 4's wire shape.
- Produces: `ShroomClient(..., instrument=None)`; `hello()` emits
  typespec `"ssss"` and args `[dev, name, protoversion, instrument]`
  when declared, the old shape when not (read the current `hello()` --
  it sends `"s"` `[dev]` today; extend to send name/protoversion too
  only if the current shape already does elsewhere; otherwise keep the
  arity minimal: `"ss"`-style growth is NOT needed -- send
  `[dev, "", "", instrument]` with typespec `"ssss"` so the instrument
  lands in args[3] exactly where the agent reads it). Round-trip test:
  a declared `"tuneshroom"` join's blob has
  `config["instrument"]["name"] == "tuneshroom"` and `pixels == 12`;
  an undeclared client's blob says `"defaultshroom"`.

- [ ] **Step 1: Write failing tests; Step 2: implement; Step 3: full suite; Step 4: Commit:** `git commit -m "feat(harness): Testshroom declares its carried instrument"`.

---

### Task 6: Contract doc + deep-dive + spec status

**Files:**
- Create: `docs/carried-instrument-schema.md`
- Modify: `docs/MM_TERRARIUM.md`,
  `docs/superpowers/specs/2026-08-31-carried-instrument-wire-design.md`
  (Status section)

- [ ] **Step 1:** Write `docs/carried-instrument-schema.md` on the
  `docs/telemetry-trace-schema.md` pattern (read it first): the hello
  argument (position, typespec, both transports), the resolution table
  (declared/unknown/absent + the heartbeat preservation rule), the
  blob's `"instrument"` section shape with a worked JSON example, the
  12-LED floor, published-only rule, and a compatibility section stating
  plainly: mm-tuneshroom MUST send `"tuneshroom"` on hello before this
  deploys to a real room, or hardware becomes a DefaultShroom.
- [ ] **Step 2:** Deep-dive: append a `###` section in the landed
  chronology (carried-instrument wire, DEFAULTSHROOM, the default-flip
  breaking change and its mm-tuneshroom gate, the heartbeat rule, the
  pixels floor, audio.mic); mark the "every hello'ing device is presumed
  TUNESHROOM"-shaped claims in earlier sections with the file's
  supersession-parenthetical style. " -- " style, no em dashes.
- [ ] **Step 3:** Spec Status section: implemented on this branch;
  mm-tuneshroom follow-up outstanding (to be spawned at closeout).
- [ ] **Step 4:** Full suite; record the count. Commit:
  `git commit -m "docs: carried-instrument contract, deep-dive, spec status"`.

---

## Self-review notes (already applied)

- Spec coverage: section 1 -> T1; section 2 -> T2 (+heartbeat wrinkle
  the spec implies via "re-hello re-resolves"); section 3 -> T3;
  section 4 -> T2/T4; section 5 -> T5/T6; section 6 covered across
  task tests; section 7 exclusions honored (no draft resolution, no
  device-side rendering, no audio negotiation).
- The mm-tuneshroom spawn happens at closeout via spawn_task, not in a
  plan task -- it is a controller/closeout action, recorded in T6's
  contract doc and the spec Status.
- Type consistency: `carried_instruments` dict name identical in T2/T4;
  `compose_role_config(carried=...)` in T3 matches the engine call;
  `DEFAULTSHROOM` spelled once everywhere; blob key `"instrument"`
  consistent across T3/T5.
