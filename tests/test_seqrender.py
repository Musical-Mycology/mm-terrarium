import pytest

from tools.seqrender import Message, Note, SeqParseError, labels, parse, render

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
