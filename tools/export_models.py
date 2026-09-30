"""Exports every published instrument's fresh baked model into an
mm-tuneshroom checkout, and copies the shared fixture pair beside its
tests (spec: mm-tuneshroom docs/superpowers/specs/
2026-09-28-3d-tuneshroom-model-and-view-design.md section 5.4). A
sibling of tools/export_solo.py and tools/export_contract.py.

    .venv/bin/python -m tools.export_models /path/to/mm-tuneshroom

Run from an mm-terrarium checkout at origin/main; commit the result in
mm-tuneshroom in its own commit naming the provenance commit. Nothing is
written unless every declared model exports.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from control.catalog import load_catalog
from control.model_layout import layout_to_json
from control.terrarium_config import TerrariumConfigError
from tools.model_bake_helpers import BakeContractError, bake_output_path, validate_baked_glb

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_VERSION = "export_models/1"
APP_PACKAGE = "mm_shrooms_app"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "models"
FIXTURE_FILES = ("marker_fixture.mmbake.glb", "expected_layout.json")


class ExportModelsError(Exception):
    pass


def _head_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=12", "HEAD"],
            text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _check_target(out_repo: Path) -> None:
    try:
        text = (out_repo / "pubspec.yaml").read_text(encoding="utf-8")
    except OSError:
        text = ""
    if not re.search(rf"^name:\s*{APP_PACKAGE}\s*$", text, re.MULTILINE):
        raise ExportModelsError(
            f"{out_repo} is not an mm-tuneshroom checkout (no pubspec.yaml "
            f"with name: {APP_PACKAGE})")


def _model_path(instruments_root: Path, name: str) -> Path:
    """The instrument's declared model, resolved as the catalog resolves a
    published entry's (against instruments/). The catalog load that
    produced `name` has already confined and parsed this path."""
    raw = tomllib.loads((instruments_root / f"{name}.toml").read_text(encoding="utf-8"))
    return instruments_root / raw["model"]


def _stage(instruments_root: Path) -> tuple:
    """(entries, files): every published model's checked bake, or an
    ExportModelsError naming the first instrument that cannot export."""
    entries, files = {}, {}
    for name, inst in sorted(load_catalog(instruments_root).published.items()):
        if inst.model_sha256 is None:
            continue
        baked_path = bake_output_path(_model_path(instruments_root, name))
        if not baked_path.is_file():
            raise ExportModelsError(
                f"instrument {name!r} declares a model but has no bake at "
                f"{baked_path}; run tools/bake_model.py first")
        baked = baked_path.read_bytes()
        try:
            mm_bake = validate_baked_glb(baked, path=str(baked_path))
        except BakeContractError as exc:
            raise ExportModelsError(f"instrument {name!r}: {exc}") from exc
        if mm_bake["source_sha256"] != inst.model_sha256:
            raise ExportModelsError(
                f"instrument {name!r}: bake {baked_path} is stale (bake "
                f"source_sha256 {mm_bake['source_sha256']}, current model "
                f"{inst.model_sha256}); re-run tools/bake_model.py")
        if mm_bake["layout"] != layout_to_json(inst.layout):
            raise ExportModelsError(
                f"instrument {name!r}: bake {baked_path} carries a layout that "
                f"differs from the catalog's; re-run tools/bake_model.py on the "
                f"current source model")
        sha = hashlib.sha256(baked).hexdigest()
        filename = f"{name}.{sha[:8]}.baked.glb"
        files[filename] = baked
        entries[name] = {"file": filename, "model_sha256": inst.model_sha256,
                         "baked_sha256": sha}
    return entries, files


def export_models(instruments_root: Path, out_repo: Path, *, commit: str) -> dict:
    """Stage and check everything first, then write: assets/models/ gets
    the bakes and models.json, stale *.baked.glb files are removed, and
    the shared fixture pair (never the source fixture, spec section 6.4)
    is copied byte-identically into test/fixtures/models/. Returns the
    models.json payload."""
    _check_target(out_repo)
    entries, files = _stage(instruments_root)

    models_dir = out_repo / "assets" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    strays = sorted(p.name for p in models_dir.iterdir()
                    if p.is_file() and p.name != "models.json"
                    and not p.name.endswith(".baked.glb"))
    if strays:
        raise ExportModelsError(
            f"{models_dir} holds unexpected file(s) {strays}; remove them by hand "
            f"(export_models deletes only stale *.baked.glb files)")

    for old in models_dir.glob("*.baked.glb"):
        if old.name not in files:
            old.unlink()
    for filename, data in files.items():
        (models_dir / filename).write_bytes(data)
    payload = {"_provenance": {"tool": TOOL_VERSION, "commit": commit},
               "models": entries}
    (models_dir / "models.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    fixture_dest = out_repo / "test" / "fixtures" / "models"
    fixture_dest.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_FILES:
        shutil.copyfile(FIXTURE_DIR / name, fixture_dest / name)
    return payload


def main(argv: list | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit("usage: python -m tools.export_models <mm-tuneshroom checkout>")
    out_repo = Path(argv[0])
    try:
        payload = export_models(REPO_ROOT / "instruments", out_repo, commit=_head_commit())
    except (ExportModelsError, TerrariumConfigError) as exc:
        sys.exit(f"export_models: {exc}")
    print(f"wrote {out_repo / 'assets' / 'models' / 'models.json'} "
          f"({len(payload['models'])} model(s)) and the shared fixture pair")


if __name__ == "__main__":
    main()
