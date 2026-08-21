"""tests/test_metronome_bit_declarations.py"""
from bits.metronome_bit import MetronomeBit
from control.engine import GameServer
from control.rooms import RoomType, room_role_name


def _running_gs(n_players=0):
    gs = GameServer({"MetronomeBit": MetronomeBit})
    gs.load_bit("MetronomeBit")   # load_bit already lands in SETUP
    for i in range(n_players):
        assert gs.join(f"ie{i+1}", "METRO_PLAYER_NODE").granted
    return gs


def test_loads_and_validates():
    # load_bit runs role_config + trigger validation; loading IS the test.
    _running_gs()


def test_demo_only():
    assert MetronomeBit.room_types == {RoomType.DEMO}


def test_third_player_is_denied_by_capacity():
    gs = _running_gs(n_players=2)
    assert not gs.join("ie3", "METRO_PLAYER_NODE").granted


def test_on_join_records_rotation_in_join_order():
    gs = _running_gs(n_players=2)
    assert gs.bit._players == ["ie1", "ie2"]


def test_player_light_manifest_shape():
    table = MetronomeBit().role_table
    player = table.roles["player"]
    instruments = {i["instrument"] for i in
                   player.light_manifest["instruments"]}
    assert instruments == {"aurora", "bloom"}
    assert player.scored and player.capacity == 2


def test_room_declares_rainbow_dark_by_default():
    table = MetronomeBit().role_table
    room = table.roles[room_role_name(RoomType.DEMO)]
    by_name = {i["instrument"]: i for i in room.light_manifest["instruments"]}
    assert by_name["rainbow"]["params"]["level"] == 0.0


def test_trigger_names():
    names = set(MetronomeBit().trigger_table.triggers)
    assert names == {"fireworks_player", "fireworks_room",
                     "fail_player", "fail_room", "finale"}
