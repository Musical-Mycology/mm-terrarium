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
    """entries is keyed "<state>:<name>" so a draft edit of a published
    entry does not shadow the published one."""
    root: Path
    entries: dict[str, CatalogEntry]

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
