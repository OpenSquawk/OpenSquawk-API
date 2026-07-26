"""Rejected takeoffs during an ordinary departure.

A cancelled takeoff only existed as its own drill (rto-v1), which the pilot
starts knowing what is coming. The point of practising one is that you do not,
so a normal departure now carries a very small chance of Tower cancelling the
takeoff clearance instead of confirming the readback.

"Very small" has to mean it: at the default of 0.2% a pilot meets one roughly
once in five hundred departures, which is the point. That makes it useless to
test by chance, so the roll is never left to chance in a test — a session can
be told to take the branch or to never take it, and the probability itself is
only ever exercised at 0 and 1.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, Optional

from app import config

logger = logging.getLogger(__name__)

# Session variable a caller can set to take the decision out of the engine's
# hands: True always cancels, False never does. Used by the classroom to teach
# the manoeuvre on demand, and by tests so no assertion rests on a dice roll.
FORCE_VARIABLE = "force_rto"


def _forced(variables: Dict[str, Any]) -> Optional[bool]:
    """The caller's explicit choice, or None to leave it to probability."""
    if FORCE_VARIABLE not in variables:
        return None
    value = variables[FORCE_VARIABLE]
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def should_reject_takeoff(
    variables: Dict[str, Any],
    flags: Dict[str, bool],
    rng: Optional[random.Random] = None,
) -> bool:
    """Whether Tower cancels this takeoff.

    Refuses outright once the aircraft is airborne: a takeoff cannot be
    rejected after liftoff, and cancelling one then would teach the opposite of
    the manoeuvre.
    """
    if flags.get("airborne"):
        return False

    forced = _forced(variables)
    if forced is not None:
        return forced

    probability = config.RTO_PROBABILITY
    if probability <= 0:
        return False
    if probability >= 1:
        return True
    return (rng or random).random() < probability
