"""Extraction metrics. Pure functions: no database and no model calls."""

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from minemydinner.extraction.schemas import ExtractedRecipe

# Lines whose source gives a number: weights, volumes, counts, and household measures.
STATED_AMOUNT_TYPES = ("exact", "count", "spoon")


@dataclass(frozen=True)
class EvalItem:
    lang: str
    # The user's corrected recipe.
    gold: dict[str, Any]
    # The model's output before any post-processing, so fabrication stays measurable.
    predicted: dict[str, Any]


@dataclass
class ItemCounts:
    valid_json: bool = False
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    amount_counted: int = 0
    amount_correct: int = 0
    type_counted: int = 0
    type_correct: int = 0
    fabrication_counted: int = 0
    fabricated: int = 0

    def add(self, other: "ItemCounts") -> None:
        for field in self.__dataclass_fields__:
            if field != "valid_json":
                setattr(self, field, getattr(self, field) + getattr(other, field))


@dataclass(frozen=True)
class Metrics:
    items: int
    ingredient_f1: float | None
    amount_accuracy: float | None
    amount_counted: int
    amount_type_accuracy: float | None
    fabrication_rate: float | None
    valid_json_rate: float | None


@dataclass(frozen=True)
class Report:
    overall: Metrics
    by_language: dict[str, Metrics]


def _key(line: dict[str, Any]) -> str:
    return str(line.get("name") or "").strip().casefold()


def _same_amount(gold: Any, predicted: Any) -> bool:
    if gold is None or predicted is None:
        return gold is None and predicted is None
    return math.isclose(float(gold), float(predicted), rel_tol=1e-9, abs_tol=1e-9)


def score_item(gold: dict[str, Any], predicted: dict[str, Any]) -> ItemCounts:
    """Match lines by trimmed, case-insensitive name. Each line matches at most once."""
    gold_lines = list(gold.get("ingredients") or [])
    counts = ItemCounts()
    try:
        ExtractedRecipe.model_validate(predicted)
    except ValidationError:
        # Output that does not validate recalls nothing.
        counts.false_negatives = len(gold_lines)
        return counts
    counts.valid_json = True
    unmatched: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in predicted["ingredients"]:
        unmatched[_key(line)].append(line)
    for gold_line in gold_lines:
        candidates = unmatched[_key(gold_line)]
        if not candidates:
            counts.false_negatives += 1
            continue
        predicted_line = candidates.pop(0)
        counts.true_positives += 1
        counts.type_counted += 1
        counts.type_correct += gold_line.get("amount_type") == predicted_line.get("amount_type")
        if gold_line.get("amount_type") in STATED_AMOUNT_TYPES:
            counts.amount_counted += 1
            counts.amount_correct += _same_amount(
                gold_line.get("amount"), predicted_line.get("amount")
            ) and gold_line.get("unit") == predicted_line.get("unit")
        if gold_line.get("amount") is None:
            counts.fabrication_counted += 1
            counts.fabricated += predicted_line.get("amount") is not None
    counts.false_positives = sum(len(lines) for lines in unmatched.values())
    return counts


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _metrics(scored: list[ItemCounts]) -> Metrics:
    total = ItemCounts()
    for counts in scored:
        total.add(counts)
    tp, fp, fn = total.true_positives, total.false_positives, total.false_negatives
    if tp + fp + fn == 0:
        f1 = None
    else:
        f1 = 2 * tp / (2 * tp + fp + fn)
    return Metrics(
        items=len(scored),
        ingredient_f1=f1,
        amount_accuracy=_ratio(total.amount_correct, total.amount_counted),
        amount_counted=total.amount_counted,
        amount_type_accuracy=_ratio(total.type_correct, total.type_counted),
        fabrication_rate=_ratio(total.fabricated, total.fabrication_counted),
        valid_json_rate=_ratio(sum(counts.valid_json for counts in scored), len(scored)),
    )


def summarize(items: list[EvalItem]) -> Report:
    """Micro-averaged metrics overall and per language."""
    scored = [(item.lang, score_item(item.gold, item.predicted)) for item in items]
    by_language: dict[str, list[ItemCounts]] = defaultdict(list)
    for lang, counts in scored:
        by_language[lang].append(counts)
    return Report(
        overall=_metrics([counts for _, counts in scored]),
        by_language={lang: _metrics(group) for lang, group in sorted(by_language.items())},
    )
