# Planning Evaluation History

A running record of the paid planning evaluations: for each round, what changed since the round before, which runs it made, and how the results compare across rounds. Add a round after every evaluation. How to run the evaluation and what each metric means is in `docs/local-development.md` (Planning evaluation).

## Scenario sets

Every scenario file under `backend/eval_data/planning/`, with its role. Each file states its role in a top-level `role:` key (`development`, `regression` or `held-out`), which the runner reports with each role's rate apart. **Development**: written by Claude, may be used for tuning in its plan. **Regression**: run every round to catch a step backwards, every miss explained; its results have been read, so it no longer measures unseen requests. **Held-out**: written by the user, never used for tuning, run once its plan's code is final. A set's role changes only by a plan's decision, recorded in the last column.

| File | Library | Scenarios | Author | What it tests | Role | Role history |
|---|---|---|---|---|---|---|
| `scenarios_baseline.yaml` | `library.yaml` | 7 | Claude | Plan 1's first planning slice: fridge-only and shopping meals, counts, infeasible requests | Regression | Development in plan 1 and plan 2a (rounds 1-5 tuned on it); regression since then |
| `scenarios_plan2a.yaml` | `library_extended.yaml` | 19 (18 scored, 1 check-only) | Claude | Plan 2a: feasibility stops, the shortlist, named ingredients, next actions | Regression | Development in plan 2a; regression since then |
| `scenarios_counts.yaml` | `library_extended.yaml` | 10 | Claude | Meat and vegetable bounds, the implied side, the next-action buttons' messages | Regression | Added in round 6, never tuned on |
| `scenarios_diners.yaml` | `library_diners.yaml` | 17 | Claude | Plan 2b: the diner card, friends and guests, nicknames, ask-first answers, plan again | Regression | Development in plan 2b; regression since plan 3b-1 |
| `scenarios_heldout.yaml` | `library_diners.yaml` | 14 | User (requests), Claude (set-up) | Plan 2b: requests with several diners | Regression | Held-out in plan 2b (round 8); its results were read, so a regression set since plan 3b-1 (round 9) |
| `scenarios_heldout_core.yaml` | `library_diners.yaml` | 22 | User (requests), Claude (set-up) | Plan 2b: one-diner requests | Regression | Held-out in plan 2b (round 8); a regression set since plan 3b-1 (round 9) |
| `scenarios_revise.yaml` | `library_revise.yaml` | 42 (rewritten in plan 3b-2; 37 in plan 3b-1b) | Claude | Plan 3b-1: follow-ups that revise the plan, the choice card, hand edits between turns. Plan 3b-1b: drops, replacements, add-only, one-meal avoidances, a lasting rule, a staple quote, a dish not in the library, the empty-plan question, buttons after a failed revision and after an early stop, an unmeetable wish. Plan 3b-2: rewritten to two layers of expectations, 42 scenarios | Development | Development in plan 3b-1; development again in plan 3b-1b and plan 3b-2 |
| `scenarios_revise_heldout.yaml` | `library_revise.yaml` | 18 (1 deferred to 3b-2) | User (sentences), Claude (set-up) | Plan 3b-1: revision sentences | Regression | Held-out in plan 3b-1 (round 9); a regression set since plan 3b-1b (decided 2026-11-21). Plan 3b-2 Task 1 (user-approved, 2026-11-22): one expectation added (`expect_plan: false` on the dish-not-in-the-library scenario), and the deferred scenario now points to plan 3b-2 plan two. Plan 3b-2 Task 12 (user-approved, 2026-11-24): two scenarios accept a second outcome (see round 11) |
| `scenarios_revise_heldout2.yaml` | `library_revise2.yaml` | 20 | User (sentences), Claude (set-up) | Plan 3b-1b: drops, replacements, one-meal wishes, a lasting rule, add-only, buttons, unmeetable wishes, new plans | Regression | Written for plan 3b-1b (2026-11-21); held-out in round 10 (2026-11-22); a regression set since plan 3b-2. Plan 3b-2 Task 1 (user-approved, 2026-11-22): the first turn's diner card is expected in the English no-rice scenario (only the follow-up's card is checked), `expect_plan: false` on the all-allergic seafood scenario, and the named-dish swap is no longer deferred. Plan 3b-2 Task 12 (user-approved, 2026-11-24): two scenarios accept a second outcome (see round 11) |
| `scenarios_revise_heldout3.yaml` | `library_revise3.yaml` | 16 (1 deferred to plan 3b-2 plan two) | User (sentences), Claude (set-up) | Plan 3b-2: replacements and removals, a dish not in the library, adding when nothing fits, wishes, an unmeetable wish, typed next steps after a failed plan, a person joining, a message not about the plan, a reference that matches no dish, plan again | Held-out | Written for plan 3b-2 (2026-11-23); run once in round 11 |

`library_revise2.yaml` is `library_revise.yaml` unchanged plus eight dishes the plan 3b-1b sentences need; `library_revise.yaml` stays frozen so the regression set compares across rounds. `library_revise3.yaml` is `library_revise2.yaml` unchanged plus five dishes the plan 3b-2 sentences need.

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
| 9 | 20261120T102629Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0039 | $0.0211 | 0.93 | 5984 | 4.0 s | 6.2 s |
| 10 | 20261122T073916Z | no | 7/7 | 6/6 | 3.00 | 3 | $0.0045 | $0.0238 | 0.92 | 6707 | 3.4 s | 4.3 s |
| **11** | 20261123T171223Z | no | 7/7 | 6/6 | 2.86 | 3 | $0.0033 | $0.0234 | 0.95 | 6707 | 3.2 s | 4.9 s |

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
| 9 | 20261120T102702Z | no | 18/18 | 12/12 | 3.08 | 4 | $0.0095 | $0.0524 | 0.94 | 5981 | 3.7 s | 5.6 s | — |
| 10 | 20261122T073945Z | no | 18/18 | 12/12 | 3.17 | 4 | $0.0106 | $0.0602 | 0.93 | 6704 | 3.1 s | 5.6 s | — |
| **11** | 20261123T171251Z | no | 18/18 | 12/12 | 2.72 | 4 | $0.0085 | $0.0587 | 0.95 | 6704 | 3.0 s | 5.2 s | — |

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
| 9 (button fix) | 20261121T073929Z | no | 10/10 | 7/8 | 3.38 | 4 | $0.0074 | $0.0354 | 0.91 | 6001 | 4.0 s | 6.3 s | — (three_dishes_at_most_one_vegetable_shopping passed on the second attempt; each button scenario 4 calls over two turns) |
| 10 | 20261122T074050Z | no | 10/10 | 8/8 | 3.00 | 3 | $0.0062 | $0.0339 | 0.92 | 6705 | 3.3 s | 4.9 s | — (each button scenario 3 calls over two turns: since plan 3b-1b a button changes the request in code, with no parse call) |
| **11** | 20261123T171353Z | no | 10/10 | 8/8 | 2.90 | 4 | $0.0059 | $0.0355 | 0.95 | 6705 | 3.5 s | 5.1 s | — |

## Comparison: diners development set (17 scenarios, all expect a plan)

`scenarios_diners.yaml` with `library_diners.yaml`, added in plan 2b Task 11 and written by Claude: the diner card, saved friends and guests, nicknames, ask-first answers, plan again after an allergen confirmation. Tuning against it is allowed in plan 2b Task 12 only, with the user's approval. The two-turn scenarios count both turns. `nickname sent` and `label shown` mismatches: none in any run.

| Round | Run | Prompts | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Cache hit | Largest call (tokens) | Model time avg | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 20261118T065901Z | parse_v4 | 16/17 | 16/17 | 3.35 | 8 | $0.0116 | $0.0563 | 0.89 | 5216 | 4.2 s | 12.7 s | guest_dislike_is_a_warning ("只做一个凉拌菜" gave no dish count, so two people defaulted to two dishes and the fridge stopped it early) |
| 8 | 20261118T072512Z | parse_v5 | 16/17 | 17/17 | 3.59 | 9 | $0.0125 | $0.0616 | 0.90 | 5245 | 4.6 s | 12.7 s | plan_again_after_confirmation (the re-plan's parse dropped the previous query, so 香煎鸡腿 was not chosen) |
| 8 | 20261118T074711Z | parse_v5, plan again in code | 17/17 | 17/17 | 3.12 | 4 | $0.0092 | $0.0521 | 0.93 | 5223 | 3.9 s | 5.3 s | — |
| 9 | 20261120T102921Z | route_v3, parse_v6 | 16/17 | 17/17 | 3.59 | 10 | $0.0142 | $0.0699 | 0.90 | 6001 | 5.2 s | 16.1 s | unmatched_people_none (card others 1, expected 2). The 10 calls are `plan_again_after_confirmation` over its turns, as expected |
| 10 | 20261122T074130Z | route_v6, parse_v8 | 17/17 | 17/17 | 3.35 | 6 | $0.0131 | $0.0710 | 0.92 | 6724 | 3.8 s | 8.5 s | — (`unmatched_people_none`, round 9's miss, passed) |
| **11** | 20261123T171434Z | route_v6, parse_v8 | 17/17 | 17/17 | 3.24 | 5 | $0.0103 | $0.0684 | 0.95 | 6724 | 3.4 s | 6.9 s | — |

## Comparison: held-out sets

The requests were written by the user (2026-11-17); Claude added stock, friends, card answers and expectations. Each set runs once per plan, after the development set and the code are final, and nothing is changed in response within that plan. Both use `library_diners.yaml`.

| Round | Set | Run | Scenarios | As expected | First pass | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Largest call (tokens) | Wall p95 | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | `scenarios_heldout.yaml` (diners) | 20261118T075129Z | 14 (all expect a plan) | 13/14 | 12/14 | 3.43 | 7 | $0.0114 | $0.0500 | 7401 | 11.3 s | diners_five_lunch_guest_fish_allergy: 7 calls against 5 (a call-count warning only; the plan was right) |
| 8 | `scenarios_heldout_core.yaml` (one diner) | 20261118T075247Z | 22 (20 expect a plan) | 19/22 | 19/20 | 3.05 | 5 | $0.0128 | $0.0607 | 5221 | 6.1 s | core_one_dessert, core_en_dinner_with_fish, core_en_spicy_shopping (see round 8) |
| 9 | `scenarios_heldout.yaml` (diners) | 20261120T103101Z | 14 (all expect a plan) | 13/14 | 14/14 | 3.14 | 4 | $0.0090 | $0.0492 | 6016 | 6.9 s | diners_friend_cannot_eat_nuts (see round 9) |
| 9 | `scenarios_heldout_core.yaml` (one diner) | 20261120T103213Z | 22 (20 expect a plan) | 20/22 | 19/19 | 3.35 | 7 | $0.0142 | $0.0773 | 6015 | 6.6 s | core_en_dinner_with_fish, core_en_spicy_shopping (see round 9). First pass is over the 19 new plans; `core_two_meat_one_vegetable_then_one_dish` was judged on a revision turn, which passed |
| 10 | `scenarios_heldout.yaml` (diners) | 20261122T074247Z | 14 (all expect a plan) | 13/14 | 14/14 | 3.07 | 4 | $0.0098 | $0.0530 | 6739 | 4.9 s | diners_five_with_vegetarian (see round 10; round 9's miss passed) |
| 10 | `scenarios_heldout_core.yaml` (one diner) | 20261122T074350Z | 22 (20 expect a plan) | 20/22 | 19/19 | 3.20 | 5 | $0.0152 | $0.0834 | 6738 | 6.3 s | core_en_dinner_with_fish, core_en_spicy_shopping (see round 10). First pass is over the 19 new plans |
| **11** | `scenarios_heldout.yaml` (diners) | 20261123T171544Z | 14 (all expect a plan) | 12/14 | 13/14 | 3.14 | 4 | $0.0078 | $0.0546 | 6739 | 5.3 s | diners_three_people_one_no_lamb, diners_five_with_vegetarian (see round 11) |
| **11** | `scenarios_heldout_core.yaml` (one diner) | 20261123T171644Z | 22 (20 expect a plan) | 21/22 | 20/20 | 3.05 | 5 | $0.0119 | $0.0807 | 6738 | 5.0 s | core_en_spicy_shopping (see round 11) |

## Comparison: revise sets (plans 3b-1 and 3b-1b)

`scenarios_revise.yaml` (development, 19 scenarios, 17 expect a plan) and `scenarios_revise_heldout.yaml` (held-out, 18 scenarios, all expect a plan, 1 deferred to plan 3b-2), both with `library_revise.yaml`. Each scenario is a first message (or a fixed first plan), optional hand edits between the turns, and a follow-up; the follow-up is the judged turn. The development scenarios were built by Claude from the user's examples and tuning against them was allowed in plan 3b-1 Task 8 (three runs at most); the held-out requests were written by the user and run once.

From round 10 (plan 3b-1b) the development set has 37 scenarios (33 expect a plan), `scenarios_revise_heldout.yaml` is a regression set (run every round, every miss explained), and the new held-out set is `scenarios_revise_heldout2.yaml` (20 scenarios, all expect a plan, 1 deferred) with `library_revise2.yaml`. Round 10's development rows are the four development runs of plan 3b-1b Task 10; each fixed the misses of the run before.

Columns: "Revisions passed" counts judged turns that ran the revise step and ended with a passed plan (the two development scenarios that expect the revision to stop count as not passed). "Calls / revise turn" is the average and the most model calls in one judged revise turn (that turn's calls only: route, parse, revise attempts and searches). Follow-up route and first-message route are route accuracy. Avg and max calls are per scenario over both turns. Cost and largest call are over all scenarios; wall p95 too. "Replies" (from round 10) are the runner's reply checks: "0 dishes" shown as the reason for a missing answer (target 0), an expected unmet wish with no code-written sign (target 0), and scenarios with a next-action button as expected; with a button, the calls of the failed revision before it count as a revise turn too.

| Round | Set | Run | Prompts | As expected | Revisions passed | Locked removed | Calls / revise turn | Follow-up route | First-message route | Avg calls | Max calls | Cost (cache-aware) | Cost (uncached) | Largest call (tokens) | Wall p95 | Replies (0 dishes / wish not said / buttons) | Not as expected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 9 (dev 1) | development | 20261120T081202Z | route_v3, parse_v5, revise_v1 | 17/19 | 9/13 | 0 | 3.7 / 5 | 16/17 | 2/2 | 6.42 | 8 | $0.0260 | $0.1305 | 5352 | 12.8 s | — | add_one_more_after_hand_remove_and_add, choice_card_answered_revise |
| 9 (dev 2) | development | 20261120T091620Z | route_v3, parse_v6, revise_v1 | 16/19 | 9/12 | 0 | 3.6 / 5 | 16/17 | 2/2 | 5.95 | 8 | $0.0262 | $0.1283 | 6076 | 12.8 s | — | general_spicier_keeps_hand_added, fridge_only_wish_unmet, choice_card_answered_revise |
| 9 (dev 3) | development | 20261120T101036Z | route_v3, parse_v6, revise_v2 | 18/19 | 11/13 | 0 | 3.5 / 6 | 17/17 | 2/2 | 6.00 | 9 | $0.0250 | $0.1323 | 6082 | 12.4 s | — | general_spicier |
| 9 | development | 20261120T102047Z | route_v3, parse_v6, revise_v2 | 16/19 | 11/12 | 0 | 3.2 / 4 | 16/17 | 2/2 | 5.74 | 7 | $0.0246 | $0.1252 | 6077 | 11.7 s | — | general_spicier, general_spicier_keeps_hand_added, fridge_only_wish_unmet |
| 9 | held-out | 20261120T102324Z | route_v3, parse_v6, revise_v2 | 9/17, deferred 0/1 | 11/14 | 0 | 3.9 / 8 | 15/15 | — | 6.89 | 11 | $0.0279 | $0.1486 | 6083 | 17.8 s | — | eight scenarios (see round 9) |
| 10 (dev 1) | development | 20261122T050046Z | route_v3, parse_v7, planner_v5, revise_v3 | 29/37 | 22/25 | 0 | 3.8 / 15 | 25/26 | 3/3 | 6.41 | 19 | $0.0574 | $0.3206 | 6631 | 17.9 s | 0 / 1 / 4/7 | eight scenarios (see round 10) |
| 10 (dev 2) | development | 20261122T055623Z | route_v4, parse_v7, planner_v5, revise_v4 | 30/37 | 21/26 | **1** | 3.3 / 7 | 24/26 | 3/3 | 6.08 | 10 | $0.0521 | $0.3037 | 6905 | 14.1 s | 0 / 0 / 5/7 | seven scenarios (see round 10) |
| 10 (dev 3) | development | 20261122T063117Z | route_v5, parse_v8, planner_v5, revise_v4 | 35/37 | 21/24 | 0 | 3.4 / 7 | 25/26 | 3/3 | 6.27 | 12 | $0.0478 | $0.3189 | 6815 | 13.9 s | 0 / 0 / 7/7 | replace_eggplant_same_type, drop_every_dish_asks |
| 10 (dev 4) | development | 20261122T071211Z | route_v6, parse_v8, planner_v5, revise_v4 | 35/37 | 22/26 | 0 | 3.6 / 8 | 26/26 | 3/3 | 6.32 | 12 | $0.0471 | $0.3273 | 6869 | 15.6 s | 0 / 0 / 6/7 | meal_avoid_meat_zh, button_relax_split_after_failed_add |
| 10 | development | 20261122T072758Z | route_v6, parse_v8, planner_v5, revise_v4 | 33/37 | 20/26 | 0 | 3.4 / 6 | 26/26 | 3/3 | 6.27 | 11 | $0.0505 | $0.3215 | 6790 | 13.7 s | 0 / 0 / 5/7 | add_only_one_vegetable, meal_avoid_meat_zh, button_fewer_after_failed_add, button_relax_split_after_failed_add |
| 10 | held-out (`scenarios_revise_heldout2.yaml`) | 20261122T073303Z | route_v6, parse_v8, planner_v5, revise_v4 | 14/19, deferred 0/1 | 13/16 | 0 | 4.6 / 12 | 16/16 | — | 7.50 | 15 | $0.0329 | $0.2245 | 6900 | 20.3 s | 0 / 1 / 1/2 | five scenarios (see round 10) |
| 10 | regression (`scenarios_revise_heldout.yaml`) | 20261122T073630Z | route_v6, parse_v8, planner_v5, revise_v4 | 11/17, deferred 0/1 | 12/15 | 0 | 4.2 / 12 | 14/15 | — | 7.00 | 15 | $0.0273 | $0.1823 | 6828 | 18.9 s | 0 / 0 / — | six scenarios (see round 10) |

From round 11 (plan 3b-2) the revise sets run on a different structure (one reader, a frame in code, a picker) with different measures (must-never-be-wrong, unnecessary questions, calls per change turn), so their rows are in round 11's own table, under "Rounds".

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

### Round 9 — plan 3b-1: revising the plan (route_v3, parse_v6, revise_v2), revise sets (2026-11-20/21) — results of record until round 10

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

### Round 10 — plan 3b-1b: revision fixes (route_v6, parse_v8, planner_v5, revise_v4), a new held-out batch (2026-11-22) — results of record until round 11

- **Changed since round 9:** plan 3b-1b (design `2026-11-21-phase-1-plan-3b1b-revise-fixes-design.md`). Each removed dish is a drop or a replacement: a bare named removal ("玉米的不要") drops the dish and the plan gets smaller, and only "换 / 换成 / 换一个" replaces it; a named replacement dish carries its own quote (`replace_with`); an add-only kind changes no other dish; a replacement takes the type of the dish it replaces (R7) unless the user names the replacement, a one-meal avoidance covers the replaced dish, or the message states a count, and the H4 retry names the dishes of the right type; staple foods have category words; conflict words ("不适合的菜") name the dishes with a kept conflict; a one-meal avoidance (`meal_avoid`, `parse_v7`) is this meal's hard rule and brings no diner card; a nothing-fits answer ends the turn without a retry when the failures are only count ones; a change that would leave no dish asks whether to plan a new meal; next-action buttons change the request in code (no route or parse call), and on a failed revision's card revise the kept plan again; the quiet count note reads the card's own request; `planner_v5` says when a taste wish is not met, and an attempt with no answer falls back to the shortlist. The evaluation runner gained the reply checks ("0 dishes", unmet wish not said, after a button), file roles with each role's rate apart, and keys for buttons, the follow-up diner card, the kept request and the expected unmet wish (Task 9). A Task 9 ruling, recorded here: `expect_wish_unmet` reads only code-written signs, so a revision that passes with a partial change and says so only in the model's note counts as not said, and any failed or nothing-fits revision that kept the plan with an unmet entry counts as said.
- **Scenario sets:** `scenarios_revise.yaml` grew to 37 (18 added by Claude with new sentences); `scenarios_revise_heldout.yaml` became a regression set; `scenarios_revise_heldout2.yaml` (20 sentences from the user, set up by Claude in Task 1, one scenario deferred by the user's decision) is the new held-out set, with `library_revise2.yaml`. See "Scenario sets".
- **Thresholds, set in the design (section 6) before the round:** development set as expected at least 90%; new held-out set at least 80% (deferred scenarios apart); the 3b-1 held-out set, now regression, at least 80% with every miss explained; locked dishes wrongly removed 0 over every run; model calls per revise turn at most 4 on average and no turn over 7; no regression on the existing new-plan sets; "0 dishes" shown for a missing answer and an unmet taste wish not said, 0 in every scenario; after a next-action button, earlier conditions kept and no question asked again, every such scenario as expected. If the round misses a threshold, it is recorded as it is and the user decides.
- **Development runs (plan Task 10; three allowed, a fourth approved by the user):**
  - Run 1 `20261122T050046Z` at `8d45f60` (`route_v3+parse_v7+planner_v5+revise_v3`): 29/37, 3.8 calls per revise turn, 15 in one turn. Misses: `lasting_rule_for_a_friend_while_revising` (the card's stored request, with the friend rule's "Diner 2" label, was sent to the client); `button_no_time_limit_after_named_swap` (the time limit hid a named library dish, so the reply said it was not in the library and offered an import); `button_relax_split_after_failed_add` (an empty nothing-fits add was retried twice: 15 calls in the turn); `replacement_not_in_library` (the reviser dropped the dish on "换成" a dish not in the library instead of answering that nothing fits); `fridge_only_wish_unmet` (the route answered `unsure` for "想吃点海鲜", 3 of 5 runs since plan 3b-1); `meal_avoid_meat_zh`, `meal_avoid_pork_en` and `button_no_time_limit_after_early_stop` (set-up mistakes in the development set, corrected). Fixes at `cc02c08`: the client gets the card without its stored request; a nothing-fits add is not retried; search returns the dishes over the time limit apart, so a nothing-fits reply names a dish over the limit and offers to drop the limit (user decision; design 5.2 first); `route_v4` (a taste or ingredient wish right after a plan is `revise`); `revise_v4`.
  - Run 2 `20261122T055623Z` at `cc02c08` (`route_v4+parse_v7+planner_v5+revise_v4`): 30/37, **1 locked dish removed**, 3.3 / 7 calls. Misses: `fewer_eggs` and `fewer_eggs_keeps_locked_egg_dish` (`parse_v7` put 鸡蛋 in `meal_avoid` for "不要这么多鸡蛋的", and an avoidance may replace a locked dish by design: the locked removal); `choice_card_answered_plan`, `choice_card_answered_revise` and `button_shopping_after_seafood_wish` (`route_v4` over-reached to `revise`); `lasting_rule_for_a_friend_while_revising` (the route saw the planner's original first reply, a runner artifact, plus a route gap); `button_fewer_after_failed_add` (the offering turn's words, added to the button turn by a run 1 fix, contradicted the button, so the reviser asked a question). Fixes at `3d55a5f`: `parse_v8` (words about an amount stay a general wish); `route_v5` (a message that only asks to reconsider, naming no taste, ingredient, dish or count, stays `unsure`; user decision); the button turn labels the earlier wish; the runner's first reply names the scenario's dishes.
  - Run 3 `20261122T063117Z` at `3d55a5f` (`route_v5+parse_v8+planner_v5+revise_v4`): 35/37 (95%), 3.4 / 7 calls, buttons 7/7. Misses: `replace_eggplant_same_type` (a revise parse with no headcount was read as "only me", so the diner card turned the revision into a new fridge-only plan and the shopping mode was lost; a code path from plans 2b and 3b-1); `drop_every_dish_asks` (`route_v5`'s new rule sent "全都不要了" to `unsure`). Fixes at `1065f44` (user decisions): a revise turn fills the parse's unstated fields from the plan's request before the diner-card check and carries the earlier conditions into a fallback new plan; `route_v6` (words that point at dishes in the plan, with a remove or swap verb, name dishes, so `revise`).
  - Run 4 `20261122T071211Z` at `1065f44` (`route_v6+parse_v8+planner_v5+revise_v4`): 35/37, follow-up route 26/26, 3.6 / 8 calls. Misses: `meal_avoid_meat_zh` ("肉" covers only meat-category ingredients, so fish and shrimp dishes, dish type meat, replaced the meat dishes under the exact meat count); `button_relax_split_after_failed_add` (`parse_v8` read "能不能多一道有肉的？" as a taste wish with no meat count, so an added vegetable dish passed and no `relax_split` button was offered). The 8-call turn: `button_shopping_after_seafood_wish`, where the reviser searched five rounds although every shortlist type was complete. The user chose to stop fixing; the leftovers are in the carryover (committed at `d14eaf5`).
- **Round at `d14eaf5`** (`route_v6+parse_v8+planner_v5+revise_v4`), every set once without vectors on the test database (2026-11-22, 07:27-07:44 UTC): revise development 33/37 (`072758Z`), new held-out 14/19 plus the deferred scenario 0/1 (`073303Z`), regression (the 3b-1 held-out set) 11/17 plus the deferred scenario 0/1 (`073630Z`), baseline 7/7 (`073916Z`), plan 2a 18/18 (`073945Z`), counts 10/10 (`074050Z`), diners 17/17 (`074130Z`), held-out diners 13/14 (`074247Z`), held-out one diner 20/22 (`074350Z`).
- **Against the thresholds — the round does not meet the design's thresholds:**

  | Threshold | Required | Result | Verdict |
  |---|---|---|---|
  | Development set as expected | ≥ 90% | 33/37 (89%), missed by one scenario; runs 3 and 4 had 35/37 (95%) | **Missed** |
  | New held-out set as expected | ≥ 80%, deferred apart | 14/19 (74%), deferred 0/1; two misses are set-up mistakes, 16/19 (84%) if corrected | **Missed** |
  | 3b-1 held-out set (now regression) | ≥ 80%, every miss explained | 11/17 (65%; round 9: 9/17), every miss explained; 12/17 (71%) with its set-up mistake corrected | **Missed** (rate); misses explained |
  | Locked dishes wrongly removed | 0 over every run | 0 in every run of the round; development run 2 had 1 (a parse cause, fixed by `parse_v8`) | **Met** in the round |
  | Model calls per revise turn | average ≤ 4, no turn over 7 | average 3.4 (development), 4.6 (held-out), 4.2 (regression); most 12 (two held-out turns, one regression turn) | **Missed** |
  | Existing new-plan sets | no regression | baseline 7/7, plan 2a 18/18, counts 10/10, diners 17/17, held-out diners 13/14, held-out one diner 20/22: no set fell; the held-out diners miss is a different scenario than round 9's | **Met** by count |
  | "0 dishes" shown; unmet taste wish not said | 0 in every scenario | "0 dishes" 0 in every run; unmet wish not said 1 (held-out `all_four_spicy`) | **Missed** (the wish part) |
  | After a next-action button | every such scenario as expected | development 5/7, held-out 1/2 | **Missed** |

- **Development set, not as expected** (no change after run 4; all four scenarios are new in plan 3b-1b, and each passed in at least one development run, so part of the gap is run-to-run variance):
  - `add_only_one_vegetable` ("再补一个素的" on 青椒肉丝 and 红烧茄子): `parse_v8` read the vegetable count as 1 exact (absolute, not the plan's 1 plus one), so code required 2 meat dishes and the correct add (清炒蘑菇) failed H4 three times. It passed in all four development runs; no parse example covers "one more X" for a side.
  - `meal_avoid_meat_zh`: as in run 4, the reviser replaced the three meat dishes with seafood dishes (dish type meat), now on its first answer. What "肉" covers is undecided (carryover, run 4 leftovers).
  - `button_fewer_after_failed_add`: the button lowered the request from 4 to 3 dishes while the plan shown has 2, so the right move was to add one; the reviser read "少做几道菜" literally and dropped or swapped dishes in all three attempts, and the retry hint said only "Too few: 1 dish". Runs 1, 3 and 4 passed only on the last attempt: a standing near-miss.
  - `button_relax_split_after_failed_add`: identical to run 4 (no meat count for "多一道有肉的", so no failure and no button).
- **New held-out set, not as expected** (recorded as they are; nothing changed in response):
  - `no_rice_today_english` ("I don't want to eat rice today"): the rice dish became a noodle soup and the revision passed, as the user expected. The only mismatch, "a diner card appeared", is the first turn's card ("Dinner for two"): the scenario uses `expect_card: false`, which covers every turn, instead of `expect_follow_up_diner_card: false`. A set-up mistake Claude made when writing the scenarios in Task 1.
  - `add_seafood_all_allergic` ("再加个海鲜吧" with fish, crustacean and shellfish allergies): the reviser answered that no seafood dish can be added because of the allergies, and the plan stayed with the reason, as the user expected. A kept plan is a card that did not pass, and the scenario lacks `expect_plan: false`. A set-up mistake Claude made in Task 1.
  - `add_one_sandwich_english` ("Add one sandwich"): the parse stated counts with staples 0 (and soups, desserts and snacks 0), so adding the sandwich, a staple, broke the count (H4); the retries swapped or added a vegetable dish instead. A `parse_v8` gap: an English add of a non-dish type should state staples 1 (the plan's 0 plus one).
  - `any_mix_button_after_one_more_meat` ("再加一个肉菜", fridge-only, no second meat dish makeable): two causes. The parse set no meat count for "one more meat dish" (as in development run 4), so there was no split failure and no `relax_split` button to press. And the empty nothing-fits answer was retried twice, with five or six searches each time (12 calls in the turn), because the unchanged plan also fails H1 (a stock shortfall in the scenario's fridge), and the no-retry rule applies only when the failures are count ones (H4, R7, R8) alone.
  - `all_four_spicy`: a partial change passed (two dishes replaced by spicy ones after two retries); the wish that all four be spicy was not met, and only the model's note touched on it, without clearly saying so. The runner counts only code-written signs (the Task 9 ruling above), so this is the one "unmet wish not said"; the user's expectation accepts a partial change only when the reply says the rest cannot be made spicy. A runner limitation plus model variance.
  - The deferred scenario `swap_yuxiang_for_a_named_dish` (reported apart by the user's decision): nothing fits, with the wish unmet and an import offered; 鱼香肉丝 stayed.
  - Corrected for the two set-up mistakes, the set would read 16/19 (84%). The file is frozen as a held-out set, so the mistakes are recorded here and not changed; correcting them is the user's decision.
  - Turns over 7 calls: `any_mix_button_after_one_more_meat` (above) and `allow_shopping_button_after_add_one`, whose failed follow-up "再加一道" took 12 calls (an add of a dish already in the plan, R6; then searches up to the call limit with no answer; then an empty answer, R8) before the `switch_to_shopping` button added a dish in one call, as expected.
- **Regression set (the 3b-1 held-out set), not as expected** (11/17 against round 9's 9/17; every miss explained):
  - `friend_joins_and_two_more_dishes` (deferred): a new diner on a revise turn plans from scratch (design 5.1) until plan 3b-2, as in round 9.
  - `remove_what_does_not_suit_after_a_kept_egg_dish` ("还是把不适合的菜去掉吧"): the `route_v5`/`route_v6` rule that a message naming no taste, ingredient, dish or count is `unsure` sent it to the choice card (round 9: `revise`), so the reviser, whose conflict words would have handled it, never ran. A different cause than round 9 (the lock check).
  - `swap_for_a_dish_not_in_the_library` ("鱼香肉丝不要，换成西红柿炒鸡蛋"): the plan stayed unchanged, as expected, but the scenario lacks `expect_plan: false`, and a kept plan does not pass (12/17 with it). The turn took 12 calls: the reviser searched synonyms of the missing dish on every attempt against `revise_v4`'s rule; it answered "replace with nothing" instead of an empty change, which code read as R7 and retried; and the reviser has no call cap of its own (each attempt may use the planner's 6 model and 10 tool calls). Round 9: 8 calls.
  - `swap_for_a_daikon_dish`: the reviser rightly chose 家常烧肉 (白萝卜) with the replacement quote "萝卜", but the quote check accepts only an ingredient name inside the quote, not the quote inside "白萝卜", so R2 rejected all three answers. A regression from plan 3b-1b's new replacement quote; it passed in round 9.
  - `drop_the_meat_dishes` ("最近胖了，把肉菜去了吧"): the later attempts dropped both meat dishes, the expected plan, quoting "肉菜", but "肉菜" is not a category word (only "荤菜" is), so R2 rejected them. The round 9 count cause is fixed.
  - `no_meat_today_english`: the parse is now right (`meal_avoid ["肉"]`, no diner card), but the reviser replaced the meat dishes with 油焖大虾 (seafood, dish type meat) and 鸡蛋饼 under an exact meat count; the same "肉" question as `meal_avoid_meat_zh`.
  - `too_many_tomatoes_but_the_plan_has_potatoes`: no dish has tomato, but the reviser treated the message as a general wish and swapped 土豆烧鸡块 for 辣椒炒肉; `revise_v4` has no rule that a wish touching no dish changes nothing. The round 9 cause (`self_rules`, a new plan) is fixed.
  - What got better: every round 9 cause that plan 3b-1b targeted is fixed in the traces (bare drop, staples, add-only, one-meal wishes instead of the user's rules, conflict words); `drop_the_corn_dish_keep_the_rest`, `noodles_to_rice` and `add_a_green_vegetable` now pass. What got worse: `swap_for_a_daikon_dish`, and revise turns average 4.2 calls with a maximum of 12 (round 9: 3.9 and 8).
- **Other sets, not as expected:**
  - Held-out diners `diners_five_with_vegetarian` ("今天来4个朋友，有一个人吃素，其他人啥都行"): `parse_v8` listed "其他人" as three empty unnamed guests, so the card showed 4 guests and 0 others instead of one vegetarian guest and 3 others; the plan itself was fine. Round 9's miss (`diners_friend_cannot_eat_nuts`) passed, so the rate is the same with a different scenario; the same family as round 9's diners `unmatched_people_none`, which passed this time.
  - Held-out one diner `core_en_dinner_with_fish` and `core_en_spicy_shopping`: the same two scenarios as round 9, the same gap (English words cannot match the Chinese library without vectors). New in both: `planner_v5` chose 油焖大虾 (prawns, not fish) and 葱爆羊肉 (not spicy) and set `wish_met: true`, so the plan claimed the wish was met on dishes that do not meet it. The "unmet wish not said" check cannot see this, since the planner judged the wish met.
- **Notes:** no note named a dish outside its plan (`outside_dish` 0 in every run; round 9 had two).
- **Cost:** the round's nine runs $0.170 cache-aware and $1.054 uncached (summed from the runs' `summary.json`). The approved estimate was $0.16-0.20 cache-aware (at most $0.9): the cache-aware cost is within it. The four development runs added $0.204 and $1.270: plan 3b-1b spent $0.374 cache-aware and $2.324 uncached on evaluation in all.
- **Largest call:** about 6,900 tokens (round 9: about 6,080). Far below the 32,000 budget.
- **Decision:** pending (user). The round is recorded as it is, and nothing was changed in response to the held-out set. The misses not already in `docs/phase-1-carryover.md` are added under "Deferred from Phase 1 plan 3b-1b", and the "Plan 3b-1b" items of plan 3b-1 are marked confirmed or not by this round.
- **Results of record:** this round ran every set on the final prompt versions, so its runs replace round 9's as the results of record.

### Status after round 6

Against the criteria, on the round 6 runs: first pass 6/6, 12/12 and 8/8; average calls 3.0-3.5 per plan scenario; every infeasible scenario stopped within 2 calls with the expected reasons and next actions; uncached cost $0.0146 on the baseline set against $0.064, and model time 3.4-3.5 s against 11.3 s; largest call 3,658 tokens against the 32,000 budget; every scenario of all three sets as expected, with no scenario over 5 calls in this round. The call limits (`MODEL_CALL_LIMIT` 6, `TOOL_CALL_LIMIT` 10 per attempt) were never reached in any round: the most planner calls in one attempt was 5 (round 4).

Known pattern to watch: when the shortlist is complete but the model is unhappy with it (no meat dish to balance the meal, egg dishes as the only "vegetable dishes"), it sometimes ignores the instruction not to search. It costs calls, not correctness.

### Spike: one reader for follow-up turns (2026-11-22) — an experiment, not a round

**Question.** Before the plan 3b-2 design (diner changes, together with simplifying the revise turn), the user asked whether giving the model more of the work would fix the follow-up misses of round 10. A throwaway spike (branch `spike/one-reader`, commit `97c4c50`, not merged; its prompt `one_reader_v0.md` is saved in each run folder's `prompts/`) replaced the route, parse and revise calls of a follow-up turn with **one** model call that read everything at once: the whole conversation (labels for saved nicknames), the current plan with its marks, the confirmed diner card summary, the request's conditions and the revise shortlist, with a search tool capped at 3 calls. It answered one of `plan` (the full new dish list and a note), `question`, `new_meal` or `nothing_fits`. Code kept only the hard checks (diner rules, stock, cookware, time), with one retry; locked dishes and counts were recorded, not enforced. The first turn and buttons stayed as in round 10. Development set only (`scenarios_revise.yaml`, 37 scenarios), test database.

| Run | Reader model | As expected | Calls per follow-up turn (avg / max) | Locked dishes removed | Cost (cache-aware) | Avg latency |
|---|---|---|---|---|---|---|
| 20261122T095944Z | deepseek-flash | 25/37 | 1.4 / 4 | 1 | $0.0342 | 6.0 s |
| 20261122T100355Z | deepseek-flash | 26/37 | 1.4 / 3 | 1 | $0.0350 | 5.6 s |
| 20261122T100754Z | deepseek-v4-pro | 24/37 | 1.6 / 5 | 0 | $0.0917 | 9.8 s |
| 20261122T101428Z | deepseek-v4-pro | 24/37 | 1.6 / 5 | 1 | $0.0634 | 7.1 s |
| Round 10 and development runs 3-4 (today's structure, for comparison) | deepseek-flash | 33-35/37 | 3.4 / 6-8 | 0 | about $0.05 | about 7 s |

Total cost of the spike: $0.224.

**Misses, by kind.**
- *Not built in the spike* (not a reading error): no diner card from follow-up words (`revise_with_new_guest`, `lasting_rule_for_a_friend_while_revising`), and the request changes of button turns were not modelled (three or four `button_*` scenarios).
- *Mapping* (not a reading error): a reader's `question` was counted as route `unsure`, while `which_soup_asks` and `drop_every_dish_asks` expect a revision that asks.
- *Real misreadings*, about 5-6 per run against 2-4 in today's structure: a new meal read as a revision (`new_plan_four_dishes`, `new_plan_after_hand_add`, both models, every run); wrong counts (the stronger model kept 3 dishes where 4 were expected in `named_swap_eggplant_after_hand_add` and `scope_all_but_corn`, and asked instead of trimming in `fewer_dishes_on_hand_grown_plan`); a locked dish removed under a general wish (3 of 4 runs; today's structure blocks this in code); `meal_avoid_meat_zh` in every run; `choice_card_answered_revise` once.

**Conclusion.**
- One reader with the same model did not read follow-ups better: the errors from split information went away, but new ones came (new meal versus revision, counts), and without the code guards locked dishes were removed.
- The stronger model did not help (no better, 2-3 times the cost, slower), so the limit is the structure and the code guards, not model power.
- The clear gain was fewer calls: about 1.5 per follow-up turn instead of 3.4, and faster turns.
- This supports, for the plan 3b-2 design: one model reads the whole context once and answers **what the message wants** in a structured form; code decides which dishes and people that touches and keeps the guards (locked dishes, counts, rules); a model chooses concrete dishes only within that narrow frame.

### Plan 3b-2 (one) development runs (2026-11-23) — not a round

The new follow-up structure (reader `follow_up_v1`, frame in code, picker `pick_v1`; Tasks 2-10b) on the rewritten development set (`scenarios_revise.yaml`, 42 scenarios, `library_revise.yaml`), test database, deepseek-flash, **with embeddings** (`--with-embeddings`; round 10 ran without, so its search fell back to text matching while the tool said "matched by meaning" — see the carryover on `semantic_paused`). Results of record come from Task 12's round.

**The five runs side by side** (the tables after it give each run with its misses):

| | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 | Target |
|---|---|---|---|---|---|---|
| Run folder | 20261123T112836Z | 20261123T125329Z | 20261123T135719Z | 20261123T153538Z | 20261123T164312Z | |
| Commit | `e770385` | `af1a940` | `e807d2c` | `294198a` | `896d17c` | |
| Reader / picker prompt | follow_up_v1 / pick_v1 | follow_up_v2 / pick_v1 | follow_up_v3 / pick_v2 | follow_up_v3 / pick_v3 | follow_up_v4 / pick_v4 | |
| As expected (development set) | 36/42 | 38/42 | 40/42 | 40/42 | **41/42** | ≥ 90% (38) |
| Must-never-be-wrong | 1 | 3 | 0 | 1 (runner false positive) | **0** | 0 |
| Unnecessary questions | 0/30 | 0/30 | 0/30 | 0/30 | 0/30 | ≤ 20% |
| Calls per change turn (avg / max) | 2.2 / 4 | 2.1 / 5 | 2.4 / 5 | 2.2 / 6 | 2.1 / 4 | ≤ 2.5 / ≤ 7 |
| Locked dishes removed | 0 | 0 | 0 | 0 | 0 | 0 |
| After a button, as expected | 4/7 | 5/6 | 5/6 | 6/6 | 6/6 | |
| Picker answers cut by DeepSeek (of picker calls) | 3 empty / 37 | 4 / 43 | 10 / 47 | 3 / 45 | **0 / 41** | |
| Reader asked again | — | — | — | 0 | 0 | |
| Cost (cache-aware) | $0.0493 | $0.0513 | $0.0490 | $0.0484 | $0.0496 | |
| Avg latency | 6.2 s | 6.0 s | 6.7 s | 6.5 s | 6.5 s | |

Run 1's three "empty" picker replies were cut answers recorded before `invalid_tool_calls` was kept; the cut answers are traced in `docs/investigations/2026-11-24-deepseek-picker-tool-call-cut.md`. The reader re-ask exists from run 4 on.

| Run | Prompts | As expected | Must-never-be-wrong | Unnecessary questions | Calls per change turn (avg / max) | Cost (cache-aware) | Avg latency |
|---|---|---|---|---|---|---|---|
| 20261123T112836Z (run 1) | follow_up_v1, pick_v1 | 36/42 | 1 (1 scenario) | 0/30 | 2.2 / 4 | $0.0493 | 6.2 s |
| Round 10, development set (old structure, 37 scenarios, no embeddings, for comparison) | route_v6, parse_v8, revise_v4 | 33-35/37 | — | — | 3.4 / 6-8 | about $0.05 | about 7 s |

**Run 1 misses and what was done** (all fixed before run 2, with the user's approval):
- `button_fewer_after_failed_add`: an add the reader gave with no dish type was dropped by code as an unknown type, so nothing was added and no button offered. Code now reads a missing type as any type. The scenario itself assumed all-or-nothing; under spec 5.5 the dish that can be made is added at once, so it was rewritten (new sentence).
- `fridge_only_spicy_unmet` (the must violation): no spicy dish could be made, and the picker still swapped a dish for a non-spicy one with `wish_met: false`. A slot that exists only for the wish is now left unfilled when the wish is not met (user decision); the reader marks an add or a replacement made only for the wish (`for_wish`, `follow_up_v2`), so the same rule covers it.
- `button_relax_split_after_failed_add`: a stated side count that could not be met offered no `relax_split`; it does now, and the button re-runs the unfinished add as any type.
- `meal_avoid_shrimp_en`, `new_guest_brings_the_card`: the picker returned empty replies (3 of about 40 calls; most likely tool-call arguments that could not be parsed); an empty reply is now asked again at once, within a turn-wide budget of 6 picker calls, so a change turn stays at 7 calls or fewer.
- `button_shopping_after_seafood_wish`: "a wish for something the plan lacks" was read as a wish alone, so nothing changed; `follow_up_v2` turns such a wish into an add or a replacement.
- `new_guest_brings_the_card`: a new diner plus "one more dish" was read as a change; `follow_up_v2` reads any change in who eats as `people`.
- Also: the picker's shortlist no longer lists dishes already in the plan.

| Run | Prompts | As expected | Must-never-be-wrong | Unnecessary questions | Calls per change turn (avg / max) | Cost (cache-aware) | Avg latency |
|---|---|---|---|---|---|---|---|
| 20261123T125329Z (run 2) | follow_up_v2, pick_v1 | 38/42 | 3 (3 scenarios) | 0/30 | 2.1 / 5 | $0.0513 | 6.0 s |

**Run 2 misses and what was done** (fixed before run 3, with the user's approval, 2026-11-24): five of run 1's six misses passed.
- `start_over_with_four` (must: stated count): the plan had 3 meat/vegetable dishes and a soup; the picker read "dishes: exactly 4" as already met, counting the soup. The picker's input now shows each stated count next to the plan's current number and says that dishes exclude soups.
- `button_relax_split_after_failed_add` (must: stated count): on the relax_split button the picker was given the offering turn's words ("荤菜再来一个吧") as the wish, searched only meat dishes and left the any-type slot empty. No button turn passes the earlier words any more; the structured reading kept on the card carries the wish (a legacy card without it still uses the words).
- `too_many_eggs` (must: more dishes changed than allowed): the reader both dropped one egg dish and listed all three as wish targets, against its own reading sentence. `follow_up_v3`: a request is applied once.
- `general_lighter_or_added`: the picker looked only at meat dishes for a lighter dish. `pick_v2`: the plan's balance yields to the wish.
- Also: the reader's input now says what the user changed by hand since the last reply (edits add no message, so the conversation named the plan before them).

| Run | Prompts | As expected | Must-never-be-wrong | Unnecessary questions | Calls per change turn (avg / max) | Cost (cache-aware) | Avg latency |
|---|---|---|---|---|---|---|---|
| 20261123T135719Z (run 3, the last development run Task 11 allows) | follow_up_v3, pick_v2 | 40/42 | 0 | 0/30 | 2.4 / 5 | $0.0490 | 6.7 s |

**Run 3 misses** (left for the user's decision; no change made after run 3):
- `message_after_shopping_button`: all four picker answers came back as tool calls whose arguments were cut off at the same place (`{"choices": [], "slots": [{"index": 0, "recipe_id": …}], "wish_met"`), recorded as invalid tool calls; the model billed about 105 output tokens each, no output limit is set and thinking is disabled, so the cut is on the provider's side or in parsing, not a token limit. The add stayed empty and the reply said no vegetable dish was found, although the picker had chosen one every time.
- `button_shopping_after_seafood_wish`: "这顿想吃点水产的" was again read as a wish with no add, no removal and no wish targets (its own reading sentence said "replace one dish with a seafood dish"), so nothing changed and no button was offered; `follow_up_v3`'s principle that a wish always becomes a change did not hold here (run 1 the same, run 2 passed).

**After run 3** (user-approved, 2026-11-24): the picker's cut answers were traced to DeepSeek itself — a replay of one cut input straight to the HTTP API returned HTTP 200, `finish_reason: "tool_calls"`, 110 completion tokens and arguments already cut after `"wish_met"`; 17 picker calls over the three runs (none from any other model, in any run folder) had it. `pick_v3` sends a schema with no nullable field (`wish_met` "yes" / "no" / "no_wish" after the note; `recipe_id` 0 for nothing fits), and code salvages an answer cut after its slots closed; a replay of run 3's cut inputs with `pick_v3` (15 calls) returned 12 answers, none cut. The reader is asked again once when a change reading with a wish changes nothing (`button_shopping_after_seafood_wish`).

| Run | Prompts | As expected | Must-never-be-wrong | Unnecessary questions | Calls per change turn (avg / max) | Cost (cache-aware) | Avg latency |
|---|---|---|---|---|---|---|---|
| 20261123T153538Z (run 4, an extra run the user approved) | follow_up_v3, pick_v3 | 40/42 | 1 (a false positive of the runner, fixed) | 0/30 | 2.2 / 6 | $0.0484 | 6.5 s |

**Run 4.** Every button scenario passed (6/6). The picker's answer was still cut 3 times of 45 (7%), always after `"wish_met"` though it is no longer nullable; one answer was salvaged and two were set aside and asked again, and all three scenarios passed. The full account of the cut answers, with every occurrence, the raw HTTP evidence and a field-name experiment (`wish_met` 5 cut of 21, `wish_result` 0 of 19), is in `docs/investigations/2026-11-24-deepseek-picker-tool-call-cut.md`. Misses:
- `button_fewer_after_failed_add`: the behaviour was right (one of the two dishes could be made at home and was added; the card offered shopping), but the runner reported "stated counts not met" because an unfilled add with no dish type did not count as touching the stated total. The runner was fixed.
- `meal_avoid_beef`: "这顿别放牛肉" was read as a one-meal avoidance **and** a drop of the beef dish, so code dropped it instead of replacing it and the plan shrank to two dishes; the reply also said no replacement had been found, though none was searched. Left for the user's decision (code must not override the reader's reading; a clearer reader principle in a next version, and an honest reply).

| Run | Prompts | As expected | Must-never-be-wrong | Unnecessary questions | Calls per change turn (avg / max) | Cost (cache-aware) | Avg latency |
|---|---|---|---|---|---|---|---|
| 20261123T164312Z (run 5, an extra run the user approved) | follow_up_v4, pick_v4 | 41/42 | 0 | 0/30 | 2.1 / 4 | $0.0496 | 6.5 s |

**Run 5.** No picker answer was cut (0 of 41 picker calls, `tool_call_cut scan`), no reader was asked again, every button scenario passed (6/6), and `meal_avoid_beef` passed. The one miss, `change_the_soups` ("把汤换一换" on two dishes and two soups): the reader rightly listed both soups for replacement; the picker, choosing any type, filled one soup's slot with a vegetable dish (清炒菠菜) though the message was about soups, so the meal became three dishes and one soup. No count was stated, so code did not check it; it is a picker judgement against `pick_v4`'s "keep the plan's balance unless the user's words point to another kind" (carryover). This is the last development run of Task 11.

### Round 11 — plan 3b-2 (one): one reader, a frame in code, a picker (follow_up_v4, pick_v4) (2026-11-23) — results of record

- **Changed since round 10:** plan 3b-2 (one) (design `2026-11-22-phase-1-plan-3b2-revise-simplification-design.md`). A follow-up after a plan is read once by one reader (`follow_up_v4`). The reader gives:
  - the kind of message (a change, a new meal or something else);
  - the dishes to remove, each to drop or to replace, with the user's quote;
  - the dishes to add and the stated counts;
  - the wish and the dishes it targets;
  - the one-meal avoidances and conditions;
  - one sentence saying how it read the message.

  Code turns the reading into a frame: which dishes stay and which slots need a new dish. Locks, rules, avoidances and the count rules belong to code. One picker call (`pick_v4`, with a search tool) fills the slots. `route_v6` and `revise_v4` no longer run on a follow-up after a plan; a new meal still runs the first-turn path (`route_v6`, `parse_v8`, `planner_v5`). The development set was rewritten to the two layers of expectations (spec 6) and grew to 42 scenarios. The runner checks "must never be wrong" in code on every scenario and counts unnecessary questions apart. Five development runs came before the round (see "Plan 3b-2 (one) development runs" above).
- **Scenario sets:**
  - `scenarios_revise_heldout3.yaml` is the new held-out set, with `library_revise3.yaml`: 16 sentences from the user, set up by Claude, one deferred to plan two.
  - `scenarios_revise_heldout.yaml` and `scenarios_revise_heldout2.yaml` are regression sets. The user approved changes to their expectations in Task 1 and in this task (below).
- **Runs** at `a260411` (`route_v6+parse_v8+planner_v5+follow_up_v4+pick_v4`), test database, deepseek-flash. The four revise sets ran **with embeddings**, as the development runs did. The six older sets ran **without**, as in round 10, so they compare across rounds (user decision, 2026-11-24):

  | Set | Run | Scenarios | As expected | Must-never-be-wrong | Unnecessary questions | Calls per change turn (avg / max) | Locked removed | Follow-up route | Buttons | Wish not said | Cost (cache-aware) | Cost (uncached) | Largest call (tokens) | Wall p95 |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | development (`scenarios_revise.yaml`) | 20261123T165815Z | 42 | 40/42 | 0 | 0/30 | 2.12 / 4 | 0 | 31/31 | 6/6 | 0 | $0.0490 | $0.2590 | 6777 | 11.1 s |
  | held-out (`scenarios_revise_heldout3.yaml`) | 20261123T170353Z | 16 (1 deferred) | 13/15 (rescored 14/15), deferred 1/1 | 1 (a runner false positive; rescored 0) | 0/11 | 2.15 / 4 | 0 | 11/11 | — | 0 | $0.0199 | $0.0978 | 6780 | 11.6 s |
  | regression (`scenarios_revise_heldout.yaml`) | 20261123T170630Z | 18 (1 deferred) | 13/17 (rescored 15/17), deferred 0/1 | 0 | 0/16 | 2.27 / 4 | 0 | 15/15 | — | 0 | $0.0235 | $0.1170 | 6767 | 10.8 s |
  | regression (`scenarios_revise_heldout2.yaml`) | 20261123T170918Z | 20 | 15/20 (rescored 17/20) | 1 (rescored 0) | 0/19 | 2.26 / 4 | 0 | 16/17 | 0/2 | 1 | $0.0260 | $0.1310 | 6772 | 9.8 s |
  | baseline | 20261123T171223Z | 7 | 7/7 | 0 | — | — | — | — | — | 0 | $0.0033 | $0.0234 | 6707 | 4.9 s |
  | plan 2a | 20261123T171251Z | 19 (18 scored) | 18/18 | 0 | — | — | — | — | — | 0 | $0.0085 | $0.0587 | 6704 | 5.2 s |
  | counts | 20261123T171353Z | 10 | 10/10 | 0 | 0/2 | — | — | — | — | 0 | $0.0059 | $0.0355 | 6705 | 5.1 s |
  | diners | 20261123T171434Z | 17 | 17/17 | 0 | 0/1 | — | — | — | — | 0 | $0.0103 | $0.0684 | 6724 | 6.9 s |
  | held-out diners (`scenarios_heldout.yaml`) | 20261123T171544Z | 14 | 12/14 | 0 | — | — | — | — | — | 0 | $0.0078 | $0.0546 | 6739 | 5.3 s |
  | held-out one diner (`scenarios_heldout_core.yaml`) | 20261123T171644Z | 22 | 21/22 | 0 | 0/1 | — | — | — | — | 0 | $0.0119 | $0.0807 | 6738 | 5.0 s |

  "Rescored" means the same run, read again by hand after the changes below; the runs were not repeated. The changes: the runner's false positive was fixed, and four regression expectations were widened. The user approved the wider expectations after reading the model's outcomes. Both numbers are kept; the first is what the runner reported at `a260411`.
- **Changes after reading the round** (no product code or prompt changed):
  - **Runner fix.** The check "said not in the library, but a library dish" now leaves out the dishes a scenario hides (`library_exclude`). In `named_dish_not_in_library_fridge` (held-out) the named dish is hidden for that run, so saying it is not in the library was right.
  - **Four regression expectations widened** (user-approved 2026-11-24: "the four outcomes are acceptable"). Each file has a comment at the scenario.
    - `lighter_please` (heldout2): replacing both dishes that are not light, 辣椒炒鸡蛋 and 酱爆鸡丁, is also accepted.
    - `another_version_same_counts` (heldout2): a revision that replaces every dish and keeps the counts is accepted, as well as a new plan.
    - `remove_what_does_not_suit_after_a_kept_egg_dish` (heldout): replacing the egg dish with a dish that suits, keeping the other three, is accepted, as well as dropping it.
    - `wants_mushroom` (heldout): adding the mushroom dish and keeping the plan is accepted, as well as replacing one dish with it.
- **Against the thresholds (spec 6).** On the rescored numbers every threshold is met. On the runner's numbers at `a260411`, three are missed: "must never be wrong" and both regression sets.

  | Threshold | Required | Result | Verdict |
  |---|---|---|---|
  | Must never be wrong | 0 over every run | 2 reported, 0 rescored. One was a runner false positive (fixed). The other was `lighter_please`: a dish was removed that the scenario did not allow, and that expectation was widened with the user's approval | **Met** rescored (missed as reported) |
  | Development set as expected | ≥ 90% | 40/42 (95%) | **Met** |
  | New held-out set as expected | ≥ 80% | 13/15 (87%) reported, 14/15 (93%) rescored; the deferred scenario passed | **Met** |
  | 3b-1 set (regression) | ≥ 80%, every miss explained | 13/17 (76%) reported, 15/17 (88%) rescored; every miss explained below | **Met** rescored (missed as reported) |
  | 3b-1b set (regression) | ≥ 80%, every miss explained | 15/20 (75%) reported, 17/20 (85%) rescored; every miss explained below | **Met** rescored (missed as reported) |
  | Model calls per revise turn | average ≤ 2.5, no turn over 7 | average 2.12-2.27 in the four revise sets; at most 4 in one turn (round 10: 3.4-4.6 and 12) | **Met** |
  | Unnecessary questions | ≤ 20% | 0 in every set | **Met** |
  | Older new-plan sets | no regression | baseline 7/7, plan 2a 18/18, counts 10/10, diners 17/17, held-out one diner 21/22 (round 10: 20/22), held-out diners 12/14 (round 10: 13/14; the new miss is a first-turn reading, see below) | **Met** (the held-out diners miss is first-turn variance; this plan did not change the first turn) |
  | Prompt size | `follow_up_v4` + `pick_v4` shorter than `route_v6` + `revise_v4` | 9,767 characters against 12,817 | **Met** |

- **Development set, not as expected:**
  - `general_lighter_or_added`: the reader listed the three heavy dishes. The picker chose 清蒸鲈鱼 and 清炒菠菜 for two of them, and its own note said the meal was no longer heavy. Yet it answered `wish_result: "no"`, most likely because the third target (the soup) was not replaced. Code then left both slots unfilled, since a slot made only for the wish stays empty when the wish is not met, and the plan stayed as it was. A picker judgement.
  - `meal_avoid_covers_hand_added`: the reader rightly replaced both chicken dishes, quoting "鸡肉". The picker sees a removal's quote as "the user's words", which reads to it as what the user wants. So it searched "鸡肉", chose 香煎鸡腿, and refused to replace the locked chicken dish. A picker-input cause.
- **New held-out set, not as expected** (recorded, not fixed; the sentences are not repeated here):
  - `named_dish_not_in_library_fridge`: the runner's false positive above; the behaviour was right.
  - `any_mix_typed`: the plan could not meet the stated meat/vegetable split, and a typed message then lifted the split. The reader read it as a wish that changes nothing; asked again, it kept the plan. The request kept its split, so the plan did not pass. The typed form of the `relax_split` button has no path, because the reader has no field for lifting a stated split.
  - The deferred scenario passed.
- **Regression set (3b-1), not as expected:**
  - `friend_joins_and_two_more_dishes` (deferred to plan two): 青椒肉丝 was not among the shown titles.
  - `swap_for_a_daikon_dish` ("辣椒炒肉不要，换成萝卜"): the picker searched "萝卜" but excluded the shortlist dishes it had already been shown. 家常烧肉 (with 白萝卜) was one of them. The shortlist lines show no main ingredients, so the picker could not tell, and it answered that the library has no daikon dish. A picker-input cause (the shortlist).
  - `no_meat_today_english`: the change was right. The English reply named Chinese ingredients, which the reply-locale check counts as not English (as in round 10's `core_en_*` scenarios).
  - `remove_what_does_not_suit_after_a_kept_egg_dish` and `wants_mushroom`: passed after the expectation changes above.
- **Regression set (3b-1b), not as expected:**
  - `allow_shopping_button_after_add_one`: after the shopping button, the picker refused the untyped add, so the plan kept 4 dishes instead of 5. A picker judgement.
  - `any_mix_button_after_one_more_meat`: the typed add "再加一个肉菜" had a dish type but no stated count. When no meat dish could be made, code offered no `relax_split` button; the card offered only shopping and import. A code gap: a typed add that cannot be met should offer `relax_split`, as a stated side count does.
  - `all_four_spicy`: only some dishes could be made spicy. Code capped the wish targets without saying so, so the reply did not say the rest could not be changed. This is the one "wish not said". A code gap.
  - `lighter_please` and `another_version_same_counts`: passed after the expectation changes above.
- **Other sets, not as expected:**
  - Held-out diners:
    - `diners_three_people_one_no_lamb`: the first-turn parse made two others and no guest who avoids lamb. It passed in rounds 8-10, and this plan did not change the first turn, so this is `parse_v8` variance.
    - `diners_five_with_vegetarian`: the same miss as in round 10.
  - Held-out one diner `core_en_spicy_shopping`: the same English-reply cause as in round 10. `core_en_dinner_with_fish` passed.
- **Picker answers cut by DeepSeek:** none in the round (`tool_call_cut scan`); `pick_v4`'s field rename holds.
- **Cost:** the round's ten runs cost $0.166 cache-aware and $0.926 uncached (summed from the runs' `summary.json`), within the approved estimate. The five development runs added $0.248 cache-aware.
- **Largest call:** about 6,780 tokens (round 10: about 6,900).
- **Decision:**
  - Taken (user-approved, 2026-11-24): the expectation changes and the runner fix above.
  - Taken (user, 2026-11-24): the picker-input, picker-judgement and code causes are recorded now and fixed in a small plan before plan two (`meal_avoid_covers_hand_added`, `swap_for_a_daikon_dish`, `general_lighter_or_added`, `allow_shopping_button_after_add_one`, `any_mix_button_after_one_more_meat`, `all_four_spicy`, `any_mix_typed`, the held-out one a record only). They are in `docs/phase-1-carryover.md`.
- **Results of record:** this round ran every set on the final prompt versions, so its runs replace round 10's as the results of record.
