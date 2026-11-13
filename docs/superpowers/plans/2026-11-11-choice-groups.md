# Choice Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a recipe say "pick one of these lines" (yogurt or chocolate spread; any one fruit), end to end: extraction prompt, draft, review form, saved recipe, and the evaluation record.

**Architecture:** A nullable `choice_group` integer on ingredient lines. Lines of one recipe sharing a value are alternatives, and one of each group is required. `ExtractedIngredient` is the single source of the line shape — `DraftIngredient`, `DraftLine`, `RecipeLineWrite`, and `RecipeOut` all derive from it — so the field is added once in the backend and flows through draft, save, and read. The prompt also learns the review rule that the whole text is evidence for amounts and ingredients; the prompt version stays `extract_v1` because the evaluation logs are wiped first.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + pytest (backend), Next.js + vitest (frontend), Gemini extraction prompt (Markdown).

**Spec:** `docs/phase-0-import-feedback.md` — the **Decided** choice-group row, the "whole text is evidence" review rule (fixed item 23), and the open rows on step-level amounts and title version words.

## Global Constraints

- The user performs all Git operations. Every "commit" step means: give the user the exact commands and stop until they confirm. Never run `git add/commit/push` yourself (CLAUDE.md).
- Python commands use the interpreter from `.vscode/settings.json` (`python.defaultInterpreterPath`), read fresh each time, with `PYTHONNOUSERSITE=1 PYTHONUTF8=1`, run from `backend/`. Install nothing. Ruff is not installed — never claim lint ran.
- Never touch `.env`. Backend tests use the test database (`minemydinner_test`); never write test data into `minemydinner`.
- Every file in the repo is English; Chinese only in UI translations, dictionary names, and Chinese recipe text in tests and prompt examples.
- The prompt version stays `extract_v1`: Task 1 empties `extraction_logs` before Task 3 edits the prompt (memory rule: edit in place only while the table is empty).
- Semantics fixed by the spec: `choice_group` null = ordinary line; equal values within a recipe = alternatives, one of the group required when its members have `optional=false`; a group has at least two members and all members share one `optional` value. The model never splits one source into several dishes.
- The backend must be restarted after backend changes; the frontend hot-reloads but new message keys need a hard refresh. Ask the user to save review drafts before anything that reloads the page (none should exist after Task 1's wipe).

## Review Focus

Failure modes no earlier task's happy path covers; each line's pinning test is added to the owning task below.

1. The model emits a singleton group (one line with `choice_group: 1`) — the draft must open in review with the group dropped, never blocked. (Task 2, postprocess test)
2. The user removes one line of a two-line group in the form — the survivor must not be saved as a singleton group; the form clears it. (Task 4)
3. A saved grouped recipe is reopened from the library and saved again unchanged — `choice_group` must round-trip through `RecipeOut` → `ReviewForm` → `RecipeWrite`. (Task 2 API test + Task 4 buildWrite test)
4. Mixed `optional` inside one group reaches the server (an old client or a hand-made request) — 422, never silently saved with ambiguous meaning. (Task 2)
5. A group id shared *across* recipes (model reuses `1` in every recipe) must mean nothing globally — grouping is per recipe. Covered by scoping the validator and all reads to one recipe's lines; no cross-recipe query exists. (Task 2 validator test uses id 1 twice in different saves)

---

### Task 1: Wipe evaluation data and test recipes

No code. Clears the way for editing `extract_v1` in place and for re-importing with groups.

**Files:** none (database only).

- [ ] **Step 1: Show the user what will be deleted and get a yes**

Run (read-only) and show the output:

```bash
docker exec minemydinner-db psql -U postgres -d minemydinner -c "SELECT (SELECT count(*) FROM extraction_logs) AS logs, (SELECT count(*) FROM recipes) AS recipes, (SELECT count(*) FROM ingredients WHERE source='user') AS user_ingredients, (SELECT count(*) FROM cookware WHERE source='user') AS user_cookware;"
```

Ask whether user-created dictionary entries go too (they said yes in principle; confirm the counts). Inventory or rules referencing a deleted entry: list them first with a read-only query if the counts are nonzero.

- [ ] **Step 2: Delete**

```bash
docker exec minemydinner-db psql -U postgres -d minemydinner -c "DELETE FROM extraction_logs; DELETE FROM recipes;"
```

Plus, only if confirmed: `DELETE FROM ingredients WHERE source='user'; DELETE FROM cookware WHERE source='user';`

- [ ] **Step 3: Verify**

Re-run Step 1's query. Expected: confirmed tables show 0.

### Task 2: Backend — column, schema field, validation, postprocess

**Files:**
- Create: `backend/migrations/versions/0004_add_choice_groups.py`
- Modify: `backend/minemydinner/models/recipes.py:149-175` (RecipeIngredient)
- Modify: `backend/minemydinner/extraction/schemas.py` (ExtractedIngredient)
- Modify: `backend/minemydinner/extraction/postprocess.py`
- Modify: `backend/minemydinner/recipes/service.py` (RecipeWrite validator)
- Test: `backend/tests/test_recipe_save.py`, `backend/tests/test_postprocess.py`, `backend/tests/test_recipes_manage.py`

**Interfaces:**
- Produces: `ExtractedIngredient.choice_group: int | None = None` (inherited by `DraftIngredient`, `DraftLine`, `RecipeLineWrite`; serialized by `RecipeOut` via `model_validate(..., from_attributes=True)`); DB column `recipe_ingredients.choice_group INTEGER NULL`; `InvalidRecipe`-style 422 on bad groups; `drop_singleton_groups` applied inside `enforce_amount_rules` (no new public name — Task 3's fakes and Task 4 rely only on the JSON field name `choice_group`).

- [ ] **Step 1: Write the failing tests**

In `test_recipe_save.py`, using the existing `recipe_write` fixture and `line()` helper (`backend/tests/recipefakes.py:84`):

```python
def test_choice_group_saves_and_rereads(client, recipe_write, seeded):
    # two lines sharing group 1 save, and GET /api/recipes returns them with choice_group 1
def test_singleton_choice_group_rejected(client, recipe_write):
    # one line with choice_group=1 -> 422
def test_mixed_optional_in_group_rejected(client, recipe_write):
    # group 1 with optional True and False -> 422
def test_same_group_id_in_two_recipes_is_fine(client, recipe_write):
    # save two recipes each using group 1 -> both 201
```

In `test_postprocess.py`:

```python
def test_singleton_group_dropped():
    # a model line with choice_group=1 and no partner -> choice_group None after enforce_amount_rules
def test_real_group_kept():
    # two lines with choice_group=1 stay grouped
```

- [ ] **Step 2: Run them to verify they fail**

From `backend/`: `pytest tests/test_recipe_save.py tests/test_postprocess.py -q` — expected: new tests FAIL (unknown field / no validation).

- [ ] **Step 3: Implement**

- Migration `0004`: `op.add_column('recipe_ingredients', sa.Column('choice_group', sa.Integer(), nullable=True))`; downgrade drops it. Follow 0003's header style, `down_revision = '0003'`.
- `RecipeIngredient`: `choice_group: Mapped[int | None] = mapped_column(Integer)`.
- `ExtractedIngredient`: `choice_group: int | None = None`.
- `postprocess.py`: inside `enforce_amount_rules`, after the line pass, null out `choice_group` on any line whose value appears only once in the recipe.
- `service.py`: a `model_validator` on `RecipeWrite` — count each non-null `choice_group`; any count of 1 → `ValueError("a choice group needs at least two lines")`; members of one group with differing `optional` → `ValueError("lines of one choice group are optional together")`.

- [ ] **Step 4: Apply the migration and run the backend suite**

`alembic upgrade head` (against the dev DB), then `pytest -q`. Expected: all pass. Restart uvicorn afterwards.

- [ ] **Step 5: Commit (user)**

Give the user: `git add backend && git commit -m "feat: add choice groups to ingredient lines"` with the attribution line per CLAUDE.md/session rules. Wait for confirmation.

### Task 3: Prompt — groups, whole-text amounts, title example

**Files:**
- Modify: `backend/minemydinner/extraction/prompts/extract_v1.md`
- Test: `backend/tests/test_extractor.py` (only if its fake outputs need the field — `choice_group` defaults to null, so likely no change; verify by running it)

**Interfaces:**
- Consumes: `ExtractedIngredient.choice_group` from Task 2 (model JSON may now carry it).
- Produces: prompt text; version stays `extract_v1`.

- [ ] **Step 1: Edit the prompt**

In the Ingredients section add:

- `choice_group`: an integer shared by lines that are alternatives ("pick one of these"), numbered 1, 2, … per recipe; null for ordinary lines. "A（B）" and "A或B" become two lines sharing a group. A generic word such as 水果 or "any vegetables" becomes one line per item the source itself suggests, all sharing a group; never invent items the source does not name. A group needs at least two lines.
- Replace the `missing` definition (line 14): `missing`: the whole text says nothing about the amount. Amounts and ingredients stated anywhere count, including the method: "加入一点点牛奶" makes 牛奶 `vague`, and an ingredient used only in the method, such as the water in a marinade, still gets a line.
- Title rule (line 24): add the example that "（版本2）" / "(version 2)" is kept.
- Extend the Chinese example's source and JSON with a choice (e.g. "香菜（或小葱）" → two lines sharing `"choice_group": 1`), and add `"choice_group": null` to a couple of existing example lines so the model sees both.

- [ ] **Step 2: Run extraction tests**

`pytest tests/test_extractor.py tests/test_postprocess.py tests/test_imports_api.py -q`. Expected: PASS.

- [ ] **Step 3: Real-call check**

Restart uvicorn. POST the kept 果缤纷吐司 source text to `/api/imports/text` (curl, ~$0.002) and inspect the draft JSON. Expected: 酸奶 and 巧克力榛果酱 share one group; the four fruits share another; no invented fruit; 吐司 ungrouped. If the model misses, tighten the prompt wording once and re-check; more than two paid retries → stop and show the user the outputs.

- [ ] **Step 4: Commit (user)**

`git add backend/minemydinner/extraction/prompts/extract_v1.md backend/tests && git commit -m "feat: teach the prompt choice groups and whole-text amounts"`. Wait for confirmation.

### Task 4: Frontend — type, grouping UI, save payload

**Files:**
- Modify: `frontend/src/lib/api.ts:61-73` (IngredientLine)
- Modify: `frontend/src/components/ReviewForm.tsx` (group state, buildWrite, row props)
- Modify: `frontend/src/components/IngredientReviewRow.tsx` (badge + toggle button)
- Modify: `frontend/messages/zh.json`, `frontend/messages/en.json` (`line` namespace)
- Test: `frontend/src/app/[locale]/review/review.test.tsx` (+ `frontend/src/test/helpers.tsx` if its line fixtures need the field)

**Interfaces:**
- Consumes: `choice_group` on draft lines and in `RecipeWrite` (Task 2).
- Produces: `IngredientLine.choice_group: number | null`; message keys `line.choiceWith`, `line.choiceClear`, `line.choiceBadge` (zh: "与上一行二选一", "取消任选", "任选其一 · 组{group}"; en: "Choice with the line above", "Remove from choice", "Pick one · group {group}").

- [ ] **Step 1: Write the failing tests**

In `review.test.tsx`, following its existing render-and-save pattern:

```tsx
test("grouping two lines sends a shared choice_group", ...)   // click "choice with the line above" on line 2, save, assert payload lines carry the same non-null choice_group
test("removing a grouped line clears its partner's group", ...) // group lines 2+3, remove line 3, save, assert every choice_group is null
test("draft groups are shown and kept on save", ...)           // draft with two grouped lines renders the badge; saving keeps the group
test("optional toggles the whole group", ...)                  // group 2+3, tick optional on 2, assert payload has optional true on both
```

- [ ] **Step 2: Run to verify they fail**

From `frontend/`: `npm run test -- review` — expected: FAIL (no button, payload lacks field).

- [ ] **Step 3: Implement**

- `api.ts`: add `choice_group: number | null` to `IngredientLine`.
- `ReviewForm`: include `choice_group: line.choice_group` in `buildWrite` and set `choice_group: null` in `addLine`. Group logic lives here, next to `setLines`: `toggleChoice(index)` joins line *index* to line *index-1*'s group (new id = max existing + 1 when the line above has none) copying the neighbor's `optional`; clearing a line's group also clears a partner left alone; `onRemove` runs the same orphan sweep; toggling `optional` on a grouped line updates every member. Pass each row `groupBadge: string | null` and `onToggleChoice: (() => void) | null` (null for the first line).
- `IngredientReviewRow`: render the badge next to `line-raw` when `groupBadge` is set; render one button — `choiceWith` when ungrouped, `choiceClear` when grouped.
- Both message files get the three keys.

- [ ] **Step 4: Run the frontend suite**

`npm run test` and `npm run lint`. Expected: PASS. Tell the user a hard refresh (Ctrl+F5) is needed for the new keys.

- [ ] **Step 5: Commit (user)**

`git add frontend && git commit -m "feat: review choice groups in the review form"`. Wait for confirmation.

### Task 5: Docs, re-import, end-to-end verification

**Files:**
- Modify: `docs/phase-0-import-feedback.md` (move the decided row and the step-level-amounts row to the fixed list)
- Modify: `docs/design.md:198` (add `choice_group` to the `recipe_ingredients` row; check section 5.1's field list too)

**Interfaces:** consumes everything above; produces nothing new.

- [ ] **Step 1: Update the two docs** as listed.

- [ ] **Step 2: Full test pass**

Backend `pytest -q` from `backend/`; frontend `npm run test` — expected: PASS. Then `python -m minemydinner.evaluation.export --out data/eval.jsonl` is expected to report 0 rows (table is empty) — proves the CLI still runs.

- [ ] **Step 3: User acceptance**

With both servers running, the user re-imports 果缤纷吐司 and 减脂三明治, checks the groups and the step-level amounts (牛奶/番茄酱 vague, 水/油 present) in the review form, corrects and saves both. Then run the read-only `extraction_logs` query and `evaluation.run` to confirm two fresh logs with `choice_group` inside `user_corrected`.

- [ ] **Step 4: Commit (user)**

`git add docs && git commit -m "docs: record choice groups and the whole-text review rule"`. Wait for confirmation.
