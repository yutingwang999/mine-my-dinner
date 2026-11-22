# What Each Model Sees

A planning conversation calls up to four models: the route, the parse, the planner, and the revise step. This document says, for each one, what it receives, what it never receives, and why, with complete example inputs. It describes the code as of Phase 1 plan 3b-1b, with the prompt versions `route_v4`, `parse_v7`, `planner_v5`, and `revise_v4` (`PROMPT_VERSIONS` in `backend/minemydinner/agent/request.py`). The design is in `docs/superpowers/specs/2026-11-21-phase-1-plan-3b1b-revise-fixes-design.md`, Section 5.8. Related: `docs/planner-request-and-counts.md` (the steps and the counts), `docs/planner-shortlist-and-ranking.md` (the candidate list), `docs/planner-feasibility-check.md`, and `docs/planner-stock-rules.md`.

**Whenever a model's input changes, this document is updated in the same change.**

## 1. Four principles

1. **Hard rules are code's job.** Counts, dietary rules, stock, and cookware are checked by code, so a model is told what to meet, not left to derive it from raw words.
2. **A request is read once.** Only the parse reads the user's words and turns them into a structured request. Later steps use that request, so two models cannot read "两三个菜" two ways. The revise step is the one exception: it also gets the words of this turn, because it must quote them (Section 8).
3. **Privacy.** Saved nicknames never reach a model, nothing on the diner card is sent, and diners appear only as anonymous "Diner N" rule lines.
4. **Cost and stability.** Shorter inputs cost less, and an opening that is the same on every attempt of a run is served from the provider's prompt cache.

## 2. Summary

| Model | Receives | Never receives | Why |
|---|---|---|---|
| Route (`route_v4`) | The route prompt and the conversation's messages: every user message and every assistant reply's text | Cards (plan, diner, choice), the assistant's questions, the app note about the current plan | Telling "change this plan" from "plan again" needs context; the answer is one word, so the whole conversation is cheap |
| Parse (`parse_v7`) | The parse prompt, the conversation with the assistant's questions placed as assistant messages, and on a revise turn the app note line describing the current plan | The diner card's contents, the rest of the plan card, the dietary rules on record | It merges several turns into one full request; a question lets a short answer ("两个") attach to it; the plan line lets "再加一道" count from the plan; the card is kept from models for privacy |
| Planner (`planner_v5`) | The planner prompt, one opening message (the shortlist, then the code-written request summary with anonymous diner rule lines), a retry message after a failed attempt, and its own tool calls and results within the attempt | The conversation, the user's words (except the wish, the named ingredients, and the one-meal avoidance as the request holds them), earlier attempts' tool calls | The request is already read and completed; raw words could pull it away from what code checks; the fixed opening is cached across attempts |
| Revise (`revise_v4`) | The revise prompt, an opening with the same shortlist and summary under "Revise this meal.", the current plan one dish per line with its marks, the user's words of this turn, a retry message, and its own tool calls | Earlier turns, the plan card's other fields (diners line, warnings, unmet list), earlier attempts' tool calls | It must see the plan it changes and the exact words it quotes (code checks quotes against them); earlier conditions are in the summary |

Every model: saved nicknames are replaced by "Diner N" labels before the call (`replace_nicknames` in `agent/labels.py`, a pre-hook of the LLM client) and restored in what the user sees (`restore_labels`, the post-hook). The user is always "Diner 1" in a rule line; "Diner 1" is restored to "你" / "you".

## 3. What every call carries

- **A system message:** the whole prompt file of that model, `backend/minemydinner/agent/prompts/<version>.md`. The examples below show it as `<the ... prompt file>`.
- **The answer schema.** The route and the parse are structured calls (`invoke_structured`): the schema (`Intent`, `ParsedRequest`) and its field descriptions are sent with the call. The planner and the revise step are agents: the six tool schemas (`get_inventory`, `search_recipes`, `get_recipe`, `compute_missing`, `check_cookware`, `get_preferences`; `PLANNER_TOOLS` in `planning/tools.py`) and the final-answer tool (`PlanProposal` or `RevisionProposal`) are bound to the model, field descriptions included. A schema description is model input too. For the planner and the revise step, changing a tool schema or the final-answer schema changes the tools version (`TOOLS_VERSION`, `REVISE_TOOLS_VERSION` in `agent/planner.py`); the route's and the parse's schemas are not part of it.
- **Nothing outside the messages and schemas.** No hidden context: what the examples show is all the model gets, apart from the prompt file and the schemas.

## 4. The example conversation

All examples are generated from the code, not written by hand. A test-only script (not committed) runs the real graph on the test database inside a rolled-back transaction, loads the fictional dishes of `backend/eval_data/planning/library_revise.yaml`, owns every cookware, and replaces the model provider with a scripted model that records every message list it is sent. No real model is called. The conversation:

1. The user: "今晚两个人，三个菜，可以买菜". The diner card is answered with the user and one other person with no limits.
2. The planner (scripted) proposes 青椒肉丝, 土豆炖牛腩, 红烧茄子 with the note "先炖土豆炖牛腩，再快炒青椒肉丝和红烧茄子。".
3. The user: "茄子这道不要". The revise step (scripted) drops 红烧茄子; the reply is "去掉了红烧茄子。" and the plan has two dishes.

The assistant's note and the scripted answers are made up; the inputs around them are exactly what the code sends. Recipe ids are the test database's and differ between runs.

## 5. Route (`route_v4`)

**Built by** `route_intent` in `agent/graph.py`: `[SystemMessage(route prompt), *state["messages"]]`.

**Receives:** every message of the thread in order: the user's messages (including an answer typed to the ask-first question, which is added as a user message) and the assistant's reply texts (plan notes, the code-written revision sentences, failed-turn notes, the "other" reply). It answers `plan`, `revise`, `unsure`, or `other`; code turns `revise` or `unsure` into `plan` when there is no current plan.

**Never receives:** the plan card or any card, the assistant's questions (the chat shows them, but the thread keeps them beside the messages, not as messages), and the app note about the current plan. Saved nicknames arrive as "Diner N".

**Not called** for "plan again" or a next-action button (Section 9).

Example, the second turn:

```
[system] <the route_v4 prompt file>
[human] 今晚两个人，三个菜，可以买菜
[ai] 先炖土豆炖牛腩，再快炒青椒肉丝和红烧茄子。
[human] 茄子这道不要
```

The first turn's route input is the system message and the first user message only.

## 6. Parse (`parse_v7`)

**Built by** `parse_request` in `agent/graph.py`: the thread's messages with each recorded question that has text inserted as an assistant message where it was asked (`parse_messages` in `agent/questions.py`), then, on a revise turn, one more user-role line written by code (`current_plan_line`): `[App note, not written by the user] Current plan: <counts>: <titles>`.

**Receives:**

- Every user message and assistant reply, as the route does.
- The text questions: the ask-first question ("要规划这一餐，请告诉我……") and the count problem question (counts that contradict each other, or more than 12 items in total), in the wording the chat shows. The request-style question, the diner card, and the revise-or-plan choice card are forms and have no text, so they are not inserted.
- On a revise turn, the app note: the plan's dish counts in the parse's own terms ("dishes" are meat plus vegetable dishes) and its titles. No diner, no lock, no conflict. `parse_v7` counts relative words from it ("再加一道" is the note's count plus one) and never takes it for the user's words: `counts_stated_now` is true only when the newest user message itself states a count.

**Answers** with a `ParsedRequest`. The fields that reach later models: the counts, `mode`, `time_limit_min`, `query` (the taste wish, in the user's words), `must_use`, `prefer_use`, `all_use`, `exclude_recent_days`, and since `parse_v7` `meal_avoid`: the ingredients or ingredient groups the user does not want at this meal only ("今天不想吃肉" is `["肉"]`), never taste words (those go to `query`) and never a lasting rule of a person (those go to `self_rules`, `friend_rules`, or `guests` and may bring the diner card back). Code turns `meal_avoid` into this meal's own rules (diner key `meal`, no person and no profile; `with_meal_avoid` in `agent/plan_card.py`).

**Never receives:** the diner card's contents (who was selected, the rules ticked), the dietary rules on record, the plan card beyond the app note, and saved nicknames (they arrive as "Diner N"). The parse runs without the post-hook, so a `friend_rules` code stays a label for code to resolve; code restores the labels in every other field.

**Not called** for "plan again" or a next-action button on a card that keeps its request (Section 9).

Example, the second turn:

```
[system] <the parse_v7 prompt file>
[human] 今晚两个人，三个菜，可以买菜
[ai] 先炖土豆炖牛腩，再快炒青椒肉丝和红烧茄子。
[human] 茄子这道不要
[human] [App note, not written by the user] Current plan: 3 dishes (2 meat, 1 vegetable): 青椒肉丝, 土豆炖牛腩, 红烧茄子
```

The first turn's parse input is the system message and "今晚两个人，三个菜，可以买菜".

## 7. Planner (`planner_v5`)

**Built by** `plan_attempt` in `agent/graph.py`. The opening (`build_opening`, `planner_opening`, `request_summary`) is built once per run and stored, so every attempt sends the same bytes:

1. `Shortlist: candidates from the user's library for each dish type this meal needs, best first, as JSON keyed by dish type.`, then the shortlist as compact JSON: per dish type `complete` and the candidates, each with `id`, `title`, `time_min`, `time_source`, `servings`, `missing` (ingredients not at home), `missing_cookware`, `uses_expiring`, and `dislikes` (diner codes only). How it is built: `docs/planner-shortlist-and-ranking.md`.
2. `Plan this meal.` and the request summary, written by code from the spec, not from the user's words:
   - `Reply locale`, `Mode`, `Headcount`, `Dishes (meat plus vegetable)`, `Split` (the meat and vegetable ranges in words), `Soups`, `Staple foods ... desserts ... snacks`, the fixed line `Every count is exact unless it says at least, at most, or a range.`, `Time limit`, `Filled by default` (the fields code assumed).
   - Only when present: `The user feels like: <query>`, `Must use in at least one dish: ...`, `Every dish must use: ...`, `Prefer using: ...`, `Exclude dishes from the last N days`.
   - `Diners' dietary limits:` one line per rule of a selected diner, `Diner N: allergy X` / `Diner N: does not eat X` / `Diner N: dislikes X`; one line per unconfirmed person, `Diner N: dietary limits unconfirmed`; and for a one-meal avoidance one line `This meal only, not a diner's rule: leave out <the words as said>`. With none of these, `- none recorded`. A person answered "no limits" on the diner card has no line.

**A retry** after a failed check adds one user message (`retry_message`): `Your proposal [ids] failed these checks. Propose a new plan that fixes each one:`, one line per violation in plain English (a one-meal avoidance is worded as "left out of this meal (the user's wish for this meal only)", never as a diner's limit), then `The previous attempt:` with the shortlist ids offered, the ids proposed, and the missing items per proposed recipe. When the attempt had no answer and code picked a fallback plan from the shortlist, the header says so ("You gave no final answer, so code picked [...]"). Each attempt starts again from the opening (and the retry); the previous attempt's tool calls are not sent.

**Within an attempt** the planner also sees its own tool calls and their results (search results carry diner codes, never names). A search result also has `over_time_limit` (since plan 3b-1b development run 1, design 5.2): the dishes the search's query (the model's own, else the request's) or the request's own query names by their whole title that the request's time limit left out, as `{id, title, time_min}`, after the other hard rules. They are never hits and cannot be used under the limit; the list exists so that a model never calls a library dish "not in the library". The revise step gets the same tool; when a revision keeps the plan and its searches listed such a dish, code offers `drop_time_limit` and says how long the dish takes.

**Answers** with a `PlanProposal`: `recipe_ids`, `note`, and since `planner_v5` `wish_met` (true or false when the summary has a `The user feels like:` line, left out otherwise) and `wish_unmet_reason` (a short reason when `wish_met` is false). When `wish_met` is false, code puts its own fixed sentence first in the reply ("菜谱库里没有符合“<wish>”的菜，先按其他要求给你配了这份。") and adds the next actions that fit; `wish_unmet_reason` is kept in the run's record and is not shown. When an attempt ends with no answer (a call limit or the token budget), code falls back to the shortlist's usable dishes, and with a wish it counts as not met.

**Never receives:** the conversation, the user's words beyond the request fields above, saved nicknames, guests' names (guests are "Diner N" too), the diner card, earlier runs.

Example, the first turn (complete, apart from the system message and the bound schemas):

```
[system] <the planner_v5 prompt file>
[human]
Shortlist: candidates from the user's library for each dish type this meal needs, best first, as JSON keyed by dish type.
{"meat dish":{"complete":false,"candidates":[{"id":759,"title":"孜然羊排","time_min":40,"time_source":"estimated","servings":2,"missing":["羊排"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":768,"title":"家常烧肉","time_min":50,"time_source":"estimated","servings":3,"missing":["五花肉","白萝卜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":767,"title":"辣椒炒肉","time_min":15,"time_source":"stated","servings":2,"missing":["猪肉","辣椒"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":758,"title":"葱爆羊肉","time_min":15,"time_source":"stated","servings":2,"missing":["羊肉","葱"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":757,"title":"腰果鸡丁","time_min":20,"time_source":"stated","servings":2,"missing":["鸡胸肉","腰果"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":756,"title":"油焖大虾","time_min":20,"time_source":"stated","servings":2,"missing":["虾","葱"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]}]},"vegetable dish":{"complete":false,"candidates":[{"id":744,"title":"鸡蛋饼","time_min":10,"time_source":"estimated","servings":1,"missing":["鸡蛋"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":743,"title":"白煮蛋","time_min":12,"time_source":"stated","servings":1,"missing":["鸡蛋"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":769,"title":"清炒蘑菇","time_min":10,"time_source":"stated","servings":2,"missing":["蘑菇","蒜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":765,"title":"松仁玉米","time_min":15,"time_source":"stated","servings":2,"missing":["玉米","松子"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":761,"title":"清炒菠菜","time_min":8,"time_source":"stated","servings":2,"missing":["菠菜","蒜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":750,"title":"青椒土豆片","time_min":15,"time_source":"estimated","servings":2,"missing":["土豆","青椒"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]}]}}

Plan this meal.
Reply locale: zh
Mode: shopping
Headcount: 2
Dishes (meat plus vegetable): 3
Split: none (any mix of meat and vegetable dishes)
Soups: 0
Staple foods: 0, desserts: 0, snacks: 0
Every count is exact unless it says at least, at most, or a range.
Time limit: none
Filled by default: nothing
Diners' dietary limits:
- none recorded
```

(The scripted planner answered with dishes that are not on the shortlist: the planner is not limited to it and can search with its tools. The test library has no stock, so every candidate lists what to buy.)

## 8. Revise (`revise_v4`)

**Built by** `revise_attempt` in `agent/graph.py` with `revision_opening` and `current_plan_text` in `agent/revise_step.py`. The opening is built once per run:

1. The same shortlist and request summary as the planner's, under the heading `Revise this meal.` instead of `Plan this meal.`. The summary is the revision's: its counts are the current plan's own counts (exact, hand edits included) unless the message states a count, and its other conditions carry over from the plan's request unless this turn states new ones (`revision_spec` in `agent/revision.py`; `docs/planner-request-and-counts.md`, Section 7). A one-meal avoidance in force adds the `This meal only, not a diner's rule: leave out ...` line.
2. `Current plan:` and one line per dish: `- <id> <title> (<dish type>; <main ingredients>)`, then its marks. The main ingredients are the lines marked main, else the first three required lines.
3. `The user's message: <the user's words of this turn>` (all user messages since the last assistant reply, joined by line breaks; on a button turn the words of the failed turn whose card offered the button, then the button's fixed text on its own line, because the earlier wish is still the user's words). Code checks the quotes against this same text.

### The marks on a dish line

Written by `current_plan_text`; the strings are exactly these:

| Mark | When | What it allows |
|---|---|---|
| (none) | A dish no one has touched by hand | A general wish may replace it (at most half of the dishes that are not locked, rounded up) |
| `LOCKED` | The user added it by hand (the card's `locked` list) | A general wish never removes it; the user's words naming it, a scope that covers it, or a one-meal avoidance it breaks may change it |
| `LOCKED KEPT CONFLICT` | The dish breaks a diner's rule and the user confirmed keeping it (an override on the card), whether it was added by hand or planned; an override locks it (`locked_ids` in `agent/edits.py`) | As `LOCKED`; words that point at "the dishes that do not suit" ("不适合的菜拿掉") also name it; while it stays, its conflict is a warning, not a failure |
| `OPEN CONFLICT` | The dish breaks a diner's rule and the user has not confirmed it (red on the card), for example after a rule was saved since the plan was shown | It must be replaced: it fails the check (H2) until it is; its removal needs no quote and does not count toward the half limit |
| `BREAKS THIS MEAL'S WISH` (`BREAKS_MEAL_WISH` in `agent/revise_step.py`) | The dish breaks this meal's one-meal avoidance (`meal_avoid`), found by checking the plan against the meal's own rules at the start of the revision | It is replaced, never dropped, locked or not, possibly by another dish type; its removal needs no quote and does not count toward the half limit; the number of dishes stays |

The first four are exclusive, in that order of precedence (`OPEN CONFLICT` wins over `LOCKED KEPT CONFLICT`, which wins over `LOCKED`); `BREAKS THIS MEAL'S WISH` is appended to any of them. The note to the user must never repeat these words, nor ids (the `note` field's description says so).

**A retry** adds one user message (`revise_retry_message`): `Your revision (remove [...], add [...]) failed these checks. Propose a new revision of the current plan that fixes each one:`, the revision problems (R1-R9: quotes, locks, change size, the change list's form, type pairing, add-only, an empty plan) and the violations, `The previous attempt:` as for the planner, and, when the counts or the dish types are off, a line `To fix the counts: ...` or `To fix the dish types: ...` that names the dishes of the right type that may be replaced. With no answer at all it starts `You gave no revision. Answer with the RevisionProposal tool:`.

**Answers** with a `RevisionProposal`: `target` (`named`, `scope`, `general`, `add`, `unclear`), `remove` (each with `id`, `quote`, `mode` `drop` or `replace`, and an optional named `replace_with` with its quote), `add`, `scope_quote`, `excepted`, and `note`. Code checks every quote against the message, computes the revision's counts from the change list (a drop shrinks the plan, `docs/planner-request-and-counts.md`, Section 7), and writes the sentence about what changed itself ("把A换成了B", "去掉了A", "……是你手动加的，没有改动"); the model's note only says why the new dishes fit.

**Never receives:** earlier turns (their conditions are in the summary), the assistant's earlier replies, the plan card's diners line, warnings and unmet list, saved nicknames, and the previous attempt's tool calls.

Example, the second turn of the conversation (complete apart from the system message and the bound schemas):

```
[system] <the revise_v4 prompt file>
[human]
Shortlist: candidates from the user's library for each dish type this meal needs, best first, as JSON keyed by dish type.
{"meat dish":{"complete":false,"candidates":[{"id":759,"title":"孜然羊排","time_min":40,"time_source":"estimated","servings":2,"missing":["羊排"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":768,"title":"家常烧肉","time_min":50,"time_source":"estimated","servings":3,"missing":["五花肉","白萝卜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":767,"title":"辣椒炒肉","time_min":15,"time_source":"stated","servings":2,"missing":["猪肉","辣椒"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":758,"title":"葱爆羊肉","time_min":15,"time_source":"stated","servings":2,"missing":["羊肉","葱"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":757,"title":"腰果鸡丁","time_min":20,"time_source":"stated","servings":2,"missing":["鸡胸肉","腰果"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]}]},"vegetable dish":{"complete":false,"candidates":[{"id":744,"title":"鸡蛋饼","time_min":10,"time_source":"estimated","servings":1,"missing":["鸡蛋"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":743,"title":"白煮蛋","time_min":12,"time_source":"stated","servings":1,"missing":["鸡蛋"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":769,"title":"清炒蘑菇","time_min":10,"time_source":"stated","servings":2,"missing":["蘑菇","蒜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":765,"title":"松仁玉米","time_min":15,"time_source":"stated","servings":2,"missing":["玉米","松子"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":761,"title":"清炒菠菜","time_min":8,"time_source":"stated","servings":2,"missing":["菠菜","蒜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]}]}}

Revise this meal.
Reply locale: zh
Mode: shopping
Headcount: 2
Dishes (meat plus vegetable): 3
Split: 2 meat dishes, 1 vegetable dish
Soups: 0
Staple foods: 0, desserts: 0, snacks: 0
Every count is exact unless it says at least, at most, or a range.
Time limit: none
Filled by default: nothing
Diners' dietary limits:
- none recorded

Current plan:
- 727 青椒肉丝 (meat dish; 猪肉, 青椒)
- 730 土豆炖牛腩 (meat dish; 牛腩, 土豆)
- 734 红烧茄子 (vegetable dish; 茄子)

The user's message: 茄子这道不要
```

The summary says 3 dishes, 2 meat and 1 vegetable: the plan's own counts, because "茄子这道不要" states no count. The scripted answer `{"target": "named", "remove": [{"id": 734, "quote": "茄子这道", "mode": "drop"}], "add": []}` passed: code checked the attempt against the change list's own counts (2 dishes), so the drop shrank the plan. The shortlist is shorter than the planner's (5 per side instead of 6) because the revision's split asks for fewer dishes per side.

### A second example: every mark

The same first turn with two changes on record: the user does not eat 花生, and after the plan was shown the user added 宫保鸡丁 by hand (it has 花生; the user confirmed keeping it), then saved a new rule (does not eat 茄子) and added 蒜蓉西兰花 by hand. The second message is "今天不想吃土豆，换一道吧", which `parse_v7` reads as `meal_avoid: ["土豆"]`. The revise opening, after the shortlist (which no longer offers 青椒土豆片, as it breaks the avoidance):

```
Revise this meal.
Reply locale: zh
Mode: shopping
Headcount: 2
Dishes (meat plus vegetable): 5
Split: 3 meat dishes, 2 vegetable dishes
Soups: 0
Staple foods: 0, desserts: 0, snacks: 0
Every count is exact unless it says at least, at most, or a range.
Time limit: none
Filled by default: nothing
Diners' dietary limits:
- Diner 1: does not eat 花生
- Diner 1: does not eat 茄子
- This meal only, not a diner's rule: leave out 土豆

Current plan:
- 907 青椒肉丝 (meat dish; 猪肉, 青椒)
- 910 土豆炖牛腩 (meat dish; 牛腩, 土豆) BREAKS THIS MEAL'S WISH
- 914 红烧茄子 (vegetable dish; 茄子) OPEN CONFLICT
- 908 宫保鸡丁 (meat dish; 鸡胸肉) LOCKED KEPT CONFLICT
- 911 蒜蓉西兰花 (vegetable dish; 西兰花) LOCKED

The user's message: 今天不想吃土豆，换一道吧
```

"Diner 1" is the user. The scripted answer replaced only 土豆炖牛腩 and left the open conflict, so the retry message was:

```
Your revision (remove [910], add [915]) failed these checks. Propose a new revision of the current plan that fixes each one:
- Recipe 914: 茄子 breaks a dietary limit of Diner 1.

The previous attempt:
- Shortlist ids offered: 939, 948, 947, 938, 937, 936, 924, 923, 949, 945, 941
- Ids proposed: 907, 914, 908, 911, 915
- Missing items per proposed recipe: 907: 猪肉, 青椒; 914: 茄子, 蒜; 908: 鸡胸肉, 花生; 911: 西兰花, 蒜; 915: 鲈鱼, 姜, 葱
```

The parse input of that turn ends with the app note `[App note, not written by the user] Current plan: 5 dishes (3 meat, 2 vegetable): 青椒肉丝, 土豆炖牛腩, 红烧茄子, 宫保鸡丁, 蒜蓉西兰花`: titles only, no marks.

## 9. Which models a turn calls

| Turn | Route | Parse | Planner | Revise |
|---|---|---|---|---|
| A new request, or a message routed `plan` | yes | yes | yes, unless code stops first (a question, the diner card left open, or the feasibility check) | no |
| A message routed `revise` with a current plan | yes | yes | only when a lasting rule or a change in who eats brings the diner card, then the meal is planned anew | yes |
| A message routed `unsure` | yes | after the choice card | per the choice | per the choice |
| A message routed `other` | yes | no | no | no |
| "Plan again" ("按同样的要求重新规划") | no | no: the previous request is reused | yes | no |
| A next-action button on a card that keeps its request | no | no | on a new plan's card (no plan, early stop, failed new plan) | on a failed revision's card (the plan was kept) |
| A next-action button on a card saved before cards kept their request | no | yes | yes | no |

**Button turns** (plan 3b-1b, `agent/actions.py`): a button sends a fixed message (`ACTION_MESSAGES` in `agent/request.py`). Code takes the request of the card that offered it, changes one field (`switch_to_shopping`, `fewer_dishes`, `drop_time_limit`, `relax_split`, `relax_named`), and sends the turn on with no route call, no parse call, and no ask-first question. The planner or the revise step then reads that changed request through the ordinary summary; neither is told a button was pressed, except that on a revision the revise step's `The user's message:` line is the failed turn's words followed by the button's text (since the first fix round after development run 1, so a named re-attempt may quote the earlier wish; the run's record keeps `button` and the turn's own message, the button's text). Example: after the failed revision above, the card offered `fewer_dishes`; pressing it ("少做几道菜") made revise calls only (three attempts here, because the scripted answer kept failing), and their opening had `Dishes (meat plus vegetable): 4`, `Split: 2 to 3 meat dishes, 1 to 2 vegetable dishes`, the same `Current plan:` lines and marks, and `The user's message:` with the failed turn's words, then `少做几道菜` on the next line (the example ran before the earlier words were added). The route and the parse of later turns see the button's text as a user message, as the chat shows it.

## 10. Privacy: a saved friend

A saved friend named in the message is replaced before any call. With a saved friend who dislikes 茄子, the user writes "今晚我和<the friend's nickname>吃，三个菜，可以买菜" and selects the friend on the diner card. The route and the parse receive:

```
[human] 今晚我和Diner 2吃，三个菜，可以买菜
```

and the planner's summary ends:

```
Diners' dietary limits:
- Diner 2: dislikes 茄子
```

The nickname appears in no model input and in no planning record, and the label appears in nothing the client receives: a stored card keeps the request it was built from (for its buttons), whose `friend_rules` codes are labels such as "Diner 2", but the chat API sends every card without `request` and `plan_kept` (`client_card` in `agent/plan_card.py`); the user sees the friend's nickname again in the reply (post-hook). A guest who is not saved has no nickname to hide: wherever the user's own words go (the route, the parse, the revise step's message line) the name is as typed, while rule lines and tool results show the guest only as "Diner N". The run's record marks such a run (`has_temporary_guests`).
