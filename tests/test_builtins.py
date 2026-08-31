from control.builtins import RESERVED_NAMES, builtin_functions
from control.cues import TARGET, MuteCue, PlayCue, SolidCue
from control.functions import FunctionKind
from control.instrument import Instrument


def _inst(caps):
    return Instrument(name="t", capabilities=frozenset(caps),
                      accepted_cues=("midi", "play", "solid", "mute"))


def test_reserved_names_are_exactly_flash_stop_ping():
    assert RESERVED_NAMES == frozenset({"flash", "stop", "ping"})


def test_light_only_instrument_gets_flash_and_stop_no_ping():
    fns = builtin_functions(_inst({"light.surface"}))
    assert set(fns) == {"flash", "stop"}
    flash = fns["flash"]
    assert flash.kind is FunctionKind.SCRIPTED
    assert flash.condition is None and flash.target is None
    # light-only: solid white override, no chime
    assert len(flash.script) == 1
    cue = flash.script[0].cue
    assert isinstance(cue, SolidCue)
    assert (cue.dev, cue.rgb, cue.level, cue.duration) == (
        TARGET, (255, 255, 255), 0.9, 5.0)


def test_samples_instrument_flash_adds_chime_and_ping_is_playcue():
    fns = builtin_functions(_inst({"light.pixels", "audio.samples"}))
    assert set(fns) == {"flash", "stop", "ping"}
    kinds = [type(s.cue).__name__ for s in fns["flash"].script]
    assert kinds == ["PlayCue", "SolidCue"]
    ping = fns["ping"].script
    assert len(ping) == 1 and isinstance(ping[0].cue, PlayCue)
    assert ping[0].cue.name == "chime"


def test_flsyn_only_ping_is_a_short_note():
    fns = builtin_functions(_inst({"audio.flsyn"}))
    assert set(fns) == {"stop", "ping"}   # no light.* -> no flash
    steps = fns["ping"].script
    assert [s.cue for s in steps] == [
        (TARGET, 0x90, 57, 100), (TARGET, 0x80, 57, 0)]
    assert [s.offset for s in steps] == [0.0, 0.5]


def test_stop_is_a_mute_latch_and_requires_light_or_audio():
    fns = builtin_functions(_inst({"light.surface"}))
    assert isinstance(fns["stop"].script[0].cue, MuteCue)
    assert builtin_functions(_inst({"gesture.tap"})) == {}
