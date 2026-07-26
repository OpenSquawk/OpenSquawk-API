"""Only one controller issues the taxi-in clearance.

A flow that chains into taxi-in-v1 must not already have told the pilot where
to taxi: Ground issues that clearance. vfr-circuit-landing-v1 had Tower say
"taxi to the apron via taxiway Alpha, contact ground …" and then chained into
taxi-in-v1, which issues its own "taxi to <stand> via <route>" — so the pilot
was taxied twice, by two different controllers.

Real phraseology: at an airport with a Ground position, Tower clears the
aircraft to vacate and hands off. Ground does the taxiing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.flow_loader import load_all_flows

FLOWS_DIR = Path(__file__).parent.parent / "flows"

TAXI_CLEARANCE_FLOW = "taxi-in-v1"

# "taxi to X" — an instruction telling the pilot where to go on the ground.
TAXI_INSTRUCTION_RE = re.compile(r"\btaxi\s+(?:to|via)\b", re.IGNORECASE)


def _chain_targets(flows, slug, depth=5):
    """Flow slugs reachable from `slug` by following next_flow."""
    seen = []
    current = flows.get(slug)
    for _ in range(depth):
        nxt = getattr(current, "next_flow", None)
        if not nxt or nxt in seen:
            break
        seen.append(nxt)
        current = flows.get(nxt)
        if current is None:
            break
    return seen


def _flows_chaining_into_taxi_in():
    flows = load_all_flows(FLOWS_DIR)
    return [
        (slug, flow)
        for slug, flow in flows.items()
        if TAXI_CLEARANCE_FLOW in _chain_targets(flows, slug)
    ]


def test_some_flow_chains_into_taxi_in():
    """Guards the test — a broken chain walk would vacuously pass everything."""
    assert _flows_chaining_into_taxi_in()


@pytest.mark.parametrize(
    "slug,flow", _flows_chaining_into_taxi_in(),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_upstream_flow_does_not_issue_its_own_taxi_clearance(slug, flow):
    offenders = [
        state_id for state_id, state in flow.states.items()
        if state.role == "atc" and TAXI_INSTRUCTION_RE.search(state.say_template or "")
    ]
    assert not offenders, (
        f"{slug} chains into {TAXI_CLEARANCE_FLOW} but already issues a taxi "
        f"instruction at {offenders} — the pilot would be taxied twice"
    )


def test_taxi_in_still_issues_the_clearance():
    """The one controller that should be taxiing still does."""
    flows = load_all_flows(FLOWS_DIR)
    taxi_in = flows[TAXI_CLEARANCE_FLOW]
    issuing = [
        state_id for state_id, state in taxi_in.states.items()
        if state.role == "atc" and TAXI_INSTRUCTION_RE.search(state.say_template or "")
    ]
    assert issuing, "taxi-in-v1 must issue the taxi clearance"
