# Phase 1 Carryover

Decisions, warnings, and open items from Phase 0 execution, distilled into this committed file so they reach Phase 1 planning. The source was the execution ledger, which lives only on the developer's machine and outside git, so anything not carried here or into the other docs does not survive it. Items that changed the product's behavior are already folded into `design.md`; this file holds only what is still pending or forward-looking. Read it together with the still-open table in `phase-0-import-feedback.md` when writing each Phase 1 plan.

## Work Phase 0 explicitly handed to Phase 1 tasks

- **T1.1:** vector search in `search_recipes`, and tool-calling invocation on `LLMClient`.
- **T1.2:** the import allergy check uses only `rule_type` allergy; dietary restrictions and dislikes were left to T1.2.
- **T1.4:** fill the call-layer pre/post hooks with nickname replacement and restoration (the hooks exist and are empty).
- **T1.6:** when a recipe is deleted, also delete its notes in the LangGraph Store (which does not exist in Phase 0). When a friend is deleted, clean up saved plans per design 7.6.
- **T1.9:** add the delete-friend warning about conversations to the dialog built in Phase 0 Task 7. Also decide where the "Phase 0 page usability" work lands: the design has no task for it, T1.9 covers only the new chat and plan pages, and the open issues are listed in `phase-0-import-feedback.md`.

## Model and API behavior found during Phase 0

- **gpt-6-luna** function calling through Chat Completions requires `reasoning_effort: none`. Check this when adding tool calling, because OpenAI is the planning fallback.
- **DeepSeek thinking mode is disabled** (`extra_body`), because forced structured output in thinking mode returned 400. If planning turns thinking back on, tool calls then require passing `reasoning_content` back in every later request, or the API returns 400.
- **deepseek-flash vision** is listed as capable in `models.yaml` because its official page says so, but the design's model table still says text-only; this awaits a decision.
- The embedding cost estimate `len(text)//4` undercounts Chinese text (about one token per character). The cost is tiny, so it stands, but recheck if embedding volume grows.

## Before making the repository public (T1.11 checklist additions)

- Make `USER_AGENT` configurable, add a contact URL, and tell forks that change fetch rules to use their own name; choose a license and add usage terms to the README.
- Pin the resolved address in the web import before any public deployment (DNS rebinding is possible between the address check and the fetch; acceptable while the instance is private).
- Add an Origin/Host check on POST routes: a malicious page can submit a cross-origin multipart form to e.g. `/api/imports/batch` and trigger paid calls (bounded by the monthly cap; Chrome's Private Network Access largely blocks it today, other browsers may not).
- Check the full history for secrets (replaces the dropped gitleaks pre-commit hook).

## Evaluation gaps to fix before Phase 2 model comparisons

- `valid_json_rate` in the export is structurally 1.0: only outputs that passed schema validation ever produce a log row. The real parse-failure signal is `success_rate` in the `llm_calls` stats; note this wherever the metric is reported.
- An added ingredient line counts as a line the model missed, even when the source never had it. Ask, when adding, whether the source has the ingredient, or exclude added lines from recall.
- The evaluation export does not yet include `image_files`, so screenshot samples cannot be rerun from the export alone.
- Prompt version rule: `extract_v1` was edited in place only while `extraction_logs` was empty. Once any log exists, every prompt change gets a new version name (`extract_v2`, ...).
- The model duplicates ingredients across the list, the method, and formula lines despite a dedup rule in the prompt; this survived out-of-sample tests and is treated as the model's instruction-following ceiling. Revisit in `extract_v2` or with a stronger model, and let the metrics track it.

## Found while trying the first planning slice (Phase 1 plan 1, checkpoint)

Seen in the user's first real planning runs: an empty inventory and only a plate and a baking tray registered as cookware. Every run failed after 2 retries, and each run made about 30 planning model calls (plus 2 for intent and parsing).

- **Missing cookware needs its own next action (rest of T1.4, design 6.3).** When H3 (missing cookware) is what blocks the plan, the offered next actions ("switch to shopping", "fewer dishes", "drop the time limit") cannot help, because cookware is not bought with the meal. Add a next action and plain-words hint such as "register the cookware you have" with a link to the inventory page. Only offer "switch to shopping" when H1 is among the unmet rules.
- **Stop early when the request cannot be met (rest of T1.4, design 6.3).** Before retrying, let code check whether any candidate can satisfy the blocking rule at all (for example, no recipe in the library fits the owned cookware). If none can, skip the retries and go straight to the no-valid-plan answer with that reason. In the runs above this would cut about 30 calls to about 5.
- **Fewer tool round trips per attempt (rest of T1.4, tool design).** A typical attempt goes search → recipe details → missing items and cookware → proposal, which is 4-6 model calls. Let `search_recipes` hits carry the missing-ingredient count and missing cookware for each recipe, so the model rarely needs `compute_missing` and `check_cookware` separately. This changes `TOOLS_VERSION`; compare against the evaluation before and after.
- **Retries restart from nothing (rest of T1.4).** Each retry starts a fresh inner agent that only receives the violation list, so it re-reads the inventory and searches again. Either continue the previous attempt's conversation or pass a short summary of what it already found (candidate ids, missing items).
- **Context grows every call.** Each call resends the whole history (33 messages by the last call of a retry), so token cost grows faster than the call count. The changes above reduce it; trimming old tool results is a further option.
- **Measure it.** Report average model calls per run next to cost in the planning evaluation, and include a scenario where key cookware is not owned.
- **Baseline (plan 1 Task 15, `plan_v1`, deepseek-flash, 7 fictional scenarios).** The six feasible scenarios passed on the first attempt with 6-10 model calls each (about $0.004-0.008 per run). The scenario without a wok failed as expected but took 31 calls ($0.035, 2 retries), and its closest plan was two soups with only H4 (composition) unmet: the model avoided the wok dishes, so the user is told about the dish count instead of the missing cookware. Overall: first and final pass rate 0.86, 0.29 retries, 10.3 calls, $0.064 in total, 11.3 s average latency.
- **Plan 2a results (Task 10, round 4, commit `e0401c2`, `route_v2+parse_v2+planner_v3`).** Baseline set: 7/7 as expected with and without vectors, first pass 6/6, 3.0 calls per plan scenario (10.3 before), uncached $0.0128 (was $0.064), cache-aware $0.0031, model time 3.0-3.2 s (was 11.3 s), largest call 3,022 tokens. Plan 2a set: 18/18 and 17/18 as expected, first pass 12/12, 3.1-3.5 calls per plan scenario, every infeasible scenario stopped within 2 calls with the expected reasons and next actions, largest call 3,609 tokens (budget 32,000). The call limits (6 model and 10 tool calls per attempt) were never reached; the most planner calls in one attempt was 5. Every round, its changes, and a run-by-run comparison are in `docs/planning-evaluation-history.md`.
- **Plan 2a results after Task 11 (round 6, commit `fe6ed93`, `route_v2+parse_v3+planner_v3`).** Every run as expected: baseline 7/7 twice, plan 2a 18/18 twice, the new counts set 10/10; first pass 6/6, 12/12, 8/8; 3.0-3.5 calls per plan scenario, none over 5; uncached $0.0146 on the baseline set (the longer `parse_v3` adds about 14%), cache-aware $0.003-0.005. Details in `docs/planning-evaluation-history.md`.
- **Other diners' restrictions in the request text are not captured (plan 2, diner selection).** A request such as "me and Alex, who is allergic to shrimp" is not parsed by `parse_request` or checked by code in plan 1; only the generic S8 warning (unconfirmed diners) appears. Plan 2's diner selection must capture such statements, or at least flag them on the plan card, so a stated allergy is never left to the model's judgment.

## Deferred from Phase 1 plan 1

Task-review and final-review findings that were judged safe to defer. None blocks the first slice; each names where it most likely lands.

### Planning and safety (plan 2, rest of T1.4)

- **Unknown-allergen override ignores the diner.** An `H2Override` with `unknown_allergens` covers a recipe for every diner. When manual edits create it, decide whether each diner with an allergy must confirm, or check that `diner_key` belongs to such a diner.
- **`check_recipes` treats nonexistent recipe ids as safe.** Every caller checks existence today (H5, the tools' known-id check, search), so it is safe in practice; keep it that way or make the function report unknown ids.
- **S3 calls `date.today()` inline.** Pass `today` as a parameter; tests near midnight can flake.
- **S1 and S6 skip optional lines, and S1 reports per line rather than per ingredient.** Plan 2a makes S1 sum per ingredient across the plan's dishes (warning only).
- **The assistant's reply text after a plan is unchecked model text (with the chat page redesign).** The reply shown with a plan is the planner's `note` from `PlanProposal`, displayed as written; `planner_v3` only asks for one or two sentences and no allergy safety claims. Seen in the plan 2a checkpoint (real database, planning run 20, "6道菜" then "允许购物"): the plan itself was valid (4 meat, 2 vegetable dishes, no split asked), but the note said "3 道荤菜……和 3 道素菜（糖醋小排算肉菜？——实际为：荤 4 道……）", correcting itself mid-sentence, and suggested swapping in 胡萝卜炒鸡蛋, a dish not in the plan. Options discussed: (A) code writes the reply from the plan (counts, titles, items to buy) — always right, but the user finds it too rigid; (B) a stricter planner prompt (no counting dishes, no dishes outside the plan, one sentence) — a new planner version, still relies on the model; (C) code checks the note (for example a title not in the plan) and falls back — catches only some errors. A mix is possible: code states the facts (counts, dishes, what to buy) and the model adds one constrained sentence (a tip, such as which dish to start first). Decide together with the chat page redesign, which already rearranges how the reply text and the plan card are shown; a prompt or schema change needs a planner version and an evaluation round.
  - **Outcome (chat page redesign, round 7, 2026-11-16).** The user chose (B): `planner_v4` (one sentence, one tip, no counting or listing, no dish outside the plan). Measured over five models (`docs/planning-evaluation-history.md`, Model comparison): no note named a library dish outside its plan, and the user's ratings average 2.5-3.0 out of 3, with one note of 100 marked as having something that should not appear. Open points, each needing a new planner version or a check, decided by the user: (1) notes still state wrong facts the checks cannot see, such as "一荤一素" for two vegetable dishes, or a loose dish name ("肉丝") that matches no dish in the plan; (2) the default model wrote "Diner 2 的饮食限制尚未确认" in two Chinese notes, showing the internal diner label (the planner input names diners "Diner N"); (3) `counts_dishes` flags "两道菜可同时上桌", which the user accepts, so either narrow the check to type counts (荤/素) or drop it; (4) the note checks run only in the paid planning evaluation, never on real conversations, so a bad note in use is seen only by reading it. The default model stays deepseek-flash (user decision after round 7: gemini-3.8-flash rated best but was 5-15x slower and about 15x the cost; gemini-3.5-flash-lite failed `three_dishes_no_vegetable_shopping` in every run).
- **Trim the parse prompt when it next changes (reviewed 2026-11-16, user decision: not now).** `parse_v3.md` (79 lines, about 8 KB, the largest call) says several things twice: field rules and examples repeat the same case ("不要素菜 / 全是荤菜" in the `meat_bound` rule and two examples; the three egg phrases under named ingredients and again in the examples; ranges, vague amounts, and "荤素都行" likewise); some examples repeat each other ("三个菜，有一个荤菜" and "8道菜，包含2个荤菜"; "不要素菜" and "全是荤菜"; the English "two dishes and a soup" and "just a dessert" next to their Chinese twins); "leave null when not stated" and "the newest message wins" are each said more than once. About a quarter could go without losing a case. Not done now: the cached prefix makes the saving about $0.001 per run, some repeats were added on purpose in rounds 4 and 6 (gemini-3.5-flash-lite still failed "三个菜，不要素菜" in round 7), and a change needs `parse_v4` and an evaluation round. Do it together with the next parse change (for example the item below), keeping one example per distinct case. `route_v2` is already short; in `planner_v4` only the opening description of the shortlist fields could be shorter.
- **Let the parse see the assistant's questions (plan 2b).** After a question ("只用冰箱还是可以买菜？", "做几个菜？"), the user's answer is added to the conversation but the question is not, so the parse reads "今晚做两个菜" then "可以买" with nothing in between. Clear answers parse fine; short ones that only make sense next to the question ("两个", "都行") depend on the model guessing. The chat page redesign saves questions for display only (design `2026-11-15-chat-page-redesign-design.md`, option A, decided by the user). Option B, deferred here: also add each question to the messages the route and parse models read (route only from the next new message; after an answer the graph goes straight back to parse; the planner never reads the conversation). It changes model input, so it needs a new parse prompt version with question-and-answer examples and an evaluation round with scenarios that answer a question.
- **Clarifying questions written by the model (not planned in Phase 1).** Today every question to the user is decided by code and asked with a fixed form (request style, mode, dish count under "ask first", contradictions, diner selection, unconfirmed diners, saving a friend's rule); the model only parses. Vague requests that no code rule turns into numbers ("多来点荤菜", "清淡点", "像上次那样") might benefit from a question. A possible shape: the parse marks what it is unsure about, and code still decides whether to ask, how often per turn, and in what words. Needs its own design (when it may ask, limits per turn, wording, evaluation); keep allergy and diet questions code-driven.

### Shopping list (T1.5)

- **Household measures on the list (to discuss when planning T1.5).** A household-measure line stores only a number (unit null, type `spoon`), so a list would read "面粉 1" or "1 家用量具" instead of "面粉 1 大碗". Discuss: whether to add a measure-word field (the word from the source, such as 碗, 大碗, 一小把) filled by a new extraction prompt version; whether to rename the stored values at the same time (amount type `spoon` → for example `household`, and how the unit is stored), since that also needs a prompt version and a migration; and how existing lines are handled (left empty with the source text shown, filled by a one-off pass over `raw`, or re-extracted). `extraction_logs` hold `spoon` in their samples, so the extraction evaluation must accept old and new values.
- **Merge one ingredient across dishes into one line.** Design 5.1 and 7.5 do not say whether two dishes that both lack spring onion give one line or two. Merge them, listing each dish's stated amount.
- **"In stock but not enough" belongs on the list.** H1 only asks whether an ingredient is in stock, so 3 eggs in stock against two dishes needing 2 each puts nothing on the list today. Reuse plan 2a's per-ingredient summing (the S1 change): where amounts sum exactly (grams, pieces), list the shortfall ("eggs: 1 more"); where they do not (vague, spoon, or level-mode stock), list each dish's stated amount and let the user judge. Never estimate an amount the recipe does not state (design 5.1, Phase 1 design 6.4).
- **Choice-group semantics:** a group mixing optional and required lines is undocumented and untested; a parent line whose child has level `none` reports `not_in_stock` rather than `level_none`.
- **`update_inventory` can report `tool_failed` after a successful commit** if shaping the result fails, and has no direct rollback test. It is not among the planner's tools yet; fix before adding it.
- **`meal_preferences` can produce an empty subject** when a rule's dictionary entry is missing.
- **The no-valid-plan next actions** offer "switch to shopping" for an H5-only failure, where it does not help (see also the cookware item above).
- **Headcount is recomputed** as `unconfirmed_diners + 1` in the graph; return it from `apply_defaults` instead.
- **`agent/graph.py` is about 500 lines.** Move the plain-words helpers to their own module when it next grows.
- **Duplicate dietary rules produce duplicate conflicts; no NFKC folding** in dietary matching, search, or the extraction guard (all fail safe).

### Chat API and page (plan 2 or T1.9)

- **Recipe detail page edge cases (chat page redesign, Task 3b review).** After saving an edit, the detail view may show the pre-edit values for a moment until the reload returns; if that reload fails, the page shows the load-failed notice with no retry button (refreshing recovers); the edit flow is tested in `recipe.test.tsx`, except that cancel returning to the detail is not.
- **Where the language switch lives (T1.9b, decided with the user to postpone).** Today "中文 / English" sits in the top navigation on every page; switching mid-conversation makes later replies use the new language while earlier ones stay, so a conversation can mix languages. Language is a set-once preference, so options for the site-wide pass: (A) keep it on every page but quieter (a small "中 / EN" in a corner or the footer); (B) move it into a settings menu (for example ⚙ top right, which could also hold the request style). A chat note "later replies will be in English" after a mid-conversation switch was considered and not added for now.
- **Typing while the request-style choice is pending drops the question** (found in the chat page redesign, Task 2 review; pre-existing). When the first-use "ask first / plan then adjust" choice waits and the user types a message instead of pressing a button, the run clears the pending question and no item records it. Either keep the choice visible until answered, or record it like other questions.
- **No guard against concurrent requests on one thread** (two messages, or a message and a resume). Acceptable for one user; add a per-thread lock before anything multi-user.
- **A new message while a question is pending starts a fresh run** and drops the pending question. Decide whether to block it (409) or keep allowing it.
- **Progress events fire when a node finishes,** so nothing shows during the first model call. A start-of-node signal would need another stream mode.
- **The chat runtime is built lazily** on the first request and could be built twice by two concurrent first requests; build it in `lifespan`.
- **The stream is not aborted when the page unmounts.**
- **A non-404 failure while loading a stored thread** keeps the stale id in `localStorage`; the next send then starts a new thread and overwrites it.
- **The request-style select updates before the resume returns,** so a failed resume can show a style the backend did not save until reload. A restored pending question can be stale if the backend advanced before an error event.
- **`token` events are reserved but never sent** in this slice; the page handles them if they arrive.
- **The message list uses `display: contents` on the aria-live list** (`frontend/src/app/globals.css`, around lines 743-745). Some engines drop list semantics or live-region behaviour with it; check with a screen reader.
- **`PlanProposal.note` and the planner prompt disagree on length.** The schema description in `backend/minemydinner/agent/planner.py` (around line 36) says "One or two sentences to the user" while `planner_v4` says one sentence, and the model sees both. Align them in the next tool/prompt version (changing the schema description changes the tool version).
- **The first-use request-style question has no visible answer in the history.** After "ask first" or "plan, then adjust" is answered, the history shows the question with no answer, both in the session and after a reload. A short user-side item (or "chose: ask first") would fix it; a UI decision for the user.

### Evaluation (T1.10)

- **The planning evaluation's database check** compares against the configured test URL and the main database name but does not require a `*_test` name; `ensure_empty` is the remaining guard.
- **The summary has no "as expected N/M" line,** so a scenario that is meant to fail lowers the pass rates without comment.
- **A hard kill skips cleanup;** the next run then refuses until the test database is emptied by hand. `main()` also opens the checkpointer before `ensure_empty`, and a connection error prints a raw traceback.
- **`planning_eval` spending is counted only in the test database's `llm_calls`,** so evaluation spend never counts toward the real monthly cap. Document it or count it centrally.
- **Kept rows matter.** The test database keeps `llm_calls` and `planning_runs` rows from evaluations and real-app checks. Tests must not assume these tables are empty and must delete only rows they create. Before plan 1's final fixes, a Phase 0 fixture deleted the whole `llm_calls` table, which lost the per-call rows of plan 1's Task 14 check and Task 15 run (the baseline totals above survive).

### Import, dictionary, and extraction (from plan 1 Tasks 1-4)

- **Import and dictionary experience plan: done** (branch `feat/import-and-dictionary`, design `docs/superpowers/specs/2026-11-16-import-and-dictionary-design.md`, plan `docs/superpowers/plans/2026-11-16-import-and-dictionary.md`). Built as decided: remembered manual links (ingredients only; cookware left out by user decision), every queued draft matched again when shown, linking from the "allergens unknown" list, category and stock-mode suggestions (`allergen_suggest_v2`, checked once), and the amount controls (the household option kept the short label "家用量具" with the examples in a hint, by user decision). Not done, by user decision: built-in aliases for common spelling variants (西蓝花/西兰花, 西红柿/番茄, 马铃薯/土豆); the seed dictionary is unchanged. What the plan left open is under "Deferred from the import and dictionary plan" below.

- **Personal aliases:** no size limit on `PersonalAliasesIn`; the dictionary browser can show a stale dish count after a refresh.
- **Amount guard:** digit and decimal corroboration is still a substring match ("2" inside "12", "1.5" inside "11.5"); `raw`-in-source is a bare substring and can join across line breaks. The candidates are listed in design 8.4 for T1.10.
- **Web import:** `http://` links still swallow directly following CJK text.
- **Smoke tool:** the vision check passes on "not red" or "infrared"; `_report` reads the latest `llm_calls` row, which can be stale or missing when the cap stops a call; the smoke test does not assert the `bind_tools` options or the failure path.

### Cleanup

- Import order in several tests; a stale comment above `asked_question` in `agent/state.py`; a test named `commits_without_reaching_the_database` that does reach it; long lines in `stock.py` and `test_planning_tools.py`; weak `!= 403` assertions and no custom-origin test for the Origin check; `engine.dispose()` not in a `finally` in `lifespan`; `route.md` re-read on every call; `GraphDeps` fields typed `Any`; the `.allergen-notice` class has no CSS rule of its own.

## Deferred from Phase 1 plan 2a

Findings judged safe to defer during plan 2a's task reviews. The final whole-branch review triages them again before merge.

### To revisit

- **The feasibility check runs again before every retry.** Within one planning run its inputs (request, library, stock, cookware) do not change, so today the second check always repeats the first. It is kept on purpose (decided with the user): it becomes useful once stock or diners can change during a run (an `update_inventory` planner tool, diner selection in plan 2b). Revisit then, or remove it if neither arrives.

### Planning logic

- **A feasibility stop on a retry drops the closest attempt** (`graph.py`, final 2a review). The re-check before a retry can only differ from the first when stock or the library changes during the run; if that ever happens, the earlier closest attempt (`best`) is replaced by the stop. Prefer showing `best` if this case starts to matter (diner changes in 2b, inventory tools).
- **Skipping the contradiction question costs one extra parse call**: an empty answer goes back to `parse_request`, which calls the model again before reaching `unclear` (final 2a review). Rare path.
- **Named ingredient stocked at count 0.** The quick "is it at home" test reads every named ingredient leniently (as a seasoning), so a non-seasoning such as eggs at count 0 passes that test. The per-recipe check still stops the request, with "only 0 dishes with 鸡蛋 can be made" instead of "no 鸡蛋 in the fridge". Passing the ingredient's real role would give the plainer wording.
- **A recipe with no ingredient lines counts as makeable**, so it also keeps the empty-fridge reason from appearing.
- **`uses_expiring` lists optional lines** in search hits and the shortlist.
- **Mixed units for one ingredient** (grams on one line, pieces on another) are not summed together by S1; the later unit is skipped, as before.
- **Short unknown names under "must use"** match more widely now that the original line text is searched too (as the design allows).
- **The feasibility check ignores "exclude recently cooked"** while the shortlist and search apply it; it has no effect until saved plans exist (T1.5), then align them.
- **"Any mix of meat and vegetable dishes" is not offered for a combined dish shortfall**; that case only arises when no split was named, so it does not matter today.
- **Variety within one meal is not guided.** In the Task 10 evaluation (plan2a set, with vectors), "8 dishes, 2 of them meat" got 4 egg dishes (鸡蛋饼, 白煮蛋, 嫩滑蒸蛋, 煎荷包蛋). The plan is valid; the shortlist spreads main ingredients only within one dish type, and nothing asks the planner to vary main ingredients across the whole meal. Consider a soft rule or a prompt line when variety comes up again.

### Parsing and wording

- `_counts_text` would produce an empty clause if a spec required no dishes and no soups; unreachable while every spec needs at least one dish in total.
- The planner's request summary prints an explicit "0 vegetable dishes" when a side's range is `[0, 0]` ("两个荤菜", "不要素菜"), and states "Every count is exact unless it says at least, at most, or a range". Both are model-facing wording to watch in the evaluation.
- The shortlist header line (`SHORTLIST_HEADER` in `graph.py`) still says only "JSON keyed by dish type"; the `complete` marker is explained in `planner_v3.md` alone. A type marked complete that holds fewer dishes than needed gets no extra hint (the feasibility check stops that case first today).

### Cost accounting

- Provider-prefixed cache fields (such as OpenAI's `priority_cache_read`) are not read; the app uses the Standard tier.
- Cache counts that a provider does not report are stored as 0 rather than null (priced as uncached either way).
- When OpenAI reports no `cache_creation` count, written tokens are priced at the input rate rather than the cache-write rate (gpt-6-luna: $0.10 instead of $0.125 per million): a small underestimate, on the fallback model only (final 2a review).

### Evaluation

- The planning evaluation's data is committed in `backend/eval_data/planning/` (fictional, hand-written; moved there from the git-ignored `data/eval/planning/` during plan 2a): `library.yaml` and `scenarios_baseline.yaml` are plan 1's 15 dishes and 7 scenarios, kept unchanged for like-for-like comparison with its baseline; `library_extended.yaml` (23 dishes) and `scenarios_plan2a.yaml` (19 scenarios) test plan 2a's features. Keep the baseline pair unchanged in later plans, or record a new baseline when it must change. Planning scenarios derived from real use stay in `backend/data/`, outside Git.
- "No invented numbers" for vague requests is checked through the default counts; whether the reply's wording invents numbers is still read by hand. The quality of a plan (balance, "清淡", "丰盛") is read by hand until the T1.10 judge-model metric exists.
- The reply-language check removes recipe titles and then looks for Chinese characters: an English reply quoting a Chinese ingredient name would fail it, and a mostly English reply with any Chinese word would pass a Chinese check.
- Evaluation run records: route and parse calls are recorded as `model_call` events (with `step`) only when the conversation reaches planning; a conversation that never plans keeps only its `llm_calls` usage rows. A `--results-dir` inside the repository but outside `runs/` leaves untracked files that block the next run (a safe refusal).
- The time-limit scenario relies on snack dishes with stated times, because the feasibility check never blocks dishes whose time is estimated.
- **Cost basis for comparisons.** From plan 2a on, compare runs by the cache-aware cost. The uncached cost exists only to compare with plan 1's baseline ($0.064), whose records had no cache fields. Both are estimates (reported tokens × catalog prices); DeepSeek's cached price in the catalog is the peak-hour price, so off-peak bills are lower. Match against the provider's billing page when real spend matters.
- **Prompt file hashes depend on line endings.** `summary.json` hashes the prompt files as they are on disk; a Windows checkout (CRLF) and a Linux checkout (LF) of the same commit give different hashes. Hash with normalized line endings if runs are compared across machines.
- **The evaluation scenarios were used for tuning.** Task 10 changed code in response to the baseline and plan 2a scenarios, so results on them overstate quality on unseen requests. Add scenarios that took no part in tuning (plan 2b: for example a vegetarian friend at the table, other diners' allergies) and judge progress on those. Tuning stopped by user decision after round 5; a request-summary line aimed at one scenario was reverted.
- **A complete shortlist does not always stop the planner searching.** When the model is unhappy with a complete shortlist (no meat dish to balance the meal; only egg dishes as "vegetable dishes"), it sometimes ignores the instruction and searches anyway, costing 2-4 extra calls, never correctness. The only firm fix found is to offer no search tools when every needed type is complete (changes the tool set per run); not done. Calls are judged by the average, and the script's per-scenario "at most 5" mismatch is a warning.
- **`tools_version` is one hash over all tools.** When it changes between two runs, finding which tool changed means comparing `tools.json`; a per-tool hash in `summary.json` would show it directly. Tool behavior changes that keep the schema are identified only by the git commit.
- **The note checks are heuristics (chat page redesign, Task 4).** `counts_dishes` is a pattern over numbers next to dish words; it was tuned against natural tips ("荤素一起", "第一道", "最费时的一道", "这一道菜") but may still miss or over-flag wordings not seen yet, so read flagged notes before trusting a rate. **Measured against the user's review in round 7, the automatic note checks and the human ratings disagree widely:** of 100 rated notes, `counts_dishes` flagged 25 and the user marked none of them as wrong (they say "两道菜可同时上桌", which the user accepts); the one note the user marked wrong (a loose dish name, "肉丝", matching no dish in the plan) was flagged by no check; and wrong facts such as "一荤一素" for two vegetable dishes pass every check. Until the checks are reworked, the user's ratings decide note quality, and the automatic note rates are not used to compare models or prompt versions. `multi_sentence` counts an abbreviation such as "e.g." as a sentence end. `notes_review` writes a row only for plans whose own model note was checked; a plan shown with only the fixed closest-attempt text has no row.
- **`--planning-model` adds the catalog name as a model key** (`pin_task_model` in `llm/config.py`); a models key with the same name would be overwritten silently. Unlikely with today's keys (`gemini`, `openai`, `deepseek`); add a guard if keys ever reuse catalog names.

### Tests and cleanup

- The "must use" shortlist test asserts the outcome without isolating the "must use" protection; the retry-wording test now checks only the dish part.
- A long line in `llm/smoke.py`, an obscure condition in `tests/fakes.py`, and single quotes in migration `0007`.
- The plan card's list of known next actions (`KNOWN_ACTIONS` in `PlanCard.tsx`) repeats the `NextAction` type by hand; a typed record would catch drift at compile time.
- `tests/test_planning_shortlist.py` has a line over 100 characters (complete-marker test).
- `COUNTED_DISH_TYPES` in `planning/composition.py` spells out the meat and vegetable categories instead of reusing `SPLIT_CATEGORIES` (cosmetic; final 2a fix wave re-review).
- The per-run query-embedding cache drops other runs' entries when a run starts, so two conversations planning at the same moment may each embed a query once more than needed (final 2a fix wave; correctness unaffected).
- The package-data test (`test_package_data_glob_matches_every_prompt_file`) uses `fnmatch`, whose `*` also crosses `/`, so a too-broad glob would still pass; it does not build a wheel.

## Deferred from the import and dictionary plan

- `create_recipe(...)` call in `backend/minemydinner/api/recipes.py` is a little over 100 characters (Task 1 review).
- The built-in `Ingredient(...)` test setup is repeated in `test_recipe_save.py`, `test_recipes_manage.py`, and `test_personal_aliases.py`; a shared helper in `tests/recipefakes.py` would remove it (Task 1 review).
- Two lines of one save that link the same unknown name to different entries with "remember" checked: the first wins and the second is reported as a conflict; untested (Task 1 review).
- `link_name` has no test that already-linked lines with the same name are left alone; `POST /api/ingredients/match` queries each duplicate name in a request separately (harmless at 200 names or fewer) (Task 2 review).
- The suggestion check script (`evaluation/allergen_suggest.py`) and two new dictionary tests have lines over 100 characters; a `names.yaml` row missing a field raises a bare `KeyError` instead of a clear message (Task 3 review).
- The recipe edit page does not show `alias_conflicts` after saving (it navigates away), so a "remember" that hit another entry's alias is dropped silently there; the import review shows it (Task 4 review).
- `ReviewForm`'s re-match runs once per mounted form and relies on `ReviewQueue` keying the form per draft; add a note on the `rematch` prop. Same-name linking inside one draft compares names ignoring case, while the server match is exact (harmless). The inline line `onChange` in `ReviewForm` could become a named function (Task 4 review).
- Page size (Task 5b review): the backend test covers only `page_size=30`, not a non-numeric value; a page-size change made in another browser tab does not reset the library's page, so it can land past the last page; the library fetches once more on first load when a non-default size is stored; with no `tab` in the URL, resolving the last unknown row switches the dictionary page to "All ingredients" by itself.
- UI consistency (Task 5c review): the chip-layout test checks class names only (jsdom cannot test wrapping); a field whose value happens to equal the suggestion shows the "suggested" badge; the people add-rule row relies on `display: contents` for the search bar, so check it with a screen reader.
- Amount controls (Task 6 review): an extracted line can disagree with itself (marked vague but carrying a number, or exact without one); the row shows it as extracted and saving normalises it (number kept with the derived type, or saved as not stated). `AmountProblem` is now a single-case object that could be a boolean.
- Buttons (Task 5c): secondary buttons rely on the plain `button` default; only primary, danger, and compact toggles carry a class, so a new button that should be primary must say so. (Every page was measured in a browser and checked by the user.)
- Page state (Task 5b): after deleting the last row on a page the page state stays and the display clamps, so adding a row later jumps back to the stored page.
- Tab counts show (0) while their lists load.
- Switching tabs closes open forms and panels and clears unsaved input.
- An old `#cookware` link briefly shows the stock tab before switching.
- Reopening the unknown-list confirm panel calls the suggestion model again.
- The narrow-width layout of the new panels was checked by reading the CSS, not on a phone.
- `RecipeOut.alias_conflicts` appears as `[]` in every GET and list response though it only means something in the PUT reply; a separate `UpdatedRecipe` model would keep GET clean (final review).
- Review row (final review re-check): the "undo unlink" handler repeats the shape of `link()` instead of sharing a small `restore()` helper; the `previous` state type is one long line; a line that was matched at extraction, unlinked, and then linked from the create panel's owner button stays a "correction" without the remember checkbox, by the existing rule.
- Suggestion quality (Task 7): `allergen_suggest_v2` scored category 18/18, allergens 17/18, stock mode 15/18 on the approved set; the stock-mode misses (豆腐, 基围虾, 香菇) are household choices. The user kept v2. If confirming new entries keeps needing the same stock-mode fixes, a `_v3` with household-specific guidance and another check run on the same set is the next step.

## Minor findings from the final Phase 0 review, deferred

- English method-stated amounts ("two eggs" in a step) are over-cleared to vague by the corroboration guard; adding English number words to the window check would reduce false clears. Safe direction as is.
- The anti-fabrication guard trusts `raw` verbatim; checking `raw` appears in `source_text` (when available) would close a small fabrication path.
- The allergy check posts on every keystroke in the review form; a ~300 ms debounce would cut the chatter (database-only, no model cost).
- An empty Content-Type is treated as HTML in the web import, yielding `no_main_text` instead of the more accurate `not_html`.
- `GET /api/recipes` loads ingredients, cookware, and images per row (N+1); use `selectinload` when the library grows.

## Deferred from Phase 1 plan 2b

Findings judged safe to defer during plan 2b's task reviews. The final whole-branch review triages them again before merge.

- **Label matching edge cases (Task 2 review).** In `agent/labels.py`, when case folding changes a text's length (rare characters such as ß), the fallback compares the unfolded text with a folded nickname, so a mixed-case Latin nickname can be missed; `term.casefold()` can likewise change a food term's length, making the "longer food term" guard slightly inexact. Two saved friends with the same nickname: only the first is ever matched (worth a uniqueness note on the people page). `restore_labels` turns "Diner 1's" into "你's" in an English note.
- **Parse fields (Task 3 review).** `StatedRules` drops an allergen value outside the fixed list silently (spec 5.4), so a variant such as "peanuts" from the model would lose an allergy without a trace; the schema enum makes it unlikely, and the diner card shows what was kept. A log line or a visible "unrecognized allergy" note on the card would make it traceable. `questions.py` keeps its own copy of the chat's question wording; its test catches drift in existing keys and new problem keys, but not a new field key. The trim removed the examples "来个凉菜", "多来点荤菜", "两三个菜", and the egg phrases, whose wording stays in the rules; the regression runs decide whether any returns (as `parse_v5`, since `parse_v4` will then be used).
- **Diner card wiring (Task 4 review).** `with_labels` reloads friends, dictionary terms, and recipe titles on every graph node (about 10 times per run) and again in the card check; a per-run cache would remove the repeats. The card check and `select_diners` each find the matched friends; the uncovered-rule loop in `graph.py` belongs in `agent/diners.py`. `agent/graph.py` is about 1,200 lines; the diner logic and `with_labels` are the next candidates to move out. One graph test resumes the card with a bare value instead of by interrupt id. A solo "我吃素" gives diet words but no rule, so no card appears and the diet-word hint is never shown (a product call). `planning/search.py` and `planning/tools.py` now import `agent.labels` (no cycle; one import out of isort order). The label lock test passes even without the lock (the race is rare); it guards against regressions only.
- **Diner card answer (Task 5 review).** A meal-only rule the user stated about themselves on a solo card is dropped when a later "就我自己吃" clears the settled set (spec 5.1 behaviour; say so in the UI if users notice). The card's history summary (`names`, `others`) cannot show that the user deselected themselves. No test loads an old checkpoint that still holds the removed `diners_answer` channel. `has_temporary_guests` looks at this run's parsed guests and the settled guests: on a later run of the same thread, a guest named only in earlier messages (which the route and parse resend) is not flagged; flag a thread once any guest was named, if evaluation exports start relying on it.
- **Left-out dishes (Task 6 review).** No test covers an unknown-allergen dish of a requested type that the shortlist and searches never reached (it is not listed on a successful plan, by design). `present_plan` opens one more session to build the diners line.
- **People API (Task 7 review).** `list_diners`, create, and rename load every dictionary term and recipe title once per request to compute `nickname_warning` (fine at personal scale; cache if the dictionary grows). A one-letter Latin nickname ("a") always warns, since it appears inside many terms. `imports/web.py` keeps its own `_is_cjk` next to the public `agent.labels.is_cjk`.
