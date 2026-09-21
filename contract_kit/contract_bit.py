"""ContractBit: a control.bit.Bit subclass used only by
contract_kit/recorder.py and its scenarios. Loaded directly as
GameServer({"ContractBit": ContractBit}, ...) -- never through
BitRegistry.scan() -- so it carries no bit.toml and is never discoverable
as a venue Bit (spec section 5.4)."""
from __future__ import annotations

from control.bit import Bit
from control.cues import LightCue, PlayCue
from control.instrument import InstrumentRequirement
from control.roles import Role, RoleClass, RoleTable

CONTRACT_PLAYER_NODE = "CONTRACT_PLAYER_NODE"

# The Rev 1 hardware capability set (instruments/tuneshroom_rev1.toml).
# Defined once here so this module's own player role and
# tests/test_contract_bit.py don't re-spell the five capability tags; no
# production code outside contract_kit/ imports it (contract_kit/__init__.py).
# tests/test_contract_bit.py checks this stays equal to the published
# catalog entry's own capabilities.
REV1_CAPABILITIES = frozenset({"light.pixels", "gesture.tap", "gesture.hold",
                               "gesture.swing", "audio.samples"})

_LOOK_LEAD_S = 0.5          # how far into the future the "timed look" is stamped
_LOOK_CC = 74
_LOOK_VALUE_FIRST = 40
_LOOK_VALUE_LATER = 100
KNOWN_SAMPLE = "tick"
UNKNOWN_SAMPLE = "not_a_real_sample"


class ContractBit(Bit):
    """One player node gated on the Rev 1 capabilities: a tap sends a timed
    look then goes quiet, a hold plays an unknown sample name, a swing is
    handled with no light/sample consequence, and unloading releases the
    device like any other Bit's teardown. Deterministic and offline: the
    recorder (contract_kit/recorder.py, a later task) drives this Bit
    directly with no gameplay logic of its own to keep in sync."""

    version = "0.1"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._tap_count = 0

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player", role_class=RoleClass.SHARED, capacity=None,
            scored=True, requires="rev1", breath=False,
            uses=["tap", "hold", "swing"], samples=[KNOWN_SAMPLE],
            # Only cc:74 is mapped, deliberately: Control's own per-tick
            # breath feed always targets cc:11, and a role with no cc:11
            # lane cannot have its rendered frame changed by it -- the
            # "then goes quiet" half of the timed-look behavior needs
            # nothing to hold still on its own. breath=False above is set
            # anyway, for the same guarantee, belt and suspenders.
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": 0.5, "level": 0.6},
                 "lanes": [{"source": f"cc:{_LOOK_CC}", "dest": "hue"}]},
            ]},
        )
        return RoleTable(roles={"player": player},
                         node_map={CONTRACT_PLAYER_NODE: ["player"]})

    def instrument_requirements(self) -> tuple:
        return (InstrumentRequirement(slot="rev1",
                                      capabilities=REV1_CAPABILITIES),)

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap, "hold": self._on_hold,
                "swing": self._on_swing}

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """A known sample plays immediately (untimed, device-local); the
        "look" is a light cue stamped `_LOOK_LEAD_S` into the future off
        this same `at`, so scenario 4 (Task 7) can script two taps with
        the identical `onset_t` and get the identical `when` back."""
        self._tap_count += 1
        value = _LOOK_VALUE_FIRST if self._tap_count == 1 else _LOOK_VALUE_LATER
        return [PlayCue(dev, KNOWN_SAMPLE, ""),
                LightCue(dev, 0xB0, _LOOK_CC, value, when=at + _LOOK_LEAD_S)]

    def _on_hold(self, dev: str, args: list, at: float) -> list:
        """Plays a sample name the role never declares -- the recorder's
        "one unknown sample" fixture, exercising a device's own refusal/
        fallback for a name it does not recognize."""
        return [PlayCue(dev, UNKNOWN_SAMPLE, "")]

    def _on_swing(self, dev: str, args: list, at: float) -> list:
        return []
