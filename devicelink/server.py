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

    def send(self, client, msg: dict) -> None:
        try:
            client.send(json.dumps(msg))
        except Exception:
            logger.debug("device send failed; dropping client", exc_info=True)
            with self._lock:
                self._clients.discard(client)

    def broadcast(self, msg: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        payload = json.dumps(msg)
        for client in clients:
            try:
                client.send(payload)
            except Exception:
                logger.debug("device broadcast failed; dropping client",
                             exc_info=True)
                with self._lock:
                    self._clients.discard(client)
