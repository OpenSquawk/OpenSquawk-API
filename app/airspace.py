"""Which control sector a position is in, and the frequency that goes with it.

Enroute, the frequency you work depends on where you are, not on which airport
you left. The flows had no notion of that at all: the centre frequency was a
flow default for the whole flight.

Two layers, both entirely offline:

**Position → sector.** The FIR boundaries are vendored in
``data/fir_boundaries.min.json``, trimmed from the VATSpy data project. They are
ours: nothing is fetched at runtime, so a session works with no network and with
VATSIM unstaffed — which is the normal case, and the reason live controller data
is deliberately not a source here.

**Sector → frequency.** Real published frequencies where the bundled airport
data has one, which is good coverage in North America and almost none in Europe
(European AIPs do not publish the area frequency as an airport frequency the way
US ones do). Everywhere else the frequency is *derived* from the sector id, the
same deterministic invention the engine already uses for any airport position
that publishes nothing. It is not the real-world frequency, and it is stable for
a given sector so a handoff and its readback agree. A curated per-sector table,
if one is ever built, drops into exactly this slot.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "fir_boundaries.min.json"

# Ring = [(lon, lat), ...]; Polygon = [outer_ring, hole, ...]; a FIR has many.
Ring = Sequence[Tuple[float, float]]


@dataclass(frozen=True)
class Sector:
    """A control sector — a FIR or one of its subdivisions."""
    id: str
    oceanic: bool
    region: Optional[str]


_sectors: Optional[List[dict]] = None


def _load() -> None:
    global _sectors
    if _sectors is not None:
        return
    try:
        with _DATA_FILE.open(encoding="utf-8") as fh:
            _sectors = json.load(fh)["firs"]
    except Exception as exc:  # missing or corrupt file — degrade, never crash
        logger.warning("FIR boundaries unavailable (%s) — no sector lookup", exc)
        _sectors = []


def _point_in_ring(lon: float, lat: float, ring: Ring) -> bool:
    """Ray casting. True when the point is inside this ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_at = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_at:
                inside = not inside
        j = i
    return inside


def _point_in_polygon(lon: float, lat: float, polygon: Sequence[Ring]) -> bool:
    """Inside the outer ring and outside every hole."""
    if not polygon or not _point_in_ring(lon, lat, polygon[0]):
        return False
    return not any(_point_in_ring(lon, lat, hole) for hole in polygon[1:])


def sector_at(lat: float, lon: float) -> Optional[Sector]:
    """The sector covering this position, or None when it is outside them all.

    Sectors overlap (a FIR and its own subdivisions both cover a point), so the
    most specific match wins — an id with a subdivision suffix beats the plain
    FIR. The bounding box on each record rejects almost all of the ~1100
    candidates before any ray casting happens, which is what keeps this cheap
    enough to run on a telemetry tick.
    """
    _load()
    assert _sectors is not None

    matches: List[dict] = []
    for record in _sectors:
        west, south, east, north = record["bbox"]
        if not (west <= lon <= east and south <= lat <= north):
            continue
        if any(_point_in_polygon(lon, lat, poly) for poly in record["polygons"]):
            matches.append(record)

    if not matches:
        return None
    # "EDGG-S" is more specific than "EDGG"; prefer the longer id, and prefer
    # non-oceanic where a coastal sector overlaps an oceanic one.
    best = sorted(matches, key=lambda r: (r["oceanic"], -len(r["id"])))[0]
    return Sector(id=best["id"], oceanic=best["oceanic"], region=best.get("region"))


@lru_cache(maxsize=512)
def center_frequency(sector_id: str) -> str:
    """The area frequency for a sector.

    Real where the bundled airport data publishes one for the FIR's own ICAO
    prefix, derived otherwise. Cached because it is asked on every tick.
    """
    from app.airport_data import invent_frequency, resolve_airport

    # A FIR id like "EDGG-S" carries the four-letter code of the area centre.
    # Where that code is also an airport — EIDW is both Dublin FIR and Dublin
    # airport, NZAA both Auckland FIR and Auckland — the FIR is named after its
    # principal airport, so that airport's published area frequency is the
    # sector's. Codes that are not airports at all (EDGG, EDMM, EGTT, KZNY) fall
    # through to the derived frequency below, which is most of Europe.
    base = sector_id.split("-", 1)[0].upper()
    if len(base) == 4:
        info = resolve_airport(base, positions=["center"])
        published = (info.frequencies.get("center") if info else None)
        if published:
            return published

    # Derived, and keyed on the *sector* so it changes at the boundary and
    # stays put within it.
    return invent_frequency(sector_id.upper(), "center")


def center_frequency_at(lat: float, lon: float) -> Optional[Tuple[str, str]]:
    """``(sector_id, frequency)`` for a position, or None outside every sector."""
    sector = sector_at(lat, lon)
    if sector is None:
        return None
    return sector.id, center_frequency(sector.id)
