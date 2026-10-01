"""Retention: non-watchlist jobs (dismissed included) are removed after the window, with their drafts."""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

TMP = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(TMP, "jobs.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import drafts  # noqa: E402

D = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731


class Retention(unittest.TestCase):
    def test_purge(self):
        app.init_db(); drafts.init()
        rows = [("old|dismissed", "dismissed", D(40)), ("old|inbox", "", D(35)), ("old|starred", "interested", D(90)),
                ("new|dismissed", "dismissed", D(5)), ("old|undated", "", "")]
        with app.db() as c:
            c.execute("DELETE FROM jobs")
            for k, st, pd in rows:
                c.execute("INSERT INTO jobs(job_key,title,company,status,posted_date,last_seen,first_seen) VALUES(?,?,?,?,?,?,?)",
                          (k, k, "Co", st, pd, D(1) if not pd else pd, D(50)))
        paths = []
        for k in ("old|dismissed", "old|starred"):
            p = os.path.join(TMP, f"{k.replace('|', '_')}.docx")
            open(p, "wb").write(b"x")
            paths.append(p)
            with drafts._db() as c:
                c.execute("INSERT INTO drafts(job_key,kind,status,docx_path,created) VALUES(?,?,?,?,?)", (k, "resume", "done", p, "2026-09-01"))
        visible = {j["job_key"]: j for j in app.list_jobs()}
        self.assertNotIn("old|dismissed", visible)        # dismissed only listed inside the window
        self.assertIn("new|dismissed", visible)
        self.assertEqual(visible["new|dismissed"]["expires"], (date.today() + timedelta(days=26)).isoformat())
        self.assertIsNone(visible["old|starred"]["expires"])
        self.assertEqual(app.purge_expired(), 2)          # old|dismissed + old|inbox
        with app.db() as c:
            left = {r[0] for r in c.execute("SELECT job_key FROM jobs")}
        self.assertEqual(left, {"old|starred", "new|dismissed", "old|undated"})
        self.assertFalse(os.path.exists(paths[0]))        # dismissed job's .docx deleted
        self.assertTrue(os.path.exists(paths[1]))         # watchlist draft kept
        lib = app.drafts_library()
        self.assertEqual([j["job_key"] for j in lib["jobs"]], ["old|starred"])
        self.assertTrue(lib["jobs"][0]["kept"])


if __name__ == "__main__":
    unittest.main()
