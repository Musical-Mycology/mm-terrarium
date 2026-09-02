import pytest

from bits.metronome.metronome_bit import MetronomeBit
from bits.test.test_bit import TestBit
from control.engine import BitLoadError, GameServer
from control.cues import ROOM
from control.functions import FIRED_BY_ADMIN_MANUAL
from control.instrument import TUNESHROOM, Instrument
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.rooms import Room
from control.state import State

ARR = Instrument(name="arr", capabilities=frozenset({"light.surface",
                                                     "audio.flsyn"}),
                 accepted_cues=("midi", "play", "solid", "mute"))
PROFILE = RoomProfile(surface_id="r", fixtures=(
    RoomFixture(name="main", color_order="GRB",
                blocks=(RoomBlock("main", 0, 10),),
                zones=(RoomZone("all", 0, 10),), instrument=ARR),))


def _gs_with_room():
    gs = GameServer({})
    gs.room = Room(name="R", profile=PROFILE, node_id="N")
    gs.room.bound["main"] = "fix-dev"
    return gs


TWO_FIXTURE_PROFILE = RoomProfile(surface_id="r2", fixtures=(
    RoomFixture(name="main", color_order="GRB",
                blocks=(RoomBlock("main", 0, 10),),
                zones=(RoomZone("all", 0, 10),), instrument=ARR),
    RoomFixture(name="accent", color_order="GRB",
                blocks=(RoomBlock("accent", 0, 10),),
                zones=(RoomZone("all", 0, 10),), instrument=ARR),
))


@pytest.fixture
def two_fixture_gs():
    """A bound two-fixture Room, no Bit loaded -- the minimal rig for
    proving an explicit fixture fire reaches only that fixture (each has
    its own light session now, so there is no collapse to speak of), and
    that @all resolves per real dev."""
    gs = GameServer({})
    gs.room = Room(name="R2", profile=TWO_FIXTURE_PROFILE, node_id="N2")
    gs.room.bound["main"] = "main-dev"
    gs.room.bound["accent"] = "accent-dev"
    return gs, "main-dev", "accent-dev"


def test_builtin_fires_with_no_bit_in_idle():
    gs = _gs_with_room()
    seen = []
    gs.on_solid_cue = lambda dev, rgb, level, duration, when: seen.append(dev)
    assert gs.fire_function("flash", fired_by=FIRED_BY_ADMIN_MANUAL,
                            dev=ROOM) is None
    assert seen == ["fix-dev"]


def test_ping_on_flsyn_room_emits_the_note_pair():
    gs = _gs_with_room()
    cues = []
    gs.on_light_cue = lambda dev, status, data1, data2, when: cues.append(
        (dev, status, data1, data2))
    assert gs.fire_function("ping", fired_by=FIRED_BY_ADMIN_MANUAL,
                            dev=ROOM) is None
    assert cues == [("fix-dev", 0x90, 57, 100), ("fix-dev", 0x80, 57, 0)]


def test_unknown_name_is_a_zero_step_fire_not_an_error():
    gs = _gs_with_room()
    fired = []
    class Obs:
        def on_function_fired(self, record): fired.append(record)
    gs.add_observer(Obs())
    assert gs.fire_function("nope", fired_by=FIRED_BY_ADMIN_MANUAL,
                            dev=ROOM) is None
    assert fired[-1].steps == 0 and fired[-1].devs == ()


def test_surface_fire_without_dev_still_refused():
    gs = _gs_with_room()
    assert gs.fire_function("flash",
                            fired_by=FIRED_BY_ADMIN_MANUAL) is not None


def test_load_warnings_report_target_aware_gaps():
    # A Bit name-fire with ROOM target whose name no room instrument
    # declares warns; the same name on TUNESHROOM does not suppress it.
    from control.bit import Bit
    from control.functions import (Condition, ConditionSource, Function,
                                   FunctionKind, FunctionTable, FunctionTarget)
    from control.roles import RoleTable
    class NameFireBit(Bit):
        version = "1"
        @property
        def role_table(self): return RoleTable(roles={}, node_map={})
        @property
        def function_table(self):
            return FunctionTable(functions={"aurora_room": Function(
                name="aurora_room", description="d",
                kind=FunctionKind.SCRIPTED, script=(),
                target=FunctionTarget.ROOM,
                condition=Condition(name="c", description="d",
                                    source=ConditionSource.ADMIN_MANUAL))})
    gs = _gs_with_room()
    gs.bit_registry["NF"] = NameFireBit
    gs.load_bit("NF")
    assert any("aurora_room" in w and "arr" in w for w in gs.load_warnings)


def test_no_room_no_bit_fires_carried_default_instrument():
    # No Bit, no Room -- firing at a connected device that carries the
    # DEFAULTSHROOM default (2026-08-31 carried-instrument-wire: light +
    # gesture only, no audio.samples) resolves the builtin "flash" entirely
    # off the device pool fallback, emitting the SolidCue DEFAULTSHROOM's
    # flash builtin produces (no chime PlayCue -- DEFAULTSHROOM has no
    # audio.samples capability).
    gs = GameServer({})
    gs.hello("dev1", "some-device", "1")
    plays = []
    solids = []
    gs.on_play_cue = lambda dev, name, params: plays.append((dev, name))
    gs.on_solid_cue = lambda dev, rgb, level, duration, when: solids.append(dev)
    assert gs.fire_function("flash", fired_by=FIRED_BY_ADMIN_MANUAL,
                            dev="dev1") is None
    assert plays == []
    assert solids == ["dev1"]


def test_a_bit_declaring_the_reserved_stop_name_is_refused_at_load():
    # A Bit declaring "stop" can no longer shadow the reserved built-in of
    # the same name -- validate_function_table now refuses reserved names
    # for both owners (control/functions.py), so the load fails with a
    # located ValueError instead of installing a rung-1 override.
    from control.bit import Bit
    from control.cues import TARGET, MuteCue
    from control.functions import (Condition, ConditionSource, Function,
                                   FunctionKind, FunctionTable, FunctionTarget,
                                   ScriptStep)
    from control.roles import RoleTable

    class StopShadowBit(Bit):
        version = "1"

        @property
        def role_table(self): return RoleTable(roles={}, node_map={})

        @property
        def function_table(self):
            return FunctionTable(functions={"stop": Function(
                name="stop",
                description="Latch this surface dark and silent until a "
                            "Play un-mutes it.",
                kind=FunctionKind.SCRIPTED,
                target=FunctionTarget.SURFACE,
                condition=Condition(name="operator-stop",
                                    description="Fired by the operator",
                                    source=ConditionSource.ADMIN_MANUAL),
                script=(ScriptStep(0.0, MuteCue(TARGET)),))})

    gs = GameServer({})
    gs.bit_registry["StopShadow"] = StopShadowBit
    with pytest.raises(BitLoadError, match="reserved built-in"):
        gs.load_bit("StopShadow")
    assert gs.state == State.IDLE


def test_testbit_undeclared_flash_falls_through_to_builtin():
    # "flash" is not declared by TestBit at all, so even with TestBit
    # loaded and RUNNING the undeclared name falls through the ladder to
    # the device's carried default instrument's builtin -- DEFAULTSHROOM's
    # flash (SolidCue white, no chime PlayCue -- 2026-08-31 carried-
    # instrument-wire), same as the no-Bit case.
    gs = GameServer({"TestBit": TestBit})
    gs.load_bit("TestBit")
    gs.run()
    gs.hello("dev1", "some-device", "1")
    plays = []
    solids = []
    gs.on_play_cue = lambda dev, name, params: plays.append((dev, name))
    gs.on_solid_cue = lambda dev, rgb, level, duration, when: solids.append(dev)
    fired = []
    class Obs:
        def on_function_fired(self, record): fired.append(record)
    gs.add_observer(Obs())
    assert gs.fire_function("flash", fired_by=FIRED_BY_ADMIN_MANUAL,
                            dev="dev1") is None
    assert plays == []
    assert solids == ["dev1"]
    assert fired[-1].condition == "builtin"


def test_unmigrated_bits_load_with_zero_warnings():
    # Both TestBit and MetronomeBit keep their non-empty scripted
    # declarations (deferred migration, 2026-08-31 redirect); a
    # non-empty-script declaration is rung 1 and never triggers the
    # empty-script name-fire "no script on instrument X" warning path.
    gs = GameServer({"TestBit": TestBit})
    gs.load_bit("TestBit")
    assert gs.load_warnings == ()

    gs2 = GameServer({"MetronomeBit": MetronomeBit})
    gs2.load_bit("MetronomeBit")
    assert gs2.load_warnings == ()


def test_explicit_fixture_fire_is_not_collapsed(two_fixture_gs):
    """Each fixture has its own light session, so there is no canonical-dev
    collapse to prove past anymore -- this assertion now holds trivially,
    a fire at one fixture simply never touches another."""
    gs, main_dev, accent_dev = two_fixture_gs
    gs.fire_function("stop", fired_by="admin-manual", dev=accent_dev)
    assert accent_dev in gs.muted
    assert main_dev not in gs.muted


def test_all_fire_reaches_every_fixture(two_fixture_gs):
    gs, main_dev, accent_dev = two_fixture_gs
    gs.fire_function("stop", fired_by="admin-manual", dev="@all")
    assert {main_dev, accent_dev} <= gs.muted
