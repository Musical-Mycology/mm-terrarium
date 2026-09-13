# MycoQuest handoff, Terrarium side: LAN prepare, players on bit_completed, uplink identity and replay journal

**Date:** 2026-09-13
**Status:** Approved (brainstorm session with Chris; every decision below was
answered explicitly)
**Requirements source:** mm-renquest
`docs/superpowers/specs/2026-09-12-mycoquest-launches-tuneshroom-design.md`
(section 7.2 is the mm-terrarium list; sections 2.1 to 2.4 the pathway;
section 4 invariants 8 to 17; section 8 the offline table). Its MycoQuest
halves shipped as mm-renquest PR #354 (Plan A, LAN prepare, launch and start
from the app) and PR #355 (Plan B, box secret, credential publish, fairyring
poll and `bit_played` credit). The replay requirement is mm-terrarium issue
#103. The broker contract MycoQuest proposed is mm-fairyring issue #1;
mm-fairyring has no code yet and nothing here depends on it being live.
**Builds on:** `docs/superpowers/specs/2026-07-20-terrarium-uplink-design.md`
(the uplink wire protocol; this spec revises its section 5 choice of resync
over replay), `docs/superpowers/specs/2026-09-11-metronome-lobby-and-admin-start-design.md`
(`GET /start`, `request_start`, the start key, the loopback rule),
`docs/superpowers/specs/2026-09-10-terrarium-standup-and-join-design.md`
(the Join card and `build_join_info`).

## 1. Purpose

MycoQuest's play path (its sections 2.1 to 2.4) needs four things from the
box and one small extra:

1. `GET /prepare` on the LAN static server, so the app can load the quest's
   Bit into the lobby with the same key the Start Round token uses.
2. A `players` list on the `bit_completed` up-event, so MycoQuest can credit
   every GemID that was in the round.
3. An `[uplink]` table in `terrarium.toml` and an identity frame on connect,
   so fairyring can map the box to `(tenant_slug, terrarium_name)`.
4. A bounded local journal of `bit_completed`, replayed on reconnect, so a
   round completed with the link down is never lost (issue #103).
5. The box's LAN address in the resync frame, for the later address slice.

Only item 1 is needed for the demo; items 2 to 5 wait on fairyring to be
useful live. Chris chose to ship all of them as one plan.

Along the way this spec wires `UplinkAgent` into `harness/terrarium_boot.py`
for the first time. Today the agent and `WebSocketTransport` are constructed
only under `tests/`; no venue box has ever run the uplink.

## 2. Scope

**In scope:**

- `GET /prepare?key=<key>&bit=<name>[&dev=<GemID>]` on `harness/www_server.py`
  (port 8788, the documented `WWW_PORT`), the prepare rule in a new pure
  `control/prepare.py`, the drain in `devicelink/agent.py`, an observer record
  on every attempt, and the prepare URL on the Console's Join card.
- `RegistrationState.granted()` and `players: [{dev, role, class}]` on
  `bit_completed`, emitted at COMPLETING, never on abort.
- `[uplink]` in `terrarium.toml`, `UplinkConfig`, the identity frame as the
  first frame on every connect, a `LogTransport` for a box with no broker
  URL, and the boot wiring.
- `uplink/journal.py`: a count-bounded JSONL journal under the runs
  directory, replayed after the resync on a durable transport, then cleared.
- `lan_ip` on the resync `state_changed` event, injected as a callable.

**Out of scope (explicitly deferred):**

- Any mm-fairyring code, TLS, certificate handling, or an ack frame. The
  journal trims on send, not on ack, until fairyring offers one.
- The fairyring drive path (`load_bit` and `run` from MycoQuest over the
  uplink). Both commands already exist in `uplink/protocol.py`; the busy
  policy below is written so a later uplink prepare can call the same
  `PrepareAuthority`.
- Issue #100 (`set_admin_devices`, `admin_devices_applied`).
- Any change to `GET /start`, `/game/start`, the lobby, or `request_start`.
- Journaling any up-event other than `bit_completed`.
- A start timeout clearing an abandoned lobby (the Bit's `[start]
  timeout_seconds` already exists and is unchanged).

## 3. Architecture

```
MycoQuest app        harness/www_server.py         devicelink/agent.py        control/
(venue LAN)          (server thread)                (tick thread)              prepare.py
   |  GET /prepare       |                              |                        |
   |-------------------->| PrepareRequest + reply slot  |                        |
   |                     |----- prepare_requests ------>| _drain_prepare_requests|
   |                     |        (bounded queue)       |----------------------->| PrepareAuthority
   |                     |  waits <= 3 s on reply       |                        |  decide_prepare
   |   202 / 409 / 503   |<---------- reply.set() ------|<-----------------------|  load_bit / no-op
   |<--------------------|                              |                        |  notify_prepare_requested

control/engine.py            uplink/link.py                 uplink/transport.py
GameServer ---observer-----> UplinkAgent ----send/recv----> WebSocketTransport (durable)
  on_state_change(COMPLETING)   | identity, resync, replay    LogTransport (not durable)
                                | journal.append/entries/clear FakeTransport (durable flag)
                                v
                            uplink/journal.py  <runs_dir>/uplink_journal.jsonl
```

Three rules carried over unchanged:

- The engine is touched from the tick thread only. The www server thread
  enqueues; `DeviceLinkAgent` drains on its own tick, exactly as `/start`.
- `control/` never imports `uplink/`, `harness/`, luxaeterna, pyarco or
  o2litepy. `uplink/` never imports `harness/`; `lan_ip` reaches the agent
  as an injected callable.
- Every persisted or outbound JSON payload goes through
  `control/wire_json.dumps`.

## 4. The prepare route

### 4.1 HTTP surface (`harness/www_server.py`)

- `PREPARE_PATH = "/prepare"`, answered by `_QuietHandler.do_GET` beside
  `START_PATH`. Only GET; any other method falls through to the stdlib
  handler's default refusal as today.
- Query parameters: `key` (required for a reaction, may be absent), `bit`
  (required; an absent or empty `bit` is treated as an unknown Bit, see the
  rule), `dev` (optional GemID).
- The loopback rule is the `/start` rule verbatim: a client address in
  `127.0.0.1` or `::1` is `dev = TERRARIUM_ADMIN`, source `web:terrarium`;
  otherwise source is `web:<dev>` or `web:anonymous`.
- `WwwServer` gains `prepare_requests: queue.Queue` bounded at
  `PREPARE_QUEUE_MAX = 16`, wired into the handler like `start_requests`.
  When the queue is not wired the route answers 404 `prepare is not wired on
  this server`; when it is full, 503 `prepare queue full`.
- The handler enqueues `PrepareRequest(key, bit, dev, source, reply)` and
  then waits on `reply.done` (a `threading.Event`) for at most
  `PREPARE_REPLY_TIMEOUT_S = 3.0`. `reply` is a small mutable
  `PrepareReply(done, accepted, reason, visible)`.
- Status mapping, all bodies `text/plain; charset=utf-8`:

  | Outcome | Status | Body |
  |---|---|---|
  | accepted (load or no-op) | 202 | `prepare requested` |
  | refused, `visible` is true (busy, no room, load error) | 409 | the reason |
  | refused, `visible` is false (bad key, unknown Bit, wrong start condition) | 202 | `prepare requested` |
  | reply not set within the timeout | 503 | `prepare not drained` |

  A silent refusal is byte-identical to an accept so a stranger with a wrong
  key learns nothing (MycoQuest invariant 16). The key is never written to a
  log line, a marker, or a body; the `PrepareRequest` dataclass gives `key` a
  `repr=False` field so a logged request cannot leak it either.

### 4.2 The rule (`control/prepare.py`, pure stdlib)

```python
@dataclass(frozen=True)
class PrepareDecision:
    accepted: bool
    reason: str | None
    action: str            # "load" | "noop" | "none"
    visible: bool          # whether a refusal may be reported to the caller

def decide_prepare(*, room_ready: bool, state: State, loaded_bit: str | None,
                   bit: str, known: bool, when: str | None,
                   expected_key: str | None, key: str | None) -> PrepareDecision
```

Evaluated in this order; the first match wins:

1. `room_ready` false: refuse `no room loaded`, visible. The room is
   box-owned and the reason is not secret (MycoQuest 2.1).
2. `known` false (no package by that name in the registry), or `when` is not
   `admin`, or `expected_key` is empty, or `key` is `None` or differs from
   `expected_key`: refuse `bad key`, not visible. The attempt fires the
   observer record with that reason; the caller sees an accept-shaped 202.
   A Bit whose start condition is not `admin` or has no key cannot be
   prepared from the LAN (MycoQuest 7.2).
3. `state` is IDLE: accept, action `load`.
4. `state` is SETUP and `loaded_bit == bit`: accept, action `noop` (a second
   party joins the same lobby).
5. Anything else (LOADING, LOADED, SETUP with a different Bit, RUNNING,
   COMPLETING, UNLOADING): refuse `busy`, visible.

The key checked is the named Bit's `[start] key`, read off
`registry.packages[bit].config.start` (already parsed at scan time), never by
loading or importing the Bit. `resolve_config` is called only after the key
passes and only on the `load` action, so a disabled Bit reports its
`ManifestError` as a visible 409 reason the same way a `BitLoadError` does,
and only to a caller who held the key.

### 4.3 The authority (`control/prepare.py`)

```python
class PrepareAuthority:
    def __init__(self, game_server, registry, terrarium) -> None: ...
    def request(self, req: PrepareRequest) -> PrepareDecision
```

`request` builds the inputs (`room_ready` is `terrarium is None or
terrarium.state is TerrariumState.ROOM_READY`, so a roomless test
`GameServer` still works; `known`, `when`, `expected_key` from the registry
package), calls `decide_prepare`, and on `load` calls
`registry.resolve_config(bit)` then `game_server.load_bit(bit, config=cfg)`.
`ManifestError`, `KeyError`, `BitLoadError` and `InvalidTransition` become a
visible refusal with `str(exc)` as the reason. A no-op does nothing. Every
call ends with

```python
game_server.notify_prepare_requested(PrepareRequested(
    source=req.source, source_dev=req.dev, bit=req.bit,
    accepted=decision.accepted, reason=decision.reason))
```

`GameServer.notify_prepare_requested` is a one-line public wrapper over
`_notify("on_prepare_requested", record)`, the `notify_lobby` pattern, so
the Console's event log and any future observer see every attempt without
`control/` knowing the authority exists. The Console logs it as
`prepare <bit> from <source>: accepted | refused (<reason>)`; the reason for
a silent refusal is still logged locally (the Console is the operator's
trusted surface), never sent back over HTTP.

### 4.4 The drain (`devicelink/agent.py`)

`DeviceLinkAgent` gains `prepare_requests: queue.Queue | None` and
`prepare_authority: PrepareAuthority | None`, both `None` until boot wires
them, and `_drain_prepare_requests()` called from `poll()` right after
`_drain_start_requests()`. For each queued request it calls
`prepare_authority.request(req)`, copies `accepted`, `reason` and `visible`
onto `req.reply`, and sets `req.reply.done`. If no authority is wired the
request is answered as a visible refusal `prepare is not wired`. A raising
authority is logged and answered `prepare failed` (visible) so the handler
never waits out its timeout on a bug.

### 4.5 Console and markers

- `control/join_info.py`: `build_join_info` gains `prepare_url` on the
  `start` dict: `http://<lan_ip>:<www_port>/prepare?key=<key>&bit=<bit_name>`
  (no `dev`; the operator's own browser is loopback and becomes
  `terrarium`). Present only when `start` is present and `bit_name` is set.
- `console/static/join.js`: one more `lineWithCopy` under the start URL,
  labelled prepare.
- `harness/markers.py`: `PREPARE_URL`, printed by `_print_join_urls` right
  after `START_URL`.
- Boot: `_start_www_server` is unchanged; after it, `agent.prepare_requests
  = www.prepare_requests` and `agent.prepare_authority =
  PrepareAuthority(gs, registry, terrarium)`.

## 5. Players on bit_completed

### 5.1 Registration accessor

`RegistrationState.granted() -> list[tuple[str, str, RoleClass]]`: `(dev,
role_name, role_class)` for every current assignment in insertion order
(dict order), skipping `RoleClass.ROOM`. Read-only; the `assignments` dict
stays the storage.

### 5.2 Wire shape

`uplink/protocol.py`:

```python
def players_view(granted) -> list[dict]:
    # [{"dev": dev, "role": role, "class": "jam" | "scored"}]
    # RoleClass.JAM -> "jam"; every other class -> "scored"; ROOM never
    # reaches here (granted() drops it).

def bit_completed_event(result, bit_name="", bit_version="", *,
                        room_name=None, terrarium_config_version=None,
                        players=()) -> dict
```

`players` is always present on the event (an empty list when nobody was
registered). `class` derives from `RoleClass` alone; a UNIQUE or SHARED role
declared `scored = False` still reports `scored`, matching MycoQuest 7.2,
which never branches on the value.

`result` may be `null`: the Bit had no `result()` payload or its `result()`
raised (logged). The event is still sent because the players list, not the
result, is what credit reads.

### 5.3 When it fires

`UplinkAgent.on_state_change` sends `bit_completed` on entry to
`State.COMPLETING`, where `registration` is still populated and only the
tick-triggered `_complete()` path can arrive. It no longer sends on
`UNLOADING`, so `abort()` (which skips COMPLETING) emits no `bit_completed`:
an aborted round is not a completed round and credits nobody. The
`state_changed` events for both states are unchanged.

The reserved id `terrarium` is refused at hello and can never hold an
assignment, so it never appears in `players`; a test pins that.

## 6. Uplink config, identity, journal and boot

### 6.1 `[uplink]` in `terrarium.toml`

```toml
[uplink]
tenant_slug = "musical-mycology"
secret = "<64 lowercase hex characters, pasted from the MycoQuest admin site>"
url = "wss://..."          # optional; empty or absent means log-only
```

`control/terrarium_config.py` parses it into

```python
@dataclass(frozen=True)
class UplinkConfig:
    tenant_slug: str
    secret: str = field(repr=False)
    url: str = ""
```

and `TerrariumConfig.uplink: UplinkConfig | None`, `None` when the table is
absent. With the table present, `tenant_slug` must be a non-empty string and
`secret` must match `^[0-9a-f]{64}$`; `url`, when given, must be a string.
Each failure is a located `TerrariumConfigError` keyed `uplink.tenant_slug`,
`uplink.secret` or `uplink.url`, so a bad paste fails at boot, not at first
connect. The secret's message never includes the pasted value. The name
presented is `[terrarium] name`; it is never repeated in `[uplink]`.

### 6.2 Identity frame and resync

`UplinkAgent.__init__` gains keyword-only `identity: UplinkIdentity | None =
None`, `journal=None`, `lan_ip=None`. `UplinkIdentity(tenant_slug,
terrarium_name, secret)` is a frozen dataclass in `uplink/protocol.py` with
`secret` marked `repr=False`, plus `identity_frame(identity) -> dict`:

```json
{"event": "identity", "tenant_slug": "...", "terrarium_name": "...", "secret": "..."}
```

On every successful `transport.connect()` inside `maintain_connection`, the
agent sends, in order:

1. the identity frame, when `identity` is set (a test-only agent with no
   identity sends none, so every existing `test_link.py` assertion on
   `sent[0]` still holds);
2. the resync as today (`state_changed`, optional `room_loaded`,
   `registration_changed`), where `state_changed_event` gains a keyword
   `lan_ip` that is included in the dict only when the agent's `lan_ip`
   callable is set (a raising callable is logged and the key omitted);
3. the journal replay (6.4), only when `transport.durable` is true.

`state_changed` events sent for ordinary state changes carry no `lan_ip`;
only the resync does.

### 6.3 Transports

`Transport` gains `durable: bool`, meaning a frame handed to `send()` that
returns without raising has left the box on a real socket.

- `WebSocketTransport.durable = True`. Unchanged otherwise.
- `LogTransport` (new, `uplink/transport.py`): `connect()` sets `connected`
  true; `send()` logs the frame at debug through `wire_json.dumps` with the
  `secret` value replaced by `[redacted]`; `receive()` returns `None`;
  `durable = False`. It exists so a box with no broker URL still exercises
  the identity, resync and journal-append paths live, and so nothing is ever
  trimmed on its account.
- `FakeTransport.durable` defaults to `True` and is settable by tests.

### 6.4 The journal (`uplink/journal.py`)

```python
class Journal:
    CAP = 500
    def __init__(self, path: str, cap: int = CAP) -> None
    def append(self, event: dict) -> None
    def entries(self) -> list[dict]
    def clear(self) -> None
```

- Path: `<runs_dir>/uplink_journal.jsonl`, one event per line via
  `wire_json.dumps`, parent directory created on first append. `runs_dir`
  is the existing `--runs-dir` (default `runs`), the box's only persisted
  state today (`runs/<run-id>/procs.jsonl`). The file sits directly under
  `runs_dir`, not under a run id, because it must outlive the run that wrote
  it.
- `append` writes the line, then if the file now holds more than `cap`
  lines rewrites it to the newest `cap` (oldest dropped). Hours of a busy
  room is the working assumption in issue #103; 500 completed rounds is
  days.
- `entries` returns the parsed lines in file order; an unparseable line is
  logged with its line number and skipped, never fatal.
- `clear` truncates the file.

Agent behaviour:

- `_send_bit_completed` appends the event to the journal (when one is set)
  before any send attempt, whether or not the transport is connected. Then,
  as today, it sends the event if connected.
- After the resync, when `journal` is set and `transport.durable` is true:
  for each entry in `entries()` the agent sends it; if a send raises (the
  socket dropped mid-replay) the replay stops and the journal is left
  intact; once every entry has been sent, `journal.clear()`. Replay may
  repeat an event a real broker already saw (the live send before the drop,
  or a replay cut short); MycoQuest credit is idempotent (its invariant 13).
- With a non-durable transport the replay step is skipped entirely and the
  journal keeps growing to its cap.

### 6.5 Boot wiring (`harness/terrarium_boot.py`)

When `terrarium_config.uplink` is `None`, nothing changes. Otherwise, after
the Console and www server are up:

- transport: `WebSocketTransport(cfg.url)` when `url` is non-empty, else
  `LogTransport()`;
- journal: `Journal(os.path.join(runs_dir, "uplink_journal.jsonl"))` when
  run records are on, else `None` (with `--no-run-records` the box keeps no
  state, and the log says so once);
- agent: `UplinkAgent(gs, transport, registry=registry, terrarium=terrarium,
  identity=UplinkIdentity(cfg.tenant_slug, terrarium_config.name,
  cfg.secret), journal=journal, lan_ip=lan_ip)`;
- a `_pump_uplink(uplink)` helper calling `maintain_connection()` then
  `poll()`, invoked immediately after `console_agent.poll()` in every wait loop that
  already polls the Console (`_wait_in_setup`, `_serve_until_done`,
  `_wait_for_load`, `_wait_for_room_ready`, `_serve_roomless`) so the uplink
  ticks at the same cadence as the Console;
- one `markers.UPLINK` line at startup naming the mode (`log-only` or the
  URL with no credentials) and the tenant slug.

## 7. Testing

All offline, through the project venv, in the existing files plus two new
ones.

- `tests/test_www_server.py`: a drainer thread standing in for
  `DeviceLinkAgent` answers each queued `PrepareRequest`; assert 202 body on
  accept, 409 with the reason on a visible refusal, 202 accept-shaped body on
  a silent refusal, 503 `prepare not drained` when nothing answers within a
  shortened timeout, 503 when the queue is full, 404 when unwired, the
  loopback rule (`dev == "terrarium"`, source `web:terrarium`), and that the
  key appears in no response body.
- `tests/test_prepare.py` (new): the `decide_prepare` table row by row (no
  room, unknown Bit, non-admin start, no key, wrong key, IDLE, same Bit in
  SETUP, different Bit in SETUP, RUNNING); `PrepareAuthority` against a
  `BitRegistry.scan` of a tmp package with `[start] when = "admin"` and a
  key, on a `GameServer` with a stub terrarium: `load` reaches `load_bit`
  with the resolved config, `noop` leaves the engine untouched, a disabled
  package yields a visible 409 reason after a correct key, an
  `on_prepare_requested` record fires on every path including silent
  refusals, and the record's `repr` contains no key.
- `tests/test_devicelink_agent.py`: the drain answers the reply slot, the
  unwired case answers `prepare is not wired`, a raising authority answers
  `prepare failed`.
- `tests/test_join_info.py`: `prepare_url` shape, absent without a start
  key or Bit. `tests/test_terrarium_boot.py`: `PREPARE_URL` marker printed
  beside `START_URL`.
- `tests/test_registration.py`: `granted()` order, ROOM skipped, updated by
  release.
- `tests/test_protocol.py`: `players_view` class mapping,
  `bit_completed_event` always carries `players`, `identity_frame`,
  `state_changed_event` with and without `lan_ip`.
- `tests/test_link.py`: identity is `sent[0]` on every connect when set and
  absent when not; `bit_completed` is sent on COMPLETING with the players
  captured before release, not on UNLOADING; `abort()` sends no
  `bit_completed`; a `result()` of `None` or a raising `result()` still sends
  with `result: null`; the resync carries `lan_ip` from the injected
  callable; replay after resync on a durable `FakeTransport` sends the
  journal entries in order and clears the file; a non-durable transport
  replays nothing and clears nothing; a send that raises mid-replay leaves
  the journal intact; a `bit_completed` while disconnected is appended and
  replayed on the next connect.
- `tests/test_journal.py` (new): append and read back, cap keeps the newest,
  corrupt line skipped and logged, clear, missing file reads as empty.
- `tests/test_transport.py`: `LogTransport` reports connected, is not
  durable, redacts `secret` in its log line, and `receive()` is `None`.
- `tests/test_terrarium_config.py`: `[uplink]` absent gives `None`; missing
  `tenant_slug`, a secret of the wrong length or with uppercase, a
  non-string `url`, each a located error whose message omits the secret;
  the `repr` of `UplinkConfig` omits the secret.
- `tests/test_terrarium_boot.py`: no `[uplink]` builds no agent; a log-only
  config builds a `LogTransport`; `--no-run-records` builds no journal.
- Pinned invariants kept: `control/` imports nothing from `uplink/` or
  `harness/`; `uplink/` imports nothing from `harness/`; every persisted
  line goes through `wire_json.dumps`.

## 8. Deviations from MycoQuest section 7.2 (to mirror in mm-renquest)

1. `bit_completed` is no longer emitted on `abort()`. It fires at
   COMPLETING only, so an operator-aborted round credits nobody.
2. `result` may be `null` on `bit_completed`; MycoQuest's poll reads only
   `bit.name` and `players` and is unaffected.
3. The identity frame carries an `event: "identity"` discriminator alongside
   `tenant_slug`, `terrarium_name` and `secret`, so the broker can parse it
   like every other up-message.
4. `GET /prepare` can answer 503 (`prepare not drained` or `prepare queue
   full`); the app should show its "cannot reach the room" message for any
   status other than 202 and 409.
5. An unknown Bit name is a silent refusal (202, no reaction), the same as a
   bad key, so the route never enumerates Bits.
6. The resync `state_changed` event carries `lan_ip` now (7.2 listed it as a
   later slice).

## 9. Files touched (expected)

- `harness/www_server.py`, `harness/markers.py`, `harness/terrarium_boot.py`
- `control/prepare.py` (new), `control/engine.py`
  (`notify_prepare_requested`), `control/registration.py`,
  `control/join_info.py`, `control/terrarium_config.py`
- `devicelink/agent.py`
- `console/agent.py` (event log line for `on_prepare_requested`),
  `console/static/join.js`
- `uplink/protocol.py`, `uplink/link.py`, `uplink/transport.py`,
  `uplink/journal.py` (new)
- `terrarium.toml` (an `[uplink]` example, commented out),
  `docs/MM_TERRARIUM.md` (at closeout, via the deep-dive sync)
- tests as listed in section 7
