import json

from control.room_binding import RoomBindingRegistry


def make_clock():
    now = [0.0]

    def clock():
        return now[0]

    def advance(seconds):
        now[0] += seconds

    return clock, advance


def test_bind_and_bound_device_round_trip():
    registry = RoomBindingRegistry()
    assert registry.bound_device("TEST", "main") is None
    registry.bind("TEST", "main", "sim-room-main")
    assert registry.bound_device("TEST", "main") == "sim-room-main"


def test_fixtures_are_independent_within_one_room_type():
    registry = RoomBindingRegistry()
    registry.bind("TEST", "main", "sim-room-main")
    registry.bind("TEST", "accent", "sim-room-accent")
    assert registry.bound_device("TEST", "main") == "sim-room-main"
    assert registry.bound_device("TEST", "accent") == "sim-room-accent"


def test_release_one_fixture_leaves_the_other_bound():
    registry = RoomBindingRegistry()
    registry.bind("TEST", "main", "sim-room-main")
    registry.bind("TEST", "accent", "sim-room-accent")
    registry.release("TEST", "main")
    assert registry.bound_device("TEST", "main") is None
    assert registry.bound_device("TEST", "accent") == "sim-room-accent"


def test_release_with_no_fixture_clears_every_fixture():
    registry = RoomBindingRegistry()
    registry.bind("TEST", "main", "sim-room-main")
    registry.bind("TEST", "accent", "sim-room-accent")
    registry.release("TEST")
    assert registry.bound_device("TEST", "main") is None
    assert registry.bound_device("TEST", "accent") is None


def test_bindings_are_independent_per_room_type():
    registry = RoomBindingRegistry()
    registry.bind("TEST", "main", "sim-room-main")
    registry.bind("DEMO", "array", "array-1")
    assert registry.bound_device("TEST", "main") == "sim-room-main"
    assert registry.bound_device("DEMO", "array") == "array-1"


def test_arm_opens_a_window_that_expires():
    clock, advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    assert registry.is_armed("TEST") is False
    registry.arm("TEST", "main", window_seconds=10.0)
    assert registry.is_armed("TEST") is True
    assert registry.armed_fixture("TEST") == "main"
    advance(10.1)
    assert registry.is_armed("TEST") is False
    assert registry.armed_fixture("TEST") is None


def test_disarm_closes_the_window_immediately():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm("TEST", "main", window_seconds=10.0)
    registry.disarm("TEST")
    assert registry.is_armed("TEST") is False
    assert registry.armed_fixture("TEST") is None


def test_arming_a_second_fixture_replaces_the_first():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm("TEST", "main", window_seconds=10.0)
    registry.arm("TEST", "accent", window_seconds=10.0)
    assert registry.armed_fixture("TEST") == "accent"


def test_bind_disarms_only_when_the_bound_fixture_was_the_armed_one():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm("TEST", "main", window_seconds=10.0)
    registry.bind("TEST", "main", "sim-room-main")
    assert registry.is_armed("TEST") is False


def test_bind_of_an_unarmed_fixture_does_not_disturb_a_different_armed_window():
    """control/boot.py's fast path calls bind() directly with nothing armed
    at all -- must not raise or disarm something it never armed."""
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm("TEST", "accent", window_seconds=10.0)
    registry.bind("TEST", "main", "sim-room-main")   # not the armed fixture
    assert registry.is_armed("TEST") is True
    assert registry.armed_fixture("TEST") == "accent"


def test_save_then_load_restores_bindings_into_a_fresh_registry(tmp_path):
    path = str(tmp_path / "room_binding.json")
    original = RoomBindingRegistry()
    original.bind("TEST", "main", "sim-room-main")
    original.bind("TEST", "accent", "sim-room-accent")
    original.bind("DEMO", "array", "array-1")
    original.save(path)

    restored = RoomBindingRegistry()
    restored.load(path)
    assert restored.bound_device("TEST", "main") == "sim-room-main"
    assert restored.bound_device("TEST", "accent") == "sim-room-accent"
    assert restored.bound_device("DEMO", "array") == "array-1"


def test_load_missing_file_is_a_noop(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    registry = RoomBindingRegistry()
    registry.load(path)  # must not raise
    assert registry.bound_device("TEST", "main") is None


def test_save_does_not_persist_armed_state(tmp_path):
    path = str(tmp_path / "room_binding.json")
    original = RoomBindingRegistry()
    original.arm("TEST", "main", window_seconds=10.0)
    original.save(path)

    restored = RoomBindingRegistry()
    restored.load(path)
    assert restored.is_armed("TEST") is False


def test_load_ignores_an_old_flat_format_file(tmp_path, caplog):
    """Pre-N-fixture files bound one dev id per room_type as a plain string.
    That is dead data: nothing calls load() from boot() yet (see 'Not yet
    built' in the deep-dive), and guessing which fixture a bare string names
    would risk binding a stale dev to the wrong fixture."""
    path = str(tmp_path / "old_format.json")
    with open(path, "w") as f:
        json.dump({"TEST": "sim-room"}, f)   # old shape: room_type -> dev string

    registry = RoomBindingRegistry()
    registry.load(path)   # must not raise
    assert registry.bound_device("TEST", "main") is None


def test_binding_file_format_is_unchanged_by_string_keys(tmp_path):
    from control.room_binding import RoomBindingRegistry
    reg = RoomBindingRegistry()
    reg.bind("TEST", "main", "d1")
    path = str(tmp_path / "b.json")
    reg.save(path)
    import json
    assert json.load(open(path)) == {"TEST": {"main": "d1"}}
    fresh = RoomBindingRegistry()
    fresh.load(path)
    assert fresh.bound_device("TEST", "main") == "d1"
