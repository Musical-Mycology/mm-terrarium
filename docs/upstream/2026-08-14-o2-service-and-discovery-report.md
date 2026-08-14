# Two O2 / o2litepy observations, for Roger Dannenberg

**From:** mm-terrarium (Musical Mycology)
**Date:** 2026-08-14

These are reports, not proposals. Both were found while debugging a live-demo
bug in our own repo (mm-terrarium), and both sit upstream of it, in O2 and
o2litepy. Neither is patched here -- we don't vendor either library, and a
local patch would silently diverge on the next pull. We are not suggesting a
fix or a preferred design for either one; we're describing what we observed,
what it cost us, and what we did about it on our side. Full write-up, with
more surrounding detail than fits here, is in our
`docs/superpowers/specs/2026-08-14-room-simulator-service-collision-design.md`
(section 3 has the concise statement of both; section 2 has the full
investigation).

## 1. Defect 1: a refused service announcement is silent on the client

O2 refuses a second claimant of a service name. `o2/src/bridge.cpp:231-237`,
in `o2_bridge_sv_handler`, drops the announcement and logs it:

```
O2 [4.953]: Warning: dropping message because /_o2/*/sv not from service provider,
    message is !_o2/o2lite/sv @ 0 by TCP "sim-room" 1 1 "" 0
```

That log line is on the hub. `/_o2/*/sv` is fire-and-forget: o2lite sends it
and never waits for a reply, and o2lite offers no acknowledgement, no error
callback, and no way to query whether a registration actually took. A client
that loses that race is fully functional in every observable respect except
that nothing addressed to it ever arrives. It clock-syncs normally, serves its
UI normally, and receives nothing, forever. Nothing in the client-side API
distinguishes it from a client that won.

We reproduced the refusal directly: two `harness/o2_shroom.py --dev
sim-room` processes claimed the same service name against one hub (a
headless O2 host standing in for Arco, since Arco requires a controlling TTY
and would not start in our reproduction harness). The first registered at
t=2.959; the second's announcement was refused at t=10.276, and the drop
line above is byte-identical to the one from our original live-demo log.

Nothing distinguishes a refused client from an accepted one from the
outside. We confirmed that side by side with a refused simulator and an
accepted player (`ie1`): both printed a watch URL and reported a synced
clock, with nothing in either log to mark the difference. And a refused
claim does not drop the messages addressed to it, it reroutes them: in the
original live-demo run, Control's frames to `/sim-room/leds` reached the
earlier, orphaned claimant every time, while the run's own simulator, whose
announcement had been refused, rendered nothing, ever. There was no way,
short of comparing rendered output, to tell a refused client from an
accepted one from the client side.

**What we did about it (workaround, not a fix):**
`devicelink/o2_transport.py`'s `verify_service_ownership` (line 119) makes the
refusal observable, at the application layer, at startup. It registers a
handler on `/<service>/_svcheck`, sends itself a nonce over TCP
(`send_cmd`), and pumps `o2lite.poll()` until either the nonce comes back or a
2-second timeout expires. It works specifically because o2lite's `send()` has
no local short circuit: a message a client addresses to its own service does
not resolve locally, it leaves for the hub, and it returns to the client only
if the hub actually routes that service back to this connection. That
property costs every in-process consumer an unnecessary round trip -- it's
documented as a boundary rule in our own architecture doc precisely because it
is a cost we otherwise avoid -- but it is exactly the measurement this check
needs, and we could not find another way to get one from outside O2 itself.
Both of our o2lite clients (the Room simulator harness and Control's
transport) call it once, immediately after clock sync and before doing
anything else with the service, and treat a failed check as a hard stop
rather than a degraded run.

## 2. Defect 2: o2litepy's discovery has no ensemble filter

`o2litepy/o2lite_disc.py:24` takes `ensemble` as a constructor argument and
never stores it anywhere. `py3discovery.py:74` browses
`_o2proc._tcp.local.` over mDNS, and `handle_new_service` (line 34) appends
every host it resolves to `discovered_services`. We read the whole module
looking for an ensemble comparison and found none.

The consequence: an o2lite client joins whatever O2 host mDNS offers first on
the local network, regardless of that host's ensemble. We observed this
directly during the same investigation -- a client started with
`--ensemble arco` registered its service on a host whose ensemble was
`svprobe`, and unrelated clients from other concurrent sessions on the same
network arrived in our `arco` ensemble uninvited, with nothing in either
client's own configuration asking for that.

## 3. Why the second one is worth attention on its own

For the bug we were chasing, defect 2 is what turns a same-machine mistake
into a network-wide one: it widens an orphaned client's reach from "the Arco
process that spawned it" to "any O2 host mDNS can find on the LAN." But it
stands on its own as a venue-scale hazard independent of that bug. Our
deployment model is one Terrarium install per room. Two Terrariums running on
the same physical network today would cross-connect -- any device or
simulator on one could be picked up by the other's hub, or vice versa, purely
on the basis of which one mDNS answers first -- and there is nothing in
o2litepy's discovery path that would stop it. That is the part we think
deserves your attention even if defect 1, on its own, is judged acceptable
behavior for O2's service model.

## 4. What we did about it

All three of the following are application-layer workarounds in mm-terrarium.
None of them touch O2, o2lite, or o2litepy, and none of them close the
underlying gaps described above -- they only limit what those gaps cost us.

- **`verify_service_ownership`** (section 1, above): makes a refused
  announcement loud instead of silent, for our own two O2 clients.
- **`harness/o2_shroom.py --exit-with-parent PID`**, backed by
  `parent_is_gone()`: our Room simulator harness previously had no way to
  detect its own orphaning and would run forever once its parent process
  died, waiting on a `/release` message only a live parent would ever send.
  With this flag it compares its current parent pid against the one recorded
  at launch on every iteration of its wait loops and exits as soon as they
  differ. This closes the specific leak that let an orphaned simulator
  survive long enough to win defect 1's service race against the next run's
  own simulator -- it does not touch the race itself.
- **Teardown ordering in `control/boot.py` and
  `harness/terrarium_boot.py`**: the boot sequence now unwinds every
  subprocess it has started so far, including the simulator, on any failure
  after that subprocess is spawned, using a single guarded teardown stack
  rather than a shutdown call that only knew about Arco. This reduces how
  often our own code creates orphans in the first place -- the raw material
  both defects act on. It is a lifecycle fix on our side, not a change to
  discovery or service registration.
