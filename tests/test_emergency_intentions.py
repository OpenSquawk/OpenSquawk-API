"""The emergency flow remembers what the pilot said and then does something.

Two reported problems, both structural:

- ATC asked "what are your intentions?" even when the MAYDAY call had already
  said them. That call is consumed by the global emergency intercept, so the
  flow never saw it.
- Whatever the pilot answered, a single catch-all transition ran straight to
  the end state. No vectors, no level, no plan — the emergency was "handled" by
  ending it.
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


def _declare(mayday_call: str):
    """Start a tower session and declare an emergency with the given call."""
    session = create_session(get_flow("tower"))
    session.variables.update({"callsign": "DLH39A", "runway": "25L", "qnh": "1013"})
    session_store.save_session(session)
    resp = process_transmission(
        session.session_id, DecisionRequest(pilot_utterance=mayday_call)
    )
    assert resp.active_flow == "emergency-v1", resp.active_flow
    return session, resp


def _say(resp) -> str:
    return (resp.controller_say_rendered or "").lower()


class TestIntentionsAreRemembered:
    def test_intention_in_the_mayday_call_is_not_asked_for_again(self):
        session, _ = _declare(
            "Mayday mayday mayday, DLH39A, engine failure, returning to the field"
        )
        # The details call says nothing about intentions — the flow must still
        # know them from the MAYDAY.
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="engine failure, 148 persons on board, 3 hours of fuel",
        ))
        said = _say(resp)
        assert "say your intentions" not in said, (
            f"asked for an intention already stated in the MAYDAY call: {said!r}"
        )
        assert "cleared to return" in said, said

    @pytest.mark.parametrize("call,expected", [
        ("Mayday mayday mayday, DLH39A, engine fire, we are diverting to the nearest airport",
         "cleared to divert"),
        ("Mayday mayday mayday, DLH39A, hydraulic failure, we need time to hold and run the checklist",
         "hold present position"),
        ("Mayday mayday mayday, DLH39A, medical emergency, able to continue as cleared",
         "continue as cleared"),
    ])
    def test_each_intention_gets_its_own_instruction(self, call, expected):
        session, _ = _declare(call)
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="as reported, 148 persons on board, 3 hours of fuel",
        ))
        assert expected in _say(resp), _say(resp)

    def test_a_mayday_without_intentions_is_asked(self):
        session, _ = _declare("Mayday mayday mayday, DLH39A, engine failure")
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="engine failure, 148 persons on board, 3 hours of fuel",
        ))
        assert "say your intentions" in _say(resp), _say(resp)
        assert resp.next_state_id == "PILOT_INTENTIONS"

    def test_intentions_stated_in_the_details_call_are_honoured(self):
        session, _ = _declare("Mayday mayday mayday, DLH39A, engine failure")
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance=(
                "engine failure, 148 persons on board, 3 hours of fuel, "
                "request return to the field"
            ),
        ))
        assert "cleared to return" in _say(resp), _say(resp)

    def test_a_new_emergency_does_not_inherit_the_previous_intention(self):
        session, _ = _declare(
            "Mayday mayday mayday, DLH39A, engine failure, returning to the field"
        )
        assert session_store.get_session(session.session_id).variables[
            "emergency_intention"] == "return"

        stored = session_store.get_session(session.session_id)
        stored.variables["_emergency_active"] = False
        stored.active_flow = "tower-v1"
        stored.current_state = "INITIAL_CONTACT"
        stored.flow_stack = []
        session_store.save_session(stored)

        process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="Mayday mayday mayday, DLH39A, smoke in the cabin",
        ))
        assert session_store.get_session(session.session_id).variables[
            "emergency_intention"] == "", "a stale intention leaked into a new emergency"


class TestTheFlowActuallyHandlesTheEmergency:
    def test_a_return_gets_a_level_and_an_approach_to_read_back(self):
        session, _ = _declare(
            "Mayday mayday mayday, DLH39A, engine failure, returning to the field"
        )
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="engine failure, 148 persons on board, 3 hours of fuel",
        ))
        said = _say(resp)
        assert "3000" in said and "25l" in said.replace("two five left", "25l"), said
        assert resp.next_state_id == "PILOT_APPROACH_READBACK"

        # And that clearance is graded like any other.
        wrong = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="descend altitude 5000 feet, runway 25 right, DLH39A",
        ))
        assert wrong.next_state_id == "PILOT_APPROACH_READBACK", "wrong readback accepted"

        good = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="descend altitude 3000 feet, QNH 1013, approach runway 25L, DLH39A",
        ))
        assert "readback correct" in _say(good), _say(good)

    def test_unclear_intentions_are_asked_again_rather_than_ending_the_flow(self):
        session, _ = _declare("Mayday mayday mayday, DLH39A, engine failure")
        process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="engine failure, 148 persons on board, 3 hours of fuel",
        ))
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="standby",
        ))
        assert resp.next_state_id == "PILOT_INTENTIONS", resp.next_state_id
        assert "say again your intentions" in _say(resp), _say(resp)


def test_no_catch_all_runs_straight_to_the_end_state():
    """Structural: the end state is reached through a handled plan, not a shrug."""
    flow = get_flow("emergency")
    for state_id, state in flow.states.items():
        for transition in state.ok_next:
            if transition.to in flow.end_states:
                assert state.role != "pilot", (
                    f"{state_id} routes a pilot utterance straight to the end state — "
                    f"the emergency must be handled, not just terminated"
                )


# ---------------------------------------------------------------------------
# Vectors — a controller turning an aircraft back gives a heading
# ---------------------------------------------------------------------------
# Everything needed is already on the session: the bridge reports the aircraft's
# position and the bundled dataset has the airport's, so the course between them
# is the heading to fly. Without a bridge there is no position and therefore no
# honest vector, and the flow says so by taking its non-vectored branch.

class TestEmergencyVectors:
    def test_bearing_matches_known_geometry(self):
        from app.decision_engine import _initial_bearing_deg
        # Due north, east, south and west of a reference point.
        assert round(_initial_bearing_deg(48.0, 11.0, 49.0, 11.0)) == 0
        assert round(_initial_bearing_deg(48.0, 11.0, 48.0, 12.0)) == 90
        assert round(_initial_bearing_deg(48.0, 11.0, 47.0, 11.0)) == 180
        assert round(_initial_bearing_deg(48.0, 11.0, 48.0, 10.0)) == 270

    def _returning_with_position(self, lat, lon):
        session, _ = _declare(
            "Mayday mayday mayday, DLH39A, engine failure, returning to the field"
        )
        stored = session_store.get_session(session.session_id)
        stored.airport_icao = "EDDM"
        stored.telemetry.update({"lat": lat, "lon": lon})
        session_store.save_session(stored)
        return process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="engine failure, 148 persons on board, 3 hours of fuel",
        ))

    def test_a_bridged_session_is_given_a_heading_and_a_distance(self):
        # North of EDDM (48.354 / 11.786), so the turn back is roughly south.
        resp = self._returning_with_position(48.90, 11.786)
        said = _say(resp)
        assert "turn heading 180" in said, said
        assert "miles" in said, said

    def test_the_heading_follows_the_aircraft_position(self):
        # West of the field — the vector back points east.
        resp = self._returning_with_position(48.354, 10.90)
        assert "turn heading 090" in _say(resp), _say(resp)

    def test_without_a_bridge_no_heading_is_invented(self):
        session, _ = _declare(
            "Mayday mayday mayday, DLH39A, engine failure, returning to the field"
        )
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="engine failure, 148 persons on board, 3 hours of fuel",
        ))
        said = _say(resp)
        assert "cleared to return" in said, said
        assert "turn heading" not in said, said
        # And nothing renders as an unresolved placeholder.
        assert "[" not in said, said

    def test_a_stale_position_of_zero_zero_is_not_a_position(self):
        resp = self._returning_with_position(0.0, 0.0)
        said = _say(resp)
        assert "turn heading" not in said, said
        assert "[" not in said, said
