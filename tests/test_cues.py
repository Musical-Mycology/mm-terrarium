import pytest

from control.cues import ALL, FIXTURE_PREFIX, ROOM, TARGET, fixture_dev, fixture_name


def test_fixture_dev_round_trips_through_fixture_name():
    assert fixture_name(fixture_dev("accent")) == "accent"


def test_fixture_dev_spells_the_prefix_once():
    assert fixture_dev("main") == FIXTURE_PREFIX + "main" == "@fixture:main"


@pytest.mark.parametrize("bad", ["", "with space", "a.b", "x/y", "@fixture:z"])
def test_fixture_dev_refuses_malformed_names(bad):
    with pytest.raises(ValueError):
        fixture_dev(bad)


@pytest.mark.parametrize("dev", [ROOM, TARGET, ALL, "ie1", "@fixture:", "@fixture:a.b",
                                 None, 42, "@fixtures:main"])
def test_fixture_name_is_none_for_non_fixture_devs(dev):
    assert fixture_name(dev) is None
