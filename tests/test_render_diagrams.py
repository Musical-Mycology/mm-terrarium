import pytest

from tools.render_diagrams import (
    ValidationError,
    d2_labels,
    source_labels,
    validate_d2_source,
)


def test_d2_labels_reads_node_and_edge_labels():
    text = 'a: Arco server\nb: Control\na -> b: /game/join\n'
    assert d2_labels(text) == ["Arco server", "Control", "/game/join"]


def test_d2_labels_strips_quotes():
    assert d2_labels('a -> b: "cc:74 -> hue"') == ["cc:74 -> hue"]


def test_d2_labels_strips_a_container_brace():
    assert d2_labels("box: Terrarium box {") == ["Terrarium box"]


def test_d2_labels_skips_styling_keywords():
    text = "a: Arco\na.shape: circle\na.style.fill: red\ndirection: down\n"
    assert d2_labels(text) == ["Arco"]


def test_d2_labels_skips_comments_and_blank_lines():
    assert d2_labels("# a comment\n\na: Arco\n") == ["Arco"]


def test_source_labels_dispatches_to_seqrender():
    got = source_labels("seq", "participants: A, B\nA -> B: /game/hello\n")
    assert got == ["/game/hello"]


def test_validate_rejects_a_newline_inside_a_label():
    """Measured to corrupt D2's box grid: spec section 1.2."""
    with pytest.raises(ValidationError) as exc:
        validate_d2_source("topology", 'arco: Arco server\\n(o2 hub)')
    assert "topology" in str(exc.value)
    assert "\\n" in str(exc.value)


def test_validate_rejects_sequence_diagram_shape():
    """D2's sequence renderer corrupts every slash-bearing label."""
    with pytest.raises(ValidationError) as exc:
        validate_d2_source("flow", "shape: sequence_diagram\na: A\n")
    assert "seqrender" in str(exc.value)


def test_validate_accepts_a_clean_source():
    validate_d2_source("topology", "a: Arco\na -> b: /game/join\n")
