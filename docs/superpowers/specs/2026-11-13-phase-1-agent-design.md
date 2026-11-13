# Phase 1 (Agent) Design

**Status:** approved approach, written 2026-11-13. Branch: `feat/phase-1-agent`.

**Specification:** `docs/design.md` as a whole. Phase 1 rests on 7.x (tools, Skills, graph, state, `validate_plan`, meals for several people), 5.5 (allergies and unrecognized ingredients), and Section 8 (evaluation and the regression gate). Its tasks are T1.1-T1.11 in Section 11. This document does not restate those rules; it records how Phase 1 will be built, the order, and the decisions made while planning it. Where this document and `design.md` disagree, this document records a decision made after `design.md` was written, and the task that makes the decision must also update `design.md`.

**Other inputs:** `docs/phase-1-carryover.md` and the still-open table in `docs/phase-0-import-feedback.md`. Every item in both is placed below.

## 1. Intent

Phase 0 built the data foundation: 134 reviewed recipes, the dictionary, inventory, diners, and extraction evaluation. Phase 1 turns that data into the product's core: a conversational agent that plans one meal from the user's own library and fridge, revises it over several turns, handles several diners safely, remembers preferences, and is measured by a planning evaluation. At the end, the repository is made public.

The user found most Phase 0 problems by using the app, not from tests. Phase 1 is therefore ordered so the user can plan a real meal in the browser as early as possible, and every later capability arrives together with the UI that exercises it.

## 2. Goals, non-goals, anti-goals

**Goals**

- Everything in the Phase 1 acceptance line of `design.md` Section 11.
- Hard constraints are enforced by code (`validate_plan`, search exclusions), never by the model.
- Every model call, including every planning turn, goes through the one call layer that applies the monthly cap rules, failover, `llm_calls` logging, and nickname replacement. Planning keeps its Phase 0 `essential: true` status, so the cap does not stop it (design 3 stops only non-essential calls); it is still logged and counted.
- Each planning run leaves a complete, local record that the planning evaluation and debugging can use (Section 6).

**Non-goals**

- Automatic inventory deduction (design 5.4: Phase 1 is manual).
- Public-demo switches (read-only library, no memory or record writes; design 7.6). They arrive with deployment in Phase 3. Phase 1 keeps write paths in a few service functions so a switch can be added there (Claude's call).
- Web search for substitutions, video import, MCP server, cloud deployment (Phase 3).
- Pinning resolved addresses in web import against DNS rebinding (moved to Phase 3 deployment; see Section 9).
- A separate toast dictionary entry (吐司 stays an alias of 面包 for now; the broader gap of missing common aliases is addressed by personal aliases in T1.0).

**Anti-goals** (a failure even if everything else works)

- A model can bypass a hard constraint, a confirmation step, or the call layer (and with it the cap rules and logging).
- A saved friend's nickname reaches a model provider.
- Any UI showing a plan or substituted dish lacks the permanent allergen notice.
- The user cannot try the agent in the browser until the end of the phase.

## 3. Constraints

- **Python:** use the interpreter from `.vscode/settings.json`. Packages are not installed by the agent; the plan asks the user to install them. Phase 1 needs `langgraph-checkpoint-postgres` (with `psycopg-pool`) for the checkpointer and Store. `langgraph` 1.2.11 is already present through `langchain` 1.4.2. Any version pins are added to `backend/pyproject.toml` with the exact installed versions.
- **Backend style:** the backend is synchronous (sync SQLAlchemy sessions, `def` routes). Phase 1 stays synchronous: the sync `PostgresSaver` and `PostgresStore`, sync graph invocation, and a sync generator behind a streaming response.
- **Frontend:** Next.js 16 with next-intl and plain `fetch`; no new frontend dependencies are needed for streaming (read the response body stream).
- **Git:** the user performs all repository-changing Git operations. Plans give the exact commands and stop.
- **Secrets:** `.env` is never read or edited; plans say which variables the user must set.
- **Language:** all files in English, except Chinese UI strings, Chinese dictionary names and aliases, and Chinese recipe text in tests and prompt examples.
- **Real-app checks** use a test database (override `DATABASE_URL`), never the real `minemydinner` database.
- **Prompt versions:** the extraction prompt is now `extract_v1` with logs, so any change becomes `extract_v2`. The new planning prompt starts at `plan_v1` and follows the same rule once planning records exist.
- **Frontend tests** run through PowerShell (Git Bash vitest fails collection).

## 4. Approaches

**Order of work.** Three orders were considered:

| Approach | Trade-off |
|---|---|
| A. Follow T1.1-T1.11 layer by layer | Each layer is fully tested before the next, and the plan maps one-to-one to the design. But the chat page arrives in T1.9, so hands-on discovery, which found most Phase 0 issues, comes last |
| **B. Thin end-to-end slice first, then widen (chosen)** | After the deterministic core, build a minimal planning path with a minimal chat page, so a real meal can be planned in the browser early. Later tasks each add their UI to the working page. Cost: T1.9's deliverables are spread across tasks, so the plan labels which design task each step belongs to |
| C. Evaluation first | Writing planning scenarios before the agent exists tends to aim them wrong. T1.4 already requires a minimal evaluation, which B includes |

**Agent loop.** Two ways to build the planner ⇄ tools loop were considered:

| Approach | Trade-off |
|---|---|
| Hand-written planner and tool nodes calling `LLMClient` | Full control and no framework semantics to learn, but the loop (parsing tool calls, executing, appending results, stopping) and its edge cases are ours to write and test |
| **Outer hand-written `StateGraph` + inner `create_agent` with our own middleware (chosen)** | The outer graph follows design 7.3 exactly and keeps `validate_plan` as its own node, so retries are counted cleanly. The inner loop, streaming, and call-count limits come from LangChain 1.x. The call-layer guarantees become one `wrap_model_call` middleware: cap rules, failover, `llm_calls`, the planning record, and nickname replacement on the outgoing request with restoration on the response (Section 6.1). Built-in `ModelFallbackMiddleware` and `PIIMiddleware` are not used, because they do not log to `llm_calls` and do not know the nickname table. `ModelCallLimitMiddleware` and `ToolCallLimitMiddleware` are used as runaway guards |

Prebuilt agents without our middleware were rejected because they would bypass the cap, logging, failover, and anonymization.

**Shared call core.** `LLMClient.invoke_structured` currently holds the cap check, candidate failover, and logging in one method. T1.1 extracts that into a shared core used by structured calls, embeddings, and the planning middleware, so all tasks count cost and failover the same way.

## 5. Order of work

Each step names the design tasks it delivers. The plan may split steps further.

| Step | Delivers | Content |
|---|---|---|
| 1 | **T1.0** (new) | Data-affecting Phase 0 follow-ups and fixes (Section 8.1). The deepseek-flash vision check (Section 7). The Origin/Host check is **not** here; it lands in step 3 |
| 2 | **T1.1, T1.2** | Deterministic tools and `validate_plan` with full unit tests: the safety floor. Tool-calling support in the call layer (shared core, provider quirks in Section 7). Vector search in `search_recipes`. T1.2 extends the dietary-rule check to dietary restrictions and dislikes (Phase 0 checked only `allergy`) |
| 3 | **T1.3** + thin **T1.4** + minimal **T1.9** | Graph skeleton, `route_intent`, Postgres checkpointer, resumable conversations. The core planning path for **one diner**: `parse_request` (interrupt when information is missing) → `load_context` → planner ⇄ tools → `validate_plan` retry loop → `present_plan`, in both fridge-only and shopping-allowed modes. Planning records (Section 6), request defaults with the first-use question and its setting (6.2), the no-valid-plan behavior (6.3), and `plan_v1`. A minimal chat page: streaming, plan card, the permanent allergen notice, and the notice that conversation content may be sent to the model provider. The Origin/Host check on all POST routes. A minimal planning evaluation of 5-10 scenarios. **Checkpoint: the user plans a real meal in the browser** |
| 4 | rest of **T1.4** | Diner selection interrupt and card, headcount mismatch, local nickname matching with replacement and restoration in the call layer's hooks (filling the empty Phase 0 hooks), ranking signals, dishes left out for unknown allergens with a way to resolve them (5.5), `exclude_recent_days` only when asked. UI for each |
| 5 | **T1.5** | `revise_plan`, plan versions, revalidation on diner changes, saving the accepted plan, shopping list with confirmation, manual plan and shopping-list edits with the edit log. UI for each |
| 6 | **T1.6** | Ratings, notes, dietary rules, LangGraph Store semantic memory, `extract_memories` with confirmation (including learned request defaults, 6.2), saving friend information, the friend-deletion cleanup (7.6) and deletion of a recipe's Store notes. UI for each |
| 7 | **T1.7** | `SKILL.md` files, `load_skill`, system prompt with names and descriptions only |
| 8 | **T1.8** | Subgraphs B, C, D with their interrupts and UI |
| 9 | rest of **T1.9** | Saved-plans page with shopping lists; conversation deletion and the explanation of how conversations are kept; the delete-friend dialog's conversation warning and link; the read-only recipe detail view (Section 8.2); Chinese/English switching with the agent replying in the user's language, if not already complete |
| 10 | **T1.9b** (new) | Phase 0 page usability, UI-only items (Section 8.3), and one visual and mobile design pass over old and new pages together |
| 11 | **T1.10** | Planning evaluation at 30-50 requests, DeepSeek/OpenAI/Gemini comparison, the regression gate for extraction and planning, and the evaluation gaps (Section 8.4) |
| 12 | **T1.11** | Publication checklist (Section 9) |

## 6. Planning records

Each planning run (a planner pass from the request until `present_plan`, including revisions) writes one local record, so the planning evaluation (T1.10) and debugging have the full picture. Contents:

- the user request as sent to the model;
- the tool definitions offered (stored as a version or hash of the shared JSON Schemas);
- each model call's messages as sent to the model, each tool call's name and arguments, and each tool result;
- tool calls that failed schema validation, recorded rather than dropped, so T1.10 can report a tool-call validity rate;
- the final plan, each `validate_plan` result, and the retry count;
- provider, model ID, prompt version (`plan_v1`), and the `thread_id`;
- a flag set when the run included temporary guests' information, which local code cannot anonymize, so exports can exclude it.

- which request details were filled by a default (Section 6.2), so the default-override rate can be computed.

**Content is recorded as sent to the model, after nickname replacement**, never in the restored form shown to the user. The recording point is inside the `wrap_model_call` middleware, after replacement.

**Storage:** a new local table (name left to the builder) in the same database, never shown in the UI, never exported to the repository, excluded from the public demo (which writes no records). An export script writes it to `data/` for evaluation.

**Retention (decided):** during development, deleting a conversation keeps its planning records. The conversation-deletion explanation must say so plainly ("planning records used for evaluation stay on this computer"). **Revisit before the public demo is deployed or before anyone other than the developer uses an instance.**

### 6.1 Nicknames in conversations (Claude's call, from design 7.6)

Conversation state (the checkpointer) keeps saved friends' real nicknames, because design 7.6 says conversations may still contain a deleted friend's information and a reopened conversation must read naturally. Replacement happens only on the outgoing request inside `wrap_model_call`, and restoration on the model's response, including streamed text: the stream buffers enough to restore a code such as "Diner 1" that arrives split across chunks. Tool arguments the model sends with codes are restored before tools run; tool results are replaced before they return to the model. Planning records keep the replaced form.

### 6.2 Missing request details (decided)

Instances are single-user, but different people will run them, so the agent must not assume one person's habits.

- **Defaults:** headcount is the user alone unless others are mentioned (others go through 7.6). Dishes and soups default by headcount as a starting point: 1 person → 1 dish; 2 → 2 dishes; 3-4 → 2 dishes and 1 soup; 5 or more → 3 dishes and 1 soup. Mode defaults to fridge-only. No time limit unless stated. The plan card states every defaulted value, and one sentence changes it.
- **First-use question:** the first time a user asks for a plan, the agent asks once whether, when details are missing, they prefer to be asked first or to get a plan and adjust it. The answer is saved as a preference and can be changed in settings. Skipping it means "plan then adjust". With "ask first", `parse_request` interrupts when the mode or the dish count is unstated.
- **Measurement:** each planning record marks which details were defaulted; T1.10 reports the default-override rate (how often the next turn changes a defaulted value). It is computed per instance and stays local.
- **Learned defaults (T1.6):** when the user repeatedly changes a default in the same way, `extract_memories` proposes a preference such as "usually two dishes and a soup"; once confirmed, it replaces the headcount-based default.
- Genuine contradictions (such as a headcount that disagrees with the named diners) always interrupt, regardless of the preference.

### 6.3 When no valid plan is found (decided)

When `validate_plan` still fails after N retries, or fridge-only mode cannot reach the requested composition:

- A plan that breaks H2 (allergies, restrictions, unknown allergens under 5.5) or H5 is **never shown**.
- Otherwise the closest attempt is shown, with each unmet rule stated plainly ("2 ingredients missing", "only 1 dish possible from the fridge"), and next actions offered: switch to shopping-allowed, fewer dishes, or drop the time limit.
- Evaluation counts such a run as failed.

### 6.4 Amounts and headcount (decided)

Phase 1 does not scale recipe amounts to the meal's headcount. `compute_missing`, S1, and the shopping list compare and list amounts as the recipe states them. When a recipe's `servings` differs from the meal's headcount, the plan card and the shopping list say so ("this recipe serves 2; this meal is for 4"), and the user judges. Many recipes have no servings, vague and spoon amounts cannot be scaled, and design 5.1 forbids model estimates of amounts.

### 6.5 Monthly cap during planning (Claude's call)

Planning is essential and is not stopped by the cap. Query embeddings for `search_recipes` use the non-essential `embeddings` task; once the cap is exceeded they fail, and search falls back to filters plus name and alias matching, with a one-line notice that semantic search is paused. Planning continues. T1.10's evaluation runs use their own non-essential task name, so evaluation is counted and capped (design 8).

## 7. Model and API decisions

- **Tool calling on OpenAI (fallback):** gpt-6-luna function calling through Chat Completions requires `reasoning_effort: none`. Set it for the planning task's OpenAI candidate and cover it in tests.
- **DeepSeek thinking mode stays disabled.** If it is ever enabled for planning, every later request must pass `reasoning_content` back, or the API returns 400. Record this next to the `models.yaml` option.
- **deepseek-flash vision (decided):** in T1.0, run one real smoke call sending an image to deepseek-flash. If it works, change `design.md`'s capability table to ✅ for DeepSeek images with the source and date; if it fails, remove `images` from its `models.yaml` capabilities. Task assignments do not change either way: image tasks stay on Gemini with OpenAI fallback, and the planner never receives images.
- **Embedding estimate:** `len(text)//4` undercounts Chinese. It stands; Store embeddings (notes) add little volume. Recheck if volume grows.

## 8. Placement of carried-over items

### 8.1 T1.0: data-affecting follow-ups

From the still-open table and the user's additions:

- Personal aliases on built-in (seed) entries, so wording such as 马铃薯丁 links automatically. This is the general fix for common aliases the seed dictionary misses. Search, normalization, and H2 alias matching must all see personal aliases. They are stored apart from the seed rows, because reseeding overwrites seed aliases; adding one relinks existing unlinked lines with the same name, reusing the existing relinking approach (`link_unmatched_lines`).
- Recognize links without `http://`/`https://` (`www.` links, app short links).
- Lower the 200-character main-text floor for Chinese web pages.
- Warn when several recipes are pasted without a `---` line (the model reports how many recipes it sees). This changes the extraction prompt, so it becomes `extract_v2`.
- Fix the stale import-page hint that says screenshots are not stored (`messages/en.json` and `zh.json`, `import.screenshotsHint`); screenshots are kept locally with the recipe (design 4.2).

Deferred minors from the final Phase 0 review that change extraction results, so the T1.10 gate's first baseline includes them:

- Add English number words to the method-amount window check.
- Check that `raw` appears in `source_text` when source text exists.
- Treat an empty Content-Type as `not_html`, not HTML.

### 8.2 Inside other Phase 1 tasks

- `_log` in `llm/client.py` has no error handling, so a failed `llm_calls` write discards a paid result: fix in step 2 while extracting the shared call core.
- `GET /api/recipes` N+1 loading: use `selectinload` in step 2 alongside `search_recipes`.
- Read-only recipe detail view (the user's addition): the library shows only the source; structured ingredients appear only in the edit form, and users do not think to click Edit. Build a read-only detail view (ingredients, cookware, time and source, steps summary) in step 9, shared with the chat plan card's "view dish".
- T1.6 also deletes a recipe's Store notes when the recipe is deleted, and cleans up saved plans when a friend is deleted (7.6).
- T1.9's delete-friend dialog (built in Phase 0 Task 7) gains the conversation warning and link.

### 8.3 T1.9b: UI-only usability

- Keep queued review drafts in local storage, so a reload does not lose them.
- Progress during batch import ("3 of 10 done").
- Edit form for cookware entries (the API exists).
- Cache allergen suggestions for the page session.
- Paging for "My ingredients" and "My cookware" on the dictionary page, like the built-in browser (the user's addition).
- ~300 ms debounce on the review form's allergy check.
- One visual and mobile design pass across all pages, old and new.

`docs/phase-0-import-feedback.md` gains a "Lands in" column in its still-open table and rows for the user's additions, so each item points to its task.

### 8.4 T1.10: evaluation gaps

- Note wherever `valid_json_rate` is reported that it is structurally 1.0 (only schema-valid outputs produce a log row); the real parse-failure signal is `success_rate` in `llm_calls`.
- Lines added by the user no longer count as lines the model missed: when adding a line, ask whether the source has the ingredient, or exclude added lines from recall.
- Include `image_files` in the evaluation export so screenshot samples can be rerun.
- Duplicated ingredients across list, method, and formula lines: tracked by the metrics; revisited in `extract_v2` or with a stronger model.
- The gate's baseline records the evaluation set's hash and the prompt versions (`extract_v2`, `plan_v1` or later).

## 9. Publication checklist (T1.11)

In addition to `design.md` T1.11 (README, architecture diagram, LICENSE, a full-history secret check, cleanup of secrets and private data, making the repository public):

- Make `USER_AGENT` (now a constant in `imports/web.py`) configurable, add a contact URL, and tell forks that change fetch rules to use their own name.
- Choose the license and add usage terms to the README.
- The full-history secret check replaces the dropped gitleaks pre-commit hook.
- README "known limitations": web import does not pin the resolved address (DNS rebinding), acceptable while the instance is private; fixed before any public deployment in Phase 3.
- Confirm that no committed file contains private planning content and that `data/`, `docs/private/`, and `private/` stay ignored.

The Origin/Host check on POST routes is done earlier, in step 3, because the chat endpoint can trigger repeated paid planning calls from a cross-site request.

## 10. Left to the builder

- Exact state field types, node names beyond those in design 7.3, and the planning-record table's name and columns.
- How streaming is framed (server-sent events or a newline-delimited stream), as long as interrupts reach the page as confirmation UI.
- The 5-10 minimal scenarios and later the 30-50 requests, using fictional inventories, not the real database.
- The retry limit N for `validate_plan` (design leaves it open) and the call-count limits for the runaway guards, stated in the plan.
- Splitting steps into plan tasks, provided each step's checkpoint and test coverage remain.
