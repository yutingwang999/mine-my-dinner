# Prompt Versions

Each model task has its own prompt file, versioned on its own: `route`, `parse`, and `planner` for planning (`backend/minemydinner/agent/prompts/<kind>_vN.md`), and `extract_vN.md` for recipe extraction (`backend/minemydinner/extraction/prompts/`), which follows the same rule.

## The rule

A prompt version that any record has used is never edited. Records are rows in the real database (`planning_runs.prompt_version`, `extraction_logs.prompt_version`) and saved evaluation runs. A change to a prompt makes the next version of that prompt only (for example `planner_v4.md`); the other prompts keep their versions. Then update `PROMPT_VERSIONS` in `backend/minemydinner/agent/request.py`.

Only while no record has used a version may it be edited in place.

## The composite label

`PROMPT_VERSIONS` maps each planning prompt to its current version, in the order route, parse, planner. `PROMPT_VERSION` joins them with `+`; today it is `route_v2+parse_v3+planner_v3`.

- `planning_runs.prompt_version` records the composite label (the column is plain text, so no migration was needed).
- Each evaluation run's `summary.json` records, under `conditions`, `prompt_version` (the label), `prompt_versions` (the mapping), and `prompt_files` (file name to SHA-256). The run folder's `prompts/` copy holds only the files in use.

## Old set labels

Before per-prompt versions, one label named the whole set.

| Old label | Meaning |
| --- | --- |
| `plan_v1` | `route_v1+parse_v1+planner_v1` |
| `plan_v2` | `route_v2+parse_v2+planner_v2` |

Records that say `plan_v1` or `plan_v2` keep that label.

## Version history

| Label | Change |
| --- | --- |
| `route_v2+parse_v2+planner_v3` | `planner_v3` adds the complete marker to the planner's shortlist rules. |
| `route_v2+parse_v3+planner_v3` (current) | `parse_v3` adds `meat_bound` and `vegetable_bound` ("至少" / "最多" on a meat or vegetable count), reads "at least" or "at most" on other categories as that number in `range_fields`, and gives examples for the messages the next-action buttons send ("荤素都行", "不一定要用指定的食材") after a plan (design 5.11). The planner prompt is unchanged: the request summary states the ranges in words. |
