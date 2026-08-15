# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

JammySearch (formerly named "GummySearch", after the commercial product of that name, which ceased
operating on 2025-11-30; this project is an independent implementation and not affiliated with it) is
a Reddit intelligence / social-listening tool, currently in early scaffolding. Most of the architecture
described below (and in `README.md`) is the **planned** design, not yet implemented as application
logic. What *is* real: Phase 1 (Setup, tasks T001–T010 of
`specs/001-reddit-audience-intelligence/tasks.md`) is complete — the full directory tree, `ruff` and
`pytest` configured and enforcing an 80% service-layer coverage gate, `docker-compose.yml` (Postgres
with `pgvector`, Redis), an async Alembic environment, `backend/app/config.py` as the sole reader of
`.env` secrets, `.env.example`, a scaffolded SvelteKit frontend, and the `evals/` entry point stub.
Phase 2 (Foundational, T011–T047) — the database session, Redis connection, the FastAPI app itself,
auth, the rest of the Reddit data layer, telemetry, and the base ORM models — has not started yet.
Check what actually exists under `backend/app/` before assuming a module is present; `backend/app/main.py`
is still empty and `backend/app/reddit/` only has `client.py`. Each implemented task has a
beginner-oriented writeup in `docs/tasks/T<id>.md` explaining what it added and why.

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

`requirements.txt` now lists the full Phase 1 stack: `praw`, FastAPI + `uvicorn`, `celery[redis]`,
`sqlalchemy` + `alembic` + `asyncpg` + `pgvector`, `pydantic` + `pydantic-settings`, `langchain` +
`langchain-groq` + `langchain-huggingface` + `sentence-transformers`, `python-dotenv`, `ruff`, and
`pytest` + `pytest-asyncio` + `pytest-cov`. Add new dependencies here in the same change that imports
them (Constitution, Technology and Data Constraints).

All settings — including secrets — are read from `.env` at the repo root, and **only**
`backend/app/config.py` is permitted to read them (other modules import `settings` from there). See
`.env.example` for the full, documented list: Reddit credentials, `DATABASE_URL`, `REDIS_URL`, Groq
chat-completion settings (`GROQ_API_KEY`, `GROQ_MODEL` — pinned explicitly, never a floating alias),
the local embedding model name, and `ALLOW_REMOTE_EXPOSURE` (FR-078's deployment exposure flag,
defaults to `false`).

Build, lint, and test commands, run from `backend/` with `reddit-env` active:

```bash
ruff check . && ruff format --check .          # lint + format, per backend/pyproject.toml
pytest                                          # full suite; --cov=app with an 80% fail-under gate
alembic upgrade head                            # apply migrations (none exist yet)
```

`docker-compose.yml` at the repo root brings up Postgres (with `pgvector`) and Redis, both bound to
`127.0.0.1` only:

```bash
docker compose up -d
```

The frontend (`frontend/`, SvelteKit — see below) has its own toolchain:

```bash
cd frontend
npm run dev      # dev server
npm run build    # production static build (adapter-static)
npm run lint     # eslint + prettier --check
npm run test     # vitest
```

## Architecture

### Planned backend stack

- FastAPI (async web framework)
- PRAW for the Reddit API
- Celery + Redis for the job queue / scheduled ingestion
- SQLAlchemy + Alembic for ORM and migrations
- LangChain as the orchestration layer for the RAG "Ask" pipeline and embeddings, decided over
  CrewAI (wrong shape — built for multi-agent tool delegation, not single-shot structured
  completions). Chat completions go through **Groq** (`langchain-groq`, an open-weight model, pinned
  explicitly — never a floating alias); embeddings run **locally** via a HuggingFace
  `sentence-transformers` model (`langchain-huggingface`), no API key or network call. Both were
  chosen over OpenAI/Anthropic for zero per-token cost at single-user scale — see `research.md` R1.
- PostgreSQL with the `pgvector` extension as the vector store (deliberately *not* a separate managed
  vector database — see the constitution's Technology and Data Constraints)
- Pydantic for validation

### Planned frontend stack

- **SvelteKit**, configured with `adapter-static` and SSR explicitly off (`export const ssr = false`
  in the root layout) — ships as a pure client-side SPA consuming the backend's REST API, with a
  fallback page (`fallback: 'index.html'`) so runtime-dynamic routes like `/audiences/[id]/feed`
  still resolve client-side even though nothing is server-rendered. Chosen over Next.js because none
  of Next's SSR/ISR/edge-middleware capabilities have a consumer in this design — see `research.md`
  R10 for the full comparison against Next.js and bare Vite+React.
- TypeScript, ESLint + Prettier (the frontend's equivalent of `ruff`), Vitest (the frontend's
  equivalent of `pytest`) — all configured by the SvelteKit scaffold in `frontend/`.
- File-based routing under `frontend/src/routes/` (SvelteKit's own convention — no router library).

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
   detection, and the RAG-based "Ask" feature (embed locally via sentence-transformers → pgvector
   similarity search → LLM completion via Groq, through LangChain). All model calls use pinned
   models, temperature 0, versioned prompt files, and schema-validated structured output, and emit
   cost/latency telemetry.
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

The "Ask" feature (`ai/ask_service.py`) follows: embed query locally (sentence-transformers) → pgvector
similarity search → fetch full post context from Postgres → LangChain prompt construction → Groq
completion → structured response with source posts.

See `README.md` for the full planned folder structure, API route table, and architecture diagrams.
