# Chat Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the chat page work like a familiar chat agent (home page is the chat, per-reply plan cards, questions kept in the history, bottom composer with a request-style switch), and make the planner's note reliable with `planner_v4`, measured across five models.

**Architecture:** The backend keeps each plan and each question as display records in the graph state, outside the messages the models read, and `GET /api/chat/threads/{id}` returns one ordered list of items. The frontend splits `ChatPage` into a message list, reply items with plan cards, a composer, a switch, and a welcome state, using the existing plain CSS. The evaluation gains note checks, a planning-model override, and a notes review export.

**Tech Stack:** FastAPI, LangGraph, SQLAlchemy (backend); Next.js 16, React 19, next-intl, plain CSS (frontend); vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-11-15-chat-page-redesign-design.md`

## Global Constraints

- **Git:** the user performs every Git operation that changes the repository. Each "User commit" step gives the commands and stops until the user confirms. Read-only Git may run directly.
- **Packages:** no new Python or npm packages.
- **Python:** `$PY` is `python.defaultInterpreterPath` from `.vscode/settings.json`, read each time; run backend commands from `backend/` with `PYTHONNOUSERSITE=1 PYTHONUTF8=1`.
- **Secrets:** never read, print, create, edit, or delete `.env`; never print database URLs.
- **Language:** every repository file in English; Chinese only in UI translations, and in recipe text with expected results in tests and prompt examples. Every user-facing string goes into both `frontend/messages/en.json` and `frontend/messages/zh.json`.
- **Databases and models:** tests use `minemydinner_test` only and never call a real model; paid evaluation runs only after the user approves each batch with its expected cost.
- **Frontend checks** run through the PowerShell tool: `cd frontend; npm test; npm run lint; npm run build` (skip the build when port 3000 has a listener, and say so).
- **Nothing for display reaches a model:** plans and questions stored per reply are never added to the messages that route, parse, or the planner read.
- **Prompt caching:** history is never edited; the planner's opening is built once per run and reused on retries.
- **Prompt versions:** `planner_v3.md` stays unchanged; `planner_v4.md` is new and `PROMPT_VERSIONS["planner"] = "planner_v4"` (`docs/prompt-versions.md`). The `PlanProposal` schema is unchanged.
- **Old data:** threads saved before this plan (only `current_plan`, no per-reply records) must still open.
- **Evaluation history:** every paid evaluation adds a round to `docs/planning-evaluation-history.md`.

## Review Focus

1. A thread saved before this change (only `current_plan`) opens with its plan as a card-only item at the end, actionable — Task 1 and Task 2 tests.
2. Enter while an input method is composing (Chinese IME) must not send — Task 3 test.
3. Reloading while a question waits, and after it was answered, shows the question in its place — Task 1 and Task 2 tests.
4. A plan with an empty note still appears, as a card-only reply — Task 1 and Task 2 tests.
5. The newest plan stays actionable after a later reply without a plan, but its buttons are disabled while a run is working or a question waits; older cards never act — Task 2 tests.

---

### Task 1: Per-reply plans and question records in the backend

**Files:**
- Modify: `backend/minemydinner/agent/state.py`, `backend/minemydinner/agent/graph.py` (`present_plan`, `ask_style`, `ask_question`), `backend/minemydinner/api/chat.py` (`_visible_messages`, `read_thread`)
- Test: `backend/tests/test_agent_graph.py`, `backend/tests/test_chat_api.py`

**Interfaces:**
- Produces in `ChatState`: `reply_plans: dict[str, dict]` (assistant message id → plan) and `questions: list[dict]`, each `{"at": int, "value": dict}` where `at` is the number of messages in the conversation when the question was asked and `value` is the interrupt value (`{"kind": "request_style"}` or `{"kind": "question", "fields": [...], "problem"?: str}`).
- `present_plan` creates its reply as `AIMessage(note, id=<new uuid4 string>)` and adds `{id: plan}` to `reply_plans`; `current_plan` and `plan_versions` stay.
- `ask_style` and `ask_question` append their question to `questions` in the update they return after the resume (`at = len(state["messages"])` before the answer is added).
- `GET /api/chat/threads/{id}` returns `{"items": [...], "messages": [...], "current_plan": ..., "pending": ...}`. `items` is the conversation in order: `{"type": "user", "text"}`, `{"type": "assistant", "id", "text", "plan": dict | None}` (an assistant message with a plan is included even when its text is empty), and `{"type": "question", "value"}` placed after the first `at` messages. `messages` stays as today for old clients and tests.

- [ ] **Step 1: Write the failing tests** — `test_plan_is_stored_with_its_reply` (two planning turns in one thread → `items` has two assistant items, each with its own plan, in order); `test_empty_note_plan_is_a_card_only_item` (a fake planner returning note "" → an assistant item with text "" and a plan); `test_questions_stay_in_the_history` (ask-first question answered, then read → a question item between the user's request and the answer); `test_pending_question_is_not_an_item_yet` (while pending, `pending` is set and no question item exists); `test_old_thread_without_reply_plans_reads` (state with `current_plan` and no `reply_plans` → `items` has no plan on any assistant item, `current_plan` returned as before); `test_display_records_never_reach_models` (after a plan and a question, the next turn's route, parse, and planner inputs contain no plan JSON or question value).
- [ ] **Step 2: Run them to see them fail** — `$PY -m pytest tests/test_agent_graph.py tests/test_chat_api.py -q -k "reply or question or old_thread or display"`.
- [ ] **Step 3: Implement** the state fields, the message ids and `reply_plans` in `present_plan`, the question records, and `items` in `read_thread` (build from `messages`, `reply_plans`, `questions`; read missing keys with `.get` and empty defaults).
- [ ] **Step 4: Run the full backend suite** — `$PY -m pytest -q`; expected: all pass.
- [ ] **Step 5: User commit**

```powershell
git add backend/minemydinner/agent/state.py backend/minemydinner/agent/graph.py backend/minemydinner/api/chat.py backend/tests
git commit -m "feat: keep each plan with its reply and the assistant's questions for display"
```

### Task 2: Thread items in the frontend data layer and the message list

**Files:**
- Modify: `frontend/src/lib/chat.ts`, `frontend/src/components/ChatPage.tsx`, `frontend/src/components/PlanCard.tsx`, `frontend/src/test/chatFixtures.ts`
- Create: `frontend/src/components/MessageList.tsx` (items, streaming text, progress, error, pending question)
- Test: `frontend/src/lib/chat.test.ts`, `frontend/src/components/ChatPage.test.tsx`, `frontend/src/components/PlanCard.test.tsx`

**Interfaces:**
- Consumes: Task 1's `items`.
- Produces in `chat.ts`: `type ThreadItem = { type: "user"; text: string } | { type: "assistant"; id: string | null; text: string; plan: ChatPlan | null } | { type: "question"; value: Pending }`; `loadThread` returns `items` (each plan through `normalizePlan`); when `items` is absent or no item has a plan but `current_plan` is set, a card-only assistant item with `current_plan` is appended (old threads).
- `PlanCard` gains `actionable: boolean`: false disables every action button and link action.
- Streaming: a `message` event appends an assistant item; a `plan` event attaches to the assistant item appended in the same run, or appends a card-only item when none was.
- The newest item with a plan is the only actionable one; it is disabled while `busy` or while `pending` is set.
- Questions: a pending question renders as an assistant item at the end (the request-style choice with its two buttons, or the hint text that sat above the input); after the user answers in the same session it stays in place.
- Progress shows as one transient status line (`role="status"`) in the assistant position; errors show as an assistant-position notice and clear on the next send; the log stays `aria-live="polite"` for final messages only.

- [ ] **Step 1: Write the failing tests** — two plans in a loaded thread render two cards, only the last actionable; a later reply without a plan keeps the newest card actionable; the newest card is disabled while busy and while a question is pending; a streamed plan with no message renders a card-only reply; an old thread (only `current_plan`) shows the card at the end, actionable; a pending ask-first question renders as an assistant item and stays after answering; `PlanCard` with `actionable={false}` has every action disabled.
- [ ] **Step 2: Run them to see them fail** (PowerShell: `cd frontend; npm test`).
- [ ] **Step 3: Implement** the types, `loadThread`, the item reducer in `ChatPage`, `MessageList`, and `PlanCard`'s `actionable`.
- [ ] **Step 4: Run frontend tests and lint** — all pass, lint clean.
- [ ] **Step 5: User commit**

```powershell
git add frontend/src
git commit -m "feat: chat shows each reply with its own plan card and keeps questions in the conversation"
```

### Task 3: Layout, composer, request-style switch, welcome state, and home page

**Files:**
- Create: `frontend/src/components/Composer.tsx`, `frontend/src/components/RequestStyleSwitch.tsx`, `frontend/src/components/ChatWelcome.tsx`
- Modify: `frontend/src/components/ChatPage.tsx` (remove the top dropdown and top "new conversation"), `frontend/src/app/[locale]/page.tsx` (renders the chat), `frontend/src/app/[locale]/chat/page.tsx` (redirects to `/[locale]`), `frontend/src/components/MainNav.tsx` (drop the separate chat link), `frontend/src/app/globals.css`, `frontend/messages/en.json`, `frontend/messages/zh.json`
- Test: `frontend/src/components/ChatPage.test.tsx` and new `Composer.test.tsx`, `RequestStyleSwitch.test.tsx`

**Interfaces:**
- `Composer({ busy, onSend, value, onChange })`: multi-line `textarea` growing to a few lines; Enter sends; Shift+Enter inserts a line break; Enter while `event.nativeEvent.isComposing` (or keyCode 229) does nothing; send button disabled when empty or busy.
- `RequestStyleSwitch({ value, onChange })`: `role="switch"`, `aria-checked` true for `"ask_first"`; label "信息不全时先问我" / "Ask me first when details are missing"; the text next to it shows the current meaning; `null` (no saved choice) shows off. It saves through the existing `putRequestStyle` at once.
- `ChatWelcome({ onPick })`: shows `app.name` and `app.tagline` and three example buttons ("两菜一汤，只用冰箱里的", "三个人吃，可以买菜", "做个快手菜，20 分钟内", with English versions); a button fills the composer and does not send.
- Layout: messages scroll; the composer area is fixed at the bottom with the switch, "new conversation", and the provider notice; user messages are right-aligned bubbles, assistant items left; bubbles at most about 80% wide on narrow screens; the view scrolls to the newest item on send and while streaming unless the user is more than about 80 px above the bottom.
- Opening the site continues the stored conversation; "new conversation" starts fresh and shows the welcome state.

- [ ] **Step 1: Write the failing tests** — Enter sends; Shift+Enter does not send; Enter during IME composition does not send; the switch reads `ask_first` as on and `null` as off, and toggling calls `putRequestStyle` with the other value; an empty conversation shows the welcome title and examples, and an example fills the input without sending; the chat route redirects to the home route; the nav has no separate chat link.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the components, the routes, the nav change, the CSS, and the messages.
- [ ] **Step 4: Run frontend tests, lint, and build** (build only when port 3000 is free).
- [ ] **Step 5: User check** — the user opens `http://localhost:3000/zh` on the real app (backend restarted after Task 1) and tries a plan, a question, a new conversation, the switch, and a narrow window; findings are fixed in this task or recorded in `docs/phase-1-carryover.md`.
- [ ] **Step 6: User commit**

```powershell
git add frontend
git commit -m "feat: chat page layout with bottom composer, request-style switch, welcome state, and the chat as home page"
```

### Task 3b: Read-only recipe detail in the plan card and the library (design 5.6)

**Files:**
- Create: `frontend/src/components/RecipeDetail.tsx`, `frontend/src/app/[locale]/library/[id]/page.tsx`
- Modify: `frontend/src/lib/api.ts` (`getRecipe`), `frontend/src/components/PlanCard.tsx`, `frontend/src/components/RecipeLibrary.tsx`, `frontend/src/app/globals.css`, `frontend/messages/en.json`, `frontend/messages/zh.json`
- Test: new `RecipeDetail.test.tsx`, `PlanCard.test.tsx`, the library tests, a test for the detail route

**Interfaces:**
- `api.getRecipe(id: number): Promise<SavedRecipe>` calling `GET /api/recipes/{id}`.
- `RecipeDetail({ recipe, compact, missing }: { recipe: SavedRecipe; compact?: boolean; missing?: string[] })`: ingredients (amount and unit label as in the review form's labels; vague → "适量"/"to taste"; missing → no amount; household measure → the number and "家用量具"; choice groups as one "任选其一" line; optional lines marked), cookware, time with its source, servings, steps summary, source link (`source_url`, opens in a new tab), and the stored source text behind a collapsed toggle; names in `missing` are marked. `compact` hides servings and shows the source text toggle only.
- `PlanCard`: each dish has a toggle button "查看做法" / "How to cook" with `aria-expanded`; first open calls `getRecipe` and shows a loading line, then `RecipeDetail compact` with the dish's names from `plan.missing[id]`; an error shows a short notice with retry. It works whatever `actionable` is.
- Library: each recipe row gets a "查看" / "View" link to `/[locale]/library/[id]`; that page loads the recipe, shows `RecipeDetail` (not compact), a back link to the library, and an edit link (to the library with that recipe in the edit form, or the existing edit flow).

- [ ] **Step 1: Write the failing tests** — `RecipeDetail` renders amounts ("300 克", "适量", household "1 家用量具"), a choice group as one "任选其一" line, marks a missing ingredient, shows the steps summary and a source link, and keeps the source text collapsed until toggled; the plan card toggle fetches once, shows the detail, collapses, and works on a non-actionable card; a failed fetch shows the notice and retries; the library row links to the detail route; the detail route renders the recipe and a 404 shows a not-found message.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run frontend tests, lint, and build** (build only when port 3000 is free).
- [ ] **Step 5: User check** in the browser (plan card toggle on a few dishes; library view page).
- [ ] **Step 6: User commit**

```powershell
git add frontend
git commit -m "feat: read-only recipe detail in the plan card and the recipe library"
```

### Task 4: `planner_v4` note rules, note checks, model override, and notes review export

**Files:**
- Create: `backend/minemydinner/agent/prompts/planner_v4.md` (copy of `planner_v3.md` with the note rules of design 5.5), `backend/minemydinner/evaluation/notes_review.py`
- Modify: `backend/minemydinner/agent/request.py` (`PROMPT_VERSIONS`), `backend/minemydinner/evaluation/planning.py`, `backend/minemydinner/llm/config.py` (if needed for the override), `.gitignore`, `docs/prompt-versions.md`, `docs/local-development.md`
- Test: `backend/tests/test_planning_eval.py`, `backend/tests/test_agent_request.py`

**Interfaces:**
- `planner_v4.md` note rule, replacing the note paragraph: one sentence with one practical tip (which dish to start first, which takes longest, how the dishes go together); do not count or list the plan's dishes, the card shows them; name no dish outside the plan and suggest no swaps; write in the reply locale; no allergy safety claims.
- `note_flags(note: str, plan_titles: list[str], library_titles: list[str], locale: str) -> list[str]` in `evaluation/planning.py`, returning any of `"outside_dish"` (a library title not in the plan appears), `"counts_dishes"` (a number — digits or 一二两三四五六七八九十几 — directly before or after 道 / 个菜 / 荤 / 素 / dish / dishes / meat / vegetable, as in "3 道荤菜", "两荤一素", "荤 4 道", "4 dishes"), `"multi_sentence"` (more than one sentence ending in 。！？.!?, ignoring a trailing one), `"wrong_locale"` (the existing locale rule). The note checked is the shown plan's own model note, without the fixed closest-attempt text.
- `summary.json`: `conditions.planning_model` (catalog name, or the configured default); per metrics group, `notes: {"checked": n, "flags": {flag: count}, "flagged_rate": float}`; per scenario, the flag list. `results.jsonl` keeps the note text.
- CLI: `--planning-model <catalog name>` runs the whole `planning_eval` task on that catalog model with no fallback (an unknown name exits with an error listing the tool-calling catalog models); the run id gains the model name (`<UTC>-<set>-<shortlist|no-shortlist>-<vectors|no-vectors>-<model>` when the flag is given).
- `python -m minemydinner.evaluation.notes_review --runs <run folder>... --out <file.csv>` writes one row per plan scenario per run: `model, run_id, scenario, plan_titles, note, flags, quality, should_not_appear, comment` (the last three empty for the user). The CSV holds model text, so `.gitignore` ignores `backend/eval_data/planning/runs/**/notes_review*.csv` and the default output goes under the runs folder.

- [ ] **Step 1: Write the failing tests** — `note_flags` cases: "糖醋小排最费时，可以先腌上。" with that dish in the plan → `[]`; "可以把糖醋小排换成胡萝卜炒鸡蛋" with 胡萝卜炒鸡蛋 in the library but not the plan → `["outside_dish"]`; "这一餐安排了 3 道荤菜" → `["counts_dishes"]`; "两荤一素，很均衡" → `["counts_dishes"]`; "Start the ribs first. Then the eggs." → `["multi_sentence"]`; a Chinese note for `en` → `["wrong_locale"]`. `PROMPT_VERSIONS["planner"] == "planner_v4"` and `planner_v3.md` is byte-identical to the committed file. The CLI rejects an unknown `--planning-model`; a run with the flag records `conditions.planning_model` and the model in the run id. `notes_review` writes the header and one row per plan scenario from a fixture run folder.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** the prompt file, the version switch, `note_flags` and its summary metrics, the override, the export, the ignore rule, and the docs (`prompt-versions.md` gets the `route_v2+parse_v3+planner_v4` row; `local-development.md` documents `--planning-model`, the note metrics, and `notes_review`).
- [ ] **Step 4: Run the full backend suite.**
- [ ] **Step 5: User commit**

```powershell
git add backend .gitignore docs/prompt-versions.md docs/local-development.md
git commit -m "feat: planner_v4 note rules, note checks and model override in the planning evaluation, notes review export"
```

### Task 5: Paid evaluation and model comparison

**Files:**
- Modify: `docs/planning-evaluation-history.md`, `docs/phase-1-carryover.md`
- Run folders: `backend/eval_data/planning/runs/` (summaries committed; full records git-ignored)

- [ ] **Step 1: User gate** — state the batch and its expected cost from the catalog prices and round 6 token counts: the baseline set once with the default model (round continuity), then the five models (deepseek-flash, deepseek-v4-pro, gpt-6-luna, gemini-3.5-flash-lite, gemini-3.8-flash) on the plan 2a set and the counts set, three runs each (30 runs). Wait for approval.
- [ ] **Step 2: Run** the approved batch on the test database, one run after another.
- [ ] **Step 3: Report** per model: as expected, first-attempt pass, average and maximum calls, cache-aware and uncached cost, model time, wall p95, and the note flags (rate and examples), with run-to-run spread over the three runs.
- [ ] **Step 4: Export the notes for review** — `notes_review` over all comparison runs into one CSV under the runs folder; the user rates `quality` and `should_not_appear` (and may comment); then summarise the user's ratings per model next to the automatic flags.
- [ ] **Step 5: Record** round 7 in `docs/planning-evaluation-history.md` (planner_v4 with the default model, rows in each set's table) and a "Model comparison" section with the per-model table and the user's ratings; add the outcome to `docs/phase-1-carryover.md`. Choosing a new default planning model is the user's decision and a separate change.
- [ ] **Step 6: User commit**

```powershell
git add docs/planning-evaluation-history.md docs/phase-1-carryover.md backend/eval_data/planning/runs
git commit -m "docs: planner_v4 evaluation round and planning model comparison"
```
