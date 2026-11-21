# Phase 1 Plan 3b-1b: Revision Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revising the plan in the conversation follows the user's words (drop versus replace, add only, one-meal wishes, same-type swaps), the next-action buttons change the request in code, a new plan with an unmeetable taste wish still gives a plan and says so, and what each model sees is documented.

**Architecture:** The revise step's change list marks each removed dish as drop or replace and gains an add-only kind; code derives the attempt's counts from the change (unless the message states a count) and checks type pairing, add-only, and empty plans. `parse_v7` separates one-meal avoidances (`meal_avoid`, applied as a meal rule) from lasting rules. Button messages build their request in code from the card that offered them and either re-run the revision or plan anew. `planner_v5` reports whether a taste wish was met, and code falls back to the shortlist when the planner gives no answer.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, LangGraph 1.x (backend); Next.js 16, React 19, next-intl (frontend); pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-11-21-phase-1-plan-3b1b-revise-fixes-design.md` (section numbers below refer to it). Plan 3b-1's spec and code are the base.

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository; each "User commit" step gives the commands and stops. Branch: `feat/phase-1-plan-3b1b-revise-fixes` (exists). Implementer subagents never stage or commit.
- **Packages:** none new.
- **Prompts:** never edit a prompt version any record or evaluation run used (`route_v3`, `parse_v6`, `planner_v4`, `revise_v2` are frozen). New files: `parse_v7.md`, `revise_v3.md`, `planner_v5.md`; `route_v4.md` only if Task 10 decides it. Once a new version has been used by a run, a further change makes the next version. `docs/prompt-versions.md` lists every new version.
- **Paid runs:** Tasks 10 and 11 call real models; before each run give the user the estimated cost and wait for approval; at most three development runs in Task 10. Every evaluation and check uses the test database only.
- **Python:** `$PY` is `python.defaultInterpreterPath` from `.vscode/settings.json`; run backend commands from `backend/` with `PYTHONNOUSERSITE=1 PYTHONUTF8=1`. Backend suite: `$PY -m pytest -q`.
- **Frontend checks** through the PowerShell tool only: `cd frontend; npm test; npm run lint; npm run build` (the first build may stop on a Next.js security-advisory check; rerun once; never run `next upgrade`).
- **Secrets:** never read, print, create, edit or delete `.env`. Tests use the test database only, never call a real model, never assume `llm_calls` or `planning_runs` is empty.
- **Language:** every repository file in English; Chinese only in UI translations, dictionary names, Chinese request text in tests, prompt examples and scenarios. Reply sentences written by code in both languages.
- **Labels:** never send a saved nickname to a model and never show "Diner N".
- **Hard constraints in code:** counts, locks, quotes, change size, type pairing, dietary rules (including `meal_avoid`), stock and cookware are checked by code (spec 3).
- **Held-out set:** the new file `backend/eval_data/planning/scenarios_revise_heldout2.yaml` is written by the user in Task 1 and frozen; no implementer or reviewer subagent opens it; no held-out sentence appears in a prompt, a test, or a development scenario; no change is made in response to a held-out result within this plan. The 3b-1 file `scenarios_revise_heldout.yaml` becomes a regression set in Task 1 and may be read from then on.
- **Records:** every revision and button turn is a planning run with all its events; a button turn is marked `request["button"] = "<action>"`.
- **Carryover:** after each task, copy deferred review findings into `docs/phase-1-carryover.md` under a new section "Deferred from Phase 1 plan 3b-1b", and mark items of the "Plan 3b-1b" block closed when a task fixes them.

## Review Focus

1. A bare drop of the only dish of a type ("汤不要了" with one soup): the revised plan has 0 soups and no H4 failure, the reply says "去掉了…" — Task 3.
2. "全都不要了" or a scope covering every dish: nothing changes and the reply asks whether to plan a new meal; no empty plan is ever stored — Task 2 and Task 3.
3. A button pressed on a failed-revision card in a thread saved before this plan (no per-card request): falls back to parsing the message, no crash — Task 6.
4. "今天不想吃肉" on a plan whose only vegetable dish is locked and whose meat dish is locked: the locked meat dish is replaced (named by the avoidance) and the reply names it; the locked vegetable dish stays — Task 5.
5. A new plan with a taste wish where the planner hits its call limit on every attempt: a plan from the shortlist with the wish-unmet sentence, never "0 dishes" — Task 7.

---

### Task 1: New held-out batch and the regression set (user gate)

**Files:**
- Create: `backend/eval_data/planning/scenarios_revise_heldout2.yaml`
- Modify: `backend/eval_data/planning/scenarios_revise_heldout.yaml` (only the expectations the user approves; its header becomes a regression-set header)
- Modify: `docs/planning-evaluation-history.md` (the table of all scenario sets at the top, spec 5.9)

**Interfaces:**
- Produces: the new held-out file in the same keys as `scenarios_revise.yaml` (the runner's keys from plan 3b-1 Task 7), using `library_revise2.yaml` (`library_revise.yaml` unchanged plus eight dishes the sentences need; `library_revise.yaml` stays frozen for the regression set). New keys Task 9 adds may be used only after Task 9; until then the user writes expectations in the existing keys or in plain notes Claude turns into keys in Task 9.
- Produces: the scenario-set table (columns: file, library, scenarios, author, what it tests, role: development / regression / held-out, role history).

- [ ] **Step 1: List the regression set's conflicts.** Claude reads `scenarios_revise_heldout.yaml` and lists every scenario whose expectation this design changes (spec 5.1 drops, 5.4 one-meal wishes and locked dishes, 5.5 buttons), with the proposed new expectation. Give the list to the user; edit only what the user approves.
- [ ] **Step 2: Ask the user for the new held-out sentences** (15-20, the kinds in spec 5.9), each with its expected result. Stop until they arrive. Write the file: one scenario per sentence, the text exactly as written, header "Held-out set written by the user on <date>. Not used for tuning in plan 3b-1b; do not edit."
- [ ] **Step 3: Write the scenario-set table** at the top of `docs/planning-evaluation-history.md` for every `scenarios_*.yaml` (development: may tune; regression: run every round, misses explained; held-out: never tune), including the 3b-1 held-out set's role change.
- [ ] **Step 4: Validate** both YAML files load: `$PY -c "import yaml; [yaml.safe_load(open(f, encoding='utf-8')) for f in ('eval_data/planning/scenarios_revise_heldout.yaml', 'eval_data/planning/scenarios_revise_heldout2.yaml')]"` — no error.
- [ ] **Step 5: User commit**

```powershell
git add backend/eval_data/planning/scenarios_revise_heldout2.yaml backend/eval_data/planning/scenarios_revise_heldout.yaml docs/planning-evaluation-history.md
git commit -m "eval: plan 3b-1b held-out batch, 3b-1 held-out set becomes a regression set, scenario-set table"
```

### Task 2: The change list v2 and its checks (pure code)

**Files:**
- Modify: `backend/minemydinner/agent/revision.py`
- Modify: `backend/minemydinner/agent/revise_step.py` (`CATEGORY_WORDS`)
- Test: `backend/tests/test_agent_revision.py`

**Interfaces:**
- Produces: `RemovedDish.mode: Literal["drop", "replace"] = "replace"` (the default keeps stored 3b-1 proposals valid). Field description: drop when the words only say not to have the dish; replace when they want another dish in its place ("换 / 换成 / 换一个 / 来个 / 再要一道").
- Produces: `RevisionProposal.target` gains `"add"` (a message that only adds dishes: `remove` must be empty).
- Produces: `RemovedDish.replace_with: int | None = None` — the id of the dish the user named as the replacement ("用西红柿炒鸡蛋换掉鱼香肉丝"); it must also be in `add`, and its quote (the named dish's words) must be in the message.
- Produces: new problem codes on `RevisionProblem.code`: `"R7"` a replacement of another dish type (allowed only for a dish in `avoid_ids`, or when the replacement is the dish the user named in `replace_with`, spec 5.2); `"R8"` an `add` answer that removes a dish; `"R9"` a change that leaves no dish.
- Produces: `check_revision(plan, proposal, message, dish_facts, *, spec=None, avoid_ids: Collection[int] = ()) -> list[RevisionProblem]`. `avoid_ids` are the plan's dishes that break this meal's `meal_avoid` (Task 5 computes them): they count as named (no quote needed, locked allowed, outside the R4 limit) and may be replaced by another dish type. Pairing rule for R7: replacements are matched to the `mode == "replace"` removals by dish type; every unmatched added dish is an extra (allowed only when the message states a count, `spec` given, or under `add`); `DishFacts` gains `dish_type: str`.
- Produces: `attempt_counts(plan: dict, proposal: RevisionProposal, dish_types: dict[int, str]) -> dict[str, int]` — the category counts of the revised plan (`count_categories` over the revised dish types); Task 3 uses it as the attempt's exact counts when the message states no count.
- Produces: `CATEGORY_WORDS["staple food"]` = `["主食", "面", "饭", "粥", "粉", "staple", "noodles", "rice", "porridge"]`; `quote_matches` also accepts a one-character quote equal to the title's last character.
- Produces: words that point at the dishes that do not suit name them (spec 5.4): `dish_facts` adds `CONFLICT_WORDS` = `["不适合", "不合适", "不能吃", "doesn't suit", "not suitable"]` and the conflicting ingredient or allergen name to the words of every dish with an open conflict or a kept override, so a quote such as "不适合的菜" matches those dishes (locked ones included) and no other.

- [ ] **Step 1: Write the failing tests** in `test_agent_revision.py`:
  - `test_drop_needs_a_quote_and_counts_go_down` — "茄子这道不要" removes 红烧茄子 with `mode="drop"`, quote "茄子这道" → no problem; `attempt_counts` shows one vegetable dish fewer.
  - `test_replace_must_keep_the_dish_type` — `mode="replace"` of a vegetable dish with a meat dish → `["R7"]`; with a vegetable dish → `[]`.
  - `test_named_replacement_may_change_type` — "用西红柿炒鸡蛋换掉鱼香肉丝": remove 鱼香肉丝 (`mode="replace"`, `replace_with` = 番茄炒蛋's id) and add 番茄炒蛋 → `[]`; the same swap without `replace_with` → `["R7"]`.
  - `test_avoid_ids_allow_another_type_and_locked_dishes` — a locked meat dish in `avoid_ids`, target `general`, replaced by a vegetable dish with no quote → `[]`; the same dish not in `avoid_ids` → R3 and R7.
  - `test_add_only_removes_nothing` — target `add` with one removal → `["R8"]`; with only `add` → `[]`.
  - `test_a_change_leaving_no_dish_is_refused` — dropping every dish of a 3-dish plan → contains `"R9"`.
  - `test_staple_quotes` — quote "面" matches 清汤面 (category word and last character); quote "饭" matches 白米饭; quote "面" does not match 番茄炒蛋.
  - `test_old_proposals_still_load` — a 3b-1 proposal dict without `mode` validates with `mode == "replace"`.
  - `test_unsuitable_words_name_kept_conflict_dishes` — a locked egg dish with a kept override, quote "不适合的菜" under `named` → `[]`; the same quote on a dish with no conflict → R2.
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_agent_revision.py` — expected FAIL on the new tests.
- [ ] **Step 3: Implement** the fields, codes, `attempt_counts`, the `avoid_ids` rules and the quote change in `revision.py` / `revise_step.py`. Problem texts in English (they go to the retry message).
- [ ] **Step 4: Run** the revision tests — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/revision.py backend/minemydinner/agent/revise_step.py backend/tests/test_agent_revision.py
git commit -m "feat: change list v2 - drop or replace, add-only, type pairing, empty-plan refusal, staple quotes"
```

### Task 3: The revise step uses the change list v2 (`revise_v3`)

**Files:**
- Create: `backend/minemydinner/agent/prompts/revise_v3.md`
- Modify: `backend/minemydinner/agent/request.py` (`PROMPT_VERSIONS["revise"] = "revise_v3"`)
- Modify: `backend/minemydinner/agent/graph.py` (`check_revised`, `present_revision`), `backend/minemydinner/agent/revise_step.py` (`count_fix_hint`, reply sentences)
- Modify: `docs/prompt-versions.md`
- Test: `backend/tests/test_agent_revise.py`

**Interfaces:**
- Consumes: Task 2's `check_revision(..., avoid_ids=...)`, `attempt_counts`, R7-R9.
- Produces: in `check_revised`, the attempt's spec: when the parse's `counts_stated_now` is true the revision spec stands (3b-1); otherwise the counts are `attempt_counts(...)` as exact counts. The type pairing (R7) and add-only (R8) checks replace the old size guarantee.
- Produces: an R9 answer (or `scope`/`named` covering every dish) ends without a change and the reply is the fixed question `EMPTY_PLAN_QUESTION_ZH` = "这样就一道菜都不剩了。要重新规划一顿吗？" / `EMPTY_PLAN_QUESTION_EN` = "That would leave no dish. Shall I plan a new meal?" (plan unchanged, the run finished not passed, no retry).
- Produces: `count_fix_hint(detail, replaceable: dict[str, list[tuple[int, str]]])` — after the side text, "Replace one of these <type>: <title> (id N), …" listing the unlocked dishes of the type that is over.
- Produces: `changed_text` gains drops: "去掉了X。" / "Removed X." for `mode="drop"` (already supported for remove-only), and a locked dish changed by an avoidance or by "不适合的菜" is named: "X是你手动加的，因为……换成了Y。" (wording left to the implementer, both languages).

- [ ] **Step 1: Write the failing tests** (scripted models, `tests/agentfakes.py`):
  - `test_bare_drop_shrinks_the_plan` — a 2 meat + 1 vegetable + 1 soup plan, proposal drops the soup (`mode="drop"`, quote "汤") with no count stated → passed; the new version has 3 dishes and 0 soups; reply starts "去掉了…".
  - `test_replace_keeps_the_size` — "茄子换成别的": replace with a vegetable dish → 4 dishes.
  - `test_add_only_adds` — target `add` "加个青菜" → one more vegetable dish, nothing removed.
  - `test_wrong_type_is_retried_with_the_dishes_named` — first answer replaces a vegetable dish with a meat dish → retry message contains R7's text and the names of the replaceable vegetable dishes; second answer fixes it → passed.
  - `test_empty_plan_asks` — a drop of every dish → plan and versions unchanged, reply equals `EMPTY_PLAN_QUESTION_ZH`, no retry.
  - `test_stated_count_still_wins` — "4个菜就够了" with `counts_stated_now` true on a 5-dish plan → the stated count is checked as in 3b-1.
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_agent_revise.py` — expected FAIL on the new tests.
- [ ] **Step 3: Write `revise_v3.md`** = `revise_v2` plus: drop versus replace with the user's table (spec 5.1, examples from the spec only); the add-only kind; a one-meal avoidance's dishes are named and may be replaced by another type (spec 5.4); "不适合的菜" names the dishes with a conflict, kept ones included; an asked-for dish that is not in the library is answered as nothing fits with no search loop (spec 5.2); the note never uses internal words ("锁定", LOCKED, OPEN CONFLICT, ids) and says only why the new dishes fit the wish (spec 5.7). Update the field descriptions to match; `REVISE_TOOLS_VERSION` changes (note it in `docs/prompt-versions.md`).
- [ ] **Step 4: Implement** the attempt spec, R9 handling, the hint and the reply sentences.
- [ ] **Step 5: Run** the revise, revision and graph tests — expected PASS; then the full backend suite.
- [ ] **Step 6: User commit**

```powershell
git add backend/minemydinner/agent/prompts/revise_v3.md backend/minemydinner/agent/request.py backend/minemydinner/agent/graph.py backend/minemydinner/agent/revise_step.py backend/tests/test_agent_revise.py docs/prompt-versions.md
git commit -m "feat: revise_v3 - drops shrink the plan, add-only, same-type swaps with named dishes in the retry, empty-plan question"
```

### Task 4: Quiet count note and card leftovers

**Files:**
- Modify: `backend/minemydinner/agent/edits.py` (`_count_notes`), `backend/minemydinner/agent/graph.py` (failed / unclear / nothing-fits cards)
- Test: `backend/tests/test_agent_edits.py`, `backend/tests/test_agent_revise.py`

**Interfaces:**
- Consumes: `plan_request` (3b-1 final fix).
- Produces: `_count_notes` decides stated counts from `values["plan_request"]` (falling back to `values["request"]` for older threads), closing the plan 3a carryover item.
- Produces: a failed, unclear or nothing-fits reply card drops dishes that left the library and carries the `deleted_recipes` note, as a passed revision does.

- [ ] **Step 1: Write the failing tests**: `test_count_note_reads_the_plans_request` (a later turn ends unclear with a different parse; the edited card's count note is unchanged); `test_failed_card_drops_a_deleted_dish` (a dish deleted before a nothing-fits turn is absent from the reply card, with the note).
- [ ] **Step 2: Run** them — expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the edits and revise tests — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/edits.py backend/minemydinner/agent/graph.py backend/tests/test_agent_edits.py backend/tests/test_agent_revise.py
git commit -m "fix: count notes read the plan's own request; failed revision cards drop deleted dishes"
```

### Task 5: One-meal wishes versus lasting rules (`parse_v7`, `meal_avoid`)

**Files:**
- Create: `backend/minemydinner/agent/prompts/parse_v7.md`
- Modify: `backend/minemydinner/agent/request.py` (`ParsedRequest.meal_avoid`, `PROMPT_VERSIONS["parse"] = "parse_v7"`, `apply_defaults` passes it on)
- Modify: `backend/minemydinner/agent/graph.py` (constraints for this meal; `avoid_ids` for `check_revision`)
- Modify: `docs/prompt-versions.md`
- Modify: `backend/minemydinner/agent/edits.py` (`_conflicts` for `diner_key="meal"`), `frontend/src/components/PlanCard.tsx`, `frontend/messages/en.json`, `frontend/messages/zh.json`
- Test: `backend/tests/test_agent_request.py`, `backend/tests/test_agent_graph.py`, `backend/tests/test_agent_revise.py`, `frontend/src/components/PlanCard.test.tsx`

**Interfaces:**
- Produces: `ParsedRequest.meal_avoid: list[str] = []` — ingredient names or groups to keep out of this meal only (never taste words; spec 5.4), at most 10. Stored requests without it load as `[]`.
- Produces: the meal's constraints gain one `RuleRef` per `meal_avoid` name with `diner_key="meal"`, `rule_type="dietary restriction"`, the ingredient resolved by the dictionary (`ingredient_id`) or else `text`; search and `validate_plan` apply it like a guest's meal-only rule; it never reaches a profile, never shows the diner card, and is shown on the card as this meal's wish (not as a person's rule).
- Produces: on a revise turn, `avoid_ids` = the plan's dishes with an H2 finding for `diner_key="meal"`; they are passed to `check_revision` (Task 2) and named in the revise opening (for example "BREAKS THIS MEAL'S WISH").
- Produces: `meal_avoid` joins the revision spec like the other conditions (stated and different → replaces; null never clears).
- Produces: a conflict with `diner_key="meal"` (a hand-added dish that breaks this meal's wish) is shown on the card as this meal's wish, not as a person: `Conflict.kind` unchanged, `diner_key="meal"`, and the frontend text "这顿不要{name}" / "Not this meal: {name}" (both message files). It can be kept with "确认保留" like any conflict.

- [ ] **Step 1: Write the failing tests**:
  - `test_meal_avoid_is_not_a_rule` — a parse with `meal_avoid ["肉"]` and no `self_rules` on a revise turn → no diner card interrupt; the revise step runs.
  - `test_meal_avoid_replaces_meat_dishes_keeping_the_size` — "今天不想吃肉" on 2 meat + 1 vegetable + 1 soup → scripted proposal replaces both meat dishes with a vegetable dish and a soup → passed, 4 dishes, no meat.
  - `test_meal_avoid_changes_a_locked_dish_and_says_so` — the meat dish was added by hand → replaced; the reply names it.
  - `test_lasting_rule_still_shows_the_card` — `self_rules` avoid 肉 on a revise turn → the diner card, then a new plan (3b-1 behaviour).
  - `test_meal_avoid_applies_to_a_new_plan` — a first message with `meal_avoid ["虾"]` → no shrimp dish passes validation; no profile row is written.
  - `test_old_request_without_meal_avoid_loads`.
  - `PlanCard.test.tsx`: `shows a meal wish conflict as this meal's wish` — a conflict with `diner_key "meal"` renders "这顿不要肉", never a person's name or "Diner".
- [ ] **Step 2: Run** them (backend with pytest; frontend via the PowerShell tool, `npx vitest run src/components/PlanCard.test.tsx`) — expected FAIL.
- [ ] **Step 3: Write `parse_v7.md`** = `parse_v6` plus the one-meal versus lasting rule with examples from spec 5.4 ("今天不想吃肉", "这顿不要辣的" → query, "西红柿太多了" → query, "我不吃肉" / "我对虾过敏" → lasting). Lines only added; a test pins `parse_v6` unchanged as for earlier versions.
- [ ] **Step 4: Implement** the field, the meal rule, `avoid_ids` and the opening marker.
- [ ] **Step 5: Run** the request, graph and revise tests, the full backend suite, and the frontend checks — expected PASS.
- [ ] **Step 6: User commit**

```powershell
git add backend/minemydinner/agent/prompts/parse_v7.md backend/minemydinner/agent/request.py backend/minemydinner/agent/graph.py backend/minemydinner/agent/edits.py backend/tests/test_agent_request.py backend/tests/test_agent_graph.py backend/tests/test_agent_revise.py frontend/src/components/PlanCard.tsx frontend/src/components/PlanCard.test.tsx frontend/messages/en.json frontend/messages/zh.json docs/prompt-versions.md
git commit -m "feat: parse_v7 - one-meal avoidances apply to this meal only, never as a person's rule"
```

### Task 6: Next-action buttons in code

**Files:**
- Create: `backend/minemydinner/agent/actions.py`
- Modify: `backend/minemydinner/agent/graph.py` (`route_intent`, `choose`, the card's stored request), `backend/minemydinner/agent/state.py`
- Test: `backend/tests/test_agent_actions.py`, `backend/tests/test_agent_graph.py`, `backend/tests/test_planning_eval.py` (the button scenario's model-call count)

**Interfaces:**
- Produces: `apply_action(request: dict, action: str) -> dict` in `actions.py` (pure): `switch_to_shopping` → `mode="shopping"`; `fewer_dishes` → `dishes` one fewer, never below 1, a named side above the new total lowered to it; `drop_time_limit` → `time_limit_min=None`; `relax_split` → both sides and bounds `None`, the total kept (from the spec when the request had none); `relax_named` → `must_use` and `all_use` moved into `prefer_use`. Every changed request has `counts_stated_now` set so the revision keeps the counts it states.
- Produces: every reply card stores the request it was built from (`card["request"]`, the parsed request dict of that turn).
- Produces: a button turn (3b-1's `_is_action_message`) on a card with `request`: no route, no parse; the request is `apply_action(card["request"], action)`; if the newest card is an unchanged revision card (failed, unclear or nothing fits) the turn runs `start_revision` on the current plan with that request; otherwise it plans from scratch (`load_context`). The run records `request["button"] = action`. A card without `request` falls back to 3b-1 behaviour (parse the message).

- [ ] **Step 1: Write the failing tests**:
  - `test_apply_action_each` — one assertion per action on a request with `mode="fridge_only"`, `dishes=3`, `meat_dishes=2`, `time_limit_min=30`, `all_use=["鸡蛋"]` (`fewer_dishes` → dishes 2 and meat 2; called twice from 2 → 1 and meat 1; from 1 → 1).
  - `test_button_on_failed_revision_revises_again` — fridge-only plan with a hand-added dish, "想吃点海鲜" ends nothing-fits, then "允许购物" → no route or parse call, `start_revision` runs with `mode="shopping"` and `query` kept, the hand-added dish stays locked.
  - `test_button_on_a_new_plan_card_plans_from_scratch` — an early-stop card, "荤素都行" → `load_context`, no parse, sides cleared, no ask-first question.
  - `test_button_on_an_old_card_parses` — a card without `request` → the parse runs as in 3b-1.
- [ ] **Step 2: Run** them — expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests, the graph tests, `tests/test_planning_eval.py`, `tests/test_chat_api.py` — expected PASS (update the eval test's model-call count with a comment).
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/actions.py backend/minemydinner/agent/graph.py backend/minemydinner/agent/state.py backend/tests/test_agent_actions.py backend/tests/test_agent_graph.py backend/tests/test_planning_eval.py
git commit -m "feat: next-action buttons change the card's request in code and revise again or plan anew"
```

### Task 7: A new plan with an unmeetable taste wish (`planner_v5`)

**Files:**
- Create: `backend/minemydinner/agent/prompts/planner_v5.md`
- Modify: `backend/minemydinner/agent/planner.py` (`PlanProposal`), `backend/minemydinner/agent/request.py` (`PROMPT_VERSIONS["planner"] = "planner_v5"`), `backend/minemydinner/agent/graph.py` (`check_plan`, `present_plan`)
- Modify: `docs/prompt-versions.md`
- Test: `backend/tests/test_agent_graph.py`

**Interfaces:**
- Produces: `PlanProposal.wish_met: bool | None = None` and `PlanProposal.wish_unmet_reason: str = ""` (descriptions: whether the request's taste wish, `The user feels like: …`, is met by the chosen dishes; None when the request has no wish). `TOOLS_VERSION` changes.
- Produces: when the passed plan has `wish_met is False`, the reply leads with `WISH_UNMET_NOTE_ZH` = "菜谱库里没有符合“{wish}”的菜，先按其他要求给你配了这份。" / `WISH_UNMET_NOTE_EN` = "Nothing in your library fits “{wish}”, so this plan follows your other requests." then the model's note; the card gets unmet `wish_unmet` and next actions `wish_unmet_actions(spec)` (3b-1).
- Produces: when an attempt ends with no proposal, code builds a fallback proposal from the shortlist's makeable dishes in shortlist order that fill the spec's counts, validates it like any attempt, and presents it with the wish-unmet note when the request has a wish. The text "方案有 0 道菜" is never produced for a missing answer.

- [ ] **Step 1: Write the failing tests**:
  - `test_unmet_wish_plans_and_says_so` — scripted planner answers `wish_met=False` with 2 dishes → passed card, reply starts with the wish-unmet sentence, next actions include `import_recipes`.
  - `test_no_answer_falls_back_to_the_shortlist` — the planner never answers (limit hit on every attempt) → a plan from the shortlist, the wish-unmet sentence, no "0 道菜" in the reply or unmet texts.
  - `test_hard_shortfall_still_stops` — an infeasible count still stops with reasons (2a behaviour).
- [ ] **Step 2: Run** them — expected FAIL.
- [ ] **Step 3: Write `planner_v5.md`** = `planner_v4` plus: a taste wish is a wish, not a requirement; when nothing fits, answer with the best plan for the other conditions and `wish_met=false` with a short reason, instead of searching on.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run** the graph tests and the full backend suite — expected PASS.
- [ ] **Step 6: User commit**

```powershell
git add backend/minemydinner/agent/prompts/planner_v5.md backend/minemydinner/agent/planner.py backend/minemydinner/agent/request.py backend/minemydinner/agent/graph.py backend/tests/test_agent_graph.py docs/prompt-versions.md
git commit -m "feat: planner_v5 - an unmet taste wish still gives a plan and says so; a missing answer falls back to the shortlist"
```

### Task 8: What each model sees (document)

**Files:**
- Create: `docs/planner-what-each-model-sees.md`, `docs/private/planner-what-each-model-sees.zh.md` (git-ignored, not committed)
- Modify: `docs/planner-request-and-counts.md`, `docs/planner-shortlist-and-ranking.md`, `docs/planner-feasibility-check.md`, `docs/planner-stock-rules.md` (one pointer line each; and the request-and-counts sections the tasks above changed: drops, `meal_avoid`, buttons)

**Interfaces:**
- Consumes: the code after Tasks 2-7.

- [ ] **Step 1: Generate the examples from the code**: a test-only script (not committed) builds, for the conversation "今晚两个人，三个菜，可以买菜" → a plan → "茄子这道不要", the exact route, parse, planner and revise inputs with fictional dishes from `library_revise.yaml` (no real model call).
- [ ] **Step 2: Write the document**: spec 5.8's four principles, the table, and one complete example input per model; "never receives" per model.
- [ ] **Step 3: Write the Chinese copy** and the pointer lines; update `docs/planner-request-and-counts.md` for drops, `meal_avoid` and buttons.
- [ ] **Step 4: User commit**

```powershell
git add docs/planner-what-each-model-sees.md docs/planner-request-and-counts.md docs/planner-shortlist-and-ranking.md docs/planner-feasibility-check.md docs/planner-stock-rules.md
git commit -m "docs: what each model sees, with complete examples"
```

### Task 9: Evaluation runner and the development set

**Files:**
- Modify: `backend/minemydinner/evaluation/planning.py`, `backend/minemydinner/evaluation/revise_eval.py`
- Modify: `backend/eval_data/planning/scenarios_revise.yaml`
- Test: `backend/tests/test_planning_eval.py`

**Interfaces:**
- Produces: scenario keys `button_after: <action>` (press a next-action button after the follow-up; the runner sends the fixed message), `expect_wish_unmet: bool`, `expect_no_question: bool`; a check on every scenario that no reply or unmet text says "0 道菜" / "0 dishes" for a missing answer; `summarize` reports a scenario file's role (`role: development | regression | held-out` in its header) and keeps regression results out of the held-out rate.
- Produces: development scenarios (new sentences, never held-out ones) for: bare drop, drop of the only soup, replace, add-only, a one-meal avoidance in Chinese and English, a lasting rule while revising, a staple quote, a dish not in the library, the empty-plan question, each button after a failed revision and after an early stop, a new plan with an unmeetable wish; `choice_card_answered_revise` gets a sentence that no prompt uses.

- [ ] **Step 1: Write the failing tests** for each new key and check (stubbed `_drive` as the existing eval tests do).
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_planning_eval.py` — expected FAIL.
- [ ] **Step 3: Implement** the keys, the check and the roles.
- [ ] **Step 4: Write the development scenarios** and dry-load every scenario file through `load_scenarios` (the controller dry-loads `scenarios_revise_heldout2.yaml`; subagents never open it).
- [ ] **Step 5: Run** the eval tests and the full backend suite — expected PASS.
- [ ] **Step 6: User commit**

```powershell
git add backend/minemydinner/evaluation/planning.py backend/minemydinner/evaluation/revise_eval.py backend/eval_data/planning/scenarios_revise.yaml backend/tests/test_planning_eval.py
git commit -m "eval: button, wish-unmet and no-question checks, scenario roles, 3b-1b development scenarios"
```

### Task 10: Development runs (paid, with the user's approval)

**Files:**
- Create (only if needed): next prompt versions (`revise_v4`, `parse_v8`, `planner_v6`, `route_v4`)
- Create: run folders under `backend/eval_data/planning/runs/`

- [ ] **Step 1: Estimate the cost** of one run of `scenarios_revise.yaml` from round 9's per-scenario cost and give it to the user; wait for approval.
- [ ] **Step 2: Run** on the test database and read every scenario not as expected (traces in the run folder).
- [ ] **Step 3: Fix** prompt or code problems the run shows; a used prompt is never edited (a change makes the next version); each further run gets its own estimate and approval; at most three runs, then list what is left and ask the user.
- [ ] **Step 4: Run the backend suite** — expected PASS.
- [ ] **Step 5: User commit** (prompt files, code, `docs/prompt-versions.md`, run folders).

### Task 11: Evaluation round, documents and carryover

**Files:**
- Modify: `docs/planning-evaluation-history.md`, `docs/design.md` (7.3 and 7.5 lines for drops, one-meal wishes, buttons and the wish-unmet plan), `docs/prompt-versions.md`, `docs/phase-1-carryover.md`
- Create: run folders

- [ ] **Step 1: Estimate the cost** of the round (development, new held-out, regression, and every existing set once) and wait for approval.
- [ ] **Step 2: Run** the round on the test database.
- [ ] **Step 3: Record the round** against every threshold in spec 6, with each miss and its cause; held-out misses are recorded, not fixed.
- [ ] **Step 4: Update** `docs/design.md`, the carryover (close fixed "Plan 3b-1b" items, add "Deferred from Phase 1 plan 3b-1b"), and the scenario-set table if a role changed.
- [ ] **Step 5: Run** the backend suite and the frontend checks — expected PASS.
- [ ] **Step 6: User commit**

```powershell
git add docs/planning-evaluation-history.md docs/design.md docs/prompt-versions.md docs/phase-1-carryover.md backend/eval_data/planning/runs
git commit -m "docs: plan 3b-1b evaluation round, design and carryover updates"
```
