from control.bit import Bit
from control.bit_config import parse_manifest
from control.engine import GameServer
from control.roles import RoleTable


class ConfigBit(Bit):
    @property
    def role_table(self):
        return RoleTable(roles={}, node_map={})


def test_bit_stores_config_and_defaults_to_none():
    assert ConfigBit().config is None
    cfg = parse_manifest("[bit]\nname='C'\nentry='m:C'", source="s")
    assert ConfigBit(cfg).config is cfg


def test_load_bit_passes_config_through():
    cfg = parse_manifest("[bit]\nname='C'\nentry='m:C'", source="s")
    gs = GameServer({"ConfigBit": ConfigBit})
    gs.load_bit("ConfigBit", config=cfg)
    assert gs.bit.config is cfg


def test_load_bit_without_config_calls_zero_arg():
    gs = GameServer({"ConfigBit": ConfigBit})
    gs.load_bit("ConfigBit")
    assert gs.bit.config is None
