"""MinigameBit: a four-phase white build on every Tower, set from the Console.

Two kinds of device take part, told apart by their dev id:

- a player, ie<N> (PLAYER_DEV_RE), takes the `player` role. Every tap
  lights the tapping shroom purple at full, fading to dark over
  FLASH_FADE_S, on each of its FLASH_PIXELS LEDs: PixelFlashCue paint
  over its dark aurora, not the aurora's
  own level, whose glide (luxaeterna's ~0.15 s tau) would stretch the
  fade's tail to most of a second. fires() steps the fade each tick, each
  step held until the fade's end, and a tap mid-fade restarts it from
  full. Taps move nothing on the Towers.
- a Tower, devtower<N> (TOWER_DEV_RE, the instrument instruments/
  devtower.toml, 128 pixels), takes the `tower` role. It is a player
  device, not a Room fixture: it hellos like any device and gets its own
  128 px player frame at start or walk-up, no Arm and no Room node. Its
  taps do nothing.
  Every Tower shows the same phase.

The Tower's first 32 LEDs (the bar) are 16 lights of PIXELS_PER_LIGHT (2), bottom first,
in GROUPS (4) groups of LIGHTS_PER_GROUP (4) lights, so group g is pixels
8g..8g+7. In phase p (1..GROUPS) group p-1 blinks white at BPM, on for
the first half of each beat, and every group below it is held solid
white; the groups above are dark. Each blink fades in over FADE_S, holds,
and fades out over FADE_S, ending dark at the half beat. The Console's Phase 1..4 buttons
(Bit.actions) switch phase; the Bit starts in phase 1.

Its next 96 frame pixels (32..127) are the RESPONDERS (4) responder lights,
RESPONDER_PIXELS (24) each, bottom first: responder r is pixels
32 + 24r..32 + 24r + 23. They are RGB LEDs, so each takes its colour in a
frame pixel's R, G, B and leaves W at 0 (the /leds blob is R, G, B
anyway). Every beat lights the next
responder in order, 1, 2, 3, 4, 1, ..., RESPONDER_RGB (beige) at full on
the beat and fading to dark over RESPONDER_FADE_S, whatever the phase. The
fade outlasts a beat, so each responder's tail overlaps the next one's
start; every responder fades on its own.

The aurora (lexicon) has no white, so the tower role's own light is one
aurora held dark and the white is painted over it with PixelFlashCue,
the way DeuxAraDropBit flickers its Tower. Each beat sends every lit
pixel on every Tower: a blinking pixel for BLINK_ON_S, a solid one for
HOLD_S, a little over a beat, so the next beat's refresh lands before it
lapses. The blink's fade is stepped once a tick by fires(): a new W level
for the group whenever it moves, each held until the blink's end, so the
last step lapses dark on time. The responder's fade is stepped the same
way, each level held until its own fade's end. A Tower that joins late is picked up on the next beat. The
beat grid runs unbroken from the first tick for the whole session: a phase
switch never restarts it. The switch repaints at once instead: the groups
below the new phase solid, the blinking group at the current blink's level
(dark between blinks), and the groups above black for CLEAR_S, so nothing
waits for an old hold to lapse.

Any other device falls through to its own solo role, untouched by the game.
No scoring, and the Bit needs nothing from the Room.
"""

from __future__ import annotations

import re

from control.bit import Bit
from control.cues import PixelFlashCue
from control.functions import FunctionTable
from control.roles import Role, RoleClass, RoleTable

MINIGAME_PLAYER_NODE = "MINIGAME_PLAYER_NODE"
MINIGAME_TOWER_NODE = "MINIGAME_TOWER_NODE"

# The devices that play: ie1, ie2, ...
PLAYER_DEV_RE = re.compile(r"ie\d+")
# The devices that show the phases: devtower1, devtower2, ...
TOWER_DEV_RE = re.compile(r"devtower\d+")

BAR_PIXELS = 32                 # the Dev Tower's bar LEDs, pixels 0..31
PIXELS_PER_LIGHT = 2            # LEDs that light up as one
LIGHTS_PER_GROUP = 4
GROUPS = 4                      # one per phase
GROUP_PIXELS = LIGHTS_PER_GROUP * PIXELS_PER_LIGHT   # 8
RESPONDERS = 4                  # RGB responder lights, lit in order each beat
RESPONDER_PIXELS = 24           # LEDs a responder light
RESPONDER_FADE_S = 0.5          # a responder's fade, full to dark
RESPONDER_RGB = (255, 214, 160)  # beige, warm enough to read as not white
TOWER_PIXELS = BAR_PIXELS + RESPONDERS * RESPONDER_PIXELS   # 128

BPM = 130.0
BEAT_S = 60.0 / BPM
BLINK_ON_S = BEAT_S / 2         # the blinking group is lit half of each beat
FADE_S = 0.06                   # each blink's fade in, and its fade out
HOLD_S = BEAT_S + 0.1           # a solid pixel outlasts the gap to its refresh
CLEAR_S = 0.05                  # the black paint a phase switch clears with
WHITE = (0, 0, 0, 255)          # the lexicon's white, on the W channel
BLACK = (0, 0, 0, 0)

# A tapped shroom's own flash: purple on every LED, full on the tap,
# fading to dark.
FLASH_HUE = 0.75               # purple
FLASH_RGB = (128, 0, 255)      # FLASH_HUE at full
FLASH_FADE_S = 0.2             # full to dark
FLASH_PIXELS = 4               # the devshroom's LED_COUNT (mm-devshroom)


def group_pixels(g: int) -> range:
    """Group g's pixels, bottom first: 8g..8g+7."""
    return range(g * GROUP_PIXELS, (g + 1) * GROUP_PIXELS)


def responder_pixels(r: int) -> range:
    """Responder r's pixels, after the bar: 32 + 24r..32 + 24r + 23."""
    start = BAR_PIXELS + r * RESPONDER_PIXELS
    return range(start, start + RESPONDER_PIXELS)


def blink_value(t: float) -> int:
    """The blink's W level, 0..255, `t` seconds into it: up over FADE_S,
    full, and down over FADE_S to 0 at BLINK_ON_S."""
    level = min(t / FADE_S, (BLINK_ON_S - t) / FADE_S, 1.0)
    return round(max(level, 0.0) * 255)


def responder_value(t: float) -> int:
    """The responder's level, 255..0, `t` seconds after its beat."""
    return round(min(max(1.0 - t / RESPONDER_FADE_S, 0.0), 1.0) * 255)


def flash_value(elapsed: float) -> int:
    """The flash's level, 255..0, `elapsed` seconds after its tap."""
    return round(min(max(1.0 - elapsed / FLASH_FADE_S, 0.0), 1.0) * 255)


def flash_cues(dev: str, value: int, elapsed: float, at: float) -> list:
    """Every LED of `dev` at flash level `value`, held to the fade's end."""
    rgb = tuple(c * value // 255 for c in FLASH_RGB) + (0,)
    return [PixelFlashCue(dev, px, rgb, FLASH_FADE_S - elapsed, when=at)
            for px in range(FLASH_PIXELS)]


def is_tower(dev: str) -> bool:
    return TOWER_DEV_RE.fullmatch(dev) is not None


def is_player(dev: str) -> bool:
    return PLAYER_DEV_RE.fullmatch(dev) is not None


class MinigameBit(Bit):
    version = "0.9"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self.phase = 1
        self._players: set[str] = set()
        self._towers: set[str] = set()
        # Shrooms mid-flash: dev -> (tap time, last cc value sent).
        self._flashes: dict[str, tuple[float, int]] = {}
        # When the next beat's Tower cues go out; None until the first tick.
        self._next_beat: float | None = None
        # The blink under way: its start, and the last W level sent.
        self._blink_start: float | None = None
        self._blink_sent = 0
        # Beats count the responders 0..3; those mid-fade: r -> (its beat,
        # last level sent).
        self._beats = 0
        self._responders: dict[int, tuple[float, int]] = {}

    @property
    def role_table(self) -> RoleTable:
        player = Role(
            name="player",
            role_class=RoleClass.JAM,      # every ie<N> device, see admits()
            capacity=None,
            scored=False,
            uses=["tap"],
            breath=False,                  # dark between flashes
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": FLASH_HUE, "level": 0.0}, "lanes": []},
            ]},
        )
        tower = Role(
            name="tower",
            role_class=RoleClass.JAM,      # every devtower<N> device
            capacity=None,
            scored=False,
            breath=False,                  # dark under the white paint
            light_manifest={"instruments": [
                {"instrument": "aurora", "target": "primary",
                 "params": {"hue": 0.0, "level": 0.0}, "lanes": []},
            ]},
        )
        return RoleTable(roles={"player": player, "tower": tower},
                         node_map={MINIGAME_PLAYER_NODE: ["player"],
                                   MINIGAME_TOWER_NODE: ["tower"]})

    def instrument_requirements(self) -> tuple:
        return ()

    @property
    def function_table(self) -> FunctionTable:
        return FunctionTable(functions={})

    def verb_handlers(self) -> dict:
        return {"tap": self._on_tap}

    def actions(self) -> dict:
        return {f"phase{p}": (f"Phase {p}",
                              lambda at, p=p: self._on_phase(p, at))
                for p in range(1, GROUPS + 1)}

    def on_setup_enter(self) -> None:
        pass

    def on_run_start(self) -> None:
        pass

    def admits(self, dev: str, role_name: str) -> bool:
        if role_name == "player":
            return is_player(dev)
        if role_name == "tower":
            return is_tower(dev)
        return True

    def on_join(self, dev: str, role_name: str) -> None:
        self._players.discard(dev)
        self._towers.discard(dev)
        self._flashes.pop(dev, None)
        if role_name == "player":
            self._players.add(dev)
        elif role_name == "tower":
            self._towers.add(dev)

    def update(self, dt: float) -> bool:
        return False   # this Bit never auto-completes; unload it from the Console

    def fires(self, at: float) -> list:
        """One step of every shroom's fade (its pixels repainted when the
        level moved; the last step lapses dark at the fade's end), on each beat the Towers' cues, and
        one step each of the blink's and every responder's fades."""
        out = []
        for dev, (start, sent) in list(self._flashes.items()):
            value = flash_value(at - start)
            if 0 < value != sent:
                out.extend(flash_cues(dev, value, at - start, at))
            if value == 0:
                del self._flashes[dev]
            else:
                self._flashes[dev] = (start, value)
        if self._next_beat is None:
            self._next_beat = at
        if at >= self._next_beat:
            # A beat more than one late (a stalled tick) restarts the grid
            # at `at` rather than firing a burst of missed beats.
            beat = self._next_beat if at - self._next_beat < BEAT_S else at
            out.extend(self._beat_cues(beat))
            self._next_beat = beat + BEAT_S
        out.extend(self._blink_step(at))
        out.extend(self._responder_steps(at))
        return out

    def on_complete(self) -> None:
        pass

    def result(self) -> dict | None:
        return None

    def status(self) -> dict:
        return {"phase": self.phase, "players": sorted(self._players),
                "towers": sorted(self._towers)}

    def on_unload(self) -> None:
        pass

    def _beat_cues(self, at: float) -> list:
        """One beat on every Tower: the groups below the phase's held solid,
        and the blink started on the phase's own group and the fade on the
        next responder in order (fires() steps both)."""
        self._blink_start = at
        self._blink_sent = 0
        self._responders[self._beats % RESPONDERS] = (at, 0)
        self._beats += 1
        return [PixelFlashCue(tower, px, WHITE, HOLD_S, when=at)
                for tower in sorted(self._towers)
                for g in range(self.phase - 1)
                for px in group_pixels(g)]

    def _blink_step(self, at: float) -> list:
        """The blinking group (W) at the fade's level `at`, sent only when
        the level moved, and held until the blink's end."""
        if self._blink_start is None:
            return []
        t = at - self._blink_start
        if t >= BLINK_ON_S:
            self._blink_start = None
            return []
        value = blink_value(t)
        if value == self._blink_sent:
            return []
        self._blink_sent = value
        return [PixelFlashCue(tower, px, (0, 0, 0, value), BLINK_ON_S - t,
                              when=at)
                for tower in sorted(self._towers)
                for px in group_pixels(self.phase - 1)]

    def _responder_steps(self, at: float) -> list:
        """Each fading responder (R, G, B) at its fade's level `at`, sent
        only when the level moved, and held until that fade's end."""
        out = []
        towers = sorted(self._towers)
        for r, (start, sent) in list(self._responders.items()):
            t = at - start
            value = responder_value(t)
            if value == 0:
                del self._responders[r]
                continue
            if value == sent:
                continue
            self._responders[r] = (start, value)
            rgb = tuple(c * value // 255 for c in RESPONDER_RGB) + (0,)
            out.extend(PixelFlashCue(tower, px, rgb, RESPONDER_FADE_S - t,
                                     when=at)
                       for tower in towers
                       for px in responder_pixels(r))
        return out

    def _on_phase(self, phase: int, at: float) -> list:
        """The operator's Phase N button: the new phase painted at once, on
        the running beat grid (never restarted). The groups below it solid,
        its own group at the blink under way (black between blinks), and
        the groups above cleared."""
        self.phase = phase
        towers = sorted(self._towers)
        out = [PixelFlashCue(tower, px, WHITE, HOLD_S, when=at)
               for tower in towers
               for g in range(phase - 1)
               for px in group_pixels(g)]
        blinking = (self._blink_start is not None
                    and at - self._blink_start < BLINK_ON_S)
        if blinking:
            self._blink_sent = -1          # resend the level on the new group
        dark = range(phase if blinking else phase - 1, GROUPS)
        out.extend(PixelFlashCue(tower, px, BLACK, CLEAR_S, when=at)
                   for tower in towers
                   for g in dark
                   for px in group_pixels(g))
        out.extend(self._blink_step(at))
        return out

    def _on_tap(self, dev: str, args: list, at: float) -> list:
        """The tapping shroom flashes purple. A tap from a non-player does
        nothing."""
        if dev not in self._players:
            return []
        self._flashes[dev] = (at, 255)
        return flash_cues(dev, 255, 0.0, at)
