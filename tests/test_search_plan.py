import unittest
from unittest import mock

import sources

PROFILE = {"home_location": "Melbourne, FL", "radius_miles": 30}


def ctx(**profile):
    return sources.Context({}, {**PROFILE, **profile})


def plan(phrases, **profile):
    return sources.jsearch_plan({"jsearch_what": "\n".join(phrases), "jsearch_budget": 100000}, ctx(**profile))


class SearchPlan(unittest.TestCase):
    def test_all_modes_home_areas_remote(self):
        p = plan(["CISO"], work_modes="Hybrid, Remote", search_areas="Orlando, FL\nmelbourne, fl\nHuntsville, AL;Orlando, FL")
        self.assertEqual([s["q"] for s in p["searches"]],
                         ["CISO in Melbourne, FL", "CISO in Orlando, FL", "CISO in Huntsville, AL", "CISO remote"])
        self.assertEqual([s["place"] for s in p["searches"]], ["Melbourne, FL", "Orlando, FL", "Huntsville, AL", ""])

    def test_remote_only_skips_areas(self):
        p = plan(["CISO"], work_modes="Remote", search_areas="Orlando, FL")
        self.assertEqual([s["q"] for s in p["searches"]], ["CISO remote"])

    def test_no_home_note(self):
        p = sources.jsearch_plan({"jsearch_what": "CISO"}, sources.Context({}, {"work_modes": "On-site"}))
        self.assertEqual(p["searches"], [])
        self.assertEqual(p["notes"], [sources.SEARCH_NO_HOME_NOTE])

    def test_empty_modes_means_all(self):
        self.assertEqual(len(plan(["CISO"], work_modes="  ")["searches"]), 2)


class Radius(unittest.TestCase):
    def test_conversion_and_bounds(self):
        self.assertEqual(sources.radius_km(30), 48)
        self.assertEqual(sources.radius_km(5), 8)
        self.assertEqual(sources.radius_km(200), 322)
        self.assertIsNone(sources.radius_km(0))
        self.assertIsNone(sources.radius_km(400))
        self.assertIsNone(sources.radius_km("x"))

    def test_sent_near_home_only(self):
        calls = []

        def fake(url, headers=None, timeout=None):
            calls.append(url)
            return {"data": []}
        with mock.patch.object(sources, "http", fake):
            sources._jsearch_call({"jsearch_api_key": "k"}, "CISO in Melbourne, FL", False, radius_km=48)
            sources._jsearch_call({"jsearch_api_key": "k"}, "CISO remote", True, radius_km=48)
        self.assertIn("radius=48", calls[0])
        self.assertNotIn("radius=", calls[1])


class Areas(unittest.TestCase):
    def test_area_in_other_state_counts_as_local(self):
        c = ctx(work_modes="On-site", search_areas="Huntsville, AL")
        self.assertEqual(c.where("Huntsville, AL"), "local")
        self.assertEqual(c.where("Melbourne, FL"), "local")
        self.assertIsNone(c.where("Birmingham, AL"))
        self.assertIsNone(ctx(work_modes="On-site").where("Huntsville, AL"))


if __name__ == "__main__":
    unittest.main()
