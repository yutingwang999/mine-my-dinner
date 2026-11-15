# From Request to Plan: Who Decides the Dish Counts

This explains how one planning request travels from the user's words to a checked plan, which steps are done by a model and which by code, and how the dish counts ("几荤几素") are read, stored, checked, and put into words along the way. It describes the behaviour after Phase 1 plan 2a. The design is in `docs/superpowers/specs/2026-11-14-phase-1-plan-2a-planner-efficiency-design.md`, Section 5.1. Related: `docs/planner-feasibility-check.md` (stopping early), `docs/planner-shortlist-and-ranking.md` (the candidate list), `docs/planner-stock-rules.md` (what is in stock).

## 1. The steps

A model does two things only: it understands the user's words, and it chooses the dishes. Everything else is code.

| Step | Who | Where | Takes | Gives |
|---|---|---|---|---|
| 1. Parse | Model (`parse_v2`) | `parse_request` in `agent/graph.py` | The conversation | The parsed request: what the user said, field by field; unstated fields stay empty |
| 2. Defaults | Code | `apply_defaults` in `agent/request.py` | The parsed request | The spec: the hard requirements every later step follows |
| 3. Feasibility check | Code | `planning/feasibility.py` | The spec, the library, the fridge | Go ahead, or stop early with reasons |
| 4. First message | Code | `agent/graph.py` | The spec and the candidate list | The text the planner reads (candidate list, then request summary) |
| 5. Planner | Model (`planner_v3`) | `agent/planner.py` | That text, plus its tools | The chosen recipe ids |
| 6. Validation | Code | `_h4` and the other rules in `planning/validate.py` | The spec and the chosen recipes | Passed, or the rules it breaks |
| 7. Retry message and plan card | Code | `agent/graph.py` | The validation result | Text for the planner's next attempt, and the reasons shown to the user |

Two things follow from this:

- **The spec is the one source of truth.** Feasibility, validation, and every piece of text are built from it. The parse step only says what the user said.
- **The planner never sees the spec itself.** It reads the text that code writes from the spec (step 4) and from validation results (step 7). If that text is wrong, a correct spec and a correct check still produce a failed plan.

## 2. How the counts are read and stored

The parsed request has a field for each count: `dishes` (meat plus vegetable dishes), `meat_dishes`, `vegetable_dishes`, `soups`, `staples`, `desserts`, `snacks`. A field the user did not mention stays empty (null). The parse step does not do arithmetic: "8 dishes, 2 of them meat" gives `dishes 8, meat_dishes 2`, and the vegetable count stays empty.

`apply_defaults` turns that into the spec:

- **What the user stated is a hard requirement.**
- **Defaults apply only when the user stated no count at all.** Then dishes and soups come from the headcount (1 person: 1 dish; 2: 2 dishes; 3 or 4: 2 dishes and a soup; 5 or more: 3 dishes and a soup), and they are listed as assumed. Once any count is stated, every unstated count is 0, because the user did not ask for it.
- **A meat and vegetable split with no total** sets the total: "一荤一素" means 2 dishes.
- **A split below the stated total is a minimum.** "三个菜，一荤一素" means 3 dishes with at least 1 meat and at least 1 vegetable dish (`split_is_minimum`). This is set only when both sides are named.
- **One side named with a total is exact.** "8个菜，2个荤菜" means exactly 2 meat dishes, so exactly 6 vegetable dishes. The spec stores `meat_dishes 2` and leaves `vegetable_dishes` empty; the 6 follows from the total.
- **A contradiction is asked about,** never guessed: a split above the total ("两个菜，三个荤菜"), or more than 12 items in total.
- **A range is taken at its lower end** and listed as assumed: "两三个菜" is 2 dishes.

## 3. How validation checks the counts (H4)

Validation counts the chosen recipes by dish type and compares each category with the spec:

- `dishes`, `soups`, `staples`, `desserts`, `snacks` must match exactly; a category nobody asked for must be 0.
- A named meat or vegetable count must match exactly, or be reached when the split is a minimum.
- An unnamed side is not checked on its own. With an exact one-sided split it is fixed anyway: 8 dishes in total with exactly 2 meat dishes leaves 6 vegetable dishes.

## 4. How the counts are put into words

Code writes the counts in three places. All three use one rule (`_with_implied_side` in `agent/graph.py`) for the meat and vegetable sides:

| What the user named | Words used |
|---|---|
| Both sides | Both numbers as given |
| One side, exact | The named side, and the other side as total minus the named side |
| One side or both, minimum | "at least" with the named numbers; an unnamed side gets no number |
| Neither side | No split ("any mix of meat and vegetable dishes") |

An unnamed side is never written as 0. The three places:

1. **The request summary in the planner's first message** (`_split_text`): `Split: 2 meat dishes, 6 vegetable dishes`.
2. **The retry message after a failed check** (`_counts_text` with `wanted=True`): "The plan has 8 dishes, 3 meat dishes, 5 vegetable dishes; the user wants 8 dishes, 2 meat dishes, 6 vegetable dishes."
3. **The reason on the plan card**, in the user's language: "方案有 8 道菜，3 道荤菜，5 道素菜，要求是 8 道菜，2 道荤菜，6 道素菜".

The "plan has" side always reports what the plan contains.

**A fourth case: an empty side.** When no split is named, the meal needs dishes, the shortlist is in use, and exactly one of "meat dish" and "vegetable dish" is complete with no candidates while the other has some, the summary adds one line after `Split:`: "No meat dish fits this request, so every dish is a vegetable dish. Do not search for meat dishes." (or the vegetable mirror). Example: "做三个菜，全做鸡蛋" in fridge-only mode has five egg vegetable dishes and no egg meat dish; before this line the planner still searched meat dishes three times. Nothing is added in any other case (`empty_side_line` in `agent/graph.py`).

**Why the derived number lives only in the text.** The parsed request and the spec keep only what the user said. Storing a derived "6 vegetable dishes" as if the user had said it would mix stated and inferred values: the contradiction question and the minimum rule depend on knowing which sides were named. For "三个菜，一荤一素" no exact vegetable number exists at all.

## 5. Examples

| User says | Parsed (step 1) | Spec (step 2) | Summary line the planner reads | What validation requires |
|---|---|---|---|---|
| 8个菜，其中2个荤菜 | dishes 8, meat 2 | dishes 8, meat 2, exact | Split: 2 meat dishes, 6 vegetable dishes | 8 dishes, exactly 2 meat (so 6 vegetable) |
| 三个菜，一荤一素 | dishes 3, meat 1, vegetable 1 | dishes 3, meat 1, vegetable 1, minimum | Split: at least 1 meat dish, at least 1 vegetable dish | 3 dishes, at least 1 of each |
| 一荤一素 | meat 1, vegetable 1 | dishes 2, meat 1, vegetable 1, exact | Split: 1 meat dish, 1 vegetable dish | 2 dishes, 1 of each |
| 三个人吃 (no count) | headcount 3 | dishes 2, soups 1, assumed | Split: none (any mix of meat and vegetable dishes) | 2 dishes, 1 soup |
| 两个菜一个汤 | dishes 2, soups 1 | dishes 2, soups 1, no split | Split: none (any mix of meat and vegetable dishes) | 2 dishes, 1 soup, nothing else |
| 两个菜，三个荤菜 | dishes 2, meat 3 | none: the user is asked to fix the counts | — | — |
| 两三个菜 | dishes 2 (range) | dishes 2, assumed | Split: none (any mix of meat and vegetable dishes) | 2 dishes |

### The case that went wrong, step by step

Found in the Task 10 evaluation (run `20261114T234332Z-plan2a-shortlist-no-vectors`, scenario `eight_dishes_two_meat`), before the wording fix:

1. Parse: `dishes 8, meat_dishes 2`. Correct.
2. Spec: 8 dishes, exactly 2 meat dishes, vegetable side unnamed. Correct.
3. Feasibility: enough recipes. Correct.
4. First message: `Split: 2 meat dishes`. The 6 vegetable dishes were left for the model to work out.
5. Planner: chose 3 meat and 5 vegetable dishes. (It also seemed to avoid the four egg dishes, one of which it needed to reach 6 vegetable dishes.)
6. Validation: H4, 3 meat dishes instead of 2. Correct.
7. Retry message: "the user wants 8 dishes, 2 meat dishes, **0 vegetable dishes**". Wrong: the unnamed side was printed as 0. The planner then proposed the same plan, and on the last attempt only 7 dishes.

The fix changed only steps 4 and 7 (the words). Steps 1, 2, 3, and 6 were already right.

## 6. Known gaps

- **"At least" on one side.** "8个菜，至少2个荤菜" has no rule of its own: the parse prompt has no example for it, and a minimum is set only when both sides are named, so it would be read as exactly 2 meat dishes. See `docs/phase-1-carryover.md`.
- **A total below the named side** ("两个菜，三个荤菜") would derive a vegetable count of 0, but the contradiction question stops that request first.
