"""Check the suggestion prompt (allergen_suggest_v2) over a fixed list of ingredient names.

Calls the real configured model once per name, so a run costs a little money. Runs only against
the test database (the calls write `llm_calls` usage rows). Prints, per field, how many
suggestions match the expected values, then the mismatches and the cost.
"""

import argparse
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from minemydinner.evaluation.planning import InputError, UnsafeDatabase, check_test_database
from minemydinner.extraction.allergen_suggest import PROMPT_VERSION, suggest_entry
from minemydinner.llm.client import LLMClient
from minemydinner.llm.config import load_llm_config
from minemydinner.models import LlmCall
from minemydinner.settings import get_settings

TASK = "allergen_suggestions"
FIELDS = ("allergens", "category", "stock_mode")


@dataclass
class Report:
    total: int = 0
    matches: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    mismatches: list[str] = field(default_factory=list)
    cost: Decimal = Decimal("0")


def load_names(path: Path) -> list[dict[str, Any]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise InputError(f"Cannot read {path}: {error}") from error
    names = document.get("names") if isinstance(document, dict) else None
    if not isinstance(names, list) or not names:
        raise InputError(f"{path} needs a non-empty 'names' list")
    return names


def run_check(
    factory: sessionmaker[Session], client: LLMClient, names: list[dict[str, Any]]
) -> Report:
    """One suggestion call per name; the cost is the sum of the `llm_calls` rows written."""
    with factory() as session:
        watermark = session.scalar(select(func.coalesce(func.max(LlmCall.id), 0)))
    report = Report(total=len(names))
    for item in names:
        suggestion = suggest_entry(client, item["name"])
        if suggestion is None:
            report.mismatches.append(f"{item['name']}: no suggestion (no model available)")
            continue
        for name_field in FIELDS:
            got, expected = getattr(suggestion, name_field), item[name_field]
            if name_field == "allergens":
                got, expected = set(got), set(expected)
            if got == expected:
                report.matches[name_field] += 1
            else:
                shown = (sorted(got), sorted(expected)) if name_field == "allergens" else (got, expected)
                report.mismatches.append(
                    f"{item['name']}: {name_field} got {shown[0]}, expected {shown[1]}"
                )
    with factory() as session:
        costs = session.scalars(
            select(LlmCall.cost_estimate).where(LlmCall.id > watermark, LlmCall.task == TASK)
        )
        report.cost = sum((Decimal(cost or 0) for cost in costs), Decimal("0"))
    return report


def print_report(report: Report) -> None:
    print(f"Suggestion check with {PROMPT_VERSION} over {report.total} names")
    for name_field in FIELDS:
        print(f"  {name_field}: {report.matches[name_field]}/{report.total}")
    print("Mismatches:" if report.mismatches else "No mismatches.")
    for line in report.mismatches:
        print(f"  {line}")
    print(f"Cost (USD, from llm_calls): {report.cost:.4f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", type=Path, required=True)
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        check_test_database(settings)
        names = load_names(args.names)
    except (UnsafeDatabase, InputError) as error:
        print(error, file=sys.stderr)
        return 2
    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    client = LLMClient(
        load_llm_config(model_overrides=settings.model_overrides()),
        factory,
        settings.llm_monthly_cap_usd,
    )
    try:
        print_report(run_check(factory, client, names))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
