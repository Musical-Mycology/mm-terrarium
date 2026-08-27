"""Terrarium config: the valid-room set and bit search path, as data.
Schema v1. Pure stdlib (tomllib); located errors in the same style as
control/bit_config.py. See docs/superpowers/specs/
2026-08-26-terrarium-lifecycle-and-config-rooms-design.md section 2.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from control.instrument import (Instrument, InstrumentError,
                                validate_instrument,
                                validate_instrument_manifests)
from control.room_profile import (RoomBlock, RoomFixture, RoomProfile,
                                  RoomZone)

KNOWN_BACKENDS = frozenset({"devicelink", "array"})


class TerrariumConfigError(Exception):
    def __init__(self, *, source: str, key: str, message: str) -> None:
        self.source = source
        self.key = key
        super().__init__(f"{source}: [{key}] {message}")


@dataclass(frozen=True)
class RoomSpec:
    name: str
    description: str
    backends: tuple[str, ...]
    node_id: str
    profile: RoomProfile
    arco_ready_timeout: float = 15.0
    arco_settle_seconds: float = 0.0


@dataclass(frozen=True)
class TerrariumConfig:
    schema: int
    name: str
    bit_paths: tuple[str, ...]
    rooms: dict[str, RoomSpec]
    version: str          # f"{schema}-{sha256(text)[:12]}", content-addressed
    instruments: dict[str, Instrument] = field(default_factory=dict)


def load_terrarium_config(path: str) -> TerrariumConfig:
    with open(path, encoding="utf-8") as f:
        return parse_terrarium_config(f.read(), source=path)


def parse_terrarium_config(text: str, source: str) -> TerrariumConfig:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TerrariumConfigError(source=source, key="-",
                                   message=f"not valid TOML: {exc}") from exc
    schema = raw.get("schema")
    if schema != 1:
        raise TerrariumConfigError(source=source, key="schema",
                                   message=f"expected 1, got {schema!r}")
    terr = raw.get("terrarium", {})
    name = terr.get("name")
    if not isinstance(name, str) or not name:
        raise TerrariumConfigError(source=source, key="terrarium.name",
                                   message="required non-empty string")
    bit_paths = tuple(terr.get("bit_paths", ["bits"]))
    instruments_raw = raw.get("instruments", {})
    instruments: dict[str, Instrument] = {}
    for iname, iraw in instruments_raw.items():
        instruments[iname] = _parse_instrument(iname, iraw, source=source)
    rooms_raw = raw.get("rooms")
    if not isinstance(rooms_raw, dict) or not rooms_raw:
        raise TerrariumConfigError(source=source, key="rooms",
                                   message="at least one [rooms.<NAME>] required")
    rooms: dict[str, RoomSpec] = {}
    for rname, rraw in rooms_raw.items():
        rooms[rname] = _parse_room(rname, rraw, source=source,
                                   instruments=instruments)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return TerrariumConfig(schema=schema, name=name, bit_paths=bit_paths,
                           rooms=rooms, instruments=instruments,
                           version=f"{schema}-{digest}")


def _parse_instrument(iname: str, iraw: dict, *, source: str) -> Instrument:
    key = f"instruments.{iname}"
    ambient = iraw.get("ambient", {})
    light_manifest = ambient.get("light", {})
    ugen_manifest = ambient.get("ugen", {})
    instrument = Instrument(
        name=iname,
        description=iraw.get("description", ""),
        capabilities=frozenset(iraw.get("capabilities", [])),
        functions=tuple(iraw.get("functions", [])),
        accepted_cues=tuple(iraw.get("accepted_cues", [])),
        light_manifest=light_manifest,
        ugen_manifest=ugen_manifest,
    )
    try:
        validate_instrument(instrument)
        validate_instrument_manifests(instrument)
    except InstrumentError as exc:
        raise TerrariumConfigError(source=source, key=key,
                                   message=str(exc)) from exc
    return instrument


def _parse_room(rname: str, rraw: dict, *, source: str,
                instruments: dict[str, Instrument]) -> RoomSpec:
    key = f"rooms.{rname}"
    backends = tuple(rraw.get("backends", []))
    unknown = [b for b in backends if b not in KNOWN_BACKENDS]
    if unknown:
        raise TerrariumConfigError(
            source=source, key=key,
            message=f"unknown backends {unknown}; known: {sorted(KNOWN_BACKENDS)}")
    fixtures = []
    for fraw in rraw.get("fixtures", []):
        blocks = tuple(RoomBlock(b["name"], b["start"], b["count"])
                       for b in fraw.get("blocks", []))
        zones = tuple(RoomZone(z["name"], z["start"], z["count"])
                      for z in fraw.get("zones", []))
        iname = fraw.get("instrument")
        if not iname:
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"fixture {fraw.get('name')!r} missing required "
                        f"'instrument' key")
        instrument = instruments.get(iname)
        if instrument is None:
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"fixture {fraw.get('name')!r} references unknown "
                        f"instrument {iname!r}; known: {sorted(instruments)}")
        fixtures.append(RoomFixture(name=fraw["name"],
                                    color_order=fraw["color_order"],
                                    blocks=blocks, zones=zones,
                                    instrument=instrument))
    try:
        profile = RoomProfile(surface_id=f"room_{rname.lower()}",
                              fixtures=tuple(fixtures))
    except (ValueError, KeyError, TypeError) as exc:
        raise TerrariumConfigError(source=source, key=key,
                                   message=str(exc)) from exc
    arco = rraw.get("arco", {})
    return RoomSpec(
        name=rname,
        description=rraw.get("description", ""),
        backends=backends,
        node_id=rraw.get("node_id", f"ROOM_{rname}_NODE"),
        profile=profile,
        arco_ready_timeout=float(arco.get("ready_timeout", 15.0)),
        arco_settle_seconds=float(arco.get("settle_seconds", 0.0)),
    )


def resolve_bit_roots(config: TerrariumConfig, config_path: str) -> list[Path]:
    """config.bit_paths, resolved to filesystem roots for BitRegistry.scan().
    A relative entry is anchored at config_path's own directory (not the
    process CWD); an absolute entry passes through unchanged."""
    base = Path(config_path).resolve().parent
    roots = []
    for raw in config.bit_paths:
        path = Path(raw)
        roots.append(path if path.is_absolute() else base / path)
    return roots


def validate_rooms(config: TerrariumConfig, *,
                   array_backend_configured: bool) -> dict[str, str | None]:
    """Per-room loadability, boot-time. None = loadable; else the reason.
    The room actually being loaded fails hard on its reason
    (control/terrarium.py); the rest of the set is advisory, surfaced on
    the Console rooms panel and CLI listings. No silent downgrade."""
    out: dict[str, str | None] = {}
    for name, spec in config.rooms.items():
        if "array" in spec.backends and not array_backend_configured:
            out[name] = f"{name} requires an array backend, none configured"
        else:
            out[name] = None
    return out
