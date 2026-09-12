# steam-ops / steam-mcp

A read-only MCP that exposes Kai's Steam library over streamable-HTTP. It reads
Steam through three deliberately separate data-access planes: the Web API,
public storefront endpoints, and an authenticated Steam client/PICS session.

It is a pure-read member of the [coilyco-bridge/deploy#30](https://forgejo.coilysiren.me/coilyco-bridge/deploy) personal-MCP fleet. Its Web API, storefront, and client/PICS adapters remain isolated so the distinct Steam access planes do not blur into one credential shape.

> **The repo is `steam-ops`, but the image and service are `steam-mcp`.** CI
> publishes the private image as
> `forgejo.coilysiren.me/coilyco-gaming/steam-mcp:<full-source-sha>` and verifies
> the remote manifest.

## Superseded: the clipboard scrape

`steam_games_to_yaml.py` and `games.yaml` are the **superseded** predecessor - a manual select-all-copy scrape of the rendered games page. They are **left in place as data, not deleted.** The scrape's own docstring already named the durable path this MCP now wraps: `IPlayerService/GetOwnedGames?include_appinfo=1&include_played_free_games=1` (clean JSON from an API key + steamid64). New work goes through the MCP.

## Tools (all read-only)

- **get_owned_games** - full owned library via `IPlayerService/GetOwnedGames` (`include_appinfo=1&include_played_free_games=1`): appid, name, playtime, last-played.
- **get_recently_played** - last-two-weeks activity via `IPlayerService/GetRecentlyPlayedGames`.

Web API tools retain their `{source, count, items}` response shape, with an
additive `provenance` object identifying the `web_api` / `IPlayerService` plane.

- **get_store_app_details** — public, unauthenticated storefront metadata for a
  single known app id.
- **get_store_search_results** — public, unauthenticated storefront search with a
  normalized small result set.
- **get_pics_product_info** — authenticated Steam client-protocol/PICS metadata
  for one app id.
- **get_account_licenses** — authenticated account package-license metadata;
  access tokens are deliberately excluded.

Every response carries `source` and `provenance.plane`: `web_api`,
`storefront`, or `client_pics`. There is no arbitrary-URL/request tool.

## Read-only by construction

A Web API key over `IPlayerService` **reads** a library - it cannot post, trade, or refund (deploy#30). There is no write tool, and no path that both ingests untrusted content and can act. Like reddit-mcp, it is a **plain outbound-HTTPS reader**: no `hostPID`, `hostNetwork`, or `hostPath`. The auth overlay is added in the deploy repo, not here (deploy#28: the source stays unchanged by the overlay).

## Access and credential model

These are three access planes, not interchangeable credentials:

1. **Web API** — `IPlayerService` requires a Web API key plus SteamID64.
2. **Storefront/community HTTP** — the fixed app-details and search endpoints
   are intentionally unauthenticated. The service sends no Steam account cookie
   or Web API key to them.
3. **Steam client protocol/PICS** — `steamio` (steam.py) logs in as the account
   using a persisted refresh token. It reads PICS and account licenses only.

Authelia protects who can reach this MCP; it is transport access control, not a
Steam data source.

The Web API key and SteamID64 are private and **never** live in the image, the
repo, or a committed config. Each resolves at runtime, server-side: an env var
first and then SSM SecureString via `aws ssm get-parameter --with-decryption`.

- `STEAM_WEB_API_KEY` / SSM `/steam/web-api-key` (SecureString)
- `STEAM_STEAMID64` / SSM `/steam/steam-id-64`

The client/PICS adapter resolves its runtime credential independently, env
first then SSM:

- `STEAM_CLIENT_REFRESH_TOKEN` / `/steam/client-refresh-token` — steady-state
  session credential. When Steam rotates it, the adapter writes the replacement
  SecureString back to that same parameter without putting its value in process
  arguments, logs, exceptions, or MCP results.

### Steam Guard bootstrap

A non-interactive server cannot safely prompt for a code. Before the first
client/PICS rollout, Kai runs this from an interactive operator host with an
AWS admin session:

```sh
just bootstrap-client
```

The command reads the existing `/steam/username` and `/steam/password`
SecureStrings through AOSGuard. If
`/steam/client-guard-shared-secret` is absent, `steamio` prompts Kai for a
one-time Steam Guard code. The command writes only the issued refresh token to
`/steam/client-refresh-token`, using a mode-0600 temporary value file that it
removes immediately. The deployed workload receives only that refresh token.
Account credentials and Guard material never enter its ExternalSecret.

Do not put a password, refresh token, shared secret, or one-time code in an
issue, shell history, committed file, or tool call. Re-run the command when
Steam revokes the persisted session.

Env-first lets the deploy inject via an ExternalSecret without granting the pod `ssm:GetParameter`; the SSM fallback mirrors reddit-mcp's resolver. The secrets never leave the box.

> **Operator prereq:** these two SSM params **already exist** (used by steam-games-cli and the website /now) - the MCP reuses them, no new provisioning: `/steam/web-api-key` (a key from https://steamcommunity.com/dev/apikey) and `/steam/steam-id-64` (the 64-bit steamid).

## Port

Streamable-HTTP on `PORT` (default **9112**), `HOST` (default `0.0.0.0`), endpoint at `/mcp`. Fleet ports: node-stats `9110`, reddit `9111`, steam `9112`.

## Run it locally

```sh
just sync
STEAM_WEB_API_KEY='...' STEAM_STEAMID64='7656119...' just run
```

## Host-side ops

`scripts/sunshine-sync-steam.ps1` rebuilds a [Sunshine](https://github.com/LizardByte/Sunshine) streaming host's app list from that host's installed Steam games, so a Moonlight client shows a launchable tile per game. It reads local `appmanifest_*.acf` and pushes to the local Sunshine web API - no Steam Web API key, no elevation. Client-side only; **not** part of the steam-mcp image. See [docs/FEATURES.md](docs/FEATURES.md).

## Commands

Dev commands are declared in the [`justfile`](justfile). Run them as `just <verb>`.

## See also

- [AGENTS.md](AGENTS.md) - agent operating context for this repo.
- [docs/FEATURES.md](docs/FEATURES.md) - inventory of what ships today.
- [coilyco-flight-deck/reddit-mcp](https://forgejo.coilysiren.me/coilyco-flight-deck/reddit-mcp) - the source pattern this repo mirrors.

Cross-reference convention from [coilysiren/agentic-os#59](https://github.com/coilyco-flight-deck/agentic-os/issues/59).
