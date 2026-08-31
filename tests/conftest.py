"""Load the harvester as a module for testing.

scripts/ is not a package and the harvester is a single file run directly by
the workflow, so it is loaded by path rather than imported by name. yfinance is
optional at import time (see the try/except at the top of update_intel.py), so
these tests run without the market-data stack installed.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

HARVESTER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "update_intel.py"


@pytest.fixture(scope="session")
def harvester():
    spec = importlib.util.spec_from_file_location("update_intel", HARVESTER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["update_intel"] = module
    spec.loader.exec_module(module)
    return module
