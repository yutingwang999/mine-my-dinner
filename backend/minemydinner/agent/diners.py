"""The diner card (spec 5.1, 5.2): when to ask who eats, and what the card shows.

Nothing here calls a model, and nothing the parse extracted applies before the card is
confirmed: stated rules are only proposed on the card.
"""

from collections.abc import Collection, Sequence
from dataclasses import asdict, dataclass
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.agent.labels import LABEL_PATTERN, LabelTable
from minemydinner.agent.request import ParsedRequest, StatedRules
from minemydinner.api.diners import RuleIn, RuleOut
from minemydinner.constants import DIET_WORDS, RULE_TYPES
from minemydinner.extraction.normalize import match_ingredient
from minemydinner.models import DietaryRule, Diner, Ingredient
from minemydinner.planning.context import MealConstraints, RuleRef

# A restriction word in the request with no rule parsed shows the card's notice (spec 5.2).
RESTRICTION_WORDS: tuple[str, ...] = (
    "过敏", "不吃", "不能吃", "忌口", "吃素", "素食",
    "allergic", "allergy", "vegetarian", "vegan", "don't eat", "can't eat", "avoid",
)


class ProposedRule(BaseModel):
    """A rule the user stated in the chat, proposed on the card. Exactly one of allergen,
    ingredient_id, or text is set."""

    rule_type: Literal[*RULE_TYPES]  # type: ignore[valid-type]
    allergen: str | None = None
    ingredient_id: int | None = None
    # The dictionary name the stated name matched, for display.
    ingredient_name: str | None = None
    text: str | None = None
    # A text rule, which matches recipe lines by wording and may match loosely.
    by_wording: bool = False


def _has_cjk(text: str) -> bool:
    return any("㐀" <= char <= "鿿" for char in text)


def stated_to_rules(session: Session, stated: StatedRules | None) -> list[ProposedRule]:
    """Stated allergens and avoid items as rules, without duplicates. An avoid item the
    dictionary matches (personal aliases included) becomes an ingredient rule; any other a
    text rule. Diet words are never turned into rules."""
    if stated is None:
        return []
    rules = [ProposedRule(rule_type="allergy", allergen=allergen) for allergen in stated.allergens]
    diet = {word.casefold() for word in DIET_WORDS}
    for item in stated.avoid:
        name = item.name.strip()
        if not name or name.casefold() in diet:
            continue
        rule_type = "dietary restriction" if item.kind == "restriction" else "dislike"
        entry = match_ingredient(session, name)
        if entry is not None:
            shown = entry.name_zh if _has_cjk(name) or not entry.name_en else entry.name_en
            rules.append(
                ProposedRule(
                    rule_type=rule_type,
                    ingredient_id=entry.id,
                    ingredient_name=shown or entry.name_zh,
                )
            )
        else:
            rules.append(ProposedRule(rule_type=rule_type, text=name, by_wording=True))
    return _unique(rules)


def _target(rule) -> tuple:
    """What a rule is about. Two rules about the same thing count as the same rule, whatever
    their strength, so a stated "不喜欢羊肉" is not proposed over a saved "不吃羊肉"."""
    text = rule.text.strip().casefold() if rule.text else None
    return (rule.allergen, rule.ingredient_id, text)


def _same_target(rule, other) -> bool:
    return _target(rule) == _target(other)


def _unique(rules: Sequence[ProposedRule]) -> list[ProposedRule]:
    unique: list[ProposedRule] = []
    for rule in rules:
        if not any(_same_target(rule, other) for other in unique):
            unique.append(rule)
    return unique


def not_yet_known(proposed: Sequence[ProposedRule], known: Sequence) -> list[ProposedRule]:
    """The proposed rules that no known rule (a profile rule, or a settled meal rule as a
    dict or object) already covers."""
    targets = {_target(_as_rule(item)) for item in known}
    return [rule for rule in proposed if _target(rule) not in targets]


class _RuleView:
    def __init__(self, data: dict) -> None:
        self.allergen = data.get("allergen")
        self.ingredient_id = data.get("ingredient_id")
        self.text = data.get("text")


def _as_rule(item):
    return _RuleView(item) if isinstance(item, dict) else item


def settled_rules(settled: dict | None, key: str) -> list[dict]:
    """The settled set's meal-only rules of one diner key."""
    if not settled:
        return []
    return [
        rule for rule in settled.get("meal_rules") or []
        if isinstance(rule, dict) and rule.get("diner_key") == key
    ]


def _guest_name(name: str | None) -> str | None:
    return name.strip().casefold() or None if name else None


def guest_key(settled: dict | None, name: str | None) -> str | None:
    """The settled guest key of a named guest, matching the name ignoring case."""
    wanted = _guest_name(name)
    if not settled or wanted is None:
        return None
    for key, settled_name in (settled.get("guest_names") or {}).items():
        if _guest_name(settled_name) == wanted:
            return key
    return None


def _guests_covered(parsed: ParsedRequest, settled: dict) -> bool:
    """Every parsed guest is in the settled set: a named one by name, and the unnamed ones
    by the settled set's unnamed guests and unmatched people."""
    names = settled.get("guest_names") or {}
    unnamed_room = sum(1 for name in names.values() if _guest_name(name) is None)
    unnamed_room += int(settled.get("others") or 0)
    unnamed = 0
    for guest in parsed.guests:
        if _guest_name(guest.name) is None:
            unnamed += 1
        elif guest_key(settled, guest.name) is None:
            return False
    return unnamed <= unnamed_room


def card_needed(
    parsed: ParsedRequest,
    matched_friend_ids: list[int],
    new_self_rules: list[ProposedRule],
    settled: dict | None,
    existing_friend_ids: set[int],
    *,
    self_id: int | None = None,
    uncovered_rules: Collection[ProposedRule] = (),
) -> bool:
    """Whether to show the diner card (spec 5.1).

    A new rule the user states about themselves always asks, even for a solo meal. A
    request where only the user eats (headcount 1) never asks otherwise. With a settled set
    the card asks again only when the request changes who eats: a matched friend or a guest
    not in it, a stated guest or friend rule not on it (`uncovered_rules`), a different
    headcount, or one of its friends deleted since. Without one it asks when more than one
    person eats. `self_id` is the user's diner id, which is never counted as a deleted
    friend.
    """
    if new_self_rules:
        return True
    if parsed.headcount == 1:
        return False
    if settled is not None:
        settled_ids = set(settled.get("diner_ids") or [])
        friends = settled_ids - {self_id}
        if friends - set(existing_friend_ids):
            return True
        if set(matched_friend_ids) - settled_ids:
            return True
        if not _guests_covered(parsed, settled):
            return True
        if uncovered_rules:
            return True
        return parsed.headcount is not None and parsed.headcount != settled.get("headcount")
    return bool(
        (parsed.headcount or 1) >= 2
        or parsed.others_mentioned
        or parsed.guests
        or matched_friend_ids
        or uncovered_rules
    )


def friend_id_for_code(code: str, labels: LabelTable) -> int | None:
    """The saved friend's diner id behind a "Diner N" label, or None."""
    match = LABEL_PATTERN.fullmatch(code.strip())
    if match is None:
        return None
    key = labels.key_for(int(match.group(1)))
    if key is None or not key.startswith("diner:"):
        return None
    return int(key.split(":", 1)[1])


def stated_friend_rules(
    parsed: ParsedRequest, labels: LabelTable
) -> dict[int, list[StatedRules]]:
    """The stated friend rules by the friend's diner id; a code with no friend is dropped."""
    found: dict[int, list[StatedRules]] = {}
    for item in parsed.friend_rules:
        diner_id = friend_id_for_code(item.code, labels)
        if diner_id is not None:
            found.setdefault(diner_id, []).append(item)
    return found


def _rule_out(rule: DietaryRule) -> dict:
    ingredient = rule.ingredient
    return RuleOut(
        id=rule.id,
        diner_id=rule.diner_id,
        rule_type=rule.rule_type,
        allergen=rule.allergen,
        ingredient_id=rule.ingredient_id,
        ingredient_name_zh=ingredient.name_zh if ingredient else None,
        ingredient_name_en=ingredient.name_en if ingredient else None,
        text=rule.text,
    ).model_dump()


def profile_rules(session: Session, diner_ids: Collection[int]) -> dict[int, list[DietaryRule]]:
    """Each diner's saved rules, in creation order."""
    found: dict[int, list[DietaryRule]] = {diner_id: [] for diner_id in diner_ids}
    if diner_ids:
        query = (
            select(DietaryRule)
            .where(DietaryRule.diner_id.in_(list(diner_ids)))
            .order_by(DietaryRule.id)
        )
        for rule in session.scalars(query):
            found[rule.diner_id].append(rule)
    return found


def _diet_words(stated: Sequence[StatedRules]) -> list[str]:
    return list(dict.fromkeys(word for item in stated for word in item.diet_words))


def has_restriction_word(text: str) -> bool:
    folded = text.casefold()
    return any(word.casefold() in folded for word in RESTRICTION_WORDS)


def _anything_stated(parsed: ParsedRequest) -> bool:
    people: list[StatedRules] = [*parsed.guests, *parsed.friend_rules]
    if parsed.self_rules is not None:
        people.append(parsed.self_rules)
    return any(item.allergens or item.avoid or item.diet_words for item in people)


def build_card(
    session: Session,
    parsed: ParsedRequest,
    labels: LabelTable,
    matched_friend_ids: list[int],
    current_text: str,
) -> dict:
    """The diner card's interrupt value (spec 5.2). Rows: the user first, then every saved
    friend by name; the user and the matched friends are preselected. Stated rules already
    in a profile are left out of `proposed`. Display names only; no label appears.

    When one person eats (the card asks only about the user's own new rules), the card
    shows the user's row alone and mentions no friend (spec 5.1)."""
    diners = list(session.scalars(select(Diner)))
    me = next((diner for diner in diners if diner.is_self), None)
    by_friend = stated_friend_rules(parsed, labels)
    matched = set(matched_friend_ids) | set(by_friend)
    solo = (
        (parsed.headcount or 1) == 1
        and not parsed.others_mentioned
        and not parsed.guests
        and not matched
    )
    friends = [] if solo else sorted(
        (diner for diner in diners if not diner.is_self),
        key=lambda diner: (diner.display_name.casefold(), diner.id),
    )
    ordered = ([me] if me is not None else []) + friends
    saved = profile_rules(session, [diner.id for diner in ordered])
    rows = []
    for diner in ordered:
        stated: list[StatedRules] = []
        if diner.is_self:
            if parsed.self_rules is not None:
                stated = [parsed.self_rules]
        else:
            stated = by_friend.get(diner.id, [])
        proposed = [rule for item in stated for rule in stated_to_rules(session, item)]
        proposed = not_yet_known(proposed, saved[diner.id])
        rows.append(
            {
                "diner_id": diner.id,
                "name": None if diner.is_self else diner.display_name,
                "is_self": diner.is_self,
                "selected": diner.is_self or diner.id in matched,
                "rules": [_rule_out(rule) for rule in saved[diner.id]],
                "proposed": [rule.model_dump() for rule in _unique(proposed)],
                "diet_words": _diet_words(stated),
            }
        )
    guests = [
        {
            "name": guest.name,
            "proposed": [rule.model_dump() for rule in stated_to_rules(session, guest)],
            "diet_words": _diet_words([guest]),
        }
        for guest in parsed.guests
    ]
    preselected = sum(1 for row in rows if row["selected"])
    # "我和朋友吃饭" states no number, but at least two people eat.
    headcount = parsed.headcount or max(
        preselected + len(guests), 2 if parsed.others_mentioned else 1
    )
    nudge = (
        has_restriction_word(current_text)
        and not _anything_stated(parsed)
        and not matched
    )
    return {
        "kind": "diners",
        "headcount": headcount,
        "rows": rows,
        "guests": guests,
        "others": max(0, headcount - preselected - len(guests)),
        "nudge": nudge,
    }


# ---- applying the answer (spec 5.3, 5.4) ----


def _diet_text(text: str | None) -> bool:
    return text is not None and text.strip().casefold() in {w.casefold() for w in DIET_WORDS}


class RuleChoice(RuleIn):
    """A rule ticked or entered on the card. `save` writes it to the person's profile;
    otherwise it applies to this meal only."""

    save: bool = False

    @model_validator(mode="after")
    def no_diet_word(self) -> Self:
        if _diet_text(self.text):
            raise ValueError("a diet word is not a rule; add the ingredients one by one")
        return self


class GuestAnswer(BaseModel):
    """A person on the card who is not a saved friend. `save` creates a friend with the
    name as given and these rules."""

    name: str | None = Field(default=None, max_length=40)
    rules: list[RuleChoice] = Field(default_factory=list, max_length=20)
    save: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def save_needs_name(self) -> Self:
        if self.save and self.name is None:
            raise ValueError("saving a guest as a friend needs a name")
        return self


class DinerAnswer(BaseModel):
    """The confirmed diner card. `selected` holds diner ids (the user included when eating);
    `stated` the ticked rules per selected diner; `others` the people still not matched,
    with "none" (no dietary limits) or "unsure" for them together."""

    selected: list[int] = Field(max_length=50)
    stated: dict[int, list[RuleChoice]] = Field(default_factory=dict)
    guests: list[GuestAnswer] = Field(default_factory=list, max_length=20)
    others: int = Field(default=0, ge=0, le=50)
    others_answer: Literal["none", "unsure"] | None = None

    @model_validator(mode="after")
    def someone_eats(self) -> Self:
        self.selected = list(dict.fromkeys(self.selected))
        if not self.selected and not self.guests and not self.others:
            raise ValueError("at least one person eats")
        if set(self.stated) - set(self.selected):
            raise ValueError("stated rules belong to selected diners only")
        if self.others and self.others_answer is None:
            # Unmatched people with no answer count as unsure, so S8 warns about them.
            self.others_answer = "unsure"
        return self


@dataclass
class SettledDiners:
    """The confirmed diner set of a conversation (spec 5.1, 5.3). `meal_rules` are the
    meal-only rules: selected diners' under "diner:<id>" and guests' under "guest:<n>".
    `guest_names` holds the guests not saved as friends."""

    diner_ids: list[int]
    meal_rules: list[RuleRef]
    guest_names: dict[str, str | None]
    others: int
    others_answer: str | None
    headcount: int

    def to_state(self) -> dict:
        return {
            "diner_ids": list(self.diner_ids),
            "meal_rules": [asdict(rule) for rule in self.meal_rules],
            "guest_names": dict(self.guest_names),
            "others": self.others,
            "others_answer": self.others_answer,
            "headcount": self.headcount,
        }

    @classmethod
    def from_state(cls, data: dict) -> "SettledDiners":
        return cls(
            diner_ids=list(data.get("diner_ids") or []),
            meal_rules=[RuleRef(**rule) for rule in data.get("meal_rules") or []],
            guest_names=dict(data.get("guest_names") or {}),
            others=int(data.get("others") or 0),
            others_answer=data.get("others_answer"),
            headcount=int(data["headcount"]),
        )


def constraints_from(settled: SettledDiners) -> MealConstraints:
    unconfirmed = settled.others if settled.others_answer == "unsure" else 0
    return MealConstraints(
        diner_ids=tuple(settled.diner_ids),
        guest_rules=tuple(settled.meal_rules),
        unconfirmed_diners=unconfirmed,
    )


def _next_guest_number(labels: LabelTable) -> int:
    """One past every guest number in the table, so guests settled earlier in the thread
    keep their keys."""
    numbers = [
        int(key.split(":", 1)[1])
        for key in (*labels.labels, *labels.names)
        if key.startswith("guest:") and key.split(":", 1)[1].isdigit()
    ]
    return max(numbers, default=0) + 1


def _check_rule(session: Session, rule: RuleChoice) -> None:
    # The card never sends a diet word as a rule; refuse one anyway (spec 5.4).
    if _diet_text(rule.text):
        raise ValueError("a diet word is not a rule")
    if rule.ingredient_id is not None and session.get(Ingredient, rule.ingredient_id) is None:
        raise ValueError(f"unknown ingredient {rule.ingredient_id}")


def _rule_fields(rule: RuleChoice) -> dict:
    return rule.model_dump(include={"rule_type", "allergen", "ingredient_id", "text"})


def _add_meal_rule(meal_rules: list[RuleRef], key: str, rule: RuleChoice) -> None:
    same_key = [other for other in meal_rules if other.diner_key == key]
    if not any(_same_target(rule, other) for other in same_key):
        meal_rules.append(RuleRef(key, rule.rule_type, rule.allergen, rule.ingredient_id,
                                  rule.text))


def apply_answer(session: Session, answer: DinerAnswer, labels: LabelTable) -> SettledDiners:
    """Apply the confirmed card: write saved rules to profiles, create saved guests as
    friends (they join the selected diners), label the selected diners and the guests in the
    conversation's table, and return the settled set. Commits the session.

    Raises ValueError for an unknown diner or ingredient, or a diet word as a text rule."""
    for rules in (*answer.stated.values(), *(guest.rules for guest in answer.guests)):
        for rule in rules:
            _check_rule(session, rule)
    known = set(session.scalars(select(Diner.id).where(Diner.id.in_(answer.selected))))
    if missing := [i for i in answer.selected if i not in known]:
        raise ValueError(f"unknown diners {missing}")
    diner_ids = list(answer.selected)
    saved = profile_rules(session, diner_ids)
    meal_rules: list[RuleRef] = []
    for diner_id in diner_ids:
        for rule in answer.stated.get(diner_id, []):
            if any(_same_target(rule, other) for other in saved[diner_id]):
                continue
            if rule.save:
                row = DietaryRule(diner_id=diner_id, **_rule_fields(rule))
                session.add(row)
                saved[diner_id].append(row)
            else:
                _add_meal_rule(meal_rules, f"diner:{diner_id}", rule)
    guest_names: dict[str, str | None] = {}
    for guest in answer.guests:
        if guest.save:
            diner = Diner(display_name=guest.name, is_self=False)
            session.add(diner)
            session.flush()
            kept: list[RuleChoice] = []
            for rule in guest.rules:
                if not any(_same_target(rule, other) for other in kept):
                    kept.append(rule)
                    session.add(DietaryRule(diner_id=diner.id, **_rule_fields(rule)))
            diner_ids.append(diner.id)
            continue
        key = f"guest:{_next_guest_number(labels)}"
        labels.names[key] = guest.name
        labels.label_for(key)
        guest_names[key] = guest.name
        for rule in guest.rules:
            _add_meal_rule(meal_rules, key, rule)
    session.commit()
    for diner_id in diner_ids:
        labels.label_for(f"diner:{diner_id}")
    return SettledDiners(
        diner_ids=diner_ids,
        meal_rules=meal_rules,
        guest_names=guest_names,
        others=answer.others,
        others_answer=answer.others_answer if answer.others else None,
        headcount=len(diner_ids) + len(guest_names) + answer.others,
    )


def answer_summary(session: Session, answer: DinerAnswer) -> dict:
    """The card's history summary: whether the user eats (`self`), the selected friends' and
    the guests' names, and how many people are shown without a name (unmatched or unnamed).
    Read before `apply_answer`, so nothing that can fail runs after its commit."""
    me = session.scalars(select(Diner.id).where(Diner.is_self.is_(True))).first()
    friends = {
        diner.id: diner.display_name
        for diner in session.scalars(
            select(Diner).where(Diner.id.in_(answer.selected), Diner.is_self.is_(False))
        )
    }
    names = [friends[i] for i in answer.selected if i in friends]
    names += [guest.name for guest in answer.guests if guest.name]
    unnamed = sum(1 for guest in answer.guests if not guest.name)
    return {"self": me in answer.selected, "names": names, "others": answer.others + unnamed}

