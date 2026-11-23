# Local Development

How to start the app locally and how to inspect the evaluation data in the database.

All commands are for PowerShell and start from the repository root unless a step says otherwise.

## Services

| Service | Address | Started by |
|---|---|---|
| Postgres | `127.0.0.1:15433` | Docker container `minemydinner-db` |
| Backend (FastAPI) | `http://127.0.0.1:8000` | uvicorn |
| Frontend (Next.js) | `http://localhost:3000` | `npm run dev` |

Check which ports are already in use before starting anything:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8000,3000,15433 -ErrorAction SilentlyContinue
docker ps
```

## Configuration

The app reads its settings from environment variables, loaded from `.env`. Copy `.env.example` to `.env` and fill in the values. Never commit `.env`.

## Python interpreter

Use the interpreter set by `python.defaultInterpreterPath` in `.vscode/settings.json`. Read it into a variable in each new terminal:

```powershell
$py = (Get-Content .vscode/settings.json -Raw | ConvertFrom-Json).'python.defaultInterpreterPath'
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONUTF8 = "1"
```

The backend and evaluation commands below use `$py`.

## Start the app

### 1. Database

```powershell
docker compose up -d
```

The container also creates the `minemydinner_test` database, which tests and checks against the real app use.

### 2. Backend

```powershell
cd backend
& $py -m alembic upgrade head      # only after new migrations
& $py -m uvicorn minemydinner.main:app --host 127.0.0.1 --port 8000
```

Restart the backend after every backend code change.

### 3. Frontend

In a second terminal:

```powershell
cd frontend
npm run dev
```

Open `http://localhost:3000`. The frontend hot-reloads, but new message keys need a hard refresh (Ctrl+F5).

The review queue lives only in browser memory. Save or finish open drafts before reloading the page or restarting the frontend.

## Evaluation data

Each saved review writes one row to `extraction_logs`: the input text, the model output, the user's corrected result, the model name, and the prompt version. Each model call writes one row to `llm_calls` with its task, tokens, cost estimate, latency, and success.

### Query the tables directly

These queries only read data.

List the evaluation samples:

```powershell
docker exec minemydinner-db psql -U postgres -d minemydinner -c "SELECT id, model_name, prompt_version, user_corrected->>'lang' AS lang, user_corrected->>'title' AS title, cardinality(image_files) AS images, created_at FROM extraction_logs ORDER BY id;"
```

Show one sample in full (replace `5` with the id):

```powershell
docker exec minemydinner-db psql -U postgres -d minemydinner -c "SELECT input_text, jsonb_pretty(model_output), jsonb_pretty(user_corrected) FROM extraction_logs WHERE id = 5;"
```

Summarize model calls by task and model:

```powershell
docker exec minemydinner-db psql -U postgres -d minemydinner -c "SELECT task, model, count(*) AS calls, sum(CASE WHEN success THEN 1 ELSE 0 END) AS ok, round(sum(cost_estimate), 4) AS cost_usd FROM llm_calls GROUP BY 1, 2 ORDER BY 1, 2;"
```

### Score the samples

Run from `backend/`, with `$py` and the environment variables set as above:

```powershell
cd backend
& $py -m minemydinner.evaluation.export --out data/eval.jsonl
& $py -m minemydinner.evaluation.run --input data/eval.jsonl
```

`export` writes every log that holds a user correction to a JSONL file. `run` reports ingredient F1, amount accuracy, amount type accuracy, fabrication rate, and valid JSON rate, plus each model's call count, success rate, cost per item, and latency from `llm_calls`.

`data/` is listed in `.gitignore`. Keep exported files there, because evaluation data from real recipes must never enter the repository.

### Planning evaluation

The planning evaluation runs fictional scenarios against the test database with real models, so each run costs money; run it only on purpose. Its scenarios and fictional recipe libraries are hand-written and committed in `backend/eval_data/planning/`:

- `scenarios_baseline.yaml` with `library.yaml`: the fixed baseline set, kept unchanged so results compare across plans.
- `scenarios_plan2a.yaml` with `library_extended.yaml`: scenarios for the planner-efficiency features.
- `scenarios_counts.yaml` with `library_extended.yaml`: meat and vegetable bounds ("至少" / "最多"), the implied side, and the next-action buttons' messages (added in plan 2a Task 11; these scenarios took no part in earlier tuning). A scenario with `follow_up` sends a second user message on the same thread; the second turn is the one scored, and `expect_first_reasons` checks why the first turn stopped.
- `scenarios_diners.yaml` with `library_diners.yaml`: the plan 2b development set (several diners, saved friends, guests, nicknames, ask-first answers, plan again after an allergen confirmation). Claude wrote it; during plan 2b it was used while building, and tuning against it happened only in plan 2b Task 12 with the user's approval of each round. Since plan 3b-1 it is a regression set (`role: regression`).
- `scenarios_heldout.yaml` and `scenarios_heldout_core.yaml` with `library_diners.yaml`: the plan 2b held-out sets, whose requests the user wrote; regression sets since plan 3b-1.
- `scenarios_revise.yaml` with `library_revise.yaml`: the plan 3b-2 development set (follow-ups on a plan: drops, replaces and adds, ambiguous messages, one-meal avoidances and taking one back, a condition and a count in one message, changes that can be done only in part, a must-use ingredient, a reference that matches no dish, a message after a button, buttons after a failed change, messages that are not about the plan, people). Plan 3b-2 Task 10 rewrote it for the two layers below, with new follow-up sentences. Its header defines every follow-up key; every key is also in `Scenario` (`evaluation/planning.py`).
- `scenarios_revise_heldout.yaml` with `library_revise.yaml`: the plan 3b-1 held-out set, a regression set since plan 3b-1b. `scenarios_revise_heldout2.yaml` with `library_revise2.yaml`: the plan 3b-1b held-out set.

Each file states its role in a top-level `role:` key (`development`, `regression`, or `held-out`); the table of all sets, their roles, and when a role changed is at the top of `docs/planning-evaluation-history.md`. A held-out set runs only once its plan's code is final. What it shows is recorded in the history and the carryover; nothing (code, prompts, tests, or the development set) is tuned against it, and no request from it may be copied into prompts, tests, or the development set.

Scenario keys for diners (plan 2b):

- `friends`: saved friends, `[{name, rules: [{type, allergen?, ingredient?, text?}]}]`. Before each scenario the runner deletes every diner except the user (with their rules) and creates these; the user's own rules come from `rules`, as before.
- `request_style`: `ask_first` or `plan_then_adjust` (the default).
- `answers`: replies to the questions the assistant asks (the ask-first question, or a contradiction question), in order. A question with no answer left stops the scenario (`note: stopped at a question: question`).
- `diners_answer`: how the runner answers the diner card. Absent or `accept`: the card's preselection, every proposed rule ticked for this meal only (on the selected rows and the guests), and "none" (no dietary limits) for the unmatched people. A mapping replaces single parts of that: `selected` (names and `me`; the proposed rules of each selected row are ticked), `guests` (`[{name, rules, save}]`, rules in the `friends` format), `others` (an integer), `others_answer` (`none` or `unsure`).
- `expect_card`: absent, the card is not checked, but a card that appears is still answered (by `diners_answer`, default accept) and the scenario goes on. `false`: a card appearing in any turn is a mismatch. A mapping: a card must appear, and the first card is checked on the parts given: `preselected` (names and `me`; exactly these rows selected), `others`, `nudge`, `diet_words` (some row or guest shows the diet-word hint), `guests` (`[{name?, allergens?, avoid?: [{name, kind?}], diet_words?}]`; each listed guest must be a different guest on the card with at least these values, and a left-out `kind` accepts either), and `self_proposed` (`[{allergen? | ingredient?}]`, proposed rules on the user's row).
- `expect_rules_applied` / `expect_rules_not_applied`: `[{who: name | me | guest, allergen? | ingredient?}]`, checked against the rules of the planning context of the judged (last) turn. An ingredient matches a dictionary entry by any of its names or aliases, or a text rule by its wording.
- `between_turns`: `[{confirm_allergens: {name, allergens: []}}]`, database actions done after the first turn and before `follow_up` (so it needs `follow_up`). It confirms the allergens of an unmatched recipe line's name the way the dictionary page does (`resolve_allergens`): a new user entry with those allergens, and matching unlinked lines linked to it. A name some entry already owns is refused. The next scenario's reset undoes it (the user entries are removed and their lines unlinked again), so scenarios do not depend on their order.

Follow-ups on a plan are judged in two layers (plan 3b-2 design, section 6):

- What must never be wrong is checked in code on every scenario of every set (`must_failures` in `evaluation/revise_eval.py`; each failure starts with `must:`, is listed in the scenario's `must_failures`, and also makes the scenario not as expected): no locked dish removed without its quote (from the turn's own message, and acted on by the frame), a diner rule or this meal's avoidance (stock, cookware and the time limit never move a locked dish); no dish removed or added that the scenario does not allow; the counts the message stated are met (unless the reply says a part bearing on a stated count could not be done); no dish that breaks a diner rule or this meal's avoidance stays; no "not in the library" for a library dish (its title, or a name inside exactly one title, such as 小白菜 for 清炒小白菜); no "0 道菜" / "0 dishes"; an unmet wish the change path recorded is said; no internal words shown (the plan-line marks such as `LOCKED`, dish ids, "Diner N"). A check that needs what only the change path records (the frame, the reading, the outcome) applies only where it was recorded, so the older new-plan sets get the checks that apply to them.
- What is acceptable is stated by the scenario's keys, with `expect_any_of` where a sentence has more than one reasonable reading. Which dishes may change comes from `expect_removed`, `expect_removed_any`, `expect_max_changed`, `expect_unchanged` and `expect_titles_include`, with the frame the run recorded: named removals (and dishes the frame found breaking the new request) may go; with no named removal, the dishes the frame let go (drops, replaced dishes, choice candidates); titles in `expect_titles_include` may not go; `expect_unchanged: true` lets nothing go or come; no more new dishes come in than the frame made room for. A scenario with none of these keys is checked only by the other must checks. New plans are not judged on which dishes changed.
- A follow-up that ends with a question other than the empty-plan question and the which-dish question, while the scenario's keys accept a change (`change_allowed`; not when they expect a question, `expect_route: other`, or the plan kept with no new version), is an unnecessary question: counted apart (`unnecessary_question`), never a failure by itself.
- `expect_route` is what the turn was taken as: a follow-up on a plan is read, and the reading's kind stands for the route (`change` and `people`: `revise`, `new_meal`: `plan`, `other`: `other`); a first message keeps the route model's answer. `unsure` (the removed choice card's route) still loads for the older sets and is never met; `expect_choice_card` and `choice_answer` also still load (true cannot be met; the answer is ignored).
- `expect_some_next_action: bool`: true, the judged turn's shown card offers at least one next action (any kind); false, it offers none. (`expect_next_actions` names actions that must all be offered.)
- `button_before: <action>` presses that next-action button of the newest card after the first message, before `first_plan` and the follow-up, so the follow-up is a message after a button (it needs `follow_up`; a card that does not offer it stops the scenario, not as expected).

Two automatic checks run on every scenario and count as mismatches:

- `nickname sent`: a saved friend's nickname (from `friends`) in any model-bound message, that is the content and tool-call arguments of the non-system `messages` of every `model_call` event (route, parse, and planner) of every planning run of the thread. System prompts are skipped: they are fixed text the nickname replacement leaves as written, and their examples hold names. An occurrence inside a longer food word ("鱼" in "鲈鱼") does not count, the same rule the nickname replacement follows. A guest saved as a friend on the card is not checked: the parse saw their name as a guest's before the card, which the design accepts.
- `label shown`: a "Diner N" label (`diner` and a number, with or without a space, any case) in what the user sees: every assistant message of the thread, every diner card shown, and the final plan.

Each scenario records `card_shown`, `cards_shown` (cards over every turn; a "plan again" follow-up should not show a second one, since it reuses the settled diners and asks again only when a friend in them was deleted), and, in `results.jsonl` only, the first card's value (`card`; it holds names).

Friends' names in the exports are an accepted exception: the evaluation friends are fictional scenario data, `results.jsonl` (git-ignored) holds the first card with their display names, and a failure string in `summary.json` may name one (for example "rule not applied: Alex"). The rule that no saved nickname appears in any evaluation export is about model-bound text and the planning records of real users; the model-bound messages in an export are still checked by `nickname sent`.

Run from `backend/`, with `DATABASE_URL` pointing at the test database (the script refuses the main database and a non-empty test database):

```powershell
cd backend
$env:DATABASE_URL = (& $py -c "from minemydinner.settings import Settings; print(Settings().test_database_url)")
& $py -m minemydinner.evaluation.planning --scenarios eval_data/planning/scenarios_baseline.yaml --library eval_data/planning/library.yaml
Remove-Item Env:DATABASE_URL
```

Options:

- `--no-shortlist` runs the planner without the code-built shortlist, for comparison.
- `--with-embeddings` computes embeddings for the fictional recipes after loading them, so meaning-based search is tested too (a few cheap embedding calls, counted separately).
- `--planning-model <catalog name>` runs the whole `planning_eval` task (route, parse, and planner) on that catalog model, with no fallback, so models can be compared. Only tool-calling catalog models in `backend/config/models.yaml` are valid; an unknown name exits with an error that lists them. Production defaults are not changed. The run id then ends with the model name, and `summary.json` records it in `conditions.planning_model` (the configured default when the flag is absent).

The script refuses to start when the working tree has uncommitted changes (run folders excepted), so every run belongs to one commit.

Each run writes a folder `backend/eval_data/planning/runs/<run id>/`, where the run id is `<UTC time>-<scenario set>-<shortlist|no-shortlist>-<vectors|no-vectors>` (with `-<model>` added when `--planning-model` is given):

- `summary.json` (committed): conditions (commit, data file hashes, the prompt version label, the version of each prompt and the SHA-256 of each prompt file in use, each prompt's length in characters as `load_prompt` reads it (`prompt_lengths`), the tool version, models and prices, settings; see `docs/prompt-versions.md`), metrics by area, and each scenario's metrics. It holds no model text.
- `prompts/` and `tools.json` (committed): only the prompt files in use, under their versioned names, and the model-facing tool definitions the run used.
- `results.jsonl` (git-ignored): each scenario's full planning record and model calls, including model output. The record's events include the route and parse model calls (`step` is `route`, `parse` or `planner`) as well as the planner's; a "plan again" turn reuses the previous parsed request and has a `parse_reused` event in their place; the metrics in `summary.json` count `llm_calls` rows and are not affected by these events. `planning_runs` lists every planning run on the scenario's thread in order (two for a `follow_up` scenario); `planning_run` is the last one. Keep a private copy outside Git if the run matters.

How to read the metrics in `summary.json`:

- `model_time_ms` is the sum of one scenario's model-call latencies. `wall_ms` is the scenario from start to finish, with database and tool time included. `per_call_ms` covers the individual model calls (`planning_eval` task), not scenarios.
- Latency is reported as avg, p50, and p95 (nearest rank); wall time also has the max. Cost and tokens have totals and per-scenario averages.
- Metrics come in three groups: `all`, `expect_plan` (scenarios that should end with a plan), and `expect_stop` (scenarios that should stop early with a reason). Check-only scenarios are in no group; their pass count is `metrics.checks`.
- Compare aggregates only between runs of the same scenario set. When the sets differ, compare same-named scenarios.
- `metrics.roles` lists the role of the scenario file (its top-level `role:` key: `development`, `regression` or `held-out`; `unset` when the file states none). `outcomes.as_expected_by_role` gives `as_expected` per role, so a regression set never counts toward the held-out rate; scenarios with `deferred_to` are counted apart in `outcomes.deferred`.
- `revisions` covers the change turns (since plan 3b-2 the reader, frame and picker path; a turn whose run records `revision`; before it, the revise step): `pass_rate` (the share that made a new version), `locked_removed` (locked dishes removed without their quote, rule or avoidance; target 0; also a must check), `avg_changed`, and `avg_turn_model_calls` / `max_turn_model_calls` (the model calls of each change turn; target at most 2.5 on average and 7 at most; when a next-action button follows a follow-up change, that change counts as a change turn too, and the scenario lists its calls in `earlier_revise_calls`).
- `two_layers` (plan 3b-2): `must_violations` (must lines over every scenario, deferred ones included; one fault gives one line where cheap; target 0) and `must_violation_scenarios` (scenarios with at least one); only the judged turn is must-checked, so with `button_after` the follow-up before the button is not (the thread's shown texts are); `unnecessary_questions` over `change_allowed_follow_ups` (the rated follow-up scenarios whose keys accept a change), with `unnecessary_question_rate` (target at most 0.2).
- `change_path` (plan 3b-2): `reader_calls`, `picker_calls` (each model call of the picker), `picker_attempts` and `frame_ignored` (parts of a reading the frame builder set aside, such as a dish id not in the plan), over every run of every scenario. An "other" reply makes no planning run, so its reader call is counted from the turn. Each scenario lists the same counts, its `must_failures`, `change_allowed` and `unnecessary_question`.
- `replies` counts, over every scenario: `zero_dishes_shown` (a reply or unmet text says the plan has "0 道菜" / "0 dishes"; target 0), `wish_unmet_not_said` (a scenario with `expect_wish_unmet: true` whose reply gave no code-written sign that the wish was not met; target 0), `buttons` (as expected / total over scenarios that press a next-action button with `button_after`), `fallbacks` (attempts whose dishes code picked because the planner gave no answer) and `fallback_scenarios`. Every follow-up key (`button_after`, `expect_wish_unmet`, and the others) is described in the header of `scenarios_revise.yaml` and in `Scenario` (`evaluation/planning.py`).
- `notes` (in every group) checks the model's own note of each shown plan (design 5.5 of the chat page redesign; the fixed closest-attempt text is left out): `checked` is the number of notes, `flags` counts each flag, and `flagged_rate` is the share of notes with at least one of `outside_dish`, `counts_dishes`, or `wrong_locale`. `multi_sentence` is counted in `flags` but left out of `flagged_rate`: it measures only the one-sentence rule and is reported separately. The flags are `outside_dish` (a library dish that is not in the plan is named), `counts_dishes` (a number next to 道, 个菜, 荤, 素, dish, meat, or vegetable), `multi_sentence` (more than one sentence), and `wrong_locale` (the same rule as `expect_locale_reply`). The checks are heuristics: read the flagged notes before trusting a rate. `counts_dishes` in particular matches number and dish-word patterns, so it is a hint, not a ranking metric. In round 7 the note checks disagreed widely with the user's ratings (see "Model comparison" in `docs/planning-evaluation-history.md`); the user's ratings decide note quality. Each scenario in `summary.json` lists its flags (`note_flags`); the note text is only in `results.jsonl` (`reply_note`).

To rate the notes by hand, export them from one or more run folders (the CSV holds model text, so it is git-ignored under the runs folder, which is also the default place):

```powershell
cd backend
& $py -m minemydinner.evaluation.notes_review --runs eval_data/planning/runs/<run id> eval_data/planning/runs/<other run id> --out eval_data/planning/runs/notes_review.csv
```

It writes one row per scenario with a checked note, per run: `model, run_id, scenario, plan_titles, note, flags, quality, should_not_appear, comment`. For the model comparison, export one run per model (pass one run folder per model to `--runs`). Fill in the last three columns; they are left empty for you:

- `quality`: 3 = a useful, correct practical tip that reads naturally; 2 = harmless but vague or wordy; 1 = useless, wrong, or awkward.
- `should_not_appear`: `y` or `n`. `y` when the note counts dishes, lists the plan's dishes, names a dish outside the plan or suggests a swap, is in the wrong language, or makes an allergy safety claim.
- `comment`: optional; when `should_not_appear` is `y`, name which kind.

After every evaluation, add a round to `docs/planning-evaluation-history.md`: what changed since the previous round, the runs made, and a row per run in its comparison tables.

### Suggestion check

The suggestion check calls the configured `allergen_suggestions` model once for each name in `backend/eval_data/allergen_suggest/names.yaml` and compares the suggested allergens (as a set), category, and stock mode with the expected values. It reports how many match per field, lists the mismatches, and prints the cost from the `llm_calls` rows it wrote. It uses the real model, so each run costs money (about one cent for 18 names); run it only on purpose. The expected values are a household judgement; the user reviewed and approved this set on 2026-11-17. Keep the set unchanged so results of different prompt versions compare like for like; a changed set goes into a new file, and each recorded result names the set it used.

Run from `backend/`, with `DATABASE_URL` pointing at the test database (the script refuses the main database):

```powershell
cd backend
$env:DATABASE_URL = (& $py -c "from minemydinner.settings import Settings; print(Settings().test_database_url)")
& $py -m minemydinner.evaluation.allergen_suggest --names eval_data/allergen_suggest/names.yaml
Remove-Item Env:DATABASE_URL
```

Record the outcome next to `allergen_suggest_v2` in `docs/prompt-versions.md`.
