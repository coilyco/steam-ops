# Clipboard scrape (superseded)

`steam_games_to_yaml.py` parses the rendered text of a Steam games page into
YAML. It is left in place as data and gets no new work. New work goes through
the MCP, which reads the Steam Web API instead.

## Input

Open `https://steamcommunity.com/id/<account>/games`, select all, copy, and
paste into a file. The script reads that visible-text dump, not the HTML and not
the Web API.

- `python3 steam_games_to_yaml.py page.txt > library.yaml`
- `python3 steam_games_to_yaml.py < page.txt > library.yaml`
- `pbpaste | python3 steam_games_to_yaml.py` (macOS clipboard)

## Layout assumptions

These break if Steam changes the page.

- Every title is rendered twice in a row (alt text, then visible text),
  sometimes concatenated as `FooFoo` then `Foo`.
- The playtime line starts with `TOTAL PLAYED` and may append `LAST PLAYED...`.
- Achievements are an `ACHIEVEMENTS` line followed by an `N/M` line.
- Playtimes under 2 hours show as `N minutes` and convert to fractional hours.

## Durable replacement

`IPlayerService/GetOwnedGames?include_appinfo=1&include_played_free_games=1`
needs an API key and a 64-bit steamid, and returns clean JSON. That is the
path the MCP wraps. See [security.md](security.md) for credential custody.
