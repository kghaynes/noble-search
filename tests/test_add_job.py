import unittest
from unittest import mock

import sources

LD_PAGE = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"Director of Cybersecurity","hiringOrganization":{"@type":"Organization","name":"Acme &amp; Co"},
"datePosted":"2026-11-01","jobLocation":{"address":{"addressLocality":"Melbourne","addressRegion":"FL"}},
"description":"<p>Lead the security program.</p><ul><li>Own risk</li></ul>"}</script></head><body></body></html>"""

RADIANCY_PAGE = """<html><head><meta property="og:title" content="Sr. Manager, Program Management at DISNEY"></head><body>
<nav>Keywords Location Search Jobs</nav>
<div class="job-description job-description__job-title-info-container"><h1>Sr. Manager</h1>
<span class="job-location job-info"><b>Location</b> Glendale, California, United States</span>
<div class="inner"><p>Job ID 1</p></div></div>
<div class="ats-description"><h2>Job Summary:</h2><div class="x"><p>Drive launch operations across product, engineering, legal and marketing teams for a new platform.</p><div><p>Lead programs.</p></div></div></div>
<footer><div>Location Select Location Anaheim, California</div></footer></body></html>"""


class ReadPage(unittest.TestCase):
    def test_json_ld_company_and_location(self):
        d = sources.parse_job_page(LD_PAGE)
        self.assertEqual((d["title"], d["company"], d["locations"]), ("Director of Cybersecurity", "Acme & Co", ["Melbourne, FL"]))
        self.assertIn("• Own risk", d["description"])

    def test_description_blocks_title_at_company_location_line(self):
        d = sources.parse_job_page(RADIANCY_PAGE)
        self.assertEqual((d["title"], d["company"]), ("Sr. Manager, Program Management", "DISNEY"))
        self.assertEqual(d["locations"], ["Glendale, California, United States"])
        self.assertTrue(d["description"].startswith("Job Summary:"))
        self.assertIn("Lead programs.", d["description"])
        self.assertNotIn("Anaheim", d["description"])

    def test_site_name_fills_company(self):
        d = sources.parse_job_page('<meta property="og:site_name" content="Leidos Careers"><h1>Program Manager</h1><main><p>x</p></main>')
        self.assertEqual((d["title"], d["company"]), ("Program Manager", "Leidos Careers"))


class ReadLink(unittest.TestCase):
    def test_refuses_bad_and_blocked_links(self):
        self.assertFalse(sources.read_job_link("disney.com/job")["ok"])
        r = sources.read_job_link("https://www.linkedin.com/jobs/view/1")
        self.assertFalse(r["ok"])
        self.assertIn("LinkedIn", r["reason"])

    def test_reads_page(self):
        with mock.patch.object(sources, "http", lambda *a, **k: RADIANCY_PAGE):
            r = sources.read_job_link("https://careers.example.com/job/1")
        self.assertTrue(r["ok"])
        self.assertEqual((r["title"], r["company"], r["location"]), ("Sr. Manager, Program Management", "DISNEY", "Glendale, California"))
        self.assertIn("description", r["reason"])   # short sample description: asks for a paste



class WorkModeFromText(unittest.TestCase):
    def test_phrases(self):
        f = sources.work_mode_from_text
        self.assertEqual(f("This role is considered remote, which means you work remotely.")[0], "Remote")
        self.assertEqual(f("A fully remote team. Apply today.")[0], "Remote")
        self.assertEqual(f("This is a hybrid role in Orlando. It is also remote friendly.")[0], "Hybrid")
        self.assertEqual(f("This position is not remote.")[0], "")
        self.assertEqual(f("No remote work. Must report on site.")[0], "")
        self.assertEqual(f("We value remote collaboration tools.")[0], "")
        self.assertEqual(f("")[0], "")
        self.assertEqual(f("Great job.\nThis role is considered remote.")[1], "This role is considered remote.")

    def test_read_link_sets_mode_with_note(self):
        page = RADIANCY_PAGE.replace("<div class=\"inner\">", "<span class=\"job-remote\">This role is considered remote.</span><div class=\"inner\">")
        with mock.patch.object(sources, "http", lambda *a, **k: page):
            r = sources.read_job_link("https://careers.example.com/job/1")
        self.assertEqual(r["work_mode"], "Remote")
        self.assertIn("considered remote", r["work_mode_note"])


if __name__ == "__main__":
    unittest.main()
