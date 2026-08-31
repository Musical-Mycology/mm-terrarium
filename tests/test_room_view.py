"""The Room read model the Console renders. Pure dict builders, no engine
imports, mirroring console/protocol.py."""

from control.cues import ROOM
from control.functions import Function, FunctionKind, GeneratorSpec
from control.instrument import Instrument
from control.room_profile import RoomBlock, RoomFixture, RoomProfile, RoomZone
from control.triggers import EventTrigger
from tests.instrument_fixtures import GENERIC_SURFACE
from control.room_view import room_view
from control.rooms import Room, room_role

TEST_PROFILE = RoomProfile(surface_id="room_test", fixtures=(
    RoomFixture(name="main", color_order="GRB",
               blocks=(RoomBlock("main", 0, 60),),
               zones=(RoomZone("left", 0, 20),
                     RoomZone("center", 20, 20),
                     RoomZone("right", 40, 20)), instrument=GENERIC_SURFACE),
    RoomFixture(name="accent", color_order="GRB",
               blocks=(RoomBlock("accent", 0, 30),),
               zones=(RoomZone("low", 0, 15),
                     RoomZone("high", 15, 15)), instrument=GENERIC_SURFACE),
))


def make_room(name="TEST", **kw):
    return Room(name=name, profile=TEST_PROFILE, node_id="ROOM_TEST_NODE", **kw)


def _role():
    room = make_room()
    _, role, _ = room_role(
        room,
        light_manifest={"instruments": [
            {"instrument": "rainbow", "target": "primary",
             "params": {"hue": 0.6, "level": 0.55},
             "lanes": [{"source": "cc:74", "dest": "hue"}]}]},
        ugen_manifest={"instruments": [
            {"instrument": "flsyn", "program": 89,
             "drone": {"key": 50, "velocity": 80},
             "lanes": [{"source": "cc:74", "dest": "cc:74"}]}]},
    )
    return role


def _room(bound=None):
    room = make_room()
    # `or` would treat an explicitly-passed {} the same as "no argument",
    # since both are falsy -- and _view(bound={}) below needs a genuinely
    # empty dict to reach the "no fixture bound" case.
    room.bound = {"main": "sim-room-main"} if bound is None else bound
    return room


def _view(bound=None):
    return room_view(_room(bound), TEST_PROFILE, _role(), {74: 93})


def test_no_room_configured_yields_none():
    assert room_view(None, None, None, {}) is None


def test_header_fields():
    view = _view()
    assert view["room_type"] == "TEST"


def test_fixtures_list_carries_name_dev_and_slice():
    fixtures = _view()["fixtures"]
    assert [f["name"] for f in fixtures] == ["main", "accent"]
    assert fixtures[0]["dev"] == "sim-room-main"
    assert fixtures[0]["pixel_count"] == 60
    assert fixtures[0]["channel_start"] == 0
    assert fixtures[0]["channel_count"] == 180
    assert fixtures[1]["dev"] is None    # accent not bound in this fixture's default
    assert fixtures[1]["pixel_count"] == 30
    assert fixtures[1]["channel_start"] == 180
    assert fixtures[1]["channel_count"] == 90


def test_fixtures_zones_are_scoped_to_their_own_fixture():
    fixtures = _view()["fixtures"]
    assert [z["name"] for z in fixtures[0]["zones"]] == [
        "main.left", "main.center", "main.right"]
    assert [z["name"] for z in fixtures[1]["zones"]] == [
        "accent.low", "accent.high"]
    # Fixture-LOCAL offsets, not the global concatenated-surface offsets
    # profile.zones carries -- accent starts at channel offset 60 in the
    # concatenated surface, but its own zones must read from 0.
    assert [(z["start"], z["count"]) for z in fixtures[0]["zones"]] == [
        (0, 20), (20, 20), (40, 20)]
    assert [(z["start"], z["count"]) for z in fixtures[1]["zones"]] == [
        (0, 15), (15, 15)]


def test_both_fixtures_bound_report_their_own_dev():
    view = _view(bound={"main": "sim-room-main", "accent": "sim-room-accent"})
    fixtures = view["fixtures"]
    assert fixtures[0]["dev"] == "sim-room-main"
    assert fixtures[1]["dev"] == "sim-room-accent"


def test_fixtures_carry_their_instrument():
    fixtures = _view()["fixtures"]
    for fixture in fixtures:
        assert fixture["instrument"] == {
            "name": "generic_surface",
            "capabilities": ["audio.flsyn", "light.surface"],
            "functions": [],
            "accepted_cues": ["midi", "play", "solid", "mute"],
            "event_triggers": [],
        }


def test_fixture_instrument_renders_declared_event_triggers():
    sensing = Instrument(
        name="sensing_surface",
        capabilities=frozenset({"light.surface"}),
        accepted_cues=("midi",),
        event_triggers=(
            EventTrigger(name="tap", description="a sharp accelerometer spike",
                        thresholds={"z_delta": 2.5}),
        ),
    )
    profile = RoomProfile(surface_id="room_sense", fixtures=(
        RoomFixture(name="main", color_order="GRB",
                   blocks=(RoomBlock("main", 0, 10),),
                   zones=(), instrument=sensing),))
    room = Room(name="SENSE", profile=profile, node_id="ROOM_SENSE_NODE")
    room.bound = {"main": "sim-sense-main"}

    view = room_view(room, profile, None, {})

    assert view["fixtures"][0]["instrument"]["event_triggers"] == [
        {"name": "tap", "thresholds": {"z_delta": 2.5}}]


def test_fixture_instrument_renders_declared_generator_functions():
    animated = Instrument(
        name="animated_surface",
        capabilities=frozenset({"light.surface"}),
        accepted_cues=("midi",),
        functions=(Function(
            name="glow", description="ambient breathing glow",
            kind=FunctionKind.GENERATOR,
            generator=GeneratorSpec(dev=ROOM, status=0xB0, data1=74,
                                    waveform="triangle", period=12.0,
                                    lo=0, hi=127)),))
    profile = RoomProfile(surface_id="room_anim", fixtures=(
        RoomFixture(name="main", color_order="GRB",
                   blocks=(RoomBlock("main", 0, 10),),
                   zones=(), instrument=animated),))
    room = Room(name="ANIM", profile=profile, node_id="ROOM_ANIM_NODE")
    room.bound = {"main": "sim-anim-main"}

    view = room_view(room, profile, None, {})

    assert view["fixtures"][0]["instrument"]["functions"] == [
        {"name": "glow", "kind": "generator", "lane": "cc:74", "period": 12.0}]


def test_fixture_instrument_renders_declared_scripted_functions():
    """The trigger-instrument redesign lets an instrument declare SCRIPTED
    functions too (e.g. terrarium.toml's dev_strip play_aurora/win/...),
    not only GENERATOR ones -- _function_view must render them rather than
    reach for a `.generator` that is None."""
    from control.cues import TARGET
    from control.functions import ScriptStep

    scripted = Instrument(
        name="scripted_surface",
        capabilities=frozenset({"light.surface"}),
        accepted_cues=("midi",),
        functions=(Function(
            name="play_aurora", description="Hue bloom sweep",
            kind=FunctionKind.SCRIPTED,
            script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),)),))
    profile = RoomProfile(surface_id="room_scripted", fixtures=(
        RoomFixture(name="main", color_order="GRB",
                   blocks=(RoomBlock("main", 0, 10),),
                   zones=(), instrument=scripted),))
    room = Room(name="SCRIPTED", profile=profile, node_id="ROOM_SCRIPTED_NODE")
    room.bound = {"main": "sim-scripted-main"}

    view = room_view(room, profile, None, {})

    assert view["fixtures"][0]["instrument"]["functions"] == [
        {"name": "play_aurora", "kind": "scripted"}]


def test_capability_carries_the_whole_concatenated_surface():
    view = _view()
    assert view["capability"]["surface_id"] == "room_test"
    assert view["capability"]["pixel_count"] == 90
    assert view["capability"]["color_order"] == "GRB"
    assert [z["name"] for z in view["capability"]["zones"]] == [
        "main.left", "main.center", "main.right", "accent.low", "accent.high"]


def test_primary_is_absent_from_the_serialized_zones():
    assert "primary" not in [z["name"] for z in _view()["capability"]["zones"]]


def test_light_and_audio_appear_in_one_list_discriminated_by_kind():
    instruments = _view()["instruments"]
    assert [i["kind"] for i in instruments] == ["light", "audio"]
    assert instruments[0]["instrument"] == "rainbow"
    assert instruments[0]["target"] == "primary"
    assert instruments[1]["instrument"] == "flsyn"


def test_instrument_entries_carry_the_owning_instrument_name():
    """The room's first fixture's Instrument name -- see room_view's
    comment on the ambiguity-breaking simplification."""
    instruments = _view()["instruments"]
    assert [i["instrument_name"] for i in instruments] == \
        ["generic_surface", "generic_surface"]


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
    view = room_view(_room(bound={}), TEST_PROFILE, None, {})
    assert view["instruments"] == []
    assert view["capability"]["pixel_count"] == 90
    assert all(f["dev"] is None for f in view["fixtures"])


def test_empty_manifests_yield_no_instruments():
    _, role, _ = room_role(make_room())
    view = room_view(_room(bound={}), TEST_PROFILE, role, {})
    assert view["instruments"] == []


def test_the_view_is_json_serializable():
    import json
    json.dumps(_view())


def test_the_node_id_never_appears_anywhere_in_the_view():
    """Section 3 of the room-panel design spec: the Registration Node id
    stays hidden."""
    import json
    blob = json.dumps(_view())
    assert "ROOM_TEST_NODE" not in blob


def test_bound_fixture_with_reported_canvas_gets_url():
    view = room_view(_room(), TEST_PROFILE, _role(), {},
                      canvas_urls={"sim-room-main": "http://h:9/"})
    by_name = {f["name"]: f for f in view["fixtures"]}
    assert by_name["main"]["url"] == "http://h:9/"


def test_unbound_or_unreported_fixture_url_is_none():
    view = room_view(_room(), TEST_PROFILE, _role(), {},
                      canvas_urls={})
    assert all(f["url"] is None for f in view["fixtures"])


def test_omitting_canvas_urls_still_works():
    view = room_view(_room(), TEST_PROFILE, _role(), {})
    assert all(f["url"] is None for f in view["fixtures"])


def test_the_room_role_name_never_appears_in_the_view():
    """room_role_name(TEST) == "room_test", and RoomProfile(TEST).surface_id
    == "room_test" too -- two independently authored, already-locked-in facts
    that happen to collide for this one RoomType. capability.surface_id is
    meant to be visible, so its presence is not the role name leaking. The
    check is scoped past that one legitimate field to assert the real fact:
    the role name has no other route into the view."""
    import json
    from control.rooms import room_role_name
    view = _view()
    view["capability"].pop("surface_id")
    assert room_role_name("TEST") not in json.dumps(view)
