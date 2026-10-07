"""A Console Fire click, end to end: ConsoleAgent's fire_function command
through the real GameServer and DeviceLinkAgent (luxaeterna sessions) to
the /<dev>/leds frames a device receives.

tests/test_console_agent.py stubs gs.fire_function and the fire-ladder tests
call it directly, so neither shows a click reaching a device. The case this
pins: under contract v3 a device holds no role (so no light session) until
RUNNING, and a lane-cue fire in SETUP used to report success while sending
nothing. It is now refused with a reason the Console shows on the button.
"""

import pytest

pytest.importorskip("luxaeterna")

from bits.minigame.minigame_bit import MinigameBit
from console.agent import ConsoleAgent
from control.bit import Bit
from control.cues import TARGET
from control.engine import GameServer
from control.functions import (Condition, ConditionSource, Function,
                               FunctionTable, FunctionTarget, ScriptStep)
from control.room_binding import RoomBindingRegistry
from control.roles import Role, RoleClass, RoleTable
from control.rooms import Room
from control.terrarium_config import load_terrarium_config
from devicelink.agent import DeviceLinkAgent
from devicelink.contract import HELLO_INTERVAL_S

from tests.test_console_agent import FakeConsoleServer
from tests.test_devicelink_agent import FakeServer, _Clock

TEST_PROFILE = load_terrarium_config("terrarium.toml").rooms["TEST"].profile
HORIZON = 0.060
TICK = 1.0 / 44.0
HELLO = ["ie1", "esp", "1", "tuneshroom_rev1"]


class _Rig:
    def __init__(self, bits, name):
        self.clk = _Clock(100.0)
        self.gs = GameServer(bits, room_binding=RoomBindingRegistry(),
                             cue_horizon=HORIZON, clock=self.clk)
        self.gs.room = Room(name="TEST", profile=TEST_PROFILE,
                            node_id="ROOM_TEST_NODE")
        self.gs.load_bit(name)
        self.dl = FakeServer()
        self.agent = DeviceLinkAgent(self.gs, self.dl, clock=self.clk,
                                     horizon=HORIZON)
        self.console_srv = FakeConsoleServer()
        self.console = ConsoleAgent(self.gs, self.console_srv)
        self.dl.arrive("c1")
        self._hello()
        self.agent.poll()

    def _hello(self):
        self.dl.deliver("c1", "/game/hello", "ssss", HELLO)
        self._last_hello = self.clk()

    def run(self, seconds):
        for _ in range(int(round(seconds / TICK))):
            self.clk.advance(TICK)
            if self.clk() - self._last_hello >= HELLO_INTERVAL_S:
                self._hello()       # the heartbeat; 15 s silent = reaped
            self.agent.poll()
            self.console.poll()
            self.gs.tick(TICK)

    def click(self, name, dev="ie1"):
        """One Fire click; returns (error message or None, frames sent to
        ie1 over the next second)."""
        before = len(self.dl.addressed("/ie1/leds"))
        err = self.console._handle_command(
            {"command": "fire_function", "name": name, "dev": dev})
        self.run(1.0)
        frames = [bytes(m["args"][0])
                  for m in self.dl.addressed("/ie1/leds")[before:]]
        return (err["message"] if err else None), frames


def _minigame():
    rig = _Rig({"MinigameBit": MinigameBit}, "MinigameBit")
    rig.run(3.0)
    return rig


def _lit(frames):
    return [f for f in frames if sum(f)]


@pytest.mark.parametrize("name", ["blink", "flash"])
def test_a_solid_fire_reaches_a_device_with_no_role_yet(name):
    rig = _minigame()
    assert rig.gs.state.name == "SETUP"
    err, frames = rig.click(name)
    assert err is None
    assert _lit(frames) and _lit(frames)[0][:3] == bytes((230, 230, 230))


@pytest.mark.parametrize("name", ["rainbow_on", "rainbow_off"])
def test_a_lane_fire_in_setup_is_refused_not_silently_dropped(name):
    rig = _minigame()
    err, frames = rig.click(name)
    assert err is not None and "ie1 has no role yet" in err
    assert frames == []


@pytest.mark.parametrize("name", ["blink", "rainbow_on", "flash"])
def test_every_fire_button_reaches_the_device_once_it_holds_its_role(name):
    rig = _minigame()
    rig.gs.run()
    rig.run(3.0)                   # clear the ~1.5 s role-opening signature
    assert "ie1" in rig.gs.registration.assignments
    err, frames = rig.click(name)
    assert err is None
    assert _lit(frames)


def test_rainbow_off_fades_the_ring_dark_in_running():
    rig = _minigame()
    rig.gs.run()
    rig.run(3.0)
    rig.click("rainbow_on")
    err, frames = rig.click("rainbow_off")
    rig.run(1.0)
    assert err is None and frames
    assert sum(rig.dl.addressed("/ie1/leds")[-1]["args"][0]) == 0


class _AllLaneBit(Bit):
    """One lane-cue Function aimed at @all: the Room's fixtures plus every
    connected device."""
    room_types = {"TEST"}

    @property
    def role_table(self):
        return RoleTable(
            roles={"p": Role(name="p", role_class=RoleClass.SHARED,
                             capacity=None, scored=False)},
            node_map={"P_NODE": ["p"]})

    @property
    def function_table(self):
        return FunctionTable(functions={"all_hue": Function(
            name="all_hue", description="hue everywhere",
            target=FunctionTarget.ALL,
            condition=Condition(name="all_hue", description="manual",
                                source=ConditionSource.ADMIN_MANUAL),
            script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 64)),))})


def test_a_lane_fire_that_lands_somewhere_is_never_refused():
    """@all in SETUP reaches the Room's fixtures even though ie1 has no role
    yet: refused only when NOTHING would land."""
    rig = _Rig({"AllLaneBit": _AllLaneBit}, "AllLaneBit")
    rig.run(1.0)
    assert rig.gs.state.name == "SETUP"
    assert rig.gs.fire_function("all_hue", fired_by="admin-manual") is None
