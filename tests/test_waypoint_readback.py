"""Word-pronounced waypoints must survive speech-to-text.

ICAO five-letter name-codes (BIBAX, SULUS, ANEKI) are built to be pronounceable
and are spoken as words on the radio — never spelled out. Whisper therefore
returns whatever English it heard: "Bibaks", "bee backs", "Sullus". The matcher
only accepted the exact spelling or a full phonetic spell-out, so a pilot who
read the waypoint back correctly was graded as wrong.

The rescue is the same shape as the SID one: a fuzzy stem match. Its safety
rests on ICAO name-codes being deliberately distinct from each other, so a
*different* waypoint must still fail — that is what the negative cases pin.
"""

from __future__ import annotations

import pytest

from app.readback_evaluator import _match_readback_value


def _matched(expected: str, utterance: str) -> bool:
    return _match_readback_value(expected, utterance)[0]


# ---------------------------------------------------------------------------
# Positive — the waypoint was read back, however STT spelled it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expected,utterance", [
    # Clean transcription.
    ("BIBAX", "climb flight level 150, direct BIBAX, DLH39A"),
    ("BIBAX", "direct Bibax, Lufthansa three nine alpha"),
    # Spelled out phonetically (a pilot may still do this).
    ("BIBAX", "direct bravo india bravo alfa x-ray, DLH39A"),
    # Realistic Whisper manglings of the spoken word.
    ("BIBAX", "direct Bibaks, DLH39A"),
    ("BIBAX", "direct bee backs, DLH39A"),
    ("BIBAX", "direct by backs, DLH39A"),
    ("BIBAX", "direct Bibex, DLH39A"),
    # A second waypoint, same treatment.
    ("SULUS", "direct SULUS, DLH39A"),
    ("SULUS", "direct Sullus, DLH39A"),
    ("SULUS", "direct Suluss, DLH39A"),
    ("SULUS", "direct soulless, DLH39A"),
    # A third.
    ("ANEKI", "direct ANEKI, DLH39A"),
    ("ANEKI", "direct Anneki, DLH39A"),
    ("ANEKI", "direct Anecki, DLH39A"),
    ("ANEKI", "direct a necky, DLH39A"),
])
def test_spoken_waypoint_is_recognised(expected, utterance):
    assert _matched(expected, utterance), (
        f"{expected} not recognised in {utterance!r}"
    )


# ---------------------------------------------------------------------------
# Negative — a different waypoint, or none at all, must not pass
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expected,utterance", [
    # A different waypoint entirely.
    ("BIBAX", "direct NAPSA, DLH39A"),
    ("BIBAX", "direct TOBAK, DLH39A"),
    ("BIBAX", "direct SULUS, DLH39A"),
    ("SULUS", "direct ANEKI, DLH39A"),
    ("ANEKI", "direct BIBAX, DLH39A"),
    # Similar-looking but genuinely different name-codes.
    ("TOBAK", "direct OBOKA, DLH39A"),
    ("OBOKA", "direct TOBAK, DLH39A"),
    # The waypoint was simply not read back.
    ("BIBAX", "climb flight level 150, DLH39A"),
    ("SULUS", "roger, DLH39A"),
    # Ordinary radiotelephony must never stand in for a waypoint name: RIDAR
    # sounds exactly like "radar", which appears in "radar contact".
    ("RIDAR", "radar contact, climb flight level 150, DLH39A"),
])
def test_wrong_or_absent_waypoint_is_rejected(expected, utterance):
    assert not _matched(expected, utterance), (
        f"{expected} wrongly accepted from {utterance!r}"
    )
