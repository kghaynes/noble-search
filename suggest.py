"""AI helpers for setup: suggest nearby towns, job-board searches and title lists from the Profile
(including military occupation codes, rank and skill identifiers).

The suggestions are only put into the text boxes on the Search page; nothing is saved until
the user clicks Save, so they can review and edit first.
"""
import re

import llm
import profile_store as ps

TOWNS_SYSTEM = """You list towns for a job search. Reply with JSON only: {"items": ["Town, ST", ...]}.
Rules: real incorporated cities, towns and well-known places (including military bases and
space/industrial centers where jobs are listed) within the given radius of the home location.
Use the format "Town, ST" with the two-letter US state code. Include the home town itself.
Order by size/importance for jobs. 8 to 40 items. No commentary."""

SEARCHES_SYSTEM = """You write job-board search phrases for a job seeker. Reply with JSON only:
{"items": ["...", ...]}.
Rules:
- 5 to 7 lines. Each line is one search a recruiter would type into Indeed or LinkedIn, 2-6 words,
  plain civilian job titles (translate military roles into civilian titles), no quotes, no boolean operators.
- Most lines end with " in {home}" (keep the literal text {home}; the app replaces it with the person's town).
- If the person accepts remote work, make 1 or 2 lines end with " remote" instead.
- Match the person's seniority and fields. Do not repeat near-identical phrases."""


TITLES_SYSTEM = """You are a senior recruiter who places transitioning military members and veterans in civilian jobs.
From the person's military background (branch, rank/pay grade, occupation codes such as Army MOS, Air Force AFSC,
Navy NEC/rating/designator, Marine MOS, skill identifiers such as ASI/SQI/SI), career inventory and target levels,
work out which CIVILIAN job titles they should search for. Reply with JSON only:
{"translations": ["<code or role> -> <civilian equivalent>", ...],
 "example_titles": ["...", ...],
 "levels": ["...", ...],
 "fields": ["...", ...],
 "skip": ["...", ...]}
Rules:
- translations: 2-8 short lines, e.g. "25A Signal Officer -> IT / network operations leader",
  "O-6 Deputy Commanding Officer of 5,000 people -> COO / VP level", "E-8 68W -> clinical operations manager".
- example_titles: 8-15 realistic civilian job titles at the right level, e.g. "Director of IT Operations".
- levels: 4-14 seniority words that appear in those titles, lowercase, 1-3 words each
  (e.g. "director", "vice president", "chief", "head of", "senior manager", "program manager", "superintendent").
  Pick the level from rank and scope: senior officers and senior NCOs with large scope -> director/VP/chief;
  mid-grade -> manager/senior manager/lead; junior -> specialist/analyst/coordinator/technician.
- fields: 5-20 function or domain words that appear in titles for this person's field, lowercase, 1-3 words
  (e.g. "information technology", "it", "cyber", "network", "security", "communications", "program", "operations").
  Use the occupation codes and skill identifiers to choose them. Do not include seniority words here.
- skip: 4-12 words for titles that would match the lists above but are wrong for this person
  (e.g. "sales", "nurse", "help desk", "intern", "software engineer" for a non-developer).
- Use only what the person's material supports; do not invent experience. No commentary."""


def _clean_terms(items, limit):
    """Lowercase plain phrases -> safe match lines (short acronyms get word boundaries)."""
    out, seen = [], set()
    parts = [y for x in items or [] for y in re.split(r"[/,;|]", str(x or ""))]
    for x in parts:
        s = re.sub(r"\s+", " ", x.strip().lower())
        s = re.sub(r"[^a-z0-9 &.'+#-]", "", s).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        line = re.escape(s).replace("\\ ", " ")
        if len(s) <= 4 and " " not in s:
            line = rf"\b{line}\b"
        out.append(line)
    return out[:limit]


INVENTORY_MAX = 20000   # ~5k tokens: the whole inventory for almost everyone
RESUME_MAX = 12000


def _career_text():
    """The person's career for suggestions: the full career inventory, or — if there is none yet —
    the text of their uploaded resumes (template resume first)."""
    inv = ps.get_inventory().strip()
    if inv:
        return "Career inventory:\n" + inv[:INVENTORY_MAX] + ("\n[…inventory shortened]" if len(inv) > INVENTORY_MAX else "")
    try:
        import resume   # imported here so suggest works even if python-docx is unavailable
        rs = sorted(ps.list_resumes(), key=lambda r: not r.get("is_template"))
        texts = []
        for r in rs[:2]:
            try:
                texts.append(f"Resume ({r['name']}):\n" + resume.extract_docx_text(ps.resume_path(r["name"])))
            except Exception:  # noqa: BLE001 — an unreadable file just isn't used
                continue
        return "\n\n".join(texts)[:RESUME_MAX]
    except Exception:  # noqa: BLE001
        return ""


def _profile_text(p, inventory):
    parts = [f"Home: {p.get('home_location') or p.get('city_state') or '(not set)'}",
             f"Radius: {p.get('radius_miles') or 30} miles",
             f"Work modes accepted: {p.get('work_modes') or 'any'}",
             f"Target levels: {p.get('levels') or '(not set)'}",
             f"Target fields/lanes: {p.get('lanes') or '(not set)'}",
             f"Notes: {p.get('notes') or ''}"]
    mil = ps.military_text(p)
    if mil:
        parts.append("Military background:\n" + mil)
    if inventory:
        parts.append(inventory)
    return "\n".join(parts)


def suggest(what):
    """what = 'towns' | 'searches' (-> {ok, text, items, detail}) or 'titles' (-> lists to pick from)."""
    p = ps.get_profile()
    settings = ps.get_settings(include_secret=True)
    if what == "towns":
        home = (p.get("home_location") or p.get("city_state") or "").strip()
        if not home:
            raise ValueError("Enter your home town or address on the Profile page first.")
        system, user = TOWNS_SYSTEM, f"Home location: {home}\nRadius: {p.get('radius_miles') or 30} miles"
    elif what == "titles":
        career = _career_text()
        if not (ps.military_text(p) or p.get("levels") or p.get("lanes") or career):
            raise ValueError("Fill in your military background, target levels and fields, or upload a resume / build your career inventory on the Profile page first.")
        try:
            raw, _prov, model = llm.complete(settings, TITLES_SYSTEM, _profile_text(p, career),
                                             json_mode=True, max_tokens=1500)
            d = llm.parse_json(raw)
        except llm.LLMError as e:
            raise ValueError(f"The AI model could not answer: {e}")
        except (ValueError, AttributeError):
            raise ValueError("The AI model gave an answer that could not be read. Try again.")
        res = {"ok": True,
               "translations": [str(x).strip() for x in d.get("translations") or [] if str(x).strip()][:8],
               "example_titles": [str(x).strip() for x in d.get("example_titles") or [] if str(x).strip()][:15],
               "levels": _clean_terms(d.get("levels"), 14), "fields": _clean_terms(d.get("fields"), 20),
               "skip": _clean_terms(d.get("skip"), 12)}
        res["detail"] = (f"Suggested by {model}. Tick what you want, then add it to your lists. "
                         "You can still edit every list yourself.")
        return res
    elif what == "searches":
        career = _career_text()
        if not (p.get("levels") or p.get("lanes") or ps.military_text(p) or career):
            raise ValueError("Fill in target levels and fields, your military background, or upload a resume / build your career inventory on the Profile page first.")
        system, user = SEARCHES_SYSTEM, _profile_text(p, career)
    else:
        raise ValueError("Unknown suggestion type")
    try:
        raw, _prov, model = llm.complete(settings, system, user, json_mode=True, max_tokens=1200)
        items = llm.parse_json(raw).get("items") or []
    except llm.LLMError as e:
        raise ValueError(f"The AI model could not answer: {e}")
    except (ValueError, AttributeError):
        raise ValueError("The AI model gave an answer that could not be read. Try again.")
    items = [str(x).strip() for x in items if str(x).strip()][:40]
    if what == "searches":
        items = [x if ("{home}" in x or "remote" in x.lower()) else f"{x} in {{home}}" for x in items]
    return {"ok": True, "items": items, "text": "\n".join(items),
            "detail": f"{len(items)} suggestions from {model}. Review them, then click Save search settings."}
