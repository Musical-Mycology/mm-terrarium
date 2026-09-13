"""uplink/journal.py: the bounded bit_completed journal (spec 2026-09-13
section 6.4, mm-terrarium issue #103)."""
import logging

from uplink.journal import JOURNAL_CAP, JOURNAL_FILENAME, Journal


def test_constants():
    assert JOURNAL_FILENAME == "uplink_journal.jsonl"
    assert JOURNAL_CAP == 500


def test_missing_file_reads_as_empty(tmp_path):
    j = Journal(str(tmp_path / "runs" / "uplink_journal.jsonl"))
    assert j.entries() == []


def test_append_creates_the_directory_and_reads_back_in_order(tmp_path):
    j = Journal(str(tmp_path / "runs" / "uplink_journal.jsonl"))
    j.append({"event": "bit_completed", "n": 1})
    j.append({"event": "bit_completed", "n": 2})
    assert [e["n"] for e in j.entries()] == [1, 2]
    text = (tmp_path / "runs" / "uplink_journal.jsonl").read_text()
    assert text.count("\n") == 2


def test_cap_keeps_the_newest(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"), cap=3)
    for n in range(5):
        j.append({"n": n})
    assert [e["n"] for e in j.entries()] == [2, 3, 4]


def test_corrupt_line_is_skipped_and_logged(tmp_path, caplog):
    p = tmp_path / "j.jsonl"
    p.write_text('{"n": 1}\nnot json\n{"n": 3}\n')
    j = Journal(str(p))
    with caplog.at_level(logging.WARNING, logger="uplink.journal"):
        assert [e["n"] for e in j.entries()] == [1, 3]
    assert any("line 2" in r.getMessage() for r in caplog.records)


def test_clear_truncates(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"))
    j.append({"n": 1})
    j.clear()
    assert j.entries() == []
    assert (tmp_path / "j.jsonl").read_text() == ""


def test_lines_go_through_wire_json(tmp_path, monkeypatch):
    import uplink.journal as journal_module
    calls = []
    real = journal_module.dumps
    monkeypatch.setattr(journal_module, "dumps", lambda obj: (calls.append(obj), real(obj))[1])
    Journal(str(tmp_path / "j.jsonl")).append({"n": 1})
    assert calls == [{"n": 1}]
