# Specification Quality Checklist: Reddit Audience Intelligence

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-14
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`

### Validation iteration 1 — 2026-08-14

**Passing**: 15 of 16 items.

**Failing**: "No [NEEDS CLARIFICATION] markers remain" — three markers are open, all
deliberately retained because no reasonable default exists and each materially changes
scope:

1. **FR-006** — source of ready-made ("curated") audiences. The README describes them as
   authored by "other users / the web app team", but the product is single-user with no
   team, so the described mechanism cannot exist as written.
2. **FR-019** — meaning of the "Anyone" search scope. Searching all of Reddit is a
   materially different capability from searching an audience's collected material, with
   very different cost and feasibility.
3. **FR-027** — whether comments are collected and analysed in addition to posts. The
   README's feature list describes posts only, but its module description mentions
   "comment-level analysis". This changes collected volume and analysis cost by roughly an
   order of magnitude.

**Deliberate omissions** (informed defaults taken rather than asking, per spec guidance):
data retention, maximum audience size, authentication method, error-handling style,
paging behaviour. All are recorded in the spec's Assumptions section.

**Action**: presented to the user as Q1–Q3. Re-validate once answered.

### Validation iteration 2 — 2026-08-14

**Passing**: 16 of 16 items. No open markers remain.

All three clarifications were answered and written into the spec:

1. **FR-006** — ready-made audiences are a fixed set authored and shipped with the product.
   Saving a copy does not alter the original, and later edits to a shipped audience do not
   propagate into copies a user has already saved.
2. **FR-019 / FR-020** — two selectable search scopes. Saved material is the default on every
   new search; an all-of-Reddit scope reaches the source live. The interface must state which
   scope is active and must warn that live results are slower, subject to the source's limits,
   and carry no pattern or sentiment analysis. SC-004's latency target applies only to the
   saved-material scope.
3. **FR-027 / FR-027a / FR-027b** — posts plus the top-level comments of posts meeting a
   configurable engagement threshold. Nested replies are out of scope for this release and
   recorded under Out of Scope as a deferred decision. Analysis must indicate whether comment
   material contributed to a result.

**Downstream propagation**: User Story 2 narrative and a new acceptance scenario for scope
switching; User Story 3 acceptance scenario for discussion-informed analysis; a Comment key
entity; three new edge cases (live search refused, no post meets the threshold, thread with
very many comments); three new assumptions (editorial upkeep of shipped audiences, conservative
starting threshold, widened search as a deliberate act).

**Status**: ready for `/speckit-plan`.

### Validation iteration 3 — 2026-08-14

**Trigger**: a use-case review against the four outcomes the product is meant to serve —
ideating startups, validating demand, inspiring content, and finding sales leads. Two were
well covered, one partially, one barely, and one README/spec inconsistency was found.

**Gaps closed**:

1. **Alerts were absent entirely.** `README.md` states in-app alerts are in scope while the
   spec contained no alert requirements at all — a direct contradiction between two documents
   written in the same session. Added User Story 5 (P5) and FR-056–FR-063. Later stories
   renumbered to 6, 7, 8 with matching priorities.
2. **Demand validation had no time dimension.** Community growth was covered (FR-037,
   FR-038) but nothing showed whether a *topic or theme* was being discussed more or less
   over time, and nothing counted how many distinct people were behind it. Added FR-053–FR-055
   and User Story 3 scenarios 8 and 9. The distinct-author count is the substantive addition:
   it separates a widely held concern from one person posting repeatedly.
3. **Lead-finding had no workflow state.** Bookmarks and notes existed but nothing recorded
   whether a lead had been acted on. Added FR-064–FR-066 and User Story 7 scenarios 4 and 5,
   deliberately scoped as personal state rather than as customer relationship management.

**Also added**: Alert Rule and Alert Match key entities; Bookmark extended with status; three
edge cases (one post matching several rules, a rule that never matches, a collection gap
distorting a trend); SC-014 through SC-016; three assumptions covering alert evaluation
cadence, the boundary around lead status, and the trend cold start.

**README updated** in the same change to add Keyword Alerts and Saved posts sections, so the
feature list and the spec no longer disagree.

**Numbering note**: requirement identifiers are stable and unique but no longer contiguous in
document order. This is stated at the top of the Functional Requirements section.

**Status**: 16 of 16 passing. Ready for `/speckit-plan`.
