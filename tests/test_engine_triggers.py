"""Engine-side trigger behavior: load-time validation, firing, and the
on_trigger_fired observer hook.

Grouped in one file because they share the Bit fixtures below; split from
tests/test_engine.py so the existing lifecycle tests stay readable.
"""

import pytest

from control.bit import Bit
from control.cues import ROOM, TARGET, PlayCue
from control.engine import BitLoadError, GameServer
from control.roles import Role, RoleClass, RoleTable
from control.state import State
from control.triggers import (
    Condition,
    ConditionSource,
    ScriptStep,
    Trigger,
    TriggerTable,
    TriggerTarget,
)


class _BaseBit(Bit):
    version = "0.1"

    @property
    def role_table(self) -> RoleTable:
        return RoleTable(
            roles={"player": Role(name="player", role_class=RoleClass.SHARED,
                                  capacity=None, scored=True)},
            node_map={"NODE": ["player"]})


class PlainBit(_BaseBit):
    """No trigger_table override at all: the default must keep working."""


class GoodTriggerBit(_BaseBit):
    def verb_handlers(self) -> dict:
        return {"tap": lambda dev, args, at: []}

    @property
    def trigger_table(self) -> TriggerTable:
        return TriggerTable(triggers={
            "flash": Trigger(
                name="flash", description="Flash the device",
                target=TriggerTarget.DEVICE,
                condition=Condition(name="tapped", description="Player taps",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="tap"),
                script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),)),
        })


class UnimplementedVerbBit(_BaseBit):
    def verb_handlers(self) -> dict:
        return {"tap": lambda dev, args, at: []}

    @property
    def trigger_table(self) -> TriggerTable:
        return TriggerTable(triggers={
            "flash": Trigger(
                name="flash", description="Flash the device",
                target=TriggerTarget.DEVICE,
                condition=Condition(name="wiggled", description="Player wiggles",
                                    source=ConditionSource.GESTURE_VERB,
                                    verb="wiggle"),
                script=()),
        })


def _server(bit_cls, **kwargs):
    return GameServer({"bit": bit_cls}, **kwargs)


def test_a_bit_declaring_no_triggers_loads_exactly_as_before():
    gs = _server(PlainBit)
    gs.load_bit("bit")
    assert gs.state == State.SETUP
    assert gs.bit.trigger_table.triggers == {}


def test_a_valid_trigger_table_loads():
    gs = _server(GoodTriggerBit)
    gs.load_bit("bit")
    assert gs.state == State.SETUP
    assert "flash" in gs.bit.trigger_table.triggers


def test_a_trigger_naming_an_unimplemented_verb_fails_load():
    """Goal 4 and the spec's section 13 test 1: declared-but-unimplemented
    fails as a BitLoadError at load, and Control returns cleanly to IDLE."""
    gs = _server(UnimplementedVerbBit)
    with pytest.raises(BitLoadError, match="wiggle"):
        gs.load_bit("bit")
    assert gs.state == State.IDLE
    assert gs.bit is None
    assert gs.registration is None
