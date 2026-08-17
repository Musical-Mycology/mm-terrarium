"""The Room read model the Console renders. Pure dict builders, no engine
imports, mirroring console/protocol.py."""

from control.room_profile import room_profile
from control.room_view import room_view
from control.rooms import Room, RoomType, room_role


def _role():
    _, role, _ = room_role(
        RoomType.TEST,
        light_manifest={"instruments": [
            {"instrument": "aurora", "target": "primary",
             "params": {"hue": 0.6, "level": 0.55},
             "lanes": [{"source": "cc:74", "dest": "hue"}]}]},
        ugen_manifest={"instruments": [
            {"instrument": "flsyn", "program": 89,
             "drone": {"key": 50, "velocity": 80},
             "lanes": [{"source": "cc:74", "dest": "cc:74"}]}]},
    )
    return role


def _view():
    return room_view(Room(room_type=RoomType.TEST, bound_dev="sim-room"),
                     room_profile(RoomType.TEST), _role(), {74: 93})


def test_no_room_configured_yields_none():
    assert room_view(None, None, None, {}) is None


def test_header_fields():
    view = _view()
    assert view["room_type"] == "TEST"
    assert view["bound_dev"] == "sim-room"


def test_capability_carries_the_zones():
    view = _view()
    assert view["capability"]["surface_id"] == "room_test"
    assert view["capability"]["pixel_count"] == 60
    assert view["capability"]["color_order"] == "GRB"
    assert view["capability"]["zones"] == [
        {"name": "left", "start": 0, "count": 20},
        {"name": "center", "start": 20, "count": 20},
        {"name": "right", "start": 40, "count": 20},
    ]


def test_primary_is_absent_from_the_serialized_zones():
    """It spans the whole surface, so drawing it would cover every real zone.
    The renderer gets it from harness/room_surface.py instead."""
    assert "primary" not in [z["name"] for z in _view()["capability"]["zones"]]


def test_light_and_audio_appear_in_one_list_discriminated_by_kind():
    """The point of the panel: cc:74 is visibly the same controller driving
    aurora's hue and FluidSynth's cutoff."""
    instruments = _view()["instruments"]
    assert [i["kind"] for i in instruments] == ["light", "audio"]
    assert instruments[0]["instrument"] == "aurora"
    assert instruments[0]["target"] == "primary"
    assert instruments[1]["instrument"] == "flsyn"


def test_lanes_carry_across_for_both_kinds():
    instruments = _view()["instruments"]
    assert instruments[0]["lanes"] == [{"source": "cc:74", "dest": "hue"}]
    assert instruments[1]["lanes"] == [{"source": "cc:74", "dest": "cc:74"}]


def test_audio_extras_are_preserved():
    audio = _view()["instruments"][1]
    assert audio["program"] == 89
    assert audio["drone"] == {"key": 50, "velocity": 80}


def test_controllers_are_carried_through():
    assert _view()["controllers"] == {74: 93}


def test_no_bit_loaded_yields_capability_with_no_instruments():
    view = room_view(Room(room_type=RoomType.TEST), room_profile(RoomType.TEST),
                     None, {})
    assert view["instruments"] == []
    assert view["capability"]["pixel_count"] == 60
    assert view["bound_dev"] is None


def test_empty_manifests_yield_no_instruments():
    _, role, _ = room_role(RoomType.TEST)
    view = room_view(Room(room_type=RoomType.TEST), room_profile(RoomType.TEST),
                     role, {})
    assert view["instruments"] == []


def test_the_view_is_json_serializable():
    import json
    json.dumps(_view())


def test_the_node_id_never_appears_anywhere_in_the_view():
    """Section 3 of the design spec: the Registration Node id stays hidden."""
    import json
    from control.rooms import ROOM_NODE_IDS
    blob = json.dumps(_view())
    assert ROOM_NODE_IDS[RoomType.TEST] not in blob


def test_the_room_role_name_never_appears_in_the_view():
    """Corrected during Task 6: as written, this assertion is unsatisfiable
    for RoomType.TEST. room_role_name(TEST) == "room_test"
    (tests/test_rooms.py, landed pre-Task-6), and RoomProfile(TEST).surface_id
    == "room_test" too (tests/test_room_profile.py, Task 1) -- two
    independently authored, already-locked-in facts that happen to collide
    for this one RoomType. capability.surface_id is meant to be visible (see
    test_capability_carries_the_zones above and design spec section 3's
    right-hand column), so its presence is not the role name leaking. The
    check is scoped past that one legitimate field to assert the real fact:
    the role name has no other route into the view."""
    import json
    from control.rooms import room_role_name
    view = _view()
    view["capability"].pop("surface_id")
    assert room_role_name(RoomType.TEST) not in json.dumps(view)
