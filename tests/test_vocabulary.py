"""Pins the Spec 3 rename: the acting-side Trigger vocabulary is gone.

A grep-shaped test, deliberately: the rename is total (spec section 2),
and this is what stops a future edit reintroducing the old names."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["control", "console", "devicelink", "bits", "harness", "tests"]
# Old acting-side identifiers and wire words. \b guards keep incidental
# English ("triggered the notification") and the NEW sensing entities
# (EventTrigger/StreamTrigger, control/triggers.py) out of scope.
FORBIDDEN = [
    r"\bFireTrigger\b", r"\bTriggerTable\b", r"\bTriggerTarget\b",
    r"\bTriggerFired\b", r"\bfire_trigger\b", r"\btrigger_table\b",
    r"\bvalidate_trigger_table\b", r"\btrigger_view\b", r"\btriggers_view\b",
    r"\btrigger_fired\b", r"\btriggers_changed\b", r"\baccepted_triggers\b",
    r"\bon_trigger_fired\b",
]

def test_acting_side_trigger_vocabulary_is_gone():
    pattern = re.compile("|".join(FORBIDDEN))
    hits = []
    for d in SCAN_DIRS:
        for path in (ROOT / d).rglob("*"):
            if path.suffix not in {".py", ".js", ".html", ".toml", ".css"}:
                continue
            if path.name == "test_vocabulary.py":
                continue
            for i, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                # Narrow exemption: control/terrarium_config.py's located
                # error tells an operator with a stale config to rename
                # 'accepted_triggers' to 'accepted_cues' (Spec 3). Reporting
                # the old key name is the whole point of that error, so it
                # necessarily contains the forbidden text. Use ONLY on the
                # lines implementing/exercising that one error.
                if line.rstrip().endswith("# legacy-vocabulary-ok"):
                    continue
                if pattern.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "old acting-side vocabulary survives:\n" + "\n".join(hits)
