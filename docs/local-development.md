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

- `summary.json` (committed): conditions (commit, data file hashes, the prompt version label, the version of each prompt and the SHA-256 of each prompt file in use, the tool version, models and prices, settings; see `docs/prompt-versions.md`), metrics by area, and each scenario's metrics. It holds no model text.
- `prompts/` and `tools.json` (committed): only the prompt files in use, under their versioned names, and the model-facing tool definitions the run used.
- `results.jsonl` (git-ignored): each scenario's full planning record and model calls, including model output. The record's events include the route and parse model calls (`step` is `route`, `parse` or `planner`) as well as the planner's; the metrics in `summary.json` count `llm_calls` rows and are not affected by these events. `planning_runs` lists every planning run on the scenario's thread in order (two for a `follow_up` scenario); `planning_run` is the last one. Keep a private copy outside Git if the run matters.

How to read the metrics in `summary.json`:

- `model_time_ms` is the sum of one scenario's model-call latencies. `wall_ms` is the scenario from start to finish, with database and tool time included. `per_call_ms` covers the individual model calls (`planning_eval` task), not scenarios.
- Latency is reported as avg, p50, and p95 (nearest rank); wall time also has the max. Cost and tokens have totals and per-scenario averages.
- Metrics come in three groups: `all`, `expect_plan` (scenarios that should end with a plan), and `expect_stop` (scenarios that should stop early with a reason). Check-only scenarios are in no group; their pass count is `metrics.checks`.
- Compare aggregates only between runs of the same scenario set. When the sets differ, compare same-named scenarios.
- `notes` (in every group) checks the model's own note of each shown plan (design 5.5 of the chat page redesign; the fixed closest-attempt text is left out): `checked` is the number of notes, `flags` counts each flag, and `flagged_rate` is the share of notes with at least one of `outside_dish`, `counts_dishes`, or `wrong_locale`. `multi_sentence` is counted in `flags` but left out of `flagged_rate`: it measures only the one-sentence rule and is reported separately. The flags are `outside_dish` (a library dish that is not in the plan is named), `counts_dishes` (a number next to 道, 个菜, 荤, 素, dish, meat, or vegetable), `multi_sentence` (more than one sentence), and `wrong_locale` (the same rule as `expect_locale_reply`). The checks are heuristics: read the flagged notes before trusting a rate. `counts_dishes` in particular matches number and dish-word patterns, so it is a hint, not a ranking metric. Each scenario in `summary.json` lists its flags (`note_flags`); the note text is only in `results.jsonl` (`reply_note`).

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

