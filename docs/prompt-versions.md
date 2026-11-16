# Prompt Versions

Each model task has its own prompt file, versioned on its own: `route`, `parse`, and `planner` for planning (`backend/minemydinner/agent/prompts/<kind>_vN.md`), and `extract_vN.md` for recipe extraction (`backend/minemydinner/extraction/prompts/`), which follows the same rule.

## The rule

A prompt version that any record has used is never edited. Records are rows in the real database (`planning_runs.prompt_version`, `extraction_logs.prompt_version`) and saved evaluation runs. A change to a prompt makes the next version of that prompt only (for example `planner_v4.md`); the other prompts keep their versions. Then update `PROMPT_VERSIONS` in `backend/minemydinner/agent/request.py`.

Only while no record has used a version may it be edited in place.

## The composite label

`PROMPT_VERSIONS` maps each planning prompt to its current version, in the order route, parse, planner. `PROMPT_VERSION` joins them with `+`; today it is `route_v2+parse_v3+planner_v4`.

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
| `route_v2+parse_v3+planner_v3` | `parse_v3` adds `meat_bound` and `vegetable_bound` ("至少" / "最多" on a meat or vegetable count), reads "at least" or "at most" on other categories as that number in `range_fields`, and gives examples for the messages the next-action buttons send ("荤素都行", "不一定要用指定的食材") after a plan (design 5.11). The planner prompt is unchanged: the request summary states the ranges in words. |
| `route_v2+parse_v3+planner_v4` (current) | `planner_v4` replaces the note paragraph (chat page redesign, design 5.5): the note is one sentence with one practical tip (which dish to start first, which takes longest, how the dishes go together); it does not count or list the plan's dishes, names no dish outside the plan, suggests no swaps, and makes no allergy safety claims. Only the note rule changed; `PlanProposal` and the tool version are unchanged. The planning evaluation checks the notes automatically (`note_flags`). No saved run or real planning run has used it yet. |

## Recipe extraction

`PROMPT_VERSION` in `backend/minemydinner/extraction/extractor.py` names the current file; each saved review writes it to `extraction_logs.prompt_version`.

| Version | Change |
| --- | --- |
| `extract_v1` | The Phase 0 prompt. It was edited in place several times during the Phase 0 import, each time with the user's approval, while `extraction_logs` was still empty. The 134 logs in the real database (as of 2026-11-15) all use it. |
| `extract_v2` (current) | Adds `recipe_count`: the number of distinct recipes in the input (at least 1; only the first is extracted; variations of one recipe count as one), so the review can warn when several recipes were pasted together. Adds an example with two recipes pasted together. Commit `d8a6271`, 2026-11-13; the same commit tightened the amount guard in `postprocess.py`, which is code, not prompt. No saved review has used it yet (2026-11-15). |

## Prompts without a version yet

- **Allergen suggestions** (`backend/minemydinner/extraction/allergen_suggest.py`, task `allergen_suggestions`). The prompt is a constant in the code, not a file, and no record names it: a suggestion is never stored, only the allergens the user confirms, and the model call leaves a `llm_calls` usage row without a prompt version. The import and dictionary experience plan changes this prompt (it adds the category and stock mode); that change moves it to a file (`allergen_suggest_v1.md`) under the same rule. See `docs/phase-1-carryover.md`.

