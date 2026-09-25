"""Run regression tests without credentials, network access, or application data.

Usage: python backend/run_isolated_tests.py [test_filename_pattern]
Uses unittest from the standard library; no extra test dependency is required.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


def main():
    backend = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="botbulls-tests-") as isolation:
        os.environ.clear()
        os.environ.update({
            "ENV": "test",
            "PYTHON_DOTENV_DISABLED": "1",
            # Public test fixture, never an application encryption key.
            "VAULT_SECRET_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            "BOTBULLS_LOCAL_DB_PATH": str(Path(isolation) / "state.sqlite3"),
        })
        sys.path.insert(0, str(backend))
        os.chdir(isolation)
        with patch("dotenv.load_dotenv", return_value=False), patch(
            "firebase_admin.firestore.client", return_value=Mock()
        ), patch(
            "socket.socket.connect", side_effect=AssertionError("Network disabled in tests")
        ), patch(
            "socket.socket.connect_ex", side_effect=AssertionError("Network disabled in tests")
        ):
            import local_store
            local_store._DATA_DIR = Path(isolation) / "data"
            suite = unittest.defaultTestLoader.discover(
                str(backend / "tests"), pattern=sys.argv[1] if len(sys.argv) > 1 else "test_*.py"
            )
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
