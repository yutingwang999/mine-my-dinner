# Phase 1 plan 3b-2 change fixes: design

A small plan between plan 3b-2 (one) and plan two (diner changes). It fixes the causes behind round 11's misses on the change path, takes a few small leftovers of the final review with it, and checks the result in one evaluation round. Parent design: `docs/superpowers/specs/2026-11-22-phase-1-plan-3b2-revise-simplification-design.md` (cited below as "the 3b-2 design"). The misses are listed in `docs/phase-1-carryover.md`, "Misses of round 11".

## 1. Intent

Round 11 met every threshold of the 3b-2 design, but six misses had causes that are cheap to remove: what the picker is shown, two lines of its principles, and two gaps in what code offers or says after a change. Plan two builds on the change path, so these are fixed first.

## 2. Goals, non-goals, anti-goals

Goals:
- The six causes below are removed, and their scenarios pass in round 12.
- Nothing that passed in round 11 starts to fail.

Non-goals (user decision, 2026-11-24):
- Lifting a stated meat/vegetable split by a typed message (held-out `any_mix_typed`). It needs a new reader field and `follow_up_v5`; it goes to plan two.
- Making "apply, check, adjust" one loop that re-checks after every adjustment (final review recommendation). The fallback and the final check in `present_change` already keep the result safe.
- A salvaged answer with an unknown wish result (final review M2). No answer has been cut since `pick_v4`; keep watching.
- English replies naming Chinese ingredients, and first-turn diner readings (→ T1.10).

Anti-goals:
- Code that interprets the user's words. **The principle of the 3b-2 design (5.4 amendment) holds: code checks, it does not interpret.** Every change below either states a fact code already has (where a slot came from, a recipe's main ingredients, that a cap was applied) or lets a model judge. No change makes code decide what words mean, which dish a wish touches, or what a new dish should be.
- A change to what the planner (new plans) sees. The new-plan sets must stay comparable with round 11.
- Editing a prompt version a run has used (`pick_v4`, `follow_up_v4` are frozen).

## 3. Constraints

- Prompt versioning: any picker prompt change is a new file, `pick_v5`; `pick_v4` stays frozen (pinned by hash).
- Held-out sentences never go into prompts, docs or examples.
- Paid runs need the user's approval and a cost estimate first, and use the test database only.
- A change turn keeps its budget: at most 7 model calls.

## 4. Approaches

**Fix 3, the picker answers "no" for a wish it met in part.** Code cannot judge whether a dish is spicy or light, so the judgement stays with the picker.
- **A. Change what "yes" means (`pick_v5`; chosen by the user, 2026-11-24).** `wish_result` "yes" means the new dishes the picker chose meet the wish, even when not every dish the wish touches could change. What was not changed is said by code (fix 6). The answer schema does not change.
- B. A per-slot "meets the wish" field, and the wish-only guard keeps only the slots marked yes. More precise, but a schema change (`PICK_TOOLS_VERSION` changes), and DeepSeek once cut answers right after a field key, so a new field risks bringing that back. Not chosen.

**Fix 2, main ingredients in the shortlist** (Claude's call). Add them to the picker's shortlist only, not to the shared shortlist builder, so the planner's input and the new-plan sets stay as they are.

**Evaluation** (proposed by Claude, not objected to). No new held-out batch: the fixes are small and aimed at named scenarios. `scenarios_revise_heldout3.yaml` was run in round 11 and becomes a regression set, like the two before it.

## 5. Decisions

### 5.1 The removal quote is the reason a dish goes (fix 1)

Now the picker's slot line reads `in place of X; the user's words: "..."`. For a quote that names what the user does not want, the picker searched for it and chose another dish of the same kind (`meal_avoid_covers_hand_added`).

Change: the slot line says the words are why the dish goes, and where the new dish's guidance comes from instead, for example `in place of X, which goes because of the user's words "..."; choose the new dish from the wish line and the meal's rules, not from these words`. Code only labels what the quote is for (the reader's `quote` field is defined as the words that point at the dish). It does not judge what the words say.

### 5.2 Main ingredients in the picker's shortlist (fix 2)

Now a shortlist entry has title, time, servings, missing lines, cookware, expiring and dislikes, but no main ingredients. For a replacement named by an ingredient the picker could not see that a dish uses it and answered that none does (`swap_for_a_daikon_dish`).

Change: each entry of the picker's shortlist gets `main`: the recipe's main ingredients, chosen the same way as on the plan's lines (`dish_facts`: lines marked main, else the first three required lines). It is added after `picker_shortlist` builds the shortlist; `build_shortlist_result` and the planner's shortlist do not change. The shortlist is input, not a tool schema, so `PICK_TOOLS_VERSION` does not change.

### 5.3 `wish_result` judges the chosen dishes (fix 3, approach A)

`pick_v5` changes the principle for `wish_result`: "yes" when the new dishes chosen meet the wish; "no" when the dishes chosen for the wish do not meet it, or nothing could be chosen for it; "no_wish" when there is no wish. It is not about whether every dish the wish touches was changed. The guard `without_unmet_wish_picks` does not change: on "no" it still empties the wish-only slots. Scenario: `general_lighter_or_added`.

### 5.4 An add the user asked for is filled when a dish can be made (fix 4)

Now an add slot of any type reads `Slot 0: added`. After the shopping button, the picker answered 0 because the meal already had one meat and three vegetable dishes (`allow_shopping_button_after_add_one`).

Change:
- Input: an add slot says it is one the user asked for, for example `Slot 0: one more dish the user asked for, of any type` (a typed add names its type). This is a fact code holds: the slot came from the reading's `add` or from a stated count.
- `pick_v5` principle: fill a slot the user asked for whenever a usable dish exists; the meal's balance is not a reason to answer 0.

### 5.5 "Any mix" for a typed add that cannot be met (fix 5)

Now `change_actions` offers `relax_split` for an unfilled meat or vegetable add only when this turn stated that side's count. "One more meat dish" with no stated count gives a typed add, and when no meat dish can be made the card offers only shopping and import (`any_mix_button_after_one_more_meat`).

Change: an unfilled typed add (meat or vegetable dish) offers `relax_split` whether or not a count was stated. This is structural: a typed add left unfilled. Pressed, the button re-runs that add as a dish of any type and keeps the total the turn asked for. The builder confirms that `relax_split_spec` and the button turn handle the no-stated-count case, and makes them do so if they do not.

### 5.6 Capped wish targets are said (fix 6, with final review M4)

Now wish targets beyond the cap (half of the unlocked dishes, rounded up) go to `frame.ignored` with reason `over_limit`, and the reply says nothing about them (`all_four_spicy`).

Change:
- The reply adds one fixed sentence (Chinese and English) saying that N of the dishes were changed this time and the rest stay, and that the user can ask again. It states the cap's effect; it does not judge the wish.
- `Frame` gets typed fields for the wish targets left out, `locked_wish_targets` and `capped_wish_targets`. `present_change` reads them instead of `frame.ignored`, which stays a record (final review M4).

### 5.7 Small leftovers taken along

- **An older thread paused on a question (final review M1).** `read_paused_turn` keeps the parse's `model_call` event (the reader's events no longer overwrite it), and the parse call counts against the turn budget, as `reader_reasks` does, so the turn stays at 7 calls or fewer.
- **The record after the final check (final review item 5).** When the final check in `present_change` removes a rule-breaking dish, the `change_outcome` event's `slots` are rewritten so no slot names a dish that is not in `recipe_ids`.
- **Dead code.** Remove `EditAction` (`agent/edits.py`), unused anywhere.
- **`tool_call_cut.py` reads the model from the LLM config (final review M3)** instead of the hard-coded `API_URL` and `MODEL`.

## 6. Evaluation and success criteria

Development runs (paid, approved first) until the target scenarios pass, then round 12 on: the development set, the three revise sets as regression sets, and the older new-plan sets (baseline, plan 2a, counts, diners, held-out diners, held-out one diner). The planner's input does not change, so the new-plan sets are a no-regression check.

| Criterion | Required |
|---|---|
| Must-never-be-wrong | 0 over every run |
| Target scenarios | `meal_avoid_covers_hand_added`, `swap_for_a_daikon_dish`, `general_lighter_or_added`, `allow_shopping_button_after_add_one`, `any_mix_button_after_one_more_meat`, `all_four_spicy` as expected |
| Development set | ≥ 90% as expected |
| Revise regression sets | no fewer as expected than round 11 rescored (heldout3 14/15, heldout 15/17, heldout2 17/20); every miss explained |
| Calls per change turn | average ≤ 2.5, no turn over 7 |
| Unnecessary questions | ≤ 20% |
| Older new-plan sets | no regression from round 11 |

Round 12 is appended to `docs/planning-evaluation-history.md`; misses not fixed go to the carryover.

## 7. Left to the builder

- The exact wording of the slot lines and the capped-targets sentence, within 5.1, 5.4 and 5.6.
- How `pick_v5` words its two changed principles, keeping its size close to `pick_v4`.
- Where in `pick_attempts` / `present_change` the M1 and record fixes sit.
- Unit and graph tests for each fix; each fix's test is seen failing first.
