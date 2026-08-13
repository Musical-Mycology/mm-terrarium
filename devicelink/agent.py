"""DeviceLinkAgent: translates between the DeviceLink wire protocol and
GameServer calls, and owns one luxaeterna LightSession per joined device.

The device-facing sibling of console.ConsoleAgent -- transport-agnostic (it
talks to a server object, see devicelink/server.py), so it is fully testable
offline against an in-process fake. Driven from the engine tick loop via
poll().

Boundary rule 2: nothing in here may propagate into the engine tick.
"""

from __future__ import annotations

import logging
import time

from control.breath import BREATH_CC, breath_cc
from control.engine import GameServer
from control.role_config import compose_role_config
from control.rooms import room_role_name
from control.state import State
from control.timed_queue import TimedQueue
from devicelink import protocol
from harness.device_bridge import DeviceBridge
from luxaeterna.synth.capability import shroom_capability
from luxaeterna.synth.director import CLOSING
from luxaeterna.synth.manifest import LightManifest
from luxaeterna.synth.session import build_session
from luxaeterna.universe import Universe

logger = logging.getLogger(__name__)

# Bound on how many render attempts a released device may spend in CLOSING
# before it is dropped unconditionally. A session that never reports leaving
# CLOSING (a stuck/misbehaving bit, or a signature that never completes) must
# not wedge the device forever -- see StatusDirector.QUARANTINE_FRAMES (44
# frames, ~1s at 44Hz) for the analogous per-binding guard this mirrors. 200
# frames is generous headroom over the ~0.6s sys:closing signature.
_MAX_CLOSING_FRAMES = 200


class _RoomLightSink:
    """Satisfies control.room_bridge.RoomLightSink. `universe`/`session` are
    extra, used only by DeviceLinkAgent._render_room() -- RoomBridge itself
    only ever calls feed_midi()/clear()."""

    def __init__(self, session, universe: Universe) -> None:
        self.session = session
        self.universe = universe

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        self.session.feed_midi(status, d1, d2)

    def clear(self) -> None:
        self.session.clear()


class _RoomAudioSink:
    """Satisfies control.room_bridge.RoomAudioSink by adapting
    AudioBridge.feed_midi(dev, status, d1, d2)'s dev-keyed signature down to
    the Protocol's dev-less one -- this sink is already scoped to one dev."""

    def __init__(self, audio_bridge, dev: str) -> None:
        self._audio = audio_bridge
        self._dev = dev

    def feed_midi(self, status: int, d1: int, d2: int) -> None:
        self._audio.feed_midi(self._dev, status, d1, d2)

    def shutdown(self) -> None:
        self._audio.shutdown()


class DeviceLinkAgent:
    def __init__(self, game_server: GameServer, server,
                 capability=None, clock=time.monotonic,
                 room_bridge=None, room_audio=None, horizon: float = 0.0):
        self.game_server = game_server
        self.server = server
        self._capability = capability
        self._clock = clock
        # BootConfig.cue_horizon, passed down by whatever builds this agent
        # (harness/terrarium_boot.py). Not consulted here -- the `when` on
        # each cue already has it baked in by whoever scheduled the gesture;
        # this agent only waits for `when` to arrive. Held for Task 9's
        # driver, which needs the configured value to reach here at all.
        self._horizon = horizon
        self._room_cues = TimedQueue()
        self.bridges: dict[str, DeviceBridge] = {}
        self._universes: dict[str, Universe] = {}
        self._last_frames: dict[str, bytes] = {}
        # dev -> render-attempt count, for devices released but still being
        # driven through their closing fade. Presence in this dict, not in
        # self.bridges, is the source of truth for "closing" (a device can
        # legitimately have no bridge at all -- see _on_release).
        self._closing: dict[str, int] = {}
        # Control owns the breath now (control/breath.py): a role declaring
        # aurora's `level` param no longer breathes on its own clock, so every
        # renderer has to be fed cc:11 or it renders a static surface.
        self._breath_origin = self._clock()
        self._last_breath: dict[str, int] = {}
        # Room wiring (see design spec section 5). None of this exists
        # unless a Room is both configured and already bound -- a
        # GameServer built the pre-Room way (no room_binding/room) leaves
        # every attribute below at its default and every Room-aware method
        # below a no-op.
        self._room_bridge = room_bridge
        self._room_audio = room_audio
        self._room_dev: str | None = None
        self._room_light = None
        self._setup_room()
        game_server.add_observer(self)
        game_server.on_release = self._on_release
        game_server.on_light_cue = self._on_light_cue
        game_server.on_play_cue = self._on_play_cue

    def _setup_room(self) -> None:
        """Build the Room's real LightSession (and, if room_audio was
        injected, wire its Arco voice) from the loaded Bit's own Room
        declaration -- the same declare-then-compose pattern every per-role
        device already uses, just without a JoinResult (there is no join
        for this path; see design spec section 4).

        Construction happens eagerly here, at agent-construction time --
        not deferred until the Room device's hello arrives, as design spec
        section 5's wording ("when a connecting dev equals gs.room.bound_dev")
        might suggest. There is nothing to wait for: room.bound_dev is
        already known synchronously by the time this agent is constructed,
        so an arrival-triggered build would just add an extra state to
        track for no benefit."""
        gs = self.game_server
        room = gs.room
        if room is None or room.bound_dev is None or gs.bit is None:
            return
        role = gs.bit.role_table.roles.get(room_role_name(room.room_type))
        if role is None:
            return
        self._room_dev = room.bound_dev
        blob = compose_role_config(gs.bit_name, gs.bit.version, role)
        manifest = LightManifest.from_dict(blob["light_manifest"])
        cap = self._capability or shroom_capability()
        session = build_session(manifest, cap, clock=self._clock)
        self._room_light = _RoomLightSink(session, Universe())
        audio_sink = None
        if self._room_audio is not None:
            self._room_audio.on_grant(self._room_dev, role)
            audio_sink = _RoomAudioSink(self._room_audio, self._room_dev)
        if self._room_bridge is not None:
            self._room_bridge.bind(self._room_dev, light=self._room_light,
                                   audio=audio_sink)

    def client_for(self, dev: str):
        return self.server._devs.get(dev)

    @property
    def clamped(self) -> int:
        """Room cues that arrived already late. A rising count means
        BootConfig.cue_horizon is too small."""
        return self._room_cues.clamped

    # --- driven once per tick-loop iteration -------------------------------
    def poll(self) -> None:
        self.server.drain_new_clients()      # devices are anonymous until hello
        for client, msg in self.server.drain_inbound():
            try:
                self._handle(client, msg)
            except Exception:
                logger.exception("devicelink inbound handling failed; "
                                 "dropping frame")
        self._feed_breath()
        self._render_frames()
        self._render_room()

    def _render_room(self) -> None:
        if self._room_light is None or self._room_dev is None:
            return
        # Drain before rendering: a cue released this tick must be reflected
        # in the frame rendered this tick, not the next one -- draining
        # after the render would delay every cue by one frame, exactly the
        # class of error this slice exists to remove.
        for (status, d1, d2) in self._room_cues.due(self._clock()):
            try:
                self._room_bridge.feed_midi(status, d1, d2)
            except Exception:
                logger.exception("Room feed_midi failed")
        universe = self._room_light.universe
        try:
            self._room_light.session.render_into(universe)
        except Exception:
            logger.exception("Room render failed; skipping frame")
            return
        frame = bytes(universe.get_frame()[:36])
        if frame != self._last_frames.get(self._room_dev):
            self._last_frames[self._room_dev] = frame
            try:
                self._send(self._room_dev,
                          protocol.leds_event(self._room_dev, frame))
            except Exception:
                logger.exception("Room leds send failed")

    def _feed_breath(self) -> None:
        """Drive every joined device's breath. Sent on change only, and never
        to a device mid-release-fade."""
        value = breath_cc(self._clock() - self._breath_origin)
        for dev, bridge in list(self.bridges.items()):
            if dev in self._closing or bridge.session is None:
                continue
            if self._last_breath.get(dev) == value:
                continue
            self._last_breath[dev] = value
            try:
                bridge.session.feed_midi(0xB0, BREATH_CC, value)
            except Exception:
                logger.exception("breath feed for %s failed", dev)

    def _render_frames(self) -> None:
        """Render each joined device's session and emit /<dev>/leds when the
        frame actually changed. Rendering runs on the tick thread: the tick
        rate is the frame rate, and there is no second thread to race.

        A device that has been released (see _on_release) stays in these
        maps -- and keeps getting rendered here -- until its session's
        closing fade actually finishes; that is what makes the fade frames
        go out on the wire at all. _finish_release() below is what removes
        it and sends /<dev>/release, once CLOSING is done (or the stuck-
        session guard fires)."""
        for dev, bridge in list(self.bridges.items()):
            universe = self._universes.get(dev)
            session = bridge.session
            if universe is None or session is None:
                continue
            closing = dev in self._closing
            if closing:
                self._closing[dev] += 1
            try:
                session.render_into(universe)
            except Exception:
                logger.exception("render for %s failed; skipping frame", dev)
                if closing:
                    self._check_closing_bound(dev)
                continue
            frame = bytes(universe.get_frame()[:36])
            if frame != self._last_frames.get(dev):
                self._last_frames[dev] = frame
                try:
                    self._send(dev, protocol.leds_event(dev, frame))
                except Exception:
                    logger.exception("leds send for %s failed", dev)
            if closing:
                self._check_closing_done(dev, session)

    def _check_closing_done(self, dev: str, session) -> None:
        try:
            still_closing = session.state == CLOSING
        except Exception:
            logger.exception("state check for %s failed while closing; "
                              "releasing", dev)
            still_closing = False
        if not still_closing:
            self._finish_release(dev)
        else:
            self._check_closing_bound(dev)

    def _check_closing_bound(self, dev: str) -> None:
        if self._closing.get(dev, 0) >= _MAX_CLOSING_FRAMES:
            logger.error("session for %s stuck in CLOSING after %d render "
                         "attempts; forcing release", dev, _MAX_CLOSING_FRAMES)
            self._finish_release(dev)

    # --- inbound dispatch ---------------------------------------------------
    def _handle(self, client, msg: dict) -> None:
        try:
            env = protocol.decode(msg)
        except ValueError as exc:
            logger.warning("dropping unparseable device frame: %s", exc)
            return
        verb = protocol.parse_game_address(env.address)
        if verb is None:
            logger.warning("dropping non-/game address %r", env.address)
            return
        if not env.args or not isinstance(env.args[0], str):
            logger.warning("dropping /game/%s with no dev argument", verb)
            return
        dev = env.args[0]
        if verb == "hello":
            self._on_hello(client, dev, env.args)
        elif verb == "join":
            self._on_join(client, dev, env.args)
        else:
            self._on_verb(dev, verb, env.args)

    def _on_hello(self, client, dev: str, args: list) -> None:
        name = args[1] if len(args) > 1 else ""
        protoversion = args[2] if len(args) > 2 else ""
        self.server.bind_dev(dev, client)
        self.game_server.hello(dev, name, protoversion)

    def _on_join(self, client, dev: str, args: list) -> None:
        if len(args) < 2:
            self._send(dev, protocol.error_event(dev, "join", "missing node"))
            return
        self.server.bind_dev(dev, client)
        result = self.game_server.join(dev, args[1])
        if not result.granted:
            self._send(dev, protocol.deny_event(dev, result.reason, result.hint))
            return
        bridge = DeviceBridge(capability=self._capability, clock=self._clock)
        try:
            bridge.on_grant(result)
        except Exception:
            logger.exception("building the LightSession for %s failed", dev)
            self._send(dev, protocol.error_event(
                dev, "role", "could not build light session"))
            return
        self.bridges[dev] = bridge
        self._universes[dev] = Universe()
        self._last_frames.pop(dev, None)
        self._closing.pop(dev, None)
        # A rejoining device must not be starved of its first breath by a
        # stale entry from its previous session.
        self._last_breath.pop(dev, None)
        self._send(dev, protocol.role_event(dev, result.config))

    def _on_verb(self, dev: str, verb: str, args: list) -> None:
        reason = self.game_server.data(dev, verb, args)
        if reason is not None:
            self._send(dev, protocol.error_event(dev, verb, reason))

    # --- engine-owned sinks -------------------------------------------------
    def on_state_change(self, old_state: State, new_state: State) -> None:
        """FluidSynth is silent without a note (see control/audio.py), so
        the Room's declared drone has to start once the Bit is actually
        RUNNING and stop once it's UNLOADING -- mirrors harness/led_smoke.py's
        own start_drone/on_release-adjacent handling for a player role."""
        if self._room_audio is None or self._room_dev is None:
            return
        if new_state == State.RUNNING:
            self._room_audio.start_drone(self._room_dev)
        elif new_state == State.UNLOADING:
            self._room_audio.stop_drone(self._room_dev)

    def _on_release(self, dev: str) -> None:
        """Engine released dev. Kick off the closing fade -- but keep the
        device in the render maps (see _render_frames) so its bridge/session
        are still there on the next poll() to actually play the fade out and
        emit /<dev>/leds. /<dev>/release itself is deferred to
        _finish_release(), once CLOSING has actually finished.

        A device can be released with no bridge at all (e.g. its on_grant
        failed earlier -- see test_failing_on_grant_sends_error_not_role...):
        nothing to fade in that case, so release immediately."""
        bridge = self.bridges.get(dev)
        if bridge is None:
            try:
                self._send(dev, protocol.release_event(dev))
            except Exception:
                logger.exception("release notify for %s failed", dev)
            return
        try:
            bridge.on_release(dev)   # -> session.clear(): enqueues the fade
        except Exception:
            logger.exception("session clear for %s failed", dev)
        self._closing[dev] = 0

    def _finish_release(self, dev: str) -> None:
        """The closing fade (or the stuck-session guard) is done: drop the
        device from every map and send /<dev>/release."""
        self.bridges.pop(dev, None)
        self._universes.pop(dev, None)
        self._last_frames.pop(dev, None)
        self._closing.pop(dev, None)
        self._last_breath.pop(dev, None)
        try:
            self._send(dev, protocol.release_event(dev))
        except Exception:
            logger.exception("release notify for %s failed", dev)

    def _on_light_cue(self, dev: str, status: int,
                      data1: int, data2: int,
                      when: float | None = None) -> None:
        if dev == self._room_dev and self._room_bridge is not None:
            # Queue rather than feed: the Room's light must land on the same
            # frame as the audio scheduled for the same `when`, not the
            # instant the cue happens to arrive. Drained in _render_room().
            self._room_cues.push(when, (status, data1, data2),
                                 now=self._clock())
            return
        bridge = self.bridges.get(dev)
        if bridge is None or bridge.session is None:
            return
        try:
            bridge.session.feed_midi(status, data1, data2)
        except Exception:
            logger.exception("feed_midi for %s failed", dev)

    def _on_play_cue(self, dev: str, name: str, params: str) -> None:
        """Forward a Bit's local-sample cue to the device. Unlike the light
        path there is no session to consult: the device owns its samples, and
        Control only names one. An unknown name is the device's business."""
        self._send(dev, protocol.play_event(dev, name, params))

    # --- outbound -----------------------------------------------------------
    def _send(self, dev: str, msg: dict) -> None:
        self.server.send(dev, msg)
