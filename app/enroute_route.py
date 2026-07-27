"""Pick the first enroute fix out of a filed route string.

A departure clearance names the SID and the point where it hands over to the
enroute structure. The rest of the route — ten or more elements including
airways — is covered by "flight planned route": no pilot reads it back, and
requiring it would fail every transcription.

The parser is deliberately shape-based rather than dataset-backed. Route strings
arrive from VATSIM and SimBrief in several dialects, and the elements that are
*not* fixes (airways, procedures, speed/level groups, filler) have far more
regular shapes than the fixes themselves.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

# A fix is an ICAO name-code (BIBAX, ARMUT) or a navaid ident (FFM, RID) —
# letters only, two to five of them.
_FIX_RE = re.compile(r"^[A-Z]{2,5}$")

# An airway: one or two letters and up to three digits (Y101, UM984, L603).
_AIRWAY_RE = re.compile(r"^[A-Z]{1,2}\d{1,3}$")

# A SID or STAR ident: a pronounceable stem, a revision number, a final letter
# (SULUS5S, TOBAK2E). Matched by shape as well as against the assigned SID,
# because the filed procedure and the one ATC assigns need not agree.
_PROCEDURE_RE = re.compile(r"^[A-Z]{2,5}\d{1,2}[A-Z]$")

# Route vocabulary that is letters-only and would otherwise read as a fix.
_FILLER = frozenset({"DCT", "SID", "STAR", "IFR", "VFR"})


def first_enroute_fix(
    route: str,
    sid: str | None = None,
    airports: Sequence[str] = (),
) -> str | None:
    """The first enroute fix in ``route``, or ``None`` when there is none.

    ``sid`` and ``airports`` name elements that appear in route strings but are
    not enroute fixes: the assigned departure procedure, and the departure and
    destination aerodromes some dialects include.
    """

    if not route or not route.strip():
        return None

    excluded = {token.strip().upper() for token in airports if token}
    if sid:
        excluded.add(sid.strip().upper())

    for token in route.upper().split():
        if token in _FILLER or token in excluded:
            continue
        if _AIRWAY_RE.match(token) or _PROCEDURE_RE.match(token):
            continue
        if _FIX_RE.match(token):
            return token

    return None


def resolve_enroute_clearance(
    route: str,
    sid: str | None = None,
    airports: Sequence[str] = (),
) -> dict[str, Any] | None:
    """Session variables granting the enroute route, or ``None``.

    ``None`` when the filed route yields no fix — demo and manual flights file
    none at all — so the flow keeps its YAML defaults, which are the no-route
    wording. Returns the overrides the flow expects:
      - ``enroute_fixes``: the fixes the pilot must read back
      - ``enroute_clause``: ready to speak, or "" when nothing is granted
    """

    fix = first_enroute_fix(route, sid=sid, airports=airports)
    if fix is None:
        return None

    return {
        "enroute_fixes": [fix],
        "enroute_clause": f", then {fix}, flight planned route",
    }
