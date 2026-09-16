# Bench Replay Feasibility Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This is a spike, not a feature.** TDD is adapted, not dropped: every
> task still ends in a checked, recorded observation, but the code that
> produces it is throwaway. Nothing built here is a candidate for
> `main` -- the deliverable is the go/no-go recorded in Task 7, not the
> code from Tasks 1-6.

**Goal:** Answer design spec section 8's question in two working days: can
a throwaway replay driver take `DeviceLinkAgent`'s place inside the normal
Terrarium boot and replay one exported contract-kit scenario to a real
device within `contract.json`'s `lifecycle.bench_tolerance_ms`, with
`Terrarium`/`ArcoProcess`/`O2LiteTransport` still owning Arco and the
`game` service exactly as they do today? Record a go/no-go, the chosen
shape, measured timing and what a production version would need, in the
design spec itself.

**Architecture:** A new `harness/replay_driver.py` (spike branch only)
defines `ReplayDriver`, a class shaped exactly like the three members of
`DeviceLinkAgent` that `harness/terrarium_boot.py`'s tick loop and its
optional Console wiring actually touch (`poll()`, `closing`, `room_audio`,
`controllers()`, `canvas_urls()` -- verified in Task 2). `main()` boots
with the ALREADY-PROVEN `--no-bit --console-port <port> --room TEST`
combination (exactly what `./terrarium.sh --room TEST` runs today), plus
one new `--replay-scenario PATH` flag; right after the existing `build()`
call returns, if that flag is set, the real (but never-polled)
`DeviceLinkAgent` is discarded and a `ReplayDriver` takes its place in the
local `agent` variable every later line already uses. `Terrarium`,
`ArcoProcess` and `O2LiteTransport.start()`'s `game`/`actl` ownership claim
are untouched by this -- zero lines of their code change. The driver reads
one scenario file (Export Format v1), sends its `control_sends` at
offsets from the device's own first `/game/hello`, logs every inbound
`/game/*` message, and an offline `evaluate_expectations()` checks
`expect_out`/`expect_quiet` against the log afterward. A day-1 stop rule
falls back (Task 6) to a hand-started Arco plus a scripted o2litepy client
that claims `game` itself and self-checks with the same
`verify_service_ownership` `O2LiteTransport.start()` already uses.

**Tech Stack:** Python 3.14 in mm-terrarium's `.venv` (symlinked per
`README.md`), pytest for the one offline smoke check, o2litepy/pyarco from
the sibling `~/projects/arco` checkout, the Python Testshroom
(`harness/o2_shroom.py`) as the device under test, optionally mm-tuneshroom's
`?profile=rev1` hardware profile and an ESP32-P4 board on day 2.

**Spec:** `docs/superpowers/specs/2026-09-16-device-contract-kit-design.md`
(sections 4.2, 4.3, 8, 10, 12 especially).

## Global Constraints

- All commands run on MYCOLOGICAL (the dev Mac) inside the spike worktree
  unless a block is labeled otherwise with **RUN ON: \<HOST\>**.
- Any step that needs the bench network or a real board or phone is
  labeled **RUN ON: bench Terrarium Mac (on the bench AP)**. Design spec
  section 9 / ESP32 spec section 4.5 leaves which physical Mac that is as
  an open item ("Which Mac is a project-plan open item"; ESP32 spec
  section 11, open item 1) -- this plan does not name one.
- **Prerequisite:** mm-terrarium's Phase 1 (verb table, capabilities,
  `tuneshroom_rev1`) and Phase 3 (contract Bit, recorder,
  `tools/export_contract.py`, the eleven recorded scenarios) have landed
  on `origin/main` (design spec section 10's schedule puts both before the
  week of Oct 5). Task 1 verifies this before anything else runs; if it
  has not landed, stop and say so rather than guessing at an interface
  that does not exist yet.
- **Timing:** two working days in the week of Oct 5, ending before
  **Gate 1, Fri 2026-10-09**. Day 1 = Tasks 1-4. Day 2 = Task 5 (if day 1
  succeeded) or Task 6 (if it didn't), then Task 7 always.
- **Stop rules** (design spec section 8, verbatim in intent):
  1. If day 1 ends with no replayed message reaching the Testshroom,
     switch to the Task 6 fallback on day 2.
  2. Decide the go/no-go by end of day 2 regardless of how either probe
     went.
  3. **Never merge spike code.** Tasks 1-6 live only on branch
     `claude/bench-replay-feasibility`, which is never opened as a PR
     against `main`. Task 7's findings paragraph is committed on a
     SEPARATE, ordinary branch instead (see Task 7) -- that one is
     mergeable and is the actual deliverable.
- Every file path, function, flag, command and line number cited below
  was grepped or read directly out of `harness/terrarium_boot.py`,
  `devicelink/o2_transport.py`, `devicelink/protocol.py`,
  `control/terrarium.py`, `README.md`, `docs/MM_TERRARIUM.md` and
  `docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md` at
  commit `d39d2c4` (2026-09-16). Phases 1, 3 and 4 land on top of that
  commit before this spike runs and CAN move these line numbers -- Task 2
  step 1 re-greps them before anything is edited.
- Do not create, modify or open a PR against `/Users/chris/projects/mm-devshroom`
  (Victor's repo) at any point in this plan.
- No em dashes in code, comments, docs or commit messages on the spike
  branch -- the repo's own style uses `--` (matches every existing file
  read while researching this plan).
- Scratch output (logs, JSON dumps, the day-by-day findings notes) goes
  under `.superpowers/bench-replay-feasibility/` inside the spike
  worktree, which `.gitignore` already carves out for exactly this
  ("Agent/SDD scratch: reports, ledgers, review packages"). Nothing under
  it is ever committed.
- `$OUT` (Task 1 step 3) is a shell variable. Every new terminal needs
  it set first:
  `OUT=/Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility/.superpowers/bench-replay-feasibility/export`.
- Out of scope for this spike, by design: gesture injection (`gesture`
  steps are skipped, or a person performs the physical tap/hold/swing on
  day 2's board/phone check); `expect_frame` and `expect_play` are "not
  observable over the wire" (design spec section 8's own framing of
  section 4.3's rules) and are always skipped; nothing built here is a
  candidate for the production replay driver a follow-up plan would
  design (Task 7 lists what that follow-up needs).

---

## File structure

| File | Responsibility |
|---|---|
| `harness/replay_driver.py` (new, spike branch only) | `load_scenario(path)`; `ReplayDriver` (the `DeviceLinkAgent`-shaped seam: `poll`, `closing`, `room_audio`, `controllers`, `canvas_urls`); `evaluate_expectations(...)`. |
| `harness/terrarium_boot.py` (modified, spike branch only) | One new `--replay-scenario PATH` argparse entry (Task 3 step 2) and, right after the existing `build()` call in `main()`, a small block that substitutes `ReplayDriver` for `agent` when the flag is set. No other line changes. |
| `harness/replay_fallback_probe.py` (new, spike branch only, Task 6) | A scripted o2litepy client, independent of `Terrarium`/`ArcoProcess`, that claims `actl,game` on a hand-started Arco and self-checks with `verify_service_ownership`. |
| `.superpowers/bench-replay-feasibility/` (scratch, spike worktree, never committed) | Run logs, `evaluate_expectations` output, the day-1/day-2 raw notes Task 7 quotes from. |
| `docs/superpowers/specs/2026-09-16-device-contract-kit-design.md` (modified, on a SEPARATE ordinary branch, Task 7 only) | Section 8 gains a dated findings paragraph, in the style of the `**P7 result (...)**` / `**P8 result (...)**` paragraphs `docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md` section 7 already carries. |

---

## Day 1

### Task 1: Set up the spike worktree and verify the Phase 3 prerequisite

**Files:**
- Create (via `git worktree`, not by hand): the spike worktree itself.
- Read: `tools/export_contract.py`, `devicelink/contract.py`,
  `instruments/tuneshroom_rev1.toml` (all Phase 1/3 deliverables that must
  already exist).

**Interfaces:**
- Consumes: `origin/main` as it stands in the week of Oct 5.
- Produces: a scratch export directory (`$OUT` below) with `contract.json`
  and `scenarios/*.json`, confirmed against Export Format v1 (design spec
  sections 4.2-4.3) -- Tasks 3 and 4 read from it.

- [ ] **Step 1: Fetch and create the spike worktree**

```bash
cd /Users/chris/projects/mm-terrarium
git fetch origin
git worktree add .claude/worktrees/bench-replay-feasibility \
  -b claude/bench-replay-feasibility origin/main
cd .claude/worktrees/bench-replay-feasibility
ln -s /Users/chris/projects/mm-terrarium/.venv .venv
```

Expected: `Preparing worktree (new branch 'claude/bench-replay-feasibility')`
followed by `HEAD is now at <sha> <subject>`. The `ln -s` step is
`README.md`'s own documented fix for "A fresh git worktree has no `.venv`
at all."

- [ ] **Step 2: Confirm the prerequisite actually landed**

```bash
ls tools/export_contract.py devicelink/contract.py instruments/tuneshroom_rev1.toml
git log --oneline -i --grep="contract kit\|export_contract\|tuneshroom_rev1" | head -20
```

Expected: all three paths print with no "No such file or directory";
at least one commit shows. **If any path is missing, STOP.** Do not
improvise an interface that has not landed -- report back that Phase 1
and/or Phase 3 have not shipped yet and the spike cannot start.

- [ ] **Step 3: Run the export tool into a scratch directory**

```bash
mkdir -p .superpowers/bench-replay-feasibility
OUT="$(pwd)/.superpowers/bench-replay-feasibility/export"
mkdir -p "$OUT"
.venv/bin/python -m tools.export_contract "$OUT"
ls "$OUT" "$OUT/scenarios"
```

Expected: `$OUT/contract.json` plus eleven files under `$OUT/scenarios/`
matching design spec section 4.3's table: `boot_hello_heartbeat.json`,
`explicit_join_role.json`, `lobby_tap_join.json`,
`timed_frames_hold_last.json`, `gestures_after_role.json`,
`deny_stays_hellod.json`, `release_keeps_display.json`,
`play_known_and_unknown.json`, `link_loss_rejoin.json`,
`error_no_state_change.json`, `malformed_dropped.json`. If
`tools/export_contract.py --help` shows a different CLI than the spec's
single `<out-dir>` positional argument, use the real one and write the
difference down now -- it goes in Task 7's findings as a drifted
interface.

- [ ] **Step 4: Confirm the export matches Export Format v1's documented shape**

```bash
.venv/bin/python -c "
import json
c = json.load(open('$OUT/contract.json'))
assert 'lifecycle' in c and 'bench_tolerance_ms' in c['lifecycle'], sorted(c)
print('bench_tolerance_ms:', c['lifecycle']['bench_tolerance_ms'])
print('verbs:', sorted(v['address'] for v in c['verbs']))
print('instruments:', sorted(c.get('instruments', {})))
"
```

Expected: `bench_tolerance_ms: {'frame': 50, 'heartbeat': 1000}` (design
spec section 4.2, quoted verbatim in the task brief's Export Format v1);
`verbs` includes `/game/hold` and `/game/swing` (decision D7); `instruments`
includes `tuneshroom_rev1`. Write the printed `bench_tolerance_ms` down --
Task 3's `ReplayDriver` reads it from this same file, never a hardcoded
constant.

- [ ] **Step 5: Confirm nothing was accidentally staged**

```bash
git status --short
```

Expected: empty. The export went to `$OUT` under `.superpowers/`, which
`.gitignore` already excludes.

---

### Task 2: Map the boot sequence and decide the replay seam

Read-only. Its deliverable is a decision, written down as the docstring
Task 3's new file opens with -- not a separate document.

**Files:**
- Read: `harness/terrarium_boot.py`, `devicelink/o2_transport.py`,
  `devicelink/agent.py`, `control/terrarium.py`.

**Interfaces:**
- Consumes: nothing built yet.
- Produces: the exact current line numbers and the seam decision Task 3's
  code and docstring rely on.

- [ ] **Step 1: Re-locate the five construction points**

As read for this plan (commit `d39d2c4`), the boot sequence is:

- `harness/terrarium_boot.py:352` -- `gs = GameServer(bit_registry, room_binding=room_binding, cue_horizon=..., clock=clock, carried_instruments=..., admin_devices=...)`.
- `harness/terrarium_boot.py:356` -- `terrarium = Terrarium(terrarium_config, gs, room_binding, boot_config=config, arco_command=arco_command, ...)`. `terrarium.load_room(room_spec.name)` at `:364` is what actually spawns Arco (`control/terrarium.py`'s `Terrarium.load_room`, "spawning arco" progress stage) -- this does not touch `DeviceLinkAgent` or any Bit at all.
- `harness/terrarium_boot.py:412` -- `agent = DeviceLinkAgent(gs, server, room_audio=room_audio, horizon=..., clock=clock, on_join_denied=..., stale_timeout=...)`, where `server` is `build()`'s already-adopted `O2LiteTransport`.
- `harness/terrarium_boot.py:1705` (inside `main()`) -- `transport = O2LiteTransport()`, constructed BEFORE `build()` is called.
- `harness/terrarium_boot.py:1924` -- `transport.start(o2lite, pump=arco.poll)`, called AFTER `build()` returns, guarded on `arco is not None`. This is where `verify_service_ownership` claims and self-checks `game`+`actl` (`devicelink/o2_transport.py:445-475`).

Re-run the greps that produced these against the SPIKE worktree's actual
checkout, since they may have shifted:

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
grep -n "gs = GameServer(\|terrarium = Terrarium(\|agent = DeviceLinkAgent(\|transport = O2LiteTransport()\|transport.start(o2lite" harness/terrarium_boot.py
```

Expected: five matches, one per line above. If a match moved, read the
surrounding function and re-derive which line now does the same job
before continuing -- do not assume the numbers above still hold.

- [ ] **Step 2: Confirm the tick loop's whole contract with `agent`**

```bash
grep -n "agent\.poll()\|getattr(agent, \"closing\"\|getattr(agent, \"room_audio\"\|agent\.controllers\|agent\.canvas_urls\|agent\._on_room_frame\|agent\.start_requests\|agent\.prepare_requests\|agent\.prepare_authority" harness/terrarium_boot.py
```

Expected matches confirm: `agent.poll()` runs every tick-loop iteration
(`_wait_in_setup`, `_serve_until_done`, `_wait_for_load`,
`_wait_for_room_ready`); `getattr(agent, "closing", 0)` gates
`_serve_until_done`'s exit; `getattr(agent, "room_audio", None)` is read
exactly once, right after `build()` returns; and (only when
`--console-port` is given) `ConsoleAgent(...)` is constructed with
`room_controllers=agent.controllers` and `canvas_urls=agent.canvas_urls`
as plain attribute references, plus `agent._on_room_frame = ...`,
`agent.start_requests = ...`, `agent.prepare_requests = ...`,
`agent.prepare_authority = ...` set as new attributes afterward (the last
three only matter once `--web-build`/a www server is in play, which Tasks
1-4 never pass).

- [ ] **Step 3: Confirm the transport must never be drained twice**

```bash
grep -n "drain_inbound\|drain_new_clients" devicelink/agent.py devicelink/o2_transport.py
```

Expected: `devicelink/agent.py`'s `poll()` is the ONLY production caller of
both, and `devicelink/o2_transport.py`'s `drain_inbound()` docstring
confirms it pumps o2lite and then hands back AND CLEARS its buffer
(`drained, self._inbound = self._inbound, []`). Two independent callers
would each see a different subset of messages. This is why the decision
below constructs a `ReplayDriver` to REPLACE `agent` in the tick loop,
never to run alongside the real one.

- [ ] **Step 4: Record the decision**

The smallest spike-flag change, and the one this plan implements in Task
3:

1. Boot with the ALREADY-PROVEN combination `--no-bit --console-port
   <port> --room TEST` -- exactly what `./terrarium.sh --room TEST` runs
   today (`terrarium.sh`'s own comment: "NO Bit and NO spawned
   Testshrooms... Without --room it boots to NO_ROOM"; `README.md`:
   "`./terrarium.sh --room TEST # Arco + the TEST Room up, no Bit`"). This
   reaches `gs.state == IDLE`, no Bit ever loaded, with ZERO changes to
   `main()`'s existing bit-selection or validation logic.
   **(recommended)** over inventing a new "no Bit, no console needed"
   code path, because it touches no existing conditional at all -- every
   line this plan adds to `harness/terrarium_boot.py` is new, not edited.
2. Add one new argparse entry, `--replay-scenario PATH` (Task 3 step 2).
3. Right after the existing `gs, server, agent, arco, teardown, terrarium
   = build(...)` call, add a small block: if `args.replay_scenario` is
   set, load the scenario, construct a `ReplayDriver`, and REASSIGN the
   local `agent` variable to it. Every later line in `main()` that reads
   `agent` (Step 2 above) already tolerates this, because `ReplayDriver`
   implements the same three-member shape. **(recommended)** over
   threading a new parameter through `build()`'s own signature, because
   `build()`'s real `DeviceLinkAgent` construction has no network side
   effect by itself (only `.poll()` ever drains the transport -- Step 3
   above), so discarding it unread and substituting the tick loop's
   `agent` variable is a strictly smaller diff than teaching `build()` a
   new construction branch. The discarded real agent still holds an idle
   `AudioBridge`/`LightSession` per TEST fixture (`_setup_room()` inside
   `DeviceLinkAgent.__init__` runs regardless) -- wasteful but harmless,
   since nothing ever calls its `.poll()` to render or feed them. Record
   this as a known inefficiency in Task 7, not something to fix.
4. Because `--console-port` IS given (point 1), `ConsoleAgent` will be
   constructed and will read `agent.controllers` / `agent.canvas_urls` at
   construction time -- `ReplayDriver` needs both methods, trivially
   returning `{}`, purely to survive that construction. The Console page
   itself is never opened in Tasks 1-4.

No commit for this task -- nothing is written until Task 3.

---

### Task 3: Prototype the replay driver behind a spike flag

**Files:**
- Create: `harness/replay_driver.py`
- Modify: `harness/terrarium_boot.py` (one new argparse entry, one new
  block in `main()`, per Task 2 step 4)

**Interfaces:**
- Consumes: `O2LiteTransport`'s transport interface -- verified in
  `devicelink/o2_transport.py`: `drain_new_clients() -> list`,
  `drain_inbound() -> list[tuple[client, dict]]`,
  `bind_dev(dev: str, client, *, protoversion: str = "") -> None`,
  `send(dev: str, msg: dict) -> None`. `devicelink.protocol.decode(msg:
  dict) -> Envelope(timestamp: float, address: str, typespec: str, args:
  list)` and `protocol.parse_game_address(address: str) -> str | None`.
  `FakeO2Lite`/`O2LiteTransport` (both in `devicelink/o2_transport.py`)
  for the offline smoke check.
- Produces: `load_scenario(path: str) -> tuple[dict, dict]` (scenario
  dict, `bench_tolerance_ms` dict). `ReplayDriver(transport, clock,
  scenario, tolerances)` with `.poll()`, `.closing` (always `0`),
  `.room_audio` (always `None`), `.controllers()` (`{}`),
  `.canvas_urls()` (`{}`), `.seen` (list, appended to by `.poll()`),
  `.dump_seen(path)`. `evaluate_expectations(scenario: dict, dev: str, t0:
  float, seen: list, tolerances: dict) -> list[str]` (empty = pass) --
  Task 4 calls this.

- [ ] **Step 1: Write `harness/replay_driver.py` and smoke-test it offline, no Arco needed**

```python
"""Spike-only: a throwaway replay driver for design spec section 8's
feasibility question (docs/superpowers/specs/
2026-09-16-device-contract-kit-design.md). Lives ONLY on the spike branch
claude/bench-replay-feasibility; never merged.

Takes DeviceLinkAgent's place in harness/terrarium_boot.py's tick loop
(spliced in by main(), not inside build() -- see the plan's Task 2 step
4): Terrarium, ArcoProcess and O2LiteTransport boot and claim `actl,game`
exactly as they do today; this object becomes the tick loop's only
`agent`, and therefore the transport's ONLY drain_inbound() caller for the
process's whole life (two callers would each see a different subset of
messages -- see the plan's Task 2 step 3).

It is a "dumb timeline player": every control_sends step fires at its
scheduled offset from the device's first hello, never gated on anything
the device does in between (a real join, a real gesture). GameServer is
never called into at all. That is the spike's main documented limitation
-- see the plan's Task 7 "what a production version needs".
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from devicelink import protocol

logger = logging.getLogger("replay_driver")


def load_scenario(path: str) -> tuple[dict, dict]:
    """One scenario file plus its sibling contract.json's bench
    tolerances. Layout is tools/export_contract.py's (design spec section
    4.2): <out-dir>/contract.json and <out-dir>/scenarios/<name>.json, so
    the contract is two directories up from a scenario file."""
    scenario_path = Path(path)
    contract_path = scenario_path.parent.parent / "contract.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    return scenario, contract["lifecycle"]["bench_tolerance_ms"]


class ReplayDriver:
    def __init__(self, transport, clock, scenario: dict, tolerances: dict):
        self.transport = transport
        self.clock = clock
        self.scenario = scenario
        self.tolerances = tolerances
        # The shape harness/terrarium_boot.py's tick loop and its optional
        # Console wiring read off `agent` -- see the plan's Task 2 step 2.
        self.closing = 0
        self.room_audio = None
        self.dev: str | None = None
        self.t0: float | None = None
        self.seen: list[dict] = []
        self._sent: set[int] = set()
        self._control_sends = sorted(
            (s for s in scenario["steps"] if "control_sends" in s),
            key=lambda s: s["t"])

    def controllers(self) -> dict:
        return {}

    def canvas_urls(self) -> dict:
        return {}

    def poll(self) -> None:
        self.transport.drain_new_clients()
        for client, msg in self.transport.drain_inbound():
            self._handle(client, msg)
        if self.dev is not None:
            self._send_due()

    def _handle(self, client, msg: dict) -> None:
        try:
            env = protocol.decode(msg)
        except ValueError as exc:
            logger.warning("replay: dropping unparseable frame: %s", exc)
            return
        verb = protocol.parse_game_address(env.address)
        if verb is None:
            logger.warning("replay: dropping non-/game address %r", env.address)
            return
        if not env.args or not isinstance(env.args[0], str):
            logger.warning("replay: dropping /game/%s with no dev argument", verb)
            return
        dev = env.args[0]
        self.seen.append({"t": self.clock(), "address": env.address,
                          "typespec": env.typespec, "args": env.args,
                          "timestamp": env.timestamp})
        logger.info("replay: <- %s %s", env.address, env.args)
        if verb == "hello":
            protoversion = env.args[2] if len(env.args) > 2 else ""
            self.transport.bind_dev(dev, client, protoversion=protoversion)
            if self.dev is None:
                self.dev = dev
                self.t0 = self.clock()
                logger.info("replay: scenario clock started at hello from %s", dev)

    def _send_due(self) -> None:
        now_ms = (self.clock() - self.t0) * 1000.0
        for i, step in enumerate(self._control_sends):
            if i in self._sent or step["t"] > now_ms:
                continue
            self._sent.add(i)
            self._send_control(step["control_sends"])

    def _send_control(self, spec: dict) -> None:
        address = spec["address"].replace("$DEV", self.dev)
        at_ms = spec.get("at")
        when = self.t0 + at_ms / 1000.0 if at_ms is not None else self.clock()
        msg = {"address": address, "typespec": spec["typespec"],
               "args": spec["args"], "timestamp": when}
        logger.info("replay: -> %s %s at %.3f", address, spec["args"], when)
        self.transport.send(self.dev, msg)

    def dump_seen(self, path: str) -> None:
        Path(path).write_text(json.dumps(
            {"t0": self.t0, "dev": self.dev, "seen": self.seen}, indent=2),
            encoding="utf-8")
        logger.info("replay: wrote %d observed messages to %s", len(self.seen), path)


def _matches(actual, expected, dev: str) -> bool:
    if expected == "*":
        return True
    if expected == "$DEV":
        return actual == dev
    if isinstance(expected, float) or isinstance(actual, float):
        # Export Format v1: numbers match within 1e-3.
        return abs(float(actual) - float(expected)) <= 1e-3
    return actual == expected


def evaluate_expectations(scenario: dict, dev: str, t0: float, seen: list,
                          tolerances: dict) -> list[str]:
    """Check every expect_out/expect_quiet step in `scenario` against
    `seen` (ReplayDriver.seen, or its dump's "seen" list -- entries are
    {"t", "address", "typespec", "args", "timestamp"}, "t" on the SAME
    clock as `t0`). Returns human-readable failures; [] is a pass.

    Matching follows Export Format v1, widened by the bench tolerances: a
    seen message satisfies an expect_out when its address, typespec and
    args match and its time falls in [t - slack, t + within_ms + slack],
    and each seen message satisfies at most one expect_out (three expected
    heartbeats need three hellos -- without this, the first hello would
    satisfy all three and boot_hello_heartbeat would pass with no
    heartbeat at all). expect_quiet windows are [t, t + for_ms).

    expect_frame, expect_play and gesture steps are always skipped --
    design spec section 8 calls the first two "not observable over the
    wire" and gestures "can't be injected" on the bench.

    JUDGMENT CALL, recorded for Task 7: the slack, and the stamp_t budget,
    is tolerances["heartbeat"] for a hello and tolerances["frame"] for
    everything else -- the export format does not say which bucket a
    gesture verb's onset stamp uses, and this spike has no gesture verb to
    test it against (gestures are skipped), so this default is untested.
    """
    failures = []
    used: set[int] = set()
    for step in sorted(scenario["steps"], key=lambda s: s["t"]):
        if "expect_out" in step:
            spec = step["expect_out"]
            address = spec["address"].replace("$DEV", dev)
            verb = address.rsplit("/", 1)[-1]
            slack_ms = tolerances["heartbeat" if verb == "hello" else "frame"]
            earliest = t0 + (step["t"] - slack_ms) / 1000.0
            deadline = t0 + (step["t"] + spec["within_ms"] + slack_ms) / 1000.0
            match_i = next((i for i, m in enumerate(seen)
                            if i not in used
                            and m["address"] == address
                            and m["typespec"] == spec["typespec"]
                            and len(m["args"]) == len(spec["args"])
                            and all(_matches(a, e, dev)
                                    for a, e in zip(m["args"], spec["args"]))
                            and earliest <= m["t"] <= deadline), None)
            if match_i is None:
                failures.append(f"t={step['t']}ms: expected {address} "
                                f"{spec['typespec']} {spec['args']} within "
                                f"{spec['within_ms']}ms (slack {slack_ms}ms), "
                                "never seen matching")
                continue
            used.add(match_i)
            match = seen[match_i]
            stamp_t = spec.get("stamp_t")
            if stamp_t is not None:
                actual = match["timestamp"] - t0
                expected = stamp_t / 1000.0
                if abs(actual - expected) > slack_ms / 1000.0:
                    failures.append(
                        f"t={step['t']}ms: {address} stamp off by "
                        f"{abs(actual - expected) * 1000:.1f}ms, "
                        f"budget {slack_ms}ms")
        elif "expect_quiet" in step:
            spec = step["expect_quiet"]
            window_start = t0 + step["t"] / 1000.0
            window_end = window_start + spec["for_ms"] / 1000.0
            addresses = {a.replace("$DEV", dev) for a in spec["addresses"]}
            noisy = [m for m in seen if m["address"] in addresses
                     and window_start <= m["t"] < window_end]
            if noisy:
                failures.append(f"t={step['t']}ms: expected quiet on "
                                f"{sorted(addresses)} for {spec['for_ms']}ms, "
                                f"saw {noisy}")
    return failures
```

Smoke-test it offline, no Arco, no Testshroom, using the same
`FakeO2Lite`/`O2LiteTransport` pairing `tests/test_o2_transport.py`
already establishes:

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
.venv/bin/python -c "
from devicelink.o2_transport import FakeO2Lite, O2LiteTransport
from harness.replay_driver import ReplayDriver, evaluate_expectations

fake = FakeO2Lite()
fake.set_services('actl')
transport = O2LiteTransport()
transport.start(fake)

scenario = {'name': 'smoke', 'steps': [
    {'t': 0, 'control_sends': {'address': '/\$DEV/room', 'typespec': 'b',
                               'args': [{'name': 'IDLE'}], 'at': None}},
]}
driver = ReplayDriver(transport=transport, clock=fake.time_get,
                      scenario=scenario, tolerances={'frame': 50, 'heartbeat': 1000})

fake.deliver('/game/hello', 'ssss', ('ie1', '', '', 'tuneshroom_rev1'), 0.0)
driver.poll()
assert driver.dev == 'ie1', driver.dev
fake.set_time(fake.time_get() + 0.2)
driver.poll()
assert fake.sent, 'expected the /ie1/room control_send to have gone out'
print('OK:', fake.sent)
print('eval:', evaluate_expectations(scenario, 'ie1', driver.t0, driver.seen,
                                     {'frame': 50, 'heartbeat': 1000}))
"
```

Expected: `OK: [('/ie1/room', ..., 'b', ({'name': 'IDLE'},))]` (the
`b`-typespec argument round-tripped through `to_o2_arg` as a `Blob`) and
`eval: []` (no `expect_out`/`expect_quiet` steps in this synthetic
scenario, so nothing to fail). If this fails, fix `replay_driver.py` here
-- do not spend a live Arco boot cycle debugging a bug this catches for
free.

- [ ] **Step 2: Add the CLI flag**

In `harness/terrarium_boot.py`'s `_build_arg_parser()`, immediately after
the existing `--no-bit` entry (confirmed at the line Task 2 step 1's grep
reported, next to `--profile`), add:

```python
    ap.add_argument("--replay-scenario", default=None, metavar="PATH",
                    help="SPIKE ONLY (docs/superpowers/plans/"
                         "2026-09-16-bench-replay-feasibility.md): replay "
                         "this exported scenario file in place of the real "
                         "DeviceLinkAgent. Arco and `game` are still owned "
                         "by the standard boot path; combine with --no-bit "
                         "and --room, exactly like ./terrarium.sh --room "
                         "TEST. Never used outside this spike.")
```

- [ ] **Step 3: Splice the driver in after `build()` returns**

Immediately after the existing call (the one Task 2 step 1 located at
`harness/terrarium_boot.py:1708-1719`):

```python
    gs, server, agent, arco, teardown, terrarium = build(
        config, registry.lazy_class_map(),
        arco_command=[args.arco_command],
        room_binding=room_binding, room_spec=room_spec,
        terrarium_config=terrarium_config,
        transport=transport, clock=clock,
        arco_process_cls=arco_process_cls, on_join_denied=_print_join_denied,
        runs_dir=runs_dir, run_id=run_id,
        arco_ready_timeout=args.arco_ready_timeout)
```

add:

```python
    if args.replay_scenario:
        from harness.replay_driver import ReplayDriver, load_scenario
        scenario, tolerances = load_scenario(args.replay_scenario)
        agent = ReplayDriver(transport=server, clock=clock,
                             scenario=scenario, tolerances=tolerances)
        seen_path = os.path.join(
            ".superpowers", "bench-replay-feasibility",
            f"seen-{scenario['name']}.json")
        teardown.push("replay-driver-dump",
                      lambda: agent.dump_seen(seen_path))
        print(f"REPLAY: driving scenario {scenario['name']!r} in place of "
             f"DeviceLinkAgent (docs/superpowers/plans/"
             f"2026-09-16-bench-replay-feasibility.md)", flush=True)
```

`teardown.push(name, callback)` is `TeardownStack`'s existing interface
(already used at `harness/terrarium_boot.py`'s own `teardown.push("arco",
arco.shutdown)` and elsewhere) -- reusing it means the dump happens on
EVERY shutdown path this process already handles, including Ctrl-C
(`sigterm_as_keyboard_interrupt()` turns SIGTERM into `KeyboardInterrupt`,
and `main()`'s outer `try/finally` always calls `shutdown(teardown, ...)`).

- [ ] **Step 4: Confirm it boots with zero devices, then Ctrl-C it**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
.venv/bin/python -m harness.terrarium_boot --room TEST --no-bit \
  --console-port 8772 \
  --replay-scenario "$OUT/scenarios/boot_hello_heartbeat.json"
```

Expected stdout, in order: `REPLAY: driving scenario 'boot_hello_heartbeat'
...`, then `DeviceLink running on o2lite ensemble`
(`markers.CONTROL_TRANSPORT_READY` -- confirms `game`+`actl` ownership
exactly as an unmodified boot would show), then the Console's
`BROWSE_URL:` line. No crash, no traceback. Ctrl-C it; expected: a clean
shutdown with no unhandled exception, and
`.superpowers/bench-replay-feasibility/seen-boot_hello_heartbeat.json`
now exists (even if its `"seen"` list is empty -- nothing connected yet).
This is the "does it boot and idle cleanly" checkpoint, separate from
"does it see a device," which is Task 4.

- [ ] **Step 5: Commit on the spike branch**

```bash
git add harness/replay_driver.py harness/terrarium_boot.py
git commit -m "spike: replay driver behind --replay-scenario (never merged)"
```

---

### Task 4: Exercise the replay driver against the Python Testshroom

This is the day-1 stop-rule checkpoint: if nothing here reaches the
Testshroom, day 2 runs Task 6 instead of Task 5.

**Files:**
- Modify: `harness/replay_driver.py` (fixes found while exercising it)
- Read: whichever scenario files are tried, plus `harness/o2_shroom.py
  --help`

**Interfaces:**
- Consumes: `harness/o2_shroom.py`'s CLI as a real subprocess acting as
  the device under test.
- Produces: pass/fail notes per scenario tried, quoted in Task 7.

- [ ] **Step 1: Check the Testshroom's actual flags before assuming any of them**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
.venv/bin/python -m harness.o2_shroom --help
```

Confirm `--dev` (default `ie1`), `--instrument` (default `testshroom`;
"Pass an empty string to stay undeclared, resolving to defaultshroom"),
`--no-join` ("Send /game/hello but never /game/join, and emit no
gestures"), and `--node` exist with this plan's assumed defaults and
semantics. **If any has drifted, use the real one** and note the drift in
Task 7.

- [ ] **Step 2: Boot the stack against the simplest scenario, `boot_hello_heartbeat`**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
.venv/bin/python -m harness.terrarium_boot --room TEST --no-bit \
  --console-port 8772 \
  --replay-scenario "$OUT/scenarios/boot_hello_heartbeat.json" \
  2>&1 | tee .superpowers/bench-replay-feasibility/run-boot_hello_heartbeat.log
```

Run this in its own terminal: it blocks, and Step 6 stops it with
Ctrl-C. Wait for `DeviceLink running on o2lite ensemble` in the log
before moving on. Two Room-fixture simulators for TEST's `main` and `accent` fixtures
(`control/terrarium.py`'s `_bind_room_fast_path`, spawned by
`harness/terrarium_boot.py`'s `_O2SimulatorFactory` as real
`harness.o2_shroom --no-join --room-type TEST --fixture <name>`
subprocesses) join in the background -- this is existing, unmodified
behavior and unrelated to the device under test below.

- [ ] **Step 3: Read the actual scenario before assuming it matches spec section 4.3's abbreviated example**

```bash
cat "$OUT/scenarios/boot_hello_heartbeat.json"
```

Design spec section 4.3's inline JSON example is `timed_frames_hold_last`,
a DIFFERENT scenario -- do not assume `boot_hello_heartbeat`'s steps
match it. Note its actual `steps` list (per its row in the eleven-scenario
table: "hello declares an instrument; nothing else goes out before a
join").

- [ ] **Step 4: Spawn the Testshroom as the device under test**

**(recommended)** start with `--no-join`, matching this scenario's own
description exactly, before trying a join-carrying scenario -- it isolates
"does hello even reach the driver" from "does role/join handling work."

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
.venv/bin/python -m harness.o2_shroom --dev ie1 \
  --instrument tuneshroom_rev1 --no-join \
  2>&1 | tee .superpowers/bench-replay-feasibility/testshroom-boot_hello_heartbeat.log
```

**(recommended)** `--instrument tuneshroom_rev1`, not the harness's own
default `testshroom`, on every invocation in this task -- the contract
kit's whole point is Rev 1 parity, and testing the wrong declared
instrument would silently validate nothing about it.

Expected: the Testshroom's own stdout shows `clock synced at ...`
(`markers.DEVICE_CLOCK_SYNCED`), then sits waiting.

- [ ] **Step 5: Check the replay driver's log**

```bash
grep "replay:" .superpowers/bench-replay-feasibility/run-boot_hello_heartbeat.log
```

Expected: `replay: <- /game/hello ['ie1', '', '', 'tuneshroom_rev1']`
followed by `replay: scenario clock started at hello from ie1`. If the
scenario's Rule 1 heartbeat repeat matters (hello resent every 5s per
`contract.json`'s `lifecycle.hello_interval_s`), wait at least one
interval before the next step.

- [ ] **Step 6: Evaluate against the scenario's own expectations**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
.venv/bin/python -c "
import json
from harness.replay_driver import evaluate_expectations
scenario = json.load(open('$OUT/scenarios/boot_hello_heartbeat.json'))
dump = json.load(open('.superpowers/bench-replay-feasibility/seen-boot_hello_heartbeat.json'))
failures = evaluate_expectations(scenario, 'ie1', dump['t0'], dump['seen'],
                                 {'frame': 50, 'heartbeat': 1000})
print('PASS' if not failures else 'FAIL:\n' + '\n'.join(failures))
"
```

(`seen-boot_hello_heartbeat.json` is written by Task 3 step 3's
`teardown.push` when the stack is stopped -- Ctrl-C the boot process
first, then run this.) Record PASS/FAIL and, on FAIL, the exact failure
strings -- this goes in Task 7 verbatim.

- [ ] **Step 7: If time remains, repeat against `explicit_join_role`**

```bash
cat "$OUT/scenarios/explicit_join_role.json"   # read device.join_node first
```

Re-run steps 2-6 with `--replay-scenario "$OUT/scenarios/explicit_join_role.json"`,
and spawn the Testshroom WITHOUT `--no-join`, passing
`--node <the scenario's device.join_node value>` (read from the file, never
guessed -- `o2_shroom.py --node` defaults to `TEST_PLAYER_NODE`, which is
TestBit's node, not the Contract Bit's). Recall from Task 2's decision
that `ReplayDriver` sends `control_sends` purely on elapsed time since
hello, never gated on the device's own `/game/join` arriving first -- a
role blob may go out before the device has actually asked for one. This
is expected, and is exactly the "dumb timeline player" limitation Task 7
records.

- [ ] **Step 8: Stop everything and record the day-1 result**

Ctrl-C the Testshroom, then the stack. Write a short note (not committed)
at `.superpowers/bench-replay-feasibility/day1-notes.md`: which scenarios
were tried, PASS/FAIL per `evaluate_expectations`, and the raw
`replay:`-tagged log lines for at least one full exchange. **Apply the
stop rule now:** if at least one message was successfully replayed AND
observed in either direction (even just the hello exchange, even if
`evaluate_expectations` found other failures), day 1 has satisfied
design spec section 8's minimum bar -- proceed to Task 5 on day 2. If
NOTHING reached the Testshroom in either direction (a boot failure, a
crash, `game` never routing), stop here and do Task 6 on day 2 instead.

---

## Day 2

### Task 5: Optional -- bench Terrarium Mac, the phone hardware profile, and a board

Only attempted if Task 4's stop-rule check passed. This task is
explicitly best-effort (the task brief: "Optional, only if day 1 went
well") -- if the bench network, a phone, or a board is not available or
not working on day 2 morning, skip straight to Task 7 and say so in the
findings.

**Files:** none new; reruns Task 1 and Task 4's commands against a
different host and device.

**Interfaces:**
- Consumes: mm-tuneshroom's `?profile=rev1` hardware profile (design spec
  section 6.1) and `--web-build`/`WWW_URL` (verified in
  `docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md`
  sections 4.1, 8, and `harness/terrarium_boot.py`'s `--www-port`), an
  already-built mm-tuneshroom web bundle, and, if available, an ESP32-P4
  board running the Rev 1 firmware.
- Produces: the same PASS/FAIL notes Task 4 produced, against real
  hardware instead of the Python Testshroom.

- [ ] **Step 1: RUN ON: bench Terrarium Mac (on the bench AP) -- repeat Task 1's worktree setup**

Follow Task 1 steps 1-4 on the bench Terrarium Mac, adjusting
`/Users/chris/projects/mm-terrarium` if that Mac's checkout lives
elsewhere (whichever
machine that turns out to be -- design spec section 9 / ESP32 spec
section 4.5 leave this an open item; do not guess a hostname).

- [ ] **Step 2: RUN ON: bench Terrarium Mac (on the bench AP) -- stage the app and boot so a phone can load it**

`--web-build` is a `harness/run_stack.py` option, not a
`harness/terrarium_boot.py` one: run_stack copies the build into
`www/app/` with `stage_web_build()`, and terrarium_boot's own static
server (`_start_www_server`, port `WWW_PORT` = 8788 in
`harness/www_server.py`) serves it. Stage it the same way, then boot
exactly as in Task 4. `WEB_BUILD` is the `build/web` directory of an
mm-tuneshroom checkout on that Mac, built there with `tool/sim build`
(mm-tuneshroom `CLAUDE.md`). This plan does not build or modify
mm-tuneshroom, and where that checkout lives on the bench Mac is not
known in advance, so set it explicitly:

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
WEB_BUILD="<absolute path to that mm-tuneshroom checkout>/build/web"
.venv/bin/python -c "from harness.run_stack import stage_web_build; stage_web_build('$WEB_BUILD', 'www')"
.venv/bin/python -m harness.terrarium_boot --room TEST --no-bit \
  --console-port 8772 \
  --replay-scenario "$OUT/scenarios/boot_hello_heartbeat.json"
```

Expected: a `WWW_URL:` line beside `BROWSE_URL:`.

- [ ] **Step 3: RUN ON: bench Terrarium Mac (on the bench AP) -- join from the phone's hardware profile**

On a phone on the bench AP, browse to `<WWW_URL>app/index.html?profile=rev1&dev=ie1&ens=arco`
(the printed `WWW_URL` ends in `/`, and the app is served under
`app/`; `profile=rev1` is design spec section 6.1's selection). Repeat Task 4 steps 5-6's log check and
`evaluate_expectations` call against this run's dump.

- [ ] **Step 4: RUN ON: bench Terrarium Mac (on the bench AP) -- repeat against a board, if one is on hand**

If an ESP32-P4 running the Rev 1 firmware is available on the bench,
power it on pointed at the same Arco/ensemble and repeat the same log
check. If none is available, write "no board available on \<date\>" in the
findings rather than skipping this line silently.

- [ ] **Step 5: Record results**

Append to `.superpowers/bench-replay-feasibility/day2-notes.md`: which of
phone/board were tried, PASS/FAIL, and anything that differed from the
Python Testshroom's behavior in Task 4 (timing, encoding, anything
`wire_flavor`-related for the phone's `o2ws/1` protoversion).

---

### Task 6: Fallback probe -- hand-started Arco plus a scripted o2litepy client

Only attempted if Task 4's stop-rule check failed on day 1.

**Files:**
- Create: `harness/replay_fallback_probe.py`

**Interfaces:**
- Consumes: `harness.arco_paths.ensure_o2litepy()` (already used by
  `harness/terrarium_boot.py`'s own `_o2lite_module()`),
  `devicelink.o2_transport.{GAME_VERBS, SERVICES, pull_args,
  verify_service_ownership}` (all four confirmed present in that module),
  a hand-started Arco binary.
- Produces: a pass/fail answer to design spec section 8's fallback
  question: "does a device's hello reach it, and does clock sync work?"

- [ ] **Step 1: Start Arco by hand, in its own shell**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility/arcoserver
/Users/chris/projects/arco/apps/pytest/server
```

(`/Users/chris/projects/arco` is the sibling checkout
`harness/arco_paths.py`'s `sibling_path()` resolves to by default; if
`MM_ARCO_PATH` is set in this shell, use that path's `apps/pytest/server`
instead -- check with `echo $MM_ARCO_PATH` first.) `arcoserver/` is the
required cwd -- confirmed by `harness/terrarium_boot.py`'s own
`_arco_popen`, which sets `cwd=ARCOSERVER_DIR`, and by
`docs/MM_TERRARIUM.md`'s o2lite-cutover note ("Arco is launched with
`arcoserver/` as its cwd... reads prefs from its cwd"). Leave this
running in its own terminal for the rest of this task.

- [ ] **Step 2: Write the scripted o2litepy client**

```python
"""Spike-only, Task 6 fallback probe (design spec section 8's "Fallback
to try"): a scripted o2litepy client, independent of Control,
ArcoProcess and O2LiteTransport, that connects to a HAND-STARTED Arco,
claims `game`+`actl` itself, and self-checks the claim with
devicelink.o2_transport.verify_service_ownership -- the SAME check
O2LiteTransport.start() makes in the standard boot
(devicelink/o2_transport.py:445-475), so a pass here means a hand-started
Arco behaves the same way for a real device's hello.

THROWAWAY: spike branch only, never merged.
"""
from __future__ import annotations

import logging
import time

from harness.arco_paths import ensure_o2litepy
from devicelink.o2_transport import (GAME_VERBS, SERVICES, pull_args,
                                     verify_service_ownership)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fallback_probe")


def main() -> None:
    if not ensure_o2litepy():
        raise SystemExit("could not import o2litepy; set MM_ARCO_PATH")
    from o2litepy import o2lite

    o2lite.initialize("arco")   # ensemble name -- matches --ensemble's
                                 # default in harness/run_stack.py and
                                 # harness/o2_shroom.py
    print("waiting for clock sync...")
    deadline = time.monotonic() + 15.0
    while o2lite.time_get() < 0:
        if time.monotonic() > deadline:
            raise SystemExit("no clock sync within 15s -- is Arco running "
                             "with arcoserver/ as its cwd?")
        o2lite.poll()
        time.sleep(0.05)
    print(f"clock synced at {o2lite.time_get():.3f}")

    o2lite.set_services(SERVICES)      # "actl,game"
    received: list[tuple[str, str, list]] = []

    def _on_message(address, typespec, info) -> None:
        received.append((address, typespec, pull_args(o2lite, typespec or "")))

    for verb in GAME_VERBS:
        o2lite.method_new(f"/game/{verb}", None, True, _on_message, None)

    ok_game = verify_service_ownership(o2lite, "game", timeout=10.0,
                                       resend_interval=2.0)
    print(f"game ownership: {'OK' if ok_game else 'FAILED'}")
    ok_actl = verify_service_ownership(o2lite, "actl", timeout=10.0,
                                       resend_interval=2.0)
    print(f"actl ownership: {'OK' if ok_actl else 'FAILED'}")
    if not (ok_game and ok_actl):
        raise SystemExit("ownership check failed; see the printed reason "
                         "and check o2debug.log in arcoserver/")

    print("polling for a device hello for 60s (start the Testshroom now)...")
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        o2lite.poll()
        for address, typespec, args in received:
            print(f"<- {address} {typespec} {args}")
        received.clear()
        time.sleep(0.05)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the probe, then the Testshroom, in two more shells**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
.venv/bin/python -m harness.replay_fallback_probe
```

Expected: `clock synced at ...`, `game ownership: OK`, `actl ownership:
OK`. If either FAILs, read the printed reason (it is the same text
`O2LiteTransport.start()` would show -- see `devicelink/o2_transport.py:445-475`)
and check `arcoserver/o2debug.log`.

In a third shell, once ownership shows OK:

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-feasibility
.venv/bin/python -m harness.o2_shroom --dev ie1 --instrument tuneshroom_rev1 --no-join
```

Expected in the probe's shell: `<- /game/hello ['ie1', '', '', 'tuneshroom_rev1']`.

- [ ] **Step 4: Record the fallback's answer**

Write to `.superpowers/bench-replay-feasibility/day2-notes.md`: whether
ownership succeeded, whether the hello arrived, and whether clock sync
behaved normally (compare `o2lite.time_get()`'s value here against a
normal boot's). This directly answers design spec section 8's fallback
question and feeds Task 7's "chosen shape."

- [ ] **Step 5: Stop Arco**

Ctrl-C the probe, then Ctrl-C the Testshroom, then Ctrl-C Arco's own
shell. `git status --short` should show only
`harness/replay_fallback_probe.py` (add and commit it on the spike
branch, same as Task 3 step 5, for the same "never merged, kept for
reference" reasoning).

```bash
git add harness/replay_fallback_probe.py
git commit -m "spike: fallback ownership probe against a hand-started Arco (never merged)"
```

---

### Task 7: Findings document

Always done, regardless of which path (5 or 6) day 2 took. **This is the
actual deliverable** -- Tasks 1-6 exist only to produce what goes here.

**Files:**
- Modify (on a SEPARATE, ordinary branch off `origin/main` -- NOT the
  spike branch): `docs/superpowers/specs/2026-09-16-device-contract-kit-design.md`
  (section 8).

**Interfaces:**
- Consumes: every note written under `.superpowers/bench-replay-feasibility/`
  across Tasks 1, 4, 5/6.
- Produces: the findings paragraph itself -- nothing downstream in this
  plan reads it, but design spec section 10's Phase 5 and Gate 1 do.

**Why a separate branch, not the spike branch:** the STOP RULES say spike
code is never merged, but the findings must reach `main` to inform Gate 1
(design spec section 10: "so a working replay can run against the
Tuneshroom first article at Gate 1"). This mirrors exactly how
`docs/superpowers/plans/2026-09-08-o2ws-browser-link-control.md`'s Task 1
recorded its P7/P8 probe results: "Modify:
`docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md` (section
7, record results)" on the FEATURE branch that DID get merged, not on a
throwaway probe branch. **(recommended)** keep the spike branch itself
around (pushed, unmerged) rather than deleting it immediately -- if the
go/no-go is ever questioned, the exact throwaway code is one `git log`
away instead of gone; "never merge" only forbids landing it on `main`, it
does not require deleting it.

- [ ] **Step 1: Create the findings branch from a clean `main`**

```bash
cd /Users/chris/projects/mm-terrarium
git fetch origin
git worktree add .claude/worktrees/bench-replay-findings \
  -b claude/bench-replay-findings origin/main
cd .claude/worktrees/bench-replay-findings
```

- [ ] **Step 2: Compose the dated result paragraph**

Following `docs/superpowers/specs/2026-09-08-o2ws-browser-link-design.md`
section 7's exact convention (`**P7 result (2026-09-08).**`, `**P8 result
(2026-09-08).**`, each with the literal printed lines), append to THIS
spec's section 8, right after its existing bullets, using today's actual
date:

```markdown
**Bench replay result (<YYYY-MM-DD>).** <GO or NO-GO>. <One sentence
naming the chosen shape: either "a ReplayDriver spliced into
harness/terrarium_boot.py's tick loop in place of DeviceLinkAgent, with
Terrarium/ArcoProcess/O2LiteTransport unmodified" (question (a), a GO) or
"the fallback: Arco started by hand plus a scripted o2litepy client
claiming `game`+`actl` directly" (question (b))>. Measured against
`contract.json`'s `lifecycle.bench_tolerance_ms` (frame 50ms, heartbeat
1000ms): <the actual evaluate_expectations() results from Task 4, and
Task 5's if run, quoted verbatim -- PASS/FAIL per scenario, and any
stamp_t timing numbers observed>. Tried on: the Python Testshroom
(always); <phone hardware profile: yes/no, result>; <a board: yes/no,
result>.

A production version needs: (1) a real trigger for when to fire each
control_sends step -- this spike's ReplayDriver fires purely on elapsed
time since hello, never gated on the device's own join or state, which is
fine for a wire-timing probe but not for a scenario that depends on
sequencing; (2) a decision on the stamp_t tolerance bucket for gesture
verbs (this spike defaulted untested -- see harness/replay_driver.py's
evaluate_expectations docstring); (3) <anything else Task 4/5/6 actually
surfaced -- a drifted CLI flag, an unexpected error, a timing figure
outside tolerance>.

<On a NO-GO only:> Per design spec section 8: Gate 1 relies on in-process
replay plus the manual checks in
docs/superpowers/specs/2026-09-11-student-hardware-track-esp32-design.md
section 3.1.
```

Fill in every `<...>` from the actual Task 4/5/6 notes -- none of this is
committed with a bracket still in it.

- [ ] **Step 3: Commit and open a normal PR**

```bash
cd /Users/chris/projects/mm-terrarium/.claude/worktrees/bench-replay-findings
git add docs/superpowers/specs/2026-09-16-device-contract-kit-design.md
git commit -m "docs(spec): record the bench replay feasibility result (section 8)"
git push -u origin claude/bench-replay-findings
gh pr create --title "docs(spec): bench replay feasibility result" --body "$(cat <<'EOF'
## Summary
- Records the go/no-go for design spec section 8's bench replay question, per the two-day spike in docs/superpowers/plans/2026-09-16-bench-replay-feasibility.md.

## Test plan
- [ ] Docs-only change; no tests to run.
EOF
)"
```

- [ ] **Step 4: Leave the spike branch and worktree in place**

```bash
cd /Users/chris/projects/mm-terrarium
git push -u origin claude/bench-replay-feasibility
```

Do not open a PR for it. Note its branch name in the findings PR's
description so a future reader can find the throwaway code without it
ever being a merge candidate.

---

## Self-review against the spec

- Section 4.2/4.3 (Export Format v1): Task 1 verifies the real export
  against every field quoted in the task brief (`bench_tolerance_ms`,
  `verbs`, `instruments`); Task 3's `ReplayDriver`/`evaluate_expectations`
  implement `control_sends` (`$DEV`, `at`->O2 time, `to_o2_arg`'s existing
  blob/flavor handling) and `expect_out`/`expect_quiet` exactly as
  specified, and explicitly skip `gesture`, `expect_frame`, `expect_play`
  per section 8's own framing.
- Section 8's two questions: (a) is Tasks 2-4's main path; (b) is Task 6,
  gated by the stop rule.
- Section 10's schedule: Task 1 verifies the Phase 3 prerequisite; the
  plan's own timing matches "Week of Oct 5... so a working replay can run
  against the Tuneshroom first article at Gate 1 (Fri Oct 9)."
- Section 12's risks: "Bench replay isn't feasible without a hub-only
  mode" is exactly Task 6's trigger condition; its mitigation ("a two-day
  budget; the fallback is in-process replay plus the section 3.1 checks")
  is Task 7's no-go template, quoting ESP32 spec section 3.1 by path.
- No placeholder steps: every command above names its actual host, path
  and expected output; the only bracketed `<...>` text is in Task 7's
  findings template, which is inherently filled from that day's own
  measurements, not a deferred design decision.
- Type/name consistency: `ReplayDriver`, `load_scenario`,
  `evaluate_expectations` are named and shaped identically everywhere
  they appear (Tasks 3, 4, 7); the `--replay-scenario` flag name is
  identical in Task 2's decision, Task 3's argparse addition, and every
  command in Tasks 3-6.
