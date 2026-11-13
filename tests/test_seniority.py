"""Seniority control: on/off switch, level, and a separate editable word list per level."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import search  # noqa: E402
import sources  # noqa: E402

PROFILE = {"work_modes": "On-site, Hybrid, Remote"}

# Titles that probe the tricky parts of the presets: short words, endings, "lead" vs "leader",
# "chief" inside other words, "Sr." with and without the dot, numbered levels, "Principal," vs "Principal Engineer".
CORPUS = [
    "Director of IT", "Directors Program", "Vice President, Cyber", "VP Operations", "SVP Technology", "EVP",
    "Chief of Staff", "Chief Information Officer", "Mischief Coordinator", "Head of Security", "CIO", "CISO",
    "CTO", "COO", "General Manager", "Senior Manager, Programs", "Sr. Manager IT", "Sr Manager IT", "Sr. Mgr Network",
    "Manager III", "Manager 4", "Manager IV, Ops", "Manager", "Program Manager", "Project Manager", "L6 Engineer",
    "Software Engineer L5", "Principal, IT Strategy", "Principal Engineer", "Principal Program Manager",
    "Principal Capture Manager", "Deputy Program Manager", "Executive Officer", "Executive Assistant",
    "Team Lead", "Team Leader", "Leadership Development", "Supervisor, Help Desk", "Supervisors", "Senior Analyst",
    "Sr Analyst", "Heads of Delivery", "Directorate Analyst", "Technician", "Cashier", "Lead Engineer",
    "Managers", "Management Analyst", "Deputies", "Chiefs", "Senior", "Principals",
]


def ctx(**cfg):
    return sources.Context({"title_exclude": "intern", **cfg}, PROFILE)


class Readable(unittest.TestCase):
    def test_readable_default_keeps_and_drops_the_same_titles(self):
        for level in sources.SENIORITY_LEVELS:
            raw = sources.TITLE_PRESETS[level][1]
            readable = sources.readable_lines(raw)
            a, b = ctx(title_include=raw), ctx(title_include=readable)
            for t in CORPUS:
                self.assertEqual(a.title_ok(t), b.title_ok(t), f"{level}: {t!r}")

    def test_plain_alternatives_split(self):
        self.assertEqual(sources.readable_lines(r"\bcio\b|\bcto\b"), "cio\ncto")
        self.assertEqual(sources.readable_lines(r"\bvp\b"), "vp")
        self.assertEqual(sources.readable_lines(r"sr\.? manager"), r"sr\.? manager")
        self.assertIn("cio", sources.readable_lines(sources.TITLE_PRESETS["executive"][1]).splitlines())


class Effective(unittest.TestCase):
    def test_off_means_any_title(self):
        cfg = {"title_seniority_on": False, "title_level": "executive", "title_include_by_level": {}}
        self.assertEqual(sources.effective_include(cfg), "*")
        c = ctx(**cfg)
        self.assertTrue(c.title_ok("Cashier"))
        self.assertFalse(c.title_ok("Summer Intern"))   # skip words still apply

    def test_level_edit_or_preset(self):
        self.assertEqual(sources.effective_include({"title_level": "manager"}), sources.TITLE_PRESETS["manager"][1])
        cfg = {"title_level": "manager", "title_include_by_level": {"manager": "boss", "executive": "chief"}}
        self.assertEqual(sources.effective_include(cfg), "boss")
        cfg["title_include_by_level"]["manager"] = "   "
        self.assertEqual(sources.effective_include(cfg), sources.TITLE_PRESETS["manager"][1])
        self.assertEqual(sources.effective_include({"title_level": "bogus", "title_seniority_on": True}),
                         sources.TITLE_PRESETS[sources.DEFAULT_LEVEL][1])

    def test_old_cfg_without_new_keys_unchanged(self):
        self.assertEqual(sources.effective_include({"title_include": "boss"}), "boss")
        self.assertTrue(ctx(title_include="boss").title_ok("Big Boss"))
        self.assertFalse(ctx(title_include="boss").title_ok("Director"))


class Saved(unittest.TestCase):
    def setUp(self):
        self.old = search.SEARCH_JSON
        search.SEARCH_JSON = os.path.join(tempfile.mkdtemp(), "search.json")

    def tearDown(self):
        search.SEARCH_JSON = self.old

    def write(self, data):
        with open(search.SEARCH_JSON, "w") as f:
            json.dump(data, f)

    def saved(self):
        with open(search.SEARCH_JSON) as f:
            return json.load(f)

    def test_defaults(self):
        c = search.get_config()
        self.assertEqual((c["title_seniority_on"], c["title_level"], c["title_include_by_level"]), (True, "executive", {}))
        self.assertEqual(c["title_include"], sources.TITLE_PRESETS["executive"][1])

    def test_per_level_edits_kept_separately(self):
        search.save_config({"title_level": "executive", "title_include_by_level": {"executive": "chief\nboss"}})
        search.save_config({"title_level": "manager", "title_include_by_level": {"manager": "supervisor"}})
        c = search.get_config()
        self.assertEqual(c["title_include_by_level"], {"executive": "chief\nboss", "manager": "supervisor"})
        self.assertEqual(c["title_include"], "supervisor")
        search.save_config({"title_level": "executive"})
        self.assertEqual(search.get_config()["title_include"], "chief\nboss")

    def test_default_text_is_not_an_edit(self):
        readable = sources.readable_lines(sources.TITLE_PRESETS["manager"][1])
        search.save_config({"title_level": "manager", "title_include_by_level": {"manager": readable, "executive": ""}})
        self.assertEqual(search.get_config()["title_include_by_level"], {})

    def test_reset_removes_the_edit(self):
        search.save_config({"title_include_by_level": {"executive": "boss", "manager": "lead"}})
        search.reset_level("executive")
        c = search.get_config()
        self.assertEqual(c["title_include_by_level"], {"manager": "lead"})
        self.assertEqual(c["title_include"], sources.TITLE_PRESETS["executive"][1])

    def test_switch_off_and_on(self):
        search.save_config({"title_seniority_on": False})
        self.assertEqual(search.get_config()["title_include"], "*")
        search.save_config({"title_seniority_on": "true"})
        self.assertEqual(search.get_config()["title_include"], sources.TITLE_PRESETS["executive"][1])

    def test_migration_of_old_custom_list(self):
        self.write({"title_include": "director\nchief", "title_exclude": "intern"})
        c = search.get_config()
        self.assertEqual((c["title_seniority_on"], c["title_level"]), (True, "executive"))
        self.assertEqual(c["title_include_by_level"], {"executive": "director\nchief"})
        self.assertEqual(c["title_include"], "director\nchief")      # behaviour unchanged
        search.save_config({"title_exclude": "intern\ncashier"})      # first save after the upgrade
        s = self.saved()
        self.assertEqual(s["title_include_by_level"], {"executive": "director\nchief"})
        self.assertEqual(s["title_include"], "director\nchief")

    def test_migration_preset_and_star(self):
        self.write({"title_include": sources.TITLE_PRESETS["manager"][1]})
        c = search.get_config()
        self.assertEqual((c["title_level"], c["title_include_by_level"]), ("manager", {}))
        self.write({"title_include": "*"})
        c = search.get_config()
        self.assertEqual((c["title_seniority_on"], c["title_include"]), (False, "*"))

    def test_older_client_saving_one_list(self):
        search.save_config({"title_include": "boss"})
        c = search.get_config()
        self.assertEqual((c["title_include"], c["title_include_by_level"]), ("boss", {"executive": "boss"}))


if __name__ == "__main__":
    unittest.main()
