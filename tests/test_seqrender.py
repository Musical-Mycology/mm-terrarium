import pytest

from tools.seqrender import Message, Note, SeqParseError, labels, parse

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
