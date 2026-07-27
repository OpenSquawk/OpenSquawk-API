"""Rare controller interventions during an otherwise normal flight.

A rejected takeoff and a go-around only existed as dedicated drills, which the
pilot starts already knowing what is coming — the opposite of the situation
being practised. Both can now happen unannounced in an ordinary departure or
approach.

"Rare" has to mean it: at the default of 0.2% a pilot meets one roughly once in
five hundred flights, which is the point. That makes it useless to test by
chance, so the roll is never left to chance in a test — a session can be told to
take the branch or never to take it, and the probability itself is only ever
exercised at 0 and 1. The same override is what lets an instructor call for one
on demand.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, Optional

from app import config

logger = logging.getLogger(__name__)

# Session variables a caller sets to take the decision out of the engine's
# hands: True always intervenes, False never does.
FORCE_RTO_VARIABLE = "force_rto"
FORCE_GO_AROUND_VARIABLE = "force_go_around"


def _forced(variables: Dict[str, Any], name: str) -> Optional[bool]:
    """The caller's explicit choice, or None to leave it to probability."""
    if name not in variables:
        return None
    value = variables[name]
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _roll(probability: float, rng: Optional[random.Random]) -> bool:
    if probability <= 0:
        return False
    if probability >= 1:
        return True
    return (rng or random).random() < probability


def should_reject_takeoff(
    variables: Dict[str, Any],
    flags: Dict[str, bool],
    rng: Optional[random.Random] = None,
) -> bool:
    """Whether Tower cancels this takeoff.

    Refuses outright once the aircraft is airborne: a takeoff cannot be rejected
    after liftoff, and cancelling one then would teach the opposite of the
    manoeuvre.
    """
    if flags.get("airborne"):
        return False
    forced = _forced(variables, FORCE_RTO_VARIABLE)
    if forced is not None:
        return forced
    return _roll(config.RTO_PROBABILITY, rng)


def should_go_around(
    variables: Dict[str, Any],
    flags: Dict[str, bool],
    rng: Optional[random.Random] = None,
) -> bool:
    """Whether Tower sends this approach around.

    Refuses once the aircraft is down: after touchdown the instruction is a
    rejected landing, not a go-around, and the flow has no such thing.
    """
    if flags.get("landed") or flags.get("runway_vacated"):
        return False
    forced = _forced(variables, FORCE_GO_AROUND_VARIABLE)
    if forced is not None:
        return forced
    return _roll(config.GO_AROUND_PROBABILITY, rng)
