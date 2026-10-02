"""Offline test for the morning summary email. Run: python3 -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(TMP, "jobs.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import notify  # noqa: E402

SENT = []
FAIL_587 = False


class FakeSMTP:
    def __init__(self, host, port, timeout=30, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        if self.port != 465 and FAIL_587:
            import smtplib
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    def ehlo(self):
        pass

    def login(self, user, pw):
        if pw != "good":
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, msg):
        SENT.append(msg)


class Notify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.init_db(); app.drafts.init(); app.search.init(app.db, app._db_lock); notify.init(app.db, app._db_lock)
        with app.db() as c:
            for k, t, co, f, r in [("a|director it", "Director IT", "A", "High", "Ran IT for 5,000; gap: none."),
                                   ("b|vp ops", "VP Ops", "B", "Med", "COO scope; gap: commercial P&L."),
                                   ("c|cio", "CIO", "C", "Low", "gap: hands-on dev."),
                                   ("d|old", "Old", "D", "High", "x")]:
                c.execute("INSERT INTO jobs(job_key,title,company,fit,fit_reason,link,location,work_mode,posted_date) VALUES(?,?,?,?,?,?,?,?,?)",
                          (k, t, co, f, r, f"https://x/{co}", "Melbourne, FL", "On-site", "2026-09-30"))
            cur = c.execute("INSERT INTO search_runs(started,trigger,status,found,added,updated,detail,added_keys) VALUES(?,?,?,?,?,?,?,?)",
                            ("2026-10-01T09:30:00", "scheduled", "partial", 40, 3, 5,
                             json.dumps([{"name": "Jacobs", "ok": False, "errors": ["bot check"]}]),
                             json.dumps(["a|director it", "b|vp ops", "c|cio"])))
            cls.run_id = cur.lastrowid
        notify.smtplib.SMTP = FakeSMTP
        notify.smtplib.SMTP_SSL = FakeSMTP

    def cfg(self, **kw):
        return {"email_enabled": True, "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_user": "user@example.com",
                "smtp_password": "good", "email_to": "user@example.com", "dashboard_url": "http://localhost:8093", **kw}

    def test_summary(self):
        SENT.clear()
        subject = notify.send_for_run(self.run_id, self.cfg())
        self.assertIn("3 new — 1 High, 1 Med, 1 Low", subject)
        msg = SENT[0]
        body = msg.get_body(("plain",)).get_content()
        self.assertLess(body.index("Director IT"), body.index("VP Ops"))
        self.assertIn("Gap: Commercial P&L", body)
        self.assertIn("Jacobs (bot check)", body)
        self.assertNotIn("Old", body)
        html_part = msg.get_body(("html",)).get_content()
        self.assertIn("https://x/A", html_part)
        self.assertIn("Open your dashboard", html_part)
        self.assertIn("Today's top picks", html_part)

    def test_split_reason(self):
        self.assertEqual(notify.split_reason("Strong match: led IT for 5,000. gap: no EVM. Meets basic quals."),
                         ("Led IT for 5,000.", "No EVM."))
        self.assertEqual(notify.split_reason("Runs ops reviews; fits you."), ("Runs ops reviews; fits you.", ""))

    def test_fallback_465(self):
        global FAIL_587
        FAIL_587 = True
        try:
            self.assertEqual(notify.send_email(self.cfg(), "s", "t", "<p>h</p>"), 465)
        finally:
            FAIL_587 = False

    def test_stage_in_error(self):
        global FAIL_587
        FAIL_587 = True
        try:
            with self.assertRaises(ValueError) as cm:
                notify.send_email(self.cfg(smtp_port=2525), "s", "t", "<p>h</p>")
        finally:
            FAIL_587 = False
        self.assertIn("2525", str(cm.exception))

    def test_bad_password(self):
        with self.assertRaises(ValueError) as cm:
            notify.send_email(self.cfg(smtp_password="nope"), "s", "t", "<p>h</p>")
        self.assertIn("app password", str(cm.exception))

    def test_not_configured(self):
        with self.assertRaises(ValueError):
            notify.send_email({"smtp_host": "smtp.gmail.com"}, "s", "t", "h")


if __name__ == "__main__":
    unittest.main()
