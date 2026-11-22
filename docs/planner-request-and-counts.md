# From Request to Plan: Who Decides the Dish Counts

This explains how one planning request travels from the user's words to a checked plan, which steps are done by a model and which by code, and how the dish counts ("几荤几素") are read, stored, checked, and put into words along the way. It describes the behaviour after Phase 1 plan 2a, including the meat and vegetable bounds of Task 11, with plan 2b's changes (the headcount from the diner card, the cold-dish count of `parse_v5`, and "plan again"), plan 3b-1's changes (revising the current plan in the conversation: `parse_v6`, the revise step, and the counts of a revision, Section 7), and plan 3b-1b's changes (drop or replace, one-meal wishes in `meal_avoid` with `parse_v7`, and next-action buttons applied in code, Sections 7 and 8). The design is in `docs/superpowers/specs/2026-11-14-phase-1-plan-2a-planner-efficiency-design.md`, Sections 5.1 and 5.11, `docs/superpowers/specs/2026-11-17-phase-1-plan-2b-diners-design.md`, `docs/superpowers/specs/2026-11-19-phase-1-plan-3b1-revise-plan-design.md`, and `docs/superpowers/specs/2026-11-21-phase-1-plan-3b1b-revise-fixes-design.md`. Related: `docs/planner-feasibility-check.md` (stopping early), `docs/planner-shortlist-and-ranking.md` (the candidate list), `docs/planner-stock-rules.md` (what is in stock).

What each model (route, parse, planner, revise) receives and never receives, with complete example inputs: `docs/planner-what-each-model-sees.md`.

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

When the diner card is shown (more than one person eats, or the user states a new rule or a diet word about themselves), it (`select_diners`) comes between steps 1 and 2, and its confirmed headcount replaces the parsed one. "Plan again" (the plan card's fixed message "按同样的要求重新规划") skips step 1: code reuses the thread's previous parsed request, with no route or parse call. The five fixed next-action button messages (`ACTION_MESSAGES` in `agent/request.py`) skip the route (since plan 3b-1: a no-plan reply counts as a current plan, so a route would send them to the revise step) and, since plan 3b-1b, the parse as well: code changes the request (Section 8).

**A revise turn differs** (plan 3b-1). When the route says "revise" and the thread has a current plan, the steps change as follows:

| Step | On a revise turn |
|---|---|
| 1. Parse | The parse input ends with one app-written line, "[App note, not written by the user] Current plan: ..." (`current_plan_line` in `agent/graph.py`): the plan's dish counts and titles. `parse_v8` (as `parse_v7` and `parse_v6` before it) counts relative words from it and never takes it for the user's words. |
| 2. Defaults | Not run. The revision's spec comes from `revision_spec` in `agent/revision.py` (Section 7). |
| 3. Feasibility check | Not run (`docs/planner-feasibility-check.md`, Section 1). |
| 4-5. First message, model | The revise step (`revise_v3`, `agent/revise_step.py`) gets the same candidate list and request summary, then the current plan and the user's message, and answers with a change list (what to remove, each as a drop or a replacement, and what to add) instead of a whole plan. |
| 6. Validation | The normal rules, plus the revision checks R1 to R9 (`check_revision`: quotes, locked dishes, the size of a general change, a well-formed change list, same-type replacement, add-only, a change that would leave no dish). H1, H3, H6, and H7 caused only by locked dishes are allowed, not failures. Without a stated count each attempt is checked against its own change list's counts (Section 7). |

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
- **Closed in plan 3b-1b: whether a button changes the request depended on the model.** Code now makes a button's change on the request of the card that offered it, with no parse call, and a button on a failed revision's card revises the current plan again, so hand edits are kept (Section 8). Only a card saved before cards kept their request still has its button message parsed.

## 7. Counts on a revision

A revision turn builds its spec with `revision_spec` (`agent/revision.py`) instead of `apply_defaults`:

- **The current plan is the baseline.** Its own counts, hand edits included, become exact counts: dishes, meat and vegetable dishes (both exact), soups, staples, desserts, snacks. A plan trimmed or grown by hand keeps its size.
- **`counts_stated_now` decides whether the message's counts replace them.** `parse_v6` and later set it true when the newest message itself states or changes a count, as a number or relative to the plan, and false otherwise (the app note never makes it true). When it is false, the message changes no count itself ("想要辣一些的" keeps the plan's counts); only the change list's drops and adds do (below). When it is true, every non-null total and category count the parse gives replaces the baseline, even one equal to the previous request (a plan hand-trimmed to 2, asked as 3, with "再加一道" becomes 3). A request parsed before the field existed has it null; then a count is taken only when it differs from the previous request.
- **Relative counts** ("再加一道") are computed by the parse from the current-plan line, not from the first request: 2 dishes in the line plus one is 3.
- **The meat and vegetable split changes only when it differs from the previous request,** so a split carried over from an earlier message never becomes a stale exact side. When the total or a side changes, the old split is dropped: only a side the message itself changes keeps a count, and the other side is left free.
- **A plan with no dishes** uses its own spec's counts as the baseline.
- **A null never clears a non-count condition.** A null or empty field is not a statement, so a follow-up cannot drop a "must use" ingredient or the time limit (`docs/phase-1-carryover.md`).
- **Drop or replace (plan 3b-1b).** The revise step marks each removed dish `drop` or `replace` (`RevisionProposal` in `agent/revision.py`). Words that only say not to have a dish ("茄子这道不要", "去掉茄子", "汤不要了") drop it; words that want another dish in its place ("换一个", "换成", "来个") replace it with a dish of the same type, or with the dish the user names. A message that only adds ("再加一道") uses the add-only target and removes nothing. A change that would leave no dish is refused, and the reply asks whether to plan a new meal.
- **Without a stated count, the change list sets the counts.** When `counts_stated_now` is false, each attempt is checked against its own counts (`attempt_spec` in `agent/revise_step.py`): the plan's counts minus the dropped dishes, by dish type, plus the added ones. So "茄子这道不要" on 2 meat and 1 vegetable dish leaves a plan of 2 dishes, and the type pairing (R7) and add-only (R8) checks guard the size instead of H4. When the message states a count ("4个菜就够了"), the stated count wins as above, and the dishes needed to reach it may be dropped (locked dishes stay). The parse no longer guesses a count change for a removal; this replaces plan 3b-1's rule that a named removal without a number keeps the plan's size.
- **A one-meal avoidance (`meal_avoid`, since `parse_v7`).** "今天不想吃肉" or "不想吃土豆" is a wish for this meal only, not a person's rule, so it brings no diner card. Code applies it as this meal's own rule (diner key `meal`; `with_meal_avoid` in `agent/plan_card.py`) in the search and the checks, and the plan card keeps it, so a later revision keeps it until a turn states a different one (`revision_meal_avoid`). On a revision the plan's dishes that break it are marked for the revise step and replaced, never dropped, locked dishes included; a replacement may be another dish type, and the counts by type follow it while the total stays. On a new plan it excludes the dishes. Taste words for this meal ("这顿不要辣的") are the query, not an avoidance. So are words about an amount (`parse_v8`): "不要这么多 X", "X 太多了" or "少放点 X" is a remark about the plan, a general wish that changes only some dishes within the usual limit, never an avoidance that would mark every X dish, locked ones included; only leaving X out of this meal with no word about amount ("这顿别放 X") is. A lasting rule ("我不吃肉") still goes to the diner card.

## 8. Next-action buttons (plan 3b-1b)

A next-action button sends a fixed message (`ACTION_MESSAGES` in `agent/request.py`; the frontend's `chat.actionMessage` must match it). When the newest reply's card keeps the request it was built from (every card since plan 3b-1b), code takes that request and changes one field (`apply_action` in `agent/actions.py`):

| Button | Change |
|---|---|
| `switch_to_shopping` ("允许购物") | `mode` becomes shopping |
| `fewer_dishes` ("少做几道菜") | The dish total goes down by one, never below one; a named meat or vegetable side above the new total is lowered with it, and two named sides that would then contradict the total become "at most" |
| `drop_time_limit` ("不限时间") | The time limit is cleared |
| `relax_split` ("荤素都行") | The meat and vegetable sides and their bounds are cleared; the total stays |
| `relax_named` ("不一定要用指定的食材") | `must_use` and `all_use` move into `prefer_use` |

`fewer_dishes` and `relax_split` set `counts_stated_now`, so a revision takes their counts. No route call, no parse call, and no ask-first question is made. What runs next depends on the card. On a failed revision's card (the plan was kept) the revision runs again on the current plan with the changed request, so hand edits and locks stay; when that card's turn stated no count, the request's counts are first rebased on the plan the user sees (`on_plan_counts`), so "one fewer" is one fewer than the dishes shown. On any other card (no plan, early stop, a failed new plan) the meal is planned from scratch with the changed request; the diner card shows only when a friend in the settled set was deleted since. The run's record marks the turn with `button`. A card saved before cards kept their request falls back to plan 3b-1's behaviour: the route is skipped and the message is parsed.
