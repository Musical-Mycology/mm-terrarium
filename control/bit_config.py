"""Bit manifest schema v1: parse `bit.toml`, validate it, and merge
hand-typed overrides (e.g. CLI/launch-time overrides) over a parsed config.

See .superpowers/sdd/2026-08-21-bit-packaging-and-launch/ for the design.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

KINDS = frozenset({"music", "r_game", "game", "tool", "ambient"})

_START_WHEN = frozenset({"immediate", "players", "operator"})
_ON_TIMEOUT = frozenset({"start", "abort"})
_TRANSPORTS = frozenset({"any", "o2lite"})


class ManifestError(Exception):
    def __init__(self, *, source: str, key: str, message: str):
        self.source = source
        self.key = key
        self.message = message
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"{self.source}: [{self.key}] {self.message}"


@dataclass(frozen=True)
class BitIdentity:
    name: str
    version: str = ""
    description: str = ""
    entry: str = ""
    kind: str = "game"
    author: str = ""
    requires_terrarium_api: int | None = None
    enabled: bool = True


@dataclass(frozen=True)
class LaunchConfig:
    room_types: tuple[str, ...] = ("TEST",)
    default_room_type: str = "TEST"
    default_devices: int = 1
    setup_seconds: float = 0.0
    expected_run_seconds: float | None = None
    transport: str = "any"
    nodes: tuple[tuple[str, str], ...] = ()
    default_join_role: str = ""


@dataclass(frozen=True)
class StartCondition:
    when: str = "immediate"
    min_scored: int = 1
    timeout_seconds: float | None = None
    on_timeout: str = "start"


@dataclass(frozen=True)
class ConsoleBlock:
    display_name: str = ""
    notes: str = ""
    hidden: bool = False


@dataclass(frozen=True)
class RhythmConfig:
    bpm: float = 100.0
    beats_per_cycle: int = 8
    cycles: int = 4
    grading_window_ms: float = 50.0
    input_offset_ms: float = 0.0


@dataclass(frozen=True)
class AmbientConfig:
    jam_control: bool = False
    default_pattern: str = ""


@dataclass(frozen=True)
class BitConfig:
    identity: BitIdentity
    launch: LaunchConfig
    start: StartCondition
    console: ConsoleBlock
    results_keys: tuple[str, ...] = ()
    assets: tuple[tuple[str, str], ...] = ()
    rhythm: RhythmConfig | None = None
    ambient: AmbientConfig | None = None
    extras: dict = field(default_factory=dict)
    # Stamped by BitRegistry.resolve_config, never parsed from the manifest:
    # the absolute package directory asset paths resolve against. None means
    # "location unknown" and asset_path refuses rather than guessing.
    assets_root: Path | None = None

    def asset_path(self, key: str) -> Path:
        for akey, rel in self.assets:
            if akey == key:
                if self.assets_root is None:
                    raise ManifestError(
                        source=self.identity.name, key=f"assets.{key}",
                        message="no assets_root: config was not resolved "
                                "through a BitRegistry")
                return self.assets_root / rel
        raise ManifestError(
            source=self.identity.name, key=f"assets.{key}",
            message=f"no such asset; declared: "
                    f"{sorted(k for k, _ in self.assets)}")

    def node_for(self, role: str) -> str | None:
        for node_role, node in self.launch.nodes:
            if node_role == role:
                return node
        return None

    def join_node(self) -> str | None:
        if self.launch.default_join_role:
            node = self.node_for(self.launch.default_join_role)
            if node is not None:
                return node
        if self.launch.nodes:
            return self.launch.nodes[0][1]
        return None


def _is_number(value: Any) -> bool:
    # bool is a subclass of int in Python; TOML makes true/false unambiguous
    # from numbers, so a manifest value of `true` must not pass as numeric.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _get(table: dict, key: str, expected: type, default: Any, *, source: str,
         prefix: str) -> Any:
    if key not in table:
        return default
    value = table[key]
    if expected is float:
        ok = _is_number(value)
    elif expected is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif expected is bool:
        ok = isinstance(value, bool)
    elif expected is str:
        ok = isinstance(value, str)
    elif expected is list:
        ok = isinstance(value, list)
    elif expected is dict:
        ok = isinstance(value, dict)
    else:
        ok = isinstance(value, expected)
    if not ok:
        raise ManifestError(
            source=source, key=f"{prefix}.{key}" if prefix else key,
            message=f"expected {expected.__name__}, got {type(value).__name__}")
    return value


def _warn_unknown_keys(table: dict, known: set[str], *, source: str, prefix: str):
    for key in table:
        if key not in known:
            logger.warning("%s: unknown key [%s.%s] ignored", source, prefix, key)


def _parse_identity(raw: dict, *, source: str) -> BitIdentity:
    known = {"name", "version", "description", "entry", "kind", "author",
             "requires_terrarium_api", "enabled"}
    _warn_unknown_keys(raw, known, source=source, prefix="bit")

    name = _get(raw, "name", str, "", source=source, prefix="bit")
    if not name:
        raise ManifestError(source=source, key="bit.name",
                             message="required non-empty string")

    entry = _get(raw, "entry", str, "", source=source, prefix="bit")
    if not entry:
        raise ManifestError(source=source, key="bit.entry",
                             message="required non-empty string")
    if entry.count(":") != 1:
        raise ManifestError(source=source, key="bit.entry",
                             message="must contain exactly one ':'")

    kind = _get(raw, "kind", str, "game", source=source, prefix="bit")
    if kind not in KINDS:
        raise ManifestError(source=source, key="bit.kind",
                             message=f"must be one of {sorted(KINDS)}")

    return BitIdentity(
        name=name,
        version=_get(raw, "version", str, "", source=source, prefix="bit"),
        description=_get(raw, "description", str, "", source=source, prefix="bit"),
        entry=entry,
        kind=kind,
        author=_get(raw, "author", str, "", source=source, prefix="bit"),
        requires_terrarium_api=_get(raw, "requires_terrarium_api", int, None,
                                     source=source, prefix="bit"),
        enabled=_get(raw, "enabled", bool, True, source=source, prefix="bit"),
    )


def _parse_launch(raw: dict, *, source: str) -> LaunchConfig:
    known = {"room_types", "default_room_type", "default_devices",
              "setup_seconds", "expected_run_seconds", "transport", "nodes",
              "default_join_role"}
    _warn_unknown_keys(raw, known, source=source, prefix="launch")

    room_types = tuple(
        _get(raw, "room_types", list, ["TEST"], source=source, prefix="launch"))
    if not room_types:
        raise ManifestError(source=source, key="launch.room_types",
                             message="must be non-empty")

    default_room_type = _get(raw, "default_room_type", str,
                              room_types[0] if "room_types" in raw else "TEST",
                              source=source, prefix="launch")
    if default_room_type not in room_types:
        raise ManifestError(
            source=source, key="launch.default_room_type",
            message=f"must be one of {list(room_types)}")

    transport = _get(raw, "transport", str, "any", source=source, prefix="launch")
    if transport not in _TRANSPORTS:
        raise ManifestError(source=source, key="launch.transport",
                             message=f"must be one of {sorted(_TRANSPORTS)}")

    expected_run_seconds = raw.get("expected_run_seconds")
    if expected_run_seconds is not None and not _is_number(expected_run_seconds):
        raise ManifestError(
            source=source, key="launch.expected_run_seconds",
            message=f"expected float, got {type(expected_run_seconds).__name__}")

    nodes_raw = _get(raw, "nodes", dict, {}, source=source, prefix="launch")
    nodes = tuple(sorted(nodes_raw.items()))

    return LaunchConfig(
        room_types=room_types,
        default_room_type=default_room_type,
        default_devices=_get(raw, "default_devices", int, 1, source=source,
                              prefix="launch"),
        setup_seconds=float(_get(raw, "setup_seconds", float, 0.0, source=source,
                                  prefix="launch")),
        expected_run_seconds=(float(expected_run_seconds)
                               if expected_run_seconds is not None else None),
        transport=transport,
        nodes=nodes,
        default_join_role=_get(raw, "default_join_role", str, "", source=source,
                                prefix="launch"),
    )


def _parse_start(raw: dict, *, source: str) -> StartCondition:
    known = {"when", "min_scored", "timeout_seconds", "on_timeout"}
    _warn_unknown_keys(raw, known, source=source, prefix="start")

    when = _get(raw, "when", str, "immediate", source=source, prefix="start")
    if when == "scheduled":
        raise ManifestError(
            source=source, key="start.when",
            message="scheduled start conditions are reserved for a later slice")
    if when not in _START_WHEN:
        raise ManifestError(source=source, key="start.when",
                             message=f"must be one of {sorted(_START_WHEN)}")

    min_scored = _get(raw, "min_scored", int, 1, source=source, prefix="start")
    if min_scored < 1:
        raise ManifestError(source=source, key="start.min_scored",
                             message="must be >= 1")

    on_timeout = _get(raw, "on_timeout", str, "start", source=source,
                       prefix="start")
    if on_timeout not in _ON_TIMEOUT:
        raise ManifestError(source=source, key="start.on_timeout",
                             message=f"must be one of {sorted(_ON_TIMEOUT)}")

    timeout_seconds = raw.get("timeout_seconds")
    if timeout_seconds is not None and not _is_number(timeout_seconds):
        raise ManifestError(
            source=source, key="start.timeout_seconds",
            message=f"expected float, got {type(timeout_seconds).__name__}")

    return StartCondition(
        when=when,
        min_scored=min_scored,
        timeout_seconds=(float(timeout_seconds)
                          if timeout_seconds is not None else None),
        on_timeout=on_timeout,
    )


def _parse_console(raw: dict, *, source: str) -> ConsoleBlock:
    known = {"display_name", "notes", "hidden"}
    _warn_unknown_keys(raw, known, source=source, prefix="console")
    return ConsoleBlock(
        display_name=_get(raw, "display_name", str, "", source=source,
                           prefix="console"),
        notes=_get(raw, "notes", str, "", source=source, prefix="console"),
        hidden=_get(raw, "hidden", bool, False, source=source, prefix="console"),
    )


def _parse_rhythm(raw: dict, *, source: str) -> RhythmConfig:
    known = {"bpm", "beats_per_cycle", "cycles", "grading_window_ms",
              "input_offset_ms"}
    _warn_unknown_keys(raw, known, source=source, prefix="rhythm")
    return RhythmConfig(
        bpm=float(_get(raw, "bpm", float, 100.0, source=source, prefix="rhythm")),
        beats_per_cycle=_get(raw, "beats_per_cycle", int, 8, source=source,
                              prefix="rhythm"),
        cycles=_get(raw, "cycles", int, 4, source=source, prefix="rhythm"),
        grading_window_ms=float(_get(raw, "grading_window_ms", float, 50.0,
                                      source=source, prefix="rhythm")),
        input_offset_ms=float(_get(raw, "input_offset_ms", float, 0.0,
                                    source=source, prefix="rhythm")),
    )


def _parse_ambient(raw: dict, *, source: str) -> AmbientConfig:
    known = {"jam_control", "default_pattern"}
    _warn_unknown_keys(raw, known, source=source, prefix="ambient")
    return AmbientConfig(
        jam_control=_get(raw, "jam_control", bool, False, source=source,
                          prefix="ambient"),
        default_pattern=_get(raw, "default_pattern", str, "", source=source,
                              prefix="ambient"),
    )


_KNOWN_TOP_TABLES = {"bit", "launch", "start", "console", "results", "rhythm",
                     "ambient", "defaults", "assets"}


def parse_manifest(text: str, *, source: str) -> BitConfig:
    doc = tomllib.loads(text)

    for key in doc:
        if key not in _KNOWN_TOP_TABLES:
            logger.warning("%s: unknown table [%s] ignored", source, key)

    identity = _parse_identity(doc.get("bit", {}), source=source)
    launch = _parse_launch(doc.get("launch", {}), source=source)
    start = _parse_start(doc.get("start", {}), source=source)
    console = _parse_console(doc.get("console", {}), source=source)

    results_raw = doc.get("results", {})
    _warn_unknown_keys(results_raw, {"keys"}, source=source, prefix="results")
    results_keys = tuple(
        _get(results_raw, "keys", list, [], source=source, prefix="results"))

    assets_raw = _get(doc, "assets", dict, {}, source=source, prefix="")
    assets_items = []
    for akey, aval in sorted(assets_raw.items()):
        if not isinstance(aval, str):
            raise ManifestError(
                source=source, key=f"assets.{akey}",
                message=f"expected str path, got {type(aval).__name__}")
        p = PurePosixPath(aval)
        if p.is_absolute() or ".." in p.parts:
            raise ManifestError(
                source=source, key=f"assets.{akey}",
                message="must be a package-relative path with no '..'")
        assets_items.append((akey, aval))
    assets = tuple(assets_items)

    rhythm = None
    if "rhythm" in doc:
        if identity.kind != "r_game":
            logger.warning(
                "%s: [rhythm] present on non-r_game kind %r; parsed anyway",
                source, identity.kind)
        rhythm = _parse_rhythm(doc["rhythm"], source=source)

    ambient = None
    if "ambient" in doc:
        if identity.kind != "ambient":
            logger.warning(
                "%s: [ambient] present on non-ambient kind %r; parsed anyway",
                source, identity.kind)
        ambient = _parse_ambient(doc["ambient"], source=source)

    extras = dict(doc.get("defaults", {}))

    return BitConfig(
        identity=identity,
        launch=launch,
        start=start,
        console=console,
        results_keys=results_keys,
        assets=assets,
        rhythm=rhythm,
        ambient=ambient,
        extras=extras,
    )


# Map of override table name -> (dataclass field name on BitConfig, parser,
# default-instance factory used when the current field is None).
_OVERRIDE_TABLES = {
    "bit": ("identity", _parse_identity, None),
    "launch": ("launch", _parse_launch, None),
    "start": ("start", _parse_start, None),
    "console": ("console", _parse_console, None),
    "rhythm": ("rhythm", _parse_rhythm, RhythmConfig),
    "ambient": ("ambient", _parse_ambient, AmbientConfig),
}


def _to_raw(table_name: str, obj: Any) -> dict:
    """Convert a parsed dataclass instance back into the raw TOML-shaped
    dict its parser function expects, so overrides can be re-validated
    through the exact same rules the manifest parser uses."""
    if table_name == "launch":
        return {
            "room_types": list(obj.room_types),
            "default_room_type": obj.default_room_type,
            "default_devices": obj.default_devices,
            "setup_seconds": obj.setup_seconds,
            "expected_run_seconds": obj.expected_run_seconds,
            "transport": obj.transport,
            "nodes": dict(obj.nodes),
            "default_join_role": obj.default_join_role,
        }
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def merge_overrides(config: BitConfig, overrides: dict, *, source: str) -> BitConfig:
    changes: dict[str, Any] = {}

    for table_name, table_value in overrides.items():
        if table_name == "defaults":
            if not isinstance(table_value, dict):
                raise ManifestError(source=source, key="defaults",
                                     message="expected a table")
            merged_extras = dict(config.extras)
            merged_extras.update(table_value)
            changes["extras"] = merged_extras
            continue

        if table_name == "results":
            if not isinstance(table_value, dict) or "keys" not in table_value:
                raise ManifestError(source=source, key="results",
                                     message="expected a table with 'keys'")
            unknown = set(table_value) - {"keys"}
            if unknown:
                bad = sorted(unknown)[0]
                raise ManifestError(source=source, key=f"results.{bad}",
                                     message="unknown override key")
            changes["results_keys"] = tuple(table_value["keys"])
            continue

        if table_name not in _OVERRIDE_TABLES:
            raise ManifestError(source=source, key=table_name,
                                 message="unknown override table")
        if not isinstance(table_value, dict):
            raise ManifestError(source=source, key=table_name,
                                 message="expected a table")

        field_name, parser, default_factory = _OVERRIDE_TABLES[table_name]
        current = getattr(config, field_name)

        # Determine the dataclass whose field names bound valid keys. When
        # the current value is None (e.g. rhythm/ambient not present), we
        # still need a schema to validate against -- build a default instance.
        if current is None:
            current = default_factory()

        valid_keys = {f.name for f in fields(current)}
        for key in table_value:
            if key not in valid_keys:
                raise ManifestError(
                    source=source, key=f"{table_name}.{key}",
                    message="unknown override key")

        # Re-validate the merged values through the same parser the manifest
        # itself uses, so semantic/type errors (e.g. start.when="scheduled",
        # a bool where a number is required) raise here too, not just on
        # unknown key names.
        raw = _to_raw(table_name, current)
        raw.update(table_value)
        changes[field_name] = parser(raw, source=source)

    return replace(config, **changes)
