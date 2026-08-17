"""Render sequence diagrams to ASCII.

Pure stdlib, no I/O. Written rather than taken off the shelf because every
off-the-shelf ASCII sequence renderer measured on 2026-08-17 was disqualified
for this repo: D2's swallows `/` from right-to-left labels, Diagon silently
drops messages containing `//` or `:`, and adia synthesizes a return arrow for
every message, which misrepresents fire-and-forget O2 traffic. See
docs/superpowers/specs/2026-08-17-ascii-diagram-pipeline-design.md section 1.2.
"""

from __future__ import annotations

import math
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
