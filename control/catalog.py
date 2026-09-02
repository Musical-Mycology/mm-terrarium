"""File-based catalog: instruments/*.toml and rooms/*.toml are published
entries, their drafts/*.toml siblings are drafts. Name = file stem.
Published entries fail hard; drafts collect their error. Pure stdlib
(control/ discipline).

Spec: docs/superpowers/specs/2026-08-31-design-panel-and-instrument-catalog-design.md.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from control.instrument import Instrument
from control.terrarium_config import (RoomSpec, TerrariumConfigError,
                                       _parse_instrument, _parse_room)

CATALOG_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

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


def _check_stem(path: Path, kind: str) -> str:
    if not CATALOG_NAME_RE.match(path.stem):
        raise TerrariumConfigError(
            source=str(path), key="-",
            message=f"{kind} file name must match [A-Za-z0-9_-]+")
    return path.stem


def _parse_entry(path: Path, state: str, kind: str, instruments) -> CatalogEntry:
    name = _check_stem(path, kind)
    text = path.read_text(encoding="utf-8")
    try:
        obj = _parse_text(name, text, path, kind, instruments)
    except (tomllib.TOMLDecodeError, TerrariumConfigError) as exc:
        if state == "published":
            if isinstance(exc, TerrariumConfigError):
                raise
            raise TerrariumConfigError(
                source=str(path), key="-",
                message=f"not valid TOML: {exc}") from exc
        return CatalogEntry(name=name, state=state, path=path, kind=kind,
                            instrument=None, room=None, error=str(exc))
    return CatalogEntry(
        name=name, state=state, path=path, kind=kind,
        instrument=obj if kind == "instrument" else None,
        room=obj if kind == "room" else None,
        error=None)


def _refuse_name(name: str, kind: str) -> str | None:
    if not CATALOG_NAME_RE.match(name):
        return f"{kind} name {name!r} must match [A-Za-z0-9_-]+"
    return None


def _draft_errors(name: str, text: str, path: Path, kind: str, instruments) -> list[str]:
    try:
        _parse_text(name, text, path, kind, instruments)
    except tomllib.TOMLDecodeError as exc:
        return [f"not valid TOML: {exc}"]
    except TerrariumConfigError as exc:
        return [str(exc)]
    return []


def save_draft(root: Path, name: str, text: str, kind: str = "instrument",
               instruments=None) -> tuple[str | None, list[str]]:
    _check_kind(kind, instruments)
    refusal = _refuse_name(name, kind)
    if refusal:
        return refusal, []
    drafts = Path(root) / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    return None, _draft_errors(name, text, path, kind, instruments)


def clone_entry(root: Path, source_state: str, source_name: str,
                 new_name: str, kind: str = "instrument") -> str | None:
    refusal = _refuse_name(new_name, kind)
    if refusal:
        return refusal
    root = Path(root)
    src = (root / f"{source_name}.toml" if source_state == "published"
           else root / "drafts" / f"{source_name}.toml")
    if _refuse_name(source_name, kind) or not src.is_file():
        return f"no {source_state} {kind} named {source_name!r}"
    dst = root / "drafts" / f"{new_name}.toml"
    if dst.exists():
        return f"draft {new_name!r} already exists"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return None


def publish_entry(root: Path, name: str, kind: str = "instrument",
                   instruments=None) -> str | None:
    _check_kind(kind, instruments)
    if _refuse_name(name, kind):
        return f"no draft named {name!r}"
    root = Path(root)
    src = root / "drafts" / f"{name}.toml"
    if not src.is_file():
        return f"no draft named {name!r}"
    errors = _draft_errors(name, src.read_text(encoding="utf-8"), src, kind, instruments)
    if errors:
        return "; ".join(errors)
    src.replace(root / f"{name}.toml")
    return None


def load_catalog(root: Path, kind: str = "instrument", instruments=None) -> Catalog:
    _check_kind(kind, instruments)
    root = Path(root)
    entries: dict[str, CatalogEntry] = {}
    if root.is_dir():
        for path in sorted(root.glob("*.toml")):
            entry = _parse_entry(path, "published", kind, instruments)
            entries[f"published:{entry.name}"] = entry
        drafts = root / "drafts"
        if drafts.is_dir():
            for path in sorted(drafts.glob("*.toml")):
                entry = _parse_entry(path, "draft", kind, instruments)
                entries[f"draft:{entry.name}"] = entry
    return Catalog(root=root, kind=kind, entries=entries)
