"""LobbyRuntime: the lobby's light and sound, driven through sinks.

Owned by DeviceLinkAgent for the duration of one SETUP (spec sections 4
and 5). It never touches a session, a voice, or the wire directly: every
effect goes through a LobbySinks callable the agent supplies, so this
module is testable with plain lists and never imports luxaeterna.

Timing: all timed effects sit in one TimedQueue of thunks drained by
tick(); the join ceremony and the feedback flashes are pure schedules
over the spec's offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from control.breath import BREATH_CC, breath_cc
from control.lobby import (BELL_DURATION_S, BELL_OFFSET_S, BELL_PROGRAM,
                           BELL_VEL, CEREMONY_SPAN_S, CHIME_OFFSET_S,
                           DEVICE_FLASH_GAP_S, DEVICE_FLASH_ON_S,
                           DEVICE_FLASH_TRAIN_S,
                           FEEDBACK_ACCEPT, FEEDBACK_MINIMUM,
                           FEEDBACK_REFUSED, FIXTURE_FLASH_GAP_S,
                           FIXTURE_FLASH_ON_S, GREEN, GREEN_HUE_CC, HUE_CC,
                           LOBBY_DRONE_KEY, LOBBY_DRONE_VEL, LOBBY_PROGRAM,
                           RED, WHITE, CeremonySlots, InviteSchedule,
                           LobbyConfig, LobbyState, hue_drift_cc,
                           pulse_level, scale_note)
from control.timed_queue import TimedQueue


@dataclass
class LobbySinks:
    fixture_names: Callable[[], list]
    bound_dev: Callable[[str], str | None]
    feed_light: Callable[[str, int, int, int], None]
    feed_audio: Callable[[str, int, int, int], None]
    set_audio_control: Callable[[str, int, int], None]
    play_note: Callable[[int, int, int, float], None]
    set_override: Callable[[str, tuple, float, float], None]
    send_play: Callable[[str, str, str], None]
    announce: Callable[[str, str], None]
    set_base: Callable[[str, tuple | None, float], None]


@dataclass
class _Status:
    """One hello'd device's slow pulse: WHITE while invited, GREEN once
    validated. Dark until `start`, which every flash train for the device
    pushes past its own end."""
    rgb: tuple
    start: float


class LobbyRuntime:
    def __init__(self, config: LobbyConfig, sinks: LobbySinks, clock, *,
                 room_program: int | None) -> None:
        self._cfg = config
        self._s = sinks
        self._clock = clock
        self._room_program = room_program
        self._state = LobbyState.WAITING
        self._running = False
        self._origin = clock()
        self._queue = TimedQueue()
        self._slots = CeremonySlots(CEREMONY_SPAN_S, config.ceremony_gap_s)
        self._invites = InviteSchedule(config.invite_interval_s)
        self._last_light: dict[tuple[str, int], int] = {}
        self._last_audio: dict[str, int] = {}
        self._joins = 0
        self._status: dict[str, _Status] = {}
        # dev -> (rgb, level byte) last handed to set_base, so a steady
        # pulse (held dark, say) costs nothing per tick.
        self._last_base: dict[str, tuple] = {}

    # --- lifecycle -----------------------------------------------------
    @property
    def state(self) -> LobbyState:
        return self._state

    def start(self) -> None:
        self._running = True
        self._origin = self._clock()
        for name in self._s.fixture_names():
            self._s.feed_audio(name, 0xC0, LOBBY_PROGRAM, 0)
        self._drone(True)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._drone(False)
        if self._room_program is not None:
            for name in self._s.fixture_names():
                self._s.feed_audio(name, 0xC0, int(self._room_program), 0)
        # The queue is KEPT (spec 2026-10-01 section 5.5): a ceremony
        # whose validation landed just before start still plays its bell
        # and chime. tick() drains due thunks before its _running check,
        # and the agent keeps ticking a stopped runtime while draining().
        # New invites stop: the schedule is cleared and the agent no
        # longer calls consider_invite.
        self._invites.clear()
        # Device flashes go, though: a white invite or red deny flash still
        # queued would fire after the device holds its new role and paint
        # over its sys:loaded welcome. Ceremony cues and fixture feedback
        # stay.
        self._queue.purge(lambda thunk: isinstance(thunk, _DeviceFlash))
        # No pulse outlives the lobby: at RUNNING every device gets a role,
        # and an abort leaves nothing to wait for.
        for dev in list(self._status):
            self._clear_status(dev)
        # The de-dupe caches are what a restarted lobby would otherwise
        # measure its first frame against, silencing the opening breath
        # and hue feeds.
        self._last_light.clear()
        self._last_audio.clear()

    def draining(self) -> bool:
        """Stopped, but queued ceremony or feedback thunks remain."""
        return not self._running and self._queue.pending() > 0

    def set_state(self, state: LobbyState) -> None:
        if state is self._state:
            return
        self._state = state
        if state is LobbyState.FULL:
            self._drone(False)
            for name in self._s.fixture_names():
                self._light(name, HUE_CC, GREEN_HUE_CC)
            # FULL stops invites, so nobody is invited any more. A
            # validated device keeps its green Ready pulse.
            for dev in [d for d, st in self._status.items() if st.rgb == WHITE]:
                self._clear_status(dev)
        else:
            self._drone(True)

    def _drone(self, on: bool) -> None:
        for name in self._s.fixture_names():
            if on and self._state is LobbyState.WAITING:
                self._s.feed_audio(name, 0x90, LOBBY_DRONE_KEY, LOBBY_DRONE_VEL)
            elif not on:
                self._s.feed_audio(name, 0x80, LOBBY_DRONE_KEY, 0)

    # --- per tick ------------------------------------------------------
    def tick(self) -> None:
        now = self._clock()
        for thunk in self._queue.due(now):
            thunk()
        if not self._running:
            return
        for dev, st in list(self._status.items()):
            self._base(dev, st.rgb, pulse_level(now - st.start))
        t = now - self._origin
        breath = breath_cc(t)
        drift = hue_drift_cc(t)
        for name in self._s.fixture_names():
            if self._state is LobbyState.WAITING:
                self._light(name, HUE_CC, drift)
                if self._last_audio.get(name) != breath:
                    self._last_audio[name] = breath
                    self._s.set_audio_control(name, BREATH_CC, breath)
            self._light(name, BREATH_CC, breath)

    def _light(self, name: str, cc: int, value: int) -> None:
        if self._last_light.get((name, cc)) == value:
            return
        self._last_light[(name, cc)] = value
        self._s.feed_light(name, 0xB0, cc, value)

    def _at(self, when: float, thunk) -> None:
        self._queue.push(when, thunk, now=self._clock())

    # --- device status pulse (lexicon G1) ------------------------------
    def _hold(self, dev: str, rgb: tuple, until: float) -> None:
        """Give `dev` the `rgb` pulse, dark until at least `until`."""
        st = self._status.get(dev)
        if st is None or st.rgb != rgb:
            self._status[dev] = _Status(rgb, until)
        else:
            st.start = max(st.start, until)

    def _base(self, dev: str, rgb: tuple, level: float) -> None:
        key = (rgb, round(level * 255))
        if self._last_base.get(dev) == key:
            return
        self._last_base[dev] = key
        self._s.set_base(dev, rgb, level)

    def _clear_status(self, dev: str) -> None:
        self._status.pop(dev, None)
        if self._last_base.pop(dev, None) is not None:
            self._s.set_base(dev, None, 0.0)

    # --- join ceremony (spec 4) ----------------------------------------
    def on_scored_join(self, dev: str) -> None:
        key = scale_note(self._joins)
        self._joins += 1
        at = self._slots.reserve(self._clock())
        # Ready: the green pulse rises once the ceremony's flashes are done.
        self._hold(dev, GREEN, at + DEVICE_FLASH_TRAIN_S)
        for i in range(2):
            t = at + i * (DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S)
            self._at(t, lambda d=dev: self._s.set_override(d, GREEN, 1.0,
                                                           DEVICE_FLASH_ON_S))
        self._at(at + BELL_OFFSET_S,
                 lambda k=key: self._s.play_note(BELL_PROGRAM, k, BELL_VEL,
                                                 BELL_DURATION_S))
        self._at(at + CHIME_OFFSET_S,
                 lambda d=dev, k=key: self._s.send_play(d, "chime", f"key={k}"))

    # --- start feedback (spec 2) ---------------------------------------
    def feedback(self, feedback: str) -> None:
        count, rgb = {FEEDBACK_ACCEPT: (2, GREEN), FEEDBACK_MINIMUM: (2, RED),
                      FEEDBACK_REFUSED: (3, RED)}.get(feedback, (0, GREEN))
        self._flash_fixtures(rgb, count)

    def _flash_fixtures(self, rgb: tuple, count: int) -> None:
        now = self._clock()
        devs = [d for d in (self._s.bound_dev(n) for n in self._s.fixture_names())
                if d is not None]
        for i in range(count):
            t = now + i * (FIXTURE_FLASH_ON_S + FIXTURE_FLASH_GAP_S)
            for dev in devs:
                self._at(t, lambda d=dev: self._s.set_override(
                    d, rgb, 1.0, FIXTURE_FLASH_ON_S))

    # --- invite flash (spec 5) -----------------------------------------
    def consider_invite(self, dev: str) -> None:
        """The white invite flash only. /<dev>/handshake itself is the
        agent's (DeviceLinkAgent._tick_handshakes), so it goes out in SETUP
        whether or not a lobby runtime exists (spec 2026-10-01 3.1, 3.3)."""
        if not self._running or self._state is not LobbyState.WAITING:
            return
        first = not self._invites.invited(dev)
        if not self._invites.due(dev, self._clock()):
            return
        if first:
            self._s.announce("invite", dev)
        now = self._clock()
        for i in range(2):
            t = now + i * (DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S)
            self._at(t, _InviteFlash(dev, self._s))
        self._hold(dev, WHITE, now + DEVICE_FLASH_TRAIN_S)

    # --- deny flash (lexicon G2) ---------------------------------------
    def on_deny(self, dev: str) -> None:
        """Failure: red x2 on the denied device. Its still-queued white
        invite flash goes first, so white and red never interleave. A
        device still invited gets its white pulse back after the red."""
        self._purge_invite_flashes(dev)
        now = self._clock()
        for i in range(2):
            t = now + i * (DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S)
            self._at(t, _DenyFlash(dev, self._s))
        st = self._status.get(dev)
        if st is not None:
            st.start = max(st.start, now + DEVICE_FLASH_TRAIN_S)

    def is_invited(self, dev: str) -> bool:
        return self._invites.invited(dev)

    def forget(self, dev: str) -> None:
        """Stop inviting `dev`, and drop its invite flashes still queued,
        so a device that validates mid-invite sees no white flash after its
        /validated (a white frame there reads as a second invite). Only
        that dev's invite flashes go: other devs' flashes and every
        ceremony cue stay queued. Its status pulse goes too."""
        self._invites.forget(dev)
        self._purge_invite_flashes(dev)
        self._clear_status(dev)

    def _purge_invite_flashes(self, dev: str) -> None:
        self._queue.purge(
            lambda thunk: isinstance(thunk, _InviteFlash) and thunk.dev == dev)


class _DeviceFlash:
    """One queued per-device flash for `dev`: a named thunk rather than a
    lambda, so LobbyRuntime can purge exactly these from its TimedQueue
    (forget and on_deny drop a dev's invite flashes, stop drops all)."""

    __slots__ = ("dev", "_sinks")
    rgb: tuple

    def __init__(self, dev: str, sinks: LobbySinks) -> None:
        self.dev = dev
        self._sinks = sinks

    def __call__(self) -> None:
        self._sinks.set_override(self.dev, self.rgb, 1.0, DEVICE_FLASH_ON_S)


class _InviteFlash(_DeviceFlash):
    """A white invite flash."""

    __slots__ = ()
    rgb = WHITE


class _DenyFlash(_DeviceFlash):
    """A red deny flash (lexicon G2)."""

    __slots__ = ()
    rgb = RED
