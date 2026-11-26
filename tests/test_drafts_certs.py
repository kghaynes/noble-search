"""Resume and application prompts: 'Certifications held' line and held-credential rule; screening-question cap;
fit backlog order; drafts library. Run: python3 -m unittest discover -s tests"""
import os
import random
import sqlite3
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import apply  # noqa: E402
import career_inventory as ci  # noqa: E402
import closures  # noqa: E402
import drafts  # noqa: E402
import fit  # noqa: E402
import resume  # noqa: E402

INV = ("# Career Inventory\n## Certifications\n- **CISSP** (ISC)2\n- PMP\n"
       "## Gaps / to fill in (questions for the candidate)\n- Is the CISSP current?\n")
PROFILE = {"full_name": "A. Vet", "city_state": "Melbourne, FL", "clearance": "Active TS/SCI", "mil_certs": "Security+ CE"}
JOB = {"title": "CISO", "company": "Acme", "location": "Remote", "work_mode": "Remote"}


class PromptCerts(unittest.TestCase):
    def test_one_implementation(self):
        self.assertIs(fit.certifications_held, ci.certifications_held)
        self.assertIs(drafts.inventory_section, ci.inventory_section)

    def test_resume_line_after_clearance(self):
        _, user = resume.build_resume_prompt(PROFILE, INV, [], JOB, "Requires CISSP.")
        lines = user.split("\n")
        i = lines.index("Clearance: Active TS/SCI")
        self.assertEqual(lines[i + 1], "Certifications held: CISSP (ISC)2; PMP; Security+ CE")
        self.assertIn("## Gaps / to fill in", user)   # the gaps section stays in the drafts' inventory

    def test_application_line_after_clearance(self):
        _, user = apply.build_app_prompt(dict(PROFILE, levels="Director"), INV, [], JOB, "", [])
        lines = user.split("\n")
        i = lines.index("Clearance: Active TS/SCI")
        self.assertEqual(lines[i + 1], "Certifications held: CISSP (ISC)2; PMP; Security+ CE")
        self.assertEqual(lines[i + 2], "Target levels: Director")
        self.assertIn("- Is the CISSP current?", user)

    def test_line_omitted_when_none(self):
        for _, user in (resume.build_resume_prompt({"full_name": "A"}, "## Roles\n- COO", [], JOB, ""),
                        apply.build_app_prompt({"full_name": "A"}, "", [("r.docx", "CISSP")], JOB, "", [])):
            self.assertNotIn("Certifications held", user)

    def test_system_rule(self):
        for system in (resume.RESUME_SYSTEM, apply.APP_SYSTEM):
            self.assertIn('(including the "Certifications held" line) are held', system)
            self.assertIn("Never add one that is not listed.", system)
            self.assertIn('"Gaps / to fill in" section holds open questions', system)
            self.assertIn("Return ONLY a JSON object", system)


class Screening(unittest.TestCase):
    def test_cap_and_cleanup(self):
        text = "\n".join(f" Q{i}? " for i in range(30)) + "\r\n\n"
        got = apply.screening_questions(text)
        self.assertEqual(len(got), apply.SCREENING_MAX_QUESTIONS)
        self.assertEqual(got[0], "Q0?")
        self.assertEqual(apply.screening_questions("a\r\nb c\n\n  \n"), ["a", "b", "c"])
        self.assertEqual(apply.screening_questions(None), [])
        self.assertEqual(apply.screening_questions("x" * 5000), ["x" * 5000])   # no length limit


class Backlog(unittest.TestCase):
    OLD_SQL = """SELECT job_key FROM jobs WHERE COALESCE(fit,'')='' AND COALESCE(status,'')!='dismissed'
                 ORDER BY COALESCE(NULLIF(posted_date,''), last_seen) DESC"""

    def test_matches_old_query(self):
        rnd = random.Random(7)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE jobs (job_key TEXT, fit TEXT, status TEXT, posted_date TEXT, last_seen TEXT)")
        dates = [None, "", "2026-11-01", "2026-11-20", "2026-11-20T08:00:00", "2026-10-05"]
        for i in range(300):
            conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (f"k{i}", rnd.choice([None, "", "High", "Low"]),
                         rnd.choice([None, "", "dismissed", "applied"]), rnd.choice(dates), rnd.choice(dates)))
        old = [r[0] for r in conn.execute(self.OLD_SQL)]
        rows = [dict(r) for r in conn.execute("SELECT job_key, fit, status, posted_date, last_seen FROM jobs ORDER BY rowid")]
        new = fit.backlog(rows)
        self.assertEqual(set(old), set(new))
        key = {r["job_key"]: (r["posted_date"] or None) or r["last_seen"] for r in rows}
        self.assertEqual([key[k] for k in old], [key[k] for k in new])   # same order (ties are unordered in SQL)

    def test_simple(self):
        jobs = [{"job_key": "a", "posted_date": "2026-11-01"}, {"job_key": "b", "posted_date": "", "last_seen": "2026-11-25"},
                {"job_key": "c", "fit": "High", "posted_date": "2026-11-26"}, {"job_key": "d", "status": "dismissed"},
                {"job_key": "e"}, {"job_key": "f", "posted_date": "2026-11-01"}]
        self.assertEqual(fit.backlog(jobs), ["b", "a", "f", "e"])


class Library(unittest.TestCase):
    def test_view(self):
        today = date(2026, 11, 26)
        ds = [{"id": 5, "job_key": "x"}, {"id": 4, "job_key": "y"}, {"id": 3, "job_key": "x"}, {"id": 2, "job_key": "gone"},
              {"id": 1, "job_key": "w"}]
        jobs = {"x": {"title": "X", "company": "C", "status": "", "posted_date": "2026-11-20"},
                "y": {"title": "Y", "company": "C", "status": None, "posted_date": "2026-10-28"},
                "w": {"title": "W", "company": "C", "status": "applied", "posted_date": "2026-10-01"}}
        out = closures.library_view(ds, jobs, today, 30)
        self.assertEqual([e["job_key"] for e in out], ["y", "x", "w"])
        self.assertEqual((out[0]["expires"], out[0]["days_left"], out[0]["kept"], out[0]["status"]), ("2026-11-28", 2, False, ""))
        self.assertEqual([d["id"] for d in out[1]["drafts"]], [5, 3])
        self.assertEqual((out[2]["expires"], out[2]["days_left"], out[2]["kept"]), (None, None, True))


if __name__ == "__main__":
    unittest.main()
