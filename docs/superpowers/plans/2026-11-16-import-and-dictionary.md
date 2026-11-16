# Import and Dictionary Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make importing recipes need fewer clicks and corrections: remembered ingredient links, re-matching each draft as it is shown, linking from the "allergens unknown" list, category and stock-mode suggestions, a leave-page warning, and amount controls that do not fight the user.

**Architecture:** Remembered links reuse the alias columns (`user_aliases_*` on built-in entries, `aliases_*` on the user's own entries) and `link_unmatched_lines`; the alias is written when the recipe is saved, and each queued draft is matched again when it becomes current. The suggestion call moves to versioned prompt files and returns category and stock mode with the allergens. Amount and warning changes are frontend only.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic (backend); Next.js 16, React 19, next-intl, plain CSS (frontend); pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-11-16-import-and-dictionary-design.md`

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository. Each "User commit" step gives the commands and stops until the user confirms.
- **Packages:** no new Python or npm packages.
- **Python:** `$PY` is `python.defaultInterpreterPath` from `.vscode/settings.json`, read each time; run backend commands from `backend/` with `PYTHONNOUSERSITE=1 PYTHONUTF8=1`.
- **Secrets:** never read, print, create, edit, or delete `.env`.
- **Language:** every repository file in English; Chinese only in UI translations, dictionary names and aliases, and recipe text with expected results in tests and prompt examples. Every user-facing string in both `frontend/messages/en.json` and `zh.json`.
- **Frontend checks** through the PowerShell tool only: `cd frontend; npm test; npm run lint; npm run build` (skip the build when port 3000 has a listener, and say so).
- **Databases and models:** tests use `minemydinner_test` only and never call a real model; the paid suggestion check runs only after the user approves it.
- **Prompt versions:** `allergen_suggest_v1.md` holds today's prompt text unchanged; `allergen_suggest_v2.md` is new; a used version is never edited (`docs/prompt-versions.md`).
- **Matching stays exact** (`find_term_owner`: case and outer spaces ignored, nothing fuzzy). Cookware gets no remembered links. The seed dictionary does not change.
- **No suggestion takes effect without confirmation**; an unchecked link never changes the dictionary; removing an alias never unlinks existing lines.
- **Stored amount values, the extraction prompt, and existing data do not change.**

## Review Focus

1. A name that differs from an existing alias only by case or outer spaces ("西蓝花 " vs "西蓝花") is treated as the same name: no duplicate alias, no conflict with its own entry — Task 1 test.
2. The user links a line, then edits its name before saving: the alias remembered is the line's name at save time — Task 1 test.
3. The re-match never changes a line the user touched (linked, unlinked, skipped, or renamed), only lines still unmatched as extracted — Task 4 test.
4. The re-match request fails (network or server error): the draft still shows and stays usable, unmatched lines stay unmatched, no error blocks the review — Task 4 test.
5. The leave-page warning is active only while the queue holds a draft: after the last draft is saved, closing the page asks nothing — Task 4 test.

---

### Task 1: Remember a link when the recipe is saved

**Files:**
- Modify: `backend/minemydinner/dictionary/service.py`, `backend/minemydinner/recipes/service.py`, `backend/minemydinner/api/recipes.py`
- Test: `backend/tests/test_personal_aliases.py`, `backend/tests/test_recipe_save.py`, `backend/tests/test_recipes_manage.py`

**Interfaces:**
- Produces: `remember_alias(session: Session, entry: Ingredient, name: str) -> Literal["added", "exists", "conflict"]` in `dictionary/service.py`. Trims the name; zh list when the name has CJK characters, else en; built-in entry → `user_aliases_*`, user entry → `aliases_*`; "exists" when `find_term_owner` already returns this entry; "conflict" when it returns another entry (nothing written); after "added" calls `link_unmatched_lines(session, entry)`.
- Produces: `RecipeLineWrite.remember_link: bool = False`. It is excluded from the `RecipeIngredient` row and from the extraction log's `user_corrected`.
- Produces: `CreatedRecipe.alias_conflicts: list[str]` (POST `/api/recipes`) and `RecipeOut.alias_conflicts: list[str] = []` (PUT `/api/recipes/{id}`): the names not remembered because another entry owns them.

- [ ] **Step 1: Write the failing tests**
  - `test_remember_alias_on_builtin_entry`: an unmatched line 西蓝花 and a seed entry 西兰花 → `remember_alias(...) == "added"`, 西蓝花 in `user_aliases_zh`, the other unmatched 西蓝花 lines now link to it.
  - `test_remember_alias_on_user_entry`: same on a user entry → the name lands in `aliases_zh`.
  - `test_remember_alias_english_name_goes_to_en`: "broccolini" → `user_aliases_en`.
  - `test_remember_alias_exists_ignores_case_and_spaces`: alias already "西蓝花", name " 西蓝花 " → "exists", no duplicate.
  - `test_remember_alias_conflict_writes_nothing`: the name is another entry's alias → "conflict", both entries unchanged.
  - `test_save_with_remember_link_adds_alias` (POST): one line linked by hand with `remember_link: true` → alias added, response `alias_conflicts == []`; the stored line has no `remember_link` column and the log's `user_corrected` has no `remember_link` key.
  - `test_save_without_remember_link_changes_no_dictionary`: same line with `remember_link` false → entry aliases unchanged.
  - `test_save_conflict_still_saves`: name owned by another entry → 201, line keeps the chosen `ingredient_id`, `alias_conflicts == ["西蓝花"]`.
  - `test_save_remembers_the_name_as_saved`: the line's name is "西蓝花碎" in the request (edited after linking) → that name becomes the alias.
  - `test_update_with_remember_link_adds_alias` (PUT): same behaviour on the edit route, `alias_conflicts` in the response.
- [ ] **Step 2: Run them to see them fail.** `cd backend; PYTHONNOUSERSITE=1 PYTHONUTF8=1 $PY -m pytest tests/test_personal_aliases.py tests/test_recipe_save.py tests/test_recipes_manage.py -q`
- [ ] **Step 3: Implement** `remember_alias`, the `remember_link` field, and the remembering in `create_recipe` / `update_recipe`: after the lines are applied and before the commit, for each line with `remember_link` and an `ingredient_id`, call `remember_alias` with the line's name; collect "conflict" names; return them through the API models. `_apply` and `_write_log` dump lines with `exclude={"remember_link"}`.
- [ ] **Step 4: Run the full backend suite.** Expected: all pass.
- [ ] **Step 5: User commit**

```powershell
git add backend
git commit -m "feat: remember a manual ingredient link as an alias when the recipe is saved"
```

### Task 2: Match names again, and link from the "allergens unknown" list (backend)

**Files:**
- Modify: `backend/minemydinner/api/dictionary.py`, `backend/minemydinner/dictionary/service.py`
- Test: `backend/tests/test_dictionary_manage.py`

**Interfaces:**
- Consumes: `remember_alias` (Task 1), `match_ingredient(session, name)` (`extraction/normalize.py`), `link_unmatched_lines`.
- Produces: `POST /api/dictionary/ingredients/match` with body `{"names": list[str]}` (at most 200 names) → `{"matches": [{"name": str, "entry": IngredientOut}]}`, one item per input name that matches, in input order; unmatched names are left out.
- Produces: `link_name(session, name: str, ingredient_id: int, remember: bool) -> tuple[Ingredient, int, bool]` in `dictionary/service.py`: links every unmatched recipe line whose trimmed, case-folded name equals the name; with `remember`, calls `remember_alias`; returns the entry, the number of lines linked, and whether the name was remembered ("added" or "exists").
- Produces: `POST /api/dictionary/allergens/link` with body `{"name": str, "ingredient_id": int, "remember": bool}` → `LinkedIngredient` plus `remembered: bool`. 404 for an unknown entry, 422 for a blank name.

- [ ] **Step 1: Write the failing tests**
  - `test_match_returns_entries_for_known_names`: names ["西兰花", "西蓝花", "火龙果皮"] with 西蓝花 a personal alias of 西兰花 → two matches (西兰花, 西蓝花 → the same entry), 火龙果皮 absent.
  - `test_match_ignores_case_and_spaces`: " Broccoli " → matches the entry named broccoli.
  - `test_match_rejects_too_many_names`: 201 names → 422.
  - `test_link_name_links_lines_without_remembering`: two unmatched 牛肉馅 lines, link to 牛肉末 with `remember` false → both lines linked, 牛肉末 aliases unchanged, `remembered` false, 牛肉馅 gone from `/allergens/unresolved`.
  - `test_link_name_remembers`: `remember` true → alias added, `remembered` true.
  - `test_link_name_conflict`: 牛肉馅 already another entry's alias → lines still linked to the chosen entry, `remembered` false.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** `link_name` and the two routes, following the existing `_committed` / `_linked` helpers in `api/dictionary.py`.
- [ ] **Step 4: Run the full backend suite.**
- [ ] **Step 5: User commit**

```powershell
git add backend
git commit -m "feat: match a draft's names again and link an unknown name to an existing entry"
```

### Task 3: Suggest category and stock mode with the allergens (allergen_suggest_v2)

**Files:**
- Create: `backend/minemydinner/extraction/prompts/allergen_suggest_v1.md`, `backend/minemydinner/extraction/prompts/allergen_suggest_v2.md`, `backend/eval_data/allergen_suggest/names.yaml`, `backend/minemydinner/evaluation/allergen_suggest.py`
- Modify: `backend/minemydinner/extraction/allergen_suggest.py`, `backend/minemydinner/api/dictionary.py`, `backend/minemydinner/dictionary/service.py` (`resolve_allergens`), `docs/prompt-versions.md`, `docs/local-development.md`
- Test: `backend/tests/test_dictionary_create.py`, `backend/tests/test_dictionary_manage.py`

**Interfaces:**
- Produces: `PROMPT_VERSION = "allergen_suggest_v2"` and `load_prompt(version: str = PROMPT_VERSION) -> str` in `extraction/allergen_suggest.py` (same pattern as `extraction/extractor.py`). The v1 file holds today's `PROMPT` text exactly as the code builds it (allergen list expanded), so it can be compared byte for byte.
- Produces: `Suggestion(allergens: list[str], category: str | None, stock_mode: str | None)` and `suggest_entry(client: LLMClient, name: str) -> Suggestion | None` (None when no model is available). Invalid category or stock mode values become None; unlisted allergens are dropped (as today). `suggest_allergens` is removed; its callers use `suggest_entry`.
- Produces: `SuggestionOut` gains `category: str | None` and `stock_mode: str | None` (null when the suggestion is null). `ResolveIn` gains `category: Literal[*CATEGORIES] | None = None` and `stock_mode: Literal[*STOCK_MODES] | None = None`; `resolve_allergens(session, name, ingredient_id, allergens, category=None, stock_mode=None)` uses them for a new entry, else today's "other" / "level".
- Produces: the check script `python -m minemydinner.evaluation.allergen_suggest --names eval_data/allergen_suggest/names.yaml` (Task 7 runs it): per name, calls `suggest_entry` once and prints per-field matches (allergens as a set, category, stock mode), the mismatches, and the total cost from the `llm_calls` rows it wrote. It refuses the main database like the planning evaluation.
- v2 prompt content (design 5.3): the allergen instruction of v1; one category from the `CATEGORIES` list; one stock mode with one line each — `count` for things counted in pieces (eggs, potatoes), `level` for things used up gradually (oil, salt, rice), `availability` for things one either has or not (for example a dried spice kept for long). No examples beyond these.

- [ ] **Step 1: Write the failing tests**
  - `test_v1_prompt_matches_the_old_text`: `load_prompt("allergen_suggest_v1")` equals the old constant's text (keep the expected string in the test).
  - `test_suggest_entry_parses_all_fields` (fake client returning allergens ["egg"], category "egg and dairy", stock_mode "count") → `Suggestion(["egg"], "egg and dairy", "count")`.
  - `test_suggest_entry_drops_invalid_values`: category "dairy", stock_mode "grams" → both None, allergens kept.
  - `test_suggestion_route_returns_category_and_stock_mode`, and all three null when no model is available.
  - `test_resolve_uses_confirmed_category_and_stock_mode`: resolving an unmatched name with category "meat" and stock_mode "count" creates the entry with them; without them, "other" / "level".
  - `test_check_script_scores_fields` (fake client): two names, one mismatching category → the report counts 1/2 for category and lists the mismatch.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the prompt files, `suggest_entry`, the API fields, the `resolve_allergens` parameters, the script, and `names.yaml` with 18 names (13 Chinese, 5 English, covering every stock mode and at least 8 categories, each with `category`, `stock_mode`, `allergens`). Docs: `prompt-versions.md` gets an "Allergen suggestions" section with v1 and v2 rows and drops it from "Prompts without a version yet"; `local-development.md` documents the script and that it costs money.
- [ ] **Step 4: Run the full backend suite.**
- [ ] **Step 5: User commit**

```powershell
git add backend docs/prompt-versions.md docs/local-development.md
git commit -m "feat: allergen_suggest_v2 suggests category and stock mode, versioned suggestion prompts, suggestion check script"
```

### Task 4: Review form — remember checkbox, re-match on show, leave-page warning

**Files:**
- Modify: `frontend/src/components/IngredientReviewRow.tsx`, `frontend/src/components/ReviewForm.tsx`, `frontend/src/components/ReviewQueue.tsx`, `frontend/src/lib/drafts.tsx`, `frontend/src/lib/api.ts`, `frontend/messages/en.json`, `frontend/messages/zh.json`
- Test: `frontend/src/app/[locale]/review/review.test.tsx`, `frontend/src/components/ReviewForm.test.tsx`

**Interfaces:**
- Consumes: `remember_link` and `alias_conflicts` (Task 1); `POST /api/dictionary/ingredients/match` (Task 2).
- Produces: `api.matchIngredients(names: string[]): Promise<{ matches: { name: string; entry: IngredientEntry }[] }>`.
- Produces: each review line in the form state carries `rememberLink: boolean` (sent as `remember_link`) and `touched: boolean` (set when the user links, unlinks, skips, or renames the line).
- Strings: `review.rememberLink` "记住这个关联" / "Remember this link"; `review.aliasConflict` (with `{names}`) "这些名字已是其他条目的别名，没有记住：{names}" / "Not remembered, already an alias of another entry: {names}".

- [ ] **Step 1: Write the failing tests**
  - `shows the remember checkbox checked after a manual link of an unmatched line`; it is absent on a line whose own name matched, and absent when relinking a matched line.
  - `sends remember_link for a remembered line and false when unchecked`.
  - `links the other unmatched same-name lines of the draft when remembered`: two 西蓝花 lines, link one → both linked; unchecking the box leaves the second line's link in place but not remembered.
  - `matches the next draft again when it is shown`: queue of two drafts both with 西蓝花 unmatched; save the first with a remembered link; the match endpoint answers 西蓝花 → 西兰花; the second draft shows 西蓝花 linked.
  - `the re-match leaves touched lines alone`: a line the user unlinked or skipped stays as it is although the endpoint returns a match.
  - `a failed re-match leaves the draft usable`: the match request fails → the draft shows, the line stays unmatched, no error notice blocks saving.
  - `shows the alias conflict notice after saving` when `alias_conflicts` is non-empty (the queue moves on; the notice shows above the next draft or on the empty-queue page).
  - `warns before unload only while drafts are queued`: a `beforeunload` event is prevented with drafts in the queue and not after the last draft is saved.
- [ ] **Step 2: Run them to see them fail.** (PowerShell) `cd frontend; npm test`
- [ ] **Step 3: Implement.** The re-match runs when a draft becomes the current one (`ReviewQueue`), sends the names of lines with `ingredient_id === null && !touched` once, and applies matches only to lines still unmatched and untouched when the answer arrives. The `beforeunload` listener lives in `DraftsProvider` and is registered only while `drafts.length > 0`.
- [ ] **Step 4: Run** `npm test`, `npm run lint`, `npm run build`. Expected: all pass.
- [ ] **Step 5: User browser check** (servers on the real database; warn that a reload loses open drafts): import two recipes that share an unknown name, link it once with the box checked, save, and see the second draft link it by itself; close the tab with a draft open and see the browser's confirmation.
- [ ] **Step 6: User commit**

```powershell
git add frontend
git commit -m "feat: remember ingredient links in the review form, match each draft again when shown, warn before leaving with drafts"
```

### Task 5: Link from the "allergens unknown" list, and prefill category and stock mode from the suggestion

**Files:**
- Modify: `frontend/src/components/DictionaryManager.tsx`, `frontend/src/components/IngredientReviewRow.tsx`, `frontend/src/lib/api.ts`, `frontend/messages/en.json`, `frontend/messages/zh.json`
- Test: `frontend/src/app/[locale]/dictionary/dictionary.test.tsx`, `frontend/src/app/[locale]/review/review.test.tsx`

**Interfaces:**
- Consumes: `POST /api/dictionary/allergens/link`, `remembered` (Task 2); `SuggestionOut.category` / `stock_mode`, `ResolveIn.category` / `stock_mode` (Task 3).
- Produces: `api.linkUnknownName(name: string, ingredientId: number, remember: boolean)`; `api.suggestAllergens` renamed `api.suggestEntry`, returning `{ suggested: Allergen[] | null; category: Category | null; stock_mode: StockMode | null }`; `api.resolveAllergens` accepts optional `category` and `stock_mode`.
- Strings: `dictionary.linkExisting` "关联到已有条目" / "Link to an existing entry" (the remember checkbox reuses the wording of `review.rememberLink`, defined again under `dictionary` if the namespaces require it); `dictionary.suggestedHint` "建议值，请确认" / "Suggested, please confirm" or reuse the existing allergen suggestion hint if one exists.

- [ ] **Step 1: Write the failing tests**
  - `links an unknown name to an existing entry with remember checked by default`: click "关联到已有条目", pick 牛肉末, confirm → request `{name: "牛肉馅", ingredient_id, remember: true}`; the name leaves the list.
  - `can link without remembering`: uncheck → `remember: false`.
  - `prefills category and stock mode from the suggestion` in the review row's new-entry form and in the unknown list's confirm form; the user can change them; with a null suggestion the old prefill stays (seasoning when the line's role is seasoning, else other; level).
  - `the confirm form sends the confirmed category and stock mode` to `/allergens/resolve`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement**, reusing `DictionarySearch` and the existing allergen-suggestion display for the "suggested" look.
- [ ] **Step 4: Run** `npm test`, `npm run lint`, `npm run build`.
- [ ] **Step 5: User commit**

```powershell
git add frontend
git commit -m "feat: link an unknown name from the allergens list, and prefill category and stock mode from the suggestion"
```

### Task 6: Amount controls in the review form

**Files:**
- Modify: `frontend/src/components/IngredientReviewRow.tsx`, `frontend/src/lib/amounts.ts`, `backend/minemydinner/constants.py` (comment only), `frontend/src/app/globals.css`, `frontend/messages/en.json`, `frontend/messages/zh.json`
- Test: `frontend/src/app/[locale]/review/review.test.tsx` (or a new `frontend/src/components/IngredientReviewRow.test.tsx` if the review tests get long)

**Interfaces:**
- Consumes: `typeForUnit(unit)` in `amounts.ts` (existing).
- Produces: the type select is removed; with a number the type shows as a read-only label from `typeForUnit`; toggle buttons `review.amountVague` "适量" / "Unspecified amount" and `review.amountMissing` "没写" / "Not stated" with `aria-pressed`; the no-unit option label `review.unitHousehold` "家用量具（碗、杯、把、撮）" / "Household measure (bowl, cup, handful, pinch)". "无" stays only where no number is entered.

- [ ] **Step 1: Write the failing tests**
  - `typing a number derives the type from the unit`: unit 克 → label 精确; switch to 个 → 计数; household option → 家用量具; the number stays.
  - `适量 clears number and unit and is pressed`: saved line has `amount_type: "vague"`, `amount: null`, `unit: null`.
  - `typing a number releases a pressed toggle`: press 没写, type 2 → `aria-pressed` false on both, type derived from the unit.
  - `clicking a pressed toggle again releases it and leaves the amount empty`.
  - `a line extracted as vague shows 适量 pressed`; one extracted as missing shows 没写 pressed.
  - `the save request never has a type that disagrees with the unit`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement**, and add the naming comment next to `UNIT_AMOUNT_TYPES` in `constants.py` and in `amounts.ts`: the stored type for a household measure is `spoon` with a null unit, while the unit `spoon` means a spoon of unstated size.
- [ ] **Step 4: Run** `npm test`, `npm run lint`, `npm run build`; backend `tests/test_schema.py` if the constants comment touched anything else (it should not).
- [ ] **Step 5: User browser check** of the amount controls on an import draft.
- [ ] **Step 6: User commit**

```powershell
git add frontend backend/minemydinner/constants.py
git commit -m "feat: amount type derived from the unit, 适量 and 没写 toggles, household measure label"
```

### Task 7: Paid suggestion check (user gate)

**Files:**
- Modify: `docs/prompt-versions.md`, `backend/eval_data/allergen_suggest/names.yaml` (only if the user changes expected values)

- [ ] **Step 1: User review of the expected values** in `names.yaml` (stock mode is partly a household choice); apply the user's changes.
- [ ] **Step 2: User gate** — state the run (18 names, one call each, the configured `allergen_suggestions` model) and the expected cost (about $0.01); wait for approval.
- [ ] **Step 3: Run** the script once on the test database (`DATABASE_URL` set to the test database as in `docs/local-development.md`).
- [ ] **Step 4: Record** in `docs/prompt-versions.md` next to `allergen_suggest_v2`: date, model, matches per field, notable mismatches. If a field is often wrong, the user decides between a `_v3` and keeping v2; a `_v3` is a follow-up, not part of this task.
- [ ] **Step 5: User commit**

```powershell
git add docs/prompt-versions.md backend/eval_data/allergen_suggest/names.yaml
git commit -m "docs: allergen_suggest_v2 check result"
```
