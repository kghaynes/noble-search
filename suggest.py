"""AI helpers for first-time setup: suggest nearby towns and job-board searches from the Profile.

The suggestions are only put into the text boxes on the Search page; nothing is saved until
the user clicks Save, so they can review and edit first.
"""
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


def _profile_text(p, inventory):
    parts = [f"Home: {p.get('home_location') or p.get('city_state') or '(not set)'}",
             f"Radius: {p.get('radius_miles') or 30} miles",
             f"Work modes accepted: {p.get('work_modes') or 'any'}",
             f"Target levels: {p.get('levels') or '(not set)'}",
             f"Target fields/lanes: {p.get('lanes') or '(not set)'}",
             f"Notes: {p.get('notes') or ''}"]
    if inventory:
        parts.append("Career summary (first part of their inventory):\n" + inventory[:3000])
    return "\n".join(parts)


def suggest(what):
    """what = 'towns' | 'searches'. Returns {ok, text, items, detail}."""
    p = ps.get_profile()
    settings = ps.get_settings(include_secret=True)
    if what == "towns":
        home = (p.get("home_location") or p.get("city_state") or "").strip()
        if not home:
            raise ValueError("Enter your home town or address on the Profile page first.")
        system, user = TOWNS_SYSTEM, f"Home location: {home}\nRadius: {p.get('radius_miles') or 30} miles"
    elif what == "searches":
        if not (p.get("levels") or p.get("lanes") or ps.get_inventory().strip()):
            raise ValueError("Fill in target levels and fields (or your career inventory) on the Profile page first.")
        system, user = SEARCHES_SYSTEM, _profile_text(p, ps.get_inventory())
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
