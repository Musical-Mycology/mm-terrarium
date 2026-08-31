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

from control.cues import ROOM, TARGET, MuteCue, PlayCue, SolidCue
from control.functions import Function, FunctionKind, GeneratorSpec, ScriptStep
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


_LANE_DEV_WIRE = {"room": ROOM, "target": TARGET}


def _parse_script_step(iname, fname, idx, sraw, *, source, key):
    def err(message):
        return TerrariumConfigError(source=source, key=key,
            message=f"function {fname!r} script[{idx}]: {message}")
    if not isinstance(sraw, dict):
        raise err(f"must be a table, got {type(sraw).__name__}")
    offset = sraw.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        raise err(f"offset must be a number, got {offset!r}")
    cue_keys = [k for k in ("midi", "play", "solid", "mute") if k in sraw]
    if len(cue_keys) != 1:
        raise err("must carry exactly one of midi/play/solid/mute")
    kind = cue_keys[0]
    if kind == "midi":
        v = sraw["midi"]
        if (not isinstance(v, list) or len(v) != 3
                or any(isinstance(x, bool) or not isinstance(x, int) for x in v)):
            raise err(f"midi must be [status, data1, data2] ints, got {v!r}")
        return ScriptStep(float(offset), (TARGET, v[0], v[1], v[2]))
    if kind == "play":
        v = sraw["play"]
        if not isinstance(v, str) or not v:
            raise err(f"play must be a sample name, got {v!r}")
        return ScriptStep(float(offset), PlayCue(TARGET, v, ""))
    if kind == "solid":
        v = sraw["solid"]
        if not isinstance(v, dict):
            raise err(f"solid must be a table, got {type(v).__name__}")
        rgb = v.get("rgb")
        if (not isinstance(rgb, list) or len(rgb) != 3):
            raise err(f"solid.rgb must be [r, g, b], got {rgb!r}")
        return ScriptStep(float(offset), SolidCue(
            TARGET, tuple(rgb), v.get("level", 1.0), v.get("duration")))
    return ScriptStep(float(offset), MuteCue(TARGET))


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
        if kind == "scripted":
            sraw_list = entry.get("script")
            if not isinstance(sraw_list, list) or not sraw_list:
                raise TerrariumConfigError(source=source, key=key,
                    message=f"function {name!r}: scripted functions require "
                            f"a non-empty script array")
            steps = tuple(
                _parse_script_step(iname, name, i, s, source=source, key=key)
                for i, s in enumerate(sraw_list))
            functions.append(Function(
                name=name, description=entry.get("description", ""),
                kind=FunctionKind.SCRIPTED, script=steps))
            continue
        if kind != "generator":
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"function {name!r}: kind {kind!r} must be "
                        f"'generator' or 'scripted'")
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
