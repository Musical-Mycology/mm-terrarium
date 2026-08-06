"""TestBit: the durable reference/test fixture for the Control+GameServer
lifecycle. Exercises both a scored and a jam role. Not throwaway -- this
stays in the repo as the engine's regression fixture. See design spec
section 4.
"""

from control.bit import Bit
from control.roles import Role, RoleClass, RoleTable

RUN_DURATION_SECONDS = 2.0


class TestBit(Bit):
    version = "0.1"

    def __init__(self, run_duration: float = RUN_DURATION_SECONDS):
        self._run_duration = run_duration
        self._elapsed = 0.0
        self._setup_entered = False
        self._run_started = False
        self._completed = False
        self._unloaded = False

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player", role_class=RoleClass.SHARED, capacity=None,
            scored=True,
            # First real light-lane declaration: the act that freezes the
            # light-manifest v2 authored shape (see control/roles.py).
            # Instrument names are opaque to Control; these are luxaeterna
            # registry names. Declaring `level` opts aurora out of its private
            # breathing clock and onto cc:11, which is what lets the audio
            # swell in step with the visible pulse rather than near it.
            light_manifest={
                "instruments": [
                    {"instrument": "aurora", "target": "primary",
                     "params": {"hue": 0.33, "level": 0.55},
                     "lanes": [{"source": "cc:74", "dest": "hue"},
                               {"source": "cc:11", "dest": "level"}]},
                ],
            },
            # The audio half of the SAME two controllers. cc:74 is General
            # MIDI Brightness (FluidSynth reads it as filter cutoff) and cc:11
            # is Expression (a direct attenuation, so the swell is audible on
            # any soundfont). Both lanes forward the controller unchanged; the
            # lane exists so a role CAN remap a gesture, not because it must.
            # v0 and provisional, not a frozen wire contract: see
            # docs/superpowers/specs/2026-08-06-tuneshroom-audio-design.md.
            ugen_manifest={
                "instruments": [
                    {"instrument": "flsyn", "program": 89,
                     "drone": {"key": 45, "velocity": 90},
                     "lanes": [{"source": "cc:74", "dest": "cc:74"},
                               {"source": "cc:11", "dest": "cc:11"}]},
                ],
            },
            welcome={
                "light": {"instrument": "glow",
                          "params": {"hue": 0.33}, "duration": 1.5},
                "audio": {"instrument": "chime", "duration": 1.5},
            },
        )
        jammer = Role(name="jammer", role_class=RoleClass.JAM,
                      capacity=None, scored=False)
        return RoleTable(
            roles={"player": player, "jammer": jammer},
            node_map={"TEST_PLAYER_NODE": ["player"],
                      "TEST_JAM_NODE": ["jammer"]},
        )

    def on_setup_enter(self) -> None:
        self._setup_entered = True

    def on_run_start(self) -> None:
        self._run_started = True
        self._elapsed = 0.0

    def update(self, dt: float) -> bool:
        self._elapsed += dt
        return self._elapsed >= self._run_duration

    def on_complete(self) -> None:
        self._completed = True

    def on_unload(self) -> None:
        self._unloaded = True

    def status(self) -> dict:
        return {"elapsed": round(self._elapsed, 2),
                "run_duration": self._run_duration}

    def verb_handlers(self) -> dict:
        """Gameplay verbs beyond the fixed lifecycle set. `tilt` maps device
        tilt onto cc:74, which this Bit's `player` role binds to aurora's hue
        lane -- so tilting a device glides its Shroom's colour. Boundary
        rule 3: the Bit decides the light consequence, not the transport."""
        return {"tilt": self._on_tilt}

    def _on_tilt(self, dev: str, args: list) -> list:
        """args: [dev, gamma]. gamma is degrees in [-90, 90]."""
        gamma = float(args[1]) if len(args) > 1 else 0.0
        gamma = max(-90.0, min(90.0, gamma))
        cc = int(round((gamma + 90.0) / 180.0 * 127.0))
        return [(dev, 0xB0, 74, cc)]
