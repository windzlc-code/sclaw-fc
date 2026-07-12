from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "templates" / "index.html"
RUNTIME = ROOT / "static" / "index-app.js"


class HomeRuntimeBundleTests(unittest.TestCase):
    def test_home_runtime_is_cacheable_static_asset(self):
        source = INDEX.read_text(encoding="utf-8")
        self.assertIn('src="/static/index-app.js?v=', source)
        self.assertTrue(RUNTIME.is_file())
        self.assertIn("function runSclawStartupChecks()", RUNTIME.read_text(encoding="utf-8"))

    def test_dynamic_home_payload_stays_small_and_inline(self):
        source = INDEX.read_text(encoding="utf-8")
        self.assertIn("window.__SCLAW_INDEX_BOOTSTRAP", source)
        self.assertNotIn("function runSclawStartupChecks()", source)


if __name__ == "__main__":
    unittest.main()
