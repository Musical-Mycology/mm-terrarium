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
            # registry names.
            light_manifest={
                "instruments": [
                    {"instrument": "bloom", "target": "primary",
                     "params": {"hue": 0.33},
                     "lanes": [{"source": "note", "dest": "trigger"},
                               {"source": "cc:74", "dest": "hue"}]},
                ],
            },
            welcome={
                "light": {"instrument": "bloom",
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
