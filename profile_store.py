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
}


def military_text(p):
    """Plain-text military background block for AI prompts ('' if none given)."""
    lines = [f"{lbl}: {str(p.get(k) or '').strip()}" for k, lbl in (
        ("mil_branch", "Branch"), ("mil_rank", "Highest rank / pay grade"),
        ("mil_codes", "Occupation codes (MOS/AFSC/NEC/designator)"),
        ("mil_skill_ids", "Skill identifiers, qualifications, schools")) if str(p.get(k) or "").strip()]
    return "\n".join(lines)

SETTINGS_DEFAULTS = {
    "provider": "anthropic",  # anthropic | ollama
    "ollama_url": os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434"),
    "ollama_model": os.environ.get("OLLAMA_MODEL", ""),
    "ollama_num_ctx": 24576,
    "anthropic_model": "claude-sonnet-5-5",
    "anthropic_fit_model": "claude-haiku-4-5-20251001",
    "anthropic_api_key": "",
    "temperature": 0.3,
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
    return os.path.join(RESUME_DIR, safe_name(name))


def save_resume_upload(filename, data_b64):
    name = safe_name(filename)
    raw = base64.b64decode(data_b64 or "", validate=False)
    if not raw or len(raw) > MAX_UPLOAD:
        raise ValueError("File empty or larger than 5 MB")
    if raw[:2] != b"PK":
        raise ValueError("Not a valid .docx file")
    os.makedirs(RESUME_DIR, exist_ok=True)
    with open(os.path.join(RESUME_DIR, name), "wb") as f:
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


def template_path():
    name = get_profile().get("template_resume")
    if not name:
        return None
    p = os.path.join(RESUME_DIR, name)
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
