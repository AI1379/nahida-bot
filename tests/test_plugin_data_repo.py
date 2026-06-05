"""Tests for the plugin data repository."""

from __future__ import annotations

import pytest

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_plugin_data_repo import (
    SQLitePluginDataRepository,
)


@pytest.mark.asyncio
async def test_prefix_matching_treats_like_wildcards_as_literals() -> None:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    repo = SQLitePluginDataRepository(engine)
    try:
        await repo.set("plugin", "server:%literal", {"value": 1})
        await repo.set("plugin", "server:abc", {"value": 2})
        await repo.set("plugin", "server:_literal", {"value": 3})

        percent_matches = await repo.get_by_prefix("plugin", "server:%")
        underscore_matches = await repo.get_by_prefix("plugin", "server:_")

        assert percent_matches == {"server:%literal": {"value": 1}}
        assert underscore_matches == {"server:_literal": {"value": 3}}
    finally:
        await engine.close()
