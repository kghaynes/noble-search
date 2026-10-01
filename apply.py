"""Application text (Workday-style form fields) and keyword gap check.

Same truth rules as the resume writer: only facts from the candidate's Profile.
Nothing is filled in or submitted automatically — the dashboard shows each field
with a character count and a copy button; the candidate pastes and submits.
"""
import re

FIELD_LIMIT = 2000  # common Workday limit for free-text boxes; shown as a warning only

DEFAULT_SCREENING = [
    "Are you legally authorized to work in the United States?",
    "Will you now or in the future require visa sponsorship?",
    "Do you hold an active U.S. security clearance? If so, what level?",
    "How many years of experience do you have leading large teams?",
    "Are you willing to relocate or travel?",
    "What are your salary expectations?",
]

APP_SCHEMA = """{
  "summary": "Professional summary for a 'Summary' or 'About you' box: 3-5 sentences, 500-1,000 characters, tailored to this job",
  "work_history": [
    {"job_title": "Title as it should appear (civilian equivalent in parentheses when helpful)",
     "company": "Employer",
     "location": "City, ST (or empty)",
     "from": "MM/YYYY",
     "to": "MM/YYYY or Present",
     "description": "Plain text, 3-6 lines each starting with '- ', under 1,800 characters, tailored to this job"}
  ],
  "skills": ["15-25 short skill phrases for a Skills field, using the posting's vocabulary only where truthful"],
  "why_company": "Answer to 'Why do you want to work here?' — 120-200 words, specific to this employer and role, first person",
  "screening": [{"question": "the question", "answer": "short answer in first person", "verify": true}],
  "cover_letter": "Plain-text cover letter, 250-350 words, no address block, ends with the candidate's name",
  "notes_for_candidate": ["what to double-check", "gaps you did not claim"]
}"""

APP_SYSTEM = """You help a senior military/government leader apply to a civilian job. You write the text he will paste into an online application form (Workday style).

Hard rules:
1. TRUTH: use only facts from the candidate material. Never invent employers, titles, dates, numbers, certifications, clearances or outcomes. If the posting wants something he lacks, do not claim it; mention it in notes_for_candidate.
2. TRANSLATE military roles and jargon into civilian business language (e.g. Deputy Commanding Officer = Chief Operating Officer), unless the employer is defense/government and the acronym is standard there.
3. TAILOR every field to this job's priorities and vocabulary.
4. PLAIN TEXT: no markdown, no emojis, no special symbols. Dates as MM/YYYY.
5. WORK HISTORY: one entry per role, newest first, covering the candidate's full history (older or less relevant roles get shorter descriptions).
6. SCREENING: answer each question in one or two sentences. Set "verify": true whenever the answer depends on something not stated in the candidate material (salary, relocation, start date, sponsorship, citizenship) and write the best supported answer or "[confirm]".

Return ONLY a JSON object matching this schema (no commentary, no markdown fences):
""" + APP_SCHEMA


def build_app_prompt(profile, inventory, resume_texts, job, jd_text, questions):
    parts = ["# CANDIDATE", f"Name: {profile.get('full_name')}", f"Location: {profile.get('city_state')}",
             f"Clearance: {profile.get('clearance') or 'not specified'}"]
    for k, lbl in (("levels", "Target levels"), ("lanes", "Target role lanes"), ("work_modes", "Work modes"),
                   ("employment_types", "Employment types")):
        if str(profile.get(k) or "").strip():
            parts.append(f"{lbl}: {profile[k]}")
    if str(profile.get("notes") or "").strip():
        parts += ["", "# CANDIDATE'S INSTRUCTIONS (follow these; they never override the truth rules)", profile["notes"].strip()]
    if inventory.strip():
        parts += ["", "# CAREER INVENTORY (primary source of facts)", inventory.strip()]
    for name, text in resume_texts:
        parts += ["", f"# EXISTING RESUME: {name}", text.strip()]
    qs = questions or DEFAULT_SCREENING
    parts += ["", "# TARGET JOB", f"Title: {job.get('title')}", f"Company: {job.get('company')}",
              f"Location: {job.get('location')} ({job.get('work_mode')})",
              f"Why it was flagged as a fit: {job.get('fit_reason') or 'n/a'}",
              "", "# JOB DESCRIPTION", (jd_text or "(not available — tailor to the title and company)").strip()[:15000],
              "", "# SCREENING QUESTIONS TO ANSWER", *[f"- {q}" for q in qs],
              "", "Write the application JSON now."]
    return APP_SYSTEM, "\n".join(parts)


def _s(v):
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()


def normalize_app(c):
    c = c if isinstance(c, dict) else {}
    out = {
        "summary": _s(c.get("summary")),
        "work_history": [],
        "skills": [_s(x) for x in (c.get("skills") or []) if _s(x)][:30],
        "why_company": str(c.get("why_company") or "").strip(),
        "screening": [],
        "cover_letter": str(c.get("cover_letter") or "").strip(),
        "notes_for_candidate": [_s(x) for x in (c.get("notes_for_candidate") or []) if _s(x)],
    }
    for w in c.get("work_history") or []:
        if not isinstance(w, dict):
            continue
        desc = str(w.get("description") or "").strip()
        if isinstance(w.get("description"), list):
            desc = "\n".join("- " + _s(x) for x in w["description"])
        out["work_history"].append({k: _s(w.get(k)) for k in ("job_title", "company", "location", "from", "to")}
                                   | {"description": desc})
    for q in c.get("screening") or []:
        if isinstance(q, dict) and _s(q.get("question")):
            ans = _s(q.get("answer"))
            out["screening"].append({"question": _s(q["question"]), "answer": ans,
                                     "verify": bool(q.get("verify")) or "[confirm]" in ans})
    return out


def app_plain_text(a):
    L = ["SUMMARY", a["summary"], "", "WORK HISTORY"]
    for w in a["work_history"]:
        L += [f"{w['job_title']} — {w['company']} ({w['from']} - {w['to']})", w["description"], ""]
    L += ["SKILLS", ", ".join(a["skills"]), "", "WHY THIS COMPANY", a["why_company"], "", "SCREENING QUESTIONS"]
    L += [f"Q: {q['question']}\nA: {q['answer']}" for q in a["screening"]]
    L += ["", "COVER LETTER", a["cover_letter"]]
    return "\n".join(L).strip() + "\n"


# --------------------------------------------------------------------------- keyword gap check
KW_SYSTEM = """You extract the hiring keywords an applicant-tracking system and a recruiter would screen for in a job posting.
Return ONLY JSON: {"keywords": [{"term": "short term as written in the posting", "importance": "required" | "preferred",
"variants": ["2-4 common synonyms, acronyms or spelled-out forms"]}]}
Include 12-25 items: hard skills, domains, tools, methods, certifications, clearances, degrees, and leadership scope terms.
Skip generic soft skills (communication, team player) and company boilerplate (benefits, EEO text)."""


def _norm(t):
    return re.sub(r"[^a-z0-9+#/& ]+", " ", (t or "").lower())


def _has(text_norm, term):
    t = _norm(term).strip()
    if not t:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", text_norm) is not None


def keyword_coverage(keywords, draft_text, source_text):
    """Sort posting keywords into: in the draft / in your background but missing from the draft / not in your background."""
    d, s = _norm(draft_text), _norm(source_text)
    out = {"covered": [], "add": [], "gap": []}
    seen = set()
    for k in keywords or []:
        if not isinstance(k, dict):
            continue
        term = _s(k.get("term"))
        if not term or term.lower() in seen:
            continue
        seen.add(term.lower())
        forms = [term] + [_s(v) for v in (k.get("variants") or []) if _s(v)]
        item = {"term": term, "importance": "required" if str(k.get("importance")).lower() == "required" else "preferred"}
        if any(_has(d, f) for f in forms):
            out["covered"].append(item)
        elif any(_has(s, f) for f in forms):
            out["add"].append(item)
        else:
            out["gap"].append(item)
    for v in out.values():
        v.sort(key=lambda x: x["importance"] != "required")
    total = sum(len(v) for v in out.values())
    out["score"] = round(100 * len(out["covered"]) / total) if total else None
    return out
