# Phase 1 Plan 2a: Planner Efficiency and Candidate Shortlist

Status: draft for approval. Builds on Phase 1 plan 1 (merged) and the Phase 1 agent design (`2026-11-13-phase-1-agent-design.md`), which stays the authority for everything this document does not change. Inputs: `docs/phase-1-carryover.md`, sections "Found while trying the first planning slice" and "Deferred from Phase 1 plan 1".

## 1. Intent

The user's first real planning runs each made about 30 model calls, took a long time, and all failed. The library could not satisfy the request (empty inventory, no wok), yet the agent kept searching and retrying, and its final message talked about the dish count instead of the real cause. The plan 1 evaluation confirmed the pattern: feasible requests passed with 6-10 calls each, while the request without a wok took 31 calls and ended with the wrong explanation.

Plan 2a makes planning cheap, fast, and honest about why a request cannot be met. It also keeps the candidate dishes varied. It is the first half of roadmap step 4 (rest of T1.4); plan 2b covers several diners, nicknames, other diners' stated restrictions, and resolving dishes left out for unknown allergens.

## 2. Goals, non-goals, anti-goals

**Goals**

1. A typical feasible request is planned in about 1-2 planner model calls (plus intent and parsing), without a drop in first-attempt pass rate.
2. A request that cannot be met stops early and says why, with next actions that fit the reason.
3. Candidates offered to the model are varied: every needed category is represented, and one main ingredient does not dominate a category.
4. Free-form requests ("一荤一素", "丰盛点", no count at all) are parsed without invented numbers.
5. The evaluation can show the before/after difference and checks each new behavior.

**Non-goals (later plans)**

- Several diners, nickname replacement, other diners' restrictions, resolving unknown-allergen dishes (plan 2b).
- Ranking by learned preferences or ratings (T1.6 provides the data), avoiding recently cooked dishes across meals (needs saved plans from T1.5), embedding-based diversity such as MMR.
- Multi-turn plan revision and its cost (T1.5), token streaming of replies.
- Resending the whole conversation to `route_intent` and `parse_request` (matters for long conversations; T1.5).
- Explicit cache control (for example, provider-specific cache breakpoints or cached-content objects); 2a relies on the providers' automatic prefix caching and only orders content to benefit from it.

**Anti-goals (a failure even if it technically works)**

- Code silently decides the menu: the model must still choose the dishes and their balance.
- The shortlist hides dishes the model needs: a request the library can satisfy must not fail because the shortlist left the right dishes out (expansion must reach them).
- A hard constraint moves from code into the prompt, or an H2/H5 plan becomes visible.
- An early stop fires when a plan was actually possible.
- The model invents a dish count from a vague phrase, which then fails H4.

## 3. Constraints

All of the Phase 1 design's constraints still hold. In particular:

- Every model call goes through `LLMClient` (directly or through `CallLayerMiddleware`); planning stays essential.
- Hard constraints H1-H6 and the search exclusions are enforced in `minemydinner/planning/`; prompts only describe them.
- `planning_runs` already has `plan_v1` rows, so every planning prompt change goes into a new version `plan_v2`; `plan_v1` files stay unchanged. Changing a tool or the final-answer schema changes `TOOLS_VERSION` (by design).
- Evaluation and real-app checks use only the test database; paid runs need the user's approval first. Tests never call a real model, never assume `llm_calls` or `planning_runs` is empty, and delete only rows they create.
- No new Python packages are expected. If one turns out to be needed, the user installs it.

## 4. Approaches

The three ways considered differ in where the model gets its candidate dishes. All three share the early stop, reason-specific next actions, ranking, context trimming, and evaluation additions.

| Approach | How | Trade-off |
|---|---|---|
| A. Richer tools only | `search_recipes` hits carry missing items, missing cookware, and time, ranked by stock and expiry; the model still searches on its own | Smallest change; about 3-4 planner calls; the model may still search several rounds, so the count varies |
| **B. Code-built shortlist in the first message, tools kept (chosen)** | Before the planner runs, code builds a ranked, category-balanced shortlist with each dish's missing items, cookware, and time, and puts it in the planner's first message. The model usually picks straight from it; it can still search (with "exclude already seen") and read details | About 1-2 planner calls; stable results because code filtered and ranked; the first message is longer but total tokens are lower; fewer tool calls appear in trajectories |
| C. Code composes the plan, model writes the note | Code picks a combination that passes H1-H6 | One call and deterministic, but loses the model's judgment (balance, "something light") and drops the planner ⇄ tools structure of design 7.3 |

The user chose B. To keep tool-heavy trajectories available for comparison, the evaluation gets a switch that runs the planner without the shortlist (Section 5.8).

## 5. Decisions

### 5.1 Request parsing (`plan_v2`)

**One rule for every dish category (decided by the user): what the user states is a hard requirement, and nothing else is assumed unless the user adds it later.**

The real library has meat dishes 67, staple food 25, vegetable dishes 18, other 15, dessert 4, soup 3, snack 2, so staples and desserts need the same treatment as dishes and soups.

`ParsedRequest` gains counts, each set only when the user states it:

- `meat_dishes: int | None`, `vegetable_dishes: int | None` (a split of `dishes`; an unnamed side is not constrained, see the table below).
- `staples: int | None` ("来个炒饭", "要主食" → 1; "就吃碗面" → 1), `desserts: int | None`, `snacks: int | None`.

`dishes` (meat and vegetable dishes) and `soups` stay as today. Every stated count is a hard requirement (Section 5.5). A category the user did not mention is 0: it gets no candidates and no slot in the plan.

**Defaults apply only when the user stated no count for any category** (decided by the user, "option B"). Then dishes and soups come from the headcount table (design 6.2) and are stated as assumed; staples, desserts, and snacks stay 0. As soon as any category has a stated count, the unstated ones are 0, not defaulted:

| The user says | Parsed | Plan contains |
|---|---|---|
| "三个人吃饭" | headcount 3, no counts | 2 dishes and 1 soup, both stated as assumed |
| "三个人，两个菜" | headcount 3, dishes 2 | 2 dishes, no soup |
| "就吃碗面" | staples 1 | 1 staple, nothing else |
| "两菜一汤加个炒饭" | dishes 2, soups 1, staples 1 | exactly that |
| "来个甜品" (only this) | desserts 1 | 1 dessert, nothing else |

This replaces today's per-item defaulting of dishes and soups (`apply_defaults`), which turned "三个人，两个菜" into two dishes plus an assumed soup. Headcount, mode, and time limit keep their defaults. With the "ask first" style, the question about composition is asked only when no category count was stated.

**Category "other"** (drinks, sauces) is never shortlisted and never counted. If the user names such a recipe ("顺便做个酸梅汤"), the model may find it by search and include it; it does not count toward any number. **Cold dishes** are a tag on meat or vegetable dishes, not a category: "来个凉菜" is a query term that ranks cold dishes first within those categories, and a cold dish counts as a meat or vegetable dish.

How the meat and vegetable split combines with the dish total:

| The user says | Parsed | Required of the plan |
|---|---|---|
| "一荤一素" | meat 1, vegetable 1, dishes 2 | exactly 1 meat and 1 vegetable dish |
| "两个荤菜" (no total) | meat 2, dishes 2 | exactly 2 meat dishes, so no vegetable dish |
| "三个菜，有一个荤菜" | dishes 3, meat 1 | exactly 1 meat dish; the other 2 are vegetable dishes |
| "8道菜，包含2个荤菜" | dishes 8, meat 2 | exactly 2 meat and 6 vegetable dishes; soups are not part of "菜", and since a count was stated, there is no soup |
| "三个菜，一荤一素" (named split below the total) | dishes 3, meat 1, vegetable 1 | the total wins; the named counts become minimums (at least 1 meat and 1 vegetable) |
| "两个菜，三个荤菜" (named split above the total) | contradiction | `parse_request` interrupts and asks (design 6.2: genuine contradictions always interrupt) |

`dishes` counts meat and vegetable dishes only, as H4 does today; soups, staples, desserts, and snacks are separate. When no dish total is stated, `dishes` is the sum of the named meat and vegetable counts. All counts together are capped at 12; a larger request interrupts and asks, treating it as a likely slip.

**The card shows what was understood, not only what was assumed.** `current_plan` carries the stated values next to `defaults_used`, and the card renders two lines: "As you asked: 8 dishes (2 meat), fridge only" and, only when something was assumed, "Assumed: 1 person, 2 dishes and 1 soup. Say if that's wrong." A misparse is then visible at a glance and corrected in one sentence.

Parsing rules stated in `parse.md` (v2), with Chinese and English examples:

- A number is filled only when the text maps to one clearly. A range takes its lower end ("两三个菜" → 2), and the card states it as assumed.
- Vague phrases ("丰盛点", "随便", "简单吃点") leave the numbers empty, so defaults apply (design 6.2) when no other count was stated, and the phrase goes into `query` for the planner to read.
- "想喝汤" means one soup (`soups` = 1); "下饭菜" is a query term, not a count.

**Named ingredients (decided with the user).** Today "优先用鸡蛋", "把鸡蛋用掉", and "全做鸡蛋" all become `must_use`, which is only a search filter and is never validated. They mean three different things, so `ParsedRequest` separates them:

| The user says | Field | Shortlist | Validation |
|---|---|---|---|
| "优先用鸡蛋", "鸡蛋多，想用掉些" | `prefer_use: list[str]` | dishes using it rank first; others stay | none (a preference) |
| "要用到鸡蛋", "这顿一定要用鸡蛋" | `must_use: list[str]` | enough dishes using it are kept; others stay | **H7**: at least one dish in the plan uses each named ingredient |
| "全做鸡蛋", "每道都要有蛋" | `all_use: list[str]` | only dishes using it | **H7**: every dish in the plan uses each named ingredient |

A dish "uses" a named ingredient by the same rule search's `must_use` filter applies today: a line linked to that dictionary entry or one of its children ("猪肉" is satisfied by 猪肉末), or, for an unlinked line or a name the dictionary does not know, the folded name contained in the line's name or raw text. Ingredients close to expiry keep ranking first on their own (Section 5.4); `prefer_use` adds the user's own choice.

`apply_defaults` changes as described above (defaults only when no category count was stated). A range taken at its lower end is reported in `defaults_used` like a defaulted value, so the card says it was assumed.

### 5.2 Feasibility check and early stop

New code in `minemydinner/planning/` (no model call) runs after `load_context` and before the planner, and again before each retry. For the request's mode, owned cookware, time limit, and composition, it answers whether the library can satisfy the request at all and, if not, why. Reasons, in the order they are reported:

| Reason | Example | Applies when |
|---|---|---|
| `empty_inventory` | Fridge-only, no stock at all beyond always-in-stock items | mode is fridge-only |
| `no_cookware` | No candidate in a needed category fits the owned cookware | always |
| `not_enough_dishes` | Fridge-only can make only 1 of the 2 dishes asked for ("only 1 dish possible from the fridge") | mode is fridge-only |
| `time_limit` | No candidate in a needed category fits the time limit (counting only firm time sources for the cut, as search does today) | a time limit is set |
| `not_enough_in_library` | A needed category has fewer safe recipes in the library (after H2 and unknown-allergen exclusions) than the request needs, including none at all ("you have 0 meat dishes you can eat; this meal needs 1") | always |
| `named_ingredient` | A `must_use` ingredient is used by no safe recipe in any needed category, or, for `all_use`, a needed category has fewer safe recipes using it than it needs; in fridge-only mode the same counts use makeable recipes, and an ingredient not in stock (and not always in stock) counts as unavailable ("No eggs in the fridge"; "Only 1 vegetable dish with eggs can be made; this meal needs 3") | a named ingredient is required |

How each reason is decided (Claude's calls, from the builder check):

- A recipe is **makeable from stock** when `compute_missing` reports no missing non-optional line for it, counting always-in-stock items as stock and a choice group as satisfied by any one member. This reuses the existing stock code, so the check and H1 agree.
- The check covers the **whole library** for each needed category (the library is small); it does not depend on the shortlist cut.
- `not_enough_in_library` and `not_enough_dishes` compare, per needed category, a count of distinct recipes with the number required (meat and vegetable counts when named, otherwise the counted-dish total across both, and soups). Named counts that the parse marked as minimums are compared as minimums. `not_enough_in_library` counts safe recipes in the library in any mode; `not_enough_dishes` counts recipes makeable from stock, fridge-only only. Shared stock between two dishes is not modelled; that case passes through to the planner and validation.
- The reason text states both numbers per short category: what the meal needs and what is available ("By the current fridge you can make 0 meat dishes; this meal needs 1").
- `no_cookware` and `time_limit` hold when some needed category has no recipe that fits the owned cookware, or the time limit with a firm time source.
- When several reasons hold, all are reported in the listed order, and the next actions are their union.

If any reason holds, the graph skips the planner and retries and goes to `present_plan` with no recipes, `passed=False`, the reason in plain words (localized, fixed text, not model output), and next actions from Section 5.3. The planning record gets a `feasibility` event with the reason and is finished with `final_passed=False`. If the check passes, planning proceeds; a later failure follows the existing retry and no-valid-plan rules.

The check must never stop a request that could be met: when in doubt (for example, a choice group or optional line makes availability unclear), it passes the request through.

### 5.3 Next actions by reason

`next_actions` in `current_plan` is computed from the reasons and unmet rules, never offered blindly:

| Reason or unmet rule | Next actions |
|---|---|
| `empty_inventory`, H1 (missing ingredients), `not_enough_dishes` | `switch_to_shopping`, `fewer_dishes`, `update_inventory` (new: link to the inventory page) |
| `no_cookware`, H3 (missing cookware) | `register_cookware` (new: link to the cookware section of the inventory page), `fewer_dishes` |
| `time_limit`, H6 | `drop_time_limit`, `fewer_dishes` |
| `not_enough_in_library`, H4 alone | `fewer_dishes`, `import_recipes` (new: link to the import page) |
| `named_ingredient`, H7 | `relax_named` (new, sends a fixed short message such as "不一定要用指定的食材" / "The named ingredients are optional"), `fewer_dishes`, `switch_to_shopping` (fridge-only), `update_inventory` (fridge-only), `import_recipes` |
| H2 (every attempt broke an allergy or diet rule; no plan shown) | `fewer_dishes`, `switch_to_shopping` (fridge-only) — added during implementation: both widen the model's safe choices, and a dead end is worse for the user |
| H5 alone | none (retry by sending the request again); the no-plan note then promises no changes |

Two conditional actions are added on top of the table:

- `relax_split` (new, sends a fixed short message such as "荤素都行" / "Any mix of meat and vegetable dishes is fine"): offered whenever the request named a meat or vegetable count and the failing category is meat or vegetable dishes. The next parse drops the split and keeps the total.
- `fewer_dishes` appears only when the request asked for more than one dish or soup.

The existing three actions, `relax_split`, and `relax_named` send a fixed short chat message (after `relax_named`, the next parse moves `must_use` and `all_use` names into `prefer_use`). The three link actions (`update_inventory`, `register_cookware`, `import_recipes`) render as links to the existing inventory page (`/[locale]/inventory`) and import page (`/[locale]/import`); the cookware section gets an anchor id so the link lands on it. The reason text follows the existing pattern: the backend writes it in the request's locale from fixed strings, and `current_plan` also carries the reason codes for tests and the evaluation.

### 5.4 The shortlist

A new function in `minemydinner/planning/` builds the shortlist from the parsed and defaulted request. Its fixed stages, so later plans can add signals without touching the graph:

1. **Fetch per category** safe candidates (H2 exclusions and unknown-allergen exclusions apply exactly as in `search_recipes`), using the request's query, time limit, and `exclude_recent_days` the same way search does. `all_use` filters every category to dishes using each named ingredient. `must_use` does not filter; stage 4 keeps enough dishes using it. `prefer_use` does not filter; stage 3 ranks by it. Search gains a dish-type filter so each category is fetched on its own; a single fetch capped at 20 could miss a category entirely. Missing lines and cookware are computed for every fetched candidate (cheap at this library size). **In fridge-only mode only makeable candidates are fetched** (no missing non-optional line, as `compute_missing` decides); the planner's own `search_recipes` calls in fridge-only mode apply the same filter. A dish that cannot be made from the fridge fails H1, so it is removed rather than ranked low (decided during Task 10 tuning).
2. **Quota by category**: needed categories come from the request: `dishes` ≥ 1 → meat dish and vegetable dish (only the named ones when the split names one side as the whole total, such as "两个荤菜"); `soups` ≥ 1 → soup; `staples` ≥ 1 → staple food; `desserts` ≥ 1 → dessert; `snacks` ≥ 1 → snack. The "other" type is never shortlisted. Each needed category gets `max(SHORTLIST_MIN_PER_CATEGORY, needed + SHORTLIST_MARGIN)` slots, with `SHORTLIST_MIN_PER_CATEGORY` = 5 and `SHORTLIST_MARGIN` = 3 (code constants, tuned after evaluation), where `needed` is the count required of that category (for meat and vegetable without a named split, the counted-dish total). So "8 dishes, 2 meat" gets about 9 vegetable candidates. Other dish types are not shortlisted.
3. **Rank within each category** by, in order of weight: query relevance (as search ranks today), use of `must_use` and `prefer_use` ingredients, fewer missing lines (this matters only in shopping mode; in fridge-only mode every fetched candidate has none, and there is no separate "makeable" ranking key), use of ingredients close to expiry (only where an expiry date was entered), dislikes last (soft rules), and, when a time limit is set, a firm time source before an estimated one.
4. **Spread main ingredients**: within a category, keep at most `SHORTLIST_SAME_MAIN` = 2 recipes whose main ingredients (`role = main`, compared through the dictionary entry, or the parent entry when one exists) are the same set; free slots go to the next recipes with other main ingredients. **Back-fill (decided during Task 10 tuning):** the walk down the ranked list skips recipes over the per-set limit; if slots are still empty after the walk, they are filled from the skipped recipes in rank order until the slots are full or none remain. Slots are a cap: the list is never padded. The two protections below run after the back-fill, in both modes. A recipe with no linked main line is not grouped with any other. Two protections, both decided with the user:
   - **Named ingredients are left out of the comparison.** With "全做鸡蛋", 蒸蛋羹 (eggs only) and 水煮蛋 (eggs only) both have no other main ingredient once eggs are set aside, so they are not grouped; 番茄炒蛋 and 番茄蛋汤 are compared by tomato. The spread then varies what goes with the eggs and never removes what the user asked for.
   - **The spread never leaves too few usable dishes.** If spreading would leave fewer makeable dishes in a category (fridge-only: no missing line; shopping: every safe dish) than that category needs, or fewer dishes using a `must_use` ingredient than one, the highest-ranked recipes it removed are put back, ignoring the per-group limit, until the count is met. Example: only eggs in the fridge, three vegetable dishes asked for, and the makeable ones are 蒸蛋羹, 葱花煎蛋, 水煮蛋, 溏心蛋 (eggs only): only those four are fetched, the spread keeps two, and the back-fill restores the other two, so the model sees all four dishes it can make and nothing it cannot.
5. **Cut** to the quota (a cap; never padded) and attach, per recipe: id, title, dish type, time and its source, servings, missing ingredient names, missing cookware names, ingredients close to expiry it uses, and dislike hits. Results hold diner codes only and no `source_text`, as tool results do today.

The shortlist goes into the planner's first message as compact JSON, before the request summary (data first, the task last; Section 5.7). The planner prompt (`planner.md` v2) says: choose from the shortlist when it fits; search for more only when it does not; check details only when needed; final answer is `PlanProposal` as before.

### 5.5 Validation change

H4 checks every category count in the spec: dishes (meat plus vegetable) and soups as today, and staples, desserts, and snacks exactly as stated (0 when not stated, so an unrequested staple or dessert fails H4 just as an extra soup does today). When `meat_dishes` or `vegetable_dishes` is set, H4 also checks them: exactly that many, or at least that many when the parse marked them as minimums (named split below the total, Section 5.1). Recipes of type "other" are not counted. The S5 warning ("all dishes are the same type") stays for requests without a split.

**H7, named ingredients (new hard constraint, decided with the user).** For each `must_use` ingredient, at least one dish in the plan uses it; for each `all_use` ingredient, every counted dish uses it (recipes of type "other" are ignored). "Uses" follows the rule in Section 5.1. The finding names the ingredient, the rule, and the dishes without it. H7 is not a safety rule, so a plan failing only H7 can be shown as the closest attempt with the unmet rule stated.

**S1 sums across dishes (decided with the user).** Today S1 compares each recipe line with stock on its own, so two dishes that each need 2 of the 3 eggs in stock pass silently. S1 now sums, per ingredient, the amounts of every dish in the plan and compares the total with stock, for the cases that can be summed exactly: amounts in grams against stock in grams, and counts in pieces against stock in pieces (the same cases S1 handles today). Vague, spoon, and level-mode amounts are skipped as before. S1 stays a warning: the plan is not failed and no retry is triggered. The card shows, for example, "Eggs: 番茄炒蛋 2 + 蛋花汤 2, 4 needed, 3 in stock". The summing is a separate function that T1.5's shopping list will reuse (`docs/phase-1-carryover.md`). The shortlist does not show these amounts to the model (Section 6).

**Choosing the closest attempt (decided with the user).** When no attempt passes and the plan shown is the closest attempt (design 6.3; attempts with H2 or H5 are never candidates), attempts are compared by the number of violations, then, on a tie, by the composition gap: the sum over every counted category of |required − present| (for a minimum, only the shortfall counts). With "3 meat, 3 vegetable", an attempt with 2 meat and 3 vegetable dishes is closer than one with 1 meat and 3 vegetable dishes, although each has one H4 violation. A remaining tie goes to the later attempt, as today.

`PlanSpec` gains the new count fields and a flag for minimums; the rule "dishes plus soups at least 1" becomes "all counts together at least 1", and all counts together are at most 12.

### 5.6 Expansion and tools

- `search_recipes` gains `exclude_ids` (up to 50) so the model can ask for "the next batch" without seeing the same dishes again, and a `dish_type` filter (the tool does not expose one today), so the model can ask for more soups specifically. Search hits use the same ranking as the shortlist. These changes alter `TOOLS_VERSION`, which is the intended way to tell old and new records apart; old `planning_runs` rows are not rewritten.
- Search hits carry the same per-recipe facts as the shortlist (missing items, missing cookware, time and source), so a search rarely needs a separate `compute_missing` or `check_cookware` call. Those two tools stay for detail.
- Within one planning run, a repeated identical search query reuses the earlier query embedding instead of calling the embedding model again.

### 5.7 Retries and context

All three providers cache prompt prefixes: when the start of a request matches an earlier request exactly, that part is billed at a much lower cached-input price. History is therefore **never edited after it is written**; anything that rewrites the middle of the conversation would turn every later call into a cache miss. (An earlier draft trimmed old tool results on every call; it was dropped for this reason, decided with the user.)

- **Data first, the task last.** The request goes to the model as system prompt and tool definitions, then the first message: the shortlist, then the request summary. The long shortlist comes before the request so that the counts, mode, and dietary limits are the last thing the model reads before it answers, which helps it follow them (the usual guidance for long-context prompts). Caching does not depend on this order: the first message is built once per run and reused unchanged, so every attempt of a run shares it as a cached prefix whichever part comes first. Across runs little is shared beyond the system prompt and tool definitions, because the shortlist follows the request. (An earlier draft gave "most to least stable" as the reason for this order; corrected with the user.) Within a run nothing before the newest message changes.
- **Retries reuse the same opening.** A retry starts the inner agent with the identical first message and appends the retry message (violations as today, plus a short summary of the previous attempt: shortlist ids already offered, the ids it proposed, missing items per proposed recipe). The shared opening is then a cache hit across attempts. Call limits still apply per attempt.
- **Growth is bounded at the source, not trimmed afterwards.** Each tool result is capped when it is produced: search returns at most 20 hits with compact fields; recipe detail carries no source text; the shortlist uses the same compact fields. With the call limits below, the largest possible context is bounded.
- **A token budget as the last guard.** `PLANNER_INPUT_TOKEN_BUDGET` = 32,000. After each planner call, the provider-reported input tokens are compared with the budget; if a call reached it, the attempt ends before the next call, handled like hitting the call limit (validation, then retry or the no-valid-plan path). Nothing is summarized. The value is about twice the largest planning input observed so far (17,038 tokens in the user's real `plan_v1` runs; median about 6,200) and must stay well below the smallest context window among the planning task's candidates (Section 5.10).
- Call limits are lowered once the evaluation shows the new normal: the target is `MODEL_CALL_LIMIT` = 6 and `TOOL_CALL_LIMIT` = 10 per attempt, confirmed or adjusted by the evaluation run, and stated in the plan.

### 5.10 Cost accounting with cached input

Today `llm_calls.cost_estimate` prices every input token at the full rate and `models.yaml` has no cached price, so reported costs overstate reality, and an evaluation could reward a change that lowers our number while raising the real bill (decided with the user to fix in 2a).

- **Record cached tokens.** Migration `0007` adds `llm_calls.cached_input_tokens` (integer, nullable; existing rows stay null). `LLMClient` reads the cached-token count each provider reports in its usage data and stores it. A provider or response that reports none stores null and is priced as fully uncached, which never underestimates spending.
- **Record cached prices and context windows.** Each catalog entry in `config/models.yaml` gains `cached_input_usd_per_mtok` and `context_window` (tokens), taken from each provider's official pricing and model pages during implementation, with `prices_updated` set to the check date. A provider without prompt caching for a model gets its full input price as the cached price. The config loader requires both fields for chat models (embeddings need neither).
- **Price it.** `cost_estimate` = uncached input × input price + cached input × cached price + output × output price, where `input_tokens` always stores the **total** input and uncached input = `input_tokens` − `cached_input_tokens`. Providers report usage differently (some include cached tokens in the input total and report them separately, some report hit and miss counts), so the client normalizes each provider's report to this pair before pricing; counting cached tokens at both prices is the error to test against. The monthly cap uses the same figure. Existing comments about peak versus off-peak DeepSeek pricing still apply (peak rates are used).
- **Cache writes are recorded and priced exactly (decided by the user).** The price check (2026-11-14) found that OpenAI bills cache writes at 1.25× the input rate (gpt-6-luna: $0.125 per million tokens for the default 30-minute cache); DeepSeek and Gemini list no write charge, and Gemini lists storage only next to explicit caching, which the app does not use (assumed: no storage charge for implicit caching; revisit if a bill shows one). Migration `0007` therefore also adds `llm_calls.cache_write_input_tokens` (integer, nullable), the catalog gains `cache_write_usd_per_mtok` (null when the provider has no write charge), and the client reads the written-token count each provider reports. The formula becomes: plain input × input price + cache-written input × write price (input price when null) + cached input × cached price + output × output price, where plain input = total input − cached − written.
- **Length tiers** are noted in the catalog (gpt-6-luna doubles above 272K input tokens; gemini-3.8-flash prices double from 2027-01-01); the 32,000-token budget stays far below any tier.
- **Guard the budget.** A config test asserts that `PLANNER_INPUT_TOKEN_BUDGET` is below, with margin (at most half), the `context_window` of every candidate of the `planning` and `planning_eval` tasks, so changing a model cannot silently break the guard.
- **Report it.** The planning evaluation prints, per scenario and overall, the cache hit rate (cached input ÷ total input) and the largest single-call input tokens. The `llm_calls` stats used elsewhere include cached tokens.

### 5.8 Evaluation additions

- The summary prints "as expected N/M" next to the raw pass rates.
- A `--no-shortlist` switch runs the planner without the shortlist (tools only), for comparison.
- New fictional scenarios (the existing 7 stay):
  - fridge-only with an empty inventory (expect `empty_inventory`, no plan, at most 3 model calls in total);
  - fridge-only that can make only one of two dishes (expect `not_enough_dishes`);
  - a time limit no dish can meet (expect `time_limit`);
  - "一荤一素" (expect one meat dish and one vegetable dish);
  - "8道菜，包含2个荤菜" with a library that has enough vegetable dishes (expect 2 meat and 6 vegetable dishes, soup stated as assumed);
  - "一荤一素" with a library that has no safe meat dish (expect `not_enough_in_library`, with `relax_split` and `import_recipes`);
  - "一荤一素" in fridge-only mode where the library has meat dishes but none is makeable from stock (expect `not_enough_dishes`, with `switch_to_shopping` and `relax_split`);
  - "丰盛点" with no count, and a request with no count at all (expect defaults stated as assumed, no invented numbers);
  - "三个人，两个菜" (expect two dishes and no soup) and "就吃碗面" (expect one staple and nothing else);
  - "三道素菜，就用冰箱里的" with only eggs in stock and four egg-only vegetable dishes (expect the shortlist to keep three makeable dishes and a passing plan);
  - "优先用鸡蛋" (expect egg dishes ranked first, other dishes still offered), "这顿一定要用鸡蛋" (expect at least one egg dish), "全做鸡蛋" (expect every dish to use eggs);
  - "全做鸡蛋" fridge-only with no eggs in stock (expect `named_ingredient`, with `relax_named`);
  - a library where one main ingredient dominates the vegetable dishes (expect the shortlist to spread main ingredients; checked by a code assertion on the shortlist, not by the model);
  - "两菜一汤加个炒饭" (expect two dishes, one soup, and one staple).
- Each scenario may state expected `reason` and expected next actions; the runner checks them.
- The "no wok" scenario now expects `no_cookware` (or H3 in `unmet`), not H4.

### 5.9 Success criteria (measured on the test database, `plan_v2`, against the plan 1 baseline)

- Feasible scenarios: first-attempt pass rate at least the baseline (6/6 of the original feasible ones), average planner calls about 2 (total calls per run at most 5 including intent and parsing).
- Each infeasible scenario ends within 8 model calls with the right reason and next actions; empty inventory within 3. The 8-call bound also covers requests that reach the planner and then fail validation.
- Paid evaluation runs: one full run after implementation (and, if constants are tuned, one confirming rerun), each approved by the user before it starts.
- Total cost and average latency below the baseline ($0.064 and 11.3 s for the original 7 scenarios). The baseline was priced without cache discounts; the comparison uses the same uncached pricing for both runs (computed from token counts), and the cache-aware cost is reported next to it.
- No planner call exceeds `PLANNER_INPUT_TOKEN_BUDGET` in the evaluation, and the cache hit rate is reported for every scenario.
- Every new scenario matches its expectation.

## 6. Decided against for now

- **Amounts and stock in the shortlist (decided by the user).** The shortlist does not show the model each dish's countable amounts or the current stock. It carries only the per-dish facts in Section 5.4, so less of the inventory is sent to the provider and the first message stays short. Competing dishes are caught by the S1 warning instead. Revisit if the evaluation shows many S1 warnings in accepted plans.

## 7. Left to the builder

- The exact prompt wording of `parse.md` and `planner.md` v2, within the rules above.
- The shortlist JSON shape and how it is placed in the first message.
- How "close to expiry" is weighted against stock in ranking, within the stated order.
- How the feasibility check reuses search, stock, and cookware code, as long as it never stops a satisfiable request.
- How each provider's cached-token count is read from the LangChain usage data (the field differs by provider).
- Task order, and where tests sit.
