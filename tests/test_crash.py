"""Sentry gets crashes only, fully annotated, never the Steam key (teable:coilyco/deploy#8409)."""

from __future__ import annotations

import http.server
import json
import logging
import socketserver
import threading

import pytest
import requests
import sentry_sdk
from sentry_sdk.transport import Transport
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from steam_mcp import crash, server

DSN = "https://public@example.invalid/1"
# Built at runtime so source context can never be where a match comes from.
KEY = "-".join(["STEAMKEY", "9f8e7d6c"])
STEAMID = "".join(["7656", "1198000011111"])


class _Capture(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    def capture_envelope(self, envelope) -> None:  # type: ignore[no-untyped-def]
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def steam_api(monkeypatch):
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(server, "API_BASE", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setenv("STEAM_WEB_API_KEY", KEY)
    monkeypatch.setenv("STEAM_STEAMID64", STEAMID)
    yield
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def captured(monkeypatch):
    transport = _Capture()
    real_init = sentry_sdk.init
    monkeypatch.setattr(
        crash.sentry_sdk, "init", lambda **kwargs: real_init(transport=transport, **kwargs)
    )
    monkeypatch.setattr(crash, "_window", [])
    monkeypatch.setattr(crash, "_secrets", set())
    monkeypatch.setenv("SENTRY_DSN", DSN)
    assert crash.init_crash_reporting() is True
    yield transport
    real_init()


def _app() -> Starlette:
    async def crashed(_request):
        route_name = "GetOwnedGames"  # noqa: F841 - a harmless local that must stay readable
        url = server._api_url("IPlayerService/GetOwnedGames/v1/", {"include_appinfo": 1})
        requests.get(url, timeout=5)
        raise RuntimeError(f"Steam call failed for {url}")

    async def handled(_request):
        logging.getLogger("steam_mcp.test").error("Steam API returned 503, returning a tool error")
        return PlainTextResponse("ok")

    async def refused(_request):
        raise HTTPException(status_code=503, detail="deliberate")

    return Starlette(
        routes=[Route("/crash", crashed), Route("/handled", handled), Route("/refused", refused)]
    )


def test_no_dsn_leaves_sentry_off(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert crash.init_crash_reporting() is False


def test_a_crash_is_fully_annotated_and_carries_no_key(captured, steam_api):
    client = TestClient(_app(), raise_server_exceptions=False)
    assert client.get("/crash").status_code == 500
    sentry_sdk.flush()
    (event,) = captured.events
    dump = json.dumps(event, default=str)
    # The key reached three places before value redaction: the httplib
    # breadcrumb's query, the `url` local, and the exception message.
    assert KEY not in dump
    assert STEAMID not in dump
    frames = event["exception"]["values"][-1]["stacktrace"]["frames"]
    assert "GetOwnedGames" in frames[-1]["vars"]["route_name"]
    assert frames[-1].get("context_line")
    categories = [crumb.get("category") for crumb in event["breadcrumbs"]["values"]]
    assert "httplib" in categories
    assert event["request"]["method"] == "GET"
    assert event["request"]["url"].endswith("/crash")


def test_a_registered_secret_is_redacted_from_any_string(captured):
    crash.register_secret(KEY)
    try:
        value = f"resolved {KEY}"  # noqa: F841 - a generic name no key scrubber would mask
        raise RuntimeError("boom")
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)
    sentry_sdk.flush()
    assert KEY not in json.dumps(captured.events, default=str)


def test_a_key_held_under_a_known_name_is_scrubbed(captured):
    try:
        # Built inline: a separate local would hold it under an unscrubbed name.
        params = {"key": "".join(["UNREGISTERED", "123456"])}  # noqa: F841
        raise RuntimeError("boom")
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)
    sentry_sdk.flush()
    assert "".join(["UNREGISTERED", "123456"]) not in json.dumps(captured.events, default=str)


def test_handled_errors_stay_out_of_sentry(captured):
    client = TestClient(_app(), raise_server_exceptions=False)
    assert client.get("/handled").status_code == 200
    assert client.get("/refused").status_code == 503
    sentry_sdk.flush()
    assert captured.events == []


def test_the_mcp_integration_is_on_without_pii(captured):
    client = sentry_sdk.get_client()
    assert client.get_integration("mcp") is not None
    # Tool arguments and results are recorded only with PII on.
    assert client.options["send_default_pii"] is False


def test_budget_caps_events_per_process_minute(monkeypatch):
    monkeypatch.setattr(crash, "_window", [])
    allowed = [crash._within_budget(100.0) for _ in range(crash.EVENTS_PER_MINUTE + 1)]
    assert allowed.count(True) == crash.EVENTS_PER_MINUTE
    assert allowed[-1] is False
    assert crash._within_budget(161.0) is True


def test_init_failure_logs_the_class_and_never_the_dsn(monkeypatch, caplog):
    def refuse(**_kwargs):
        raise ValueError("https://secret-key@o0.ingest.example/1")

    monkeypatch.setattr(crash.sentry_sdk, "init", refuse)
    monkeypatch.setenv("SENTRY_DSN", "https://secret-key@o0.ingest.example/1")
    with caplog.at_level(logging.WARNING, logger="steam_mcp.crash"):
        assert crash.init_crash_reporting() is False
    assert "ValueError" in caplog.text
    assert "secret-key" not in caplog.text
