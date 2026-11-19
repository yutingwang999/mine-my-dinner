# Phase 1 Plan 2b: Several Diners, Nicknames, and Unknown Allergens

Status: approved, and implemented in plan 2b (2026-11-17 to 2026-11-19). Decisions made after approval (during the tasks, evaluation round 8, and the final review) are marked in their sections with their date or "final review"; the code is the reference where a detail differs. The second half of roadmap step 4 (rest of T1.4). Builds on the Phase 1 agent design (`2026-11-13-phase-1-agent-design.md`) and plan 2a (`2026-11-14-phase-1-plan-2a-planner-efficiency-design.md`), which stay the authority for everything this document does not change. Rules for diners come from `docs/design.md` 7.6 (meals for several people), 5.5 (unknown allergens), and 7.5 (H2 and overrides). Inputs from `docs/phase-1-carryover.md`: the T1.4 nickname hooks; "Other diners' restrictions in the request text are not captured"; "Unknown-allergen override ignores the diner"; "Let the parse see the assistant's questions"; "Trim the parse prompt when it next changes"; "The evaluation scenarios were used for tuning"; and the "Diner 2" label in a reply (Outcome of the chat page redesign item).

## 1. Intent

Today a plan knows only the user. Anyone else at the table counts as an unconfirmed diner and gets one generic S8 warning; a friend's saved allergy is never applied, and "me and Alex, who is allergic to peanuts" is not captured at all. Plan 2b lets a meal be planned for several people, each person's allergies and restrictions excluding dishes by code, with the user confirming who is eating before the model chooses dishes. Saved friends' nicknames never reach a model provider. Dishes left out because an ingredient's allergens are unknown can be resolved from the plan card. The parse reads the assistant's own questions next to the answers. A held-out scenario set, written by the user, measures progress on requests no tuning has seen.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Several diners per meal: the user, saved friends, and temporary guests, each with rules; H2 and the search exclusions apply every selected person's rules (design 7.5: the whole table).
2. One confirmation card before planning whenever more than one person eats (decided by the user, whatever the request style).
3. Restrictions stated in the chat about other people are extracted by the parse, shown on the card, and applied only after the user confirms them.
4. Saved friends' nicknames are replaced with codes before every planning model call and restored in what the user sees; no internal code ("Diner 2") appears in a reply.
5. Dishes left out for unknown allergens show the ingredient responsible, and the user can confirm its allergens on the plan card and plan again (decided by the user).
6. The parse sees the assistant's questions; `parse_v4` also drops the duplicated rules and examples listed in the carryover.
7. A held-out scenario set measures the result.

**Non-goals (later)**

- Changing diners on an existing plan with partial replacement ("小李也来" replaces only the dishes that break the rules, design 7.6): T1.5 with `revise_plan`. In 2b a message that changes who eats is a new request and is planned anew.
- Manual plan edits and the H2 overrides they create (design 7.5): T1.5. 2b only fixes the override check (Section 5.8).
- Sorting friends on the card by the most recent saved plan: saved plans arrive in T1.5. Until then the card sorts friends by name.
- Saving friend information inferred by `extract_memories` (T1.6), friend deletion cleanup of saved plans (T1.6).
- Clarifying questions written by the model (carryover, not planned in Phase 1).
- Rules on whole dictionary categories ("吃素") and remembered taste preferences ("最近不吃辣"); see Section 8.

**Anti-goals (a failure even if it technically works)**

- A stated allergy is applied, or dropped, without the user seeing it on the card.
- A saved friend's nickname appears in any model-bound text, planning record, or evaluation export.
- An internal code ("Diner 2", "Guest 1") appears in text shown to the user.
- A diner the user selected stops being checked without notice (design 7.6).
- The card appears for a meal the user eats alone.

## 3. Constraints

Everything in the Phase 1 design and plan 2a still holds. In particular:

- Hard constraints stay in code (`minemydinner/planning/`); prompts only describe them.
- Prompt versions: `parse_v3` and `planner_v4` have records, so the parse change is `parse_v4`. The planner prompt is not changed (Section 5.5 keeps its "Diner N" wording true). `docs/prompt-versions.md` gets a row.
- Paid evaluation runs need the user's approval with a cost estimate first; evaluation and real-app checks use only the test database.
- No new Python or frontend packages are expected.
- Repository files in English; Chinese only in UI translations, dictionary names, and Chinese request text in tests and prompt examples.

## 4. Approaches

**Where the card sits (decided by the user).** (A) Before planning, for every meal with more than one person, whatever the request style. (B) With "plan then adjust", plan at once and show the diners on the plan card for correction. The user chose A: the rules in use are seen before dishes are chosen, and a parsed allergy is never applied unseen; the cost is one click.

**How other people's restrictions are extracted (decided by the user).** (A) The parse extracts them in the same call (`parse_v4`). (B) A separate extraction call when others are mentioned. (C) No model extraction; the user types them on the card, with a local keyword reminder. The user chose A: no extra call, one prompt version to evaluate; the parse prompt grows by the guest rules, partly offset by the trim.

**Where unknown allergens are resolved (decided by the user).** (A) On the plan card: confirm the ingredient's allergens there, then plan again. (B) A link to the dictionary page's unknown list; the change applies from the next plan. The user chose A: the user meets the problem while wanting that dish.

**Who writes the held-out scenarios (decided by the user).** (A) Claude writes them before implementation and never tunes on them. (B) Two sets: a development set Claude writes and may tune on, and a held-out set whose requests the user writes in their own words. (C) As B, with Claude writing both. The user chose B. After this design is approved, Claude gives the user an outline of the cases the held-out set must cover and a suggested count; the user writes the requests.

## 5. Decisions

### 5.1 The graph

```
route_intent → parse_request → after_parse
  ├─ ask_style / ask_question / unclear   (as today)
  ├─ select_diners  [interrupt: the diner card]   (new; Section 5.2)
  └─ load_context → check_feasibility → planner ⇄ tools → validate_plan → present_plan
```

`after_parse` goes to `select_diners` after the existing gates and before `load_context` when the meal has more than one person and the diners are not already settled (below). More than one person means any of: `headcount` ≥ 2, `others_mentioned`, a parsed guest, a saved friend's nickname matched in the current request (Section 5.5), or a stated rule of a friend or guest; a parsed headcount of 1 does not override the others (final review).

**Settled diners carry over in the thread (Claude's call; the change rule decided by the user, 2026-11-18).** The confirmed diner set is kept in the conversation state, together with the headcount the parse gave when the card was confirmed. A later request in the same thread reuses the set without the card unless it states a change in who eats or how many: a saved nickname or guest not in the set, a guest rule not on the set, or a stated headcount different from the one stated when the set was confirmed (a set saved before that headcount was kept compares with its confirmed headcount). Any such change shows the card; nothing is cleared silently. When the set holds anyone besides the user, a parse that says nobody else eats (no others mentioned, no guest, no friend named or given a rule, no headcount above 1) is such a change even with no headcount, so "就我自己吃" parsed without a number still shows the card (decided by the user, 2026-11-19). "就我自己吃" after a set with 小王 therefore shows the card with only the user preselected, and confirming it replaces the set. A total edited on the card does not count as a change: "我和朋友吃饭" (2) confirmed as 3, then "换个辣的" parsed as 2 again, does not ask. "Plan again" (Section 5.7) reuses the parsed request and never shows the card, except when a friend in the settled set was deleted since, and the next-action buttons and follow-ups that state nothing about the diners do not ask again. A thread whose set includes a friend deleted since then asks again (design 7.6: never drop a person silently). A skipped card leaves no set.

When one person eats, nothing changes from today: no card, no mention of friends (design 7.6). **One exception (decided by the user):** when the parse extracts a rule the user states about themselves that is not already in their profile (Section 5.4), the card appears for a solo meal too, showing only the user's row with the proposed rule. A diet word the user states about themselves ("我吃素") shows the same solo card when no set is settled, so the diet-word hint (Section 5.4) is seen; confirming it applies nothing new (final review).

### 5.2 The diner card

One card holds everything design 7.6 asks for, so the user is not interrupted several times in a row (Claude's call):

```
这顿谁吃？   一共 [−] 3 [+] 人
☑ 我        过敏：花生
☑ 小王      忌口：香菜              ← named in the chat, preselected
☐ 老李      忌口：牛肉
── 对话里提到的其他人 ──
☑ Alex     过敏：花生  [改]   ☐ 存到朋友档案（请先征得本人同意）
── 还有 0 位没对上 ──
[确认，开始规划]
```

- **Rows.** The user (selected by default; can be deselected when not eating), every saved friend (friends named in the request preselected, others not), and one row per parsed guest (selected). Each row lists the person's rules.
- **Editing.** Rules can be added and removed on the card. For the user and saved friends this edits their profile through the existing diner and rule API (the same as the people page). A guest's rules apply to this meal only unless "save to friend profiles" is ticked, which creates a friend with the name as given and those rules when the card is confirmed, with the reminder to get the friend's agreement (design 7.6). A guest can be renamed or removed, and the user can add a guest row.
- **The total (decided by the user, 2026-11-18).** The card shows "这顿谁吃？ 一共 [N] 人" ("Who is eating? [N] people in all") with a number input and −/+ buttons. N is prefilled with the stated headcount, or with the count inferred from the people mentioned. N never goes below the people ticked and added (the − button stops there); when they exceed N, N rises to their count and the card says the headcount changed. With N 0 and nobody on the card, Confirm is disabled. The answer carries N; the backend derives the unmatched people from it and refuses a total below the people on the card.
- **The rest of the table.** The unmatched people are N less the people ticked and added. When there are any, the card shows how many and asks, for them together, "没有忌口 / 有，我来填 / 不确定". "有，我来填" adds that many empty guest rows to the same card; "不确定" plans with the S8 warning (design 7.6). Unticking someone keeps N, so that person becomes unmatched; lowering N removes unmatched people. A headcount-based default composition is computed from the confirmed N.
- **Possible missed restriction (Claude's call).** If the request text contains a restriction word (`过敏`, `不吃`, `不能吃`, `忌口`, `吃素`, `素食`, `allergic`, `allergy`, `vegetarian`, `vegan`, `don't eat`, `can't eat`, `avoid`; the list lives in code) but the parse returned no guest rule and no saved friend was named, the card shows a notice: "你提到了忌口，请确认已经填上". Local code, no model call.
- **Answer.** Confirming resumes the graph with the final list: selected diner ids, guest rows (name and rules), which guests to save, the confirmed total, and the answer for unmatched people (an answer without a total, as the evaluation runner sends, gives the unmatched count directly). Code writes the meal constraints from it; nothing on the card is sent to a model as a message. A new chat message while the card is pending behaves as other pending questions do today (a fresh run; carryover item).
- **History.** The card is recorded in `questions` like other questions, with a short summary of the answer so the history shows who was selected.

### 5.3 Constraints from the card

`MealConstraints` already has `diner_ids`, `guest_rules`, and `unconfirmed_diners`. After the card:

- `diner_ids`: the selected user and friends. `guest_rules`: one `RuleRef` per guest rule, keyed `guest:<n>`. A guest with no rules still counts toward the headcount.
- `unconfirmed_diners`: the unmatched people when the answer is "不确定"; 0 for "没有忌口".
- `headcount`: selected people plus unmatched people. `apply_defaults` takes the headcount from the card, not the parse, once the card is answered, and returns it (the carryover item "Headcount is recomputed" is fixed on the way).
- The planning record's `has_temporary_guests` is true when any guest is in the meal (their names cannot be anonymized; design 6, 7.6).
- The feasibility check, shortlist, search, tools, and validation read the constraints as today; they already accept several diners and guest rules.

### 5.4 Guest restrictions from the parse

`ParsedRequest` gains `guests`: a list, at most 10, of:

- `name`: the person as the user named them ("Alex", "我妈"), or null when unnamed ("a friend").
- `allergens`: values from the fixed list only (design 5.5); a value outside it is dropped by the schema.
- `avoid`: items of `{"name": <ingredient or phrase as said>, "kind": "restriction" | "dislike"}`. "不吃 / 不能吃 / 忌口 / doesn't eat" is a restriction; "不喜欢 / 不太爱吃 / doesn't like" is a dislike.

Saved friends are not guests: the parse sees them as codes (Section 5.5) and does not list them. When the user states a new rule for a saved friend in the chat ("小王最近不吃辣"), the parse lists it under `friend_rules` (code plus the same allergen and avoid fields), and the card shows it on that friend's row as a proposed addition; an unticked one is dropped. **A ticked stated rule applies to this meal only by default, with "save to profile" as a choice per person (Claude's call, following design 7.6).** Meal-only rules of a saved friend go into `guest_rules` under the friend's own key (`diner:<id>`), which `load_planning_context` already concatenates with the saved rules, and they are part of the settled diner set (Section 5.1). The parse output's codes in `friend_rules` are kept as codes, not restored by the post-hooks, so code can resolve them through the label table.

**The user's own stated rules (decided by the user).** "我对花生过敏", "我不吃羊肉" are captured the same way under `self_rules` (allergens and avoid items). A rule already in the user's profile is ignored; a new one appears on the user's row as a proposed addition, with "this meal only" (default) or "save to my profile", and triggers the card even for a solo meal (Section 5.1). Meal-only rules of the user go into `guest_rules` under the user's key.

**Taste preferences are not rules (decided by the user).** "最近不吃辣", "清淡点", "小王最近不吃辣" describe taste, not an ingredient that can be matched, and are not allergies or firm restrictions. The parse puts them in `query`, so this meal's planner takes them into account, and no rule is created. Remembering them across conversations belongs to T1.6's memory (`extract_memories` with confirmation); the carryover records this example. `avoid` is only for named ingredients or ingredient groups the dictionary can match ("香菜", "羊肉", "猪肉").

**Diet words are not accepted as rules (decided by the user).** "吃素", "素食", "vegetarian", "vegan" and similar have no working rule form (a text rule "素食" matches no line). The parse leaves them out of `avoid` and flags them (`diet_words`); the card and the people page show, instead of a rule, "暂不支持按类别忌口，请把不吃的食材（如猪肉、牛肉、鸡肉、鱼、虾）逐个加上" next to that person, and refuse a typed text rule made of such a word. Category rules go to the carryover.

Code turns each `avoid` name into a rule: a dictionary match (`match_ingredient`, which sees personal aliases) gives an `ingredient_id` rule; otherwise a text rule, which the card marks "按文字匹配" so the user can see it may match loosely. An allergy outside the fixed list ("对芒果过敏") is an `avoid` restriction on that ingredient (design 5.5).

### 5.5 Nicknames and codes

**One label table per conversation (Claude's call).** Labels keep the planner's existing "Diner N" form, so `planner_v4`'s line "Diners appear only as codes such as 'Diner 1'" stays true and the planner prompt does not change. The table is kept in the conversation state and only grows:

- The user is always "Diner 1".
- A saved friend gets the next number the first time their nickname is matched in a model-bound text of the thread, or when they are selected on the card.
- A guest gets the next number when the card is confirmed.
- Unconfirmed people continue the numbering in each run, as `meal_preferences` does today.

`diner_codes` and `meal_preferences` read this table instead of numbering by selection order, so the parse and the planner use the same code for the same friend.

**Replacement (pre-hook).** For calls of the planning tasks (`planning`, `planning_eval`), the pre-hook replaces every saved friend's nickname in model-bound text with the friend's label, assigning one if needed. The table for the current call comes from the graph (a context variable set around each node that calls a model, or an equivalent; left to the builder), because one `LLMClient` serves every thread. Other tasks (extraction, suggestions) get no replacement. Matching (Claude's call): longest nickname first; Latin nicknames match whole words, ignoring case; CJK nicknames match as substrings. Planning records and evaluation exports keep the replaced form, as today.

**Short or food-like nicknames (Claude's call).** A CJK nickname of one character, or one that equals or appears inside a dictionary name or alias, would also replace food words ("鱼" in "鱼香肉丝"). Two guards: (1) the matcher skips an occurrence that lies inside a longer dictionary name or alias, or a recipe title, found at that position of the text, so "鱼香肉丝" stays as written, is not sent as "Diner 2香肉丝", and does not count as naming a friend (no card for a solo meal); (2) the people page warns when such a nickname is saved ("这个昵称也出现在食材名里，规划时可能被误认；建议换一个更长的昵称"); saving is still allowed. A remaining false match shows up as a preselected friend on the card, where the user can untick it.

**Restoration (post-hook and the reply).** Model text shown to the user (the planner's note and any restored tool arguments) has each label restored: a friend's label to the nickname, a guest's label to the guest's name, "Diner 1" to "你" / "you", and an unconfirmed person's label to "其他用餐者" / "another diner". Any "Diner N" left over (a number not in the table) is restored to "其他用餐者" / "another diner". This removes the "Diner 2 的饮食限制尚未确认" seen in round 7.

**Guests' names in the parse.** The parse reads the user's words, so a guest's name reaches the provider (design 7.6 accepts this and the UI already says conversations may be sent to the provider). The planner sees guests only by label.

### 5.6 The parse sees the assistant's questions (`parse_v4`)

Option B of the carryover item, now decided by the user's request:

- The parse input interleaves each recorded question with the messages, at its `at` position, as an assistant message rendered from fixed text in the conversation's locale (the same wording the chat shows; for example "只用冰箱里的食材，还是可以买菜？" and "做几个菜？"). Questions stay in `questions` for display; `messages` is not changed, so the route and the planner see nothing new. The request-style question and the diner card are not rendered (their answers never become messages).
- `parse_v4.md` adds: how to read a short answer next to the question just asked ("两个" after "做几个菜？" is dishes 2; "都行" after the mode question is shopping), the `guests`, `friend_rules`, `self_rules`, and `diet_words` fields and the taste-preference rule (Section 5.4), and that saved friends appear as "Diner N" and are never listed as guests.
- The trim (carryover, reviewed 2026-11-16) applies to the rules and the few-shot examples inside the prompt file, not to tests or evaluation scenarios, which all stay. One prompt example per distinct case. Drop the repeated "不要素菜 / 全是荤菜" pair (keep one), the egg phrases repeated in the examples, the second of "三个菜，有一个荤菜" / "8道菜，包含2个荤菜", the English twins of Chinese examples ("two dishes and a soup", "just a dessert"), and the repeated "leave null when not stated" and "the newest message wins". Keep every case whose example was added on purpose in rounds 4 and 6 ("三个菜，不要素菜" stays). Target: `parse_v4` no longer than `parse_v3` (79 lines) despite the new fields. The regression runs of the three existing sets (Section 5.9) check the trim: if a scenario gets worse because an example was removed, that example goes back in.
- `PROMPT_VERSIONS` becomes `route_v2+parse_v4+planner_v4`. Round 8 then made `parse_v5` (decided by the user, 2026-11-18): a stated number of cold dishes is the dish count (`docs/prompt-versions.md`).

### 5.7 Unknown allergens on the plan card

- **What is shown.** The plan carries, per left-out dish, its title and the ingredient names that caused it (search already has them: `ExcludedRecipe.lines`). The card shows "宫保鸡丁：花椒油（过敏原未知）" for each, grouped by ingredient name when several dishes share one. This appears only when some selected diner has an allergy, as today.
- **Confirming.** Each ingredient has "确认过敏原", which opens the dictionary page's confirmation panel on the card: for a user-created entry with unknown allergens, choose its allergens (with the model's suggestion) or "contains none"; for a line not linked to the dictionary, link it to an entry or create one. The panel is extracted from the dictionary page into a shared component; the backend paths are the existing ones, including relinking other unlinked lines with the same name (design 5.5).
- **Planning again.** Once at least one ingredient is confirmed, the card offers "重新规划". It sends a fixed message ("按同样的要求重新规划" / "Plan again with the same request"), which code recognises: when the stripped message equals it and the thread holds the previous turn's parsed request, that request is reused as this turn's parse, with no route or parse model call (decided by the user, 2026-11-18, after round 8 showed the parse dropping the previous query). The planning record holds the reused request and a `parse_reused` event in place of the route and parse calls; the fixed text as a thread's first message takes the normal path. The parse prompt's example for it stays but is no longer relied on. The settled diners carry over (Section 5.1), so no card appears unless a friend in the settled set was deleted since. A dish whose confirmed allergens conflict with a diner stays excluded by H2, and the new plan says so as usual.
- **Also without a plan (Claude's call).** The list, the panel, and "重新规划" appear on every planning reply that has left-out dishes, including the early stop and the no-valid-plan reply, since unknown allergens are often why no plan is possible. The feasibility check therefore also reports the dishes it left out for unknown allergens.
- The plan card's permanent allergen notice is unchanged.

### 5.8 The unknown-allergen override and the diner

`H2Override` with `unknown_allergens` today covers a recipe for every diner. **Kept as is (decided by the user, 2026-11-17):** the user confirms a dish with unknown allergens once, and for the rest of that plan the dish is not checked for unknown allergens again, whoever eats. This includes a diner who joins the plan later: they are not asked about this dish (their known allergens are still checked by H2 as usual). The override is about the dish in this plan, so its `diner_key` is not required to name each allergic diner. No code change in 2b; T1.5 builds the dialog that creates the override (manual edits, design 7.5) and records it per plan. The carryover item "Unknown-allergen override ignores the diner" is closed with this decision. `docs/design.md` 7.5 says an override "covers only that diner and rule, so a new conflict asks again"; 2b updates it to say this holds for rule conflicts, while an unknown-allergen confirmation covers the dish for the whole plan. **Known rule conflicts stay as design 7.5 says (confirmed by the user, 2026-11-17):** a manually added dish that breaks a diner's known allergy or restriction is allowed after the user confirms the named diner and rule; that confirmation covers only that diner and rule, so a diner added later whose rules the dish breaks is asked again.

### 5.9 Evaluation

**Runner additions.**

- `friends`: saved friends for the scenario (nickname and rules), created on the test database like `rules` are for the user.
- `diners_answer`: how the runner answers the diner card: `accept` (the card's preselection and parsed guests as shown), or an explicit answer (selection, guest rules, unmatched answer). A scenario that expects no card fails if one appears, and the reverse.
- `answers`: replies to ask-first questions, in order, with `request_style: ask_first`, so question-and-answer scenarios run end to end.
- Expectations: `expect_card` (shown or not, and which rows are preselected), `expect_guests` (parsed guest rules, compared as sets), `expect_titles_none` reused for dishes a guest's rule must exclude. As built (Task 11), `expect_guests` became `expect_card.guests`, and `expect_rules_applied` / `expect_rules_not_applied` and `between_turns` were added (`docs/local-development.md`).
- Two automatic checks on every scenario: **no nickname sent** (no saved nickname appears in any model-bound message of the planning record, including route and parse events) and **no label shown** (no "Diner N", "Guest N", or other label in the reply text). Both are code checks and count as mismatches. As built, the label check looks for "Diner N" only, since the code makes no other label form, and also reads the diner cards shown.
- **Friends' names in the exports (an exception, decided at the final review).** The evaluation friends are fictional scenario data. `results.jsonl` (git-ignored) keeps the first diner card with their display names, and a failure string in `summary.json` may name one (for example "rule not applied: Alex"). The rule "no saved nickname in any evaluation export" is about model-bound text and the planning records of real users; the model-bound messages inside an export are still checked by **no nickname sent**.

**Scenario sets.**

- **Development set** (`scenarios_diners.yaml` with `library_extended.yaml`, extended with any dishes the cases need): written by Claude, used while building and tuning. Cases: a saved friend named by nickname with an allergy; a guest's allergy in the request ("我和 Alex，他对花生过敏"); a guest's dislike (warning only); a saved friend not named (not preselected, rules not applied); headcount above the named people with each unmatched answer; the user deselected; a food-like nickname; a short answer after an ask-first question ("两个"); "都行" after the mode question; plan again after an allergen confirmation (the confirmation done by the runner on the database between turns); a friend's new rule stated in the chat.
- **Held-out set** (`scenarios_heldout.yaml`): requests written by the user after this design is approved, from an outline Claude provides (cases to cover and a suggested count); Claude adds the fictional library, stock, friends, and expectations, and commits the set **before implementation starts**. The plan's first task is therefore this set, and it waits on the user's requests. During 2b it runs only at the end, after the development set is final; what it shows is recorded in the history and the carryover, and is not tuned against within 2b.
- The three existing sets are rerun as a regression check, since `parse_v4` changes the parse of every request.

**Rounds.** One development round when the work is complete (more only if tuning is needed, each approved), then one held-out round, plus the regression runs. Each paid run is estimated and approved first. The rounds go into `docs/planning-evaluation-history.md` with a new comparison table per new set.

### 5.10 Success criteria

- Every development and held-out scenario as expected, except call-count warnings; the held-out result is reported as is.
- No nickname sent and no label shown, in every run.
- Regression sets: as expected at round 7's level (baseline 7/7, plan 2a 18/18, counts 10/10 within the known call-count warning), first pass not lower.
- Single-person requests: no card, and calls per plan scenario not above round 7 (3.0-3.5). The largest call may grow by the guest fields; it stays far below the 32,000-token budget.

## 6. UI summary

- Chat: the diner card as a new pending-question kind, with its history summary.
- Plan card: diners line ("按 我、小王、Alex 排除"), the left-out dishes with ingredient names, the confirmation panel, and "重新规划".
- People page: the food-like nickname warning.
- Shared: the confirmation panel extracted from the dictionary page.
- Chinese and English strings for all of it.

## 7. Left to the builder

- The exact card answer payload, state field names, and how the label table reaches the hooks.
- Fixed question wordings rendered into the parse input, as long as they match what the chat shows.
- The exact `parse_v4` wording within Section 5.6.
- The development scenarios' wording and expectations.
- Task order and test placement; the plan states which tasks change model input.

## 8. To the carryover

- **Category rules** ("吃素": no meat or seafood categories): a new rule target, a migration, H2 and search support, card and people page. Until then diet words are refused with a hint (Section 5.4). "不吃猪肉" already works through the dictionary hierarchy.
- **Taste preferences across conversations** ("最近不吃辣"): T1.6 memory.
