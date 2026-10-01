"""Claude API call drops `temperature` when a model refuses it. Run: python3 -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import llm  # noqa: E402


class Temperature(unittest.TestCase):
    def test_retry_without_temperature(self):
        sent = []

        def fake_post(url, payload, headers=None, timeout=900):
            sent.append(dict(payload))
            if "temperature" in payload:
                raise llm.LLMError('HTTP 400: {"message":"`temperature` is deprecated for this model."}')
            return {"content": [{"type": "text", "text": "OK"}]}
        orig = llm._post
        llm._post = fake_post
        try:
            s = {"anthropic_api_key": "k", "anthropic_model": "claude-test-x"}
            self.assertEqual(llm.complete(s, "sys", "hi", provider="anthropic")[0], "OK")
            self.assertEqual(llm.complete(s, "sys", "hi", provider="anthropic")[0], "OK")
        finally:
            llm._post = orig
        self.assertEqual(["temperature" in p for p in sent], [True, False, False])  # remembered after the first refusal


if __name__ == "__main__":
    unittest.main()
