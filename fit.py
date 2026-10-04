"""Job-fit rating: High / Med / Low, the role lane, and a plain reason that names the gaps.

Runs on the model chosen in Settings (Claude API by default, using a small, cheap
model; or a local Ollama model). The candidate's profile + career inventory go in the
system prompt, which stays identical from job to job so it can be cached (cheaper, faster).
Only unrated jobs are rated automatically; "Re-rate" on a card forces one.
"""
import threading
import time
from datetime import datetime, timezone

import drafts
import llm
import profile_store as ps

LANES_DEFAULT = ["Tech/Cyber", "Operations", "Defense prime", "Space", "Other"]
_state = {"running": False, "done": 0, "total": 0, "current": "", "errors": 0, "last_error": "", "provider": ""}
_run_lock = threading.Lock()
_db = None

FIT_SYSTEM = """You are a senior executive recruiter who specializes in placing retired military officers and senior government leaders into civilian executive roles. You judge job fit honestly and translate military experience into civilian terms (e.g. Deputy Commanding Officer = COO; Chief of Staff = chief of staff/COO; Signal/Cyber command = enterprise IT, network and cyber operations).

Rate how well THIS candidate fits the job below. Use only facts in the candidate material — never invent experience.

Rating scale:
- High: meets the posted basic qualifications, and the core of the role maps directly to work the candidate has done at comparable or larger scope. Worth applying now.
- Med: strong leadership/scope match, but one notable gap (a domain, a hands-on technical requirement, a certification, or the level is a step down/up).
- Low: the posting is open only to internal candidates or current employees; or a key requirement is missing (e.g. years of hands-on software development, a specific engineering discipline, a license or cert he lacks, a domain he has never worked), or the level/pay is clearly wrong.

Return ONLY a JSON object:
{"fit": "High" | "Med" | "Low",
 "lane": one of LANES,
 "reason": "Max 45 words, plain language, three parts in this order. (1) What the job actually is, in a few words (e.g. 'Runs program-health reviews for an aircraft sector'). (2) Why THIS candidate fits THIS job: name the one or two parts of their background that match this posting's main duties, in civilian terms. Do not open with years of experience or repeat the same headline numbers for every job; use a number only when it matches the scale this posting asks for. (3) 'Gap:' and the main gap or risk. Refer to the candidate as 'you'. A higher clearance than required is not a gap; mention clearance only if the posting requires one.",
 "meets_basic_quals": "yes" | "no" | "unclear"}
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(db_fn, db_lock):
    global _db
    _db = (db_fn, db_lock)
    with db_lock, db_fn() as conn:
        have = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        for col in ("fit_by", "fit_at"):
            if col not in have:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT DEFAULT ''")


def status():
    return dict(_state)


def system_prompt(profile, inventory, resume_texts):
    lanes = [x.strip() for x in str(profile.get("lanes") or "").replace("\n", ",").split(",") if x.strip()]
    lane_list = LANES_DEFAULT if not lanes else lanes + ["Other"]
    parts = [FIT_SYSTEM.replace("one of LANES", "one of " + ", ".join(f'"{l}"' for l in lane_list)), "",
             "# CANDIDATE", f"Clearance: {profile.get('clearance') or 'not specified'}"]
    for k, lbl in (("levels", "Target levels"), ("lanes", "Target role lanes"), ("home_location", "Home"),
                   ("work_modes", "Work modes"), ("employment_types", "Employment types")):
        if str(profile.get(k) or "").strip():
            parts.append(f"{lbl}: {str(profile[k]).strip()}")
    mil = ps.military_text(profile)
    if mil:
        parts += ["", "# MILITARY BACKGROUND (translate codes and rank into civilian scope and level)", mil]
    if str(profile.get("notes") or "").strip():
        parts += ["", "# CANDIDATE'S OWN NOTES", profile["notes"].strip()]
    if inventory.strip():
        parts += ["", "# CAREER INVENTORY", inventory.strip()[:40000]]
    else:
        for name, text in resume_texts[:2]:
            parts += ["", f"# RESUME: {name}", text.strip()[:15000]]
    return "\n".join(parts)


def job_prompt(job):
    desc = (job.get("description") or "").strip()
    return "\n".join([
        "# JOB",
        f"Title: {job.get('title')}",
        f"Company: {job.get('company')}",
        f"Location: {job.get('location')} ({job.get('work_mode') or 'n/a'})",
        f"Pay: {job.get('salary') or 'not listed'}",
        f"Clearance: {job.get('clearance') or 'not listed'}",
        "", "# JOB DESCRIPTION",
        desc[:9000] if desc else "(No description available. Rate from the title and company only, and say so in the reason.)",
        "", "Return the JSON now."])


def rate_job(settings, provider, system, job):
    last = None
    for _ in range(2):
        raw, prov, model = llm.complete(settings, system, job_prompt(job), provider=provider, json_mode=True, max_tokens=400,
                                       model=settings.get("anthropic_fit_model") or None)
        try:
            d = llm.parse_json(raw)
        except llm.LLMError as e:
            last = e
            continue
        fit = str(d.get("fit") or "").strip().title()
        fit = {"Medium": "Med", "Moderate": "Med", "Strong": "High", "Weak": "Low"}.get(fit, fit)
        if fit not in ("High", "Med", "Low"):
            last = llm.LLMError(f"Model returned fit={d.get('fit')!r}")
            continue
        reason = " ".join(str(d.get("reason") or "").split())
        if str(d.get("meets_basic_quals") or "").lower() == "no" and "basic qual" not in reason.lower():
            reason += " (Does not meet a posted basic qualification.)"
        if not (job.get("description") or "").strip() and "title" not in reason.lower():
            reason += " (Rated from title only.)"
        label = "Claude" if prov == "anthropic" else "Local"
        return {"fit": fit, "lane": str(d.get("lane") or "").strip()[:40], "fit_reason": reason[:600],
                "fit_by": f"{label} · {model}"}
    raise last or llm.LLMError("No rating")


def pending_keys():
    db_fn, lock = _db
    with lock, db_fn() as conn:
        rows = conn.execute("""SELECT job_key FROM jobs WHERE COALESCE(fit,'')='' AND COALESCE(status,'')!='dismissed'
                               ORDER BY COALESCE(NULLIF(posted_date,''), last_seen) DESC""").fetchall()
    return [r[0] for r in rows]


def run(keys=None, provider=None, force=False):
    """Rate jobs (default: every unrated, non-dismissed job). Blocking; one run at a time."""
    if not _run_lock.acquire(blocking=False):
        raise RuntimeError("Fit rating is already running")
    db_fn, lock = _db
    try:
        import search  # local import: search imports fit
        provider = provider or search.get_config().get("fit_provider") or "anthropic"
        if provider == "off":
            return
        keys = keys or pending_keys()
        settings = ps.get_settings(include_secret=True)
        inv, texts = drafts._sources()
        system = system_prompt(ps.get_profile(), inv, texts)
        _state.update(running=True, done=0, total=len(keys), current="", errors=0, last_error="", provider=provider,
                      started=time.time())
        for key in keys:
            with lock, db_fn() as conn:
                r = conn.execute("SELECT * FROM jobs WHERE job_key=?", (key,)).fetchone()
            if not r:
                continue
            job = dict(r)
            if job.get("fit") and not force:
                _state["done"] += 1
                continue
            _state["current"] = f"{job['company']}: {job['title']}"[:120]
            try:
                res = rate_job(settings, provider, system, job)
            except Exception as e:  # noqa: BLE001 — keep going; one bad job must not stop the run
                _state["errors"] += 1
                _state["last_error"] = str(e)[:300]
                if isinstance(e, llm.LLMError) and ("Cannot reach" in str(e) or "No Claude API key" in str(e)):
                    break  # the model is down; no point trying the rest
                continue
            with lock, db_fn() as conn:
                conn.execute("""UPDATE jobs SET fit=?, fit_reason=?, fit_by=?, fit_at=?,
                                lane=CASE WHEN COALESCE(lane,'')='' OR ? THEN ? ELSE lane END WHERE job_key=?""",
                             (res["fit"], res["fit_reason"], res["fit_by"], _now(), 1 if force else 0, res["lane"], key))
            _state["done"] += 1
    finally:
        _state.update(running=False, current="")
        _run_lock.release()


def run_async(keys=None, provider=None, force=False):
    if _state["running"]:
        raise RuntimeError("Fit rating is already running")

    def go():
        try:
            run(keys, provider, force)
        except Exception as e:  # noqa: BLE001
            _state["last_error"] = str(e)[:300]
            print(f"fit run failed: {e}", flush=True)
    threading.Thread(target=go, daemon=True).start()
    time.sleep(0.05)
