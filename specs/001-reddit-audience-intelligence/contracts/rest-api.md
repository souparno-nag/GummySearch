# REST API Contract: Reddit Audience Intelligence

**Feature**: `001-reddit-audience-intelligence`
**Date**: 2026-08-14

This file is the source of truth for the HTTP surface. Per Constitution III, `README.md`'s API table
MUST be updated in the same change set as any modification here, and every endpoint MUST declare
Pydantic request and response models — returning bare dicts or ORM instances from a router is
prohibited.

## Cross-cutting conventions

**Pagination envelope** — every collection response uses the same shape, so clients handle all
endpoints identically (Constitution V):

```json
{
  "items": [],
  "page": 1,
  "page_size": 25,
  "total": 0,
  "has_more": false
}
```

`page_size` has an enforced maximum; unbounded queries are prohibited (Constitution VI).

**Error envelope** — produced by shared middleware from typed exceptions, never assembled in a
router. Messages state what failed and what the user can do next (FR-052); stack traces and raw
exception text never reach the client (Constitution V).

```json
{
  "error": {
    "code": "audience_limit_reached",
    "message": "This audience already has 50 communities. Remove one before adding another.",
    "details": {}
  }
}
```

**Timestamps** — all UTC ISO 8601, localized only at render time (Constitution V).

**Freshness** — any response derived from collected material carries `last_refreshed_at` (FR-013).

**Auth** — session-based, single user (R11). All endpoints require an authenticated session; there is
no registration endpoint.

---

## Audiences — User Story 1

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `GET` | `/audiences` | List the user's audiences | FR-001 |
| `POST` | `/audiences` | Create an audience | FR-001, FR-004, FR-005 |
| `GET` | `/audiences/{id}` | Audience detail with member communities | FR-001 |
| `PATCH` | `/audiences/{id}` | Rename | FR-002 |
| `DELETE` | `/audiences/{id}` | Delete, with warning that analysis becomes unreachable | FR-003 |
| `POST` | `/audiences/{id}/communities` | Add a community | FR-002, FR-004, FR-005, FR-050 |
| `DELETE` | `/audiences/{id}/communities/{name}` | Remove a community | FR-002 |
| `GET` | `/audiences/starter` | Browse the shipped starter set | FR-006 |
| `POST` | `/audiences/starter/{id}/copy` | Save an editable copy | FR-006 |
| `GET` | `/audiences/{id}/suggestions` | Related communities to add | FR-007 |

`POST /audiences/{id}/communities` returns `409` with a specific `code` distinguishing
`community_not_found`, `community_private`, `community_banned`, and `community_quarantined` — FR-050
requires the user to be told which applies, not given a generic failure.

## Feed — User Story 1

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `GET` | `/audiences/{id}/feed` | Combined paginated timeline | FR-008, FR-011, FR-012 |
| `GET` | `/posts/{id}` | Full post title and body | FR-010 |

Query parameters: `sort` (`new`, `top_today`, `top_week` — FR-009), `page`, `page_size`.

Each item carries its source community (FR-008) and `flagged_adult` (FR-051). Cross-posts and
verbatim reposts are collapsed before the page is assembled (FR-011).

## Search — User Story 2

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `POST` | `/search` | Search with filters | FR-014 – FR-020 |

Request carries: `scope` (`audience`, `all_saved`, `all_reddit` — FR-019, defaulting to `audience`),
`audience_id`, `keywords` each with an `enabled` flag (FR-015), `time_period`, `limit`, `sort`
(FR-016), `include_communities` / `exclude_communities` (FR-017), `include_authors` /
`exclude_authors` (FR-018).

Response carries `scope_used`, and for saved-material scopes the detected `patterns` and `sentiment`
(FR-020). For `all_reddit` the response sets `analysis_available: false` and `live: true`, and the
client must surface that results are live, may be slower, and carry no analysis (FR-019, FR-020).
`all_reddit` is exempt from SC-004's latency target.

## Analysis — User Story 3

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `GET` | `/audiences/{id}/analysis/topics` | Topics across the audience | FR-021 |
| `GET` | `/audiences/{id}/analysis/themes` | Themes, `kind` distinguishing scored from interpreted | FR-021, FR-022 |
| `GET` | `/audiences/{id}/analysis/themes/{theme_id}` | Description, sub-categories, contributing topics and communities, posts | FR-023 |
| `GET` | `/audiences/{id}/analysis/themes/{theme_id}/patterns` | Patterns and sentiment | FR-024 |
| `GET` | `/audiences/{id}/analysis/trends` | Share of discussion over time | FR-053 – FR-055 |

Trend responses carry per-bucket `share`, `distinct_post_count`, `distinct_author_count`, and
`collection_coverage`, plus the `period_covered`. When the collected period is too short, the
response returns `trend_available: false` with the period length rather than a direction (FR-055).

All analysis responses carry `derived_from_comments` (FR-027b) and, when material is insufficient,
`sufficient: false` with what is missing (FR-025).

## Ask — User Story 4

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `POST` | `/audiences/{id}/ask` | Ask a question; streamed response | FR-028 – FR-032 |
| `GET` | `/audiences/{id}/ask/{turn_id}` | Retrieve a past answer with citations | FR-029 |

Streamed per Constitution VI and FR-031. Every non-refused answer carries a non-empty `citations`
array, each referencing a post that can be opened in full (FR-029). When retrieval falls below the
relevance threshold the response sets `refused: true` with a plain explanation and no answer body
(FR-030) — answering from general knowledge is prohibited.

## Alerts — User Story 5

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `GET` | `/alerts/rules` | List rules | FR-056 |
| `POST` | `/alerts/rules` | Create a rule scoped to an audience | FR-056 |
| `PATCH` | `/alerts/rules/{id}` | Edit keywords, pause, or resume | FR-056, FR-059 |
| `DELETE` | `/alerts/rules/{id}` | Delete; recorded matches survive | FR-059 |
| `GET` | `/alerts/matches` | Matches, grouped so one post appears once | FR-058 |
| `POST` | `/alerts/matches/{id}/read` | Mark seen | FR-058 |

Match responses list every rule a post matched and the `matched_terms` that fired (FR-058), and
embed enough post content to be actionable in place (FR-062). A rule matching an unusually high share
of material returns `matching_broadly: true` so the client can offer to narrow it (FR-060). A rule
whose audience contains an unavailable community still evaluates and reports
`degraded_communities` (FR-061).

Delivery is in-app only; there are no outbound-channel endpoints (FR-063).

## Community discovery — User Story 6

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `GET` | `/communities/search` | Search by name, description, topic | FR-034 |
| `GET` | `/communities/ranked` | Rank by size, activity, or growth with filters | FR-035 |
| `GET` | `/communities/{name}` | Detail with size, newness, and activity tags | FR-036 |
| `GET` | `/communities/{name}/history` | Membership and posting volume over time | FR-037, FR-039 |

History returns `history_available: false` with the collected period length when there is too little
to plot (FR-039) rather than a misleading line.

## Saved posts — User Story 7

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `GET` | `/bookmarks` | List, filterable by `status` | FR-041, FR-065 |
| `POST` | `/bookmarks` | Save a post, capturing its content | FR-041, FR-043 |
| `PATCH` | `/bookmarks/{id}` | Update note or status | FR-042, FR-064 |
| `DELETE` | `/bookmarks/{id}` | Remove | FR-041 |

Bookmarks of posts removed at the source return captured content with `source_available: false`
(FR-043). Notes and status are never transmitted to the source (FR-066).

## Operations and transparency — User Story 8

| Method | Path | Purpose | Requirements |
|---|---|---|---|
| `GET` | `/ops/usage` | Cost, tokens, latency, cache hit rate in aggregate | FR-044, FR-045 |
| `GET` | `/ops/usage/{audience_id}` | The same, scoped to one audience | FR-044 |
| `GET` | `/ops/quota` | Reddit calls used versus avoided by caching | FR-045 |
| `GET` | `/ops/limits` | Current spend ceilings and consumption against them | FR-046 |
| `GET` | `/ops/evaluation` | Published accuracy against the labeled reference set | FR-047, SC-008 |

When a spend ceiling is reached, the analysis and Ask endpoints return `429` with code
`spend_limit_reached` and a clear explanation rather than continuing to spend (FR-046).

---

## README synchronization required

`README.md`'s API table predates this contract and must be brought into line in the same change set
(Constitution III). Differences: `/alerts` expands to `/alerts/rules` and `/alerts/matches`;
`/bookmarks` gains status filtering and `PATCH`; `/ops` gains `/limits` and `/evaluation`;
`/audiences/curated` becomes `/audiences/starter` with an explicit copy endpoint; and the deferred
`/users/workspace` and `/users/reports` entries stay commented out.
