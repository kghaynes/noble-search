import json
import unittest

import sources

SAMPLE = {"PositionURI": "https://www.usajobs.gov/job/1", "PositionTitle": "Supervisory IT Specialist",
          "OrganizationName": "NASA", "PositionLocation": [{"LocationName": "Kennedy Space Center, Florida"}],
          "JobGrade": [{"Code": "GS"}], "PositionRemuneration": [{"MinimumRange": "150000", "MaximumRange": "191900"}],
          "PublicationStartDate": "2026-11-10T00:00:00", "ApplicationCloseDate": "2026-11-24T23:59:59",
          "UserArea": {"Details": {"LowGrade": "15", "HighGrade": "15", "JobSummary": "Lead IT.", "MajorDuties": ["Plan", 3]}}}
CFG = {"title_include": "director\nchief", "title_fields": "it\ncyber", "usajobs_min_grade": 14}
PROFILE = {"home_location": "Melbourne, FL", "radius_miles": 50, "work_modes": "On-site, Remote"}


class Queries(unittest.TestCase):
    def test_near_remote_nationwide(self):
        ctx = sources.Context(CFG, PROFILE)
        q, note = sources.usajobs_queries({**CFG, "usajobs_nationwide": "all"}, ctx)
        self.assertEqual(q[0], {"LocationName": "Melbourne, Florida", "Radius": 50})
        self.assertEqual(q[1], {"RemoteIndicator": "True"})
        self.assertEqual(q[2], {"PayGradeLow": "13", "_nationwide": True})
        self.assertEqual(note, "")
        q, note = sources.usajobs_queries({**CFG, "usajobs_nationwide": "agencies"}, ctx)
        self.assertEqual(len(q), 2)
        self.assertEqual(note, sources.NOTE_NO_AGENCIES)
        self.assertNotIn("_nationwide", sources.usajobs_params(q[0], 90, 2))
        self.assertEqual(sources.usajobs_params(q[0], 90, 2)["DatePosted"], 60)


class Listing(unittest.TestCase):
    def test_senior_grade_stands_in_for_title(self):
        ctx = sources.Context(CFG, PROFILE)
        lst, stage = sources.usajobs_listing(SAMPLE, {"LocationName": "Melbourne, Florida", "Radius": 50}, CFG, ctx)
        self.assertEqual(stage, "kept")
        self.assertEqual((lst["title"], lst["where"], lst["salary"], lst["closing"]),
                         ("Supervisory IT Specialist (GS-15)", "local", "$150,000–$191,900", "2026-11-24"))
        self.assertIn("• Plan", lst["description"])

    def test_low_grade_dropped(self):
        d = json.loads(json.dumps(SAMPLE)); d["UserArea"]["Details"]["LowGrade"] = "11"
        self.assertEqual(sources.usajobs_listing(d, {"LocationName": "x"}, CFG, sources.Context(CFG, PROFILE)), (None, "title"))


if __name__ == "__main__":
    unittest.main()
