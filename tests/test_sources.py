"""Offline tests for the job readers. Run: python3 -m unittest discover -s tests"""
import json
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sources  # noqa: E402

TODAY = date.today()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731
PROFILE = {"home_location": "100 Main St, Melbourne, FL 32901", "radius_miles": 30,
           "work_modes": "On-site, Hybrid, Remote"}


PLACES = "Melbourne, FL\nPalm Bay, FL\nCape Canaveral\nCocoa, (FL|Florida)\nTitusville, FL"


def ctx(**kw):
    return sources.Context({"local_places": PLACES, **kw}, PROFILE)


class Fake:
    """Stand-in for sources.http: maps URL substrings to responses."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, data=None, headers=None, timeout=30, as_json=True):
        self.calls.append((url, data))
        for k, v in self.routes.items():
            if k in url:
                return v(url, data) if callable(v) else v
        raise sources.ReaderError("no route " + url)


class Matching(unittest.TestCase):
    def test_title(self):
        c = ctx()
        for t in ["Director, Programs 1", "VP, IT and Security", "Chief Information Officer", "Head of Avionics",
                  "Multifunctional Manufacturing Sr Mgr - L6", "Principal, Program Management 1",
                  "Senior Manager, Cyber Intelligence", "Deputy Program Manager"]:
            self.assertTrue(c.title_ok(t), t)
        for t in ["Software Engineer", "Executive Assistant to the Director", "Director Intern",
                  "Principal Engineer Supplier Quality", "Data Management Analyst", "Art Director"]:
            self.assertFalse(c.title_ok(t), t)

    def test_where(self):
        c = ctx()
        local = ["United States-Florida-Melbourne", "Melbourne, FL", "USA - Palm Bay, FL", "US-FL-Palm Bay",
                 "Cape Canaveral SFS, FL", "Melbourne, FL or Rochester, NY", "palm bay", "Cocoa, Florida"]
        for l in local:
            self.assertEqual(c.where(l), "local", l)
        remote = ["US-Remote", "Remote (US)", "Remote - United States", "Telework"]
        for l in remote:
            self.assertEqual(c.where(l), "remote", l)
        for l in ["AUS - Port Melbourne, Australia", "Orlando, FL", "Huntsville, AL", "Remote - UK",
                  "Melbourne, VIC, Australia", "United States-New York-Rochester", "Brevard, NC"]:
            self.assertIsNone(c.where(l), l)

    def test_remote_off(self):
        c = sources.Context({}, {**PROFILE, "work_modes": "On-site"})
        self.assertIsNone(c.where("US-Remote"))

    def test_dates(self):
        self.assertEqual(sources.parse_date("2026-9-3"), "2026-09-03")
        self.assertEqual(sources.parse_date("Tue Sep 15 07:00:00 UTC 2026"), "2026-09-15")
        self.assertEqual(sources.parse_date("Wed, 30 Sep 2026 7:00:00 GMT"), "2026-09-30")
        self.assertEqual(sources.parse_date(1790785440), "2026-09-30")
        self.assertEqual(sources.parse_date(1790785440000), "2026-09-30")
        self.assertEqual(sources.parse_date("09/30/2026"), "2026-09-30")
        self.assertEqual(sources.workday_age("Posted 30+ Days Ago"), None)
        self.assertEqual(sources.workday_age("Posted 4 Days Ago"), 4)

    def test_tidy(self):
        self.assertEqual(sources.tidy_loc("United States-Florida-Melbourne"), "Melbourne, FL")
        self.assertEqual(sources.tidy_loc("USA - El Segundo, CA"), "El Segundo, CA")

    def test_detect(self):
        self.assertEqual(sources.detect("https://ngc.wd1.myworkdayjobs.com/en-US/Northrop_Grumman_External_Site")[1],
                         {"host": "ngc.wd1.myworkdayjobs.com", "tenant": "ngc", "site": "Northrop_Grumman_External_Site"})
        self.assertEqual(sources.detect("https://boards.greenhouse.io/spacex")[1]["token"], "spacex")
        self.assertEqual(sources.detect("https://job-boards.greenhouse.io/embed/job_board?for=relativity")[1]["token"], "relativity")
        self.assertEqual(sources.detect("https://lockheedmartin.eightfold.ai/careers?domain=lockheedmartin.com")[1]["domain"], "lockheedmartin.com")
        self.assertEqual(sources.detect("https://careers.jacobs.com/en_US/careers/sitemap.xml")[0], "sitemap")
        self.assertEqual(sources.detect("https://vayaspace.bamboohr.com/careers")[0], "bamboohr")
        self.assertEqual(sources.detect("https://workforcenow.adp.com/x")[0], "off")


class Readers(unittest.TestCase):
    def run_board(self, board, routes, **cfg):
        fake = Fake(routes)
        old = sources.http
        sources.http = fake
        try:
            rows, st = sources.run_board(board, ctx(**cfg))
        finally:
            sources.http = old
        return rows, st, fake

    def test_workday(self):
        page = {"total": 4, "jobPostings": [
            {"title": "Director, Programs 1", "externalPath": "/job/A_1", "locationsText": "United States-Florida-Melbourne", "postedOn": "Posted Today"},
            {"title": "Software Engineer", "externalPath": "/job/B", "locationsText": "United States-Florida-Melbourne", "postedOn": "Posted Today"},
            {"title": "Director, IT", "externalPath": "/job/C", "locationsText": "2 Locations", "postedOn": "Posted 3 Days Ago"},
            {"title": "Director, Old", "externalPath": "/job/D", "locationsText": "Melbourne, FL", "postedOn": "Posted 30+ Days Ago"}]}
        det = {"/job/A_1": {"title": "Director, Programs 1", "location": "United States-Florida-Melbourne",
                            "startDate": D(0), "jobDescription": "<p>Pay $193,800.00 - $290,600.00. Active TS/SCI required.</p>",
                            "timeType": "Full time", "externalUrl": "https://ngc.wd1.myworkdayjobs.com/x/job/A_1"},
               "/job/C": {"title": "Director, IT", "location": "United States-Virginia-Falls Church",
                          "additionalLocations": ["US-Remote"], "startDate": D(3), "jobDescription": "x", "remoteType": "Remote"}}
        rows, st, fake = self.run_board(
            {"name": "Northrop Grumman", "url": "https://ngc.wd1.myworkdayjobs.com/Northrop_Grumman_External_Site"},
            {"/jobs": page, "/job/": lambda u, d: {"jobPostingInfo": det[u[u.index('/job/'):]]}})
        self.assertTrue(st["ok"], st)
        self.assertEqual([r["title"] for r in rows], ["Director, Programs 1", "Director, IT"])
        a = rows[0]
        self.assertEqual(a["location"], "Melbourne, FL")
        self.assertEqual(a["salary"], "$193,800 - $290,600")
        self.assertEqual(a["clearance"], "TS/SCI")
        self.assertEqual(a["posted_date"], D(0))
        self.assertEqual(rows[1]["work_mode"], "Remote")
        self.assertEqual(fake.calls[0][1]["searchText"], "")

    def test_greenhouse(self):
        data = {"jobs": [
            {"title": "Director, Launch Site Operations", "location": {"name": "Cape Canaveral, FL"}, "first_published": D(2) + "T10:00:00-04:00",
             "absolute_url": "https://boards.greenhouse.io/spacex/jobs/1", "content": "&lt;p&gt;Lead ops. $200,000 - $250,000&lt;/p&gt;"},
            {"title": "Director, Finance", "location": {"name": "Hawthorne, CA"}, "first_published": D(1)},
            {"title": "Director, Old", "location": {"name": "Cape Canaveral, FL"}, "first_published": D(90)}]}
        rows, st, _ = self.run_board({"name": "SpaceX", "url": "https://boards.greenhouse.io/spacex"}, {"boards-api": data})
        self.assertEqual(len(rows), 1)
        self.assertIn("Lead ops", rows[0]["description"])
        self.assertEqual(rows[0]["salary"], "$200,000 - $250,000")

    def test_eightfold(self):
        search = {"data": {"count": 2, "positions": [
            {"id": 1, "name": "Multifunctional Engineering & Science Mgr - L5", "standardizedLocations": ["Cape Canaveral, FL, US"],
             "postedTs": int(__import__('time').time()) - 86400, "workLocationOption": "onsite"},
            {"id": 2, "name": "Systems Analyst", "standardizedLocations": ["Orlando, FL, US"], "postedTs": int(__import__('time').time())}]}}
        det = {"data": {"jobDescription": "<p>Lead EGSE.</p>", "efcustomTextCustpayrange": ["$129,000.00 - $239,600.00"],
                        "efcustomTextFinalclearancefortherole": ["Secret"], "publicUrl": "https://lockheedmartin.eightfold.ai/careers/job/1"}}
        rows, st, fake = self.run_board({"name": "Lockheed Martin", "url": "https://lockheedmartin.eightfold.ai/careers?domain=lockheedmartin.com"},
                                        {"/api/pcsx/search": search, "/api/pcsx/position_details": det})
        self.assertEqual(len(rows), 1, st)
        self.assertEqual(rows[0]["clearance"], "Secret")
        self.assertEqual(rows[0]["salary"], "$129,000 - $239,600")
        self.assertIn("location=Melbourne%2C+FL", fake.calls[0][0])

    def test_sitemap_jsonld(self):
        sm = f"""<?xml version="1.0"?><urlset>
          <url><loc>https://careers.l3harris.com/en/job/melbourne/director-secure-area-it/4832/1</loc><lastmod>{D(0)}</lastmod></url>
          <url><loc>https://careers.l3harris.com/en/job/rochester/director-radio-programs/4832/2</loc><lastmod>{D(0)}</lastmod></url>
          <url><loc>https://careers.l3harris.com/en/job/palm-bay/software-engineer/4832/3</loc><lastmod>{D(0)}</lastmod></url>
          <url><loc>https://careers.l3harris.com/en/category/engineering-jobs/4832/1/1</loc></url></urlset>"""
        page1 = ('<script type="application/ld+json">' + json.dumps({"@context": "http://schema.org", "@type": "JobPosting",
                 "title": "Director, Secure Area IT", "datePosted": D(5).replace("-0", "-"), "employmentType": "Full-Time",
                 "description": "&lt;p&gt;Zero Trust. Active TS required.&lt;/p&gt;",
                 "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Melbourne", "addressRegion": "FL", "addressCountry": "US"}}]}) + "</script>")
        page2 = ('<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "title": "Director, Radio Programs",
                 "datePosted": D(1), "jobLocation": {"address": {"addressLocality": "Rochester", "addressRegion": "NY"}}}) + "</script>")
        rows, st, fake = self.run_board({"name": "L3Harris Technologies", "url": "https://careers.l3harris.com/sitemap.xml"},
                                        {"sitemap.xml": sm, "/4832/1": page1, "/4832/2": page2})
        self.assertEqual([r["title"] for r in rows], ["Director, Secure Area IT"], st)
        self.assertEqual(rows[0]["posted_date"], D(5))
        self.assertEqual(rows[0]["clearance"], "Top Secret")
        self.assertFalse(any("/4832/3" in c[0] for c in fake.calls))  # title filtered from the URL

    def test_sitemap_microdata(self):
        sm = f"<urlset><url><loc>https://careers.leonardodrs.com/job/Melbourne-Director-Program-Management-FL-32901/11/</loc></url></urlset>"
        page = ('<span itemprop="title">Director, Program Management</span><meta itemprop="datePosted" content="' +
                (TODAY - timedelta(days=2)).strftime("%a %b %d 07:00:00 UTC %Y") + '">'
                '<span itemprop="addressLocality">Melbourne</span><span itemprop="addressRegion">FL</span>'
                '<span class="jobdescription"><p>Run programs.</p></span></span>')
        rows, st, _ = self.run_board({"name": "Leonardo DRS", "url": "https://careers.leonardodrs.com/sitemap.xml"},
                                     {"sitemap.xml": sm, "/job/": page})
        self.assertEqual(len(rows), 1, st)
        self.assertEqual(rows[0]["location"], "Melbourne, FL")
        self.assertEqual(rows[0]["posted_date"], D(2))

    def test_bamboo(self):
        lst = {"result": [{"id": "170", "jobOpeningName": "Director of Operations", "location": {"city": "Cocoa", "state": "Florida"}}]}
        det = {"result": {"jobOpening": {"description": "<p>Ops</p>", "datePosted": D(4), "compensation": "$150,000 - $170,000",
                                         "employmentStatusLabel": "Full-Time", "jobOpeningShareUrl": "https://vayaspace.bamboohr.com/careers/170"}}}
        rows, st, _ = self.run_board({"name": "Vaya Space", "url": "https://vayaspace.bamboohr.com/careers"},
                                     {"/careers/list": lst, "/detail": det})
        self.assertEqual(len(rows), 1, st)
        self.assertEqual(rows[0]["location"], "Cocoa, Florida")

    def test_error_isolated(self):
        rows, st, _ = self.run_board({"name": "X", "url": "https://x.wd1.myworkdayjobs.com/Ext"}, {})
        self.assertFalse(st["ok"])
        self.assertTrue(st["errors"])

    def test_bot_check(self):
        waf = '<!DOCTYPE html><html><head><script>window.awsWafCookieDomainList = []; window.gokuProps = {}</script>'
        rows, st, _ = self.run_board({"name": "Jacobs", "url": "https://careers.jacobs.com/en_US/careers/sitemap.xml"},
                                     {"sitemap.xml": waf})
        self.assertFalse(st["ok"])
        self.assertIn("bot check", st["errors"][0])

    def test_off(self):
        rows, st, _ = self.run_board({"name": "Sidus", "url": "https://sidusspace.com/careers/", "type": "off", "note": "ADP"}, {})
        self.assertEqual(st.get("skipped"), "ADP")

    def test_usajobs(self):
        item = lambda pid, title, grade, plan="GS", remote=False: {"MatchedObjectDescriptor": {  # noqa: E731
            "PositionID": pid, "PositionTitle": title, "PositionURI": f"https://www.usajobs.gov/job/{pid}",
            "OrganizationName": "Space Launch Delta 45", "PositionLocation": [{"LocationName": "Patrick SFB, Florida"}],
            "PositionRemuneration": [{"MinimumRange": "150000", "MaximumRange": "195000"}], "PublicationStartDate": D(3),
            "ApplicationCloseDate": D(-10), "JobGrade": [{"Code": plan}],
            "UserArea": {"Details": {"LowGrade": grade, "HighGrade": grade, "JobSummary": "Lead IT.", "MajorDuties": ["Plan"],
                                     "RemoteIndicator": remote}}}}
        data = {"SearchResult": {"SearchResultItems": [item("1", "Supervisory IT Specialist", "15"), item("2", "IT Specialist", "12"),
                                                       item("3", "Director", "00", "ES")], "UserArea": {"NumberOfPages": "1"}}}
        fake = Fake({"data.usajobs.gov": data})
        old = sources.http
        sources.http = fake
        try:
            rows, st = sources.read_usajobs({"usajobs_api_key": "k", "usajobs_email": "a@b.c"}, ctx())
        finally:
            sources.http = old
        self.assertEqual(sorted(r["title"] for r in rows), ["Director (ES-00)", "Supervisory IT Specialist (GS-15)"], st)
        self.assertIn("LocationName=Melbourne%2C+Florida", fake.calls[0][0])


class JSearch(unittest.TestCase):
    def test_reader(self):
        data = {"status": "OK", "data": [
            {"job_id": "1", "employer_name": "L3Harris Technologies", "job_title": "Director, Secure Area IT", "job_city": "Melbourne",
             "job_state": "FL", "job_country": "US", "job_is_remote": False, "job_posted_at_datetime_utc": D(2) + "T12:00:00.000Z",
             "job_apply_link": "https://www.linkedin.com/jobs/view/1", "job_publisher": "LinkedIn",
             "apply_options": [{"publisher": "L3Harris", "apply_link": "https://careers.l3harris.com/en/job/melbourne/x/4832/1", "is_direct": True}],
             "job_description": "Lead classified IT. Active TS/SCI.", "job_min_salary": 150000, "job_max_salary": 200000, "job_salary_period": "YEAR",
             "job_employment_type": "FULLTIME"},
            {"job_id": "2", "employer_name": "Acme", "job_title": "Director of IT", "job_city": "Denver", "job_state": "CO",
             "job_is_remote": True, "job_posted_at_datetime_utc": D(1) + "T12:00:00.000Z", "job_apply_link": "https://acme.com/j/2"},
            {"job_id": "3", "employer_name": "Acme", "job_title": "Help Desk Analyst", "job_city": "Melbourne", "job_state": "FL"},
            {"job_id": "4", "employer_name": "Old Co", "job_title": "Director of IT", "job_city": "Melbourne", "job_state": "FL",
             "job_posted_at_datetime_utc": D(80) + "T12:00:00.000Z"},
            {"job_id": "5", "employer_name": "Far Co", "job_title": "Director of IT", "job_city": "Orlando", "job_state": "FL",
             "job_posted_at_datetime_utc": D(1) + "T12:00:00.000Z"}]}
        fake = Fake({"jsearch": data})
        old = sources.http
        sources.http = fake
        try:
            cfg = {"jsearch_api_key": "k", "jsearch_queries": "director it in {home}\ndirector it remote"}
            rows, st = sources.read_jsearch(cfg, ctx())
        finally:
            sources.http = old
        self.assertEqual(st["requests"], 2)
        self.assertEqual(sorted(r["title"] for r in rows), ["Director of IT", "Director, Secure Area IT"])
        a = next(r for r in rows if r["company"] == "L3Harris Technologies")
        self.assertTrue(a["link"].startswith("https://careers.l3harris.com"))  # employer link preferred
        self.assertEqual(a["salary"], "$150,000–$200,000")
        self.assertEqual(a["sources"], "JSearch (LinkedIn)")
        self.assertIn("query=director+it+in+Melbourne%2C+FL", fake.calls[0][0])
        self.assertIn("remote_jobs_only=true", fake.calls[1][0])

    def test_v2_shapes(self):
        self.assertEqual(sources.jsearch_items({"data": {"jobs": [{"a": 1}]}}), [{"a": 1}])
        self.assertEqual(sources.jsearch_posted({"job_posted_at": "3 days ago"}), D(3))
        self.assertEqual(sources.jsearch_posted({"job_posted_at": "5 hours ago"}), D(0))

    def test_no_key(self):
        rows, st = sources.read_jsearch({}, ctx())
        self.assertIn("JSearch API key", st["skipped"])

    def test_limit_stops(self):
        def boom(url, data=None, headers=None, timeout=30, as_json=True):
            raise sources.ReaderError("HTTP 429 from jsearch.p.rapidapi.com")
        old = sources.http
        sources.http = boom
        try:
            rows, st = sources.read_jsearch({"jsearch_api_key": "k", "jsearch_queries": "director it in {home}"}, ctx())
        finally:
            sources.http = old
        self.assertFalse(st["ok"])
        self.assertEqual(len(st["errors"]), 1)
        self.assertIn("limit", st["errors"][0])


if __name__ == "__main__":
    unittest.main()
