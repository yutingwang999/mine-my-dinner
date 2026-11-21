# Phase 1 Plan 3b-1b: Revision Fixes

Status: draft for approval (the user's answers to the builder check, 2026-11-21, are included). A follow-up to plan 3b-1 (`2026-11-19-phase-1-plan-3b1-revise-plan-design.md`), decided by the user on 2026-11-21 after plan 3b-1's evaluation round (round 9) missed its thresholds and after the user's own browser test. Plan 3b-1 and everything before it still hold unless this document changes them. The list of causes is in `docs/phase-1-carryover.md`, "Deferred from Phase 1 plan 3b-1" → "Plan 3b-1b". Next plans: 3b-2 (diner changes on an existing plan), then 3c (saving the plan and the shopping list).

## 1. Intent

Plan 3b-1 made a follow-up message change the current plan. In real use and in round 9 it often did the wrong thing: "不要X" was answered with a replacement the user did not want, a short one-meal wish ("今天不想吃肉") was taken as a lasting rule and threw the plan away, a general wish swapped a dish for one of the wrong type, a next-action button forgot the earlier request, and a new plan with a taste wish the library cannot meet ended with no plan and a misleading reason. 3b-1b fixes these so that revising holds up in real use, and makes what each model sees explicit and documented.

## 2. Goals, non-goals, anti-goals

**Goals**

1. A named removal follows the user's words: "不要 / 去掉" drops the dish and the plan gets smaller; "换 / 换成 / 来个 / 再要一道" replaces it with a dish of the same type (Section 5.1).
2. A message that only adds dishes adds them and changes nothing else.
3. A general wish replaces dishes with dishes of the same type and actually meets the wish when the library can.
4. A wish about this meal only ("今天不想吃肉", "too many tomatoes") stays a wish for this meal: no diner card, the revision goes on, hand edits are kept. Only a lasting rule ("我不吃肉", "我对虾过敏") goes to the diner card as today.
5. The next-action buttons change the request in code, so earlier conditions are never lost and no question is asked again.
6. A new plan whose taste wish the library cannot meet still gives a plan from the other conditions and says the wish was not met; it never ends with "0 dishes" as the reason.
7. The quote check accepts the short words people use for staples ("面", "饭").
8. The revise note never uses internal words ("锁定", LOCKED) and says only why the new dishes fit the wish.
9. A written, committed description of what each model sees, with complete examples.

**Non-goals**

- Diner changes on an existing plan and the parse seeing the diner card: 3b-2. This plan only stops one-meal wishes from being read as lasting rules.
- Saving plans, the shopping list: 3c.
- New intents (adding a recipe, updating stock).

**Anti-goals**

- A plan that shrinks or grows without the user asking.
- A locked dish (added by hand or kept by confirmation) removed by a general wish. (A dish the user's words name, a one-meal avoidance it breaks, or "the dishes that do not suit" pointing at its conflict may change it, and the reply says so: Section 5.4.)
- Tuning prompts on the new held-out sentences.
- A "fix" that hides a failed wish behind a plan that pretends to meet it: an unmet wish is always said.

## 3. Constraints

- Prompt versions are never edited once any record or evaluation run used them (`route_v3`, `parse_v6`, `revise_v2` are frozen). Expected new versions: `parse_v7` (Section 5.4), `revise_v3` (Sections 5.1-5.4, 5.7), `planner_v5` (Section 5.6, with a schema change to `PlanProposal`, so the planner's tools version changes too); `route_v4` only if the development runs show the route needs it.
- The user performs every Git operation; paid evaluation runs need the estimated cost and the user's approval first; evaluation and checks use the test database only.
- Nicknames never reach a model; "Diner N" is never shown (plan 2b).
- Hard rules stay in code: dish counts, locks, quotes, change size, dietary rules, stock, cookware.
- No database migration unless the builder finds one unavoidable (none is expected: the new fields live in the conversation state and in JSON records).

## 4. Approaches

**Who decides drop or replace, and who computes the counts (decided by the user, 2026-11-21: B).**

| Approach | Trade-off | Decision |
|---|---|---|
| A. The parse decides; it lowers the counts for a drop | The parse cannot see which dishes a scope covers ("荤菜都不要了" is how many?); the same class of error as round 9's relative-count miss | Rejected |
| B. The revise step marks each removed dish as drop or replace, and a new "add only" kind; code computes the revision's counts from the change list | Follows the user's table exactly and every rule is checkable by code; changes the `RevisionProposal` schema and needs `revise_v3` | **Chosen** |
| C. Code reads keywords ("换", "来个") | Brittle ("不要茄子了，整个清淡点"), and disagrees with the model's reading | Rejected |

**How a one-meal wish is told from a lasting rule (decided by the user, 2026-11-21: A).**

| Approach | Trade-off | Decision |
|---|---|---|
| A. `parse_v7` separates "for this meal" from "lasting": one-meal avoidances go to a new request field, lasting rules stay `self_rules` / `friend_rules` | Needs a parse version and an evaluation; keeps lasting rules working everywhere | **Chosen** |
| B. On a revise turn code ignores every new rule (no card) | Simple, but a lasting rule stated while revising ("我最近对虾过敏") would never reach the card or the profile; against plan 3b-1's design | Rejected |

Everything else has one sensible way and follows the user's decisions in Section 5.

## 5. Decisions

### 5.1 Drop or replace (the user's table, 2026-11-21)

The principle: **if the words want another dish in its place ("换 / 换成 / 换一个 / 来个 / 再要一道"), replace it with a dish of the same type; if they only say not to have it, drop it and the plan gets smaller.**

| The user says (current plan: 2 meat, 1 vegetable, 1 soup) | Result |
|---|---|
| "茄子这道不要" / "去掉茄子" / "茄子不要了" | Drop: 3 dishes left |
| "茄子这道不要，换一个别的" / "茄子换成别的" / "把茄子换掉" | Replace with a dish of the same type: still 4 |
| "茄子不要，来个辣的" | Replace with a spicy dish of the same type: still 4 |
| "汤不要了" (one soup in the plan) | Drop: 3 dishes, 0 soups |
| "那道汤不要了" (two soups in the plan) | Ask which one; nothing changes |
| "荤菜都不要了" | Drop every meat dish |
| "不要这么多鸡蛋的" | A general wish: replace, never drop; at most half of the unlocked dishes |
| A named dish that was added by hand or kept by confirmation | Named, so it is dropped or replaced as above |
| A message that would drop every dish ("全都不要了") | Nothing changes; the reply (plain text, no card) asks whether to plan a new meal. There is no "skip this meal" feature, and none is added (Claude's call) |

- The change list (`RevisionProposal`) marks each removed dish as `drop` or `replace`, and gains a kind for a message that only adds ("再加一道", "加个青菜"). Code checks: a drop under `named` or `scope` needs the quote as today; an add-only answer removes nothing; a replacement is the same dish type as the dish it replaces; a change list that leaves no dish is refused and answered with the question above.
- The revision's counts are computed by code (approach B): when the message states a number (`counts_stated_now`), the stated counts win; otherwise the counts are the current plan's own counts minus the dropped dishes (by dish type) plus the added ones. The parse no longer guesses a count change for a removal. This replaces plan 3b-1's rule that a named removal without a number keeps the plan's size.
- A general wish never drops and never removes a locked dish (unchanged from 3b-1).

### 5.2 General wishes and same-type replacement

- A replacement is always the same dish type as the dish it replaces, unless the message states a count change.
- When a count check fails on a revision, the retry message names the concrete dishes of the right type that may be replaced ("replace one of these meat dishes: 清蒸鲈鱼 (id 12), 土豆炖牛腩 (id 18)"), not only which side is off.
- When the wish fits no dish of the type that could be replaced, the answer is "nothing fits" with the reason (3b-1's nothing-fits path), not a swap that ignores the wish.
- An asked-for dish that is not in the library ("换成西红柿炒鸡蛋" when there is none) is answered as nothing fits, saying the dish is not in the library and offering import, without a search loop.

### 5.3 The quote check

- Staple foods get category words, as soups have "汤": at least "面", "饭", "粥", "粉", and their English words. A one-character quote is accepted when it is the dish's category word or the last character of its title.
- The other quote rules stay (the quote must be in the message, and must name the dish).

### 5.4 One-meal wishes versus lasting rules (`parse_v7`)

- The parse separates "for this meal" from "lasting". "今天不想吃肉", "这顿不要辣的", "too many tomatoes" are about this meal; "我不吃肉", "我对虾过敏", "小王最近不吃羊肉" are lasting. Only lasting rules go into `self_rules` / `friend_rules` and can bring the diner card back.
- A one-meal avoidance goes into a new request field (name left to the builder, for example `meal_avoid`). It holds **ingredient names or ingredient groups only** ("肉", "西红柿", "虾"), never taste words: the library has no taste tags, so code could not enforce "不要辣的" (Claude's call). Code applies the field to this meal as a hard exclusion for the search and the checks, the same way a meal-only rule is applied, without touching any profile.
- Taste words for this meal ("这顿不要辣的", "清淡点") and remarks such as "西红柿太多了" are a general wish (the query), not an exclusion.
- **On a revise turn, dishes that break a one-meal avoidance are replaced, and the plan keeps its size (decided by the user, 2026-11-21).** "今天不想吃肉" on 2 meat, 1 vegetable and 1 soup replaces both meat dishes with dishes that have no meat; here a replacement may be **another dish type** (a vegetable dish or a soup for a meat dish), an exception to Section 5.2's same-type rule, and the revision's counts by type follow the replacements while the total stays. An avoidance is not a bare removal, so Section 5.1's "drop" does not apply to it.
- **An avoidance, or words that point at the dishes that do not suit ("不适合的菜拿掉"), name the dishes they cover, locked dishes included (decided by the user, 2026-11-21).** A hand-added or confirmed-keep dish that breaks the avoidance, or that has the conflict the words point at, is replaced or removed like any named dish, and the reply names every locked dish it changed ("红烧肉是你手动加的，因为今天不吃肉，换成了……"). A general wish still never touches a locked dish.
- Unchanged: a lasting rule stated in a follow-up still shows the diner card, and the turn plans anew, until 3b-2.

### 5.5 Next-action buttons in code

- A button message no longer goes through the parse. **Code takes the request of the turn whose card offered the button** (the newest card; buttons appear only there) and changes one field (decided by the user, 2026-11-21). On a failed revision's card that is the revision's request (the current plan's request with that turn's changes, for example "想吃点海鲜" kept), so the button keeps the wish it was offered for. The field changes are:
  - `switch_to_shopping`: mode becomes shopping.
  - `fewer_dishes`: the dish total goes down by one, never below one; a named meat or vegetable side above the new total is lowered with it (Claude's call, the user agreed to "one fewer").
  - `drop_time_limit`: the time limit is cleared.
  - `relax_split`: the meat and vegetable sides and their bounds are cleared; the total stays.
  - `relax_named`: `must_use` and `all_use` move into `prefer_use`.
- **What runs next depends on the card (decided by the user, 2026-11-21):**
  - On a failed revision's card (the plan was kept): the revision runs again on the current plan with the changed request, so hand edits and locks are kept ("允许购物" after "想吃点海鲜" looks for a seafood dish to buy for, in place of a dish of the plan).
  - On a new plan's card (a no-plan, early-stop, or failed new plan): the meal is planned from scratch with the changed request, as today.
  - No route call, no parse call, no ask-first question in either case (the request is complete).
- The request a card was built from must therefore be kept per card (for example on the card or by reply id), not only as the thread's latest `request`; the builder chooses where. A thread saved before this plan without it falls back to today's behaviour (parse the message).

### 5.6 A new plan with a taste wish the library cannot meet

- **Only the planner can tell whether a dish fits a taste wish**, because the library has no taste tags. `planner_v5` adds to `PlanProposal` whether the stated wish was met and, when not, a short reason (Claude's call; a schema change, so the planner's tools version changes). With no wish in the request the field is not used.
- When the planner says the wish was not met, the meal is still planned from the other conditions (count, mode, rules, stock), and the reply says that the wish was not met, with next actions that fit (import recipes; allow shopping when fridge-only). The planner is told to answer with such a plan instead of searching on.
- When an attempt ends with no answer (the planner hit its call limit), code falls back to a plan built from the shortlist's makeable dishes (the same checks apply) and says the wish was not met. An empty plan never fails H4 as "0 dishes"; that text is never shown for a missing answer.
- Hard requirements that cannot be met (counts, rules, stock) still stop with reasons, as today.
- This closes the new-plan half of the carryover item "An unmet taste wish is not reported".

### 5.7 The revise note

- `revise_v3` tells the note never to use internal words ("锁定", LOCKED, OPEN CONFLICT, ids) and to say only why the new dishes fit the wish, in one or two sentences. The code-written sentences ("把A换成了B", "…是你手动加的") stay as they are. There is no rule about counting dishes (the user's decision).

### 5.8 What each model sees (a written deliverable)

A new document `docs/planner-what-each-model-sees.md`, in English (decided by the user, 2026-11-21: its own document, because it covers four models, not only the request and counts), with a Chinese copy at `docs/private/planner-what-each-model-sees.zh.md`. The other planner documents (`planner-request-and-counts.md`, `planner-shortlist-and-ranking.md`, `planner-feasibility-check.md`, `planner-stock-rules.md`) get a one-line pointer to it. Whenever a model's input changes, this document is updated in the same change. For each model: what it receives, what it never receives, why, and one complete example input. The design reasons:

1. **Hard rules are code's job.** Counts, dietary rules, stock and cookware are checked by code, so a model needs to know what to meet, not to derive it from raw words.
2. **A request is read once.** Only the parse reads the user's words and turns them into a structured request; later steps use that request, so two models cannot read "两三个菜" two ways.
3. **Privacy.** Saved nicknames never reach a model; nothing on the diner card is sent; diners appear only as anonymous "Diner N" rule lines.
4. **Cost and stability.** Shorter inputs cost less, and an opening that is the same on every attempt of a run is served from the provider's prompt cache.

| Model | Receives | Never receives | Why |
|---|---|---|---|
| Route (`route_v3`) | The route prompt and the whole conversation: every user message and every assistant reply's text | Cards (plan, diner, choice), the assistant's questions | Telling "change this plan" from "plan again" needs context; the answer is one word, so the whole conversation is cheap |
| Parse (`parse_v6`, then `parse_v7`) | The parse prompt, the whole conversation with the assistant's questions placed as assistant messages, and on a revise turn the app note line describing the current plan | The diner card's contents, the rest of the plan card | It merges several turns into one full request; questions let a short answer ("两个") attach to its question; the plan line lets "再加一道" count from the plan; the card is kept from models for privacy (plan 2b) |
| Planner (`planner_v4`, then `planner_v5`) | The planner prompt, one opening message (the shortlist, then the code-written request summary with anonymous diner rule lines), a retry message after a failed attempt, and its own tool calls and results within the attempt | The conversation and the user's words (except the query phrase in the summary) | The request is already read and completed; raw words could pull it away from what code checks; the fixed opening is cached across attempts |
| Revise (`revise_v2`, then `revise_v3`) | The revise prompt, an opening with the same shortlist and summary under "Revise this meal.", the current plan one dish per line (marked LOCKED or OPEN CONFLICT), the user's words of this turn, a retry message, and its own tool calls | Earlier turns | It must see the plan it changes and the exact words it quotes (code checks quotes against them); earlier conditions are in the summary |

Every model: saved nicknames are replaced by "Diner N" labels before the call and restored in what the user sees; the user is shown as "你" / "you".

The section shows one complete example per model, built from fictional dishes of `library_revise.yaml` (never from the user's library or the held-out sentences), for one short conversation: "今晚两个人，三个菜，可以买菜" → a plan → "茄子这道不要". For example, the parse input of the second turn ends:

```
[human] 今晚两个人，三个菜，可以买菜
[ai] 先炖土豆炖牛腩，再快炒……
[human] 茄子这道不要
[human] [App note, not written by the user] Current plan: 3 dishes (2 meat, 1 vegetable): 青椒肉丝, 土豆炖牛腩, 红烧茄子
```

and the revise opening ends:

```
Revise this meal.
Reply locale: zh
Mode: shopping
Headcount: 2
Dishes (meat plus vegetable): 2
...
Diners' dietary limits:
- none recorded

Current plan:
- 31 青椒肉丝 (meat dish; 青椒, 猪肉)
- 18 土豆炖牛腩 (meat dish; 土豆, 牛肉)
- 7 红烧茄子 (vegetable dish; 茄子) LOCKED

The user's message: 茄子这道不要
```

(The exact text comes from the code at the time; the builder generates the examples from the code rather than copying these.)

### 5.9 Evaluation

- **A new held-out batch** from the user, 15-20 sentences, written after this design and the plan are approved and before any implementation. Suggested kinds: bare drops ("不要 / 去掉"), replaces ("换一个"), one-character and staple quotes, one-meal wishes in Chinese and English, general wishes, add-only, buttons after a failed turn, a new plan with an unmeetable taste wish, new plans and ambiguous messages.
- **The 3b-1 held-out set becomes a regression set**: run every round, not counted in the held-out threshold, misses explained. As a regression set it is no longer secret and may be read (Claude's call). In the plan's first task, together with the new held-out batch, Claude lists its scenarios whose expectations this design changes (for example a drop the old expectation replaced, or the reverse) and proposes the new expectations; the user approves each change before the file is edited. The builder never changes an expectation without the user's approval.
- The development set gains scenarios for every decision above, written by Claude with new sentences (never held-out ones). `choice_card_answered_revise`'s sentence, which is now a `revise_v2` example, is replaced with a sentence no prompt uses.
- **A table of all scenario sets** at the top of `docs/planning-evaluation-history.md`: file, library, size, author, what it tests, its role (development: may tune; regression: run every round; held-out: never tune), and when its role changed.
- New runner checks where needed: a reply never shows "0 dishes" as the reason for a missing answer; a wish that was not met is said; after a button the request keeps the earlier conditions and no question is asked.

## 6. Success criteria (thresholds, set before the round; the user's approval, 2026-11-21)

| Threshold | Required |
|---|---|
| Development set as expected | ≥ 90% |
| New held-out set as expected | ≥ 80% (scenarios with `deferred_to` reported apart) |
| 3b-1 held-out set (now regression) | ≥ 80%, every miss explained |
| Locked dishes wrongly removed | 0 over every run |
| Model calls per revise turn | average ≤ 4, no turn over 7 |
| Existing new-plan sets (baseline, plan2a, counts, diners, held-out core and diners) | no regression |
| "0 dishes" shown for a missing answer; an unmet taste wish not said | 0 in every scenario |
| After a next-action button: earlier conditions kept, no question asked again | every such scenario as expected |

If the round misses a threshold, it is recorded as it is and the user decides, as in plan 3b-1.

## 7. Records

- New prompt versions are listed in `docs/prompt-versions.md`; the evaluation round goes to `docs/planning-evaluation-history.md`; deferred findings go to `docs/phase-1-carryover.md` under "Deferred from Phase 1 plan 3b-1b"; closed items in the "Plan 3b-1b" block are marked closed.
- A button turn's planning record notes that the request came from code (for example `request["button"] = "<action>"`), so it can be told apart.

## 8. Also in this plan (from the carryover, small)

- The plan 3a item "The quiet count note reads the newest parse" (retagged to 3b-1b): decide the stated counts from the card's own run (`plan_request`), now that it exists.
- The final-review leftovers tagged 3b-1b in the carryover (a failed card still showing a dish that left the library; a side count restated after a hand trim being ignored) are fixed if they fall out of the work above; otherwise they stay in the carryover.

## 9. Left to the builder

- The schema details of the change list (field names for drop / replace and for the add-only kind) and of the one-meal field.
- How code chooses the fallback dishes for a new plan with no answer (the order of the shortlist is a reasonable start).
- The exact category words for staples.
- The wording of the new reply sentences (both languages) and of the "drop every dish" question.
- The examples in `parse_v7` and `revise_v3` (never held-out sentences).
- Whether `route_v4` is needed (only if the development runs show it).
