"""Offline test for fit rating with a stand-in model. Run: python3 -m unittest discover -s tests"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest

TMP = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(TMP, "jobs.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import fit  # noqa: E402
import llm  # noqa: E402
import profile_store as ps  # noqa: E402


class Fit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.init_db(); app.drafts.init(); app.search.init(app.db, app._db_lock); fit.init(app.db, app._db_lock)
        ps.save_inventory("Deputy Commanding Officer (COO), 1st Signal Brigade: 5,000 people, $500M+/yr.")
        ps.save_profile({"lanes": "Tech/Cyber, Operations, Defense prime, Space", "clearance": "TS/SCI"})
        with app.db() as c:
            for k, t, f in [("ngc|director, it", "Director, IT", ""), ("x|vp ops", "VP Ops", "High"), ("y|cio", "CIO", "")]:
                c.execute("INSERT INTO jobs(job_key,title,company,fit,description,status) VALUES(?,?,?,?,?,?)",
                          (k, t, k.split("|")[0], f, "Lead enterprise IT." if "it" in k else "", ""))
            c.execute("UPDATE jobs SET status='dismissed' WHERE job_key='y|cio'")
        cls.calls = []

        def fake(settings, system, user, provider=None, json_mode=True, max_tokens=8000, model=None):
            cls.calls.append((system, user))
            if len(cls.calls) == 1:
                return "not json", "ollama", "qwen"
            return json.dumps({"fit": "medium", "lane": "Tech/Cyber", "reason": "Ran IT for 5,000 people as COO; gap: no DevSecOps.",
                               "meets_basic_quals": "yes"}), "ollama", "qwen3:14b"
        cls._orig = llm.complete
        llm.complete = fake

    @classmethod
    def tearDownClass(cls):
        llm.complete = cls._orig

    def test_rate(self):
        self.assertIn("ngc|director, it", fit.pending_keys())
        self.assertNotIn("y|cio", fit.pending_keys())  # dismissed jobs are skipped
        fit.run(provider="ollama")
        with app.db() as c:
            r = dict(c.execute("SELECT fit, fit_reason, lane, fit_by FROM jobs WHERE job_key='ngc|director, it'").fetchone())
        self.assertEqual(r["fit"], "Med")
        self.assertIn("gap:", r["fit_reason"])
        self.assertEqual(r["lane"], "Tech/Cyber")
        self.assertEqual(r["fit_by"], "Local · qwen3:14b")
        system, user = self.calls[-1]
        self.assertIn("5,000 people", system)
        self.assertIn('"Defense prime"', system)
        self.assertIn("Lead enterprise IT.", user)
        self.assertNotIn("5,000 people", user)  # candidate stays in the reusable system prompt
        self.assertNotIn("ngc|director, it", fit.pending_keys())
        self.assertFalse(fit.status()["running"])


if __name__ == "__main__":
    unittest.main()
