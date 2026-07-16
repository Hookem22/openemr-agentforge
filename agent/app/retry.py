"""Shared retry policy for outbound httpx calls (Engineering Requirements: "All outbound LLM and
retrieval calls must have timeouts and retry logic"). Timeouts already exist on every outbound
call (see fhir_client.py, ingestion.py, main.py's /ready checks). This module adds the missing
retry half for the two clients that don't already have it: `httpx` itself is retry-agnostic by
design (no client-level retry), unlike the Anthropic and Voyage SDKs used elsewhere in this
service -- see the note below on why those two are deliberately left alone.

**Anthropic and Voyage calls are NOT wrapped here, on purpose**: both SDKs already retry
transient errors internally.
- `anthropic.Anthropic(...)` defaults to `max_retries=2`, retrying on 408/409/429/5xx and
  connection errors (confirmed via `anthropic._base_client.BaseClient._should_retry`).
- `voyageai.Client(...)` defaults to `max_retries=0` (off) but supports the same idea --
  `rag.py`'s `_voyage_client()` now passes `max_retries=2`, using the SDK's own tenacity-based
  retry (confirmed via its source: retries only `RateLimitError`, `ServiceUnavailableError`,
  `Timeout` -- the correct transient set, not every exception).
Wrapping either SDK call in a second, independent retry layer here would stack retries (up to
~9x latency under a real outage) rather than add safety -- so this module is httpx-only.

Two retry policies, not one, because httpx POST calls aren't all equally safe to retry:
- `retry_idempotent_http`: full transient-error retry (connect failures, any timeout, 429/5xx).
  Safe for GET requests and for POSTs that have real server-side dedup (e.g.
  `persist_lab_results` -> `procedure_order.external_id`) -- a retried request that actually
  succeeded the first time is a harmless no-op on the server.
- `retry_connect_only_http`: retries only on `ConnectError`/`ConnectTimeout` -- failures in the
  TCP handshake itself, before any bytes of the request were sent, so the server could not
  possibly have processed it. Used for POSTs with no server-side dedup (document upload,
  medication/allergy persistence) -- a `ReadTimeout` there is genuinely ambiguous (the request may
  have been received and processed; we just didn't see the response), so retrying it risks a
  real duplicate write. Documented here rather than silently retried "for safety."
"""
from __future__ import annotations

from typing import Callable, TypeVar

import httpx
from tenacity import retry, retry_if_exception, retry_if_exception_type, stop_after_attempt, wait_exponential

T = TypeVar("T")

_STOP = stop_after_attempt(3)  # 1 original attempt + 2 retries -- matches Anthropic/Voyage's own default
_WAIT = wait_exponential(multiplier=0.5, min=0.5, max=4)


def _is_transient_http_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def retry_idempotent_http(fn: Callable[..., T]) -> Callable[..., T]:
    return retry(stop=_STOP, wait=_WAIT, retry=retry_if_exception(_is_transient_http_error), reraise=True)(fn)


def retry_connect_only_http(fn: Callable[..., T]) -> Callable[..., T]:
    return retry(
        stop=_STOP, wait=_WAIT,
        retry=retry_if_exception_type((httpx.ConnectError, httpx.ConnectTimeout)),
        reraise=True,
    )(fn)
