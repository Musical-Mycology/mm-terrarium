"""Rev1Bit: the bench check for the Rev 1 ESP32 board (mm-devshroom).

The venue-loadable counterpart of TestBit for Rev 1 hardware: the Console
lists it, a board joins REV1_PLAYER_NODE, and every gesture the board can
send has one response a person at the bench can see and hear. It never
completes on its own; unload it from the Console when the check is done.

This is NOT the device contract. contract_kit/contract_bit.py's ContractBit
and the recordings it produces are the contract, and ContractBit is kept
unregistered on purpose (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md section 5.4). Rev1Bit gates on the
same Rev 1 capability set; tests/test_rev1_bit.py checks it against the
published tuneshroom_rev1 instrument and ContractBit's copy, so the three
cannot drift apart.
"""

from control.bit import Bit
from control.cues import PlayCue, SolidCue
from control.instrument import InstrumentRequirement
from control.roles import Role, RoleClass, RoleTable

REV1_PLAYER_NODE = "REV1_PLAYER_NODE"

# The Rev 1 hardware capability set (instruments/tuneshroom_rev1.toml).
# Spelled here rather than imported from contract_kit, which is test-only
# and never imported by venue code (contract_kit/__init__.py).
REV1_CAPABILITIES = frozenset({"light.pixels", "gesture.tap", "gesture.hold",
                               "gesture.swing", "audio.samples"})

# The two samples the Rev 1 firmware bundles (plan Task A5,
# src/audio/samples.cpp). A name outside these is the contract kit's
# "unknown sample" case, which this Bit has no reason to exercise.
TAP_SAMPLE = "tick"
HOLD_SAMPLE = "hold"

# Each tap steps the aurora's hue lane to the next of these cc:74 values
# and wraps, so consecutive taps are always a visibly different color.
HUE_CC = 74
TAP_HUE_STEPS = (0, 32, 64, 96, 127)

# Hold and swing are SolidCue flashes over the rendered frame, so they read
# the same whatever hue the taps left behind. SolidCue takes RGB.
HOLD_RGB = (255, 255, 255)
SWING_NEG_RGB = (255, 0, 0)
SWING_POS_RGB = (0, 0, 255)
FLASH_LEVEL = 0.9
HOLD_FLASH_S = 1.0
SWING_FLASH_S = 0.5


class Rev1Bit(Bit):
    version = "0.1"

    room_types = {"TEST", "DEMO"}

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._taps = 0
        self._holds = 0
        self._swings = 0
        self._last_held_s: float | None = None
        self._last_swing_g: float | None = None

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player", role_class=RoleClass.SHARED, capacity=None,
            scored=False, requires="rev1",
            # Control's breath drives cc:11; this role maps only cc:74, so
            # the breath could not move it anyway. Off so nothing but a
            # gesture ever changes the board's frame.
            breath=False,
            uses=["tap", "hold", "swing"],
            samples=[TAP_SAMPLE, HOLD_SAMPLE],
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": 0.5, "level": 0.6},
                 "lanes": [{"source": f"cc:{HUE_CC}", "dest": "hue"}]},
            ]},
        )
        return RoleTable(roles={"player": player},
                         node_map={REV1_PLAYER_NODE: ["player"]})

    def instrument_requirements(self) -> tuple:
        """The "rev1" slot: the five Rev 1 capabilities, the same set as
        ContractBit's gate. The board (`tuneshroom_rev1`) satisfies it; the
        app's full `tuneshroom` profile and the harness `testshroom` do
        not."""
        return (InstrumentRequirement(slot="rev1",
                                      capabilities=REV1_CAPABILITIES),)

    def status(self) -> dict:
        return {"taps": self._taps,
                "holds": self._holds,
                "swings": self._swings,
                "last_held_s": self._last_held_s,
                "last_swing_g": self._last_swing_g}

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap, "hold": self._on_hold,
                "swing": self._on_swing}

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """args: [dev, peak_g, duration_ms, count]. Plays `tick` and steps
        the hue. Every tap steps once: Rev 1 always sends count 1."""
        value = TAP_HUE_STEPS[self._taps % len(TAP_HUE_STEPS)]
        self._taps += 1
        return [PlayCue(dev, TAP_SAMPLE, ""),
                (dev, 0xB0, HUE_CC, value)]

    def _on_hold(self, dev: str, args: list, at: float) -> list:
        """args: [dev, held_seconds, count]. Plays `hold` and flashes white."""
        self._holds += 1
        self._last_held_s = float(args[1]) if len(args) > 1 else None
        return [PlayCue(dev, HOLD_SAMPLE, ""),
                SolidCue(dev, HOLD_RGB, FLASH_LEVEL, HOLD_FLASH_S)]

    def _on_swing(self, dev: str, args: list, at: float) -> list:
        """args: [dev, signed_peak_g, count]. Flashes red for a negative
        swing and blue for a positive one, so the sign the accelerometer
        reported is checkable by eye."""
        g = float(args[1]) if len(args) > 1 else 0.0
        self._swings += 1
        self._last_swing_g = g
        rgb = SWING_NEG_RGB if g < 0 else SWING_POS_RGB
        return [SolidCue(dev, rgb, FLASH_LEVEL, SWING_FLASH_S)]
