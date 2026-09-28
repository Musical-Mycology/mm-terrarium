# Tier 3 Deep-Dive Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `docs/MM_TERRARIUM.md` from a 6,206-line dated build log
into a current-state reference of at most 2,000 lines, fix the inbound
pointers, and delete landed implementation plans.

**Architecture:**
- The new doc is built in a staging file, `docs/MM_TERRARIUM.new.md`. Task 1
  writes its skeleton: headings, placeholders, and the six diagram blocks
  already placed. Tasks 2 to 8 each fill one placeholder from assigned
  line ranges of the old doc.
- Task 9 swaps the staging file over `docs/MM_TERRARIUM.md` and does the
  editing and verification pass. Tasks 10 and 11 fix pointers and delete
  plans.
- The old doc is frozen as `old.md` in the plan workspace. It is the source
  for every writing task.

**Tech Stack:** Markdown, Python 3 (via `.venv/bin/python`) for the
checker script, pytest (`tests/test_diagrams.py`).

**Spec:** `docs/superpowers/specs/2026-09-28-tier3-deepdive-rewrite-design.md`

## Global Constraints

**Setup**
- Worktree: `/Users/chris/projects/mm-terrarium/.claude/worktrees/interesting-bhaskara-62910e`
  - Branch: `claude/tier3-deepdive-rewrite`.
  - Commands run from the worktree root.
  - Python is `.venv/bin/python`, never bare `python3`.
- Pre-rewrite source of truth is commit `9dd35c3` (main, including PR #154).
  - Task 1 freezes it as `WS/old.md`, where
    `WS=.superpowers/sdd/2026-09-28-tier3-deepdive-rewrite` (gitignored).
  - Old line numbers below refer to `WS/old.md`.

**What must not change**
- The final doc path stays `docs/MM_TERRARIUM.md`.
- These headings must appear verbatim, as `##` lines:
  - `## What it is, in one picture`
  - `## Landed subsystems`
  - `## Boundary rules (the load-bearing invariants)`
  - `## Host platform (gotcha)`
  - `## Relationships to other repos`
  - `## Not yet built / deferred`
  - `## Design docs (in-repo, authoritative)`
- Boundary rules keep their existing numbering.
- The mm-tuneshroom relationships bullet keeps its opening words.
- Under "Not yet built / deferred", the bullet beginning "A device's
  clock-sync to Arco after Control has connected is unreliable" keeps
  that opening wording.
- The six diagram regions are copied byte-for-byte from `WS/old.md`: the
  marker line, the fenced `ascii` block, and the closing marker, never
  edited by hand. Names and homes:

  | Diagram | New home |
  |---|---|
  | `light-path` | What it is |
  | `player-flow` | What it is |
  | `lifecycle` | control/ |
  | `terrarium-state` | control/ (terrarium) |
  | `cue-path` | devicelink/ |
  | `boot-teardown` | harness/ |

**Decisions that must be moved, not dropped**
- The `terrarium.py` "deliberate deviations from the spec's prose" go to
  Task 4.
- Roger Dannenberg's 2026-08-16 ruling on o2lite service refusal goes to
  Task 5.
- The `cue_horizon` measurement method and figures go to Task 5.

**Writing rules**
- Write current state only. Remove:
  - status phrased "as of <date>"
  - test-count baselines ("N passed")
  - strike-throughs (`~~`)
  - "superseded" notes
  - plan-deviation notes
  - build narratives

  Keep the *reason* behind current behavior when it prevents a mistake
  (gotchas, invariants, "why not X").
- Every repo path you write must exist at HEAD. Check with
  `ls <path>` or `git ls-files <path>`.
  - Do not mention deleted modules (`harness/room_simulator.py`,
    `control/boot.py`, `control/room_bridge.py`, `harness/led_smoke.py`,
    `control/trigger_view.py`, `console/static/triggers.js`,
    `devicelink/server.py`, `bits/test_bit.py`) unless a current gotcha
    needs them.
- Links stay absolute GitHub URLs
  (`https://github.com/Musical-Mycology/mm-terrarium/blob/main/...`) or
  in-repo paths. The repo is public.
- Never link a file under `docs/superpowers/plans/`. Link the same-named
  spec under `docs/superpowers/specs/` instead.
- No em dashes in anything you write. Existing em dashes inside copied
  diagram regions or kept verbatim headings stay as they are.
- Style: short paragraphs and bullets, code identifiers in backticks, and
  file:line pointers only where they help find something.
- Line budgets are hard caps for each section's prose, not counting the
  diagram regions (about 330 lines in total). The whole doc, diagrams
  included, must be at most 2,000 lines.

**Process rules**
- Each writing task edits only its own placeholder region in
  `docs/MM_TERRARIUM.new.md`.
- Never use `git stash`. Do not dispatch subagents.

---

### Task 1: Freeze the source, build the skeleton, write the checker

**Files:**
- Create: `docs/MM_TERRARIUM.new.md`
- Create (workspace, not committed):
  - `WS/old.md`
  - `WS/mapping.md`
  - `WS/check_doc.py`

**Interfaces:**
- Produces:
  - The staging doc, with placeholders written exactly as
    `<!-- FILL:T<n> <label> -->`, one per writing task (T2 to T8).
  - `WS/check_doc.py`, run as
    `.venv/bin/python WS/check_doc.py <doc>`. It exits 0 or 1 and prints
    findings.
  - `WS/mapping.md`, the old-to-new mapping table.

- [ ] **Step 1: Freeze the source**

```bash
WS=.superpowers/sdd/2026-09-28-tier3-deepdive-rewrite
mkdir -p "$WS"
git show 9dd35c3:docs/MM_TERRARIUM.md > "$WS/old.md"
wc -l "$WS/old.md"   # expect 6206
```

- [ ] **Step 2: Write the checker script** at `WS/check_doc.py`:

```python
"""Checks a rewritten deep-dive against the Tier 3 spec. Exit 1 on any finding."""
import pathlib, re, subprocess, sys

doc_path = pathlib.Path(sys.argv[1])
doc = doc_path.read_text()
lines = doc.splitlines()
findings = []

# 1. Length.
if len(lines) > 2000:
    findings.append(f"length {len(lines)} > 2000")

# 2. Required headings, verbatim.
REQUIRED = [
    "## What it is, in one picture",
    "## Landed subsystems",
    "## Boundary rules (the load-bearing invariants)",
    "## Host platform (gotcha)",
    "## Relationships to other repos",
    "## Not yet built / deferred",
    "## Design docs (in-repo, authoritative)",
]
for h in REQUIRED:
    if h not in lines:
        findings.append(f"missing heading: {h}")

# 3. Required phrase for the clock-sync bullet cited by harness/o2_shroom.py.
if "A device's clock-sync to Arco after Control has connected is unreliable" not in doc:
    findings.append("missing clock-sync bullet opening wording")

# 4. Diagram regions present and byte-identical to the frozen source.
# Run from the worktree root.
old = pathlib.Path(".superpowers/sdd/2026-09-28-tier3-deepdive-rewrite/old.md").read_text()
for name in ["light-path", "player-flow", "lifecycle", "cue-path",
             "boot-teardown", "terrarium-state"]:
    pat = re.compile(r"<!-- diagram:%s GENERATED.*?<!-- /diagram:%s -->" % (name, name), re.S)
    o, n = pat.search(old), pat.search(doc)
    if not n:
        findings.append(f"missing diagram region: {name}")
    elif o and o.group(0) != n.group(0):
        findings.append(f"diagram region altered: {name}")

# 5. History markers.
for i, line in enumerate(lines, 1):
    if "<!-- diagram:" in line:
        continue
    for pat, what in [(r"\bas of 20\d\d", "dated status 'as of'"),
                      (r"\b\d+ passed\b", "test-count baseline"),
                      (r"~~", "strike-through"),
                      (r"[Ss]uperseded", "'superseded' note"),
                      (r"docs/superpowers/plans/", "link to a plan")]:
        if re.search(pat, line):
            findings.append(f"line {i}: {what}: {line.strip()[:90]}")

# 6. Repo paths that do not exist (backticked or linked, repo-relative).
TOP = ("control/", "devicelink/", "harness/", "console/", "uplink/", "capture/",
       "contract_kit/", "bits/", "www/", "arcoserver/", "tools/", "tests/",
       "docs/", "rooms/", "instruments/", "profiles/")
cands = set(re.findall(r"`([A-Za-z0-9_./-]+)`", doc))
cands |= set(re.findall(r"blob/main/([A-Za-z0-9_./-]+)", doc))
for c in sorted(cands):
    p = c.split(":")[0].rstrip("/.")
    if p.startswith(TOP) or p in ("terrarium.toml", "terrarium.sh", "smoke-test.sh",
                                  "requirements.txt", "requirements-dev.txt", "pytest.ini"):
        if "*" in p or "<" in p:
            continue
        if not pathlib.Path(p).exists():
            findings.append(f"path does not exist: {c}")

# 7. Em dashes outside diagram regions and kept headings.
stripped = re.sub(r"<!-- diagram:.*?<!-- /diagram:[a-z-]+ -->", "", doc, flags=re.S)
for i, line in enumerate(stripped.splitlines(), 1):
    if "\u2014" in line and not line.startswith("#"):
        findings.append(f"em dash (post-strip line {i}): {line.strip()[:80]}")

for f in findings:
    print(f)
print(f"{len(lines)} lines, {len(findings)} findings")
sys.exit(1 if findings else 0)
```

- [ ] **Step 3: Sanity-run the checker on the old doc.** It must report
  many findings, which shows the checks fire:

```bash
.venv/bin/python "$WS/check_doc.py" "$WS/old.md" | tail -3
```

Expected: a length finding, many "as of" and path findings, and exit 1.

- [ ] **Step 4: Write `docs/MM_TERRARIUM.new.md`** as the skeleton below.
  The line `<<DIAGRAM name>>` is not literal text: replace each one with
  the exact region copied from `WS/old.md`, from its
  `<!-- diagram:name GENERATED ...-->` line through its
  `<!-- /diagram:name -->` line inclusive. Use a script, not hand-copying:

```bash
.venv/bin/python - <<'EOF'
import re, pathlib
WS = pathlib.Path(".superpowers/sdd/2026-09-28-tier3-deepdive-rewrite")
old = (WS / "old.md").read_text()
def region(name):
    return re.search(r"<!-- diagram:%s GENERATED.*?<!-- /diagram:%s -->" % (name, name), old, re.S).group(0)
skeleton = """# mm-terrarium: the per-room venue server (Arco + Control+GameServer)

<!-- FILL:T2 header, status, history pointer -->

## What it is, in one picture

<<DIAGRAM light-path>>

<<DIAGRAM player-flow>>

<!-- FILL:T2 what-it-is prose (around and between the two diagrams) -->

## Running it

<!-- FILL:T2 running it -->

## Landed subsystems

<!-- FILL:T3 control part 1 (engine, lifecycle, Bits API, lobby, functions, builtins, packaging) -->

<<DIAGRAM lifecycle>>

<!-- FILL:T4 control part 2 (terrarium, rooms, instruments, catalog, light sessions, audio) -->

<<DIAGRAM terrarium-state>>

<!-- FILL:T5 devicelink and contract_kit -->

<<DIAGRAM cue-path>>

<!-- FILL:T6 harness -->

<<DIAGRAM boot-teardown>>

<!-- FILL:T7 console, uplink, capture, bits, www and arcoserver -->

<!-- FILL:T8 tail sections: Boundary rules, Host platform, Relationships, Not yet built, Design docs -->
"""
for name in ["light-path", "player-flow", "lifecycle", "terrarium-state", "cue-path", "boot-teardown"]:
    skeleton = skeleton.replace(f"<<DIAGRAM {name}>>", region(name))
pathlib.Path("docs/MM_TERRARIUM.new.md").write_text(skeleton)
EOF
grep -c 'diagram:' docs/MM_TERRARIUM.new.md   # expect 12
```

  Each writing task moves the diagram inside its own section wherever it
  reads best, keeping it byte-identical. T8 writes all five tail `##`
  headings itself.

- [ ] **Step 5: Write `WS/mapping.md`.** It is a table with one row per
  old `##` and `###` heading, taken from
  `grep -n '^## \|^### ' WS/old.md`. Columns: old line range | old
  heading (short) | assigned task | class (`current` / `mostly-history` /
  `deleted-code`) | notes.
  - Assign tasks using the ranges in Tasks 2 to 8 below.
  - Classify each row by reading its opening lines. Most rows can be
    classified from the heading and the first 20 lines.
  - Add three rows for the must-move decisions, with their exact old line
    ranges (grep `deliberate deviations`, `2026-08-16`, `cue_horizon`).

- [ ] **Step 6: Record the baseline test count**

```bash
.venv/bin/python -m pytest tests -q -p no:cacheprovider | tail -1 > .superpowers/sdd/2026-09-28-tier3-deepdive-rewrite/baseline.txt
cat .superpowers/sdd/2026-09-28-tier3-deepdive-rewrite/baseline.txt
```

- [ ] **Step 7: Commit the skeleton**

```bash
git add docs/MM_TERRARIUM.new.md
git commit -m "docs(terrarium): skeleton for the current-state deep-dive rewrite"
```

---

### Task 2: Header, "What it is, in one picture", Running it

**Files:** Modify `docs/MM_TERRARIUM.new.md`: only the three `FILL:T2`
placeholders.

**Sources in `WS/old.md`:**
- 1-96: preamble
- 97-253: What it is
- 254-281: Landed subsystems intro (venv, suite command)
- 2086-2143: operator/harness handoff and Arco pty
- 4377-4568: `./terrarium.sh`, `run_stack --no-bit`, Join card
- `terrarium.sh`, `smoke-test.sh`, `harness/run_stack.py --help`, and
  `README.md`: verify all commands and flags against these.

**Budget:** 180 lines of prose.

**Interfaces:** Consumes the Task 1 skeleton. Produces the header,
including this line exactly, with the SHA filled in:
`Full pre-rewrite history: \`git show 9dd35c3:docs/MM_TERRARIUM.md\`.`

- [ ] **Step 1: Read the sources.** Record in your report which facts are
  current. Verify every command and flag by running
  `.venv/bin/python -m harness.run_stack --help` and reading
  `terrarium.sh` and `smoke-test.sh`.

- [ ] **Step 2: Write the header**, replacing `FILL:T2 header...`.
  - One paragraph on what the Terrarium is: one per room, two processes,
    Arco as the only full-O2 process, and Control as an o2lite client
    offering `game` and `actl`.
  - A "Status" list: what runs today (the o2lite device path, Room
    audio, MetronomeBit and others), and what is missing (point at
    "Not yet built / deferred").
  - The history pointer line.

- [ ] **Step 3: Write the what-it-is prose**, replacing
  `FILL:T2 what-it-is...`.
  - Where light is rendered, and the pixel sinks.
  - The Bit, role and Registration Node concepts.
  - The player flow in outline.
  - The diagram captions.
  - Arrange it around the two diagram regions, which you may move but
    not alter.

- [ ] **Step 4: Write "Running it"**, replacing `FILL:T2 running it`,
  covering:
  - the `.venv` symlink for fresh worktrees and why
  - the bare-`python3` trap
  - the Python and JS suite commands
  - `./terrarium.sh`, `./smoke-test.sh`, and `harness.run_stack`'s main
    flags (`--room`, `--bit`, `--no-bit`, `--profile`, `--serve`,
    `--ci`, `--seconds`, `--devices`, `--list-bits`)
  - profiles
  - ports (8080 www, 8788 console, as the code defines them)
  - `runs/<timestamp>/` logs and markers
  - arco checkout resolution (ARCO_ROOT, PYTHONPATH, sibling checkout)
  - the Arco pty gotcha

- [ ] **Step 5: Check** that no other region changed and the budget is
  met:

```bash
git diff --stat
.venv/bin/python .superpowers/sdd/2026-09-28-tier3-deepdive-rewrite/check_doc.py docs/MM_TERRARIUM.new.md | grep -v 'missing heading' | head -30
```

  Missing-heading findings for tail sections are expected until T8. Fix
  every other finding inside your region. Findings in other regions are
  not yours.

- [ ] **Step 6: Commit**
  `git commit -am "docs(terrarium): rewrite header, overview and Running it"`

---

### Task 3: Landed subsystems, `control/` part 1

**Files:** Modify only the `FILL:T3` placeholder. You may move the
`lifecycle` diagram region inside your section, byte-identical.

**Sources in `WS/old.md`:**
- 282-401: control/ lifecycle engine
- 1338-1393: device_pool and reap_stale
- 1771-1849: triggers (renamed Functions; the current names are in 3101)
- 2023-2085: wire_json
- 2363-2461: Bit packaging, manifests, start conditions, profiles
- 2717-2769: SolidCue, SURFACE, mute
- 3101-3172: functions, triggers, generator_runner
- 3173-3242: api_version, bundle_bit
- 3243-3364: builtins, fire_function ladder
- 3456-3548: gesture_eval, design_bench
- 4569-4662: lobby, join handshake, admin start
- 4663-4693: `roles_changed`
- Code to verify against: `control/engine.py`, `control/bit.py`,
  `control/roles.py`, `control/registration.py`, `control/role_config.py`,
  `control/bit_config.py`, `control/bit_registry.py`, `control/lobby.py`,
  `control/start_condition.py`, `control/functions.py`,
  `control/builtins.py`, `control/wire_json.py`, `control/api_version.py`,
  `control/device_pool.py`, `control/gesture_eval.py`,
  `control/design_bench.py`, `control/run_profile.py`

**Budget:** 250 lines of prose.

- [ ] **Step 1: Read the sources and the listed code.** In your report,
  list each current fact, and each old claim you dropped as history or
  as no longer true, with the reason.

- [ ] **Step 2: Write** a `### \`control/\`: the lifecycle engine and Bit
  runtime` subsection. Use `####` sub-headings: State machine
  (`lifecycle` diagram); Data model; Bit interface; Bit packages and
  manifests; Start conditions and profiles; Lobby and join handshake;
  Functions and builtins; Cues (SolidCue, SURFACE, mute); Wire JSON;
  Device pool and stale reaping; API version; Design bench and gesture
  eval. Each covers purpose, key names, contracts and gotchas.

- [ ] **Step 3: Check and commit.** Run `check_doc.py` as in Task 2
  Step 5 and fix findings in your region, then:
  `git commit -am "docs(terrarium): rewrite control/ engine and Bit runtime"`

---

### Task 4: Landed subsystems, `control/` part 2

**Files:** Modify only the `FILL:T4` placeholder. You may move the
`terrarium-state` diagram region inside your section, byte-identical.

**Sources in `WS/old.md`:**
- 628-648: audio and arco_synth
- 836-940: rooms and room_binding; RoomBridge and boot are deleted
- 1687-1770: Room panel
- 1850-1895: N-fixture
- 1896-2022: RoomBlock, DEMO
- 2770-2984: terrarium.py and terrarium_config, **including the
  "deliberate deviations" passage, which must be kept**
- 2985-3100: instrument
- 3365-3455: catalog
- 3722-3805: DEFAULTSHROOM, hello's 4th argument
- 3893-3972: per-fixture instruments, Stop, ABORT
- 3973-4099: per-fixture light sessions and FixtureSinks
- 4100-4228: Rooms catalog, TEST/DEMO
- 5430-5490: VENUE room
- Code to verify against: `control/terrarium.py`,
  `control/terrarium_config.py`, `control/rooms.py`,
  `control/room_binding.py`, `control/room_profile.py`,
  `control/room_view.py`, `control/instrument.py`, `control/catalog.py`,
  `control/audio.py`, `control/fixture_sink.py`, `control/breath.py`,
  `harness/arco_synth.py`, `rooms/`, `instruments/`, `terrarium.toml`

**Budget:** 250 lines of prose.

- [ ] **Step 1: Read the sources and the listed code.** Report the facts
  kept and dropped, as in Task 3.

- [ ] **Step 2: Write** a `### \`control/\`: Terrarium, Rooms,
  instruments and audio` subsection. Use `####` sub-headings: Terrarium
  lifecycle (`terrarium-state` diagram, and the spec deviations kept as
  current decisions with their reasons); terrarium.toml; Rooms and
  fixtures (TEST, DEMO, VENUE); Room binding (save/load exists: say so);
  Instruments and the catalog; Per-fixture light sessions and sinks;
  Audio (AudioBridge, ArcoSynthPool, breath, `AudioBridge.shutdown()`
  being terminal after PR #154).

- [ ] **Step 3: Check and commit.**
  `git commit -am "docs(terrarium): rewrite control/ Terrarium, Rooms, instruments and audio"`

---

### Task 5: Landed subsystems, `devicelink/` and `contract_kit/`

**Files:** Modify only the `FILL:T5` placeholder. You may move the
`cue-path` diagram region inside your section, byte-identical.

**Sources in `WS/old.md`:**
- 700-775: devicelink transport
- 1009-1337: o2_transport, timed_queue, o2_shroom, timed cues
- 4229-4285: the o2lite cutover
- 4286-4376: browser guests over o2ws, Control side
- 4949-5044: device contract, contract_kit, export_contract
- 5134-5281: artnet_sink
- 5491-5520: muted surface ignores SolidCue
- **From "Not yet built / deferred" (5725-6125), move here:**
  - the `cue_horizon` measurement method and figures
  - Roger Dannenberg's 2026-08-16 ruling on o2lite service refusal

  Grep `cue_horizon` and `2026-08-16` to find them. Keep both as current
  decisions and facts, not dated narrative. Task 8 leaves only the
  still-open parts in that section.
- Code to verify against: `devicelink/agent.py`,
  `devicelink/o2_transport.py`, `devicelink/protocol.py`,
  `devicelink/lobby_runtime.py`, `devicelink/artnet_sink.py`,
  `devicelink/contract.py`, `control/timed_queue.py`, `contract_kit/`,
  `tools/export_contract.py`, `docs/device-contract-guide.md`

**Budget:** 220 lines of prose.

- [ ] **Step 1: Read the sources and the listed code.** Report as in
  Task 3.

- [ ] **Step 2: Write** a `### \`devicelink/\`: the device-facing side
  over o2lite` subsection. Use `####` sub-headings: Transport (the
  O2LiteTransport, services claimed, `DeviceLinkAgent.transport`);
  Message vocabulary; Timed cues and cue_horizon (`cue-path` diagram,
  measurement method); Service refusal (Roger's ruling); Browser guests
  over o2ws; Fixture sinks and Art-Net; The device contract and
  `contract_kit/`.

- [ ] **Step 3: Check and commit.**
  `git commit -am "docs(terrarium): rewrite devicelink/ and contract_kit/"`

---

### Task 6: Landed subsystems, `harness/`

**Files:** Modify only the `FILL:T6` placeholder. You may move the
`boot-teardown` diagram region inside your section, byte-identical.

**Sources in `WS/old.md`:**
- 544-627: retired Slice 1 LED harness. Keep only what is still true of
  `harness/device_bridge.py` and `harness/websim_leds.py`.
- 649-699: venue-array and device tooling
- 941-1008: simulator_process and terrarium_boot; `room_simulator` is
  deleted
- 1394-1686: teardown, process, signals, markers, and the stack runner
- 2144-2207: WebSim two-way input
- 3549-3648: Bit-cycle room recycle, persistent Testshrooms
- 5359-5429: tick_pacer
- Code to verify against: `harness/terrarium_boot.py`,
  `harness/run_stack.py`, `harness/o2_shroom.py`, `harness/signals.py`
  (`parent_is_gone` now lives here), `harness/markers.py`,
  `harness/tick_pacer.py`, `harness/arco_paths.py`,
  `harness/www_server.py`, `harness/sim_audio.py`,
  `harness/artnet_listen.py`, `harness/render_bench.py`,
  `harness/array_smoke.py`, `control/teardown.py`, `control/process.py`,
  `control/simulator_process.py`, `control/arco_process.py`

**Budget:** 190 lines of prose.

- [ ] **Step 1: Read the sources and the listed code.** Report as in
  Task 3.

- [ ] **Step 2: Write** a `### \`harness/\`: boot, the stack runner and
  tooling` subsection. Use `####` sub-headings:
  - terrarium_boot, covering the shared tick loop (`_run_tick_loop`,
    `_pump`) and the parent-gone check
  - Boot and teardown order (`boot-teardown` diagram)
  - run_stack
  - The Testshroom (`o2_shroom`) and WebSim input
  - Tick pacing
  - Benches and venue tools, as a one-line-each list: render_bench,
    array_smoke, artnet_listen, sync_bench, trace_stats

- [ ] **Step 3: Check and commit.**
  `git commit -am "docs(terrarium): rewrite harness/"`

---

### Task 7: Landed subsystems, `console/`, `uplink/`, `capture/`, `bits/`, `www/` and `arcoserver/`

**Files:** Modify only the `FILL:T7` placeholder.

**Sources in `WS/old.md`:**
- 402-432: bits
- 433-497: uplink
- 498-543: console
- 776-835: capture
- 2208-2362: MetronomeBit
- 2462-2716: Console-operator rounds, front-end rewrite, nav redesign
- 3649-3721: toml_edit, design_forms
- 3806-3892: Console load stabilization
- 4694-4948: confirm-tap, bit.js, capture Bit, Live view, ensure_room
- 5045-5133: Rev1Bit, MinigameBit
- 5282-5358: functions.js, surface.js, arm refusal
- 4286-4376: only for the `www/` and `arcoserver/` facts; Task 5 owns
  the o2ws Control side
- Code to verify against: `console/agent.py`, `console/protocol.py`,
  `console/server.py`, `console/static/` (including `dom.js`),
  `uplink/`, `capture/`, `bits/*/bit.toml`, `bits/*/*.py`, `www/README.md`,
  `arcoserver/`

**Budget:** 240 lines of prose.

- [ ] **Step 1: Read the sources and the listed code.** Report as in
  Task 3.

- [ ] **Step 2: Write** one `###` subsection each:
  - `console/`: protocol, admin commands, panels, the rendering
    discipline, `dom.js`, and the refusals
  - `uplink/`
  - `capture/`
  - `bits/`: one short paragraph per Bit, covering test, chase, rev1,
    capture, metronome and minigame
  - `www/` and `arcoserver/`

- [ ] **Step 3: Check and commit.**
  `git commit -am "docs(terrarium): rewrite console/, uplink/, capture/, bits/, www/"`

---

### Task 8: Tail sections

**Files:** Modify only the `FILL:T8` placeholder, which you replace with
five `##` sections using the exact heading lines from Global Constraints.

**Sources in `WS/old.md`:** 5521-6206. Also read `WS/mapping.md` rows
for the must-move decisions: the `cue_horizon` method and Roger's ruling
now live in Task 5's section, so link to them rather than repeat them.

**Budget:** 240 lines of prose.

- [ ] **Step 1: "Boundary rules (the load-bearing invariants)".** Keep
  every rule and its number. Only remove dated asides.

- [ ] **Step 2: "Host platform (gotcha)".** Keep the current gotchas;
  drop stale lines.

- [ ] **Step 3: "Relationships to other repos".** Keep every repo
  bullet, with dates removed. Keep the mm-tuneshroom bullet's opening
  words unchanged
  (`mm-documents/services/MM_TUNESHROOM.md:19,352` cites it). Confirm
  with
  `grep -n "relationships bullet" /Users/chris/projects/mm-documents/services/MM_TUNESHROOM.md`.

- [ ] **Step 4: "Not yet built / deferred".** Keep only open items.
  - Remove the closed ones: timed cues; the stale device entry; the
    ensemble filter; the websocket default; real Bits; Spec B; Spec C;
    the Room light driver; RoomBindingRegistry save/load; "device frame
    timing depends on transport"; `arcoserver/` and `www/` (keep
    `deploy/` if still absent).
  - Before removing each, verify in code that it is really closed, and
    list each removal with its evidence in your report.
  - Keep the clock-sync bullet, with its opening wording exact.
  - For `cue_horizon` and service refusal, keep only what is still open,
    plus a pointer to the devicelink section.

- [ ] **Step 5: "Design docs (in-repo, authoritative)".** Keep the list.
  Replace any `docs/superpowers/plans/...` link with the same-named
  `docs/superpowers/specs/...-design.md` link if that file exists (check
  with `ls`); otherwise drop the link.

- [ ] **Step 6: Check and commit.** After this task `check_doc.py`
  should report no missing headings.
  `git commit -am "docs(terrarium): rewrite tail sections"`

---

### Task 9: Swap in the new doc, editing pass, verification

**Files:**
- Modify: `docs/MM_TERRARIUM.md` (replaced)
- Delete: `docs/MM_TERRARIUM.new.md`

- [ ] **Step 1: Confirm all placeholders are filled.**
  `grep -n 'FILL:' docs/MM_TERRARIUM.new.md` must print nothing.

- [ ] **Step 2: Editing pass on the staging file.**
  - Make terminology consistent: Control, Arco, Room, fixture,
    Testshroom, Bit.
  - Remove facts repeated across sections; keep each in its most
    specific home and cross-link.
  - Add a short "Contents" list after the header, linking the `##` and
    `###` headings.
  - Keep total length at most 2,000 lines.

- [ ] **Step 3: Swap:**

```bash
git mv -f docs/MM_TERRARIUM.new.md docs/MM_TERRARIUM.md
```

- [ ] **Step 4: Verify.** All must pass:

```bash
.venv/bin/python .superpowers/sdd/2026-09-28-tier3-deepdive-rewrite/check_doc.py docs/MM_TERRARIUM.md   # exit 0
.venv/bin/python -m pytest tests/test_diagrams.py -q -p no:cacheprovider
.venv/bin/python tools/render_diagrams.py --check
.venv/bin/python -m pytest tests -q -p no:cacheprovider   # full suite: same passed/skipped counts as WS/baseline.txt
```

- [ ] **Step 5: Commit**
  `git commit -am "docs(terrarium): replace the deep-dive with the current-state rewrite"`

---

### Task 10: Fix inbound pointers in this repo

**Files:**
- Modify: `requirements-dev.txt` (around line 40)
- Modify: `control/boot_config.py` (around line 65)
- Modify: `tests/test_room_binding.py` (around line 143)
- Modify: `docs/team-walkthrough-metronome-bit.md` (around line 144)
- Check, and edit only if the pointer is broken:
  - `harness/run_stack.py:35,390,638`
  - `harness/o2_shroom.py:654`
  - `control/audio.py:8`
  - `harness/render_bench.py:7`
  - `tests/test_devicelink_agent.py:518`
  - `docs/control-gameserver-design.md:34`

- [ ] **Step 1: List every pointer:**
  `git grep -n "MM_TERRARIUM" -- ':!docs/superpowers' ':!docs/MM_TERRARIUM.md'`

- [ ] **Step 2: Fix each one against the new doc.**
  - `requirements-dev.txt`: its "harness/ section" becomes the new
    section name that covers the soundfont (grep `FluidR3` in the new
    doc).
  - `control/boot_config.py` and `tests/test_room_binding.py` cite
    RoomBindingRegistry save/load as unbuilt. Reword to the current
    truth: it exists (see `control/terrarium.py`). Read the surrounding
    code first. Comment-only edits.
  - `docs/team-walkthrough-metronome-bit.md`: the player-flow diagram is
    under "What it is, in one picture".
  - Every other pointer: confirm that the section or bullet it names
    exists in the new doc (grep the named text). Only edit if it does
    not.

- [ ] **Step 3: Verify:** the full suite passes, then
  `git grep -n "Not yet built" -- harness control tests`. Each hit must
  name an item still present in that section.

- [ ] **Step 4: Commit**
  `git commit -am "docs: point inbound references at the rewritten deep-dive"`

---

### Task 11: Delete landed plans

**Files:** Delete files under `docs/superpowers/plans/`.

- [ ] **Step 1: List the plans:** `ls docs/superpowers/plans/`

- [ ] **Step 2: Classify each plan as `landed` or `keep`.**
  - Default: `landed`.
  - `keep`:
    - `2026-09-16-bench-replay-feasibility.md`, whose GO/NO-GO is
      pending
    - this plan, `2026-09-28-tier3-deepdive-rewrite.md`, which the
      controller deletes after the final review
    - any plan whose own text says it is not executed, or whose main
      deliverable is absent from the code
  - For each `keep`, give the evidence. For a sample of 10 `landed`
    plans, name the deliverable you checked exists, for example
    `ls <file>` or `git log --oneline -S '<symbol>' | head -1`.
  - Check open PRs with `gh pr list --state open`. A plan belonging to
    an open PR is `keep`.

- [ ] **Step 3: Delete the landed plans** with `git rm` and the explicit
  list.

- [ ] **Step 4: Confirm nothing links a deleted plan:**
  `git grep -n "docs/superpowers/plans/" -- ':!docs/superpowers'` must
  print nothing, except links to kept plans. Specs may still mention
  plans; that is historical and fine.

- [ ] **Step 5: Commit**
  `git commit -m "docs: delete implementation plans for landed work"`

---

## After the final review (controller)

- Delete this plan with
  `git rm docs/superpowers/plans/2026-09-28-tier3-deepdive-rewrite.md`
  and commit `docs: delete the executed Tier 3 plan`.
- Ask the user before making the two mm-documents edits (spec section
  "Inbound pointer fixes"). They go through that repo's normal flow, as a
  separate change.
