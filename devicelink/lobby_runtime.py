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
                           FEEDBACK_ACCEPT, FEEDBACK_MINIMUM,
                           FEEDBACK_REFUSED, FIXTURE_FLASH_GAP_S,
                           FIXTURE_FLASH_ON_S, GREEN, GREEN_HUE_CC, HUE_CC,
                           LOBBY_DRONE_KEY, LOBBY_DRONE_VEL, LOBBY_PROGRAM,
                           RED, WHITE, CeremonySlots, DoubleTapDetector,
                           InviteSchedule, LobbyConfig, LobbyState,
                           hue_drift_cc, scale_note)
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
    request_join: Callable[[str, object], None]
    announce: Callable[[str, str], None]


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
        self._taps = DoubleTapDetector(config.double_tap_window_s)
        self._last_light: dict[tuple[str, int], int] = {}
        self._last_audio: dict[str, int] = {}
        self._joins = 0

    # --- lifecycle -----------------------------------------------------
    @property
    def state(self) -> LobbyState:
        return self._state

    @property
    def join_count(self) -> int:
        return self._joins

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
        self._queue = TimedQueue()
        self._invites.clear()

    def set_state(self, state: LobbyState) -> None:
        if state is self._state:
            return
        self._state = state
        if state is LobbyState.FULL:
            self._drone(False)
            for name in self._s.fixture_names():
                self._light(name, HUE_CC, GREEN_HUE_CC)
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

    # --- join ceremony (spec 4) ----------------------------------------
    def on_scored_join(self, dev: str) -> None:
        key = scale_note(self._joins)
        self._joins += 1
        at = self._slots.reserve(self._clock())
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
        count, rgb = {FEEDBACK_ACCEPT: (1, GREEN), FEEDBACK_MINIMUM: (2, RED),
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

    # --- handshake (spec 5) --------------------------------------------
    def consider_invite(self, dev: str) -> None:
        if self._state is not LobbyState.WAITING:
            return
        first = not self._invites.invited(dev)
        if not self._invites.due(dev, self._clock()):
            return
        if first:
            self._s.announce("invite", dev)
        now = self._clock()
        for i in range(2):
            t = now + i * (DEVICE_FLASH_ON_S + DEVICE_FLASH_GAP_S)
            self._at(t, lambda d=dev: self._s.set_override(d, WHITE, 1.0,
                                                           DEVICE_FLASH_ON_S))

    def is_invited(self, dev: str) -> bool:
        return self._invites.invited(dev)

    def forget(self, dev: str) -> None:
        self._invites.forget(dev)
        self._taps.forget(dev)

    def observe_tap(self, dev: str, count: int, stamp: float, client) -> bool:
        if not self._invites.invited(dev):
            return False
        if not self._taps.observe(dev, count, stamp):
            return False
        self._s.announce("handshake", dev)
        self._s.request_join(dev, client)
        return True
