# An ASCII + SVG diagram pipeline for the deep-dive

Generate the system diagrams in [`docs/MM_TERRARIUM.md`](../../MM_TERRARIUM.md)
from committed sources instead of drawing them by hand, so that a diagram that
no longer matches the code is a **test failure** rather than something noticed
six weeks later.

Two renderers, chosen by measurement rather than preference (section 1.2):
**D2** for the four graph diagrams, and a small **pure-Python sequence
renderer** written here for the player flow, because all three off-the-shelf
sequence renderers tested are disqualified for this repo specifically.

Supersedes nothing. Touches no runtime code: everything lands in `docs/`,
`tools/` and `tests/`, following the precedent set by
[`tools/trace_stats.py`](../../../tools/trace_stats.py) (an offline CLI that is
explicitly not part of the runtime).

Baseline at the time of writing: `7a4a845`, **764 passed, 1 skipped**, 14.03s,
fully offline.

## 1. The problem

### 1.1 Hand-drawn diagrams rot, and the densest passages have none

`docs/MM_TERRARIUM.md` carries exactly one diagram, the hand-drawn topology
block under *What it is, in one picture*. Everything else that would benefit
from a picture is prose, including the two passages that have already cost
debugging time:

- **Teardown order.** The current text describes three separately-maintained
  orderings, a `TeardownStack` that replaced them, and two different unwind
  orders depending on whether the run is websocket or o2lite. It took its own
  spec ([`2026-08-14-teardown-order-and-stack-runner-design.md`](2026-08-14-teardown-order-and-stack-runner-design.md))
  to get right and it is rendered today as a paragraph.
- **The cue path.** `gesture -> GameServer.data() -> at = origin + cue_horizon
  -> verb handler -> on_light_cue -> transport -> TimedQueue -> device render`
  is a seven-hop path with a Room-targeted branch. Two bugs hid in it that only
  a live run could find, and neither was visible to 611 passing tests.

The hand-drawn block also has no defence against drift. Nothing connects it to
the code it describes.

### 1.2 Every off-the-shelf ASCII renderer tested fails silently or corrupts

Measured 2026-08-17 against real MM labels. This is the finding that drives the
whole design.

**D2** (`go run github.com/d2lang/d2@latest`, v0.8.1-HEAD). The flowchart and
container renderer is solid: `/game/join`, `cc:74 -> hue` and
`at = origin + cue_horizon` all render verbatim, containers nest cleanly, cycles
are fine. The `shape: sequence_diagram` renderer is **not** usable here. Every
row below is a right-to-left message:

| Source label | Rendered |
| --- | --- |
| `/abc` | `abc` |
| `" /abc"` | `abc` |
| `x/abc` | `x─abc` |
| `//abc` | `abc` |
| `-abc` | `abc` |
| `./abc` | `abc` |

`/`, `-`, `.` and leading spaces are swallowed into the connector line,
including mid-label. Every MM address begins with `/`. There is no workaround.

D2 has a second, smaller defect that the design has to route around: a literal
`\n` inside a node label corrupts the box grid. `Arco server\n(o2 hub)`
rendered as `│Arco server` followed by `(o2 h┌──────────────┐`, eating the
neighbouring box. D2's ASCII renderer is documented upstream as alpha.

**Diagon** (`diagonjs`, npm). Correct one-way arrows and clean output, but a
message whose label contains `//` is **silently dropped along with every
message after it**, and `:` mis-parses as the message separator, so `cc:74`
renders as a stray participant named `74`. Silent truncation is a worse failure
mode for a doc pipeline than corruption. It also drags node and npm into a
Python repo.

**adia** (pip, pure Python). Handles every MM label intact in both directions,
but models every `->` as a UML `Call` that draws an **unconditional return
arrow** (`adia/sequence.py:157`), with no one-way message in the grammar. MM's
O2 traffic is fire-and-forget. A diagram showing a reply to every `/game/hello`
would not be ugly, it would be wrong about the protocol.

**Graph::Easy** (CPAN, pure Perl, runs off macOS system perl with no install)
produced correct output on every input tested, including a 22-node graph in
0.087s. It is not used by this design because D2 renders containers better and
emits SVG from the same source, but it is recorded here as the fallback if D2's
alpha renderer becomes a problem.

The common thread: **these tools do not raise on bad input.** They corrupt or
truncate. Any pipeline built on them needs its own verification layer.

## 2. What we build

### 2.1 Tool split

| Diagram | Renderer | Why |
| --- | --- | --- |
| topology | D2 | containers, measured solid |
| boot + teardown order | D2 | containers, measured solid |
| cue path | D2 | containers, measured solid |
| Bit lifecycle | D2 | state graph, measured solid |
| player flow | `tools/seqrender.py` | all three off-the-shelf options disqualified (1.2) |

Writing `seqrender.py` is the one place this design grows code rather than
consuming a library. It is justified because sequence diagrams are the single
diagram type with no graph-layout problem: participants are columns at fixed
x, messages are rows. The alternative is adding a node toolchain for one
diagram, or shipping a diagram that misrepresents the protocol.

### 2.2 Layout

```
docs/diagrams/
  topology.d2
  boot-teardown.d2
  cue-path.d2
  lifecycle.d2
  player-flow.seq
  manifest.json
  out/
    topology.txt          topology.svg
    boot-teardown.txt     boot-teardown.svg
    cue-path.txt          cue-path.svg
    lifecycle.txt         lifecycle.svg
    player-flow.txt
tools/
  render_diagrams.py
  seqrender.py
tests/
  test_diagrams.py
  test_seqrender.py
```

`docs/diagrams/out/` is **committed**. Reading the repo, or running the test
suite, never requires D2. Only regenerating does.

`player-flow` has no SVG. `seqrender.py` emits ASCII only, and inventing an SVG
backend for it is out of scope.

### 2.3 `manifest.json`

The contract between the renderer and the drift test.

```json
{
  "d2_version": "0.8.1",
  "diagrams": {
    "topology": {
      "source": "topology.d2",
      "renderer": "d2",
      "source_sha256": "...",
      "outputs": {
        "out/topology.txt": "...",
        "out/topology.svg": "..."
      },
      "inject_into": "docs/MM_TERRARIUM.md",
      "marker": "topology"
    }
  }
}
```

`d2_version` is recorded but **deliberately not asserted** by the drift test.
Asserting it would turn every D2 upgrade into a repo-wide test failure with no
defect behind it. It is there so that when a diagram does render differently,
you can tell whether the tool moved under you.

`inject_into` and `marker` are optional. A diagram may exist as a file without
being injected anywhere.

## 3. `tools/render_diagrams.py`

### 3.1 Stages

1. Load and parse `manifest.json`.
2. For each diagram: run the **static validation** rules (3.2).
3. Render to a temporary directory (never straight into `out/`).
4. Run the **post-render round trip** (3.3) on each rendered `.txt`.
5. Only when every diagram has passed: move the temp directory into `out/`,
   inject into the target markdown, and rewrite `manifest.json` with fresh
   hashes.

Stage 5 is all-or-nothing (3.4).

CLI: `python -m tools.render_diagrams` regenerates everything.
`--check` renders to a temp directory and reports what would change without
writing, for use when you want the answer without the diff.

### 3.2 Static validation

Two rules, both derived from measured defects:

- **Reject a literal `\n` inside any D2 label.** Measured to corrupt the box
  grid (1.2). Error names the diagram and the offending label, and suggests
  splitting into separate nodes.
- **Reject `shape: sequence_diagram` in any `.d2` source.** Measured to corrupt
  every `/`-bearing label (1.2). Error points at `seqrender.py` and
  `player-flow.seq` as the supported path.

`.seq` sources have no character restrictions. `seqrender.py` is ours, so its
failure modes are ours to not have. Its only static rule is that every
participant named in a message must be declared in the `participants:` line,
which keeps column order deterministic and diffs quiet.

### 3.3 The post-render round trip

The most important check in the design, and the reason it generalises past the
specific bugs found on 2026-08-17.

**For every label in the source, assert the literal string appears in the
rendered `.txt`.** If it does not, fail, naming the diagram, the label, and the
renderer.

This single check catches every failure documented in 1.2 without encoding
knowledge of any of them: D2 eating `/`, Diagon dropping a `//` message and
everything after it, `cc:74` mis-parsing into a stray participant. It also
catches whatever the next alpha-renderer defect turns out to be.

If a label legitimately wraps across lines and fails the check, the fix is to
shorten the label. That is the right answer for an ASCII diagram anyway, so the
check is left strict rather than being weakened to a whitespace-normalised
match.

### 3.4 Atomic output

Rendering goes to a temp directory. `out/` is only updated once **every**
diagram has passed both validation stages. A run that fails on diagram four of
five leaves `out/`, the markdown, and `manifest.json` exactly as they were.

This matters because the outputs are committed. A partial render that landed
would produce a commit where some diagrams are current and some are not, with
nothing recording which is which.

## 4. `tools/seqrender.py`

### 4.1 Source format

```
title: Player flow
participants: Phone, Arco, Control

Phone -> Arco: /game/hello
Arco -> Control: /game/hello
Control -> Arco: /ie1/role
Arco -> Phone: /ie1/role
Phone -> Arco: /game/join TEST_PLAYER_NODE
```

- `title:` optional, rendered above the diagram.
- `participants:` required, comma-separated. Declares column order explicitly
  rather than inferring it from first mention, so inserting a message near the
  top cannot reorder every column and produce a whole-diagram diff.
- Blank lines and `#` comments ignored.
- One message per line: `<source> -> <target>: <label>`.
- `note: <text>` renders a full-width annotation row.

### 4.2 Invariants

These are the properties `test_seqrender.py` asserts, and they are the whole
reason this file exists:

1. **No synthesized arrows.** An arrow appears if and only if the source
   declares one. There are no automatic returns (this is what disqualified
   adia).
2. **Labels are never truncated.** Column pitch grows to fit the longest label.
   A diagram gets wider; it never loses a character (this is what disqualified
   D2's sequence renderer).
3. **Every declared participant renders**, as a header box and a footer box, at
   the same column in both.
4. **Output is deterministic**: same source, same bytes. No timestamps, no
   dict-ordering dependence.
5. **Pure stdlib.** Adds nothing to `requirements-dev.txt`.

## 5. Injection into `MM_TERRARIUM.md`

### 5.1 Marker format

````
<!-- diagram:topology GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
...rendered block...
```
<!-- /diagram:topology -->
````

The renderer replaces only the bytes between the markers. It never touches the
markers themselves or any prose outside them.

The "do not hand-edit" text is inline and load-bearing rather than decorative.
Hand-editing the ASCII in this file is exactly what happens today, so the
warning has to appear at the point of temptation.

### 5.2 Coexistence with `mm-deepdive-sync`

`MM_TERRARIUM.md` is rewritten at closeout by the `mm-deepdive-sync` skill.
Two facts make this safe:

- The deep-dive for this repo is **in-repo** and goes through the ordinary
  branch/PR flow. `scripts/mm-docs-push.sh` refuses any origin that is not
  `mm-documents`, so the direct-to-main docs path does not apply here and there
  is no push-ordering hazard.
- The sync edits prose; the renderer edits only marker regions. The regions are
  disjoint, so a normal deep-dive update leaves every diagram hash intact.

If a future session does hand-edit inside a marker region, the drift test fails.
That is the intended signal, not a problem to design around.

## 6. The drift test (`tests/test_diagrams.py`)

Pure stdlib. No subprocess, no D2, no node, no network. It therefore runs in the
core offline suite as a first-class test rather than as an `importorskip` that
silently no-ops on every machine without the binary.

For each entry in `manifest.json`:

1. sha256 the source file; compare to `source_sha256`.
2. sha256 each file in `outputs`; compare.
3. For an injected diagram: read the `.txt`, build the expected region from it
   using the same fence wrapping the injector uses, extract the actual region
   between the markers in `inject_into`, and compare the two **byte for byte**.
   Comparing reconstructed text rather than hashing "whatever is between the
   markers" removes the question of whether the fences are inside or outside
   the hashed span.
4. Assert the marker sets match in both directions: every declared marker
   exists in the markdown, and every `<!-- diagram:... -->` marker in the
   markdown is declared in the manifest. An orphaned marker is a failure.

A failure message names the diagram and says to run
`python -m tools.render_diagrams`.

**Known limit.** The manifest is the test's source of truth, so hand-editing
both a rendered file and its recorded hash would pass. This is accepted: the
check exists to catch forgetting to re-render, which is the realistic failure,
not to defend against someone deliberately faking a hash.

## 7. The starter diagram set

Five diagrams, all injected into `MM_TERRARIUM.md`.

| Name | Content |
| --- | --- |
| `topology` | Devices over o2lite vs websocket, Arco as the only full-O2 process, Control as an o2lite guest on pyarco's connection offering `game` and `actl`. Replaces the existing hand-drawn block. |
| `boot-teardown` | `boot()`'s load sequence, and the `TeardownStack` LIFO unwind, showing both the websocket and o2lite orderings side by side. |
| `cue-path` | gesture to rendered frame: `GameServer.data()`, `at = origin + cue_horizon`, verb handler, `on_light_cue`, transport, `TimedQueue`, device render, plus the `ROOM`-targeted branch. |
| `lifecycle` | `IDLE -> LOADING -> LOADED -> SETUP -> RUNNING -> COMPLETING -> UNLOADING -> IDLE`, annotated with the scored-denied/jam-open rule during RUNNING and the always-reachable COMPLETING/UNLOADING guarantee. |
| `player-flow` | hello, join, role, play, complete across Phone, Arco and Control. Rendered by `seqrender.py`. |

Converting `topology` first is deliberate: it is the one diagram that already
exists, so it is the only one where the pipeline can be judged against a
hand-drawn before-and-after.

## 8. Testing

| File | Covers |
| --- | --- |
| `tests/test_seqrender.py` | The five invariants in 4.2; `note:` rows; a label longer than every participant name, to pin invariant 2; plus parse errors: undeclared participant, malformed message line, empty `participants:`. |
| `tests/test_diagrams.py` | The four drift checks in section 6, plus a manifest that references a missing source file. |

Both are stdlib-only and run in the core offline suite. Neither invokes D2.

The validation logic in 3.2 and 3.3 is unit-testable without rendering anything:
the static rules take source text, and the round-trip check takes a label list
plus rendered text. Both get direct tests with synthetic input, including a
regression case built from the exact D2 corruption measured in 1.2 (label
`/abc`, rendered text containing only `abc`, expected to fail).

## 9. Failure modes

| Condition | Behaviour |
| --- | --- |
| D2 not on `PATH` | `render_diagrams.py` exits non-zero with the `brew install d2` line. Nothing written. Test suite unaffected. |
| Static validation fails | Whole run fails, naming diagram and label. Nothing written. |
| Round trip fails | Whole run fails, naming diagram, label and renderer. Nothing written. |
| Declared marker missing from markdown | Error, not a silent skip. |
| Undeclared marker present in markdown | Drift test failure (section 6, check 4). |
| Partial render | Impossible by construction (3.4). |

## 10. Non-goals

- **No changes to [`docs/control-gameserver-design.md`](../../control-gameserver-design.md).**
  Its topology block is terse and hand-tuned, and it is the doc Roger reads.
  Regenerating it for tooling reasons is a bad trade.
- **No CI integration.** This repo has no `.github/workflows`. The drift test
  is a pytest test, which is the enforcement surface that actually exists.
- **No deriving diagrams from the import graph.** Four of the five diagrams
  encode editorial knowledge no import graph contains.
- **No SVG for the sequence diagram.**
- **No new runtime dependencies.** `requirements-dev.txt` is unchanged. D2 is
  documented as an optional external tool, following the luxaeterna and pyarco
  precedent.

## 11. Decisions deferred

- **Graph::Easy as a fallback renderer** is documented in 1.2 but not wired in.
  If D2's alpha ASCII renderer produces a defect the round-trip check cannot
  route around, it is the measured replacement, and `manifest.json` already
  carries a per-diagram `renderer` field to make that swap local.
- **Diagrams in module docstrings** rather than only markdown. The injector is
  marker-based and does not care about file type, so extending it to `.py` is
  additive. Out of scope here because it widens the surface that has to stay in
  sync before the pipeline has proven itself on one file.
- **Reporting the D2 sequence-renderer corruption upstream.** The measurements
  in 1.2 are a complete bug report, but filing it is not part of this work.
