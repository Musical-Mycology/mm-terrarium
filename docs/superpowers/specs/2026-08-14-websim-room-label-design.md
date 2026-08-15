# Labeling the Room simulator's WebSim canvas

Date: 2026-08-14
Status: design approved, pending implementation plan
Primary repo: **luxaeterna** (see
[`2026-08-14-websim-backend-label-design.md`](https://github.com/Musical-Mycology/luxaeterna/blob/main/docs/superpowers/specs/2026-08-14-websim-backend-label-design.md),
`WebSimBackend` gains a `label` parameter). This repo: the two call sites that use it.

## 1. Why this exists

`harness/room_simulator.py` and `harness/o2_shroom.py --no-join` both render a Room
simulator into luxaeterna's `WebSimBackend` for the Room's canvas — the same backend
`harness/o2_shroom.py`'s normal (joining) mode uses for a player device's own canvas.
Both currently show the identical generic browser tab and page title, so there is no
visual way to tell a Room canvas from a player-device canvas when both are open at
once. luxaeterna's companion spec adds a `label` parameter to `WebSimBackend` to solve
the rendering half; this spec is the small follow-up that actually passes one in.

## 2. Goal & success criteria

- `harness/room_simulator.py`'s canvas title reads
  `Lux Aeterna — Shroom LED Simulator — sim-room` (its `dev` is always the
  `SIM_DEV = "sim-room"` constant `terrarium_boot.py` spawns it with).
- `harness/o2_shroom.py`'s canvas title reads `... — {dev}` for **whatever `dev` it
  was invoked with** — `sim-room` when spawned as the Room simulator
  (`--no-join`, from `terrarium_boot.py`'s `_O2SimulatorFactory`), or the real
  player device id (e.g. `ie1`) in normal joining mode. No `--no-join`-specific
  branching: the same one-line change covers both modes because `dev` already
  differs between them.
- Opening a Room canvas and a player canvas side by side is now visually
  distinguishable by browser tab / page title alone.

## 3. Non-goals (scope boundary)

- **`harness/led_smoke.py` is untouched.** It also constructs a `WebSimBackend`, but
  with a hardcoded `"sim-dev"` join (not a CLI-supplied dev id) and it isn't part of
  the Room-vs-player confusion this spec addresses — it's a standalone TestBit smoke
  demo, not run alongside a Room simulator in practice. Labeling it is a plausible
  future one-liner, not part of this slice.
- **No change to `build()`'s signature in either file.** Both already receive `dev`
  as a parameter; only the `WebSimBackend(...)` call site inside each gains
  `label=dev`.
- **No change to what the terminal `print()` lines say** (`room_simulator.py`'s
  "Watch the Room at ..." / `o2_shroom.py`'s "Watch the Shroom at ..."). Those are
  a separate, already-somewhat-distinct piece of operator-facing text; this spec is
  about the browser tab / page `<title>` specifically.
- **No new CLI flags.** The label is derived entirely from the `dev` value the
  caller already has; nothing new to pass on the command line.

## 4. Architecture

No data flow changes — this is two one-line additions at existing
`WebSimBackend(...)` construction call sites, once luxaeterna's `label` parameter
(see the companion spec) is available via the editable install.

```
room_simulator.py build(dev, ...)     ──▶  WebSimBackend(..., label=dev)
o2_shroom.py       build(dev, ...)     ──▶  WebSimBackend(..., label=dev)
```

## 5. Component design

### 5.1 `harness/room_simulator.py`

`build()` (`room_simulator.py:43-58`), current construction at line 55-56:

```python
backend = WebSimBackend(capability=shroom_capability(),
                         host=sim_host, port=sim_port, serve=serve)
```

becomes:

```python
backend = WebSimBackend(capability=shroom_capability(),
                         host=sim_host, port=sim_port, serve=serve,
                         label=dev)
```

`dev` is already the function's first parameter — no signature change.

### 5.2 `harness/o2_shroom.py`

`build()` (`o2_shroom.py:125-142`), current construction at line 139-140:

```python
backend = WebSimBackend(capability=shroom_capability(),
                        host=sim_host, port=sim_port, serve=serve)
```

becomes:

```python
backend = WebSimBackend(capability=shroom_capability(),
                        host=sim_host, port=sim_port, serve=serve,
                        label=dev)
```

Same shape — `dev` is already `build()`'s first parameter. `args.no_join` is not
consulted; it doesn't need to be, since `dev` already differs between the Room path
(`terrarium_boot.py`'s `_O2SimulatorFactory` always spawns this with
`--dev sim-room --no-join`) and a real player device's own invocation.

## 6. Error handling & testing

No new error paths — `label` is a plain pass-through of a value both functions
already receive and already validate (or don't need to: `dev` is just an
identifier string here as it is everywhere else it's used in these two files).

- `tests/test_room_simulator.py` — extend the existing `build("sim-room", serve=False)`
  case (currently at line 35) with `assert backend.label == "sim-room"`, using the
  public `WebSimBackend.label` attribute the companion luxaeterna spec adds.
- `tests/test_o2_shroom.py` — same extension to the existing
  `build("ie1", "TEST_PLAYER_NODE", serve=False)` case (currently at line 40):
  `assert backend.label == "ie1"`.
- Both are pure unit-level assertions on `build()`'s return value — no server,
  network, or O2 involvement, consistent with how these two test files already
  exercise `build()`.

## 7. Alternatives considered (and why rejected)

- **Special-case the label for `--no-join`** (e.g. always show literal `"ROOM"`
  instead of the dev id when `--no-join` is set). Rejected during brainstorming:
  requires threading `args.no_join` into `o2_shroom.py`'s `build()` (a signature
  change neither file currently needs), and the dev id alone already gives the
  differentiation — `sim-room` reads unambiguously as the Room simulator without
  an extra flag or branch.
- **Also label `led_smoke.py`.** Rejected for this slice as scope creep beyond the
  reported confusion — see §3.

## 8. Decisions locked (from brainstorm)

- Both call sites pass **`label=dev`**, unconditionally — no Room-vs-player
  branching logic anywhere in mm-terrarium.
- `led_smoke.py` is explicitly out of scope.
- Sequencing: this repo's change lands **after** luxaeterna's `label` parameter is
  merged and picked up by the editable install (`requirements-dev.txt`'s
  `pip install -e "/Users/chris/projects/luxaeterna[websim]"`), since `label` isn't
  a valid `WebSimBackend` kwarg until then.
