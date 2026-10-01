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
CONTRACT_JAM_NODE = "CONTRACT_JAM_NODE"

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
# The handler-declared refusal a jammer's hold or swing draws (contract v3's
# error_no_state_change). A jammer's role uses only tap.
JAMMER_REFUSAL = "jammer role uses tap only"


def player_role() -> Role:
    """The one scored role, shared by ContractBit and SoloContractBit:
    UNIQUE with capacity 1 (contract v3 caps validated devices at the sum
    of the scored capacities, so one accept fills the round)."""
    return Role(
        name="player", role_class=RoleClass.UNIQUE, capacity=1,
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


class ContractBit(Bit):
    """One player node (UNIQUE, capacity 1) gated on the Rev 1 capabilities,
    plus a JAM node every other device lands on at RUNNING: a tap sends a timed
    look then goes quiet, a hold plays an unknown sample name, a swing is
    handled with no light/sample consequence, a jammer's hold or swing is
    refused with JAMMER_REFUSAL, and unloading releases the device like
    any other Bit's teardown. Deterministic and offline: the
    recorder (contract_kit/recorder.py, a later task) drives this Bit
    directly with no gameplay logic of its own to keep in sync."""

    version = "0.1"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._tap_count = 0
        self._jammers: set[str] = set()

    def on_join(self, dev: str, role_name: str) -> None:
        if role_name == "jammer":
            self._jammers.add(dev)
        else:
            self._jammers.discard(dev)

    @property
    def role_table(self) -> RoleTable:
        # The jam role every non-validated device gets at RUNNING: a dim,
        # steady green with no lanes (so nothing moves it) and no
        # requires, so any carried instrument fits.
        jammer = Role(
            name="jammer", role_class=RoleClass.JAM, capacity=None,
            scored=False, breath=False, uses=["tap"],
            samples=[KNOWN_SAMPLE],
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": 0.33, "level": 0.2}},
            ]},
        )
        return RoleTable(roles={"player": player_role(), "jammer": jammer},
                         node_map={CONTRACT_PLAYER_NODE: ["player"],
                                   CONTRACT_JAM_NODE: ["jammer"]})

    def room_manifests(self) -> tuple[dict, dict]:
        """A steady Room light and no drone, so a Recorder(with_room=True)
        has a ROOM role (and so a Room node) a device can bind a fixture
        through (contract v3's room_node_handshake_binds). Steady for the
        same reason the player's look is: an explicit level and no lanes,
        so a bound fixture's frames settle and go quiet."""
        return ({"instruments": [
            {"instrument": "aurora", "target": "primary",
             "params": {"hue": 0.08, "level": 0.5}},
        ]}, {})

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
        fallback for a name it does not recognize. A jammer's hold is
        refused instead (its role uses only tap)."""
        if dev in self._jammers:
            return JAMMER_REFUSAL
        return [PlayCue(dev, UNKNOWN_SAMPLE, "")]

    def _on_swing(self, dev: str, args: list, at: float) -> list:
        if dev in self._jammers:
            return JAMMER_REFUSAL
        return []
