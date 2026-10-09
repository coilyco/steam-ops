"""Keep the real Steam session spacing out of the suite: it would sleep every test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_session_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("steam_mcp.client.MIN_SESSION_GAP", 0.0)
    monkeypatch.setattr("steam_mcp.client._last_session_end", None)
