# Phase 1, Plan 1: Core and First Planning Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver steps 1-3 of the Phase 1 design: the Phase 0 follow-ups that change data (T1.0), the deterministic tools and `validate_plan` (T1.1, T1.2), and a thin end-to-end planning slice (T1.3, core T1.4, minimal T1.9) that ends with the user planning a real meal in the browser.

**Architecture:** Deterministic code in `minemydinner/planning/` (context, stock, dietary rules, search, validation, tools) carries every hard constraint. An outer hand-written LangGraph `StateGraph` in `minemydinner/agent/` follows design 7.3; its planner node runs an inner `create_agent` loop whose only path to a model is `CallLayerMiddleware`, which delegates to `LLMClient.run_task` (cap rules, failover, `llm_calls`, hooks) and writes the planning record. A FastAPI streaming endpoint feeds a minimal Next.js chat page.

**Tech Stack:** Python 3.13, FastAPI (sync), SQLAlchemy 2, Alembic, pgvector, LangChain 1.4.2 (`create_agent`, middleware), LangGraph 1.2.11, `langgraph-checkpoint-postgres` (sync `PostgresSaver`), pytest; Next.js 16, next-intl 4, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-13-phase-1-agent-design.md` (the Phase 1 design) and `docs/design.md` (the product specification it builds on). Read both. Section numbers below such as "6.2" refer to the Phase 1 design; "design 7.5" refers to `docs/design.md`.

**Later plans:** steps 4-12 of the Phase 1 design get their own plans after the user has used this slice. Not in this plan: multi-diner selection and nickname matching (step 4), revision and shopping lists (step 5), memory (step 6), Skills (step 7), subgraphs B-D (step 8), the rest of the chat UI (steps 9-10), the full evaluation and gate (step 11), publication (step 12). The tools `save_note`, `update_preference`, `save_rating`, `save_dietary_rule`, `make_shopping_list`, `extract_recipe`, and `load_skill` arrive with their steps. The "manage or query inventory" intent arrives in plan 2; `update_inventory` is built here so it is ready.

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository or a remote. Each "User commit" step gives the exact commands, then stops until the user confirms. Read-only Git commands may run directly.
- **Python:** `$PY` means the value of `python.defaultInterpreterPath` in `.vscode/settings.json`, read each time. Never copy that path into a file.
- **Packages:** do not install Python packages. A "User gate" step asks the user to install. If another package is needed, stop and say which and why. npm packages may be installed only if a task names them (none here).
- **Secrets:** never read, print, create, edit, or delete `.env`. Tests never load `.env` (`Settings(_env_file=None)`).
- **Language:** every repository file in English; Chinese only in UI translations, dictionary names and aliases, and recipe text with expected results in tests and prompt examples.
- **Data:** no real recipe or evaluation data enters Git. Test data is fictional and hand-written. Planning evaluation scenarios live in `data/` (git-ignored).
- **Real-app checks** run against the test database (`DATABASE_URL` overridden to the `minemydinner_test` URL from `Settings.test_database_url`), never `minemydinner`. The user's own hands-on use at the checkpoint is on their real instance.
- **Tests never call a real model or the network.** Real calls happen only in `minemydinner.llm.smoke`, the running app, and the planning evaluation script.
- **Prompt versions:** `extraction_logs` has rows, so the extraction prompt change in Task 3 creates `extract_v2`; `extract_v1.md` stays unchanged. The planning prompts start at `plan_v1`.
- **Hard constraints are code:** H1-H6 and the search exclusions are enforced in `minemydinner/planning/`; prompts only describe them.
- **Every model call goes through `LLMClient`** (directly, or through `CallLayerMiddleware`). No code constructs a chat model outside `llm/registry.py`.
- **Planning is essential:** `planning` keeps `essential: true`; the cap never stops it. Query embeddings use the non-essential `embeddings` task and fall back as in 6.5.
- **Allergen notice:** wherever a plan is shown, the fixed notice from design 5.5 appears; it cannot be dismissed and is UI text, not model output.
- **Frontend tests** run in PowerShell: `cd frontend; npm test`. Git Bash vitest fails collection.
- **Reply language:** the agent replies in the UI locale (`zh` or `en`) sent with each message.

## Review Focus

These conditions are implied by the specs but no requirement names them. Each has a test in the task named.

- **A recipe line linked to a parent ingredient (酱油) while stock holds only a child (生抽), and the reverse:** the parent need is satisfied by the child; a child need is never satisfied by the parent. Also through a choice group whose only satisfiable member is a child. (Task 5)
- **The user has an allergy and a dish has one unlinked line:** the dish is excluded from search, listed under "left out for unknown allergens", and a hand-picked plan containing it fails H2. With only a dislike, the same dish is selectable. (Tasks 6, 7, 8)
- **The model proposes a recipe id that does not exist, the same id twice, or an id the search excluded:** H5 or H2 rejects it and the planner gets the specific error; after N retries the plan is never shown if H2/H5 still fail (6.3). (Tasks 8, 12)
- **The monthly cap is exceeded mid-conversation:** planning continues, search falls back to text matching with `semantic_paused`, and the chat shows a one-line notice. (Tasks 7, 12, 14)
- **The user reloads the chat page while a question (interrupt) is pending:** the conversation, the pending question, and the last plan come back from the checkpointer. (Tasks 11, 13, 14)

## File Map

```
backend/
  pyproject.toml                         + langgraph-checkpoint-postgres, psycopg-pool pins (Task 11)
  config/models.yaml                     tool_call_options for gpt-6-luna; deepseek vision outcome (Task 4)
  migrations/versions/0005_personal_aliases.py         (Task 1)
  migrations/versions/0006_planning_runs_and_prefs.py  (Task 11)
  minemydinner/
    settings.py                          allowed_origins, allowed_hosts (Task 10)
    main.py                              origin/host middleware, chat router, checkpointer lifecycle
    dictionary/service.py                personal aliases (Task 1)
    extraction/normalize.py              names_equal covers personal aliases (Task 1)
    extraction/postprocess.py            English words in window, raw-in-source (Task 3)
    extraction/schemas.py, extractor.py  recipe_count, extract_v2 (Task 3)
    extraction/prompts/extract_v2.md     (Task 3)
    imports/service.py, web.py           link pattern, Chinese floor, Content-Type (Task 2)
    llm/config.py, client.py, smoke.py   run_task, tool_call_options, safe _log, smoke checks (Task 4)
    api/recipes.py                       selectinload (Task 7)
    planning/
      context.py                         PlanningContext, RuleRef, load_planning_context (Task 5)
      stock.py                           compute_missing, missing_cookware (Task 5)
      dietary.py                         check_recipes (Task 6)
      search.py                          search_recipes, recipe_detail (Task 7)
      inventory.py                       inventory_snapshot, update_inventory (Task 7)
      validate.py                        validate_plan (Task 8)
      tools.py                           LangChain tools, TOOLS_VERSION (Task 9)
    agent/
      state.py, request.py, records.py, middleware.py, planner.py, graph.py, checkpoint.py
      prompts/plan_v1/route.md, parse.md, planner.md
    api/chat.py, api/preferences.py
    evaluation/planning.py               minimal planning evaluation (Task 15)
  tests/  test_personal_aliases.py, test_web_import.py, test_postprocess.py, test_extractor.py,
          test_llm_client.py, test_planning_stock.py, test_planning_dietary.py, test_planning_search.py,
          test_planning_inventory.py, test_validate_plan.py, test_planning_tools.py, test_origin_check.py,
          test_agent_graph.py, test_agent_middleware.py, test_agent_request.py, test_chat_api.py,
          test_planning_eval.py, planningfakes.py
frontend/
  messages/en.json, zh.json
  src/components/DictionaryBrowser.tsx   personal aliases editor (Task 1)
  src/components/ReviewForm.tsx          several-recipes warning (Task 3)
  src/components/MainNav.tsx             chat entry (Task 14)
  src/app/[locale]/chat/page.tsx
  src/components/ChatPage.tsx, PlanCard.tsx, AllergenNotice.tsx, RequestStylePrompt.tsx
  src/lib/chat.ts                        stream reader and event types
```

---

### Task 1 (T1.0): Personal Aliases on Built-in Ingredients

Built-in (seed) entries are read-only and reseeding overwrites their aliases, so personal aliases live in separate columns the seed never touches. Phase 1 design 8.1.

**Files:**
- Create: `backend/migrations/versions/0005_personal_aliases.py`
- Modify: `backend/minemydinner/models/recipes.py` (`Ingredient`), `extraction/normalize.py`, `dictionary/service.py`, `api/dictionary.py`, `frontend/src/components/DictionaryBrowser.tsx`, `frontend/messages/en.json`, `zh.json`
- Test: `backend/tests/test_personal_aliases.py`, `frontend/src/components/DictionaryBrowser.test.tsx`

**Interfaces:**
- Produces: `Ingredient.user_aliases_zh: list[str]`, `Ingredient.user_aliases_en: list[str]` (Text arrays, not null, default `{}`), on every entry; meaningful for seed entries.
- Produces: `normalize.all_terms(entry: Ingredient) -> list[str]` (names, aliases, user aliases; non-empty). `names_equal(table, term)` also matches `user_aliases_*` when the table is `Ingredient`.
- Produces: `dictionary.service.set_personal_aliases(session, entry_id: int, aliases_zh: list[str], aliases_en: list[str]) -> tuple[Ingredient, int]`, returning the entry and the number of lines relinked. Raises `EntryNotFound`, `TermConflict` (a term owned by another entry), `ReadOnlyEntry` when the entry is user-created (those edit their own aliases).
- Produces: `PUT /api/dictionary/ingredients/{entry_id}/personal-aliases` body `{"aliases_zh": [...], "aliases_en": [...]}` → `LinkedIngredient` (same shape as create). 404, 409 `term_conflict`, 422 `not_seed`.
- Produces: `IngredientOut` gains `user_aliases_zh`, `user_aliases_en`.
- Consumed later: `all_terms` by `planning/dietary.py` (Task 6) and `planning/search.py` (Task 7).

- [ ] **Step 1: Write the failing backend tests** in `test_personal_aliases.py` (use `seeded`, `by_zh`, `client` from `recipefakes`/`conftest`):

```python
def test_personal_alias_links_new_import_lines(session, seeded):
    potato = by_zh(session, "土豆")
    set_personal_aliases(session, potato.id, ["马铃薯丁"], [])
    assert match_ingredient(session, "马铃薯丁").id == potato.id

def test_personal_alias_relinks_existing_unlinked_lines(client, session, recipe_with_unlinked_line):  # line name "马铃薯丁", ingredient_id None
    potato = by_zh(session, "土豆")
    entry, relinked = set_personal_aliases(session, potato.id, ["马铃薯丁"], [])
    assert relinked == 1 and unlinked_line(session).ingredient_id == potato.id

def test_personal_alias_survives_reseed(session, seeded):
    set_personal_aliases(session, by_zh(session, "土豆").id, ["马铃薯丁"], [])
    seed_all(session)
    assert by_zh(session, "土豆").user_aliases_zh == ["马铃薯丁"]

def test_personal_alias_conflicting_with_another_entry_is_rejected(session, seeded):
    with pytest.raises(TermConflict):
        set_personal_aliases(session, by_zh(session, "土豆").id, ["番茄"], [])

def test_user_entry_rejects_personal_aliases(session, user_ingredient):
    with pytest.raises(ReadOnlyEntry):
        set_personal_aliases(session, user_ingredient.id, ["x"], [])

def test_new_user_entry_cannot_take_a_personal_alias_term(client, session, seeded):
    set_personal_aliases(session, by_zh(session, "土豆").id, ["马铃薯丁"], [])
    response = client.post("/api/dictionary/ingredients", json={**USER_ENTRY, "name_zh": "马铃薯丁"})
    assert response.status_code == 409

def test_put_personal_aliases_endpoint(client, session, seeded):
    potato = by_zh(session, "土豆")
    body = client.put(f"/api/dictionary/ingredients/{potato.id}/personal-aliases",
                      json={"aliases_zh": [" 马铃薯丁 ", "马铃薯丁"], "aliases_en": []}).json()
    assert body["user_aliases_zh"] == ["马铃薯丁"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_personal_aliases.py -v`
Expected: FAIL (import error or missing columns).

- [ ] **Step 3: Implement**

- Migration `0005`: add both columns with `server_default '{}'`. `downgrade` drops them.
- `names_equal`: add `_alias_matches(table.user_aliases_zh, term)` and `_en` only when `table is Ingredient` (cookware has no such columns).
- `_check_terms` already uses `find_term_owner`, so conflicts with personal aliases follow from `names_equal`. `set_personal_aliases` cleans terms with `_clean_aliases`, checks each against other entries, writes both columns, flushes, then relinks with `link_unmatched_lines` extended to include `user_aliases_*` (use `all_terms`).
- `seed.py` must not assign `user_aliases_*` (the test pins this).
- Ingredient dictionary search (`_search`) also matches `user_aliases_*` in its ILIKE clause.

- [ ] **Step 4: Run the backend tests**

Run: `cd backend && "$PY" -m pytest tests/test_personal_aliases.py tests/test_dictionary_create.py tests/test_dictionary_manage.py tests/test_normalize.py tests/test_seed.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing frontend test** in `DictionaryBrowser.test.tsx`:

```tsx
it("lets the user add a personal alias to a built-in entry", async () => {
  // A seed row "土豆" is listed; clicking "My aliases" opens a small form.
  await user.click(screen.getByRole("button", { name: "My aliases" }));
  await user.type(screen.getByLabelText("Chinese aliases (comma-separated)"), "马铃薯丁");
  await user.click(screen.getByRole("button", { name: "Save aliases" }));
  expect(fetchMock).toHaveBeenCalledWith("/api/dictionary/ingredients/1/personal-aliases",
    expect.objectContaining({ method: "PUT" }));
  expect(await screen.findByText(/马铃薯丁/)).toBeInTheDocument();
});
```

- [ ] **Step 6: Implement the editor in `DictionaryBrowser.tsx`** for seed rows only: shows current personal aliases, a comma-separated input per language, Save and Cancel. A 409 shows the existing `term_conflict` message. Add the strings to `en.json` and `zh.json` (`dictionary.personalAliases`, `personalAliasesZh`, `personalAliasesEn`, `savePersonalAliases`, and a hint: "Your own wordings for this ingredient. Lines already imported with these words are linked too.").

- [ ] **Step 7: Run frontend tests**

Run (PowerShell): `cd frontend; npm test; npm run lint`
Expected: PASS.

- [ ] **Step 8: User commit**

```bash
git add backend frontend
git commit -m "feat: let users add personal aliases to built-in ingredients"
```

---

### Task 2 (T1.0): Import Link, Page-Length, and Content-Type Fixes, and the Screenshot Hint

**Files:**
- Modify: `backend/minemydinner/imports/service.py` (`URL_PATTERN`, `first_url`, `single_url`), `imports/web.py` (`MIN_MAIN_TEXT_CHARS`, content-type check), `frontend/messages/en.json`, `zh.json` (`import.screenshotsHint`)
- Test: `backend/tests/test_web_import.py`, `backend/tests/test_imports_api.py`

**Interfaces:**
- Produces: `first_url(text) -> str | None` recognizes `https?://…`, `www.…`, and bare `host.tld/path` links (TLD of 2+ ASCII letters followed by `/`). Links without a scheme are returned with `https://` prepended.
- Produces: `web.min_main_text_chars(text: str) -> int`: 200, or 80 when at least 30% of non-space characters are CJK.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("text, url", [
    ("看这个 www.example.com/recipe/1 很好", "https://www.example.com/recipe/1"),
    ("xhslink.com/a/AbC12", "https://xhslink.com/a/AbC12"),
    ("https://example.com/x，然后", "https://example.com/x"),
])
def test_first_url_recognizes_links_without_scheme(text, url):
    assert first_url(text) == url

@pytest.mark.parametrize("text", ["盐1.5/2勺", "比例 3.5/1", "v1.2 版本"])
def test_first_url_ignores_numbers_and_versions(text):
    assert first_url(text) is None

def test_single_www_link_is_imported_as_web(client, monkeypatch):  # fetch is faked
    ...  # POST /api/imports/text {"text": "www.example.com/r"} calls the web path with "https://www.example.com/r"

def test_short_chinese_page_is_accepted(respx_mock):  # main text: 100 CJK characters
    assert fetch_page_text(URL)

def test_short_english_page_is_still_rejected(respx_mock):  # 100 ASCII characters
    with pytest.raises(WebImportError, match="no_main_text"): fetch_page_text(URL)

def test_empty_content_type_is_not_html(respx_mock):  # header "Content-Type: "
    with pytest.raises(WebImportError, match="not_html"): fetch_page_text(URL)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_web_import.py tests/test_imports_api.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement.** A missing `Content-Type` header (absent entirely) keeps today's behaviour of trying HTML; only a present-but-empty value is `not_html`. Change `import.screenshotsHint` to say images are kept on this computer with the recipe: en "1 to 5 PNG, JPEG, or WebP images, up to 10 MB each. They are kept on this computer with the recipe." zh "1 到 5 张 PNG、JPEG 或 WebP 图片，每张不超过 10 MB。图片会随菜谱保存在这台电脑上。"

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_web_import.py tests/test_imports_api.py -v` then (PowerShell) `cd frontend; npm test`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend frontend
git commit -m "fix: recognize links without a scheme, accept short Chinese pages, and correct the screenshot hint"
```

---

### Task 3 (T1.0): extract_v2: Several-Recipes Warning and Stricter Amount Guard

**Files:**
- Create: `backend/minemydinner/extraction/prompts/extract_v2.md` (copy of `extract_v1.md` plus the rule below)
- Modify: `extraction/schemas.py`, `extraction/extractor.py` (`PROMPT_VERSION = "extract_v2"`), `extraction/postprocess.py`, `imports/service.py` (draft field), `frontend/src/components/ReviewForm.tsx`, `frontend/src/lib/api.ts`, messages
- Test: `backend/tests/test_postprocess.py`, `test_extractor.py`, `test_imports_api.py`, `frontend/src/components/ReviewForm.test.tsx` (create if absent, else the existing review test file)

**Interfaces:**
- Produces: `ExtractedRecipe.recipe_count: int = 1` (`ge=1`): how many distinct recipes the model sees in the input. Not stored on `recipes`; `RecipeDraft.recipe_count: int` carries it to review.
- Produces: `_amount_words(amount)` also yields English words for 1-12 ("one" … "twelve"), "half" for 0.5, and "dozen" for 12.
- Produces: `_enforce_line(line, source)`: when `source` is given, `raw` must appear in `source` (compare after collapsing whitespace and casefolding) before `states_an_amount(raw)` is trusted; otherwise it falls through to the window check.

Prompt rule added to `extract_v2.md` (English, near the output fields): "Set `recipe_count` to the number of distinct recipes the input contains. Extract only the first one. A recipe with variations or optional steps is one recipe." Add one short example with two fictional recipes pasted together.

- [ ] **Step 1: Write the failing tests**

```python
def test_english_word_amount_in_method_survives():
    line = ingredient(raw="eggs", name="eggs", amount=2, unit="piece", amount_type="count")
    out = enforce_amount_rules(recipe_with(line), "Whisk two eggs with the milk.")
    assert out.ingredients[0].amount == 2

def test_raw_not_in_source_does_not_vouch_for_amount():
    line = ingredient(raw="盐 5g", name="盐", amount=5, unit="g", amount_type="exact")
    out = enforce_amount_rules(recipe_with(line), "盐适量，翻炒均匀。")
    assert out.ingredients[0].amount is None and out.ingredients[0].amount_type == "vague"

def test_raw_in_source_still_vouches(): ...  # source contains "盐 5g" → amount kept

def test_screenshot_import_keeps_strict_guard(): ...  # source None → unchanged Phase 0 behaviour

def test_prompt_version_is_extract_v2():
    assert PROMPT_VERSION == "extract_v2" and "recipe_count" in load_prompt()

def test_extract_v1_prompt_is_unchanged():
    assert sha256(load_prompt("extract_v1")) == EXTRACT_V1_SHA256  # computed from the file at HEAD before this task

def test_draft_carries_recipe_count(client, fake_llm):  # fake returns recipe_count=2
    assert client.post("/api/imports/text", json={"text": TEXT}).json()["recipe_count"] == 2
```

Frontend:

```tsx
it("warns when the model saw several recipes", () => {
  renderReview(draft({ recipe_count: 2 }));
  expect(screen.getByRole("alert")).toHaveTextContent(
    "This text seems to hold 2 recipes; only the first was read.");
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_postprocess.py tests/test_extractor.py tests/test_imports_api.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement.** The review warning (en) reads "This text seems to hold {count} recipes; only the first was read. Split them with a line of --- and import again." (zh: "这段文字里好像有 {count} 道菜，只读取了第一道。请用单独一行 --- 分开后重新导入。"). The evaluation export already records `prompt_version`; nothing else changes there.

- [ ] **Step 4: Run all backend and frontend tests**

Run: `cd backend && "$PY" -m pytest -q` then (PowerShell) `cd frontend; npm test; npm run lint`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend frontend
git commit -m "feat: extract_v2 warns about several recipes and tightens the amount guard"
```

---

### Task 4 (T1.1): Shared Call Core, Tool-Call Options, and Smoke Checks

**Files:**
- Modify: `backend/minemydinner/llm/config.py`, `llm/client.py`, `llm/smoke.py`, `llm/__init__.py`, `config/models.yaml`, `docs/design.md` (3.1 table, per the vision result)
- Test: `backend/tests/test_llm_client.py`, `backend/tests/test_llm_config.py`

**Interfaces:**
- Produces: `CatalogEntry.tool_call_options: dict[str, Any] = {}`: extra model settings used only when tools are bound. In `models.yaml`, `gpt-6-luna` gets `tool_call_options: {reasoning_effort: none}`.
- Produces: `CallOutcome[R]` dataclass: `value: R`, `model_name: str` (`"name:model_id"`), `fallback_used: bool`.
- Produces: `Attempt[R] = Callable[[ModelConfig, BaseChatModel], tuple[R, dict[str, int] | None]]`: given the candidate's config and a built chat model, returns the result and its usage.
- Produces: `LLMClient.run_task(task: str, attempt: Attempt[R]) -> CallOutcome[R]`: checks the cap (skipped for essential tasks), tries candidates in order, logs each try to `llm_calls`, raises `MonthlyCapExceeded` (never fails over) or `AllModelsFailed`. `invoke_structured` is reimplemented on top of it with unchanged behaviour.
- Produces: `LLMClient.prepare_messages(messages) -> list[BaseMessage]` (pre-hooks; public form of `_prepare_message`) and `LLMClient.restore_text(text: str) -> str` (post-hooks).
- Changes: `_log` catches and logs (`logger.exception`) any database error, so a paid result is still returned.

- [ ] **Step 1: Write the failing tests** in `test_llm_client.py`:

```python
def test_run_task_fails_over_and_logs_both(llm_primary_fails, session):
    outcome = llm_primary_fails.run_task("planning", lambda cfg, model: ("ok", USAGE))
    assert outcome.fallback_used is True and outcome.model_name.startswith("openai:")
    assert [c.success for c in calls(session)] == [False, True]

def test_run_task_respects_cap_for_non_essential(llm_over_cap):
    with pytest.raises(MonthlyCapExceeded):
        llm_over_cap.run_task("extraction", lambda cfg, model: ("ok", USAGE))

def test_run_task_ignores_cap_for_planning(llm_over_cap):
    assert llm_over_cap.run_task("planning", lambda cfg, model: ("ok", USAGE)).value == "ok"

def test_failed_log_write_keeps_the_result(llm, broken_log_session_factory, caplog):
    assert llm_with(broken_log_session_factory).invoke_structured("extraction", MESSAGES, Schema).parsed
    assert "llm_calls" in caplog.text

def test_invoke_structured_behaviour_unchanged(...):  # existing tests keep passing unmodified
```

In `test_llm_config.py`:

```python
def test_repository_config_sets_tool_options_for_openai_fallback():
    cfg = load_llm_config(REPO_CONFIG)
    assert cfg.models["openai"].tool_call_options == {"reasoning_effort": "none"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_llm_client.py tests/test_llm_config.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement** `run_task` by moving the loop out of `invoke_structured`. The attempt receives `build_chat_model(model, timeout)`. Usage `None` logs no tokens. Exceptions other than `MonthlyCapExceeded` from an attempt count as that candidate's failure. In `models.yaml`, next to `deepseek-flash`'s `extra_body`, add the comment: "If thinking is ever enabled for planning, every later request must pass `reasoning_content` back, or the API returns 400."

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Extend `llm/smoke.py`** with two optional checks, run with flags so the default smoke run is unchanged:
  - `--tools`: for each candidate of the `planning` task, bind one trivial tool (`get_time(city: str)`) with that model's `tool_call_options` and require one tool call back. Prints provider, model, success, latency.
  - `--deepseek-vision`: send one tiny generated PNG (a solid red 8×8 square, built in code) to `deepseek-flash` asking for the color; success means a reply containing "red". Prints the reply's first 40 characters.

- [ ] **Step 6: User gate, then real call check**

Tell the user the cost (a few cents) and ask for approval. Then run:

```bash
cd backend && "$PY" -m minemydinner.llm.smoke --tools --deepseek-vision
```

Expected: both planning candidates make a tool call. Record the vision outcome:
- Success: in `docs/design.md` 3.1, change the DeepSeek "Images" cell to "✅ (deepseek-flash; checked 2026-11-xx by a real call)" with the actual date, keeping task assignments unchanged.
- Failure: remove `images` from `deepseek-flash` capabilities in `models.yaml`.

- [ ] **Step 7: User commit**

```bash
git add backend docs/design.md
git commit -m "feat: shared call core with tool-call options, safer call logging, and smoke checks"
```

---

### Task 5 (T1.1): Planning Context and Stock Checks

**Files:**
- Create: `backend/minemydinner/planning/__init__.py`, `planning/context.py`, `planning/stock.py`, `backend/tests/planningfakes.py`
- Test: `backend/tests/test_planning_stock.py`

**Interfaces:**
- Produces in `context.py`:
  - `RuleRef(diner_key: str, rule_type: str, allergen: str | None, ingredient_id: int | None, text: str | None)` frozen dataclass. `diner_key` is `"diner:<id>"` for saved diners, `"guest:<n>"` for temporary guests (plan 2).
  - `MealConstraints(diner_ids: tuple[int, ...], guest_rules: tuple[RuleRef, ...] = (), unconfirmed_diners: int = 0)`.
  - `StockItem(ingredient_id: int, count: int | None, level: str | None, grams: float | None, expires_on: date | None)`.
  - `PlanningContext(constraints: MealConstraints, stock: dict[int, StockItem], always_in_stock: frozenset[int], cookware_owned: frozenset[int], rules: tuple[RuleRef, ...], parents: dict[int, int | None], stock_modes: dict[int, str])`.
  - `load_planning_context(session, constraints) -> PlanningContext`: one query per table; `rules` are the selected diners' `dietary_rules` plus `guest_rules`; `parents` and `stock_modes` cover every ingredient referenced by stock or by recipe lines.
  - `diner_codes(constraints) -> dict[str, str]`: `"diner:<id>"` → `"Diner 1"`… in `diner_ids` order (self first), guests continue the numbering.
- Produces in `stock.py`:
  - `MissingLine(recipe_id: int, name: str, ingredient_id: int | None, reason: Literal["not_in_stock", "level_none", "unrecognized"], choice_group: int | None)`.
  - `compute_missing(session, ctx, recipe_ids: list[int]) -> dict[int, list[MissingLine]]`; every requested id has a key.
  - `missing_cookware(session, ctx, recipe_ids) -> dict[int, list[tuple[int, str]]]` (cookware id, name in the recipe's language).
  - `is_available(ctx, ingredient_id: int, role: str) -> bool`.

Rules (design 5.3 hierarchy, H1): `optional` lines are skipped. A line is available when its ingredient is in `always_in_stock`, or stock holds it with level not `"none"`, or (only when the line's ingredient is a parent) stock holds one of its children likewise. A child line is never satisfied by its parent. `seasoning` lines check availability only (a stock row, level not `"none"`). An unlinked line is `unrecognized`. A choice group is satisfied when any member is; otherwise every non-optional member is reported with its `choice_group`. `count` stock with `count == 0` is unavailable.

- [ ] **Step 1: Write `planningfakes.py`** with builders over the seeded test database: `make_recipe(session, title, dish_type="vegetable dish", lines=[...], cookware=[...], time_min=None, time_source=None, servings=None, embedding=None) -> Recipe`, `stock(session, name_zh, count=None, level=None, grams=None, expires_on=None)`, `own_cookware(session, name_zh)`, `rule(session, diner_id, rule_type, allergen=None, ingredient=None, text=None)`, `self_diner(session)`, `ctx_for(session, *diner_ids, **kwargs) -> PlanningContext`. Lines are `L("生抽", role="seasoning", optional=False, choice_group=None, linked=True)`.

- [ ] **Step 2: Write the failing tests** in `test_planning_stock.py`:

```python
def test_parent_need_met_by_child_in_stock(session, seeded):
    r = make_recipe(session, "红烧", lines=[L("酱油", role="seasoning")]); stock(session, "生抽")
    assert compute_missing(session, ctx_for(session), [r.id])[r.id] == []

def test_child_need_not_met_by_parent(session, seeded):
    r = make_recipe(session, "凉拌", lines=[L("生抽", role="seasoning")]); stock(session, "酱油")
    assert [m.name for m in compute_missing(session, ctx_for(session), [r.id])[r.id]] == ["生抽"]

def test_level_none_counts_as_missing(session, seeded): ...           # reason "level_none"
def test_zero_count_counts_as_missing(session, seeded): ...
def test_always_in_stock_needs_no_inventory_row(session, seeded): ...  # 盐
def test_optional_line_is_ignored(session, seeded): ...
def test_unlinked_line_is_unrecognized(session, seeded): ...          # reason "unrecognized"

def test_choice_group_satisfied_by_one_member_via_child(session, seeded):
    r = make_recipe(session, "吐司", lines=[L("酸奶", choice_group=1), L("酱油", choice_group=1)])
    stock(session, "生抽")
    assert compute_missing(session, ctx_for(session), [r.id])[r.id] == []

def test_unsatisfied_choice_group_reports_every_member(session, seeded): ...

def test_missing_cookware_compares_by_id(session, seeded):
    r = make_recipe(session, "烤", cookware=["烤箱"]); own_cookware(session, "炒锅")
    assert [name for _, name in missing_cookware(session, ctx_for(session), [r.id])[r.id]] == ["烤箱"]
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_planning_stock.py -v`
Expected: FAIL (module not found).

- [ ] **Step 4: Implement `context.py` and `stock.py`.** Pick dictionary names that exist in `backend/seed/ingredients.json` (check 酱油/生抽 parentage and that 盐 is in `always_in_stock.json`); adjust test names to real seed entries if any differ.

- [ ] **Step 5: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_planning_stock.py -v`
Expected: PASS.

- [ ] **Step 6: User commit**

```bash
git add backend
git commit -m "feat: planning context and stock checks with the ingredient hierarchy"
```

---

### Task 6 (T1.1/T1.2): Dietary Rule Checks

Extends Phase 0's allergy-only check to dietary restrictions, dislikes, text rules, aliases, and unknown allergens, for any set of rules. `recipes/allergy.py` keeps serving import review and gains no behaviour change.

**Files:**
- Create: `backend/minemydinner/planning/dietary.py`
- Test: `backend/tests/test_planning_dietary.py`

**Interfaces:**
- Consumes: `RuleRef`, `PlanningContext` (Task 5); `allergen_info` from `recipes/allergy.py`; `all_terms` (Task 1).
- Produces:
  - `RuleConflict(diner_key: str, recipe_id: int, line_name: str, rule_type: str, allergen: str | None, ingredient_id: int | None, text: str | None)`.
  - `DietaryCheck(hard: list[RuleConflict], dislikes: list[RuleConflict], unknown_allergen_lines: dict[int, list[str]], unrecognized_lines: dict[int, list[str]], any_allergy: bool)` with `blocked_by_unknown(recipe_id) -> bool` (true when `any_allergy` and the recipe has unknown-allergen lines).
  - `check_recipes(session, rules: Sequence[RuleRef], recipe_ids: list[int]) -> DietaryCheck`.

Matching (design 5.5, H2): an `allergen` rule hits a linked line whose `allergen_info.known` contains it (parent's allergens inherited). An `ingredient_id` rule hits a linked line whose ingredient or its parent is that id, and an unlinked line whose casefolded `name` equals one of `all_terms(rule ingredient)`. A `text` rule hits a line when the casefolded rule text is contained in the line's `name` or `raw`, or equals any of `all_terms` of the line's ingredient or its parent. `allergy` and `dietary restriction` conflicts go to `hard`; `dislike` to `dislikes`. Optional lines are checked too (a cook may still add them). Variants are ordinary recipes and are checked the same way.

- [ ] **Step 1: Write the failing tests**

```python
def test_allergen_rule_hits_child_via_parent_allergens(session, seeded): ...
def test_rule_on_parent_ingredient_covers_child_line(session, seeded): ...
def test_rule_on_child_does_not_cover_parent_line(session, seeded): ...
def test_ingredient_rule_matches_unlinked_line_by_alias(session, seeded): ...      # unlinked "葱花" vs rule on 葱
def test_ingredient_rule_matches_personal_alias(session, seeded): ...
def test_text_rule_matches_raw_wording(session, seeded): ...                        # text "香菜" vs raw "少许香菜末"
def test_restriction_is_hard_dislike_is_soft(session, seeded):
    check = check_recipes(session, [restriction("猪肉"), dislike("芹菜")], [r.id])
    assert [c.rule_type for c in check.hard] == ["dietary restriction"]
    assert [c.rule_type for c in check.dislikes] == ["dislike"]

def test_unknown_allergens_block_only_when_someone_has_an_allergy(session, seeded):
    r = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    assert check_recipes(session, [allergy("egg")], [r.id]).blocked_by_unknown(r.id)
    assert not check_recipes(session, [restriction("猪肉")], [r.id]).blocked_by_unknown(r.id)

def test_null_allergens_user_entry_is_unknown(session, user_ingredient_with_null_allergens): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_planning_dietary.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `dietary.py`.** Load lines and their ingredients (with parents) for all ids in one query with `selectinload`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_planning_dietary.py tests/test_allergy_check.py -v`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: dietary rule checks for restrictions, dislikes, text rules, and unknown allergens"
```

---

### Task 7 (T1.1): Search, Recipe Detail, Inventory, and Preferences Functions

**Files:**
- Create: `backend/minemydinner/planning/search.py`, `planning/inventory.py`
- Modify: `backend/minemydinner/api/recipes.py` (`list_recipes` uses `selectinload` for ingredients with their ingredient, cookware with their cookware, and images)
- Test: `backend/tests/test_planning_search.py`, `backend/tests/test_planning_inventory.py`, `backend/tests/test_recipes_manage.py` (query-count test)

**Interfaces:**
- Consumes: Tasks 5-6; `LLMClient.embed`.
- Produces in `search.py`:
  - `RecipeHit(id, title, dish_type, time_min, time_source, tags, servings, rating: int | None)`.
  - `ExcludedRecipe(id: int, title: str, lines: list[str])`.
  - `SearchResult(hits: list[RecipeHit], left_out_unknown_allergens: list[ExcludedRecipe], semantic_paused: bool)`.
  - `search_recipes(session, client, ctx, query: str | None = None, max_time: int | None = None, tags: list[str] | None = None, must_use: list[str] | None = None, exclude_recent_days: int | None = None, limit: int = 20) -> SearchResult`.
  - `recipe_detail(session, recipe_id: int) -> dict | None`: title, dish_type, time and source, servings, tags, supplies, steps_summary, source_url, lines (raw, name, ingredient_id, amount, unit, amount_type, role, optional, choice_group), cookware names. No `source_text`, no images.
- Produces in `inventory.py`:
  - `inventory_snapshot(session, category: str | None = None, sort_by_expiry: bool = False) -> dict` with `ingredients` (id, name_zh, name_en, category, stock_mode, count, level, grams, expires_on), `always_in_stock` (names), `cookware` (owned names).
  - `InventoryChange(name: str, action: Literal["set", "remove"], count: int | None = None, level: str | None = None, grams: float | None = None, expires_on: date | None = None)`.
  - `update_inventory(session, changes: list[InventoryChange]) -> dict` with `applied` and `unmatched` (names the dictionary does not know, never created silently); values must fit the entry's `stock_mode` as `api/inventory.py` already enforces (reuse `_fits_stock_mode`, moving it into `planning/inventory.py`).
  - `meal_preferences(session, ctx) -> list[str]`: anonymous constraint lines using `diner_codes`, such as `"Diner 1: allergy egg"`, `"Diner 1: does not eat 香菜"`, `"Diner 1: dislikes 芹菜"`, `"Diner 2: dietary limits unconfirmed"`. Never names, never unselected diners.

Search rules: candidates are filtered by `max_time` (only `stated`/`user` times over the limit are dropped, matching H6), `tags` (all present), `must_use` (each name resolved with `match_ingredient`; a recipe needs a line linked to it or to its child), `exclude_recent_days` (recipes in `meal_plan_items` of plans confirmed in that window). Then `check_recipes` with `ctx.rules`: recipes with `hard` conflicts are dropped silently; recipes `blocked_by_unknown` go to `left_out_unknown_allergens`. With a `query`, rank by cosine distance of `client.embed([query])[0]` among recipes with embeddings, then text matches without embeddings; text matching covers title, tags, line names, and dictionary names, aliases, and personal aliases. If `embed` raises `MonthlyCapExceeded` or `AllModelsFailed`, use text matching only and set `semantic_paused=True`. Without a query, order by rating (nulls last) then newest. Filtering runs over the whole library before `limit`; the library is single-user and small.

- [ ] **Step 1: Write the failing tests**

```python
def test_search_excludes_allergen_dish_and_lists_unknown(session, seeded, fake_embed):
    egg = make_recipe(session, "番茄炒蛋", lines=[L("鸡蛋")])
    odd = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    ok = make_recipe(session, "清炒白菜", lines=[L("白菜")])
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    result = search_recipes(session, fake_embed, ctx_for(session))
    assert [h.id for h in result.hits] == [ok.id]
    assert [e.id for e in result.left_out_unknown_allergens] == [odd.id]

def test_dislike_does_not_exclude(session, seeded, fake_embed): ...
def test_max_time_drops_only_stated_or_user_times(session, seeded, fake_embed): ...
def test_must_use_accepts_child_lines(session, seeded, fake_embed): ...   # must_use ["酱油"] finds a 生抽 recipe
def test_exclude_recent_days_uses_saved_plans(session, seeded, fake_embed): ...
def test_query_ranks_by_vector_distance(session, seeded, embed_returning_vector): ...

def test_cap_falls_back_to_text_search(session, seeded, embed_over_cap):
    make_recipe(session, "土豆丝", lines=[L("土豆")])
    result = search_recipes(session, embed_over_cap, ctx_for(session), query="土豆")
    assert result.semantic_paused and [h.title for h in result.hits] == ["土豆丝"]

def test_text_search_matches_personal_alias(session, seeded, embed_over_cap): ...

def test_update_inventory_rejects_unknown_names(session, seeded):
    out = update_inventory(session, [InventoryChange(name="龙肝", action="set", count=1)])
    assert out["unmatched"] == ["龙肝"]

def test_meal_preferences_use_codes_not_names(session, seeded):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    assert meal_preferences(session, ctx_for(session)) == ["Diner 1: allergy egg"]

def test_list_recipes_query_count_is_constant(client, session, seeded, count_queries):
    for i in range(5): make_recipe(session, f"菜{i}", lines=[L("白菜")])
    with count_queries() as counter: client.get("/api/recipes")
    assert counter.count <= 6
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_planning_search.py tests/test_planning_inventory.py tests/test_recipes_manage.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement.** `count_queries` is a fixture that counts `before_cursor_execute` events on the session's connection; add it to `conftest.py`. Fake embedding clients live in `planningfakes.py`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest -q`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: recipe search with code-enforced exclusions, recipe detail, and inventory tools"
```

---

### Task 8 (T1.2): validate_plan

**Files:**
- Create: `backend/minemydinner/planning/validate.py`
- Test: `backend/tests/test_validate_plan.py`

**Interfaces:**
- Consumes: Tasks 5-7.
- Produces:
  - `PlanSpec(BaseModel)`: `mode: Literal["fridge_only", "shopping"]`, `dishes: int` (≥0), `soups: int` (≥0), `time_limit_min: int | None`.
  - `H2Override(BaseModel)`: `recipe_id: int`, `diner_key: str`, `allergen: str | None`, `ingredient_id: int | None`, `text: str | None`. (Created by manual edits in plan 2; accepted here.)
  - `Finding(BaseModel)`: `code: str` (`"H1"`…`"H6"`, `"S1"`…`"S8"`), `recipe_id: int | None`, `detail: dict[str, Any]`.
  - `ValidationResult(BaseModel)`: `violations: list[Finding]`, `warnings: list[Finding]`, `passed: bool` (no violations), `left_out_unknown_allergens` not here (search reports it).
  - `validate_plan(session, ctx, spec: PlanSpec, recipe_ids: list[int], overrides: Sequence[H2Override] = ()) -> ValidationResult`.
  - `EXPIRY_SOON_DAYS = 3`, `LOW_RATING = 2`.

Rules, from design 7.5 (exact):
- H1 (fridge_only only): `compute_missing` non-empty → one violation per recipe, `detail={"missing": [names]}`.
- H2: `check_recipes(ctx.rules)` hard conflicts, plus `blocked_by_unknown` recipes (`detail={"unknown": [names]}`). A conflict matching an override on the same recipe, diner_key, and target is skipped; any other conflict still fails.
- H3: `missing_cookware` non-empty.
- H4: count of `meat dish` + `vegetable dish` must equal `dishes`; `soup` must equal `soups`; other dish types are extras and do not count. One violation with `detail={"dishes": n, "soups": m, "wanted": [dishes, soups]}`.
- H5: duplicate ids or ids not in `recipes`.
- H6: with `time_limit_min`, a `stated`/`user` time over the limit.
- S1: stock compared only when units match: `exact` g line vs stock `grams`; `count` line with unit `piece` vs stock `count`.
- S2: dislikes, `detail={"diner_key", "line"}`.
- S3: stock items with `expires_on` within `EXPIRY_SOON_DAYS` of today that no selected recipe uses (via hierarchy).
- S4: a selected recipe rated ≤ `LOW_RATING`.
- S5: two or more counted dishes, all `meat dish` or all `vegetable dish`.
- S6: a `vague` line whose stock level is `low`.
- S7: with a time limit, `estimated` time → "estimated"; null time → "unknown"; sum of known times > 1.5 × limit → "total".
- S8: `ctx.constraints.unconfirmed_diners > 0`; and per selected recipe with `unrecognized_lines`.

- [ ] **Step 1: Write the failing tests**, one per rule, plus:

```python
def test_shopping_mode_skips_h1_but_keeps_h2(session, seeded): ...
def test_confirmed_override_covers_only_that_diner_and_rule(session, seeded):
    # recipe has egg (diner 1 allergy) and 花生 (diner 1 allergy peanut); override egg only
    result = validate_plan(session, ctx, SPEC, [r.id], [H2Override(recipe_id=r.id, diner_key="diner:1", allergen="egg")])
    assert [v.detail["allergen"] for v in result.violations if v.code == "H2"] == ["peanut"]
def test_unknown_allergen_dish_fails_h2_only_with_an_allergy(session, seeded): ...
def test_nonexistent_and_duplicate_ids_fail_h5(session, seeded): ...
def test_staple_dish_is_an_extra_for_h4(session, seeded): ...
def test_h6_ignores_estimated_times_but_s7_warns(session, seeded): ...
def test_restriction_on_parent_rejects_child_dish(session, seeded): ...
def test_variant_is_checked_like_any_recipe(session, seeded): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_validate_plan.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `validate.py`.** Each rule is a small private function returning findings; `validate_plan` runs H5 first and drops unknown ids from the other checks.

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_validate_plan.py -v`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: validate_plan with hard constraints H1-H6 and warnings S1-S8"
```

---

### Task 9 (T1.1): Tool Definitions for the Planner

**Files:**
- Create: `backend/minemydinner/planning/tools.py`
- Test: `backend/tests/test_planning_tools.py`

**Interfaces:**
- Consumes: Tasks 5-8.
- Produces: `PlannerDeps` dataclass (`session_factory: sessionmaker[Session]`, `client: LLMClient`, `constraints: MealConstraints`, `mode: str`), passed as the agent's runtime context (`context_schema=PlannerDeps`).
- Produces: LangChain tools (`@tool` with Pydantic argument models; each reads `runtime: ToolRuntime[PlannerDeps]`, opens its own session, builds `load_planning_context`, and returns JSON-serialisable dicts): `get_inventory(category: str | None = None, sort_by_expiry: bool = False)`, `search_recipes(query, max_time, tags, must_use, exclude_recent_days)`, `get_recipe(recipe_id: int)`, `compute_missing(recipe_ids: list[int])`, `check_cookware(recipe_ids: list[int])`, `get_preferences()`.
- Produces: `PLANNER_TOOLS: list[BaseTool]` in that order, and `TOOLS_VERSION: str`: the first 12 hex characters of SHA-256 over the canonical JSON of every tool's name, description, and argument schema.
- `update_inventory` is wrapped as a tool too (`INVENTORY_TOOLS`), not offered to the planner.

Tool descriptions are English and state what code enforces: "Dishes the selected diners cannot eat are already excluded; you cannot include them." `search_recipes` returns `hits`, `left_out_unknown_allergens` (titles only), and `semantic_paused`.

- [ ] **Step 1: Write the failing tests**

```python
def test_tools_version_changes_with_a_schema(monkeypatch): ...
def test_tools_version_is_stable():
    assert TOOLS_VERSION == tools_version(PLANNER_TOOLS)
def test_search_tool_returns_json_with_codes_not_names(session_factory, seeded_db): ...
def test_tool_argument_schemas_are_plain_json_schema():
    for t in PLANNER_TOOLS: json.dumps(t.tool_call_schema.model_json_schema())
def test_get_recipe_unknown_id_returns_error_payload(...):
    assert invoke("get_recipe", {"recipe_id": 999999}) == {"error": "not_found"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_planning_tools.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**. Check the `ToolRuntime` import path and injection in the installed `langchain` 1.4.2 (`langchain.tools`), and test invocation through `ToolNode` or the tool's `invoke` with a `ToolCall` dict as its docs show.

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest tests/test_planning_tools.py -v`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: planner tool definitions with a stable schema version"
```

---

### Task 10: Origin and Host Check on Write Routes

A cross-site page must not trigger paid calls (design: Phase 1 design 9). Done before the chat endpoint exists.

**Files:**
- Modify: `backend/minemydinner/settings.py`, `backend/minemydinner/main.py`, `backend/tests/conftest.py` (client `base_url="http://localhost"`), `.env.example` (document both settings, no values needed)
- Test: `backend/tests/test_origin_check.py`

**Interfaces:**
- Produces: `Settings.allowed_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]`, `Settings.allowed_hosts: list[str] = ["localhost", "127.0.0.1"]`.
- Produces: an HTTP middleware in `main.py`: every request whose `Host` (port stripped) is not in `allowed_hosts` → 400 `{"code": "bad_host"}`; every `POST`/`PUT`/`PATCH`/`DELETE` with an `Origin` header not in `allowed_origins` → 403 `{"code": "forbidden_origin"}`. Requests without `Origin` (scripts, tests) pass.

- [ ] **Step 1: Write the failing tests**

```python
def test_cross_site_post_is_rejected(client):
    r = client.post("/api/imports/text", json={"text": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "forbidden_origin"
def test_same_site_post_passes_the_check(client, fake_llm):
    assert client.post("/api/imports/text", json={"text": TEXT}, headers={"Origin": "http://localhost:3000"}).status_code != 403
def test_get_with_foreign_origin_is_allowed(client): ...
def test_rebound_host_is_rejected(client):
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
```

- [ ] **Step 2: Run to verify they fail**, then implement, then run all backend tests.

Run: `cd backend && "$PY" -m pytest -q`
Expected: PASS after implementation.

- [ ] **Step 3: Check through the Next proxy** with both servers running against the test database: a browser `fetch` from the app page to a POST route succeeds (proxy forwards `Origin: http://localhost:3000` and rewrites `Host` to `127.0.0.1:8000`). If the proxy sends another Host, add it to the defaults and note why in `settings.py`.

- [ ] **Step 4: User commit**

```bash
git add backend .env.example
git commit -m "feat: reject cross-site writes and unexpected hosts"
```

---

### Task 11 (T1.3): Graph Skeleton, Checkpointer, Planning Records Table, and Preferences

**Files:**
- Create: `backend/migrations/versions/0006_planning_runs_and_prefs.py`, `backend/minemydinner/models/agent.py` (exported from `models/__init__.py`), `backend/minemydinner/agent/__init__.py`, `agent/state.py`, `agent/checkpoint.py`, `agent/graph.py`, `agent/records.py`, `backend/minemydinner/api/preferences.py`
- Modify: `backend/pyproject.toml`, `backend/minemydinner/main.py`
- Test: `backend/tests/test_agent_graph.py`, `backend/tests/test_agent_records.py`, `backend/tests/test_preferences_api.py`

**Interfaces:**
- Produces tables:
  - `planning_runs`: `id`, `thread_id` (text, indexed), `created_at`, `prompt_version` (text), `tools_version` (text), `request` (JSONB: the parsed request after defaults), `defaults_used` (text[]), `events` (JSONB list, default `[]`), `retries` (int, default 0), `first_passed` (bool, null), `final_passed` (bool, null), `final_plan` (JSONB, null), `has_temporary_guests` (bool, default false). Local only; never shown in the UI; deleting a conversation does not delete rows (Phase 1 design 6).
  - `app_preferences`: `key` (text PK), `value` (JSONB).
- Produces `records.py`: `PlanningRecorder(session_factory)` with `start(thread_id, request: dict, defaults_used: list[str], prompt_version: str, tools_version: str, has_temporary_guests: bool) -> int`, `add_event(run_id: int, event: dict) -> None` (appends with `jsonb ||` in its own committed transaction), `finish(run_id, retries: int, first_passed: bool, final_passed: bool, final_plan: dict | None) -> None`. Event kinds: `model_call` (`model_name`, `fallback_used`, `messages` as sent, `response` with content and tool calls), `tool_call` (`name`, `args`, `result`), `invalid_tool_call` (`name`, `args`, `error`), `validation` (`attempt`, `result`).
- Produces `state.py`: `ChatState(TypedDict)`: `messages: Annotated[list[AnyMessage], add_messages]`, `locale: str`, `intent: str | None`, `request: dict | None`, `defaults_used: list[str]`, `run_id: int | None`, `proposal: dict | None`, `validation: dict | None`, `retries: int`, `current_plan: dict | None`, `plan_versions: list[dict]`.
- Produces `checkpoint.py`: `open_checkpointer(database_url: str) -> PostgresSaver` (a `psycopg_pool.ConnectionPool` over the URL with `+psycopg` removed; `setup()` once at startup; closed at shutdown).
- Produces `graph.py`: `build_graph(deps: GraphDeps, checkpointer) -> CompiledStateGraph`, with `GraphDeps(session_factory, client, recorder)`. In this task: `route_intent` → `plan` (placeholder node returning a fixed message, replaced in Task 12) or `other` (a fixed reply in the locale: what the agent can do now). 
- Produces preferences API: `GET /api/preferences/request-style` → `{"value": "ask_first" | "plan_then_adjust" | null}`; `PUT` with `{"value": ...}`. Helpers `get_request_style(session) -> str | None`, `set_request_style(session, value) -> None` in `agent/request.py` (created here, extended in Task 12).

`route_intent` uses `client.invoke_structured("planning", messages, Intent)` with `Intent(intent: Literal["plan", "other"])` and the prompt `agent/prompts/plan_v1/route.md`. Later plans add intents.

- [ ] **Step 1: User gate.** Ask the user to install `langgraph-checkpoint-postgres` and `psycopg-pool` into `$PY` (`"$PY" -m pip install langgraph-checkpoint-postgres psycopg-pool`), then pin the installed versions in `pyproject.toml` (read them with `"$PY" -m pip show`). Stop until confirmed.

- [ ] **Step 2: Write the failing tests**

```python
def test_graph_routes_other_intent_to_fixed_reply(fake_planning_llm):  # InMemorySaver
    out = graph.invoke({"messages": [HumanMessage("hi")], "locale": "en"}, thread("t1"))
    assert out["intent"] == "other" and out["messages"][-1].content == OTHER_REPLY_EN

def test_conversation_resumes_from_postgres_checkpointer(test_database_url, fake_planning_llm):
    with open_checkpointer(test_database_url) as saver:
        build_graph(deps, saver).invoke(first_message, thread("resume-test"))
    with open_checkpointer(test_database_url) as saver:
        state = build_graph(deps, saver).get_state(thread("resume-test"))
    assert len(state.values["messages"]) == 2

def test_recorder_appends_events_in_order(session_factory):
    run = recorder.start("t", {}, [], "plan_v1", "abc", False)
    recorder.add_event(run, {"kind": "tool_call"}); recorder.add_event(run, {"kind": "validation"})
    assert [e["kind"] for e in load_run(run).events] == ["tool_call", "validation"]

def test_request_style_round_trip(client):
    assert client.get("/api/preferences/request-style").json() == {"value": None}
    client.put("/api/preferences/request-style", json={"value": "ask_first"})
    assert client.get("/api/preferences/request-style").json() == {"value": "ask_first"}
```

The checkpointer test cleans its thread with `saver.delete_thread("resume-test")`.

- [ ] **Step 3: Run to verify they fail**, implement, run again.

Run: `cd backend && "$PY" -m pytest tests/test_agent_graph.py tests/test_agent_records.py tests/test_preferences_api.py -v`
Expected: PASS after implementation.

- [ ] **Step 4: User commit**

```bash
git add backend
git commit -m "feat: graph skeleton with Postgres checkpointer, planning records, and request-style preference"
```

---

### Task 12 (T1.4 core): The Planning Path

**Files:**
- Create: `backend/minemydinner/agent/request.py` (extend), `agent/middleware.py`, `agent/planner.py`, `agent/prompts/plan_v1/parse.md`, `agent/prompts/plan_v1/planner.md`
- Modify: `agent/graph.py`, `agent/state.py`
- Test: `backend/tests/test_agent_request.py`, `backend/tests/test_agent_middleware.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- Produces in `request.py`:
  - `PROMPT_VERSION = "plan_v1"`.
  - `ParsedRequest(BaseModel)`: `others_mentioned: bool`, `headcount: int | None`, `dishes: int | None`, `soups: int | None`, `mode: Literal["fridge_only", "shopping"] | None`, `time_limit_min: int | None`, `query: str | None`, `must_use: list[str]`, `exclude_recent_days: int | None`.
  - `default_composition(headcount: int) -> tuple[int, int]`: 1 → (1, 0); 2 → (2, 0); 3-4 → (2, 1); 5+ → (3, 1).
  - `apply_defaults(parsed: ParsedRequest) -> tuple[PlanSpec, MealConstraints, list[str]]`: headcount defaults to 1; dishes and soups from `default_composition` when either is null (both reported); mode defaults to `fridge_only`; the list names each defaulted field among `"headcount"`, `"composition"`, `"mode"`. Constraints: the self diner only; `unconfirmed_diners = headcount - 1` (others' limits are unknown until plan 2 adds diner selection, and S8 warns).
  - `needs_question(parsed, style: str | None) -> list[str]`: with `"ask_first"`, returns `["mode"]` and/or `["composition"]` for unstated values; otherwise `[]`.
- Produces in `middleware.py`: `CallLayerMiddleware(client: LLMClient, task: str, recorder: PlanningRecorder)` (an `AgentMiddleware`):
  - `wrap_model_call(request, handler)`: calls `client.run_task(task, attempt)`, where `attempt(cfg, model)` returns `handler(request.override(model=model, messages=client.prepare_messages(request.messages), model_settings={**request.model_settings, **cfg.tool_call_options}))` and its usage from the last `AIMessage.usage_metadata`. Restores text and tool-call string arguments in the response with `client.restore_text`. Records a `model_call` event (messages as sent, after `prepare_messages`) under `request.state["run_id"]`.
  - `wrap_tool_call(request, handler)`: runs the tool; records `tool_call`, or `invalid_tool_call` when the tool result is an error `ToolMessage` (argument validation or execution error).
- Produces in `planner.py`: `PlanProposal(BaseModel)`: `recipe_ids: list[int]`, `note: str` (one or two sentences to the user, in the locale). `build_planner(deps: GraphDeps) -> CompiledStateGraph` = `create_agent(model=<placeholder chat model from the registry for the task's primary>, tools=PLANNER_TOOLS, system_prompt=<planner.md>, middleware=[CallLayerMiddleware(...), ModelCallLimitMiddleware(run_limit=12), ToolCallLimitMiddleware(run_limit=20)], response_format=ToolStrategy(PlanProposal), context_schema=PlannerDeps, state_schema=PlannerState)` with `PlannerState(AgentState)` adding `run_id: int`.
- Produces graph nodes (design 7.3, subgraph A): `parse_request` → (first-use question | ask-first question | `load_context`) → `planner` → `validate_plan` → (`planner` with errors, at most `MAX_RETRIES = 2` retries | `present_plan`).
  - First-use: when `get_request_style` is null, `interrupt({"kind": "request_style"})`; the resume value `"ask_first"` or `"plan_then_adjust"` is saved; a resume of `null` (skipped) saves nothing and plans with defaults.
  - Ask-first: `interrupt({"kind": "question", "fields": [...]})`; the resume value is the user's text, appended as a `HumanMessage` and re-parsed.
  - `load_context`: builds `MealConstraints`, starts the planning record (`has_temporary_guests=False` in this plan), stores `run_id`.
  - `planner`: invokes the inner agent with the request summary (spec, defaults, query, must_use, the anonymous preference lines) plus, on retry, one message listing each violation in plain words. Stores `proposal`.
  - `validate_plan`: runs `validate_plan(...)`; records a `validation` event; increments `retries` on failure.
  - `present_plan`: builds `current_plan` = `{"recipes": [recipe cards: id, title, dish_type, time_min, time_source, servings], "missing": {recipe_id: [names]}, "missing_cookware": {...}, "warnings": [...], "unmet": [...], "defaults_used": [...], "spec": {...}, "headcount": n, "servings_notes": [...], "left_out_unknown_allergens": [...], "semantic_paused": bool, "passed": bool}`; appends it to `plan_versions`; adds an `AIMessage` with `proposal.note`; finishes the record.
  - No valid plan (6.3): if after `MAX_RETRIES` any H2 or H5 violation remains, `current_plan` holds no recipes, `passed=False`, and `unmet` explains why with next actions `["switch_to_shopping", "fewer_dishes", "drop_time_limit"]` as applicable; the note is a fixed locale string, not model text. Otherwise the closest attempt is shown with `unmet` listing H1/H3/H4/H6 findings in plain words.
  - Servings (6.4): when a recipe's `servings` differs from headcount, add `{"recipe_id", "servings", "headcount"}` to `servings_notes`. Amounts are never scaled.

Prompts (`plan_v1/`), English, stating: plan one meal only from the user's library through the tools; never invent recipes or ids; hard constraints are enforced by code and violations will come back; prefer recipes with fewer missing items in fridge-only mode; reply in the given locale; the final answer is `PlanProposal`.

- [ ] **Step 1: Write the failing tests**

`test_agent_request.py`:

```python
@pytest.mark.parametrize("n, expected", [(1, (1, 0)), (2, (2, 0)), (3, (2, 1)), (4, (2, 1)), (5, (3, 1))])
def test_default_composition(n, expected): assert default_composition(n) == expected
def test_apply_defaults_reports_each_default(): ...  # empty parse → defaults_used == ["headcount", "composition", "mode"]
def test_other_people_are_unconfirmed_until_diner_selection(): ...  # headcount 3 → unconfirmed_diners == 2
def test_ask_first_asks_only_for_unstated(): ...
```

`test_agent_middleware.py` (a fake chat model registered through `register_provider` that returns scripted `AIMessage`s with tool calls; see `tests/fakes.py`):

```python
def test_planner_calls_go_through_llm_calls(planner_env, session):
    run_planner(planner_env, script=[tool_call("search_recipes", {}), final_proposal([r1.id])])
    assert [c.task for c in calls(session)] == ["planning", "planning"]

def test_primary_failure_fails_over_inside_the_loop(planner_env_primary_fails, session): ...
def test_record_holds_messages_as_sent_after_pre_hooks(planner_env_with_hook): ...   # hook "小李"→"Diner 2"; event text has "Diner 2"
def test_response_is_restored_before_state(planner_env_with_hook): ...                 # state message has "小李"
def test_invalid_tool_arguments_are_recorded(planner_env, session): ...                # recipe_ids="x" → invalid_tool_call event
def test_runaway_loop_is_cut_off(planner_env_endless_tools): ...                        # ModelCallLimitMiddleware ends it
```

`test_agent_graph.py`:

```python
def test_first_use_asks_request_style_once(graph_env): ...
def test_skipped_style_question_plans_with_defaults(graph_env): ...
def test_ask_first_interrupts_for_unstated_mode(graph_env): ...
def test_failed_validation_retries_with_errors(graph_env):  # first proposal fails H4, second passes
    out = plan(graph_env, "two dishes please")
    assert out["retries"] == 1 and out["current_plan"]["passed"]
    assert "H4" in run_record(graph_env).events[1]["result"]["violations"][0]["code"]

def test_h2_failure_after_retries_is_never_shown(graph_env):  # every proposal includes an allergen dish
    out = plan(graph_env, "...")
    assert out["current_plan"]["recipes"] == [] and out["current_plan"]["passed"] is False

def test_fridge_only_shortfall_shows_closest_with_unmet(graph_env): ...
def test_servings_note_when_headcount_differs(graph_env): ...
def test_record_marks_defaults_and_pass_flags(graph_env): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && "$PY" -m pytest tests/test_agent_request.py tests/test_agent_middleware.py tests/test_agent_graph.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement.** Confirm the 1.4.2 middleware API by reading `langchain/agents/middleware/types.py` in the installed package: `ModelRequest.override(...)` field names, `ModelResponse.result`, how `wrap_tool_call` exposes the tool result, and `ToolStrategy`'s import path. Use a fake model that supports `bind_tools` (return self) in tests.

- [ ] **Step 4: Run the tests**

Run: `cd backend && "$PY" -m pytest -q`
Expected: PASS.

- [ ] **Step 5: User commit**

```bash
git add backend
git commit -m "feat: planning path with defaults, retries, planning records, and the no-valid-plan rule"
```

---

### Task 13 (T1.3/T1.9): Chat API with Streaming

**Files:**
- Create: `backend/minemydinner/api/chat.py`
- Modify: `backend/minemydinner/main.py` (router; checkpointer opened in `lifespan` and stored on `app.state`)
- Test: `backend/tests/test_chat_api.py`

**Interfaces:**
- Produces routes:
  - `POST /api/chat/threads` → `{"thread_id": "<uuid4>"}`.
  - `POST /api/chat/threads/{thread_id}/messages` body `{"text": str (1-4000 chars), "locale": "zh" | "en"}` → `text/event-stream`.
  - `POST /api/chat/threads/{thread_id}/resume` body `{"value": Any, "locale": ...}` → `text/event-stream`.
  - `GET /api/chat/threads/{thread_id}` → `{"messages": [{"role": "user" | "assistant", "text": str}], "current_plan": dict | null, "pending": dict | null}`; 404 `thread_not_found` for an unknown thread.
- Produces SSE events (one JSON object per `data:` line, `event:` names): `progress` `{"step": "parse" | "context" | "search" | "check" | "retry" | "present"}`, `token` `{"text": str}` (assistant text from model calls outside tool loops), `message` `{"role": "assistant", "text": str}`, `plan` `{current_plan}`, `interrupt` `{"kind": ..., ...}`, `error` `{"code": "models_unavailable" | "internal"}`, `done` `{}`.
- Headers: `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`.

The route runs `graph.stream(input, config, stream_mode=["updates", "messages"], subgraphs=True)` in a sync generator and maps chunks to events; the `semantic_paused` flag reaches the page inside `plan`. `AllModelsFailed` during streaming yields `error` then `done`; the conversation stays resumable.

- [ ] **Step 1: Write the failing tests** (graph built with fake models and `InMemorySaver`, injected through a dependency):

```python
def test_message_streams_progress_plan_and_done(client, chat_env):
    events = sse(client.post(f"/api/chat/threads/{tid}/messages", json={"text": "今晚吃什么", "locale": "zh"}))
    assert names(events)[-1] == "done" and "plan" in names(events)

def test_interrupt_then_resume(client, chat_env_first_use):
    events = sse(post_message(...))
    assert events_of(events, "interrupt")[0]["kind"] == "request_style"
    events = sse(client.post(f"/api/chat/threads/{tid}/resume", json={"value": "plan_then_adjust", "locale": "en"}))
    assert "plan" in names(events)

def test_reload_returns_pending_question_and_plan(client, chat_env_first_use): ...
def test_unknown_thread_is_404(client): ...
def test_cross_site_message_is_rejected(client): ...   # Origin evil → 403 (Task 10 middleware)
def test_models_down_yields_error_event(client, chat_env_all_fail): ...
```

- [ ] **Step 2: Run to verify they fail**, implement, run all backend tests.

Run: `cd backend && "$PY" -m pytest -q`
Expected: PASS after implementation.

- [ ] **Step 3: User commit**

```bash
git add backend
git commit -m "feat: chat API with streamed progress, plans, and resumable questions"
```

---

### Task 14 (T1.9 minimal): Chat Page

**Files:**
- Create: `frontend/src/app/[locale]/chat/page.tsx`, `frontend/src/components/ChatPage.tsx`, `PlanCard.tsx`, `AllergenNotice.tsx`, `RequestStylePrompt.tsx`, `frontend/src/lib/chat.ts`, tests `ChatPage.test.tsx`, `PlanCard.test.tsx`, `chat.test.ts`
- Modify: `frontend/src/components/MainNav.tsx` (add `"chat"` after `"home"`), `frontend/messages/en.json`, `zh.json`

**Interfaces:**
- Consumes: Task 13's routes and events.
- Produces in `chat.ts`: `type ChatEvent` (union of the event shapes above), `readEvents(response: Response): AsyncGenerator<ChatEvent>` (parses `event:`/`data:` frames split across chunks), `startThread(): Promise<string>`, `sendMessage(threadId, text, locale)`, `resume(threadId, value, locale)`, `loadThread(threadId)`.
- Behaviour: the thread id lives in `localStorage` key `mmd.chat.thread` (wrapped in try/catch; without it, a new thread starts); "New conversation" starts a new thread. On load, `loadThread` restores messages, the plan, and any pending question. Progress events show a one-line status. `request_style` interrupts render `RequestStylePrompt` (two buttons and "Skip"); `question` interrupts render a hint above the input, and the next message is sent as `resume`.
- `PlanCard` shows: each dish (title, type, time with source), missing ingredients and cookware per dish, warnings in plain words, `unmet` with next-action buttons (each sends a short fixed message such as "Allow shopping" / "允许购物"), defaulted values ("Assumed: 1 person, 1 dish, fridge only. Say if that's wrong."), servings notes, the count of dishes left out for unknown allergens with their titles on request, and the `semantic_paused` notice.
- `AllergenNotice` renders inside every `PlanCard`, always, with no close control. Text (en): "Always check the actual ingredients for allergens yourself. Dictionary entries and extraction can be wrong, so this plan is not a safety guarantee." zh: "请自己核对实际食材中的过敏原。词典和提取都可能出错，这份方案不是安全保证。"
- A small setting on the chat page, "When details are missing: ask me first / plan, then I adjust", reads and writes `/api/preferences/request-style` (6.2: the first-use answer can be changed later).
- A fixed provider notice under the input. en: "Your messages are sent to an AI model provider to plan meals. Conversations are kept on this computer." zh: "你的消息会发送给 AI 模型服务商用于规划。对话保存在这台电脑上。"

- [ ] **Step 1: Write the failing tests**

```tsx
it("parses events split across chunks", async () => { ... });                   // chat.test.ts
it("always shows the allergen notice with no way to close it", () => {
  render(<PlanCard plan={planWithOneDish} />);
  expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /close|dismiss/i })).toBeNull();
});
it("shows the allergen notice even for a plan with no dishes", () => { ... });
it("states defaulted values", () => { ... });
it("lists unmet rules with next-action buttons", async () => { ... });
it("asks the request-style question and resumes with the choice", async () => { ... });
it("restores the conversation and a pending question after reload", async () => { ... });
it("shows the semantic-search notice when paused", () => { ... });
it("changes the request-style setting", async () => { ... });   // PUT /api/preferences/request-style
```

- [ ] **Step 2: Run to verify they fail**

Run (PowerShell): `cd frontend; npm test`
Expected: FAIL.

- [ ] **Step 3: Implement**, reusing `globals.css` tokens and the card styles from Phase 0 pages.

- [ ] **Step 4: Run the tests**

Run (PowerShell): `cd frontend; npm test; npm run lint; npm run build`
Expected: PASS.

- [ ] **Step 5: Real-app check on the test database.** Start the backend with `DATABASE_URL` set to the test database URL and the frontend; create two fictional recipes and stock through the existing pages; ask for a meal in both locales. Verify: tokens or progress appear before the plan (not all at once at the end, which would mean the Next proxy buffers; if it does, report and stop), the allergen notice shows, reload restores the conversation, and a cross-site POST is still rejected. This costs a few planning calls: say so before starting.

- [ ] **Step 6: User commit**

```bash
git add frontend
git commit -m "feat: minimal chat page with plan card, allergen notice, and resumable questions"
```

---

### Task 15 (T1.4): Minimal Planning Evaluation and the Checkpoint

**Files:**
- Create: `backend/minemydinner/evaluation/planning.py`, `backend/tests/test_planning_eval.py`
- Modify: `backend/config/models.yaml` (task `planning_eval`: same primary, fallback, requires, and timeout as `planning`, but `essential: false`, so evaluation is counted and capped per Phase 1 design 6.5), `backend/minemydinner/agent/graph.py` (`GraphDeps.task: str = "planning"`, used by every model call in the graph, including `route_intent`, `parse_request`, and `CallLayerMiddleware`), `backend/tests/test_llm_config.py`
- Create locally, never committed: `data/eval/planning/library.yaml`, `data/eval/planning/scenarios.yaml`

**Interfaces:**
- Produces: `python -m minemydinner.evaluation.planning --scenarios data/eval/planning/scenarios.yaml --library data/eval/planning/library.yaml` → loads the fictional library and inventory into the **test database** (refuses to run when `DATABASE_URL` is the main database URL), runs each scenario through the graph with the real planning models (each scenario a fresh thread; `request_style` preset to `plan_then_adjust`), and prints per scenario and overall: first-attempt pass rate, final pass rate, average retries, default-override rate is not applicable here (single turn), cost and latency summed from `llm_calls` rows created during the run. Exits 0; the gate comes in step 11.
- Produces: `summarize(runs: list[RunOutcome]) -> dict` (pure, tested in CI with fictional numbers). `RunOutcome(scenario: str, first_passed: bool, final_passed: bool, retries: int, cost: Decimal, latency_ms: int)`.
- Scenario fields: `name`, `request` (text), `locale`, `inventory` (name_zh → count/level), `rules` (self diner rules), `expect` (optional notes for the human reader). Write 6 scenarios: fridge-only one person; two people with a soup; an egg allergy with an egg dish and an unlinked-ingredient dish in the library; a time limit with one estimated and one stated dish; shopping-allowed with a nearly empty fridge; an English request. Library: about 15 fictional dishes covering every dish type used.

- [ ] **Step 1: Write the failing test** for `summarize`:

```python
def test_summarize_rates_and_averages():
    s = summarize([RunOutcome("a", True, True, 0, Decimal("0.01"), 900), RunOutcome("b", False, True, 2, Decimal("0.03"), 2100)])
    assert s == {"first_pass_rate": 0.5, "final_pass_rate": 1.0, "avg_retries": 1.0,
                 "cost_usd": Decimal("0.04"), "avg_latency_ms": 1500}
def test_refuses_the_main_database(monkeypatch): ...
def test_repository_config_has_non_essential_planning_eval():   # in test_llm_config.py
    cfg = load_llm_config(REPO_CONFIG)
    assert cfg.tasks["planning_eval"].essential is False
    assert cfg.tasks["planning_eval"].candidates == cfg.tasks["planning"].candidates
```

- [ ] **Step 2: Run to verify it fails**, implement, run again.

Run: `cd backend && "$PY" -m pytest tests/test_planning_eval.py -v`
Expected: PASS after implementation.

- [ ] **Step 3: User gate, then run the evaluation once.** State the expected cost (6 scenarios × up to 3 attempts of a few calls each, a few cents) and ask for approval. Run it against the test database and show the summary.

- [ ] **Step 4: User commit**

```bash
git add backend
git commit -m "feat: minimal planning evaluation over fictional scenarios"
```

- [ ] **Step 5: Checkpoint.** Ask the user to plan a real meal on their own instance in the browser and to note anything that feels wrong. Their findings go into `docs/phase-0-import-feedback.md`'s style of list in a new `docs/phase-1-slice-feedback.md`, and become input to plan 2.
