"""Engine-side function behavior: load-time validation, firing, and the
on_function_fired observer hook.

Grouped in one file because they share the Bit fixtures below; split from
tests/test_engine.py so the existing lifecycle tests stay readable.
"""

import pytest

from control.bit import Bit
from control.cues import ROOM, TARGET, MuteCue, PlayCue, SolidCue
from control.engine import BitLoadError, GameServer
from control.instrument import CUE_KINDS, TUNESHROOM, Instrument
from control.roles import Role, RoleClass, RoleTable
from control.state import State
from control.terrarium_config import load_terrarium_config
from control.functions import (
    Condition,
    ConditionSource,
    ScriptStep,
    Function,
    FunctionKind,
    FunctionTable,
    FunctionTarget,
    GeneratorSpec,
)


class _BaseBit(Bit):
    version = "0.1"

    @property
    def role_table(self) -> RoleTable:
        return RoleTable(
            roles={"player": Role(name="player", role_class=RoleClass.SHARED,
                                  capacity=None, scored=True)},
            node_map={"NODE": ["player"]})


class PlainBit(_BaseBit):
    """No function_table override at all: the default must keep working."""


class GoodFunctionBit(_BaseBit):
    def verb_handlers(self) -> dict:
        return {"tap": lambda dev, args, at: []}

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={
            "flash": Function(
                name="flash", description="Flash the device",
                target=FunctionTarget.DEVICE,
                condition=Condition(name="tapped", description="Player taps",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="tap"),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),)),
        })


class UnimplementedVerbBit(_BaseBit):
    def verb_handlers(self) -> dict:
        return {"tap": lambda dev, args, at: []}

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={
            "flash": Function(
                name="flash", description="Flash the device",
                target=FunctionTarget.DEVICE,
                condition=Condition(name="wiggled", description="Player wiggles",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="wiggle"),
                script=()),
        })


def _server(bit_cls, **kwargs):
    return GameServer({"bit": bit_cls}, **kwargs)


def test_a_bit_declaring_no_triggers_loads_exactly_as_before():
    gs = _server(PlainBit)
    gs.load_bit("bit")
    assert gs.state == State.SETUP
    assert gs.bit.function_table.functions == {}


def test_a_valid_function_table_loads():
    gs = _server(GoodFunctionBit)
    gs.load_bit("bit")
    assert gs.state == State.SETUP
    assert "flash" in gs.bit.function_table.functions


def test_a_trigger_naming_an_unimplemented_verb_fails_load():
    """Goal 4 and the spec's section 13 test 1: declared-but-unimplemented
    fails as a BitLoadError at load, and Control returns cleanly to IDLE."""
    gs = _server(UnimplementedVerbBit)
    with pytest.raises(BitLoadError, match="wiggle"):
        gs.load_bit("bit")
    assert gs.state == State.IDLE
    assert gs.bit is None
    assert gs.registration is None


class Recorder:
    """An observer that records every hook the engine offers it."""

    def __init__(self):
        self.fired = []

    def on_function_fired(self, record):
        self.fired.append(record)


class RaisingRecorder:
    def __init__(self):
        self.calls = 0

    def on_function_fired(self, record):
        self.calls += 1
        raise RuntimeError("observer exploded")


class ScriptBit(_BaseBit):
    """Both fire paths plus a three-step Room script."""

    def __init__(self):
        self.fire_next = None

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap}

    def _on_tap(self, dev, args, at):
        from control.cues import FireFunction
        return [(dev, 0xB0, 74, 1), FireFunction("flash", dev)]

    def fires(self, at):
        from control.cues import FireFunction
        if self.fire_next is None:
            return []
        name, self.fire_next = self.fire_next, None
        return [FireFunction(name)]

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={
            "sweep": Function(
                name="sweep", description="Sweep the Room",
                target=FunctionTarget.ROOM,
                condition=Condition(name="round_won",
                                    description="User wins a round",
                                    source=ConditionSource.BIT_ADJUDICATED),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                        ScriptStep(0.5, (TARGET, 0xB0, 74, 40)),
                        ScriptStep(2.0, (TARGET, 0xB0, 74, 0)))),
            "flash": Function(
                name="flash", description="Flash the tapping device",
                target=FunctionTarget.DEVICE,
                condition=Condition(name="tapped", description="Player taps",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="tap"),
                script=(ScriptStep(0.0, PlayCue(TARGET, "click", "")),
                        ScriptStep(0.0, (TARGET, 0xB0, 74, 127)))),
            "everywhere": Function(
                name="everywhere", description="Light the whole room",
                target=FunctionTarget.ALL,
                condition=Condition(name="manual", description="Operator asks",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 64)),)),
            "stop": Function(
                name="stop", description="Mute the tapped device",
                target=FunctionTarget.DEVICE,
                condition=Condition(name="manual", description="Operator asks",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, MuteCue(TARGET)),)),
            "spot": Function(
                name="spot", description="Light an operator-chosen surface",
                target=FunctionTarget.SURFACE,
                condition=Condition(name="manual", description="Operator asks",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 100)),)),
        })


class _Room:
    def __init__(self, bound):
        from control.room_profile import (RoomBlock, RoomFixture, RoomProfile,
                                          RoomZone)
        from tests.instrument_fixtures import GENERIC_SURFACE
        self.name = "TEST"
        self.profile = RoomProfile(surface_id="room_test", fixtures=(
            RoomFixture(name="main", color_order="GRB",
                       blocks=(RoomBlock("main", 0, 10),),
                       zones=(RoomZone("all", 0, 10),),
                       instrument=GENERIC_SURFACE),
            RoomFixture(name="accent", color_order="GRB",
                       blocks=(RoomBlock("accent", 0, 10),),
                       zones=(RoomZone("all", 0, 10),),
                       instrument=GENERIC_SURFACE),
        ))
        self.bound = bound   # dict[str, str], fixture name -> dev


def _running(bit_cls=ScriptBit, bound=None, clock=None):
    if bound is None:
        bound = {"main": "sim-room-main"}
    gs = GameServer({"bit": bit_cls}, clock=clock or (lambda: 100.0))
    gs.room = _Room(bound)
    light, play = [], []
    gs.on_light_cue = lambda *a: light.append(a)
    gs.on_play_cue = lambda *a: play.append(a)
    gs.load_bit("bit")
    gs.join("ie1", "NODE")
    gs.run()
    return gs, light, play


def test_manual_fire_dispatches_every_step_with_its_offset():
    gs, light, _ = _running()
    assert gs.fire_function("sweep", fired_by="admin-manual") is None
    assert [c[0] for c in light] == ["sim-room-main"] * 3
    assert [c[4] for c in light] == [100.0, 100.5, 102.0]
    assert [c[3] for c in light] == [127, 40, 0]


def test_a_verb_handler_fire_shares_the_gestures_presentation_time():
    gs, light, play = _running()
    assert gs.data("ie1", "tap", ["ie1"]) is None
    assert play == [("ie1", "click", "")]
    assert [c[0] for c in light] == ["ie1", "ie1"]
    assert {c[4] for c in light} == {100.0}


def test_a_fires_fire_is_recorded_as_bit_adjudicated():
    """Bit.fires(at) replaces Bit.cues(at)'s FireFunction-reporting job:
    drained once per RUNNING tick, in the same place, the same way."""
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.bit.fire_next = "sweep"
    gs.tick(0.01)
    assert [r.fired_by for r in observer.fired] == ["bit-adjudicated"]


def test_fires_returned_fire_with_explicit_at_overrides_the_ticks_at():
    """FireFunction.at (control/cues.py) is the sanctioned seam for a Bit
    to stamp a fire with its own presentation time -- e.g. a beat-grid time
    it computed itself -- instead of inheriting fires(at)'s tick `at`. A
    Bit-supplied `at` must reach fire_function untouched, so the script's
    offsets land relative to THAT time, not the tick's."""
    class GridFiresBit(_BaseBit):
        @property
        def function_table(self) -> FunctionTable:
            return FunctionTable(functions={
                "sweep": Function(
                    name="sweep", description="Sweep the Room",
                    target=FunctionTarget.ROOM,
                    condition=Condition(name="round_won",
                                        description="User wins a round",
                                        source=ConditionSource.BIT_ADJUDICATED),
                    script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                            ScriptStep(0.5, (TARGET, 0xB0, 74, 40)),
                            ScriptStep(2.0, (TARGET, 0xB0, 74, 0)))),
            })

        def fires(self, at):
            from control.cues import FireFunction
            return [FireFunction("sweep", at=500.0)]

    gs, light, _ = _running(bit_cls=GridFiresBit)
    gs.tick(0.01)                      # tick's own `at` would be 100.0
    assert [c[4] for c in light] == [500.0, 500.5, 502.0]


def test_fires_returning_a_plain_cue_tuple_is_dropped_not_dispatched():
    """Bit.fires may return only FireFunctions -- lane-driving from this
    hook is exactly what generators exist to replace. A non-FireFunction
    element is logged and dropped, dispatching nothing."""
    class BadFiresBit(_BaseBit):
        def fires(self, at):
            return [(ROOM, 0xB0, 74, 5)]

    gs, light, _ = _running(bit_cls=BadFiresBit)
    gs.tick(0.01)
    assert light == []


def test_firing_a_generator_function_is_refused_not_scripted():
    class DriftBit(_BaseBit):
        @property
        def function_table(self) -> FunctionTable:
            return FunctionTable(functions={
                "drift": Function(
                    name="drift", description="Ambient drift",
                    kind=FunctionKind.GENERATOR,
                    generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                                            waveform="triangle", period=12.0)),
            })

    gs, _, _ = _running(bit_cls=DriftBit)
    reason = gs.fire_function("drift", fired_by="admin-manual")
    assert reason == "function 'drift' is not scripted"


class GeneratorBit(_BaseBit):
    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={
            "drift": Function(
                name="drift", description="Ambient drift",
                kind=FunctionKind.GENERATOR,
                generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                                        waveform="triangle", period=12.0,
                                        lo=0, hi=254)),
            "flash": Function(
                name="flash", description="Flash cc:74 on the Room",
                target=FunctionTarget.ROOM,
                condition=Condition(name="manual", description="Operator asks",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                        ScriptStep(2.0, (TARGET, 0xB0, 74, 0)))),
        })


def test_generator_cues_dispatch_once_per_running_tick():
    """load_bit builds the GeneratorRunner from the validated table's
    GENERATOR functions; tick() dispatches its non-suppressed lanes through
    on_light_cue, exactly like a scripted fire's steps."""
    gs, light, _ = _running(bit_cls=GeneratorBit)
    gs.tick(3.0)
    assert light == [("sim-room-main", 0xB0, 74, 127, pytest.approx(100.0))]


def test_scripted_fire_suppresses_the_generator_lane_it_writes_and_it_resumes():
    """Overlay, not kill (spec section 4): a scripted fire on the same lane
    a generator drives suppresses that generator's emissions until
    at + span, and the generator resumes -- with its phase having kept
    advancing underneath -- once the window closes."""
    gs, light, _ = _running(bit_cls=GeneratorBit, clock=lambda: 100.0)
    assert gs.fire_function("flash", fired_by="admin-manual") is None
    light.clear()
    gs.tick(0.5)   # elapsed=0.5, at=100.0: inside the flash's 100..102 window
    assert light == []
    gs.tick(2.0)   # elapsed=2.5, at=100.0: still inside the window
    assert light == []


def test_generator_resumes_emitting_once_the_suppression_window_closes():
    gs, light, _ = _running(bit_cls=GeneratorBit, clock=lambda: 100.0)
    assert gs.fire_function("flash", fired_by="admin-manual") is None
    light.clear()
    gs._clock = lambda: 102.5   # past at(100.0) + span(2.0)
    gs.tick(0.1)
    assert [c for c in light if c[2] == 74 and c[0] == "sim-room-main"]


def test_fired_by_never_inherits_declared_source():
    """Spec section 13 test 2: an operator firing a gesture-verb trigger must
    stay distinguishable from a player actually doing it."""
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.fire_function("flash", fired_by="admin-manual", dev="ie1")
    record = observer.fired[0]
    assert record.fired_by == "admin-manual"
    assert record.declared_source == "gesture-verb"


def test_the_record_reports_what_the_fire_resolved_to():
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.fire_function("sweep", fired_by="admin-manual")
    record = observer.fired[0]
    assert record.name == "sweep"
    assert record.condition == "round_won"
    assert record.devs == ("sim-room-main",)
    assert record.steps == 3
    assert record.at == 100.0


def test_function_fired_room_name_is_none_without_a_room():
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.fire_function("sweep", fired_by="admin-manual")
    assert observer.fired[0].room_name is None


def test_function_fired_carries_room_name_from_gs_provenance():
    gs, _, _ = _running()
    gs.provenance = {"room_name": "atrium",
                     "terrarium_config_version": "1-abcdef012345"}
    observer = Recorder()
    gs.add_observer(observer)
    gs.fire_function("sweep", fired_by="admin-manual")
    assert observer.fired[0].room_name == "atrium"


def test_a_target_fanout_across_two_bound_fixtures_feeds_the_room_once_per_step():
    """The Room's TARGET-fanout would double-feed the shared session once per
    bound fixture if not collapsed -- see control/engine.py's
    _collapse_room_fanout. Two fixtures bound, three script steps: still
    exactly 3 light cues, not 6, all addressed to the canonical
    (first-declared) fixture's dev. The fired record still reports every
    fixture the trigger's target resolved to, uncollapsed -- collapsing is a
    fan-out concern, not a reporting one."""
    gs, light, _ = _running(bound={"main": "sim-room-main",
                                   "accent": "sim-room-accent"})
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_function("sweep", fired_by="admin-manual") is None
    assert [c[0] for c in light] == ["sim-room-main"] * 3
    assert observer.fired[0].devs == ("sim-room-main", "sim-room-accent")
    assert observer.fired[0].steps == 3


def test_room_devs_resolve_in_profile_declaration_order_not_bind_order():
    """_resolve_target must walk the profile's fixtures in declaration order
    (main, then accent for the TEST profile), never dict/bind order -- an
    operator can arm and bind accent before main, and nothing about admin
    sequencing prevents that. Bound accent-first here, opposite of profile
    declaration order, to prove the resolved and reported dev order still
    comes out main-then-accent."""
    gs, light, _ = _running(bound={"accent": "sim-room-accent",
                                   "main": "sim-room-main"})
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_function("sweep", fired_by="admin-manual") is None
    assert observer.fired[0].devs == ("sim-room-main", "sim-room-accent")
    assert [c[0] for c in light] == ["sim-room-main"] * 3


def test_resolve_target_on_an_unbound_room_returns_nothing():
    """_resolve_target's room_devs block must short-circuit on "is anything
    bound" before ever walking the profile's fixtures, exactly like its
    sibling _canonical_room_dev does -- an empty Room must never reach a
    profile that happens to be misshapen for its own gate."""
    from control.rooms import Room

    gs = GameServer({}, clock=lambda: 0.0)
    gs.room = Room(name="DEMO", profile=_Room({}).profile, node_id="ROOM_DEMO_NODE")
    assert gs._resolve_target(FunctionTarget.ROOM, None) == []


def test_all_resolves_to_the_room_plus_registered_players_deduped():
    gs, light, _ = _running()
    gs.fire_function("everywhere", fired_by="admin-manual")
    assert [c[0] for c in light] == ["sim-room-main", "ie1"]


def test_all_never_lists_a_room_bound_device_twice():
    gs, light, _ = _running(bound={"main": "ie1"})
    gs.fire_function("everywhere", fired_by="admin-manual")
    assert [c[0] for c in light] == ["ie1"]


def test_a_device_target_with_no_device_is_refused_not_silently_empty():
    gs, light, _ = _running()
    reason = gs.fire_function("flash", fired_by="admin-manual")
    assert reason is not None
    assert "no device given" in reason
    assert light == []


def test_surface_resolves_device():
    gs, _, _ = _running()
    assert gs._resolve_target(FunctionTarget.SURFACE, "ie1") == ["ie1"]


def test_surface_resolves_room_sentinel():
    gs, _, _ = _running()
    assert (gs._resolve_target(FunctionTarget.SURFACE, ROOM) ==
            gs._resolve_target(FunctionTarget.ROOM, None))


def test_surface_fire_without_dev_refused():
    gs, light, _ = _running()
    reason = gs.fire_function("spot", fired_by="admin-manual", dev=None)
    assert reason is not None
    assert "no surface given" in reason
    assert light == []


def test_surface_fire_with_room_sentinel_lights_the_room():
    gs, light, _ = _running()
    assert gs.fire_function("spot", fired_by="admin-manual", dev=ROOM) is None
    assert [c[0] for c in light] == ["sim-room-main"]


def test_an_unknown_trigger_is_refused():
    gs, _, _ = _running()
    assert "unknown function" in gs.fire_function("nope", fired_by="admin-manual")


def test_firing_with_no_bit_running_is_refused():
    gs = GameServer({"bit": ScriptBit})
    assert gs.fire_function("sweep", fired_by="admin-manual") == "no Bit running"


def test_a_room_target_with_no_room_bound_fires_and_reaches_nothing():
    """A fire that reached nothing must be visible as such, not absent."""
    gs, light, _ = _running(bound={})
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_function("sweep", fired_by="admin-manual") is None
    assert light == []
    assert observer.fired[0].devs == ()
    assert observer.fired[0].steps == 0


def test_a_raising_observer_does_not_stop_the_cues_or_its_peers():
    """Spec section 13 test 3, mirroring the on_release/on_light_cue guards."""
    gs, light, _ = _running()
    raiser, recorder = RaisingRecorder(), Recorder()
    gs.add_observer(raiser)
    gs.add_observer(recorder)
    assert gs.fire_function("sweep", fired_by="admin-manual") is None
    assert len(light) == 3
    assert raiser.calls == 1
    assert len(recorder.fired) == 1


def test_an_unknown_trigger_from_a_bit_does_not_break_neighbouring_cues():
    from control.cues import FireFunction
    gs, light, _ = _running()
    gs._dispatch_cues([(ROOM, 0xB0, 74, 5), FireFunction("nope"),
                       (ROOM, 0xB0, 74, 6)], 100.0)
    assert [c[3] for c in light] == [5, 6]


def test_a_bit_whose_function_table_raises_is_refused_not_crashed():
    class ExplodingBit(_BaseBit):
        @property
        def function_table(self):
            raise RuntimeError("boom")

    gs = GameServer({"bit": PlainBit})
    gs.load_bit("bit")
    gs.run()
    gs.bit = ExplodingBit()
    assert gs.fire_function("x", fired_by="admin-manual") == "function table error"


class _FlipFunctionTableBit(_BaseBit):
    """function_table is valid on its first read (the one load_bit validates)
    and structurally invalid on every read after that, modeling a Bit whose
    property builds a fresh object per access -- the same hazard
    RegistrationState's role_table snapshot exists to close for role_table."""

    def __init__(self):
        self._read_once = False

    def verb_handlers(self) -> dict:
        return {}

    @property
    def function_table(self) -> FunctionTable:
        if not self._read_once:
            self._read_once = True
            return FunctionTable(functions={
                "bad": Function(
                    name="bad", description="Valid at load, bad later",
                    target=FunctionTarget.ROOM,
                    condition=Condition(name="c", description="d",
                                        source=ConditionSource.ADMIN_MANUAL),
                    script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),)),
            })
        # A 3-tuple cue: legal nowhere, but load-time validation never sees
        # it, because it only ran against the first, valid, access above.
        return FunctionTable(functions={
            "bad": Function(
                name="bad", description="Valid at load, bad later",
                target=FunctionTarget.ROOM,
                condition=Condition(name="c", description="d",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74)),)),
        })


def test_solid_cue_dispatch_reaches_sink():
    gs, _, _ = _running()
    got = []
    gs.on_solid_cue = lambda *a: got.append(a)
    gs._dispatch_cues([SolidCue("ie1", (255, 255, 255), 0.9, 5.0,
                                when=123.0)], at=120.0)
    assert got == [("ie1", (255, 255, 255), 0.9, 5.0, 123.0)]


def test_solid_cue_without_when_takes_at():
    gs, _, _ = _running()
    got = []
    gs.on_solid_cue = lambda *a: got.append(a)
    gs._dispatch_cues([SolidCue("ie1", (0, 0, 0), 0.0, None)], at=120.0)
    assert got[0][4] == 120.0


def test_mute_cue_latches_and_notifies():
    gs, _, _ = _running()
    got = []
    gs.on_mute_change = lambda dev, m: got.append((dev, m))
    gs._dispatch_cues([MuteCue("ie1")], at=None)
    assert "ie1" in gs.muted and got == [("ie1", True)]


def test_non_mute_fire_clears_mute_first():
    gs, _, _ = _running()
    gs.muted.add("ie1")
    events = []
    gs.on_mute_change = lambda dev, m: events.append((dev, m))
    assert gs.fire_function("flash", fired_by="admin-manual", dev="ie1") is None
    assert "ie1" not in gs.muted and ("ie1", False) in events


def test_mute_fire_does_not_unmute_itself():
    gs, _, _ = _running()
    assert gs.fire_function("stop", fired_by="admin-manual", dev="ie1") is None
    assert "ie1" in gs.muted


def test_raising_sinks_never_wedge():
    gs, _, _ = _running()
    gs.on_solid_cue = lambda *a: 1 / 0
    gs.on_mute_change = lambda *a: 1 / 0
    gs._dispatch_cues([SolidCue("ie1", (1, 1, 1), 1.0, 1.0),
                       MuteCue("ie1")], at=1.0)   # must not raise


def test_unload_clears_mutes():
    gs, _, _ = _running()
    gs.muted.add("ie1")
    events = []
    gs.on_mute_change = lambda dev, m: events.append((dev, m))
    gs.abort()
    assert not gs.muted and ("ie1", False) in events


def test_muted_device_suppresses_play_cue():
    """PlayCue suppression while muted is checked directly against
    _dispatch_cues, bypassing fire_function -- a real fire at the surface
    would itself un-latch the mute first (see
    test_non_mute_fire_clears_mute_first), so this proves the dispatch-time
    guard rather than the un-mute-on-fire rule."""
    gs, _, play = _running()
    gs.muted.add("ie1")
    gs._dispatch_cues([PlayCue("ie1", "click", "")], at=100.0)
    assert play == []


def test_a_function_table_that_turns_invalid_after_load_is_refused_not_crashed():
    """function_table is a property: load_bit validates whatever it returns on
    that one call, and the validated object is never retained. fire_function
    must guard its own later read of the same property rather than trust
    load-time validation to still apply."""
    gs = _server(_FlipFunctionTableBit)
    gs.load_bit("bit")
    reason = gs.fire_function("bad", fired_by="admin-manual")
    assert reason is not None
    assert isinstance(reason, str)


class SolidSurfaceBit(_BaseBit):
    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={
            "glow": Function(
                name="glow", description="Solid glow on a surface",
                target=FunctionTarget.SURFACE,
                condition=Condition(name="manual", description="Operator asks",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(
                    0.0, SolidCue(TARGET, (255, 0, 0), 1.0, None)),)),
        })


def test_fire_refused_when_target_instrument_rejects_cue_kind():
    """spec section 7: a SolidCue fired at a device carrying an instrument
    whose accepted_cues is midi-only must refuse the whole fire, naming
    the rejected kind, and dispatch nothing."""
    narrow = Instrument(name="narrow_midi_only", accepted_cues=("midi",))
    gs = _server(SolidSurfaceBit)
    light = []
    gs.on_light_cue = lambda *a: light.append(a)
    gs.load_bit("bit")
    info = gs.devices.hello("dev1", "Dev1", "1.0")
    info.carried = narrow

    reason = gs.fire_function("glow", fired_by="admin-manual", dev="dev1")

    assert reason is not None
    assert "solid" in reason
    assert light == []


def test_shipped_instruments_accept_every_cue_kind():
    """Pin: TUNESHROOM (every hello'd device's default carried instrument)
    and every instrument parsed from the repo terrarium.toml accept all of
    CUE_KINDS -- nothing shipped today should ever trip the new gate."""
    assert set(TUNESHROOM.accepted_cues) == set(CUE_KINDS)
    config = load_terrarium_config("terrarium.toml")
    assert config.instruments, "expected at least one declared instrument"
    for name, instrument in config.instruments.items():
        assert set(instrument.accepted_cues) == set(CUE_KINDS), name


# --- Task 10: stream triggers transform args in data() before Functions/
# handler see them ---

class TiltRecorderBit(_BaseBit):
    """Records every args list its "tilt"/"tap" verb handlers receive, so
    tests can inspect exactly what data() handed downstream after any
    stream trigger transform. "tap" has no declared StreamTrigger on
    SMOOTHING_WIDGET, so it's the unmatched-verb control."""

    def __init__(self):
        self.received = []

    def verb_handlers(self) -> dict:
        return {"tilt": self._record, "tap": self._record}

    def _record(self, dev, args, at):
        self.received.append(list(args))
        return []


def _joined(bit_cls=TiltRecorderBit, carried=None, run=True):
    from tests.instrument_fixtures import SMOOTHING_WIDGET
    gs = _server(bit_cls)
    gs.load_bit("bit")
    info = gs.devices.hello("ie1", "Dev1", "1.0")
    info.carried = carried if carried is not None else SMOOTHING_WIDGET
    gs.join("ie1", "NODE")
    if run:
        gs.run()
    return gs


def test_first_stream_trigger_sample_passes_through_unchanged():
    gs = _joined()
    assert gs.data("ie1", "tilt", [0.8]) is None
    assert gs.bit.received == [[0.8]]


def test_second_stream_trigger_sample_is_ema_blended():
    gs = _joined()
    gs.data("ie1", "tilt", [0.0])
    gs.data("ie1", "tilt", [1.0])
    # alpha=0.5: y = 0.5*1.0 + 0.5*0.0 = 0.5
    assert gs.bit.received[-1] == [0.5]


def test_stream_trigger_state_is_per_device():
    from tests.instrument_fixtures import SMOOTHING_WIDGET
    gs = _server(TiltRecorderBit)
    gs.load_bit("bit")
    for dev in ("ie1", "ie2"):
        info = gs.devices.hello(dev, dev, "1.0")
        info.carried = SMOOTHING_WIDGET
        gs.join(dev, "NODE")
    gs.run()
    gs.data("ie1", "tilt", [0.0])
    gs.data("ie2", "tilt", [10.0])
    gs.data("ie1", "tilt", [1.0])
    gs.data("ie2", "tilt", [20.0])
    assert gs.bit.received[-2] == [0.5]     # ie1: 0.5*1.0 + 0.5*0.0
    assert gs.bit.received[-1] == [15.0]    # ie2: 0.5*20.0 + 0.5*10.0


def test_stream_trigger_state_resets_after_release():
    """reap_stale is the one engine-level path a device's registration ends
    through today (control/engine.py); release there must clear this dev's
    EMA state so a rejoin starts from a clean first sample."""
    gs = _joined(run=False)     # role is scored: stays in SETUP so it can
                                # accept the rejoin below (scored roles
                                # close once RUNNING, control/registration.py)
    gs.data("ie1", "tilt", [0.0])
    gs.data("ie1", "tilt", [1.0])
    assert gs.bit.received[-1] == [0.5]
    gs.reap_stale(timeout=-1.0)     # every hello'd dev is "stale"
    from tests.instrument_fixtures import SMOOTHING_WIDGET
    info = gs.devices.hello("ie1", "Dev1", "1.0")
    info.carried = SMOOTHING_WIDGET
    gs.join("ie1", "NODE")
    gs.data("ie1", "tilt", [1.0])
    assert gs.bit.received[-1] == [1.0]     # first sample again: passthrough


def test_unmatched_verb_is_untouched_by_stream_triggers():
    gs = _joined()
    original = [0.8]
    assert gs.data("ie1", "tap", original) is None
    assert original == [0.8]


def test_non_numeric_stream_arg_passes_through_untouched_never_raises():
    gs = _joined()
    assert gs.data("ie1", "tilt", ["not-a-number"]) is None
    assert gs.bit.received[-1] == ["not-a-number"]
