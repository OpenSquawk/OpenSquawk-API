"""Spoken forms must survive the punctuation speech-to-text inserts.

Whisper routinely hyphenates spoken numbers ("one-hundred", "two-five-left")
and inserts commas mid-phrase. Matching the spoken form as an escaped literal
required exactly one space between words, so a hyphen made a correct readback
grade as wrong — which is what "flight level one hundred is not understood"
turned out to be.
"""

from __future__ import annotations

import pytest

from app.readback_evaluator import evaluate_readback_simple


def _matches(utterance: str, value: str, field: str = "level") -> bool:
    passed, _missing, _reports = evaluate_readback_simple(
        utterance, [field], {field: value}
    )
    return passed


@pytest.mark.parametrize("utterance", [
    "descend flight level one hundred, DLH39A",
    "descend flight level one-hundred, DLH39A",
    "descend flight level one—hundred, DLH39A",
    "descend flight level one, hundred, DLH39A",
    "descend flight level wun hundred, DLH39A",
    "descend flight level one zero zero, DLH39A",
    "descend FL100, DLH39A",
    "descend flight level 100, DLH39A",
])
def test_flight_level_one_hundred_is_accepted(utterance):
    assert _matches(utterance, "FL100"), utterance


@pytest.mark.parametrize("utterance", [
    "climb flight level one five zero, DLH39A",
    "climb flight level one-five-zero, DLH39A",
    "climb flight level wun fife zero, DLH39A",
])
def test_hyphenated_digit_groups_are_accepted(utterance):
    assert _matches(utterance, "FL150"), utterance


@pytest.mark.parametrize("utterance", [
    "runway two five left, DLH39A",
    "runway two-five-left, DLH39A",
])
def test_hyphenated_runway_is_accepted(utterance):
    assert _matches(utterance, "25L", field="runway"), utterance


@pytest.mark.parametrize("utterance", [
    "climb five thousand feet, DLH39A",
    "climb five-thousand feet, DLH39A",
])
def test_hyphenated_altitude_is_accepted(utterance):
    assert _matches(utterance, "5000", field="initial_altitude"), utterance


def test_a_wrong_level_is_still_rejected():
    """The separator tolerance must not turn into matching anything."""
    assert not _matches("descend flight level two hundred, DLH39A", "FL100")
    assert not _matches("descend flight level one one zero, DLH39A", "FL100")


def test_a_wrong_runway_is_still_rejected():
    assert not _matches("runway two-five-right, DLH39A", "25L", field="runway")
