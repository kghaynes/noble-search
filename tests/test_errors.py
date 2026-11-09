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


class CurrentResume(unittest.TestCase):
    def test_tagged_resume_is_followed(self):
        import resume
        _, user = resume.build_resume_prompt({"full_name": "Pat"}, "", [("old.docx", "OLD TEXT"), ("new.docx", "NEW TEXT")],
                                             {"title": "Director"}, "jd", template_name="new.docx")
        self.assertLess(user.index("NEW TEXT"), user.index("OLD TEXT"))
        self.assertIn("# CURRENT RESUME — new.docx (follow its section names", user)
        self.assertIn("# OLDER RESUME — old.docx (use only for facts", user)
        _, user = resume.build_resume_prompt({"full_name": "Pat"}, "", [("a.docx", "A")], {"title": "D"}, "jd")
        self.assertIn("# EXISTING RESUME — a.docx", user)

if __name__ == "__main__":
    unittest.main()


class CutOff(unittest.TestCase):
    def _run(self, replies):
        calls = []

        def fake(settings, system, user, provider=None, json_mode=True, max_tokens=8000, model=None):
            calls.append((max_tokens, "ONLY one complete, valid JSON" in user))
            r = replies[len(calls) - 1]
            if r is None:
                raise llm.LLMCutOff("cut off", '{"a": "half', max_tokens)
            return r, "anthropic", "sonnet"
        old = llm.complete
        llm.complete = fake
        try:
            return drafts._complete_json({}, "sys", "user", "anthropic", "resume", 9), calls
        finally:
            llm.complete = old

    def test_cut_off_gets_twice_the_room(self):
        (data, _, _), calls = self._run([None, '{"a": "ok"}'])
        self.assertEqual(data, {"a": "ok"})
        self.assertEqual(calls, [(16000, False), (32000, False)])   # same prompt, more room
        self.assertTrue(any("cut off" in l and "draft #9" in l for l in applog.tail(20)))

    def test_cut_off_at_largest_limit_says_so(self):
        with self.assertRaises(llm.LLMError) as cm:
            self._run([None, None])
        self.assertIn("too long and got cut off", str(cm.exception))

    def test_bad_json_then_good(self):
        (data, _, _), calls = self._run(['{"a": "x" : }', '{"a": "ok"}'])
        self.assertEqual(data, {"a": "ok"})
        self.assertEqual(calls, [(16000, False), (16000, True)])

    def test_claude_max_tokens_stop_raises_cutoff(self):
        old = llm._post
        llm._post = lambda url, body, headers=None, timeout=900: {"stop_reason": "max_tokens",
                                                                   "content": [{"type": "text", "text": '{"a": "x'}]}
        try:
            with self.assertRaises(llm.LLMCutOff) as cm:
                llm.complete({"anthropic_api_key": "k"}, "s", "u", provider="anthropic", max_tokens=123)
        finally:
            llm._post = old
        self.assertEqual(cm.exception.limit, 123)
        self.assertEqual(cm.exception.text, '{"a": "x')


class PerJobTemplate(unittest.TestCase):
    def test_pick_overrides_default(self):
        import profile_store as ps
        old = ps.resume_names
        ps.resume_names = lambda: ["exec.docx", "pm.docx"]
        try:
            prof = {"template_resume": "exec.docx"}
            self.assertEqual(drafts._template_for({"extra_json": json.dumps({"template": "pm.docx"})}, prof), "pm.docx")
            self.assertEqual(drafts._template_for({"extra_json": "{}"}, prof), "exec.docx")
            # a pick that was since removed falls back to the default
            self.assertEqual(drafts._template_for({"extra_json": json.dumps({"template": "gone.docx"})}, prof), "exec.docx")
            self.assertEqual(drafts.public({"id": 1, "job_key": "k", "kind": "resume", "status": "done", "error": "",
                                            "provider": "anthropic", "model": "m", "created": "", "finished": "",
                                            "extra_json": json.dumps({"template": "pm.docx"})})["template"], "pm.docx")
        finally:
            ps.resume_names = old
