"""Bit-declared functions: the named things an operator can see coming.

A Bit declares a FunctionTable parallel to its RoleTable. Each entry names a
thing that can happen, describes it in words an operator reads, says where it
lands, names the condition the BIT evaluates, and carries a declarative cue
script: an ordered list of (offset_seconds, cue).

Declarative rather than a callable for three reasons. The Console can render
the real steps rather than only a prose description; a test can assert the
exact cue sequence with no Arco; and manual fire becomes pushing data through
the dispatch path that already exists rather than calling into Bit code at an
arbitrary moment.

Pure and stdlib-only apart from control.cues, which is itself pure, so this
module imports in the offline suite with no renderer and no Arco. Validation
lives here rather than in a sibling config module because, unlike
control/role_config.py, there is no composed device-side blob to keep apart
from the declaration, and expand_script belongs next to the shape it expands.

See docs/superpowers/specs/
2026-08-17-bit-declared-triggers-and-cue-scripts-design.md sections 5 to 7.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum, auto

from control.cues import ROOM, TARGET, LightCue, MuteCue, PlayCue, SolidCue


class FunctionTarget(Enum):
    ROOM = auto()      # every Room fixture
    DEVICE = auto()    # the device that fired, when there was one
    ALL = auto()       # Room fixtures plus every registered non-ROOM device
    SURFACE = auto()   # operator-chosen: one device, or the Room


class FunctionKind(Enum):
    SCRIPTED = auto()   # target + condition + cue script, the original shape
    GENERATOR = auto()  # a continuous free-running waveform on one MIDI lane
    STREAM = auto()     # a live gesture value mapped onto one or more lanes


WAVEFORMS: frozenset[str] = frozenset({"triangle"})
STREAM_MODES: frozenset[str] = frozenset({"linear", "abs"})

_LEGAL_GENERATOR_DEVS = (ROOM, TARGET)


class ConditionSource(Enum):
    GESTURE_VERB = auto()       # a device gesture the Bit adjudicates
    BIT_ADJUDICATED = auto()    # decided inside the Bit, no message behind it
    ADMIN_MANUAL = auto()       # exists only to be fired by an operator


# Wire spellings, so the Console and the fired record read the same words and
# neither has to lowercase an enum name and hope.
SOURCE_WIRE: dict[ConditionSource, str] = {
    ConditionSource.GESTURE_VERB: "gesture-verb",
    ConditionSource.BIT_ADJUDICATED: "bit-adjudicated",
    ConditionSource.ADMIN_MANUAL: "admin-manual",
}

# What actually fired a function THIS time, which is not the same thing as the
# source its condition declares: an operator may fire a gesture-verb function by
# hand, and the record has to keep those distinguishable. See FunctionFired.
FIRED_BY_GESTURE_VERB = "gesture-verb"
FIRED_BY_BIT_ADJUDICATED = "bit-adjudicated"
FIRED_BY_ADMIN_MANUAL = "admin-manual"

# A function name becomes a DOM id in console/static/functions.js, the same way
# a capture label becomes a path component in capture/store.py. Restricted at
# the declaration boundary for the same reason: it is cheaper to refuse an odd
# name at Bit load than to escape it at every consumer.
_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")

_LEGAL_SCRIPT_DEVS = (TARGET, ROOM)


@dataclass(frozen=True)
class Condition:
    """What makes a function fire, in the Bit's own words.

    `verb` is required exactly when source is GESTURE_VERB, and is metadata
    plus something real for load-time validation to check. It does NOT cause
    Control to fire the function when that verb arrives: a tap that misses or a
    tilt below threshold must not fire, so the Bit's handler returns a
    FireFunction explicitly or does not. Auto-firing on verb dispatch would put
    condition evaluation, however trivial, inside Control.
    """
    name: str
    description: str
    source: ConditionSource
    verb: str | None = None


@dataclass(frozen=True)
class ScriptStep:
    """One step of a cue script. `offset` is seconds from the function's `at`.

    `cue` is a plain (dev, status, data1, data2) tuple or a PlayCue, whose dev
    is cues.TARGET or cues.ROOM. A LightCue is refused: it names its own
    absolute time, and the offset is this step's timing.
    """
    offset: float
    cue: object


@dataclass(frozen=True)
class GeneratorSpec:
    """A continuous free-running waveform written to one MIDI lane.

    `period` is seconds for one full cycle. `lo`/`hi` bound the emitted
    value (both 0-255, lo <= hi); most lanes will use the MIDI 0-127 range,
    but the bound is not clamped to it, matching data1/data2 elsewhere in
    this module.
    """
    dev: str
    status: int
    data1: int
    waveform: str
    period: float
    lo: int = 0
    hi: int = 127


@dataclass(frozen=True)
class StreamOutput:
    """One lane a StreamSpec writes to, with its own output range and mode.

    `out_lo`/`out_hi` are floats and may be inverted (out_lo > out_hi) to
    reverse the mapping; `mode` selects how the input value is transformed
    before scaling (see STREAM_MODES).
    """
    dev: str
    status: int
    data1: int
    out_lo: float
    out_hi: float
    mode: str = "linear"


@dataclass(frozen=True)
class StreamSpec:
    """A live gesture value mapped onto one or more MIDI lanes.

    `verb` names the gesture whose value drives this stream; `arg` selects
    which of the gesture's positional values to read. `in_lo`/`in_hi` bound
    the domain (in_lo < in_hi); each entry in `outputs` maps that domain onto
    its own output range.

    Two streams on the SAME verb may write the same output lane as long as
    their `in_lo`/`in_hi` domains do not overlap. They may touch at a
    single shared boundary point (one's in_hi equal to the other's
    in_lo); at that exact point, the function whose domain is lower (the
    one for which the shared value is its in_hi) applies. Two streams on
    DIFFERENT verbs may share a lane with overlapping domains freely --
    GameServer.data() dispatches one verb's streams per call, so they
    never compete for the same gesture.
    """
    verb: str
    arg: int
    in_lo: float
    in_hi: float
    outputs: tuple[StreamOutput, ...]


@dataclass(frozen=True)
class Function:
    name: str
    description: str
    target: FunctionTarget | None = None
    condition: Condition | None = None
    script: tuple[ScriptStep, ...] = ()
    kind: FunctionKind = FunctionKind.SCRIPTED
    generator: GeneratorSpec | None = None
    stream: StreamSpec | None = None


@dataclass
class FunctionTable:
    functions: dict[str, Function] = field(default_factory=dict)


@dataclass(frozen=True)
class FunctionFired:
    """One fire, as the Console and any future uplink observer see it.

    fired_by and declared_source are separate on purpose. A manual fire of a
    gesture-verb function records fired_by="admin-manual" against
    declared_source="gesture-verb"; collapsing them is what would make an
    operator action indistinguishable from real gameplay in the log.

    devs and steps report what the fire RESOLVED to, not what it declared, so a
    fire that reached nothing is visibly that rather than silently absent.
    """
    name: str
    condition: str
    fired_by: str
    declared_source: str
    dev: str | None
    devs: tuple[str, ...]
    at: float
    steps: int
    room_name: str | None = None


def generator_lane(fn: Function) -> tuple[str, int, int]:
    """The (dev, status, data1) lane a GENERATOR Function writes to.

    Two GENERATOR functions sharing a lane would fight over the same MIDI
    value every cycle, so validate_function_table refuses that at load time.
    """
    return (fn.generator.dev, fn.generator.status, fn.generator.data1)


def stream_input(spec: StreamSpec, args: list) -> float | None:
    """The raw numeric value args[spec.arg] resolves to, or None when the
    arg is missing or not a number (a malformed gesture, never raised on).

    Shared by stream_cues (to build a mapped value) and GameServer.data()
    (to test domain membership across sibling STREAM functions on the same
    verb, ahead of any per-output mode transform)."""
    if not isinstance(args, (list, tuple)) or spec.arg >= len(args):
        return None
    x = args[spec.arg]
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    return x


def stream_cues(fn: Function, dev: str, args: list) -> list[tuple]:
    """Mapped plain cues for one arriving verb. Clamps args[spec.arg]
    to [in_lo, in_hi] (mode "abs" takes abs(x) first), maps linearly
    onto each output's [out_lo, out_hi] (floats; inverted legal),
    int(round(...)), clamps 0-255. TARGET -> the gesturing dev;
    ROOM passes through for _resolve_dev. A missing/non-numeric arg
    returns [] (a malformed gesture maps to nothing, never raises).
    Boundary rule: when the value sits on two touching domains, the
    function whose in_hi it is applies (enforced by matching
    in_lo <= x <= in_hi in declaration order and skipping a later
    match on an already-written lane)."""
    spec = fn.stream
    x = stream_input(spec, args)
    if x is None:
        return []
    span_in = spec.in_hi - spec.in_lo
    out: list[tuple] = []
    for output in spec.outputs:
        val = abs(x) if output.mode == "abs" else x
        clamped = min(max(val, spec.in_lo), spec.in_hi)
        t = (clamped - spec.in_lo) / span_in if span_in else 0.0
        mapped = output.out_lo + t * (output.out_hi - output.out_lo)
        data2 = max(0, min(255, int(round(mapped))))
        out_dev = dev if output.dev == TARGET else output.dev
        out.append((out_dev, output.status, output.data1, data2))
    return out


def collect_stream_cues(streams, dev: str, args: list) -> list[tuple]:
    """Mapped cues for every STREAM function in `streams` (already
    verb-filtered by the caller -- see GameServer.data) whose domain
    contains this gesture's arg, in declaration order, first write wins
    per output lane (stream_cues's touching-boundary rule).

    Edge-clamp rule: a raw arg outside every declared domain on a lane is
    not simply dropped. If it is below the lane's lowest in_lo, the
    function that owns that lowest in_lo still applies, with the value
    clamped to its in_lo (stream_cues already clamps internally, so
    calling it unmodified produces exactly the in_lo-mapped cue);
    symmetrically for an arg above the lane's highest in_hi and the
    function owning it. This reproduces the old imperative handlers'
    defensive clamp -- a device overshooting 90 degrees still reads as
    full deflection -- without a Bit having to declare its own overflow
    guard functions. A value in a GAP between two disjoint (non-touching)
    domains is unaffected by this rule and still drops: only a value
    beyond the lane's whole domain hull clamps to an edge.
    """
    lane_domains: dict[tuple[str, int, int], list[tuple[float, float, Function]]] = {}
    for fn in streams:
        spec = fn.stream
        for output in spec.outputs:
            lane = (output.dev, output.status, output.data1)
            lane_domains.setdefault(lane, []).append(
                (float(spec.in_lo), float(spec.in_hi), fn))

    written_lanes: set[tuple[str, int, int]] = set()
    out: list[tuple] = []
    for fn in streams:
        spec = fn.stream
        x = stream_input(spec, args)
        if x is None:
            continue
        for cue, output in zip(stream_cues(fn, dev, args), spec.outputs):
            lane = (output.dev, output.status, output.data1)
            if lane in written_lanes:
                continue
            if not (spec.in_lo <= x <= spec.in_hi):
                domains = lane_domains[lane]
                lo_lo, _, lo_owner = min(domains, key=lambda d: d[0])
                _, hi_hi, hi_owner = max(domains, key=lambda d: d[1])
                is_edge = (fn is lo_owner and x < lo_lo) or \
                          (fn is hi_owner and x > hi_hi)
                if not is_edge:
                    continue
            written_lanes.add(lane)
            out.append(cue)
    return out


def validate_function_table(function_table, verb_names, *, owner="bit") -> None:
    """Shallow structural validation of an authored FunctionTable.

    Called from GameServer.load_bit alongside validate_role_declarations, and
    from validate_instrument, and raises ValueError with a message locating
    the offending field, so a typo'd Bit or instrument fails as a load-time
    error rather than mid-installation.

    `owner` distinguishes the two declaration sites: "bit" (default) is a
    Bit's own FunctionTable, where SCRIPTED is a name-fire (script=()) whose
    content lives on the resolved instrument and target+condition are
    required. "instrument" is an instrument's own FunctionTable, where
    SCRIPTED carries the actual content (a non-empty script, implicitly
    targeting the instrument's own surface via cues.TARGET only) and may not
    declare a target or condition. Reserved built-in names (flash/stop/ping,
    control.builtins.RESERVED_NAMES) are refused for both owners so a
    built-in can never be shadowed. STREAM is Bit-declared gameplay and is
    refused on an instrument; GENERATOR is unchanged for either owner.

    `verb_names` is the key set of the Bit's verb_handlers(). Cross-referencing
    it here is what makes a declared-but-unimplemented gesture function a load
    failure. Deliberately shallow everywhere else, in the same places
    control/role_config.py is: a cue's controller number is not checked against
    any manifest lane, because an undeclared cc is already dropped by design in
    AudioBridge._apply_midi, and the light half's instrument registry belongs
    to luxaeterna, which Control cannot see.
    """
    from control.builtins import RESERVED_NAMES  # lazy: builtins imports us
    if owner not in ("bit", "instrument"):
        raise ValueError(f"owner must be 'bit' or 'instrument', got {owner!r}")
    if not isinstance(function_table, FunctionTable):
        raise ValueError(
            f"function_table: must be a FunctionTable, "
            f"got {type(function_table).__name__}")
    # A scripted Function's GESTURE_VERB condition may name either a verb
    # implemented by verb_handlers() or a verb declared by a STREAM Function
    # in this same table -- a verb can be entirely stream-driven, with no
    # handler at all, and still be a legal condition target. Computed as a
    # first pass so declaration order within the table does not matter.
    stream_verbs: set[str] = {
        function_decl.stream.verb
        for function_decl in function_table.functions.values()
        if isinstance(function_decl, Function)
        and function_decl.kind is FunctionKind.STREAM
        and isinstance(function_decl.stream, StreamSpec)
        and isinstance(function_decl.stream.verb, str)
    }
    allowed_verbs = set(verb_names) | stream_verbs
    generator_lanes: dict[tuple[str, int, int], str] = {}
    streams: list[Function] = []
    for key, function_decl in function_table.functions.items():
        where = f"function {key!r}"
        if not isinstance(function_decl, Function):
            raise ValueError(
                f"{where}: must be a Function, got {type(function_decl).__name__}")
        if function_decl.name != key:
            raise ValueError(
                f"{where}: name {function_decl.name!r} does not match its key")
        if not isinstance(function_decl.name, str) or not _NAME_RE.match(function_decl.name):
            raise ValueError(
                f"{where}: name {function_decl.name!r} must match [A-Za-z0-9_-]+ "
                f"(it becomes a DOM id on the Console)")
        if not isinstance(function_decl.description, str) or not function_decl.description:
            raise ValueError(f"{where}: description must be non-empty")
        if not isinstance(function_decl.kind, FunctionKind):
            raise ValueError(
                f"{where}: kind must be a FunctionKind, got {function_decl.kind!r}")
        if owner == "instrument" and function_decl.name in RESERVED_NAMES:
            raise ValueError(
                f"{where}: {function_decl.name!r} is a reserved built-in "
                f"name (flash/stop/ping) and may not be declared")
        if function_decl.kind is FunctionKind.SCRIPTED:
            if owner == "bit":
                _validate_scripted(function_decl, allowed_verbs)
            else:
                if not function_decl.script:
                    raise ValueError(
                        f"{where}: an instrument scripted function must "
                        f"carry a non-empty script")
                if function_decl.target is not None:
                    raise ValueError(
                        f"{where}: target is resolution-time; an instrument "
                        f"function may not declare one")
                if function_decl.condition is not None:
                    raise ValueError(
                        f"{where}: condition is a Bit concern; an "
                        f"instrument function may not declare one")
                if function_decl.generator is not None:
                    raise ValueError(
                        f"{where}: generator is only meaningful on a "
                        f"GENERATOR function, not on SCRIPTED")
                if function_decl.stream is not None:
                    raise ValueError(
                        f"{where}: stream is only meaningful on a STREAM "
                        f"function, not on SCRIPTED")
                _validate_script(function_decl)
                for idx, step in enumerate(function_decl.script):
                    dev = step.cue.dev if hasattr(step.cue, "dev") \
                        else step.cue[0]
                    if dev != TARGET:
                        raise ValueError(
                            f"{where} script[{idx}]: instrument scripts "
                            f"implicitly target their own surface; only "
                            f"cues.TARGET is legal, got {dev!r}")
        elif function_decl.kind is FunctionKind.GENERATOR:
            _validate_generator(function_decl)
            lane = generator_lane(function_decl)
            if lane in generator_lanes:
                raise ValueError(
                    f"{where}: generator lane {lane!r} is already written by "
                    f"function {generator_lanes[lane]!r}; two generators may "
                    f"not share a lane")
            generator_lanes[lane] = function_decl.name
        elif function_decl.kind is FunctionKind.STREAM:
            if owner == "instrument":
                raise ValueError(
                    f"{where}: STREAM functions are Bit-declared gameplay "
                    f"and may not live on an instrument")
            _validate_stream(function_decl)
            streams.append(function_decl)
    _validate_stream_lane_overlap(streams)


def _validate_scripted(function_decl: Function, verb_names) -> None:
    where = f"function {function_decl.name!r}"
    if not isinstance(function_decl.target, FunctionTarget):
        raise ValueError(
            f"{where}: target must be a FunctionTarget, "
            f"got {function_decl.target!r}")
    if function_decl.generator is not None:
        raise ValueError(
            f"{where}: generator is only meaningful on a GENERATOR function, "
            f"not on SCRIPTED")
    if function_decl.stream is not None:
        raise ValueError(
            f"{where}: stream is only meaningful on a STREAM function, "
            f"not on SCRIPTED")
    _validate_condition(function_decl, verb_names)
    _validate_script(function_decl)


def _validate_generator(function_decl: Function) -> None:
    where = f"function {function_decl.name!r} generator"
    spec = function_decl.generator
    if not isinstance(spec, GeneratorSpec):
        raise ValueError(
            f"{where}: must be a GeneratorSpec, got {type(spec).__name__}")
    if function_decl.target is not None:
        raise ValueError(
            f"function {function_decl.name!r}: target is only meaningful on "
            f"a SCRIPTED function, not on GENERATOR")
    if function_decl.condition is not None:
        raise ValueError(
            f"function {function_decl.name!r}: condition is only meaningful "
            f"on a SCRIPTED function, not on GENERATOR")
    if function_decl.script:
        raise ValueError(
            f"function {function_decl.name!r}: script is only meaningful on "
            f"a SCRIPTED function, not on GENERATOR")
    if spec.waveform not in WAVEFORMS:
        raise ValueError(
            f"{where}: waveform {spec.waveform!r} must be one of "
            f"{sorted(WAVEFORMS)}")
    if (isinstance(spec.period, bool) or not isinstance(spec.period, (int, float))
            or not math.isfinite(float(spec.period)) or float(spec.period) <= 0):
        raise ValueError(f"{where}: period must be > 0 and finite, got {spec.period!r}")
    for label, value in (("lo", spec.lo), ("hi", spec.hi)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError(f"{where}: {label} {value!r} must be an int in 0-255")
    if spec.lo > spec.hi:
        raise ValueError(f"{where}: lo {spec.lo} must be <= hi {spec.hi}")
    for label, value in (("status", spec.status), ("data1", spec.data1)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError(f"{where}: {label} {value!r} must be an int in 0-255")
    if spec.dev not in _LEGAL_GENERATOR_DEVS:
        raise ValueError(
            f"{where}: dev must be cues.ROOM ({ROOM!r}) or cues.TARGET "
            f"({TARGET!r}), got {spec.dev!r}")


def _validate_stream(function_decl: Function) -> None:
    where = f"function {function_decl.name!r} stream"
    spec = function_decl.stream
    if not isinstance(spec, StreamSpec):
        raise ValueError(
            f"{where}: must be a StreamSpec, got {type(spec).__name__}")
    if function_decl.target is not None:
        raise ValueError(
            f"function {function_decl.name!r}: target is only meaningful on "
            f"a SCRIPTED function, not on STREAM")
    if function_decl.condition is not None:
        raise ValueError(
            f"function {function_decl.name!r}: condition is only meaningful "
            f"on a SCRIPTED function, not on STREAM")
    if function_decl.script:
        raise ValueError(
            f"function {function_decl.name!r}: script is only meaningful on "
            f"a SCRIPTED function, not on STREAM")
    if not isinstance(spec.verb, str) or not spec.verb:
        raise ValueError(f"{where}: verb must be non-empty")
    if isinstance(spec.arg, bool) or not isinstance(spec.arg, int) or spec.arg < 0:
        raise ValueError(f"{where}: arg must be an int >= 0, got {spec.arg!r}")
    for label, value in (("in_lo", spec.in_lo), ("in_hi", spec.in_hi)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)):
            raise ValueError(f"{where}: {label} must be a finite number, got {value!r}")
    if not float(spec.in_lo) < float(spec.in_hi):
        raise ValueError(
            f"{where}: in_lo {spec.in_lo} must be < in_hi {spec.in_hi}")
    if not isinstance(spec.outputs, tuple) or not spec.outputs:
        raise ValueError(f"{where}: outputs must be a non-empty tuple")
    for idx, output in enumerate(spec.outputs):
        _validate_stream_output(output, f"{where} outputs[{idx}]")


def _validate_stream_output(output: StreamOutput, where: str) -> None:
    if not isinstance(output, StreamOutput):
        raise ValueError(f"{where}: must be a StreamOutput, got {type(output).__name__}")
    if output.dev not in _LEGAL_GENERATOR_DEVS:
        raise ValueError(
            f"{where}: dev must be cues.ROOM ({ROOM!r}) or cues.TARGET "
            f"({TARGET!r}), got {output.dev!r}")
    for label, value in (("status", output.status), ("data1", output.data1)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError(f"{where}: {label} {value!r} must be an int in 0-255")
    for label, value in (("out_lo", output.out_lo), ("out_hi", output.out_hi)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)):
            raise ValueError(f"{where}: {label} must be a finite number, got {value!r}")
    if output.mode not in STREAM_MODES:
        raise ValueError(
            f"{where}: mode {output.mode!r} must be one of {sorted(STREAM_MODES)}")


def _validate_stream_lane_overlap(streams: list) -> None:
    """Two STREAM functions on the SAME verb may not write overlapping
    domains onto the same lane -- one gesture value could satisfy both,
    and collect_stream_cues's first-write-wins boundary rule only
    resolves a single shared point, not a real overlap.

    Two different verbs sharing a lane are unrestricted here even when
    their domains overlap as plain numeric ranges: GameServer.data()
    dispatches one verb's streams per call, so a tilt's domain and a
    shake's domain on the same lane never compete for the same gesture --
    the verb itself already disambiguates which one applies.
    """
    lanes: dict[tuple[str, int, int, str],
               list[tuple[float, float, str]]] = {}
    for function_decl in streams:
        spec = function_decl.stream
        for output in spec.outputs:
            lane = (output.dev, output.status, output.data1, spec.verb)
            lanes.setdefault(lane, []).append(
                (float(spec.in_lo), float(spec.in_hi), function_decl.name))
    for lane, intervals in lanes.items():
        intervals.sort(key=lambda item: item[0])
        for (_, prev_hi, prev_name), (next_lo, _, next_name) in zip(
                intervals, intervals[1:]):
            if prev_hi > next_lo:
                raise ValueError(
                    f"stream lane {lane[:3]!r} verb {lane[3]!r}: functions "
                    f"{prev_name!r} and {next_name!r} overlap on their "
                    f"input domains")


def _validate_condition(function_decl: Function, verb_names) -> None:
    where = f"function {function_decl.name!r} condition"
    condition = function_decl.condition
    if not isinstance(condition, Condition):
        raise ValueError(
            f"{where}: must be a Condition, got {type(condition).__name__}")
    if not isinstance(condition.name, str) or not condition.name:
        raise ValueError(f"{where}: name must be non-empty")
    if not isinstance(condition.description, str) or not condition.description:
        raise ValueError(f"{where}: description must be non-empty")
    if not isinstance(condition.source, ConditionSource):
        raise ValueError(
            f"{where}: source must be a ConditionSource, "
            f"got {condition.source!r}")
    if condition.source is ConditionSource.GESTURE_VERB:
        if not condition.verb:
            raise ValueError(
                f"{where}: a gesture-verb condition must name a verb")
        if condition.verb not in verb_names:
            raise ValueError(
                f"{where}: verb {condition.verb!r} is not implemented by "
                f"verb_handlers() (implemented: {sorted(verb_names)})")
    elif condition.verb is not None:
        raise ValueError(
            f"{where}: verb {condition.verb!r} is only meaningful on a "
            f"gesture-verb condition, not on "
            f"{SOURCE_WIRE[condition.source]}")


def _validate_script(function_decl: Function) -> None:
    script = function_decl.script
    if not isinstance(script, tuple):
        raise ValueError(
            f"function {function_decl.name!r} script: must be a tuple, "
            f"got {type(script).__name__}")
    previous: float | None = None
    for idx, step in enumerate(script):
        where = f"function {function_decl.name!r} script[{idx}]"
        if not isinstance(step, ScriptStep):
            raise ValueError(
                f"{where}: must be a ScriptStep, got {type(step).__name__}")
        offset = _validate_offset(step, where, previous)
        previous = offset
        _validate_step_cue(step, where)


def _validate_offset(step: ScriptStep, where: str,
                     previous: float | None) -> float:
    offset = step.offset
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        raise ValueError(f"{where}: offset must be a number, got {offset!r}")
    offset = float(offset)
    if not math.isfinite(offset):
        raise ValueError(f"{where}: offset must be finite, got {step.offset!r}")
    if offset < 0:
        raise ValueError(f"{where}: offset must be >= 0, got {offset}")
    if previous is not None and offset < previous:
        raise ValueError(
            f"{where}: offset {offset} is earlier than the previous step's "
            f"{previous}; steps must be in non-decreasing order, because the "
            f"Console renders them as a sequence")
    return offset


def _validate_step_cue(step: ScriptStep, where: str) -> None:
    cue = step.cue
    if isinstance(cue, LightCue):
        raise ValueError(
            f"{where}: a LightCue names its own absolute time, and a script "
            f"step's timing is its offset. Declare a plain "
            f"(dev, status, data1, data2) tuple instead")
    if isinstance(cue, PlayCue):
        if float(step.offset) != 0.0:
            raise ValueError(
                f"{where}: a PlayCue must sit at offset 0. The device owns "
                f"when a local sample fires and the play path has no queue, so "
                f"a non-zero offset would be silently ignored")
        _validate_script_dev(cue.dev, where)
        return
    if isinstance(cue, SolidCue):
        _validate_script_dev(cue.dev, where)
        for i, ch in enumerate(cue.rgb):
            if isinstance(ch, bool) or not isinstance(ch, int) or not 0 <= ch <= 255:
                raise ValueError(f"{where}: rgb[{i}] {ch!r} is outside 0-255")
        if not isinstance(cue.level, (int, float)) or not 0.0 <= float(cue.level) <= 1.0:
            raise ValueError(f"{where}: level {cue.level!r} is outside 0.0-1.0")
        if cue.duration is not None and (
                not isinstance(cue.duration, (int, float))
                or not math.isfinite(float(cue.duration))
                or float(cue.duration) <= 0):
            raise ValueError(f"{where}: duration must be > 0 seconds or None")
        if cue.when is not None:
            raise ValueError(f"{where}: a script SolidCue's timing is its "
                             f"offset; leave when=None")
        return
    if isinstance(cue, MuteCue):
        if float(step.offset) != 0.0:
            raise ValueError(f"{where}: a MuteCue must sit at offset 0; the "
                             f"latch is immediate, an offset would be ignored")
        _validate_script_dev(cue.dev, where)
        return
    if not isinstance(cue, tuple) or len(cue) != 4:
        raise ValueError(
            f"{where}: cue must be a PlayCue or a 4-tuple "
            f"(dev, status, data1, data2), got {cue!r}")
    dev, status, data1, data2 = cue
    _validate_script_dev(dev, where)
    for label, value in (("status", status), ("data1", data1),
                         ("data2", data2)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where}: {label} must be an int, got {value!r}")
        if not 0 <= value <= 255:
            raise ValueError(f"{where}: {label} {value} is outside 0-255")


def _validate_script_dev(dev, where: str) -> None:
    if dev not in _LEGAL_SCRIPT_DEVS:
        raise ValueError(
            f"{where}: dev must be cues.TARGET ({TARGET!r}) or cues.ROOM "
            f"({ROOM!r}), got {dev!r}. Device ids are assigned at runtime, so "
            f"a literal in a static declaration can never resolve")


def expand_script(function_decl: Function, at: float, devs) -> list:
    """Turn a declared script into concrete, timed cues for _dispatch_cues.

    Every light step becomes a LightCue carrying an explicit when of
    at + offset. GameServer._dispatch_cues already honors a LightCue's own
    when, and DeviceLinkAgent._on_light_cue already holds a cue further out
    than one horizon on its _light_cues queue, so this slice adds no
    scheduler and no second copy of horizon arithmetic. That holding branch
    was written on 2026-08-14 for exactly this case; expansion only supplies
    it with input.

    A step addressed at cues.TARGET fans out to every dev the function's target
    resolved to. A step addressed at cues.ROOM is left alone and resolved
    downstream, so one script can address the Room explicitly even when its
    own target is DEVICE.
    """
    out: list = []
    for step in function_decl.script:
        when = at + float(step.offset)
        cue = step.cue
        if isinstance(cue, PlayCue):
            for dev in _step_devs(cue.dev, devs):
                out.append(PlayCue(dev, cue.name, cue.params))
            continue
        if isinstance(cue, SolidCue):
            for dev in _step_devs(cue.dev, devs):
                out.append(SolidCue(dev, cue.rgb, cue.level, cue.duration,
                                    when=when))
            continue
        if isinstance(cue, MuteCue):
            for dev in _step_devs(cue.dev, devs):
                out.append(MuteCue(dev))
            continue
        step_dev, status, data1, data2 = cue
        for dev in _step_devs(step_dev, devs):
            out.append(LightCue(dev, status, data1, data2, when=when))
    return out


def _step_devs(step_dev: str, devs) -> tuple:
    """TARGET fans out; anything else (in practice ROOM, the only other legal
    value per _validate_script_dev) passes through as itself."""
    if step_dev == TARGET:
        return tuple(devs)
    return (step_dev,)
