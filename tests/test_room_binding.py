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
