# Phase 0: Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the database, multi-model call layer, recipe import and review flow, management pages, and extraction evaluation, then import 30-50 real recipes.

**Architecture:** A FastAPI backend and a Next.js frontend live in one repository, with PostgreSQL + pgvector in Docker. Every model call goes through one `LLMClient`, which handles task-based model selection, failover, hooks, the monthly cap, and `llm_calls`. Extraction results stay in the browser until the user confirms, and only then are recipes written.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 (sync), Alembic, psycopg 3, pgvector, Pydantic 2, LangChain `init_chat_model`, trafilatura, pytest, respx; Next.js 16, next-intl 4, Vitest, Testing Library; Docker Compose; GitHub Actions.

**Spec:** `docs/design.md`. Read it with this plan. Task numbers match T0.1-T0.9 in its Section 11.

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository or a remote. Each "User commit" step gives the exact commands, then stops until the user confirms. Read-only Git commands may run directly. This overrides any skill step.
- **Python:** `$PY` in this plan means the value of `python.defaultInterpreterPath` in `.vscode/settings.json`, read each time. Never copy that path into a file.
- **Packages:** do not install Python packages. A "User gate" step asks the user to run the install. If another package is needed, stop and tell the user which one and why. npm packages may be installed.
- **Secrets:** never read, print, create, edit, or delete `.env`, and never print secret values. The user manages it. Code gets its configuration from environment variables. Tests never load `.env`. Running the app, scripts, and database queries is allowed.
- **Language:** every repository file is in English. Chinese is allowed only in UI translations, dictionary names and aliases, and recipe text and its expected results in tests and prompt examples.
- **Data:** no evaluation data or real recipe data enters Git. It stays in the local database or `data/`. Test recipes are fictional and hand-written.
- **Single user:** no accounts, login, or `user_id`. Bind the backend and the database port to `127.0.0.1`.
- **Allergens:** the fixed list is `crustaceans`, `shellfish`, `fish`, `egg`, `milk`, `peanut`, `tree nut`, `soy`, `wheat`, `sesame`. `allergens[]` empty means confirmed none and null means unknown. A missing value never counts as "contains no allergens". Seed entries are never null.
- **Normalization:** map only wordings or forms of the same ingredient (葱花 → 葱, 生抽 → 生抽, never 生抽 → 酱油). The hierarchy has one level.
- **Amounts:** the model must not replace "a little" with an estimated amount.
- **Storage:** store structured data, the step summary, and the link, never images or source text. `extraction_logs` is the only exception: it is private, local, capped, never shown in the UI.
- **Embeddings:** `gemini-embedding-001` at 768 dimensions, with no failover. Failed calls are retried. A failed call never loses a recipe.
- **Models:** Gemini primary with OpenAI fallback for extraction, screenshots, and allergen suggestions. DeepSeek primary with OpenAI fallback for planning. Verify model IDs and prices in official documentation, and record the date in the config.
- **Tests:** tests never call a real model or the network. Real calls happen only in `minemydinner.llm.smoke`, the real app, and the evaluation flow.

## Review Focus

These inputs have no line in the spec but are likely to bite. Each has a test in the task named.

- **A model invents an amount:** "少量葱花" comes back as 5 g. The review form shows no invented number, and `extraction_logs.model_output` still holds the raw value so the fabrication rate stays measurable. (Task 4)
- **A batch file is GBK-encoded, has a BOM, is too large, or fails on one item:** GBK and BOM files read correctly, oversized files get a clear error, and one failed item does not lose the others. (Task 5)
- **A provider is down or the cap is reached during import or review:** the pasted input stays on screen with a clear message. A failed allergen suggestion does not block saving, and a failed embedding does not lose the recipe. (Tasks 5, 6)
- **A user-created dictionary name collides with an existing name or alias:** the API returns 409, so one text can never match two entries. (Task 6)
- **The user leaves the review page or clicks save twice:** leaving warns that paid extractions will be lost, and a double click saves one recipe. (Task 6)

## File Map

```
docker-compose.yml, docker/init-test-db.sql, .env.example, .github/workflows/ci.yml
backend/
  pyproject.toml, alembic.ini, migrations/
  config/models.yaml
  seed/ingredients.json, cookware.json, always_in_stock.json
  minemydinner/
    settings.py, db.py, main.py, constants.py, seed.py
    models/recipes.py, people.py, planning.py     ORM, grouped as in design 5.3
    llm/config.py, registry.py, client.py, smoke.py
    extraction/schemas.py, postprocess.py, normalize.py, extractor.py, allergen_suggest.py, prompts/extract_v1.md
    imports/web.py, batch.py, service.py
    recipes/service.py, embedding.py, allergy.py
    dictionary/service.py
    api/deps.py, imports.py, recipes.py, dictionary.py, inventory.py, diners.py
    evaluation/metrics.py, export.py, run.py
  tests/
frontend/
  messages/en.json, zh.json
  src/i18n/, src/lib/api.ts, src/lib/drafts.tsx
  src/app/[locale]/import, review, library, inventory, people, dictionary
```

---

### Task 1 (T0.1): Repository Skeleton

**Files:**
- Create: `docker-compose.yml`, `docker/init-test-db.sql`, `.env.example`, `.github/workflows/ci.yml`
- Create: `backend/pyproject.toml`, `backend/minemydinner/{__init__,settings,db,main}.py`, `backend/tests/conftest.py`, `backend/tests/test_skeleton.py`
- Create: `frontend/` (Next.js app with next-intl)
- Modify: `.gitignore`

**Interfaces:**
- Produces: `settings.get_settings() -> Settings` with `database_url`, `llm_monthly_cap_usd: Decimal`, `extraction_log_max_rows: int = 500`.
- Produces: `db.get_session() -> Iterator[Session]`, `db.SessionFactory`, `main.app`.
- Produces: the pytest fixture `session` (a transaction rolled back after each test) and `client` (`TestClient` using that session).

- [ ] **Step 1: User gate: start the branch and confirm the repository**

Ask the user to run the commands below and to confirm that `yutingwang999/mine-my-dinner` is private on GitHub. Stop until they confirm.

```bash
git switch -c feat/phase-0-data-foundation
```

- [ ] **Step 2: Write `backend/pyproject.toml`, then the user installs**

Name `minemydinner`, `requires-python = ">=3.13,<3.14"`. Dependencies: `fastapi>=0.128`, `uvicorn>=0.40`, `sqlalchemy>=2.0,<2.2`, `alembic>=1.18`, `psycopg[binary]>=3.3`, `pgvector>=0.4`, `pydantic>=2.12`, `pydantic-settings>=2.12`, `pyyaml>=6.0`, `httpx>=0.28`, `python-multipart>=0.0.22`, `trafilatura>=2.3`, `langchain>=1.4`, `langchain-google-genai>=4.4`, `langchain-openai>=1.4`, `langchain-deepseek>=1.1`. Extra `dev`: `pytest>=9`, `respx>=0.23`. Pytest `testpaths = ["tests"]` and `pythonpath = ["."]`, so local runs from `backend/` need no install of the project itself. Setuptools finds packages with `include = ["minemydinner*"]` and ships `extraction/prompts/*.md`.

Tell the user the project needs a dedicated environment, because the original one is shared with other projects and the install would upgrade its `openai` package. They create it and point `python.defaultInterpreterPath` at it. Stop until they confirm:

```bash
"$PY" -m pip install -e "backend[dev]"
```

- [ ] **Step 3: Write the failing tests in `backend/tests/test_skeleton.py`**

```python
def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}

def test_vector_extension_available(session):
    assert session.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).scalar() == "vector"

@pytest.mark.parametrize("path", [".env", "data/x.json", "docs/private/x.md", "private/x.py",
                                  "frontend/node_modules/x", "frontend/.next/x"])
def test_sensitive_paths_are_ignored(path):
    assert subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO_ROOT).returncode == 0

def test_env_example_is_not_ignored():
    assert subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=REPO_ROOT).returncode == 1
```

- [ ] **Step 4: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_skeleton.py -v`
Expected: FAIL (no app or fixtures).

- [ ] **Step 5: Implement the backend skeleton and infrastructure**

- `docker-compose.yml`: service `db`, image `pgvector/pgvector:pg16`, `container_name: minemydinner-db`, `POSTGRES_USER=postgres`, a local-only `POSTGRES_PASSWORD=postgres`, `POSTGRES_DB=minemydinner`, port `127.0.0.1:15433:5432`, a named volume, and `docker/init-test-db.sql` mounted to create `minemydinner_test` and the `vector` extension in both databases.
- `.env.example`: `DATABASE_URL`, `TEST_DATABASE_URL`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` (empty), `LLM_MONTHLY_CAP_USD=20`, `EXTRACTION_LOG_MAX_ROWS=500`. `$20` is a starting value; the user can change it.
- `.gitignore`: append `node_modules/` and `.next/`, with an English comment.
- `settings.py`: defaults point at the local Docker database, so tests need no `.env`. The test fixtures build `Settings(_env_file=None)`.
- `.github/workflows/ci.yml`: on pull requests to `main`, one job with a `pgvector/pgvector:pg16` service, Python 3.13, `pip install -e "backend[dev]"`, `TEST_DATABASE_URL` pointing at the service, and `pytest`; one job with Node 22, `npm ci`, `npm run lint`, `npm test`, `npm run build`. CI receives no API keys and no evaluation data.

- [ ] **Step 6: User gate: create `.env` and start the database**

```bash
cp .env.example .env
docker compose up -d db
```

- [ ] **Step 7: Run the backend tests**

Run: `cd backend && "$PY" -m pytest tests/test_skeleton.py -v`
Expected: PASS.

- [ ] **Step 8: Create the frontend**

Run `npx create-next-app@16 frontend --yes --use-npm --disable-git --ts --app --src-dir --eslint --no-tailwind --import-alias "@/*"`, then install `next-intl@4` and, as dev dependencies, `vitest`, `@vitejs/plugin-react`, `jsdom`, `@testing-library/react`, `@testing-library/user-event`. Add an `npm test` script (`vitest run`). Configure locales `zh` (default) and `en` with `/zh` and `/en` routes per the next-intl documentation for Next 16, a language switcher, and a rewrite from `/api/*` to `BACKEND_URL` (default `http://127.0.0.1:8000`). Add `frontend/src/i18n/messages.test.ts`:

```ts
it("zh and en define the same message keys", () => {
  expect(flatKeys(zh).sort()).toEqual(flatKeys(en).sort());
});
```

- [ ] **Step 9: Verify the frontend**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: all pass.

- [ ] **Step 10: User commit**

```bash
git add .gitignore .env.example .github docker docker-compose.yml backend frontend docs CLAUDE.md
git commit -m "chore: add repository skeleton with database and CI"
```

---

### Task 2 (T0.2): Database Tables, Migrations, and Seed Data

**Files:**
- Create: `backend/minemydinner/constants.py`, `backend/minemydinner/models/{__init__,recipes,people,planning}.py`, `backend/alembic.ini`, `backend/migrations/`, `backend/minemydinner/seed.py`, `backend/seed/*.json`
- Test: `backend/tests/test_schema.py`, `backend/tests/test_seed.py`

**Interfaces:**
- Produces: `constants.py` with `ALLERGENS`, `CATEGORIES`, `DISH_TYPES`, `STOCK_MODES`, `AMOUNT_TYPES`, `ROLES`, `TIME_SOURCES`, `UNITS = ("g", "ml", "piece", "stalk", "spoon")`, `SOURCE_TYPES = ("text", "web", "screenshot", "batch", "manual")`, `EMBEDDING_DIMENSION = 768`. Values are verbatim from design 5.3, such as `"tree nut"`, `"staple food"`.
- Produces: ORM classes `Ingredient`, `Cookware`, `Recipe`, `RecipeIngredient`, `RecipeCookware`, `Diner`, `Inventory`, `CookwareOwned`, `AlwaysInStock`, `DietaryRule`, `Rating`, `MealPlan`, `MealPlanItem`, `ShoppingList`, `ExtractionLog`, `LlmCall`, with the fields in design 5.3. Add `extraction_logs.prompt_version` and `created_at`.
- Produces: `seed.seed_all(session) -> None`, `seed.ensure_self_diner(session) -> Diner`; command `python -m minemydinner.seed`.

- [ ] **Step 1: Write the failing schema tests in `test_schema.py`**

```python
def test_seed_entry_rejects_null_allergens(session):
    session.add(Ingredient(name_zh="测试", name_en="test", category="other", stock_mode="level", allergens=None, source="seed"))
    with pytest.raises(IntegrityError): session.flush()

def test_user_entry_allows_null_and_empty_allergens(session):
    for value in (None, []):
        session.add(Ingredient(name_zh=f"酱{value}", category="other", stock_mode="level", allergens=value, source="user"))
    session.flush()

def test_allergens_reject_values_outside_the_list(session):
    session.add(Ingredient(name_zh="测试", category="other", stock_mode="level", allergens=["shrimp"], source="user"))
    with pytest.raises(IntegrityError): session.flush()

def test_dietary_rule_needs_exactly_one_target(session, self_diner):
    session.add(DietaryRule(diner_id=self_diner.id, rule_type="allergy", allergen="egg", text="x"))
    with pytest.raises(IntegrityError): session.flush()

def test_allergy_rule_rejects_free_text(session, self_diner):
    session.add(DietaryRule(diner_id=self_diner.id, rule_type="allergy", text="虾"))
    with pytest.raises(IntegrityError): session.flush()

def test_only_one_self_diner(session, self_diner):
    session.add(Diner(display_name="Other", is_self=True))
    with pytest.raises(IntegrityError): session.flush()

def test_deleting_recipe_keeps_plan_item_title_and_variant(session, recipe_in_plan_with_variant):
    recipe, item, variant = recipe_in_plan_with_variant
    session.delete(recipe); session.flush(); session.expire_all()
    assert item.recipe_id is None and item.recipe_title == recipe.title
    assert variant.parent_recipe_id is None

def test_embedding_dimension_and_hnsw_index(session):
    assert session.execute(text("SELECT indexdef FROM pg_indexes WHERE tablename='recipes' AND indexdef ILIKE '%hnsw%'")).scalar()
    assert Recipe.__table__.c.embedding.type.dim == 768
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_schema.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement the models and the first migration**

Use `Text` columns with CHECK constraints, not PostgreSQL enums. Required constraints: `allergens <@ ALLERGENS`; seed entries non-null with both names; at least one name on any entry; `num_nonnulls(allergen, ingredient_id, text) = 1`; allergy rules have null `text`; a partial unique index for `is_self`; `time_source` is null when `time_min` is null. Foreign keys: `meal_plan_items.recipe_id` and `recipes.parent_recipe_id` use `ON DELETE SET NULL`; `recipe_ingredients`, `recipe_cookware`, `ratings`, and `dietary_rules` use `ON DELETE CASCADE`; `recipe_ingredients.ingredient_id` uses `ON DELETE SET NULL`; `inventory`, `always_in_stock`, `cookware_owned`, and `recipe_cookware` cascade from the dictionary; `dietary_rules.ingredient_id` and `ingredients.parent_id` use `ON DELETE RESTRICT`, so deleting a dictionary entry cannot silently remove an allergy rule. Add an HNSW index with `vector_cosine_ops`. The test fixture runs `alembic upgrade head` on the test database once per session.

- [ ] **Step 4: Run the schema tests**

Run: `cd backend && "$PY" -m pytest tests/test_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing seed tests in `test_seed.py`**

```python
def test_dictionary_size_and_required_fields(ingredients_json):
    assert 180 <= len(ingredients_json) <= 220
    for e in ingredients_json:
        assert e["name_zh"] and e["name_en"] and e["category"] in CATEGORIES and e["stock_mode"] in STOCK_MODES
        assert e["allergens"] is not None and set(e["allergens"]) <= set(ALLERGENS)

def test_no_name_or_alias_maps_to_two_entries(ingredients_json):
    assert_no_duplicate_terms(ingredients_json)

def test_hierarchy_has_one_level_and_design_examples(session_seeded):
    assert {c.name_zh for c in by_zh(session_seeded, "酱油").children} >= {"生抽", "老抽"}
    assert by_zh(session_seeded, "五花肉").parent.name_zh == "猪肉"
    assert session_seeded.scalar(select(func.count()).select_from(child_with_child_query())) == 0

def test_soy_sauce_kinds_are_not_aliases(session_seeded):
    assert "生抽" not in by_zh(session_seeded, "酱油").aliases_zh
    assert "葱花" in by_zh(session_seeded, "葱").aliases_zh

def test_design_allergen_tags(session_seeded):
    assert "crustaceans" in by_zh(session_seeded, "海米").allergens
    assert "shellfish" in by_zh(session_seeded, "蚝油").allergens
    assert {"soy", "wheat"} <= set(by_zh(session_seeded, "酱油").allergens)

def test_always_in_stock_defaults(session_seeded):
    assert {"盐", "食用油", "水", "糖", "酱油", "醋"} <= always_in_stock_names(session_seeded)

def test_cookware_alias(session_seeded):
    assert "wok" in by_zh_cookware(session_seeded, "炒锅").aliases_en

def test_reseeding_keeps_user_data(session_seeded):
    user_entry = add_user_ingredient(session_seeded, "秘制酱")
    remove_always_in_stock(session_seeded, "醋")
    seed_all(session_seeded)
    assert by_zh(session_seeded, "秘制酱").id == user_entry.id
    assert "醋" not in always_in_stock_names(session_seeded)
    assert session_seeded.scalar(select(func.count()).where(Diner.is_self)) == 1
```

- [ ] **Step 6: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_seed.py -v`
Expected: FAIL.

- [ ] **Step 7: Write the seed data and `seed.py`**

Write about 200 ingredients with Chinese and English names, aliases, category, `stock_mode`, `allergens`, and `parent` (by `name_zh`), plus common cookware and the always-in-stock list. `seed_all` upserts rows with `source = 'seed'`, keyed by `name_zh`, and never touches `source = 'user'` rows. It fills `always_in_stock` only on the run that first creates seed ingredients, so removed items are not restored.

- [ ] **Step 8: Run the seed tests**

Run: `cd backend && "$PY" -m pytest tests/test_seed.py -v`
Expected: PASS.

- [ ] **Step 9: User gate: review the allergen tags**

Allergy filtering depends on this data, and the design requires a person to check it. Ask the user to review `backend/seed/ingredients.json`, focusing on `allergens` for sauces and processed foods, and on `parent`. Apply their corrections, rerun Step 8, and stop until they confirm.

- [ ] **Step 10: User commit**

```bash
git add backend
git commit -m "feat: add database schema, migrations, and dictionary seed data"
```

---

### Task 3 (T0.3): Multi-Model Call Layer

**Files:**
- Create: `backend/config/models.yaml`, `backend/minemydinner/llm/{__init__,config,registry,client,smoke}.py`
- Test: `backend/tests/fakes.py`, `backend/tests/test_llm_config.py`, `backend/tests/test_llm_client.py`

**Interfaces:**
- Produces: `config.load_llm_config(path: Path) -> LLMConfig`, with `models` and `tasks`. A task has `primary`, `fallback: str | None`, `requires: list[str]`, `essential: bool`, `timeout_seconds: int`.
- Produces: `registry.register_provider(name: str, builder: Callable[[ModelConfig, int], BaseChatModel]) -> None`, `registry.register_embedding_provider(name, builder) -> None`.
- Produces: `TextHook = Callable[[str], str]`.
- Produces: `StructuredResult[T]` with `parsed: T`, `model_name: str`, `fallback_used: bool`.
- Produces: `client.LLMClient(config, session_factory, monthly_cap_usd: Decimal, pre_hooks: Sequence[TextHook] = (), post_hooks: Sequence[TextHook] = ())` with:
  - `invoke_structured(task: str, messages: list[BaseMessage], schema: type[T]) -> StructuredResult[T]`
  - `embed(texts: list[str], task: str = "embeddings") -> list[list[float]]`
- Produces: exceptions `MonthlyCapExceeded`, `AllModelsFailed`, `ConfigError`.
- Later: T1.4 fills in `pre_hooks` and `post_hooks` for nickname replacement and restoration. T1.1 adds tool-calling invocation to `LLMClient`.

Config rules: tasks are `extraction`, `image_understanding`, `allergen_suggestions`, `planning`, `embeddings`, and `smoke`. All are non-essential in Phase 0 except `planning`, which is essential for Phase 1. Non-essential calls stop at the cap. Each model has `provider`, `model`, `capabilities`, `input_usd_per_mtok`, `output_usd_per_mtok`. The file has `prices_updated`. Capabilities use the names from design 3.1.

- [ ] **Step 1: Write the failing tests**

`test_llm_config.py`:

```python
def test_rejects_model_without_required_capability(tmp_config):
    with pytest.raises(ConfigError): load_llm_config(tmp_config(primary="deepseek", requires=["strict_json"]))

def test_rejects_model_without_price(tmp_config):
    with pytest.raises(ConfigError): load_llm_config(tmp_config(drop_price="gemini"))

def test_embeddings_cannot_have_a_fallback(tmp_config):
    with pytest.raises(ConfigError): load_llm_config(tmp_config(task="embeddings", fallback="openai"))

def test_repository_config_matches_design():
    cfg = load_llm_config(REPO_CONFIG)
    assert provider_of(cfg, "extraction", "primary") == "google_genai"
    assert provider_of(cfg, "extraction", "fallback") == "openai"
    assert provider_of(cfg, "planning", "primary") == "deepseek"
    assert cfg.tasks["embeddings"].fallback is None
    assert cfg.models[cfg.tasks["embeddings"].primary].model == "gemini-embedding-001"
```

`test_llm_client.py`, using a `FakeChatModel` registered through `register_provider`:

```python
def test_primary_success_logs_one_call(llm, session):
    result = llm.invoke_structured("extraction", MESSAGES, Schema)
    assert result.fallback_used is False
    call = only_call(session)
    assert (call.task, call.success, call.fallback_used) == ("extraction", True, False)
    assert call.input_tokens == 10 and call.latency_ms is not None and call.cost_estimate > 0

def test_failure_switches_to_fallback_and_logs_both(llm_primary_fails, session):
    assert llm_primary_fails.invoke_structured("extraction", MESSAGES, Schema).fallback_used is True
    first, second = calls(session)
    assert first.success is False and second.fallback_used is True

def test_schema_parse_failure_also_fails_over(llm_primary_bad_json):
    assert llm_primary_bad_json.invoke_structured("extraction", MESSAGES, Schema).fallback_used is True

def test_all_models_failing_raises_and_is_logged(llm_all_fail, session):
    with pytest.raises(AllModelsFailed): llm_all_fail.invoke_structured("extraction", MESSAGES, Schema)
    assert len(calls(session)) == 2

def test_monthly_cap_stops_non_essential_without_calling_model(llm_over_cap, primary_fake):
    with pytest.raises(MonthlyCapExceeded): llm_over_cap.invoke_structured("extraction", MESSAGES, Schema)
    assert primary_fake.call_count == 0

def test_monthly_cap_allows_essential(llm_over_cap):
    assert llm_over_cap.invoke_structured("planning", MESSAGES, Schema).parsed

def test_cap_counts_only_the_current_utc_month(llm_with_last_month_spend):
    assert llm_with_last_month_spend.invoke_structured("extraction", MESSAGES, Schema).parsed

def test_pre_hook_reaches_primary_and_fallback(llm_hooked_primary_fails, primary_fake, fallback_fake):
    llm_hooked_primary_fails.invoke_structured("extraction", [HumanMessage("小李 eats")], Schema)
    assert "Diner 1" in primary_fake.last_text and "Diner 1" in fallback_fake.last_text

def test_pre_hook_keeps_image_parts(llm_hooked, primary_fake):
    llm_hooked.invoke_structured("image_understanding", [IMAGE_AND_TEXT_MESSAGE], Schema)
    assert primary_fake.last_image_url == IMAGE_URL

def test_post_hook_restores_string_fields_in_nested_results(llm_hooked):
    result = llm_hooked.invoke_structured("extraction", MESSAGES, NestedSchema)
    assert result.parsed.items[0].note == "小李 eats"

def test_new_provider_needs_only_registration(tmp_config, session_factory):
    register_provider("qwen_local", lambda model, timeout: FakeChatModel())
    assert client_for(tmp_config(primary_provider="qwen_local"), session_factory).invoke_structured("extraction", MESSAGES, Schema).parsed

def test_embed_returns_768_and_has_no_failover(llm_embed_fails, session):
    with pytest.raises(AllModelsFailed): llm_embed_fails.embed(["x"])
    assert len(calls(session)) == 1

def test_embed_rejects_wrong_dimension(llm_embed_wrong_size):
    with pytest.raises(AllModelsFailed): llm_embed_wrong_size.embed(["x"])

def test_call_log_survives_business_rollback(llm, session_factory):
    with session_factory() as business:
        llm.invoke_structured("extraction", MESSAGES, Schema); business.rollback()
    assert count_calls(session_factory) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_llm_config.py tests/test_llm_client.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement the layer**

- `registry.py`: the default builder for `google_genai`, `openai`, and `deepseek` calls `init_chat_model(model, model_provider=provider, timeout=timeout)`. The default Gemini embedding builder requests 768 dimensions; check the parameter name in the current `langchain-google-genai` documentation.
- `client.py`: call `model.with_structured_output(schema, include_raw=True).invoke(messages)`. A non-null `parsing_error` counts as failure. Read tokens from `raw.usage_metadata`. Pre-hooks run on each text part of each message and on embedding text. Post-hooks run recursively on every string in the `model_dump()` result, which is then revalidated.
- Write each `llm_calls` row through a new session from `session_factory`, committed immediately. Check the cap against `sum(cost_estimate)` for the current UTC month before any model is called. `MonthlyCapExceeded` never triggers failover. Estimate embedding tokens as `len(text) // 4`, because the API returns no usage.
- `models.yaml`: look up current model IDs and prices in the official documentation, and write them with `prices_updated` set to that date.

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_llm_config.py tests/test_llm_client.py -v`
Expected: PASS.

- [ ] **Step 5: Write `llm/smoke.py`**

`python -m minemydinner.llm.smoke` makes one small call to each configured model and one embedding, then prints the provider, success, latency, cost, and vector length, and exits non-zero on any failure. It prints no key values. Tests do not run it.

- [ ] **Step 6: Real call check**

User gate first: ask the user to put API keys in `.env`, set a budget alert with each provider (design Section 3), and confirm that the small cost is acceptable. Then run the command below. Expected: every line succeeds and the vector length is 768.

```bash
cd backend && "$PY" -m minemydinner.llm.smoke
```

- [ ] **Step 7: User commit**

```bash
git add backend
git commit -m "feat: add multi-model call layer with failover and monthly cap"
```

---

### Task 4 (T0.4): Extraction Format and Normalization

**Files:**
- Create: `backend/minemydinner/extraction/{__init__,schemas,postprocess,normalize,extractor}.py`, `backend/minemydinner/extraction/prompts/extract_v1.md`
- Test: `backend/tests/test_postprocess.py`, `backend/tests/test_normalize.py`, `backend/tests/test_extractor.py`

**Interfaces:**
- Consumes: `LLMClient.invoke_structured`; `Ingredient`, `Cookware`.
- Produces in `schemas.py`:
  - `ExtractedIngredient(raw, name, amount: float | None, unit: Literal[*UNITS] | None, amount_type, role, optional: bool = False)`
  - `ExtractedRecipe(lang: Literal["zh", "en"], title, dish_type, time_min: int | None, time_source: Literal["stated", "estimated"] | None, servings: int | None, tags: list[str], steps_summary: str | None, ingredients: list[ExtractedIngredient], cookware: list[str])`
- Produces: `postprocess.enforce_amount_rules(recipe: ExtractedRecipe) -> ExtractedRecipe`.
- Produces in `normalize.py`:
  - `match_ingredient(session, name: str) -> Ingredient | None`
  - `match_cookware(session, name: str) -> Cookware | None`
  - `find_term_owner(session, table, term: str) -> Row | None`, used for collision checks in Task 6
- Produces: `extractor.PROMPT_VERSION = "extract_v1"`, and:
  - `extract_from_text(client: LLMClient, text: str) -> ExtractionResult`
  - `extract_from_images(client, images: list[ImageInput]) -> ExtractionResult`
  - `ExtractionResult(recipe: ExtractedRecipe, raw_output: dict, model_name: str, prompt_version: str)`, where `raw_output` is the value **before** `enforce_amount_rules`
  - `ImageInput(mime_type: str, data: bytes)`

- [ ] **Step 1: Write the failing tests**

```python
# test_postprocess.py
@pytest.mark.parametrize("raw,name,expected_type", [
    ("少量葱花", "葱花", "vague"),
    ("芹菜", "芹菜", "missing"),
    ("a little salt", "salt", "vague"),
])
def test_invented_amount_is_removed(raw, name, expected_type):
    line = enforce(raw=raw, name=name, amount=5, unit="g", amount_type="exact")
    assert (line.amount, line.unit, line.amount_type) == (None, None, expected_type)

@pytest.mark.parametrize("raw,amount", [("2个土豆", 2), ("两个土豆", 2), ("半斤五花肉", 0.5), ("1/2 cup milk", 0.5)])
def test_amount_stated_in_the_source_is_kept(raw, amount):
    assert enforce(raw=raw, name="x", amount=amount, unit="piece", amount_type="count").amount == amount

@pytest.mark.parametrize("amount_type", ["vague", "missing"])
def test_vague_and_missing_never_keep_a_number(amount_type):
    assert enforce(raw="2个少量", name="x", amount=2, unit="piece", amount_type=amount_type).amount is None

def test_stated_time_without_time_min_is_cleared():
    assert enforce_amount_rules(recipe_with(time_min=None, time_source="stated")).time_source is None

# test_normalize.py
@pytest.mark.parametrize("name,expected", [("葱花", "葱"), ("生抽", "生抽"), ("  老抽 ", "老抽"), ("Scallion", "葱"), ("SOY SAUCE", "酱油")])
def test_matches_names_and_aliases(session_seeded, name, expected):
    assert match_ingredient(session_seeded, name).name_zh == expected

def test_unknown_name_is_not_guessed(session_seeded):
    assert match_ingredient(session_seeded, "秘制酱") is None

def test_user_entries_are_matched(session_seeded):
    add_user_ingredient(session_seeded, "秘制酱")
    assert match_ingredient(session_seeded, "秘制酱").source == "user"

def test_cookware_alias(session_seeded):
    assert match_cookware(session_seeded, "wok").name_zh == "炒锅"
    assert match_cookware(session_seeded, "空气炸锅X") is None

# test_extractor.py
def test_raw_output_keeps_invented_amount_but_recipe_does_not(fake_llm_returning_invented_amount):
    result = extract_from_text(fake_llm_returning_invented_amount, "少量葱花")
    assert result.raw_output["ingredients"][0]["amount"] == 5
    assert result.recipe.ingredients[0].amount is None

def test_prompt_contains_version_rules_and_input(fake_llm):
    extract_from_text(fake_llm, "番茄炒蛋")
    assert "番茄炒蛋" in fake_llm.last_text and "must not" in fake_llm.last_system_text

def test_images_use_image_task_and_pass_image_parts(fake_llm):
    extract_from_images(fake_llm, [ImageInput("image/png", b"x")])
    assert fake_llm.last_task == "image_understanding" and fake_llm.last_has_image
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_postprocess.py tests/test_normalize.py tests/test_extractor.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement**

`enforce_amount_rules` algorithm: clear `time_source` when `time_min` is null. For each line, if `amount_type` is `vague` or `missing`, clear `amount` and `unit`. Otherwise, if `raw` holds no digit and no Chinese numeral (一二两三四五六七八九十半), clear them and set the type to `missing` when `raw` equals `name` after trimming, else `vague`.

`match_*`: trim, then compare case-insensitively with exact equality against names and aliases. Never use fuzzy matching.

`extract_v1.md` states these rules: keep `raw` verbatim; do not estimate amounts; do not fold a specific kind into a broader one; apply the `dish_type` rules from design 5.3; use `estimated` only for time the source does not state; `steps_summary` is a short paraphrase. It has hand-written fictional Chinese and English examples.

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_postprocess.py tests/test_normalize.py tests/test_extractor.py -v`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: add extraction schema, amount rules, and dictionary normalization"
```

---

### Task 5 (T0.5): Import Sources

**Files:**
- Create: `backend/minemydinner/imports/{__init__,web,batch,service}.py`, `backend/minemydinner/api/{deps,imports}.py`
- Test: `backend/tests/test_web_import.py`, `backend/tests/test_batch.py`, `backend/tests/test_imports_api.py`

**Interfaces:**
- Consumes: `extract_from_text`, `extract_from_images`, `match_ingredient`, `match_cookware`.
- Produces in `web.py`: `fetch_page_text(url: str) -> str`, which raises `WebImportError(reason)` with `reason` in `invalid_url`, `blocked_address`, `robots_disallowed`, `fetch_failed`, `not_html`, `too_large`, `no_main_text`.
- Produces in `batch.py`: `split_batch(data: bytes) -> list[str]`, which raises `BatchFileError(reason)`.
- Produces in `service.py`: `RecipeDraft`, with the `ExtractedRecipe` fields, `ingredients[].ingredient_id: int | None`, `cookware: list[{name, cookware_id: int | None}]`, `source_type`, `source_url`, and `provenance: {input_text, model_output, model_name, prompt_version}`.
- Produces API routes, none of which write recipes:
  - `POST /api/imports/text {text}` -> `RecipeDraft`. If the whole trimmed text is one URL, it imports that URL.
  - `POST /api/imports/url {url}` -> `RecipeDraft`
  - `POST /api/imports/screenshots` (multipart, 1-5 images of one dish) -> `RecipeDraft`
  - `POST /api/imports/batch` (multipart file) -> `{items: [{index, draft | null, error | null}]}`
- Errors: `{"code": str, "suggest_paste": bool}`. `MonthlyCapExceeded` returns 429 with code `monthly_cap`. `AllModelsFailed` returns 503 with code `models_unavailable`.

Limits: web 15 s and 2 MB; `no_main_text` below 200 characters; screenshots PNG, JPEG, or WebP up to 10 MB each; batch 1 MB and 50 items. Screenshot `provenance.input_text` is the literal `[screenshot]`, and image bytes are never stored. The user agent is an honest `MineMyDinnerBot/0.1`, with no retries that evade a block.

- [ ] **Step 1: Write the failing tests**

```python
# test_web_import.py, using respx
def test_extracts_main_text(mock_site):
    assert "番茄炒蛋的做法" in fetch_page_text(mock_site.url)

def test_robots_disallow_stops_before_fetching_page(mock_site_disallowed):
    assert_web_error(mock_site_disallowed.url, "robots_disallowed")
    assert mock_site_disallowed.page_route.called is False

def test_robots_404_allows_and_robots_500_blocks(mock_site_robots_404, mock_site_robots_500):
    assert fetch_page_text(mock_site_robots_404.url)
    assert_web_error(mock_site_robots_500.url, "fetch_failed")

@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://localhost/x", "http://10.0.0.1/x", "http://169.254.169.254/x", "http://[::1]/x"])
def test_private_addresses_are_blocked(url):
    assert_web_error(url, "blocked_address")

def test_redirect_to_private_address_is_blocked(mock_redirect_to_private):
    assert_web_error(mock_redirect_to_private.url, "blocked_address")

@pytest.mark.parametrize("url", ["ftp://x.com/a", "file:///etc/passwd", "not a url"])
def test_invalid_urls(url):
    assert_web_error(url, "invalid_url")

@pytest.mark.parametrize("fixture,reason", [("mock_pdf", "not_html"), ("mock_huge", "too_large"), ("mock_login_wall", "no_main_text"), ("mock_timeout", "fetch_failed")])
def test_failures_have_reasons(request, fixture, reason):
    assert_web_error(request.getfixturevalue(fixture).url, reason)

# test_batch.py
def test_splits_on_separator_line():
    assert split_batch("番茄炒蛋\n鸡蛋 番茄\n---\n土豆丝\n土豆".encode()) == ["番茄炒蛋\n鸡蛋 番茄", "土豆丝\n土豆"]

def test_without_separator_splits_on_blank_lines():
    assert split_batch("番茄炒蛋\n\n\n土豆丝".encode()) == ["番茄炒蛋", "土豆丝"]

@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030"])
def test_reads_common_encodings(encoding):
    assert split_batch("番茄炒蛋".encode(encoding)) == ["番茄炒蛋"]

def test_empty_and_oversized_files_are_rejected():
    assert_batch_error(b"  \n", "empty")
    assert_batch_error(b"x" * (1024 * 1024 + 1), "too_large")
    assert_batch_error(b"---\n".join([b"x"] * 51), "too_many_items")

# test_imports_api.py
def test_text_returns_draft_without_writing_a_recipe(client, session, fake_extraction):
    body = client.post("/api/imports/text", json={"text": "番茄炒蛋"}).json()
    assert body["source_type"] == "text" and body["provenance"]["input_text"] == "番茄炒蛋"
    assert count(session, Recipe) == 0 and count(session, ExtractionLog) == 0

def test_draft_marks_matched_and_unmatched(client, seeded, fake_extraction_with_unknown):
    lines = client.post("/api/imports/text", json={"text": "x"}).json()["ingredients"]
    assert ids_by_name(lines)["葱花"] is not None and ids_by_name(lines)["秘制酱"] is None

def test_pasted_single_url_is_imported_as_web(client, fake_extraction, mock_site):
    assert client.post("/api/imports/text", json={"text": f"  {mock_site.url} "}).json()["source_type"] == "web"

def test_web_failure_suggests_pasting(client, mock_login_wall):
    response = client.post("/api/imports/url", json={"url": mock_login_wall.url})
    assert response.status_code == 422 and response.json()["suggest_paste"] is True

def test_screenshot_type_size_and_no_storage(client, fake_extraction, session):
    assert post_images(client, [("a.gif", b"x", "image/gif")]).status_code == 415
    assert post_images(client, [("a.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")]).status_code == 413
    assert post_images(client, [("a.png", b"x", "image/png")] * 6).status_code == 422
    assert post_images(client, [("a.png", b"x", "image/png")]).json()["provenance"]["input_text"] == "[screenshot]"

def test_batch_keeps_other_items_when_one_fails(client, fake_extraction_fails_on_second):
    items = post_batch(client, "甲\n---\n乙\n---\n丙").json()["items"]
    assert [bool(i["draft"]) for i in items] == [True, False, True] and items[1]["error"]

def test_cap_and_outage_return_clear_errors(client, llm_over_cap, llm_down):
    assert post_text(client, llm_over_cap).json()["code"] == "monthly_cap"
    assert post_text(client, llm_down).json()["code"] == "models_unavailable"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_web_import.py tests/test_batch.py tests/test_imports_api.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`web.py`: parse the URL, resolve its host, and reject any non-global address. Follow redirects manually, repeating that check and the robots check on each hop. Parse `robots.txt` with `urllib.robotparser`: a 4xx response allows the fetch, and a 5xx or network error returns `fetch_failed`. Stream the body to enforce the size limit. Use `trafilatura.extract` for the main text. Add a `source_url` when text contains a URL: the first URL found.

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_web_import.py tests/test_batch.py tests/test_imports_api.py -v`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: add text, web, screenshot, and batch recipe import"
```

---

### Task 6 (T0.6): Review and Save

**Files:**
- Create: `backend/minemydinner/recipes/{__init__,service,embedding,allergy}.py`, `backend/minemydinner/dictionary/{__init__,service}.py`, `backend/minemydinner/extraction/allergen_suggest.py`, `backend/minemydinner/api/{recipes,dictionary}.py`
- Create: `frontend/src/lib/{api.ts,drafts.tsx}`, `frontend/src/app/[locale]/import/page.tsx`, `frontend/src/app/[locale]/review/page.tsx`, `frontend/src/components/{IngredientReviewRow,CookwareReviewRow,AllergenPicker}.tsx`
- Test: `backend/tests/test_recipe_save.py`, `test_allergy_check.py`, `test_dictionary_create.py`, `test_embedding.py`; `frontend/src/app/[locale]/review/review.test.tsx`

**Interfaces:**
- Consumes: `RecipeDraft`, `find_term_owner`, `LLMClient`.
- Produces in `recipes/service.py`:
  - `RecipeWrite`: the reviewed recipe, `ingredients[].ingredient_id: int | None`, `cookware_ids: list[int]`, `source_type`, `source_url`, `provenance | None`. The client sets `time_source = "user"` when the user changes time.
  - `create_recipe(session, client: LLMClient, data: RecipeWrite) -> Recipe`
  - `update_recipe(session, client, recipe_id, data) -> Recipe` and `delete_recipe(session, recipe_id) -> None` (routes in Task 7)
- Produces in `recipes/embedding.py`:
  - `embedding_text(recipe) -> str`: title, ingredient names, tags, and step summary
  - `try_embed_recipe(session, client, recipe) -> bool`: never raises for model errors
  - `retry_pending_embeddings(session, client) -> int`: runs at app startup and from `POST /api/recipes/embeddings/retry`
- Produces in `recipes/allergy.py`:
  - `allergen_info(ingredient: Ingredient) -> AllergenInfo(known: frozenset[str], unknown: bool)`: own plus parent's allergens; `unknown` is true when its own value is null
  - `find_allergy_conflicts(session, diner_ids: list[int], lines: list[LineRef]) -> list[AllergyConflict(diner_id, line_name, allergen | None, ingredient_id | None)]`
  - `find_unknown_allergen_lines(session, lines: list[LineRef]) -> list[str]`. T1.1 and T1.2 reuse these.
- Produces in `dictionary/service.py`:
  - `create_user_ingredient(session, data: IngredientWrite) -> Ingredient`, where `allergens` defaults to null; raises `TermConflict(term, owner_id)` and `HierarchyError`
  - `create_user_cookware(session, data: CookwareWrite) -> Cookware`
  - `link_unmatched_lines(session, ingredient: Ingredient) -> int`: links rows with an empty `ingredient_id` whose `name` exactly equals one of the entry's names or aliases
- Produces in `allergen_suggest.py`: `suggest_allergens(client, name: str) -> list[str] | None`: null when the model is unavailable or the cap is reached; values outside the list are discarded.
- Produces API routes:
  - `POST /api/recipes`
  - `POST /api/recipes/allergy-check {ingredient lines}` -> `{conflicts, unknown}` for the `is_self` diner
  - `GET /api/ingredients?q=`, `POST /api/ingredients`
  - `GET /api/cookware?q=`, `POST /api/cookware {…, owned: bool}`
  - `POST /api/allergen-suggestions {name}` -> `{suggested: list | null}`

- [ ] **Step 1: Write the failing backend tests**

```python
# test_recipe_save.py
def test_save_writes_recipe_lines_and_cookware(client, session, seeded, fake_llm, recipe_write):
    created = client.post("/api/recipes", json=recipe_write).json()
    recipe = session.get(Recipe, created["id"])
    assert recipe.source_url == recipe_write["source_url"] and recipe.embedding is not None
    assert {l.raw for l in recipe.ingredients} == {"少量葱花", "秘制酱"} and recipe.cookware

def test_save_keeps_unmatched_line_with_empty_id(client, session, seeded, fake_llm, recipe_write):
    assert any(l.ingredient_id is None for l in saved(client, session, recipe_write).ingredients)

def test_save_writes_log_with_raw_model_output_and_correction(client, session, seeded, fake_llm, recipe_write_with_invented_amount):
    client.post("/api/recipes", json=recipe_write_with_invented_amount)
    log = one(session, ExtractionLog)
    assert log.model_output["ingredients"][0]["amount"] == 5
    assert log.user_corrected["ingredients"][0]["amount"] is None and log.model_name

def test_manual_save_writes_no_log(client, session, seeded, fake_llm, recipe_write):
    client.post("/api/recipes", json={**recipe_write, "provenance": None, "source_type": "manual"})
    assert count(session, ExtractionLog) == 0

def test_log_stops_at_row_limit_but_recipe_saves(client, session, seeded, fake_llm, recipe_write, settings_max_logs_1):
    client.post("/api/recipes", json=recipe_write); client.post("/api/recipes", json=recipe_write)
    assert count(session, Recipe) == 2 and count(session, ExtractionLog) == 1

@pytest.mark.parametrize("mutation", [{"title": " "}, {"ingredients": []}, {"dish_type": "main"}, {"cookware_ids": [999999]}, {"time_min": None, "time_source": "stated"}])
def test_invalid_recipes_are_rejected(client, session, seeded, fake_llm, recipe_write, mutation):
    assert client.post("/api/recipes", json={**recipe_write, **mutation}).status_code == 422
    assert count(session, Recipe) == 0

# test_embedding.py
def test_failed_embedding_keeps_recipe_for_retry(client, session, seeded, llm_embed_down, recipe_write):
    assert saved(client, session, recipe_write).embedding is None

def test_cap_reached_keeps_recipe_for_retry(client, session, seeded, llm_over_cap, recipe_write):
    assert saved(client, session, recipe_write).embedding is None

def test_retry_fills_only_pending(session, fake_llm, recipe_pending, recipe_done):
    assert retry_pending_embeddings(session, fake_llm) == 1 and recipe_pending.embedding is not None

# test_allergy_check.py
def test_child_inherits_parent_allergens(seeded):
    assert "soy" in allergen_info(by_zh(seeded, "生抽")).known

def test_null_is_unknown_and_empty_is_confirmed_none(seeded):
    assert allergen_info(user_ingredient(seeded, allergens=None)).unknown is True
    assert allergen_info(user_ingredient(seeded, allergens=[])).unknown is False

def test_child_with_null_keeps_parents_known_and_is_unknown(seeded):
    info = allergen_info(user_ingredient(seeded, allergens=None, parent=by_zh(seeded, "酱油")))
    assert "soy" in info.known and info.unknown is True

def test_tag_rule_conflict_warns_and_does_not_block(client, seeded, self_allergy_crustaceans, fake_llm, recipe_write_with_haimi):
    assert client.post("/api/recipes/allergy-check", json=lines(recipe_write_with_haimi)).json()["conflicts"][0]["allergen"] == "crustaceans"
    assert client.post("/api/recipes", json=recipe_write_with_haimi).status_code == 201

def test_ingredient_rule_on_parent_covers_child(session, seeded, self_diner):
    add_rule(session, self_diner, rule_type="allergy", ingredient=by_zh(session, "猪肉"))
    assert find_allergy_conflicts(session, [self_diner.id], [line_of(by_zh(session, "五花肉"))])

def test_friend_rules_do_not_trigger_self_check(client, seeded, friend_allergy_crustaceans, recipe_write_with_haimi):
    assert client.post("/api/recipes/allergy-check", json=lines(recipe_write_with_haimi)).json()["conflicts"] == []

def test_unmatched_line_is_unknown_not_safe(client, seeded, recipe_write):
    assert "秘制酱" in client.post("/api/recipes/allergy-check", json=lines(recipe_write)).json()["unknown"]

# test_dictionary_create.py
def test_new_entry_defaults_to_unknown_and_user_source(client, session, seeded):
    entry = create_ingredient(client, name_zh="秘制酱")
    assert entry["allergens"] is None and entry["source"] == "user"

def test_explicit_empty_means_confirmed_none(client, seeded):
    assert create_ingredient(client, name_zh="秘制酱", allergens=[])["allergens"] == []

@pytest.mark.parametrize("term", ["葱花", "生抽", "SOY SAUCE"])
def test_name_or_alias_collision_returns_409(client, seeded, term):
    response = post_ingredient(client, name_zh="新词", aliases_en=[term])
    assert response.status_code == 409 and response.json()["owner_id"]

def test_parent_must_be_top_level(client, seeded):
    assert post_ingredient(client, name_zh="新词", parent_id=id_of(seeded, "生抽")).status_code == 422

def test_new_entry_links_exact_name_rows_only(session, seeded, recipe_with_lines_named):
    recipe_with_lines_named("秘制酱", "秘制酱汁")
    entry = create_user_ingredient(session, IngredientWrite(name_zh="秘制酱", category="other", stock_mode="level"))
    assert link_unmatched_lines(session, entry) == 1

def test_new_cookware_with_owned_adds_to_owned(client, session, seeded):
    created = post_cookware(client, name_zh="空气炸锅", owned=True).json()
    assert session.get(CookwareOwned, created["id"])

def test_new_cookware_not_owned_is_only_a_dictionary_entry(client, session, seeded):
    created = post_cookware(client, name_zh="空气炸锅", owned=False).json()
    assert session.get(CookwareOwned, created["id"]) is None

def test_suggestions_drop_unlisted_and_survive_outage(fake_llm_suggesting_shrimp_and_soy, llm_down):
    assert suggest_allergens(fake_llm_suggesting_shrimp_and_soy, "秘制酱") == ["soy"]
    assert suggest_allergens(llm_down, "秘制酱") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_recipe_save.py tests/test_embedding.py tests/test_allergy_check.py tests/test_dictionary_create.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the backend**

`create_recipe` commits the recipe first, then calls `try_embed_recipe`, so an embedding failure cannot lose it. `extraction_logs.user_corrected` stores the saved recipe as JSON, including `lang`. Skip the log when the table holds `extraction_log_max_rows` rows. Add `retry_pending_embeddings` to the app lifespan and catch its errors so startup still succeeds.

- [ ] **Step 4: Run the backend tests**

Run: `cd backend && "$PY" -m pytest tests/test_recipe_save.py tests/test_embedding.py tests/test_allergy_check.py tests/test_dictionary_create.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing frontend tests in `review.test.tsx`**

```tsx
it("marks an unmatched ingredient and offers link, create, and skip", ...);
  // expect "秘制酱" row to show linkExisting, createEntry, and skip actions
it("keeps allergens unknown until the user confirms a suggestion", ...);
  // suggestion ["soy"] is shown preselected; without clicking confirm, POST /api/ingredients sends allergens: null
it("does not treat an empty suggestion as confirmed none", ...);
  // suggestion [] shows no "confirmed none" state; only clicking containsNone sends allergens: []
it("still allows saving when suggestions are unavailable", ...);
  // { suggested: null } shows a notice, and save stays enabled
it("sets time_source to user when the time is changed", ...);
it("keeps time_source when the time is unchanged", ...);
it("asks whether the user owns newly created cookware", ...);
  // owned checkbox value is sent to POST /api/cookware
it("drops skipped cookware from the save request", ...);
it("shows the allergen warning without blocking save", ...);
it("posts one recipe when save is double-clicked", ...);
  // two quick clicks, one POST /api/recipes
it("keeps the edited form and shows an error when save fails", ...);
it("warns before unload while unsaved drafts exist", ...);
  // dispatching beforeunload is prevented when the queue is not empty
it("moves to the next draft after saving in a batch", ...);
```

Also add `import/import.test.tsx`:

```tsx
it("keeps the pasted text and shows a message when import fails", ...);
it("suggests pasting text when a web import fails", ...);
it("shows each failed batch item and queues the rest", ...);
```

- [ ] **Step 6: Run them to verify they fail**

Run: `cd frontend && npm test`
Expected: FAIL.

- [ ] **Step 7: Implement the frontend**

`drafts.tsx`: a React context holding the review queue in memory only, per the Phase 0 decision that drafts are not stored; a refresh requires extracting again. The import page has text and URL input, screenshot upload, and batch upload. The review page shows and edits every `RecipeWrite` field, and sends `provenance` back unchanged. All text comes from `messages/*.json`.

- [ ] **Step 8: Verify**

Run: `cd frontend && npm test && npm run lint`
Expected: PASS.

- [ ] **Step 9: User commit**

```bash
git add backend frontend
git commit -m "feat: add recipe review, saving, embeddings, and allergy warnings"
```

---

### Task 7 (T0.7): Management Pages

**Files:**
- Create: `backend/minemydinner/api/{inventory,diners}.py`
- Modify: `backend/minemydinner/api/{recipes,dictionary}.py`, `backend/minemydinner/dictionary/service.py`
- Create: `frontend/src/app/[locale]/{library,inventory,people,dictionary}/` pages
- Test: `backend/tests/test_recipes_manage.py`, `test_inventory_api.py`, `test_diners_api.py`, `test_dictionary_manage.py`; frontend tests beside each page

**Interfaces:**
- Consumes: `update_recipe`, `delete_recipe`, `try_embed_recipe`, `link_unmatched_lines`, `suggest_allergens`.
- Produces API routes:
  - `GET /api/recipes?q=&dish_type=&page=`: text search on title, tags, and ingredient names, with no model call. Vector search arrives in T1.1.
  - `GET|PUT|DELETE /api/recipes/{id}`
  - `GET|PUT /api/inventory`, `DELETE /api/inventory/{ingredient_id}`
  - `GET|PUT|DELETE /api/cookware-owned/{cookware_id}`, with `GET /api/cookware-owned` for the list
  - `GET|PUT|DELETE /api/always-in-stock/{ingredient_id}`, with `GET /api/always-in-stock` for the list
  - `GET|POST /api/diners`, `PATCH|DELETE /api/diners/{id}`
  - `GET|POST /api/diners/{id}/rules`, `DELETE /api/rules/{id}`
  - `GET /api/ingredients?source=user`, `PATCH|DELETE /api/ingredients/{id}`
  - `GET /api/cookware?source=user`, `PATCH|DELETE /api/cookware/{id}`
  - `GET /api/allergens/unresolved` -> `{entries: [{id, name, dish_count}], unmatched: [{name, dish_count}]}`
  - `POST /api/allergens/resolve {name | ingredient_id, allergens: list[str]}`

- [ ] **Step 1: Write the failing backend tests**

```python
# test_recipes_manage.py
def test_search_matches_title_tag_and_ingredient_without_model_call(client, library, fake_llm):
    assert titles(client, q="番茄") == ["番茄炒蛋"] and titles(client, q="凉菜") == ["拍黄瓜"]
    assert titles(client, q="鸡蛋") == ["番茄炒蛋"] and fake_llm.call_count == 0

def test_edit_replaces_lines_and_recomputes_embedding(client, session, library, fake_llm):
    updated = put_recipe(client, title="新名字", ingredients=[LINE])
    assert updated["title"] == "新名字" and len(updated["ingredients"]) == 1 and fake_llm.embed_count == 1

def test_failed_reembedding_keeps_edit_and_marks_pending(client, session, library, llm_embed_down):
    recipe = session.get(Recipe, put_recipe(client, title="新名字", ingredients=[LINE])["id"])
    assert recipe.title == "新名字" and recipe.embedding is None

def test_delete_removes_ratings_and_keeps_plan_title_and_variant(client, session, recipe_with_rating_plan_variant):
    recipe_id, item, variant = recipe_with_rating_plan_variant
    assert client.delete(f"/api/recipes/{recipe_id}").status_code == 204
    session.expire_all()
    assert count(session, Rating) == 0 and item.recipe_id is None and item.recipe_title
    assert variant.parent_recipe_id is None

# test_inventory_api.py
@pytest.mark.parametrize("name,body,status", [
    ("鸡蛋", {"count": 3}, 200),
    ("鸡蛋", {"level": "low"}, 422),
    ("猪肉", {"level": "plenty", "grams": 500}, 200),
    ("猪肉", {"count": 2}, 422),
    ("盐", {}, 200),
    ("盐", {"count": 1}, 422),
])
def test_fields_follow_stock_mode(client, seeded, name, body, status):
    assert put_inventory(client, name, body).status_code == status

def test_expiry_is_optional_and_sorted(client, seeded):
    put_inventory(client, "鸡蛋", {"count": 3, "expires_on": "2030-01-01"})
    put_inventory(client, "猪肉", {"level": "low"})
    assert client.get("/api/inventory?sort=expiry").json()[0]["expires_on"] == "2030-01-01"

def test_availability_item_is_deleted_to_mean_none(client, seeded):
    put_inventory(client, "盐", {})
    assert client.delete(f"/api/inventory/{id_of(seeded, '盐')}").status_code == 204
    assert client.get("/api/inventory").json() == []

def test_always_in_stock_add_and_remove(client, seeded):
    client.delete(f"/api/always-in-stock/{id_of(seeded, '醋')}")
    assert "醋" not in always_in_stock_names(client)

def test_cookware_owned_add_and_remove(client, seeded):
    cookware_id = cookware_id_of(seeded, "炒锅")
    client.put(f"/api/cookware-owned/{cookware_id}")
    assert cookware_id in owned_ids(client)
    client.delete(f"/api/cookware-owned/{cookware_id}")
    assert cookware_id not in owned_ids(client)

# test_diners_api.py
def test_friend_crud_and_delete_removes_rules(client, session):
    friend = client.post("/api/diners", json={"display_name": "小李"}).json()
    add_rule_api(client, friend["id"], rule_type="allergy", allergen="egg")
    assert client.delete(f"/api/diners/{friend['id']}").status_code == 204
    assert count(session, DietaryRule) == 0

def test_self_diner_cannot_be_deleted(client, self_diner):
    assert client.delete(f"/api/diners/{self_diner.id}").status_code == 409

@pytest.mark.parametrize("rule,status", [
    ({"rule_type": "allergy", "allergen": "peanut"}, 201),
    ({"rule_type": "allergy", "allergen": "shrimp"}, 422),
    ({"rule_type": "allergy", "text": "虾"}, 422),
    ({"rule_type": "allergy", "ingredient_id": "@芒果"}, 201),
    ({"rule_type": "dietary restriction", "text": "不吃内脏"}, 201),
    ({"rule_type": "dislike", "ingredient_id": "@香菜"}, 201),
])
def test_rule_validation(client, seeded, self_diner, rule, status):
    assert post_rule(client, self_diner, resolve_at(seeded, rule)).status_code == status

# test_dictionary_manage.py
def test_seed_entries_are_read_only(client, seeded):
    assert client.patch(f"/api/ingredients/{id_of(seeded, '葱')}", json={"allergens": []}).status_code == 403
    assert client.delete(f"/api/ingredients/{id_of(seeded, '葱')}").status_code == 403

def test_unresolved_lists_null_entries_and_unmatched_names_with_counts(client, session, seeded, dishes_with_unknowns):
    body = client.get("/api/allergens/unresolved").json()
    assert body["unmatched"] == [{"name": "秘制酱", "dish_count": 2}]
    assert body["entries"][0]["name"] == "外婆辣酱"

def test_resolving_unmatched_name_creates_entry_and_links_all_dishes(client, session, seeded, dishes_with_unknowns):
    client.post("/api/allergens/resolve", json={"name": "秘制酱", "allergens": ["soy"]})
    assert client.get("/api/allergens/unresolved").json()["unmatched"] == []
    assert all(line.ingredient_id for line in lines_named(session, "秘制酱"))

def test_resolving_entry_as_none_clears_unknown(client, seeded, dishes_with_unknowns):
    entry_id = client.get("/api/allergens/unresolved").json()["entries"][0]["id"]
    client.post("/api/allergens/resolve", json={"ingredient_id": entry_id, "allergens": []})
    assert client.get("/api/allergens/unresolved").json()["entries"] == []

def test_resolve_rejects_unlisted_allergen_and_requires_explicit_value(client, seeded, dishes_with_unknowns):
    assert client.post("/api/allergens/resolve", json={"name": "秘制酱", "allergens": ["shrimp"]}).status_code == 422
    assert client.post("/api/allergens/resolve", json={"name": "秘制酱"}).status_code == 422

def test_deleting_used_entry_returns_its_lines_to_unmatched(client, session, seeded, entry_used_by_dish):
    client.delete(f"/api/ingredients/{entry_used_by_dish.id}")
    session.expire_all()
    assert all(line.ingredient_id is None for line in lines_named(session, entry_used_by_dish.name_zh))

def test_entry_used_by_a_rule_or_as_a_parent_cannot_be_deleted(client, seeded, entry_used_by_allergy_rule, entry_with_child):
    assert client.delete(f"/api/ingredients/{entry_used_by_allergy_rule.id}").status_code == 409
    assert client.delete(f"/api/ingredients/{entry_with_child.id}").status_code == 409

def test_seed_cookware_is_read_only_and_user_cookware_can_be_deleted(client, seeded):
    assert client.delete(f"/api/cookware/{cookware_id_of(seeded, '炒锅')}").status_code == 403
    assert client.delete(f"/api/cookware/{post_cookware(client, name_zh='空气炸锅', owned=False).json()['id']}").status_code == 204
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_recipes_manage.py tests/test_inventory_api.py tests/test_diners_api.py tests/test_dictionary_manage.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the routes**

`PUT /api/inventory` takes `{ingredient_id, count?, level?, grams?, expires_on?}` and validates against `stock_mode` (`availability`: none of the quantity fields; `count`: `count`; `level`: `level` and optional `grams`). A `grams` value without `level` is rejected.

- [ ] **Step 4: Run the backend tests**

Run: `cd backend && "$PY" -m pytest tests/test_recipes_manage.py tests/test_inventory_api.py tests/test_diners_api.py tests/test_dictionary_manage.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing frontend tests**

```tsx
// library: 
it("searches and filters, and shows name and source link", ...);
it("asks before deleting a recipe", ...);
it("sets time_source to user when an edit changes the time", ...);
// inventory:
it("shows only the field the stock_mode allows", ...);
// people:
it("offers the fixed allergen list and a specific ingredient for an allergy rule", ...);
it("does not offer delete for the user's own record", ...);
it("asks before deleting a friend and says their rules are deleted too", ...);
// dictionary:
it("lists unresolved items with dish counts and suggested allergens", ...);
it("does not send a value until containsNone or confirm is clicked", ...);
```

- [ ] **Step 6: Run them to verify they fail**

Run: `cd frontend && npm test`
Expected: FAIL.

- [ ] **Step 7: Implement the pages**

Add navigation for the import, library, inventory, people, and dictionary pages. Display dictionary names in the current locale, falling back to the other language when one is empty. Reuse `AllergenPicker` and the Task 6 review form for recipe editing.

- [ ] **Step 8: Verify**

Run: `cd frontend && npm test && npm run lint && npm run build`
Expected: PASS.

- [ ] **Step 9: Check the flow in the real app**

Run the backend (`cd backend && "$PY" -m uvicorn minemydinner.main:app --host 127.0.0.1 --port 8000`) and frontend (`cd frontend && npm run dev`). In both languages, add an inventory item, a friend, and a rule; import pasted fictional text; resolve an unknown ingredient; find the dish in the library; edit it; and delete it. Expected: every step works, with no missing translation keys in the console.

- [ ] **Step 10: User commit**

```bash
git add backend frontend
git commit -m "feat: add recipe library, inventory, diner, and dictionary management"
```

---

### Task 8 (T0.9): Extraction Evaluation Script

The design lists this as T0.9. It is built before the real import so the import can be checked at once.

**Files:**
- Create: `backend/minemydinner/evaluation/{__init__,metrics,export,run}.py`
- Test: `backend/tests/test_eval_metrics.py`, `backend/tests/test_eval_cli.py`

**Interfaces:**
- Consumes: `ExtractionLog`, `LlmCall`, `ExtractedRecipe`.
- Produces in `metrics.py`, which is pure and needs no database:
  - `score_item(gold: dict, predicted: dict) -> ItemCounts`
  - `summarize(items: list[EvalItem]) -> Report`, with overall and per-language results for `ingredient_f1`, `amount_accuracy`, `amount_type_accuracy`, `fabrication_rate`, and `valid_json_rate`
- Produces: `python -m minemydinner.evaluation.export --out data/eval/extraction.jsonl` and `python -m minemydinner.evaluation.run --input data/eval/extraction.jsonl`. Each JSONL row is `{lang, input_text, model_output, user_corrected, model_name, prompt_version}`.

Metric definitions: lines match by exact trimmed, case-insensitive `name`. F1 is micro-averaged over lines. Amount accuracy counts only matched lines whose gold `amount_type` is `exact` or `count`, and requires equal amount and unit. Amount type accuracy covers all matched lines. Fabrication rate is the share of matched lines with a null gold amount where the prediction has a number. Valid JSON rate is the share of rows whose `model_output` validates against `ExtractedRecipe`; rows that fail count as zero recall and precision. The cost per item and average latency come from successful `extraction` and `image_understanding` rows in `llm_calls`, per model, and the report also prints the call success rate.

- [ ] **Step 1: Write the failing tests**

```python
# test_eval_metrics.py
def test_f1_counts_missing_and_extra_lines():
    report = summarize([item(gold=["葱", "蛋"], predicted=["葱", "盐"])])
    assert report.overall.ingredient_f1 == pytest.approx(0.5)

def test_amount_accuracy_uses_only_precise_gold():
    report = summarize([item(gold=[line("蛋", 2, "piece", "count"), line("盐", None, None, "vague")],
                             predicted=[line("蛋", 2, "piece", "count"), line("盐", None, None, "vague")])])
    assert report.overall.amount_accuracy == 1.0 and report.overall.amount_counted == 1

def test_fabrication_rate_uses_raw_model_output():
    report = summarize([item(gold=[line("葱", None, None, "vague")], predicted=[line("葱", 5, "g", "exact")])])
    assert report.overall.fabrication_rate == 1.0

def test_results_are_split_by_language():
    report = summarize([item(lang="zh", gold=["葱"], predicted=["葱"]), item(lang="en", gold=["egg"], predicted=[])])
    assert report.by_language["zh"].ingredient_f1 == 1.0 and report.by_language["en"].ingredient_f1 == 0.0

def test_invalid_output_counts_against_json_rate_and_recall():
    report = summarize([item(gold=["葱"], predicted_raw={"bad": 1})])
    assert report.overall.valid_json_rate == 0.0 and report.overall.ingredient_f1 == 0.0

def test_no_precise_samples_reports_none_not_zero():
    assert summarize([item(gold=[line("盐", None, None, "vague")], predicted=[line("盐", None, None, "vague")])]).overall.amount_accuracy is None

# test_eval_cli.py
def test_export_writes_only_rows_with_corrections(session, tmp_path, logs_with_and_without_correction):
    rows = run_export(session, tmp_path / "out.jsonl")
    assert [r["lang"] for r in rows] == ["zh"]

def test_run_prints_metrics_cost_and_latency(tmp_path, session, llm_calls_rows, capsys):
    run_eval(fictional_jsonl(tmp_path))
    out = capsys.readouterr().out
    assert all(k in out for k in ["ingredient_f1", "fabrication_rate", "zh", "en", "cost_per_item", "latency_ms"])

def test_empty_input_exits_with_clear_message(tmp_path):
    assert run_eval_exit_code(empty_file(tmp_path)) == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_eval_metrics.py tests/test_eval_cli.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `metrics.py`, `export.py`, and `run.py`**

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_eval_metrics.py tests/test_eval_cli.py -v`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: add extraction evaluation export and metrics"
```

---

### Task 9 (T0.8): Import Real Data and Phase 0 Acceptance

No code. The user does the import, because the review builds the human labels.

**Files:** none in Git. Data stays in the local database and `data/`.

- [ ] **Step 1: User gate: import and review 30-50 real recipes**

Ask the user to import through the app, including both Chinese and English recipes, and to correct every extraction carefully. Record any bug as a new failing test and fix it with the usual test cycle.

- [ ] **Step 2: Check the counts**

```bash
docker exec minemydinner-db psql -U postgres -d minemydinner -c "SELECT lang, count(*), count(embedding) AS embedded FROM recipes GROUP BY lang"
docker exec minemydinner-db psql -U postgres -d minemydinner -c "SELECT count(*) FROM extraction_logs"
```

Expected: 30-50 recipes in total, both `zh` and `en` present, and `embedded` equal to `count`. Call `POST /api/recipes/embeddings/retry` if any are pending.

- [ ] **Step 3: Export and run the evaluation**

```bash
cd backend && "$PY" -m minemydinner.evaluation.export --out ../data/eval/extraction.jsonl
"$PY" -m minemydinner.evaluation.run --input ../data/eval/extraction.jsonl
```

Expected: metrics for both languages, plus cost and latency.

- [ ] **Step 4: Confirm nothing private is tracked**

Run: `git status --short` and `git ls-files | grep -E "^data/|\.env$"`
Expected: no `data/` or `.env` entries. Show the user the metrics.

- [ ] **Step 5: Run everything**

Run: `cd backend && "$PY" -m pytest` and `cd frontend && npm test && npm run lint && npm run build`
Expected: all pass.

- [ ] **Step 6: Phase 0 acceptance**

Check against design Section 11: 30-50 real recipes are imported, the evaluation set has started, and the extraction evaluation script outputs metrics locally. Ask the user to confirm, then give them the push and PR commands. `gh` is not installed, so they open the PR on GitHub:

```bash
git push -u origin feat/phase-0-data-foundation
```

---

## Carried Into Phase 1

- **T1.1:** vector search in `search_recipes`, and tool-calling invocation on `LLMClient`.
- **T1.4:** fill the call-layer hooks with nickname replacement and restoration.
- **T1.6:** when a recipe is deleted, also delete its notes in the LangGraph Store, which does not exist in Phase 0. When a friend is deleted, clean up saved plans per design 7.6.
- **T1.9:** add the delete-friend warning about conversations to the Task 7 dialog.
