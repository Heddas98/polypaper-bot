"""Shared pytest fixtures — Heddas 2026-05-06 Wave 22+ mega push.

Tüm test dosyalarına paylaşılan async DB stubs + telegram update/context
helper'lar. Bu sayede her test dosyası kendi mock'unu yazmak zorunda kalmaz.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    import pytest_asyncio

    _ASYNC_FIXTURE = pytest_asyncio.fixture
except ImportError:
    _ASYNC_FIXTURE = pytest.fixture


# ════════════════════════════════════════════════════════════════════════
# Shared async context manager mock — async with db.conn.execute() için
# ════════════════════════════════════════════════════════════════════════
class _AsyncCM:
    """Real async context manager — async with db.conn.execute() chain.

    Coverage v20 RuntimeWarning fix: AsyncMock yerine gerçek __aenter__.
    """

    def __init__(self, fetchone=None, fetchall=None):
        self.cursor = MagicMock()
        self.cursor.fetchone = AsyncMock(return_value=fetchone)
        self.cursor.fetchall = AsyncMock(return_value=fetchall or [])

    async def __aenter__(self):
        return self.cursor

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def db_stub():
    """Realistic async DB stub with proper context manager support."""
    db = MagicMock()
    db.conn = MagicMock()
    db.conn.execute = MagicMock(side_effect=lambda *a, **kw: _AsyncCM())
    db.conn.commit = AsyncMock()
    db.conn.executemany = AsyncMock()
    db.conn.execute_fetchone = AsyncMock(return_value=None)
    db.conn.execute_fetchall = AsyncMock(return_value=[])
    return db
