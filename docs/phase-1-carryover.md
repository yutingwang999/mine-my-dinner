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
- **Other diners' restrictions in the request text are not captured (plan 2, diner selection).** A request such as "me and Alex, who is allergic to shrimp" is not parsed by `parse_request` or checked by code in plan 1; only the generic S8 warning (unconfirmed diners) appears. Plan 2's diner selection must capture such statements, or at least flag them on the plan card, so a stated allergy is never left to the model's judgment.

## Deferred from Phase 1 plan 1

Task-review and final-review findings that were judged safe to defer. None blocks the first slice; each names where it most likely lands.

### Planning and safety (plan 2, rest of T1.4)

- **Unknown-allergen override ignores the diner.** An `H2Override` with `unknown_allergens` covers a recipe for every diner. When manual edits create it, decide whether each diner with an allergy must confirm, or check that `diner_key` belongs to such a diner.
- **`check_recipes` treats nonexistent recipe ids as safe.** Every caller checks existence today (H5, the tools' known-id check, search), so it is safe in practice; keep it that way or make the function report unknown ids.
- **S3 calls `date.today()` inline.** Pass `today` as a parameter; tests near midnight can flake.
- **S1 and S6 skip optional lines, and S1 reports per line rather than per ingredient.** Plan 2a makes S1 sum per ingredient across the plan's dishes (warning only).

### Shopping list (T1.5)

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

- **No guard against concurrent requests on one thread** (two messages, or a message and a resume). Acceptable for one user; add a per-thread lock before anything multi-user.
- **A new message while a question is pending starts a fresh run** and drops the pending question. Decide whether to block it (409) or keep allowing it.
- **Progress events fire when a node finishes,** so nothing shows during the first model call. A start-of-node signal would need another stream mode.
- **The chat runtime is built lazily** on the first request and could be built twice by two concurrent first requests; build it in `lifespan`.
- **Only the latest plan is shown and restored after a reload;** `plan_versions` is kept in state but not returned by `GET /api/chat/threads/{id}`.
- **The chat input is a single line;** a textarea suits long requests.
- **The stream is not aborted when the page unmounts.**
- **A non-404 failure while loading a stored thread** keeps the stale id in `localStorage`; the next send then starts a new thread and overwrites it.
- **The request-style select updates before the resume returns,** so a failed resume can show a style the backend did not save until reload. A restored pending question can be stale if the backend advanced before an error event.
- **`token` events are reserved but never sent** in this slice; the page handles them if they arrive.

### Evaluation (T1.10)

- **The planning evaluation's database check** compares against the configured test URL and the main database name but does not require a `*_test` name; `ensure_empty` is the remaining guard.
- **The summary has no "as expected N/M" line,** so a scenario that is meant to fail lowers the pass rates without comment.
- **A hard kill skips cleanup;** the next run then refuses until the test database is emptied by hand. `main()` also opens the checkpointer before `ensure_empty`, and a connection error prints a raw traceback.
- **`planning_eval` spending is counted only in the test database's `llm_calls`,** so evaluation spend never counts toward the real monthly cap. Document it or count it centrally.
- **Kept rows matter.** The test database keeps `llm_calls` and `planning_runs` rows from evaluations and real-app checks. Tests must not assume these tables are empty and must delete only rows they create. Before plan 1's final fixes, a Phase 0 fixture deleted the whole `llm_calls` table, which lost the per-call rows of plan 1's Task 14 check and Task 15 run (the baseline totals above survive).

### Import, dictionary, and extraction (from plan 1 Tasks 1-4)

- **Personal aliases:** no size limit on `PersonalAliasesIn`; the dictionary browser can show a stale dish count after a refresh.
- **Amount guard:** digit and decimal corroboration is still a substring match ("2" inside "12", "1.5" inside "11.5"); `raw`-in-source is a bare substring and can join across line breaks. The candidates are listed in design 8.4 for T1.10.
- **Web import:** `http://` links still swallow directly following CJK text.
- **Smoke tool:** the vision check passes on "not red" or "infrared"; `_report` reads the latest `llm_calls` row, which can be stale or missing when the cap stops a call; the smoke test does not assert the `bind_tools` options or the failure path.

### Cleanup

- Import order in several tests; a stale comment above `asked_question` in `agent/state.py`; a test named `commits_without_reaching_the_database` that does reach it; long lines in `stock.py` and `test_planning_tools.py`; weak `!= 403` assertions and no custom-origin test for the Origin check; `engine.dispose()` not in a `finally` in `lifespan`; `route.md` re-read on every call; `GraphDeps` fields typed `Any`; the `.allergen-notice` class has no CSS rule of its own.

## Deferred from Phase 1 plan 2a (in progress; updated after each task)

Findings judged safe to defer during plan 2a's task reviews. The final whole-branch review triages them again before merge.

### To revisit

- **The feasibility check runs again before every retry.** Within one planning run its inputs (request, library, stock, cookware) do not change, so today the second check always repeats the first. It is kept on purpose (decided with the user): it becomes useful once stock or diners can change during a run (an `update_inventory` planner tool, diner selection in plan 2b). Revisit then, or remove it if neither arrives.

### Planning logic

- **Named ingredient stocked at count 0.** The quick "is it at home" test reads every named ingredient leniently (as a seasoning), so a non-seasoning such as eggs at count 0 passes that test. The per-recipe check still stops the request, with "only 0 dishes with 鸡蛋 can be made" instead of "no 鸡蛋 in the fridge". Passing the ingredient's real role would give the plainer wording.
- **A recipe with no ingredient lines counts as makeable**, so it also keeps the empty-fridge reason from appearing.
- **`uses_expiring` lists optional lines** in search hits and the shortlist.
- **Mixed units for one ingredient** (grams on one line, pieces on another) are not summed together by S1; the later unit is skipped, as before.
- **Short unknown names under "must use"** match more widely now that the original line text is searched too (as the design allows).
- **"Any mix of meat and vegetable dishes" is not offered for a combined dish shortfall**; that case only arises when no split was named, so it does not matter today.

### Parsing and wording

- `parse.md` (plan_v2) has no example of moving "must use" names to "prefer" after "the named ingredients are optional", and no example of a soup range.
- `_counts_text` would produce an empty clause if a spec required no dishes and no soups; unreachable while every spec needs at least one dish in total.
- The planner's request summary prints an explicit "0 vegetable dishes" when a split names one side as 0, and states "Every count is exact unless it says at least". Both are new model-facing wording to watch in the evaluation.

### Cost accounting

- Provider-prefixed cache fields (such as OpenAI's `priority_cache_read`) are not read; the app uses the Standard tier.
- Cache counts that a provider does not report are stored as 0 rather than null (priced as uncached either way).

### Evaluation

- The planning evaluation's data is committed in `backend/eval_data/planning/` (fictional, hand-written; moved there from the git-ignored `data/eval/planning/` during plan 2a): `library.yaml` and `scenarios_baseline.yaml` are plan 1's 15 dishes and 7 scenarios, kept unchanged for like-for-like comparison with its baseline; `library_extended.yaml` (23 dishes) and `scenarios_plan2a.yaml` (19 scenarios) test plan 2a's features. Keep the baseline pair unchanged in later plans, or record a new baseline when it must change. Planning scenarios derived from real use stay in `backend/data/`, outside Git.
- "No invented numbers" for vague requests is checked through the default counts; whether the reply's wording invents numbers is still read by hand. The quality of a plan (balance, "清淡", "丰盛") is read by hand until the T1.10 judge-model metric exists.
- The reply-language check removes recipe titles and then looks for Chinese characters: an English reply quoting a Chinese ingredient name would fail it, and a mostly English reply with any Chinese word would pass a Chinese check.
- The time-limit scenario relies on snack dishes with stated times, because the feasibility check never blocks dishes whose time is estimated.

### Tests and cleanup

- The "must use" shortlist test asserts the outcome without isolating the "must use" protection; the retry-wording test now checks only the dish part.
- A long line in `llm/smoke.py`, an obscure condition in `tests/fakes.py`, and single quotes in migration `0007`.
- The plan card's list of known next actions (`KNOWN_ACTIONS` in `PlanCard.tsx`) repeats the `NextAction` type by hand; a typed record would catch drift at compile time.

## Minor findings from the final Phase 0 review, deferred

- English method-stated amounts ("two eggs" in a step) are over-cleared to vague by the corroboration guard; adding English number words to the window check would reduce false clears. Safe direction as is.
- The anti-fabrication guard trusts `raw` verbatim; checking `raw` appears in `source_text` (when available) would close a small fabrication path.
- The allergy check posts on every keystroke in the review form; a ~300 ms debounce would cut the chatter (database-only, no model cost).
- An empty Content-Type is treated as HTML in the web import, yielding `no_main_text` instead of the more accurate `not_html`.
- `GET /api/recipes` loads ingredients, cookware, and images per row (N+1); use `selectinload` when the library grows.
