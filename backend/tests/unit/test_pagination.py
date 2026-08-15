"""Unit tests for the shared pagination envelope.

Pure computation — nothing here touches a database or the network. The integration test
at the end drives a throwaway FastAPI app to prove the ceiling is enforced by validation
rather than merely declared.
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.common.middleware import install_error_handling
from app.common.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    PageParams,
)


def test_the_first_page_starts_at_offset_zero():
    params = PageParams()

    assert params.page == 1
    assert params.page_size == DEFAULT_PAGE_SIZE
    assert params.offset == 0
    assert params.limit == DEFAULT_PAGE_SIZE


def test_the_offset_follows_the_one_indexed_page():
    # Page 3 at 25 per page skips the first two pages, not the first three.
    assert PageParams(page=3, page_size=25).offset == 50


def test_a_page_size_above_the_ceiling_is_rejected():
    # Constitution VI: unbounded queries are prohibited, so this must fail rather than
    # quietly clamp.
    with pytest.raises(ValidationError):
        PageParams(page_size=MAX_PAGE_SIZE + 1)


def test_the_ceiling_itself_is_allowed():
    assert PageParams(page_size=MAX_PAGE_SIZE).page_size == MAX_PAGE_SIZE


@pytest.mark.parametrize("bad_page", [0, -1])
def test_pages_are_counted_from_one(bad_page):
    # Page 0 would produce a negative offset and silently wrong results.
    with pytest.raises(ValidationError):
        PageParams(page=bad_page)


def test_an_empty_page_size_is_rejected():
    with pytest.raises(ValidationError):
        PageParams(page_size=0)


def test_a_full_first_page_of_more_reports_that_more_remain():
    page = Page.create(items=list(range(25)), total=80, params=PageParams())

    assert page.has_more is True
    assert page.total == 80
    assert page.page_size == 25
    assert len(page.items) == 25


def test_the_last_page_reports_that_nothing_remains():
    # Page 4 of 80 rows at 25 per page holds the final 5.
    page = Page.create(items=list(range(5)), total=80, params=PageParams(page=4))

    assert page.has_more is False


def test_an_exactly_full_final_page_reports_that_nothing_remains():
    # The boundary case: 50 rows at 25 per page ends exactly on page 2, and a naive
    # has_more would claim a non-existent page 3.
    page = Page.create(items=list(range(25)), total=50, params=PageParams(page=2))

    assert page.has_more is False


def test_a_single_short_page_reports_that_nothing_remains():
    page = Page.create(items=[1, 2, 3], total=3, params=PageParams())

    assert page.has_more is False


def test_an_empty_result_is_a_page_not_an_error():
    # The four-state UI needs an empty state to render, not a 404.
    page = Page.empty(PageParams())

    assert page.items == []
    assert page.total == 0
    assert page.has_more is False
    assert page.page == 1


def test_a_page_past_the_end_is_empty_and_reports_nothing_remaining():
    page = Page.create(items=[], total=10, params=PageParams(page=99))

    assert page.items == []
    assert page.has_more is False


def test_the_envelope_has_exactly_the_contracted_keys():
    # contracts/rest-api.md fixes this shape; an extra or missing key breaks every
    # client at once.
    page = Page.create(items=[1], total=1, params=PageParams())

    assert set(page.model_dump()) == {"items", "page", "page_size", "total", "has_more"}


def test_the_item_type_is_carried_into_the_schema():
    # Page[int] must validate its items, or the generated OpenAPI document would
    # describe every collection as a list of anything.
    schema = Page[int].model_json_schema()

    assert schema["properties"]["items"]["items"]["type"] == "integer"


def test_the_ceiling_is_enforced_over_http_with_the_shared_error_envelope():
    app = FastAPI()
    install_error_handling(app)

    # The Annotated form rather than `= Depends()` in the default: identical to FastAPI,
    # and it keeps a mutable call out of an argument default (ruff B008).
    @app.get("/items")
    async def items(params: Annotated[PageParams, Depends()]):
        return Page.create(items=[], total=0, params=params)

    client = TestClient(app)

    ok = client.get("/items", params={"page_size": MAX_PAGE_SIZE})
    assert ok.status_code == 200

    rejected = client.get("/items", params={"page_size": MAX_PAGE_SIZE + 1})
    assert rejected.status_code == 422
    error = rejected.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["details"]["fields"][0]["field"] == "page_size"
