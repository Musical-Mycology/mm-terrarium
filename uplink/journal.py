"""A bounded on-box journal of bit_completed events (spec docs/superpowers/
specs/2026-09-13-mycoquest-handoff-terrarium-design.md section 6.4,
mm-terrarium issue #103).

One JSON line per event under the runs directory, appended at emit time
whether or not the uplink is up, and sent in file order on every connect
over a durable transport. Sending removes nothing: an entry leaves only when
the broker confirms its completion_id with completions_stored (box contract
2026-11-23 section 4.6), or when the cap drops it. Bounded by count: the
newest `cap` entries survive. Pure stdlib plus control/wire_json.dumps.
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
        # errors="replace": bytes that are not UTF-8 become a line that is
        # not JSON, skipped and purged like any other corrupt line, instead
        # of a UnicodeDecodeError that would stop the box at boot.
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
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
            dropped = len(lines) - self.cap
            self._rewrite(lines[-self.cap:])
            logger.warning("journal %s over its cap of %d; dropped the %d "
                           "oldest entries", self.path, self.cap, dropped)

    def _parse(self, lines: list[str] | None = None) -> list[tuple[str, dict]]:
        """(raw line, entry) for each valid line, in file order; warns once
        per skipped line."""
        out = []
        for number, line in enumerate(
                self._lines() if lines is None else lines, start=1):
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
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def remove(self, completion_ids: Iterable[str]) -> int:
        """Drop the entries whose completion_id is confirmed; returns how
        many were removed. Unknown ids are ignored. Every other raw line
        (valid, corrupt or id-less) is written back unchanged."""
        if isinstance(completion_ids, str):
            raise TypeError("completion_ids must be an iterable of ids, "
                            "not a bare str")
        ids = set(completion_ids)
        kept = []
        removed = 0
        for line in self._lines():
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if isinstance(entry, dict) and entry.get("completion_id") in ids:
                removed += 1
            else:
                kept.append(line)
        if removed:
            self._rewrite(kept)
        return removed

    def purge_unidentified(self) -> int:
        """Rewrite keeping only what entries() returns; returns the number
        of lines dropped."""
        raw = self._lines()
        kept = [line for line, _ in self._parse(raw)]
        dropped = len(raw) - len(kept)
        if dropped:
            self._rewrite(kept)
        return dropped

    def is_empty(self) -> bool:
        """True when the file is missing or has no raw lines at all.
        Distinct from entries() == [], which is also true when every raw
        line is corrupt or has no completion_id: use this to ask whether
        the file holds anything at all."""
        return not self._lines()

    def clear(self) -> None:
        with open(self.path, "w", encoding="utf-8"):
            pass
