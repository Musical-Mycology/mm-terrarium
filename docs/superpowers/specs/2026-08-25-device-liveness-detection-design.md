# Device liveness detection: reaping a stale DevicePool/registration entry

Closes the entry under *Not yet built / deferred* in `docs/MM_TERRARIUM.md`:
*"A stale device entry survives an ungraceful disconnect."* A Tuneshroom (or
simulated device) that crashes, loses power, or walks out of range without
sending a clean release leaves its `DevicePool` entry -- and, if it held a
role, its registration slot -- sitting there forever, because Control is an
o2lite **client**, not the O2 host: it never holds a socket to a device, and
o2litepy exposes no per-peer liveness at all.

Baseline: **1295 passed, 1 skipped**, fully offline (`.venv/bin/python -m
pytest tests -v`; a fresh worktree needs `ln -s
/Users/chris/projects/mm-terrarium/.venv .venv` first -- see `docs/MM_TERRARIUM.md`
"Landed subsystems").

## 1. The asymmetry the deep-dive entry assumes is smaller than it looks

The deferred entry frames this as o2lite-specific, with the websocket
transport implied to already handle it via a real TCP socket. Reading
`devicelink/server.py` shows that isn't true in practice:

- `DeviceLinkServer._handle()`'s `finally` block, which runs when a
  connection's read loop ends, does exactly one thing: `self._clients.discard
  (connection)`. It never touches `self._devs` (the dev-id -> connection
  map), never touches `DevicePool`, never calls `RegistrationState.release`,
  and never notifies anything.
- `drop_dev(dev)` is defined on **both** `DeviceLinkServer` and
  `O2LiteTransport`, satisfying the shared transport interface `DeviceLinkAgent`
  expects -- and is called from **nowhere** in the codebase. It is dead code
  today, on both transports.
- `harness/terrarium_boot.py`'s `_LifecycleLogger` already documents the
  consequence in its own docstring: *"DevicePool never drops a device... this
  set only grows."*

So the socket layer on websocket *knows* a connection died (its read loop
returns), but nothing wires that fact into engine state. The practical gap
is not "o2lite has no detection and websocket does" -- it's "neither
transport frees a stale entry today," and o2lite additionally has no
transport-level signal to wire up even if something tried. That reframing is
why this design is a single mechanism above both transports, not two
transport-specific fixes.

## 2. Mechanism: a device-initiated heartbeat riding the existing `/game/hello`

No new wire verb. `/game/hello` already exists, already round-trips through
`DeviceLinkAgent._on_hello` -> `GameServer.hello` -> `DevicePool.hello` on
**both** transports identically, and a repeat hello is already harmless
there (`bind_dev` re-binds to the current connection, the room blob resends
-- both idempotent). A device's own client loop resends hello on a fixed
interval; Control tracks the most recent contact per device and reaps
whatever falls silent for too long.

Two knobs, on two different sides of the wire, each following an existing
pattern rather than inventing a new one. `stale_timeout` (Control-side reap
threshold, default **15.0s** -- three missed heartbeats, the same
generous-multiple-over-the-interval shape `_MAX_CLOSING_FRAMES` already uses
relative to a session's fade time) is a `BootConfig` field, same as
`cue_horizon`, and reaches `DeviceLinkAgent` the same way `horizon` already
does: a constructor parameter (`stale_timeout: float = 15.0`) that `poll()`
passes to `self.game_server.reap_stale(self._stale_timeout)` every tick --
`GameServer` itself stays stateless about the value, matching how it
receives `cue_horizon` at construction rather than `DeviceLinkAgent`
threading it through per call. `heartbeat_interval` (device-side resend
cadence, default **5.0s**) is *not* a `BootConfig` field -- the harness
device clients are separate subprocesses with their own `argparse` setup
(`o2_shroom.py --dev ... --node ...`), not sharing Control's config object,
so it becomes its own `--heartbeat-interval` flag on each of them, the same
way `--join-retry` already is.

**Any inbound traffic counts as proof of life, not just hello.** A device
mid-gesture-stream is obviously alive; requiring a redundant hello alongside
real traffic would be pointless. `DeviceLinkAgent._handle()` is the one
choke point every inbound frame already passes through on both transports,
so it is where liveness is touched, not just `_on_hello`.

Alternatives considered and rejected: a Control-initiated ping/pong would
need a new wire verb implemented in every device client (this repo's
harness simulators today, `mm-tuneshroom`'s Dart client eventually) for
symmetric benefit, and doubles steady-state traffic per device for no
detection this shape doesn't already provide. A registration-level TTL
(the role lapses, not the device) only covers devices that already hold a
role, missing the SETUP-but-never-joined case, and would introduce a second,
narrower expiry concept sitting next to `DevicePool`'s. Reusing hello covers
both cases with zero new protocol surface.

## 3. Data model

`control/device_pool.py`:

- `DeviceInfo` gains `last_seen: float`.
- `DevicePool.touch(dev, now)` updates `last_seen` for a known dev; a no-op
  for an unknown one (mirrors `known`/`get`'s tolerance of an absent dev
  rather than raising).
- `DevicePool.hello(...)` takes a `now` and calls through to the same update,
  so hello and touch share one code path.
- `DevicePool.stale(now, timeout) -> list[str]` -- pure query, same style as
  `all()`: returns dev ids whose `last_seen` is older than `timeout`,
  mutates nothing.
- `DevicePool.remove(dev)` -- the method that doesn't exist today. Drops the
  entry outright, not a tombstone: a device that reconnects later says hello
  again and is indistinguishable from a first-time connection, which is
  correct -- there is nothing to preserve about a device that was never
  cleanly released.

`control/registration.py` needs no change. `RegistrationState.release(dev)`
already frees the slot and decrements the count; reaping is just a new
caller of it.

## 4. The reap algorithm

New `GameServer.reap_stale(timeout: float) -> list[str]`, called every tick
from `DeviceLinkAgent.poll()`. `poll()` is the right driver because it is
the one loop `harness/terrarium_boot.py`'s `_serve_until_done` already runs
unconditionally across every engine state -- including the SETUP-hold wait,
where `gs.tick()` itself is never called because it's a no-op outside
RUNNING, but devices can absolutely go stale while just sitting in the
waiting room before their first join.

```
now = self._clock()
stale = self.devices.stale(now, timeout)
room_devs = set(self.room.bound.values()) if self.room is not None else set()
released_any = False
removed_any = False
for dev in stale:
    if dev in room_devs:
        continue  # see section 5 -- Room devices are explicitly excluded
    if self.registration is not None and dev in self.registration.assignments:
        self.registration.release(dev)
        released_any = True
        if self.on_release:
            guarded(self.on_release, dev)
    self.devices.remove(dev)
    removed_any = True
if released_any:
    self._notify("on_registration_change")
if removed_any:
    self._notify("on_devices_change")
```

Batched once per call, not once per device -- the same shape `_unload()`
already uses when it releases every assigned device in one pass. This is
also why the role/capacity slot frees **immediately and synchronously**
(`registration.release` runs before `on_release` fires), while the visible
light fade -- if the transport wires one up, which `DeviceLinkAgent` does --
plays out asynchronously afterward exactly as a graceful release's fade
already does. A new player can join the freed slot the instant it's freed,
without waiting for the stale device's fade to finish.

**Adjacent fix folded in:** `_finish_release` in `devicelink/agent.py`
currently tears down `bridges`/`_universes`/`_last_frames`/`_closing`/
`_last_breath` and sends `/<dev>/release`, but never calls
`self.server.drop_dev(dev)` -- so even a normal, graceful release leaves a
dead connection object in the transport's `_devs` map forever (a harmless
but permanent no-op send on o2lite; a caught-and-logged exception on every
subsequent send attempt on websocket). Since reaping is the first real
caller of "this dev is definitively gone," this change wires `drop_dev(dev)`
into both `_finish_release` and the new reap path.

## 5. Room devices are explicitly out of scope for this design

A `RoleClass.ROOM` device is also a `DevicePool`/`registration.assignments`
entry, so `reap_stale` would technically apply to it -- but releasing it the
same way as a player would only clear `registration.assignments`, not
`self.room.bound[fixture]`, leaving `RoomBridge`/`AudioBridge` feeding a
fixture whose device no longer exists. Properly closing that also touches
`RoomBindingRegistry`'s persisted binding, which has its own already-deferred
item (`RoomBindingRegistry.save()/load()` implemented and tested but not
called from `boot()`). Stacking a liveness decision on top of an
admittedly-incomplete persistence story risks a half-designed interaction
between two separate open questions.

This design therefore has `reap_stale` skip any dev present in
`self.room.bound.values()` unconditionally -- `last_seen` tracking still
happens on it (so a future Room-liveness design has data to build on), but
nothing is ever reaped for it. Documented here rather than silently decided:
a stale Room binding is a known, named gap this spec does not close.

## 6. Heartbeat carrier: the harness device clients

`harness/shroom_client.py`'s `hello()` is already a pure, socket-free
message-builder (`self._up("hello", "s", [self.dev])`), reused unmodified.
The clients that own a real send loop gain the resend:

- `harness/o2_shroom.py` -- its `main()` loop already ticks on a cadence
  (draining gestures, feeding the synthetic tilt sweep); resending `hello()`
  every `heartbeat_interval` seconds is one more per-iteration check against
  a locally-tracked last-sent time, using the o2lite clock it already reads
  for everything else in that loop.
- `harness/room_simulator.py` -- same shape; it already sends `hello` once
  at connect and never `join` (the Room binds by pre-recorded dev id, not by
  registration), so it gains the identical periodic resend.

`mm-tuneshroom` (the Dart/Flutter client) is a separate repo and no real
Tuneshroom hardware exists yet (`docs/MM_TERRARIUM.md`, *Not yet built*:
"No hardware exists"). This spec does not touch it. The needed change there
-- call `hello()` on a timer alongside the existing one-shot send -- is a
cross-repo follow-up, the same relationship `devicelink/protocol.py`'s
docstring already documents for its Dart counterpart contract
(`lib/link/envelope.dart`).

Test doubles (`FakeO2Lite`, the websocket in-process fakes used by
`tests/test_devicelink_agent.py` and friends) need no heartbeat-resend
logic: tests exercise `reap_stale`/`stale()` directly against an injected
clock, the same pattern `verify_service_ownership`'s own tests and
`control/boot.py`'s `wait_for_room_binding` tests already use to exhaust a
timeout with no real time spent.

## 7. Observability

`harness/terrarium_boot.py`'s `_LifecycleLogger` currently only prints
*appearances* in `gs.devices.all()` (`"device hello: <dev>"`), never
departures from `DevicePool` -- there was never a departure to print before
this. It gains `"device timed out: <dev>"`, diffed the same way appearances
already are (a dev present last `on_devices_change` and absent now, with no
matching `"device released: <dev>"` already printed for it via the
assignments diff -- a timed-out player prints **both** lines if it held a
role, "released" from the assignments diff and "timed out" from the devices
diff, which is accurate: both things happened). The whole point of this
feature is turning a silent failure into an observed one, so the
operator-facing log should say so plainly.

Console and uplink need **no new code**. `ConsoleAgent._devices_view()`
reads `gs.devices.all()` directly and the uplink's registration-count
snapshot reads `registration.counts()` directly; both already refresh on
`on_devices_change`/`on_registration_change`, which `reap_stale` already
fires. Removing the entry from `DevicePool` is sufficient for both to stop
showing a device that no longer exists.

## 8. Testing

New tests, colocated with the existing `DevicePool`/`GameServer` coverage:

1. **Slot freed on timeout.** A device joins a scored role, an injected
   clock advances past `stale_timeout` with no further traffic, `reap_stale`
   runs: the role's count in `registration.counts()` drops, the dev is gone
   from `devices.all()`, `on_release` fired (assert via a recording fake,
   the same style `test_devicelink_agent.py` already uses for the graceful
   path), and both `on_registration_change`/`on_devices_change` fired once
   each, not once per device.
2. **Un-joined device also reaped.** A device says hello, never joins,
   goes stale: removed from `DevicePool`, no `on_release` call (nothing was
   ever assigned), `on_devices_change` fires, `on_registration_change` does
   not.
3. **Traffic other than hello resets the clock.** A device sends `/game/tilt`
   after its last hello; `stale()` reads it as live at a time a hello-only
   read would have called stale.
4. **Room devices are excluded.** A dev bound to a Room fixture goes stale;
   `reap_stale` leaves `registration.assignments`, `room.bound`, and
   `DevicePool` untouched for it.
5. **`drop_dev` wiring.** Both the graceful `_finish_release` path and the
   new reap path call the transport's `drop_dev`, asserted against a
   recording fake transport (extending the existing `O2LiteTransport`/
   `DeviceLinkServer` test doubles, which already track calls this way).
6. **Doc update is prose**, reviewed not tested, same as every other closed
   entry in `docs/MM_TERRARIUM.md`.

The suite must stay fully offline and at or above **1295 passed, 1 skipped**.

## 9. What this deliberately does not do

- Does not touch `mm-tuneshroom` (separate repo, no hardware to exercise it
  against yet -- see section 6).
- Does not design Room-device liveness (see section 5) -- that stays a named
  gap, not a silent one.
- Does not add a Control-initiated ping/pong path; section 2 records why it
  was considered and rejected.
- Does not change `_MAX_CLOSING_FRAMES` or the closing-fade mechanism
  itself -- a reaped device's fade (when it has one) reuses that machinery
  exactly as a graceful release does, with no new teardown path.

## 10. Success criteria

1. A device that stops sending anything for `stale_timeout` is removed from
   `DevicePool`; if it held a role, the slot frees immediately and the
   existing closing-fade path plays out.
2. Any inbound traffic, not just hello, counts as proof of life.
3. A Room-bound device is never reaped by this mechanism (documented gap,
   not silently dropped).
4. Console and uplink reflect a reaped device's departure with no code
   changes of their own, via the existing observer hooks.
5. `drop_dev` is called on both the graceful and stale-reap release paths,
   on both transports.
6. `docs/MM_TERRARIUM.md`'s "A stale device entry survives an ungraceful
   disconnect" entry is struck through with what landed, matching the
   file's existing convention for closed items.
7. Suite fully offline, at or above 1295 passed, 1 skipped.
