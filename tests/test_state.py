from control.state import State


def test_all_lifecycle_states_present():
    names = {s.name for s in State}
    assert names == {
        "IDLE", "LOADING", "LOADED", "SETUP",
        "RUNNING", "COMPLETING", "UNLOADING",
    }


def test_states_are_distinct_values():
    assert len({s.value for s in State}) == len(State)
