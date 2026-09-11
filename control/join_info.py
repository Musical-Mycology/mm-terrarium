"""Join info: everything a guest needs to reach the loaded Bit.

The Console's Join card and terrarium_boot's JOIN_URL stdout lines both
read this. Pure: every input is an argument (LAN address, ports,
ensemble, the Bit's role-to-node map), so it runs offline and the same
function serves both consumers. Spec:
docs/superpowers/specs/2026-09-10-terrarium-standup-and-join-design.md
section 5.

Why `dev` is absent from the URL and the command: the Tuneshroom web
app mints its own device id per page load (mm-tuneshroom
lib/sim/device_id.dart), and many phones scan the same poster, so a
pinned dev would make every scan after the first lose O2's service-name
race.

Why only the Chrome sim command: since the o2lite cutover the Terrarium
speaks o2lite and o2ws only. Native iOS/Android and the Radxa app still
use the old websocket wire, so the browser sim is the one Tuneshroom
path that connects today.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

NATIVE_NOTE = ("Native iOS/Android and the Radxa app cannot connect until "
               "mm-tuneshroom's FFI o2lite link lands; use the Chrome sim.")


def guest_url(*, lan_ip: str, www_port: int, arco_http_port: int,
              ensemble: str, node: str) -> str:
    """The URL a phone opens (or scans) to join `node` on this Terrarium.
    The page is served by harness/www_server.py under /app/; its o2ws
    websocket goes to Arco's HTTP port on the same host."""
    query = urlencode({"node": node,
                       "o2ws": f"{lan_ip}:{arco_http_port}",
                       "ens": ensemble})
    return f"http://{lan_ip}:{www_port}/app/?{query}"


def start_url(*, lan_ip: str, www_port: int, key: str) -> str:
    """The admin start URL a QR code or NFC tag carries (spec section 3)."""
    return f"http://{lan_ip}:{www_port}/start?{urlencode({'key': key})}"


def tuneshroom_command(*, lan_ip: str, arco_http_port: int, ensemble: str,
                       node: str) -> str:
    """The `flutter run` line for the Tuneshroom web sim, to be run from
    the mm-tuneshroom checkout root. The defines mirror the URL query
    parameters (mm-tuneshroom lib/sim/join_params.dart resolves URL
    params first, then --dart-define)."""
    return ("flutter run -d chrome -t lib/sim_main.dart "
            f"--dart-define=NODE={node} "
            f"--dart-define=O2WS={lan_ip}:{arco_http_port} "
            f"--dart-define=ENS={ensemble}")


def default_qr_svg(url: str) -> str:
    """An inline SVG (no XML prologue) of a medium-error-correction QR
    for `url`. segno is imported lazily so importing this module never
    requires it."""
    import segno
    return segno.make(url, error="m").svg_inline(scale=4)


def build_join_info(*, lan_ip: str, www_port: int, arco_http_port: int,
                    ensemble: str, bit_name: str | None, nodes,
                    app_present: bool, qr_svg=default_qr_svg,
                    start_key: str | None = None) -> dict:
    """The Join card's read model. `nodes` is an iterable of (role, node)
    pairs, the shape of BitConfig.launch.nodes; empty when no Bit is
    loaded. `qr_svg` is a callable url -> svg string, or None for no QR;
    an encoder that raises yields qr_svg=None for that row rather than a
    failed snapshot."""
    rows = []
    for role, node in nodes:
        url = guest_url(lan_ip=lan_ip, www_port=www_port,
                        arco_http_port=arco_http_port, ensemble=ensemble,
                        node=node)
        svg = None
        if qr_svg is not None:
            try:
                svg = qr_svg(url)
            except Exception:
                logger.exception("QR encoder failed for %s; card shows the "
                                 "URL only", url)
                svg = None
        rows.append({
            "role": role,
            "node": node,
            "url": url,
            "qr_svg": svg,
            "tuneshroom_cmd": tuneshroom_command(
                lan_ip=lan_ip, arco_http_port=arco_http_port,
                ensemble=ensemble, node=node),
        })
    start = None
    if start_key:
        url = start_url(lan_ip=lan_ip, www_port=www_port, key=start_key)
        svg = None
        if qr_svg is not None:
            try:
                svg = qr_svg(url)
            except Exception:
                logger.exception("QR encoder failed for %s", url)
        start = {"url": url, "qr_svg": svg, "key": start_key,
                 "wire": f'/game/start "ss" <dev> {start_key}'}
    return {
        "www_url": f"http://{lan_ip}:{www_port}/app/",
        "o2ws_host": f"{lan_ip}:{arco_http_port}",
        "ensemble": ensemble,
        "app_present": bool(app_present),
        "bit": bit_name,
        "nodes": rows,
        "native_note": NATIVE_NOTE,
        "start": start,
    }
