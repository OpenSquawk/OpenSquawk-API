"""Region-dependent phraseology.

A handful of standard phrases differ between ICAO and FAA radiotelephony. The
one that was reported is radar identification: ICAO Doc 4444 has the controller
say "IDENTIFIED", while "RADAR CONTACT" is the FAA equivalent. The flows are
flown at European airports and said "radar contact", which is the wrong phrase
there.

Hardcoding the ICAO wording instead would only move the problem, so the phrases
are variables and the region is resolved per session. The default is derived
from the airport itself: ICAO location indicators are assigned by region, and
the first letter identifies it — K (contiguous US), C (Canada) and P (US
Pacific) are FAA airspace, everything else follows ICAO. An explicit
PHRASEOLOGY_REGION setting overrides the derivation.
"""

from __future__ import annotations

from typing import Dict, Optional

from app import config

# First letters of ICAO location indicators that denote FAA phraseology.
_FAA_PREFIXES = ("K", "C", "P")

_PHRASES: Dict[str, Dict[str, str]] = {
    "icao": {
        # Radar identification — ICAO Doc 4444 §12.3.1.5.
        "radar_identification": "identified",
    },
    "faa": {
        "radar_identification": "radar contact",
    },
}

DEFAULT_REGION = "icao"


def resolve_region(airport_icao: Optional[str]) -> str:
    """Which phraseology applies for a session at this airport."""
    configured = (config.PHRASEOLOGY_REGION or "auto").strip().lower()
    if configured in _PHRASES:
        return configured

    code = (airport_icao or "").strip().upper()
    if len(code) == 4 and code[0] in _FAA_PREFIXES:
        return "faa"
    return DEFAULT_REGION


def phrase_variables(airport_icao: Optional[str]) -> Dict[str, str]:
    """Phraseology variables to seed a new session with."""
    return dict(_PHRASES[resolve_region(airport_icao)])
