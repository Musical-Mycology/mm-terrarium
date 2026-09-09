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
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# The documented port a QR poster points at (spec section 4.1).
WWW_PORT = 8788


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
    stack's stdout carries markers, not access logs."""

    def log_message(self, format, *args):  # noqa: A002 (stdlib signature)
        return


class WwwServer:
    def __init__(self, root: str, host: str = "0.0.0.0",
                 port: int = WWW_PORT) -> None:
        self._root = root
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        handler = functools.partial(_QuietHandler, directory=self._root)
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
