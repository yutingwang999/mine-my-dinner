"""Score an exported extraction file and report cost and latency from llm_calls."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from minemydinner.db import SessionFactory, configure_engine
from minemydinner.evaluation.metrics import EvalItem, Metrics, summarize
from minemydinner.models import LlmCall
from minemydinner.settings import get_settings

EXTRACTION_TASKS = ("extraction", "image_understanding")
METRIC_COLUMNS = (
    "items",
    "ingredient_f1",
    "amount_accuracy",
    "amount_counted",
    "amount_type_accuracy",
    "fabrication_rate",
    "valid_json_rate",
)


class InputError(Exception):
    pass


@dataclass(frozen=True)
class CallStats:
    model: str
    calls: int
    success_rate: float
    # Averages over successful calls.
    cost_per_item: float | None
    latency_ms: float | None


def read_items(path: Path) -> list[EvalItem]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise InputError(f"Cannot read {path}: {error}") from error
    items = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            items.append(EvalItem(row["lang"], row["user_corrected"], row["model_output"]))
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise InputError(f"{path} line {number} is not a valid row: {error}") from error
    if not items:
        raise InputError(f"{path} has no rows")
    return items


def call_stats(session: Session) -> list[CallStats]:
    successful = LlmCall.success.is_(True)
    rows = session.execute(
        select(
            LlmCall.model,
            func.count(),
            func.sum(case((successful, 1), else_=0)),
            func.avg(case((successful, LlmCall.cost_estimate))),
            func.avg(case((successful, LlmCall.latency_ms))),
        )
        .where(LlmCall.task.in_(EXTRACTION_TASKS))
        .group_by(LlmCall.model)
        .order_by(LlmCall.model)
    ).all()
    return [
        CallStats(
            model=model,
            calls=calls,
            success_rate=succeeded / calls,
            cost_per_item=float(cost) if cost is not None else None,
            latency_ms=float(latency) if latency is not None else None,
        )
        for model, calls, succeeded, cost, latency in rows
    ]


def _cell(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return str(value) if isinstance(value, int) else f"{value:.4f}"


def _table(header: tuple[str, ...], rows: list[list[str]]) -> str:
    widths = [max(len(cell) for cell in column) for column in zip(header, *rows, strict=True)]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True))
        for line in [header, *rows]
    ]
    return "\n".join(line.rstrip() for line in lines)


def _metric_row(scope: str, metrics: Metrics) -> list[str]:
    return [scope, *(_cell(getattr(metrics, column)) for column in METRIC_COLUMNS)]


def run_evaluation(path: Path, session: Session) -> int:
    try:
        items = read_items(path)
    except InputError as error:
        print(error, file=sys.stderr)
        return 2
    report = summarize(items)
    rows = [_metric_row("overall", report.overall)]
    rows += [_metric_row(lang, metrics) for lang, metrics in report.by_language.items()]
    print(f"Extraction evaluation of {len(items)} rows from {path}")
    print(_table(("scope", *METRIC_COLUMNS), rows))
    print()
    print("Calls for extraction and image_understanding in llm_calls:")
    stats = call_stats(session)
    if not stats:
        print("No calls recorded.")
    else:
        print(
            _table(
                ("model", "calls", "success_rate", "cost_per_item", "latency_ms"),
                [
                    [s.model, str(s.calls), _cell(s.success_rate), _cell(s.cost_per_item),
                     _cell(s.latency_ms)]
                    for s in stats
                ],
            )
        )
    return 0


def _run_with_database(path: Path) -> int:
    configure_engine(get_settings().database_url)
    with SessionFactory() as session:
        return run_evaluation(path, session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    return _run_with_database(args.input)


if __name__ == "__main__":
    sys.exit(main())
