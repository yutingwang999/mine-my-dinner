# Chat Page Redesign — Design

Status: approved in conversation with the user (2026-11-15), to be built after Phase 1 plan 2a is merged, on its own branch, before plan 2b.

## 1. Intent

While trying plan 2a on real meals, the user found the chat page does not behave the way people expect from conversational agents (ChatGPT and similar): the request-style setting at the top looks like a question asked again, the input is a single line with a label, the plan card sits outside the conversation and only the latest plan is ever visible, and progress and questions appear away from the reply they belong to. The goal is a page that reads and works like a familiar chat agent, so the planning features are usable without explanation.

## 2. Goals, non-goals, anti-goals

Goals:

- The home page is the chat page, with a welcome state when the conversation is empty (Section 5.4).
- One conversation shown as a chat: the user's messages on the right as bubbles, the assistant's replies on the left, each reply carrying its own plan card.
- Scrolling up shows every earlier plan of the conversation; only the newest plan's actions can be used.
- The input area is fixed at the bottom: a multi-line box, Enter sends, Shift+Enter adds a line, a send button.
- Under the input, a switch for the request style ("信息不全时先问我"), placed on the user's saved choice, and the "new conversation" button.
- Progress ("正在查看冰箱……") and the assistant's questions (request style on first use, missing mode or count, contradictions) appear in the conversation where the next reply will be.
- Works on narrow screens.

Non-goals (decided by the user):

- No list of past conversations and no left sidebar; "new conversation" still starts fresh and the old one is not listed. A left-hand layout and history list may come later.
- No change to what planning does, what the plan card says, or the allergy note; only where and how things are shown.

Anti-goals:

- A plan card's actions sending a message for an old plan (only the newest plan is actionable).
- Plan data reaching the models: storing plans per reply must not add them to anything sent to route, parse, or the planner.
- Losing existing conversations: threads saved before this change must still open.

## 3. Constraints

- Frontend: Next.js 16, React 19, next-intl, plain CSS in `frontend/src/app/globals.css`; no new packages (project rule: the agent does not install packages).
- Every user-facing string in both `messages/en.json` and `messages/zh.json`.
- Frontend tests, lint, and build run through PowerShell (Git Bash vitest fails collection).
- Backend changes follow the project rules (test database only, no real model calls in tests).

## 4. Approaches

| Approach | Trade-off |
|---|---|
| **Rework the existing components and plain CSS (chosen, Claude's call)** | No new dependency; fits the interrupt questions and plan cards the app already has; the rest of the site keeps its look. More CSS written by hand. |
| Add a UI library (Tailwind with shadcn/ui) | Faster polished components, but new packages and a second styling system next to the rest of the site. |
| A ready-made chat UI kit | Chat layout for free, but it does not know our interrupts, plan cards, and next actions, so much of it would be worked around. |

The user handed over how it is built; Claude chose the first.

## 5. Decisions

### 5.1 Plans kept per reply (user chose option B)

Today the conversation state keeps one `current_plan`, overwritten by each new plan; earlier plans exist only in `planning_runs`.

- Each plan is stored with the assistant reply it belongs to, in graph state **outside the message content** (for example a mapping from the reply's message id to its plan), so nothing about plans is added to the messages sent to the models. `current_plan` stays for code that uses it.
- `GET /api/chat/threads/{id}` returns, for each visible assistant message, its plan when it has one. The streamed `plan` event is unchanged; the frontend attaches it to the reply being streamed.
- Threads saved before this change have no per-reply plans: their `current_plan` is shown on the last assistant reply.
- **Which card is actionable (decided by the user, option A):** the newest plan in the conversation stays actionable until a newer plan replaces it, even when later turns produced no plan (a chat reply, an "unclear" reply, a failed run). Its buttons are disabled only while a run is working or a question is waiting for an answer. Older cards render the same content with their buttons disabled.
- **A plan with an empty note** (Claude's call): it still gets its own assistant item, showing only the card, so no plan is dropped. The streamed `plan` event attaches to the reply being streamed, or starts a card-only item when no text came first.
- **Old threads** (Claude's call): a thread saved before this change keeps only `current_plan`, and which reply it belonged to cannot be known. It is shown as a card-only assistant item at the end of the conversation, not under a reply it may not match. Earlier plans of old threads are not shown.

### 5.2 Layout (Claude's calls, confirmed by the user)

- The site navigation at the top stays. The request-style dropdown and the top "new conversation" button are removed from the top.
- Messages: user on the right in a bubble; assistant on the left with a light background or none; maximum bubble width about 80% on narrow screens.
- The input area is fixed at the bottom of the viewport: a multi-line text box that grows to a few lines, Enter sends, Shift+Enter inserts a line break (IME composition must not send), a send button. Under it: the request-style switch, the "new conversation" button, and the provider notice in small text.
- The view scrolls to the newest message when a message is sent or a reply streams, unless the user has scrolled up to read.
- Progress shows as one line in the assistant position while a run is working.
- Questions appear in the conversation as an assistant message: the first-use request-style choice keeps its two buttons there; a missing-detail or contradiction hint becomes an assistant message (today it sits above the input).
- **Questions stay in the history, for display only (decided by the user, option A).** Today a question exists only while it waits for an answer, so after a reload the user's answer ("可以买") appears with no question above it. Each question the assistant asks is saved in its own display record in the thread's state, in conversation order, and returned by `GET /api/chat/threads/{id}` with the messages. It is never added to the messages the models read: route and parse see exactly what they see today, so planning and the evaluation are unaffected. (Letting the parse see the questions is a separate change for plan 2b; see `docs/phase-1-carryover.md`.)
- Errors appear in the conversation as an assistant-position notice and are cleared on the next send, as today (Claude's call).
- The progress line is transient: it disappears when the reply arrives. Screen readers announce the final reply once; the progress line is a separate status, so nothing is read twice (Claude's call).

### 5.3 The request-style switch (user's request)

- A switch labelled "信息不全时先问我" / "Ask me first when details are missing". On means "ask first", off means "plan, then I adjust"; the text next to it shows the current meaning.
- It reads the saved preference on load and sits on that position; changing it saves at once through the existing `PUT /api/preferences/request-style` and applies to every conversation, like the dropdown today.
- With no saved preference yet, the switch shows off ("plan, then I adjust", the default when skipped) and the first-use question still appears in the conversation the first time a plan is requested; answering it moves the switch.
- Accessible as a switch (keyboard and screen reader), not only by mouse.

### 5.4 The home page is the chat (decided by the user)

Today the home page shows only the app name and tagline, and the chat lives at `/[locale]/chat`.

- **The home page becomes the chat page** (option A of three; B, a second input box on the home page that jumps to the chat, duplicated the input and behaved differently from the chat page; C, an overview page with fridge and recent plans, needs new data endpoints and is left for later). `/[locale]` renders the chat; `/[locale]/chat` redirects to it, so old links keep working. The navigation's separate "对话" / "Chat" item is removed; "首页" / "Home" leads to the chat.
- **Welcome state.** When the conversation has no messages (first visit, or after "new conversation"), the message area shows the app name and tagline that the home page shows today ("MineMyDinner", "从你收藏的菜谱和冰箱里，挖出今晚的晚饭。" — decided by the user), and a few example requests as buttons (for example "两菜一汤，只用冰箱里的", "三个人吃，可以买菜", "做个快手菜，20 分钟内"). An example button fills the input box; it does not send. The composer and the request-style switch are in their usual place at the bottom. Sending the first message turns the page into the normal conversation.
- **Opening the site continues the last conversation** (Claude's call, open to change by the user): with no list of past conversations, starting a new one on every visit would hide the previous conversation for good. "New conversation" starts a fresh one. When a history list is added later, opening on a new conversation (as ChatGPT does) can be reconsidered.
- The welcome texts and example requests are in `messages/en.json` and `messages/zh.json`.

### 5.5 The reply text that comes with a plan (decided by the user)

The reply shown with a plan is the planner model's `note`, unchecked; in the plan 2a checkpoint it miscounted the dishes ("3 道荤菜……实际为荤 4 道") and suggested a dish outside the plan. The user found a fully code-written reply too rigid and chose to **change only the prompt**, then measure which model writes the note most reliably.

- **`planner_v4`** (a new planner prompt version; only the note rules change): the note is one sentence with one practical tip (which dish to start first, which takes longest, how the dishes go together); it does not count or list the plan's dishes (the card shows them); it names no dish outside the plan and suggests no swaps; it is in the reply locale and makes no allergy safety claims (as today). The `PlanProposal` schema stays the same, so the tool version is unchanged.
- **Automatic note checks in the planning evaluation**, per scenario and per run: names a library dish that is not in the plan; counts dishes (a number next to 道/菜/dish words, or 荤/素 counts); more than one sentence; wrong reply language (existing check). Reported in `summary.json` (counts and rates, no model text) and in the git-ignored `results.jsonl` (the note itself).
- **Model comparison.** The evaluation gains `--planning-model <catalog name>`, which runs the whole `planning_eval` task (route, parse, planner) on that model and records it in `summary.json`. All five tool-calling catalog models are compared — deepseek-flash (current), deepseek-v4-pro, gpt-6-luna, gemini-3.5-flash-lite, gemini-3.8-flash — on the plan 2a and counts scenario sets, three runs each, so stability shows. Because the whole chain changes, the comparison reports planning results (as expected, first pass, calls, cost, time) next to the note checks. Paid runs need the user's approval with the expected cost.
- **The user rates the notes.** After the automatic checks, the notes of every run are exported for review: model, run, scenario, the plan's dishes, the note, and the automatic flags, with empty columns for the user's judgement (quality, and whether anything appears that should not). Model text stays out of Git. The user decides which model plans from the combined result; switching the default planning model is a separate, approved change.

### 5.6 Read-only recipe detail (brought forward from T1.9, decided by the user)

While trying the redesigned page the user wanted to see how to cook a dish from the plan card. The Phase 1 design planned a read-only recipe detail view in T1.9, shared with the plan card's "view dish"; it is built now, in both places, with one component.

- **One `RecipeDetail` component** shows a recipe read-only: ingredients with amounts as entered (amount and unit; "适量" for a vague amount; nothing for a missing one; choice groups shown as "任选其一"; optional lines marked), cookware, time and its source, servings, the steps summary, the source link, and the source text kept at import (collapsed by default).
- **Plan card:** each dish gets a "查看做法" / "How to cook" toggle that expands the detail inline under the dish (compact) and collapses it again; the recipe is fetched on first open, with loading and error states. The dish's missing ingredients are marked in the ingredient list, matching the card's missing list. Viewing is not an action: it works on every card, including older, non-actionable ones.
- **Recipe library:** each recipe gets a "查看" / "View" link to a new read-only page `/[locale]/library/[id]` with the full detail and a link back to the library and to edit.
- The backend already serves a recipe (`GET /api/recipes/{id}`); no backend change.

## 6. Left to the builder

- Exact colours, spacing, and the switch's look, within the site's existing style.
- How the per-reply plan mapping is keyed (message id is preferred over list index, so it survives message filtering), and how old checkpoints are detected.
- Component split (for example a message list, a composer, a switch component).
- Tests: per-reply plans returned by the read endpoint and not present in any model input; old threads; only the newest card actionable; Enter and Shift+Enter, IME not sending; the switch's initial position from the saved preference and saving on change; the first-use question; narrow-screen layout where testable.
