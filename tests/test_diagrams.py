"""Detect a diagram that no longer matches its source.

Pure stdlib: no d2, no node, no network, so this runs in the core offline
suite. Spec: docs/superpowers/specs/2026-08-17-ascii-diagram-pipeline-design.md

Known limit: the manifest is the source of truth, so hand-editing both a
rendered file and its recorded hash would pass. This catches forgetting to
re-render, which is the realistic failure.
"""

from pathlib import Path

import pytest

from tools.render_diagrams import (
    build_region,
    extract_region,
    load_manifest,
    markers_in,
    sha256_text,
)

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs/diagrams"
STALE = "stale: run `python -m tools.render_diagrams`"


def _entries():
    return sorted(load_manifest(ROOT)["diagrams"].items())


@pytest.mark.parametrize("name,entry", _entries())
def test_source_matches_its_recorded_hash(name, entry):
    text = (DIAGRAMS / entry["source"]).read_text("utf-8")
    assert sha256_text(text) == entry["source_sha256"], f"{name}: source {STALE}"


@pytest.mark.parametrize("name,entry", _entries())
def test_outputs_match_their_recorded_hashes(name, entry):
    assert entry["outputs"], f"{name}: manifest records no outputs"
    for rel, digest in entry["outputs"].items():
        path = DIAGRAMS / rel
        assert path.exists(), f"{name}: missing output {rel}"
        assert sha256_text(path.read_text("utf-8")) == digest, f"{name}/{rel}: {STALE}"


@pytest.mark.parametrize("name,entry", _entries())
def test_injected_region_matches_the_rendered_text(name, entry):
    target = entry.get("inject_into")
    if not target:
        return
    stem = Path(entry["source"]).stem
    ascii_text = (DIAGRAMS / f"out/{stem}.txt").read_text("utf-8")
    markdown = (ROOT / target).read_text("utf-8")
    expected = build_region(entry["marker"], ascii_text)
    assert extract_region(markdown, entry["marker"]) == expected, f"{name}: {STALE}"


def test_no_orphaned_markers_in_targets():
    manifest = load_manifest(ROOT)
    by_target: dict[str, set[str]] = {}
    for entry in manifest["diagrams"].values():
        target = entry.get("inject_into")
        if target:
            by_target.setdefault(target, set()).add(entry["marker"])
    for target, declared in by_target.items():
        found = markers_in((ROOT / target).read_text("utf-8"))
        assert found == declared, (
            f"{target}: markers in the document {found} do not match the manifest "
            f"{declared}. An undeclared marker is never regenerated."
        )


def test_every_manifest_source_exists():
    for name, entry in _entries():
        assert (DIAGRAMS / entry["source"]).exists(), f"{name}: missing source"
