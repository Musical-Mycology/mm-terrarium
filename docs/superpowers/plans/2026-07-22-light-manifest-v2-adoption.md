# Light-Manifest v2 Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt the luxaeterna light-manifest v2 wire contract on `Role.light_manifest`, add the per-role `welcome` pair (light + audio halves), and surface the composed per-role config blob on `JoinResult` through Control's role-adoption flow.

**Architecture:** `Role.light_manifest` becomes a plain dict in the v2 wire shape (authored subset: instruments only); a new pure module `control/role_config.py` validates authored declarations at `load_bit` time and composes the `/ie<N>/role` config blob (provenance stamped, welcome light half folded) at grant time; `GameServer` gains `bit_name`, `Bit` gains `version`, and `JoinResult` gains `config`. Two riding fixes: the `load_bit` LOADING-wedge and the Console's isinstance name scan.

**Tech Stack:** Python 3.10+ (stdlib only), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-22-light-manifest-v2-adoption-design.md` (committed on this branch).

## Global Constraints

- Work in the worktree `/Users/chris/projects/mm-terrarium/.claude/worktrees/light-manifest-v2` (branch `claude/light-manifest-v2`). All paths below are relative to it.
- Run tests as `python3 -m pytest tests -q` from the worktree root (system python3 has pytest+websockets; there is no repo venv).
- Tests are plain functions only — `pytest.ini` disables class-based collection (`python_classes =`), so never write `Test*` pytest classes. Helper `Bit` subclasses inside test files are fine.
- No new runtime dependencies; stdlib only (`copy.deepcopy` is the only new import).
- Do NOT touch the luxaeterna repo — this plan is mm-terrarium only.
- Validation is shallow and exactly per spec: required keys + container types + the four forbidden composed keys. No instrument-name/param-domain validation (that vocabulary lives in luxaeterna's registry).
- Commit after every task with conventional-commit messages (`feat:`/`fix:`/`test:`/`docs:`).
- The suite must be green (103 pre-existing tests + new ones) at the end of every task.

---

### Task 1: Role schema — `light_manifest` dict + `welcome` field

**Files:**
- Modify: `control/roles.py`
- Test: `tests/test_roles.py`
- Modify: `tests/test_console_protocol.py` (the `role_view` shape assertion references the old `[]` default)

**Interfaces:**
- Produces: `Role.light_manifest: dict` (default `{}`), `Role.welcome: dict | None` (default `None`). Every later task relies on these exact field names and defaults.

- [ ] **Step 1: Update/add the failing tests**

In `tests/test_roles.py`, replace `test_role_has_empty_light_manifest_by_default` with:

```python
def test_role_light_manifest_defaults_to_empty_v2_dict():
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True)
    # v2 wire shape (see docs/superpowers/specs/
    # 2026-07-22-light-manifest-v2-adoption-design.md section 3): a dict,
    # empty by default -- parses device-side as "declares no light".
    assert role.light_manifest == {}


def test_role_welcome_defaults_to_none():
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True)
    assert role.welcome is None


def test_roles_do_not_share_light_manifest_instances():
    a = Role(name="a", role_class=RoleClass.SHARED, capacity=None, scored=True)
    b = Role(name="b", role_class=RoleClass.SHARED, capacity=None, scored=True)
    assert a.light_manifest is not b.light_manifest
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_roles.py -q`
Expected: FAIL — `assert [] == {}` and `AttributeError: 'Role' object has no attribute 'welcome'`

- [ ] **Step 3: Implement the schema change**

In `control/roles.py`, replace the `light_manifest` field (and its comment) with:

```python
    # This role's light declaration in the light-manifest v2 wire shape
    # (luxaeterna docs/superpowers/specs/2026-07-22-synth-session-lifecycle-
    # design.md section 9; adopted here per docs/superpowers/specs/
    # 2026-07-22-light-manifest-v2-adoption-design.md). Authored subset only:
    #   {"instruments": [{instrument, target, params?,
    #                     lanes?: [{source, dest, curve?}]}]}
    # welcome/bit_name/bit_version/role are composed into the outgoing blob
    # by Control at adoption time and are forbidden here (validated at Bit
    # load, control/role_config.py). {} parses device-side as "no light".
    # The Terrarium Console displays it; the composed blob, not this field,
    # reaches Lux Aeterna.
    light_manifest: dict = field(default_factory=dict)
    # The role's welcome ceremony, both halves declared in one place:
    #   {"light": {instrument, params?, duration?},
    #    "audio": {instrument, params?, duration?}}
    # light folds into the outgoing light_manifest blob (plays in LOADING
    # instead of sys:loaded); audio stays Control-side for the future Arco
    # cue path (no consumer yet; shape frozen so Bit authors declare both
    # together from day one).
    welcome: dict | None = None
```

- [ ] **Step 4: Update the Console shape assertion**

In `tests/test_console_protocol.py::test_role_view_shape`, change the expected `"light_manifest": []` to `"light_manifest": {}`.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests -q`
Expected: all pass (the old `test_role_has_empty_light_manifest_by_default` is gone; everything else green)

- [ ] **Step 6: Commit**

```bash
git add control/roles.py tests/test_roles.py tests/test_console_protocol.py
git commit -m "feat(roles): adopt light-manifest v2 wire shape + welcome pair on Role"
```

---

### Task 2: `role_config.validate_role_declarations`

**Files:**
- Create: `control/role_config.py`
- Test: `tests/test_role_config.py` (new)

**Interfaces:**
- Consumes: `Role`, `RoleTable` from `control.roles` (Task 1 field shapes).
- Produces: `validate_role_declarations(role_table: RoleTable) -> None`, raising `ValueError` with located messages. Task 4 calls it inside `load_bit`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_role_config.py`:

```python
import pytest

from control.role_config import validate_role_declarations
from control.roles import Role, RoleClass, RoleTable


def make_role(name="player", **kwargs):
    return Role(name=name, role_class=RoleClass.SHARED, capacity=None,
                scored=True, **kwargs)


def make_table(*roles):
    return RoleTable(roles={r.name: r for r in roles}, node_map={})


GOOD_MANIFEST = {
    "instruments": [
        {"instrument": "bloom", "target": "primary",
         "params": {"base_hue": 0.33},
         "lanes": [{"source": "note", "dest": "trigger"},
                   {"source": "cc:74", "dest": "base_hue", "curve": "linear"}]},
    ],
}

GOOD_WELCOME = {
    "light": {"instrument": "bloom", "params": {"base_hue": 0.33},
              "duration": 1.5},
    "audio": {"instrument": "chime", "duration": 1.5},
}


def test_validate_accepts_empty_defaults():
    validate_role_declarations(make_table(make_role()))


def test_validate_accepts_full_declaration():
    role = make_role(light_manifest=GOOD_MANIFEST, welcome=GOOD_WELCOME)
    validate_role_declarations(make_table(role))


def test_validate_rejects_non_dict_manifest():
    role = make_role(light_manifest=[])
    with pytest.raises(ValueError, match=r"role 'player' light_manifest: must be a dict"):
        validate_role_declarations(make_table(role))


@pytest.mark.parametrize("key", ["welcome", "bit_name", "bit_version", "role"])
def test_validate_rejects_composed_keys_in_authored_manifest(key):
    role = make_role(light_manifest={"instruments": [], key: "x"})
    with pytest.raises(ValueError,
                       match=rf"role 'player' light_manifest: field '{key}' is "
                             r"composed by Control at adoption time"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_non_list_instruments():
    role = make_role(light_manifest={"instruments": {}})
    with pytest.raises(ValueError,
                       match=r"role 'player' light_manifest: 'instruments' must be a list"):
        validate_role_declarations(make_table(role))


@pytest.mark.parametrize("missing", ["instrument", "target"])
def test_validate_rejects_instrument_decl_missing_required_field(missing):
    decl = {"instrument": "bloom", "target": "primary"}
    del decl[missing]
    role = make_role(light_manifest={"instruments": [decl]})
    with pytest.raises(ValueError,
                       match=rf"role 'player' light_manifest instruments\[0\]: "
                             rf"missing required field '{missing}'"):
        validate_role_declarations(make_table(role))


@pytest.mark.parametrize("missing", ["source", "dest"])
def test_validate_rejects_lane_missing_required_field(missing):
    lane = {"source": "note", "dest": "trigger"}
    del lane[missing]
    role = make_role(light_manifest={
        "instruments": [{"instrument": "bloom", "target": "primary",
                         "lanes": [lane]}]})
    with pytest.raises(ValueError,
                       match=rf"role 'player' light_manifest instruments\[0\] "
                             rf"lanes\[0\]: missing required field '{missing}'"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_welcome_without_halves():
    role = make_role(welcome={})
    with pytest.raises(ValueError,
                       match=r"role 'player' welcome: must declare at least one "
                             r"of 'light'/'audio'"):
        validate_role_declarations(make_table(role))


@pytest.mark.parametrize("half", ["light", "audio"])
def test_validate_rejects_welcome_half_without_instrument(half):
    role = make_role(welcome={half: {"duration": 1.0}})
    with pytest.raises(ValueError,
                       match=rf"role 'player' welcome {half!r}: missing required "
                             r"field 'instrument'"):
        validate_role_declarations(make_table(role))


def test_validate_rejects_non_dict_welcome():
    role = make_role(welcome="hello")
    with pytest.raises(ValueError, match=r"role 'player' welcome: must be a dict"):
        validate_role_declarations(make_table(role))


def test_validate_names_the_failing_role():
    bad = Role(name="jammer", role_class=RoleClass.JAM, capacity=None,
               scored=False, light_manifest=[])
    with pytest.raises(ValueError, match=r"role 'jammer'"):
        validate_role_declarations(make_table(make_role(), bad))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_role_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control.role_config'`

- [ ] **Step 3: Implement the validator**

Create `control/role_config.py`:

```python
"""Per-role config: validation of authored Role light/welcome declarations
(at Bit load) and composition of the /ie<N>/role config blob (at role
adoption). See docs/superpowers/specs/2026-07-22-light-manifest-v2-adoption-
design.md sections 5-6. Pure functions, no engine imports, mirroring the
protocol-module discipline. The wire contract is luxaeterna's light-manifest
v2 (LightManifest.from_dict); validation here is deliberately shallow --
instrument names and params belong to luxaeterna's installation-overridable
registry, which Control cannot see.
"""

from control.roles import Role, RoleTable

# Keys Control composes into the outgoing blob at adoption time; authoring
# any of them on a Role is a contract violation caught at Bit load.
_COMPOSED_KEYS = ("welcome", "bit_name", "bit_version", "role")
_WELCOME_HALVES = ("light", "audio")


def validate_role_declarations(role_table: RoleTable) -> None:
    """Shallow structural validation of every role's light_manifest and
    welcome against the authored subset of the v2 wire shape. Raises
    ValueError with a message locating the offending field."""
    for role in role_table.roles.values():
        _validate_light_manifest(role)
        _validate_welcome(role)


def _validate_light_manifest(role: Role) -> None:
    where = f"role {role.name!r} light_manifest"
    manifest = role.light_manifest
    if not isinstance(manifest, dict):
        raise ValueError(
            f"{where}: must be a dict in the v2 wire shape, "
            f"got {type(manifest).__name__}")
    for key in _COMPOSED_KEYS:
        if key in manifest:
            raise ValueError(
                f"{where}: field {key!r} is composed by Control at adoption "
                f"time; declare a welcome via Role.welcome")
    instruments = manifest.get("instruments", [])
    if not isinstance(instruments, list):
        raise ValueError(f"{where}: 'instruments' must be a list")
    for idx, decl in enumerate(instruments):
        decl_where = f"{where} instruments[{idx}]"
        if not isinstance(decl, dict):
            raise ValueError(f"{decl_where}: must be a dict")
        for req in ("instrument", "target"):
            if req not in decl:
                raise ValueError(
                    f"{decl_where}: missing required field {req!r}")
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


def _validate_welcome(role: Role) -> None:
    welcome = role.welcome
    if welcome is None:
        return
    where = f"role {role.name!r} welcome"
    if not isinstance(welcome, dict):
        raise ValueError(f"{where}: must be a dict")
    halves = [h for h in _WELCOME_HALVES if h in welcome]
    if not halves:
        raise ValueError(
            f"{where}: must declare at least one of 'light'/'audio'")
    for half in halves:
        half_where = f"{where} {half!r}"
        decl = welcome[half]
        if not isinstance(decl, dict):
            raise ValueError(f"{half_where}: must be a dict")
        if "instrument" not in decl:
            raise ValueError(
                f"{half_where}: missing required field 'instrument'")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_role_config.py -q`
Expected: all pass

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m pytest tests -q` — expected: all pass.

```bash
git add control/role_config.py tests/test_role_config.py
git commit -m "feat(role-config): shallow load-time validation of light/welcome declarations"
```

---

### Task 3: `role_config.compose_role_config`

**Files:**
- Modify: `control/role_config.py`
- Test: `tests/test_role_config.py`

**Interfaces:**
- Consumes: Task 1 field shapes; module from Task 2.
- Produces: `compose_role_config(bit_name: str, bit_version: str, role: Role) -> dict` returning `{"role", "class", "scored", "light_manifest"}` with provenance stamped and the welcome light half folded. Task 5 calls it from `GameServer.join`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_role_config.py`:

```python
from control.role_config import compose_role_config


def test_compose_stamps_provenance_and_folds_welcome_light_half():
    role = make_role(light_manifest=GOOD_MANIFEST, welcome=GOOD_WELCOME)
    config = compose_role_config("test_bit", "0.9", role)
    assert config == {
        "role": "player",
        "class": "SHARED",
        "scored": True,
        "light_manifest": {
            "instruments": GOOD_MANIFEST["instruments"],
            "bit_name": "test_bit",
            "bit_version": "0.9",
            "role": "player",
            "welcome": GOOD_WELCOME["light"],
        },
    }


def test_compose_with_empty_defaults_ships_bare_provenance():
    config = compose_role_config("test_bit", "", make_role())
    assert config == {
        "role": "player",
        "class": "SHARED",
        "scored": True,
        "light_manifest": {"bit_name": "test_bit", "bit_version": "",
                           "role": "player"},
    }
    # No welcome declared -> no welcome key; the device falls back to
    # sys:loaded (luxaeterna lifecycle spec section 5).
    assert "welcome" not in config["light_manifest"]


def test_compose_audio_only_welcome_ships_no_welcome_key():
    role = make_role(welcome={"audio": {"instrument": "chime"}})
    config = compose_role_config("test_bit", "", role)
    assert "welcome" not in config["light_manifest"]


def test_compose_light_only_welcome_still_folds():
    role = make_role(welcome={"light": {"instrument": "bloom"}})
    config = compose_role_config("test_bit", "", role)
    assert config["light_manifest"]["welcome"] == {"instrument": "bloom"}


def test_compose_unique_role_class_and_scored_flag():
    role = Role(name="conductor", role_class=RoleClass.UNIQUE, capacity=1,
                scored=False)
    config = compose_role_config("test_bit", "", role)
    assert config["class"] == "UNIQUE"
    assert config["scored"] is False
    assert config["role"] == "conductor"


def test_compose_never_aliases_the_authored_declaration():
    role = make_role(light_manifest={"instruments": [
        {"instrument": "bloom", "target": "primary", "params": {"a": 1}}]},
        welcome={"light": {"instrument": "bloom", "params": {"b": 2}}})
    config = compose_role_config("test_bit", "", role)
    config["light_manifest"]["instruments"][0]["params"]["a"] = 99
    config["light_manifest"]["welcome"]["params"]["b"] = 99
    assert role.light_manifest["instruments"][0]["params"]["a"] == 1
    assert role.welcome["light"]["params"]["b"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_role_config.py -q`
Expected: FAIL with `ImportError: cannot import name 'compose_role_config'`

- [ ] **Step 3: Implement composition**

Add `from copy import deepcopy` at the top of `control/role_config.py` (above the `control.roles` import), then append:

```python
def compose_role_config(bit_name: str, bit_version: str, role: Role) -> dict:
    """The per-role config blob shipped in /ie<N>/role at adoption time
    (docs/control-gameserver-design.md, player flow step 3). Deep-copied so
    transport/Console consumers can never alias the Bit's declaration. The
    welcome audio half is deliberately absent: it never ships to the device;
    the future Arco cue path reads it off Role.welcome."""
    light = deepcopy(role.light_manifest)
    light["bit_name"] = bit_name
    light["bit_version"] = bit_version
    light["role"] = role.name
    if role.welcome and "light" in role.welcome:
        light["welcome"] = deepcopy(role.welcome["light"])
    return {
        "role": role.name,
        "class": role.role_class.name,
        "scored": role.scored,
        "light_manifest": light,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_role_config.py -q`
Expected: all pass

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m pytest tests -q` — expected: all pass.

```bash
git add control/role_config.py tests/test_role_config.py
git commit -m "feat(role-config): compose per-role /ie<N>/role config blob"
```

---

### Task 4: Bit identity + `load_bit` guarded region

**Files:**
- Modify: `control/bit.py`
- Modify: `control/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `validate_role_declarations` (Task 2).
- Produces: `Bit.version: str = ""` (class attribute), `GameServer.bit_name: str | None` (set in `load_bit`, cleared in `_unload`). Task 5 reads both; Task 6 reads `bit_name`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_engine.py` (module level, near the other fixture bits; reuse the existing `REGISTRY`/`make_server` pattern — add the two new bits to `REGISTRY`):

```python
class RaisingRoleTableBit(Bit):
    @property
    def role_table(self) -> RoleTable:
        raise RuntimeError("role table exploded")


class BadManifestBit(Bit):
    @property
    def role_table(self) -> RoleTable:
        bad = Role(name="player", role_class=RoleClass.SHARED, capacity=None,
                   scored=True, light_manifest=["not", "a", "dict"])
        return RoleTable(roles={"player": bad}, node_map={"N": ["player"]})
```

(Import `Bit`, `Role`, `RoleClass`, `RoleTable` at the top of the file if not already imported: `from control.bit import Bit` / `from control.roles import Role, RoleClass, RoleTable`.)

Register them in the existing `REGISTRY` dict:

```python
    "raising_role_table_bit": RaisingRoleTableBit,
    "bad_manifest_bit": BadManifestBit,
```

Then add the tests:

```python
def test_load_bit_records_bit_name_and_clears_it_on_unload():
    server = make_server()
    assert server.bit_name is None
    server.load_bit("test_bit")
    assert server.bit_name == "test_bit"
    server.abort()
    assert server.bit_name is None


def test_bit_version_defaults_to_empty_string():
    assert TestBit().version == ""


def test_load_bit_raising_role_table_fails_cleanly_to_idle():
    server = make_server()
    with pytest.raises(BitLoadError):
        server.load_bit("raising_role_table_bit")
    assert server.state == State.IDLE
    assert server.bit is None
    assert server.bit_name is None
    assert server.registration is None
    # regression: the engine must not be wedged -- a good load still works
    server.load_bit("test_bit")
    assert server.state == State.SETUP


def test_load_bit_invalid_manifest_fails_cleanly_to_idle():
    server = make_server()
    with pytest.raises(BitLoadError, match=r"role 'player' light_manifest"):
        server.load_bit("bad_manifest_bit")
    assert server.state == State.IDLE
    assert server.bit is None
    assert server.registration is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -q`
Expected: FAIL — `AttributeError: 'GameServer' object has no attribute 'bit_name'`, `AttributeError: ... 'version'`, and `RuntimeError: role table exploded` escaping `load_bit` (the wedge this task fixes)

- [ ] **Step 3: Implement**

`control/bit.py` — add to the `Bit` class body, right after the docstring:

```python
    # Bit identity for provenance stamping (light-manifest v2 bit_version).
    # The bit *name* is the registry key GameServer loaded it under -- not
    # an attribute here, so there is nothing for an author to keep in sync.
    version: str = ""
```

`control/engine.py`:

1. Add the import: `from control.role_config import validate_role_declarations`
2. In `__init__`, after `self.bit: Bit | None = None`, add:

```python
        # Registry key of the loaded Bit; provenance for /ie<N>/role blobs
        # and the Console. Set in load_bit, cleared in _unload.
        self.bit_name: str | None = None
```

3. Rewrite `load_bit` so everything that can fail sits inside the guarded region (today a raising `role_table` property escapes the handler and wedges the engine in LOADING):

```python
    def load_bit(self, name: str) -> None:
        if self.state != State.IDLE:
            raise InvalidTransition(
                f"load_bit requires IDLE, current state is {self.state}")
        self._set_state(State.LOADING)
        try:
            bit_cls = self.bit_registry[name]
            bit = bit_cls()
            role_table = bit.role_table
            validate_role_declarations(role_table)
            registration = RegistrationState(role_table)
        except Exception as exc:
            self._set_state(State.IDLE)
            raise BitLoadError(f"failed to load Bit {name!r}: {exc}") from exc
        self.bit = bit
        self.bit_name = name
        self.registration = registration
        self._set_state(State.LOADED)
        self._enter_setup()
```

4. In `_unload`, next to `self.bit = None`, add `self.bit_name = None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py -q`
Expected: all pass

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m pytest tests -q` — expected: all pass.

```bash
git add control/bit.py control/engine.py tests/test_engine.py
git commit -m "feat(engine): bit identity for provenance; fix load_bit exception wedge"
```

---

### Task 5: `JoinResult.config` + composition in `GameServer.join`

**Files:**
- Modify: `control/registration.py`
- Modify: `control/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `compose_role_config` (Task 3), `GameServer.bit_name` / `Bit.version` (Task 4).
- Produces: `JoinResult.config: dict | None` (default `None`; populated only on granted joins, by `GameServer.join`, never by `RegistrationState`). The future o2lite transport reads it to build `/ie<N>/role`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_engine.py` a fixture bit exercising manifest + welcome (module level, registered in `REGISTRY` as `"welcome_bit"`):

```python
WELCOME_LIGHT_MANIFEST = {
    "instruments": [
        {"instrument": "bloom", "target": "primary",
         "lanes": [{"source": "note", "dest": "trigger"}]},
    ],
}

WELCOME_PAIR = {
    "light": {"instrument": "bloom", "duration": 2.0},
    "audio": {"instrument": "chime", "duration": 2.0},
}


class WelcomeBit(Bit):
    version = "0.9"

    @property
    def role_table(self) -> RoleTable:
        greeter = Role(name="greeter", role_class=RoleClass.UNIQUE,
                       capacity=1, scored=True,
                       light_manifest=WELCOME_LIGHT_MANIFEST,
                       welcome=WELCOME_PAIR)
        jammer = Role(name="jammer", role_class=RoleClass.JAM,
                      capacity=None, scored=False)
        return RoleTable(
            roles={"greeter": greeter, "jammer": jammer},
            node_map={"NODE_GREET": ["greeter"], "NODE_JAM": ["jammer"]},
        )
```

And the tests:

```python
def test_granted_join_carries_composed_config_blob():
    server = make_server()
    server.load_bit("welcome_bit")
    result = server.join("ie1", "NODE_GREET")
    assert result.granted is True
    assert result.config == {
        "role": "greeter",
        "class": "UNIQUE",
        "scored": True,
        "light_manifest": {
            "instruments": WELCOME_LIGHT_MANIFEST["instruments"],
            "bit_name": "welcome_bit",
            "bit_version": "0.9",
            "role": "greeter",
            "welcome": WELCOME_PAIR["light"],
        },
    }


def test_denied_join_carries_no_config():
    server = make_server()
    server.load_bit("welcome_bit")
    server.join("ie1", "NODE_GREET")
    denied = server.join("ie2", "NODE_GREET")  # capacity 1
    assert denied.granted is False
    assert denied.config is None


def test_role_switch_composes_the_new_roles_config():
    server = make_server()
    server.load_bit("welcome_bit")
    server.join("ie1", "NODE_GREET")
    switch = server.join("ie1", "NODE_JAM")
    assert switch.granted is True
    assert switch.config["role"] == "jammer"
    assert switch.config["scored"] is False
    # jammer declares nothing: bare provenance, no welcome key
    assert switch.config["light_manifest"] == {
        "bit_name": "welcome_bit", "bit_version": "0.9", "role": "jammer"}


def test_join_with_no_bit_loaded_carries_no_config():
    server = make_server()
    result = server.join("ie1", "NODE_GREET")
    assert result.granted is False
    assert result.config is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -q`
Expected: FAIL — `AttributeError: 'JoinResult' object has no attribute 'config'` (and the exact-dict assertion once the field exists)

- [ ] **Step 3: Implement**

`control/registration.py` — add the field to `JoinResult` after `hint`:

```python
    # Composed per-role config blob for /ie<N>/role -- filled by
    # GameServer.join on granted results (control/role_config.py);
    # RegistrationState itself never touches it.
    config: dict | None = None
```

`control/engine.py`:

1. Extend the import: `from control.role_config import compose_role_config, validate_role_declarations`
2. In `join`, inside the `if result.granted:` block, before the notifies:

```python
        if result.granted:
            role = self.registration.role_table.roles[result.role]
            result.config = compose_role_config(
                self.bit_name, self.bit.version, role)
            self._notify("on_registration_change")
            self._notify("on_devices_change")
```

(Note: the role comes from the registration's role-table snapshot, not a fresh `self.bit.role_table` call — Bits build tables per property access.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_engine.py -q`
Expected: all pass

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m pytest tests -q` — expected: all pass.

```bash
git add control/registration.py control/engine.py tests/test_engine.py
git commit -m "feat(engine): surface composed role config blob on JoinResult"
```

---

### Task 6: Console — `role_view` welcome + `bit_name` cleanup

**Files:**
- Modify: `console/protocol.py`
- Modify: `console/agent.py`
- Test: `tests/test_console_protocol.py`, `tests/test_console_agent.py`

**Interfaces:**
- Consumes: `Role.welcome` (Task 1), `GameServer.bit_name` (Task 4).
- Produces: `role_view` dict gains a `"welcome"` key. Browser panel consumers see it in snapshots.

- [ ] **Step 1: Write the failing tests**

In `tests/test_console_protocol.py`, extend `test_role_view_shape`'s expected dict with `"welcome": None`, and add:

```python
def test_role_view_carries_v2_manifest_and_welcome():
    role = Role(name="player", role_class=RoleClass.SHARED,
                capacity=None, scored=True,
                light_manifest={"instruments": [
                    {"instrument": "bloom", "target": "primary"}]},
                welcome={"light": {"instrument": "bloom"}})
    view = protocol.role_view(role)
    assert view["light_manifest"] == {"instruments": [
        {"instrument": "bloom", "target": "primary"}]}
    assert view["welcome"] == {"light": {"instrument": "bloom"}}
```

In `tests/test_console_agent.py`, add (uses that file's existing `_server_with_agent` helper):

```python
def test_snapshot_loaded_bit_name_comes_from_game_server_bit_name():
    gs, srv, agent = _server_with_agent()
    gs.load_bit("TestBit")
    assert gs.bit_name == "TestBit"
    assert agent.snapshot()["loaded_bit"] == "TestBit"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_console_protocol.py tests/test_console_agent.py -q`
Expected: `test_role_view_shape` FAILS (missing `"welcome"` key). The agent test may already pass — that is fine; it becomes the regression guard for the cleanup.

- [ ] **Step 3: Implement**

`console/protocol.py::role_view` — add after the `light_manifest` line:

```python
        "welcome": role.welcome,
```

`console/agent.py` — replace the `_loaded_bit_name` method body:

```python
    def _loaded_bit_name(self) -> str | None:
        return self.game_server.bit_name
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_console_protocol.py tests/test_console_agent.py -q`
Expected: all pass

- [ ] **Step 5: Run the full suite and commit**

Run: `python3 -m pytest tests -q` — expected: all pass.

```bash
git add console/protocol.py console/agent.py tests/test_console_protocol.py tests/test_console_agent.py
git commit -m "feat(console): render v2 manifest + welcome; read bit_name from engine"
```

---

### Task 7: TestBit declares v2 light + welcome; canonical-doc touch

**Files:**
- Modify: `bits/test_bit.py`
- Modify: `docs/control-gameserver-design.md`
- Test: `tests/test_test_bit.py`

**Interfaces:**
- Consumes: everything above. TestBit becomes the first real Bit declaring light lanes — the act that formally freezes the schema per the old placeholder's promise.

- [ ] **Step 1: Write the failing tests**

In `tests/test_test_bit.py`, add:

```python
def test_player_role_declares_v2_light_manifest_and_welcome():
    bit = TestBit()
    table = bit.role_table
    player = table.roles["player"]
    assert player.light_manifest == {
        "instruments": [
            {"instrument": "bloom", "target": "primary",
             "params": {"base_hue": 0.33},
             "lanes": [{"source": "note", "dest": "trigger"},
                       {"source": "cc:74", "dest": "base_hue"}]},
        ],
    }
    assert player.welcome == {
        "light": {"instrument": "bloom", "params": {"base_hue": 0.33},
                  "duration": 1.5},
        "audio": {"instrument": "chime", "duration": 1.5},
    }


def test_jammer_role_keeps_empty_light_defaults():
    bit = TestBit()
    table = bit.role_table
    assert table.roles["jammer"].light_manifest == {}
    assert table.roles["jammer"].welcome is None


def test_test_bit_declares_a_version():
    assert TestBit().version == "0.1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_test_bit.py -q`
Expected: FAIL — `assert {} == {...}` and version `"" == "0.1"`

- [ ] **Step 3: Implement**

In `bits/test_bit.py`, add `version = "0.1"` to the class body (right below `class TestBit(Bit):`'s docstring position, above `__init__`), and replace the `player` role in `role_table` with:

```python
        player = Role(
            name="player", role_class=RoleClass.SHARED, capacity=None,
            scored=True,
            # First real light-lane declaration: the act that freezes the
            # light-manifest v2 authored shape (see control/roles.py).
            # Instrument names are opaque to Control; these are luxaeterna
            # registry names.
            light_manifest={
                "instruments": [
                    {"instrument": "bloom", "target": "primary",
                     "params": {"base_hue": 0.33},
                     "lanes": [{"source": "note", "dest": "trigger"},
                               {"source": "cc:74", "dest": "base_hue"}]},
                ],
            },
            welcome={
                "light": {"instrument": "bloom",
                          "params": {"base_hue": 0.33}, "duration": 1.5},
                "audio": {"instrument": "chime", "duration": 1.5},
            },
        )
```

- [ ] **Step 4: Touch the canonical design doc**

In `docs/control-gameserver-design.md`, player-flow step 3, extend the config-blob sentence. Change:

```
The config blob carries what the role needs the device to know (LED palette, local sample set, sensor rates, scored flag).
```

to:

```
The config blob carries what the role needs the device to know (local sample set, sensor rates, scored flag, and the role's light-manifest v2 blob -- instruments plus the per-role welcome gesture, with bit/role provenance stamped by Control; see the luxaeterna session-lifecycle spec section 9 and this repo's 2026-07-22 light-manifest-v2-adoption spec).
```

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests -q`
Expected: all pass (~130 tests)

- [ ] **Step 6: Commit**

```bash
git add bits/test_bit.py tests/test_test_bit.py docs/control-gameserver-design.md
git commit -m "feat(test-bit): declare v2 light manifest + welcome pair; doc touch"
```

---

## Post-plan notes (for the finishing step, not tasks)

- Cross-repo flags recorded in the spec §9 (luxaeterna `from_dict`/`_require` merge reconciliation; reserved `sys:*` names) — surface them in the PR description.
- `docs/MM_TERRARIUM.md` deep-dive sync happens at closeout per house process (`mm-deepdive-sync`).
