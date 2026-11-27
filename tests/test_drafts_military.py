"""Resume and application prompts: the same MILITARY BACKGROUND block as the fit prompt (spec sections 7.1, 7.2).
Run: python3 -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import apply  # noqa: E402
import fit  # noqa: E402
import military  # noqa: E402
import profile_store as ps  # noqa: E402
import resume  # noqa: E402

MIL = {"mil_branch": "U.S. Army", "mil_rank": "Colonel (O-6)", "mil_codes": "25A Signal Officer",
       "mil_highest_position": "Deputy Commanding Officer", "mil_certs": "PMP (Army-funded)",
       "vet_preference": "10", "disability_30": "true"}
PROFILE = dict(MIL, full_name="A. Vet", city_state="Melbourne, FL", clearance="Active TS/SCI",
               levels="Director", lanes="IT Ops", notes="Keep it short.", work_modes="Remote")
JOB = {"title": "CIO", "company": "Acme", "location": "Remote", "work_mode": "Remote"}
INV = "## Roles\n- Deputy Commanding Officer"


def _users():
    return {"resume": resume.build_resume_prompt(PROFILE, INV, [], JOB, "Lead IT.")[1],
            "application": apply.build_app_prompt(PROFILE, INV, [], JOB, "Lead IT.", [])[1],
            "fit": fit.system_prompt(PROFILE, INV, [])}


class DraftsMilitary(unittest.TestCase):
    def test_one_implementation(self):
        self.assertIs(fit.military_block, military.military_block)
        self.assertIs(resume.military_block, military.military_block)
        self.assertIs(apply.military_block, military.military_block)
        self.assertEqual(military.military_block(PROFILE)[2], ps.military_text(PROFILE))

    def test_block_after_profile_lines_before_notes_and_inventory(self):
        block = "\n".join(military.military_block(PROFILE))
        for kind, user in _users().items():
            self.assertEqual(user.count(military.MILITARY_HEADING), 1, kind)
            self.assertIn(block, user, kind)
            i = user.index(military.MILITARY_HEADING)
            last_profile = "Target role lanes: IT Ops" if kind == "resume" else "Work modes: Remote"
            self.assertLess(user.index(last_profile), i, kind)
            self.assertLess(i, user.index("Keep it short."), kind)
            self.assertLess(i, user.index("# CAREER INVENTORY"), kind)

    def test_federal_fields_never_in_any_prompt(self):
        for kind, user in _users().items():
            self.assertNotIn("10", user.replace("Colonel (O-6)", ""), kind)
            self.assertNotIn("disab", user.lower(), kind)
            self.assertNotIn("preference", user.lower(), kind)

    def test_omitted_when_empty(self):
        bare = {"full_name": "A. Vet", "vet_preference": "5", "disability_30": "true"}
        self.assertEqual(military.military_block(bare), [])
        self.assertNotIn("MILITARY BACKGROUND", resume.build_resume_prompt(bare, "", [], JOB, "")[1])
        self.assertNotIn("MILITARY BACKGROUND", apply.build_app_prompt(bare, "", [], JOB, "", [])[1])

    def test_system_rules_extended_not_duplicated(self):
        for system in (resume.RESUME_SYSTEM, apply.APP_SYSTEM):
            self.assertEqual(system.count("MILITARY BACKGROUND"), 1)
            self.assertEqual(system.count("TRANSLATE"), 1)
            self.assertIn("rank, occupation codes, schools and scope of command into civilian terms and level", system)

    def test_certifications_in_both_lines(self):
        # Military-earned certifications show in 'Certifications held' and in the block, as in the fit prompt.
        for kind, user in _users().items():
            self.assertIn("Certifications held: PMP (Army-funded)", user, kind)
            self.assertIn("Military-earned certifications: PMP (Army-funded)", user, kind)


if __name__ == "__main__":
    unittest.main()
