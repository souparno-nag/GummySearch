# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

JammySearch (formerly named "GummySearch", after the commercial product of that name, which ceased
operating on 2025-11-30; this project is an independent implementation and not affiliated with it) is
a Reddit intelligence / social-listening tool, currently in early scaffolding. Most of the architecture
described below (and in `README.md`) is the **planned** design, not yet implemented — only the Reddit
Data Layer has been started. Check what actually exists under `backend/app/` before assuming a module
is present; `backend/app/main.py` and `backend/alembic/` are currently empty.

The binding engineering standards live in `.specify/memory/constitution.md` and take precedence over
this file and over `README.md` wherever they disagree. Read it before planning work — it governs
module boundaries, testing, UX consistency, performance budgets, and AI determinism, guardrails, and
evaluation.

JammySearch is built first as a portfolio project and as a single-user market-research tool. Billing,
subscription tiers, plan gating, team workspaces, and outbound integrations are explicitly deferred
future scope — do not build them.

## Environment setup

The project uses a Python 3.11 virtualenv named `reddit-env` at the repo root (gitignored).

```bash
source reddit-env/bin/activate
pip install -r requirements.txt
```

`requirements.txt` currently only lists `praw` (Reddit API client) and `python-dotenv`. As new backend
modules from the planned architecture are implemented (FastAPI, SQLAlchemy, Celery, etc. — see below),
add their dependencies here.

Reddit API credentials are read from `.env` at the repo root via `python-dotenv`:

```
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=...
```

There are no build, lint, or test commands configured yet (no `pyproject.toml`, no test framework, no
Dockerfile/docker-compose).

## Architecture

### Planned backend stack

- FastAPI (async web framework)
- PRAW for the Reddit API
- Celery + Redis for the job queue / scheduled ingestion
- SQLAlchemy + Alembic for ORM and migrations
- LangChain/LlamaIndex + OpenAI SDK for the RAG "Ask" pipeline and embeddings
- PostgreSQL with the `pgvector` extension as the vector store (deliberately *not* a separate managed
  vector database — see the constitution's Technology and Data Constraints)
- Pydantic for validation

### Modular design

The backend is organized around 6 modules, each intended to own its own `router.py` / `service.py` /
`models.py` / `schemas.py` under `backend/app/<module>/`:

1. **`reddit/`** — Reddit Data Layer. The *only* module allowed to talk to the Reddit API (via PRAW). All
   other modules must go through this layer rather than calling PRAW directly. Owns caching, rate
   limiting, and scheduled data refreshes. `backend/app/reddit/client.py` currently has
   `setup_reddit_client()` and `obtain_subreddit()`.
2. **`audiences/`** — Audience Management. CRUD for user-defined groups of subreddits ("audiences"),
   curated/team-provided audiences, and related-subreddit suggestions.
3. **`feed/`** — Feed & Search Engine. Aggregates posts across an audience's subreddits, handles advanced
   keyword/boolean search, filtering, dedup, and cross-post detection. Consumes the Reddit Data Layer, not
   Reddit directly.
4. **`ai/`** — AI & Analysis Engine. Theme tagging, topic extraction, sentiment analysis, pattern
   detection, and the RAG-based "Ask" feature (embed → pgvector similarity search → LLM completion via
   OpenAI/LangChain). All model calls use pinned models, temperature 0, versioned prompt files, and
   schema-validated structured output, and emit cost/latency telemetry.
5. **`alerts/`** — Alerts, Notifications & Integrations. Keyword alert rules, email digests, Slack/Discord
   webhooks, brand mention tracking, cron-driven checks.
6. **`users/`** — User & Workspace Management. Auth, subscription/plan gating, team workspaces, bookmarks,
   shareable report generation. Determines what features a user's plan allows.

Cross-cutting rule from the design: **no module besides `reddit/` should call the Reddit API directly** —
everything else consumes cached/normalized data through the Reddit Data Layer.

### Data flow (planned)

Post ingestion runs as a Celery Beat-scheduled task chain:
`ingest.py` (PRAW fetch + dedup + write to Postgres + update Redis cache) → `embed.py` (chunk + embed to
pgvector) → `analyze.py` (theme/sentiment tagging) → `alerts.py` (keyword match).

The "Ask" feature (`ai/ask_service.py`) follows: embed query → pgvector similarity search → fetch full
post context from Postgres → LangChain prompt construction → OpenAI completion → structured response with
source posts.

See `README.md` for the full planned folder structure, API route table, and architecture diagrams.
