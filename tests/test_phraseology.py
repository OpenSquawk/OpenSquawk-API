"""Radar identification uses the phrase the region actually uses.

ICAO Doc 4444 has the controller say "IDENTIFIED"; "RADAR CONTACT" is the FAA
equivalent. departure-v1 said "radar contact" at German airports, which is the
wrong phrase there — but hardcoding the ICAO wording would only move the
problem, so the phrase is a variable resolved per session.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config, session_store
from app.decision_engine import process_transmission
from app.flow_loader import get_flow, load_all_flows
from app.models import DecisionRequest
from app.phraseology import phrase_variables, resolve_region
from app.session_store import create_session

FLOWS_DIR = Path(__file__).parent.parent / "flows"


@pytest.fixture(autouse=True)
def load_flows():
    load_all_flows(FLOWS_DIR)
    session_store._sessions.clear()


@pytest.fixture(autouse=True)
def default_region(monkeypatch):
    monkeypatch.setattr(config, "PHRASEOLOGY_REGION", "auto")


class TestRegionResolution:
    @pytest.mark.parametrize("icao", ["EDDM", "EDDF", "LFPG", "EGLL", "LOWW", "LSZH"])
    def test_icao_airports_use_icao_phraseology(self, icao):
        assert resolve_region(icao) == "icao"
        assert phrase_variables(icao)["radar_identification"] == "identified"

    @pytest.mark.parametrize("icao", ["KJFK", "KLAX", "CYYZ", "PHNL"])
    def test_faa_airspace_uses_faa_phraseology(self, icao):
        assert resolve_region(icao) == "faa"
        assert phrase_variables(icao)["radar_identification"] == "radar contact"

    def test_unknown_airport_defaults_to_icao(self):
        assert resolve_region(None) == "icao"
        assert resolve_region("") == "icao"

    def test_explicit_setting_overrides_the_airport(self, monkeypatch):
        monkeypatch.setattr(config, "PHRASEOLOGY_REGION", "faa")
        assert resolve_region("EDDM") == "faa"
        monkeypatch.setattr(config, "PHRASEOLOGY_REGION", "icao")
        assert resolve_region("KJFK") == "icao"


class TestDepartureFlow:
    def _radar_contact_line(self, airport_icao):
        session = create_session(get_flow("departure"), airport_icao=airport_icao)
        resp = process_transmission(
            session.session_id,
            DecisionRequest(pilot_utterance="passing 1500, climbing 5000"),
        )
        return resp.controller_say_rendered or ""

    def test_european_departure_says_identified(self):
        line = self._radar_contact_line("EDDM")
        assert "identified" in line.lower(), line
        assert "radar contact" not in line.lower(), line

    def test_us_departure_says_radar_contact(self):
        line = self._radar_contact_line("KJFK")
        assert "radar contact" in line.lower(), line


def test_no_flow_hardcodes_a_region_specific_phrase():
    """Structural: the phrase must come from the variable, not the YAML."""
    offenders = []
    for path in sorted(FLOWS_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "say_template:" in line and "radar contact" in line.lower():
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        "region-specific phraseology hardcoded in a flow; use "
        f"{{{{radar_identification}}}} instead:\n" + "\n".join(offenders)
    )
