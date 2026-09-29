"""Find this box's Arco on zeroconf and report its O2 process name, which
carries Arco's ephemeral TCP/UDP ports (the Arco server build defines
O2_NO_O2DISCOVERY, so the ports change on every start). Served at
GET /o2proc by harness/www_server.py for firmware that cannot use mDNS.
Spec: docs/superpowers/specs/2026-09-29-o2proc-port-lookup-design.md.

Pure parsing and selection here; the zeroconf browse is added in Task A2
and imported lazily (no zeroconf at module level: boundary rules)."""
from __future__ import annotations

from typing import NamedTuple

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
