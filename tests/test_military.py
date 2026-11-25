from datetime import date

import military as M
import profile_store as ps
import sources

T = date(2026, 11, 25)


def test_pay_grade_and_equivalents():
    assert M.pay_grade("Colonel (O-6)") == "O-6"
    assert M.pay_grade("Master Sergeant (E8)") == "E-8"
    assert M.pay_grade("CW3") == ""            # no pay grade written as W-3
    d = M.derive({"mil_rank": "Lieutenant Colonel (O-5)"}, T)
    assert d["gs_equivalent"] == 14 and d["level_preset"] == "executive"


def test_service_years():
    assert M.service_years({"mil_entry_date": "1994-05", "mil_separation_date": "2024-06", "mil_status": "retired"}) == "1994 - 2024 (30 years)"
    assert M.service_years({"mil_entry_date": "2010-01", "mil_status": "active"}) == "2010 - present"
    assert M.service_years({}) == ""


def test_clearance_currency():
    base = {"clearance_level": "TS/SCI", "mil_status": "separated"}
    assert M.derive({**base, "mil_separation_date": "2025-06"}, T)["clearance_wording"].endswith("eligible for reinstatement)")
    assert M.derive({**base, "mil_separation_date": "2023-06"}, T)["clearance_wording"] == "Previously held TS/SCI"
    assert M.derive({**base, "mil_separation_date": "2023-06", "clearance_status": "active"}, T)["clearance_wording"] == "Active TS/SCI"
    assert M.derive({"clearance_level": "Secret", "mil_status": "active"}, T)["clearance_wording"] == "Active Secret"


def test_windows_and_paths():
    d = M.derive({"mil_status": "transitioning", "mil_separation_date": "2027-03"}, T)
    assert d["skillbridge"].startswith("Inside the SkillBridge window") and d["hiring_paths"] == ["vet"]
    assert M.derive({"mil_status": "transitioning", "mil_separation_date": "2027-09"}, T)["hiring_paths"] == []
    assert M.derive({"mil_status": "retired", "mil_separation_date": "2025-01"}, T)["vra"] is True
    assert M.derive({"mil_status": "guard", "disability_30": "true"}, T)["hiring_paths"] == ["nguard", "disability"]


def test_military_text_never_has_federal_fields():
    p = {"mil_branch": "U.S. Army", "mil_education": "U.S. Army War College, 2015", "vet_preference": "10", "disability_30": "true"}
    t = M.military_text(p)
    assert "War College, 2015 (Senior service college" in t
    assert "10" not in t and "disab" not in t.lower()


def test_clearance_written_from_structured_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "PROFILE_JSON", str(tmp_path / "profile.json"))
    p = ps.save_profile({"clearance": "old text", "clearance_level": "TS/SCI", "clearance_poly": "ci",
                         "clearance_investigation": "2021-03", "clearance_status": "active"})
    assert p["clearance"] == "TS/SCI with CI polygraph, investigation 2021, active"


def test_usajobs_hiring_path_queries():
    class Ctx:
        home_city, radius, allow_local, allow_remote, hiring_paths = "Melbourne, FL", 35, True, True, ["vet"]
    qs, _ = sources.usajobs_queries({}, Ctx())
    assert len(qs) == 4 and qs[2]["_hiring_path"] == "vet"
    params = sources.usajobs_params(qs[2], 30, 1)
    assert params["HiringPath"] == "vet" and "WhoMayApply" not in params
    assert sources.usajobs_params(qs[0], 30, 1)["WhoMayApply"] == "public"
