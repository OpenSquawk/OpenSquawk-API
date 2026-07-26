"""The nine reports that could not be confirmed from the code alone.

Each was reproduced against the live flows before deciding anything. Most turned
out to behave correctly already; the ones that did not were fixed in their own
commits and are pinned here so the reported symptom cannot come back.

The verdict for each is recorded in its test, including the cases where the
answer is "this is correct radiotelephony and stays as it is" — that reasoning
is worth keeping next to the behaviour it justifies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import session_store
from app.decision_engine import process_transmission
from app.flow_loader import get_flow, load_all_flows
from app.models import DecisionRequest
from app.readback_evaluator import _match_readback_value
from app.session_store import create_session

FLOWS_DIR = Path(__file__).parent.parent / "flows"


@pytest.fixture(autouse=True)
def load_flows():
    load_all_flows(FLOWS_DIR)
    session_store._sessions.clear()


def _say(resp) -> str:
    return (resp.controller_say_rendered or "").lower()


# ---------------------------------------------------------------------------
# 1 + 2 — "the initial call / startup readback is not accepted"
# ---------------------------------------------------------------------------
# Both are accepted. What the reports describe is the *silence* that follows a
# startup readback, which is deliberate: the pilot requests pushback next, so
# they are not waiting on anything and a controller would say nothing. See
# docs/readback-acknowledgement-policy.md. The readback is still graded, and the
# per-field result is on the response for the UI to show.

def test_a_complete_initial_call_is_accepted_first_time():
    session = create_session(get_flow("clearance"))
    resp = process_transmission(session.session_id, DecisionRequest(
        pilot_utterance=(
            "DLH39A, information K, IFR to Munich, stand A12, request clearance"
        ),
    ))
    assert resp.next_state_id == "PILOT_READBACK"
    assert "say again" not in _say(resp)


def test_the_startup_readback_is_accepted_and_graded():
    session = create_session(get_flow("taxi"), variable_overrides={
        "callsign": "DLH39A", "qnh": "1013",
    })
    process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="DLH39A request startup",
    ))
    resp = process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="startup approved QNH 1013, DLH39A",
    ))
    assert resp.next_state_id == "REQUEST_PUSHBACK", "the readback was not accepted"
    # Accepted *and* graded — the silence is a phraseology choice, not a shrug.
    assert [r for r in resp.readback_report if r.field == "qnh" and r.matched]


def test_a_wrong_startup_readback_is_still_rejected():
    session = create_session(get_flow("taxi"), variable_overrides={
        "callsign": "DLH39A", "qnh": "1013",
    })
    process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="DLH39A request startup",
    ))
    resp = process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="startup approved QNH 1008, DLH39A",
    ))
    assert resp.next_state_id == "PILOT_STARTUP_READBACK"


# ---------------------------------------------------------------------------
# 5 — "the takeoff readback asks for the runway again after line up and wait"
# ---------------------------------------------------------------------------
# Correct as it stands. ICAO PANS-ATM makes the runway a mandatory readback item
# on a takeoff clearance in its own right; line-up and takeoff are two separate
# clearances and each is read back in full. Pinned so it is not "tidied away".

def test_the_takeoff_clearance_readback_requires_the_runway():
    flow = get_flow("tower")
    assert "runway" in flow.states["PILOT_LINEUP_READBACK"].readback_required
    assert "runway" in flow.states["PILOT_TAKEOFF_READBACK"].readback_required


def test_a_takeoff_readback_without_the_runway_is_rejected():
    session = create_session(get_flow("tower"), variable_overrides={
        "callsign": "DLH39A", "runway": "25L", "force_rto": False,
    })
    process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="DLH39A holding short runway 25L ready for departure",
    ))
    process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="line up and wait runway 25L, DLH39A",
    ))
    resp = process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="cleared for takeoff, DLH39A",
    ))
    assert resp.next_state_id == "PILOT_TAKEOFF_READBACK"


# ---------------------------------------------------------------------------
# 6 — "the Lufthansa callsign is swallowed"
# ---------------------------------------------------------------------------
# Not reproducible in the text pipeline: the telephony name is produced and the
# matcher accepts every spoken form of it. Anything left is the TTS voice's
# articulation, which no test here can see.

@pytest.mark.parametrize("spoken", [
    "Lufthansa three nine alpha",
    "Lufthansa tree niner Alfa",
    "lufthansa 39 alpha",
    "delta lima hotel three nine alpha",
    "DLH39A",
])
def test_the_airline_callsign_is_recognised_however_it_is_spoken(spoken):
    matched, _via, _forms = _match_readback_value("DLH39A", spoken)
    assert matched, f"callsign not recognised in {spoken!r}"


# ---------------------------------------------------------------------------
# 7 — "expected 120.505, the system asks for 118.705"
# ---------------------------------------------------------------------------
# Correct as it stands. A field may publish several frequencies for one position
# (EDDM has two Tower frequencies) and the controller assigns one of them; the
# pilot uses the one assigned. What must not happen is a *different* frequency
# being accepted, and that holds.

@pytest.mark.parametrize("expected,spoken", [
    ("120.505", "going to 120.505, DLH39A"),
    ("120.505", "one two zero decimal five zero five, DLH39A"),
    ("120.505", "wun too zero decimal fife zero fife"),
    ("118.705", "118.705, DLH39A"),
    ("118.705", "one one eight decimal seven zero five, DLH39A"),
])
def test_a_three_decimal_frequency_reads_back_correctly(expected, spoken):
    matched, _via, _forms = _match_readback_value(expected, spoken)
    assert matched, f"{expected} not recognised in {spoken!r}"


@pytest.mark.parametrize("expected,spoken", [
    ("120.505", "118.705, DLH39A"),
    ("118.705", "120.505, DLH39A"),
    ("120.505", "120.5, DLH39A"),
])
def test_a_different_frequency_is_never_accepted(expected, spoken):
    matched, _via, _forms = _match_readback_value(expected, spoken)
    assert not matched, f"{spoken!r} wrongly accepted as {expected}"


# ---------------------------------------------------------------------------
# 4 + 8 — "no further reply after a frequency readback without a sign-off"
# ---------------------------------------------------------------------------
# Was real: four flows ended on the pilot's own frequency readback in silence.
# Every frequency readback is now answered by the controller's sign-off, whether
# or not the pilot said good day. Fixed with the acknowledgement policy.

@pytest.mark.parametrize("readback", [
    "121.800, DLH39A",
    "121.800, DLH39A, good day",
    "going to 121.800, thanks, DLH39A",
])
def test_a_frequency_readback_is_answered_with_or_without_a_farewell(readback):
    session = create_session(get_flow("clearance"), variable_overrides={
        "callsign": "DLH39A", "destination": "Munich", "sid": "BIBAX1N",
        "initial_altitude": "5000", "squawk": "2341", "ground_freq": "121.800",
    })
    process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="DLH39A, information K, IFR to Munich, stand A12, request clearance",
    ))
    process_transmission(session.session_id, DecisionRequest(
        pilot_utterance=(
            "cleared to Munich via BIBAX1N, climb initially 5000 feet, squawk 2341, DLH39A"
        ),
    ))
    resp = process_transmission(
        session.session_id, DecisionRequest(pilot_utterance=readback)
    )
    assert _say(resp), f"no reply at all to {readback!r}"
    assert "good day" in _say(resp), _say(resp)


# ---------------------------------------------------------------------------
# 9 — "the RTO flow reports a wrong frequency after line-up"
# ---------------------------------------------------------------------------
# Not reproducible: the drill never names a frequency at all between line-up and
# the cancelled takeoff, and it never hands off — the aircraft stays with Tower.

def test_the_rto_drill_names_no_frequency_before_the_cancellation():
    session = create_session(get_flow("rto"), variable_overrides={
        "callsign": "DLH39A", "runway": "25L",
    })
    spoken = []
    for utterance in [
        "DLH39A holding short runway 25L ready for departure",
        "line up and wait runway 25L, DLH39A",
        "cleared for takeoff runway 25L, DLH39A",
    ]:
        resp = process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=utterance)
        )
        spoken.append(_say(resp))

    joined = " ".join(spoken)
    assert "cancel take-off" in joined, joined
    assert "contact" not in joined, f"the drill handed the pilot off: {joined}"
    assert "decimal" not in joined and "1" not in joined.replace("25l", ""), joined


def test_the_rto_drill_never_chains_to_another_flow():
    flow = get_flow("rto")
    assert flow.next_flow is None, (
        "a rejected takeoff leaves the aircraft stopped on the runway; "
        "chaining onward would hand it to Departure"
    )
