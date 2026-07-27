"""Picking the first enroute fix out of a filed route string.

The clearance grants the SID and the point it hands over to the enroute
structure — not the whole chain, which no pilot reads back and no STT survives.
"""

from pathlib import Path

from app.enroute_route import first_enroute_fix, resolve_enroute_clearance
from app.flow_loader import get_flow, load_all_flows
from app.session_store import create_session

FLOWS_DIR = Path(__file__).parent.parent / "flows"


def test_first_fix_follows_a_leading_sid():
    # SimBrief and VATSIM routes commonly repeat the SID before its exit point.
    assert (
        first_enroute_fix("SULUS5S SULUS Y101 ARMUT UM984 NATOR", sid="SULUS5S")
        == "SULUS"
    )


def test_first_fix_when_the_route_omits_the_sid():
    assert first_enroute_fix("BIBAX Y101 ARMUT", sid="BIBAX1N") == "BIBAX"


def test_airway_designators_are_not_fixes():
    # A route may open on the airway when the SID exit is implied.
    assert first_enroute_fix("Y101 ARMUT UM984 NATOR", sid="BIBAX1N") == "ARMUT"


def test_dct_filler_is_skipped():
    assert first_enroute_fix("DCT BIBAX Y101 ARMUT", sid="BIBAX1N") == "BIBAX"


def test_speed_and_level_group_is_skipped():
    assert (
        first_enroute_fix("N0450F360 SULUS5S SULUS Y101 ARMUT", sid="SULUS5S")
        == "SULUS"
    )


def test_unflown_sid_revision_is_still_recognised_as_a_procedure():
    # The filed SID and the one ATC assigned can differ; neither is a fix.
    assert first_enroute_fix("TOBAK2E TOBAK Y101 ARMUT", sid="SULUS5S") == "TOBAK"


def test_departure_and_destination_icao_are_not_fixes():
    assert (
        first_enroute_fix(
            "EDDF DCT BIBAX Y101 ARMUT DCT EDDM",
            sid="BIBAX1N",
            airports=("EDDF", "EDDM"),
        )
        == "BIBAX"
    )


def test_navaid_ident_counts_as_a_fix():
    assert first_enroute_fix("DCT FFM UM984 NATOR", sid="BIBAX1N") == "FFM"


def test_empty_route_has_no_fix():
    assert first_enroute_fix("", sid="BIBAX1N") is None
    assert first_enroute_fix("   ", sid="BIBAX1N") is None


def test_route_of_only_filler_has_no_fix():
    assert first_enroute_fix("DCT DCT", sid="BIBAX1N") is None


def test_lowercase_route_is_accepted():
    assert first_enroute_fix("dct bibax y101 armut", sid="BIBAX1N") == "BIBAX"


# ---------------------------------------------------------------------------
# Session variables
# ---------------------------------------------------------------------------


def test_clearance_variables_carry_the_fix_and_a_speakable_clause():
    assert resolve_enroute_clearance(
        "SULUS5S SULUS Y101 ARMUT", sid="SULUS5S"
    ) == {
        "enroute_fixes": ["SULUS"],
        "enroute_clause": ", then SULUS, flight planned route",
    }


def test_no_route_leaves_the_flow_defaults_alone():
    # Demo and manual flights file no route. Returning None keeps the YAML
    # initial values, which are the no-route wording.
    assert resolve_enroute_clearance("", sid="BIBAX1N") is None
    assert resolve_enroute_clearance("DCT DCT", sid="BIBAX1N") is None


# ---------------------------------------------------------------------------
# Derivation at session create
# ---------------------------------------------------------------------------


def _clearance_session(**overrides):
    load_all_flows(FLOWS_DIR)
    return create_session(get_flow("clearance"), variable_overrides=overrides)


def test_session_create_derives_the_fix_from_the_filed_route():
    session = _clearance_session(sid="SULUS5S", route="SULUS5S SULUS Y101 ARMUT")
    assert session.variables["enroute_fixes"] == ["SULUS"]
    assert session.variables["enroute_clause"] == ", then SULUS, flight planned route"


def test_session_create_without_a_route_keeps_the_no_route_defaults():
    session = _clearance_session(sid="BIBAX1N")
    assert session.variables["enroute_fixes"] == []
    assert session.variables["enroute_clause"] == ""


def test_session_create_excludes_the_aerodromes_from_the_route():
    session = create_session(
        get_flow("clearance"),
        variable_overrides={"sid": "BIBAX1N", "route": "EDDF DCT BIBAX Y101 ARMUT EDDM"},
        airport_icao="EDDF",
        destination_icao="EDDM",
    )
    assert session.variables["enroute_fixes"] == ["BIBAX"]


def test_a_caller_supplied_fix_is_not_overwritten():
    # The frontend can pin the clearance; a derived value must not clobber it.
    session = _clearance_session(
        sid="SULUS5S",
        route="SULUS5S SULUS Y101 ARMUT",
        enroute_fixes=["ARMUT"],
    )
    assert session.variables["enroute_fixes"] == ["ARMUT"]
