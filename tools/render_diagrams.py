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
