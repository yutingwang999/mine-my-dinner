# Planning Evaluation History

A running record of the paid planning evaluations: for each round, what changed since the round before, which runs it made, and how the results compare across rounds. Add a round after every evaluation. How to run the evaluation and what each metric means is in `docs/local-development.md` (Planning evaluation).

## How to add a round

1. Run the evaluation on a clean, committed tree (the script refuses otherwise), so each run belongs to one commit.
2. Add a row to each comparison table below for every run made, in run order.
3. Add a section under "Rounds": the commit, what changed since the previous round (code, prompts, tools, data), the run ids, what came out, and any decision taken because of it.
4. If a change is later reverted, keep its round and say so; mark which round holds the results of record.

Rules for reading the tables:

- Compare aggregates only within one scenario set (baseline set or plan 2a set). Across sets, compare same-named scenarios.
- Compare cost by the cache-aware figure. The uncached figure exists only to compare with plan 1's baseline, whose records had no cache fields.
- The prompt and tool versions alone do not identify a change: code that writes model-facing text (the shortlist and request summary in `agent/graph.py`) and tool behaviour that keeps the schema change neither. The commit does.
- **The scenarios were used for tuning.** Rounds 2 to 5 changed code in response to these same scenarios, so later results on them overstate how well the planner does on requests it has not seen. Judge real progress on new scenarios that took no part in tuning (to add in plan 2b).

## Criteria in use

From the plan 2a design (Section 5.9), measured against plan 1's baseline:

- Feasible scenarios pass on the first attempt (at least 6/6 of the original feasible ones).
- Model calls are judged by the average over scenarios that expect a plan: about 2 planner calls, so about 4 calls in total with intent and parsing. A single scenario over 5 calls is reported as a mismatch by the script but is a warning, not a failure (user decision after round 5).
- Infeasible scenarios end within 8 calls with the right reason and next actions; an empty fridge within 3.
- Cost and latency below the baseline ($0.064 uncached, 11.3 s, original 7 scenarios).
- No planner call over `PLANNER_INPUT_TOKEN_BUDGET` (32,000 tokens).
- Every new scenario matches its expectation, apart from the call-count warning above.

## Comparison: baseline set (7 scenarios, 6 expect a plan)

Columns: first pass and calls are over the 6 scenarios that expect a plan; cost, cache hit, largest call and model time are over all 7. Model time is the average per scenario of the summed model-call latencies. Wall p95 is over the 6 scenarios that expect a plan.

| Round | Run | Vectors | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Cache hit | Largest call (tokens) | Model time avg | Wall p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (plan 1) | Task 15 | no | — | 6/6 | 10.3 (all 7) | 31 | — | $0.064 | — | — | 11.3 s | — |
| 1 | 20261114T174402Z | no | 5/7 | 4/6 | 3.33 | 4 | $0.0057 | $0.0152 | 0.72 | 3062 | 3.6 s | 4.8 s |
| 2 | 20261114T222626Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0041 | $0.0128 | 0.82 | 2910 | 2.9 s | 4.4 s |
| 2 | 20261114T222651Z | yes | 6/7 | 6/6 | 3.67 | 6 | $0.0042 | $0.0161 | 0.86 | 2910 | 3.6 s | 5.7 s |
| 3 | 20261114T234232Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0043 | $0.0128 | 0.79 | 3022 | 2.9 s | 4.1 s |
| 3 | 20261114T234257Z | yes | 7/7 | 6/6 | 3.00 | 3 | $0.0037 | $0.0128 | 0.85 | 3022 | 3.0 s | 4.4 s |
| 4 | 20261115T003551Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0031 | $0.0128 | 0.89 | 3022 | 3.2 s | 4.8 s |
| 4 | 20261115T003618Z | yes | 7/7 | 6/6 | 3.00 | 3 | $0.0031 | $0.0128 | 0.89 | 3022 | 3.0 s | 4.4 s |
| 5 (reverted) | 20261115T013859Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0037 | $0.0128 | 0.84 | 3022 | 3.4 s | 5.7 s |
| 5 (reverted) | 20261115T013928Z | yes | 7/7 | 6/6 | 3.00 | 3 | $0.0031 | $0.0127 | 0.89 | 3022 | 3.3 s | 4.8 s |
| **6** | 20261115T040226Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0046 | $0.0146 | 0.79 | 3493 | 3.5 s | 4.9 s |
| **6** | 20261115T040255Z | yes | 7/7 | 6/6 | 3.00 | 3 | $0.0033 | $0.0147 | 0.90 | 3493 | 3.4 s | 5.5 s |

## Comparison: plan 2a set (18 scored scenarios, 12 expect a plan, plus 1 check-only)

Same columns. "Over 5" names the scenarios that used more than 5 calls.

| Round | Run | Vectors | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Cache hit | Largest call (tokens) | Model time avg | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 20261114T222729Z | no | 17/18 | 12/12 | 3.58 | 6 | $0.0094 | $0.0364 | 0.87 | 3492 | 3.2 s | 6.3 s | three_vegetable_dishes_only_eggs (6 calls) |
| 2 | 20261114T222835Z | yes | 17/18 | 12/12 | 3.58 | 7 | $0.0091 | $0.0376 | 0.89 | 3859 | 2.9 s | 6.9 s | all_dishes_with_eggs (7 calls) |
| 3 | 20261114T234332Z | no | 17/18 | 11/12 | 3.50 | 8 | $0.0102 | $0.0378 | 0.87 | 3895 | 3.1 s | 9.5 s | eight_dishes_two_meat (failed all 3 attempts) |
| 3 | 20261114T234435Z | yes | 18/18 | 12/12 | 3.08 | 4 | $0.0078 | $0.0314 | 0.89 | 3514 | 2.8 s | 4.3 s | — |
| 4 | 20261115T003653Z | no | 18/18 | 12/12 | 3.08 | 4 | $0.0078 | $0.0314 | 0.89 | 3609 | 2.9 s | 5.0 s | — |
| 4 | 20261115T003755Z | yes | 17/18 | 12/12 | 3.50 | 7 | $0.0086 | $0.0365 | 0.90 | 3609 | 3.2 s | 7.5 s | all_dishes_with_eggs (7 calls) |
| 5 (reverted) | 20261115T014004Z | no | 18/18 | 12/12 | 3.08 | 4 | $0.0077 | $0.0313 | 0.89 | 3609 | 3.0 s | 5.0 s | — |
| 5 (reverted) | 20261115T014105Z | yes | 17/18 | 12/12 | 3.42 | 7 | $0.0086 | $0.0355 | 0.90 | 3609 | 3.4 s | 9.1 s | three_vegetable_dishes_only_eggs (7 calls) |
| 5 (reverted) | 20261115T014229Z | yes | 18/18 | 12/12 | 3.08 | 4 | $0.0075 | $0.0312 | 0.89 | 3609 | 3.1 s | 5.4 s | — |
| 5 (reverted) | 20261115T014347Z | yes | 18/18 | 12/12 | 3.17 | 5 | $0.0079 | $0.0326 | 0.89 | 3730 | 3.0 s | 6.7 s | — |
| **6** | 20261115T040333Z | no | 18/18 | 12/12 | 3.08 | 4 | $0.0081 | $0.0363 | 0.90 | 3658 | 3.0 s | 5.6 s | — |
| **6** | 20261115T040434Z | yes | 18/18 | 12/12 | 3.17 | 4 | $0.0081 | $0.0372 | 0.91 | 3602 | 3.0 s | 4.6 s | — |

Round 1 ran the baseline set only. The check-only scenario (`one_main_ingredient_dominates`) passed in every plan 2a run.

## Comparison: counts set (10 scenarios, 8 expect a plan)

`scenarios_counts.yaml` with `library_extended.yaml`, added in round 6: meat and vegetable bounds, the implied side, and the next-action buttons' messages. These scenarios took no part in tuning. The two button scenarios (`relax_split_button`, `relax_named_button`) send two user messages, so their calls count both turns (route and parse twice); they count as plan scenarios.

| Round | Run | Vectors | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Cache hit | Largest call (tokens) | Model time avg | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **6** | 20261115T040549Z | no | 10/10 | 8/8 | 3.50 | 5 | $0.0059 | $0.0238 | 0.88 | 3510 | 3.7 s | 6.1 s | — |

## Rounds

Bold rounds in the tables hold the results of record for the current code.

### Round 0 — plan 1 baseline (Task 15)

- Prompts `plan_v1` (now `route_v1+parse_v1+planner_v1`), deepseek-flash, the 7 baseline scenarios. No run folder (the folder format came in plan 2a).
- The six feasible scenarios passed on the first attempt with 6-10 calls each; the no-wok scenario failed as expected but took 31 calls and 2 retries. Totals: 10.3 calls on average, $0.064 (no cache pricing), 11.3 s average latency.

### Round 1 — first plan 2a run (2026-11-14, `b388a68`)

- **Changed since round 0:** all of plan 2a Tasks 1-9: code-built shortlist in the planner's first message, feasibility check with early stop, one counting rule, named ingredients, cache-stable retries with an input token budget, cache-aware cost accounting, `plan_v2` prompts; and the evaluation tooling (run folders, expectations, grouped metrics).
- **Runs:** baseline set without vectors only; the other runs were paused.
- **Result:** calls fell from 10.3 to 3.1 and uncached cost from $0.064 to $0.015, but only 4 of 6 feasible scenarios passed on the first attempt: in fridge-only mode the shortlist offered dishes with missing ingredients, and the model picked them (H1).
- **Decision:** in fridge-only mode, the shortlist and the planner's search hold only makeable recipes.

### Round 2 — makeable-only shortlist (2026-11-14, `57eb27f`)

- **Changed:** fridge-only shortlist and search keep only makeable recipes (a filter, not a ranking); shortlist slots are a cap, never padded; empty slots are back-filled from recipes the main-ingredient spread skipped; in fridge-only mode the feasibility time-limit check uses the makeable recipes.
- **Runs:** all four.
- **Result:** first pass 6/6 and 12/12. Three scenarios went over 5 calls: the planner chose correctly at once, then searched one to six more times for alternatives that did not exist (every search empty).
- **Decision:** tell the planner when a dish type in the shortlist is complete.

### Round 3 — complete marker and per-prompt versions (2026-11-14, `f7ea76e`)

- **Changed:** each shortlist dish type carries `complete` (true when the shortlist holds every recipe that fits the request); the planner prompt says not to search a complete type or a type the shortlist does not list. Prompts moved to per-prompt versions; the planner prompt became `planner_v3` (`route_v2` and `parse_v2` unchanged). Tool definitions unchanged (`tools_version` `f8e0a9e09f6e`), although the shortlist's JSON shape changed.
- **Runs:** all four.
- **Result:** no scenario went over 5 calls through extra searches; the three that had done so in round 2 took 3-4 calls. A new failure exposed a bug: `eight_dishes_two_meat` failed all three attempts, because the retry message told the planner the user wanted "0 vegetable dishes" (an unnamed split side printed as 0), and the request summary never stated the implied 6.
- **Decision:** fix the wording of counts; also record the route and parse calls.

### Round 4 — implied split side and route/parse records (2026-11-15, `e0401c2`)

- **Changed:** the request summary, retry message, and plan card state the implied side of an exact one-sided split ("2 meat dishes, 6 vegetable dishes") and never print an unnamed side as 0; a minimum split gives the unnamed side no number. Route and parse model calls are now recorded as events on the planning run (input, structured output, provider, served model), so `results.jsonl` holds every model call. No prompt or tool change.
- **Runs:** all four.
- **Result:** `eight_dishes_two_meat` passed on the first attempt with 4 calls in both plan 2a runs. Baseline set 7/7 twice; plan 2a set 18/18 and 17/18. The one mismatch: `all_dishes_with_eggs` (vectors) took 7 calls: the shortlist listed "meat dish" as complete and empty, and the planner still searched for a meat dish four times to balance the meal.

### Round 5 — empty-side line (2026-11-15, `bf44304`) — reverted

- **Changed:** when no split is named and one side (meat or vegetable) has no fitting recipe, the request summary added "No meat dish fits this request, so every dish is a vegetable dish. Do not search for meat dishes." (or the vegetable mirror).
- **Runs:** all four, plus the plan 2a set with vectors twice more.
- **Result:** `all_dishes_with_eggs` took 3 calls in all four plan 2a runs. But `three_vegetable_dishes_only_eggs` went to 7 calls in one run for a different reason: it searched for green vegetables because it did not see egg dishes as real vegetable dishes. Over the six runs, 1 of 60 plan scenarios went over 5 calls.
- **Decision (user):** revert this change. It answered one evaluation scenario, and each such line closes only one of the model's reasons for searching. Stop tuning; judge model calls by the average (3.0-3.5 per plan scenario, against about 4) and treat a single scenario over 5 as a warning. After the revert the code was the same as round 4, so round 4's runs were the results of record until round 6.

### Round 6 — meat and vegetable bounds (2026-11-15, `fe6ed93`) — results of record

- **Changed:** plan 2a Task 11 (design 5.11). `parse_v3` reads "至少" / "最多" on meat and vegetable counts and the next-action buttons' messages; each side gets a range used by validation, the feasibility check, the shortlist, and every text; the feasibility check now covers the side the user did not name; a default total is clamped to the stated bounds; contradictions ask with `split_conflicts_total`. The evaluation runner gained two-message scenarios, a range expectation, and saves every planning run of a thread. New scenario file `scenarios_counts.yaml`. Prompts `route_v2+parse_v3+planner_v3`; tools unchanged.
- **Runs:** the four usual runs and the counts set once (no vectors).
- **Result:** every run as expected: baseline 7/7 twice, plan 2a 18/18 twice, counts 10/10; first pass 6/6, 12/12, 8/8. No scenario over 5 calls; average calls per plan scenario 3.0-3.2 (3.5 in the counts set, where the two button scenarios take 5 calls over two turns). In the counts set, "8个菜，2个荤菜" with 5 makeable vegetable dishes stopped at once with `not_enough_dishes` and `relax_split`, and "8个菜，至少2个荤菜" with the same stock planned 3 meat and 5 vegetable dishes. Both button conversations ended with a plan on the second turn.
- **Cost:** the uncached cost on the baseline set rose from $0.0128 to $0.0146 and the largest call from 3,022 to 3,493 tokens, because `parse_v3` is a longer prompt (the parse call is the largest). The cache-aware cost stayed between $0.003 and $0.005.

### Status after round 6

Against the criteria, on the round 6 runs: first pass 6/6, 12/12 and 8/8; average calls 3.0-3.5 per plan scenario; every infeasible scenario stopped within 2 calls with the expected reasons and next actions; uncached cost $0.0146 on the baseline set against $0.064, and model time 3.4-3.5 s against 11.3 s; largest call 3,658 tokens against the 32,000 budget; every scenario of all three sets as expected, with no scenario over 5 calls in this round. The call limits (`MODEL_CALL_LIMIT` 6, `TOOL_CALL_LIMIT` 10 per attempt) were never reached in any round: the most planner calls in one attempt was 5 (round 4).

Known pattern to watch: when the shortlist is complete but the model is unhappy with it (no meat dish to balance the meal, egg dishes as the only "vegetable dishes"), it sometimes ignores the instruction not to search. It costs calls, not correctness.
