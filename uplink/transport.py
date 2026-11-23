"""Transport implementations for the uplink -- the only code that touches
an actual socket. See design spec section 3.

Every transport offers what UplinkAgent needs from a connection to
fairyring: `connected`, `connect()`, `send(msg)` and a non-blocking
`receive()` that returns None when nothing is waiting. `durable` is True
when a send() that returns without raising has left the box on a real
socket; the agent replays and trims its journal only then (spec
2026-09-13 section 6.3).
"""

import json
import logging
from collections import deque

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect

from control.wire_json import dumps as _json_dumps

logger = logging.getLogger(__name__)


def frame_size(msg: dict) -> int:
    """Bytes the frame occupies on the wire (UTF-8 of its wire JSON)."""
    return len(_json_dumps(msg).encode("utf-8"))


class FakeTransport:
    """In-process test double. No socket, no server process -- tests push
    inbound messages via `push_incoming` and inspect `sent`.
    """

    def __init__(self, durable: bool = True):
        self.connected = False
        self.durable = durable
        self.sent: list[dict] = []
        self._incoming: deque[dict] = deque()
        self.connect_count = 0
        self.close_count = 0

    def connect(self) -> None:
        self.connected = True
        self.connect_count += 1

    def disconnect(self) -> None:
        """Test helper: simulate the link dropping."""
        self.connected = False

    def close(self) -> None:
        self.connected = False
        self.close_count += 1

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

    durable = True

    def __init__(self, uri: str, headers: dict[str, str] | None = None):
        self.uri = uri
        self._headers = headers  # carries the secret: never logged
        self.connected = False
        self._ws = None

    def __repr__(self) -> str:
        return f"WebSocketTransport(uri={self.uri!r})"

    def connect(self) -> None:
        self._ws = ws_connect(self.uri, additional_headers=self._headers)
        self.connected = True

    def close(self) -> None:
        """Drop the socket if any. Never raises."""
        self.connected = False
        ws, self._ws = self._ws, None
        if ws is None:
            return
        try:
            ws.close()
        except Exception:
            pass

    def send(self, msg: dict) -> None:
        if not self.connected:
            raise RuntimeError("send() called while disconnected")
        try:
            self._ws.send(_json_dumps(msg))
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


class LogTransport:
    """A box with [uplink] but no broker URL: frames go to the log so the
    identity, resync and journal-append paths run live, and nothing is ever
    trimmed on its account (durable is False)."""

    durable = False

    def __init__(self) -> None:
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def send(self, msg: dict) -> None:
        shown = dict(msg)
        if "secret" in shown:
            shown["secret"] = "[redacted]"
        logger.debug("uplink (log-only) %s", _json_dumps(shown))

    def receive(self) -> dict | None:
        return None
