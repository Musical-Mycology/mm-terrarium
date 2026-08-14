# Measuring the o2lite cue path, and replacing `cue_horizon`'s placeholder

**Date:** 2026-08-14
**Status:** design, approved
**Supersedes nothing.** Closes the *"`cue_horizon`'s default is measurably too
small"* item in `docs/MM_TERRARIUM.md`'s *Not yet built / deferred* section.

## 1. The problem

`control/boot_config.py` sets `cue_horizon: float = 0.060` and its own comment
says the value is "a placeholder to be replaced by a measured figure from
`harness/sync_bench.py`". It never was.

The live o2lite run of 2026-08-13 measured the shortfall indirectly: of 820 LED
frames delivered to a device, **762 arrived already past their deadline** and
were clamped. Arithmetic on the first frame (Control stamped `when = 6.440`, the
device's clock read `6.446` on arrival) put end-to-end delivery through Arco at
roughly **67 ms against the 60 ms horizon**.

That 67 ms is a single-frame estimate, not a distribution. A path that averages
67 ms while missing by 200 ms once a second would produce the same number and
would be a different problem. This spec replaces the estimate with a measured
distribution.

The practical effect today is that frames still display, because a clamped frame
is released on arrival, but **the scheduling is bypassed on 93% of frames**. The
transport, the clock agreement and the rendering all work. Honored timing does
not.

## 2. Where the measurement already lives

No new clock and no new wire field are needed. The comparison is already being
made, and its result is already being thrown away.

`DeviceLinkAgent` stamps every `/<dev>/leds` with `when = clock() + horizon`
(`devicelink/agent.py:278`). The device buffers the frame and, at its next
`tick(now)`, pushes it into a `TimedQueue` with `now = o2lite.time_get()`. On the
o2lite path both ends read the same O2 clock by construction, so the two readings
are directly comparable.

`TimedQueue.push()` holds both values at the exact moment it decides whether to
clamp:

```python
elif when < now:
    self.clamped += 1
    due_at = now
```

It keeps the count and discards the magnitude. Recording that magnitude is the
entire measurement:

```
lateness  L = now - when          (negative = arrived early, the healthy case)
latency   E = horizon + L         (stamp-to-displayable, always positive)
```

`E` is what `cue_horizon` has to cover. It is stamp-to-*displayable* rather than
stamp-to-*arrival*, and that is deliberate: displayable is what the clamp counter
actually tests, so it is what determines whether timing is honored. It therefore
includes the device loop's own tick quantization (~5 ms in `harness/o2_shroom.py`,
which sleeps 0.005 s per iteration). That quantization is a real part of the
device path, not measurement error, but it does mean `E` is not a pure transport
figure and must not be quoted as one.

### Avoiding a censored sample

Measuring with the current 60 ms horizon would clamp ~93% of frames and truncate
the distribution at the clamp boundary. The measurement run therefore uses a
deliberately oversized `--horizon 0.15`, chosen to sit well above the ~67 ms
hint, so that essentially nothing clamps and the full range of `L` is observable.

**If any frame still clamps at 0.15, the sample is censored at 150 ms and the
result must say so** rather than reporting a bounded tail as if it were the real
one. The clamp count is reported alongside the distribution precisely so this is
checkable rather than assumed.

## 3. Changes

### 3.1 `control/timed_queue.py` — record the magnitude

A bounded buffer of signed `L` values beside the existing `clamped` counter.

- **Bounded** (`collections.deque(maxlen=...)`), mirroring the module's existing
  `_MAX_PENDING_FRAMES` idiom. `TimedQueue` runs on a Radxa in a long-lived
  installation; an unbounded list of floats is a leak.
- `when=None` contributes **no sample**, consistent with it not counting as a
  clamp — no time was declared, so there is no lateness to speak of.
- Signed, not absolute: "arrived 80 ms early" is the healthy case and must stay
  distinguishable from "arrived 80 ms late".
- Both consumers get it for free — Control's `_room_cues` and the device's
  `_frames` are the same class.
- Stays pure and stdlib-only, so the offline suite keeps running.

### 3.2 `harness/shroom_client.py` — expose it

A `lateness` property mirroring the existing `clamped` property. No behavior
change.

### 3.3 `harness/o2_shroom.py` — report absolute latency

A `--control-horizon` flag carrying the horizon Control was run with, used
**only** to convert observed lateness into absolute latency for the printout.
The device does not otherwise know or need Control's horizon, and this must not
be read as the device gaining a scheduling opinion.

On exit, alongside the existing `frames displayed late: N` line, print
`sync_bench.summarise()` over `[horizon + L for L in lateness]`.

Converting to absolute latency **before** calling `summarise()` matters:
`summarise()` takes absolute values, which is right for its original job of
reducing signed audio-vs-light deltas. Feeding it signed lateness would report
"80 ms of error" for a frame that arrived a healthy 80 ms early. Feeding it `E`,
which is always positive, makes the `abs()` a no-op and makes mean/p95/worst read
as genuine latency figures.

### 3.4 `harness/sync_bench.py` — the `main()` it already advertises

`harness/terrarium_boot.py:267` tells the operator to run
`python -m harness.sync_bench`. The module has no `main()` and no `__main__`
block, so that instruction fails today. Add the entry point: read recorded
samples, print the summary. `summarise()` keeps its current signature and its
no-luxaeterna/no-pyarco property, so it stays in the core offline suite.

### 3.5 `control/arco_process.py` — opt-in pty spawn

Arco's curses init opens `/dev/tty`, so a plain `Popen` whose stdio is a pipe or
socket fails with `Could not open /dev/tty`. `script` does not rescue it. This is
recorded in the deep-dive as a hard blocker on headless runs: CI, cron, or an
agent-driven measurement cannot boot the stack.

It is closable. `pty.fork()` differs from `Popen` in exactly the way that
matters: the child calls `setsid()` and makes the pty slave its **controlling
terminal**, which is what makes `/dev/tty` resolvable. Verified on 2026-08-14 —
Arco reaches its main menu and stays up.

Two details are load-bearing and both cost a silent failure when omitted:

- **`TERM` must be set.** Without it curses has no terminal description.
- **The pty needs a non-zero window size** via `TIOCSWINSZ`. A fresh pty is
  0×0, and curses exits silently against it — the first probe of this approach
  produced zero bytes of output and looked like a hard failure when it was a
  one-ioctl fix.

Delivered as a `pty_popen` callable injected through `ArcoProcess`'s **existing**
`popen=` seam. The default `subprocess.Popen` path is untouched, so an
interactive venue run behaves exactly as it does today, and nothing in
`control/` grows a pty import at module level.

### 3.6 `control/boot_config.py` — the new default

Replace `0.060` with the measured p99, rounded up to a clean figure, and replace
the placeholder comment with the measurement, its date, its host, and an explicit
dev-box label.

## 4. Choosing the percentile

**p99, reported alongside the worst frame.**

The horizon is a **fixed added latency on every cue**, not a timeout. Sizing it
to the worst frame ever observed makes every gesture in the room pay for the
single worst hiccup, and this is an interactive instrument where tap-to-light lag
is the thing a player feels directly. Sizing it to p95 leaves ~1 frame in 20
bypassing the scheduling, which is a visibly high rate for a mechanism whose
entire purpose is honored timing.

p99 covers all but ~1 frame in 100. Those clamp and display immediately — the
degradation is graceful, not a failure — and `DeviceLinkAgent.clamped` /
`ShroomClient.clamped` report the rate, which is exactly the production signal
the clamp counter exists to provide.

The worst-frame figure is reported next to it so the tail stays visible rather
than hidden behind a percentile. If p99 and worst diverge sharply, that is itself
a finding about the path and is recorded as one rather than silently averaged
away.

## 5. What this does NOT establish

**Every figure produced here is a dev-box figure.** The venue target is
bare-metal Linux on a Raspberry Pi 5 relaying every hop through the same process
doing all room synthesis while feeding a 44 Hz render loop. **No venue-box
measurement exists, because the box does not exist.**

This follows the convention `harness/render_bench.py` and `harness/sync_bench.py`
already set, and the deep-dive's *Host platform* section states directly that any
timing figure must be measured on the venue box. The new default is a
better-founded starting value than an unmeasured placeholder; it is not a venue
number, and re-measuring on real hardware stays required.

This also does not make timed cues load-bearing. No Bit computes
`T = gesture_time + horizon`, no Bit emits a `LightCue`, and the non-Room branch
of `_on_light_cue` still accepts `when` and never reads it. That gap is a
separate interface change and is untouched here. The single production use of a
real `when` remains Control's own render clock — which is precisely the path this
spec measures.

## 6. Success criteria

1. A latency distribution over hundreds of frames from a live o2lite run against
   a real Arco, reported as mean / p95 / p99 / worst, with the clamp count
   included so censoring is checkable.
2. `cue_horizon`'s default replaced by a value justified by that distribution,
   with the reasoning at the call site.
3. A re-run at the new horizon confirming the clamp rate collapses — the
   measurement's own validation, using the production signal.
4. `docs/MM_TERRARIUM.md` records the figure, labelled a dev-box figure, and its
   *Not yet built* entry updated from "no number" to the measured one.
5. Suite still green and still fully offline: 621 passed, 1 skipped, plus new
   tests for the added behavior.
