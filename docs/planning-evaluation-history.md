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
| 8 | 20261118T074847Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0035 | $0.0185 | 0.92 | 5206 | 3.5 s | 5.9 s |
| **9** | 20261120T102629Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0039 | $0.0211 | 0.93 | 5984 | 4.0 s | 6.2 s |

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
| 8 | 20261118T074917Z | no | 18/18 | 12/12 | 3.17 | 5 | $0.0082 | $0.0466 | 0.92 | 5203 | 3.3 s | 6.4 s | — |
| **9** | 20261120T102702Z | no | 18/18 | 12/12 | 3.08 | 4 | $0.0095 | $0.0524 | 0.94 | 5981 | 3.7 s | 5.6 s | — |

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
| 8 | 20261118T075026Z | no | 10/10 | 7/8 | 3.75 | 5 | $0.0060 | $0.0323 | 0.92 | 5223 | 4.5 s | 7.6 s | — (one scenario passed on the second attempt) |
| 9 | 20261120T102816Z | no | 8/10 | 4/6 | 4.88 | 15 | $0.0084 | $0.0492 | 0.93 | 6017 | 5.5 s | 21.7 s | relax_split_button (the button message was routed `revise`: 13 calls in that turn, 15 in all, no plan); relax_named_button (routed `unsure`, stopped at the choice card). First pass is over the 6 scenarios that made a new plan; two of them passed on the second attempt |
| **9 (button fix)** | 20261121T073929Z | no | 10/10 | 7/8 | 3.38 | 4 | $0.0074 | $0.0354 | 0.91 | 6001 | 4.0 s | 6.3 s | — (three_dishes_at_most_one_vegetable_shopping passed on the second attempt; each button scenario 4 calls over two turns) |

## Comparison: diners development set (17 scenarios, all expect a plan)

`scenarios_diners.yaml` with `library_diners.yaml`, added in plan 2b Task 11 and written by Claude: the diner card, saved friends and guests, nicknames, ask-first answers, plan again after an allergen confirmation. Tuning against it is allowed in plan 2b Task 12 only, with the user's approval. The two-turn scenarios count both turns. `nickname sent` and `label shown` mismatches: none in any run.

| Round | Run | Prompts | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Cache hit | Largest call (tokens) | Model time avg | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 20261118T065901Z | parse_v4 | 16/17 | 16/17 | 3.35 | 8 | $0.0116 | $0.0563 | 0.89 | 5216 | 4.2 s | 12.7 s | guest_dislike_is_a_warning ("只做一个凉拌菜" gave no dish count, so two people defaulted to two dishes and the fridge stopped it early) |
| 8 | 20261118T072512Z | parse_v5 | 16/17 | 17/17 | 3.59 | 9 | $0.0125 | $0.0616 | 0.90 | 5245 | 4.6 s | 12.7 s | plan_again_after_confirmation (the re-plan's parse dropped the previous query, so 香煎鸡腿 was not chosen) |
| 8 | 20261118T074711Z | parse_v5, plan again in code | 17/17 | 17/17 | 3.12 | 4 | $0.0092 | $0.0521 | 0.93 | 5223 | 3.9 s | 5.3 s | — |
| **9** | 20261120T102921Z | route_v3, parse_v6 | 16/17 | 17/17 | 3.59 | 10 | $0.0142 | $0.0699 | 0.90 | 6001 | 5.2 s | 16.1 s | unmatched_people_none (card others 1, expected 2). The 10 calls are `plan_again_after_confirmation` over its turns, as expected |

## Comparison: held-out sets

The requests were written by the user (2026-11-17); Claude added stock, friends, card answers and expectations. Each set runs once per plan, after the development set and the code are final, and nothing is changed in response within that plan. Both use `library_diners.yaml`.

| Round | Set | Run | Scenarios | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Largest call (tokens) | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | `scenarios_heldout.yaml` (diners) | 20261118T075129Z | 14 (all expect a plan) | 13/14 | 12/14 | 3.43 | 7 | $0.0114 | $0.0500 | 7401 | 11.3 s | diners_five_lunch_guest_fish_allergy: 7 calls against 5 (a call-count warning only; the plan was right) |
| 8 | `scenarios_heldout_core.yaml` (one diner) | 20261118T075247Z | 22 (20 expect a plan) | 19/22 | 19/20 | 3.05 | 5 | $0.0128 | $0.0607 | 5221 | 6.1 s | core_one_dessert, core_en_dinner_with_fish, core_en_spicy_shopping (see round 8) |
| **9** | `scenarios_heldout.yaml` (diners) | 20261120T103101Z | 14 (all expect a plan) | 13/14 | 14/14 | 3.14 | 4 | $0.0090 | $0.0492 | 6016 | 6.9 s | diners_friend_cannot_eat_nuts (see round 9) |
| **9** | `scenarios_heldout_core.yaml` (one diner) | 20261120T103213Z | 22 (20 expect a plan) | 20/22 | 19/19 | 3.35 | 7 | $0.0142 | $0.0773 | 6015 | 6.6 s | core_en_dinner_with_fish, core_en_spicy_shopping (see round 9). First pass is over the 19 new plans; `core_two_meat_one_vegetable_then_one_dish` was judged on a revision turn, which passed |

## Comparison: revise sets (plan 3b-1)

`scenarios_revise.yaml` (development, 19 scenarios, 17 expect a plan) and `scenarios_revise_heldout.yaml` (held-out, 18 scenarios, all expect a plan, 1 deferred to plan 3b-2), both with `library_revise.yaml`. Each scenario is a first message (or a fixed first plan), optional hand edits between the turns, and a follow-up; the follow-up is the judged turn. The development scenarios were built by Claude from the user's examples and tuning against them was allowed in plan 3b-1 Task 8 (three runs at most); the held-out requests were written by the user and run once.

Columns: "Revisions passed" counts judged turns that ran the revise step and ended with a passed plan (the two development scenarios that expect the revision to stop count as not passed). "Calls / revise turn" is the average and the most model calls in one judged revise turn (that turn's calls only: route, parse, revise attempts and searches). Follow-up route and first-message route are route accuracy. Avg and max calls are per scenario over both turns. Cost and largest call are over all scenarios; wall p95 too.

| Round | Set | Run | Prompts | As expected | Revisions passed | Locked removed | Calls / revise turn | Follow-up route | First-message route | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Largest call (tokens) | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 9 (dev 1) | development | 20261120T081202Z | route_v3, parse_v5, revise_v1 | 17/19 | 9/13 | 0 | 3.7 / 5 | 16/17 | 2/2 | 6.42 | 8 | $0.0260 | $0.1305 | 5352 | 12.8 s | add_one_more_after_hand_remove_and_add, choice_card_answered_revise |
| 9 (dev 2) | development | 20261120T091620Z | route_v3, parse_v6, revise_v1 | 16/19 | 9/12 | 0 | 3.6 / 5 | 16/17 | 2/2 | 5.95 | 8 | $0.0262 | $0.1283 | 6076 | 12.8 s | general_spicier_keeps_hand_added, fridge_only_wish_unmet, choice_card_answered_revise |
| 9 (dev 3) | development | 20261120T101036Z | route_v3, parse_v6, revise_v2 | 18/19 | 11/13 | 0 | 3.5 / 6 | 17/17 | 2/2 | 6.00 | 9 | $0.0250 | $0.1323 | 6082 | 12.4 s | general_spicier |
| **9** | development | 20261120T102047Z | route_v3, parse_v6, revise_v2 | 16/19 | 11/12 | 0 | 3.2 / 4 | 16/17 | 2/2 | 5.74 | 7 | $0.0246 | $0.1252 | 6077 | 11.7 s | general_spicier, general_spicier_keeps_hand_added, fridge_only_wish_unmet |
| **9** | held-out | 20261120T102324Z | route_v3, parse_v6, revise_v2 | 9/17, deferred 0/1 | 11/14 | 0 | 3.9 / 8 | 15/15 | — | 6.89 | 11 | $0.0279 | $0.1486 | 6083 | 17.8 s | eight scenarios (see round 9) |

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

### Round 8 — plan 2b: several diners, parse_v4 and parse_v5, held-out sets (2026-11-18) — results of record until round 9

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

### Round 9 — plan 3b-1: revising the plan (route_v3, parse_v6, revise_v2), revise sets (2026-11-20/21) — results of record

- **Changed since round 8:** plan 3a (manual edits on the plan card: remove, add, keep, restore, checked by code; no prompt change and no evaluation round) and plan 3b-1 (design `2026-11-19-phase-1-plan-3b1-revise-plan-design.md`): a follow-up after a plan now revises the current plan instead of planning anew. `route_v3` adds `revise` and `unsure` (`unsure` shows a choice card: revise this plan or plan a new meal). On a revise turn the parse input gets an app-written line describing the current plan. A new revise step (`revise_v1`, then `revise_v2`) answers with a change list (`RevisionProposal`: the dishes to remove, each named one with its quote from the message; the dishes to add; the target kind, named, scope, general or unclear; a note). Code checks the change (a locked dish leaves only when the message names it, quotes, change size, the revision's counts), then validates it like a plan and retries with the reason; a passed revision is a new model version, and locks and overrides are stored on every version. The planner prompt (`planner_v4`) and the planner's tools (`f8e0a9e09f6e`) are unchanged; the revise step's tools add `RevisionProposal` (`1f17814b542a`). The evaluation runner gained follow-ups on a fixed first plan, hand edits between the turns, the choice-card answer, and the revision metrics (revisions passed, locked dishes removed, dishes changed, calls per revise turn, follow-up and first-message route accuracy). New scenario files: `scenarios_revise.yaml` and `scenarios_revise_heldout.yaml` (see "Comparison: revise sets").
- **Thresholds, set in the plan before the round ran:** follow-up routing at least 90% on the development set and on the held-out set; first-message routing: no scenario of the existing sets not as expected because of the route; locked dishes wrongly removed: 0 in every run; as expected: at least 90% of the development set and at least 80% of the held-out set (deferred scenarios reported apart); model calls per revise turn: at most 4 on average, no turn over 7.
- **Development runs (plan Task 8, three allowed):**
  - Run 1 `20261120T081202Z` at `a1d6f8a` (`route_v3+parse_v5+planner_v4+revise_v1`): 17/19. `add_one_more_after_hand_remove_and_add`: on a plan of 2 grown by hand to 3, "再加一道" was computed by `parse_v5` from the first request (2 + 1 = 3), so nothing was added. `choice_card_answered_revise`: the scenario's follow-up was not ambiguous, so the route answered `revise` instead of `unsure`; its sentence was replaced by "要不换个思路？" (the sentence of the plan-side choice-card scenario; development set only). Led to `parse_v6`: the current-plan line is described as context, relative counts are computed from the current plan, and `counts_stated_now` says whether the newest message states a count.
  - Run 2 `20261120T091620Z` at `361d7a7` (`parse_v6`): 16/19. The relative count passed. `general_spicier_keeps_hand_added`: for "想要辣一些的" the revise step swapped a vegetable dish for a spicy meat dish (H4: a revision keeps the plan's meat and vegetable split), then gave a malformed answer, then an empty change. `choice_card_answered_revise`: after the user chose "revise" on the card, the revise step answered `unclear` to a directionless wish. `fridge_only_wish_unmet`: the route answered `unsure` for "想吃点海鲜" (run 1: `revise`), so the turn stopped at the card. Led to `revise_v2`: a replacement takes the type of the dish it replaces; a directionless wish to change is a general wish, and `unclear` is only for a message that points at a dish ambiguously; the H4 retry text on a revision says which side is off. No `route_v4`: one case of route variance, and the choice card is an acceptable answer.
  - Run 3 `20261120T101036Z` at `325bf97` (`revise_v2`): 18/19, follow-up route 17/17. Left: `general_spicier` (on 2 meat and 2 vegetable dishes the revise step twice swapped the vegetable dish 番茄炒蛋 for the spicy meat dish 辣椒炒肉 despite the hint, then swapped it for 鸡蛋饼, which passed but is not spicy; the right move was to replace a meat dish). The three-run limit was reached; the user agreed to carry it over.
- **Round at `7e8a9d0`** (`route_v3+parse_v6+planner_v4+revise_v2`), every set once without vectors on the test database: revise development 16/19 (`102047Z`), revise held-out 9/17 plus the deferred scenario 0/1 (`102324Z`), baseline 7/7 (`102629Z`), plan 2a 18/18 (`102702Z`), counts 8/10 (`102816Z`), diners 16/17 (`102921Z`), held-out diners 13/14 (`103101Z`), held-out one diner 20/22 (`103213Z`).
- **Against the thresholds — the round does not meet the plan's thresholds:**
  - Follow-up routing: development 16/17 (94%), held-out 15/15. **Met.**
  - First-message routing: no scenario of the existing sets missed because of a first message's route (round 8's route miss `core_one_dessert` is now routed `plan` and passes), and the development set's two first-message checks were 2/2. **But the counts set's two next-action button scenarios regressed:** their second message, the fixed text of a next-action button, went through `route_v3` and was routed `revise` (`relax_split_button`, 13 calls in that turn, no plan) and `unsure` (`relax_named_button`, stopped at the choice card). A code bug from plan Task 5. **Not met in the round; fixed at `e870121`** (the fixed button messages skip the route and plan from scratch with a parse, as before 3b-1); counts rerun `20261121T073929Z`: 10/10, each button scenario 4 calls.
  - Locked dishes wrongly removed: 0 in every run. **Met.**
  - As expected: development 16/19 = 84%, **not met** (below 90%); held-out 9/17 = 53%, **not met** (below 80%). The deferred held-out scenario (`friend_joins_and_two_more_dishes`, a diner change with more dishes) is reported apart: 0/1, as designed until plan 3b-2 (a change in who eats plans from scratch, so a dish of the first plan was not kept).
  - Model calls per revise turn: average 3.2 (development) and 3.9 (held-out), **met**; most in one turn 4 (development) but 8 in the held-out set (`swap_for_a_dish_not_in_the_library`), **not met**. Before the fix, the counts set's `relax_split_button` used 13 in one turn.
- **Development set, not as expected** (not changed after run 3). Two of the three passed in run 3 on the same code, so part of the gap is run-to-run variance:
  - `general_spicier` ("想要辣一些的", 2 meat and 2 vegetable dishes): the only spicy dish in the revise step's shortlist was the meat dish 辣椒炒肉 (the spicy vegetable dish 酸辣土豆丝 is in the library but did not reach that shortlist, since the query "辣一些的" matches no title, and the step did not search). The first answer swapped the vegetable dish 番茄炒蛋 for 辣椒炒肉 (H4); the retry swapped 番茄炒蛋 for 松仁玉米, which passed but is not spicy, so the wish went unmet and only the model's note said so.
  - `general_spicier_keeps_hand_added` (the same, with a hand-added vegetable dish locked): the first answer's note said the only spicy dish is a meat dish and it would change nothing, yet it removed 番茄炒蛋 without adding a dish (H4); the retry swapped 番茄炒蛋 for 清炒蘑菇. Passed, not spicy. Both scenarios show the revise step not replacing a meat dish under a general wish even when it sees that this is the move.
  - `fridge_only_wish_unmet`: the route answered `unsure` for "想吃点海鲜" again (as in run 2), so the turn stopped at the choice card before the revise step.
- **Held-out set, not as expected** (recorded as they are; nothing was changed in response within plan 3b-1):
  - `drop_the_corn_dish_keep_the_rest` ("玉米的不要，其他的就这样吧"): the corn dish was replaced by 清炒蘑菇; the user expected it dropped and the plan one dish smaller. The design kept the dish count unless the message states a number (design 5.2; a bare named removal is not a count change).
  - `drop_the_meat_dishes` ("最近胖了，把肉菜去了吧", 2 meat and 2 vegetable dishes): the first answer removed both meat dishes without a replacement, which failed H4 because the count stays; the retry replaced them with two other meat dishes, against the wish. Expected: the two vegetable dishes only. Same count rule.
  - `remove_what_does_not_suit_after_a_kept_egg_dish` ("还是把不适合的菜去掉吧", after the user had kept an egg dish despite its conflict, so the dish was locked): the revise step replaced the egg dish, which the lock check rejected (a general wish never removes a locked dish, and "不适合的菜" quotes no title, ingredient or category word); the retry changed nothing and reported the wish unmet. Expected: the egg dish dropped, 3 dishes. Same count rule, plus a reference to a dish by its conflict, which the quote check cannot match.
  - `noodles_to_rice` ("把面换成饭"): the revise step swapped 清汤面 for 白米饭 three times, quoting the whole message, then "面"; the quote check rejected each, because a match needs at least two characters of the title (or an ingredient name or a category word) and staple foods have no category word. The plan stayed unchanged.
  - `swap_for_a_dish_not_in_the_library` ("鱼香肉丝不要，换成西红柿炒鸡蛋"; the library has no such dish): every answer replaced the meat dish with the soup 番茄蛋花汤 (H4: one meat dish and one soup required), with searches between the attempts; after three failures the plan stayed unchanged with H4 next actions. 8 calls in the turn.
  - `add_a_green_vegetable` ("加个青菜"): the first answer replaced 青椒肉丝 with a vegetable dish instead of adding one (H4); the retry added 清炒菠菜 but also swapped 青椒肉丝 for 葱爆羊肉. Expected no dish changed.
  - `no_meat_today_english` ("I don't want to eat meat today."): the parse put "肉" into the user's own rules (`self_rules`, a restriction). A new rule about the user brings the diner card back, and a card on a revise turn makes it a new plan (design 5.1), so the meal was planned from scratch: no meat, but the plan's vegetable dish 清炒小白菜, which a revision would have kept, was gone.
  - `too_many_tomatoes_but_the_plan_has_potatoes` ("Too many tomatoes!!!!!!" on a plan with potatoes and no tomato): the parse put tomato into `self_rules` as a dislike, so the same path planned a new meal of four different dishes instead of leaving the plan as it was; the new note also wrote "蘑菇" in an English reply.
- **Other sets, not as expected:**
  - Counts `relax_split_button` and `relax_named_button`: the button regression above; fixed and rerun.
  - Diners `unmatched_people_none` ("这周六家里一共四个人吃饭，我和Diner 2都在…"): the parse returned one empty unnamed guest for the two people not named, so the card showed one guest and 1 other instead of 2 others. It passed in round 8 on `parse_v5`; `parse_v6` only added lines, so this is variance or an interaction of the new lines.
  - Held-out `diners_friend_cannot_eat_nuts` ("一个朋友今天来吃晚餐，她没法吃坚果，其他的都行"): the parse wrote "坚果" as a text restriction instead of the tree-nut allergen, so code did not apply the allergen rule (the plan happened to hold no nuts). It passed in round 8.
  - Held-out `core_en_dinner_with_fish`: the plan was right and the note now English (round 8's miss), but 7 calls against 5: the planner searched "fish" and "fish seafood" three times, which keyword search cannot match in a Chinese library without vectors.
  - Held-out `core_en_spicy_shopping`: as in round 8 (葱爆羊肉 for "spicy"; same cause).
- **Notes:** two notes named a dish outside their plan (`outside_dish`; round 8 had four): one in the diners set, which also gave confused allergy advice about the plan's cashew dish, and one in the core held-out set that named a dish and then corrected itself. One English note held a Chinese word (above). `planner_v4` is unchanged.
- **Cost:** the round's eight runs $0.1117 cache-aware and $0.5928 uncached (summed from the runs' `summary.json`). The approved estimate was about $0.10-0.12 cache-aware and at most $0.55 uncached: the cache-aware cost is within it, the uncached cost about $0.04 above. The three development runs added $0.0772 and $0.3911, the counts rerun (approved separately, about $0.01 / $0.05) $0.0074 and $0.0354: plan 3b-1 spent $0.1963 cache-aware and $1.019 uncached on evaluation in all.
- **Largest call:** about 6,080 tokens, the parse call with `parse_v6` (round 8: about 5,200). Far below the 32,000 budget.
- **Decision (user, 2026-11-21):** record the round as it is; fix the button regression and rerun the counts set (done); merge plan 3b-1 after its final review; fix the causes above in a follow-up plan 3b-1b on a new branch, with a new held-out batch from the user, because this batch has now been seen. In 3b-1b a named removal without a number drops the dish (the plan gets smaller), and only "换 / 换成 / 换一个" replaces it. The items are in `docs/phase-1-carryover.md`, "Deferred from Phase 1 plan 3b-1".
- **Results of record:** this round ran every set on the final prompt versions, so its runs replace round 8's as the results of record. For the counts set the rerun at `e870121` holds them; the button fix changed only the path of the fixed button messages, which no scenario of the other sets sends.

### Status after round 6

Against the criteria, on the round 6 runs: first pass 6/6, 12/12 and 8/8; average calls 3.0-3.5 per plan scenario; every infeasible scenario stopped within 2 calls with the expected reasons and next actions; uncached cost $0.0146 on the baseline set against $0.064, and model time 3.4-3.5 s against 11.3 s; largest call 3,658 tokens against the 32,000 budget; every scenario of all three sets as expected, with no scenario over 5 calls in this round. The call limits (`MODEL_CALL_LIMIT` 6, `TOOL_CALL_LIMIT` 10 per attempt) were never reached in any round: the most planner calls in one attempt was 5 (round 4).

Known pattern to watch: when the shortlist is complete but the model is unhappy with it (no meat dish to balance the meal, egg dishes as the only "vegetable dishes"), it sometimes ignores the instruction not to search. It costs calls, not correctness.
