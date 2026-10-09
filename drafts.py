"""Draft generation: SQLite-backed queue + single background worker (serializes GPU/LLM use)."""
import json
import os
import queue
import re
import sqlite3
import threading
import traceback
from datetime import datetime, timezone

import apply
import applog
import llm
import profile_store as ps
import resume

DB_PATH = os.environ.get("DB_PATH", "/data/jobs.db")
DRAFT_DIR = os.path.join(os.path.dirname(DB_PATH) or ".", "drafts")
PROFILE_KEY = "__profile__"  # pseudo job_key for profile-level jobs (inventory builder)
KINDS = {"resume", "inventory", "application"}

_q = queue.Queue()
_lock = threading.Lock()
_current = {"id": None}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init():
    os.makedirs(DRAFT_DIR, exist_ok=True)
    with _lock, _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_key TEXT, kind TEXT, status TEXT, error TEXT DEFAULT '',
            provider TEXT, model TEXT, created TEXT, finished TEXT,
            jd_text TEXT DEFAULT '', content_json TEXT DEFAULT '', text TEXT DEFAULT '',
            docx_path TEXT DEFAULT '', ats_json TEXT DEFAULT '', facts_json TEXT DEFAULT '')""")
        c.execute("CREATE INDEX IF NOT EXISTS drafts_job ON drafts(job_key)")
        have = {r[1] for r in c.execute("PRAGMA table_info(drafts)")}
        for col in ("keywords_json", "extra_json"):
            if col not in have:
                c.execute(f"ALTER TABLE drafts ADD COLUMN {col} TEXT DEFAULT ''")
        # anything left running by a restart is dead
        c.execute("UPDATE drafts SET status='error', error='Interrupted by a restart — generate again.' "
                  "WHERE status IN ('queued','running')")
    threading.Thread(target=_worker, daemon=True).start()


def create(job_key, kind, jd_text="", provider=None, extra=None):
    if kind not in KINDS:
        raise ValueError("unknown draft kind")
    settings = ps.get_settings(include_secret=True)
    provider = provider if provider in ("ollama", "anthropic") else settings.get("provider", "anthropic")
    model = settings.get("anthropic_model") if provider == "anthropic" else settings.get("ollama_model")
    with _lock, _db() as c:
        cur = c.execute("INSERT INTO drafts (job_key, kind, status, provider, model, created, jd_text, extra_json) "
                        "VALUES (?,?,?,?,?,?,?,?)", (job_key, kind, "queued", provider, model, _now(), jd_text or "",
                                                     json.dumps(extra or {})))
        did = cur.lastrowid
    _q.put(did)
    return did


def _row(did):
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
    return dict(r) if r else None


def _update(did, **kw):
    sets = ", ".join(f"{k}=?" for k in kw)
    with _lock, _db() as c:
        c.execute(f"UPDATE drafts SET {sets} WHERE id=?", list(kw.values()) + [did])


def public(r, full=False):
    out = {k: r[k] for k in ("id", "job_key", "kind", "status", "error", "provider", "model", "created", "finished")}
    out["has_docx"] = bool(r.get("docx_path")) and os.path.exists(r["docx_path"])
    out["ats"] = json.loads(r["ats_json"]) if r.get("ats_json") else None
    out["facts"] = json.loads(r["facts_json"]) if r.get("facts_json") else []
    notes = []
    if r.get("content_json"):
        try:
            notes = json.loads(r["content_json"]).get("notes_for_candidate", [])
        except (json.JSONDecodeError, AttributeError):
            pass
    out["notes"] = notes
    out["keywords"] = json.loads(r["keywords_json"]) if r.get("keywords_json") else None
    if r.get("kind") == "application" and r.get("content_json"):
        try:
            out["app"] = json.loads(r["content_json"])
        except json.JSONDecodeError:
            out["app"] = None
    if full:
        out["text"] = r.get("text", "")
        out["jd_text"] = r.get("jd_text", "")
    return out


def list_for(job_key):
    with _lock, _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM drafts WHERE job_key=? ORDER BY id DESC", (job_key,))]
    return [public(r, full=True) for r in rows]


def get(did):
    r = _row(did)
    return (public(r, full=True), r) if r else (None, None)


def delete(did):
    r = _row(did)
    if not r:
        return
    if r.get("docx_path") and os.path.exists(r["docx_path"]):
        os.remove(r["docx_path"])
    with _lock, _db() as c:
        c.execute("DELETE FROM drafts WHERE id=?", (did,))


def delete_for_job(job_key):
    """Remove every draft (and its .docx file) for a job."""
    with _lock, _db() as c:
        rows = [dict(r) for r in c.execute("SELECT id, docx_path FROM drafts WHERE job_key=?", (job_key,))]
    for r in rows:
        if r.get("docx_path") and os.path.exists(r["docx_path"]):
            try:
                os.remove(r["docx_path"])
            except OSError:
                pass
    with _lock, _db() as c:
        c.execute("DELETE FROM drafts WHERE job_key=?", (job_key,))
    return len(rows)


def list_all():
    """All resume/application drafts (not the profile inventory drafts), newest first, without full text."""
    with _lock, _db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM drafts WHERE kind IN ('resume','application') ORDER BY id DESC")]
    return [public(r) for r in rows]


def counts_by_job():
    with _lock, _db() as c:
        return {r[0]: r[1] for r in c.execute(
            "SELECT job_key, COUNT(*) FROM drafts WHERE kind='resume' AND status='done' GROUP BY job_key")}


def queue_state():
    with _lock, _db() as c:
        n = c.execute("SELECT COUNT(*) FROM drafts WHERE status IN ('queued','running')").fetchone()[0]
    return {"pending": n, "running_id": _current["id"]}


# ---------------------------------------------------------------- worker

def _sources():
    inv = ps.get_inventory()
    texts = []
    for r in ps.list_resumes():
        try:
            texts.append((r["name"], resume.extract_docx_text(os.path.join(ps.RESUME_DIR, r["name"]))))
        except Exception:  # noqa: BLE001 — skip unreadable files
            continue
    return inv, texts


def _job(job_key):
    with _lock, _db() as c:
        r = c.execute("SELECT * FROM jobs WHERE job_key=?", (job_key,)).fetchone()
    return dict(r) if r else None


def _keywords(r, settings, draft_text, source_text):
    """Posting keywords vs. the draft and the Profile. Best effort: a failure never fails the draft."""
    if len((r.get("jd_text") or "").strip()) < 300:
        return None
    try:
        raw, _, _ = llm.complete(settings, apply.KW_SYSTEM, "# JOB POSTING\n" + r["jd_text"][:15000] + "\n\nReturn the JSON now.",
                                 provider=r["provider"], json_mode=True, max_tokens=1500)
        kws = llm.parse_json(raw).get("keywords") or []
        return apply.keyword_coverage(kws, draft_text, source_text)
    except Exception as e:  # noqa: BLE001
        return {"error": f"Keyword check failed: {e}"[:300]}


def _complete_json(settings, system, user, provider, kind, did, max_tokens=8000):
    """Ask for JSON; if the reply can't be read even after repair, ask once more. Returns (data, provider, model)."""
    last = None
    for attempt in range(2):
        prompt = user if attempt == 0 else (user + "\n\nYour previous reply was not valid JSON. "
                                            "Return ONLY one complete, valid JSON object — no other text.")
        raw, prov, model = llm.complete(settings, system, prompt, provider=provider, json_mode=True, max_tokens=max_tokens)
        try:
            return llm.parse_json(raw), prov, model
        except llm.LLMError as e:
            last = e
            saved = applog.save_bad_reply(f"{kind}-draft", raw)
            applog.warn("draft", f"{kind} draft #{did}: {e} (try {attempt + 1} of 2, model {model}"
                                 + (f", reply saved as logs/{saved}" if saved else "") + ")")
    raise last


def _run_application(did, r, settings):
    job = _job(r["job_key"])
    if not job:
        raise RuntimeError("That job is no longer in the dashboard.")
    profile = ps.get_profile()
    inv, texts = _sources()
    if not inv.strip() and not texts:
        raise RuntimeError("Your Profile is empty. Upload a resume and/or fill in the career inventory first.")
    extra = json.loads(r.get("extra_json") or "{}")
    system, user = apply.build_app_prompt(profile, inv, texts, job, r["jd_text"], extra.get("questions") or [])
    data, provider, model = _complete_json(settings, system, user, r["provider"], "application", did)
    app = apply.normalize_app(data)
    if not app["work_history"] and not app["summary"]:
        raise RuntimeError("The model returned empty application text. Try again or switch models.")
    text = apply.app_plain_text(app)
    source_text = inv + "\n" + "\n".join(t for _, t in texts)
    lines = [ln for ln in re.sub(r"\b\d{1,2}/\d{4}\b", " ", text).splitlines() if ln.strip()]  # MM/YYYY dates aren't claims
    facts = resume.fact_check({"summary": "", "highlights": lines, "competencies": [], "experience": [],
                               "additional_roles": [], "headline": "", "credentials_line": "", "education": [],
                               "certifications": "", "recognitions": "", "technology_skills": ""}, source_text)
    kw = _keywords(r, settings, text, source_text)
    _update(did, status="done", finished=_now(), provider=provider, model=model, content_json=json.dumps(app),
            text=text, facts_json=json.dumps(facts), keywords_json=json.dumps(kw) if kw else "")


def _run_resume(did, r, settings):
    job = _job(r["job_key"])
    if not job:
        raise RuntimeError("That job is no longer in the dashboard.")
    profile = ps.get_profile()
    inv, texts = _sources()
    if not inv.strip() and not texts:
        raise RuntimeError("Your Profile is empty. Upload a resume and/or fill in the career inventory first.")
    system, user = resume.build_resume_prompt(profile, inv, texts, job, r["jd_text"], profile.get("template_resume") or "")
    data, provider, model = _complete_json(settings, system, user, r["provider"], "resume", did)
    content = resume.normalize_resume(data)
    if not content["experience"]:
        raise RuntimeError("The model returned a resume with no experience section. Try again or switch models.")
    source_text = inv + "\n" + "\n".join(t for _, t in texts)
    facts = resume.fact_check(content, source_text)
    safe_company = "".join(ch for ch in (job.get("company") or "job") if ch.isalnum())[:30]
    out = os.path.join(DRAFT_DIR, f"draft-{did}-{safe_company}.docx")
    layout = resume.build_docx(content, profile, ps.template_path(), out) or {}
    ats = resume.ats_check(out, profile)
    if layout.get("mode") == "template":
        ats["checks"].insert(0, {"name": "Layout copied from your template resume", "ok": True, "level": "ok",
                                 "detail": "headings, lines, bullets and job lines match it"})
    else:
        ats["checks"].insert(0, {"name": "Layout copied from your template resume", "ok": False, "level": "warn",
                                 "detail": "Used the built-in design because " + (layout.get("reason") or "the template could not be read")})
        applog.info("drafts", f"resume draft #{did}: built-in layout ({layout.get('reason')})")
    text = resume.to_plain_text(content, profile, layout.get("names"))
    kw = _keywords(r, settings, text, source_text)
    _update(did, status="done", finished=_now(), provider=provider, model=model,
            content_json=json.dumps(content), text=text,
            docx_path=out, ats_json=json.dumps(ats), facts_json=json.dumps(facts),
            keywords_json=json.dumps(kw) if kw else "")


INVENTORY_SYSTEM = """You turn resumes into a detailed CAREER INVENTORY in Markdown that a resume writer will use as the single source of truth.
Rules: use ONLY facts in the resumes — never invent numbers, titles, dates or outcomes. Keep every metric exactly as written.
Structure:
# Career Inventory
## Contact & clearance
## Summary of strengths (bullets)
## Roles (newest first) — for each: ### Title — Organization (Mon YYYY - Mon YYYY), then
- Civilian equivalent title:
- Scope: people / budget / users / sites / customers
- Accomplishments: bullets with metrics
- Keywords: comma-separated
## Education
## Certifications
## Awards & recognitions
## Volunteer / board roles
## Technical skills
## Military-to-civilian translation notes (acronyms spelled out, civilian equivalents)
## Gaps / to fill in (things a writer would want that are missing — questions for the candidate)
Return Markdown only."""


def _run_inventory(did, r, settings):
    _, texts = _sources()
    if not texts:
        raise RuntimeError("Upload at least one resume first.")
    user = "\n\n".join(f"# RESUME: {n}\n{t}" for n, t in texts) + "\n\nWrite the career inventory now."
    raw, provider, model = llm.complete(settings, INVENTORY_SYSTEM, user, provider=r["provider"], json_mode=False)
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    _update(did, status="done", finished=_now(), provider=provider, model=model, text=text)


def _worker():
    while True:
        did = _q.get()
        r = _row(did)
        if not r or r["status"] != "queued":
            continue
        _current["id"] = did
        _update(did, status="running")
        try:
            settings = ps.get_settings(include_secret=True)
            if r["kind"] == "resume":
                _run_resume(did, r, settings)
            elif r["kind"] == "inventory":
                _run_inventory(did, r, settings)
            elif r["kind"] == "application":
                _run_application(did, r, settings)
        except llm.LLMError as e:
            applog.error("draft", f"{r['kind']} draft #{did} for {r['job_key']}: {e}")
            _update(did, status="error", error=str(e)[:600], finished=_now())
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            applog.error("draft", f"{r['kind']} draft #{did} for {r['job_key']}: {e.__class__.__name__}: {e}")
            _update(did, status="error", error=f"{e.__class__.__name__}: {e}"[:600], finished=_now())
        finally:
            _current["id"] = None
