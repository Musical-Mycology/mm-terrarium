from control.engine import GameServer
from control.cues import ROOM
from control.functions import FIRED_BY_ADMIN_MANUAL
from control.instrument import TUNESHROOM, Instrument
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.rooms import Room

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
    # TUNESHROOM default (light+samples) resolves the builtin "flash"
    # entirely off the device pool fallback, emitting the PlayCue+SolidCue
    # pair TUNESHROOM's flash builtin produces.
    gs = GameServer({})
    gs.hello("dev1", "some-device", "1")
    plays = []
    solids = []
    gs.on_play_cue = lambda dev, name, params: plays.append((dev, name))
    gs.on_solid_cue = lambda dev, rgb, level, duration, when: solids.append(dev)
    assert gs.fire_function("flash", fired_by=FIRED_BY_ADMIN_MANUAL,
                            dev="dev1") is None
    assert plays == [("dev1", "chime")]
    assert solids == ["dev1"]
