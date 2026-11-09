"""LinkedIn results: off by default, on only after a recorded confirmation, and then title, company and link only."""
import json
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.environ.setdefault("DB_PATH", os.path.join(TMP, "jobs.db"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import closures  # noqa: E402
import profile_store as ps  # noqa: E402
import resume  # noqa: E402
import search  # noqa: E402
import sources  # noqa: E402

PROFILE = {"home_location": "Melbourne, FL", "work_modes": "On-site, Hybrid, Remote"}
LI = {"job_id": "1", "job_title": "Director of IT", "employer_name": "Acme", "job_publisher": "LinkedIn",
      "job_apply_link": "https://www.linkedin.com/jobs/view/123", "job_is_remote": True,
      "job_description": "SECRET LINKEDIN TEXT", "job_salary_string": "$200k", "job_city": "Austin", "job_state": "TX",
      "job_posted_at_datetime_utc": "2026-11-08T00:00:00Z"}
INDEED = dict(LI, job_id="2", job_title="Director of Cyber", employer_name="Beta", job_publisher="Indeed",
              job_apply_link="https://www.indeed.com/viewjob?jk=9",
              apply_options=[{"apply_link": "https://beta.example/jobs/9", "is_direct": True}])


class Base(unittest.TestCase):
    def setUp(self):
        app.DB_PATH = os.path.join(tempfile.mkdtemp(), "jobs.db")
        app.init_db()
        search.init(app.db, app._db_lock)
        closures.init(app.db, app._db_lock)
        try:
            self._cfg = open(search.SEARCH_JSON).read()
        except FileNotFoundError:
            self._cfg = None
        self._prof = ps.get_profile()
        ps.save_profile(PROFILE)

    def tearDown(self):
        if self._cfg is None:
            if os.path.exists(search.SEARCH_JSON):
                os.remove(search.SEARCH_JSON)
        else:
            with open(search.SEARCH_JSON, "w") as f:
                f.write(self._cfg)
        ps.save_profile(self._prof)

    def run_jsearch(self, on):
        cfg = dict(search.DEFAULTS, jsearch_api_key="k", jsearch_what="director of IT", jsearch_linkedin=on,
                   title_include="director", title_fields="it\ncyber")
        old = sources._jsearch_call
        sources._jsearch_call = lambda *a, **k: {"data": [LI, INDEED]}
        try:
            return sources.read_jsearch(cfg, sources.Context(cfg, PROFILE))
        finally:
            sources._jsearch_call = old


class Reader(Base):
    def test_off_drops_linkedin_before_anything(self):
        rows, st = self.run_jsearch(False)
        self.assertEqual([r["company"] for r in rows], ["Beta"])
        self.assertEqual(st["linkedin_dropped"], 1)
        self.assertIn("LinkedIn result(s) left out", st["note"])

    def test_on_keeps_title_company_link_only(self):
        rows, _ = self.run_jsearch(True)
        li = next(r for r in rows if r["company"] == "Acme")
        self.assertEqual((li["title"], li["linkedin_link"], li["sources"], li["link"]),
                         ("Director of IT", "https://www.linkedin.com/jobs/view/123", "JSearch (LinkedIn)", ""))
        for f in ("location", "work_mode", "salary", "description", "posted_date", "employment_type", "clearance"):
            self.assertEqual(li[f], "", f)
        self.assertNotIn("SECRET", json.dumps(rows[0] if rows[0]["company"] == "Acme" else li))

    def test_merge_into_employer_job_keeps_employer_details(self):
        search.merge_rows([{"company": "Acme", "title": "Director of IT", "sources": "Acme careers (Workday)",
                            "link": "https://acme.example/1", "location": "Melbourne, FL", "salary": "$150k"}], app.LISTING_FIELDS)
        rows, _ = self.run_jsearch(True)
        search.merge_rows([r for r in rows if r["company"] == "Acme"], app.LISTING_FIELDS)
        with app.db() as c:
            j = dict(c.execute("SELECT * FROM jobs WHERE job_key='acme|director of it'").fetchone())
        self.assertEqual((j["link"], j["location"], j["salary"], j["linkedin_link"]),
                         ("https://acme.example/1", "Melbourne, FL", "$150k", "https://www.linkedin.com/jobs/view/123"))


class Toggle(Base):
    def test_off_by_default_and_needs_confirmation(self):
        self.assertFalse(search.DEFAULTS["jsearch_linkedin"])
        with self.assertRaises(ValueError):
            search.set_linkedin(True)
        search.save_config({"jsearch_linkedin": True, "jsearch_linkedin_ack": "fake"})   # the normal Save can't turn it on
        self.assertFalse(search.get_config()["jsearch_linkedin"])
        cfg = search.set_linkedin(True, confirmed=True)
        self.assertTrue(cfg["jsearch_linkedin"])
        self.assertTrue(cfg["jsearch_linkedin_ack"].startswith("20"))
        self.assertFalse(search.set_linkedin(False)["jsearch_linkedin"])


class Display(Base):
    def add(self, key, sources_, status="", link="https://www.linkedin.com/jobs/view/5"):
        company, title = key.split("|")
        with app.db() as c:
            c.execute("INSERT INTO jobs (job_key, company, title, sources, status, link, location, description, salary, "
                      "linkedin_link, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,date('now'),date('now'))",
                      (key, company, title, sources_, status, link, "Austin, TX", "long text", "$1", link))

    def test_list_hides_linkedin_when_off(self):
        self.add("acme|director it", "JSearch (LinkedIn)")
        self.add("beta|director it", "Beta careers (Workday); JSearch (LinkedIn)", link="https://beta.example/1")
        keys = {j["job_key"]: j for j in app.list_jobs()}
        self.assertNotIn("acme|director it", keys)
        self.assertEqual(keys["beta|director it"]["linkedin_link"], "")
        search.set_linkedin(True, confirmed=True)
        keys = {j["job_key"]: j for j in app.list_jobs()}
        self.assertTrue(keys["acme|director it"]["linkedin_only"])
        self.assertFalse(keys["beta|director it"]["linkedin_only"])

    def test_cleanup_once(self):
        self.add("acme|director it", "JSearch (LinkedIn)")
        self.add("gamma|vp it", "JSearch (LinkedIn)", status="applied")
        self.add("beta|director it", "Beta careers (Workday); JSearch (LinkedIn)", link="https://beta.example/1")
        import drafts
        old, deleted = drafts.delete_for_job, []
        drafts.delete_for_job = deleted.append
        try:
            self.assertEqual(app.linkedin_cleanup(), 2)
        finally:
            drafts.delete_for_job = old
        self.assertEqual(deleted, ["acme|director it"])                     # its drafts go too
        with app.db() as c:
            keys = {r["job_key"]: dict(r) for r in c.execute("SELECT * FROM jobs")}
        self.assertNotIn("acme|director it", keys)                         # removed
        g = keys["gamma|vp it"]                                             # watchlist: title, company, link only
        self.assertEqual((g["location"], g["description"], g["salary"], g["link"], g["linkedin_link"]),
                         ("", "", "", "", "https://www.linkedin.com/jobs/view/5"))
        self.assertEqual(keys["beta|director it"]["location"], "Austin, TX")  # not LinkedIn-only: untouched
        self.assertEqual(app.linkedin_cleanup(), 0)                          # runs once

    def test_never_fetches_linkedin(self):
        ok, text, reason = resume.fetch_posting("https://www.linkedin.com/jobs/view/123")
        self.assertFalse(ok)
        self.assertIn("paste it here", reason)


class RateWithDescription(Base):
    def test_pasted_description_saved_and_rerated(self):
        with app.db() as c:
            c.execute("INSERT INTO jobs (job_key, company, title, sources, linkedin_link, fit, first_seen, last_seen) "
                      "VALUES ('acme|director it','Acme','Director IT','JSearch (LinkedIn)','https://www.linkedin.com/jobs/view/1','Med',date('now'),date('now'))")
        search.set_linkedin(True, confirmed=True)
        self.assertFalse(next(j for j in app.list_jobs() if j["job_key"] == "acme|director it")["has_description"])  # → warning pill
        import fit
        calls, old = [], fit.run_async
        fit.run_async = lambda keys=None, provider=None, force=False: calls.append((keys, force))
        try:
            with self.assertRaises(ValueError):
                app.Handler._post_extra(None, "/jobs/rate", {"job_key": "acme|director it", "jd_text": "too short"})
            app.Handler._post_extra(None, "/jobs/rate", {"job_key": "acme|director it", "jd_text": "Lead IT. " * 40})
        finally:
            fit.run_async = old
        self.assertEqual(calls, [(["acme|director it"], True)])
        self.assertTrue(next(j for j in app.list_jobs() if j["job_key"] == "acme|director it")["has_description"])


if __name__ == "__main__":
    unittest.main()
