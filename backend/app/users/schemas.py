"""Request and response models for the users module.

Constitution III requires every endpoint to declare Pydantic models; returning a bare dict or
an ORM instance from a router is prohibited, because the generated OpenAPI schema — which the
frontend is written against — is only as truthful as these declarations.

The shapes here are contracted in `contracts/rest-api.md` under "Sessions". One property is
worth stating: **no model in this file carries a session token.** The token reaches the client
in exactly one place, the `HttpOnly` cookie, because FR-079 requires secrets to be unreadable
by the client and a response body is readable by any script on the page.

External systems touched: none.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class SignInRequest(BaseModel):
    """A sign-in attempt.

    Neither field is validated beyond being a non-empty string. Rejecting a *malformed*
    credential differently from a wrong one would tell a caller which usernames could exist,
    and length or character rules on a password field leak the shape of the real one.
    """

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SessionResponse(BaseModel):
    """The signed-in session, as a client is allowed to see it.

    `expires_at` is here so the client can show how long is left, or refresh ahead of the
    deadline, instead of hard-coding an assumption about the server's TTL. It is UTC ISO 8601
    like every other timestamp on the wire (Constitution V).
    """

    username: str
    expires_at: datetime
