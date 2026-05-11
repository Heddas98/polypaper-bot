"""Auto-apply the `integration` marker to every test collected under
tests/integration/ (Epic 9 T9.9).

This avoids having to sprinkle `@pytest.mark.integration` on every test
class in the folder. The convention is: **location = marker**.

Running integration-only: `py -3.11 -m pytest -m integration`
Running unit-only:         `py -3.11 -m pytest -m "not integration"`
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        # Only apply to items collected from tests/integration/
        if "tests/integration" in item.nodeid or "tests\\integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)
