"""ConsoleServer: the only socket-touching code in the console package. One
port serves the static admin page over HTTP (GET /) and upgrades websocket
clients (/ws) on the same listener. Handler threads only touch thread-safe
queues and a lock-guarded client set; every GameServer access stays on the
tick thread, which drives ConsoleAgent.poll(). Synchronous websockets API to
match this codebase's plain-tick-loop style.
"""

import json
import logging
import threading
from collections import deque
from pathlib import Path

from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.sync.server import serve

logger = logging.getLogger(__name__)

_INDEX_HTML = (Path(__file__).resolve().parent / "static" / "index.html")


class ConsoleServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._host = host
        self._port = port
        self._server = None
        self._thread = None
        self._index_bytes = _INDEX_HTML.read_bytes()
        self._lock = threading.Lock()
        self._clients: set = set()
        self._new_clients: deque = deque()
        self._inbound: deque = deque()

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._server = serve(
            self._handle, self._host, self._port,
            process_request=self._process_request)
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

    # --- HTTP: serve index.html on GET / ; let /ws upgrade -----------------
    def _process_request(self, connection, request):
        if request.path == "/ws":
            return None   # proceed to the websocket handshake
        headers = Headers()
        headers["Content-Type"] = "text/html; charset=utf-8"
        headers["Content-Length"] = str(len(self._index_bytes))
        return Response(200, "OK", headers, self._index_bytes)

    # --- per-connection handler thread -------------------------------------
    def _handle(self, connection) -> None:
        with self._lock:
            self._clients.add(connection)
            self._new_clients.append(connection)
        try:
            for raw in connection:      # blocks until a message or close
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    logger.warning("dropping non-JSON console frame")
                    continue
                with self._lock:
                    self._inbound.append((connection, msg))
        except Exception:
            logger.debug("console client handler ended", exc_info=True)
        finally:
            with self._lock:
                self._clients.discard(connection)

    # --- tick-thread API (consumed by ConsoleAgent) ------------------------
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
            logger.debug("console send failed; dropping client", exc_info=True)
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
                logger.debug("console broadcast failed; dropping client",
                             exc_info=True)
                with self._lock:
                    self._clients.discard(client)
