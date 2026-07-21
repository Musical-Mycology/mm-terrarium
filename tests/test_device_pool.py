from control.device_pool import DevicePool


def test_hello_registers_a_device():
    pool = DevicePool()
    info = pool.hello("ie3", "Tuneshroom 3", "1.0")
    assert pool.known("ie3") is True
    assert pool.get("ie3") is info
    assert info.name == "Tuneshroom 3"
    assert len(pool) == 1


def test_unknown_device_is_not_known():
    pool = DevicePool()
    assert pool.known("ie9") is False
    assert pool.get("ie9") is None


def test_repeated_hello_from_same_device_updates_in_place():
    pool = DevicePool()
    pool.hello("ie3", "Tuneshroom 3", "1.0")
    pool.hello("ie3", "Tuneshroom 3", "1.1")
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
