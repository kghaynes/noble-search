"""Application draft end to end through the worker with a stand-in model."""
import json
import os
import sys
import tempfile
import time
import unittest

TMP = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(TMP, "jobs.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402
import drafts  # noqa: E402
import llm  # noqa: E402
import profile_store as ps  # noqa: E402


class AppDraft(unittest.TestCase):
    def test_worker(self):
        app.init_db(); drafts.init()
        ps.save_inventory("Deputy Commanding Officer, 1st Signal Brigade, 06/2019-07/2021: 5,000 people, Zero Trust.")
        with app.db() as c:
            c.execute("INSERT OR REPLACE INTO jobs(job_key,title,company) VALUES('ngc|director it','Director IT','NGC')")
        calls = []

        def fake(settings, system, user, provider=None, json_mode=True, max_tokens=8000, model=None):
            calls.append(system[:40])
            if "hiring keywords" in system:
                return json.dumps({"keywords": [{"term": "Zero Trust", "importance": "required"},
                                                {"term": "Kubernetes", "importance": "preferred"}]}), "ollama", "qwen"
            return json.dumps({"summary": "COO-level technology executive.", "work_history": [
                {"job_title": "Deputy Commanding Officer (COO)", "company": "1st Signal Brigade", "from": "06/2019", "to": "07/2021",
                 "description": "- Led 5,000 people\n- Rolled out Zero Trust\n- Cut costs 37%"}],
                "skills": ["Zero Trust"], "why_company": "Mission.", "screening": [{"question": "Salary?", "answer": "[confirm]", "verify": True}],
                "cover_letter": "Dear hiring team...", "notes_for_candidate": ["No Kubernetes"]}), "ollama", "qwen"
        orig = llm.complete
        llm.complete = fake
        self.addCleanup(setattr, llm, 'complete', orig)
        did = drafts.create("ngc|director it", "application", "x" * 400 + " Zero Trust Kubernetes", "ollama", {"questions": ["Salary?"]})
        for _ in range(50):
            info, _ = drafts.get(did)
            if info["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        self.assertEqual(info["status"], "done", info.get("error"))
        self.assertEqual(info["app"]["work_history"][0]["company"], "1st Signal Brigade")
        self.assertEqual([f["value"] for f in info["facts"]], ["37%"])  # dates ignored, 5,000 found, 37% flagged
        self.assertEqual(info["keywords"]["score"], 50)
        self.assertEqual(info["keywords"]["gap"][0]["term"], "Kubernetes")
        self.assertIn("Salary?", json.dumps(calls) + info["text"])


if __name__ == "__main__":
    unittest.main()
