import pytest

from minemydinner.extraction.normalize import find_term_owner, match_cookware, match_ingredient
from minemydinner.models import Cookware, Ingredient
from minemydinner.seed import seed_all


@pytest.fixture
def session_seeded(session):
    seed_all(session)
    session.flush()
    return session


def add_user_ingredient(session, name_zh: str, aliases_en: list[str] | None = None) -> Ingredient:
    entry = Ingredient(
        name_zh=name_zh,
        aliases_en=aliases_en or [],
        category="other",
        stock_mode="level",
        source="user",
    )
    session.add(entry)
    session.flush()
    return entry


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("葱花", "葱"),
        ("生抽", "生抽"),
        ("  老抽 ", "老抽"),
        ("Scallion", "葱"),
        ("SOY SAUCE", "酱油"),
        ("　酱油　", "酱油"),
    ],
)
def test_matches_names_and_aliases(session_seeded, name, expected):
    assert match_ingredient(session_seeded, name).name_zh == expected


@pytest.mark.parametrize("name", ["温水", "凉水", "凉白开", "冰水"])
def test_water_at_any_temperature_is_water(session_seeded, name):
    assert match_ingredient(session_seeded, name).name_zh == "水"


@pytest.mark.parametrize("name", ["秘制酱", "葱 花", "葱花儿", "", "   "])
def test_unknown_name_is_not_guessed(session_seeded, name):
    assert match_ingredient(session_seeded, name) is None


def test_user_entries_are_matched(session_seeded):
    add_user_ingredient(session_seeded, "秘制酱", aliases_en=["Secret Sauce"])
    assert match_ingredient(session_seeded, "秘制酱").source == "user"
    assert match_ingredient(session_seeded, "secret sauce").name_zh == "秘制酱"


def test_cookware_alias(session_seeded):
    assert match_cookware(session_seeded, "wok").name_zh == "炒锅"
    assert match_cookware(session_seeded, "不粘锅").name_zh == "平底锅"
    assert match_cookware(session_seeded, "空气炸锅X") is None


def test_find_term_owner_reports_the_entry_holding_a_term(session_seeded):
    assert find_term_owner(session_seeded, Ingredient, "葱花").name_zh == "葱"
    assert find_term_owner(session_seeded, Cookware, "Skillet").name_zh == "平底锅"
    assert find_term_owner(session_seeded, Ingredient, "秘制酱") is None
