"""CalibrationBit: measure the audio delay from tuneshrooms tapping to knocks.

Devices are told apart by their dev id, as in BeatsBit: any number of ie<N>
take the `shroom` role, and anything else falls through to its own solo role.
The role has no light of its own.

The Console's button (Bit.actions) loops calibration.mid until pressed again,
at BPM whatever tempo the file carries, on GM program 115 (Woodblock). It reads
Play while stopped and Stop while looping, and each Play starts from the top. A
MusicCue is Room audio only (DeviceLinkAgent._on_music_cue), so it sounds from
the MIDITEST Room's flsyn, not from the tuneshrooms.

A player taps along with the knocks they hear, so a tap lands late by however
long the sound took to reach them. Each shroom tap during a loop is compared
with the nearest knock in the file: its delay is the tap's own moment less that
knock's grid time, positive when the tap is late. The Console shows the latest
delay and the mean of them all, signed, so that taps either side of the true
delay cancel out. Play starts a new measurement; Stop keeps the last one on show.
Taps from other devices, or while stopped, do not count.

Every counted tap also saves the mean in Bit.carried, where it outlasts this Bit
for later Bits to read: BeatsBit holds its lights back by it. Play alone leaves
the saved mean as it is, until the new measurement's first tap replaces it.

The measurement is everything between the knock leaving Control and the tap
being stamped, which includes the player's own timing. A delay of more than half
the gap between knocks reads as early against the next knock.

The timing lives in control.sequencer.Sequencer.
"""

from __future__ import annotations

import math
import re

from control.bit import MEAN_TAP_DELAY_S, Bit
from control.cues import ROOM, MusicCue
from control.functions import FunctionTable
from control.midifile import read_midi_file
from control.roles import Role, RoleClass, RoleTable
from control.sequencer import Sequencer

CALIBRATION_SHROOM_NODE = "CALIBRATION_SHROOM_NODE"

SHROOM_DEV_RE = re.compile(r"ie\d+")

BPM = 75.0
CALIBRATION = "calibration"
PART = 0
PROGRAM = 115                   # General MIDI woodblock, numbered from 0


def _ms(seconds: float | None) -> str:
    """A delay as the Console and the log show it: "+212.5 ms", or "-" for
    none yet."""
    return "-" if seconds is None else f"{seconds * 1000:+.1f} ms"


class CalibrationBit(Bit):
    version = "0.1"
    room_types = {"MIDITEST"}

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._seq = Sequencer(BPM)
        midi = read_midi_file(self.config.asset_path(CALIBRATION))
        self._seq.add_track(CALIBRATION, midi, part=PART, loop=True)
        self._loop_beats = midi.loop_beats()
        # One pass of the loop's knocks, as beats within it.
        self._knocks = sorted(e.beat for e in midi.events
                              if e.status & 0xF0 == 0x90)
        self._latest_delay: float | None = None     # seconds
        self._delay_total = 0.0
        self._delay_count = 0
        self._shrooms: set[str] = set()

    # -- declarations -----------------------------------------------------

    @property
    def role_table(self) -> RoleTable:
        shroom = Role(name="shroom", role_class=RoleClass.JAM, capacity=None,
                      scored=False, uses=["tap"], light_manifest={})
        return RoleTable(roles={"shroom": shroom},
                         node_map={CALIBRATION_SHROOM_NODE: ["shroom"]})

    def room_manifests(self) -> tuple[dict, dict]:
        ugen = {"instruments": [
            {"instrument": "flsyn", "program": PROGRAM,
             "lanes": [{"source": "cc:7", "dest": "cc:7"}]},
        ]}
        return {}, ugen

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={})

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap}

    def actions(self) -> dict:
        label = "Stop" if self._seq.is_on(CALIBRATION) else "Play"
        return {"play": (label, self._on_press)}

    def admits(self, dev: str, role_name: str) -> bool:
        if role_name == "shroom":
            return SHROOM_DEV_RE.fullmatch(dev) is not None
        return True

    def on_join(self, dev: str, role_name: str) -> None:
        self._shrooms.discard(dev)
        if role_name == "shroom":
            self._shrooms.add(dev)

    # -- the tick ---------------------------------------------------------

    def update(self, dt: float) -> bool:
        return False   # stays loaded until unloaded from the Console

    def fires(self, at: float) -> list:
        return self._seq.advance(at) if self._seq.is_on(CALIBRATION) else []

    # -- the button -------------------------------------------------------

    def _on_press(self, at: float) -> list:
        """Stop if looping, its queued knocks dropped. Otherwise grid beat 0 at
        `at`, the woodblock restored (the lobby reprograms part 0), the file
        in. Its first notes go out here rather than on the next tick."""
        if self._seq.is_on(CALIBRATION):
            return self._seq.disable(CALIBRATION, at)
        self._latest_delay = None
        self._delay_total = 0.0
        self._delay_count = 0
        self._seq.start(at)
        out: list = [MusicCue(ROOM, 0xC0 | PART, PROGRAM, 0, when=at)]
        out += self._seq.enable(CALIBRATION, at)
        out += self._seq.advance(at)
        return out

    # -- the tap ----------------------------------------------------------

    def _delay_from_nearest_knock(self, tap: float) -> float:
        """Seconds from the knock nearest real time `tap` to `tap`, late
        positive. The nearest may be in the pass before or after `tap`'s, and
        no knock comes before the first."""
        this_pass = math.floor(self._seq.beat_at(tap) / self._loop_beats)
        knocks = [self._seq.time_at(p * self._loop_beats + knock)
                  for p in range(max(this_pass - 1, 0), max(this_pass, 0) + 2)
                  for knock in self._knocks]
        return tap - min(knocks, key=lambda t: abs(tap - t))

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """`at` is presentation time, a horizon ahead of the tap's own moment,
        and a knock's grid time is the moment it leaves for the speaker."""
        if (dev not in self._shrooms or not self._knocks
                or not self._seq.is_on(CALIBRATION)):
            return []
        delay = self._delay_from_nearest_knock(at - self.cue_horizon)
        self._latest_delay = delay
        self._delay_total += delay
        self._delay_count += 1
        self.carried[MEAN_TAP_DELAY_S] = self.mean_delay()
        print(f"tap {dev}: delay {_ms(delay)}, mean {_ms(self.mean_delay())}, "
              f"{self._delay_count} taps", flush=True)
        return []

    # -- read-outs --------------------------------------------------------

    def mean_delay(self) -> float | None:
        """The mean delay in seconds, or None before the first tap counted."""
        if not self._delay_count:
            return None
        return self._delay_total / self._delay_count

    def status(self) -> dict:
        return {"calibration": ("playing" if self._seq.is_on(CALIBRATION)
                                else "ready"),
                "shrooms": sorted(self._shrooms),
                "latest delay": _ms(self._latest_delay),
                "mean delay": _ms(self.mean_delay()),
                "taps": self._delay_count}
