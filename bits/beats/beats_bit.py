"""BeatsBit: a devtower and up to three tuneshrooms connect, a Console button
starts.

Devices are told apart by their dev id, as in MinigameBit: one devtower<N>
takes the `tower` role and three ie<N> take the `shroom` role. Anything else,
or one more than those capacities, falls through to its own solo role.

There are SHROOMS (3) players, and the Console has one dropdown per player
(Bit.actions) choosing which seated shroom it is, listing the seated shrooms
and showing "-" while none is. A shroom takes the first free player as it
connects and keeps it across a reconnect, so by default the first to connect
is player1. Picking a shroom makes it that player, and if it was another
player the two swap. Each tap's printed lines name its player, and status() shows
which device each player is. A seated shroom that is no player (one connecting
while every player is taken) stays seated, so a dropdown can bring it in with no
reconnect, but its taps do nothing and it neither blinks nor scores. The
players are fixed once Start is pressed, since a score is a mean over every
answer beat since Start.

The Console's Music dropdown (Bit.actions) chooses what plays, one of MUSICS,
music1 until picked, until Start fixes it. The Start button brings in every track of the music together,
and each loops on the shared grid until the Bit is unloaded, however many of
those devices are connected. Each track repeats at its own length, all on
Start's beat 0. A MusicCue is Room audio only (DeviceLinkAgent._on_music_cue), so the
music sounds from the MIDITEST Room's flsyn, not from the devtower's own
speaker. Its parts are the same whichever music is chosen (PART_PROGRAMS),
since the Room's instruments are fixed when the Bit loads: the files carry no
programs of their own.

The Console's Beats dropdown (Bit.actions) chooses the call, one of BEATS,
beats1 until picked, until Start fixes it. Each beats is a folder of one-measure clips, one per gesture
it asks for (assets/judging/beatsN/tap.mid, swing.mid, squeeze.mid): beats1
taps only, beats2 adds swings, beats3 and beats4 squeezes too. While the music
plays, every clip of the chosen beats is the rhythm for CALL_MEASURES, the
first of every CYCLE_BEATS (four measures). Each beat of any of
them lights one of the tower's RESPONDERS (4), chosen by where the beat falls in
its measure: 1 to 1.99 the first responder, 2 to 2.99 the second, 3 to 3.99
the third and 4 to 4.99 the fourth. A responder starts at its gesture's
GESTURE_RGB on its beat, red for a tap, green for a swing and blue for a
squeeze, and fades to dark over RESPONDER_FADE_S, stepped each tick by fires()
as in MinigameBit. Each gesture fades on its own and a responder shows their
sum, so a beat asking for a tap and a swing lights yellow, and a beat landing
on a responder still fading mixes with what is left of it; a beat of the same
gesture restarts that gesture's fade from full.
Every responder runs the light delay behind the grid, in seconds whatever the
tempo, so that its light meets the music coming out of the speaker: Arco takes
no timestamps, so the sound cannot be sent early, only the light held back.
The delay is the mean tap delay CalibrationBit last saved in Bit.carried, or
LIGHT_DELAY_S if nothing has been calibrated since the Terrarium started.

The tower's frame pixels are BAR_PIXELS (32) of bar, then the responders'
RESPONDER_PIXELS (24) each, bottom first. The tower role declares no light of
its own, so the frame is dark under the PixelFlashCue paint.

The bar is the players' shared progress, 0 to PROGRESS_FULL (50): a tap earning
more than PROGRESS_MIN_POINTS on its answer beat adds 1, whichever shroom made
it, painted at once. The bar's BAR_LIGHTS (16) two-LED lights fill white from
the bottom, the top one at a partial level, so every point shows. PixelFlashCue
paint lapses, so fires() repaints it every BAR_REFRESH_S, which also picks up a
tower that joins late. Nothing happens at full: the bar stays full and the music
plays on.

A tap on player1's shroom blinks every player's shroom: FLASH_RGB at full on
all FLASH_PIXELS, fading to dark over FLASH_FADE_S, painted with PixelFlashCue
and stepped by fires() the same way. It needs no Start, and a blink mid-fade
restarts from full. A swing on player2's shroom blinks the same way in
SWING_FLASH_RGB. Only player1's taps and player2's swings blink, and only they
are printed: the other players' taps and swings are still judged but blink
nothing. A tap or swing from any other device does nothing.

Any player's squeeze is printed. A tilt, which a tuneshroom sends every few
tens of milliseconds whatever its role asks for, is accepted and ignored, so it
draws no "unknown verb" error back to the device. A squeeze from any other
device is not printed.

Every player's gesture sounds on that player's own tuneshroom as a PlayCue,
sent to the device as /<dev>/play <sample> "key=<key>": the key follows the
judgment and the sample the gesture. Judged GOOD it is the key
GOOD_KEY_TRACK (music1's Basic Sine Keys) has sounding at the answer beat it
took (good_key), so a run of GOOD gestures plays the keys' line; judged BAD
it is BAD_TAP_KEY; a tap or swing plays TAP_SAMPLE and a squeeze SQUEEZE_SAMPLE.
Not judged (before Start, or taking no beat), a tap plays TAP_SAMPLE at
TAP_KEY, a squeeze SQUEEZE_SAMPLE at SQUEEZE_KEY, and a swing nothing. It is
untimed, so the shroom sounds it as the cue arrives, with no cue_horizon. The
Room plays nothing for a gesture.

Call and response: the first measure of each cycle is the responders' call,
and its other three (ANSWER_MEASURES) are the players' answer, the same rhythm
repeated in each, judged all three times, each answer beat asking for its
clip's gesture. Each shroom answers for itself, and
each gesture is judged on its own, below as for a tap: a beat asking for a tap
and a swing is two answer beats. Its tap takes the nearest tap beat of the answer it has
not yet taken within TAP_WINDOW_BEATS either side, so the window in seconds
follows the tempo, and earns 1 dead on, falling linearly to 0 at the window's
edge. A late tap is forgiven LATE_GRACE_S first, in seconds whatever the tempo:
one that late counts as dead on, and the window's late edge moves out by as
much. The whole judgment runs JUDGE_DELAY_S behind the grid, in seconds whatever
the tempo: an answer beat's dead on is that long after the beat itself, and the
window, its grace and its closing all move with it. A beat with no tap earns 0,
and a tap that takes no beat earns nothing.
A player's score, 0 to 1, is the mean over every answer beat since Start whose
window has closed.

Each answer beat is also rated, per player, as its window closes: GOOD when
the player's tap took it within TAP_WINDOW_BEATS (and earned its points), BAD
when the tap took it outside that but within BAD_WINDOW_BEATS (earning 0), and
MISS when no tap took it. A tap takes the nearest beat it has not yet taken
within BAD_WINDOW_BEATS, with the same grace and slide, and the beat closes
once that wider window, its grace and the slack have passed. status() counts
each player's GOOD, BAD and MISS since Start.

The music's timing lives in control.sequencer.Sequencer.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import NamedTuple

from control.bit import MEAN_TAP_DELAY_S, Bit
from control.cues import ROOM, MusicCue, PixelFlashCue, PlayCue
from control.functions import FunctionTable
from control.midifile import read_midi_file
from control.roles import Role, RoleClass, RoleTable
from control.sequencer import Sequencer

BEATS_SHROOM_NODE = "BEATS_SHROOM_NODE"
BEATS_TOWER_NODE = "BEATS_TOWER_NODE"

# The devices that take part: ie1, ie2, ... and devtower1, devtower2, ...
SHROOM_DEV_RE = re.compile(r"ie\d+")
TOWER_DEV_RE = re.compile(r"devtower\d+")
SHROOMS = 3                     # seats, and players
TOWERS = 1

# The music files carry no tempo, so every music plays at this; as DeuxAraDropBit.
BPM = 130.0

# The Room's flsyn parts, list index = part, for every music. A track's notes
# go out on its part whatever channel the file used. The drum part is the
# General MIDI kit, which takes no program.
KEYS_PART = 0
DRUM_PART = 1
SUB_BASS_PART = 2
BASS_PART = 3
# General MIDI programs, numbered from 0, or None for the drum kit, chosen
# from the files' names.
PART_PROGRAMS = (4,             # electric piano 1, for "Basic Sine Keys"
                 None,          # the drum kit, for "Thicket Kit"
                 38,            # synth bass 1, for "808 Pure"
                 39)            # synth bass 2, for "Abel Bass"


class Track(NamedTuple):
    """One file of a music: its asset, its part, and how many beats it loops
    on, when its last note-off runs past its final bar line (otherwise the
    Sequencer rounds its end up to a bar)."""
    asset: str
    part: int
    loop_beats: float | None = None


# The Console's music choices, the first until picked. Every track of the
# chosen music comes in together at Start. The asset names are bit.toml's
# [assets]. music1's files each end on a note at beat 32 that restarts the
# loop, which loop_beats drops (otherwise the loop would round up to 36 or 40).
MUSICS = {
    "music1": (Track("sub_808", SUB_BASS_PART, loop_beats=32.0),
               Track("abel_bass", BASS_PART, loop_beats=32.0),
               Track("sine_keys", KEYS_PART, loop_beats=32.0),
               Track("thicket_kit", DRUM_PART, loop_beats=32.0)),
}

BEATS_PER_MEASURE = 4
# The call and response repeats every four measures, whatever the music.
CYCLE_BEATS = 4 * BEATS_PER_MEASURE
# The cycle's measures, counted from 1: the call is shown in CALL_MEASURES and
# the players repeat it in each of ANSWER_MEASURES, every one judged.
CALL_MEASURES = (1,)
ANSWER_MEASURES = (2, 3, 4)
TAP, SWING, SQUEEZE = "tap", "swing", "squeeze"
GESTURES = (TAP, SWING, SQUEEZE)    # also the order a beat's gestures close in
# The Console's beats choices, the first until picked: each gesture's clip,
# as bit.toml's [assets] names. A clip is one measure long, played in every
# call measure, and its own tempo is ignored: it runs on BPM. A gesture with
# no clip in the chosen beats is not asked for.
BEATS = {
    "beats1": {TAP: "beats1_tap"},
    "beats2": {TAP: "beats2_tap", SWING: "beats2_swing"},
    "beats3": {TAP: "beats3_tap", SWING: "beats3_swing",
               SQUEEZE: "beats3_squeeze"},
    "beats4": {TAP: "beats4_tap", SWING: "beats4_swing",
               SQUEEZE: "beats4_squeeze"},
}

BAR_PIXELS = 32                 # the Dev Tower's bar LEDs, ahead of the responders
BAR_LIGHT_PIXELS = 2            # LEDs that light up as one
BAR_LIGHTS = BAR_PIXELS // BAR_LIGHT_PIXELS     # 16
BAR_REFRESH_S = 0.5             # how often the bar's paint is renewed
BAR_HOLD_S = 1.0                # the paint outlasts the gap to its renewal
PROGRESS_FULL = 50              # the bar's full score
PROGRESS_MIN_POINTS = 0.1       # a tap must earn more than this to add 1
RESPONDERS = BEATS_PER_MEASURE  # one per beat of the measure, bottom first
RESPONDER_PIXELS = 24           # LEDs a responder light
RESPONDER_FADE_S = 0.25         # a responder's fade, full to dark
# A responder's colour for each gesture its beat asks for, at half bright;
# a responder lit for several at once shows their sum.
GESTURE_RGB = {"tap": (64, 0, 0),           # red
               "swing": (0, 64, 0),         # green
               "squeeze": (0, 0, 64)}       # blue
# How far a responder's light is held behind its beat until CalibrationBit has
# measured it. Raise it if the light still leads the sound, lower it if the
# light trails. Not measured: it is JUDGE_DELAY_S's hand-set audio delay less
# the one tick (~23 ms) a light cue already loses between fires() and the next
# render. It depends on the box's audio output, so tune it on the machine that
# plays the Room.
LIGHT_DELAY_S = 0.175

TAP_WINDOW_BEATS = 0.25         # either side of an answer beat: GOOD
BAD_WINDOW_BEATS = 0.5          # either side, past TAP_WINDOW_BEATS: BAD
LATE_GRACE_S = 0.020            # how late a tap still counts as dead on
JUDGE_DELAY_S = 0.200           # the judgment's slide behind the grid
JUDGE_SLACK_S = 0.050           # a late tap's trip, before a beat closes

FLASH_PIXELS = 12               # a tuneshroom's LEDs (8 ring + 4 stem)
FLASH_FADE_S = 0.2              # a tap's blink, full to dark
FLASH_RGB = (128, 0, 255)       # MinigameBit's tap purple
SWING_FLASH_RGB = (255, 200, 0) # a swing's yellow
TAP_SEAT = 0                    # only player1's taps blink and are printed
SWING_SEAT = 1                  # only player2's swings blink and are printed

TAP_SAMPLE = "guzheng"          # the tuneshroom's own instrument for a tap
TAP_KEY = 69                    # A4: an unjudged tap
BAD_TAP_KEY = 49                # C#3/Db3: any BAD gesture
# A GOOD gesture plays the key this track (music1's "Basic Sine Keys") has
# sounding at the answer beat it took, on its own loop.
GOOD_KEY_TRACK = "sine_keys"
SQUEEZE_SAMPLE = "chime"        # the tuneshroom's own instrument for a squeeze
SQUEEZE_KEY = 75                # D#5/Eb5, as a MIDI note number


def responder_pixels(r: int) -> range:
    """Responder r's pixels, after the bar: 32 + 24r..32 + 24r + 23."""
    start = BAR_PIXELS + r * RESPONDER_PIXELS
    return range(start, start + RESPONDER_PIXELS)


def bar_light_value(light: int, progress: int) -> int:
    """Bar light `light`'s W level, 0..255, at `progress`, bottom first."""
    filled = progress * BAR_LIGHTS / PROGRESS_FULL - light
    return round(min(max(filled, 0.0), 1.0) * 255)


GOOD, BAD, MISS = "GOOD", "BAD", "MISS"
RATINGS = (GOOD, BAD, MISS)


def tap_rating(error_beats: float) -> str:
    """A tap's rating `error_beats` from the beat it took: GOOD inside
    TAP_WINDOW_BEATS, else BAD (it took the beat, so it was within
    BAD_WINDOW_BEATS)."""
    return GOOD if abs(error_beats) <= TAP_WINDOW_BEATS else BAD


def tap_points(error_beats: float) -> float:
    """What a tap `error_beats` from its beat earns: 1 dead on, 0 at or past
    the window's edge."""
    return max(1.0 - abs(error_beats) / TAP_WINDOW_BEATS, 0.0)


def forgive_late(error_beats: float, grace_beats: float) -> float:
    """A tap's error with a late tap's grace taken off: a late tap no later
    than the grace is dead on, and an early tap is unchanged."""
    return max(error_beats - grace_beats, 0.0) if error_beats > 0 else error_beats


def measure_and_beat(grid_beat: float) -> tuple[int, float]:
    """A grid beat as (measure since Start, beat within it), both from 1."""
    measure, beat = divmod(grid_beat, BEATS_PER_MEASURE)
    return int(measure) + 1, beat + 1


def fade_value(t: float, fade_s: float) -> int:
    """A fade's level, 255..0, `t` seconds into a fade lasting `fade_s`."""
    return round(min(max(1.0 - t / fade_s, 0.0), 1.0) * 255)


class BeatsBit(Bit):
    version = "0.1"
    room_types = {"MIDITEST"}

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._seq = Sequencer(BPM)
        for tracks in MUSICS.values():
            for track in tracks:
                self._seq.add_track(
                    track.asset,
                    read_midi_file(self.config.asset_path(track.asset)),
                    part=track.part, loop=True, loop_beats=track.loop_beats)
        self._music = next(iter(MUSICS))    # what Start plays
        # GOOD_KEY_TRACK's note-ons over one loop, (beat, key), in order.
        track = next(t for tracks in MUSICS.values() for t in tracks
                     if t.asset == GOOD_KEY_TRACK)
        self._good_loop = track.loop_beats
        self._good_notes = sorted(
            (e.beat, e.data1)
            for e in read_midi_file(self.config.asset_path(track.asset)).events
            if e.status & 0xF0 == 0x90 and e.data2 > 0
            and e.beat < track.loop_beats)
        # Each beats choice's call, read once: gesture -> clip beats.
        self._clips = {name: {gesture: self._clip_beats(asset)
                              for gesture, asset in clips.items()}
                       for name, clips in BEATS.items()}
        self._beats_done = 0            # beats struck since Start
        self._measures_begun = 0        # measures announced since Start
        self._answers_announced = 0     # answer beats printed since Start
        self._answers_closed = 0        # answer beats judged since Start
        self._choose_beats(next(iter(BEATS)))
        # Open answer beats' points: (grid beat, gesture) -> {shroom: points}.
        self._open_taps: dict[tuple[float, str], dict[str, float]] = {}
        self._earned: dict[str, float] = {}     # shroom -> points, closed beats
        # Open answer beats' ratings: (grid beat, gesture) -> {shroom: rating}.
        self._open_ratings: dict[tuple[float, str], dict[str, str]] = {}
        # Closed answer beats' ratings: shroom -> {GOOD/BAD/MISS: count}.
        self._ratings: dict[str, dict[str, int]] = {}
        self._progress = 0              # the players' shared bar score
        self._tracks_in = 0             # the chosen music's tracks playing
        self._bar_refresh_at = 0.0      # when fires() next repaints the bar
        # Responders' fades under way: (r, gesture) -> when it started.
        self._responders: dict[tuple[int, str], float] = {}
        # The (R, G, B) last sent for each responder still lit.
        self._responder_sent: dict[int, tuple[int, int, int]] = {}
        # The shrooms' blink under way: (its tap, last level sent), or None.
        self._flash: tuple[float, int, tuple] | None = None
        self._shrooms: list[str] = []   # seated, in the order they connected
        # Player n's shroom at index n - 1, or None while it has none.
        self._seats: list[str | None] = [None] * SHROOMS
        self._towers: set[str] = set()
        self._started = False

    # -- declarations -----------------------------------------------------

    @property
    def role_table(self) -> RoleTable:
        shroom = Role(name="shroom", role_class=RoleClass.JAM,
                      capacity=SHROOMS, scored=False, uses=["tap", "swing", "squeeze"],
                      samples=[TAP_SAMPLE, SQUEEZE_SAMPLE], light_manifest={})
        tower = Role(name="tower", role_class=RoleClass.JAM,
                     capacity=TOWERS, scored=False, light_manifest={})
        return RoleTable(roles={"shroom": shroom, "tower": tower},
                         node_map={BEATS_SHROOM_NODE: ["shroom"],
                                   BEATS_TOWER_NODE: ["tower"]})

    def room_manifests(self) -> tuple[dict, dict]:
        volume = {"source": "cc:7", "dest": "cc:7"}
        ugen = {"instruments": [
            {"instrument": "flsyn", "drums": True, "lanes": [volume]}
            if program is None else
            {"instrument": "flsyn", "program": program, "lanes": [volume]}
            for program in PART_PROGRAMS
        ]}
        return {}, ugen

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={})

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap, "swing": self._on_swing,
                "squeeze": self._on_squeeze, "tilt": self._on_tilt}

    def actions(self) -> dict:
        players = {f"player{n}": (
                       f"Player {n}",
                       lambda at, dev, seat=n - 1: self._on_player(seat, dev),
                       list(self._shrooms), dev)
                   for n, dev in enumerate(self._seats, start=1)}
        return {**players,
                "music": ("Music", self._on_music, list(MUSICS), self._music),
                "beats": ("Beats", self._on_beats, list(BEATS),
                          self._beats_name),
                "start": ("Start", self._on_start)}

    def admits(self, dev: str, role_name: str) -> bool:
        if role_name == "shroom":
            return SHROOM_DEV_RE.fullmatch(dev) is not None
        if role_name == "tower":
            return TOWER_DEV_RE.fullmatch(dev) is not None
        return True

    def on_join(self, dev: str, role_name: str) -> None:
        self._towers.discard(dev)
        if role_name == "shroom":
            if dev not in self._shrooms:
                self._shrooms.append(dev)
            if dev not in self._seats and None in self._seats:
                self._seats[self._seats.index(None)] = dev
            return
        if dev in self._shrooms:
            self._shrooms.remove(dev)
            self._seats = [None if d == dev else d for d in self._seats]
        if role_name == "tower":
            self._towers.add(dev)

    def _playing(self) -> list[str]:
        """The shrooms that play, player1 first."""
        return [dev for dev in self._seats if dev is not None]

    def _at_seat(self, dev: str, seat: int) -> bool:
        """Whether `dev` is the player at `seat` (0 is player1)."""
        return seat < len(self._seats) and self._seats[seat] == dev

    def _tap_source(self, dev: str) -> str:
        """Who a playing shroom's tap is from, as printed: "player1 (ie3)"."""
        return f"player{self._seats.index(dev) + 1} ({dev})"

    # -- the tick ---------------------------------------------------------

    def update(self, dt: float) -> bool:
        return False   # plays until unloaded from the Console

    def fires(self, at: float) -> list:
        out = self._flash_step(at)
        if not self._started:
            return out
        self._announce_measures(at)
        self._announce_answers(at)
        self._strike_due_beats(at)
        self._close_answers(at)
        return (out + self._seq.advance(at) + self._responder_steps(at)
                + self._bar_refresh(at))

    # -- the controls -----------------------------------------------------

    def _on_player(self, seat: int, chosen: str) -> list | str:
        """Make seated shroom `chosen` player seat + 1, swapping with
        whichever player it was, until Start fixes the players."""
        if self._started:
            return "already started"
        if chosen not in self._shrooms:
            return f"{chosen} is not connected"
        current = self._seats[seat]
        if chosen in self._seats:
            self._seats[self._seats.index(chosen)] = current
        self._seats[seat] = chosen
        return []

    def _on_music(self, at: float, name: str) -> list | str:
        """Choose `name`, one of MUSICS, until Start fixes it."""
        if self._started:
            return "already started"
        if name not in MUSICS:
            return f"unknown music {name!r}"
        self._music = name
        return []

    def _on_beats(self, at: float, name: str) -> list | str:
        """Choose `name`, one of BEATS, until Start fixes it."""
        if self._started:
            return "already started"
        if name not in BEATS:
            return f"unknown beats {name!r}"
        self._choose_beats(name)
        return []

    def _on_start(self, at: float) -> list | str:
        """Grid beat 0 at `at`, the melodic programs restored (the lobby
        reprograms part 0), every track of the chosen music in. Its first
        notes go out here rather than on the next tick, as does any light
        already due; beat 0's light comes the light delay later, from
        fires()."""
        if self._started:
            return "already started"
        self._started = True
        self._seq.start(at)
        out: list = [MusicCue(ROOM, 0xC0 | part, program, 0, when=at)
                     for part, program in enumerate(PART_PROGRAMS)
                     if program is not None]
        for track in MUSICS[self._music]:
            print(f"track in: {track.asset}", flush=True)
            out += self._seq.enable(track.asset, at)
        self._tracks_in = len(MUSICS[self._music])
        out += self._seq.advance(at)
        self._strike_due_beats(at)
        out += self._responder_steps(at)
        return out

    # -- the measures -----------------------------------------------------

    def _announce_measures(self, at: float) -> None:
        """Print each measure as it reaches the real time behind presentation
        time `at`, so the line comes with the sound. A measure of
        ANSWER_MEASURES is also the players' answer, and so is judged."""
        while True:
            start = self._seq.time_at(self._measures_begun * BEATS_PER_MEASURE)
            if start > at - self.cue_horizon:
                return
            self._measures_begun += 1
            measure = self._measures_begun
            print(f"====== Measure {measure} Playing ======", flush=True)
            if (measure - 1) % (CYCLE_BEATS // BEATS_PER_MEASURE) + 1 \
                    in ANSWER_MEASURES:
                print(f"====== Judging {measure} Start ======", flush=True)

    def _announce_answers(self, at: float) -> None:
        """Print each correct answer's O2 time, the moment a tap is judged
        against (its grid beat plus JUDGE_DELAY_S), as it reaches the real time
        behind presentation time `at`, so the line comes on that moment."""
        while self._answers:
            target, gesture = self._answer(self._answers_announced)
            beat_at = self._seq.time_at(target) + JUDGE_DELAY_S
            if beat_at > at - self.cue_horizon:
                return
            self._answers_announced += 1
            measure, in_measure = measure_and_beat(target)
            print(f"answer {gesture} measure {measure} beat {in_measure:g}: "
                  f"o2 t={beat_at:.3f} s", flush=True)

    # -- the beats --------------------------------------------------------

    def _clip_beats(self, asset: str) -> list[float]:
        """A clip's note-on beats, from 0 at its measure's start."""
        return [e.beat
                for e in read_midi_file(self.config.asset_path(asset)).events
                if e.status & 0xF0 == 0x90 and e.data2 > 0]

    def _choose_beats(self, name: str) -> None:
        """Make `name`, one of BEATS, the call: its clips in every one of
        CALL_MEASURES, answered in each of ANSWER_MEASURES. `_beats` is one cycle's
        lights, in order, (cycle beat, the responder it lights, the gestures
        it asks for), one per beat however many gestures share it; a clip's beats count from 0, so the
        whole part of one names its responder. `_answers` is one cycle's
        answer, (cycle beat, gesture), in closing order."""
        self._beats_name = name
        calls = [((measure - 1) * BEATS_PER_MEASURE + beat, gesture, beat)
                 for measure in CALL_MEASURES
                 for gesture, beats in self._clips[name].items()
                 for beat in beats]
        lights: dict[tuple[float, int], set[str]] = {}
        for cycle_beat, gesture, beat in calls:
            lights.setdefault((cycle_beat, int(beat)), set()).add(gesture)
        self._beats = sorted((cycle_beat, r, tuple(sorted(gestures)))
                             for (cycle_beat, r), gestures in lights.items())
        self._answers = sorted(
            ((measure - 1) * BEATS_PER_MEASURE + beat, gesture)
            for measure in ANSWER_MEASURES
            for gesture, beats in self._clips[name].items()
            for beat in beats)
        self._answers.sort(key=lambda a: (a[0], GESTURES.index(a[1])))

    def _light_delay(self) -> float:
        """Seconds a responder's light is held behind its beat: calibration's
        saved mean, else LIGHT_DELAY_S."""
        return self.carried.get(MEAN_TAP_DELAY_S, LIGHT_DELAY_S)

    def _strike_due_beats(self, at: float) -> None:
        """Start the fade of every beat's responder whose light, the light
        delay behind the beat, is reached by `at`."""
        while self._beats:
            passes, i = divmod(self._beats_done, len(self._beats))
            loop_beat, responder, gestures = self._beats[i]
            beat_at = (self._seq.time_at(passes * CYCLE_BEATS + loop_beat)
                       + self._light_delay())
            if beat_at > at:
                return
            for gesture in gestures:
                self._responders[(responder, gesture)] = beat_at
            self._beats_done += 1

    def _responder_steps(self, at: float) -> list:
        """Each lit responder at its colour `at`: the sum of its gestures'
        GESTURE_RGB, each scaled by its own fade's level, so gestures
        sharing a beat, or one beat fading under the next, mix. Sent only
        when the colour moved, and held until the last of its fades ends."""
        out = []
        towers = sorted(self._towers)
        lit: dict[int, list[tuple[str, float]]] = {}
        for (r, gesture), start in list(self._responders.items()):
            if fade_value(at - start, RESPONDER_FADE_S) == 0:
                del self._responders[(r, gesture)]
            else:
                lit.setdefault(r, []).append((gesture, at - start))
        for r in list(self._responder_sent):
            if r not in lit:
                del self._responder_sent[r]
        for r, fades in sorted(lit.items()):
            rgb = tuple(
                min(sum(GESTURE_RGB[g][ch] * fade_value(t, RESPONDER_FADE_S)
                        // 255 for g, t in fades), 255)
                for ch in range(3))
            if rgb == self._responder_sent.get(r):
                continue
            self._responder_sent[r] = rgb
            hold = RESPONDER_FADE_S - min(t for _g, t in fades)
            out.extend(PixelFlashCue(tower, px, rgb + (0,), hold, when=at)
                       for tower in towers
                       for px in responder_pixels(r))
        return out

    # -- the answer -------------------------------------------------------

    def _answer(self, index: int) -> tuple[float, str]:
        """The index-th answer beat since Start: (grid beat, gesture)."""
        passes, i = divmod(index, len(self._answers))
        beat, gesture = self._answers[i]
        return passes * CYCLE_BEATS + beat, gesture

    def _prints(self, dev: str, gesture: str) -> bool:
        """Whether `dev`'s `gesture` is printed: player1's taps, player2's
        swings and every player's squeezes."""
        if gesture == TAP:
            return self._at_seat(dev, TAP_SEAT)
        if gesture == SWING:
            return self._at_seat(dev, SWING_SEAT)
        return True

    def _judge(self, dev: str, gesture: str,
               at: float) -> tuple[str, float] | None:
        """Give `dev` the points of the nearest open answer beat for
        `gesture` it has not taken, returning (the rating, that beat's grid
        beat), or None if it took no beat. `at` is presentation time, a horizon ahead of the gesture's
        own moment, while the grid is what is presented. `beat` is that
        moment with JUDGE_DELAY_S off, so the grid can be judged as it
        stands."""
        grid_beat = self._seq.beat_at(at - self.cue_horizon)
        beat = self._seq.beat_at(at - self.cue_horizon - JUDGE_DELAY_S)
        passes = (beat + BAD_WINDOW_BEATS) // CYCLE_BEATS
        rank = GESTURES.index(gesture)
        if self._answers_closed:
            closed_beat, closed_gesture = self._answer(self._answers_closed - 1)
            closed_to = (closed_beat, GESTURES.index(closed_gesture))
        else:
            closed_to = (float("-inf"), 0)
        grace = LATE_GRACE_S * self._seq.bpm / 60.0

        def error(target: float) -> float:
            return forgive_late(beat - target, grace)

        candidates = [
            target for target in (passes * CYCLE_BEATS + a
                                  for a, g in self._answers if g == gesture)
            if (target, rank) > closed_to
            and abs(error(target)) <= BAD_WINDOW_BEATS
            and dev not in self._open_taps.get((target, gesture), {})]
        prints = self._prints(dev, gesture)
        if not candidates:
            if prints:
                measure, in_measure = measure_and_beat(grid_beat)
                print(f"{gesture} {self._tap_source(dev)}: "
                      f"at measure {measure} beat {in_measure:.3f}, "
                      f"took no beat", flush=True)
            return None
        target = min(candidates, key=lambda t: abs(error(t)))
        rating = tap_rating(error(target))
        points = tap_points(error(target))
        self._open_taps.setdefault((target, gesture), {})[dev] = points
        self._open_ratings.setdefault((target, gesture), {})[dev] = rating
        if prints:
            measure, in_measure = measure_and_beat(target)
            print(f"{gesture} {self._tap_source(dev)}: "
                  f"measure {measure} beat {in_measure:g}, "
                  f"error {beat - target:+.3f} beats, "
                  f"{rating}, points {points:.3f}", flush=True)
        if points > PROGRESS_MIN_POINTS and self._progress < PROGRESS_FULL:
            self._progress += 1
            print(f"progress {self._progress}/{PROGRESS_FULL}", flush=True)
        return rating, target

    def _close_answers(self, at: float) -> None:
        """Bank every answer beat whose BAD window, with a late gesture's
        slack, has passed by the real time behind presentation time `at`,
        rating it for every player: the rating of the gesture that took it,
        else MISS."""
        while self._answers:
            target, gesture = self._answer(self._answers_closed)
            deadline = (self._seq.time_at(target + BAD_WINDOW_BEATS)
                        + JUDGE_DELAY_S + LATE_GRACE_S + JUDGE_SLACK_S)
            if deadline > at - self.cue_horizon:
                return
            key = (target, gesture)
            for dev, points in self._open_taps.pop(key, {}).items():
                self._earned[dev] = self._earned.get(dev, 0.0) + points
            rated = self._open_ratings.pop(key, {})
            for dev in self._playing():
                rating = rated.get(dev, MISS)
                counts = self._ratings.setdefault(dev, dict.fromkeys(RATINGS, 0))
                counts[rating] += 1
                if rating == MISS and self._prints(dev, gesture):
                    measure, in_measure = measure_and_beat(target)
                    print(f"{gesture} {self._tap_source(dev)}: measure "
                          f"{measure} beat {in_measure:g}, MISS", flush=True)
            self._answers_closed += 1

    # -- the tap ----------------------------------------------------------

    def good_key(self, grid_beat: float) -> int:
        """The key GOOD_KEY_TRACK has sounding at `grid_beat`: its latest
        note-on at or before that point of its loop, wrapping round to the
        loop's last note before its first."""
        beat = grid_beat % self._good_loop
        i = bisect_right(self._good_notes, (beat + 1e-9, 128))
        return self._good_notes[i - 1][1]

    def _judged_sound(self, dev: str, gesture: str, at: float,
                      sample: str, key: int | None) -> list:
        """Judge `dev`'s `gesture` once started, and sound it on its own
        shroom: `sample` at the good_key of the beat it took if GOOD,
        BAD_TAP_KEY if BAD, else at `key` (nothing if None). The bar is
        repainted if it moved."""
        progress = self._progress
        judged = self._judge(dev, gesture, at) if self._started else None
        if judged is not None:
            rating, target = judged
            key = BAD_TAP_KEY if rating == BAD else self.good_key(target)
        out: list = [] if key is None else [PlayCue(dev, sample, f"key={key}")]
        if self._progress != progress:
            out += self._bar_cues(at)
        return out

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """Any player's tap is judged once started and plays TAP_SAMPLE on
        its own shroom (_judged_sound; TAP_KEY when not judged). player1's
        also blinks every player purple, on this tap's own time rather than
        the next tick. A tap from anything else does nothing."""
        if dev not in self._playing():
            return []
        blinks = self._at_seat(dev, TAP_SEAT)
        if blinks:
            print(f"tap {self._tap_source(dev)}: "
                  f"received at o2 t={at - self.cue_horizon:.3f} s",
                  flush=True)
        out = self._judged_sound(dev, TAP, at, TAP_SAMPLE, TAP_KEY)
        if blinks:
            self._flash = (at, 255, FLASH_RGB)
            out += self._flash_cues(255, 0.0, at, FLASH_RGB)
        return out

    def _on_swing(self, dev: str, args: list, at: float) -> list:
        """Any player's swing is judged once started and, if judged, plays
        TAP_SAMPLE on its own shroom (_judged_sound; silent when not
        judged). player2's is also printed and blinks every player yellow. A
        swing from anything else does nothing."""
        if dev not in self._playing():
            return []
        blinks = self._at_seat(dev, SWING_SEAT)
        if blinks:
            print(f"swing {self._tap_source(dev)}: "
                  f"received at o2 t={at - self.cue_horizon:.3f} s",
                  flush=True)
        out = self._judged_sound(dev, SWING, at, TAP_SAMPLE, None)
        if blinks:
            self._flash = (at, 255, SWING_FLASH_RGB)
            out += self._flash_cues(255, 0.0, at, SWING_FLASH_RGB)
        return out

    def _on_squeeze(self, dev: str, args: list, at: float) -> list:
        """Any player's squeeze is printed, judged once started, and plays
        SQUEEZE_SAMPLE on its own shroom (_judged_sound; SQUEEZE_KEY when not
        judged). A squeeze from anything else does nothing."""
        if dev not in self._playing():
            return []
        print(f"squeeze {self._tap_source(dev)}: "
              f"received at o2 t={at - self.cue_horizon:.3f} s",
              flush=True)
        return self._judged_sound(dev, SQUEEZE, at, SQUEEZE_SAMPLE,
                                  SQUEEZE_KEY)

    def _on_tilt(self, dev: str, args: list, at: float) -> list:
        """A tilt does nothing here; handled only so Control accepts it
        instead of answering every one with an "unknown verb" error. Not
        printed: it streams."""
        return []

    # -- the bar ----------------------------------------------------------

    def _bar_cues(self, at: float) -> list:
        """Every lit bar pixel on every tower at its level for the progress."""
        return [PixelFlashCue(tower, px, (0, 0, 0, value), BAR_HOLD_S, when=at)
                for tower in sorted(self._towers)
                for light in range(BAR_LIGHTS)
                if (value := bar_light_value(light, self._progress))
                for px in range(light * BAR_LIGHT_PIXELS,
                                (light + 1) * BAR_LIGHT_PIXELS)]

    def _bar_refresh(self, at: float) -> list:
        """The bar's paint renewed every BAR_REFRESH_S, before it lapses."""
        if at < self._bar_refresh_at:
            return []
        self._bar_refresh_at = at + BAR_REFRESH_S
        return self._bar_cues(at)

    def _flash_step(self, at: float) -> list:
        """The blink at its fade's level `at`, sent only when the level moved;
        the last step lapses dark at the fade's end."""
        if self._flash is None:
            return []
        start, sent, rgb = self._flash
        t = at - start
        value = fade_value(t, FLASH_FADE_S)
        if value == 0:
            self._flash = None
            return []
        if value == sent:
            return []
        self._flash = (start, value, rgb)
        return self._flash_cues(value, t, at, rgb)

    def _flash_cues(self, value: int, t: float, at: float,
                    colour: tuple) -> list:
        """Every LED of every player at `colour` scaled to `value`, held to
        the fade's end."""
        rgb = tuple(c * value // 255 for c in colour) + (0,)
        return [PixelFlashCue(dev, px, rgb, FLASH_FADE_S - t, when=at)
                for dev in sorted(self._playing())
                for px in range(FLASH_PIXELS)]

    # -- read-outs --------------------------------------------------------

    def score(self, dev: str) -> float:
        """`dev`'s mean points over the answer beats closed since Start."""
        if not self._answers_closed:
            return 0.0
        return self._earned.get(dev, 0.0) / self._answers_closed

    def ratings(self, dev: str) -> dict[str, int]:
        """`dev`'s GOOD, BAD and MISS counts over the answer beats closed
        since Start."""
        return dict(self._ratings.get(dev, dict.fromkeys(RATINGS, 0)))

    def status(self) -> dict:
        return {"music": self._music,
                "beats": self._beats_name,
                "state": "playing" if self._started else "waiting",
                "tracks": f"{self._tracks_in}/{len(MUSICS[self._music])}",
                "tower": sorted(self._towers),
                "shrooms": sorted(self._shrooms),
                "light delay": f"{self._light_delay() * 1000:+.1f} ms",
                **{f"player{n}": dev
                   for n, dev in enumerate(self._seats, start=1)
                   if dev is not None},
                "progress": f"{self._progress}/{PROGRESS_FULL}",
                **{f"score {dev}": round(self.score(dev), 3)
                   for dev in sorted(self._playing())},
                **{f"rating {dev}": " ".join(
                       f"{r} {n}" for r, n in self.ratings(dev).items())
                   for dev in sorted(self._playing())}}
