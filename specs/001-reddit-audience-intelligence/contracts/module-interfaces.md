# Module Interface Contract: Reddit Audience Intelligence

**Feature**: `001-reddit-audience-intelligence`
**Date**: 2026-08-14

Constitution II requires cross-module access to go through the owning module's service functions — no
module may query another module's tables or import its ORM models except for declared foreign keys.
This file is the enumeration of what each module exposes. Anything not listed here is private to its
module.

These are contracts, not signatures to copy verbatim; argument names and return types are indicative.

---

## `app/reddit/` — Reddit Data Layer

**The only module permitted to import PRAW** (Constitution I). Raw PRAW objects never leave this
boundary; everything below returns project-owned Pydantic types.

| Exposed | Purpose | Consumers |
|---|---|---|
| `fetch_community(name) -> CommunityData` | Metadata and current statistics | `audiences`, `feed`, workers |
| `fetch_posts(community, since, limit) -> list[PostData]` | New material for collection | workers |
| `fetch_top_level_comments(post, limit) -> list[CommentData]` | Threshold-gated comment collection | workers |
| `search_reddit(query, filters) -> LiveSearchResult` | The widened `all_reddit` scope (R9) | `feed` |
| `snapshot_community_stats(name) -> SnapshotData` | Daily statistics capture | workers |
| `quota_status() -> QuotaStatus` | Remaining budget and calls avoided by cache | `ops` |

**Forbidden**: any other module importing `praw`, constructing a Reddit client, or reading
`app/reddit/` tables directly. `search_reddit` results are returned to the caller and are *not*
persisted into the audience corpus.

---

## `app/audiences/` — Audience Management

| Exposed | Purpose | Consumers |
|---|---|---|
| `get_audience(id) -> Audience` | Resolve an audience with its communities | `feed`, `ai`, `alerts`, `ops` |
| `list_audience_communities(id) -> list[Community]` | Membership for collection and querying | `feed`, `ai`, workers |
| `assert_audience_owned_by(id, user_id)` | Ownership check | all routers |
| `suggest_related(community_names) -> list[Community]` | Suggestions on creation (FR-007) | `audiences` router |
| `copy_starter_audience(starter_id, user_id) -> Audience` | Full copy, no live link (FR-006) | `audiences` router |

**Forbidden**: other modules writing to `Audience` or `AudienceCommunity`. The 50-community cap
(FR-005) and the no-duplicates rule (FR-004) are enforced here and nowhere else, so bypassing this
module bypasses both.

---

## `app/feed/` — Feed & Search

| Exposed | Purpose | Consumers |
|---|---|---|
| `get_feed(audience_id, sort, page) -> Page[Post]` | Deduplicated combined timeline | `feed` router |
| `search(scope, filters) -> SearchResult` | Both saved-material and widened scopes | `feed` router, `ai` |
| `get_posts_for_audience(audience_id, window) -> list[Post]` | Material for interpretation | `ai`, `alerts` |
| `get_post(id) -> Post` | Single post with availability state | `users`, `alerts`, `ai` |

**Forbidden**: other modules re-implementing deduplication. FR-011 and SC-011 are satisfied here
only; a second dedup path would produce inconsistent counts and break trend denominators.

---

## `app/ai/` — Analysis & Ask

| Exposed | Purpose | Consumers |
|---|---|---|
| `get_topics(audience_id)` / `get_themes(audience_id)` | Interpretation results | `ai` router |
| `get_theme_detail(theme_id)` | Description, sub-categories, posts (FR-023) | `ai` router |
| `get_trends(audience_id, target)` | Share of discussion over time (FR-053–FR-055) | `ai` router |
| `ask(audience_id, question) -> AsyncIterator[AskChunk]` | Streamed, cited answer | `ai` router |
| `analyze_batch(post_ids)` | Called by workers after ingestion | workers |
| `embed_batch(post_ids)` | Chunk and embed into pgvector | workers |

**Internal, never exposed**: `adapter.py` is the single point of model access. No other module — and
no other file within `app/ai/` outside the adapter — may call the provider SDK directly, because the
pinned model identifier, temperature, prompt version, structured-output validation, cache key, and
telemetry record are all applied there (Constitution VII, IX).

**Forbidden**: any caller passing retrieved Reddit text into the instruction portion of a prompt.
Untrusted content is delimited and labeled by the adapter (Constitution VIII, FR-033).

---

## `app/alerts/` — Alert Rules & Matches

| Exposed | Purpose | Consumers |
|---|---|---|
| `list_rules(user_id)` / `create_rule` / `update_rule` / `delete_rule` | Rule lifecycle (FR-056) | `alerts` router |
| `evaluate_new_material(post_ids) -> list[AlertMatch]` | Called by the ingestion chain (R8) | workers |
| `list_matches(user_id, filters) -> Page[GroupedMatch]` | Grouped so one post appears once | `alerts` router |

**Forbidden**: evaluating rules by re-scanning history. Evaluation is against the newly persisted
batch only; anything else grows with corpus size and duplicates matches.

---

## `app/users/` — Auth, Bookmarks, Notes

| Exposed | Purpose | Consumers |
|---|---|---|
| `current_user()` | FastAPI dependency | all routers |
| `create_bookmark(user_id, post_id)` | Captures content at save time (FR-043) | `users` router, `alerts` |
| `update_bookmark(id, note, status)` | Note and lead status (FR-042, FR-064) | `users` router, `alerts` |
| `list_bookmarks(user_id, status)` | Filterable by status (FR-065) | `users` router |

**Forbidden**: storing a bookmark as a reference alone. FR-043 requires captured text so the bookmark
survives deletion at the source.

---

## `app/ops/` — Usage & Transparency

| Exposed | Purpose | Consumers |
|---|---|---|
| `record_usage(...)` | Write one `UsageRecord` per external call | `reddit`, `ai` |
| `check_spend_allowed(feature, estimated_cost)` | Called **before** spending (FR-046) | `ai` |
| `usage_summary(audience_id=None)` | Aggregates for the `/ops` surfaces | `ops` router |
| `evaluation_results()` | Published accuracy figures (FR-047) | `ops` router |

**Forbidden**: any external call path skipping `record_usage`, and any spending path skipping
`check_spend_allowed`. Constitution VI and IX both depend on these being unconditional.

---

## Dependency direction

```text
users ─┐
       ├─> audiences ─> reddit
feed ──┤        │
       │        v
ai ────┼──> feed ──> reddit
       │
alerts ┴──> feed
ops <── (written to by reddit and ai; reads nothing from them directly)
```

No cycles. `reddit` depends on nothing internal. `ops` is written to but never read by the modules
that write it, which keeps telemetry from becoming a hidden coupling.
