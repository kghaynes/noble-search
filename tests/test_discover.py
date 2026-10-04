"""Employer auto-discovery with stand-in websites. Run: python3 -m unittest discover -s tests"""
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import discover  # noqa: E402
import sources  # noqa: E402


class Discover(unittest.TestCase):
    def setUp(self):
        self._http, self._fetch = sources.http, discover.fetch

    def tearDown(self):
        sources.http, discover.fetch = self._http, self._fetch

    def test_board_from_url(self):
        f = discover.board_from_url
        self.assertEqual(f("https://ngc.wd1.myworkdayjobs.com/en-US/Northrop_Grumman_External_Site/job/X_R1"),
                         ("https://ngc.wd1.myworkdayjobs.com/Northrop_Grumman_External_Site", "workday"))
        self.assertEqual(f("https://job-boards.greenhouse.io/embed/job_board?for=relativity"), ("https://boards.greenhouse.io/relativity", "greenhouse"))
        self.assertEqual(f("https://boards.greenhouse.io/spacex/jobs/123"), ("https://boards.greenhouse.io/spacex", "greenhouse"))
        self.assertEqual(f("https://lockheedmartin.eightfold.ai/careers/job/1", "lockheedmartin.com"),
                         ("https://lockheedmartin.eightfold.ai/careers?domain=lockheedmartin.com", "eightfold"))
        self.assertEqual(f("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=x"), (None, "ADP Workforce Now"))
        self.assertEqual(f("https://vayaspace.bamboohr.com/careers/170"), ("https://vayaspace.bamboohr.com/careers", "bamboohr"))
        self.assertEqual(discover.slugs("Leonardo DRS, Inc.")[:2], ["leonardodrs", "leonardo-drs"])

    def test_website_path_workday(self):
        site = '<a href="/careers">Careers</a>'
        careers = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/Acme_Careers">Search jobs</a>'

        def fake_fetch(url, as_json=False, timeout=10):
            if urllib.parse.urlparse(url).hostname == "www.acmerockets.com":
                return ("https://www.acmerockets.com/", site) if not url.endswith("/careers") else (url, careers)
            return None, "nope"

        def fake_http(url, data=None, headers=None, timeout=30, as_json=True, retries=2):
            if "myworkdayjobs.com/wday/cxs/acme/Acme_Careers/jobs" in url:
                return {"total": 123, "jobPostings": [{"title": "Director, IT"}]}
            raise sources.ReaderError("HTTP 404")
        discover.fetch, sources.http = fake_fetch, fake_http
        r = discover.discover("Acme Rockets Inc")
        self.assertTrue(r["ok"], r)
        c = r["candidates"][0]
        self.assertEqual((c["url"], c["type"], c["jobs"]), ("https://acme.wd5.myworkdayjobs.com/Acme_Careers", "workday", 123))

    def test_unsupported_reported(self):
        def fake_fetch(url, as_json=False, timeout=10):
            if "www.sidus" in url:
                return url, '<a href="https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=1">Jobs</a>'
            return None, "x"
        discover.fetch = fake_fetch
        sources.http = lambda *a, **k: (_ for _ in ()).throw(sources.ReaderError("HTTP 404"))
        r = discover.discover("Sidus Space")
        self.assertFalse(r["ok"])
        self.assertIn("ADP Workforce Now", r["tip"])

    def test_slug_api(self):
        def fake_http(url, data=None, headers=None, timeout=30, as_json=True, retries=2):
            if url == "https://boards-api.greenhouse.io/v1/boards/stokespace/jobs":
                return {"jobs": [{"title": "Director, Launch Ops"}]}
            raise sources.ReaderError("HTTP 404")
        discover.fetch, sources.http = (lambda *a, **k: (None, "x")), fake_http
        r = discover.discover("Stoke Space")
        self.assertEqual(r["candidates"][0]["url"], "https://boards.greenhouse.io/stokespace")


if __name__ == "__main__":
    unittest.main()
