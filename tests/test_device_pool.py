from control.device_pool import DevicePool


def test_hello_registers_a_device():
    pool = DevicePool()
    info = pool.hello("ie3", "Testshroom 3", "1.0")
    assert pool.known("ie3") is True
    assert pool.get("ie3") is info
    assert info.name == "Testshroom 3"
    assert len(pool) == 1


def test_unknown_device_is_not_known():
    pool = DevicePool()
    assert pool.known("ie9") is False
    assert pool.get("ie9") is None


def test_repeated_hello_from_same_device_updates_in_place():
    pool = DevicePool()
    pool.hello("ie3", "Testshroom 3", "1.0")
    pool.hello("ie3", "Testshroom 3", "1.1")
    assert len(pool) == 1
    assert pool.get("ie3").protoversion == "1.1"


def test_all_returns_every_known_device():
    from control.device_pool import DevicePool
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1")
    pool.hello("ie2", "Shroom Two", "1")
    devs = pool.all()
    assert [d.dev for d in devs] == ["ie1", "ie2"]
    # returns a fresh list, not the internal dict's view
    devs.clear()
    assert len(pool) == 2


def test_touch_updates_last_seen_for_a_known_device():
    pool = DevicePool()
    pool.hello("ie3", "Testshroom 3", "1.0", now=10.0)
    pool.touch("ie3", now=20.0)
    assert pool.get("ie3").last_seen == 20.0


def test_touch_is_a_no_op_for_an_unknown_device():
    pool = DevicePool()
    pool.touch("ie9", now=20.0)   # must not raise
    assert pool.known("ie9") is False


def test_hello_sets_last_seen():
    pool = DevicePool()
    pool.hello("ie3", "Testshroom 3", "1.0", now=5.0)
    assert pool.get("ie3").last_seen == 5.0


def test_stale_returns_devices_past_the_timeout():
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1", now=0.0)
    pool.hello("ie2", "Shroom Two", "1", now=9.0)
    assert pool.stale(now=10.0, timeout=5.0) == ["ie1"]


def test_stale_is_a_pure_query():
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1", now=0.0)
    pool.stale(now=10.0, timeout=5.0)
    assert pool.known("ie1") is True   # stale() did not remove it


def test_remove_drops_the_entry_outright():
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1")
    pool.remove("ie1")
    assert pool.known("ie1") is False
    assert len(pool) == 0


def test_remove_is_a_no_op_for_an_unknown_device():
    pool = DevicePool()
    pool.remove("ie9")   # must not raise
    assert len(pool) == 0


def test_a_removed_device_can_say_hello_again_as_if_new():
    pool = DevicePool()
    pool.hello("ie1", "Shroom One", "1", now=0.0)
    pool.remove("ie1")
    pool.hello("ie1", "Shroom One (reconnected)", "1", now=100.0)
    assert pool.known("ie1") is True
    assert pool.get("ie1").last_seen == 100.0
