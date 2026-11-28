"""Profile (candidate data) and settings storage — plain JSON/files under DATA_DIR.

Everything personal lives here, not in code, so the app works for any user.
"""
import base64
import json
import os
import re
import threading
from datetime import datetime, timezone

DATA_DIR = os.path.dirname(os.environ.get("DB_PATH", "/data/jobs.db")) or "."
PROFILE_DIR = os.path.join(DATA_DIR, "profile")
RESUME_DIR = os.path.join(PROFILE_DIR, "resumes")
PROFILE_JSON = os.path.join(PROFILE_DIR, "profile.json")
INVENTORY_MD = os.path.join(PROFILE_DIR, "inventory.md")
SETTINGS_JSON = os.path.join(DATA_DIR, "settings.json")
MAX_UPLOAD = 5 * 1024 * 1024

_lock = threading.Lock()

PROFILE_DEFAULTS = {
    "full_name": "",
    "city_state": "",
    "phone": "",
    "email": "",
    "linkedin": "",
    "clearance": "",
    "template_resume": "",
    # search preferences (used for drafting context today; for multi-user search later)
    "home_location": "",
    "radius_miles": 30,
    "search_areas": "",     # other towns/regions to search besides home, one per line (each runs its own near-home search)
    "work_modes": "On-site, Hybrid, Remote",
    "employment_types": "W2, 1099",
    "levels": "",
    "lanes": "",
    "target_employers": "",
    "notes": "",
    # military background (used to translate into civilian titles, searches and fit)
    "mil_branch": "",       # e.g. U.S. Army
    "mil_rank": "",         # highest rank / pay grade, e.g. Colonel (O-6), Master Sergeant (E-8)
    "mil_codes": "",        # MOS / AFSC / NEC / rating / designator codes, one per line, e.g. 25A Signal Officer
    "mil_skill_ids": "",    # ASI / SQI / SI / special qualifications, schools, badges
    # more military background (spec section 24)
    "mil_status": "",           # active | reserve | guard | retired | separated | transitioning
    "mil_entry_date": "",       # YYYY-MM
    "mil_separation_date": "",  # YYYY-MM, past or planned
    "mil_highest_position": "",  # e.g. Deputy Commanding Officer, Army NETCOM
    "mil_largest_org": "",      # e.g. 5,000 people, $1.2B budget, 40 sites
    "mil_education": "",        # professional military education, one per line
    "mil_joint": "",            # e.g. Joint Qualified Officer
    "mil_8140": "",             # DoD 8140/8570 cyber workforce roles, one per line
    "mil_certs": "",            # military-earned certifications, one per line
    "mil_languages": "",        # one per line, e.g. Arabic (DLPT 2/2)
    "clearance_level": "",      # e.g. TS/SCI; when set, `clearance` is written from these four fields
    "clearance_poly": "",       # "" | ci | full
    "clearance_investigation": "",  # YYYY-MM of the last investigation
    "clearance_status": "",     # "" | active | current | expired
    # federal hiring: never sent to the AI, only chooses USAJOBS hiring paths
    "vet_preference": "",       # "" | 5 | 10
    "disability_30": "",        # "true" when 30% or more
}


def military_text(p):
    """Plain-text military background block for AI prompts ('' if none given). See military.py."""
    import military
    return military.military_text(p)

SETTINGS_DEFAULTS = {
    "provider": "anthropic",  # anthropic | ollama
    "ollama_url": os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434"),
    "ollama_model": os.environ.get("OLLAMA_MODEL", ""),
    "ollama_num_ctx": 24576,
    "anthropic_model": "claude-sonnet-5-5",
    "anthropic_fit_model": "claude-haiku-4-5-20251001",
    "anthropic_api_key": "",
    "temperature": 0.3,
    # swap name, street address, phone, email and LinkedIn for placeholders before anything goes to the
    # model, and cut clearance details to level + status (privacy.py). On by default.
    "redact_pii": True,
    "update_check": True,   # once a day, ask GitHub for the latest release (updates.py); nothing about you is sent
}

# Settings (here and in search.DEFAULTS) that exist only because this app runs on the person's own server.
# The Apple and Windows apps must not copy them (spec section 8). Recorded only; nothing reads this at run time.
_EMAIL = "The morning email is sent by this server through the person's own mail (SMTP) account."
_PLAN = ("The person's own job-board (JSearch) API plan: key, provider, monthly budget and the rotation it drives; "
         "the Apple app's job-board searches go through the relay.")
_OLLAMA = "Local models (Ollama) run only beside the self-hosted server."
SELF_HOSTED_ONLY_SETTINGS = {
    "provider": "Choice between Anthropic and a local Ollama model; the Apple app always uses the relay (Messages API).",
    "ollama_url": _OLLAMA, "ollama_model": _OLLAMA, "ollama_num_ctx": _OLLAMA,
    "anthropic_api_key": "The person's own Anthropic key on their server; the Apple app holds no key and uses the relay.",
    "update_check": "Asks GitHub for the latest self-hosted release; the Apple app is updated through the App Store.",
    "email_enabled": _EMAIL, "email_to": _EMAIL, "email_from_name": _EMAIL, "email_manual_runs": _EMAIL,
    "smtp_host": _EMAIL, "smtp_port": _EMAIL, "smtp_user": _EMAIL, "smtp_password": _EMAIL,
    "dashboard_url": "Address of the self-hosted page, linked from the morning email.",
    "ntfy_url": "Push through the person's own ntfy server.", "ntfy_topic": "Push through the person's own ntfy server.",
    "ntfy_token": "Push through the person's own ntfy server.",
    "jsearch_api_key": _PLAN, "jsearch_provider": _PLAN, "jsearch_budget": _PLAN, "jsearch_pages": _PLAN,
    "max_age_days": "Server copy of MAX_AGE_DAYS; other clients use keep_days (spec section 25).",
}
SELF_HOSTED_ONLY_ENV = {
    "PORT": "Port the self-hosted server listens on (8093).",
    "BIND_ADDRESS": "Docker: the host address the port is published on (127.0.0.1).",
    "HOST_PORT": "Docker: the host port mapped to the server.",
    "APP_PASSWORD": "Optional password (HTTP Basic login) for the self-hosted page.",
    "DB_PATH": "Where the server keeps its database and settings (the Docker /data volume).",
    "MAX_AGE_DAYS": "Overrides keep_days (30) on the server only.",
    "OLLAMA_URL": _OLLAMA, "OLLAMA_MODEL": _OLLAMA,
    "TZ": "Docker: the server's time zone, the default for the search schedule's time zone.",
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path, defaults):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    out = dict(defaults)
    out.update({k: v for k, v in data.items() if k in defaults})
    return out


def _write_json(path, data, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------- profile ----------------
def get_profile():
    with _lock:
        return _read_json(PROFILE_JSON, PROFILE_DEFAULTS)


def save_profile(data):
    with _lock:
        cur = _read_json(PROFILE_JSON, PROFILE_DEFAULTS)
        for k, v in (data or {}).items():
            if k in PROFILE_DEFAULTS:
                if k == "radius_miles":
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        v = PROFILE_DEFAULTS[k]
                else:
                    v = str(v)[:5000]
                cur[k] = v
        if str(cur.get("clearance_level") or "").strip():   # structured clearance wins (spec section 24)
            import military
            cur["clearance"] = military.clearance_text(cur)
        _write_json(PROFILE_JSON, cur)
        return cur


def get_inventory():
    try:
        with open(INVENTORY_MD, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def save_inventory(text):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with _lock, open(INVENTORY_MD, "w", encoding="utf-8") as f:
        f.write(str(text)[:200000])


def safe_name(name):
    base = os.path.basename(str(name or "")).strip()
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:120]
    if not base.lower().endswith(".docx"):
        raise ValueError("Only .docx files are supported")
    return base


def list_resumes():
    os.makedirs(RESUME_DIR, exist_ok=True)
    tpl = get_profile().get("template_resume")
    out = []
    for n in sorted(os.listdir(RESUME_DIR)):
        if n.lower().endswith(".docx"):
            p = os.path.join(RESUME_DIR, n)
            st = os.stat(p)
            out.append({
                "name": n,
                "size": st.st_size,
                "uploaded": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                "is_template": n == tpl,
            })
    return out


def resume_path(name):
    """Full path of a resume inside RESUME_DIR; refuses anything that would land outside it."""
    root = os.path.realpath(RESUME_DIR)
    p = os.path.realpath(os.path.join(root, safe_name(name)))
    if not p.startswith(root + os.sep):
        raise ValueError("Invalid file name")
    return p


def save_resume_upload(filename, data_b64):
    name = safe_name(filename)
    raw = base64.b64decode(data_b64 or "", validate=False)
    if not raw or len(raw) > MAX_UPLOAD:
        raise ValueError("File empty or larger than 5 MB")
    if raw[:2] != b"PK":
        raise ValueError("Not a valid .docx file")
    os.makedirs(RESUME_DIR, exist_ok=True)
    with open(resume_path(name), "wb") as f:
        f.write(raw)
    prof = get_profile()
    if not prof.get("template_resume"):
        save_profile({"template_resume": name})
    return name


def delete_resume(name):
    p = resume_path(name)
    if os.path.exists(p):
        os.remove(p)
    if get_profile().get("template_resume") == os.path.basename(p):
        remaining = [r["name"] for r in list_resumes()]
        save_profile({"template_resume": remaining[0] if remaining else ""})


def set_template(name):
    p = resume_path(name)
    if not os.path.exists(p):
        raise ValueError("No such resume")
    save_profile({"template_resume": os.path.basename(p)})


def resume_names():
    return [r["name"] for r in list_resumes()]


def template_path(name=None):
    """Path of the layout template: `name` if given and uploaded, else the resume tagged in the Profile."""
    if name and name not in resume_names():
        name = None
    name = name or get_profile().get("template_resume")
    if not name:
        return None
    try:
        p = resume_path(name)
    except ValueError:
        return None
    return p if os.path.exists(p) else None


# ---------------- settings ----------------
def get_settings(include_secret=False):
    with _lock:
        s = _read_json(SETTINGS_JSON, SETTINGS_DEFAULTS)
    if not include_secret:
        key = s.get("anthropic_api_key") or ""
        s["anthropic_api_key"] = ("••••" + key[-4:]) if key else ""
        s["anthropic_key_set"] = bool(key)
    return s


def save_settings(data):
    with _lock:
        cur = _read_json(SETTINGS_JSON, SETTINGS_DEFAULTS)
        for k, v in (data or {}).items():
            if k not in SETTINGS_DEFAULTS:
                continue
            if k == "anthropic_api_key":
                v = str(v or "").strip()
                if not v or v.startswith("••••"):
                    continue  # unchanged
                if v == "CLEAR":
                    v = ""
            elif k == "provider":
                v = v if v in ("ollama", "anthropic") else "anthropic"
            elif k == "ollama_num_ctx":
                try:
                    v = max(4096, min(131072, int(v)))
                except (TypeError, ValueError):
                    v = SETTINGS_DEFAULTS[k]
            elif k in ("redact_pii", "update_check"):
                v = v if isinstance(v, bool) else str(v).strip().lower() not in ("false", "0", "off", "no", "")
            elif k == "temperature":
                try:
                    v = max(0.0, min(1.0, float(v)))
                except (TypeError, ValueError):
                    v = SETTINGS_DEFAULTS[k]
            else:
                v = str(v).strip()[:500]
            cur[k] = v
        _write_json(SETTINGS_JSON, cur, private=True)
    return get_settings()
