import pytest

from control.bit import Bit
from control.roles import RoleTable


class MinimalBit(Bit):
    @property
    def role_table(self) -> RoleTable:
        return RoleTable(roles={}, node_map={})


def test_cannot_instantiate_bit_without_role_table():
    with pytest.raises(TypeError):
        Bit()  # abstract: role_table has no implementation


def test_default_hooks_are_no_ops_and_never_complete():
    bit = MinimalBit()
    assert bit.on_setup_enter() is None
    assert bit.on_run_start() is None
    assert bit.update(0.1) is False
    assert bit.on_complete() is None
    assert bit.result() is None
    assert bit.on_unload() is None
    assert bit.verb_handlers() == {}


def test_result_can_be_overridden_to_report_a_payload():
    class ScoringBit(MinimalBit):
        def result(self):
            return {"score": 42}

    assert ScoringBit().result() == {"score": 42}
