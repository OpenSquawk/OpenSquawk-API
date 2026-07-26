"""Every frequency handoff must give the pilot somewhere to read it back.

Real phraseology has the pilot read a frequency change back before switching.
Six of the eight handoffs already had a readback state; clearance-v1 and
taxi-v1 bundled the handoff into the "readback correct" line and ended the flow
immediately, so a pilot who read the new frequency back was talking to a
session that had already moved on.

This is a structural invariant over all flows, so a new flow cannot reintroduce
the gap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.flow_loader import load_all_flows

FLOWS_DIR = Path(__file__).parent.parent / "flows"

# "contact ground 121.800", "contact Approach {{approach_freq}}"
HANDOFF_PATTERN = re.compile(r"\bcontact\s+\w+\s+\{\{\s*(\w*freq\w*)\s*\}\}", re.IGNORECASE)


def _handoff_states():
    """(flow_slug, state_id, state, freq_var) for every ATC handoff instruction."""
    found = []
    for slug, flow in load_all_flows(FLOWS_DIR).items():
        for state_id, state in flow.states.items():
            template = state.say_template or ""
            match = HANDOFF_PATTERN.search(template)
            if match:
                found.append((slug, state_id, flow, state, match.group(1)))
    return found


def _reachable_states(flow, state, depth=3):
    """States reachable from `state` through auto-transitions."""
    seen = []
    frontier = [state]
    for _ in range(depth):
        nxt = []
        for current in frontier:
            for transition in current.auto_transitions or []:
                target = flow.states.get(transition.to)
                if target is not None and target.id not in [s.id for s in seen]:
                    seen.append(target)
                    nxt.append(target)
        frontier = nxt
    return seen


def test_every_flow_has_a_handoff():
    """Guards the test itself — a broken pattern would silently pass everything."""
    assert len(_handoff_states()) >= 8


@pytest.mark.parametrize(
    "slug,state_id,flow,state,freq_var",
    _handoff_states(),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_handoff_is_followed_by_a_frequency_readback(slug, state_id, flow, state, freq_var):
    followers = _reachable_states(flow, state)
    pilot_readbacks = [
        s for s in followers
        if s.role == "pilot" and freq_var in (s.readback_required or [])
    ]
    assert pilot_readbacks, (
        f"{slug}::{state_id} hands off to {freq_var} but no pilot state reads it back; "
        f"reachable: {[s.id for s in followers]}"
    )
