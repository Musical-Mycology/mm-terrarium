"""Per-role config: validation of authored Role light/welcome declarations
(at Bit load) and composition of the /ie<N>/role config blob (at role
adoption). See docs/superpowers/specs/2026-07-22-light-manifest-v2-adoption-
design.md sections 5-6. Pure functions, no engine imports, mirroring the
protocol-module discipline. The wire contract is luxaeterna's light-manifest
v2 (LightManifest.from_dict); validation here is deliberately shallow --
instrument names and params belong to luxaeterna's installation-overridable
registry, which Control cannot see.

The welcome-audio half is the one exception: its instrument table
(control.audio.WELCOME_INSTRUMENTS) is Control-owned, not a device-side
registry, so this module can and does check names against it. That is the
one point where this module imports a sibling leaf module rather than
staying purely structural; control.audio imports control.roles only, never
this module, so the edge does not close a cycle.
"""

from copy import deepcopy

from control.audio import WELCOME_INSTRUMENTS
from control.function_view import function_view
from control.instrument import Instrument
from control.roles import Role, RoleTable

# Keys Control composes into the outgoing blob at adoption time; authoring
# any of them on a Role is a contract violation caught at Bit load.
_COMPOSED_KEYS = ("welcome", "bit_name", "bit_version", "role")
_WELCOME_HALVES = ("light", "audio")
_CC_PREFIX = "cc:"


def validate_role_declarations(role_table: RoleTable) -> None:
    """Shallow structural validation of every role's light_manifest, welcome
    and ugen_manifest against their authored shapes. Raises ValueError with a
    message locating the offending field."""
    for role in role_table.roles.values():
        _validate_light_manifest(role)
        _validate_welcome(role)
        validate_ugen_manifest(role)
        _validate_string_list(role, "uses")
        _validate_string_list(role, "samples")


def _validate_light_manifest(role: Role) -> None:
    validate_light_manifest(role.light_manifest, f"role {role.name!r} light_manifest")


def validate_light_manifest(manifest: dict, where: str) -> None:
    """Shallow structural validation of a light_manifest, shared by the
    per-role path (Bit load, via _validate_light_manifest) and any other
    caller (e.g. instruments) that supplies its own location prefix."""
    if not isinstance(manifest, dict):
        raise ValueError(
            f"{where}: must be a dict in the v2 wire shape, "
            f"got {type(manifest).__name__}")
    for key in _COMPOSED_KEYS:
        if key in manifest:
            raise ValueError(
                f"{where}: field {key!r} is composed by Control at adoption "
                f"time; declare a welcome via Role.welcome")
    instruments = manifest.get("instruments", [])
    if not isinstance(instruments, list):
        raise ValueError(f"{where}: 'instruments' must be a list")
    for idx, decl in enumerate(instruments):
        decl_where = f"{where} instruments[{idx}]"
        if not isinstance(decl, dict):
            raise ValueError(f"{decl_where}: must be a dict")
        for req in ("instrument", "target"):
            if req not in decl:
                raise ValueError(
                    f"{decl_where}: missing required field {req!r}")
        params = decl.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"{decl_where}: 'params' must be a dict")
        lanes = decl.get("lanes", [])
        if not isinstance(lanes, list):
            raise ValueError(f"{decl_where}: 'lanes' must be a list")
        for lidx, lane in enumerate(lanes):
            lane_where = f"{decl_where} lanes[{lidx}]"
            if not isinstance(lane, dict):
                raise ValueError(f"{lane_where}: must be a dict")
            for req in ("source", "dest"):
                if req not in lane:
                    raise ValueError(
                        f"{lane_where}: missing required field {req!r}")


def _validate_welcome(role: Role) -> None:
    welcome = role.welcome
    if welcome is None:
        return
    where = f"role {role.name!r} welcome"
    if not isinstance(welcome, dict):
        raise ValueError(f"{where}: must be a dict")
    halves = [h for h in _WELCOME_HALVES if h in welcome]
    if not halves:
        raise ValueError(
            f"{where}: must declare at least one of 'light'/'audio'")
    for half in halves:
        half_where = f"{where} {half!r}"
        decl = welcome[half]
        if not isinstance(decl, dict):
            raise ValueError(f"{half_where}: must be a dict")
        if "instrument" not in decl:
            raise ValueError(
                f"{half_where}: missing required field 'instrument'")
        if half == "audio" and decl["instrument"] not in WELCOME_INSTRUMENTS:
            raise ValueError(
                f"{half_where}: unknown instrument {decl['instrument']!r} "
                f"(known: {sorted(WELCOME_INSTRUMENTS)})")
        params = decl.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"{half_where}: 'params' must be a dict")


def _validate_string_list(role: Role, field_name: str) -> None:
    """Shallow check for the flat string-list fields (uses, samples): a list
    of non-empty strings. Deliberately does not check membership against a
    vocabulary -- surface names belong to the device, exactly as instrument
    names belong to luxaeterna's registry."""
    where = f"role {role.name!r} {field_name}"
    value = getattr(role, field_name)
    if not isinstance(value, list):
        raise ValueError(
            f"{where}: must be a list, got {type(value).__name__}")
    for idx, entry in enumerate(value):
        if not isinstance(entry, str) or not entry:
            raise ValueError(
                f"{where}[{idx}]: must be a non-empty string, got {entry!r}")


def compose_role_config(bit_name: str, bit_version: str, role: Role, *,
                        room_name: str | None = None,
                        terrarium_config_version: str | None = None,
                        slot: str | None = None,
                        instrument: str | None = None,
                        event_triggers: tuple = (),
                        carried: Instrument | None = None) -> dict:
    """The per-role config blob shipped in /ie<N>/role at adoption time
    (docs/control-gameserver-design.md, player flow step 3). Deep-copied so
    transport/Console consumers can never alias the Bit's declaration. The
    welcome audio half is deliberately absent: it never ships to the device;
    the future Arco cue path reads it off Role.welcome.

    room_name and terrarium_config_version are provenance stamps from an
    active Room (see control/terrarium.py's GameServer.provenance); a Bit
    joined outside a Room passes neither, and the two keys are omitted
    entirely so a pre-Room blob stays byte-identical to what always shipped
    -- never present as null.

    slot and instrument stamp the requirement slot a granted join filled
    and the carried instrument's name that filled it (GameServer.join,
    Task 6); both are omitted for ROOM joins and requires-less roles, same
    never-null discipline as the provenance stamps.

    event_triggers is the carried instrument's Task 8 EventTrigger tuple;
    when non-empty it ships as config["triggers"] = {name: thresholds},
    deep-copied so the device-side detector's server-declared thresholds can
    never alias Instrument.event_triggers. Omitted entirely when empty --
    same never-null discipline as every other stamp here.

    carried is the granted join's carried Instrument (2026-08-31 carried-
    instrument-wire, spec section 3); when not None the blob gains
    config["instrument"] = {name, capabilities (sorted), pixels, ambient
    (light/ugen manifests), functions (function_view's wire shape)},
    deep-copied so a generic host's rendering can never alias the
    Instrument. Omitted entirely when carried is None -- same never-null
    discipline as every other stamp here. This supersedes the instrument
    keyword's flat-string stamp for any caller that also passes carried:
    every granted non-ROOM join does today (GameServer.join), so the
    section's own "name" field is the sole surviving form of that stamp."""
    light = deepcopy(role.light_manifest)
    light["bit_name"] = bit_name
    light["bit_version"] = bit_version
    light["role"] = role.name
    if role.welcome and "light" in role.welcome:
        light["welcome"] = deepcopy(role.welcome["light"])
    config = {
        "role": role.name,
        "class": role.role_class.name,
        "scored": role.scored,
        "light_manifest": light,
        "uses": list(role.uses),
        "samples": list(role.samples),
    }
    if room_name is not None:
        config["room_name"] = room_name
    if terrarium_config_version is not None:
        config["terrarium_config_version"] = terrarium_config_version
    if slot is not None:
        config["slot"] = slot
    if instrument is not None:
        config["instrument"] = instrument
    if event_triggers:
        config["triggers"] = {t.name: dict(t.thresholds)
                              for t in event_triggers}
    if carried is not None:
        config["instrument"] = {
            "name": carried.name,
            "capabilities": sorted(carried.capabilities),
            "pixels": carried.pixels,
            "ambient": {"light": deepcopy(carried.light_manifest),
                        "ugen": deepcopy(carried.ugen_manifest)},
            "functions": [function_view(f) for f in carried.functions],
        }
    return config


def _cc_number(ref, where: str) -> int:
    """Parse a 'cc:<n>' reference. Both lane ends use this form: the mapping
    to a synth parameter is FluidSynth's own reading of the controller number,
    so there is no destination vocabulary for Control to invent here."""
    if not isinstance(ref, str) or not ref.startswith(_CC_PREFIX):
        raise ValueError(f"{where}: must be a {_CC_PREFIX!r} reference, got {ref!r}")
    try:
        num = int(ref[len(_CC_PREFIX):])
    except ValueError:
        raise ValueError(
            f"{where}: {ref!r} is not a controller number") from None
    if not 0 <= num <= 127:
        raise ValueError(f"{where}: controller {num} is outside 0-127")
    return num


def validate_ugen_manifest(subject: Role | dict, where: str | None = None) -> None:
    """Shallow structural validation of an authored ugen_manifest.
    Deliberately provisional (v0): instrument names and programs belong to the
    Arco/FluidSynth side Control cannot see, so only shape is checked here.

    Called either as validate_ugen_manifest(role) from the per-role path
    (Bit load), where the location prefix is derived from the role, or as
    validate_ugen_manifest(manifest, where) by any other caller (e.g.
    instruments) supplying its own location prefix."""
    if isinstance(subject, Role):
        manifest = subject.ugen_manifest
        where = f"role {subject.name!r} ugen_manifest"
    else:
        manifest = subject
        assert where is not None
    if not isinstance(manifest, dict):
        raise ValueError(
            f"{where}: must be a dict, got {type(manifest).__name__}")
    instruments = manifest.get("instruments", [])
    if not isinstance(instruments, list):
        raise ValueError(f"{where}: 'instruments' must be a list")
    for idx, decl in enumerate(instruments):
        decl_where = f"{where} instruments[{idx}]"
        if not isinstance(decl, dict):
            raise ValueError(f"{decl_where}: must be a dict")
        if "instrument" not in decl:
            raise ValueError(
                f"{decl_where}: missing required field 'instrument'")
        drone = decl.get("drone")
        if drone is not None:
            if not isinstance(drone, dict):
                raise ValueError(f"{decl_where}: 'drone' must be a dict")
            for req in ("key", "velocity"):
                if req not in drone:
                    raise ValueError(
                        f"{decl_where} drone: missing required field {req!r}")
        lanes = decl.get("lanes", [])
        if not isinstance(lanes, list):
            raise ValueError(f"{decl_where}: 'lanes' must be a list")
        for lidx, lane in enumerate(lanes):
            lane_where = f"{decl_where} lanes[{lidx}]"
            if not isinstance(lane, dict):
                raise ValueError(f"{lane_where}: must be a dict")
            for req in ("source", "dest"):
                if req not in lane:
                    raise ValueError(
                        f"{lane_where}: missing required field {req!r}")
            _cc_number(lane["source"], f"{lane_where} source")
            _cc_number(lane["dest"], f"{lane_where} dest")
