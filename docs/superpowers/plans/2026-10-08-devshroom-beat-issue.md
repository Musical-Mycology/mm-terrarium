# Beat heartbeat: device side (contract v4)

Victor, this is the firmware half of the problem you raised: the device cannot tell when Terrarium is gone. mm-terrarium has the Control side, the Testshroom and the contract kit done. mm-devshroom needs the device side.

- Spec: `mm-terrarium` `docs/superpowers/specs/2026-10-08-bidirectional-heartbeat-design.md` (sections 4 to 6 are the device contract, 9 the prerequisite, 10 and 11 the bench and risks).
- Guide: `mm-terrarium` `docs/device-contract-guide.md` (rules 9 and 10, firmware checklist items 10 to 15, section 8 item 7).
- Reference state machine to port: `mm-terrarium` `harness/beat_link.py` (`BeatLink`). It is pure, with no sockets and no clock of its own: you feed it local time in seconds (never O2 time, which goes away during a relink) and carry out the actions it returns.
- Contract export: `tools/export_contract.py`, `CONTRACT_VERSION = 4`.

Rollout order does not matter. A device that never gets a beat reply stays in legacy mode and keeps today's 5 s hello.

## 1. Prerequisite: fix the mDNS discovery hang first

Land this before or with the beat firmware. Quoting spec section 9:

> In `o2ldisc_poll()` (`lib/o2/o2liteesp32.cpp:181-203` on mm-devshroom `origin/main` `cce3dec`), both `continue` statements (lines 190 and 202) skip advancing `r`, so a first mDNS result that is not a usable Arco (another ensemble's `_o2proc._tcp`, or a stale or renamed record such as "arco (2)") spins the main loop forever, freezing the lights with it. It is upstream o2lite code (rbdannenberg/o2 `src/o2liteesp32.cpp` lines 182 and 194, present since 2021), reported to Roger on 2026-09-16 and unfixed in both upstream and the vendored copy.
>
> The heartbeat makes it worse. Today discovery runs at boot and after a TCP error; with section 6.1 it runs after every lost link. Right after a Terrarium restart the old Arco's record can still be cached or re-advertised under a renamed instance, which is exactly the result that hangs, so the epoch-change recovery would freeze the device instead of recovering it.

Do this:
- Iterate as `for (r = results; r; r = r->next)`, or advance `r` before each `continue`.
- List it in `lib/o2/VENDORED.md` as a local patch, next to the existing ones.
- Chris sends the same change to Roger as a follow-up on the existing report. If Roger fixes it upstream before you start, re-vendoring replaces the local patch.

## 2. What to build

Port `harness/beat_link.py`. Timing numbers come from the export's `lifecycle` block (`beat_interval_s` 1.0, `beat_jitter_s` 0.1, `link_lost_s` 3.0, `grace_s` 15.0, `hello_interval_s` 5.0).

### State table (taken from the code)

States: `down` (no transport, never linked, display untouched), `linking` (transport up, no reply yet on this link), `linked` (a reply arrived on this link), `looking` (lost, white pulse over the held frame), `solo` (lost past the grace window, aurora).

| Input | Condition | Actions | New state |
|---|---|---|---|
| link up (transport connected) | any | send hello, reset `seq` to 0, send beat 0, schedule next beat (interval plus jitter) and next legacy hello (5 s), set last-heard to now, clear `armed` | `down` becomes `linking`; every other state stays (looking and solo stay until a reply) |
| link down (socket error) | was `linked` | stop beats and hello, clear `armed`, remember `lost_since = last_heard` | `looking` |
| link down | was `linking` | stop beats and hello, clear `armed` | `down` |
| link down | was `looking`, `solo` or `down` | stop beats and hello, clear `armed` | unchanged |
| any message from Control | link is up | `last_heard = now` | unchanged |
| beat reply (`seq`, `epoch`) | link is not up | ignore it | unchanged |
| beat reply | link is up | count it as a Control message; if `seq` is still remembered, `rtt_ms = now - sent_at[seq]`; if `epoch` differs from the stored one (and one is stored): drop role, round id and validation, then send hello; store `epoch`; set `armed`; clear `lost_since` | `linked` |
| tick | up, `armed`, `linked`, and `now - last_heard >= 3.0` | drop the transport, clear `armed`, stop beats and hello, `lost_since = last_heard`. Send nothing (no beat, no hello) | `looking` |
| tick | otherwise, link up | send a beat if one is due (reschedule from the due time, so a late tick neither drifts nor bursts); send hello if one is due and not `armed` | unchanged |
| tick | `looking` and `now - lost_since >= 15.0` (evaluated on every tick, link up or not) | show Solo | `solo` |

Details that are easy to get wrong:
- `seq` wraps at 2^31. Remember send times for the last 32 unanswered seqs only.
- The stored `epoch` survives a relink. Only a reply on a later link can show it changed.
- A transport that comes up while `looking` or `solo` does not leave that state. Only a beat reply does.
- `rtt_ms` goes out as the third arg of the next beat. It is 0 until the first reply.

### Checklist

1. **Send `/game/beat dev seq rtt_ms` over UDP** every 1 s, each gap drawn uniformly from 0.9 to 1.1 s. Send hello (TCP) first on each link, then beat 0. Typespec `sii`; `si` is also accepted. Use the four-argument hello as today.
2. **Arm on the first `/<dev>/beat` reply** (`is`: seq, epoch) on the current link, never on the TCP connect. Until then keep the 5 s hello. After arming, stop the repeated hello.
3. **Treat any down message as proof of life**, not only beat replies. `/leds`, `/role`, `/room`, `/handshake` and the rest all reset the 3 s timer.
4. **At 3 s of silence after arming, show Looking and drop the transport.** Close the o2lite TCP socket and clear the bridge id so the 2 s mDNS rediscovery starts now. o2lite has no public call for this: `disconnect()` (`lib/o2/o2lite.c:772`) is non-static but not in the header, so either declare it or add a small `o2l_disconnect()` wrapper (a candidate upstream change for Roger). Stop beating.
5. **At 15 s, show Solo.** Keep trying to relink.
6. **On a new epoch: drop the role, round id and validation, and send a fresh hello.**
7. **Keep role state through a link loss (rule 9).** Looking and Solo are display states only. Remove the `g_device_registered = false` reset (`src/o2_test/main.cpp:85-88` on `origin/main` `cce3dec`). Only a changed epoch drops the role.
8. **Call `esp_wifi_set_ps(WIFI_PS_NONE)`.** Modem sleep may hold received data up to one DTIM period (typically 100 to 300 ms; confirm against the ESP-IDF docs on the bench). With it off, the 3 s window tolerates two lost beats plus jitter.
9. **Replay contract export v4** (details below).

### Behaviours the implementation and review turned up

- **Solo is timed from the last Control message**, not from when the link was declared lost and not from a socket error. That is the same moment as Control's 15 s reap. `BeatLink` sets `lost_since = last_heard` on both paths into Looking.
- **The tick that declares Looking sends no beat** (and no legacy hello). Check for loss before the send logic.
- **Instant Looking on a socket error is allowed, not required.** The contract requires Looking only after 3 s with nothing from Control. `BeatLink.link_down` shows it at once; a simpler firmware may wait for the 3 s timer.
- **Compare the epoch on every `/<dev>/beat` reply, whatever its seq**, including a reply to a seq you already had answered. `beat_epoch_change_rehellos` delivers the new epoch exactly that way.
- **Any message from Control is proof of life**, not only beat replies (checklist 3).
- **Integration trap (found in the Python Testshroom).** If you detect a relink by watching o2lite's bridge id change, then after your own drop (Looking) you must treat your own link as down. Otherwise a fast reconnect that lands on the same bridge id is never seen as a new link, and the device stays Looking forever. In o2litepy, `tcp_close` sets the bridge id to -1. In the C o2lite, `disconnect()` in `lib/o2/o2lite.c` clears `o2l_bridge_id` and `tcp_sock`. What to do: when you drop the transport yourself, also reset your remembered link state (your "last seen bridge id" or "link was up" flag), so the next valid bridge id counts as a new link and runs the link-up path (hello, `seq` 0, beat 0).
- **Rule 9 stays.** After a relink inside the 15 s window Control repaints your current `/leds` frame and re-sends no `/role`, `/validated` or `/room`. You must still have them.

### Replaying export v4

- Bump the version guard in your replay tests to 4.
- Accept the string `"*"` for `rtt_ms` in `expect_out`.
- Four new scenarios, all with `device.beats: true`: `beat_reply_echo`, `beat_link_lost_looking`, `beat_relink_within_grace`, `beat_epoch_change_rehellos`. They use the new `expect_link_state` step (`linked`, `looking`, `solo`).
- The 18 older scenarios gain `"beats": false`. Their recordings hold no `/$DEV/beat` reply, so a beat-capable device never arms in them, keeps the 5 s hello and never shows Looking. Your runner ignores the device's `/game/beat` outputs there.
- A device that shows Looking closes its own link. The runner treats that as the link going down until the scenario's next link step brings it up.
- `docs/device-contract-guide.md` section 2 and rule 10 list what each scenario checks and at which times.

## 3. Bench (real Rev 1 board, with Chris)

Spec section 10, bench row. Measure detection time on both sides for each:
- AP power-off.
- Terrarium kill.
- Cable pull.

Plus:
- **Second O2 host.** Advertise another ensemble's `_o2proc._tcp`, or a renamed "arco (2)", first on the LAN, then restart Terrarium. The device must skip it and relink, not hang. This proves the section 1 fix.
- **Stale clock after a forced disconnect** (spec section 11). o2lite resets `clock_synchronized` only in `o2l_clock_initialize`, not on disconnect, and does not reset its ping schedule (`lib/o2/o2lite.c`, clock ping around 1147-1160). A relinked device may stamp gestures with a stale offset. Check the offset after a forced relink. If it is real, re-initialise the clock on relink and add it to the o2lite defect list for Roger.
- **False Looking on a busy AP.** With power save off the 3 s window has margin. If the bench shows false Looking on a venue AP, tell us: the first knob is raising `LINK_LOST_S`, before touching the beat interval.

## 4. Done when

- The prerequisite patch is in and listed in `lib/o2/VENDORED.md`.
- Export v4 replays green, including the four beat scenarios, in the firmware's replay tests.
- The bench items above are measured and written down.
