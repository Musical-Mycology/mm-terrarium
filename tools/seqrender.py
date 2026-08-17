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
