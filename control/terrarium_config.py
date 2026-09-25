"""Terrarium config: the valid-room set and bit search path, as data.
Schema v1. Pure stdlib (tomllib); located errors in the same style as
control/bit_config.py. See docs/superpowers/specs/
2026-08-26-terrarium-lifecycle-and-config-rooms-design.md section 2.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from control.cues import ROOM, TARGET, MuteCue, PlayCue, SolidCue
from control.functions import Function, FunctionKind, GeneratorSpec, ScriptStep
from control.instrument import (Instrument, InstrumentError, SoloConfig,
                                validate_instrument,
                                validate_instrument_manifests)
from control.lobby import TERRARIUM_ADMIN
from control.room_profile import (RoomBlock, RoomFixture, RoomProfile,
                                  RoomZone)
from control.triggers import EventTrigger, StreamTrigger

KNOWN_BACKENDS = frozenset({"devicelink", "array"})


SECRET_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class UplinkConfig:
    """[uplink]: the broker and the MycoQuest-issued box secret (spec
    2026-09-13 section 6.1). The name presented is [terrarium] name."""
    tenant_slug: str
    secret: str = field(repr=False)
    url: str = ""


@dataclass(frozen=True)
class ArtNetOutput:
    """One [[artnet]] entry: a Room fixture's physical output to a WLED
    controller. Pure data; devicelink/artnet_sink.py's outputs_factory
    builds the sink. Spec 2026-09-23 section 6.1."""
    room: str
    fixture: str
    host: str
    max_amps: float
    start_universe: int = 0
    port: int = 6454
    amps_per_pixel_full: float = 0.025
    lead_ms: float = 0.0
    keepalive_ms: float = 250.0
    # Which [psus.<name>] this output's strip hangs on; None = unchecked.
    psu: str | None = None


_ARTNET_KEYS = frozenset({"room", "fixture", "host", "max_amps",
                          "start_universe", "port", "amps_per_pixel_full",
                          "lead_ms", "keepalive_ms", "psu"})
_PSU_KEYS = frozenset({"amps"})
# Outputs sharing one PSU must sum to at most this fraction of its rating
# (spec 2026-09-23 section 6.3, enforced per spec 2026-09-25 section 8).
_PSU_BUDGET_FRACTION = 0.8
_PIXELS_PER_RGBW_UNIVERSE = 128
_ARTNET_MAX_UNIVERSE = 32767


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
    # [terrarium] instrument_paths, resolved to filesystem roots the same
    # way load_terrarium_config resolves them for load_catalog below --
    # relative to the config file's own directory. Empty from
    # parse_terrarium_config (no config path to resolve against); the
    # Console's design panel reads instrument_roots[0], when non-empty, as
    # its catalog_root (harness/terrarium_boot.py's main()).
    instrument_roots: tuple[Path, ...] = ()
    # [terrarium] room_paths, resolved to filesystem roots the same way
    # load_terrarium_config resolves them for load_catalog below --
    # relative to the config file's own directory. Empty from
    # parse_terrarium_config (no config path to resolve against); the
    # Console's design panel reads room_roots[0], when non-empty, as
    # its rooms_root.
    room_roots: tuple[Path, ...] = ()
    # [admin] devices, a list of device names that are admin-control targets
    # (in addition to the Terrarium itself, which is always an admin target).
    admin_devices: tuple[str, ...] = ()
    # [uplink], None when the table is absent: no uplink is built at boot.
    uplink: UplinkConfig | None = None
    # [[artnet]] entries: one per Room fixture wired to a real WLED
    # controller. Empty means no Art-Net output is configured anywhere.
    artnet_outputs: tuple[ArtNetOutput, ...] = ()
    # [psus.<name>] tables: PSU name -> rated amps. Validation only.
    psus: dict[str, float] = field(default_factory=dict)


def load_terrarium_config(path: str) -> TerrariumConfig:
    from control.catalog import load_catalog  # local: avoid import cycle
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return parse_terrarium_config(text, source=path)  # located there
    instrument_paths = raw.get("terrarium", {}).get(
        "instrument_paths", ["instruments"])
    extra: dict = {}
    base = Path(path).resolve().parent
    roots = tuple(base / rel for rel in instrument_paths)
    for root in roots:
        for name, inst in load_catalog(root).published.items():
            if name in extra:
                raise TerrariumConfigError(
                    source=str(root), key=f"instruments.{name}",
                    message="defined in more than one catalog root")
            extra[name] = inst
    config = parse_terrarium_config(text, source=path, extra_instruments=extra,
                                    require_rooms=False)
    room_paths = raw.get("terrarium", {}).get("room_paths", ["rooms"])
    room_roots = tuple(base / rel for rel in room_paths)
    rooms = dict(config.rooms)
    from_catalog: set[str] = set()
    for root in room_roots:
        for rname, spec in load_catalog(root, kind="room",
                                        instruments=config.instruments).published.items():
            if rname in rooms:
                # Two different mistakes, two different messages -- the same
                # split the instrument path makes.
                raise TerrariumConfigError(
                    source=str(root), key=f"rooms.{rname}",
                    message=("defined in more than one rooms catalog root"
                             if rname in from_catalog else
                             "defined both inline and in a rooms catalog; pick one home"))
            rooms[rname] = spec
            from_catalog.add(rname)
    if not rooms:
        raise TerrariumConfigError(
            source=path, key="rooms",
            message="at least one room required: a [rooms.<NAME>] table or a "
                    "rooms catalog entry")
    validate_artnet_outputs(config.artnet_outputs, rooms, source=path)
    return replace(config, rooms=rooms, instrument_roots=roots, room_roots=room_roots)


def parse_terrarium_config(text: str, source: str,
                           extra_instruments: dict[str, Instrument] | None = None,
                           *, require_rooms: bool = True
                           ) -> TerrariumConfig:
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
    admin_raw = raw.get("admin", {})
    if not isinstance(admin_raw, dict):
        raise TerrariumConfigError(source=source, key="admin",
                                   message="expected a table")
    devices_raw = admin_raw.get("devices", [])
    if not isinstance(devices_raw, list) or not all(
            isinstance(d, str) and d for d in devices_raw):
        raise TerrariumConfigError(source=source, key="admin.devices",
                                   message="expected a list of non-empty strings")
    if TERRARIUM_ADMIN in devices_raw:
        raise TerrariumConfigError(
            source=source, key="admin.devices",
            message=f"{TERRARIUM_ADMIN!r} is the Terrarium itself and is always "
                    f"an admin; do not list it")
    admin_devices = tuple(devices_raw)
    uplink_raw = raw.get("uplink")
    uplink = None
    if uplink_raw is not None:
        if not isinstance(uplink_raw, dict):
            raise TerrariumConfigError(source=source, key="uplink",
                                       message="expected a table")
        slug = uplink_raw.get("tenant_slug")
        if not isinstance(slug, str) or not slug:
            raise TerrariumConfigError(source=source, key="uplink.tenant_slug",
                                       message="required non-empty string")
        secret = uplink_raw.get("secret")
        if not isinstance(secret, str) or not SECRET_PATTERN.match(secret):
            raise TerrariumConfigError(
                source=source, key="uplink.secret",
                message="expected 64 lowercase hex characters, pasted from "
                        "the MycoQuest admin site (value not shown)")
        url = uplink_raw.get("url", "")
        if not isinstance(url, str):
            raise TerrariumConfigError(source=source, key="uplink.url",
                                       message="expected a string")
        uplink = UplinkConfig(tenant_slug=slug, secret=secret, url=url)
    instruments_raw = raw.get("instruments", {})
    instruments: dict[str, Instrument] = {}
    for iname, iraw in instruments_raw.items():
        instruments[iname] = _parse_instrument(iname, iraw, source=source)
    for iname, inst in (extra_instruments or {}).items():
        if iname in instruments:
            raise TerrariumConfigError(
                source=source, key=f"instruments.{iname}",
                message="defined both inline and in an instrument catalog; "
                        "pick one home")
        instruments[iname] = inst
    rooms_raw = raw.get("rooms", {})
    if not isinstance(rooms_raw, dict):
        raise TerrariumConfigError(source=source, key="rooms",
                                   message="[rooms] must be a table of [rooms.<NAME>] tables")
    if require_rooms and not rooms_raw:
        raise TerrariumConfigError(
            source=source, key="rooms",
            message="at least one room required: a [rooms.<NAME>] table or a "
                    "rooms catalog entry")
    artnet = _parse_artnet(raw.get("artnet"), source=source)
    psus = _parse_psus(raw.get("psus"), source=source)
    validate_psu_budgets(artnet, psus, source=source)
    rooms: dict[str, RoomSpec] = {}
    for rname, rraw in rooms_raw.items():
        rooms[rname] = _parse_room(rname, rraw, source=source,
                                   instruments=instruments)
    if require_rooms:
        validate_artnet_outputs(artnet, rooms, source=source)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return TerrariumConfig(schema=schema, name=name, bit_paths=bit_paths,
                           rooms=rooms, instruments=instruments,
                           version=f"{schema}-{digest}", admin_devices=admin_devices,
                           uplink=uplink, artnet_outputs=artnet, psus=psus)


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
    solo = None
    sraw = iraw.get("solo")
    if sraw is not None:
        if not isinstance(sraw, dict):
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"instrument {iname!r}: [solo] must be a table")
        sambient = sraw.get("ambient", {})
        bindings = sraw.get("bindings", {})
        if not isinstance(bindings, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in bindings.items()):
            raise TerrariumConfigError(
                source=source, key=key,
                message=f"instrument {iname!r}: [solo.bindings] must map "
                        f"event names to function names")
        solo = SoloConfig(light_manifest=sambient.get("light", {}),
                          bindings=dict(bindings))
    pixels = iraw.get("pixels", 0)
    if isinstance(pixels, bool) or not isinstance(pixels, int):
        raise TerrariumConfigError(
            source=source, key=key,
            message=f"instrument {iname!r}: pixels must be an int, got "
                    f"{pixels!r}")
    instrument = Instrument(
        name=iname,
        description=iraw.get("description", ""),
        pixels=pixels,
        capabilities=frozenset(iraw.get("capabilities", [])),
        functions=_parse_functions(iname, iraw, source=source, key=key),
        accepted_cues=tuple(iraw.get("accepted_cues", [])),
        light_manifest=light_manifest,
        ugen_manifest=ugen_manifest,
        event_triggers=_parse_event_triggers(iname, iraw, source=source, key=key),
        stream_triggers=_parse_stream_triggers(iname, iraw, source=source, key=key),
        solo=solo,
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


def _parse_artnet(raw, *, source: str) -> tuple[ArtNetOutput, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TerrariumConfigError(source=source, key="artnet",
                                   message="expected [[artnet]] tables")
    out = []
    for i, entry in enumerate(raw):
        key = f"artnet[{i}]"

        def err(message, key=key):
            return TerrariumConfigError(source=source, key=key, message=message)

        if not isinstance(entry, dict):
            raise err("expected a table")
        unknown = sorted(set(entry) - _ARTNET_KEYS)
        if unknown:
            raise err(f"unknown key(s) {unknown}; known: {sorted(_ARTNET_KEYS)}")
        for name in ("room", "fixture", "host"):
            if not isinstance(entry.get(name), str) or not entry[name]:
                raise err(f"{name} is a required non-empty string")
        amps = entry.get("max_amps")
        if isinstance(amps, bool) or not isinstance(amps, (int, float)) or amps <= 0:
            raise err("max_amps is required and must be a positive number "
                      "(power limiting has no opt-out)")
        start = entry.get("start_universe", 0)
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise err("start_universe must be an integer >= 0")
        port = entry.get("port", 6454)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            raise err("port must be an integer in 1-65535")
        numbers = {}
        for name, default in (("amps_per_pixel_full", 0.025), ("lead_ms", 0.0),
                              ("keepalive_ms", 250.0)):
            v = entry.get(name, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise err(f"{name} must be a number >= 0")
            numbers[name] = float(v)
        if numbers["amps_per_pixel_full"] == 0 or numbers["keepalive_ms"] == 0:
            raise err("amps_per_pixel_full and keepalive_ms must be > 0")
        psu = entry.get("psu")
        if psu is not None and (not isinstance(psu, str) or not psu):
            raise err("psu must be a non-empty string naming a [psus.<name>] table")
        out.append(ArtNetOutput(room=entry["room"], fixture=entry["fixture"],
                                host=entry["host"], max_amps=float(amps),
                                start_universe=start, port=port, psu=psu,
                                **numbers))
    return tuple(out)


def validate_artnet_outputs(outputs, rooms: dict, *, source: str) -> None:
    """Cross-check [[artnet]] against the rooms it names: the room and
    fixture exist, the fixture is RGBW, one output per fixture, no two
    outputs on one host:port share a universe, and no output's universe
    span exceeds the Art-Net maximum (15-bit port-address, 0-32767;
    luxaeterna's ArtNet._build_packet packs the universe as `<H`, which
    only accepts 0-65535, so this refusal happens before that packing ever
    sees an out-of-range value)."""
    seen: dict[tuple[str, str], int] = {}
    spans: dict[tuple[str, int], list[tuple[int, int, int]]] = {}
    for i, out in enumerate(outputs):
        key = f"artnet[{i}]"
        spec = rooms.get(out.room)
        if spec is None:
            raise TerrariumConfigError(source=source, key=key,
                message=f"unknown room {out.room!r}; known: {sorted(rooms)}")
        fixture = next((f for f in spec.profile.fixtures if f.name == out.fixture), None)
        if fixture is None:
            raise TerrariumConfigError(source=source, key=key,
                message=f"unknown fixture {out.fixture!r} in room {out.room!r}; "
                        f"known: {[f.name for f in spec.profile.fixtures]}")
        if fixture.color_order != "RGBW":
            raise TerrariumConfigError(source=source, key=key,
                message=f"fixture {out.fixture!r} is {fixture.color_order}; an "
                        f"Art-Net output needs color_order = \"RGBW\" (the wire order)")
        if (out.room, out.fixture) in seen:
            raise TerrariumConfigError(source=source, key=key,
                message=f"fixture {out.room}.{out.fixture} has more than one "
                        f"[[artnet]] output (first: artnet[{seen[(out.room, out.fixture)]}])")
        seen[(out.room, out.fixture)] = i
        count = -(-fixture.pixel_count // _PIXELS_PER_RGBW_UNIVERSE)
        lo, hi = out.start_universe, out.start_universe + count - 1
        if hi > _ARTNET_MAX_UNIVERSE:
            raise TerrariumConfigError(source=source, key=key,
                message=f"universes {lo}-{hi} exceed the Art-Net maximum "
                        f"{_ARTNET_MAX_UNIVERSE}")
        for (olo, ohi, oi) in spans.setdefault((out.host, out.port), []):
            if lo <= ohi and olo <= hi:
                raise TerrariumConfigError(source=source, key=key,
                    message=f"universes {lo}-{hi} on {out.host}:{out.port} "
                            f"overlap artnet[{oi}] ({olo}-{ohi})")
        spans[(out.host, out.port)].append((lo, hi, i))


def _parse_psus(raw, *, source: str) -> dict[str, float]:
    """[psus.<name>] tables -> {name: rated amps}. Optional."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TerrariumConfigError(source=source, key="psus",
                                   message="expected [psus.<name>] tables")
    out: dict[str, float] = {}
    for name, entry in raw.items():
        key = f"psus.{name}"
        if not isinstance(entry, dict):
            raise TerrariumConfigError(source=source, key=key,
                                       message="expected a table")
        unknown = sorted(set(entry) - _PSU_KEYS)
        if unknown:
            raise TerrariumConfigError(source=source, key=key,
                message=f"unknown key(s) {unknown}; known: {sorted(_PSU_KEYS)}")
        amps = entry.get("amps")
        if isinstance(amps, bool) or not isinstance(amps, (int, float)) or amps <= 0:
            raise TerrariumConfigError(source=source, key=key,
                message="amps is required and must be a positive number "
                        "(the PSU's rated current)")
        out[name] = float(amps)
    return out


def validate_psu_budgets(outputs, psus: dict[str, float], *, source: str) -> None:
    """Every output naming a PSU names a declared one, and each PSU's
    outputs' max_amps sum to at most 80% of its rating. The sum spans every
    room: only one Room loads at a time, but one box has one supply. An
    output with no psu is not checked."""
    loads: dict[str, list[tuple[int, float]]] = {}
    for i, out in enumerate(outputs):
        if out.psu is None:
            continue
        if out.psu not in psus:
            raise TerrariumConfigError(source=source, key=f"artnet[{i}]",
                message=f"unknown psu {out.psu!r}; known: {sorted(psus)}")
        loads.setdefault(out.psu, []).append((i, out.max_amps))
    for name, entries in loads.items():
        total = sum(amps for _, amps in entries)
        limit = _PSU_BUDGET_FRACTION * psus[name]
        if total > limit + 1e-9:
            parts = ", ".join(f"artnet[{i}] {amps:g} A" for i, amps in entries)
            raise TerrariumConfigError(source=source, key=f"psus.{name}",
                message=f"max_amps sum {total:g} A exceeds 80% of the "
                        f"{psus[name]:g} A rating ({limit:g} A): {parts}")


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
    the Console rooms panel and CLI listings. No silent downgrade.

    `array_backend_configured` means the simulator; a real array is
    `[[artnet]]` coverage of every fixture."""
    out: dict[str, str | None] = {}
    covered = {(o.room, o.fixture) for o in config.artnet_outputs}
    for name, spec in config.rooms.items():
        out[name] = None
        if "array" not in spec.backends or array_backend_configured:
            continue
        missing = [f.name for f in spec.profile.fixtures
                   if (name, f.name) not in covered]
        if missing:
            out[name] = (f"{name} requires an array backend, none configured: "
                         f"no simulator, and no [[artnet]] output for "
                         f"fixture(s) {missing}")
    return out
