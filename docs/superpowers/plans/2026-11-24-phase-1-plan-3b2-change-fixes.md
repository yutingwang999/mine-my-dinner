# Phase 1 Plan 3b-2 Change Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the causes of round 11's six change-path misses, take four small final-review leftovers along, and confirm the result in round 12.

**Architecture:** Most fixes change what the picker is shown (`agent/picker.py`, the picker's shortlist in `agent/graph.py`) and one new picker prompt (`pick_v5`). Two are code gaps after a change: a next action (`agent/change_step.py`) and a reply sentence fed by two new typed `Frame` fields (`agent/frame.py`, `present_change`). Code still only states facts it holds; every judgement of words stays with a model.

**Tech Stack:** Python 3.13, FastAPI, LangGraph, LangChain `create_agent`, pydantic, pytest; DeepSeek through the project's LLM layer.

**Spec:** `docs/superpowers/specs/2026-11-24-phase-1-plan-3b2-change-fixes-design.md` (parent: `docs/superpowers/specs/2026-11-22-phase-1-plan-3b2-revise-simplification-design.md`)

## Global Constraints

- **Code checks, it does not interpret** (3b-2 design 5.4 amendment). No change may make code decide what words mean, which dish a wish touches, or what a new dish should be. Code may only state facts it holds (where a slot came from, a recipe's main ingredients, that a cap was applied).
- The planner's input and the new-plan path do not change: `shortlist_json` output for the planner and `build_shortlist_result` stay byte-for-byte as today.
- A used prompt version is never edited: `pick_v4.md` and `follow_up_v4.md` stay frozen (hash test in `tests/test_agent_request.py`). The picker's change is the new file `pick_v5.md`.
- `PICK_TOOLS_VERSION` does not change (no answer-schema change; approach A).
- A change turn makes at most 7 model calls (`PICK_TURN_MODEL_CALLS` = 6 plus the reader).
- Held-out scenario files (`scenarios_revise_heldout.yaml`, `scenarios_revise_heldout2.yaml`, `scenarios_revise_heldout3.yaml`, `scenarios_heldout*.yaml`) are never opened by implementers or reviewers, and held-out sentences never go into prompts, docs or tests. Do not grep or glob broadly over scenario files.
- Python: the interpreter in `.vscode/settings.json` (`python.defaultInterpreterPath`); never install packages; run one pytest process at a time; never read or edit `.env`.
- Git: the user makes every commit. A "Commit" step means: stop, report the files and a commit message, and wait; do not run `git add` or `git commit`.
- Paid runs (development runs, round 12) need the user's approval and a cost estimate first, and use the test database only (`DATABASE_URL` set to the test URL for the run).
- Repository files in English; Chinese only in the allowed places (UI translations, dictionaries, Chinese recipe text in tests and prompt examples, `README.zh-CN.md`). When a doc in `docs/` changes, its Chinese copy in `docs/private/` changes too.
- Every finding deferred in a task review goes into `docs/phase-1-carryover.md` with a status tag (`Open`, `Note`, ...), in a section "Deferred from Phase 1 plan 3b-2 change fixes".

## Review Focus

1. A replace slot that is also named ("换成萝卜": `words` and `named` both set): the new slot line must not tell the picker to ignore the words in a way that drops the named dish — the "not from these words" clause appears only when the slot has no `named`. (Task 1 test.)
2. A recipe whose lines are all optional, or that has no lines: its shortlist `main` is `[]`, never an error. (Task 1 test.)
3. The planner's shortlist JSON carries no `main` field (new-plan sets stay comparable). (Task 1 test.)
4. "Any mix" on a typed add with no stated count: pressed, the plan grows by the asked dish as any type; it never shrinks and never asks for the count. (Task 3 graph test.)
5. Capped wish targets together with locked wish targets, and capped targets on a turn whose wish is unmet: the capped sentence appears once, the locked sentence only when every target is locked, and the unmet-wish sentence still appears. (Task 3 tests.)

---

### Task 1: What the picker is shown (fixes 1, 2, and the input half of 4)

**Files:**
- Modify: `backend/minemydinner/agent/picker.py` (`_slot_line`)
- Modify: `backend/minemydinner/agent/graph.py` (`shortlist_json`, `picker_shortlist`)
- Test: `backend/tests/test_picker.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- Produces: `shortlist_json(entries, complete=(), mains: dict[int, list[str]] | None = None) -> str` — when `mains` is given, each candidate gets `"main": mains.get(id, [])` placed right after `"title"`; when `None` (the planner), output is unchanged.
- Produces: the slot-line formats below, which Task 2's prompt examples copy.

Slot-line formats (spec 5.1, 5.4); everything after these prefixes (`, for the wish`, the named clause, the must-use clause) stays as today:

| Slot | Line |
|---|---|
| replace with `words`, no `named` | `- Slot {i}: in place of {replaces}, which goes because of the user's words "{words}"; choose the new dish from the wish and the meal's rules, not from these words` |
| replace with `words` and `named` | `- Slot {i}: in place of {replaces}, which goes because of the user's words "{words}"` |
| replace with no `words` | `- Slot {i}: in place of {replaces}` (as today) |
| add with `dish_type` | `- Slot {i}: one more {dish_type} the user asked for` |
| add with no `dish_type` | `- Slot {i}: one more dish the user asked for, of any type` |

- [ ] **Step 1: Write the failing tests** in `tests/test_picker.py`:
  - `test_slot_line_says_the_quote_is_why_the_dish_goes`: a replace `Slot(kind="replace", replaces=12, words="今晚鸡肉就算了")` → line equals the first row above with `i=0`.
  - `test_slot_line_with_a_named_replacement_keeps_the_name` (Review Focus 1): `words="把辣椒炒肉换成萝卜", named="萝卜"` → contains `which goes because of the user's words`, contains `the user named "萝卜"`, and does not contain `not from these words`.
  - `test_slot_line_for_an_asked_add`: `Slot(kind="add", dish_type="meat dish")` → `- Slot 0: one more meat dish the user asked for`; `Slot(kind="add")` → `- Slot 0: one more dish the user asked for, of any type`; a `for_wish=True` add ends with `, for the wish`.
  - Update existing assertions that expect `the user's words: "` or `, added` to the new formats.
- [ ] **Step 2: Write the failing tests** in `tests/test_agent_graph.py`:
  - `test_picker_shortlist_lines_carry_main_ingredients`: a change turn whose picker input shortlist has a candidate with main lines (`role="main"`) → its JSON object has `"main": [...]` right after `"title"`; a candidate with no main-marked lines gets its first three required lines.
  - `test_picker_shortlist_main_is_empty_for_a_recipe_without_required_lines` (Review Focus 2) → `"main": []`.
  - `test_planner_shortlist_has_no_main_field` (Review Focus 3): the planner's opening message from `planner_opening` contains no `"main"` key.
- [ ] **Step 3: Run to see them fail**
  Run: `& $py -m pytest tests/test_picker.py tests/test_agent_graph.py -k "slot_line or main" -q` (from `backend/`)
  Expected: FAIL (old line formats; no `main` key).
- [ ] **Step 4: Implement** `_slot_line` per the table, and `shortlist_json(..., mains=None)`. In `picker_shortlist`, get `mains` from `dish_facts(session, ids)` (it already returns `(facts, mains)`) for the shortlist's candidate ids, and pass them to `shortlist_json`.
- [ ] **Step 5: Run the picker, frame and graph tests**
  Run: `& $py -m pytest tests/test_picker.py tests/test_frame.py tests/test_agent_graph.py -q`
  Expected: all pass.
- [ ] **Step 6: Docs** — `docs/planner-what-each-model-sees.md` (the picker's input: slot lines and the shortlist's `main`), `docs/planner-chat-flow.md` ("What the picker sees"), and their Chinese copies `docs/private/planner-what-each-model-sees.zh.md`, `docs/private/planner-chat-flow.zh.md`.
- [ ] **Step 7: Commit** (user) — `fix: the picker sees why a dish goes, that an add was asked for, and each candidate's main ingredients`

---

### Task 2: `pick_v5` (fix 3, and the principle half of 4)

**Files:**
- Create: `backend/minemydinner/agent/prompts/pick_v5.md` (from `pick_v4.md`)
- Modify: `backend/minemydinner/agent/request.py` (`PROMPT_VERSIONS["pick"] = "pick_v5"`)
- Test: `backend/tests/test_agent_request.py`

**Interfaces:**
- Consumes: Task 1's slot-line formats (the examples must use them).
- Produces: run label `route_v6+parse_v8+planner_v5+follow_up_v4+pick_v5`.

`pick_v5` differs from `pick_v4` in exactly these points (spec 5.3, 5.4); everything else is copied unchanged:
1. The `wish_result` principle: "yes" when the new dishes chosen meet the wish, even when not every dish the wish touches could change; "no" when the dishes chosen for the wish do not meet it, or nothing could be chosen for it; "no_wish" when there is no wish.
2. A new principle: a slot the user asked for is filled whenever a usable dish exists; the meal's balance (enough meat or vegetable dishes already) is not a reason to answer 0.
3. The examples' slot lines follow Task 1's formats (for example line 22's `in place of 12, the user's words "菠菜"` becomes the new replace format). No held-out sentence appears.

- [ ] **Step 1: Write the failing tests:**
  - `test_prompt_version_label` (existing, line 42): expect `route_v6+parse_v8+planner_v5+follow_up_v4+pick_v5`.
  - `test_pick_v5_differs_from_pick_v4_only_in_the_wish_and_asked_add_principles`: every non-example line of `pick_v4.md` other than the `wish_result` principle line is in `pick_v5.md`; `pick_v5.md` contains `"yes"` near `chose` / `chosen` and a line containing `asked for` and `balance`.
  - `test_pick_v5_size`: `len(pick_v5) <= len(pick_v4) + 600` and `len(follow_up_v4) + len(pick_v5) < 12817` (the 3b-2 design's prompt-size bound).
  - The `pick_v4` frozen-hash test stays as it is.
- [ ] **Step 2: Run to see them fail** — `& $py -m pytest tests/test_agent_request.py -q`; expected FAIL (no `pick_v5.md`).
- [ ] **Step 3: Write `pick_v5.md`** and switch `PROMPT_VERSIONS["pick"]`.
- [ ] **Step 4: Run** `& $py -m pytest tests/test_agent_request.py tests/test_picker.py tests/test_agent_graph.py -q` — all pass.
- [ ] **Step 5: Docs** — `docs/prompt-versions.md` (a new row for `pick_v5`: what changed, why, which round-11 scenarios), `docs/planner-what-each-model-sees.md` and `docs/planner-chat-flow.md` (version names), and their Chinese copies.
- [ ] **Step 6: Commit** (user) — `feat: pick_v5 - wish_result judges the dishes chosen; an add the user asked for is filled when a dish can be made`

---

### Task 3: After a change — "any mix" for a typed add, and capped wish targets said (fixes 5, 6, final review M4)

**Files:**
- Modify: `backend/minemydinner/agent/frame.py` (`Frame`, the wish-target loop)
- Modify: `backend/minemydinner/agent/change_step.py` (`change_actions`; `side_adds_left` / `relax_split_spec` only if Step 1's graph test shows they need it)
- Modify: `backend/minemydinner/agent/revise_step.py` (new sentence)
- Modify: `backend/minemydinner/agent/graph.py` (`present_change`)
- Test: `backend/tests/test_frame.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- Produces: `Frame.locked_wish_targets: list[int] = []` and `Frame.capped_wish_targets: list[int] = []`, filled by `build_frame` where it now appends `ignored` entries with reasons `locked` and `over_limit` (the `ignored` entries stay, as the record).
- Produces: `wish_targets_capped(locale: str) -> str` in `agent/revise_step.py` with the fixed copy:
  - zh: `一次最多为愿望换一半的菜，其余的先留着；还想换的话可以再说。`
  - en: `A wish changes at most half of the dishes at a time, so the rest stay; ask again to change more.`

- [ ] **Step 1: Write the failing tests:**
  - `test_frame` — `test_wish_targets_over_the_cap_are_listed_as_capped`: 4 unlocked dishes, `wish_targets` all four → 2 replace slots, `capped_wish_targets` = the last two in the reader's order; `test_locked_wish_targets_are_listed`: a locked target → in `locked_wish_targets`.
  - graph — `test_a_typed_add_left_unfilled_offers_any_mix_without_a_stated_count`: reading `add=[{dish_type: "meat dish", count: 1}]`, no counts, no meat dish usable → the card's `next_actions` include `relax_split`.
  - graph — `test_any_mix_on_a_typed_add_without_a_stated_count_grows_the_plan` (Review Focus 4): press `relax_split` on that card → the picker's slot line is `one more dish the user asked for, of any type`; the new plan has one dish more than the card; no question is asked.
  - graph — `test_capped_wish_targets_are_said`: 4 unlocked dishes, all four wish targets, picker fills both slots → reply contains `wish_targets_capped("zh")`.
  - graph — `test_capped_and_locked_and_unmet_wish_sentences_together` (Review Focus 5): targets = 1 locked + 4 unlocked, `wish_result` "no" → the capped sentence once, no "every target locked" sentence, and the unmet-wish sentence.
- [ ] **Step 2: Run to see them fail** — `& $py -m pytest tests/test_frame.py tests/test_agent_graph.py -k "capped or locked_wish or any_mix" -q`; expected FAIL.
- [ ] **Step 3: Implement.**
  - `change_actions`: an unfilled add item whose `dish_type` is a meat or vegetable dish offers `relax_split` whether or not its side was stated (drop the `category in stated` condition; keep `stated` in the signature for callers, or remove it with its callers).
  - If the second graph test fails on the button turn, make `side_adds_left` count unfinished typed adds when the card's turn stated no count, so `relax_split_spec` keeps the total that turn asked for.
  - `present_change` reads `frame.locked_wish_targets` instead of filtering `frame.ignored`, and adds `wish_targets_capped(locale)` to the reply when `frame.capped_wish_targets` is not empty, after the change sentences and before the picker's note.
- [ ] **Step 4: Run** `& $py -m pytest tests/test_frame.py tests/test_picker.py tests/test_agent_graph.py tests/test_agent_revise.py -q` — all pass.
- [ ] **Step 5: Docs** — `docs/planner-chat-flow.md` ("When the change cannot all be done", "How code builds the frame" step 3), and its Chinese copy.
- [ ] **Step 6: Commit** (user) — `fix: an unfilled typed add offers any mix; capped wish targets are said; the frame names locked and capped wish targets`

---

### Task 4: Small leftovers (final review M1, item 5, M3; dead code)

**Files:**
- Modify: `backend/minemydinner/agent/graph.py` (`read_paused_turn`, `build_change`'s budget, `present_change`'s record)
- Modify: `backend/minemydinner/agent/edits.py` (remove `EditAction`)
- Modify: `backend/minemydinner/evaluation/tool_call_cut.py`
- Test: `backend/tests/test_agent_graph.py`, `backend/tests/test_tool_call_cut.py`

**Interfaces:**
- Produces: `build_change`'s change dict gains `"earlier_calls": int` — model-call events of this turn before the reader (the parse of an older paused thread); the picker budget becomes `PICK_TURN_MODEL_CALLS - reader_reasks - earlier_calls`.
- Produces: `tool_call_cut.replay_model() -> tuple[str, str]` returning `(api_url, model_id)` from `load_llm_config()`: task `planning_eval`'s primary model; its provider must be `deepseek` (URL `https://api.deepseek.com/chat/completions`), otherwise exit with a clear message. `API_URL` and `MODEL` constants go.

- [ ] **Step 1: Write the failing tests:**
  - `test_an_older_paused_thread_keeps_the_parse_event_and_stays_within_seven_calls`: an older thread paused on a question (as in the existing `read_paused_turn` tests), a picker that answers empty every time → the planning run holds the parse `model_call` event and the reader's, and the turn's model calls are ≤ 7.
  - `test_the_outcome_record_names_no_dish_the_final_check_removed`: extend the scenario of `test_a_rule_breaking_dish_left_in_the_result_is_removed_by_the_final_check` → every `recipe_id` in the `change_outcome` event's `slots` is `None` or in its `recipe_ids`.
  - `test_replay_model_reads_the_config`: with the default config → `("https://api.deepseek.com/chat/completions", <planning_eval primary model id>)`; with a config whose primary is not deepseek → `SystemExit`.
- [ ] **Step 2: Run to see them fail** — `& $py -m pytest tests/test_agent_graph.py tests/test_tool_call_cut.py -k "paused_thread or outcome_record or replay_model" -q`; expected FAIL.
- [ ] **Step 3: Implement.** `read_paused_turn` prepends the state's existing `model_calls` to the reader's events. After the final check in `present_change`, slots whose `recipe_id` is not in the result get `recipe_id: None` and `why: "removed by the final rule check"`. Remove `EditAction`. `replay` uses `replay_model()`.
- [ ] **Step 4: Run the full backend suite** — `& $py -m pytest -q`; all pass.
- [ ] **Step 5: Carryover** — retag the closed items in `docs/phase-1-carryover.md` ("Final whole-branch review, deferred minors" 1, 3, 4, 5; round 11's six misses stay `Open` until round 12) with status lines.
- [ ] **Step 6: Commit** (user) — `fix: an older paused thread keeps its parse event within the turn budget; the outcome record matches the final check; the cut-answer replay reads the model from the config; EditAction removed`

---

### Task 5: Development runs (paid, user gate)

**Files:**
- Modify (only if a run shows a cause): files of Tasks 1-3; a further prompt change is `pick_v6`, never an edit of a used `pick_v5`.
- Modify: `docs/planning-evaluation-history.md` (a "Plan 3b-2 change fixes development runs" section, like the 3b-2 (one) one)

- [ ] **Step 1: Estimate and ask.** Development set `scenarios_revise.yaml` with `library_revise.yaml`, `--with-embeddings`, about $0.05 cache-aware / $0.26 uncached per run (round 11). Ask the user before each run.
- [ ] **Step 2: Run** from `backend/` with the test database (`docs/local-development.md`, "Run from backend/"): `& $py -m minemydinner.evaluation.planning --scenarios eval_data/planning/scenarios_revise.yaml --library eval_data/planning/library_revise.yaml --with-embeddings`. Also `& $py -m minemydinner.evaluation.tool_call_cut scan --runs <run folder>`.
- [ ] **Step 3: Read** the misses, must-never-be-wrong lines, and calls per change turn; check `meal_avoid_covers_hand_added` and `general_lighter_or_added` (the target scenarios in the development set). Record the run in the history doc.
- [ ] **Step 4:** If a target scenario misses for a cause this plan owns, fix it (as a fix round with its own tests), get approval, run again. At most three development runs; stop and report to the user if the third still misses.
- [ ] **Step 5: Commit** (user) — `eval: plan 3b-2 change fixes development run N`

---

### Task 6: Round 12, history and carryover (paid, user gate)

**Files:**
- Modify: `docs/planning-evaluation-history.md` (round 12 section; results of record), `docs/phase-1-carryover.md`, `docs/private/architecture-overview.md` and `.zh.md` (versions and status)

- [ ] **Step 1: Estimate and ask.** Round 11's sets cost about $0.17 cache-aware / $0.9 uncached in all.
- [ ] **Step 2: Run** (test database): the development set; the three revise sets with their libraries (`scenarios_revise_heldout3.yaml` + `library_revise3.yaml`, `scenarios_revise_heldout.yaml` + `library_revise.yaml`, `scenarios_revise_heldout2.yaml` + `library_revise2.yaml`), all with `--with-embeddings`; and the new-plan sets as in round 11 (baseline, plan 2a, counts, diners, held-out diners, held-out one diner). The controller runs and reads the held-out-derived sets; subagents never open them.
- [ ] **Step 3: Judge against spec 6:** must-never-be-wrong 0; the six target scenarios as expected; development ≥ 90%; revise sets ≥ round 11 rescored (heldout3 14/15, heldout 15/17, heldout2 17/20) with every miss explained; calls per change turn average ≤ 2.5, none over 7; unnecessary questions ≤ 20%; new-plan sets no regression. A regression expectation changes only with the user's approval.
- [ ] **Step 4: Record** round 12 in the history doc (tables as round 11; `heldout3` listed as a regression set from now on), retag round 11's six misses in the carryover, and add round 12's misses.
- [ ] **Step 5: Commit** (user) — `eval: round 12 - plan 3b-2 change fixes`
