"""DeviceLinkServer: the only socket-touching code in the devicelink package.

Deliberately the same shape as console/server.py -- handler threads touch
only thread-safe queues and a lock-guarded client set, every GameServer
access stays on the tick thread that drives DeviceLinkAgent.poll().
Devices need no page served, so there is no static-HTML branch.
"""

import json
import logging
import threading
from collections import deque

from websockets.sync.server import serve

from control.wire_json import dumps as _json_dumps

logger = logging.getLogger(__name__)


class DeviceLinkServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._host = host
        self._port = port
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self._clients: set = set()
        self._new_clients: deque = deque()
        self._inbound: deque = deque()
        self._devs: dict[str, object] = {}

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._server = serve(self._handle, self._host, self._port)
        self._port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    @property
    def port(self) -> int:
        return self._port

    # --- per-connection handler thread -------------------------------------
    def _handle(self, connection) -> None:
        with self._lock:
            self._clients.add(connection)
            self._new_clients.append(connection)
        try:
            for raw in connection:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    logger.warning("dropping non-JSON device frame")
                    continue
                with self._lock:
                    self._inbound.append((connection, msg))
        except Exception:
            logger.debug("device client handler ended", exc_info=True)
        finally:
            with self._lock:
                self._clients.discard(connection)

    # --- tick-thread API (consumed by DeviceLinkAgent) ---------------------
    def drain_new_clients(self) -> list:
        with self._lock:
            out = list(self._new_clients)
            self._new_clients.clear()
        return out

    def drain_inbound(self) -> list:
        with self._lock:
            out = list(self._inbound)
            self._inbound.clear()
        return out

    def bind_dev(self, dev: str, client) -> None:
        """Associate a dev id with its connection. Called by the agent once
        /game/hello names an otherwise anonymous client."""
        self._devs[dev] = client

    def drop_dev(self, dev: str) -> None:
        self._devs.pop(dev, None)

    def send(self, dev: str, msg: dict) -> None:
        """Send to a dev id. Unknown dev is a silent no-op: a cue for a
        device that has gone away must never raise into the engine tick."""
        client = self._devs.get(dev)
        if client is None:
            return
        try:
            client.send(_json_dumps(msg))
        except Exception:
            logger.debug("device send failed; dropping client", exc_info=True)
            with self._lock:
                self._clients.discard(client)

    def broadcast(self, msg: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        payload = _json_dumps(msg)
        for client in clients:
            try:
                client.send(payload)
            except Exception:
                logger.debug("device broadcast failed; dropping client",
                             exc_info=True)
                with self._lock:
                    self._clients.discard(client)
