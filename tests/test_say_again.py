"""A pilot asking for a repeat must get the last transmission again.

"Say again" is standard phraseology and can come at any point. Before this the
engine had no handler for it, so it fell through to trigger matching, failed,
and was graded as a wrong readback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import session_store
from app.decision_engine import process_transmission
from app.flow_loader import get_flow, load_all_flows
from app.models import DecisionRequest
from app.session_store import create_session

FLOWS_DIR = Path(__file__).parent.parent / "flows"

GOOD_INITIAL_CALL = "request clearance"


@pytest.fixture(autouse=True)
def load_flows():
    load_all_flows(FLOWS_DIR)
    session_store._sessions.clear()


@pytest.fixture
def clearance_session():
    return create_session(get_flow("clearance"))


def _advance_to_readback(session):
    """Drive the session to PILOT_READBACK, where ATC has issued the clearance."""
    resp = process_transmission(
        session.session_id, DecisionRequest(pilot_utterance=GOOD_INITIAL_CALL)
    )
    assert resp.next_state_id == "PILOT_READBACK"
    return resp


@pytest.mark.parametrize("utterance", [
    "say again",
    "Say again.",
    "please say again",
    "say again please",
    "DLH39A, say again",
    "sorry, say again the clearance",
    "repeat please",
    "could you repeat that",
    "say again all after cleared",
])
def test_say_again_repeats_the_last_transmission(clearance_session, utterance):
    issued = _advance_to_readback(clearance_session)

    resp = process_transmission(
        clearance_session.session_id, DecisionRequest(pilot_utterance=utterance)
    )

    # Stays put — the pilot still owes the readback.
    assert resp.next_state_id == "PILOT_READBACK"
    # And hears the clearance again, with the same values.
    assert resp.controller_say_rendered == issued.controller_say_rendered
    assert "2341" in (resp.controller_say_rendered or "")
    assert resp.fallback_used is False


def test_say_again_does_not_count_as_a_failed_readback(clearance_session):
    _advance_to_readback(clearance_session)
    resp = process_transmission(
        clearance_session.session_id, DecisionRequest(pilot_utterance="say again")
    )
    assert "readback_missing" not in (resp.fallback_reason or "")


def test_a_real_readback_still_wins_over_the_say_again_handler(clearance_session):
    """A correct readback is graded normally even if it mentions "again"."""
    _advance_to_readback(clearance_session)
    resp = process_transmission(
        clearance_session.session_id,
        DecisionRequest(
            pilot_utterance="cleared Munich BIBAX1N departure climb 5000 squawk 2341 DLH39A"
        ),
    )
    assert resp.next_state_id != "PILOT_READBACK"


def test_say_again_before_any_transmission_does_not_crash(clearance_session):
    """At the very first state there is nothing to repeat — must not blow up."""
    resp = process_transmission(
        clearance_session.session_id, DecisionRequest(pilot_utterance="say again")
    )
    assert resp.next_state_id is not None
