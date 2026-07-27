"""Runway endpoint selection against real recorded OSM geometry.

`apply_runway_point` is covered by unit tests on synthetic two-node ways, and
`/taxiroute` has a contract test on a synthetic fixture. Neither exercises the
thing that actually goes wrong on real data: an OSM way's node order is
arbitrary, so taking the first node as "start" puts you at the wrong end of the
runway roughly half the time.

These run the public endpoint over recorded Overpass responses for real German
airports. The assertions are plain geography — a 25 takes off toward the west,
so its threshold is the *eastern* end of the strip — which is independent of
how the implementation decides, so a regression cannot satisfy them by
agreeing with itself.

Fixtures come from ``tests/fixtures/osm``; no test here touches the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.airport_geocode import airport_features_query, parse_airport_features, way_nodes_query
from app.routes.tool_routes import get_overpass_client
from main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from taxi_sweep import CachingOverpassClient  # noqa: E402


def _offline_client() -> CachingOverpassClient:
    return CachingOverpassClient(offline=True)


@pytest.fixture
def client():
    app.dependency_overrides[get_overpass_client] = _offline_client
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_overpass_client, None)


def _require_fixtures(icao: str, way_id: int) -> None:
    """Skip when this airport's recordings are not in the checkout."""
    overpass = _offline_client()
    try:
        overpass.fetch_json(airport_features_query(icao))
        overpass.fetch_json(way_nodes_query(way_id))
    except RuntimeError as exc:
        if "offline mode" in str(exc):
            pytest.skip(f"no OSM fixtures recorded for {icao}")
        raise


def _resolve(client, icao: str, designator: str, point: str) -> tuple[float, float]:
    resp = client.get(
        "/api/service/tools/airport-geocode",
        params={
            "airport": icao,
            "origin_name": designator,
            "origin_runway_point": point,
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["origin"]["result"]
    assert result is not None, f"{icao} {designator} did not resolve"
    assert result["type"] == "runway", f"{icao} {designator} resolved to {result['type']}"
    return result["lat"], result["lon"]


# (icao, way id whose fixture is needed, designator, the opposite designator)
RUNWAY_PAIRS = [
    ("EDDF", 32132863, "07L", "25R"),
    ("EDDL", 142676595, "05L", "23R"),
    ("EDDL", 142676596, "05R", "23L"),
    ("EDDN", 4316825, "10", "28"),
]


@pytest.mark.parametrize("icao,way_id,low,high", RUNWAY_PAIRS)
def test_opposite_designators_start_at_opposite_ends(icao, way_id, low, high, client):
    """The two ends of one runway are two different thresholds.

    Both designators name the same OSM way. Node order cannot distinguish them,
    so an implementation that reads "start" off the geometry returns the same
    point for both — which is the bug.
    """
    _require_fixtures(icao, way_id)

    low_start = _resolve(client, icao, low, "start")
    high_start = _resolve(client, icao, high, "start")

    assert low_start != high_start, (
        f"{icao} {low} and {high} share a threshold — the designator was ignored"
    )
    assert low_start == _resolve(client, icao, high, "end")
    assert high_start == _resolve(client, icao, low, "end")


@pytest.mark.parametrize("icao,way_id,low,high", RUNWAY_PAIRS)
def test_the_lower_designator_starts_at_the_western_end(icao, way_id, low, high, client):
    """A 05/07/10 departs eastward, so it starts at the western end.

    Plain geography rather than the implementation's own bearing maths: every
    pair here runs broadly east-west, so longitude alone settles which end is
    which.
    """
    _require_fixtures(icao, way_id)

    _, low_lon = _resolve(client, icao, low, "start")
    _, high_lon = _resolve(client, icao, high, "start")

    assert low_lon < high_lon, (
        f"{icao} {low} should line up at the western end and {high} at the eastern; "
        f"got {low}={low_lon}, {high}={high_lon}"
    )


@pytest.mark.parametrize("icao,way_id,low,high", RUNWAY_PAIRS)
def test_center_still_returns_the_middle_of_the_strip(icao, way_id, low, high, client):
    """`center` is the old behaviour, kept for callers that want the way centre."""
    _require_fixtures(icao, way_id)

    _, center_lon = _resolve(client, icao, low, "center")
    _, low_lon = _resolve(client, icao, low, "start")
    _, high_lon = _resolve(client, icao, high, "start")

    assert low_lon < center_lon < high_lon, (
        f"{icao} {low} centre {center_lon} is not between the two thresholds"
    )


def test_a_runway_threshold_is_far_from_the_strip_centre(client):
    """The gap the default closes: a threshold is not the middle of the runway.

    EDDF's 07L/25R is ~4000 m long, so routing to the centre lands a taxiing
    aircraft a kilometre or more from where it was told to hold.
    """
    from app.airport_geocode import haversine_distance

    _require_fixtures("EDDF", 32132863)

    start = _resolve(client, "EDDF", "07L", "start")
    center = _resolve(client, "EDDF", "07L", "center")

    assert haversine_distance(start, center) > 1000, (
        "threshold and centre should be far apart on a full-length runway"
    )
