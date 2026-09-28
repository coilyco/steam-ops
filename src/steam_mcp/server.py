"""FastMCP server exposing Kai's Steam library over streamable-HTTP.

Read-only by construction. Each tool calls one `IPlayerService` endpoint of the
Steam Web API and returns clean, normalized JSON records - the durable
replacement for the clipboard scrape this repo used to carry
(`steam_games_to_yaml.py`, now superseded):

- get_owned_games      - the full owned library (appid, name, playtime, last-played)
- get_recently_played  - games played in the last two weeks (recent-activity signal)

A Steam Web API key over `IPlayerService` *reads* a library - it cannot post,
trade, refund, or otherwise act on the account. That is the whole point: this is
the second credential shape in the deploy#30 personal-MCP fleet (a Web API key,
vs reddit-mcp's feed URL), and like reddit-mcp there is deliberately **no write
tool** and no path that both ingests untrusted content and can act.

Credential custody: the key and the steamid64 are private and never live in the
image, the repo, or a committed config. Each is resolved at runtime,
server-side, from an env var first and then from SSM (`aws ssm get-parameter
--with-decryption`), mirroring reddit-mcp's `_ssm` resolver and node-stats'
env-based config. Env wins so the deploy can inject via an ExternalSecret
without granting the pod `ssm:GetParameter`. Both SSM params already exist (used
by steam-games-cli and the website /now) - this MCP reuses them, it does not
introduce them. The secrets never leave the box and are never logged or returned
to callers - only the fetched game records are.
"""

from __future__ import annotations

import base64
import os
import subprocess
from importlib.resources import files
from typing import Any
from urllib.parse import urlencode

import requests
from mcp.server.fastmcp import FastMCP
from mcp.types import Icon

from steam_mcp import storefront
from steam_mcp.client import ClientProtocolAdapter, persist_refresh_token
from steam_mcp.crash import init_crash_reporting

API_BASE = "https://api.steampowered.com"
TIMEOUT = 20


def _steam_icon() -> Icon:
    """The Steam brand mark, embedded as a self-contained data-URI icon.

    Wired into the server's `initialize` response (`serverInfo.icons`) so clients
    that render server icons - the claude.ai connector tile - show the Steam logo
    instead of a generic placeholder. The asset is Valve's official Steam brand
    glyph on a Steam-navy tile, committed at `assets/steam-icon.svg` and read here
    at import time. It is base64'd into a `data:` URI rather than served over HTTP
    so the icon has no external dependency and rides inside the initialize payload
    itself. SVG is scalable, so `sizes=["any"]`.
    """
    svg = files("steam_mcp.assets").joinpath("steam-icon.svg").read_bytes()
    encoded = base64.b64encode(svg).decode("ascii")
    return Icon.model_validate(
        {
            "src": f"data:image/svg+xml;base64,{encoded}",
            "mimeType": "image/svg+xml",
            "sizes": ["any"],
        }
    )


# Each secret is (env var checked first, SSM SecureString param second); env wins
# so the deploy can inject via ExternalSecret without ssm:GetParameter.
SECRETS = {
    "api_key": ("STEAM_WEB_API_KEY", "/steam/web-api-key"),
    "steamid64": ("STEAM_STEAMID64", "/steam/steam-id-64"),
    # Client-protocol credentials are distinct from the Web API key.  The
    # refresh token is the workload's only Steam account credential.
    "client_refresh_token": ("STEAM_CLIENT_REFRESH_TOKEN", "/steam/client-refresh-token"),
}

mcp = FastMCP(
    "steam",
    host=os.environ.get("HOST", "0.0.0.0"),
    port=int(os.environ.get("PORT", "9112")),
    icons=[_steam_icon()],
)

# Cache a rotated token until ExternalSecret refreshes its injected Secret.
# This value is server-only and never returned.
_client_refresh_token_cache: str | None = None


def _ssm(name: str) -> str | None:
    """Fetch a decrypted SSM parameter value. None if unavailable.

    The key and steamid64 are SecureString params read server-side at call time;
    they are never logged or returned to callers. Ported from reddit-mcp's `_ssm`.
    """
    try:
        proc = subprocess.run(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--name",
                name,
                "--with-decryption",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None
    except Exception:
        return None


def _secret(name: str) -> str:
    """Resolve a secret: env var first, then SSM.

    Raises ValueError (surfaced to the caller as a tool error) when neither
    source has the value, so an unconfigured deploy fails loud instead of
    silently making a keyless request that Steam answers with an empty library.
    """
    env_var, ssm_name = SECRETS[name]
    value = os.environ.get(env_var) or _ssm(ssm_name)
    if not value:
        raise ValueError(f"secret {name!r} is not configured (set {env_var} or SSM {ssm_name})")
    return value


def _optional_secret(name: str) -> str | None:
    """Resolve an optional secret without leaking its value or request URL."""
    env_var, ssm_name = SECRETS[name]
    return os.environ.get(env_var) or _ssm(ssm_name)


def _api_url(endpoint: str, extra: dict[str, Any]) -> str:
    """Build a Steam Web API URL, folding in the key + steamid64 + format=json.

    The key rides in the query string (Steam takes no other auth), so this URL
    is itself sensitive - `_fetch_json` never logs it, and it never leaves the
    box. Callers pass only the read-shaping params (include_appinfo, ...).
    """
    params = {
        "key": _secret("api_key"),
        "steamid": _secret("steamid64"),
        "format": "json",
        **extra,
    }
    return f"{API_BASE}/{endpoint}?{urlencode(params)}"


def _fetch_json(url: str) -> dict | None:
    """GET a Steam Web API endpoint. Read-only: this is the only network call, a
    plain outbound GET the key cannot turn into a write. The URL carries the key,
    so it is never logged - only the parsed JSON body flows onward."""
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if not r.ok:
            return None
        return r.json()
    except Exception:
        return None


def _normalize_game(game: dict) -> dict[str, Any]:
    """Flatten one IPlayerService game record into a stable, scrape-faithful shape.

    Keeps the fields the old clipboard scrape produced (name, hours, last-played)
    plus the durable identifiers the API adds (appid, raw minutes, 2-week play).
    Playtimes are minutes in the API; hours are the scrape's headline unit, so we
    surface both.
    """
    forever = game.get("playtime_forever") or 0
    two_weeks = game.get("playtime_2weeks") or 0
    last_played = game.get("rtime_last_played") or 0
    return {
        "appid": game.get("appid"),
        "name": (game.get("name") or "").strip(),
        "playtime_forever_minutes": forever,
        "playtime_forever_hours": round(forever / 60, 1),
        "playtime_2weeks_minutes": two_weeks,
        # Unix epoch seconds of last launch; 0 in the API means "never / hidden",
        # surfaced as None so callers do not read the epoch as a real timestamp.
        "last_played_unix": last_played or None,
    }


def _games(endpoint: str, source: str, extra: dict[str, Any]) -> dict[str, Any]:
    """Call an IPlayerService endpoint and return its normalized game records.

    Steam nests the list under `response.games`; a private profile or an empty
    result yields `response: {}`, which normalizes to an empty list (not an
    error - the request itself succeeded).
    """
    payload = _fetch_json(_api_url(endpoint, extra))
    response = (payload or {}).get("response") or {}
    items = [_normalize_game(g) for g in (response.get("games") or [])]
    return {
        "source": source,
        "provenance": {"plane": "web_api", "interface": "IPlayerService"},
        "count": len(items),
        "items": items,
    }


def get_owned_games() -> dict[str, Any]:
    """Kai's full owned Steam library, with app info and free games included.

    Calls `IPlayerService/GetOwnedGames` with `include_appinfo=1` (so names come
    back, not just appids) and `include_played_free_games=1`. This is the durable
    replacement for the clipboard scrape's fields. Read-only.
    """
    return _games(
        "IPlayerService/GetOwnedGames/v1/",
        "owned_games",
        {"include_appinfo": 1, "include_played_free_games": 1},
    )


def get_recently_played() -> dict[str, Any]:
    """Games Kai has played in the last two weeks - a recent-activity signal.

    Calls `IPlayerService/GetRecentlyPlayedGames`. Cheap and useful alongside the
    full library; the same normalized record shape. Read-only.
    """
    return _games("IPlayerService/GetRecentlyPlayedGames/v1/", "recently_played", {})


def get_store_app_details(appid: int, country_code: str = "US") -> dict[str, Any]:
    """Public metadata for one app via the unauthenticated Steam storefront.

    This is not a Web API or account-client credential path.  It is a fixed,
    read-only storefront endpoint, intentionally without any account cookie.
    """
    return storefront.app_details(appid, country_code)


def get_store_search_results(query: str, country_code: str = "US") -> dict[str, Any]:
    """Public Steam storefront search; returns a small normalized result set."""
    return storefront.search(query, country_code)


def _client_adapter() -> ClientProtocolAdapter:
    """Create a client adapter lazily so Web API/storefront tools stay isolated."""
    refresh_ssm_name = SECRETS["client_refresh_token"][1]

    def client_optional_secret(name: str) -> str | None:
        if name == "client_refresh_token" and _client_refresh_token_cache:
            return _client_refresh_token_cache
        return _optional_secret(name)

    def persist(token: str) -> None:
        global _client_refresh_token_cache
        persist_refresh_token(refresh_ssm_name, token)
        _client_refresh_token_cache = token

    return ClientProtocolAdapter(
        client_optional_secret,
        persist,
    )


async def get_pics_product_info(appid: int) -> dict[str, Any]:
    """Authenticated PICS metadata for one app; refresh-token first, read-only."""
    return await _client_adapter().product_info(appid)


async def get_account_licenses() -> dict[str, Any]:
    """Account-readable Steam package licenses, excluding access tokens."""
    return await _client_adapter().licenses()


# Register each tool without rebinding its name, so the plain callables stay
# directly invokable (tests call them). Every registered tool is a read - keep it so.
for _tool in (
    get_owned_games,
    get_recently_played,
    get_store_app_details,
    get_store_search_results,
    get_pics_product_info,
    get_account_licenses,
):
    mcp.tool()(_tool)


def main() -> None:
    """Run the MCP server over streamable-HTTP (endpoint served at /mcp)."""
    init_crash_reporting()
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
