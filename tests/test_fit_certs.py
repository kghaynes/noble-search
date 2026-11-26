"""Fit prompt: 'Certifications held' line, the inventory without its gaps section, and the manual re-rate model.
Run: python3 -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import drafts  # noqa: E402
import fit  # noqa: E402

INV = """# Career Inventory
## Summary of strengths
- CISSP-certified cyber leader
## Certifications
- **CISSP** (ISC)2
* PMP
1. ITIL v3 Foundation
Dates, credential IDs and current status were not provided.
## Gaps / to fill in (questions for the candidate)
- Are CISSP, PMP current?
### Dates
- When did you earn them?
## Other notes
Likes Space Coast."""


class CertsHeld(unittest.TestCase):
    def test_inventory_and_profile_deduped(self):
        got = fit.certifications_held(INV, {"mil_certs": "cissp (isc)2\nSecurity+ CE\nPMP"})
        self.assertEqual(got, ["CISSP (ISC)2", "PMP", "ITIL v3 Foundation", "Security+ CE"])

    def test_notes_and_none_skipped(self):
        inv = "## Certifications\n- None listed\n- See resume for the full list of things I hold today please now\n- Sec+"
        self.assertEqual(fit.certifications_held(inv, {}), ["Sec+"])

    def test_cap_and_item_length(self):
        inv = "## Certifications\n" + "\n".join(f"- Cert{i}" for i in range(30))
        self.assertEqual(len(fit.certifications_held(inv, {})), fit.CERTS_MAX_ITEMS)
        long = "## Certifications\n- " + "X" * 300
        self.assertEqual(len(fit.certifications_held(long, {})[0]), fit.CERTS_ITEM_MAX_CHARS)

    def test_none(self):
        self.assertEqual(fit.certifications_held("plain text inventory", {}), [])
        self.assertNotIn(fit.CERTS_LABEL + ":", fit.system_prompt({}, "plain text", []))

    def test_line_in_candidate_block(self):
        s = fit.system_prompt({"clearance": "TS/SCI", "mil_certs": "CISSP PMP"}, INV, [])
        self.assertIn("Clearance: TS/SCI\nCertifications held: CISSP (ISC)2; PMP; ITIL v3 Foundation; CISSP PMP\n", s)
        self.assertIn('check the CANDIDATE lines (including "Certifications held")', s)


class FitInventory(unittest.TestCase):
    def test_gaps_dropped_with_subsections(self):
        out = fit.fit_inventory(INV)
        self.assertNotIn("Gaps", out)
        self.assertNotIn("When did you earn", out)
        self.assertIn("## Other notes\nLikes Space Coast.", out)
        self.assertIn("## Certifications", out)

    def test_at_end_and_case(self):
        self.assertEqual(fit.fit_inventory("## Roles\nx\n## GAPS / TO FILL IN\n- q?"), "## Roles\nx")

    def test_noop(self):
        for t in ("plain text, no headings. Gaps / to fill in", "## Roles\nx", ""):
            self.assertEqual(fit.fit_inventory(t), t)

    def test_drafts_keep_gaps(self):
        self.assertIn("## Gaps / to fill in (things", drafts.INVENTORY_SYSTEM)
        self.assertIn("## Certifications\n## Awards", drafts.INVENTORY_SYSTEM)


class ManualModel(unittest.TestCase):
    def test_tiers(self):
        st = {"anthropic_model": "big", "anthropic_fit_model": "small"}
        self.assertEqual(fit.model_for(st), "small")
        self.assertEqual(fit.model_for(st, manual=True), "big")


if __name__ == "__main__":
    unittest.main()
