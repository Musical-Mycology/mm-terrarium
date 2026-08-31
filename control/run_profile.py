"""Run profiles: `--profile venue.toml`, a venue's own launch defaults.

A profile sits between a Bit's manifest and the operator's own explicit CLI
flags in the precedence chain each launcher applies: manifest < profile <
explicit CLI. It carries two things: the five launcher fields under `[run]`
(bit/room_type/devices/console_port/seconds), and a `[bit.overrides]` table
passed through untouched -- validated later by control/bit_config.py's
merge_overrides against whichever Bit is actually selected, not here.

See .superpowers/sdd/2026-08-21-bit-packaging-and-launch/ for the design.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_RUN_KEYS = frozenset(
    {"bit", "room_type", "devices", "console_port", "seconds"})


@dataclass(frozen=True)
class RunProfile:
    bit: str | None = None
    room_type: str | None = None
    devices: int | None = None
    console_port: int | None = None
    seconds: float | None = None
    overrides: dict = field(default_factory=dict)


def parse_profile(text: str, *, source: str) -> RunProfile:
    """Parse a profile TOML's `[run]` table and pass `[bit.overrides]`
    through verbatim -- it is validated later, against the selected Bit's
    schema, by control/bit_config.py's merge_overrides.

    An unknown `[run]` key WARNS rather than raising: a profile is a venue
    operator's own file, hand-edited, and a typo'd or forward-looking key
    (e.g. one a newer launcher understands but this one does not yet) should
    not turn a working profile into a launch failure -- unlike a manifest's
    unknown keys, which are strict (see bit_config.parse_manifest)."""
    data = tomllib.loads(text)

    run = data.get("run", {})
    if not isinstance(run, dict):
        raise ValueError(f"{source}: [run] expected a table")
    unknown = sorted(set(run) - _RUN_KEYS)
    for key in unknown:
        logger.warning("%s: [run] unknown key %r ignored", source, key)

    bit_table = data.get("bit", {})
    overrides: dict[str, Any] = {}
    if isinstance(bit_table, dict):
        raw_overrides = bit_table.get("overrides", {})
        if isinstance(raw_overrides, dict):
            overrides = raw_overrides

    return RunProfile(
        bit=run.get("bit"),
        room_type=run.get("room_type"),
        devices=run.get("devices"),
        console_port=run.get("console_port"),
        seconds=run.get("seconds"),
        overrides=overrides,
    )


def deep_merge_overrides(profile_overrides: dict, cli_overrides: dict) -> dict:
    """Deep-merge `cli_overrides` OVER `profile_overrides`: CLI wins per-key,
    recursing into nested tables (e.g. `[bit.overrides.rhythm]`) so a CLI
    flag touching one key of a table (say `rhythm.bpm`) does not blow away
    the profile's other keys in that same table (say `rhythm.cycles`).

    Neither input is mutated; the result is a fresh dict tree wherever the
    two inputs actually overlap."""
    merged = dict(profile_overrides)
    for key, cli_value in cli_overrides.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(cli_value, dict):
            merged[key] = deep_merge_overrides(base_value, cli_value)
        else:
            merged[key] = cli_value
    return merged
