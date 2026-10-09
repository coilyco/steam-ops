"""Authenticated, read-only Steam client-protocol/PICS adapter.

The ``steamio`` package (steam.py) owns the client protocol.  This adapter owns
only credential precedence, the refresh-token lifecycle, and normalization.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess
from collections.abc import Callable, Iterator
from typing import Any, Literal

LOGIN_READY_TIMEOUT = 45

_GATEWAY_LOG = "steam.gateway"
# steamio logs why it closed the socket at DEBUG only, in these two lines. A reword
# there loses the hint, never the failure.
_CLOSE_REASONS = ("Failed to login with result", "Received WSMessage")
# Logged right after Steam accepts the logon response, before the post-logon requests.
_LOGON_ACCEPTED = "Heartbeat started"

_session_lock: asyncio.Lock | None = None
_session_lock_loop: asyncio.AbstractEventLoop | None = None


def _one_session_at_a_time() -> asyncio.Lock:
    """One lock per running loop: every call is a fresh logon on the same account."""
    global _session_lock, _session_lock_loop
    loop = asyncio.get_running_loop()
    if _session_lock is None or _session_lock_loop is not loop:
        _session_lock, _session_lock_loop = asyncio.Lock(), loop
    return _session_lock


class ClientProtocolAdapter:
    """Use a Steam account session only for PICS/account reads.

    A refresh token is the only runtime credential. Account credentials and
    Steam Guard input belong to the separate, operator-run bootstrap command.
    Nothing from either credential path is returned or included in exception
    text.
    """

    def __init__(
        self,
        optional_secret: Callable[[str], str | None],
        persist_refresh_token: Callable[[str], None],
    ) -> None:
        self._optional_secret = optional_secret
        self._persist_refresh_token = persist_refresh_token

    async def _login(
        self,
        client: Any,
        operation: Callable[[Any], Any],
        *,
        readiness: Literal["login", "licenses"],
    ) -> Any:
        refresh_token = self._optional_secret("client_refresh_token")
        if not refresh_token:
            raise ValueError(
                "Steam client refresh token is not configured; run the documented "
                "operator bootstrap."
            )
        result = await run_authenticated_session(
            client,
            operation,
            ready_timeout=LOGIN_READY_TIMEOUT,
            readiness=readiness,
            refresh_token=refresh_token,
        )
        rotated_token = getattr(client, "refresh_token", None)
        if rotated_token and rotated_token != refresh_token:
            self._persist_refresh_token(rotated_token)
        return result

    async def _with_client(
        self,
        operation: Callable[[Any], Any],
        *,
        readiness: Literal["login", "licenses"],
    ) -> Any:
        async with _one_session_at_a_time():
            with _capture_close_reason() as seen:
                try:
                    import steam

                    async with steam.Client() as client:
                        return await self._login(client, operation, readiness=readiness)
                except BaseExceptionGroup as group:
                    # steamio's TaskGroup wraps ConnectionClosed in a group the tuple
                    # below never matched, so callers saw only "unhandled errors".
                    raise _failure(_leaf_types(group), seen) from None
                except (ImportError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
                    raise _failure([type(exc).__name__], seen) from None

    async def product_info(self, appid: int) -> dict[str, Any]:
        """Read PICS product metadata for a single positive application id."""
        if appid <= 0:
            raise ValueError("appid must be a positive Steam application id")

        async def operation(client: Any) -> dict[str, Any]:
            # steamio reads `.id` off each app, so a bare int raises AttributeError.
            infos = await client.fetch_product_info(apps=[client.get_app(appid)])
            info = infos[0] if infos else None
            return {
                "source": "steam_client_pics",
                "provenance": {"plane": "client_pics"},
                "item": _product_item(appid, info),
            }

        return await self._with_client(operation, readiness="login")

    async def licenses(self) -> dict[str, Any]:
        """Read account package-license metadata without exposing access tokens."""

        async def operation(client: Any) -> dict[str, Any]:
            items = [
                {
                    "packageid": getattr(account_license, "id", None),
                    "created_at": _safe_value(getattr(account_license, "created_at", None)),
                    "payment_method": _safe_value(getattr(account_license, "payment_method", None)),
                }
                for account_license in client.licenses
            ]
            return {
                "source": "steam_client",
                "provenance": {"plane": "client_pics"},
                "count": len(items),
                "items": items,
            }

        return await self._with_client(operation, readiness="licenses")


class _CloseReasons(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []
        self.logon_accepted = False

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if text.startswith(_CLOSE_REASONS):
            self.lines.append(text)
        elif text.startswith(_LOGON_ACCEPTED):
            self.logon_accepted = True


@contextlib.contextmanager
def _capture_close_reason() -> Iterator[_CloseReasons]:
    """Collect steamio's DEBUG close-reason lines for the failure message.

    The lines carry a Steam result or a websocket close code, never the token.
    """
    log = logging.getLogger(_GATEWAY_LOG)
    handler = _CloseReasons()
    previous = log.level
    log.addHandler(handler)
    if previous == logging.NOTSET or previous > logging.DEBUG:
        log.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)


def _leaf_types(group: BaseExceptionGroup[BaseException]) -> list[str]:
    """Class names of every non-group exception inside a (nested) group."""
    names: list[str] = []
    for member in group.exceptions:
        if isinstance(member, BaseExceptionGroup):
            names.extend(_leaf_types(member))
        else:
            names.append(type(member).__name__)
    return names


def _failure(types: list[str], seen: _CloseReasons) -> RuntimeError:
    """Name what failed by class and Steam's stated reason, never by exception text."""
    detail = ", ".join(dict.fromkeys(types)) or "unknown"
    if seen.lines:
        detail += f"; steam said: {seen.lines[-1]}"
    if seen.logon_accepted and "TimeoutError" in types:
        hint = (
            f"Steam accepted the refresh token but sent no reply to the post-logon "
            f"requests within {LOGIN_READY_TIMEOUT}s; the token is not the cause, retry."
        )
    elif seen.logon_accepted:
        hint = "Steam accepted the refresh token, so the failure came after logon."
    else:
        hint = "verify the persisted refresh token or perform the documented manual bootstrap."
    return RuntimeError(f"Steam client authentication or PICS request failed ({detail}); {hint}")


async def run_authenticated_session(
    client: Any,
    operation: Callable[[Any], Any],
    *,
    ready_timeout: float | None,
    readiness: Literal["licenses", "login", "ready", "refresh_token"] = "ready",
    **login_kwargs: Any,
) -> Any:
    """Run one operation after the requested steamio milestone, then close.

    ``steam.Client.login`` owns the connection loop and normally returns only
    when the client closes. It must therefore run beside the readiness wait,
    rather than being awaited before the operation. Bootstrap needs only the
    ``login`` event because the refresh token exists before cache hydration.
    """
    login_task = asyncio.create_task(client.login(**login_kwargs))
    if readiness == "licenses":
        readiness_task = asyncio.create_task(client._state.handled_licenses.wait())
    elif readiness == "login":
        readiness_task = asyncio.create_task(client.wait_for("login"))
    elif readiness == "refresh_token":
        readiness_task = asyncio.create_task(_wait_for_refresh_token(client))
    else:
        readiness_task = asyncio.create_task(client.wait_until_ready())
    try:
        done, _ = await asyncio.wait(
            {login_task, readiness_task},
            timeout=ready_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if login_task in done:
            await login_task
            if readiness_task not in done:
                raise RuntimeError("Steam client login ended before the requested milestone")
        if readiness_task in done:
            await readiness_task
        else:
            raise TimeoutError("Steam client did not reach the requested login milestone")
        return await operation(client)
    finally:
        readiness_task.cancel()
        if readiness == "refresh_token" and not login_task.done():
            login_task.cancel()
            await asyncio.gather(login_task, return_exceptions=True)
        try:
            close = client.close()
            if readiness == "refresh_token":
                try:
                    await asyncio.wait_for(close, timeout=5)
                except TimeoutError:
                    pass
            else:
                await close
        finally:
            if not login_task.done():
                login_task.cancel()
            await asyncio.gather(login_task, readiness_task, return_exceptions=True)


async def _wait_for_refresh_token(client: Any) -> None:
    """Return as soon as Steam assigns the persistent login credential."""
    while not getattr(client, "refresh_token", None):
        await asyncio.sleep(0.05)


def _safe_value(value: Any) -> Any:
    """Serialize enum/date-like public metadata without serializing secrets."""
    return getattr(value, "value", value.isoformat() if hasattr(value, "isoformat") else value)


def _product_item(appid: int, info: Any) -> dict[str, Any] | None:
    """Flatten steamio's AppInfo, which exposes attributes and has no `common` dict."""
    if info is None:
        return None
    platforms = (
        ("windows", info.is_on_windows()),
        ("macos", info.is_on_mac_os()),
        ("linux", info.is_on_linux()),
    )
    app_type = info.type
    return {
        "appid": appid,
        "name": info.name,
        "type": getattr(app_type, "name", None) or str(app_type),
        "oslist": [name for name, supported in platforms if supported],
        "is_free": info.is_free(),
        "change_number": info.change_number,
    }


def persist_refresh_token(ssm_name: str, refresh_token: str) -> None:
    """Write a rotated token to SSM without placing its value in process argv."""
    payload = json.dumps(
        {"Name": ssm_name, "Value": refresh_token, "Type": "SecureString", "Overwrite": True}
    )
    try:
        subprocess.run(
            ["aws", "ssm", "put-parameter", "--cli-input-json", "file:///dev/stdin"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Steam client refresh token could not be persisted") from None
