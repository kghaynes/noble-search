"""Closed postings: detection rules, safety guards, reopening, the apply-fast signal, reasons, email."""
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
import closures  # noqa: E402
import notify  # noqa: E402
import search  # noqa: E402

TODAY = date.today()


def d(n):
    return (TODAY - timedelta(days=n)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        app.DB_PATH = os.path.join(tempfile.mkdtemp(), "jobs.db")
        app.init_db()
        search.init(app.db, app._db_lock)
        notify.init(app.db, app._db_lock)
        closures.init(app.db, app._db_lock)

    def add(self, company, title, sources, posted=None, last_seen=None, status="", closing="", link=""):
        key = f"{company}|{title}".lower()
        with app.db() as c:
            c.execute("INSERT INTO jobs (job_key, company, title, sources, posted_date, first_seen, last_seen, status, closing_date, link) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", (key, company, title, sources, posted or d(3), posted or d(3),
                                                       last_seen or d(1), status, closing, link))
        return key

    def job(self, key):
        with app.db() as c:
            return dict(c.execute("SELECT * FROM jobs WHERE job_key=?", (key,)).fetchone())

    OK = {"ok": True, "scanned": 100}

    def read(self, seen, name="Acme", kind="board", fp="u|workday|30", st=None):
        return closures.detect(name, kind, st or self.OK, seen, fp, 30, run_id=7)


class Detect(Base):
    def test_first_read_learns_only(self):
        k = self.add("Acme", "Director IT", "Acme careers (Workday)")
        self.assertEqual(self.read(["Other job"] * 1), ([], "first read (or first since its settings changed)"))
        self.assertEqual(self.job(k)["closed_on"], "")

    def test_employer_rules(self):
        gone = self.add("Acme", "Director IT", "Acme careers (Workday)")
        renamed_rule = self.add("Acme", "VP Cyber", "Acme careers (Workday)")        # still listed; rules changed
        seen_today = self.add("Acme", "Chief of Staff", "Acme careers (Workday)", last_seen=TODAY.isoformat())
        too_old = self.add("Acme", "Program Director", "Acme careers (Workday)", posted=d(40))
        board_only = self.add("Acme", "CIO", "JSearch (LinkedIn)")
        self.read(["VP Cyber", "x", "y"])                 # baseline
        closed, _ = self.read(["VP  cyber", "x", "y"])    # whitespace/case don't matter
        self.assertEqual(closed, [gone])
        j = self.job(gone)
        self.assertEqual((j["closed_on"], j["closed_by"], j["open_status"]), (TODAY.isoformat(), "employer", "Closed"))
        for k in (renamed_rule, seen_today, too_old, board_only):
            self.assertEqual(self.job(k)["closed_on"], "", k)
        with app.db() as c:
            row = dict(c.execute("SELECT * FROM closures").fetchone())
        self.assertEqual((row["days_up"], row["detected_by"], row["run_id"]), (3, "employer", 7))

    def test_guards(self):
        k = self.add("Acme", "Director IT", "Acme careers (Workday)")
        self.read(["a"] * 10)
        self.assertEqual(self.read(["a"] * 10, st={"ok": False})[0], [])                       # read failed
        self.assertEqual(self.read(["a"] * 10, st={"ok": True, "skipped": "Turned off"})[0], [])
        self.assertIn("only 4 listings", self.read(["a"] * 4)[1])                               # suspiciously few
        self.assertIn("settings changed", self.read(["a"] * 10, fp="new-url|workday|30")[1])    # settings changed
        self.assertEqual(self.read(["a"] * 10, kind="jsearch")[0], [])                         # job boards never
        self.assertEqual(self.job(k)["closed_on"], "")
        self.assertEqual(self.read(["a"] * 10, fp="new-url|workday|30")[0], [k])                # next day it counts

    def test_usajobs(self):
        listed = self.add("NASA", "Deputy CIO (GS-15)", "USAJOBS")
        early = self.add("DHS", "IT Director (ES-00)", "USAJOBS", closing=(TODAY + timedelta(days=12)).isoformat())
        self.read(["Deputy CIO", "z"], name="USAJOBS", kind="usajobs", fp="f")
        closed, _ = self.read(["Deputy CIO", "z"], name="USAJOBS", kind="usajobs", fp="f")
        self.assertEqual(closed, [early])
        self.assertEqual(self.job(listed)["closed_on"], "")
        with app.db() as c:
            self.assertEqual(tuple(c.execute("SELECT early_days, detected_by FROM closures").fetchone()), (12, "usajobs"))


class Reopen(Base):
    def test_repost_reopens_and_manual_close_holds(self):
        k = self.add("Acme", "Director IT", "Acme careers (Workday)")
        self.read(["a"]); self.read(["a"])
        self.assertTrue(self.job(k)["closed_on"])
        row = {"company": "Acme", "title": "Director IT", "sources": "Acme careers (Workday)"}
        search.merge_rows([row], app.LISTING_FIELDS)
        self.assertEqual(self.job(k)["closed_on"], "")
        with app.db() as c:
            self.assertEqual(c.execute("SELECT reopened_on FROM closures").fetchone()[0], TODAY.isoformat())
        # you closed it: a job board still showing it doesn't reopen it, the employer's site does
        closures.set_closed(k, True)
        search.merge_rows([dict(row, sources="JSearch (LinkedIn)")], app.LISTING_FIELDS)
        self.assertEqual(self.job(k)["closed_by"], "you")
        search.merge_rows([row], app.LISTING_FIELDS)
        self.assertEqual(self.job(k)["closed_on"], "")

    def test_manual_buttons(self):
        k = self.add("Acme", "Director IT", "JSearch (Indeed)")
        closures.set_closed(k, True)
        self.assertEqual(self.job(k)["closed_by"], "you")
        closures.set_closed(k, False)
        self.assertEqual(self.job(k)["closed_on"], "")
        with self.assertRaises(KeyError):
            closures.set_closed("nope|nope", True)


class Signal(Base):
    def test_speed_needs_five_and_ten_days(self):
        with app.db() as c:
            for n in (3, 4, 5, 6, 30):
                c.execute("INSERT INTO closures (company, days_up, reopened_on) VALUES ('L3Harris', ?, '')", (n,))
            c.execute("INSERT INTO closures (company, days_up, reopened_on) VALUES ('L3Harris', 1, ?)", (TODAY.isoformat(),))  # repost: ignored
            for n in (2, 2, 2, 2):
                c.execute("INSERT INTO closures (company, days_up, reopened_on) VALUES ('RTX', ?, '')", (n,))
        sp = closures.employer_speed()
        self.assertEqual(sp["l3harris"], {"n": 5, "median": 5})
        self.assertEqual(closures.fast_days("L3HARRIS", sp), 5)
        self.assertIsNone(closures.fast_days("RTX", sp))          # only 4 closures
        self.assertEqual(closures.summary()["employers"], [["L3Harris", 5, 5]])

    def test_list_jobs_fields(self):
        k = self.add("Acme", "Director IT", "JSearch (Indeed)", posted=d(4), closing=(TODAY + timedelta(days=9)).isoformat())
        closures.set_closed(k, True)
        j = next(x for x in app.list_jobs() if x["job_key"] == k)
        self.assertEqual((j["days_up"], j["early_days"], j["closed_how"]), (4, 9, "You marked it closed"))


class LinkCheck(Base):
    def test_only_open_watchlist_jobs(self):
        w = self.add("Acme", "A", "x", status="applied", link="https://jobs.example.com/1")
        inbox = self.add("Acme", "B", "x", link="https://jobs.example.com/2")
        closed = closures.link_check(run_id=3, checker=lambda u: True)
        self.assertEqual(closed, [w])
        self.assertEqual(self.job(w)["closed_by"], "link")
        self.assertEqual(self.job(inbox)["closed_on"], "")

    def test_never_checks_boards_or_private_hosts(self):
        self.assertFalse(closures.link_gone("https://www.linkedin.com/jobs/view/123"))
        self.assertFalse(closures.link_gone("http://127.0.0.1:8094/x"))
        self.assertFalse(closures.link_gone("file:///etc/passwd"))


class Passes(Base):
    def test_reasons_only_from_watchlist(self):
        w = self.add("Acme", "A", "x", status="interested")
        inbox = self.add("Acme", "B", "x")
        app.update_job(w, {"status": "dismissed", "reasons": ["salary", "location", "bogus"], "reason_note": "too far"})
        app.update_job(inbox, {"status": "dismissed", "reasons": ["salary"]})
        with app.db() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM passes")]
        self.assertEqual(len(rows), 1)
        self.assertEqual((json.loads(rows[0]["reasons"]), rows[0]["note"], rows[0]["status_before"]),
                         (["salary", "location"], "too far", "interested"))
        self.assertEqual(closures.summary()["reasons"], {"salary": 1, "location": 1})


class Email(Base):
    def test_closed_box_and_fast_chip(self):
        applied = self.add("Acme", "Director IT", "Acme careers (Workday)", status="applied")
        self.add("Acme", "VP Ops", "Acme careers (Workday)")
        self.read(["a"]); self.read(["a"])          # both close in run 7
        new = self.add("Fastco", "CIO", "Fastco careers (Greenhouse)")
        with app.db() as c:
            c.execute("UPDATE jobs SET fit='High' WHERE job_key=?", (new,))
            for n in (2, 3, 4, 5, 6):
                c.execute("INSERT INTO closures (company, days_up, reopened_on) VALUES ('Fastco', ?, '')", (n,))
        subject, text, html, _ = notify.build({"id": 7, "added_keys": [new], "detail": []}, {"dashboard_url": ""})
        self.assertIn("Closed since the last search (your watchlist)", text)
        self.assertIn("Director IT — Acme (applied): likely filled", text)
        self.assertIn("1 other posting in your dashboard closed", text)
        self.assertIn("⚡ Fastco usually closes in ~4 days — apply soon.", text)
        self.assertIn("Closed since the last search", html)
        self.assertNotIn(applied, html)


if __name__ == "__main__":
    unittest.main()


class GreenhouseRedirect(unittest.TestCase):
    def _fake(self, final):
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def geturl(s): return final
        return lambda req, timeout=15: R()

    def test_only_real_greenhouse_host(self):
        import urllib.request
        old_open, old_host = urllib.request.urlopen, closures._public_host
        closures._public_host = lambda u: True
        try:
            for final, want in (("https://boards.greenhouse.io/acme?error=true", True),
                                ("https://evil.example/?x=greenhouse.io&error=true", False),
                                ("https://greenhouse.io.evil.example/?error=true", False),
                                ("https://boards.greenhouse.io/acme/jobs/1", False)):
                urllib.request.urlopen = self._fake(final)
                self.assertEqual(closures.link_gone("https://jobs.example.com/1"), want, final)
        finally:
            urllib.request.urlopen, closures._public_host = old_open, old_host
