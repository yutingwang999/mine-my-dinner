"""Export the model notes of planning evaluation runs for the user's review.

One CSV row per scenario with a checked note, per run: the model, the run, the scenario, the
plan's dishes, the note, and the automatic flags, with empty columns for the user's judgement.
The file holds model text, so it goes under the git-ignored runs folder by default.

    python -m minemydinner.evaluation.notes_review --runs <run folder>... --out <file.csv>
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from minemydinner.evaluation.planning import RUNS_ROOT

HEADER = [
    "model",
    "run_id",
    "scenario",
    "plan_titles",
    "note",
    "flags",
    "quality",
    "should_not_appear",
    "comment",
]
DEFAULT_OUT = RUNS_ROOT / "notes_review.csv"


def review_rows(run_folder: Path) -> list[list[str]]:
    """The review rows of one run folder, in scenario order."""
    summary = json.loads((run_folder / "summary.json").read_text(encoding="utf-8"))
    model = summary.get("conditions", {}).get("planning_model") or ""
    run_id = summary.get("run_id") or run_folder.name
    rows = []
    for line in (run_folder / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        result = json.loads(line)
        if not result.get("note_checked"):
            continue
        rows.append(
            [
                model,
                run_id,
                result["name"],
                " / ".join(result.get("titles", [])),
                result.get("reply_note", ""),
                ",".join(result.get("note_flags", [])),
                "",
                "",
                "",
            ]
        )
    return rows


def write_review(run_folders: list[Path], out: Path) -> int:
    """Write the CSV and return the number of rows. UTF-8 with a byte order mark, so a
    spreadsheet opens the Chinese text correctly."""
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for folder in run_folders:
            rows = review_rows(folder)
            writer.writerows(rows)
            count += len(rows)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, nargs="+", required=True, help="run folders")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the CSV file to write")
    args = parser.parse_args(argv)
    try:
        count = write_review(args.runs, args.out)
    except (OSError, ValueError, KeyError) as error:
        print(f"Cannot write the review: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(f"Wrote {count} notes to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
