"""A pilot who says "correction" must be graded on the corrected value.

"CORRECTION" is standard phraseology for withdrawing what was just said. The
matcher searched the whole transmission for each expected value, so the
withdrawn half still counted: a pilot who read back the right squawk, said
"correction", and then read back a *different* one was told "readback correct".
That is a false positive on a safety-critical item, and it is the shape of the
report that only the corrected version should be evaluated.

The rule implemented is the phraseological one: everything after the last
correction marker supersedes what was said before it — but only for the item
the correction actually restates. Correcting the squawk must not invalidate an
altitude that was read back correctly earlier in the same transmission.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import session_store
from app.decision_engine import process_transmission
from app.flow_loader import get_flow, load_all_flows
from app.models import DecisionRequest
from app.readback_evaluator import check_readback
from app.session_store import create_session

FLOWS_DIR = Path(__file__).parent.parent / "flows"

VARIABLES = {
    "callsign": "DLH39A",
    "destination": "Munich",
    "sid": "BIBAX1N",
    "initial_altitude": "5000",
    "squawk": "2341",
}
REQUIRED = ["destination", "sid", "initial_altitude", "squawk"]


def _graded(utterance: str) -> bool:
    passed, _missing, _reports = check_readback(
        utterance, REQUIRED, "simple", VARIABLES
    )
    return passed


# ---------------------------------------------------------------------------
# The corrected value is the one that counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    # Wrong first, corrected to the right value — the classic case.
    "cleared to Munich via BIBAX1N, climb 5000 feet, squawk 2431, correction, squawk 2341",
    "cleared to Munich via BIBAX1N, climb 5000 feet, squawk 2431, negative, correction, squawk 2341",
    # Correcting a different item leaves the others standing.
    "cleared to Munich via BIBAX1N, climb 6000, correction, climb 5000 feet, squawk 2341",
    # Correction of the SID.
    "cleared to Munich via ANEKI7S, correction, via BIBAX1N, climb 5000 feet, squawk 2341",
    # No correction at all — unchanged behaviour.
    "cleared to Munich via BIBAX1N, climb 5000 feet, squawk 2341",
])
def test_corrected_readback_is_accepted(utterance):
    assert _graded(utterance), f"correct after correction was rejected: {utterance!r}"


@pytest.mark.parametrize("utterance,what", [
    # Right value first, then corrected to a wrong one — must NOT pass.
    ("cleared to Munich via BIBAX1N, climb 5000 feet, squawk 2341, correction, squawk 2431",
     "squawk corrected to a wrong value"),
    ("cleared to Munich via BIBAX1N, climb 5000 feet, squawk 2341, negative, correction, squawk 7000",
     "squawk corrected away after 'negative'"),
    ("cleared to Munich via BIBAX1N, climb 5000, correction, climb 6000 feet, squawk 2341",
     "altitude corrected to a wrong value"),
    ("cleared to Munich via BIBAX1N, correction, via ANEKI7S, climb 5000 feet, squawk 2341",
     "SID corrected to a wrong value"),
])
def test_superseded_value_no_longer_counts(utterance, what):
    assert not _graded(utterance), f"{what} was still accepted: {utterance!r}"


def test_correction_of_one_item_leaves_the_others_alone():
    """Correcting the squawk must not invalidate a correct altitude."""
    passed, missing, _ = check_readback(
        "cleared to Munich via BIBAX1N, climb 5000 feet, squawk 2341, correction, squawk 2431",
        REQUIRED, "simple", VARIABLES,
    )
    assert not passed
    assert missing == ["squawk"], (
        f"only the corrected item should fail, got {missing}"
    )


# ---------------------------------------------------------------------------
# A correction sent as the next transmission
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def load_flows():
    load_all_flows(FLOWS_DIR)
    session_store._sessions.clear()


def _at_readback():
    session = create_session(get_flow("clearance"))
    session.variables.update(VARIABLES)
    session_store.save_session(session)
    process_transmission(
        session.session_id, DecisionRequest(pilot_utterance="request clearance")
    )
    return session


def test_correction_as_the_following_transmission_is_accepted():
    """Wrong readback, then a standalone "correction, ..." with the right values."""
    session = _at_readback()
    first = process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="cleared to Munich via BIBAX1N, climb 5000 feet, squawk 2431",
    ))
    assert first.next_state_id == "PILOT_READBACK", "wrong squawk should be rejected"

    second = process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="correction, cleared to Munich via BIBAX1N, climb 5000 feet, squawk 2341",
    ))
    assert second.next_state_id == "PILOT_GROUND_FREQ_READBACK", (
        f"corrected readback rejected; report={second.readback_report}"
    )
