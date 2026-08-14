# Phase 0 Research: Reddit Audience Intelligence

**Feature**: `001-reddit-audience-intelligence`
**Date**: 2026-08-14
**Purpose**: Resolve every open decision in the plan's Technical Context before design begins.

The constitution (`.specify/memory/constitution.md`) already pins most of the stack — Python 3.11,
FastAPI, PRAW, Celery with Redis, SQLAlchemy with Alembic, Pydantic, PostgreSQL with `pgvector`,
pytest, and ruff. Those are not re-litigated here. What follows are the decisions the constitution
leaves open, plus the ones the specification's requirements force.

---

## R1. LLM provider and model pinning

**Decision**: Access the model through a single internal adapter in `app/ai/` that exposes
`complete()` and `embed()` and hides the vendor SDK from every call site. Pin an explicit model
identifier in configuration, never a floating alias. Default to the provider already named in
`README.md`; the adapter makes switching a configuration change rather than a refactor.

**Rationale**: Constitution VII forbids provider aliases that silently re-point, and requires the
model identifier to be recorded with every result. An adapter is what makes that enforceable in one
place, and it is what allows the evaluation harness (R12) to run the same eval set against two
providers to justify a choice with numbers rather than preference.

**Concrete model identifiers and per-token prices are deliberately not recorded here.** They move
faster than this document will be revised, and a stale price in a design artifact is worse than no
price. Verify current identifiers and pricing at the moment of pinning, and record the chosen values
in configuration plus the plan's Complexity Tracking if they constrain anything.

**Alternatives considered**: Calling the vendor SDK directly from each service — rejected, it
scatters the model identifier across the codebase and makes Constitution VII's telemetry
requirement (IX) impossible to satisfy centrally. A general-purpose multi-provider abstraction
library — rejected as premature; a two-method adapter covers current needs without a dependency.

---

## R2. Embedding and chunking strategy

**Decision**: Embed at post granularity for short posts, and split longer posts on paragraph
boundaries with a target chunk size well inside the embedding model's context window and a small
overlap. Store one row per chunk in `pgvector` with a reference back to the owning post. Comments
collected under FR-027 are embedded as their own chunks, tagged with their parent post.

**Rationale**: Reddit posts are mostly short — a title plus a few paragraphs — so per-post embedding
is adequate for the majority and avoids fragmenting an argument that spans a whole post. Paragraph
splitting for the long tail preserves semantic units better than fixed character windows. Keeping
comments as separate chunks with a parent reference is what lets FR-027b report whether a result was
supported by discussion or by post text alone.

**Alternatives considered**: Fixed-size character chunking with overlap everywhere — rejected, it
cuts mid-sentence and produces chunks that retrieve poorly. Embedding whole comment threads as one
unit — rejected, it destroys the attribution needed for citations (FR-029).

---

## R3. Reddit API quota budgeting and collection cadence

**Decision**: Treat quota as a first-class budget owned by `app/reddit/`. Every outbound call passes
through a single rate-limited client that records its cost. Collection runs on a Celery Beat schedule
per audience, with the interval derived from the audience's observed posting rate rather than fixed —
quiet audiences are polled less often. A daily community-statistics snapshot job (FR-038) runs
independently of audience collection and is never skipped, because its value is purely a function of
uninterrupted history.

**Rationale**: Constitution I makes `app/reddit/` the only place quota can be accounted for, and
Constitution VI requires per-call telemetry. Uniform polling wastes quota on dormant communities and
starves active ones. Separating the snapshot job matters because a missed snapshot is permanently
lost — there is no backfill for it — whereas a missed post fetch self-heals on the next cycle.

**Alternatives considered**: Fixed uniform polling for all audiences — rejected as quota-wasteful.
Polling on user request only — rejected, it makes alerts (FR-057) impossible, since alerts depend on
material arriving without anyone asking.

---

## R4. Duplicate and cross-post detection

**Decision**: Two-stage. First, exact identity — Reddit exposes a canonical identifier for a
cross-post's origin, so genuine cross-posts collapse deterministically without any similarity
computation. Second, near-duplicate detection over title plus body using a normalized-text hash for
verbatim reposts. Similarity-based clustering is explicitly not used at this stage.

**Rationale**: FR-011 requires collapsing duplicates and SC-011 sets a measurable ceiling of 2%. The
overwhelming majority of duplication in an aggregated feed is true cross-posting, which is exactly
identifiable and therefore free of false positives. Reaching for embeddings-based similarity first
would risk collapsing two genuinely distinct posts that happen to discuss the same topic, which is a
much worse failure for a research tool than showing an occasional repeat.

**Alternatives considered**: Embedding similarity with a threshold — rejected for this release as
over-engineered and risky; revisit only if SC-011 is measured as failing. Title-only matching —
rejected, too many legitimately distinct posts share a title.

---

## R5. Comment collection engagement threshold

**Decision**: Start conservative — collect top-level comments only for posts in the upper tail of
engagement within their own community over the collection window, rather than against a fixed global
number. Make the threshold configurable per FR-027a and expose its current value. Record, from day
one, how many posts qualify and what that costs, so the threshold can be tuned against evidence.

**Rationale**: A fixed global score is wrong across communities of different sizes — a score of 50 is
exceptional in a 5,000-member community and unremarkable in a 2M-member one. A relative threshold
adapts automatically. The specification's Assumptions already commit to starting conservative because
beginning permissive risks exhausting quota before the value of comments is demonstrated.

**Alternatives considered**: Fixed absolute score threshold — rejected, does not transfer across
community sizes. Collecting comments for every post — rejected as roughly an order of magnitude more
volume, which is why the spec's Q3 decision chose the middle path.

---

## R6. Topic and theme trend computation

**Decision**: Compute a theme's or topic's *share of discussion* per time bucket — its post count in
the bucket divided by the audience's total post count in that bucket — rather than raw counts. Store
per-bucket aggregates rather than recomputing over raw posts on every view. Record collection
coverage per bucket so that a gap in collection is visibly distinguishable from a genuine decline.

**Rationale**: FR-053 asks for change over time and FR-055 forbids showing a direction when the period
is too short. Raw counts conflate "this topic grew" with "we collected more that week", which would
make the trend actively misleading — the exact failure the specification's edge case about
interrupted collection calls out. Share-of-discussion normalizes that away. Distinct-author counts
(FR-054) are stored alongside, because a topic carried by one prolific poster is not a trend.

**Alternatives considered**: Raw post counts per bucket — rejected, indistinguishable from collection
volume changes. On-the-fly computation over raw posts — rejected, it scales badly and violates
Constitution VI's prohibition on unbounded queries in request paths.

---

## R7. Non-LLM baseline

**Decision**: Implement a classical baseline for topic extraction — term-frequency scoring with
stopword and boilerplate removal, clustered into topics — in `app/ai/baseline.py`. Run it against the
same evaluation set as the model path and report both scores together.

**Rationale**: Constitution IX requires a non-LLM baseline for at least one extraction task and
requires the model's measured advantage over it to be reported. Topic extraction is the right task to
baseline because it is the one where a classical method is genuinely competitive — which makes the
comparison informative rather than a formality. If the baseline wins, that is a real finding and a
real cost saving.

**Alternatives considered**: Baselining sentiment instead — rejected; a lexicon-based sentiment
baseline is a weaker comparison and less likely to change any decision. Skipping the baseline —
prohibited by Constitution IX.

---

## R8. Alert evaluation

**Decision**: Evaluate alert rules as a stage in the ingestion chain, immediately after new material
is persisted and before analysis. Match against the newly written batch only, never by re-scanning
history. Persist matches as their own records so they survive rule edits and deletion, per FR-059.

**Rationale**: FR-057 requires evaluation without the user re-running a search, and SC-014 sets a
15-minute surfacing target which is bounded by collection cadence rather than by matching cost.
Matching only the new batch keeps the work proportional to arrival volume. Persisting matches
independently of rules is what makes FR-059 satisfiable — a match is a historical fact, not a view
over a rule.

**Alternatives considered**: A periodic job that re-runs each rule as a search over all history —
rejected, cost grows with corpus size rather than arrival rate, and it produces duplicate matches.
Evaluating at read time when the user opens the alerts view — rejected, it cannot meet SC-014 and
defeats the purpose of a standing rule.

---

## R9. The "all of Reddit" search scope

**Decision**: Implement the widened scope (FR-019) as a distinct path in `app/reddit/` that queries
the source's own search endpoint at request time, clearly separated from the saved-material search in
`app/feed/`. Results are returned without pattern or sentiment analysis, marked as live, and are not
persisted into the audience corpus. Apply a per-request quota charge and a short result cap.

**Rationale**: These are two different capabilities wearing one control, and the specification
already requires the interface to say so. Keeping them as separate code paths prevents the live path
from silently inheriting assumptions — pagination, dedup, analysis — that only hold for collected
material. Not persisting the results keeps the corpus meaning "material we chose to track", which is
what every trend and analysis figure depends on.

**Alternatives considered**: Persisting live results into the corpus — rejected, it would pollute
audience statistics and trends with material the user never chose to track. A single unified search
service handling both — rejected, the two have different latency, cost, and correctness properties.

---

## R10. Frontend

**Decision**: Next.js, as named in `README.md`, consuming the REST contract in `contracts/`. Design
tokens and a shared component library established before feature screens, so Constitution V's
prohibition on one-off colors and spacing is enforceable from the first screen rather than
retrofitted.

**Rationale**: Constitution V requires all four view states (loading, empty, error, populated) on
every data-loading view and requires shared components. Both are dramatically cheaper to establish
first than to retrofit. The four-state requirement in particular is best expressed as a single shared
wrapper component that every data view uses.

**Alternatives considered**: Server-rendered templates from FastAPI — rejected, it contradicts the
README and makes the four-state UX requirement awkward. Deferring the design system until several
screens exist — rejected, that is precisely how one-off styling accumulates.

---

## R11. Authentication

**Decision**: Session-based sign-in for a single user, with credentials in configuration and no
self-service registration. No roles, no tenancy scoping in queries.

**Rationale**: FR-048 requires sign-in and privacy of the user's data; the spec's Assumptions state
sign-in exists to protect one person's data rather than to separate tenants. Building tenancy now
would tax every query for a capability the Future Scope explicitly defers. Registration endpoints for
a single-user system are surface area with no user.

**Alternatives considered**: Full OAuth2 with registration and refresh flows — rejected as
disproportionate and explicitly deferred by the README's Future Scope. No authentication at all —
rejected, FR-048 requires it and the deployment is network-reachable.

---

## R12. Evaluation dataset construction

**Decision**: Build three labeled sets, committed to `evals/datasets/`: theme and topic labels on a
sample of posts, sentiment labels on the same sample, and question-to-relevant-post mappings for
retrieval — the last including a deliberate subset of **unanswerable** questions, because SC-007
requires measuring correct refusal and refusal cannot be measured without questions that should be
refused.

**Rationale**: Constitution IX requires a labeled set covering known-hard cases and requires
before-and-after scores on any prompt, model, chunking, or retrieval change. SC-008 publishes a 75%
agreement floor and FR-047 requires publishing the figure. None of that is possible without the sets
existing first, which makes this a prerequisite of the AI work rather than a follow-up to it.

**Alternatives considered**: Model-generated labels — rejected, scoring a model against its own
output measures nothing. Deferring the eval set until after the AI features work — rejected, it
inverts Constitution IX's regression gate and means the first prompt change ships unmeasured.

---

## Resolved Technical Context

| Item | Resolution |
|------|------------|
| Language/Version | Python 3.11 (constitution) |
| Backend framework | FastAPI (constitution) |
| Reddit access | PRAW, confined to `app/reddit/` (Constitution I) |
| Storage | PostgreSQL as system of record; `pgvector` for embeddings; Redis as cache and broker |
| Background work | Celery with Celery Beat |
| LLM access | Single internal adapter, explicitly pinned model identifier (R1) |
| Frontend | Next.js with a design-token system established first (R10) |
| Testing | pytest, pytest-asyncio, ruff; externals always mocked (Constitution IV) |
| Target platform | Linux server, containerized |
| Project type | Web application — backend plus frontend |
| Auth | Single-user session sign-in (R11) |
| Scale | One user; up to 50 communities per audience; 8 user stories; 68 functional requirements |

No `NEEDS CLARIFICATION` items remain.
