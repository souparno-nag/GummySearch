"""Unit tests for the startup bind guard (FR-078, SC-020, R17).

The guard is the thing standing between "this tool runs on my laptop" and "this tool is
on the internet". FR-078 requires binding beyond the local machine to take an explicit
configuration change, and the spec's last edge case requires the application to *refuse
to start* and explain itself rather than silently becoming reachable.

Nothing here opens a socket or resolves a name. The guard is written as pure functions
taking the host and the flag as arguments precisely so it can be tested exhaustively
without a network — Constitution IV forbids a test reaching one.
"""

import pytest

from app.main import (
    UnsafeBindError,
    assert_bind_allowed,
    create_app,
    is_loopback,
    resolve_bind_host,
)

# ---------------------------------------------------------------------------
# Which hosts count as local
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.53",  # the whole 127.0.0.0/8 block is loopback, not just .0.1
        "localhost",
        "::1",
        "[::1]",  # the bracketed form uvicorn accepts for IPv6
        "  127.0.0.1  ",  # surrounding whitespace must not change the verdict
        "LOCALHOST",
    ],
)
def test_local_hosts_are_recognised_as_loopback(host):
    assert is_loopback(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",  # every interface — the classic accidental exposure
        "::",
        "192.168.1.10",
        "10.0.0.5",
        "203.0.113.7",
        "",
    ],
)
def test_non_local_hosts_are_not_loopback(host):
    assert is_loopback(host) is False


def test_an_unresolvable_hostname_is_treated_as_remote():
    # The guard deliberately does not resolve DNS: resolution needs a network (forbidden
    # in tests) and the answer can change under it. Failing closed means an unrecognised
    # name is refused rather than quietly permitted.
    assert is_loopback("jammysearch.example.com") is False


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_a_local_bind_is_allowed_without_the_exposure_flag(host):
    # The default posture: the product works out of the box, bound to the machine only.
    assert_bind_allowed(host, allow_remote_exposure=False)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_a_remote_bind_is_refused_without_the_exposure_flag(host):
    with pytest.raises(UnsafeBindError):
        assert_bind_allowed(host, allow_remote_exposure=False)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10"])
def test_a_remote_bind_is_permitted_once_the_flag_is_set(host):
    # SC-020: exposing the application is a configuration change and nothing more. If
    # this ever needed a code edit to pass, the requirement would be unmet.
    assert_bind_allowed(host, allow_remote_exposure=True)


def test_the_refusal_names_the_host_and_the_setting_that_would_permit_it():
    # FR-052's "say what failed and what to do next", applied to a startup failure.
    # A bare "refused to start" would send the operator hunting through source.
    with pytest.raises(UnsafeBindError) as raised:
        assert_bind_allowed("0.0.0.0", allow_remote_exposure=False)

    message = str(raised.value)

    assert "0.0.0.0" in message
    assert "ALLOW_REMOTE_EXPOSURE" in message


# ---------------------------------------------------------------------------
# Working out which host was actually asked for
# ---------------------------------------------------------------------------


def test_an_explicit_uvicorn_host_argument_is_what_gets_checked():
    # quickstart.md starts the server through the uvicorn CLI, so `--host` is the real
    # way a person exposes this application. A guard that only read configuration would
    # miss the exact command that causes the problem.
    assert resolve_bind_host(["uvicorn", "app.main:app", "--host", "0.0.0.0"]) == "0.0.0.0"


def test_the_joined_host_argument_form_is_understood():
    assert resolve_bind_host(["uvicorn", "app.main:app", "--host=0.0.0.0"]) == "0.0.0.0"


def test_the_host_falls_back_to_configuration_when_no_argument_is_given():
    # Covers programmatic startup, where there is no uvicorn command line to read.
    assert is_loopback(resolve_bind_host(["pytest"]))


def test_a_trailing_host_flag_with_no_value_does_not_crash():
    # Malformed input is uvicorn's to complain about, not ours to explode on.
    assert is_loopback(resolve_bind_host(["uvicorn", "app.main:app", "--host"]))


# ---------------------------------------------------------------------------
# The guard is actually wired into startup
# ---------------------------------------------------------------------------


def test_building_the_application_refuses_an_unsafe_bind(monkeypatch):
    # The guard existing is worthless if nothing calls it. This is the test that would
    # fail if a later change quietly dropped the call from create_app().
    monkeypatch.setattr("sys.argv", ["uvicorn", "app.main:app", "--host", "0.0.0.0"])

    with pytest.raises(UnsafeBindError):
        create_app()


def test_building_the_application_succeeds_when_exposure_is_permitted(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr("sys.argv", ["uvicorn", "app.main:app", "--host", "0.0.0.0"])
    monkeypatch.setattr(main_module.settings, "allow_remote_exposure", True)

    assert create_app() is not None


def test_building_the_application_is_unaffected_by_a_normal_local_start(monkeypatch):
    monkeypatch.setattr("sys.argv", ["uvicorn", "app.main:app", "--reload"])

    assert create_app() is not None
