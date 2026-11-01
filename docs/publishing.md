# Publishing the image

How a push to `main` became a published `steam-mcp` image.

## The train

`.github/workflows/build-publish.yml` runs the tests, then pushes
`ghcr.io/coilyco/steam-mcp:<full-source-sha>` on every push to `main`, with
`GITHUB_TOKEN` and `packages: write` (COI-3102). Forgejo OCI published it until
2026-11-01. The repo is public, so the workflow runs on `ubuntu-latest` and holds
no cluster credentials.

## No deploy stage, deliberately

Rollout is driven from `coilyco/deploy/services/steam-mcp`, per the layer
invariant that source repositories publish and deploy repositories roll out. A
deploy step here would put one layer in charge of both.

## The name is not the repo

The repository is `steam-ops` and the image it publishes is `steam-mcp`. They do
not match, and the mismatch is deliberate: the repo holds operations for the
Steam surface while the image is the MCP server it ships.
