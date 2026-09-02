# Rooms Catalog and Design Tab Room Editor Implementation Plan (Plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Rooms a file catalog mirroring the instrument catalog, migrate TEST and DEMO into it, and add a stubbed Room editor (raw TOML plus a fixture-order form) to the Console's Design tab.

**Architecture:** `control/catalog.py` gains a `kind` ("instrument" | "room") so one draft/published/clone/publish flow serves both catalogs; room files parse through the existing `_parse_room` with the loaded instrument set. `load_terrarium_config` merges `rooms/*.toml` (published) with inline `[rooms.<NAME>]` tables and refuses collisions. The Console's five design commands and their events carry `kind`; the Design tab shows a Rooms list beside Instruments over the same editor, with one structured form: the fixture list with move up/down and an instrument picker. Order applies at the next Room load.

**Tech Stack:** Python 3.11 stdlib in `control/` (tomllib), pytest, vanilla ES modules in `console/static/` tested through Node inside `tests/test_console_js.py`.

**Spec:** `docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md` section 6 (and section 12 Status for recording deviations). Plan 1 (per-fixture sessions) landed as PR #82 on branch `claude/instrument-topology-triggers-6d2031`; this plan's branch is stacked on it.

## Global Constraints

- Tests ONLY via `.venv/bin/python -m pytest tests -q` (a fresh worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`). Baseline at this branch's base (dc3b4e1): 1955 passed, 1 skipped. JS Console tests run through `tests/test_console_js.py` inside that command.
- No em dashes anywhere (code, comments, docs, commit messages). The repo's `--` style is fine.
- `control/` is pure stdlib.
- Catalog names match `[A-Za-z0-9_-]+` (existing `CATALOG_NAME_RE`). A room file's stem is the Room name; TEST and DEMO keep their exact current names and profiles.
- The loaded TEST and DEMO `RoomProfile`s after migration must equal the pre-migration ones (spec 6.2); this is the regression every live checklist step depends on.
- Existing instrument Design flow keeps working unchanged: every command and event defaults `kind` to `"instrument"` when absent.
- Room order is a configuration edit applied at the next Room load. Nothing in this plan changes a live Room.
- Commit after every task with the prefix shown in the task.

---

## File structure

| File | Responsibility after this plan |
|------|-------------------------------|
| `control/catalog.py` | Kind-aware file catalog: `CatalogEntry` (kind, instrument or room), `load_catalog(root, kind, instruments)`, `save_draft`, `clone_entry`, `publish_entry` for both kinds. |
| `control/terrarium_config.py` | `room_paths` / `room_roots`; merges catalog rooms with inline rooms; "at least one room from either source". |
| `rooms/TEST.toml`, `rooms/DEMO.toml` (new) | The shipped Room specs, moved out of `terrarium.toml`. |
| `terrarium.toml` | Terrarium-level keys and instruments only; no `[rooms.*]`. |
| `console/protocol.py`, `console/agent.py` | Design commands/events/rows carry `kind`; `rooms_root` beside `catalog_root`. |
| `harness/terrarium_boot.py` | Wires `rooms_root`; `--room` help names both sources. |
| `console/static/index.html`, `console/static/design.js` | Rooms list beside Instruments; kind-aware selection and commands. |
| `console/static/toml_edit.js` | `listFixtures`, `moveFixture`, `setFixtureInstrument` pure transforms. |
| `console/static/design_forms.js` | Kind dispatch: the room form (fixture list) vs the instrument forms. |
| `docs/MM_TERRARIUM.md`, spec Status, handoff | Recorded. |

---

### Task 1: Kind-aware catalog

**Files:**
- Modify: `control/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Produces:
  - `CatalogEntry(name, state, path, kind, instrument, room, error)`; `kind` is `"instrument"` or `"room"`; `room` is a `control.terrarium_config.RoomSpec | None`.
  - `Catalog(root, kind, entries)` replaces `InstrumentCatalog` (keep `InstrumentCatalog = Catalog` as an alias for one release so existing imports work). `.published` returns `{name: Instrument}` for kind instrument and `{name: RoomSpec}` for kind room.
  - `load_catalog(root, kind="instrument", instruments=None) -> Catalog`; `instruments` (a `{name: Instrument}` dict) is REQUIRED when `kind == "room"` (raise `ValueError` if missing) because a room's fixtures reference instruments by name.
  - `save_draft(root, name, text, kind="instrument", instruments=None) -> (refusal, errors)`, `clone_entry(root, source_state, source_name, new_name, kind="instrument") -> refusal`, `publish_entry(root, name, kind="instrument", instruments=None) -> refusal`.
  - `KINDS = ("instrument", "room")`.
- Consumes: `control.terrarium_config._parse_room(rname, rraw, *, source, instruments)` (existing) and `_parse_instrument` (existing).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_catalog.py` (it already imports `Path`, `pytest`, `clone_entry`, `load_catalog`, `publish_entry`, `save_draft`, `TerrariumConfigError`, `load_terrarium_config`):

```python
from control.instrument import Instrument
from control.catalog import Catalog, CatalogEntry, KINDS

STRIP = Instrument(name="strip", capabilities=frozenset({"light.surface"}),
                   accepted_cues=("midi", "play", "solid", "mute"))
INSTRUMENTS = {"strip": STRIP}

ROOM_TOML = '''description = "Two strips"
backends = ["devicelink"]

[[fixtures]]
name = "main"
color_order = "GRB"
instrument = "strip"
  [[fixtures.blocks]]
  name = "main"
  start = 0
  count = 60
  [[fixtures.zones]]
  name = "left"
  start = 0
  count = 30
  [[fixtures.zones]]
  name = "right"
  start = 30
  count = 30

[[fixtures]]
name = "accent"
color_order = "GRB"
instrument = "strip"
  [[fixtures.blocks]]
  name = "accent"
  start = 0
  count = 30
'''


def test_kinds_are_instrument_and_room():
    assert KINDS == ("instrument", "room")


def test_room_catalog_requires_instruments(tmp_path):
    with pytest.raises(ValueError, match="instruments"):
        load_catalog(tmp_path, kind="room")


def test_published_room_parses_to_a_room_spec(tmp_path):
    (tmp_path / "LOFT.toml").write_text(ROOM_TOML)
    cat = load_catalog(tmp_path, kind="room", instruments=INSTRUMENTS)
    entry = cat.get("published", "LOFT")
    assert entry.kind == "room" and entry.instrument is None
    spec = entry.room
    assert spec.name == "LOFT"
    assert [f.name for f in spec.profile.fixtures] == ["main", "accent"]
    assert spec.profile.surface_id == "room_loft"
    assert cat.published == {"LOFT": spec}


def test_published_room_with_unknown_instrument_raises_located(tmp_path):
    (tmp_path / "LOFT.toml").write_text(ROOM_TOML.replace('"strip"', '"ghost"'))
    with pytest.raises(TerrariumConfigError, match="ghost"):
        load_catalog(tmp_path, kind="room", instruments=INSTRUMENTS)


def test_room_draft_errors_are_collected_not_raised(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "LOFT.toml").write_text("description = 1\n[[fixtures]]\nname = 'x'\n")
    cat = load_catalog(tmp_path, kind="room", instruments=INSTRUMENTS)
    entry = cat.get("draft", "LOFT")
    assert entry.room is None and entry.error


def test_room_save_draft_reports_room_errors(tmp_path):
    refusal, errors = save_draft(tmp_path, "LOFT", ROOM_TOML.replace('"strip"', '"ghost"'),
                                 kind="room", instruments=INSTRUMENTS)
    assert refusal is None
    assert any("ghost" in e for e in errors)
    refusal, errors = save_draft(tmp_path, "LOFT", ROOM_TOML,
                                 kind="room", instruments=INSTRUMENTS)
    assert (refusal, errors) == (None, [])


def test_room_publish_moves_a_valid_draft(tmp_path):
    save_draft(tmp_path, "LOFT", ROOM_TOML, kind="room", instruments=INSTRUMENTS)
    assert publish_entry(tmp_path, "LOFT", kind="room", instruments=INSTRUMENTS) is None
    assert (tmp_path / "LOFT.toml").is_file()
    assert not (tmp_path / "drafts" / "LOFT.toml").exists()


def test_room_publish_refuses_an_invalid_draft_in_place(tmp_path):
    save_draft(tmp_path, "LOFT", ROOM_TOML.replace('"strip"', '"ghost"'),
               kind="room", instruments=INSTRUMENTS)
    refusal = publish_entry(tmp_path, "LOFT", kind="room", instruments=INSTRUMENTS)
    assert refusal and "ghost" in refusal
    assert (tmp_path / "drafts" / "LOFT.toml").is_file()


def test_room_clone_names_the_kind_in_its_refusal(tmp_path):
    refusal = clone_entry(tmp_path, "published", "NOPE", "NEW", kind="room")
    assert refusal == "no published room named 'NOPE'"


def test_instrument_kind_is_the_default_and_unchanged(tmp_path):
    (tmp_path / "glow.toml").write_text('description = "g"\ncapabilities = ["light.surface"]\n'
                                        'accepted_cues = ["midi"]\n')
    cat = load_catalog(tmp_path)
    assert cat.kind == "instrument"
    entry = cat.get("published", "glow")
    assert entry.kind == "instrument" and entry.room is None
    assert entry.instrument.name == "glow"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_catalog.py -q`
Expected: FAIL with `ImportError: cannot import name 'Catalog'` (or `KINDS`).

- [ ] **Step 3: Implement**

In `control/catalog.py`:

```python
from control.terrarium_config import RoomSpec, TerrariumConfigError, _parse_instrument, _parse_room

KINDS = ("instrument", "room")


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    state: str                     # "published" | "draft"
    path: Path
    kind: str = "instrument"       # one of KINDS
    instrument: Instrument | None = None   # kind == "instrument", None when a draft failed
    room: RoomSpec | None = None           # kind == "room", None when a draft failed
    error: str | None = None


@dataclass(frozen=True)
class Catalog:
    """entries is keyed "<state>:<name>" so a draft edit of a published
    entry does not shadow the published one. One Catalog holds one kind."""
    root: Path
    kind: str
    entries: dict[str, CatalogEntry]

    def get(self, state: str, name: str) -> CatalogEntry | None:
        return self.entries.get(f"{state}:{name}")

    @property
    def published(self) -> dict:
        """{name: Instrument} for kind "instrument", {name: RoomSpec} for
        kind "room"; published entries only."""
        out = {}
        for e in self.entries.values():
            if e.state == "published":
                out[e.name] = e.instrument if self.kind == "instrument" else e.room
        return out


InstrumentCatalog = Catalog   # compatibility alias; remove after Plan 2 lands


def _check_kind(kind: str, instruments) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown catalog kind {kind!r}; known: {KINDS}")
    if kind == "room" and instruments is None:
        raise ValueError("a room catalog needs the loaded instruments to "
                         "resolve fixture instrument names")


def _parse_text(name: str, text: str, path: Path, kind: str, instruments):
    """Parse one entry's TOML text into its object for `kind`. Raises
    tomllib.TOMLDecodeError or TerrariumConfigError."""
    raw = tomllib.loads(text)
    if kind == "instrument":
        return _parse_instrument(name, raw, source=str(path))
    return _parse_room(name, raw, source=str(path), instruments=instruments)
```

Rewrite `_parse_entry(path, state, kind, instruments)` to call `_parse_text` and build the entry with `instrument=obj if kind == "instrument" else None`, `room=obj if kind == "room" else None`; published failures still raise (wrapping a TOMLDecodeError in a located `TerrariumConfigError` as today); drafts collect `error`. `_refuse_name(name, kind)` says `f"{kind} name {name!r} must match [A-Za-z0-9_-]+"`. `_draft_errors(name, text, path, kind, instruments)` uses `_parse_text`. `save_draft(root, name, text, kind="instrument", instruments=None)`, `publish_entry(root, name, kind="instrument", instruments=None)` call `_check_kind` first and thread the kind and instruments; `clone_entry(root, source_state, source_name, new_name, kind="instrument")` uses `f"no {source_state} {kind} named {source_name!r}"`. `load_catalog(root, kind="instrument", instruments=None)` calls `_check_kind`, passes kind/instruments to `_parse_entry`, returns `Catalog(root=root, kind=kind, entries=entries)`. Update the module docstring: `instruments/*.toml` and `rooms/*.toml`.

`control/terrarium_config.py` imports `load_catalog` locally inside `load_terrarium_config` to avoid a cycle; the new top-level import of `RoomSpec`/`_parse_room` from `terrarium_config` into `catalog` is the same direction as today's `_parse_instrument` import, so no new cycle.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS (existing callers use the defaults).

- [ ] **Step 5: Commit**

```bash
git add control/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): kind-aware catalog parses rooms/*.toml alongside instruments"
```

---

### Task 2: `room_paths` in the Terrarium config

**Files:**
- Modify: `control/terrarium_config.py`
- Test: `tests/test_terrarium_config.py`

**Interfaces:**
- Produces: `TerrariumConfig.room_roots: tuple[Path, ...] = ()`; `[terrarium] room_paths` (default `["rooms"]`) resolved relative to the config file like `instrument_paths`; `parse_terrarium_config(text, source, extra_instruments=None, *, require_rooms=True)`; `load_terrarium_config` merges published catalog rooms into `config.rooms` and raises a located `TerrariumConfigError` (`key="rooms.<NAME>"`, message "defined both inline and in a rooms catalog; pick one home") on a collision, and (`key="rooms"`, message "at least one room required: a [rooms.<NAME>] table or a rooms catalog entry") when the union is empty.
- Consumes: `load_catalog(root, kind="room", instruments=...)` from Task 1.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_terrarium_config.py` (read its top for existing imports and a minimal-config helper; write a local helper if none fits):

```python
import textwrap
from control.terrarium_config import load_terrarium_config, TerrariumConfigError

_INSTRUMENT = '''description = "strip"
capabilities = ["light.surface"]
accepted_cues = ["midi", "play", "solid", "mute"]
'''
_ROOM = '''description = "Loft"
backends = ["devicelink"]
[[fixtures]]
name = "main"
color_order = "GRB"
instrument = "strip"
  [[fixtures.blocks]]
  name = "main"
  start = 0
  count = 10
'''


def _write_tree(tmp_path, config_text, rooms=None):
    (tmp_path / "instruments").mkdir()
    (tmp_path / "instruments" / "strip.toml").write_text(_INSTRUMENT)
    (tmp_path / "rooms").mkdir()
    for name, text in (rooms or {}).items():
        (tmp_path / "rooms" / f"{name}.toml").write_text(text)
    cfg = tmp_path / "terrarium.toml"
    cfg.write_text(textwrap.dedent(config_text))
    return str(cfg)


_HEAD = '''schema = 1
[terrarium]
name = "t"
'''


def test_catalog_rooms_load_from_the_default_rooms_dir(tmp_path):
    cfg = _write_tree(tmp_path, _HEAD, rooms={"LOFT": _ROOM})
    config = load_terrarium_config(cfg)
    assert set(config.rooms) == {"LOFT"}
    assert config.rooms["LOFT"].profile.surface_id == "room_loft"
    assert config.room_roots == (tmp_path / "rooms",)


def test_room_paths_key_overrides_the_default(tmp_path):
    (tmp_path / "venues").mkdir()
    (tmp_path / "venues" / "HALL.toml").write_text(_ROOM)
    cfg = _write_tree(tmp_path, _HEAD + 'room_paths = ["venues"]\n')
    config = load_terrarium_config(cfg)
    assert set(config.rooms) == {"HALL"}


def test_inline_and_catalog_rooms_merge(tmp_path):
    inline = _HEAD + '''
    [rooms.STAGE]
    backends = ["devicelink"]
      [[rooms.STAGE.fixtures]]
      name = "m"
      color_order = "GRB"
      instrument = "strip"
        [[rooms.STAGE.fixtures.blocks]]
        name = "m"
        start = 0
        count = 4
    '''
    cfg = _write_tree(tmp_path, inline, rooms={"LOFT": _ROOM})
    assert set(load_terrarium_config(cfg).rooms) == {"STAGE", "LOFT"}


def test_room_defined_inline_and_in_catalog_is_refused(tmp_path):
    inline = _HEAD + '''
    [rooms.LOFT]
    backends = ["devicelink"]
      [[rooms.LOFT.fixtures]]
      name = "m"
      color_order = "GRB"
      instrument = "strip"
        [[rooms.LOFT.fixtures.blocks]]
        name = "m"
        start = 0
        count = 4
    '''
    cfg = _write_tree(tmp_path, inline, rooms={"LOFT": _ROOM})
    with pytest.raises(TerrariumConfigError, match=r"rooms\.LOFT.*both inline and in a rooms catalog"):
        load_terrarium_config(cfg)


def test_no_room_anywhere_is_refused(tmp_path):
    cfg = _write_tree(tmp_path, _HEAD)
    with pytest.raises(TerrariumConfigError, match="at least one room"):
        load_terrarium_config(cfg)


def test_catalog_room_fixture_may_use_a_catalog_instrument(tmp_path):
    cfg = _write_tree(tmp_path, _HEAD, rooms={"LOFT": _ROOM})
    config = load_terrarium_config(cfg)
    assert config.rooms["LOFT"].profile.fixtures[0].instrument.name == "strip"
```

If the file already has a helper that writes a config tree into `tmp_path`, reuse it instead of `_write_tree` and adapt the tests' bodies accordingly.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q -k "catalog_room or room_paths or inline_and or no_room_anywhere"`
Expected: FAIL (`rooms` KeyError / "at least one [rooms.<NAME>] required").

- [ ] **Step 3: Implement**

In `control/terrarium_config.py`:

1. `TerrariumConfig` gains `room_roots: tuple[Path, ...] = ()` after `instrument_roots`, with a comment mirroring `instrument_roots`' (the Console's design panel reads `room_roots[0]` as its `rooms_root`).
2. `parse_terrarium_config(text, source, extra_instruments=None, *, require_rooms=True)`: the "at least one" check becomes:

```python
    rooms_raw = raw.get("rooms", {})
    if not isinstance(rooms_raw, dict):
        raise TerrariumConfigError(source=source, key="rooms",
                                   message="[rooms] must be a table of [rooms.<NAME>] tables")
    if require_rooms and not rooms_raw:
        raise TerrariumConfigError(
            source=source, key="rooms",
            message="at least one room required: a [rooms.<NAME>] table or a "
                    "rooms catalog entry")
```

3. `load_terrarium_config`: after computing the instrument catalog extras and parsing with `require_rooms=False`, resolve `room_paths = raw.get("terrarium", {}).get("room_paths", ["rooms"])`, `room_roots = tuple(base / rel for rel in room_paths)`, then:

```python
    rooms = dict(config.rooms)
    for root in room_roots:
        for rname, spec in load_catalog(root, kind="room",
                                        instruments=config.instruments).published.items():
            if rname in rooms:
                raise TerrariumConfigError(
                    source=str(root), key=f"rooms.{rname}",
                    message="defined both inline and in a rooms catalog; pick one home")
            rooms[rname] = spec
    if not rooms:
        raise TerrariumConfigError(
            source=path, key="rooms",
            message="at least one room required: a [rooms.<NAME>] table or a "
                    "rooms catalog entry")
    return replace(config, rooms=rooms, instrument_roots=roots, room_roots=room_roots)
```

A collision between two catalog roots is refused the same way the instrument loop does it. Keep `parse_terrarium_config`'s default `require_rooms=True` so direct callers (`harness/terrarium_boot.py` builds a `TerrariumConfig` literal; tests parse text) keep today's behaviour.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS. `terrarium.toml` still carries inline rooms at this commit and `rooms/` does not exist yet, so nothing shipped changes.

- [ ] **Step 5: Commit**

```bash
git add control/terrarium_config.py tests/test_terrarium_config.py
git commit -m "feat(config): room_paths rooms catalog merges with inline [rooms.<NAME>] tables"
```

---

### Task 3: Migrate TEST and DEMO into `rooms/`

**Files:**
- Create: `rooms/TEST.toml`, `rooms/DEMO.toml`, `rooms/drafts/.gitkeep` (only if `instruments/drafts/` has one; mirror it)
- Modify: `terrarium.toml`, `harness/terrarium_boot.py` (the `--config` / `--room` help strings, lines ~1185-1196)
- Test: `tests/test_terrarium_config.py`

**Interfaces:**
- Produces: `load_terrarium_config("terrarium.toml").rooms` has exactly `TEST` and `DEMO`, both from the catalog, with profiles equal to the pre-migration ones.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_terrarium_config.py`. `PRE_MIGRATION` is the exact `terrarium.toml` text at commit dc3b4e1 (run `git show dc3b4e1:terrarium.toml` and paste it verbatim into the triple-quoted string; it is 93 lines):

```python
PRE_MIGRATION = r'''<paste git show dc3b4e1:terrarium.toml here>'''


def test_shipped_rooms_come_from_the_catalog_and_match_the_pre_migration_profiles():
    config = load_terrarium_config("terrarium.toml")
    assert set(config.rooms) == {"TEST", "DEMO"}
    before = parse_terrarium_config(PRE_MIGRATION, source="pre-migration",
                                    extra_instruments=config.instruments)
    for name in ("TEST", "DEMO"):
        assert config.rooms[name].profile == before.rooms[name].profile
        assert config.rooms[name].backends == before.rooms[name].backends
        assert config.rooms[name].description == before.rooms[name].description
        assert config.rooms[name].node_id == before.rooms[name].node_id
    assert not tomllib.loads(open("terrarium.toml").read()).get("rooms")
```

`parse_terrarium_config` with `extra_instruments` will raise on the inline/catalog instrument collision if `PRE_MIGRATION` still had inline `[instruments.*]`; it does not (they moved to the catalog earlier), so this parses. Import `tomllib` and `parse_terrarium_config` at the top if missing.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_terrarium_config.py -q -k pre_migration`
Expected: FAIL on the last assertion (`terrarium.toml` still has rooms) or on `set(config.rooms)` if a collision is raised.

- [ ] **Step 3: Migrate**

Create `rooms/TEST.toml` from the `[rooms.TEST]` body: drop the `[rooms.TEST]` header line, and rewrite every `[[rooms.TEST.fixtures]]` to `[[fixtures]]`, `[[rooms.TEST.fixtures.blocks]]` to `[[fixtures.blocks]]`, `[[rooms.TEST.fixtures.zones]]` to `[[fixtures.zones]]`, keeping the existing indentation (unindented `[[fixtures]]`, two-space-indented children) so `toml_edit.js`'s `splitBlocks` treats each fixture and its children as one block. Same for DEMO. Remove both tables from `terrarium.toml`, leaving `schema` and `[terrarium]` (add `room_paths = ["rooms"]` explicitly so the file documents the default). Update the two argparse help strings so they read "its `[rooms.<NAME>]` tables and its rooms catalog (`room_paths`, default `rooms/`) are the valid --room values".

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS. Every test that loads `terrarium.toml` (`tests/test_devicelink_agent.py`'s `TEST_PROFILE`, boot tests, the ChaseBit DEMO refusal test) must still pass with identical profiles.

- [ ] **Step 5: Commit**

```bash
git add rooms terrarium.toml harness/terrarium_boot.py tests/test_terrarium_config.py
git commit -m "feat(rooms): migrate TEST and DEMO into the rooms/ catalog"
```

---

### Task 4: Console design commands and events carry `kind`; `rooms_root` wiring

**Files:**
- Modify: `console/protocol.py`, `console/agent.py`, `harness/terrarium_boot.py`
- Test: `tests/test_console_agent.py`, `tests/test_terrarium_boot.py`, `tests/test_console_protocol.py` (if present; else the protocol assertions go in `tests/test_console_agent.py`)

**Interfaces:**
- Produces:
  - `ListDesignsCommand(kind="instrument")`, `GetDesignCommand(state, name, kind="instrument")`, `SaveDesignCommand(name, text, kind="instrument")`, `PublishDesignCommand(name, kind="instrument")`, `CloneDesignCommand(source_state, source_name, new_name, kind="instrument")`; `parse_command` reads an optional `"kind"` and refuses anything not in `("instrument", "room")` with `ValueError`.
  - `design_row(entry) -> {"name", "state", "error", "kind"}`; `design_event(name, state, text, errors, kind)` adds `"kind"`; `designs_listed_event` / `designs_changed_event` unchanged in shape (rows carry kind).
  - `ConsoleAgent(..., catalog_root=None, rooms_root=None, ...)`; `_design_rows()` returns instrument rows followed by room rows (each with `kind`), so `snapshot.designs` and both list events carry both kinds; `_root_for(kind)` maps kind to root; room-kind commands answer `error_event(name, "no rooms catalog")` when `rooms_root` is None; room parsing uses `self.terrarium.config.instruments` when a Terrarium is wired, else `load_catalog(self.catalog_root).published` when `catalog_root` is set, else `{}`.
  - `harness/terrarium_boot.py` passes `rooms_root=(terrarium_config.room_roots[0] if terrarium_config.room_roots else None)`.
- Consumes: Task 1's kind-aware catalog API.

- [ ] **Step 1: Write the failing tests**

In `tests/test_console_agent.py` (read `_server_with_agent` near line 115; add a `rooms_root=None` passthrough parameter to it), add:

```python
ROOM_TOML = '''description = "Loft"
backends = ["devicelink"]
[[fixtures]]
name = "main"
color_order = "GRB"
instrument = "dev_strip_main"
  [[fixtures.blocks]]
  name = "main"
  start = 0
  count = 10
'''


def _roots(tmp_path):
    inst = tmp_path / "instruments"
    inst.mkdir()
    (inst / "dev_strip_main.toml").write_text(
        'description = "s"\ncapabilities = ["light.surface"]\naccepted_cues = ["midi"]\n')
    rooms = tmp_path / "rooms"
    rooms.mkdir()
    (rooms / "LOFT.toml").write_text(ROOM_TOML)
    return inst, rooms


def test_snapshot_designs_carry_both_kinds(tmp_path):
    inst, rooms = _roots(tmp_path)
    gs, srv, agent = _server_with_agent(catalog_root=inst, rooms_root=rooms)
    srv.connect("c1")
    agent.poll()
    _, msg = srv.sent[0]
    rows = {(r["kind"], r["name"]) for r in msg["designs"]}
    assert rows == {("instrument", "dev_strip_main"), ("room", "LOFT")}


def test_get_design_for_a_room_returns_its_text_with_kind(tmp_path):
    inst, rooms = _roots(tmp_path)
    gs, srv, agent = _server_with_agent(catalog_root=inst, rooms_root=rooms)
    srv.connect("c1")
    srv.deliver("c1", {"command": "get_design", "kind": "room",
                       "state": "published", "name": "LOFT"})
    agent.poll()
    design = [m for _c, m in srv.sent if m.get("event") == "design"][-1]
    assert design["kind"] == "room" and design["name"] == "LOFT"
    assert design["text"] == ROOM_TOML and design["errors"] == []


def test_save_design_for_a_room_writes_a_draft_and_reports_errors(tmp_path):
    inst, rooms = _roots(tmp_path)
    gs, srv, agent = _server_with_agent(catalog_root=inst, rooms_root=rooms)
    srv.connect("c1")
    srv.deliver("c1", {"command": "save_design", "kind": "room", "name": "LOFT",
                       "text": ROOM_TOML.replace("dev_strip_main", "ghost")})
    agent.poll()
    assert (rooms / "drafts" / "LOFT.toml").is_file()
    changed = [m for _c, m in srv.sent if m.get("event") == "designs_changed"][-1]
    draft = next(r for r in changed["designs"] if r["kind"] == "room" and r["state"] == "draft")
    assert "ghost" in draft["error"]


def test_room_design_command_without_a_rooms_root_is_an_error(tmp_path):
    inst, _rooms = _roots(tmp_path)
    gs, srv, agent = _server_with_agent(catalog_root=inst)
    srv.connect("c1")
    srv.deliver("c1", {"command": "list_designs", "kind": "room"})
    agent.poll()
    errors = [m for _c, m in srv.sent if m.get("event") == "error"]
    assert errors and errors[-1]["message"].startswith("no rooms catalog")


def test_unknown_design_kind_is_refused():
    with pytest.raises(ValueError, match="kind"):
        protocol.parse_command({"command": "list_designs", "kind": "venue"})


def test_instrument_design_commands_default_kind(tmp_path):
    inst, rooms = _roots(tmp_path)
    gs, srv, agent = _server_with_agent(catalog_root=inst, rooms_root=rooms)
    srv.connect("c1")
    srv.deliver("c1", {"command": "get_design", "state": "published",
                       "name": "dev_strip_main"})
    agent.poll()
    design = [m for _c, m in srv.sent if m.get("event") == "design"][-1]
    assert design["kind"] == "instrument"
```

Check the error event's key name in `protocol.error_event` (it may be `"message"` or `"reason"`) and match it. In `tests/test_terrarium_boot.py`, extend `test_main_wires_the_shipped_instrument_catalog_root_into_the_console_agent` (line ~2462) to also capture `console_agent.rooms_root` and assert its `.name == "rooms"`.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py tests/test_terrarium_boot.py -q -k "design or kind or catalog_root"`
Expected: FAIL (`rooms_root` unexpected kwarg; rows lack `kind`).

- [ ] **Step 3: Implement**

`console/protocol.py`: add `kind: str = "instrument"` to the five command dataclasses; in `parse_command` add a helper:

```python
def _design_kind(msg: dict) -> str:
    kind = msg.get("kind", "instrument")
    if kind not in ("instrument", "room"):
        raise ValueError("design commands take 'kind' of 'instrument' or 'room'")
    return kind
```

and pass `kind=_design_kind(msg)` into each constructor. `design_row` adds `"kind": entry.kind`; `design_event(name, state, text, errors, kind="instrument")` adds `"kind": kind`.

`console/agent.py`: constructor takes `rooms_root=None` (stored on `self.rooms_root`); add

```python
    def _root_for(self, kind: str):
        return self.catalog_root if kind == "instrument" else self.rooms_root

    def _instruments_for_rooms(self) -> dict:
        """The instrument set a room file's fixtures resolve against: the
        wired Terrarium's config when there is one, else the published
        instrument catalog, else nothing."""
        if self.terrarium is not None:
            return dict(self.terrarium.config.instruments)
        if self.catalog_root is not None:
            from control.catalog import load_catalog
            return load_catalog(self.catalog_root).published
        return {}

    def _design_rows(self) -> list:
        from control.catalog import load_catalog
        rows = []
        if self.catalog_root is not None:
            cat = load_catalog(self.catalog_root)
            rows += [protocol.design_row(e) for e in sorted(
                cat.entries.values(), key=lambda e: (e.name, e.state))]
        if self.rooms_root is not None:
            cat = load_catalog(self.rooms_root, kind="room",
                               instruments=self._instruments_for_rooms())
            rows += [protocol.design_row(e) for e in sorted(
                cat.entries.values(), key=lambda e: (e.name, e.state))]
        return rows
```

`_handle_design_command`: `root = self._root_for(command.kind)`; if None, `error_event(name, "no instrument catalog" if command.kind == "instrument" else "no rooms catalog")`; `instruments = self._instruments_for_rooms() if command.kind == "room" else None`; pass `kind=command.kind` and `instruments=instruments` into `load_catalog`, `save_draft`, `publish_entry`, and `kind=command.kind` into `clone_entry`; `design_event(..., kind=command.kind)`. The snapshot's `designs=self._design_rows() if (self.catalog_root or self.rooms_root) else []`. A published room catalog file that fails to parse raises `TerrariumConfigError` from `load_catalog`; wrap `_design_rows`'s room half in try/except that logs and returns the instrument rows plus one synthetic row `{"name": "<rooms catalog>", "state": "published", "kind": "room", "error": str(exc)}` so the Console shows the fault rather than losing the whole panel.

`harness/terrarium_boot.py`: alongside `catalog_root`, compute `rooms_root` from `terrarium_config.room_roots` and pass `rooms_root=rooms_root` to `ConsoleAgent`.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add console/protocol.py console/agent.py harness/terrarium_boot.py tests/test_console_agent.py tests/test_terrarium_boot.py
git commit -m "feat(console): design commands and rows carry kind; rooms catalog wired as rooms_root"
```

---

### Task 5: Design tab Rooms list and the fixture-order form

**Files:**
- Modify: `console/static/index.html`, `console/static/design.js`, `console/static/toml_edit.js`, `console/static/design_forms.js`
- Test: `tests/js/toml_edit.test.js`, `tests/js/design_panel.test.js`, `tests/js/design_forms.test.js` (or a new `tests/js/design_forms_rooms.test.js` following the existing `design_forms_*.test.js` split; check how `tests/test_console_js.py` discovers files, it likely globs `tests/js/*.test.js`)

**Interfaces:**
- Produces:
  - `toml_edit.js`: `listFixtures(text) -> [{name, instrument, start, end}]` (one per top-level `[[fixtures]]` block, in file order, using `splitBlocks`; `instrument` read with `getScalar` semantics from the block's own lines), `moveFixture(text, index, delta) -> text` (swaps the block at `index` with the one at `index + delta`, each block carrying its indented `[[fixtures.blocks]]` / `[[fixtures.zones]]` children; returns text unchanged when out of range), `setFixtureInstrument(text, index, name) -> text` (rewrites that block's `instrument = "..."` line).
  - `design.js`: `renderDesigns(listEl, designs, onSelect, kind="instrument")` filters rows to `kind` (a row without `kind` counts as instrument); `render()` paints `#designList` with instruments and `#roomDesignList` with rooms; `current = {name, state, kind}`; `getSelection()` returns it; every command sent includes `kind: current.kind`; `openDesign(msg)` records `msg.kind || "instrument"`.
  - `design_forms.js`: `rebuild(text, kind="instrument")`; for `kind === "room"` the panel shows only a "Fixtures" section: one row per fixture with its name, an instrument `<select>` (options: the published instrument names from the last `designs` rows, `data-form-key="fixture:<i>:instrument"`), and Up/Down buttons (`data-form-key="fixture:<i>:up"` / `":down"`, disabled at the ends) wired through `applyEdit` with `moveFixture` / `setFixtureInstrument`; `initForms` keeps the last designs rows from `snapshot`/`designs_listed`/`designs_changed` to populate the picker, and passes `m.kind` from the `design` event into `rebuild`.
  - `index.html`: `<h4>Instruments</h4><div id="designList"></div><h4>Rooms</h4><div id="roomDesignList"></div>` inside `#designPanel`, above `#formsPanel`.
- Consumes: Task 4's `kind` on rows, commands, and the `design` event.

- [ ] **Step 1: Write the failing JS tests**

Append to `tests/js/toml_edit.test.js` (inside its async IIFE, after the existing assertions; it imports `toml_edit.js` dynamically, follow the file's pattern):

```javascript
  // -- fixtures: listFixtures / moveFixture / setFixtureInstrument --------
  const ROOM = `description = "Two strips"
backends = ["devicelink"]

[[fixtures]]
name = "main"
color_order = "GRB"
instrument = "dev_strip_main"
  [[fixtures.blocks]]
  name = "main"
  start = 0
  count = 60

[[fixtures]]
name = "accent"
color_order = "GRB"
instrument = "dev_strip_accent"
  [[fixtures.blocks]]
  name = "accent"
  start = 0
  count = 30
`;
  const fx = te.listFixtures(ROOM);
  assert.deepStrictEqual(fx.map((f) => [f.name, f.instrument]),
    [["main", "dev_strip_main"], ["accent", "dev_strip_accent"]]);

  const swapped = te.moveFixture(ROOM, 1, -1);
  assert.deepStrictEqual(te.listFixtures(swapped).map((f) => f.name), ["accent", "main"]);
  // children travel with their fixture: accent's block still follows accent's header
  const accentIdx = swapped.indexOf('name = "accent"');
  const accentBlockIdx = swapped.indexOf("[[fixtures.blocks]]", accentIdx);
  const mainIdx = swapped.indexOf('name = "main"\ncolor_order');
  assert.ok(accentIdx < accentBlockIdx && accentBlockIdx < mainIdx, "accent's children precede main");
  assert.ok(swapped.startsWith('description = "Two strips"'), "top-level scalars untouched");
  assert.strictEqual(te.moveFixture(ROOM, 0, -1), ROOM, "out of range is a no-op");
  assert.strictEqual(te.moveFixture(ROOM, 1, 1), ROOM, "out of range is a no-op");

  const repicked = te.setFixtureInstrument(ROOM, 1, "venue_array");
  assert.deepStrictEqual(te.listFixtures(repicked).map((f) => f.instrument),
    ["dev_strip_main", "venue_array"]);
  assert.strictEqual(te.setFixtureInstrument(ROOM, 5, "x"), ROOM, "unknown index is a no-op");
```

Use whatever the file names its imported module (`te` above stands for that binding; read the file).

Append to `tests/js/design_panel.test.js` (inside its IIFE, after the existing `openDesign` assertions):

```javascript
  // -- rooms list renders separately and carries kind on every command ----
  const BOTH = [
    { name: "tuneshroom", state: "published", error: null, kind: "instrument" },
    { name: "TEST", state: "published", error: null, kind: "room" },
  ];
  send({ event: "designs_changed", designs: BOTH });
  assert.ok(byId.get("designList").innerHTML.includes("tuneshroom"));
  assert.ok(!byId.get("designList").innerHTML.includes("TEST"), "rooms stay out of the instrument list");
  assert.ok(byId.get("roomDesignList").innerHTML.includes("TEST [published]"));

  sock.sent.length = 0;
  byId.get("roomDesignList").children[0].onclick();
  const get = JSON.parse(sock.sent.at(-1));
  assert.strictEqual(get.command, "get_design");
  assert.strictEqual(get.kind, "room");
  assert.strictEqual(get.name, "TEST");

  design.openDesign({ name: "TEST", state: "published", kind: "room", text: "x = 1", errors: [] });
  assert.deepStrictEqual(design.getSelection(), { name: "TEST", state: "published", kind: "room" });
  sock.sent.length = 0;
  byId.get("designSave").onclick();
  assert.strictEqual(JSON.parse(sock.sent.at(-1)).kind, "room");

  // a row without kind is an instrument (older server)
  design.openDesign({ name: "glowcap", state: "draft", text: "x = 1", errors: [] });
  assert.strictEqual(design.getSelection().kind, "instrument");
```

Check how the existing test reads sent frames (`sock.sent` and JSON shape) and match it.

Create `tests/js/design_forms_rooms.test.js` modelled on `tests/js/design_forms_ambient.test.js`'s harness (read it first; copy its imports, `init` calls, and how it seeds `designText` and dispatches the `design` event):

```javascript
"use strict";
const assert = require("node:assert");
const { byId, FakeSocket } = require("./_dom_stub.js");

const ROOM = `description = "Two strips"
backends = ["devicelink"]

[[fixtures]]
name = "main"
color_order = "GRB"
instrument = "dev_strip_main"
  [[fixtures.blocks]]
  name = "main"
  start = 0
  count = 60

[[fixtures]]
name = "accent"
color_order = "GRB"
instrument = "dev_strip_accent"
  [[fixtures.blocks]]
  name = "accent"
  start = 0
  count = 30
`;

(async () => {
  // (same boot sequence as design_forms_ambient.test.js: import wire, design, design_forms; init(); initForms(); connect FakeSocket; open it)
  send({ event: "snapshot", designs: [
    { name: "dev_strip_main", state: "published", error: null, kind: "instrument" },
    { name: "dev_strip_accent", state: "published", error: null, kind: "instrument" },
    { name: "venue_array", state: "published", error: null, kind: "instrument" },
    { name: "sketch", state: "draft", error: null, kind: "instrument" },
    { name: "TEST", state: "published", error: null, kind: "room" },
  ], design_vocab: { capabilities: [], cue_kinds: [] } });
  send({ event: "design", name: "TEST", state: "published", kind: "room", text: ROOM, errors: [] });

  const panel = byId.get("formsPanel");
  assert.ok(panel.innerHTML.includes("Fixtures"));
  assert.ok(!panel.innerHTML.includes("Description"), "room form has no instrument identity field");
  const up1 = panel.querySelector('[data-form-key="fixture:1:up"]');
  const down0 = panel.querySelector('[data-form-key="fixture:0:down"]');
  const up0 = panel.querySelector('[data-form-key="fixture:0:up"]');
  assert.ok(up0.disabled, "first fixture cannot move up");
  up1.onclick();
  let text = byId.get("designText").value;
  assert.ok(text.indexOf('name = "accent"') < text.indexOf('name = "main"\ncolor_order'));

  const pick = panel.querySelector('[data-form-key="fixture:0:instrument"]');
  const options = Array.from(pick.children).map((o) => o.value);
  assert.deepStrictEqual(options, ["dev_strip_accent", "dev_strip_main", "venue_array"],
    "picker offers published instruments only, sorted");
  pick.value = "venue_array";
  pick.onchange();
  text = byId.get("designText").value;
  assert.ok(text.includes('instrument = "venue_array"'));
  console.log("design_forms_rooms.test.js ok");
})();
```

If the DOM stub lacks `querySelector`, use the file's `findByFormKey`-style lookup or iterate `panel` descendants as the sibling tests do.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_js.py -q`
Expected: FAIL (`listFixtures` undefined; `roomDesignList` empty; room form absent).

- [ ] **Step 3: Implement**

`console/static/index.html`: replace `<div id="designList"></div>` with

```html
        <h4>Instruments</h4>
        <div id="designList"></div>
        <h4>Rooms</h4>
        <div id="roomDesignList"></div>
```

`console/static/toml_edit.js`, appended (pure, line-based, following the file's conventions):

```javascript
// -- fixtures ([[fixtures]] blocks in a room file) -------------------------

function fixtureBlocks(text) {
  return splitBlocks(text).filter((b) => b.header === "[[fixtures]]");
}

export function listFixtures(text) {
  const lines = text.split("\n");
  return fixtureBlocks(text).map((b) => {
    let instrument = null;
    for (let i = b.start + 1; i < b.end; i++) {
      const m = lines[i].match(/^\s*instrument\s*=\s*"([^"]*)"\s*$/);
      if (m) { instrument = m[1]; break; }
    }
    return { name: b.name, instrument, start: b.start, end: b.end };
  });
}

export function moveFixture(text, index, delta) {
  const blocks = fixtureBlocks(text);
  const j = index + delta;
  if (index < 0 || index >= blocks.length || j < 0 || j >= blocks.length || delta === 0) return text;
  const lines = text.split("\n");
  const a = blocks[Math.min(index, j)];
  const b = blocks[Math.max(index, j)];
  const before = lines.slice(0, a.start);
  const aLines = lines.slice(a.start, a.end);
  const between = lines.slice(a.end, b.start);
  const bLines = lines.slice(b.start, b.end);
  const after = lines.slice(b.end);
  return [...before, ...bLines, ...between, ...aLines, ...after].join("\n");
}

export function setFixtureInstrument(text, index, name) {
  const blocks = fixtureBlocks(text);
  if (index < 0 || index >= blocks.length) return text;
  const lines = text.split("\n");
  for (let i = blocks[index].start + 1; i < blocks[index].end; i++) {
    if (/^\s*instrument\s*=/.test(lines[i])) {
      lines[i] = lines[i].replace(/=.*$/, `= "${name}"`);
      return lines.join("\n");
    }
  }
  return text;
}
```

`splitBlocks` already keeps indented `[[fixtures.blocks]]` / `[[fixtures.zones]]` lines inside the enclosing `[[fixtures]]` block (indented headers do not match `TOP_HEADER_RE`), which is why the migration in Task 3 indents them. Note a trailing blank line belongs to the preceding block; swapping keeps each block's own trailing blank, so the result stays valid TOML.

`console/static/design.js`: `renderDesigns(listEl, designs, onSelect, kind = "instrument")` filters `designs.filter((d) => (d.kind || "instrument") === kind)`; `render()` paints both lists and marks the selected row in whichever list holds `current.kind`; `onRowSelect(design)` sends `{ kind: design.kind || "instrument", state, name }`; `openDesign` sets `current = { name, state, kind: msg.kind || "instrument" }`; Save/Publish/Clone include `kind: current.kind`. Update the header comment.

`console/static/design_forms.js`: keep `lastDesigns` (updated from `snapshot`/`designs_listed`/`designs_changed`); `rebuild(text, kind = "instrument")`: when `kind === "room"`, clear the panel and build only the Fixtures section:

```javascript
function buildFixtureSection(panel, text) {
  panel.appendChild(mk("h4", null, "Fixtures"));
  const fixtures = listFixtures(text);
  if (!fixtures.length) {
    panel.appendChild(mk("p", "muted", "no [[fixtures]] declared (add via raw TOML)"));
    return;
  }
  const published = lastDesigns
    .filter((d) => (d.kind || "instrument") === "instrument" && d.state === "published")
    .map((d) => d.name).sort();
  fixtures.forEach((fx, i) => {
    const row = mk("div", "fixture-row");
    row.appendChild(mk("span", "name", fx.name || `(fixture ${i})`));
    const pick = document.createElement("select");
    pick.setAttribute("data-form-key", `fixture:${i}:instrument`);
    for (const name of published) {
      const opt = document.createElement("option");
      opt.value = name; opt.textContent = name;
      if (name === fx.instrument) opt.selected = true;
      pick.appendChild(opt);
    }
    pick.onchange = () => applyEdit((t) => setFixtureInstrument(t, i, pick.value));
    row.appendChild(pick);
    const up = mk("button", "btn", "Up");
    up.setAttribute("data-form-key", `fixture:${i}:up`);
    up.disabled = i === 0;
    up.onclick = () => applyEdit((t) => moveFixture(t, i, -1), { restoreFocus: false });
    const down = mk("button", "btn", "Down");
    down.setAttribute("data-form-key", `fixture:${i}:down`);
    down.disabled = i === fixtures.length - 1;
    down.onclick = () => applyEdit((t) => moveFixture(t, i, 1), { restoreFocus: false });
    row.appendChild(up);
    row.appendChild(down);
    panel.appendChild(row);
  });
  panel.appendChild(mk("p", "muted", "Order applies at the next Room load."));
}
```

Import `listFixtures`, `moveFixture`, `setFixtureInstrument` from `toml_edit.js`. `applyEdit` must call `rebuild(newText, currentKind)`: track `currentKind` in a module variable set by the `design` event handler (`wire.on("design", (m) => { currentKind = m.kind || "instrument"; rebuild(m.text, currentKind); })`) and used by the textarea `oninput` debounce. Moving a fixture shifts indices, so the Up/Down buttons pass `restoreFocus: false` (the file's own rule for index-shifting actions). Update the module header comment.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS, including `tests/test_console_js.py` with the new JS test file discovered.

- [ ] **Step 5: Commit**

```bash
git add console/static/index.html console/static/design.js console/static/toml_edit.js console/static/design_forms.js tests/js/toml_edit.test.js tests/js/design_panel.test.js tests/js/design_forms_rooms.test.js
git commit -m "feat(console): Rooms list in the Design tab with a fixture-order form over the raw TOML editor"
```

---

### Task 6: Documentation sync

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (new dated section; `**(Superseded 2026-09-01: ...)**` note on the 2026-08-27 "config-defined rooms" entry's `[rooms.<NAME>]` bullet; the "Not yet built / deferred" list), `docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md` (section 12 Status: Plan 2 landed, any deviation), `docs/superpowers/handoffs/2026-09-01-rooms-catalog-and-o2-time-handoff.md` (Plan 2 done; Plan 3 remains; live checklist still unrun), `README.md` and `docs/handoff.html` only if they describe `[rooms.<NAME>]` in `terrarium.toml` (grep first)

- [ ] **Step 1: Write the deep-dive section**

Title `### Rooms catalog, TEST/DEMO migration, Design tab Room editor (2026-09-01)`, in the doc's style (Design: link to the spec, bold-lead-in bullets, closing `**Test baseline for this slice:**` with the real count from a fresh `.venv/bin/python -m pytest tests -q`). Cover: `rooms/<NAME>.toml` + `rooms/drafts/`, `[terrarium] room_paths` default, inline still parses, collision rule, at-least-one rule; the migration and the profile-equality pin; the catalog `kind` generalization and the `instruments` dependency of room parsing; design commands/events/rows carrying `kind`, `rooms_root`, the synthetic error row for a broken published room file; the Design tab's Rooms list and fixture-order form (Up/Down, instrument picker limited to published instruments; blocks/zones raw TOML; applies at next Room load); the `--room` help change; what is NOT done (structured blocks/zones forms, `fixture_controllers` consumer, Plan 3).

- [ ] **Step 2: Verify and commit**

`grep -n $'\xe2\x80\x94'` over the added lines must print nothing. Run the suite once and record the count.

```bash
git add docs/MM_TERRARIUM.md docs/superpowers/specs/2026-09-01-per-fixture-light-sessions-design.md docs/superpowers/handoffs/2026-09-01-rooms-catalog-and-o2-time-handoff.md README.md docs/handoff.html
git commit -m "docs: deep-dive sync for the rooms catalog and Design tab Room editor"
```

(Omit `README.md` / `docs/handoff.html` from `git add` if untouched.)

---

## Live verification (after Task 6, before merge; RUN ON: MYCOLOGICAL)

1. Boot with `--config terrarium.toml --room TEST` (rooms now come from `rooms/TEST.toml`): loads exactly as before.
2. Console Design tab: Rooms list shows TEST and DEMO; select TEST; the fixture form shows main then accent with their instruments; Down on main; Save writes `rooms/drafts/TEST.toml`; Publish replaces `rooms/TEST.toml`; reload the Room; the Console strips now list accent first.
3. Restore the order (Up, Save, Publish) so the shipped file is unchanged, or `git checkout rooms/TEST.toml`.
4. Load DEMO: unchanged.
