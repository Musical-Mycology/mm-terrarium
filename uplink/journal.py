"""A bounded on-box journal of bit_completed events (spec docs/superpowers/
specs/2026-09-13-mycoquest-handoff-terrarium-design.md section 6.4,
mm-terrarium issue #103).

One JSON line per event under the runs directory, appended at emit time
whether or not the uplink is up, replayed in file order on reconnect over a
durable transport, then cleared. Bounded by count: the newest `cap`
entries survive. Pure stdlib plus control/wire_json.dumps.
"""

from __future__ import annotations

import json
import logging
import os

from control.wire_json import dumps

logger = logging.getLogger(__name__)

JOURNAL_FILENAME = "uplink_journal.jsonl"
JOURNAL_CAP = 500


class Journal:
    def __init__(self, path: str, cap: int = JOURNAL_CAP) -> None:
        self.path = path
        self.cap = cap

    def _lines(self) -> list[str]:
        try:
            with open(self.path, encoding="utf-8") as f:
                return [line for line in f.read().split("\n") if line]
        except FileNotFoundError:
            return []

    def append(self, event: dict) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(dumps(event) + "\n")
        lines = self._lines()
        if len(lines) > self.cap:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines[-self.cap:]) + "\n")

    def entries(self) -> list[dict]:
        out = []
        for number, line in enumerate(self._lines(), start=1):
            try:
                out.append(json.loads(line))
            except ValueError:
                logger.warning("journal %s line %d is not JSON; skipping",
                               self.path, number)
        return out

    def is_empty(self) -> bool:
        """True when the file is missing or has no raw lines at all --
        distinct from entries() == [], which is also true when every raw
        line is corrupt JSON. Callers that need to know whether there is
        anything left to truncate (see _replay_journal in uplink/link.py)
        must use this, not `not entries()`."""
        return not self._lines()

    def clear(self) -> None:
        with open(self.path, "w", encoding="utf-8"):
            pass
