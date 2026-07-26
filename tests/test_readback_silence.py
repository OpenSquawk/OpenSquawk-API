"""ATC chases a readback that never came.

READBACK_SILENCE_MS existed but was read by nothing: readback states carry no
``auto_advance_timeout_ms`` (they wait for the pilot rather than advancing on
their own), so the frontend had no window to arm a timer from and the
re-request never fired at all — not late, never.

The window is a server-side policy, so it is published on every readback state
in the runtime tree, and it is short: a controller waiting on a mandatory
readback asks again within seconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, session_store
from app.decision_engine import process_timeout, process_transmission
from app.flow_loader import get_flow, load_all_flows
from app.models import DecisionRequest
from app.session_store import create_session
from main import app

FLOWS_DIR = Path(__file__).parent.parent / "flows"


@pytest.fixture(autouse=True)
def load_flows():
    load_all_flows(FLOWS_DIR)
    session_store._sessions.clear()


class TestPublishedWindow:
    def test_window_is_between_ten_and_fifteen_seconds(self):
        assert 10_000 <= config.READBACK_SILENCE_MS <= 15_000, (
            f"a controller chases a missing readback within seconds, not "
            f"{config.READBACK_SILENCE_MS / 1000:.0f}s"
        )

    def test_runtime_tree_publishes_the_window_on_readback_states(self):
        with TestClient(app) as client:
            payload = client.get("/api/decision-flows/runtime").json()

        checked = 0
        for slug, flow in payload["flows"].items():
            for state_id, state in flow["states"].items():
                if state.get("role") == "pilot" and state.get("readback_required"):
                    assert state.get("readback_silence_ms") == config.READBACK_SILENCE_MS, (
                        f"{slug}::{state_id} publishes no readback silence window, "
                        f"so the frontend cannot arm the re-request timer"
                    )
                    checked += 1
        assert checked >= 25, f"only {checked} readback states seen — query is wrong"

    def test_window_is_not_published_where_the_pilot_owes_nothing(self):
        with TestClient(app) as client:
            payload = client.get("/api/decision-flows/runtime").json()

        tower = payload["flows"]["tower-v1"]["states"]
        assert "readback_silence_ms" not in tower["PILOT_AWAIT_AIRBORNE"]


class TestTimeoutBehaviour:
    def _at_readback(self):
        session = create_session(get_flow("clearance"))
        process_transmission(
            session.session_id, DecisionRequest(pilot_utterance="request clearance")
        )
        return session

    def test_silence_re_requests_the_readback(self):
        session = self._at_readback()
        resp = process_timeout(session.session_id)

        assert resp.next_state_id == "PILOT_READBACK", (
            "after the re-request the pilot must still owe the readback"
        )
        said = (resp.controller_say_rendered or "").lower()
        assert "say again" in said, f"re-request used no standard phrasing: {said!r}"

    def test_the_re_request_repeats_the_clearance_items(self):
        session = self._at_readback()
        resp = process_timeout(session.session_id)
        said = (resp.controller_say_rendered or "").lower()
        # Asking again without repeating what was missed is useless to the pilot.
        assert "squawk" in said and "2341" in said, said

    def test_a_readback_given_before_the_timeout_ends_the_wait(self):
        """The pilot answered — the state moved on, so no timeout applies."""
        session = self._at_readback()
        process_transmission(session.session_id, DecisionRequest(
            pilot_utterance=(
                "cleared to Munich via BIBAX1N, climb initially 5000 feet, "
                "squawk 2341, DLH39A"
            ),
        ))
        moved_on = session_store.get_session(session.session_id)
        assert moved_on.current_state == "PILOT_GROUND_FREQ_READBACK"

        # A timeout now belongs to the *new* state, and re-requests that one.
        resp = process_timeout(session.session_id)
        assert resp.next_state_id == "PILOT_GROUND_FREQ_READBACK"
        assert "ground" in (resp.controller_say_rendered or "").lower()
