#!/usr/bin/env python3
"""
steam_games_to_yaml.py

Parse the *rendered text* of a Steam games page into YAML.

Source: https://steamcommunity.com/id/<account>/games  -> select-all, copy, paste to a file.
This reads that visible-text dump (NOT the HTML, NOT the Web API).

Usage:
    python3 steam_games_to_yaml.py page.txt > library.yaml
    python3 steam_games_to_yaml.py < page.txt > library.yaml
    pbpaste | python3 steam_games_to_yaml.py            # macOS clipboard

Layout assumptions (break if Steam changes the page):
  - every title is rendered twice in a row (alt text + visible text),
    sometimes concatenated as "FooFoo" then "Foo"
  - playtime line literally starts "TOTAL PLAYED", may append "LAST PLAYED..."
  - achievements are an "ACHIEVEMENTS" line followed by an "N/M" line
  - playtimes under 2h show as "N minutes"; converted here to fractional hours

For a durable feed instead of scraping, use the Steam Web API:
  IPlayerService/GetOwnedGames?include_appinfo=1&include_played_free_games=1
  (needs an API key + your 64-bit steamid; returns clean JSON).
"""
import re
import sys


def load(argv):
    if len(argv) > 1 and argv[1] not in ("-", "/dev/stdin"):
        return open(argv[1], encoding="utf-8").read()
    return sys.stdin.read()


def name_pair(a, b):
    """Return the clean title if (a, b) is a Steam title pair, else None."""
    if a == b:
        return a
    if b and a == b + b:            # concatenated double: "FooFoo", "Foo"
        return b
    return None


def find_start(lines):
    for i, l in enumerate(lines):
        if l.startswith("REMOTE DOWNLOAD TO"):
            return i + 1
    for i, l in enumerate(lines):   # fallback: back up to the first game's name pair
        if l.startswith("TOTAL PLAYED"):
            return max(0, i - 2)
    return 0


def parse(text):
    lines = [l.strip() for l in text.split("\n")]
    lines = [l for l in lines if l]
    # drop site footer if present
    for i, l in enumerate(lines):
        if l.startswith("\u00a9") or l.startswith("(c) 20") or l == "STEAM":
            lines = lines[:i]
            break

    region = lines[find_start(lines):]
    games, i, n = [], 0, len(region)
    while i < n:
        a = region[i]
        b = region[i + 1] if i + 1 < n else ""
        name = name_pair(a, b)
        if name is None:
            i += 1
            continue
        i += 2
        g = {"name": name, "hours": None, "last_played": None,
             "ach_earned": None, "ach_total": None}
        while i < n:
            x = region[i]
            y = region[i + 1] if i + 1 < n else ""
            if name_pair(x, y) is not None:        # next game starts
                break
            if x.startswith("TOTAL PLAYED"):
                m = re.search(r"TOTAL PLAYED([\d,\.]+)\s*(hour|minute)", x)
                if m:
                    val = float(m.group(1).replace(",", ""))
                    if m.group(2) == "minute":
                        val = round(val / 60, 1)
                    g["hours"] = val
                lp = re.search(r"LAST PLAYED(.+)$", x)
                if lp:
                    g["last_played"] = lp.group(1).strip()
            elif x == "ACHIEVEMENTS" and re.fullmatch(r"\d+/\d+", y):
                e, t = y.split("/")
                g["ach_earned"], g["ach_total"] = int(e), int(t)
                i += 1
            i += 1
        games.append(g)
    return games


def yq(s):
    """Quote a scalar only when YAML needs it."""
    if s == "" or s.strip() != s or re.search(r"""[:#\[\]{}&*!|>%@`"']""", s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def to_yaml(games, account="unknown"):
    played = [g for g in games if g["hours"] is not None]
    total_h = round(sum(g["hours"] for g in played), 1)
    out = [
        "steam_library:",
        f"  account: {account}",
        f"  total_games: {len(games)}",
        f"  games_played: {len(played)}",
        f"  games_never_played: {len(games) - len(played)}",
        f"  total_hours: {total_h}",
        "  notes: minutes-level playtimes converted to fractional hours; "
        "last_played omitted when Steam showed none; "
        "achievements omitted when the game has none",
        "  games:",
    ]
    for g in games:
        out.append(f"    - name: {yq(g['name'])}")
        if g["hours"] is not None:
            out.append(f"      hours: {g['hours']}")
        if g["last_played"]:
            out.append(f"      last_played: {yq(g['last_played'])}")
        if g["ach_total"] is not None:
            out.append(f"      achievements: {{ earned: {g['ach_earned']}, "
                       f"total: {g['ach_total']} }}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    account = "coilysiren"
    games = parse(load(sys.argv))
    sys.stdout.write(to_yaml(games, account))
