"""Pure rules for a job found again (search.merge_job) and LinkedIn helpers (SPEC section 26)."""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "jobs.db"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import search  # noqa: E402
import sources  # noqa: E402

F = app.LISTING_FIELDS
T, CUT = "2026-11-26", "2026-10-27"
OLD = {"company": "Acme", "title": "Director of IT", "location": "Melbourne, FL", "work_mode": "", "employment_type": "",
       "posted_date": "2026-11-01", "closing_date": "2026-12-01", "salary": "$150k", "sources": "Acme careers (Workday)",
       "link": "https://acme.example/1", "fit": "High", "description": "short", "open_status": "Open", "linkedin_link": ""}


def m(existing, row):
    return search.merge_job(existing, row, F, T, CUT)


class MergeJob(unittest.TestCase):
    def test_new_job_added_with_all_fields(self):
        p = m(None, {"company": "Acme ", "title": "Director", "posted_date": "2026-11-20"})
        self.assertEqual((p["action"], p["key"], p["set"]["first_seen"], p["set"]["last_seen"], p["set"]["salary"]),
                         ("add", "acme |director", T, T, ""))
        self.assertEqual(list(p["set"])[:len(F)], F)

    def test_blank_key_and_old_new_job_skipped(self):
        self.assertEqual(m(None, {"company": "", "title": ""})["action"], "skip")
        self.assertEqual(m(None, {"company": "A", "title": "B", "posted_date": "2026-10-26T09:00:00Z"})["action"], "skip")
        self.assertEqual(m(None, {"company": "A", "title": "B", "posted_date": CUT})["action"], "add")
        self.assertEqual(m(dict(OLD, posted_date="2026-01-01"), {"company": "Acme", "title": "Director of IT",
                                                                  "posted_date": "2026-01-01"})["action"], "update")

    def test_field_rules(self):
        row = {"company": "Acme", "title": "Director of IT", "posted_date": "2026-11-20", "closing_date": "2026-12-15",
               "location": "Remote", "work_mode": "Remote", "salary": "$200k", "fit": "Low", "sources": "JSearch (Indeed)",
               "link": "https://www.indeed.com/viewjob?jk=1", "description": "a much longer description"}
        s = m(OLD, row)["set"]
        self.assertNotIn("posted_date", s)
        self.assertNotIn("location", s)
        self.assertNotIn("salary", s)
        self.assertNotIn("fit", s)
        self.assertNotIn("link", s)   # employer link kept
        self.assertEqual((s["closing_date"], s["work_mode"], s["description"], s["sources"], s["last_seen"]),
                         ("2026-12-15", "Remote", "a much longer description", "Acme careers (Workday); JSearch (Indeed)", T))

    def test_aggregator_link_replaced(self):
        p = m(dict(OLD, link="https://www.indeed.com/viewjob?jk=1", sources="JSearch (Indeed)"),
              {"company": "Acme", "title": "Director of IT", "link": "https://acme.example/2", "sources": "JSearch (Indeed)"})
        self.assertEqual(p["set"], {"link": "https://acme.example/2", "last_seen": T})
        self.assertTrue(p["updated"])

    def test_nothing_new_not_updated(self):
        p = m(OLD, {"company": "Acme", "title": "Director of IT", "sources": "Acme careers (Workday)"})
        self.assertEqual((p["set"], p["updated"]), ({"last_seen": T}, False))

    def test_closed_repost_reopens(self):
        ex = dict(OLD, closed_on="2026-11-10", closed_by="employer")
        self.assertTrue(m(ex, {"company": "Acme", "title": "Director of IT", "sources": "JSearch (Indeed)"})["reopen"])


class LinkedIn(unittest.TestCase):
    def test_cleanup_action(self):
        W = app.WATCH_STATUSES
        self.assertEqual(sources.linkedin_cleanup_action({"sources": "Acme careers; JSearch (LinkedIn)"}, W)["action"], "keep")
        self.assertEqual(sources.linkedin_cleanup_action({"sources": "JSearch (LinkedIn)", "status": "dismissed"}, W)["action"], "remove")
        p = sources.linkedin_cleanup_action({"sources": "JSearch (LinkedIn)", "status": "applied", "link": "https://linkedin.com/jobs/view/1"}, W)
        self.assertEqual((p["action"], p["set"]["linkedin_link"], p["set"]["link"], p["set"]["description"]),
                         ("reduce", "https://linkedin.com/jobs/view/1", "", ""))

    def test_view(self):
        li = {"sources": "JSearch (LinkedIn)", "linkedin_link": "https://www.linkedin.com/jobs/view/1"}
        both = {"sources": "Beta careers; JSearch (LinkedIn)", "linkedin_link": "https://www.linkedin.com/jobs/view/2"}
        self.assertIsNone(sources.linkedin_view(li, False))
        self.assertEqual(sources.linkedin_view(both, False)["linkedin_link"], "")
        self.assertTrue(sources.linkedin_view(li, True)["linkedin_only"])
        self.assertEqual(sources.linkedin_view(both, True)["linkedin_link"], both["linkedin_link"])

    def test_paste_message(self):
        self.assertTrue(search.LINKEDIN_PASTE.startswith("LinkedIn does not permit this application to show"))


if __name__ == "__main__":
    unittest.main()
