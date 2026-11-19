# Phase 1 Plan 3a: Manual Plan Edits

Status: draft for approval. The first of three plans for roadmap step 5 (T1.5, `docs/superpowers/specs/2026-11-13-phase-1-agent-design.md` section 5): **3a** manual plan edits (this document), **3b** revising the plan in the conversation and diner changes, **3c** saving the plan and the shopping list, in that order (decided by the user, 2026-11-19). Rules come from `docs/design.md` 7.5 ("Manual plan edits", "Edit log") and 5.5 (unknown allergens). Everything in the Phase 1 design and plans 2a and 2b still holds unless this document changes it.

## 1. Intent

Today a plan can only be replaced: any follow-up plans the whole meal again. The user wants to adjust the plan the model gave by hand on the plan card: remove a dish, add one from their own library, and go back to the model's plan if the edits went wrong. Code checks every edit at once, without a model, and a dish that breaks a diner's allergy or restriction is added only after the user confirms that diner and rule. 3a builds this editing layer; 3b and 3c build on it (3b revises the edited plan in the conversation, 3c saves it).

## 2. Goals, non-goals, anti-goals

**Goals**

1. Remove a dish from the newest plan card, and add one found by a search inside the card.
2. Every edit is applied and checked by code at once and kept in the conversation; a page reload keeps it.
3. A dish that breaks a hard rule of a selected diner (H2) is added only after the user confirms the named diner and rule; the confirmation is kept and later checks honour it.
4. "Restore the model's plan" returns to the latest plan the model produced.
5. An edit log and plan versions are kept in the conversation state in a form 3b and 3c use directly.
6. Editing also works on a reply without a plan (no valid plan, early stop): the user can build the meal from the library.

**Non-goals (later)**

- Revising the plan in the conversation (`revise_plan`), diner changes on an existing plan, the parse seeing the diner card: 3b.
- Saving the plan, the shopping list, writing `meal_plans` / `meal_plan_items` / `shopping_lists`: 3c.
- A list of past versions to browse or compare, and a step-by-step undo (decided by the user: removing an added dish and re-adding a removed one cover it).
- Changing the counts or conditions of the request from the card (those go through the chat or the next-action buttons).
- Regenerating the model's note after an edit.

**Anti-goals (a failure even if it technically works)**

- A dish with an H2 conflict enters the plan without the user confirming that diner and rule.
- An edit is lost on reload, or a later check silently undoes a confirmed override.
- An edit calls a planning model (the card search may embed its query, as search does today; nothing else).
- Manual edits enter the planning metrics or the planning record of the model's run (design 7.5: metrics use the plan as the model produced it).
- A saved friend's nickname reaches a model, or a label ("Diner N") is shown, through the new paths.

## 3. Constraints

- Hard constraints stay in code; the edit path reuses `planning/validate.py` and the existing `H2Override` model.
- No new packages. No prompt change, so no paid evaluation round for 3a.
- No database migration: the edit log, overrides and versions live in the conversation state (the LangGraph checkpointer) until 3c saves a plan.
- Repository rules as before (git by the user, English files, test database for checks, no `.env` access).

## 4. Approaches

Three ways to build the edit path were considered (decided by the user, 2026-11-19: A):

| Approach | Trade-off |
|---|---|
| **A. Edit endpoints that change the conversation state directly (chosen)** | Backend endpoints remove, add (with confirmation), restore, and search; code edits the plan, runs the existing checks, writes the result to the thread state (`update_state`), and returns the new plan card. Simple, no model, immediate. The planning graph does not change; editing is refused while a run or a pending card/question is in progress |
| B. Edits as messages through the planning graph | Edits sit in the conversation history naturally, but the graph needs a branch around route and parse, and edits get tied to streaming and interrupts. Much more complex |
| C. Edits kept in the browser until saved | Lost on reload; the next turn and 3b cannot see them; checks duplicated in the frontend. Rejected |

How overrides behave does not depend on the choice: they live in the state and every check reads them (Section 5.4).

## 5. Decisions

### 5.1 The plan card

```
方案                                     （已手动修改） [恢复模型方案]
番茄炒蛋        素菜 · 10 分钟        [查看做法][原文] [×]
清炒小白菜      素菜 · 8 分钟         [查看做法][原文] [×]
┌╌ + 加一道菜 ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┐   (the list's last row)
  ┌ [小白菜                          ] [搜索] × ───┐   (opened in place)
  │ 清炒小白菜   素菜 · 缺：小白菜、蒜        已在方案中 │
  │ 小白菜豆腐汤 汤 · 缺：小白菜  ⚠ 小王：忌口 豆腐 [加入] │
  └──────────────────────────────────────────────┘
需要注意 …（recomputed by code after each edit）
────────── one light block with the left-out dishes
```

- Each dish has a remove button (×). Removing is always allowed.
- "+ 加一道菜" opens a search inside the card (decided by the user: in the card, not on the library page, not model suggestions). It searches by dish name or ingredient by text only (title, tags, ingredient lines, and ingredient names and aliases), the same match as the library page, across all dish types, at most 10 results with title matches first; the card shows 5, then "显示更多". Changed from meaning and text during the browser check: meaning-based search with no threshold returned unrelated dishes ("牛" found 番茄炒蛋). Each result shows its dish type, missing ingredients, missing cookware, any conflict with a selected diner (who and which rule, or an unknown-allergen ingredient), and "already in the plan" instead of an add button for a dish already there.
- Only the newest plan card is editable (as "Plan again" today); older cards stay read-only.
- A reply without a plan (no valid plan, early stop) shows "+ 加一道菜" too (decided by the user), so the user can build the meal from the library; the reasons and next actions stay as they are.
- After the first edit the card shows "已手动修改" and "恢复模型方案". The model's note above the card is kept and marked as describing the original plan (Claude's call; no new model call).
- "+ 加一道菜" is the last row of the dish list (with no dishes, it follows the reasons) and opens the search in place; × or Escape closes it. Warnings and left-out dishes share one light block (browser check).
- A dish whose conflict the user confirmed shows a line "已确认保留：{who} {rule}" (or "{lines} 过敏原未知"), and the diners line adds "（n 道菜已确认保留）" (`edit.kept` on the card; browser check).
- The plan card's other parts (warnings, left-out dishes, the footer with asked, assumed and diners line, the allergen notice) are recomputed from the edited plan by the same code that builds them today.

### 5.2 What an edit checks

After each edit, code runs the plan checks on the edited dishes and recomputes missing ingredients, missing cookware and the warnings, without a model. The request's spec and the conversation's diners stay as they are.

| The edited plan breaks… | Handling |
|---|---|
| H2: a selected diner's allergy or restriction, or (when any diner has an allergy) an ingredient of unknown allergens | Adding the dish asks first: the confirmation names each diner and rule (by display name, never a label), or the ingredient of unknown allergens with the existing allergen confirmation panel offered (design 5.5, 7.5). Only after confirming is the dish added and the override recorded |
| H1 missing ingredients, H3 missing cookware | Allowed, with a warning; 3c puts missing ingredients on the shopping list |
| H4 counts | Allowed: the edited plan is what counts (design 7.5). A quiet note, not red, says how the plan now differs from a count the user stated ("和你的要求不同：要求 3 道菜，现在 2 道", decided by the user, 2026-11-19). A count filled by default (listed in `defaults_used`, shown in the card's "我假设了" line) gives no note: the user never asked for that number (Claude's call). The number of dishes added never has to match the number removed |
| H5 duplicates | Not allowed; the search marks dishes already in the plan |
| H6 over a stated time limit | Allowed, with a warning (Claude's call; design 7.5 does not list H6) |
| H7 a named ingredient no longer used | Allowed, with a warning (Claude's call; the user's edit wins) |

Soft warnings (S1-S8) show as they do for a model plan. A failed hard check that the user allowed (H1, H3, H6, H7, and H4 as described in its row) shows as a warning or note on the edited plan, not as "no plan" reasons.

**A new conflict on a dish already in the plan (decided by the user, 2026-11-19).** Every check reads today's data (diners' rules, the dictionary, recipes, stock), which may have changed since the model planned: a rule added on the People page, an ingredient's allergens confirmed, a recipe edited. When a check finds an H2 conflict, or an unknown-allergen block, on a dish already in the plan that no override covers, the dish is not removed and the conflict is not hidden:

- The dish is marked in red on the card with a plain sentence saying why, so the user understands what changed, for example "规划之后有变化：小王 忌口 牛肉，这道菜含牛肉" or "这道菜里的 花椒油 过敏原未知，而这顿有人过敏".
- Two actions: "确认保留" (records the override exactly as when adding a dish) and "删除".
- Until every such conflict is resolved, the plan counts as having unresolved conflicts: the card says so, and 3c does not save it.

### 5.2a The search inside the card

The library search leaves out nothing; the card search shows dishes with a conflict or unknown allergens with their markers, and marks dishes already in the plan instead of hiding them. Built as a text match shared with the library page's search, so it makes no model or embedding call.

### 5.3 Versions and restore

- A **model version** is each plan the model produces (a planning run, "plan again", and in 3b a revision).
- A **manual version** starts with the first edit after a model version; every following edit changes that same version. It ends when the model produces a new plan or the user restores the model's plan (decided by the user, 2026-11-19). The user never confirms version boundaries.
- "恢复模型方案" replaces the manual version with the latest model version, dropping the manual version's edits and overrides from the current plan (the versions themselves stay recorded). The restored dishes are **checked again against today's data** (decided by the user, 2026-11-19): missing ingredients and warnings are current, and a new conflict is marked as in Section 5.2. The restored card can therefore differ from the one the model produced; the planning record keeps the model's original. Restoring a no-plan or early-stop reply restores it with its reasons and next actions as recorded.
- No list of versions and no undo in the UI (decided by the user). `plan_versions` in the state records every version for later use: the manual version is appended on the first edit and updated in place by later edits (Claude's call).

### 5.4 Overrides and the edit log

- An override is an `H2Override` (recipe, diner key, and the allergen, ingredient or text; or `unknown_allergens` for the dish). It belongs to the manual version and is passed to every later check of that version, so the confirmed conflict is not raised again. **Removing the dish removes its overrides** (decided by the user, 2026-11-19): adding it back asks again. The model requires a diner key even for an unknown-allergen override, which covers every diner; the builder fills it consistently (Claude's call). A new conflict (another diner or rule) asks again; an unknown-allergen confirmation covers the dish for the whole plan, while known allergens of diners who join later are still checked (design 7.5, plan 2b spec 5.8).
- The edit log records each edit: the action (remove, add, keep after a new conflict, restore), the recipe id, the time, and any override, identified by diner id (guests by their `guest:<n>` key) and allergen or ingredient, never by name (design 7.5). It lives with the manual version in the state; 3c copies it to `meal_plans.edits` when the plan is saved.
- **Contract for 3b:** the current plan, its overrides and its edit log are readable from the state in the form above. 3b revises the edited plan when the user writes in the conversation, and must carry the overrides into every planner attempt and check, so a confirmed dish is never replaced as a violation.

### 5.5 Edits and the conversation

- Edits do not add chat messages and do not go through route, parse or the planner.
- Until 3b, a new message after manual edits plans the meal again as today; the edits are not carried into the new plan (decided by the user). The editable card says so in one quiet line (for example "继续对话会重新规划，手动修改不会带入"). In 3b the next turn revises the edited plan instead.
- Editing is refused while a run is in progress or a card or question is pending in the thread; the UI disables the edit controls then. The backend has no running-stream flag; the saved thread state (its pending tasks and interrupts) tells (Claude's call).
- The edit endpoints take the conversation's locale, since they rebuild the card's text.
- The planning record (`planning_runs`) of the model's run is not changed by edits; planning metrics stay on the model's plans.

### 5.6 Backend

New endpoints under the thread (exact paths and payloads left to the builder): search recipes for the card, remove a dish, add a dish (returning the conflicts to confirm when there are any, and accepting the confirmed overrides), and restore the model's plan. Each returns the updated plan card. They read and write the thread state through the checkpointer (`update_state`), update both `current_plan` and the newest reply's plan in `reply_plans` (the chat history shows plans per reply), and record the manual version in `plan_versions` as 5.3 says (appended on the first edit after a model version, updated in place after that). The code that builds a plan card from recipe ids is shared with `present_plan` rather than copied. Label handling follows plan 2b: any text shown is restored. The card search sends nothing to a model.

## 6. Success criteria

- Remove, add, confirm, restore and search work on plan, closest-attempt, early-stop and no-plan replies of the newest card, and survive a reload.
- An H2-conflicting dish is never added without the confirmation; a confirmed one is not raised again by later edits; a new diner or rule conflict asks again; a dish already in the plan that gains a conflict is marked with the reason and kept until the user confirms or removes it; removing a confirmed dish drops its override.
- No planning model call during edits; planning records and metrics unchanged; no nickname or label leaks.
- Backend and frontend tests cover each rule in Section 5.2, the version rule in 5.3, and the override behaviour in 5.4. No paid evaluation is needed.

## 7. Left to the builder

- Endpoint paths, payloads and error codes; the exact state fields for the manual version, overrides and edit log (keeping Section 5.4's contract).
- How the card search is laid out on phone width, and its result limit.
- The wording of the quiet lines and the confirmation dialog (both languages).
- Whether to refactor `present_plan` into a shared plan-card builder in this plan's first task.

## 8. To the carryover

- A list of versions, comparing them, and undo: later, if the user asks.
- Regenerating the note for an edited plan: later, if wanted (a model call).
