"""Engine-side trigger behavior: load-time validation, firing, and the
on_trigger_fired observer hook.

Grouped in one file because they share the Bit fixtures below; split from
tests/test_engine.py so the existing lifecycle tests stay readable.
"""

import pytest

from control.bit import Bit
from control.cues import ROOM, TARGET, PlayCue
from control.engine import BitLoadError, GameServer
from control.roles import Role, RoleClass, RoleTable
from control.state import State
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerTable,
    TriggerTarget,
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
    """No trigger_table override at all: the default must keep working."""


class GoodTriggerBit(_BaseBit):
    def verb_handlers(self) -> dict:
        return {"tap": lambda dev, args, at: []}

    @property
    def trigger_table(self) -> TriggerTable:
        return TriggerTable(triggers={
            "flash": Trigger(
                name="flash", description="Flash the device",
                target=TriggerTarget.DEVICE,
                condition=Condition(name="tapped", description="Player taps",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="tap"),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),)),
        })


class UnimplementedVerbBit(_BaseBit):
    def verb_handlers(self) -> dict:
        return {"tap": lambda dev, args, at: []}

    @property
    def trigger_table(self) -> TriggerTable:
        return TriggerTable(triggers={
            "flash": Trigger(
                name="flash", description="Flash the device",
                target=TriggerTarget.DEVICE,
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
    assert gs.bit.trigger_table.triggers == {}


def test_a_valid_trigger_table_loads():
    gs = _server(GoodTriggerBit)
    gs.load_bit("bit")
    assert gs.state == State.SETUP
    assert "flash" in gs.bit.trigger_table.triggers


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

    def on_trigger_fired(self, record):
        self.fired.append(record)


class RaisingRecorder:
    def __init__(self):
        self.calls = 0

    def on_trigger_fired(self, record):
        self.calls += 1
        raise RuntimeError("observer exploded")


class ScriptBit(_BaseBit):
    """Both fire paths plus a three-step Room script."""

    def __init__(self):
        self.fire_next = None

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap}

    def _on_tap(self, dev, args, at):
        from control.cues import FireTrigger
        return [(dev, 0xB0, 74, 1), FireTrigger("flash", dev)]

    def cues(self, at):
        from control.cues import FireTrigger
        if self.fire_next is None:
            return []
        name, self.fire_next = self.fire_next, None
        return [FireTrigger(name)]

    @property
    def trigger_table(self) -> TriggerTable:
        return TriggerTable(triggers={
            "sweep": Trigger(
                name="sweep", description="Sweep the Room",
                target=TriggerTarget.ROOM,
                condition=Condition(name="round_won",
                                    description="User wins a round",
                                    source=ConditionSource.BIT_ADJUDICATED),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                        ScriptStep(0.5, (TARGET, 0xB0, 74, 40)),
                        ScriptStep(2.0, (TARGET, 0xB0, 74, 0)))),
            "flash": Trigger(
                name="flash", description="Flash the tapping device",
                target=TriggerTarget.DEVICE,
                condition=Condition(name="tapped", description="Player taps",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="tap"),
                script=(ScriptStep(0.0, PlayCue(TARGET, "click", "")),
                        ScriptStep(0.0, (TARGET, 0xB0, 74, 127)))),
            "everywhere": Trigger(
                name="everywhere", description="Light the whole room",
                target=TriggerTarget.ALL,
                condition=Condition(name="manual", description="Operator asks",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 64)),)),
        })


class _Room:
    def __init__(self, bound):
        from control.rooms import RoomType
        self.room_type = RoomType.TEST
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
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert [c[0] for c in light] == ["sim-room-main"] * 3
    assert [c[4] for c in light] == [100.0, 100.5, 102.0]
    assert [c[3] for c in light] == [127, 40, 0]


def test_a_verb_handler_fire_shares_the_gestures_presentation_time():
    gs, light, play = _running()
    assert gs.data("ie1", "tap", ["ie1"]) is None
    assert play == [("ie1", "click", "")]
    assert [c[0] for c in light] == ["ie1", "ie1"]
    assert {c[4] for c in light} == {100.0}


def test_a_cues_fire_is_recorded_as_bit_adjudicated():
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.bit.fire_next = "sweep"
    gs.tick(0.01)
    assert [r.fired_by for r in observer.fired] == ["bit-adjudicated"]


def test_fired_by_never_inherits_declared_source():
    """Spec section 13 test 2: an operator firing a gesture-verb trigger must
    stay distinguishable from a player actually doing it."""
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.fire_trigger("flash", fired_by="admin-manual", dev="ie1")
    record = observer.fired[0]
    assert record.fired_by == "admin-manual"
    assert record.declared_source == "gesture-verb"


def test_the_record_reports_what_the_fire_resolved_to():
    gs, _, _ = _running()
    observer = Recorder()
    gs.add_observer(observer)
    gs.fire_trigger("sweep", fired_by="admin-manual")
    record = observer.fired[0]
    assert record.name == "sweep"
    assert record.condition == "round_won"
    assert record.devs == ("sim-room-main",)
    assert record.steps == 3
    assert record.at == 100.0


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
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert [c[0] for c in light] == ["sim-room-main"] * 3
    assert observer.fired[0].devs == ("sim-room-main", "sim-room-accent")
    assert observer.fired[0].steps == 3


def test_all_resolves_to_the_room_plus_registered_players_deduped():
    gs, light, _ = _running()
    gs.fire_trigger("everywhere", fired_by="admin-manual")
    assert [c[0] for c in light] == ["sim-room-main", "ie1"]


def test_all_never_lists_a_room_bound_device_twice():
    gs, light, _ = _running(bound={"main": "ie1"})
    gs.fire_trigger("everywhere", fired_by="admin-manual")
    assert [c[0] for c in light] == ["ie1"]


def test_a_device_target_with_no_device_is_refused_not_silently_empty():
    gs, light, _ = _running()
    reason = gs.fire_trigger("flash", fired_by="admin-manual")
    assert reason is not None
    assert "no device given" in reason
    assert light == []


def test_an_unknown_trigger_is_refused():
    gs, _, _ = _running()
    assert "unknown trigger" in gs.fire_trigger("nope", fired_by="admin-manual")


def test_firing_with_no_bit_running_is_refused():
    gs = GameServer({"bit": ScriptBit})
    assert gs.fire_trigger("sweep", fired_by="admin-manual") == "no Bit running"


def test_a_room_target_with_no_room_bound_fires_and_reaches_nothing():
    """A fire that reached nothing must be visible as such, not absent."""
    gs, light, _ = _running(bound={})
    observer = Recorder()
    gs.add_observer(observer)
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert light == []
    assert observer.fired[0].devs == ()
    assert observer.fired[0].steps == 0


def test_a_raising_observer_does_not_stop_the_cues_or_its_peers():
    """Spec section 13 test 3, mirroring the on_release/on_light_cue guards."""
    gs, light, _ = _running()
    raiser, recorder = RaisingRecorder(), Recorder()
    gs.add_observer(raiser)
    gs.add_observer(recorder)
    assert gs.fire_trigger("sweep", fired_by="admin-manual") is None
    assert len(light) == 3
    assert raiser.calls == 1
    assert len(recorder.fired) == 1


def test_an_unknown_trigger_from_a_bit_does_not_break_neighbouring_cues():
    from control.cues import FireTrigger
    gs, light, _ = _running()
    gs._dispatch_cues([(ROOM, 0xB0, 74, 5), FireTrigger("nope"),
                       (ROOM, 0xB0, 74, 6)], 100.0)
    assert [c[3] for c in light] == [5, 6]


def test_a_bit_whose_trigger_table_raises_is_refused_not_crashed():
    class ExplodingBit(_BaseBit):
        @property
        def trigger_table(self):
            raise RuntimeError("boom")

    gs = GameServer({"bit": PlainBit})
    gs.load_bit("bit")
    gs.run()
    gs.bit = ExplodingBit()
    assert gs.fire_trigger("x", fired_by="admin-manual") == "trigger table error"


class _FlipTriggerTableBit(_BaseBit):
    """trigger_table is valid on its first read (the one load_bit validates)
    and structurally invalid on every read after that, modeling a Bit whose
    property builds a fresh object per access -- the same hazard
    RegistrationState's role_table snapshot exists to close for role_table."""

    def __init__(self):
        self._read_once = False

    def verb_handlers(self) -> dict:
        return {}

    @property
    def trigger_table(self) -> TriggerTable:
        if not self._read_once:
            self._read_once = True
            return TriggerTable(triggers={
                "bad": Trigger(
                    name="bad", description="Valid at load, bad later",
                    target=TriggerTarget.ROOM,
                    condition=Condition(name="c", description="d",
                                        source=ConditionSource.ADMIN_MANUAL),
                    script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),)),
            })
        # A 3-tuple cue: legal nowhere, but load-time validation never sees
        # it, because it only ran against the first, valid, access above.
        return TriggerTable(triggers={
            "bad": Trigger(
                name="bad", description="Valid at load, bad later",
                target=TriggerTarget.ROOM,
                condition=Condition(name="c", description="d",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74)),)),
        })


def test_a_trigger_table_that_turns_invalid_after_load_is_refused_not_crashed():
    """trigger_table is a property: load_bit validates whatever it returns on
    that one call, and the validated object is never retained. fire_trigger
    must guard its own later read of the same property rather than trust
    load-time validation to still apply."""
    gs = _server(_FlipTriggerTableBit)
    gs.load_bit("bit")
    reason = gs.fire_trigger("bad", fired_by="admin-manual")
    assert reason is not None
    assert isinstance(reason, str)
