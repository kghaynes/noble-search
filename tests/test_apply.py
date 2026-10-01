"""Offline tests for application text and keyword coverage. Run: python3 -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import apply  # noqa: E402


class Apply(unittest.TestCase):
    def test_normalize(self):
        a = apply.normalize_app({"summary": " Exec  leader ", "work_history": [
            {"job_title": "COO", "company": "Army", "from": "06/2019", "to": "Present", "description": ["Led 5,000", "Ran $500M"]}],
            "skills": ["Cyber", ""], "screening": [{"question": "Salary?", "answer": "[confirm]"}, {"question": "", "answer": "x"}],
            "cover_letter": "Dear team", "why_company": "Because"})
        self.assertEqual(a["summary"], "Exec leader")
        self.assertEqual(a["work_history"][0]["description"], "- Led 5,000\n- Ran $500M")
        self.assertEqual(a["skills"], ["Cyber"])
        self.assertEqual(len(a["screening"]), 1)
        self.assertTrue(a["screening"][0]["verify"])
        self.assertIn("COVER LETTER", apply.app_plain_text(a))

    def test_prompt_has_questions(self):
        sys_, user = apply.build_app_prompt({"full_name": "Pat Example"}, "inventory text", [], {"title": "CIO", "company": "X"}, "JD", ["Do you have a TS?"])
        self.assertIn("Do you have a TS?", user)
        self.assertIn("TRUTH", sys_)
        _, user2 = apply.build_app_prompt({}, "inv", [], {"title": "CIO", "company": "X"}, "JD", [])
        self.assertIn(apply.DEFAULT_SCREENING[0], user2)

    def test_coverage(self):
        kws = [{"term": "Zero Trust", "importance": "required", "variants": ["ZTA"]},
               {"term": "NIST RMF", "importance": "required", "variants": ["Risk Management Framework"]},
               {"term": "Kubernetes", "importance": "preferred"},
               {"term": "P&L", "importance": "required", "variants": ["profit and loss"]},
               {"term": "zero trust", "importance": "preferred"}]
        cov = apply.keyword_coverage(kws, "Led Zero Trust rollout across 42 sites.",
                                     "Applied the Risk Management Framework to 400K endpoints. Owned P&L of $165M ARR.")
        self.assertEqual([k["term"] for k in cov["covered"]], ["Zero Trust"])
        self.assertEqual(sorted(k["term"] for k in cov["add"]), ["NIST RMF", "P&L"])
        self.assertEqual([k["term"] for k in cov["gap"]], ["Kubernetes"])
        self.assertEqual(cov["score"], 25)
        self.assertFalse(apply._has(apply._norm("cybersecurity"), "security"))


if __name__ == "__main__":
    unittest.main()
