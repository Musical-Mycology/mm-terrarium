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

from control.engine import GameServer
from devicelink import protocol
from harness.device_bridge import DeviceBridge

logger = logging.getLogger(__name__)


class DeviceLinkAgent:
    def __init__(self, game_server: GameServer, server,
                 capability=None, clock=time.monotonic):
        self.game_server = game_server
        self.server = server
        self._capability = capability
        self._clock = clock
        self.bridges: dict[str, DeviceBridge] = {}
        self._clients: dict[str, object] = {}     # dev -> client
        game_server.add_observer(self)
        game_server.on_release = self._on_release
        game_server.on_light_cue = self._on_light_cue

    def client_for(self, dev: str):
        return self._clients.get(dev)

    # --- driven once per tick-loop iteration -------------------------------
    def poll(self) -> None:
        self.server.drain_new_clients()      # devices are anonymous until hello
        for client, msg in self.server.drain_inbound():
            try:
                self._handle(client, msg)
            except Exception:
                logger.exception("devicelink inbound handling failed; "
                                 "dropping frame")

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
        self._clients[dev] = client
        self.game_server.hello(dev, name, protoversion)

    def _on_join(self, client, dev: str, args: list) -> None:
        if len(args) < 2:
            self._send(dev, protocol.error_event(dev, "join", "missing node"))
            return
        self._clients[dev] = client
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
        self._send(dev, protocol.role_event(dev, result.config))

    def _on_verb(self, dev: str, verb: str, args: list) -> None:
        reason = self.game_server.data(dev, verb, args)
        if reason is not None:
            self._send(dev, protocol.error_event(dev, verb, reason))

    # --- engine-owned sinks -------------------------------------------------
    def _on_release(self, dev: str) -> None:
        bridge = self.bridges.pop(dev, None)
        if bridge is not None:
            try:
                bridge.on_release(dev)
            except Exception:
                logger.exception("session clear for %s failed", dev)
        try:
            self._send(dev, protocol.release_event(dev))
        except Exception:
            logger.exception("release notify for %s failed", dev)

    def _on_light_cue(self, dev: str, status: int,
                      data1: int, data2: int) -> None:
        bridge = self.bridges.get(dev)
        if bridge is None or bridge.session is None:
            return
        try:
            bridge.session.feed_midi(status, data1, data2)
        except Exception:
            logger.exception("feed_midi for %s failed", dev)

    # --- outbound -----------------------------------------------------------
    def _send(self, dev: str, msg: dict) -> None:
        client = self._clients.get(dev)
        if client is None:
            return
        self.server.send(client, msg)
