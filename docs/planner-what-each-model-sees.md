# What Each Model Sees

A planning conversation calls up to five models: the route, the parse, the planner, the reader, and the dish picker. This document says, for each one, what it receives, what it never receives, and why, with complete example inputs. It describes the code as of Phase 1 plan 3b-2 (one), with the prompt versions `route_v6`, `parse_v8`, `planner_v5`, `follow_up_v1`, and `pick_v1` (`PROMPT_VERSIONS` in `backend/minemydinner/agent/request.py`). The design is in `docs/superpowers/specs/2026-11-22-phase-1-plan-3b2-revise-simplification-design.md`, Sections 5.1-5.8. The revise step of plans 3b-1 and 3b-1b (`revise_v4`) is retired (Section 13). Related: `docs/planner-request-and-counts.md` (the steps and the counts), `docs/planner-shortlist-and-ranking.md` (the candidate list), `docs/planner-feasibility-check.md`, and `docs/planner-stock-rules.md`.

**Whenever a model's input changes, this document is updated in the same change.**

## 1. Four principles

1. **Hard rules are code's job.** Counts, dietary rules, stock, cookware, and locks are checked by code, so a model is told what to meet, not left to derive it from raw words. On a follow-up, code also decides which dishes change (the frame, Section 10); a model only fills what code leaves open.
2. **A message is read once.** A thread's first request is read by the parse; a follow-up on a plan with dishes is read by the reader, with no route call and, for a change, no parse call. Later steps use that reading or that request, so two models cannot read "两三个菜" two ways. The picker gets the user's words only as the wish (and on a button turn, the offering turn's words as the wish's context, Section 12); it never reads the message to decide what changes.
3. **Privacy.** Saved nicknames never reach a model, nothing on the diner card is sent, and diners appear only as anonymous "Diner N" rule lines. "Diner N" is never shown to the user.
4. **Cost and stability.** Shorter inputs cost less, and an opening that is the same on every attempt of a run is served from the provider's prompt cache.

## 2. Summary

| Model | Called | Receives | Never receives | Why |
|---|---|---|---|---|
| Route (`route_v6`) | A thread's first message, and a message after an early stop or a no-plan reply (a current plan with no dishes) | The route prompt and the conversation's messages: every user message and every assistant reply's text | Cards (plan, diner), the assistant's questions, the app note about the current plan | Telling a new meal from anything else needs context; the answer is one word, so the whole conversation is cheap |
| Parse (`parse_v8`) | The first request; a follow-up the reader reads as `new_meal` or `people`; a message after an early stop | The parse prompt, the conversation with the assistant's questions placed as assistant messages, and on a `people` turn or after an early stop the app note line describing the current plan | The diner card's contents, the rest of the plan card, the dietary rules on record | It merges several turns into one full request; a question lets a short answer ("两个") attach to it; the plan line lets a relative count count from the plan; the card is kept from models for privacy |
| Planner (`planner_v5`) | Every new plan | The planner prompt, one opening message (the shortlist, then the code-written request summary with anonymous diner rule lines), a retry message after a failed attempt, and its own tool calls and results within the attempt | The conversation, the user's words (except the wish, the named ingredients, and the one-meal avoidance as the request holds them), earlier attempts' tool calls | The request is already read and completed; raw words could pull it away from what code checks; the fixed opening is cached across attempts |
| Reader (`follow_up_v1`) | A typed message on a plan with dishes | The reader prompt and one user message: the current plan one dish per line with its marks, the diners' rules as anonymous lines, the meal's current conditions, the whole conversation, and the reply locale | The library (no shortlist, no titles beyond the plan, no tools), the cards, the assistant's questions, saved nicknames | Deciding what a message wants needs the plan, the conditions and the conversation, never recipes; a named dish is passed on as the user's words: code places an exact library title, the picker finds what other words mean |
| Picker (`pick_v1`) | A change whose frame has a slot to fill or a choice to make | The picker prompt and one user message: a shortlist for the dish types it may fill, the current plan with its marks, the diner and meal rules, one line of the meal's conditions (mode, time limit, must-use ingredients), the counts the user stated, the wish in the user's words, what code already decided, the choices and the slots (the dish a slot replaces with the user's words, an add's type, a named dish; a must-use choice or add slot names its ingredient), and the reply locale; a retry message; its own search calls | The conversation, the reading, the rest of the request summary (unstated counts, prefer-use, all-use), saved nicknames, earlier attempts' tool calls | It only chooses dishes inside a frame code built; what changes was decided before it runs |

Every model: saved nicknames are replaced by "Diner N" labels before the call (`replace_nicknames` in `agent/labels.py`, a pre-hook of the LLM client) and restored in what the user sees (`restore_labels`, the post-hook). The user is always "Diner 1" in a rule line; "Diner 1" is restored to "你" / "you". The reader's `reading` sentence and the picker's `note` are shown to the user, and both prompts forbid "Diner N", ids and the plan's marks in them.

## 3. What every call carries

- **A system message:** the whole prompt file of that model, `backend/minemydinner/agent/prompts/<version>.md`. The examples below show it as `<the ... prompt file>`.
- **The answer schema.** The route, the parse and the reader are structured calls (`invoke_structured`): the schema (`Intent`, `ParsedRequest`, `FollowUpReading` in `agent/follow_up.py`) and its field descriptions are sent with the call. The planner and the picker are agents. The planner gets the six tool schemas (`get_inventory`, `search_recipes`, `get_recipe`, `compute_missing`, `check_cookware`, `get_preferences`; `PLANNER_TOOLS` in `planning/tools.py`) and the final-answer tool `PlanProposal`. The picker gets `search_recipes` only (`PICK_TOOLS` in `agent/planner.py`) and the final-answer tool `PickerAnswer` (`agent/picker.py`). Field descriptions are bound too. A schema description is model input. For the planner and the picker, changing a tool schema or the final-answer schema changes the tools version (`TOOLS_VERSION`, `PICK_TOOLS_VERSION` in `agent/planner.py`); the route's, the parse's and the reader's schemas are not part of it.
- **Nothing outside the messages and schemas.** No hidden context: what the examples show is all the model gets, apart from the prompt file and the schemas.

## 4. The example conversations

All examples are generated from the code, not written by hand. A test-only script (not committed) runs the real graph on the test database inside a rolled-back transaction, loads the fictional dishes of `backend/eval_data/planning/library_revise.yaml`, owns every cookware, and replaces the model provider with a scripted model that records every message list it is sent. No real model is called. Each conversation starts the same way:

1. The user: "今晚两个人，三个菜，可以买菜". The diner card is answered with the user and one other person with no limits.
2. The planner (scripted) proposes 青椒肉丝, 土豆炖牛腩, 红烧茄子 with the note "先炖土豆炖牛腩，再快炒青椒肉丝和红烧茄子。".

Then one of these follow-ups:

- **A. "茄子这道不要".** The reader (scripted) answers a `change` that drops 红烧茄子. The frame has nothing to choose, so the picker is not called; the reply is "去掉了红烧茄子。" and the plan has two dishes.
- **B. "茄子换一个".** The reader answers a `change` that replaces 红烧茄子. The frame has one slot in its place (no dish type: the picker chooses the kind of dish), and the picker (scripted) fills it with 清炒菠菜. The reply is "把红烧茄子换成了清炒菠菜。" and the picker's note.
- **C. "今天不想吃土豆，换一道吧"**, with one rule on record: the user does not eat 花生. The reader answers a `change` whose `meal_avoid` is `["土豆"]`. Code finds that 土豆炖牛腩 breaks it and makes a slot in its place; the picker fills it with 腰果鸡丁.
- **D. "我先生以后都不吃羊肉".** The reader answers `people`; the parse runs, and the diner card shows.
- **E. A follow-up the reader reads as a new must-use ingredient,** `conditions: {"must_use": ["豆腐"]}` (no dish of the plan uses 豆腐). The frame makes a "replace 1" choice for 豆腐. The picker (scripted) first answers 清炒菠菜 in place of 红烧茄子, which code rejects and retries; the second answer, 麻婆豆腐, passes.

The assistant's note and the scripted answers are made up; the inputs around them are exactly what the code sends. Recipe ids are the test database's and differ between runs (and between the examples below).

## 5. Route (`route_v6`)

**Built by** `route_intent` in `agent/graph.py`: `[SystemMessage(route prompt), *state["messages"]]`.

**Called** only when the thread has no plan with dishes: a thread's first message, and a message after an early stop or a no-plan reply (those replies are a current plan with no dishes). A typed message on a plan with dishes goes to the reader instead (`_reads_follow_up`); "plan again" and the next-action buttons never call it (Section 12).

**Receives:** every message of the thread in order: the user's messages (including an answer typed to the ask-first question, which is added as a user message) and the assistant's reply texts. It answers `plan`, `revise`, `unsure`, or `other`. Code turns `revise` into `plan` when there is no current plan, and `unsure` into `plan` always (the choice card is gone, Section 13). A `revise` after an early stop is parsed as a follow-up of that request (Section 6).

**Never receives:** the plan card or any card, the assistant's questions (the chat shows them, but the thread keeps them beside the messages, not as messages), and the app note about the current plan. Saved nicknames arrive as "Diner N".

Example, the first turn:

```
[system] <the route_v6 prompt file>
[human] 今晚两个人，三个菜，可以买菜
```

## 6. Parse (`parse_v8`)

**Built by** `parse_request` in `agent/graph.py`: the thread's messages with each recorded question that has text inserted as an assistant message where it was asked (`parse_messages` in `agent/questions.py`), then, on a turn with intent `revise`, one more user-role line written by code (`current_plan_line`): `[App note, not written by the user] Current plan: <counts>: <titles>` (`no dishes` after an early stop).

**Called** for a thread's first request, after an early stop, and for a follow-up on a plan with dishes only when the reader reads it as `new_meal` (it is parsed as a new request: no app note, the ask-first question may be asked) or as `people` (it is parsed as a follow-up: the app note, unstated fields filled from the plan's request by `carry_unstated`, and the diner card always shows; the meal is then planned anew). A `change` reading is never parsed: code builds the request from the reading (`request_after`, `docs/planner-request-and-counts.md`, Section 7).

**Receives:**

- Every user message and assistant reply, as the route does.
- The text questions: the ask-first question ("要规划这一餐，请告诉我……") and the count problem question (counts that contradict each other, or more than 12 items in total), in the wording the chat shows. The request-style question and the diner card are forms and have no text, so they are not inserted.
- On a `people` turn or after an early stop, the app note: the plan's dish counts in the parse's own terms ("dishes" are meat plus vegetable dishes) and its titles. No diner, no lock, no conflict. `parse_v8` counts relative words from it and never takes it for the user's words: `counts_stated_now` is true only when the newest user message itself states a count.

**Answers** with a `ParsedRequest`. The fields that reach later models: the counts, `mode`, `time_limit_min`, `query` (the taste wish, in the user's words), `must_use`, `prefer_use`, `all_use`, `exclude_recent_days`, and `meal_avoid`: the ingredients or ingredient groups the user does not want at this meal only ("今天不想吃肉" is `["肉"]`), never taste words (those go to `query`) and never a lasting rule of a person (those go to `self_rules`, `friend_rules`, or `guests` and may bring the diner card back). Code turns `meal_avoid` into this meal's own rules (diner key `meal`, no person and no profile; `with_meal_avoid` in `agent/plan_card.py`).

**Never receives:** the diner card's contents (who was selected, the rules ticked), the dietary rules on record, the plan card beyond the app note, and saved nicknames (they arrive as "Diner N"). The parse runs without the post-hook, so a `friend_rules` code stays a label for code to resolve; code restores the labels in every other field.

Example, the first turn: the system message and "今晚两个人，三个菜，可以买菜". Example, follow-up D (read as `people`):

```
[system] <the parse_v8 prompt file>
[human] 今晚两个人，三个菜，可以买菜
[ai] 先炖土豆炖牛腩，再快炒青椒肉丝和红烧茄子。
[human] 我先生以后都不吃羊肉
[human] [App note, not written by the user] Current plan: 3 dishes (2 meat, 1 vegetable): 青椒肉丝, 土豆炖牛腩, 红烧茄子
```

## 7. Planner (`planner_v5`)

**Built by** `plan_attempt` in `agent/graph.py`. The opening (`build_opening`, `planner_opening`, `request_summary`) is built once per run and stored, so every attempt sends the same bytes:

1. `Shortlist: candidates from the user's library for each dish type this meal needs, best first, as JSON keyed by dish type.`, then the shortlist as compact JSON: per dish type `complete` and the candidates, each with `id`, `title`, `time_min`, `time_source`, `servings`, `missing` (ingredients not at home), `missing_cookware`, `uses_expiring`, and `dislikes` (diner codes only). How it is built: `docs/planner-shortlist-and-ranking.md`.
2. `Plan this meal.` and the request summary, written by code from the spec, not from the user's words:
   - `Reply locale`, `Mode`, `Headcount`, `Dishes (meat plus vegetable)`, `Split` (the meat and vegetable ranges in words), `Soups`, `Staple foods ... desserts ... snacks`, the fixed line `Every count is exact unless it says at least, at most, or a range.`, `Time limit`, `Filled by default` (the fields code assumed).
   - Only when present: `The user feels like: <query>`, `Must use in at least one dish: ...`, `Every dish must use: ...`, `Prefer using: ...`, `Exclude dishes from the last N days`.
   - `Diners' dietary limits:` one line per rule of a selected diner, `Diner N: allergy X` / `Diner N: does not eat X` / `Diner N: dislikes X`; one line per unconfirmed person, `Diner N: dietary limits unconfirmed`; and for a one-meal avoidance one line `This meal only, not a diner's rule: leave out <the words as said>`. With none of these, `- none recorded`. A person answered "no limits" on the diner card has no line.

**A retry** after a failed check adds one user message (`retry_message`): `Your proposal [ids] failed these checks. Propose a new plan that fixes each one:`, one line per violation in plain English (a one-meal avoidance is worded as "left out of this meal (the user's wish for this meal only)", never as a diner's limit), then `The previous attempt:` with the shortlist ids offered, the ids proposed, and the missing items per proposed recipe. When the attempt had no answer and code picked a fallback plan from the shortlist, the header says so ("You gave no final answer, so code picked [...]"). Each attempt starts again from the opening (and the retry); the previous attempt's tool calls are not sent.

**Within an attempt** the planner also sees its own tool calls and their results (search results carry diner codes, never names). A search result also has `over_time_limit` (since plan 3b-1b development run 1): the dishes the search's query (the model's own, else the request's) or the request's own query names by their whole title that the request's time limit left out, as `{id, title, time_min}`, after the other hard rules. They are never hits and cannot be used under the limit; the list exists so that a model never calls a library dish "not in the library". The picker's search tool is the same. On the change path a dish the user names is placed by code when it equals a library title, and otherwise found by the picker (Section 10); a named dish hidden by the time limit, found either way, gets the reply "how long it takes" and the `drop_time_limit` next action.

**Answers** with a `PlanProposal`: `recipe_ids`, `note`, `wish_met` (true or false when the summary has a `The user feels like:` line, left out otherwise) and `wish_unmet_reason` (a short reason when `wish_met` is false). When `wish_met` is false, code puts its own fixed sentence first in the reply ("菜谱库里没有符合“<wish>”的菜，先按其他要求给你配了这份。") and adds the next actions that fit; `wish_unmet_reason` is kept in the run's record and is not shown. When an attempt ends with no answer (a call limit or the token budget), code falls back to the shortlist's usable dishes, and with a wish it counts as not met.

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

## 8. The marks on a dish line

The reader and the picker see the current plan in the same form (`current_plan_text` in `agent/revise_step.py`): `Current plan:` and one line per dish, `- <id> <title> (<dish type>; <main ingredients>)`, then its marks. The main ingredients are the lines marked main, else the first three required lines. The marks are exactly these strings:

| Mark | When | What code does with it |
|---|---|---|
| (none) | A dish no one has touched by hand | The reader may name it as a dish a wish touches (`wish_targets`); code changes at most half of the unlocked dishes for a wish, rounded up |
| `LOCKED` | The user added it by hand (the card's `locked` list) | Never a candidate of a choice and never a wish target. It leaves only when the reader lists it in `remove` with a quote that code finds in this message (whether the words point at it is the reader's judgment; code does not match them against the dish), or when it breaks a diner rule or this meal's one-meal avoidance. Stock, cookware and the time limit never move it |
| `LOCKED KEPT CONFLICT` | The dish breaks a diner's rule and the user confirmed keeping it (an override on the card), whether it was added by hand or planned; an override locks it (`locked_ids` in `agent/edits.py`) | As `LOCKED`. The confirmed conflict is not a breaking rule (the plan's overrides are carried into the check), so it is not replaced for it |
| `OPEN CONFLICT` | The dish breaks a diner's rule and the user has not confirmed it (red on the card), for example after a rule was saved since the plan was shown | Not locked. Code makes it a replace slot (`breaks_rule`); with no replacement found, the dish is removed and the reply says so |
| `BREAKS THIS MEAL'S WISH` (`BREAKS_MEAL_WISH` in `agent/revise_step.py`) | The dish breaks the one-meal avoidance: in the reader's input, the avoidance in force before the message; in the picker's input, the avoidance after it | Code makes it a replace slot (`breaks_rule`), locked or not; with no replacement found, the dish is removed and the reply says so |

The first four are exclusive, in that order of precedence (`OPEN CONFLICT` wins over `LOCKED KEPT CONFLICT`, which wins over `LOCKED`); `BREAKS THIS MEAL'S WISH` is appended to any of them. The reader is told to list a dish in `remove` only when the message points at it, because code replaces the dishes that break a rule or this meal's avoidance by itself, and a `LOCKED` dish only when the message clearly points at it. Neither model may repeat these words or ids to the user.

## 9. Reader (`follow_up_v1`)

**Built by** `read_follow_up` in `agent/graph.py` with `reader_messages` in `agent/follow_up.py` and `reader_conditions` in `agent/graph.py`: `[SystemMessage(reader prompt), HumanMessage(...)]`. The one user message has five parts, separated by a blank line, in this order:

1. The current plan with its marks (Section 8), the dishes deleted from the library since it was shown left out.
2. `Diners:` and one line per rule of a confirmed diner, `Diner N: allergy X` / `Diner N: does not eat X` / `Diner N: dislikes X`, and `Diner N: dietary limits unconfirmed` per unconfirmed person, or `- no diner rules`. Diner 1 is the user. These are the same anonymous lines the planner gets, from the settled diner set; this meal's one-meal avoidance is not a diner and is left out here (it is a condition, part 3).
3. `Current conditions:` from the request behind the plan (`plan_request`), a value it leaves unstated falling back to the plan's spec: `Headcount`, `Counts the user stated` (only the counts the user stated, with a side's bound; "none (the plan as shown)" otherwise), `Mode`, `Time limit`, `Must use`, `Prefer using`, `Leave out of this meal` (the one-meal avoidance as the plan card keeps it), `Taste wish`.
4. `Conversation:` the whole thread, one line per message, `User: <text>` or `Assistant: <text>` (the assistant's reply texts: plan notes, code's change sentences, the "other" reply, the empty-plan question).
5. `Reply locale: <zh or en>`.

**Called** for every typed message on a plan with dishes; never for "plan again" or a next-action button. One call, no tools, no retry of the same model. A reading that fails schema validation counts as a failed attempt in the call layer (`LLMClient.run_task` via `invoke_structured`): the call moves on to the task's next candidate model when one is configured, and fails (`AllModelsFailed`) when none is left.

**Answers** with a `FollowUpReading`:

- `kind`: `change` (the message changes this plan), `new_meal` (a different meal instead), `people` (a change in who eats, or a lasting rule about someone), `other` (not about this meal's plan).
- `ambiguous` and `reading`: one short sentence in the reply locale saying how the message was read; code shows it only when `ambiguous` is true.
- For `change`: `remove` (dishes of the current plan, each by `id` with `mode` `drop` or `replace`, a `quote` of the user's words that point at it, and for a replace the dish the user named, `with_name`); `add` (a `dish_type` and `count`, or a named dish as `name`); `wish` (a taste or style wish in the user's words) and `wish_targets` (when the wish is about dishes of the plan rather than a dish the message removes or adds, the ids of the dishes it touches, most touched first; possibly empty); `meal_avoid` (the **whole** list of things to leave out of this meal after the message, so a withdrawn avoidance is simply missing); `counts` (only counts the message states, absolute for the whole plan); `conditions` (`mode`, `time_limit_min`, `clear_time_limit`, `must_use`, `prefer_use`, only when stated).

The reading never holds new dishes (the picker's job) or the final counts (code's job). Code acts on `kind` (`after_reading`): `change` goes to the frame (Section 10); `new_meal` and `people` go to the parse (Section 6); `other` gets the fixed "other" reply, the plan and the request stay as they are, and nothing is recorded. The reading is kept in the state (`reading`) and recorded with a change's planning run, as a `model_call` event of step `read_follow_up` and in the run's request.

**Never receives:** the recipe library (no shortlist, no titles beyond the plan's, no tools), the cards (the plan card beyond its dish lines, the diner card), the assistant's questions that are not reply texts, the dietary rules on record beyond the confirmed diners' lines, and saved nicknames: the whole message goes through the pre-hook, so a nickname typed in the conversation arrives as "Diner N", and the diner lines are written with labels from the start. A guest who is not saved has no nickname to hide, so a guest's name appears in the conversation as typed, as it does for the parse.

**Why:** what a message wants depends on the plan it points at, the conditions it may change or withdraw, who eats (so a lasting rule can be told from a wish for this meal), and the conversation before it. It does not depend on recipes; with no library the reader cannot pick dishes, and a named dish is passed on as the user's words: code places it when it equals a library title, and otherwise the picker finds the dish the words mean. This is also the first model that reads the user's words with the confirmed diners' rules in front of it.

Example, follow-up A (complete apart from the bound schema):

```
[system] <the follow_up_v1 prompt file>
[human]
Current plan:
- 1270 青椒肉丝 (meat dish; 猪肉, 青椒)
- 1273 土豆炖牛腩 (meat dish; 牛腩, 土豆)
- 1277 红烧茄子 (vegetable dish; 茄子)

Diners:
- no diner rules

Current conditions:
- Headcount: 2
- Counts the user stated: dishes 3
- Mode: shopping
- Time limit: none
- Must use: nothing
- Prefer using: nothing
- Leave out of this meal: nothing
- Taste wish: none

Conversation:
User: 今晚两个人，三个菜，可以买菜
Assistant: 先炖土豆炖牛腩，再快炒青椒肉丝和红烧茄子。
User: 茄子这道不要

Reply locale: zh
```

The other person answered "no limits" on the diner card, so there is no diner line. The scripted reading was `{"kind": "change", "remove": [{"id": 1277, "mode": "drop", "quote": "茄子这道"}], "reading": "去掉红烧茄子。"}` (every other field empty). Follow-ups B and D send the same input with their own last user line.

Follow-up C differs in the diner part and the last line:

```
Diners:
Diner 1: does not eat 花生
```

```
User: 今天不想吃土豆，换一道吧
```

No dish has a mark: 土豆 is not yet left out of this meal when the message is read, so `Leave out of this meal` still says `nothing`. The scripted reading was `{"kind": "change", "meal_avoid": ["土豆"], "reading": "这顿不放土豆，把土豆炖牛腩换掉。"}`: no `remove`, because code finds the dish that breaks the new avoidance itself.

## 10. The frame (code, no model)

**Built by** `build_change` in `agent/graph.py` with `build_frame` in `agent/frame.py`. No model is called. It takes:

- **The new request.** After a reading, `request_after` (`agent/follow_up.py`): the plan's request with the stated counts, conditions, the whole `meal_avoid` and a stated wish put in (`docs/planner-request-and-counts.md`, Section 7). On a button turn, the request the button built. Its spec comes from `revision_spec` (`agent/revision.py`): the plan's own counts unless the message states counts.
- **The explicit changes.** The reading's `remove`, `add` and `wish_targets` (on a button turn, the offering card's unfinished parts, Section 12).
- **The dishes that break the new request.** `validate_plan` runs on the kept plan against the new request with the plan's overrides carried (`breaking_from`): a diner rule or an unknown allergen, this meal's avoidance, stock in fridge-only mode, cookware, the time limit. A locked dish counts only for a rule or the avoidance.
- **A lookup for named dishes** (`lookup_named`): the library title equal to the name, case and spaces ignored, and nothing else; a found dish carries the hard condition that hides it, if any.

Code checks, it does not interpret (spec 5.4, amended 2026-11-23): which dish the user's words point at, which dish a name means, which dishes a wish touches and what kind of dish takes a dish's place are a model's judgment; code holds the invariants (locks, stated counts, diner rules and this meal's avoidance, stock, cookware, time) and checks every result against them. From these, code decides, in this order:

1. **Named removals.** Each `remove` entry drops its dish or makes a replace slot (no dish type) carrying the reading's quote; a named replacement that equals a library title and is usable is placed directly, one hidden by a hard condition is recorded as missing with the real reason (`over_time_limit`, `breaks_rule`), and any other name makes a named slot (`named`) for the picker. A locked dish whose quote is not in this message is refused (`ignored`, `locked_without_quote`); code does not check that the words name the dish.
2. **Dishes that break the new request.** Each becomes a replace slot, locked ones included for a rule or the avoidance. The reader never lists them.
3. **The wish's targets** (`wish_targets`): each becomes a replace slot for the wish (`for_wish`), unlocked dishes only, at most half of the unlocked dishes rounded up, in the reader's order; a locked target, an id not in the plan and one over the limit are recorded in `ignored` (`wish_target`). A wish with no targets changes nothing on its own; when it guides no new dish either, the reply says the wish matched nothing in the plan. When every target is locked, the reply says those dishes were added or kept by hand and stay.
4. **Adds.** A library title is placed or recorded as missing (hidden by a hard condition); any other name makes a named add slot with no type; a typed add makes one add slot per dish.
5. **The counts.** Without stated counts, the change list sets them (a drop shrinks the plan, a replace keeps it, an add grows it); a replace slot counts as the replaced dish's type. With stated counts, only the stated fields bind: code adds slots or drops dishes to meet them, a drop the picker may choose being a "drop exactly N of these unlocked dishes" choice, and the frame records them (`count_ranges`) so code checks the picker's result.
6. **A new must-use ingredient no dish uses** (validation's H7 against the kept plan, a name the plan's own spec already had excluded): a "replace 1 of these unlocked dishes" choice for each such name, carrying the name (`must_use`). When the frame already has an add slot, the add takes the ingredient's place instead: the first add made for a stated count (else the first add) carries every such name.
7. **An old kept card's wish.** A card saved before cards kept their unfinished parts has only its wish, which names no dish: its button gets a "replace 1 to N of these unlocked dishes" choice, N at most half of the unlocked dishes, rounded up.
8. **Whether any dish is left.** A frame that would leave no dish asks the empty-plan question; no picker is called.

The frame (`Frame`): `keep`, `drops`, `slots` (each with `kind` replace or add, `dish_type` (null for a replace slot and a named add), `replaces`, `reason` `asked` or `breaks_rule`, `for_count`, `must_use`, `words` (the reading's quote), `named` (a name that is no library title), `for_wish`), `choices` (`candidates`, `up_to`, `mode` drop or replace, `dish_type`, `must_use`), `placed`, `missing`, `empty_plan`, `ignored`, `count_drops`, `count_ranges` (the stated counts as `[low, high]`). It is recorded as a `frame` event with the reading used and the breaking dishes. The picker is called only when the frame has a slot or a choice (`needs_picker`), that is when a new dish must be chosen or the picker must choose which dishes go; otherwise code applies the drops and placed dishes alone.

The examples' frames: A `{"keep": [1270, 1273], "drops": [1277], "slots": [], "choices": []}` (no picker); B one slot `{"kind": "replace", "dish_type": null, "replaces": 9, "reason": "asked", "words": "茄子"}`; C one slot `{"kind": "replace", "dish_type": null, "replaces": 778, "reason": "breaks_rule"}`; E no slot and one choice `{"candidates": [820, 823, 827], "up_to": 1, "mode": "replace", "must_use": ["豆腐"]}`.

## 11. Picker (`pick_v1`)

**Built by** `pick_attempts` in `agent/graph.py` with `picker_messages` in `agent/picker.py`; the agent is `build_picker` in `agent/planner.py`. The one user message has these parts, separated by a blank line:

1. The shortlist (`picker_shortlist`): the planner's format, for the dish types the picker may fill only (an add's own type; for a replace slot or a replace choice, which take a dish of any type, the replaced dishes' types and the meat and vegetable dishes; a named add has none, the picker searches for it), built from the new request (its mode, time limit, wish, named ingredients and rules rank and filter it). Without such a type (a drop choice only), `Shortlist: none given; search the library when a slot needs a dish.`
2. The current plan with its marks (Section 8), the marks computed against the new request's one-meal avoidance.
3. `Diner and meal rules:` the diners' anonymous rule lines and, for a one-meal avoidance, `This meal only, not a diner's rule: leave out <words>`; or `- none recorded`.
4. The meal's conditions (`conditions_line`), on every call: `Meal conditions: <shopping allowed | fridge only (new dishes use what is at home)>; time limit: <N minutes | none>; must use in at least one dish: <names | nothing>`. Then, only when the message (or a button) states counts, its own part: `Stated counts (code checks the result): <field>: <exactly N | L to H>; ...` (fields such as `dishes`, `meat dishes`, `soups`).
5. `The user's wish: <the reading's wish, else the request's taste wish, or none>`. On a button turn the offering turn's words are added: `<wish> (the user's earlier message: <words>)`, or `the user's earlier message: <words>` alone.
6. Only when there is something: `Already decided:` with `- Leaving the plan: <ids>` and `- Added by name: <ids>`.
7. Only when there are choices: `Choices (which current dishes go):` one line each, `- Choice <n>: drop exactly <N> of <ids> (<dish type or any type>)` or `- Choice <n>: replace 1 to <N> of <ids> (...); each dish you choose becomes a new slot in its place`; a must-use choice ends `; the new dish must use <names>, which no dish uses yet`.
8. `Slots (new dishes to choose):` one line each, `- Slot <n>: in place of <id>` (no dish type) with `; the user's words: "<quote>"` when the reading quoted the user and `, for the wish` for a wish target, or `- Slot <n>: a <dish type>, added`, or `- Slot <n>: added` for a named add; a named slot ends `; the user named "<name>": the library dish these words mean, or null when none is`; or `- none listed`; an add slot carrying a must-use ingredient ends `; it must use <names>, which no dish uses yet`; with a replace choice, a line saying that its slots are numbered after the listed ones.
9. `Reply locale: <zh or en>`.

**Answers** with a `PickerAnswer`: `choices` (per choice the ids chosen), `slots` (per slot a `recipe_id` or null with a short `reason`), `wish_met` (null with no wish), and `note` (one or two sentences on why the new dishes fit).

**Checks and retries.** Code checks the answer against the frame (`apply_picks`: every choice answered within its candidates and number, every slot answered with a library dish (of its type for a typed add; a replace slot and a named slot take any type), not in the plan, not chosen twice, and the result inside the stated counts: "The user stated exactly 2 vegetable dishes, but with your dishes the plan has 1. ..."; a count is the picker's problem only when its dishes move it out of the stated range and the dishes the frame planned would have kept it in), then `validate_plan` on the result, keeping only the new dishes' rule, stock, cookware and time findings (H2, H1, H3, H6), and a must-use choice or add slot whose ingredient the result still misses (`must_use_misses`: "Choice 0 is for 豆腐, which no dish in the plan uses, but the new dish (854) does not use 豆腐. Choose a dish that uses 豆腐, or answer null for that slot when none fits."; a slot answered null is not a problem). Problems send one retry (`PICK_MAX_ATTEMPTS` is 2): a fresh message list with one more user message, `Your previous answer had these problems. Answer again with each one fixed:` and one line per problem. After the last attempt, its dishes that broke a check or missed the must-use ingredient are left out (their slots count as unfilled); with no usable answer, code makes only the drop choices (`fallback_pick`). Each attempt allows at most two searches (`PICK_SEARCH_LIMIT`) and three model calls. Each attempt is recorded as a `pick_attempt` event with its answer and problems; the run's tools version is `PICK_TOOLS_VERSION`. A named slot's dish that a hard condition hides (time limit, rule, stock, cookware; `hidden_by` in `agent/frame.py`) is no retry problem: the slot is left unfilled with that reason, and the reply says the dish is in the library but why it was not added, with the matching next action, never "not in the library". A named slot the picker itself answers null for is said as not in the library (import offered), and its replaced dish stays; a named slot left empty because no answer was usable (code's fallback) is said as not done this time ("这次没能把…加进来，可以再试一次。"), never as not in the library.

**What code does with the answer.** A slot nothing fills keeps its dish, except a `breaks_rule` slot, whose dish is removed; an add slot for a stated count left unfilled withholds the count's drops (the plan never shrinks unasked). The reply is code's: the change sentences ("去掉了…", "把…换成了…", "加了…"), the reader's sentence when the message was ambiguous, the picker's note, what could not be done, and the unmet wish (`present_change`). A changed answer (dishes left out, a fallback) loses its note and `wish_met`.

**A must-use ingredient no new dish uses.** A replace slot left unfilled keeps its original dish; an add left unfilled adds nothing (and a count's drops made with it are withheld). Only when the result still misses the name (another new dish may use it) does the reply say once that it was not used ("没找到用豆腐做的菜来换红烧茄子，这次没用上豆腐，红烧茄子先留着。" and its English form), and the card offers the next actions of an unfilled slot. The unused name is taken out of the new version's spec and `plan_request`, so the card shows no second H7 warning and a later typed follow-up does not carry it; it stays in the turn's `request`, which the card keeps, so a button on that card tries it again. With no other change, the plan stays as it was.

**Never receives:** the conversation and the user's message (only the wish, and on a button turn the offering turn's words), the reading itself (its kind, quotes and sentence), the rest of the request summary (unstated counts, prefer-use and all-use ingredients: the shortlist reflects them), the diner card, saved nicknames, and the previous attempt's tool calls.

**Why:** what changes is decided before the picker runs, by the reader and code; the picker's judgement is which dishes fit, including what kind of dish takes a dish's place and which library dish a name means. It can never touch a locked dish, and code checks its result against the stated counts, the rules and the conditions, so a swap of another type that breaks a stated count is caught and retried.

Example, follow-up B (complete apart from the system message and the bound schemas):

```
[system] <the pick_v1 prompt file>
[human]
Shortlist: candidates from the user's library for each dish type this meal needs, best first, as JSON keyed by dish type.
{"meat dish":{"complete":false,"candidates":[{"id":34,"title":"孜然羊排","time_min":40,"time_source":"estimated","servings":2,"missing":["羊排"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":43,"title":"家常烧肉","time_min":50,"time_source":"estimated","servings":3,"missing":["五花肉","白萝卜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":42,"title":"辣椒炒肉","time_min":15,"time_source":"stated","servings":2,"missing":["猪肉","辣椒"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":33,"title":"葱爆羊肉","time_min":15,"time_source":"stated","servings":2,"missing":["羊肉","葱"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":32,"title":"腰果鸡丁","time_min":20,"time_source":"stated","servings":2,"missing":["鸡胸肉","腰果"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]}]},"vegetable dish":{"complete":false,"candidates":[{"id":19,"title":"鸡蛋饼","time_min":10,"time_source":"estimated","servings":1,"missing":["鸡蛋"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":18,"title":"白煮蛋","time_min":12,"time_source":"stated","servings":1,"missing":["鸡蛋"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":44,"title":"清炒蘑菇","time_min":10,"time_source":"stated","servings":2,"missing":["蘑菇","蒜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":40,"title":"松仁玉米","time_min":15,"time_source":"stated","servings":2,"missing":["玉米","松子"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]},{"id":36,"title":"清炒菠菜","time_min":8,"time_source":"stated","servings":2,"missing":["菠菜","蒜"],"missing_cookware":[],"uses_expiring":[],"dislikes":[]}]}}

Current plan:
- 2 青椒肉丝 (meat dish; 猪肉, 青椒)
- 5 土豆炖牛腩 (meat dish; 牛腩, 土豆)
- 9 红烧茄子 (vegetable dish; 茄子)

Diner and meal rules:
- none recorded

Meal conditions: shopping allowed; time limit: none; must use in at least one dish: nothing

The user's wish: none

Slots (new dishes to choose):
- Slot 0: in place of 9; the user's words: "茄子"

Reply locale: zh
```

The scripted answer `{"slots": [{"index": 0, "recipe_id": 36}], "note": "清炒菠菜做法简单，和两道肉菜搭配清爽。"}` passed, and the reply was "把红烧茄子换成了清炒菠菜。" then the note.

Example, follow-up C, after the shortlist (meat and vegetable dishes, as in B: the slot takes any type):

```
Current plan:
- 775 青椒肉丝 (meat dish; 猪肉, 青椒)
- 778 土豆炖牛腩 (meat dish; 牛腩, 土豆) BREAKS THIS MEAL'S WISH
- 782 红烧茄子 (vegetable dish; 茄子)

Diner and meal rules:
- Diner 1: does not eat 花生
- This meal only, not a diner's rule: leave out 土豆

Meal conditions: shopping allowed; time limit: none; must use in at least one dish: nothing

The user's wish: none

Slots (new dishes to choose):
- Slot 0: in place of 778

Reply locale: zh
```

The scripted answer filled slot 0 with 腰果鸡丁; the reply was "把土豆炖牛腩换成了腰果鸡丁。" then the note.

Example, follow-up E, after the shortlist (meat and vegetable dishes, the types the choice may free), first attempt:

```
Current plan:
- 820 青椒肉丝 (meat dish; 猪肉, 青椒)
- 823 土豆炖牛腩 (meat dish; 牛腩, 土豆)
- 827 红烧茄子 (vegetable dish; 茄子)

Diner and meal rules:
- none recorded

Meal conditions: shopping allowed; time limit: none; must use in at least one dish: 豆腐

The user's wish: none

Choices (which current dishes go):
- Choice 0: replace 1 to 1 of 820, 823, 827 (any type); each dish you choose becomes a new slot in its place; the new dish must use 豆腐, which no dish uses yet

Slots (new dishes to choose):
- none listed
Slots for the dishes you choose to replace are numbered from 0, in the order of the choices and of the ids you list.

Reply locale: zh
```

The scripted first answer chose 827 and filled slot 0 with 清炒菠菜 (854), which does not use 豆腐. The second attempt sent the same message followed by:

```
[human] Your previous answer had these problems. Answer again with each one fixed:
- Choice 0 is for 豆腐, which no dish in the plan uses, but the new dish (854) does not use 豆腐. Choose a dish that uses 豆腐, or answer null for that slot when none fits.
```

The second answer (麻婆豆腐 in place of 红烧茄子) passed; the reply was "把红烧茄子换成了麻婆豆腐。" then the note.

## 12. Which models a turn calls

| Turn | Route | Reader | Parse | Planner | Picker |
|---|---|---|---|---|---|
| A thread's first message | yes | no | yes | yes, unless code stops first (a question, the diner card left open, or the feasibility check) | no |
| A message after an early stop or a no-plan reply | yes | no | yes (the app note `no dishes` when routed `revise`) | as above | no |
| A typed message on a plan with dishes, read `change` | no | yes | no | no | only when the frame has a slot or a choice |
| ... read `new_meal` | no | yes | yes, as a new request | as on a first message | no |
| ... read `people` | no | yes | yes, with the app note | yes, after the diner card: the meal is planned anew | no |
| ... read `other` | no | yes | no | no | no |
| "Plan again" ("按同样的要求重新规划") | no | no | no: the previous request is reused | yes | no |
| A next-action button on a card that keeps its request | no | no | no | on a new plan's card (no plan, early stop, failed new plan) | on a card whose plan was kept or whose change was done only in part, when the frame has something to choose |
| A next-action button on a card saved before cards kept their request | no | no | yes | yes | no |

So a change turn makes one call (when no new dish and no choice of which dishes go is needed), two (the usual case), and at most about seven with the picker's retry and searches. The picker is also called with no new dish when a stated lower count leaves more unlocked candidates than dishes to drop (a "drop exactly N" choice).

**Button turns** (`agent/actions.py`): a button sends a fixed message (`ACTION_MESSAGES` in `agent/request.py`). Code takes the request of the card that offered it, changes one field (`switch_to_shopping`, `fewer_dishes`, `drop_time_limit`, `relax_split`, `relax_named`), and sends the turn on with no route, reader or parse call, and no ask-first question. On a card whose plan was kept (`plan_kept`) or whose change was done only in part (the card's server-only `unfinished` field), the turn goes through the same frame as a reading: the reading is the card's unfinished parts (`button_reading`: the removals and adds not done and the wish's targets still to change, with the button request's counts when it states counts), compared with the plan the user sees, and the picker fills what it asks for. The picker's wish line carries the offering turn's words (Section 11). The run's record keeps `button` and the turn's own message, the button's text. A later typed message shows the button's text to the reader as a user message, as the chat shows it.

**An older thread** paused on a question during a revise turn before plan 3b-2 is read by the reader when it resumes (`read_paused_turn`).

## 13. The retired revise step

Plans 3b-1 and 3b-1b changed a plan with three models reading the same message: the route (`revise`, `plan`, `unsure`, `other`), the parse (the whole request) and the revise step (`revise_v1` to `revise_v4`, an agent with the planner's tools answering a `RevisionProposal` change list, checked after the fact by the rules R1-R9). An `unsure` route showed a choice card ("在这份方案上改 / 重新规划一顿"). Plan 3b-2 (one) replaced them with one reading, a frame built in code from the same rules, and a picker that only chooses dishes. The route, the choice card, the parse and the revise step are no longer called for a follow-up on a plan with dishes; the choice card and `RevisionProposal` are gone from the code. The prompt files stay as frozen records of the runs that used them (`docs/prompt-versions.md`).

## 14. Privacy: a saved friend

A saved friend named in the message is replaced before any call. With a saved friend who dislikes 茄子, the user writes "今晚我和<the friend's nickname>吃，三个菜，可以买菜" and selects the friend on the diner card. The route and the parse receive:

```
[human] 今晚我和Diner 2吃，三个菜，可以买菜
```

the planner's summary ends:

```
Diners' dietary limits:
- Diner 2: dislikes 茄子
```

and on a follow-up the reader's conversation line is `User: 今晚我和Diner 2吃，三个菜，可以买菜`, with `Diner 2: dislikes 茄子` among its diner lines; the picker's rule lines are the same.

The nickname appears in no model input and in no planning record, and the label appears in nothing the client receives: a stored card keeps the request it was built from (for its buttons), whose `friend_rules` codes are labels such as "Diner 2", but the chat API sends every card without `request`, `plan_kept`, `wish_words`, `unfinished` (a change's unfinished parts and that turn's words) and `meal_avoid` (`client_card` in `agent/plan_card.py`); the user sees the friend's nickname again in the reply (post-hook). A guest who is not saved has no nickname to hide: wherever the user's own words go (the route, the parse, the reader's conversation, the picker's wish line) the name is as typed, while rule lines and tool results show the guest only as "Diner N". The run's record marks such a run (`has_temporary_guests`).
