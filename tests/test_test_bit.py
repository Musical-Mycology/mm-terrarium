from bits.test_bit import TestBit
from control.cues import ROOM, FireTrigger
from control.roles import RoleClass
from control.rooms import room_role_name, RoomType
from control.triggers import validate_trigger_table


def test_role_table_has_one_scored_and_one_jam_role():
    bit = TestBit()
    table = bit.role_table
    assert table.roles["player"].scored is True
    assert table.roles["player"].role_class == RoleClass.SHARED
    assert table.roles["jammer"].scored is False
    assert table.roles["jammer"].role_class == RoleClass.JAM


def test_jammer_keeps_empty_media_defaults():
    # The no-light, no-audio path stays exercised.
    table = TestBit().role_table
    assert table.roles["jammer"].ugen_manifest == {}
    assert table.roles["jammer"].light_manifest == {}


def test_player_declares_a_flsyn_instrument_with_a_drone():
    decl = TestBit().role_table.roles["player"].ugen_manifest["instruments"][0]
    assert decl["instrument"] == "flsyn"
    assert decl["drone"]["key"] > 0 and decl["drone"]["velocity"] > 0


def test_player_binds_the_same_two_controllers_in_light_and_audio():
    # The property this whole slice exists to establish: one control stream,
    # two consumers. If these ever diverge, the demo is two timelines again.
    roles = TestBit().role_table.roles["player"]
    light_sources = {lane["source"]
                     for inst in roles.light_manifest["instruments"]
                     for lane in inst["lanes"]}
    audio_sources = {lane["source"]
                     for inst in roles.ugen_manifest["instruments"]
                     for lane in inst.get("lanes", [])}
    assert light_sources == audio_sources == {"cc:74", "cc:11"}


def test_player_light_declares_level_so_the_breath_is_externally_driven():
    inst = TestBit().role_table.roles["player"].light_manifest["instruments"][0]
    assert "level" in inst["params"]                # opts aurora into external drive
    assert {"source": "cc:11", "dest": "level"} in inst["lanes"]


def test_player_light_still_declares_no_note_lane():
    # PR #9's strobe fix: aurora froze its colour at note-on, so sweeping the hue
    # re-triggered constantly. The audio path adds a drone note-on; light must
    # keep ignoring it.
    inst = TestBit().role_table.roles["player"].light_manifest["instruments"][0]
    assert not [lane for lane in inst["lanes"] if lane["source"] == "note"]


def test_node_map_grants_each_role_from_its_own_node():
    bit = TestBit()
    table = bit.role_table
    assert table.node_map["TEST_PLAYER_NODE"] == ["player"]
    assert table.node_map["TEST_JAM_NODE"] == ["jammer"]


def test_lifecycle_hooks_flip_flags():
    bit = TestBit()
    bit.on_setup_enter()
    bit.on_run_start()
    bit.on_complete()
    bit.on_unload()
    assert bit._setup_entered is True
    assert bit._run_started is True
    assert bit._completed is True
    assert bit._unloaded is True


def test_update_completes_after_run_duration_elapses():
    bit = TestBit(run_duration=1.0)
    bit.on_run_start()
    assert bit.update(0.4) is False
    assert bit.update(0.4) is False
    assert bit.update(0.4) is True  # 1.2s elapsed >= 1.0s


def test_bit_status_defaults_to_empty_dict():
    from control.roles import RoleTable
    from control.bit import Bit

    class MinimalBit(Bit):
        @property
        def role_table(self) -> RoleTable:
            return RoleTable(roles={}, node_map={})

    assert MinimalBit().status() == {}


def test_test_bit_status_reports_elapsed_and_duration():
    from bits.test_bit import TestBit
    bit = TestBit(run_duration=5.0)
    bit.on_run_start()
    bit.update(1.5)
    status = bit.status()
    assert status["run_duration"] == 5.0
    assert status["elapsed"] == 1.5


def test_player_role_declares_v2_light_manifest_and_welcome():
    bit = TestBit()
    table = bit.role_table
    player = table.roles["player"]
    assert player.light_manifest == {
        "instruments": [
            {"instrument": "aurora", "target": "primary",
             "params": {"hue": 0.33, "level": 0.55},
             "lanes": [{"source": "cc:74", "dest": "hue"},
                       {"source": "cc:11", "dest": "level"}]},
        ],
    }
    assert player.welcome == {
        "light": {"instrument": "glow", "params": {"hue": 0.33},
                  "duration": 1.5},
        "audio": {"instrument": "chime", "duration": 1.5},
    }


def test_jammer_role_keeps_empty_light_defaults():
    bit = TestBit()
    table = bit.role_table
    assert table.roles["jammer"].light_manifest == {}
    assert table.roles["jammer"].welcome is None


def test_test_bit_declares_a_version():
    assert TestBit().version == "0.1"


def test_player_declares_surfaces_and_samples():
    from bits.test_bit import TestBit
    player = TestBit().role_table.roles["player"]
    assert player.uses == ["tilt", "tap", "shake", "speaker"]
    assert player.samples == ["click", "chime"]


def test_jammer_declares_only_tilt():
    """The asymmetry is the point: the two nodes light different surfaces."""
    from bits.test_bit import TestBit
    jammer = TestBit().role_table.roles["jammer"]
    assert jammer.uses == ["tilt"]
    assert jammer.samples == []


def test_tap_yields_a_play_cue_and_a_light_cue():
    from bits.test_bit import TestBit
    from control.cues import PlayCue
    cues = TestBit().verb_handlers()["tap"]("ie1", ["ie1", 2.4, 40.0, 1], 0.0)
    assert PlayCue("ie1", "click", "") in cues
    assert any(isinstance(c, tuple) and c[0] == "ie1" for c in cues)


def test_double_tap_plays_chime():
    from bits.test_bit import TestBit
    from control.cues import PlayCue
    cues = TestBit().verb_handlers()["tap"]("ie1", ["ie1", 2.4, 40.0, 2], 0.0)
    assert PlayCue("ie1", "chime", "") in cues


def test_shake_maps_sweep_to_a_light_cue():
    from bits.test_bit import TestBit
    cues = TestBit().verb_handlers()["shake"]("ie1", ["ie1", 2.4, 600.0, 90.0], 0.0)
    assert cues == [("ie1", 0xB0, 74, 127)]


def test_shake_clamps_out_of_range_sweep():
    from bits.test_bit import TestBit
    cues = TestBit().verb_handlers()["shake"]("ie1", ["ie1", 2.4, 600.0, 999.0], 0.0)
    assert cues == [("ie1", 0xB0, 74, 127)]


def test_gesture_handlers_tolerate_short_args():
    """A device must never be able to wedge Control with a truncated frame."""
    from bits.test_bit import TestBit
    bit = TestBit()
    assert bit.verb_handlers()["tap"]("ie1", ["ie1"], 0.0) is not None
    assert bit.verb_handlers()["shake"]("ie1", ["ie1"], 0.0) is not None


def test_test_bit_declares_a_room_test_role():
    bit = TestBit()
    name = room_role_name(RoomType.TEST)
    role = bit.role_table.roles[name]
    assert role.role_class == RoleClass.ROOM
    assert role.light_manifest["instruments"]
    assert role.ugen_manifest["instruments"]


def test_test_bit_room_node_is_registered():
    from control.rooms import ROOM_NODE_IDS
    bit = TestBit()
    node = ROOM_NODE_IDS[RoomType.TEST]
    assert room_role_name(RoomType.TEST) in bit.role_table.node_map[node]


def test_tilt_drives_the_calling_device_and_the_room_at_one_time():
    """The Room role declares cc:74 on BOTH its light_manifest (aurora hue)
    and its ugen_manifest (FluidSynth cutoff), so one tilt moves the Room's
    colour and the Room's drone timbre against a single shared time. This is
    the gesture that makes the timed-cue path load-bearing rather than merely
    present."""
    from control.cues import ROOM
    bit = TestBit()
    cues = bit._on_tilt("ie1", ["ie1", 0.0], at=1000.06)
    assert cues == [("ie1", 0xB0, 74, 64), (ROOM, 0xB0, 74, 64)]


def test_room_drift_is_a_deterministic_triangle():
    """Deterministic in _elapsed, which update(dt) already accumulates, so a
    test can assert the exact value. Triangle rather than sawtooth: a
    sawtooth snaps from 127 back to 0 once a period and aurora glides to its
    target, so the snap reads as a visible lurch."""
    from control.cues import ROOM
    bit = TestBit(run_duration=1000.0)
    bit.on_run_start()

    assert bit.cues(at=0.0) == [(ROOM, 0xB0, 74, 0)]

    bit.update(TestBit.ROOM_DRIFT_PERIOD / 2)          # half a period
    assert bit.cues(at=0.0) == [(ROOM, 0xB0, 74, 127)]

    bit.update(TestBit.ROOM_DRIFT_PERIOD / 2)          # a full period
    assert bit.cues(at=0.0) == [(ROOM, 0xB0, 74, 0)]

    bit.update(TestBit.ROOM_DRIFT_PERIOD * 3 / 4)       # three-quarters through the next period
    assert bit.cues(at=0.0) == [(ROOM, 0xB0, 74, 64)]


def test_room_animates_with_no_device_joined():
    """verb_handlers() can only react to a gesture. Without cues(), the
    Room's aurora reached its declared static hue once and held it for the
    whole run."""
    bit = TestBit(run_duration=1000.0)
    bit.on_run_start()
    bit.update(1.0)
    assert bit.cues(at=0.0), "the Room must animate with nobody joined"


def test_test_bit_declares_both_triggers():
    bit = TestBit()
    names = sorted(bit.trigger_table.triggers)
    assert names == ["flash_device", "play_aurora"]


def test_test_bits_trigger_table_validates_against_its_own_verbs():
    """The gesture-verb condition names `tap`, which TestBit implements. This
    is the fixture behind the declared-but-unimplemented check."""
    bit = TestBit()
    validate_trigger_table(bit.trigger_table, set(bit.verb_handlers()))


def test_a_tap_fires_flash_device_for_the_tapping_device():
    bit = TestBit()
    cues = bit.verb_handlers()["tap"]("ie1", ["ie1", 2.0, 30, 1], 100.0)
    fires = [c for c in cues if isinstance(c, FireTrigger)]
    assert [(f.name, f.dev) for f in fires] == [("flash_device", "ie1")]


def test_full_deflection_tilts_win_a_round_and_fire_play_aurora():
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    for _ in range(TestBit.ROUND_TILTS):
        bit.verb_handlers()["tilt"]("ie1", ["ie1", 90.0], 100.0)
    bit.update(0.01)
    fires = [c for c in bit.cues(100.0) if isinstance(c, FireTrigger)]
    assert [f.name for f in fires] == ["play_aurora"]


def test_a_partial_tilt_does_not_count_toward_the_round():
    """The Bit adjudicates: Control must never fire just because the verb
    arrived."""
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    for _ in range(TestBit.ROUND_TILTS):
        bit.verb_handlers()["tilt"]("ie1", ["ie1", 10.0], 100.0)
    bit.update(0.01)
    assert not [c for c in bit.cues(100.0) if isinstance(c, FireTrigger)]


def test_the_round_fires_once_not_every_tick():
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    for _ in range(TestBit.ROUND_TILTS):
        bit.verb_handlers()["tilt"]("ie1", ["ie1", 90.0], 100.0)
    bit.update(0.01)
    first = [c for c in bit.cues(100.0) if isinstance(c, FireTrigger)]
    bit.update(0.01)
    second = [c for c in bit.cues(100.0) if isinstance(c, FireTrigger)]
    assert len(first) == 1 and second == []


def test_the_ambient_drift_yields_the_lane_while_the_script_plays():
    """The drift and play_aurora's script both drive the Room's cc:74. Without
    this the 44 Hz drift would overwrite every script step within one tick and
    the declared sweep would never be visible."""
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    for _ in range(TestBit.ROUND_TILTS):
        bit.verb_handlers()["tilt"]("ie1", ["ie1", 90.0], 100.0)
    bit.update(0.01)
    bit.cues(100.0)                       # the fire tick
    bit.update(0.5)
    assert bit.cues(100.5) == []          # still inside the script
    bit.update(TestBit.SCRIPT_QUIET_SECONDS)
    assert bit.cues(103.0)                # drift resumes afterwards


def test_the_ambient_drift_is_unchanged_when_no_round_has_been_won():
    bit = TestBit(run_duration=30.0)
    bit.on_run_start()
    bit.update(0.0)
    assert bit.cues(100.0) == [(ROOM, 0xB0, 74, 0)]
