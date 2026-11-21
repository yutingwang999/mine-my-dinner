# Phase 1 Plan 3b-1: Revising the Plan in the Conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A follow-up message after a plan changes the current plan, including a hand-edited one, instead of planning from scratch: the route tells a revision from a new plan (and asks with a small card when unsure), a new revise step returns a change list that code applies and checks (locked dishes, quotes, change size, counts, hard rules), and the reply says what changed.

**Architecture:** `route_v3` returns plan / revise / unsure / other. A revise turn parses as today (with one line describing the current plan), builds the revision's spec from the current plan, and runs a new revise agent (the planner's loop with `revise_v1` and a `RevisionProposal` answer) whose proposal is checked by a pure module (`agent/revision.py`) and by `validate_plan` with the carried overrides, retried up to the planner's limit. Locks and overrides are stored on every plan version, so they survive revisions and restore. The evaluation runner gains follow-up scenarios with a fixed first plan and hand edits between turns.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, LangGraph 1.x (backend); Next.js 16, React 19, next-intl, plain CSS (frontend); pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-11-19-phase-1-plan-3b1-revise-plan-design.md` (section numbers below refer to it). 3a's spec and code (`agent/edits.py`, `agent/plan_card.py`) are the base.

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository; each "User commit" step gives the commands and stops. Branch: `feat/phase-1-plan-3b-revise-plan` (exists). Implementer subagents never stage or commit.
- **Packages:** none new.
- **Prompts:** new files only, never edit a prompt version any record or evaluation run folder has used (`route_v2`, `parse_v5`, `planner_v4` stay as they are). New: `route_v3.md`, `revise_v1.md`, and `parse_v6.md` only if Task 8 decides it. Once a new version has been used by an evaluation run, a further change makes the next version (`revise_v2`, ...). `PROMPT_VERSIONS` gains `"revise"`; `docs/prompt-versions.md` lists every new version.
- **Paid runs:** Tasks 8 and 9 call real models. Before each run, give the user the estimated cost and wait for approval. Every evaluation and check uses the test database only.
- **Python:** `$PY` is `python.defaultInterpreterPath` from `.vscode/settings.json`; run backend commands from `backend/` with `PYTHONNOUSERSITE=1 PYTHONUTF8=1`. Backend suite: `$PY -m pytest -q`.
- **Frontend checks** through the PowerShell tool only: `cd frontend; npm test; npm run lint; npm run build` (the first build may stop on a Next.js security-advisory check after compiling; rerun once; never run `next upgrade`).
- **Secrets:** never read, print, create, edit or delete `.env`. **Databases:** tests use the test database only, never call a real model, never assume `llm_calls` or `planning_runs` is empty, delete only rows they create.
- **Language:** every repository file in English; Chinese only in UI translations (`frontend/messages/zh.json`), dictionary names, Chinese request text in tests, prompt examples, and scenarios. Every user-facing string in both `en.json` and `zh.json`; reply sentences written by code in both languages.
- **No migration.** Revision state lives in the checkpointer; the planning record marks a revision inside its `request` JSON (spec 3, 5.6).
- **Labels:** never send a saved nickname to a model and never show a label ("Diner N"); the revise input names diners only as the planner's anonymous rule lines (plan 2b).
- **Hard constraints in code:** a revision's dishes go through `validate_plan` with the carried overrides; locks, quotes and change size are checked by `agent/revision.py`, never left to the prompt alone (spec 5.2).
- **Records:** a revision is a planning run with the same events as a new plan (every model call with its tool calls and results, each validation result, the prompt and tools versions), marked in `request["revision"]` with the version it started from, so it can be told apart and is complete on its own.
- **Held-out set:** `backend/eval_data/planning/scenarios_revise_heldout.yaml` is committed in Task 1 and never edited afterwards in this plan. No code or prompt change is made in response to a held-out result within this plan. Implementer and reviewer subagents of Tasks 2-8 are told not to open it; no held-out sentence or close paraphrase appears in a prompt, a unit test, or `scenarios_revise.yaml`.
- **Carryover:** after each task, copy deferred review findings into `docs/phase-1-carryover.md` under a new section "Deferred from Phase 1 plan 3b-1", each tagged with its Phase 1 step.

## Review Focus

1. A thread saved before 3b-1 (cards without `locked`, versions without `revision`, no `previous_request`, an older card that is not the newest): revising works, treating `locked` as empty plus the dishes with overrides; old histories render — Task 2 and Task 4 tests.
2. The newest card has an open (red, unconfirmed) conflict and the user writes "想要辣一些的": the revision must replace that dish, and that replacement does not count toward the half limit — Task 3 test.
3. A short follow-up whose parse drops an earlier condition (the first turn said 只用冰箱里的, the follow-up's parse has `mode: null`) under ask-first: no question is asked and the revision stays fridge-only — Task 3 and Task 5 tests.
4. The user asked for 3 dishes, added a 4th by hand, then writes "想要辣一些的": the revision keeps 4; "再加一道" makes 5; a no-plan reply (0 dishes) revised with a wish takes its counts from the request, not 0 — Task 3 test.
5. A revision that fails every attempt (or the model answers `unclear`): `current_plan` and `plan_versions` are unchanged, the reply shows the same dishes with the reason or the question, and the planning run is finished as a failed revision — Task 4 test.

---

### Task 1: Held-out revision set (user gate)

**Files:**
- Create: `backend/eval_data/planning/scenarios_revise_heldout.yaml`
- Create: `backend/eval_data/planning/library_revise.yaml` (`library_diners.yaml` plus 松仁玉米, 鱼香肉丝, 辣椒炒肉, 家常烧肉, 清炒蘑菇, 白米饭; both revise sets run with it; frozen after this task)

**Interfaces:**
- Produces: the scenario keys Task 7 implements, defined in the header comment of `scenarios_revise_heldout.yaml` exactly as below:
  - `first_plan: [titles]` — after the first message's turn, the runner replaces the newest reply's dishes with these library titles as a model version (not edited, no locks), so the follow-up starts from a known plan.
  - `edits_between_turns: [{add: title} | {remove: title} | {keep: title}]` — 3a edits applied in order after `between_turns`, through `apply_edit` and `update_state` as the API does (an add that needs confirmation is confirmed).
  - `choice_answer: revise | plan` — the answer to the choice card; with no answer the runner stops at the card.
  - `expect_route: plan | revise | unsure` — the route result of the follow-up.
  - `expect_choice_card: bool` — whether the choice card appeared on the follow-up.
  - `expect_question: bool` — the follow-up ends with a question to the user and no new plan version.
  - `expect_unchanged: bool` — the dishes after the follow-up equal those before it.
  - `expect_removed: [titles]` — each is gone after the follow-up.
  - `expect_max_changed: int` — at most this many dishes of the plan before the follow-up are gone.
  - `expect_removed_any: [titles]` — at least one of them is gone after the follow-up.
  - `expect_any_of: [{expect_* keys}]` — met when the follow-up meets every key of at least one entry; keys outside the list are still all required.
  - `library_exclude: [titles]` — these library dishes do not exist for this scenario (hidden before the first message, brought back after).
  - `deferred_to: <plan>` — the expected result needs a later plan; the scenario is run and reported but left out of plan 3b-1's held-out threshold.
  - Existing keys keep their meaning, judged on the follow-up's plan (`expect_titles_include`, `expect_titles_none`, `expect_counts`, `expect_spec`, ...).
  - Locked dishes are checked for every scenario without a key: a dish locked before the follow-up and absent after it, not listed in `expect_removed`, counts as a wrongly removed locked dish.

- [ ] **Step 1: Ask the user** for the held-out sentences (15-20, the kinds listed in the conversation: named dishes, an explicit scope, a general wish, count changes, ingredient words that name no dish, new plans, ambiguous ones), each with its expected result. Stop until they arrive.
- [ ] **Step 2: Write the file**: one scenario per sentence, the follow-up text exactly as the user wrote it, with `library: library_diners.yaml`, a first request, `first_plan`, any `edits_between_turns`, inventory and expectations from the user's notes. Header comment: the key definitions above, then "Held-out set written by the user on <date>. Not used for tuning in plan 3b-1; do not edit."
- [ ] **Step 3: Validate the YAML** loads: `$PY -c "import yaml,sys; yaml.safe_load(open('eval_data/planning/scenarios_revise_heldout.yaml', encoding='utf-8'))"` — no error. (The runner's own key check comes in Task 7.)
- [ ] **Step 4: User commit**

```powershell
git add backend/eval_data/planning/scenarios_revise_heldout.yaml backend/eval_data/planning/library_revise.yaml docs/superpowers/plans/2026-11-19-phase-1-plan-3b1-revise-plan.md
git commit -m "eval: plan 3b-1 held-out revision scenarios and library"
```

### Task 2: Locks and overrides on every plan version

**Files:**
- Modify: `backend/minemydinner/agent/edits.py` (`_edited_card`, `_restored_card`, `apply_edit`)
- Modify: `backend/minemydinner/agent/graph.py` (`present_plan`: `locked: []` on model plans)
- Test: `backend/tests/test_agent_edits.py`

**Interfaces:**
- Produces: every plan card has `locked: list[int]` — recipe ids added by hand and still in the plan. A model plan from scratch has `[]`; a manual add appends the id; a remove drops it; a card saved before this task reads as `[]`.
- Produces: `locked_ids(plan: dict) -> set[int]` in `edits.py`: `set(plan.get("locked") or [])` plus the `recipe_id` of every override in `plan["edit"]["overrides"]` (when `edit` exists).
- Produces: restore of a model version that carries `edit.overrides` and `locked` (a revised version, Task 4) keeps them; a model version without them restores as today.

- [ ] **Step 1: Write the failing tests**: `test_manual_add_locks_the_dish` (add → `locked == [id]`; remove it → `[]`); `test_locked_ids_include_override_dishes` (a kept dish's id is in `locked_ids` though not in `locked`); `test_old_card_without_locked_reads_empty` (a card dict with no `locked` key → `locked_ids` gives only override dishes, no KeyError); `test_restore_keeps_a_revised_versions_locks_and_overrides` (a model version dict with `locked: [41]` and one override, then a manual edit, then restore → the card has `locked == [41]` and that override, and the overridden conflict is in `edit.kept`, not in `edit.conflicts`).
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_agent_edits.py` — expected FAIL on the four new tests.
- [ ] **Step 3: Implement**: `locked` carried from the current card into each manual version and updated by add/remove; `_restored_card` validates with the model version's overrides (`model.get("edit", {}).get("overrides", [])`) and copies its `locked`; `present_plan` sets `"locked": []`.
- [ ] **Step 4: Run** the edits tests, `tests/test_plan_edits_api.py` and `tests/test_agent_graph.py` — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/edits.py backend/minemydinner/agent/graph.py backend/tests/test_agent_edits.py
git commit -m "feat: locks and overrides stored on every plan version, kept by restore"
```

### Task 3: Revision checks (pure code, no graph)

**Files:**
- Create: `backend/minemydinner/agent/revision.py`
- Test: `backend/tests/test_agent_revision.py`

**Interfaces:**
- Consumes: `locked_ids` (Task 2); `PlanSpec` (`planning/validate.py`); `count_categories` (`planning/composition.py`).
- Produces (all in `revision.py`):
  - `class RemovedDish(BaseModel)`: `id: int`, `quote: str = ""` (the words of the message that name it; empty for a general wish).
  - `class RevisionProposal(BaseModel)`: `target: Literal["named", "scope", "general", "unclear"]`, `remove: list[RemovedDish]`, `add: list[int]`, `scope_quote: str = ""`, `excepted: list[int] = []` (dishes a scope leaves out), `note: str` (reply language). Field descriptions are the model-facing contract; `revise_v1` (Task 4) relies on them.
  - `class RevisionProblem(BaseModel)`: `code: Literal["R1", "R2", "R3", "R4", "R5", "R6"]`, `recipe_id: int | None`, `text: str` (English, for the retry message). R1 quote not in the message; R2 quote does not match its dish; R3 a locked dish removed without a valid quote (or by a general wish); R4 more changes than allowed; R5 an excepted dish removed; R6 an id not in the plan removed, or an id already in the plan added.
  - `max_general_changes(unlocked: int) -> int`: `0` when `unlocked == 0`, else `max(1, ceil(unlocked / 2))`.
  - `quote_matches(quote: str, title: str, ingredient_names: Iterable[str], category_words: Iterable[str]) -> bool`: true when the quote contains any 2-character substring of the title, any ingredient name, or any category word (e.g. 汤 / soup for soups).
  - `check_revision(plan: dict, proposal: RevisionProposal, message: str, dish_facts: dict[int, DishFacts]) -> list[RevisionProblem]` — `DishFacts` is a small dataclass (`title`, `ingredient_names`, `category_words`) built by the caller from the database. Rules from spec 5.2: named and scope quotes are checked (R1, R2); locked dishes may leave only under `named` with a matching quote or under `scope` unless excepted (R3, R5); under `general`, removals of unlocked dishes that have no open conflict are limited by `max_general_changes` (R4); dishes with an open conflict (`plan["edit"]["conflicts"]`) are never locked and are outside the limit. `unclear` must have empty `remove` and `add`.
  - `revision_spec(plan: dict, previous_request: dict | None, request: dict) -> PlanSpec` — starts from `plan["spec"]`; the counts become the plan's own category counts as exact counts (spec 5.2), except: (a) a count field whose value in `request` is not null and differs from `previous_request` replaces it; (b) a plan with no dishes keeps `plan["spec"]` counts. Every other field (`mode`, `time_limit_min`, `must_use`, `prefer_use`, `all_use`) changes only when `request` states it and it differs from `previous_request`; a null in `request` never clears it (spec 5.3).
  - `revised_ids(plan: dict, proposal: RevisionProposal) -> list[int]`: the plan's ids in order without the removed ones, then the added ones.
  - `changed_text(removed: list[str], added: list[str], untouched_locked: list[str], locale: str) -> str`: "把A换成了B。" / "Replaced A with B." when one of each; "去掉了A。" / "加了B。" otherwise (joined with 、 / ", "); then, when `untouched_locked` is not empty, "X是你手动加的，没有改动。" / "X was added by you, so it was left as is."

- [ ] **Step 1: Write the failing tests** in `test_agent_revision.py`, using plain dicts and `DishFacts`:
  - `test_max_general_changes` — `(0→0, 1→1, 2→1, 3→2, 4→2, 5→3)`.
  - `test_named_quote_must_be_in_message_and_match` — "茄子这道不要，换一个别的" with remove `{id: 鱼香茄子, quote: "茄子这道"}` → no problem; quote "土豆这道" → R1; quote "茄子这道" on 番茄炒蛋 → R2.
  - `test_locked_dish_needs_named_quote` — 韭菜炒鸡蛋 locked, target `general` removing it → R3; target `named` with quote "韭菜炒鸡蛋这道" → no problem.
  - `test_scope_removes_locked_but_not_excepted` — "除了玉米这道剩下全换", scope removes every dish but 玉米排骨汤 including a locked one → no problem; removing 玉米排骨汤 too (listed in `excepted`) → R5.
  - `test_general_wish_limit_excludes_open_conflicts` — 5 unlocked dishes, one with an open conflict; removing the conflicted one plus 3 others → no problem; plus 4 others → R4.
  - `test_unclear_changes_nothing` — `unclear` with a removal → R6 (or a dedicated failure; the test asserts a problem is returned).
  - `test_revision_spec_keeps_hand_grown_count` — `plan["spec"]` dishes 3, plan has 4 dishes (2 meat, 2 vegetable), previous and new request both `dishes: 3` → spec counts are the plan's (4 total, 2 and 2, exact); new request `dishes: 5` → 5.
  - `test_revision_spec_null_never_clears` — previous `mode: fridge_only`, new `mode: null` → spec mode `fridge_only`.
  - `test_revision_spec_no_dishes_uses_request_counts` — a plan with `recipes: []` and `spec` dishes 3 → counts 3.
  - `test_changed_text_both_languages` — the sentences above for one swap, one add, and a locked dish left.
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_agent_revision.py` — expected FAIL (module missing).
- [ ] **Step 3: Implement** `revision.py` as specified.
- [ ] **Step 4: Run** the tests — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/revision.py backend/tests/test_agent_revision.py
git commit -m "feat: revision checks - locks, quotes, change size, the revision's spec, what-changed text"
```

### Task 4: The revise step in the graph

**Files:**
- Create: `backend/minemydinner/agent/prompts/revise_v1.md`
- Modify: `backend/minemydinner/agent/planner.py` (`build_reviser`, `REVISE_TOOLS_VERSION`)
- Modify: `backend/minemydinner/agent/request.py` (`PROMPT_VERSIONS["revise"] = "revise_v1"`, `load_prompt` accepts `"revise"`)
- Modify: `backend/minemydinner/agent/graph.py` (nodes `start_revision`, `reviser`, `check_revised`, `present_revision`; edges)
- Modify: `backend/minemydinner/agent/state.py` (`revision: dict | None`)
- Test: `backend/tests/test_agent_revise.py`

**Interfaces:**
- Consumes: Task 3's module; Task 2's `locked_ids`; `build_dishes_card`, `card_base` (`plan_card.py`); `validate_plan` with overrides.
- Produces: `build_reviser(deps) -> CompiledStateGraph` — the planner's `create_agent` setup with `system_prompt=load_prompt("revise")`, the same tools and middleware, and `response_format=ToolStrategy(RevisionProposal)`.
- Produces: the graph path `start_revision → reviser → check_revised → (reviser | present_revision) → END`. Task 5 routes revise turns into `start_revision`; until then tests enter it by setting `intent: "revise"` with a scripted route.
- Produces: a revised version — a card from `build_dishes_card` plus `edited: False`, `locked` (the plan's locks that remain), `edit: {"overrides": carried, "log": [], "conflicts": [...], "kept": [...]}`, `revision: {"from_version": <index>}` — appended to `plan_versions`, stored as `current_plan` and the new reply's plan.

- [ ] **Step 1: Write the failing tests** in `test_agent_revise.py` with the scripted model fakes (`tests/agentfakes.py`) and a seeded library:
  - `test_named_swap_keeps_the_rest` — a current plan of 3 dishes; scripted proposal removes 鱼香茄子 with quote "茄子这道" and adds another meat dish → the new plan has the other two plus the new one; the reply starts with "把鱼香茄子换成了…"; a new version with `revision.from_version` is appended; the planning run's `request["revision"]` is set and it is finished passed.
  - `test_locked_dish_removed_by_general_wish_is_retried` — first proposal removes a locked dish under `general` → a retry whose message lists R3; the second proposal keeps it → passed.
  - `test_overrides_carry_into_revision` — a kept dish (override) stays; `validate_plan` raises no H2 for it; the new version carries the override and `locked`.
  - `test_failed_revision_leaves_plan_unchanged` — every attempt fails → `current_plan` and `plan_versions` unchanged; the reply's plan has the same dishes, `unmet` from the last attempt and next actions; the run is finished not passed.
  - `test_unclear_asks_and_changes_nothing` — proposal `unclear` with a note "你说的是哪一道汤？" → the reply text is the note; no new version; the run is finished not passed.
  - `test_locked_untouched_sentence` — `general` wish with a locked dish left → the reply contains "…是你手动加的，没有改动。".
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_agent_revise.py` — expected FAIL.
- [ ] **Step 3: Write `revise_v1.md`**: the step's job (change the current plan as the message asks and keep the rest); the three target kinds with the quote rules and examples (named: "茄子这道不要"; scope: "剩下全换", "除了玉米这道剩下全换"; general: "想要辣一些的", "不要这么多鸡蛋的" — an ingredient or taste word that does not point at one dish is a general wish); locked dishes stay unless named or in a scope; change as few dishes as possible; dishes marked with an open conflict must be replaced; `unclear` when it cannot tell which dish is meant, with a one-sentence question in the note; the counts in the request are exact; search only when the shortlist has nothing fitting. No example may use a sentence from the held-out file.
- [ ] **Step 4: Implement** the nodes. `start_revision` builds the revision spec (`revision_spec`), starts the planning run (`request` as in `load_context` plus `"revision": {"from_version": i}`; `prompt_version=PROMPT_VERSION`, `tools_version=REVISE_TOOLS_VERSION`) and the opening message: the shortlist (as the planner's), the request summary headed "Revise this meal." instead of "Plan this meal.", then "Current plan:" with one line per dish (`id title (dish type; main ingredients) LOCKED|OPEN CONFLICT`) and "The user's message: …". `check_revised` runs `revision.check_revision` and then `validate_plan(..., overrides)`; H1/H3/H6/H7 caused only by a locked dish are warnings (3a `ALLOWED_CODES`); problems and violations go into the retry message in the planner's format; retries up to `MAX_RETRIES`. `present_revision` writes the reply (`changed_text` + the model's note), the version and the record, or the unchanged plan on failure or `unclear`. Every model call, tool call, and validation goes into the run's events as the planner's do.
- [ ] **Step 5: Run** the new tests and `tests/test_agent_graph.py` — expected PASS.
- [ ] **Step 6: User commit**

```powershell
git add backend/minemydinner/agent/prompts/revise_v1.md backend/minemydinner/agent/planner.py backend/minemydinner/agent/request.py backend/minemydinner/agent/graph.py backend/minemydinner/agent/state.py backend/tests/test_agent_revise.py
git commit -m "feat: the revise step - change list from revise_v1, checked by code, a revised plan version"
```

### Task 5: Routing a follow-up

**Files:**
- Create: `backend/minemydinner/agent/prompts/route_v3.md`
- Modify: `backend/minemydinner/agent/graph.py` (`Intent`, `route_intent`, `choose`, `after_parse`, node `ask_revise_or_plan`, the parse input line)
- Modify: `backend/minemydinner/agent/request.py` (`PROMPT_VERSIONS["route"] = "route_v3"`)
- Modify: `backend/minemydinner/agent/state.py` (`previous_request: dict | None`)
- Modify: `backend/minemydinner/api/chat.py` (history rendering of the new question kind, if the history lists questions by kind)
- Test: `backend/tests/test_agent_graph.py`, `backend/tests/test_chat_api.py`

**Interfaces:**
- Consumes: Task 4's `start_revision`.
- Produces: `Intent.intent: Literal["plan", "revise", "unsure", "other"]`.
- Produces: the choice card interrupt value `{"kind": "revise_or_plan", "message": <the user's latest message>}`; the resume value `"revise"` or `"plan"`; a skipped card (`None` or other text) continues as `revise` (Claude's call: it keeps the user's work, and the change is visible in the reply).
- Produces: `route_intent` stores the thread's `request` as `previous_request` before the parse overwrites it.

- [ ] **Step 1: Write the failing tests**:
  - `test_revise_without_a_plan_plans` — scripted route `revise` on a new thread → the turn plans as `plan`.
  - `test_revise_goes_to_the_revise_step` — after a plan, route `revise` → the turn passes `start_revision` (no `load_context`), and the parse input ends with the current plan line ("Current plan: 3 dishes (…): A, B, C").
  - `test_unsure_shows_the_choice_card` — route `unsure` after a plan → an interrupt of kind `revise_or_plan` with the message; resume `"plan"` → plans from scratch; resume `"revise"` and resume `None` → revise.
  - `test_revise_never_asks_the_ask_first_question` — request style `ask_first`, the follow-up's parse leaves `mode` and counts null → no `question` interrupt; a contradiction (`request_problem`) still asks.
  - `test_revise_with_a_diner_change_plans` — the follow-up's parse names a new guest → the diner card shows and the meal is planned from scratch (spec 5.1).
  - `test_plan_again_still_plans_from_scratch` — the fixed "plan again" message after a plan → no route call, a new plan as today.
  - In `test_chat_api.py`: `test_choice_card_resumes_and_shows_in_history` — the stream yields the interrupt; resume with `"revise"`; the thread history lists the question.
- [ ] **Step 2: Run** the new tests — expected FAIL.
- [ ] **Step 3: Write `route_v3.md`**: the four intents with short examples (revise: changing, swapping, adding or removing dishes of the plan already in this conversation; plan: a new meal, a different day, an explicit fresh start such as "重新设计一顿"; unsure: could be either; other: as `route_v2`), and that an answer to the assistant's question is not `other`. No held-out sentence.
- [ ] **Step 4: Implement** the routing, the card node, the ask-first skip on revise turns (`after_parse`), the diner-change fallback (a revise turn whose `diner_card_needed` is true continues as `plan`), and the parse input line for revise turns.
- [ ] **Step 5: Run** the new tests and the whole backend suite — expected PASS.
- [ ] **Step 6: User commit**

```powershell
git add backend/minemydinner/agent/prompts/route_v3.md backend/minemydinner/agent/graph.py backend/minemydinner/agent/request.py backend/minemydinner/agent/state.py backend/minemydinner/api/chat.py backend/tests/test_agent_graph.py backend/tests/test_chat_api.py
git commit -m "feat: route_v3 - revise, plan or unsure after a plan; the choice card; no ask-first question on a revision"
```

### Task 6: Frontend - the choice card and the edited card's line

**Files:**
- Modify: `frontend/src/lib/chat.ts` (`Pending` gains `{ kind: "revise_or_plan"; message: string }`)
- Create: `frontend/src/components/ReviseOrPlanCard.tsx`
- Modify: `frontend/src/components/MessageList.tsx`, `frontend/src/components/ChatPage.tsx` (render the pending card, resume with `"revise"` / `"plan"`, the history entry)
- Modify: `frontend/src/components/PlanCard.tsx` (drop the quiet line `chat.edit.quiet` and its `showQuiet` prop path)
- Modify: `frontend/messages/en.json`, `frontend/messages/zh.json`
- Test: `frontend/src/components/ReviseOrPlanCard.test.tsx`, `frontend/src/components/PlanCard.test.tsx`, `frontend/src/components/ChatPage.test.tsx`

**Interfaces:**
- Consumes: Task 5's interrupt value and resume values.
- Produces: the card shows the user's message and two buttons, "在这份方案上改" / "Change this plan" and "重新规划一顿" / "Plan a new meal"; the history line after answering reads "你选择了：在这份方案上改" or "…：重新规划一顿" (and English), or "已跳过" when skipped.

- [ ] **Step 1: Write the failing tests**: the card renders the message and both buttons and sends `"revise"` / `"plan"`; ChatPage resumes with the chosen value and disables the composer while the card is pending (as for other cards); the history shows the choice; the edited plan card no longer shows the quiet line.
- [ ] **Step 2: Run** `cd frontend; npx vitest run` — expected FAIL on the new tests.
- [ ] **Step 3: Implement** with the existing card styles (no new colours), both message files, phone width wrapping like the other cards.
- [ ] **Step 4: Run** `npm test; npm run lint; npm run build` — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add frontend/src/lib/chat.ts frontend/src/components/ReviseOrPlanCard.tsx frontend/src/components/ReviseOrPlanCard.test.tsx frontend/src/components/MessageList.tsx frontend/src/components/ChatPage.tsx frontend/src/components/ChatPage.test.tsx frontend/src/components/PlanCard.tsx frontend/src/components/PlanCard.test.tsx frontend/messages/en.json frontend/messages/zh.json
git commit -m "feat: the revise-or-plan choice card; edited cards no longer say a message replans"
```

### Task 7: Evaluation runner and the development set

**Files:**
- Modify: `backend/minemydinner/evaluation/planning.py` (`Scenario` keys, `run_scenario`, `_drive`, `check_expectations`, `summarize`, `group_metrics`)
- Create: `backend/eval_data/planning/scenarios_revise.yaml`
- Test: `backend/tests/test_planning_eval.py`

**Interfaces:**
- Consumes: the key definitions in Task 1's header (implemented exactly as written there; the implementer reads only that header, not the scenarios); `apply_edit` (3a); `build_dishes_card`.
- Produces: `RunOutcome` gains `route_intent: str | None` (the follow-up's route result), `locked_removed: int`, `changed: int`; `summarize` gains `route_as_expected` ("k/n" over scenarios with `expect_route`), `locked_removed` (sum) and `avg_changed`.

- [ ] **Step 1: Write the failing tests** (no real model; scripted graph or stubbed `_drive` as the existing eval tests do): each new key is read and validated (unknown values refused, `first_plan` titles must exist in the library, `edits_between_turns` needs `follow_up`); `first_plan` replaces the first reply's dishes as a model version; `edits_between_turns` applies an add, a remove and a keep; `choice_answer` answers the card; `library_exclude` hides the dishes for that scenario only; `expect_any_of` passes when one entry is met; `deferred_to` scenarios are reported but kept out of the held-out rate; each `expect_*` key reports a failure when unmet; `locked_removed` counts a locked dish gone without `expect_removed`.
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_planning_eval.py` — expected FAIL.
- [ ] **Step 3: Implement** the keys and metrics.
- [ ] **Step 4: Write `scenarios_revise.yaml`** (development set, about 16 scenarios, `library_revise.yaml`): the user's five example sentences from the spec (Section 1) after a fixed `first_plan`, the same with a hand-added (locked) dish, a scope with an exception, a general wish that must leave a locked egg dish, a count change on a hand-grown plan, a two-soup "那道汤不要了" that must ask, a fridge-only wish that cannot be met (unchanged, reasons), a choice card answered `plan` and one answered `revise`, a revise with a new guest (diner card, then a new plan), and two first-message scenarios checking `route_v3` returns `plan`. Write new sentences; do not open the held-out file.
- [ ] **Step 5: Run** the eval tests and a dry load of both revise files through the runner's loader (`load_scenarios`) — expected PASS.
- [ ] **Step 6: User commit**

```powershell
git add backend/minemydinner/evaluation/planning.py backend/tests/test_planning_eval.py backend/eval_data/planning/scenarios_revise.yaml
git commit -m "eval: follow-up scenarios with a fixed first plan, edits between turns, route and lock metrics; revise development set"
```

### Task 8: Development runs (paid, with the user's approval)

**Files:**
- Create (only if needed): `backend/minemydinner/agent/prompts/parse_v6.md`, `revise_v2.md`, `route_v4.md`
- Modify (only if needed): `backend/minemydinner/agent/request.py` (`PROMPT_VERSIONS`)
- Create: run folders under `backend/eval_data/planning/runs/` (as the runner writes them)

**Interfaces:**
- Produces: the prompt versions the evaluation round in Task 9 uses, recorded in `docs/prompt-versions.md`.

- [ ] **Step 1: Estimate the cost** of one run of `scenarios_revise.yaml` from the latest round's per-scenario cost (both cache-aware and uncached, two turns per scenario) and **give it to the user; wait for approval**.
- [ ] **Step 2: Run** the development set once on the test database and read every scenario not as expected.
- [ ] **Step 3: Decide the parse version** (spec 5.3): if "再加一道"-type scenarios get a wrong count with `parse_v5` and the plan line, write `parse_v6.md` (explaining the current-plan line and relative counts) and switch `PROMPT_VERSIONS`; otherwise keep `parse_v5` and say so in `docs/prompt-versions.md`.
- [ ] **Step 4: Fix prompt or code problems** the run shows. A prompt that has been used by a run is never edited: a change makes the next version. Each further paid run gets its own estimate and approval. At most three development runs in this task; past that, list what is left and ask the user.
- [ ] **Step 5: Run the backend suite** — expected PASS.
- [ ] **Step 6: User commit** (the prompt files, `request.py` if changed, `docs/prompt-versions.md`, and the run folders, as the earlier evaluation commits did).

### Task 9: Evaluation round, documents and carryover

**Files:**
- Modify: `docs/planning-evaluation-history.md`, `docs/design.md` (7.1 flow lines for revision, 7.6 "Changes mid-conversation" note about 3b-2, 7.5 manual edits line about a new message), `docs/prompt-versions.md`, `docs/phase-1-carryover.md`
- Create: run folders under `backend/eval_data/planning/runs/`

**Interfaces:**
- Thresholds, set before the round runs (spec 6):
  - Follow-up routing: `route_as_expected` at least 90% on the development set and on the held-out set.
  - First-message routing: no scenario of the existing sets (baseline, plan2a, counts, diners, held-out core and diners) is not as expected because of the route.
  - Locked dishes wrongly removed: 0 over every run.
  - As expected: at least 90% of `scenarios_revise.yaml` and at least 80% of `scenarios_revise_heldout.yaml` (scenarios with `deferred_to` reported apart, not counted).
  - Average model calls per revise turn at most 4 (route, parse, revise attempts), with no turn over 7.

- [ ] **Step 1: Estimate the cost** of the round (the two revise sets plus every existing set once, with the final prompt versions) and **give it to the user; wait for approval**.
- [ ] **Step 2: Run** the round on the test database.
- [ ] **Step 3: Append the round** to `docs/planning-evaluation-history.md`: prompt versions, results against each threshold, cost, and every scenario not as expected with its cause. A held-out miss is recorded, not fixed in this plan (it goes to the carryover).
- [ ] **Step 4: Update `docs/design.md`**: 7.1's revision line ("swap the second dish / make it spicier" → route revise → parse → revise → checks → present, with the choice card when unsure), the T1.5 note under 7.6 (diner changes still plan from scratch until 3b-2), and the manual-edits paragraph in 7.5 (a new message now revises the edited plan).
- [ ] **Step 5: Update the carryover**: close "An unmet taste wish is not reported" for revisions (it stays open for new plans), add every deferred finding and held-out miss under "Deferred from Phase 1 plan 3b-1".
- [ ] **Step 6: Run** the backend suite and the frontend checks — expected PASS.
- [ ] **Step 7: User commit**

```powershell
git add docs/planning-evaluation-history.md docs/design.md docs/prompt-versions.md docs/phase-1-carryover.md backend/eval_data/planning/runs
git commit -m "docs: plan 3b-1 evaluation round, design and carryover updates"
```
