"""Export a catalog instrument's solo definition as the wire-shape JSON
mm-tuneshroom bundles (spec: mm-tuneshroom
docs/superpowers/specs/2026-09-13-standalone-solo-mode-design.md, section 3).

    .venv/bin/python -m tools.export_solo tuneshroom \
        /path/to/mm-tuneshroom/assets/solo/tuneshroom.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from control.catalog import load_catalog
from control.instrument import Instrument
from control.role_config import carried_instrument_view

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_VERSION = "export_solo/1"


def export_solo(inst: Instrument, *, catalog_path: str, commit: str) -> dict:
    if inst.solo is None:
        raise ValueError(f"instrument {inst.name!r} declares no [solo] table")
    return {
        "_provenance": {"catalog": catalog_path, "commit": commit,
                        "tool": TOOL_VERSION},
        "instrument": carried_instrument_view(inst),
        "triggers": {t.name: dict(t.thresholds) for t in inst.event_triggers},
        "solo": {
            "ambient": {"light": deepcopy(inst.solo.light_manifest)},
            "bindings": dict(inst.solo.bindings),
        },
    }


def _head_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=12", "HEAD"],
            text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        sys.exit("usage: export_solo.py <instrument-name> <out.json>")
    name, out = argv
    inst = load_catalog(REPO_ROOT / "instruments").published.get(name)
    if inst is None:
        sys.exit(f"no published instrument named {name!r}")
    if inst.solo is None:
        sys.exit(f"instrument {name!r} declares no [solo] table")
    data = export_solo(inst, catalog_path=f"instruments/{name}.toml",
                       commit=_head_commit())
    Path(out).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    print(f"wrote {out} ({data['_provenance']['commit']})")


if __name__ == "__main__":
    main()
