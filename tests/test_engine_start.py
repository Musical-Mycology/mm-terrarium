"""GameServer.request_start: the single start authority (spec section 2)."""
from dataclasses import replace

from bits.test.test_bit import TestBit
from control.bit_registry import BitRegistry
from control.engine import GameServer
from control.lobby import (FEEDBACK_ACCEPT, FEEDBACK_MINIMUM, FEEDBACK_NONE,
                           FEEDBACK_REFUSED, TERRARIUM_ADMIN)
from control.state import State
from pathlib import Path


class _Observer:
    def __init__(self):
        self.starts = []
        self.lobby = []

    def on_start_requested(self, record):
        self.starts.append(record)

    def on_lobby_event(self, event, dev):
        self.lobby.append((event, dev))


def _admin_config():
    """TestBit's manifest, forced to an admin start with key 'k' and a
    minimum of 1 scored player."""
    registry = BitRegistry.scan([Path("bits")])
    cfg = registry.resolve_config("TestBit")
    return replace(cfg, start=replace(cfg.start, when="admin", key="k",
                                      min_scored=1))


def _loaded(config=None, admin_devices=()):
    gs = GameServer({"TestBit": TestBit}, admin_devices=admin_devices)
    obs = _Observer()
    gs.add_observer(obs)
    gs.load_bit("TestBit", config=config)
    return gs, obs


def test_terrarium_is_always_admin_and_config_adds():
    gs = GameServer({"TestBit": TestBit}, admin_devices=["gem-1"])
    assert gs.is_admin(TERRARIUM_ADMIN)
    assert gs.is_admin("gem-1")
    assert not gs.is_admin("ie1")
    assert not gs.is_admin(None)


def test_no_bit_loaded_is_refused_silently():
    gs = GameServer({"TestBit": TestBit})
    obs = _Observer()
    gs.add_observer(obs)
    assert gs.request_start(None, TERRARIUM_ADMIN, "console") == "no Bit loaded"
    assert obs.starts[-1].feedback == FEEDBACK_NONE
    assert gs.state is State.IDLE


def test_console_start_on_a_non_admin_bit_runs_like_before():
    gs, obs = _loaded()                       # TestBit, no config: immediate
    assert gs.request_start(None, TERRARIUM_ADMIN, "console") is None
    assert gs.state is State.RUNNING
    assert obs.starts[-1].accepted and obs.starts[-1].feedback == FEEDBACK_ACCEPT


def test_keyed_start_on_a_non_admin_bit_is_refused():
    gs, obs = _loaded()
    assert gs.request_start("k", "ie1", "device:ie1") == "Bit does not take an admin start"
    assert gs.state is State.SETUP


def test_bad_key_is_silent_and_logged_as_a_record():
    gs, obs = _loaded(_admin_config())
    assert gs.request_start("nope", "ie1", "device:ie1") == "bad key"
    assert obs.starts[-1].feedback == FEEDBACK_NONE
    assert obs.starts[-1].source == "device:ie1"


def test_minimum_not_met_then_met():
    gs, obs = _loaded(_admin_config())
    assert gs.request_start("k", "ie1", "device:ie1") == "minimum not met"
    assert obs.starts[-1].feedback == FEEDBACK_MINIMUM
    gs.hello("ie1", "sim", "1")
    assert gs.join("ie1", "TEST_PLAYER_NODE").granted
    assert gs.request_start("k", "ie1", "device:ie1") is None
    assert gs.state is State.RUNNING


def test_admin_device_overrides_the_minimum():
    gs, obs = _loaded(_admin_config(), admin_devices=["gem-1"])
    assert gs.request_start("k", "gem-1", "device:gem-1") is None
    assert gs.state is State.RUNNING


def test_valid_key_outside_setup_is_refused_with_feedback():
    gs, obs = _loaded(_admin_config())
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.state is State.RUNNING
    assert gs.request_start("k", "ie1", "device:ie1") == "not in SETUP"
    assert obs.starts[-1].feedback == FEEDBACK_REFUSED


def test_lobby_state_follows_registration_and_setup():
    gs, obs = _loaded(_admin_config())
    assert gs.lobby_state() == "WAITING"      # TestBit's player is uncapped
    gs.request_start(None, TERRARIUM_ADMIN, "console")
    assert gs.lobby_state() is None


def test_lobby_state_is_none_when_disabled():
    cfg = _admin_config()
    cfg = replace(cfg, lobby=replace(cfg.lobby, enabled=False))
    gs, obs = _loaded(cfg)
    assert gs.lobby_state() is None
    assert gs.lobby_config().enabled is False


def test_notify_lobby_reaches_observers():
    gs, obs = _loaded()
    gs.notify_lobby("invite", "ie1")
    assert obs.lobby == [("invite", "ie1")]
