# Publishing the image

How a push to `main` became a published `steam-mcp` image.

## The train

`.forgejo/workflows/build-publish.yml` ran the tests, then published to Forgejo
OCI on every push to `main`. It retired with Forgejo on 2026-11-01 (COI-2931),
and no GitHub workflow publishes an image yet.

## No deploy stage, deliberately

Rollout is driven from `coilyco/deploy/services/steam-mcp`, per the layer
invariant that source repositories publish and deploy repositories roll out. A
deploy step here would put one layer in charge of both.

## The name is not the repo

The repository is `steam-ops` and the image it publishes is `steam-mcp`. They do
not match, and the mismatch is deliberate: the repo holds operations for the
Steam surface while the image is the MCP server it ships.
