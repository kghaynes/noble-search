"""Model replies with JSON slips, the draft retry, and the problems log."""
import json
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.environ.setdefault("DB_PATH", os.path.join(TMP, "jobs.db"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import applog  # noqa: E402
import drafts  # noqa: E402
import llm  # noqa: E402


class Repair(unittest.TestCase):
    def test_missing_commas(self):
        bad = '{"summary": "Leads IT"\n "experience": [{"title": "COO"}\n {"title": "CIO"}]\n "skills": ["a" "b"]}'
        self.assertEqual(llm.parse_json(bad), {"summary": "Leads IT", "experience": [{"title": "COO"}, {"title": "CIO"}],
                                               "skills": ["a", "b"]})

    def test_trailing_commas(self):
        self.assertEqual(llm.parse_json('{"a": [1, 2, ], "b": {"c": 3, }, }'), {"a": [1, 2], "b": {"c": 3}})

    def test_long_reply_with_one_slip(self):
        items = ",\n".join(json.dumps({"role": f"Role {i}", "bullets": ["did x", "did y"]}) for i in range(60))
        good = '{"experience": [' + items + '], "headline": "Exec"}'
        bad = good.replace('}, {"role": "Role 41"', '} {"role": "Role 41"').replace(',\n{"role": "Role 41"', '\n{"role": "Role 41"')
        self.assertNotEqual(bad, good)
        self.assertEqual(len(llm.parse_json(bad)["experience"]), 60)

    def test_unfixable_still_errors(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_json('{"a": "unterminated}')
        with self.assertRaises(llm.LLMError):
            llm.parse_json("no json here")


class DraftRetry(unittest.TestCase):
    def test_second_try_after_unreadable_reply(self):
        calls = []

        def fake(settings, system, user, provider=None, json_mode=True, max_tokens=8000, model=None):
            calls.append("ONLY one complete, valid JSON" in user)
            return ('{"a": "x" : }' if len(calls) == 1 else '{"a": "ok"}'), "anthropic", "sonnet"
        old = llm.complete
        llm.complete = fake
        try:
            data, prov, model = drafts._complete_json({}, "sys", "user", "anthropic", "resume", 7)
        finally:
            llm.complete = old
        self.assertEqual(data, {"a": "ok"})
        self.assertEqual(calls, [False, True])
        self.assertTrue(any("resume draft #7" in l for l in applog.tail(20)))
        self.assertTrue(os.path.exists(os.path.join(applog.LOG_DIR, "bad-reply-resume-draft.txt")))


class Log(unittest.TestCase):
    def test_levels_and_secrets(self):
        applog.error("email", "login failed for https://x.test/?token=abc123&key=SECRET")
        applog.info("search", "Manual run done: 3 new")
        lines = applog.tail(5)
        self.assertTrue(any(" ERROR " in l and "[email]" in l for l in lines))
        self.assertFalse(any("abc123" in l or "SECRET" in l for l in lines))
        self.assertTrue(lines[-1].endswith("[search] Manual run done: 3 new"))


if __name__ == "__main__":
    unittest.main()
