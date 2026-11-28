"""The jobs list rules (spec section 27): listview.py twins of the browser code in static/index.html."""
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import listview as L  # noqa: E402
import sources  # noqa: E402

with open(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"), encoding="utf-8") as _f:
    HTML = _f.read()
NOW = datetime(2026, 11, 28, 9, 0, 0)


def ctx(modes="Hybrid, Remote"):
    return sources.Context({"local_places": "Melbourne, FL\nPalm Bay, FL"}, {"home_location": "Melbourne, FL", "work_modes": modes})


class Search(unittest.TestCase):
    def test_fields_case_and_trim(self):
        j = {"title": "Director of IT", "company": "Acme", "location": "Tampa, FL", "notes": "Call Bob",
             "fit_reason": "Strong match", "clearance": "TS/SCI", "description": "kubernetes"}
        for q in ("director", "  ACME ", "tampa", "call bob", "strong", "ts/sci", ""):
            self.assertTrue(L.passes_search(j, q), q)
        self.assertFalse(L.passes_search(j, "kubernetes"))   # description is not searched
        self.assertFalse(L.passes_search(j, "director acme"))   # one phrase, not separate words
        self.assertTrue(L.passes_search(j, "it acme"))   # fields joined with one space: a phrase can span two

    def test_missing_fields(self):
        self.assertTrue(L.passes_search({"title": None, "company": "Acme"}, "acme"))
        self.assertFalse(L.passes_search({}, "x"))


class Filters(unittest.TestCase):
    def test_fit_unrated_only_any(self):
        self.assertTrue(L.passes_filters({"fit": ""}, {"fit": ""}))
        self.assertFalse(L.passes_filters({"fit": ""}, {"fit": "Low"}))
        self.assertTrue(L.passes_filters({"fit": "Med"}, {"fit": "Med"}))

    def test_where(self):
        self.assertTrue(L.passes_where({"work_mode": "Hybrid", "location": "Remote, US"}, "remote"))
        self.assertFalse(L.passes_where({"work_mode": "Hybrid", "location": "Tampa, FL"}, "remote"))
        self.assertTrue(L.passes_where({"work_mode": "On-site · Relocation required"}, "relocate"))
        self.assertFalse(L.passes_where({"location": "", "near_home": False}, "local"))

    def test_near_home(self):
        c = ctx()
        self.assertTrue(L.near_home({"location": "Palm Bay, FL", "work_mode": "Hybrid"}, c))
        self.assertFalse(L.near_home({"location": "Orlando, FL"}, c))   # not a listed town
        self.assertFalse(L.near_home({"location": "Palm Bay, FL", "work_mode": "Remote"}, c))
        self.assertFalse(L.near_home({"location": ""}, c))
        self.assertFalse(L.near_home({"location": "Palm Bay, FL"}, None))
        self.assertFalse(L.near_home({"location": "Palm Bay, FL"}, ctx("Remote")))   # remote only: near home off

    def test_tabs_and_banner(self):
        jobs = [{"job_key": "a", "status": "", "watch": False, "viewed": 0, "fit": "High"},
                {"job_key": "b", "status": "applied", "watch": True, "viewed": 1, "fit": "Low"},
                {"job_key": "c", "status": "dismissed", "watch": False, "viewed": 0}]
        v = L.view(jobs, {"fit": "High"}, "all", "posted")
        self.assertEqual(v["keys"], ["a"])
        self.assertEqual(v["banner"], "Filters are hiding 2 jobs in this tab.")
        self.assertEqual([t["count"] for t in v["tabs"]], ["1 · 1 new", "0 of 1", "0 of 1", "0 of 1", "1 of 3 · 1 new"])
        self.assertEqual(L.view([], {}, "inbox", "posted")["empty"], "no_jobs")
        self.assertEqual(L.view(jobs, {}, "watch", "posted")["keys"], ["b"])


class Sorts(unittest.TestCase):
    def test_posted_closed_sink_and_ties(self):
        jobs = [{"job_key": "old", "posted_date": "2026-11-01"}, {"job_key": "none", "posted_date": ""},
                {"job_key": "closed", "posted_date": "2026-11-27", "closed_on": "2026-11-27"},
                {"job_key": "newlow", "posted_date": "2026-11-20", "fit": "Low"},
                {"job_key": "newhigh", "posted_date": "2026-11-20", "fit": "High"}]
        self.assertEqual([j["job_key"] for j in L.sort_jobs(jobs, "posted")], ["newhigh", "newlow", "old", "none", "closed"])
        self.assertEqual([j["job_key"] for j in L.sort_jobs(jobs, "fit")], ["newhigh", "newlow", "old", "none", "closed"])

    def test_closing_and_company(self):
        jobs = [{"job_key": "x", "closing_date": ""}, {"job_key": "y", "closing_date": "2026-12-01"},
                {"job_key": "z", "closing_date": "2026-11-30"}]
        self.assertEqual([j["job_key"] for j in L.sort_jobs(jobs, "closing")], ["z", "y", "x"])
        cos = [{"company": c} for c in ("beta", "Alpha", "alpha", "Éclair", "eclair", "10x", "9 Lives", "Zeta")]
        self.assertEqual([j["company"] for j in L.sort_jobs(cos, "company")],
                         ["10x", "9 Lives", "alpha", "Alpha", "beta", "eclair", "Éclair", "Zeta"])


class Card(unittest.TestCase):
    def test_closed_watched(self):
        j = {"title": "CIO", "company": "Acme", "location": "", "closed_on": "2026-11-07", "days_up": 1, "early_days": 3,
             "closed_how": "Removed from USAJOBS", "watch": True, "status": "applied", "viewed": 1, "fit": "High", "active": True}
        c = L.card(j, NOW)
        self.assertEqual(c["pills"][1]["text"], "Closed Nov 7 · was up 1 day · 3 days before its deadline")
        self.assertEqual(c["alert"], "Posting closed — likely filled. Follow up or move on.")
        self.assertEqual(L.card(dict(j, status="interested"), NOW)["alert"], "Closed before you applied.")
        self.assertEqual(c["sub"], "Acme · ")
        self.assertEqual(c["close_button"], "↺ Reopen — it's still up")

    def test_removal_and_closing_soon(self):
        j = {"title": "CIO", "status": "dismissed", "expires": "2026-12-05", "viewed": 0, "closing_date": "2026-12-04",
             "posted_date": "2026-11-27"}
        c = L.card(j, NOW)
        self.assertIn({"kind": "removed", "text": "Removed Dec 5", "highlight": False}, c["meta"])
        self.assertEqual(c["meta"][0]["text"], "Posted 2026-11-27 (1 day ago)")
        self.assertTrue(c["meta"][1]["highlight"])
        self.assertFalse(L.closing_soon({"closing_date": "2026-12-05"}, NOW))
        c = L.card(dict(j, status=""), NOW, draft_count=2)
        self.assertIn({"kind": "removed", "text": "Job and drafts removed Dec 5 — ★ to keep", "highlight": True}, c["meta"])
        self.assertEqual(c["draft_button"], "✎ Resume & apply · 2")
        self.assertFalse(any(m["kind"] == "removed" for m in L.card(dict(j, status=""), NOW)["meta"]))

    def test_fast_and_title_only(self):
        c = L.card({"company": "Acme", "fast_days": 6, "viewed": 1}, NOW, fit_running=True)
        self.assertEqual([p["text"] for p in c["pills"]], ["Rating soon…", "⚡ Usually closes in ~6 days"])
        li = L.card({"linkedin_only": True, "title": "CIO", "company": "Acme", "has_description": False, "viewed": 1}, NOW)
        self.assertEqual(li["pills"][0]["kind"], "title_only")
        self.assertIn("or get a full rating", li["hint"])

    def test_days_ago(self):
        self.assertEqual(L.days_ago("2026-11-28", date(2026, 11, 28)), "today")
        self.assertEqual(L.days_ago("2026-11-18", date(2026, 11, 28)), "10 days ago")
        self.assertEqual(L.days_ago("soon", date(2026, 11, 28)), "")


class InStepWithPage(unittest.TestCase):
    """index.html runs the same rules; these strings must appear there too (keep both in step)."""
    def test_strings_in_index_html(self):
        page = HTML.replace("&amp;", "&").replace("&#9888;", "⚠")
        must = [L.SEARCH_PLACEHOLDER, "[j.title,j.company,j.location,j.notes,j.fit_reason,j.clearance]",
                "/remote/i", "/relocation required/i", "{High:0, Med:1, Low:2}", "??3", "'0000'", "'9999'",
                "(!!x.closed_on - !!y.closed_on)", "x.company.localeCompare(y.company)", "d>=0 && d<=7",
                "T23:59:59", L.NEW_ONLY_LABEL]
        must += [label for _, label in L.FIT_CHOICES + L.WHERE_CHOICES + L.SORT_CHOICES + L.TABS + L.STATUS_BUTTONS]
        must += [v for _, v in L.WHERE_CHOICES + L.SORT_CHOICES if v]
        for k in ("unrated", "unrated_running", "over_window", "posted_none", "linkedin", "likely_filled", "closed_before",
                  "rate", "rerate", "close", "reopen", "empty_watch", "empty_filtered", "title_only"):
            must.append(L.TEXT[k])
        for frag in ("Usually closes in ~", "postings have stayed up a median of", "days before its deadline",
                     "was up ", "Job and drafts removed", " — ★ to keep", "Fit by ", "Filters are hiding ",
                     "LinkedIn doesn’t permit showing this job’s details here. Open it on LinkedIn, and to draft a resume",
                     " or get a full rating", "paste its description into <b>Resume & apply</b>."):
            must.append(frag)
        for s in must:
            self.assertIn(s, page, s)

    def test_server_exclusions(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "app.py"), encoding="utf-8") as f:
            app_src = f.read()
        self.assertIn("listview.decorate(", app_src)


class SelfHostedOnly(unittest.TestCase):
    def test_keys_exist(self):
        import profile_store as ps
        import search
        for k in ps.SELF_HOSTED_ONLY_SETTINGS:
            self.assertTrue(k in ps.SETTINGS_DEFAULTS or k in search.DEFAULTS, k)
        root = os.path.join(os.path.dirname(__file__), "..")
        text = ""
        for fn in ("app.py", "profile_store.py", "search.py", "docker-compose.yml"):
            with open(os.path.join(root, fn), encoding="utf-8") as f:
                text += f.read()
        for k in ps.SELF_HOSTED_ONLY_ENV:
            self.assertIn(k, text)


if __name__ == "__main__":
    unittest.main()
