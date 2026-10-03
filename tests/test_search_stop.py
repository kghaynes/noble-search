"""Stop search: the run ends after the current source and is marked 'stopped' (no summary email)."""
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.environ.setdefault("DB_PATH", os.path.join(TMP, "jobs.db"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import notify  # noqa: E402
import search  # noqa: E402
import sources  # noqa: E402


class Stop(unittest.TestCase):
    def test_stop_mid_run(self):
        app.init_db(); search.init(app.db, app._db_lock); notify.init(app.db, app._db_lock)
        calls = []
        orig = (sources.read_usajobs, sources.read_jsearch, notify.after_search)

        def usa(cfg, ctx):
            calls.append("usajobs"); self.assertTrue(search.stop())
            return [], {"name": "USAJOBS", "type": "usajobs", "ok": True, "errors": []}

        def js(cfg, ctx):
            calls.append("jsearch")
            return [], {"name": "JSearch (job boards)", "type": "jsearch", "ok": True, "errors": []}
        sources.read_usajobs, sources.read_jsearch = usa, js
        notify.after_search = lambda *a, **k: calls.append("email")
        try:
            run_id = search.run("manual", app.LISTING_FIELDS)
        finally:
            sources.read_usajobs, sources.read_jsearch, notify.after_search = orig
        self.assertEqual(calls, ["usajobs"])
        self.assertEqual(search.recent_runs(1)[0]["status"], "stopped")
        self.assertEqual(search.recent_runs(1)[0]["id"], run_id)
        self.assertFalse(sources.STOP.is_set())
        self.assertFalse(search.stop())   # nothing running now

    def test_http_refuses_when_stopped(self):
        sources.STOP.set()
        try:
            with self.assertRaises(sources.ReaderError):
                sources.http("https://example.com")
        finally:
            sources.STOP.clear()


if __name__ == "__main__":
    unittest.main()
