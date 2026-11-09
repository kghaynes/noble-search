"""Personal details stay out of AI requests (privacy.py, llm.complete). Run: python3 -m unittest discover -s tests"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import llm  # noqa: E402
import privacy  # noqa: E402
import profile_store  # noqa: E402

PROFILE = {"full_name": "Kenneth G. Haynes", "email": "ken@example.com", "phone": "321-555-0123",
           "linkedin": "https://www.linkedin.com/in/kenhaynes/", "city_state": "Melbourne, FL",
           "clearance": "TOP SECRET/SCI with CI polygraph (2021), active, SSBI 2019"}

RESUME = """Kenneth G. Haynes | Melbourne, FL | (321) 555-0123 | ken@example.com | linkedin.com/in/kenhaynes
2895 Main St, Apt 4
Ken Haynes led a 5,000-person organization. B.S., Cal Poly, 1994.
Holds TS/SCI with Full Scope Poly completed June 2022; SSBI 2019."""


class Redact(unittest.TestCase):
    def test_contact_details_replaced(self):
        _, u, r = privacy.prepare(PROFILE, "sys", RESUME)
        for secret in ("Kenneth", "Haynes", "555-0123", "ken@example.com", "kenhaynes", "2895 Main St"):
            self.assertNotIn(secret, u, secret)
        for ph in ("{{NAME}}", "{{PHONE}}", "{{EMAIL}}", "{{LINKEDIN}}", "{{ADDRESS}}"):
            self.assertIn(ph, u, ph)
        self.assertIn("Melbourne, FL", u)        # town is kept: needed for location and relocation checks
        self.assertIn("Cal Poly", u)             # a school named 'Poly' is not a polygraph

    def test_other_peoples_details_get_their_own_placeholder(self):
        _, u, r = privacy.prepare(PROFILE, "sys", "Recruiter: jane.doe@corp.com, 407-555-9876. Me: ken@example.com")
        self.assertIn("{{EMAIL_2}}", u)
        self.assertIn("{{PHONE}}", u.replace("{{PHONE_2}}", "{{PHONE}}"))
        self.assertEqual(r.restore("{{EMAIL_2}} / {{EMAIL}}"), "jane.doe@corp.com / ken@example.com")

    def test_restore_round_trip_and_json_safe(self):
        prof = dict(PROFILE, full_name='Kenneth "Ken" Haynes')
        _, _, r = privacy.prepare(prof, "sys", "x")
        out = r.restore('{"name": "{{NAME}}", "email": "{{EMAIL}}"}', json_safe=True)
        self.assertEqual(json.loads(out), {"name": 'Kenneth "Ken" Haynes', "email": "ken@example.com"})

    def test_notice_only_when_something_was_replaced(self):
        s, _, _ = privacy.prepare({}, "sys", "nothing personal here")
        self.assertEqual(s, "sys")
        s, _, _ = privacy.prepare(PROFILE, "sys", RESUME)
        self.assertIn("PRIVACY PLACEHOLDERS", s)


class Clearance(unittest.TestCase):
    def test_generalize(self):
        g = privacy.generalize_clearance
        self.assertEqual(g(PROFILE["clearance"]), "Active TS/SCI")
        self.assertEqual(g("Secret (inactive since 2020)"), "Inactive Secret")
        self.assertEqual(g("Top Secret, eligible"), "Eligible Top Secret")
        self.assertEqual(g("Public Trust"), "Public Trust")
        self.assertEqual(g(""), "")

    def test_profile_clearance_generalized_in_prompt(self):
        _, u, _ = privacy.prepare(PROFILE, "sys", "Clearance: " + PROFILE["clearance"])
        self.assertEqual(u, "Clearance: Active TS/SCI")

    def test_candidate_material_scrubbed(self):
        inv, texts = privacy.scrub_candidate(RESUME, [("r.docx", "Active TS/SCI with CI polygraph (2021).")])
        self.assertNotIn("Poly completed", inv)
        self.assertNotIn("SSBI", inv)
        self.assertIn("TS/SCI", inv)
        self.assertIn("Cal Poly", inv)
        self.assertEqual(texts[0][1], "Active TS/SCI.")

    def test_job_posting_requirements_untouched(self):
        # a posting's own requirement must reach the model so the fit rating can flag it
        jd = "Requires an active TS/SCI with Full Scope Polygraph."
        _, u, _ = privacy.prepare(PROFILE, "sys", jd)
        self.assertEqual(u, jd)


class Wiring(unittest.TestCase):
    def setUp(self):
        self.sent = []

        def fake_post(url, payload, headers=None, timeout=900):
            self.sent.append((url, payload))
            return {"content": [{"type": "text", "text": '{"header": "{{NAME}} | {{EMAIL}}"}'}], "stop_reason": "end_turn"}
        self.orig_post, self.orig_profile = llm._post, profile_store.get_profile
        llm._post = fake_post
        profile_store.get_profile = lambda: dict(PROFILE)

    def tearDown(self):
        llm._post, profile_store.get_profile = self.orig_post, self.orig_profile

    def test_request_redacted_reply_restored(self):
        s = {"anthropic_api_key": "k", "anthropic_model": "claude-test"}
        text, _, _ = llm.complete(s, "Candidate: Kenneth G. Haynes", RESUME, provider="anthropic")
        url, body = self.sent[0]
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")    # Messages API only
        wire = json.dumps(body)
        for secret in ("Kenneth", "ken@example.com", "555-0123"):   # poly details: scrubbed at load, see Loaders
            self.assertNotIn(secret, wire, secret)
        self.assertEqual(json.loads(text), {"header": "Kenneth G. Haynes | ken@example.com"})

    def test_setting_off_sends_as_is(self):
        s = {"anthropic_api_key": "k", "anthropic_model": "claude-test", "redact_pii": False}
        llm.complete(s, "sys", "ken@example.com", provider="anthropic")
        self.assertIn("ken@example.com", json.dumps(self.sent[0][1]))

    def test_setting_saved_as_bool(self):
        orig = profile_store.SETTINGS_JSON
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            profile_store.SETTINGS_JSON = os.path.join(d, "settings.json")
            try:
                self.assertIs(profile_store.save_settings({"redact_pii": "false"})["redact_pii"], False)
                self.assertIs(profile_store.save_settings({"redact_pii": True})["redact_pii"], True)
            finally:
                profile_store.SETTINGS_JSON = orig


class Loaders(unittest.TestCase):
    def test_drafts_sources_scrub_polygraph(self):
        import drafts
        saved = (profile_store.get_inventory, profile_store.list_resumes, profile_store.get_settings)
        profile_store.get_inventory = lambda: RESUME
        profile_store.list_resumes = lambda: []
        profile_store.get_settings = lambda include_secret=False: {"redact_pii": True}
        try:
            inv, _ = drafts._sources()
        finally:
            profile_store.get_inventory, profile_store.list_resumes, profile_store.get_settings = saved
        self.assertNotIn("Poly completed", inv)
        self.assertNotIn("SSBI", inv)
        self.assertIn("TS/SCI", inv)


class NoBatchOrFilesAPI(unittest.TestCase):
    def test_only_messages_endpoint_in_code(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        for name in os.listdir(root):
            if name.endswith(".py"):
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    src = f.read()
                for bad in ("/v1/messages/batches", "/v1/files"):
                    self.assertNotIn(bad, src, f"{bad} in {name}")


if __name__ == "__main__":
    unittest.main()
