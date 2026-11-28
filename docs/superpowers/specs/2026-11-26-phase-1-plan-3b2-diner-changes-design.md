# Phase 1 plan 3b-2 (two): diner changes on an existing plan — design

Status: draft for approval. Parent design: `docs/superpowers/specs/2026-11-22-phase-1-plan-3b2-revise-simplification-design.md` (cited as "the 3b-2 design"); its Section 9 outlined this plan. The change-fixes plan (`2026-11-24-phase-1-plan-3b2-change-fixes-design.md`) and everything before it still hold unless this document changes them.

## 1. Intent

Today, when a follow-up on a plan changes who eats ("小李今晚也来", "小王不来了", "就我自己吃"), the reader reads it as `people`, the parse runs, the diner card shows, and the meal is **planned from scratch**: hand edits, locked dishes and the current dishes are lost. The diner card itself is built from this message's parse alone, ignoring the diner set already confirmed in the thread, which is the source of most open diner items in the carryover (a solo-shaped card that drops a guest, meal-only rules lost on a new card, "小王不来了" not removing anyone, "再叫几个朋友" not asking).

The user wants a change in who eats to **adjust the current plan** (user decision, 2026-11-22), always confirmed through the diner card, and predictable in the sense of the 3b-2 design: the app never does something surprising or wrong.

## 2. Goals, non-goals, anti-goals

**Goals**

1. A follow-up that changes who eats shows a diner card that **starts from the confirmed set** and adds what the message names; after the user confirms it, code adjusts the current plan instead of planning anew.
2. Dish counts move by the difference between the default counts of the old and the new headcount (Section 5.3), unless the message states counts.
3. Dishes that break a rule of the confirmed set are replaced, locked ones included; a person leaving lowers the count by the same difference. Nothing restores a dish that an earlier turn replaced for them: the plan stays as it is unless the user asks (Section 5.5).
4. The plan is made again only when the facts say most of it must change (Section 5.4); code decides, never a model.
5. `follow_up_v5` closes the reader's carryover items: a fixed dish type for adds, lifting a meat/vegetable split by message, taking back a must-use ingredient, and the reader sees who is eating.
6. Every branch kept only for threads, cards or records saved by earlier versions is removed, backend and frontend (Section 5.9), before the diner-change work starts.

**Non-goals**

- The first turn of a thread: route, `parse_v8`, the first-turn diner card and `planner_v5` are unchanged.
- A model reading who joins or leaves (approach A, rejected): the card is where the user says it (Section 4).
- Showing the confirmed set's meal-only rules on the plan card's diners line (→ T1.9b).
- Taste preferences remembered across conversations, category rules ("吃素" as a rule) (→ T1.6).
- What "肉" covers in a one-meal avoidance (still open for the user).
- Prompt trims of `parse_v8` and `planner_v5`.

**Anti-goals**

- A person, a guest, or a meal-only rule of the confirmed set lost because the user confirmed a follow-up card without touching it.
- A dish that breaks a confirmed diner's rule kept in the plan (except one kept against that rule by the user's own confirmation, as today).
- A locked dish removed for anything but a rule it breaks or the user's words pointing at it.
- The plan made again from scratch, or hand edits dropped, when the user did not ask for a fresh meal and the facts do not call for it.
- Code interpreting the user's words (the 3b-2 design 5.4 principle). Every decision in this plan is a fact code holds (the confirmed card, the plan, the rules, the counts) or a model's reading.

## 3. Constraints

- Prompt versions any run used are never edited: the reader's change is `follow_up_v5`; `follow_up_v4`, `pick_v6`, `parse_v8`, `route_v6`, `planner_v5` stay as they are. `pick_v6` and `parse_v8` are not changed by this plan.
- The reader prompt stays within the 3b-2 design 5.8a budget: under half of `route_v6` + `revise_v4` (about 6,900 characters); `follow_up_v4` is 5,788. `docs/prompt-versions.md` records its length.
  - Amendment 2026-11-29 (user decision): from `follow_up_v9` the reader's budget is 8,000 characters (applies to a later reader version; this plan ships follow_up_v7), since the half budget made `follow_up_v7` and `follow_up_v8` trim useful cues; `follow_up_v9` is 7,953; the reader-plus-picker bound (under `route_v6` + `revise_v4`, 12,817) no longer applies (user decision 2026-11-29).
- Saved nicknames never reach a model; "Diner N" is never shown to the user; the card reaches the client through `client_card` without server-only fields.
- A change turn makes at most 7 model calls, the parse on a people turn included.
- Held-out scenario files are the user's: no expectation in them changes without the user's approval; no held-out sentence appears in a prompt, a test, a doc or a development scenario.
- Paid runs need a cost estimate and the user's approval, and use the test database only.
- Whenever a model's input changes, `docs/planner-what-each-model-sees.md` and its Chinese copy in `docs/private/` change in the same commit; `docs/planner-chat-flow.md` and its copy likewise for the flow.
- The user performs every Git operation.

## 4. Approaches

**Who reads who joins or leaves** (decided by the user, 2026-11-26: D).

| Approach | Trade-off | Decision |
|---|---|---|
| A. `follow_up_v5` reads who joins, who leaves and "only me" into a structure; `parse_v8` reads the rules; code builds the card from both | The card is prefilled for leaving too; but the reader grows by 600-900 characters and two models read people from one message and can disagree | Rejected |
| B. `follow_up_v5` reads everything, rules included | One reading; but over the reader's size budget, and the allergy extraction proven in `parse_v8` moves into an unproven prompt | Rejected |
| C. The parse reads people with the confirmed set in its input (`parse_v9`) | The reader barely changes; but the first-turn prompt changes, every new-plan set becomes a regression check, and the parse grows past 15,000 characters | Rejected |
| **D. The card confirms it.** The reader says only that the message is `people` (plus the message's other changes); `parse_v8`, unchanged, finds the people and rules the message names, as on a first turn; the card starts from the confirmed set with those added; leaving is done by the user on the card | Simplest; no second reading of who eats; every change in who eats is the user's own act on the card. A person leaving must be unticked by hand; forgetting it errs on the safe side (their rules stay, a dish too many) | **Chosen** |

**When the plan is made again** (decided by the user, 2026-11-26): code decides from facts (Section 5.4). Letting a model decide was considered and rejected: the same situation could adjust once and start over the next time, a wrong "start over" loses the dishes the user liked, and it is a judgement about the plan, not about the user's words. The user's own words still decide when they ask for it ("干脆重新配一桌"): that is `new_meal`.

**Counts after a change in headcount** (decided by the user, 2026-11-26: A, the difference of the defaults). Rejected: B, reset to the new headcount's default (loses a count the user stated); C, keep any stated count (adds no dish for four more people).

**How a plan is made again** (Claude's call, Section 5.4): through the change path, every unlocked dish becoming a replace slot, not through the planner. The planner has no notion of locked dishes, so keeping them would need a new planner version and a rerun of every new-plan set.

## 5. Decisions

### 5.1 The turn, end to end

When the thread has a current plan with dishes and the reader reads the message as `people`:

1. **Reader** (`follow_up_v5`, model call 1): `kind: people`, plus this message's other changes in the usual `change` fields (remove, add, wish, meal_avoid, counts, conditions), so "我妹妹今晚也来，再加个汤" keeps its soup. Under `follow_up_v4` a people reading carried nothing else.
2. **Parse** (`parse_v8`, model call 2), unchanged prompt and input, as a people turn runs it today (the plan line appended, `carry_unstated`): it finds the people, guests, saved friends, rules and stated headcount the message names.
3. **Diner card** (⏸), always shown on a people reading, in its follow-up shape (Section 5.2). Carryover 3b-2 (one) Task 6 (6) ("`people` always shows the card, even when the parse finds no change") is closed as won't-do: under D the reader does not say what changed, and the card is the confirmation.
4. **After the card**: code applies the answer as today (`apply_answer`), giving a new confirmed set, the new headcount and the new constraints.
5. **Adjust** (Section 5.3-5.5): the change path (`build_change`, the frame, the picker, `present_change`) runs on the current plan with the new constraints, the count difference and the reading's other changes. Model calls 3 and later are the picker's; the turn's budget of 7 counts the reader and the parse.

A message that asks for a fresh meal is `new_meal` even when it also changes who eats ("小李也来，干脆重新配吧"): `follow_up_v5` says a fresh start wins over `people`. That path is today's (parse, diner card when `card_needed`, planner), except that the card takes the follow-up shape whenever a set is confirmed (Section 5.2).

A lasting rule about someone already eating ("我先生以后都不吃羊肉") is `people` too: the card shows (saving a rule to a profile is chosen on the card), and the plan is then adjusted, not planned anew.

**Skipped card on a follow-up** (Claude's call): the confirmed set stays exactly as it was; the reading's other changes still run through the change path, and the reply says who eats did not change. (A skipped first-turn card keeps today's behaviour.)

**A thread with no confirmed set** (Claude's call after the builder check): a solo first turn that showed no card or a skipped first-turn card has no `settled_diners`. For a people follow-up on such a thread, code takes the plan's own facts as the confirmed set: the user alone, plus `headcount − 1` unconfirmed others (the plan's constraints headcount). The follow-up card starts from that, `old` in Section 5.3 is that headcount, and the turn adjusts like any other. So "小李今晚也来" after a solo plan adjusts the plan, never plans anew.

### 5.2 The follow-up card

Built by code from the confirmed set plus what the parse found; no model call. When the thread has a confirmed set, every card after the first (people, and `new_meal` when `card_needed`) takes this shape:

- **Rows:** the user and every saved friend, as today's non-solo card (never the solo shape on a follow-up). Preselected: every diner of the confirmed set, plus the friends the parse matched in this message.
- **Rules:** each row's saved rules as today; the confirmed set's meal-only rules of that diner appear as already chosen for this meal (not saved to the profile); rules the parse found in this message are proposed as today.
- **Guests:** every guest of the confirmed set, with their name and meal-only rules, followed by the guests the parse found that are not already in the set (matched by name, `guest_key`), with their proposed rules.
- **Total:** the headcount the parse states, when it states one; otherwise the confirmed headcount plus the people this message adds (new guests and newly matched friends). Unmatched people ("others") make up the rest, as today. "States one" means the parse's own answer, before `carry_unstated` fills the headcount from the plan's request (otherwise "小李也来" would get the old total).
- **A stated total below the people preselected** (decided by the user, 2026-11-26: b). For example "就我自己吃" (stated 1) on a confirmed set of three. The total is never set below the people preselected on the card, so nobody leaves without the user's own act, and the card shows a notice in the card's language: the message said N people, M are ticked, untick the ones not eating. Code never unticks anyone from a parsed number (rejected: that lets a model's reading remove a person, the A-I1 risk). The card's confirm stays enabled; the API's `headcount_too_low` refusal still guards the answer.
- **The invariant** (tested): confirming a follow-up card without touching it gives back the confirmed set plus exactly what the message added; no person, guest, meal-only rule or unmatched person of the set is lost.

Leaving is done by the user: unticking a friend, removing a guest, or lowering the total. This closes, by construction, the carryover items "`build_card` ignores the settled set's meal-only rules", "the card for '就我自己吃' lists only the user", the known risk A-I1 (a solo card that drops an added guest), "小王不来了" and "再叫几个朋友" (each now shows a card starting from the set).

The frontend renders the follow-up card with the fields the card already has where it can; where a confirmed meal-only rule or a confirmed guest cannot be shown as preselected with today's fields, the builder adds the field, and `client_card` keeps server-only fields out.

### 5.3 Counts after the card

Let `old` be the confirmed headcount before the card and `new` the headcount after it, and `default_composition(h)` the default (dishes, soups) for headcount `h` (1 → 1+0, 2 → 2+0, 3-4 → 2+1, 5+ → 3+1).

- The plan's dish total (meat and vegetable dishes) moves by `default(new).dishes − default(old).dishes`, never below 1; its soups by `default(new).soups − default(old).soups`, never below 0. Staples, desserts and snacks do not move. The split between meat and vegetable dishes is not stated: an added dish is a meat or vegetable dish of the picker's choice (an untyped add), and a dish to drop is chosen by the picker among the unlocked meat and vegetable dishes.
- **When the message itself asks for a count change, the difference is not applied at all** (decided by the user, 2026-11-26: option 丙, after the builder check). If the reading has any `add` or any stated `counts`, the counts follow only the message, as on any change turn ("我妹妹今晚也来，再加个汤" on a plan for 2: one soup, not two; "小李也来，加个肉菜" from 4 to 5 people: one meat dish more, and no soup added by the difference). Rejected: adding both (two soups), and taking the larger of the two per type. A removal in the reading does not turn the difference off.
- The moved counts bind like stated counts for this turn: code checks the result against them.
- Equal defaults (for example 3 → 4 people, or only a rule changed): no count changes.
- Example: 2 people with a stated 4 dishes, 2 more join: 2+0 → 2+1, so 4 dishes and 1 soup.

### 5.4 Adjust, or make it again

After the card, code counts the dishes of the current plan that **must change**: those that break a rule of the new confirmed set (H2, overrides carried as today, so a dish the user kept against a rule stays) plus the dishes the count difference drops. Locked dishes count when they break a rule.

- **At most half of the plan's dishes** (all types) must change: **adjust** (Section 5.5).
- **More than half**: **make it again**. Every unlocked dish becomes a replace slot (the picker keeps each slot's kind, as `pick_v6` does), the counts are those of Section 5.3, locked dishes that break no rule stay, and the reply says why the plan was made again. This runs on the change path with the same picker, checks and fallbacks; no planner call.
- Slots that cannot be filled follow the existing rules in either case: a rule-breaking dish with no replacement is removed and the reply says so; the card offers next actions for the parts not done. A plan is never sent to the planner because a slot could not be filled (the planner uses the same library).

### 5.5 The frame for a diner change

`build_change` runs with:

- the **new constraints** from the confirmed set (with this meal's avoidance from the reading, as today);
- the **request**: the plan's request after the reading (`request_after`), with the new headcount and the moved counts of Section 5.3;
- the **reading's** own changes, applied as on any change turn.

The frame's existing steps then do the work: dishes that break the new rules become replace slots (locked ones too, step 2); the moved counts add untyped add slots or a "drop exactly N" choice among unlocked dishes (step 5). A dish that was replaced earlier because of a person who has now left is not swapped back. No step of the frame does so, and none is added; this needs no guard. The picker may still choose that dish for a slot that this turn opens for another reason. That is allowed, because the person it was avoided for has left.

### 5.6 The reply

Code's sentences lead, before the usual change sentences:

- who-eats line, a fixed sentence: the headcount change ("现在是 4 个人吃") and, when counts moved, what moved ("所以加了一道汤");
- when dishes were replaced for a new person's rule, the existing replaced-dish sentence names the reason as a diner rule (no names of people in the sentence beyond what the card showed);
- when the plan was made again (5.4), one fixed sentence saying most dishes had to change for the new diners, so the plan was made again, keeping the dishes added by hand;
- when the card was skipped, one fixed sentence that who eats did not change.

Exact wording is the builder's (both languages). Small leftover taken along: the capped-targets sentence is said only when the plan changed (carryover change fixes, Task 3 review (2)).

### 5.7 `follow_up_v5`

Changes, each stated as a principle or a schema field, not a boundary patch:

1. **People and the rest of the message.** A `people` reading also fills the change fields for the message's other requests; they are applied after the diners are confirmed.
2. **A fresh start wins.** A message that asks for a new meal is `new_meal`, even when it also changes who eats.
3. **Add types are fixed** (carryover "Reader and route variance", (1)): `ReadAdd.dish_type` becomes an enum of the six dish types or null. A schema change: the reader's tools version changes.
4. **Lifting a split** (held-out `any_mix_typed`, carryover round 11): `ReadConditions` gains a flag for "meat and vegetable in any mix"; code applies it as the `relax_split` button does (the sides cleared, the total kept).
5. **Taking back a must-use ingredient** (carryover 3b-2 (one) Task 2 (2) and 3b-1 "A revision cannot clear a condition"): `ReadConditions` gains the names no longer required; code removes them from `must_use` (and `all_use`).
6. **Who is eating, in the input.** The reader's `Diners:` part lists the confirmed set: Diner 1 (the user) and each other diner by label with their rules, guests by label marked as this meal only, the number of unnamed people, and the headcount. This closes, for follow-ups, the carryover root cause "the parse never sees the diner card".
7. **Size:** within the budget of Section 3; the examples are invented, few, and never from a held-out set.

Not changed by principle patches: the round-12 reader variance and the final review's watch items stay watch items for this plan's evaluation rounds; the two-layer expectations accept the reasonable readings.

### 5.8 Records

- A people turn's planning run holds the reader and parse `model_call` events, the card answer (as the diner card records it today), a `diner_change` event (old and new headcount, the count difference, the must-change count, adjust or made again), then the usual `frame`, `pick_attempt` and `change_outcome` events.
- `PROMPT_VERSIONS` takes `follow_up_v5`; `docs/prompt-versions.md` lists it with its length.

### 5.9 Old threads and compatibility branches (decided by the user, 2026-11-26)

**Done before this plan.** A read-only count of the real database found 20 saved threads, all from 2026-11-14 to 2026-11-22. The threads that would still reach a compatibility branch were: 11 whose newest card has no `request`, 14 with a plan but no `plan_request`, and 6 with a `revision` state. None was paused on a question. With the user's approval, all checkpoints and their planning runs were backed up to `docs/private/backups/` (git-ignored) and deleted. `checkpoint_migrations` and every other table were kept. No thread saved before this plan exists.

**The first task of this plan removes every compatibility branch**, with its tests. Then the diner-change work builds on code that has one shape of thread:

- Backend flow:
  - `read_paused_turn` and its graph edge;
  - the parse path for a button on a card without `request`;
  - `words_for_picker`, `wish_choice` and the frame's legacy wish choice;
  - the part of `_offering_words` for a card without `wish_words`;
  - `revision_words`' `earlier` merge;
  - the `revision` state, and the note that `proposal` may hold a revise step's dump;
  - the `previous_request` fallback for `plan_request`, and `previous_request` itself if nothing else reads it.
- Backend data:
  - the `meal_avoid` fallbacks (`current_meal_avoid` / `revision_meal_avoid`);
  - a card without `locked`;
  - an H4 finding without ranges;
  - `split_is_minimum`;
  - a settled set without `stated_headcount`;
  - a thread without a label table;
  - notes without `left_out_details`.
- Frontend:
  - the `revise_or_plan` history line;
  - threads before per-reply plans;
  - defaults filled in for old plan fields (`self`, `edited`, `composition` in `defaults_used`);
  - `split_exceeds_total`;
  - the missing diners line before plan 2b;
  - left-out titles without ingredients.
- Module names: `agent/revise_step.py` and `agent/revision.py` get names that say what they hold now (carryover).
- Docs: `docs/planner-chat-flow.md` (and its Chinese copy) loses the older-thread paths.

**Not removed:**
- Frozen prompt files. They are kept by the versioning rule.
- Fields that are optional by design today, meaning a current writer may leave them out. The builder checks each one before removing it.
- Evaluation code that reads older planning runs in the test database, when an evaluation round still needs it.

**The rule from now on:** a change that leaves saved threads unreadable adds no compatibility branch. The plan that makes it says so; the user's threads in the real database are backed up and cleared before it is merged, as here. Every backup goes to `docs/private/backups/`.

## 6. Evaluation and success criteria

**Development** (Claude writes, in the development set or a new development file): a friend joins with a rule that breaks a dish (locked and unlocked); a guest joins with no rule and the count moves; a person leaves (the runner unticks on the card); "就我自己吃" after a shared set; a total raised on the card; a join plus another change in the same message; a join that breaks more than half the plan (made again); a skipped follow-up card; a join after a solo plan that showed no card; a stated total below the people on the card (the notice shows, nobody is unticked by code); a join plus an add in one message (the difference not applied); a lasting rule about someone already eating; a fresh start with a join (`new_meal`); "再叫几个朋友"; the reader items of 5.7 (a soup add, lifting a split, taking back a must-use). The runner gains follow-up card answers: confirm as shown, untick a person or remove a guest, set the total (the `headcount` key, closing that T1.10 runner gap).

**Must never be wrong** (added to the runner's checks, every scenario): no dish in the result breaks a rule of the confirmed set unless the user kept it against that rule; a follow-up card confirmed as shown loses nobody and no meal-only rule of the set; a locked dish leaves only for a rule or the user's words; the result meets the counts of Section 5.3 (or the stated ones) or the reply says why not.

**Sets in the round:** the development set; the three revise regression sets (`friend_joins_and_two_more_dishes` stops being deferred, with the user's approval of any expectation change); every older new-plan and diners set (the first turn is unchanged, so no regression); a **new held-out batch written by the user** after this design and the plan are approved (Claude says then how many sentences and which cases).

**Thresholds** (proposed, as round 13's plus the new ones):

| Criterion | Required |
|---|---|
| Must never be wrong | 0 over every run |
| Development set | ≥ 90% |
| New held-out batch | ≥ 80% |
| Revise regression sets | no fewer as expected than round 13 (heldout3 14/15, heldout 17/17, heldout2 19/20); every miss explained |
| Calls per change turn (people turns included) | average ≤ 2.5 on change turns, ≤ 3.5 on people turns; no turn over 7 |
| Unnecessary questions | ≤ 20% |
| Older new-plan and diners sets | no regression from round 13 |
| Reader prompt size | within the 5.8a budget |

Each round is appended to `docs/planning-evaluation-history.md`; deferred findings go to a new "Deferred from Phase 1 plan 3b-2 (two)" section of `docs/phase-1-carryover.md`, and the items this plan closes are marked `Done` with a status line.

## 7. Carryover items this plan takes

| Item | Outcome here |
|---|---|
| Plan 3b-1: Diner changes on an existing plan | Done (5.1-5.5) |
| Plan 2b: Settled diners and later changes — root cause, A-I1, meal-only rules on a new card, "再叫几个朋友", "就我自己吃", "小王不来了" | Done for follow-ups (5.2, 5.7 item 6) |
| Plan 2b: meal-only rules shown nowhere | The card shows them; the plan card's line → T1.9b |
| Plan 2b: a skipped card leaves no settled set | Follow-ups keep the set (5.1); the first turn unchanged, stays open |
| 3b-2 (one) Task 6 (6): people always shows the card | Won't do (5.1) |
| Round 11: lifting a split by message | `follow_up_v5` (5.7 item 4) |
| Change fixes: reader `dish_type` free string | `follow_up_v5` (5.7 item 3) |
| 3b-2 (one) Task 2 (2) and 3b-1: clearing must-use | `follow_up_v5` (5.7 item 5) |
| Change fixes: reader readings in round 12; watch items from the final review | Watched in this plan's rounds |
| Change fixes Task 3 (2): the capped sentence on a kept plan | Done (5.6) |
| Final review M1: an older paused turn can reach 8 calls | Done: `read_paused_turn` removed (5.9) |
| Renaming `revise_step.py` / `revision.py` | Done (5.9) |

## 8. Left to the builder

- Field names: the split-lifting flag, the must-use withdrawal field, the `diner_change` event.
- The exact layout of the reader's `Diners:` part, within 5.7 item 6.
- How `build_card` is split between the first-turn and follow-up shapes, and the frontend fields needed for preselected meal-only rules and confirmed guests.
- How the moved counts enter the request (as stated counts for this turn) without changing later turns' meaning of `counts_stated_now`.
- The wording of the new reply sentences (both languages).
- Tests: the card invariant of 5.2, the count rule of 5.3, the half rule of 5.4, each seen failing first.
