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
