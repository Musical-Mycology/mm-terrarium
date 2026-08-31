# Design Bench + Calibrate Implementation Plan (Plan 2, rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Rev 2 (2026-08-31):** rewritten against the instrument-scripted-functions
slice (PR #74, spec `docs/superpowers/specs/2026-08-31-instrument-scripted-functions-design.md`).
That slice decided SCRIPTED functions live on instruments while GENERATOR
and STREAM stay Bit-declared, and added capability-derived builtins
(`flash`/`stop`/`ping`) plus a fire ladder. The bench therefore fires the
instrument's scripted vocabulary + builtins instead of the earlier
draft's instrument-STREAM idea (which contradicted that decision and is
dropped).

**Goal:** Give the Design panel a live in-browser simulator for draft
instruments (ambient light + generators + fireable scripted/builtin
functions + a smoothed tilt lane + recorded-trace replay) and the
Calibrate flow that turns captured gesture telemetry into accepted
event-trigger thresholds.

**Architecture:** A backend-agnostic `DesignBench` (pure, injected
light-session builder, mirroring `RoomBridge`'s protocol-typed-sinks
pattern) runs a draft instrument's ambient light manifest and GENERATOR
functions, fires builtins and instrument SCRIPTED functions through a
bench-local ladder (scheduled LightCue steps, SolidCue override, MuteCue
latch; PlayCue skipped — audio is out of scope), and streams rendered
frames to the panel over the existing console websocket (mirroring
`room_frame_event`). A pure `control/gesture_eval.py` evaluates recorded
traces against an EventTrigger's thresholds and proposes thresholds from
labelled capture stats. The panel gains a bench canvas with fire buttons
and a tilt lane, a captures browser, and proposal-into-TOML application
(client-side text edit the operator reviews before Save). Structured form
editors (spec section 4) are Plan 3.

**Tech Stack:** Python stdlib in `control/`; luxaeterna only behind an
injected builder implemented in `harness/`; vanilla JS console modules;
pytest + node JS tests.

**Spec:** `docs/superpowers/specs/2026-08-31-design-panel-and-instrument-catalog-design.md`
(sections 5, 6; section 4 deferred to Plan 3 — update its Status in
Task 7). Read alongside
`docs/superpowers/specs/2026-08-31-instrument-scripted-functions-design.md`
(the fire-ladder/builtins model this plan builds on).

## Global Constraints

- Run tests via `.venv/bin/python -m pytest tests -q` (never bare
  python3); worktree needs `ln -s /Users/chris/projects/mm-terrarium/.venv .venv`.
- Baseline at plan start: **1737 passed, 1 skipped** (post PR #73 + #74).
- `control/` imports stdlib only — never luxaeterna, never pyarco. All
  luxaeterna contact lives in `harness/` behind injected callables.
- `console/protocol.py` keeps zero engine imports.
- The console must stay fully usable with no Arco and no luxaeterna in
  offline tests (fakes only); audio preview is out of scope for this plan
  entirely — a PlayCue reaching the bench is skipped, never an error.
- GENERATOR and STREAM functions stay Bit-declared (PR #74 decision);
  nothing in this plan adds function kinds to instruments.
- Design/bench/calibrate commands are console-local admin commands
  (parse_admin_command), never uplink.
- Never-raises operator-mistake convention: refusals are reason strings.
- No em dashes in authored prose; docs use " -- ".
- Commit per task; full suite green before each commit.

---

### Task 1: `control/gesture_eval.py` — trace evaluation + threshold proposal

**Files:**
- Create: `control/gesture_eval.py`
- Test: `tests/test_gesture_eval.py`

**Interfaces:**
- Consumes: trace dicts in `capture/trace.py`'s `to_dict` shape
  (`samples: {t_ms, ax, ay, az, gx, gy, gz}`, `label`, `capture_id`); the
  feature logic mirrors `tools/trace_stats.py` (`GRAVITY`, magnitude,
  rising edges) — import nothing from `tools/` (it is not a runtime
  package); reimplement the three tiny helpers locally with a docstring
  cross-reference.
- Produces:
  - `evaluate_trace(trace: dict, thresholds: dict) -> dict` with keys
    `{"fires": [t_ms, ...], "peak_dev_g": float, "spikes": int,
    "isi_ms": [float, ...]}`. Semantics: accel magnitude in g
    (`sqrt(ax^2+ay^2+az^2)/GRAVITY`, GRAVITY = 9.80665); a fire is a
    rising edge through `thresholds["peak_g"]`; edges closer together
    than `thresholds.get("window_ms", 200)` collapse into the first
    edge's fire; if `thresholds` has `double_ms`, fires within
    `double_ms` of the previous fire are additionally listed in
    `"double_fires": [t_ms, ...]`.
  - `propose_thresholds(rows: list[dict]) -> dict | None`: rows carry at
    least `peak_dev_g`, `span_ms`, `isi_ms`. Proposal:
    `peak_g = round(0.8 * min(peak_dev_g), 2)`,
    `window_ms = int(max(span_ms) + 50)`, and
    `double_ms = int(max(flattened isi_ms) + 100)` only when some row
    has a non-empty `isi_ms`. Returns None for empty rows.
  - `session_rows(session_dir: Path) -> list[dict]`: one row per
    `<label>/<series>.json` under the session dir with keys
    `{"label", "capture_id", "series", "peak_dev_g", "span_ms",
    "spikes", "isi_ms", "duration_ms", "n"}` (mic analysis stays in
    tools/trace_stats.py).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gesture_eval.py
"""Pure trace evaluation against EventTrigger thresholds."""
import json
from pathlib import Path

from control.gesture_eval import evaluate_trace, propose_thresholds, session_rows

GRAVITY = 9.80665


def make_trace(t_ms, accel_g, label="tap", series=1):
    # z carries the whole magnitude; x/y zero; gyro zero
    return {"label": label, "capture_id": f"{label}-{series}", "series": series,
            "samples": {"t_ms": list(t_ms),
                        "ax": [0.0] * len(t_ms), "ay": [0.0] * len(t_ms),
                        "az": [g * GRAVITY for g in accel_g],
                        "gx": [0.0] * len(t_ms), "gy": [0.0] * len(t_ms),
                        "gz": [0.0] * len(t_ms)}}


def test_single_spike_fires_once():
    trace = make_trace([0, 10, 20, 30, 40], [1.0, 1.0, 3.0, 1.0, 1.0])
    result = evaluate_trace(trace, {"peak_g": 2.0, "window_ms": 200})
    assert result["fires"] == [20]
    assert result["spikes"] == 1
    assert result["peak_dev_g"] > 1.5


def test_below_threshold_never_fires():
    trace = make_trace([0, 10, 20], [1.0, 1.4, 1.0])
    result = evaluate_trace(trace, {"peak_g": 2.0, "window_ms": 200})
    assert result["fires"] == []


def test_edges_inside_window_collapse():
    trace = make_trace([0, 10, 20, 30, 40, 50],
                       [1.0, 3.0, 1.0, 3.0, 1.0, 1.0])
    result = evaluate_trace(trace, {"peak_g": 2.0, "window_ms": 200})
    assert result["fires"] == [10]


def test_double_fire_annotated_when_double_ms_declared():
    trace = make_trace([0, 10, 300, 310, 320],
                       [1.0, 3.0, 1.0, 3.0, 1.0])
    result = evaluate_trace(
        trace, {"peak_g": 2.0, "window_ms": 200, "double_ms": 400})
    assert result["fires"] == [10, 310]
    assert result["double_fires"] == [310]


def test_propose_thresholds_from_rows():
    rows = [{"peak_dev_g": 2.5, "span_ms": 80.0, "isi_ms": [150.0]},
            {"peak_dev_g": 2.0, "span_ms": 120.0, "isi_ms": []}]
    proposal = propose_thresholds(rows)
    assert proposal == {"peak_g": 1.6, "window_ms": 170, "double_ms": 250}
    assert propose_thresholds([]) is None


def test_propose_omits_double_ms_without_isi():
    proposal = propose_thresholds([{"peak_dev_g": 2.0, "span_ms": 100.0,
                                    "isi_ms": []}])
    assert proposal == {"peak_g": 1.6, "window_ms": 150}


def test_session_rows_reads_capture_layout(tmp_path):
    d = tmp_path / "tap"
    d.mkdir()
    trace = make_trace([0, 10, 20], [1.0, 3.0, 1.0])
    (d / "1.json").write_text(json.dumps(trace))
    rows = session_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["label"] == "tap" and rows[0]["n"] == 3
```

- [ ] **Step 2: Run, verify failure:**
  `.venv/bin/python -m pytest tests/test_gesture_eval.py -q` -> ImportError.
- [ ] **Step 3: Implement**

```python
"""Evaluate recorded gesture traces against EventTrigger thresholds and
propose thresholds from labelled capture rows. Pure stdlib.

The magnitude/edge helpers mirror tools/trace_stats.py deliberately (that
module is an offline CLI, not an importable runtime package)."""
from __future__ import annotations

import json
import math
from pathlib import Path

GRAVITY = 9.80665


def _accel_g(samples: dict) -> list[float]:
    return [math.sqrt(x * x + y * y + z * z) / GRAVITY
            for x, y, z in zip(samples["ax"], samples["ay"], samples["az"])]


def _rising_edges(values: list, threshold: float) -> list[int]:
    edges, above = [], False
    for i, value in enumerate(values):
        now = value >= threshold
        if now and not above:
            edges.append(i)
        above = now
    return edges


def evaluate_trace(trace: dict, thresholds: dict) -> dict:
    samples = trace["samples"]
    t_ms = samples["t_ms"]
    accel_g = _accel_g(samples)
    peak_g = thresholds["peak_g"]
    window_ms = thresholds.get("window_ms", 200)
    edges = _rising_edges(accel_g, peak_g)
    fires: list = []
    for i in edges:
        if fires and t_ms[i] - fires[-1] < window_ms:
            continue
        fires.append(t_ms[i])
    result = {
        "fires": fires,
        "peak_dev_g": max((abs(a - 1.0) for a in accel_g), default=0.0),
        "spikes": len(edges),
        "isi_ms": [t_ms[b] - t_ms[a] for a, b in zip(edges, edges[1:])],
    }
    if "double_ms" in thresholds:
        double_ms = thresholds["double_ms"]
        result["double_fires"] = [
            t for prev, t in zip(fires, fires[1:]) if t - prev <= double_ms]
    return result


def propose_thresholds(rows: list) -> dict | None:
    if not rows:
        return None
    proposal = {
        "peak_g": round(0.8 * min(r["peak_dev_g"] for r in rows), 2),
        "window_ms": int(max(r["span_ms"] for r in rows) + 50),
    }
    isi = [v for r in rows for v in r["isi_ms"]]
    if isi:
        proposal["double_ms"] = int(max(isi) + 100)
    return proposal


def session_rows(session_dir: Path) -> list:
    rows = []
    for path in sorted(Path(session_dir).glob("*/[0-9]*.json")):
        trace = json.loads(path.read_text())
        ev = evaluate_trace(trace, {"peak_g": 2.0, "window_ms": 200})
        t_ms = trace["samples"]["t_ms"]
        hits = [i for i, g in enumerate(_accel_g(trace["samples"]))
                if g >= 2.0]
        rows.append({
            "label": trace["label"], "capture_id": trace["capture_id"],
            "series": trace["series"], "peak_dev_g": ev["peak_dev_g"],
            "span_ms": (t_ms[hits[-1]] - t_ms[hits[0]]) if hits else 0.0,
            "spikes": ev["spikes"], "isi_ms": ev["isi_ms"],
            "duration_ms": (t_ms[-1] - t_ms[0]) if t_ms else 0.0,
            "n": len(t_ms),
        })
    return rows
```

Check the proposal arithmetic in the tests by hand: `0.8 * 2.0 = 1.6`,
`120 + 50 = 170`, `150 + 100 = 250`. If your implementation rounds
differently, fix the implementation to match the test, not the reverse.
- [ ] **Step 4: Run, verify pass; run full suite.**
- [ ] **Step 5: Commit:** `git add control/gesture_eval.py tests/test_gesture_eval.py && git commit -m "feat(design): pure gesture-trace evaluation and threshold proposal"`

---

### Task 2: `control/design_bench.py` — the bench engine

**Files:**
- Create: `control/design_bench.py`
- Test: `tests/test_design_bench.py`

**Interfaces:**
- Consumes:
  - `Instrument` (functions — GENERATOR + SCRIPTED after PR #74 —
    stream_triggers, light_manifest, capabilities);
  - `GeneratorRunner(functions)` (`control/generator_runner.py`):
    `cues(elapsed: float, at: float) -> list[tuple]` of
    `(dev, status, data1, data2)`, and
    `suppress(lanes: Iterable[tuple[str, int, int]], until: float)` —
    read its docstring for the exact lane-tuple shape before use;
  - `builtin_functions(instrument) -> dict[str, Function]` and
    `RESERVED_NAMES` from `control/builtins.py`;
  - `expand_script(function_decl, at, devs) -> list` from
    `control/functions.py` — returns `LightCue(dev, status, data1, data2,
    when=...)`, `SolidCue(dev, rgb, level, duration, when=...)`,
    `MuteCue(dev)`, `PlayCue(dev, name, params)` from `control/cues.py`;
  - `StreamTrigger` "smooth" semantics — read
    `GameServer._apply_stream_triggers` (control/engine.py:452) and
    mirror its EMA transform exactly (including how it seeds), with
    bench-local per-trigger state;
  - fire-ladder precedence — read `GameServer` around
    `control/engine.py:702` ("The ladder, per instrument: built-ins
    first ...") and match that order for the bench's own resolution:
    builtin first, then the instrument's own function table.
- Produces:

```python
class DesignBench:
    def __init__(self, instrument, session, clock=time.monotonic): ...
    def tick(self) -> list[int] | None       # None = no new frame
    def fire(self, name: str) -> str | None  # refusal string or None
    def fireable(self) -> list[dict]         # [{"name","description","source"}]
    def lane(self, verb: str, value: float, status: int, data1: int) -> None
    def close(self) -> None
```

  where `session` is any object with `feed_midi(status, d1, d2)`,
  `render() -> list[int]`, `close()` (Protocol named `BenchSession`,
  fakes in tests). Behavior:
  - `tick()`: `elapsed = clock() - start`. Feed due scheduled script
    steps (see `fire`), then generator cues (`GeneratorRunner.cues`),
    then render. Apply the bench's SolidCue override (paint every pixel
    `rgb` scaled by `level` until `when + duration` passes) and MuteCue
    latch (all-black frame while latched) OVER the rendered channels.
    Return the frame only when it differs from the previous returned
    frame; the very first tick always returns a frame.
  - `fire(name)`: resolve `builtin_functions(self._instrument).get(name)`
    first, else the instrument's own SCRIPTED function of that name;
    no match -> return `f"no function {name!r} on this instrument"`.
    Expand via `expand_script(fn, at=elapsed, devs=("bench",))`.
    LightCues go on a min-heap of `(when, status, data1, data2)` drained
    in `tick()`; SolidCue sets the override slot `(rgb, level,
    expires_at)`; MuteCue sets the latch; PlayCue is skipped (audio out
    of scope). Firing any function other than `stop` clears the mute
    latch first (mirror of the engine's un-mute-on-play, simplified for
    a one-surface bench; record the simplification in the docstring).
    Suppress generator lanes for the script's span:
    `runner.suppress(lanes_of(expanded LightCues), until=last when)`.
  - `fireable()`: builtins first (source "builtin"), then the
    instrument's SCRIPTED functions not shadowed by a builtin name
    (source "instrument"), each `{"name", "description", "source"}`.
  - `lane(verb, value, status, data1)`: run `value` through the
    instrument's stream triggers matching `verb` (arg index 0), then
    scale from [-1.0, 1.0] to [0, 127] (clamped, rounded) and
    `session.feed_midi(status, data1, scaled)`.
  - `close()`: clears EMA/override/latch state and closes the session.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_design_bench.py
from control.design_bench import DesignBench
from control.cues import TARGET
from control.functions import (Function, FunctionKind, GeneratorSpec,
                               ScriptStep)
from control.instrument import Instrument
from control.triggers import StreamTrigger


class FakeSession:
    def __init__(self):
        self.midi, self.closed = [], False
        self.frame = [0, 0, 0]
    def feed_midi(self, status, d1, d2):
        self.midi.append((status, d1, d2))
        self.frame = [d2, d2, d2]
    def render(self):
        return list(self.frame)
    def close(self):
        self.closed = True


GEN = Function(name="drift", description="", kind=FunctionKind.GENERATOR,
               generator=GeneratorSpec(dev="room", status=0xB0, data1=74,
                                       waveform="triangle", period=8.0))
SCRIPTED = Function(name="pulse", description="two-step cc pulse",
                    kind=FunctionKind.SCRIPTED,
                    script=(ScriptStep(0.0, (TARGET, 0xB0, 74, 127)),
                            ScriptStep(1.0, (TARGET, 0xB0, 74, 0))))


def make_bench(functions=(), stream_triggers=(), capabilities=("light.pixels",)):
    t = [0.0]
    inst = Instrument(name="bench", functions=tuple(functions),
                      stream_triggers=tuple(stream_triggers),
                      capabilities=frozenset(capabilities))
    session = FakeSession()
    return DesignBench(inst, session, clock=lambda: t[0]), session, t


def test_tick_runs_generators_and_reports_changed_frames():
    bench, session, t = make_bench([GEN])
    first = bench.tick()
    assert first is not None
    t[0] = 2.0
    assert bench.tick() != first
    assert session.midi


def test_tick_returns_none_when_frame_unchanged():
    bench, session, _ = make_bench([])
    assert bench.tick() is not None
    assert bench.tick() is None


def test_fireable_lists_builtins_then_instrument_functions():
    bench, _, _ = make_bench([SCRIPTED])
    names = [(f["name"], f["source"]) for f in bench.fireable()]
    assert ("flash", "builtin") in names
    assert ("stop", "builtin") in names
    assert ("pulse", "instrument") in names


def test_fire_schedules_light_cues_by_offset():
    bench, session, t = make_bench([SCRIPTED])
    assert bench.fire("pulse") is None
    bench.tick()
    assert (0xB0, 74, 127) in session.midi
    assert (0xB0, 74, 0) not in session.midi
    t[0] = 1.5
    bench.tick()
    assert (0xB0, 74, 0) in session.midi


def test_fire_unknown_name_refuses():
    bench, _, _ = make_bench([])
    assert bench.fire("nope") is not None


def test_flash_overrides_frame_white_then_expires():
    bench, session, t = make_bench([])
    bench.tick()
    assert bench.fire("flash") is None
    frame = bench.tick()
    assert frame is not None and max(frame) > 200   # solid white override
    t[0] = 6.0                                      # flash duration is 5 s
    late = bench.tick()
    assert late is not None and max(late) < 200


def test_stop_latches_black_until_next_fire():
    bench, session, t = make_bench([SCRIPTED])
    bench.tick()
    assert bench.fire("stop") is None
    assert set(bench.tick()) == {0}
    assert bench.fire("pulse") is None
    t[0] = 0.1
    frame = bench.tick()
    assert frame is not None and max(frame) > 0


def test_lane_applies_smooth_stream_trigger_and_scales():
    trig = StreamTrigger(name="s", description="", verb="tilt", arg=0,
                         transform="smooth", params={"alpha": 0.5})
    bench, session, _ = make_bench([], [trig])
    bench.lane("tilt", 1.0, 0xB0, 74)
    bench.lane("tilt", 1.0, 0xB0, 74)
    values = [d2 for (s, d1, d2) in session.midi if d1 == 74]
    assert len(values) == 2 and values[0] < values[1] <= 127


def test_close_closes_session():
    bench, session, _ = make_bench([])
    bench.close()
    assert session.closed
```

Before finalizing the smooth test, read `_apply_stream_triggers` and its
engine tests: if the EMA seeds from the first sample rather than 0,
mirror that here and adjust the two-value assertion to whatever two
distinct converging values the real transform yields. Same for
`test_flash_overrides_frame_white_then_expires`: read
`control/builtins.py` for flash's actual `level`/duration and adjust the
magnitude assertions to it rather than guessing.
- [ ] **Step 2: Run, verify failure.**
- [ ] **Step 3: Implement** per the Produces block. Keep the scheduled
  steps in a `heapq` of `(when, status, data1, data2)`; the override slot
  as `self._solid: tuple | None = (rgb, level, expires_at)`; the latch as
  `self._muted: bool`. The module docstring records both deliberate
  simplifications: PlayCue skipped (no audio), and any non-stop fire
  clears the mute latch (single-surface reading of un-mute-on-play).
- [ ] **Step 4: Run full suite.**
- [ ] **Step 5: Commit:** `git commit -m "feat(design): DesignBench engine (generators, fire ladder, lane)"`.

---

### Task 3: Console wire — bench, captures, calibrate stats

**Files:**
- Modify: `console/protocol.py`, `console/agent.py`
- Test: `tests/test_console_protocol.py` (append), `tests/test_console_agent.py` (append)

**Interfaces:**
- Consumes: `DesignBench` (Task 2); `load_catalog(...).get(state, name)`
  (`CatalogEntry.instrument` may be None for a broken draft);
  `session_rows`/`propose_thresholds`/`evaluate_trace` (Task 1).
- Produces:
  - New admin commands (extend `parse_admin_command`, same strict
    validation style; add the names to the admin gate in
    `_handle_command`): `BenchStartCommand(state, name)`,
    `BenchStopCommand()`, `BenchFireCommand(name)`,
    `BenchLaneCommand(verb, value: float, status: int, data1: int)`,
    `ListCapturesCommand()`, `CaptureStatsCommand(session, label)`,
    `ReplayTraceCommand(state, name, trigger, session, label, series: int)`.
  - New events:
    - `bench_started_event(functions) -> {"event": "bench_started",
      "functions": [{"name", "description", "source"}]}` (the reply to a
      successful bench_start, from `DesignBench.fireable()`);
    - `bench_frame_event(channels) -> {"event": "bench_frame",
      "channels": [...]}`;
    - `captures_listed_event(sessions) -> {"event": "captures_listed",
      "sessions": [{"session", "labels": {label: count}}]}`;
    - `capture_stats_event(rows, proposal) -> {"event": "capture_stats",
      "rows": [...], "proposal": {...}|None}`;
    - `replay_result_event(result) -> {"event": "replay_result",
      "result": {...evaluate_trace output..., "trace": {"t_ms": [...],
      "accel_g": [...]}}}` (the panel plots from `trace`).
  - `ConsoleAgent.__init__` gains `bench_session_factory=None` (callable
    `(light_manifest: dict) -> BenchSession`) and
    `captures_root: Path | None = None`. Bench commands refuse without a
    factory ("no bench backend"); capture commands refuse without a root
    ("no capture store").
  - `ConsoleAgent.poll()` ticks a live bench and broadcasts
    `bench_frame` at most every `BENCH_FRAME_INTERVAL = 0.1` seconds
    (module constant, mirroring `ROOM_FRAME_INTERVAL`'s decimation).
  - Bench lifecycle: `bench_start` loads the entry via `load_catalog`; a
    missing entry or `instrument is None` refuses with the entry's error;
    an already-running bench is closed and replaced; the reply is
    `bench_started_event`. `bench_stop` closes it. `bench_fire` maps a
    `DesignBench.fire` refusal to an error event. `bench_lane` calls
    `bench.lane(verb, value, status, data1)`.
  - Replay: load the entry, find the named `EventTrigger` among
    `entry.instrument.event_triggers` (refuse if absent), read
    `captures_root / session / label / f"{series}.json"` (refuse if
    missing), run `evaluate_trace(trace, trigger.thresholds)`, fold
    `t_ms` and computed accel_g into the event's `trace` key.
  - `list_captures` walks `captures_root` subdirs; each session dir's
    labels are its child dirs, count = number of `[0-9]*.json` inside.
  - `capture_stats` = `session_rows(captures_root / session)` filtered to
    `label`, plus `propose_thresholds` over the filtered rows.

- [ ] **Step 1: Write failing protocol tests** covering: each new command
  parses to its dataclass with correct fields; missing/mistyped fields
  raise ValueError; each event builder returns the exact dict shape above
  (assert full equality on small literals, e.g.
  `bench_frame_event([1, 2]) == {"event": "bench_frame", "channels": [1, 2]}`).
- [ ] **Step 2: Write failing agent tests** (reuse the file's fixtures):
  bench commands refuse without a factory; `bench_start` on a published
  entry (catalog dir built in tmp_path; an instrument with no ambient
  light manifest is legal) replies `bench_started` listing at least the
  builtins, and `poll()` broadcasts `bench_frame`; `bench_fire` with an
  unknown name returns an error event; `list_captures`/`capture_stats`
  over a tmp_path captures tree built from Task 1's `make_trace` dicts
  dumped to JSON; `replay_trace` returns fires for a spike trace against
  tuneshroom's tap thresholds; each refusal path returns an `error`
  event. Use a fake factory returning Task 2's FakeSession shape.
- [ ] **Step 3: Run, verify failures; implement** protocol side then agent
  side per Produces. Keep `console/protocol.py` pure builders; agent-side
  catalog/bench/gesture_eval imports live inside the handler methods like
  the design commands already do.
- [ ] **Step 4: Run full suite.**
- [ ] **Step 5: Commit:** `git commit -m "feat(console): bench, captures, and calibrate wire"`.

---

### Task 4: Boot wiring — real bench session builder

**Files:**
- Create: `harness/design_session.py`
- Modify: `harness/terrarium_boot.py` (ConsoleAgent construction site in `main()`)
- Test: `tests/test_design_session.py` (importorskip luxaeterna), `tests/test_terrarium_boot.py` (append)

**Interfaces:**
- Consumes: luxaeterna via the `DeviceBridge` pipeline — read
  `harness/device_bridge.py` and `harness/led_smoke.py` FIRST and use
  their real APIs (`shroom_capability()`, `LightManifest.from_dict`,
  `build_session`, `session.render_into`); the sketch below is the
  intended shape, corrected against those files, never invented.
- Produces: `harness/design_session.py` with

```python
"""Real luxaeterna-backed BenchSession for the Design bench.

Renders a draft instrument's ambient light manifest on a standard
Testshroom-sized surface (shroom_capability). Dev/test dependency on
luxaeterna, mirroring harness/device_bridge.py."""
import time

from luxaeterna.synth.capability import shroom_capability
from luxaeterna.synth.manifest import LightManifest
from luxaeterna.synth.session import build_session


class LuxBenchSession:
    def __init__(self, light_manifest: dict, clock=time.monotonic):
        self._cap = shroom_capability()
        manifest = LightManifest.from_dict(light_manifest or {})
        self._session = build_session(manifest, self._cap, clock=clock)
        self._buf = bytearray(self._channel_count())
    def _channel_count(self) -> int:
        ...  # read off the capability the way led_smoke's Universe sizing does
    def feed_midi(self, status, d1, d2):
        self._session.feed_midi(status, d1, d2)
    def render(self) -> list[int]:
        self._session.render_into(self._buf)
        return list(self._buf)
    def close(self):
        self._session.clear()


def bench_session_factory(light_manifest: dict):
    return LuxBenchSession(light_manifest)
```

  The `_channel_count` ellipsis is the one API fact to pull from
  luxaeterna (how led_smoke sizes its Universe for `shroom_capability`,
  `harness/led_smoke.py:52-66`); if `render_into` has a different
  contract (e.g. takes a Universe), adapt while keeping the
  `BenchSession` protocol surface identical. Verify `clear()` is the
  right teardown by reading `luxaeterna.synth.session`; if not, drop the
  session object instead and record the choice in the docstring.
- `main()` passes `bench_session_factory=bench_session_factory` (import
  from `harness.design_session`) and `captures_root` resolved with the
  same convention `capture/store.py`/`harness/capture_smoke.py` use for
  the captures directory — read `CaptureStore.__init__` and
  `capture_smoke.build()` first and match them exactly.

- [ ] **Step 1: Write the luxaeterna-gated test**
  (`pytest.importorskip("luxaeterna")`): build `LuxBenchSession({})`,
  assert `render()` returns a non-empty list of ints and
  `feed_midi(0xB0, 74, 64)` does not raise; then a boot test asserting
  the built console agent has a non-None `bench_session_factory` and a
  `captures_root` ending in `captures`.
- [ ] **Step 2: Run, verify failure; implement; run full suite** (the
  luxaeterna test runs in the dev venv; the core suite skips it without
  luxaeterna).
- [ ] **Step 3: Commit:** `git commit -m "feat(harness): luxaeterna bench session + boot wiring"`.

---

### Task 5: Panel — bench canvas, fire buttons, tilt lane

**Files:**
- Modify: `console/static/design.js`, `console/static/index.html`
- Test: `tests/js/design_bench.test.js` (new)

**Interfaces:**
- Consumes: Task 3 wire (`bench_start`/`bench_stop`/`bench_fire`/
  `bench_lane` commands; `bench_started` and `bench_frame` events;
  `bench_frame.channels` is a flat GRB int list).
- Produces, inside the existing `viewDesign` section of `index.html`,
  below the editor block:

```html
      <section id="benchPanel" class="card">
        <canvas id="benchCanvas" width="360" height="30"></canvas>
        <div id="benchFunctions"></div>
        <div class="actions">
          <button id="benchStart" class="btn solid-gold">Simulate</button>
          <button id="benchStop" class="btn" disabled>Stop</button>
        </div>
        <label>Tilt <input id="benchTilt" type="range" min="-100"
               max="100" value="0" disabled></label>
      </section>
```

  and in `design.js` new exports:
  - `initBench()`: registers `wire.on("bench_started", ...)` /
    `wire.on("bench_frame", ...)` and the button/slider handlers.
  - `renderBenchFunctions(el, functions, send)`: one button per entry
    labeled `<name>` with a title/tooltip of its description and a
    `builtin`/`instrument` class; click sends
    `{command: "bench_fire", name}`.
  - `paintBenchFrame(canvas, channels)`: one filled rect per pixel;
    channels arrive GRB, 3 per pixel — reuse the room-frame painting
    approach from `surface.js`'s `onRoomFrame` path (read it first and
    match its color handling).
  Behavior: Simulate sends `{command: "bench_start", state, name}` for
  the current selection (disabled with no selection); on
  `bench_started`, render the fire buttons and enable Stop + slider;
  Stop sends `{command: "bench_stop"}` and disables/clears them; the
  tilt slider sends on input `{command: "bench_lane", verb: "tilt",
  value: v / 100, status: 176, data1: 74}` throttled to at most one send
  per 100 ms (accept every send in the test and assert on the last
  payload only if the stub has no clock control).
- [ ] **Step 1: Write failing node tests**: `bench_started` renders one
  button per function and enables controls; clicking a function button
  sends bench_fire with its name; `bench_frame` paints (extend
  `_dom_stub.js` with a minimal `getContext("2d")` recorder if it lacks
  one, and assert fillRect calls); Simulate sends bench_start with the
  current selection; slider value 50 sends `value: 0.5`; Stop disables
  and clears the function list.
- [ ] **Step 2: Run via `.venv/bin/python -m pytest tests/test_console_js.py -q`, verify failure; implement; verify pass; full suite.**
- [ ] **Step 3: Commit:** `git commit -m "feat(console): Design bench canvas, fire buttons, tilt lane"`.

---

### Task 6: Panel — captures browser, stats, proposal, replay plot

**Files:**
- Modify: `console/static/design.js`, `console/static/index.html`
- Test: `tests/js/design_calibrate.test.js` (new)

**Interfaces:**
- Consumes: Task 3 wire (`list_captures`, `capture_stats`,
  `replay_trace`; events `captures_listed`, `capture_stats`,
  `replay_result`).
- Produces, below the bench section:

```html
      <section id="calibratePanel" class="card">
        <div class="actions">
          <button id="calRefresh" class="btn">Captures</button>
        </div>
        <div id="calSessions"></div>
        <div id="calStats"></div>
        <div class="actions">
          <button id="calPropose" class="btn solid-gold" disabled>Apply proposal to draft</button>
          <button id="calReplay" class="btn" disabled>Replay</button>
        </div>
        <canvas id="calPlot" width="360" height="120" hidden></canvas>
      </section>
```

  `design.js` additions:
  - `renderCaptures(el, sessions, onPick)`: one row per session/label
    pair (`<session> / <label> (<count>)`), click selects.
  - On `capture_stats`: render rows as a compact table (label, series,
    peak_dev_g, span_ms, spikes) into `#calStats`; remember
    `msg.proposal`; enable `#calPropose` when a proposal exists and a
    draft is selected, `#calReplay` when a selection + session exist.
  - `applyProposal(text, trigger, proposal, provenance)` (exported, pure
    string -> string): inside the `[[event_triggers]]` block whose
    `name = "<trigger>"`, replace the numeric values of `peak_g`,
    `window_ms`, `double_ms` under its `[event_triggers.thresholds]`
    table with the proposal's values (add a missing key line, drop
    nothing), and insert `# calibrated from <provenance>` directly above
    the thresholds table header, replacing any previous
    `# calibrated from` line there. Line-based transform: locate the
    trigger's block by scanning lines from its `name =` match to the
    next `[[` header. Unknown trigger -> return the text unchanged and
    let the caller surface "trigger not found in draft text". Clicking
    `#calPropose` runs it against the textarea content with provenance
    `"<session> on <today's ISO date>"`, updates the textarea, and the
    operator reviews and hits the existing Save.
  - On `replay_result`: unhide `#calPlot` and draw accel_g as a polyline
    over t_ms, a horizontal threshold line at the draft trigger's
    `peak_g` (parsed from the textarea via a shared
    `findTriggerBlock(lines, trigger)` helper factored out of
    `applyProposal`), and one vertical tick per `result.fires` entry.
    Replay sends `{command: "replay_trace", state, name, trigger,
    session, label, series}` for the picked capture row, trigger chosen
    via `window.prompt("Trigger name", "tap")` (Clone's prompt idiom).
- [ ] **Step 1: Write failing node tests** for: `renderCaptures` rows and
  selection; `applyProposal` replacing values, adding a missing
  `double_ms`, inserting and replacing the provenance comment, and
  returning input unchanged for an unknown trigger (assert exact output
  strings on a small fixture TOML with two `[[event_triggers]]` blocks —
  guard that the SECOND block is untouched); `capture_stats` enabling the
  buttons; `replay_result` drawing (canvas recorder assertions, at least
  "drawn to and unhidden").
- [ ] **Step 2: Run, verify failure; implement; verify pass; full suite.**
- [ ] **Step 3: Commit:** `git commit -m "feat(console): captures browser, threshold proposal, replay plot"`.

---

### Task 7: Docs + spec status

**Files:**
- Modify: `docs/MM_TERRARIUM.md`, `docs/superpowers/specs/2026-08-31-design-panel-and-instrument-catalog-design.md`

- [ ] **Step 1:** Extend the deep-dive's catalog/Design-panel section (or
  add a sibling `###` section) covering: `control/gesture_eval.py` (pure,
  mirrors trace_stats deliberately), `DesignBench` and its injected
  `BenchSession` protocol (bench-local ladder mirroring the engine's
  builtin-first order; PlayCue skipped; simplified un-mute; generator
  lane suppression during script spans), the seven new console commands
  and five events, `BENCH_FRAME_INTERVAL` decimation, the client-side
  `applyProposal` decision (raw-text edit reviewed by the operator
  instead of a server-side TOML writer, provenance as a comment line),
  and the harness `LuxBenchSession`. Note explicitly that the bench adds
  NO function kinds to instruments — STREAM stayed Bit-declared per the
  instrument-scripted-functions spec. No em dashes; use " -- ".
- [ ] **Step 2:** Update the Design-panel spec's Status section: sections
  5 and 6 shipped (audio preview explicitly not built; synthetic gesture
  input realized as fire buttons + tilt lane rather than the original
  stream-function idea, superseded by the instrument-scripted-functions
  decision); section 4 (structured form editors) is Plan 3; slice 3 wire
  support still unplanned.
- [ ] **Step 3:** Full suite; record the count. Commit:
  `git commit -m "docs: design bench + calibrate, spec status"`.

---

## Self-review notes (already applied)

- Spec 5 coverage: light sim (T2/T4/T5), gesture input via the fire
  vocabulary + smoothed tilt lane (T2/T5) — reworked from the rev-1
  instrument-STREAM idea, which PR #74's spec forecloses; trace replay
  (T3/T6); audio preview deliberately excluded and recorded in T7.
- Spec 6 coverage: capture arming reuses the existing load_bit path for
  CaptureBit (no new machinery); stats + proposal (T1/T3/T6); provenance
  (T6); replay-before-publish (T6). "Operator accepts" is the
  applyProposal + review + Save flow.
- Type consistency: `BenchSession` protocol (feed_midi/render/close)
  identical in T2 (fake), T3 (factory type), T4 (LuxBenchSession).
  `evaluate_trace` result keys match T1 and T3's `replay_result_event`.
  `fireable()` rows match `bench_started_event`'s functions shape.
