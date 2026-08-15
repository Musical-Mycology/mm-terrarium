# The Room simulator's service announcement, and the orphan that steals it

**Date:** 2026-08-14
**Status:** Design. Root cause found and reproduced against a headless O2 host;
the fix is specified here and not yet built.
**Repos touched:** `mm-terrarium` only. No change to `arco`, `o2` or
`o2litepy`: they are upstream-maintained sibling checkouts reached by
`PYTHONPATH`, never vendored, so a local patch would silently diverge and
vanish on the next pull. The two upstream defects section 3 records are
documented in `docs/MM_TERRARIUM.md` instead.

This document is a point-in-time design record, not a living doc: for current
behavior, constraints and known issues read `docs/MM_TERRARIUM.md`.

---

## 1. What was reported, and what it actually was

The 2026-08-13 live run left this in Arco's own `o2debug.log`:

```
O2 [4.953]: Warning: dropping message because /_o2/*/sv not from service provider,
    message is !_o2/o2lite/sv @ 0 by TCP "sim-room" 1 1 "" 0
```

The args decode as service name `sim-room`, exists=1, service=1, properties="",
send_mode=0: the Room simulator asking Arco to register it, and Arco refusing.
A player device announcing `ie1` in the same run registered fine and received
820 LED frames, so the failure was specific to the Room simulator.

`docs/MM_TERRARIUM.md` recorded it under *Not yet built / deferred* as
"Unresolved, and an O2-layer question rather than an mm-terrarium one." That
reading was wrong on the second half. The O2 layer behaves exactly as designed.
The bug is in this repo's process lifecycle.

## 2. Root cause

### 2.1 What O2 is enforcing

`o2/src/bridge.cpp:231-237`, in `o2_bridge_sv_handler`:

```c
Service_provider *spp = Services_entry::find_local_entry(serv);
if (spp) {
    if (spp->service != o2_message_source) {  // cannot replace
        o2_drop_msg_data("/_o2/*/sv not from service provider", msgdata);
```

`find_local_entry` resolves through `Services_entry::proc_service_index`
(`o2/src/services.cpp:284-300`), which matches **any** local provider once the
querying proc is the local process, bridges included. So the drop has exactly
one meaning: **something else already owned `sim-room` on that Arco, and it was
not this connection.** `services.cpp:134-137` states the rule in a comment: "it
is an error to replace an existing local service ... a bridge cannot coopt an
existing local service."

### 2.2 What the something else is

An **orphaned Room simulator from a previous run**. One was found live on the
development machine during this investigation:

```
PID 44777   PPID 1   (no controlling terminal)
  python -m harness.o2_shroom --dev sim-room --ensemble arco --no-join
```

That is `harness/terrarium_boot.py`'s `_O2SimulatorFactory` command line,
verbatim. PPID 1 means its parent was long gone and it had been reparented to
launchd.

Two properties turn an orphan from untidy into destructive:

1. **It never exits on its own.** `harness/o2_shroom.py:171` loops
   `while not client.released`, and a Room simulator receives `/release` only
   from a Control that is by definition no longer alive.
2. **o2litepy reconnects automatically.** `o2lite.py`'s `network_poll` (line
   912) opens a fresh connection whenever `_tcp_socket is None`, and
   `_id_handler` (line 601) re-announces every service on each connect. So the
   orphan re-attaches to the **next** Arco that starts and claims `sim-room`
   before the next run's own simulator is even spawned.

### 2.3 Why nobody noticed

`/_o2/*/sv` is fire-and-forget. o2lite sends it and never waits for an answer,
and O2's refusal is a log line on the **hub**, not a reply to the client. The
refused simulator therefore clock-syncs, prints its watch URL, and is
indistinguishable from a healthy one. Measured side by side in the reproduction
below:

```
refused simulator:   Watch the Shroom at http://127.0.0.1:49439/
                     clock synced at 10.890
accepted player:     Watch the Shroom at http://127.0.0.1:49458/
                     clock synced at 18.875
```

Control is equally blind: it addresses `/sim-room/leds` and the hub routes it
successfully, to the orphan. The frames are not dropped, they are **delivered
to a zombie from a previous run**, whose browser tab nobody is watching. The
live run's own simulator renders nothing, ever.

This is why the original finding suspected the Room simulator had never
received a frame. It had not. Neither had it lost them.

### 2.4 The reproduction

Arco cannot start without a controlling TTY, so the reproduction uses a
purpose-built headless O2 host (`o2_initialize` plus `o2lite_initialize` plus
`o2_clock_set`, no curses, no synthesis) in an ensemble deliberately **not**
named `arco`, and runs the real topology against it: a stand-in Control
claiming `actl` then `actl,game`, then `harness/o2_shroom.py --dev sim-room
--no-join`, then `harness/o2_shroom.py --dev ie1`.

| run | orphan present | result |
| --- | --- | --- |
| full topology | yes | `dropping message because /_o2/*/sv not from service provider` / `!_o2/o2lite/sv @ 0 by TCP "sim-room" 1 1 "" 0`, byte-identical to the demo log |
| full topology | no | zero drops; `sim-room`, `game`, `actl` and `ie1` all register, and all are removed at teardown |

The host's service timeline in the failing run shows the mechanism directly:
`sim-room` is registered at t=2.959 by the orphan, six seconds before the run's
own simulator is spawned, and the real simulator's announcement is refused at
t=10.276.

### 2.5 Why `ie1` is immune

A player device is hand-started in a foreground terminal and stopped with
Ctrl-C, so it dies with the terminal's process group, and it is not respawned
by any harness. Only the Room simulator is spawned per run by a parent that can
die without taking it along.

### 2.6 Where the orphans come from

Four leak paths, all in this repo:

| # | Path | Effect |
| --- | --- | --- |
| 1 | `control/boot.py:110` spawns the simulator inside `_bind_room_fast_path()`, but the "structural shutdown guarantee" at line 92 only calls `arco.shutdown()` | an unknown Bit, a `BitLoadError` or a `RoomBindingTimeout` leaks the simulator |
| 2 | that same handler is `except Exception`, which does not catch `KeyboardInterrupt` | Ctrl-C during boot leaks Arco **and** the simulator |
| 3 | `harness/terrarium_boot.py:134-140` constructs `ArcoSynthPool` and calls `start()` after `_boot()` has already spawned the simulator | if it raises, `build()` never returns, `main()` never binds `simulator`, and its `finally: shutdown(...)` never runs |
| 4 | a `Popen` child has no lifetime tie to its parent | any external kill of the parent orphans it, which is how agent-driven runs are actually terminated |

Path 3 is not hypothetical. `ArcoSynthPool.start()` is `arco.initialize()`,
which raises `TimeoutError`, and the `/host/clear` trap already documented in
`docs/MM_TERRARIUM.md` makes a second run on one Arco start fragile by design.

Path 1 is worth naming precisely, because the surrounding code was written to
prevent exactly this class of mistake: `boot()`'s docstring promises "one
try/except around the whole post-start section ... so a future failure mode
added to this section can't accidentally orphan the subprocess by forgetting
one." That guarantee was written for Arco and never extended to the simulator
the same function spawns.

## 3. Two upstream defects this exposed

Both are recorded here and in `docs/MM_TERRARIUM.md`, and neither is patched.

**3.1 A refused service announcement is silent on the client.** O2 logs the
drop on the hub; o2lite offers no acknowledgement, no error callback and no way
to query whether a service registration succeeded. A client that loses a
service race is fully functional in every observable respect except that
nothing addressed to it ever arrives. Section 4.2 works around this rather than
fixing it.

**3.2 o2litepy's discovery has no ensemble filter at all.**
`o2litepy/o2lite_disc.py:24` takes `ensemble` as a constructor argument and
never stores it. `py3discovery.py:74` browses `_o2proc._tcp.local.` and
`handle_new_service` (line 34) appends **every** host it resolves to
`discovered_services`, with no comparison against the requested ensemble
anywhere in the module.

The consequence is that an o2lite client joins whatever O2 host mDNS offers
first, in any ensemble, on any machine on the LAN. This was observed directly
during the investigation: the `--ensemble arco` orphan registered `sim-room` on
a host whose ensemble was `svprobe`, and unrelated clients from concurrent
sessions arrived in the same ensemble uninvited.

For this bug it widens an orphan's reach from "this run's Arco" to "any O2 host
on the LAN". Standing alone it is a venue-scale hazard: two Terrariums on one
network today would cross-connect, and `MM_TERRARIUM.md`'s "one Terrarium per
room" model assumes they do not. It deserves an upstream report to Roger.

## 4. What changes

### 4.1 Prevent the orphan

**4.1.1 `harness/o2_shroom.py` gains `--exit-with-parent PID`.**

A pure predicate, socket-free in the style `harness/shroom_client.py` already
established:

```python
def parent_is_gone(expected_ppid, getppid=os.getppid) -> bool:
    return expected_ppid is not None and getppid() != expected_ppid
```

It is checked inside both blocking loops: the clock-sync wait at line 146 and
the tick loop at line 171. Exiting returns through the existing
`finally: backend.close()` rather than calling `sys.exit` from inside the loop,
so the browser backend still shuts down cleanly.

The flag is opt-in and defaults to off, so a hand-run
`python -m harness.o2_shroom --dev ie1 --node TEST_PLAYER_NODE` is unchanged.
`_O2SimulatorFactory` always passes `os.getpid()`.

Comparing against a pid the parent stamped in, rather than watching `getppid()`
for a change, is deliberate: if the parent dies before the child reads its
argv, `getppid()` is already 1 and a change detector would wait forever.
Comparison against the recorded value is correct in either order.

This is the only guard that survives path 4 (an external kill of the parent),
which teardown ownership structurally cannot.

**4.1.2 `control/boot.py` shuts the simulator down on any post-spawn failure.**

`boot()` retrieves the handle from the factory with
`getattr(simulator_factory, "process", None)`, which is exactly what
`harness/terrarium_boot.py:145` already does with the same object, and states
the contract in the docstring: a `simulator_factory` that spawns a process
exposes it as `.process` with a `shutdown()`. A factory that spawns nothing,
including the bare `lambda: "sim-room-dev"` used throughout `tests/test_boot.py`,
keeps working untouched because `getattr` yields `None`.

The existing `except Exception` becomes `except BaseException`, so a Ctrl-C
during boot tears both subprocesses down instead of leaking both. The bare
`raise` continues to re-raise unchanged, so no exception is swallowed or
reshaped.

**4.1.3 `harness/terrarium_boot.py`'s `build()` cannot leak a spawned simulator.**

Everything after `_boot()` returns, which is the `room_audio` construction and
the `DeviceLinkAgent`, moves inside a `try/except BaseException` that shuts
down `factory.process` and `arco` before re-raising. This is the one path
`main()`'s `finally` cannot reach: `build()` never returned, so `main()` holds
no handles to tear down.

### 4.2 Make a refused announcement loud

**4.2.1 A new helper in `devicelink/o2_transport.py`.**

```python
def verify_service_ownership(o2lite, service, *, timeout=2.0,
                             clock=time.monotonic, sleep=time.sleep) -> bool
```

It registers `/<service>/_svcheck`, sends itself a nonce, and pumps
`o2lite.poll()` until the nonce returns or the timeout expires. `clock` and
`sleep` are injected so a test drives the timeout without spending real time,
matching the pattern `control/boot.py`'s `wait_for_room_binding` already uses.
It returns a bool and raises nothing: each caller decides what a failure means.

It works because of boundary rule 4: o2lite `send()` has **no local short
circuit**, so a message a client addresses to its own service leaves for the
hub and comes back only if the hub really routes that service to this bridge.
That property, which rule 4 documents as a cost, is here the measurement.

Rule 4 also says `game` and `actl` are inbound-only and that Control never
messages itself, and asks that it stay that way. This check is a deliberate,
documented exception: a **one-shot startup assertion**, not a steady-state
message path, sending one message before the tick loop begins. It will be
annotated as such at the call site and in the rule's own entry in
`docs/MM_TERRARIUM.md`, so it reads as an exception taken knowingly rather than
as erosion.

The helper lives in `o2_transport.py` because that module already exists to
encode the o2litepy facts that break the transport at runtime otherwise, it
never imports o2litepy (the caller injects an already-connected object), and
both consumers need it.

**4.2.2 Two consumers.**

`harness/o2_shroom.py` verifies its own `--dev` immediately after clock sync
and before entering the tick loop. On failure it prints a diagnostic naming the
cause and the remedy, and exits non-zero rather than serving a canvas that will
never light. The message must be specific enough to end the investigation on
the spot: another process already offers this service on the hub, look for a
stale `harness.o2_shroom`.

`O2LiteTransport.start()` verifies `game` after `set_services`, raising
`RuntimeError` in the same shape as its existing clock-not-synced guard.
`harness/terrarium_boot.py`'s `main()` already routes a `transport.start()`
failure through `shutdown()`, so nothing new is needed on that side. Control's
`game` service has the same exposure as a device's: an orphaned Terrarium
holding `game` would make every device silently unreachable.

**4.2.3 `FakeO2Lite` must model the refusal.**

The fake gains an explicit `refused_services` set: a service listed there never
loops a self-addressed message back. Without it the double would be more
permissive than the hub, and the ownership check would pass in every test while
failing live. That is precisely the failure boundary rule 5 was added for, on
the evidence of this same transport.

### 4.3 Documentation

`docs/MM_TERRARIUM.md` loses the unresolved *Not yet built / deferred* bullet
("Arco rejects the Room simulator's service announcement") and gains:

- the resolved finding, with the mechanism and the reproduction;
- both upstream defects from section 3 as recorded constraints, including the
  venue consequence that two Terrariums on one network would cross-connect
  today;
- the rule 4 exception noted in 4.2.1.

## 5. What deliberately does not change

- **`harness/room_simulator.py` (the websocket Room simulator) gets no guard.**
  It runs inside `async with websockets.connect(...)`, so when Control's
  devicelink server goes away the connection closes, `run()` returns and the
  process exits. It cannot orphan, and adding a guard would be dead code.
- **No patch to `arco/o2litepy` or `o2`.** Section 3 explains why.
- **Nothing touching Arco's TTY constraint.** That is separate work.
- **No skip flag on the ownership check.** See section 8.

## 6. Testing

Everything stays offline: no O2 network, no Arco server, no pyarco, no
o2litepy import. The suite's fully-offline property is load-bearing and pinned
by tests.

| test file | what it pins |
| --- | --- |
| `tests/test_o2_shroom.py` | `parent_is_gone` truth table: matching pid stays, changed pid exits, `None` never exits (the default hand-run case) |
| `tests/test_o2_transport.py` | ownership verification returns True for an owned service and False for a refused one; `O2LiteTransport.start()` raises `RuntimeError` on refusal; `FakeO2Lite` actually withholds the loopback for a refused service |
| `tests/test_boot.py` | the simulator is shut down for an unknown Bit, a `BitLoadError`, an unsupported room type and a `RoomBindingTimeout`; and on `KeyboardInterrupt`; and a bare-callable factory with no `.process` still boots |
| `tests/test_terrarium_boot.py` | `build()` shuts down both the simulator and Arco when room-audio construction raises |

## 7. Success criteria

1. Two `harness/o2_shroom.py` processes claiming the same dev: the second one
   reports that it does not own its service and exits non-zero, instead of
   serving a dark canvas.
2. Killing `terrarium_boot` by any means, including `SIGKILL`, leaves no
   `harness.o2_shroom` process behind.
3. A failure anywhere after the simulator is spawned, including Ctrl-C, tears
   down both Arco and the simulator.
4. The suite still runs fully offline, and still passes.
5. A live o2lite run shows `sim-room` registered on Arco and the Room simulator
   receiving `/sim-room/leds`.

Criterion 5 needs a TTY and is the only one that cannot be checked from an
agent-driven session. **It is separate from whether the Room's light animates.**
Nothing in `bits/test_bit.py` emits a cue targeting the Room, so its declared
`aurora` reaches one static hue and holds. Frame *delivery* is the measurement
here, not visible motion.

## 8. Risks

**A false negative on the ownership check would stop a working run.** This is
accepted deliberately rather than mitigated with a skip flag. A loud stop is
strictly better than the dark canvas this cost a live-demo debugging session,
and a skip flag is a way to reintroduce exactly the silent failure the check
exists to remove. The 2 s timeout runs after clock sync has already proved the
hub responsive, and the measured round trip in the reproduction was immediate.

**`getattr(factory, "process", None)` is duck-typing.** It is chosen over
changing `simulator_factory`'s `Callable[[], str]` contract because that
contract is set by the Room-concept spec and relied on by every existing
`tests/test_boot.py` case. The looseness is contained: one `getattr`, one
documented attribute, and `terrarium_boot.build()` already reads the same
attribute off the same object.

**Pid reuse could defeat `parent_is_gone`.** A recycled pid landing on exactly
the recorded parent value within the life of one run is not a realistic risk on
a venue box, and the failure mode is a simulator that outlives its parent,
which is where we already are.
