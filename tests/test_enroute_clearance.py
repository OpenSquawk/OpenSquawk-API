"""The departure clearance grants the enroute route, and grades it.

Before this, `clearance-v1` issued the SID and stopped there: the pilot was
never told where the SID hands over to the enroute structure, and the readback
had nothing to grade. Only the first enroute fix is granted and required — a
filed route runs to ten-plus elements including airways, which no readback
covers and no transcription survives.

A flight without a filed route (demo, manual) must keep the wording it has
today, so those two paths are pinned against each other here.
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

# The wording a routeless flight has today, and must keep.
NO_ROUTE_CLEARANCE = (
    "DLH39A, cleared to Munich via TOBAK2E departure, "
    "climb initially 5000 feet, squawk 2341"
)


@pytest.fixture(autouse=True)
def load_flows():
    load_all_flows(FLOWS_DIR)
    session_store._sessions.clear()


def _clearance(route: str = ""):
    """A session parked on PILOT_READBACK, plus what the controller said.

    The SID ATC assigns is generated independently of the filed route, so the
    two deliberately disagree here — that is the case in which grading the fix
    means anything.
    """
    session = create_session(
        get_flow("clearance"),
        variable_overrides={
            "callsign": "DLH39A",
            "destination": "Munich",
            "sid": "TOBAK2E",
            "initial_altitude": "5000",
            "squawk": "2341",
            "route": route,
        },
    )
    resp = process_transmission(
        session.session_id, DecisionRequest(pilot_utterance="request clearance")
    )
    assert resp.next_state_id == "PILOT_READBACK"
    return session, resp.controller_say_rendered


def _readback(session, utterance):
    return process_transmission(
        session.session_id, DecisionRequest(pilot_utterance=utterance)
    )


def _was_accepted(resp) -> bool:
    return resp.next_state_id == "PILOT_GROUND_FREQ_READBACK"


# ---------------------------------------------------------------------------
# The route is granted
# ---------------------------------------------------------------------------


def test_clearance_names_the_first_enroute_fix():
    _, said = _clearance("SULUS5S SULUS Y101 ARMUT UM984 NATOR")
    assert said == (
        "DLH39A, cleared to Munich via TOBAK2E departure, then SULUS, "
        "flight planned route, climb initially 5000 feet, squawk 2341"
    )


def test_clearance_does_not_recite_the_rest_of_the_route():
    _, said = _clearance("SULUS5S SULUS Y101 ARMUT UM984 NATOR")
    for beyond in ("Y101", "ARMUT", "UM984", "NATOR"):
        assert beyond not in said, f"{beyond} should be covered by 'flight planned route'"


def test_without_a_route_the_wording_is_unchanged():
    _, said = _clearance("")
    assert said == NO_ROUTE_CLEARANCE


# ---------------------------------------------------------------------------
# The route is graded
# ---------------------------------------------------------------------------


def test_readback_including_the_fix_is_accepted():
    session, _ = _clearance("SULUS5S SULUS Y101 ARMUT")
    resp = _readback(
        session,
        "Cleared to Munich via TOBAK2E departure, then SULUS, flight planned route, "
        "climb initially 5000 feet, squawk 2341, DLH39A",
    )
    assert _was_accepted(resp), f"report={resp.readback_report}"


def test_readback_omitting_the_fix_is_rejected():
    session, _ = _clearance("SULUS5S SULUS Y101 ARMUT")
    resp = _readback(
        session,
        "Cleared to Munich via TOBAK2E departure, climb initially 5000 feet, "
        "squawk 2341, DLH39A",
    )
    assert not _was_accepted(resp), f"report={resp.readback_report}"
    assert resp.next_state_id == "PILOT_READBACK"


def test_readback_with_the_wrong_waypoint_is_rejected():
    session, _ = _clearance("SULUS5S SULUS Y101 ARMUT")
    resp = _readback(
        session,
        "Cleared to Munich via TOBAK2E departure, then ARMUT, flight planned route, "
        "climb initially 5000 feet, squawk 2341, DLH39A",
    )
    assert not _was_accepted(resp), (
        f"a fix further down the route is not the one that was cleared; "
        f"report={resp.readback_report}"
    )


def test_the_correction_repeats_the_fix_the_pilot_has_to_say():
    """A correction that omits the failing item leaves the pilot stuck."""
    session, _ = _clearance("SULUS5S SULUS Y101 ARMUT")
    resp = _readback(
        session,
        "Cleared to Munich via TOBAK2E departure, climb initially 5000 feet, "
        "squawk 2341, DLH39A",
    )
    assert "SULUS" in (resp.controller_say_rendered or "")


# ---------------------------------------------------------------------------
# A routeless flight is graded exactly as before
# ---------------------------------------------------------------------------


def test_without_a_route_the_readback_needs_no_fix():
    session, _ = _clearance("")
    resp = _readback(
        session,
        "Cleared to Munich via TOBAK2E departure, climb initially 5000 feet, "
        "squawk 2341, DLH39A",
    )
    assert _was_accepted(resp), (
        f"a flight that filed no route must not be asked for one; "
        f"report={resp.readback_report}"
    )


def test_without_a_route_the_correction_is_unchanged():
    session, _ = _clearance("")
    resp = _readback(session, "squawk 2341, DLH39A")
    assert resp.controller_say_rendered == (
        "Negative, DLH39A, say again: TOBAK2E, squawk 2341, climb initially 5000 feet"
    )
