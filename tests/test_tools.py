"""Behavioural tests for the steam-mcp tools.

The tools are registered with FastMCP without rebinding their names, so the
plain callables stay directly invokable here. The focus mirrors reddit-mcp's:
the read-only + credential-custody envelope. The key + steamid64 resolve
env-first then SSM, an unconfigured secret fails loud, the request carries the
right IPlayerService params, the normalization is scrape-faithful, and there is
no write tool.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import steam
from steam.manifest import AppInfo

_SECRET_ENV = [
    "STEAM_WEB_API_KEY",
    "STEAM_STEAMID64",
    "STEAM_CLIENT_REFRESH_TOKEN",
]


def _load(monkeypatch: pytest.MonkeyPatch, env: dict[str, str] | None = None) -> ModuleType:
    """Reimport the server module with a clean secret env applied."""
    for var in _SECRET_ENV:
        monkeypatch.delenv(var, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    import steam_mcp.server as server

    return importlib.reload(server)


def _creds() -> dict[str, str]:
    return {"STEAM_WEB_API_KEY": "test-key", "STEAM_STEAMID64": "7656119800000000"}


_OWNED_PAYLOAD = {
    "response": {
        "game_count": 1,
        "games": [
            {
                "appid": 440,
                "name": "  Team Fortress 2  ",
                "playtime_forever": 150,  # minutes -> 2.5 hours
                "playtime_2weeks": 30,
                "rtime_last_played": 1_700_000_000,
                "img_icon_url": "abc",
            }
        ],
    }
}

_RECENT_PAYLOAD = {
    "response": {
        "total_count": 1,
        "games": [
            {"appid": 570, "name": "Dota 2", "playtime_forever": 6000, "playtime_2weeks": 120},
        ],
    }
}


def test_steamio_runtime_imports_are_compatible() -> None:
    """Exercise the lazy import that the interactive bootstrap requires."""
    importlib.import_module("steam.manifest")


def test_owned_games_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _load(monkeypatch, _creds())
    monkeypatch.setattr(server, "_fetch_json", lambda url: _OWNED_PAYLOAD)
    got = server.get_owned_games()
    assert got["source"] == "owned_games"
    assert got["count"] == 1
    game = got["items"][0]
    assert game["appid"] == 440
    assert game["name"] == "Team Fortress 2"  # stripped
    assert game["playtime_forever_minutes"] == 150
    assert game["playtime_forever_hours"] == 2.5  # minutes converted to hours
    assert game["last_played_unix"] == 1_700_000_000


def test_recently_played_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _load(monkeypatch, _creds())
    monkeypatch.setattr(server, "_fetch_json", lambda url: _RECENT_PAYLOAD)
    got = server.get_recently_played()
    assert got["source"] == "recently_played"
    assert got["count"] == 1
    game = got["items"][0]
    assert game["appid"] == 570
    assert game["playtime_forever_hours"] == 100.0
    # No rtime_last_played in the recent payload -> None, not epoch 0.
    assert game["last_played_unix"] is None


def test_owned_games_url_carries_required_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """GetOwnedGames must request app info + free games, keyed by the steamid."""
    server = _load(monkeypatch, _creds())
    seen: dict[str, str] = {}

    def _capture(url: str) -> dict:
        seen["url"] = url
        return {"response": {"games": []}}

    monkeypatch.setattr(server, "_fetch_json", _capture)
    server.get_owned_games()
    url = seen["url"]
    assert "IPlayerService/GetOwnedGames" in url
    assert "include_appinfo=1" in url
    assert "include_played_free_games=1" in url
    assert "key=test-key" in url
    assert "steamid=7656119800000000" in url


def test_private_profile_yields_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A private profile / empty result returns `response: {}` - an empty list,
    not an error: the request itself succeeded."""
    server = _load(monkeypatch, _creds())
    monkeypatch.setattr(server, "_fetch_json", lambda url: {"response": {}})
    got = server.get_owned_games()
    assert got["count"] == 0
    assert got["items"] == []


def test_unconfigured_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _load(monkeypatch)  # no env
    monkeypatch.setattr(server, "_ssm", lambda name: None)  # no SSM either
    with pytest.raises(ValueError, match="not configured"):
        server.get_owned_games()


def test_env_takes_precedence_over_ssm(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _load(monkeypatch, _creds())
    # If SSM were consulted it would fail the test; env must win and be used verbatim.
    monkeypatch.setattr(server, "_ssm", lambda name: pytest.fail("SSM must not be read"))
    seen: dict[str, str] = {}

    def _capture(url: str) -> dict:
        seen["url"] = url
        return {"response": {"games": []}}

    monkeypatch.setattr(server, "_fetch_json", _capture)
    server.get_owned_games()
    assert "key=test-key" in seen["url"]
    assert "steamid=7656119800000000" in seen["url"]


def test_storefront_tools_are_unauthenticated_and_provenanced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _load(monkeypatch)
    seen: list[tuple[str, dict[str, object]]] = []

    def fake_get(path: str, params: dict[str, object]) -> dict:
        seen.append((path, params))
        if path == "/api/appdetails":
            return {
                "440": {
                    "success": True,
                    "data": {
                        "steam_appid": 440,
                        "name": "Team Fortress 2",
                        "type": "game",
                        "is_free": True,
                        "genres": [{"description": "Action"}],
                        "categories": [{"description": "Multi-player"}],
                    },
                }
            }
        return {"items": [{"id": 440, "name": "Team Fortress 2", "type": "app"}]}

    monkeypatch.setattr(server.storefront, "_get", fake_get)
    details = server.get_store_app_details(440)
    search = server.get_store_search_results("team fortress")
    assert details["provenance"]["plane"] == "storefront"
    assert details["item"]["genres"] == ["Action"]
    assert search["count"] == 1
    assert all("key" not in params and "steamid" not in params for _, params in seen)


def test_storefront_failure_and_input_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _load(monkeypatch)
    monkeypatch.setattr(server.storefront, "_get", lambda path, params: None)
    assert server.get_store_app_details(440)["item"] is None
    with pytest.raises(ValueError, match="positive"):
        server.get_store_app_details(0)
    with pytest.raises(ValueError, match="empty"):
        server.get_store_search_results(" ")


class _FakeSteamClient:
    def __init__(self) -> None:
        self.login_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.wait_for_calls: list[str] = []
        self.wait_until_ready_calls = 0
        self.refresh_token: str | None = "rotated-token"
        self.closed = asyncio.Event()
        self._state = SimpleNamespace(handled_licenses=asyncio.Event())
        self.licenses = [
            SimpleNamespace(id=123, created_at=None, payment_method=SimpleNamespace(value="store"))
        ]

    async def __aenter__(self) -> _FakeSteamClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def login(self, *args: object, **kwargs: object) -> None:
        self.login_calls.append((args, kwargs))
        self._state.handled_licenses.set()
        await self.closed.wait()

    async def wait_until_ready(self) -> None:
        self.wait_until_ready_calls += 1
        return None

    async def wait_for(self, event: str) -> None:
        self.wait_for_calls.append(event)

    async def close(self) -> None:
        self.closed.set()

    def get_app(self, appid: int) -> object:
        return steam.PartialApp(self._state, id=appid)  # type: ignore[arg-type]

    async def fetch_product_info(self, *, apps: list[object]) -> list[object]:
        # steamio does `app.id for app in apps`; a bare int raised AttributeError in prod.
        ids = [app.id for app in apps]  # type: ignore[attr-defined]
        assert ids == [440]
        proto = SimpleNamespace(sha=b"\x01", size=10, change_number=7, appid=440)
        data = {
            "common": {"name": "Team Fortress 2", "type": "game", "oslist": "windows,linux"},
            "extended": {"isfreeapp": "1"},
        }
        return [AppInfo(self._state, data, proto)]  # type: ignore[arg-type]


def test_client_uses_refresh_token_rotates_and_never_returns_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steam_mcp.client import ClientProtocolAdapter

    fake_client = _FakeSteamClient()
    persisted: list[str] = []
    adapter = ClientProtocolAdapter(
        lambda name: "old-token" if name == "client_refresh_token" else None,
        persisted.append,
    )
    monkeypatch.setitem(sys.modules, "steam", SimpleNamespace(Client=lambda: fake_client))
    got = asyncio.run(adapter.product_info(440))
    assert fake_client.login_calls == [((), {"refresh_token": "old-token"})]
    assert persisted == ["rotated-token"]
    assert got["provenance"]["plane"] == "client_pics"
    assert got["item"] == {
        "appid": 440,
        "name": "Team Fortress 2",
        "type": "Game",
        "oslist": ["windows", "linux"],
        "is_free": True,
        "change_number": 7,
    }
    assert "old-token" not in repr(got)
    assert "rotated-token" not in repr(got)


def test_client_waits_only_for_account_licenses(monkeypatch: pytest.MonkeyPatch) -> None:
    from steam_mcp.client import ClientProtocolAdapter

    fake_client = _FakeSteamClient()
    adapter = ClientProtocolAdapter(
        lambda name: "refresh-token" if name == "client_refresh_token" else None,
        lambda token: None,
    )
    monkeypatch.setitem(sys.modules, "steam", SimpleNamespace(Client=lambda: fake_client))

    got = asyncio.run(adapter.licenses())

    assert got["count"] == 1
    assert got["items"][0]["packageid"] == 123
    assert fake_client.wait_until_ready_calls == 0


def test_bootstrap_session_stops_at_login_without_waiting_for_cache() -> None:
    from steam_mcp.client import run_authenticated_session

    fake_client = _FakeSteamClient()

    async def exercise() -> str:
        async def capture_token(client: _FakeSteamClient) -> str:
            return "captured-token"

        return await run_authenticated_session(
            fake_client,
            capture_token,
            ready_timeout=None,
            readiness="login",
            username="account",
            password="credential",
        )

    assert asyncio.run(exercise()) == "captured-token"
    assert fake_client.wait_for_calls == ["login"]
    assert fake_client.wait_until_ready_calls == 0


def test_bootstrap_session_captures_token_before_login_event() -> None:
    from steam_mcp.client import run_authenticated_session

    class TokenIssuingClient(_FakeSteamClient):
        def __init__(self) -> None:
            super().__init__()
            self.refresh_token = None

        async def login(self, *args: object, **kwargs: object) -> None:
            self.login_calls.append((args, kwargs))
            self.refresh_token = "issued-token"
            await self.closed.wait()

    fake_client = TokenIssuingClient()

    async def exercise() -> str:
        async def capture_token(client: TokenIssuingClient) -> str:
            assert client.refresh_token is not None
            return client.refresh_token

        return await run_authenticated_session(
            fake_client,
            capture_token,
            ready_timeout=None,
            readiness="refresh_token",
            username="account",
            password="credential",
        )

    assert asyncio.run(exercise()) == "issued-token"
    assert fake_client.wait_for_calls == []
    assert fake_client.wait_until_ready_calls == 0


def test_client_requires_operator_bootstrap_without_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steam_mcp.client import ClientProtocolAdapter

    fake_client = _FakeSteamClient()
    adapter = ClientProtocolAdapter(lambda name: None, lambda token: None)
    monkeypatch.setitem(sys.modules, "steam", SimpleNamespace(Client=lambda: fake_client))
    with pytest.raises(RuntimeError, match="manual bootstrap"):
        asyncio.run(adapter.licenses())
    assert fake_client.login_calls == []


def test_client_rejects_an_invalid_refresh_token_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steam_mcp.client import ClientProtocolAdapter

    class ExpiringClient(_FakeSteamClient):
        async def login(self, *args: object, **kwargs: object) -> None:
            self.login_calls.append((args, kwargs))
            if kwargs.get("refresh_token"):
                raise RuntimeError("expired token")

    fake_client = ExpiringClient()
    adapter = ClientProtocolAdapter(
        lambda name: "expired-token" if name == "client_refresh_token" else None,
        lambda token: pytest.fail("an invalid token must not be persisted"),
    )
    monkeypatch.setitem(sys.modules, "steam", SimpleNamespace(Client=lambda: fake_client))
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(adapter.licenses())
    assert fake_client.login_calls == [((), {"refresh_token": "expired-token"})]
    assert "expired-token" not in str(exc_info.value)


def test_client_failure_does_not_surface_credential_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steam_mcp.client import ClientProtocolAdapter

    class RejectingClient(_FakeSteamClient):
        async def login(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("password=super-secret")

    adapter = ClientProtocolAdapter(
        lambda name: "super-secret",
        lambda x: None,
    )
    monkeypatch.setitem(sys.modules, "steam", SimpleNamespace(Client=RejectingClient))
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(adapter.licenses())
    assert "super-secret" not in str(exc_info.value)


def test_client_names_the_failure_inside_a_steamio_task_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logging

    from steam_mcp.client import ClientProtocolAdapter

    class ConnectionClosed(Exception):  # noqa: N818 - steamio's own name
        pass

    class ClosingClient(_FakeSteamClient):
        async def login(self, *args: object, **kwargs: object) -> None:
            logging.getLogger("steam.gateway").debug(
                "Failed to login with result: %r", "<Result.AccessDenied: 15>"
            )
            raise ExceptionGroup(
                "unhandled errors in a TaskGroup",
                [ExceptionGroup("inner", [ConnectionClosed("wss://cm?token=live-token")])],
            )

    adapter = ClientProtocolAdapter(
        lambda name: "live-token" if name == "client_refresh_token" else None,
        lambda token: pytest.fail("a failed login must not persist a token"),
    )
    monkeypatch.setitem(sys.modules, "steam", SimpleNamespace(Client=ClosingClient))
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(adapter.product_info(382310))
    message = str(exc_info.value)
    assert "ConnectionClosed" in message
    assert "TaskGroup" not in message
    assert "live-token" not in message


def test_close_reason_capture_keeps_steamio_result_and_restores_logger() -> None:
    import logging

    from steam_mcp.client import _capture_close_reason

    gateway = logging.getLogger("steam.gateway")
    before = (gateway.level, list(gateway.handlers))
    with _capture_close_reason() as reasons:
        gateway.debug("Failed to login with result: %r", "<Result.AccessDenied: 15>")
        gateway.debug("Received %d servers from WebAPI", 3)
    assert reasons == ["Failed to login with result: '<Result.AccessDenied: 15>'"]
    assert (gateway.level, list(gateway.handlers)) == before


def test_refresh_token_persistence_keeps_value_out_of_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    from steam_mcp.client import persist_refresh_token

    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("steam_mcp.client.subprocess.run", fake_run)
    persist_refresh_token("/steam/client-refresh-token", "rotated-token")
    assert "rotated-token" not in repr(captured["args"])
    assert "rotated-token" in str(captured["input"])


def test_bootstrap_persistence_uses_aosguard_file_source_and_removes_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steam_mcp.bootstrap import _write_refresh_token

    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        value_arg = args[args.index("--value") + 1]
        token_path = value_arg.removeprefix("file://")
        captured["token_path"] = token_path
        captured["value"] = Path(token_path).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("steam_mcp.bootstrap.subprocess.run", fake_run)
    _write_refresh_token("bootstrap-token")
    args = captured["args"]
    assert isinstance(args, list)
    assert args[:5] == ["aosguard", "ops", "aws", "ssm", "put-parameter"]
    assert "bootstrap-token" not in repr(args)
    assert captured["value"] == "bootstrap-token"
    assert not Path(str(captured["token_path"])).exists()


def test_bootstrap_failure_reports_only_exception_class(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from steam_mcp import bootstrap

    async def reject() -> None:
        raise RuntimeError("password=credential-material")

    monkeypatch.setattr(bootstrap, "_bootstrap", reject)
    with pytest.raises(SystemExit) as exc_info:
        bootstrap.main()
    assert "RuntimeError" in str(exc_info.value)
    assert "credential-material" not in str(exc_info.value)
    assert "credential-material" not in capsys.readouterr().err


def test_bootstrap_import_failure_reports_only_module_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steam_mcp import bootstrap

    async def reject() -> None:
        error = ImportError("credential-material")
        error.name = "safe_dependency"
        raise error

    monkeypatch.setattr(bootstrap, "_bootstrap", reject)
    with pytest.raises(SystemExit) as exc_info:
        bootstrap.main()
    assert "ImportError: safe_dependency" in str(exc_info.value)
    assert "credential-material" not in str(exc_info.value)


def test_bootstrap_input_ignores_completion_after_auth_method_is_cancelled() -> None:
    from steam_mcp.bootstrap import _set_result_if_pending

    async def exercise() -> None:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        future.cancel()
        _set_result_if_pending(future, "unused-code")
        assert future.cancelled()

    asyncio.run(exercise())


def test_initialize_response_carries_steam_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server's `initialize` response advertises the Steam brand icon.

    Clients that render server icons (the claude.ai connector tile) read
    `serverInfo.icons` off the initialize result, which the low-level server
    builds verbatim from `create_initialization_options().icons`. Assert the
    Steam mark is there as a self-contained SVG data URI, so the tile renders the
    logo instead of a generic placeholder.
    """
    server = _load(monkeypatch)
    icons = server.mcp._mcp_server.create_initialization_options().icons
    assert icons, "expected serverInfo to advertise at least one icon"
    icon = icons[0]
    assert icon.mimeType == "image/svg+xml"
    assert icon.src.startswith("data:image/svg+xml;base64,")
    decoded = base64.b64decode(icon.src.split(",", 1)[1]).decode()
    assert "<title>Steam</title>" in decoded


def test_no_write_tools_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The read-only invariant: every registered tool reads, none can act."""
    server = _load(monkeypatch)
    names = [t.name for t in server.mcp._tool_manager.list_tools()]
    assert names, "expected tools to be registered"
    # The action verb is the leading underscore-delimited token; every tool must
    # read (`get_`), never act. A write tool would lead with buy/trade/refund/...
    forbidden_verbs = {"buy", "trade", "refund", "post", "purchase", "delete", "send", "set"}
    for n in names:
        verb = n.split("_", 1)[0]
        assert verb == "get", f"non-read tool {n!r} registered"
        assert verb not in forbidden_verbs
