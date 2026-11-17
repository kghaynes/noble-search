import unittest

import drafts
import suggest


class Career(unittest.TestCase):
    def test_inventory_first_then_two_resumes(self):
        self.assertEqual(suggest.career_text("  inv  ", [("A", "a")]), "Career inventory:\ninv")
        self.assertEqual(suggest.career_text("", [("A", "a"), ("B", "b"), ("C", "c")]), "Resume (A):\na\n\nResume (B):\nb")
        self.assertEqual(suggest.career_text("", []), "")
        long = suggest.career_text("x" * (suggest.INVENTORY_MAX + 5), [])
        self.assertTrue(long.endswith("x\n[…inventory shortened]"))


class Replies(unittest.TestCase):
    def test_searches_strip_places(self):
        raw = '{"items": ["Director of IT", "CIO remote", "remote IT Director", "CISO in Orlando, FL", "director of it", " "]}'
        self.assertEqual(suggest.searches_items(raw), ["Director of IT", "CIO", "IT Director"])

    def test_titles_reply_cleans_terms(self):
        r = suggest.titles_reply('{"levels": ["Director", "VP / Vice President"], "fields": ["I.T.", "Cyber"], "skip": ["Sales!"], "example_titles": [" CIO "]}')
        self.assertEqual(r["levels"], ["director", "vp", "vice president"])
        self.assertEqual(r["fields"], ["it", "cyber"])
        self.assertEqual((r["skip"], r["example_titles"], r["translations"]), (["sales"], ["CIO"], []))
        with self.assertRaises(ValueError):
            suggest.titles_reply("no json")

    def test_prompts_need_something(self):
        with self.assertRaises(ValueError):
            suggest.searches_prompt({}, "")
        s, u = suggest.titles_prompt({"levels": "Director"}, "")
        self.assertIs(s, suggest.TITLES_SYSTEM)
        self.assertIn("Target levels: Director", u)


class Inventory(unittest.TestCase):
    def test_user_and_reply(self):
        self.assertEqual(drafts.inventory_user([("A.docx", "text")]), "# RESUME: A.docx\ntext\n\nWrite the career inventory now.")
        with self.assertRaises(RuntimeError):
            drafts.inventory_user([])
        self.assertEqual(drafts.inventory_reply("```markdown\n# Career Inventory\nx\n```\n"), "# Career Inventory\nx\n")
        self.assertEqual(drafts.inventory_reply("  # Career Inventory  "), "# Career Inventory")


if __name__ == "__main__":
    unittest.main()
