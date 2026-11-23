"""The picker (plan 3b-2 spec 5.5): its answer schema, its input and the code that checks and
applies its answer against the frame. Pure code: hand-built frames and facts, no model call."""

from minemydinner.agent.frame import Choice, Frame, Slot
from minemydinner.agent.picker import (
    PICK_MAX_ATTEMPTS,
    ChoiceAnswer,
    PickerAnswer,
    SlotAnswer,
    apply_picks,
    choice_slots,
    conditions_line,
    must_use_misses,
    picker_messages,
)
from minemydinner.agent.planner import PICK_SEARCH_LIMIT, PICK_TOOLS_VERSION, TOOLS_VERSION
from minemydinner.agent.revision import DishFacts
from minemydinner.planning.validate import PlanSpec
from tests.recipefakes import seeded  # noqa: F401

# The current plan: two meat dishes, two vegetable dishes and a soup; 60 to 64 are library
# dishes the picker may choose.
FACTS = {
    1: DishFacts("宫保鸡丁", ["鸡肉"], ["荤菜"], "meat dish"),
    2: DishFacts("蒜蓉西兰花", ["西兰花"], ["素菜"], "vegetable dish"),
    3: DishFacts("番茄蛋汤", ["番茄"], ["汤"], "soup"),
    4: DishFacts("红烧肉", ["猪肉"], ["荤菜"], "meat dish"),
    5: DishFacts("清炒菠菜", ["菠菜"], ["素菜"], "vegetable dish"),
    50: DishFacts("辣子鸡", ["鸡肉"], ["荤菜"], "meat dish"),
    60: DishFacts("凉拌黄瓜", ["黄瓜"], ["素菜"], "vegetable dish"),
    61: DishFacts("糖醋排骨", ["排骨"], ["荤菜"], "meat dish"),
    62: DishFacts("紫菜蛋花汤", ["紫菜"], ["汤"], "soup"),
    63: DishFacts("手撕包菜", ["包菜"], ["素菜"], "vegetable dish"),
    64: DishFacts("水煮牛肉", ["牛肉"], ["荤菜"], "meat dish"),
}
PLAN = [1, 2, 3, 4, 5]


def _frame(**over) -> Frame:
    fields = {
        "keep": [i for i in PLAN],
        "drops": [],
        "slots": [],
        "choices": [],
        "placed": [],
        "missing": [],
        "empty_plan": False,
        "ignored": [],
    }
    fields.update(over)
    handled = set(fields["drops"]) | {s.replaces for s in fields["slots"] if s.replaces}
    if "keep" not in over:
        fields["keep"] = [i for i in PLAN if i not in handled]
    return Frame(**fields)


def _answer(slots=(), choices=(), **over) -> PickerAnswer:
    return PickerAnswer(
        slots=[SlotAnswer(index=i, recipe_id=r) for i, r in slots],
        choices=[ChoiceAnswer(index=i, ids=list(ids)) for i, ids in choices],
        **over,
    )


REPLACE_VEG = Slot(kind="replace", dish_type="vegetable dish", replaces=2)
ADD_SOUP = Slot(kind="add", dish_type="soup")


def test_constants():
    assert PICK_MAX_ATTEMPTS == 2
    assert PICK_SEARCH_LIMIT == 2
    assert PICK_TOOLS_VERSION != TOOLS_VERSION


# What a valid answer does.


def test_a_filled_replace_slot_takes_the_dishs_place():
    outcome = apply_picks(PLAN, _frame(slots=[REPLACE_VEG]), _answer([(0, 60)]), FACTS)
    assert outcome.problems == []
    assert outcome.recipe_ids == [1, 60, 3, 4, 5]
    assert outcome.unfilled == []


def test_an_unfilled_replace_keeps_the_dish():
    outcome = apply_picks(PLAN, _frame(slots=[REPLACE_VEG]), _answer([(0, None)]), FACTS)
    assert outcome.problems == []
    assert outcome.recipe_ids == PLAN
    assert outcome.unfilled == [REPLACE_VEG]


def test_an_unfilled_breaks_rule_slot_removes_the_dish():
    slot = Slot(kind="replace", dish_type="meat dish", replaces=4, reason="breaks_rule")
    outcome = apply_picks(PLAN, _frame(slots=[slot]), _answer([(0, None)]), FACTS)
    assert outcome.problems == []
    assert outcome.recipe_ids == [1, 2, 3, 5]
    assert outcome.unfilled == [slot]


def test_a_filled_add_grows_the_plan():
    outcome = apply_picks(PLAN, _frame(slots=[ADD_SOUP]), _answer([(0, 62)]), FACTS)
    assert outcome.problems == []
    assert outcome.recipe_ids == [*PLAN, 62]


def test_an_unfilled_add_adds_nothing():
    outcome = apply_picks(PLAN, _frame(slots=[ADD_SOUP]), _answer([(0, None)]), FACTS)
    assert outcome.problems == [] and outcome.recipe_ids == PLAN
    assert outcome.unfilled == [ADD_SOUP]


def test_drops_and_placed_dishes_are_applied():
    frame = _frame(drops=[3, 1], placed=[50])
    outcome = apply_picks(PLAN, frame, _answer(), FACTS)
    assert outcome.problems == []
    assert outcome.recipe_ids == [2, 4, 5, 50]


def test_a_drop_choice_drops_exactly_the_chosen_dishes():
    choice = Choice(dish_type="meat dish", candidates=[1, 4], up_to=1, mode="drop")
    outcome = apply_picks(PLAN, _frame(choices=[choice]), _answer(choices=[(0, [4])]), FACTS)
    assert outcome.problems == []
    assert outcome.recipe_ids == [1, 2, 3, 5]


def test_a_replace_choice_makes_slots_of_the_chosen_dishes_types():
    choice = Choice(dish_type=None, candidates=[1, 2, 4, 5], up_to=2, mode="replace")
    frame = _frame(slots=[ADD_SOUP], choices=[choice])
    answer = _answer([(0, 62), (1, 61), (2, 63)], choices=[(0, [4, 5])])
    assert choice_slots(frame, answer, FACTS) == [
        ADD_SOUP,
        Slot(kind="replace", dish_type="meat dish", replaces=4),
        Slot(kind="replace", dish_type="vegetable dish", replaces=5),
    ]
    outcome = apply_picks(PLAN, frame, answer, FACTS)
    assert outcome.problems == []
    assert outcome.recipe_ids == [1, 2, 3, 61, 63, 62]


def test_an_unfilled_choice_slot_keeps_the_chosen_dish():
    choice = Choice(dish_type=None, candidates=[1, 2, 4, 5], up_to=2, mode="replace")
    answer = _answer([(0, None)], choices=[(0, [1])])
    outcome = apply_picks(PLAN, _frame(choices=[choice]), answer, FACTS)
    assert outcome.problems == []
    assert outcome.recipe_ids == PLAN
    assert outcome.unfilled == [Slot(kind="replace", dish_type="meat dish", replaces=1)]


# Problems: an answer with any problem is never applied (Review Focus 4).


def _rejected(frame, answer, *words):
    outcome = apply_picks(PLAN, frame, answer, FACTS)
    assert outcome.problems, "expected a problem"
    assert outcome.recipe_ids == PLAN, "a rejected answer must not change the plan"
    assert outcome.unfilled == []
    text = " ".join(outcome.problems)
    for word in words:
        assert word in text, text
    return outcome


def test_a_slot_filled_with_a_dish_already_in_the_plan_is_a_problem():
    _rejected(_frame(slots=[REPLACE_VEG]), _answer([(0, 5)]), "already in the plan")
    # Also a dish the frame drops or replaces, and a dish code placed.
    _rejected(_frame(slots=[REPLACE_VEG]), _answer([(0, 2)]), "already in the plan")
    frame = _frame(slots=[Slot(kind="add", dish_type="meat dish")], placed=[50])
    _rejected(frame, _answer([(0, 50)]), "already in the plan")


def test_a_slot_filled_with_a_dish_of_the_wrong_type_is_a_problem():
    _rejected(_frame(slots=[REPLACE_VEG]), _answer([(0, 61)]), "meat dish", "vegetable dish")


def test_a_slot_filled_with_an_unknown_dish_is_a_problem():
    _rejected(_frame(slots=[REPLACE_VEG]), _answer([(0, 999)]), "999")


def test_the_same_dish_picked_twice_is_a_problem():
    slots = [ADD_SOUP, Slot(kind="add", dish_type="soup")]
    _rejected(_frame(slots=slots), _answer([(0, 62), (1, 62)]), "twice")


def test_more_choices_than_allowed_is_a_problem():
    choice = Choice(dish_type=None, candidates=[1, 2, 4, 5], up_to=2, mode="replace")
    answer = _answer([(0, 61), (1, 60), (2, 63)], choices=[(0, [1, 2, 5])])
    _rejected(_frame(choices=[choice]), answer, "at most 2")


def test_a_drop_choice_needs_exactly_its_count():
    choice = Choice(dish_type="meat dish", candidates=[1, 4], up_to=1, mode="drop")
    _rejected(_frame(choices=[choice]), _answer(choices=[(0, [1, 4])]), "exactly 1")
    _rejected(_frame(choices=[choice]), _answer(choices=[(0, [])]), "exactly 1")
    _rejected(_frame(choices=[choice]), _answer(), "Choice 0")


def test_a_replace_choice_needs_at_least_one_dish():
    choice = Choice(dish_type=None, candidates=[1, 2], up_to=1, mode="replace")
    _rejected(_frame(choices=[choice]), _answer(choices=[(0, [])]), "at least 1")


def test_a_choice_outside_its_candidates_is_a_problem():
    choice = Choice(dish_type="meat dish", candidates=[1, 4], up_to=1, mode="drop")
    _rejected(_frame(choices=[choice]), _answer(choices=[(0, [2])]), "not one of")


def test_one_dish_chosen_by_two_choices_is_a_problem():
    wish = Choice(dish_type=None, candidates=[1, 2, 4, 5], up_to=2, mode="replace")
    count = Choice(dish_type="meat dish", candidates=[1, 4], up_to=1, mode="drop")
    answer = _answer([(0, 61)], choices=[(0, [4]), (1, [4])])
    _rejected(_frame(choices=[wish, count]), answer, "twice")


def test_unknown_or_repeated_indexes_and_a_missing_slot_answer_are_problems():
    frame = _frame(slots=[REPLACE_VEG])
    _rejected(frame, _answer([(0, 60), (3, 63)]), "Slot 3")
    _rejected(frame, _answer([(0, 60), (0, 63)]), "Slot 0")
    _rejected(frame, _answer(), "Slot 0")
    _rejected(frame, _answer([(0, 60)], choices=[(0, [1])]), "Choice 0")


# The picker's input.


def test_picker_messages_show_slot_types_and_limits():
    frame = _frame(
        slots=[REPLACE_VEG, ADD_SOUP],
        drops=[3],
        placed=[50],
        choices=[
            Choice(dish_type="meat dish", candidates=[1, 4], up_to=1, mode="drop"),
            Choice(dish_type=None, candidates=[1, 4, 5], up_to=2, mode="replace"),
        ],
    )
    messages = picker_messages(
        frame,
        "Current plan:\n- 1 宫保鸡丁 (meat dish; 鸡肉)",
        "Diners:\n- Diner 1: no peanuts",
        "想吃点清淡的",
        "Shortlist:\n- 60 凉拌黄瓜",
        "zh",
    )
    assert len(messages) == 1
    text = messages[0].content
    for part in (
        "Shortlist:\n- 60 凉拌黄瓜",
        "Current plan:",
        "Diner 1: no peanuts",
        "想吃点清淡的",
        "Slot 0: a vegetable dish in place of 2",
        "Slot 1: a soup, added",
        "Choice 0: drop exactly 1 of 1, 4 (meat dish)",
        "Choice 1: replace 1 to 2 of 1, 4, 5 (any type)",
        "numbered from 2",
        "Leaving the plan: 3",
        "Added by name: 50",
        "Reply locale: zh",
    ):
        assert part in text, part


def test_picker_messages_without_a_wish_or_choices():
    text = picker_messages(_frame(slots=[ADD_SOUP]), "P", "R", "", "S", "en")[0].content
    assert "The user's wish: none" in text
    assert "Choice" not in text and "numbered from" not in text
    assert "Meal conditions" not in text


# A must-use ingredient no dish uses (spec 5.4): a replace choice made for it.

MUST_TOFU = Choice(
    dish_type=None, candidates=[1, 2, 4, 5], up_to=1, mode="replace", must_use=["豆腐"]
)
WISH = Choice(dish_type=None, candidates=[1, 2, 4, 5], up_to=2, mode="replace")


def test_picker_messages_name_the_must_use_ingredient_and_the_conditions():
    spec = PlanSpec(
        dishes=2, meat_dishes=1, vegetable_dishes=1, soups=1, mode="fridge_only",
        time_limit_min=30, must_use=["豆腐"],
    )
    text = picker_messages(
        _frame(choices=[MUST_TOFU]), "P", "R", "", "S", "zh", conditions=conditions_line(spec)
    )[0].content
    assert (
        "- Choice 0: replace 1 to 1 of 1, 2, 4, 5 (any type); each dish you choose becomes a "
        "new slot of its own dish type; the new dish must use 豆腐, which no dish uses yet"
    ) in text
    assert (
        "Meal conditions: fridge only (new dishes use what is at home); time limit: 30 "
        "minutes; must use in at least one dish: 豆腐"
    ) in text
    loose = PlanSpec(dishes=1, soups=0, mode="shopping")
    assert conditions_line(loose) == (
        "Meal conditions: shopping allowed; time limit: none; must use in at least one dish: "
        "nothing"
    )


def test_a_choice_slot_carries_the_must_use_ingredient():
    answer = _answer([(0, 61)], choices=[(0, [4])])
    [slot] = choice_slots(_frame(choices=[MUST_TOFU]), answer, FACTS)
    assert slot.replaces == 4 and slot.must_use == ["豆腐"]


def test_a_must_use_pick_that_leaves_the_ingredient_unused_is_a_problem():
    frame = _frame(choices=[WISH, MUST_TOFU])
    # The wish choice makes slots 0 and 1; the must-use choice's dish is slot 2.
    answer = _answer([(0, 60), (1, 64), (2, 61)], choices=[(0, [2, 1]), (1, [4])])
    problems, bad = must_use_misses(frame, answer, {"豆腐"})
    assert bad == {61}
    [problem] = problems
    assert "Choice 1" in problem and "豆腐" in problem and "61" in problem
    # The plan uses it (another new dish does): no problem.
    assert must_use_misses(frame, answer, set()) == ([], set())
    # Nothing fits ("null") is an answer, not a problem: the replaced dish stays.
    empty = _answer([(0, 60), (1, 64), (2, None)], choices=[(0, [2, 1]), (1, [4])])
    assert must_use_misses(frame, empty, {"豆腐"}) == ([], set())


def test_an_add_slot_for_a_must_use_ingredient_is_named_and_held_to_it():
    add = Slot(kind="add", dish_type="meat dish", for_count=True, must_use=["豆腐"])
    frame = _frame(slots=[REPLACE_VEG, add])
    text = picker_messages(frame, "P", "R", "", "S", "zh")[0].content
    assert "- Slot 1: a meat dish, added; it must use 豆腐, which no dish uses yet" in text
    problems, bad = must_use_misses(frame, _answer([(0, 60), (1, 61)]), {"豆腐"})
    assert bad == {61} and len(problems) == 1 and problems[0].startswith("Slot 1 is for 豆腐")
    # The other slot is never held to it, and a null add is not a problem.
    assert must_use_misses(frame, _answer([(0, 60), (1, None)]), {"豆腐"}) == ([], set())


def test_an_unused_must_use_name_without_a_choice_is_never_a_problem():
    # A name the plan missed before this turn has no choice: the picker is not held to it.
    answer = _answer([(0, 60), (1, 64)], choices=[(0, [2, 1])])
    assert must_use_misses(_frame(choices=[WISH]), answer, {"豆腐"}) == ([], set())


# The picker agent (planner.py).


def _picker_env(tmp_path, session, monkeypatch):
    from sqlalchemy import delete

    from minemydinner.agent import planner as planner_module
    from minemydinner.agent.graph import GraphDeps
    from minemydinner.agent.planner import build_picker
    from minemydinner.agent.records import PlanningRecorder
    from minemydinner.models import LlmCall
    from tests.agentfakes import ScriptedChatModel, make_llm_client
    from tests.planningfakes import rollback_session_factory

    session.execute(delete(LlmCall))
    seen: dict = {}
    real = planner_module.create_agent

    def capture(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(planner_module, "create_agent", capture)
    primary = ScriptedChatModel()
    factory = rollback_session_factory(session)
    client = make_llm_client(tmp_path, factory, primary, ScriptedChatModel())
    recorder = PlanningRecorder(factory)
    picker = build_picker(GraphDeps(factory, client, recorder), prompt="PICK PROMPT")
    return picker, primary, recorder, factory, client, seen


def test_build_picker_searches_only_within_its_limits(tmp_path, session, seeded, monkeypatch):
    from minemydinner.agent.planner import PICK_MODEL_CALL_LIMIT

    *_, seen = _picker_env(tmp_path, session, monkeypatch)
    assert [t.name for t in seen["tools"]] == ["search_recipes"]
    assert seen["system_prompt"] == "PICK PROMPT"
    assert seen["response_format"].schema is PickerAnswer
    limits = {type(m).__name__: m for m in seen["middleware"]}
    assert limits["ModelCallLimitMiddleware"].run_limit == PICK_MODEL_CALL_LIMIT == 3
    assert limits["ToolCallLimitMiddleware"].run_limit == PICK_SEARCH_LIMIT
    assert limits["CallLayerMiddleware"].step == "pick"


def test_the_picker_runs_at_most_two_searches(tmp_path, session, seeded, monkeypatch):
    from langchain_core.messages import HumanMessage

    from minemydinner.agent.planner import PICK_MODEL_CALL_LIMIT
    from minemydinner.agent.records import load_run
    from minemydinner.agent.request import PROMPT_VERSION
    from minemydinner.planning.context import MealConstraints
    from minemydinner.planning.tools import PlannerDeps
    from tests.agentfakes import tool_call

    picker, primary, recorder, factory, client, _ = _picker_env(tmp_path, session, monkeypatch)
    primary.replies = [tool_call("search_recipes", {})]
    run_id = recorder.start("t", {}, [], PROMPT_VERSION, PICK_TOOLS_VERSION, False)
    context = PlannerDeps(factory, client, MealConstraints(diner_ids=()), "fridge_only")
    out = picker.invoke({"messages": [HumanMessage("pick")], "run_id": run_id}, context=context)
    assert len(primary.sent) == PICK_MODEL_CALL_LIMIT
    assert out.get("structured_response") is None
    session.expire_all()
    ran = [e for e in load_run(session, run_id).events if e["kind"] == "tool_call"]
    assert len(ran) == PICK_SEARCH_LIMIT


def test_the_picker_answers_with_a_picker_answer(tmp_path, session, seeded, monkeypatch):
    from langchain_core.messages import HumanMessage

    from minemydinner.agent.request import PROMPT_VERSION
    from minemydinner.planning.context import MealConstraints
    from minemydinner.planning.tools import PlannerDeps
    from tests.agentfakes import tool_call

    picker, primary, recorder, factory, client, _ = _picker_env(tmp_path, session, monkeypatch)
    args = {"choices": [], "slots": [{"index": 0, "recipe_id": 60}], "note": "清淡。"}
    primary.replies = [tool_call("PickerAnswer", args)]
    run_id = recorder.start("t", {}, [], PROMPT_VERSION, PICK_TOOLS_VERSION, False)
    context = PlannerDeps(factory, client, MealConstraints(diner_ids=()), "fridge_only")
    out = picker.invoke({"messages": [HumanMessage("pick")], "run_id": run_id}, context=context)
    assert out["structured_response"] == _answer([(0, 60)], note="清淡。")
