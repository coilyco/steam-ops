# Publishing the image

How a push to `main` becomes a published `steam-mcp` image.

## The train

`.forgejo/workflows/build-publish.yml` runs the tests, then publishes to Forgejo
OCI on every push to `main`.

## No deploy stage, deliberately

Rollout is driven from `coilyco-bridge/deploy/services/steam-mcp`, per the layer
invariant that source repositories publish and deploy repositories roll out. A
deploy step here would put one layer in charge of both.

## The name is not the repo

The repository is `steam-ops` and the image it publishes is `steam-mcp`. They do
not match, and the mismatch is deliberate: the repo holds operations for the
Steam surface while the image is the MCP server it ships.
