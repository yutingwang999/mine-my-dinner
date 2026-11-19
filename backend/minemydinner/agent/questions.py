"""The assistant's recorded questions as text for the parse input (spec 5.6).

The wording matches what the chat shows (frontend/messages/*.json, chat.question and
chat.problem, joined as MessageList does), so the parse reads the question the user saw."""

from langchain_core.messages import AIMessage

QUESTION_STRINGS = {
    "zh": {
        "hint": "要规划这一餐，请告诉我{fields}。你的下一条消息会作为回答。",
        "fields": {
            "mode": "只用冰箱里现有的食材，还是也可以买菜",
            "composition": "想做几道菜",
        },
        "problem": {
            "split_conflicts_total": "荤菜和素菜的数量要求和总菜数对不上。请重新告诉我各自的数量。",
            "split_exceeds_total": "荤菜和素菜的数量加起来超过了总菜数。请重新告诉我各自的数量。",
            "too_many": "一次最多只能安排 12 道（所有种类合计）。请告诉我想做几道。",
        },
    },
    "en": {
        "hint": "To plan this, tell me {fields}. Your next message answers it.",
        "fields": {
            "mode": "whether to cook only from the fridge or to shop as well",
            "composition": "how many dishes you want",
        },
        "problem": {
            "split_conflicts_total": (
                "The meat and vegetable counts do not fit the total number of dishes. "
                "Tell me the counts again."
            ),
            "split_exceeds_total": (
                "The meat and vegetable dishes add up to more than the total number of "
                "dishes. Tell me the counts again."
            ),
            "too_many": (
                "That is more than I can plan at once, at most 12 dishes in total. "
                "Tell me how many you want."
            ),
        },
    },
}


def _join_or(items: list[str], locale: str) -> str:
    """Intl.ListFormat with type disjunction, as MessageList formats the fields."""
    if len(items) <= 1:
        return "".join(items)
    if locale == "zh":
        return "、".join(items[:-1]) + "或" + items[-1]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + ", or " + items[-1]


def question_text(value: dict, locale: str) -> str | None:
    """The fixed wording of a recorded question in the locale (English when unknown); None
    for kinds the chat shows as a form rather than a message (request style, diner card)."""
    if value.get("kind") != "question":
        return None
    if locale not in QUESTION_STRINGS:
        locale = "en"
    strings = QUESTION_STRINGS[locale]
    problem = value.get("problem")
    if problem in strings["problem"]:
        return strings["problem"][problem]
    fields = [strings["fields"].get(name, name) for name in value.get("fields") or []]
    return strings["hint"].replace("{fields}", _join_or(fields, locale))


def parse_messages(messages: list, questions: list[dict], locale: str) -> list:
    """The messages with each question that has text inserted as an AIMessage after the
    first `at` messages, in the order asked. The given list is not changed."""
    asked = [(q["at"], text) for q in questions if (text := question_text(q["value"], locale))]
    out: list = []
    pending = iter(sorted(asked, key=lambda item: item[0]))  # stable: same `at` keeps order
    upcoming = next(pending, None)
    for index, message in enumerate(messages):
        while upcoming is not None and upcoming[0] <= index:
            out.append(AIMessage(upcoming[1]))
            upcoming = next(pending, None)
        out.append(message)
    while upcoming is not None:
        out.append(AIMessage(upcoming[1]))
        upcoming = next(pending, None)
    return out
