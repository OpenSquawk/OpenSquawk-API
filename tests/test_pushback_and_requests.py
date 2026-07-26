"""Requests the pilot makes that used to go unanswered.

Two reports, same shape: the crew asks for something in standard phraseology
and the system carries on as if nothing was said.

- "able to push facing north" — ground always read out the flow's own default
  facing, so offering one had no effect at all.
- "request backtrack" / "we require backtrack" — no flow had a transition for
  it, so it fell through to the correction prompt and the request itself was
  never answered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import session_store
from app.decision_engine import process_transmission
from app.flow_loader import get_flow, load_all_flows
from app.models import DecisionRequest
from app.pushback import assignable_facing, facing_is_workable, requested_facing
from app.session_store import create_session

FLOWS_DIR = Path(__file__).parent.parent / "flows"


@pytest.fixture(autouse=True)
def load_flows():
    load_all_flows(FLOWS_DIR)
    session_store._sessions.clear()


def _say(resp) -> str:
    return (resp.controller_say_rendered or "").lower()


class TestFacingHelpers:
    @pytest.mark.parametrize("utterance,expected", [
        ("able to push facing north", "north"),
        ("we can push facing south east", "southeast"),
        ("request pushback facing north-west", "northwest"),
        ("DLH39A, ready for push, face east", "east"),
        ("request pushback", None),
        ("holding short runway 25L", None),
    ])
    def test_requested_facing(self, utterance, expected):
        assert requested_facing(utterance) == expected

    @pytest.mark.parametrize("runway,facing", [
        ("25L", "west"),    # 250° → west
        ("07", "east"),     # 070° → east
        ("36", "north"),    # 360° → north
        ("18C", "south"),   # 180° → south
    ])
    def test_default_facing_follows_the_runway(self, runway, facing):
        assert assignable_facing(runway) == facing

    def test_a_facing_within_a_right_angle_of_the_runway_works(self):
        assert facing_is_workable("west", "25L")
        assert facing_is_workable("southwest", "25L")
        assert facing_is_workable("south", "25L")

    def test_a_facing_pointing_away_from_the_runway_does_not(self):
        assert not facing_is_workable("north", "25L")
        assert not facing_is_workable("east", "25L")

    def test_an_unknown_runway_yields_no_default(self):
        assert assignable_facing("") is None
        assert assignable_facing("XX") is None


class TestPushbackFacing:
    def _at_pushback(self, runway="25L"):
        session = create_session(get_flow("taxi"), variable_overrides={
            "callsign": "DLH39A", "runway": runway, "qnh": "1013",
        })
        process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="request startup",
        ))
        process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="startup approved, QNH 1013, DLH39A",
        ))
        return session

    def test_default_facing_comes_from_the_runway_not_a_fixed_compass_point(self):
        session = create_session(get_flow("taxi"), variable_overrides={"runway": "07"})
        assert session.variables["pushback_direction"] == "east"

    def test_a_workable_requested_facing_is_granted(self):
        session = self._at_pushback(runway="25L")
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="DLH39A, request pushback, able to push facing south",
        ))
        said = _say(resp)
        assert "pushback approved" in said and "south" in said, said
        assert "negative" not in said, said

    def test_an_unworkable_requested_facing_is_refused_out_loud(self):
        session = self._at_pushback(runway="25L")
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="DLH39A, request pushback, able to push facing north",
        ))
        said = _say(resp)
        assert "negative" in said and "unable facing north" in said, said
        # And ground says what it *is* assigning.
        assert "west" in said, said

    def test_a_wrong_facing_in_a_readback_does_not_rewrite_the_clearance(self):
        """A readback must never move the target it is graded against.

        The pilot is quoting a facing ATC already assigned. Treating that as a
        fresh request would let any wrong facing grade itself correct.
        """
        session = self._at_pushback(runway="25L")
        process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="DLH39A, request pushback",
        ))
        assert session_store.get_session(session.session_id).current_state == \
            "PILOT_PUSHBACK_READBACK"

        # "south" is workable for runway 25L — but it is not what was assigned.
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="pushback approved, face south, DLH39A",
        ))
        assert resp.next_state_id == "PILOT_PUSHBACK_READBACK", "wrong facing accepted"
        assert session_store.get_session(session.session_id).variables[
            "pushback_direction"] == "west"

    def test_no_request_leaves_the_runway_derived_facing_alone(self):
        session = self._at_pushback(runway="25L")
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="DLH39A, request pushback",
        ))
        said = _say(resp)
        assert "pushback approved" in said and "west" in said, said
        assert "negative" not in said, said


class TestBacktrackRequests:
    def _holding_short(self):
        session = create_session(get_flow("tower"), variable_overrides={
            "callsign": "DLH39A", "runway": "25L",
        })
        return session

    @pytest.mark.parametrize("utterance", [
        "DLH39A, request backtrack",
        "DLH39A, we require backtrack runway 25L",
        "DLH39A, request back track and line up",
        "requesting backtracking, DLH39A",
    ])
    def test_a_backtrack_request_is_answered(self, utterance):
        session = self._holding_short()
        resp = process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=utterance)
        )
        said = _say(resp)
        assert "backtrack" in said, f"request went unanswered: {said!r}"
        assert "25" in said or "two five" in said, said

    def test_the_request_does_not_move_the_training_state(self):
        session = self._holding_short()
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="DLH39A, request backtrack",
        ))
        assert resp.next_state_id == "INITIAL_CONTACT"

        # The pilot's normal call still works afterwards.
        follow = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="DLH39A, holding short runway 25L, ready for departure",
        ))
        assert follow.next_state_id == "PILOT_LINEUP_READBACK"

    def test_a_call_that_also_makes_the_normal_request_is_routed_normally(self):
        """Backtrack must not hijack a transmission the state was waiting for."""
        session = self._holding_short()
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance=(
                "DLH39A, holding short runway 25L, ready for departure, "
                "request backtrack"
            ),
        ))
        assert resp.next_state_id == "PILOT_LINEUP_READBACK", resp.next_state_id

    def test_while_taxiing_the_backtrack_is_acknowledged_for_later(self):
        session = create_session(get_flow("taxi"), variable_overrides={
            "callsign": "DLH39A", "runway": "25L",
        })
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="DLH39A, we require backtrack runway 25L",
        ))
        said = _say(resp)
        assert "expect backtrack" in said, said

    def test_once_cleared_for_takeoff_a_backtrack_no_longer_applies(self):
        session = self._holding_short()
        session.flags["takeoff_clearance_issued"] = True
        session_store.save_session(session)
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="DLH39A, request backtrack",
        ))
        assert "negative" in _say(resp), _say(resp)
