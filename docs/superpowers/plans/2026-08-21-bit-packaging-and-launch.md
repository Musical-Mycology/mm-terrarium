# Bit Packaging and Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn each Bit into a discoverable `bits/<name>/` package with a declarative `bit.toml` manifest, and make the launch scripts, Console, and uplink discover, configure, and launch Bits from those manifests.

**Architecture:** A pure `control/bit_config.py` parses manifests into a frozen `BitConfig` tree; `control/bit_registry.py` discovers packages without importing Bit code; `GameServer.load_bit` gains an optional opaque `config` argument; the harness resolves defaults with precedence manifest < profile < CLI; start conditions are evaluated in the harness through the existing observer/hold machinery, never in the engine.

**Tech Stack:** Python 3.11+ stdlib only (`tomllib`, `dataclasses`, `importlib`). Suite runs fully offline via `.venv/bin/python -m pytest tests -v`.

**Spec:** `docs/superpowers/specs/2026-08-21-bit-packaging-and-launch-design.md`

## Global Constraints

- Run all tests through the project venv: `.venv/bin/python -m pytest ...` (a worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first; never bare `python3`).
- `control/` must stay module-level-import free of luxaeterna/pyarco/o2litepy; `tomllib` and `importlib` are stdlib and fine.
- Discovery must never import Bit code; a broken manifest disables only its own package.
- Unknown manifest keys/tables warn (once, via `logging`); unknown `kind` and malformed values fail as located errors.
- Every outbound JSON payload goes through `control/wire_json.dumps()`, never bare `json.dumps`.
- Engine stays Bit-agnostic: no start-condition logic, no manifest knowledge in `control/engine.py` beyond the opaque `config` pass-through.
- Behavior of the three migrated Bits must be byte-identical with an empty override set (existing tests pin this).
- Commit after every task; commit messages follow the repo's `feat:/fix:/refactor:/test:/docs:` style.

---

### Task 1: `control/bit_config.py` — manifest schema, parsing, and merge

**Files:**
- Create: `control/bit_config.py`
- Test: `tests/test_bit_config.py`

**Interfaces:**
- Produces:
  - `KINDS = frozenset({"music", "r_game", "game", "tool", "ambient"})`
  - `class ManifestError(Exception)` with attributes `source: str`, `key: str`, `message: str`; `str()` reads `"<source>: [<key>] <message>"`.
  - Frozen dataclasses (all fields shown are the full v1 schema):
    - `BitIdentity(name: str, version: str = "", description: str = "", entry: str = "", kind: str = "game", author: str = "", min_terrarium: str = "")`
    - `LaunchConfig(room_types: tuple[str, ...] = ("TEST",), default_room_type: str = "TEST", default_devices: int = 1, setup_seconds: float = 0.0, expected_run_seconds: float | None = None, transport: str = "any", nodes: tuple[tuple[str, str], ...] = (), default_join_role: str = "")`
    - `StartCondition(when: str = "immediate", min_scored: int = 1, timeout_seconds: float | None = None, on_timeout: str = "start")`
    - `ConsoleBlock(display_name: str = "", notes: str = "", hidden: bool = False)`
    - `RhythmConfig(bpm: float = 100.0, beats_per_cycle: int = 8, cycles: int = 4, grading_window_ms: float = 50.0, input_offset_ms: float = 0.0)`
    - `AmbientConfig(jam_control: bool = False, default_pattern: str = "")`
    - `BitConfig(identity: BitIdentity, launch: LaunchConfig, start: StartCondition, console: ConsoleBlock, results_keys: tuple[str, ...] = (), assets: tuple[tuple[str, str], ...] = (), rhythm: RhythmConfig | None = None, ambient: AmbientConfig | None = None, extras: dict = field(default_factory=dict))`
    - `BitConfig.node_for(role: str) -> str | None` helper; `BitConfig.join_node() -> str | None` (the `default_join_role`'s node, else the first `nodes` entry, else None).
  - `parse_manifest(text: str, *, source: str) -> BitConfig` — parses TOML, validates, fills defaults.
  - `merge_overrides(config: BitConfig, overrides: dict, *, source: str) -> BitConfig` — dotted-table dict (same shape as the TOML, e.g. `{"launch": {"setup_seconds": 5}, "defaults": {...}}`) merged over `config`; unknown keys raise `ManifestError` (overrides are hand-typed, so strictness beats a silent no-op); `extras`/`[defaults]` keys merge freely.

**Validation rules to implement** (each a `ManifestError` with the offending key):
`[bit].name` and `entry` required non-empty strings; `entry` must contain exactly one `":"`; `kind` must be in `KINDS`; `[start].when` in `{"immediate", "players", "operator"}` (`"scheduled"` rejected with message `"scheduled start conditions are reserved for a later slice"`); `on_timeout` in `{"start", "abort"}`; `min_scored >= 1`; `launch.transport` in `{"any", "o2lite"}`; `room_types` non-empty and `default_room_type` a member; numeric fields must be numbers (`bool` is not a number — TOML makes this unambiguous). `[rhythm]` present on a non-`r_game` kind (or `[ambient]` on non-`ambient`) logs a warning and is parsed anyway. Unknown top-level tables and unknown keys inside known tables log one warning naming source and key, and are ignored — except inside `[defaults]`, which is free-form and lands verbatim in `extras`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bit_config.py
import pytest

from control.bit_config import (
    BitConfig, ManifestError, StartCondition, merge_overrides, parse_manifest,
)

MINIMAL = """
[bit]
name = "TestBit"
entry = "test_bit:TestBit"
"""

FULL = """
[bit]
name = "MetronomeBit"
version = "1.0.0"
description = "Call-and-response rhythm game"
entry = "metronome_bit:MetronomeBit"
kind = "r_game"
author = "Musical Mycology"

[launch]
room_types = ["DEMO"]
default_room_type = "DEMO"
default_devices = 2
setup_seconds = 20
expected_run_seconds = 45
transport = "any"
default_join_role = "player"

[launch.nodes]
player = "METRO_PLAYER_NODE"

[start]
when = "players"
min_scored = 1
timeout_seconds = 120
on_timeout = "start"

[console]
display_name = "Metronome"
notes = "Players tap back the 4-beat call."

[results]
keys = ["phrases", "successes"]

[rhythm]
bpm = 100
beats_per_cycle = 8
cycles = 4
grading_window_ms = 50
input_offset_ms = 0

[defaults]
extra_knob = 7
"""


def test_minimal_manifest_fills_defaults():
    cfg = parse_manifest(MINIMAL, source="bits/test/bit.toml")
    assert cfg.identity.name == "TestBit"
    assert cfg.identity.kind == "game"
    assert cfg.launch.room_types == ("TEST",)
    assert cfg.start.when == "immediate"
    assert cfg.rhythm is None and cfg.ambient is None
    assert cfg.join_node() is None


def test_full_manifest_round_trip():
    cfg = parse_manifest(FULL, source="bits/metronome/bit.toml")
    assert cfg.identity.kind == "r_game"
    assert cfg.launch.default_devices == 2
    assert cfg.launch.expected_run_seconds == 45
    assert cfg.node_for("player") == "METRO_PLAYER_NODE"
    assert cfg.join_node() == "METRO_PLAYER_NODE"
    assert cfg.start == StartCondition(
        when="players", min_scored=1, timeout_seconds=120, on_timeout="start")
    assert cfg.rhythm.bpm == 100.0
    assert cfg.extras == {"extra_knob": 7}
    assert cfg.results_keys == ("phrases", "successes")


@pytest.mark.parametrize("mutation,key", [
    ("[bit]\nentry = 'a:B'", "bit.name"),
    ("[bit]\nname = 'X'", "bit.entry"),
    ("[bit]\nname='X'\nentry='a:B'\nkind='sport'", "bit.kind"),
    (MINIMAL + "[start]\nwhen = 'scheduled'", "start.when"),
    (MINIMAL + "[start]\nwhen='players'\nmin_scored = 0", "start.min_scored"),
    (MINIMAL + "[launch]\ntransport = 'udp'", "launch.transport"),
])
def test_bad_manifest_raises_located_error(mutation, key):
    with pytest.raises(ManifestError) as exc:
        parse_manifest(mutation, source="bits/x/bit.toml")
    assert exc.value.source == "bits/x/bit.toml"
    assert exc.value.key == key


def test_unknown_key_warns_not_fails(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        cfg = parse_manifest(MINIMAL + "[mystery]\nx = 1", source="s")
    assert cfg.identity.name == "TestBit"
    assert any("mystery" in r.message for r in caplog.records)


def test_merge_overrides_precedence_and_strictness():
    cfg = parse_manifest(FULL, source="s")
    merged = merge_overrides(
        cfg, {"launch": {"setup_seconds": 5}, "defaults": {"extra_knob": 9}},
        source="cli")
    assert merged.launch.setup_seconds == 5
    assert merged.extras == {"extra_knob": 9}
    assert cfg.launch.setup_seconds == 20  # original untouched (frozen)
    with pytest.raises(ManifestError):
        merge_overrides(cfg, {"launch": {"no_such_key": 1}}, source="cli")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_bit_config.py -v`
Expected: FAIL — `ModuleNotFoundError: control.bit_config`

- [ ] **Step 3: Implement `control/bit_config.py`**

Implement with `tomllib.loads`, a small `_get(table, key, type, default, *, source, prefix)` helper that raises `ManifestError(source=source, key=f"{prefix}.{key}", message=...)` on type mismatch, and one module logger for unknown-key warnings. `merge_overrides` walks the same known-table map used by the parser (`{"bit": BitIdentity fields, "launch": ..., "start": ..., "console": ..., "results": ..., "rhythm": ..., "ambient": ..., "defaults": free}`) and rebuilds frozen dataclasses via `dataclasses.replace`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_bit_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control/bit_config.py tests/test_bit_config.py
git commit -m "feat(bits): BitConfig manifest schema v1 -- parse, validate, merge"
```

---

### Task 2: `control/bit_registry.py` — discovery without importing Bit code

**Files:**
- Create: `control/bit_registry.py`
- Test: `tests/test_bit_registry.py` (fixture packages built in `tmp_path`)

**Interfaces:**
- Consumes: `parse_manifest`, `merge_overrides`, `BitConfig`, `ManifestError` from Task 1.
- Produces:
  - `@dataclass class BitPackage: name: str; config: BitConfig; path: Path; import_root: str` (`import_root` is the dotted package prefix, e.g. `"bits.metronome"`).
  - `@dataclass class PackageError: path: str; message: str`
  - `class BitRegistry:`
    - `.packages: dict[str, BitPackage]` (keyed by `config.identity.name`)
    - `.errors: list[PackageError]`
    - `@classmethod discover(cls, root: Path | None = None) -> "BitRegistry"` — default root is the repo's `bits/` directory (`Path(__file__).resolve().parent.parent / "bits"`); scans `root/*/bit.toml`; a `ManifestError`/`tomllib.TOMLDecodeError`/duplicate name becomes a `PackageError` entry, never an exception.
    - `.bit_class(name: str) -> type` — resolves `entry = "module:Class"` via `importlib.import_module(f"{import_root}.{module}")`, `getattr` the class. Raises `KeyError` for an unknown name, `ManifestError` if the import or attribute fails (message carries the underlying error).
    - `.resolve_config(name: str, overrides: dict | None = None) -> BitConfig` — the package config, with `merge_overrides` applied when overrides given.
    - `.list_view(*, include_hidden: bool = True) -> list[dict]` — JSON-safe wire shape per package: `{"name", "version", "kind", "description", "display_name", "hidden", "room_types": [...], "start": {"when", "min_scored", "timeout_seconds", "on_timeout"}, "notes"}`, sorted by name, plus nothing else; `include_hidden=False` filters `console.hidden` packages. Also `.errors_view() -> list[dict]` (`{"path", "message"}`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bit_registry.py
from pathlib import Path

import pytest

from control.bit_registry import BitRegistry


def make_pkg(root: Path, dirname: str, manifest: str, module: str = ""):
    d = root / dirname
    d.mkdir(parents=True)
    (d / "bit.toml").write_text(manifest)
    (d / "__init__.py").write_text("")
    if module:
        (d / "fake_bit.py").write_text(module)
    return d


GOOD = """
[bit]
name = "GoodBit"
entry = "fake_bit:GoodBit"
[console]
hidden = false
"""

HIDDEN = GOOD.replace("GoodBit", "HiddenBit").replace(
    "hidden = false", "hidden = true")

MODULE = "class GoodBit:\n    pass\n"


def test_discover_scans_and_isolates_broken_manifests(tmp_path):
    make_pkg(tmp_path, "good", GOOD, MODULE)
    make_pkg(tmp_path, "broken", "[bit]\nkind = 'sport'")
    make_pkg(tmp_path, "notoml", "this is { not toml")
    reg = BitRegistry.discover(tmp_path)
    assert set(reg.packages) == {"GoodBit"}
    assert len(reg.errors) == 2
    assert all("broken" in e.path or "notoml" in e.path for e in reg.errors)


def test_duplicate_name_is_an_error_not_a_clobber(tmp_path):
    make_pkg(tmp_path, "a", GOOD, MODULE)
    make_pkg(tmp_path, "b", GOOD, MODULE)
    reg = BitRegistry.discover(tmp_path)
    assert len(reg.packages) == 1
    assert any("duplicate" in e.message for e in reg.errors)


def test_discovery_never_imports_bit_code(tmp_path):
    make_pkg(tmp_path, "boom", GOOD.replace("GoodBit", "BoomBit"),
             "raise RuntimeError('imported at discovery')\n")
    reg = BitRegistry.discover(tmp_path)  # must not raise
    assert "BoomBit" in reg.packages


def test_list_view_shape_and_hidden_filter(tmp_path):
    make_pkg(tmp_path, "good", GOOD, MODULE)
    make_pkg(tmp_path, "hid", HIDDEN, MODULE)
    reg = BitRegistry.discover(tmp_path)
    names = [row["name"] for row in reg.list_view()]
    assert names == ["GoodBit", "HiddenBit"]
    visible = [row["name"] for row in reg.list_view(include_hidden=False)]
    assert visible == ["GoodBit"]
    row = reg.list_view()[0]
    assert row["start"]["when"] == "immediate"
    assert row["room_types"] == ["TEST"]


def test_real_bits_tree_discovers_cleanly():
    reg = BitRegistry.discover()
    assert reg.errors == []
    assert {"TestBit", "MetronomeBit", "CaptureBit"} <= set(reg.packages)
```

(The last test will only pass after Tasks 4–6 migrate the real Bits; mark it `@pytest.mark.xfail(strict=False, reason="until bits are migrated")` in this task and remove the marker in Task 6.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_bit_registry.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `control/bit_registry.py`**

For `tmp_path` roots (outside the repo), `bit_class` computes the import by inserting `str(pkg.path.parent)` on `sys.path` and importing `f"{pkg.path.name}.{module}"`; for the default root, `import_root = f"bits.{pkg.path.name}"`. Keep that branch in one `_import_module(pkg, module_name)` helper.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_bit_registry.py -v`
Expected: PASS (final test xfail)

- [ ] **Step 5: Commit**

```bash
git add control/bit_registry.py tests/test_bit_registry.py
git commit -m "feat(bits): BitRegistry discovers bits/*/bit.toml without importing Bit code"
```

---

### Task 3: `Bit(config)` and `GameServer.load_bit(name, config=None)`

**Files:**
- Modify: `control/bit.py` (add `__init__`), `control/engine.py:103-122` (`load_bit`)
- Test: `tests/test_engine_bit_config.py`

**Interfaces:**
- Consumes: `BitConfig` (Task 1).
- Produces:
  - `Bit.__init__(self, config: "BitConfig | None" = None)` storing `self.config = config`. Import `BitConfig` under `typing.TYPE_CHECKING` only (keeps `control/bit.py`'s import surface flat); the default `None` keeps every existing hand-constructed test Bit working.
  - `GameServer.load_bit(self, name: str, config=None) -> None` — inside the existing try block, `bit = bit_cls(config) if config is not None else bit_cls()`. `config` is opaque to the engine.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_engine_bit_config.py
from control.bit import Bit
from control.bit_config import parse_manifest
from control.engine import GameServer
from control.roles import RoleTable


class ConfigBit(Bit):
    @property
    def role_table(self):
        return RoleTable(roles={}, node_fallbacks={})


def test_bit_stores_config_and_defaults_to_none():
    assert ConfigBit().config is None
    cfg = parse_manifest("[bit]\nname='C'\nentry='m:C'", source="s")
    assert ConfigBit(cfg).config is cfg


def test_load_bit_passes_config_through():
    cfg = parse_manifest("[bit]\nname='C'\nentry='m:C'", source="s")
    gs = GameServer({"ConfigBit": ConfigBit})
    gs.load_bit("ConfigBit", config=cfg)
    assert gs.bit.config is cfg


def test_load_bit_without_config_calls_zero_arg():
    gs = GameServer({"ConfigBit": ConfigBit})
    gs.load_bit("ConfigBit")
    assert gs.bit.config is None
```

(Adjust the `RoleTable` construction to match its real signature in `control/roles.py` — copy whatever `tests/test_engine.py`'s minimal Bit fixture does.)

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_engine_bit_config.py -v` → FAIL (`unexpected keyword argument 'config'`)

- [ ] **Step 3: Implement** the two modifications.

- [ ] **Step 4: Run the full suite** — `.venv/bin/python -m pytest tests -q` → all pass (this change must break nothing).

- [ ] **Step 5: Commit**

```bash
git add control/bit.py control/engine.py tests/test_engine_bit_config.py
git commit -m "feat(engine): Bit stores an opaque BitConfig; load_bit passes it through"
```

---

### Task 4: Migrate TestBit to `bits/test/`

**Files:**
- Create: `bits/test/__init__.py`, `bits/test/bit.toml`
- Move: `bits/test_bit.py` → `bits/test/test_bit.py` (`git mv`)
- Modify: every importer of `bits.test_bit` (grep: `harness/terrarium_boot.py`, `harness/devicelink_smoke.py`, `harness/led_smoke.py`, `harness/capture_smoke.py` if present, and ~all tests importing it), plus `bits/test/test_bit.py` itself.
- Test: existing suite pins behavior; add `tests/test_bit_packages.py`.

**Interfaces:**
- Produces: `bits/test/bit.toml`:

```toml
[bit]
name = "TestBit"
version = "1.0.0"
description = "Durable reference/regression fixture: scored player + jam jammer"
entry = "test_bit:TestBit"
kind = "tool"

[launch]
room_types = ["TEST", "DEMO"]
default_room_type = "TEST"
default_devices = 1
default_join_role = "player"

[launch.nodes]
player = "TEST_PLAYER_NODE"
jammer = "TEST_JAM_NODE"

[start]
when = "immediate"

[console]
display_name = "Test Bit"
hidden = true

[defaults]
run_duration_seconds = 2.0
```

(Check the jam node's real constant name in `bits/test_bit.py` before writing; use exactly the constants the code declares.)

- `TestBit.__init__(self, config=None, run_duration: float | None = None)`: calls `super().__init__(config)`; resolves duration as `run_duration if run_duration is not None else (config.extras.get("run_duration_seconds") if config else None) or RUN_DURATION_SECONDS`. The explicit kwarg stays so existing tests and `devicelink_smoke` keep working.

- [ ] **Step 1: Write the failing package test**

```python
# tests/test_bit_packages.py
from control.bit_config import parse_manifest
from control.bit_registry import BitRegistry


def test_testbit_package_resolves_and_constructs():
    reg = BitRegistry.discover()
    assert "TestBit" in reg.packages, reg.errors
    cls = reg.bit_class("TestBit")
    cfg = reg.resolve_config(
        "TestBit", {"defaults": {"run_duration_seconds": 0.5}})
    bit = cls(cfg)
    assert bit.run_duration == 0.5
    assert cfg.node_for("player") == "TEST_PLAYER_NODE"
```

(Confirm the attribute name TestBit stores its duration under — grep `run_duration` in the moved file — and assert on the real one.)

- [ ] **Step 2: Run to verify failure** — package doesn't exist yet.

- [ ] **Step 3: Migrate** — `git mv bits/test_bit.py bits/test/test_bit.py`, add `__init__.py` + `bit.toml`, update `TestBit.__init__`, then fix all importers: `grep -rln "bits.test_bit" | xargs sed -i '' 's/bits\.test_bit/bits.test.test_bit/g'` and eyeball the diff.

- [ ] **Step 4: Run the full suite** — `.venv/bin/python -m pytest tests -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add -A bits/test tests/test_bit_packages.py
git commit -m "refactor(bits): TestBit becomes the bits/test/ package with bit.toml"
```

---

### Task 5: Migrate MetronomeBit to `bits/metronome/` with `[rhythm]`

**Files:**
- Create: `bits/metronome/__init__.py`, `bits/metronome/bit.toml`
- Move: `bits/metronome_bit.py` → `bits/metronome/metronome_bit.py`
- Modify: importers (`harness/terrarium_boot.py`, metronome tests), and `MetronomeBit.__init__`.
- Test: extend `tests/test_bit_packages.py`; existing 4 metronome test files pin behavior.

**Interfaces:**
- Produces: `bits/metronome/bit.toml` — the FULL manifest from Task 1's test (name `MetronomeBit`, kind `r_game`, DEMO-only, `default_devices = 2`, `expected_run_seconds = 45`, node map `player = "METRO_PLAYER_NODE"`, `[rhythm] bpm=100, beats_per_cycle=8, cycles=4, grading_window_ms=50, input_offset_ms=0`, `[results] keys = ["phrases", "successes"]`, `[start] when="players" min_scored=1 timeout_seconds=120 on_timeout="start"`, console notes as in the spec).
- `MetronomeBit.__init__(self, config=None)`: `super().__init__(config)`; when `config and config.rhythm`, set instance attributes `self.BEAT_S = 60.0 / r.bpm`, `self.BEATS_PER_CYCLE = r.beats_per_cycle`, `self.CYCLES = r.cycles`, `self.WINDOW_S = r.grading_window_ms / 1000.0`, `self.INPUT_OFFSET_S = r.input_offset_ms / 1000.0` (map onto the exact class-constant names in the file — check `WINDOW`'s real name first, `grep -n "0.05\|WINDOW" bits/metronome/metronome_bit.py`). Class constants stay as defaults so a zero-arg construction is byte-identical.

- [ ] **Step 1: Write the failing test**

```python
def test_metronome_package_rhythm_block_reaches_instance():
    reg = BitRegistry.discover()
    cls = reg.bit_class("MetronomeBit")
    fast = cls(reg.resolve_config("MetronomeBit", {"rhythm": {"bpm": 120}}))
    assert abs(fast.BEAT_S - 0.5) < 1e-9
    default = cls()
    assert abs(default.BEAT_S - 0.6) < 1e-9
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Migrate** (same mechanics as Task 4).
- [ ] **Step 4: Full suite green** — `.venv/bin/python -m pytest tests -q`
- [ ] **Step 5: Commit** — `git commit -m "refactor(bits): MetronomeBit package; rhythm knobs move to [rhythm] manifest block"`

---

### Task 6: Migrate CaptureBit to `bits/capture/`

**Files:**
- Create: `bits/capture/__init__.py`, `bits/capture/bit.toml`
- Move: `bits/capture_bit.py` → `bits/capture/capture_bit.py`
- Modify: importers (`harness/capture_smoke.py`, capture tests); remove the Task 2 xfail marker; delete the now-empty top-level `bits/__init__.py` only if nothing imports bare `bits` (check first; otherwise leave it).
- Test: `tests/test_bit_packages.py` extension.

**Interfaces:**
- Produces: `bits/capture/bit.toml` — kind `tool`, `entry = "capture_bit:CaptureBit"`, `room_types = ["TEST"]`, `[launch.nodes] recorder = "CAPTURE_NODE"`, `default_join_role = "recorder"`, `[start] when = "operator"` (a capture session is operator-driven by design), console notes pointing at the live capture dashboard behavior.
- **CaptureBit's constructor requires a `CaptureStore`** (`bits/capture_bit.py:29`), so it cannot be built from a bare manifest. Give it `__init__(self, store=None, config=None, ...)` keeping current kwargs, and where `store is None` construct the default `CaptureStore` exactly the way `harness/capture_smoke.py` does today (copy that construction into the Bit as the default; `capture_smoke` keeps passing its own). This is what makes CaptureBit launchable from `run_stack` for free.

- [ ] **Step 1: Failing test** — `reg.bit_class("CaptureBit")(reg.resolve_config("CaptureBit"))` constructs; `status()` returns a dict.
- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Migrate;** un-xfail `test_real_bits_tree_discovers_cleanly`.
- [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `git commit -m "refactor(bits): CaptureBit package; default store makes it launchable"`

---

### Task 7: `control/start_condition.py` — pure start-decision evaluator

**Files:**
- Create: `control/start_condition.py`
- Test: `tests/test_start_condition.py`

**Interfaces:**
- Consumes: `StartCondition` (Task 1).
- Produces:
  - `scored_count(gs) -> int` — sums `count` over `gs.registration.counts()` entries whose role is scored (resolve scored-ness off `gs.bit.role_table`; copy the role lookup idiom from `control/registration.py`). Returns 0 when registration is None.
  - `start_decision(cond: StartCondition, *, scored: int, elapsed: float, setup_seconds: float) -> str | None` — pure. Returns `None` (keep holding) or one of `"start"` / `"abort"`:
    - `immediate`: `"start"` once `elapsed >= setup_seconds` (today's hold semantics, unchanged).
    - `operator`: always `None` (the state-change watch in the harness is what ends the hold; timeoutless by design).
    - `players`: `"start"` once `scored >= cond.min_scored`; else when `cond.timeout_seconds` is set and `elapsed >= timeout_seconds`, return `"start"` or `"abort"` per `cond.on_timeout`; else `None`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_start_condition.py
from control.bit_config import StartCondition
from control.start_condition import start_decision


def test_immediate_matches_todays_hold():
    c = StartCondition(when="immediate")
    assert start_decision(c, scored=0, elapsed=1.0, setup_seconds=5.0) is None
    assert start_decision(c, scored=0, elapsed=5.0, setup_seconds=5.0) == "start"


def test_players_threshold_then_timeout_start_and_abort():
    c = StartCondition(when="players", min_scored=2, timeout_seconds=10,
                       on_timeout="start")
    assert start_decision(c, scored=1, elapsed=9, setup_seconds=0) is None
    assert start_decision(c, scored=2, elapsed=1, setup_seconds=0) == "start"
    assert start_decision(c, scored=1, elapsed=10, setup_seconds=0) == "start"
    a = StartCondition(when="players", min_scored=2, timeout_seconds=10,
                       on_timeout="abort")
    assert start_decision(a, scored=0, elapsed=10, setup_seconds=0) == "abort"


def test_operator_never_self_starts():
    c = StartCondition(when="operator")
    assert start_decision(c, scored=9, elapsed=999, setup_seconds=0) is None
```

- [ ] **Step 2: Verify failure.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(control): pure start-condition evaluator (immediate/players/operator)"`

---

### Task 8: `terrarium_boot` goes discovery-driven; start conditions wired into the hold

**Files:**
- Modify: `harness/terrarium_boot.py` — delete the hardcoded imports of both Bit classes, `_timed_test_bit_cls` (`:358-377`), the `choices=` list (`:590-592`), and the literal registry dict (`:666-667`).
- Test: `tests/test_terrarium_boot.py` additions (follow its existing CLI-test style).

**Interfaces:**
- Consumes: `BitRegistry.discover()`, `resolve_config`, `bit_class`, `start_decision`, `scored_count`.
- Produces (later tasks rely on):
  - `--bit NAME` — any discovered name; unknown name exits with the discovered list and any `PackageError`s.
  - `--list-bits` — prints one line per package (`name  version  kind  rooms  start  description`, hidden included, errors appended) and exits 0.
  - Defaults resolution in `main()`: `--setup-seconds`/`--room-type`/`--run-seconds` argparse defaults become `None`; after parsing, `cfg = registry.resolve_config(args.bit, overrides)` where `overrides` collects only explicitly-given CLI values (`{"launch": {"setup_seconds": args.setup_seconds}}` etc., plus `{"defaults": {"run_duration_seconds": args.run_seconds}}` when given). Effective room type = `args.room_type or cfg.launch.default_room_type`; effective setup hold = `cfg.launch.setup_seconds`.
  - `GameServer` registry: `build(...)` receives `{name: registry.bit_class(name)}` for the selected Bit and `main()` calls `gs.load_bit(args.bit, config=cfg)` — via whatever call path `build()` currently uses for `load_bit` (thread `config` through it).
  - `_wait_in_setup` gains `condition: StartCondition` and `game_server`; its loop consults `start_decision(condition, scored=scored_count(gs), elapsed=..., setup_seconds=...)` each tick (keeping the existing pty-drain, parent-gone, and state-change checks byte-identical) and returns the existing reason strings plus `"players-met"`, `"timeout-start"`, `"timeout-abort"`. `main()` treats `"timeout-abort"` as `gs.abort()` + clean exit; `"players-met"`/`"timeout-start"`/`"expired"` all proceed to `gs.run()` when still in SETUP.

- [ ] **Step 1: Failing tests** — extend `tests/test_terrarium_boot.py`: (a) `--list-bits` output contains all three names (run `main()` with patched `sys.argv` and capsys, or the file's existing CLI harness); (b) `--bit NoSuchBit` exits non-zero naming the available list; (c) `_wait_in_setup` with a `players` condition returns `"players-met"` when a fake gs's scored count crosses the threshold (drive with the same fake-gs pattern the file's existing hold tests use).
- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement.** Keep `--run-seconds`/`--hold` semantics by mapping them into the overrides dict; TestBit reads them from `extras` now (Task 4).
- [ ] **Step 4: Full suite green** — the deleted `_TimedTestBit` had tests; update them to assert the new resolution path instead.
- [ ] **Step 5: Commit** — `git commit -m "feat(harness): terrarium_boot discovers Bits, resolves manifest defaults, honors start conditions"`

---

### Task 9: `run_stack` — manifest defaults, `--list-bits`, derived CI bound

**Files:**
- Modify: `harness/run_stack.py` — delete `NODE_FOR_BIT` (`:70-71`) and the `choices=` list (`:459-461`).
- Test: `tests/test_run_stack*.py` additions in the existing style.

**Interfaces:**
- Consumes: `BitRegistry` (Task 2).
- Produces:
  - `Config.node` default `None` → resolved as `args.node or cfg.join_node()`; a Bit with no nodes and no `--node` is a config error before anything spawns.
  - `Config.devices` default becomes `None` → `args.devices if args.devices is not None else cfg.launch.default_devices`.
  - `--room-type` default `None` → `cfg.launch.default_room_type`.
  - `--ci` with no `--seconds`: `seconds = cfg.launch.setup_seconds + (cfg.launch.expected_run_seconds or 45.0) + 15.0` (15 s grace covers teardown + closing fades; document inline).
  - `--list-bits` mirrors Task 8's output and exits.
  - `--setup-seconds` passthrough default `None` → manifest value (it forwards to `terrarium_boot`, which resolves identically; forward only when explicitly given to avoid double-defaulting).
- [ ] **Step 1: Failing tests** — (a) node derivation: `Config` built from `--bit MetronomeBit` with no `--node` resolves `METRO_PLAYER_NODE` from the manifest (patch `BitRegistry.discover` where the tests need isolation); (b) CI bound derivation from `expected_run_seconds`; (c) `--devices` default from manifest (MetronomeBit → 2).
- [ ] **Step 2: Verify failure.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `git commit -m "feat(harness): run_stack derives node/devices/room/CI bound from bit.toml"`

---

### Task 10: Run profiles — `--profile venue.toml`

**Files:**
- Create: `control/run_profile.py`, `profiles/dev-metronome.toml` (worked example)
- Modify: `harness/run_stack.py` (flag + merge), `harness/terrarium_boot.py` (flag + merge)
- Test: `tests/test_run_profile.py`

**Interfaces:**
- Consumes: `merge_overrides`, `ManifestError` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class RunProfile: bit: str | None; room_type: str | None; devices: int | None; console_port: int | None; seconds: float | None; overrides: dict` (the `[bit.overrides]` table verbatim).
  - `parse_profile(text: str, *, source: str) -> RunProfile` — `[run]` table for the five launcher fields (unknown keys warn), `[bit.overrides]` passed through untouched (validated later by `merge_overrides` against the selected Bit).
  - Precedence, implemented in one place per launcher: manifest < profile < explicit CLI. Concretely: `bit = args.bit or profile.bit or "TestBit"`; each launcher field uses CLI-if-given, else profile-if-given, else manifest/argparse default; `overrides = profile.overrides` deep-merged under the CLI overrides dict from Tasks 8–9.
  - `profiles/dev-metronome.toml`:

```toml
[run]
bit = "MetronomeBit"
room_type = "DEMO"
devices = 2

[bit.overrides.rhythm]
bpm = 80

[bit.overrides.start]
when = "players"
min_scored = 2
timeout_seconds = 60
on_timeout = "start"
```

- [ ] **Step 1: Failing tests** — parse the example profile; precedence test: profile sets devices=2, CLI `--devices 1` wins; profile `bpm=80` reaches the resolved `BitConfig` when no CLI rhythm override exists.
- [ ] **Step 2: Verify failure.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Full suite green.**
- [ ] **Step 5: Commit** — `git commit -m "feat(harness): --profile venue TOML with manifest<profile<CLI precedence"`

---

### Task 11: Protocol + Console/Uplink — `list_bits`, `load_bit` overrides, stamped `bit_completed`

**Files:**
- Modify: `uplink/protocol.py`, `console/agent.py`, `uplink/agent module` (locate: `grep -rn "class UplinkAgent" uplink/`), `harness/terrarium_boot.py` (hand the registry to the agents it constructs)
- Test: `tests/test_console_protocol.py`, `tests/test_console_agent.py`, `tests/test_uplink*.py` additions

**Interfaces:**
- Consumes: `BitRegistry.list_view()/errors_view()/resolve_config()`.
- Produces (`uplink/protocol.py`):
  - `LoadBitCommand(name: str, overrides: dict | None = None)`; `parse_command` accepts an optional dict `overrides` (`ValueError` if present and not a dict).
  - `ListBitsCommand` (dataclass, no fields); `parse_command` maps `"list_bits"`.
  - `bits_listed_event(bits: list[dict], errors: list[dict]) -> dict` → `{"event": "bits_listed", "bits": [...], "errors": [...]}`.
  - `bit_completed_event(result: dict, bit_name: str = "", bit_version: str = "") -> dict` → adds `"bit": {"name": ..., "version": ...}` to the existing shape (defaulted params keep old call sites compiling; update both agents to pass them).
- Both agents: constructor gains `registry: BitRegistry | None = None`. On `ListBitsCommand`: broadcast/send `bits_listed_event(registry.list_view(), registry.errors_view())` (Console panel listing may later filter hidden client-side; the wire carries everything — both surfaces are operator surfaces). On `LoadBitCommand`: `cfg = registry.resolve_config(cmd.name, cmd.overrides)` then `gs.load_bit(cmd.name, config=cfg)`; a `ManifestError`/`KeyError` becomes the existing `error` event, never a raise (matching the agents' current engine-error handling). With `registry=None` (legacy tests), behavior is exactly today's (`gs.load_bit(name)`, `list_bits` answered with an error event `"no registry"`).
- `bit_completed`: both agents pass `gs.bit_name or ""` and `gs.bit.version` when broadcasting (`console/agent.py:265-275` and the uplink's equivalent).

- [ ] **Step 1: Failing tests** — (a) `parse_command({"command": "list_bits"})` returns `ListBitsCommand`; (b) `parse_command({"command": "load_bit", "name": "X", "overrides": {"launch": {"setup_seconds": 1}}})` carries the dict; (c) ConsoleAgent with a stub registry answers `list_bits` with the `bits_listed` shape and loads with merged config (assert the constructed Bit's `config.launch.setup_seconds == 1` via a recording fake registry); (d) `bit_completed` event carries `bit.name`/`bit.version`; (e) bad overrides produce an `error` event, state stays IDLE.
- [ ] **Step 2: Verify failure.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Full suite green** (existing bit_completed tests updated for the new key).
- [ ] **Step 5: Commit** — `git commit -m "feat(protocol): list_bits, load_bit overrides, bit-stamped completion for console+uplink"`

---

### Task 12: Console panel — Bits list rendered

**Files:**
- Modify: `console/static/console.js` (dispatch + a `renderBits` entry), `console/static/index.html` (a Bits panel section), `console/agent.py` (send `bits_listed` in the connect-time snapshot path so the panel needs no request round-trip)
- Test: `tests/js/` — new `bits_panel_behavior.test.js` in the existing Node `vm` style, wrapped by a `tests/test_bits_panel_behavior.py`; extend `tests/js/console_script_isolation.test.js` expectations if a new script file is added (prefer extending `console.js` to avoid a new global surface).

**Interfaces:**
- Consumes: `bits_listed` event shape from Task 11.
- Produces: a Bits panel listing name, version, kind, rooms, start condition, description, with hidden Bits visually muted (not omitted — operator surface), disabled packages shown with their error, and a Load button per row issuing `{"command": "load_bit", "name": ...}` over the existing socket. Re-render only on `bits_listed` (which fires on connect and never per-frame), honoring the no-rebuild-per-event discipline the Room and Trigger panels earned.

- [ ] **Step 1: Failing JS behavior test** — feed a `bits_listed` event through `handle()`; assert one card per bit, the hidden one carries the muted class, the error row renders, and a second unrelated event (`state_changed`) does not rebuild the list (compare child node identity, same idiom as `trigger_panel_behavior.test.js`).
- [ ] **Step 2: Verify failure** (`node --test tests/js/` via the Python wrapper).
- [ ] **Step 3: Implement.** — [ ] **Step 4: Full suite green including JS wrappers.**
- [ ] **Step 5: Commit** — `git commit -m "feat(console): Bits panel lists discovered packages with Load"`

---

### Task 13: Docs + live verification

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (new Landed subsystems section; retire the hardcoded-map descriptions), spec Status line, `README.md` if it names `--bit` choices.

**Steps:**
- [ ] **Step 1: Live verify per spec §10** (needs Arco checkout on this box; all **RUN ON: MYCOLOGICAL**):

```bash
.venv/bin/python -m harness.run_stack --list-bits
```

```bash
.venv/bin/python -m harness.run_stack --ci --bit MetronomeBit --devices 2
```

(no `--room-type`, no `--node` — both must come from the manifest), and one `players` start-condition run:

```bash
.venv/bin/python -m harness.run_stack --profile profiles/dev-metronome.toml --seconds 90
```

confirming the run starts on the second join, not on a timer. Open the Console and confirm the Bits panel lists and loads.
- [ ] **Step 2: Record outcomes** in the spec's Status line (what was verified live vs offline-only, per house style).
- [ ] **Step 3: Update the deep-dive** (mm-deepdive-sync at closeout).
- [ ] **Step 4: Commit** — `git commit -m "docs(terrarium): record Bit packaging, manifests, start conditions, and launch changes"`

---

## Self-review notes

- Spec coverage: §2 → Tasks 2, 4–6; §3 schema → Task 1; §4 BitConfig/constructor → Tasks 1, 3–6; §5 start conditions → Tasks 7–8; §6 launch scripts → Tasks 8–10; §7 console/uplink → Tasks 11–12; §10 live verify → Task 13.
- The engine's only change (Task 3) is the opaque `config` param — boundary rule "engine stays Bit-agnostic" holds.
- Type consistency: `resolve_config(name, overrides)` and `bit_class(name)` are used identically in Tasks 4–6, 8–9, 11; `start_decision(cond, scored=, elapsed=, setup_seconds=)` identical in Tasks 7–8; `bits_listed_event(bits, errors)` identical in Tasks 11–12.
- Known judgment calls surfaced to the reviewer: CaptureBit default-store construction (Task 6) and the 15 s CI grace constant (Task 9) are decisions an implementer should not silently change.
