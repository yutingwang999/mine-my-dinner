# Phase 1 Plan 3b-2: Revise Simplification (plan one of two)

Status: draft for approval (the user's answers to the builder check, 2026-11-22, are included). Plan 3b-2 was widened by the user on 2026-11-22: diner changes on an existing plan **and** a simpler revise turn, done together. The user chose to split it into two plans (approach A in Section 4): **this document is plan one, the revise simplification**; plan two, diner changes on the new structure, is outlined in Section 9 and gets its own design later. Plans 3b-1 and 3b-1b (`2026-11-19-phase-1-plan-3b1-revise-plan-design.md`, `2026-11-21-phase-1-plan-3b1b-revise-fixes-design.md`) and everything before them still hold unless this document changes them.

## 1. Intent

A follow-up message today passes three models that each read the user's words: the route (revise, plan, unsure, other), the parse (the whole request) and the revise step (which dishes to drop, replace or add, with quotes). Their prompts carry overlapping boundary rules, and code has gathered exceptions to keep them consistent. Plan 3b-1b's development runs moved misses from one boundary to another (`route_v4` → `route_v5` → `route_v6`), the prompts kept growing, and round 10 missed most thresholds. Part of that was the evaluation itself: many scenarios accepted only one of several reasonable answers (asking back versus acting), so ordinary variation counted as failure and drove more prompt patches.

The user wants follow-ups whose results are **predictable in the sense that the app never does something surprising or wrong**, not in the sense that an ambiguous sentence always takes the same path; prompts that state principles instead of patches; and a structure that later features (diner changes first) can join without new boundary rules.

A spike on 2026-11-22 (recorded in `docs/planning-evaluation-history.md`, "Spike: one reader") tried the opposite: one model reading everything and choosing the new plan, with code keeping only the hard checks. It scored 24-26/37 against 33-35/37 today, removed locked dishes without the code guards, and a stronger model did not help; only the number of calls fell (about 1.5 per follow-up instead of 3.4). The structure and the code guards matter more than model power.

## 2. Goals, non-goals, anti-goals

**Goals**

1. A follow-up on an existing plan is read **once**: one model sees the whole context and answers what the message wants, in a structured form (Section 5.2).
2. Code decides, from that answer, which dishes change and keeps every guard (locks, counts, diner and one-meal rules, stock, cookware, time). A model chooses concrete dishes only inside the frame code gives it (Section 5.4).
3. An ambiguous follow-up is acted on with its most likely meaning, and the reply says what was changed and how the message was read (user decision, 2026-11-22). A question is asked only when no reasonable change exists (Section 5.6).
4. The new prompts state principles, not boundary patches, and are much shorter than the prompts they replace.
5. Fewer model calls per revise turn and no long search loops (Section 5.7).
6. Evaluation expectations in two layers: what must never be wrong, and where several outcomes are acceptable (Section 6).
7. A one-meal wish can be withdrawn on a later turn (carryover "One-meal wishes", first item), because the reading states the meal's wishes after the message rather than adding to them.

**Non-goals**

- Diner changes on an existing plan: plan two (Section 9). In this plan a change in who eats, or a lasting rule stated in a follow-up, behaves as today: the diner card shows and the meal is planned anew.
- The first turn of a thread (no plan yet): the route, `parse_v8`, the diner card and `planner_v5` are unchanged.
- Trimming `parse_v8` and `planner_v5` (carryover "Prompt length"): later.
- Remembering dishes the user removed by hand so they never come back (offered and declined by the user, 2026-11-22).
- What "肉" covers in a one-meal avoidance (carryover "Development run 4 leftovers", item 1): stays open for the user.

**Anti-goals**

- A locked dish (added by hand, or kept against a rule by confirmation) removed when the user's words do not point at it, a rule it breaks, or a one-meal avoidance it breaks.
- A dish removed or added that the message did not ask for; a plan that shrinks or grows without the message saying so.
- "Not in the library" said about a dish that is in the library (for example hidden by the time limit; plan 3b-1b's over-time-limit handling stays).
- An unmet wish that the reply does not mention.
- Patching a prompt to force one of several reasonable answers.

## 3. Constraints

- Prompt versions any record or evaluation run used are never edited. New files in this plan: the follow-up reader prompt and the dish-picker prompt (names in Section 5.8); `route_v6`, `parse_v8`, `planner_v5` and `revise_v4` stay as they are. Once a new version has been used by a run, a further change makes the next version. `docs/prompt-versions.md` lists every new version.
- Whenever a model's input changes, `docs/planner-what-each-model-sees.md` (and its private Chinese copy) is updated in the same change.
- The user performs every Git operation. Paid evaluation runs need the estimated cost and the user's approval first. Evaluation and checks use the test database only.
- Saved nicknames never reach a model; "Diner N" is never shown to the user; cards reach the client through `client_card` without server-only fields.
- Every revision and button turn is a planning run with all its events; button turns keep `request["button"]`.
- The next-action buttons keep applying their change in code (plan 3b-1b, design 5.5).
- No database migration is expected (the new fields live in the conversation state and JSON records).
- Held-out scenario files are the user's: an expectation in them changes only with the user's approval; no held-out sentence appears in a prompt, a test or a development scenario.

## 4. Approaches

**How to split the work (decided by the user, 2026-11-22: A).**

| Approach | Trade-off | Decision |
|---|---|---|
| A. Two plans: first the new structure (this plan), then diner changes on it | The new structure is proven on its own before a feature joins it; a regression points at one change. Two evaluation rounds | **Chosen** |
| B. One plan for both | One round, and diner changes are designed into the reading from the start; but a large change in which a missed threshold cannot be traced to structure or feature | Rejected |
| C. Keep three models; trim prompts, relax expectations, add diner changes in today's path | Least work, but keeps the double reading and the boundary rules the user wants gone | Rejected |

**How much one model does (decided by the user, 2026-11-22: two steps).**

| Approach | Trade-off | Decision |
|---|---|---|
| Reading and dish choice in two steps: a reader answers what the message wants; code computes the frame; a picker chooses dishes in it | Usually 2 calls, 1 when no new dish is needed; each judgement is small and checkable; a miss shows which step made it | **Chosen** |
| One step: the reader also chooses the dishes; code checks afterwards | About 1-1.5 calls; but it is the spike's shape, which confused a new meal with a revision and got counts wrong | Rejected |
| One model reads everything and code keeps only hard checks | Tried as a spike: 24-26/37, locked dishes removed | Rejected (spike) |

## 5. Decisions

### 5.1 The turn, end to end

When the thread has **no current plan**, nothing changes: route, parse, diner card, planner.

When the thread **has a current plan** and the user writes a message (not a button):

1. No route call. The **reader** (one model call, no tools) receives the whole context (Section 5.3) and answers a `FollowUpReading` (Section 5.2).
2. Code acts on the reading's `kind`:
   - `change` → code builds the frame (Section 5.4); when new dishes are needed, the **picker** chooses them; code validates and presents the revision (Section 5.5).
   - `new_meal` → today's new-plan path for this turn (parse, diner card when needed, planner), as a route answer `plan` does today.
   - `people` → a change in who eats or a lasting rule about someone: today's behaviour for this plan (the parse runs, the diner card shows, the meal is planned anew). Plan two replaces this.
   - `other` → a message that is not about this meal's plan ("你好", "谢谢", a cooking question, a request the app cannot do yet such as adding a recipe): today's fixed "other" reply; the plan, the request and the card stay as they are, and the next message is read against the same plan. No parse call.
3. The choice card (revise or plan) is no longer produced: an ambiguous message is acted on (Section 5.6). The card, its resume and its frontend component were removed in Task 8 (user decision, 2026-11-23: no saved thread was paused on one).

Button turns: unchanged up to the request (code applies the button's change to the card's request). A button that revises again skips the reader: the frame builder (Section 5.4) compares the request the button built with the kept plan, and the picker fills what it asks for, with the offering turn's words as the wish context (plan 3b-1b behaviour, now feeding the frame and the picker). Amended 2026-11-24 (user decision, after development run 2): the card keeps the offering turn's structured reading (its unfinished parts and its wish), and the button rebuilds the reading from it, so the picker gets the wish from that reading and no longer the offering turn's words, which could contradict the button ("荤菜再来一个" on a relax-split button). Only a card saved before cards kept that reading still uses the words.

### 5.2 The reading (`FollowUpReading`)

The reader answers what the message wants, never which new dishes. Field names are the builder's; the content is decided:

- `kind`: `change`, `new_meal`, `people`, `other`.
- `reading`: one short sentence in the reply locale saying how the message was understood, shown to the user only when the message was ambiguous (`ambiguous: true`).
- For `change`:
  - `remove`: dishes of the current plan the message removes, each by id with `drop` (the plan gets smaller) or `replace` (another dish takes its place; the picker chooses it), and the user's words that point at it (a quote; for a locked dish code checks that the quote is the user's own words, Section 5.4).
  - `replace_with`: for a replaced dish, the dish the user named, as the user's words (a library title or not).
  - `add`: dishes to add, each as a dish type and a count, or a named dish (the user's words).
  - `wish`: a taste or style wish the new dishes should meet ("辣一些", "清淡点"), as the user's words.
  - `wish_targets` (amended 2026-11-23): when the wish is about the plan's dishes rather than a dish the message already changes, the ids of the dishes it touches ("西红柿太多了" → the dishes with tomato; possibly none). Code caps how many may change (Section 5.4).
  - `meal_avoid`: the **whole** list of things to leave out of this meal after this message (so a withdrawn wish is simply missing from it).
  - `counts`: only counts the message itself states (a total, sides, soups, staples, desserts, snacks), absolute or relative to the plan as shown; empty when the message states none.
  - `conditions`: changes to mode, time limit, named ingredients (must use, prefer) that the message states.
- For `people`: nothing more in this plan (plan two adds who joins or leaves, and the rules).

Two things are deliberately not in the reading: the new dishes (the picker's job) and the final counts (code's job).

### 5.3 What the reader sees

One input: the reader prompt; the whole conversation (user messages and assistant reply texts, with saved nicknames as "Diner N" labels as today); the current plan, one dish per line with id, title, dish type, main ingredients and its mark (`LOCKED`, `LOCKED KEPT CONFLICT`, `OPEN CONFLICT`, `BREAKS THIS MEAL'S WISH`), reusing today's plan lines; the confirmed diner summary as anonymous rule lines (Diner 1 is the user; this is new for any model that reads the user's words, and it fixes the carryover root cause "the parse never sees the diner card" for follow-ups); and the request's current conditions (counts, mode, time limit, named ingredients, one-meal avoidances, taste wish). The reader gets no shortlist, no library titles and no tools: deciding what the message wants needs no recipes. A named dish is passed on as the user's words; code places it only when it equals a library title, and otherwise the picker searches for it (Section 5.4).

### 5.4 Code builds the frame

**Principle (user decision, 2026-11-23): code checks, it does not interpret.** Whatever needs reading language is a model's judgment: which dish the user's words point at, which dish a name means, which dishes a wish touches, what kind of dish should take a dish's place. Code holds the invariants (locks, stated counts, diner rules and one-meal avoidances, stock, cookware, time) and checks every result against them. Before this amendment four code rules made such judgments: same-type slots, title matching beyond an exact title, at least one change for a general wish, and a fixed reading of how words point at a dish. Each needed a new exception whenever a new sentence arrived, the same growth the route and revise prompts had shown in plan 3b-1b.

The frame builder is one piece of code for both paths: it takes the **new request** (the plan's request updated by the reading, or by a button) and the **explicit changes** (the reading's `remove`, `add` and `wish_targets`; none on a button turn) and compares them with the current plan. (Claude's call after the builder check: one engine for readings and buttons, so a button turn needs no reader.) From that, code decides only:

- **Which dishes may change.** Every dish gets one of: keep, drop, or a slot to fill (a replace).
  - Dishes the reading lists in `remove`, as it marks them (drop or replace). An id that is not in the plan is ignored and recorded.
  - Dishes the reading lists in `wish_targets` (the dishes a taste wish touches; possibly none): each becomes a replace slot, at most half of the unlocked dishes, rounded up. When the wish touches no dish, nothing changes for it, and the reply says the wish matched nothing in the plan.
  - Dishes that break the new request: a diner rule, a one-meal avoidance (old or new), stock in fridge-only mode, cookware, or the time limit. Each becomes a replace slot. Code finds them from the dish facts; the reader never lists them. These are facts code can check, not readings.
  - A stated count lower than the plan: exactly the difference must go, and the picker chooses which unlocked dishes. A must-use ingredient that no dish uses: the picker chooses one unlocked dish to replace, unless the counts call for an add, which then carries the ingredient.
- **Locked dishes.** A locked dish moves only when it breaks a diner rule or a one-meal avoidance, or when the reading lists it in `remove` with a quote that is the user's own words from this message. Code checks that the quote appears in the message; it no longer requires the words to match the dish's title or ingredients (whether "不适合的菜" points at a dish is a reading, not a string match). A locked dish never moves for stock, cookware or time. Because a wrongly removed locked dish is the worst anti-goal, the reader prompt states it as a principle (list a locked dish only when the message clearly points at it), and evaluation checks it on every scenario.
- **The counts.** Counts the message states are invariants: code checks the result against them. Unstated counts follow the change list: a drop shrinks, a replace keeps, an add grows. Code checks the size of the result, not the type of each new dish.
- **What each new dish is.** Code sets no type for a replace slot. The picker chooses the dish, keeping the plan's balance unless the message suggests otherwise ("不想吃肉" may take a vegetable dish in a meat dish's place). An add keeps the type the reading gave it, since that is the reading's judgment, not code's.
- **Named dishes.** A name that equals a library title (after folding) is placed directly by code, with no picker call. Any other name becomes a slot carrying the user's words; the picker searches (search ranks by meaning when embeddings are available) and places the dish the words mean, or answers that nothing fits. "Not in the library" is said only when the picker found nothing. A found dish hidden by a hard condition still gets nothing-fits with the real reason and the matching next action (plan 3b-1b's over-time-limit handling, generalised).
- **Empty plan.** A change that would leave no dish asks whether to plan a new meal (today's R9 question; Section 5.6).
- **Nothing to choose.** When no new dish and no choice is needed (drops only, exact titles placed), the picker is not called.

### 5.5 The picker

Called only when new dishes must be chosen, or when code left a choice of which unlocked dishes go (Section 5.4). It receives the frame: the slots to fill, each with the dish it replaces (or the add's type) and the user's words for it (a name or a must-use ingredient); any "choose up to N of these unlocked dishes" choice; the wish (as the user's words); the diner and meal rules; the current plan (for balance and to avoid repeats); and the revise shortlist; it may search with a small cap (Section 5.7). It answers, for each choice, the dishes it chose; for each slot, a recipe id or "nothing fits" with a short reason; whether the wish was met (as `wish_met` does for the planner today); and one or two sentences to the user on why the new dishes fit. Code validates the result (stated counts, an add's type, choices within their limits, rules, stock, cookware, time, a must-use ingredient) and retries the picker at most once with the reasons.

**When part of a change cannot be done** (decided by the user, 2026-11-22): the parts that can be done are applied and form a new version; a replace slot left unfilled **keeps its original dish** (the plan never shrinks unasked), **except** a slot made because the dish breaks a diner rule or a one-meal avoidance: with no replacement that dish is **removed**, and the reply says no replacement was found (decided by the user, 2026-11-22: the plan may get smaller, but it never keeps a dish that breaks a rule the user stated); a named dish not in the library, or hidden by a hard condition, is not added and the reply says why. The reply lists what could not be done, and the card offers the next actions for those parts only (import recipes, drop the time limit, allow shopping, as today's failure reasons map them); a button pressed there re-runs only the unfinished parts against the new version. A change where nothing at all can be done keeps the plan, as today's nothing-fits. (Confirmed by the user, 2026-11-22: apply what can be done, rather than keeping the whole plan.)

**The wish lasts** like today's `query`: the reading's `wish` replaces the request's taste wish for later turns and buttons, and an empty `wish` on a message that is clearly about something else keeps it (as today). An unmet wish (`wish_met` false) is said in the reply by code.

The picker never removes a locked dish, never changes stated counts, and chooses what goes only inside a limit code set. It chooses the kind of each new dish (amended 2026-11-23); plan 3b-1b's general-wish failure (a swap of the other type that broke the counts) is caught by code when the counts are stated, and otherwise the picker keeps the plan's balance unless the message suggests otherwise.

### 5.6 Ambiguity and questions

- An ambiguous message is acted on with its most likely meaning (user decision, 2026-11-22). The reply leads with code's sentences about what changed (today's "去掉了… / 把…换成了… / 加了…"), adds the reader's `reading` sentence when the reader marked the message ambiguous, and then the picker's note. The user can restore the previous version (existing feature) or say what they meant.
- A question is asked only when no change is reasonable: a change that would leave no dish (today's question), or a reference that matches no dish at all. "Which soup?" with two soups is no longer asked: the reader picks the more likely one and says so.

### 5.7 Calls and limits

- Reader: one call, no tools, no retry (a reading that fails schema validation is retried once by the structured-output layer, as today).
- Picker: at most two attempts; at most two search calls per attempt.
- So a revise turn makes 1 call (no new dishes), 2 calls (the usual case), and at most about 7 including searches. Exact limits are the builder's within these bounds.

### 5.8 Prompts and records

- New: the reader prompt (for example `follow_up_v1.md`) and the picker prompt (for example `pick_v1.md`). Both state principles: act on the most likely meaning; never change what the message did not ask for; a wish for this meal is not a lasting rule; say how an ambiguous message was read; never use internal words (LOCKED, ids, "Diner N") in user-facing text. Examples are invented, never from a held-out set, and kept few.
- Retired for follow-ups: `route_v6` (still used for a thread's first turn), `parse_v8` (still used for the first turn and for `new_meal` / `people`), `revise_v4` (replaced by the picker).
- The planning run records the reading, the frame code built, each picker attempt and the validation, so evaluation and debugging can see which step made a decision.
- `docs/planner-what-each-model-sees.md` gains the reader and the picker, with complete examples generated from the code.

### 5.8a One entry for intents later (decided by the user, 2026-11-22: option A)

`docs/design.md` plans more chat intents: importing a recipe, manual entry, ingredient substitution (T1.8), rating a dish (T1.6) and managing or querying inventory. With this plan, a message's intent is decided in two places: `route_v6` on a thread's first turn, the reader's `kind` once a plan exists. This plan keeps that split (the first turn is out of scope) and prepares the merge:

- `kind` is built to take new values; until T1.8 every message that is not about the meal is `other`.
- **At T1.8** the reader also reads a thread's first turn, `route_v6` is retired, and every message is classified in one place. (Recorded here so T1.8's design starts from it.)
- **The reader stays short as intents are added.** A new intent adds a one- or two-line definition to the reader (for example "a recipe link or screenshot to save → import_recipe") and the few fields it needs (the link, the ingredient to substitute); the details are handled by that intent's own step (the import extraction, the substitution Skill, tools), in line with T1.7's "names and descriptions only" for Skills.
- **A size budget**: the reader prompt stays under half of `route_v6` plus `revise_v4` together; `docs/prompt-versions.md` records each version's length. If T1.8 pushes it over, the reader splits into a short classifier and a change reader (one more call on change turns), decided then from its length and the evaluation.

### 5.9 What changes in plan 3b-1/3b-1b code

Kept: buttons in code, `client_card`, the over-time-limit list, `carry_unstated` (now fed by the reading for `change`, and still used when a follow-up goes to the parse), the plan card, edits and restore, the presenting sentences, the empty-plan question. Replaced: route on follow-ups, the revise step's change list (`RevisionProposal`) and its R1-R9 checks as after-the-fact gates (the same rules become code that builds the frame; a quote check remains for locked dishes). The builder removes code the new path no longer reaches, keeping what old threads need to load.

## 6. Evaluation and success criteria

**Two layers of expectations.**

- **Must never be wrong** (checked in code by the runner, every scenario, every run): no locked dish removed without its quote, rule or avoidance; no dish removed or added that the scenario does not allow; stated counts met; diner and one-meal rules met; no "not in the library" for a library dish; no "0 dishes"; an unmet wish is said; no internal words shown.
- **Unnecessary questions** (decided by the user, 2026-11-22: not a failure): a follow-up that ends with a question although a reasonable change exists (anything but the empty-plan question and a reference that matches no dish) still counts as expected when the scenario's outcome allows the plan to stay; it is counted apart as an unnecessary question, with a loose limit (thresholds below), so a general habit of asking shows up without forcing prompt patches.
- **Several outcomes acceptable** (`expect_any_of`, or looser keys): where a sentence has more than one reasonable reading, the scenario lists every acceptable outcome. Scenarios that today accept only "ask" or only "choice card" are rewritten.
- **Where "which dishes may change" comes from** (Claude's call after the builder check): the runner derives it from the existing keys (`expect_removed`, `expect_removed_any`, `expect_max_changed`, `expect_unchanged`, `expect_titles_include`) and the frame the run recorded; a scenario that sets none of them is checked only by the other must-never-be-wrong checks (locks, counts, rules). The development set gets the keys it needs while it is rewritten. In the regression sets, `expect_route` maps to the reading's kind (`change` → revise, `new_meal` → plan), and every `expect_choice_card` / "unsure" expectation is listed for the user to rewrite, with the other proposed changes.

**Sets.**

- The development set (`scenarios_revise.yaml`) is rewritten to the two layers by Claude; scenarios that tested the route and the choice card are changed to the act-and-say behaviour.
- The 3b-1 set (`scenarios_revise_heldout.yaml`) and the 3b-1b set (`scenarios_revise_heldout2.yaml`, results already read in round 10) become regression sets. Their expectations are the user's: Claude lists every expectation that conflicts with this design (for example "which soup" questions, choice cards) or that round 10 showed to be a set-up mistake, and the user approves each change before the files are edited.
- A new held-out batch written by the user (decided 2026-11-22), 15-20 sentences, after this design and the plan are approved and before implementation, so the new structure is judged on sentences nobody tuned against. Suggested kinds: drops, replaces and adds; ambiguous messages; a one-meal avoidance and taking it back; a condition and a count in one message; a change that can be done only in part; a message after a button; a message that is not about the plan.
- Every older set runs once in the round (no regression on new plans; the first turn is unchanged).

**Thresholds** (set before the round; approved by the user, 2026-11-22):

| Threshold | Required |
|---|---|
| "Must never be wrong" checks | 0 violations over every run of the round |
| Development set as expected | ≥ 90% |
| New held-out set as expected | ≥ 80% |
| 3b-1 and 3b-1b sets (regression) | ≥ 80% each, every miss explained |
| Model calls per revise turn | average ≤ 2.5, no turn over 7 |
| Unnecessary questions | ≤ 20% of the follow-up scenarios where a reasonable change exists |
| Older new-plan sets | no regression |
| Prompt size | the reader and picker prompts together shorter than `route_v6` + `revise_v4` |

## 7. Records

New prompt versions in `docs/prompt-versions.md`; the round in `docs/planning-evaluation-history.md`; deferred findings in `docs/phase-1-carryover.md` under a new "Deferred from Phase 1 plan 3b-2 (one)" section; carryover items this plan closes marked closed (the round-10 misses rooted in the double reading, "The revise turn is hard to reason about", one-meal wish withdrawal).

## 8. Left to the builder

- Field names and the exact schema of the reading and of the picker's answer.
- The picker's exact limits within Section 5.7.
- How named dishes are matched to library titles (exact title, then a forgiving match; the reader passes the user's words). Amended 2026-11-23: code places only an exact title; the picker searches for any other name (Section 5.4).
- The wording of the reading sentence and of new reply sentences (both languages).
- Removing code the new path no longer reaches, with old threads still loading.
- The prompts' examples (invented, few).

## 9. Plan two (outline, its own design later)

Diner changes on the new structure: the reading's `people` kind carries who joins or leaves and the rules stated; the diner card still shows for any change in who eats (user decision, 2026-11-22: adding or removing people always confirms through the card; the first-turn card is unchanged); after the card, code adjusts the current plan instead of planning anew (user decision): the dish count follows the default per headcount unless stated; dishes that break a joining person's rules are replaced with dishes of the same type, locked ones included; a person leaving removes unlocked dishes first, and dishes once avoided for them are not swapped back on their own; only when the current plan cannot be adjusted (for example most dishes break the new rules, or stock runs out) is a new plan made, keeping locked dishes where possible, and the reply says why. Also from the carryover's diner items (the settled set's meal-only rules, removing a person, "再叫几个朋友") as plan two's design decides.
