"""First-run helpers: AI suggestions, setup checklist, Claude request shape."""
import json
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.environ.setdefault("DB_PATH", os.path.join(TMP, "jobs.db"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import llm  # noqa: E402
import profile_store as ps  # noqa: E402
import suggest  # noqa: E402


class Suggest(unittest.TestCase):
    def setUp(self):
        self._orig = llm.complete
        self.addCleanup(setattr, llm, "complete", self._orig)

    def test_towns_needs_home(self):
        ps.save_profile({"home_location": "", "city_state": ""})
        with self.assertRaises(ValueError):
            suggest.suggest("towns")

    def test_searches_adds_home(self):
        ps.save_profile({"levels": "Director, VP", "lanes": "IT, cyber"})
        llm.complete = lambda *a, **k: (json.dumps({"items": ["director of it", "ciso remote", "vp operations in {home}"]}), "anthropic", "m")
        r = suggest.suggest("searches")
        self.assertEqual(r["items"], ["director of it in {home}", "ciso remote", "vp operations in {home}"])

    def test_towns(self):
        ps.save_profile({"home_location": "Springfield, IL", "radius_miles": 25})
        seen = {}

        def fake(settings, system, user, **k):
            seen["user"] = user
            return '{"items": ["Springfield, IL", "Chatham, IL"]}', "anthropic", "m"
        llm.complete = fake
        r = suggest.suggest("towns")
        self.assertIn("Springfield, IL", seen["user"])
        self.assertEqual(r["text"], "Springfield, IL\nChatham, IL")


class Setup(unittest.TestCase):
    def test_checklist(self):
        app.init_db()
        s = app.setup_state(False)
        self.assertFalse(s["done"])
        self.assertEqual([x["id"] for x in s["steps"]], ["ai", "profile", "career", "search", "run"])


class ProfileStep(unittest.TestCase):
    def test_says_what_is_missing(self):
        app.init_db()
        ps.save_profile({"full_name": "", "home_location": "Cape Canaveral", "city_state": ""})
        step = next(x for x in app.setup_state(False)["steps"] if x["id"] == "profile")
        self.assertFalse(step["done"])
        self.assertIn("full name", step["hint"]); self.assertIn("home town", step["hint"])
        ps.save_profile({"full_name": "Pat Example", "home_location": "Cape Canaveral", "city_state": "Melbourne, Florida"})
        step = next(x for x in app.setup_state(False)["steps"] if x["id"] == "profile")
        self.assertTrue(step["done"], step["hint"])   # City, State is used when Home location can't be read
        import sources
        self.assertEqual(sources.home_town(ps.get_profile()), "Melbourne, FL")


class Progress(unittest.TestCase):
    def test_phases_and_warnings(self):
        import fit, search, time
        app.init_db(); search.init(app.db, app._db_lock)
        self.assertEqual(app.progress()["phase"], "")
        search._state.update(running=True, current="L3Harris", step=6, steps=21, t0=time.time() - 600)
        try:
            p = app.progress()
        finally:
            search._state.update(running=False, current="", step=0, steps=0, t0=None)
        self.assertEqual((p["phase"], p["step"], p["steps"]), ("scan", 7, 21))
        self.assertGreater(p["left"], 1000)   # 100 s per source so far x 15 left
        fit._state.update(running=True, done=10, total=40, started=time.time() - 100, provider="anthropic")
        try:
            p = app.progress()
        finally:
            fit._state.update(running=False)
        self.assertEqual(p["phase"], "rate"); self.assertAlmostEqual(p["left"], 300, delta=5)
        search.save_config({"fit_provider": "off"})
        self.assertTrue(app.progress()["fit_off"])
        search.save_config({"fit_provider": "anthropic"})


class ClaudeRequest(unittest.TestCase):
    def test_system_cached_and_model_override(self):
        sent = {}
        orig = llm._post
        self.addCleanup(setattr, llm, "_post", orig)

        def fake(url, body, headers=None, timeout=900):
            sent.update(body)
            return {"content": [{"type": "text", "text": "ok"}]}
        llm._post = fake
        _, _, model = llm.complete({"anthropic_api_key": "k", "anthropic_model": "big"}, "SYS", "hi", model="small")
        self.assertEqual(model, "small")
        self.assertEqual(sent["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(sent["system"][0]["text"], "SYS")


if __name__ == "__main__":
    unittest.main()
