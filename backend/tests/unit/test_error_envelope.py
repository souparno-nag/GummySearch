"""Unit tests for the shared error envelope.

Every test drives a throwaway FastAPI app rather than the real one, so these stay
independent of which routers happen to be registered. Nothing here touches the network:
the TestClient calls the ASGI app in-process.
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.common.exceptions import ConflictError, NotFoundError, UpstreamError
from app.common.middleware import GENERIC_ERROR_MESSAGE, install_error_handling


class Payload(BaseModel):
    page_size: int


@pytest.fixture
def client():
    app = FastAPI()
    install_error_handling(app)

    @app.get("/missing")
    async def missing():
        raise NotFoundError("That audience no longer exists.")

    @app.get("/at-limit")
    async def at_limit():
        raise ConflictError(
            "This audience already has 50 communities. Remove one before adding another.",
            code="audience_limit_reached",
            details={"limit": 50},
        )

    @app.get("/upstream")
    async def upstream():
        raise UpstreamError()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("asyncpg connection string user=jammysearch password=hunter2")

    @app.post("/validated")
    async def validated(payload: Payload):
        return {"ok": payload.page_size}

    # Without this the client re-raises server errors instead of returning the 500 the
    # handler produced, which is exactly the behaviour under test.
    return TestClient(app, raise_server_exceptions=False)


def _error(response):
    body = response.json()
    assert set(body) == {"error"}, "the envelope must be the whole body"
    return body["error"]


def test_a_typed_error_answers_with_its_own_status_and_code(client):
    response = client.get("/missing")

    assert response.status_code == 404
    assert _error(response) == {
        "code": "not_found",
        "message": "That audience no longer exists.",
        "details": {},
    }


def test_a_raise_site_code_and_details_reach_the_client(client):
    # This is the exact envelope contracts/rest-api.md documents by example.
    response = client.get("/at-limit")

    error = _error(response)
    assert response.status_code == 409
    assert error["code"] == "audience_limit_reached"
    assert error["details"] == {"limit": 50}
    assert "Remove one before adding another" in error["message"]


def test_an_upstream_outage_is_not_reported_as_an_application_bug(client):
    # 502, not 500 — the distinction the Ask feature's failed/refused split depends on.
    response = client.get("/upstream")

    assert response.status_code == 502
    assert _error(response)["code"] == "upstream_unavailable"


def test_an_unexpected_bug_becomes_a_generic_500(client):
    response = client.get("/boom")

    assert response.status_code == 500
    assert _error(response) == {
        "code": "internal_error",
        "message": GENERIC_ERROR_MESSAGE,
        "details": {},
    }


def test_an_unexpected_bug_never_leaks_its_own_text(client):
    # Constitution V: raw exception text must not reach the client. The raised message
    # deliberately contains a credential-shaped string.
    response = client.get("/boom")

    assert "password" not in response.text
    assert "asyncpg" not in response.text
    assert "Traceback" not in response.text


def test_an_unexpected_bug_is_logged_with_its_traceback(client, caplog):
    # It must not reach the user, but it must not vanish either.
    with caplog.at_level("ERROR"):
        client.get("/boom")

    assert "unhandled exception" in caplog.text
    assert "hunter2" in caplog.text


def test_a_malformed_request_names_the_field_to_fix(client):
    response = client.post("/validated", json={"page_size": "not a number"})

    error = _error(response)
    assert response.status_code == 422
    assert error["code"] == "invalid_request"
    assert error["details"]["fields"] == [
        {"field": "page_size", "reason": error["details"]["fields"][0]["reason"]}
    ]


def test_a_malformed_request_does_not_expose_the_model_internals(client):
    # FastAPI's default body includes the internal location tuple and the offending
    # input; the reshaped envelope carries only the field name and the reason.
    response = client.post("/validated", json={"page_size": "not a number"})

    assert "loc" not in response.text
    assert "body" not in response.text


def test_an_unrouted_path_uses_the_same_envelope(client):
    # Starlette's own 404 would otherwise be plain text with a different shape.
    response = client.get("/no-such-route")

    assert response.status_code == 404
    assert _error(response)["code"] == "not_found"


def test_a_wrong_method_uses_the_same_envelope(client):
    response = client.post("/missing")

    assert response.status_code == 405
    assert _error(response)["code"] == "method_not_allowed"


def test_an_unmapped_framework_status_still_produces_an_envelope():
    app = FastAPI()
    install_error_handling(app)

    @app.get("/teapot")
    async def teapot():
        raise HTTPException(status_code=418, detail="I'm a teapot")

    response = TestClient(app).get("/teapot")

    assert response.status_code == 418
    assert _error(response)["code"] == "request_failed"


def test_a_framework_error_without_detail_falls_back_to_the_generic_message():
    app = FastAPI()
    install_error_handling(app)

    @app.get("/silent")
    async def silent():
        raise HTTPException(status_code=418, detail="")

    response = TestClient(app).get("/silent")

    assert _error(response)["message"] == GENERIC_ERROR_MESSAGE
