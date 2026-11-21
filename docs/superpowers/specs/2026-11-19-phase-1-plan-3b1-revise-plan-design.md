# Phase 1 Plan 3b-1: Revising the Plan in the Conversation

Status: draft for approval. The second of the T1.5 plans (roadmap step 5, `docs/superpowers/specs/2026-11-13-phase-1-agent-design.md` section 5). 3b was split in two (decided by the user, 2026-11-19): **3b-1** revising the plan in the conversation (this document), **3b-2** diner changes on an existing plan and the parse seeing the diner card. **3c** (saving the plan and the shopping list) follows. Rules come from `docs/design.md` 7.1 (the flow), 7.5 (validation, manual edits) and 7.6 (diners). Plan 3a (`2026-11-19-phase-1-plan-3a-manual-edits-design.md`) and everything before it still hold unless this document changes them.

## 1. Intent

Today every follow-up message plans the whole meal again, so hand edits are lost and a small wish ("茄子这道不要，换一个别的") throws away dishes the user liked. The user wants a follow-up to **change the current plan**, including a hand-edited one: change what was asked, keep the rest. Dishes the user added or confirmed by hand stay unless the user names them. A message that asks for a new meal still plans from scratch, and when the assistant cannot tell which one the user means, it asks.

Typical messages (the user's examples): "茄子这道不要，换一个别的", "想要辣一些的", "再加一道吧", "不要这么多鸡蛋的" (all revisions), "重新设计一顿4个菜的吧" (a new plan).

## 2. Goals, non-goals, anti-goals

**Goals**

1. Route a follow-up to one of: a new plan, a revision of the current plan, or other. When it cannot decide between a new plan and a revision, show a small choice card.
2. A revision changes only what the message asks for, keeps the original request's conditions (for example fridge-only, a dish count) with the message's changes on top, and changes as few dishes as it can.
3. Dishes the user added by hand or confirmed with "确认保留" are **locked**: a revision never removes them unless the message names them (decided by the user, 2026-11-19).
4. Overrides confirmed in 3a carry into every revision attempt and check, so a confirmed dish is never replaced as a violation.
5. The reply says what changed, written by code from the change, not taken from the model's own account.
6. A revision that cannot be done leaves the plan as it was and says why, with a way forward.

**Non-goals**

- Diner changes on an existing plan, the parse seeing the diner card, and the seven diner items in `docs/phase-1-carryover.md` ("Settled diners and later changes"): 3b-2.
- New intents such as adding a recipe, updating stock, or rating a dish. They come later; the route design must make adding them easy (Section 5.1), but none is built here.
- Saving the plan, the shopping list: 3c.
- Undo of a revision beyond "恢复模型方案" (3a's restore).

**Anti-goals**

- A revision that silently drops a locked dish, or replaces most of the meal for a wish about one dish.
- A revision that gets worse than the plan it started from: a failed revision must not replace the current plan.
- Tuning the new prompts on the held-out sentences.

## 3. Constraints

- Prompt versions are never edited once any record used them (memory: prompt version rule). New files: `route_v3`, `parse_v6` (only if Section 5.3 needs it; see there), and a new `revise_v1`. `PROMPT_VERSIONS` gains the revise prompt.
- Nicknames never reach a model; the label table ("Diner N") is never shown (plan 2b).
- A paid evaluation round is needed. Before it runs, the user gets the estimated cost and approves it. Evaluation and checks use only the test database.
- No database migration: revisions, like 3a's edits, live in the conversation state until 3c.

## 4. Approaches

| Approach | Trade-off | Decision |
|---|---|---|
| **A. A new revise step that returns a change list** (remove these ids, add these ids), applied and checked by code | One new prompt and its evaluation; one path for every kind of revision; locks and "change little" are checked by code; the reply's "what changed" comes from the change list | **Chosen (user, 2026-11-19)** |
| B. Reuse the planner and give it only the open slots | Almost no new prompt, but "想要辣一些的" and "不要这么多鸡蛋的" need a judgement on which dishes to replace that code cannot make; the planner would need the whole plan anyway, which is A in a roundabout form | Rejected |
| C. Code for simple cases ("再加一道", "茄子不要"), the model for vague ones | Cheaper for simple cases, but two paths with their own replies and failures, and mixed messages ("茄子这道不要，再来个汤") fall between them | Rejected |

## 5. Decisions

### 5.1 Routing a follow-up

- The route call (already made on every message) returns `plan`, `revise`, `other`, or `unsure` (between plan and revise). `route_v3` describes `revise` as changing the plan already in this conversation, and `plan` as a new meal or an explicit fresh start ("重新设计一顿", "换一套"). The intent is an enumeration in one place, so later intents are added there and in the route prompt only.
- Without a plan in the conversation (no `current_plan`), `revise` and `unsure` are treated as `plan` by code. A no-plan or early-stop reply counts as a plan: revising it starts from its dishes (possibly none) and its request.
- `unsure` shows a **choice card** (an interrupt, like the diner card) with the user's message and two buttons: "在这份方案上改" and "重新规划一顿" (both languages). The answer continues the run as `revise` or `plan`. The card appears once per message.
- "重新规划" (plan again) keeps its meaning: the latest request, planned from scratch.
- **Diner changes (until 3b-2, decided by the user, 2026-11-19):** when the parse of a revise turn finds a change in who eats (today's rule in design 7.6), the turn becomes a new plan: the diner card shows, then the meal is planned from scratch. This applies also when the message mixes a dish change and a diner change ("再加一道，小李也来"). Hand edits are not carried into that plan.

### 5.2 What a revision is allowed to change

- **Locked dishes:** a dish in the current plan that the user added by hand (3a add, still in the plan) or that has an override (3a confirm or keep). A locked dish may leave only when the latest message names it, that is, points at that dish ("茄子这道不要", "那道汤不要了"); a general wish ("不要这么多鸡蛋的") names no dish (decided by the user, 2026-11-19). **The model judges and code checks the quote** (decided by the user): for each locked dish it removes, the revise answer quotes the words of the message that name it; code checks that the quote appears in the message and matches the dish (part of its title, one of its ingredient names, or its category word such as 汤). Otherwise the attempt is rejected and retried. The revise prompt states that a general wish does not name a dish, and the evaluation has such sentences and counts wrongly removed locked dishes.
- **The target of a message, three kinds (decided by the user, 2026-11-19).** The revise step, not the parse, reads which dishes a message targets, since only it sees the plan's dishes. It answers with one kind, each with quotes from the message that code checks are present:
  1. **Named dishes** ("茄子这道不要", "那道汤不要了"): each quote matches its dish (part of the title, an ingredient name, or a category word).
  2. **An explicit scope** ("剩下全换", "全部换掉", "除了玉米这道剩下全换", "汤以外都换"): the quote states the scope and any exceptions; every dish in the scope may go, **locked dishes included** (the scope names them; decided by the user), the excepted dishes must stay, and the half limit below does not apply.
  3. **A general wish** ("想要辣一些的", "不要这么多鸡蛋的"): no dish is targeted; locked dishes stay and the half limit applies.
  The kind is the model's judgement; code only checks the quotes and applies the kind's limits. Reading a named dish as a general wish is the safe direction (fewer changes). Reading a general wish as naming a dish is not: "不要这么多鸡蛋的" read as naming, with the quote "鸡蛋", passes the quote check against a locked egg dish. This risk is accepted (decided by the user, 2026-11-19, no extra confirmation before a locked dish leaves); it is reduced by the prompt (an ingredient or taste word that does not point at one dish is a general wish, with examples), by the evaluation (wrongly removed locked dishes, target 0), and by the reply naming every dish it removed.
  Any phrasing not foreseen here falls into one of the three; when the revise step cannot tell which dishes are meant, it answers **unclear**, and the assistant asks which dish the user means instead of guessing (the plan stays as it was). Evaluation sentences cover each kind and some unforeseen phrasings.
- **Unlocked dishes** (chosen by the model): the revision may replace them. Whether the message names dishes is the same judgement, with a quote for each named dish. When the message names dishes, only the named dishes and the dishes needed for a count change may go. When the message names none ("想要辣一些的", "不要这么多鸡蛋的"), at most half of the unlocked dishes, rounded up and at least one, may go (Claude's call). Code checks both.
- **Counts: the current plan is the baseline** (decided by the user, 2026-11-19). A revision keeps the current plan's dish count and categories, including dishes added or removed by hand, unless the message states a count. "再加一道吧" is the current count plus one and removes none; "想要辣一些的" on a hand-grown plan of 4 keeps 4. Only an explicit count in the message ("4个菜就够了") changes it.
- If the wish can only be met by touching a locked dish, the revision does what it can and the reply says which locked dishes it left (for example "剩下两道蛋菜是你手动加的，没有动").
- **Checks and locked dishes (Claude's call):** a hard-rule failure caused only by a locked dish (H1 missing ingredients, H3 missing cookware, H6 time, H7 a named ingredient no longer used) stays a warning, as on a manual version (3a `ALLOWED_CODES`), since the revision may not remove that dish. Every dish the revision adds is checked as the planner's dishes are. A dish with an **open** conflict (red on the card, not yet confirmed) is not locked: the revision must replace it or the attempt fails H2, and such a replacement does not count toward the change-size limit.

### 5.3 The request after a revision

- The original request's conditions stand (fridge-only or shopping, time, named ingredients, dislikes, servings; counts follow Section 5.2), with the message's changes applied on top: a new count, a taste wish, an ingredient to avoid or to use.
- The parse already reads the whole conversation and returns the full request for the meal, so most changes need no new field. For relative counts ("再加一道"), the parse input gets one fixed line describing the current plan (its dish count and categories; dish titles are allowed, as they already reach models). Whether that needs `parse_v6`, or only an input line with `parse_v5`, is decided by a development run before the evaluation (left to the builder; a new version whenever the prompt text changes).
- Which dishes the message targets ("茄子这道") is the revise step's job, not the parse's.
- **A revision never asks the ask-first question** (decided by the user, 2026-11-19). Under either request style, the mode and the counts come from the current plan's spec unless the message changes them ("可以买菜了", "再加一道"); a field the parse leaves unstated on a revise turn is taken from the current plan, never from defaults. This covers a skipped question on the first turn and a short follow-up whose parse drops an earlier condition. A contradiction in the message (`request_problem`) is still asked about, as today. New plans keep the ask-first behaviour unchanged.

### 5.4 The revise step

- Input: the current plan (each dish with its id, title, category, main ingredients, and a "locked" flag), the latest message, the updated request summary, and the same shortlist and search tools the planner uses. Overrides are passed to every check of the attempt. No diner names; diners appear as today's anonymous rules.
- Output (`revise_v1`): the ids to remove (each named dish with its quote from the message), the ids to add, and a short note in the reply language. Code applies the change to the current plan, checks Section 5.2, then runs `validate_plan` with the overrides. A failed check is retried with the reason, up to the planner's retry limit.
- **What changed** is written by code from the change list ("把鱼香茄子换成了干煸豆角", "加了一道紫菜蛋花汤"), followed by the model's note. Sentences in both languages.
- **When it cannot be done** (every attempt fails, or the model reports that nothing fits): the current plan stays as it was; the reply explains why and offers next actions in 2a's style (for example allow shopping, drop the wish). This also covers the carryover item "An unmet taste wish is not reported" for revisions; for a new plan it stays in the carryover.

### 5.5 Versions, edits and the card

- A successful revision is a **model version** (3a spec 5.3); "恢复模型方案" goes back to the latest one. After a revision, 3a's manual edits work as before and start a new manual version.
- Overrides of dishes that remain are carried into the revised version; overrides of removed dishes are dropped (as 3a's remove does). The edit log keeps its entries; a revision adds no edit-log entry (it is a model version, recorded in `plan_versions` and the planning record).
- **Locks and overrides belong to the dish while it stays in the plan (Claude's call).** Each version stores its locked dish ids and overrides, so they survive later revisions; a dish added by hand stays locked through any number of revisions until it leaves the plan. "恢复模型方案" to a revised version brings back that version's locks and overrides (3a's restore of a version the model planned from scratch still has none). This changes 3a's restore, which today always clears overrides.
- 3a's quiet line "继续对话会重新规划，手动修改不会带入" is removed: a new message now revises.
- The revised plan's card is a new reply card; only the newest card is editable (as in 3a).

### 5.6 Records and evaluation

- Each revision is a planning run in `planning_runs`, marked as a revision with the version it started from. Planning metrics (first-attempt satisfaction, final pass rate) stay on new plans; revisions get their own: pass rate, locked dishes kept (must be 100%), dishes changed per revision, and route accuracy on follow-ups (plan / revise / unsure).
- Development scenarios: a new `scenarios_revise.yaml`, each a first message and a `follow_up`, built from the user's examples. Locked-dish scenarios need a hand edit between the turns; the runner gains a way to apply 3a edits between turns (left to the builder, on top of `between_turns`).
- A held-out set of revision sentences comes from the user, as in plan 2b, and no prompt is tuned on it.
- `route_v3` must not regress first-message routing: the existing scenario files run in the same round.
- The round's estimated cost goes to the user before it runs; results go to `docs/planning-evaluation-history.md`.

## 6. Success criteria

- The user's five example messages behave as in Section 1 after a plan, including after hand edits.
- A locked dish never leaves unless named; overrides are never raised again; a failed revision leaves the plan unchanged and says why.
- Route accuracy on follow-ups and on first messages, and the revision metrics in 5.6, meet thresholds the plan sets before the round runs.
- Backend and frontend tests cover routing (including no current plan and `unsure`), the lock and change-size checks, the count change, the failure path, versions and overrides.

## 7. Left to the builder

- The state fields for the revision (start version, change list) and the planning-record marking.
- Whether the revise step is its own agent loop or the planner's loop with a different prompt and answer schema.
- The wording of the choice card and the "what changed" sentences (both languages).
- The exact parse input line for the current plan, and whether it needs `parse_v6` (Section 5.3).
- The runner's way to apply hand edits between turns.

## 8. To the carryover

- Unmet taste wishes on a new plan (not a revision) stay in the carryover (→ T1.5 or T1.10).
- Diner changes on an existing plan (→ 3b-2).
