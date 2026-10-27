import importlib
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


def _load_app(env):
    # ENV is read at import (docs) and per request (HSTS), so it stays set for the whole test.
    patcher = patch.dict(os.environ, {"ENV": env})
    patcher.start()
    import main
    app = importlib.reload(main).app
    return app, patcher


class ProductionExposureTests(unittest.TestCase):
    def test_docs_and_schema_are_hidden_outside_development(self):
        for env in ("production", "test", ""):
            app, env_patch = _load_app(env)
            self.addCleanup(env_patch.stop)
            client = TestClient(app)
            for path in ("/docs", "/redoc", "/openapi.json"):
                self.assertEqual(client.get(path).status_code, 404, (env, path))

    def test_docs_stay_available_in_development(self):
        app, env_patch = _load_app("development")
        self.addCleanup(env_patch.stop)
        client = TestClient(app)
        self.assertEqual(client.get("/openapi.json").status_code, 200)

    def test_hsts_only_on_https_outside_development(self):
        app, env_patch = _load_app("production")
        client = TestClient(app)
        self.assertIn("max-age=", client.get("/health", headers={"x-forwarded-proto": "https"}).headers.get("strict-transport-security", ""))
        self.assertNotIn("strict-transport-security", client.get("/health").headers)
        env_patch.stop()
        app, env_patch = _load_app("development")
        self.addCleanup(env_patch.stop)
        dev = TestClient(app)
        self.assertNotIn("strict-transport-security", dev.get("/health", headers={"x-forwarded-proto": "https"}).headers)


if __name__ == "__main__":
    unittest.main()
