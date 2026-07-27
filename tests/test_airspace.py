"""Enroute, the frequency depends on where you are.

The flows had no notion of that: the centre frequency was a flow default for the
whole flight. Sector boundaries are now vendored rather than fetched, so this
works with no network and with VATSIM unstaffed — which is the normal case, and
why live controller data is deliberately not a source.
"""

from __future__ import annotations

import re

import pytest

from app.airspace import (
    _point_in_polygon,
    center_frequency,
    center_frequency_at,
    sector_at,
)

VHF_BAND = re.compile(r"^1(1[89]|2\d|3[0-6])\.\d{3}$")


class TestSectorLookup:
    @pytest.mark.parametrize("name,lat,lon,expected_fir", [
        ("Munich",      48.354, 11.786, "EDMM"),
        ("Frankfurt",   50.033,  8.570, "EDGG"),
        ("Hamburg",     53.630,  9.988, "EDWW"),
        ("Paris CDG",   49.010,  2.548, "LFFF"),
        ("London LHR",  51.470, -0.454, "EGTT"),
        ("New York",    40.640, -73.779, "KZNY"),
        ("Athens",      37.936, 23.945, "LGMD"),
    ])
    def test_a_position_resolves_to_its_own_fir(self, name, lat, lon, expected_fir):
        sector = sector_at(lat, lon)
        assert sector is not None, f"{name} fell outside every sector"
        assert sector.id.split("-")[0] == expected_fir, f"{name} → {sector.id}"

    def test_the_most_specific_sector_wins(self):
        """A FIR and its own subdivisions both cover a point."""
        sector = sector_at(48.354, 11.786)
        assert sector.id.startswith("EDMM")
        assert "-" in sector.id, f"expected a subdivision, got {sector.id}"

    def test_oceanic_airspace_resolves(self):
        sector = sector_at(0.0, -160.0)
        assert sector is not None
        assert sector.oceanic, sector.id

    def test_coverage_is_global(self):
        """There is no gap to fall into — FIRs tile the whole planet.

        Worth pinning because it is the reason nothing needs a "no sector"
        fallback: even the poles and mid-ocean resolve.
        """
        for lat, lon in [(-89.9, 0.0), (89.9, 0.0), (35.0, -40.0), (0.0, -160.0)]:
            assert sector_at(lat, lon) is not None, f"{lat},{lon} fell through"

    def test_neighbouring_sectors_differ(self):
        """The whole point: crossing a boundary changes who you work."""
        munich = sector_at(48.354, 11.786)
        hamburg = sector_at(53.630, 9.988)
        assert munich.id != hamburg.id


class TestCenterFrequency:
    @pytest.mark.parametrize("lat,lon", [
        (48.354, 11.786), (50.033, 8.570), (51.470, -0.454), (40.640, -73.779),
    ])
    def test_every_resolved_sector_has_a_usable_frequency(self, lat, lon):
        result = center_frequency_at(lat, lon)
        assert result is not None
        _sector_id, freq = result
        assert VHF_BAND.match(freq), f"{freq} is not an airband frequency"
        assert freq != "121.500", "never the guard frequency"

    def test_the_frequency_is_stable_for_a_sector(self):
        """A handoff and its readback have to agree, and so do two sessions."""
        first = center_frequency("EDMM-RDG")
        assert first == center_frequency("EDMM-RDG")
        assert first == center_frequency("edmm-rdg")

    def test_crossing_a_boundary_changes_the_frequency(self):
        munich = center_frequency_at(48.354, 11.786)
        hamburg = center_frequency_at(53.630, 9.988)
        assert munich[1] != hamburg[1]

    def test_a_missing_dataset_degrades_rather_than_crashing(self, monkeypatch):
        """The only way a lookup yields nothing: the vendored file is gone."""
        import app.airspace as airspace
        monkeypatch.setattr(airspace, "_sectors", [])
        assert airspace.sector_at(48.354, 11.786) is None
        assert airspace.center_frequency_at(48.354, 11.786) is None


class TestGeometry:
    SQUARE = [[(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (0.0, 0.0)]]

    def test_inside_and_outside(self):
        assert _point_in_polygon(5, 5, self.SQUARE)
        assert not _point_in_polygon(15, 5, self.SQUARE)
        assert not _point_in_polygon(5, 15, self.SQUARE)

    def test_a_hole_is_not_inside(self):
        with_hole = self.SQUARE + [[(4.0, 4.0), (4.0, 6.0), (6.0, 6.0), (6.0, 4.0), (4.0, 4.0)]]
        assert not _point_in_polygon(5, 5, with_hole), "the hole counted as inside"
        assert _point_in_polygon(2, 2, with_hole)


def test_lookup_is_cheap_enough_for_a_telemetry_tick():
    """The bounding-box prefilter is what makes ~1100 polygons affordable."""
    import time
    sector_at(48.354, 11.786)  # warm the dataset load out of the measurement
    start = time.perf_counter()
    for _ in range(100):
        sector_at(48.354, 11.786)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 500, f"100 lookups took {elapsed_ms:.0f} ms"


class TestSessionTracking:
    """The sector follows the aircraft, and only changes when it really does."""

    @pytest.fixture(autouse=True)
    def _flows(self):
        from pathlib import Path
        from app import session_store
        from app.flow_loader import load_all_flows
        load_all_flows(Path(__file__).parent.parent / "flows")
        session_store._sessions.clear()

    def _session(self):
        from app.flow_loader import get_flow
        from app.session_store import create_session
        return create_session(get_flow("departure"), airport_icao="EDDM")

    def test_a_position_sets_the_sector_and_its_frequency(self):
        from app.decision_engine import process_telemetry
        session = self._session()
        process_telemetry(session.session_id, {"lat": 48.354, "lon": 11.786, "altitude_ft": 20000})

        from app import session_store
        stored = session_store.get_session(session.session_id)
        assert stored.variables["sector_id"].startswith("EDMM")
        assert VHF_BAND.match(stored.variables["sector_freq"])

    def test_flying_into_another_sector_changes_the_frequency(self):
        from app import session_store
        from app.decision_engine import process_telemetry
        session = self._session()

        process_telemetry(session.session_id, {"lat": 48.354, "lon": 11.786, "altitude_ft": 20000})
        south = session_store.get_session(session.session_id).variables["sector_freq"]

        process_telemetry(session.session_id, {"lat": 53.630, "lon": 9.988, "altitude_ft": 20000})
        north = session_store.get_session(session.session_id)
        assert north.variables["sector_id"].startswith("EDWW")
        assert north.variables["sector_freq"] != south

    def test_staying_put_does_not_reassign_the_frequency(self):
        from app import session_store
        from app.decision_engine import process_telemetry
        session = self._session()

        process_telemetry(session.session_id, {"lat": 48.354, "lon": 11.786, "altitude_ft": 20000})
        first = session_store.get_session(session.session_id).variables["sector_freq"]
        process_telemetry(session.session_id, {"lat": 48.360, "lon": 11.790, "altitude_ft": 21000})
        assert session_store.get_session(session.session_id).variables["sector_freq"] == first

    def test_without_a_position_no_sector_is_invented(self):
        """The no-bridge case: nothing is claimed about where the aircraft is."""
        from app import session_store
        from app.decision_engine import process_telemetry
        session = self._session()
        process_telemetry(session.session_id, {"altitude_ft": 20000})
        stored = session_store.get_session(session.session_id)
        assert "sector_id" not in stored.variables
