# Implementation Plan: Reddit Audience Intelligence

**Branch**: `001-reddit-audience-intelligence` | **Date**: 2026-08-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-reddit-audience-intelligence/spec.md`

## Summary

Build JammySearch's core product: a researcher groups Reddit communities into named audiences, reads
and searches their combined material, has it interpreted into topics and themes with trends, asks
plain-language questions answered with citations, and is alerted when new material matches a standing
rule.

The technical approach is a **modular monolith** in Python — FastAPI for the API surface, PRAW behind
a single isolating data layer, Celery for all collection and analysis, PostgreSQL as system of record
with `pgvector` for retrieval, and a Next.js frontend. All model access goes through one internal
adapter with a pinned model identifier, versioned prompt files, and schema-validated output, and
every model call and Reddit call emits a cost and latency record.

Delivery is sequenced by the specification's eight prioritized user stories. P1 (audience plus feed)
is independently shippable and is the foundation everything else consumes.

## Technical Context

**Language/Version**: Python 3.11 (backend), TypeScript (frontend)

**Primary Dependencies**: FastAPI, PRAW, Celery, SQLAlchemy, Alembic, Pydantic, pgvector, an LLM
provider SDK behind an internal adapter, Next.js

**Storage**: PostgreSQL as system of record, including embeddings via the `pgvector` extension. Redis
as cache and Celery broker, treated as fully reconstructible from PostgreSQL.

**Testing**: pytest with pytest-asyncio; ruff for lint and format. All external services mocked —
no test may contact Reddit or a model provider (Constitution IV).

**Target Platform**: Linux server, containerized. Single deployment.

**Project Type**: Web application — backend plus frontend.

**Performance Goals**: See the Latency Budget Reconciliation entry in Complexity Tracking. Server-side
p95 budgets are the constitution's; the specification's success criteria are end-to-end and
user-perceived.

**Constraints**: Reddit API quota is the binding external limit on collection breadth and cadence.
Model spend is capped per request and per day (FR-046). Single user — no tenancy scoping anywhere.

**Scale/Scope**: One user. Up to 50 communities per audience (FR-005). 8 user stories, 68 functional
requirements, 16 success criteria.

All Phase 0 unknowns are resolved in [research.md](./research.md); no `NEEDS CLARIFICATION` items
remain.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| # | Principle | Gate | Status |
|---|-----------|------|--------|
| I | Reddit Data Layer Isolation | PRAW imported only in `backend/app/reddit/`; no raw PRAW object crosses a module boundary; the widened search path (R9) also lives here | **PASS** |
| II | Modular Boundaries and Code Quality | Modules own their own routers, services, models, schemas; no cross-module table access; typed signatures; ruff clean; no business logic in routers | **PASS** |
| III | Contract-First Interfaces | Every endpoint has Pydantic request and response models; `contracts/rest-api.md` is the source that `README.md`'s API table must match | **PASS** — README API table needs the alerts, bookmark-status, and `/ops` additions applied in the same change set |
| IV | Testing Standards | Test-first for all service logic; unit and integration split; deterministic; 80% service-layer coverage floor; externals mocked | **PASS** |
| V | UX Consistency | Four view states everywhere; shared components and tokens established before feature screens (R10); UTC ISO 8601; shared pagination and error envelopes | **CONDITIONAL** — see Vocabulary Extension in Complexity Tracking |
| VI | Performance and Efficiency | Caching with defined TTLs on all paid or rate-limited calls; inference persisted; bulk work in Celery; mandatory pagination; no N+1; no blocking I/O in request paths | **CONDITIONAL** — see Latency Budget Reconciliation in Complexity Tracking |
| VII | Deterministic and Bounded AI | Temperature 0; pinned model identifier; prompts in versioned files; Pydantic-validated structured output; cache keyed on (content hash, prompt version, model id); Ask conversation state persisted in PostgreSQL | **PASS** |
| VIII | AI Guardrails and Safety | Retrieved Reddit text delimited and labeled untrusted (FR-033); model output never constructs an operation; output escaped before display; per-request and per-day spend ceilings (FR-046); explicit refusal below the relevance threshold (FR-030) | **PASS** |
| IX | AI Evaluation and Observability | Labeled sets committed before AI features ship (R12); regression gate on any prompt, model, chunking, or retrieval change; non-LLM baseline (R7); telemetry on every call; aggregates surfaced via `/ops` | **PASS** |

**Post-design re-check**: re-evaluated after Phase 1. No new violations introduced. The two
conditional items are unchanged and both require a decision from the project owner — they are
documented rather than silently resolved.

## Project Structure

### Documentation (this feature)

```text
specs/001-reddit-audience-intelligence/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── rest-api.md
│   └── module-interfaces.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
backend/
├── alembic/versions/               # All schema changes ship as migrations
├── app/
│   ├── main.py                     # App init, router registration
│   ├── config.py                   # Settings from .env; the only place secrets are read
│   ├── dependencies.py             # Shared FastAPI dependencies
│   │
│   ├── reddit/                     # Module 1 — the ONLY module importing PRAW
│   │   ├── client.py               # Rate-limited, quota-accounted client
│   │   ├── search.py               # Widened "all of Reddit" scope (R9)
│   │   ├── cache.py                # Redis caching with defined TTLs
│   │   ├── schemas.py              # Normalized types; PRAW objects stop here
│   │   └── quota.py                # Quota budgeting and telemetry
│   │
│   ├── audiences/                  # Module 2 — audiences, shipped starter set, suggestions
│   ├── feed/                       # Module 3 — aggregation, dedup, search over saved material
│   ├── ai/                         # Module 4 — topics, themes, patterns, sentiment, Ask
│   │   ├── adapter.py              # Single LLM access point; pinned model id
│   │   ├── baseline.py             # Non-LLM topic extraction (Constitution IX)
│   │   ├── telemetry.py            # Per-call cost / latency / cache-hit records
│   │   ├── trends.py               # Share-of-discussion aggregates (R6)
│   │   └── prompts/                # Versioned prompt files, never inline
│   │
│   ├── alerts/                     # Module 5 — rules, matches (in-app only)
│   ├── users/                      # Module 6 — auth, bookmarks, notes, lead status
│   ├── ops/                        # Usage, cost, quota, freshness surfaces
│   └── common/                     # database, redis, exceptions, middleware, pagination
│
├── workers/
│   ├── celery_app.py
│   ├── tasks/
│   │   ├── ingest.py               # Fetch + dedup + persist
│   │   ├── comments.py             # Threshold-gated comment collection (R5)
│   │   ├── snapshot.py             # Daily community stats — never skipped (R3)
│   │   ├── alerts.py               # Evaluate rules against the new batch (R8)
│   │   ├── embed.py                # Chunk + embed into pgvector (R2)
│   │   └── analyze.py              # Topics, themes, sentiment, trends
│   └── schedules.py
│
└── tests/
    ├── unit/
    └── integration/

evals/                              # Constitution IX
├── datasets/                       # Labeled: themes, sentiment, retrieval (incl. unanswerable)
├── run_eval.py
└── results/

frontend/
├── src/
│   ├── design/                     # Tokens + primitives, established before screens (R10)
│   ├── components/                 # Shared components incl. the four-state data wrapper
│   ├── pages/
│   └── services/                   # Generated/typed API client
└── tests/
```

**Structure Decision**: Web application with `backend/` and `frontend/` at the repository root,
matching the layout already documented in `README.md`. The backend is a modular monolith: module
boundaries are enforced in code per Constitution II so extraction into services remains possible, but
a single deployable is correct at this scale. Two directories depart from the README's tree and are
deliberate: `backend/app/ops/` gives the transparency surfaces (FR-044–FR-046) a home rather than
scattering them, and top-level `evals/` sits outside `backend/` because it is a measurement artifact
of the project, not application code.

## Complexity Tracking

> Filled because the Constitution Check produced two conditional results.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| **Latency Budget Reconciliation** — Constitution VI sets feed reads under 500 ms and search under 1 s; spec SC-003 and SC-004 set 2 s and 3 s | The two documents are measuring different things. The constitution's budgets are server-side p95 for the API response; the specification's success criteria are end-to-end and user-perceived, including network transit and render. Both are legitimate, and neither document currently says which it means. | Silently adopting the looser number would gut Constitution VI's intent; silently adopting the tighter one would set a user-facing promise the frontend cannot keep on a cold render. **Requires a decision**: amend Constitution VI (PATCH) to state its budgets are server-side p95, and annotate the spec's criteria as end-to-end. Until that amendment lands, treat the constitution's numbers as binding on backend work. |
| **Vocabulary Extension** — Constitution V fixes the domain vocabulary as *audience, subreddit, topic, theme, pattern, bookmark*; this feature introduces *alert rule*, *alert match*, and *status* | The alerts capability (User Story 5) and lead tracking (FR-064–FR-066) cannot be described in the existing six terms, and Constitution V forbids inventing synonyms for existing concepts — but these are new concepts, not synonyms. | Reusing an existing term would be exactly the synonym confusion the principle prohibits. **Requires a decision**: amend Constitution V (PATCH) to add the three terms to the fixed vocabulary. `README.md` has already been updated with Keyword Alerts and Saved posts sections, so the vocabulary is documented; the constitution's enumeration is what lags. |

Neither item blocks Phase 1 design or implementation of P1–P4, none of which touch alerts or depend
on the disputed latency numbers. Both should be resolved via `/speckit-constitution` before the
alerts work (P5) and before any performance sign-off.
