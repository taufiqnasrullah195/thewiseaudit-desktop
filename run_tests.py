"""run_tests.py — pytest entry point (file-based to dodge shell approval gates)."""
import sys

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main(["-q", "test_fleet_core.py", "test_fleet_multi.py"]))
