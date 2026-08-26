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
from control.roles import RoleClass
from control.room_profile import RoomProfile, room_profile
from control.rooms import room_role_name
from control.state import State
from control.timed_queue import TimedQueue
from devicelink import protocol
from harness.device_bridge import DeviceBridge
from harness.room_surface import to_capability
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
                 room_bridge=None, room_audio=None, horizon: float = 0.0,
                 room_profile=None, on_room_frame=None, on_join_denied=None,
                 stale_timeout: float = 15.0):
        self.game_server = game_server
        self.server = server
        self._capability = capability
        self._clock = clock
        # BootConfig.cue_horizon. Used two ways here. A frame with no cue
        # behind it (breath only) is a STREAM frame whose origin is this
        # tick, so it is stamped clock() + horizon. And a cue's session feed
        # is deferred to at - horizon when that is still in the future, so a
        # far-future state cannot leak into an intervening breath frame.
        self._horizon = horizon
        # Control-side reap threshold: a device silent this many seconds
        # is removed by GameServer.reap_stale(), called from poll() below.
        # See docs/superpowers/specs/
        # 2026-08-25-device-liveness-detection-design.md.
        self._stale_timeout = stale_timeout
        self._room_cues = TimedQueue()
        # Deferred light-session feeds: (dev, status, d1, d2, at). ONLY a
        # Bit-declared cue further out than one horizon lands here. A gesture
        # cue's feed time (at - horizon) is the gesture time itself, already
        # past by the time Control sees it, so it is applied directly in
        # _on_light_cue and never queued -- queueing it would count a clamp on
        # every single gesture and destroy the counter's meaning.
        self._light_cues = TimedQueue()
        # dev -> the time the NEXT frame emitted for that dev must be
        # displayed, set when a cue is actually applied to its session. See
        # _feed_light_now for the earliest-wins rule and _render_frames for
        # why it is popped on every render attempt.
        self._pending_at: dict[str, float] = {}
        self.bridges: dict[str, DeviceBridge] = {}
        self._universes: dict[str, Universe] = {}
        self._last_frames: dict[str, bytes] = {}
        # dev -> (rgb, level, expires_at-or-None): a SolidCue's outgoing-frame
        # override, applied at the two send seams (_render_frames,
        # _render_room) ahead of the changed-frame comparison. A latched mute
        # blackout is just an entry with rgb=(0,0,0), level=0.0, expires=None
        # -- see _on_mute_change. Keyed by canonical dev for the Room, same
        # as self._pending_at.
        self._overrides: dict[str, tuple[tuple[int, int, int], float,
                                         float | None]] = {}
        # devs currently latched mute-blackout. Checked by _feed_breath (skip
        # feeding cc:11) and _on_light_cue (drop the cue) -- transport-seam
        # suppression; PlayCue is already suppressed engine-side via
        # GameServer.muted.
        self._muted: set[str] = set()
        # dev -> the URL of that device's own browser canvas, reported by
        # /game/canvas (simulators only; hardware and phones never send
        # it). No persistence: an ephemeral port is stale the moment its
        # process dies. Read by the Console through canvas_urls().
        self._canvas_urls: dict[str, str] = {}
        # dev -> render-attempt count, for devices released but still being
        # driven through their closing fade. Presence in this dict, not in
        # self.bridges, is the source of truth for "closing" (a device can
        # legitimately have no bridge at all -- see _on_release).
        self._closing: dict[str, int] = {}
        # devs proven alive -- by ANY inbound message, not just hello, same
        # "any traffic is proof of life" rule DevicePool.stale() already
        # applies -- since their entry in self._closing was created. Only
        # ever populated for a dev currently mid-fade (set in _handle()
        # below) and always cleared by _finish_release, so it can never
        # outlive the fade it was recorded for. Exists because _on_hello has
        # no equivalent of _on_join's `self._closing.pop(dev, None)`: a
        # rejoin already rebuilds self.bridges[dev] from scratch, so a
        # stale fade's later _finish_release finds no matching entry in
        # self._closing to act on and is a no-op for that dev, but a
        # hello-only reconnect (a bare heartbeat resend, or a genuine
        # reconnect that never rejoins -- exactly how the Room simulator
        # behaves) only rebinds the transport connection and leaves the
        # stale fade running untouched. Without this, _finish_release would
        # later call self.server.drop_dev(dev) unconditionally and sever
        # the FRESH connection it just rebound, not the stale one.
        self._closing_revived: set[str] = set()
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
        # The Room's fixture declaration. None here means "resolve it from the
        # bound Room's type"; an explicit profile is for tests and for a future
        # installation that overrides the shipped shape. self._capability is
        # NOT consulted for the Room any more: that attribute is the player
        # device shape, and sharing one capability between a Tuneshroom and a
        # Room is exactly the confusion this slice removes.
        self._room_profile: RoomProfile | None = room_profile
        self._room_light = None
        # Display-only copy of each changed Room frame, for the Terrarium
        # Console. Optional and best-effort: None is the default, so a run
        # without a console constructs and behaves exactly as before.
        #
        # Boundary rule 2 permits this. The rule forbids the console carrying
        # per-device join/tick traffic and requires that gameplay correctness
        # never depend on the link's health. Nothing here is retransmitted,
        # nothing is awaited, and dropping every frame degrades the picture
        # and changes nothing else.
        self._on_room_frame = on_room_frame
        self._on_join_denied = on_join_denied
        self._setup_room()
        game_server.add_observer(self)
        game_server.on_release = self._on_release
        game_server.on_light_cue = self._on_light_cue
        game_server.on_play_cue = self._on_play_cue
        game_server.on_solid_cue = self._on_solid_cue
        game_server.on_mute_change = self._on_mute_change

    def _setup_room(self) -> None:
        """Build the Room's real LightSession over the WHOLE concatenated
        profile (every fixture, bound or not -- see design spec section 2)
        and, if room_audio was injected, wire its Arco voice. The
        declare-then-compose pattern every per-role device already uses,
        just without a JoinResult (there is no join for this path).

        Construction happens eagerly here, at agent-construction time, and
        _render_room() below is what scopes SENDING to whichever fixtures
        are actually bound at the moment -- it reads self.room.bound fresh
        on every render, so a fixture bound after construction (a late
        admin tap) is picked up on its next tick with no rebuild."""
        gs = self.game_server
        room = gs.room
        if room is None or gs.bit is None:
            return
        role = gs.bit.role_table.roles.get(room_role_name(room.room_type))
        if role is None:
            return
        blob = compose_role_config(gs.bit_name, gs.bit.version, role)
        manifest = LightManifest.from_dict(blob["light_manifest"])
        if self._room_profile is None:
            self._room_profile = room_profile(room.room_type)
        cap = to_capability(self._room_profile)
        session = build_session(manifest, cap, clock=self._clock)
        self._room_light = _RoomLightSink(
            session, Universe(channel_count=self._room_profile.channel_count))
        audio_sink = None
        canonical = self._canonical_room_dev()
        if self._room_audio is not None and canonical is not None:
            self._room_audio.on_grant(canonical, role)
            audio_sink = _RoomAudioSink(self._room_audio, canonical)
        if self._room_bridge is not None:
            self._room_bridge.bind(canonical, light=self._room_light,
                                   audio=audio_sink)

    def _canonical_room_dev(self) -> str | None:
        """The Room's one dev for MIDI-feed/audio-grant purposes: the
        first bound fixture in the profile's declaration order. Mirrors
        GameServer._canonical_room_dev's algorithm as a self-contained
        copy rather than reaching into the engine's method across the
        module boundary -- this agent already holds everything the walk
        needs (self._room_profile, self.game_server.room.bound). Every
        caller of this method must get the SAME answer for the SAME
        state, the same way every Room-dev decision in the engine goes
        through its own single canonical-dev method -- see design spec
        section 5's 'frame fan-out is the only per-fixture step'."""
        gs = self.game_server
        if gs.room is None or not gs.room.bound or self._room_profile is None:
            return None
        for fixture in self._room_profile.fixtures:
            dev = gs.room.bound.get(fixture.name)
            if dev is not None:
                return dev
        return None

    @property
    def room_bridge(self):
        """The Room's MIDI fan-out, or None when no Room is configured.

        Public because harness/terrarium_boot.py's main() needs it to build a
        ConsoleAgent: build() does not return it, and build()'s 5-tuple return
        is unpacked at 16 sites, so widening it would be churn for no gain.
        """
        return self._room_bridge

    @property
    def clamped(self) -> int:
        """Room AUDIO cues that arrived already past their target time.

        A rising count means BootConfig.cue_horizon is smaller than the
        upstream delivery time (gesture to Control). The downstream half is
        reported by the device's own counter, harness/shroom_client.py's
        ShroomClient.clamped, which rises when the horizon is smaller than
        the whole round trip. Both are dev-box figures.
        """
        return self._room_cues.clamped

    @property
    def closing(self) -> int:
        """Devices currently draining their release fade (see _on_release
        and _finish_release). A driver's serve loop should keep polling
        while this is nonzero -- release is asynchronous, so a device isn't
        actually gone until its closing fade finishes and /<dev>/release
        goes out."""
        return len(self._closing)

    # --- driven once per tick-loop iteration -------------------------------
    def poll(self) -> None:
        self.server.drain_new_clients()      # devices are anonymous until hello
        for client, msg in self.server.drain_inbound():
            try:
                self._handle(client, msg)
            except Exception:
                logger.exception("devicelink inbound handling failed; "
                                 "dropping frame")
        self.game_server.reap_stale(self._stale_timeout)
        self._tick_overrides()
        self._feed_breath()
        # Before both renders: a feed released this tick must be reflected in
        # the frame rendered this tick, not the next one. Draining after would
        # delay every cue by one frame, exactly the class of error this
        # design exists to remove.
        self._drain_light_cues()
        self._render_frames()
        self._render_room()
        self._tick_audio()

    def _tick_overrides(self) -> None:
        """Drop any solid override whose duration has elapsed, before this
        tick's renders run -- so the very next render for `dev` falls back to
        its session frame instead of the stale override. `_last_frames.pop`
        forces a resend even if the session's own frame happens to be
        unchanged from before the override started. A Room override is keyed
        by the canonical dev but was fanned out per-fixture on the wire, so
        its expiry has to clear every bound fixture dev's cache entry too, or
        only the canonical dev's own slice would re-send."""
        now = self._clock()
        for dev, (_rgb, _lvl, expires) in list(self._overrides.items()):
            if expires is None or now < expires:
                continue
            del self._overrides[dev]
            self._last_frames.pop(dev, None)
            if dev == self._canonical_room_dev():
                gs = self.game_server
                bound = gs.room.bound if gs.room is not None else {}
                for fixture_dev in bound.values():
                    self._last_frames.pop(fixture_dev, None)

    def _apply_override(self, dev: str, frame: bytes) -> bytes:
        entry = self._overrides.get(dev)
        if entry is None:
            return frame
        rgb, level, _expires = entry
        pixel = bytes(max(0, min(255, round(ch * level))) for ch in rgb)
        reps = len(frame) // 3 + 1
        return (pixel * reps)[:len(frame)]

    def _on_solid_cue(self, dev: str, rgb: tuple[int, int, int],
                      level: float, duration: float | None,
                      when: float | None) -> None:
        """A Bit's SolidCue reached the engine sink. Store the override and
        force a resend this tick (see _apply_override's use at both send
        seams) so it goes out immediately, stamped with the cue's own `when`
        rather than this tick's stream-frame origin."""
        expires = None if duration is None else when + duration
        self._overrides[dev] = (rgb, level, expires)
        self._last_frames.pop(dev, None)

    def _on_mute_change(self, dev: str, muted: bool) -> None:
        """Latch (or lift) a blackout override at the transport seam. While
        muted, _feed_breath skips `dev` and _on_light_cue drops its cues --
        PlayCue suppression already happened engine-side (GameServer.muted).
        Guarded like every other engine sink here: a failing Room-audio
        silence must not propagate into the engine tick (boundary rule 2)."""
        if muted:
            self._muted.add(dev)
            self._overrides[dev] = ((0, 0, 0), 0.0, None)
            self._last_frames.pop(dev, None)
            # Drop cues already queued for this dev -- otherwise they drain
            # into the LightSession under the blackout override while
            # muted, and un-mute reveals stale mid-script state instead of
            # the session's own idle/breath frame. _drain_light_cues also
            # guards on self._muted as a second line of defense, but the
            # purge here is what keeps the queue itself from growing stale.
            self._light_cues.purge(lambda payload: payload[0] == dev)
            if dev == self._canonical_room_dev() and self._room_bridge is not None:
                # The Room mutes by its canonical dev, and _room_cues holds
                # only Room audio tuples -- purge it outright rather than by
                # predicate.
                self._room_cues.purge(lambda payload: True)
                try:
                    self._room_bridge.feed_audio(0xB0, 11, 0)
                except Exception:
                    logger.exception("mute silence of Room audio failed for %s",
                                     dev)
        else:
            self._muted.discard(dev)
            self._overrides.pop(dev, None)
            self._last_frames.pop(dev, None)

    def _tick_audio(self) -> None:
        """Drive AudioBridge.tick() once per poll(): the only place the
        audio side ticks (control/audio.py's tick() docstring). Left
        uncalled, welcome-cue voices are acquired and never released
        (leaking the pool), and a real ArcoSynthPool.poll() -- which drives
        pyarco's scheduler -- never runs either.

        now=self._clock(), not AudioBridge's own default clock: this
        agent's clock is whatever harness/terrarium_boot.py's driver loop
        ticks on (time.monotonic for websocket mode, o2lite.time_get for
        o2lite), and that is the time base every other per-tick concern
        here (_feed_breath, _render_frames, _render_room) already reads.
        Passing it explicitly keeps a welcome cue's expiry check on that
        same time base regardless of which clock room_audio itself
        happened to be constructed with -- the frame-timing bug this
        mirrors (see harness/terrarium_boot.py's build() clock= docstring)
        was exactly two clocks disagreeing on what 'now' means."""
        if self._room_audio is None:
            return
        try:
            self._room_audio.tick(now=self._clock())
        except Exception:
            logger.exception("room audio tick failed")

    def _render_room(self) -> None:
        if self._room_light is None or self._room_profile is None:
            return
        gs = self.game_server
        bound = gs.room.bound if gs.room is not None else {}
        if not bound:
            return
        canonical = self._canonical_room_dev()
        # Room AUDIO waits here for its moment. Room LIGHT was already fed in
        # _on_light_cue (or _drain_light_cues), because the frame it renders
        # still has to cross the wire to reach the simulator by `at`. One
        # anchor, two releases -- see the 2026-08-14 spec section 2.
        for (status, d1, d2) in self._room_cues.due(self._clock()):
            try:
                self._room_bridge.feed_audio(status, d1, d2)
            except Exception:
                logger.exception("Room feed_audio failed")
        # Popped unconditionally, for the same reason _render_frames does it:
        # a cue that changes no frame must not leave a stale time behind.
        # Keyed by the canonical dev: every bound fixture's slice shares one
        # `at`, since they all come from the same single render.
        at = self._pending_at.pop(canonical, None)
        universe = self._room_light.universe
        try:
            self._room_light.session.render_into(universe)
        except Exception:
            logger.exception("Room render failed; skipping frame")
            return
        frame = bytes(universe.get_frame()[:self._room_profile.channel_count])
        frame = self._apply_override(canonical, frame)
        when = at if at is not None else self._clock() + self._horizon
        for name, start, count in self._room_profile.fixture_slices():
            dev = bound.get(name)
            if dev is None:
                continue   # this fixture is not bound yet -- send to the rest
            slice_ = frame[start:start + count]
            if slice_ != self._last_frames.get(dev):
                self._last_frames[dev] = slice_
                self._emit_room_frame(dev, slice_)
                try:
                    self._send(dev, protocol.leds_event(dev, slice_, when=when))
                except Exception:
                    logger.exception("Room leds send failed for %s", dev)

    def _notify_join_denied(self, dev: str, node: str, reason: str) -> None:
        """Guarded exactly like on_release and on_light_cue already are: a
        failing console/harness sink must not stop the deny reply itself
        (already sent by the caller, above) or wedge the tick."""
        if self._on_join_denied is None:
            return
        try:
            self._on_join_denied(dev, node, reason)
        except Exception:
            logger.exception("join-denied sink failed for %s", dev)

    def _emit_room_frame(self, dev: str, frame: bytes) -> None:
        """Guarded exactly like on_release and on_light_cue already are: a
        failing console must not stop the Room rendering or wedge the tick."""
        if self._on_room_frame is None:
            return
        try:
            self._on_room_frame(dev, frame)
        except Exception:
            logger.exception("room frame sink failed; dropping frame")

    def _feed_breath(self) -> None:
        """Drive every joined device's breath. Sent on change only, and never
        to a device mid-release-fade."""
        value = breath_cc(self._clock() - self._breath_origin)
        for dev, bridge in list(self.bridges.items()):
            if dev in self._closing or bridge.session is None:
                continue
            if dev in self._muted:
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
            # Popped on EVERY render attempt for this dev, changed frame or
            # not. A cue can feed a session without changing the frame; if
            # the entry survived, a stale `at` would attach to some later
            # frame and manufacture a spurious clamp on the device, which
            # would corrupt the one counter the horizon measurement depends
            # on. Popping before render_into also means a raised render drops
            # the time rather than mis-stamping a future frame.
            at = self._pending_at.pop(dev, None)
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
            frame = self._apply_override(dev, frame)
            if frame != self._last_frames.get(dev):
                self._last_frames[dev] = frame
                # The cue's own time when a cue produced this frame, else
                # this stream frame's own origin. Explicit `is not None`,
                # never truthiness: 0.0 is a legal O2 time.
                when = at if at is not None else self._clock() + self._horizon
                try:
                    self._send(dev, protocol.leds_event(dev, frame, when=when))
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
        self.game_server.devices.touch(dev, self._clock())
        if dev in self._closing:
            # Proof of life on ANY inbound message (hello, join, or a plain
            # gesture) while dev's prior release fade is still draining --
            # see self._closing_revived's docstring above and
            # _finish_release below, which is what actually reads this.
            self._closing_revived.add(dev)
        if verb == "hello":
            self._on_hello(client, dev, env.args)
        elif verb == "join":
            self._on_join(client, dev, env.args)
        elif verb == "canvas":
            self._on_canvas(dev, env.args)
        else:
            self._on_verb(dev, verb, env.args, env.timestamp)

    def _on_canvas(self, dev: str, args: list) -> None:
        try:
            url = protocol.parse_canvas_url(args)
        except ValueError as exc:
            logger.warning("refusing canvas url from %s: %s", dev, exc)
            return
        if self._canvas_urls.get(dev) == url:
            return
        self._canvas_urls[dev] = url
        # hello's own devices_changed broadcast already went out with this
        # dev's url still null, since canvas always arrives after hello --
        # nothing else re-fires devices_changed for a non-fixture dev, so
        # Console tabs would otherwise show url null until an unrelated
        # device event. Room-fixture devs need no poke: poll()'s room diff
        # already covers them. GameServer exposes no public single-event
        # notify, only add_observer for registration, so this reaches into
        # its private _notify -- see control/engine.py's add_observer.
        self.game_server._notify("on_devices_change")

    def canvas_urls(self) -> dict:
        """A copy of the live dev -> canvas-url map, for the Console."""
        return dict(self._canvas_urls)

    def _on_hello(self, client, dev: str, args: list) -> None:
        name = args[1] if len(args) > 1 else ""
        protoversion = args[2] if len(args) > 2 else ""
        self.server.bind_dev(dev, client)
        self.game_server.hello(dev, name, protoversion)
        self._send(dev, protocol.room_event(dev, self._room_blob()))

    def _on_join(self, client, dev: str, args: list) -> None:
        if len(args) < 2:
            self._send(dev, protocol.error_event(dev, "join", "missing node"))
            return
        self.server.bind_dev(dev, client)
        result = self.game_server.join(dev, args[1])
        if not result.granted:
            self._send(dev, protocol.deny_event(dev, result.reason, result.hint))
            self._notify_join_denied(dev, args[1], result.reason)
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
        # This rejoin is itself the "proof of life" that made _handle() add
        # dev to _closing_revived a moment ago (dev was still in _closing
        # when that check ran, just above the pop() this same rejoin just
        # did). Nothing will ever pop it now that _closing no longer has
        # this dev -- _render_frames only calls _finish_release for a dev
        # in _closing -- so drop it explicitly rather than leave a stale
        # entry sitting around for a dev that may never be released again.
        self._closing_revived.discard(dev)
        self._send(dev, protocol.role_event(dev, result.config))

    def _on_verb(self, dev: str, verb: str, args: list,
                 gesture_time: float = 0.0) -> None:
        """`gesture_time` is the inbound envelope's timestamp: the device's
        own reading of the O2 clock at the instant of the gesture (Design
        Rule 4, timestamps at the source). It is 0.0 on the websocket
        transport, which never stamps, and GameServer falls back to its own
        clock in that case -- so the transport must pass 0.0 through rather
        than invent anything.
        """
        reason = self.game_server.data(dev, verb, args,
                                       gesture_time=gesture_time)
        if reason is not None:
            self._send(dev, protocol.error_event(dev, verb, reason))

    # --- engine-owned sinks -------------------------------------------------
    def on_state_change(self, old_state: State, new_state: State) -> None:
        """FluidSynth is silent without a note (see control/audio.py), so
        the Room's declared drone has to start once the Bit is actually
        RUNNING and stop once it's UNLOADING -- mirrors harness/led_smoke.py's
        own start_drone/on_release-adjacent handling for a player role.

        UNLOADING also drops every still-pending timed cue. A trigger's cue
        script can schedule a step past its Bit's own completion, and the
        Room's bridge persists across a Bit lifecycle by design, so without
        this the Room keeps gliding after the drone has stopped and the Bit is
        gone. Player devices are already covered, because _feed_light_now
        returns early once _finish_release has cleared the bridge. Dropped
        rather than drained: these are cues for a Bit that no longer exists.
        """
        self._broadcast_room()
        gs = self.game_server
        if new_state == State.UNLOADING:
            self._room_cues = TimedQueue()
            self._light_cues = TimedQueue()
        if self._room_audio is None or gs.room is None or not gs.room.bound:
            return
        canonical = self._canonical_room_dev()
        if new_state == State.RUNNING:
            self._room_audio.start_drone(canonical)
        elif new_state == State.UNLOADING:
            self._room_audio.stop_drone(canonical)

    def _on_release(self, dev: str) -> None:
        """Engine released dev. Kick off the closing fade -- but keep the
        device in the render maps (see _render_frames) so its bridge/session
        are still there on the next poll() to actually play the fade out and
        emit /<dev>/leds. /<dev>/release itself is deferred to
        _finish_release(), once CLOSING has actually finished.

        A device can be released with no bridge at all (e.g. its on_grant
        failed earlier -- see test_failing_on_grant_sends_error_not_role...):
        nothing to fade in that case, so release immediately -- including
        forgetting the transport's connection mapping, the same cleanup
        _finish_release does for the faded case below."""
        bridge = self.bridges.get(dev)
        if bridge is None:
            # Send BEFORE drop_dev: both transports' send() treats an
            # unbound dev as a silent no-op (see devicelink/server.py and
            # devicelink/o2_transport.py), so dropping the connection
            # mapping first would swallow this very notification.
            try:
                self._send(dev, protocol.release_event(dev))
            except Exception:
                logger.exception("release notify for %s failed", dev)
            self.server.drop_dev(dev)
            self._canvas_urls.pop(dev, None)
            return
        try:
            bridge.on_release(dev)   # -> session.clear(): enqueues the fade
        except Exception:
            logger.exception("session clear for %s failed", dev)
        self._closing[dev] = 0
        # Defensive: this fade's window is just starting, so nothing should
        # have been able to mark dev revived yet. Discarding here keeps that
        # invariant true even if some future caller re-releases a dev whose
        # prior fade's _finish_release was, for whatever reason, skipped.
        self._closing_revived.discard(dev)

    def _finish_release(self, dev: str) -> None:
        """The closing fade (or the stuck-session guard) is done: drop the
        device from every map and send /<dev>/release.

        self.server.drop_dev(dev) is skipped when dev has been proven alive
        (see self._closing_revived, set in _handle()) since THIS fade
        began: a hello-only reconnect -- a bare heartbeat resend, or a
        genuine reconnect that never rejoins -- can land while a prior
        release's fade is still draining, and _on_hello has no equivalent
        of _on_join's `self._closing.pop(dev, None)` rejoin guard. Left
        unconditional, this call would drop the FRESH connection _on_hello
        just rebound, not the stale one the fade actually belongs to.
        Everything else here still runs unconditionally, including the
        /<dev>/release send: that state (the OLD bridge/session/frame/
        breath) really did finish, and the device -- on whichever
        connection it is using right now -- really did lose its role and
        needs to know, so its own display resets instead of showing a
        session that no longer exists on Control's side."""
        revived = dev in self._closing_revived
        self._closing_revived.discard(dev)
        self.bridges.pop(dev, None)
        self._universes.pop(dev, None)
        self._last_frames.pop(dev, None)
        self._closing.pop(dev, None)
        self._last_breath.pop(dev, None)
        self._canvas_urls.pop(dev, None)
        # Send BEFORE drop_dev, same reasoning as _on_release's no-bridge
        # branch above: dropping the connection mapping first would make
        # this very send a silent no-op.
        try:
            self._send(dev, protocol.release_event(dev))
        except Exception:
            logger.exception("release notify for %s failed", dev)
        if not revived:
            self.server.drop_dev(dev)

    def _is_room_dev(self, dev: str) -> bool:
        gs = self.game_server
        return gs.room is not None and dev in gs.room.bound.values()

    def _feed_light_now(self, dev: str, status: int, d1: int, d2: int,
                        at: float | None) -> None:
        """Apply a light cue to its session and record when the frame it
        produces must be displayed.

        Earliest wins: one frame carries every cue applied in a tick, so it
        must not be late for the soonest deadline among them.
        """
        if self._is_room_dev(dev) and self._room_bridge is not None:
            try:
                self._room_bridge.feed_light(status, d1, d2)
            except Exception:
                logger.exception("Room feed_light failed")
                return
        else:
            bridge = self.bridges.get(dev)
            if bridge is None or bridge.session is None:
                return
            try:
                bridge.session.feed_midi(status, d1, d2)
            except Exception:
                logger.exception("feed_midi for %s failed", dev)
                return
        if at is None:
            return
        pending = self._pending_at.get(dev)
        if pending is None or at < pending:
            self._pending_at[dev] = at

    def _drain_light_cues(self) -> None:
        """Release deferred light-session feeds whose moment has come.

        `dev in self._muted` is a second line of defense: _on_mute_change
        already purges this dev's pending cues when the mute lands, but a
        cue that arrives (or a mute that lands) between purge and drain
        must not feed a muted session either."""
        for (dev, status, d1, d2, at) in self._light_cues.due(self._clock()):
            if dev in self._muted:
                continue
            self._feed_light_now(dev, status, d1, d2, at)

    def _on_light_cue(self, dev: str, status: int,
                      data1: int, data2: int,
                      when: float | None = None) -> None:
        """`when` is the cue's PRESENTATION time: GameServer computed it as
        origin + cue_horizon, once, for whatever produced this cue.

        Two halves, one anchor. Light is fed as early as possible, because
        the frame it renders still has to cross the wire to reach the device
        by `when`; that frame is stamped `when` and the device holds it.
        Room audio waits until `when` on _room_cues, because it reaches Arco
        from here with no wire in between. See docs/superpowers/specs/
        2026-08-14-load-bearing-timed-cues-design.md section 2.

        A muted dev's light cues are dropped here, at the transport seam:
        its session must not keep advancing under a latched blackout
        override, or the override's own frame comparison would be racing a
        session state the device can never actually see.
        """
        if dev in self._muted:
            return
        now = self._clock()
        if self._is_room_dev(dev) and self._room_bridge is not None:
            self._room_cues.push(when, (status, data1, data2), now=now)
        feed_at = None if when is None else when - self._horizon
        if feed_at is not None and feed_at > now:
            # A Bit-declared cue further out than one horizon. Hold the
            # session feed too, or the future state leaks into whatever
            # breath frame renders in between.
            self._light_cues.push(feed_at, (dev, status, data1, data2, when),
                                  now=now)
            return
        self._feed_light_now(dev, status, data1, data2, when)

    def _on_play_cue(self, dev: str, name: str, params: str) -> None:
        """Forward a Bit's local-sample cue to the device. Unlike the light
        path there is no session to consult: the device owns its samples, and
        Control only names one. An unknown name is the device's business."""
        self._send(dev, protocol.play_event(dev, name, params))

    def on_registration_change(self) -> None:
        self._broadcast_room()

    # --- room snapshot (informational push) ---------------------------------
    def _room_blob(self) -> dict:
        gs = self.game_server
        nodes: list[dict] = []
        reg = gs.registration
        if reg is not None and gs.bit is not None:
            table = reg.role_table
            counts = {name: (count, cap) for name, count, cap in reg.counts()}
            for node_id, role_names in table.node_map.items():
                roles = [table.roles[n] for n in role_names if n in table.roles]
                if not roles or any(r.role_class == RoleClass.ROOM for r in roles):
                    continue
                first = roles[0]
                count, capacity = counts.get(first.name, (0, None))
                nodes.append({
                    "id": node_id,
                    "roles": [r.name for r in roles],
                    "scored": bool(first.scored),
                    "count": count,
                    "capacity": capacity,
                })
        return {
            "state": gs.state.name,
            "bit": gs.bit_name if gs.bit is not None else None,
            "version": getattr(gs.bit, "version", None) if gs.bit is not None else None,
            "nodes": nodes,
        }

    def _broadcast_room(self) -> None:
        blob = self._room_blob()
        for info in self.game_server.devices.all():
            self._send(info.dev, protocol.room_event(info.dev, blob))

    # --- outbound -----------------------------------------------------------
    def _send(self, dev: str, msg: dict) -> None:
        self.server.send(dev, msg)
