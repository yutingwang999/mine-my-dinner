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
| 6 | 20261115T040226Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0046 | $0.0146 | 0.79 | 3493 | 3.5 s | 4.9 s |
| 6 | 20261115T040255Z | yes | 7/7 | 6/6 | 3.00 | 3 | $0.0033 | $0.0147 | 0.90 | 3493 | 3.4 s | 5.5 s |
| 7 | 20261116T042755Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0042 | $0.0148 | 0.83 | 3493 | 3.2 s | 4.6 s |
| 8 (parse_v4) | 20261118T070858Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0032 | $0.0182 | 0.93 | 5166 | 3.4 s | 5.3 s |
| **8** | 20261118T074847Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0035 | $0.0185 | 0.92 | 5206 | 3.5 s | 5.9 s |

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
| 6 | 20261115T040333Z | no | 18/18 | 12/12 | 3.08 | 4 | $0.0081 | $0.0363 | 0.90 | 3658 | 3.0 s | 5.6 s | — |
| 6 | 20261115T040434Z | yes | 18/18 | 12/12 | 3.17 | 4 | $0.0081 | $0.0372 | 0.91 | 3602 | 3.0 s | 4.6 s | — |
| 7 | 20261116T042822Z | no | 18/18 | 12/12 | 3.08 | 4 | $0.0086 | $0.0363 | 0.88 | 3554 | 3.0 s | 5.1 s | — |
| 7 | 20261116T042924Z | no | 18/18 | 12/12 | 3.17 | 5 | $0.0081 | $0.0378 | 0.91 | 3792 | 3.2 s | 5.7 s | — |
| 7 | 20261116T043028Z | no | 18/18 | 12/12 | 3.17 | 5 | $0.0079 | $0.0376 | 0.91 | 3661 | 3.2 s | 6.4 s | — |
| 8 (parse_v4) | 20261118T070934Z | no | 18/18 | 12/12 | 3.17 | 5 | $0.0080 | $0.0464 | 0.93 | 5163 | 3.3 s | 6.2 s | — |
| **8** | 20261118T074917Z | no | 18/18 | 12/12 | 3.17 | 5 | $0.0082 | $0.0466 | 0.92 | 5203 | 3.3 s | 6.4 s | — |

Round 1 ran the baseline set only. The check-only scenario (`one_main_ingredient_dominates`) passed in every plan 2a run.

## Comparison: counts set (10 scenarios, 8 expect a plan)

`scenarios_counts.yaml` with `library_extended.yaml`, added in round 6: meat and vegetable bounds, the implied side, and the next-action buttons' messages. These scenarios took no part in tuning. The two button scenarios (`relax_split_button`, `relax_named_button`) send two user messages, so their calls count both turns (route and parse twice); they count as plan scenarios.

| Round | Run | Vectors | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Cache hit | Largest call (tokens) | Model time avg | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 6 | 20261115T040549Z | no | 10/10 | 8/8 | 3.50 | 5 | $0.0059 | $0.0238 | 0.88 | 3510 | 3.7 s | 6.1 s | — |
| 7 | 20261116T043132Z | no | 10/10 | 8/8 | 3.50 | 5 | $0.0058 | $0.0239 | 0.88 | 3510 | 3.6 s | 5.8 s | — |
| 7 | 20261116T043213Z | no | 10/10 | 8/8 | 3.88 | 7 | $0.0057 | $0.0268 | 0.92 | 3510 | 4.0 s | 6.7 s | — (relax_split_button 7 calls over two turns, within its limit) |
| 7 | 20261116T043258Z | no | 9/10 | 7/8 | 4.25 | 7 | $0.0070 | $0.0312 | 0.90 | 5542 | 4.3 s | 9.3 s | eight_dishes_at_least_two_meat_fridge (6 calls, over its limit of 5); three_dishes_at_most_one_vegetable_shopping passed on the second attempt |
| 8 (parse_v4) | 20261118T071048Z | no | 10/10 | 7/8 | 3.88 | 5 | $0.0058 | $0.0331 | 0.93 | 5183 | 4.3 s | 7.3 s | — (three_dishes_at_most_one_vegetable_shopping passed on the second attempt: the planner proposed 4 dishes first) |
| **8** | 20261118T075026Z | no | 10/10 | 7/8 | 3.75 | 5 | $0.0060 | $0.0323 | 0.92 | 5223 | 4.5 s | 7.6 s | — (one scenario passed on the second attempt) |

## Comparison: diners development set (17 scenarios, all expect a plan)

`scenarios_diners.yaml` with `library_diners.yaml`, added in plan 2b Task 11 and written by Claude: the diner card, saved friends and guests, nicknames, ask-first answers, plan again after an allergen confirmation. Tuning against it is allowed in plan 2b Task 12 only, with the user's approval. The two-turn scenarios count both turns. `nickname sent` and `label shown` mismatches: none in any run.

| Round | Run | Prompts | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Cache hit | Largest call (tokens) | Model time avg | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 20261118T065901Z | parse_v4 | 16/17 | 16/17 | 3.35 | 8 | $0.0116 | $0.0563 | 0.89 | 5216 | 4.2 s | 12.7 s | guest_dislike_is_a_warning ("只做一个凉拌菜" gave no dish count, so two people defaulted to two dishes and the fridge stopped it early) |
| 8 | 20261118T072512Z | parse_v5 | 16/17 | 17/17 | 3.59 | 9 | $0.0125 | $0.0616 | 0.90 | 5245 | 4.6 s | 12.7 s | plan_again_after_confirmation (the re-plan's parse dropped the previous query, so 香煎鸡腿 was not chosen) |
| **8** | 20261118T074711Z | parse_v5, plan again in code | 17/17 | 17/17 | 3.12 | 4 | $0.0092 | $0.0521 | 0.93 | 5223 | 3.9 s | 5.3 s | — |

## Comparison: held-out sets

The requests were written by the user (2026-11-17); Claude added stock, friends, card answers and expectations. Each set runs once per plan, after the development set and the code are final, and nothing is changed in response within that plan. Both use `library_diners.yaml`.

| Round | Set | Run | Scenarios | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Largest call (tokens) | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **8** | `scenarios_heldout.yaml` (diners) | 20261118T075129Z | 14 (all expect a plan) | 13/14 | 12/14 | 3.43 | 7 | $0.0114 | $0.0500 | 7401 | 11.3 s | diners_five_lunch_guest_fish_allergy: 7 calls against 5 (a call-count warning only; the plan was right) |
| **8** | `scenarios_heldout_core.yaml` (one diner) | 20261118T075247Z | 22 (20 expect a plan) | 19/22 | 19/20 | 3.05 | 5 | $0.0128 | $0.0607 | 5221 | 6.1 s | core_one_dessert, core_en_dinner_with_fish, core_en_spicy_shopping (see round 8) |

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

### Round 6 — meat and vegetable bounds (2026-11-15, `fe6ed93`)

- **Changed:** plan 2a Task 11 (design 5.11). `parse_v3` reads "至少" / "最多" on meat and vegetable counts and the next-action buttons' messages; each side gets a range used by validation, the feasibility check, the shortlist, and every text; the feasibility check now covers the side the user did not name; a default total is clamped to the stated bounds; contradictions ask with `split_conflicts_total`. The evaluation runner gained two-message scenarios, a range expectation, and saves every planning run of a thread. New scenario file `scenarios_counts.yaml`. Prompts `route_v2+parse_v3+planner_v3`; tools unchanged.
- **Runs:** the four usual runs and the counts set once (no vectors).
- **Result:** every run as expected: baseline 7/7 twice, plan 2a 18/18 twice, counts 10/10; first pass 6/6, 12/12, 8/8. No scenario over 5 calls; average calls per plan scenario 3.0-3.2 (3.5 in the counts set, where the two button scenarios take 5 calls over two turns). In the counts set, "8个菜，2个荤菜" with 5 makeable vegetable dishes stopped at once with `not_enough_dishes` and `relax_split`, and "8个菜，至少2个荤菜" with the same stock planned 3 meat and 5 vegetable dishes. Both button conversations ended with a plan on the second turn.
- **Cost:** the uncached cost on the baseline set rose from $0.0128 to $0.0146 and the largest call from 3,022 to 3,493 tokens, because `parse_v3` is a longer prompt (the parse call is the largest). The cache-aware cost stayed between $0.003 and $0.005.

### Round 7 — planner_v4 note rules and model comparison (2026-11-16, `d4b2e30`)

- **Changed:** chat page redesign Task 4 (design 5.5 of `2026-11-15-chat-page-redesign-design.md`). `planner_v4` changes only the note rule: one sentence with one practical tip; do not count or list the plan's dishes; name no dish outside the plan and suggest no swaps. Prompts `route_v2+parse_v3+planner_v4`; tools and the `PlanProposal` schema unchanged. The evaluation gained note checks (`notes` per group, `note_flags` per scenario), `--planning-model`, and the `notes_review` export.
- **Runs:** the baseline set once with the default model (`20261116T042755Z`), then each of the five tool-calling catalog models on the plan 2a and counts sets, three runs each, all without vectors (30 runs, `20261116T042822Z` to `20261116T051816Z`). The `deepseek-flash` runs are the default model's rows in the tables above; the others are in "Model comparison" below. Estimated spend for the 31 runs: $1.07 cache-aware, $1.98 uncached.
- **Result (default model):** baseline 7/7; plan 2a 18/18 three times; counts 10/10, 10/10 and 9/10. Planning results match round 6: the note rule changed nothing else. The one mismatch is a call-count warning (`eight_dishes_at_least_two_meat_fridge`, 6 calls against 5).
- **Notes:** no note in any run named a library dish outside its plan (`outside_dish` 0 for every model). See "Model comparison" for the other checks and the user's ratings.

### Model comparison (round 7)

The whole `planning_eval` task (route, parse, planner) ran on each model with no fallback, on prompts `route_v2+parse_v3+planner_v4` at `d4b2e30`. Figures are the mean over three runs, with the range in brackets. Calls are the average over plan scenarios, followed by the most calls in any one scenario. Cost and model time are per run (cost) and per scenario (time), over all scenarios. Wall p95 is over plan scenarios.

**Plan 2a set (18 scored scenarios, 12 expect a plan)**

| Model | As expected | First pass | Calls | Cost (cache-aware) | Cost (uncached) | Model time avg | Wall p95 | Note flags (36 notes) | Not as expected |
|---|---|---|---|---|---|---|---|---|---|
| deepseek-flash (default) | 54/54 | 36/36 | 3.14 [3.08-3.17], max 5 | $0.0082 [0.0079-0.0086] | $0.037 | 3.1 s | 5.7 s [5.1-6.4] | counts 16, multi 1 | — |
| deepseek-v4-pro | 54/54 | 36/36 | 3.06 [3.00-3.17], max 5 | $0.028 [0.022-0.040] | $0.155 | 4.2 s | 6.4 s [5.5-7.7] | counts 15 | — |
| gpt-6-luna | 53/54 | 35/36 | 3.47 [3.42-3.58], max 6 | $0.0042 [0.0037-0.0050] | $0.012 | 5.7 s | 8.7 s [8.2-9.6] | counts 4 | `eight_dishes_two_meat` once (6 calls, one retry) |
| gemini-3.5-flash-lite | 51/54 | 36/36 | 3.33 [3.33-3.33], max 6 | $0.036 | $0.036 | 3.7 s | 7.9 s [7.7-8.1] | counts 11 | `eight_dishes_two_meat` in every run (6 calls) |
| gemini-3.8-flash | 54/54 | 36/36 | 3.00, max 3 | $0.121 [0.115-0.130] | $0.131 | 15.6 s | 38.9 s [24.1-66.2] | counts 6 | — |

**Counts set (10 scenarios, 8 expect a plan)**

| Model | As expected | First pass | Calls | Cost (cache-aware) | Cost (uncached) | Model time avg | Wall p95 | Note flags (24 notes) | Not as expected |
|---|---|---|---|---|---|---|---|---|---|
| deepseek-flash (default) | 29/30 | 23/24 | 3.88 [3.50-4.25], max 7 | $0.0062 [0.0057-0.0070] | $0.027 | 3.9 s | 7.3 s [5.8-9.3] | counts 12, multi 1 | `eight_dishes_at_least_two_meat_fridge` once (6 calls) |
| deepseek-v4-pro | 30/30 | 22/24 | 3.62, max 5 | $0.019 [0.017-0.022] | $0.108 | 5.3 s | 9.0 s [8.2-9.4] | counts 8 | — |
| gpt-6-luna | 30/30 | 24/24 | 4.46 [4.38-4.50], max 5 | $0.0030 [0.0028-0.0035] | $0.010 | 7.2 s | 10.5 s [9.8-11.4] | counts 3 | — |
| gemini-3.5-flash-lite | 23/30 | 23/24 | 4.88 [4.62-5.25], max 9 | $0.032 [0.030-0.034] | $0.032 | 5.3 s | 11.2 s [10.7-11.6] | counts 4 | `three_dishes_no_vegetable_shopping` in every run (wrong split: 1-2 meat and 1-2 vegetable dishes for "no vegetable, 3 dishes"); `relax_named_button` in every run (8-9 calls against 7); `eight_dishes_at_least_two_meat_fridge` once (9 calls) |
| gemini-3.8-flash | 29/30 | 24/24 | 3.67 [3.50-4.00], max 7 | $0.097 [0.090-0.106] | $0.106 | 21.1 s | 45.3 s [25.4-59.7] | counts 2 | `five_people_at_most_one_each_shopping` once (7 calls) |

Gemini models showed little or no cache hits (gemini-3.5-flash-lite 0.00 in every run; gemini-3.8-flash 0.00-0.37), so their cache-aware cost is close to the uncached one.

**The user's ratings.** The notes of run 1 of each set for each model (100 notes, 20 per model) were exported with `notes_review` and rated by the user, with the model names hidden by default: `quality` 1-3, judged by how the sentence reads as a whole, and `should_not_appear` y/n. Five single-dish notes were left without a y/n and are counted as n.

| Model | Avg quality | 3 / 2 / 1 | should_not_appear y | Auto-flagged (outside, counts, locale) |
|---|---|---|---|---|
| gemini-3.8-flash | 3.00 | 20 / 0 / 0 | 0 | 2 |
| deepseek-flash (default) | 2.90 | 19 / 0 / 1 | 0 | 6 |
| deepseek-v4-pro | 2.75 | 16 / 3 / 1 | 1 | 7 |
| gemini-3.5-flash-lite | 2.65 | 13 / 7 / 0 | 0 | 8 |
| gpt-6-luna | 2.50 | 10 / 10 / 0 | 0 | 2 |

- **The automatic checks against the user's judgement.** None of the 25 notes flagged `counts_dishes` was marked y by the user: almost all say "两道菜可同时上桌" or "三道都很快手", a correct reference to the dishes as a group, which the user accepted. The one note the user marked y (deepseek-v4-pro: "最后快炒肉丝和青菜" for a plan with 宫保鸡丁 and no 肉丝 dish) was not flagged: it names a dish loosely, not by its library title. As written, `counts_dishes` measures a wording the user does not object to, so its rate does not rank the models.
- **Wrong facts seen while reading (not caught by any check):** "一荤一素" for 蒜蓉西兰花 and 清炒小白菜, both vegetable dishes (deepseek-v4-pro, rated 1); "Diner 2 的饮食限制尚未确认" in two Chinese notes (deepseek-flash, default model), which shows the internal diner label to the user.
- **Decision (user, 2026-11-16):** keep deepseek-flash as the default planning model. It rated second (2.90 of 3), is the fastest, and costs a fraction of the models that rated close to it.

### Round 8 — plan 2b: several diners, parse_v4 and parse_v5, held-out sets (2026-11-18) — results of record

- **Changed:** plan 2b (design `2026-11-17-phase-1-plan-2b-diners-design.md`): the diner card before planning, saved friends' and guests' rules applied by code, nickname replacement with "Diner N" labels, unknown-allergen exclusions with their ingredient names, and `parse_v4` (guests, friend and self rules, diet words, the assistant's questions in the parse input, the trimmed examples). The evaluation runner gained friends, the card answer, ask-first answers, `between_turns`, and the `nickname sent` and `label shown` checks (Task 11). The planner prompt and tools are unchanged (`planner_v4`).
- **Runs at `8f6babd` (`route_v2+parse_v4+planner_v4`):** development set 16/17 (`20261118T065901Z`); regression baseline 7/7, plan 2a 18/18, counts 10/10 (`070858Z`, `070934Z`, `071048Z`). The regression sets match round 7, so the `parse_v4` trim removed nothing these scenarios need. The miss, `guest_dislike_is_a_warning`: "只做一个凉拌菜" gave no dish count under the cold-dish rule ("put the phrase in query, do not fill a count from it alone"). The user judged the stated number the better reading.
- **`parse_v5` at `bb74039`:** one rule changed (a stated count of cold dishes is the dish count; a cold dish in passing still gives none). Development set 16/17 (`072512Z`): the cold-dish scenario passed, but `plan_again_after_confirmation` failed because the re-plan's parse dropped the previous query ("香煎鸡腿"), and the turn-1 parse missed "就做一个香煎鸡腿" as one dish.
- **Plan again in code at `86729e8`:** the fixed "plan again" message now reuses the thread's latest parsed request with no route or parse call (user decision; spec 5.7 updated). Development set 17/17 (`074711Z`, 3.12 calls on average, largest call 5,223 tokens). The turn-1 count miss did not repeat, so it was read as variance and no `parse_v6` was made. Regression on `parse_v5`: baseline 7/7, plan 2a 18/18, counts 10/10 (`074847Z`, `074917Z`, `075026Z`).
- **Held-out at `86729e8`, once each:** diners 13/14 (`075129Z`), one diner 19/22 (`075247Z`). No nickname was sent and no label shown in either. The misses, recorded as they are:
  - `diners_five_lunch_guest_fish_allergy` ("5人份的午餐，别太复杂，有一个人对鱼过敏"): the unnamed guest's fish allergy was parsed and applied, and no fish dish was chosen; the planner proposed the wrong dish counts twice (H4) before a valid plan, 7 calls. A call-count warning only.
  - `core_one_dessert` ("想吃个甜品，推荐一个"): `route_v2` answered "other", so no plan was made. The route prompt describes planning as "deciding what to cook, planning meals or dinners"; a request to recommend one dessert fell outside it.
  - `core_en_dinner_with_fish` ("dinner, at least 1 fish,"): the plan was right (清蒸鲈鱼), but the note was written in Chinese for an English conversation.
  - `core_en_spicy_shopping` ("I want to have some spicy food today, shopping allowed"): parsed as shopping with query "spicy", but without recipe vectors an English query cannot match the Chinese library, so the planner chose 葱爆羊肉, not a spicy dish.
- **Notes:** four notes named a library dish outside their plan (`outside_dish`; round 7 had none): "青椒肉丝" (baseline), "蒜蓉西兰花" (counts), "清蒸鲈鱼" and "孜然羊排" (core held-out). Each names a dish the planner considered but did not choose. `planner_v4` did not change in 2b. To watch; see the carryover.
- **Cost:** eleven runs, $0.092 cache-aware and $0.48 uncached in total. The approved estimate (about $0.05 cache-aware, at most $0.22 uncached) covered the six planned runs; the five re-runs after `parse_v5` and the plan-again change were each agreed with the user.
- **Largest call:** now the parse call (about 5,200 tokens, with the parse prompt and the `ParsedRequest` schema; 97% of it cached). The five-diner scenario reached 7,401 in the planner after two retries. Far below the 32,000 budget.

### Status after round 6

Against the criteria, on the round 6 runs: first pass 6/6, 12/12 and 8/8; average calls 3.0-3.5 per plan scenario; every infeasible scenario stopped within 2 calls with the expected reasons and next actions; uncached cost $0.0146 on the baseline set against $0.064, and model time 3.4-3.5 s against 11.3 s; largest call 3,658 tokens against the 32,000 budget; every scenario of all three sets as expected, with no scenario over 5 calls in this round. The call limits (`MODEL_CALL_LIMIT` 6, `TOOL_CALL_LIMIT` 10 per attempt) were never reached in any round: the most planner calls in one attempt was 5 (round 4).

Known pattern to watch: when the shortlist is complete but the model is unhappy with it (no meat dish to balance the meal, egg dishes as the only "vegetable dishes"), it sometimes ignores the instruction not to search. It costs calls, not correctness.
