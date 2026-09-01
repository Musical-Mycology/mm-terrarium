# Console Load Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Console serve-mode load flow solid: a bit load loads only the bit, MetronomeBit is disabled pending redesign, Testshrooms answer Flash/Stop/Ping, Stop can silence everything via a new All target, and the operator gets RESTART (soft cycle) and a hard-stop ABORT (room down).

**Architecture:** All changes ride existing seams: the Bit manifest schema (`enabled`), `BitRegistry`'s loadability views, `run_stack`'s child-spawn config, `terrarium_boot`'s serve-round loop, `GameServer.fire_function`'s target resolution, and ConsoleAgent's command dispatch. No new engine states, no wire-protocol breaks (one new console command, one new picker sentinel).

**Tech Stack:** Python 3 (stdlib + pytest), vanilla ES-module JS for the Console front end, TOML manifests.

**Spec:** `docs/superpowers/specs/2026-09-01-console-load-stabilization-design.md`

## Global Constraints

- Run the suite ONLY via the project venv: `.venv/bin/python -m pytest tests -q` (bare `python3` produces a phantom luxaeterna import error; the worktree `.venv` is a symlink to the main checkout's).
- The offline suite must keep passing with no Arco, no pyarco, no o2litepy importable. Never add a module-level import of any of those to `control/` or `devicelink/`.
- `Terrarium`/`GameServer` command paths return refusal reason strings or raise `InvalidTransition`/`BitLoadError` per existing convention; console handlers turn those into `error_event`, never tracebacks.
- Keep `Terrarium.recycle_room()`, `_recycle_room()`, `_restart_room_clients()` in place as callable seams (spec section 3); only their automatic invocation is removed.
- No em dashes in any authored text, comments included (house rule).
- Uplink (`uplink/link.py`) behavior is unchanged by every task: its abort stays bit-only, it gains no restart command.
- Commit after every task; message prefixes as given per task.

---

### Task 1: `[bit] enabled` manifest key and registry loadability

**Files:**
- Modify: `control/bit_config.py` (BitIdentity at :36, `_parse_identity` at :172)
- Modify: `control/bit_registry.py` (`_LazyClassMap` at :42, `resolve_config` at :186, `list_view` at :227)
- Modify: `bits/metronome/bit.toml`
- Test: `tests/test_bit_config.py`, `tests/test_bit_registry.py`

**Interfaces:**
- Produces: `BitIdentity.enabled: bool` (default True); `BitRegistry.lazy_class_map()` excludes disabled names from `__iter__`/`__len__` and raises `KeyError` on `__getitem__`; `BitRegistry.resolve_config(name)` raises `ManifestError(key="bit.enabled")` for a disabled bit; `list_view()` rows carry `"enabled": bool`. Tasks 2 and later rely on exactly these names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bit_config.py`:

```python
MINIMAL_ENABLED_FALSE = """
[bit]
name = "Off"
entry = "off:Off"
requires_terrarium_api = 1
enabled = false
"""


def test_bit_enabled_parses_and_defaults_true():
    from control.bit_config import parse_manifest
    off = parse_manifest(MINIMAL_ENABLED_FALSE, source="t")
    assert off.identity.enabled is False
    on = parse_manifest(MINIMAL_ENABLED_FALSE.replace(
        "enabled = false\n", ""), source="t")
    assert on.identity.enabled is True
```

Append to `tests/test_bit_registry.py` (reuse that module's existing helper for writing a package dir with a `bit.toml`; if none fits, use `tmp_path` directly as below):

```python
def _write_pkg(root, name, enabled):
    pkg = root / name.lower()
    pkg.mkdir(parents=True)
    line = "" if enabled else "enabled = false\n"
    (pkg / "bit.toml").write_text(
        f'[bit]\nname = "{name}"\nentry = "m:C"\n'
        f"requires_terrarium_api = 1\n{line}")
    return pkg


def test_disabled_bit_is_discovered_but_not_loadable(tmp_path):
    from control.bit_config import ManifestError
    from control.bit_registry import BitRegistry
    _write_pkg(tmp_path, "OnBit", True)
    _write_pkg(tmp_path, "OffBit", False)
    reg = BitRegistry.scan([tmp_path])
    assert set(reg.packages) == {"OnBit", "OffBit"}   # still discovered
    cmap = reg.lazy_class_map()
    assert set(cmap) == {"OnBit"}                     # not loadable
    assert len(cmap) == 1
    import pytest
    with pytest.raises(KeyError):
        cmap["OffBit"]
    with pytest.raises(ManifestError, match="disabled"):
        reg.resolve_config("OffBit")
    rows = {r["name"]: r for r in reg.list_view()}
    assert rows["OffBit"]["enabled"] is False
    assert rows["OnBit"]["enabled"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_bit_config.py -k enabled tests/test_bit_registry.py -k disabled -v`
Expected: FAIL (`enabled` attribute missing / KeyError not raised).

- [ ] **Step 3: Implement**

`control/bit_config.py`: add field `enabled: bool = True` to `BitIdentity`; in `_parse_identity` add `"enabled"` to the `known` set and `enabled=_get(raw, "enabled", bool, True, source=source, prefix="bit")` to the returned `BitIdentity`.

`control/bit_registry.py`:

```python
class _LazyClassMap(Mapping):
    # (docstring: append one line: "Disabled packages (bit.enabled =
    # false) are excluded entirely: absent from iteration and a KeyError
    # on access, so nothing can load them.")

    def __getitem__(self, name: str) -> type:
        pkg = self._registry.packages[name]     # KeyError for unknown, as before
        if not pkg.config.identity.enabled:
            raise KeyError(name)
        return self._registry.bit_class(name)

    def __iter__(self):
        return (n for n, p in self._registry.packages.items()
                if p.config.identity.enabled)

    def __len__(self) -> int:
        return sum(1 for _ in self)
```

In `resolve_config`, before the `if not overrides:` line:

```python
        if not pkg.config.identity.enabled:
            raise ManifestError(
                source=str(pkg.path / "bit.toml"), key="bit.enabled",
                message="bit is disabled (enabled = false); flip the "
                        "manifest to re-enable it")
```

In `list_view`'s row dict, after `"hidden": ...`: add `"enabled": config.identity.enabled,`.

`bits/metronome/bit.toml`: in the `[bit]` table add `enabled = false` (leave everything else untouched).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS except any test that loads MetronomeBit **through a registry** class map or `resolve_config`. Fix those by scanning a registry whose metronome manifest is enabled (test-local `tmp_path` copy) or by constructing `MetronomeBit` directly with `parse_manifest` output; do NOT weaken the enabled semantics. Direct `from bits.metronome.metronome_bit import MetronomeBit` imports are unaffected by design.

- [ ] **Step 5: Commit**

```bash
git add control/bit_config.py control/bit_registry.py bits/metronome/bit.toml tests/test_bit_config.py tests/test_bit_registry.py
git commit -m "feat(bits): [bit] enabled manifest key; disable MetronomeBit pending redesign"
```

---

### Task 2: Surface disabled bits (Console picker, --list-bits, CLI refusals)

**Files:**
- Modify: `console/static/bit.js` (the picker row loop feeding `wire.send("load_bit", ...)` at :304)
- Modify: `harness/terrarium_boot.py` (`--list-bits` print at :1246-1249, unknown-bit check at :1264)
- Modify: `harness/run_stack.py` (`config_from_args` bit check at :727)
- Test: `tests/test_console_js.py` (or `tests/test_console_static.py`, whichever already asserts on `bit.js` content), new `tests/test_disabled_bit_cli.py`

**Interfaces:**
- Consumes: `list_view()` rows' `"enabled"` key and `resolve_config`'s `ManifestError` from Task 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_disabled_bit_cli.py`:

```python
"""A disabled bit is refused with a located message, not a traceback,
from both CLI launchers."""
import pytest

from control.bit_registry import BitRegistry


def _registry_with_disabled(tmp_path):
    pkg = tmp_path / "off"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(
        '[bit]\nname = "OffBit"\nentry = "m:C"\n'
        "requires_terrarium_api = 1\nenabled = false\n")
    return BitRegistry.scan([tmp_path])


def test_run_stack_refuses_disabled_bit(tmp_path, capsys):
    from harness.run_stack import config_from_args, parse_args
    reg = _registry_with_disabled(tmp_path)
    args = parse_args(["--bit", "OffBit"])
    with pytest.raises(SystemExit):
        config_from_args(args, registry=reg)
    assert "disabled" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_disabled_bit_cli.py -v`
Expected: FAIL (currently `resolve_config` raises ManifestError as an uncaught traceback, or the bit passes).

- [ ] **Step 3: Implement**

`harness/run_stack.py` `config_from_args`, right after the `if bit not in registry.packages:` block:

```python
    if not registry.packages[bit].config.identity.enabled:
        print(f"Bit {bit!r} is disabled (bit.enabled = false in its "
              f"manifest); re-enable it there to load it.", file=sys.stderr)
        raise SystemExit(1)
```

`harness/terrarium_boot.py` `main()`, after its own `if bit not in registry.packages:` block (:1264-1270), add the same guard with `sys.exit(1)`. In the `--list-bits` loop (:1246-1249), append a status column:

```python
            status = "" if row.get("enabled", True) else "\tDISABLED"
            print(f"{row['name']}\t{row['version']}\t{row['kind']}\t"
                  f"{rooms}\t{row['start']['when']}\t{row['description']}"
                  f"{status}")
```

`console/static/bit.js`: in the picker (`openPicker`'s bit-row loop that ends in the Load send at :304), skip disabled rows at the top of the loop: `if (bitRow.enabled === false) continue;`. Also guard the loaded-bit `findBit` display path only if it assumes presence (it tolerates `bit == null` already, so no change there).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_disabled_bit_cli.py tests/test_console_js.py tests/test_console_static.py -q` then the full suite `.venv/bin/python -m pytest tests -q`.
Expected: PASS. If `tests/test_console_js.py` executes `bit.js` in its harness, add an assertion there that a row with `enabled: false` renders no picker entry, following that file's existing DOM-fixture pattern.

- [ ] **Step 5: Commit**

```bash
git add harness/run_stack.py harness/terrarium_boot.py console/static/bit.js tests/test_disabled_bit_cli.py tests/test_console_js.py
git commit -m "feat(console,harness): surface and refuse disabled bits everywhere loadable-ness is offered"
```

---

### Task 3: `testshroom` carried instrument, declared by default

**Files:**
- Create: `instruments/testshroom.toml`
- Modify: `harness/o2_shroom.py` (`--instrument` at :375)
- Test: `tests/test_instrument_catalog.py` or `tests/test_terrarium_config.py` (whichever pins `instruments/*.toml` today; find with `grep -rln "tuneshroom.toml" tests/`), `tests/test_o2_shroom.py`

**Interfaces:**
- Produces: catalog instrument named `"testshroom"` with capabilities `{light.pixels, audio.samples, gesture.tap, gesture.tilt}`, pixels 12; `o2_shroom` CLI default `--instrument testshroom`.

- [ ] **Step 1: Write the failing tests**

In the module that pins catalog instruments (per the grep above), add:

```python
def test_testshroom_catalog_entry_resolves_with_audio_samples():
    from control.terrarium_config import load_terrarium_config
    cfg = load_terrarium_config("terrarium.toml")
    inst = cfg.instruments["testshroom"]
    assert inst.pixels == 12
    assert "audio.samples" in inst.capabilities
    assert "light.pixels" in inst.capabilities      # carriable (engine gate)
    assert "audio.mic" not in inst.capabilities
```

In `tests/test_o2_shroom.py` add:

```python
def test_o2_shroom_declares_testshroom_by_default():
    import harness.o2_shroom as o2s
    parser_default = next(
        a.default for a in o2s.main.__globals__.get("_TEST_PARSER", []) or []
    ) if False else None
    # Simpler and stable: parse the module's argparse default directly.
    import argparse
    # Build the parser the same way main() does is not exposed; assert on
    # the source contract instead:
    import inspect
    src = inspect.getsource(o2s.main)
    assert '"--instrument", default="testshroom"' in src
```

(If `test_o2_shroom.py` already has a build-args helper that reaches the parser, prefer asserting `parse_args([]).instrument == "testshroom"` through it and drop the source check.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests -k "testshroom" -v`
Expected: FAIL (`KeyError: 'testshroom'`, default is None).

- [ ] **Step 3: Implement**

Create `instruments/testshroom.toml`:

```toml
description = "Harness Testshroom: browser-canvas 12-LED test instrument"
pixels = 12
capabilities = ["light.pixels", "audio.samples", "gesture.tap", "gesture.tilt"]
accepted_cues = ["midi", "play", "solid", "mute"]
  [ambient]
  [ambient.light]
  instruments = [ { instrument = "aurora", target = "primary" } ]

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

(Thresholds copied from `instruments/defaultshroom.toml`, same guessed-provenance caveat.)

`harness/o2_shroom.py` :375: change to `parser.add_argument("--instrument", default="testshroom", help="Declare this device's carried instrument on hello (default testshroom, the harness's own catalog instrument), re-sent on every heartbeat. Pass an empty string to stay undeclared, resolving to defaultshroom.")` and, where `args.instrument` is threaded (:483), normalize empty to None: `instrument=args.instrument or None`.

`ShroomClient`'s constructor default stays `None` (the Room simulator path stays undeclared; do not touch `harness/room_simulator.py`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests -k "testshroom or o2_shroom or instrument" -q`, then the full suite.
Expected: PASS. If the catalog has a pinned "known instruments" list test, add `testshroom` to it rather than deleting the pin.

- [ ] **Step 5: Commit**

```bash
git add instruments/testshroom.toml harness/o2_shroom.py tests/
git commit -m "feat(harness): testshroom carried instrument, declared by default (audio.samples for ping/flash-chime)"
```

---

### Task 4: run_stack loads no devices per round; persist by default

**Files:**
- Modify: `harness/run_stack.py` (`StackConfig` :101, respawn machinery :254-341, `device_command` :167-191, argparse :702, `config_from_args` :787, `_hold`'s round hook :520-560)
- Test: `tests/test_run_stack.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `StackConfig.persist_shrooms` default True; `device_command(cfg, index, ppid)` (the `dev=`/`node=` keyword params are deleted); no `CONTROL_ROUND_LOADED`-driven spawning anywhere.

- [ ] **Step 1: Write the failing tests**

In `tests/test_run_stack.py` add:

```python
def test_persist_is_the_default_and_opt_out_works():
    from harness.run_stack import parse_args
    assert parse_args([]).persist_shrooms is True
    assert parse_args(["--no-persist-shrooms"]).persist_shrooms is False


def test_device_command_forwards_persist_by_default(tmp_path):
    from harness.run_stack import StackConfig, device_command
    cfg = StackConfig(log_dir=str(tmp_path))
    cmd = device_command(cfg, 1, 123)
    assert "--persist" in cmd


def test_no_round_respawn_machinery_remains():
    import harness.run_stack as rs
    import inspect
    src = inspect.getsource(rs)
    assert "spawn_round_devices" not in src
    assert "-r{" not in src and "ie1-r2" not in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_run_stack.py -k "persist or respawn" -v`
Expected: FAIL (default False, machinery present).

- [ ] **Step 3: Implement**

- `StackConfig.persist_shrooms: bool = True` (comment: "persist is the load-only-loads default; --no-persist-shrooms opts out").
- argparse: replace the `--persist-shrooms` flag (:702) with a mutually-consistent pair:

```python
    ap.add_argument("--persist-shrooms", dest="persist_shrooms",
                    action="store_true", default=True,
                    help="Launch every player o2_shroom with --persist so "
                         "devices survive Bit rounds (the default).")
    ap.add_argument("--no-persist-shrooms", dest="persist_shrooms",
                    action="store_false",
                    help="One-shot devices: each exits on /release, the "
                         "pre-2026-09 behavior.")
```

- Delete the respawn machinery wholesale: the `round_loads` queue and its `queue` import if now unused, `on_control_line`, `control_on_line` (point the control tee back at `collect_url`), `round_number`, `first_round_load_seen`, `spawn_round_devices`, `drain_rounds`, and `_hold`'s round-drain hook parameter and its call site. `markers.CONTROL_ROUND_LOADED` stays emitted by terrarium_boot and stays in `_watch_list`'s marker dicts if present; only the spawning reaction goes.
- Simplify `device_command(cfg, index, ppid)`: drop the `dev=`/`node=` keyword parameters and the docstring paragraph about round-2+ respawns; `dev = f"ie{index}"`, `node = cfg.node` inline.
- Delete the existing respawn tests: `grep -n "spawn_round_devices\|ie1-r2\|round_loads" tests/test_run_stack.py` and remove those test functions.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_run_stack.py -q`, then full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/run_stack.py tests/test_run_stack.py
git commit -m "feat(run_stack): load_bit loads only the bit; persistent Testshrooms by default, round respawn deleted"
```

---

### Task 5: Remove the automatic per-round Arco recycle

**Files:**
- Modify: `harness/terrarium_boot.py` (`_serve_rounds` :611-723, `_serve_roomless` :751-792, main()'s recycle closure :1400-1425 and round-1 recycle blocks :1567-1573, :1596-1611)
- Test: `tests/` module covering `_serve_rounds` (find with `grep -rln "_serve_rounds" tests/`; extend that module)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_serve_rounds(gs, agent, arco, *, parent_pid=None, console_agent=None, drain_arco=None, terrarium=None)` (the `recycle` parameter is deleted); `_end_round(bit_name, reason_text)` returns `None` and only announces; `_serve_roomless(gs, agent, terrarium, *, console_agent=None, parent_pid=None, restart_clients=None)` (its `recycle` parameter deleted). `_recycle_room`/`_restart_room_clients`/`Terrarium.recycle_room` remain defined but uncalled from the round path.

- [ ] **Step 1: Write the failing test**

In the `_serve_rounds` test module add (adapting to its existing gs/agent/arco fakes; the assertion is the contract):

```python
def test_round_end_never_touches_the_room(monkeypatch, serve_fixtures):
    """A completed round announces CONTROL_ROUND_ENDED and loops to the
    next _wait_for_load without any unload_room/load_room churn."""
    gs, agent, arco, terrarium = serve_fixtures  # module's existing fakes
    calls = []
    terrarium.recycle_room = lambda: calls.append("recycle") or None
    terrarium.unload_room = lambda force=False: calls.append("unload") or None
    # drive one completed round then a parent-gone exit (existing pattern)
    ...
    assert calls == []
```

Also assert `import inspect; assert "recycle" not in inspect.signature(_serve_rounds).parameters`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests -k "serve_rounds or serve_round" -v`
Expected: the new test FAILS (signature still has recycle); existing recycle tests still pass.

- [ ] **Step 3: Implement**

`_serve_rounds`: delete the `recycle=None` parameter and docstring item 6; `_end_round` shrinks to announce-only:

```python
    def _end_round(bit_name, reason_text: str) -> None:
        """Announce the round's outcome (marker + console event). The
        automatic bit-cycle room recycle that used to follow was removed
        by the 2026-09-01 console-load-stabilization spec: a round ending
        must never churn Arco."""
        print(f"{markers.CONTROL_ROUND_ENDED} {bit_name} ({reason_text})",
              flush=True)
        if console_agent is not None:
            console_agent.announce_round_ended(bit_name, reason_text)
```

Callers drop the `outcome = _end_round(...)` checks: the timeout-abort branch becomes `_end_round(...); continue`, the completed tail becomes `_end_round(bit_name, "completed"); print("round complete; waiting for next load", flush=True)`.

`_serve_roomless`: delete its `recycle` parameter and the `recycle=recycle` pass-through.

`main()`: delete the `recycle()` closure (:1402-1407), the `recycle = recycle if effective_serve else None` line, and both round-1 recycle blocks (:1567-1573 and :1596-1609 collapse to just the announce + the "round complete; waiting for next load" print). Keep `clients_stopped`, `restart_clients`, and `pool` (Task 6 reuses them). Update `_serve_rounds`/`_serve_roomless` call sites to drop `recycle=`.

Delete or rewrite existing tests that pin the recycle-per-round behavior (grep `recycle` in the serve tests); keep `_recycle_room`'s own unit tests (the function remains a seam).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py tests/
git commit -m "feat(serve): remove automatic per-round Arco recycle; round end only announces"
```

---

### Task 6: Room-down resilience in the serve loop

**Files:**
- Modify: `harness/terrarium_boot.py` (`_serve_until_done` :518-563, `_serve_rounds`, `_serve_roomless`, main()'s closures and the two round-1 `_serve_rounds` call sites :1574, :1612)
- Test: same serve-loop test module as Task 5

**Interfaces:**
- Consumes: Task 5's simplified signatures.
- Produces: `_serve_until_done(gs, agent, arco, ..., terrarium=None)` returns `"no-room"` when `terrarium.state` leaves ROOM_READY; `_serve_rounds` returns `"no-room"` after announcing the round as `"aborted"`; `_serve_roomless(..., restart_clients=None, stop_clients=None)` calls `stop_clients()` on every `"no-room"` lap; main()'s `--room` path continues into `_serve_roomless` instead of exiting on `"no-room"`. Task 7 depends on all of this.

- [ ] **Step 1: Write the failing tests**

In the serve-loop test module:

```python
def test_room_down_mid_run_returns_no_room_not_arco_exited(serve_fixtures):
    """An operator hard-abort unloads the room (Arco dies with it) while
    _serve_until_done polls. The terrarium check must win over the
    arco.poll() check or the abort misreports as a crash."""
    gs, agent, arco, terrarium = serve_fixtures
    terrarium.state = TerrariumState.NO_ROOM
    arco.returncode = 1                      # dead process too
    assert _serve_until_done(gs, agent, arco,
                             terrarium=terrarium) == "no-room"


def test_serve_roomless_stops_clients_on_no_room(serve_fixtures):
    calls = []
    # arrange _serve_rounds to return "no-room" once then "parent-gone"
    ...
    _serve_roomless(gs, agent, terrarium,
                    stop_clients=lambda: calls.append("stop"),
                    restart_clients=lambda: None)
    assert calls == ["stop"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests -k "no_room or roomless" -v`
Expected: FAIL (`_serve_until_done` has no terrarium parameter).

- [ ] **Step 3: Implement**

`_serve_until_done`: add `terrarium=None` keyword; inside the loop, FIRST check (before `arco.poll()`, same reasoning as `_wait_for_load`'s docstring):

```python
        if (terrarium is not None
                and terrarium.state is not TerrariumState.ROOM_READY):
            return "no-room"
```

`_serve_rounds`: pass `terrarium=terrarium` into `_serve_until_done`; handle its return:

```python
        reason = _serve_until_done(gs, agent, arco, parent_pid=parent_pid,
                                   console_agent=console_agent,
                                   terrarium=terrarium)
        if reason in ("parent-gone", "arco-exited"):
            return reason
        if reason == "no-room":
            _end_round(bit_name, "aborted")
            return "no-room"
        _end_round(bit_name, "completed")
```

`_serve_roomless`: add `stop_clients=None` keyword; after `_serve_rounds` returns:

```python
        if reason != "no-room":
            return reason
        if stop_clients is not None:
            stop_clients()
```

main(): next to `restart_clients`, add the idempotent stop half (reusing `transport`/`pool` already in scope; this duplicates `_recycle_room`'s stop lines deliberately, that function stays an intact seam):

```python
    def stop_clients():
        """The room went down under live Arco clients (Console hard abort
        or unload_room). Stop the transport and drop the pool's dead-hub
        handles so the next load_room's restart_clients() can bring both
        back against the new hub. Idempotent via clients_stopped."""
        if clients_stopped[0]:
            return
        if transport is not None:
            transport.stop()
        if pool is not None:
            pool.quiesce()
        clients_stopped[0] = True

    stop_clients = stop_clients if effective_serve else None
```

main()'s `--room` path: replace both post-round-1 `_serve_rounds(...)` calls (:1574 and :1612) with

```python
                    _print_round_outcome(_serve_roomless(
                        gs, agent, terrarium,
                        console_agent=console_agent,
                        parent_pid=args.exit_with_parent,
                        restart_clients=restart_clients,
                        stop_clients=stop_clients))
```

(`_serve_roomless` opens with `_wait_for_room_ready`, which returns "ready" immediately while the room is up, so the healthy path is unchanged.) Round 1's own `_serve_until_done` call (:1587) gains `terrarium=terrarium`; if it returns `"no-room"`, announce `(aborted)` for `round1_bit_name` (marker + `announce_round_ended`), call `stop_clients()` if non-None, and fall into the same `_serve_roomless` call instead of the completed-announce branch.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/terrarium_boot.py tests/
git commit -m "feat(serve): survive room-down mid-round; --room path falls back to the NO_ROOM wait"
```

---

### Task 7: ABORT is a hard stop (room down)

**Files:**
- Modify: `console/agent.py` (AbortCommand branch :194-195)
- Test: `tests/test_console_agent.py`

**Interfaces:**
- Consumes: `Terrarium.unload_room(force=True) -> str | None` (existing), Task 6's harness resilience.
- Produces: console `abort` = `gs.abort()` then `terrarium.unload_room(force=True)` when a terrarium is wired; unchanged bit-only abort when `terrarium is None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_console_agent.py`, following its existing fake-terrarium pattern (the module already builds ConsoleAgent with `terrarium=` for the load_room tests):

```python
def test_abort_unloads_the_room_when_terrarium_wired(console_fixtures):
    agent, gs, terrarium = console_fixtures     # existing helper shape
    calls = []
    terrarium.unload_room = (
        lambda force=False: calls.append(force) or None)
    # a bit is loaded and running per the fixture
    agent._handle_command({"command": "abort"})
    assert gs.state is State.IDLE
    assert calls == [True]


def test_abort_without_terrarium_stays_bit_only(console_fixtures_no_terrarium):
    agent, gs = console_fixtures_no_terrarium
    agent._handle_command({"command": "abort"})   # must not raise
    assert gs.state is State.IDLE


def test_abort_reports_unload_refusal(console_fixtures):
    agent, gs, terrarium = console_fixtures
    terrarium.unload_room = lambda force=False: "already unloading"
    out = agent._handle_command({"command": "abort"})
    assert out["event"] == "error" and "already unloading" in out["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py -k abort -v`
Expected: new tests FAIL (`unload_room` never called).

- [ ] **Step 3: Implement**

`console/agent.py` :194-195:

```python
            elif isinstance(command, protocol.AbortCommand):
                # Hard stop (2026-09-01 spec section 7): end the bit AND
                # the room. With the room's Arco gone, sound physically
                # cannot continue, a guarantee no mute can match. A
                # terrarium-less embedding keeps the old bit-only abort.
                self.game_server.abort()
                if self.terrarium is not None:
                    reason = self.terrarium.unload_room(force=True)
                    if reason is not None:
                        return protocol.error_event(name, reason)
```

No `bit.js` change: the Abort button and its confirm tap already exist.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_console_agent.py -q`, then full suite.
Expected: PASS. If an existing test pins abort leaving the room loaded WITH a terrarium wired, update it to the new contract (the no-terrarium pin stays).

- [ ] **Step 5: Commit**

```bash
git add console/agent.py tests/test_console_agent.py
git commit -m "feat(console): Abort is a hard stop -- ends the bit and unloads the room (Arco down)"
```

---

### Task 8: RESTART command, button, and serve-loop handling

**Files:**
- Modify: `uplink/protocol.py` (command dataclasses :11-40, `parse_command` :42-72)
- Modify: `console/protocol.py` (re-export list :10-49)
- Modify: `console/agent.py` (`_handle_command` try-block :178-197)
- Modify: `console/static/bit.js` (button row :97-118)
- Modify: `harness/terrarium_boot.py` (`_serve_until_done`, `_serve_rounds`, main round-1 tail)
- Test: `tests/test_console_agent.py`, `tests/test_console_protocol.py`, serve-loop test module, `tests/test_console_js.py`

**Interfaces:**
- Consumes: `GameServer.bit_name`, `gs.bit.config` (read via `getattr(gs.bit, "config", None)`), `gs.abort()`, `gs.load_bit(name, config=...)`, Task 6's `terrarium=` threading.
- Produces: wire command `{"command": "restart"}` -> `RestartCommand`; `_serve_until_done` returns `"restarted"` when it observes LOADING/LOADED/SETUP; `_serve_rounds` announces `(restarted)` + a fresh `CONTROL_ROUND_LOADED` and continues.

- [ ] **Step 1: Write the failing tests**

`tests/test_console_protocol.py`:

```python
def test_restart_parses():
    from console import protocol
    cmd = protocol.parse_command({"command": "restart"})
    assert isinstance(cmd, protocol.RestartCommand)
```

`tests/test_console_agent.py`:

```python
def test_restart_reloads_the_same_bit_with_its_config(console_fixtures):
    agent, gs, terrarium = console_fixtures      # bit loaded per fixture
    name_before = gs.bit_name
    cfg_before = getattr(gs.bit, "config", None)
    agent._handle_command({"command": "restart"})
    assert gs.bit_name == name_before
    assert getattr(gs.bit, "config", None) is cfg_before
    assert gs.state is not State.IDLE            # reloaded, not just aborted


def test_restart_with_no_bit_is_a_refusal(console_fixtures_idle):
    agent, gs = console_fixtures_idle
    out = agent._handle_command({"command": "restart"})
    assert out["event"] == "error" and "no bit loaded" in out["message"]
```

Serve-loop module:

```python
def test_serve_until_done_reports_restart(serve_fixtures):
    gs, agent, arco, terrarium = serve_fixtures
    gs.state = State.LOADED                      # a restart landed mid-poll
    assert _serve_until_done(gs, agent, arco,
                             terrarium=terrarium) == "restarted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests -k restart -v`
Expected: FAIL (`unrecognized command: 'restart'`).

- [ ] **Step 3: Implement**

`uplink/protocol.py`: add

```python
@dataclass
class RestartCommand:
    pass
```

and in `parse_command`: `if command == "restart": return RestartCommand()`.

`console/protocol.py`: add `RestartCommand` to the import and `__all__`.

`console/agent.py`, inside the existing try (:178-197), after the Abort branch:

```python
            elif isinstance(command, protocol.RestartCommand):
                # Soft cycle (2026-09-01 spec section 6): same bit, same
                # resolved config, Arco and Room untouched. One step
                # removed from Abort, which takes the room down too.
                gs = self.game_server
                if gs.bit_name is None:
                    return protocol.error_event(name, "no bit loaded")
                bit_name = gs.bit_name
                cfg = getattr(gs.bit, "config", None)
                gs.abort()
                gs.load_bit(bit_name, config=cfg)
```

(`InvalidTransition`/`BitLoadError` are already caught by the surrounding try and become error events.)

`console/static/bit.js`, between the Run and Abort buttons (:103):

```javascript
  const restartBtn = mk("button", "btn", "Restart");
  restartBtn.disabled = gated;
  restartBtn.onclick = () => {
    wire.confirmTap(restartBtn, { armLabel: "Confirm restart?" }, () => {
      wire.send("restart", {}, restartBtn);
    });
  };
  btnrow.appendChild(restartBtn);
```

`harness/terrarium_boot.py`:

- `_serve_until_done`, inside the loop after `gs.tick(...)` and before the IDLE check:

```python
        if gs.state in (State.LOADING, State.LOADED, State.SETUP):
            # Only a Console restart can put the engine here while this
            # function is running: run() was already called before entry.
            return "restarted"
```

- `_serve_rounds`, in the return handling added by Task 6, before the completed announce:

```python
        if reason == "restarted":
            _end_round(bit_name, "restarted")
            print(f"{markers.CONTROL_ROUND_LOADED} {gs.bit_name}",
                  flush=True)
            continue
```

(The `continue` re-enters `_wait_for_load`, which returns "loaded" immediately, then the new round's own SETUP hold and start condition run exactly like any console-loaded round.)

- main()'s round-1 tail: alongside Task 6's `"no-room"` handling, treat `"restarted"` the same way `"completed"` is treated for entering the round loop, but announce `(restarted)` and print the fresh `CONTROL_ROUND_LOADED {gs.bit_name}` first. In one-shot (non-serve) mode a `"restarted"` return simply falls through to teardown like `"completed"` (a restart makes no sense without rounds; note this in a comment).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests -k "restart or console_agent or serve" -q`, then the full suite.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add uplink/protocol.py console/protocol.py console/agent.py console/static/bit.js harness/terrarium_boot.py tests/
git commit -m "feat(console,serve): RESTART -- soft-cycle the loaded bit; serve loop treats it as a fresh round"
```

---

### Task 9: The All target (`@all`)

**Files:**
- Modify: `control/cues.py` (:20)
- Modify: `control/engine.py` (`_resolve_target` :672-698)
- Modify: `console/static/functions.js` (:22, `fillDevicePicker` :72-93, `surfaceLookupKey` :170-172, `refreshDiagButtons` :174-181, `isCompatible` :115-122)
- Test: `tests/test_engine_functions.py`, `tests/test_console_js.py`

**Interfaces:**
- Consumes: `builtin_functions` ladder, `DevicePool.all() -> list[DeviceInfo]`, `GameServer.muted`.
- Produces: `control.cues.ALL = "@all"`; `fire_function(..., dev="@all")` fans out to the Room's fixture devs plus every DevicePool dev; the Console picker's aggregate option value is `"@all"` labeled "All".

- [ ] **Step 1: Write the failing tests**

`tests/test_engine_functions.py` (following that module's existing GameServer fixture pattern with a bound Room and hello'd devices):

```python
def test_all_target_fans_out_to_room_and_every_device(gs_with_room):
    gs = gs_with_room                       # room bound; no bit loaded
    gs.hello("ie1", "", "", instrument="testshroom")
    gs.hello("ie2", "", "", instrument="testshroom")
    fired = []
    gs.add_observer(type("O", (), {
        "on_function_fired": lambda self, rec: fired.append(rec)})())
    assert gs.fire_function("flash", fired_by="admin-manual",
                            dev="@all") is None
    (rec,) = fired
    assert set(rec.devs) >= {"ie1", "ie2"}          # every pool dev
    assert any(d not in ("ie1", "ie2") for d in rec.devs)  # + the room


def test_stop_at_all_mutes_everything(gs_with_room):
    gs = gs_with_room
    gs.hello("ie1", "", "", instrument="testshroom")
    gs.fire_function("stop", fired_by="admin-manual", dev="@all")
    assert "ie1" in gs.muted
    assert len(gs.muted) >= 2               # the room's canonical dev too
```

(If the fixture seeds `carried_instruments` explicitly, pass a dict containing the testshroom Instrument from `load_terrarium_config("terrarium.toml").instruments`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_functions.py -k all_target -v`
Expected: FAIL (`@all` treated as a literal dev name; single-dev fan-out).

- [ ] **Step 3: Implement**

`control/cues.py` :20:

```python
ROOM = "@room"
# The operator's everything sentinel: room fixtures plus every connected
# device. Console picker value; resolved in GameServer._resolve_target.
ALL = "@all"
```

`control/engine.py` `_resolve_target` (import `ALL` alongside `ROOM`):

```python
        if target is FunctionTarget.DEVICE:
            return [dev] if dev else []
        if target is FunctionTarget.SURFACE and dev not in (ROOM, ALL):
            return [dev] if dev else []
        room_devs: list[str] = []
        if self.room is not None and self.room.bound:
            profile = self.room.profile
            room_devs = [self.room.bound[f.name] for f in profile.fixtures
                         if f.name in self.room.bound]
        if target is FunctionTarget.SURFACE and dev == ALL:
            # The operator's All: room fixtures plus every connected
            # device (DevicePool, not registration -- a lobby device with
            # no role still answers flash/stop/ping).
            out = list(room_devs)
            room_set = set(room_devs)
            for info in self.devices.all():
                if info.dev not in room_set:
                    out.append(info.dev)
            return out
        if target in (FunctionTarget.ROOM, FunctionTarget.SURFACE):
            return room_devs
        ...  # existing FunctionTarget.ALL tail unchanged
```

`console/static/functions.js`:

- `:22`: `const ALL_OPTION = "@all";` (delete `ROOM_OPTION`; grep the file for remaining uses).
- `fillDevicePicker` (:77-83): option value `ALL_OPTION`, label `"All"`.
- `surfaceLookupKey` (:170-172): `return pickerValue === ALL_OPTION ? null : pickerValue;` and keep the `"room"` mapping for per-fixture pickers only if any caller still passes `"@room"` (none should; grep).
- `refreshDiagButtons` (:174-181): for `ALL_OPTION`, enable a button when ANY surface supports it:

```javascript
function builtinsFor(pickerValue) {
  if (pickerValue === ALL_OPTION) {
    const set = new Set();
    for (const key of Object.keys(surfaceInstruments)) {
      for (const n of builtinsMap[surfaceInstruments[key]] || []) set.add(n);
    }
    return [...set];
  }
  const inst = surfaceInstruments[
    pickerValue === ALL_OPTION ? "room" : pickerValue];
  return inst ? builtinsMap[inst] || [] : [];
}
```

and have `refreshDiagButtons` use `builtinsFor(diagPicker.value)`.

- `isCompatible` (:115-122): for `ALL_OPTION`, return true when any entry of `surfaceInstruments` declares the function (or the existing bit-script/builtin short-circuits already returned true). `resolvedDescription` falls back to `fn.description` for `ALL_OPTION` (the `!instrumentName` path already does).

Update the `@room` comment blocks (:165-169, engine imports) to describe `@all`. Bit-internal `cues.ROOM` and Bit ROOM-target scripts are untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_engine_functions.py tests/test_console_js.py -q`, then the full suite.
Expected: PASS. Any test pinning the picker's "Room" option text updates to "All"/`@all`.

- [ ] **Step 5: Commit**

```bash
git add control/cues.py control/engine.py console/static/functions.js tests/
git commit -m "feat(functions): All target -- @all fans a fire out to the room and every connected device"
```

---

### Task 10: Full-suite gate, deep-dive sync prep, live checklist

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (via the `mm-deepdive-sync` skill at closeout, not by hand here)
- Create: nothing new (the live protocol lives in the spec, section 8)

- [ ] **Step 1: Run the whole suite one final time**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS (record the new passed-count baseline for the deep-dive entry).

- [ ] **Step 2: Sanity-run the drivers offline**

Run: `.venv/bin/python -m harness.terrarium_boot --list-bits --config terrarium.toml`
Expected: MetronomeBit listed with `DISABLED`; TestBit and CaptureBit listed normally. Exit 0.

- [ ] **Step 3: Commit any stragglers, then hand off**

```bash
git status --short
git add -A && git commit -m "chore: console-load-stabilization loose ends" || true
```

Then: (a) run `mm-deepdive-sync` to update `docs/MM_TERRARIUM.md` (supersede notes: bit-cycle recycle entry, per-round respawn note, Room-picker-becomes-All in the four-operator-triggers entry, abort semantics, the stale MetronomeBit-declares-stop claim, new RESTART command); (b) open the PR; (c) the live verification protocol (spec section 8) runs on MYCOLOGICAL after merge: `run_stack --open --devices 1`, then the five-step checklist, RESTART x5 delivering the round-2-audio verdict that decides the recycle machinery's deletion or the upstream report to Roger.

---

## Self-review notes

- Spec coverage: section 1 -> Tasks 1-2; section 2 -> Task 4; section 3 -> Tasks 5-6 (+10 live); section 4 -> Task 9; section 5 -> Task 3; section 6 -> Task 8; section 7 -> Task 7; section 8 -> Task 10 handoff. Testing section covered per task. Out-of-scope items untouched.
- Type consistency: `_serve_until_done(..., terrarium=None)` introduced in Task 6 and consumed in Task 8; `_end_round(bit_name, reason_text) -> None` set in Task 5 and used in 6/8; `RestartCommand`/`"restart"` consistent across protocol, agent, bit.js; `ALL = "@all"` consistent across cues.py, engine, functions.js; `persist_shrooms` default True consistent between StackConfig and argparse.
- Known judgment calls for executors: exact fixture names in serve-loop and console-agent test modules differ from the illustrative `serve_fixtures`/`console_fixtures` names; adapt to the module's existing helpers without changing the asserted contracts.
