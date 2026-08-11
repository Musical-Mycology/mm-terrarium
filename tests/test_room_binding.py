from control.room_binding import RoomBindingRegistry
from control.rooms import RoomType


def make_clock():
    now = [0.0]

    def clock():
        return now[0]

    def advance(seconds):
        now[0] += seconds

    return clock, advance


def test_bind_and_bound_device_round_trip():
    registry = RoomBindingRegistry()
    assert registry.bound_device(RoomType.TEST) is None
    registry.bind(RoomType.TEST, "ie7")
    assert registry.bound_device(RoomType.TEST) == "ie7"


def test_release_clears_binding():
    registry = RoomBindingRegistry()
    registry.bind(RoomType.TEST, "ie7")
    registry.release(RoomType.TEST)
    assert registry.bound_device(RoomType.TEST) is None


def test_bindings_are_independent_per_room_type():
    registry = RoomBindingRegistry()
    registry.bind(RoomType.TEST, "ie7")
    registry.bind(RoomType.DEMO, "array-1")
    assert registry.bound_device(RoomType.TEST) == "ie7"
    assert registry.bound_device(RoomType.DEMO) == "array-1"


def test_arm_opens_a_window_that_expires():
    clock, advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    assert registry.is_armed(RoomType.TEST) is False
    registry.arm(RoomType.TEST, window_seconds=10.0)
    assert registry.is_armed(RoomType.TEST) is True
    advance(10.1)
    assert registry.is_armed(RoomType.TEST) is False


def test_disarm_closes_the_window_immediately():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm(RoomType.TEST, window_seconds=10.0)
    registry.disarm(RoomType.TEST)
    assert registry.is_armed(RoomType.TEST) is False


def test_bind_disarms_the_window():
    clock, _advance = make_clock()
    registry = RoomBindingRegistry(clock=clock)
    registry.arm(RoomType.TEST, window_seconds=10.0)
    registry.bind(RoomType.TEST, "ie7")
    assert registry.is_armed(RoomType.TEST) is False


def test_save_then_load_restores_bindings_into_a_fresh_registry(tmp_path):
    path = str(tmp_path / "room_binding.json")
    original = RoomBindingRegistry()
    original.bind(RoomType.TEST, "ie7")
    original.bind(RoomType.DEMO, "array-1")
    original.save(path)

    restored = RoomBindingRegistry()
    restored.load(path)
    assert restored.bound_device(RoomType.TEST) == "ie7"
    assert restored.bound_device(RoomType.DEMO) == "array-1"


def test_load_missing_file_is_a_noop(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    registry = RoomBindingRegistry()
    registry.load(path)  # must not raise
    assert registry.bound_device(RoomType.TEST) is None


def test_save_does_not_persist_armed_state(tmp_path):
    path = str(tmp_path / "room_binding.json")
    original = RoomBindingRegistry()
    original.arm(RoomType.TEST, window_seconds=10.0)
    original.save(path)

    restored = RoomBindingRegistry()
    restored.load(path)
    assert restored.is_armed(RoomType.TEST) is False
