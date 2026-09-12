"""
Safe, read-only HTTP health checking for a single service.

Design goals (see project README):
  - Only ever perform a GET request. Never submit forms, never follow a login
    flow, never send payment data, never write anything to the target system.
  - Clearly distinguish "server/website reachable" from "keyword/content
    verified" so the dashboard never overstates what was actually checked.
  - Classify failures (timeout / DNS / connection / SSL / bad HTTP status /
    keyword mismatch) so incident history is informative.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from requests.exceptions import (
    ConnectionError as ReqConnectionError,
    SSLError,
    Timeout,
    TooManyRedirects,
    RequestException,
)

# Socket-level DNS errors surface inside requests' ConnectionError, but we can
# detect them more specifically by inspecting the exception chain.
import socket


@dataclass
class CheckResult:
    ok: bool                     # True if server responded with an acceptable status
    keyword_ok: Optional[bool]   # None if no keyword configured, else True/False
    http_status: Optional[int]
    response_time_ms: Optional[int]
    error_type: str              # "" | TIMEOUT | DNS_ERROR | CONNECTION_ERROR |
                                  # SSL_ERROR | HTTP_ERROR | TOO_MANY_REDIRECTS |
                                  # UNKNOWN_ERROR
    error_message: str = ""
    final_url: Optional[str] = field(default=None)


def _is_dns_error(exc: BaseException) -> bool:
    cur = exc
    seen = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, socket.gaierror):
            return True
        msg = str(cur).lower()
        if "name or service not known" in msg or "nodename nor servname" in msg \
                or "getaddrinfo failed" in msg or "temporary failure in name resolution" in msg:
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return False


def check_service(
    url: str,
    expect_status: list[int],
    keyword: Optional[str],
    timeout_seconds: int,
    user_agent: str,
) -> CheckResult:
    """
    Perform one safe GET request against `url` and classify the result.
    Never raises — all failure modes are captured into CheckResult.
    """
    headers = {"User-Agent": user_agent, "Accept": "text/html,*/*"}
    start = time.monotonic()
    try:
        resp = requests.get(
            url,
            headers=headers,
            timeout=timeout_seconds,
            allow_redirects=True,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        status_ok = resp.status_code in expect_status
        keyword_ok: Optional[bool] = None
        if keyword:
            keyword_ok = keyword.lower() in (resp.text or "").lower()

        if not status_ok:
            return CheckResult(
                ok=False,
                keyword_ok=keyword_ok,
                http_status=resp.status_code,
                response_time_ms=elapsed_ms,
                error_type="HTTP_ERROR",
                error_message=f"Unexpected HTTP status {resp.status_code}",
                final_url=resp.url,
            )

        return CheckResult(
            ok=True,
            keyword_ok=keyword_ok,
            http_status=resp.status_code,
            response_time_ms=elapsed_ms,
            error_type="",
            final_url=resp.url,
        )

    except Timeout as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return CheckResult(
            ok=False, keyword_ok=None, http_status=None,
            response_time_ms=elapsed_ms, error_type="TIMEOUT",
            error_message=f"Request timed out after {timeout_seconds}s",
        )

    except SSLError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return CheckResult(
            ok=False, keyword_ok=None, http_status=None,
            response_time_ms=elapsed_ms, error_type="SSL_ERROR",
            error_message=str(exc)[:300],
        )

    except TooManyRedirects as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return CheckResult(
            ok=False, keyword_ok=None, http_status=None,
            response_time_ms=elapsed_ms, error_type="TOO_MANY_REDIRECTS",
            error_message=str(exc)[:300],
        )

    except ReqConnectionError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if _is_dns_error(exc):
            return CheckResult(
                ok=False, keyword_ok=None, http_status=None,
                response_time_ms=elapsed_ms, error_type="DNS_ERROR",
                error_message="DNS resolution failed",
            )
        return CheckResult(
            ok=False, keyword_ok=None, http_status=None,
            response_time_ms=elapsed_ms, error_type="CONNECTION_ERROR",
            error_message=str(exc)[:300],
        )

    except RequestException as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return CheckResult(
            ok=False, keyword_ok=None, http_status=None,
            response_time_ms=elapsed_ms, error_type="UNKNOWN_ERROR",
            error_message=str(exc)[:300],
        )
