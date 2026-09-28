"""Send crashes, and only crashes, to Sentry when SENTRY_DSN is set.

Handled errors, including every tool error FastMCP returns to a caller, stay out
to keep inside Sentry's shared free quota (teable:coilyco/deploy#8347). The
Steam key rides in request URLs, so frame locals, source context and breadcrumbs are off, and any
`key=` value left in an exception message is redacted before send.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.mcp import MCPIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

if TYPE_CHECKING:
    from sentry_sdk.types import Event

EVENTS_PER_MINUTE = 20
_log = logging.getLogger(__name__)
_window: list[float] = []
_SECRET_PARAM = re.compile(r"((?:key|steamid|access_token)=)[^&\s'\"]+", re.IGNORECASE)


def _within_budget(now: float) -> bool:
    """Cap events per process so one crash loop cannot spend the monthly quota."""
    cutoff = now - 60.0
    while _window and _window[0] < cutoff:
        _window.pop(0)
    if len(_window) >= EVENTS_PER_MINUTE:
        return False
    _window.append(now)
    return True


def _redact(event: Event) -> Event:
    for value in (event.get("exception") or {}).get("values") or []:
        if isinstance(value.get("value"), str):
            value["value"] = _SECRET_PARAM.sub(r"\1[redacted]", value["value"])
    return event


def _before_send(event: Event, _hint: dict[str, Any]) -> Event | None:
    return _redact(event) if _within_budget(time.monotonic()) else None


def init_crash_reporting() -> bool:
    """Turn crash reporting on when SENTRY_DSN is set. Never fails the start."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=0.0,
            environment=os.environ.get("SENTRY_ENVIRONMENT", "homelab"),
            include_local_variables=False,
            include_source_context=False,
            max_breadcrumbs=0,
            max_request_body_size="never",
            send_default_pii=False,
            before_send=_before_send,
            integrations=[
                LoggingIntegration(level=None, event_level=None),
                StarletteIntegration(failed_request_status_codes=set()),
            ],
            # It reports every MCP tool error, which FastMCP returns as handled.
            disabled_integrations=[MCPIntegration()],
        )
    except Exception as exc:
        # The class only: a BadDsn message can carry the DSN itself.
        _log.warning("Sentry initialization failed (%s); continuing", type(exc).__name__)
        return False
    return True
