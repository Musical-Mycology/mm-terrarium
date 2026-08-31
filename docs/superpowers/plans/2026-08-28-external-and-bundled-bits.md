# External and Bundled Bits (Spec 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an external Bit package (living outside mm-terrarium's `bits/`) a supported, safe thing: a versioned API gate at discovery, `[assets]` resolved only through config, a bundle/verify/install tool, and a reference external Bit in mm-tuneshroom.

**Architecture:** All engine changes ride the existing discovery pipeline (`control/bit_config.py` parse -> `control/bit_registry.py` scan) and its located-error surfaces (`ManifestError` / `PackageError`); no engine, transport, Console, or wire changes. The bundling tool is an offline CLI peer of `tools/trace_stats.py`. The reference external Bit lands in the mm-tuneshroom repo as its own PR.

**Tech Stack:** Python 3.11+ stdlib only (`tomllib`, `zipfile`, `hashlib`, `json`). Tests: pytest, fully offline.

**Spec:** `docs/superpowers/specs/2026-08-28-external-and-bundled-bits-design.md`

## Global Constraints

- Run tests as `.venv/bin/python -m pytest tests -q` (NEVER bare `python3` — see docs/MM_TERRARIUM.md; a fresh worktree symlinks the main checkout's venv: `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`).
- Suite baseline before this plan: **1634 passed, 1 skipped**. It must never drop below green.
- Discovery executes no Bit code; all new checks are `tomllib` parsing + `stat()`.
- `control/` stays stdlib-only at module level.
- Bits may import only the stdlib and mm-terrarium's own modules (no third-party deps) — spec section 5.3.
- The engine provides exactly one API version: equality check, not ranges (spec 2.2).
- All new manifest errors are located: they name the source file and key.
- No em dashes in any prose written for docs (house rule).
- mm-tuneshroom checkout: `/Users/chris/projects/mm-tuneshroom` (verified present; has no `bits/` yet).

---

### Task 1: `TERRARIUM_API` constant and `requires_terrarium_api` parsing (`min_terrarium` retired)

**Files:**
- Create: `control/api_version.py`
- Modify: `control/bit_config.py` (BitIdentity, `_parse_identity`)
- Test: `tests/test_bit_config.py`

**Interfaces:**
- Produces: `control.api_version.TERRARIUM_API: int == 1`; `BitIdentity.requires_terrarium_api: int | None` (None = key absent; the registry enforces presence in Task 2). `BitIdentity.min_terrarium` is DELETED.
- Consumes: existing `_get` / `_warn_unknown_keys` / `ManifestError` in `control/bit_config.py`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_bit_config.py`; reuse its existing minimal-manifest helper if one exists, else this literal):

```python
MINIMAL = """
[bit]
name = "X"
entry = "x:X"
"""


def test_terrarium_api_constant_is_one():
    from control.api_version import TERRARIUM_API
    assert TERRARIUM_API == 1


def test_requires_terrarium_api_parses_as_int():
    text = MINIMAL + "requires_terrarium_api = 1\n"
    config = parse_manifest(text, source="t")
    assert config.identity.requires_terrarium_api == 1


def test_requires_terrarium_api_absent_is_none():
    config = parse_manifest(MINIMAL, source="t")
    assert config.identity.requires_terrarium_api is None


def test_requires_terrarium_api_bool_refused():
    # TOML `true` must not pass as 1 (spec 2.2).
    text = MINIMAL + "requires_terrarium_api = true\n"
    with pytest.raises(ManifestError) as exc:
        parse_manifest(text, source="t")
    assert "requires_terrarium_api" in str(exc.value)


def test_min_terrarium_now_warns_as_unknown(caplog):
    text = MINIMAL + 'min_terrarium = "0.1"\n'
    with caplog.at_level(logging.WARNING):
        config = parse_manifest(text, source="t")
    assert not hasattr(config.identity, "min_terrarium")
    assert any("min_terrarium" in r.message % r.args for r in caplog.records)
```

Note: the `[bit]` table's `requires_terrarium_api = 1` line must be inside the `[bit]` table — `MINIMAL` ends inside `[bit]`, so plain string concatenation as above is correct. Match the file's existing imports (`parse_manifest`, `ManifestError`, `pytest`, `logging`).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_bit_config.py -q -k "terrarium_api or min_terrarium"`
Expected: FAIL (`ModuleNotFoundError: control.api_version`, missing attribute, etc.)

- [ ] **Step 3: Implement**

`control/api_version.py`:

```python
"""The Terrarium Bit API version: one integer, defined exactly once,
bumped only on a breaking change to the Bit-facing contract (the Bit
interface, manifest schema v1, and the cue/Function/Trigger vocabulary).
Additive changes never bump it. See
docs/superpowers/specs/2026-08-28-external-and-bundled-bits-design.md
section 2.
"""

TERRARIUM_API = 1
```

`control/bit_config.py` edits, all inside `_parse_identity` and `BitIdentity`:

- In `BitIdentity`: replace `min_terrarium: str = ""` with `requires_terrarium_api: int | None = None`.
- In `_parse_identity`'s `known` set: replace `"min_terrarium"` with `"requires_terrarium_api"` (so a stale `min_terrarium` hits the existing `_warn_unknown_keys` path — that is the whole retirement mechanism).
- Replace the `min_terrarium=_get(...)` constructor line with:

```python
        requires_terrarium_api=_get(raw, "requires_terrarium_api", int, None,
                                     source=source, prefix="bit"),
```

(`_get` with `expected is int` already refuses bool.)

- [ ] **Step 4: Run the targeted tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_bit_config.py -q` then `.venv/bin/python -m pytest tests -q`
Expected: all green (nothing in the tree reads `min_terrarium`; verified by grep during planning — its only occurrences were the schema itself).

- [ ] **Step 5: Commit**

```bash
git add control/api_version.py control/bit_config.py tests/test_bit_config.py
git commit -m "feat(bits): TERRARIUM_API constant; parse requires_terrarium_api, retire min_terrarium"
```

---

### Task 2: discovery-time API gate + the three in-repo manifests

**Files:**
- Modify: `control/bit_registry.py` (`scan()`), `bits/test/bit.toml`, `bits/metronome/bit.toml`, `bits/capture/bit.toml`
- Test: `tests/test_bit_registry.py`

**Interfaces:**
- Consumes: `BitIdentity.requires_terrarium_api` (Task 1), `control.api_version.TERRARIUM_API`.
- Produces: `scan()` refuses (as a located `PackageError`, package-scoped) any manifest whose `requires_terrarium_api` is absent or != `TERRARIUM_API`. All three in-repo bit.tomls carry `requires_terrarium_api = 1`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_bit_registry.py`; follow its existing tmp-root fixture pattern for writing throwaway packages — it already builds `root/<pkg>/bit.toml` trees in tmp_path):

```python
def _manifest(name: str, api_line: str = "requires_terrarium_api = 1") -> str:
    return f"""
[bit]
name = "{name}"
entry = "{name.lower()}:{name}"
{api_line}
"""


def test_matching_api_version_discovers(tmp_path):
    pkg = tmp_path / "ok"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(_manifest("Ok"))
    reg = BitRegistry.scan((tmp_path,))
    assert "Ok" in reg.packages and not reg.errors


def test_missing_api_key_is_located_package_error(tmp_path):
    pkg = tmp_path / "old"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(_manifest("Old", api_line=""))
    reg = BitRegistry.scan((tmp_path,))
    assert "Old" not in reg.packages
    assert len(reg.errors) == 1
    err = reg.errors[0]
    assert "requires_terrarium_api" in err.message
    assert err.path.endswith("old/bit.toml")


def test_wrong_api_version_names_both_numbers(tmp_path):
    pkg = tmp_path / "future"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(
        _manifest("Future", api_line="requires_terrarium_api = 2"))
    reg = BitRegistry.scan((tmp_path,))
    assert "Future" not in reg.packages
    assert "2" in reg.errors[0].message and "1" in reg.errors[0].message


def test_api_refusal_is_package_scoped(tmp_path):
    for name, line in (("Good", "requires_terrarium_api = 1"),
                       ("Bad", "requires_terrarium_api = 99")):
        pkg = tmp_path / name.lower()
        pkg.mkdir()
        (pkg / "bit.toml").write_text(_manifest(name, api_line=line))
    reg = BitRegistry.scan((tmp_path,))
    assert "Good" in reg.packages and "Bad" not in reg.packages
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_bit_registry.py -q -k api`
Expected: FAIL (packages discover despite missing/wrong key).

- [ ] **Step 3: Implement**

In `control/bit_registry.py`: `from control.api_version import TERRARIUM_API` at the top. In `scan()`, immediately after the successful `parse_manifest` (before the `import_root` line), insert:

```python
                declared = config.identity.requires_terrarium_api
                if declared is None:
                    registry.errors.append(PackageError(
                        path=source,
                        message="[bit.requires_terrarium_api] required as of "
                                f"Terrarium API v{TERRARIUM_API}"))
                    continue
                if declared != TERRARIUM_API:
                    registry.errors.append(PackageError(
                        path=source,
                        message=f"requires Terrarium API {declared}, this "
                                f"engine provides {TERRARIUM_API}"))
                    continue
```

Then add `requires_terrarium_api = 1` to the `[bit]` table of all three in-repo manifests (`bits/test/bit.toml`, `bits/metronome/bit.toml`, `bits/capture/bit.toml`) — without this the whole suite goes red, since many tests scan the default root.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green. If any test writes its own fixture bit.toml and now fails on the missing key, add `requires_terrarium_api = 1` to that fixture manifest — that churn is expected and correct (one rule for every root, spec 2.2).

- [ ] **Step 5: Commit**

```bash
git add control/bit_registry.py bits/*/bit.toml tests/
git commit -m "feat(bits): enforce requires_terrarium_api at discovery; stamp in-repo manifests"
```

---

### Task 3: `[assets]` validation (parse-level shape, discovery-level existence/escape)

**Files:**
- Modify: `control/bit_config.py` (the `assets_raw` block in `parse_manifest`), `control/bit_registry.py` (`scan()`)
- Test: `tests/test_bit_config.py`, `tests/test_bit_registry.py`

**Interfaces:**
- Produces: `parse_manifest` refuses non-string, absolute, or `..`-containing asset values (located `ManifestError`, key `assets.<name>`). `scan()` refuses a package whose declared asset file is missing or resolves outside the package dir (located `PackageError`). `BitConfig.assets` stays `tuple[tuple[str, str], ...]` of package-relative paths.
- Consumes: Task 2's scan structure (insert the asset check right after the API gate).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bit_config.py`:

```python
def test_assets_parse_as_sorted_pairs():
    text = MINIMAL + '\n[assets]\nchime = "assets/chime.wav"\n'
    config = parse_manifest(text, source="t")
    assert config.assets == (("chime", "assets/chime.wav"),)


def test_asset_absolute_path_refused():
    text = MINIMAL + '\n[assets]\nchime = "/etc/passwd"\n'
    with pytest.raises(ManifestError) as exc:
        parse_manifest(text, source="t")
    assert "assets.chime" in str(exc.value)


def test_asset_parent_escape_refused():
    text = MINIMAL + '\n[assets]\nchime = "../outside.wav"\n'
    with pytest.raises(ManifestError):
        parse_manifest(text, source="t")


def test_asset_non_string_value_refused():
    text = MINIMAL + "\n[assets]\nchime = 3\n"
    with pytest.raises(ManifestError):
        parse_manifest(text, source="t")
```

Append to `tests/test_bit_registry.py` (reuses Task 2's `_manifest` helper):

```python
def test_declared_asset_must_exist(tmp_path):
    pkg = tmp_path / "a"
    pkg.mkdir()
    (pkg / "bit.toml").write_text(
        _manifest("A") + '\n[assets]\nchime = "assets/chime.wav"\n')
    reg = BitRegistry.scan((tmp_path,))
    assert "A" not in reg.packages
    assert "chime" in reg.errors[0].message


def test_present_asset_discovers(tmp_path):
    pkg = tmp_path / "a"
    (pkg / "assets").mkdir(parents=True)
    (pkg / "assets" / "chime.wav").write_bytes(b"RIFF")
    (pkg / "bit.toml").write_text(
        _manifest("A") + '\n[assets]\nchime = "assets/chime.wav"\n')
    reg = BitRegistry.scan((tmp_path,))
    assert "A" in reg.packages and not reg.errors


def test_symlink_escape_refused(tmp_path):
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"RIFF")
    pkg = tmp_path / "a"
    (pkg / "assets").mkdir(parents=True)
    (pkg / "assets" / "chime.wav").symlink_to(outside)
    (pkg / "bit.toml").write_text(
        _manifest("A") + '\n[assets]\nchime = "assets/chime.wav"\n')
    reg = BitRegistry.scan((tmp_path,))
    assert "A" not in reg.packages
    assert "escapes" in reg.errors[0].message
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_bit_config.py tests/test_bit_registry.py -q -k asset`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `parse_manifest`, replace the two `assets_raw` lines with:

```python
    assets_raw = _get(doc, "assets", dict, {}, source=source, prefix="")
    assets_items = []
    for akey, aval in sorted(assets_raw.items()):
        if not isinstance(aval, str):
            raise ManifestError(
                source=source, key=f"assets.{akey}",
                message=f"expected str path, got {type(aval).__name__}")
        p = PurePosixPath(aval)
        if p.is_absolute() or ".." in p.parts:
            raise ManifestError(
                source=source, key=f"assets.{akey}",
                message="must be a package-relative path with no '..'")
        assets_items.append((akey, aval))
    assets = tuple(assets_items)
```

(add `from pathlib import PurePosixPath` to the imports.)

In `control/bit_registry.py`'s `scan()`, right after the Task 2 API gate block:

```python
                asset_error = None
                for akey, rel in config.assets:
                    target = pkg_dir / rel
                    if not target.is_file():
                        asset_error = f"declared asset {akey!r} missing: {rel}"
                        break
                    resolved = target.resolve()
                    if not resolved.is_relative_to(pkg_dir.resolve()):
                        asset_error = (f"declared asset {akey!r} escapes the "
                                       f"package directory: {rel}")
                        break
                if asset_error is not None:
                    registry.errors.append(
                        PackageError(path=source, message=asset_error))
                    continue
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green (no shipped manifest declares assets).

- [ ] **Step 5: Commit**

```bash
git add control/bit_config.py control/bit_registry.py tests/
git commit -m "feat(bits): validate [assets] at parse and discovery (relative, present, non-escaping)"
```

---

### Task 4: asset resolution only through config (`asset_path`)

**Files:**
- Modify: `control/bit_config.py` (BitConfig), `control/bit_registry.py` (`BitPackage`, `resolve_config`)
- Test: `tests/test_bit_registry.py`

**Interfaces:**
- Produces: `BitConfig.assets_root: Path | None = None` (not manifest-parsed; stamped by the registry); `BitConfig.asset_path(key: str) -> Path` raising `ManifestError` on unknown key or no root; `BitPackage.asset_path(key) -> Path`; `BitRegistry.resolve_config(name, overrides)` returns a config with `assets_root` set to the package dir in BOTH the no-override and override branches.
- Consumes: Task 3's validated `BitConfig.assets`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_bit_registry.py`):

```python
def _asset_pkg(tmp_path):
    pkg = tmp_path / "a"
    (pkg / "assets").mkdir(parents=True)
    (pkg / "assets" / "chime.wav").write_bytes(b"RIFF")
    (pkg / "bit.toml").write_text(
        _manifest("A") + '\n[assets]\nchime = "assets/chime.wav"\n')
    return pkg


def test_resolve_config_stamps_assets_root(tmp_path):
    pkg = _asset_pkg(tmp_path)
    reg = BitRegistry.scan((tmp_path,))
    config = reg.resolve_config("A")
    assert config.asset_path("chime") == pkg / "assets" / "chime.wav"
    # override branch stamps too
    config2 = reg.resolve_config("A", {"console": {"notes": "x"}})
    assert config2.asset_path("chime") == pkg / "assets" / "chime.wav"


def test_bit_package_asset_path(tmp_path):
    pkg = _asset_pkg(tmp_path)
    reg = BitRegistry.scan((tmp_path,))
    assert reg.packages["A"].asset_path("chime") == pkg / "assets" / "chime.wav"


def test_asset_path_unknown_key_raises(tmp_path):
    _asset_pkg(tmp_path)
    reg = BitRegistry.scan((tmp_path,))
    with pytest.raises(ManifestError) as exc:
        reg.resolve_config("A").asset_path("nope")
    assert "nope" in str(exc.value)


def test_asset_path_with_no_root_raises():
    config = parse_manifest(
        '[bit]\nname = "X"\nentry = "x:X"\nrequires_terrarium_api = 1\n'
        '\n[assets]\nchime = "assets/chime.wav"\n', source="t")
    with pytest.raises(ManifestError):
        config.asset_path("chime")
```

(import `parse_manifest` and `ManifestError` from `control.bit_config` at the top of the test file if not already imported.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_bit_registry.py -q -k asset_path`
Expected: FAIL (`asset_path` not defined).

- [ ] **Step 3: Implement**

`control/bit_config.py`: add to `BitConfig` (needs `from pathlib import Path`):

```python
    # Stamped by BitRegistry.resolve_config, never parsed from the manifest:
    # the absolute package directory asset paths resolve against. None means
    # "location unknown" and asset_path refuses rather than guessing.
    assets_root: "Path | None" = None

    def asset_path(self, key: str) -> "Path":
        for akey, rel in self.assets:
            if akey == key:
                if self.assets_root is None:
                    raise ManifestError(
                        source=self.identity.name, key=f"assets.{key}",
                        message="no assets_root: config was not resolved "
                                "through a BitRegistry")
                return self.assets_root / rel
        raise ManifestError(
            source=self.identity.name, key=f"assets.{key}",
            message=f"no such asset; declared: "
                    f"{sorted(k for k, _ in self.assets)}")
```

`control/bit_registry.py`:

- `BitPackage` gains:

```python
    def asset_path(self, key: str) -> Path:
        return self.resolved_config().asset_path(key)

    def resolved_config(self) -> BitConfig:
        from dataclasses import replace
        return replace(self.config, assets_root=self.path)
```

- `resolve_config` becomes:

```python
    def resolve_config(self, name: str, overrides: dict | None = None) -> BitConfig:
        pkg = self.packages[name]
        if not overrides:
            return pkg.resolved_config()
        merged = merge_overrides(pkg.config, overrides,
                                  source=str(pkg.path / "bit.toml"))
        from dataclasses import replace
        return replace(merged, assets_root=pkg.path)
```

(Move the `replace` import to module top with the other imports; shown inline here only for locality.)

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add control/bit_config.py control/bit_registry.py tests/test_bit_registry.py
git commit -m "feat(bits): asset_path resolution stamped through resolve_config only"
```

---

### Task 5: `tools/bundle_bit.py` — `bundle` and `verify`

**Files:**
- Create: `tools/bundle_bit.py`
- Test: `tests/test_bundle_bit.py`

**Interfaces:**
- Produces (module-level, importable for tests and for Task 6):
  - `bundle(pkg_dir: Path, out: Path | None = None) -> Path` (returns archive path; default name `<name>-<version>.mmbit` beside pkg_dir; version-less manifests use `<name>.mmbit`)
  - `verify(archive: Path, *, terrarium_api: int | None = None) -> list[str]` (empty list = clean; non-empty = human-readable discrepancies; API mismatch is returned as a warning string prefixed `"warning: "` and does NOT make verify fail)
  - `BUNDLE_MANIFEST = "BUNDLE.json"`; `EXCLUDE_DIRS = {"__pycache__", ".git"}`; files excluded: `*.pyc` and dotfiles.
  - `main(argv)` argparse CLI: `bundle <pkg-dir> [-o OUT]`, `verify <archive>`; exit 0 clean / 1 refusal.
- Consumes: `control.bit_config.parse_manifest` + Task 3's asset rules (bundle refuses a package discovery would refuse), `control.api_version.TERRARIUM_API`.

- [ ] **Step 1: Write the failing tests** (`tests/test_bundle_bit.py`):

```python
import json
import zipfile
from pathlib import Path

import pytest

from tools.bundle_bit import BUNDLE_MANIFEST, bundle, verify

MANIFEST = """
[bit]
name = "GlowBit"
version = "1.0.0"
entry = "glow_bit:GlowBit"
kind = "ambient"
requires_terrarium_api = 1

[assets]
palette = "assets/palette.json"
"""


@pytest.fixture
def pkg(tmp_path):
    pkg = tmp_path / "GlowBit"
    (pkg / "assets").mkdir(parents=True)
    (pkg / "bit.toml").write_text(MANIFEST)
    (pkg / "glow_bit.py").write_text("class GlowBit:\n    pass\n")
    (pkg / "assets" / "palette.json").write_text('{"hue": 0.6}')
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "junk.pyc").write_bytes(b"x")
    (pkg / ".DS_Store").write_bytes(b"x")
    return pkg


def test_bundle_names_archive_and_excludes_junk(pkg):
    archive = bundle(pkg)
    assert archive.name == "GlowBit-1.0.0.mmbit"
    names = set(zipfile.ZipFile(archive).namelist())
    assert names == {"bit.toml", "glow_bit.py", "assets/palette.json",
                     BUNDLE_MANIFEST}


def test_bundle_manifest_carries_hashes_and_provenance(pkg):
    archive = bundle(pkg)
    meta = json.loads(zipfile.ZipFile(archive).read(BUNDLE_MANIFEST))
    assert meta["name"] == "GlowBit"
    assert meta["version"] == "1.0.0"
    assert meta["requires_terrarium_api"] == 1
    assert set(meta["files"]) == {"bit.toml", "glow_bit.py",
                                  "assets/palette.json"}
    assert all(len(h) == 64 for h in meta["files"].values())
    assert "created" in meta and "bundler" in meta


def test_bundle_refuses_broken_package(pkg):
    (pkg / "assets" / "palette.json").unlink()
    with pytest.raises(SystemExit):
        bundle(pkg)


def test_verify_clean(pkg):
    assert verify(bundle(pkg)) == []


def test_verify_refuses_tampered_member(pkg):
    archive = bundle(pkg)
    # rewrite one member with different bytes
    tampered = archive.with_suffix(".tampered.mmbit")
    with zipfile.ZipFile(archive) as zin, \
         zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "glow_bit.py":
                data = b"class GlowBit:\n    EVIL = True\n"
            zout.writestr(item, data)
    problems = verify(tampered)
    assert problems and any("glow_bit.py" in p for p in problems)


def test_verify_refuses_unlisted_member(pkg):
    archive = bundle(pkg)
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("extra.py", "x = 1\n")
    problems = verify(archive)
    assert any("extra.py" in p for p in problems)


def test_verify_warns_not_fails_on_api_mismatch(pkg):
    archive = bundle(pkg)
    problems = verify(archive, terrarium_api=2)
    assert problems and all(p.startswith("warning:") for p in problems)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_bundle_bit.py -q`
Expected: FAIL (`ModuleNotFoundError: tools.bundle_bit`). If `tools/` lacks an `__init__.py` and the import fails for that reason, create an empty `tools/__init__.py` (check how `tests/` import `tools/trace_stats.py` first and follow that pattern).

- [ ] **Step 3: Implement** (`tools/bundle_bit.py`):

```python
"""Bundle, verify, and install Bit packages as single .mmbit archives.

Offline CLI, peer of tools/trace_stats.py; never imported by the runtime.
Format and rules: docs/superpowers/specs/
2026-08-28-external-and-bundled-bits-design.md section 5. sha256 gives
integrity, not authenticity; install bundles only from sources you trust.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import socket
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from control.api_version import TERRARIUM_API
from control.bit_config import ManifestError, parse_manifest

BUNDLE_MANIFEST = "BUNDLE.json"
EXCLUDE_DIRS = {"__pycache__", ".git"}


def _package_files(pkg_dir: Path) -> list[Path]:
    out = []
    for path in sorted(pkg_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(pkg_dir)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name.startswith(".") or rel.suffix == ".pyc":
            continue
        out.append(rel)
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_commit(pkg_dir: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(pkg_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10)
    except OSError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def bundle(pkg_dir: Path, out: Path | None = None) -> Path:
    pkg_dir = pkg_dir.resolve()
    manifest_path = pkg_dir / "bit.toml"
    if not manifest_path.is_file():
        sys.exit(f"refusing to bundle: no bit.toml in {pkg_dir}")
    try:
        config = parse_manifest(manifest_path.read_text(),
                                 source=str(manifest_path))
    except ManifestError as exc:
        sys.exit(f"refusing to bundle a package discovery would refuse: {exc}")
    if config.identity.requires_terrarium_api is None:
        sys.exit("refusing to bundle: [bit.requires_terrarium_api] missing")
    for akey, rel in config.assets:
        target = pkg_dir / rel
        if not target.is_file():
            sys.exit(f"refusing to bundle: declared asset {akey!r} "
                     f"missing: {rel}")
        if not target.resolve().is_relative_to(pkg_dir):
            sys.exit(f"refusing to bundle: declared asset {akey!r} escapes "
                     f"the package directory: {rel}")

    name = config.identity.name
    version = config.identity.version
    default_name = f"{name}-{version}.mmbit" if version else f"{name}.mmbit"
    archive = (out or pkg_dir.parent / default_name).resolve()

    files = _package_files(pkg_dir)
    meta = {
        "name": name,
        "version": version,
        "requires_terrarium_api": config.identity.requires_terrarium_api,
        "created": datetime.now(timezone.utc).isoformat(),
        "bundler": f"{getpass.getuser()}@{socket.gethostname()}",
        "files": {str(rel): _sha256(pkg_dir / rel) for rel in files},
    }
    commit = _source_commit(pkg_dir)
    if commit:
        meta["source_commit"] = commit

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(pkg_dir / rel, str(rel))
        z.writestr(BUNDLE_MANIFEST, json.dumps(meta, indent=2, sort_keys=True))
    return archive


def verify(archive: Path, *, terrarium_api: int | None = None) -> list[str]:
    api = TERRARIUM_API if terrarium_api is None else terrarium_api
    problems: list[str] = []
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        if BUNDLE_MANIFEST not in names:
            return [f"no {BUNDLE_MANIFEST} in archive"]
        meta = json.loads(z.read(BUNDLE_MANIFEST))
        listed = dict(meta.get("files", {}))
        for member in sorted(names - {BUNDLE_MANIFEST}):
            if member.endswith("/"):
                continue
            if member not in listed:
                problems.append(f"member not in manifest: {member}")
                continue
            digest = hashlib.sha256(z.read(member)).hexdigest()
            if digest != listed.pop(member):
                problems.append(f"hash mismatch: {member}")
        for missing in sorted(listed):
            problems.append(f"manifest entry missing from archive: {missing}")
        declared = meta.get("requires_terrarium_api")
        if not problems and declared != api:
            problems.append(
                f"warning: bundle requires Terrarium API {declared}, "
                f"this checkout provides {api}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_bundle = sub.add_parser("bundle")
    p_bundle.add_argument("pkg_dir", type=Path)
    p_bundle.add_argument("-o", "--out", type=Path, default=None)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("archive", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "bundle":
        archive = bundle(args.pkg_dir, args.out)
        print(archive)
        return 0
    problems = verify(args.archive)
    for p in problems:
        print(p, file=sys.stderr)
    hard = [p for p in problems if not p.startswith("warning:")]
    if not hard:
        print(f"{args.archive}: OK")
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note the verify/API subtlety: a hard problem list suppresses the API comparison (hashes first); the CLI exits 0 on warnings-only, matching spec 5.2 ("warn, not refuse").

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_bundle_bit.py -q` then `.venv/bin/python -m pytest tests -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add tools/bundle_bit.py tests/test_bundle_bit.py
git commit -m "feat(tools): bundle_bit bundle+verify (.mmbit, sha256 manifest, provenance)"
```

---

### Task 6: `bundle_bit.py install` + full round-trip

**Files:**
- Modify: `tools/bundle_bit.py`
- Test: `tests/test_bundle_bit.py`

**Interfaces:**
- Produces: `install(archive: Path, root: Path, *, force: bool = False) -> Path` (returns installed package dir). Runs the full `verify` first (hard problems abort via `SystemExit`; warnings print, proceed). Zip-slip guarded. Existing target dir refused without `force`; with `force`, replaced atomically (unpack beside as `<name>.installing`, swap, remove old). `BUNDLE.json` is installed too. CLI subcommand `install <archive> <root> [--force]`.
- Consumes: Task 5's `bundle`/`verify`; Task 2's discovery gate (round-trip test).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_bundle_bit.py`):

```python
from control.bit_registry import BitRegistry
from tools.bundle_bit import install


def test_install_roundtrip_discovers_and_loads(pkg, tmp_path):
    archive = bundle(pkg)
    root = tmp_path / "installed"
    root.mkdir()
    dest = install(archive, root)
    assert dest == root / "GlowBit"
    assert (dest / "BUNDLE.json").is_file()
    reg = BitRegistry.scan((root,))
    assert "GlowBit" in reg.packages and not reg.errors
    cls = reg.bit_class("GlowBit")
    assert cls.__name__ == "GlowBit"


def test_install_refuses_existing_without_force(pkg, tmp_path):
    archive = bundle(pkg)
    root = tmp_path / "installed"
    (root / "GlowBit").mkdir(parents=True)
    with pytest.raises(SystemExit):
        install(archive, root)
    install(archive, root, force=True)          # replaces
    assert (root / "GlowBit" / "glow_bit.py").is_file()


def test_install_refuses_tampered(pkg, tmp_path):
    archive = bundle(pkg)
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("extra.py", "x = 1\n")
    root = tmp_path / "installed"
    root.mkdir()
    with pytest.raises(SystemExit):
        install(archive, root)


def test_install_refuses_zip_slip(pkg, tmp_path):
    archive = bundle(pkg)
    evil = archive.with_suffix(".evil.mmbit")
    with zipfile.ZipFile(archive) as zin, \
         zipfile.ZipFile(evil, "w") as zout:
        meta = json.loads(zin.read(BUNDLE_MANIFEST))
        payload = b"pwn"
        meta["files"]["../pwn.py"] = hashlib.sha256(payload).hexdigest()
        for item in zin.infolist():
            if item.filename != BUNDLE_MANIFEST:
                zout.writestr(item, zin.read(item.filename))
        zout.writestr("../pwn.py", payload)
        zout.writestr(BUNDLE_MANIFEST, json.dumps(meta))
    root = tmp_path / "installed"
    root.mkdir()
    with pytest.raises(SystemExit):
        install(evil, root)
    assert not (tmp_path / "pwn.py").exists()
```

(add `import hashlib` to the test imports.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_bundle_bit.py -q -k install`
Expected: FAIL (`install` not defined).

- [ ] **Step 3: Implement** (append to `tools/bundle_bit.py`; `import shutil` at top):

```python
def _safe_members(z: zipfile.ZipFile) -> list[str]:
    members = []
    for member in z.namelist():
        if member.endswith("/"):
            continue
        p = Path(member)
        if p.is_absolute() or ".." in p.parts:
            sys.exit(f"refusing to install: unsafe member path {member!r}")
        members.append(member)
    return members


def install(archive: Path, root: Path, *, force: bool = False) -> Path:
    problems = verify(archive)
    hard = [p for p in problems if not p.startswith("warning:")]
    for p in problems:
        print(p, file=sys.stderr)
    if hard:
        sys.exit(f"refusing to install {archive}: verification failed")

    with zipfile.ZipFile(archive) as z:
        meta = json.loads(z.read(BUNDLE_MANIFEST))
        name = meta["name"]
        dest = root / name
        if dest.exists() and not force:
            sys.exit(f"refusing to install: {dest} exists (use --force)")
        staging = root / f"{name}.installing"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for member in _safe_members(z):
            target = staging / member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(member))
    old = root / f"{name}.replaced"
    if dest.exists():
        dest.rename(old)
    staging.rename(dest)
    if old.exists():
        shutil.rmtree(old)
    return dest
```

CLI: in `main()`, add:

```python
    p_install = sub.add_parser("install")
    p_install.add_argument("archive", type=Path)
    p_install.add_argument("root", type=Path)
    p_install.add_argument("--force", action="store_true")
```

and the dispatch branch:

```python
    if args.cmd == "install":
        print(install(args.archive, args.root, force=args.force))
        return 0
```

(Note `_safe_members` runs against the archive listing before any byte is written, and the zip-slip member also fails `verify` as "member not in manifest" unless the attacker forged the manifest too — the belt is the path check, the manifest is the suspenders. The round-trip discovery works because Task 3's registry check ignores `BUNDLE.json` — `scan()` only reads `bit.toml`.)

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_bundle_bit.py -q && .venv/bin/python -m pytest tests -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add tools/bundle_bit.py tests/test_bundle_bit.py
git commit -m "feat(tools): bundle_bit install (verify-first, zip-slip guarded, atomic --force replace)"
```

---

### Task 7: mm-tuneshroom `bits/GlowBit` + `bits/README.md` (cross-repo)

**Files (in `/Users/chris/projects/mm-tuneshroom`, NOT this repo):**
- Create: `bits/GlowBit/bit.toml`, `bits/GlowBit/glow_bit.py`, `bits/GlowBit/assets/palette.json`, `bits/README.md`

**Interfaces:**
- Consumes: the full Spec 4 engine surface (Tasks 1-4) plus mm-terrarium's `Bit`, `RoleTable`, `Function` APIs.
- Produces: the reference external Bit package; discovered by pointing `terrarium.toml`'s `bit_paths` at the checkout's `bits/` dir. No mm-terrarium code change.

**Process:** work in the mm-tuneshroom checkout on a new branch `claude/spec4-glowbit` off its default branch (`cd` is fine there — it is a different repo, not the main mm-terrarium checkout). This lands as its own PR in mm-tuneshroom. No pytest exists there; verification runs from the mm-terrarium worktree (step 3).

- [ ] **Step 1: Create the package**

`bits/GlowBit/bit.toml`:

```toml
[bit]
name = "GlowBit"
version = "1.0.0"
description = "Reference external Bit: ambient room glow driven by a declared generator, parameterized by an [assets] palette"
entry = "glow_bit:GlowBit"
kind = "ambient"
author = "Musical Mycology"
requires_terrarium_api = 1

[launch]
room_types = ["TEST", "DEMO"]
default_room_type = "TEST"

[start]
when = "immediate"

[console]
display_name = "Glow"
notes = "External reference Bit living in mm-tuneshroom/bits. No player roles; the room breathes on its own."

[assets]
palette = "assets/palette.json"
```

`bits/GlowBit/assets/palette.json`:

```json
{"hue": 0.62, "level": 0.5, "drift_period_seconds": 45.0}
```

`bits/GlowBit/glow_bit.py`:

```python
"""GlowBit: the reference EXTERNAL Bit package (mm-tuneshroom/bits).

Demonstrates every Spec 4 mechanism: requires_terrarium_api in bit.toml,
an [assets] file read ONLY via config.asset_path() (never __file__), and
a package layout bundle_bit.py can archive. Stdlib + mm-terrarium imports
only -- external Bits are not allowed third-party dependencies.
"""

import json

from control.bit import Bit
from control.functions import Function, FunctionKind, FunctionTable, GeneratorSpec
from control.roles import RoleTable
from control.cues import ROOM


class GlowBit(Bit):
    version = "1.0.0"

    def __init__(self, config=None):
        super().__init__(config)
        palette = {"hue": 0.62, "level": 0.5, "drift_period_seconds": 45.0}
        if config is not None and config.assets:
            palette.update(json.loads(
                config.asset_path("palette").read_text()))
        self._palette = palette
        self.role_table = RoleTable(roles={}, node_fallbacks={})

    def room_manifests(self):
        light = {
            "instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": self._palette["hue"],
                            "level": self._palette["level"]},
                 "lanes": [{"source": "cc:74", "dest": "hue"}]},
            ],
        }
        return light, {}

    def function_table(self):
        return FunctionTable(functions={
            "glow_drift": Function(
                name="glow_drift",
                description="Slow ambient hue drift across the Room",
                kind=FunctionKind.GENERATOR,
                generator=GeneratorSpec(
                    dev=ROOM, status=0xB0, data1=74, waveform="triangle",
                    period=self._palette["drift_period_seconds"],
                    lo=0, hi=127),
            ),
        })

    def update(self, dt):
        return False        # ambient: never self-completes; operator unloads
```

IMPORTANT for the implementer: before committing, check the exact import locations and constructor signatures against the mm-terrarium worktree — `ROOM` may live in `control.cues` or `control.functions` (grep for `^ROOM = ` / `from control.cues import`), `RoleTable`'s constructor args may differ (read `control/roles.py`), and `Bit.__init__`'s signature is in `control/bit.py`. `bits/test/test_bit.py` is the working exemplar for all three; mirror it. Adjust the code above to what the tree actually exports rather than trusting this listing.

`bits/README.md`:

```markdown
# Terrarium Bits (external packages)

Terrarium-side Bit packages that ship with the instrument they are
written for. These are Python packages executed by the mm-terrarium
venue server, NOT part of the Dart app: the app never imports them, and
they are consumed only through `terrarium.toml`'s `bit_paths`. This
directory deliberately revises the earlier "no Terrarium-side logic in
mm-tuneshroom" boundary; see mm-terrarium's
`docs/superpowers/specs/2026-08-28-external-and-bundled-bits-design.md`
section 4.

## The contract

- One directory per package: `bits/<BitName>/bit.toml` at its root.
- `bit.toml` must declare `requires_terrarium_api` (currently `1`);
  a mismatched engine refuses the package at discovery.
- Package files a Bit needs at runtime are declared in `[assets]` and
  read ONLY via `config.asset_path(key)`. Never `__file__`-relative.
- Bits import only the Python stdlib and mm-terrarium's own modules.
  No third-party dependencies.
- Bundle for distribution with mm-terrarium's `tools/bundle_bit.py`:
  `bundle` -> `verify` -> `install`. sha256 gives integrity, not
  authenticity: install bundles only from sources you trust.

## Wiring a Terrarium at this checkout

In `terrarium.toml`:

    bit_paths = ["bits", "/Users/chris/projects/mm-tuneshroom/bits"]
```

- [ ] **Step 2: Verify from the mm-terrarium worktree (offline)**

Run from the mm-terrarium worktree:

```bash
.venv/bin/python -c "
from pathlib import Path
from control.bit_registry import BitRegistry
reg = BitRegistry.scan((Path('/Users/chris/projects/mm-tuneshroom/bits'),))
print('packages:', sorted(reg.packages))
print('errors:', reg.errors)
cls = reg.bit_class('GlowBit')
bit = cls(config=reg.resolve_config('GlowBit'))
light, ugen = bit.room_manifests()
print('light instruments:', [i['instrument'] for i in light['instruments']])
print('functions:', sorted(bit.function_table().functions))
print('palette hue:', bit._palette['hue'])
"
```

Expected: `packages: ['GlowBit']`, no errors, `functions: ['glow_drift']`, `palette hue: 0.62`. Fix any import/signature drift the run surfaces (see the IMPORTANT note above).

Also bundle it once for real: `.venv/bin/python tools/bundle_bit.py bundle /Users/chris/projects/mm-tuneshroom/bits/GlowBit -o /tmp/GlowBit-1.0.0.mmbit && .venv/bin/python tools/bundle_bit.py verify /tmp/GlowBit-1.0.0.mmbit` -> `OK`.

- [ ] **Step 3: Commit + PR in mm-tuneshroom**

```bash
cd /Users/chris/projects/mm-tuneshroom
git checkout -b claude/spec4-glowbit
git add bits/
git commit -m "feat(bits): GlowBit reference external Bit + bits/ contract README (Terrarium Spec 4)"
git push -u origin claude/spec4-glowbit
gh pr create --title "GlowBit reference external Bit (Terrarium Spec 4)" --body "Seeds bits/ per mm-terrarium Spec 4 (external and bundled Bits): the reference external package + the bits/ contract README. Deliberate revision of the no-Terrarium-side-logic boundary; see the spec, section 4."
```

Do NOT merge that PR; report its URL. (Chris merges cross-repo PRs.)

---

### Task 8: docs — deep-dive entry, relationships rewording, spec status

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (new landed-subsystems entry after the Spec 3 entry; the mm-tuneshroom bullet under "Relationships to other repos"; test-baseline number), `docs/superpowers/specs/2026-08-28-external-and-bundled-bits-design.md` (Status section)

**Interfaces:** none (prose only). No em dashes in new prose (house rule; the file's existing style uses `--`).

- [ ] **Step 1: Add the landed-subsystems entry**

Insert after the Spec 3 entry (`### control/functions.py, ... (2026-08-27)`), following the established entry shape: heading `### control/api_version.py, requires_terrarium_api, [assets], tools/bundle_bit.py -- external and bundled Bits (2026-08-28)`, naming: Spec 4 of the restructure with a link to the spec and plan; the discovery-time API gate (equality, package-scoped located refusal, min_terrarium retired to the unknown-key warn path); [assets] validated at discovery and resolved only via `config.asset_path()` (`resolve_config` stamps `assets_root`; the never-guesses rule); `tools/bundle_bit.py` bundle/verify/install with the .mmbit format, per-file sha256 + provenance `BUNDLE.json`, zip-slip guard, atomic `--force` replace, and the integrity-not-authenticity limit; the sys.path-over-venv decision with the no-third-party-deps constraint and the recorded revisit trigger; GlowBit at `mm-tuneshroom/bits/GlowBit` as the reference external package (cross-repo PR, link it); and the new test-baseline line (fill in the actual `pytest -q` count at execution time, "up from 1634 passed, 1 skipped"). End with: the Spec 1-4 live-Arco checklists are all still pending, run them together.

- [ ] **Step 2: Reword the mm-tuneshroom relationship bullet**

In "Relationships to other repos", the mm-tuneshroom bullet currently says "it never contains Terrarium-side logic". Replace that clause with the revised boundary (spec section 4): the *application* (Dart app, web build, native harness) never contains Terrarium-side logic; as of Spec 4 (2026-08-28) the repo also hosts `bits/` -- Terrarium-side Bit packages that ship with the instrument they target, consumed only through `bit_paths`, never imported by the app. Keep the rest of the bullet intact.

- [ ] **Step 3: Update the spec's Status section**

In the Spec 4 design doc, replace `Status: draft, awaiting review` header line with `Status: approved 2026-08-28` and rewrite the bottom `## Status` section: spec approved and implemented 2026-08-28; record any execution deviations discovered during Tasks 1-7 (there will be some -- e.g. GlowBit import corrections); note the live checklist (section 8) remains unrun, queued behind Spec 1/2/3's.

- [ ] **Step 4: Run the full suite one more time**

Run: `.venv/bin/python -m pytest tests -q`
Expected: green; record the final count in the deep-dive entry.

- [ ] **Step 5: Commit**

```bash
git add docs/MM_TERRARIUM.md docs/superpowers/specs/2026-08-28-external-and-bundled-bits-design.md
git commit -m "docs(terrarium): external and bundled Bits slice landed"
```

---

## Self-review notes (done at planning time)

- Spec coverage: 2.1/2.2 -> Tasks 1-2; 2.3 -> Task 1; 3 -> Tasks 3-4; 4 -> Task 7 (+ Task 8 doc reword; shipped terrarium.toml unchanged per spec); 5 -> Tasks 5-6; 6 covered across tasks; 7 -> each task's tests; 8 stays a live checklist (unrun by design); 9 out of scope.
- Type consistency: `requires_terrarium_api: int | None` (Task 1) is what Task 2 gates on; `BitConfig.assets` stays `(key, relpath)` pairs (Task 3) which Task 4's `asset_path` iterates; Task 5's `bundle`/`verify` signatures are what Task 6's `install` and tests consume.
- Known risk, called out where it bites: Task 7's GlowBit listing may drift from the tree's real import surface; the task instructs verifying against `bits/test/test_bit.py` and the live scan harness before committing.
