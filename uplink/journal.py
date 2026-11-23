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
from typing import Iterable

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

    def _parse(self) -> list[tuple[str, dict]]:
        """(raw line, entry) for each valid line, in file order; warns once
        per skipped line."""
        out = []
        for number, line in enumerate(self._lines(), start=1):
            try:
                entry = json.loads(line)
            except ValueError:
                logger.warning("journal %s line %d is not JSON; skipping",
                               self.path, number)
                continue
            cid = entry.get("completion_id") if isinstance(entry, dict) else None
            if not isinstance(cid, str) or not cid:
                logger.warning("journal %s line %d has no completion_id; "
                               "skipping", self.path, number)
                continue
            out.append((line, entry))
        return out

    def entries(self) -> list[dict]:
        return [entry for _, entry in self._parse()]

    def _rewrite(self, lines: list[str]) -> None:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("".join(line + "\n" for line in lines))
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def remove(self, completion_ids: Iterable[str]) -> int:
        """Drop the entries whose completion_id is confirmed; returns how
        many were removed. Unknown ids are ignored."""
        ids = set(completion_ids)
        parsed = self._parse()
        kept = [line for line, e in parsed if e["completion_id"] not in ids]
        removed = len(parsed) - len(kept)
        if removed:
            self._rewrite(kept)
        return removed

    def purge_unidentified(self) -> int:
        """Rewrite keeping only what entries() returns; returns the number
        of lines dropped."""
        raw = self._lines()
        kept = [line for line, _ in self._parse()]
        dropped = len(raw) - len(kept)
        if dropped:
            self._rewrite(kept)
        return dropped

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
