"""Instruments: first-class entities a room loads (as fixtures) or a device
carries. Pure stdlib -- no luxaeterna, no pyarco (control/ discipline).

Spec: docs/superpowers/specs/2026-08-27-instruments-and-fixtures-design.md.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

from control.functions import Function, FunctionKind, FunctionTable, ScriptStep, validate_function_table
from control.triggers import (
    EventTrigger, StreamTrigger, validate_event_trigger, validate_stream_trigger,
)
from control.cues import TARGET, PlayCue

CAPABILITY_VOCABULARY: frozenset[str] = frozenset({
    "light.pixels",    # addressable pixels of any shape
    "light.surface",   # a linear multi-zone surface (Room-style array)
    "audio.flsyn",     # an Arco FluidSynth voice reachable
    "audio.samples",   # local sample playback
    "gesture.tap",
    "gesture.tilt",
})

CUE_KINDS: tuple[str, ...] = ("midi", "play", "solid", "mute")


def cue_kind(cue) -> str:
    """Classify an expanded cue (control/functions.py's expand_script output)
    by the accepted_cues vocabulary: SolidCue -> "solid", PlayCue ->
    "play", MuteCue -> "mute", everything else (plain 4-tuples, LightCue)
    -> "midi". Imported lazily to avoid control.cues <-> control.instrument
    becoming a cycle if control.cues ever needs an Instrument."""
    from control.cues import MuteCue, PlayCue, SolidCue
    if isinstance(cue, SolidCue):
        return "solid"
    if isinstance(cue, PlayCue):
        return "play"
    if isinstance(cue, MuteCue):
        return "mute"
    return "midi"


class InstrumentError(ValueError):
    pass


@dataclass(frozen=True)
class Instrument:
    name: str
    description: str = ""
    capabilities: frozenset[str] = frozenset()
    functions: tuple[Function, ...] = ()
    accepted_cues: tuple[str, ...] = ()
    light_manifest: dict = field(default_factory=dict)
    ugen_manifest: dict = field(default_factory=dict)
    event_triggers: tuple[EventTrigger, ...] = ()
    stream_triggers: tuple[StreamTrigger, ...] = ()


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
    bad = [k for k in instrument.accepted_cues if k not in CUE_KINDS]
    if bad:
        raise InstrumentError(
            f"instrument {instrument.name!r}: unknown accepted cue "
            f"kind(s) {bad}; known: {list(CUE_KINDS)}")
    for fn in instrument.functions:
        if not isinstance(fn, Function) or fn.kind not in (
                FunctionKind.GENERATOR, FunctionKind.SCRIPTED):
            raise InstrumentError(
                f"instrument {instrument.name!r}: only generator and "
                f"scripted Functions may be declared on an instrument")
    table = FunctionTable(functions={fn.name: fn for fn in instrument.functions})
    try:
        validate_function_table(table, verb_names=frozenset(), owner="instrument")
    except ValueError as exc:
        raise InstrumentError(f"instrument {instrument.name!r}: {exc}") from exc
    for fn in instrument.functions:
        if fn.kind is not FunctionKind.SCRIPTED:
            continue
        for i, step in enumerate(fn.script):
            kind = cue_kind(step.cue)
            if kind not in instrument.accepted_cues:
                raise InstrumentError(
                    f"instrument {instrument.name!r}: function {fn.name!r} "
                    f"script[{i}] is a {kind!r} cue but accepted_cues is "
                    f"{list(instrument.accepted_cues)}")
    where = f"instrument {instrument.name!r}"
    try:
        for trig in instrument.event_triggers:
            validate_event_trigger(trig, where)
        for trig in instrument.stream_triggers:
            validate_stream_trigger(trig, where)
    except ValueError as exc:
        raise InstrumentError(str(exc)) from exc


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


def validate_instrument_manifests(instrument: Instrument) -> None:
    """Validate an instrument's light_manifest/ugen_manifest with the same
    shallow structural checks role_config applies to Roles, located by the
    instrument's own name rather than a role name."""
    from control import role_config
    where = f"instrument {instrument.name!r}"
    try:
        if instrument.light_manifest:
            role_config.validate_light_manifest(
                instrument.light_manifest, f"{where} light_manifest")
        if instrument.ugen_manifest:
            role_config.validate_ugen_manifest(
                instrument.ugen_manifest, f"{where} ugen_manifest")
    except Exception as exc:
        raise InstrumentError(str(exc)) from exc


def ambient_manifests(profile) -> tuple[dict, dict]:
    """Concatenate every fixture's instrument's declared ambient manifests,
    in fixture (declaration) order, into one (light, ugen) manifest pair
    shaped like a LightManifest/ugen manifest input --
    ``{"instruments": [...]}`` -- ready to feed the same
    LightManifest.from_dict pipeline a Bit's declared ROOM role manifest
    goes through (see devicelink/agent.py's _setup_room).

    Returns ({}, {}) when no fixture's instrument declares anything, so a
    caller can tell "nothing to render ambient" apart from "render an empty
    surface" -- an empty dict is falsy, a `{"instruments": []}` manifest is
    not.

    Entries are deep-copied on the way out: these dicts are lifted straight
    off Instrument.light_manifest/ugen_manifest, which are themselves held
    by the loaded RoomProfile/terrarium config -- nothing downstream (a
    LightManifest.from_dict, a caller mutating in place) may be allowed to
    corrupt the config's own copy."""
    light_instruments = []
    ugen_instruments = []
    for fixture in profile.fixtures:
        instrument = fixture.instrument
        light_instruments.extend(
            instrument.light_manifest.get("instruments", []))
        ugen_instruments.extend(
            instrument.ugen_manifest.get("instruments", []))
    light = {"instruments": light_instruments} if light_instruments else {}
    ugen = {"instruments": ugen_instruments} if ugen_instruments else {}
    return copy.deepcopy(light), copy.deepcopy(ugen)


# Values are the mm-tuneshroom native TapDetector's current constants
# (lib/sensors/tap_detector.dart: thresholdG=2.0, debounceDuration=200ms,
# doubleTapWindow=400ms), carried here so the server owns them (Spec 3
# section 6). The browser sensors.js detector mirrors the same heuristic
# but compares a gravity-deviation magnitude rather than a raw peak, so the
# two client detectors disagree by ~3x on what counts as a spike; real
# values come from capture/ traces via tools/trace_stats.py -- a later tool
# pass, not this slice. Shake has no dedicated native detector -- it reuses
# the same TapDetector-derived peak_g/window_ms (www/sensors.js documents
# itself as "mirrors the native TapDetector heuristic"), with no
# double-tap concept.
# MetronomeBit's device-content constants, duplicated here (not imported)
# per the instrument/bit split redirect: bits/ are not being migrated, so
# bits/metronome/metronome_bit.py keeps its own copies untouched and this
# module carries the values TUNESHROOM's scripted functions need. Real
# values copied verbatim from bits/metronome/metronome_bit.py.
RED_CC = 0
GREEN_CC = 42
LEVEL_BASE = 60
LEVEL_PULSE = 110


def _fireworks_script():
    """12 flashes over ~1.4 s, seeded so every build is identical.

    Verbatim copy of bits/metronome/metronome_bit.py's _fireworks_script."""
    rng = random.Random(2026)
    steps = []
    for i in range(12):
        t = i * 0.12
        pitch = rng.randrange(48, 84)
        steps.append(ScriptStep(t, (TARGET, 0xB0, 70, rng.randrange(0, 128))))
        steps.append(ScriptStep(t, (TARGET, 0x90, pitch, 100)))
        steps.append(ScriptStep(t + 0.08, (TARGET, 0x80, pitch, 0)))
    return tuple(steps)


TUNESHROOM = Instrument(
    name="tuneshroom",
    description="Handheld 12-LED Tuneshroom (8-ring + 4-stem)",
    capabilities=frozenset({"light.pixels", "audio.samples",
                            "gesture.tap", "gesture.tilt"}),
    accepted_cues=("midi", "play", "solid", "mute"),
    functions=(
        Function(name="play_aurora", kind=FunctionKind.SCRIPTED,
                 description="Hue bloom on the handheld's ring",
                 script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                         ScriptStep(1.0, (TARGET, 0xB0, 74, 0)))),
        Function(name="win", kind=FunctionKind.SCRIPTED,
                 description="Win celebration: ascending chime plus a hue "
                             "flourish",
                 script=(ScriptStep(0.0, PlayCue(TARGET, "win", "")),
                         ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                         ScriptStep(0.3, (TARGET, 0xB0, 74, 60)),
                         ScriptStep(0.6, (TARGET, 0xB0, 74, 110)),
                         ScriptStep(1.2, (TARGET, 0xB0, 74, 0)))),
        Function(name="fireworks_player", kind=FunctionKind.SCRIPTED,
                 description="Celebratory flashes on the player who nailed it",
                 script=_fireworks_script()),
        Function(name="fail_player", kind=FunctionKind.SCRIPTED,
                 description="Player's light goes red and dark on a miss",
                 script=(ScriptStep(0.0, (TARGET, 0xB0, 74, RED_CC)),
                         ScriptStep(1.0, (TARGET, 0xB0, 11, 0)))),
        Function(name="metro_pulse_player", kind=FunctionKind.SCRIPTED,
                 description="A non-failed player's every-beat level "
                             "pulse-then-decay",
                 script=(ScriptStep(0.0, (TARGET, 0xB0, 11, LEVEL_PULSE)),
                         ScriptStep(0.15, (TARGET, 0xB0, 11, LEVEL_BASE)))),
        Function(name="metro_recovery", kind=FunctionKind.SCRIPTED,
                 description="A failed player's green flash and level reset",
                 script=(ScriptStep(0.0, (TARGET, 0xB0, 74, GREEN_CC)),
                         ScriptStep(0.0, (TARGET, 0xB0, 11, LEVEL_BASE)))),
    ),
    event_triggers=(
        EventTrigger(
            name="tap", description="a single or double tap on the shell",
            thresholds={"peak_g": 2.0, "window_ms": 200, "double_ms": 400}),
        EventTrigger(
            name="shake", description="a shake gesture",
            thresholds={"peak_g": 2.0, "window_ms": 200}),
    ),
)
