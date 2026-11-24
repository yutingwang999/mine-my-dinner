# Phase 1 Plan 3b-2 (one): Revise Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A follow-up on an existing plan is read once by one model into a structured reading; code builds the frame of what changes and keeps every guard; a narrow picker chooses dishes only inside that frame; ambiguous messages are acted on and explained; evaluation checks two layers.

**Architecture:** Three new modules: `agent/follow_up.py` (the reading schema, the reader's input, the request after a reading), `agent/frame.py` (pure code: which dishes keep, drop or become slots, and the choices the picker may make), `agent/picker.py` (the picker's schema, input and how its answer is applied and checked). `agent/graph.py` routes every non-button message on a thread that has a plan to a new `read_follow_up` node instead of `route_intent`, and every revision (message or button) through the frame and the picker instead of the revise step. The first turn of a thread is unchanged.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, LangGraph 1.x (backend); Next.js 16, React 19 (frontend, no change expected); pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-11-22-phase-1-plan-3b2-revise-simplification-design.md` (section numbers below refer to it). Plans 3b-1 and 3b-1b are the base.

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository; each "User commit" step gives the commands and stops. Branch: `feat/phase-1-plan-3b2-diner-changes` (exists). Implementer subagents never stage or commit.
- **Packages:** none new.
- **Prompts:** never edit a prompt version any record or evaluation run used (`route_v6`, `parse_v8`, `planner_v5`, `revise_v4` and every older one are frozen). New files: `follow_up_v1.md`, `pick_v1.md`. Once a new version has been used by a run, a further change makes the next version. `docs/prompt-versions.md` lists every new version **with its length in characters**; the reader prompt stays under half of `route_v6` plus `revise_v4` together (spec 5.8a).
- **What each model sees:** whenever a model's input changes, `docs/planner-what-each-model-sees.md` and `docs/private/planner-what-each-model-sees.zh.md` change in the same task.
- **Paid runs:** Tasks 11 and 12 call real models; before each run give the user the estimated cost and wait for approval; at most three development runs in Task 11. Every evaluation and check uses the test database only.
- **Python:** `$PY` is `python.defaultInterpreterPath` from `.vscode/settings.json`; run backend commands from `backend/` with `PYTHONNOUSERSITE=1 PYTHONUTF8=1`. Backend suite: `$PY -m pytest -q`.
- **Frontend checks** through the PowerShell tool only: `cd frontend; npm test; npm run lint; npm run build` (the first build may stop on a Next.js security-advisory check; rerun once; never run `next upgrade`).
- **Secrets:** never read, print, create, edit or delete `.env`. Tests use the test database only, never call a real model, never assume `llm_calls` or `planning_runs` is empty.
- **Language:** every repository file in English; Chinese only in UI translations, dictionary names, Chinese request text in tests, prompt examples and scenarios. Reply sentences written by code in both languages.
- **Labels:** never send a saved nickname to a model and never show "Diner N"; cards reach the client only through `client_card`.
- **Hard constraints in code:** locks (quote, rule or avoidance), counts, dish types of slots, diner rules and one-meal avoidances, stock, cookware, time (spec 5.4, 5.5).
- **Records:** every revision and button turn is a planning run; the run records the reading, the frame and every picker attempt with its validation; button turns keep `request["button"]`.
- **Held-out sets:** the new held-out file written by the user in Task 1 is frozen; no implementer or reviewer subagent opens it or `scenarios_revise_heldout.yaml` / `scenarios_revise_heldout2.yaml` except where a task says the controller edits them with the user's approval; no held-out sentence appears in a prompt, a test or a development scenario; no change is made in response to a held-out result within this plan.
- **Carryover:** after each task, copy deferred review findings into `docs/phase-1-carryover.md` under "Deferred from Phase 1 plan 3b-2 (one)", and mark items closed when a task fixes them.

## Review Focus

1. A thread saved before this plan (a pending choice card, a revision dict without the new fields, a card without `wish_words`) gets a follow-up: it is read and revised, no crash — Task 6.
2. The reader names a dish id that is not in the plan, or removes a locked dish with a quote that does not point at it: code ignores that removal and never removes the dish — Task 3.
3. A message about something else ("再加一道") on a plan with a one-meal avoidance: the avoidance stays (the reader input shows it and the scripted reading restates it); a reading that drops it is applied as given (the withdrawal path) — Task 2 and Task 6.
4. The picker answers a dish already in the plan, a dish of the wrong type, or more choices than allowed: rejected, retried once, never applied — Task 4.
5. A named dish whose title differs from the library title by one character ("宫爆鸡丁" for 宫保鸡丁): matched; a name that matches two titles: not placed, the reply asks nothing and says it was not found exactly — Task 3. (Superseded by Task 10b: only an exact title is placed by code; any other name goes to the picker, which searches, and "not in the library" is said only when it finds nothing.)

---

### Task 1: The user's new held-out batch and the regression-set changes (user gate)

**Files:**
- Create (controller only): `backend/eval_data/planning/scenarios_revise_heldout3.yaml` (the user's sentences), `backend/eval_data/planning/library_revise3.yaml` only if the sentences need dishes `library_revise2.yaml` lacks (`library_revise2.yaml` stays frozen)
- Modify (controller only, after the user's approval of each change): `backend/eval_data/planning/scenarios_revise_heldout.yaml`, `backend/eval_data/planning/scenarios_revise_heldout2.yaml` (role `regression` for the second; expectation changes)
- Modify: `docs/planning-evaluation-history.md` (scenario-set table)

- [ ] **Step 1:** List, for the user, every expectation in the two older revise sets that conflicts with the design (questions or choice cards where a reasonable change exists, spec 5.6; `expect_route: unsure`), every set-up mistake round 10 found, and the proposed new expectation (often an `expect_any_of`). Wait for the user's decision on each.
- [ ] **Step 2:** Ask the user for 15-20 new held-out sentences (kinds in spec 6). Turn them into scenarios with the user; notes that need runner keys this plan adds are written as `# TODO(Task 10)` comments and converted by the controller after Task 10.
- [ ] **Step 3:** Apply the approved changes; dry-load every changed file through `load_scenarios`; update the scenario-set table (heldout2 → regression; heldout3 → held-out).
- [ ] **Step 4: User commit**

```powershell
git add backend/eval_data/planning docs/planning-evaluation-history.md
git commit -m "eval: plan 3b-2 held-out batch, older revise sets become regression sets with approved expectation changes"
```

### Task 2: The reading (`agent/follow_up.py`)

**Files:**
- Create: `backend/minemydinner/agent/follow_up.py`
- Test: `backend/tests/test_follow_up.py`

**Interfaces:**
- Produces: `FollowUpReading` (Pydantic): `kind: Literal["change", "new_meal", "people", "other"]`; `ambiguous: bool = False`; `reading: str = ""`; `remove: list[ReadRemoval] = []` with `ReadRemoval(id: int, mode: Literal["drop", "replace"], quote: str, with_name: str = "")`; `add: list[ReadAdd] = []` with `ReadAdd(dish_type: str | None = None, count: int = 1, name: str = "")`; `wish: str = ""`; `wish_general: bool = False`; `meal_avoid: list[str] = []` (the whole list after the message); `counts: ReadCounts` (`dishes, meat_dishes, vegetable_dishes, soups, staples, desserts, snacks: int | None`, absolute); `conditions: ReadConditions` (`mode: Literal["fridge_only", "shopping"] | None`, `time_limit_min: int | None`, `clear_time_limit: bool = False`, `must_use: list[str] = []`, `prefer_use: list[str] = []`). Field descriptions are model input (they state spec 5.2's rules in one line each).
- Produces: `reader_messages(conversation: list[BaseMessage], plan_lines: str, diner_lines: list[str], conditions_text: str, locale: str) -> list[BaseMessage]` — the system prompt is added by the caller; the user message holds the plan lines, the diner lines, the conditions and the conversation in that order (spec 5.3).
- Produces: `request_after(request: dict, reading: FollowUpReading) -> dict` — the request after a `change` reading: stated counts replace the count group (as a group) and set `counts_stated_now`; conditions replace their fields (`clear_time_limit` clears it); `meal_avoid` is replaced by the reading's list; `wish` (non-empty) replaces `query`; everything else is carried.

- [ ] **Step 1: Write the failing tests:** `test_reading_schema_defaults` (an `other` reading validates with only `kind`); `test_request_after_counts_replace_as_a_group` (stated `dishes: 4` clears old sides, `counts_stated_now` true); `test_request_after_meal_avoid_is_the_whole_list` (old `["猪肉"]`, reading `[]` → `[]`; reading `["猪肉", "香菜"]` → both); `test_request_after_wish_replaces_query_only_when_stated`; `test_request_after_clear_time_limit`; `test_reader_messages_order_and_no_nickname` (a diner line given as "Diner 2: 不吃羊肉" appears; no saved nickname string appears).
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_follow_up.py` — expected FAIL (module missing).
- [ ] **Step 3: Implement** the module.
- [ ] **Step 4: Run** — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/follow_up.py backend/tests/test_follow_up.py
git commit -m "feat: the follow-up reading - schema, reader input, the request after a reading"
```

### Task 3: The frame (`agent/frame.py`, pure code)

**Files:**
- Create: `backend/minemydinner/agent/frame.py`
- Test: `backend/tests/test_frame.py`

**Interfaces:**
- Consumes: `FollowUpReading`, `ReadRemoval`, `ReadAdd` (Task 2); `DishFacts` (`agent/revision.py`); `quote_matches` (`agent/revision.py`); `locked_ids` (`agent/edits.py`); `PlanSpec` (`planning/validate.py`).
- Produces: `Slot(kind: Literal["replace", "add"], dish_type: str, replaces: int | None = None, reason: Literal["asked", "breaks_rule"] = "asked")`; `Choice(dish_type: str | None, candidates: list[int], up_to: int, mode: Literal["drop", "replace"])`; `Missing(name: str, reason: Literal["not_in_library", "ambiguous", "over_time_limit", "breaks_rule"], detail: dict)`; `NamedLookup(status: Literal["found", "missing", "ambiguous"], recipe_id: int | None, title: str, blocked: str | None)`; `Frame(keep: list[int], drops: list[int], slots: list[Slot], choices: list[Choice], placed: list[int], missing: list[Missing], empty_plan: bool, ignored: list[dict])`.
- Produces: `build_frame(plan_ids: list[int], locked: set[int], facts: dict[int, DishFacts], spec_before: dict, spec_after: PlanSpec, reading: FollowUpReading | None, breaking: dict[int, str], lookup: Callable[[str], NamedLookup], message: str) -> Frame` — `breaking` maps a plan dish to the hard reason it breaks the new request (computed by the caller with `validate_plan`); `lookup` resolves a named dish; `message` is the user's words (quotes are checked against it). Rules: spec 5.4 exactly, with the general-wish limit from `max_general_changes` (`agent/revision.py`) and same-type slots unless the counts or a named replacement say otherwise. `ignored` lists removals code refused (id not in the plan; locked without a matching quote) with the reason, for the record.
- Produces: `lookup_named(session, name: str, request: dict) -> NamedLookup` — exact title, then a title within one differing character of the name (unique match only), else missing; `blocked` names a hard condition that hides a found dish (time limit, cookware, a diner rule), reusing the over-time-limit logic in `planning/tools.py`.

- [ ] **Step 1: Write the failing tests** (pure, with hand-built facts): named drop and named replace (same type slot); replace with a named library dish (placed, no slot); a named dish missing / ambiguous / over the time limit (`missing` with its reason, the replaced dish kept); a locked dish removed with a matching quote (allowed) and without (in `ignored`, kept); an id not in the plan (`ignored`); dishes that break a new one-meal avoidance or rule become same-type replace slots, locked included; a general wish gives one `Choice` of up to `max_general_changes(unlocked)` unlocked dishes; a stated lower count gives a drop `Choice` of exactly the difference over unlocked dishes of that type; an uncovered must-use ingredient gives one replace `Choice`; a change that leaves no dish sets `empty_plan`; `reading=None` (a button) builds the frame from the specs alone; `test_lookup_one_character_off_is_matched_and_two_matches_are_ambiguous` (Review Focus 5).
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_frame.py` — expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/frame.py backend/tests/test_frame.py
git commit -m "feat: the change frame - which dishes keep, drop or become slots, decided in code"
```

### Task 4: The picker (`agent/picker.py`)

**Files:**
- Create: `backend/minemydinner/agent/picker.py`
- Modify: `backend/minemydinner/agent/planner.py` (`PICK_TOOLS_VERSION`, a picker agent with `PICK_SEARCH_LIMIT = 2` tool calls per attempt)
- Test: `backend/tests/test_picker.py`

**Interfaces:**
- Consumes: `Frame`, `Slot`, `Choice` (Task 3).
- Produces: `PickerAnswer`: `choices: list[ChoiceAnswer]` (`index: int`, `ids: list[int]`); `slots: list[SlotAnswer]` (`index: int`, `recipe_id: int | None`, `reason: str = ""`); `wish_met: bool | None = None`; `note: str = ""`.
- Produces: `picker_messages(frame: Frame, plan_lines: str, rules_text: str, wish: str, shortlist: str, locale: str) -> list[BaseMessage]`.
- Produces: `apply_picks(plan_ids: list[int], frame: Frame, answer: PickerAnswer, facts: dict[int, DishFacts]) -> PickOutcome` with `PickOutcome(recipe_ids: list[int], problems: list[str], unfilled: list[Slot])`: applies drops and choices, fills slots; a problem for a choice outside its candidates or over `up_to`, a slot answer of the wrong type, a dish already in the plan or picked twice; an unfilled replace slot keeps its original dish, except a slot made because the dish breaks a diner rule or a one-meal avoidance, whose dish is removed (spec 5.5). `Slot` therefore carries `reason: Literal["asked", "breaks_rule"] = "asked"` (Task 3).
- Produces: `PICK_MAX_ATTEMPTS = 2`.

- [ ] **Step 1: Write the failing tests:** each problem kind above (Review Focus 4); an unfilled replace keeps the dish; an unfilled `breaks_rule` slot removes the dish; a filled add grows the plan; `test_picker_messages_show_slot_types_and_limits`.
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_picker.py` — expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/picker.py backend/minemydinner/agent/planner.py backend/tests/test_picker.py
git commit -m "feat: the dish picker - schema, input, and checking its answer against the frame"
```

### Task 5: The two prompts

**Files:**
- Create: `backend/minemydinner/agent/prompts/follow_up_v1.md`, `backend/minemydinner/agent/prompts/pick_v1.md`
- Modify: `backend/minemydinner/agent/request.py` (`PROMPT_VERSIONS["follow_up"] = "follow_up_v1"`, `PROMPT_VERSIONS["pick"] = "pick_v1"`; `load_prompt` accepts both), `docs/prompt-versions.md`
- Test: `backend/tests/test_agent_request.py`

- [ ] **Step 1: Write the failing tests:** both prompts load; `len(follow_up_v1) < (len(route_v6) + len(revise_v4)) / 2` and `len(follow_up_v1) + len(pick_v1) < len(route_v6) + len(revise_v4)` (spec 5.8a, spec 6); hash pins for `route_v6` and `revise_v4` unchanged.
- [ ] **Step 2: Run** — expected FAIL.
- [ ] **Step 3: Write the prompts** from spec 5.2, 5.5, 5.6 and 5.8: principles, not boundary rules; at most five short invented examples each; never held-out sentences (do not open held-out files).
- [ ] **Step 4: Run** — expected PASS; record each prompt's length in `docs/prompt-versions.md`.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/prompts/follow_up_v1.md backend/minemydinner/agent/prompts/pick_v1.md backend/minemydinner/agent/request.py backend/tests/test_agent_request.py docs/prompt-versions.md
git commit -m "feat: follow_up_v1 and pick_v1 - principle prompts for the reader and the picker"
```

### Task 6: The graph reads a follow-up once

**Files:**
- Modify: `backend/minemydinner/agent/graph.py`, `backend/minemydinner/agent/state.py`
- Test: `backend/tests/test_agent_graph.py`

**Interfaces:**
- Consumes: Tasks 2 and 5.
- Produces: node `read_follow_up` (label hooks as the other model nodes) storing `state["reading"]` (the reading as a dict); routing: a thread with a current plan and a message that is not a button goes `read_follow_up` instead of `route_intent`; `kind` → `change`: the change path (Task 7); `new_meal`: `parse_request` then today's new-plan path; `people`: `parse_request` then today's diner-card path; `other`: the existing `other` node. The choice card is no longer produced; a resume of a pending choice card from an older thread still works (Review Focus 1). "Has a plan" means a current plan with at least one dish (an early stop with no dishes keeps today's route path). The fixed messages keep today's paths and never reach the reader: "plan again" (`REPLAN_MESSAGE`) and the next-action button messages.

- [ ] **Step 1: Write the failing tests** (scripted reader): each kind routes as above with one reader call and no route call; "plan again" after a plan makes no reader call and reuses the request as today; a follow-up after an early stop with no dishes goes through the route as today; `other` leaves the plan, request and card untouched; an old checkpoint with a pending choice card resumes; the reader input contains the plan lines, the diner lines and the current `meal_avoid` (Review Focus 3).
- [ ] **Step 2: Run** — expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the graph tests — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/graph.py backend/minemydinner/agent/state.py backend/tests/test_agent_graph.py
git commit -m "feat: a follow-up on a plan is read once - no route call, the reading decides the path"
```

### Task 7: The change path: frame, picker, reply

**Files:**
- Modify: `backend/minemydinner/agent/graph.py`, `backend/minemydinner/agent/revise_step.py` (reply sentences)
- Test: `backend/tests/test_agent_graph.py`

**Interfaces:**
- Consumes: Tasks 2-6; `client_card`, `carry_unstated`, `wish_unmet_actions`, `revision_actions`, `over_limit_note`, `empty_plan_question`, the presenting sentences.
- Produces: nodes `build_change` (request after the reading or the button, `validate_plan` for `breaking`, `build_frame`, the empty-plan question), `pick_dishes` (picker attempts up to `PICK_MAX_ATTEMPTS`, skipped when the frame needs no choice), `present_change` (a new version when anything changed, else the kept plan; partial results per spec 5.5; reply = code's change sentences + the reading sentence when `ambiguous` + the picker's note + what could not be done + the unmet-wish sentence when `wish_met` is false; next actions only for unfinished parts). Button revisions use `reading=None` with the button's request and the offering turn's words as the wish context. Records: the reading, the frame (`ignored` included), each picker attempt and its validation.
- Produces: `CHANGE_PARTIAL_ZH/EN`, `READING_PREFIX_ZH/EN` (exact wording is the builder's; both languages).

- [ ] **Step 1: Write the failing tests** (scripted reader and picker): a named drop makes 1 reader call and 0 picker calls; a named replace makes 1 + 1; a general wish replaces only unlocked dishes within the limit; a new one-meal avoidance replaces a locked dish that breaks it and the reply names it; "去掉汤，加个<missing dish>" drops the soup and says the dish is not in the library (partial, `import_recipes` offered); an unfilled replace keeps the dish; the empty-plan question; an ambiguous reading's sentence leads the reply; `wish_met` false adds the unmet sentence; each button on a kept plan revises through the frame with no reader call; `request["button"]` recorded; no "Diner N" in any client payload.
- [ ] **Step 2: Run** — expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the graph tests and the full backend suite — expected PASS.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/graph.py backend/minemydinner/agent/revise_step.py backend/tests/test_agent_graph.py
git commit -m "feat: the change path - frame in code, picker inside it, partial changes said in the reply"
```

### Task 8: Remove what the new path no longer reaches

**Files:**
- Modify: `backend/minemydinner/agent/graph.py`, `backend/minemydinner/agent/revision.py`, `backend/minemydinner/agent/revise_step.py`, `backend/minemydinner/agent/planner.py`, tests that only covered removed code

- [ ] **Step 1:** List every function, node and constant only the old revise path used (the revise step, `RevisionProposal` checks R1-R9 as gates, `ask_revise_or_plan` production, route on follow-ups) and what old threads still need to load (state keys, card fields, the choice card's resume).
- [ ] **Step 2:** Remove the unreached code and its tests; keep loaders for old state.
- [ ] **Step 3: Run** the full backend suite and the frontend checks — expected PASS.
- [ ] **Step 4: User commit**

```powershell
git add -A backend/minemydinner backend/tests
git commit -m "refactor: remove the old revise step and route-on-follow-up code the new path no longer reaches"
```

### Task 9: What each model sees, and the request document

**Files:**
- Modify: `docs/planner-what-each-model-sees.md`, `docs/private/planner-what-each-model-sees.zh.md` (git-ignored), `docs/planner-request-and-counts.md`, `docs/private/planner-request-and-counts.zh.md`, `docs/design.md` (7.3, 7.5)

- [ ] **Step 1:** Generate the reader's and the picker's complete inputs from the code with a test-only script (not committed) for the conversation "今晚两个人，三个菜，可以买菜" → a plan → "茄子这道不要", with `library_revise.yaml` dishes.
- [ ] **Step 2:** Rewrite the revise sections: the reader (receives, never receives, why, example), the frame (code), the picker; mark the route as first-turn only; the revise step retired.
- [ ] **Step 3:** Update the request document (the request after a reading, `meal_avoid` as a whole list, buttons through the frame) and `docs/design.md` 7.3/7.5.
- [ ] **Step 4: User commit**

```powershell
git add docs/planner-what-each-model-sees.md docs/planner-request-and-counts.md docs/design.md
git commit -m "docs: the reader, the frame and the picker in what each model sees, the request and the design"
```

### Task 10: Evaluation runner: two layers

**Files:**
- Modify: `backend/minemydinner/evaluation/planning.py`, `backend/minemydinner/evaluation/revise_eval.py`
- Test: `backend/tests/test_planning_eval.py`

**Interfaces:**
- Produces: `must_failures(facts) -> list[str]` run on every scenario (spec 6's list), counted as `must_violations` in the summary; `unnecessary_question(facts) -> bool` (spec 6: a question other than the empty-plan question when the scenario allows a change), counted as `unnecessary_questions` with its rate; the allowed-changes derivation from existing keys (spec 6); `expect_route` mapped from the reading's kind; per-scenario and summary counts of reader calls, picker calls, picker attempts and frame `ignored` entries; prompt lengths in `summary.json`.

- [ ] **Step 1: Write the failing tests** (stubbed `_drive`): each must-check fails when it should and passes when it should; the kind mapping; the allowed-changes derivation with no keys checks only locks, counts and rules.
- [ ] **Step 2: Run** `$PY -m pytest -q tests/test_planning_eval.py` — expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Rewrite the development set** (`scenarios_revise.yaml`) to two layers (new sentences only; scenarios that tested the route or the choice card become act-and-say scenarios); dry-load every scenario file through `load_scenarios` (the controller dry-loads the held-out file and converts its `TODO(Task 10)` lines, showing the user).
- [ ] **Step 5: Run** the eval tests and the full suite — expected PASS.
- [ ] **Step 6: User commit**

```powershell
git add backend/minemydinner/evaluation backend/eval_data/planning/scenarios_revise.yaml backend/tests/test_planning_eval.py
git commit -m "eval: must-never-be-wrong checks on every scenario, allowed changes from existing keys, the development set in two layers"
```

### Task 10b: Code checks, it does not interpret (spec 5.4 amendment, user decision 2026-11-23)

Added after Task 10. Four code rules made judgments that need reading language (same-type slots, title matching beyond an exact title, at least one change for a general wish, a quote that must match a locked dish's title or ingredients); each needed a new exception for each new sentence. Spec 5.2-5.5 were amended so code holds only the invariants and checks results. `follow_up_v1` and `pick_v1` have not been used by any run yet, so they are edited in place (the size budget still holds).

**Files:**
- Modify: `backend/minemydinner/agent/follow_up.py` (`wish_targets`), `backend/minemydinner/agent/frame.py`, `backend/minemydinner/agent/picker.py`, `backend/minemydinner/agent/change_step.py`, `backend/minemydinner/agent/graph.py`, `backend/minemydinner/agent/prompts/follow_up_v1.md`, `backend/minemydinner/agent/prompts/pick_v1.md`, `docs/prompt-versions.md` (new lengths)
- Modify: `docs/planner-what-each-model-sees.md`, `docs/private/planner-what-each-model-sees.zh.md`, `docs/design.md` (7.3), `docs/planner-request-and-counts.md` and its zh copy where they describe the four rules
- Test: `backend/tests/test_follow_up.py`, `backend/tests/test_frame.py`, `backend/tests/test_picker.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- `FollowUpReading.wish_targets: list[int] = []` — the plan dishes a taste wish touches when the wish is not about a dish the message already changes; possibly empty. `wish_general` is removed outright, with no loader kept for it: it exists only on this branch, and no checkpoint in the real database carries it (checked 2026-11-23).
- `build_frame`: a replace slot carries no dish type (`Slot.dish_type` is `None` for replace slots; an add keeps the reading's type). `wish_targets` become replace slots capped at `max_general_changes(unlocked)`, unlocked dishes only, the reader's order deciding which stay over the cap; an empty list changes nothing. A locked dish in `remove` is accepted when its quote is a non-empty substring of this turn's message (no title or ingredient match); a locked dish still moves for a diner rule or a one-meal avoidance, never for stock, cookware or time. A named dish: an exact title (folded) is placed; anything else becomes a slot carrying the name (`Slot.named`), and the replaced dish is kept if the picker finds nothing.
- `picker_messages` / `apply_picks`: a replace slot shows the dish it replaces and the user's words; the picker's dish may be any type; code checks stated counts, an add's type, rules, stock, cookware, time, a must-use ingredient, and for a named slot nothing beyond those (the picker judges the name). "Not in the library" is said for a named slot only when the picker answered nothing fits.
- `lookup_named` keeps the exact-title path only; the one-character-off match is removed.

- [ ] **Step 1: Write the failing tests:** a meat dish breaking a "no meat" avoidance may be replaced by a vegetable dish; a wish whose targets are empty changes nothing and the reply says so; a wish with three targets on four unlocked dishes changes at most two; a locked dish listed with a quote from the message ("不适合的菜") is removed, and one listed with a quote not in the message is ignored; "小白菜" becomes a named slot and the picker's 清炒小白菜 is placed; a named slot the picker answers nothing for keeps the replaced dish and says not in the library; an exact title is placed with no picker call; stated counts still bind an any-type pick.
- [ ] **Step 2: Run** the four test files — expected FAIL.
- [ ] **Step 3: Implement**, edit both prompts in place by principle (no boundary rules; size budget), update `docs/prompt-versions.md`.
- [ ] **Step 4: Run** the four test files, then the full backend suite — expected PASS.
- [ ] **Step 5: Update the docs** listed above (regenerate the reader and picker example inputs with a scratchpad script on the test database).
- [ ] **Step 6: User commit**

```powershell
git add backend/minemydinner/agent backend/tests docs/prompt-versions.md docs/planner-what-each-model-sees.md docs/planner-request-and-counts.md docs/design.md
git commit -m "feat: code checks, it does not interpret - any-type replacements, wish targets from the reader, named dishes through the picker"
```

### Task 11: Development runs (paid, with the user's approval)

- [ ] **Step 0:** The controller reads the two regression revise sets and lists, for the user, each expectation that still conflicts with the design after Task 10b, with a proposed change; the user approves each before any edit.
- [ ] **Step 1:** Estimate one run of `scenarios_revise.yaml` from round 10's cost; wait for approval.
- [ ] **Step 2:** Run on the test database; read every scenario not as expected and every must violation.
- [ ] **Step 3:** Fix code or make the next prompt version (a used prompt is never edited; the size budget holds); at most three runs, each with its estimate and approval; then list what is left and ask the user.
- [ ] **Step 4:** Backend suite — expected PASS.
- [ ] **Step 5: User commit** (code, prompts, `docs/prompt-versions.md`, run folders).

### Task 12: Evaluation round, documents and carryover

**Files:** `docs/planning-evaluation-history.md`, `docs/design.md`, `docs/prompt-versions.md`, `docs/phase-1-carryover.md`, run folders

- [ ] **Step 1:** Estimate the round (development, new held-out, the two regression revise sets, every older set once); wait for approval.
- [ ] **Step 2:** Run it on the test database.
- [ ] **Step 3:** Record round 11 against every threshold in spec 6, each miss with its cause; held-out misses are recorded, not fixed.
- [ ] **Step 4:** Update the carryover (close what this plan closed; add "Deferred from Phase 1 plan 3b-2 (one)"), `docs/design.md` if needed, the scenario-set table.
- [ ] **Step 5:** Backend suite and frontend checks — expected PASS.
- [ ] **Step 6: User commit**

```powershell
git add docs/planning-evaluation-history.md docs/design.md docs/prompt-versions.md docs/phase-1-carryover.md backend/eval_data/planning/runs
git commit -m "docs: plan 3b-2 (one) evaluation round, design and carryover updates"
```
