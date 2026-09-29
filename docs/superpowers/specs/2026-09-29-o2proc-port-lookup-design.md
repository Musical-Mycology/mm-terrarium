# O2 port lookup: a fixed-host fallback for dev shrooms

**Date:** 2026-09-29
**Repos:** mm-terrarium (lookup endpoint), mm-devshroom (firmware fallback)
**Status:** design, awaiting review

## Problem

A dev shroom (mm-devshroom, ESP32-P4 on o2lite) finds Arco only through mDNS:
it browses `_o2proc._tcp`, reads Arco's O2 process name from the TXT record
(`@<pub ip>:<internal ip>:<tcp port>:<udp port>`, 28 chars) and connects
there (`lib/o2/o2liteesp32.cpp`, `o2ldisc_poll`). Where multicast does not get
through, the device never connects and says nothing useful. Campus and
enterprise Wi-Fi commonly block it (the mm-devshroom README already warns).

A fixed `IP:port` in `config.h` cannot fix this. The Arco server build defines
`O2_NO_O2DISCOVERY=1` (`arco/server/arcoserver.cmakeinclude`), so O2 skips its
fixed port map (`o2/src/discovery.cpp`, 64541, ...) and the OS assigns
ephemeral ports. Measured on WSL on 2026-09-29: TCP 44985, UDP 40776. Both
ports change on every Arco start.

Context: WSL on Windows is the default dev host. On Windows 11, mirrored
networking plus avahi-daemon has been confirmed to let a dev shroom discover
and connect over mDNS (mm-terrarium `docs/MM_TERRARIUM.md`, *Linux / WSL host
setup*). The fallback is therefore **not** required for WSL. It is for
networks that block mDNS.

## Goal

A developer who already flashes their own shroom (the agreed workflow: the
same person reflashes it per setup) sets one IP in `include/config.h`. The
device then connects to that Terrarium whether or not mDNS works, with no
other per-launch step. Firmware with the setting empty behaves exactly as it
does today.

## Non-goals

- WSL in NAT mode. The fallback host is the Windows machine's LAN IP, and
  NAT does not expose WSL's ports on it. Mirrored mode is the supported path.
- Hostname resolution. IPv4 literals only.
- Changing how Arco picks ports, or anything in upstream arco, o2 or the
  vendored `lib/o2/`.
- Phones and browsers. They reach Arco over o2ws on port 8080 and need
  no discovery.

## Design

The Terrarium already runs an HTTP server on a fixed, documented port: the
guest-page server, `WWW_PORT = 8788` (`harness/www_server.py`), up from boot
and hosting `GET /start` and `GET /prepare`. It gains one read-only route that
returns Arco's current O2 process name. The firmware learns the ports from it
at runtime.

```
dev shroom                                       Terrarium box
  mDNS browse _o2proc._tcp ... nothing within O2_FALLBACK_AFTER_MS
  GET http://<O2_FALLBACK_HOST>:8788/o2proc  -->  harness/www_server.py
                                             <--  200 "@...:afb9:9f48\n"
  o2l_address_init(udp_server_sa, <O2_FALLBACK_HOST>, udp)
  o2l_network_connect(<O2_FALLBACK_HOST>, tcp)  --> Arco (o2lite handshake)
  /game/hello + /game/join as today
```

### Terrarium side (mm-terrarium)

**1. `harness/o2proc_lookup.py` (new).** It finds this box's Arco on the
zeroconf network. It browses `_o2proc._tcp` with the `zeroconf` package,
which requirements-dev.txt already lists for o2litepy. It keeps a record only
when all of these hold:

- the instance name equals the stack's ensemble (`arco` by default,
  `run_stack --ensemble`);
- the TXT `name` is a valid 28-char O2 process name;
- its TCP port field equals the SRV port;
- the resolved address is one of this host's own addresses.

The last filter excludes another box's Arco on the same LAN. Exactly one
match returns the name; zero or several matches return a reason. Parsing and
filtering are pure functions; the browse is injected, so tests need no
network.

It lives in `harness/`, never `control/`: Control stays free of
zeroconf/o2litepy at module level (*Boundary rules*;
`tests/test_room_profile.py`). `zeroconf` is imported lazily inside the
browse. Every live stack already needs it, because o2litepy uses it, though
it is only listed in `requirements-dev.txt`. The offline suite never imports
it.

**2. When the lookup runs.** It runs once per Arco lifetime, never per
request:

- It is triggered by a Terrarium observer on `on_terrarium_state_change`.
  On `ROOM_READY`, Arco has already passed `wait_ready`, so it is
  advertising.
- It runs off the tick thread with a bounded browse (about 3 s), and caches
  the result in a small thread-safe holder.
- On `ROOM_UNLOADING` / `NO_ROOM` the holder is cleared, because the next
  Arco has new ports.
- One Arco per Control process (*One Arco per Control process*), so there is
  exactly one name per ROOM_READY.

**3. `GET /o2proc` on `harness/www_server.py`.** The holder is injected into
the handler the same way `start_requests` is. Responses:

| Case | Status | Body (`text/plain`) |
|---|---|---|
| name cached | 200 | the 28-char O2 process name, newline |
| no Room / lookup pending / lookup failed | 503 | reason (e.g. `arco not ready`) |
| `?ensemble=X` given and not ours | 404 | `unknown ensemble` |

It is read-only and triggers no engine action, so it needs no key. It
exposes nothing mDNS does not already broadcast to the same LAN.

### Firmware side (mm-devshroom)

**4. Config.** New optional settings. They are macros with defaults (in a
small `include/fallback_config.h` that `config.h` may pre-empt), so every
existing `config.h` still compiles:

```cpp
// in include/config.h, per developer (gitignored):
#define O2_FALLBACK_HOST "192.168.1.20"   // unset or "" = mDNS only (today)
// optional overrides, defaults shown:
#define O2_FALLBACK_HTTP_PORT 8788        // Terrarium WWW_PORT
#define O2_FALLBACK_AFTER_MS  10000       // mDNS gets this long first
#define O2_FALLBACK_RETRY_MS  5000        // between lookup attempts
```

`config.example.h` documents them, commented out.

**5. `lib/fallback_state/fallback_state.h` (new, pure).** This follows the
`JoinState` pattern (mm-devshroom PR #5): no Arduino, no o2lite, native
Unity tests. It holds:

- the timing policy: armed `AFTER_MS` after boot or link loss, then one
  attempt every `RETRY_MS`, and disarmed on link up;
- a parser: 28-char name to `{tcp, udp}`, rejecting anything malformed.

It parses independently of `o2l_is_valid_proc_name` so the native tests do
not link o2lite. The format is pinned by a test vector taken from a real
Arco (`@00000000:ac17f983:afb9:9f48` gives tcp 44985, udp 40776).

**6. `src/main.cpp` wiring.**

- While not linked and the state says attempt: `HTTPClient` GET (Arduino-ESP32
  core, timeout of about 1.5 s, since `loop()` must keep polling), then parse.
- Then `o2l_address_init(&udp_server_sa, O2_FALLBACK_HOST, udp, false)` and
  `o2l_network_connect(O2_FALLBACK_HOST, tcp)`. Both are public o2lite API
  (`lib/o2/o2lite.h`), and the latter performs the `!_o2/o2lite/con`
  handshake. Nothing in `lib/o2/` changes.
- Once `tcp_sock` is valid, o2lite's own mDNS poll stands down
  (`o2liteesp32.cpp`: it returns early), so the two paths cannot race into
  two connections.
- **Use `O2_FALLBACK_HOST` for both TCP and UDP, never the internal IP inside
  the name.** On a multi-homed Windows host in mirrored mode, O2 may embed an
  interface address (vEthernet, VPN) the device cannot reach. The IP that
  just answered HTTP is known to be reachable.
- Serial log: one line per lookup outcome change (`fallback: 503 arco not
  ready`, `fallback: connecting 192.168.1.20 tcp 44985 udp 40776`).
- Join retry (PR #5) applies unchanged after the link comes up.

## Error handling

- Terrarium up with no Room: 503, and the device retries every `RETRY_MS`.
  A Room loads, the next attempt gets 200.
- Arco restarts (a Room reload): the link drops, the holder is cleared then
  refilled, and the device re-arms and relearns the new ports.
- A wrong IP or firewall: the HTTP attempt times out, is logged once and
  retried. Mirrored WSL needs the Hyper-V inbound rule that the recipe
  already requires for 8788 (phones).
- A stale name (Arco died between lookup and connect): connect fails and the
  retry cycle repeats.
- The guest-page server is disabled (`WWW_PORT` `0`, *Ports* in the
  deep-dive): there is no `/o2proc`, so the fallback cannot work. The HTTP
  attempt fails like a wrong IP. This is documented next to
  `O2_FALLBACK_HOST` in `config.example.h`.

## Testing

- **mm-terrarium (pytest):** parser and filters on fake zeroconf records
  (match, wrong ensemble, remote address, malformed name, two matches); the
  holder lifecycle via fake terrarium state changes; `/o2proc` returns
  200/503/404 through the existing `www_server` test style.
- **mm-terrarium (live):** after `./terrarium.sh --room TEST`, `curl
  localhost:8788/o2proc` equals the name a zeroconf browse shows (the same
  probe used on 2026-09-29).
- **mm-devshroom (native):** `FallbackState` timing and the name parser,
  including the real-Arco vector.
- **Hardware (manual, pending a teammate):** set `O2_FALLBACK_HOST`, block
  mDNS (a network that drops multicast, or a Windows firewall rule dropping
  inbound UDP 5353 for WSL). The device logs the fallback, connects, then
  `ROLE granted` after a Bit loads.

## Rollout

1. mm-terrarium: lookup, holder, endpoint, a deep-dive line under *Ports* and
   the WSL recipe.
2. mm-devshroom: after PR #5 merges (both touch `main.cpp` and
   `platformio.ini`).
3. The mm-devshroom README gains a "Networks that block mDNS" entry pointing
   at `O2_FALLBACK_HOST`.

## Alternatives rejected

- **A fixed `IP:port` in config.** The ports are ephemeral (see Problem).
- **Pinning Arco's ports** by building with O2's own discovery: this turns
  off the zeroconf discovery that Control and devices use today. It is an
  upstream build change with a wide blast radius.
- **A unicast mDNS query to the host's :5353.** It still depends on who owns
  5353 on the host, the exact variable being routed around.
- **Reading the ports out of Control's o2litepy connection.** o2litepy pops
  the discovered host dict (`py3discovery.pop_a_service`) and keeps the ports
  only in private connection state. That reaches into upstream internals.
