"""Version, update check and About."""
import os
import sys
import tempfile
import unittest
import urllib.error

TMP = tempfile.mkdtemp()
os.environ.setdefault("DB_PATH", os.path.join(TMP, "jobs.db"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import profile_store as ps  # noqa: E402
import updates  # noqa: E402
from version import __version__  # noqa: E402


class Updates(unittest.TestCase):
    def setUp(self):
        app.DB_PATH = os.path.join(tempfile.mkdtemp(), "jobs.db")
        app.init_db()
        updates.init(app.db, app._db_lock)
        self._s = ps.get_settings(include_secret=True)

    def tearDown(self):
        ps.save_settings({"update_check": self._s.get("update_check", True)})

    def test_versions(self):
        self.assertEqual(updates.parse("v0.4.10"), (0, 4, 10))
        self.assertEqual(updates.parse("1.2"), (1, 2, 0))
        self.assertTrue(updates.newer("v0.10.0", "0.9.9"))
        self.assertFalse(updates.newer("v0.4.1", "0.4.1"))
        self.assertFalse(updates.newer("garbage", "0.4.1"))
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_newer_release_banner_and_dismiss(self):
        st = updates.check(force=True, fetch=lambda: {"tag_name": "v99.0.0", "html_url": "https://github.com/x/r/releases/tag/v99.0.0",
                                                       "published_at": "2027-01-01T00:00:00Z"})
        self.assertTrue(st["newer"] and st["show_banner"])
        self.assertEqual((st["latest"], st["published"]), ("99.0.0", "2027-01-01"))
        self.assertFalse(updates.dismiss("99.0.0")["show_banner"])
        st = updates.check(force=True, fetch=lambda: {"tag_name": "v99.0.1", "html_url": "u"})
        self.assertTrue(st["show_banner"])            # a later version shows again

    def test_up_to_date_once_a_day_and_errors(self):
        calls = []
        f = lambda: calls.append(1) or {"tag_name": "v" + __version__, "html_url": "u"}  # noqa: E731
        self.assertFalse(updates.check(force=True, fetch=f)["newer"])
        updates.check(fetch=f)                        # within 24 h: no second request
        self.assertEqual(len(calls), 1)

        def boom():
            raise urllib.error.URLError("offline")
        st = updates.check(force=True, fetch=boom)
        self.assertIn("Couldn't reach GitHub", st["error"])
        self.assertEqual(st["latest"], __version__)   # keeps the last known answer

    def test_off_means_no_request(self):
        ps.save_settings({"update_check": False})
        calls = []
        self.assertFalse(updates.check(fetch=lambda: calls.append(1) or {})["enabled"])
        self.assertEqual(calls, [])

    def test_about(self):
        a = updates.about()
        self.assertEqual(a["support_email"], "support@cybercloudai.tech")
        self.assertEqual(a["links"]["website"], "https://www.cybercloudai.tech/noble-search/")
        self.assertTrue(a["links"]["privacy"].endswith("/PRIVACY.md"))
        for k in ("bug", "employer", "feature"):
            name = a["links"][k].split("template=")[1]
            self.assertTrue(os.path.exists(os.path.join(os.path.dirname(__file__), "..", ".github", "ISSUE_TEMPLATE", name)), name)


if __name__ == "__main__":
    unittest.main()
