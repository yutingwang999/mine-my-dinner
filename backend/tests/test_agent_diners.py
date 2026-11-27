from minemydinner.agent.diners import (
    DIET_WORDS,
    RESTRICTION_WORDS,
    ProposedRule,
    build_card,
    card_needed,
    stated_to_rules,
)
from minemydinner.agent.labels import LabelTable
from minemydinner.agent.request import ParsedRequest, StatedRules
from minemydinner.models import Diner
from tests.planningfakes import rule, self_diner
from tests.recipefakes import by_zh, seeded  # noqa: F401


def friend(session, name: str) -> Diner:
    diner = Diner(display_name=name, is_self=False)
    session.add(diner)
    session.flush()
    return diner


def peanut() -> ProposedRule:
    return ProposedRule(rule_type="allergy", allergen="peanut")


def test_word_lists():
    assert "过敏" in RESTRICTION_WORDS and "avoid" in RESTRICTION_WORDS
    assert set(DIET_WORDS) == {"吃素", "素食", "全素", "vegetarian", "vegan"}


def test_stated_to_rules_matches_dictionary(session, seeded):
    stated = StatedRules(
        allergens=["peanut"],
        avoid=[
            {"name": "羊肉", "kind": "restriction"},
            {"name": "某某酱", "kind": "dislike"},
        ],
        diet_words=["吃素"],
    )
    rules = stated_to_rules(session, stated)
    mutton = by_zh(session, "羊肉")
    assert rules == [
        ProposedRule(rule_type="allergy", allergen="peanut"),
        ProposedRule(
            rule_type="dietary restriction",
            ingredient_id=mutton.id,
            ingredient_name="羊肉",
        ),
        ProposedRule(rule_type="dislike", text="某某酱", by_wording=True),
    ]


def test_stated_to_rules_never_turns_a_diet_word_into_a_rule(session, seeded):
    stated = StatedRules(avoid=[{"name": "素食", "kind": "restriction"}], diet_words=["素食"])
    assert stated_to_rules(session, stated) == []


# ---- when the card is needed ----


def test_card_not_needed_alone():
    assert not card_needed(ParsedRequest(), [], [], None, set())
    assert not card_needed(ParsedRequest(headcount=1), [], [], None, set())


def test_card_needed_for_headcount_two():
    assert card_needed(ParsedRequest(headcount=2), [], [], None, set())
    assert card_needed(ParsedRequest(others_mentioned=True), [], [], None, set())


def test_card_needed_for_guest():
    parsed = ParsedRequest(guests=[{"name": "Alex"}])
    assert card_needed(parsed, [], [], None, set())


def test_card_needed_for_matched_friend():
    assert card_needed(ParsedRequest(), [7], [], None, {7})


def test_card_needed_for_new_self_rule_alone():
    assert card_needed(ParsedRequest(headcount=1), [], [peanut()], None, set())


SAME = object()


def settled(diner_ids, headcount, guest_names=None, meal_rules=None, stated=SAME) -> dict:
    """A settled set as stored; `stated` is the headcount the parse gave when it was
    confirmed (by default the card's total; None: the parse gave none)."""
    return {
        "diner_ids": diner_ids,
        "meal_rules": meal_rules or [],
        "guest_names": guest_names or {},
        "others": 0,
        "others_answer": None,
        "headcount": headcount,
        "stated_headcount": headcount if stated is SAME else stated,
    }


def test_card_not_needed_when_settled_covers():
    # Settled: the user (1) and 小王 (7); the follow-up "换个辣的" names no one, and its parse
    # (which reads the conversation) still has others mentioned.
    follow_up = ParsedRequest(query="辣的", others_mentioned=True)
    assert not card_needed(follow_up, [], [], settled([1, 7], 2), {7}, self_id=1)
    # 小王 named again, with the same headcount, is still covered.
    again = ParsedRequest(headcount=2, others_mentioned=True)
    assert not card_needed(again, [7], [], settled([1, 7], 2), {7}, self_id=1)


def test_card_needed_when_settled_does_not_cover():
    known = settled([1, 7], 2, guest_names={"guest:1": "Alex"})
    assert card_needed(ParsedRequest(), [8], [], known, {7, 8}, self_id=1)
    assert card_needed(ParsedRequest(headcount=4), [], [], known, {7}, self_id=1)
    new_guest = ParsedRequest(guests=[{"name": "Bob"}])
    assert card_needed(new_guest, [], [], known, {7}, self_id=1)
    same_guest = ParsedRequest(guests=[{"name": "alex"}])
    assert not card_needed(same_guest, [], [], known, {7}, self_id=1)
    assert card_needed(
        same_guest, [], [], known, {7}, self_id=1, uncovered_rules=[peanut()]
    )


def test_card_needed_when_settled_friend_deleted():
    assert card_needed(ParsedRequest(), [], [], settled([1, 7], 2), set(), self_id=1)


def test_card_needed_when_only_the_user_eats_after_a_shared_set():
    # "就我自己吃" after {me, 小王} changes who eats: the card asks (decided 2026-11-18).
    alone = ParsedRequest(headcount=1)
    assert card_needed(alone, [], [], settled([1, 7], 2, stated=2), {7}, self_id=1)


def test_card_compares_with_the_headcount_stated_when_the_set_was_confirmed():
    # "我和朋友" (2) confirmed with the total edited to 3: a follow-up repeating 2 is no change.
    edited = settled([1, 7], 3, stated=2)
    assert not card_needed(ParsedRequest(headcount=2), [], [], edited, {7}, self_id=1)
    assert card_needed(ParsedRequest(headcount=3), [], [], edited, {7}, self_id=1)
    # No headcount in the follow-up (others still mentioned): nothing to compare.
    assert not card_needed(ParsedRequest(others_mentioned=True), [], [], edited, {7}, self_id=1)
    # "给小王做饭" (1) settled as {小王}: a follow-up with 1 that names 小王 again keeps it; one
    # that names nobody says nobody else eats, so the card asks (decided 2026-11-19).
    wang_only = settled([7], 1, stated=1)
    assert not card_needed(ParsedRequest(headcount=1), [7], [], wang_only, {7}, self_id=1)
    assert card_needed(ParsedRequest(headcount=1), [], [], wang_only, {7}, self_id=1)


def test_card_asks_for_a_stated_headcount_when_the_set_has_none():
    # `stated_headcount` None means the parse gave none when the set was confirmed: any stated
    # headcount differs from it, whatever the card's own total was.
    unstated = settled([1, 7], 3, stated=None)
    assert card_needed(ParsedRequest(headcount=3), [], [], unstated, {7}, self_id=1)
    assert card_needed(ParsedRequest(headcount=2), [], [], unstated, {7}, self_id=1)


# ---- what the card shows ----


def test_build_card_rows_and_preselection(session, seeded):
    me = self_diner(session)
    wang = friend(session, "小王")
    li = friend(session, "老李")
    rule(session, li.id, "dietary restriction", ingredient="牛肉")
    table = LabelTable.from_state(None, me.id)
    parsed = ParsedRequest(
        headcount=3, others_mentioned=True, guests=[{"name": "Alex", "allergens": ["peanut"]}]
    )
    card = build_card(session, parsed, table, [wang.id], "我和小王还有Alex，他对花生过敏")
    assert card["kind"] == "diners" and card["headcount"] == 3
    rows = card["rows"]
    assert [row["diner_id"] for row in rows] == [me.id, wang.id, li.id]
    by_id = {row["diner_id"]: row for row in rows}
    assert rows[0]["is_self"] and rows[0]["name"] is None and rows[0]["selected"]
    assert by_id[wang.id]["selected"] and by_id[wang.id]["name"] == "小王"
    assert not by_id[li.id]["selected"]
    assert [r["ingredient_name_zh"] for r in by_id[li.id]["rules"]] == ["牛肉"]
    (guest,) = card["guests"]
    assert guest["name"] == "Alex"
    assert guest["proposed"] == [peanut().model_dump()]
    assert card["others"] == 0 and card["nudge"] is False


def test_build_card_friends_sorted_by_name(session, seeded):
    me = self_diner(session)
    bob = friend(session, "Bob")
    amy = friend(session, "Amy")
    card = build_card(session, ParsedRequest(headcount=2), LabelTable.from_state(None, me.id), [], "")
    assert [row["diner_id"] for row in card["rows"]] == [me.id, amy.id, bob.id]
    # Only the user is preselected: one more person is not matched.
    assert card["others"] == 1


def test_build_card_nudge(session, seeded):
    me = self_diner(session)
    table = LabelTable.from_state(None, me.id)
    vague = ParsedRequest(headcount=2, others_mentioned=True, guests=[{"name": None}])
    card = build_card(session, vague, table, [], "我朋友不吃那个")
    assert card["nudge"] is True
    stated = ParsedRequest(
        headcount=2,
        others_mentioned=True,
        guests=[{"name": None, "avoid": [{"name": "羊肉", "kind": "restriction"}]}],
    )
    assert build_card(session, stated, table, [], "我朋友不吃羊肉")["nudge"] is False
    # No restriction word: no nudge.
    assert build_card(session, vague, table, [], "我和朋友吃饭")["nudge"] is False


def test_build_card_shows_stated_friend_and_self_rules(session, seeded):
    me = self_diner(session)
    wang = friend(session, "小王")
    table = LabelTable.from_state(None, me.id)
    table.label_for(f"diner:{wang.id}")
    parsed = ParsedRequest(
        headcount=2,
        friend_rules=[{"code": "Diner 2", "avoid": [{"name": "羊肉", "kind": "restriction"}]}],
        self_rules={"allergens": ["peanut"], "diet_words": []},
    )
    card = build_card(session, parsed, table, [wang.id], "")
    by_id = {row["diner_id"]: row for row in card["rows"]}
    assert by_id[me.id]["proposed"] == [peanut().model_dump()]
    assert [p["ingredient_name"] for p in by_id[wang.id]["proposed"]] == ["羊肉"]


def test_build_card_leaves_out_rules_already_saved(session, seeded):
    me = self_diner(session)
    wang = friend(session, "小王")
    rule(session, wang.id, "allergy", allergen="peanut")
    table = LabelTable.from_state(None, me.id)
    table.label_for(f"diner:{wang.id}")
    parsed = ParsedRequest(
        headcount=2, friend_rules=[{"code": "Diner 2", "allergens": ["peanut"]}]
    )
    card = build_card(session, parsed, table, [wang.id], "小王对花生过敏")
    by_id = {row["diner_id"]: row for row in card["rows"]}
    assert by_id[wang.id]["proposed"] == []
    assert [r["allergen"] for r in by_id[wang.id]["rules"]] == ["peanut"]


def test_build_card_solo_self_rule_shows_only_the_user(session, seeded):
    me = self_diner(session)
    friend(session, "小王")
    parsed = ParsedRequest(self_rules={"allergens": ["peanut"]})
    card = build_card(session, parsed, LabelTable.from_state(None, me.id), [], "我对花生过敏")
    assert [row["diner_id"] for row in card["rows"]] == [me.id]
    assert card["rows"][0]["proposed"] == [peanut().model_dump()]
    assert card["headcount"] == 1 and card["others"] == 0 and card["guests"] == []


def test_build_card_headcount_when_others_are_mentioned_without_a_number(session, seeded):
    me = self_diner(session)
    parsed = ParsedRequest(others_mentioned=True)
    card = build_card(session, parsed, LabelTable.from_state(None, me.id), [], "我和朋友吃饭")
    assert card["headcount"] == 2 and card["others"] == 1


# ---- applying the answer (spec 5.3, 5.4) ----


def answer(**kwargs):
    from minemydinner.agent.diners import DinerAnswer

    return DinerAnswer.model_validate(kwargs)


def profile(session, diner_id: int) -> list[tuple]:
    from sqlalchemy import select

    from minemydinner.models import DietaryRule

    session.expire_all()
    rows = session.scalars(
        select(DietaryRule).where(DietaryRule.diner_id == diner_id).order_by(DietaryRule.id)
    )
    return [(row.rule_type, row.allergen, row.ingredient_id, row.text) for row in rows]


PEANUT = {"rule_type": "allergy", "allergen": "peanut"}


def test_apply_answer_meal_only_rule_not_saved(session, seeded):
    from minemydinner.agent.diners import apply_answer
    from minemydinner.planning.context import RuleRef

    me = self_diner(session)
    wang = friend(session, "小王")
    table = LabelTable.from_state(None, me.id)
    settled = apply_answer(
        session, answer(selected=[me.id, wang.id], stated={wang.id: [PEANUT]}), table
    )
    assert settled.diner_ids == [me.id, wang.id]
    assert settled.meal_rules == [RuleRef(f"diner:{wang.id}", "allergy", "peanut", None, None)]
    assert profile(session, wang.id) == []
    assert settled.headcount == 2 and settled.guest_names == {}
    # A selected friend gets a label in the conversation's table.
    assert table.labels[f"diner:{wang.id}"] == "Diner 2"


def test_apply_answer_save_rule_writes_profile(session, seeded):
    from minemydinner.agent.diners import apply_answer

    me = self_diner(session)
    wang = friend(session, "小王")
    table = LabelTable.from_state(None, me.id)
    chosen = answer(selected=[me.id, wang.id], stated={wang.id: [{**PEANUT, "save": True}]})
    settled = apply_answer(session, chosen, table)
    assert profile(session, wang.id) == [("allergy", "peanut", None, None)]
    assert settled.meal_rules == []
    # Saving the same rule again does not duplicate it.
    apply_answer(session, chosen, table)
    assert profile(session, wang.id) == [("allergy", "peanut", None, None)]


def test_apply_answer_saves_guest_as_friend(session, seeded):
    from sqlalchemy import select

    from minemydinner.agent.diners import apply_answer

    me = self_diner(session)
    table = LabelTable.from_state(None, me.id)
    chosen = answer(
        selected=[me.id], guests=[{"name": "Alex", "rules": [PEANUT], "save": True}]
    )
    settled = apply_answer(session, chosen, table)
    alex = session.scalars(select(Diner).where(Diner.display_name == "Alex")).one()
    assert not alex.is_self
    assert profile(session, alex.id) == [("allergy", "peanut", None, None)]
    assert settled.diner_ids == [me.id, alex.id]
    assert settled.meal_rules == [] and settled.guest_names == {}
    assert settled.headcount == 2


def test_apply_answer_guest_rules_for_this_meal(session, seeded):
    from minemydinner.agent.diners import apply_answer
    from minemydinner.planning.context import RuleRef

    me = self_diner(session)
    table = LabelTable.from_state(None, me.id)
    # A guest settled earlier in the thread keeps guest:1.
    table.label_for("guest:1")
    table.names["guest:1"] = "Bob"
    settled = apply_answer(
        session,
        answer(selected=[me.id], guests=[{"name": "Alex", "rules": [PEANUT]}, {"name": None}]),
        table,
    )
    assert settled.guest_names == {"guest:2": "Alex", "guest:3": None}
    assert settled.meal_rules == [RuleRef("guest:2", "allergy", "peanut", None, None)]
    assert table.names["guest:2"] == "Alex" and table.labels["guest:2"] == "Diner 3"
    assert "guest:3" in table.labels
    assert settled.headcount == 3


def test_apply_answer_user_not_eating(session, seeded):
    from minemydinner.agent.diners import apply_answer, constraints_from
    from minemydinner.planning.context import RuleRef, load_planning_context

    me = self_diner(session)
    rule(session, me.id, "allergy", allergen="egg")
    table = LabelTable.from_state(None, me.id)
    settled = apply_answer(
        session, answer(selected=[], guests=[{"name": "Alex", "rules": [PEANUT]}]), table
    )
    assert settled.diner_ids == [] and settled.headcount == 1
    ctx = load_planning_context(session, constraints_from(settled))
    assert ctx.rules == (RuleRef("guest:1", "allergy", "peanut", None, None),)


def test_constraints_unsure_vs_none(session, seeded):
    from minemydinner.agent.diners import apply_answer, constraints_from

    me = self_diner(session)
    for others_answer, unconfirmed in (("unsure", 2), ("none", 0)):
        table = LabelTable.from_state(None, me.id)
        chosen = answer(
            selected=[me.id], guests=[{"name": "Alex"}], others=2, others_answer=others_answer
        )
        settled = apply_answer(session, chosen, table)
        assert settled.headcount == 4
        constraints = constraints_from(settled)
        assert constraints.unconfirmed_diners == unconfirmed
        assert constraints.diner_ids == (me.id,)


def test_settled_diners_state_round_trip(session, seeded):
    from minemydinner.agent.diners import SettledDiners, apply_answer

    me = self_diner(session)
    table = LabelTable.from_state(None, me.id)
    settled = apply_answer(
        session,
        answer(selected=[me.id], guests=[{"name": "Alex", "rules": [PEANUT]}], others=1,
               others_answer="unsure"),
        table,
        stated_headcount=2,
    )
    data = settled.to_state()
    assert set(data) == {
        "diner_ids", "meal_rules", "guest_names", "others", "others_answer", "headcount",
        "stated_headcount",
    }
    assert data["stated_headcount"] == 2 and data["headcount"] == 3
    assert data["meal_rules"][0]["diner_key"] == "guest:1"
    assert SettledDiners.from_state(data) == settled


def test_diner_answer_validation():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        answer(selected=[])  # nobody eats
    with pytest.raises(ValidationError):
        answer(selected=[1], others=-1)
    with pytest.raises(ValidationError):
        answer(selected=[1], guests=[{"name": None, "save": True}])  # save needs a name
    with pytest.raises(ValidationError):
        answer(selected=[1], stated={1: [{"rule_type": "allergy", "text": "芒果"}]})
    with pytest.raises(ValidationError):
        answer(selected=[1], stated={2: [PEANUT]})  # stated rules only for selected diners
    with pytest.raises(ValidationError):
        answer(selected=[1], stated={1: [{"rule_type": "dietary restriction", "text": "Vegan"}]})
    assert answer(selected=[], others=1).others_answer == "unsure"


def test_diner_answer_headcount_sets_the_unmatched_people():
    # The card's confirmed total: the unmatched people are what the selection does not cover.
    chosen = answer(selected=[1, 2], guests=[{"name": "Alex"}], others=7, headcount=5)
    assert chosen.others == 2 and chosen.others_answer == "unsure"
    assert answer(selected=[1], headcount=1, others=3).others == 0
    # Without a headcount (the evaluation runner, older clients) `others` is used as sent.
    assert answer(selected=[1], others=3).others == 3
    # A total below the people on the card is not applied here; the API refuses it.
    low = answer(selected=[1, 2], headcount=1)
    assert low.headcount_too_low and low.others == 0
    assert not chosen.headcount_too_low


def test_apply_answer_refuses_diet_word_text_rule(session, seeded):
    import pytest

    from minemydinner.agent.diners import DinerAnswer, GuestAnswer, RuleChoice, apply_answer

    me = self_diner(session)
    diet = RuleChoice.model_construct(
        rule_type="dietary restriction", allergen=None, ingredient_id=None, text="素食",
        save=False,
    )
    guest = GuestAnswer.model_construct(name="Alex", rules=[diet], save=False)
    chosen = DinerAnswer.model_construct(
        selected=[me.id], stated={}, guests=[guest], others=0, others_answer=None
    )
    with pytest.raises(ValueError):
        apply_answer(session, chosen, LabelTable.from_state(None, me.id))


def test_answer_summary_names_the_user_and_the_friends(session, seeded):
    from minemydinner.agent.diners import answer_summary

    me = self_diner(session)
    wang = friend(session, "小王")
    summary = answer_summary(session, answer(selected=[me.id, wang.id]))
    assert summary == {"self": True, "names": ["小王"], "others": 0}


def test_answer_summary_when_the_user_is_not_eating(session, seeded):
    from minemydinner.agent.diners import answer_summary

    self_diner(session)
    wang = friend(session, "小王")
    summary = answer_summary(session, answer(selected=[wang.id]))
    assert summary == {"self": False, "names": ["小王"], "others": 0}


# ---- final review fixes ----


def test_card_needed_when_headcount_one_names_a_friend_or_guest():
    # "给小王做饭" may parse as headcount 1; the matched friend still asks.
    assert card_needed(ParsedRequest(headcount=1), [7], [], None, {7})
    assert card_needed(ParsedRequest(headcount=1, guests=[{"name": "Alex"}]), [], [], None, set())
    assert card_needed(ParsedRequest(headcount=1, others_mentioned=True), [], [], None, set())


def test_card_needed_for_self_diet_words_without_a_settled_set():
    # "我吃素，今晚吃什么": no rule, but the solo card shows the diet-word hint (spec 5.4).
    alone = ParsedRequest(headcount=1)
    assert card_needed(alone, [], [], None, set(), self_diet_words=["吃素"])
    assert not card_needed(
        alone, [], [], settled([1], 1), set(), self_id=1, self_diet_words=["吃素"]
    )


def test_build_card_solo_diet_words_on_the_self_row(session, seeded):
    me = self_diner(session)
    friend(session, "小王")
    parsed = ParsedRequest(self_rules={"diet_words": ["吃素"]})
    card = build_card(session, parsed, LabelTable.from_state(None, me.id), [], "我吃素")
    (row,) = card["rows"]
    assert row["diner_id"] == me.id and row["diet_words"] == ["吃素"]
    assert row["proposed"] == []



# ---- "nobody else eats" after a shared set (decided by the user, 2026-11-19) ----


def test_card_when_the_parse_says_nobody_else_eats_after_a_shared_set():
    # "今晚我和朋友吃饭" settled {me, 小王} (no stated number); "就我自己吃" parsed with no
    # headcount, no guest, no one else mentioned: who eats changed, so the card asks.
    shared = settled([1, 7], 2)
    alone = ParsedRequest()
    assert card_needed(alone, [], [], shared, {7}, self_id=1)
    # The same for a set with a guest, or with unmatched people.
    with_guest = settled([1], 2, guest_names={"guest:1": "Alex"})
    assert card_needed(alone, [], [], with_guest, set(), self_id=1)
    with_others = {**settled([1], 2), "others": 1}
    assert card_needed(alone, [], [], with_others, set(), self_id=1)


def test_no_card_when_the_parse_still_mentions_others():
    # "换个辣的" whose parse carries others_mentioned (it reads the conversation).
    shared = settled([1, 7], 2, stated=2)
    assert not card_needed(ParsedRequest(others_mentioned=True), [], [], shared, {7}, self_id=1)


def test_no_card_for_a_solo_set_when_nobody_else_eats():
    # A solo set (the user alone, with a meal-only rule) is not affected by the rule.
    rule_ = {"diner_key": "diner:1", "rule_type": "allergy", "allergen": "peanut",
             "ingredient_id": None, "text": None}
    solo = settled([1], 1, meal_rules=[rule_], stated=1)
    assert not card_needed(ParsedRequest(), [], [], solo, set(), self_id=1)
    assert not card_needed(ParsedRequest(headcount=1), [], [], solo, set(), self_id=1)


def test_no_card_when_the_settled_friend_is_named_again():
    # {小王} alone (the user unticked); "再给小王做个汤" matches 小王.
    wang_only = settled([7], 1, stated=1)
    assert not card_needed(ParsedRequest(headcount=1), [7], [], wang_only, {7}, self_id=1)
    # A friend rule stated for a settled friend also says someone else eats.
    by_rule = ParsedRequest(friend_rules=[{"code": "Diner 2", "avoid": []}])
    assert not card_needed(by_rule, [7], [], wang_only, {7}, self_id=1)
