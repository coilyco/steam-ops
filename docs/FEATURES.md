# steam-mcp features

Living inventory of what ships here. One image, one process: a FastMCP server
on port 9112, streamable-HTTP at `/mcp`. The repo is `steam-ops`, the image is
`steam-mcp`.

## Tools (all read-only)

- **get_owned_games**, **get_recently_played** - the owned library and the last
  two weeks, replacing the superseded clipboard scrape.
- **get_store_app_details**, **get_store_search_results** - public storefront
  metadata and search, deliberately unauthenticated.
- **get_pics_product_info**, **get_account_licenses** - authenticated
  client-protocol and license surfaces. Shapes: [tool results](tool-results.md).

## Envelope and extras

- **Security** - read-only by construction, credentials never in the image,
  network-gated reach. See [security](security.md).
- **Steam brand icon** - `initialize` advertises `serverInfo.icons`, so a client
  that renders server icons shows Valve's mark.
- **Ops helpers** - `sunshine-sync-steam.ps1` and `steam-airgap.ps1` run on an
  operator's own streaming host, never in the service. See
  [ops helpers](ops-helpers.md).
- **Superseded** - `steam_games_to_yaml.py` and `games.yaml` are the old
  clipboard scrape, kept as historical data, getting no new work.

## Deploy

Every push to canonical `main` publishes and verifies the private image at a
full source SHA. Rollout lives in [deploy](https://forgejo.coilysiren.me/coilyco-bridge/deploy).

## See also

- [../README.md](../README.md) - human-facing intro.
- [../AGENTS.md](../AGENTS.md) - agent operating context.

Cross-reference convention from [coilysiren/agentic-os#59](https://github.com/coilyco-flight-deck/agentic-os/issues/59).
