"""A departure clearance readback must cover every safety-critical item.

ICAO Annex 10 / CAP 413 require the pilot to read back the *clearance limit*
(destination), the departure procedure, the initial level and the squawk. The
flow only graded SID, squawk and initial altitude, so a pilot could read back a
completely different destination — or route — and still be told "readback
correct".

These tests pin the whole set: correct readbacks pass, and each individually
wrong item fails. The negative cases matter most — they are what a
prompt-biased or too-lenient matcher gets wrong.
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


@pytest.fixture(autouse=True)
def load_flows():
    load_all_flows(FLOWS_DIR)
    session_store._sessions.clear()


@pytest.fixture
def at_readback():
    """A clearance session parked on PILOT_READBACK with a known clearance."""
    session = create_session(get_flow("clearance"))
    session.variables.update({
        "callsign": "DLH39A",
        "destination": "Munich",
        "sid": "BIBAX1N",
        "initial_altitude": "5000",
        "squawk": "2341",
    })
    session_store.save_session(session)
    resp = process_transmission(
        session.session_id, DecisionRequest(pilot_utterance="request clearance")
    )
    assert resp.next_state_id == "PILOT_READBACK"
    return session


def _readback(session, utterance):
    return process_transmission(
        session.session_id, DecisionRequest(pilot_utterance=utterance)
    )


def _was_accepted(resp) -> bool:
    """True when ATC graded the readback as correct and released the pilot.

    The engine auto-advances through ATC states, so a rejected readback comes
    back resting on PILOT_READBACK again (having spoken the correction), while
    an accepted one lands on the ground-frequency readback.
    """
    return resp.next_state_id == "PILOT_GROUND_FREQ_READBACK"


# ---------------------------------------------------------------------------
# Positive — a complete, correct readback is accepted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "Cleared to Munich via BIBAX1N departure, climb initially 5000 feet, squawk 2341, DLH39A",
    "cleared to munich via bibax one november, climbing five thousand feet, squawk two tree four one, Lufthansa three nine alpha",
    "Munich via Bibax 1 November, 5000 feet, squawk 2341, DLH39A",
])
def test_complete_readback_is_accepted(at_readback, utterance):
    resp = _readback(at_readback, utterance)
    assert _was_accepted(resp), (
        f"correct readback rejected; state={resp.next_state_id} "
        f"report={resp.readback_report}"
    )


# ---------------------------------------------------------------------------
# Negative — every safety-critical item is graded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance,what", [
    # Wrong clearance limit — everything else verbatim correct.
    ("Cleared to Frankfurt via BIBAX1N departure, climb initially 5000 feet, squawk 2341, DLH39A",
     "wrong destination"),
    # Wrong departure procedure.
    ("Cleared to Munich via ANEKI7S departure, climb initially 5000 feet, squawk 2341, DLH39A",
     "wrong SID"),
    # Wrong initial level.
    ("Cleared to Munich via BIBAX1N departure, climb initially 6000 feet, squawk 2341, DLH39A",
     "wrong initial altitude"),
    # Wrong squawk.
    ("Cleared to Munich via BIBAX1N departure, climb initially 5000 feet, squawk 2431, DLH39A",
     "wrong squawk"),
    # Only fragments of the clearance.
    ("squawk 2341, DLH39A", "partial readback — squawk only"),
    ("Cleared to Munich, DLH39A", "partial readback — destination only"),
])
def test_incorrect_readback_is_rejected(at_readback, utterance, what):
    resp = _readback(at_readback, utterance)
    assert not _was_accepted(resp), (
        f"{what} was accepted as a correct readback; report={resp.readback_report}"
    )
    assert resp.next_state_id == "PILOT_READBACK", (
        f"{what} should loop back for another readback attempt, "
        f"got {resp.next_state_id}"
    )


# ---------------------------------------------------------------------------
# The destination may be read back by code or by name, in either language
# ---------------------------------------------------------------------------

def _at_readback_with_destination(destination: str):
    session = create_session(get_flow("clearance"))
    session.variables.update({
        "callsign": "DLH39A",
        "destination": destination,
        "sid": "BIBAX1N",
        "initial_altitude": "5000",
        "squawk": "2341",
    })
    session_store.save_session(session)
    process_transmission(
        session.session_id, DecisionRequest(pilot_utterance="request clearance")
    )
    return session


@pytest.mark.parametrize("destination,spoken", [
    # ATC issued the ICAO code — city name in either language reads it back.
    ("EDDM", "Munich"),
    ("EDDM", "München"),
    ("EDDM", "EDDM"),
    # ATC issued the city — the German name is the same airport.
    ("Munich", "München"),
    ("Munich", "Munich"),
    # Multi-word city names resolve too.
    ("EDDF", "Frankfurt"),
])
def test_destination_accepts_equivalent_names(destination, spoken):
    session = _at_readback_with_destination(destination)
    resp = _readback(
        session,
        f"Cleared to {spoken} via BIBAX1N departure, climb initially 5000 feet, squawk 2341, DLH39A",
    )
    assert _was_accepted(resp), (
        f"{spoken!r} should read back destination {destination!r}; "
        f"report={resp.readback_report}"
    )


@pytest.mark.parametrize("destination,spoken", [
    ("EDDM", "Frankfurt"),
    ("Munich", "Hamburg"),
    ("EDDF", "EDDM"),
])
def test_destination_rejects_a_different_airport(destination, spoken):
    session = _at_readback_with_destination(destination)
    resp = _readback(
        session,
        f"Cleared to {spoken} via BIBAX1N departure, climb initially 5000 feet, squawk 2341, DLH39A",
    )
    assert not _was_accepted(resp), (
        f"{spoken!r} must not read back destination {destination!r}; "
        f"report={resp.readback_report}"
    )


def test_expected_phrase_shape_alone_does_not_pass(at_readback):
    """Right sentence structure, wrong values — must not be rescued by bias.

    The utterance mirrors ``expected_pilot_template`` word for word and only
    swaps the values. Any matcher that grades on sentence shape (or an LLM
    nudged by the expected phrase) accepts this; a value matcher rejects it.
    """
    resp = _readback(
        at_readback,
        "Cleared Hamburg, TOBAK2E, climb initially 7000, squawk 5510, DLH39A",
    )
    assert not _was_accepted(resp), (
        f"structurally-correct but factually wrong readback accepted; "
        f"report={resp.readback_report}"
    )
