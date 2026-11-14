# The Planner's Feasibility Check

Before the planner calls a model, code asks one question: **can this request be met at all with the user's recipe library (and, when only using what is at home, the fridge)?** If not, planning stops early, and the user sees why and what to change, without paying for model calls that were bound to fail.

It describes the behaviour after Phase 1 plan 2a (`backend/minemydinner/planning/feasibility.py`, wired in `backend/minemydinner/agent/graph.py`). The design is in `docs/superpowers/specs/2026-11-14-phase-1-plan-2a-planner-efficiency-design.md`, Sections 5.2 and 5.3. Related: `docs/planner-stock-rules.md` (what "makeable" and "in stock" mean) and `docs/planner-shortlist-and-ranking.md` (the candidate list built when planning goes ahead).

## 1. When it runs

- After the request is parsed and its defaults applied, **before the first planner model call**.
- Again **before every retry**, so a request that turns out impossible does not keep retrying. Within one planning run its inputs do not change today, so this second check always repeats the first; it is kept for when stock or diners can change during a run (an inventory-changing planner tool, diner selection in plan 2b). See `docs/phase-1-carryover.md`.

It only reads the database and computes; it calls no model. On the chat page it shows as the step "Checking the fridge and dietary rules".

## 2. What it checks

Six reasons, checked in this order. All reasons that hold are reported, not only the first.

| Reason | Meaning | Checked when | Example |
|---|---|---|---|
| Empty fridge (`empty_inventory`) | No inventory row outside the always-in-stock list **and** nothing the meal needs can be made | Fridge-only | Empty fridge, only seasonings always in stock |
| No cookware (`no_cookware`) | For some needed dish type, no recipe fits the cookware the user owns | Always | Every vegetable dish needs a wok; only a baking tray is owned |
| Not enough from the fridge (`not_enough_dishes`) | For some needed dish type, fewer makeable recipes than the meal needs | Fridge-only | 2 vegetable dishes asked, the fridge makes 1 |
| Time limit (`time_limit`) | For some needed dish type, no recipe fits the limit (stated or user-given times only). In fridge-only mode only makeable recipes count | A time limit is set | 10-minute limit, every soup takes 40 minutes |
| Not enough in the library (`not_enough_in_library`) | For some needed dish type, fewer safe recipes in the library than the meal needs | Always | "一荤一素", no meat dish the diners can eat |
| Named ingredient (`named_ingredient`) | A "must use" or "every dish uses" ingredient cannot be met | An ingredient is named | "全做鸡蛋", no eggs in the fridge |

How each is decided:

- **"Safe"** means what search allows: allergy and dietary exclusions apply, and dishes left out for unknown allergens do not count.
- **"Makeable"** follows the stock rules in `docs/planner-stock-rules.md`, exactly as validation (H1) and the plan card judge it.
- **The whole library is checked**, not the candidate list.
- **The time limit in fridge-only mode** is judged on the makeable recipes, because only those reach the candidate list and the planner's searches. If every makeable soup is over the limit, the check stops with the time limit even when a quicker soup exists that would need shopping. When nothing of a type is makeable (already "not enough from the fridge"), the time limit is judged on every safe recipe, so the user also learns whether shopping alone would meet it.
- **Dish types and counts** come from the request: meat and vegetable dishes are checked together unless the user named a split; a split the parse marked as a minimum ("三个菜，一荤一素") is compared as "at least".
- **When in doubt, the request goes through.** Ingredients shared between two dishes are not modelled, a time the recipe does not state counts as fitting, and an ingredient name the dictionary does not know is only checked against the library. A wrong early stop on a request that could be met is the failure this check must never cause.
- **An empty fridge** is reported together with the reasons that do not depend on stock (cookware, time limit, library size), so the user does not stock up only to meet a second problem. The stock-based reasons are not repeated, since they would only restate the empty fridge.

## 3. What the user sees when it stops

The plan card shows no dishes. It shows, in the user's language:

- **the reason in plain words, with numbers** where they apply (fixed strings in `reason_text`, `graph.py`), for example:
  - "There is nothing in the fridge to cook with" / "冰箱里没有可以用来做菜的食材"
  - "None of your recipes can be made with the cookware you have"
  - "By the current fridge you can make 1 vegetable dish; this meal needs 2 vegetable dishes" / "按冰箱现有的食材只能做 1 道素菜，这餐需要 2 道素菜"
  - "Your library has 0 meat dishes you can eat; this meal needs at least 1 meat dish" ("at least" / "至少" when the parse marked the count as a minimum)
  - "No 鸡蛋 in the fridge" / "冰箱里没有鸡蛋" (only when eggs are really absent from stock), or "No dish that can be made from the fridge uses 鸡蛋" / "用现有的食材做不出用到鸡蛋的菜" (eggs in stock, but no makeable dish uses them)
  - "Only 0 vegetable dishes with 鸡蛋 can be made; this meal needs 2 vegetable dishes"
- **next-step buttons that fit the reason** (Section 4);
- **a fixed note**, not model text.

The planning record gets a `feasibility` event listing the reasons (names replaced as in the rest of the record), and the run is marked failed.

## 4. Next steps by reason

| Reason or unmet rule | Next steps |
|---|---|
| Empty fridge, missing ingredients (H1), not enough from the fridge | Allow shopping, Fewer dishes, Update inventory (link) |
| No cookware, missing cookware (H3) | Register cookware (link to the cookware section), Fewer dishes |
| Time limit, over time (H6) | Drop the time limit, Fewer dishes |
| Not enough in the library, wrong dish count (H4) alone | Fewer dishes, Import recipes (link) |
| Named ingredient, H7 | The named ingredients are optional, Fewer dishes, Allow shopping (fridge-only), Update inventory (fridge-only), Import recipes |
| Every attempt broke an allergy or diet rule (H2) | Fewer dishes, Allow shopping (fridge-only) |
| Only an invented or repeated recipe id (H5) | none; the note then promises no changes |

Two buttons appear only when they can help:

- **Fewer dishes** only when the meal has more than one dish in total.
- **Any mix of meat and vegetable dishes** only when the user named a meat or vegetable count and that category is the one short.

"Allow shopping", "Fewer dishes", "Drop the time limit", "Any mix of meat and vegetable dishes", and "The named ingredients are optional" send a short fixed message. "Update inventory", "Register cookware", and "Import recipes" are links to the inventory and import pages.

## 5. How it relates to validation

| | Feasibility check | Validation (H1-H7) |
|---|---|---|
| Asks | Could any plan meet this request? | Does this particular plan meet it? |
| Runs | Before the planner, and before each retry | After the planner proposes dishes |
| Looks at | The whole library (and the fridge) | The dishes the model chose |
| On failure | Stops planning early, with reasons | Retries with the violations, up to twice, then shows the closest safe attempt or no plan |

The feasibility check saves model calls on requests that cannot succeed; it never replaces validation. A plan that passes the feasibility check is still validated in full.

## 6. Examples

Fridge-only unless stated.

| Situation | Request | Result |
|---|---|---|
| Fridge empty, only seasonings always in stock | 两道素菜 | Stops: empty fridge. Allow shopping, Fewer dishes, Update inventory |
| Same, and no wok owned | 两道素菜 | Stops: empty fridge **and** no cookware. The union of both sets of next steps |
| Eggs always in stock; 蒸蛋羹 (eggs, salt) is the only makeable dish | 一道素菜 | Goes ahead |
| Same | 两道素菜 | Stops: "By the current fridge you can make 1 vegetable dish; this meal needs 2 vegetable dishes" |
| No meat dish the diners can eat | 一荤一素 (shopping allowed) | Stops: not enough in the library. Fewer dishes, Any mix of meat and vegetable dishes, Import recipes |
| Eggs absent from stock | 全做鸡蛋, 两道素菜 | Stops: "No 鸡蛋 in the fridge". The named ingredients are optional, Fewer dishes, Allow shopping, Update inventory, Import recipes |
| Eggs in stock at count 0 | 全做鸡蛋, 两道素菜 | Stops: "Only 0 vegetable dishes with 鸡蛋 can be made; this meal needs 2 vegetable dishes", with the same next steps |
| 10-minute limit, every soup takes 40 minutes (stated) | 一菜一汤, 10分钟 (shopping allowed) | Stops: time limit. Drop the time limit, Fewer dishes |
| 30-minute limit; the only makeable soup takes 60 minutes, a 10-minute soup needs tomatoes not in the fridge | 一汤, 30分钟 | Stops: time limit (the quick soup cannot be made from the fridge) |
| Enough makeable dishes of each type | 两菜一汤 | Goes ahead to the candidate list and the planner |
