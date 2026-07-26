"""What the controller says and what the pilot is expected to say back must agree.

Two items from the combined phraseology report:

- an approach clearance must not name a runway the flow is not using, or the
  pilot is read one runway and graded on another;
- the expected readback should follow the same order as the instruction, since
  a pilot reads a clearance back in the order it was given.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.flow_loader import load_all_flows

FLOWS_DIR = Path(__file__).parent.parent / "flows"
_FLOWS = load_all_flows(FLOWS_DIR)

_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _states_with_both_templates():
    found = []
    for slug, flow in sorted(_FLOWS.items()):
        for state_id, state in flow.states.items():
            if state.role != "pilot" or not state.expected_pilot_template:
                continue
            # The instruction is whatever ATC said to get here.
            for other_id, other in flow.states.items():
                if other.role != "atc" or not other.say_template:
                    continue
                if any(t.to == state_id for t in other.auto_transitions):
                    found.append((slug, other_id, state_id))
                    break
    return found


def test_there_are_instruction_readback_pairs():
    assert len(_states_with_both_templates()) >= 15


@pytest.mark.parametrize("slug,atc_id,pilot_id", _states_with_both_templates())
def test_readback_follows_the_order_of_the_instruction(slug, atc_id, pilot_id):
    """Items common to both are quoted back in the order they were given."""
    flow = _FLOWS[slug]
    said = _VAR.findall(flow.states[atc_id].say_template or "")
    expected = _VAR.findall(flow.states[pilot_id].expected_pilot_template or "")

    # The callsign moves to the end of a readback by convention, so it carries
    # no ordering information.
    said = [v for v in said if v not in ("callsign", "callsign_short")]
    expected = [v for v in expected if v not in ("callsign", "callsign_short")]

    shared = [v for v in said if v in expected]
    in_readback_order = [v for v in expected if v in shared]
    # De-duplicate, keeping first occurrence, so a value repeated for emphasis
    # (the runway in a cancel-takeoff call) does not read as a reordering.
    def dedupe(items):
        seen, out = set(), []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    assert dedupe(shared) == dedupe(in_readback_order), (
        f"{slug}: {atc_id} says {dedupe(shared)} but {pilot_id} expects them back as "
        f"{dedupe(in_readback_order)} — a pilot reads a clearance back in the order "
        f"it was given"
    )


def _approach_states():
    found = []
    for slug, flow in sorted(_FLOWS.items()):
        for state_id, state in flow.states.items():
            template = state.say_template or ""
            if re.search(r"\bcleared\b.{0,40}\bapproach\b", template, re.IGNORECASE):
                found.append((slug, state_id))
    return found


@pytest.mark.parametrize("slug,state_id", _approach_states())
def test_an_approach_clearance_names_the_runway_by_variable(slug, state_id):
    """A literal runway in an approach clearance contradicts the flow's own.

    The runway is a session variable resolved from the live ATIS; writing one
    into the template hardcodes a different runway than the one being flown.
    """
    template = _FLOWS[slug].states[state_id].say_template or ""
    literal_runways = re.findall(r"\brunway\s+(\d{2}[LCR]?)\b", template, re.IGNORECASE)
    assert not literal_runways, (
        f"{slug}::{state_id} names runway {literal_runways} literally; "
        f"use {{{{runway}}}} so the approach matches the runway in use: {template!r}"
    )
