"""Export reviewed extractions to a local JSONL file. The data never enters the repository."""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.db import SessionFactory, configure_engine
from minemydinner.models import ExtractionLog
from minemydinner.settings import get_settings


def export_logs(session: Session, out: Path) -> int:
    """Write one row per log that holds a user correction. Returns the row count."""
    logs = session.scalars(select(ExtractionLog).order_by(ExtractionLog.id))
    rows = [
        {
            "lang": log.user_corrected.get("lang"),
            "input_text": log.input_text,
            "model_output": log.model_output,
            "user_corrected": log.user_corrected,
            "model_name": log.model_name,
            "prompt_version": log.prompt_version,
        }
        for log in logs
        if log.user_corrected
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    configure_engine(get_settings().database_url)
    with SessionFactory() as session:
        count = export_logs(session, args.out)
    print(f"Wrote {count} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
