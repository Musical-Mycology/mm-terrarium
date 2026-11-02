"""BeatsBit: a devtower and two tuneshrooms connect, a Console button starts.

Devices are told apart by their dev id, as in MinigameBit: one devtower<N>
takes the `tower` role and two ie<N> take the `shroom` role. Anything else,
or one more than those capacities, falls through to its own solo role.

The Console's Start button (Bit.actions) loops piano.mid on the shared grid
until the Bit is unloaded, however many of those devices are connected.
A MusicCue is Room audio only (DeviceLinkAgent._on_music_cue), so the piano
sounds from the MIDITEST Room's flsyn, not from the devtower's own speaker.

While the piano plays, beats1.mid is the rhythm for its first measure and
beats2.mid for its third, on every pass of the loop. Those beats are meant to
flash the tower's responders; with no tower connected yet, each is printed to
the console instead. The print goes out on the tick that reaches the beat, so
it lines up with the piano rather than the Sequencer's look-ahead.

The piano's timing lives in control.sequencer.Sequencer, and neither role has
any light or gestures yet.
"""

from __future__ import annotations

import re

from control.bit import Bit
from control.cues import ROOM, MusicCue
from control.functions import FunctionTable
from control.midifile import read_midi_file
from control.roles import Role, RoleClass, RoleTable
from control.sequencer import Sequencer

BEATS_SHROOM_NODE = "BEATS_SHROOM_NODE"
BEATS_TOWER_NODE = "BEATS_TOWER_NODE"

# The devices that take part: ie1, ie2, ... and devtower1, devtower2, ...
SHROOM_DEV_RE = re.compile(r"ie\d+")
TOWER_DEV_RE = re.compile(r"devtower\d+")
SHROOMS = 2
TOWERS = 1

BPM = 130.0                     # piano.mid carries no tempo; as DeuxAraDropBit
PIANO = "piano"
PIANO_PART = 0
PIANO_PROGRAM = 0               # General MIDI acoustic grand

BEATS_PER_MEASURE = 4
# (asset, measure of the piano loop it plays in, counted from 1). Each asset
# is one measure long, and its own tempo is ignored: it runs on the piano's.
BEAT_CLIPS = (("beats1", 1), ("beats2", 3))


class BeatsBit(Bit):
    version = "0.1"
    room_types = {"MIDITEST"}

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._seq = Sequencer(BPM)
        piano = read_midi_file(self.config.asset_path(PIANO))
        self._seq.add_track(PIANO, piano, part=PIANO_PART, loop=True)
        self._loop_beats = piano.loop_beats()
        self._beats = self._beat_pattern()
        self._beats_done = 0            # beats printed since Start
        self._shrooms: set[str] = set()
        self._towers: set[str] = set()
        self._started = False

    # -- declarations -----------------------------------------------------

    @property
    def role_table(self) -> RoleTable:
        shroom = Role(name="shroom", role_class=RoleClass.JAM,
                      capacity=SHROOMS, scored=False, light_manifest={})
        tower = Role(name="tower", role_class=RoleClass.JAM,
                     capacity=TOWERS, scored=False, light_manifest={})
        return RoleTable(roles={"shroom": shroom, "tower": tower},
                         node_map={BEATS_SHROOM_NODE: ["shroom"],
                                   BEATS_TOWER_NODE: ["tower"]})

    def room_manifests(self) -> tuple[dict, dict]:
        ugen = {"instruments": [
            {"instrument": "flsyn", "program": PIANO_PROGRAM,
             "lanes": [{"source": "cc:7", "dest": "cc:7"}]},
        ]}
        return {}, ugen

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={})

    def actions(self) -> dict:
        return {"start": ("Start", self._on_start)}

    def admits(self, dev: str, role_name: str) -> bool:
        if role_name == "shroom":
            return SHROOM_DEV_RE.fullmatch(dev) is not None
        if role_name == "tower":
            return TOWER_DEV_RE.fullmatch(dev) is not None
        return True

    def on_join(self, dev: str, role_name: str) -> None:
        self._shrooms.discard(dev)
        self._towers.discard(dev)
        if role_name == "shroom":
            self._shrooms.add(dev)
        elif role_name == "tower":
            self._towers.add(dev)

    # -- the tick ---------------------------------------------------------

    def update(self, dt: float) -> bool:
        return False   # plays until unloaded from the Console

    def fires(self, at: float) -> list:
        if not self._started:
            return []
        self._print_due_beats(at)
        return self._seq.advance(at)

    # -- the button -------------------------------------------------------

    def _on_start(self, at: float) -> list | str:
        """Grid beat 0 at `at`, the melodic program restored (the lobby
        reprograms part 0), the piano in. Its first notes go out here rather
        than on the next tick."""
        if self._started:
            return "already started"
        self._started = True
        self._seq.start(at)
        out: list = [MusicCue(ROOM, 0xC0 | PIANO_PART, PIANO_PROGRAM, 0,
                              when=at)]
        out += self._seq.enable(PIANO, at)
        out += self._seq.advance(at)
        self._print_due_beats(at)
        return out

    # -- the beats --------------------------------------------------------

    def _beat_pattern(self) -> list[tuple[float, str, int, float]]:
        """One pass of the piano loop's beats, in order: (loop beat, asset,
        measure, beat within that asset)."""
        pattern = []
        for asset, measure in BEAT_CLIPS:
            offset = (measure - 1) * BEATS_PER_MEASURE
            for e in read_midi_file(self.config.asset_path(asset)).events:
                if e.status & 0xF0 == 0x90:
                    pattern.append((offset + e.beat, asset, measure, e.beat))
        return sorted(pattern)

    def _print_due_beats(self, at: float) -> None:
        """Print every beat reached by `at`. Stands in for flashing the
        tower's responders until the tower is connected."""
        while self._beats:
            passes, i = divmod(self._beats_done, len(self._beats))
            loop_beat, asset, measure, beat = self._beats[i]
            if self._seq.time_at(passes * self._loop_beats + loop_beat) > at:
                return
            print(f"BEAT {asset}: measure {measure} beat {beat + 1:g}",
                  flush=True)
            self._beats_done += 1

    # -- read-outs --------------------------------------------------------

    def status(self) -> dict:
        return {"piano": "playing" if self._started else "waiting",
                "tower": sorted(self._towers),
                "shrooms": sorted(self._shrooms)}
