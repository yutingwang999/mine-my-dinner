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


def settled(diner_ids, headcount, guest_names=None, meal_rules=None) -> dict:
    return {
        "diner_ids": diner_ids,
        "meal_rules": meal_rules or [],
        "guest_names": guest_names or {},
        "others": 0,
        "others_answer": None,
        "headcount": headcount,
    }


def test_card_not_needed_when_settled_covers():
    # Settled: the user (1) and 小王 (7); the follow-up "换个辣的" names no one.
    follow_up = ParsedRequest(query="辣的")
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


def test_card_not_needed_when_only_the_user_eats_even_with_a_settled_set():
    alone = ParsedRequest(headcount=1)
    assert not card_needed(alone, [], [], settled([1, 7], 2), {7}, self_id=1)


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
