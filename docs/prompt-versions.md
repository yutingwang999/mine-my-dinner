# Prompt Versions

Each model task has its own prompt file, versioned on its own: `route`, `parse`, and `planner` for planning (`backend/minemydinner/agent/prompts/<kind>_vN.md`), `extract_vN.md` for recipe extraction, and `allergen_suggest_vN.md` for allergen suggestions (both in `backend/minemydinner/extraction/prompts/`), which follow the same rule.

## The rule

A prompt version that any record has used is never edited. Records are rows in the real database (`planning_runs.prompt_version`, `extraction_logs.prompt_version`) and saved evaluation runs. A change to a prompt makes the next version of that prompt only (for example `planner_v4.md`); the other prompts keep their versions. Then update `PROMPT_VERSIONS` in `backend/minemydinner/agent/request.py`.

Only while no record has used a version may it be edited in place.

## The composite label

`PROMPT_VERSIONS` maps each planning prompt to its current version, in the order route, parse, planner. `PROMPT_VERSION` joins them with `+`; today it is `route_v2+parse_v5+planner_v4`.

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
| `route_v2+parse_v3+planner_v4` | `planner_v4` replaces the note paragraph (chat page redesign, design 5.5): the note is one sentence with one practical tip (which dish to start first, which takes longest, how the dishes go together); it does not count or list the plan's dishes, names no dish outside the plan, suggests no swaps, and makes no allergy safety claims. Only the note rule changed; `PlanProposal` and the tool version are unchanged. The planning evaluation checks the notes automatically (`note_flags`). Used from evaluation round 7 (2026-11-16, `d4b2e30`), so it must not be edited in place. |
| `route_v2+parse_v4+planner_v4` | `parse_v4` (plan 2b, spec 5.4 and 5.6) adds the rules the user states in the chat: `guests` (name, `allergens` from the fixed list only, `avoid` items of kind `restriction` or `dislike`, `diet_words`), `friend_rules` (the same, keyed by a saved friend's label such as "Diner 2"; saved friends are never guests) and `self_rules`; taste preferences ("不太能吃辣", "清淡点") go to `query` and diet words ("吃素") to `diet_words`, never to `avoid`. The parse input now interleaves the assistant's recorded questions as assistant messages in the chat's wording (`agent/questions.py`), with examples for short answers ("两个" after the dish-count question, "都行" after the mode question); the route and planner inputs are unchanged. Adds the example for the fixed replan message ("按同样的要求重新规划", the previous request unchanged). Trims the repeats listed in the carryover: one example per case ("三个菜，不要素菜" kept, "全是荤菜", "8道菜，包含2个荤菜", the English twins, the repeated egg, range, vague-amount and cold-dish examples dropped) and the repeated "leave null" and "newest message wins" lines; 79 lines, like `parse_v3`. Used from evaluation round 8 (2026-11-18, `8f6babd`: the plan 2b development set and the three regression sets), so it must not be edited in place. |
| `route_v2+parse_v5+planner_v4` (current) | `parse_v5` changes one rule of `parse_v4`, the cold dish: a cold dish mentioned in passing still gives no count ("来个凉菜"), but when the user states the meal's dish count as cold dishes, that number is `dishes` ("就做两个凉菜" is dishes 2). Round 8's development run read "只做一个凉拌菜" as no count, so two people defaulted to two dishes and a fridge with one makeable dish stopped early; the user judged the stated number the better reading (2026-11-18). The example sits in the rule line, so the prompt stays at 79 lines. Used from evaluation round 8 (2026-11-18, `bb74039`; results of record at `86729e8`, including both held-out sets), so it must not be edited in place. |

## Recipe extraction

`PROMPT_VERSION` in `backend/minemydinner/extraction/extractor.py` names the current file; each saved review writes it to `extraction_logs.prompt_version`.

| Version | Change |
| --- | --- |
| `extract_v1` | The Phase 0 prompt. It was edited in place several times during the Phase 0 import, each time with the user's approval, while `extraction_logs` was still empty. The 134 logs in the real database (as of 2026-11-15) all use it. |
| `extract_v2` (current) | Adds `recipe_count`: the number of distinct recipes in the input (at least 1; only the first is extracted; variations of one recipe count as one), so the review can warn when several recipes were pasted together. Adds an example with two recipes pasted together. Commit `d8a6271`, 2026-11-13; the same commit tightened the amount guard in `postprocess.py`, which is code, not prompt. No saved review has used it yet (2026-11-15). |

## Allergen suggestions

`PROMPT_VERSION` in `backend/minemydinner/extraction/allergen_suggest.py` names the current file (`backend/minemydinner/extraction/prompts/`), task `allergen_suggestions`. No record names the version: a suggestion is never stored, only what the user confirms, and the model call leaves a `llm_calls` usage row without a prompt version. The rule above still applies, and the paid check (`python -m minemydinner.evaluation.allergen_suggest`, see `docs/local-development.md`) counts as using a version once its result is recorded here.

| Version | Change |
| --- | --- |
| `allergen_suggest_v1` | The Phase 0 prompt, moved unchanged from a constant in the code into a file: which listed allergens the ingredient may contain. The file ends with one newline, which `load_prompt` removes, so the loaded text equals the old constant. |
| `allergen_suggest_v2` (current) | Also asks for one category from `CATEGORIES` and one stock mode from `STOCK_MODES`, with one line for each stock mode (`count`, `level`, `availability`). The allergen instruction is v1's. Checked on 2026-11-17 (import and dictionary plan, Task 7) with `python -m minemydinner.evaluation.allergen_suggest` over the 18 names in `backend/eval_data/allergen_suggest/names.yaml` (the set the user approved on 2026-11-17), on the configured `allergen_suggestions` model (gemini-3.5-flash-lite, no fallback used), test database, $0.0026: category 18/18, allergens 17/18, stock mode 15/18. Mismatches: 豆腐 stock mode `level` (expected `count`); 基围虾 stock mode `count` (expected `level`) and allergens crustaceans plus shellfish (expected crustaceans only, the safe direction); shiitake mushroom stock mode `count` (expected `level`). The stock-mode misses are household judgement calls that the user confirms anyway; the user decided to keep v2. |

## Prompts without a version yet

None.
