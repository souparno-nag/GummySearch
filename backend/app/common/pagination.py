"""Shared pagination envelope and page-size ceiling.

Every collection response in the API uses the `Page` shape defined here, so a client
handles all of them identically (Constitution V, and `contracts/rest-api.md`):

    {"items": [], "page": 1, "page_size": 25, "total": 0, "has_more": false}

`MAX_PAGE_SIZE` is the ceiling Constitution VI requires. Unbounded queries are
prohibited: without a ceiling, a single request for a million rows exhausts memory and
stalls every other request, and no amount of well-behaved client code prevents it,
because the request is the attacker's to shape.
"""

from collections.abc import Sequence
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

T = TypeVar("T")


class PageParams(BaseModel):
    """Validated `page` and `page_size` query parameters.

    Used as a FastAPI dependency so the ceiling is enforced by validation before a route
    runs, rather than by every service remembering to clamp. A request above the ceiling
    is rejected through the shared error envelope rather than silently truncated —
    quietly returning fewer rows than asked for makes `total` look wrong to the caller
    and hides the limit from whoever needs to know about it.
    """

    page: int = Field(default=1, ge=1, description="1-indexed page number.")
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Rows per page, at most {MAX_PAGE_SIZE}.",
    )

    @property
    def limit(self) -> int:
        """Row count for the SQL LIMIT clause."""
        return self.page_size

    @property
    def offset(self) -> int:
        """Rows to skip for the SQL OFFSET clause, derived from the 1-indexed page."""
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    """One page of results, plus the counts a client needs to navigate.

    Parameterized by the item type — `Page[AudienceRead]` — so each endpoint's response
    schema stays precise and renders correctly in the generated OpenAPI document.
    """

    items: list[T]
    page: int
    page_size: int
    total: int
    has_more: bool

    @classmethod
    def create(cls, items: Sequence[T], total: int, params: PageParams) -> "Page[T]":
        """Build a page from a query's rows and the total count matching that query.

        `has_more` is derived here rather than by each caller, because working it out
        from the offset and the total is exactly the kind of off-by-one every call site
        would get wrong independently.

        `total` is the count of all matching rows, not the length of `items`. Touches no
        external system — the caller has already run the query.
        """
        return cls(
            items=list(items),
            page=params.page,
            page_size=params.page_size,
            total=total,
            has_more=params.offset + len(items) < total,
        )

    @classmethod
    def empty(cls, params: PageParams) -> "Page[T]":
        """Build an empty page, for the four-state UI's empty state."""
        return cls.create(items=[], total=0, params=params)
