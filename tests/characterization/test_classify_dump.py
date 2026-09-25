"""The classifier verdict for every row of the fixture tree matches the committed dump."""

import importlib.util
import sys

from tests.characterization._snapshot import HERE, updating
from tests.conftest import REPO_ROOT

TREE = HERE / "classify_tree"


def _load_tool():
    path = REPO_ROOT / "tools" / "classify_dump.py"
    spec = importlib.util.spec_from_file_location("_nzbpostarr_classify_dump", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_classify_dump_matches_committed_dump() -> None:
    tool = _load_tool()
    mode = "--write" if updating() else "--check"

    assert tool.main([str(TREE), mode]) == 0
