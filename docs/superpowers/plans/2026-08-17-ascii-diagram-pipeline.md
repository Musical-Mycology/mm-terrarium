# ASCII + SVG Diagram Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate the five system diagrams in `docs/MM_TERRARIUM.md` from committed sources, so a diagram that no longer matches the code fails the test suite.

**Architecture:** Sources live in `docs/diagrams/`. `tools/render_diagrams.py` validates them, renders through D2 (four graph diagrams) or `tools/seqrender.py` (the player flow), verifies every source label survived rendering verbatim, injects the ASCII between HTML-comment markers in the markdown, and records sha256 hashes in `docs/diagrams/manifest.json`. `tests/test_diagrams.py` re-checks those hashes using nothing but stdlib, so it runs in the core offline suite without D2 installed.

**Tech Stack:** Python 3 stdlib only (`hashlib`, `json`, `pathlib`, `re`, `subprocess`, `dataclasses`, `argparse`, `tempfile`). D2 v0.8.1+ as an external binary, needed only to regenerate.

**Spec:** [`2026-08-17-ascii-diagram-pipeline-design.md`](../specs/2026-08-17-ascii-diagram-pipeline-design.md)

## Plan amendment, 2026-08-17: topology is not converted

**Task 7 is now a revert, and the task order changes.** Decided by Chris during execution, after Task 7 was implemented, reviewed, fixed and re-reviewed.

The plan chose the topology diagram first as a proof of pipeline, on the reasoning that it was the only diagram with a hand-drawn before-and-after to judge against. The judgment came back and it was negative:

- Round 1 rendered a box that overflowed its container, and shipped an `arco -> control` edge which, under the grammar the diagram's own device edges establish, asserted that Arco attaches to Control. The deep-dive says the reverse (`docs/MM_TERRARIUM.md:13`, `:694`). It passed the automated label round trip while being architecturally wrong.
- Round 1 also deleted the `each Tuneshroom offers "ie<N>", each browser offers "ui<X>"` fact, which lived inside the replaced fence.
- The fix corrected all three, but cutting 33 lines to 24 required removing the `Terrarium box (one per room)` container, which was the single structural advantage the generated version had over the hand-drawn one.
- Task review and scoped re-review both concluded, independently, that the result was **worse** than the 8-line original: same information, three times the height, plurality reduced to a caption, and the bidirectional-edge fix only reaching parity with what the original already drew.

**The pipeline itself is unaffected and stays.** Tasks 1-6 are complete and reviewed. What this changes is only which diagrams it renders. The pipeline's value was always the four diagrams that do not exist in any form (teardown order, cue path, lifecycle, player flow), not the one that already reads well in 8 hand-drawn lines.

**Revised task order:**

| Was | Now |
| --- | --- |
| T7 topology | **T7 revert** the topology conversion; `docs/MM_TERRARIUM.md` returns byte-identical to `3b086fd` |
| T8 drift test | **T8 moves after T9.** It runs against the real committed manifest and needs at least one diagram to exist first |
| T9 boot/teardown | **T9 becomes the first real diagram**, and inherits Task 7's editorial bar |
| T10-T12 | unchanged |

Execution order from here: **T7 (revert) → T9 → T8 → T10 → T11 → T12.**

**The editorial bar, now binding on T9-T12.** A generated diagram ships only if it genuinely serves a reader better than what it replaces, or better than nothing where no diagram exists today. A green `--check` and a passing label round trip are necessary and not sufficient. For T9-T12 there is no incumbent diagram, so the bar is "clearer than the prose alone", which is a much easier bar than T7 faced.

Section 7's starter-set table below still lists `topology`; that row is superseded by this amendment.

## Global Constraints

- **Pure stdlib.** Everything added to `tools/` and `tests/` uses only the Python standard library. `requirements-dev.txt` gains nothing.
- **No test may invoke `d2`, node, or the network.** Tests that need rendered output use a fake renderer injected by monkeypatch, or fixture text written inline.
- **The offline suite must stay green.** Baseline at `7a4a845`: **764 passed, 1 skipped**, 14.03s. Every task ends with the full suite passing.
- **Run tests through the project venv:** `.venv/bin/python -m pytest tests -q`. Never bare `python3`; it collects a phantom import error in `tests/test_terrarium_boot.py`. A fresh worktree has no `.venv`; create it with `ln -s /Users/chris/projects/mm-terrarium/.venv .venv` from the worktree root.
- **D2 v0.8.1 or later** is required only for Tasks 7, 9, 10 and 11. Install: `brew install d2`.
- **Charset: D2's default Unicode box-drawing.** Do not pass `--ascii-mode=standard`. `MM_TERRARIUM.md` is UTF-8 and already carries Unicode box art.
- **`docs/diagrams/out/` is committed.** Reading the repo or running the suite never requires D2.
- **Never modify `docs/control-gameserver-design.md`.** It is the Roger-facing architecture doc and is explicitly out of scope.
- **Never hand-edit anything between `<!-- diagram:... -->` markers.** Regenerate instead.

## File Structure

| File | Responsibility |
| --- | --- |
| `tools/seqrender.py` | Parse `.seq` sources and render them to ASCII. Pure functions, no I/O. |
| `tools/render_diagrams.py` | Validation, D2 subprocess, label round-trip, marker injection, manifest, CLI. |
| `tests/test_seqrender.py` | Parser and renderer invariants. |
| `tests/test_render_diagrams.py` | Validation, round-trip, manifest, injection, pipeline. |
| `tests/test_diagrams.py` | The drift check against the real committed manifest. |
| `docs/diagrams/*.d2`, `*.seq` | Diagram sources. |
| `docs/diagrams/out/*` | Committed rendered outputs. |
| `docs/diagrams/manifest.json` | Generated hash manifest. |
| `docs/MM_TERRARIUM.md` | Gains marker regions. |

### Deviations from the spec

Two, both deliberate, flagged so a reviewer is not surprised:

1. **A third test file.** The spec's section 8 names `tests/test_seqrender.py` and `tests/test_diagrams.py`. This plan adds `tests/test_render_diagrams.py` for the validation, injection and pipeline units. Folding those into `test_diagrams.py` would mix unit tests of the tooling with the drift check against the real committed manifest, which are different things with different failure meanings.
2. **In-memory atomicity instead of a temp-directory move.** Spec section 3.4 describes rendering to a temp directory and moving it into `out/` on success. D2 can only write to files, so a temp directory is still used for its output, but everything is then read into memory and *all* writes (outputs, markdown, manifest) happen at the end from memory. Same all-or-nothing guarantee, and it covers the markdown and manifest writes that a directory move would not.

---

### Task 1: `seqrender.py` source parsing

**Files:**
- Create: `tools/seqrender.py`
- Test: `tests/test_seqrender.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Message(source: str, target: str, label: str)`, `Note(text: str)`, `Sequence(title: str, participants: tuple[str, ...], rows: tuple[Message | Note, ...])`, `SeqParseError(ValueError)`, `parse(text: str) -> Sequence`, `labels(seq: Sequence) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_seqrender.py
import pytest

from tools.seqrender import Message, Note, SeqParseError, labels, parse

SOURCE = """\
title: Player flow
participants: Phone, Arco, Control

# a comment
Phone -> Arco: /game/hello
Arco -> Control: /game/hello
note: registration closes when run() is called
Control -> Arco: /ie1/role
"""


def test_parse_reads_title_and_participants():
    seq = parse(SOURCE)
    assert seq.title == "Player flow"
    assert seq.participants == ("Phone", "Arco", "Control")


def test_parse_reads_rows_in_order():
    seq = parse(SOURCE)
    assert seq.rows == (
        Message("Phone", "Arco", "/game/hello"),
        Message("Arco", "Control", "/game/hello"),
        Note("registration closes when run() is called"),
        Message("Control", "Arco", "/ie1/role"),
    )


def test_parse_title_is_optional():
    seq = parse("participants: A, B\nA -> B: x\n")
    assert seq.title == ""


def test_labels_returns_message_labels_and_note_text():
    assert labels(parse(SOURCE)) == [
        "/game/hello",
        "/game/hello",
        "registration closes when run() is called",
        "/ie1/role",
    ]


def test_label_may_contain_colons_and_slashes():
    seq = parse("participants: A, B\nA -> B: /ie1/leds cc:74\n")
    assert seq.rows[0].label == "/ie1/leds cc:74"


def test_undeclared_participant_is_an_error():
    with pytest.raises(SeqParseError) as exc:
        parse("participants: A, B\nA -> C: x\n")
    assert "line 2" in str(exc.value)
    assert "C" in str(exc.value)


def test_missing_participants_line_is_an_error():
    with pytest.raises(SeqParseError):
        parse("A -> B: x\n")


def test_empty_participants_line_is_an_error():
    with pytest.raises(SeqParseError):
        parse("participants:\nA -> B: x\n")


def test_malformed_message_line_is_an_error():
    with pytest.raises(SeqParseError) as exc:
        parse("participants: A, B\nA to B: x\n")
    assert "line 2" in str(exc.value)


def test_self_message_is_an_error():
    with pytest.raises(SeqParseError) as exc:
        parse("participants: A, B\nA -> A: x\n")
    assert "self-message" in str(exc.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_seqrender.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'tools.seqrender'`

- [ ] **Step 3: Write the parser**

```python
# tools/seqrender.py
"""Render sequence diagrams to ASCII.

Pure stdlib, no I/O. Written rather than taken off the shelf because every
off-the-shelf ASCII sequence renderer measured on 2026-08-17 was disqualified
for this repo: D2's swallows `/` from right-to-left labels, Diagon silently
drops messages containing `//` or `:`, and adia synthesizes a return arrow for
every message, which misrepresents fire-and-forget O2 traffic. See
docs/superpowers/specs/2026-08-17-ascii-diagram-pipeline-design.md section 1.2.
"""

from __future__ import annotations

from dataclasses import dataclass


class SeqParseError(ValueError):
    """A .seq source could not be parsed."""


@dataclass(frozen=True)
class Message:
    source: str
    target: str
    label: str


@dataclass(frozen=True)
class Note:
    text: str


@dataclass(frozen=True)
class Sequence:
    title: str
    participants: tuple[str, ...]
    rows: tuple[Message | Note, ...]


def parse(text: str) -> Sequence:
    title = ""
    participants: tuple[str, ...] = ()
    rows: list[Message | Note] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("title:"):
            title = line[len("title:") :].strip()
            continue

        if line.startswith("participants:"):
            names = [n.strip() for n in line[len("participants:") :].split(",")]
            names = [n for n in names if n]
            if not names:
                raise SeqParseError(f"line {lineno}: participants: declares no participants")
            participants = tuple(names)
            continue

        if line.startswith("note:"):
            rows.append(Note(line[len("note:") :].strip()))
            continue

        if "->" not in line or ":" not in line:
            raise SeqParseError(f"line {lineno}: not a message, expected '<A> -> <B>: <label>': {line!r}")

        endpoints, _, label = line.partition(":")
        source, _, target = endpoints.partition("->")
        source, target, label = source.strip(), target.strip(), label.strip()

        if not participants:
            raise SeqParseError(f"line {lineno}: message before any participants: line")
        for name in (source, target):
            if name not in participants:
                raise SeqParseError(f"line {lineno}: undeclared participant {name!r}")
        if source == target:
            raise SeqParseError(f"line {lineno}: self-message {source!r} is not supported")

        rows.append(Message(source, target, label))

    if not participants:
        raise SeqParseError("no participants: line found")

    return Sequence(title=title, participants=participants, rows=tuple(rows))


def labels(seq: Sequence) -> list[str]:
    """Every string that must survive rendering verbatim."""
    out: list[str] = []
    for row in seq.rows:
        out.append(row.label if isinstance(row, Message) else row.text)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_seqrender.py -q`
Expected: PASS, 10 tests

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 774 passed, 1 skipped

- [ ] **Step 6: Commit**

```bash
git add tools/seqrender.py tests/test_seqrender.py
git commit -m "feat(diagrams): parse .seq sequence-diagram sources"
```

---

### Task 2: `seqrender.py` ASCII rendering

**Files:**
- Modify: `tools/seqrender.py`
- Test: `tests/test_seqrender.py`

**Interfaces:**
- Consumes: `Sequence`, `Message`, `Note`, `parse` from Task 1.
- Produces: `render(seq: Sequence) -> str`.

Layout algorithm, specified so the implementer does not have to invent it:

- `col_w[i] = len(participants[i]) + 4`, the width of `| Name |`.
- Columns are laid out left to right separated by a uniform `gap`.
  `left[0] = 0`; `left[i] = left[i-1] + col_w[i-1] + gap`.
  `center[i] = left[i] + col_w[i] // 2`.
- For a message between columns `i` and `j` (`i != j`), let `lo, hi = sorted((i, j))`.
  The distance `center[hi] - center[lo]` equals `base + gap * (hi - lo)` where
  `base = (col_w[lo] - col_w[lo] // 2) + sum(col_w[lo+1:hi]) + col_w[hi] // 2`.
  The arrow body occupies `distance - 1` columns and must hold `len(label) + 2`.
  So `gap` must satisfy `gap >= ceil((len(label) + 3 - base) / (hi - lo))`.
- `gap = max(3, max over all messages of that bound)`. Uniform, deterministic,
  and it can only grow, which is what makes "never truncate" hold.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_seqrender.py
from tools.seqrender import render


def _render(src: str) -> str:
    return render(parse(src))


def test_render_draws_header_and_footer_boxes():
    out = _render("participants: Phone, Arco\nPhone -> Arco: hi\n")
    lines = out.splitlines()
    assert "| Phone |" in lines[1]
    assert "| Arco |" in lines[1]
    assert "| Phone |" in lines[-2]
    assert "| Arco |" in lines[-2]


def test_render_places_participants_at_the_same_column_top_and_bottom():
    out = _render("participants: Phone, Arco\nPhone -> Arco: hi\n")
    lines = out.splitlines()
    assert lines[0] == lines[-3]
    assert lines[1] == lines[-2]
    assert lines[2] == lines[-1]


def test_render_draws_a_left_to_right_arrow():
    out = _render("participants: A, B\nA -> B: go\n")
    arrow = next(line for line in out.splitlines() if "go" in line)
    assert arrow.rstrip().endswith(">|") or arrow.rstrip().endswith(">")
    assert "-go-" in arrow
    assert "<" not in out


def test_render_draws_a_right_to_left_arrow():
    out = _render("participants: A, B\nB -> A: back\n")
    assert "<" in out
    assert ">" not in out


def test_render_never_synthesizes_a_return_arrow():
    """One declared message produces exactly one arrow. adia's failure mode."""
    out = _render("participants: A, B\nA -> B: only\n")
    assert out.count(">") == 1
    assert out.count("<") == 0


def test_render_never_truncates_a_long_label():
    long = "at = origin + cue_horizon, then TimedQueue releases it"
    out = _render(f"participants: A, B\nA -> B: {long}\n")
    assert long in out


def test_render_preserves_slashes_and_colons():
    """The exact corruption class that disqualified D2's sequence renderer."""
    out = _render("participants: A, B\nB -> A: /ie1/leds cc:74\n")
    assert "/ie1/leds cc:74" in out


def test_render_includes_the_title_when_present():
    out = _render("title: Player flow\nparticipants: A, B\nA -> B: x\n")
    assert out.splitlines()[0] == "Player flow"


def test_render_renders_a_note_row():
    out = _render("participants: A, B\nA -> B: x\nnote: registration closed\n")
    assert "[ registration closed ]" in out


def test_render_is_deterministic():
    src = "participants: A, B, C\nA -> B: x\nC -> A: y\n"
    assert _render(src) == _render(src)


def test_render_widens_for_a_label_spanning_two_columns():
    long = "a label considerably wider than any participant name"
    out = _render(f"participants: A, B, C\nA -> C: {long}\n")
    assert long in out
    widths = {len(line) for line in out.splitlines() if line}
    assert max(widths) >= len(long)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_seqrender.py -q`
Expected: FAIL, `ImportError: cannot import name 'render'`

- [ ] **Step 3: Write the renderer**

```python
# append to tools/seqrender.py
import math


def _layout(seq: Sequence) -> tuple[list[int], list[int], int]:
    """Return (col_w, center, gap)."""
    col_w = [len(name) + 4 for name in seq.participants]
    index = {name: i for i, name in enumerate(seq.participants)}

    gap = 3
    for row in seq.rows:
        if not isinstance(row, Message):
            continue
        lo, hi = sorted((index[row.source], index[row.target]))
        base = (col_w[lo] - col_w[lo] // 2) + sum(col_w[lo + 1 : hi]) + col_w[hi] // 2
        needed = math.ceil((len(row.label) + 3 - base) / (hi - lo))
        gap = max(gap, needed)

    left = [0]
    for i in range(1, len(col_w)):
        left.append(left[i - 1] + col_w[i - 1] + gap)
    center = [left[i] + col_w[i] // 2 for i in range(len(col_w))]
    return col_w, center, gap


def _boxes(seq: Sequence, col_w: list[int], center: list[int]) -> list[str]:
    width = center[-1] + col_w[-1] // 2 + 1
    border = [" "] * width
    names = [" "] * width
    for i, name in enumerate(seq.participants):
        start = center[i] - col_w[i] // 2
        border[start : start + col_w[i]] = list("+" + "-" * (col_w[i] - 2) + "+")
        names[start : start + col_w[i]] = list("| " + name + " |")
    return ["".join(border).rstrip(), "".join(names).rstrip(), "".join(border).rstrip()]


def _spacer(center: list[int], width: int) -> str:
    row = [" "] * width
    for c in center:
        row[c] = "|"
    return "".join(row).rstrip()


def render(seq: Sequence) -> str:
    col_w, center, _gap = _layout(seq)
    width = center[-1] + col_w[-1] // 2 + 1
    index = {name: i for i, name in enumerate(seq.participants)}

    out: list[str] = []
    if seq.title:
        out.append(seq.title)
        out.append("")
    out.extend(_boxes(seq, col_w, center))

    for row in seq.rows:
        out.append(_spacer(center, width))
        if isinstance(row, Note):
            out.append(f"[ {row.text} ]")
            continue

        line = list(_spacer(center, width).ljust(width))
        i, j = index[row.source], index[row.target]
        lo, hi = sorted((i, j))
        body_start, body_end = center[lo] + 1, center[hi]  # exclusive end
        body = body_end - body_start
        pad = body - 1 - len(row.label)
        head = pad // 2
        tail = pad - head
        if i < j:
            drawn = "-" * head + row.label + "-" * tail + ">"
        else:
            drawn = "<" + "-" * head + row.label + "-" * tail
        line[body_start:body_end] = list(drawn)
        out.append("".join(line).rstrip())

    out.append(_spacer(center, width))
    out.extend(_boxes(seq, col_w, center))
    return "\n".join(out) + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_seqrender.py -q`
Expected: PASS, 21 tests

- [ ] **Step 5: Eyeball one rendering**

Run:
```bash
.venv/bin/python -c "
from tools.seqrender import parse, render
print(render(parse(open('/dev/stdin').read())))
" <<'EOF'
title: Player flow
participants: Phone, Arco, Control
Phone -> Arco: /game/hello
Arco -> Control: /game/hello
Control -> Arco: /ie1/role
Arco -> Phone: /ie1/role
EOF
```
Expected: boxes aligned top and bottom, arrows land exactly on the `|` columns, no label truncated. If a `|` is overwritten or an arrowhead misses a column, fix the layout math before continuing.

- [ ] **Step 6: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add tools/seqrender.py tests/test_seqrender.py
git commit -m "feat(diagrams): render sequence diagrams to ASCII, no synthesized returns"
```

---

### Task 3: Label extraction and static validation

**Files:**
- Create: `tools/render_diagrams.py`
- Test: `tests/test_render_diagrams.py`

**Interfaces:**
- Consumes: `tools.seqrender.parse`, `tools.seqrender.labels`.
- Produces: `ValidationError(Exception)`, `d2_labels(text: str) -> list[str]`, `source_labels(renderer: str, text: str) -> list[str]`, `validate_d2_source(name: str, text: str) -> None`.

`d2_labels` is deliberately a simple line scanner, not a D2 parser. We author the sources, so if it misreads one, the fix is to simplify the source. Known limits: a label containing `#` is truncated at the `#`, and a label spanning multiple physical lines is not seen.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_diagrams.py
import pytest

from tools.render_diagrams import (
    ValidationError,
    d2_labels,
    source_labels,
    validate_d2_source,
)


def test_d2_labels_reads_node_and_edge_labels():
    text = 'a: Arco server\nb: Control\na -> b: /game/join\n'
    assert d2_labels(text) == ["Arco server", "Control", "/game/join"]


def test_d2_labels_strips_quotes():
    assert d2_labels('a -> b: "cc:74 -> hue"') == ["cc:74 -> hue"]


def test_d2_labels_strips_a_container_brace():
    assert d2_labels("box: Terrarium box {") == ["Terrarium box"]


def test_d2_labels_skips_styling_keywords():
    text = "a: Arco\na.shape: circle\na.style.fill: red\ndirection: down\n"
    assert d2_labels(text) == ["Arco"]


def test_d2_labels_skips_comments_and_blank_lines():
    assert d2_labels("# a comment\n\na: Arco\n") == ["Arco"]


def test_source_labels_dispatches_to_seqrender():
    got = source_labels("seq", "participants: A, B\nA -> B: /game/hello\n")
    assert got == ["/game/hello"]


def test_validate_rejects_a_newline_inside_a_label():
    """Measured to corrupt D2's box grid: spec section 1.2."""
    with pytest.raises(ValidationError) as exc:
        validate_d2_source("topology", 'arco: Arco server\\n(o2 hub)')
    assert "topology" in str(exc.value)
    assert "\\n" in str(exc.value)


def test_validate_rejects_sequence_diagram_shape():
    """D2's sequence renderer corrupts every slash-bearing label."""
    with pytest.raises(ValidationError) as exc:
        validate_d2_source("flow", "shape: sequence_diagram\na: A\n")
    assert "seqrender" in str(exc.value)


def test_validate_accepts_a_clean_source():
    validate_d2_source("topology", "a: Arco\na -> b: /game/join\n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_diagrams.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'tools.render_diagrams'`

- [ ] **Step 3: Write the module**

```python
# tools/render_diagrams.py
"""Render docs/diagrams/ sources to ASCII and SVG, and inject the ASCII into
the deep-dive between HTML-comment markers.

Offline tooling, not runtime. Nothing here is imported by the Control+GameServer.
Spec: docs/superpowers/specs/2026-08-17-ascii-diagram-pipeline-design.md
"""

from __future__ import annotations

from tools import seqrender


class ValidationError(Exception):
    """A diagram source or its rendered output failed a check."""


_D2_KEYWORDS = {
    "shape", "direction", "style", "fill", "stroke", "stroke-width", "stroke-dash",
    "font-size", "font-color", "near", "icon", "width", "height", "link", "tooltip",
    "class", "constraint", "opacity", "border-radius", "3d", "multiple", "animated",
    "bold", "italic", "underline", "text-transform", "label", "vars", "grid-rows",
    "grid-columns",
}


def d2_labels(text: str) -> list[str]:
    """Every string in a .d2 source that must survive rendering verbatim."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().split(".")[-1] in _D2_KEYWORDS:
            continue
        value = value.strip()
        if value.endswith("{"):
            value = value[:-1].strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if value:
            out.append(value)
    return out


def source_labels(renderer: str, text: str) -> list[str]:
    if renderer == "d2":
        return d2_labels(text)
    if renderer == "seq":
        return seqrender.labels(seqrender.parse(text))
    raise ValidationError(f"unknown renderer {renderer!r}")


def validate_d2_source(name: str, text: str) -> None:
    """Static rules derived from measured D2 defects (spec section 1.2)."""
    if "shape: sequence_diagram" in text:
        raise ValidationError(
            f"{name}: D2's sequence renderer corrupts labels containing '/', '-' or '.'. "
            f"Use a .seq source rendered by tools/seqrender.py instead."
        )
    for label in d2_labels(text):
        if "\\n" in label:
            raise ValidationError(
                f"{name}: label {label!r} contains a literal \\n, which corrupts D2's "
                f"box grid. Split it into separate nodes."
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_render_diagrams.py -q`
Expected: PASS, 9 tests

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add tools/render_diagrams.py tests/test_render_diagrams.py
git commit -m "feat(diagrams): extract labels and statically validate diagram sources"
```

---

### Task 4: The post-render round trip

**Files:**
- Modify: `tools/render_diagrams.py`
- Test: `tests/test_render_diagrams.py`

**Interfaces:**
- Consumes: `ValidationError` from Task 3.
- Produces: `verify_labels_present(name: str, renderer: str, labels: list[str], rendered: str) -> None`.

This is the check that generalises past the specific 2026-08-17 defects. It knows nothing about them and catches all of them.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_render_diagrams.py
from tools.render_diagrams import verify_labels_present


def test_round_trip_passes_when_every_label_survives():
    verify_labels_present("topology", "d2", ["/game/join", "Arco"], "box /game/join -> Arco")


def test_round_trip_catches_the_measured_d2_slash_corruption():
    """D2 rendered '/abc' as 'abc' on right-to-left arrows. Spec section 1.2."""
    with pytest.raises(ValidationError) as exc:
        verify_labels_present("flow", "d2", ["/abc"], "|<-----abc------|")
    assert "/abc" in str(exc.value)
    assert "flow" in str(exc.value)
    assert "d2" in str(exc.value)


def test_round_trip_catches_a_silently_dropped_message():
    """Diagon dropped a '//' message and every message after it."""
    with pytest.raises(ValidationError):
        verify_labels_present("flow", "seq", ["/a", "//b", "/c"], "only /a here")


def test_round_trip_reports_the_first_missing_label_only():
    with pytest.raises(ValidationError) as exc:
        verify_labels_present("d", "d2", ["gone", "also-gone"], "")
    assert "gone" in str(exc.value)
    assert "also-gone" not in str(exc.value)


def test_round_trip_names_the_regenerate_command():
    with pytest.raises(ValidationError) as exc:
        verify_labels_present("d", "d2", ["x"], "")
    assert "shorten" in str(exc.value).lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_diagrams.py -q`
Expected: FAIL, `ImportError: cannot import name 'verify_labels_present'`

- [ ] **Step 3: Implement it**

```python
# append to tools/render_diagrams.py
def verify_labels_present(name: str, renderer: str, labels: list[str], rendered: str) -> None:
    """Assert every source label appears verbatim in the rendered text.

    Renderers in this space corrupt or silently truncate rather than raising.
    This check caught, without knowing about any of them: D2 swallowing '/' from
    right-to-left labels, Diagon dropping a '//' message and everything after it,
    and 'cc:74' mis-parsing into a stray participant.
    """
    for label in labels:
        if label not in rendered:
            raise ValidationError(
                f"{name}: renderer {renderer!r} did not reproduce label {label!r} "
                f"verbatim. Either the renderer corrupted it or it wrapped across "
                f"lines; shorten the label."
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_render_diagrams.py -q`
Expected: PASS, 14 tests

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add tools/render_diagrams.py tests/test_render_diagrams.py
git commit -m "feat(diagrams): verify every source label survives rendering verbatim"
```

---

### Task 5: Manifest and marker injection

**Files:**
- Modify: `tools/render_diagrams.py`
- Test: `tests/test_render_diagrams.py`

**Interfaces:**
- Consumes: `ValidationError`.
- Produces: `sha256_text(text: str) -> str`, `open_marker(name: str) -> str`, `close_marker(name: str) -> str`, `build_region(name: str, ascii_text: str) -> str`, `inject(markdown: str, name: str, ascii_text: str) -> str`, `markers_in(markdown: str) -> set[str]`, `extract_region(markdown: str, name: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_render_diagrams.py
from tools.render_diagrams import (
    build_region,
    extract_region,
    inject,
    markers_in,
    open_marker,
    sha256_text,
)

DOC = """\
# Title

Some prose.

<!-- diagram:topology GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
old
```
<!-- /diagram:topology -->

More prose.
"""


def test_sha256_text_is_stable():
    assert sha256_text("abc") == sha256_text("abc")
    assert sha256_text("abc") != sha256_text("abd")


def test_build_region_wraps_in_markers_and_an_ascii_fence():
    region = build_region("topology", "BOX\n")
    assert region.startswith(open_marker("topology"))
    assert "```ascii\nBOX\n```" in region
    assert region.rstrip().endswith("<!-- /diagram:topology -->")


def test_inject_replaces_only_the_marked_region():
    out = inject(DOC, "topology", "NEW\n")
    assert "NEW" in out
    assert "old" not in out
    assert "Some prose." in out
    assert "More prose." in out


def test_inject_is_idempotent():
    once = inject(DOC, "topology", "NEW\n")
    assert inject(once, "topology", "NEW\n") == once


def test_inject_raises_when_the_marker_is_missing():
    with pytest.raises(ValidationError) as exc:
        inject("# Title\n", "topology", "NEW\n")
    assert "topology" in str(exc.value)


def test_extract_region_round_trips_with_build_region():
    out = inject(DOC, "topology", "NEW\n")
    assert extract_region(out, "topology") == build_region("topology", "NEW\n")


def test_markers_in_finds_every_declared_diagram():
    assert markers_in(DOC) == {"topology"}


def test_markers_in_returns_empty_for_a_doc_with_none():
    assert markers_in("# Title\n\nprose\n") == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_diagrams.py -q`
Expected: FAIL, `ImportError: cannot import name 'build_region'`

- [ ] **Step 3: Implement it**

```python
# append to tools/render_diagrams.py
import hashlib
import re

_MARKER_RE = re.compile(r"<!--\s*diagram:([A-Za-z0-9_-]+)\s+GENERATED[^>]*-->")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def open_marker(name: str) -> str:
    return f"<!-- diagram:{name} GENERATED by tools/render_diagrams.py -- do not hand-edit -->"


def close_marker(name: str) -> str:
    return f"<!-- /diagram:{name} -->"


def build_region(name: str, ascii_text: str) -> str:
    body = ascii_text.rstrip("\n")
    return f"{open_marker(name)}\n```ascii\n{body}\n```\n{close_marker(name)}"


def extract_region(markdown: str, name: str) -> str:
    start = markdown.find(open_marker(name))
    close = close_marker(name)
    end = markdown.find(close, start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        raise ValidationError(
            f"marker for diagram {name!r} not found in the target document. "
            f"Add:\n{open_marker(name)}\n```ascii\n```\n{close_marker(name)}"
        )
    return markdown[start : end + len(close)]


def inject(markdown: str, name: str, ascii_text: str) -> str:
    region = extract_region(markdown, name)
    return markdown.replace(region, build_region(name, ascii_text), 1)


def markers_in(markdown: str) -> set[str]:
    return set(_MARKER_RE.findall(markdown))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_render_diagrams.py -q`
Expected: PASS, 22 tests

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add tools/render_diagrams.py tests/test_render_diagrams.py
git commit -m "feat(diagrams): hash, marker and injection primitives"
```

---

### Task 6: The render pipeline and CLI

**Files:**
- Modify: `tools/render_diagrams.py`
- Test: `tests/test_render_diagrams.py`

**Interfaces:**
- Consumes: everything from Tasks 3, 4, 5.
- Produces: `RenderError(Exception)`, `render_d2(source: Path, workdir: Path) -> dict[str, str]`, `render_seq(source: Path, workdir: Path) -> dict[str, str]`, `render_all(root: Path, renderers: dict[str, Callable] | None = None) -> dict[str, dict[str, str]]`, `write_all(root: Path, rendered, manifest) -> None`, `main(argv: list[str] | None = None) -> int`.

Atomicity comes from rendering everything into memory first. Nothing is written to `out/`, the markdown, or the manifest until every diagram has passed both validation stages.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_render_diagrams.py
import json
from pathlib import Path

from tools.render_diagrams import RenderError, main, render_all


def _fixture_root(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "diagrams" / "out").mkdir(parents=True)
    (tmp_path / "docs" / "diagrams" / "demo.seq").write_text(
        "participants: A, B\nA -> B: /game/hello\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "diagrams" / "manifest.json").write_text(
        json.dumps(
            {
                "d2_version": "0.8.1",
                "diagrams": {
                    "demo": {
                        "source": "demo.seq",
                        "renderer": "seq",
                        "source_sha256": "",
                        "outputs": {},
                        "inject_into": "docs/TARGET.md",
                        "marker": "demo",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "docs" / "TARGET.md").write_text(
        "# T\n\n"
        "<!-- diagram:demo GENERATED by tools/render_diagrams.py -- do not hand-edit -->\n"
        "```ascii\n```\n"
        "<!-- /diagram:demo -->\n",
        encoding="utf-8",
    )
    return tmp_path


def test_render_all_renders_a_seq_diagram_without_d2(tmp_path):
    root = _fixture_root(tmp_path)
    rendered = render_all(root)
    assert "/game/hello" in rendered["demo"]["out/demo.txt"]


def test_main_writes_outputs_markdown_and_manifest(tmp_path):
    root = _fixture_root(tmp_path)
    assert main(["--root", str(root)]) == 0
    assert "/game/hello" in (root / "docs/diagrams/out/demo.txt").read_text()
    assert "/game/hello" in (root / "docs/TARGET.md").read_text()
    manifest = json.loads((root / "docs/diagrams/manifest.json").read_text())
    assert manifest["diagrams"]["demo"]["source_sha256"]
    assert manifest["diagrams"]["demo"]["outputs"]["out/demo.txt"]


def test_main_is_idempotent(tmp_path):
    root = _fixture_root(tmp_path)
    main(["--root", str(root)])
    first = (root / "docs/TARGET.md").read_text()
    main(["--root", str(root)])
    assert (root / "docs/TARGET.md").read_text() == first


def test_check_mode_writes_nothing(tmp_path):
    root = _fixture_root(tmp_path)
    before = (root / "docs/TARGET.md").read_text()
    rc = main(["--root", str(root), "--check"])
    assert rc == 1
    assert (root / "docs/TARGET.md").read_text() == before
    assert not (root / "docs/diagrams/out/demo.txt").exists()


def test_check_mode_returns_zero_when_current(tmp_path):
    root = _fixture_root(tmp_path)
    main(["--root", str(root)])
    assert main(["--root", str(root), "--check"]) == 0


def test_a_failing_diagram_writes_nothing(tmp_path):
    """Atomicity: diagram two failing must not leave diagram one on disk."""
    root = _fixture_root(tmp_path)
    (root / "docs/diagrams/bad.seq").write_text(
        "participants: A, B\nA -> A: oops\n", encoding="utf-8"
    )
    manifest = json.loads((root / "docs/diagrams/manifest.json").read_text())
    manifest["diagrams"]["bad"] = {
        "source": "bad.seq", "renderer": "seq",
        "source_sha256": "", "outputs": {},
    }
    (root / "docs/diagrams/manifest.json").write_text(json.dumps(manifest))
    assert main(["--root", str(root)]) == 1
    assert not (root / "docs/diagrams/out/demo.txt").exists()


def test_missing_d2_binary_reports_the_install_command(tmp_path, monkeypatch):
    root = _fixture_root(tmp_path)
    (root / "docs/diagrams/g.d2").write_text("a: A\n", encoding="utf-8")
    manifest = json.loads((root / "docs/diagrams/manifest.json").read_text())
    manifest["diagrams"] = {
        "g": {"source": "g.d2", "renderer": "d2", "source_sha256": "", "outputs": {}}
    }
    (root / "docs/diagrams/manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    with pytest.raises(RenderError) as exc:
        render_all(root)
    assert "brew install d2" in str(exc.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render_diagrams.py -q`
Expected: FAIL, `ImportError: cannot import name 'render_all'`

- [ ] **Step 3: Implement it**

```python
# append to tools/render_diagrams.py
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


class RenderError(Exception):
    """A renderer could not be run."""


def render_d2(source: Path, workdir: Path) -> dict[str, str]:
    if shutil.which("d2") is None:
        raise RenderError(
            "d2 is not on PATH and is required to regenerate diagrams.\n"
            "Install it with: brew install d2"
        )
    stem = source.stem
    txt, svg = workdir / f"{stem}.txt", workdir / f"{stem}.svg"
    for target in (txt, svg):
        proc = subprocess.run(
            ["d2", str(source), str(target)], capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise RenderError(f"{stem}: d2 failed\n{proc.stderr.strip()}")
    return {f"out/{stem}.txt": txt.read_text("utf-8"),
            f"out/{stem}.svg": svg.read_text("utf-8")}


def render_seq(source: Path, _workdir: Path) -> dict[str, str]:
    text = source.read_text("utf-8")
    rendered = seqrender.render(seqrender.parse(text))
    return {f"out/{source.stem}.txt": rendered}


_RENDERERS = {"d2": render_d2, "seq": render_seq}


def load_manifest(root: Path) -> dict:
    return json.loads((root / "docs/diagrams/manifest.json").read_text("utf-8"))


def render_all(root: Path, renderers: dict | None = None) -> dict[str, dict[str, str]]:
    """Render every diagram into memory. Writes nothing."""
    renderers = renderers or _RENDERERS
    manifest = load_manifest(root)
    out: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for name, entry in manifest["diagrams"].items():
            source = root / "docs/diagrams" / entry["source"]
            text = source.read_text("utf-8")
            renderer = entry["renderer"]
            if renderer == "d2":
                validate_d2_source(name, text)
            rendered = renderers[renderer](source, workdir)
            ascii_text = rendered[f"out/{source.stem}.txt"]
            verify_labels_present(name, renderer, source_labels(renderer, text), ascii_text)
            out[name] = rendered
    return out


def write_all(root: Path, rendered: dict[str, dict[str, str]], manifest: dict) -> None:
    docs = root / "docs/diagrams"
    (docs / "out").mkdir(parents=True, exist_ok=True)
    for name, entry in manifest["diagrams"].items():
        source = docs / entry["source"]
        entry["source_sha256"] = sha256_text(source.read_text("utf-8"))
        entry["outputs"] = {}
        for rel, text in rendered[name].items():
            (docs / rel).write_text(text, encoding="utf-8")
            entry["outputs"][rel] = sha256_text(text)
        target = entry.get("inject_into")
        if target:
            path = root / target
            ascii_text = rendered[name][f"out/{source.stem}.txt"]
            path.write_text(
                inject(path.read_text("utf-8"), entry["marker"], ascii_text),
                encoding="utf-8",
            )
    (docs / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _is_current(root: Path, rendered: dict, manifest: dict) -> bool:
    for name, entry in manifest["diagrams"].items():
        for rel, text in rendered[name].items():
            path = root / "docs/diagrams" / rel
            if not path.exists() or path.read_text("utf-8") != text:
                return False
        if entry.get("source_sha256") != sha256_text(
            (root / "docs/diagrams" / entry["source"]).read_text("utf-8")
        ):
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render docs/diagrams sources.")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--check", action="store_true",
                        help="report whether outputs are current; write nothing")
    args = parser.parse_args(argv)
    root = Path(args.root)

    try:
        rendered = render_all(root)
    except (ValidationError, RenderError, seqrender.SeqParseError) as exc:
        print(f"render_diagrams: {exc}")
        return 1

    manifest = load_manifest(root)
    if args.check:
        if _is_current(root, rendered, manifest):
            print("render_diagrams: diagrams are current")
            return 0
        print("render_diagrams: diagrams are STALE. Run: python -m tools.render_diagrams")
        return 1

    write_all(root, rendered, manifest)
    print(f"render_diagrams: wrote {len(rendered)} diagram(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_render_diagrams.py -q`
Expected: PASS, 29 tests

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add tools/render_diagrams.py tests/test_render_diagrams.py
git commit -m "feat(diagrams): all-or-nothing render pipeline with a --check mode"
```

---

### Task 7: The topology diagram

**Prerequisite:** `brew install d2` (v0.8.1+). Verify with `d2 --version`.

**Files:**
- Create: `docs/diagrams/topology.d2`, `docs/diagrams/manifest.json`
- Modify: `docs/MM_TERRARIUM.md` (the *What it is, in one picture* block)

This is the proof-of-pipeline task: it is the one diagram that already exists by hand, so it is the only one where the output can be judged against a known-good before-and-after.

- [ ] **Step 1: Write the source**

```
# docs/diagrams/topology.d2
direction: down

devices: Interactive Elements {
  shroom: Tuneshroom (o2lite)
  phone: Phone browser (websocket)
}

box: Terrarium box (one per room) {
  arco: Arco server, service "arco"
  control: Control+GameServer, services "game" and "actl"
}

devices.shroom -> box.arco: o2lite
devices.phone -> box.arco: websocket
box.arco -> box.control: o2lite, same box
box.control -> box.arco: only Control writes to /arco
```

- [ ] **Step 2: Write the manifest**

```json
{
  "d2_version": "0.8.1",
  "diagrams": {
    "topology": {
      "source": "topology.d2",
      "renderer": "d2",
      "source_sha256": "",
      "outputs": {},
      "inject_into": "docs/MM_TERRARIUM.md",
      "marker": "topology"
    }
  }
}
```

- [ ] **Step 3: Replace the hand-drawn block with empty markers**

In `docs/MM_TERRARIUM.md`, under `## What it is, in one picture`, replace the existing fenced ASCII block (the one beginning `Phone browser --ws--+`) with:

````
<!-- diagram:topology GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
```
<!-- /diagram:topology -->
````

Leave the paragraph below it (`each Tuneshroom offers "ie<N>"...` context and the **Bit** definition) untouched.

- [ ] **Step 4: Render**

Run: `.venv/bin/python -m tools.render_diagrams`
Expected: `render_diagrams: wrote 1 diagram(s)`

If it fails with a label round-trip error, shorten the offending label in `topology.d2` and re-run. Do not weaken the check.

- [ ] **Step 5: Read the output and judge it**

Run: `cat docs/diagrams/out/topology.txt`

It must be at least as clear as the block it replaced. Check that both containers render, that no box is overwritten by an edge, and that all four edge labels are present and legible. If D2's layout is poor, adjust `direction:` or reorder declarations and re-render. This step is editorial, not mechanical.

- [ ] **Step 6: Verify `--check` agrees and commit**

```bash
.venv/bin/python -m tools.render_diagrams --check
.venv/bin/python -m pytest tests -q
git add docs/diagrams docs/MM_TERRARIUM.md
git commit -m "feat(diagrams): generate the topology diagram from source"
```

---

### Task 8: The drift test

**Files:**
- Create: `tests/test_diagrams.py`

**Interfaces:**
- Consumes: `sha256_text`, `build_region`, `extract_region`, `markers_in`, `load_manifest` from `tools.render_diagrams`.
- Produces: nothing importable.

Stdlib only. Never invokes D2. It runs against the real committed manifest, which exists as of Task 7, so it needs no skip logic.

- [ ] **Step 1: Write the tests**

```python
# tests/test_diagrams.py
"""Detect a diagram that no longer matches its source.

Pure stdlib: no d2, no node, no network, so this runs in the core offline
suite. Spec: docs/superpowers/specs/2026-08-17-ascii-diagram-pipeline-design.md

Known limit: the manifest is the source of truth, so hand-editing both a
rendered file and its recorded hash would pass. This catches forgetting to
re-render, which is the realistic failure.
"""

from pathlib import Path

import pytest

from tools.render_diagrams import (
    build_region,
    extract_region,
    load_manifest,
    markers_in,
    sha256_text,
)

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs/diagrams"
STALE = "stale: run `python -m tools.render_diagrams`"


def _entries():
    return sorted(load_manifest(ROOT)["diagrams"].items())


@pytest.mark.parametrize("name,entry", _entries())
def test_source_matches_its_recorded_hash(name, entry):
    text = (DIAGRAMS / entry["source"]).read_text("utf-8")
    assert sha256_text(text) == entry["source_sha256"], f"{name}: source {STALE}"


@pytest.mark.parametrize("name,entry", _entries())
def test_outputs_match_their_recorded_hashes(name, entry):
    assert entry["outputs"], f"{name}: manifest records no outputs"
    for rel, digest in entry["outputs"].items():
        path = DIAGRAMS / rel
        assert path.exists(), f"{name}: missing output {rel}"
        assert sha256_text(path.read_text("utf-8")) == digest, f"{name}/{rel}: {STALE}"


@pytest.mark.parametrize("name,entry", _entries())
def test_injected_region_matches_the_rendered_text(name, entry):
    target = entry.get("inject_into")
    if not target:
        return
    stem = Path(entry["source"]).stem
    ascii_text = (DIAGRAMS / f"out/{stem}.txt").read_text("utf-8")
    markdown = (ROOT / target).read_text("utf-8")
    expected = build_region(entry["marker"], ascii_text)
    assert extract_region(markdown, entry["marker"]) == expected, f"{name}: {STALE}"


def test_no_orphaned_markers_in_targets():
    manifest = load_manifest(ROOT)
    by_target: dict[str, set[str]] = {}
    for entry in manifest["diagrams"].values():
        target = entry.get("inject_into")
        if target:
            by_target.setdefault(target, set()).add(entry["marker"])
    for target, declared in by_target.items():
        found = markers_in((ROOT / target).read_text("utf-8"))
        assert found == declared, (
            f"{target}: markers in the document {found} do not match the manifest "
            f"{declared}. An undeclared marker is never regenerated."
        )


def test_every_manifest_source_exists():
    for name, entry in _entries():
        assert (DIAGRAMS / entry["source"]).exists(), f"{name}: missing source"
```

- [ ] **Step 2: Run them to verify they pass against the real manifest**

Run: `.venv/bin/python -m pytest tests/test_diagrams.py -q`
Expected: PASS

- [ ] **Step 3: Prove the test actually catches drift**

```bash
printf '\n# drift\n' >> docs/diagrams/topology.d2
.venv/bin/python -m pytest tests/test_diagrams.py -q
```
Expected: FAIL on `test_source_matches_its_recorded_hash` with the "stale" message.

Then revert and confirm green:
```bash
git checkout docs/diagrams/topology.d2
.venv/bin/python -m pytest tests/test_diagrams.py -q
```
Expected: PASS

Do not skip this step. A drift test that cannot be shown to fail is not a drift test.

- [ ] **Step 4: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add tests/test_diagrams.py
git commit -m "test(diagrams): fail the offline suite when a diagram goes stale"
```

---

### Task 9: The boot and teardown-order diagram

**Files:**
- Create: `docs/diagrams/boot-teardown.d2`
- Modify: `docs/diagrams/manifest.json`, `docs/MM_TERRARIUM.md`

Content source: the *teardown order, structurally* section of `MM_TERRARIUM.md` and [`2026-08-14-teardown-order-and-stack-runner-design.md`](../specs/2026-08-14-teardown-order-and-stack-runner-design.md). The invariant to make visible: **anything registered later is torn down earlier.**

- [ ] **Step 1: Write the source**

```
# docs/diagrams/boot-teardown.d2
direction: right

up: Startup order (TeardownStack push) {
  direction: down
  s1: 1. devicelink server (websocket mode only)
  s2: 2. Arco subprocess
  s3: 3. Room simulator subprocess
  s4: 4. Room bridge
  s5: 5. Bit
  s1 -> s2 -> s3 -> s4 -> s5
}

down: Teardown order (LIFO pop) {
  direction: down
  t5: 5. Bit (abort)
  t4: 4. Room bridge (frees the Arco voice)
  t3: 3. Room simulator subprocess
  t2: 2. Arco subprocess
  t1: 1. devicelink server
  t5 -> t4 -> t3 -> t2 -> t1
}

up.s5 -> down.t5: registered later, torn down earlier
```

- [ ] **Step 2: Add the manifest entry**

Add to `docs/diagrams/manifest.json` under `diagrams`:

```json
"boot-teardown": {
  "source": "boot-teardown.d2",
  "renderer": "d2",
  "source_sha256": "",
  "outputs": {},
  "inject_into": "docs/MM_TERRARIUM.md",
  "marker": "boot-teardown"
}
```

- [ ] **Step 3: Add empty markers to the doc**

In `docs/MM_TERRARIUM.md`, in the `control/teardown.py` section immediately after the sentence stating the LIFO invariant, insert:

````
<!-- diagram:boot-teardown GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
```
<!-- /diagram:boot-teardown -->
````

- [ ] **Step 4: Render and judge**

```bash
.venv/bin/python -m tools.render_diagrams
cat docs/diagrams/out/boot-teardown.txt
```
Expected: two labelled columns, five steps each, reading in opposite directions. If the o2lite-vs-websocket difference is not legible, add a note node rather than a second diagram.

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add docs/diagrams docs/MM_TERRARIUM.md
git commit -m "feat(diagrams): generate the boot and teardown-order diagram"
```

---

### Task 10: The cue-path diagram

**Files:**
- Create: `docs/diagrams/cue-path.d2`
- Modify: `docs/diagrams/manifest.json`, `docs/MM_TERRARIUM.md`

Content source: the *load-bearing timed cues* section. The point to make visible: one gesture produces two cues from one computed time, and the horizon is added once, in `GameServer.data()`, never seen by the Bit.

- [ ] **Step 1: Write the source**

```
# docs/diagrams/cue-path.d2
direction: down

gesture: Device gesture, stamped at origin
data: "GameServer.data(): at = origin + cue_horizon"
handler: "Bit verb handler(dev, args, at)"
cues: Emitted cues
sink: "on_light_cue (transport-owned sink)"
queue: "TimedQueue: release at the tick covering when"
dev_render: Calling device renders its frame
room_render: Room renders light and drone

gesture -> data: raw stamp
data -> handler: at, never the horizon itself
handler -> cues: one gesture, two cues
cues -> sink: dev-targeted
cues -> sink: ROOM sentinel
sink -> queue: when = at
queue -> dev_render: on time
queue -> room_render: on time
```

- [ ] **Step 2: Add the manifest entry**

```json
"cue-path": {
  "source": "cue-path.d2",
  "renderer": "d2",
  "source_sha256": "",
  "outputs": {},
  "inject_into": "docs/MM_TERRARIUM.md",
  "marker": "cue-path"
}
```

- [ ] **Step 3: Add empty markers to the doc**

In `docs/MM_TERRARIUM.md`, in the *Control on o2lite, and timed cues* section, immediately after the paragraph describing `GameServer.data()` computing `at = origin + cue_horizon`, insert:

````
<!-- diagram:cue-path GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
```
<!-- /diagram:cue-path -->
````

- [ ] **Step 4: Render and judge**

```bash
.venv/bin/python -m tools.render_diagrams
cat docs/diagrams/out/cue-path.txt
```
Expected: a single top-to-bottom chain that forks once at `cues`. Note that two edges from `cues` to `sink` carry different labels; if D2 overlaps them illegibly, split `sink` into two nodes.

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add docs/diagrams docs/MM_TERRARIUM.md
git commit -m "feat(diagrams): generate the cue-path diagram"
```

---

### Task 11: The Bit lifecycle diagram

**Files:**
- Create: `docs/diagrams/lifecycle.d2`
- Modify: `docs/diagrams/manifest.json`, `docs/MM_TERRARIUM.md`

- [ ] **Step 1: Write the source**

```
# docs/diagrams/lifecycle.d2
direction: down

idle: IDLE
loading: LOADING
loaded: LOADED
setup: SETUP (registration open)
running: RUNNING
completing: COMPLETING
unloading: UNLOADING

idle -> loading: load_bit
loading -> loaded
loaded -> setup
setup -> running: run
running -> completing: Bit signals done from update(dt)
completing -> unloading
unloading -> idle
running -> completing: abort
note: During RUNNING scored roles are denied, jam roles stay open
note2: COMPLETING and UNLOADING are reachable even if a Bit hook raises
```

- [ ] **Step 2: Add the manifest entry**

```json
"lifecycle": {
  "source": "lifecycle.d2",
  "renderer": "d2",
  "source_sha256": "",
  "outputs": {},
  "inject_into": "docs/MM_TERRARIUM.md",
  "marker": "lifecycle"
}
```

- [ ] **Step 3: Add empty markers to the doc**

In `docs/MM_TERRARIUM.md`, in the `control/` section, immediately after the **State machine** bullet, insert:

````
<!-- diagram:lifecycle GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
```
<!-- /diagram:lifecycle -->
````

- [ ] **Step 4: Render and judge**

```bash
.venv/bin/python -m tools.render_diagrams
cat docs/diagrams/out/lifecycle.txt
```
Expected: a linear chain with the `abort` edge visible as a second path into COMPLETING. The two `note` nodes render as free-floating boxes; if D2 places them badly, connect them with a dashed edge to the state they annotate.

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add docs/diagrams docs/MM_TERRARIUM.md
git commit -m "feat(diagrams): generate the Bit lifecycle diagram"
```

---

### Task 12: The player-flow sequence diagram

**Files:**
- Create: `docs/diagrams/player-flow.seq`
- Modify: `docs/diagrams/manifest.json`, `docs/MM_TERRARIUM.md`

This is the only diagram rendered by `seqrender.py`, and the task that exercises it end to end through the pipeline. It needs no D2.

- [ ] **Step 1: Write the source**

```
title: Player flow, hello to complete

participants: Tuneshroom, Arco, Control

# Registration. Control offers "game" on Arco's hub as an o2lite guest.
Tuneshroom -> Arco: /game/hello
Arco -> Control: /game/hello
note: SETUP holds registration open; --setup-seconds widens the window
Tuneshroom -> Arco: /game/join TEST_PLAYER_NODE
Arco -> Control: /game/join
Control -> Arco: /ie1/role composed config blob
Arco -> Tuneshroom: /ie1/role

# Play. Gesture in, timed frames out.
Tuneshroom -> Arco: /game/tilt
Arco -> Control: /game/tilt
Control -> Arco: /ie1/leds at = origin + cue_horizon
Arco -> Tuneshroom: /ie1/leds

# Completion. Release arrives after the closing fade, not at Bit end.
Control -> Arco: /ie1/release
Arco -> Tuneshroom: /ie1/release
```

- [ ] **Step 2: Add the manifest entry**

```json
"player-flow": {
  "source": "player-flow.seq",
  "renderer": "seq",
  "source_sha256": "",
  "outputs": {},
  "inject_into": "docs/MM_TERRARIUM.md",
  "marker": "player-flow"
}
```

- [ ] **Step 3: Add empty markers to the doc**

In `docs/MM_TERRARIUM.md`, in the *What it is, in one picture* section, immediately after the paragraph defining a **Bit** and its player flow, insert:

````
<!-- diagram:player-flow GENERATED by tools/render_diagrams.py -- do not hand-edit -->
```ascii
```
<!-- /diagram:player-flow -->
````

- [ ] **Step 4: Render and judge**

```bash
.venv/bin/python -m tools.render_diagrams
cat docs/diagrams/out/player-flow.txt
```
Expected: three columns, every `/`-prefixed address intact, exactly one arrowhead per declared message and no synthesized returns. Confirm no `.svg` was produced for this diagram.

- [ ] **Step 5: Confirm the whole pipeline is current**

```bash
.venv/bin/python -m tools.render_diagrams --check
```
Expected: `render_diagrams: diagrams are current`, exit 0.

- [ ] **Step 6: Run the full suite and commit**

```bash
.venv/bin/python -m pytest tests -q
git add docs/diagrams docs/MM_TERRARIUM.md
git commit -m "feat(diagrams): generate the player-flow sequence diagram"
```

---

## Done when

- `.venv/bin/python -m pytest tests -q` passes with 764 + the new tests, still fully offline, with no D2 installed.
- `.venv/bin/python -m tools.render_diagrams --check` exits 0.
- `docs/MM_TERRARIUM.md` carries five generated diagrams and no hand-drawn ASCII in a marked region.
- `docs/control-gameserver-design.md` is byte-identical to its state at `7a4a845`.
