# Tick pacing: hold the 44 Hz render tick on a deadline, not a fixed sleep

Status: approved design (2026-09-25). Follows the Art-Net fixture sink
(`2026-09-23-artnet-fixture-sink-design.md`), whose first live run received
~33 fps against a 44 Hz tick.

## 1. Problem, as measured

All figures are **dev-box figures** (the Mac this was developed on,
2026-09-23/25), not venue measurements.

A live `harness.run_stack --ci --no-bit --room DEMO` against a real Arco,
with a scratch `[[artnet]]` entry for `DEMO/array` pointed at
`127.0.0.1:16454` and `python -m harness.artnet_listen --port 16454
--pixels 864` receiving, was probed at three points for 70 s:

| Stage | Figure |
|---|---|
| Tick loop (`DeviceLinkAgent.poll` to `poll`) | 36.2-37.5 Hz, interval p50 ~27.5 ms, p95 ~29.5 ms |
| Work per tick (`poll`) | p50 ~0.5 ms, p95 <= 1.6 ms (`_render_room` ~0.45 ms) |
| `sleep(1.0 / 44.0)` actual (22.7 ms asked) | p50 ~26.8 ms, p95 ~27.8 ms |
| `send_frame` calls per tick | 177-180 per 182 ticks (unchanged-frame skip ~2%) |
| Sink sends (sender thread) | 35.4-36.8 Hz, `_send` p50 0.3 ms; 0 keepalives, 0 late, 0 errors |
| Listener | 32.7-38.7 fps, interval p50 26-28 ms, 0 seq gaps, 0 bad packets |
| `PowerLimiter.apply` + pack, 3456 ch | p50 ~0.2 ms |
| `render_bench --host 127.0.0.1 --pixels 864 --seconds 25` | mean 37.78 fps, p95 27.78 ms, worst 40.26 ms |
| Bare `time.sleep(0.02273)`, 200 samples | p50 27.05 ms, p95 28.98 ms |
| Same work, deadline-paced loop | 44.00 Hz mean, interval p50 22.68 ms |

**Cause.** `harness/terrarium_boot.py`'s three 44 Hz loops
(`_wait_in_setup`, `_serve_until_done`, `_wait_for_load`) do their work and
then call `sleep(1.0 / 44.0)` unconditionally, so the period is work plus
the *actual* sleep. The work is small; macOS oversleeps a 22.7 ms request by
~4 ms (~19%). The sink, its sender thread, the power limiter and GIL
contention are all ruled out: each forwards the tick rate it is given. The
original ~33 fps is the same mechanism under more host load.

`harness/render_bench.py`'s `measure()` subtracts elapsed work but not the
oversleep, so the tool built to catch a slow loop reads the same ~37.8.

## 2. Design

### 2.1 `harness/tick_pacer.py`: `TickPacer`

```python
class TickPacer:
    def __init__(self, period: float, *, clock=time.monotonic,
                 sleep=time.sleep) -> None: ...
    def wait(self) -> None: ...
```

- The first `wait()` anchors the schedule: `deadline = clock() + period`.
- Each `wait()` sleeps `deadline - clock()` if positive, then advances
  `deadline += period`. A tick that ran long therefore gets a shorter
  sleep, and an oversleep is repaid on the next tick, so the **mean** rate
  holds at `1 / period` whatever the platform's sleep slack.
- **No burst catch-up.** If, on entry, `clock()` is already more than one
  period past `deadline`, the schedule resyncs: no sleep, and
  `deadline = now + period`. A stall is lost time, never a run of
  back-to-back ticks (the same "a stall never backs up" rule the Art-Net
  sink's coalescing follows). Lateness of up to one period is repaid.
- `sleep` is called only with a positive argument.
- Pure, no imports beyond `time`; offline-testable with a fake clock.

### 2.2 The three 44 Hz loops

`_wait_in_setup`, `_serve_until_done` and `_wait_for_load` each gain a
keyword `pacer: TickPacer | None = None`. When `None`, the loop builds
`TickPacer(1.0 / 44.0, sleep=sleep)`: the **loop's** `sleep` seam, but the
pacer's own default `time.monotonic` clock, **not** the loop's `clock`
argument. Existing tests drive `clock` with scripted iterators
(`iter([...]).__next__`) whose length is part of the assertion; sharing
that clock would change how many readings each loop consumes. The
`sleep(1.0 / 44.0)` line becomes `pacer.wait()`.

`gs.tick(1.0 / 44.0)` keeps its nominal `dt` (unchanged: the engine's
`dt` is the design rate, and the paced mean now matches it).

The 20 Hz no-room wait (`_wait_for_room_ready`'s `sleep(1.0 / 20.0)`)
renders no fixture and is out of scope.

A pacer is per call: each loop entry builds a fresh one, so a new round
anchors its own schedule and never inherits a stale deadline.

### 2.3 `harness/render_bench.py`'s `measure()`

`measure(loop, seconds, *, clock=time.monotonic, sleep=time.sleep)` paces
with `TickPacer(loop.frame_interval, clock=clock, sleep=sleep)` instead of
`sleep(frame_interval - elapsed)`, and records the interval between
successive tick *starts*. Its module docstring notes that it now measures
the render path at a correctly paced rate, and that luxaeterna's own
threaded loop (`MultiUniverseOutputLoop._run`, which has the same
fixed-remaining-sleep pattern) is not what it times.

## 3. Out of scope

- **Per-tick jitter.** Deadline pacing fixes the mean, not the ~4 ms
  per-tick jitter macOS sleep adds (render_bench's p95 <= 25 ms pass line
  may still fail on a Mac). A sleep-short-then-spin finish is a possible
  follow-up if bring-up needs it.
- **luxaeterna's `MultiUniverseOutputLoop._run`** (`luxaeterna/output.py`):
  same pattern, different repo; filed as a separate task.
- The ~2% unchanged-frame skip in `DeviceLinkAgent._render_room` is correct
  behavior (the sink's keepalive covers it) and stays.

## 4. Testing

- `tests/test_tick_pacer.py`, fake clock plus a fake sleep that advances
  it (optionally oversleeping by a fixed slack):
  - with 4 ms oversleep, N waits span `N * period` within one period;
  - work time is subtracted (a tick that ran 10 ms sleeps period - 10 ms);
  - an overrun of more than one period resyncs: no sleep that call, next
    deadline is `now + period`, and the following wait sleeps a full-ish
    period (no burst);
  - sleep is never called with a non-positive value;
  - the first wait sleeps about one period.
- `tests/test_terrarium_boot.py`: each of the three loops calls an injected
  fake pacer's `wait()` once per iteration and never calls `sleep(1/44)`
  directly; all existing loop tests pass unchanged.
- `tests/test_render_bench.py`: with a fake clock whose sleep oversleeps
  4 ms, `measure()` reports a mean within 1% of `1 / frame_interval`.
- Full suite: `.venv/bin/python -m pytest tests -q`.
- **Live re-measure** (dev box): the section 1 run and `render_bench`
  again, tick/sink/listener; results recorded in `docs/MM_TERRARIUM.md`.

## 5. Docs

`docs/MM_TERRARIUM.md`: replace the Art-Net entry's "cause has not been
investigated" bullet with the diagnosis and before/after dev-box figures;
add a `TickPacer` note to the *Host platform* section beside the
`render_bench.py` paragraph (macOS sleep slack; pace to deadlines).
