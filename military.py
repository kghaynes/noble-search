"""Military background: more fields and what can be worked out from them (spec section 24).

Pure functions over the profile dict and today's date. The federal-hiring fields (veterans' preference,
30% disability rating) never go into AI prompts: they only choose USAJOBS hiring paths.
"""
import re
from datetime import date

STATUSES = {"active": "Active duty", "reserve": "Reserve", "guard": "National Guard", "retired": "Retired",
            "separated": "Separated (veteran)", "transitioning": "Transitioning (separation date set)"}
POLYGRAPHS = {"": "", "ci": "CI", "full": "Full-scope"}   # as written on resumes
CLEARANCE_STATUSES = {"": "Not set", "active": "Active", "current": "Current (inactive, can be reinstated)",
                      "expired": "Expired"}
VET_PREFERENCE = {"": "None", "5": "5-point", "10": "10-point"}

# About-equal civilian grade for a pay grade (DoD protocol equivalency), used only to suggest a USAJOBS minimum
# grade. General/flag officers map to GS-15 (SES jobs are searched separately).
GS_EQUIVALENT = {"E-4": 5, "E-5": 6, "E-6": 7, "E-7": 9, "E-8": 11, "E-9": 12,
                 "W-1": 9, "W-2": 11, "W-3": 12, "W-4": 13, "W-5": 13,
                 "O-1": 7, "O-2": 9, "O-3": 11, "O-4": 13, "O-5": 14, "O-6": 15,
                 "O-7": 15, "O-8": 15, "O-9": 15, "O-10": 15}
# civilian level wording and the title-matching seniority preset it suggests
LEVEL_BY_GRADE = [
    (("O-7", "O-8", "O-9", "O-10"), "Executive (C-suite, Vice President)", "executive"),
    (("O-5", "O-6"), "Senior leader (Director, Senior Director, Vice President)", "executive"),
    (("O-4", "W-4", "W-5", "E-9"), "Senior manager (Senior Manager, Director)", "executive"),
    (("O-3", "W-2", "W-3", "E-7", "E-8"), "Manager (Manager, Senior Manager)", ""),
    (("O-1", "O-2", "W-1", "E-5", "E-6"), "Team lead / supervisor", ""),
]
# professional military education -> how civilian employers read it (first match wins)
PME_CIVILIAN = [
    (("war college", "senior service college", "eisenhower school", "industrial college", "national defense university"),
     "Senior service college (master's-level strategy and executive leadership)"),
    (("command and general staff", "cgsc", "intermediate level education", "command and staff college",
      "air command and staff", "naval command and staff"),
     "Intermediate service college (graduate-level leadership and operations)"),
    (("sergeants major academy", "senior enlisted academy", "chief master sergeant leadership", "sergeants major course"),
     "Senior enlisted academy (executive-level leadership education)"),
    (("captains career course", "squadron officer school", "expeditionary warfare school", "career course"),
     "Officer career course (management and operations)"),
    (("jpme", "joint professional military education", "joint and combined warfighting"),
     "Joint Professional Military Education (joint operations and strategy)"),
    (("advanced leader course", "senior leader course", "nco academy", "senior nco academy"),
     "Senior NCO leadership course (supervision and operations management)"),
]
SKILLBRIDGE_DAYS = 180      # DoD SkillBridge: up to 180 days before separation
VET_PATH_DAYS = 120         # VOW Act: apply as a veteran up to 120 days before separation
VRA_YEARS = 3               # Veterans Recruitment Appointment: within 3 years of separation
REINSTATE_MONTHS = 24       # a clearance can usually be reinstated within 24 months of leaving

_GRADE_RE = re.compile(r"\b([EOW])\s*-?\s*(\d{1,2})\b", re.I)


def pay_grade(rank):
    """'Colonel (O-6)' -> 'O-6'; '' when no pay grade is written."""
    m = _GRADE_RE.search(str(rank or ""))
    if not m:
        return ""
    g = f"{m.group(1).upper()}-{int(m.group(2))}"
    return g if g in GS_EQUIVALENT or g in ("E-1", "E-2", "E-3") else ""


def month(s):
    """'2024-06' or '2024-06-30' -> date(2024, 6, 1); None if unreadable."""
    m = re.match(r"^\s*(\d{4})-(\d{1,2})", str(s or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return None
    return date(int(m.group(1)), int(m.group(2)), 1)


def _months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def service_years(p):
    """'2004 - 2024 (20 years)', '2004 - present' or ''. Still serving (or no end date): '... - present'."""
    start, end = month(p.get("mil_entry_date")), month(p.get("mil_separation_date"))
    if not start:
        return ""
    if end is None or (p.get("mil_status") or "") in ("active", "reserve", "guard"):
        return f"{start.year} - present"
    return f"{start.year} - {end.year} ({max(0, _months_between(start, end)) // 12} years)"


def pme_civilian(line):
    t = str(line or "").lower()
    for words, text in PME_CIVILIAN:
        if any(w in t for w in words):
            return text
    return ""


def _lines(v):
    return [x.strip() for x in str(v or "").splitlines() if x.strip()]


def clearance_text(p):
    """The clearance as shown in prompts and resumes: the structured fields when set, else the free text."""
    level = str(p.get("clearance_level") or "").strip()
    if not level:
        return str(p.get("clearance") or "").strip()
    out = level
    poly = POLYGRAPHS.get(p.get("clearance_poly") or "", "")
    if poly:
        out += f" with {poly} polygraph"
    inv = month(p.get("clearance_investigation"))
    if inv:
        out += f", investigation {inv.year}"
    st = p.get("clearance_status") or ""
    if st:
        out += f", {st}"
    return out


def derive(p, today):
    """What the app suggests from the military fields (today: a date). Keys are always present."""
    grade = pay_grade(p.get("mil_rank"))
    level, preset = "", ""
    for grades, text, pre in LEVEL_BY_GRADE:
        if grade in grades:
            level, preset = text, pre
            break
    status = p.get("mil_status") or ""
    sep = month(p.get("mil_separation_date"))
    out = {"pay_grade": grade, "gs_equivalent": GS_EQUIVALENT.get(grade), "civilian_level": level,
           "level_preset": preset, "service_years": service_years(p),
           "clearance_wording": "", "clearance_note": "", "skillbridge": "", "vra": False, "hiring_paths": []}
    # clearance currency
    level_c = str(p.get("clearance_level") or "").strip()
    chosen = p.get("clearance_status") or ""
    if level_c and chosen == "active":
        out["clearance_wording"] = f"Active {level_c}"
    elif level_c and chosen == "current":
        out["clearance_wording"] = f"{level_c} (current; eligible for reinstatement)"
    elif level_c and chosen == "expired":
        out["clearance_wording"] = f"Previously held {level_c}"
    elif level_c:   # not chosen: work it out from service status and separation date
        serving = status in ("active", "reserve", "guard") or (status == "transitioning" and (not sep or sep > today))
        if serving:
            out["clearance_wording"] = f"Active {level_c}"
        elif sep:
            left = _months_between(sep, today)
            until = _add_months(sep, REINSTATE_MONTHS)
            if left <= REINSTATE_MONTHS:
                out["clearance_wording"] = f"{level_c} (current; eligible for reinstatement)"
                out["clearance_note"] = f"Can usually be reinstated until {until.strftime('%B %Y')} ({REINSTATE_MONTHS} months after leaving service)."
            else:
                out["clearance_wording"] = f"Previously held {level_c}"
                out["clearance_note"] = "More than 24 months since leaving service: describe it as previously held."
    # SkillBridge and veteran windows
    if sep and sep > today and status in ("active", "transitioning", "reserve", "guard"):
        days = (sep - today).days
        if days <= SKILLBRIDGE_DAYS:
            out["skillbridge"] = f"Inside the SkillBridge window ({days} days to separation)."
        elif days <= SKILLBRIDGE_DAYS + 180:
            out["skillbridge"] = f"SkillBridge opens {SKILLBRIDGE_DAYS} days before separation (in {days - SKILLBRIDGE_DAYS} days)."
    veteran = status in ("retired", "separated") or (status == "transitioning" and sep and (sep - today).days <= VET_PATH_DAYS)
    if status in ("retired", "separated") and sep and sep <= today and _months_between(sep, today) < VRA_YEARS * 12:
        out["vra"] = True
    paths = []
    if veteran or str(p.get("vet_preference") or ""):
        paths.append("vet")
    if status in ("reserve", "guard"):
        paths.append("nguard")
    if str(p.get("disability_30") or "").lower() in ("1", "true", "yes", "on"):
        paths.append("disability")
    out["hiring_paths"] = paths
    return out


def military_text(p):
    """Plain-text military background block for AI prompts ('' if none given). Federal-hiring fields are never
    included."""
    lines = [f"{lbl}: {str(p.get(k) or '').strip()}" for k, lbl in (
        ("mil_branch", "Branch"), ("mil_rank", "Highest rank / pay grade"),
        ("mil_codes", "Occupation codes (MOS/AFSC/NEC/designator)"),
        ("mil_skill_ids", "Skill identifiers, qualifications, schools")) if str(p.get(k) or "").strip()]
    if p.get("mil_status") in STATUSES:
        lines.append(f"Service status: {STATUSES[p['mil_status']]}")
    years = service_years(p)
    if years:
        lines.append(f"Years of service: {years}")
    for k, lbl in (("mil_highest_position", "Highest leadership position"),
                   ("mil_largest_org", "Largest organization led (people, budget, equipment)")):
        if str(p.get(k) or "").strip():
            lines.append(f"{lbl}: {str(p[k]).strip()}")
    pme = _lines(p.get("mil_education"))
    if pme:
        lines.append("Military education: " + "; ".join(x + (f" ({pme_civilian(x)})" if pme_civilian(x) else "") for x in pme))
    for k, lbl in (("mil_joint", "Joint qualification"),):
        if str(p.get(k) or "").strip():
            lines.append(f"{lbl}: {str(p[k]).strip()}")
    for k, lbl in (("mil_8140", "DoD 8140/8570 cyber workforce roles"), ("mil_certs", "Military-earned certifications"),
                   ("mil_languages", "Languages")):
        items = _lines(p.get(k))
        if items:
            lines.append(f"{lbl}: " + "; ".join(items))
    return "\n".join(lines)
