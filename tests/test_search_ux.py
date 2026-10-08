"""Plain-word matching, job phrases + search plan, run records, settings check, why-not, report card."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

TMP = tempfile.mkdtemp()
os.environ.setdefault("DB_PATH", os.path.join(TMP, "jobs.db"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import insight  # noqa: E402
import profile_store as ps  # noqa: E402
import search  # noqa: E402
import sources  # noqa: E402

PROFILE = {"home_location": "Melbourne, FL", "work_modes": "On-site, Hybrid, Remote"}


def setup_db():
    app.init_db()
    search.init(app.db, app._db_lock)
    insight.init(app.db, app._db_lock)
    search.notify.init(app.db, app._db_lock)
    import fit
    fit.init(app.db, app._db_lock)


class KeepConfig(unittest.TestCase):
    """Restore search.json and the profile after each test."""
    def setUp(self):
        setup_db()
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
            open(search.SEARCH_JSON, "w").write(self._cfg)
        ps.save_profile(self._prof)


class Matching(unittest.TestCase):
    def ok(self, words, title):
        return bool(sources.term_pattern(words).search(sources._norm(title)))

    def test_plurals_and_endings(self):
        self.assertTrue(self.ok("programs", "Director, Program Management"))
        self.assertTrue(self.ok("program", "Senior Manager, Programs"))
        self.assertTrue(self.ok("operations", "Director of Operation"))
        self.assertTrue(self.ok("security", "Securities Director"))
        self.assertTrue(self.ok("engineer", "Head of Engineering"))
        self.assertTrue(self.ok("intern", "Internship Program Director"))
        self.assertFalse(self.ok("intern", "International Programs Director"))
        self.assertFalse(self.ok("director", "Directorate Chief of Staff"))

    def test_short_words_exact(self):
        self.assertTrue(self.ok("it", "Director of IT"))
        self.assertFalse(self.ok("it", "Director with its team"))
        self.assertFalse(self.ok("it", "Digital Transformation Lead"))
        self.assertTrue(self.ok("ai", "AI Program Director"))
        self.assertFalse(self.ok("ai", "Director of Maintenance"))

    def test_prefix_words_and_phrases(self):
        self.assertTrue(self.ok("cyber", "Director, Cybersecurity"))
        self.assertTrue(self.ok("director it", "Director of IT"))
        self.assertTrue(self.ok("information technology", "VP, Information Technology"))
        self.assertTrue(self.ok("head of", "Head of AI Governance"))

    def test_abbreviations_with_periods(self):
        self.assertTrue(self.ok("sr director", "Sr. Director, IT"))
        self.assertTrue(self.ok("sr. manager", "Sr. Manager Cyber"))
        self.assertTrue(self.ok(r"senior manager|sr\.? manager", "Sr. Manager Cyber"))

    def test_patterns_still_work(self):
        self.assertTrue(self.ok(r"\bvp\b|vice president", "VP Operations"))
        self.assertTrue(self.ok(r"manager (3|4|5)\b", "Program Manager 4"))
        self.assertTrue(self.ok("[unbalanced", "[Unbalanced title"))   # bad pattern -> plain words

    def test_explain(self):
        c = sources.Context({"title_include": "director", "title_fields": "it\nprogram", "title_exclude": "sales"}, {})
        self.assertEqual(c.explain_title("Director of Sales Programs")["skip"], "sales")
        self.assertIn("No field word", c.explain_title("Director of Nursing")["reason"])
        self.assertIn("No seniority word", c.explain_title("IT Specialist")["reason"])
        ex = c.explain_title("Director, Program Management")
        self.assertTrue(ex["ok"]); self.assertEqual((ex["seniority"], ex["field"]), ("director", "program"))
        self.assertFalse(c.explain_title("Director of IT (internal candidates only)")["ok"])


class Plan(unittest.TestCase):
    def test_split_old_lines(self):
        ph, ex = sources.split_search_lines("Director IT in {home}\ndirector it remote\nCISO remote\n"
                                            "remote program director\ndirector it in Orlando, FL\n# note")
        self.assertEqual(ph, ["Director IT", "CISO", "program director"])
        self.assertEqual(ex, ["director it in Orlando, FL"])

    def test_split_edge_cases(self):
        ph, ex = sources.split_search_lines("CIO in Orlando, FL remote\nremote sensing director\nmanager in training\n"
                                            "Director IT work from home")
        self.assertEqual(ph, ["sensing director", "manager in training", "Director IT"])
        self.assertIn("CIO in Orlando, FL remote", ex)                 # names a place: run as typed
        self.assertIn("manager in training", ph)                       # "in training" is not a place
        self.assertIn("Director IT", ph)
        self.assertIn("sensing director", ph)                          # leading "remote" is the location word

    def ctx(self, modes="On-site, Hybrid, Remote", home="Melbourne, FL"):
        return sources.Context({"local_places": ""}, {"home_location": home, "work_modes": modes})

    def test_modes_follow_profile(self):
        cfg = {"jsearch_what": "director it\nciso"}
        both = sources.jsearch_plan(cfg, self.ctx())
        self.assertEqual([s["q"] for s in both["searches"]],
                         ["director it in Melbourne, FL", "director it remote", "ciso in Melbourne, FL", "ciso remote"])
        self.assertEqual([s["remote"] for s in both["searches"]], [False, True, False, True])
        self.assertEqual([s["q"] for s in sources.jsearch_plan(cfg, self.ctx("Remote"))["searches"]], ["director it remote", "ciso remote"])
        self.assertEqual(len(sources.jsearch_plan(cfg, self.ctx("On-site"))["searches"]), 2)
        nohome = sources.jsearch_plan(cfg, self.ctx(home=""))
        self.assertTrue(all(s["remote"] for s in nohome["searches"])); self.assertTrue(nohome["notes"])

    def test_rotation_is_even_and_within_budget(self):
        cfg = {"jsearch_what": "\n".join(f"job {i}" for i in range(8)), "jsearch_budget": 200}   # 16 searches
        c = self.ctx()
        p = sources.jsearch_plan(cfg, c, date(2026, 10, 5))
        self.assertTrue(p["rotating"]); self.assertEqual(p["per_run"], 9); self.assertLessEqual(p["monthly"], 200)
        count = {}
        d = date(2026, 10, 5)
        for i in range(56):   # 8 weeks of weekdays
            day = d + timedelta(days=i)
            if day.weekday() < 5:
                for s in sources.jsearch_plan(cfg, c, day)["today"]:
                    count[s["q"]] = count.get(s["q"], 0) + 1
        self.assertEqual(len(count), 16)
        self.assertLessEqual(max(count.values()) - min(count.values()), 1)

    def test_all_fit_no_rotation(self):
        p = sources.jsearch_plan({"jsearch_what": "director it\nciso", "jsearch_budget": 200}, self.ctx())
        self.assertFalse(p["rotating"]); self.assertEqual(len(p["today"]), 4)


class Settings(KeepConfig):
    def test_old_settings_migrate(self):
        json.dump({"jsearch_queries": "Director IT in {home}\nCISO remote\ndirector it in Orlando, FL"}, open(search.SEARCH_JSON, "w"))
        c = search.get_config()
        self.assertEqual(c["jsearch_what"], "Director IT\nCISO")
        self.assertEqual(c["jsearch_queries"], "director it in Orlando, FL")

    def test_first_save_after_upgrade_does_not_double(self):
        json.dump({"jsearch_queries": "director it in {home}\ncio remote"}, open(search.SEARCH_JSON, "w"))
        c = search.save_config({"jsearch_what": "director it\ncio"})          # partial save: phrases only
        self.assertEqual(c["jsearch_what"], "director it\ncio")
        self.assertEqual(c["jsearch_queries"], "")

    def test_save_strips_places(self):
        json.dump({}, open(search.SEARCH_JSON, "w"))
        c = search.save_config({"jsearch_what": "director it remote\nciso in {home}\nvp ops in Tampa, FL", "jsearch_queries": ""})
        self.assertEqual(c["jsearch_what"], "director it\nciso")
        self.assertEqual(c["jsearch_queries"], "vp ops in Tampa, FL")


class JSearchLines(unittest.TestCase):
    def test_per_line_stats(self):
        def job(i, title, remote=False):
            return {"job_id": str(i), "job_title": title, "employer_name": "Acme", "job_city": "Melbourne", "job_state": "FL",
                    "job_country": "US", "job_is_remote": remote, "job_apply_link": f"https://acme.com/{i}",
                    "job_posted_at_datetime_utc": date.today().isoformat()}
        old = sources.http
        sources.http = lambda url, **k: {"data": [job(1, "Director of IT"), job(2, "IT Specialist")]} if "remote" not in url \
            else {"data": [job(3, "Director of IT Operations", True)]}
        try:
            c = sources.Context({"title_include": "director", "title_fields": "it", "local_places": "Melbourne, FL"}, PROFILE)
            rows, st = sources.read_jsearch({"jsearch_api_key": "k", "jsearch_what": "director it"}, c)
        finally:
            sources.http = old
        self.assertEqual([(l["mode"], l["returned"], l["title_ok"], l["kept"]) for l in st["lines"]],
                         [("near home", 2, 1, 1), ("remote", 1, 1, 1)])
        self.assertEqual(sorted(r["_line"] for r in rows), [0, 1])


class RunRecords(KeepConfig):
    def test_run_records_titles_and_reasons(self):
        json.dump({"title_include": "director", "title_fields": "it", "title_exclude": "sales", "local_places": "Melbourne, FL",
                   "boards": [], "jsearch_what": "director it"}, open(search.SEARCH_JSON, "w"))

        def fake_js(cfg, ctx):
            rows = []
            for title, loc in (("Director of IT", "Melbourne, FL"), ("Director of IT", "Boise, ID"),
                               ("Director of Sales", "Melbourne, FL"), ("Director IT Ops", "Austin, TX")):
                if ctx.title_ok(title) and ctx.where(loc, loose_remote=False):
                    rows.append({**sources.make_row(ctx, company="Acme" if loc.startswith("Mel") else "Zed", title=title,
                                                    location=loc, where="local", link="https://x.com", posted=date.today().isoformat()), "_line": 0})
            return rows, {"name": "JSearch (job boards)", "type": "jsearch", "ok": True, "errors": [],
                          "lines": [{"phrase": "director it", "mode": "near home", "q": "q", "returned": 4, "title_ok": 3, "kept": len(rows), "new": 0}]}
        orig = (sources.read_usajobs, sources.read_jsearch, search.notify.after_search)
        sources.read_usajobs = lambda cfg, ctx: ([], {"name": "USAJOBS", "type": "usajobs", "ok": True, "errors": []})
        sources.read_jsearch = fake_js
        search.notify.after_search = lambda *a, **k: None
        try:
            rid = search.run("manual", app.LISTING_FIELDS)
        finally:
            sources.read_usajobs, sources.read_jsearch, search.notify.after_search = orig
        rows = dict(((r[1], r[2]), r[3]) for r in search.recent_titles(1)[0][2])
        self.assertEqual(rows[("Director of IT", "kept")], "Kept")
        self.assertIn("Skip word", rows[("Director of Sales", "title")])
        self.assertIn("Austin, TX", rows[("Director IT Ops", "dropped")])
        js = next(d for d in search.recent_runs(1)[0]["detail"] if d["type"] == "jsearch")
        self.assertEqual(js["lines"][0]["new"], 1)
        rep = insight.line_report()
        self.assertEqual(rep["lines"][0]["phrase"], "director it"); self.assertEqual(rep["lines"][0]["new"], 1)
        with app._db_lock, app.db() as conn:
            conn.execute("DELETE FROM jobs WHERE company IN ('Acme','Zed')")
        self.assertTrue(rid)


class Check(KeepConfig):
    def test_warnings_examples_preview(self):
        json.dump({"title_include": "director\nsenior manager", "title_fields": "cyber\ncybersecurity\nprogram", "title_exclude": "manager",
                   "title_examples": "Director, Program Management\nDirector of Nursing"}, open(search.SEARCH_JSON, "w"))
        r = insight.check({})
        texts = " | ".join(w["text"] for w in r["warnings"])
        self.assertIn("“cybersecurity” is already covered by “cyber”", texts)
        self.assertIn("“manager” also blocks your word “senior manager”", texts)
        ex = {e["title"]: e for e in r["examples"]}
        self.assertFalse(ex["Director of Nursing"]["ok"])
        self.assertEqual(ex["Director of Nursing"]["fixes"][0]["value"], "nursing")
        self.assertTrue(ex["Director, Program Management"]["ok"])      # "manager" doesn't block "management"
        r2 = insight.check({"title_fields": ""})                        # the draft (unsaved) is what's checked
        self.assertTrue({e["title"]: e for e in r2["examples"]}["Director of Nursing"]["ok"])


class WhyNot(KeepConfig):
    def test_steps(self):
        json.dump({"title_include": "director", "title_fields": "it", "title_exclude": "", "local_places": "Melbourne, FL",
                   "jsearch_skip_sites": "dedyn.io"}, open(search.SEARCH_JSON, "w"))
        r = insight.why_not({"link": "https://us.remotejobs.dedyn.io/job/1", "title": "Director of IT", "location": "Remote"})
        link = next(s for s in r["steps"] if s["name"] == "Link")
        self.assertFalse(link["ok"]); self.assertIn("re-posting", link["text"])
        r = insight.why_not({"title": "AI Program Director", "company": "Michael J. Fox Foundation", "location": "Remote"})
        title = next(s for s in r["steps"] if s["name"] == "Title")
        self.assertFalse(title["ok"])
        self.assertIn({"list": "title_fields", "op": "add", "value": "ai", "label": "Add “ai” as a field word"}, title["fixes"])
        seen = next(s for s in r["steps"] if s["name"] == "Seen by a search")
        self.assertEqual(seen["fixes"][0]["list"], "jsearch_what")
        self.assertEqual(seen["fixes"][1]["op"], "employer")
        r = insight.why_not({"title": "Director of IT", "location": "Nashua, NH"})
        loc = next(s for s in r["steps"] if s["name"] == "Location")
        self.assertFalse(loc["ok"]); self.assertEqual(loc["fixes"][0]["value"], "Nashua, NH")
        self.assertTrue(r["verdict"].startswith("Most likely reason"))

    def test_similar_titles(self):
        self.assertFalse(insight._similar("AI Program Director", "IT Program Director"))
        self.assertTrue(insight._similar("Director, Program Management", "Program Management Director"))
        self.assertTrue(insight._similar("Deputy Chief Information Officer", "Deputy Chief Information Officer (ES-00)"))

    def test_dismissed(self):
        with app._db_lock, app.db() as conn:
            conn.execute("INSERT OR REPLACE INTO jobs (job_key, title, company, location, status, posted_date, first_seen, last_seen) "
                         "VALUES ('bae|it program director','IT Program Director','BAE Systems','Remote','dismissed',?,?,?)",
                         (date.today().isoformat(),) * 3)
        try:
            r = insight.why_not({"title": "IT Program Director", "company": "BAE Systems"})
        finally:
            with app._db_lock, app.db() as conn:
                conn.execute("DELETE FROM jobs WHERE job_key='bae|it program director'")
        self.assertFalse(r["steps"][0]["ok"]); self.assertEqual(r["steps"][0]["fixes"][0]["op"], "restore")

    def test_link_lookup_only_public_web(self):
        self.assertFalse(insight._public_url("file:///etc/passwd"))
        self.assertFalse(insight._public_url("http://127.0.0.1:8093/settings"))
        self.assertFalse(insight._public_url("http://192.168.30.38/"))
        self.assertEqual(insight._page_title("file:///etc/hostname"), "")

    def test_needs_title(self):
        old = sources.http
        sources.http = lambda *a, **k: (_ for _ in ()).throw(sources.ReaderError("HTTP 403"))
        try:
            r = insight.why_not({"link": "https://www.linkedin.com/jobs/view/123"})
        finally:
            sources.http = old
        self.assertEqual(r.get("need"), "title")


if __name__ == "__main__":
    unittest.main()
