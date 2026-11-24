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
    j.append({"event": "bit_completed", "n": 1, "completion_id": "c1"})
    j.append({"event": "bit_completed", "n": 2, "completion_id": "c2"})
    assert [e["n"] for e in j.entries()] == [1, 2]
    text = (tmp_path / "runs" / "uplink_journal.jsonl").read_text()
    assert text.count("\n") == 2


def test_cap_keeps_the_newest(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"), cap=3)
    for n in range(5):
        j.append({"n": n, "completion_id": f"c{n}"})
    assert [e["n"] for e in j.entries()] == [2, 3, 4]


def test_corrupt_line_is_skipped_and_logged(tmp_path, caplog):
    p = tmp_path / "j.jsonl"
    p.write_text('{"n": 1, "completion_id": "a"}\nnot json\n{"n": 3, "completion_id": "c"}\n')
    j = Journal(str(p))
    with caplog.at_level(logging.WARNING, logger="uplink.journal"):
        assert [e["n"] for e in j.entries()] == [1, 3]
    assert any("line 2" in r.getMessage() for r in caplog.records)


def test_clear_truncates(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"))
    j.append({"n": 1, "completion_id": "c1"})
    j.clear()
    assert j.entries() == []
    assert (tmp_path / "j.jsonl").read_text() == ""


def test_is_empty_true_for_missing_file(tmp_path):
    j = Journal(str(tmp_path / "runs" / "j.jsonl"))
    assert j.is_empty() is True


def test_is_empty_false_when_file_has_raw_lines_even_if_all_corrupt(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text("not json\nalso not json\n")
    j = Journal(str(p))
    assert j.entries() == []
    assert j.is_empty() is False


def test_is_empty_true_after_clear(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"))
    j.append({"n": 1, "completion_id": "c1"})
    j.clear()
    assert j.is_empty() is True


def test_lines_go_through_wire_json(tmp_path, monkeypatch):
    import uplink.journal as journal_module
    calls = []
    real = journal_module.dumps
    monkeypatch.setattr(journal_module, "dumps", lambda obj: (calls.append(obj), real(obj))[1])
    Journal(str(tmp_path / "j.jsonl")).append({"n": 1, "completion_id": "c1"})
    assert calls == [{"n": 1, "completion_id": "c1"}]


def _seed(tmp_path, ids):
    j = Journal(str(tmp_path / "j.jsonl"))
    for i in ids:
        j.append({"completion_id": i})
    return j


def test_remove_one_of_three_keeps_the_others_in_order(tmp_path):
    j = _seed(tmp_path, ["a", "b", "c"])
    assert j.remove(["b"]) == 1
    assert [e["completion_id"] for e in j.entries()] == ["a", "c"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["j.jsonl"]


def test_remove_unknown_id_returns_zero_and_file_is_byte_identical(tmp_path):
    j = _seed(tmp_path, ["a", "b"])
    before = (tmp_path / "j.jsonl").read_bytes()
    assert j.remove(["zzz"]) == 0
    assert (tmp_path / "j.jsonl").read_bytes() == before


def test_remove_on_missing_file_returns_zero(tmp_path):
    j = Journal(str(tmp_path / "nope" / "j.jsonl"))
    assert j.remove(["a"]) == 0


def test_entries_skip_entry_without_completion_id_with_warning(tmp_path, caplog):
    p = tmp_path / "j.jsonl"
    p.write_text('{"n": 1}\n{"completion_id": "", "n": 2}\n[1]\n'
                 '{"completion_id": 7}\nnot json\n{"completion_id": "ok"}\n')
    j = Journal(str(p))
    with caplog.at_level(logging.WARNING, logger="uplink.journal"):
        assert [e["completion_id"] for e in j.entries()] == ["ok"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 5


def test_purge_unidentified_drops_bad_lines_keeps_order(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text('{"completion_id": "a"}\n{"n": 1}\nnot json\n{"completion_id": "b"}\n')
    j = Journal(str(p))
    assert j.purge_unidentified() == 2
    assert [e["completion_id"] for e in j.entries()] == ["a", "b"]
    assert p.read_text().count("\n") == 2
    assert sorted(x.name for x in tmp_path.iterdir()) == ["j.jsonl"]
    assert j.purge_unidentified() == 0


def test_purge_unidentified_on_missing_file_returns_zero(tmp_path):
    assert Journal(str(tmp_path / "x.jsonl")).purge_unidentified() == 0


def test_cap_still_keeps_newest_500_by_default(tmp_path):
    j = Journal(str(tmp_path / "j.jsonl"))
    for n in range(505):
        j.append({"completion_id": f"c{n}"})
    ids = [e["completion_id"] for e in j.entries()]
    assert len(ids) == 500
    assert ids[0] == "c5" and ids[-1] == "c504"


def test_remove_keeps_corrupt_and_unidentified_lines_unchanged(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text('{"completion_id": "a"}\nnot json\n{"completion_id": "b"}\n'
                 '{"n": 9}\n{"completion_id": "c"}\n')
    j = Journal(str(p))
    assert j.remove(["b"]) == 1
    assert p.read_text() == ('{"completion_id": "a"}\nnot json\n'
                             '{"n": 9}\n{"completion_id": "c"}\n')


def test_remove_rejects_a_bare_string(tmp_path):
    import pytest
    j = _seed(tmp_path, ["a", "b"])
    with pytest.raises(TypeError):
        j.remove("abc")
    assert [e["completion_id"] for e in j.entries()] == ["a", "b"]


def test_failing_rewrite_leaves_original_and_no_temp_file(tmp_path, monkeypatch):
    import pytest
    import uplink.journal as journal_module
    j = _seed(tmp_path, ["a", "b"])
    before = (tmp_path / "j.jsonl").read_bytes()

    def boom(src, dst):
        raise OSError("disk")
    monkeypatch.setattr(journal_module.os, "replace", boom)
    with pytest.raises(OSError):
        j.remove(["a"])
    assert (tmp_path / "j.jsonl").read_bytes() == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["j.jsonl"]


# --- final fix wave ---------------------------------------------------------

def test_undecodable_bytes_are_a_corrupt_line_not_an_error(tmp_path, caplog):
    p = tmp_path / "j.jsonl"
    p.write_bytes(b'{"completion_id": "a"}\n\xff\xfe junk\n{"completion_id": "b"}\n')
    j = Journal(str(p))
    with caplog.at_level(logging.WARNING, logger="uplink.journal"):
        assert [e["completion_id"] for e in j.entries()] == ["a", "b"]
    assert any("line 2" in r.getMessage() for r in caplog.records)
    assert j.is_empty() is False


def test_undecodable_bytes_do_not_stop_append_remove_or_purge(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_bytes(b'{"completion_id": "a"}\n\xff\n')
    j = Journal(str(p))
    j.append({"completion_id": "b"})
    assert j.remove(["a"]) == 1
    assert j.purge_unidentified() == 1
    assert [e["completion_id"] for e in j.entries()] == ["b"]
    assert p.read_bytes() == b'{"completion_id": "b"}\n'


def test_rewrite_fsyncs_before_the_replace(tmp_path, monkeypatch):
    import uplink.journal as journal_module
    calls = []
    real_fsync, real_replace = journal_module.os.fsync, journal_module.os.replace
    monkeypatch.setattr(journal_module.os, "fsync",
                        lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(journal_module.os, "replace",
                        lambda a, b: (calls.append("replace"), real_replace(a, b))[1])
    j = _seed(tmp_path, ["a", "b"])
    calls.clear()
    j.remove(["a"])
    assert calls == ["fsync", "replace"]


def test_cap_trim_goes_through_the_atomic_rewrite_and_warns(tmp_path, caplog,
                                                            monkeypatch):
    import uplink.journal as journal_module
    replaced = []
    real_replace = journal_module.os.replace
    monkeypatch.setattr(journal_module.os, "replace",
                        lambda a, b: (replaced.append(b), real_replace(a, b))[1])
    j = Journal(str(tmp_path / "j.jsonl"), cap=3)
    for n in range(3):
        j.append({"completion_id": f"c{n}"})
    assert replaced == []                       # under the cap: plain append
    with caplog.at_level(logging.WARNING, logger="uplink.journal"):
        j.append({"completion_id": "c3"})
    assert replaced == [str(tmp_path / "j.jsonl")]
    assert [e["completion_id"] for e in j.entries()] == ["c1", "c2", "c3"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["j.jsonl"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "1 oldest" in warnings[0].getMessage()


def test_is_empty_docstring_no_longer_names_replay_journal():
    assert "_replay_journal" not in Journal.is_empty.__doc__
