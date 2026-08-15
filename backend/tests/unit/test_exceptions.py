"""Unit tests for the typed application exceptions.

What matters here is the contract the error envelope depends on: every exception
carries a status, a stable code, and a message written for a user.
"""

import pytest

from app.common.exceptions import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DegradedError,
    NotFoundError,
    RateLimitedError,
    SpendCeilingError,
    UpstreamError,
    ValidationError,
)

ALL_ERRORS = [
    AppError,
    ValidationError,
    NotFoundError,
    ConflictError,
    AuthenticationError,
    AuthorizationError,
    RateLimitedError,
    SpendCeilingError,
    UpstreamError,
    DegradedError,
]


@pytest.mark.parametrize("error_class", ALL_ERRORS)
def test_every_error_is_catchable_as_the_base_class(error_class):
    # The handlers register against AppError alone; a subclass that escaped the
    # hierarchy would be reported as an unexpected 500 instead of its own status.
    assert issubclass(error_class, AppError)
    assert isinstance(error_class(), Exception)


@pytest.mark.parametrize("error_class", ALL_ERRORS)
def test_every_error_carries_a_status_a_code_and_a_default_message(error_class):
    error = error_class()
    assert isinstance(error.status_code, int)
    assert error.code and error.code.islower()
    # FR-052: a message must be usable as-is, so no class may rely on the raise site
    # to supply one.
    assert error.message


def test_the_message_is_what_str_returns():
    # Anything that logs or formats the exception gets the user-facing sentence rather
    # than a bare class name.
    assert str(NotFoundError("That audience was deleted.")) == "That audience was deleted."


def test_a_raise_site_can_supply_its_own_code_and_message():
    error = ConflictError(
        "This audience already has 50 communities. Remove one before adding another.",
        code="audience_limit_reached",
    )
    assert error.code == "audience_limit_reached"
    assert error.status_code == 409
    assert "Remove one" in error.message


def test_overriding_the_code_does_not_leak_into_other_instances():
    # `code` is set on the instance; assigning to the class would change the default
    # for every later raise of the same type.
    ConflictError("one", code="audience_limit_reached")
    assert ConflictError().code == "conflict"


def test_details_default_to_an_empty_dict_rather_than_none():
    # The envelope always renders a `details` object, so handlers never guard for None.
    assert AppError().details == {}


def test_details_carry_structured_context():
    error = ValidationError(
        "Choose a page size of 100 or fewer.",
        details={"field": "page_size", "max": 100},
    )
    assert error.details == {"field": "page_size", "max": 100}


def test_statuses_match_their_meaning():
    assert ValidationError().status_code == 422
    assert NotFoundError().status_code == 404
    assert ConflictError().status_code == 409
    assert AuthenticationError().status_code == 401
    assert AuthorizationError().status_code == 403
    assert UpstreamError().status_code == 502
    assert DegradedError().status_code == 503
    assert AppError().status_code == 500


def test_an_exhausted_ceiling_is_distinguishable_from_a_rate_limit():
    # Both answer 429, but waiting fixes only one of them, so the codes must differ.
    assert SpendCeilingError().status_code == RateLimitedError().status_code
    assert SpendCeilingError().code != RateLimitedError().code


def test_an_upstream_outage_is_not_an_application_bug():
    # A provider failing must never be reported as a 500, or the Ask feature's `failed`
    # outcome becomes indistinguishable from a crash (SC-007).
    assert UpstreamError().status_code != AppError().status_code
