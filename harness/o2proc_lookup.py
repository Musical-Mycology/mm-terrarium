"""Find this box's Arco on zeroconf and report its O2 process name, which
carries Arco's ephemeral TCP/UDP ports (the Arco server build defines
O2_NO_O2DISCOVERY, so the ports change on every start). Served at
GET /o2proc by harness/www_server.py for firmware that cannot use mDNS.
Spec: docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md.

Pure parsing and selection here; the zeroconf browse is added in Task A2
and imported lazily (no zeroconf at module level: boundary rules)."""
from __future__ import annotations

import logging
import threading
from typing import NamedTuple

from control.terrarium import TerrariumState

_HEX = set("0123456789abcdefABCDEF")


class ProcName(NamedTuple):
    internal_ip: str
    tcp: int
    udp: int


class O2Record(NamedTuple):
    instance: str
    port: int
    txt_name: str | None


def _hex(s: str) -> bool:
    return bool(s) and all(c in _HEX for c in s)


def parse_proc_name(name) -> ProcName | None:
    """`@pppppppp:iiiiiiii:tttt:uuuu` -> ProcName, else None."""
    if not isinstance(name, str) or len(name) != 28 or name[0] != "@":
        return None
    if name[9] != ":" or name[18] != ":" or name[23] != ":":
        return None
    pub, internal, tcp, udp = name[1:9], name[10:18], name[19:23], name[24:28]
    if not all(_hex(p) for p in (pub, internal, tcp, udp)):
        return None
    ip = ".".join(str(int(internal[i:i + 2], 16)) for i in range(0, 8, 2))
    return ProcName(ip, int(tcp, 16), int(udp, 16))


def select_local(records, ensemble: str, local_ips) -> tuple[str | None, str]:
    """The one record that is `ensemble`, well-formed, self-consistent and
    whose internal IP is this host's. Anything else is (None, reason)."""
    matches = []
    for rec in records:
        if rec.instance != ensemble:
            continue
        parsed = parse_proc_name(rec.txt_name)
        if parsed is None or parsed.tcp != rec.port:
            continue
        if parsed.internal_ip not in local_ips:
            continue
        matches.append(rec.txt_name)
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return None, f"no {ensemble} advertised by this host"
    return None, f"{len(matches)} local {ensemble} processes advertised"


NOT_READY = "arco not ready"
PENDING = "lookup pending"
_SERVICE = "_o2proc._tcp.local."


def browse(timeout: float = 3.0) -> list[O2Record]:
    """One zeroconf browse of _o2proc._tcp. Live network only; tests inject
    a fake. zeroconf is imported here, never at module level."""
    import time
    from zeroconf import ServiceBrowser, Zeroconf

    records: list[O2Record] = []
    zc = Zeroconf()

    class _Listener:
        def add_service(self, z, type_, name):
            info = z.get_service_info(type_, name, 2000)
            if info is None:
                return
            txt = info.properties.get(b"name")
            records.append(O2Record(name.split("." + type_)[0], info.port,
                                    txt.decode("utf-8", "replace") if txt else None))

        def update_service(self, *args):
            pass

        def remove_service(self, *args):
            pass

    try:
        ServiceBrowser(zc, _SERVICE, _Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    return records


def local_ipv4s() -> set[str]:
    """Every IPv4 address on this host's interfaces (netifaces, lazy)."""
    import netifaces

    ips = {"127.0.0.1"}
    for iface in netifaces.interfaces():
        for addr in netifaces.ifaddresses(iface).get(netifaces.AF_INET, []):
            ips.add(addr["addr"])
    return ips


def find_local_arco(ensemble: str, *, browse=browse, local_ips=local_ipv4s,
                    attempts: int = 3) -> tuple[str | None, str]:
    reason = ""
    for _ in range(attempts):
        name, reason = select_local(browse(), ensemble, local_ips())
        if name is not None:
            return name, ""
    return None, reason


class O2ProcHolder:
    """What GET /o2proc serves; written by O2ProcWatcher's thread, read by
    the www server's handler threads."""

    def __init__(self, ensemble: str) -> None:
        self.ensemble = ensemble
        self._lock = threading.Lock()
        self._name: str | None = None
        self._reason = NOT_READY

    def get(self) -> tuple[str | None, str]:
        with self._lock:
            return self._name, self._reason

    def set_name(self, name: str) -> None:
        with self._lock:
            self._name, self._reason = name, ""

    def set_unavailable(self, reason: str) -> None:
        with self._lock:
            self._name, self._reason = None, reason


def _spawn_daemon(fn) -> None:
    threading.Thread(target=fn, daemon=True, name="o2proc-lookup").start()


class O2ProcWatcher:
    """Terrarium observer: one lookup per ROOM_READY (Arco has passed
    wait_ready, so it is advertising), cleared on any other state (the next
    Arco has new ports). A generation counter drops a lookup that finishes
    after its Room is gone."""

    def __init__(self, holder: O2ProcHolder, *, lookup, spawn=_spawn_daemon):
        self._holder = holder
        self._lookup = lookup
        self._spawn = spawn
        self._lock = threading.Lock()
        self._gen = 0

    def seed(self, state) -> None:
        """For a Room loaded before this observer was registered (--room)."""
        if state is TerrariumState.ROOM_READY:
            self._start()

    def on_terrarium_state_change(self, old_state, new_state) -> None:
        if new_state is TerrariumState.ROOM_READY:
            self._start()
            return
        with self._lock:
            self._gen += 1
            self._holder.set_unavailable(NOT_READY)

    def _start(self) -> None:
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._holder.set_unavailable(PENDING)

        def run():
            try:
                name, reason = self._lookup()
            except Exception as exc:
                logging.getLogger(__name__).exception("o2proc lookup failed")
                name, reason = None, f"lookup failed: {exc}"
            with self._lock:
                if gen != self._gen:
                    return
                if name is not None:
                    self._holder.set_name(name)
                else:
                    self._holder.set_unavailable(reason)

        self._spawn(run)
