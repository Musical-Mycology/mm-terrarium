# Instrument Catalog + Design Panel v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move instruments into a file-based draft/published catalog and ship
the first Design panel (console left-nav view): list, raw-TOML edit, clone,
publish.

**Architecture:** Instruments become per-file TOML entries under
`instruments/` (published) and `instruments/drafts/` (drafts); the name is
the file stem. `load_terrarium_config` loads the catalog and merges it into
the existing fixture-resolution path. A new `control/catalog.py` owns
load/save/clone/publish. The console gains admin commands + a Design view.
Structured form editors, the simulator embed, and the Calibrate flow are
**Plan 2** (see spec sections 5-6), planned after this lands.

**Tech Stack:** Python 3 stdlib (`tomllib`, no TOML writer needed: drafts
are stored as raw TOML text and publish is a validated file move), vanilla
JS console modules, pytest + node JS tests.

**Spec:** `docs/superpowers/specs/2026-08-31-design-panel-and-instrument-catalog-design.md`

## Global Constraints

- Run everything through the project venv: `.venv/bin/python -m pytest tests -q`.
  A fresh worktree has no `.venv`: `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` first.
- Full-suite baseline before this plan: record it in the first commit message
  (deep-dive last records 1634 passed, 1 skipped, plus later slices).
- `control/` never imports luxaeterna or pyarco. `console/protocol.py` never
  imports the engine.
- Fails-hard at load applies to **published** entries only; drafts collect
  errors instead of raising.
- Only published entries are room-loadable or wire-visible outside the
  Design view. The uplink never carries design commands/events.
- Located errors everywhere: reuse `TerrariumConfigError(source=, key=, message=)`
  conventions.
- Never use bare `git stash`. Commit per task.
- No em dashes in any authored prose/docstrings.

---

### Task 1: Catalog load-side (`control/catalog.py`)

**Files:**
- Create: `control/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `control.terrarium_config._parse_instrument(iname, iraw, *, source)`,
  `control.terrarium_config.TerrariumConfigError`.
- Produces:
  - `@dataclass(frozen=True) CatalogEntry: name: str; state: str  # "published"|"draft"; path: Path; instrument: Instrument | None; error: str | None`
  - `@dataclass(frozen=True) InstrumentCatalog: root: Path; entries: dict[str, CatalogEntry]` with property
    `published -> dict[str, Instrument]` (only entries with state=="published").
  - `load_catalog(root: Path) -> InstrumentCatalog`
  - `CATALOG_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")`

Layout contract (locked here for all later tasks): `root/*.toml` are
published, `root/drafts/*.toml` are drafts, name = file stem. A file whose
stem fails `CATALOG_NAME_RE` is a hard `TerrariumConfigError` in both dirs
(names become wire fields and path components). A published file that fails
to parse raises `TerrariumConfigError`; a draft that fails to parse yields
`CatalogEntry(state="draft", instrument=None, error=str(exc))`. A draft
whose stem collides with a published name is legal (it is the pending edit
of that entry); two files may not collide within one dir (filesystem
guarantees that). A missing `root` yields an empty catalog (not an error):
a repo without an `instruments/` dir keeps booting.

The per-file TOML shape is the **body** of today's `[instruments.<name>]`
table promoted to top level (description, capabilities, accepted_cues,
`[[functions]]`, `[ambient.light]`, `[ambient.ugen]`), i.e. parse with
`_parse_instrument(stem, tomllib.loads(text), source=str(path))`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_catalog.py
"""Catalog load-side: instruments/*.toml published, instruments/drafts/*.toml drafts."""
from pathlib import Path

import pytest

from control.catalog import load_catalog
from control.terrarium_config import TerrariumConfigError

GOOD = '''
description = "a test instrument"
capabilities = ["light.pixels"]
accepted_cues = ["midi"]
'''


def make_catalog(tmp_path: Path) -> Path:
    root = tmp_path / "instruments"
    (root / "drafts").mkdir(parents=True)
    return root


def test_missing_root_is_an_empty_catalog(tmp_path):
    cat = load_catalog(tmp_path / "nope")
    assert cat.entries == {}
    assert cat.published == {}


def test_published_entry_parses_to_an_instrument(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    cat = load_catalog(root)
    entry = cat.entries["glowcap"]
    assert entry.state == "published"
    assert entry.instrument.name == "glowcap"
    assert entry.error is None
    assert "glowcap" in cat.published


def test_published_parse_failure_raises_located(tmp_path):
    root = make_catalog(tmp_path)
    (root / "bad.toml").write_text('capabilities = ["no.such.capability"]')
    with pytest.raises(TerrariumConfigError) as exc:
        load_catalog(root)
    assert "bad.toml" in str(exc.value)


def test_draft_parse_failure_is_collected_not_raised(tmp_path):
    root = make_catalog(tmp_path)
    (root / "drafts" / "wip.toml").write_text('capabilities = ["no.such.capability"]')
    cat = load_catalog(root)
    entry = cat.entries["wip"]
    assert entry.state == "draft"
    assert entry.instrument is None
    assert "no.such.capability" in entry.error
    assert cat.published == {}


def test_draft_shadowing_published_keeps_both_reachable(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    (root / "drafts" / "glowcap.toml").write_text(GOOD)
    cat = load_catalog(root)
    # entries key is "<state>:<name>" precisely so a draft edit of a
    # published entry does not hide it.
    assert cat.entries["published:glowcap"].state == "published"
    assert cat.entries["draft:glowcap"].state == "draft"


def test_bad_stem_is_refused_even_as_draft(tmp_path):
    root = make_catalog(tmp_path)
    (root / "drafts" / "we ird.toml").write_text(GOOD)
    with pytest.raises(TerrariumConfigError):
        load_catalog(root)
```

Note the shadowing test overrides the simple `entries: dict[str, ...]`
sketch: key entries by `f"{state}:{name}"`. Update the dataclass docstring
accordingly and add a helper `get(state, name) -> CatalogEntry | None`.

- [ ] **Step 2: Run tests, verify failure**

Run: `.venv/bin/python -m pytest tests/test_catalog.py -q`
Expected: FAIL, `ModuleNotFoundError: control.catalog`

- [ ] **Step 3: Implement `control/catalog.py`**

```python
"""File-based instrument catalog: instruments/*.toml are published entries,
instruments/drafts/*.toml are drafts. Name = file stem. Published entries
fail hard; drafts collect their error. Pure stdlib (control/ discipline).

Spec: docs/superpowers/specs/2026-08-31-design-panel-and-instrument-catalog-design.md.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from control.instrument import Instrument
from control.terrarium_config import TerrariumConfigError, _parse_instrument

CATALOG_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    state: str                     # "published" | "draft"
    path: Path
    instrument: Instrument | None  # None when a draft failed to parse
    error: str | None


@dataclass(frozen=True)
class InstrumentCatalog:
    root: Path
    entries: dict[str, CatalogEntry]  # keyed "<state>:<name>"

    def get(self, state: str, name: str) -> CatalogEntry | None:
        return self.entries.get(f"{state}:{name}")

    @property
    def published(self) -> dict[str, Instrument]:
        return {e.name: e.instrument for e in self.entries.values()
                if e.state == "published"}


def _check_stem(path: Path) -> str:
    if not CATALOG_NAME_RE.match(path.stem):
        raise TerrariumConfigError(
            source=str(path), key="-",
            message="instrument file name must match [A-Za-z0-9_-]+")
    return path.stem


def _parse_entry(path: Path, state: str) -> CatalogEntry:
    name = _check_stem(path)
    text = path.read_text(encoding="utf-8")
    try:
        raw = tomllib.loads(text)
        instrument = _parse_instrument(name, raw, source=str(path))
    except (tomllib.TOMLDecodeError, TerrariumConfigError) as exc:
        if state == "published":
            if isinstance(exc, TerrariumConfigError):
                raise
            raise TerrariumConfigError(
                source=str(path), key="-",
                message=f"not valid TOML: {exc}") from exc
        return CatalogEntry(name=name, state=state, path=path,
                            instrument=None, error=str(exc))
    return CatalogEntry(name=name, state=state, path=path,
                        instrument=instrument, error=None)


def load_catalog(root: Path) -> InstrumentCatalog:
    root = Path(root)
    entries: dict[str, CatalogEntry] = {}
    if root.is_dir():
        for path in sorted(root.glob("*.toml")):
            entry = _parse_entry(path, "published")
            entries[f"published:{entry.name}"] = entry
        drafts = root / "drafts"
        if drafts.is_dir():
            for path in sorted(drafts.glob("*.toml")):
                entry = _parse_entry(path, "draft")
                entries[f"draft:{entry.name}"] = entry
    return InstrumentCatalog(root=root, entries=entries)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_catalog.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add control/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): file-based instrument catalog, load side"
```

---

### Task 2: Config-side trigger parsing (`[[event_triggers]]` / `[[stream_triggers]]`)

TUNESHROOM carries event triggers; a catalog file cannot express it until
`_parse_instrument` parses triggers. Today it does not (it stops at
functions + ambient).

**Files:**
- Modify: `control/terrarium_config.py` (`_parse_instrument`, add
  `_parse_event_triggers` / `_parse_stream_triggers` next to
  `_parse_functions`)
- Test: `tests/test_terrarium_config.py` (append; follow the file's
  existing test style)

**Interfaces:**
- Consumes: `control.triggers.EventTrigger(name, description, thresholds)`,
  `StreamTrigger(name, description, verb, arg, transform, params)`;
  validation already runs via `validate_instrument` inside
  `_parse_instrument` (it validates `event_triggers`/`stream_triggers`
  fields on the built Instrument).
- Produces: `_parse_instrument` now populates `Instrument.event_triggers`
  and `Instrument.stream_triggers` from `[[event_triggers]]` /
  `[[stream_triggers]]` arrays-of-tables in the instrument body.

- [ ] **Step 1: Write failing tests** (append to `tests/test_terrarium_config.py`)

```python
EVENT_TRIGGER_CONFIG = """
schema = 1
[terrarium]
name = "t"
[instruments.shroomy]
capabilities = ["gesture.tap"]
accepted_cues = ["midi"]
  [[instruments.shroomy.event_triggers]]
  name = "tap"
  description = "a tap"
    [instruments.shroomy.event_triggers.thresholds]
    peak_g = 2.0
    window_ms = 200
  [[instruments.shroomy.stream_triggers]]
  name = "smooth_tilt"
  description = "EMA over tilt"
  verb = "tilt"
  arg = 0
  transform = "smooth"
    [instruments.shroomy.stream_triggers.params]
    alpha = 0.4
[rooms.T]
description = "d"
backends = ["devicelink"]
"""


def test_instrument_event_and_stream_triggers_parse():
    config = parse_terrarium_config(EVENT_TRIGGER_CONFIG, source="test")
    inst = config.instruments["shroomy"]
    (tap,) = inst.event_triggers
    assert tap.name == "tap"
    assert tap.thresholds == {"peak_g": 2.0, "window_ms": 200}
    (smooth,) = inst.stream_triggers
    assert smooth.verb == "tilt" and smooth.transform == "smooth"


def test_event_trigger_missing_name_is_located():
    bad = EVENT_TRIGGER_CONFIG.replace('name = "tap"\n  ', "")
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(bad, source="test")
    assert "instruments.shroomy" in str(exc.value)
```

(Adjust the room stanza to whatever minimal valid room the file's existing
fixtures use; copy an existing minimal-config constant if one exists rather
than inventing a new one.)

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q`
Expected: new tests FAIL (`event_triggers` empty / no such key handling)

- [ ] **Step 3: Implement**

In `_parse_instrument`, before constructing `Instrument`:

```python
def _parse_trigger_tables(iname: str, iraw: dict, *, source: str, key: str,
                          table: str, required: tuple[str, ...]) -> list[dict]:
    raw_list = iraw.get(table, [])
    if not isinstance(raw_list, list):
        raise TerrariumConfigError(
            source=source, key=key,
            message=f"{table} must be an array of "
                    f"[[instruments.{iname}.{table}]] tables")
    out = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"{table} entries must be tables, got {entry!r}")
        for req in required:
            if req not in entry:
                raise TerrariumConfigError(
                    source=source, key=key,
                    message=f"{table} entry missing required {req!r}")
        out.append(entry)
    return out
```

then:

```python
    event_triggers = tuple(
        EventTrigger(name=e["name"], description=e.get("description", ""),
                     thresholds=dict(e.get("thresholds", {})))
        for e in _parse_trigger_tables(iname, iraw, source=source, key=key,
                                       table="event_triggers",
                                       required=("name",)))
    stream_triggers = tuple(
        StreamTrigger(name=e["name"], description=e.get("description", ""),
                      verb=e["verb"], arg=int(e["arg"]),
                      transform=e["transform"],
                      params=dict(e.get("params", {})))
        for e in _parse_trigger_tables(iname, iraw, source=source, key=key,
                                       table="stream_triggers",
                                       required=("name", "verb", "arg",
                                                 "transform")))
```

and pass both into the `Instrument(...)` constructor. Import `EventTrigger`,
`StreamTrigger` from `control.triggers` at the top. Defects inside a trigger
(bad transform, non-numeric threshold) already surface through
`validate_instrument` as a located `TerrariumConfigError`; do not duplicate
those checks here.

- [ ] **Step 4: Run full suite** (this touches every config parse)

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS, count >= baseline

- [ ] **Step 5: Commit**

```bash
git add control/terrarium_config.py tests/test_terrarium_config.py
git commit -m "feat(config): parse event/stream triggers on instruments"
```

---

### Task 3: Catalog wired into config load + shipped migration

**Files:**
- Modify: `control/terrarium_config.py` (`parse_terrarium_config` gains
  `extra_instruments`; `load_terrarium_config` loads the catalog),
  `terrarium.toml` (instruments move out; add `instrument_paths`)
- Create: `instruments/tuneshroom.toml`, `instruments/venue_array.toml`,
  `instruments/dev_strip.toml`, `instruments/drafts/.gitkeep`
- Test: `tests/test_catalog.py` (append), `tests/test_terrarium_config.py` (append)

**Interfaces:**
- Consumes: `load_catalog` (Task 1).
- Produces:
  - `parse_terrarium_config(text, source, extra_instruments: dict[str, Instrument] | None = None)`:
    extras merge under the inline `[instruments.*]` table; a name collision
    between the two is a located `TerrariumConfigError` (never silent
    precedence). Fixture `instrument = "<name>"` resolves against the merged
    dict. `TerrariumConfig.instruments` holds the merged dict.
  - `load_terrarium_config(path)`: reads `[terrarium] instrument_paths`
    (default `["instruments"]`), resolves each relative to the config file's
    directory, loads each catalog, and passes the union of their `published`
    dicts as `extra_instruments` (collision across catalog roots: located
    error).
  - Shipped `instruments/tuneshroom.toml` parses **equal to**
    `control.instrument.TUNESHROOM` (pinned by test; the code constant
    remains the wire default for `DeviceInfo.carried`).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_catalog.py`:

```python
from control.instrument import TUNESHROOM
from control.terrarium_config import load_terrarium_config, parse_terrarium_config


def test_shipped_tuneshroom_catalog_file_matches_the_code_constant():
    cat = load_catalog(Path("instruments"))
    assert cat.published["tuneshroom"] == TUNESHROOM


def test_shipped_config_still_resolves_fixture_instruments():
    config = load_terrarium_config("terrarium.toml")
    assert "venue_array" in config.instruments
    assert "dev_strip" in config.instruments
    fixture = config.rooms["TEST"].profile.fixtures[0]
    assert fixture.instrument.name == "dev_strip"


def test_extra_instrument_collision_with_inline_is_located(tmp_path):
    text = (
        'schema = 1\n[terrarium]\nname = "t"\n'
        '[instruments.dupe]\ncapabilities = []\n'
        '[rooms.T]\ndescription = "d"\nbackends = ["devicelink"]\n')
    from control.instrument import Instrument
    with pytest.raises(TerrariumConfigError) as exc:
        parse_terrarium_config(
            text, source="test",
            extra_instruments={"dupe": Instrument(name="dupe")})
    assert "dupe" in str(exc.value)
```

(If `RoomSpec.profile.fixtures` is not the accessor shape, read
`control/room_profile.py` and fix the attribute chain in the test rather
than the production code.)

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/python -m pytest tests/test_catalog.py -q`
Expected: FAIL (no `instruments/` dir, no `extra_instruments` param)

- [ ] **Step 3: Implement**

1. `parse_terrarium_config(text, source, extra_instruments=None)`: after
   building the inline `instruments` dict, merge:

```python
    for iname, inst in (extra_instruments or {}).items():
        if iname in instruments:
            raise TerrariumConfigError(
                source=source, key=f"instruments.{iname}",
                message="defined both inline and in an instrument catalog; "
                        "pick one home")
        instruments[iname] = inst
```

2. `load_terrarium_config`:

```python
def load_terrarium_config(path: str) -> TerrariumConfig:
    from control.catalog import load_catalog  # local: avoid import cycle
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return parse_terrarium_config(text, source=path)  # located there
    instrument_paths = raw.get("terrarium", {}).get(
        "instrument_paths", ["instruments"])
    extra: dict = {}
    base = Path(path).resolve().parent
    for rel in instrument_paths:
        for name, inst in load_catalog(base / rel).published.items():
            if name in extra:
                raise TerrariumConfigError(
                    source=str(base / rel), key=f"instruments.{name}",
                    message="defined in more than one catalog root")
            extra[name] = inst
    return parse_terrarium_config(text, source=path, extra_instruments=extra)
```

   Add `from pathlib import Path` if missing.
3. Migrate the shipped config: delete the whole `[instruments.venue_array]`
   and `[instruments.dev_strip]` tables from `terrarium.toml`, add
   `instrument_paths = ["instruments"]` under `[terrarium]`, and create the
   three catalog files. `venue_array.toml` / `dev_strip.toml` are the table
   bodies verbatim (top-level keys, `[ambient.light]` etc.).
   `tuneshroom.toml`:

```toml
description = "Handheld 12-LED Tuneshroom (8-ring + 4-stem)"
capabilities = ["light.pixels", "audio.samples", "gesture.tap", "gesture.tilt"]
accepted_cues = ["midi", "play", "solid", "mute"]

[[event_triggers]]
name = "tap"
description = "a single or double tap on the shell"
  [event_triggers.thresholds]
  peak_g = 2.0
  window_ms = 200
  double_ms = 400

[[event_triggers]]
name = "shake"
description = "a shake gesture"
  [event_triggers.thresholds]
  peak_g = 2.0
  window_ms = 200
```

   If the equality test fails on capability ordering or float identity,
   fix the file, never the constant.

- [ ] **Step 4: Run full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS. Any test that hand-built configs with inline instruments
still passes (inline stays supported; only the shipped file moved).

- [ ] **Step 5: Commit**

```bash
git add control/terrarium_config.py control/catalog.py terrarium.toml instruments tests
git commit -m "feat(catalog): load instruments/ catalog at config load; migrate shipped instruments"
```

---

### Task 4: Catalog write-side (save draft, clone, publish)

**Files:**
- Modify: `control/catalog.py`
- Test: `tests/test_catalog.py` (append)

**Interfaces:**
- Consumes: Task 1's layout + `_parse_entry` internals.
- Produces (all never-raise on operator mistakes; a `str` reason refusal,
  `None` on success, matching `fire_function`'s convention; programmer
  errors still raise):
  - `save_draft(root: Path, name: str, text: str) -> tuple[str | None, list[str]]`:
    writes `root/drafts/<name>.toml` (creating `drafts/` if needed) after
    checking `CATALOG_NAME_RE`; returns `(refusal, errors)` where refusal
    covers a bad name, and `errors` is the list of validation problems in
    the saved text (saving invalid draft text is allowed; the errors ride
    back to the panel).
  - `clone_entry(root: Path, source_state: str, source_name: str, new_name: str) -> str | None`:
    copies the source file's bytes to `root/drafts/<new_name>.toml`;
    refuses a bad new name, a missing source, or an existing draft target.
  - `publish_entry(root: Path, name: str) -> str | None`: parses
    `root/drafts/<name>.toml` with full validation; on success **moves** it
    to `root/<name>.toml` (replacing any prior published file); refuses a
    missing draft or any validation error (the error text is the reason).

- [ ] **Step 1: Write failing tests**

```python
from control.catalog import clone_entry, publish_entry, save_draft


def test_save_draft_roundtrips_and_reports_errors(tmp_path):
    root = make_catalog(tmp_path)
    refusal, errors = save_draft(root, "wip", 'capabilities = ["nope"]')
    assert refusal is None
    assert errors and "nope" in errors[0]
    assert (root / "drafts" / "wip.toml").read_text() == 'capabilities = ["nope"]'
    refusal, errors = save_draft(root, "wip", GOOD)
    assert refusal is None and errors == []


def test_save_draft_refuses_bad_name(tmp_path):
    root = make_catalog(tmp_path)
    refusal, _ = save_draft(root, "../evil", GOOD)
    assert refusal is not None
    assert not (tmp_path / "evil.toml").exists()


def test_clone_published_to_new_draft(tmp_path):
    root = make_catalog(tmp_path)
    (root / "glowcap.toml").write_text(GOOD)
    assert clone_entry(root, "published", "glowcap", "glowcap2") is None
    assert (root / "drafts" / "glowcap2.toml").read_text() == GOOD
    # refuses to clobber an existing draft
    assert clone_entry(root, "published", "glowcap", "glowcap2") is not None


def test_publish_moves_a_valid_draft(tmp_path):
    root = make_catalog(tmp_path)
    save_draft(root, "wip", GOOD)
    assert publish_entry(root, "wip") is None
    assert (root / "wip.toml").exists()
    assert not (root / "drafts" / "wip.toml").exists()


def test_publish_refuses_an_invalid_draft_in_place(tmp_path):
    root = make_catalog(tmp_path)
    save_draft(root, "wip", 'capabilities = ["nope"]')
    reason = publish_entry(root, "wip")
    assert reason is not None and "nope" in reason
    assert (root / "drafts" / "wip.toml").exists()
    assert not (root / "wip.toml").exists()
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/python -m pytest tests/test_catalog.py -q`
Expected: FAIL, ImportError on the three new names

- [ ] **Step 3: Implement**

```python
def _refuse_name(name: str) -> str | None:
    if not CATALOG_NAME_RE.match(name):
        return f"instrument name {name!r} must match [A-Za-z0-9_-]+"
    return None


def _draft_errors(name: str, text: str, path: Path) -> list[str]:
    try:
        _parse_instrument(name, tomllib.loads(text), source=str(path))
    except tomllib.TOMLDecodeError as exc:
        return [f"not valid TOML: {exc}"]
    except TerrariumConfigError as exc:
        return [str(exc)]
    return []


def save_draft(root: Path, name: str, text: str) -> tuple[str | None, list[str]]:
    refusal = _refuse_name(name)
    if refusal:
        return refusal, []
    drafts = Path(root) / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    return None, _draft_errors(name, text, path)


def clone_entry(root: Path, source_state: str, source_name: str,
                new_name: str) -> str | None:
    refusal = _refuse_name(new_name)
    if refusal:
        return refusal
    root = Path(root)
    src = (root / f"{source_name}.toml" if source_state == "published"
           else root / "drafts" / f"{source_name}.toml")
    if _refuse_name(source_name) or not src.is_file():
        return f"no {source_state} instrument named {source_name!r}"
    dst = root / "drafts" / f"{new_name}.toml"
    if dst.exists():
        return f"draft {new_name!r} already exists"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return None


def publish_entry(root: Path, name: str) -> str | None:
    if _refuse_name(name):
        return f"no draft named {name!r}"
    root = Path(root)
    src = root / "drafts" / f"{name}.toml"
    if not src.is_file():
        return f"no draft named {name!r}"
    errors = _draft_errors(name, src.read_text(encoding="utf-8"), src)
    if errors:
        return "; ".join(errors)
    src.replace(root / f"{name}.toml")
    return None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_catalog.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add control/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): draft save, clone, publish write-side"
```

---

### Task 5: Console wire commands + events for designs

**Files:**
- Modify: `console/protocol.py` (admin commands + events),
  `console/agent.py` (routing + handlers + snapshot key)
- Test: `tests/test_console_protocol.py` (append; create if the repo keeps
  protocol tests inside `tests/test_console_agent.py`, follow the existing
  home), `tests/test_console_agent.py` (append)

**Interfaces:**
- Consumes: Task 1/4 catalog API.
- Produces:
  - Commands (parsed by `parse_admin_command`; these are local operator
    actions, never uplink, same rationale as `arm_room`):
    `ListDesignsCommand`, `GetDesignCommand(state, name)`,
    `SaveDesignCommand(name, text)`, `PublishDesignCommand(name)`,
    `CloneDesignCommand(source_state, source_name, new_name)`.
  - Events:
    - `designs_listed_event(designs: list[dict]) -> {"event": "designs_listed", "designs": [...]}`,
      each row `{"name", "state", "error"}` (error None unless a draft
      failed to parse), sorted by (name, state).
    - `design_event(name, state, text, errors) -> {"event": "design", ...}`
      carrying the raw TOML text for the editor.
    - `designs_changed_event(designs)` broadcast after any mutation, same
      row shape as `designs_listed_event`.
  - `ConsoleAgent.__init__` gains keyword `catalog_root: Path | None = None`.
    All design commands answer `error_event(cmd, "no instrument catalog")`
    when it is None. The snapshot gains a `"designs"` key (the listed rows,
    or `[]`).
  - The agent re-loads the catalog with `load_catalog(catalog_root)` on
    every design command (files are tiny; no cache invalidation problem).

- [ ] **Step 1: Write failing protocol tests**

```python
from console import protocol


def test_design_admin_commands_parse():
    cmd = protocol.parse_admin_command({"command": "get_design",
                                        "state": "draft", "name": "wip"})
    assert isinstance(cmd, protocol.GetDesignCommand)
    assert (cmd.state, cmd.name) == ("draft", "wip")
    cmd = protocol.parse_admin_command(
        {"command": "save_design", "name": "wip", "text": "x = 1"})
    assert isinstance(cmd, protocol.SaveDesignCommand)
    cmd = protocol.parse_admin_command({"command": "publish_design",
                                        "name": "wip"})
    assert isinstance(cmd, protocol.PublishDesignCommand)
    cmd = protocol.parse_admin_command(
        {"command": "clone_design", "source_state": "published",
         "source_name": "tuneshroom", "new_name": "fungiflute"})
    assert isinstance(cmd, protocol.CloneDesignCommand)
    cmd = protocol.parse_admin_command({"command": "list_designs"})
    assert isinstance(cmd, protocol.ListDesignsCommand)


def test_design_command_missing_field_raises():
    import pytest
    with pytest.raises(ValueError):
        protocol.parse_admin_command({"command": "save_design", "name": "w"})
```

And failing agent tests (append to `tests/test_console_agent.py`, reusing
that file's existing GameServer/agent fixtures; the sketch below names the
behaviors, copy the file's construction idiom):

```python
def test_design_commands_error_without_catalog(agent_without_catalog):
    reply = agent_without_catalog._handle_command({"command": "list_designs"})
    assert reply["event"] == "error"


def test_design_roundtrip_via_agent(tmp_path, agent_factory):
    root = tmp_path / "instruments"
    (root / "drafts").mkdir(parents=True)
    (root / "glowcap.toml").write_text('capabilities = ["light.pixels"]\n')
    agent = agent_factory(catalog_root=root)
    listed = agent._handle_command({"command": "list_designs"})
    assert listed["event"] == "designs_listed"
    assert listed["designs"][0]["name"] == "glowcap"
    assert agent.snapshot()["designs"][0]["name"] == "glowcap"
    assert agent._handle_command(
        {"command": "clone_design", "source_state": "published",
         "source_name": "glowcap", "new_name": "glowcap2"}) is not None
    # mutation replies with designs_changed so every client re-renders
    got = agent._handle_command({"command": "get_design",
                                 "state": "draft", "name": "glowcap2"})
    assert got["event"] == "design"
    assert "light.pixels" in got["text"]
    saved = agent._handle_command(
        {"command": "save_design", "name": "glowcap2",
         "text": 'capabilities = ["nope"]\n'})
    assert saved["event"] == "designs_changed"
    reason = agent._handle_command({"command": "publish_design",
                                    "name": "glowcap2"})
    assert reason["event"] == "error"
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py tests/test_console_protocol.py -q`
Expected: new tests FAIL

- [ ] **Step 3: Implement protocol side**

In `console/protocol.py`: five dataclasses,

```python
@dataclass
class ListDesignsCommand:
    pass


@dataclass
class GetDesignCommand:
    state: str
    name: str


@dataclass
class SaveDesignCommand:
    name: str
    text: str


@dataclass
class PublishDesignCommand:
    name: str


@dataclass
class CloneDesignCommand:
    source_state: str
    source_name: str
    new_name: str
```

extend `parse_admin_command` (require every field a string, `state` and
`source_state` in `("published", "draft")`, `ValueError` otherwise, matching
the arm_room style), and add:

```python
def design_row(entry) -> dict:
    return {"name": entry.name, "state": entry.state, "error": entry.error}


def designs_listed_event(designs: list) -> dict:
    return {"event": "designs_listed", "designs": designs}


def designs_changed_event(designs: list) -> dict:
    return {"event": "designs_changed", "designs": designs}


def design_event(name: str, state: str, text: str, errors: list) -> dict:
    return {"event": "design", "name": name, "state": state,
            "text": text, "errors": errors}
```

Update `__all__`. `design_row` takes the `CatalogEntry` duck-typed (name/
state/error attributes) so protocol keeps zero engine imports.

- [ ] **Step 4: Implement agent side**

`ConsoleAgent.__init__(..., catalog_root=None)` stores it. In
`_handle_command`, extend the admin-command name gate:

```python
        if name in ("arm_room", "release_room", "fire_function",
                    "list_designs", "get_design", "save_design",
                    "publish_design", "clone_design"):
            return self._handle_admin_command(msg)
```

In `_handle_admin_command`, before the room-command block:

```python
        if isinstance(command, (protocol.ListDesignsCommand,
                                protocol.GetDesignCommand,
                                protocol.SaveDesignCommand,
                                protocol.PublishDesignCommand,
                                protocol.CloneDesignCommand)):
            return self._handle_design_command(name, command)
```

and:

```python
    def _design_rows(self) -> list:
        from control.catalog import load_catalog
        cat = load_catalog(self.catalog_root)
        return [protocol.design_row(e) for e in sorted(
            cat.entries.values(), key=lambda e: (e.name, e.state))]

    def _handle_design_command(self, name: str, command) -> dict | None:
        if self.catalog_root is None:
            return protocol.error_event(name, "no instrument catalog")
        from control.catalog import (clone_entry, load_catalog,
                                     publish_entry, save_draft)
        if isinstance(command, protocol.ListDesignsCommand):
            return protocol.designs_listed_event(self._design_rows())
        if isinstance(command, protocol.GetDesignCommand):
            entry = load_catalog(self.catalog_root).get(
                command.state, command.name)
            if entry is None:
                return protocol.error_event(
                    name, f"no {command.state} design {command.name!r}")
            text = entry.path.read_text(encoding="utf-8")
            errors = [entry.error] if entry.error else []
            return protocol.design_event(entry.name, entry.state, text, errors)
        if isinstance(command, protocol.SaveDesignCommand):
            refusal, _errors = save_draft(
                self.catalog_root, command.name, command.text)
        elif isinstance(command, protocol.PublishDesignCommand):
            refusal = publish_entry(self.catalog_root, command.name)
        else:
            refusal = clone_entry(self.catalog_root, command.source_state,
                                  command.source_name, command.new_name)
        if refusal is not None:
            return protocol.error_event(name, refusal)
        return protocol.designs_changed_event(self._design_rows())
```

Note the deliberate shape: mutations reply `designs_changed` to the caller.
Broadcast-to-all-clients rides the existing reply/broadcast mechanics; if
`_handle_command` replies only to the sender in this console, ALSO push the
`designs_changed` event through the same broadcast path `functions_changed`
uses (read `_broadcast_functions_if_changed` and mirror it; keep whichever
the existing mechanics make natural, and assert it in the test you write).
Add `"designs": self._design_rows() if self.catalog_root else []` to
`snapshot()` and thread a `designs` kwarg through
`protocol.snapshot_event`.

- [ ] **Step 5: Run, verify pass; full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add console/protocol.py console/agent.py tests
git commit -m "feat(console): design catalog wire commands and events"
```

---

### Task 6: Boot wiring (`harness/terrarium_boot.py`)

**Files:**
- Modify: `harness/terrarium_boot.py` (pass `catalog_root` where
  `ConsoleAgent` is constructed)
- Test: `tests/test_terrarium_boot.py` (append)

**Interfaces:**
- Consumes: `ConsoleAgent(..., catalog_root=...)` (Task 5).
- Produces: a booted console whose agent has
  `catalog_root == <config dir>/instruments` (first entry of
  `instrument_paths`, resolved the same way `load_terrarium_config`
  resolves it). Locate the `ConsoleAgent(` construction site in
  `harness/terrarium_boot.py` (grep; it is inside `build()`) and pass the
  resolved path; if the boot path only has the parsed `TerrariumConfig`,
  add `instrument_roots: tuple[Path, ...]` to `TerrariumConfig` populated
  by `load_terrarium_config` (empty tuple from bare
  `parse_terrarium_config`) and use `instrument_roots[0]` when non-empty.

- [ ] **Step 1: Write a failing test** asserting the built console agent's
  `catalog_root` is the shipped `instruments/` dir (follow
  `tests/test_terrarium_boot.py`'s existing build fixtures; assert
  `agent.catalog_root and agent.catalog_root.name == "instruments"`).
- [ ] **Step 2: Run, verify failure.**
- [ ] **Step 3: Implement** (per Interfaces above; prefer the
  `TerrariumConfig.instrument_roots` field, it keeps path resolution in one
  place).
- [ ] **Step 4: Run full suite:** `.venv/bin/python -m pytest tests -q` -> PASS.
- [ ] **Step 5: Commit:**

```bash
git add harness/terrarium_boot.py control/terrarium_config.py tests/test_terrarium_boot.py
git commit -m "feat(boot): console gets the instrument catalog root"
```

---

### Task 7: Design panel UI (nav view, list, raw-TOML editor, clone, publish)

**Files:**
- Create: `console/static/design.js`, `tests/js/design_panel.test.js`
- Modify: `console/static/index.html` (nav button + view container + script
  tag), `console/static/shell.js` (VIEWS entry), `console/static/wire.js`
  (route `designs_listed` / `design` / `designs_changed` to design.js —
  mirror how `functions_changed` reaches `functions.js`)

**Interfaces:**
- Consumes: Task 5 wire shapes verbatim.
- Produces: `design.js` exports (same module pattern as `functions.js`;
  read it first and copy its export/registration idiom):
  - `renderDesigns(listEl, designs, onSelect)`: one row per design,
    `<name> [draft|published]`, error badge when `error` non-null.
  - `openDesign(msg)`: fills the editor (`#designText` textarea) with
    `msg.text`, shows `msg.errors` in `#designErrors`, remembers
    `{name, state}`.
  - Buttons: `#designSave` sends `{command: "save_design", name, text}`
    (a published selection saves as a draft of the same name, which is the
    draft-shadowing edit flow); `#designPublish` sends
    `{command: "publish_design", name}`; `#designClone` prompts for a new
    name (`window.prompt`, stubbed in tests) and sends `clone_design` with
    the current selection as source.
  - On `designs_changed`: re-render the list, keep the current selection
    if it still exists.

`index.html` nav gains, inside the existing `viewnav` and directly after
the Room button so Design sits under ROOM:

```html
<button id="navDesign" class="navbtn">Design</button>
```

and a `viewDesign` section with: list container `#designList`, editor
`<textarea id="designText" spellcheck="false"></textarea>`, error area
`#designErrors`, and the three buttons. `shell.js` VIEWS gains
`design: ["viewDesign", "navDesign"]`.

- [ ] **Step 1: Write failing node tests** (`tests/js/design_panel.test.js`,
  using `_dom_stub.js` the way `functions_and_rail.test.js` does; read that
  file first and mirror its harness setup). Cover: list rendering with a
  draft error badge; `openDesign` filling the textarea; Save sending the
  right command payload via a captured `send` stub; `designs_changed`
  preserving selection; Clone using the prompt stub.
- [ ] **Step 2: Run, verify failure:**
  `.venv/bin/python -m pytest tests/test_console_js.py -q` (the glob picks
  the new file up automatically).
- [ ] **Step 3: Implement** `design.js` + `index.html` + `shell.js` +
  `wire.js` routing, following the existing modules' declaration-signature
  discipline (a new field in a card counts as part of the signature).
- [ ] **Step 4: Run all JS + python console tests:**
  `.venv/bin/python -m pytest tests/test_console_js.py tests/test_console_agent.py -q` -> PASS.
- [ ] **Step 5: Manual smoke** (record the command for the executor;
  running it live is optional in a headless session):

```bash
.venv/bin/python -m harness.terrarium_boot --room TEST --console-port 8770
```

  Open the printed console URL, switch to Design, clone `tuneshroom` to a
  new name, edit, save, publish; confirm the file appears under
  `instruments/`.
- [ ] **Step 6: Commit:**

```bash
git add console/static tests/js
git commit -m "feat(console): Design panel v1 (list, TOML editor, clone, publish)"
```

---

### Task 8: Docs + closeout

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (new subsection for the catalog + Design
  panel, plus corrections to the two instrument-ingestion-path claims),
  `docs/superpowers/specs/2026-08-31-design-panel-and-instrument-catalog-design.md`
  (Status section noting what this plan shipped vs Plan 2)

- [ ] **Step 1:** Add a deep-dive entry describing: catalog layout
  (`instruments/*.toml` published, `drafts/` drafts, name = file stem),
  merge/collision rules, the never-raises write API, the design wire
  vocabulary, and the pinned TUNESHROOM file/constant equality. Mark the
  "TUNESHROOM is defined once here in code -- the only instrument"
  sentence as superseded.
- [ ] **Step 2:** Append a Status section to the spec: sections 2 (slices
  1-2 partial), 3, and 7 shipped; sections 4 (structured forms), 5, 6 are
  Plan 2; slice 3 (wire support) unplanned.
- [ ] **Step 3:** Run the full suite one last time, record the count.
- [ ] **Step 4: Commit:**

```bash
git add docs
git commit -m "docs: catalog + Design panel v1, spec status"
```

Then follow `superpowers:finishing-a-development-branch` (PR against main)
and `mm-deepdive-sync` per house closeout.
