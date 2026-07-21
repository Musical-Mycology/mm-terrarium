"""Transport implementations for the uplink -- the only code that touches
an actual socket. See design spec section 3.
"""

import json
from collections import deque
from typing import Protocol

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect


class Transport(Protocol):
    """What UplinkAgent needs from a connection to fairyring. Non-blocking:
    receive() returns None immediately when there's nothing waiting.
    """

    connected: bool

    def connect(self) -> None:
        """Establish (or re-establish) the connection."""

    def send(self, msg: dict) -> None:
        """Send one message. Callers are expected to check `connected`
        first -- behavior when disconnected is implementation-defined."""

    def receive(self) -> dict | None:
        """Return the next queued inbound message, or None if none
        waiting."""


class FakeTransport:
    """In-process test double. No socket, no server process -- tests push
    inbound messages via `push_incoming` and inspect `sent`.
    """

    def __init__(self):
        self.connected = False
        self.sent: list[dict] = []
        self._incoming: deque[dict] = deque()
        self.connect_count = 0

    def connect(self) -> None:
        self.connected = True
        self.connect_count += 1

    def disconnect(self) -> None:
        """Test helper: simulate the link dropping."""
        self.connected = False

    def send(self, msg: dict) -> None:
        self.sent.append(msg)

    def receive(self) -> dict | None:
        if not self._incoming:
            return None
        return self._incoming.popleft()

    def push_incoming(self, msg: dict) -> None:
        """Test helper: queue a message as if it arrived from fairyring."""
        self._incoming.append(msg)


class WebSocketTransport:
    """Persistent outbound websocket connection, Terrarium-initiated (venue
    boxes are NAT'd -- this always dials out, never listens). Synchronous
    API to match this codebase's plain-tick-loop style (see
    control/engine.py); receive() uses a zero-timeout recv so it never
    blocks the caller's loop.
    """

    def __init__(self, uri: str):
        self.uri = uri
        self.connected = False
        self._ws = None

    def connect(self) -> None:
        self._ws = ws_connect(self.uri)
        self.connected = True

    def send(self, msg: dict) -> None:
        if not self.connected:
            raise RuntimeError("send() called while disconnected")
        try:
            self._ws.send(json.dumps(msg))
        except ConnectionClosed:
            self.connected = False
            raise

    def receive(self) -> dict | None:
        if not self.connected:
            return None
        try:
            raw = self._ws.recv(timeout=0)
        except TimeoutError:
            return None
        except ConnectionClosed:
            self.connected = False
            return None
        return json.loads(raw)
