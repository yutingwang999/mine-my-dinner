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

## Minor findings from the final Phase 0 review, deferred

- English method-stated amounts ("two eggs" in a step) are over-cleared to vague by the corroboration guard; adding English number words to the window check would reduce false clears. Safe direction as is.
- The anti-fabrication guard trusts `raw` verbatim; checking `raw` appears in `source_text` (when available) would close a small fabrication path.
- A failed `llm_calls` write discards the paid model result (`client.py` `_log` has no error handling).
- The allergy check posts on every keystroke in the review form; a ~300 ms debounce would cut the chatter (database-only, no model cost).
- An empty Content-Type is treated as HTML in the web import, yielding `no_main_text` instead of the more accurate `not_html`.
- `GET /api/recipes` loads ingredients, cookware, and images per row (N+1); use `selectinload` when the library grows.
