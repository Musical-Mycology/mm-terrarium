"""The lobby: what the Terrarium does in SETUP while it waits for an
admin start (spec docs/superpowers/specs/2026-09-11-metronome-lobby-and-
admin-start-design.md, sections 2 and 5).

Pure stdlib. Holds the lobby state (WAITING / FULL), the start rule, the
note scale, and the three small schedulers the device-link runtime drives.
Nothing here touches a session, a voice, or the wire; devicelink/
lobby_runtime.py does that through injected sinks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

# The Terrarium's own admin identity: always in the effective admin set,
# never removable by config, and refused as a hello'd dev id.
TERRARIUM_ADMIN = "terrarium"

# A major from A4, wrapping every seven joins (spec section 2).
NOTE_SCALE = (69, 71, 73, 74, 76, 78, 80)

# Lobby sound: the warm pad drone (General MIDI 89, the program TestBit's
# Room already uses) and the bell on a transient voice (GM 14, tubular
# bells).
LOBBY_PROGRAM = 89
LOBBY_DRONE_KEY = 48
LOBBY_DRONE_VEL = 80
BELL_PROGRAM = 14
BELL_VEL = 100

# Join ceremony offsets from the ceremony's start time `at` (spec 4).
DEVICE_FLASH_ON_S = 0.2
DEVICE_FLASH_GAP_S = 0.2
BELL_OFFSET_S = 0.8
BELL_DURATION_S = 1.0
CHIME_OFFSET_S = 1.8
CEREMONY_SPAN_S = CHIME_OFFSET_S

# Fixture feedback flashes (accept / refusal) and invites.
FIXTURE_FLASH_ON_S = 0.25
FIXTURE_FLASH_GAP_S = 0.25

HUE_CC = 74
GREEN_HUE_CC = 42            # aurora hue ~0.33
HUE_DRIFT_PERIOD_S = 20.0

GREEN = (0, 255, 0)
RED = (255, 0, 0)
WHITE = (255, 255, 255)

FEEDBACK_NONE = "none"
FEEDBACK_ACCEPT = "accept"
FEEDBACK_MINIMUM = "minimum"
FEEDBACK_REFUSED = "refused"


@dataclass(frozen=True)
class LobbyConfig:
    enabled: bool = True
    invite_interval_s: float = 5.0
    ceremony_gap_s: float = 1.0
    double_tap_window_s: float = 1.5


DEFAULT_LOBBY = LobbyConfig()


class LobbyState(Enum):
    WAITING = auto()
    FULL = auto()


def lobby_state(counts, role_table) -> LobbyState:
    """FULL when every scored role with a finite capacity is at capacity
    and at least one such role exists; an uncapped scored role means the
    lobby can never fill. `counts` is RegistrationState.counts()."""
    capped = False
    for name, count, capacity in counts:
        role = role_table.roles.get(name)
        if role is None or not role.scored:
            continue
        if capacity is None:
            return LobbyState.WAITING
        capped = True
        if count < capacity:
            return LobbyState.WAITING
    return LobbyState.FULL if capped else LobbyState.WAITING


def scale_note(join_index: int) -> int:
    return NOTE_SCALE[join_index % len(NOTE_SCALE)]


def hue_drift_cc(t: float, period: float = HUE_DRIFT_PERIOD_S) -> int:
    """A slow triangle over cc range, so the aurora's hue sweeps the wheel
    and back once per period."""
    phase = (t % period) / period
    tri = 2.0 * phase if phase < 0.5 else 2.0 * (1.0 - phase)
    return round(tri * 127)


def lobby_light_manifest() -> dict:
    """What every fixture renders while waiting: an aurora whose hue rides
    cc:74 (the drift) and whose level rides cc:11 (the breath). Declaring
    `level` opts aurora out of its private breathing clock, so light and
    sound read one number."""
    return {
        "instruments": [
            {"instrument": "aurora", "target": "primary",
             "params": {"hue": 0.55, "level": 0.55},
             "lanes": [{"source": "cc:74", "dest": "hue"},
                       {"source": "cc:11", "dest": "level"}]},
        ],
    }


@dataclass(frozen=True)
class StartDecision:
    accepted: bool
    reason: str | None
    feedback: str


def decide_start(*, bit_loaded: bool, in_setup: bool, when: str | None,
                 expected_key: str | None, key: str | None, admin: bool,
                 scored: int, min_scored: int) -> StartDecision:
    """The start rule (spec section 2). `key is None` means an unkeyed
    operator surface (Console, uplink); a keyed source is judged against
    the Bit's key before anything else so a stranger with an old poster
    gets no room reaction at all."""
    if not bit_loaded:
        return StartDecision(False, "no Bit loaded", FEEDBACK_NONE)
    keyed = key is not None
    if keyed:
        if when != "admin":
            return StartDecision(False, "Bit does not take an admin start",
                                 FEEDBACK_NONE)
        if not expected_key or key != expected_key:
            return StartDecision(False, "bad key", FEEDBACK_NONE)
    if not in_setup:
        return StartDecision(False, "not in SETUP",
                             FEEDBACK_REFUSED if keyed else FEEDBACK_NONE)
    if admin:
        return StartDecision(True, None, FEEDBACK_ACCEPT)
    if min_scored > 0 and scored < min_scored:
        return StartDecision(False, "minimum not met", FEEDBACK_MINIMUM)
    return StartDecision(True, None, FEEDBACK_ACCEPT)


@dataclass(frozen=True)
class StartRequested:
    """The engine observer record for every start attempt."""
    source: str
    source_dev: str | None
    accepted: bool
    reason: str | None
    feedback: str


@dataclass(frozen=True)
class StartRequest:
    """One queued web start (harness/www_server.py -> DeviceLinkAgent)."""
    key: str
    dev: str | None
    source: str


class DoubleTapDetector:
    """Two taps from one device within the window, or one tap carrying
    count >= 2, is a double tap."""

    def __init__(self, window_s: float) -> None:
        self._window = window_s
        self._last: dict[str, float] = {}

    def observe(self, dev: str, count: int, stamp: float) -> bool:
        if count >= 2:
            self._last.pop(dev, None)
            return True
        prev = self._last.get(dev)
        self._last[dev] = stamp
        if prev is not None and 0.0 <= stamp - prev <= self._window:
            self._last.pop(dev, None)
            return True
        return False

    def forget(self, dev: str) -> None:
        self._last.pop(dev, None)

    def clear(self) -> None:
        self._last.clear()


class InviteSchedule:
    """When to (re)flash an invite: now on first sight, then every
    interval while the device stays un-joined."""

    def __init__(self, interval_s: float) -> None:
        self._interval = interval_s
        self._last: dict[str, float] = {}

    def due(self, dev: str, now: float) -> bool:
        last = self._last.get(dev)
        if last is None or now - last >= self._interval:
            self._last[dev] = now
            return True
        return False

    def invited(self, dev: str) -> bool:
        return dev in self._last

    def forget(self, dev: str) -> None:
        self._last.pop(dev, None)

    def clear(self) -> None:
        self._last.clear()


class CeremonySlots:
    """Serialises join ceremonies: each reserve() returns the start time
    of the next one, span plus gap after the previous."""

    def __init__(self, span_s: float, gap_s: float) -> None:
        self._span = span_s
        self._gap = gap_s
        self._next_free = 0.0

    def reserve(self, now: float) -> float:
        at = max(now, self._next_free)
        self._next_free = at + self._span + self._gap
        return at
