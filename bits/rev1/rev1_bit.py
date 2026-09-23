"""Rev1Bit: the bench check for the Rev 1 ESP32 board (mm-devshroom).

The venue-loadable counterpart of TestBit for Rev 1 hardware: the Console
lists it, a board joins REV1_PLAYER_NODE, and every gesture the board can
send has one response a person at the bench can see and hear. It never
completes on its own; unload it from the Console when the check is done.

A standard tuneshroom (the harness sim's `testshroom`, or the app's
`tuneshroom`) joins REV1_SIM_NODE instead, gated only on pixels and tap, and
gets the same tap response. REV1_PLAYER_NODE keeps the full Rev 1 gate, so a
board declaring the wrong instrument is still refused rather than admitted
quietly.

This is NOT the device contract. contract_kit/contract_bit.py's ContractBit
and the recordings it produces are the contract, and ContractBit is kept
unregistered on purpose (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md section 5.4). Rev1Bit gates on the
same Rev 1 capability set; tests/test_rev1_bit.py checks it against the
published tuneshroom_rev1 instrument and ContractBit's copy, so the three
cannot drift apart.
"""

from control.bit import Bit
from control.cues import TARGET, FireFunction, PlayCue, SolidCue
from control.functions import (Condition, ConditionSource, Function,
                               FunctionTable, FunctionTarget, ScriptStep)
from control.instrument import InstrumentRequirement
from control.roles import Role, RoleClass, RoleTable

REV1_PLAYER_NODE = "REV1_PLAYER_NODE"
REV1_SIM_NODE = "REV1_SIM_NODE"

# The Rev 1 hardware capability set (instruments/tuneshroom_rev1.toml).
# Spelled here rather than imported from contract_kit, which is test-only
# and never imported by venue code (contract_kit/__init__.py).
REV1_CAPABILITIES = frozenset({"light.pixels", "gesture.tap", "gesture.hold",
                               "gesture.swing", "audio.samples"})

# What REV1_SIM_NODE asks for: enough to see a tap land. Both `testshroom`
# and `tuneshroom` carry it, and so does `tuneshroom_rev1`.
SIM_CAPABILITIES = frozenset({"light.pixels", "gesture.tap"})

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
        def role(name: str, requires: str, uses: list, samples: list) -> Role:
            return Role(
                name=name, role_class=RoleClass.SHARED, capacity=None,
                scored=False, requires=requires,
                # Control's breath drives cc:11; this role maps only cc:74,
                # so the breath could not move it anyway. Off so nothing
                # but a gesture ever changes the device's frame.
                breath=False, uses=uses, samples=samples,
                light_manifest={"instruments": [
                    {"instrument": "aurora", "target": "primary",
                     "params": {"hue": 0.5, "level": 0.6},
                     "lanes": [{"source": f"cc:{HUE_CC}", "dest": "hue"}]},
                ]},
            )
        # The sim role asks only for tap: a standard tuneshroom has no hold
        # or swing, and harness/o2_shroom.py sends those two only when the
        # role's `uses` names them (a long press goes out as a tap instead).
        player = role("player", "rev1", ["tap", "hold", "swing"],
                      [TAP_SAMPLE, HOLD_SAMPLE])
        sim = role("sim", "sim", ["tap"], [TAP_SAMPLE])
        return RoleTable(roles={"player": player, "sim": sim},
                         node_map={REV1_PLAYER_NODE: ["player"],
                                   REV1_SIM_NODE: ["sim"]})

    def instrument_requirements(self) -> tuple:
        """The "rev1" slot: the five Rev 1 capabilities, the same set as
        ContractBit's gate. The board (`tuneshroom_rev1`) satisfies it; the
        app's full `tuneshroom` profile and the harness `testshroom` do
        not. The "sim" slot asks only for pixels and tap, which all three
        satisfy."""
        return (InstrumentRequirement(slot="rev1",
                                      capabilities=REV1_CAPABILITIES),
                InstrumentRequirement(slot="sim",
                                      capabilities=SIM_CAPABILITIES))

    def status(self) -> dict:
        return {"taps": self._taps,
                "holds": self._holds,
                "swings": self._swings,
                "last_held_s": self._last_held_s,
                "last_swing_g": self._last_swing_g}

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap, "hold": self._on_hold,
                "swing": self._on_swing}

    @property
    def function_table(self) -> FunctionTable:
        """One DEVICE trigger per gesture response, so the Console's Triggers
        panel can fire each on a chosen board without touching it.

        The gesture handlers below fire these same triggers, so a real
        gesture and the operator's Fire button share one definition, and a
        live gesture shows up on its trigger's last-fired line. The counters
        in status() move only in the handlers: a manual fire checks the
        response, it is not a gesture. The tap's hue step stays in _on_tap
        because it depends on how many taps came before; tap_tick is the
        stateless part (the sample)."""
        def gesture(name, verb, description):
            return Condition(name=name, description=description,
                             source=ConditionSource.GESTURE_VERB, verb=verb)

        def flash(rgb, seconds):
            return ScriptStep(0.0, SolidCue(TARGET, rgb, FLASH_LEVEL, seconds))

        return FunctionTable(functions={
            "tap_tick": Function(
                name="tap_tick",
                description=f"Tap response: play `{TAP_SAMPLE}`. A real tap "
                            "also steps the hue; a manual fire does not.",
                target=FunctionTarget.DEVICE,
                condition=gesture("tapped", "tap", "A tap on the touch pad"),
                script=(ScriptStep(0.0, PlayCue(TARGET, TAP_SAMPLE, "")),)),
            "hold_flash": Function(
                name="hold_flash",
                description=f"Hold response: play `{HOLD_SAMPLE}` and flash "
                            f"white for {HOLD_FLASH_S:g} s",
                target=FunctionTarget.DEVICE,
                condition=gesture("held", "hold",
                                  "Touch pad held past the hold window"),
                script=(ScriptStep(0.0, PlayCue(TARGET, HOLD_SAMPLE, "")),
                        flash(HOLD_RGB, HOLD_FLASH_S))),
            "swing_negative": Function(
                name="swing_negative",
                description=f"Negative swing response: flash red for "
                            f"{SWING_FLASH_S:g} s",
                target=FunctionTarget.DEVICE,
                condition=gesture("swung_negative", "swing",
                                  "A swing with negative peak g"),
                script=(flash(SWING_NEG_RGB, SWING_FLASH_S),)),
            "swing_positive": Function(
                name="swing_positive",
                description=f"Positive swing response: flash blue for "
                            f"{SWING_FLASH_S:g} s",
                target=FunctionTarget.DEVICE,
                condition=gesture("swung_positive", "swing",
                                  "A swing with positive peak g"),
                script=(flash(SWING_POS_RGB, SWING_FLASH_S),)),
        })

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """args: [dev, peak_g, duration_ms, count]. Fires tap_tick and steps
        the hue. Every tap steps once: Rev 1 always sends count 1."""
        value = TAP_HUE_STEPS[self._taps % len(TAP_HUE_STEPS)]
        self._taps += 1
        return [FireFunction("tap_tick", dev),
                (dev, 0xB0, HUE_CC, value)]

    def _on_hold(self, dev: str, args: list, at: float) -> list:
        """args: [dev, held_seconds, count]. Fires hold_flash."""
        self._holds += 1
        self._last_held_s = float(args[1]) if len(args) > 1 else None
        return [FireFunction("hold_flash", dev)]

    def _on_swing(self, dev: str, args: list, at: float) -> list:
        """args: [dev, signed_peak_g, count]. Fires swing_negative (red) or
        swing_positive (blue), so the sign the accelerometer reported is
        checkable by eye."""
        g = float(args[1]) if len(args) > 1 else 0.0
        self._swings += 1
        self._last_swing_g = g
        return [FireFunction("swing_negative" if g < 0 else "swing_positive",
                             dev)]
