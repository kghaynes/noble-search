import unittest

import fit
import llm

PROFILE = {"levels": "Director"}
TWO = [("Cyber Leader.docx", "Kenneth Haynes\nCyber operations leader.  30 years."), ("PM.docx", "Program manager\nPMP.")]


class Prompt(unittest.TestCase):
    def test_list_only_with_two_or_more(self):
        one = fit.system_prompt(PROFILE, "inventory", TWO[:1])
        self.assertNotIn(fit.BASE_RESUMES_HEADER, one)
        two = fit.system_prompt(PROFILE, "inventory", TWO)
        self.assertIn(fit.BASE_RESUMES_HEADER + "\n" + fit.BASE_RESUMES_ASK, two)
        self.assertIn("- Cyber Leader.docx: Kenneth Haynes Cyber operations leader. 30 years.", two)


class Reply(unittest.TestCase):
    job = {"description": "Lead security."}

    def test_best_resume_kept_only_when_it_names_one(self):
        raw = '{"fit": "High", "lane": "Cyber", "reason": "Fits.", "best_resume": "cyber leader.docx", "best_resume_why": "  Closest   to the role. "}'
        r = fit.parse_reply(raw, self.job, [n for n, _ in TWO])
        self.assertEqual((r["best_resume"], r["best_resume_why"]), ("Cyber Leader.docx", "Closest to the role."))
        r = fit.parse_reply(raw.replace("cyber leader.docx", "Other.docx"), self.job, [n for n, _ in TWO])
        self.assertEqual((r["best_resume"], r["best_resume_why"]), ("", ""))
        r = fit.parse_reply(raw, self.job, ["Cyber Leader.docx"])
        self.assertEqual(r["best_resume"], "")

    def test_bad_fit_raises(self):
        with self.assertRaises(llm.LLMError):
            fit.parse_reply('{"fit": "Great"}', self.job, [])


if __name__ == "__main__":
    unittest.main()
