# Phase 1 Plan 3a: Manual Plan Edits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user remove dishes from the newest plan card, add dishes found by a search inside the card, confirm H2 conflicts per diner and rule, see new conflicts on dishes already in the plan, and restore the model's plan — all applied and checked by code, kept in the conversation state.

**Architecture:** A shared plan-card builder is extracted from `present_plan`. A new edit module applies one edit to the thread's current plan (validate with overrides, classify findings, rebuild the card) without any planning model. New endpoints under the thread read and write the checkpointed state with `update_state` and return the new card. The frontend plan card gets remove/add/search/confirm/keep/restore controls on the newest card.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, LangGraph 1.x (backend); Next.js 16, React 19, next-intl, plain CSS (frontend); pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-11-19-phase-1-plan-3a-manual-edits-design.md` (section numbers below refer to it). Rules from `docs/design.md` 7.5 and 5.5.

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository; each "User commit" step gives the commands and stops. Branch: `feat/phase-1-plan-3-plan-revision` (exists).
- **Packages:** none new. **Prompts:** no prompt file changes; no paid evaluation in this plan.
- **Python:** `$PY` is `python.defaultInterpreterPath` from `.vscode/settings.json`; run backend commands from `backend/` with `PYTHONNOUSERSITE=1 PYTHONUTF8=1`. Backend suite: `$PY -m pytest -q`.
- **Frontend checks** through the PowerShell tool only: `cd frontend; npm test; npm run lint; npm run build` (the first build may stop on a Next.js security-advisory check after compiling; rerun once; never run `next upgrade`).
- **Secrets:** never read, print, create, edit or delete `.env`. **Databases:** tests use the test database only, never call a real model, never assume `llm_calls` or `planning_runs` is empty, delete only rows they create.
- **Language:** every repository file in English; Chinese only in UI translations (`frontend/messages/zh.json`), dictionary names, and Chinese request text in tests. Every user-facing string in both `en.json` and `zh.json`.
- **No migration.** Edit state lives in the checkpointer (spec 3).
- **No planning model call in an edit.** The card search may embed its query through the call layer (spec 2, anti-goals).
- **Hard constraints in code; no H2-conflicting dish enters the plan unconfirmed** (spec 5.2).
- **Never show a label ("Diner N") or send a saved nickname to a model** on the new paths (plan 2b rules): conflict texts name diners by display name, the user as "你" / "you", guests by their name.
- **Planning records and metrics are not changed by edits** (spec 5.5).
- **Carryover:** after each task, copy deferred review findings into `docs/phase-1-carryover.md` under a new section "Deferred from Phase 1 plan 3a", each tagged with its Phase 1 step.

## Review Focus

1. Two quick edits on a slow connection, or an edit sent while a run is streaming: the second edit sees the first's result; an edit during a run or a pending card is refused with `thread_busy` and nothing changes — Task 3 test.
2. A thread saved before 3a (plan without `edit` fields, or no plan at all, or an older card that is not the newest): editing an older card is refused (`not_newest_plan`), the newest one works, `normalizePlan` keeps old plans rendering — Task 3 and Task 4 tests.
3. A rule added on the People page after planning, then a plain remove: the remaining dish that now conflicts is marked with the reason and kept until "keep" or "remove"; nothing is silently dropped — Task 2 test.
4. Adding a dish whose only conflict is an unknown-allergen ingredient while one diner has an allergy: the confirmation offers the allergen panel; after confirming the dish is added with an `unknown_allergens` override that covers every diner — Task 2 test.
5. Restore after a recipe was edited in the library: the restored dishes are checked with today's data; a dish deleted from the library meanwhile is dropped with a quiet note instead of an error — Task 2 test.

---

### Task 1: Shared plan-card builder

**Files:**
- Create: `backend/minemydinner/agent/plan_card.py`
- Modify: `backend/minemydinner/agent/graph.py` (`present_plan`, ~1130-1270: use the builder for the dishes branch)
- Test: `backend/tests/test_plan_card.py`

**Interfaces:**
- Produces: `build_dishes_card(session: Session, ctx: PlanningContext, *, recipe_ids: list[int], validation: dict, base: dict, locale: str) -> dict` — returns a plan dict with the same keys `present_plan` builds today (`recipes`, `missing`, `missing_cookware`, `warnings`, `servings_notes`, `unmet`, `next_actions`, `passed`, plus every key of `base` copied unchanged: `defaults_used`, `spec`, `headcount`, `left_out_unknown_allergens`, `left_out_details`, `semantic_paused`, `diners`, `others`, `reasons`).
- Produces: `card_base(state_values: dict, session: Session) -> dict` — the non-dish keys above from a thread's state (spec, constraints headcount, defaults_used, search notes, diners line).

- [ ] **Step 1: Write the failing tests** in `test_plan_card.py`: `test_build_dishes_card_matches_present_plan_shape` (a seeded 2-dish plan gives the same keys and values `present_plan` produced for it, with `passed` True and `unmet` empty); `test_build_dishes_card_failed_validation_lists_unmet` (a validation with an H4 violation gives `passed` False, one `unmet` with code H4, and next actions).
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_plan_card.py` — expected FAIL (module missing).
- [ ] **Step 3: Implement** the two functions by moving the dishes branch of `present_plan` (rows, `compute_missing`, `missing_cookware`, recipes list, warnings, servings notes, unmet and next actions) into `build_dishes_card`; `present_plan` calls it. Behaviour of `present_plan` must not change.
- [ ] **Step 4: Run** the new tests and `$PY -m pytest -q tests/test_agent_graph.py` — expected PASS (no regression).
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/plan_card.py backend/minemydinner/agent/graph.py backend/tests/test_plan_card.py
git commit -m "refactor: shared plan-card builder for present_plan and manual edits"
```

### Task 2: Applying an edit (backend logic, no API)

**Files:**
- Create: `backend/minemydinner/agent/edits.py`
- Modify: `backend/minemydinner/planning/search.py` (`search_recipes`: a `show_blocked: bool = False` parameter)
- Test: `backend/tests/test_agent_edits.py`, `backend/tests/test_planning_search.py`

**Interfaces:**
- Consumes: `build_dishes_card`, `card_base` (Task 1); `validate_plan(session, ctx, spec, recipe_ids, overrides)` and `H2Override` (`planning/validate.py`); `load_planning_context`.
- Produces (all in `edits.py`):
  - `class Conflict(BaseModel)`: `recipe_id: int`, `diner_key: str` (`"diner:<id>"` or `"guest:<n>"`), `diner_name: str | None` (display name; None for the user), `is_self: bool`, `kind: Literal["allergen","ingredient","text","unknown_allergens"]`, `allergen: str | None`, `ingredient_id: int | None`, `ingredient_name: str | None`, `text: str | None`, `rule_type: str | None`, `lines: list[str]` (the unknown-allergen ingredient names).
  - `class EditOutcome(BaseModel)`: `plan: dict | None` (the new card; None when confirmation is needed), `needs_confirmation: list[Conflict]`.
  - `apply_edit(session, ctx, values: dict, action: EditAction, locale: str) -> tuple[EditOutcome, dict]` — returns the outcome and the state update (`current_plan`, `plan_versions`, `reply_plans`). `EditAction` is a tagged union: `Remove(recipe_id)`, `Add(recipe_id, confirm: list[Conflict])`, `Keep(recipe_id, confirm: list[Conflict])`, `Restore()`.
  - Edited plan keys added to the card dict: `edited: bool`, `model_version: int` (index in `plan_versions` of the model version it derives from), `edit: {"overrides": [H2Override dumps], "log": [entries], "conflicts": [Conflict dumps]}`, `count_notes: list[{"field": str, "stated": int, "now": int}]`.
  - `search_recipes(..., show_blocked=True)` keeps hard-conflict and unknown-allergen dishes in the hits and adds `conflicts: list[dict]` and `blocked_unknown: list[str]` to each hit.

Rules (spec 5.2-5.4), each with its test in `test_agent_edits.py`:

| Test | Assertion |
|---|---|
| `test_remove_starts_manual_version` | first remove on a model plan: `edited` True, `plan_versions` grows by 1, `model_version` points at the model entry |
| `test_second_edit_updates_same_version` | a following add or remove changes the last `plan_versions` entry in place (length unchanged) |
| `test_add_without_conflict` | the dish is appended; missing ingredients and cookware recomputed; no confirmation |
| `test_add_conflict_needs_confirmation` | adding a dish with a selected diner's restriction returns `plan` None and one `Conflict` naming that diner and rule; the state update is empty |
| `test_add_confirmed_records_override_and_log` | the same add with `confirm` adds the dish, stores one `H2Override` and one log entry `{action: "add", recipe_id, at, overrides: [{diner_key, allergen or ingredient_id}]}`; no names in the log |
| `test_confirmed_not_raised_again` | a later remove of another dish does not mark the confirmed dish |
| `test_new_conflict_on_existing_dish_is_marked` | after adding a restriction rule to a selected diner in the DB, a remove of another dish returns the existing dish in `edit.conflicts` (not removed), and the card `passed` is False |
| `test_keep_records_override` | `Keep(recipe_id, confirm)` clears that conflict and stores the override and a `keep` log entry |
| `test_remove_drops_overrides` | removing a confirmed dish deletes its overrides; adding it back needs confirmation again |
| `test_unknown_allergen_override_covers_dish` | with a diner allergy and an unlinked line, adding the dish asks with `kind` `unknown_allergens` and `lines`; confirmed, it is stored with `unknown_allergens` True and is not asked again for another diner |
| `test_duplicate_add_refused` | adding a dish already in the plan raises `EditError("already_in_plan")` |
| `test_counts_note_only_for_stated` | removing a dish below a stated `dishes` count gives `count_notes` `[{field: "dishes", stated: 3, now: 2}]`; with `dishes` in `defaults_used` it gives none |
| `test_h1_h3_h6_h7_are_warnings` | an added dish missing stock, cookware, over the time limit, or dropping a must-use name leaves `passed` True with these in `warnings`, not `unmet` |
| `test_restore_rechecks_today` | restore returns the model version's dishes rebuilt with today's checks, `edited` False, overrides and log dropped; a conflict new since planning is marked |
| `test_restore_drops_deleted_recipe` | a model dish deleted from the library meanwhile is left out and a quiet note `{"field": "deleted_recipes", ...}` is added (Review Focus 5) |
| `test_restore_no_plan_reply_as_recorded` | restoring a no-plan or early-stop model version returns it unchanged |
| `test_edit_on_no_plan_reply` | adding a dish to a reply without recipes makes an edited plan with that dish; the original reasons and next actions are not shown on the edited plan, which has its own checks (restore brings them back; Claude's call, the spec is silent once the first dish is added) |

- [ ] **Step 1: Write the failing tests** above, plus in `test_planning_search.py`: `test_show_blocked_keeps_conflicting_hits` (a restricted dish appears with one `conflicts` entry; without the flag it is left out as today).
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_agent_edits.py tests/test_planning_search.py` — expected FAIL.
- [ ] **Step 3: Implement** `edits.py`. Classify `validate_plan` findings: H2 on the dish being added → `needs_confirmation`; H2 on another dish with no covering override → `edit.conflicts`; H5 duplicate → `EditError`; H1, H3, H6, H7 → warnings; H4 → `count_notes` for fields not in `defaults_used`, else nothing. Diner names come from `diners` rows and the settled set's `guest_names`, never from labels. Conflict `diner_key` for `unknown_allergens` overrides is `"*"` (spec 5.4, the builder's consistent fill). `passed` on an edited plan = no unresolved `edit.conflicts`.
- [ ] **Step 4: Implement** `show_blocked` in `search_recipes`.
- [ ] **Step 5: Run** the tests — expected PASS; then the full backend suite — expected PASS.
- [ ] **Step 6: Update the carryover; user commit**

```powershell
git add backend docs/phase-1-carryover.md
git commit -m "feat: manual plan edits - remove, add with confirmation, keep, restore, checked by code"
```

### Task 3: Edit endpoints

**Files:**
- Create: `backend/minemydinner/api/plan_edits.py` (router mounted next to `api/chat.py`)
- Modify: `backend/minemydinner/main.py` (include the router)
- Test: `backend/tests/test_plan_edits_api.py`

**Interfaces:**
- Consumes: `apply_edit`, `EditAction`, `Conflict`, `EditError` (Task 2); `_snapshot`/`_config` patterns of `api/chat.py`; the runtime graph.
- Produces (all `POST`, body includes `locale: "zh"|"en"`):
  - `/api/chat/threads/{thread_id}/plan/search` `{query}` → `{hits: [{id, title, dish_type, missing, missing_cookware, conflicts, blocked_unknown, in_plan}]}`
  - `/api/chat/threads/{thread_id}/plan/remove` `{recipe_id}` → `{plan}`
  - `/api/chat/threads/{thread_id}/plan/add` `{recipe_id, confirm: Conflict[]}` → `{plan}` or `409 {code: "needs_confirmation", conflicts}`
  - `/api/chat/threads/{thread_id}/plan/keep` `{recipe_id, confirm: Conflict[]}` → `{plan}`
  - `/api/chat/threads/{thread_id}/plan/restore` `{}` → `{plan}`
  - Errors: `409 thread_busy` (state has `next` nodes or pending interrupts), `409 no_plan` (no reply with a plan), `422 already_in_plan`, `404 unknown_recipe`. The chat router's prefix is `/api/chat`.
- State writes: `graph.update_state(config, update, as_node="present_plan")`, so the thread's next step stays END.

- [ ] **Step 1: Write the failing tests:** `test_remove_returns_card_and_persists` (a second `GET /threads/{id}` shows the edited `current_plan` and the newest reply's plan in `items`); `test_add_needs_confirmation_409`; `test_add_confirmed_200`; `test_busy_thread_refused` (a thread with a pending diner card → `thread_busy`, state unchanged; Review Focus 1); `test_sequential_edits_build_on_each_other` (two removes in a row leave both removed; Review Focus 1); `test_old_thread_without_edit_fields` (a plan dict without `edited`/`edit` is editable; a thread with no plan → `no_plan`; Review Focus 2); `test_edits_do_not_touch_planning_runs` (row count and the run's `final_plan` unchanged); `test_search_marks_in_plan_and_conflicts`; `test_no_label_in_responses` (no `(?i)diner\s*\d+` in any response body).
- [ ] **Step 2: Run** — expected FAIL.
- [ ] **Step 3: Implement** the router: load the snapshot, refuse when busy, run `apply_edit` inside a session with the thread's label context restored for texts (plan 2b `with_labels` pattern), `update_state`, return.
- [ ] **Step 4: Run** the tests and the full backend suite — expected PASS.
- [ ] **Step 5: Update the carryover; user commit**

```powershell
git add backend docs/phase-1-carryover.md
git commit -m "feat: plan edit endpoints - search, remove, add, keep, restore"
```

### Task 4: Plan card editing UI

**Files:**
- Create: `frontend/src/lib/planEdits.ts` (API calls and types), `frontend/src/components/PlanEditor.tsx` (search panel, confirmation dialog)
- Modify: `frontend/src/components/PlanCard.tsx`, `frontend/src/components/MessageList.tsx`, `frontend/src/components/ChatPage.tsx`, `frontend/src/lib/chat.ts` (`ChatPlan` gains optional `edited`, `edit`, `count_notes`), `frontend/messages/en.json`, `frontend/messages/zh.json`, `frontend/src/app/globals.css`
- Test: `frontend/src/components/PlanCard.test.tsx`, `frontend/src/components/PlanEditor.test.tsx`

**Interfaces:**
- Consumes: Task 3 endpoints.
- Produces: `PlanCard` props `editable: boolean` and `onEdited(plan: ChatPlan): void`; `ChatPage` replaces the newest assistant item's plan with the returned card.

UI (spec 5.1): a remove button per dish; "+ 加一道菜" opens a search inside the card (query box, results with dish type, missing items, conflict markers, "已在方案中"); "加入" on a conflicting result opens the confirmation listing each diner and rule (and the 2b allergen panel for an unknown-allergen ingredient); a dish in `edit.conflicts` is red with the reason sentence ("规划之后有变化：小王 忌口 牛肉，这道菜含牛肉" / "这道菜里的 花椒油 过敏原未知，而这顿有人过敏") and "确认保留" / "删除"; after the first edit "已手动修改" and "恢复模型方案"; the model note above the card gets "以上说明针对原方案"; one quiet line "继续对话会重新规划，手动修改不会带入"; `count_notes` as a quiet line "和你的要求不同：要求 3 道菜，现在 2 道"; controls disabled when the card is not the newest, while busy, or while a card or question is pending. Only the newest card is editable; no-plan replies show "+ 加一道菜" too.

- [ ] **Step 1: Write the failing tests:** remove calls the endpoint and renders the returned card; add without conflict; add with 409 opens the dialog and confirms with the conflicts; a red conflict shows the reason and keep/remove call the endpoints; restore appears only when `edited`; the count note shows for stated counts only (from the returned `count_notes`); an older card and a busy state show no edit controls; a no-plan reply shows "+ 加一道菜"; an old plan without `edited` renders unchanged (Review Focus 2); zh texts for the reason sentences.
- [ ] **Step 2: Run** `cd frontend; npx vitest run src/components/PlanCard.test.tsx src/components/PlanEditor.test.tsx` — expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test; npm run lint; npm run build` — expected all pass.
- [ ] **Step 5: Update the carryover; user commit**

```powershell
git add frontend docs/phase-1-carryover.md
git commit -m "feat: plan card editing - remove, search and add, confirm conflicts, keep, restore"
```

### Task 5: Browser check with the user, and docs

**Files:**
- Modify: `docs/design.md` 7.5 (one line: edits live in the conversation until 3c saves; restore rechecks), `docs/superpowers/specs/2026-11-19-phase-1-plan-3a-manual-edits-design.md` 5.6 (`plan_versions`: append on the first edit, update in place after, matching 5.3), `docs/phase-1-carryover.md`

- [ ] **Step 1:** Start the servers on the test database (frontend on 3400 if 3000 is reserved, backend with `ALLOWED_ORIGINS` for that origin), seeded with a library, friends and stock; tell the user the check makes a few cents of paid planning calls.
- [ ] **Step 2:** With the user: remove two dishes and add two; add a dish that breaks a friend's rule (confirm dialog); add a rule on the People page, then remove another dish (red mark with reason, keep and remove); restore; a no-plan reply built by hand; a new message after edits (re-plans, edits not carried). Fix what the user finds within this task.
- [ ] **Step 3:** Stop the servers, clear the test database, run the full backend suite and the frontend checks.
- [ ] **Step 4: Update the docs and carryover; user commit**

```powershell
git add frontend backend docs
git commit -m "docs: plan 3a browser check fixes and docs"
```
