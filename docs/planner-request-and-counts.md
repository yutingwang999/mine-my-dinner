# From Request to Plan: Who Decides the Dish Counts

This explains how one planning request travels from the user's words to a checked plan, which steps are done by a model and which by code, and how the dish counts ("几荤几素") are read, stored, checked, and put into words along the way. It describes the behaviour after Phase 1 plan 2a, including the meat and vegetable bounds of Task 11, with plan 2b's changes (the headcount from the diner card, the cold-dish count of `parse_v5`, and "plan again"), plan 3b-1's changes (revising the current plan in the conversation: `parse_v6`, the revise step, and the counts of a revision, Section 7), plan 3b-1b's changes (drop or replace, one-meal wishes in `meal_avoid` with `parse_v7`, and next-action buttons applied in code), and plan 3b-2 (one)'s changes (a follow-up on a plan is read once by the reader, code builds the change's frame, and a dish picker fills it; the revise step is retired; Sections 1, 7 and 8). The design is in `docs/superpowers/specs/2026-11-14-phase-1-plan-2a-planner-efficiency-design.md`, Sections 5.1 and 5.11, `docs/superpowers/specs/2026-11-17-phase-1-plan-2b-diners-design.md`, `docs/superpowers/specs/2026-11-19-phase-1-plan-3b1-revise-plan-design.md`, `docs/superpowers/specs/2026-11-21-phase-1-plan-3b1b-revise-fixes-design.md`, and `docs/superpowers/specs/2026-11-22-phase-1-plan-3b2-revise-simplification-design.md`. Related: `docs/planner-feasibility-check.md` (stopping early), `docs/planner-shortlist-and-ranking.md` (the candidate list), `docs/planner-stock-rules.md` (what is in stock).

What each model (route, parse, planner, reader, picker) receives and never receives, with complete example inputs: `docs/planner-what-each-model-sees.md`.

## 1. The steps

A model does two things only: it understands the user's words, and it chooses the dishes. Everything else is code.

| Step | Who | Where | Takes | Gives |
|---|---|---|---|---|
| 1. Parse | Model (`parse_v8`) | `parse_request` in `agent/graph.py` | The conversation | The parsed request: what the user said, field by field; unstated fields stay empty |
| 2. Defaults | Code | `apply_defaults` in `agent/request.py` | The parsed request | The spec: the hard requirements every later step follows |
| 3. Feasibility check | Code | `planning/feasibility.py` | The spec, the library, the fridge | Go ahead, or stop early with reasons |
| 4. First message | Code | `agent/graph.py` | The spec and the candidate list | The text the planner reads (candidate list, then request summary) |
| 5. Planner | Model (`planner_v5`) | `agent/planner.py` | That text, plus its tools | The chosen recipe ids, and whether they meet the taste wish (`wish_met`); with no answer, code picks from the shortlist |
| 6. Validation | Code | `_h4` and the other rules in `planning/validate.py` | The spec and the chosen recipes | Passed, or the rules it breaks |
| 7. Retry message and plan card | Code | `agent/graph.py` | The validation result | Text for the planner's next attempt, and the reasons shown to the user |

Two things follow from this:

- **The spec is the one source of truth.** Feasibility, validation, and every piece of text are built from it. The parse step only says what the user said.
- **The planner never sees the spec itself.** It reads the text that code writes from the spec (step 4) and from validation results (step 7). If that text is wrong, a correct spec and a correct check still produce a failed plan.

When the diner card is shown (more than one person eats, or the user states a new rule or a diet word about themselves), it (`select_diners`) comes between steps 1 and 2, and its confirmed headcount replaces the parsed one. "Plan again" (the plan card's fixed message "按同样的要求重新规划") skips step 1: code reuses the thread's previous parsed request, with no route or parse call. The five fixed next-action button messages (`ACTION_MESSAGES` in `agent/request.py`) skip the route and the reader (code knows what a button changes) and, since plan 3b-1b, the parse as well: code changes the request (Section 8).

**A follow-up on a plan with dishes differs** (plan 3b-2 (one); before it, plans 3b-1 and 3b-1b routed the message and sent it to the parse and the revise step). The route is called only on a thread's first message and after an early stop or a no-plan reply. A typed message on a plan with dishes is read once by the reader (`follow_up_v2`, `read_follow_up` in `agent/graph.py`), whose `FollowUpReading` says what kind of message it is. For a `change`, the steps change as follows:

| Step | On a change |
|---|---|
| 1. Parse | Not run. The reader reads the message with the plan, the diners' anonymous rules, the meal's current conditions and the whole conversation, and answers what the message wants: dishes to remove (drop or replace, each with a quote), dishes to add, a wish, this meal's whole `meal_avoid`, stated counts and stated conditions. |
| 1a. The request after the reading | Code builds it (`request_after` in `agent/follow_up.py`): the request behind the plan (`plan_request`), with the reading's stated counts replacing the whole count group, its stated conditions replacing their own fields (`clear_time_limit` clears the limit), `meal_avoid` set to the reading's whole list, and a stated wish replacing `query`. Everything else is carried, so nothing is left unstated and no diner-card check runs. `counts_stated_now` is true only when the reading states a count (Section 7). |
| 1b. Diner card | Not shown. A message read as `people` (a change in who eats, a lasting rule about someone) takes the parse instead, as before: the app note line, the unstated fields filled from the plan's request (`carry_unstated`), the diner card, and the meal planned anew. A message read as `new_meal` is parsed as a new request; `other` gets the fixed reply. |
| 2. Defaults | Not run. The change's spec comes from `revision_spec` in `agent/revision.py` (Section 7). |
| 3. Feasibility check | Not run (`docs/planner-feasibility-check.md`, Section 1). |
| 4. The frame | Code (`build_frame` in `agent/frame.py`), no model: which dishes keep, drop, or become a slot to fill, which bounded choices are left, which named dishes are placed or missing, and whether any dish is left. The rules plans 3b-1 and 3b-1b checked after the fact (R1-R9) are how the frame is built, except those that needed reading language: since spec 5.4's amendment (2026-11-23) code checks and does not interpret, so a replacement may be of any type (stated counts are checked on the result), a locked dish's quote must only be in the message, the dishes a wish touches are the reader's, and a name that is no library title goes to the picker (`docs/planner-what-each-model-sees.md`, Section 10). |
| 5. Picker | A model (`pick_v1`, `agent/picker.py`), only when the frame has a slot or a choice: it gets a shortlist for the slots' dish types, the plan, the rules, the wish and the frame, and answers a dish (or none) per slot and the dishes chosen per choice. |
| 6. Validation | The answer against the frame (`apply_picks`), then `validate_plan` on the new dishes (rules, stock, cookware, time) and whether a choice or add made for a new must-use ingredient uses it; one retry with the problems. The card's result is validated in full against the spec; when its counts are off, against the counts of the dishes it has (`result_spec`), as the change list sets them. |

## 2. How the counts are read and stored

The parsed request has a field for each count: `dishes` (meat plus vegetable dishes), `meat_dishes`, `vegetable_dishes`, `soups`, `staples`, `desserts`, `snacks`. A field the user did not mention stays empty (null). The meat and vegetable counts each have a bound, `meat_bound` and `vegetable_bound`: `exact` for a plain number, `at_least` for "至少", `at_most` for "最多". A count with no bound is exact; a bound with no count is ignored. The parse step does not do arithmetic: "8 dishes, 2 of them meat" gives `dishes 8, meat_dishes 2`, and the vegetable count stays empty.

Only the meat and vegetable pair has bounds, because the dish total limits them. "至少一个汤" or "最多一个主食" is read as that number and listed as assumed (`range_fields`), as a range taken at its lower end is. Vague phrases ("多来点荤菜", "以素为主") are a query term, not a count. A cold dish ("凉菜", "凉拌菜") is a meat or vegetable dish, not a category: mentioned in passing it gives no count ("来个凉菜"), but a stated number of cold dishes is the dish total ("就做两个凉菜" is `dishes 2`; `parse_v5`).

`apply_defaults` turns that into the spec:

- **What the user stated is a hard requirement.**
- **Defaults apply only when the user stated no count at all.** Then dishes and soups come from the headcount (the diner card's confirmed total once the card was answered, otherwise the parsed one; 1 person: 1 dish; 2: 2 dishes; 3 or 4: 2 dishes and a soup; 5 or more: 3 dishes and a soup), and they are listed as assumed. Once any count is stated, every unstated count is 0, because the user did not ask for it.
- **The dish total** (`dish_total` in `agent/request.py`, shared with the contradiction check):
  - a stated total is used as is;
  - with no total and every named side exact, the total is the sum of the named counts: "一荤一素" is 2 dishes, "两个荤菜" is 2;
  - with no total and a side named "at least" or "at most", the total is the headcount default number of dishes, raised to the sum of the lower bounds if that is larger, lowered to the sum of the upper bounds if that is smaller (only when both sides have an upper bound: exact `n` or at most `n`; an unnamed or "at least" side has none), and listed as assumed. "五个人，最多一个荤菜，最多一个素菜" is 2 dishes instead of the default 3, so the user is not asked about a total they never gave. The total is never lowered to 0: with both sides at most 0 it stays at the default, and the empty ranges are a contradiction. No soup is added: a count was stated. "三个人，最多一个素菜" is 2 dishes (assumed), at most 1 vegetable dish, no soup. Both request styles do this; "ask first" does not ask for the total.
- **Both sides named exactly below the total are minimums.** "三个菜，一荤一素" means 3 dishes with at least 1 meat and at least 1 vegetable dish: both bounds become `at_least`.
- **One side named with a total is exact.** "8个菜，2个荤菜" means exactly 2 meat dishes, so exactly 6 vegetable dishes. The spec stores `meat_dishes 2, meat_bound exact` and leaves `vegetable_dishes` empty; the 6 follows from the ranges (Section 3).
- **A contradiction is asked about,** never guessed (`request_problem`): `split_conflicts_total` when a side's range comes out empty against the total ("两个菜，三个荤菜", "两个菜，至少三个荤菜", "三个菜，最多一个荤菜，最多一个素菜"), and `too_many` for more than 12 items in total. The existing composition question asks the user to restate the counts, whatever the request style.
- **A range is taken at its lower end** and listed as assumed: "两三个菜" is 2 dishes.

A spec saved before bounds existed carries `split_is_minimum` instead. `PlanSpec` and the frontend's `normalizePlan` read it as "at least" on each named side when it is true, otherwise exact.

## 3. One range per side

`side_ranges` in `planning/composition.py` turns the spec into a range `[low, high]` for meat dishes and for vegetable dishes. Validation, the feasibility check, the shortlist, and every text use it; nothing else computes ranges.

With dish total `T`, a named side gives: exact `n` → `[n, n]`; at least `n` → `[n, T]`; at most `n` → `[0, n]`. An unnamed side is `[0, T]`. Then each side is tightened by the other, because meat plus vegetable dishes equal `T`:

- `meat_low = max(meat_low, T − veg_high)`, `meat_high = min(meat_high, T − veg_low)`, and the same for vegetables.

So "三个菜，最多一个素菜" gives vegetables `[0, 1]` and meat `[2, 3]`, and "8个菜，2个荤菜" gives meat `[2, 2]` and vegetables `[6, 6]`. A range whose low end is above its high end is the contradiction of Section 2.

How each step reads the ranges:

- **Validation (H4):** soups, staples, desserts, and snacks must match exactly, and so must the dish total; meat dishes must lie within the meat range and vegetable dishes within the vegetable range. The finding's detail carries both ranges. When no attempt passes, the closest attempt is chosen by the number of violations, then by the composition gap: the distance from each exact count plus the distance outside each range.
- **Feasibility check:** a side, named or implied, is short when fewer recipes are available than its low end; the dish total is short when `min(meat high, meat available) + min(veg high, veg available)` is below it (`docs/planner-feasibility-check.md`).
- **Next actions:** "Any mix of meat and vegetable dishes" (`relax_split`) is offered when the user named a meat or vegetable count and a meat or vegetable side, named or implied, is short.
- **Shortlist:** each side gets candidates for its high end; a side whose high end is 0 is not shortlisted.

## 4. How the counts are put into words

Code writes the counts in four places, and every one turns a side's range into words with one rule (`side_phrase` in `agent/graph.py`):

| Range against the total `T` | English | Chinese |
|---|---|---|
| `low = high` | 6 vegetable dishes | 6 道素菜 |
| `low > 0` and `high < T` | 2 to 3 meat dishes | 2 到 3 道荤菜 |
| `low > 0` | at least 2 meat dishes | 至少 2 道荤菜 |
| `high < T` | at most 1 vegetable dish | 最多 1 道素菜 |
| otherwise (`[0, T]`) | nothing: the side is not constrained | |

An unconstrained side is never written as a number. The four places:

1. **The request summary in the planner's first message** (`_split_text`): `Split: at least 2 meat dishes, at most 1 vegetable dish`, then the line `Every count is exact unless it says at least, at most, or a range.` With no split: `Split: none (any mix of meat and vegetable dishes)`.
2. **The retry message after a failed check** (`_counts_text` with `wanted=True`): "The plan has 3 dishes, 1 meat dish, 2 vegetable dishes; the user wants 3 dishes, at least 2 meat dishes, at most 1 vegetable dish."
3. **The unmet rule on the plan card**, in the user's language: "方案有 3 道菜，1 道荤菜，2 道素菜，要求是 3 道菜，至少 2 道荤菜，最多 1 道素菜".
4. **The reason of an early stop** (`reason_text`): "this meal needs at least 2 meat dishes" when the needed count is the low end of a range that is not exact, otherwise the plain number ("this meal needs 6 vegetable dishes").

The "plan has" side always reports what the plan contains. Since plan 3b-1b an attempt with no dishes never reaches place 3: a planner that gives no answer is replaced by a code-picked plan from the shortlist, an empty attempt is never kept as the closest plan, and when every attempt was empty the card shows no reason and the note says no dishes could be put together (`docs/planner-feasibility-check.md`, Section 5). The count note of a hand-edited card (`_count_notes` in `agent/edits.py`) reads the request the plan was built from (`plan_request`), not a later turn's parse.

**The card's "As you asked" line shows the user's own words, not the ranges.** "四个菜，至少一个荤菜，素菜最多两个" gives the planner "at least 2 meat dishes, at most 2 vegetable dishes" (the tightened ranges, which are stricter and clearer), while the card says "4 dishes (at least 1 meat dish, at most 2 vegetable dishes)" / "4 道菜（至少 1 道荤菜、最多 2 道素菜）". The two differ on purpose (decided with the user). When the dish total was assumed, the card lists the named side on its own ("As you asked: at most 1 vegetable dish") and the total under "Assumed".

**Why the derived numbers live only in the ranges.** The parsed request and the spec keep only what the user said. Storing a derived "6 vegetable dishes" as if the user had said it would mix stated and inferred values: the contradiction question and the minimum rule depend on knowing which sides were named, and the card shows the user's own bounds.

## 5. Examples

| User says | Parsed (step 1) | Spec (step 2) | Ranges (meat, vegetable) | Summary line the planner reads |
|---|---|---|---|---|
| 8个菜，其中2个荤菜 | dishes 8, meat 2 exact | dishes 8, meat 2 exact | `[2, 2]`, `[6, 6]` | Split: 2 meat dishes, 6 vegetable dishes |
| 8个菜，至少2个荤菜 | dishes 8, meat 2 at_least | dishes 8, meat 2 at_least | `[2, 8]`, `[0, 6]` | Split: at least 2 meat dishes, at most 6 vegetable dishes |
| 三个菜，最多一个素菜 | dishes 3, vegetable 1 at_most | dishes 3, vegetable 1 at_most | `[2, 3]`, `[0, 1]` | Split: at least 2 meat dishes, at most 1 vegetable dish |
| 四个菜，至少一个荤菜，素菜最多两个 | dishes 4, meat 1 at_least, vegetable 2 at_most | the same | `[2, 4]`, `[0, 2]` | Split: at least 2 meat dishes, at most 2 vegetable dishes |
| 三个菜，不要素菜 | dishes 3, vegetable 0 exact | the same | `[3, 3]`, `[0, 0]` | Split: 3 meat dishes, 0 vegetable dishes |
| 三个人，最多一个素菜 | headcount 3, vegetable 1 at_most | dishes 2 (assumed), vegetable 1 at_most, no soup | `[1, 2]`, `[0, 1]` | Split: at least 1 meat dish, at most 1 vegetable dish |
| 五个人，最多一个荤菜，最多一个素菜 | headcount 5, meat 1 at_most, vegetable 1 at_most | dishes 2 (assumed: the default 3 lowered to the upper bounds), no soup | `[1, 1]`, `[1, 1]` | Split: 1 meat dish, 1 vegetable dish |
| 三个菜，一荤一素 | dishes 3, meat 1, vegetable 1 | dishes 3, both at_least | `[1, 2]`, `[1, 2]` | Split: 1 to 2 meat dishes, 1 to 2 vegetable dishes |
| 一荤一素 | meat 1, vegetable 1 | dishes 2, both exact | `[1, 1]`, `[1, 1]` | Split: 1 meat dish, 1 vegetable dish |
| 三个人吃 (no count) | headcount 3 | dishes 2, soups 1, assumed | `[0, 2]`, `[0, 2]` | Split: none (any mix of meat and vegetable dishes) |
| 两个菜一个汤 | dishes 2, soups 1 | dishes 2, soups 1, no split | `[0, 2]`, `[0, 2]` | Split: none (any mix of meat and vegetable dishes) |
| 至少一个汤 | soups 1, range_fields [soups] | soups 1 (assumed) | `[0, 0]`, `[0, 0]` | Split: none (any mix of meat and vegetable dishes) |
| 两个菜，三个荤菜 / 两个菜，至少三个荤菜 / 三个菜，最多一个荤菜，最多一个素菜 | a side's range is empty | none: the user is asked to restate the counts | — | — |
| 两三个菜 | dishes 2 (range) | dishes 2, assumed | `[0, 2]`, `[0, 2]` | Split: none (any mix of meat and vegetable dishes) |

The next-action buttons change the request in code (Section 8): "一荤一素" then "荤素都行" gives dishes 2 with no split (the total is kept); "全做鸡蛋" then "不一定要用指定的食材" moves eggs from `all_use` to `prefer_use`. The parse prompt (`parse_v3` and later) keeps examples for these conversations; they matter only for a card saved before cards kept their request.

### The cases that went wrong, step by step

Found in the Task 10 evaluation (run `20261114T234332Z-plan2a-shortlist-no-vectors`, scenario `eight_dishes_two_meat`), before the wording fix:

1. Parse: `dishes 8, meat_dishes 2`. Correct.
2. Spec: 8 dishes, exactly 2 meat dishes, vegetable side unnamed. Correct.
3. Feasibility: enough recipes. Correct.
4. First message: `Split: 2 meat dishes`. The 6 vegetable dishes were left for the model to work out.
5. Planner: chose 3 meat and 5 vegetable dishes. (It also seemed to avoid the four egg dishes, one of which it needed to reach 6 vegetable dishes.)
6. Validation: H4, 3 meat dishes instead of 2. Correct.
7. Retry message: "the user wants 8 dishes, 2 meat dishes, **0 vegetable dishes**". Wrong: the unnamed side was printed as 0. The planner then proposed the same plan, and on the last attempt only 7 dishes.

The wording fix changed only steps 4 and 7. Task 11 then closed two gaps the same case showed (design 5.11):

- **"At least" was lost.** "8个菜，至少2个荤菜" was parsed as exactly 2 meat dishes. In fridge-only mode with 3 makeable meat and 5 makeable vegetable dishes, it reached the planner, failed H4 on every attempt, and ended with no plan, although 3 meat and 5 vegetable dishes meet the request. `parse_v3` now keeps the bound, and every step reads the range `[2, 8]`.
- **The implied side was never checked.** For "8个菜，2个荤菜" with the same fridge, the feasibility check compared the named side and the dish total but not the 6 vegetable dishes the split implies, so an impossible request went through to three failing attempts. The check now compares every side's low end and stops early: "By the current fridge you can make 5 vegetable dishes; this meal needs 6 vegetable dishes".

## 6. Known gaps

- **Questions written by the model** for vague amounts ("多来点荤菜", "以素为主") are out of scope; those phrases stay a query term (`docs/phase-1-carryover.md`).
- **Closed in plan 3b-1b: whether a button changes the request depended on the model.** Code now makes a button's change on the request of the card that offered it, with no parse call, and a button on a card whose plan was kept (or, since plan 3b-2 (one), whose change was done only in part) changes the current plan again, so hand edits are kept (Section 8). Only a card saved before cards kept their request still has its button message parsed.

## 7. Counts on a change

A change (a `change` reading, or a button on a card whose plan was kept or was changed only in part) builds its spec with `revision_spec` (`agent/revision.py`) instead of `apply_defaults`:

- **The current plan is the baseline.** Its own counts, hand edits included, become exact counts: dishes, meat and vegetable dishes (both exact), soups, staples, desserts, snacks. A plan trimmed or grown by hand keeps its size.
- **The reading states counts only for this message.** The reader's `counts` hold only the counts the message itself states, as absolute numbers for the whole plan; a relative number ("少一个菜") is written as the number it gives on the plan as shown, which the reader sees line by line. `request_after` sets `counts_stated_now` to true exactly when the reading states a count ("stated this turn"), and then the stated counts replace the whole count group of the plan's request (totals, sides, bounds and `range_fields`), so an old split never joins a new total. When the message states none, `counts_stated_now` is false and the plan's request keeps its counts, which the change does not read: the plan's own counts stand, and only the change list's drops, replacements and adds move them.
- **A stated count binds even when it equals the earlier request.** `build_change` compares the new request with the plan's request without its counts, so "还是要两个荤菜" after a turn that could not add one still counts. A meat or vegetable side changes the split and leaves the other side free; a new total drops the old split.
- **Without a stated count, the change list sets the counts.** A drop shrinks the plan ("茄子这道不要" on 2 meat and 1 vegetable dish leaves 2 dishes), a replace keeps it ("换一个"; the picker chooses the new dish, of any type, keeping the plan's balance unless the message suggests otherwise), an add grows it ("再加一道"; an add with no type, "再添两道", grows it by its count, each new dish of any type the picker chooses). The frame computes this from the reading; no model guesses a count change for a removal.
- **With a stated count, the frame meets the stated fields only.** It adds slots for a count above the plan and drops dishes for a count below it: first new dishes not yet chosen, then unlocked dishes, as a "drop exactly N of these" choice for the picker when there are more candidates than needed. An add of any type counts toward a stated total, not toward either side. A locked dish is never a candidate for a count drop. When an add made for a stated count finds no dish, the drops made with it are withheld, so the plan never shrinks unasked.
- **A new must-use ingredient is held to.** When the reading adds a must-use ingredient no kept dish uses, the frame makes one replace choice for it (or, when the change already adds a dish, the add carries it), and the picker must bring a dish that uses it (one retry with the reason). When no dish does, the original dish stays (or the add is not made, and a count's drops made with it are withheld), the reply says once that the ingredient was not used, and the name is left out of the new version's spec and `plan_request`; the turn's `request` keeps it, so a button on that card tries it again.
- **A null never clears a non-count condition.** `request_after` replaces only the conditions the reading states (`mode`, `time_limit_min`, `must_use`, `prefer_use`; `clear_time_limit` removes the limit on purpose), so a follow-up cannot drop a "must use" ingredient or the time limit by not mentioning it (`docs/phase-1-carryover.md`).
- **A one-meal avoidance (`meal_avoid`) is the reading's whole list.** "今天不想吃土豆" adds 土豆 to the list the meal already has; "香菜其实放点也行" leaves 香菜 out of it, so the avoidance is withdrawn. Code applies the list as this meal's own rule (diner key `meal`; `with_meal_avoid` in `agent/plan_card.py`), and the card keeps it for later turns. The plan's dishes that break it become replace slots (of any type), locked dishes included; a dish no replacement is found for is removed, and the reply says so (the plan may get smaller, but it never keeps a dish that breaks a wish the user stated). Taste words for this meal ("这顿不要辣的") and words about an amount ("豆腐有点多") are a wish, not an avoidance. A lasting rule about a person is read as `people` and goes to the diner card.
- **The wish lasts.** A wish in the reading replaces the request's `query` for later turns and buttons; a message without one keeps it. An unmet wish is said in the reply by code.
- **After an early stop** the plan has no dishes, so a follow-up is routed and parsed as before. When the route answers `revise`, the parse input ends with the app note `Current plan: no dishes`, `carry_unstated` fills the unstated fields, and the meal is planned from that request; when it answers `plan`, the message is parsed as a new request, with no app note.

## 8. Next-action buttons (plan 3b-1b)

A next-action button sends a fixed message (`ACTION_MESSAGES` in `agent/request.py`; the frontend's `chat.actionMessage` must match it). The button belongs to the newest card: that of the newest reply with a card, as in the chat, where a later reply without one (an "other" reply, a question, the empty-plan question) leaves its buttons active. When that card keeps the request it was built from (every card since plan 3b-1b), code takes that request and changes one field (`apply_action` in `agent/actions.py`):

| Button | Change |
|---|---|
| `switch_to_shopping` ("允许购物") | `mode` becomes shopping |
| `fewer_dishes` ("少做几道菜") | The dish total goes down by one, never below one; a named meat or vegetable side above the new total is lowered with it, and two named sides that would then contradict the total become "at most" |
| `drop_time_limit` ("不限时间") | The time limit is cleared |
| `relax_split` ("荤素都行") | The meat and vegetable sides and their bounds are cleared; the total stays. On a change card it is also offered when an added meat or vegetable dish found nothing and the turn stated that side's count; the total then also counts those adds, which the change re-runs as dishes of any type |
| `relax_named` ("不一定要用指定的食材") | `must_use` and `all_use` move into `prefer_use` |

`fewer_dishes` and `relax_split` set `counts_stated_now`, so a change takes their counts. No route call, no reader or parse call, and no ask-first question is made. What runs next depends on the card. On a card whose plan was kept (`plan_kept`: nothing could be done) or whose change was done only in part (the card's server-only `unfinished` field keeps the parts not done and that turn's words), the change path runs again on the current plan with the changed request, through the same frame as a reading, so hand edits and locks stay: there is no reader call, and the reading is the card's unfinished parts (`button_reading` in `agent/change_step.py`), with the button request's counts when it states counts. So a button that lifts a condition re-tries what that condition blocked, and the picker's wish line carries the offering turn's words. When that card's turn stated no count, the request's counts are first rebased on the plan the user sees (`on_plan_counts`), so "one fewer" is one fewer than the dishes shown. On any other card (no plan, early stop, a failed new plan) the meal is planned from scratch with the changed request; the diner card shows only when a friend in the settled set was deleted since. The run's record marks the turn with `button`. A card saved before cards kept their request falls back to plan 3b-1's behaviour: the route is skipped and the message is parsed.
