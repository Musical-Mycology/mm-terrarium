"""SoloContractBit: ContractBit's scored player role and nothing else. With
no unscored role to fall back on, every non-validated device gets a solo
role synthesized for its carried instrument at RUNNING (spec 2026-10-01
section 3.7), which is what contract v3's jam_solo_fallback scenario
records. Test-only, like ContractBit: loaded directly by
contract_kit/recorder.py, never through BitRegistry.scan()."""
from __future__ import annotations

from control.roles import RoleTable

from contract_kit.contract_bit import CONTRACT_PLAYER_NODE, ContractBit, player_role


class SoloContractBit(ContractBit):
    """ContractBit with no jam role."""

    @property
    def role_table(self) -> RoleTable:
        return RoleTable(roles={"player": player_role()},
                         node_map={CONTRACT_PLAYER_NODE: ["player"]})
