"""Instruments: first-class entities a room loads (as fixtures) or a device
carries. Pure stdlib -- no luxaeterna, no pyarco (control/ discipline).

Spec: docs/superpowers/specs/2026-08-27-instruments-and-fixtures-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CAPABILITY_VOCABULARY: frozenset[str] = frozenset({
    "light.pixels",    # addressable pixels of any shape
    "light.surface",   # a linear multi-zone surface (Room-style array)
    "audio.flsyn",     # an Arco FluidSynth voice reachable
    "audio.samples",   # local sample playback
    "gesture.tap",
    "gesture.tilt",
})

CUE_KINDS: tuple[str, ...] = ("midi", "play", "solid", "mute")


class InstrumentError(ValueError):
    pass


@dataclass(frozen=True)
class Instrument:
    name: str
    description: str = ""
    capabilities: frozenset[str] = frozenset()
    functions: tuple[str, ...] = ()
    accepted_triggers: tuple[str, ...] = ()
    light_manifest: dict = field(default_factory=dict)
    ugen_manifest: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InstrumentRequirement:
    slot: str
    capabilities: frozenset[str]
    min_pixels: int = 0
    optional: bool = False


@dataclass(frozen=True)
class CarriedInstrument:
    instrument: Instrument
    dev: str


def validate_instrument(instrument: Instrument) -> None:
    unknown = set(instrument.capabilities) - CAPABILITY_VOCABULARY
    if unknown:
        raise InstrumentError(
            f"instrument {instrument.name!r}: unknown capability tag(s) "
            f"{sorted(unknown)}; known: {sorted(CAPABILITY_VOCABULARY)}")
    bad = [k for k in instrument.accepted_triggers if k not in CUE_KINDS]
    if bad:
        raise InstrumentError(
            f"instrument {instrument.name!r}: unknown accepted trigger "
            f"kind(s) {bad}; known: {list(CUE_KINDS)}")


def satisfies(instrument: Instrument, requirement: InstrumentRequirement,
              *, pixel_count: int | None = None) -> str | None:
    """None when the instrument satisfies the contract, else the reason.

    Matching is on contracts, never names (spec section 2)."""
    missing = requirement.capabilities - instrument.capabilities
    if missing:
        return (f"instrument {instrument.name!r} lacks capability "
                f"{sorted(missing)} required by slot {requirement.slot!r}")
    if requirement.min_pixels:
        if pixel_count is None:
            return (f"slot {requirement.slot!r} requires min_pixels="
                    f"{requirement.min_pixels} but no pixel count is known")
        if pixel_count < requirement.min_pixels:
            return (f"slot {requirement.slot!r} requires at least "
                    f"{requirement.min_pixels} pixels; {instrument.name!r} "
                    f"surface has {pixel_count}")
    return None


TUNESHROOM = Instrument(
    name="tuneshroom",
    description="Handheld 12-LED Tuneshroom (8-ring + 4-stem)",
    capabilities=frozenset({"light.pixels", "audio.samples",
                            "gesture.tap", "gesture.tilt"}),
    functions=("tap", "tilt"),
    accepted_triggers=("midi", "play", "solid", "mute"),
)
