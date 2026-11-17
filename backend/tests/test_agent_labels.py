from minemydinner.agent.labels import (
    SELF_LABEL,
    Friend,
    LabelContext,
    LabelTable,
    current_labels,
    find_friends,
    labels_scope,
    load_label_context,
    replace_nicknames,
    restore_labels,
)
from minemydinner.models import Diner
from minemydinner.planning.context import MealConstraints, RuleRef, diner_codes
from tests.planningfakes import make_recipe, self_diner
from tests.recipefakes import by_zh, seeded  # noqa: F401


def context(friends, food_terms=(), locale="zh", restore=True, table=None) -> LabelContext:
    return LabelContext(
        table=table or LabelTable.from_state(None, self_id=1),
        friends=list(friends),
        food_terms=frozenset(food_terms),
        locale=locale,
        restore=restore,
    )


def test_self_is_always_diner_1():
    table = LabelTable.from_state(None, self_id=5)
    assert SELF_LABEL == "Diner 1"
    assert table.label_for("diner:5") == "Diner 1"
    assert table.label_for("diner:7") == "Diner 2"
    assert table.label_for("diner:5") == "Diner 1"


def test_friend_gets_next_label_once():
    table = LabelTable.from_state(None, self_id=1)
    assert table.label_for("diner:2") == "Diner 2"
    assert table.label_for("diner:2") == "Diner 2"
    assert table.label_for("diner:3") == "Diner 3"


def test_replace_nicknames_cjk_and_latin():
    ctx = context([Friend(2, "小王"), Friend(3, "Lily")])
    with labels_scope(ctx):
        assert replace_nicknames("我和小王还有lily") == "我和Diner 2还有Diner 3"
    assert ctx.table.labels == {"diner:1": "Diner 1", "diner:2": "Diner 2", "diner:3": "Diner 3"}


def test_labels_follow_the_order_of_mention():
    ctx = context([Friend(2, "小王"), Friend(3, "Lily")])
    with labels_scope(ctx):
        assert replace_nicknames("Lily和小王") == "Diner 2和Diner 3"


def test_longest_nickname_first():
    with labels_scope(context([Friend(2, "王"), Friend(3, "小王")])):
        assert replace_nicknames("小王来") == "Diner 2来"
        assert replace_nicknames("小王和王") == "Diner 2和Diner 3"


def test_food_word_is_not_a_nickname():
    with labels_scope(context([Friend(2, "鱼")], food_terms={"鱼香肉丝"})):
        assert replace_nicknames("想吃鱼香肉丝") == "想吃鱼香肉丝"
        assert replace_nicknames("鱼也来，想吃鱼香肉丝") == "Diner 2也来，想吃鱼香肉丝"


def test_food_term_equal_to_the_nickname_does_not_block_it():
    with labels_scope(context([Friend(2, "鱼")], food_terms={"鱼"})):
        assert replace_nicknames("鱼也来") == "Diner 2也来"


def test_find_friends_returns_spans_without_overlap():
    wang, xiao_wang = Friend(2, "王"), Friend(3, "小王")
    found = find_friends("小王和王", [wang, xiao_wang], frozenset())
    assert sorted(found, key=lambda item: item[0]) == [(0, 2, xiao_wang), (3, 4, wang)]


def test_latin_whole_word_only():
    with labels_scope(context([Friend(2, "Al")])):
        assert replace_nicknames("Alex and Al") == "Alex and Diner 2"
        assert replace_nicknames("AL来了") == "Diner 2来了"


def test_no_context_changes_nothing():
    assert current_labels.get() is None
    assert replace_nicknames("我和小王") == "我和小王"
    assert restore_labels("Diner 2 也来") == "Diner 2 也来"


def test_scope_resets_the_context():
    ctx = context([Friend(2, "小王")])
    with labels_scope(ctx):
        assert current_labels.get() is ctx
    assert current_labels.get() is None


def restore_table() -> LabelTable:
    return LabelTable(
        labels={
            "diner:1": "Diner 1",
            "diner:2": "Diner 2",
            "guest:1": "Diner 3",
            "guest:2": "Diner 4",
        },
        names={"diner:1": None, "guest:1": "Alex", "guest:2": None},
    )


def test_restore_labels():
    friends = [Friend(2, "小王")]
    with labels_scope(context(friends, table=restore_table())):
        assert (
            restore_labels("Diner 1、Diner 2、Diner 3、Diner 4 和 Diner 7")
            == "你、小王、Alex、客人 和 其他用餐者"
        )
        assert restore_labels("diner 2") == "小王"
    with labels_scope(context(friends, locale="en", table=restore_table())):
        assert (
            restore_labels("Diner 1, Diner 4 and Diner 7") == "you, a guest and another diner"
        )


def test_restore_labels_next_to_cjk_text():
    with labels_scope(context([Friend(2, "小王")], table=restore_table())):
        assert restore_labels("Diner 2也来") == "小王也来"
        assert restore_labels("和Diner 2的饮食限制") == "和小王的饮食限制"
        assert restore_labels("Diner 23") == "其他用餐者"


def test_restore_labels_for_a_friend_no_longer_saved():
    with labels_scope(context([], table=restore_table())):
        assert restore_labels("Diner 2") == "其他用餐者"


def test_restore_disabled_keeps_labels():
    with labels_scope(context([Friend(2, "小王")], restore=False, table=restore_table())):
        assert restore_labels("Diner 2 也来") == "Diner 2 也来"


def test_label_table_round_trip():
    table = restore_table()
    assert LabelTable.from_state(table.to_state(), self_id=1) == table
    assert LabelTable.from_state(None, self_id=5).labels == {"diner:5": "Diner 1"}


def test_diner_codes_use_the_table():
    table = LabelTable.from_state(None, self_id=1)
    table.label_for("diner:9")
    guest = RuleRef("guest:1", "allergy", "peanut", None, None)
    constraints = MealConstraints(diner_ids=(1, 4), guest_rules=(guest,))
    assert diner_codes(constraints, table) == {
        "diner:1": "Diner 1",
        "diner:4": "Diner 3",
        "guest:1": "Diner 4",
    }


def test_load_label_context(session, seeded):
    me = self_diner(session)
    friend = Diner(display_name="小王", is_self=False)
    session.add(friend)
    potato = by_zh(session, "土豆")
    potato.user_aliases_zh = ["洋山芋"]
    make_recipe(session, "鱼香肉丝")
    make_recipe(session, "汤")
    session.flush()
    ctx = load_label_context(session, LabelTable.from_state(None, me.id), "zh")
    assert ctx.friends == [Friend(friend.id, "小王")]
    assert {"土豆", "洋山芋", "鱼香肉丝"} <= ctx.food_terms
    assert "汤" not in ctx.food_terms
    assert all(len(term) > 1 for term in ctx.food_terms)
    assert ctx.locale == "zh" and ctx.restore is True
