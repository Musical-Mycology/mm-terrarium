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

from control.cues import ROOM, TARGET
from control.functions import Function, FunctionKind, GeneratorSpec
from control.instrument import (Instrument, InstrumentError,
                                validate_instrument,
                                validate_instrument_manifests)
from control.room_profile import (RoomBlock, RoomFixture, RoomProfile,
                                  RoomZone)
from control.triggers import EventTrigger, StreamTrigger

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


_LANE_DEV_WIRE = {"room": ROOM, "target": TARGET}


def _parse_functions(iname: str, iraw: dict, *, source: str, key: str
                     ) -> tuple[Function, ...]:
    """`[[instruments.<name>.functions]]` array-of-tables -> Functions.

    A bare list (the pre-v0 `functions = ["tap"]` shape) is refused here,
    located, naming the table shape a config author must switch to -- the
    only structural check this module makes; a defect in the parsed
    Function itself (bad waveform, missing lane key, ...) surfaces later
    through validate_instrument's own located InstrumentError."""
    raw_list = iraw.get("functions", [])
    if not isinstance(raw_list, list):
        raise TerrariumConfigError(
            source=source, key=key,
            message=f"functions must be an array of "
                    f"[[instruments.{iname}.functions]] tables, got "
                    f"{type(raw_list).__name__}")
    functions = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"functions must be declared as "
                        f"[[instruments.{iname}.functions]] tables, not a "
                        f"bare list entry (got {entry!r})")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise TerrariumConfigError(
                source=source, key=key,
                message="function entry missing required 'name'")
        kind = entry.get("kind", "generator")
        if kind != "generator":
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"function {name!r}: kind {kind!r} must be "
                        f"'generator' (v0 only supports ambient generators "
                        f"on an instrument)")
        lane = entry.get("lane")
        if not isinstance(lane, dict):
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"function {name!r}: lane table required, got "
                        f"{type(lane).__name__}")
        dev_wire = lane.get("dev")
        dev = _LANE_DEV_WIRE.get(dev_wire)
        if dev is None:
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"function {name!r}: lane.dev must be 'room' or "
                        f"'target', got {dev_wire!r}")
        try:
            status = lane["status"]
            data1 = lane["data1"]
        except KeyError as exc:
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"function {name!r}: lane missing required key "
                        f"{exc}") from exc
        functions.append(Function(
            name=name,
            description=entry.get("description", ""),
            kind=FunctionKind.GENERATOR,
            generator=GeneratorSpec(
                dev=dev, status=status, data1=data1,
                waveform=entry.get("waveform"),
                period=entry.get("period"),
                lo=entry.get("lo", 0),
                hi=entry.get("hi", 127),
            ),
        ))
    return tuple(functions)


def _parse_trigger_tables(iname: str, iraw: dict, *, source: str, key: str,
                          table: str, required: tuple[str, ...]) -> list[dict]:
    """`[[instruments.<name>.<table>]]` array-of-tables -> raw dicts, with a
    located structural check (array-of-tables shape, required keys present).
    Defects inside a trigger itself (bad transform, non-numeric threshold)
    are left to validate_instrument on the built Instrument."""
    raw_list = iraw.get(table, [])
    if not isinstance(raw_list, list):
        raise TerrariumConfigError(
            source=source, key=key,
            message=f"{table} must be an array of "
                    f"[[instruments.{iname}.{table}]] tables")
    out = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"{table} entries must be tables, got {entry!r}")
        for req in required:
            if req not in entry:
                raise TerrariumConfigError(
                    source=source, key=key,
                    message=f"{table} entry missing required {req!r}")
        out.append(entry)
    return out


def _parse_event_triggers(iname: str, iraw: dict, *, source: str, key: str
                          ) -> tuple[EventTrigger, ...]:
    return tuple(
        EventTrigger(name=e["name"], description=e.get("description", ""),
                     thresholds=dict(e.get("thresholds", {})))
        for e in _parse_trigger_tables(iname, iraw, source=source, key=key,
                                       table="event_triggers",
                                       required=("name",)))


def _parse_stream_triggers(iname: str, iraw: dict, *, source: str, key: str
                           ) -> tuple[StreamTrigger, ...]:
    return tuple(
        StreamTrigger(name=e["name"], description=e.get("description", ""),
                      verb=e["verb"], arg=int(e["arg"]),
                      transform=e["transform"],
                      params=dict(e.get("params", {})))
        for e in _parse_trigger_tables(iname, iraw, source=source, key=key,
                                       table="stream_triggers",
                                       required=("name", "verb", "arg",
                                                 "transform")))


def _parse_instrument(iname: str, iraw: dict, *, source: str) -> Instrument:
    key = f"instruments.{iname}"
    if "accepted_triggers" in iraw:  # legacy-vocabulary-ok
        raise TerrariumConfigError(
            source=source, key=key,
            message="'accepted_triggers' was renamed to 'accepted_cues' "  # legacy-vocabulary-ok
                    "(Spec 3); update the key")
    ambient = iraw.get("ambient", {})
    light_manifest = ambient.get("light", {})
    ugen_manifest = ambient.get("ugen", {})
    instrument = Instrument(
        name=iname,
        description=iraw.get("description", ""),
        capabilities=frozenset(iraw.get("capabilities", [])),
        functions=_parse_functions(iname, iraw, source=source, key=key),
        accepted_cues=tuple(iraw.get("accepted_cues", [])),
        light_manifest=light_manifest,
        ugen_manifest=ugen_manifest,
        event_triggers=_parse_event_triggers(iname, iraw, source=source, key=key),
        stream_triggers=_parse_stream_triggers(iname, iraw, source=source, key=key),
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
