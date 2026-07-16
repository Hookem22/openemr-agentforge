"""Pure unit tests for app/retry.py (Engineering Requirements: "All outbound LLM and retrieval
calls must have timeouts and retry logic"). No real network, no real sleeping -- time.sleep is
monkeypatched so these run instantly regardless of tenacity's exponential backoff config.

Two policies are tested separately because they're deliberately not the same (see retry.py's
module docstring): retry_idempotent_http retries the full transient set (connect errors, any
timeout, 429/5xx) since a retried call there is always safe to repeat; retry_connect_only_http
retries only pre-send connection failures, since the calls it guards (document upload,
medication/allergy persistence) have no server-side dedup and a ReadTimeout is genuinely ambiguous.
"""
from __future__ import annotations

import time

import httpx
import pytest

from app.retry import retry_connect_only_http, retry_idempotent_http


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def _fake_request() -> httpx.Request:
    return httpx.Request("GET", "https://example.test/thing")


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = _fake_request()
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code} error", request=request, response=response)


def test_idempotent_retries_connect_error_and_eventually_succeeds():
    calls = {"n": 0}

    @retry_idempotent_http
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom", request=_fake_request())
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_idempotent_retries_503_and_eventually_succeeds():
    calls = {"n": 0}

    @retry_idempotent_http
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _status_error(503)
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_idempotent_does_not_retry_a_404_permanent_client_error():
    calls = {"n": 0}

    @retry_idempotent_http
    def always_404():
        calls["n"] += 1
        raise _status_error(404)

    with pytest.raises(httpx.HTTPStatusError):
        always_404()
    assert calls["n"] == 1  # no retry -- a 404 is permanent, retrying wastes time and hides the real error


def test_idempotent_gives_up_after_three_attempts():
    calls = {"n": 0}

    @retry_idempotent_http
    def always_fails():
        calls["n"] += 1
        raise httpx.ConnectError("boom", request=_fake_request())

    with pytest.raises(httpx.ConnectError):
        always_fails()
    assert calls["n"] == 3  # 1 original attempt + 2 retries, matching Anthropic/Voyage's own default


def test_connect_only_retries_connect_error():
    calls = {"n": 0}

    @retry_connect_only_http
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("boom", request=_fake_request())
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_connect_only_does_not_retry_a_read_timeout():
    """The key difference from retry_idempotent_http: a ReadTimeout means the request may already
    have been processed server-side (unlike a ConnectError, which means it never arrived) -- for a
    write with no server-side dedup, retrying that would risk a real duplicate, so this policy
    must NOT retry it."""
    calls = {"n": 0}

    @retry_connect_only_http
    def always_read_timeout():
        calls["n"] += 1
        raise httpx.ReadTimeout("boom", request=_fake_request())

    with pytest.raises(httpx.ReadTimeout):
        always_read_timeout()
    assert calls["n"] == 1


def test_connect_only_does_not_retry_a_503():
    """503 (or any HTTPStatusError) is also not retried by this policy -- only pure pre-send
    connection failures are, per the same non-idempotent-write reasoning."""
    calls = {"n": 0}

    @retry_connect_only_http
    def always_503():
        calls["n"] += 1
        raise _status_error(503)

    with pytest.raises(httpx.HTTPStatusError):
        always_503()
    assert calls["n"] == 1
