"""Pushback facing — what the pilot asks for, and what ground can give.

A crew routinely offers a facing they are able to take ("able to push facing
north"). That was ignored: ground always read out the flow's own default, so
the offer had no effect at all.

Honouring it needs a rule for what is possible, and the airport data that is
actually available is the runway in use. Facing is chosen so the aircraft ends
up pointing broadly the way it will taxi, so the runway's own direction is the
reference: a request within a right angle of it is workable and gets approved,
anything else is refused and ground assigns the runway-derived facing instead.

This works for any runway at any airport, and replaces a hardcoded "west" that
was wrong at every field whose departure runway does not point that way.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# Compass points and their bearings. Ordered longest-first where names overlap
# ("northeast" before "north") so the regex cannot match the shorter prefix.
_COMPASS: Tuple[Tuple[str, float], ...] = (
    ("northeast", 45.0),
    ("northwest", 315.0),
    ("southeast", 135.0),
    ("southwest", 225.0),
    ("north", 0.0),
    ("south", 180.0),
    ("east", 90.0),
    ("west", 270.0),
)

_COMPASS_BEARINGS = dict(_COMPASS)

# "able to push facing north", "request pushback facing south east",
# "we can push facing north-west". The direction may be hyphenated or spaced.
_FACING_RE = re.compile(
    r"\bfac(?:e|ing)\s+(" + "|".join(
        name[:5] + r"[\s\-]?" + name[5:] if len(name) > 5 else name
        for name, _ in _COMPASS
    ) + r")\b",
    re.IGNORECASE,
)

# How far from the runway direction a requested facing may sit and still work.
_MAX_FACING_OFFSET_DEG = 90.0


def requested_facing(utterance: str) -> Optional[str]:
    """The facing the pilot offered, normalised, or None if none was offered."""
    match = _FACING_RE.search(utterance)
    if not match:
        return None
    return re.sub(r"[\s\-]", "", match.group(1)).lower()


def runway_bearing(runway: str) -> Optional[float]:
    """Bearing of a runway designator: "25L" → 250°."""
    match = re.match(r"^\s*(\d{1,2})\s*[LCR]?\s*$", str(runway or ""), re.IGNORECASE)
    if not match:
        return None
    number = int(match.group(1))
    if not 1 <= number <= 36:
        return None
    return (number * 10.0) % 360.0


def _angular_difference(a: float, b: float) -> float:
    """Smallest angle between two bearings, 0–180."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def assignable_facing(runway: str) -> Optional[str]:
    """The facing ground would assign by default for this runway."""
    bearing = runway_bearing(runway)
    if bearing is None:
        return None
    return min(_COMPASS_BEARINGS, key=lambda name: _angular_difference(_COMPASS_BEARINGS[name], bearing))


def facing_is_workable(facing: str, runway: str) -> bool:
    """Can the aircraft be pushed to face this way for a departure off `runway`?"""
    bearing = runway_bearing(runway)
    wanted = _COMPASS_BEARINGS.get((facing or "").lower())
    if bearing is None or wanted is None:
        return False
    return _angular_difference(wanted, bearing) <= _MAX_FACING_OFFSET_DEG
