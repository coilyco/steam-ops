"""Send crashes, and only crashes, to Sentry when SENTRY_DSN is set.

Handled errors stay out to keep inside Sentry's shared free quota
(teable:coilyco/deploy#8347), but a crash carries everything the integrations
attach: frame locals, source context, breadcrumbs, request data, MCP context.
The Steam key rides in request URLs, so it is scrubbed twice: by variable name,
and by value wherever a string still carries it.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

if TYPE_CHECKING:
    from sentry_sdk.types import Breadcrumb, Event

EVENTS_PER_MINUTE = 20
# Names that hold the Steam key, the account id, or a client token.
USER_DATA_KEYS = [
    "key",
    "api_key",
    "steam_key",
    "steamid",
    "steam_id",
    "steamid64",
    "access_token",
    "refresh_token",
    "client_refresh_token",
    "params",
]
_REDACTED = "[redacted]"
_log = logging.getLogger(__name__)
_window: list[float] = []
_secrets: set[str] = set()
_SECRET_PARAM = re.compile(r"((?:key|steamid|access_token)=)[^&\s'\"]+", re.IGNORECASE)


def register_secret(value: str | None) -> None:
    """Remember a resolved secret so any string that still carries it is redacted."""
    if value and len(value) >= 6:
        _secrets.add(value)


def _within_budget(now: float) -> bool:
    """Cap events per process so one crash loop cannot spend the monthly quota."""
    cutoff = now - 60.0
    while _window and _window[0] < cutoff:
        _window.pop(0)
    if len(_window) >= EVENTS_PER_MINUTE:
        return False
    _window.append(now)
    return True


def _redact_text(text: str) -> str:
    text = _SECRET_PARAM.sub(r"\1" + _REDACTED, text)
    for secret in _secrets:
        text = text.replace(secret, _REDACTED)
    return text


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def _before_send(event: Event, _hint: dict[str, Any]) -> Event | None:
    if not _within_budget(time.monotonic()):
        return None
    return _redact(event)


def _before_breadcrumb(crumb: Breadcrumb, _hint: dict[str, Any]) -> Breadcrumb | None:
    return _redact(crumb)


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
            send_default_pii=False,
            before_send=_before_send,
            before_breadcrumb=_before_breadcrumb,
            event_scrubber=EventScrubber(
                denylist=DEFAULT_DENYLIST + USER_DATA_KEYS, recursive=True
            ),
            # Every integration stays on for annotation. These two only stop a
            # handled error or a deliberate 5xx from becoming an event.
            integrations=[
                LoggingIntegration(event_level=None),
                StarletteIntegration(failed_request_status_codes=set()),
            ],
        )
    except Exception as exc:
        # The class only: a BadDsn message can carry the DSN itself.
        _log.warning("Sentry initialization failed (%s); continuing", type(exc).__name__)
        return False
    return True
