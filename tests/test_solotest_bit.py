from control.bit_registry import BitRegistry
from control.engine import GameServer
from control.roles import RoleClass
from tests.helpers_admit import admit


def _gs():
    reg = BitRegistry.discover()
    assert "SoloTestBit" in reg.packages, reg.errors
    gs = GameServer({"SoloTestBit": reg.bit_class("SoloTestBit")},
                    clock=lambda: 100.0)
    gs.load_bit("SoloTestBit")
    return gs


def test_declares_no_jam_role():
    gs = _gs()
    classes = {r.role_class for r in gs.registration.role_table.roles.values()}
    assert RoleClass.JAM not in classes


def test_second_device_gets_solo():
    gs = _gs()
    grants = []
    gs.on_grant = lambda d, r: grants.append((d, r.role))
    admit(gs, "a")
    gs.hello("b", "", "", "tuneshroom")
    gs.request_start(None, "terrarium", "test")
    assert grants == [("a", "player"), ("b", "solo:tuneshroom")]
