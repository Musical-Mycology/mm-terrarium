"""The breath Control generates now that aurora no longer breathes itself.
Pure and offline: no luxaeterna, no Arco, no network."""

from __future__ import annotations

from control.breath import BREATH_CC, breath_cc


def test_breath_cc_is_midi_expression():
    # cc:11 is General MIDI Expression, which FluidSynth honors as a direct
    # attenuation, so the swell is audible on any soundfont.
    assert BREATH_CC == 11


def test_breath_cc_matches_auroras_own_envelope_at_the_knots():
    # Control now generates the breath aurora used to generate itself. These
    # are luxaeterna's _AURORA_BREATHE points scaled to 7-bit, so the light
    # looks identical to before: same period, same floor, never dark.
    assert breath_cc(0.0) == 70          # round(0.55 * 127)
    assert breath_cc(3.0) == 127
    assert breath_cc(6.0) == 70          # loops back to the start


def test_breath_cc_interpolates_between_the_knots():
    assert breath_cc(1.5) == 98          # round(0.775 * 127)


def test_breath_cc_rises_monotonically_over_the_first_half():
    vals = [breath_cc(t / 10.0) for t in range(0, 31)]
    assert vals == sorted(vals)


def test_breath_cc_falls_over_the_second_half():
    vals = [breath_cc(3.0 + t / 10.0) for t in range(0, 31)]
    assert vals == sorted(vals, reverse=True)


def test_breath_cc_never_reaches_zero():
    assert min(breath_cc(t / 10.0) for t in range(0, 61)) >= 70


def test_breath_cc_loops_rather_than_running_off_the_end():
    assert breath_cc(13.5) == breath_cc(1.5)
