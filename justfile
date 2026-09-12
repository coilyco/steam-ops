# Per-repo task manifest. Run `just` (or `just --list`) to see every verb.
#
# Recipes take trailing arguments directly: `just <verb> a b`, where the
# retired form was `ward exec <verb> -- a b`.
#
# One line of comment per recipe on purpose: just reads only the LAST comment
# line above a recipe, so a wrapped description silently truncates to its tail.
#
# `ward exec` is retired, and so is the `.ward/ward.yaml` that outlived it.

set positional-arguments

# Default target: list every available recipe.
default:
    @just --list --unsorted

# uv lock + uv sync with dev deps.
sync *ARGS:
    @uv sync --group dev "$@"

# Run the pytest suite.
test *ARGS:
    @uv run pytest "$@"

# ruff check + ruff format --check + mypy on src/ and tests/.
lint *ARGS:
    @bash scripts/ward-quality.sh check "$@"

# Apply ruff fixes and formatting in place.
fmt *ARGS:
    @bash scripts/ward-quality.sh format "$@"

# Run all pre-commit hooks against every file.
precommit *ARGS:
    @uv run pre-commit run --all-files "$@"

# Build the steam-mcp docker image locally.
build-docker *ARGS:
    @docker build -t steam-mcp:local . "$@"

# Validate the trusted Forgejo OCI publisher shell contract.
check-publish *ARGS:
    @bash -n scripts/publish-image.sh "$@"

# Run the MCP server locally over streamable-HTTP on :9112.
run *ARGS:
    @uv run steam-mcp "$@"

# Interactively log in to Steam and store the issued client refresh token in SSM.
bootstrap-client *ARGS:
    @uv run python -m steam_mcp.bootstrap "$@"
