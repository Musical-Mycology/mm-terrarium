"""Per-role config: validation of authored Role light/welcome declarations
(at Bit load) and composition of the /ie<N>/role config blob (at role
adoption). See docs/superpowers/specs/2026-07-22-light-manifest-v2-adoption-
design.md sections 5-6. Pure functions, no engine imports, mirroring the
protocol-module discipline. The wire contract is luxaeterna's light-manifest
v2 (LightManifest.from_dict); validation here is deliberately shallow --
instrument names and params belong to luxaeterna's installation-overridable
registry, which Control cannot see.
"""

from control.roles import Role, RoleTable

# Keys Control composes into the outgoing blob at adoption time; authoring
# any of them on a Role is a contract violation caught at Bit load.
_COMPOSED_KEYS = ("welcome", "bit_name", "bit_version", "role")
_WELCOME_HALVES = ("light", "audio")


def validate_role_declarations(role_table: RoleTable) -> None:
    """Shallow structural validation of every role's light_manifest and
    welcome against the authored subset of the v2 wire shape. Raises
    ValueError with a message locating the offending field."""
    for role in role_table.roles.values():
        _validate_light_manifest(role)
        _validate_welcome(role)


def _validate_light_manifest(role: Role) -> None:
    where = f"role {role.name!r} light_manifest"
    manifest = role.light_manifest
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
