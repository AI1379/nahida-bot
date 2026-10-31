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


async def test_compare_and_set_requires_absence_and_rejects_stale_values() -> None:
    import asyncio

    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    repo = SQLitePluginDataRepository(engine)
    try:
        results = await asyncio.gather(
            repo.compare_and_set("plugin", "record", None, {"revision": 1}),
            repo.compare_and_set("plugin", "record", None, {"revision": 2}),
        )
        assert results.count(True) == 1
        current = await repo.get("plugin", "record")
        assert not await repo.compare_and_set("plugin", "record", {}, {"revision": 3})
        assert await repo.compare_and_set("plugin", "record", current, {"revision": 3})
        await repo.set("plugin", "null-value", None)
        assert not await repo.compare_and_set(
            "plugin", "null-value", None, {"revision": 1}
        )
    finally:
        await engine.close()
