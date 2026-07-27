"""A rejected takeoff can happen when you are not expecting one.

Cancelling a takeoff only existed as its own drill, which the pilot starts
knowing what is coming — the opposite of the situation being practised. An
ordinary departure now carries a very small chance of Tower cancelling instead
of confirming the readback.

Nothing here rests on the dice. The probability is only exercised at 0 and 1,
and every behavioural test drives the branch through the explicit override, so
these cannot flake.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config, session_store
from app.decision_engine import process_transmission
from app.flow_loader import get_flow, load_all_flows
from app.models import DecisionRequest
from app.interventions import should_go_around, should_reject_takeoff
from app.session_store import create_session

FLOWS_DIR = Path(__file__).parent.parent / "flows"

GOOD_READBACK = "cleared for takeoff, runway 25L, DLH39A"


@pytest.fixture(autouse=True)
def load_flows():
    load_all_flows(FLOWS_DIR)
    session_store._sessions.clear()


def _say(resp) -> str:
    return (resp.controller_say_rendered or "").lower()


def _at_takeoff_readback(force_rto=None):
    overrides = {"callsign": "DLH39A", "runway": "25L"}
    if force_rto is not None:
        overrides["force_rto"] = force_rto
    session = create_session(get_flow("tower"), variable_overrides=overrides)
    process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="DLH39A, holding short runway 25L, ready for departure",
    ))
    process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="line up and wait runway 25L, DLH39A",
    ))
    stored = session_store.get_session(session.session_id)
    assert stored.current_state == "PILOT_TAKEOFF_READBACK", stored.current_state
    return session


class TestTheRoll:
    def test_default_probability_is_tiny(self, shipped_rto_probability):
        # The suite runs with the roll disabled, so this reads the shipped
        # default rather than the value tests see.
        assert 0 < shipped_rto_probability <= 0.01, (
            f"{shipped_rto_probability} is far too likely for an unannounced RTO"
        )

    def test_probability_zero_never_fires(self, monkeypatch):
        monkeypatch.setattr(config, "RTO_PROBABILITY", 0.0)
        assert not any(should_reject_takeoff({}, {}) for _ in range(200))

    def test_probability_one_always_fires(self, monkeypatch):
        monkeypatch.setattr(config, "RTO_PROBABILITY", 1.0)
        assert all(should_reject_takeoff({}, {}) for _ in range(50))

    def test_an_explicit_override_beats_the_probability(self, monkeypatch):
        monkeypatch.setattr(config, "RTO_PROBABILITY", 0.0)
        assert should_reject_takeoff({"force_rto": True}, {})
        monkeypatch.setattr(config, "RTO_PROBABILITY", 1.0)
        assert not should_reject_takeoff({"force_rto": False}, {})

    def test_a_takeoff_is_never_rejected_after_liftoff(self, monkeypatch):
        monkeypatch.setattr(config, "RTO_PROBABILITY", 1.0)
        assert not should_reject_takeoff({}, {"airborne": True})
        # Not even when forced: there is no such thing after the wheels are up.
        assert not should_reject_takeoff({"force_rto": True}, {"airborne": True})


class TestTheOrdinaryDeparture:
    def test_without_the_override_the_departure_proceeds(self, monkeypatch):
        monkeypatch.setattr(config, "RTO_PROBABILITY", 0.0)
        session = _at_takeoff_readback()
        resp = process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=GOOD_READBACK)
        )
        assert resp.next_state_id == "PILOT_AWAIT_AIRBORNE"
        assert "readback correct" in _say(resp)
        assert "cancel" not in _say(resp)


class TestTheRejectedTakeoff:
    def test_tower_cancels_with_the_standard_phraseology(self):
        session = _at_takeoff_readback(force_rto=True)
        resp = process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=GOOD_READBACK)
        )
        said = _say(resp)
        assert "cancel take-off" in said, said
        assert "stop immediately" in said, said
        # The one call that has to be unmistakable: instruction and callsign
        # are both repeated.
        assert said.count("cancel take-off") >= 2, said
        assert resp.next_state_id == "PILOT_RTO_ACK"

    def test_a_wrong_readback_gets_the_correction_not_a_cancelled_takeoff(self):
        session = _at_takeoff_readback(force_rto=True)
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="cleared for takeoff, DLH39A",
        ))
        said = _say(resp)
        assert "cancel" not in said, said
        assert "say again" in said, said
        assert resp.next_state_id == "PILOT_TAKEOFF_READBACK"

    def test_the_stop_is_acknowledged_and_the_aircraft_held(self):
        session = _at_takeoff_readback(force_rto=True)
        process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=GOOD_READBACK)
        )
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="stopping, DLH39A",
        ))
        said = _say(resp)
        assert "hold position" in said, said
        assert resp.next_state_id == "TOWER_RTO_COMPLETE"
        assert resp.session_complete, "the session ends with the aircraft stopped"

    def test_an_unclear_stopping_call_is_chased(self):
        session = _at_takeoff_readback(force_rto=True)
        process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=GOOD_READBACK)
        )
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="say again",
        ))
        assert resp.next_state_id == "PILOT_RTO_ACK"

    def test_giving_up_on_the_readback_does_not_reject_the_takeoff(self, monkeypatch):
        """Three unreadable readbacks is a transcription problem, not an RTO.

        Giving up lands where a correct readback would have. Picking the first
        accepted branch blindly would take the guarded RTO branch instead and
        cancel the takeoff of a pilot who was merely not understood.
        """
        monkeypatch.setattr(config, "RTO_PROBABILITY", 0.0)
        session = _at_takeoff_readback()
        for _ in range(3):
            resp = process_transmission(session.session_id, DecisionRequest(
                pilot_utterance="cleared for takeoff, DLH39A",
            ))
        assert resp.next_state_id == "PILOT_AWAIT_AIRBORNE", resp.next_state_id
        assert "cancel" not in _say(resp), _say(resp)

    def test_a_rejected_takeoff_never_hands_off_to_departure(self):
        """The aircraft is stopped on the runway; no frequency change is due."""
        session = _at_takeoff_readback(force_rto=True)
        process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=GOOD_READBACK)
        )
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="stopping, DLH39A",
        ))
        assert resp.active_flow == "tower-v1", (
            f"chained on to {resp.active_flow} after a rejected takeoff"
        )
        stored = session_store.get_session(session.session_id)
        assert stored.no_chain, "the departure flow must not follow an RTO"

        # And nothing along the way mentioned a departure frequency.
        spoken = " ".join(
            str(entry.get("pilot_utterance", "")) for entry in stored.decision_history
        )
        assert "120.8" not in spoken


# ---------------------------------------------------------------------------
# Go-around — Tower breaks off an approach
# ---------------------------------------------------------------------------

GOOD_LANDING_READBACK = "cleared to land runway 26L, DLH6RK"


def _at_landing_readback(force_go_around=None):
    overrides = {"callsign": "DLH6RK", "runway": "26L",
                 "approach_freq": "119.000", "go_around_altitude": "3000"}
    if force_go_around is not None:
        overrides["force_go_around"] = force_go_around
    session = create_session(get_flow("ifr-tower-landing"), variable_overrides=overrides)
    process_transmission(session.session_id, DecisionRequest(
        pilot_utterance="DLH6RK, established ILS runway 26L",
    ))
    stored = session_store.get_session(session.session_id)
    assert stored.current_state == "PILOT_LANDING_READBACK", stored.current_state
    return session


class TestTheGoAroundRoll:
    def test_default_probability_is_tiny(self, shipped_go_around_probability):
        assert 0 < shipped_go_around_probability <= 0.01

    def test_an_explicit_override_beats_the_probability(self, monkeypatch):
        monkeypatch.setattr(config, "GO_AROUND_PROBABILITY", 0.0)
        assert should_go_around({"force_go_around": True}, {})
        monkeypatch.setattr(config, "GO_AROUND_PROBABILITY", 1.0)
        assert not should_go_around({"force_go_around": False}, {})

    def test_never_sent_around_once_the_aircraft_is_down(self, monkeypatch):
        monkeypatch.setattr(config, "GO_AROUND_PROBABILITY", 1.0)
        assert not should_go_around({}, {"landed": True})
        assert not should_go_around({"force_go_around": True}, {"runway_vacated": True})


class TestTheGoAround:
    def test_the_ordinary_approach_lands(self):
        session = _at_landing_readback()
        resp = process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=GOOD_LANDING_READBACK)
        )
        assert resp.next_state_id == "PILOT_RUNWAY_VACATED"
        assert "go around" not in _say(resp)

    def test_tower_uses_the_standard_phraseology(self):
        session = _at_landing_readback(force_go_around=True)
        resp = process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=GOOD_LANDING_READBACK)
        )
        said = _say(resp)
        # Given twice: it has to be acted on before it is understood.
        assert said.count("go around") >= 2, said
        assert "3000" in said, said
        assert resp.next_state_id == "PILOT_GO_AROUND_READBACK"

    def test_a_wrong_landing_readback_gets_the_correction_not_a_go_around(self):
        session = _at_landing_readback(force_go_around=True)
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="cleared to land, DLH6RK",
        ))
        assert "go around" not in _say(resp), _say(resp)
        assert resp.next_state_id == "PILOT_LANDING_READBACK"

    def test_the_climb_is_read_back_and_graded(self):
        session = _at_landing_readback(force_go_around=True)
        process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=GOOD_LANDING_READBACK)
        )
        wrong = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="going around, climbing 5000 feet, DLH6RK",
        ))
        assert wrong.next_state_id == "PILOT_GO_AROUND_READBACK", "wrong altitude accepted"

        good = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="going around, climbing 3000 feet, DLH6RK",
        ))
        assert "approach" in _say(good), _say(good)

    def test_a_go_around_goes_back_to_approach_not_to_ground(self):
        """The aircraft is flying another approach — it must not be taxied."""
        session = _at_landing_readback(force_go_around=True)
        process_transmission(
            session.session_id, DecisionRequest(pilot_utterance=GOOD_LANDING_READBACK)
        )
        process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="going around, climbing 3000 feet, DLH6RK",
        ))
        resp = process_transmission(session.session_id, DecisionRequest(
            pilot_utterance="119.000, DLH6RK",
        ))
        assert resp.next_state_id == "TOWER_GO_AROUND_COMPLETE", resp.next_state_id
        assert resp.active_flow == "ifr-tower-landing-v1", (
            f"chained to {resp.active_flow} — a go-around does not taxi in"
        )
