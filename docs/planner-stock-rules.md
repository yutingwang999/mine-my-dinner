# How the Planner Decides What Is in Stock

This explains how the planner decides whether an ingredient is "at home", where that decision is used during one planning run, and the few places that look at stock differently. It describes the behaviour after Phase 1 plan 2a (`backend/minemydinner/planning/stock.py`, `feasibility.py`, `validate.py`). The candidate list built on top of it is described in `docs/planner-shortlist-and-ranking.md`, and the feasibility check that uses it before planning in `docs/planner-feasibility-check.md`.

What each model (route, parse, planner, revise) receives and never receives, with complete example inputs: `docs/planner-what-each-model-sees.md`.

The rule is written once (`_held` and `is_available` in `stock.py`) and reached through `compute_missing`, which lists what each recipe is missing. Every step that asks "is this dish missing something?" goes through it, so a dish is judged the same way in the feasibility check, the candidate list, the planner's tools, validation, and the plan card.

## 1. How stock is recorded

Each dictionary ingredient has a stock mode, and the inventory table has at most one row per ingredient:

| Stock mode | Typical items | "In stock" looks like | "Not in stock" looks like |
|---|---|---|---|
| availability | soy sauce, spring onion | the row exists | no row |
| count | eggs, potatoes | count 6 | no row, or count 0 |
| level | rice, flour | plenty, low | no row, or level "none" |
| grams | meat | 500 g | no row, or 0 g |

A count of 0 usually means the user lowered the count to 0 on the inventory page instead of deleting the row.

**The always-in-stock list** is separate: items the user marks as always at home (by default salt, cooking oil, water, sugar, soy sauce, vinegar). They need no inventory row.

**The dictionary is only a catalogue.** An ingredient being in the dictionary says what it is called and where it belongs; it says nothing about whether it is at home.

## 2. The rule: is this line covered?

A recipe's ingredient lines each have a role: main, secondary, or seasoning. For one line, the ingredient counts as held when:

1. it is on the always-in-stock list, whatever the inventory says; otherwise
2. it has an inventory row that is not marked level "none", and
3. either the line is a **seasoning**, or the row's count and grams are not 0.

So a count or grams of 0 means "not held" for a main or secondary line, but "held" for a seasoning line: nobody counts their soy sauce, and a 0 there usually just was never filled in.

Two more rules decide a whole line:

- **Child entries cover a parent line.** A line asking for 酱油 is covered when 生抽, a child of 酱油 in the dictionary, is held (by the same rule).
- **Optional lines and choice groups.** An optional line is never reported missing. In a choice group ("A or B"), the group is covered when any one member is held.

A recipe is **makeable** when no non-optional line is missing.

### Example: 生抽 in 红烧肉

In the recipe 红烧肉, 生抽 is a seasoning line.

| 生抽's state | Result |
|---|---|
| On the always-in-stock list | held |
| Not always in stock, no inventory row | **missing** |
| Not always in stock, row marked "none" | **missing** |
| Not always in stock, row with count 0 | held (seasoning) |
| Not always in stock, row with count 2 | held |

If 红烧肉's line asked for 酱油 (the parent) and the inventory held 生抽, the line would be covered too.

### Example: 鸡蛋 as a main ingredient

In 番茄炒蛋, 鸡蛋 is a main line. With no row, a row marked "none", or a row with count 0, the dish is **missing 鸡蛋**. With count 6, it is covered.

## 3. Where the rule is used

In the order of one planning run:

| Step | What it uses the rule for | Code |
|---|---|---|
| Feasibility check (before any model call) | Per needed dish type, how many recipes are makeable (fridge-only), and how many of those use a named ingredient | `feasibility.py`, through search's per-recipe facts |
| Feasibility check, quick named-ingredient test | Whether a named ingredient is at home at all (Section 4) | `feasibility.py` `_unavailable` |
| Search | Each hit carries the names it is missing | `search.py` |
| Candidate list | Fridge-only: only makeable recipes are kept, a filter (the planner's own searches apply it too). Shopping: fewer missing ranks higher. The "enough makeable dishes" safeguard after the spread | `shortlist.py` and `tools.py`, through search |
| Planner tools | Search hits carry missing items; the `compute_missing` tool lists them on request | `tools.py` |
| Validation H1 (fridge-only) | A dish in the plan that is missing something breaks H1. A revision run has no feasibility check; in a revision, H1 caused only by a locked dish (added by hand) is a warning, not a failure (plan 3b-1) | `validate.py`, `revise_step.py` `split_violations` |
| Plan card | "Missing: …" under each dish | `graph.py` |

### Places that look at stock differently

- **The empty-fridge check** (Section 5) looks at whether any inventory row exists outside the always-in-stock list, and at whether anything is makeable at all.
- **S1, amount warnings**, compares the amounts a recipe states with the counts or grams in stock, summed over the plan's dishes. It works only where both are exact (grams against grams, pieces against pieces); seasonings, vague amounts, and level-mode stock are skipped. It never decides "missing" and never fails a plan.
- **S3 and ranking by expiry** use only the expiry dates entered in the inventory.

## 4. The quick named-ingredient test

When the user names an ingredient ("全做鸡蛋", "这顿一定要用鸡蛋") in fridge-only mode, the feasibility check first asks whether it is at home at all, to give the plain reason "No eggs in the fridge". This test sees only a name, not a recipe line, so it has no role. It uses the most lenient reading, the seasoning one: a row with count 0 counts as held here. Using the strict reading would stop "一定要用酱油" when the soy sauce row says 0, although every recipe uses soy sauce as a seasoning and counts it as held.

Right after the quick test, the per-recipe check counts makeable dishes using the named ingredient, with each line's real role. So the final answer does not change; only the wording does.

### Example: eggs and "全做鸡蛋", fridge-only

| Eggs in stock | Quick test | Per-recipe check | What the user sees |
|---|---|---|---|
| No row | not at home | (skipped) | Stops early: "No 鸡蛋 in the fridge" |
| Row marked "none" | not at home | (skipped) | Stops early: "No 鸡蛋 in the fridge" |
| Row with count 0 | held (lenient) | egg dishes miss their main 鸡蛋, so 0 makeable | Stops early: "Only 0 vegetable dishes with 鸡蛋 can be made; this meal needs 2 vegetable dishes" |
| Row with count 6 | held | egg dishes are makeable | Planning goes ahead |

"这顿一定要用鸡蛋" behaves the same way: with eggs at count 0, no makeable dish uses eggs, so it stops early with the named-ingredient reason.

## 5. The empty-fridge check

In fridge-only mode, the feasibility check reports **empty fridge** only when both hold:

1. no inventory row exists outside the always-in-stock list, and
2. no recipe of any dish type the meal needs is makeable (always-in-stock items included).

The second condition matters because the always-in-stock list can hold more than seasonings. If the user put 鸡蛋 or 米 on it, or the library has a dessert made only of sugar and water, a meal can be cooked with an otherwise empty inventory. Then the empty-fridge reason is not reported, and the usual per-dish-type checks run instead.

When the fridge is empty, the checks that depend on stock (makeable counts, named ingredients at home) are not repeated, since they would only restate it. The checks that do not depend on stock still run and are reported with it: cookware, time limit, and whether the library has enough dishes at all. A user who stocks up then does not meet a second, hidden problem.

### Examples, fridge-only, no inventory rows outside the always-in-stock list

| Always in stock | Request | Result |
|---|---|---|
| salt, oil, water, sugar, soy sauce, vinegar | 两道素菜 | Empty fridge (nothing makeable), with next actions "switch to shopping", "fewer dishes", "update inventory" |
| the same, and no wok owned | 两道素菜 | Empty fridge **and** no cookware, with the union of both next actions |
| also 鸡蛋; the library has 蒸蛋羹 (鸡蛋, 盐) | 一道素菜 | No shortfall: 蒸蛋羹 is makeable, planning goes ahead |
| also 鸡蛋; only 蒸蛋羹 is makeable | 两道素菜 | Not enough dishes: "By the current fridge you can make 1 vegetable dish; this meal needs 2 vegetable dishes" |
| the defaults, and the library has 糖水 (糖, 水) | 一道甜品 | No shortfall: 糖水 is makeable |
