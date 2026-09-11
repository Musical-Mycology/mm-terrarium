"""WwwServer: the LAN static server that serves www/ to guest phones.

Spec: docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md,
section 4.1. Arco also serves www/ (on ARCO_HTTP_PORT) but labels every
file text/html, which a Flutter web build's .wasm and module scripts
cannot load under; this server exists so the page comes with the right
Content-Type while its websocket still goes to Arco. Static files only,
no state, so it binds the LAN by default; the Console's trust model
(loopback, no auth) does not apply here and guests are never pointed at
the Console.
"""

from __future__ import annotations

import functools
import logging
import queue
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from control.lobby import StartRequest, TERRARIUM_ADMIN

# The documented port a QR poster points at (spec section 4.1).
WWW_PORT = 8788

# The one dynamic route this server answers; everything else is a static
# file under `directory`.
START_PATH = "/start"
_LOOPBACK = ("127.0.0.1", "::1")
START_QUEUE_MAX = 16


def lan_ip() -> str:
    """The first non-loopback IPv4 address of this host, or 127.0.0.1.

    A UDP socket "connected" to a routable address never sends a packet;
    the kernel just picks the interface and source address it would use.
    That is the address a phone on the venue LAN can reach.

    With no route out, the probe either raises or hands back the unspecified
    address 0.0.0.0 (or nothing at all), none of which a phone can open.
    All three collapse to the loopback fallback, and every fallback is
    logged: the printed WWW_URL then says 127.0.0.1, which reads as a
    working link but reaches no guest, so the operator needs to be told the
    host has no LAN address rather than left to debug the phone.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        ip = probe.getsockname()[0]
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "no LAN address found (%s); serving the guest page on 127.0.0.1, "
            "which no phone can reach", exc)
        ip = "127.0.0.1"
    else:
        if not ip or ip == "0.0.0.0":
            logging.getLogger(__name__).warning(
                "no LAN address found (the interface probe returned %r); "
                "serving the guest page on 127.0.0.1, which no phone can "
                "reach", ip)
            ip = "127.0.0.1"
    finally:
        probe.close()
    return ip


class _QuietHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler already confines paths to `directory`
    (translate_path drops '..' components) and maps .wasm and .js through
    the mimetypes table. Only its per-request log line is silenced: the
    stack's stdout carries markers, not access logs.

    Static files as before, plus the one dynamic route: GET /start.
    The handler runs on the server thread and never touches the engine;
    it only enqueues a StartRequest for DeviceLinkAgent to drain on its
    own tick (spec section 3)."""

    def __init__(self, *args, start_requests=None, **kwargs):
        self._start_requests = start_requests
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):  # noqa: A002 (stdlib signature)
        return

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path != START_PATH:
            return super().do_GET()
        params = parse_qs(parsed.query)
        key = params.get("key", [""])[0]
        dev = params.get("dev", [""])[0] or None
        if self.client_address[0] in _LOOPBACK:
            dev, source = TERRARIUM_ADMIN, "web:terrarium"
        else:
            source = f"web:{dev}" if dev else "web:anonymous"
        if self._start_requests is None:
            self.send_error(404, "start is not wired on this server")
            return
        try:
            self._start_requests.put_nowait(StartRequest(key, dev, source))
        except queue.Full:
            self.send_error(503, "start queue full")
            return
        body = b"start requested\n"
        self.send_response(202)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WwwServer:
    def __init__(self, root: str, host: str = "0.0.0.0",
                 port: int = WWW_PORT) -> None:
        self._root = root
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.start_requests: queue.Queue = queue.Queue(maxsize=START_QUEUE_MAX)

    def start(self) -> None:
        handler = functools.partial(_QuietHandler, directory=self._root,
                                    start_requests=self.start_requests)
        self._server = ThreadingHTTPServer((self._host, self._port), handler)
        self._server.daemon_threads = True
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="www-server")
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def port(self) -> int:
        return self._port

    def url(self, host: str | None = None) -> str:
        return f"http://{host or lan_ip()}:{self._port}/"
