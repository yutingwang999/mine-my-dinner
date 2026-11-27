# Phase 1 Plan 3b-2 (two) Diner Changes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First remove every compatibility branch for threads saved by earlier versions. Then make a follow-up that changes who eats show a diner card that starts from the confirmed set, and adjust the current plan after it instead of planning anew. `follow_up_v5` closes the reader's carryover items.

**Architecture:**
- The people path keeps today's order up to the card: reader, then `parse_v8`, then the diner card.
- The card gets a follow-up shape built from the confirmed set (`agent/diners.py`).
- After the card, the turn goes to `build_change` instead of `load_context`. There, code adds:
  - the new constraints;
  - counts moved by the difference of the default compositions;
  - a fact-based "make it again" switch.

  These come from a new pure module, `agent/diner_change.py`, plus a `remake` flag on `build_frame`.
- The picker, the checks and `present_change` are reused. The reply gains fixed who-eats sentences.

**Tech Stack:**
- Backend: Python 3.13, FastAPI, LangGraph, pydantic, pytest; DeepSeek through the project's LLM layer.
- Frontend: Next.js and React with next-intl; vitest.

**Spec:** `docs/superpowers/specs/2026-11-26-phase-1-plan-3b2-diner-changes-design.md`. Sections are cited as "spec 5.x". Its parent is `docs/superpowers/specs/2026-11-22-phase-1-plan-3b2-revise-simplification-design.md`.

## Global Constraints

- **Code checks, it does not interpret** (3b-2 design 5.4). Every decision is a fact code holds: the confirmed card, the plan, the rules, the counts. Otherwise it is a model's reading. Code never decides who joins or leaves from words, and never unticks a person from a parsed number (spec 5.2, user choice b).
- **No compatibility branches** (spec 5.9). Code reads only the current shape of thread, card and record. A change that would leave saved threads unreadable says so in its task report. It does not add a fallback.
- **Frozen prompts.** A used prompt version is never edited. `follow_up_v4`, `pick_v6`, `parse_v8`, `route_v6` and `planner_v5` stay byte for byte, with a hash test each. The reader's change is the new file `agent/prompts/follow_up_v5.md`.
- **Reader size.** `follow_up_v5` stays under 6,900 characters (spec 3). `follow_up_v4` is 5,788.
- **Turn budget.** A change turn, people turns included, makes at most 7 model calls: the reader, the parse and the picker's calls. The parse comes out of the picker's budget (`PICK_TURN_MODEL_CALLS` = 6), as reader re-asks do.
- **Privacy.** Saved nicknames never reach a model. "Diner N" is never shown to the user. `client_card` keeps server-only fields away from the client.
- **Held-out files.** Never opened by implementers or reviewers: `scenarios_revise_heldout*.yaml` and `scenarios_heldout*.yaml`. Held-out sentences never go into prompts, docs or tests. No broad grep or glob over scenario files.
- **Regression sets.** Changing an expectation in one needs the user's approval first.
- **Python.**
  - Use the interpreter in `.vscode/settings.json` (`python.defaultInterpreterPath`). Below it is `$py`, run from `backend/`.
  - Never install packages. Run one pytest process at a time.
  - Never read or edit `.env`.
- **Frontend tests.** Run them from PowerShell: `npx vitest run <file>` in `frontend/`. Git Bash fails collection.
- **Git.** The user makes every commit. A "Commit" step means: stop, report the files and a commit message, and wait.
- **Real database.** Never touch it in tests or checks. Paid runs need a cost estimate and the user's approval, and use the test database (`DATABASE_URL` set to the test URL for the run).
- **Docs.**
  - Repository files are in English. Chinese is allowed only in UI translations, dictionaries, Chinese recipe text in tests and prompt examples, and `README.zh-CN.md`.
  - When a doc in `docs/` changes, its Chinese copy in `docs/private/` changes in the same task.
  - When a model's input changes, update `docs/planner-what-each-model-sees.md` in the same task.
- **Carryover.** Every finding deferred in a task review goes into `docs/phase-1-carryover.md`, in a new section "Deferred from Phase 1 plan 3b-2 (two)", with a status tag.

## Review Focus

1. **A people turn where no dish changes.** For example 3 → 4 people (equal defaults) with no rule broken:
   - The new constraints and headcount persist in the state and in `plan_request`.
   - A new version shows the new diners line.
   - The reply leads with the who-eats sentence, never "nothing changed".

   (Task 6 graph test.)
2. **A guest confirmed earlier survives a follow-up card.** The guest keeps its key, its label and its meal-only rules. Confirming the card untouched creates no new label. (Task 3 test.)
3. **A friend of the confirmed set deleted on the people page before the follow-up.** The follow-up card does not preselect the deleted id, and confirming it does not raise `CardOutOfDate`. (Task 3 test.)
4. **Moved counts that cannot be met because the dishes that would go are locked.** For example 5 → 1 people with every dish locked:
   - No locked dish leaves for a count.
   - The plan stays.
   - The reply says the count could not be met.

   (Task 6 graph test.)
5. **A "make it again" turn on a plan with hand-added dishes.** Every locked dish that breaks no rule stays at its place. The picker is called once with the other dishes as replace slots. (Task 6 graph test.)

---

### Task 1: Remove the backend compatibility branches

Spec 5.9. The real database's old threads are already cleared. This task removes the code that served them, with their tests.

**Files:**
- Modify:
  - `backend/minemydinner/agent/graph.py`
  - `backend/minemydinner/agent/state.py`
  - `backend/minemydinner/agent/frame.py`
  - `backend/minemydinner/agent/change_step.py`
  - `backend/minemydinner/agent/card_text.py`
  - `backend/minemydinner/agent/edits.py`
  - `backend/minemydinner/agent/diners.py`
  - `backend/minemydinner/agent/labels.py` (if the empty-table start lives there)
  - `backend/minemydinner/planning/validate.py`
  - `backend/minemydinner/agent/request.py` (comment at the `meal_avoid` field)
- Rename:
  - `backend/minemydinner/agent/revise_step.py` → `backend/minemydinner/agent/change_text.py` (the change reply's sentences);
  - `backend/minemydinner/agent/revision.py` → `backend/minemydinner/agent/change_rules.py` (`revision_spec`, `carry_unstated`, `max_general_changes`, `COUNT_GROUP`, `unset`, `DishFacts`).
  - Update every import, including `agent/follow_up.py`, tests and `evaluation/`.
- Test:
  - `backend/tests/test_agent_graph.py`, `backend/tests/test_frame.py`, `backend/tests/test_agent_revise.py`, `backend/tests/test_agent_revision.py`, `backend/tests/test_agent_diners.py`, `backend/tests/test_validate_plan.py`;
  - test files of the renamed modules: rename `test_agent_revise.py` → `test_change_text.py` and `test_agent_revision.py` → `test_change_rules.py`.
- Docs: `docs/planner-chat-flow.md` and `docs/private/planner-chat-flow.zh.md`. Remove the older-thread paths and the paused-question path.

**Interfaces:**
- Produces: `agent/change_text.py` and `agent/change_rules.py`, with the same public names as today's modules. Later tasks import from the new names only.
- Produces: `build_frame(...)` without `wish_choice`.
- Produces: `ChatState` without `revision`.
- Produces: `build_change`'s `change` dict:
  - without `words_for_picker`;
  - keeps `earlier_calls`, which now counts any `model_call` event of this turn that is not the reader's. On a people turn (Task 6) that is the parse.

Remove each of these, with the tests that exist only for it:

| Where | What goes |
|---|---|
| `graph.py` | `read_paused_turn`; its node and edges; the `after_parse` branch that returns it. A revise turn with no reading cannot happen now: `after_parse` with `_revises` and no reading falls to `select_diners` / `load_context` as the non-dish case does today. |
| `graph.py` `route_intent` / `_button_card` | The path for a card without `request` (`model_calls: []`, intent `plan`). `_button_card` returns the newest card. A button press with no card is a 409 from the API, as for any stale interrupt. If the API does not already refuse it, `route_intent` sends it to `unclear`. |
| `graph.py` `build_change` | `words_for_picker`, `wish_choice`, and the kept-card `request["query"]` branch. `base = state["plan_request"]`; drop the `previous_request` fallback. |
| `graph.py` | `_offering_words`' part for a card without `WISH_WORDS`, and `revision_words`' `earlier` merge. A card always has `wish_words` when `plan_kept`. |
| `graph.py` | Lines 355 and 410 (docstrings and fallbacks for cards without `request`). Lines 695 and 708 (notes without `left_out_details`, read as empty). Line 2496 ("a thread saved before the table existed"). |
| `graph.py` / `revision.py` | `current_meal_avoid` / `revision_meal_avoid`. The card's `meal_avoid` is always present: read it directly. |
| `state.py` | `revision`. The `proposal` note about a revise step's dump. `previous_request`, if after the removals nothing reads it. Grep before removing it, and keep it if anything still does. |
| `frame.py` | `wish_choice` and the "old card's wish choice" step. |
| `change_step.py:484` | The "old card's wish choice counts as targets" clause and its code. |
| `picker.py` / `graph.py` | `picker_wish` / words-for-picker input, if only `words_for_picker` used it. |
| `card_text.py:58` | The H4 finding without ranges. |
| `edits.py:161` | A card without `locked` reads as no locks. A card always carries `locked`. |
| `diners.py:491`, `card_needed` | The `stated_headcount is None → headcount` fallback. A set always stores `stated_headcount`; `None` means the parse gave none, so `card_needed` compares with `None` as the value. |
| `validate.py:60` | `split_is_minimum` in `PlanSpec`. First check that no file under `backend/eval_data/` that the evaluation code still loads carries it (`rg -l split_is_minimum backend/eval_data` lists only frozen run folders). If a loaded file carries it, keep the field and report it. |

Not removed:
- `DinerAnswer` without `headcount` (the evaluation runner sends none);
- frozen prompt files;
- fields that a current writer may leave out by design.

- [ ] **Step 1: Pin the new behaviour with failing tests.** In `tests/test_agent_graph.py`:
  - `test_button_turn_without_a_card_request_is_unclear`, or the API 409, whichever path Step 3 chooses.
  - `test_after_parse_never_routes_to_read_paused_turn`: a compiled graph has no node `read_paused_turn`.

  In `tests/test_frame.py`, `test_build_frame_has_no_wish_choice`: `inspect.signature(build_frame)` has no `wish_choice` parameter.
- [ ] **Step 2: Run them to see them fail.**
  Run: `& $py -m pytest tests/test_agent_graph.py tests/test_frame.py -k "unclear or read_paused or wish_choice" -q`
  Expected: FAIL.
- [ ] **Step 3: Remove the branches in the table.** Then delete the tests that exist only for them, for example those named `*older*`, `*legacy*`, `*saved_before*`, `*paused*`, `*wish_choice*` and `*split_is_minimum*`. List each deleted test in the task report.
- [ ] **Step 4: Rename the two modules and their test files; fix the imports.**
  Run: `rg -n "revise_step|agent\.revision\b|from minemydinner.agent.revision" backend`
  Expected: no hit.
- [ ] **Step 5: Run the whole backend suite.**
  Run: `& $py -m pytest -q`
  Expected: all pass.
- [ ] **Step 6: Check that no compatibility note is left.**
  Run: `rg -n -i "saved before|older thread|old card|older card|before plan 3b|stored before" backend/minemydinner`
  Expected: no hit, or only the `DinerAnswer` runner note. Report any remaining hit.
- [ ] **Step 7: Docs.** Update `docs/planner-chat-flow.md` and its Chinese copy.
- [ ] **Step 8: Commit (user).** Message: `refactor: remove compatibility branches for threads saved by earlier versions; rename revise_step/revision`

---

### Task 2: Remove the frontend compatibility branches

**Files:**
- Modify:
  - `frontend/src/lib/chat.ts` (lines 94, 102, 167, 176, 337, 389-400);
  - `frontend/src/components/PlanCard.tsx` (lines 100-104, 182, 228);
  - `frontend/src/components/MessageList.tsx` (the `revise_or_plan` history line).
- Test: `frontend/src/lib/chat.test.ts`, `frontend/src/components/PlanCard.test.tsx`, `frontend/src/components/ChatPage.test.tsx`.

**Interfaces:**
- Produces: `ChatPlan` with these fields required, as the backend writes them:
  - `edited`;
  - `left_out_details`;
  - a spec with `meat_bound` / `vegetable_bound`.
- Produces: `DinerSummary.self` required.
- Produces: the `AskedQuestion` union without `revise_or_plan` and without `split_exceeds_total`.

What goes:
- the one-plan thread fallback (chat.ts:337);
- the `split_is_minimum` reading (chat.ts:394-400);
- `split_exceeds_total` (chat.ts:176);
- defaults for absent `self`, `edited` and `left_out_details`;
- the `composition` entry of `defaults_used` (PlanCard.tsx:100);
- "none for a plan saved before 2b" (PlanCard.tsx:182): keep "none for a solo plan";
- left-out titles without ingredient names (PlanCard.tsx:228);
- the `revise_or_plan` case (MessageList.tsx and its translation keys in both locale files).

- [ ] **Step 1: Remove the branches and their tests.** Make the types required. Delete the tests that feed old shapes, for example in `ChatPage.test.tsx` around line 494.
- [ ] **Step 2: Type check and run the tests.**
  Run (PowerShell, `frontend/`): `npx tsc --noEmit; if ($?) { npx vitest run }`
  Expected: no type error; all tests pass.
- [ ] **Step 3: Check that no compatibility note is left.**
  Run: `rg -n -i "saved before|before plan|legacy|split_is_minimum|revise_or_plan|split_exceeds_total" frontend/src`
  Expected: no hit.
- [ ] **Step 4: Commit (user).** Message: `refactor(frontend): remove compatibility branches for old threads and plans`

---

### Task 3: The follow-up diner card (backend)

Spec 5.2, and 5.1 "a thread with no confirmed set".

**Files:**
- Modify: `backend/minemydinner/agent/diners.py`, `backend/minemydinner/api/chat.py` (only if `client_card` needs to pass the new fields).
- Test: `backend/tests/test_agent_diners.py`.

**Interfaces:**
- Produces: `settled_from_plan(self_id: int | None, constraints: dict) -> dict`. This is the `SettledDiners.to_state()` of a thread with no confirmed set:
  - `diner_ids = [self_id]` (empty when there is no user row);
  - `meal_rules = []`, `guest_names = {}`;
  - `others = headcount − len(diner_ids)`, never below 0;
  - `others_answer`: `"unsure"` when `constraints["unconfirmed_diners"] > 0` and `others > 0`; `"none"` when `others > 0` otherwise; `None` when `others == 0`;
  - `headcount = constraints["headcount"]`, and `stated_headcount = None`.
- Produces: `build_follow_up_card(session: Session, parsed: ParsedRequest, labels: LabelTable, matched_friend_ids: list[int], current_text: str, settled: dict, stated_headcount: int | None) -> dict`. It returns the same keys as `build_card`, plus three:
  - `"others_answer"`: the set's;
  - `"stated_total"`: `int | None`;
  - each guest entry's `"key"`: the settled `guest:<n>` key, or `None` for a new guest.

  Rows:
  - always the non-solo shape: the user and every saved friend;
  - `selected` for every id in `settled["diner_ids"]` that still exists, and every matched friend;
  - `proposed` holds the set's meal-only rules of that diner (`settled_rules(settled, f"diner:{id}")`), then the parse's new rules not already known (`not_yet_known` against saved rules and meal-only rules). The frontend ticks `proposed` with "save" off, which is exactly "chosen for this meal".

  Guests:
  - the set's guests (`guest_names`, in key order), each with `key`, `name` and `proposed` = its meal-only rules;
  - then the parse's guests not matched by `guest_key`, with `key: None`.

  Total:
  - `stated_headcount` given: `headcount = max(stated_headcount, preselected)`, where `preselected = selected rows + guests`. `stated_total = stated_headcount` only when it is below `preselected`.
  - Otherwise: `headcount = settled["headcount"] + new guests + newly matched friends`.

  Others and nudge:
  - `others = headcount − preselected`, never below 0;
  - `nudge` as in `build_card`.
- Produces: `GuestAnswer.key: str | None = None`, matching `^guest:\d+$`.
- Produces: `apply_answer(session, answer, labels, stated_headcount=None, settled: dict | None = None)`. A guest whose `key` is in `settled["guest_names"]` keeps that key, and its label is not re-created. Any other guest gets a new key as today.
- Produces: the card keeps `others_answer` and `stated_total` as fields of the interrupt value, so the frontend can read them.

- [ ] **Step 1: Write the failing tests** in `tests/test_agent_diners.py`.
  - `test_follow_up_card_confirmed_untouched_loses_nobody` (spec 5.2 invariant). A set has the user, a friend with a meal-only rule, a guest "小李" with a meal-only rule, and 1 unsure other. The parse adds nothing new. Build the card, then answer it as the runner's default does: each row's `proposed` ticked with `save` off, guests with their `key`, `others`. `apply_answer` then gives back:
    - the same `diner_ids`;
    - the same `meal_rules`;
    - the same guest key and name;
    - `others == 1` and `others_answer == "unsure"`;
    - the same headcount;
    - labels unchanged.
  - `test_follow_up_card_adds_what_the_message_names`. The same set, and the parse has one new guest "小王" with no headcount: the card total is the set's headcount + 1, and the new guest is listed with `key: None` after the set's guest.
  - `test_follow_up_card_stated_total_below_preselected` (user choice b). A set of three, parse headcount 1:
    - `headcount == 3` and `stated_total == 1`;
    - every set member is still `selected`.
  - `test_follow_up_card_never_preselects_a_deleted_friend` (Review Focus 3). The set holds a deleted friend's id: no row has it, and `apply_answer` on the default answer does not raise.
  - `test_guest_key_kept_on_a_follow_up_answer` (Review Focus 2). Answering with the settled guest's `key` keeps that key in `guest_names`. `labels` gains no new `guest:` key.
  - `test_settled_from_plan`. Two cases:
    - `constraints = {"diner_ids": [1], "guest_rules": [], "unconfirmed_diners": 1, "headcount": 2}` with `self_id = 1` gives `diner_ids [1]`, `others 1`, `others_answer "unsure"` and `headcount 2`;
    - headcount 1 gives `others 0` and `others_answer None`.
- [ ] **Step 2: Run them to see them fail.**
  Run: `& $py -m pytest tests/test_agent_diners.py -k "follow_up or guest_key or settled_from_plan" -q`
  Expected: FAIL (names not defined).
- [ ] **Step 3: Implement** `settled_from_plan`, `build_follow_up_card`, `GuestAnswer.key` and the `settled` parameter of `apply_answer`. Share the row building with `build_card` through a private helper rather than copying it.
- [ ] **Step 4: Run the diner and chat API tests.**
  Run: `& $py -m pytest tests/test_agent_diners.py tests/test_chat_api.py tests/test_diners_api.py -q`
  Expected: all pass.
- [ ] **Step 5: Commit (user).** Message: `feat: the follow-up diner card starts from the confirmed set`

---

### Task 4: The follow-up diner card (frontend)

**Files:**
- Modify:
  - `frontend/src/lib/chat.ts`: `DinersPending` gains `others_answer?: "none" | "unsure" | null` and `stated_total?: number | null`; `GuestRow` gains `key?: string | null`; `DinerAnswer` guests gain `key?: string | null`.
  - `frontend/src/components/DinerCard.tsx`.
  - The locale files: `chat.diners.statedBelow` in both languages.
- Test: `frontend/src/components/DinerCard.test.tsx`.

**Interfaces:**
- Consumes: the card fields of Task 3.
- Produces:
  - the answer's guests carry `key` for each guest that came with one;
  - `othersAnswer` starts from `pending.others_answer`;
  - when `pending.stated_total` is set, the card shows `t("statedBelow", { said: stated_total, ticked: eating })`. In Chinese: "你说的是 {said} 个人，现在勾选了 {ticked} 个，请把不来吃的取消勾选。" In English: "You said {said}, and {ticked} are ticked. Untick anyone not eating."

  The confirm button stays enabled.

- [ ] **Step 1: Write the failing tests** in `DinerCard.test.tsx`:
  - `sends the settled guest key back`: a pending card with a guest `{key: "guest:1", name: "小李", proposed: [...]}`, confirmed untouched, gives an answer whose guest has `key: "guest:1"` and the rule ticked with `save: false`.
  - `starts the unmatched answer from the card`: `others_answer: "unsure"` gives the "unsure" button pressed.
  - `shows the stated-below notice`: `stated_total: 1` with 3 preselected shows the notice text with 1 and 3. Confirm is enabled, and unticking two rows still lets the user confirm with total 1.
- [ ] **Step 2: Run them to see them fail.**
  Run (PowerShell, `frontend/`): `npx vitest run src/components/DinerCard.test.tsx`
  Expected: FAIL.
- [ ] **Step 3: Implement.** A removed guest that had a `key` simply is not sent.
- [ ] **Step 4: Type check and run the tests.**
  Run: `npx tsc --noEmit; if ($?) { npx vitest run src/components/DinerCard.test.tsx src/components/ChatPage.test.tsx }`
  Expected: pass.
- [ ] **Step 5: Commit (user).** Message: `feat(frontend): the follow-up diner card keeps settled guests and shows a stated total below the ticked people`

---

### Task 5: Counts and "make it again", in code

Spec 5.3 and 5.4.

**Files:**
- Create: `backend/minemydinner/agent/diner_change.py` (pure: no model, no database).
- Modify: `backend/minemydinner/agent/frame.py` (`remake` flag).
- Test: `backend/tests/test_diner_change.py` (new), `backend/tests/test_frame.py`.

**Interfaces:**
- Produces, in `agent/diner_change.py`:
  - `moved_counts(old_headcount: int, new_headcount: int, present: dict[str, int]) -> dict[str, int]`.
    - `present` is `count_categories` of the plan.
    - With `(d0, s0) = default_composition(old)` and `(d1, s1) = default_composition(new)`, it returns `{"dishes": max(1, present["dishes"] + d1 − d0)}` when `d1 ≠ d0`, and `{"soups": max(0, present["soups"] + s1 − s0)}` when `s1 ≠ s0`.
    - The result is empty when neither moves.
  - `message_sets_counts(reading: FollowUpReading) -> bool`: true when `reading.add` is non-empty or any `reading.counts` field is not null (spec 5.3, user choice 丙). A removal does not count.
  - `must_change(breaking: dict[int, str], present: dict[str, int], moved: dict[str, int]) -> int`. It returns the number of `breaking` entries whose reason is in `RULE_BREAKS`, plus `max(0, present["dishes"] − moved.get("dishes", present["dishes"]))`, plus the same for `"soups"`.
  - `remakes(plan_size: int, must: int) -> bool`: `must * 2 > plan_size` ("more than half").
  - `people_constraints(settled: dict | None, old: dict) -> dict`. It returns the constraints dict that `build_change` uses:
    - from `constraints_from(SettledDiners.from_state(settled))`, with `headcount = settled["headcount"]`, when `settled` is given;
    - otherwise `old` unchanged, which covers a skipped card.

    It does not apply the meal avoidance; the caller does, as today.
- Produces: `build_frame(..., remake: bool = False)`. After the rule-breaking step and before the wish step, every unlocked plan dish not yet handled becomes a replace slot (reason `"asked"`, no words). Later steps (adds, counts) then act on those slots as on any others; `remove_count` already takes replace slots first.

- [ ] **Step 1: Write the failing tests** in `tests/test_diner_change.py`:
  - `test_moved_counts_from_two_to_four`: `moved_counts(2, 4, {"dishes": 4, "soups": 0, ...})` returns `{"soups": 1}`. This is the spec 5.3 example: 2 people with a stated 4 dishes, 2 more join.
  - `test_moved_counts_from_four_to_five`: `moved_counts(4, 5, {"dishes": 2, "soups": 1, ...})` returns `{"dishes": 3}`.
  - `test_moved_counts_equal_defaults`: `moved_counts(3, 4, ...)` returns `{}`.
  - `test_moved_counts_floors`: `moved_counts(5, 1, {"dishes": 1, "soups": 0, ...})` returns `{"dishes": 1, "soups": 0}` (both floors hold). `moved_counts(5, 2, {"dishes": 2, "soups": 1, ...})` returns `{"dishes": 1, "soups": 0}`.
  - `test_message_sets_counts`: true with one add; true with `counts.soups = 1`; false with only a removal; false with only a wish.
  - `test_must_change_and_remakes`. Two rule breaks and one count drop on a 5-dish plan: `must == 3` and `remakes(5, 3)` is true. Two rule breaks on 5 dishes: `remakes(5, 2)` is false. A `"stock"` break does not count.
  - `test_people_constraints`: a settled set gives its ids, meal rules, unconfirmed count and headcount; `None` gives `old` unchanged.
- [ ] **Step 2: Write the failing tests** in `tests/test_frame.py`:
  - `test_remake_turns_every_unlocked_dish_into_a_replace_slot`. A plan has 4 dishes: one locked, and one unlocked that breaks a rule. `remake=True` gives replace slots for the 3 unlocked dishes; the locked dish is in `keep`.
  - `test_remake_keeps_a_locked_rule_breaker_out` (Review Focus 5). A locked dish that breaks a rule becomes a replace slot with reason `breaks_rule`. A locked dish that breaks none stays in `keep`.
  - `test_remake_with_a_lower_count_drops_replace_slots_first`. With `remake=True` and a stated dish total one lower, one replace slot is removed and its dish is in `drops` and `count_drops`.
- [ ] **Step 3: Run them to see them fail.**
  Run: `& $py -m pytest tests/test_diner_change.py tests/test_frame.py -k "moved or message_sets or must_change or people_constraints or remake" -q`
  Expected: FAIL.
- [ ] **Step 4: Implement** `agent/diner_change.py` and the `remake` step.
- [ ] **Step 5: Run the tests.**
  Run: `& $py -m pytest tests/test_diner_change.py tests/test_frame.py -q`
  Expected: all pass.
- [ ] **Step 6: Commit (user).** Message: `feat: diner-change counts and the make-it-again rule, in code`

---

### Task 6: The people turn adjusts the plan (graph)

Spec 5.1, 5.4, 5.5 and 5.8.

**Files:**
- Modify: `backend/minemydinner/agent/graph.py`, `backend/minemydinner/agent/state.py`.
- Test: `backend/tests/test_agent_graph.py`.

**Interfaces:**
- Consumes:
  - `build_follow_up_card`, `settled_from_plan` and `apply_answer(..., settled=)` from Task 3;
  - everything in `agent/diner_change.py` and `build_frame(remake=)` from Task 5.
- Produces two new `ChatState` fields:
  - `parsed_headcount: int | None`: the parse's own headcount, before `carry_unstated`. `parse_request` sets it on every parse.
  - `diners_skipped: bool`: set by `select_diners` (true for a skipped card); reset to `False` by `_fresh_turn`.
- Produces: `change["diner_change"]` on a people turn, read by Task 7:

  ```
  {"old_headcount": int, "new_headcount": int, "present": dict[str, int],
   "moved": dict[str, int], "must_change": int, "remade": bool, "skipped": bool}
  ```

  It is `None` on any other turn.
- Produces: a `diner_change` planning event with the same keys, recorded after the reader and parse events and before `frame` (spec 5.8).

Behaviour:
- **`select_diners`.** It uses `build_follow_up_card` when the thread has `settled_diners`, or when this is a people turn on a plan with dishes. In the second case it starts from `settled_from_plan(self_id, state["constraints"])`. It passes `state["parsed_headcount"]` as `stated_headcount`, and `settled=` to `apply_answer`.

  A skipped card:
  - on a people turn with dishes: leaves `settled_diners` as it was and sets `diners_skipped`;
  - on any other turn: as today.

  `new_meal` with a confirmed set also gets the follow-up shape, then `load_context` as today.
- **Edge after `select_diners`.** It is conditional: `build_change` when the reading's kind is `people` and `_has_dishes(state)`; otherwise `load_context`.
- **`build_change` on a people turn** (no button, `reading.kind == "people"`):
  - `old = state["constraints"]["headcount"]`.
  - `constraints = people_constraints(state["settled_diners"] if not state["diners_skipped"] else None, state["constraints"])`, then the meal avoidance as today. `new = constraints["headcount"]`.
  - `present = count_categories` of the live plan.
  - `moved = {}` if `message_sets_counts(reading)`, else `moved_counts(old, new, present)`. When `moved` is non-empty, the reading gets `counts = ReadCounts(**moved)` before `request_after`, so the moved counts bind as this turn's stated counts.
  - `request["headcount"] = new`.
  - After `validate_plan` with the new constraints, compute `must = must_change(breaking, present, moved)`.
  - Then set `remade = remakes(len(ids), must)` and also require that some plan dish is unlocked and does not break a rule. With every dish locked, a remake would change nothing, and the reply must not claim one. Pass `remake=remade` to `build_frame`.
- **Budget.** `earlier_calls` (Task 1) counts the parse, so the picker's budget is `PICK_TURN_MODEL_CALLS − reader_reasks − earlier_calls`.
- **The plan stays on a people turn** (`plan_kept` path in `present_change`):
  - the new constraints stay, and are not reset to the old ones;
  - `plan_request` takes the new headcount;
  - when the confirmed set changed, a new version is made, so the plan card's diners line shows the new set. Treat it as a change of conditions in the `new_conditions` check.

- [ ] **Step 1: Write the failing graph tests** in `tests/test_agent_graph.py`. Use the existing fake reader, parse and picker helpers. Each test drives a plan for N, then a follow-up read as `people`, then the card resumed.
  - `test_people_turn_adjusts_instead_of_planning_anew`. A plan for 2 with 2 dishes; a friend with a rule that breaks one dish joins.
    - The planner is not called.
    - The breaking dish is replaced.
    - The other dish stays.
    - The new version's headcount is 3, and it has one soup (2+0 → 2+1).
  - `test_people_turn_with_an_add_does_not_apply_the_difference`. The reading has `add: [{dish_type: "soup"}]`, and 2 → 3 people: exactly one soup is added.
  - `test_people_turn_where_no_dish_changes` (Review Focus 1). With 3 → 4 people and no rule broken:
    - the state's `constraints["headcount"] == 4`;
    - `plan_request["headcount"] == 4`;
    - a new version exists;
    - the reply's first paragraph is the who-eats sentence.
  - `test_people_turn_cannot_drop_locked_dishes` (Review Focus 4). 5 → 1 people, every dish locked:
    - no dish leaves;
    - `remade` is false;
    - the reply says the count could not be met. Use the existing count-not-met sentence. If the change path has none for a count drop it could not make, add one in `change_text.py` in this task, and list it in the report.
  - `test_people_turn_remakes_when_most_dishes_must_change` (Review Focus 5). A 4-dish plan with one hand-added (locked) dish; the new set's rule breaks 3 of the others:
    - `change["diner_change"]["remade"]` is true;
    - the picker gets 3 replace slots;
    - the locked dish is kept.
  - `test_people_turn_skipped_card_keeps_the_set`. With a confirmed set and a skipped card, `settled_diners` is unchanged, and the reading's other change (a removal) still applies.
  - `test_people_turn_without_a_confirmed_set`. On a solo plan with no card, then "a friend joins": the card has the user preselected and total 2, and the turn adjusts (the planner is not called).
  - `test_people_turn_records_diner_change`. The run's events, in order: the `read_follow_up` model call, the `parse` model call, `diner_change`, `frame`.
  - `test_people_turn_budget_counts_the_parse`. With `PICK_TURN_MODEL_CALLS` patched to 2 and a picker that always needs a re-ask, the turn makes at most 2 + 1 calls (reader, parse, then 1 picker call).
- [ ] **Step 2: Run them to see them fail.**
  Run: `& $py -m pytest tests/test_agent_graph.py -k people_turn -q`
  Expected: FAIL.
- [ ] **Step 3: Implement** the behaviour above.
- [ ] **Step 4: Run the whole backend suite.**
  Run: `& $py -m pytest -q`
  Expected: all pass. Today's tests for "people plans anew" must change to the new behaviour. List each one changed in the report.
- [ ] **Step 5: Docs.**
  - `docs/planner-chat-flow.md` and its Chinese copy: the people path, the follow-up card, adjust or make it again.
  - `docs/planner-what-each-model-sees.md` and its Chinese copy: the picker on a people turn and remake slots.
- [ ] **Step 6: Commit (user).** Message: `feat: a change in who eats adjusts the current plan after the diner card`

---

### Task 7: The reply on a people turn

Spec 5.6.

**Files:**
- Modify: `backend/minemydinner/agent/change_text.py` (renamed in Task 1), `backend/minemydinner/agent/graph.py` (`present_change`).
- Test: `backend/tests/test_change_text.py`, `backend/tests/test_agent_graph.py`.

**Interfaces:**
- Consumes: `change["diner_change"]` from Task 6.
- Produces, in `change_text.py`:
  - `who_eats_sentence(old: int, new: int, moved: dict[str, int], present: dict[str, int], locale: str) -> str`.
    - It is the headcount change, plus what moved when counts moved.
    - zh examples: "现在是 4 个人吃，所以加了一道汤。", "现在是 2 个人吃，所以少了一道菜。", "现在是 4 个人吃。" (nothing moved).
    - en examples: "Now 4 people are eating, so I added a soup." and the like.
    - It is empty when `old == new` and nothing moved, for example when only a rule changed.
  - `remade_sentence(locale: str) -> str`.
    - zh: "新加入的人让大部分菜都要换，所以重新配了一桌，你手动加的菜都保留了。"
    - en: "Most dishes had to change for the new diners, so I made the plan again, keeping the dishes you added yourself."
  - `diners_unchanged_sentence(locale: str) -> str`.
    - zh: "吃饭的人没有变。"
    - en: "Who is eating has not changed."

  The exact wording is the builder's, within these examples.
- Produces, in `present_change`:
  - on a people turn the reply leads with `who_eats_sentence`, `remade_sentence` (when `remade`) and `diners_unchanged_sentence` (when `skipped`), before the usual change sentences;
  - the kept-plan lead (`unchanged_note`) is replaced by the who-eats sentence when that sentence is non-empty;
  - `capped_text` is said only on the changed-plan path, never on the kept path (spec 5.6, carryover change fixes Task 3 (2)).

- [ ] **Step 1: Write the failing tests.**
  - In `tests/test_change_text.py`: one test per sentence and locale, including the empty `who_eats_sentence`.
  - In `tests/test_agent_graph.py`:
    - `test_people_turn_reply_leads_with_who_eats`;
    - `test_remade_reply_says_why`;
    - `test_skipped_follow_up_card_says_diners_unchanged`;
    - `test_capped_sentence_only_when_the_plan_changed`: a kept plan with capped wish targets has no capped sentence.
  - Also `test_people_turn_replaced_sentence_names_no_person`: the replaced-dish sentence contains neither "Diner" nor a saved nickname.
- [ ] **Step 2: Run them to see them fail.**
  Run: `& $py -m pytest tests/test_change_text.py tests/test_agent_graph.py -k "who_eats or remade or diners_unchanged or capped or names_no_person" -q`
  Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the whole backend suite.**
  Run: `& $py -m pytest -q`
  Expected: all pass.
- [ ] **Step 5: Commit (user).** Message: `feat: the reply says who eats now, and why a plan was made again`

---

### Task 8: `follow_up_v5`

Spec 5.7.

**Files:**
- Create: `backend/minemydinner/agent/prompts/follow_up_v5.md`.
- Modify:
  - `backend/minemydinner/agent/follow_up.py` (schema, `request_after`, `reader_messages`);
  - `backend/minemydinner/agent/request.py` (`PROMPT_VERSIONS["follow_up"] = "follow_up_v5"`);
  - `backend/minemydinner/agent/graph.py` (`read_follow_up`'s `Diners:` input);
  - `backend/minemydinner/agent/change_step.py` (if the any-mix code is shared with `relax_split`).
- Test:
  - `backend/tests/test_follow_up.py`;
  - `backend/tests/test_agent_request.py` (a hash test freezing `follow_up_v4.md`; it may already exist);
  - `backend/tests/test_agent_graph.py`.
- Docs:
  - `docs/prompt-versions.md` (`follow_up_v5` and its length);
  - `docs/planner-what-each-model-sees.md` and its Chinese copy (the reader's input and schema).

**Interfaces:**
- Produces:
  - `ReadAdd.dish_type: Literal["meat dish", "vegetable dish", "soup", "staple food", "dessert", "snack"] | None`. These are the keys of `CATEGORY_OF_DISH_TYPE`.
  - `ReadConditions.any_mix: bool = False`: "meat and vegetable dishes in any mix".
  - `ReadConditions.no_longer_must_use: list[str] = []`: must-use names the message takes back.
- Produces: `request_after`.
  - When `any_mix` is set, it clears `meat_dishes`, `vegetable_dishes` and their bounds, keeping the total. The total is the request's `dishes` when it has one, else the plan's present dish total. This is what `relax_split` does.
  - It removes each `no_longer_must_use` name from `must_use` and `all_use`, matching with `fold`.

  `request_after` gains a parameter for the plan's present dish total: `request_after(request: dict, reading: FollowUpReading, present_dishes: int | None = None) -> dict`.
- Produces: `confirmed_set_lines(settled: dict | None, table: LabelTable, headcount: int) -> list[str]`, in `follow_up.py`. The lines are:
  - `- Diner 1 (the user)`;
  - `- Diner N` for each other confirmed diner;
  - `- Diner N, guest for this meal only` for each guest;
  - `- K more people, not named` when `others > 0`;
  - `- Headcount: H`.

  Rules stay in the existing rule lines (`meal_preferences`). `read_follow_up` puts these lines first in the `Diners:` part. No nickname appears: labels only.
- Prompt principles, each a sentence of the prompt, not a boundary patch:
  1. A `people` reading also fills the change fields for the message's other requests.
  2. A fresh start wins: `new_meal` even when the message also changes who eats.
  3. A lasting rule about someone already eating is `people`.
  4. `any_mix` and `no_longer_must_use` are each set only when the message says so.

  Examples are invented and few, never from a held-out set. The size stays under 6,900 characters.

- [ ] **Step 1: Write the failing tests.**
  - In `tests/test_follow_up.py`:
    - `test_add_dish_type_is_an_enum`: `ReadAdd(dish_type="main course")` raises `ValidationError`.
    - `test_request_after_any_mix_keeps_the_total`: a request with `dishes 4`, `meat_dishes 2 exact` and `vegetable_dishes 2 exact`, with `any_mix=True`, gives both sides `None`, both bounds `None`, `dishes 4`, and `counts_stated_now` true.
    - `test_request_after_any_mix_without_a_total_uses_the_plan`: `dishes None`, `present_dishes=3` gives `dishes 3`.
    - `test_request_after_takes_back_must_use`: `must_use ["鸡蛋", "番茄"]`, `all_use ["鸡蛋"]`, `no_longer_must_use ["鸡蛋"]` gives `must_use ["番茄"]` and `all_use []`.
    - `test_confirmed_set_lines`: the user, one friend labelled "Diner 2", one guest "Diner 3", 1 other and headcount 4 give exactly the five lines above. No saved name appears.
  - In `tests/test_agent_request.py`:
    - `test_follow_up_v4_is_frozen` (if it does not exist): the sha256 of `follow_up_v4.md` equals its current value.
    - `test_follow_up_v5_is_within_budget`: `len(follow_up_v5.md) < 6900`.
    - `PROMPT_VERSIONS["follow_up"] == "follow_up_v5"`.
  - In `tests/test_agent_graph.py`, `test_reader_input_lists_the_confirmed_set`: the reader's input has the confirmed-set lines under `Diners:`, and no saved nickname.
- [ ] **Step 2: Run them to see them fail.**
  Run: `& $py -m pytest tests/test_follow_up.py tests/test_agent_request.py tests/test_agent_graph.py -k "enum or any_mix or must_use or confirmed_set or follow_up_v" -q`
  Expected: FAIL.
- [ ] **Step 3: Implement** the schema, `request_after`, `confirmed_set_lines` and the `read_follow_up` input. Write `follow_up_v5.md` from `follow_up_v4.md` with the principles above.
- [ ] **Step 4: Run the whole backend suite.**
  Run: `& $py -m pytest -q`
  Expected: all pass.
- [ ] **Step 5: Docs.** Update `docs/prompt-versions.md` and `docs/planner-what-each-model-sees.md` (and its Chinese copy).
- [ ] **Step 6: Commit (user).** Message: `feat: follow_up_v5 - people carries the message's other changes, a fresh start wins, fixed add types, any mix, taking back a must-use, the confirmed set in the input; follow_up_v4 frozen`

---

### Task 9: The evaluation runner and the development scenarios

Spec 6.

**Files:**
- Modify: `backend/minemydinner/evaluation/planning.py`.
- Create: a new development file in `backend/eval_data/planning/`: `scenarios_diner_changes.yaml`. Register it as a set where the other sets are registered (`diner_changes`).
- Test: `backend/tests/test_planning_eval.py`.

**Interfaces:**
- Produces: a scenario follow-up step may carry `diners_answer`, answered on the follow-up's card. On top of today's keys it takes:
  - `unselect: [names]`;
  - `remove_guests: [names]`;
  - `headcount: int` (sent as `DinerAnswer.headcount`, closing the T1.10 runner gap);
  - `"skip"` (resume with `None`).

  The default (`accept`) sends each guest's `key`, and ticks `proposed` with `save` off, so it confirms the card untouched.
- Produces: four new must-never-be-wrong checks, run on every scenario with a follow-up card:
  1. No dish in the result breaks a rule of the confirmed set (H2 on the final plan, with carried overrides).
  2. A follow-up card answered `accept` loses nobody and no meal-only rule of the previous set (compare `settled_diners` before and after).
  3. A locked dish leaves only for a rule (`breaking` reason in `RULE_BREAKS`) or a reading removal with a quote.
  4. The result meets `frame.count_ranges`, or the reply carries a count-not-met sentence.
- Produces: the run report gains "calls per people turn" next to "calls per change turn".

Development scenarios, one each, invented sentences only, none like a held-out sentence:
- a friend joins with a rule that breaks an unlocked dish;
- the same for a locked dish;
- a guest with no rule joins and the count moves;
- a person leaves (the runner unticks);
- "就我自己吃" after a shared set (the runner unticks, notice shown);
- a total raised on the card;
- a join plus a removal in one message;
- a join that breaks more than half the plan (made again);
- a skipped follow-up card;
- a join after a solo plan with no card;
- a stated total below the ticked people (no untick by code);
- a join plus an add (difference not applied);
- a lasting rule about someone already eating;
- a fresh start with a join (`new_meal`);
- "再叫几个朋友";
- a soup add (enum type);
- lifting a split by message;
- taking back a must-use ingredient.

Each scenario uses two-layer expectations: must-never-be-wrong, plus `expect_any_of` for reasonable readings.

- [ ] **Step 1: Write the failing tests** in `tests/test_planning_eval.py`:
  - `test_follow_up_diners_answer_unselect`, `..._remove_guests`, `..._headcount`, `..._skip`, and `test_default_follow_up_answer_sends_guest_keys`;
  - one test per new check, each with a passing and a failing fake run.
- [ ] **Step 2: Run them to see them fail.**
  Run: `& $py -m pytest tests/test_planning_eval.py -q`
  Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the runner changes and the checks. Write `scenarios_diner_changes.yaml`.
- [ ] **Step 4: Dry check without models.**
  Run: `& $py -m pytest tests/test_planning_eval.py tests/test_eval_cli.py -q`, then the CLI's scenario validation for the new set (the existing `--check` / load path; no model calls).
  Expected: pass; every scenario loads.
- [ ] **Step 5: Ask the user** for approval to change `friend_joins_and_two_more_dishes` in the revise regression set from deferred to expected (spec 6). Show the old and new expectation. Change it only after approval.
- [ ] **Step 6: Commit (user).** Message: `eval: follow-up diner card answers, diner-change checks and development scenarios`

---

### Task 10: Development runs and the evaluation round (paid)

Spec 6.

**Files:**
- Modify:
  - `docs/planning-evaluation-history.md` (a new round);
  - `docs/phase-1-carryover.md` (closed items marked `Done` with a status line, per spec 7; deferred items in the new section);
  - `docs/superpowers/specs/...` only if a decision changed.

- [ ] **Step 1: Ask the user for approval of a development run.** Give a cost estimate for: the development set and `diner_changes`, on the test database.
- [ ] **Step 2: Run and fix.** On approval, run. Fix code or write `follow_up_v6` for misses, never editing a used version. Ask before each further paid run.
- [ ] **Step 3: Ask for the held-out batch.** When the development set meets ≥ 90% and must-never-be-wrong is 0, tell the user how many held-out sentences are needed and which cases they should cover (spec 6). Wait for the user's file. Never open it.
- [ ] **Step 4: Ask the user for approval of the round.** Give a cost estimate for:
  - the development set and `diner_changes`;
  - the user's new held-out batch;
  - the three revise regression sets;
  - every older new-plan and diners set.
- [ ] **Step 5: Run the round on approval.** Check the thresholds:

  | Criterion | Required |
  |---|---|
  | must-never-be-wrong | 0 |
  | development set | ≥ 90% |
  | new held-out batch | ≥ 80% |
  | revise regression sets | heldout3 ≥ 14/15, heldout ≥ 17/17, heldout2 ≥ 19/20; every miss explained |
  | calls per change turn | average ≤ 2.5 |
  | calls per people turn | average ≤ 3.5 |
  | calls in any turn | ≤ 7 |
  | unnecessary questions | ≤ 20% |
  | older new-plan and diners sets | no regression from round 13 |
  | reader prompt | within budget |

- [ ] **Step 6: Record the round.** Append it to `docs/planning-evaluation-history.md`. Update the carryover.
- [ ] **Step 7: Commit (user).** Message: `eval: round 14 - plan 3b-2 (two) diner changes`
