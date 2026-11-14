# Phase 1 Plan 2a: Planner Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make planning cheap, fast, and honest about why a request cannot be met: a code-built, category-balanced shortlist in the planner's first message, an early stop with reasons and fitting next actions, one counting rule for every dish category, cache-aware cost accounting, and an evaluation that measures the change.

**Architecture:** New pure planning modules (`planning/shortlist.py`, `planning/feasibility.py`, `planning/composition.py`) compute candidates, feasibility, and category counts in code. The graph gains a feasibility step before the planner and before each retry; the planner's first message carries the shortlist in a cache-stable order, and a token-budget middleware ends runaway attempts. `LLMClient` records cached input tokens and prices them from `models.yaml`. Prompts move to `plan_v2`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, LangChain 1.4.2 / LangGraph, pytest; Next.js, next-intl, vitest.

**Spec:** `docs/superpowers/specs/2026-11-14-phase-1-plan-2a-planner-efficiency-design.md` (approved). It builds on `docs/superpowers/specs/2026-11-13-phase-1-agent-design.md`, which stays authoritative for everything 2a does not change. Background: `docs/phase-1-carryover.md`.

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository or a remote. Each "User commit" step gives the exact commands, then stops until the user confirms. Read-only Git commands may run directly.
- **Python:** `$PY` means the value of `python.defaultInterpreterPath` in `.vscode/settings.json`, read each time. Never copy that path into a file. Run backend commands from `backend/`.
- **Packages:** do not install Python or npm packages. If one is needed, stop and say which and why.
- **Secrets:** never read, print, create, edit, or delete `.env`. Tests never load `.env` (`Settings(_env_file=None)`). Never print database URLs.
- **Language:** every repository file in English; Chinese only in UI translations, dictionary names and aliases, and recipe text with expected results in tests and prompt examples.
- **Data:** no real recipe or evaluation data enters Git. Evaluation scenarios and the fictional library live in `data/eval/planning/` (git-ignored).
- **Databases:** tests, real-app checks, and evaluations use only `minemydinner_test`. Tests never assume `llm_calls` or `planning_runs` is empty and delete only rows they create.
- **Tests never call a real model or the network.** Paid calls happen only in the planning evaluation, and only after the user approves each run.
- **Every model call goes through `LLMClient`** (directly or through `CallLayerMiddleware`). Planning keeps `essential: true`; `planning_eval` stays `essential: false`.
- **Hard constraints are code:** H1-H7 (H7 is new: named ingredients), the search exclusions, and the feasibility check live in `minemydinner/planning/`; prompts only describe them. A plan with H2 or H5 is never shown.
- **Prompt versions:** `plan_v1` stays unchanged on disk. This plan creates `plan_v2` (`route.md`, `parse.md`, `planner.md`) and sets `PROMPT_VERSION = "plan_v2"`. `TOOLS_VERSION` changes; that is intended.
- **History is never edited after it is written** (prompt caching). No step trims or rewrites earlier messages.
- **Exact values:** `SHORTLIST_MIN_PER_CATEGORY = 5`, `SHORTLIST_MARGIN = 3`, `SHORTLIST_SAME_MAIN = 2`, `MAX_TOTAL_COUNT = 12`, `PLANNER_INPUT_TOKEN_BUDGET = 32000`, `MODEL_CALL_LIMIT = 6`, `TOOL_CALL_LIMIT = 10` (the last two confirmed or adjusted in Task 10), search hits capped at 20, `exclude_ids` at most 50.
- **Frontend tests, lint, and build** run through the PowerShell tool: `cd frontend; npm test; npm run lint; npm run build` (skip the build when port 3000 has a listener, and say so). Git Bash vitest fails collection.
- **Reply language:** the agent replies in the UI locale (`zh` or `en`); fixed reason texts are written by the backend in the request's locale.

## Review Focus

1. **A satisfiable request stopped early.** Choice groups, optional lines, child ingredients satisfying a parent line, and always-in-stock items must count as available exactly as `compute_missing` counts them; any uncertainty passes the request through. Pinned in Task 5 by `test_choice_group_and_child_stock_count_as_makeable` and `test_always_in_stock_counts_as_stock`.
2. **A stated count silently overridden by defaults.** "三个人，两个菜" must give two dishes and no soup; "就吃碗面" one staple and nothing else; a 0 total from parsing falls back to defaults only when no category was stated. Pinned in Task 2.
3. **The shortlist hides the dishes the request needs.** A request needing 6 vegetable dishes, a fridge where only egg-only dishes are makeable, or "全做鸡蛋" must still give the model enough usable candidates; expansion through `exclude_ids` must reach the rest. Pinned in Task 4 by `test_quota_grows_with_the_needed_count`, `test_spread_keeps_enough_makeable_dishes`, `test_named_ingredient_is_left_out_of_the_spread`, and `test_exclude_ids_reaches_the_next_batch`.
4. **Cached tokens counted twice, or a provider that reports none priced as cached.** Pinned in Task 1 by `test_cached_tokens_are_not_double_counted` and `test_missing_cache_report_is_priced_uncached`.
5. **A next action that cannot help.** "Switch to shopping" for missing cookware or an invented id, "fewer dishes" for a one-dish meal, "any mix" when no split was named. Pinned in Task 6 by the parametrized `test_next_actions_by_reason`.

---

### Task 1: Cache-aware cost accounting

**Files:**
- Create: `backend/migrations/versions/0007_llm_calls_cached_tokens.py`
- Modify: `backend/minemydinner/models/planning.py` (`LlmCall`), `backend/minemydinner/llm/config.py` (`CatalogEntry`), `backend/config/models.yaml`, `backend/minemydinner/llm/client.py` (`_estimate_cost`, `_log`, usage normalization), `backend/minemydinner/agent/middleware.py` (`_usage` passes the normalized usage through)
- Test: `backend/tests/test_llm_client.py`, `backend/tests/test_llm_config.py`

**Interfaces:**
- Produces: `LlmCall.cached_input_tokens: int | None`. `CatalogEntry.cached_input_usd_per_mtok: Decimal | None` and `CatalogEntry.context_window: int | None`, both required (not None) for entries whose capabilities include `tool_calling` or `strict_json`, absent for embedding entries. `normalize_usage(usage: Mapping[str, Any] | None) -> dict[str, int] | None` in `llm/client.py`, returning `{"input_tokens": total, "cached_input_tokens": cached, "output_tokens": out}` with `cached_input_tokens` 0 when the provider reported nothing. `_estimate_cost(model, input_tokens, cached_input_tokens, output_tokens) -> Decimal`.
- `PLANNER_INPUT_TOKEN_BUDGET = 32000` lives in `minemydinner/agent/planner.py` (used by Task 7); this task's config test imports it.

- [ ] **Step 1: Price check (controller, before dispatch).** For every chat model in the catalog (`gemini-3.5-flash-lite`, `gemini-3.8-flash`, `gpt-6-luna`, `deepseek-flash`, `deepseek-v4-pro`), read the provider's official pricing and model pages and note: cached-input price per million tokens, any cache write or storage charge, any price tier by context length, and the context window. Show the table to the user and get their confirmation before the implementer writes the values. A model without prompt caching gets its full input price as the cached price.

- [ ] **Step 2: Write the failing tests**

```python
# test_llm_client.py
def test_cached_tokens_are_not_double_counted():
    model = catalog_model(input_usd_per_mtok="1.00", cached_input_usd_per_mtok="0.10", output_usd_per_mtok="2.00")
    # 1,000,000 input of which 600,000 cached, 1,000,000 output
    assert _estimate_cost(model, 1_000_000, 600_000, 1_000_000) == Decimal("0.40") + Decimal("0.06") + Decimal("2.00")

def test_missing_cache_report_is_priced_uncached():
    assert normalize_usage({"input_tokens": 100, "output_tokens": 5}) == {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 5}

def test_langchain_cache_read_is_read():
    usage = {"input_tokens": 100, "output_tokens": 5, "input_token_details": {"cache_read": 60}}
    assert normalize_usage(usage)["cached_input_tokens"] == 60

def test_llm_calls_row_stores_cached_tokens(session_factory, fake_cached_model):  # 100 in, 60 cached
    client.invoke_structured(...)
    row = latest_call()
    assert (row.input_tokens, row.cached_input_tokens) == (100, 60)
    assert row.cost_estimate == expected_cost_with_cache

# test_llm_config.py
def test_chat_models_declare_cached_price_and_context_window():
    cfg = load_llm_config(REPO_CONFIG)
    for model in cfg.models.values():
        if "embeddings" not in model.capabilities:
            assert model.cached_input_usd_per_mtok is not None and model.context_window

def test_planner_budget_is_at_most_half_of_every_planning_window():
    cfg = load_llm_config(REPO_CONFIG)
    for task in ("planning", "planning_eval"):
        for name in cfg.tasks[task].candidates:
            assert PLANNER_INPUT_TOKEN_BUDGET * 2 <= cfg.models[name].context_window

def test_chat_model_without_cached_price_is_rejected(tmp_path): ...  # ConfigError
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_llm_client.py tests/test_llm_config.py -v`
Expected: FAIL (`normalize_usage`, the new fields, and `PLANNER_INPUT_TOKEN_BUDGET` do not exist).

- [ ] **Step 4: Implement.** Migration `0007` adds the nullable integer column (downgrade drops it), following `0005`'s style. `normalize_usage` reads LangChain's `usage_metadata` (`input_tokens` is the total including cached; `input_token_details.cache_read` is the cached part); confirm in the installed `langchain-openai`, `langchain-deepseek`, and `langchain-google-genai` how each fills these fields and handle any provider that reports cached tokens elsewhere, keeping `input_tokens` the total. `run_task`, `invoke_structured`, `CallLayerMiddleware`, and `AttemptError` pass the normalized dict; `_log` stores all three counts. The monthly cap sums `cost_estimate` as today. Embedding calls store `cached_input_tokens` 0. Add `PLANNER_INPUT_TOKEN_BUDGET = 32000` to `agent/planner.py`. Fill `models.yaml` with the confirmed values from Step 1 and set `prices_updated` to the check date; note any length tier in a comment.

- [ ] **Step 5: Run the migration on the test database, then the tests**

Run: `cd backend && "$PY" -m pytest tests/test_llm_client.py tests/test_llm_config.py tests/test_schema.py -v`, then the full suite `"$PY" -m pytest -q`.
Expected: PASS. Downgrade and upgrade of `0007` succeed on the test database.

- [ ] **Step 6: User commit**

```bash
git add backend
git commit -m "feat: record cached input tokens and price them from the catalog"
```

---

### Task 2: One counting rule for every dish category

**Files:**
- Create: `backend/minemydinner/agent/prompts/plan_v2/route.md` (copy of v1), `plan_v2/parse.md`, `plan_v2/planner.md` (copy of v1 for now; Task 7 rewrites it)
- Modify: `backend/minemydinner/agent/request.py`, `backend/minemydinner/planning/validate.py` (`PlanSpec` only), `backend/minemydinner/agent/graph.py` (`parse_request` routing for problems), `backend/pyproject.toml` only if the package-data glob does not already cover `plan_v2`
- Test: `backend/tests/test_agent_request.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- Produces in `request.py`: `PROMPT_VERSION = "plan_v2"`. `ParsedRequest` gains `meat_dishes`, `vegetable_dishes`, `staples`, `desserts`, `snacks` (each `int | None`, `ge=0`); `prefer_use: list[str]` and `all_use: list[str]` next to the existing `must_use` (each at most 10 names; `must_use` now means "at least one dish uses it"); and `range_fields: list[Literal["dishes", "soups", "meat_dishes", "vegetable_dishes", "staples", "desserts", "snacks"]]` (counts taken from the lower end of a range). `COUNT_FIELDS = ("dishes", "soups", "meat_dishes", "vegetable_dishes", "staples", "desserts", "snacks")`. `request_problem(parsed: ParsedRequest) -> Literal["split_exceeds_total", "too_many"] | None`. `apply_defaults(parsed, self_diner_id=None) -> tuple[PlanSpec, MealConstraints, list[str]]` keeps its signature with the new rules. `needs_question(parsed, style)` asks about "composition" only when no count field was stated.
- Produces in `validate.py`: `PlanSpec` gains `meat_dishes: int | None = None`, `vegetable_dishes: int | None = None`, `split_is_minimum: bool = False`, `staples: int = 0`, `desserts: int = 0`, `snacks: int = 0` (all `ge=0`), and `must_use: list[str] = []`, `all_use: list[str] = []`, `prefer_use: list[str] = []` copied from the parse by `apply_defaults`. Its validator requires `dishes + soups + staples + desserts + snacks` between 1 and `MAX_TOTAL_COUNT = 12`.
- Produces in `graph.py`: when `request_problem` returns a code, `parse_request` interrupts with `{"kind": "question", "fields": ["composition"], "problem": code}` regardless of the request style; the resume text is appended and re-parsed, as for ask-first questions.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("parsed, spec_counts, defaults", [
    (P(headcount=3), dict(dishes=2, soups=1), ["dishes", "soups", "mode"]),                 # no count stated
    (P(headcount=3, dishes=2), dict(dishes=2, soups=0), ["mode"]),                         # 三个人，两个菜
    (P(staples=1), dict(dishes=0, soups=0, staples=1), ["headcount", "mode"]),              # 就吃碗面
    (P(dishes=2, soups=1, staples=1), dict(dishes=2, soups=1, staples=1), ["headcount", "mode"]),
    (P(desserts=1), dict(dishes=0, desserts=1), ["headcount", "mode"]),
])
def test_defaults_apply_only_when_no_count_was_stated(parsed, spec_counts, defaults): ...

def test_split_without_total_sets_the_total():            # 两个荤菜 -> dishes 2, meat 2, vegetable unconstrained
def test_named_split_below_the_total_becomes_minimums():   # 三个菜，一荤一素 -> split_is_minimum True
def test_split_above_the_total_is_a_problem():            # request_problem == "split_exceeds_total"
def test_more_than_twelve_is_a_problem():                 # request_problem == "too_many"
def test_range_fields_are_reported_as_assumed():           # range_fields=["dishes"] -> "dishes" in defaults_used
def test_plan_spec_rejects_zero_and_over_twelve(): ...
def test_ask_first_asks_about_composition_only_without_any_count(): ...
def test_named_ingredient_lists_reach_the_spec():               # prefer/must/all copied unchanged

# test_agent_graph.py
def test_contradiction_interrupts_with_its_problem(graph_env):
    out = send(graph_env, "两个菜，三个荤菜")    # fake parse returns dishes=2, meat_dishes=3
    assert pending(graph_env) == {"kind": "question", "fields": ["composition"], "problem": "split_exceeds_total"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_agent_request.py tests/test_agent_graph.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement.** `apply_defaults`: a count is "stated" when its `ParsedRequest` field is not None; if none of `COUNT_FIELDS` is stated, dishes and soups come from `default_composition(headcount)` and both go in `defaults_used`; otherwise every unstated count is 0 and nothing is added. `dishes` unstated but a split named → `dishes = meat + vegetable` (the stated sides). Split below a stated total → `split_is_minimum = True`. `range_fields` entries join `defaults_used`. Order of `defaults_used`: headcount, the count fields in `COUNT_FIELDS` order, mode. Write `plan_v2/parse.md` with the rules of design 5.1 (clear mapping only, ranges to the lower end with `range_fields`, vague phrases to `query`, "想喝汤" = 1 soup, "下饭菜" and "凉菜" as query, "other" category never counted, and the three named-ingredient fields: "优先用/想用掉些" → `prefer_use`, "要用到/一定要用" → `must_use`, "全做/每道都要有" → `all_use`; "不一定要用指定的食材" moves earlier `must_use` and `all_use` names into `prefer_use`), with Chinese and English examples covering every row of the design's tables.

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_agent_request.py tests/test_agent_graph.py tests/test_validate_plan.py -v`, then `"$PY" -m pytest -q`.
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: plan_v2 parsing with one counting rule for every dish category"
```

---

### Task 3: Validation: category counts, H7, closest attempt, S1 across dishes

**Files:**
- Create: `backend/minemydinner/planning/composition.py`, `backend/minemydinner/planning/named.py`
- Modify: `backend/minemydinner/planning/validate.py` (`_h4`, `_s1`), `backend/minemydinner/agent/graph.py` (`check_plan` best-attempt choice)
- Test: `backend/tests/test_validate_plan.py`, `backend/tests/test_planning_composition.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- Produces in `composition.py`: `CATEGORY_OF_DISH_TYPE = {"meat dish": "meat_dishes", "vegetable dish": "vegetable_dishes", "soup": "soups", "staple food": "staples", "dessert": "desserts", "snack": "snacks"}` ("other" maps to nothing). `count_categories(dish_types: Iterable[str]) -> dict[str, int]` with keys `dishes` (meat + vegetable), `soups`, `meat_dishes`, `vegetable_dishes`, `staples`, `desserts`, `snacks`. `required_counts(spec: PlanSpec) -> dict[str, int]` (only categories with a requirement; meat/vegetable only when named). `composition_gap(spec: PlanSpec, present: dict[str, int]) -> int`: sum of |required − present| over required categories, where for `split_is_minimum` meat/vegetable only a shortfall counts.
- `_h4` returns one `H4` finding when any requirement fails, with `detail = {"required": required_counts(spec), "present": count_categories(...), "minimum": spec.split_is_minimum}`. Recipes of type "other" are ignored.
- `_s1` emits one `S1` finding per ingredient whose summed exact amount exceeds stock, with `recipe_id = None` and `detail = {"ingredient_id", "name", "needed", "have", "unit", "parts": [{"recipe_id", "amount"}]}`. Same summable cases as today (grams/grams, pieces/pieces); optional lines skipped as today. The summing lives in `sum_exact_amounts(ctx, recipes) -> dict[int, ExactNeed]` in `composition.py` so T1.5 can reuse it.
- `check_plan` keeps `best` as the attempt with the fewest violations, then the smallest `composition_gap`, then the later attempt.
- `named.py` produces `recipes_using(session: Session, ctx: PlanningContext, name: str, recipe_ids: Iterable[int]) -> set[int]`: the recipes among `recipe_ids` that use the named ingredient, by the same rule as search's `must_use` filter (linked line to the entry or a child; unlinked line or unknown name by folded containment in line name or raw). Search's existing `_must_use_clause` and this function must agree (one shared implementation of the rule).
- `validate_plan` adds `_h7(session, ctx, spec, recipes)`: one `H7` finding per failing name with `detail = {"name", "rule": "must" | "all", "recipes_without": [ids]}`; `must`: no counted dish uses it; `all`: some counted dish does not. "other" recipes are ignored. H7 is not in `NEVER_SHOWN`.

- [ ] **Step 1: Write the failing tests**

```python
def test_h4_checks_staples_and_desserts_exactly(): ...          # unrequested dessert -> H4
def test_h4_named_split_is_exact():                              # 一荤一素 with two vegetable dishes -> H4
def test_h4_minimum_split_counts_only_shortfall(): ...            # 3 dishes, at least 1+1: 1 meat 2 veg passes
def test_other_type_is_never_counted(): ...
def test_composition_gap_prefers_two_meat_over_one():           # spec 3 meat 3 veg
    assert composition_gap(spec, {"meat_dishes": 2, "vegetable_dishes": 3, ...}) < composition_gap(spec, {"meat_dishes": 1, "vegetable_dishes": 3, ...})
def test_s1_sums_one_ingredient_across_dishes():                # eggs 3 in stock, 2 + 2 needed
    (w,) = [f for f in validate_plan(...).warnings if f.code == "S1"]
    assert w.detail["needed"] == 4 and w.detail["have"] == 3 and len(w.detail["parts"]) == 2
def test_s1_skips_vague_and_level_amounts(): ...
def test_h7_must_use_needs_one_dish(): ...                        # 一定要用鸡蛋, no egg dish -> H7 rule "must"
def test_h7_all_use_needs_every_dish(): ...                       # 全做鸡蛋, one dish without -> H7 rule "all", recipes_without [that id]
def test_h7_child_ingredient_satisfies_the_parent(): ...          # 用猪肉 satisfied by 猪肉末
def test_h7_unknown_name_matches_line_text(): ...
def test_prefer_use_is_never_validated(): ...

# test_agent_graph.py
def test_closest_attempt_breaks_ties_by_composition_gap(graph_env):
    # attempt 1: 1 meat 3 veg; attempt 2: 2 meat 3 veg; attempt 3: 1 meat 3 veg (all one H4)
    assert shown_ids(out) == ATTEMPT_2_IDS
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_validate_plan.py tests/test_planning_composition.py tests/test_agent_graph.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** in `composition.py`, `named.py`, and `validate.py`; update `_violation_for_model` and `_unmet_for_user` in `graph.py` for the new H4 detail and for H7 (state required and present counts per category, and which dishes lack a named ingredient, in plain words, en and zh).

- [ ] **Step 4: Run the tests**, then the full backend suite.
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: category counts in H4, named ingredients as H7, closest attempt by composition gap, S1 across dishes"
```

---

### Task 4: Search by category with per-recipe facts, and the shortlist

**Files:**
- Create: `backend/minemydinner/planning/shortlist.py`
- Modify: `backend/minemydinner/planning/search.py`, `backend/minemydinner/planning/tools.py` (`SearchRecipesArgs`, `search_recipes_tool`, `PlannerDeps`)
- Test: `backend/tests/test_planning_search.py`, `backend/tests/test_planning_shortlist.py`, `backend/tests/test_planning_tools.py`

**Interfaces:**
- `search_recipes(...)` gains `dish_types: Sequence[str] | None = None` (filter), `exclude_ids: Sequence[int] = ()`, `query_vectors: dict[str, list[float]] | None = None` (per-run cache: a query already embedded is not embedded again). `RecipeHit` gains `missing: list[str]`, `missing_cookware: list[str]`, `uses_expiring: list[str]`, `dislikes: list[str]` (diner codes and ingredient names, no real names). Ranking within the result: query relevance as today, then fewer missing lines, then more expiring ingredients used, then no dislike before dislike, then (only when `max_time` is set) a firm time source before an estimated one. Hits capped at `limit` (default 20).
- Tool `search_recipes` gains `dish_type: Literal[<DISH_TYPES except "other">] | None` and `exclude_ids: list[int]` (`max_length=50`, items `ge=1`); its description says hits carry missing items and cookware, and that `exclude_ids` fetches the next batch. `PlannerDeps` gains `query_vectors: dict[str, list[float]]` (default empty dict, one per planning run).
- `shortlist.py` produces `ShortlistEntry` (the hit fields above) and `build_shortlist(session, client, ctx, spec: PlanSpec, parsed: ParsedRequest, query_vectors: dict) -> dict[str, list[ShortlistEntry]]` keyed by dish type. Constants: `SHORTLIST_MIN_PER_CATEGORY = 5`, `SHORTLIST_MARGIN = 3`, `SHORTLIST_SAME_MAIN = 2`. Needed dish types from `spec` (`dishes` ≥ 1 → meat and vegetable dish, but only the named side when the split names one side as the whole total; `soups`/`staples`/`desserts`/`snacks` ≥ 1 → their type). Slots per type: `max(MIN, needed + MARGIN)`. Named ingredients: `all_use` filters each category to recipes using every named ingredient (`recipes_using`, Task 3); `must_use` and `prefer_use` only rank (a recipe using more of them first, right after query relevance). Main-ingredient spread: at most `SHORTLIST_SAME_MAIN` entries per identical set of main ingredients (`role == "main"`, linked lines, compared through the parent entry when one exists), with the named ingredients (`prefer_use`, `must_use`, `all_use`) removed from each set before comparing; recipes with no linked main line are never grouped, and a set that is empty after removal counts as unique. After the spread, if a category has fewer makeable entries (fridge-only: nothing missing; shopping: all) than it needs, or no entry using a `must_use` ingredient while the library has one, the highest-ranked removed recipes that fix this are put back, ignoring the per-group limit and replacing the lowest-ranked entries that do not help.

- [ ] **Step 1: Write the failing tests**

```python
def test_dish_type_filter_and_exclude_ids(session): ...
def test_hits_carry_missing_items_and_cookware(session): ...
def test_fewer_missing_ranks_first_after_relevance(session): ...
def test_repeated_query_is_embedded_once(session, counting_client): ...
def test_tool_rejects_other_type_and_too_many_exclude_ids(): ...

def test_each_needed_category_gets_its_quota(session):            # 两菜一汤 -> meat, vegetable, soup keys only
def test_quota_grows_with_the_needed_count(session):              # 8 dishes, 2 meat -> 9 vegetable slots
def test_same_main_ingredient_is_spread(session):                 # 6 egg-based vegetable dishes -> at most 2 in the list
def test_recipe_without_main_line_is_not_grouped(session): ...
def test_spread_keeps_enough_makeable_dishes(session):            # only eggs in stock, 3 vegetable dishes, 4 egg-only makeable -> 3 makeable in the list
def test_named_ingredient_is_left_out_of_the_spread(session):     # 全做鸡蛋: 蒸蛋羹 and 水煮蛋 both kept; 番茄炒蛋 vs 番茄蛋汤 compared by tomato
def test_all_use_filters_and_prefer_use_ranks(session): ...
def test_must_use_keeps_a_dish_using_it(session): ...             # the only egg dish ranks low but stays in the list
def test_exclude_ids_reaches_the_next_batch(session):             # 12 soups, quota 5: excluding the first 5 returns the next
def test_shortlist_respects_dietary_exclusions(session): ...      # an H2 recipe never appears
```

- [ ] **Step 2: Run to verify they fail**, implement, run again.

Run: `cd backend && "$PY" -m pytest tests/test_planning_search.py tests/test_planning_shortlist.py tests/test_planning_tools.py -v`, then the full suite.
Expected: PASS; `TOOLS_VERSION` changes (update the version tests' expectations only where they pin a value).

- [ ] **Step 3: User commit**

```bash
git add backend
git commit -m "feat: category search with per-recipe facts and a balanced shortlist"
```

---

### Task 5: Feasibility check

**Files:**
- Create: `backend/minemydinner/planning/feasibility.py`
- Test: `backend/tests/test_planning_feasibility.py`

**Interfaces:**
- Produces: `REASONS = ("empty_inventory", "no_cookware", "not_enough_dishes", "time_limit", "not_enough_in_library", "named_ingredient")` (report order). For `named_ingredient`, `Shortfall.category` names the dish category (or None for a `must_use` name nobody uses) and the dataclass also carries `ingredient: str | None` (default None). `Shortfall(reason: str, category: str | None, needed: int | None, available: int | None)` (frozen dataclass). `check_feasibility(session: Session, ctx: PlanningContext, spec: PlanSpec) -> list[Shortfall]` — empty means "plan it".
- Rules (design 5.2): `empty_inventory` when fridge-only and no inventory row beyond always-in-stock items. Per needed category (from `required_counts(spec)`, using the meat/vegetable total when no split is named): `not_enough_in_library` when safe recipes (H2 and unknown-allergen exclusions as in search) < needed, any mode; `not_enough_dishes` when fridge-only and recipes with no missing non-optional line per `compute_missing` < needed; `no_cookware` when no safe recipe of that category fits owned cookware; `time_limit` when a time limit is set and no safe recipe of that category has a firm time within it. Minimum splits compare as minimums. `named_ingredient`: a `must_use` name used by no safe recipe in any needed category (fridge-only: by no makeable recipe; a name not in stock and not always in stock counts as unavailable); for `all_use`, per needed category, fewer safe (fridge-only: makeable) recipes using every named ingredient than needed. The whole library is checked, not the shortlist. Uses `recipes_using` (Task 3).

- [ ] **Step 1: Write the failing tests**

```python
def test_empty_fridge_in_fridge_only_mode(session): ...
def test_empty_fridge_is_fine_when_shopping(session): ...
def test_not_enough_in_library_reports_counts(session):          # no meat dish -> Shortfall("not_enough_in_library", "meat_dishes", 1, 0)
def test_not_enough_dishes_from_stock(session):                   # library has meat dishes, none makeable
def test_no_cookware_per_category(session):
def test_time_limit_counts_only_firm_times(session): ...
def test_choice_group_and_child_stock_count_as_makeable(session): ...
def test_always_in_stock_counts_as_stock(session): ...
def test_minimum_split_compares_as_minimum(session): ...
def test_satisfiable_request_returns_nothing(session): ...
def test_all_use_without_the_ingredient_in_stock(session):         # 全做鸡蛋, fridge-only, no eggs -> named_ingredient
def test_all_use_counts_per_category(session):                     # 3 vegetable dishes, 1 makeable with eggs -> needed 3, available 1
def test_must_use_used_by_some_dish_passes(session): ...
```

- [ ] **Step 2: Run to verify they fail**, implement, run again.

Run: `cd backend && "$PY" -m pytest tests/test_planning_feasibility.py -v`, then the full suite.
Expected: PASS.

- [ ] **Step 3: User commit**

```bash
git add backend
git commit -m "feat: feasibility check with shortfall reasons"
```

---

### Task 6: Early stop, reasons, and next actions in the graph

**Files:**
- Modify: `backend/minemydinner/agent/graph.py`, `backend/minemydinner/agent/state.py`
- Test: `backend/tests/test_agent_graph.py`, `backend/tests/test_chat_api.py`

**Interfaces:**
- Consumes: `check_feasibility` (Task 5), `required_counts` (Task 3).
- Produces: a `check_feasibility` graph step after `load_context` and before each retry. When it returns shortfalls, the graph goes to `present_plan` without calling the planner; the record gets `{"kind": "feasibility", "shortfalls": [...]}` and finishes with `final_passed=False`. `current_plan` gains `reasons: [{"reason", "category", "needed", "available", "text"}]` (text in the request's locale, fixed strings stating needed and available counts). `NextAction` values: `switch_to_shopping`, `fewer_dishes`, `drop_time_limit`, `relax_split`, `relax_named`, `update_inventory`, `register_cookware`, `import_recipes`.
- `next_actions(spec: dict, reasons: list[str], unmet_codes: list[str]) -> list[str]` replaces `_next_actions`, following the design 5.3 table: union over reasons and unmet codes; `fewer_dishes` only when the spec's counts total more than 1; `relax_split` only when the spec names a split and a meat or vegetable category is short (or H4 fails on it); `switch_to_shopping` only in fridge-only mode and only for `empty_inventory`, `not_enough_dishes`, `named_ingredient`, or H1; `relax_named` for `named_ingredient` or H7; `update_inventory` for `named_ingredient` only in fridge-only mode; H5 alone gives none. Order follows the order of `NextAction` above.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("spec, reasons, unmet, expected", [
    (FRIDGE_2, ["empty_inventory"], [], ["switch_to_shopping", "fewer_dishes", "update_inventory"]),
    (SHOP_2, ["no_cookware"], [], ["fewer_dishes", "register_cookware"]),
    (FRIDGE_1, ["no_cookware"], [], ["register_cookware"]),
    (SPLIT_1_1, ["not_enough_in_library"], [], ["fewer_dishes", "relax_split", "import_recipes"]),
    (SHOP_2, [], ["H5"], []),
    (TIME_2, ["time_limit"], [], ["fewer_dishes", "drop_time_limit"]),
    (FRIDGE_ALL_EGG_3, ["named_ingredient"], [], ["switch_to_shopping", "fewer_dishes", "relax_named", "update_inventory", "import_recipes"]),
    (SHOP_MUST_EGG_2, [], ["H7"], ["fewer_dishes", "relax_named", "import_recipes"]),
])
def test_next_actions_by_reason(spec, reasons, unmet, expected): ...

def test_infeasible_request_skips_the_planner(graph_env):        # zero planner model calls, plan has reasons, no recipes
def test_feasibility_event_is_recorded_and_run_finished(graph_env): ...
def test_reason_text_states_needed_and_available_in_locale(graph_env): ...   # zh and en
def test_feasibility_is_rechecked_before_a_retry(graph_env): ...
def test_feasible_request_reaches_the_planner(graph_env): ...

# test_chat_api.py
def test_plan_event_carries_reasons_and_new_actions(client, chat_env_empty_fridge): ...
```

- [ ] **Step 2: Run to verify they fail**, implement, run again.

Run: `cd backend && "$PY" -m pytest tests/test_agent_graph.py tests/test_chat_api.py -v`, then the full suite.
Expected: PASS.

- [ ] **Step 3: User commit**

```bash
git add backend
git commit -m "feat: early stop with stated reasons and next actions that fit them"
```

---

### Task 7: Planner input: shortlist first, cache-stable retries, token budget

**Files:**
- Modify: `backend/minemydinner/agent/graph.py` (`planner`, `request_summary`, `retry_message`), `backend/minemydinner/agent/state.py` (`planner_opening`), `backend/minemydinner/agent/planner.py`, `backend/minemydinner/agent/prompts/plan_v2/planner.md`
- Create: `backend/minemydinner/agent/budget.py`
- Test: `backend/tests/test_agent_graph.py`, `backend/tests/test_agent_middleware.py`

**Interfaces:**
- Consumes: `build_shortlist` (Task 4), `PLANNER_INPUT_TOKEN_BUDGET` (Task 1), normalized usage (Task 1).
- Produces: `GraphDeps.use_shortlist: bool = True`. The planner's first `HumanMessage` is built once per run and stored in state as `planner_opening: str`: the shortlist as compact JSON (`{"meat dish": [...], ...}`, entries with id, title, time and source, servings, missing, missing_cookware, uses_expiring, dislikes), then the request summary (the summary also states the counts, the split and whether it is a minimum, and staples/desserts/snacks). Without the shortlist (`use_shortlist=False`), the opening is the request summary alone. Every attempt starts the inner agent with `[HumanMessage(planner_opening)]` plus, on a retry, one appended message: the violations as today and the previous attempt's summary (shortlist ids offered, ids proposed, missing items per proposed recipe).
- `budget.py` produces `TokenBudgetMiddleware(budget: int)` (an `AgentMiddleware`): after each model call, when the provider-reported input tokens of that call reached the budget and the model asked for tools, the run ends before the next model call (as when the call limit is hit). It writes nothing else. Confirm the installed 1.4.2 API for ending a run from a hook (`after_model` with a jump to the end).
- `planner.py`: `MODEL_CALL_LIMIT = 6`, `TOOL_CALL_LIMIT = 10`, middleware order `CallLayerMiddleware`, `TokenBudgetMiddleware(PLANNER_INPUT_TOKEN_BUDGET)`, the two limit middlewares. `planner.md` v2: choose from the shortlist when it fits, search with `dish_type` and `exclude_ids` only when it does not, read details only when needed, never invent ids, hard constraints are checked by code, reply in the locale, final answer `PlanProposal`.

- [ ] **Step 1: Write the failing tests**

```python
def test_first_message_is_shortlist_then_summary(graph_env): ...
def test_retry_reuses_the_identical_opening(graph_env):
    calls = recorded_model_calls(run)
    assert calls[retry_index]["messages"][: len(opening)] == calls[0]["messages"][: len(opening)]
def test_retry_message_summarizes_the_previous_attempt(graph_env): ...
def test_no_shortlist_switch_sends_summary_only(graph_env): ...
def test_budget_ends_the_attempt_without_rewriting_history(planner_env_big_inputs):
    # fake model reports input_tokens >= budget and asks for a tool -> run ends; earlier messages unchanged
def test_typical_request_needs_one_planner_call(graph_env_shortlist_fits): ...
```

- [ ] **Step 2: Run to verify they fail**, implement, run again.

Run: `cd backend && "$PY" -m pytest tests/test_agent_graph.py tests/test_agent_middleware.py -v`, then the full suite.
Expected: PASS.

- [ ] **Step 3: User commit**

```bash
git add backend
git commit -m "feat: shortlist-first planner input, cache-stable retries, and an input token budget"
```

---

### Task 8: Plan card and inventory link targets

**Files:**
- Modify: `frontend/src/lib/chat.ts` (`NextAction`, `ChatPlan`), `frontend/src/components/PlanCard.tsx`, `frontend/src/components/ChatPage.tsx` (fixed messages for `relax_split`; problem hint for `question` interrupts), `frontend/src/components/InventoryManager.tsx` (anchor `id="cookware"` on the owned-cookware section), `frontend/messages/en.json`, `frontend/messages/zh.json`
- Test: `frontend/src/components/PlanCard.test.tsx`, `frontend/src/components/ChatPage.test.tsx`, `frontend/src/lib/chat.test.ts`

**Interfaces:**
- Consumes: `current_plan.reasons`, the new `NextAction` values, the new `spec` fields (Tasks 2, 6), the S1 `parts` detail (Task 3), `pending.problem` (Task 2).
- Produces: `NextAction` gains `relax_split`, `relax_named`, `update_inventory`, `register_cookware`, `import_recipes`. Link actions render as links: `update_inventory` → `/{locale}/inventory`, `register_cookware` → `/{locale}/inventory#cookware`, `import_recipes` → `/{locale}/import`; the other five stay buttons sending fixed messages (`relax_split`: zh "荤素都行", en "Any mix of meat and vegetable dishes is fine"; `relax_named`: zh "不一定要用指定的食材", en "The named ingredients are optional"). The "As you asked" line also lists named ingredients by kind ("优先用：鸡蛋", "一定要用：鸡蛋", "每道都用：鸡蛋"). The card renders `reasons[].text` above unmet rules; an "As you asked" line from the spec fields not in `defaults_used` (counts with the split and "at least" when `split_is_minimum`, mode, time limit); the "Assumed" line only when `defaults_used` is not empty; the S1 warning with its parts ("鸡蛋：番茄炒蛋 2 + 蛋花汤 2，共需 4，库存 3"). A `question` interrupt with `problem` shows a hint for `split_exceeds_total` or `too_many`.

- [ ] **Step 1: Write the failing tests**

```tsx
it("renders link actions as links with the locale", () => { ... });           // register_cookware -> /zh/inventory#cookware
it("sends the fixed relax_split message", async () => { ... });
it("sends the fixed relax_named message", async () => { ... });
it("lists named ingredients by kind under what was asked", () => { ... });
it("shows reasons with needed and available counts", () => { ... });
it("states what was asked and only then what was assumed", () => { ... });     // no Assumed line when defaults_used is empty
it("shows 'at least' for a minimum split", () => { ... });
it("renders the S1 warning with each dish's part", () => { ... });
it("shows the contradiction hint for a composition question", async () => { ... });
```

- [ ] **Step 2: Run to verify they fail**, implement, run again (PowerShell): `cd frontend; npm test; npm run lint; npm run build`.
Expected: PASS; en and zh key sets match.

- [ ] **Step 3: User commit**

```bash
git add frontend
git commit -m "feat: plan card shows reasons, what was asked, link actions, and S1 parts"
```

---

### Task 9: Evaluation additions

**Files:**
- Modify: `backend/minemydinner/evaluation/planning.py`, `backend/tests/test_planning_eval.py`
- Create locally, never committed: additions to `data/eval/planning/scenarios.yaml` and `library.yaml`

**Interfaces:**
- `RunOutcome` gains `input_tokens: int`, `cached_input_tokens: int`, `max_call_input_tokens: int`, `uncached_cost: Decimal` (all tokens priced at the full input rate, for comparison with the plan 1 baseline), `reasons: list[str]`, `next_actions: list[str]`, `as_expected: bool`. `summarize` adds `"as_expected": "N/M"`, `"cache_hit_rate"`, `"max_call_input_tokens"`, `"uncached_cost_usd"`. Scenarios gain optional `expect_reasons: list[str]`, `expect_next_actions: list[str]`, `expect_counts: dict[str, int]` (category counts of the shown plan); `as_expected` checks every expectation given plus `expect_plan`. CLI gains `--no-shortlist` (sets `GraphDeps.use_shortlist=False`).
- New scenarios (design 5.8): empty fridge in fridge-only mode; fridge-only that can make only one of two dishes; a time limit no dish meets; "一荤一素"; "8道菜，包含2个荤菜"; "一荤一素" with no safe meat dish; "一荤一素" fridge-only with meat dishes none makeable; "丰盛点" without a count; a request with no count; "三个人，两个菜"; "就吃碗面"; "两菜一汤加个炒饭"; a library where one main ingredient dominates the vegetable dishes (checked by a code assertion on `build_shortlist`, not by the model); two dishes competing for the same eggs (expect the S1 warning); "三道素菜，就用冰箱里的" with only eggs in stock and four egg-only vegetable dishes (expect three makeable dishes in the shortlist and a passing plan); "优先用鸡蛋", "这顿一定要用鸡蛋", "全做鸡蛋" (expect, respectively, egg dishes first with others still offered, at least one egg dish, every dish with eggs); "全做鸡蛋" fridge-only with no eggs (expect `named_ingredient` and `relax_named`). The "no wok" scenario now expects `no_cookware` or H3.

- [ ] **Step 1: Write the failing tests** for `summarize` (fictional numbers including cached tokens and an expected failure), for the expectation checks, and a fake-model end-to-end of one infeasible scenario that asserts zero planner calls and the reason.
- [ ] **Step 2: Run to verify they fail**, implement, run again.

Run: `cd backend && "$PY" -m pytest tests/test_planning_eval.py -v`, then the full suite.
Expected: PASS.

- [ ] **Step 3: User commit**

```bash
git add backend
git commit -m "feat: evaluation reports cache use, expectations, and a no-shortlist mode"
```

---

### Task 10: Measure against the baseline

**Files:**
- Modify (only if tuned): `backend/minemydinner/planning/shortlist.py` constants, `backend/minemydinner/agent/planner.py` limits
- Modify: `docs/phase-1-carryover.md` (the 2a result next to the plan 1 baseline)

- [ ] **Step 1: User gate.** State the expected cost (the original 7 plus the new scenarios; feasible ones about 3-5 calls, infeasible ones 2-8; a few cents) and ask for approval.
- [ ] **Step 2: Run the evaluation on the test database** with the shortlist, then, if the user approves a second run, with `--no-shortlist` for comparison. Show both summaries.
- [ ] **Step 3: Compare with the design's success criteria** (design 5.9): first-attempt pass rate of the original feasible scenarios at least 6/6; total calls per feasible run at most 5; each infeasible scenario within 8 calls (empty fridge within 3) with the expected reason and next actions; uncached cost and average latency below $0.064 and 11.3 s on the original 7; no call over the budget; every new scenario as expected. Report each line as met or not.
- [ ] **Step 4: Tune if needed.** Adjust `MODEL_CALL_LIMIT`, `TOOL_CALL_LIMIT`, or the shortlist constants only with a stated reason from the run; a change needs one confirming run (user approval again).
- [ ] **Step 5: Record the result** in `docs/phase-1-carryover.md` under the baseline entry.
- [ ] **Step 6: User commit**

```bash
git add backend docs/phase-1-carryover.md
git commit -m "docs: plan 2a evaluation result against the plan 1 baseline"
```

- [ ] **Step 7: Checkpoint.** The user plans a real meal on their own instance (after `alembic upgrade head` for `0007` and a backend restart) and notes anything that feels wrong; findings go into `docs/phase-1-carryover.md` for plan 2b.
