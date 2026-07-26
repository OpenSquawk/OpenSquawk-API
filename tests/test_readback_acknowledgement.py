"""When a correct readback is acknowledged, and when silence is right.

Real controllers do not confirm every readback — silence after a correct one is
normal R/T, and a "readback correct" behind every transmission would be as
wrong as never sending one. What the reports actually describe is the pilot
being left hanging: the readback is accepted, nothing is said, and nothing
happens next either, so a correct readback is indistinguishable from not having
been heard.

The policy, applied across all flows:

1. If the pilot is left WAITING after the readback, the controller answers.
   A waiting state is one the pilot does not speak at of their own accord — it
   advances on telemetry or on a silence timeout (the takeoff roll, a climb to
   the cleared level, the landing roll), or the flow simply ends there.

2. If it is the PILOT's turn to speak next — requesting pushback, requesting
   taxi, reporting downwind — silence is correct and realistic. The pilot is not
   waiting on anything, and a confirmation would be phraseology no controller
   uses.

3. One response, not two. Where the controller's next instruction follows
   immediately, that instruction *is* the acknowledgement; a separate "readback
   correct" must not be stacked in front of it.

Enforced structurally so a new flow cannot reintroduce a silently-accepted
readback that leaves the pilot waiting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.flow_loader import load_all_flows

FLOWS_DIR = Path(__file__).parent.parent / "flows"

_FLOWS = load_all_flows(FLOWS_DIR)


def _readback_states():
    """(slug, state_id) for every pilot state that grades a readback."""
    found = []
    for slug, flow in sorted(_FLOWS.items()):
        for state_id, state in flow.states.items():
            if state.role == "pilot" and state.readback_required:
                found.append((slug, state_id))
    return found


def _first_controller_response(flow, start_id: str, max_hops: int = 6):
    """Follow the accepted path until something is said, or the pilot is up again.

    Returns the say_template of the first ATC state reached, or None when the
    path reaches a pilot state or an end state without anything being said.
    """
    state_id = start_id
    for _ in range(max_hops):
        state = flow.states.get(state_id)
        if state is None:
            return None
        if state.say_template:
            return state.say_template
        if state.role == "pilot":
            return None  # back to the pilot with nothing said
        # System/ATC state with no speech — keep following auto-transitions.
        if not state.auto_transitions:
            return None
        state_id = state.auto_transitions[0].to
    return None


def _leaves_pilot_waiting(flow, start_id: str, max_hops: int = 6) -> bool:
    """Does the accepted path park the pilot somewhere they don't speak next?

    True for a state that advances on its own (telemetry or a silence timeout)
    and for a flow end — in both cases nothing further will be said unless the
    controller says it. False when the pilot holds the next transmission.
    """
    state_id = start_id
    for _ in range(max_hops):
        state = flow.states.get(state_id)
        if state is None:
            return False
        if state_id in flow.end_states:
            return True
        if state.role == "pilot":
            has_timeout = bool(state.auto_advance_on_silence)
            has_telemetry = any(t.telemetry is not None for t in state.auto_transitions)
            return has_timeout or has_telemetry
        if not state.auto_transitions:
            return False
        state_id = state.auto_transitions[0].to
    return False


def test_there_are_readback_states_to_check():
    """Guards the discovery helper — a broken query would pass everything."""
    assert len(_readback_states()) >= 25


@pytest.mark.parametrize("slug,state_id", _readback_states())
def test_readback_that_leaves_the_pilot_waiting_is_answered(slug, state_id):
    flow = _FLOWS[slug]
    state = flow.states[state_id]
    assert state.ok_next, f"{slug}::{state_id} has no accepted path"
    target = state.ok_next[0].to

    if not _leaves_pilot_waiting(flow, target):
        return  # the pilot speaks next — silence is correct R/T

    say = _first_controller_response(flow, target)
    assert say, (
        f"{slug}::{state_id} accepts the readback in silence and then leaves the "
        f"pilot waiting at '{target}' — nothing further is said and nothing is "
        f"expected of them, so a correct readback is indistinguishable from not "
        f"being heard. Confirm it, or issue the next instruction."
    )


@pytest.mark.parametrize("slug,state_id", _readback_states())
def test_confirmation_does_not_stack_with_an_instruction(slug, state_id):
    """Rule 2: "readback correct" only where no instruction follows it."""
    flow = _FLOWS[slug]
    state = flow.states[state_id]
    say = _first_controller_response(flow, state.ok_next[0].to) or ""
    if "readback correct" not in say.lower():
        return

    # A confirmation may carry a handoff ("contact tower …"), which is the
    # controller releasing the pilot rather than a fresh instruction to fly.
    instruction_words = (
        "climb", "descend", "turn", "line up", "cleared for take",
        "cleared to land", "taxi to", "push back", "hold short",
    )
    lowered = say.lower()
    stacked = [w for w in instruction_words if w in lowered]
    assert not stacked, (
        f"{slug}::{state_id} answers with a confirmation AND an instruction "
        f"({stacked}) — the instruction alone is the acknowledgement: {say!r}"
    )
