# Phase 1 Plan 2b: Several Diners Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plan one meal for several people (the user, saved friends, temporary guests) with every selected person's rules applied by code after one confirmation card; keep saved nicknames away from model providers; let the user resolve dishes left out for unknown allergens from the reply; let the parse read the assistant's questions (`parse_v4`); measure on a held-out set the user wrote.

**Architecture:** A new `select_diners` interrupt node sits between the existing parse gates and `load_context`; its answer becomes `MealConstraints` (diner ids, guest and meal-only rules, unconfirmed count) kept as a settled set in the conversation state. A per-conversation label table ("Diner N") feeds the `LLMClient` pre/post hooks through a context variable set around every graph node. Unknown-allergen exclusions travel with ingredient names into every planning reply, where a panel extracted from the dictionary page confirms them and a fixed message plans again.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, LangGraph 1.x (backend); Next.js 16, React 19, next-intl, plain CSS (frontend); pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-11-17-phase-1-plan-2b-diners-design.md` (section numbers below refer to it).

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository. Each "User commit" step gives the commands and stops until the user confirms. Suggested branch: `feat/phase-1-plan-2b`, created by the user before Task 1.
- **Packages:** no new Python or npm packages.
- **Python:** `$PY` is `python.defaultInterpreterPath` from `.vscode/settings.json`, read each time; run backend commands from `backend/` with `PYTHONNOUSERSITE=1 PYTHONUTF8=1`. Backend suite: `$PY -m pytest -q`.
- **Secrets:** never read, print, create, edit, or delete `.env`.
- **Language:** every repository file in English; Chinese only in UI translations, dictionary names, and Chinese request text with expected results in tests, scenarios, and prompt examples. Every user-facing string in both `frontend/messages/en.json` and `zh.json`.
- **Frontend checks** through the PowerShell tool only: `cd frontend; npm test; npm run lint; npm run build` (skip the build when port 3000 has a listener, and say so).
- **Databases and models:** tests use `minemydinner_test` only, never call a real model, never assume `llm_calls` or `planning_runs` is empty, and delete only rows they create. Paid evaluation runs only after the user approves a cost estimate.
- **Prompt versions:** `parse_v4.md` is new; `parse_v1-3`, `route_v1-2`, `planner_v1-4` are never edited. `PROMPT_VERSIONS` becomes `{"route": "route_v2", "parse": "parse_v4", "planner": "planner_v4"}`. The planner prompt and `TOOLS_VERSION` must not change (the label form stays "Diner N").
- **Hard constraints stay in code.** The card's answer, not the model, decides whose rules apply; nothing the parse extracts applies before the card is confirmed.
- **No saved nickname in any model-bound text, planning record, or evaluation export; no label ("Diner N") in any text shown to the user.**
- **Held-out sets:** `scenarios_heldout.yaml` (diners), `scenarios_heldout_core.yaml` (features of plans 1 and 2a, single diner, with `library_diners.yaml`), and `library_diners.yaml` are committed in Task 1 and never edited afterwards in this plan. No code or prompt change is made in response to a held-out result within plan 2b. Implementer and reviewer subagents of Tasks 2-11 are told not to open either held-out file; no held-out request, or a close paraphrase of one, may appear in `parse_v4.md` examples, unit tests, or `scenarios_diners.yaml`.
- **Carryover:** after each task, copy every deferred review finding into `docs/phase-1-carryover.md` under a new section "Deferred from Phase 1 plan 2b".
- **Single-person requests behave as today** (no card), except when the user states a new rule about themselves (Section 5.1).

## Review Focus

1. A saved friend's nickname inside a food word in a solo request (friend "鱼", request "想吃鱼香肉丝"): no card, the text reaches the model unchanged — Task 2 test and Task 4 test.
2. The user deselects themselves on the card and only guests eat: the user's rules are not applied, the headcount counts only the people eating — Task 5 test.
3. A friend in the settled set is deleted between two requests of one thread: the next request shows the card again instead of planning without them — Task 5 test.
4. The model writes a label that is not in the table ("Diner 7") or in another case ("diner 2"): the user sees "其他用餐者" / "another diner", never the label — Task 2 test.
5. A thread or plan saved before 2b (no `diner_labels`, no `settled_diners`, `left_out_unknown_allergens` as a list of titles) still loads, plans, and renders — Task 4 test (backend) and Task 9 test (frontend `normalizePlan`).

---

### Task 1: Held-out set and the diners library (user gate)

**Files:**
- Create: `backend/eval_data/planning/library_diners.yaml`, `backend/eval_data/planning/scenarios_heldout.yaml`

**Interfaces:**
- Produces: the scenario keys every later task's runner must read (Task 12 implements them), documented in a comment block at the top of `scenarios_heldout.yaml`:
  - `friends`: list of `{name: str, rules: [{type, allergen?, ingredient?, text?}]}` — saved friends created for the scenario (same rule shape as today's `rules`).
  - `request_style`: `ask_first` | `plan_then_adjust` (default `plan_then_adjust`).
  - `answers`: list of str — replies to ask-first questions, in order.
  - `diners_answer`: absent or `accept` (the card's preselection, every proposed rule ticked for this meal only, unmatched people "none"), or a mapping overriding parts of that default: `selected` (names, "me"), `guests` ([{name, rules, save}]), `others` (int), `others_answer` (none | unsure).
  - `expect_card`: `false`, or the parts to check: `preselected` (exactly these rows selected), `others`, `nudge`, `diet_words` (bool), `guests` ([{name?, allergens?, avoid?: [{name, kind?}], diet_words?}], each listed guest present with at least these values; a missing `kind` accepts either), `self_proposed` ([{allergen? | ingredient?}]).
  - `expect_rules_applied`: list of `{who: name | "me" | "guest", allergen? | ingredient?}` — rules the planning context must include; `expect_rules_not_applied`: the reverse.
  - The exact definitions are the header comment of `scenarios_heldout.yaml`, written in Task 1; Task 11 implements them as written.
- Produces: `library_diners.yaml` = `library_extended.yaml` (unchanged, 23 dishes) plus 16 dishes for plan 2b: potato, tomato, fish, spicy, crustacean, nut, lamb, and coriander dishes, more vegetable dishes, and three desserts that need an oven (39 dishes). The unknown-allergen case is the existing 香煎鸡腿 (unlinked 秘制腌料). The fictional friends: 小王 (peanut allergy), 老李 (no beef, restriction), Lily (crustaceans allergy); the user dislikes 香菜.

- [ ] **Step 1: Send the user the outline** (already sent in the conversation: 14 requests over 10 categories) **and wait for their sentences.** Do not write the scenarios' requests yourself.
- [ ] **Step 2: Write `library_diners.yaml`** from `library_extended.yaml` plus the dishes above; check it loads: `$PY -c "import yaml,sys; yaml.safe_load(open('eval_data/planning/library_diners.yaml',encoding='utf-8'))"`.
- [ ] **Step 3: Write `scenarios_heldout.yaml`**: one scenario per user sentence, the request text exactly as the user wrote it, with inventory, cookware, friends, `diners_answer`, and expectations taken from the design (Sections 5.1-5.7). Header comment: "Held-out set written by the user on <date>. Not used for tuning in plan 2b; do not edit."
- [ ] **Step 4: Write `scenarios_heldout_core.yaml`** (with `library_diners.yaml`) from the user's second batch of sentences: single-diner requests on the features of plans 1 and 2a. For a case the user marked as "cannot be met", set the stock or cookware to the situation they described. Same header rule.
- [ ] **Step 5: Show the user the expectations** of both files (a short table per scenario: request, expected card, expected rules or counts, expected plan property or stop reason) and apply their corrections.
- [ ] **Step 6: User commit**

```powershell
git add backend/eval_data/planning/library_diners.yaml backend/eval_data/planning/scenarios_heldout.yaml backend/eval_data/planning/scenarios_heldout_core.yaml
git commit -m "eval: plan 2b held-out scenarios written by the user, and the diners library"
```

### Task 2: Label table and nickname hooks

**Files:**
- Create: `backend/minemydinner/agent/labels.py`
- Modify: `backend/minemydinner/planning/context.py` (`diner_codes`), `backend/minemydinner/planning/inventory.py` (`meal_preferences`), `backend/minemydinner/api/deps.py`, `backend/minemydinner/evaluation/planning.py` (client construction only)
- Test: `backend/tests/test_agent_labels.py`, `backend/tests/test_planning_inventory.py`

**Interfaces:**
- Produces in `agent/labels.py`:
  - `SELF_LABEL = "Diner 1"`.
  - `@dataclass class LabelTable: labels: dict[str, str]` (diner key → "Diner N"; keys `"diner:<id>"`, `"guest:<n>"`), `names: dict[str, str | None]` (key → name shown to the user; None for the user and unnamed guests). Methods `label_for(key: str) -> str` (assigns the next number when new; the self key always gets "Diner 1"), `to_state() -> dict`, `@classmethod from_state(data: dict | None, self_id: int | None) -> LabelTable`.
  - `@dataclass(frozen=True) class Friend: diner_id: int; nickname: str`.
  - `find_friends(text: str, friends: Sequence[Friend], food_terms: Collection[str]) -> list[tuple[int, int, Friend]]` — every (start, end, friend) occurrence: longest nickname first; Latin nicknames whole-word and case-insensitive; CJK as substrings; an occurrence covered by a longer `food_terms` entry found at an overlapping position is skipped; occurrences do not overlap.
  - `@dataclass class LabelContext: table: LabelTable; friends: list[Friend]; food_terms: frozenset[str]; locale: str; restore: bool = True`.
  - `current_labels: ContextVar[LabelContext | None]` (default None).
  - `replace_nicknames(text: str) -> str` (pre-hook): with no context, returns text unchanged; else replaces each `find_friends` occurrence with `table.label_for(f"diner:{id}")`.
  - `restore_labels(text: str) -> str` (post-hook): with no context or `restore` False, unchanged; else every match of `(?i)\bdiner\s+(\d+)\b` becomes: self → "你" / "you"; friend → nickname; guest → name, or "客人" / "a guest" when unnamed; anything else → "其他用餐者" / "another diner" (by `locale`).
  - `load_label_context(session, table: LabelTable, locale: str, restore: bool = True) -> LabelContext` — friends are all non-self diners; `food_terms` are every ingredient name and alias (built-in, user, and personal aliases, as `all_terms` gives them) and every recipe title, longer than one character.
  - `labels_scope(ctx: LabelContext)` — a context manager that sets and resets `current_labels`.
- Produces: `diner_codes(constraints: MealConstraints, table: LabelTable | None = None) -> dict[str, str]` — with a table, the table's labels (assigning new ones for guests); without, today's numbering (kept for callers outside the graph). `meal_preferences(session, ctx, table: LabelTable | None = None)` — unconfirmed diners continue after the highest label in use.
- Produces: `get_llm_client` and the evaluation's `LLMClient` pass `pre_hooks=(replace_nicknames,)`, `post_hooks=(restore_labels,)`.

- [ ] **Step 1: Write the failing tests** in `test_agent_labels.py`:
  - `test_self_is_always_diner_1`, `test_friend_gets_next_label_once` (same friend twice → same label; second friend → "Diner 3").
  - `test_replace_nicknames_cjk_and_latin`: friends 小王 and "Lily"; "我和小王还有lily" → "我和Diner 2还有Diner 3".
  - `test_longest_nickname_first`: friends 王 and 小王; "小王来" → only 小王 replaced.
  - `test_food_word_is_not_a_nickname`: friend 鱼, food term 鱼香肉丝; "想吃鱼香肉丝" unchanged; "鱼也来，想吃鱼香肉丝" → "Diner 2也来，想吃鱼香肉丝".
  - `test_latin_whole_word_only`: friend "Al"; "Alex and Al" → "Alex and Diner 2".
  - `test_no_context_changes_nothing`: both hooks are identity outside `labels_scope`.
  - `test_restore_labels`: zh table with self, 小王 (Diner 2), guest Alex (Diner 3), unnamed guest (Diner 4): "Diner 1、Diner 2、Diner 3、Diner 4 和 Diner 7" → "你、小王、Alex、客人 和 其他用餐者"; "diner 2" (lower case) → "小王"; en locale gives "you", "a guest", "another diner".
  - `test_restore_disabled_keeps_labels`: `restore=False` leaves "Diner 2".
  - `test_label_table_round_trip`: `from_state(to_state())` equal; `from_state(None, self_id=5)` has only `"diner:5": "Diner 1"`.
  - In `test_planning_inventory.py`: `test_meal_preferences_uses_label_table` (friend selected as Diner 3 by the table → the line says "Diner 3"; one unconfirmed diner → "Diner 4: dietary limits unconfirmed").
- [ ] **Step 2: Run them to see them fail.** `$PY -m pytest tests/test_agent_labels.py tests/test_planning_inventory.py -q`
- [ ] **Step 3: Implement** `agent/labels.py`, the `diner_codes` / `meal_preferences` parameters, and the hook wiring in `api/deps.py` and the evaluation client.
- [ ] **Step 4: Run the full backend suite.** Expected: all pass (the graph does not set a context yet, so behaviour is unchanged).
- [ ] **Step 5: Update the carryover; user commit**

```powershell
git add backend docs/phase-1-carryover.md
git commit -m "feat: per-conversation diner labels and nickname replacement hooks"
```

### Task 3: parse_v4 — stated rules, the assistant's questions, and the trim

**Files:**
- Create: `backend/minemydinner/agent/prompts/parse_v4.md`, `backend/minemydinner/agent/questions.py`
- Modify: `backend/minemydinner/agent/request.py`, `backend/minemydinner/agent/graph.py` (`parse_request` only), `docs/prompt-versions.md`
- Test: `backend/tests/test_agent_request.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- Produces in `request.py`:
  - `class AvoidItem(BaseModel): name: str (1-40 chars); kind: Literal["restriction", "dislike"]`.
  - `class StatedRules(BaseModel): allergens: list[Literal[*ALLERGENS]] = []` (a value outside the list fails validation of that item only — use a `field_validator(mode="before")` that drops unknown values); `avoid: list[AvoidItem] = []` (max 10); `diet_words: list[str] = []` (max 5).
  - `class StatedGuest(StatedRules): name: str | None = None`; `class StatedFriendRules(StatedRules): code: str` (a label such as "Diner 2").
  - `ParsedRequest` gains `guests: list[StatedGuest] = []` (max 10), `friend_rules: list[StatedFriendRules] = []` (max 10), `self_rules: StatedRules | None = None`, each with a description that says when to fill it (Section 5.4: taste preferences go to `query`, never to `avoid`; diet words go to `diet_words`, never to `avoid`).
  - `PROMPT_VERSIONS["parse"] = "parse_v4"`.
- Produces in `questions.py`: `question_text(value: dict, locale: str) -> str | None` — the fixed wording of a recorded question (kinds `question` with `fields` / `problem`), matching the chat's wording in `frontend/messages/*.json`; None for `request_style` and `diners`. `parse_messages(messages: list, questions: list[dict], locale: str) -> list` — the messages with each question inserted as an `AIMessage` at its `at` index (after the first `at` messages), in order.
- Produces in `graph.py`: `parse_request` sends `[SystemMessage(parse prompt), *parse_messages(...)]`. Route input is unchanged.
- Produces: `REPLAN_MESSAGE = {"zh": "按同样的要求重新规划", "en": "Plan again with the same request"}` in `request.py` (Task 9 sends it; `parse_v4` has its example).

- [ ] **Step 1: Write the failing tests:**
  - `test_parsed_request_drops_unknown_allergen_values`: `{"guests": [{"name": "Alex", "allergens": ["peanut", "mango"]}]}` → allergens `["peanut"]`.
  - `test_parsed_request_old_shape_still_loads`: a `parse_v3`-shaped dict validates with empty `guests`, `friend_rules`, and `self_rules` None.
  - `test_question_text_matches_chat_wording`: for `{"kind": "question", "fields": ["mode"]}` in zh, the text equals the zh message string the chat shows for that question (read `frontend/messages/zh.json` in the test); same for `composition` and each `problem`.
  - `test_parse_messages_interleaves_questions`: messages [H"今晚做饭", H"两个"] with a question at 1 → [H, AI(question), H]; `request_style` questions are skipped.
  - `test_parse_request_sends_questions` (graph, fake client): after an ask-first question and the answer "两个", the parse call's messages contain the question text between the two user messages; the route call's do not.
  - `test_prompt_versions_label`: `PROMPT_VERSION == "route_v2+parse_v4+planner_v4"`.
- [ ] **Step 2: Run them to see them fail.** `$PY -m pytest tests/test_agent_request.py tests/test_agent_graph.py -q`
- [ ] **Step 3: Implement** the models, `questions.py`, and the `parse_request` change.
- [ ] **Step 4: Write `parse_v4.md`** from `parse_v3.md` (Section 5.6): add the question-and-answer rule with examples ("做几个菜？" → "两个" is dishes 2; mode question → "都行" is shopping), the `guests` / `friend_rules` / `self_rules` / `diet_words` rules with examples ("我和 Alex，他对花生过敏" → guests [{name Alex, allergens [peanut]}], headcount 2, others_mentioned true; "小王最近不吃羊肉" with 小王 shown as "Diner 2" → friend_rules [{code "Diner 2", avoid [{羊肉, restriction}]}]; "我对芒果过敏" → self_rules avoid [{芒果, restriction}]; "Alex 不太能吃辣" → query "不太能吃辣", no rule; "老李吃素" → diet_words ["吃素"]), saved friends appear as "Diner N" and are never guests, and `REPLAN_MESSAGE` keeps the previous request unchanged. Trim per Section 5.6: keep one example per case, keep "三个菜，不要素菜", drop the English twins and repeated "leave null" / "newest message wins" lines. The file has at most 79 lines.
- [ ] **Step 5: Update `docs/prompt-versions.md`**: a row for `route_v2+parse_v4+planner_v4` naming the new fields, the questions in the input, the replan example, and the trim; mark it current.
- [ ] **Step 6: Run the full backend suite** (including the package-data prompt glob test). Expected: all pass.
- [ ] **Step 7: Update the carryover; user commit**

```powershell
git add backend docs/prompt-versions.md docs/phase-1-carryover.md
git commit -m "feat: parse_v4 - stated rules for guests, friends and the user, the assistant's questions in the parse input, trimmed examples"
```

### Task 4: The diner card — when to ask and what it shows (backend)

**Files:**
- Create: `backend/minemydinner/agent/diners.py`
- Modify: `backend/minemydinner/agent/graph.py`, `backend/minemydinner/agent/state.py`
- Test: `backend/tests/test_agent_diners.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- Consumes: `LabelTable`, `LabelContext`, `labels_scope`, `load_label_context`, `find_friends` (Task 2); `ParsedRequest` stated-rule fields (Task 3).
- Produces in `state.py`: `diner_labels: dict | None` (a `LabelTable.to_state()`), `settled_diners: dict | None` (Task 5 fills it).
- Produces in `graph.py`: every node is registered through `with_labels(node)`, which opens a session, builds the label context from `state["diner_labels"]` and the locale (with `restore=False` for `parse_request`), runs the node inside `labels_scope`, and adds `diner_labels` to the node's update when the table grew. Existing threads with no `diner_labels` start a table with the user as "Diner 1".
- Produces in `diners.py`:
  - `RESTRICTION_WORDS: tuple[str, ...]` = ("过敏", "不吃", "不能吃", "忌口", "吃素", "素食", "allergic", "allergy", "vegetarian", "vegan", "don't eat", "can't eat", "avoid").
  - `DIET_WORDS: tuple[str, ...]` = ("吃素", "素食", "全素", "vegetarian", "vegan").
  - `ProposedRule` (Pydantic): `rule_type: Literal[*RULE_TYPES]`, `allergen`, `ingredient_id`, `ingredient_name: str | None`, `text`, `by_wording: bool`.
  - `stated_to_rules(session, stated: StatedRules) -> list[ProposedRule]` — allergens → allergy rules; each `avoid` → `match_ingredient` hit gives an `ingredient_id` rule ("dietary restriction" or "dislike"), else a text rule with `by_wording=True`; diet words are never turned into rules.
  - `card_needed(parsed, matched_friend_ids: list[int], new_self_rules: list[ProposedRule], settled: dict | None, existing_friend_ids: set[int]) -> bool` — Section 5.1: True when more than one person eats and the settled set does not cover the request (a matched friend or guest or guest rule not in it, a headcount different from its headcount, or one of its friends no longer exists), or when `new_self_rules` is not empty. False for `headcount == 1` with no new self rules (the settled set is then cleared by Task 5).
  - `build_card(session, parsed, labels: LabelTable, matched_friend_ids, current_text: str) -> dict` — the interrupt value: `{"kind": "diners", "headcount": int, "rows": [{"diner_id", "name" (None for the user), "is_self", "selected", "rules": [RuleOut-shaped dicts], "proposed": [ProposedRule], "diet_words": [str]}], "guests": [{"name", "proposed": [...], "diet_words": [...]}], "others": int, "nudge": bool}`. Rows: the user first, then friends sorted by name; preselected: the user and matched friends. Proposed rules already in a profile are left out. `others = max(0, headcount - preselected - guests)`. `nudge` per Section 5.2 (a restriction word in `current_text`, no stated rule at all, no friend matched).
- Produces in `graph.py`: `after_parse` returns `"select_diners"` after the existing gates when `card_needed(...)`; node `select_diners` interrupts with `build_card(...)` (Task 5 handles the answer; in this task the node returns the raw answer under `diners_answer` and goes to `load_context`). The friend matching uses `find_friends` on the current request's user messages.

- [ ] **Step 1: Write the failing tests:**
  - `test_stated_to_rules_matches_dictionary`: avoid 羊肉 (in the test dictionary) → ingredient rule; avoid 某某酱 → text rule `by_wording`; allergens [peanut] → allergy rule; diet words produce no rule.
  - `test_card_not_needed_alone`, `test_card_needed_for_headcount_two`, `test_card_needed_for_guest`, `test_card_needed_for_matched_friend`, `test_card_needed_for_new_self_rule_alone`.
  - `test_card_not_needed_when_settled_covers`: settled {friends [小王], headcount 2}, follow-up "换个辣的" (headcount null, no names) → False.
  - `test_card_needed_when_settled_friend_deleted`: settled friend id no longer in `existing_friend_ids` → True.
  - `test_build_card_rows_and_preselection`: user, 小王 (matched), 老李 → rows in that order, 小王 selected, 老李 not; guest Alex with proposed peanut allergy; headcount 3 → others 0.
  - `test_build_card_nudge`: "我朋友不吃那个" with no stated rules → nudge True; with a parsed guest rule → False.
  - `test_build_card_leaves_out_rules_already_saved`: 小王 already allergic to peanut; stated peanut for 小王 → not proposed.
  - `test_graph_solo_request_has_no_card` (fake client): "想吃鱼香肉丝" with a friend named 鱼 → no interrupt, plans as today, the parse call's text still contains 鱼香肉丝.
  - `test_graph_card_after_style_and_question_gates`: ask-first with mode missing and a guest → the mode question comes first, then the card.
  - `test_graph_old_thread_without_labels_plans`: a checkpoint state without `diner_labels` / `settled_diners` → a solo request plans; `diner_labels` is written with the user as "Diner 1".
  - `test_graph_nickname_never_sent`: friend 小王 named in the request → no recorded model-call event of the run contains "小王"; the parse output's `friend_rules` code stays "Diner 2".
- [ ] **Step 2: Run them to see them fail.** `$PY -m pytest tests/test_agent_diners.py tests/test_agent_graph.py -q`
- [ ] **Step 3: Implement** `diners.py`, `with_labels`, the state fields, `after_parse` and the `select_diners` node.
- [ ] **Step 4: Run the full backend suite.** Expected: all pass.
- [ ] **Step 5: Update the carryover; user commit**

```powershell
git add backend docs/phase-1-carryover.md
git commit -m "feat: diner card - when to ask, what it shows, labels around every graph node"
```

### Task 5: The diner card — applying the answer (backend)

**Files:**
- Modify: `backend/minemydinner/agent/diners.py`, `backend/minemydinner/agent/graph.py`, `backend/minemydinner/agent/request.py` (`apply_defaults`), `backend/minemydinner/api/chat.py` (`ResumeBody`), `docs/design.md` (7.5, 7.6)
- Test: `backend/tests/test_agent_diners.py`, `backend/tests/test_agent_graph.py`, `backend/tests/test_chat_api.py`, `backend/tests/test_agent_request.py`

**Interfaces:**
- Produces in `diners.py`:
  - `class RuleChoice(BaseModel)`: a `RuleIn`-shaped rule (`rule_type`, one of `allergen` / `ingredient_id` / `text`) plus `save: bool = False`.
  - `class GuestAnswer(BaseModel): name: str | None; rules: list[RuleChoice] = []; save: bool = False` (save creates a friend; needs a name).
  - `class DinerAnswer(BaseModel): selected: list[int]` (diner ids, the user included when eating); `stated: dict[int, list[RuleChoice]] = {}` (ticked proposed rules per selected diner id); `guests: list[GuestAnswer] = []`; `others: int = 0` (≥ 0); `others_answer: Literal["none", "unsure"] | None = None`. Validation: at least one person eats in total.
  - `apply_answer(session, answer: DinerAnswer, labels: LabelTable) -> SettledDiners` — writes saved rules (`save` true) to profiles; creates saved guests as friends with their rules (they join `selected`); returns the settled set. Text rules that are diet words are rejected (422-equivalent `ValueError`) — the card never sends them.
  - `@dataclass SettledDiners: diner_ids: list[int]; meal_rules: list[RuleRef]` (meal-only rules of selected diners under `diner:<id>` and guests' rules under `guest:<n>`); `guest_names: dict[str, str | None]`; `others: int`; `others_answer: str | None`; `headcount: int` (selected + guests + others); `to_state()` / `from_state()`.
  - `constraints_from(settled: SettledDiners) -> MealConstraints` — `diner_ids`, `guest_rules = meal_rules`, `unconfirmed_diners = others if others_answer == "unsure" else 0`.
- Produces: `apply_defaults(parsed, self_diner_id=None, headcount: int | None = None)` returns `(spec, constraints, defaults_used, headcount)`; a given headcount wins over the parse and is not "assumed"; callers pass the settled headcount and constraints.
- Produces: `ResumeBody.value: str | DinerAnswer | None`; `select_diners` validates a dict answer as `DinerAnswer`, calls `apply_answer`, stores `settled_diners`, labels guests in the table, records the question with a summary (`{"kind": "diners", "summary": {"names": [...], "others": n}}`), and goes to `load_context`. A `None` or string answer to the card keeps today's "skip" behaviour: plan for the user alone with the parse's headcount and S8 for the rest.
- Produces: `load_context` builds constraints from `settled_diners` when present (headcount from it) and from today's path otherwise; `has_temporary_guests` is true when the meal has guests; `_constraints(state)` reads `guest_rules` back from state. A request with `headcount == 1` and no new self rules clears `settled_diners`.

- [ ] **Step 1: Write the failing tests:**
  - `test_apply_answer_meal_only_rule_not_saved`: 小王 selected, stated peanut ticked without save → `meal_rules` has it under `diner:<id>`, 小王's profile unchanged.
  - `test_apply_answer_save_rule_writes_profile`: same with `save` → profile has the rule, not duplicated in `meal_rules`.
  - `test_apply_answer_saves_guest_as_friend`: guest Alex with peanut and `save` → a new diner "Alex" with the rule, in `diner_ids`.
  - `test_apply_answer_user_not_eating`: `selected` without the user, one guest → `diner_ids` empty, headcount 1, the user's rules not in the context (`load_planning_context` rules empty but the guest's).
  - `test_constraints_unsure_vs_none`: others 2 → unconfirmed 2 with "unsure", 0 with "none"; headcount 4 either way.
  - `test_apply_defaults_headcount_from_card`: parse headcount null, card headcount 3 → default 2 dishes and 1 soup, "headcount" not in assumed.
  - `test_resume_accepts_diner_answer` (API): POST resume with a `DinerAnswer` dict → 200 stream; a malformed dict → 422.
  - `test_graph_card_answer_plans_with_guest_rule` (fake client): "我和 Alex，他对花生过敏" → card → answer accepting Alex with peanut → the planning context has the guest rule, `has_temporary_guests` true, the shortlist excludes the peanut dish.
  - `test_graph_follow_up_reuses_settled`: after that plan, "荤素都行" → no card, same constraints.
  - `test_graph_settled_friend_deleted_asks_again`: settled with 小王; delete 小王; "再来一个" → card.
  - `test_graph_alone_clears_settled`: "就我自己吃" → no card, constraints only the user.
  - `test_graph_reply_never_shows_label` (fake planner note "Diner 2 不吃花生，宫保鸡丁已避开") → the reply text says "小王 不吃花生…".
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the answer models, `apply_answer`, `constraints_from`, the `apply_defaults` change (fixing the carryover item "Headcount is recomputed"), `ResumeBody`, and the graph changes.
- [ ] **Step 4: Update `docs/design.md`**: 7.6 — one card holds selection, unmatched people, and save choices; the confirmed diners carry over in a conversation until a request changes who eats; stated rules default to this meal. 7.5 — an override covers only that diner and rule for rule conflicts; an unknown-allergen confirmation covers the dish for the whole plan, including diners who join later (Section 5.8).
- [ ] **Step 5: Run the full backend suite.** Expected: all pass.
- [ ] **Step 6: Update the carryover** (close "Headcount is recomputed", "Other diners' restrictions in the request text are not captured", "Unknown-allergen override ignores the diner" with their outcomes); **user commit**

```powershell
git add backend docs/design.md docs/phase-1-carryover.md
git commit -m "feat: diner card answer - meal constraints, saved rules and friends, settled diners per conversation"
```

### Task 6: Unknown allergens in every planning reply (backend)

**Files:**
- Modify: `backend/minemydinner/planning/search.py`, `backend/minemydinner/planning/shortlist.py`, `backend/minemydinner/planning/feasibility.py`, `backend/minemydinner/planning/tools.py`, `backend/minemydinner/agent/graph.py`
- Test: `backend/tests/test_planning_search.py`, `backend/tests/test_planning_feasibility.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- Produces: `unknown_allergen_exclusions(session, ctx, recipe_ids: list[int] | None = None) -> list[ExcludedRecipe]` in `search.py` (all recipes when None); empty when no diner of the meal has an allergy. Search and the shortlist use it.
- Produces: `ShortlistResult.left_out_unknown_allergens: list[ExcludedRecipe]`; the `search_recipes` tool result keeps sending titles only to the model (unchanged schema, `TOOLS_VERSION` unchanged).
- Produces: `check_feasibility(...)` unchanged signature; `feasibility_step` also merges `unknown_allergen_exclusions(session, ctx)` into `search_notes`.
- Produces: `current_plan["left_out_unknown_allergens"]: list[{"id": int, "title": str, "lines": list[str]}]`, one entry per recipe, on every planning reply (plan, closest attempt, early stop, no valid plan). `current_plan["diners"]: list[{"name": str | None, "is_self": bool, "guest": bool}]` and `"others": int` for the plan card's diners line.

- [ ] **Step 1: Write the failing tests:**
  - `test_unknown_allergen_exclusions_lines`: a recipe with an unlinked line 某某酱 and a diner with an allergy → `[ExcludedRecipe(id, title, ["某某酱"])]`; no allergy in the meal → `[]`.
  - `test_feasibility_stop_carries_left_out` (graph, fake client): fridge-only request that stops early, with an unknown-allergen dish and an allergic diner → the reply's plan has `reasons` and `left_out_unknown_allergens` with lines.
  - `test_no_valid_plan_carries_left_out`: the same on the H2 no-plan path.
  - `test_plan_diners_line`: after a card with 小王 and guest Alex → `diners` lists the user, 小王, Alex (guest), `others` 0.
  - `test_search_tool_result_unchanged_for_model`: the tool's JSON still has `left_out_unknown_allergens` as titles.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full backend suite.** Expected: all pass.
- [ ] **Step 5: Update the carryover; user commit**

```powershell
git add backend docs/phase-1-carryover.md
git commit -m "feat: unknown-allergen exclusions with ingredient names on every planning reply, and the diners line"
```

### Task 7: Diner API — diet words and food-like nicknames (backend)

**Files:**
- Modify: `backend/minemydinner/api/diners.py`
- Test: `backend/tests/test_diners_api.py`

**Interfaces:**
- Consumes: `DIET_WORDS` (Task 4), the food-term loader of `load_label_context` (Task 2; factor it out as `food_terms(session) -> frozenset[str]` in `agent/labels.py` if not already).
- Produces: `POST /api/diners/{id}/rules` with a text rule whose folded text contains a `DIET_WORDS` entry → 422 with code `diet_word`.
- Produces: `DinerOut.nickname_warning: bool` — true for a non-self diner whose name is one CJK character, or equals or appears inside a food term (Section 5.5). Returned by list, create, and rename.

- [ ] **Step 1: Write the failing tests:** `test_diet_word_text_rule_refused` ("吃素", "Vegetarian" → 422 `diet_word`; "香菜" text → 201); `test_nickname_warning_single_cjk` ("鱼" → true); `test_nickname_warning_food_term` ("鱼香" inside 鱼香肉丝 → true); `test_nickname_warning_normal` ("小王" → false; the user's own row → false).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full backend suite.** Expected: all pass.
- [ ] **Step 5: Update the carryover; user commit**

```powershell
git add backend docs/phase-1-carryover.md
git commit -m "feat: refuse diet words as text rules, warn about food-like nicknames"
```

### Task 8: The diner card in the chat (frontend)

**Files:**
- Create: `frontend/src/components/DinerCard.tsx`, `frontend/src/components/DinerCard.test.tsx`
- Modify: `frontend/src/lib/chat.ts` (types, `resume` accepts an object), `frontend/src/components/ChatPage.tsx`, `frontend/src/components/MessageList.tsx` (history summary), `frontend/messages/en.json`, `frontend/messages/zh.json`, `frontend/src/app/globals.css`

**Interfaces:**
- Consumes: the interrupt value of Task 4 and `DinerAnswer` of Task 5; the existing diner and rule API in `lib/api.ts` (rule add and delete on the user and friends edit their profiles at once, as the people page does).
- Produces: `Pending` gains `{ kind: "diners"; headcount: number; rows: DinerRow[]; guests: GuestRow[]; others: number; nudge: boolean }` and the history item `{ kind: "diners"; summary: { names: string[]; others: number } }`; `resume(threadId, value: string | DinerAnswer | null, locale)`.
- Produces: `DinerCard({ pending, busy, onConfirm(answer: DinerAnswer) })`.

The card follows the mock in Section 5.2: selectable rows with rules; proposed rules as ticked checkboxes with "save to profile"; guest rows (rename, remove, add, rules, "save to friend profiles" with the consent reminder); the "还有 N 位没对上" line with "没有忌口 / 有，我来填 / 不确定" ("有，我来填" adds N empty guest rows); diet words shown as the fixed hint, never as a rule; rules matched by wording marked "按文字匹配"; the nudge notice; "确认，开始规划".

- [ ] **Step 1: Write the failing tests** in `DinerCard.test.tsx`: preselection rendered as checked; unticking a proposed rule removes it from the answer; "有，我来填" adds two guest rows for others 2 and the answer has `others` 0; "不确定" gives `others_answer: "unsure"`; a diet word shows the hint and adds no rule; save on a guest without a name is disabled; the nudge notice appears only when `nudge`; confirm sends the expected `DinerAnswer`. In `ChatPage.test.tsx`: a `diners` interrupt renders the card instead of the composer answer path, and confirming calls `resume` with the object.
- [ ] **Step 2: Run them to see them fail.** PowerShell: `cd frontend; npx vitest run src/components/DinerCard.test.tsx src/components/ChatPage.test.tsx`
- [ ] **Step 3: Implement** the component, types, `resume` change, history summary, strings (zh and en), styles.
- [ ] **Step 4: Run the frontend checks.** `npm test; npm run lint; npm run build`. Expected: all pass.
- [ ] **Step 5: Update the carryover; user commit**

```powershell
git add frontend docs/phase-1-carryover.md
git commit -m "feat: diner card in the chat"
```

### Task 9: Plan card — diners line, left-out dishes, confirm panel, plan again (frontend)

**Files:**
- Create: `frontend/src/components/AllergenResolvePanels.tsx` (the `ConfirmPanel` and `LinkPanel` moved out of `DictionaryManager.tsx`, unchanged in behaviour)
- Modify: `frontend/src/components/DictionaryManager.tsx`, `frontend/src/components/PlanCard.tsx`, `frontend/src/components/PlanCard.test.tsx`, `frontend/src/lib/chat.ts` (`ChatPlan`, `normalizePlan`), `frontend/src/components/ChatPage.tsx`, messages, CSS

**Interfaces:**
- Consumes: `left_out_unknown_allergens` and `diners` / `others` of Task 6; `REPLAN_MESSAGE` wording (Task 3) — the zh and en strings are copied exactly into the messages files.
- Produces: `ChatPlan.left_out_unknown_allergens: { id: number; title: string; lines: string[] }[]`; `normalizePlan` turns an old list of titles into entries with `id` 0 and `lines` [] (shown as titles only, no confirm button). `ChatPlan.diners`, `ChatPlan.others` optional (absent in old plans → no diners line).
- Produces: `PlanCard` prop `onReplan(): void`. The card groups left-out dishes by line name: "宫保鸡丁、麻辣香锅：花椒油（过敏原未知）" with "确认过敏原" per name. A name opens `LinkPanel` (unlinked line) or `ConfirmPanel` (entry with unknown allergens) — the backend's existing match endpoint (`POST /api/ingredients/match`) tells which. After at least one success the card shows "重新规划", which calls `onReplan`; `ChatPage` sends the `REPLAN_MESSAGE` for the locale. This works on plan, closest-attempt, early-stop, and no-plan replies.

- [ ] **Step 1: Write the failing tests** in `PlanCard.test.tsx`: grouped left-out lines render with the dish titles; confirming a name shows "重新规划" and clicking it calls `onReplan`; an old plan (titles only) renders titles without a confirm button; the diners line reads "按 我、小王、Alex 排除" and is absent for a solo plan without `diners`; an early-stop plan with left-out dishes shows the list. Existing dictionary tests still pass after the move.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the frontend checks.** Expected: all pass.
- [ ] **Step 5: Update the carryover; user commit**

```powershell
git add frontend docs/phase-1-carryover.md
git commit -m "feat: plan card - diners line, left-out dishes with their ingredients, confirm allergens and plan again"
```

### Task 10: People page warnings, and the user's browser check (frontend)

**Files:**
- Modify: `frontend/src/components/PeopleManager.tsx`, `frontend/src/lib/api.ts` (`DinerOut.nickname_warning`, `diet_word` error code text), messages, CSS
- Test: `frontend/src/components/PeopleManager.test.tsx` (create if absent)

**Interfaces:**
- Consumes: Task 7's `nickname_warning` and `diet_word` error.

- [ ] **Step 1: Write the failing tests:** a friend with `nickname_warning` shows "这个昵称也出现在食材名里，规划时可能被误认；建议换一个更长的昵称"; adding a text rule "吃素" shows "暂不支持按类别忌口，请把不吃的食材（如猪肉、牛肉、鸡肉、鱼、虾）逐个加上".
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the frontend checks.** Expected: all pass.
- [ ] **Step 5: Browser check with the user** on the test database (`DATABASE_URL` overridden to the test URL; warn before restarting a server that has unsaved review drafts): a solo request (no card); "我和小王" (card, preselection, rules); a guest's allergy in the request; "有，我来填"; plan card diners line; a left-out dish confirmed and planned again; the people page warnings. Planning here makes paid model calls (a few cents); say so before starting. Fix what the user finds within this task.
- [ ] **Step 6: Update the carryover; user commit**

```powershell
git add frontend docs/phase-1-carryover.md
git commit -m "feat: people page warnings for diet words and food-like nicknames"
```

### Task 11: Evaluation runner for diners, and the development set

**Files:**
- Modify: `backend/minemydinner/evaluation/planning.py`, `docs/local-development.md` (Planning evaluation)
- Create: `backend/eval_data/planning/scenarios_diners.yaml`
- Test: `backend/tests/test_planning_eval.py`

**Interfaces:**
- Consumes: the scenario keys defined in Task 1; `DinerAnswer` (Task 5).
- Produces: `Scenario` fields `friends`, `request_style`, `answers`, `diners_answer`, `expect_card`, `expect_rules_applied`, `expect_rules_not_applied`, and `between_turns: list[{confirm_allergens: {name, allergens: []}}]` (a database action the runner does before `follow_up`, for the plan-again case). `reset_scenario_state` deletes and recreates non-self diners. `run_scenario` answers questions from `answers`, the card from `diners_answer` (`accept` builds the answer from the card's preselection and proposals), and records `card_shown` and the card value.
- Produces: two checks on every scenario, counted as mismatches: `nickname_sent` (any friend nickname inside any model-bound message of every planning run of the thread, route and parse events included) and `label_shown` (`(?i)\bdiner\s+\d+` in the reply text).
- Produces: `scenarios_diners.yaml` (development set, `library_diners.yaml`) covering Section 5.9's list.

- [ ] **Step 1: Write the failing tests** (fake model): `test_runner_answers_card_accept`; `test_runner_answers_ask_first_questions`; `test_runner_flags_nickname_sent` (a record event containing a friend's nickname → mismatch); `test_runner_flags_label_shown`; `test_runner_expect_card_false_mismatch` (a card shown where none was expected); `test_reset_recreates_friends` (only scenario friends exist afterwards; the user row is kept); `test_heldout_file_loads` (both new scenario files parse into `Scenario`).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement**, and write `scenarios_diners.yaml`.
- [ ] **Step 4: Document** the new keys, the two checks, and the two new files in `docs/local-development.md`, including the rule that `scenarios_heldout.yaml` is not tuned against.
- [ ] **Step 5: Run the full backend suite.** Expected: all pass.
- [ ] **Step 6: Update the carryover; user commit**

```powershell
git add backend docs/local-development.md docs/phase-1-carryover.md
git commit -m "eval: diners in the planning evaluation, nickname and label checks, development set"
```

### Task 12: Paid evaluation rounds (user gate)

**Files:**
- Modify: `docs/planning-evaluation-history.md`, `docs/phase-1-carryover.md`, `docs/prompt-versions.md` (first use of `parse_v4`)
- Create: run folders under `backend/eval_data/planning/runs/` (committed parts only)

- [ ] **Step 1: Estimate and ask.** Give the user the estimate per run from round 7's cache-aware costs (baseline about $0.004, plan 2a about $0.008, counts about $0.006; the diner sets about $0.01 each, more calls per scenario because of two-turn cases) and the order: development set, then regression (baseline, plan 2a, counts), then held-out. Wait for approval.
- [ ] **Step 2: Development round** on a clean, committed tree. If a scenario fails, investigate, propose a fix to the user, and re-run only with their approval (each re-run estimated). Tuning happens only here.
- [ ] **Step 3: Regression runs** of the three existing sets. A scenario that is worse than round 7 because of the trim gets its `parse_v4` example back — which is a new `parse_v5`, since `parse_v4` is now used; ask the user first.
- [ ] **Step 4: Held-out runs, once each** (`scenarios_heldout.yaml` with `library_diners.yaml`, `scenarios_heldout_core.yaml` with `library_diners.yaml`), after the development set and code are final. Record the result as is; no change in response within plan 2b.
- [ ] **Step 5: Write round 8** (and later rounds) in `docs/planning-evaluation-history.md`: what changed (2b, `parse_v4`), the runs, a new comparison table for the diners set and one for the held-out set, the regression rows. Add held-out findings and anything deferred to the carryover.
- [ ] **Step 6: User commit**

```powershell
git add backend/eval_data/planning/runs docs
git commit -m "docs: planning evaluation round 8 - plan 2b development, regression, and held-out results"
```
