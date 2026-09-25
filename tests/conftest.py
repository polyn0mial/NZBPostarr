"""Suite-wide fixtures."""

import sys

import pytest


@pytest.fixture(autouse=True)
def _retargeted_patches_are_hit():
    """Fail any test whose patch_hit() sentinel was never called (the patch missed its real owner)."""
    support = sys.modules.get("tests.support")
    if support is not None:
        support.PATCH_SENTINELS.clear()
    yield
    support = sys.modules.get("tests.support")
    if support is None:
        return
    missed = [label for label, hits in support.PATCH_SENTINELS if not hits]
    support.PATCH_SENTINELS.clear()
    assert not missed, f"patched but never called: {missed}"
