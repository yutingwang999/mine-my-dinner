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

Run from `backend/`, with `DATABASE_URL` pointing at the test database (the script refuses the main database and a non-empty test database):

```powershell
cd backend
$env:DATABASE_URL = (& $py -c "from minemydinner.settings import Settings; print(Settings().test_database_url)")
& $py -m minemydinner.evaluation.planning --scenarios eval_data/planning/scenarios_baseline.yaml --library eval_data/planning/library.yaml
Remove-Item Env:DATABASE_URL
```

Add `--no-shortlist` to run the planner without the code-built shortlist, for comparison.
