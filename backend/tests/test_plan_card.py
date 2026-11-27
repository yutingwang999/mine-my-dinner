from sqlalchemy import select

from minemydinner.agent.plan_card import build_dishes_card, card_base
from minemydinner.models import Diner
from minemydinner.planning.context import MealConstraints, load_planning_context
from tests.agentfakes import final_proposal
from tests.recipefakes import seeded  # noqa: F401
from tests.test_agent_graph import graph_env, meat, plan, veg  # noqa: F401


def _state(session, current: dict) -> dict:
    """A state shaped like the graph's, built from a plan the graph presented."""
    self_id = session.scalar(select(Diner.id).where(Diner.is_self))
    return {
        "defaults_used": current["defaults_used"],
        "spec": current["spec"],
        "constraints": {
            "headcount": current["headcount"],
            "diner_ids": [self_id] if self_id else [],
            "unconfirmed_diners": current["others"],
        },
        "search_notes": {
            "left_out_unknown_allergens": current["left_out_unknown_allergens"],
            "left_out_details": current["left_out_details"],
            "semantic_paused": current["semantic_paused"],
        },
    }


def _ctx(session, state: dict):
    return load_planning_context(
        session, MealConstraints(diner_ids=tuple(state["constraints"]["diner_ids"]))
    )


def test_build_dishes_card_matches_present_plan_shape(graph_env, veg, meat):  # noqa: F811
    session = graph_env.session
    parse = {"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1, "mode": "shopping"}
    out = plan(graph_env, "dinner", parse=parse, replies=[final_proposal([veg.id, meat.id])])
    current = out["current_plan"]
    assert current["passed"] and current["unmet"] == []
    state = _state(session, current)
    base = card_base(state, session)
    card = build_dishes_card(
        session,
        _ctx(session, state),
        recipe_ids=[veg.id, meat.id],
        validation={"passed": True, "warnings": current["warnings"], "violations": []},
        base=base,
        locale="en",
    )
    # present_plan adds the (empty) locks of a model plan and the request the card was built
    # from (plan 3b-1b spec 5.5); the builder does not.
    assert current["locked"] == [] and current["request"] == out["request"]
    assert card == {k: v for k, v in current.items() if k not in ("locked", "request")}


def test_build_dishes_card_failed_validation_lists_unmet(graph_env, veg):  # noqa: F811
    session = graph_env.session
    out = plan(graph_env, "dinner", replies=[final_proposal([veg.id])])
    state = _state(session, out["current_plan"])
    violation = {"code": "H4", "recipe_id": veg.id, "detail": {
        "required": {"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1},
        "present": {"vegetable dish": 1},
        "ranges": {"meat_dishes": [1, 1], "vegetable_dishes": [1, 1]},
    }}
    card = build_dishes_card(
        session,
        _ctx(session, state),
        recipe_ids=[veg.id],
        validation={"passed": False, "warnings": [], "violations": [violation]},
        base=card_base(state, session),
        locale="en",
    )
    assert card["passed"] is False
    assert [u["code"] for u in card["unmet"]] == ["H4"]
    assert card["unmet"][0]["recipe_id"] == veg.id
    assert card["next_actions"]
