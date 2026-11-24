English | [简体中文](README.zh-CN.md)

# MineMyDinner

**Dig tonight's dinner out of your own saved recipes and your fridge.**

## Why

I had saved a lot of recipes — videos, posts, screenshots — and still never remembered to cook any of them. When it was time to make dinner, the saved recipes were out of sight and out of mind.

MineMyDinner turns that collection into something you can ask: "two of us tonight, three dishes, only what's in the fridge". The key choice is that **it never invents dishes**. A plan is built only from recipes you imported and confirmed yourself, so every suggestion is a dish you already wanted to make, with a link back to where you found it. The language model reads your request and picks from your library; code checks the result against your fridge, your cookware, the time you have, and everyone's allergies and dietary rules.

## What it does

- **Import recipes** from pasted text, a web page link, screenshots, or a batch text file. A model extracts the title, ingredients, amounts and cookware; you review and correct the draft before it is saved. Each recipe keeps its original source.
- **Keep track of what you have**: fridge inventory, always-in-stock seasonings, cookware.
- **Plan a meal in chat**: say what you want in your own words. Plan with only what you have, or allow shopping and see what is missing.
- **Cook for several people**: confirm who is eating; their allergies and dietary rules are combined for this meal. Friends' details are saved only if you choose to.
- **Adjust the plan**: remove or add dishes by hand, or say "swap the eggplant", "something lighter", "no pork tonight" — a follow-up changes only what you asked for, and the reply says what changed and anything it could not do.
- **Chinese and English** throughout: UI, recipes, and the ingredient dictionary.

## How it is built

| Part | Choice |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, Alembic |
| Agent | LangGraph; DeepSeek, OpenAI and Gemini through one model layer, chosen per task |
| Database | PostgreSQL + pgvector (business data, vector search, and the conversation checkpoints) |
| Frontend | Next.js, React, next-intl |

A few design ideas the project is built around:

- **Models read, code decides the hard parts.** Locks, dish counts, allergies, one-meal wishes, stock, cookware and time limits are checked in code, not left to a prompt. When a follow-up changes a plan, a model reads what the message wants, code works out which dishes may change, and a model picks dishes only inside that frame.
- **Measured, not guessed.** Recipe extraction and meal planning each have an evaluation suite: development, regression and held-out scenario sets, with every round recorded in [docs/planning-evaluation-history.md](docs/planning-evaluation-history.md). Prompts are versioned and never edited once a run has used them ([docs/prompt-versions.md](docs/prompt-versions.md)).
- **No surprises with people's data.** Saved nicknames never reach a model; diners are sent as anonymous labels.

More detail: [docs/design.md](docs/design.md) (overall design) and [docs/planner-what-each-model-sees.md](docs/planner-what-each-model-sees.md) (exactly what each model receives).

## Status

A personal project in active development. Phase 0 (recipe import, review, library, inventory, people) is done; Phase 1 (the planning agent and chat) is in progress. Planned later: ratings and memory, ingredient substitution, a fine-tuned local extraction model, and video import.

## Run it locally

You need Docker, Python 3.13 and Node.js, and at least one model API key.

```powershell
copy .env.example .env      # then fill in your API keys
docker compose up -d        # Postgres + pgvector on 127.0.0.1:15433

cd backend
pip install -e ".[dev]"
python -m alembic upgrade head
python -m minemydinner.seed   # ingredient and cookware dictionary
python -m uvicorn minemydinner.main:app --host 127.0.0.1 --port 8000

# in a second terminal
cd frontend
npm install
npm run dev                 # http://localhost:3000
```

See [docs/local-development.md](docs/local-development.md) for the full guide, including tests and the evaluation scripts.

## License

This repository is public for viewing only. It has no open-source license: all rights are reserved, and no permission is given to use, copy, modify, or distribute the code or its documents.
